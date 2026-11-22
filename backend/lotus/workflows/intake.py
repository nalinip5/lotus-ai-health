"""Packet intake workflow (the design's "Appeal pre-process" + "Structuring" workflows).

    ingest ─▶ structure ─┬─▶ integrity ───┐
                         └─▶ intent_admit ─┴─▶ summary ─▶ finalize

Fixed graph, no LLM routing. Every node is idempotent through the stage
ledger; nodes apply ledger output to the case with plain assignments so a
resumed run converges to the same record. thread_id = "intake:<case id>".
"""
from __future__ import annotations

import asyncio
import operator
from statistics import mean
from typing import TYPE_CHECKING, Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from ..agents import clinical, integrity
from ..domain.models import Case, CaseStatus, Finding
from ..observability import new_correlation_id, tracer
from .common import case_context, fingerprint

if TYPE_CHECKING:
    from ..container import Container


class IntakeState(TypedDict):
    case_id: str
    correlation_id: str
    stages: Annotated[list[str], operator.add]


def _doc_fp(case: Case) -> str:
    return fingerprint([(d.id, d.sha256, [(p.number, p.sha256, p.ocr_confidence, p.unreadable) for p in d.pages])
                        for d in case.documents])


def merge_findings(case: Case, fresh: list[Finding]) -> None:
    """Keep human decisions (accepted / rescan) on findings that still exist."""
    old = {f.id: f for f in case.findings}
    merged = []
    for f in fresh:
        if f.id in old:
            f.status, f.rationale = old[f.id].status, old[f.id].rationale
        merged.append(f)
    for f in old.values():  # findings that disappeared (e.g. page rescanned) become resolved
        if f.id not in {x.id for x in fresh} and f.status != "resolved":
            f.status = "resolved"
            merged.append(f)
    case.findings = merged


def build_intake_graph(c: "Container") -> StateGraph:
    async def ingest(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case

            async def work():
                async with tracer.span("tool.blob.put_packet", kind="tool"):
                    sha = c.blobs.put_json(cid, "packet", "manifest.json",
                                           [d.model_dump(mode="json") for d in case.documents])
                return {"pages": sum(len(d.pages) for d in case.documents), "manifest_sha": sha}

            out, cached = await c.ledger.run_once(cid, "ingest", _doc_fp(case), work)
            if not cached:
                async with c.repo.mutate(cid) as cs:
                    cs.correlation_id = state["correlation_id"]
                    cs.log("INTAKE", f"Packet ingested; type checks passed; manifest sha256 {out['manifest_sha'][:12]}…")
                c.bus.publish("packet.ingested", "Packet ingested",
                              f"{cid} · {case.payer} · {out['pages']} pages", cid)
                c.bus.publish("packet.segmented", f"Page segmentation complete — {out['pages']} pages, manifest hashed",
                              cid, cid)
        return {"stages": ["ingest"]}

    async def structure(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case

            async def work():
                results = await asyncio.gather(*(clinical.structure_document(c.llm, d) for d in case.documents))
                out = {d.id: {"classified_type": r.doc_type, "sections": r.sections}
                       for d, r in zip(case.documents, results)}
                c.blobs.put_json(cid, "structured", "structured_output.json", out)
                return out

            out, cached = await c.ledger.run_once(cid, "structure", _doc_fp(case), work)
            async with c.repo.mutate(cid) as cs:
                for d in cs.documents:
                    if d.id in out:
                        d.classified_type = out[d.id]["classified_type"]
                        d.sections = out[d.id]["sections"]
                conf = mean(p.ocr_confidence for d in cs.documents for p in d.pages)
                if not cached:
                    cs.log("STRUCTURING", f"{len(out)} documents classified and sectioned; mean OCR {conf:.2f}")
            if not cached:
                c.bus.publish("ocr.complete", f"OCR complete — mean confidence {conf:.2f}", cid, cid)
        return {"stages": ["structure"]}

    async def integrity_checks(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            async with tracer.span("rule.integrity", kind="rule"):
                async with c.repo.mutate(cid) as cs:
                    before = (cs.status, sorted(b.id for b in cs.open_blockers))
                    merge_findings(cs, integrity.quality_findings(cs))
                    integrity.evaluate_readiness(cs)
                    after = (cs.status, sorted(b.id for b in cs.open_blockers))
                    if before != after:
                        cs.log("READINESS", f"{cs.status.value}: {len(cs.open_blockers)} open blocker(s)")
        return {"stages": ["integrity"]}

    async def intent_admit(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case

            async def work():
                return (await clinical.extract_admit(c.llm, case)).model_dump()

            out, _ = await c.ledger.run_once(cid, "intent_admit", _doc_fp(case), work)
            async with c.repo.mutate(cid) as cs:
                cs.admit_datetime = out.get("admit_datetime")
        return {"stages": ["intent_admit"]}

    async def summary(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case

            async def work():
                s = await clinical.summarize_case(c.llm, case)
                c.blobs.put_json(cid, "summary", "summary.json", s.model_dump())
                return s.model_dump()

            out, _ = await c.ledger.run_once(cid, "summary", _doc_fp(case) + str(case.admit_datetime), work)
            async with c.repo.mutate(cid) as cs:
                cs.summary = out
        return {"stages": ["summary"]}

    async def finalize(state: IntakeState) -> dict:
        cid = state["case_id"]
        with case_context(cid, state["correlation_id"]):
            case = c.repo.get(cid)
            assert case
            announce_status(c, case)
        return {"stages": ["finalize"]}

    g = StateGraph(IntakeState)
    for name, fn in [("ingest", ingest), ("structure", structure), ("integrity", integrity_checks),
                     ("intent_admit", intent_admit), ("summary", summary), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "structure")
    g.add_edge("structure", "integrity")
    g.add_edge("structure", "intent_admit")
    g.add_edge(["integrity", "intent_admit"], "summary")
    g.add_edge("summary", "finalize")
    g.add_edge("finalize", END)
    return g


def announce_status(c: "Container", case: Case) -> None:
    """Publish the integrity outcome once per (case, status, blocker set). Safe to call repeatedly."""
    key = fingerprint(case.id, case.status.value, sorted(b.id for b in case.open_blockers))
    if not c.store.insert_if_absent("announcements", key, {"case_id": case.id}):
        return
    if case.status == CaseStatus.READY:
        c.bus.publish("packet.ready", "Packet ready", f"{case.id} · all deterministic checks passed", case.id, "ok")
        c.notifier.notify("ready", f"Packet ready to submit — {case.id}",
                          f"{case.id} ({case.payer}, ${case.billed:,.2f}) passed every deterministic check "
                          f"and is ready to submit.", case.id, dedupe_key=f"ready-{case.id}")
    elif case.open_blockers:
        kinds = []
        for b in case.open_blockers:
            label = {"completeness": "missing documents", "identity": "identity conflict"}.get(b.kind)
            if b.kind == "quality":
                label = "critical scan defect" if "Critical" in b.message else "open quality finding"
            if label not in kinds:
                kinds.append(label)
        c.bus.publish("exceptions.raised", "Exceptions raised", f"{case.id} · {', '.join(kinds)}", case.id,
                      "bad" if case.status == CaseStatus.QUARANTINED else "warn")


async def run_intake(c: "Container", case_id: str) -> Case:
    corr = new_correlation_id()
    await c.intake.ainvoke({"case_id": case_id, "correlation_id": corr, "stages": []},
                           {"configurable": {"thread_id": f"intake:{case_id}"}})
    case = c.repo.get(case_id)
    assert case
    return case
