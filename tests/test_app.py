import pytest
from app import app
import vfs_db

@pytest.fixture(autouse=True)
def init_database():
    vfs_db.init_db()

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    with app.test_client() as client:
        yield client

@pytest.fixture
def auth_client(client):
    """Client authenticated as Super Admin."""
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["username"] = "superadmin"
        sess["full_name"] = "Super Administrator"
        sess["role"] = "super_admin"
    return client

@pytest.fixture
def user_client(client):
    """Client authenticated as Normal Operator."""
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["username"] = "operator1"
        sess["full_name"] = "Automation Operator 1"
        sess["role"] = "user"
    return client


def test_login_page_renders(client):
    rv = client.get("/login")
    assert rv.status_code == 200
    assert b"Sign In" in rv.data
    assert b"VFS Global Suite" in rv.data


def test_auth_login_api_success(client):
    rv = client.post("/api/auth/login", json={
        "username": "superadmin",
        "password": "Admin@2026!"
    })
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert data["user"]["role"] == "super_admin"


def test_auth_login_api_operator(client):
    rv = client.post("/api/auth/login", json={
        "username": "ankit.developer2004@gmail.com",
        "password": "Dev@2026"
    })
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert data["user"]["role"] == "user"


def test_auth_login_api_failure(client):
    rv = client.post("/api/auth/login", json={
        "username": "superadmin",
        "password": "WrongPassword!"
    })
    assert rv.status_code == 401
    data = rv.get_json()
    assert data["success"] is False


def test_unauthenticated_redirect(client):
    rv = client.get("/")
    assert rv.status_code == 302
    assert "/login" in rv.headers["Location"]


def test_index_route_superadmin_redirect(auth_client):
    # Super admin cannot open operator booking dashboard; redirected to /admin
    rv_admin = auth_client.get("/")
    assert rv_admin.status_code == 302
    assert "/admin" in rv_admin.headers["Location"]


def test_index_route_operator_access(user_client):
    # Normal operator can open booking dashboard
    rv_user = user_client.get("/")
    assert rv_user.status_code == 200
    assert b"VFS Global Automation Suite" in rv_user.data
    assert b"Start Automation" in rv_user.data


def test_admin_portal_access_superadmin(auth_client):
    # Super Admin can access /admin
    rv_admin = auth_client.get("/admin")
    assert rv_admin.status_code == 200
    assert b"Super Admin Control Center" in rv_admin.data


def test_admin_portal_access_forbidden_for_user(user_client):
    # Normal user gets 403 Forbidden
    rv_user = user_client.get("/admin")
    assert rv_user.status_code == 403
    assert b"Access Restricted" in rv_user.data


def test_status_api(auth_client):
    rv = auth_client.get("/api/status")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "state" in data
    assert data["state"]["status"] in ("IDLE", "RUNNING", "COMPLETED", "STOPPED")
    assert data["target"]["city"] == "delhi"
    assert "applicants" in data
    assert data["applicant_count"] >= 1


def test_live_route_superadmin_redirect(auth_client):
    # Super Admin is redirected away from live booking view
    rv_admin = auth_client.get("/live")
    assert rv_admin.status_code == 302
    assert "/admin" in rv_admin.headers["Location"]


def test_live_route_operator_access(user_client):
    # Operator gets full live screen
    rv_user = user_client.get("/live")
    assert rv_user.status_code == 200
    assert b"Crystal Clear Live View" in rv_user.data
    assert b"streamImg" in rv_user.data


def test_admin_users_api(auth_client):
    rv = auth_client.get("/api/admin/users")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["success"] is True
    assert len(data["users"]) >= 3
    usernames = [u["username"] for u in data["users"]]
    assert "superadmin" in usernames
    assert "ankit" in usernames or "operator1" in usernames
    assert "vikas" in usernames or "operator2" in usernames


def test_multi_applicants_config(auth_client):
    sample_applicants = [
        {
            "first_name": f"Applicant{i}",
            "last_name": f"Test{i}",
            "gender": "Male" if i % 2 == 0 else "Female",
            "dob": "10/10/1990",
            "nationality": "India",
            "passport_number": f"P123456{i}",
            "passport_expiry": "10/10/2030",
            "phone": "9876543210",
            "email": f"applicant{i}@example.com"
        }
        for i in range(1, 6)
    ]
    rv = auth_client.post("/api/config", json={"applicants": sample_applicants})
    assert rv.status_code == 200
    res = rv.get_json()
    assert res["success"] is True
    assert len(res["applicants"]) == 5

    get_rv = auth_client.get("/api/config")
    get_data = get_rv.get_json()
    assert len(get_data["applicants"]) == 5
    assert get_data["applicants"][0]["first_name"] == "Applicant1"

    # Restore default primary applicant
    auth_client.post("/api/config", json={"applicants": [{
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
    }]})
