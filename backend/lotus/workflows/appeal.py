"""Appeal workflow (the design's "Appeal letter agent workflow" + "Evidence workflow"), with HITL.

    diagnosis ─▶ evidence ─▶ triage ─▶ ⏸ await_prepare ─┬─▶ draft ─▶ ⏸ await_decision ─▶ finalize
                                                        └──────────────── (deny) ──────────────▶ finalize

The two ⏸ nodes call LangGraph `interrupt()`; the checkpoint survives restarts
and the API resumes the thread with Command(resume={...}). The AI never sends
an appeal: only a human "approve" does, and only when the citation gate passed.
thread_id = "appeal:<case id>:<notice date>".
"""
from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..agents import appeal as appeal_agents
from ..agents import clinical
from ..agents.evidence import evaluate, flat_questions, run_evidence_agent
from ..domain.models import Case, CaseStatus, QuestionnaireResponse, utcnow
from ..domain.policies import CATEGORY_TO_QUESTIONNAIRE, QUESTIONNAIRES
from ..observability import new_correlation_id
from .common import case_context, fingerprint

if TYPE_CHECKING:
    from ..container import Container


class AppealState(TypedDict, total=False):
    case_id: str
    correlation_id: str
    questionnaire_id: str
    decision: Optional[dict[str, Any]]
    stages: Annotated[list[str], operator.add]


def thread_id(case: Case) -> str:
    assert case.denial
    return f"appeal:{case.id}:{case.denial.notice_date}"


async def evidence_for(c: "Container", case: Case, questionnaire_id: str) -> tuple[QuestionnaireResponse, dict]:
    """Evidence workflow with caching: key = case + questionnaire + record fingerprint + denial day."""
    qn = QUESTIONNAIRES[questionnaire_id]
    fp = fingerprint(questionnaire_id, case.denial.notice_date if case.denial else None,
                     [(d.id, [p.sha256 for p in d.pages]) for d in case.documents])

    async def work():
        resp = await run_evidence_agent(c.llm, case, qn)
        return resp.model_dump()

    raw, _ = await c.ledger.run_once(case.id, f"evidence:{questionnaire_id}", fp, work)
    resp = QuestionnaireResponse.model_validate(raw)
    result = evaluate(qn, resp)  # deterministic, cheap: always recomputed
    return resp, result


