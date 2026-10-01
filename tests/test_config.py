from config import cfg

def test_config_loaded():
    assert cfg.VFS_EMAIL == "ankit.developer2004@gmail.com"
    assert cfg.VFS_PASSWORD == "@Nkit55555"
    assert cfg.VFS_GMAIL_USER == "ankit.developer2004@gmail.com"
    assert cfg.VFS_GMAIL_APP_PASSWORD == "dbfq cwtw nfwm ouuk"
    assert cfg.BOOK_APPOINTMENT_URL == "https://visa.vfsglobal.com/ind/en/bgr/book-an-appointment"
    assert cfg.LOGIN_URL == "https://visa.vfsglobal.com/ind/en/bgr/login"
    assert cfg.DASHBOARD_URL == "https://visa.vfsglobal.com/ind/en/bgr/dashboard"
    assert cfg.APPLICATION_DETAIL_URL == "https://visa.vfsglobal.com/ind/en/bgr/application-detail"
