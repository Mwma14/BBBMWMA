# START OF FILE handlers/login.py

import os
import logging
import asyncio
import random
from datetime import datetime, timedelta
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError, SessionPasswordNeededError, PhoneNumberInvalidError,
    FloodWaitError, PhoneCodeExpiredError, PasswordHashInvalidError
)
from telethon.tl.functions.account import GetAuthorizationsRequest, ResetAuthorizationRequest
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

import database
from config import BOT_TOKEN # Import BOT_TOKEN for independent job execution

logger = logging.getLogger(__name__)

DEVICE_PROFILES = [
    {"device_model": "Desktop", "system_version": "Windows 10", "app_version": "5.1.5 x64"},
    {"device_model": "PC 64bit", "system_version": "Windows 11", "app_version": "4.17.2 x64"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "SDK 34", "app_version": "10.13.0 (4641)"},
    {"device_model": "Apple iPhone 15 Pro Max", "system_version": "17.5.1", "app_version": "10.13"},
]

def _get_session_path(phone_number: str, user_id: str, countries_config: dict) -> str:
    """Generates the session file path with the new format: +PHONENUMBER (USERID).session"""
    country_name = "Uncategorized"
    matching_code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone_number.startswith(c)), None)
    if matching_code:
        country_name = countries_config[matching_code].get("name", "Unknown")
    folder_name = f"{matching_code} {country_name}" if matching_code else "Uncategorized"
    sessions_dir_path = os.path.join("sessions", folder_name)
    os.makedirs(sessions_dir_path, exist_ok=True)
    # Changed filename format to be more readable
    session_filename = f"{phone_number} ({user_id}).session"
    return os.path.join(sessions_dir_path, session_filename)

def _get_client_for_job(session_file: str, bot_data: dict) -> TelegramClient:
    api_id = int(bot_data['api_id'])
    api_hash = bot_data['api_hash']
    device_profile = random.choice(DEVICE_PROFILES)
    proxy_str = database.get_random_proxy()
    proxy_parts = proxy_str.split(':') if proxy_str else []
    proxy_config = None
    if len(proxy_parts) >= 2:
        try:
            proxy_config = {
                'proxy_type': 'socks5',
                'addr': proxy_parts[0],
                'port': int(proxy_parts[1])
            }
            if len(proxy_parts) == 4:
                proxy_config['username'] = proxy_parts[2]
                proxy_config['password'] = proxy_parts[3]
            logger.info(f"Using proxy {proxy_config['addr']} for new session.")
        except (ValueError, IndexError):
            logger.error(f"Invalid proxy format: {proxy_str}. Ignoring.")
    
    return TelegramClient(session_file, api_id, api_hash, device_model=device_profile["device_model"], system_version=device_profile["system_version"], app_version=device_profile["app_version"], proxy=proxy_config)

async def _perform_spambot_check(client: TelegramClient, spambot_username: str) -> str:
    if not spambot_username:
        logger.warning("SpamBot username not configured. Skipping check.")
        return 'ok'
    try:
        me = await client.get_me()
        logger.info(f"Performing spambot check for +{me.phone}.")
        async with client.conversation(spambot_username, timeout=30) as conv:
            await conv.send_message('/start')
            resp = await conv.get_response()
            logger.info(f"SpamBot response for +{me.phone}: {resp.text}")
            text_lower = resp.text.lower()
            if 'good news' in text_lower or 'no limits' in text_lower or 'is free' in text_lower:
                return 'ok'
            elif "i'm afraid" in text_lower or 'is limited' in text_lower or 'some limitations' in text_lower:
                return 'restricted'
            else:
                logger.warning(f"Unexpected SpamBot response for +{me.phone}: {resp.text}")
                return 'error'
    except asyncio.TimeoutError:
        logger.error(f"Timeout during conversation with @SpamBot.")
        return 'error'
    except Exception as e:
        logger.error(f"Error during spambot check: {e}", exc_info=True)
        return 'error'

