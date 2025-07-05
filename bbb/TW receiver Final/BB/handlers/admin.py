# START OF FILE handlers/admin.py

import logging
import asyncio
import os
import zipfile
import json
import sqlite3
from enum import Enum, auto
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BotCommandScopeChat
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, filters, CallbackQueryHandler,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError, BadRequest
from datetime import datetime, timedelta

import database
from handlers import login
from config import BOT_TOKEN

logger = logging.getLogger(__name__)

# --- States for Conversations ---
class AdminState(Enum):
    GET_USER_INFO_ID = auto()
    BLOCK_USER_ID = auto()
    UNBLOCK_USER_ID = auto()
    ADJ_BALANCE_ID = auto()
    ADJ_BALANCE_AMOUNT = auto()
    ADD_ADMIN_ID = auto()
    REMOVE_ADMIN_ID = auto()
    BROADCAST_MSG = auto()
    BROADCAST_CONFIRM = auto()
    MSG_USER_ID = auto()
    MSG_USER_CONTENT = auto()
    ADD_PROXY = auto()
    EDIT_SETTING_VALUE = auto()
    ADD_COUNTRY_CODE = auto()
    ADD_COUNTRY_NAME = auto()
    ADD_COUNTRY_FLAG = auto()
    ADD_COUNTRY_PRICE = auto()
    ADD_COUNTRY_TIME = auto()
    ADD_COUNTRY_CAPACITY = auto()
    DELETE_COUNTRY_CODE = auto()
    DELETE_COUNTRY_CONFIRM = auto()
    DELETE_USER_DATA_ID = auto()
    DELETE_USER_DATA_CONFIRM = auto()
    RECHECK_BY_USER_ID = auto()


def _get_auth_key_from_session(session_path: str) -> str | None:
    if not os.path.exists(session_path):
        return None
    try:
        conn = sqlite3.connect(session_path)
        cursor = conn.cursor()
        cursor.execute("SELECT auth_key FROM sessions LIMIT 1")
        result = cursor.fetchone()
        conn.close()
        if result and result[0]:
            return result[0].hex()
    except Exception as e:
        logger.error(f"Could not read auth_key from session {session_path}: {e}")
    return None

async def try_edit_message(query: Update.callback_query, text: str, reply_markup: InlineKeyboardMarkup | None):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
    except BadRequest as e:
        if "Message is not modified" not in str(e).lower():
            logger.error(f"Error editing message for callback {query.data}: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred in try_edit_message: {e}")

