"""Visit-to-Claim workflow: telehealth note → evidence-bound coding → denial-risk rules → ⏸ staff → claim.

    code ─▶ risks ─▶ ⏸ await_review ─▶ finalize (claim | return to clinician | reject)

thread_id = "visit:<id>:v<version>". A clinician addendum bumps the version and starts a fresh thread,
so the re-check runs on the amended note (closed-loop documentation repair).
While the thread waits, staff fixes (code changes, risk acceptance) re-run the rules directly; the
LLM coding result is cached per note version in the stage ledger, so fixes never re-call the model.
"""
from __future__ import annotations

import asyncio
import operator
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..agents import coding
from ..domain.models import Case, Document, Page, utcnow
from ..domain.visits import CodeLine, Visit, VisitStatus
from ..observability import new_correlation_id, tracer
from .common import case_context, fingerprint

if TYPE_CHECKING:
    from ..container import Container


class VisitRepo:
    COLL = "visits"

    def __init__(self, store):
        self.store = store
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, vid: str) -> Optional[Visit]:
        raw = self.store.get(self.COLL, vid)
        return Visit.model_validate(raw) if raw else None

    def save(self, v: Visit) -> None:
        self.store.put(self.COLL, v.id, v.model_dump(mode="json"))

    def all(self) -> list[Visit]:
        return [Visit.model_validate(r) for r in self.store.find(self.COLL, limit=10_000)]

    @asynccontextmanager
    async def mutate(self, vid: str) -> AsyncIterator[Visit]:
        async with self._locks[vid]:
            v = self.get(vid)
            if v is None:
                raise KeyError(vid)
            yield v
            self.save(v)


class VisitState(TypedDict, total=False):
    visit_id: str
    correlation_id: str
    decision: Optional[dict[str, Any]]
    stages: Annotated[list[str], operator.add]


def thread_id(v: Visit) -> str:
    return f"visit:{v.id}:v{v.version}"


async def ai_dx_for(c: "Container", v: Visit) -> list[CodeLine]:
    """LLM coding, cached per note text (idempotent)."""
    async def work():
        return coding.serialize_dx(await coding.code_diagnoses(c.llm, v))
    raw, _ = await c.ledger.run_once(v.id, "coding", fingerprint(v.note), work)
    return [CodeLine.model_validate(x) for x in raw]


async def recheck(c: "Container", vid: str) -> Visit:
    """Re-run the rules on the current note and claim lines (after a staff fix or addendum)."""
    v = c.visits.get(vid)
    assert v
    ai_dx = await ai_dx_for(c, v)
    async with c.visits.mutate(vid) as vv:
        async with tracer.span("rule.claim_risks", kind="rule", visit=vid):
            coding.evaluate_risks(vv, ai_dx)
            coding.fill_descriptions(vv, ai_dx)
            vv.risk_score = coding.score(vv.risks, vv.payer, c.learner)
        out = vv
    return out


def build_visit_graph(c: "Container") -> StateGraph:
    async def code(state: VisitState) -> dict:
        vid = state["visit_id"]
        with case_context(vid, state["correlation_id"]):
            async with c.visits.mutate(vid) as v:
                v.status, v.correlation_id = VisitStatus.CODING, state["correlation_id"]
            v = c.visits.get(vid)
            await ai_dx_for(c, v)
        return {"stages": ["code"]}

    async def risks(state: VisitState) -> dict:
        vid = state["visit_id"]
        with case_context(vid, state["correlation_id"]):
            v = await recheck(c, vid)
            async with c.visits.mutate(vid) as vv:
                vv.status = VisitStatus.REVIEW
                if vv.initial_risk_score is None:
                    vv.initial_risk_score = vv.risk_score
                vv.log("CODED", f"{len(vv.codes)} claim lines · {len(vv.open_risks)} open risk(s) · "
                                f"denial risk {vv.risk_score}%")
            c.bus.publish("visit.coded", "Visit coded", f"{vid} · {v.payer} · denial risk {v.risk_score}%",
                          None, "ok" if v.risk_score < 20 else "warn")
        return {"stages": ["risks"]}

    async def await_review(state: VisitState) -> dict:
        decision = interrupt({"step": "review", "visit_id": state["visit_id"]})
        return {"decision": decision, "stages": ["await_review"]}

    async def finalize(state: VisitState) -> dict:
        vid = state["visit_id"]
        d = state.get("decision") or {}
        with case_context(vid, state["correlation_id"]):
            v = c.visits.get(vid)
            assert v
            if v.status in (VisitStatus.CLAIMED, VisitStatus.REJECTED, VisitStatus.RETURNED):
                return {"stages": ["finalize"]}  # idempotent on resume
            action, who = d.get("action"), d.get("actor", "staff")
            if action == "approve":
                case_id = await create_claim(c, v, who)
                async with c.visits.mutate(vid) as vv:
                    vv.status, vv.case_id = VisitStatus.CLAIMED, case_id
                    vv.log("CLAIM_CREATED", f"Claim packet {case_id} created (${vv.billed:,.2f})", actor=who)
                c.bus.publish("visit.claimed", "Clean claim created from visit", f"{vid} → {case_id}", case_id, "ok")
            elif action == "return":
                async with c.visits.mutate(vid) as vv:
                    vv.status, vv.return_reason = VisitStatus.RETURNED, d.get("reason")
                    vv.log("RETURNED", f"Returned to {vv.clinician}: {vv.return_reason}", actor=who)
                c.bus.publish("visit.returned", "Visit returned to clinician", f"{vid} · {d.get('reason', '')}",
                              None, "warn")
            else:
                async with c.visits.mutate(vid) as vv:
                    vv.status = VisitStatus.REJECTED
                    vv.log("REJECTED", d.get("reason") or "Not billable", actor=who)
        return {"stages": ["finalize"]}

    g = StateGraph(VisitState)
    for name, fn in [("code", code), ("risks", risks), ("await_review", await_review), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "code")
    g.add_edge("code", "risks")
    g.add_edge("risks", "await_review")
    g.add_edge("await_review", "finalize")
    g.add_edge("finalize", END)
    return g