async def reprocess_account(bot: Bot, account: dict):
    job_id = account['job_id']
    phone_number = account['phone_number']
    chat_id = account['user_id']
    logger.info(f"Job {job_id} (Reprocessing): Running final check and session termination for {phone_number}")
    bot_data = database.get_all_settings()
    if not account.get('session_file'):
        logger.error(f"Job {job_id} (Reprocessing): Could not find session file.")
        return
    client = _get_client_for_job(account['session_file'], bot_data)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception("Session became unauthorized during the 24h wait.")
        logger.info(f"Job {job_id} (Reprocessing): Terminating other sessions for {phone_number}.")
        authorizations = await client(GetAuthorizationsRequest())
        for auth in authorizations.authorizations:
            if not auth.current:
                await client(ResetAuthorizationRequest(hash=auth.hash))
        logger.info(f"Job {job_id} (Reprocessing): Successfully sent termination requests.")
        new_status = 'confirmed_ok'
        if bot_data.get('enable_spam_check') == 'True':
            spam_status = await _perform_spambot_check(client, bot_data.get('spambot_username'))
            if spam_status == 'restricted': new_status = 'confirmed_restricted'
            elif spam_status == 'error': new_status = 'confirmed_error'
        database.update_account_status(job_id, new_status)
        countries_config = database.get_countries_config()
        matching_code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone_number.startswith(c)), None)
        country_info = countries_config.get(matching_code) if matching_code else None
        price = country_info.get('price', 0.0) if country_info else 0.0
        if new_status == 'confirmed_ok':
            message = (f"🎉 Reprocessing complete! We have successfully processed your account.\n"
                       f"```\nNumber: {phone_number}\nPrice:  {price:.2f}$\nStatus: Free Spam\n```\n"
                       f"Congratulations, it has been added to your balance.")
        elif new_status == 'confirmed_restricted':
            message = (f"✅ Reprocessing for `{phone_number}` is complete.\n\n"
                       f"⚠️ Your account has limitations (reported as spam) and will **not** be added to your balance.")
        else: # confirmed_error
             message = (f"✅ Reprocessing for `{phone_number}` is complete.\n\n"
                       f"❌ An error occurred during the final check. The account will not be added to your balance.")
        await bot.send_message(chat_id, message, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Job {job_id} (Reprocessing): Critical error during final check: {e}", exc_info=True)
        database.update_account_status(job_id, 'confirmed_error')
        await bot.send_message(chat_id, f"❌ A critical error occurred while reprocessing `{phone_number}`. It will not be added to your balance.", parse_mode=ParseMode.MARKDOWN)
    finally:
        if client.is_connected():
            await client.disconnect()

# --- MODIFIED: The entire function is now wrapped in a try...except block to be robust ---
async def schedule_initial_check(bot_token: str, user_id_str: str, chat_id: int, phone_number: str, job_id: str):
    """
    This is the first job that runs after login. It decides whether to finalize
    the account now or mark it for later reprocessing.
    This version includes robust, all-encompassing error handling to prevent stuck accounts.
    """
    bot = Bot(token=bot_token)
    client = None # Define client here to be accessible in finally block

    try:
        logger.info(f"Job {job_id} (Initial Check): Running for {phone_number}")
        
        bot_data = database.get_all_settings()
        account = database.find_account_by_job_id(job_id)

        # Critical check: If account data is missing, we must notify the user.
        if not account or not account.get('session_file') or not os.path.exists(account.get('session_file')):
            logger.error(f"Job {job_id}: Aborting. Could not find account data or session file for {phone_number}.")
            await bot.send_message(
                chat_id,
                f"❌ An error occurred while trying to process `{phone_number}`. The account data could not be found, possibly due to a server issue. Please contact support.",
                parse_mode=ParseMode.MARKDOWN
            )
            # If the account exists but session is missing, mark as error
            if account:
                database.update_account_status(job_id, 'confirmed_error')
            return

        # Do not re-process if it's no longer in the initial pending state
        if account['status'] != 'pending_confirmation':
            logger.warning(f"Job {job_id}: Attempted to run initial check on account with status '{account['status']}'. Skipping.")
            return

        client = _get_client_for_job(account['session_file'], bot_data)
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception("Session not authorized.")

        # Device Check
        num_sessions = 1
        if bot_data.get('enable_device_check') == 'True':
            authorizations = await client(GetAuthorizationsRequest())
            num_sessions = len(authorizations.authorizations)
            logger.info(f"Job {job_id} (Initial Check): Device check found {num_sessions} session(s).")
        else:
            logger.info(f"Job {job_id} (Initial Check): Device check disabled.")

        if num_sessions > 1:
            logger.warning(f"Job {job_id} (Initial Check): Multiple sessions detected. Marking for 24h reprocessing.")
            database.update_account_status(job_id, 'pending_session_termination')
            
            user_message = (f"⚠️ Multiple active sessions detected for `{phone_number}`.\n"
                            f"🖥️ Total devices found: {num_sessions}\n\n"
                            f"Your account will be reprocessed in 24 hours to terminate other sessions and complete the check. You will be notified of the final result then.")
            await bot.send_message(chat_id, user_message, parse_mode=ParseMode.MARKDOWN)
            return # This is a normal exit, not an error

        # --- SINGLE DEVICE FLOW ---
        logger.info(f"Job {job_id} (Initial Check): Single session detected. Proceeding with immediate check.")
        new_status = 'confirmed_ok'
        if bot_data.get('enable_spam_check') == 'True':
            spam_status = await _perform_spambot_check(client, bot_data.get('spambot_username'))
            if spam_status == 'restricted': new_status = 'confirmed_restricted'
            elif spam_status == 'error': new_status = 'confirmed_error'

        database.update_account_status(job_id, new_status)

        countries_config = database.get_countries_config()
        matching_code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone_number.startswith(c)), None)
        country_info = countries_config.get(matching_code) if matching_code else None
        price = country_info.get('price', 0.0) if country_info else 0.0
        
        if new_status == 'confirmed_ok':
            message = (f"🎉 We have successfully processed your account\n"
                       f"```\nNumber: {phone_number}\nPrice:  {price:.2f}$\nStatus: Free Spam\n```\n"
                       f"Congratulations, it has been added to your balance.")
        elif new_status == 'confirmed_restricted':
            message = (f"⚠️ Your account `{phone_number}` is confirmed but has limitations (reported as spam).\n"
                       f"It will **not** be added to your balance.")
        else: # confirmed_error
            message = f"❌ An error occurred while checking `{phone_number}`. It will not be added to your balance."
        
        await bot.send_message(chat_id, message, parse_mode=ParseMode.MARKDOWN)
    
    except Exception as e:
        logger.error(f"Job {job_id} (Initial Check): A critical and unhandled error occurred: {e}", exc_info=True)
        # Always try to update DB status and notify user to prevent getting stuck
        database.update_account_status(job_id, 'confirmed_error')
        await bot.send_message(chat_id, f"❌ A critical error occurred while checking `{phone_number}` (e.g., network issue). It will not be added to your balance. Please contact support if this persists.", parse_mode=ParseMode.MARKDOWN)
    finally:
        # Ensure client is always disconnected
        if client and client.is_connected():
            await client.disconnect()

