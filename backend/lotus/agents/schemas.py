"""Structured output schemas for every LLM task (one schema per task)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class DocClassification(BaseModel):
    doc_type: str
    confidence: float
    sections: dict[str, str] = Field(default_factory=dict, description="canonical section name -> text")


class TimelineEvent(BaseModel):
    date: str
    event: str


class CaseSummary(BaseModel):
    admit_type: str = Field(description="ED | Direct | Scheduled | Unknown")
    clinical_course: str
    key_findings: list[str]
    disposition: str
    timeline: list[TimelineEvent]


class AdmitExtraction(BaseModel):
    admit_datetime: Optional[str] = None
    doc_id: Optional[str] = None
    page: Optional[int] = None
    quote: Optional[str] = None


class Diagnosis(BaseModel):
    description: str
    icd10: Optional[str] = None
    doc_id: str
    page: int
    quote: str


class DiagnosisList(BaseModel):
    diagnoses: list[Diagnosis]


class RankedDiagnosis(Diagnosis):
    rank: int
    rationale: str


class RankedDiagnoses(BaseModel):
    diagnoses: list[RankedDiagnosis]


class EvidenceQuote(BaseModel):
    page: int
    quote: str
    confidence: float


class EvidenceSearch(BaseModel):
    found: bool
    quotes: list[EvidenceQuote] = Field(default_factory=list)


class DenialParse(BaseModel):
    code: str
    notice_date: str
    source_text: str


class TriageOut(BaseModel):
    category: str
    confidence: float
    recommendation: str


class DraftAssertion(BaseModel):
    text: str
    doc_id: Optional[str] = None
    page: Optional[int] = None
    quote: Optional[str] = None


class LetterDraft(BaseModel):
    opening: str
    assertions: list[DraftAssertion]
    closing: str


class GuardVerdict(BaseModel):
    allowed: bool
    reason: str


class ChatCitation(BaseModel):
    doc_id: str
    page: int
    quote: str


class ChatAnswer(BaseModel):
    answer: str
    citations: list[ChatCitation] = Field(default_factory=list)
