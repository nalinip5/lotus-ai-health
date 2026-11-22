"""Shared workflow plumbing: idempotent stage ledger and context propagation.

Every node writes its result to the `stage_runs` ledger keyed by
(case, stage, input fingerprint). When LangGraph resumes a thread after a
crash, or an operator retries a step, a node whose key already exists returns
the stored output instead of calling tools/LLMs again or writing duplicates.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Iterator

from ..observability import correlation_id, current_case
from ..store.docstore import DocStore


def fingerprint(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


class StageLedger:
    def __init__(self, store: DocStore):
        self.store = store

    async def run_once(self, case_id: str, stage: str, fp: str,
                       fn: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        key = f"{case_id}:{stage}:{fp}"
        hit = self.store.get("stage_runs", key)
        if hit is not None:
            return hit["output"], True
        output = await fn()
        self.store.put("stage_runs", key, {"case_id": case_id, "stage": stage, "fp": fp, "output": output,
                                           "correlation_id": correlation_id.get()})
        return output, False

    def runs(self, case_id: str) -> list[dict]:
        return self.store.find("stage_runs", {"case_id": case_id}, newest_first=False)


@contextmanager
def case_context(case_id: str, corr: str | None) -> Iterator[None]:
    t1 = current_case.set(case_id)
    t2 = correlation_id.set(corr)
    try:
        yield
    finally:
        current_case.reset(t1)
        correlation_id.reset(t2)
