"""Dependency container: one instance per running app (or per test)."""
from __future__ import annotations

from typing import Any

from .config import Settings, settings as default_settings
from .llm.base import LLM
from .llm.providers import build_llm
from .observability import tracer
from .services import CaseRepo, EventBus, Notifier
from .store.blob import BlobStore
from .store.docstore import DocStore
from .workflows.common import StageLedger


class Container:
    def __init__(self, cfg: Settings | None = None, llm: LLM | None = None, db_path: str | None = None):
        self.settings = cfg or default_settings
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = DocStore(db_path or self.settings.db_path)
        self.blobs = BlobStore(self.settings.blob_dir)
        self.llm = llm or build_llm()
        self.repo = CaseRepo(self.store)
        self.bus = EventBus(self.store)
        self.notifier = Notifier(self.store, self.bus)
        self.ledger = StageLedger(self.store)
        tracer.store = self.store
        self.checkpointer: Any = None
        self.intake: Any = None
        self.appeal: Any = None

    def attach_checkpointer(self, checkpointer: Any) -> None:
        from .workflows.appeal import build_appeal_graph
        from .workflows.intake import build_intake_graph

        self.checkpointer = checkpointer
        self.intake = build_intake_graph(self).compile(checkpointer=checkpointer)
        self.appeal = build_appeal_graph(self).compile(checkpointer=checkpointer)
