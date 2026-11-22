"""Synthetic claim packets, denial notices and clinical text. No real PHI.

Each packet is produced from a clinical scenario plus a random set of defects
(missing document, low-OCR or skewed page, unreadable page, identity conflict)
so every branch of the integrity workflow is exercised.
"""
from __future__ import annotations

import hashlib
import random
import string
from dataclasses import dataclass
from datetime import date, timedelta

from ..domain.models import Case, Document, Page
from ..domain.policies import DENIAL_CODES, PAYERS

SCENARIOS = [
    {
        "name": "sepsis",
        "dx": [("Sepsis due to community-acquired pneumonia", "A41.9"),
               ("Community-acquired pneumonia, right lower lobe", "J18.9"),
               ("Acute kidney injury", "N17.9"), ("Type 2 diabetes mellitus", "E11.9")],
        "admit": "ED",
        "ed": ("Patient presented to the ED with fever 39.2 C, cough and confusion. "
               "Blood pressure 84/52 on arrival, MAP 62. Lactate 3.4 mmol/L. SpO2 88% on room air."),
        "hp": ("HPI: 67 year old with 3 days of productive cough and rigors. "
               "ASSESSMENT: Sepsis due to community-acquired pneumonia with hypotension and hypoxemia. "
               "PLAN: Admit to inpatient telemetry. Start IV antibiotics piperacillin-tazobactam and vancomycin. "
               "Repeat lactate in 6 hours and serial labs every 12 hours."),
        "course": ("Received 30 mL/kg crystalline fluid bolus. Continuous cardiac monitoring on telemetry for 48 hours. "
                   "Repeat lactate 1.6 mmol/L at 18 hours. Transitioned to oral antibiotics on day 3."),
        "imaging": "CT chest: right lower lobe consolidation. IMPRESSION: Findings consistent with pneumonia.",
        "labs": "WBC 18.4 K/uL. Lactate 3.4 mmol/L, repeat lactate 1.6 mmol/L. Creatinine 2.1 mg/dL.",
        "procedure": None,
    },
    {
        "name": "appendectomy",
        "dx": [("Acute appendicitis with localized peritonitis", "K35.30"), ("Leukocytosis", "D72.829")],
        "admit": "ED",
        "ed": "Patient presented to the ED with right lower quadrant pain for 18 hours, nausea and fever 38.4 C.",
        "hp": ("HPI: 34 year old with migratory periumbilical pain to RLQ. "
               "ASSESSMENT: Acute appendicitis. PLAN: Admit to surgery, IV ceftriaxone and metronidazole, "
               "laparoscopic appendectomy."),
        "course": "Underwent laparoscopic appendectomy without complication. Tolerated diet on post-op day 1.",
        "imaging": "CT abdomen: dilated appendix 11 mm with periappendiceal fat stranding. IMPRESSION: Acute appendicitis.",
        "labs": "WBC 15.2 K/uL. CRP 48 mg/L.",
        "procedure": ("PROCEDURE: Laparoscopic appendectomy. INDICATION: Acute appendicitis confirmed on CT. "
                      "FINDINGS: Inflamed, non-perforated appendix. Procedure performed without complication. "
                      "Electronically signed by attending surgeon."),
    },
    {
        "name": "hip_fracture",
        "dx": [("Closed intertrochanteric fracture of left femur", "S72.142A"), ("Fall from standing", "W19"),
               ("Osteoporosis", "M81.0")],
        "admit": "ED",
        "ed": "Patient presented to the ED after a fall at home with left hip pain and inability to bear weight.",
        "hp": ("HPI: 82 year old after mechanical fall. ASSESSMENT: Left intertrochanteric femur fracture. "
               "PLAN: Admit to orthopedics, IV analgesia, ORIF within 24 hours, telemetry for age and anticoagulation."),
        "course": "ORIF performed on hospital day 2. Physical therapy initiated. Serial hemoglobin every 12 hours stable.",
        "imaging": "X-ray left hip: displaced intertrochanteric fracture. IMPRESSION: Acute left hip fracture.",
        "labs": "Hemoglobin 11.2 g/dL, repeat 10.6 g/dL. INR 1.1.",
        "procedure": ("PROCEDURE: Open reduction internal fixation, left intertrochanteric fracture. "
                      "INDICATION: Displaced fracture due to fall. Procedure performed with cephalomedullary nail. "
                      "Electronically signed by attending surgeon."),
    },
    {
        "name": "chest_pain_obs",
        "dx": [("Chest pain, unspecified", "R07.9"), ("Essential hypertension", "I10")],
        "admit": "Direct",
        "ed": "Patient presented to the ED with intermittent chest pressure, resolved on arrival. Vital signs stable.",
        "hp": ("HPI: 55 year old with atypical chest pressure. ASSESSMENT: Chest pain, low risk. "
               "PLAN: Place in observation only for serial troponins and stress test."),
        "course": "Serial troponins negative. Stress test negative. Discharged next morning.",
        "imaging": "X-ray chest: no acute cardiopulmonary process. IMPRESSION: Normal chest radiograph.",
        "labs": "Troponin < 0.01 ng/mL x3.",
        "procedure": None,
    },
]


