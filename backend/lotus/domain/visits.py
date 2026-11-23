"""Visit-to-Claim domain: telehealth visits from Lotus BPM Health become clean claim packets.

Payer telehealth rules below are illustrative demo content, not real payer policy.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import AuditEntry, utcnow


class VisitStatus(str, Enum):
    RECEIVED = "received"        # note arrived from the telehealth platform
    CODING = "coding"            # workflow running
    REVIEW = "review"            # waiting for staff
    RETURNED = "returned"        # sent back to the clinician for documentation
    CLAIMED = "claimed"          # approved; claim packet created in the console
    REJECTED = "rejected"        # not billable


class CodeLine(BaseModel):
    kind: Literal["ICD-10", "CPT", "CDT"]
    code: str
    description: str
    modifiers: list[str] = Field(default_factory=list)
    source: Literal["clinician", "ai", "rule", "staff"] = "clinician"
    quote: Optional[str] = None          # evidence in the note (diagnoses)
    verified: Optional[bool] = None      # quote found verbatim in the note


class Risk(BaseModel):
    id: str
    rule: str
    severity: Literal["critical", "high", "medium"]
    message: str
    fix: Optional[str] = None            # human-readable suggested fix
    fix_action: Optional[str] = None     # machine action the staff can apply (code changes only)
    needs_clinician: bool = False        # documentation gap: only the clinician can fix
    acceptable: bool = True              # False for compliance risks (e.g. upcoding): must be fixed
    status: Literal["open", "fixed", "accepted"] = "open"
    rationale: Optional[str] = None


class Visit(BaseModel):
    id: str
    patient_token: str
    mrn: str
    payer: str
    clinician: str
    specialty: Literal["medical", "dental"]
    modality: Literal["video", "audio", "async"] = "video"
    visit_date: str
    note: str
    version: int = 1
    submitted_codes: list[CodeLine] = Field(default_factory=list)   # what the clinician picked
    codes: list[CodeLine] = Field(default_factory=list)             # claim lines after AI + rules + staff
    pos: Optional[str] = None
    documented_minutes: Optional[int] = None
    sections: dict[str, str] = Field(default_factory=dict)
    risks: list[Risk] = Field(default_factory=list)
    risk_score: int = 0                  # 0 = clean, 100 = will certainly deny
    initial_risk_score: Optional[int] = None  # score when first coded (before any fix)
    fixes_applied: int = 0
    status: VisitStatus = VisitStatus.RECEIVED
    case_id: Optional[str] = None
    billed: float = 0.0
    return_reason: Optional[str] = None
    audit: list[AuditEntry] = Field(default_factory=list)
    correlation_id: Optional[str] = None
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    def log(self, action: str, detail: str, actor: str = "system") -> None:
        self.audit.append(AuditEntry(action=action, detail=detail, actor=actor, correlation_id=self.correlation_id))
        self.updated_at = utcnow()

    @property
    def open_risks(self) -> list[Risk]:
        return [r for r in self.risks if r.status == "open"]


# Time-based office/outpatient E/M for established patients (illustrative thresholds).
EM_BY_MINUTES = [(40, "99215", 4), (30, "99214", 3), (20, "99213", 2), (10, "99212", 1)]
EM_DESC = {"99212": "Office/outpatient visit, est. patient, straightforward",
           "99213": "Office/outpatient visit, est. patient, low complexity",
           "99214": "Office/outpatient visit, est. patient, moderate complexity",
           "99215": "Office/outpatient visit, est. patient, high complexity"}
EM_FEE = {"99212": 78.0, "99213": 112.0, "99214": 165.0, "99215": 228.0, "D9995": 45.0, "D0140": 68.0}

# Demo payer telehealth rules.
TELEHEALTH_RULES: dict[str, dict] = {
    "UnitedHealthcare Choice+": {"modifier_95": True, "teledentistry": False},
    "Aetna Choice POS": {"modifier_95": True, "teledentistry": False},
    "Humana Gold MA": {"modifier_95": False, "teledentistry": False},
    "Cigna HealthSpring": {"modifier_95": True, "teledentistry": True},
    "BlueCross PPO": {"modifier_95": True, "teledentistry": True},
}

SEVERITY_WEIGHT = {"critical": 45, "high": 25, "medium": 10}
