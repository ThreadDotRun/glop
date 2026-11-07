"""
Atomic SQLite persistence for the Engine.

Design:
  - One table `blobs(key TEXT PRIMARY KEY, value BLOB)`.
  - Every engine-owned object is pickled and stored under a `key`.
  - Keys are namespaced: 'meta:world_time', 'ent:<id>', 'sched:<n>', 'containers'.
  - Save and load are each wrapped in a single SQLite transaction, so
    a crash mid-write leaves the DB in the previous consistent state.
  - Objects must be picklable. The engine, containers, ents, rates, stats,
    position dicts, status lists, and scheduler entries all qualify.
"""
import pickle
import sqlite3
from pathlib import Path
from typing import Any, Optional


class GameStore:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, isolation_level=None)  # autocommit off-by-default via explicit BEGIN
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS blobs ("
                "  key TEXT PRIMARY KEY,"
                "  value BLOB NOT NULL"
                ")"
            )

    # ---------------------------------------------------------- low-level
    def _put(self, key: str, obj: Any):
        blob = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
        self._conn.execute(
            "INSERT INTO blobs(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, blob),
        )

    def _get(self, key: str) -> Optional[Any]:
        row = self._conn.execute(
            "SELECT value FROM blobs WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return pickle.loads(row[0])

    # ---------------------------------------------------------- atomic API
    def save(self, engine):
        """Persist the entire engine state atomically."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            # Wipe prior contents inside the same transaction
            self._conn.execute("DELETE FROM blobs")

            self._put("meta:world_time", engine.world_time)
            self._put("meta:sched_seq", engine._sched_seq)
            self._put("meta:containers", engine.containers)

            for ent_id, ent in engine.ents.items():
                self._put(f"ent:{ent_id}", ent)

            # Scheduler is a heap of tuples; pickling the list preserves order semantics
            self._put("sched:heap", list(engine.scheduler))

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def load(self, engine):
        """Replace the engine's state with what is on disk, atomically."""
        self._conn.execute("BEGIN")
        try:
            world_time = self._get("meta:world_time")
            if world_time is None:
                raise ValueError("no saved state in database")

            engine.world_time = world_time
            engine._sched_seq = self._get("meta:sched_seq") or 0
            engine.containers = self._get("meta:containers") or {}

            engine.ents = {}
            rows = self._conn.execute(
                "SELECT key, value FROM blobs WHERE key LIKE 'ent:%'"
            ).fetchall()
            for key, blob in rows:
                ent_id = key[len("ent:"):]
                engine.ents[ent_id] = pickle.loads(blob)

            sched = self._get("sched:heap") or []
            engine.scheduler = sched
            import heapq
            heapq.heapify(engine.scheduler)

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def exists(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM blobs LIMIT 1").fetchone()
        return row is not None

    def wipe(self):
        with self._conn:
            self._conn.execute("DELETE FROM blobs")

    def close(self):
        self._conn.close()
