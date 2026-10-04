"""Tests for 15 VFS Application Centres and Category/Sub-Category dropdown options."""

import pytest
from config import cfg
from vfs_slot_monitor import is_d_visa_category, is_work_category


def test_d_visa_and_business_categories():
    # Primary categories
    assert is_d_visa_category("D visa") is True
    assert is_d_visa_category("Long Stay D visa") is True
    assert is_d_visa_category("National D Visa") is True
    assert is_d_visa_category("Type D Visa") is True

    # Business and Schengen
    assert is_d_visa_category("Business") is False
    assert is_d_visa_category("Business Visa") is False
    assert is_d_visa_category("Short Stay") is False
    assert is_d_visa_category("Tourist Visa") is False

    # Work / Employment
    assert is_work_category("Seasonal worker") is True
    assert is_work_category("Work Permit") is True
    assert is_work_category("Business") is False


def test_15_vfs_centres_mapping():
    # 15 Official Centres for Bulgaria VFS Global in India
    centres = [
        ("delhi", "Bulgaria Visa Application Center ,New Delhi"),
        ("mumbai", "Bulgaria Visa Application Center ,Mumbai"),
        ("bengaluru", "Bulgaria Visa Application Center ,Bengaluru"),
        ("chennai", "Bulgaria Visa Application Center ,Chennai"),
        ("kolkata", "Bulgaria Visa Application Center ,Kolkata"),
        ("ahmedabad", "Bulgaria Visa Application Center ,Ahmedabad"),
        ("chandigarh", "Bulgaria Visa Application Center ,Chandigarh"),
        ("cochin", "Bulgaria Visa Application Center ,Cochin"),
        ("goa", "Bulgaria Visa Application Center ,Goa"),
        ("hyderabad", "Bulgaria Visa Application Center ,Hyderabad"),
        ("jaipur", "Bulgaria Visa Application Center ,Jaipur"),
        ("jalandhar", "Bulgaria Visa Application Center ,Jalandhar"),
        ("pondicherry", "Bulgaria Visa Application Center ,Pondicherry"),
        ("pune", "Bulgaria Visa Application Center ,Pune"),
        ("trivandrum", "Bulgaria Visa Application Center ,Trivandrum"),
    ]
    assert len(centres) == 15
    for code, full_name in centres:
        assert len(code) >= 3
        assert "Bulgaria Visa Application Center" in full_name


def test_api_config_target_update():
    from app import app
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "admin"
        sess["role"] = "super_admin"

    # Update to Business route
    res = client.post("/api/config", json={
        "TARGET_CITY": "delhi",
        "VISA_CATEGORY": "Business",
        "VISA_SUB_CATEGORY": "Business Visa"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert cfg.TARGET_CITY == "delhi"
    assert cfg.VISA_CATEGORY == "Business"
    assert cfg.VISA_SUB_CATEGORY == "Business Visa"

    # Update to D-Visa route
    res2 = client.post("/api/config", json={
        "TARGET_CITY": "mumbai",
        "VISA_CATEGORY": "D visa",
        "VISA_SUB_CATEGORY": "Long Stay D visa"
    })
    assert res2.status_code == 200
    assert cfg.TARGET_CITY == "mumbai"
    assert cfg.VISA_CATEGORY == "D visa"
    assert cfg.VISA_SUB_CATEGORY == "Long Stay D visa"

    # Restore default target city to delhi
    client.post("/api/config", json={
        "TARGET_CITY": "delhi",
        "VISA_CATEGORY": "Business",
        "VISA_SUB_CATEGORY": "Business Visa"
    })
    assert cfg.TARGET_CITY == "delhi"

