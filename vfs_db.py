"""MySQL Database Manager for VFS Global Automation.

Supports Hostinger Remote MySQL (srv2203.hstgr.io / 82.25.121.184) and local MySQL.
Stores users, multiple applicant profiles, VFS accounts, and booking history.
"""

import hashlib
import json
import logging
import os
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import pymysql
import pymysql.cursors

from config import cfg

log = logging.getLogger("vfs.db")


def hash_password(password: str, salt: Optional[str] = None) -> Tuple[str, str]:
    """Secure password hashing using PBKDF2-HMAC-SHA256 with 120,000 iterations."""
    if not salt:
        salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000
    )
    return key.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    """Verify password against stored PBKDF2 hash and salt."""
    key = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120000
    )
    return secrets.compare_digest(key.hex(), stored_hash)


import threading
import sqlite3
import time

_db_lock = threading.Lock()
_persistent_mysql_conn = None
_last_mysql_fail_time = 0.0
SQLITE_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vfs_local.sqlite")


class ResilientCursor:
    """Cursor wrapper that transparently supports both MySQL DictCursor and SQLite Row cursors."""
    def __init__(self, raw_cur, is_sqlite=False):
        self._cur = raw_cur
        self.is_sqlite = is_sqlite

    def execute(self, query, params=None):
        if self.is_sqlite:
            query = query.replace("%s", "?")
            query = query.replace("NOW()", "CURRENT_TIMESTAMP")
            if params is None:
                return self._cur.execute(query)
            return self._cur.execute(query, tuple(params) if isinstance(params, (list, tuple)) else params)
        else:
            if params is None:
                return self._cur.execute(query)
            return self._cur.execute(query, params)

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        if self.is_sqlite and hasattr(row, "keys"):
            return dict(row)
        return row

    def fetchall(self):
        rows = self._cur.fetchall()
        if self.is_sqlite:
            return [dict(r) if hasattr(r, "keys") else r for r in rows]
        return rows

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class ResilientConnection:
    """Connection wrapper that preserves persistent TCP sockets for MySQL and supports local SQLite."""
    def __init__(self, raw_conn, is_sqlite=False):
        self._raw = raw_conn
        self.is_sqlite = is_sqlite

    def cursor(self):
        if self.is_sqlite:
            return ResilientCursor(self._raw.cursor(), is_sqlite=True)
        return ResilientCursor(self._raw.cursor(), is_sqlite=False)

    def commit(self):
        try:
            self._raw.commit()
        except Exception:
            pass

    def rollback(self):
        try:
            self._raw.rollback()
        except Exception:
            pass

    def close(self):
        if self.is_sqlite:
            try:
                self._raw.commit()
                self._raw.close()
            except Exception:
                pass
        # Note: persistent MySQL connection stays open across requests to conserve connection quota!

    def __getattr__(self, name):
        return getattr(self._raw, name)


