"""Case operations: human actions (HITL) and payer lifecycle steps.

Called by the REST API (people) and by the simulator (synthetic payers / HIM).
Each operation is a small, explicit state transition with an audit entry.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

from .agents import appeal as appeal_agents
from .agents import integrity
from .domain.models import CaseStatus, Submission, utcnow
from .observability import new_correlation_id
from .workflows.appeal import start_appeal
from .workflows.common import case_context
from .workflows.intake import announce_status, run_intake

if TYPE_CHECKING:
    from .container import Container
    from .sim.generator import Generator


class OpError(ValueError):
    """Business-rule refusal; surfaced to the UI as HTTP 409."""


def demo_today() -> date:
    return date.today()


async def _reassess(c: "Container", case_id: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        integrity.evaluate_readiness(cs)
        case = cs
    announce_status(c, case)


# ---------------------------------------------------------------- HITL actions
async def mark_received(c: "Container", gen: "Generator", case_id: str, doc_type: str, actor: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        if any((d.classified_type or d.declared_type) == doc_type for d in cs.documents):
            raise OpError(f"{doc_type} is already in the packet")
        cs.documents.append(gen.document(cs, doc_type, f"d{len(cs.documents) + 1}"))
        cs.log("HIM_DELIVERY", f"{doc_type} received from HIM", actor=actor)
    c.bus.publish("him.delivered", "HIM delivery received", f"{case_id} · {doc_type}", case_id)
    c.notifier.notify("rescan", f"Document delivered — {case_id}", f"HIM delivered {doc_type} for {case_id}.",
                      case_id)
    await run_intake(c, case_id)


async def request_rescan(c: "Container", case_id: str, finding_id: str, actor: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        f = next((x for x in cs.findings if x.id == finding_id), None)
        if not f:
            raise OpError("finding not found")
        f.status = "rescan_requested"
        cs.log("RESCAN_REQUESTED", f"{f.doc_id} p.{f.page}: {f.kind}", actor=actor)
        integrity.evaluate_readiness(cs)
    c.bus.publish("rescan.requested", "Rescan requested", f"{case_id} · {finding_id}", case_id, "warn")


async def deliver_rescan(c: "Container", case_id: str) -> bool:
    async with c.repo.mutate(case_id) as cs:
        pending = [f for f in cs.findings if f.status == "rescan_requested"]
        if not pending:
            return False
        for f in pending:
            doc = cs.doc(f.doc_id)
            page = next(p for p in doc.pages if p.number == f.page)
            page.unreadable, page.skew_deg, page.ocr_confidence = False, 0.4, 0.95
            page.sha256 = uuid.uuid4().hex  # new scan, new checksum
            f.status = "resolved"
        cs.log("RESCAN_DELIVERED", f"{len(pending)} page(s) rescanned by HIM")
    c.bus.publish("rescan.delivered", "Rescan delivered", f"{case_id} · {len(pending)} page(s)", case_id, "ok")
    c.notifier.notify("rescan", f"Rescan delivered — {case_id}", f"HIM rescanned {len(pending)} page(s).", case_id)
    await run_intake(c, case_id)
    return True


async def accept_finding(c: "Container", case_id: str, finding_id: str, rationale: str, actor: str) -> None:
    if not rationale.strip():
        raise OpError("A rationale is required to accept a finding")
    async with c.repo.mutate(case_id) as cs:
        f = next((x for x in cs.findings if x.id == finding_id), None)
        if not f:
            raise OpError("finding not found")
        if f.severity == "critical":
            raise OpError("Critical scan defects cannot be accepted — request a rescan")
        f.status, f.rationale = "accepted", rationale
        cs.log("FINDING_ACCEPTED", f"{finding_id}: {rationale}", actor=actor)
    await _reassess(c, case_id)


async def reconcile_identity(c: "Container", case_id: str, choice: str, rationale: str, actor: str) -> None:
    if choice not in ("cover_authoritative", "different_patient"):
        raise OpError("An identity conflict requires an explicit reconciliation choice")
    if not rationale.strip():
        raise OpError("A rationale is required")
    async with c.repo.mutate(case_id) as cs:
        ids = [b for b in cs.open_blockers if b.kind == "identity"]
        if not ids:
            raise OpError("No identity conflict to reconcile")
        if choice == "different_patient":
            cs.status = CaseStatus.RETURNED
            cs.log("RETURNED", f"Packet returned — different patient. {rationale}", actor=actor)
        else:
            for b in ids:
                b.resolved = True
            cs.log("IDENTITY_RECONCILED", f"Cover sheet authoritative. {rationale}", actor=actor)
    if choice == "different_patient":
        c.bus.publish("packet.returned", "Packet returned", f"{case_id} · different patient", case_id, "bad")
    else:
        await _reassess(c, case_id)


async def verify_deadline(c: "Container", case_id: str, actor: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        if not cs.denial:
            raise OpError("No denial on file")
        cs.denial.deadline_verified, cs.denial.verified_by = True, actor
        cs.log("DEADLINE_VERIFIED", f"{cs.denial.deadline} verified against payer terms", actor=actor)


# ---------------------------------------------------------------- payer lifecycle
async def submit(c: "Container", case_id: str, actor: str = "system") -> None:
    async with c.repo.mutate(case_id) as cs:
        if cs.status != CaseStatus.READY:
            raise OpError("Only Ready packets can be submitted (readiness gate)")
        cs.submission = Submission(receipt="TR-" + uuid.uuid4().hex[:8], sent_at=utcnow())
        cs.status = CaseStatus.SENT
        cs.log("SUBMITTED", f"Transport receipt {cs.submission.receipt}", actor=actor)
        receipt = cs.submission.receipt
    c.bus.publish("packet.submitted", "Packet submitted",
                  f"{case_id} · transport receipt {receipt} — acknowledgment pending", case_id)


async def payer_ack(c: "Container", case_id: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        if cs.status != CaseStatus.SENT or not cs.submission:
            return
        cs.submission.ack, cs.submission.ack_ref = "confirmed", f"#{uuid.uuid4().int % 90000 + 10000}"
        cs.status = CaseStatus.ACKNOWLEDGED
        cs.log("PAYER_ACK", f"Payer 999 acknowledgment {cs.submission.ack_ref}")
    c.bus.publish("payer.ack", "Payer acknowledged", f"{case_id} · receipt confirmed", case_id, "ok")
    c.notifier.notify("submission", f"Payer acknowledgment received — {case_id}",
                      f"The payer confirmed receipt of {case_id}.", case_id, dedupe_key=f"ack-{case_id}")


async def receive_denial(c: "Container", gen: "Generator", case_id: str, code: str | None = None,
                         notice: date | None = None) -> None:
    case = c.repo.get(case_id)
    assert case
    if case.denial:
        raise OpError("Denial already on file")
    text = gen.denial_notice(case, code, notice)
    with case_context(case_id, new_correlation_id()):
        denial = await appeal_agents.ingest_denial(c.llm, case, text)
    async with c.repo.mutate(case_id) as cs:
        cs.denial = denial
        cs.status = CaseStatus.DENIED
        cs.log("DENIAL", f"{denial.code}: {denial.reason} Deadline {denial.deadline} (provisional)")
    c.blobs.put_bytes(case_id, "letters", "denial_notice.txt", text.encode())
    days = (date.fromisoformat(denial.deadline) - demo_today()).days
    c.bus.publish("denial.received", f"Denial {denial.code} received",
                  f"{case_id} · {denial.category.replace('_', ' ')} · {days}d to deadline", case_id, "bad")
    c.notifier.notify("denial", f"Denial {denial.code} received — {case_id}",
                      f"{case.payer} denied {case_id} ({denial.reason}). Appeal deadline {denial.deadline} "
                      f"(provisional, {days} days).", case_id, dedupe_key=f"denial-{case_id}")
    await start_appeal(c, case_id)


async def archive(c: "Container", case_id: str) -> None:
    async with c.repo.mutate(case_id) as cs:
        if cs.status not in (CaseStatus.ACKNOWLEDGED, CaseStatus.APPEAL_SENT):
            return
        cs.status = CaseStatus.ARCHIVED
        cs.log("ARCHIVED", "Completed and archived")
    c.bus.publish("case.archived", "Case archived", f"{case_id} · completed and archived", case_id)


def deadline_sweep(c: "Container", warn_days: int = 14) -> None:
    for case in c.repo.all():
        if case.denial and case.appeal.status not in ("sent", "written_off"):
            days = (date.fromisoformat(case.denial.deadline) - demo_today()).days
            if days <= warn_days:
                c.notifier.notify("deadline", f"Appeal deadline approaching — {case.id} ({days} day(s) left)",
                                  f"Deadline {case.denial.deadline} "
                                  f"({'verified' if case.denial.deadline_verified else 'provisional'}).",
                                  case.id, dedupe_key=f"deadline-{case.id}-{days <= 5}")
