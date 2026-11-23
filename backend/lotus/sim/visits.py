"""Synthetic telehealth visits from the Lotus BPM Health patient app, with realistic billing gaps.

Gaps: missing consent, missing total time, missing patient location, over-coded E/M, missing modifier 95,
a billed diagnosis the note doesn't support. The simulated clinician later answers a "return" with an
addendum that documents what was actually done (closing the loop).
"""
from __future__ import annotations

import random
import string
from datetime import date, timedelta

from ..domain.policies import PAYERS
from ..domain.visits import EM_BY_MINUTES, EM_DESC, CodeLine, Visit

MEDICAL = [
    {"dx": ("J01.90", "Acute sinusitis, unspecified"), "wrong": ("J32.9", "Chronic sinusitis, unspecified"),
     "s": "5 days of facial pressure, purulent nasal discharge and low-grade fever.",
     "o": "Afebrile on home thermometer; patient demonstrates maxillary tenderness on self-palpation.",
     "a": "Acute sinusitis, likely viral with possible bacterial superinfection.",
     "p": "Saline irrigation, intranasal steroid; start amoxicillin-clavulanate if no improvement in 3 days."},
    {"dx": ("N39.0", "Urinary tract infection, site not specified"), "wrong": ("N30.00", "Acute cystitis without hematuria"),
     "s": "2 days of dysuria and urinary frequency, no flank pain or fever.",
     "o": "Well-appearing on video; home urine dipstick positive for leukocytes and nitrites.",
     "a": "Uncomplicated urinary tract infection.",
     "p": "Nitrofurantoin 100 mg twice daily for 5 days; return precautions reviewed."},
    {"dx": ("I10", "Essential (primary) hypertension"), "wrong": ("I11.9", "Hypertensive heart disease without heart failure"),
     "s": "Follow-up of blood pressure; home readings 138-146/88-92 over two weeks.",
     "o": "Home cuff reading during visit 142/90; no edema reported.",
     "a": "Essential hypertension, not at goal.",
     "p": "Increase lisinopril to 20 mg daily; recheck home readings in 2 weeks; BMP ordered."},
    {"dx": ("F41.1", "Generalized anxiety disorder"), "wrong": ("F32.9", "Major depressive disorder, single episode"),
     "s": "Ongoing worry, poor sleep and restlessness for 6 months; GAD-7 score 14.",
     "o": "Alert, appropriate, mildly anxious affect on video.",
     "a": "Generalized anxiety disorder, moderate.",
     "p": "Start sertraline 25 mg daily; referral to licensed therapist; follow-up in 4 weeks."},
    {"dx": ("L25.9", "Unspecified contact dermatitis, unspecified cause"), "wrong": ("L30.9", "Dermatitis, unspecified"),
     "s": "Itchy rash on both forearms after gardening 3 days ago.",
     "o": "Photos and live video show linear erythematous papules and vesicles on forearms.",
     "a": "Contact dermatitis, likely plant exposure.",
     "p": "Triamcinolone 0.1% cream twice daily; oral antihistamine at night."},
]
DENTAL = [
    {"dx": ("K04.0", "Pulpitis"), "wrong": ("K08.89", "Other disorders of teeth"),
     "s": "Throbbing pain in lower right molar for 3 days, worse with cold and at night.",
     "o": "Intraoral photos show deep occlusal lesion on tooth 30; mild gingival swelling.",
     "a": "Pulpitis of tooth 30, likely irreversible.",
     "p": "Ibuprofen and acetaminophen alternating; in-person endodontic evaluation within 48 hours."},
    {"dx": ("K02.9", "Dental caries, unspecified"), "wrong": ("K03.81", "Cracked tooth"),
     "s": "Sensitivity to sweets on upper left teeth for 2 weeks.",
     "o": "Photos show dark interproximal lesions on teeth 13 and 14.",
     "a": "Dental caries on teeth 13 and 14.",
     "p": "Fluoride toothpaste, dietary counseling; schedule restorative visit."},
]
CLINICIANS = {"medical": ["Dr. Maya Chen, MD", "Dr. Omar Haddad, DO", "Dr. Priya Raman, MD"],
              "dental": ["Dr. Lena Ortiz, DDS", "Dr. Sam Okafor, DMD"]}