def init_sqlite_db():
    """Ensure local SQLite mirror has all tables and default seed data."""
    try:
        os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(SQLITE_DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT,
                telegram_chat_id TEXT,
                telegram_bot_token TEXT,
                telegram_notifications INTEGER DEFAULT 1,
                email_notifications INTEGER DEFAULT 1,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                full_name TEXT,
                role TEXT DEFAULT 'user',
                status TEXT DEFAULT 'active',
                created_by TEXT DEFAULT 'system',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            );
        """)
        # Safe migration for existing SQLite users table
        for _col, _type in [
            ("telegram_chat_id", "TEXT"), ("telegram_bot_token", "TEXT"),
            ("telegram_notifications", "INTEGER DEFAULT 1"), ("email_notifications", "INTEGER DEFAULT 1"),
            ("vfs_email", "TEXT"), ("vfs_password", "TEXT"),
            ("vfs_email_secondary", "TEXT"), ("vfs_password_secondary", "TEXT"),
            ("email_provider", "TEXT DEFAULT 'gmail'"),
            ("otp_email", "TEXT"), ("otp_app_password", "TEXT"),
            ("otp_imap_host", "TEXT"), ("otp_imap_port", "INTEGER DEFAULT 993"),
            ("otp_smtp_host", "TEXT"), ("otp_smtp_port", "INTEGER DEFAULT 587")
        ]:
            try:
                cur.execute(f"ALTER TABLE users ADD COLUMN {_col} {_type};")
            except Exception:
                pass

        cur.execute("""
            CREATE TABLE IF NOT EXISTS applicant_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                gender TEXT DEFAULT 'Male',
                dob TEXT NOT NULL,
                nationality TEXT DEFAULT 'India',
                passport_number TEXT NOT NULL,
                passport_expiry TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL,
                target_city TEXT DEFAULT 'delhi',
                visa_category TEXT DEFAULT 'Business',
                visa_sub_category TEXT DEFAULT 'Business Visa',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vfs_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_name TEXT,
                vfs_email TEXT UNIQUE NOT NULL,
                vfs_password TEXT NOT NULL,
                gmail_user TEXT,
                gmail_app_password TEXT,
                operator_id INTEGER,
                status TEXT DEFAULT 'active',
                is_active INTEGER DEFAULT 0,
                notes TEXT,
                last_used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Safe migration for existing SQLite vfs_accounts
        for _col, _type in [("account_name", "TEXT"), ("operator_id", "INTEGER"), ("is_active", "INTEGER DEFAULT 0")]:
            try:
                cur.execute(f"ALTER TABLE vfs_accounts ADD COLUMN {_col} {_type};")
            except Exception:
                pass
        cur.execute("""
            CREATE TABLE IF NOT EXISTS booking_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applicant_name TEXT,
                passport_number TEXT,
                target_city TEXT,
                visa_category TEXT,
                status TEXT,
                step_reached TEXT,
                slot_date TEXT,
                slot_time TEXT,
                reference_no TEXT,
                message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slot_monitor_settings (
                id INTEGER PRIMARY KEY,
                target_centre TEXT DEFAULT 'Bulgaria Visa Application Center ,New Delhi',
                target_category TEXT DEFAULT 'Long Stay D visa',
                check_interval_seconds INTEGER DEFAULT 30,
                telegram_bot_token TEXT DEFAULT '',
                telegram_chat_id TEXT DEFAULT '',
                telegram_enabled INTEGER DEFAULT 1,
                notify_email TEXT DEFAULT '',
                email_enabled INTEGER DEFAULT 1,
                daily_report_time TEXT DEFAULT '22:00',
                daily_report_enabled INTEGER DEFAULT 1,
                auto_book_d_visa INTEGER DEFAULT 1,
                is_running INTEGER DEFAULT 0,
                last_checked_at TEXT,
                last_status_message TEXT,
                last_found_date TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slot_checks_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                centre TEXT,
                category TEXT,
                status_text TEXT,
                is_available INTEGER DEFAULT 0,
                appointment_date TEXT,
                all_categories_json TEXT,
                notified_telegram INTEGER DEFAULT 0,
                notified_email INTEGER DEFAULT 0,
                checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Ensure seed users in SQLite
        h_admin, s_admin = hash_password("Admin@2026!")
        cur.execute("SELECT id FROM users WHERE username = 'superadmin' OR email = 'superadmin@vfsautomation.com'")
        if not cur.fetchone():
            cur.execute("INSERT INTO users (username, email, password_hash, salt, full_name, role) VALUES (?, ?, ?, ?, ?, ?)",
                        ("superadmin", "superadmin@vfsautomation.com", h_admin, s_admin, "Super Administrator", "super_admin"))

        # User 1: Vikas Bhardwaj
        h_u1, s_u1 = hash_password("Vikas@20.26")
        cur.execute("SELECT id FROM users WHERE email = 'bhardwajvikas824@gmail.com' OR username IN ('vikas', 'operator2')")
        r1 = cur.fetchone()
        if r1:
            cur.execute("""
                UPDATE users SET username = 'vikas', email = 'bhardwajvikas824@gmail.com', full_name = 'Vikas Bhardwaj',
                                 password_hash = ?, salt = ?, telegram_chat_id = '8895308694',
                                 telegram_bot_token = '8614915379:AAEauGr9wyzbf5aaqu8AZsBvXagwh6e5jco',
                                 email_provider = 'gmail', otp_email = 'bhardwajvikas824@gmail.com', role = 'user', status = 'active'
                WHERE id = ?
            """, (h_u1, s_u1, r1[0]))
        else:
            cur.execute("""
                INSERT INTO users (username, email, password_hash, salt, full_name, role, status, telegram_chat_id, telegram_bot_token, email_provider, otp_email)
                VALUES ('vikas', 'bhardwajvikas824@gmail.com', ?, ?, 'Vikas Bhardwaj', 'user', 'active', '8895308694', '8614915379:AAEauGr9wyzbf5aaqu8AZsBvXagwh6e5jco', 'gmail', 'bhardwajvikas824@gmail.com')
            """, (h_u1, s_u1))

        # User 2: Ankit Kumar
        h_u2, s_u2 = hash_password("Dev@2026")
        cur.execute("SELECT id FROM users WHERE email = 'ankit.developer2004@gmail.com' OR username IN ('ankit', 'operator1')")
        r2 = cur.fetchone()
        if r2:
            cur.execute("""
                UPDATE users SET username = 'ankit', email = 'ankit.developer2004@gmail.com', full_name = 'Ankit Kumar',
                                 password_hash = ?, salt = ?, telegram_chat_id = '7815919062',
                                 telegram_bot_token = '8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw',
                                 vfs_email = 'oli930110@gmail.com', vfs_password = 'Milan@123',
                                 vfs_email_secondary = 'ankit.developer2004@gmail.com', vfs_password_secondary = 'Dev@2026',
                                 email_provider = 'gmail', otp_email = 'mytutorankit@gmail.com', otp_app_password = 'hwzw lrzx xovu ybgs',
                                 role = 'user', status = 'active'
                WHERE id = ?
            """, (h_u2, s_u2, r2[0]))
        else:
            cur.execute("""
                INSERT INTO users (username, email, password_hash, salt, full_name, role, status, telegram_chat_id, telegram_bot_token,
                                   vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary, email_provider, otp_email, otp_app_password)
                VALUES ('ankit', 'ankit.developer2004@gmail.com', ?, ?, 'Ankit Kumar', 'user', 'active', '7815919062', '8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw',
                        'oli930110@gmail.com', 'Milan@123', 'ankit.developer2004@gmail.com', 'Dev@2026', 'gmail', 'mytutorankit@gmail.com', 'hwzw lrzx xovu ybgs')
            """, (h_u2, s_u2))

        # Seed applicant profile if empty
        cur.execute("SELECT COUNT(*) FROM applicant_profiles")
        if cur.fetchone()[0] == 0 and cfg.APPLICANT_FIRST_NAME:
            cur.execute("""
                INSERT INTO applicant_profiles (
                    profile_name, first_name, last_name, gender, dob, nationality,
                    passport_number, passport_expiry, phone, email,
                    target_city, visa_category, visa_sub_category, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                f"{cfg.APPLICANT_FIRST_NAME} {cfg.APPLICANT_LAST_NAME} (Default)",
                cfg.APPLICANT_FIRST_NAME, cfg.APPLICANT_LAST_NAME, cfg.APPLICANT_GENDER,
                cfg.APPLICANT_DOB, cfg.APPLICANT_NATIONALITY, cfg.APPLICANT_PASSPORT_NUMBER,
                cfg.APPLICANT_PASSPORT_EXPIRY, cfg.APPLICANT_PHONE, cfg.APPLICANT_EMAIL,
                cfg.TARGET_CITY, cfg.VISA_CATEGORY, cfg.VISA_SUB_CATEGORY
            ))

        # Seed slot monitor settings if empty
        cur.execute("SELECT COUNT(*) FROM slot_monitor_settings WHERE id = 1")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT INTO slot_monitor_settings (
                    id, target_centre, target_category, check_interval_seconds,
                    telegram_bot_token, telegram_chat_id, telegram_enabled,
                    notify_email, email_enabled, daily_report_time, daily_report_enabled
                ) VALUES (1, ?, ?, 30, ?, ?, 1, ?, 1, '22:00', 1)
            """, (
                "Bulgaria Visa Application Center ,New Delhi",
                "Long Stay D visa",
                cfg.TELEGRAM_BOT_TOKEN or "",
                cfg.TELEGRAM_CHAT_ID or "7815919062",
                cfg.VFS_EMAIL or ""
            ))

        # Schema migration: Add auto_book_d_visa column if missing
        try:
            cur.execute("ALTER TABLE slot_monitor_settings ADD COLUMN auto_book_d_visa INTEGER DEFAULT 1;")
        except Exception:
            pass

        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"Error ensuring SQLite db: {e}")


def get_db_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None,
    timeout: int = 8
) -> ResilientConnection:
    """Establish connection to remote MySQL database with persistent socket reuse and transparent SQLite fallback."""
    global _persistent_mysql_conn

    # Custom host/credentials requested (e.g. testing connection in admin portal):
    if host or port or user or password or database:
        try:
            raw = pymysql.connect(
                host=host or cfg.DB_HOST or "srv2203.hstgr.io",
                port=int(port or cfg.DB_PORT or 3306),
                user=user or cfg.DB_USERNAME,
                password=password or cfg.DB_PASSWORD,
                database=database or cfg.DB_DATABASE,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=timeout,
                read_timeout=timeout,
                write_timeout=timeout
            )
            return ResilientConnection(raw, is_sqlite=False)
        except Exception as e:
            log.warning(f"Direct MySQL connection attempt failed: {e}")
            raise

    # 1. Try persistent MySQL connection if not in cooldown
    global _last_mysql_fail_time
    now_ts = time.time()
    with _db_lock:
        if _persistent_mysql_conn is not None:
            try:
                _persistent_mysql_conn.ping(reconnect=True)
                return ResilientConnection(_persistent_mysql_conn, is_sqlite=False)
            except Exception as e:
                log.warning(f"Persistent MySQL connection dropped, attempting reconnect: {e}")
                try:
                    _persistent_mysql_conn.close()
                except Exception:
                    pass
                _persistent_mysql_conn = None

        if (now_ts - _last_mysql_fail_time) > 60:
            try:
                _persistent_mysql_conn = pymysql.connect(
                    host=cfg.DB_HOST or "srv2203.hstgr.io",
                    port=int(cfg.DB_PORT or 3306),
                    user=cfg.DB_USERNAME,
                    password=cfg.DB_PASSWORD,
                    database=cfg.DB_DATABASE,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True,
                    connect_timeout=timeout,
                    read_timeout=timeout,
                    write_timeout=timeout
                )
                log.info("Established persistent MySQL connection to Hostinger.")
                return ResilientConnection(_persistent_mysql_conn, is_sqlite=False)
            except Exception as e:
                _last_mysql_fail_time = now_ts
                log.warning(f"MySQL connection unavailable ({e}). Cooling down for 60s, falling back to local SQLite mirror.")

    # 2. Transparent fallback to SQLite mirror
    init_sqlite_db()
    sqlite_conn = sqlite3.connect(SQLITE_DB_PATH, timeout=10)
    sqlite_conn.row_factory = sqlite3.Row
    return ResilientConnection(sqlite_conn, is_sqlite=True)


def test_connection(
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None
) -> Dict[str, Any]:
    """Test MySQL connection and return status and server info."""
    try:
        conn = get_db_connection(host, port, user, password, database, timeout=6)
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION() AS ver, DATABASE() AS db, CURRENT_USER() AS usr")
            info = cur.fetchone()
        conn.close()
        return {
            "success": True,
            "message": "Connected successfully to remote MySQL database.",
            "version": info.get("ver"),
            "database": info.get("db"),
            "user": info.get("usr")
        }
    except Exception as e:
        log.warning(f"Database connection test failed: {e}")
        return {
            "success": False,
            "message": f"Connection failed: {str(e)}"
        }


def init_db() -> bool:
    """Ensure all required tables exist in the database and seed default accounts."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # 1. Users Table (Super Admin & Operators)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(120),
                    telegram_chat_id VARCHAR(100) NULL,
                    telegram_bot_token VARCHAR(200) NULL,
                    telegram_notifications TINYINT(1) DEFAULT 1,
                    email_notifications TINYINT(1) DEFAULT 1,
                    password_hash VARCHAR(255) NOT NULL,
                    salt VARCHAR(64) NOT NULL,
                    full_name VARCHAR(100),
                    role ENUM('super_admin', 'user') DEFAULT 'user',
                    status ENUM('active', 'inactive') DEFAULT 'active',
                    created_by VARCHAR(50) DEFAULT 'system',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_login TIMESTAMP NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 2. Applicant Profiles Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS applicant_profiles (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    profile_name VARCHAR(120) NOT NULL,
                    first_name VARCHAR(100) NOT NULL,
                    last_name VARCHAR(100) NOT NULL,
                    gender VARCHAR(20) DEFAULT 'Male',
                    dob VARCHAR(20) NOT NULL,
                    nationality VARCHAR(50) DEFAULT 'India',
                    passport_number VARCHAR(50) NOT NULL,
                    passport_expiry VARCHAR(20) NOT NULL,
                    phone VARCHAR(30) NOT NULL,
                    email VARCHAR(150) NOT NULL,
                    target_city VARCHAR(50) DEFAULT 'delhi',
                    visa_category VARCHAR(100) DEFAULT 'Business',
                    visa_sub_category VARCHAR(100) DEFAULT 'Business Visa',
                    is_active TINYINT(1) DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 3. VFS Accounts Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS vfs_accounts (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    account_name VARCHAR(100) NULL,
                    vfs_email VARCHAR(150) UNIQUE NOT NULL,
                    vfs_password VARCHAR(100) NOT NULL,
                    gmail_user VARCHAR(150) NULL,
                    gmail_app_password VARCHAR(100) NULL,
                    operator_id INT NULL,
                    status VARCHAR(50) DEFAULT 'active',
                    is_active TINYINT(1) DEFAULT 0,
                    notes TEXT NULL,
                    last_used_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    KEY idx_vfs_operator (operator_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Column migrations for existing vfs_accounts table
            for _col, _definition in [
                ("account_name", "VARCHAR(100) NULL AFTER id"),
                ("operator_id", "INT NULL AFTER gmail_app_password"),
                ("is_active", "TINYINT(1) DEFAULT 0 AFTER status"),
            ]:
                try:
                    cur.execute(f"SHOW COLUMNS FROM vfs_accounts LIKE '{_col}'")
                    if not cur.fetchone():
                        cur.execute(f"ALTER TABLE vfs_accounts ADD COLUMN {_col} {_definition}")
                except Exception as _e:
                    log.warning(f"Could not add column {_col} to vfs_accounts: {_e}")

            # 4. Booking History Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS booking_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    applicant_name VARCHAR(150),
                    passport_number VARCHAR(50),
                    target_city VARCHAR(50),
                    visa_category VARCHAR(100),
                    status VARCHAR(50),
                    step_reached VARCHAR(50),
                    slot_date VARCHAR(50),
                    slot_time VARCHAR(50),
                    reference_no VARCHAR(100),
                    message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 5. Slot Monitor Settings Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS slot_monitor_settings (
                    id INT PRIMARY KEY DEFAULT 1,
                    target_centre VARCHAR(150) DEFAULT 'Bulgaria Visa Application Center ,New Delhi',
                    target_category VARCHAR(100) DEFAULT 'Long Stay D visa',
                    check_interval_seconds INT DEFAULT 30,
                    telegram_bot_token VARCHAR(200) DEFAULT '',
                    telegram_chat_id VARCHAR(100) DEFAULT '',
                    telegram_enabled TINYINT(1) DEFAULT 1,
                    notify_email VARCHAR(150) DEFAULT '',
                    email_enabled TINYINT(1) DEFAULT 1,
                    daily_report_time VARCHAR(10) DEFAULT '22:00',
                    daily_report_enabled TINYINT(1) DEFAULT 1,
                    is_running TINYINT(1) DEFAULT 0,
                    last_checked_at VARCHAR(50) NULL,
                    last_status_message TEXT NULL,
                    last_found_date VARCHAR(100) NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # 6. Slot Checks History Table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS slot_checks_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    centre VARCHAR(150),
                    category VARCHAR(100),
                    status_text VARCHAR(150),
                    is_available TINYINT(1) DEFAULT 0,
                    appointment_date VARCHAR(100) NULL,
                    all_categories_json TEXT NULL,
                    notified_telegram TINYINT(1) DEFAULT 0,
                    notified_email TINYINT(1) DEFAULT 0,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

            # Schema Migration: Add telegram and credential columns to users if missing
            for _col, _definition in [
                ("telegram_chat_id", "VARCHAR(100) NULL AFTER email"),
                ("telegram_notifications", "TINYINT(1) DEFAULT 1 AFTER telegram_chat_id"),
                ("email_notifications", "TINYINT(1) DEFAULT 1 AFTER telegram_notifications"),
                ("telegram_bot_token", "VARCHAR(200) NULL AFTER telegram_chat_id"),
                ("vfs_email", "VARCHAR(150) NULL AFTER email_notifications"),
                ("vfs_password", "VARCHAR(150) NULL AFTER vfs_email"),
                ("vfs_email_secondary", "VARCHAR(150) NULL AFTER vfs_password"),
                ("vfs_password_secondary", "VARCHAR(150) NULL AFTER vfs_email_secondary"),
                ("email_provider", "VARCHAR(50) DEFAULT 'gmail' AFTER vfs_password_secondary"),
                ("otp_email", "VARCHAR(150) NULL AFTER email_provider"),
                ("otp_app_password", "VARCHAR(150) NULL AFTER otp_email"),
                ("otp_imap_host", "VARCHAR(120) NULL AFTER otp_app_password"),
                ("otp_imap_port", "INT DEFAULT 993 AFTER otp_imap_host"),
                ("otp_smtp_host", "VARCHAR(120) NULL AFTER otp_imap_port"),
                ("otp_smtp_port", "INT DEFAULT 587 AFTER otp_smtp_host"),
            ]:
                try:
                    cur.execute(f"SHOW COLUMNS FROM users LIKE '{_col}'")
                    if not cur.fetchone():
                        cur.execute(f"ALTER TABLE users ADD COLUMN {_col} {_definition};")
                        log.info(f"Migrated users table with {_col} column.")
                except Exception as _e:
                    log.debug(f"User column migration note ({_col}): {_e}")

            # Schema Migration: Add auto_book_d_visa to slot_monitor_settings if missing
            try:
                cur.execute("SHOW COLUMNS FROM slot_monitor_settings LIKE 'auto_book_d_visa'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE slot_monitor_settings ADD COLUMN auto_book_d_visa TINYINT(1) DEFAULT 1 AFTER daily_report_enabled;")
                    log.info("Migrated slot_monitor_settings table with auto_book_d_visa column.")
            except Exception as e:
                log.debug(f"slot_monitor_settings migration note: {e}")

            # Ensure row 1 in slot_monitor_settings exists
            cur.execute("SELECT id FROM slot_monitor_settings WHERE id = 1")
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO slot_monitor_settings (
                        id, target_centre, target_category, check_interval_seconds,
                        telegram_bot_token, telegram_chat_id, telegram_enabled,
                        notify_email, email_enabled, daily_report_time, daily_report_enabled
                    ) VALUES (
                        1, 'Bulgaria Visa Application Center ,New Delhi', 'Long Stay D visa', 30,
                        %s, %s, 1,
                        %s, 1, '22:00', 1
                    )
                """, (
                    os.getenv("TELEGRAM_BOT_TOKEN", ""),
                    os.getenv("TELEGRAM_CHAT_ID", ""),
                    "mytutorankit@gmail.com"
                ))
            else:
                cur.execute("UPDATE slot_monitor_settings SET notify_email = 'mytutorankit@gmail.com' WHERE id = 1")

            # -------------------------------------------------------------
            # SEED / SYNC USERS: 1 Super Admin & 2 Operators
            # -------------------------------------------------------------
            # Super Admin
            cur.execute("SELECT id FROM users WHERE username = 'superadmin' OR email = 'superadmin@vfsautomation.com'")
            if not cur.fetchone():
                p_hash, p_salt = hash_password("Admin@2026!")
                cur.execute("""
                    INSERT INTO users (username, email, password_hash, salt, full_name, role, status, created_by)
                    VALUES ('superadmin', 'superadmin@vfsautomation.com', %s, %s, 'Super Administrator', 'super_admin', 'active', 'system')
                """, (p_hash, p_salt))

            # User 1: Vikas Bhardwaj (bhardwajvikas824@gmail.com / Vikas@20.26)
            u1_hash, u1_salt = hash_password("Vikas@20.26")
            cur.execute("SELECT id FROM users WHERE email = 'bhardwajvikas824@gmail.com' OR username IN ('vikas', 'operator2')")
            r1 = cur.fetchone()
            if r1:
                cur.execute("""
                    UPDATE users SET 
                        username = 'vikas', email = 'bhardwajvikas824@gmail.com', full_name = 'Vikas Bhardwaj',
                        password_hash = %s, salt = %s, telegram_chat_id = '8895308694',
                        telegram_bot_token = '8614915379:AAEauGr9wyzbf5aaqu8AZsBvXagwh6e5jco',
                        email_provider = 'gmail', otp_email = 'bhardwajvikas824@gmail.com',
                        role = 'user', status = 'active'
                    WHERE id = %s
                """, (u1_hash, u1_salt, r1["id"]))
            else:
                cur.execute("""
                    INSERT INTO users (username, email, password_hash, salt, full_name, role, status, telegram_chat_id, telegram_bot_token, email_provider, otp_email, created_by)
                    VALUES ('vikas', 'bhardwajvikas824@gmail.com', %s, %s, 'Vikas Bhardwaj', 'user', 'active', '8895308694', '8614915379:AAEauGr9wyzbf5aaqu8AZsBvXagwh6e5jco', 'gmail', 'bhardwajvikas824@gmail.com', 'system')
                """, (u1_hash, u1_salt))

            # User 2: Ankit Kumar (ankit.developer2004@gmail.com / Dev@2026)
            u2_hash, u2_salt = hash_password("Dev@2026")
            cur.execute("SELECT id FROM users WHERE email = 'ankit.developer2004@gmail.com' OR username IN ('ankit', 'operator1')")
            r2 = cur.fetchone()
            if r2:
                cur.execute("""
                    UPDATE users SET 
                        username = 'ankit', email = 'ankit.developer2004@gmail.com', full_name = 'Ankit Kumar',
                        password_hash = %s, salt = %s, telegram_chat_id = '7815919062',
                        telegram_bot_token = '8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw',
                        vfs_email = 'oli930110@gmail.com', vfs_password = 'Milan@123',
                        vfs_email_secondary = 'ankit.developer2004@gmail.com', vfs_password_secondary = 'Dev@2026',
                        email_provider = 'gmail', otp_email = 'mytutorankit@gmail.com', otp_app_password = 'hwzw lrzx xovu ybgs',
                        role = 'user', status = 'active'
                    WHERE id = %s
                """, (u2_hash, u2_salt, r2["id"]))
            else:
                cur.execute("""
                    INSERT INTO users (username, email, password_hash, salt, full_name, role, status, telegram_chat_id, telegram_bot_token,
                                       vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary, email_provider, otp_email, otp_app_password, created_by)
                    VALUES ('ankit', 'ankit.developer2004@gmail.com', %s, %s, 'Ankit Kumar', 'user', 'active', '7815919062', '8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw',
                            'oli930110@gmail.com', 'Milan@123', 'ankit.developer2004@gmail.com', 'Dev@2026', 'gmail', 'mytutorankit@gmail.com', 'hwzw lrzx xovu ybgs', 'system')
                """, (u2_hash, u2_salt))

            # Check if default profile exists, if not insert current config
            cur.execute("SELECT COUNT(*) AS cnt FROM applicant_profiles")
            res = cur.fetchone()
            if res and res["cnt"] == 0 and cfg.APPLICANT_FIRST_NAME:
                cur.execute("""
                    INSERT INTO applicant_profiles (
                        profile_name, first_name, last_name, gender, dob, nationality,
                        passport_number, passport_expiry, phone, email,
                        target_city, visa_category, visa_sub_category, is_active
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1
                    )
                """, (
                    f"{cfg.APPLICANT_FIRST_NAME} {cfg.APPLICANT_LAST_NAME} (Default)",
                    cfg.APPLICANT_FIRST_NAME,
                    cfg.APPLICANT_LAST_NAME,
                    cfg.APPLICANT_GENDER,
                    cfg.APPLICANT_DOB,
                    cfg.APPLICANT_NATIONALITY,
                    cfg.APPLICANT_PASSPORT_NUMBER,
                    cfg.APPLICANT_PASSPORT_EXPIRY,
                    cfg.APPLICANT_PHONE,
                    cfg.APPLICANT_EMAIL,
                    cfg.TARGET_CITY,
                    cfg.VISA_CATEGORY,
                    cfg.VISA_SUB_CATEGORY
                ))

            # Sync default VFS account if vfs_accounts table is empty or update active
            cur.execute("SELECT id FROM vfs_accounts WHERE vfs_email = 'oli930110@gmail.com'")
            oli_row = cur.fetchone()
            if not oli_row:
                cur.execute("""
                    INSERT INTO vfs_accounts (account_name, vfs_email, vfs_password, gmail_user, gmail_app_password, status, is_active)
                    VALUES (%s, %s, %s, %s, %s, 'active', 1)
                """, ("Milan - Bulgaria VFS", "oli930110@gmail.com", "Milan@123", "mytutorankit@gmail.com", "hwzw lrzx xovu ybgs"))
            else:
                cur.execute("""
                    UPDATE vfs_accounts 
                    SET vfs_password = 'Milan@123', gmail_user = 'mytutorankit@gmail.com', gmail_app_password = 'hwzw lrzx xovu ybgs', is_active = 1
                    WHERE vfs_email = 'oli930110@gmail.com'
                """)

            # Ensure only one account is marked is_active = 1
            cur.execute("SELECT id FROM vfs_accounts WHERE is_active = 1 LIMIT 1")
            act_row = cur.fetchone()
            if not act_row:
                cur.execute("UPDATE vfs_accounts SET is_active = 1 WHERE vfs_email = 'oli930110@gmail.com'")

        conn.close()
        log.info("Database initialized successfully with all tables and seeded users.")
        return True
    except Exception as e:
        log.warning(f"Could not initialize database: {e}")
        return False


# =============================================================================
# USER MANAGEMENT FUNCTIONS (Super Admin Control)
# =============================================================================

def authenticate_user(username: str, password: str) -> Optional[Dict[str, Any]]:
    """Verify username & password, returns user dict on success or None.
    Supports login via username or email, plus operator aliases."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            u_clean = username.strip()
            cur.execute("""
                SELECT * FROM users 
                WHERE LOWER(username) = LOWER(%s) 
                   OR LOWER(email) = LOWER(%s)
                   OR (LOWER(%s) = 'operator1' AND (LOWER(username) = 'ankit' OR id = 2))
                   OR (LOWER(%s) = 'operator2' AND (LOWER(username) = 'vikas' OR id = 3))
                LIMIT 1
            """, (u_clean, u_clean, u_clean, u_clean))
            user = cur.fetchone()
        conn.close()

        if not user:
            return None

        if user.get("status") != "active":
            log.warning(f"Login denied for inactive user: {username}")
            return None

        if verify_password(password, user["password_hash"], user["salt"]):
            update_last_login(user["id"])
            return user
        return None
    except Exception as e:
        log.error(f"Authentication error: {e}")
        return None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    """Fetch user by ID with complete profile and credentials."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, full_name, role, status,
                       vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary,
                       email_provider, otp_email, otp_app_password, otp_imap_host, otp_imap_port, otp_smtp_host, otp_smtp_port,
                       telegram_chat_id, telegram_bot_token, telegram_notifications, email_notifications,
                       created_by, created_at, last_login 
                FROM users WHERE id = %s
            """, (user_id,))
            user = cur.fetchone()
        conn.close()
        return user
    except Exception as e:
        log.error(f"Error getting user by id {user_id}: {e}")
        return None


def get_all_users() -> List[Dict[str, Any]]:
    """Retrieve all users list with complete professional details for Super Admin control."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, full_name, role, status,
                       vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary,
                       email_provider, otp_email, otp_app_password, otp_imap_host, otp_imap_port, otp_smtp_host, otp_smtp_port,
                       telegram_chat_id, telegram_bot_token, telegram_notifications, email_notifications,
                       created_by, created_at, last_login 
                FROM users 
                ORDER BY role DESC, id ASC
            """)
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Error fetching users: {e}")
        return []


def get_user_credentials(user_id: int) -> Optional[Dict[str, Any]]:
    """Get VFS, Email OTP (Gmail/Hostinger), and Telegram credentials for a specific user."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, full_name, role, status,
                       vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary,
                       email_provider, otp_email, otp_app_password, otp_imap_host, otp_imap_port, otp_smtp_host, otp_smtp_port,
                       telegram_chat_id, telegram_bot_token, telegram_notifications, email_notifications
                FROM users WHERE id = %s
            """, (user_id,))
            user = cur.fetchone()
        conn.close()
        return user
    except Exception as e:
        log.error(f"Error fetching user credentials for #{user_id}: {e}")
        return None


def update_user_credentials(user_id: int, data: Dict[str, Any]) -> Tuple[bool, str]:
    """Update VFS, Email OTP provider (Gmail/Hostinger), and Telegram credentials for a user."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            updates = []
            params = []
            
            allowed_fields = [
                "vfs_email", "vfs_password", "vfs_email_secondary", "vfs_password_secondary",
                "email_provider", "otp_email", "otp_app_password", "otp_imap_host", "otp_imap_port",
                "otp_smtp_host", "otp_smtp_port", "telegram_chat_id", "telegram_bot_token"
            ]
            for fld in allowed_fields:
                if fld in data:
                    updates.append(f"{fld} = %s")
                    val = data[fld]
                    if val is not None and isinstance(val, str):
                        val = val.strip()
                    params.append(val if val != "" else None)

            for bool_fld in ["telegram_notifications", "email_notifications"]:
                if bool_fld in data:
                    updates.append(f"{bool_fld} = %s")
                    params.append(1 if data[bool_fld] else 0)

            if not updates:
                conn.close()
                return True, "No credential changes provided."

            params.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", params)
        conn.close()
        return True, "User credentials updated successfully."
    except Exception as e:
        log.error(f"Error updating user credentials for #{user_id}: {e}")
        return False, str(e)


def update_user_full(user_id: int, data: Dict[str, Any]) -> Tuple[bool, str]:
    """Super Admin full edit of user details, role, status, and all credentials."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            updates = []
            params = []

            for col in [
                "username", "full_name", "email", "role", "status",
                "vfs_email", "vfs_password", "vfs_email_secondary", "vfs_password_secondary",
                "email_provider", "otp_email", "otp_app_password", "otp_imap_host", "otp_imap_port",
                "otp_smtp_host", "otp_smtp_port", "telegram_chat_id", "telegram_bot_token"
            ]:
                if col in data and data[col] is not None:
                    val = str(data[col]).strip()
                    updates.append(f"{col} = %s")
                    params.append(val if val != "" else None)

            for bool_col in ["telegram_notifications", "email_notifications"]:
                if bool_col in data:
                    updates.append(f"{bool_col} = %s")
                    params.append(1 if data[bool_col] else 0)

            # Optional password update
            if data.get("password"):
                p_hash, p_salt = hash_password(str(data["password"]))
                updates.append("password_hash = %s")
                updates.append("salt = %s")
                params.extend([p_hash, p_salt])

            if not updates:
                conn.close()
                return True, "No changes specified."

            params.append(user_id)
            cur.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = %s", params)
        conn.close()
        return True, "User profile and credentials updated successfully."
    except Exception as e:
        log.error(f"Error in update_user_full for #{user_id}: {e}")
        return False, str(e)


def create_user(
    username: str,
    password: str,
    full_name: str = "",
    email: str = "",
    role: str = "user",
    created_by: str = "super_admin",
    vfs_email: str = "",
    vfs_password: str = "",
    vfs_email_secondary: str = "",
    vfs_password_secondary: str = "",
    email_provider: str = "gmail",
    otp_email: str = "",
    otp_app_password: str = "",
    telegram_chat_id: str = "",
    telegram_bot_token: str = "",
) -> Tuple[bool, str, Optional[int]]:
    """Create a new user in MySQL/SQLite with complete credentials. Callable by Super Admin."""
    username = username.strip()
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters long.", None
    if not password or len(password) < 6:
        return False, "Password must be at least 6 characters long.", None
    if role not in ("super_admin", "user"):
        role = "user"

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # Check existing
            cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s) OR (email != '' AND LOWER(email) = LOWER(%s))", (username, email.strip()))
            if cur.fetchone():
                conn.close()
                return False, f"Username '{username}' or email '{email}' already exists.", None

            p_hash, p_salt = hash_password(password)
            cur.execute("""
                INSERT INTO users (
                    username, email, password_hash, salt, full_name, role, status, created_by,
                    vfs_email, vfs_password, vfs_email_secondary, vfs_password_secondary,
                    email_provider, otp_email, otp_app_password, telegram_chat_id, telegram_bot_token
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                username, email.strip() or None, p_hash, p_salt, full_name.strip() or username, role, created_by,
                vfs_email.strip() or None, vfs_password.strip() or None,
                vfs_email_secondary.strip() or None, vfs_password_secondary.strip() or None,
                email_provider.strip() or "gmail", otp_email.strip() or None, otp_app_password.strip() or None,
                telegram_chat_id.strip() or None, telegram_bot_token.strip() or None
            ))
            new_id = cur.lastrowid
        conn.close()
        return True, f"User '{username}' created successfully.", new_id
    except Exception as e:
        log.error(f"Error creating user: {e}")
        return False, str(e), None


def update_user_status(user_id: int, status: str) -> bool:
    """Activate or deactivate a user account."""
    if status not in ("active", "inactive"):
        return False
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET status = %s WHERE id = %s", (status, user_id))
        conn.close()
        return True
    except Exception as e:
        log.error(f"Error updating user status: {e}")
        return False


def update_user_password(user_id: int, new_password: str) -> Tuple[bool, str]:
    """Reset password for a user."""
    if not new_password or len(new_password) < 6:
        return False, "Password must be at least 6 characters."
    try:
        p_hash, p_salt = hash_password(new_password)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, salt = %s WHERE id = %s",
                (p_hash, p_salt, user_id)
            )
        conn.close()
        return True, "Password updated successfully."
    except Exception as e:
        log.error(f"Error updating password: {e}")
        return False, str(e)


def delete_user(user_id: int) -> Tuple[bool, str]:
    """Delete a user account. Cannot delete the last super admin."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT role FROM users WHERE id = %s", (user_id,))
            target = cur.fetchone()
            if not target:
                conn.close()
                return False, "User not found."

            if target["role"] == "super_admin":
                cur.execute("SELECT COUNT(*) AS cnt FROM users WHERE role = 'super_admin'")
                adm_cnt = cur.fetchone()["cnt"]
                if adm_cnt <= 1:
                    conn.close()
                    return False, "Cannot delete the only Super Admin account."

            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.close()
        return True, "User deleted successfully."
    except Exception as e:
        log.error(f"Error deleting user: {e}")
        return False, str(e)


