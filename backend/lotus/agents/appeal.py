"""Denial intake, triage, and the appeal-letter agent with a citation-integrity gate."""
from __future__ import annotations

import json
from datetime import date, timedelta

from ..domain.models import AppealLetter, Assertion, Case, Citation, Denial, Triage
from ..domain.policies import DENIAL_CODES, PAYERS
from ..llm.base import LLM
from ..observability import tracer
from .schemas import DenialParse, LetterDraft, TriageOut
from .text import quote_in


async def ingest_denial(llm: LLM, case: Case, notice_text: str) -> Denial:
    async with tracer.span("agent.denial_intake", kind="agent"):
        p = await llm.structured(
            "parse_denial",
            "Extract the CARC reason code (e.g. CO-50), the notice date (YYYY-MM-DD) and the verbatim reason "
            "line from this remittance / denial notice.", notice_text, DenialParse, {"text": notice_text})
    meta = DENIAL_CODES.get(p.code, {"reason": p.source_text, "category": "unknown"})
    days = PAYERS[case.payer]["appeal_days"]
    deadline = date.fromisoformat(p.notice_date) + timedelta(days=days)
    return Denial(code=p.code, reason=meta["reason"], category=meta["category"], notice_date=p.notice_date,
                  appeal_window_days=days, deadline=deadline.isoformat(), letter_text=notice_text,
                  source_text=p.source_text)


async def triage(llm: LLM, case: Case, criteria_met: bool | None, evidence_count: int) -> Triage:
    d = case.denial
    assert d is not None
    async with tracer.span("agent.triage", kind="agent"):
        out = await llm.structured(
            "triage_denial",
            "You are the denial triage agent. Given the denial and the deterministic criteria result, recommend "
            "the next action for a human appeals specialist. Your recommendation is advisory only.",
            json.dumps({"code": d.code, "reason": d.reason, "criteria_met": criteria_met,
                        "evidence_count": evidence_count}), TriageOut,
            {"code": d.code, "category": d.category, "criteria_met": criteria_met, "evidence_count": evidence_count})
    return Triage(**out.model_dump())


def citation_gate(case: Case, draft: LetterDraft) -> list[Assertion]:
    """Deterministic: an assertion is verified only if its quote appears on the cited page."""
    out = []
    for a in draft.assertions:
        verified = False
        cit = None
        if a.doc_id and a.page and a.quote:
            doc = case.doc(a.doc_id)
            page = next((p for p in doc.pages if p.number == a.page), None) if doc else None
            cit = Citation(doc_id=a.doc_id, page=a.page, quote=a.quote)
            verified = bool(page and not page.unreadable and quote_in(a.quote, page.text))
        out.append(Assertion(text=a.text, citation=cit, verified=verified))
    return out


def render_letter(case: Case, draft: LetterDraft | AppealLetter, assertions: list[Assertion]) -> str:
    lines = [f"{case.payer} — Appeals Department", "", draft.opening, ""]
    for i, a in enumerate(assertions, 1):
        cite = ""
        if a.citation:
            d = case.doc(a.citation.doc_id)
            cite = f" [{d.classified_type if d else a.citation.doc_id}, p.{a.citation.page}]"
        flag = "" if a.verified else "  ⚠ UNVERIFIED — remove or attach a source before approval"
        lines.append(f"{i}. {a.text}{cite}{flag}")
    lines += ["", draft.closing, "", "Sincerely,", "Lotus AI Health — Appeals Team (on behalf of the provider)"]
    return "\n".join(lines)


async def draft_letter(llm: LLM, case: Case, questions: list[dict], questionnaire_title: str,
                       unmet_groups: list[str] | None = None) -> AppealLetter:
    d = case.denial
    assert d is not None
    ctx = {"case_id": case.id, "encounter": case.encounter, "service_date": case.service_date,
           "notice_date": d.notice_date, "code": d.code, "reason": d.reason,
           "questionnaire": questionnaire_title, "questions": questions,
           "unmet_groups": unmet_groups or [], "summary": case.summary}
    async with tracer.span("agent.appeal_letter", kind="agent"):
        draft = await llm.structured(
            "draft_letter",
            "You are the Appeal-letter agent. Write a concise, convincing appeal. Every factual assertion must "
            "carry doc_id, page and a verbatim quote taken from the evidence provided. Never invent evidence; if a "
            "criterion has no evidence, do not claim it is met.",
            json.dumps(ctx), LetterDraft, ctx)
    async with tracer.span("rule.citation_gate", kind="rule"):
        assertions = citation_gate(case, draft)
    unverified = sum(1 for a in assertions if not a.verified)
    return AppealLetter(opening=draft.opening, closing=draft.closing, text=render_letter(case, draft, assertions),
                        assertions=assertions, gate_passed=unverified == 0 and bool(assertions), unverified=unverified)


def remove_unverified(case: Case) -> AppealLetter:
    """Human edit: drop assertions the gate could not verify, then re-run the gate."""
    letter = case.appeal.letter
    assert letter is not None
    kept = [a for a in letter.assertions if a.verified]
    return AppealLetter(opening=letter.opening, closing=letter.closing, text=render_letter(case, letter, kept),
                        assertions=kept, gate_passed=bool(kept), unverified=0)
