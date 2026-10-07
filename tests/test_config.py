from config import cfg, ENV_PATH

def test_config_loaded():
    cfg._vfs_email = None
    cfg._vfs_password = None
    cfg._vfs_gmail_user = None
    cfg._vfs_gmail_app_password = None
    assert cfg.VFS_EMAIL == "oli930110@gmail.com"
    assert cfg.VFS_PASSWORD == "Milan@123"
    assert cfg.VFS_GMAIL_USER == "ankit.developer2004@gmail.com"
    assert cfg.VFS_GMAIL_APP_PASSWORD == "dbfq cwtw nfwm ouuk"
    assert cfg.BOOK_APPOINTMENT_URL == "https://visa.vfsglobal.com/ind/en/bgr/book-an-appointment"
    assert cfg.LOGIN_URL == "https://visa.vfsglobal.com/ind/en/bgr/login"
    assert cfg.DASHBOARD_URL == "https://visa.vfsglobal.com/ind/en/bgr/dashboard"
    assert cfg.APPLICATION_DETAIL_URL == "https://visa.vfsglobal.com/ind/en/bgr/application-detail"

def test_credentials_not_in_env_file():
    if ENV_PATH.exists():
        content = ENV_PATH.read_text(encoding="utf-8")
        assert "VFS_EMAIL=" not in content, "VFS_EMAIL must NOT appear in .env"
        assert "VFS_PASSWORD=" not in content, "VFS_PASSWORD must NOT appear in .env"
        assert "VFS_GMAIL_USER=" not in content, "VFS_GMAIL_USER must NOT appear in .env"
        assert "VFS_GMAIL_APP_PASSWORD=" not in content, "VFS_GMAIL_APP_PASSWORD must NOT appear in .env"
