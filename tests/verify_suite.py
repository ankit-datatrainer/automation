import requests

def run_tests():
    session = requests.Session()

    # 1. Login as operator1
    print("1. Logging in as operator1...")
    login_res = session.post("http://127.0.0.1:4140/api/auth/login", json={"username": "operator1", "password": "Operator@2026!"})
    assert login_res.status_code == 200, f"Expected 200, got {login_res.status_code}"
    assert login_res.json().get("success") is True

    # 2. Get dashboard HTML
    print("2. Checking dashboard HTML...")
    dash_res = session.get("http://127.0.0.1:4140/")
    assert dash_res.status_code == 200
    html = dash_res.text

    # Assert NO database details on user page
    assert 'id="badgeDb"' not in html, "badgeDb should be removed!"
    assert 'tab-db' not in html, "tab-db should be removed!"
    assert "Hostinger MySQL DB" not in html, "Hostinger MySQL DB tab should be removed!"
    assert "cfg_db_host" not in html, "cfg_db_host input should be removed!"
    assert "cfg_db_password" not in html, "cfg_db_password input should be removed!"
    assert "cfg_db_username" not in html, "cfg_db_username input should be removed!"
    assert "DB Setup" not in html, "DB Setup button should be removed!"
    assert "Saved Profiles" in html, "Saved Profiles sidebar card should be present!"
    print("   -> PASS: User page has zero database credentials or config elements.")

    # 3. Check /api/config for operator1
    print("3. Checking /api/config security...")
    cfg_res = session.get("http://127.0.0.1:4140/api/config")
    cfg_json = cfg_res.json()
    assert "DB_PASSWORD" not in cfg_json, "DB_PASSWORD must not be returned to regular user!"
    assert "DB_HOST" not in cfg_json, "DB_HOST must not be returned to regular user!"
    assert "DB_USERNAME" not in cfg_json, "DB_USERNAME must not be returned to regular user!"
    assert "applicants" in cfg_json, "Applicants must be in cfg"
    print("   -> PASS: /api/config sanitizes database credentials for regular users.")

    # 4. Check /api/db/applicants
    print("4. Checking remote database profile switcher...")
    apps_res = session.get("http://127.0.0.1:4140/api/db/applicants")
    apps_json = apps_res.json()
    assert apps_json.get("success") is True
    profiles = apps_json.get("profiles", [])
    print(f"   -> PASS: Remote DB has {len(profiles)} applicant profiles loaded seamlessly.")
    assert len(profiles) >= 5, f"Expected >= 5 profiles, got {len(profiles)}"

    # 5. Check Super Admin
    print("5. Testing Super Admin authority and database inspector...")
    admin_sess = requests.Session()
    admin_login = admin_sess.post("http://127.0.0.1:4140/api/auth/login", json={"username": "superadmin", "password": "Admin@2026!"})
    assert admin_login.status_code == 200
    assert admin_login.json().get("success") is True

    admin_page = admin_sess.get("http://127.0.0.1:4140/admin")
    assert admin_page.status_code == 200

    users_res = admin_sess.get("http://127.0.0.1:4140/api/admin/users")
    users_json = users_res.json()
    assert users_json.get("success") is True
    assert len(users_json.get("users", [])) == 3
    print(f"   -> PASS: Super Admin has {len(users_json.get('users'))} users managed.")

    stats_res = admin_sess.get("http://127.0.0.1:4140/api/admin/db/stats")
    stats_json = stats_res.json()
    assert stats_json.get("success") is True
    print(f"   -> PASS: MariaDB stats: {stats_json.get('stats')}")

    print("\nALL AUTOMATED VERIFICATION CHECKS PASSED!")

if __name__ == "__main__":
    run_tests()
