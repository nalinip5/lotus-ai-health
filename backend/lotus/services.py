"""Case repository, event bus (live feed + SSE) and notification service."""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from .domain.models import Case, Event, Notification
from .store.docstore import DocStore

NOTIFY_TO = {
    "denial": "appeals-team@lotusaihealth.example", "deadline": "rcm-ops@lotusaihealth.example",
    "ready": "billing@lotusaihealth.example", "rescan": "him@lotusaihealth.example",
    "approval": "appeals-team@lotusaihealth.example", "submission": "billing@lotusaihealth.example",
}
CATEGORY_LABEL = {"denial": "Denial notices", "deadline": "Deadline alerts", "ready": "Packet ready",
                  "rescan": "Rescan delivered", "approval": "Appeal approvals", "submission": "Submission updates"}


class CaseRepo:
    """Load/save cases. `mutate` serialises writers per case so parallel graph branches can't lose updates."""

    def __init__(self, store: DocStore):
        self.store = store
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, case_id: str) -> Optional[Case]:
        raw = self.store.get("cases", case_id)
        return Case.model_validate(raw) if raw else None

    def save(self, case: Case) -> None:
        self.store.put("cases", case.id, case.model_dump(mode="json"))

    def all(self) -> list[Case]:
        return [Case.model_validate(r) for r in self.store.find("cases", limit=10_000)]

    @asynccontextmanager
    async def mutate(self, case_id: str) -> AsyncIterator[Case]:
        async with self._locks[case_id]:
            case = self.get(case_id)
            if case is None:
                raise KeyError(case_id)
            yield case
            self.save(case)


class EventBus:
    def __init__(self, store: DocStore):
        self.store = store
        self._subs: set[asyncio.Queue] = set()

    def publish(self, type: str, title: str, detail: str = "", case_id: str | None = None,
                tone: str = "info") -> Event:
        ev = Event(id="ev-" + uuid.uuid4().hex[:12], type=type, title=title, detail=detail,
                   case_id=case_id, tone=tone)  # type: ignore[arg-type]
        self.store.put("events", ev.id, ev.model_dump())
        self.broadcast({"kind": "event", "event": ev.model_dump()})
        return ev

    def broadcast(self, msg: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:  # slow client: drop rather than block producers
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def recent(self, limit: int = 40) -> list[dict]:
        return self.store.find("events", limit=limit)


class Notifier:
    """Generates (synthetic) email notifications; nothing is sent."""

    def __init__(self, store: DocStore, bus: EventBus):
        self.store, self.bus = store, bus

    def settings(self) -> dict[str, bool]:
        saved = self.store.get("settings", "notifications") or {}
        return {k: saved.get(k, True) for k in CATEGORY_LABEL}

    def set_enabled(self, category: str, enabled: bool) -> dict[str, bool]:
        s = self.settings()
        s[category] = enabled
        self.store.put("settings", "notifications", s)
        return s

    def notify(self, category: str, subject: str, body: str, case_id: str | None = None,
               dedupe_key: str | None = None) -> Optional[Notification]:
        if not self.settings().get(category, True):
            return None
        nid = "nt-" + (dedupe_key or uuid.uuid4().hex[:12])
        n = Notification(id=nid, category=category, subject=subject, body=body,  # type: ignore[arg-type]
                         to=NOTIFY_TO[category], case_id=case_id)
        if not self.store.insert_if_absent("notifications", nid, n.model_dump()):
            return None  # idempotent: same notification already generated
        self.bus.broadcast({"kind": "notification", "notification": n.model_dump()})
        return n
