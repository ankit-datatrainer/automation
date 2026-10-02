import sys
import os

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

def test_operator_and_watcher():
    session = requests.Session()

    print("\n--- 1. Testing Operator Login ---")
    login_res = session.post("http://127.0.0.1:4140/api/auth/login", json={
        "username": "operator1",
        "password": "Operator@2026!"
    })
    assert login_res.status_code == 200, f"Login failed: {login_res.text}"
    assert login_res.json().get("success") is True, "Login success must be True"
    print("[OK] Operator1 logged in successfully.")

    print("\n--- 2. Testing Booking Automation State (Must NOT run automatically) ---")
    status_res = session.get("http://127.0.0.1:4140/api/status")
    assert status_res.status_code == 200
    st = status_res.json().get("state", {})
    print(f"Current Automation Status: {st.get('status')}")
    assert st.get("status") in ["STOPPED", "IDLE", "NONE"], f"Automation must NOT be running on startup! Got: {st.get('status')}"
    print("✅ Booking automation is strictly IDLE until user clicks Start.")

    print("\n--- 3. Verifying Operator Dashboard UI Elements ---")
    dash_res = session.get("http://127.0.0.1:4140/")
    assert dash_res.status_code == 200
    html = dash_res.text

    # Operator Identity
    assert "operator1" in html or "Operator 1" in html, "Operator name must appear in UI"
    assert "7815919062" in html, "Operator Telegram Chat ID (7815919062) must appear in UI"
    assert "@VFSAIAgentbot" in html, "Telegram bot handle (@VFSAIAgentbot) must appear in UI"
    assert "Ping Telegram" in html, "Ping Telegram button must appear in UI"
    print("✅ Operator ribbon with Telegram details and Ping button verified.")

    # 24/7 Watcher Card
    assert "24/7 LIVE APPOINTMENT WATCHER" in html, "24/7 Watcher banner must be present"
    assert "Bulgaria Slot Watcher" in html, "Bulgaria Slot Watcher title must be present"
    assert "Long Stay D visa" in html, "Long Stay D visa target category must be present"
    assert "Manual Click-To-Run Protection Active" in html, "Manual click protection banner must be present"
    print("✅ 24/7 Watcher Card and Manual Click-To-Run notice verified.")

    print("\n--- 4. Testing Slot Monitor Status API ---")
    mon_res = session.get("http://127.0.0.1:4140/api/slot_monitor/status")
    assert mon_res.status_code == 200
    mon_data = mon_res.json()
    assert mon_data.get("success") is True
    print(f"Slot Watcher status: {mon_data.get('status')}")
    print("✅ Slot Monitor status API is working.")

    print("\n--- 5. Testing Operator Direct Telegram Ping API ---")
    ping_res = session.post("http://127.0.0.1:4140/api/user/test_telegram")
    assert ping_res.status_code == 200, f"Ping failed: {ping_res.text}"
    ping_data = ping_res.json()
    assert ping_data.get("success") is True
    print(f"Telegram Ping response: {ping_data.get('message')}")
    print("✅ Operator direct Telegram ping delivered successfully!")

    print("\n🎉 ALL TESTS PASSED! APPLICATION & OPERATOR 24/7 WATCHER DASHBOARD ARE 100% OPERATIONAL.\n")

if __name__ == "__main__":
    test_operator_and_watcher()
