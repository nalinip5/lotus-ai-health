"""Deterministic packet-integrity checks. No LLM decides readiness.

quality       — OCR confidence, skew, unreadable pages, type mismatch
identity      — every document's patient token / MRN must match the case
completeness  — payer policy checklist
readiness     — combines open blockers into the case status
"""
from __future__ import annotations

import re

from ..config import settings
from ..domain.models import Blocker, Case, CaseStatus, Finding
from ..domain.policies import PAYERS


def quality_findings(case: Case) -> list[Finding]:
    out: list[Finding] = []
    thr = settings.ocr_confidence_threshold
    for d in case.documents:
        for p in d.pages:
            fid = f"{d.id}-p{p.number}"
            if p.unreadable:
                out.append(Finding(id=f"{fid}-unreadable", doc_id=d.id, page=p.number, kind="unreadable",
                                   severity="critical",
                                   message=f"Page unreadable (OCR conf {p.ocr_confidence:.2f}). Rescan required."))
            elif p.skew_deg >= 5:
                out.append(Finding(id=f"{fid}-skew", doc_id=d.id, page=p.number, kind="skew", severity="minor",
                                   message=f"Page skew estimated at {p.skew_deg}°. OCR conf {p.ocr_confidence:.2f} "
                                           f"· threshold {thr:.2f}."))
            elif p.ocr_confidence < thr:
                out.append(Finding(id=f"{fid}-ocr", doc_id=d.id, page=p.number, kind="low_ocr", severity="minor",
                                   message=f"OCR conf {p.ocr_confidence:.2f} below threshold {thr:.2f}."))
        if d.classified_type and d.classified_type != d.declared_type:
            out.append(Finding(id=f"{d.id}-type", doc_id=d.id, page=1, kind="type_mismatch", severity="minor",
                               message=f"Declared '{d.declared_type}' but content classified as "
                                       f"'{d.classified_type}'."))
    return out


def identity_conflicts(case: Case) -> list[str]:
    """Read identifiers from each document's first page and compare with the case record."""
    conflicts = []
    for d in case.documents:
        text = d.pages[0].text if d.pages else ""
        pt = re.search(r"PATIENT TOKEN: (\S+)", text)
        mrn = re.search(r"MRN: (\S+)", text)
        if (pt and pt.group(1) != case.patient_token) or (mrn and mrn.group(1) != case.mrn):
            conflicts.append(f"{d.id} ({d.classified_type or d.declared_type}) carries "
                             f"{pt.group(1) if pt else '?'} / {mrn.group(1) if mrn else '?'}; "
                             f"case record is {case.patient_token} / {case.mrn}")
    return conflicts


PROFESSIONAL_REQUIRED = ["Cover sheet", "Clinical note"]


def checklist(case: Case) -> list[dict]:
    present = {d.classified_type or d.declared_type for d in case.documents}
    required = PROFESSIONAL_REQUIRED if case.claim_type == "professional" else PAYERS[case.payer]["required"]
    return [{"doc_type": t, "present": t in present} for t in required]


def evaluate_readiness(case: Case) -> None:
    """Rebuild blockers from current findings/checklist and set the status. Idempotent."""
    resolved = {b.id for b in case.blockers if b.resolved}
    blockers: list[Blocker] = []
    for item in checklist(case):
        if not item["present"]:
            bid = f"missing:{item['doc_type']}"
            blockers.append(Blocker(id=bid, kind="completeness", ref=item["doc_type"], resolved=bid in resolved,
                                    message=f"Missing mandatory document: {item['doc_type']}"))
    for c in identity_conflicts(case):
        bid = "identity:" + c.split(" ")[0]
        blockers.append(Blocker(id=bid, kind="identity", resolved=bid in resolved,
                                message=f"Patient/encounter identifier conflict unresolved — {c}"))
    for f in case.findings:
        if f.severity == "critical" and f.status in ("open", "rescan_requested"):
            blockers.append(Blocker(id=f"quality:{f.id}", kind="quality", ref=f.id,
                                    message=f"Critical scan defect: {f.message}"))
        elif f.severity == "minor" and f.status == "open":
            blockers.append(Blocker(id=f"quality:{f.id}", kind="quality", ref=f.id,
                                    message=f"Open quality finding: {f.message}"))
    case.blockers = blockers

    if case.status not in (CaseStatus.PROCESSING, CaseStatus.EXCEPTION, CaseStatus.AWAITING_RESCAN,
                           CaseStatus.QUARANTINED, CaseStatus.READY):
        return  # already past the integrity gate
    open_ = case.open_blockers
    if any(b.kind == "identity" for b in open_):
        case.status = CaseStatus.QUARANTINED
    elif any(f.status == "rescan_requested" for f in case.findings):
        case.status = CaseStatus.AWAITING_RESCAN
    elif open_:
        case.status = CaseStatus.EXCEPTION
    else:
        case.status = CaseStatus.READY