def update_last_login(user_id: int):
    """Timestamp a user's successful login."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user_id,))
        conn.close()
    except Exception:
        pass


# =============================================================================
# APPLICANT & BOOKING FUNCTIONS
# =============================================================================

def get_all_applicants() -> List[Dict[str, Any]]:
    """Retrieve all applicant profiles from database."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM applicant_profiles ORDER BY id DESC")
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.warning(f"Error fetching applicant profiles: {e}")
        return []


def get_active_applicant() -> Optional[Dict[str, Any]]:
    """Retrieve currently active profile from MySQL, or first profile."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM applicant_profiles WHERE is_active = 1 LIMIT 1")
            row = cur.fetchone()
            if not row:
                cur.execute("SELECT * FROM applicant_profiles ORDER BY id ASC LIMIT 1")
                row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        log.warning(f"Error fetching active applicant: {e}")
        return None


def select_applicant(applicant_id: int) -> Optional[Dict[str, Any]]:
    """Mark given profile active and synchronize with active memory config."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE applicant_profiles SET is_active = 0")
            cur.execute("UPDATE applicant_profiles SET is_active = 1 WHERE id = %s", (applicant_id,))
            cur.execute("SELECT * FROM applicant_profiles WHERE id = %s", (applicant_id,))
            profile = cur.fetchone()
        conn.close()

        if profile:
            cfg.APPLICANT_FIRST_NAME = profile["first_name"]
            cfg.APPLICANT_LAST_NAME = profile["last_name"]
            cfg.APPLICANT_GENDER = profile["gender"]
            cfg.APPLICANT_DOB = profile["dob"]
            cfg.APPLICANT_NATIONALITY = profile["nationality"]
            cfg.APPLICANT_PASSPORT_NUMBER = profile["passport_number"]
            cfg.APPLICANT_PASSPORT_EXPIRY = profile["passport_expiry"]
            cfg.APPLICANT_PHONE = profile["phone"]
            cfg.APPLICANT_EMAIL = profile["email"]
            if profile.get("target_city"):
                cfg.TARGET_CITY = profile["target_city"]
            if profile.get("visa_category"):
                cfg.VISA_CATEGORY = profile["visa_category"]
            if profile.get("visa_sub_category"):
                cfg.VISA_SUB_CATEGORY = profile["visa_sub_category"]

            cfg.save_applicants([{
                "first_name": profile["first_name"],
                "last_name": profile["last_name"],
                "gender": profile["gender"],
                "dob": profile["dob"],
                "nationality": profile["nationality"],
                "passport_number": profile["passport_number"],
                "passport_expiry": profile["passport_expiry"],
                "phone": profile["phone"],
                "email": profile["email"]
            }])
            return profile
        return None
    except Exception as e:
        log.error(f"Error selecting applicant #{applicant_id}: {e}")
        return None


