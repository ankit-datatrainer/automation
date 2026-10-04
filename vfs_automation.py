"""VFS Global Bulgaria Appointment Booking Automation Workflow."""

import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeout, sync_playwright

from config import Config, cfg
from vfs_browser import launch_stealth_browser, bring_window_to_front_win32
from vfs_otp import fetch_latest_otp, get_inbox_baseline

log = logging.getLogger("vfs.automation")

SCREENSHOTS_DIR = Path(__file__).resolve().parent / "data" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)


class VFSAutomation:
    def __init__(self, config: Config = cfg, log_cb: Optional[Callable[[str, str], None]] = None):
        self.cfg = config
        self.log_cb = log_cb or (lambda status, msg: log.info(f"[{status}] {msg}"))
        self.stop_requested = False
        self.is_paused = False
        self.is_otp_prompt = False
        self._should_resume_flow = False
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.current_step = "IDLE"

    def report(self, step: str, message: str):
        """Report status update to callbacks and logger."""
        self.current_step = step
        log.info(f"[{step}] {message}")
        if self.log_cb:
            self.log_cb(step, message)

    def pause(self):
        """Pause automation workflow for operator manual takeover in real browser."""
        self.is_paused = True
        self.report("PAUSED", "Automation PAUSED for operator manual control. Real browser window brought to front.")
        if sys.platform == "win32" and not self.cfg.HEADLESS:
            bring_window_to_front_win32(self.page)

    def resume(self):
        """Resume automation workflow from where the operator left off."""
        self.is_paused = False
        self._should_resume_flow = True
        self.report("RUNNING", "Automation RESUMED by operator. Resuming booking flow from active page...")

    def check_pause_and_stop(self) -> bool:
        """Helper that yields to pause loop or aborts on stop request. Returns True if stopped."""
        if self.stop_requested:
            return True
        if self.is_paused:
            self.report("PAUSED", "Automation is paused. Operator has manual control of real browser.")
            if sys.platform == "win32" and not self.cfg.HEADLESS:
                bring_window_to_front_win32(self.page)
            while self.is_paused and not self.stop_requested:
                if self.page:
                    try:
                        self.take_screenshot("live_view")
                    except Exception:
                        pass
                time.sleep(0.8)
            if self.stop_requested:
                return True
            self.report("RUNNING", "Resumed by operator! Continuing booking workflow...")
        return False

    def submit_manual_otp(self, otp_code: str) -> bool:
        """Type manual OTP code directly into the browser verification fields and submit."""
        if not self.page:
            return False
        clean_code = re.sub(r"\D", "", str(otp_code or "")).strip()
        if len(clean_code) < 4:
            self.report("OTP", f"Invalid OTP code provided: {otp_code}")
            return False

        self.report("OTP", f"Submitting operator OTP: {clean_code} into verification inputs...")
        try:
            otp_inputs = self.page.locator(
                'input[formcontrolname="otp"], input#otp, input[placeholder*="OTP" i], '
                'input[placeholder*="code" i], input[placeholder*="verification" i]'
            ).all()
            if not otp_inputs:
                otp_inputs = self.page.locator('input[type="text"]:visible, input[type="number"]:visible, input[type="tel"]:visible').all()

            if len(otp_inputs) == 1:
                otp_inputs[0].click()
                otp_inputs[0].fill("")
                otp_inputs[0].press_sequentially(clean_code, delay=35)
                otp_inputs[0].dispatch_event("input")
                otp_inputs[0].dispatch_event("change")
            elif len(otp_inputs) >= len(clean_code):
                for idx, digit in enumerate(clean_code):
                    otp_inputs[idx].click()
                    otp_inputs[idx].fill(digit)
                    otp_inputs[idx].dispatch_event("input")
                    otp_inputs[idx].dispatch_event("change")

            self.page.wait_for_timeout(600)
            self.take_screenshot("manual_otp_entered")

            # Check and solve Cloudflare Turnstile if needed
            self.handle_cloudflare_turnstile(max_retries=1)

            # Locate submit button
            otp_submit = self.page.locator(
                'button.btn-brand-orange, button:has-text("Sign In"), button:has-text("Verify"), '
                'button:has-text("Submit"), button:has-text("Continue"), button[type="submit"]'
            ).first

            if otp_submit.count() > 0:
                try:
                    otp_submit.click(timeout=4000)
                except Exception:
                    self.page.evaluate("b => { if(b) { b.removeAttribute('disabled'); b.click(); } }", otp_submit)

            self.page.wait_for_timeout(1500)
            self.take_screenshot("manual_otp_submitted")
            self.report("OTP", f"Submitted OTP {clean_code} to browser.")
            return True
        except Exception as e:
            self.report("ERROR", f"Failed entering manual OTP: {e}")
            return False

    def interact(self, action: str, data: dict) -> bool:
        """Execute interactive operator input (mouse, keyboard, scroll) directly on the live Playwright browser page."""
        if not self.page:
            return False
        try:
            if action == "click":
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                button = data.get("button", "left")
                self.page.mouse.click(x, y, button=button)
                log.info(f"Interactive browser {button}-click dispatched to ({x}, {y})")
            elif action == "dblclick":
                x = int(data.get("x", 0))
                y = int(data.get("y", 0))
                self.page.mouse.dblclick(x, y)
                log.info(f"Interactive browser double-click dispatched to ({x}, {y})")
            elif action == "type":
                text = str(data.get("text", ""))
                if text:
                    self.page.keyboard.type(text, delay=20)
                    log.info(f"Interactive browser text typed ({len(text)} chars)")
            elif action == "press":
                key = str(data.get("key", ""))
                if key:
                    self.page.keyboard.press(key)
                    log.info(f"Interactive browser key pressed: '{key}'")
            elif action == "scroll":
                dx = int(data.get("delta_x", 0))
                dy = int(data.get("delta_y", 0))
                self.page.mouse.wheel(dx, dy)
            elif action == "navigate":
                url = data.get("url")
                if url:
                    self.page.goto(url, wait_until="domcontentloaded")
                    log.info(f"Interactive browser navigated to: {url}")
            
            # Immediately capture fresh frame to data/screenshots/latest.png so live view updates instantaneously
            self.take_screenshot("live_view")
            return True
        except Exception as e:
            log.error(f"Error during browser interaction ({action}): {e}")
            return False


    def take_screenshot(self, name: str):
        """Capture screenshot to data/screenshots directory and update latest.png with single encoding."""
        if self.page:
            try:
                dest = SCREENSHOTS_DIR / f"{name}.png"
                img_bytes = self.page.screenshot(type="png")
                with open(dest, "wb") as f:
                    f.write(img_bytes)
                latest = SCREENSHOTS_DIR / "latest.png"
                with open(latest, "wb") as f:
                    f.write(img_bytes)
            except Exception as e:
                log.debug(f"Could not take screenshot {name}: {e}")

    def dismiss_cookie_banner(self):
        """Dismiss OneTrust cookie overlay if present."""
        if not self.page:
            return
        try:
            # Check for Reject all or Accept only necessary
            for sel in [
                "#onetrust-reject-all-handler",
                "#onetrust-accept-btn-handler",
                "button#onetrust-accept-btn-handler",
                "button:has-text('Reject All')",
                "button:has-text('Accept All Cookies')",
            ]:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    self.report("NAVIGATION", "Dismissing cookie banner...")
                    loc.click(timeout=3000)
                    self.page.wait_for_timeout(500)
                    break
        except Exception:
            pass

    def handle_cloudflare_turnstile(self, max_retries: int = 4) -> bool:
        """Attempt to interact with Cloudflare Turnstile checkbox and check token."""
        if not self.page:
            return False

        # 1. Check if token already populated
        try:
            cf_token = self.page.locator('input[name="cf-turnstile-response"]').first
            if cf_token.count() > 0:
                val = cf_token.input_value().strip()
                if len(val) > 10:
                    return True
        except Exception:
            pass

        # 2. Check for Turnstile iframes
        has_turnstile = False
        try:
            frames = self.page.frames
            if any("challenges.cloudflare.com" in f.url or "turnstile" in f.url for f in frames):
                has_turnstile = True
            elif self.page.locator('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]').count() > 0:
                has_turnstile = True
        except Exception:
            pass

        if not has_turnstile:
            return False

        self.report("LOGIN", "Cloudflare Turnstile detected. Engaging verification...")

        for _ in range(max_retries):
            # Check token again
            try:
                cf_token = self.page.locator('input[name="cf-turnstile-response"]').first
                if cf_token.count() > 0 and len(cf_token.input_value().strip()) > 10:
                    self.report("LOGIN", "Cloudflare Turnstile token confirmed.")
                    return True
            except Exception:
                pass

            # Coordinate click on Turnstile iframe checkbox
            try:
                for iframe in self.page.locator('iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"]').all():
                    if iframe.is_visible():
                        box = iframe.bounding_box()
                        if box and box["width"] > 40 and box["height"] > 20:
                            click_x = box["x"] + 28
                            click_y = box["y"] + (box["height"] / 2)
                            self.page.mouse.move(click_x, click_y, steps=3)
                            self.page.wait_for_timeout(80)
                            self.page.mouse.down()
                            self.page.wait_for_timeout(60)
                            self.page.mouse.up()
                            break
            except Exception:
                pass

            # Child frame direct click
            for frame in self.page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    for sel in ['input[type="checkbox"]', '.ctp-checkbox-label', 'span.mark', 'label']:
                        try:
                            el = frame.locator(sel).first
                            if el.count() > 0 and el.is_visible():
                                el.click(timeout=1000, force=True)
                                break
                        except Exception:
                            pass

            self.page.wait_for_timeout(1000)

        # Final token check
        try:
            cf_token = self.page.locator('input[name="cf-turnstile-response"]').first
            if cf_token.count() > 0 and len(cf_token.input_value().strip()) > 10:
                return True
        except Exception:
            pass

        return False

    def run(self):
        """Execute the complete automation flow up to Application Details."""
        self.stop_requested = False
        self.report("STARTING", "Initializing automation session...")

        with sync_playwright() as p:
            self.playwright = p
            self.context, self.page = launch_stealth_browser(
                p,
                channel=self.cfg.BROWSER_CHANNEL,
                headless=self.cfg.HEADLESS
            )

            # Start background thread to establish Gmail baseline early (zero blocking time)
            baseline_result = {"uid": 0, "time": datetime.now(timezone.utc)}
            def _async_baseline():
                try:
                    uid, t = get_inbox_baseline(self.cfg.VFS_GMAIL_USER, self.cfg.VFS_GMAIL_APP_PASSWORD)
                    baseline_result["uid"] = uid
                    baseline_result["time"] = t
                    log.info(f"Background Gmail baseline established: UID={uid}")
                except Exception as ex:
                    log.debug(f"Async baseline note: {ex}")
            threading.Thread(target=_async_baseline, daemon=True).start()

            try:
                # -------------------------------------------------------------
                # STEP 1 & 2: Fast-Path Direct Navigation to VFS Login
                # -------------------------------------------------------------
                self.report("NAVIGATION", f"Fast-navigating directly to VFS Portal: {self.cfg.LOGIN_URL}...")
                try:
                    self.page.goto(self.cfg.LOGIN_URL, wait_until="domcontentloaded", timeout=self.cfg.ACTION_TIMEOUT_MS)
                except Exception:
                    self.page.goto(self.cfg.BOOK_APPOINTMENT_URL, wait_until="domcontentloaded", timeout=self.cfg.ACTION_TIMEOUT_MS)

                self.dismiss_cookie_banner()
                if sys.platform == "win32" and not self.cfg.HEADLESS:
                    bring_window_to_front_win32(self.page)
                self.take_screenshot("01_portal_loaded")

                if self.check_pause_and_stop():
                    self.report("STOPPED", "Automation cancelled by user.")
                    return

                # -------------------------------------------------------------
                # STEP 3: Instant Login Credentials Entry
                # -------------------------------------------------------------
                self.report("LOGIN", "Entering credentials...")
                email_input = self.page.locator(
                    'input#email, input[formcontrolname="username"], input[type="email"], input[placeholder*="email" i]'
                ).first
                email_input.wait_for(state="visible", timeout=20000)

                pwd_input = self.page.locator(
                    'input#password, input[formcontrolname="password"], input[type="password"], input[placeholder*="password" i]'
                ).first
                pwd_input.wait_for(state="visible", timeout=20000)

                email_input.click()
                email_input.fill(self.cfg.VFS_EMAIL)
                email_input.dispatch_event("input")
                email_input.dispatch_event("change")
                email_input.evaluate("el => el.blur()")

                pwd_input.click()
                pwd_input.fill(self.cfg.VFS_PASSWORD)
                pwd_input.dispatch_event("input")
                pwd_input.dispatch_event("change")
                pwd_input.evaluate("el => el.blur()")

                self.take_screenshot("03_credentials_entered")

                if self.check_pause_and_stop():
                    self.report("STOPPED", "Automation cancelled by user.")
                    return

                # -------------------------------------------------------------
                # STEP 4: Cloudflare Verification & Fast-Click "Sign In"
                # -------------------------------------------------------------
                self.report("LOGIN", "Verifying Turnstile and Sign In button...")
                self.handle_cloudflare_turnstile(max_retries=2)

                sign_in_btn = self.page.locator(
                    'button.btn-brand-orange, button:has-text("Sign In"), button[type="submit"]:has-text("Sign In"), button[type="submit"]'
                ).first
                sign_in_btn.wait_for(state="visible", timeout=10000)

                # High-speed polling (every 250ms)
                deadline = time.monotonic() + 45
                clicked_signin = False
                while time.monotonic() < deadline:
                    if self.check_pause_and_stop():
                        self.report("STOPPED", "Automation cancelled by user.")
                        return

                    self.handle_cloudflare_turnstile(max_retries=1)

                    if sign_in_btn.is_enabled():
                        self.report("LOGIN", "Sign In button active. Submitting credentials...")
                        sign_in_btn.click()
                        clicked_signin = True
                        self.take_screenshot("04_signin_clicked")
                        break

                    # Token fallback
                    try:
                        token_el = self.page.locator('input[name="cf-turnstile-response"]').first
                        if token_el.count() > 0 and len(token_el.input_value().strip()) > 10:
                            self.page.evaluate("() => { const f = document.querySelector('form'); if(f) f.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); const b = document.querySelector('button.btn-brand-orange, button[type=\"submit\"]'); if(b) { b.removeAttribute('disabled'); b.click(); } }")
                            clicked_signin = True
                            self.take_screenshot("04_signin_clicked")
                            break
                    except Exception:
                        pass

                    self.page.wait_for_timeout(250)

                if not clicked_signin:
                    self.report("LOGIN", "Attempting force click on Sign In button...")
                    try:
                        sign_in_btn.click(force=True)
                        clicked_signin = True
                    except Exception:
                        pass

                # -------------------------------------------------------------
                # STEP 5: Fast OTP Detection & Asynchronous Auto-Poll
                # -------------------------------------------------------------
                self.report("OTP", "Monitoring for OTP challenge...")
                is_otp_screen = False
                otp_detect_deadline = time.monotonic() + 30

                while time.monotonic() < otp_detect_deadline:
                    if self.check_pause_and_stop():
                        self.report("STOPPED", "Automation cancelled by user.")
                        return

                    cur_url = self.page.url.lower()
                    if "dashboard" in cur_url or "application" in cur_url:
                        break

                    if self._is_on_otp_screen():
                        is_otp_screen = True
                        break

                    # Check for rate-limiting
                    body_text = ""
                    try:
                        body_text = self.page.locator("body").inner_text()
                    except Exception:
                        pass
                    if "429001" in body_text or "access restricted" in body_text.lower():
                        self.report("ERROR", "VFS Rate Limit (429001): Access restricted due to frequent logins. Waiting cooldown.")
                        self.take_screenshot("vfs_access_restricted_429001")
                        return

                    self.page.wait_for_timeout(250)

                if is_otp_screen:
                    self.is_otp_prompt = True
                    self.report("OTP", "OTP prompt active! Real browser brought to front. Enter OTP or wait for Gmail auto-fetch.")
                    if sys.platform == "win32" and not self.cfg.HEADLESS:
                        bring_window_to_front_win32(self.page)
                    self.take_screenshot("05_otp_screen_detected")

                    # Launch asynchronous Gmail OTP poller so the Playwright thread never blocks
                    gmail_otp_state = {"code": None, "stop": False}
                    def _async_poll_otp():
                        base_u = baseline_result.get("uid", 0)
                        base_t = baseline_result.get("time", datetime.now(timezone.utc))
                        while not gmail_otp_state["stop"]:
                            try:
                                c = fetch_latest_otp(
                                    user=self.cfg.VFS_GMAIL_USER,
                                    app_password=self.cfg.VFS_GMAIL_APP_PASSWORD,
                                    baseline_uid=base_u,
                                    baseline_time=base_t,
                                    timeout_seconds=2,
                                    poll_interval=2,
                                    log_callback=lambda msg: self.report("OTP", msg)
                                )
                                if c:
                                    gmail_otp_state["code"] = c
                                    break
                            except Exception:
                                pass
                            time.sleep(1.5)

                    otp_poll_thread = threading.Thread(target=_async_poll_otp, daemon=True)
                    otp_poll_thread.start()

                    otp_overall_deadline = time.monotonic() + 300
                    last_shot_time = 0

                    while time.monotonic() < otp_overall_deadline:
                        if self.check_pause_and_stop():
                            gmail_otp_state["stop"] = True
                            self.report("STOPPED", "Automation cancelled by user.")
                            return

                        # Check if background Gmail poller fetched the code
                        if gmail_otp_state["code"]:
                            code = gmail_otp_state["code"]
                            gmail_otp_state["code"] = None
                            gmail_otp_state["stop"] = True
                            self.report("OTP", f"Retrieved OTP from Gmail: {code}. Entering into verification fields...")
                            self.submit_manual_otp(code)

                        cur_url = self.page.url.lower()
                        if "dashboard" in cur_url or "application-detail" in cur_url or "your-details" in cur_url:
                            self.report("OTP", "OTP verified successfully! Reached dashboard.")
                            self.is_otp_prompt = False
                            gmail_otp_state["stop"] = True
                            break

                        now = time.monotonic()
                        if now - last_shot_time > 2.0:
                            self.take_screenshot("05_otp_live")
                            last_shot_time = now

                        self.page.wait_for_timeout(250)

                    gmail_otp_state["stop"] = True
                    self.is_otp_prompt = False

                # -------------------------------------------------------------
                # STEP 6: Dashboard & "Start New Booking"
                # -------------------------------------------------------------
                self.report("DASHBOARD", "Waiting for Dashboard page...")
                dash_deadline = time.monotonic() + 45
                found_dashboard = False

                while time.monotonic() < dash_deadline:
                    if self.check_pause_and_stop():
                        self.report("STOPPED", "Automation cancelled by user.")
                        return

                    cur_url = self.page.url.lower()
                    if "dashboard" in cur_url or "application-detail" in cur_url or "your-details" in cur_url:
                        found_dashboard = True
                        break

                    self.page.wait_for_timeout(250)

                if not found_dashboard:
                    self.report("ERROR", f"Could not reach dashboard within timeout. Current URL: {self.page.url}")
                    self.take_screenshot("error_not_on_dashboard")
                    if self.check_pause_and_stop():
                        return
                    return

                self.take_screenshot("08_dashboard_reached")

                # Locate and click "Start New Booking"
                if "application-detail" not in self.page.url.lower():
                    self._click_start_new_booking()

                # Step 7 & forward: Application Details, Your Details, and Slot Booking
                self._fill_application_detail_and_forward()

                while not self.stop_requested:
                    if self.check_pause_and_stop():
                        break
                    if getattr(self, "_should_resume_flow", False):
                        self._should_resume_flow = False
                        self.resume_from_current_page()
                    self.page.wait_for_timeout(1500)

            except Exception as e:
                self.report("ERROR", f"Automation alert: {str(e)}. Browser kept open for manual takeover.")
                self.take_screenshot("error_state")
                try:
                    import vfs_db
                    vfs_db.log_booking_result(
                        applicant_name=f"{self.cfg.APPLICANT_FIRST_NAME} {self.cfg.APPLICANT_LAST_NAME}",
                        passport_number=self.cfg.APPLICANT_PASSPORT_NUMBER,
                        target_city=self.cfg.TARGET_CITY,
                        visa_category=self.cfg.VISA_CATEGORY,
                        status="PAUSED_ERROR",
                        step_reached=self.current_step,
                        message=str(e)[:400]
                    )
                except Exception:
                    pass

                # Keep browser open on desktop and pause for operator manual control!
                self.pause()
                if sys.platform == "win32" and not self.cfg.HEADLESS:
                    bring_window_to_front_win32(self.page)

                while not self.stop_requested:
                    if self.check_pause_and_stop():
                        break
                    if getattr(self, "_should_resume_flow", False):
                        self._should_resume_flow = False
                        self.resume_from_current_page()
                    self.page.wait_for_timeout(1500)
            finally:
                if self.stop_requested:
                    self.report("STOPPED", "Session closed.")
                    try:
                        self.context.close()
                    except Exception:
                        pass

    def _is_on_otp_screen(self) -> bool:
        """Check if browser is currently on OTP verification prompt."""
        if not self.page:
            return False
        try:
            otp_input = self.page.locator(
                'input[formcontrolname="otp"], input#otp, input[placeholder*="OTP" i], '
                'input[placeholder*="verification" i], input[placeholder*="code" i], '
                'input[autocomplete="one-time-code"]'
            ).first
            if otp_input.count() > 0 and otp_input.is_visible():
                return True
            body_text = self.page.locator("body").inner_text()
            if any(x in body_text.lower() for x in ("one time password", "sent an email", "enter otp", "verification code")):
                return True
        except Exception:
            pass
        return False

    def _click_start_new_booking(self) -> bool:
        """Wait for dashboard to settle and click 'Start New Booking' button."""
        if not self.page:
            return False
        self.report("DASHBOARD", "Waiting for dashboard content and spinner to settle...")
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner, .cdk-overlay-backdrop').wait_for(state="hidden", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(2000)

        self.report("DASHBOARD", "Locating 'Start New Booking' button...")
        start_booking_btn = self.page.locator(
            'button.btn-brand-orange:has-text("Start New Booking"), '
            'button:has-text("Start New Booking"), '
            'a:has-text("Start New Booking"), '
            'button:has-text("New Booking"), '
            'button:has-text("Start new booking")'
        ).first

        try:
            start_booking_btn.wait_for(state="attached", timeout=12000)
            self.report("DASHBOARD", "Clicking 'Start New Booking'...")
            start_booking_btn.click(force=True, timeout=5000)
            self.page.wait_for_timeout(2500)
            return True
        except Exception:
            try:
                self.page.evaluate("() => { const btns = Array.from(document.querySelectorAll('button, a')); const b = btns.find(x => (x.textContent || '').includes('Start New Booking')); if(b) b.click(); }")
                self.page.wait_for_timeout(2500)
                return True
            except Exception:
                return False

    def _fill_application_detail_and_forward(self):
        """Handle Step 1 (Application Detail) and forward to next steps."""
        if self.check_pause_and_stop():
            return
        self.report("APPLICATION_DETAIL", f"Waiting for {self.cfg.APPLICATION_DETAIL_URL}...")
        app_deadline = time.monotonic() + 30
        while time.monotonic() < app_deadline:
            if self.check_pause_and_stop():
                return
            if "application-detail" in self.page.url.lower():
                break
            self.page.wait_for_timeout(1000)

        self.take_screenshot("09_application_detail_reached")
        self.report(
            "APPLICATION_DETAIL",
            "SUCCESS: Reached Appointment Details page! Now filling Centre, Category, and Sub-Category..."
        )

        if sys.platform == "win32" and not self.cfg.HEADLESS:
            bring_window_to_front_win32(self.page)

        # Fill Step 1
        self.fill_appointment_details(
            centre=self.cfg.TARGET_CITY,
            category=self.cfg.VISA_CATEGORY,
            sub_category=getattr(self.cfg, "VISA_SUB_CATEGORY", "Business Visa")
        )

        if self.check_pause_and_stop():
            return

        self._fill_your_details_and_forward()

    def _fill_your_details_and_forward(self):
        """Handle Step 2 (Your Details) and forward to Slot Booking."""
        if not self.page or self.check_pause_and_stop():
            return

        self.page.wait_for_timeout(2500)
        cur_url = self.page.url.lower()
        is_step2 = "your-details" in cur_url or "applicant" in cur_url or self.page.locator('.step-circle:has-text("2"), [class*="step"]:has-text("Your Details"), text="Your Details"').count() > 0

        if is_step2:
            self.report("YOUR_DETAILS", f"Reached Step 2 (Your Details)! Filling information for {len(self.cfg.APPLICANTS_LIST)} applicant(s)...")
            self.fill_your_details(self.cfg.APPLICANTS_LIST)
            if self.check_pause_and_stop():
                return
            self._book_slot_and_forward()
        else:
            self.report("COMPLETED", "SUCCESS: Appointment Details processed! Real browser window held open.")
            if sys.platform == "win32" and not self.cfg.HEADLESS:
                bring_window_to_front_win32(self.page)

    def _book_slot_and_forward(self):
        """Handle Step 3 (Calendar Slot Booking) and forward to Services/Payment."""
        if not self.page or self.check_pause_and_stop():
            return

        self.report("BOOK_APPOINTMENT", "Waiting for Step 3 (Appointment Calendar) to load...")
        step3_deadline = time.monotonic() + 25
        is_step3 = False
        while time.monotonic() < step3_deadline:
            if self.check_pause_and_stop():
                return
            if self.page.locator('mat-calendar, .mat-calendar').count() > 0:
                is_step3 = True
                break
            if self.page.locator('.step-circle.active:has-text("3"), [class*="step"]:has-text("Book Appointment").active').count() > 0:
                is_step3 = True
                break
            if self.page.locator('text="Select date", text="Choose appointment", text="Earliest available slot"').count() > 0:
                is_step3 = True
                break
            self.page.wait_for_timeout(1000)

        if is_step3:
            self.report("BOOK_APPOINTMENT", "Reached Step 3 (Book Appointment)! Selecting available appointment slot...")
            slot_ok = self.select_appointment_slot()
            if slot_ok:
                self.handle_services_and_payment()
            else:
                self.report("COMPLETED", "Slot search completed. Real browser window held open for review.")
                if sys.platform == "win32" and not self.cfg.HEADLESS:
                    bring_window_to_front_win32(self.page)
        else:
            self.report("COMPLETED", "SUCCESS: Booking flow executed! Real browser window held open for review.")
            if sys.platform == "win32" and not self.cfg.HEADLESS:
                bring_window_to_front_win32(self.page)

    def resume_from_current_page(self):
        """Intelligently inspect current URL and DOM, resuming automation from operator's current location."""
        if not self.page:
            return
        cur_url = self.page.url.lower()
        self.report("RESUMING", f"Inspecting current page: {self.page.url}")

        if "dashboard" in cur_url and "application-detail" not in cur_url:
            self.report("DASHBOARD", "Resumed on Dashboard. Initiating 'Start New Booking'...")
            self._click_start_new_booking()
            self._fill_application_detail_and_forward()
        elif "application-detail" in cur_url:
            self.report("APPLICATION_DETAIL", "Resumed on Application Details (Step 1)...")
            self._fill_application_detail_and_forward()
        elif "your-details" in cur_url or "applicant" in cur_url or self.page.locator('.step-circle:has-text("2"), [class*="step"]:has-text("Your Details")').count() > 0:
            self.report("YOUR_DETAILS", "Resumed on Your Details (Step 2)...")
            self._fill_your_details_and_forward()
        elif self.page.locator('mat-calendar, .mat-calendar').count() > 0 or "appointment" in cur_url:
            self.report("BOOK_APPOINTMENT", "Resumed on Slot Booking Calendar (Step 3)...")
            self._book_slot_and_forward()
        elif self._is_on_otp_screen():
            self.report("OTP", "Resumed on OTP screen. Waiting for OTP completion...")
        elif "login" in cur_url:
            self.report("LOGIN", "Resumed on Login page.")
        else:
            self.report("NAVIGATION", "Resuming workflow...")
            self._fill_application_detail_and_forward()

    def handle_services_and_payment(self) -> bool:
        """Handle Step 4 (Services) and Step 5 (Review & Payment Handover).
        
        Ensures the REAL browser window is maximized in the foreground on the user's
        screen so the user can enter card/payment details directly in the live browser.
        """
        if not self.page:
            return False

        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=5000)
        except Exception:
            pass
        cur_url = self.page.url.lower()

        # Step 4: Optional Value Added Services
        is_services = "service" in cur_url or self.page.locator('.step-circle:has-text("4"), [class*="step"]:has-text("Services"), text="Services"').count() > 0
        if is_services:
            self.report("SERVICES", "Reached Step 4 (Services). Advancing to Review & Payment...")
            self.take_screenshot("22_services_page")
            svc_continue = self.page.locator('button.btn-brand-orange:has-text("Continue"), button:has-text("Continue"), button:has-text("Skip")').first
            if svc_continue.count() > 0 and svc_continue.is_visible():
                try:
                    svc_continue.click(timeout=4000)
                except Exception:
                    svc_continue.click(force=True)
                try:
                    self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=5000)
                except Exception:
                    pass

        # Step 5: Review & Payment
        self.report("PAYMENT", "💳 FINAL STEP: Review & Payment screen reached! Maximizing real browser window in front of you...")
        self.take_screenshot("23_review_and_pay")

        # Bring the REAL browser window directly into the foreground for user interaction
        if sys.platform == "win32" and not self.cfg.HEADLESS:
            bring_window_to_front_win32(self.page)

        # Automatically check Terms and Conditions checkbox if present
        try:
            terms = self.page.locator('mat-checkbox:has-text("terms" i), mat-checkbox:has-text("agree" i), mat-checkbox input[type="checkbox"]').first
            if terms.count() > 0 and not terms.is_checked():
                terms.click()
                self.page.wait_for_timeout(500)
        except Exception:
            pass

        self.report(
            "PAYMENT",
            "💳 READY FOR USER PAYMENT: The real browser window is active in front of you. Please enter your payment details, OTP, and confirm payment in the live browser."
        )

        # Log milestone to MySQL database
        try:
            import vfs_db
            vfs_db.log_booking_result(
                applicant_name=f"{self.cfg.APPLICANT_FIRST_NAME} {self.cfg.APPLICANT_LAST_NAME}",
                passport_number=self.cfg.APPLICANT_PASSPORT_NUMBER,
                target_city=self.cfg.TARGET_CITY,
                visa_category=self.cfg.VISA_CATEGORY,
                status="PAYMENT_PENDING",
                step_reached="REVIEW_AND_PAY",
                message="Slot selected and review screen reached. Handed over to user for payment."
            )
        except Exception:
            pass

        # Keep browser open and monitor for confirmation
        payment_deadline = time.monotonic() + 1800  # Up to 30 mins for user to complete payment
        while time.monotonic() < payment_deadline and not self.stop_requested:
            cur_url = self.page.url.lower()
            if any(k in cur_url for k in ["confirmation", "success", "receipt", "appointment-confirmation"]):
                self.take_screenshot("24_booking_confirmed")
                self.report("COMPLETED", "🎉 SUCCESS: Payment processed and appointment booking confirmed!")
                try:
                    import vfs_db
                    vfs_db.log_booking_result(
                        applicant_name=f"{self.cfg.APPLICANT_FIRST_NAME} {self.cfg.APPLICANT_LAST_NAME}",
                        passport_number=self.cfg.APPLICANT_PASSPORT_NUMBER,
                        target_city=self.cfg.TARGET_CITY,
                        visa_category=self.cfg.VISA_CATEGORY,
                        status="CONFIRMED",
                        step_reached="PAYMENT_CONFIRMED",
                        message="Appointment confirmed and paid successfully."
                    )
                except Exception:
                    pass
                return True
            self.page.wait_for_timeout(2000)

        return True

    def select_mat_option(self, select_locator, target_text: str = "") -> list:
        """Click an Angular Material mat-select, retrieve options, and select target_text."""
        if not self.page:
            return []

        try:
            select_locator.scroll_into_view_if_needed()
            select_locator.click()
        except Exception:
            select_locator.click(force=True)

        try:
            self.page.locator('.cdk-overlay-container mat-option, mat-option, .mat-mdc-option').first.wait_for(state="visible", timeout=2000)
        except Exception:
            self.page.wait_for_timeout(150)

        # Check if there is an input search filter inside the dropdown overlay
        try:
            search_input = self.page.locator('.cdk-overlay-container input[type="text"], .cdk-overlay-container input[placeholder*="search" i]').first
            if search_input.count() > 0 and search_input.is_visible() and target_text:
                search_input.fill(target_text)
                self.page.wait_for_timeout(150)
        except Exception:
            pass

        options = self.page.locator('.cdk-overlay-container mat-option, mat-option, .mat-mdc-option').all()
        option_texts = []
        selected_opt = None

        # 1. Exact match (case-insensitive) - MUST BE FIRST to avoid "male" matching "female"
        for opt in options:
            txt = opt.inner_text().strip()
            option_texts.append(txt)
            if target_text and txt.lower() == target_text.strip().lower():
                selected_opt = opt
                break

        # 2. Word boundary match
        if not selected_opt and target_text:
            pattern = re.compile(rf'\b{re.escape(target_text.strip().lower())}\b', re.IGNORECASE)
            for opt in options:
                txt = opt.inner_text().strip()
                if pattern.search(txt):
                    selected_opt = opt
                    break

        # 3. Starts-with match
        if not selected_opt and target_text:
            for opt in options:
                txt = opt.inner_text().strip()
                if txt.lower().startswith(target_text.strip().lower()):
                    selected_opt = opt
                    break

        # 4. Bidirectional containment match (e.g. "D visa" in "Long Stay D visa" or vice-versa)
        if not selected_opt and target_text:
            t_low = target_text.strip().lower()
            for opt in options:
                txt = opt.inner_text().strip()
                txt_low = txt.lower()
                # Prevent "male" from matching "female"
                if t_low == "male" and "female" in txt_low:
                    continue
                if (t_low in txt_low) or (txt_low in t_low and len(txt_low) >= 3):
                    selected_opt = opt
                    break

        # 5. Fallback: keyboard typeahead
        if not selected_opt and target_text:
            try:
                self.page.keyboard.type(target_text[:3])
                self.page.wait_for_timeout(200)
                options = self.page.locator('.cdk-overlay-container mat-option, mat-option, .mat-mdc-option').all()
                for opt in options:
                    txt = opt.inner_text().strip()
                    if txt.lower() == target_text.strip().lower():
                        selected_opt = opt
                        break
            except Exception:
                pass

        # 6. Last resort substring match
        if not selected_opt and target_text:
            for opt in options:
                txt = opt.inner_text().strip()
                if target_text.lower() in txt.lower():
                    selected_opt = opt
                    break

        if selected_opt:
            self.report("FORM", f"Selected: '{selected_opt.inner_text().strip()}'")
            selected_opt.scroll_into_view_if_needed()
            selected_opt.click()
        elif options:
            first_txt = options[0].inner_text().strip()
            self.report("FORM", f"Target '{target_text}' not explicitly matched. Selecting: '{first_txt}'")
            options[0].click()

        self.page.wait_for_timeout(250)
        return option_texts

    def fill_appointment_details(self, centre: str = "", category: str = "", sub_category: str = "") -> bool:
        """Select Application Centre, Appointment Category, and Sub-Category on /application-detail."""
        if not self.page:
            return False

        raw_centre = str(centre or self.cfg.TARGET_CITY or "delhi").strip()
        raw_category = str(category or getattr(self.cfg, "VISA_CATEGORY", "Business") or "Business").strip()
        raw_sub_category = str(sub_category or getattr(self.cfg, "VISA_SUB_CATEGORY", "Business Visa") or "Business Visa").strip()

        # Official 15 VFS Global Application Centres in India
        CITY_CENTRE_NAMES = {
            "delhi": "Bulgaria Visa Application Center ,New Delhi",
            "new delhi": "Bulgaria Visa Application Center ,New Delhi",
            "mumbai": "Bulgaria Visa Application Center ,Mumbai",
            "bengaluru": "Bulgaria Visa Application Center ,Bengaluru",
            "bangalore": "Bulgaria Visa Application Center ,Bengaluru",
            "chennai": "Bulgaria Visa Application Center ,Chennai",
            "kolkata": "Bulgaria Visa Application Center ,Kolkata",
            "ahmedabad": "Bulgaria Visa Application Center ,Ahmedabad",
            "chandigarh": "Bulgaria Visa Application Center ,Chandigarh",
            "cochin": "Bulgaria Visa Application Center ,Cochin",
            "kochi": "Bulgaria Visa Application Center ,Cochin",
            "goa": "Bulgaria Visa Application Center ,Goa",
            "hyderabad": "Bulgaria Visa Application Center ,Hyderabad",
            "jaipur": "Bulgaria Visa Application Center ,Jaipur",
            "jalandhar": "Bulgaria Visa Application Center ,Jalandhar",
            "pondicherry": "Bulgaria Visa Application Center ,Pondicherry",
            "puducherry": "Bulgaria Visa Application Center ,Pondicherry",
            "pune": "Bulgaria Visa Application Center ,Pune",
            "trivandrum": "Bulgaria Visa Application Center ,Trivandrum",
            "thiruvananthapuram": "Bulgaria Visa Application Center ,Trivandrum",
        }
        target_centre = CITY_CENTRE_NAMES.get(raw_centre.lower(), raw_centre)

        # Normalize category for VFS Bulgaria Angular portal
        cat_lower = raw_category.lower()
        if "d visa" in cat_lower or "long stay" in cat_lower or "national" in cat_lower:
            target_category = "D visa"
            target_sub_category = raw_sub_category if ("business" not in raw_sub_category.lower() and "short" not in raw_sub_category.lower()) else "Long Stay D visa"
        elif "business" in cat_lower:
            target_category = "Business"
            target_sub_category = raw_sub_category if raw_sub_category and "stay" not in raw_sub_category.lower() else "Business Visa"
        elif "short" in cat_lower or "schengen" in cat_lower:
            target_category = "Short Stay"
            target_sub_category = raw_sub_category or "Tourist Visa"
        elif "seasonal" in cat_lower:
            target_category = "Seasonal worker"
            target_sub_category = "Seasonal worker"
        elif "embassy" in cat_lower:
            target_category = "Embassy approved interview"
            target_sub_category = "Embassy approved interview"
        else:
            target_category = raw_category
            target_sub_category = raw_sub_category

        self.report("FORM", f"Selecting Application Centre (target: '{target_centre}')...")

        # 1. Application Centre
        centre_select = self.page.locator('mat-select[formcontrolname="centerCode"], mat-select').first
        centre_select.wait_for(state="attached", timeout=15000)
        self.select_mat_option(centre_select, target_centre)
        self.take_screenshot("10_centre_selected")

        # Dynamic wait for Category dropdown to become active
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner, .cdk-overlay-backdrop').wait_for(state="hidden", timeout=7000)
        except Exception:
            pass
        self.page.wait_for_timeout(400)

        # 2. Appointment Category
        self.report("FORM", f"Selecting Appointment Category (target: '{target_category}')...")
        category_select = self.page.locator('mat-select[formcontrolname="categoryCode"], mat-select').nth(1)
        category_select.wait_for(state="attached", timeout=15000)
        self.select_mat_option(category_select, target_category)
        self.take_screenshot("11_category_selected")

        # Dynamic wait for Sub-Category dropdown to become active
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner, .cdk-overlay-backdrop').wait_for(state="hidden", timeout=7000)
        except Exception:
            pass
        self.page.wait_for_timeout(400)

        # 3. Sub-Category
        self.report("FORM", f"Selecting Sub-Category (target: '{target_sub_category}')...")
        sub_cat_select = self.page.locator('mat-select[formcontrolname="subCategoryCode"], mat-select').nth(2)
        sub_cat_select.wait_for(state="attached", timeout=15000)
        self.select_mat_option(sub_cat_select, target_sub_category)
        self.take_screenshot("12_sub_category_selected")
        self.take_screenshot("13_appointment_details_complete")

        # Dynamic wait for portal slot calculation / alert notice
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=6000)
        except Exception:
            pass
        self.page.wait_for_timeout(800)

        # Check for slot notice alert on page
        try:
            msg_el = self.page.locator(
                '.alert, .c-message, .c-brand-message, mat-card-subtitle, '
                'text="Earliest available slot", text="We are sorry but no appointment slots", '
                'text="no appointment slots are currently available"'
            ).first
            if msg_el.count() > 0 and msg_el.is_visible():
                slot_txt = msg_el.inner_text().strip()
                self.report("SLOT", f"Portal Availability Notice: '{slot_txt}'")
        except Exception:
            pass

        # Check if Continue button is enabled
        continue_btn = self.page.locator('button.btn-brand-orange:has-text("Continue"), button:has-text("Continue")').first
        if continue_btn.count() > 0 and continue_btn.is_visible():
            try:
                is_disabled = continue_btn.get_attribute("disabled") is not None or "disabled" in (continue_btn.get_attribute("class") or "")
            except Exception:
                is_disabled = False

            if not is_disabled:
                self.report("FORM", "Slot available! Clicking 'Continue' to advance to Your Details...")
                try:
                    continue_btn.click(timeout=4000)
                except Exception:
                    continue_btn.click(force=True)
                try:
                    self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=8000)
                except Exception:
                    pass
                self.take_screenshot("14_after_continue")
            else:
                self.report("SLOT", "Continue button is currently disabled by VFS (no slots currently available for this category).")

        return True

    def _fill_text_field(self, label_names: list, value: str, placeholder_keywords: list = None) -> bool:
        """Fill an input field identified by label text, placeholder, or formcontrolname."""
        if not self.page or not value:
            return False

        # Strategy 1: Find by label container or following input
        for lbl in label_names:
            try:
                for sel in [
                    f'//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{lbl.lower()}")]/following::input[1]',
                    f'//div[contains(@class, "form-group") and .//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{lbl.lower()}")]]//input',
                    f'mat-form-field:has-text("{lbl}") input',
                ]:
                    inp = self.page.locator(sel).first
                    if inp.count() > 0 and inp.is_visible():
                        inp.scroll_into_view_if_needed()
                        inp.fill(str(value), timeout=2500)
                        inp.dispatch_event("input")
                        inp.dispatch_event("change")
                        inp.evaluate("e => e.blur()")
                        log.info(f"Filled field '{lbl}' via label xpath with '{value}'")
                        return True
            except Exception:
                pass

        # Strategy 2: Find by Playwright get_by_label
        for lbl in label_names:
            try:
                inp = self.page.get_by_label(lbl, exact=False).first
                if inp.count() > 0 and inp.is_visible():
                    inp.scroll_into_view_if_needed()
                    inp.fill(str(value), timeout=2500)
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    inp.evaluate("e => e.blur()")
                    log.info(f"Filled field '{lbl}' via get_by_label with '{value}'")
                    return True
            except Exception:
                pass

        # Strategy 3: Find by placeholder
        if placeholder_keywords:
            for ph in placeholder_keywords:
                try:
                    inp = self.page.locator(f'input[placeholder*="{ph}" i]').first
                    if inp.count() > 0 and inp.is_visible():
                        inp.scroll_into_view_if_needed()
                        inp.fill(str(value), timeout=2500)
                        inp.dispatch_event("input")
                        inp.dispatch_event("change")
                        inp.evaluate("e => e.blur()")
                        log.info(f"Filled field via placeholder '{ph}' with '{value}'")
                        return True
                except Exception:
                    pass

        # Strategy 4: Find by formcontrolname substring
        for lbl in label_names:
            clean = re.sub(r'[^a-zA-Z]', '', lbl).lower()
            try:
                inp = self.page.locator(f'input[formcontrolname*="{clean}" i]').first
                if inp.count() > 0 and inp.is_visible():
                    inp.scroll_into_view_if_needed()
                    inp.fill(str(value), timeout=2500)
                    inp.dispatch_event("input")
                    inp.dispatch_event("change")
                    inp.evaluate("e => e.blur()")
                    log.info(f"Filled field via formcontrolname '{clean}' with '{value}'")
                    return True
            except Exception:
                pass

        return False

    def _select_dropdown_field(self, label_names: list, target_text: str) -> bool:
        """Select an option in mat-select, standard select, or custom dropdown identified by label."""
        if not self.page or not target_text:
            return False

        for lbl in label_names:
            try:
                for sel in [
                    f'//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{lbl.lower()}")]/following::*[self::mat-select or self::select or @role="combobox" or contains(@class, "select")][1]',
                    f'//div[contains(@class, "form-group") and .//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "{lbl.lower()}")]]//*[self::mat-select or self::select or @role="combobox"]',
                    f'mat-form-field:has-text("{lbl}") mat-select',
                ]:
                    dropdown = self.page.locator(sel).first
                    if dropdown.count() > 0 and dropdown.is_visible():
                        tag = dropdown.evaluate("e => e.tagName.toLowerCase()")
                        if tag == "select":
                            dropdown.select_option(label=target_text)
                            dropdown.dispatch_event("change")
                            log.info(f"Selected '{target_text}' in standard select for '{lbl}'")
                            return True
                        else:
                            dropdown.scroll_into_view_if_needed()
                            self.select_mat_option(dropdown, target_text)
                            return True
            except Exception:
                pass

        return False

    def _fill_single_applicant_form(self, app_data: dict, app_idx: int = 1, total_apps: int = 1) -> bool:
        """Fill in form fields and save a single applicant."""
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering personal info for {app_data.get('first_name', '')} {app_data.get('last_name', '')}...")

        # Wait for any loading spinner to hide
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner, .cdk-overlay-backdrop').wait_for(state="hidden", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(1500)

        # Scroll to top to ensure all fields are in viewport
        try:
            self.page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

        # Helper to set text value reliably using Playwright locator and native event dispatch
        def set_val(loc, val):
            if not loc or loc.count() == 0:
                return False
            try:
                el = loc.first
                el.scroll_into_view_if_needed()
                el.click()
                el.fill("")
                el.fill(str(val))
                el.evaluate(f"e => {{ e.removeAttribute('readonly'); e.removeAttribute('disabled'); const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set; if(s) s.call(e, '{val}'); else e.value = '{val}'; e.dispatchEvent(new Event('input', {{bubbles: true}})); e.dispatchEvent(new Event('change', {{bubbles: true}})); e.dispatchEvent(new Event('blur', {{bubbles: true}})); }}")
                return True
            except Exception as ex:
                log.debug(f"Error setting value {val}: {ex}")
                return False

        # 1. First Name
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering First Name: {app_data['first_name']}")
        fn_loc = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "first name")]/following::input[1]').first
        if fn_loc.count() == 0:
            fn_loc = self.page.locator('input[formcontrolname*="first" i]').first
        set_val(fn_loc, app_data["first_name"])

        # 2. Last Name
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Last Name: {app_data['last_name']}")
        ln_loc = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "last name")]/following::input[1]').first
        if ln_loc.count() == 0:
            ln_loc = self.page.locator('input[formcontrolname*="last" i]').first
        set_val(ln_loc, app_data["last_name"])

        # 3. Gender Dropdown
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Selecting Gender: {app_data['gender']}")
        gender_select = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "gender")]/following::mat-select[1]').first
        if gender_select.count() == 0:
            gender_select = self.page.locator('mat-select[formcontrolname*="gender" i], mat-select').first
        self.select_mat_option(gender_select, app_data["gender"])

        # 4. Date of Birth
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Date of Birth: {app_data['dob']}")
        dob_loc = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "date of birth")]/following::input[1]').first
        if dob_loc.count() == 0:
            dob_loc = self.page.locator('input[formcontrolname*="birth" i], input[formcontrolname*="dob" i]').first
        set_val(dob_loc, app_data["dob"])

        # 5. Current Nationality Dropdown
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Selecting Current Nationality: {app_data['nationality']}")
        nat_select = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "nationality")]/following::mat-select[1]').first
        if nat_select.count() == 0:
            nat_select = self.page.locator('mat-select[formcontrolname*="nationality" i], mat-select').nth(1)
        self.select_mat_option(nat_select, app_data["nationality"])

        # 6. Passport Number
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Passport Number: {app_data['passport_number']}")
        ppn_loc = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "passport number")]/following::input[1]').first
        if ppn_loc.count() == 0:
            ppn_loc = self.page.locator('input[formcontrolname*="passport" i]:not([formcontrolname*="expiry" i])').first
        set_val(ppn_loc, app_data["passport_number"])

        # 7. Passport Expiry Date (Angular Material Datepicker)
        exp_date = str(app_data.get("passport_expiry") or getattr(self.cfg, "APPLICANT_PASSPORT_EXPIRY", "20/05/2031")).strip()
        if not exp_date or exp_date == "--":
            exp_date = "20/05/2031"
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Passport Expiry Date: {exp_date}")

        exp_candidates = [
            'input[placeholder*="select the date" i]',
            'input[formcontrolname*="expir" i]',
            '//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "passport expiry")]/following::input[1]',
            '//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "expiry")]/following::input[1]',
            'mat-form-field:has-text("Passport Expiry") input',
            'mat-form-field:has-text("Expiry") input'
        ]

        exp_loc = None
        for sel in exp_candidates:
            cand = self.page.locator(sel).first
            if cand.count() > 0 and cand.is_visible():
                exp_loc = cand
                break

        if exp_loc:
            try:
                exp_loc.scroll_into_view_if_needed()
                exp_loc.click(force=True)
                self.page.wait_for_timeout(200)
                exp_loc.press("Control+a")
                exp_loc.press("Backspace")
                self.page.wait_for_timeout(100)
                exp_loc.press_sequentially(exp_date, delay=45)
                self.page.wait_for_timeout(200)
                exp_loc.evaluate(f"""e => {{
                    e.removeAttribute('readonly');
                    e.removeAttribute('disabled');
                    const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    if(s) s.call(e, '{exp_date}');
                    else e.value = '{exp_date}';
                    e.dispatchEvent(new Event('input', {{bubbles: true}}));
                    e.dispatchEvent(new Event('change', {{bubbles: true}}));
                    e.dispatchEvent(new Event('blur', {{bubbles: true}}));
                }}""")
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)
            except Exception as e:
                log.debug(f"Passport expiry input note: {e}")

        # 8. Contact Number
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Contact Number: {app_data['phone']}")
        contact_inputs = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "contact number")]/following::input[position() <= 2]').all()
        if len(contact_inputs) >= 2:
            set_val(contact_inputs[0], "91")
            set_val(contact_inputs[1], app_data["phone"])
        elif len(contact_inputs) == 1:
            set_val(contact_inputs[0], app_data["phone"])
        else:
            set_val(self.page.locator('input[formcontrolname*="contact" i]').first, app_data["phone"])

        # 9. Email
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Entering Email: {app_data['email']}")
        email_loc = self.page.locator('//label[contains(translate(., "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"), "email")]/following::input[1]').first
        if email_loc.count() == 0:
            email_loc = self.page.locator('input[type="email"], input[formcontrolname*="email" i]').first
        set_val(email_loc, app_data["email"])

        self.take_screenshot(f"15_applicant_{app_idx}_filled")
        
        # Bring real browser window directly to front so user sees all entered details
        if sys.platform == "win32" and not self.cfg.HEADLESS:
            bring_window_to_front_win32(self.page)

        # 10. Rate limit countdown check: Only wait if an actual VFS security countdown is displayed
        warning_loc = self.page.locator('text="Please wait", text="wait 30 seconds", text="wait before"').first
        if warning_loc.count() > 0 and warning_loc.is_visible():
            self.report("APPLICANT", f"[{app_idx}/{total_apps}] VFS security countdown active. Waiting for timer...")
            try:
                warning_loc.wait_for(state="hidden", timeout=35000)
            except Exception:
                pass

        # 11. Click Save button
        self.report("APPLICANT", f"[{app_idx}/{total_apps}] Submitting and saving applicant details...")
        save_btn = self.page.locator(
            'button.btn-brand-orange:has-text("Save"), '
            'button:has-text("Save"), '
            'button:has-text("Save and Continue")'
        ).first

        if save_btn.count() > 0 and save_btn.is_visible():
            try:
                save_btn.click(timeout=4000)
            except Exception:
                save_btn.click(force=True)
            try:
                self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=5000)
            except Exception:
                pass

        self.take_screenshot(f"16_applicant_{app_idx}_saved")

        # 12. Check for any modal dialogs (such as Reminder / Warning modal)
        for _ in range(3):
            modal_loc = self.page.locator('.mat-dialog-container, .cdk-overlay-pane, mat-dialog-container').first
            if modal_loc.count() > 0 and modal_loc.is_visible():
                modal_text = modal_loc.inner_text().strip()
                self.report("APPLICANT", f"Modal dialog detected: '{modal_text[:80]}'...")
                m_btn = modal_loc.locator('button:has-text("Continue"), button:has-text("OK"), button:has-text("Close"), button:has-text("Dismiss")').first
                if m_btn.count() > 0 and m_btn.is_visible():
                    m_btn.click()
                    break
            self.page.wait_for_timeout(200)

        return True

    def fill_your_details(self, applicants = None) -> bool:
        """Fill in applicant personal details on Step 2 (/your-details) for up to 5 applicants."""
        if not self.page:
            return False

        if isinstance(applicants, list):
            applicants_list = applicants
        elif isinstance(applicants, dict):
            applicants_list = [applicants]
        else:
            applicants_list = self.cfg.APPLICANTS_LIST

        # Enforce max 5
        applicants_list = applicants_list[:5]
        total_apps = len(applicants_list)
        self.report("APPLICANT", f"Starting applicant details entry for {total_apps} applicant(s)...")

        for idx, app_data in enumerate(applicants_list):
            if self.stop_requested:
                return False

            if idx > 0:
                self.report("APPLICANT", f"Adding additional applicant ({idx + 1}/{total_apps})...")
                # Click 'Add another applicant'
                add_btn_clicked = False
                for sel in [
                    'button:has-text("Add another applicant")',
                    'button:has-text("Add Applicant")',
                    'button:has-text("Add another")',
                    'a:has-text("Add another applicant")',
                    'button.btn-brand-orange:has-text("Add")',
                    'button:has-text("Add")',
                ]:
                    add_btn = self.page.locator(sel).first
                    if add_btn.count() > 0 and add_btn.is_visible():
                        try:
                            add_btn.click(timeout=4000)
                        except Exception:
                            add_btn.click(force=True)
                        add_btn_clicked = True
                        break

                if add_btn_clicked:
                    self.report("APPLICANT", f"Clicked 'Add another applicant' button for applicant {idx + 1}.")
                else:
                    self.report("APPLICANT", f"Form ready for applicant {idx + 1}.")

                try:
                    self.page.locator('input[formcontrolname*="first" i]').first.wait_for(state="visible", timeout=4000)
                except Exception:
                    self.page.wait_for_timeout(250)

            # Fill single applicant
            self._fill_single_applicant_form(app_data, app_idx=idx + 1, total_apps=total_apps)

        # 13. All applicants entered! Check for Continue button to advance to Step 3
        self.report("APPLICANT", f"All {total_apps} applicant(s) saved! Advancing to Step 3 (Slot Booking)...")
        continue_btn = self.page.locator(
            'mat-card button.btn-brand-orange:has-text("Continue"), '
            '.actions button.btn-brand-orange:has-text("Continue"), '
            'button.btn-brand-orange:has-text("Continue"), '
            'button:has-text("Continue")'
        ).first

        try:
            continue_btn.wait_for(state="visible", timeout=6000)
            try:
                continue_btn.click(timeout=4000)
            except Exception:
                continue_btn.click(force=True)
            try:
                self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=6000)
            except Exception:
                pass
            self.take_screenshot("17_after_applicant_continue")
        except Exception:
            pass

        return True

    def select_appointment_slot(self) -> bool:
        """Select appointment calendar date and time slot on Step 3."""
        if not self.page:
            return False

        self.report("SLOT", "Checking appointment calendar on Step 3...")
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner, .cdk-overlay-backdrop').wait_for(state="hidden", timeout=8000)
        except Exception:
            pass

        # Check if mat-calendar is present
        cal = self.page.locator('mat-calendar, .mat-calendar').first
        if cal.count() == 0:
            self.report("SLOT", "Waiting for appointment calendar component to render...")
            try:
                cal.wait_for(state="visible", timeout=8000)
            except Exception:
                pass

        self.take_screenshot("18_calendar_screen")

        # Look for enabled calendar cells across up to 12 months
        selected_date = False
        for month_attempt in range(12):
            available_cells = self.page.locator(
                'mat-calendar .mat-calendar-body-cell:not(.mat-calendar-body-disabled):not([aria-disabled="true"]), '
                'td.mat-calendar-body-cell:not(.mat-calendar-body-disabled), '
                '.mat-calendar-body-cell.date-available'
            ).all()

            valid_cells = []
            for cell in available_cells:
                try:
                    if cell.is_visible() and cell.inner_text().strip():
                        valid_cells.append(cell)
                except Exception:
                    pass

            if valid_cells:
                first_cell = valid_cells[0]
                cell_text = first_cell.inner_text().strip()
                self.report("SLOT", f"Selecting earliest available date: Day {cell_text}...")
                first_cell.click()
                selected_date = True
                self.take_screenshot("19_date_selected")
                break

            # Try navigating to next month
            next_month_btn = self.page.locator('button.mat-calendar-next-button, button[aria-label*="Next month" i]').first
            if next_month_btn.count() > 0 and next_month_btn.is_visible() and next_month_btn.is_enabled():
                self.report("SLOT", f"No slots in current month view. Navigating to next month (attempt {month_attempt + 1})...")
                next_month_btn.click()
                self.page.wait_for_timeout(300)
            else:
                break

        if not selected_date:
            self.report("SLOT", "No active date slot cells found in available months.")
            return False

        # Select available time slot dynamically
        self.report("SLOT", "Waiting for available time slots to appear...")
        try:
            self.page.locator('.ngx-overlay, .spinner, mat-spinner, .loading-spinner').wait_for(state="hidden", timeout=6000)
        except Exception:
            pass

        try:
            self.page.locator('mat-radio-button, .time-slot, mat-chip, .slot-item').first.wait_for(state="visible", timeout=5000)
        except Exception:
            pass

        slots = self.page.locator(
            'mat-radio-button:not([disabled]):not([aria-disabled="true"]), '
            '.time-slot:not(.disabled), '
            'mat-chip:not([disabled]), '
            'input[type="radio"]:not([disabled]) + label, '
            '.slot-item:not(.disabled)'
        ).all()

        valid_slots = [s for s in slots if s.is_visible()]
        if valid_slots:
            slot_text = valid_slots[0].inner_text().strip()
            self.report("SLOT", f"Selecting time slot: '{slot_text}'...")
            valid_slots[0].click()
            self.take_screenshot("20_slot_selected")

            # Click Continue to Step 4 / Review
            slot_continue = self.page.locator('button.btn-brand-orange:has-text("Continue"), button:has-text("Continue")').first
            if slot_continue.count() > 0 and slot_continue.is_visible():
                self.report("SLOT", "Confirming slot selection and advancing...")
                try:
                    slot_continue.click(timeout=5000)
                except Exception:
                    slot_continue.click(force=True)
                self.page.wait_for_timeout(3500)
                self.take_screenshot("21_after_slot_continue")
                return True
        else:
            self.report("SLOT", "Date selected. Waiting for time slot selection...")

        return True