def _tok(rng: random.Random, n: int = 5) -> str:
    return "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(n))


class VisitGenerator:
    def __init__(self, seed: int = 11, start: int = 5001, today: date | None = None):
        self.rng = random.Random(seed)
        self.next_id = start
        self.today = today or date.today()

    def visit(self, gaps: list[str] | None = None, specialty: str | None = None, payer: str | None = None) -> Visit:
        rng = self.rng
        specialty = specialty or ("dental" if rng.random() < 0.25 else "medical")
        sc = rng.choice(DENTAL if specialty == "dental" else MEDICAL)
        payer = payer or rng.choice(list(PAYERS))
        if gaps is None:
            pool = ["consent", "time", "location", "overcode", "no_mod95", "wrong_dx"]
            gaps = [] if rng.random() < 0.3 else rng.sample(pool, k=rng.choice([1, 1, 2]))
        vid = f"VIS-{self.next_id}"
        self.next_id += 1
        vdate = (self.today - timedelta(days=rng.randint(0, 3))).isoformat()
        pt, mrn = f"PT-{_tok(rng)}", f"MRN-{rng.randint(10000, 99999)}"
        clinician = rng.choice(CLINICIANS[specialty])
        minutes = rng.choice([14, 18, 22, 24, 27, 32, 35, 41])
        lines = ["TELEHEALTH VISIT NOTE", f"PATIENT TOKEN: {pt}", f"MRN: {mrn}", f"VISIT DATE: {vdate}",
                 f"CLINICIAN: {clinician}", "MODALITY: Synchronous audio-video visit via Lotus BPM Health app."]
        if "consent" not in gaps:
            lines.append("CONSENT: Patient gave verbal consent to receive care via telehealth video visit.")
        if "location" not in gaps:
            lines.append(f"LOCATION: Patient located at home in {rng.choice(['TX', 'IL', 'FL', 'CA', 'NY'])}; "
                         "clinician at Lotus BPM Health virtual clinic.")
        lines += [f"SUBJECTIVE: {sc['s']}", f"OBJECTIVE: {sc['o']}", f"ASSESSMENT: {sc['a']}", f"PLAN: {sc['p']}"]
        if "time" not in gaps:
            lines.append(f"TIME: Total time spent on the date of the encounter was {minutes} minutes.")
        note = "\n".join(lines)

        # What the clinician selected in the app.
        dx = sc["wrong"] if "wrong_dx" in gaps else sc["dx"]
        codes = [CodeLine(kind="ICD-10", code=dx[0], description=dx[1])]
        if specialty == "dental":
            codes += [CodeLine(kind="CDT", code="D0140", description="Limited oral evaluation, problem focused"),
                      CodeLine(kind="CDT", code="D9995", description="Teledentistry, synchronous; real-time encounter")]
        else:
            supported = next((c for t, c, _ in EM_BY_MINUTES if minutes >= t), "99212")
            em = supported
            if "overcode" in gaps:
                em = {"99212": "99214", "99213": "99215", "99214": "99215", "99215": "99215"}[supported]
            mods = [] if "no_mod95" in gaps else ["95"]
            codes.append(CodeLine(kind="CPT", code=em, description=EM_DESC[em], modifiers=mods))
        return Visit(id=vid, patient_token=pt, mrn=mrn, payer=payer, clinician=clinician, specialty=specialty,
                     modality="video", visit_date=vdate, note=note, submitted_codes=codes)


ADDENDA = {
    "telehealth_consent": "Verbal consent to receive care by telehealth video was obtained at the start of the visit.",
    "patient_location": "Patient located at home during the visit.",
    "em_time_missing": "Total time spent on the date of the encounter was 25 minutes, including chart review "
                       "and documentation.",
}
