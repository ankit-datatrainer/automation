import pytest
from app import app
import vfs_db
from config import cfg, ENV_PATH


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_super_admin_vfs_accounts_api(client):
    """Super admin can view all VFS accounts and users."""
    # Login as superadmin
    login_res = client.post("/api/auth/login", json={
        "username": "superadmin",
        "password": "Admin@2026!"
    })
    assert login_res.status_code == 200

    # Get admin VFS accounts
    res = client.get("/api/admin/vfs_accounts")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "accounts" in data
    assert len(data["accounts"]) >= 1
    assert "users" in data


def test_operator_vfs_accounts_api(client):
    """Regular operator can view assigned and shared VFS accounts."""
    # Login as operator1
    login_res = client.post("/api/auth/login", json={
        "username": "operator1",
        "password": "Operator@2026!"
    })
    assert login_res.status_code == 200

    # Get operator VFS accounts
    res = client.get("/api/vfs_accounts")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "accounts" in data
    assert data["active_account_id"] is not None


def test_select_active_vfs_account_api(client):
    """Switching active VFS account updates active memory config dynamically."""
    login_res = client.post("/api/auth/login", json={
        "username": "superadmin",
        "password": "Admin@2026!"
    })
    assert login_res.status_code == 200

    accounts = vfs_db.get_all_vfs_accounts()
    assert len(accounts) >= 1
    target_account = accounts[0]

    # Select target account
    res = client.post("/api/vfs_accounts/select", json={
        "account_id": target_account["id"]
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["account"]["vfs_email"] == target_account["vfs_email"]

    # Verify config dynamic resolution
    assert cfg.VFS_EMAIL == target_account["vfs_email"]


def test_save_vfs_account_api(client):
    """Super Admin can save a VFS account to database."""
    login_res = client.post("/api/auth/login", json={
        "username": "superadmin",
        "password": "Admin@2026!"
    })
    assert login_res.status_code == 200

    test_email = "test.operator.vfs@example.com"
    res = client.post("/api/admin/vfs_accounts", json={
        "account_name": "Test Automation Unit",
        "vfs_email": test_email,
        "vfs_password": "TestPassword123!",
        "gmail_user": "ankit.developer2004@gmail.com",
        "gmail_app_password": "dbfq cwtw nfwm ouuk",
        "status": "active"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    new_id = data["account_id"]

    # Clean up test account
    vfs_db.delete_vfs_account(new_id, is_admin=True)


def test_gmail_otp_connection_verification():
    """Live IMAP connection test with known credentials succeeds."""
    ok, msg = vfs_db.test_gmail_imap_credentials(
        "ankit.developer2004@gmail.com",
        "dbfq cwtw nfwm ouuk"
    )
    assert ok is True
    assert "verified" in msg.lower() or "inbox" in msg.lower()


def test_config_update_never_writes_sensitive_keys():
    """update_config must never write VFS credentials to .env file."""
    orig_email = cfg.VFS_EMAIL
    orig_password = cfg.VFS_PASSWORD
    try:
        cfg.update_config({
            "VFS_EMAIL": "secret_vfs@example.com",
            "VFS_PASSWORD": "SecretPassword123",
            "TARGET_CITY": "delhi"
        })

        if ENV_PATH.exists():
            content = ENV_PATH.read_text(encoding="utf-8")
            assert "secret_vfs@example.com" not in content
            assert "SecretPassword123" not in content
            assert "VFS_EMAIL=" not in content
            assert "VFS_PASSWORD=" not in content
    finally:
        cfg.update_config({
            "VFS_EMAIL": orig_email,
            "VFS_PASSWORD": orig_password,
        })
        cfg._vfs_email = None
        cfg._vfs_password = None
