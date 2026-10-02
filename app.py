"""Flask Web Control Dashboard, Super Admin Portal, and REST API for VFS Global Automation."""

import functools
import json
import logging
import os
import queue
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)
from flask_cors import CORS

from config import cfg
from time_utils import get_ist_now, format_ist_dt, format_ist_time, format_ist_display, format_ist_date, IST
from vfs_automation import SCREENSHOTS_DIR, VFSAutomation
import vfs_browser
import vfs_db
import vfs_notifications
from vfs_slot_monitor import slot_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vfs.app")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "vfs_global_suite_session_secret_key_2026_@!#$")
CORS(app)

# Global automation controller state
state = {
    "status": "IDLE",            # IDLE, RUNNING, COMPLETED, STOPPED, ERROR
    "current_step": "NONE",
    "message": "System ready. Click 'Start Automation' to begin.",
    "started_at": None,
    "finished_at": None,
    "last_update": time.time(),
}

log_queue = queue.Queue(maxsize=1000)
recent_logs = []
active_automation: Optional[VFSAutomation] = None
automation_thread: Optional[threading.Thread] = None


# =============================================================================
# AUTHENTICATION & ACCESS CONTROL
# =============================================================================

def login_required(f):
    """Ensure user is logged in to access view or API."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "Authentication required. Please log in."}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated_function


def super_admin_required(f):
    """Ensure current user is authenticated with Super Admin role."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "Authentication required."}), 401
            return redirect(url_for("login_page"))
        if session.get("role") != "super_admin":
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "message": "Access denied. Super Admin privileges required."}), 403
            return render_template("403.html"), 403
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_user():
    """Make current user info available in all Jinja templates."""
    if session.get("user_id"):
        uid = session.get("user_id")
        user_db = vfs_db.get_user_by_id(uid) or {}
        return {
            "current_user": {
                "id": uid,
                "username": session.get("username"),
                "full_name": user_db.get("full_name") or session.get("full_name") or session.get("username"),
                "email": user_db.get("email") or session.get("email"),
                "role": session.get("role"),
                "telegram_chat_id": user_db.get("telegram_chat_id") or session.get("telegram_chat_id"),
                "telegram_notifications": user_db.get("telegram_notifications", 1),
                "email_notifications": user_db.get("email_notifications", 1),
            },
            "server_ist_time": format_ist_time()
        }
    return {"current_user": None, "server_ist_time": format_ist_time()}


# =============================================================================
# LOGGING & WORKER THREAD
# =============================================================================

# Set of step names that belong strictly to the browser booking automation
BOOKING_AUTOMATION_STEPS = {
    "STARTING", "INIT", "LOGIN", "OTP", "DASHBOARD", 
    "APPLICATION_DETAIL", "FORM", "YOUR_DETAILS", "APPLICANT", 
    "BOOK_APPOINTMENT", "SLOT", "SERVICES", "PAYMENT", 
    "REVIEW", "APPOINTMENT_CONFIRMATION"
}

def push_log(step: str, message: str):
    """Callback triggered by automation to record logs and update state in IST."""
    timestamp = format_ist_time()
    entry = {"timestamp": timestamp, "step": step, "message": message}

    # Only update booking automation state if this is an actual booking automation event
    if step in BOOKING_AUTOMATION_STEPS:
        state["current_step"] = step
        state["message"] = message
        state["status"] = "RUNNING"
        state["last_update"] = time.time()
    elif step == "COMPLETED":
        state["current_step"] = step
        state["status"] = "COMPLETED"
        state["message"] = message
        state["finished_at"] = timestamp
        state["last_update"] = time.time()
    elif step == "ERROR":
        state["current_step"] = step
        state["status"] = "ERROR"
        state["message"] = message
        state["finished_at"] = timestamp
        state["last_update"] = time.time()
    elif step == "PAUSED":
        state["current_step"] = step
        state["status"] = "PAUSED"
        state["message"] = message
        state["last_update"] = time.time()
    elif step in ("STOPPED", "STOPPING"):
        state["status"] = "STOPPED"
        state["message"] = message
        state["finished_at"] = timestamp
        state["last_update"] = time.time()
    # Administrative & background events (AUTH, MONITOR, ADMIN, CONFIG, DATABASE, NOTIFY, USER)
    # do NOT touch the booking automation's state or stepper.

    recent_logs.append(entry)
    if len(recent_logs) > 400:
        recent_logs.pop(0)

    try:
        log_queue.put_nowait(entry)
    except queue.Full:
        pass


