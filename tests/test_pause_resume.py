import pytest
from app import app, state
import app as app_module
from unittest.mock import MagicMock


@pytest.fixture
def auth_client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["username"] = "operator1"
            sess["full_name"] = "Automation Operator 1"
            sess["role"] = "user"
        yield client


def test_status_api_structure(auth_client):
    rv = auth_client.get("/api/status")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "state" in data
    assert "is_paused" in data["state"]
    assert "is_otp_prompt" in data["state"]
    assert "active_url" in data["state"]


def test_pause_endpoint_when_running(auth_client):
    mock_automation = MagicMock()
    mock_automation.is_paused = False
    app_module.active_automation = mock_automation
    state["status"] = "RUNNING"

    rv = auth_client.post("/api/pause")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert mock_automation.pause.called
    assert state["status"] == "PAUSED"


def test_resume_endpoint_when_paused(auth_client):
    mock_automation = MagicMock()
    mock_automation.is_paused = True
    app_module.active_automation = mock_automation
    state["status"] = "PAUSED"

    rv = auth_client.post("/api/resume")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert mock_automation.resume.called
    assert state["status"] == "RUNNING"


def test_submit_otp_endpoint(auth_client):
    mock_automation = MagicMock()
    mock_automation.page = MagicMock()
    mock_automation.submit_manual_otp.return_value = True
    app_module.active_automation = mock_automation

    rv = auth_client.post("/api/submit_otp", json={"otp": "123456"})
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    mock_automation.submit_manual_otp.assert_called_with("123456")


def test_submit_otp_validation(auth_client):
    rv = auth_client.post("/api/submit_otp", json={"otp": ""})
    assert rv.status_code == 400
    data = rv.get_json()
    assert data["success"] is False


def test_bring_to_front_endpoint(auth_client):
    rv = auth_client.post("/api/bring_to_front")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
