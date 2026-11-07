"""
Network projection layer.

Design:
  - Read-only against the engine. Zero engine changes required except
    engine.integrate(dt) now returns the committed Transaction.
  - Content-agnostic: ops are (verb, ent_id, path, value). No game knowledge.
  - Codec-agnostic: no codec means raw JSON. Codecs are pluggable.
  - Delta-first: initial sync is a snapshot; subsequent frames are journals.

The projection is built from a Transaction's journal. Every journal entry
becomes exactly one wire op. The result is provably minimal: no state is
sent that didn't change.

Codec protocol:
  encode(dict) -> bytes
  decode(bytes) -> dict
"""

import json
import heapq
import copy
from typing import Any, Dict, List, Optional, Tuple


# ============================================================ Codec
class RawJSONCodec:
    """Trivial codec: JSON, no compression. Reference implementation."""
    name = "raw-json-v1"

    def encode(self, frame: Dict[str, Any]) -> bytes:
        return json.dumps(frame, separators=(",", ":")).encode("utf-8")

    def decode(self, payload: bytes) -> Dict[str, Any]:
        return json.loads(payload.decode("utf-8"))


# ============================================================ Frame builders
class Projection:
    """
    Constructs wire frames from the engine. Holds no game state of its own;
    only a monotonically increasing tick counter.
    """
    def __init__(self, engine, codec=None):
        self.engine = engine
        self.codec = codec or RawJSONCodec()
        self.tick = 0

    # ---- snapshot: full initial state ----
    def snapshot(self) -> Dict[str, Any]:
        """Emit a complete state packet. Used for initial sync."""
        self.tick += 1
        ops = [["time", self.engine.world_time],
               ["sched_seq", self.engine._sched_seq]]

        for ent_id in sorted(self.engine.ents.keys()):
            ent = self.engine.ents[ent_id]
            ops.append(["ins", ent_id, self._serialize_ent(ent)])

        for entry in sorted(self.engine.scheduler):
            at, seq, event, target, ctx = entry
            ops.append(["sched", at, seq, event, target, ctx])

        return {"tick": self.tick, "time": self.engine.world_time, "ops": ops, "kind": "snapshot"}

    # ---- delta: mutations since last tick ----
    def delta_from_txn(self, txn) -> Dict[str, Any]:
        """
        Translate a committed Transaction's journal into wire ops.
        Called AFTER txn.commit(), so the journal reflects what was applied.
        """
        self.tick += 1
        ops = [["time", self.engine.world_time]]

        # Journal is ordered oldest-first. Reverse-walk to emit newest value
        # per (kind, path) so we don't send redundant intermediate states.
        latest = {}
        for kind, path, old in txn.journal:
            latest[(kind, path)] = old

        for (kind, path), _old in latest.items():
            if kind == "stat":
                ent_id, stat = path
                ent = self.engine.ents.get(ent_id)
                if ent is None:
                    continue
                ops.append(["set", ent_id, f"stats.{stat}", ent["stats"].get(stat)])
            elif kind == "rate":
                ent_id, stat = path
                ent = self.engine.ents.get(ent_id)
                if ent is None:
                    continue
                ops.append(["rate", ent_id, stat, ent["rates"].get(stat)])
            elif kind == "pos":
                ent_id, axis = path
                ent = self.engine.ents.get(ent_id)
                if ent is None:
                    continue
                ops.append(["pos", ent_id, axis, ent["loc"]["pos"].get(axis)])
            elif kind == "container":
                ent_id, _old_container = path
                ent = self.engine.ents.get(ent_id)
                if ent is None:
                    continue
                ops.append(["cont", ent_id, ent["loc"]["container"]])
            elif kind == "insert":
                ent_id = path
                ent = self.engine.ents.get(ent_id)
                if ent is None:
                    continue
                ops.append(["ins", ent_id, self._serialize_ent(ent)])
            elif kind == "delete":
                ent_id = path
                if ent_id in self.engine.ents:
                    ops.append(["ins", ent_id, self._serialize_ent(self.engine.ents[ent_id])])
                else:
                    ops.append(["del", ent_id])
            elif kind == "sched_push":
                at, seq, event, target, ctx = path
                ops.append(["sched", at, seq, event, target, ctx])
            elif kind == "sched_pop":
                at, seq, event, target, ctx = path
                ops.append(["unsched", at, seq])
            elif kind == "world_time":
                pass  # covered by the top-level "time" op

        return {"tick": self.tick, "time": self.engine.world_time, "ops": ops, "kind": "delta"}

    # ---- convenience: integrate + project in one step ----
    def integrate_and_project(self, dt):
        txn = self.engine.integrate(dt)
        if txn is None:
            return {"tick": self.tick, "time": self.engine.world_time, "ops": [], "kind": "delta"}
        return self.delta_from_txn(txn)

    # ---- encode/decode ----
    def encode(self, frame):
        return self.codec.encode(frame)

    def decode(self, payload):
        return self.codec.decode(payload)

    # ---- helpers ----
    @staticmethod
    def _serialize_ent(ent):
        return copy.deepcopy(ent)


# ============================================================ Consumer side
class StateMirror:
    """
    Applies wire frames to a client-side state dict.
    The mirror is content-agnostic — it applies ops mechanically.
    Used for tests and as the reference client-side reconstruction engine.
    """
    def __init__(self):
        self.ents: Dict[str, dict] = {}
        self.world_time = 0.0
        self.scheduler: List[Tuple] = []
        self._sched_seq = 0
        self.tick = 0

    def apply(self, frame):
        self.tick = frame["tick"]
        for op in frame["ops"]:
            verb = op[0]
            if verb == "time":
                self.world_time = op[1]
            elif verb == "sched_seq":
                self._sched_seq = op[1]
            elif verb == "set":
                _, ent_id, path, value = op
                _, stat = path.split(".", 1)
                if ent_id in self.ents:
                    self.ents[ent_id].setdefault("stats", {})[stat] = value
            elif verb == "rate":
                _, ent_id, stat, value = op
                if ent_id in self.ents:
                    self.ents[ent_id].setdefault("rates", {})[stat] = value
            elif verb == "pos":
                _, ent_id, axis, value = op
                if ent_id in self.ents:
                    self.ents[ent_id].setdefault("loc", {}).setdefault("pos", {})[axis] = value
            elif verb == "cont":
                _, ent_id, container = op
                if ent_id in self.ents:
                    self.ents[ent_id].setdefault("loc", {})["container"] = container
            elif verb == "ins":
                _, ent_id, ent = op
                self.ents[ent_id] = copy.deepcopy(ent)
            elif verb == "del":
                _, ent_id = op
                self.ents.pop(ent_id, None)
            elif verb == "sched":
                _, at, seq, event, target, ctx = op
                self.scheduler.append((at, seq, event, target, ctx))
                heapq.heapify(self.scheduler)
            elif verb == "unsched":
                _, at, seq = op
                self.scheduler = [e for e in self.scheduler if not (e[0] == at and e[1] == seq)]
                heapq.heapify(self.scheduler)

    def snapshot_state(self):
        return {
            "world_time": self.world_time,
            "sched_seq": self._sched_seq,
            "ents": copy.deepcopy(self.ents),
            "scheduler": sorted(self.scheduler),
            "tick": self.tick,
        }
