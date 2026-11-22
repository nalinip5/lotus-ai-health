"""Deterministic offline LLM used for local runs, the demo feed and tests.

It answers each task from the structured `context` the agent supplies, using
simple rules over the synthetic clinical text. It never sees the network. The
real providers receive the same prompts and must return the same schemas.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

from ..agents.text import find_sentence, split_sections
from .base import LLM

HEADER_TO_TYPE = {
    "CLAIM PACKET COVER SHEET": "Cover sheet", "DISCHARGE SUMMARY": "Discharge summary",
    "HISTORY AND PHYSICAL": "History & physical", "EMERGENCY DEPARTMENT NOTE": "ED documentation",
    "RADIOLOGY REPORT": "Imaging report", "LABORATORY RESULTS": "Lab results",
    "OPERATIVE NOTE": "Operative note", "PROGRESS NOTE": "Clinical note",
}

INJECTION = re.compile(r"ignore (all|previous|prior)|system prompt|developer mode|jailbreak|"
                       r"\bssn\b|social security|export all|dump (the )?database|other patients|"
                       r"drop table|<script", re.I)
CLINICAL_TERMS = re.compile(r"admi|discharg|diagnos|lab|lactate|imaging|ct |x-ray|procedure|surgery|"
                            r"antibiotic|denial|appeal|evidence|criteria|telemetry|vital|blood pressure|"
                            r"oxygen|spo2|fever|pain|fracture|sepsis|pneumonia|medication|note|record|page", re.I)


class MockLLM(LLM):
    name = "mock"

    async def _complete(self, system: str, user: str, schema: dict[str, Any], task: str,
                        context: dict[str, Any]) -> str:
        await asyncio.sleep(0)  # yield, like a network call
        handler: Callable[[dict[str, Any]], dict[str, Any]] = getattr(self, f"_t_{task}")
        return json.dumps(handler(context))

    # -- structuring ----------------------------------------------------------
    def _t_classify_document(self, c: dict) -> dict:
        first = c["text"].strip().splitlines()[0].strip().upper() if c["text"].strip() else ""
        doc_type = HEADER_TO_TYPE.get(first, c.get("declared_type", "Unknown"))
        sections = split_sections(c["text"])
        for k in ("Patient Token", "Mrn", "Encounter"):
            sections.pop(k, None)
        return {"doc_type": doc_type, "confidence": 0.97 if first in HEADER_TO_TYPE else 0.5, "sections": sections}

    # -- summary --------------------------------------------------------------
    def _t_summarize_case(self, c: dict) -> dict:
        s = c["sections"]
        get = lambda doc, sec: s.get(doc, {}).get(sec, "")  # noqa: E731
        course = get("Discharge summary", "Hospital Course") or get("Clinical note", "Plan")
        findings = [x for x in (get("ED documentation", "Presentation"), get("Imaging report", "Findings"),
                                get("Lab results", "Results")) if x]
        timeline = []
        if c.get("admit_datetime"):
            timeline.append({"date": c["admit_datetime"], "event": "Admission"})
        if get("Operative note", "Procedure"):
            timeline.append({"date": c.get("service_date", ""), "event": get("Operative note", "Procedure")[:80]})
        dispo = get("Discharge summary", "Disposition") or "Not documented"
        m = re.search(r"on (\d{4}-\d{2}-\d{2})", dispo)
        if m:
            timeline.append({"date": m.group(1), "event": "Discharge"})
        return {"admit_type": c.get("admit_type", "Unknown"), "clinical_course": course or "Not documented",
                "key_findings": findings[:5], "disposition": dispo, "timeline": timeline}

    def _t_extract_admit(self, c: dict) -> dict:
        for p in c["pages"]:
            m = re.search(r"(admitted to inpatient status|placed in observation) on (\d{4}-\d{2}-\d{2} \d{2}:\d{2})",
                          p["text"])
            if m:
                return {"admit_datetime": m.group(2), "doc_id": p["doc_id"], "page": p["page"],
                        "quote": find_sentence(p["text"], [m.group(1)])}
        return {}

    # -- diagnosis reasoning --------------------------------------------------
    def _t_extract_diagnoses(self, c: dict) -> dict:
        out = []
        for p in c["pages"]:
            for m in re.finditer(r"DIAGNOSIS: (.+?) \(([A-Z]\d[\w.]*)\)\.", p["text"]):
                out.append({"description": m.group(1), "icd10": m.group(2), "doc_id": c["doc_id"],
                            "page": p["page"], "quote": m.group(0)})
            for m in re.finditer(r"ASSESSMENT: (.+?)\.", p["text"]):
                out.append({"description": m.group(1), "icd10": None, "doc_id": c["doc_id"],
                            "page": p["page"], "quote": m.group(0)})
        return {"diagnoses": out}

    def _t_rank_diagnoses(self, c: dict) -> dict:
        seen: dict[str, dict] = {}
        for d in c["candidates"]:
            key = d["description"].lower()[:30]
            if key not in seen or (d.get("icd10") and not seen[key].get("icd10")):
                seen[key] = d
        ranked = sorted(seen.values(), key=lambda d: (d.get("icd10") is None, len(d["description"])))
        return {"diagnoses": [{**d, "rank": i + 1,
                               "rationale": "Coded on discharge summary" if d.get("icd10") else "Physician assessment"}
                              for i, d in enumerate(ranked[: c.get("top_n", 4)])]}

    # -- evidence -------------------------------------------------------------
    def _t_find_evidence(self, c: dict) -> dict:
        quotes = []
        for p in c["pages"]:
            if p.get("unreadable"):
                continue
            s = find_sentence(p["text"], c["keywords"])
            if s:
                quotes.append({"page": p["page"], "quote": s,
                               "confidence": round(min(0.97, 0.55 + 0.45 * p.get("ocr", 0.95)), 2)})
        return {"found": bool(quotes), "quotes": quotes}

    # -- denial ---------------------------------------------------------------
    def _t_parse_denial(self, c: dict) -> dict:
        t = c["text"]
        code = re.search(r"Reason code ([A-Z]{2}-\d+)", t)
        date = re.search(r"Date: (\d{4}-\d{2}-\d{2})", t)
        line = re.search(r"Reason code .+", t)
        return {"code": code.group(1) if code else "UNKNOWN", "notice_date": date.group(1) if date else "",
                "source_text": line.group(0) if line else ""}

    def _t_triage_denial(self, c: dict) -> dict:
        met = c.get("criteria_met")
        n = c.get("evidence_count", 0)
        if met:
            rec = (f"Criteria met with {n} cited evidence item(s). Prepare an appeal with page citations "
                   f"addressing reason {c['code']}.")
            conf = 0.87
        elif met is False:
            rec = ("Criteria not met from the record as submitted. Request missing documentation or a "
                   "peer-to-peer review before appealing; consider write-off if none is available.")
            conf = 0.71
        else:
            rec = "Assemble packet evidence and prepare an appeal with page citations."
            conf = 0.6
        return {"category": c["category"], "confidence": conf, "recommendation": rec}

    # -- appeal letter --------------------------------------------------------
    def _t_draft_letter(self, c: dict) -> dict:
        assertions = []
        for q in c["questions"]:
            if q["evidence"]:
                ev = q["evidence"][0]
                assertions.append({"text": f"{q['text']}: the record documents \"{ev['quote']}\"",
                                   "doc_id": ev["doc_id"], "page": ev["page"], "quote": ev["quote"]})
            elif q.get("required"):
                # A model may assert unsupported criteria; the citation gate must catch it.
                assertions.append({"text": f"{q['text']} is supported by the medical record."})
        for g in c.get("unmet_groups", []):
            # Simulates an over-claiming model on a group the rules evaluated as NOT met.
            assertions.append({"text": f"{g}: criteria are satisfied for this admission."})
        return {
            "opening": (f"Re: Request for reconsideration — claim {c['case_id']}, encounter {c['encounter']}, "
                        f"service date {c['service_date']}. We appeal the denial dated {c['notice_date']} "
                        f"(reason {c['code']}: {c['reason']}). Evaluated against: {c['questionnaire']}."),
            "assertions": assertions,
            "closing": ("Based on the documentation cited above, we request that the determination be reversed "
                        "and the claim reprocessed for payment. Cited pages are enclosed."),
        }

    # -- chat guardrails ------------------------------------------------------
    def _t_guard_malicious(self, c: dict) -> dict:
        bad = INJECTION.search(c["question"])
        return {"allowed": not bad, "reason": f"Blocked pattern: '{bad.group(0)}'" if bad else "ok"}

    def _t_guard_relevant(self, c: dict) -> dict:
        ok = bool(CLINICAL_TERMS.search(c["question"]))
        return {"allowed": ok, "reason": "ok" if ok else "Question is not about this case's record."}

    def _t_chat_answer(self, c: dict) -> dict:
        words = [w for w in re.findall(r"[a-zA-Z0-9]{4,}", c["question"].lower())
                 if w not in {"what", "when", "does", "there", "this", "patient", "show", "which", "with", "have"}]
        cites = []
        for p in c["pages"]:
            s = find_sentence(p["text"], words)
            if s:
                cites.append({"doc_id": p["doc_id"], "page": p["page"], "quote": s})
        if not cites:
            return {"answer": "I could not find this in the submitted record.", "citations": []}
        return {"answer": " ".join(x["quote"] for x in cites[:3]), "citations": cites[:3]}