def save_applicant_profile(profile_data: Dict[str, Any]) -> Tuple[bool, str, Optional[int]]:
    """Save or update an applicant profile into MySQL."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            p_id = profile_data.get("id")
            first_name = profile_data.get("first_name", "").strip()
            last_name = profile_data.get("last_name", "").strip()
            profile_name = profile_data.get("profile_name") or f"{first_name} {last_name}".strip() or "Unnamed Profile"

            if p_id:
                cur.execute("""
                    UPDATE applicant_profiles SET
                        profile_name = %s, first_name = %s, last_name = %s, gender = %s,
                        dob = %s, nationality = %s, passport_number = %s, passport_expiry = %s,
                        phone = %s, email = %s, target_city = %s, visa_category = %s, visa_sub_category = %s
                    WHERE id = %s
                """, (
                    profile_name, first_name, last_name,
                    profile_data.get("gender", "Male"),
                    profile_data.get("dob", ""),
                    profile_data.get("nationality", "India"),
                    profile_data.get("passport_number", ""),
                    profile_data.get("passport_expiry", ""),
                    profile_data.get("phone", ""),
                    profile_data.get("email", ""),
                    profile_data.get("target_city", cfg.TARGET_CITY),
                    profile_data.get("visa_category", cfg.VISA_CATEGORY),
                    profile_data.get("visa_sub_category", cfg.VISA_SUB_CATEGORY),
                    p_id
                ))
                saved_id = int(p_id)
            else:
                cur.execute("""
                    INSERT INTO applicant_profiles (
                        profile_name, first_name, last_name, gender, dob, nationality,
                        passport_number, passport_expiry, phone, email,
                        target_city, visa_category, visa_sub_category, is_active
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0)
                """, (
                    profile_name, first_name, last_name,
                    profile_data.get("gender", "Male"),
                    profile_data.get("dob", ""),
                    profile_data.get("nationality", "India"),
                    profile_data.get("passport_number", ""),
                    profile_data.get("passport_expiry", ""),
                    profile_data.get("phone", ""),
                    profile_data.get("email", ""),
                    profile_data.get("target_city", cfg.TARGET_CITY),
                    profile_data.get("visa_category", cfg.VISA_CATEGORY),
                    profile_data.get("visa_sub_category", cfg.VISA_SUB_CATEGORY)
                ))
                saved_id = cur.lastrowid
        conn.close()
        return True, "Profile saved to MySQL successfully.", saved_id
    except Exception as e:
        log.error(f"Error saving applicant profile: {e}")
        return False, str(e), None


def delete_applicant(applicant_id: int) -> bool:
    """Delete an applicant profile from MySQL."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM applicant_profiles WHERE id = %s", (applicant_id,))
        conn.close()
        return True
    except Exception as e:
        log.error(f"Error deleting applicant profile: {e}")
        return False