async def handle_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    state = context.user_data.get('login_flow', {})
    user = update.effective_user
    if not state:
        database.get_or_create_user(user.id, user.username)
        phone_number = text
        countries_config = context.bot_data.get("countries_config", {})
        matching_code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone_number.startswith(c)), None)
        if not matching_code:
            await update.message.reply_text("❌ Unsupported country.")
            return
        if database.check_phone_exists(phone_number):
            await update.message.reply_text("❌ This phone number is already registered.")
            return
        logger.info(f"User @{user.username} (`{user_id}`) started login for phone `{phone_number}`.")
        reply_msg = await update.message.reply_text("♻️ Initializing...")
        context.user_data['login_flow'] = {
            'phone': phone_number, 'step': 'awaiting_code', 
            'prompt_msg_id': reply_msg.message_id, 'status': 'failed'
        }
        session_filename = _get_session_path(phone_number, user_id, countries_config)
        client = _get_client_for_job(session_filename, context.bot_data)
        context.user_data['login_flow']['client'] = client
        context.user_data['login_flow']['session_file'] = session_filename
        try:
            await client.connect()
            await client.send_code_request(phone_number)
            prompt_text = f"Enter the code for `{phone_number}`.\n\nType /cancel to abort."
            await reply_msg.edit_text(prompt_text, parse_mode=ParseMode.MARKDOWN)
        except (FloodWaitError, PhoneNumberInvalidError, Exception) as e:
            error_message = f"❌ Error: `{e}`"
            if isinstance(e, FloodWaitError): error_message = f"❌ Rate limit. Wait {e.seconds}s."
            if isinstance(e, PhoneNumberInvalidError): error_message = "❌ Invalid phone number format."
            logger.error(f"Login init failed for `{phone_number}` by user `{user_id}`: {e}")
            await reply_msg.edit_text(error_message, parse_mode=ParseMode.MARKDOWN)
            if client.is_connected(): await client.disconnect()
            context.user_data.clear()
    elif state.get('step') == 'awaiting_code':
        client, phone = state.get('client'), state.get('phone')
        code = text
        await context.bot.edit_message_text("🔄 Verifying OTP...", chat_id=chat_id, message_id=state['prompt_msg_id'])
        try:
            await client.sign_in(phone=phone, code=code)
            logger.info(f"Telethon login successful for user `{user_id}` with phone `{phone}`.")
            if context.bot_data.get('two_step_password'):
                await client.edit_2fa(new_password=context.bot_data['two_step_password'])
            reg_time = datetime.utcnow()
            job_id = f"conf_{user_id}_{phone.replace('+', '')}_{int(reg_time.timestamp())}"
            database.add_account(user_id, phone, "pending_confirmation", job_id, state['session_file'])
            logger.info(f"Account for phone `{phone}` added to DB with job_id `{job_id}`.")
            scheduler = context.application.bot_data.get("scheduler")
            countries_config = context.bot_data.get("countries_config", {})
            matching_code = next((c for c in sorted(countries_config.keys(), key=len, reverse=True) if phone.startswith(c)), None)
            conf_time_s = countries_config.get(matching_code, {}).get('time', 600)
            run_date = datetime.utcnow() + timedelta(seconds=conf_time_s)
            scheduler.add_job(
                schedule_initial_check, 'date', run_date=run_date, 
                args=[BOT_TOKEN, user_id, chat_id, phone, job_id], id=job_id,
                misfire_grace_time=300
            )
            logger.info(f"Scheduled initial check for job `{job_id}` to run in {conf_time_s} seconds.")
            await update.message.reply_text(
                f"✅ Account `{phone}` registered.\n\n"
                f"It will be checked in {conf_time_s // 60} minutes. "
                "You will be notified of the result.",
                parse_mode=ParseMode.MARKDOWN)
            state['status'] = 'success' # Mark as success so session file is not deleted
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            logger.warning(f"Invalid/expired OTP for user `{user_id}` on phone `{phone}`.")
            await update.message.reply_text("⚠️ Incorrect or expired OTP. Try again or /cancel.")
            await context.bot.edit_message_text(f"Enter the code for `{phone}`", chat_id=chat_id, message_id=state['prompt_msg_id'], parse_mode=ParseMode.MARKDOWN)
            return
        except SessionPasswordNeededError:
            logger.warning(f"Account `{phone}` for user `{user_id}` has 2FA enabled, which is unsupported. Aborting.")
            await update.message.reply_text("❌ This account has 2FA enabled. Not supported.")
        except Exception as e:
            await update.message.reply_text(f"❌ A sign-in error occurred: `{e}`.")
            logger.error(f"Sign-in error for {user_id} ({phone}): {e}", exc_info=True)
        
        if client.is_connected(): await client.disconnect()
        context.user_data.clear()

async def cleanup_login_flow(context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('login_flow', {})
    client = state.get('client')
    if client and client.is_connected():
        await client.disconnect()
        logger.info("Disconnected Telethon client during manual cleanup/cancel.")
    session_file = state.get('session_file')
    if session_file and os.path.exists(session_file) and state.get('status') == 'failed':
        try:
            os.remove(session_file)
            logger.info(f"Removed orphaned session file on cancel: {session_file}")
        except OSError as e:
            logger.error(f"Error removing orphaned session file {session_file}: {e}")
# END OF FILE handlers/login.py