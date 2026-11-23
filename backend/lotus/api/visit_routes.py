"""Visit-to-Claim API: staff review queue + token-protected ingest for the telehealth platform."""
from __future__ import annotations

import hmac
import os
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from ..agents.coding import apply_fix, fix_impact, risk_probability
from ..auth import User, actor, current_user, staff_or_admin
from ..domain.visits import CodeLine, Visit, VisitStatus
from ..workflows.visit import clinician_addendum, recheck, resume_visit, start_visit, visit_pending

visits = APIRouter(prefix="/api/visits", dependencies=[Depends(current_user)])
ingest = APIRouter(prefix="/api/ingest")


def C(request: Request):
    return request.app.state.container


def _visit(request: Request, vid: str) -> Visit:
    v = C(request).visits.get(vid)
    if not v:
        raise HTTPException(404, f"{vid} not found")
    return v


def row(v: Visit) -> dict:
    return {"id": v.id, "patient_token": v.patient_token, "payer": v.payer, "clinician": v.clinician,
            "specialty": v.specialty, "visit_date": v.visit_date, "status": v.status.value,
            "risk_score": v.risk_score, "initial_risk_score": v.initial_risk_score, "open_risks": len(v.open_risks),
            "critical": sum(1 for r in v.open_risks if r.severity == "critical"), "billed": v.billed,
            "case_id": v.case_id, "version": v.version, "updated_at": v.updated_at}


@visits.get("")
def list_visits(request: Request, status: Optional[str] = None):
    vs = sorted(C(request).visits.all(), key=lambda v: v.id, reverse=True)
    if status:
        vs = [v for v in vs if v.status.value in status.split(",")]
    return [row(v) for v in vs]


@visits.get("/summary")
def summary(request: Request):
    vs = C(request).visits.all()
    claimed = [v for v in vs if v.status == VisitStatus.CLAIMED]
    reduced = [(v.initial_risk_score or 0) - v.risk_score for v in claimed]
    return {
        "in_review": sum(1 for v in vs if v.status == VisitStatus.REVIEW),
        "returned": sum(1 for v in vs if v.status == VisitStatus.RETURNED),
        "claimed": len(claimed),
        "first_pass_clean": round(100 * sum(1 for v in vs if v.initial_risk_score == 0) /
                                  max(1, sum(1 for v in vs if v.initial_risk_score is not None))),
        "fixes_applied": sum(v.fixes_applied for v in vs),
        "avg_risk_at_intake": round(sum(v.initial_risk_score or 0 for v in claimed) / max(1, len(claimed))),
        "avg_risk_at_claim": round(sum(v.risk_score for v in claimed) / max(1, len(claimed))),
        "risk_points_removed": sum(reduced),
        "learning": C(request).learner.all(),
    }


@visits.get("/{vid}")
async def get_visit(request: Request, vid: str):
    c = C(request)
    v = _visit(request, vid)
    impact = fix_impact(v, c.learner)
    return {"visit": v.model_dump(mode="json"), "row": row(v), "pending_step": await visit_pending(c, v),
            "impact": impact,
            "learned": {r.id: {"multiplier": c.learner.multiplier(v.payer, r.rule),
                               "p": round(risk_probability(r, v.payer, c.learner), 3),
                               **c.learner.stats(v.payer, r.rule)} for r in v.risks}}


class RiskAction(BaseModel):
    risk_id: str
    rationale: str = ""


async def _require_review(request: Request, v: Visit) -> None:
    if await visit_pending(C(request), v) != "review":
        raise HTTPException(409, "This visit isn't waiting for review")


@visits.post("/{vid}/fix")
async def fix(request: Request, vid: str, body: RiskAction, user: User = Depends(staff_or_admin)):
    c = C(request)
    v = _visit(request, vid)
    await _require_review(request, v)
    risk = next((r for r in v.open_risks if r.id == body.risk_id), None)
    if not risk or not risk.fix_action:
        raise HTTPException(409, "That risk has no automatic code fix" + (
            " — only the clinician can fix documentation; return the visit" if risk and risk.needs_clinician else ""))
    async with c.visits.mutate(vid) as vv:
        what = apply_fix(vv, risk.fix_action)
        vv.fixes_applied += 1
        vv.log("FIX", f"{what} ({risk.rule})", actor=actor(user))
    await recheck(c, vid)
    return await get_visit(request, vid)


