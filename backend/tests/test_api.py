"""API smoke test through the real app (lifespan seeds the demo book of work)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lotus import config
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "simulator_enabled", False)
    monkeypatch.setenv("LOTUS_ADMIN_EMAIL", "admin@test.example")
    monkeypatch.setenv("LOTUS_ADMIN_PASSWORD", "admin-pass-123")
    from lotus.main import app
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"email": "admin@test.example", "password": "admin-pass-123"})
        assert r.status_code == 200, r.text
        yield c


def test_console_api(client):
    d = client.get("/api/dashboard").json()
    assert d["kpis"]["denials"] >= 3 and d["kpis"]["exceptions"] >= 1 and d["llm"] == "mock"
    pending = client.get("/api/appeals").json()["pending"]
    assert pending
    unsupported = next(p for p in pending if p["triage"]["confidence"] < 0.8)
    cid = unsupported["id"]

    # Approve before preparing is refused; deny without rationale is refused.
    assert client.post(f"/api/cases/{cid}/appeal/decision", json={"action": "approve"}).status_code == 409
    assert client.post(f"/api/cases/{cid}/appeal/decision", json={"action": "deny"}).status_code == 409

    r = client.post(f"/api/cases/{cid}/appeal/prepare").json()
    assert r["pending_step"] == "decision" and not r["case"]["appeal"]["letter"]["gate_passed"]
    # Gate blocks approval of unverified assertions.
    assert client.post(f"/api/cases/{cid}/appeal/decision", json={"action": "approve"}).status_code == 409
    r = client.post(f"/api/cases/{cid}/appeal/decision", json={"action": "deny", "rationale": "No inpatient criteria"})
    assert r.json()["case"]["status"] == "written_off"

    fhir = client.get(f"/api/fhir/cases/{cid}/QuestionnaireResponse").json()
    assert fhir["resourceType"] == "QuestionnaireResponse" and fhir["item"]

    quarantined = client.get("/api/cases?status=quarantined").json()
    assert quarantined
    r = client.post(f"/api/cases/{quarantined[0]['id']}/actions",
                    json={"action": "reconcile_identity", "choice": "cover_authoritative", "rationale": "typo"})
    assert r.status_code == 200

    assert client.post("/api/packets").status_code == 200
    n = client.get("/api/notifications").json()
    assert n["items"] and len(n["settings"]) == 6