def run_worker(overrides: Optional[dict] = None):
    """Background worker thread executing VFSAutomation."""
    global active_automation
    try:
        if overrides:
            if "applicants" in overrides and isinstance(overrides["applicants"], list):
                cfg.save_applicants(overrides.pop("applicants"))
            if overrides:
                cfg.update_config(overrides)

        push_log("INIT", f"Starting VFS Bulgaria appointment automation workflow for {len(cfg.APPLICANTS_LIST)} applicant(s)...")
        active_automation = VFSAutomation(config=cfg, log_cb=push_log)
        active_automation.run()
    except Exception as e:
        push_log("ERROR", f"Automation exception: {e}")
    finally:
        if state["status"] == "RUNNING":
            state["status"] = "COMPLETED"


# =============================================================================
# AUTH ROUTES
# =============================================================================

@app.route("/login")
def login_page():
    if session.get("user_id"):
        return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password are required."}), 400

    user = vfs_db.authenticate_user(username, password)
    if not user:
        return jsonify({"success": False, "message": "Invalid username or password."}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["full_name"] = user["full_name"] or user["username"]
    session["email"] = user.get("email", "")
    session["role"] = user["role"]

    push_log("AUTH", f"User '{user['username']}' ({user['role']}) logged in.")
    return jsonify({
        "success": True,
        "message": f"Welcome back, {user['full_name'] or user['username']}!",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user["full_name"]
        },
        "redirect": "/admin" if user["role"] == "super_admin" else "/"
    })


@app.route("/logout")
def logout():
    uname = session.get("username", "User")
    session.clear()
    push_log("AUTH", f"{uname} logged out.")
    return redirect(url_for("login_page"))


@app.route("/api/auth/me")
def auth_me():
    if session.get("user_id"):
        return jsonify({
            "authenticated": True,
            "user": {
                "id": session.get("user_id"),
                "username": session.get("username"),
                "full_name": session.get("full_name"),
                "role": session.get("role")
            }
        })
    return jsonify({"authenticated": False})


# =============================================================================
# MAIN CONTROLLER & VIEWS
# =============================================================================

@app.route("/favicon.ico")
def favicon():
    return send_from_directory(Path(__file__).parent / "static", "favicon.svg", mimetype="image/svg+xml")


@app.route("/")
@login_required
def index():
    return render_template(
        "index.html",
        cfg=cfg,
        status=state["status"],
        step=state["current_step"],
        message=state["message"],
        applicants=cfg.APPLICANTS_LIST
    )


@app.route("/live")
@login_required
def live_view():
    """Standalone, crystal-clear, full-window live browser view."""
    return render_template(
        "live.html",
        cfg=cfg,
        status=state["status"],
        step=state["current_step"],
        message=state["message"],
        applicants=cfg.APPLICANTS_LIST
    )


@app.route("/admin")
@super_admin_required
def admin_portal():
    """Super Admin command center for user management & database operations."""
    return render_template("admin.html", cfg=cfg)


# =============================================================================
# SUPER ADMIN APIS
# =============================================================================

@app.route("/api/admin/users", methods=["GET", "POST"])
@super_admin_required
def admin_users():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        username = data.get("username", "").strip()
        password = data.get("password", "")
        full_name = data.get("full_name", "").strip()
        email = data.get("email", "").strip()
        role = data.get("role", "user")

        ok, msg, new_id = vfs_db.create_user(
            username=username,
            password=password,
            full_name=full_name,
            email=email,
            role=role,
            created_by=session.get("username", "super_admin")
        )
        if ok:
            push_log("ADMIN", f"Super Admin created new user '{username}' (role: {role}).")
            return jsonify({"success": True, "message": msg, "user_id": new_id})
        return jsonify({"success": False, "message": msg}), 400

    users = vfs_db.get_all_users()
    return jsonify({"success": True, "users": users})


