"""Domain model for Lotus: claim packets, integrity findings, denials, appeals.

The evidence contract follows FHIR Questionnaire / QuestionnaireResponse
(a trimmed "FHIR-lite" shape so it stays readable; see fhir.py for export).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CaseStatus(str, Enum):
    PROCESSING = "processing"
    EXCEPTION = "exception"            # blocked from Ready by one or more blockers
    AWAITING_RESCAN = "awaiting_rescan"  # HIM rescan requested
    QUARANTINED = "quarantined"        # identity conflict, needs explicit reconciliation
    READY = "ready"                    # all deterministic checks passed
    SENT = "sent"                      # transmitted, payer ack unknown
    ACKNOWLEDGED = "acknowledged"      # payer confirmed receipt
    DENIED = "denied"                  # denial notice ingested
    APPEAL_PENDING = "appeal_pending"  # appeal drafted, waiting for human decision
    APPEAL_SENT = "appeal_sent"
    WRITTEN_OFF = "written_off"
    RETURNED = "returned"              # packet returned (different patient)
    ARCHIVED = "archived"


# ---------------------------------------------------------------- packet ----
class Page(BaseModel):
    number: int
    text: str
    ocr_confidence: float = 0.95
    skew_deg: float = 0.0
    unreadable: bool = False
    sha256: str = ""


class Document(BaseModel):
    id: str
    declared_type: str                    # what the cover sheet / filename claims
    classified_type: Optional[str] = None  # what the structuring agent decided
    version: int = 1
    patient_token: str
    mrn: str
    pages: list[Page]
    sha256: str = ""
    sections: dict[str, str] = Field(default_factory=dict)  # canonical sections


class Finding(BaseModel):
    id: str
    doc_id: str
    page: int
    kind: Literal["skew", "low_ocr", "unreadable", "type_mismatch"]
    severity: Literal["minor", "critical"]
    message: str
    status: Literal["open", "accepted", "rescan_requested", "resolved"] = "open"
    rationale: Optional[str] = None


class Blocker(BaseModel):
    id: str
    kind: Literal["completeness", "identity", "quality"]
    message: str
    ref: Optional[str] = None  # doc type / finding id
    resolved: bool = False


class AuditEntry(BaseModel):
    at: str = Field(default_factory=utcnow)
    actor: str = "system"
    action: str
    detail: str
    correlation_id: Optional[str] = None


# ---------------------------------------------------------------- FHIR-lite -
class Evidence(BaseModel):
    doc_id: str
    page: int
    section: Optional[str] = None
    quote: str
    confidence: float


class QuestionnaireItem(BaseModel):
    linkId: str
    text: str
    type: Literal["group", "boolean"] = "boolean"
    # group combination metadata (Evidence evaluator)
    operator: Optional[Literal["all", "at-least-one", "exactly-one", "at-least-n", "not-exist"]] = None
    n: Optional[int] = None
    arithmetic: Optional[Literal[">", ">="]] = None
    points: Optional[int] = None  # threshold for a group / weight for a leaf
    # retrieval hints used by the evidence agent (and the mock LLM)
    keywords: list[str] = Field(default_factory=list)
    item: list["QuestionnaireItem"] = Field(default_factory=list)


class Questionnaire(BaseModel):
    resourceType: Literal["Questionnaire"] = "Questionnaire"
    id: str
    title: str
    source: str  # e.g. "Payer policy", "Book of arguments"
    item: list[QuestionnaireItem]


class ResponseItem(BaseModel):
    linkId: str
    text: str
    answer: Optional[bool] = None
    evidence: list[Evidence] = Field(default_factory=list)
    summary: Optional[str] = None
    item: list["ResponseItem"] = Field(default_factory=list)


class QuestionnaireResponse(BaseModel):
    resourceType: Literal["QuestionnaireResponse"] = "QuestionnaireResponse"
    questionnaire: str
    subject: str
    status: Literal["in-progress", "completed"] = "completed"
    item: list[ResponseItem]


# ---------------------------------------------------------------- appeals ---
class Denial(BaseModel):
    code: str
    reason: str
    category: str = "unknown"
    notice_date: str
    appeal_window_days: int
    deadline: str
    deadline_verified: bool = False
    verified_by: Optional[str] = None
    letter_text: str
    source_text: str


class Citation(BaseModel):
    doc_id: str
    page: int
    quote: str


class Assertion(BaseModel):
    text: str
    citation: Optional[Citation] = None
    verified: bool = False


class AppealLetter(BaseModel):
    opening: str = ""
    closing: str = ""
    text: str
    assertions: list[Assertion]
    gate_passed: bool
    unverified: int


class Triage(BaseModel):
    category: str
    confidence: float
    recommendation: str


class Appeal(BaseModel):
    status: Literal["none", "evidence_ready", "drafted", "approved", "sent", "written_off"] = "none"
    thread_id: Optional[str] = None
    triage: Optional[Triage] = None
    diagnoses: list[dict[str, Any]] = Field(default_factory=list)
    questionnaire_id: Optional[str] = None
    response: Optional[QuestionnaireResponse] = None
    evaluation: Optional[dict[str, Any]] = None
    letter: Optional[AppealLetter] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    rationale: Optional[str] = None
    assigned_to: str = "J. Rivera"


class Submission(BaseModel):
    receipt: str
    sent_at: str
    ack: Literal["pending", "confirmed"] = "pending"
    ack_ref: Optional[str] = None


class Case(BaseModel):
    id: str
    patient_token: str
    mrn: str
    encounter: str
    payer: str
    policy_id: str
    service_date: str
    billed: float
    scenario: Optional[str] = None  # synthetic generator scenario (demo only)
    status: CaseStatus = CaseStatus.PROCESSING
    documents: list[Document] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    blockers: list[Blocker] = Field(default_factory=list)
    summary: Optional[dict[str, Any]] = None
    admit_datetime: Optional[str] = None
    denial: Optional[Denial] = None
    appeal: Appeal = Field(default_factory=Appeal)
    submission: Optional[Submission] = None
    audit: list[AuditEntry] = Field(default_factory=list)
    correlation_id: Optional[str] = None
    created_at: str = Field(default_factory=utcnow)
    updated_at: str = Field(default_factory=utcnow)

    def log(self, action: str, detail: str, actor: str = "system", correlation_id: str | None = None) -> None:
        self.audit.append(AuditEntry(action=action, detail=detail, actor=actor,
                                     correlation_id=correlation_id or self.correlation_id))
        self.updated_at = utcnow()

    @property
    def open_blockers(self) -> list[Blocker]:
        return [b for b in self.blockers if not b.resolved]

    def doc(self, doc_id: str) -> Optional[Document]:
        return next((d for d in self.documents if d.id == doc_id), None)


class Notification(BaseModel):
    id: str
    category: Literal["denial", "deadline", "ready", "rescan", "approval", "submission"]
    subject: str
    body: str
    to: str
    case_id: Optional[str] = None
    read: bool = False
    at: str = Field(default_factory=utcnow)


class Event(BaseModel):
    id: str
    type: str
    title: str
    detail: str
    case_id: Optional[str] = None
    tone: Literal["info", "warn", "ok", "bad"] = "info"
    at: str = Field(default_factory=utcnow)
