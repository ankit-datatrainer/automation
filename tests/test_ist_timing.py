import pytest
from datetime import datetime, timezone, timedelta
import time_utils
import requests
import vfs_slot_monitor
import vfs_db

def test_time_utils_ist():
    now_ist = time_utils.get_ist_now()
    assert now_ist.tzinfo is not None
    assert now_ist.utcoffset() == timedelta(hours=5, minutes=30)
    
    dt_str = time_utils.format_ist_dt()
    assert dt_str.endswith("IST")
    
    time_str = time_utils.format_ist_time()
    assert time_str.endswith("IST")
    
    disp_str = time_utils.format_ist_display()
    assert "IST" in disp_str
    
    hm_str = time_utils.format_ist_hm()
    assert len(hm_str) == 5 and hm_str[2] == ":"

def test_slot_monitor_status_ist():
    status = vfs_slot_monitor.slot_monitor.get_status()
    assert "d_visa_status" in status
    if status.get("last_check_time"):
        assert "IST" in status["last_check_time"]

def test_pages_have_ist():
    from app import app
    client = app.test_client()
    # Login
    login_resp = client.post("/api/auth/login", json={
        "username": "operator1",
        "password": "Operator@2026!"
    })
    assert login_resp.status_code == 200
    
    # 1. Main Operator Dashboard
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "clockIstTime" in html, "IST clock must be in operator dashboard header"
    assert "IST" in html, "'IST' timezone indicator must appear on dashboard"
    assert "10:00 PM (22:00) IST" in html or "22:00 IST" in html, "Daily summary notice must specify 22:00 IST"
    
    # 2. Slot monitor status API
    stat_res = client.get("/api/slot_monitor/status")
    assert stat_res.status_code == 200
    data = stat_res.get_json()
    assert data["success"] is True

def test_db_slot_checks_ist():
    checks = vfs_db.get_recent_slot_checks(5)
    for c in checks:
        if c.get("checked_at"):
            assert "IST" in str(c["checked_at"])