def log_booking_result(
    applicant_name: str,
    passport_number: str,
    target_city: str,
    visa_category: str,
    status: str,
    step_reached: str,
    slot_date: str = "",
    slot_time: str = "",
    reference_no: str = "",
    message: str = ""
):
    """Record an automation booking attempt or confirmation in MySQL."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO booking_history (
                    applicant_name, passport_number, target_city, visa_category,
                    status, step_reached, slot_date, slot_time, reference_no, message
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                applicant_name,
                passport_number,
                target_city,
                visa_category,
                status,
                step_reached,
                slot_date,
                slot_time,
                reference_no,
                message
            ))
        conn.close()
    except Exception as e:
        log.debug(f"Could not log booking to database: {e}")


def get_booking_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch booking attempts history from MySQL."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM booking_history ORDER BY id DESC LIMIT %s", (limit,))
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.warning(f"Error fetching booking history: {e}")
        return []


# =============================================================================
# VFS ACCOUNTS & GMAIL OTP MANAGEMENT (Super Admin & Operators)
# =============================================================================

def get_all_vfs_accounts(operator_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieve VFS accounts. If operator_id is specified, returns assigned + shared accounts."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if operator_id is not None:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    WHERE a.operator_id = %s OR a.operator_id IS NULL
                    ORDER BY a.is_active DESC, a.id ASC
                """, (operator_id,))
            else:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    ORDER BY a.is_active DESC, a.id ASC
                """)
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.warning(f"Error fetching VFS accounts: {e}")
        return []


def get_vfs_account_by_id(account_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single VFS account by ID with operator metadata."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                FROM vfs_accounts a
                LEFT JOIN users u ON a.operator_id = u.id
                WHERE a.id = %s
            """, (account_id,))
            row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        log.warning(f"Error fetching VFS account #{account_id}: {e}")
        return None