@app.route("/api/admin/users/<int:user_id>/status", methods=["POST"])
@super_admin_required
def admin_user_status(user_id):
    data = request.get_json(force=True, silent=True) or {}
    status = data.get("status", "active")
    ok = vfs_db.update_user_status(user_id, status)
    if ok:
        push_log("ADMIN", f"Updated user #{user_id} status to '{status}'.")
        return jsonify({"success": True, "message": f"User status set to {status}."})
    return jsonify({"success": False, "message": "Failed to update user status."}), 400


@app.route("/api/admin/users/<int:user_id>/password", methods=["POST"])
@super_admin_required
def admin_user_password(user_id):
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    ok, msg = vfs_db.update_user_password(user_id, password)
    if ok:
        push_log("ADMIN", f"Password reset for user #{user_id}.")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@super_admin_required
def admin_delete_user(user_id):
    if user_id == session.get("user_id"):
        return jsonify({"success": False, "message": "You cannot delete your own logged-in account."}), 400

    ok, msg = vfs_db.delete_user(user_id)
    if ok:
        push_log("ADMIN", f"Deleted user account #{user_id}.")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/admin/db/stats", methods=["GET"])
@super_admin_required
def admin_db_stats():
    stats = vfs_db.get_database_stats()
    return jsonify(stats)


