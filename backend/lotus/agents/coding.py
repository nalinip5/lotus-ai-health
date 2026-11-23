"""Visit-to-Claim agents: evidence-bound coding and denial-risk rules.

Split of responsibilities
  LLM   — read the note, propose diagnoses (ICD-10) with a verbatim quote for each.
  Rules — verify every quote against the note (evidence-bound coding), derive E/M level from
          documented time, POS from patient location, payer telehealth modifiers, documentation
          completeness, and compute a denial-risk score.
  Staff — apply code fixes, accept a risk with a rationale, or return the note to the clinician.
          Staff can never add clinical documentation; only the clinician can (closed loop).

Risk score: each open risk contributes a probability of denial p = base(severity) x m(payer, rule),
where m is learned from real claim outcomes (OutcomeLearner). Score = 100 x (1 - prod(1 - p)).
"""
from __future__ import annotations

import json
import re
from typing import Optional

from pydantic import BaseModel

from ..domain.visits import (EM_BY_MINUTES, EM_DESC, EM_FEE, TELEHEALTH_RULES, CodeLine, Risk, Visit)
from ..llm.base import LLM
from ..observability import tracer
from ..store.docstore import DocStore
from .text import quote_in, split_sections


# ------------------------------------------------------------------ LLM: diagnoses with evidence
class DxCandidate(BaseModel):
    icd10: str
    description: str
    quote: str


class DxList(BaseModel):
    diagnoses: list[DxCandidate]


async def code_diagnoses(llm: LLM, visit: Visit) -> list[CodeLine]:
    async with tracer.span("agent.coding", kind="agent", visit=visit.id):
        out = await llm.structured(
            "code_visit_dx",
            "You are a certified medical coder. From the ASSESSMENT of this telehealth note, list the ICD-10-CM "
            "diagnoses the clinician actually assessed. For each, copy the exact supporting sentence from the note "
            "as `quote`. Do not code symptoms that are integral to a coded diagnosis, rule-outs or history.",
            visit.note, DxList, {"note": visit.note, "specialty": visit.specialty})
    lines = []
    for d in out.diagnoses:
        lines.append(CodeLine(kind="ICD-10", code=d.icd10, description=d.description, source="ai",
                              quote=d.quote, verified=quote_in(d.quote, visit.note)))
    return lines


# ------------------------------------------------------------------ deterministic extraction
def documented_minutes(note: str) -> Optional[int]:
    m = re.search(r"total time[^.\n]*?(\d{1,3})\s*minutes", note, re.I)
    return int(m.group(1)) if m else None


def patient_location_pos(note: str) -> Optional[str]:
    m = re.search(r"patient (?:was )?located at ([^.;\n]+)", note, re.I)
    if not m:
        return None
    return "10" if "home" in m.group(1).lower() else "02"


def has_consent(note: str) -> bool:
    return bool(re.search(r"consent[^.\n]*(telehealth|virtual|video)|(telehealth|virtual|video)[^.\n]*consent", note, re.I))


def supported_em(minutes: Optional[int]) -> Optional[str]:
    if minutes is None:
        return None
    for threshold, code, _ in EM_BY_MINUTES:
        if minutes >= threshold:
            return code
    return None


def em_rank(code: str) -> int:
    return next((r for _, c, r in EM_BY_MINUTES if c == code), 0)


# ------------------------------------------------------------------ outcome learning
class OutcomeLearner:
    """Per (payer, rule) denial rate learned from claims that went out with that risk accepted.

    Beta(1,1) prior; multiplier m = 0.25 + 1.5 * posterior mean. With no data the mean is 0.5, so m = 1.0;
    it moves toward 0.25 (risk rarely denied in practice) or 1.75 (usually denied) as outcomes arrive.
    """
    COLL = "risk_outcomes"

    def __init__(self, store: DocStore):
        self.store = store

    def _key(self, payer: str, rule: str) -> str:
        return f"{payer}|{rule}"

    def stats(self, payer: str, rule: str) -> dict:
        return self.store.get(self.COLL, self._key(payer, rule)) or {"payer": payer, "rule": rule, "n": 0, "denied": 0}

    def multiplier(self, payer: str, rule: str) -> float:
        s = self.stats(payer, rule)
        return round(0.25 + 1.5 * (s["denied"] + 1) / (s["n"] + 2), 3)

    def record(self, payer: str, rules: list[str], denied: bool) -> None:
        for rule in set(rules):
            s = self.stats(payer, rule)
            s["n"] += 1
            s["denied"] += int(denied)
            self.store.put(self.COLL, self._key(payer, rule), s)

    def all(self) -> list[dict]:
        return self.store.find(self.COLL, limit=1000)