def get_active_vfs_account(operator_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Retrieve currently active VFS account for automation runs."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            row = None
            if operator_id is not None:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    WHERE (a.operator_id = %s OR a.operator_id IS NULL) AND a.is_active = 1 AND a.status = 'active'
                    LIMIT 1
                """, (operator_id,))
                row = cur.fetchone()
            if not row:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    WHERE a.is_active = 1 AND a.status = 'active'
                    LIMIT 1
                """)
                row = cur.fetchone()
            if not row:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    WHERE a.status = 'active'
                    ORDER BY a.id ASC LIMIT 1
                """)
                row = cur.fetchone()
            if not row:
                cur.execute("""
                    SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                    FROM vfs_accounts a
                    LEFT JOIN users u ON a.operator_id = u.id
                    ORDER BY a.id ASC LIMIT 1
                """)
                row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        log.warning(f"Error fetching active VFS account: {e}")
        return None


def select_active_vfs_account(account_id: int, operator_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Mark an account as the system/operator active account for automation."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if operator_id is not None:
                cur.execute(
                    "SELECT id FROM vfs_accounts WHERE id = %s AND (operator_id = %s OR operator_id IS NULL)",
                    (account_id, operator_id)
                )
                if not cur.fetchone():
                    conn.close()
                    return None
            cur.execute("UPDATE vfs_accounts SET is_active = 0")
            if getattr(conn, "is_sqlite", False):
                cur.execute("UPDATE vfs_accounts SET is_active = 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?", (account_id,))
            else:
                cur.execute("UPDATE vfs_accounts SET is_active = 1, last_used_at = NOW() WHERE id = %s", (account_id,))
            cur.execute("""
                SELECT a.*, u.username AS operator_username, u.full_name AS operator_name
                FROM vfs_accounts a
                LEFT JOIN users u ON a.operator_id = u.id
                WHERE a.id = %s
            """, (account_id,))
            account = cur.fetchone()
        conn.close()

        if account:
            from config import cfg
            cfg.VFS_EMAIL = account.get("vfs_email") or ""
            cfg.VFS_PASSWORD = account.get("vfs_password") or ""
            cfg.VFS_GMAIL_USER = account.get("gmail_user") or ""
            cfg.VFS_GMAIL_APP_PASSWORD = account.get("gmail_app_password") or ""
        return account
    except Exception as e:
        log.error(f"Error selecting active VFS account #{account_id}: {e}")
        return None


def save_vfs_account(
    account_data: dict,
    current_user_id: Optional[int] = None,
    is_admin: bool = False
) -> Tuple[bool, str, Optional[int]]:
    """Create or update a VFS account in the database."""
    vfs_email = str(account_data.get("vfs_email", "")).strip().lower()
    vfs_password = str(account_data.get("vfs_password", "")).strip()
    gmail_user = str(account_data.get("gmail_user", "")).strip().lower()
    gmail_app_password = str(account_data.get("gmail_app_password", "")).strip()
    account_name = str(account_data.get("account_name", "")).strip() or vfs_email
    status = str(account_data.get("status", "active")).strip().lower() or "active"
    notes = str(account_data.get("notes", "")).strip()

    raw_op = account_data.get("operator_id")
    if raw_op in ("", None, "null", "None"):
        operator_id = None
    else:
        try:
            operator_id = int(raw_op)
        except (ValueError, TypeError):
            operator_id = None

    if not is_admin and current_user_id is not None:
        operator_id = current_user_id

    if not vfs_email:
        return False, "VFS email address is required.", None
    if not vfs_password:
        return False, "VFS password is required.", None

    account_id = account_data.get("id")
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if account_id:
                if not is_admin and current_user_id is not None:
                    cur.execute("SELECT operator_id FROM vfs_accounts WHERE id = %s", (account_id,))
                    existing = cur.fetchone()
                    if existing and existing.get("operator_id") not in (None, current_user_id):
                        conn.close()
                        return False, "You do not have permission to modify this account.", None

                cur.execute("""
                    UPDATE vfs_accounts 
                    SET account_name = %s, vfs_email = %s, vfs_password = %s,
                        gmail_user = %s, gmail_app_password = %s, operator_id = %s,
                        status = %s, notes = %s
                    WHERE id = %s
                """, (
                    account_name, vfs_email, vfs_password,
                    gmail_user, gmail_app_password, operator_id,
                    status, notes, account_id
                ))
                ret_id = int(account_id)
            else:
                cur.execute("""
                    INSERT INTO vfs_accounts 
                    (account_name, vfs_email, vfs_password, gmail_user, gmail_app_password, operator_id, status, is_active, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 0, %s)
                    ON DUPLICATE KEY UPDATE
                        account_name = VALUES(account_name),
                        vfs_password = VALUES(vfs_password),
                        gmail_user = VALUES(gmail_user),
                        gmail_app_password = VALUES(gmail_app_password),
                        operator_id = VALUES(operator_id),
                        status = VALUES(status),
                        notes = VALUES(notes)
                """, (
                    account_name, vfs_email, vfs_password,
                    gmail_user, gmail_app_password, operator_id,
                    status, notes
                ))
                ret_id = getattr(cur, "lastrowid", None) or 1

            # Ensure at least one account is marked active
            cur.execute("SELECT COUNT(*) AS cnt FROM vfs_accounts WHERE is_active = 1")
            act_cnt = cur.fetchone()["cnt"]
            if act_cnt == 0:
                cur.execute("UPDATE vfs_accounts SET is_active = 1 WHERE id = %s", (ret_id,))

        conn.close()
        return True, "VFS account saved successfully.", ret_id
    except Exception as e:
        log.error(f"Error saving VFS account: {e}")
        return False, str(e), None


def delete_vfs_account(
    account_id: int,
    current_user_id: Optional[int] = None,
    is_admin: bool = False
) -> Tuple[bool, str]:
    """Delete a VFS account by ID with access control."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM vfs_accounts WHERE id = %s", (account_id,))
            acc = cur.fetchone()
            if not acc:
                conn.close()
                return False, "VFS account not found."
            if not is_admin and current_user_id is not None and acc.get("operator_id") != current_user_id:
                conn.close()
                return False, "You do not have permission to delete this account."

            was_active = acc.get("is_active", 0) == 1
            cur.execute("DELETE FROM vfs_accounts WHERE id = %s", (account_id,))

            if was_active:
                cur.execute("UPDATE vfs_accounts SET is_active = 1 WHERE status = 'active' ORDER BY id ASC LIMIT 1")
        conn.close()
        return True, "VFS account deleted successfully."
    except Exception as e:
        log.error(f"Error deleting VFS account #{account_id}: {e}")
        return False, str(e)


