"""24/7 VFS Appointment Slot Watcher & Availability Monitor.

Monitors the public VFS Bulgaria appointment availability page:
URL: https://visa.vfsglobal.com/ind/en/bgr
Target Centre: Bulgaria Visa Application Center ,New Delhi (Configurable)
Target Category: Long Stay D visa (and all categories)
Frequency: Every 30 seconds (Configurable)

Notifications:
1. Instant Alert via Telegram & Email when appointment date becomes available
2. Daily 10:00 PM Status Report via Telegram & Email
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright, BrowserContext, Page, Playwright

from config import cfg
import vfs_db
import vfs_notifications

log = logging.getLogger("vfs.slot_monitor")

SCREENSHOTS_DIR = Path(__file__).resolve().parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class VFSSlotMonitor:
    def __init__(self):
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()

        # In-memory runtime state
        self.last_check_time: Optional[datetime] = None
        self.last_status: Dict[str, str] = {
            "Long Stay D visa": "Checking...",
            "Business": "Checking...",
            "Schengen Visa- Less than 90 days": "Checking...",
            "Seasonal worker": "Checking...",
            "Embassy approved interview": "Checking..."
        }
        self.last_centre: str = "Bulgaria Visa Application Center ,New Delhi"
        self.last_found_date: Optional[str] = None
        self.total_checks_today: int = 0
        self.last_reset_day: int = datetime.now().day
        self.daily_report_sent_date: Optional[str] = None
        self.last_alert_date_sent: Optional[str] = None
        self.last_error: Optional[str] = None
        self.recent_history: List[Dict[str, Any]] = []

    def get_status(self) -> Dict[str, Any]:
        """Return current slot monitor status for dashboard and APIs."""
        with self.lock:
            # Check if midnight rolled over to reset today's check count
            now_day = datetime.now().day
            if now_day != self.last_reset_day:
                self.total_checks_today = 0
                self.last_reset_day = now_day

            d_visa_status = self.last_status.get("Long Stay D visa", "Unknown")
            is_d_available = "no date" not in d_visa_status.lower() and d_visa_status not in ("Checking...", "Unknown")

            return {
                "is_running": self.is_running,
                "target_centre": self.last_centre,
                "target_category": "Long Stay D visa",
                "last_check_time": self.last_check_time.strftime("%Y-%m-%d %H:%M:%S") if self.last_check_time else None,
                "last_status": self.last_status,
                "d_visa_status": d_visa_status,
                "is_d_visa_available": is_d_available,
                "last_found_date": self.last_found_date,
                "total_checks_today": self.total_checks_today,
                "last_error": self.last_error,
                "recent_history": self.recent_history[:15]
            }

    def start(self):
        """Start the 24/7 background slot monitor thread."""
        with self.lock:
            if self.is_running and self.thread and self.thread.is_alive():
                log.info("Slot monitor is already running.")
                return

            self.is_running = True
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True, name="VFSSlotMonitorThread")
            self.thread.start()
            
            # Persist state in DB
            try:
                vfs_db.update_slot_monitor_running_state(True)
            except Exception:
                pass
            log.info("24/7 VFS Slot Monitor worker started.")

    def stop(self):
        """Stop the background slot monitor thread."""
        with self.lock:
            if not self.is_running:
                return
            self.is_running = False
            self.stop_event.set()
            
            try:
                vfs_db.update_slot_monitor_running_state(False)
            except Exception:
                pass
            log.info("VFS Slot Monitor worker requested to stop.")

    def _monitor_loop(self):
        """Continuous 24/7 checking loop."""
        log.info("Entering 24/7 VFS Slot Monitor loop...")

        while not self.stop_event.is_set():
            try:
                # Load latest settings from database
                settings = vfs_db.get_slot_monitor_settings()
                centre = settings.get("target_centre") or "Bulgaria Visa Application Center ,New Delhi"
                target_cat = settings.get("target_category") or "Long Stay D visa"
                interval_secs = max(15, int(settings.get("check_interval_seconds") or 30))
                daily_time = settings.get("daily_report_time") or "22:00"
                daily_enabled = bool(settings.get("daily_report_enabled", 1))

                # 1. Perform Single Check
                check_result = self.perform_check(centre, target_cat)
                
                # 2. Check for 10:00 PM Daily Report
                if daily_enabled:
                    self._check_and_send_daily_report(centre, daily_time)

            except Exception as e:
                log.error(f"Error in slot monitor cycle: {e}")
                with self.lock:
                    self.last_error = str(e)
                interval_secs = 30

            # Sleep in 1-second chunks to respond immediately to stop()
            for _ in range(interval_secs):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        log.info("VFS Slot Monitor loop exited.")

    def perform_check(self, target_centre: str = "Bulgaria Visa Application Center ,New Delhi", target_category: str = "Long Stay D visa") -> Dict[str, Any]:
        """Perform one slot availability check on https://visa.vfsglobal.com/ind/en/bgr."""
        from vfs_browser import launch_stealth_browser

        portal_url = cfg.PORTAL_URL or "https://visa.vfsglobal.com/ind/en/bgr"
        timestamp = datetime.now()
        timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")

        log.info(f"Checking slot availability for '{target_centre}' on {portal_url}...")
        results_data = {}
        screenshot_path = str(SCREENSHOTS_DIR / "latest_slot_check.png")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage"
                    ]
                )
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US"
                )
                ctx.add_init_script("delete Object.getPrototypeOf(navigator).webdriver;")
                page = ctx.new_page()
                
                try:
                    page.goto(portal_url, wait_until="domcontentloaded", timeout=35000)
                    time.sleep(2)

                    # Dismiss cookies
                    for sel in ["#onetrust-accept-btn-handler", "#onetrust-reject-all-handler", "button:has-text('Accept All Cookies')", "button:has-text('Accept Only Necessary')"]:
                        try:
                            loc = page.locator(sel).first
                            if loc.count() > 0 and loc.is_visible():
                                loc.click(timeout=1500)
                                time.sleep(0.5)
                                break
                        except Exception:
                            pass

                    # Wait for centre dropdown to be ready
                    page.wait_for_selector("#visa-centre", timeout=12000)
                    select_loc = page.locator("#visa-centre")

                    # Find matching option value
                    options = select_loc.locator("option").all()
                    selected_val = None
                    clean_target = target_centre.lower().replace(",", " ").replace("-", " ")
                    for opt in options:
                        txt = (opt.text_content() or "").strip()
                        val = opt.get_attribute("value")
                        clean_txt = txt.lower().replace(",", " ").replace("-", " ")
                        if "new delhi" in clean_txt or clean_target in clean_txt:
                            selected_val = val
                            break
                    
                    if selected_val is not None:
                        page.select_option("#visa-centre", value=selected_val)
                    else:
                        page.select_option("#visa-centre", index=4)

                    # Wait for results to populate
                    time.sleep(2.5)

                    # Capture screenshot
                    try:
                        page.screenshot(path=screenshot_path)
                    except Exception:
                        pass

                    # Parse categories and dates
                    parsed_dict = page.evaluate("""() => {
                        const data = {};
                        const cards = document.querySelectorAll('*');
                        for (const el of cards) {
                            const txt = (el.innerText || '').trim();
                            if (txt.includes(':') && (txt.includes('visa') || txt.includes('Visa') || txt.includes('Business') || txt.includes('worker') || txt.includes('interview'))) {
                                if (el.children.length <= 2 && txt.length < 150) {
                                    const parts = txt.split(':');
                                    if (parts.length >= 2) {
                                        const key = parts[0].trim();
                                        const val = parts.slice(1).join(':').trim().replace(/\\n/g, ' ');
                                        if (!data[key] || data[key].length < val.length) {
                                            data[key] = val;
                                        }
                                    }
                                }
                            }
                        }
                        return data;
                    }""")

                    results_data = parsed_dict or {}

                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    try:
                        browser.close()
                    except Exception:
                        pass

        except Exception as e:
            log.error(f"Slot check error: {e}")
            with self.lock:
                self.last_error = str(e)
            return {"success": False, "error": str(e)}

        # Update in-memory state
        with self.lock:
            self.last_check_time = timestamp
            self.total_checks_today += 1
            self.last_centre = target_centre
            self.last_error = None
            if results_data:
                self.last_status.update(results_data)

            # Check status of target category (e.g. Long Stay D visa)
            target_status = self.last_status.get(target_category) or self.last_status.get("Long Stay D visa", "No date available")
            is_available = "no date" not in target_status.lower() and len(target_status) > 2

            if is_available:
                self.last_found_date = target_status
                log.info(f"🔥 AVAILABLE APPOINTMENT FOUND! {target_category}: {target_status}")

            # Append to in-memory history
            history_entry = {
                "time": timestamp_str,
                "centre": target_centre,
                "category": target_category,
                "status": target_status,
                "is_available": is_available,
                "all_categories": dict(self.last_status)
            }
            self.recent_history.insert(0, history_entry)
            if len(self.recent_history) > 30:
                self.recent_history.pop()

        # Database record
        try:
            vfs_db.record_slot_check(
                centre=target_centre,
                category=target_category,
                status_text=target_status,
                is_available=is_available,
                appointment_date=target_status if is_available else None,
                all_categories=self.last_status,
                notified_tg=False,
                notified_em=False
            )
            vfs_db.update_slot_monitor_last_check(timestamp_str, self.last_status, self.last_found_date)
        except Exception as e:
            log.debug(f"DB slot check record error: {e}")

        # -------------------------------------------------------------
        # ALERT DISPATCH LOGIC (Instant Notification on Available Date)
        # -------------------------------------------------------------
        if is_available:
            # Prevent sending duplicate notifications for the exact same date within 20 minutes
            should_alert = False
            with self.lock:
                if self.last_alert_date_sent != target_status:
                    should_alert = True
                    self.last_alert_date_sent = target_status

            if should_alert:
                log.info(f"Triggering immediate Telegram & Email alerts for {target_category}: {target_status}...")
                alert_res = vfs_notifications.broadcast_slot_alert(
                    category=target_category,
                    date_found=target_status,
                    centre=target_centre,
                    screenshot_path=screenshot_path
                )
                log.info(f"Alert dispatch summary: {alert_res}")

        return {
            "success": True,
            "centre": target_centre,
            "target_category": target_category,
            "target_status": target_status,
            "is_available": is_available,
            "results": self.last_status,
            "timestamp": timestamp_str
        }

    def _check_and_send_daily_report(self, centre: str, daily_report_time: str = "22:00"):
        """Send daily summary report at configured time (default 10:00 PM)."""
        now = datetime.now()
        current_hm = now.strftime("%H:%M")
        today_date_str = now.strftime("%Y-%m-%d")

        # Check if hour and minute match (e.g. "22:00" = 10:00 PM)
        if current_hm == daily_report_time.strip():
            with self.lock:
                if self.daily_report_sent_date == today_date_str:
                    return  # Already sent today
                self.daily_report_sent_date = today_date_str
                checks_count = self.total_checks_today
                status_copy = dict(self.last_status)
                found_date = self.last_found_date

            log.info(f"Triggering daily {daily_report_time} appointment availability report...")
            try:
                vfs_notifications.broadcast_daily_report(
                    centre=centre,
                    categories_status=status_copy,
                    total_checks_today=checks_count,
                    last_found_date=found_date
                )
            except Exception as e:
                log.error(f"Error sending daily report: {e}")


# Singleton slot monitor instance
slot_monitor = VFSSlotMonitor()
