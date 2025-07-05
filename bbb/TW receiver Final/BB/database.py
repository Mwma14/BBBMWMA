# START OF FILE database.py

import sqlite3
import logging
import json
from datetime import datetime
import threading
from functools import wraps
import os

logger = logging.getLogger(__name__)

DB_FILE = os.path.abspath("bot.db")

db_lock = threading.Lock()

# --- Connection and Decorator ---
def get_db_connection():
    """Establishes a connection to the SQLite database and enables WAL mode."""
    conn = sqlite3.connect(DB_FILE, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def db_transaction(func):
    """Decorator for database WRITE operations."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        with db_lock:
            conn = get_db_connection()
            try:
                result = func(conn, *args, **kwargs)
                conn.commit()
                return result
            except Exception as e:
                conn.rollback()
                logger.error(f"DB transaction failed in {func.__name__}: {e}", exc_info=True)
                raise
            finally:
                conn.close()
    return wrapper

# --- Base Operations (Thread-Safe) ---
def fetch_one(query, params=()):
    with db_lock:
        conn = get_db_connection()
        try:
            result = conn.execute(query, params).fetchone()
            return dict(result) if result else None
        finally:
            conn.close()

def fetch_all(query, params=()):
    with db_lock:
        conn = get_db_connection()
        try:
            results = conn.execute(query, params).fetchall()
            return [dict(row) for row in results]
        finally:
            conn.close()

def execute_query(query, params=()):
    with db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            conn.rollback()
            logger.error(f"DB execute_query failed: {e}", exc_info=True)
            raise
        finally:
            conn.close()

# --- Initialization ---
@db_transaction
def init_db(conn):
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, is_blocked INTEGER DEFAULT 0, join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, manual_balance_adjustment REAL DEFAULT 0.0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS admins (telegram_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, phone_number TEXT NOT NULL, reg_time TIMESTAMP NOT NULL, status TEXT NOT NULL, job_id TEXT, session_file TEXT, last_status_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users (telegram_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS withdrawals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL NOT NULL, address TEXT NOT NULL, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'completed', account_ids TEXT, FOREIGN KEY (user_id) REFERENCES users (telegram_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS countries (code TEXT PRIMARY KEY, flag TEXT, price REAL, time INTEGER, name TEXT, capacity INTEGER DEFAULT -1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (id INTEGER PRIMARY KEY AUTOINCREMENT, proxy TEXT UNIQUE NOT NULL)''')

    default_settings = {
        'api_id': '25707049', 'api_hash': '676a65f1f7028e4d969c628c73fbfccc',
        'channel_username': '@TW_Receiver_News', 'admin_channel': '@RAESUPPORT', 'support_id': str(6158106622),
        'spambot_username': '@SpamBot', 'two_step_password': '123456',
        'enable_spam_check': 'True', 'enable_device_check': 'False',
        'bot_status': 'ON', 'add_account_status': 'UNLOCKED',
        'min_withdraw': '1.0', 'max_withdraw': '100.0',
        'welcome_message': "🎉 **Welcome to the Account Receiver Bot!**\n\nTo add an account, simply send the phone number with the country code (e.g., `+12025550104`).\n\nUse the buttons below to navigate.",
        'help_message': "🆘 **Bot Help & Guide**\n\n🔹 `/start` - Displays the main welcome message.\n🔹 `/balance` - Shows your detailed balance and allows withdrawal.\n🔹 `/rules` - View the bot's rules.\n🔹 `/cancel` - Stops any ongoing process you started.",
        'rules_message': "📜 **Bot Rules**\n\n1. Do not use the same phone number multiple times.\n2. Any attempt to exploit or cheat the bot will result in a permanent ban without appeal.\n3. The administration is not responsible for any account limitations or issues that arise after a successful confirmation."
    }
    for key, value in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))

    if cursor.execute("SELECT COUNT(*) FROM countries").fetchone()[0] == 0:
        default_countries = {
            "+44": {"flag": "🇬🇧", "price": 0.62, "time": 600, "name": "UK", "capacity": 100}, 
            "+95": {"flag": "🇲🇲", "price": 0.18, "time": 60, "name": "Myanmar", "capacity": 50}
        }
        for code, data in default_countries.items():
            cursor.execute("INSERT OR IGNORE INTO countries (code, flag, price, time, name, capacity) VALUES (?, ?, ?, ?, ?, ?)", (code, data['flag'], data['price'], data['time'], data['name'], data['capacity']))
    logger.info("Database initialized/checked successfully.")

# Admin Management
def add_admin(tid): return execute_query("INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)", (tid,))
def remove_admin(tid): return execute_query("DELETE FROM admins WHERE telegram_id = ?", (tid,))
def is_admin(tid): return fetch_one("SELECT 1 FROM admins WHERE telegram_id = ?", (tid,)) is not None
def get_all_admins(): return fetch_all("SELECT * FROM admins")

# Settings Management
def get_setting(key, default=None):
    result = fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
    return result['value'] if result else default
def get_all_settings(): return {row['key']: row['value'] for row in fetch_all("SELECT * FROM settings")}
def set_setting(key, value): return execute_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))

# Country Management
def get_countries_config(): return {row['code']: row for row in fetch_all("SELECT * FROM countries ORDER BY name")}
def add_country(code, name, flag, price, time, capacity): execute_query("INSERT OR REPLACE INTO countries (code, name, flag, price, time, capacity) VALUES (?, ?, ?, ?, ?, ?)", (code, name, flag, price, time, capacity))
def delete_country(code): return execute_query("DELETE FROM countries WHERE code = ?", (code,))
def get_country_by_code(code): return fetch_one("SELECT * FROM countries WHERE code = ?", (code,))
def get_country_account_count(code):
    return fetch_one("SELECT COUNT(*) as c FROM accounts WHERE phone_number LIKE ?", (f"{code}%",))['c']

# User Management
def get_or_create_user(tid, username=None):
    user = fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tid,))
    is_new = not user
    if is_new:
        execute_query("INSERT INTO users (telegram_id, username, join_date) VALUES (?, ?, ?)", (tid, username, datetime.utcnow()))
    elif username and user.get('username') != username:
        execute_query("UPDATE users SET username = ? WHERE telegram_id = ?", (username, tid))
    return fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tid,)), is_new

def get_user_by_id(tid): return fetch_one("SELECT * FROM users WHERE telegram_id = ?", (tid,))
def get_all_users(page=1, limit=10): return fetch_all("SELECT u.*, (SELECT COUNT(*) FROM accounts WHERE user_id = u.telegram_id) as account_count FROM users u ORDER BY join_date DESC LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
def count_all_users(): return fetch_one("SELECT COUNT(*) as c FROM users")['c']
def block_user(tid): return execute_query("UPDATE users SET is_blocked = 1 WHERE telegram_id = ?", (tid,))
def unblock_user(tid): return execute_query("UPDATE users SET is_blocked = 0 WHERE telegram_id = ?", (tid,))
def get_all_user_ids(only_non_blocked=True):
    query = "SELECT telegram_id FROM users"
    if only_non_blocked: query += " WHERE is_blocked = 0"
    return [row['telegram_id'] for row in fetch_all(query)]
def adjust_user_balance(user_id, amount_to_add): return execute_query("UPDATE users SET manual_balance_adjustment = manual_balance_adjustment + ? WHERE telegram_id = ?", (amount_to_add, user_id))

@db_transaction
def delete_all_user_data(conn, user_id):
    cursor = conn.cursor()
    cursor.execute("SELECT session_file FROM accounts WHERE user_id = ?", (user_id,))
    session_files = [row[0] for row in cursor.fetchall() if row and row[0]]
    cursor.execute("DELETE FROM accounts WHERE user_id = ?", (user_id,))
    accounts_deleted = cursor.rowcount
    cursor.execute("DELETE FROM withdrawals WHERE user_id = ?", (user_id,))
    withdrawals_deleted = cursor.rowcount
    cursor.execute("DELETE FROM users WHERE telegram_id = ?", (user_id,))
    user_deleted = cursor.rowcount
    
    files_deleted_count = 0
    for s_file in session_files:
        if os.path.exists(s_file):
            try:
                os.remove(s_file)
                files_deleted_count += 1
            except OSError as e:
                logger.error(f"Could not delete session file {s_file} for user {user_id}: {e}")

    logger.info(f"Purged data for user {user_id}: {user_deleted} user record, {accounts_deleted} accounts, {withdrawals_deleted} withdrawals, {files_deleted_count} session files.")
    return user_deleted > 0

# Proxy Management
def add_proxy(proxy_str): return execute_query("INSERT OR IGNORE INTO proxies (proxy) VALUES (?)", (proxy_str,))
def remove_proxy_by_id(proxy_id): return execute_query("DELETE FROM proxies WHERE id = ?", (proxy_id,))
def get_all_proxies(page=1, limit=10): return fetch_all("SELECT * FROM proxies ORDER BY id LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
def get_random_proxy():
    proxy = fetch_one("SELECT proxy FROM proxies ORDER BY RANDOM() LIMIT 1")
    return proxy['proxy'] if proxy else None
def count_all_proxies(): return fetch_one("SELECT COUNT(*) as c FROM proxies")['c']

# Account Management
def check_phone_exists(p_num): return fetch_one("SELECT 1 FROM accounts WHERE phone_number = ?", (p_num,)) is not None
def add_account(uid, p, status, jid, sfile):
    execute_query("INSERT INTO accounts (user_id, phone_number, reg_time, status, job_id, session_file) VALUES (?, ?, ?, ?, ?, ?)", (uid, p, datetime.utcnow(), status, jid, sfile))
    return fetch_one("SELECT last_insert_rowid() as id")['id']
def update_account_status(jid, status): execute_query("UPDATE accounts SET status = ?, last_status_update = ? WHERE job_id = ?", (status, datetime.utcnow(), jid))
def find_account_by_job_id(jid): return fetch_one("SELECT * FROM accounts WHERE job_id = ?", (jid,))
def find_account_by_phone_number(phone_number):
    return fetch_one("SELECT * FROM accounts WHERE phone_number = ?", (phone_number,))
def get_account_by_phone_for_user(user_id, phone): return fetch_one("SELECT * FROM accounts WHERE user_id = ? AND phone_number = ?", (user_id, phone))
def get_user_accounts(user_id): return fetch_all("SELECT phone_number, status, session_file FROM accounts WHERE user_id = ?", (user_id,))
def get_all_accounts_paginated(page=1, limit=10): return fetch_all("SELECT a.id, a.phone_number, a.status, a.user_id, u.username FROM accounts a LEFT JOIN users u ON a.user_id = u.telegram_id ORDER BY a.reg_time DESC LIMIT ? OFFSET ?", (limit, (page - 1) * limit))
def count_all_accounts(): return fetch_one("SELECT COUNT(*) as c FROM accounts")['c']
def get_accounts_with_sessions():
    all_accounts = fetch_all("SELECT * FROM accounts WHERE session_file IS NOT NULL")
    return [acc for acc in all_accounts if acc.get('session_file') and os.path.exists(acc['session_file'])]
def get_accounts_for_reprocessing():
    query = "SELECT * FROM accounts WHERE status = 'pending_session_termination' AND last_status_update <= datetime('now', '-24 hours')"
    return fetch_all(query)
def get_stuck_pending_accounts():
    query = "SELECT * FROM accounts WHERE status = 'pending_confirmation' AND reg_time <= datetime('now', '-30 minutes')"
    return fetch_all(query)
def get_error_accounts():
    return fetch_all("SELECT * FROM accounts WHERE status = 'confirmed_error'")
def get_problematic_accounts_by_user(user_id):
    """Finds all accounts for a user that are pending or have an error."""
    query = "SELECT * FROM accounts WHERE user_id = ? AND (status = 'pending_confirmation' OR status = 'confirmed_error')"
    return fetch_all(query, (user_id,))

# Stats and Withdrawals
def get_all_withdrawals(page=1, limit=10): return fetch_all("SELECT w.*, u.username FROM withdrawals w JOIN users u ON w.user_id = u.telegram_id ORDER BY w.timestamp DESC LIMIT ? OFFSET ?", (limit, (page-1)*limit))
def count_all_withdrawals(): return fetch_one("SELECT COUNT(*) as c FROM withdrawals")['c']
def get_bot_stats():
    return {
        "total_users": count_all_users(),
        "blocked_users": fetch_one("SELECT COUNT(*) as c FROM users WHERE is_blocked = 1")['c'],
        "total_accounts": count_all_accounts(),
        "accounts_by_status": {r['status']: r['c'] for r in fetch_all("SELECT status, COUNT(*) as c FROM accounts GROUP BY status")},
        "total_withdrawals_amount": (fetch_one("SELECT SUM(amount) as s FROM withdrawals") or {'s': 0})['s'] or 0.0,
        "total_withdrawals_count": count_all_withdrawals(),
        "total_proxies": count_all_proxies(),
    }
def get_user_balance_details(uid):
    cfg, accs = get_countries_config(), fetch_all("SELECT phone_number, status FROM accounts WHERE user_id = ?", (uid,))
    user_row = fetch_one("SELECT manual_balance_adjustment FROM users WHERE telegram_id = ?", (uid,))
    manual = (user_row or {'manual_balance_adjustment': 0.0})['manual_balance_adjustment']
    summary, calc_bal, ok_accs = {}, 0.0, []
    for acc in accs:
        summary[acc['status']] = summary.get(acc['status'], 0) + 1
        if acc['status'] == 'confirmed_ok':
            mc = next((c for c in sorted(cfg.keys(), key=len, reverse=True) if acc['phone_number'].startswith(c)), None)
            if mc:
                calc_bal += cfg.get(mc, {}).get('price', 0.0)
                ok_accs.append(acc)
    total_balance = round(calc_bal + manual, 2)
    return summary, total_balance, calc_bal, manual, ok_accs

@db_transaction
def process_withdrawal(conn, user_id, address, amount, accounts_to_update):
    cursor = conn.cursor()
    account_ids_json = json.dumps([acc['phone_number'] for acc in accounts_to_update])
    cursor.execute("INSERT INTO withdrawals (user_id, amount, address, account_ids, status) VALUES (?, ?, ?, ?, 'completed')", (user_id, amount, address, account_ids_json))
    if accounts_to_update:
        phone_numbers = tuple(acc['phone_number'] for acc in accounts_to_update)
        placeholders = ','.join('?' for _ in phone_numbers)
        query = f"UPDATE accounts SET status = 'withdrawn' WHERE user_id = ? AND phone_number IN ({placeholders})"
        params = (user_id,) + phone_numbers
        cursor.execute(query, params)
    cursor.execute("UPDATE users SET manual_balance_adjustment = 0 WHERE telegram_id = ?", (user_id,))
    logger.info(f"Processed withdrawal for user {user_id} of amount {amount}. Updated {len(accounts_to_update)} accounts and reset manual balance.")

# END OF FILE database.py