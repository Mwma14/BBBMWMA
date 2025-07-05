# handlers/callbacks.py
import logging
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import BadRequest

import database
from . import commands  # Import our new content generator functions

logger = logging.getLogger(__name__)

async def on_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles all non-admin callback queries from inline buttons.
    This now acts as the main navigation router for the user menu.
    """
    query = update.callback_query
    if not query or not query.data:
        return

    # Answer the query immediately to stop the loading icon on the user's end.
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Failed to answer callback query for data {query.data}: {e}")

    data = query.data
    user_id = query.from_user.id
    
    # --- Navigation Router ---
    
    text, keyboard = None, None

    if data == "nav_start":
        text, keyboard = commands.get_start_menu_content(context)
        
    elif data == "nav_balance":
        text, keyboard = commands.get_balance_content(context, user_id)
        
    elif data == "nav_cap":
        text, keyboard = commands.get_cap_content(context)
        
    elif data == "nav_rules":
        text, keyboard = commands.get_rules_content(context)

    elif data == "nav_support":
        text, keyboard = commands.get_support_content(context)

    elif data == "withdraw":
        await handle_withdraw_callback(update, context)
        return # This flow sends a new message, so we exit early.
        
    else:
        logger.info(f"Unhandled user callback query data: {data}")
        return

    # If we have content, edit the message.
    if text and keyboard:
        try:
            await query.edit_message_text(
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
        except BadRequest as e:
            # Ignore "Message is not modified" error which is common
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing message for callback {data}: {e}")

async def handle_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the 'withdraw' button press from the /balance command."""
    query = update.callback_query
    telegram_id = query.from_user.id
    
    _, balance_to_withdraw, _, _, _ = database.get_user_balance_details(telegram_id)
    
    min_withdraw = float(context.bot_data.get('min_withdraw', 1.0))
    if balance_to_withdraw < min_withdraw:
        # Edit the message to show the error
        text, keyboard = commands.get_balance_content(context, telegram_id)
        error_text = text + f"\n\n⚠️ Your balance of `${balance_to_withdraw:.2f}` is below the minimum of `${min_withdraw:.2f}`."
        await query.edit_message_text(text=error_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        return

    # Remove the buttons and ask for the address
    await query.edit_message_reply_markup(reply_markup=None)
    context.user_data['state'] = "waiting_for_address"
    
    await context.bot.send_message(
        chat_id=query.message.chat.id, 
        text=(
            "💳 *WITHDRAWAL REQUEST*\n\n"
            "Please enter your wallet address (e.g., USDT TRC20).\n\n"
            "Type /cancel to abort."
        ),
        parse_mode=ParseMode.MARKDOWN,
        reply_to_message_id=query.message.message_id 
    )