"""Document store with a Mongo-like API, backed by SQLite (JSON documents).

Collections used:
  cases          — one document per claim packet (the case record)
  stage_runs     — idempotency ledger: (case, stage, input hash) -> output
  events         — live activity feed
  notifications  — email notifications
  traces         — LLM / tool spans keyed by correlation id
  settings       — notification category toggles, feed state

Swap for MongoDB by implementing the same five methods (see README).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional


class DocStore:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS docs (coll TEXT NOT NULL, id TEXT NOT NULL, seq INTEGER, "
            "body TEXT NOT NULL, PRIMARY KEY (coll, id))")
        self._conn.execute("CREATE INDEX IF NOT EXISTS docs_seq ON docs(coll, seq)")
        self._lock = threading.Lock()
        self._seq = (self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM docs").fetchone()[0])

    def put(self, coll: str, id: str, body: dict[str, Any]) -> None:
        with self._lock:
            self._seq += 1
            self._conn.execute(
                "INSERT INTO docs(coll, id, seq, body) VALUES (?,?,?,?) "
                "ON CONFLICT(coll, id) DO UPDATE SET body=excluded.body, seq=excluded.seq",
                (coll, id, self._seq, json.dumps(body, default=str)))

    def insert_if_absent(self, coll: str, id: str, body: dict[str, Any]) -> bool:
        """Atomic create; returns False if the id already exists (used for idempotency)."""
        with self._lock:
            self._seq += 1
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO docs(coll, id, seq, body) VALUES (?,?,?,?)",
                (coll, id, self._seq, json.dumps(body, default=str)))
            return cur.rowcount == 1

    def get(self, coll: str, id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT body FROM docs WHERE coll=? AND id=?", (coll, id)).fetchone()
        return json.loads(row[0]) if row else None

    def find(self, coll: str, where: Optional[dict[str, Any]] = None, limit: int = 1000,
             newest_first: bool = True) -> list[dict[str, Any]]:
        order = "DESC" if newest_first else "ASC"
        with self._lock:
            rows = self._conn.execute(
                f"SELECT body FROM docs WHERE coll=? ORDER BY seq {order}", (coll,)).fetchall()
        out: list[dict[str, Any]] = []
        for (body,) in rows:
            doc = json.loads(body)
            if where and any(doc.get(k) != v for k, v in where.items()):
                continue
            out.append(doc)
            if len(out) >= limit:
                break
        return out

    def delete(self, coll: str, id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM docs WHERE coll=? AND id=?", (coll, id))

    def count(self, coll: str) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM docs WHERE coll=?", (coll,)).fetchone()[0]

    def ids(self, coll: str) -> Iterable[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT id FROM docs WHERE coll=?", (coll,))]