# Compliance: billing above what the note supports must be corrected, never waived.
NON_ACCEPTABLE = {"em_overcoded", "dx_unsupported"}

BASE_P = {"critical": 0.9, "high": 0.45, "medium": 0.12}


def risk_probability(risk: Risk, payer: str, learner: Optional[OutcomeLearner]) -> float:
    m = learner.multiplier(payer, risk.rule) if learner else 1.0
    return min(0.99, BASE_P[risk.severity] * m)


def score(risks: list[Risk], payer: str, learner: Optional[OutcomeLearner]) -> int:
    keep = 1.0
    for r in risks:
        if r.status == "open":
            keep *= 1 - risk_probability(r, payer, learner)
    return round(100 * (1 - keep))


def fix_impact(visit: Visit, learner: Optional[OutcomeLearner]) -> dict[str, int]:
    """For each open risk: the score if that risk alone were resolved (counterfactual preview)."""
    out = {}
    for r in visit.open_risks:
        others = [x for x in visit.risks if x.id != r.id]
        out[r.id] = score(others, visit.payer, learner)
    return out


# ------------------------------------------------------------------ rules
def evaluate_risks(visit: Visit, ai_dx: list[CodeLine]) -> None:
    """Rebuild claim lines and risks from the note + clinician codes. Keeps staff decisions on risks."""
    note = visit.note
    prior = {r.id: r for r in visit.risks}
    risks: list[Risk] = []

    def add(rid: str, rule: str, sev: str, msg: str, fix: str | None = None, action: str | None = None,
            clinician: bool = False) -> None:
        r = Risk(id=rid, rule=rule, severity=sev, message=msg, fix=fix, fix_action=action,  # type: ignore[arg-type]
                 needs_clinician=clinician,
                 acceptable=sev != "critical" and not clinician and rule not in NON_ACCEPTABLE)
        if rid in prior and prior[rid].status == "accepted":
            r.status, r.rationale = "accepted", prior[rid].rationale
        risks.append(r)

    visit.sections = split_sections(note)
    visit.documented_minutes = documented_minutes(note)
    visit.pos = patient_location_pos(note)
    rules = TELEHEALTH_RULES.get(visit.payer, {"modifier_95": True, "teledentistry": False})

    # Claim lines start from the current lines (clinician's, then staff-edited) — never silently changed.
    lines = [c.model_copy() for c in (visit.codes or visit.submitted_codes)]
    verified_ai = {d.code: d for d in ai_dx if d.verified}

    # 1. Evidence-bound coding: every billed diagnosis must be supported by a verified quote.
    for line in [c for c in lines if c.kind == "ICD-10"]:
        if line.code in verified_ai:
            line.quote, line.verified = verified_ai[line.code].quote, True
        else:
            line.verified = False
            alt = next(iter(verified_ai.values()), None)
            add(f"dx:{line.code}", "dx_unsupported", "critical",
                f"{line.code} ({line.description}) is not supported by the note.",
                f"Replace with {alt.code} ({alt.description}), which the note supports" if alt else
                "Remove the code or ask the clinician to document it",
                f"replace_dx:{line.code}:{alt.code}" if alt else f"remove_code:{line.code}")
    billed_dx = {c.code for c in lines if c.kind == "ICD-10"}
    for code, d in verified_ai.items():
        if code not in billed_dx:
            add(f"dx_missing:{code}", "dx_missing", "medium",
                f"The note supports {code} ({d.description}) but it isn't on the claim.",
                f"Add {code}", f"add_dx:{code}")

    # 2. Documentation completeness (clinician-only fixes).
    if not has_consent(note):
        add("consent", "telehealth_consent", "high", "Patient consent to telehealth isn't documented.",
            "Ask the clinician for an addendum documenting consent", clinician=True)
    if visit.pos is None:
        add("location", "patient_location", "medium", "Patient location isn't documented, so POS can't be set.",
            "Ask the clinician to document where the patient was during the visit", clinician=True)

    # 3. E/M level must match documented time.
    em = next((c for c in lines if c.kind == "CPT" and c.code in EM_DESC), None)
    if em:
        supported = supported_em(visit.documented_minutes)
        if supported is None:
            add("em_time", "em_time_missing", "critical",
                f"{em.code} is billed but total time isn't documented.",
                "Ask the clinician to document total time on the date of the encounter", clinician=True)
        elif em_rank(em.code) > em_rank(supported):
            add("em_over", "em_overcoded", "high",
                f"{em.code} needs more time than the {visit.documented_minutes} minutes documented.",
                f"Change to {supported}", f"set_em:{supported}")
        elif em_rank(em.code) < em_rank(supported):
            add("em_under", "em_undercoded", "medium",
                f"{visit.documented_minutes} documented minutes support {supported}; {em.code} under-bills.",
                f"Change to {supported}", f"set_em:{supported}")
        # 4. Payer telehealth modifier.
        if rules["modifier_95"] and visit.modality == "video" and "95" not in em.modifiers:
            add("mod95", "modifier_95", "high", f"{visit.payer} requires modifier 95 on synchronous telehealth.",
                "Add modifier 95", "add_modifier:95")

    # 5. Dental telehealth coverage.
    if visit.specialty == "dental" and not rules["teledentistry"]:
        add("teledent", "teledentistry_coverage", "high",
            f"{visit.payer} doesn't cover teledentistry in this demo policy.",
            "Verify benefits, or accept with a rationale (e.g. patient self-pay agreed)")

    visit.codes = lines
    visit.risks = risks
    visit.billed = round(sum(EM_FEE.get(c.code, 0.0) for c in lines if c.kind in ("CPT", "CDT")), 2)


