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
                vfs_email TEXT UNIQUE NOT NULL,
                vfs_password TEXT NOT NULL,
                gmail_user TEXT,
                gmail_app_password TEXT,
                status TEXT DEFAULT 'active',
                notes TEXT,
                last_used_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
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

        # Seed default users if empty
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            h1, s1 = hash_password("Admin@2026!")
            cur.execute("INSERT INTO users (username, email, password_hash, salt, full_name, role) VALUES (?, ?, ?, ?, ?, ?)",
                        ("superadmin", "superadmin@vfsautomation.com", h1, s1, "Super Administrator", "super_admin"))
            h2, s2 = hash_password("Operator@2026!")
            cur.execute("INSERT INTO users (username, email, telegram_chat_id, password_hash, salt, full_name, role) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        ("operator1", "operator1@vfsautomation.com", "7815919062", h2, s2, "Automation Operator 1", "user"))
            h3, s3 = hash_password("Operator@2026!")
            cur.execute("INSERT INTO users (username, email, password_hash, salt, full_name, role) VALUES (?, ?, ?, ?, ?, ?)",
                        ("operator2", "operator2@vfsautomation.com", h3, s3, "Automation Operator 2", "user"))

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
                    vfs_email VARCHAR(150) UNIQUE NOT NULL,
                    vfs_password VARCHAR(100) NOT NULL,
                    gmail_user VARCHAR(150),
                    gmail_app_password VARCHAR(100),
                    status VARCHAR(50) DEFAULT 'active',
                    notes TEXT,
                    last_used_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)

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

            # Schema Migration: Add telegram_chat_id, telegram_notifications, email_notifications to users if missing
            try:
                cur.execute("SHOW COLUMNS FROM users LIKE 'telegram_chat_id'")
                if not cur.fetchone():
                    cur.execute("ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(100) NULL AFTER email;")
                    cur.execute("ALTER TABLE users ADD COLUMN telegram_notifications TINYINT(1) DEFAULT 1 AFTER telegram_chat_id;")
                    cur.execute("ALTER TABLE users ADD COLUMN email_notifications TINYINT(1) DEFAULT 1 AFTER telegram_notifications;")
                    log.info("Migrated users table with Telegram and Email notification columns.")
            except Exception as e:
                log.debug(f"Users table migration note: {e}")

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
                    cfg.APPLICANT_EMAIL or cfg.VFS_EMAIL
                ))

            # -------------------------------------------------------------
            # SEED USERS: 1 Super Admin & 2 Normal Users
            # -------------------------------------------------------------
            default_users = [
                {
                    "username": "superadmin",
                    "email": "superadmin@vfsautomation.com",
                    "full_name": "Super Administrator",
                    "password": "Admin@2026!",
                    "role": "super_admin",
                },
                {
                    "username": "operator1",
                    "email": "operator1@vfsautomation.com",
                    "full_name": "Automation Operator 1",
                    "password": "Operator@2026!",
                    "role": "user",
                },
                {
                    "username": "operator2",
                    "email": "operator2@vfsautomation.com",
                    "full_name": "Automation Operator 2",
                    "password": "Operator@2026!",
                    "role": "user",
                },
            ]

            for u in default_users:
                cur.execute("SELECT id FROM users WHERE username = %s", (u["username"],))
                if not cur.fetchone():
                    p_hash, p_salt = hash_password(u["password"])
                    cur.execute("""
                        INSERT INTO users (username, email, password_hash, salt, full_name, role, status, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, 'active', 'system')
                    """, (u["username"], u["email"], p_hash, p_salt, u["full_name"], u["role"]))
                    log.info(f"Seeded user '{u['username']}' ({u['role']})")

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

            # Sync default VFS account if vfs_accounts table is empty
            cur.execute("SELECT COUNT(*) AS cnt FROM vfs_accounts")
            acc_res = cur.fetchone()
            if acc_res and acc_res["cnt"] == 0 and cfg.VFS_EMAIL:
                cur.execute("""
                    INSERT INTO vfs_accounts (vfs_email, vfs_password, gmail_user, gmail_app_password, status)
                    VALUES (%s, %s, %s, %s, 'active')
                    ON DUPLICATE KEY UPDATE vfs_password=VALUES(vfs_password)
                """, (cfg.VFS_EMAIL, cfg.VFS_PASSWORD, cfg.VFS_GMAIL_USER, cfg.VFS_GMAIL_APP_PASSWORD))

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
    """Verify username & password, returns user dict on success or None."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE username = %s OR email = %s",
                (username.strip(), username.strip())
            )
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
    """Fetch user by ID."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, full_name, role, status, telegram_chat_id, telegram_notifications, email_notifications, created_by, created_at, last_login 
                FROM users WHERE id = %s
            """, (user_id,))
            user = cur.fetchone()
        conn.close()
        return user
    except Exception as e:
        log.error(f"Error getting user by id {user_id}: {e}")
        return None


def get_all_users() -> List[Dict[str, Any]]:
    """Retrieve all users list (without password hashes)."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, email, full_name, role, status, telegram_chat_id, telegram_notifications, email_notifications, created_by, created_at, last_login 
                FROM users 
                ORDER BY role DESC, id ASC
            """)
            rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        log.error(f"Error fetching users: {e}")
        return []


def create_user(
    username: str,
    password: str,
    full_name: str = "",
    email: str = "",
    role: str = "user",
    created_by: str = "super_admin"
) -> Tuple[bool, str, Optional[int]]:
    """Create a new user in MySQL. Only callable by super admin."""
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
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            if cur.fetchone():
                conn.close()
                return False, f"Username '{username}' already exists.", None

            p_hash, p_salt = hash_password(password)
            cur.execute("""
                INSERT INTO users (username, email, password_hash, salt, full_name, role, status, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, 'active', %s)
            """, (username, email.strip(), p_hash, p_salt, full_name.strip(), role, created_by))
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
    """Update slot monitor settings in MySQL."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
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


def update_user_notifications(
    user_id: int,
    telegram_chat_id: str,
    telegram_notifications: bool = True,
    email_notifications: bool = True
) -> Tuple[bool, str]:
    """Update an operator's Telegram Chat ID and notification preferences."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users 
                SET telegram_chat_id = %s, telegram_notifications = %s, email_notifications = %s
                WHERE id = %s
            """, (
                str(telegram_chat_id).strip() if telegram_chat_id else None,
                1 if telegram_notifications else 0,
                1 if email_notifications else 0,
                user_id
            ))
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
                    SELECT id, username, email, full_name, role, status, telegram_chat_id, telegram_notifications, email_notifications, created_by, created_at, last_login 
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


