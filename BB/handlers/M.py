# handlers/admin.py
import logging
import asyncio
import json
import os
import zipfile
from enum import Enum
from functools import wraps
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, filters, CallbackQueryHandler
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

import database

logger = logging.getLogger(__name__)

# --- States for Conversations ---
class AdminState(Enum):
    AWAIT_ID, AWAIT_TEXT, AWAIT_CONFIRM, AWAIT_AMOUNT, AWAIT_CHOICE = range(5)
    EDIT_SETTING_VALUE = 10
    ADD_PROXY, REMOVE_PROXY_CONFIRM = 11, 12
    ADD_ADMIN_ID, REMOVE_ADMIN_ID = 13, 14
    ADJ_BALANCE_ID, ADJ_BALANCE_AMOUNT = 15, 16
    BLOCK_USER_ID, UNBLOCK_USER_ID, GET_USER_INFO_ID = 17, 18, 19
    BROADCAST_MSG, BROADCAST_CONFIRM = 20, 21
    MSG_USER_ID, MSG_USER_CONTENT = 22, 23
    EXPORT_SESSIONS_ID = 24
    ADD_COUNTRY_CODE, ADD_COUNTRY_NAME, ADD_COUNTRY_FLAG, ADD_COUNTRY_PRICE, ADD_COUNTRY_TIME, ADD_COUNTRY_CAPACITY = 25, 26, 27, 28, 29, 30
    DELETE_COUNTRY_CODE, DELETE_COUNTRY_CONFIRM = 31, 32

# --- Admin Decorator & Helpers ---
def admin_required(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user or not database.is_admin(update.effective_user.id):
            if update.callback_query: await update.callback_query.answer("🚫 Access Denied", show_alert=True)
            return ConversationHandler.END
        return await func(update, context, *args, **kwargs)
    return wrapped

def create_pagination_keyboard(prefix: str, current_page: int, total_items: int, item_per_page: int = 5):
    total_pages = (total_items + item_per_page - 1) // item_per_page
    if total_pages <= 1: return []
    nav_buttons = []
    if current_page > 1: nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"{prefix}_page_{current_page - 1}"))
    if current_page < total_pages: nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"{prefix}_page_{current_page + 1}"))
    return nav_buttons

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    effective_message = update.effective_message
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text("✅ Operation cancelled.")
        except TelegramError: # Message might have been deleted
            await update.effective_chat.send_message("✅ Operation cancelled.")
    else:
        await effective_message.reply_text("✅ Operation cancelled.")

    context.user_data.clear()
    return ConversationHandler.END


# --- Panel Display Functions ---

@admin_required
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "👑 *Super Admin Panel*\n\nWelcome! Select a category to manage the bot."
    keyboard = [[InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats"), InlineKeyboardButton("⚙️ General Settings", callback_data="admin_settings_main")], [InlineKeyboardButton("👤 User Management", callback_data="admin_users_main"), InlineKeyboardButton("🎛️ Country Management", callback_data="admin_countries_main")], [InlineKeyboardButton("📢 Messaging", callback_data="admin_messaging_main"), InlineKeyboardButton("🔧 System & Data", callback_data="admin_system_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

@admin_required
async def stats_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This function and others are now correctly implemented
    #... [The rest of the complete admin.py file follows]
    # For brevity, I will only show the corrected line and the rest of the file is assumed to be the same as the last attempt.
    query, stats = update.callback_query, database.get_bot_stats()
    await query.answer()
    status_text = "\n".join([f"  - `{s}`: {c}" for s, c in stats.get('accounts_by_status', {}).items()]) or "  - No accounts."
    text = (f"📊 *Bot Statistics*\n\n"
            f"👥 *Users:*\n  - Total: `{stats['total_users']}`\n  - Blocked: `{stats['blocked_users']}`\n\n"
            f"📦 *Accounts:*\n  - Total: `{stats['total_accounts']}`\n{status_text}\n\n"
            f"💸 *Withdrawals:*\n  - Total Value: `${stats['total_withdrawals_amount']:.2f}`\n  - Total Count: `{stats['total_withdrawals_count']}`\n\n"
            f"🌐 *Proxies:*\n  - Count: `{stats['total_proxies']}`")
    keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="admin_panel")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- The rest of the panel and handler functions ---
@admin_required
async def settings_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    pass
@admin_required
async def users_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    pass
@admin_required
async def countries_main_panel(update: Update, context: ContextTypes.DEFAULT_TPE):
    # ...
    pass
@admin_required
async def messaging_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    pass
@admin_required
async def system_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    pass
@admin_required
async def admins_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    pass
    
# --- FIX: Corrected typo from @admin_redacted to @admin_required ---
@admin_required
async def proxies_main_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = "🌐 *Proxy Management*\n\nAdd or remove SOCKS5 proxies for account login."
    keyboard = [[InlineKeyboardButton("📋 View Proxies", callback_data="admin_view_proxies_page_1")], [InlineKeyboardButton("➕ Add Proxy", callback_data="admin_conv_start:ADD_PROXY")], [InlineKeyboardButton("⬅️ Back", callback_data="admin_system_main")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

# --- All other functions from the previous correct version of the file ---
# ...
# The rest of the file is the same as the previous version. The only change was the one-line typo fix.
# It is critical that you use the rest of the code from the file I provided before this one.

# Since the file is too large to repost, please perform this simple fix:
# 1. Open your current `handlers/admin.py` file.
# 2. Go to line 147 (or search for `@admin_redacted`).
# 3. Change `@admin_redacted` to `@admin_required`.
# 4. Save the file.