def apply_fix(visit: Visit, action: str) -> str:
    """Apply a code-level fix. Returns a description for the audit log."""
    kind, _, arg = action.partition(":")
    if kind == "set_em":
        for c in visit.codes:
            if c.kind == "CPT" and c.code in EM_DESC:
                old, c.code, c.description, c.source = c.code, arg, EM_DESC[arg], "rule"
                return f"E/M {old} → {arg}"
    if kind == "add_modifier":
        for c in visit.codes:
            if c.kind == "CPT" and c.code in EM_DESC and arg not in c.modifiers:
                c.modifiers.append(arg)
                c.source = "rule"
                return f"Added modifier {arg} to {c.code}"
    if kind == "replace_dx":
        old, new = arg.split(":")
        visit.codes = [c for c in visit.codes if not (c.kind == "ICD-10" and c.code == old)]
        if not any(c.kind == "ICD-10" and c.code == new for c in visit.codes):
            visit.codes.insert(0, CodeLine(kind="ICD-10", code=new, description="", source="ai"))
        return f"Diagnosis {old} → {new}"
    if kind == "add_dx":
        visit.codes.append(CodeLine(kind="ICD-10", code=arg, description="", source="ai"))
        return f"Added diagnosis {arg}"
    if kind == "remove_code":
        visit.codes = [c for c in visit.codes if c.code != arg]
        return f"Removed {arg}"
    raise ValueError(f"Unknown fix action {action}")


def fill_descriptions(visit: Visit, ai_dx: list[CodeLine]) -> None:
    desc = {d.code: d.description for d in ai_dx}
    for c in visit.codes:
        if not c.description:
            c.description = desc.get(c.code, c.description)


def serialize_dx(ai_dx: list[CodeLine]) -> list[dict]:
    return [json.loads(d.model_dump_json()) for d in ai_dx]
