"""REST + SSE API used by the Lotus AI Health console.

Controllers stay thin: they validate input, call ops / workflows, and return
view models. Business-rule refusals (OpError) become HTTP 409.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .. import fhir, ops
from ..auth import User, actor, current_user, staff_or_admin
from ..agents import appeal as appeal_agents
from ..agents import chat as chat_agent
from ..agents.evidence import evaluate
from ..agents.integrity import checklist
from ..domain.models import Case, CaseStatus, Questionnaire, QuestionnaireResponse
from ..domain.policies import QUESTIONNAIRES
from ..workflows.appeal import evidence_for, pending_step, resume_appeal
from ..workflows.intake import run_intake

public = APIRouter(prefix="/api")
# Everything on `router` requires a signed-in admin or staff user.
router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])

STATUS_LABEL = {
    "processing": "Processing", "exception": "Exception — blocked", "awaiting_rescan": "Awaiting rescan",
    "quarantined": "Quarantined — identity", "ready": "Ready to submit", "sent": "Sent — ack unknown",
    "acknowledged": "Submitted — acknowledged", "denied": "Denied — appeal open",
    "appeal_pending": "Appeal — decision required", "appeal_sent": "Appeal sent", "written_off": "Written off",
    "returned": "Returned — different patient", "archived": "Archived",
}


def C(request: Request):
    return request.app.state.container


def F(request: Request):
    return request.app.state.feed


def _case(request: Request, case_id: str) -> Case:
    case = C(request).repo.get(case_id)
    if not case:
        raise HTTPException(404, f"{case_id} not found")
    return case


async def _guard(coro):
    try:
        return await coro
    except ops.OpError as e:
        raise HTTPException(409, str(e))


def _days_left(case: Case) -> Optional[int]:
    return (date.fromisoformat(case.denial.deadline) - ops.demo_today()).days if case.denial else None


def row(case: Case) -> dict[str, Any]:
    return {"id": case.id, "patient_token": case.patient_token, "payer": case.payer, "billed": case.billed,
            "status": case.status.value, "status_label": STATUS_LABEL[case.status.value],
            "blockers": len(case.open_blockers), "denial_code": case.denial.code if case.denial else None,
            "deadline": case.denial.deadline if case.denial else None, "days_left": _days_left(case),
            "appeal_status": case.appeal.status, "updated_at": case.updated_at}


# ------------------------------------------------------------------ meta
@public.get("/health")
def health():
    return {"ok": True}


@router.get("/dashboard")
def dashboard(request: Request):
    c = C(request)
    cases = c.repo.all()
    count = lambda *s: sum(1 for x in cases if x.status.value in s)  # noqa: E731
    decided = [x for x in cases if x.appeal.status in ("sent", "written_off")]
    pending = [x for x in cases if x.denial and x.appeal.status in ("evidence_ready", "drafted")]
    unread = sum(1 for n in c.store.find("notifications", limit=10_000) if not n["read"])
    watch = sorted(pending, key=lambda x: x.denial.deadline)
    return {
        "today": ops.demo_today().isoformat(), "llm": c.llm.name, "feed": F(request).state(),
        "kpis": {
            "active": count("processing", "exception", "awaiting_rescan", "quarantined", "ready", "sent",
                            "acknowledged", "denied", "appeal_pending"),
            "ready": count("ready"), "exceptions": count("exception"), "awaiting_rescan": count("awaiting_rescan"),
            "quarantined": count("quarantined"), "denials": sum(1 for x in cases if x.denial),
            "decided": len(decided), "approved": sum(1 for x in decided if x.appeal.status == "sent"),
            "written_off": sum(1 for x in decided if x.appeal.status == "written_off"),
            "pending_decisions": len(pending), "unread": unread,
        },
        "by_status": {k: count(k) for k in ("ready", "exception", "awaiting_rescan", "quarantined", "processing")},
        "events": c.bus.recent(40),
        "watchlist": [{"id": x.id, "deadline": x.denial.deadline, "days_left": _days_left(x),
                       "verified": x.denial.deadline_verified, "code": x.denial.code} for x in watch],
        "notifications": c.store.find("notifications", limit=4),
    }


# ------------------------------------------------------------------ cases
@router.get("/cases")
def list_cases(request: Request, status: Optional[str] = None):
    cases = sorted(C(request).repo.all(), key=lambda x: x.id, reverse=True)
    if status:
        wanted = set(status.split(","))
        cases = [x for x in cases if x.status.value in wanted]
    return [row(x) for x in cases]


@router.get("/cases/{case_id}")
async def get_case(request: Request, case_id: str):
    c = C(request)
    case = _case(request, case_id)
    return {"case": case.model_dump(mode="json"), "row": row(case), "checklist": checklist(case),
            "pending_step": await pending_step(c, case),
            "stage_runs": [{"stage": r["stage"], "fp": r["fp"]} for r in c.ledger.runs(case_id)],
            "questionnaire": QUESTIONNAIRES[case.appeal.questionnaire_id].model_dump()
            if case.appeal.questionnaire_id else None}


@router.get("/cases/{case_id}/traces")
def traces(request: Request, case_id: str):
    spans = C(request).store.find("traces", {"case_id": case_id}, limit=400, newest_first=False)
    return [{k: s.get(k) for k in ("name", "kind", "status", "duration_ms", "correlation_id", "attrs", "error",
                                   "started_at")} for s in spans]


class Action(BaseModel):
    action: Literal["mark_received", "request_rescan", "accept_finding", "reconcile_identity",
                    "verify_deadline", "submit"]
    doc_type: Optional[str] = None
    finding_id: Optional[str] = None
    choice: Optional[str] = None
    rationale: str = ""


@router.post("/cases/{case_id}/actions")
async def case_action(request: Request, case_id: str, body: Action,
                      user: User = Depends(staff_or_admin)):
    c, _ = C(request), _case(request, case_id)
    a = body.action
    if a == "mark_received":
        await _guard(ops.mark_received(c, F(request).gen, case_id, body.doc_type or "", actor(user)))
    elif a == "request_rescan":
        await _guard(ops.request_rescan(c, case_id, body.finding_id or "", actor(user)))
    elif a == "accept_finding":
        await _guard(ops.accept_finding(c, case_id, body.finding_id or "", body.rationale, actor(user)))
    elif a == "reconcile_identity":
        await _guard(ops.reconcile_identity(c, case_id, body.choice or "", body.rationale, actor(user)))
    elif a == "verify_deadline":
        await _guard(ops.verify_deadline(c, case_id, actor(user)))
    elif a == "submit":
        await _guard(ops.submit(c, case_id, actor(user)))
    return await get_case(request, case_id)


@router.post("/packets")
async def new_packet(request: Request):
    """Ingest a new synthetic packet through the intake workflow (demo upload)."""
    c, feed = C(request), F(request)
    spec = feed.gen.packet()
    c.repo.save(spec.case)
    case = await run_intake(c, spec.case.id)
    return row(case)


# ------------------------------------------------------------------ appeals
@router.get("/appeals")
def appeals(request: Request):
    cases = [x for x in C(request).repo.all() if x.denial]
    pending = sorted([x for x in cases if x.appeal.status in ("evidence_ready", "drafted", "none")],
                     key=lambda x: x.denial.deadline)
    decided = sorted([x for x in cases if x.appeal.status in ("sent", "written_off")],
                     key=lambda x: x.appeal.decided_at or "", reverse=True)

    def view(x: Case) -> dict:
        return {**row(x), "reason": x.denial.reason, "category": x.denial.category,
                "triage": x.appeal.triage.model_dump() if x.appeal.triage else None,
                "decided_by": x.appeal.decided_by, "decided_at": x.appeal.decided_at}

    return {"pending": [view(x) for x in pending], "decided": [view(x) for x in decided]}


async def _expect_step(request: Request, case: Case, step: str) -> None:
    current = await pending_step(C(request), case)
    if current != step:
        raise HTTPException(409, f"Appeal is not waiting for '{step}' (current: {current or 'none'})")


@router.post("/cases/{case_id}/appeal/prepare")
async def prepare_appeal(request: Request, case_id: str, user: User = Depends(staff_or_admin)):
    case = _case(request, case_id)
    await _expect_step(request, case, "prepare")
    await resume_appeal(C(request), case_id, {"action": "prepare", "actor": actor(user)})
    return await get_case(request, case_id)


class Decision(BaseModel):
    action: Literal["approve", "deny"]
    rationale: str = ""


@router.post("/cases/{case_id}/appeal/decision")
async def decide_appeal(request: Request, case_id: str, body: Decision,
                        user: User = Depends(staff_or_admin)):
    case = _case(request, case_id)
    step = await pending_step(C(request), case)
    if body.action == "approve":
        await _expect_step(request, case, "decision")
        if not (case.appeal.letter and case.appeal.letter.gate_passed):
            raise HTTPException(409, "Citation integrity gate has not passed — every assertion needs a verified "
                                     "source page before approval")
    else:
        if step not in ("prepare", "decision"):
            raise HTTPException(409, "No open appeal decision")
        if not body.rationale.strip():
            raise HTTPException(409, "A write-off needs a rationale")
    await resume_appeal(C(request), case_id, {"action": body.action, "actor": actor(user),
                                              "rationale": body.rationale})
    return await get_case(request, case_id)


@router.post("/cases/{case_id}/appeal/letter/remove-unverified")
async def strip_unverified(request: Request, case_id: str, user: User = Depends(staff_or_admin)):
    c = C(request)
    async with c.repo.mutate(case_id) as cs:
        if not cs.appeal.letter:
            raise HTTPException(409, "No draft letter")
        n = cs.appeal.letter.unverified
        cs.appeal.letter = appeal_agents.remove_unverified(cs)
        cs.log("LETTER_EDITED", f"Removed {n} unverified assertion(s)", actor=actor(user))
    return await get_case(request, case_id)


# ------------------------------------------------------------------ evidence
class ChatIn(BaseModel):
    question: str


@router.post("/cases/{case_id}/chat")
async def evidence_chat(request: Request, case_id: str, body: ChatIn):
    from ..observability import new_correlation_id
    from ..workflows.common import case_context

    case = _case(request, case_id)
    with case_context(case_id, new_correlation_id()):
        return await chat_agent.ask(C(request).llm, case, body.question)


class EvidenceIn(BaseModel):
    questionnaire_id: str


@router.post("/cases/{case_id}/evidence")
async def run_evidence(request: Request, case_id: str, body: EvidenceIn):
    if body.questionnaire_id not in QUESTIONNAIRES:
        raise HTTPException(404, "Unknown questionnaire")
    from ..observability import new_correlation_id
    from ..workflows.common import case_context

    case = _case(request, case_id)
    with case_context(case_id, new_correlation_id()):
        resp, result = await evidence_for(C(request), case, body.questionnaire_id)
    return {"response": resp.model_dump(), "evaluation": result}


@router.get("/questionnaires")
def questionnaires():
    return [q.model_dump() for q in QUESTIONNAIRES.values()]


class EvaluateIn(BaseModel):
    questionnaire: Questionnaire
    response: QuestionnaireResponse


@router.post("/evaluate")
def evaluate_api(body: EvaluateIn):
    """Evidence evaluator API: deterministic group evaluation of a QuestionnaireResponse."""
    result = evaluate(body.questionnaire, body.response)
    return {"evaluation": result, "response": body.response.model_dump()}


@router.get("/fhir/Questionnaire/{qid}")
def fhir_questionnaire(qid: str):
    if qid not in QUESTIONNAIRES:
        raise HTTPException(404)
    return fhir.questionnaire_r4(QUESTIONNAIRES[qid])


@router.get("/fhir/cases/{case_id}/QuestionnaireResponse")
def fhir_response(request: Request, case_id: str):
    case = _case(request, case_id)
    if not case.appeal.response:
        raise HTTPException(404, "No evidence response yet")
    return fhir.response_r4(case.appeal.response)


# ------------------------------------------------------------------ notifications
@router.get("/notifications")
def notifications(request: Request):
    c = C(request)
    from ..services import CATEGORY_LABEL, NOTIFY_TO
    return {"items": c.store.find("notifications", limit=300),
            "settings": [{"category": k, "label": v, "to": NOTIFY_TO[k], "enabled": c.notifier.settings()[k]}
                         for k, v in CATEGORY_LABEL.items()]}


class ReadIn(BaseModel):
    id: Optional[str] = None  # None = mark all read


@router.post("/notifications/read")
def mark_read(request: Request, body: ReadIn):
    c = C(request)
    for n in c.store.find("notifications", limit=10_000):
        if body.id is None or n["id"] == body.id:
            n["read"] = True
            c.store.put("notifications", n["id"], n)
    c.bus.broadcast({"kind": "refresh"})
    return {"ok": True}


class ToggleIn(BaseModel):
    category: str
    enabled: bool


@router.post("/notifications/settings")
def toggle(request: Request, body: ToggleIn):
    return C(request).notifier.set_enabled(body.category, body.enabled)


# ------------------------------------------------------------------ feed + stream
class FeedIn(BaseModel):
    paused: Optional[bool] = None
    speed: Optional[int] = None


@router.post("/feed")
def feed_control(request: Request, body: FeedIn):
    f = F(request)
    if body.paused is not None:
        f.paused = body.paused
    if body.speed in (1, 2, 4):
        f.speed = body.speed
    C(request).bus.broadcast({"kind": "feed", "feed": f.state()})
    return f.state()


@router.get("/stream")
async def stream(request: Request):
    bus = C(request).bus
    q = bus.subscribe()

    async def gen():
        try:
            yield {"event": "hello", "data": json.dumps({"ok": True})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield {"event": msg["kind"], "data": json.dumps(msg, default=str)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(gen())
