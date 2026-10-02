"""End-to-end browser verification script for 24/7 Slot Watcher and Operator Telegram configuration."""

import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

def verify_ui():
    print("🚀 Starting UI verification on http://localhost:4140...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. Login
        print("1. Opening /login...")
        page.goto("http://localhost:4140/login", wait_until="domcontentloaded")
        page.fill("#username", "superadmin")
        page.fill("#password", "Admin@2026!")
        page.click("#btnSubmit")
        page.wait_for_url("**/admin", timeout=8000)
        print("✅ Logged in successfully as superadmin -> redirected to /admin")

        # 2. Check Admin Page Tabs
        print("2. Verifying Admin Portal tabs and Telegram column...")
        page.wait_for_selector(".admin-tabs")
        
        # Verify 4 tabs exist
        tabs = page.locator(".tab-btn").all_text_contents()
        print(f"   Tabs found: {tabs}")
        assert any("24/7 Slot Watcher" in t for t in tabs), "Watcher tab missing!"

        # Verify Telegram column in Users table
        headers = page.locator(".admin-table th").all_text_contents()
        print(f"   User table headers: {headers}")
        assert any("Telegram" in h for h in headers), "Telegram column missing from users table!"

        # 3. Open Operator Telegram Modal
        print("3. Testing Operator Telegram Modal...")
        page.wait_for_selector(".btn-user-telegram", timeout=10000)
        page.locator(".btn-user-telegram").first.click()
        page.wait_for_selector("#userTelegramModal", state="visible")
        assert page.locator("#tgUserChatId").is_visible(), "Telegram Chat ID input not visible in modal!"
        print("✅ Operator Telegram modal opened successfully.")
        page.locator("#userTelegramModal .admin-modal-close").click()
        time.sleep(0.5)

        # 4. Switch to Watcher Tab
        print("4. Switching to '🚨 24/7 Slot Watcher & Telegram' tab...")
        page.locator(".tab-btn:has-text('24/7 Slot Watcher')").click()
        page.wait_for_selector("#tab-watcher", state="visible")
        page.screenshot(path="data/screenshots/admin_watcher_tab.png")
        print("✅ Admin Watcher tab loaded. Screenshot saved to data/screenshots/admin_watcher_tab.png")

        # Verify Watcher Elements
        assert page.locator("#adminDVisaBadge").is_visible(), "Long Stay D visa badge not visible in admin!"
        assert page.locator("#slotSettingsForm").is_visible(), "Slot settings form not visible in admin!"
        assert page.locator("#cfg_daily_report_time").is_visible(), "Daily report time input not visible!"

        # 5. Navigate to Operator Dashboard (/)
        print("5. Navigating to main operator dashboard (http://localhost:4140/)...")
        page.goto("http://localhost:4140/", wait_until="domcontentloaded")
        time.sleep(2)
        page.screenshot(path="data/screenshots/operator_dashboard.png")
        print("✅ Operator dashboard loaded. Screenshot saved to data/screenshots/operator_dashboard.png")

        # Verify Slot Watcher Card on Operator Dashboard
        assert page.locator("#userDVisaBadge").is_visible(), "Long Stay D visa badge not visible on main dashboard!"
        assert page.locator("#btnUserWatcherStart").is_visible() or page.locator("#btnUserWatcherStop").is_visible(), "Watcher Start/Stop buttons missing on main dashboard!"
        
        # Test Operator Alerts Modal
        page.click("button:has-text('📱 My Alerts')")
        page.wait_for_selector("#operatorNotificationModal.open", state="visible")
        assert page.locator("#op_telegram_chat_id").is_visible(), "Operator Telegram Chat ID input not visible!"
        page.locator("#operatorNotificationModal .btn-close").click()
        page.wait_for_selector("#operatorNotificationModal:not(.open)")
        print("✅ Operator alerts modal tested successfully.")

        context.close()
        browser.close()
        print("\n🎉 ALL UI & END-TO-END BROWSER CHECKS PASSED WITH 100% SUCCESS!")

if __name__ == "__main__":
    verify_ui()
