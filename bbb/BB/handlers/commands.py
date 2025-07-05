# handlers/commands.py
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import database
from . import login

logger = logging.getLogger(__name__)

# --- Navigation Content Generators ---
# These functions now RETURN text and a keyboard, they do not send messages.

def get_start_menu_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Generates the content for the main /start menu."""
    welcome_text = context.bot_data.get('welcome_message', "Welcome!")
    keyboard = [
        [InlineKeyboardButton("💼 My Balance", callback_data="nav_balance"), InlineKeyboardButton("📋 Countries & Rates", callback_data="nav_cap")],
        [InlineKeyboardButton("📜 Rules", callback_data="nav_rules"), InlineKeyboardButton("🆘 Contact Support", callback_data="nav_support")]
    ]
    return welcome_text, InlineKeyboardMarkup(keyboard)

def get_balance_content(context: ContextTypes.DEFAULT_TYPE, telegram_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Generates the content for the user's balance view."""
    summary, balance, _, _, _ = database.get_user_balance_details(telegram_id)
    
    msg_parts = [f"📊 *Balance Summary for `{telegram_id}`*\n"]
    msg_parts.append(f"💰 *Available Balance: ${balance:.2f}*")
    msg_parts.append(f"✅ From *{summary.get('confirmed_ok', 0)}* healthy accounts.\n")
    
    in_progress = summary.get('pending_confirmation', 0) + summary.get('pending_session_termination', 0)
    if in_progress > 0: msg_parts.append(f"⏳ *In Progress: {in_progress}*")
    
    issue_accounts = summary.get('confirmed_restricted', 0) + summary.get('confirmed_error', 0)
    if issue_accounts > 0: msg_parts.append(f"⚠️ *With Issues: {issue_accounts}* (Not in balance)")

    min_w = float(context.bot_data.get('min_withdraw', 1.0))
    keyboard_buttons = []
    if balance >= min_w:
        keyboard_buttons.append([InlineKeyboardButton("💳 Withdraw Balance", callback_data="withdraw")])
    
    keyboard_buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="nav_start")])
    
    return "\n".join(msg_parts), InlineKeyboardMarkup(keyboard_buttons)

def get_cap_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Generates the content for the available countries view."""
    countries_config = context.bot_data.get("countries_config", {})
    if not countries_config:
        text = "Country configuration not loaded or empty."
    else:
        header = "📋 *Available Countries & Rates*\n\n"
        lines = []
        for code, info in sorted(countries_config.items(), key=lambda item: item[1]['name']):
            price_str = f"${info.get('price', 0.0):.2f}"
            time_str = f"{info.get('time', 0) // 60}min"
            lines.append(f"{info.get('flag', '🏳️')} `{code}` | *{info.get('name', 'N/A')}* | 💰{price_str} | ⏳{time_str}")
        text = header + "\n".join(lines)
        
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_start")]])
    return text, keyboard

def get_rules_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """Generates the content for the bot rules."""
    rules_text = context.bot_data.get('rules_message', "Rules not set.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_start")]])
    return rules_text, keyboard

def get_support_content(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup | None]:
    """Generates the content for the support contact info."""
    support_id = context.bot_data.get('support_id', '')
    keyboard = None
    if support_id and support_id.isdigit():
        support_link = f"tg://user?id={support_id}"
        text = "Click the button below to contact our support team."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Contact Support", url=support_link)],
            [InlineKeyboardButton("⬅️ Back", callback_data="nav_start")]
        ])
    elif support_id:
        text = f"You can contact our support here: {support_id}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_start")]])
    else:
        text = "Support contact has not been configured by the admin."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="nav_start")]])
    return text, keyboard

# --- Command Handlers (Now used for direct commands like /balance) ---

async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the user's detailed balance when they type /balance."""
    text, keyboard = get_balance_content(context, update.effective_user.id)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def cap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows countries when they type /cap."""
    text, keyboard = get_cap_content(context)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows rules when they type /rules."""
    text, keyboard = get_rules_content(context)
    await (update.message or update.callback_query.message).reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the help message from database."""
    help_text = context.bot_data.get('help_message', "Help message not set.")
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

# --- Message Handlers ---

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles non-command text messages for login or withdrawal."""
    if not update.message or not update.message.text: return
    text_content = update.message.text.strip()
    user_state = context.user_data.get('state')

    if user_state == "waiting_for_address":
        await handle_withdrawal_address(update, context)
    elif isinstance(context.user_data.get('login_flow'), dict):
        await login.handle_login(update, context)
    elif text_content.startswith("+") and len(text_content) > 5 and text_content[1:].isdigit():
        await login.handle_login(update, context)

async def handle_withdrawal_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the withdrawal after receiving a wallet address."""
    wallet_address = update.message.text.strip()
    telegram_id = update.effective_user.id

    if not wallet_address:
        await update.message.reply_text("❌ The address cannot be empty. Please enter your withdrawal address or use /cancel.")
        return

    context.user_data.pop('state', None)
    _, actual_balance, _, _, ok_accounts = database.get_user_balance_details(telegram_id)
    
    max_w = float(context.bot_data.get('max_withdraw', 100.0))
    withdrawal_amount = min(actual_balance, max_w)

    if withdrawal_amount <= 0:
        await update.message.reply_text("⚠️ Your available balance for withdrawal is zero. Please check /balance again.")
        return
    
    database.process_withdrawal(telegram_id, wallet_address, withdrawal_amount, ok_accounts)
    
    await update.message.reply_text(
        f"✅ *Withdrawal Processed*\n\n"
        f"💰 Amount: *${withdrawal_amount:.2f}*\n"
        f"📬 Address: `{wallet_address}`\n\n"
        f"Your request has been submitted and your balance updated.",
        parse_mode=ParseMode.MARKDOWN
    )

    admin_channel = context.bot_data.get('admin_channel')
    if admin_channel:
        try:
            await context.bot.send_message(
                admin_channel,
                f"💸 *New Withdrawal Processed*\n\n"
                f"👤 User: @{update.effective_user.username} (`{telegram_id}`)\n"
                f"💰 Amount: *${withdrawal_amount:.2f}*\n"
                f"📬 Address: `{wallet_address}`\n"
                f"📦 Accounts: {len(ok_accounts)}\n\n"
                f"🗓️ Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            logger.error(f"Failed to send admin notification to {admin_channel}: {e}")

async def cancel_operation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generic cancel command to clear user state."""
    if 'login_flow' in context.user_data:
        from . import login # local import to avoid circular dependency
        await login.cleanup_login_flow(context)
    context.user_data.clear()
    await update.message.reply_text("✅ Operation canceled.")