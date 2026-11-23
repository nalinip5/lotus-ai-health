"""Visit-to-Claim: evidence-bound coding, risk rules, fixes, closed-loop addenda, outcome learning, ingest."""
import pytest
from fastapi.testclient import TestClient

from lotus.agents.coding import OutcomeLearner, documented_minutes, has_consent, patient_location_pos, supported_em
from lotus.sim.visits import VisitGenerator
from lotus.store.docstore import DocStore
from lotus.workflows.visit import clinician_addendum, start_visit, visit_pending

ADMIN = {"email": "admin@test.example", "password": "admin-pass-123"}


def test_extractors():
    note = ("CONSENT: Patient gave verbal consent to receive care via telehealth video visit.\n"
            "LOCATION: Patient located at home in TX.\nTIME: Total time spent on the date of the encounter was 27 minutes.")
    assert documented_minutes(note) == 27 and patient_location_pos(note) == "10" and has_consent(note)
    assert supported_em(27) == "99213" and supported_em(41) == "99215" and supported_em(5) is None
    assert documented_minutes("no time here") is None and not has_consent("Patient seen.")


def test_learner_moves_with_outcomes(tmp_path):
    lr = OutcomeLearner(DocStore(tmp_path / "x.db"))
    assert lr.multiplier("P", "modifier_95") == 1.0
    for _ in range(8):
        lr.record("P", ["modifier_95"], denied=True)
    assert lr.multiplier("P", "modifier_95") > 1.5
    for _ in range(8):
        lr.record("Q", ["modifier_95"], denied=False)
    assert lr.multiplier("Q", "modifier_95") < 0.5


@pytest.mark.asyncio
async def test_closed_loop_documentation(env):
    c, _, _ = env
    vg = VisitGenerator(seed=2)
    v = vg.visit(["consent"], "medical", "Aetna Choice POS")
    c.visits.save(v)
    await start_visit(c, v.id)
    v = c.visits.get(v.id)
    assert v.status.value == "review" and any(r.rule == "telehealth_consent" and r.needs_clinician for r in v.risks)
    from lotus.workflows.visit import resume_visit
    await resume_visit(c, v.id, {"action": "return", "actor": "s", "reason": "Document consent"})
    assert c.visits.get(v.id).status.value == "returned"
    v = await clinician_addendum(c, v.id, "Verbal consent for telehealth video was obtained at the start.", "Dr X")
    assert v.version == 2 and v.status.value == "review" and v.open_risks == [] and v.risk_score == 0
    assert await visit_pending(c, v) == "review"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from lotus import config
    monkeypatch.setattr(config.settings, "data_dir", tmp_path)
    monkeypatch.setattr(config.settings, "simulator_enabled", False)
    monkeypatch.setenv("LOTUS_ADMIN_EMAIL", ADMIN["email"])
    monkeypatch.setenv("LOTUS_ADMIN_PASSWORD", ADMIN["password"])
    monkeypatch.setenv("LOTUS_INGEST_TOKEN", "ingest-secret-xyz")
    from lotus.main import app
    with TestClient(app) as c:
        assert c.post("/api/auth/login", json=ADMIN).status_code == 200
        yield c


def test_review_fix_and_claim(client):
    vs = client.get("/api/visits?status=review").json()
    over = next(v for v in vs if v["payer"] == "UnitedHealthcare Choice+" and v["specialty"] == "medical")
    vid = over["id"]
    d = client.get(f"/api/visits/{vid}").json()
    rules = {r["rule"]: r for r in d["visit"]["risks"]}
    assert {"em_overcoded", "modifier_95"} <= set(rules)
    # Preview: fixing one risk lowers the score.
    assert all(d["impact"][r["id"]] < d["row"]["risk_score"] for r in d["visit"]["risks"])
    assert client.post(f"/api/visits/{vid}/decision", json={"action": "approve"}).status_code == 409
    for r in d["visit"]["risks"]:
        assert client.post(f"/api/visits/{vid}/fix", json={"risk_id": r["id"]}).status_code == 200
    d = client.get(f"/api/visits/{vid}").json()
    em = next(cl for cl in d["visit"]["codes"] if cl["kind"] == "CPT")
    assert d["row"]["risk_score"] == 0 and "95" in em["modifiers"] and em["code"] != "99215"
    r = client.post(f"/api/visits/{vid}/decision", json={"action": "approve"}).json()
    case_id = r["visit"]["case_id"]
    case = client.get(f"/api/cases/{case_id}").json()["case"]
    assert case["claim_type"] == "professional" and case["status"] == "ready" and case["origin_visit"] == vid


def test_staff_cannot_paper_over_documentation(client):
    vs = client.get("/api/visits?status=review").json()
    for v in vs:
        d = client.get(f"/api/visits/{v['id']}").json()
        for r in d["visit"]["risks"]:
            if r["severity"] == "critical" or r["needs_clinician"] or r["rule"] == "em_overcoded":
                res = client.post(f"/api/visits/{v['id']}/accept-risk", json={"risk_id": r["id"], "rationale": "ok"})
                assert res.status_code == 409


def test_ingest_requires_token(client):
    body = {"patient_token": "PT-X", "mrn": "MRN-1", "payer": "BlueCross PPO", "clinician": "Dr Y",
            "visit_date": "2026-11-20", "note": "TELEHEALTH VISIT NOTE\nASSESSMENT: Essential hypertension.",
            "codes": [{"kind": "ICD-10", "code": "I10", "description": "Essential hypertension"}]}
    assert client.post("/api/ingest/visits", json=body).status_code == 401
    r = client.post("/api/ingest/visits", json=body, headers={"Authorization": "Bearer ingest-secret-xyz"})
    assert r.status_code == 200 and r.json()["status"] == "review"