def test_gmail_imap_credentials(gmail_user: str, gmail_app_password: str) -> Tuple[bool, str]:
    """Test connection and authentication to Gmail IMAP server using user and app password."""
    import imaplib
    user = (gmail_user or "").strip()
    pwd = (gmail_app_password or "").strip().replace(" ", "")
    if not user or not pwd:
        return False, "Gmail address and 16-character App Password are required."
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=12)
        mail.login(user, pwd)
        status, messages = mail.select("INBOX", readonly=True)
        count = 0
        if status == "OK" and messages and messages[0]:
            count = int(messages[0].decode() if isinstance(messages[0], bytes) else messages[0])
        mail.logout()
        return True, f"Gmail IMAP connection verified! INBOX has {count:,} messages ready for OTP retrieval."
    except imaplib.IMAP4.error as ex:
        return False, f"IMAP authentication failed: Invalid credentials or App Password rejected by Gmail ({ex})."
    except Exception as ex:
        return False, f"IMAP connection failed: {ex}"


# =============================================================================
# SUPER ADMIN DATABASE INSPECTOR & OPERATIONS
# =============================================================================

ALLOWED_TABLES = ["users", "applicant_profiles", "booking_history", "vfs_accounts", "slot_monitor_settings", "slot_checks_history"]


def get_slot_monitor_settings() -> Dict[str, Any]:
    """Retrieve slot monitoring configuration."""
    default_settings = {
        "id": 1,
        "target_centre": "Bulgaria Visa Application Center ,New Delhi",
        "target_category": "Long Stay D visa",
        "check_interval_seconds": 30,
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
        "telegram_enabled": 1,
        "notify_email": cfg.APPLICANT_EMAIL or cfg.VFS_EMAIL or "",
        "email_enabled": 1,
        "daily_report_time": "22:00",
        "daily_report_enabled": 1,
        "auto_book_d_visa": 1,
        "is_running": 0,
        "last_checked_at": None,
        "last_status_message": None,
        "last_found_date": None
    }
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM slot_monitor_settings WHERE id = 1")
            row = cur.fetchone()
        conn.close()
        if row:
            # Merge with defaults
            default_settings.update(row)
        return default_settings
    except Exception as e:
        log.warning(f"Error reading slot_monitor_settings: {e}")
        return default_settings