def build_appeal_graph(c: "Container") -> StateGraph:
    async def diagnosis(state: AppealState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case and case.denial

            async def work():
                r = await clinical.diagnosis_reasoning(c.llm, case, case.denial.letter_text)
                return [d.model_dump() for d in r.diagnoses]

            out, _ = await c.ledger.run_once(cid, "diagnosis", fingerprint(thread_id(case)), work)
            async with c.repo.mutate(cid) as cs:
                cs.appeal.diagnoses = out
                cs.appeal.thread_id = thread_id(cs)
        return {"stages": ["diagnosis"]}

    async def evidence(state: AppealState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case and case.denial
            qid = state.get("questionnaire_id") or (
                "Q-TELEHEALTH" if case.claim_type == "professional"
                else CATEGORY_TO_QUESTIONNAIRE.get(case.denial.category, "Q-DOC-COMPLETE"))
            resp, result = await evidence_for(c, case, qid)
            async with c.repo.mutate(cid) as cs:
                cs.appeal.questionnaire_id, cs.appeal.response, cs.appeal.evaluation = qid, resp, result
                if cs.appeal.status == "none":
                    cs.appeal.status = "evidence_ready"
                    cs.log("EVIDENCE", f"{QUESTIONNAIRES[qid].title}: criteria {'met' if result['met'] else 'not met'}")
        return {"stages": ["evidence"], "questionnaire_id": qid}

    async def triage(state: AppealState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case
            n = sum(len(x["evidence"]) for x in flat_questions(QUESTIONNAIRES[case.appeal.questionnaire_id],
                                                                case.appeal.response))
            met = case.appeal.evaluation["met"] if case.appeal.evaluation else None

            async def work():
                return (await appeal_agents.triage(c.llm, case, met, n)).model_dump()

            out, cached = await c.ledger.run_once(cid, "triage", fingerprint(thread_id(case), met, n), work)
            async with c.repo.mutate(cid) as cs:
                from ..domain.models import Triage
                cs.appeal.triage = Triage(**out)
            if not cached:
                c.bus.publish("appeal.evidence_ready", "Evidence ready for appeal",
                              f"{cid} · criteria {'met' if met else 'not met'} · AI triage {out['confidence']:.2f}",
                              cid, "ok" if met else "warn")
        return {"stages": ["triage"]}

    async def await_prepare(state: AppealState) -> dict:
        decision = interrupt({"step": "prepare", "case_id": state["case_id"]})
        return {"decision": decision, "stages": ["await_prepare"]}

    def after_prepare(state: AppealState) -> str:
        return "finalize" if (state.get("decision") or {}).get("action") == "deny" else "draft"

    async def draft(state: AppealState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case
            qn = QUESTIONNAIRES[case.appeal.questionnaire_id]
            unmet = [g["text"] for g in (case.appeal.evaluation or {}).get("groups", {}).values() if not g["met"]]
            letter = await appeal_agents.draft_letter(c.llm, case, flat_questions(qn, case.appeal.response), qn.title,
                                                      unmet)
            async with c.repo.mutate(cid) as cs:
                cs.appeal.letter = letter
                cs.appeal.status = "drafted"
                cs.status = CaseStatus.APPEAL_PENDING
                cs.log("APPEAL_DRAFT", f"Evidence-linked draft: {len(letter.assertions)} assertions, "
                                       f"{letter.unverified} unverified", actor=(state.get("decision") or {}).get("actor", "system"))
            c.blobs.put_bytes(cid, "letters", "appeal_draft.txt", letter.text.encode())
            c.bus.publish("appeal.drafted", "Appeal drafted",
                          f"{cid} · citation gate {'passed' if letter.gate_passed else f'{letter.unverified} unverified'}",
                          cid, "ok" if letter.gate_passed else "warn")
        return {"stages": ["draft"]}

    async def await_decision(state: AppealState) -> dict:
        decision = interrupt({"step": "decision", "case_id": state["case_id"]})
        return {"decision": decision, "stages": ["await_decision"]}

    async def finalize(state: AppealState) -> dict:
        cid = state["case_id"]
        d = state.get("decision") or {}
        with case_context(cid, state["correlation_id"]):
            async with c.repo.mutate(cid) as cs:
                if cs.appeal.status in ("sent", "written_off"):
                    return {"stages": ["finalize"]}  # idempotent
                cs.appeal.decided_by, cs.appeal.decided_at = d.get("actor", "reviewer"), utcnow()
                cs.appeal.rationale = d.get("rationale")
                if d.get("action") == "approve":
                    cs.appeal.status = "sent"
                    cs.status = CaseStatus.APPEAL_SENT
                    cs.log("APPEAL_APPROVED", "Appeal approved by human reviewer and sent", actor=cs.appeal.decided_by)
                else:
                    cs.appeal.status = "written_off"
                    cs.status = CaseStatus.WRITTEN_OFF
                    cs.log("APPEAL_DENIED", f"Written off: {cs.appeal.rationale or 'no rationale'}",
                           actor=cs.appeal.decided_by)
                case = cs
            if case.appeal.status == "sent":
                c.blobs.put_bytes(cid, "letters", "appeal_sent.txt", case.appeal.letter.text.encode())
                c.bus.publish("appeal.sent", "Appeal approved & sent", f"{cid} · by {case.appeal.decided_by}", cid, "ok")
                c.notifier.notify("approval", f"Appeal approved and sent — {cid}",
                                  f"{case.appeal.decided_by} approved the evidence-linked appeal for {cid} "
                                  f"({case.payer}, {case.denial.code}).", cid, dedupe_key=f"approval-{cid}")
            else:
                c.bus.publish("appeal.written_off", "Appeal declined — written off", f"{cid} · {case.appeal.rationale or ''}",
                              cid, "bad")
        return {"stages": ["finalize"]}

    g = StateGraph(AppealState)
    for name, fn in [("diagnosis", diagnosis), ("evidence", evidence), ("triage", triage),
                     ("await_prepare", await_prepare), ("draft", draft), ("await_decision", await_decision),
                     ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "diagnosis")
    g.add_edge("diagnosis", "evidence")
    g.add_edge("evidence", "triage")
    g.add_edge("triage", "await_prepare")
    g.add_conditional_edges("await_prepare", after_prepare, {"draft": "draft", "finalize": "finalize"})
    g.add_edge("draft", "await_decision")
    g.add_edge("await_decision", "finalize")
    g.add_edge("finalize", END)
    return g


async def start_appeal(c: "Container", case_id: str) -> None:
    case = c.repo.get(case_id)
    assert case and case.denial
    await c.appeal.ainvoke({"case_id": case_id, "correlation_id": new_correlation_id(), "stages": []},
                           {"configurable": {"thread_id": thread_id(case)}})


async def resume_appeal(c: "Container", case_id: str, decision: dict) -> None:
    case = c.repo.get(case_id)
    assert case and case.denial
    await c.appeal.ainvoke(Command(resume=decision), {"configurable": {"thread_id": thread_id(case)}})


async def pending_step(c: "Container", case: Case) -> Optional[str]:
    if not case.denial:
        return None
    snap = await c.appeal.aget_state({"configurable": {"thread_id": thread_id(case)}})
    for t in snap.tasks:
        for intr in t.interrupts:
            return intr.value.get("step")
    return None
