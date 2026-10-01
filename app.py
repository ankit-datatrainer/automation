"""Flask Web Control Dashboard and REST API for VFS Global Automation."""

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
from flask import Flask, Response, jsonify, render_template, request, send_file, send_from_directory
from flask_cors import CORS

from config import cfg
from vfs_automation import SCREENSHOTS_DIR, VFSAutomation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vfs.app")

app = Flask(__name__)
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


def push_log(step: str, message: str):
    """Callback triggered by automation to record logs and update state."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = {"timestamp": timestamp, "step": step, "message": message}
    state["current_step"] = step
    state["message"] = message
    state["last_update"] = time.time()

    if step == "COMPLETED":
        state["status"] = "COMPLETED"
        state["finished_at"] = timestamp
    elif step == "ERROR":
        state["status"] = "ERROR"
        state["finished_at"] = timestamp
    elif step == "STOPPED":
        state["status"] = "STOPPED"
        state["finished_at"] = timestamp
    else:
        state["status"] = "RUNNING"

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


@app.route("/")
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


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({
        "state": state,
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
        "applicant_count": len(cfg.APPLICANTS_LIST)
    })


@app.route("/api/config", methods=["GET", "POST"])
def handle_config():
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if "applicants" in data and isinstance(data["applicants"], list):
            cfg.save_applicants(data["applicants"])
        
        env_updates = {k: v for k, v in data.items() if k != "applicants"}
        if env_updates:
            cfg.update_config(env_updates)
            
        push_log("CONFIG", f"Configuration updated ({len(cfg.APPLICANTS_LIST)} applicant(s)) and saved.")
        return jsonify({
            "success": True, 
            "message": "Configuration updated successfully.",
            "applicants": cfg.APPLICANTS_LIST
        })
    
    return jsonify({
        "VFS_EMAIL": cfg.VFS_EMAIL,
        "VFS_PASSWORD": cfg.VFS_PASSWORD,
        "VFS_GMAIL_USER": cfg.VFS_GMAIL_USER,
        "VFS_GMAIL_APP_PASSWORD": cfg.VFS_GMAIL_APP_PASSWORD,
        "TARGET_CITY": cfg.TARGET_CITY,
        "VISA_CATEGORY": cfg.VISA_CATEGORY,
        "VISA_SUB_CATEGORY": cfg.VISA_SUB_CATEGORY,
        "PORTAL_URL": cfg.PORTAL_URL,
        "BROWSER_CHANNEL": cfg.BROWSER_CHANNEL,
        "HEADLESS": cfg.HEADLESS,
        "APPLICANT_FIRST_NAME": cfg.APPLICANT_FIRST_NAME,
        "APPLICANT_LAST_NAME": cfg.APPLICANT_LAST_NAME,
        "APPLICANT_GENDER": cfg.APPLICANT_GENDER,
        "APPLICANT_DOB": cfg.APPLICANT_DOB,
        "APPLICANT_NATIONALITY": cfg.APPLICANT_NATIONALITY,
        "APPLICANT_PASSPORT_NUMBER": cfg.APPLICANT_PASSPORT_NUMBER,
        "APPLICANT_PASSPORT_EXPIRY": cfg.APPLICANT_PASSPORT_EXPIRY,
        "APPLICANT_PHONE": cfg.APPLICANT_PHONE,
        "APPLICANT_EMAIL": cfg.APPLICANT_EMAIL,
        "applicants": cfg.APPLICANTS_LIST,
    })


@app.route("/api/start", methods=["POST"])
def start_automation():
    global automation_thread, active_automation
    if state["status"] == "RUNNING" and automation_thread and automation_thread.is_alive():
        return jsonify({"success": False, "message": "Automation is already running."}), 400

    data = request.get_json(force=True, silent=True) or {}

    state["status"] = "RUNNING"
    state["current_step"] = "STARTING"
    state["message"] = "Starting automation session..."
    state["started_at"] = datetime.now().strftime("%H:%M:%S")
    state["finished_at"] = None

    automation_thread = threading.Thread(target=run_worker, args=(data,), daemon=True)
    automation_thread.start()

    return jsonify({"success": True, "message": "Automation started successfully."})


@app.route("/api/stop", methods=["POST"])
def stop_automation():
    global active_automation
    if active_automation:
        active_automation.stop_requested = True
        push_log("STOPPING", "Stopping automation session...")
    state["status"] = "STOPPED"
    return jsonify({"success": True, "message": "Automation stop requested."})


@app.route("/api/live_screen")
def live_screen():
    """Serve latest browser frame for remote / on-screen monitoring."""
    latest = SCREENSHOTS_DIR / "latest.png"
    if latest.exists():
        response = send_file(latest, mimetype="image/png")
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response
    
    # Check for any recent screenshot
    if SCREENSHOTS_DIR.exists():
        pngs = sorted(SCREENSHOTS_DIR.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)
        if pngs:
            response = send_file(pngs[0], mimetype="image/png")
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

    return Response(
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" viewBox="0 0 800 450"><rect width="800" height="450" fill="#0f172a"/><text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#64748b" font-family="sans-serif" font-size="18">Browser session idle. Click "Start Automation" to begin.</text></svg>',
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
def list_screenshots():
    """List captured screenshots sorted by modification time."""
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
def serve_screenshot(filename):
    return send_from_directory(SCREENSHOTS_DIR, filename)


# Ensure UTF-8 stdout encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def auto_open_browser(url: str):
    """Automatically pop up the UI dashboard in the user's browser."""
    time.sleep(1.2)
    try:
        webbrowser.open(url)
    except Exception:
        pass


if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", cfg.DASHBOARD_PORT))
    local_url = f"http://localhost:{port}"

    print("\n=======================================================")
    print("VFS GLOBAL BOOKING AUTOMATION — CONTROL SUITE")
    print(f"Interface URL: {local_url}")
    print(f"Network URL:   http://{host}:{port}")
    print("=======================================================\n")

    # Open browser on startup
    if not os.getenv("NO_AUTO_OPEN"):
        threading.Thread(target=auto_open_browser, args=(local_url,), daemon=True).start()

    app.run(host=host, port=port, debug=False, threaded=True)