def update_slot_monitor_settings(data: Dict[str, Any]) -> Tuple[bool, str]:
    """Update slot monitor settings in MySQL/SQLite."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            try:
                cur.execute("""
                    UPDATE slot_monitor_settings SET
                        target_centre = %s,
                        target_category = %s,
                        check_interval_seconds = %s,
                        telegram_bot_token = %s,
                        telegram_chat_id = %s,
                        telegram_enabled = %s,
                        notify_email = %s,
                        email_enabled = %s,
                        daily_report_time = %s,
                        daily_report_enabled = %s,
                        auto_book_d_visa = %s
                    WHERE id = 1
                """, (
                    data.get("target_centre", "Bulgaria Visa Application Center ,New Delhi"),
                    data.get("target_category", "Long Stay D visa"),
                    int(data.get("check_interval_seconds", 30)),
                    data.get("telegram_bot_token", "").strip(),
                    str(data.get("telegram_chat_id", "")).strip(),
                    1 if str(data.get("telegram_enabled", "true")).lower() in ("true", "1") else 0,
                    data.get("notify_email", "").strip(),
                    1 if str(data.get("email_enabled", "true")).lower() in ("true", "1") else 0,
                    data.get("daily_report_time", "22:00").strip(),
                    1 if str(data.get("daily_report_enabled", "true")).lower() in ("true", "1") else 0,
                    1 if str(data.get("auto_book_d_visa", "true")).lower() in ("true", "1") else 0
                ))
            except Exception:
                # Add column if not present in remote MySQL, then execute
                try:
                    cur.execute("ALTER TABLE slot_monitor_settings ADD COLUMN auto_book_d_visa TINYINT(1) DEFAULT 1;")
                except Exception:
                    pass
                cur.execute("""
                    UPDATE slot_monitor_settings SET
                        target_centre = %s,
                        target_category = %s,
                        check_interval_seconds = %s,
                        telegram_bot_token = %s,
                        telegram_chat_id = %s,
                        telegram_enabled = %s,
                        notify_email = %s,
                        email_enabled = %s,
                        daily_report_time = %s,
                        daily_report_enabled = %s
                    WHERE id = 1
                """, (
                    data.get("target_centre", "Bulgaria Visa Application Center ,New Delhi"),
                    data.get("target_category", "Long Stay D visa"),
                    int(data.get("check_interval_seconds", 30)),
                    data.get("telegram_bot_token", "").strip(),
                    str(data.get("telegram_chat_id", "")).strip(),
                    1 if str(data.get("telegram_enabled", "true")).lower() in ("true", "1") else 0,
                    data.get("notify_email", "").strip(),
                    1 if str(data.get("email_enabled", "true")).lower() in ("true", "1") else 0,
                    data.get("daily_report_time", "22:00").strip(),
                    1 if str(data.get("daily_report_enabled", "true")).lower() in ("true", "1") else 0
                ))
        conn.close()
        return True, "Slot monitor settings updated successfully."
    except Exception as e:
        log.error(f"Error updating slot_monitor_settings: {e}")
        return False, str(e)


def update_slot_monitor_running_state(is_running: bool):
    """Update is_running flag in database."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE slot_monitor_settings SET is_running = %s WHERE id = 1", (1 if is_running else 0,))
        conn.close()
    except Exception:
        pass


def update_slot_monitor_last_check(timestamp_str: str, status_dict: dict, last_found_date: Optional[str] = None):
    """Update last check timestamp, status message, and last found date."""
    try:
        msg = json.dumps(status_dict)
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE slot_monitor_settings 
                SET last_checked_at = %s, last_status_message = %s, last_found_date = %s
                WHERE id = 1
            """, (timestamp_str, msg, last_found_date))
        conn.close()
    except Exception:
        pass


def record_slot_check(
    centre: str,
    category: str,
    status_text: str,
    is_available: bool,
    appointment_date: Optional[str] = None,
    all_categories: Optional[dict] = None,
    notified_tg: bool = False,
    notified_em: bool = False
):
    """Insert a record into slot_checks_history."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO slot_checks_history (
                    centre, category, status_text, is_available,
                    appointment_date, all_categories_json, notified_telegram, notified_email
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                centre,
                category,
                status_text,
                1 if is_available else 0,
                appointment_date,
                json.dumps(all_categories or {}),
                1 if notified_tg else 0,
                1 if notified_em else 0
            ))
        conn.close()
    except Exception as e:
        log.debug(f"Error recording slot check history: {e}")


def get_recent_slot_checks(limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recent slot check records with IST checked_at string."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM slot_checks_history ORDER BY id DESC LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        conn.close()
        for r in rows:
            if "checked_at" in r and r["checked_at"]:
                val = r["checked_at"]
                if isinstance(val, datetime):
                    r["checked_at"] = val.strftime("%Y-%m-%d %H:%M:%S IST")
                elif isinstance(val, str) and not val.endswith("IST"):
                    r["checked_at"] = f"{val} IST"
        return rows
    except Exception as e:
        log.error(f"Error fetching slot checks: {e}")
        return []


def get_latest_slot_check() -> Optional[Dict[str, Any]]:
    """Fetch the most recent slot check record."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM slot_checks_history ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        conn.close()
        return row
    except Exception as e:
        log.warning(f"Error fetching latest slot check: {e}")
        return None


def update_user_notifications(
    user_id: int,
    telegram_chat_id: str,
    telegram_notifications: bool = True,
    email_notifications: bool = True,
    telegram_bot_token: Optional[str] = None,
    email: Optional[str] = None
) -> Tuple[bool, str]:
    """Update an operator's Telegram Chat ID, custom Bot Token, email, and notification preferences."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            updates = [
                "telegram_chat_id = %s",
                "telegram_notifications = %s",
                "email_notifications = %s"
            ]
            params: List[Any] = [
                str(telegram_chat_id).strip() if telegram_chat_id else None,
                1 if telegram_notifications else 0,
                1 if email_notifications else 0
            ]
            if telegram_bot_token is not None:
                updates.append("telegram_bot_token = %s")
                params.append(str(telegram_bot_token).strip() if telegram_bot_token else None)
            if email is not None:
                updates.append("email = %s")
                params.append(str(email).strip() if email else None)

            params.append(user_id)
            sql = f"UPDATE users SET {', '.join(updates)} WHERE id = %s"
            cur.execute(sql, tuple(params))
        conn.close()
        return True, "User notification preferences updated successfully."
    except Exception as e:
        log.error(f"Error updating user #{user_id} notifications: {e}")
        return False, str(e)



def get_database_stats() -> Dict[str, Any]:
    """Retrieve summary counts for all automation database tables."""
    stats = {}
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            for tbl in ALLOWED_TABLES:
                try:
                    cur.execute(f"SELECT COUNT(*) AS cnt FROM `{tbl}`")
                    stats[tbl] = cur.fetchone()["cnt"]
                except Exception:
                    stats[tbl] = 0
            if getattr(conn, "is_sqlite", False):
                cur.execute("SELECT sqlite_version() AS ver")
                ver_row = cur.fetchone()
                stats["version"] = f"SQLite {ver_row.get('ver') if ver_row else ''} (Hostinger MySQL throttled/failover mirror)"
                stats["database"] = "vfs_local.sqlite (Hostinger failover mirror)"
            else:
                cur.execute("SELECT VERSION() AS ver, DATABASE() AS db_name")
                srv = cur.fetchone()
                stats["version"] = srv.get("ver")
                stats["database"] = srv.get("db_name")
        conn.close()
        return {"success": True, "stats": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_table_rows(table_name: str, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """Inspect rows of an allowed table (Super Admin only)."""
    if table_name not in ALLOWED_TABLES:
        raise ValueError(f"Table '{table_name}' is not in allowed inspection tables.")
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if table_name == "users":
                # Include telegram_chat_id and notification flags
                cur.execute(f"""
                    SELECT id, username, email, full_name, role, status, telegram_chat_id, telegram_bot_token, telegram_notifications, email_notifications, created_by, created_at, last_login 
                    FROM users ORDER BY id DESC LIMIT %s OFFSET %s
                """, (limit, offset))
            else:
                cur.execute(f"SELECT * FROM `{table_name}` ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Error reading table {table_name}: {e}")
        return []


def clear_table_data(table_name: str) -> Tuple[bool, str]:
    """Clear data from a table (Super Admin only). Users table cannot be cleared."""
    if table_name not in ["booking_history", "applicant_profiles", "vfs_accounts", "slot_checks_history"]:
        return False, f"Clearing table '{table_name}' is prohibited."
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if getattr(conn, "is_sqlite", False):
                cur.execute(f"DELETE FROM `{table_name}`")
            else:
                cur.execute(f"TRUNCATE TABLE `{table_name}`")
        conn.close()
        return True, f"Table '{table_name}' cleared successfully."
    except Exception as e:
        log.error(f"Error clearing table {table_name}: {e}")
        return False, str(e)


# Aliases for compatibility
set_active_applicant = select_applicant
save_applicant = save_applicant_profile


