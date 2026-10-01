import pytest
from app import app

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_index_route(client):
    rv = client.get("/")
    assert rv.status_code == 200
    assert b"VFS Global Automation Suite" in rv.data
    assert b"Start Automation" in rv.data

def test_status_api(client):
    rv = client.get("/api/status")
    assert rv.status_code == 200
    data = rv.get_json()
    assert "state" in data
    assert data["state"]["status"] in ("IDLE", "RUNNING", "COMPLETED", "STOPPED")
    assert data["target"]["city"] == "delhi"
    assert "applicants" in data
    assert data["applicant_count"] >= 1


def test_live_route(client):
    rv = client.get("/live")
    assert rv.status_code == 200
    assert b"Crystal Clear Live View" in rv.data
    assert b"streamImg" in rv.data


def test_multi_applicants_config(client):
    # Test setting up to 5 applicants
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
    rv = client.post("/api/config", json={"applicants": sample_applicants})
    assert rv.status_code == 200
    res = rv.get_json()
    assert res["success"] is True
    assert len(res["applicants"]) == 5

    # Check via GET
    get_rv = client.get("/api/config")
    get_data = get_rv.get_json()
    assert len(get_data["applicants"]) == 5
    assert get_data["applicants"][0]["first_name"] == "Applicant1"
    assert get_data["applicants"][4]["first_name"] == "Applicant5"

    # Restore default primary applicant
    client.post("/api/config", json={"applicants": [{
        "first_name": "Ankit",
        "last_name": "Sharma",
        "gender": "Male",
        "dob": "15/06/1995",
        "nationality": "India",
        "passport_number": "Z1234567",
        "passport_expiry": "20/05/2031",
        "phone": "7838349247",
        "email": "ankit.developer2004@gmail.com"
    }]})
