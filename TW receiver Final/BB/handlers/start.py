# handlers/start.py
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
import database
import logging

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    # Get or create the user in the database. Returns the DB row and a boolean.
    db_user, is_new_user = database.get_or_create_user(user_id, user.username)

    if is_new_user:
        logger.info(f"New user joined: {user.full_name} (@{user.username}, ID: {user_id})")
        admin_channel = context.bot_data.get('admin_channel')
        if admin_channel:
            try:
                await context.bot.send_message(
                    chat_id=admin_channel,
                    text=f"✅ New User Alert\n\n- Name: {user.full_name}\n- Username: @{user.username}\n- ID: `{user_id}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            except Exception as e:
                logger.warning(f"Could not send new user notification to admin channel: {e}")

    # Check if user is blocked
    if db_user and db_user.get('is_blocked'):
        await update.message.reply_text("You have been blocked from using this bot.")
        return

    # Mandatory channel join check
    channel_username = context.bot_data.get('channel_username')
    if channel_username:
        try:
            member = await context.bot.get_chat_member(channel_username, user_id)
            if member.status in ("left", "kicked"):
                raise Exception("User is not a member of the channel")
        except Exception:
            channel_link = f"https://t.me/{channel_username.lstrip('@')}"
            btn = InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Join Channel", url=channel_link)]])
            await update.message.reply_text(
                f"👋 Welcome!\n\nTo use this bot, you must first join our channel: {channel_username}\n\n"
                "Click the button below to join, then press /start again.",
                reply_markup=btn,
            )
            return

    # Check global bot status
    if context.bot_data.get('bot_status') == 'OFF':
        await update.message.reply_text("🤖 The bot is currently offline for maintenance. Please check back later.")
        return

    # Welcome message from database
    welcome_text = context.bot_data.get('welcome_message', "Welcome!")

    keyboard = [
        [InlineKeyboardButton("💼 My Balance", callback_data="nav_balance"), InlineKeyboardButton("📋 Countries & Rates", callback_data="nav_cap")],
        [InlineKeyboardButton("📜 Rules", callback_data="nav_rules"), InlineKeyboardButton("🆘 Contact Support", callback_data="nav_support")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(text=welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup, disable_web_page_preview=True)