@visits.post("/{vid}/accept-risk")
async def accept_risk(request: Request, vid: str, body: RiskAction, user: User = Depends(staff_or_admin)):
    c = C(request)
    v = _visit(request, vid)
    await _require_review(request, v)
    risk = next((r for r in v.open_risks if r.id == body.risk_id), None)
    if not risk:
        raise HTTPException(404, "Risk not found")
    if risk.severity == "critical":
        raise HTTPException(409, "Critical risks can't be accepted — fix them or return the visit")
    if risk.needs_clinician:
        raise HTTPException(409, "Staff can't accept missing documentation — return the visit to the clinician")
    if not risk.acceptable:
        raise HTTPException(409, "This is a compliance risk (billing above what the note supports) — apply the fix")
    if not body.rationale.strip():
        raise HTTPException(409, "A rationale is required")
    async with c.visits.mutate(vid) as vv:
        for r in vv.risks:
            if r.id == body.risk_id:
                r.status, r.rationale = "accepted", body.rationale
        vv.log("RISK_ACCEPTED", f"{risk.rule}: {body.rationale}", actor=actor(user))
    await recheck(c, vid)
    return await get_visit(request, vid)


class VisitDecision(BaseModel):
    action: Literal["approve", "return", "reject"]
    reason: str = ""


@visits.post("/{vid}/decision")
async def decide(request: Request, vid: str, body: VisitDecision, user: User = Depends(staff_or_admin)):
    c = C(request)
    v = _visit(request, vid)
    await _require_review(request, v)
    if body.action == "approve":
        if v.open_risks:
            raise HTTPException(409, f"{len(v.open_risks)} open risk(s) — fix, accept or return before creating the claim")
        if any(cl.kind == "ICD-10" and not cl.verified for cl in v.codes):
            raise HTTPException(409, "Every diagnosis must cite verified evidence in the note")
    reason = body.reason.strip()
    if body.action == "return" and not reason:
        reason = "; ".join(r.fix or r.message for r in v.open_risks if r.needs_clinician)
        if not reason:
            raise HTTPException(409, "Say what the clinician needs to document")
    if body.action == "reject" and not reason:
        raise HTTPException(409, "A reason is required to reject")
    await resume_visit(c, vid, {"action": body.action, "actor": actor(user), "reason": reason})
    return await get_visit(request, vid)


# ------------------------------------------------------------------ ingest (telehealth platform)
def ingest_auth(authorization: str = Header(default="")) -> None:
    token = os.environ.get("LOTUS_INGEST_TOKEN", "")
    if not token:
        raise HTTPException(503, "Ingest is disabled (LOTUS_INGEST_TOKEN not set)")
    if not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "Invalid ingest token")


class IngestVisit(BaseModel):
    patient_token: str
    mrn: str
    payer: str
    clinician: str
    specialty: Literal["medical", "dental"] = "medical"
    modality: Literal["video", "audio", "async"] = "video"
    visit_date: str
    note: str
    codes: list[CodeLine]


@ingest.post("/visits", dependencies=[Depends(ingest_auth)])
async def ingest_visit(request: Request, body: IngestVisit):
    c = C(request)
    n = max([int(x.split("-")[1]) for x in c.store.ids("visits")] + [5000]) + 1
    v = Visit(id=f"VIS-{n}", patient_token=body.patient_token, mrn=body.mrn, payer=body.payer,
              clinician=body.clinician, specialty=body.specialty, modality=body.modality,
              visit_date=body.visit_date, note=body.note, submitted_codes=body.codes)
    c.visits.save(v)
    c.bus.publish("visit.received", "Telehealth visit received", f"{v.id} · {v.clinician} · {v.payer}")
    await start_visit(c, v.id)
    return row(c.visits.get(v.id))


class Addendum(BaseModel):
    author: str
    text: str


@ingest.post("/visits/{vid}/addendum", dependencies=[Depends(ingest_auth)])
async def ingest_addendum(request: Request, vid: str, body: Addendum):
    _visit(request, vid)
    try:
        v = await clinician_addendum(C(request), vid, body.text, body.author)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return row(v)
