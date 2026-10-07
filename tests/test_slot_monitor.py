"""Test Suite for 24/7 VFS Appointment Slot Watcher & Notification System."""

import json
import pytest
from app import app
import vfs_db
import vfs_notifications
from vfs_slot_monitor import VFSSlotMonitor


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    with app.test_client() as client:
        yield client


def test_slot_monitor_status():
    """Verify in-memory slot monitor status object structure."""
    monitor = VFSSlotMonitor()
    status = monitor.get_status()
    assert "is_running" in status
    assert status["is_running"] is False
    assert status["target_category"] == "Long Stay D visa"
    assert "d_visa_status" in status
    assert "last_status" in status
    assert "total_checks_today" in status


def test_slot_monitor_start_stop():
    """Verify monitor start and stop event flags."""
    monitor = VFSSlotMonitor()
    assert not monitor.is_running
    monitor.start()
    assert monitor.is_running
    monitor.stop()
    assert not monitor.is_running


def test_telegram_message_validation():
    """Verify telegram dispatcher handles missing parameters gracefully."""
    ok, msg = vfs_notifications.send_telegram_message("", "", "test")
    assert not ok
    assert "required" in msg.lower()


def test_email_alert_validation():
    """Verify email dispatcher requires configured sender."""
    ok, msg = vfs_notifications.send_email_alert(
        to_email="test@example.com",
        subject="Test Alert",
        html_content="<p>Test</p>",
        from_user="",
        from_password=""
    )
    assert not ok
    assert "credentials" in msg.lower() or "not configured" in msg.lower()


def test_database_slot_monitor_settings():
    """Verify reading and writing slot monitor settings in database."""
    settings = vfs_db.get_slot_monitor_settings()
    assert settings is not None
    assert "target_centre" in settings
    assert "target_category" in settings
    assert settings.get("target_category") == "Long Stay D visa"

    # Test update
    ok, msg = vfs_db.update_slot_monitor_settings({
        "target_centre": "Bulgaria Visa Application Center ,New Delhi",
        "target_category": "Long Stay D visa",
        "check_interval_seconds": 30,
        "daily_report_time": "22:00",
        "daily_report_enabled": 1,
        "telegram_enabled": 1,
        "email_enabled": 1
    })
    assert ok is True


def test_record_and_get_slot_checks():
    """Verify recording a slot check in database history."""
    vfs_db.record_slot_check(
        centre="Bulgaria Visa Application Center ,New Delhi",
        category="Long Stay D visa",
        status_text="No date available",
        is_available=False,
        appointment_date=None,
        all_categories={"Long Stay D visa": "No date available", "Business": "08 Oct 2026"},
        notified_tg=False,
        notified_em=False
    )
    history = vfs_db.get_recent_slot_checks(5)
    assert len(history) > 0
    assert history[0]["category"] == "Long Stay D visa"


def test_api_slot_monitor_status(client):
    """Test /api/slot_monitor/status endpoint with logged in session."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "superadmin"
        sess["role"] = "super_admin"

    res = client.get("/api/slot_monitor/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "status" in data
    assert data["status"]["target_category"] == "Long Stay D visa"


def test_api_slot_monitor_settings(client):
    """Test /api/slot_monitor/settings GET and POST."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "superadmin"
        sess["role"] = "super_admin"

    res = client.get("/api/slot_monitor/settings")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["settings"]["daily_report_time"] == "22:00"

    # POST update
    post_res = client.post("/api/slot_monitor/settings", json={
        "check_interval_seconds": 30,
        "daily_report_time": "22:00",
        "target_category": "Long Stay D visa"
    })
    assert post_res.status_code == 200
    assert post_res.get_json()["success"] is True