async def create_claim(c: "Container", v: Visit, actor: str) -> str:
    """Build a professional claim packet (cover sheet + visit note) and run the standard intake workflow."""
    from .intake import run_intake

    key = f"claim-for:{v.id}:v{v.version}"
    existing = c.store.get("claim_links", key)
    if existing:
        return existing["case_id"]
    n = max([int(x.split("-")[1]) for x in c.store.ids("cases")] + [1000]) + 1
    cid = f"CASE-{n}"
    lines = "\n".join(f"  {cl.kind} {cl.code}{('-' + '-'.join(cl.modifiers)) if cl.modifiers else ''} {cl.description}"
                      for cl in v.codes)
    head = f"PATIENT TOKEN: {v.patient_token}\nMRN: {v.mrn}\nENCOUNTER: {v.id}\n"
    cover = (f"CLAIM PACKET COVER SHEET\n{head}PAYER: {v.payer}\nSERVICE DATE: {v.visit_date}\n"
             f"PLACE OF SERVICE: {v.pos}\nCLAIM LINES:\n{lines}\nDOCUMENTS ENCLOSED: Cover sheet, Clinical note")
    import hashlib
    sha = lambda t: hashlib.sha256(t.encode()).hexdigest()  # noqa: E731
    docs = [Document(id="d1", declared_type="Cover sheet", patient_token=v.patient_token, mrn=v.mrn,
                     pages=[Page(number=1, text=cover, ocr_confidence=0.99, sha256=sha(cover))], sha256=sha(cover)),
            Document(id="d2", declared_type="Clinical note", patient_token=v.patient_token, mrn=v.mrn,
                     pages=[Page(number=1, text=v.note, ocr_confidence=0.99, sha256=sha(v.note))], sha256=sha(v.note))]
    case = Case(id=cid, patient_token=v.patient_token, mrn=v.mrn, encounter=v.id, payer=v.payer,
                policy_id="Telehealth professional claim", service_date=v.visit_date, billed=v.billed,
                claim_type="professional", origin_visit=v.id, documents=docs,
                accepted_risks=[r.rule for r in v.risks if r.status == "accepted"])
    case.log("VISIT_TO_CLAIM", f"Created from telehealth visit {v.id} (risk {v.risk_score}%)", actor=actor)
    c.repo.save(case)
    c.store.put("claim_links", key, {"case_id": cid})
    await run_intake(c, cid)
    return cid


async def start_visit(c: "Container", vid: str) -> None:
    v = c.visits.get(vid)
    assert v
    await c.visit_graph.ainvoke({"visit_id": vid, "correlation_id": new_correlation_id(), "stages": []},
                                {"configurable": {"thread_id": thread_id(v)}})


async def resume_visit(c: "Container", vid: str, decision: dict) -> None:
    v = c.visits.get(vid)
    assert v
    await c.visit_graph.ainvoke(Command(resume=decision), {"configurable": {"thread_id": thread_id(v)}})


async def visit_pending(c: "Container", v: Visit) -> Optional[str]:
    snap = await c.visit_graph.aget_state({"configurable": {"thread_id": thread_id(v)}})
    for t in snap.tasks:
        for i in t.interrupts:
            return i.value.get("step")
    return None


async def clinician_addendum(c: "Container", vid: str, addendum: str, author: str) -> Visit:
    """Closed loop: the clinician amends the note; a new version is coded and checked from scratch."""
    async with c.visits.mutate(vid) as v:
        if v.status != VisitStatus.RETURNED:
            raise ValueError("Only returned visits accept an addendum")
        v.version += 1
        v.note = v.note.rstrip() + f"\nADDENDUM ({author}, {utcnow()[:10]}): {addendum.strip()}"
        v.status, v.return_reason = VisitStatus.RECEIVED, None
        v.log("ADDENDUM", f"v{v.version}: {addendum.strip()}", actor=author)
    c.bus.publish("visit.addendum", "Clinician addendum received", f"{vid} · v{c.visits.get(vid).version}", None)
    await start_visit(c, vid)
    return c.visits.get(vid)
