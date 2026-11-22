"""Evidence agent (LLM retrieval) and Evidence evaluator (deterministic rules).

Evidence agent: for every leaf question, search each medical-record document in
parallel, rank the quotes, drop those under the confidence threshold, and fill
a QuestionnaireResponse with page-level citations.

Evaluator: combines leaf answers into group answers with explicit operators.
Semantics (fixing ambiguities in the source design):
  all           every child true                    (logical AND)
  at-least-one  >= 1 child true                     (logical OR)
  exactly-one   exactly 1 child true                (e.g. one level of care)
  at-least-n    >= n children true
  not-exist     no child true                       (exclusions / absence tests)
  points        sum(points of true children) {>|>=} group.points
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..config import settings
from ..domain.models import (Case, Evidence, Questionnaire, QuestionnaireItem, QuestionnaireResponse,
                             ResponseItem)
from ..llm.base import LLM
from ..observability import tracer
from .schemas import EvidenceSearch

SYSTEM_EVIDENCE = ("You are the Evidence agent. Given one criterion and the pages of one medical-record document, "
                   "return verbatim sentences that satisfy the criterion, with page numbers and a confidence 0-1. "
                   "If nothing satisfies it, return found=false. Never paraphrase or invent text.")


def leaves(items: list[QuestionnaireItem]) -> list[QuestionnaireItem]:
    out = []
    for it in items:
        out.extend(leaves(it.item) if it.type == "group" else [it])
    return out


async def search_leaf(llm: LLM, case: Case, q: QuestionnaireItem) -> list[Evidence]:
    docs = [d for d in case.documents if (d.classified_type or d.declared_type) != "Cover sheet"]

    async def per_doc(d) -> list[Evidence]:
        pages = [{"page": p.number, "text": p.text, "ocr": p.ocr_confidence, "unreadable": p.unreadable}
                 for p in d.pages]
        res = await llm.structured(
            "find_evidence", SYSTEM_EVIDENCE,
            f"Criterion: {q.text}\nDocument: {d.classified_type}\nPages: {json.dumps(pages)}", EvidenceSearch,
            {"question": q.text, "keywords": q.keywords, "pages": pages})
        return [Evidence(doc_id=d.id, page=x.page, quote=x.quote, confidence=x.confidence,
                         section=d.classified_type) for x in res.quotes]

    async with tracer.span("agent.evidence.question", kind="agent", linkId=q.linkId, docs=len(docs)):
        found = [e for lst in await asyncio.gather(*(per_doc(d) for d in docs)) for e in lst]
    # Rank tool: highest confidence first, prefer primary clinical documents, keep top 3.
    priority = {"Discharge summary": 0, "History & physical": 1, "ED documentation": 2}
    found.sort(key=lambda e: (-e.confidence, priority.get(e.section or "", 9)))
    return [e for e in found if e.confidence >= settings.evidence_confidence_threshold][:3]


async def run_evidence_agent(llm: LLM, case: Case, qn: Questionnaire) -> QuestionnaireResponse:
    qs = leaves(qn.item)
    async with tracer.span("agent.evidence", kind="agent", questionnaire=qn.id, questions=len(qs)):
        results = await asyncio.gather(*(search_leaf(llm, case, q) for q in qs))
    by_id = {q.linkId: ev for q, ev in zip(qs, results)}

    def build(items: list[QuestionnaireItem]) -> list[ResponseItem]:
        out = []
        for it in items:
            if it.type == "group":
                out.append(ResponseItem(linkId=it.linkId, text=it.text, item=build(it.item)))
            else:
                ev = by_id[it.linkId]
                out.append(ResponseItem(linkId=it.linkId, text=it.text, answer=bool(ev), evidence=ev,
                                        summary=ev[0].quote if ev else "No supporting evidence found"))
        return out

    return QuestionnaireResponse(questionnaire=qn.id, subject=case.id, item=build(qn.item))


# --------------------------------------------------------------- evaluator ---
def evaluate(qn: Questionnaire, resp: QuestionnaireResponse) -> dict[str, Any]:
    """Pure function: evaluates group questions and writes group answers back into `resp`."""
    groups: dict[str, Any] = {}
    answers = {r.linkId: r for r in _walk(resp.item)}

    def ev(item: QuestionnaireItem) -> bool:
        if item.type != "group":
            return bool(answers[item.linkId].answer)
        child = [(c, ev(c)) for c in item.item]
        true_n = sum(1 for _, v in child if v)
        if item.arithmetic:
            pts = sum(c.points or 0 for c, v in child if v)
            met = pts > (item.points or 0) if item.arithmetic == ">" else pts >= (item.points or 0)
            detail = {"rule": f"points {item.arithmetic} {item.points}", "points": pts}
        else:
            op = item.operator or "all"
            met = {"all": true_n == len(child), "at-least-one": true_n >= 1, "exactly-one": true_n == 1,
                   "at-least-n": true_n >= (item.n or 1), "not-exist": true_n == 0}[op]
            detail = {"rule": op + (f" (n={item.n})" if op == "at-least-n" else "")}
        groups[item.linkId] = {"text": item.text, "met": met, "true": true_n, "of": len(child), **detail}
        answers[item.linkId].answer = met
        return met

    with_results = [ev(i) for i in qn.item]
    return {"met": all(with_results), "groups": groups}


def _walk(items: list[ResponseItem]):
    for it in items:
        yield it
        yield from _walk(it.item)


def flat_questions(qn: Questionnaire, resp: QuestionnaireResponse) -> list[dict[str, Any]]:
    """Leaf questions with evidence; `required` marks leaves under an `all` group (used for drafting)."""
    answers = {r.linkId: r for r in _walk(resp.item)}
    out: list[dict[str, Any]] = []

    def walk(items: list[QuestionnaireItem], required: bool) -> None:
        for it in items:
            if it.type == "group":
                if it.operator == "not-exist":
                    continue  # exclusions are not argued in the letter
                walk(it.item, required and (it.operator in (None, "all")))
            else:
                r = answers[it.linkId]
                out.append({"linkId": it.linkId, "text": it.text, "required": required,
                            "evidence": [e.model_dump() for e in r.evidence]})

    walk(qn.item, True)
    return out