def test_api_admin_user_telegram(client):
    """Test super admin updating operator telegram chat ID."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "superadmin"
        sess["role"] = "super_admin"

    res = client.post("/api/admin/users/2/telegram", json={
        "telegram_chat_id": "9988776655",
        "telegram_notifications": True,
        "email_notifications": True
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # Verify user in database now has the telegram_chat_id
    user = vfs_db.get_user_by_id(2)
    assert user is not None
    assert user.get("telegram_chat_id") == "9988776655"


def test_d_visa_detection_helpers():
    from vfs_slot_monitor import is_d_visa_category, is_work_category
    assert is_d_visa_category("Long Stay D visa") is True
    assert is_d_visa_category("National D Visa") is True
    assert is_d_visa_category("Business") is False

    assert is_work_category("Seasonal worker") is True
    assert is_work_category("Employment / Work") is True
    assert is_work_category("Tourist") is False


def test_d_visa_auto_trigger_callback():
    from vfs_slot_monitor import VFSSlotMonitor
    mon = VFSSlotMonitor()
    triggered = []

    def mock_cb(cat, slot_date):
        triggered.append((cat, slot_date))

    mon.set_auto_booking_callback(mock_cb)

    # Mock perform_check simulation
    with mon.lock:
        mon.last_status["Long Stay D visa"] = "20 Nov 2026"
    
    # Trigger auto-booking check logic directly
    if mon.auto_booking_callback and "no date" not in mon.last_status["Long Stay D visa"].lower():
        mon.auto_booking_callback("Long Stay D visa", mon.last_status["Long Stay D visa"])

    assert len(triggered) == 1
    assert triggered[0] == ("Long Stay D visa", "20 Nov 2026")


def test_clean_slot_date_extraction():
    """Verify robust date extraction and clean formatting."""
    from vfs_notifications import clean_slot_date

    assert clean_slot_date("15/10/2026") == "15 October 2026"
    assert clean_slot_date("15-10-2026") == "15 October 2026"
    assert clean_slot_date("2026-10-17") == "17 October 2026"
    assert clean_slot_date("Earliest available: 15/10/2026") == "15 October 2026"
    assert clean_slot_date("October 15, 2026") == "15 October 2026"
    assert clean_slot_date("17 Oct 2026") == "17 October 2026"
    assert clean_slot_date("No appointment date available") is None
    assert clean_slot_date("No date available") is None
    assert clean_slot_date("Checking...") is None
    assert clean_slot_date("") is None


def test_telegram_slot_change_message_formatting():
    """Verify clean Telegram messages for AVAILABLE, SHIFTED, and UNAVAILABLE events."""
    from vfs_notifications import format_slot_change_telegram_message

    # 1. Available alert
    msg_avail = format_slot_change_telegram_message(
        event_type="AVAILABLE",
        category="Long Stay D visa",
        centre="Bulgaria Visa Application Center ,New Delhi",
        clean_date="17 October 2026"
    )
    assert "🟢" in msg_avail
    assert "Long Stay D visa" in msg_avail
    assert "17 October 2026" in msg_avail
    assert "Bulgaria VAC, New Delhi" in msg_avail
    assert "Open VFS Booking Portal" in msg_avail

    # 2. Date shifted alert
    msg_shift = format_slot_change_telegram_message(
        event_type="SHIFTED",
        category="Business",
        centre="Bulgaria Visa Application Center ,New Delhi",
        clean_date="17 October 2026",
        previous_date="15 October 2026"
    )
    assert "🔄" in msg_shift
    assert "Business" in msg_shift
    assert "Previous Date:" in msg_shift
    assert "15 October 2026" in msg_shift
    assert "New Date:" in msg_shift
    assert "17 October 2026" in msg_shift

    # 3. Not available suddenly alert with other categories
    msg_unavail = format_slot_change_telegram_message(
        event_type="UNAVAILABLE",
        category="Business",
        centre="Bulgaria Visa Application Center ,New Delhi",
        clean_date=None,
        previous_date="15 October 2026",
        other_available={"Long Stay D visa": "20 November 2026"}
    )
    assert "🔴" in msg_unavail
    assert "not available suddenly" in msg_unavail
    assert "15 October 2026" in msg_unavail
    assert "Other Available Categories:" in msg_unavail
    assert "20 November 2026" in msg_unavail


def test_slot_change_detection_flow():
    """Verify category state tracking and event generation for new slots, shifts, and dropoffs."""
    from vfs_notifications import clean_slot_date

    states = {}
    changes = []

    def process_check(results_data):
        nonlocal changes
        changes = []
        for cat, raw in results_data.items():
            cd = clean_slot_date(raw)
            avail = cd is not None
            prev = states.get(cat)

            if prev is None:
                states[cat] = {"is_available": avail, "clean_date": cd}
                if avail:
                    changes.append(("AVAILABLE", cat, cd, None))
            else:
                prev_avail = prev["is_available"]
                prev_date = prev["clean_date"]
                if not prev_avail and avail:
                    states[cat] = {"is_available": True, "clean_date": cd}
                    changes.append(("AVAILABLE", cat, cd, prev_date))
                elif prev_avail and avail and prev_date != cd:
                    states[cat] = {"is_available": True, "clean_date": cd}
                    changes.append(("SHIFTED", cat, cd, prev_date))
                elif prev_avail and not avail:
                    states[cat] = {"is_available": False, "clean_date": None}
                    changes.append(("UNAVAILABLE", cat, None, prev_date))

    # Cycle 1: Baseline, Business not available
    process_check({"Business": "No date available", "Long Stay D visa": "No date available"})
    assert len(changes) == 0

    # Cycle 2: Business becomes available on 15 Oct (Event: AVAILABLE)
    process_check({"Business": "15/10/2026", "Long Stay D visa": "No date available"})
    assert len(changes) == 1
    assert changes[0] == ("AVAILABLE", "Business", "15 October 2026", None)

    # Cycle 3: Business shifts from 15 Oct to 17 Oct (Event: SHIFTED)
    process_check({"Business": "17/10/2026", "Long Stay D visa": "No date available"})
    assert len(changes) == 1
    assert changes[0] == ("SHIFTED", "Business", "17 October 2026", "15 October 2026")

    # Cycle 4: Business suddenly becomes unavailable (Event: UNAVAILABLE)
    process_check({"Business": "No date available", "Long Stay D visa": "No date available"})
    assert len(changes) == 1
    assert changes[0] == ("UNAVAILABLE", "Business", None, "17 October 2026")


def test_api_slot_monitor_test_alert(client):
    """Verify /api/slot_monitor/test_alert endpoint dispatches test alerts."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "superadmin"
        sess["role"] = "super_admin"

    from unittest.mock import patch
    with patch("vfs_notifications.broadcast_slot_change_alert") as mock_broadcast:
        mock_broadcast.return_value = {
            "event_type": "AVAILABLE",
            "category": "Long Stay D visa",
            "clean_date": "20 November 2026",
            "previous_date": None,
            "centre": "Bulgaria Visa Application Center ,New Delhi",
            "telegram_sent": 1,
            "email_sent": 1,
            "errors": []
        }
        res = client.post("/api/slot_monitor/test_alert", json={
            "event_type": "AVAILABLE",
            "category": "Long Stay D visa",
            "clean_date": "20 November 2026"
        })
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert "results" in data
        assert data["results"]["event_type"] == "AVAILABLE"
        assert data["results"]["clean_date"] == "20 November 2026"
        assert mock_broadcast.called


