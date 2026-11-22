"""Runtime settings, read from environment variables (prefix LOTUS_)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"LOTUS_{name}", default)


@dataclass
class Settings:
    # Storage. The document store mirrors the Mongo collections in the design;
    # SQLite keeps local runs dependency-free.
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "./.lotus-data")).resolve())

    # LLM provider: "mock" (deterministic, offline), "openai", "azure_openai", "anthropic".
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "mock"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL", ""))
    azure_api_version: str = field(default_factory=lambda: _env("AZURE_API_VERSION", "2024-10-21"))
    # Back-pressure: maximum concurrent LLM calls across all workflows.
    llm_max_concurrency: int = field(default_factory=lambda: int(_env("LLM_MAX_CONCURRENCY", "8")))

    # Live synthetic feed.
    simulator_enabled: bool = field(default_factory=lambda: _env("SIMULATOR", "1") == "1")
    simulator_interval_s: float = field(default_factory=lambda: float(_env("SIM_INTERVAL", "6")))
    seed: int = field(default_factory=lambda: int(_env("SEED", "7")))

    # Gates.
    ocr_confidence_threshold: float = 0.80
    evidence_confidence_threshold: float = 0.60

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lotus.db"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "checkpoints.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"


settings = Settings()
