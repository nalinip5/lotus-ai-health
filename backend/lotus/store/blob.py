"""Content-addressed blob store on the local filesystem (stands in for Azure Blob).

Layout: <root>/<case_id>/<kind>/<name>  with a sha256 recorded by the caller.
Packets, ADI/structuring output and generated letters all live here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class BlobStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, case_id: str, kind: str, name: str) -> Path:
        safe = lambda s: "".join(c for c in s if c.isalnum() or c in "-_.")  # noqa: E731
        p = self.root / safe(case_id) / safe(kind) / safe(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put_bytes(self, case_id: str, kind: str, name: str, data: bytes) -> str:
        self._path(case_id, kind, name).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def put_json(self, case_id: str, kind: str, name: str, obj: Any) -> str:
        return self.put_bytes(case_id, kind, name, json.dumps(obj, indent=2, default=str).encode())

    def get_bytes(self, case_id: str, kind: str, name: str) -> bytes | None:
        p = self._path(case_id, kind, name)
        return p.read_bytes() if p.exists() else None
