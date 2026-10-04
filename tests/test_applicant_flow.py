import pytest
import re
from unittest.mock import MagicMock
from config import cfg
from app import app
from vfs_automation import VFSAutomation


def test_applicant_default_details():
    """Verify applicant defaults match user's exact form details."""
    applicants = cfg.load_applicants()
    assert len(applicants) >= 1
    primary = applicants[0]
    assert primary["first_name"] == "MILAN"
    assert primary["last_name"] == "RINJALI MAGAR"
    assert primary["gender"] == "Male"
    assert primary["dob"] == "30/04/2003"
    assert primary["nationality"] == "NEPAL"
    assert primary["passport_number"] == "PA0273677"
    assert primary["passport_expiry"] == "12/04/2032"
    assert primary["phone_code"] == "977"
    assert primary["phone"] == "7838349247"
    assert primary["email"] == "oli930110@gmail.com"


def test_applicant_config_api_flow():
    """Verify GET and POST /api/config with applicants list."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "admin"
        sess["role"] = "super_admin"

    # GET config
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.get_json()
    assert "applicants" in data
    assert len(data["applicants"]) >= 1
    assert data["applicants"][0]["first_name"] == "MILAN"

    # POST update
    new_apps = [{
        "first_name": "MILAN",
        "last_name": "RINJALI MAGAR",
        "gender": "Male",
        "dob": "30/04/2003",
        "nationality": "NEPAL",
        "passport_number": "PA0273677",
        "passport_expiry": "12/04/2032",
        "phone_code": "977",
        "phone": "7838349247",
        "email": "oli930110@gmail.com"
    }]
    post_res = client.post("/api/config", json={"applicants": new_apps})
    assert post_res.status_code == 200
    pdata = post_res.get_json()
    assert pdata["success"] is True
    assert pdata["applicants"][0]["first_name"] == "MILAN"


def test_security_countdown_regex():
    """Verify regex patterns match VFS rate limit security countdown text."""
    pattern = re.compile(r"(seconds before continuing|Please wait \d+)", re.IGNORECASE)
    assert pattern.search("Please wait 9 seconds before continuing")
    assert pattern.search("Please wait 25 seconds before continuing")
    assert pattern.search("Please wait 30 seconds before continuing")
    assert pattern.search("Please wait 1 seconds before continuing")
    assert not pattern.search("Please fill in below information")


def test_vfs_security_countdown_wait_helper():
    """Verify VFSAutomation._wait_for_vfs_security_countdown handles presence and clearing of overlay."""
    automation = VFSAutomation(config=cfg)
    # When page is None
    assert automation._wait_for_vfs_security_countdown() is False

    # Mock page with countdown locator
    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_filtered = MagicMock()
    mock_filtered.first = mock_filtered
    mock_filtered.count.return_value = 1
    # First visible, then hidden
    mock_filtered.is_visible.side_effect = [True, False]
    mock_filtered.inner_text.return_value = "Please wait 9 seconds before continuing"

    mock_page.locator.return_value = mock_locator
    mock_locator.filter.return_value = mock_filtered

    automation.page = mock_page
    handled = automation._wait_for_vfs_security_countdown(timeout_sec=5)
    assert handled is True
