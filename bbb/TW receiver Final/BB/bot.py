# START OF FILE bot.py

# bot.py
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from telegram import Bot, BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from rich.logging import RichHandler

import database
from config import BOT_TOKEN, INITIAL_ADMIN_ID, SCHEDULER_DB_FILE
from handlers import admin, start, commands, login, callbacks

# --- Logging Setup ---
log_level = logging.INFO
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
rich_handler = RichHandler(rich_tracebacks=True, markup=True, show_path=False, log_time_format="[%X]")
root_logger.addHandler(rich_handler)
file_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
file_handler = RotatingFileHandler("bot_activity.log", maxBytes=5*1024*1024, backupCount=2, encoding='utf-8')
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def reprocessing_cron_job(bot_token: str):
    """
    This recurring job checks for accounts that need attention.
    It handles two cases:
    1. Accounts waiting 24h for other sessions to be terminated.
    2. Accounts whose initial check failed due to an error (e.g., bot restart, network issue).
    This design is robust and survives bot restarts.
    """
    logger.info("Cron job: Running periodic account checks...")
    bot = Bot(token=bot_token)
    
    # --- Case 1: Handle accounts marked for 24-hour reprocessing ---
    accounts_for_reprocessing = database.get_accounts_for_reprocessing()
    if accounts_for_reprocessing:
        logger.info(f"Cron job: Found {len(accounts_for_reprocessing)} account(s) for 24h reprocessing.")
        reprocessing_tasks = [login.reprocess_account(bot, acc) for acc in accounts_for_reprocessing]
        await asyncio.gather(*reprocessing_tasks)
    
    # --- Case 2: Handle accounts stuck in 'pending_confirmation' ---
    stuck_accounts = database.get_stuck_pending_accounts()
    if stuck_accounts:
        logger.info(f"Cron job: Found {len(stuck_accounts)} stuck account(s). Retrying initial check.")
        retry_tasks = [
            login.schedule_initial_check(
                bot_token=bot_token,
                user_id_str=str(acc['user_id']),
                chat_id=acc['user_id'],
                phone_number=acc['phone_number'],
                job_id=acc['job_id']
            ) for acc in stuck_accounts
        ]
        await asyncio.gather(*retry_tasks)

    if not accounts_for_reprocessing and not stuck_accounts:
        logger.info("Cron job: No accounts needed attention.")
    
    logger.info("Cron job: Finished periodic account checks.")


async def post_init(application: Application):
    """Tasks to run after the bot is initialized but before it starts polling."""
    logger.info("[bold blue]Running post-initialization tasks...[/bold blue]")

    # 1. Initialize Database
    database.init_db()
    logger.info("[green]Database schema checked/initialized (WAL mode enabled).[/green]")

    # 2. Grant initial admin privileges
    if INITIAL_ADMIN_ID:
        if database.add_admin(INITIAL_ADMIN_ID):
             logger.info(f"[green]Granted admin privileges to initial admin ID: {INITIAL_ADMIN_ID}[/green]")
        else:
             logger.info(f"[green]Checked admin privileges for initial admin ID: {INITIAL_ADMIN_ID}[/green]")


    # 3. Load dynamic settings into bot_data
    application.bot_data.update(database.get_all_settings())
    application.bot_data['countries_config'] = database.get_countries_config()
    logger.info("[green]Loaded dynamic settings and country configs into bot context.[/green]")

    # 4. Set up bot commands (user-facing and admin-facing)
    user_commands = [
        BotCommand("start", "🚀 Start the bot"),
        BotCommand("balance", "💼 Check your balance"),
        BotCommand("cap", "📋 View available countries & rates"),
        BotCommand("help", "🆘 Get help and info"),
        BotCommand("rules", "📜 Read the bot rules"),
        BotCommand("cancel", "❌ Cancel the current operation"),
    ]
    admin_commands = user_commands + [
        BotCommand("admin", "👑 Access Admin Panel"),
        BotCommand("recheck", "🔄 Re-check a failed account")
    ]

    await application.bot.set_my_commands(user_commands, scope=BotCommandScopeDefault())
    logger.info("[green]Default user commands have been set.[/green]")
    
    all_admins = database.get_all_admins()
    admin_count = 0
    for admin_user in all_admins:
        try:
            await application.bot.set_my_commands(admin_commands, scope=BotCommandScopeChat(chat_id=admin_user['telegram_id']))
            admin_count += 1
        except Exception as e:
            logger.warning(f"Could not set commands for admin {admin_user['telegram_id']}: {e}")
    if admin_count > 0:
        logger.info(f"[green]Admin-specific commands have been set for {admin_count} admins.[/green]")


    # 5. Initialize and start PERSISTENT APScheduler
    jobstores = {
        'default': SQLAlchemyJobStore(url=f'sqlite:///{SCHEDULER_DB_FILE}')
    }
    job_defaults = {
        'coalesce': True,
        'misfire_grace_time': 300 # Run job if it was missed by up to 5 mins
    }
    scheduler = AsyncIOScheduler(
        timezone="UTC",
        jobstores=jobstores,
        job_defaults=job_defaults
    )
    application.bot_data["scheduler"] = scheduler
    
    if not scheduler.running:
        scheduler.start()
        logger.info("[green]Persistent APScheduler started.[/green]")
        
        scheduler.add_job(
            reprocessing_cron_job, 
            'interval', 
            minutes=5, 
            args=[BOT_TOKEN], 
            id='reprocessing_cron_job', 
            replace_existing=True
        )
        logger.info("[green]Added recurring job for all account maintenance.[/green]")

async def post_shutdown(application: Application):
    """Tasks to run on graceful shutdown."""
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[yellow]APScheduler shut down.[/yellow]")

def main() -> None:
    """Start the bot."""
    logger.info("[bold cyan]Bot starting...[/bold cyan]")

    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # --- Register Handlers ---
    # Admin Handlers (Highest Priority: group 0)
    admin_handlers = admin.get_admin_handlers()
    application.add_handlers(admin_handlers, group=0)
    logger.info(f"[yellow]Registered {len(admin_handlers)} admin handlers in group 0.[/yellow]")

    # User-facing Handlers (Normal Priority: group 1)
    user_handlers = [
        CommandHandler("start", start.start),
        CommandHandler("balance", commands.balance_cmd),
        CommandHandler("cap", commands.cap),
        CommandHandler("help", commands.help_command),
        CommandHandler("rules", commands.rules_command),
        CommandHandler("cancel", commands.cancel_operation),
        CallbackQueryHandler(callbacks.on_callback_query),
        MessageHandler(filters.TEXT & ~filters.COMMAND, commands.on_text_message),
    ]
    application.add_handlers(user_handlers, group=1)
    logger.info(f"[yellow]Registered {len(user_handlers)} user handlers in group 1.[/yellow]")

    logger.info("[bold green]Bot is ready and polling for updates...[/bold green]")
    application.run_polling()

if __name__ == "__main__":
    main()
# END OF FILE bot.py