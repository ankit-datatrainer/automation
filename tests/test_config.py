from config import cfg, ENV_PATH

def test_config_loaded():
    cfg._vfs_email = None
    cfg._vfs_password = None
    cfg._vfs_gmail_user = None
    cfg._vfs_gmail_app_password = None
    assert cfg.VFS_EMAIL == "oli930110@gmail.com"
    assert cfg.VFS_PASSWORD == "Milan@123"
    assert cfg.VFS_GMAIL_USER in ("mytutorankit@gmail.com", "ankit.developer2004@gmail.com")
    assert cfg.VFS_GMAIL_APP_PASSWORD in ("hwzw lrzx xovu ybgs", "dbfq cwtw nfwm ouuk")
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
        assert "APPLICANT_FIRST_NAME=" not in content, "Applicant details must NOT appear in .env"
        assert "APPLICANT_PASSPORT_NUMBER=" not in content, "Applicant passport must NOT appear in .env"
        assert "TARGET_CITY=" not in content, "Target configuration must NOT appear in .env"
        assert "VISA_CATEGORY=" not in content, "Visa category must NOT appear in .env"
        assert "TELEGRAM_BOT_TOKEN=" not in content, "Telegram bot token must NOT appear in .env"
        assert "TELEGRAM_CHAT_ID=" not in content, "Telegram chat ID must NOT appear in .env"
