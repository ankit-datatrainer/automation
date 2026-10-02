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
