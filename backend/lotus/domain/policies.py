"""Payer packet policies, denial reason codes and appeal criteria questionnaires.

All content here is illustrative demo content written for Lotus. Licensed
criteria sets (InterQual, MCG, payer-specific) plug in as additional
Questionnaire resources at runtime; nothing here reproduces them.
"""
from __future__ import annotations

from .models import Questionnaire, QuestionnaireItem as Q

PAYERS: dict[str, dict] = {
    "UnitedHealthcare Choice+": {"policy": "UHC-07 policy v4.6", "appeal_days": 45,
                                  "required": ["Cover sheet", "Discharge summary", "Operative note", "Imaging report"]},
    "Aetna Choice POS": {"policy": "AET-12 policy v2.1", "appeal_days": 60,
                         "required": ["Cover sheet", "Discharge summary", "Lab results"]},
    "Humana Gold MA": {"policy": "HUM-MA policy v3.0", "appeal_days": 65,
                       "required": ["Cover sheet", "History & physical", "Discharge summary", "Lab results"]},
    "Cigna HealthSpring": {"policy": "CHS-04 policy v1.9", "appeal_days": 60,
                           "required": ["Cover sheet", "Clinical note", "Lab results"]},
    "BlueCross PPO": {"policy": "BCB-02 policy v5.2", "appeal_days": 180,
                      "required": ["Cover sheet", "Discharge summary"]},
}

DOC_TYPES = ["Cover sheet", "History & physical", "Discharge summary", "Operative note",
             "Imaging report", "Lab results", "Clinical note", "ED documentation", "Orders"]

# Claim Adjustment Reason Codes used by the demo, grouped into appeal categories.
DENIAL_CODES: dict[str, dict] = {
    "CO-16": {"reason": "Claim/service lacks information or has submission/billing error(s).",
              "category": "documentation"},
    "CO-50": {"reason": "Non-covered: not deemed a medical necessity by the payer.",
              "category": "medical_necessity"},
    "CO-197": {"reason": "Precertification/authorization/notification absent.",
               "category": "authorization"},
    "CO-11": {"reason": "The diagnosis is inconsistent with the procedure.",
              "category": "coding"},
}

CATEGORY_TO_QUESTIONNAIRE = {
    "documentation": "Q-DOC-COMPLETE",
    "medical_necessity": "Q-INPATIENT-NECESSITY",
    "authorization": "Q-AUTH-EXCEPTION",
    "coding": "Q-DX-PROCEDURE",
}

QUESTIONNAIRES: dict[str, Questionnaire] = {
    "Q-INPATIENT-NECESSITY": Questionnaire(
        id="Q-INPATIENT-NECESSITY", title="Inpatient level of care — medical necessity", source="Lotus demo criteria",
        item=[Q(linkId="LOC", text="Inpatient level of care is supported", type="group", operator="all", item=[
            Q(linkId="SI", text="Severity of illness", type="group", operator="at-least-one", item=[
                Q(linkId="SI.1", text="Hemodynamic instability (SBP < 90 or MAP < 65)",
                  keywords=["hypotension", "sbp", "map <", "blood pressure 8", "pressors"]),
                Q(linkId="SI.2", text="Lactate > 2 mmol/L or evidence of sepsis",
                  keywords=["lactate", "sepsis", "septic"]),
                Q(linkId="SI.3", text="Hypoxemia (SpO2 < 90% on room air)",
                  keywords=["spo2 8", "hypox", "sat 8", "oxygen saturation 8"]),
            ]),
            Q(linkId="IS", text="Intensity of service", type="group", operator="at-least-n", n=2, item=[
                Q(linkId="IS.1", text="IV antibiotics or IV medication administered",
                  keywords=["iv antibiotic", "intravenous", "piperacillin", "vancomycin", "ceftriaxone"]),
                Q(linkId="IS.2", text="Continuous cardiac or telemetry monitoring",
                  keywords=["telemetry", "cardiac monitoring", "continuous monitoring"]),
                Q(linkId="IS.3", text="Serial labs at least every 12 hours",
                  keywords=["every 12 hours", "q12h"]),
            ]),
            Q(linkId="EX", text="No exclusion present", type="group", operator="not-exist", item=[
                Q(linkId="EX.1", text="Observation-only order without admission",
                  keywords=["observation only", "place in observation"]),
            ]),
        ])]),
    "Q-DOC-COMPLETE": Questionnaire(
        id="Q-DOC-COMPLETE", title="Documentation sufficiency", source="Payer policy",
        item=[Q(linkId="DOC", text="Documentation supports the services billed", type="group", operator="all", item=[
            Q(linkId="DOC.1", text="Signed operative / procedure note present",
              keywords=["procedure performed", "operative note", "electronically signed"]),
            Q(linkId="DOC.2", text="Indication for the procedure documented",
              keywords=["indication", "presented with", "chief complaint"]),
            Q(linkId="DOC.3", text="Diagnostic result supporting the diagnosis",
              keywords=["impression", "ct ", "x-ray", "ultrasound", "findings"]),
        ])]),
    "Q-AUTH-EXCEPTION": Questionnaire(
        id="Q-AUTH-EXCEPTION", title="Authorization exception (emergent admission)", source="Payer policy",
        item=[Q(linkId="AUTH", text="Emergency exception to prior authorization applies", type="group",
                operator="at-least-one", item=[
            Q(linkId="AUTH.1", text="Admission through the emergency department",
              keywords=["emergency department", "ed arrival", "presented to the ed"]),
            Q(linkId="AUTH.2", text="Payer notified within the notification window",
              keywords=["payer notified", "notification sent", "auth requested"]),
        ])]),
    "Q-DX-PROCEDURE": Questionnaire(
        id="Q-DX-PROCEDURE", title="Diagnosis supports procedure (book of arguments)", source="Book of arguments",
        item=[Q(linkId="BOA", text="Argument strength meets threshold", type="group", arithmetic=">=", points=5, item=[
            Q(linkId="BOA.1", text="Primary diagnosis documented by attending", points=3,
              keywords=["diagnosis", "assessment", "impression"]),
            Q(linkId="BOA.2", text="Imaging confirms the diagnosis", points=2,
              keywords=["ct ", "imaging", "x-ray", "ultrasound"]),
            Q(linkId="BOA.3", text="Procedure indication references the diagnosis", points=2,
              keywords=["indication", "due to", "secondary to"]),
        ])]),
}