@app.route("/api/admin/db/table/<table_name>", methods=["GET"])
@super_admin_required
def admin_db_table_data(table_name):
    limit = int(request.args.get("limit", 100))
    offset = int(request.args.get("offset", 0))
    try:
        rows = vfs_db.get_table_rows(table_name, limit=limit, offset=offset)
        return jsonify({"success": True, "table": table_name, "rows": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400


@app.route("/api/admin/db/table/<table_name>/clear", methods=["POST"])
@super_admin_required
def admin_db_clear_table(table_name):
    ok, msg = vfs_db.clear_table_data(table_name)
    if ok:
        push_log("ADMIN", f"Cleared table data for '{table_name}'.")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


# =============================================================================
# 24/7 SLOT MONITOR & TELEGRAM / EMAIL ALERTS APIS
# =============================================================================

@app.route("/api/slot_monitor/status", methods=["GET"])
@login_required
def slot_monitor_status():
    """Retrieve runtime status of the 24/7 slot watcher and current availability."""
    return jsonify({"success": True, "status": slot_monitor.get_status()})


@app.route("/api/slot_monitor/start", methods=["POST"])
@login_required
def slot_monitor_start():
    """Start the 24/7 background slot watcher thread."""
    slot_monitor.start()
    push_log("MONITOR", "24/7 VFS Appointment Slot Watcher started (Interval: 30s).")
    return jsonify({"success": True, "message": "24/7 Slot Watcher started successfully."})


@app.route("/api/slot_monitor/stop", methods=["POST"])
@login_required
def slot_monitor_stop():
    """Stop the 24/7 background slot watcher."""
    slot_monitor.stop()
    push_log("MONITOR", "24/7 VFS Appointment Slot Watcher stopped.")
    return jsonify({"success": True, "message": "24/7 Slot Watcher stopped."})


@app.route("/api/slot_monitor/check_now", methods=["POST"])
@login_required
def slot_monitor_check_now():
    """Trigger an immediate slot availability check."""
    settings = vfs_db.get_slot_monitor_settings()
    centre = settings.get("target_centre") or "Bulgaria Visa Application Center ,New Delhi"
    target_cat = settings.get("target_category") or "Long Stay D visa"

    def _worker():
        try:
            slot_monitor.perform_check(centre, target_cat)
        except Exception as e:
            log.error(f"Manual slot check failed: {e}")

    threading.Thread(target=_worker, daemon=True, name="ManualSlotCheckWorker").start()
    push_log("MONITOR", f"Manual slot check triggered for '{centre}' - category '{target_cat}'.")
    return jsonify({"success": True, "message": "Slot check initiated. Results will update momentarily."})


@app.route("/api/slot_monitor/settings", methods=["GET", "POST"])
@login_required
def slot_monitor_settings_api():
    """Get or update slot monitor and Telegram/Email alerting settings."""
    user_role = session.get("role", "user")

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        # If non-superadmin tries to edit, prevent changing bot token
        if user_role != "super_admin":
            curr = vfs_db.get_slot_monitor_settings()
            data["telegram_bot_token"] = curr.get("telegram_bot_token", "")
        elif "..." in data.get("telegram_bot_token", ""):
            # Preserve existing token if frontend passed masked value
            curr = vfs_db.get_slot_monitor_settings()
            data["telegram_bot_token"] = curr.get("telegram_bot_token", "")

        ok, msg = vfs_db.update_slot_monitor_settings(data)
        if ok:
            push_log("SETTINGS", "Slot monitor and notification settings updated.")
            return jsonify({"success": True, "message": msg})
        return jsonify({"success": False, "message": msg}), 400

    settings = vfs_db.get_slot_monitor_settings()
    # Mask bot token for operators
    if user_role != "super_admin":
        tok = settings.get("telegram_bot_token", "")
        if tok and len(tok) > 8:
            settings["telegram_bot_token"] = tok[:4] + "..." + tok[-4:]
    return jsonify({"success": True, "settings": settings})


@app.route("/api/slot_monitor/test_telegram", methods=["POST"])
@login_required
def slot_monitor_test_telegram():
    """Test Telegram connection and send ping."""
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("telegram_bot_token", "").strip()
    chat_id = str(data.get("telegram_chat_id", "")).strip()

    settings = vfs_db.get_slot_monitor_settings()
    if not token or "..." in token:
        token = settings.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")

    if not chat_id:
        chat_id = settings.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        return jsonify({"success": False, "message": "Telegram Bot Token and Chat ID are required."}), 400

    ok, msg, bot_info = vfs_notifications.test_telegram_connection(token, chat_id)
    if ok:
        push_log("NOTIFY", f"Telegram test sent successfully to chat {chat_id}.")
        return jsonify({"success": True, "message": msg, "bot_info": bot_info})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/slot_monitor/test_email", methods=["POST"])
@login_required
def slot_monitor_test_email():
    """Test email alert dispatch."""
    data = request.get_json(force=True, silent=True) or {}
    to_email = data.get("email", "").strip()
    if not to_email:
        settings = vfs_db.get_slot_monitor_settings()
        to_email = settings.get("notify_email") or cfg.APPLICANT_EMAIL or cfg.VFS_EMAIL

    if not to_email or "@" not in to_email:
        return jsonify({"success": False, "message": "A valid recipient email address is required."}), 400

    ok, msg = vfs_notifications.test_email_connection(to_email)
    if ok:
        push_log("NOTIFY", f"Email test sent successfully to {to_email}.")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/slot_monitor/history", methods=["GET"])
@login_required
def slot_monitor_history():
    """Get recent slot checks log."""
    limit = min(100, int(request.args.get("limit", 30)))
    history = vfs_db.get_recent_slot_checks(limit)
    return jsonify({"success": True, "history": history})


@app.route("/api/admin/users/<int:user_id>/telegram", methods=["POST"])
@super_admin_required
def admin_user_telegram(user_id):
    """Super Admin configuration of operator Telegram Chat ID & alert preferences."""
    data = request.get_json(force=True, silent=True) or {}
    chat_id = str(data.get("telegram_chat_id", "")).strip()
    tg_notif = bool(data.get("telegram_notifications", True))
    em_notif = bool(data.get("email_notifications", True))

    ok, msg = vfs_db.update_user_notifications(user_id, chat_id, tg_notif, em_notif)
    if ok:
        push_log("ADMIN", f"Updated Telegram/Notification settings for user #{user_id}.")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/user/notifications", methods=["POST"])
@login_required
def user_update_own_notifications():
    """Operator self-service update of their Telegram Chat ID."""
    uid = session.get("user_id")
    if not uid:
        return jsonify({"success": False, "message": "Not authenticated."}), 401

    data = request.get_json(force=True, silent=True) or {}
    chat_id = str(data.get("telegram_chat_id", "")).strip()
    tg_notif = bool(data.get("telegram_notifications", True))
    em_notif = bool(data.get("email_notifications", True))

    ok, msg = vfs_db.update_user_notifications(uid, chat_id, tg_notif, em_notif)
    if ok:
        push_log("USER", f"Operator updated notification settings (Telegram Chat ID: {chat_id or 'none'}).")
        return jsonify({"success": True, "message": msg})
    return jsonify({"success": False, "message": msg}), 400


@app.route("/api/user/test_telegram", methods=["POST"])
@login_required
def user_test_telegram():
    """One-click Telegram ping for the logged-in operator."""
    uid = session.get("user_id")
    user_db = vfs_db.get_user_by_id(uid) or {}
    chat_id = user_db.get("telegram_chat_id")
    if not chat_id:
        return jsonify({"success": False, "message": "No Telegram Chat ID configured for your account. Please set it in Alert Settings."}), 400

    settings = vfs_db.get_slot_monitor_settings()
    bot_token = settings.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "") or cfg.TELEGRAM_BOT_TOKEN
    if not bot_token:
        return jsonify({"success": False, "message": "Telegram Bot Token is not configured."}), 400

    text = (
        f"🔔 <b>Operator Telegram Ping Test</b>\n\n"
        f"👤 <b>Operator:</b> {user_db.get('full_name') or user_db.get('username')}\n"
        f"📱 <b>Chat ID:</b> <code>{chat_id}</code>\n"
        f"📅 <b>Time (IST):</b> <code>{format_ist_dt()}</code>\n"
        f"✅ <b>Status:</b> Telegram alerts are active and ready!"
    )
    ok, msg = vfs_notifications.send_telegram_message(bot_token, chat_id, text)
    if not ok:
        return jsonify({"success": False, "message": f"Telegram test failed: {msg}"}), 400
    return jsonify({"success": True, "message": f"Telegram ping delivered successfully to Chat ID {chat_id}!"})



# =============================================================================
# AUTOMATION CONTROLLER APIS
# =============================================================================

@app.route("/api/status", methods=["GET"])
def get_status():
    global active_automation
    is_paused = bool(active_automation.is_paused) if active_automation else False
    is_otp_prompt = bool(active_automation.is_otp_prompt) if active_automation else False
    active_url = str(active_automation.page.url) if (active_automation and active_automation.page) else ""

    return jsonify({
        "state": {
            **state,
            "is_paused": is_paused,
            "is_otp_prompt": is_otp_prompt,
            "active_url": active_url,
        },
        "recent_logs": recent_logs[-50:],
        "target": {
            "city": cfg.TARGET_CITY,
            "category": cfg.VISA_CATEGORY,
            "sub_category": cfg.VISA_SUB_CATEGORY,
            "portal": cfg.PORTAL_URL,
            "browser": cfg.BROWSER_CHANNEL,
            "headless": cfg.HEADLESS
        },
        "applicants": cfg.APPLICANTS_LIST,
        "applicant_count": len(cfg.APPLICANTS_LIST),
        "user": {
            "username": session.get("username"),
            "role": session.get("role")
        } if session.get("user_id") else None
    })


@app.route("/api/config", methods=["GET", "POST"])
@login_required
def handle_config():
    user_role = session.get("role", "user")
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if "applicants" in data and isinstance(data["applicants"], list):
            cfg.save_applicants(data["applicants"])
        
        # Protect database credentials: only super_admin may modify DB settings
        disallowed_keys = {"DB_HOST", "DB_PORT", "DB_USERNAME", "DB_PASSWORD", "DB_DATABASE", "DB_ENABLED"}
        env_updates = {
            k: v for k, v in data.items() 
            if k != "applicants" and (user_role == "super_admin" or k not in disallowed_keys)
        }
        if env_updates:
            cfg.update_config(env_updates)
            
        push_log("CONFIG", f"Configuration updated ({len(cfg.APPLICANTS_LIST)} applicant(s)) and saved.")
        return jsonify({
            "success": True, 
            "message": "Configuration updated successfully.",
            "applicants": cfg.APPLICANTS_LIST
        })
    
    cfg_data = {
        "VFS_EMAIL": cfg.VFS_EMAIL,
        "VFS_PASSWORD": cfg.VFS_PASSWORD,
        "VFS_GMAIL_USER": cfg.VFS_GMAIL_USER,
        "VFS_GMAIL_APP_PASSWORD": cfg.VFS_GMAIL_APP_PASSWORD,
        "TARGET_CITY": cfg.TARGET_CITY,
        "VISA_CATEGORY": cfg.VISA_CATEGORY,
        "VISA_SUB_CATEGORY": cfg.VISA_SUB_CATEGORY,
        "BROWSER_CHANNEL": cfg.BROWSER_CHANNEL,
        "HEADLESS": cfg.HEADLESS,
        "applicants": cfg.APPLICANTS_LIST
    }
    # Raw database details are only exposed to Super Admin
    if user_role == "super_admin":
        cfg_data.update({
            "DB_ENABLED": cfg.DB_ENABLED,
            "DB_HOST": cfg.DB_HOST,
            "DB_PORT": cfg.DB_PORT,
            "DB_USERNAME": cfg.DB_USERNAME,
            "DB_DATABASE": cfg.DB_DATABASE,
        })
    return jsonify(cfg_data)


@app.route("/api/db/status", methods=["GET"])
@login_required
def db_status():
    if not cfg.DB_ENABLED:
        return jsonify({
            "enabled": False,
            "connected": False,
            "message": "Database is currently disabled in .env (DB_ENABLED=false)"
        })

    test_res = vfs_db.test_connection()
    if not test_res.get("success"):
        return jsonify({
            "enabled": cfg.DB_ENABLED,
            "connected": False,
            "message": test_res.get("message"),
            "profile_count": 0,
            "active_profile": None
        })

    profiles = vfs_db.get_all_applicants()
    active = vfs_db.get_active_applicant()
    return jsonify({
        "enabled": cfg.DB_ENABLED,
        "connected": True,
        "message": "Connected successfully to remote MySQL database.",
        "profile_count": len(profiles),
        "active_profile": active,
        "server_version": test_res.get("version")
    })


@app.route("/api/db/test", methods=["POST"])
@login_required
def db_test():
    data = request.get_json(force=True, silent=True) or {}
    host = data.get("host") or cfg.DB_HOST
    port = int(data.get("port") or cfg.DB_PORT or 3306)
    user = data.get("username") or cfg.DB_USERNAME
    password = data.get("password") or cfg.DB_PASSWORD
    database = data.get("database") or cfg.DB_DATABASE
    result = vfs_db.test_connection(host=host, port=port, user=user, password=password, database=database)
    return jsonify(result)
    result = vfs_db.test_connection(host=host, port=port, user=user, password=password, database=database)
    return jsonify(result)


@app.route("/api/db/init", methods=["POST"])
@login_required
def db_init():
    ok = vfs_db.init_db()
    if ok:
        profiles = vfs_db.get_all_applicants()
        push_log("DATABASE", f"Database initialized successfully. Found {len(profiles)} applicant profile(s).")
        return jsonify({
            "success": True,
            "message": "Database tables and default users initialized successfully.",
            "profiles": profiles
        })
    return jsonify({"success": False, "message": "Failed to initialize database tables."}), 500


@app.route("/api/db/applicants", methods=["GET", "POST"])
@login_required
def db_applicants():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        ok, msg, p_id = vfs_db.save_applicant_profile(data)
        if ok:
            push_log("DATABASE", f"Saved applicant profile '{data.get('profile_name') or data.get('first_name', '')}' to MySQL.")
            return jsonify({"success": True, "message": msg, "id": p_id})
        return jsonify({"success": False, "message": msg}), 400

    profiles = vfs_db.get_all_applicants()
    active = vfs_db.get_active_applicant()
    return jsonify({
        "success": True,
        "profiles": profiles,
        "active": active
    })


@app.route("/api/db/applicants/<int:applicant_id>/select", methods=["POST"])
@login_required
def db_select_applicant(applicant_id):
    active = vfs_db.select_applicant(applicant_id)
    if active:
        name = active.get("profile_name") if active else f"#{applicant_id}"
        push_log("CONFIG", f"Activated applicant profile '{name}' from MySQL database.")
        return jsonify({
            "success": True,
            "message": f"Applicant '{name}' is now active for booking.",
            "applicant": active,
            "current_config": {
                "first_name": cfg.APPLICANT_FIRST_NAME,
                "last_name": cfg.APPLICANT_LAST_NAME,
                "passport": cfg.APPLICANT_PASSPORT_NUMBER,
                "city": cfg.TARGET_CITY,
                "category": cfg.VISA_CATEGORY
            }
        })
    return jsonify({"success": False, "message": f"Could not set applicant #{applicant_id} as active."}), 400


@app.route("/api/db/applicants/<int:applicant_id>", methods=["DELETE"])
@login_required
def db_delete_applicant(applicant_id):
    ok = vfs_db.delete_applicant(applicant_id)
    if ok:
        push_log("DATABASE", f"Deleted applicant profile #{applicant_id} from MySQL.")
        return jsonify({"success": True, "message": "Applicant profile deleted."})
    return jsonify({"success": False, "message": "Failed to delete applicant profile."}), 400


@app.route("/api/db/history", methods=["GET"])
@login_required
def db_history():
    limit = int(request.args.get("limit", 50))
    history = vfs_db.get_booking_history(limit=limit)
    return jsonify({"success": True, "history": history})


@app.route("/api/start", methods=["POST"])
@login_required
def start_automation():
    global automation_thread, active_automation
    if state["status"] == "RUNNING" and automation_thread and automation_thread.is_alive():
        return jsonify({"success": False, "message": "Automation is already running."}), 400

    data = request.get_json(force=True, silent=True) or {}

    state["status"] = "RUNNING"
    state["current_step"] = "STARTING"
    state["message"] = "Starting automation session..."
    state["started_at"] = format_ist_time()
    state["finished_at"] = None

    automation_thread = threading.Thread(target=run_worker, args=(data,), daemon=True)
    automation_thread.start()

    # Schedule real browser to be forced to front 2 seconds after start
    def deferred_front():
        time.sleep(2.5)
        try:
            vfs_browser.bring_browser_to_front()
        except Exception:
            pass

    threading.Thread(target=deferred_front, daemon=True).start()

    return jsonify({"success": True, "message": "Automation started successfully. Real browser window is launching on your screen."})


@app.route("/api/pause", methods=["POST"])
@login_required
def pause_automation():
    """Pause the running automation session and bring the real browser window in front."""
    global active_automation
    if active_automation and state["status"] in ("RUNNING", "STARTING"):
        active_automation.pause()
        state["status"] = "PAUSED"
        state["message"] = "Automation paused. Real browser window active on desktop for manual control."
        push_log("PAUSED", state["message"])
        return jsonify({"success": True, "message": "Automation paused. Real browser window brought to front."})
    return jsonify({"success": False, "message": "Automation is not currently running."}), 400


@app.route("/api/resume", methods=["POST"])
@login_required
def resume_automation():
    """Resume the paused automation session from current browser position."""
    global active_automation
    if active_automation:
        active_automation.resume()
        state["status"] = "RUNNING"
        state["message"] = "Automation resumed. Resuming booking flow..."
        push_log("RUNNING", state["message"])
        return jsonify({"success": True, "message": "Automation resumed. Picking up from current browser stage."})
    return jsonify({"success": False, "message": "No active automation session to resume."}), 400


@app.route("/api/submit_otp", methods=["POST"])
@login_required
def submit_otp_api():
    """Submit OTP code entered by the operator directly into the real browser."""
    global active_automation
    data = request.get_json(force=True, silent=True) or {}
    otp = str(data.get("otp", "")).strip()
    if not otp:
        return jsonify({"success": False, "message": "OTP code cannot be empty."}), 400

    if not active_automation or not active_automation.page:
        return jsonify({"success": False, "message": "No active browser session found to submit OTP to."}), 400

    ok = active_automation.submit_manual_otp(otp)
    if ok:
        push_log("OTP", f"Operator submitted OTP {otp} to browser.")
        return jsonify({"success": True, "message": f"OTP {otp} submitted to browser."})
    return jsonify({"success": False, "message": "Failed submitting OTP into browser. Please enter directly in the real browser window."}), 500


@app.route("/api/stop", methods=["POST"])
@login_required
def stop_automation():
    global active_automation
    if active_automation:
        active_automation.stop_requested = True
        active_automation.is_paused = False  # Unblock pause wait loop so it cleanly exits
        push_log("STOPPING", "Stopping automation session...")
    state["status"] = "STOPPED"
    return jsonify({"success": True, "message": "Automation stop requested."})


@app.route("/api/bring_to_front", methods=["POST"])
@login_required
def bring_to_front_api():
    """Bring the real VFS browser window directly in front of the user on Windows desktop."""
    global active_automation
    try:
        page = active_automation.page if active_automation else None
        vfs_browser.bring_browser_to_front(page)
        return jsonify({"success": True, "message": "Real browser window brought to front and maximized."})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/live_screen")
def live_screen():
    """Serve latest browser frame for remote VPS monitoring."""
    latest = SCREENSHOTS_DIR / "latest.png"
    if latest.exists():
        response = send_file(latest, mimetype="image/png")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    
    if SCREENSHOTS_DIR.exists():
        pngs = sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
        if pngs:
            response = send_file(pngs[0], mimetype="image/png")
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    return Response(
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450"><rect width="800" height="450" fill="#0f172a"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#64748b" font-family="sans-serif" font-size="18">Browser session idle. Real browser opens upon Start.</text></svg>',
        mimetype="image/svg+xml"
    )


@app.route("/api/logs/stream")
def stream_logs():
    def event_stream():
        for entry in recent_logs[-40:]:
            yield f"data: {json.dumps(entry)}\n\n"

        while True:
            try:
                entry = log_queue.get(timeout=20)
                yield f"data: {json.dumps(entry)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


@app.route("/api/screenshots")
@login_required
def list_screenshots():
    files = []
    if SCREENSHOTS_DIR.exists():
        for p in sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.name == "latest.png":
                continue
            files.append({
                "name": p.name,
                "url": f"/screenshots/{p.name}",
                "mtime": p.stat().st_mtime
            })
    return jsonify({"screenshots": files})


@app.route("/screenshots/<path:filename>")
@login_required
def serve_screenshot(filename):
    return send_from_directory(SCREENSHOTS_DIR, filename)


# Ensure UTF-8 stdout encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# Ensure remote database tables and seeded users exist on startup
try:
    log.info("Checking and initializing Hostinger remote database tables and seeded accounts...")
    vfs_db.init_db()
    # Auto-start slot watcher if enabled in database
    _mon_cfg = vfs_db.get_slot_monitor_settings()
    if _mon_cfg.get("is_running"):
        log.info("Auto-starting 24/7 Slot Watcher from saved database state...")
        slot_monitor.start()
except Exception as _e:
    log.warning(f"DB / Slot Monitor auto-init notice: {_e}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 4140))
    host = os.environ.get("HOST", "0.0.0.0")

    print("\n" + "=" * 65)
    print("  🚀 VFS GLOBAL AUTOMATION SUITE + SUPER ADMIN DASHBOARD")
    print(f"  🌐 Local Controller:   http://localhost:{port}")
    print(f"  🔐 Login Page:         http://localhost:{port}/login")
    print(f"  👑 Super Admin Center: http://localhost:{port}/admin")
    print(f"  🗄️ Remote Database:    srv2203.hstgr.io (u796972987_automation)")
    print("=" * 65)
    print("  Default Pre-configured Logins:")
    print("    👑 Super Admin: superadmin / Admin@2026!")
    print("    👤 Operator 1:  operator1  / Operator@2026!")
    print("    👤 Operator 2:  operator2  / Operator@2026!")
    print("=" * 65 + "\n")

    app.run(host=host, port=port, debug=False, threaded=True)