def admin_required(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if not database.is_admin(user_id):
            if update.callback_query:
                await update.callback_query.answer("🚫 Access Denied", show_alert=True)
            elif update.message:
                await update.message.reply_text("🚫 Access Denied. This command is for admins only.")
            if context.user_data.get('in_conversation'):
                 return ConversationHandler.END
            return
        
        if update.callback_query:
            await update.callback_query.answer()
            
        return await func(update, context, *args, **kwargs)
    return wrapped

def create_pagination_keyboard(prefix: str, current_page: int, total_items: int, item_per_page: int = 5):
    total_pages = (total_items + item_per_page - 1) // item_per_page if total_items > 0 else 1
    if total_pages <= 1: return []
    nav_buttons = []
    if current_page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_page_{current_page - 1}"))
    if current_page < total_pages: nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_page_{current_page + 1}"))
    return nav_buttons

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    message_text = "✅ Operation cancelled."
    if update.callback_query:
        await update.callback_query.answer()
        await try_edit_message(update.callback_query, message_text, None)
    else:
        await update.message.reply_text(message_text)
    return ConversationHandler.END

@admin_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *Super Admin Panel*\n\nSelect a category to manage the bot."
    keyboard = [
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats"), InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_settings_main")],
        [InlineKeyboardButton("👤 User Management", callback_data="admin_users_main"), InlineKeyboardButton("🎛️ Country Management", callback_data="admin_countries_main")],
        [InlineKeyboardButton("📦 Account Management", callback_data="admin_accounts_main"), InlineKeyboardButton("📢 Messaging", callback_data="admin_messaging_main")],
        [InlineKeyboardButton("🔧 System & Data", callback_data="admin_system_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await try_edit_message(update.callback_query, text, reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

@admin_required
async def stats_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, stats = update.callback_query, database.get_bot_stats()
    status_text = "\n".join([f"  - `{s}`: {c}" for s, c in stats.get('accounts_by_status', {}).items()]) or "  - No accounts."
    text = (f"📊 *Bot Statistics*\n\n"
            f"👥 *Users:*\n  - Total: `{stats['total_users']}`\n  - Blocked: `{stats['blocked_users']}`\n\n"
            f"📦 *Accounts:*\n  - Total: `{stats['total_accounts']}`\n{status_text}\n\n"
            f"💸 *Withdrawals:*\n  - Total Value: `${stats['total_withdrawals_amount']:.2f}`\n  - Total Count: `{stats['total_withdrawals_count']}`\n\n"
            f"🌐 *Proxies:*\n  - Count: `{stats['total_proxies']}`")
    keyboard = [[InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def settings_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query, s = update.callback_query, context.bot_data
    def get_status(key, on_val='True'): return "✅ ON" if s.get(key) == on_val else "❌ OFF"
    def get_lock(key): return "🔓 UNLOCKED" if s.get(key) == 'UNLOCKED' else "🔒 LOCKED"
    text = "*⚙️ Bot Settings*\n\nToggle features or edit values."
    keyboard = [
        [InlineKeyboardButton(f"Bot Status: {get_status('bot_status', 'ON')}", callback_data="admin_toggle:bot_status:ON:OFF")],
        [InlineKeyboardButton(f"Add Accounts: {get_lock('add_account_status')}", callback_data="admin_toggle:add_account_status:UNLOCKED:LOCKED")],
        [InlineKeyboardButton(f"Spam Check: {get_status('enable_spam_check')}", callback_data="admin_toggle:enable_spam_check:True:False")],
        [InlineKeyboardButton(f"Device Check: {get_status('enable_device_check')}", callback_data="admin_toggle:enable_device_check:True:False")],
        [InlineKeyboardButton("✍️ Edit All Text & Values", callback_data="admin_edit_values_list")],
        [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]
    ]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))
    
@admin_required
async def edit_values_list_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "✍️ *Edit Text & Values*\n\nSelect a setting to modify its value."
    settings_to_edit = {
        'Messages': ['welcome_message', 'help_message', 'rules_message'],
        'Channels & IDs': ['channel_username', 'admin_channel', 'support_id'],
        'Functionality': ['min_withdraw', 'max_withdraw', 'two_step_password', 'spambot_username'],
        'API': ['api_id', 'api_hash']
    }
    keyboard = []
    for category, keys in settings_to_edit.items():
        keyboard.append([InlineKeyboardButton(f"--- {category} ---", callback_data="admin_noop")])
        for key in keys:
            keyboard.append([InlineKeyboardButton(key.replace('_', ' ').title(), callback_data=f"admin_edit_setting:{key}")])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data="admin_settings_main")])
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def users_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👤 *User Management*\n\nSelect an action."
    keyboard = [
        [InlineKeyboardButton("📋 View All Users", callback_data="admin_view_users_page_1")],
        [InlineKeyboardButton("ℹ️ Get User Info", callback_data="admin_conv_start:GET_USER_INFO_ID")],
        [InlineKeyboardButton("💰 Adjust Balance", callback_data="admin_conv_start:ADJ_BALANCE_ID")],
        [InlineKeyboardButton("🚫 Block User", callback_data="admin_conv_start:BLOCK_USER_ID"), InlineKeyboardButton("✅ Unblock User", callback_data="admin_conv_start:UNBLOCK_USER_ID")],
        [InlineKeyboardButton("🔥 Purge User Data", callback_data="admin_conv_start:DELETE_USER_DATA_ID")],
        [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]
    ]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def accounts_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the main account management panel with new recheck buttons."""
    text = "📦 *Account Management*\n\nView accounts or use the tools below to re-check problematic ones."
    keyboard = [
        [InlineKeyboardButton("📋 View All Accounts", callback_data="admin_view_accounts_page_1")],
        [InlineKeyboardButton("👤 Recheck by User ID", callback_data="admin_conv_start:RECHECK_BY_USER_ID")],
        [InlineKeyboardButton("♻️ Recheck All Problematic", callback_data="admin_recheck_all")],
        [InlineKeyboardButton("🗂️ Export Sessions (.zip)", callback_data="admin_export:sessions")],
        [InlineKeyboardButton("📄 Export Sessions (.json)", callback_data="admin_export:json")],
        [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]
    ]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def countries_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🎛️ *Country Management*\n\nAdd, remove, or view countries supported by the bot."
    keyboard = [[InlineKeyboardButton("📋 View Countries", callback_data="admin_view_countries")], [InlineKeyboardButton("➕ Add Country", callback_data="admin_conv_start:ADD_COUNTRY_CODE")], [InlineKeyboardButton("➖ Delete Country", callback_data="admin_conv_start:DELETE_COUNTRY_CODE")], [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def messaging_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📢 *Messaging Panel*\n\nBroadcast messages to all users or send a direct message."
    keyboard = [[InlineKeyboardButton("📣 Broadcast to All Users", callback_data="admin_conv_start:BROADCAST_MSG")], [InlineKeyboardButton("✉️ Send Message to User", callback_data="admin_conv_start:MSG_USER_ID")], [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def system_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🔧 *System & Data Management*\n\nManage admins, proxies, and view withdrawal history."
    keyboard = [[InlineKeyboardButton("👑 Manage Admins", callback_data="admin_admins_main")], [InlineKeyboardButton("🌐 Manage Proxies", callback_data="admin_proxies_main")], [InlineKeyboardButton("💸 View Withdrawals", callback_data="admin_view_withdrawals_page_1")], [InlineKeyboardButton("⬅️ Back to Main Panel", callback_data="admin_panel")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def admins_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *Admin Management*\n\nAdd or remove bot administrators by their Telegram ID."
    keyboard = [[InlineKeyboardButton("📋 View Admins", callback_data="admin_view_admins")], [InlineKeyboardButton("➕ Add Admin", callback_data="admin_conv_start:ADD_ADMIN_ID")], [InlineKeyboardButton("➖ Remove Admin", callback_data="admin_conv_start:REMOVE_ADMIN_ID")], [InlineKeyboardButton("⬅️ Back to System Menu", callback_data="admin_system_main")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def proxies_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🌐 *Proxy Management*\n\nAdd or remove SOCKS5 proxies for account login."
    keyboard = [[InlineKeyboardButton("📋 View Proxies", callback_data="admin_view_proxies_page_1")], [InlineKeyboardButton("➕ Add Proxy", callback_data="admin_conv_start:ADD_PROXY")], [InlineKeyboardButton("⬅️ Back to System Menu", callback_data="admin_system_main")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))
    
@admin_required
async def toggle_setting_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, key, on_val, off_val = update.callback_query.data.split(':')
    new_val = off_val if context.bot_data.get(key) == on_val else on_val
    database.set_setting(key, new_val)
    context.bot_data[key] = new_val
    await settings_main_panel(update, context)

@admin_required
async def view_paginated_list(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int, db_fetch_func, db_count_func, title: str, format_func, back_callback: str, prefix: str, limit: int = 5):
    query = update.callback_query
    items = db_fetch_func(page=page, limit=limit)
    total_items = db_count_func()
    
    if not items and page > 1:
        page = max(1, (total_items + limit - 1) // limit if total_items > 0 else 1)
        items = db_fetch_func(page=page, limit=limit)
    
    if not items:
        text = f"No {title.lower().strip('*')} found."
    else:
        total_pages = (total_items + limit - 1) // limit if total_items > 0 else 1
        text = f"{title} (Page {page}/{total_pages})\n\n" + "\n\n".join([format_func(item) for item in items])
        
    nav_buttons = create_pagination_keyboard(prefix, page, total_items, limit)
    keyboard = [nav_buttons, [InlineKeyboardButton(f"⬅️ Back", callback_data=back_callback)]]
    await try_edit_message(query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def view_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    def format_user(user):
        status = "🔴 BLOCKED" if user['is_blocked'] else "🟢 Active"
        return f"▪️ID: `{user['telegram_id']}` (@{user.get('username', 'N/A')})\n  - Accounts: `{user['account_count']}` | Status: {status}"
    await view_paginated_list(update, context, page, database.get_all_users, database.count_all_users, "📋 *All Users*", format_user, "admin_users_main", "admin_view_users", limit=10)

@admin_required
async def view_accounts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    def format_account(acc):
        return f"▪️Phone: `{acc['phone_number']}`\n  - Status: `{acc['status']}`\n  - Owner: `{acc['user_id']}` (@{acc.get('username', 'N/A')})"
    await view_paginated_list(update, context, page, database.get_all_accounts_paginated, database.count_all_accounts, "📦 *All Accounts*", format_account, "admin_accounts_main", "admin_view_accounts", limit=10)

@admin_required
async def view_countries_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    countries = list(database.get_countries_config().values())
    text = "🎛️ *Configured Countries*\n\n"
    if not countries: text += "No countries configured."
    for country in sorted(countries, key=lambda c: c['name']):
        capacity = country.get('capacity', -1)
        cap_text = f"/{capacity}" if capacity > -1 else "/∞"
        count = database.get_country_account_count(country['code'])
        text += f"{country['flag']} `{country['code']}` *{country['name']}* \n  - Price: ${country['price']:.2f} | Time: {country['time']}s\n  - Capacity: {count}{cap_text}\n"
    keyboard = [[InlineKeyboardButton("⬅️ Back to Country Menu", callback_data="admin_countries_main")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def view_withdrawals_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    def format_withdrawal(w):
        try:
            ts_str = w.get('timestamp')
            if isinstance(ts_str, str):
                ts = datetime.fromisoformat(ts_str.split('.')[0]).strftime('%Y-%m-%d %H:%M')
            else:
                ts = "N/A"
        except (ValueError, TypeError):
            ts = w.get('timestamp', "N/A")
        
        username = w.get('username')
        if username:
            user_info = f"@{username} (`{w['user_id']}`)"
        else:
            user_info = f"ID: `{w['user_id']}`"

        return (f"▪️ User: {user_info}\n"
                f"  - Amount: `${w.get('amount', 0.0):.2f}`\n"
                f"  - Address: `{w.get('address', 'N/A')}`\n"
                f"  - Date: {ts}")

    await view_paginated_list(
        update, context, page, 
        database.get_all_withdrawals, database.count_all_withdrawals, 
        "💸 *Withdrawal History*", format_withdrawal, 
        "admin_system_main", "admin_view_withdrawals"
    )

@admin_required
async def view_proxies_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int):
    limit = 10
    proxies = database.get_all_proxies(page=page, limit=limit)
    total_proxies = database.count_all_proxies()
    total_pages = (total_proxies + limit - 1) // limit if total_proxies > 0 else 1
    text = f"🌐 *Proxy List* (Page {page}/{total_pages})\nClick ❌ to delete a proxy."
    keyboard_rows = []
    if not proxies and page > 1:
        page = total_pages
        proxies = database.get_all_proxies(page=page, limit=limit)

    if not proxies:
        text += "\n\nNo proxies configured."
    else:
        for proxy in proxies:
            keyboard_rows.append([
                InlineKeyboardButton(f"`{proxy['proxy']}`", callback_data="admin_noop"),
                InlineKeyboardButton("❌", callback_data=f"admin_delete_proxy:{proxy['id']}")
            ])
    
    nav_buttons = create_pagination_keyboard("admin_view_proxies", page, total_proxies, limit)
    keyboard_rows.extend([nav_buttons, [InlineKeyboardButton("⬅️ Back to Proxy Menu", callback_data="admin_proxies_main")]])
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard_rows))

@admin_required
async def view_admins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins = database.get_all_admins()
    text = "👑 *Current Admins*\n\n"
    if not admins: text += "No admins found."
    else: text += "\n".join([f"- `{admin['telegram_id']}`" for admin in admins])
    keyboard = [[InlineKeyboardButton("⬅️ Back to Admin Management", callback_data="admin_admins_main")]]
    await try_edit_message(update.callback_query, text, InlineKeyboardMarkup(keyboard))

@admin_required
async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    export_type = query.data.split(':')[-1]
    
    await try_edit_message(query, "⏳ Preparing export... This may take a few moments.", None)

    accounts = database.get_accounts_with_sessions()
    if not accounts:
        await query.message.reply_text("No accounts with valid session files found to export.")
        await accounts_main_panel(update, context)
        return
        
    ts = int(datetime.now().timestamp())
    
    try:
        if export_type == "sessions":
            zip_filename = f"sessions_{ts}.zip"
            with zipfile.ZipFile(zip_filename, 'w') as zipf:
                for acc in accounts:
                    if acc.get('session_file') and os.path.exists(acc['session_file']):
                        zipf.write(acc['session_file'], os.path.basename(acc['session_file']))
            
            with open(zip_filename, 'rb') as zip_file:
                await query.message.reply_document(document=zip_file, caption=f"Telethon .session files for {len(accounts)} accounts.")
            os.remove(zip_filename)
            
        elif export_type == "json":
            json_filename = f"sessions_{ts}.json"
            export_data = []
            for acc in accounts:
                if not (acc.get('session_file') and os.path.exists(acc['session_file'])):
                    continue
                auth_key_hex = _get_auth_key_from_session(acc['session_file'])
                export_data.append({
                    "phone_number": acc['phone_number'], "user_id": acc['user_id'],
                    "status": acc['status'], "auth_key_hex": auth_key_hex
                })
            
            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2)
            with open(json_filename, 'rb') as json_file:
                await query.message.reply_document(document=json_file, caption=f"JSON data for {len(export_data)} accounts.")
            os.remove(json_filename)

    except Exception as e:
        logger.error(f"Failed to create or send export file: {e}", exc_info=True)
        await query.message.reply_text("❌ An error occurred while creating the export file.")

@admin_required
async def conv_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['in_conversation'] = True
    query = update.callback_query
    action = query.data.split(':')[-1]
    
    prompts = {
        'GET_USER_INFO_ID': ("Enter User ID:", AdminState.GET_USER_INFO_ID),
        'BLOCK_USER_ID': ("Enter User ID to **BLOCK**:", AdminState.BLOCK_USER_ID),
        'UNBLOCK_USER_ID': ("Enter User ID to **UNBLOCK**:", AdminState.UNBLOCK_USER_ID),
        'ADJ_BALANCE_ID': ("Enter User ID to adjust balance for:", AdminState.ADJ_BALANCE_ID),
        'ADD_ADMIN_ID': ("Enter the Telegram ID of the new admin:", AdminState.ADD_ADMIN_ID),
        'REMOVE_ADMIN_ID': ("Enter the Telegram ID of the admin to remove:", AdminState.REMOVE_ADMIN_ID),
        'BROADCAST_MSG': ("Send the message to broadcast (text, photo, etc.).\nThis message will be copied to all users.", AdminState.BROADCAST_MSG),
        'MSG_USER_ID': ("Enter the recipient's User ID:", AdminState.MSG_USER_ID),
        'ADD_PROXY': ("Enter proxy (`ip:port` or `ip:port:user:pass`):", AdminState.ADD_PROXY),
        'ADD_COUNTRY_CODE': ("Step 1/6: Enter country code (e.g., `+44`).", AdminState.ADD_COUNTRY_CODE),
        'DELETE_COUNTRY_CODE': ("Enter country code to delete (e.g., `+44`):", AdminState.DELETE_COUNTRY_CODE),
        'DELETE_USER_DATA_ID': ("🔥 Enter User ID to **PURGE ALL DATA** for. This is irreversible.", AdminState.DELETE_USER_DATA_ID),
        'RECHECK_BY_USER_ID': ("Enter the User's Telegram ID to re-check their accounts:", AdminState.RECHECK_BY_USER_ID),
    }
    try:
        prompt_text, next_state = prompts[action]
        await try_edit_message(query, f"{prompt_text}\n\nType /cancel to abort.", None)
        return next_state
    except KeyError:
        logger.warning(f"Unhandled conversation starter: {action}")
        context.user_data.pop('in_conversation', None)
        return ConversationHandler.END

@admin_required
async def edit_setting_starter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['in_conversation'] = True
    query = update.callback_query
    key_to_edit = query.data.split(':')[-1]
    
    context.user_data['setting_to_edit'] = key_to_edit
    current_val = context.bot_data.get(key_to_edit, 'Not set')
    await try_edit_message(query, f"Editing `{key_to_edit}`.\n*Current value:*\n`{current_val}`\n\nPlease send the new value.\n\nType /cancel to abort.", None)
    return AdminState.EDIT_SETTING_VALUE

async def edit_setting_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value, key = update.message.text, context.user_data.pop('setting_to_edit')
    database.set_setting(key, new_value)
    context.bot_data[key] = new_value
    kb = [[InlineKeyboardButton("⬅️ Back to Edit List", callback_data="admin_edit_values_list")]]
    await update.message.reply_text(f"✅ Setting `{key}` updated successfully!", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def simple_id_action(update: Update, context: ContextTypes.DEFAULT_TYPE, action_func, success_msg: str, back_callback: str, needs_user: bool = True):
    try:
        user_id = int(update.message.text)
        if needs_user and not database.get_user_by_id(user_id):
            await update.message.reply_text(f"User `{user_id}` not found.", parse_mode=ParseMode.MARKDOWN)
            context.user_data.pop('in_conversation', None)
            return ConversationHandler.END
        
        action_func(user_id)
        if action_func == database.add_admin:
            user_commands = [BotCommand("start", "🚀 Start"), BotCommand("balance", "💼 Balance"), BotCommand("cap", "📋 Rates"), BotCommand("help", "🆘 Help"), BotCommand("rules", "📜 Rules"), BotCommand("cancel", "❌ Cancel")]
            admin_commands = user_commands + [BotCommand("admin", "👑 Admin Panel")]
            await context.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=user_id))
            
        kb = [[InlineKeyboardButton("⬅️ Back", callback_data=back_callback)]]
        await update.message.reply_text(success_msg.format(id=user_id), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid ID. Please enter a numeric User ID.")
    except Exception as e:
        logger.error(f"Error in simple_id_action for {action_func.__name__}: {e}")
        await update.message.reply_text("An error occurred. Please check the logs.")
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def get_user_info_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        user = database.get_user_by_id(user_id)
        if not user:
            await update.message.reply_text(f"User `{user_id}` not found.", parse_mode=ParseMode.MARKDOWN)
            context.user_data.pop('in_conversation', None)
            return ConversationHandler.END
        
        summary, total, calc, manual, _ = database.get_user_balance_details(user_id)
        status_lines = "\n".join([f"  - `{s}`: {v}" for s, v in summary.items()]) or '  - No accounts.'
        text = (f"👤 *User Info: `{user_id}`*\n\n**Username:** @{user.get('username', 'N/A')}\n"
                f"**Joined:** {user['join_date'].split('.')[0]}\n"
                f"**Status:** {'BLOCKED' if user['is_blocked'] else 'Active'}\n\n"
                f"💰 *Balance: ${total:.2f}* (Calc: ${calc:.2f}, Manual: ${manual:.2f})\n\n"
                f"📦 *Accounts Summary*\n{status_lines}")
        kb = [[InlineKeyboardButton("⬅️ Back to User Menu", callback_data="admin_users_main")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    except (ValueError, TypeError): await update.message.reply_text("Invalid ID.")
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def adj_balance_get_id(u, c):
    try:
        uid = int(u.message.text)
        if not database.get_user_by_id(uid):
            await u.message.reply_text("User not found. Please enter a valid ID.")
            return AdminState.ADJ_BALANCE_ID
        c.user_data['target_user_id'] = uid
        await u.message.reply_text("Enter amount to add/subtract (e.g., `10.5` or `-5`).")
        return AdminState.ADJ_BALANCE_AMOUNT
    except ValueError:
        await u.message.reply_text("Invalid ID format. Please enter a numeric ID.")
        return AdminState.ADJ_BALANCE_ID

async def adj_balance_get_amount(u, c):
    try:
        uid, amount = c.user_data.pop('target_user_id'), float(u.message.text)
        database.adjust_user_balance(uid, amount)
        kb = [[InlineKeyboardButton("⬅️ Back to User Menu", callback_data="admin_users_main")]]
        await u.message.reply_text(f"✅ Balance for `{uid}` adjusted by `${amount:.2f}`.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    except (ValueError, KeyError): await u.message.reply_text("❌ Invalid amount. Please start over.")
    c.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def broadcast_get_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['broadcast_msg'] = update.message
    count = len(database.get_all_user_ids(only_non_blocked=True))
    keyboard = [[InlineKeyboardButton(f"✅ Yes, Send to {count} users", callback_data="admin_bcast_confirm_yes")], [InlineKeyboardButton("❌ No, Cancel", callback_data="admin_bcast_confirm_no")]]
    await update.message.reply_text("This message will be sent to all active users. Are you sure?", reply_markup=InlineKeyboardMarkup(keyboard))
    return AdminState.BROADCAST_CONFIRM

async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == 'admin_bcast_confirm_no':
        await try_edit_message(query, "Broadcast cancelled.", None)
        context.user_data.clear()
        return ConversationHandler.END
    
    msg = context.user_data.pop('broadcast_msg')
    await try_edit_message(query, "🚀 Starting broadcast... This may take a while.", None)
    user_ids = database.get_all_user_ids(only_non_blocked=True)
    sent, failed = 0, 0
    for uid in user_ids:
        try:
            await context.bot.copy_message(uid, msg.chat_id, msg.message_id)
            sent += 1
        except TelegramError as e:
            logger.warning(f"Broadcast failed for {uid}: {e}")
            failed += 1
        await asyncio.sleep(0.05)
    await query.message.reply_text(f"📢 Broadcast finished!\n\n✅ Sent: {sent}\n❌ Failed: {failed}")
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def msg_user_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text)
        if not database.get_user_by_id(uid):
            await update.message.reply_text("User not found. Please enter a valid ID.")
            return AdminState.MSG_USER_ID
        context.user_data['recipient_id'] = uid
        await update.message.reply_text("Now, send the message to deliver.")
        return AdminState.MSG_USER_CONTENT
    except ValueError:
        await update.message.reply_text("Invalid ID format.")
        return AdminState.MSG_USER_ID

async def msg_user_get_content(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rid = context.user_data.pop('recipient_id')
    try:
        await context.bot.copy_message(rid, update.message.chat_id, update.message.message_id)
        await update.message.reply_text(f"✅ Message sent to `{rid}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to send to `{rid}`: {e}", parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def add_proxy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    proxy_str = update.message.text.strip()
    if database.add_proxy(proxy_str):
        kb = [[InlineKeyboardButton("⬅️ Back to Proxy Menu", callback_data="admin_proxies_main")]]
        await update.message.reply_text(f"✅ Proxy `{proxy_str}` added.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"Proxy `{proxy_str}` might already exist or failed to add.", parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def add_country_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_country'] = {'code': update.message.text.strip()}
    await update.message.reply_text("Step 2/6: Enter the country name (e.g., `United Kingdom`).")
    return AdminState.ADD_COUNTRY_NAME
async def add_country_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_country']['name'] = update.message.text.strip()
    await update.message.reply_text("Step 3/6: Enter the country flag emoji (e.g., 🇬🇧).")
    return AdminState.ADD_COUNTRY_FLAG
async def add_country_get_flag(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_country']['flag'] = update.message.text.strip()
    await update.message.reply_text("Step 4/6: Enter the price per account (e.g., `0.62`).")
    return AdminState.ADD_COUNTRY_PRICE
async def add_country_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['price'] = float(update.message.text)
        await update.message.reply_text("Step 5/6: Enter the confirmation time in seconds (e.g., `600`).")
        return AdminState.ADD_COUNTRY_TIME
    except ValueError:
        await update.message.reply_text("Invalid price. Please enter a number (e.g., `0.62`).")
        return AdminState.ADD_COUNTRY_PRICE
async def add_country_get_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['new_country']['time'] = int(update.message.text)
        await update.message.reply_text("Step 6/6: Enter the capacity limit (`-1` for unlimited).")
        return AdminState.ADD_COUNTRY_CAPACITY
    except ValueError:
        await update.message.reply_text("Invalid time. Please enter a whole number.")
        return AdminState.ADD_COUNTRY_TIME
async def add_country_get_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        nc = context.user_data.pop('new_country')
        nc['capacity'] = int(update.message.text)
        database.add_country(nc['code'], nc['name'], nc['flag'], nc['price'], nc['time'], nc['capacity'])
        context.bot_data['countries_config'] = database.get_countries_config()
        await update.message.reply_text(f"✅ Country *{nc['name']}* added successfully!", parse_mode=ParseMode.MARKDOWN)
    except (ValueError, KeyError):
        await update.message.reply_text("❌ Invalid capacity or an error occurred. Please start over.")
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END


async def delete_country_get_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip()
    country = database.get_country_by_code(code)
    if not country:
        await update.message.reply_text("Country code not found. Please try again or /cancel.")
        return AdminState.DELETE_COUNTRY_CODE
    context.user_data['country_to_delete'] = code
    text = f"⚠️ Are you sure you want to delete this country?\n\n{country['flag']} *{country['name']}* (`{country['code']}`)"
    keyboard = [[InlineKeyboardButton("✅ Yes, Delete", callback_data="admin_del_country_confirm_yes")],
                 [InlineKeyboardButton("❌ No, Cancel", callback_data="admin_del_country_confirm_no")]]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    return AdminState.DELETE_COUNTRY_CONFIRM

async def delete_country_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    choice = query.data

    if choice == 'admin_del_country_confirm_no':
        await try_edit_message(query, "❌ Deletion cancelled.", None)
        context.user_data.clear()
        return ConversationHandler.END
    code = context.user_data.pop('country_to_delete')
    if database.delete_country(code):
        context.bot_data['countries_config'] = database.get_countries_config()
        await try_edit_message(query, f"✅ Country `{code}` deleted successfully.", None)
    else:
        await try_edit_message(query, f"❌ Failed to delete country `{code}`.", None)
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def delete_user_data_get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        user = database.get_user_by_id(user_id)
        if not user:
            await update.message.reply_text(f"User `{user_id}` not found.", parse_mode=ParseMode.MARKDOWN)
            context.user_data.pop('in_conversation', None)
            return ConversationHandler.END
        
        context.user_data['user_to_purge'] = user_id
        text = (f"⚠️ *CONFIRM DELETION*\n\n"
                f"You are about to permanently delete all data for user:\n"
                f"ID: `{user['telegram_id']}`\n"
                f"Username: @{user.get('username', 'N/A')}\n\n"
                f"This will remove their user record, all associated accounts, all withdrawal history, and all session files.\n\n"
                f"**THIS ACTION CANNOT BE UNDONE.**\n\nAre you absolutely sure?")
        keyboard = [
            [InlineKeyboardButton("❌ NO, CANCEL ❌", callback_data="admin_purge_confirm_no")],
            [InlineKeyboardButton("🔥 YES, PURGE DATA 🔥", callback_data="admin_purge_confirm_yes")]
        ]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
        return AdminState.DELETE_USER_DATA_CONFIRM
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid ID. Please enter a numeric User ID.")
        return ConversationHandler.END

async def delete_user_data_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    choice = query.data

    if choice == 'admin_purge_confirm_no':
        await try_edit_message(query, "❌ Data purge cancelled. No changes were made.", None)
        context.user_data.clear()
        return ConversationHandler.END

    user_id = context.user_data.pop('user_to_purge', None)
    if not user_id:
        await try_edit_message(query, "❌ Error: User ID to purge was not found in context. Please start over.", None)
        return ConversationHandler.END

    if database.delete_all_user_data(user_id):
        await try_edit_message(query, f"✅ All data for user `{user_id}` has been successfully purged.", None)
    else:
        await try_edit_message(query, f"❌ Failed to purge data for user `{user_id}`. They may have already been deleted.", None)
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

async def _schedule_rechecks_staggered(accounts_to_recheck: list, context: ContextTypes.DEFAULT_TYPE, prefix: str) -> int:
    scheduler = context.application.bot_data.get("scheduler")
    rechecked_count = 0
    stagger_delay_seconds = 2

    for i, acc in enumerate(accounts_to_recheck):
        job_id = acc.get('job_id')
        if not job_id: continue

        database.update_account_status(job_id, 'pending_confirmation')
        
        run_date = datetime.utcnow() + timedelta(seconds=5 + i * stagger_delay_seconds)
        
        scheduler.add_job(
            login.schedule_initial_check, 'date', run_date=run_date,
            args=[BOT_TOKEN, str(acc['user_id']), acc['user_id'], acc['phone_number'], job_id],
            id=f"{prefix}_{job_id}", replace_existing=True, misfire_grace_time=300
        )
        rechecked_count += 1
        await asyncio.sleep(0.02)
    
    return rechecked_count

async def recheck_by_user_id_receiver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Please enter a numeric User ID.")
        return AdminState.RECHECK_BY_USER_ID

    user = database.get_user_by_id(user_id)
    if not user:
        await update.message.reply_text(f"❌ User with ID `{user_id}` not found.", parse_mode=ParseMode.MARKDOWN)
        context.user_data.pop('in_conversation', None)
        return ConversationHandler.END

    accounts_to_recheck = database.get_problematic_accounts_by_user(user_id)

    if not accounts_to_recheck:
        await update.message.reply_text(f"✅ User `{user_id}` has no problematic accounts (pending or error) to re-check.", parse_mode=ParseMode.MARKDOWN)
        context.user_data.pop('in_conversation', None)
        return ConversationHandler.END
    
    await update.message.reply_text(f"⏳ Found {len(accounts_to_recheck)} accounts for user `{user_id}`. Scheduling re-checks with a delay...", parse_mode=ParseMode.MARKDOWN)

    rechecked_count = await _schedule_rechecks_staggered(accounts_to_recheck, context, "user_recheck")
    
    logger.info(f"Admin {update.effective_user.id} triggered a re-check for {rechecked_count} accounts belonging to user {user_id}.")
    kb = [[InlineKeyboardButton("⬅️ Back to Account Menu", callback_data="admin_accounts_main")]]
    await update.message.reply_text(f"✅ Successfully scheduled *{rechecked_count}* accounts for a new, staggered check.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    
    context.user_data.pop('in_conversation', None)
    return ConversationHandler.END

@admin_required
async def recheck_all_problematic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Searching for all problematic accounts...", show_alert=False)

    stuck_accounts = database.get_stuck_pending_accounts()
    error_accounts = database.get_error_accounts()

    all_problematic_dict = {acc['job_id']: acc for acc in stuck_accounts}
    all_problematic_dict.update({acc['job_id']: acc for acc in error_accounts})
    
    accounts_to_recheck = list(all_problematic_dict.values())

    if not accounts_to_recheck:
        await try_edit_message(query, "✅ No problematic accounts (pending > 30min or error status) found to re-check.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="admin_accounts_main")]]))
        return

    await try_edit_message(query, f"⏳ Found {len(accounts_to_recheck)} accounts. Scheduling re-checks with a delay...", None)
    
    rechecked_count = await _schedule_rechecks_staggered(accounts_to_recheck, context, "mass_recheck")

    logger.info(f"Admin {update.effective_user.id} triggered a mass re-check for {rechecked_count} accounts.")
    await query.message.reply_text(f"✅ Successfully scheduled *{rechecked_count}* accounts for a new, staggered check.", parse_mode=ParseMode.MARKDOWN)
    await accounts_main_panel(update, context)

@admin_required
async def main_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if not data: return

    if data == 'admin_recheck_all':
        await recheck_all_problematic_handler(update, context)
        return

    panel_map = {
        'admin_panel': admin_panel, 'admin_stats': stats_panel, 'admin_settings_main': settings_main_panel,
        'admin_users_main': users_main_panel, 'admin_countries_main': countries_main_panel,
        'admin_messaging_main': messaging_main_panel, 'admin_system_main': system_main_panel,
        'admin_admins_main': admins_main_panel, 'admin_proxies_main': proxies_main_panel,
        'admin_accounts_main': accounts_main_panel, 'admin_edit_values_list': edit_values_list_panel,
        'admin_view_countries': view_countries_handler, 'admin_view_admins': view_admins_handler
    }
    if data in panel_map:
        await panel_map[data](update, context)
        return

    page_map = {
        'admin_view_users': view_users_handler, 'admin_view_accounts': view_accounts_handler,
        'admin_view_withdrawals': view_withdrawals_handler, 'admin_view_proxies': view_proxies_handler
    }
    for prefix, handler in page_map.items():
        if data.startswith(f"{prefix}_page_"):
            try:
                page = int(data.split('_')[-1])
                await handler(update, context, page=page)
                return
            except (ValueError, IndexError): pass
    
    if data.startswith('admin_export:'): await export_handler(update, context); return
    if data.startswith('admin_delete_proxy:'):
        try:
            proxy_id = int(data.split(':')[-1])
            if database.remove_proxy_by_id(proxy_id):
                await query.answer("✅ Proxy deleted!", show_alert=False)
                await view_proxies_handler(update, context, page=1)
            else:
                await query.answer("❌ Failed to delete proxy.", show_alert=True)
            return
        except (ValueError, IndexError): pass
            
    logger.warning(f"Unhandled admin callback query: {data}")

def get_admin_handlers():
    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(conv_starter, pattern=r'^admin_conv_start:'),
            CallbackQueryHandler(edit_setting_starter, pattern=r'^admin_edit_setting:')
        ],
        states={
            AdminState.RECHECK_BY_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, recheck_by_user_id_receiver)],
            AdminState.EDIT_SETTING_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_setting_receiver)],
            AdminState.GET_USER_INFO_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_user_info_handler)],
            AdminState.BLOCK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: simple_id_action(u, c, database.block_user, "✅ User `{id}` has been **blocked**.", "admin_users_main"))],
            AdminState.UNBLOCK_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: simple_id_action(u, c, database.unblock_user, "✅ User `{id}` has been **unblocked**.", "admin_users_main"))],
            AdminState.ADJ_BALANCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adj_balance_get_id)],
            AdminState.ADJ_BALANCE_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, adj_balance_get_amount)],
            AdminState.ADD_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: simple_id_action(u, c, database.add_admin, "✅ `{id}` is now an admin.", "admin_admins_main", needs_user=False))],
            AdminState.REMOVE_ADMIN_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: simple_id_action(u, c, database.remove_admin, "✅ `{id}` is no longer an admin.", "admin_admins_main", needs_user=False))],
            AdminState.BROADCAST_MSG: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_get_msg)],
            AdminState.BROADCAST_CONFIRM: [CallbackQueryHandler(broadcast_confirm, pattern=r'^admin_bcast_confirm_')],
            AdminState.MSG_USER_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, msg_user_get_id)],
            AdminState.MSG_USER_CONTENT: [MessageHandler(filters.ALL & ~filters.COMMAND, msg_user_get_content)],
            AdminState.ADD_PROXY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_proxy_handler)],
            AdminState.ADD_COUNTRY_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_code)],
            AdminState.ADD_COUNTRY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_name)],
            AdminState.ADD_COUNTRY_FLAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_flag)],
            AdminState.ADD_COUNTRY_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_price)],
            AdminState.ADD_COUNTRY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_time)],
            AdminState.ADD_COUNTRY_CAPACITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_country_get_capacity)],
            AdminState.DELETE_COUNTRY_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_country_get_code)],
            AdminState.DELETE_COUNTRY_CONFIRM: [CallbackQueryHandler(delete_country_confirm, pattern=r'^admin_del_country_confirm_')],
            AdminState.DELETE_USER_DATA_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_user_data_get_id)],
            AdminState.DELETE_USER_DATA_CONFIRM: [CallbackQueryHandler(delete_user_data_confirm, pattern=r'^admin_purge_confirm_')],
        },
        fallbacks=[CommandHandler('cancel', cancel_conv)],
        conversation_timeout=600,
        per_user=True,
        per_chat=True
    )

    return [
        CommandHandler("admin", admin_panel),
        CallbackQueryHandler(toggle_setting_handler, pattern=r'^admin_toggle:'),
        CallbackQueryHandler(lambda u,c: u.callback_query.answer(), pattern='^admin_noop$'),
        conv_handler,
        CallbackQueryHandler(main_router, pattern=r'^admin_'),
    ]

# END OF FILE handlers/admin.py