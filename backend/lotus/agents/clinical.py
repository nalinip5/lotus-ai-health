"""LLM agents over the medical record: structuring, summary, intent-to-admit, diagnosis reasoning.

Each function is a single agent step; the LangGraph workflows decide order,
parallelism and persistence.
"""
from __future__ import annotations

import asyncio
import json

from ..domain.models import Case, Document
from ..llm.base import LLM
from ..observability import tracer
from .schemas import AdmitExtraction, CaseSummary, DiagnosisList, DocClassification, RankedDiagnoses

SYSTEM_STRUCTURE = ("You are the Structuring agent for clinical claim packets. Classify the document type from "
                    "this list: Cover sheet, History & physical, Discharge summary, Operative note, Imaging report, "
                    "Lab results, Clinical note, ED documentation, Orders. Split the text into canonical sections "
                    "(e.g. Assessment, Plan, Hospital Course, Findings, Impression, Results). Copy text verbatim.")


async def structure_document(llm: LLM, doc: Document) -> DocClassification:
    text = "\n".join(p.text for p in doc.pages if not p.unreadable)
    async with tracer.span("agent.structuring", kind="agent", doc_id=doc.id):
        out = await llm.structured(
            "classify_document", SYSTEM_STRUCTURE,
            f"Declared type: {doc.declared_type}\n\nDocument text:\n{text}", DocClassification,
            {"declared_type": doc.declared_type, "text": text})
    doc.classified_type = out.doc_type
    doc.sections = out.sections
    return out


def _sections_by_type(case: Case) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for d in case.documents:
        out.setdefault(d.classified_type or d.declared_type, {}).update(d.sections)
    return out


async def summarize_case(llm: LLM, case: Case) -> CaseSummary:
    sections = _sections_by_type(case)
    admit_type = "ED" if "ED documentation" in sections and case.admit_datetime else (
        "Direct" if case.admit_datetime else "Unknown")
    async with tracer.span("agent.summary", kind="agent"):
        return await llm.structured(
            "summarize_case",
            "You are the Summary agent. Summarize the structured medical record to about one tenth of its size "
            "for a human reviewer: admit type (ED/Direct/Scheduled), clinical course, key findings, disposition "
            "and a dated timeline. Use only facts present in the sections.",
            json.dumps(sections), CaseSummary,
            {"sections": sections, "admit_type": admit_type, "admit_datetime": case.admit_datetime,
             "service_date": case.service_date})


async def extract_admit(llm: LLM, case: Case) -> AdmitExtraction:
    pages = [{"doc_id": d.id, "page": p.number, "text": p.text} for d in case.documents
             if (d.classified_type or d.declared_type) in ("Discharge summary", "Orders", "History & physical")
             for p in d.pages if not p.unreadable]
    async with tracer.span("agent.intent_to_admit", kind="agent"):
        return await llm.structured(
            "extract_admit",
            "You are the Intent-to-Admit agent. Find the date-time of the inpatient admission order (or observation "
            "placement) and quote the sentence verbatim with its document id and page.",
            json.dumps(pages), AdmitExtraction, {"pages": pages})


async def diagnosis_reasoning(llm: LLM, case: Case, denial_text: str = "", top_n: int = 4) -> RankedDiagnoses:
    """Extract diagnoses from discharge summary, H&P and ED documentation in parallel, then rank top-N."""
    targets = [d for d in case.documents
               if (d.classified_type or d.declared_type) in ("Discharge summary", "History & physical",
                                                              "ED documentation", "Clinical note")]

    async def one(d: Document) -> DiagnosisList:
        pages = [{"page": p.number, "text": p.text} for p in d.pages if not p.unreadable]
        return await llm.structured(
            "extract_diagnoses",
            "Extract every diagnosis stated in this document with ICD-10 code if written, the page and a verbatim "
            "quote. Do not infer diagnoses that are not written.",
            json.dumps(pages), DiagnosisList, {"doc_id": d.id, "pages": pages})

    async with tracer.span("agent.diagnosis_reasoning", kind="agent", sections=len(targets)):
        lists = await asyncio.gather(*(one(d) for d in targets))
        candidates = [x.model_dump() for lst in lists for x in lst.diagnoses]
        return await llm.structured(
            "rank_diagnoses",
            f"Rank and de-duplicate these diagnoses; return the top {top_n} most relevant to the denial.",
            json.dumps({"candidates": candidates, "denial": denial_text}), RankedDiagnoses,
            {"candidates": candidates, "denial_text": denial_text, "top_n": top_n})
