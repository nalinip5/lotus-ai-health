"""Sign-in and role-based access: only admins and staff can reach cases and appeal approvals."""
import pytest
from fastapi.testclient import TestClient

ADMIN = {"email": "admin@test.example", "password": "admin-pass-123"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    from lotus import config
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "simulator_enabled", False)
    monkeypatch.setenv("LOTUS_ADMIN_EMAIL", ADMIN["email"])
    monkeypatch.setenv("LOTUS_ADMIN_PASSWORD", ADMIN["password"])
    from lotus.main import app
    return app


def test_anonymous_is_locked_out(app):
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        for path in ("/api/dashboard", "/api/cases", "/api/appeals", "/api/cases/CASE-1001",
                     "/api/notifications", "/api/users", "/api/auth/me"):
            assert c.get(path).status_code == 401, path
        assert c.post("/api/cases/CASE-1001/appeal/decision", json={"action": "approve"}).status_code == 401
        # A forged cookie is rejected.
        c.cookies.set("lotus_session", "eyJlIjoiYWRtaW5AdGVzdC5leGFtcGxlIn0.deadbeef")
        assert c.get("/api/dashboard").status_code == 401


def test_staff_can_approve_but_not_manage_team(app):
    with TestClient(app) as admin:
        assert admin.post("/api/auth/login", json=ADMIN).status_code == 200
        r = admin.post("/api/users", json={"email": "Sam@Clinic.example", "name": "Sam Staff", "role": "staff",
                                           "temporary_password": "temp-pass-456"})
        assert r.status_code == 200 and r.json()["must_change_password"] is True
        assert admin.post("/api/users", json={"email": "x@y.z", "name": "x", "role": "staff",
                                              "temporary_password": "short"}).status_code == 400

        with TestClient(app) as staff:
            assert staff.post("/api/auth/login", json={"email": "sam@clinic.example",
                                                       "password": "temp-pass-456"}).status_code == 200
            assert staff.get("/api/users").status_code == 403
            pending = staff.get("/api/appeals").json()["pending"]
            cid = next(p["id"] for p in pending if p["triage"]["confidence"] > 0.8)
            r = staff.post(f"/api/cases/{cid}/appeal/prepare")
            assert r.status_code == 200
            r = staff.post(f"/api/cases/{cid}/appeal/decision", json={"action": "approve"})
            assert r.status_code == 200
            case = r.json()["case"]
            assert case["appeal"]["status"] == "sent"
            assert case["appeal"]["decided_by"] == "Sam Staff (staff)"  # audit uses the signed-in identity

            # Changing the password clears the must-change flag.
            r = staff.post("/api/auth/password", json={"current_password": "temp-pass-456",
                                                       "new_password": "a-better-pass-789"})
            assert r.status_code == 200 and r.json()["must_change_password"] is False

            # Deactivation signs the user out immediately.
            assert admin.patch("/api/users/sam@clinic.example", json={"active": False}).status_code == 200
            assert staff.get("/api/dashboard").status_code == 401


def test_last_admin_and_rate_limit(app):
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        assert c.patch(f"/api/users/{ADMIN['email']}", json={"role": "staff"}).status_code == 409
        c.post("/api/auth/logout")
        for _ in range(5):
            assert c.post("/api/auth/login", json={**ADMIN, "password": "wrong-password"}).status_code == 401
        assert c.post("/api/auth/login", json=ADMIN).status_code == 429  # locked for 15 minutes


def test_open_demo_mode(app, monkeypatch):
    monkeypatch.setenv("LOTUS_AUTH_DISABLED", "1")
    with TestClient(app) as c:
        me = c.get("/api/auth/me").json()
        assert me["auth_disabled"] is True and me["role"] == "admin"
        assert c.get("/api/dashboard").status_code == 200 and c.get("/api/visits").status_code == 200