def _tok(rng: random.Random, n: int = 5) -> str:
    return "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(n))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class PacketSpec:
    case: Case
    scenario: dict
    defects: list[str]


class Generator:
    def __init__(self, seed: int = 7, start_case: int = 1001, demo_today: date | None = None):
        self.rng = random.Random(seed)
        self.next_id = start_case
        self.today = demo_today or date(2026, 11, 11)

    # ---------------------------------------------------------------- packet
    def packet(self, defects: list[str] | None = None, scenario: str | None = None,
               service: date | None = None) -> PacketSpec:
        rng = self.rng
        sc = next(s for s in SCENARIOS if s["name"] == scenario) if scenario else rng.choice(SCENARIOS)
        payer = rng.choice(list(PAYERS))
        if defects is None:
            roll = rng.random()
            defects = ([] if roll < 0.35 else
                       [rng.choice(["missing_doc", "low_ocr", "skew", "unreadable", "identity"])]
                       + (["missing_doc"] if rng.random() < 0.2 else []))
        cid = f"CASE-{self.next_id}"
        self.next_id += 1
        service = service or self.today - timedelta(days=rng.randint(3, 14))
        pt, mrn, enc = f"PT-{_tok(rng)}", f"MRN-{rng.randint(10000, 99999)}", f"ENC-{_tok(rng, 4)}"
        case = Case(id=cid, patient_token=pt, mrn=mrn, encounter=enc, payer=payer,
                    policy_id=PAYERS[payer]["policy"], service_date=service.isoformat(),
                    billed=round(rng.uniform(900, 9200), 2), scenario=sc["name"])

        required = list(PAYERS[payer]["required"])
        doc_types = list(dict.fromkeys(required + ["Discharge summary", "History & physical", "Lab results",
                                                   "ED documentation", "Imaging report"]))
        if sc["procedure"]:
            doc_types.append("Operative note")
        doc_types = list(dict.fromkeys(doc_types))
        if "missing_doc" in defects:
            droppable = [d for d in required if d != "Cover sheet"]
            for d in rng.sample(droppable, k=min(len(droppable), rng.choice([1, 2]))):
                doc_types.remove(d)

        admit_dt = f"{service.isoformat()} {rng.randint(8, 22):02d}:{rng.choice(['05', '20', '40', '55'])}"
        discharge = (service + timedelta(days=rng.randint(1, 4))).isoformat()
        docs: list[Document] = []
        for i, dtype in enumerate(doc_types, start=1):
            pages = self._pages(dtype, sc, case, admit_dt, discharge, doc_types)
            docs.append(Document(id=f"d{i}", declared_type=dtype, patient_token=pt, mrn=mrn, pages=pages,
                                 sha256=_sha("".join(p.text for p in pages))))

        clinical = [d for d in docs if d.declared_type != "Cover sheet"]
        if "identity" in defects and clinical:
            victim = rng.choice(clinical)
            other_pt, other_mrn = f"PT-{_tok(rng)}", f"MRN-{rng.randint(10000, 99999)}"
            victim.patient_token, victim.mrn = other_pt, other_mrn
            victim.pages[0].text = victim.pages[0].text.replace(pt, other_pt).replace(mrn, other_mrn)
        if any(d in defects for d in ("low_ocr", "skew", "unreadable")) and clinical:
            page = rng.choice(rng.choice(clinical).pages)
            if "low_ocr" in defects:
                page.ocr_confidence = round(rng.uniform(0.55, 0.74), 2)
            if "skew" in defects:
                page.skew_deg = round(rng.uniform(6, 11), 1)
                page.ocr_confidence = round(rng.uniform(0.6, 0.79), 2)
            if "unreadable" in defects:
                page.unreadable = True
                page.ocr_confidence = round(rng.uniform(0.12, 0.35), 2)
        for d in docs:
            for p in d.pages:
                if p.ocr_confidence == 0.95:
                    p.ocr_confidence = round(rng.uniform(0.84, 0.99), 2)
                p.sha256 = _sha(p.text)
        case.documents = docs
        return PacketSpec(case=case, scenario=sc, defects=defects)

    def document(self, case: Case, dtype: str, doc_id: str) -> Document:
        """A late-arriving document (HIM delivery) for an existing case."""
        sc = next(s for s in SCENARIOS if s["name"] == case.scenario)
        sd = date.fromisoformat(case.service_date)
        pages = self._pages(dtype, sc, case, f"{case.service_date} 10:20", (sd + timedelta(days=2)).isoformat(), [])
        for p in pages:
            p.ocr_confidence = round(self.rng.uniform(0.86, 0.98), 2)
            p.sha256 = _sha(p.text)
        return Document(id=doc_id, declared_type=dtype, patient_token=case.patient_token, mrn=case.mrn,
                        pages=pages, sha256=_sha("".join(p.text for p in pages)))

    def _pages(self, dtype: str, sc: dict, case: Case, admit_dt: str, discharge: str,
               enclosed: list[str]) -> list[Page]:
        head = (f"PATIENT TOKEN: {case.patient_token}\nMRN: {case.mrn}\nENCOUNTER: {case.encounter}\n")
        dx_lines = "\n".join(f"DIAGNOSIS: {d} ({c})." for d, c in sc["dx"][:2])
        texts: list[str]
        if dtype == "Cover sheet":
            texts = [f"CLAIM PACKET COVER SHEET\n{head}PAYER: {case.payer}\nSERVICE DATE: {case.service_date}\n"
                     f"DOCUMENTS ENCLOSED: {', '.join(enclosed)}"]
        elif dtype == "Discharge summary":
            admitted = (f"ADMISSION: Patient was admitted to inpatient status on {admit_dt} via the emergency department."
                        if sc["admit"] == "ED" else
                        f"ADMISSION: Patient was placed in observation on {admit_dt}.")
            texts = [f"DISCHARGE SUMMARY\n{head}{admitted}\n{dx_lines}",
                     f"HOSPITAL COURSE: {sc['course']}",
                     f"DISPOSITION: Discharged home in stable condition on {discharge}. Follow up with primary care in 7 days."]
        elif dtype == "History & physical":
            texts = [f"HISTORY AND PHYSICAL\n{head}{sc['hp'].replace('ASSESSMENT', chr(10) + 'ASSESSMENT').replace('PLAN', chr(10) + 'PLAN')}"]
        elif dtype == "ED documentation":
            texts = [f"EMERGENCY DEPARTMENT NOTE\n{head}ED ARRIVAL: {case.service_date}.\nPRESENTATION: {sc['ed']}"]
        elif dtype == "Imaging report":
            texts = [f"RADIOLOGY REPORT\n{head}FINDINGS: {sc['imaging']}"]
        elif dtype == "Lab results":
            texts = [f"LABORATORY RESULTS\n{head}RESULTS: {sc['labs']}"]
        elif dtype == "Operative note":
            texts = [f"OPERATIVE NOTE\n{head}{sc['procedure'] or 'PROCEDURE: None performed.'}"]
        elif dtype == "Clinical note":
            texts = [f"PROGRESS NOTE\n{head}ASSESSMENT: {sc['dx'][0][0]}.\nPLAN: {sc['course']}"]
        else:
            texts = [f"{dtype.upper()}\n{head}No additional content."]
        return [Page(number=i, text=t) for i, t in enumerate(texts, start=1)]

    # ---------------------------------------------------------------- denial
    def denial_notice(self, case: Case, code: str | None = None, notice: date | None = None) -> str:
        code = code or self.rng.choice(list(DENIAL_CODES))
        notice = notice or self.today - timedelta(days=self.rng.randint(0, 3))
        days = PAYERS[case.payer]["appeal_days"]
        reason = DENIAL_CODES[code]["reason"]
        return (f"{case.payer} — EXPLANATION OF PAYMENT / REMITTANCE ADVICE (SYNTHETIC)\n\n"
                f"Date: {notice.isoformat()}\nClaim: {case.id} · Encounter {case.encounter}\n"
                f"Patient token: {case.patient_token} · Service date: {case.service_date}\n"
                f"Billed amount: ${case.billed:,.2f}\n\nDETERMINATION: DENIED\n\n"
                f"Reason code {code}: {reason}\n\n"
                f"Appeal rights: You may submit a written appeal within {days} days of the notice date. "
                f"Include the documentation listed in the payer policy with your appeal. "
                f"Deadlines are provisional until verified against the payer's current terms.")
