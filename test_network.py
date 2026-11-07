"""
Proves the wire layer is lossless for the projection's scope:
  1. snapshot + deltas reconstructs byte-identical engine state
  2. total bytes sent is bounded by the size of what actually changed
  3. codec is swappable without affecting the protocol
"""

from engine import Engine
from network import Projection, StateMirror, RawJSONCodec
import copy
import json


def engine_snapshot(eng):
    return {
        "world_time": eng.world_time,
        "sched_seq": eng._sched_seq,
        "ents": copy.deepcopy(eng.ents),
        "scheduler": sorted(eng.scheduler),
    }


def mirror_snapshot(mirror):
    return {
        "world_time": mirror.world_time,
        "sched_seq": mirror._sched_seq,
        "ents": copy.deepcopy(mirror.ents),
        "scheduler": sorted(mirror.scheduler),
    }


def assert_equal_snapshots(eng_snap, mir_snap, label):
    """Compare with detailed diagnostics — silent if equal."""
    ok = True
    for key in ("world_time", "sched_seq", "scheduler"):
        if eng_snap[key] != mir_snap[key]:
            print(f"  DIFF {label}.{key}: engine={eng_snap[key]!r} mirror={mir_snap[key]!r}")
            ok = False
    eng_ents = eng_snap["ents"]
    mir_ents = mir_snap["ents"]
    if set(eng_ents.keys()) != set(mir_ents.keys()):
        print(f"  DIFF {label}.ids:")
        print(f"    engine_only: {set(eng_ents) - set(mir_ents)}")
        print(f"    mirror_only: {set(mir_ents) - set(eng_ents)}")
        ok = False
    for eid in set(eng_ents) & set(mir_ents):
        if eng_ents[eid] != mir_ents[eid]:
            print(f"  DIFF {label}.ent[{eid}]:")
            for k in set(eng_ents[eid]) | set(mir_ents[eid]):
                ev = eng_ents[eid].get(k)
                mv = mir_ents[eid].get(k)
                if ev != mv:
                    print(f"    {k}:")
                    print(f"      engine: {ev!r}")
                    print(f"      mirror: {mv!r}")
            ok = False
    assert ok, f"{label} snapshots diverge"


print("=== A. Snapshot + delta reconstructs engine state exactly ===")
eng = Engine("ents.json", "containers.json")
proj = Projection(eng)
mirror = StateMirror()

player = eng.ents["player_001"]
player["loc"]["pos"]["x"] = 8
eng.dispatch_event("on_hit", player, {"target": "goblin_scout_001"})

snap_frame = proj.snapshot()
mirror.apply(snap_frame)
assert_equal_snapshots(engine_snapshot(eng), mirror_snapshot(mirror), "initial")
print(f"  after snapshot: {len(mirror.ents)} ents, world_time={mirror.world_time}")

frame = proj.integrate_and_project(1.0)
print(f"  tick 1 ops: {frame['ops']}")
mirror.apply(frame)
assert_equal_snapshots(engine_snapshot(eng), mirror_snapshot(mirror), "tick1")
print(f"  tick 1: {len(frame['ops'])} ops, goblin alive={'goblin_scout_001' in mirror.ents}")

frame = proj.integrate_and_project(1.0)
mirror.apply(frame)
assert_equal_snapshots(engine_snapshot(eng), mirror_snapshot(mirror), "tick2")
print(f"  tick 2: {len(frame['ops'])} ops, goblin alive={'goblin_scout_001' in mirror.ents}")
print("  => snapshot + deltas reconstruct engine state\n")


print("=== B. Delta is proportional to mutations, not world size ===")
eng = Engine("ents.json", "containers.json")
proj = Projection(eng)

for i in range(500):
    eng.add_ent({
        "id": f"idle_{i:04d}",
        "kind": "npc",
        "name": f"Idle {i}",
        "tags": [],
        "loc": {"container": "entrance_hall", "pos": {"x": 0, "y": 0, "z": 0}},
        "stats": {"hp": 10},
        "rules": [],
    })

snap = proj.snapshot()
snap_bytes = len(proj.encode(snap))
print(f"  snapshot: {len(snap['ops'])} ops, {snap_bytes} bytes (world = {len(eng.ents)} ents)")

eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "-1"},
})
frame = proj.integrate_and_project(1.0)
delta_bytes = len(proj.encode(frame))
print(f"  delta: {len(frame['ops'])} ops, {delta_bytes} bytes")
assert delta_bytes < snap_bytes / 50, \
    f"delta should be much smaller than snapshot (got {delta_bytes} vs {snap_bytes})"
print("  => delta traffic is proportional to changes, not world size\n")


print("=== C. Codec is swappable without changing the protocol ===")
class CountingCodec:
    name = "counting-raw-json-v1"
    def __init__(self):
        self._inner = RawJSONCodec()
        self.encode_calls = 0
        self.decode_calls = 0
    def encode(self, frame):
        self.encode_calls += 1
        return self._inner.encode(frame)
    def decode(self, payload):
        self.decode_calls += 1
        return self._inner.decode(payload)

eng = Engine("ents.json", "containers.json")
codec = CountingCodec()
proj = Projection(eng, codec=codec)
mirror = StateMirror()

frame = proj.snapshot()
mirror.apply(frame)
assert codec.encode_calls == 0
payload = proj.encode(frame)
assert codec.encode_calls == 1
roundtrip = proj.decode(payload)
assert codec.decode_calls == 1
assert roundtrip == frame
print(f"  codec: {codec.name}, encode calls={codec.encode_calls}, decode calls={codec.decode_calls}")
print("  => codec swap does not change protocol\n")


print("=== D. A late-joining client can catch up from a single snapshot ===")
eng = Engine("ents.json", "containers.json")
proj = Projection(eng)

for _ in range(10):
    proj.integrate_and_project(0.5)

mirror = StateMirror()
mirror.apply(proj.snapshot())

assert_equal_snapshots(engine_snapshot(eng), mirror_snapshot(mirror), "late_join")
print(f"  late-join snapshot at world_time={eng.world_time:.1f}: matched")
print("  => snapshots are complete state, joinable at any time\n")


print("PASS")
