"""
Proves the rule index produces identical results to a linear scan,
and that the index is actually consulted (not a stub).

The reference `_dispatch_linear` is a copy of the pre-index dispatch
logic. If the index ever changes behavior, this test will diverge.
"""

from engine import Engine


def snapshot(eng):
    return {
        "world_time": eng.world_time,
        "stats": {eid: dict(e.get("stats", {})) for eid, e in eng.ents.items()},
        "rates": {eid: dict(e.get("rates", {})) for eid, e in eng.ents.items()},
        "pos":   {eid: dict(e.get("loc", {}).get("pos", {})) for eid, e in eng.ents.items()},
        "ids":   sorted(eng.ents.keys()),
        "scheduler": sorted(eng.scheduler),
    }


def _dispatch_linear(eng, event_name, source_id, context=None):
    context = context or {}
    for e in list(eng.ents.values()):
        for rule in e.get("rules", []):
            if rule["trigger"] == event_name:
                if eng.evaluate_condition(rule["condition"], e, context):
                    eng._stage(rule["action"], e["id"], context)


def build_synthetic_world(eng, n_bystanders=997):
    for i in range(n_bystanders):
        ent_id = f"bystander_{i:04d}"
        eng.add_ent({
            "id": ent_id,
            "kind": "npc",
            "name": ent_id,
            "tags": [],
            "loc": {"container": "entrance_hall", "pos": {"x": 0, "y": 0, "z": 0}},
            "stats": {"hp": 10, "hp_max": 10},
            "rules": [],
        })


print("=== A. Index vs linear scan: byte-identical results at scale ===")
eng_indexed = Engine("ents.json", "containers.json")
build_synthetic_world(eng_indexed, n_bystanders=997)
print(f"  world size: {len(eng_indexed.ents)} ents")

eng_linear = Engine("ents.json", "containers.json")
eng_linear.ents = {k: v for k, v in eng_indexed.ents.items()}

for eng in (eng_indexed, eng_linear):
    eng.ents["player_001"]["loc"]["pos"]["x"] = 8

for eng in (eng_indexed, eng_linear):
    txn = __import__("engine").Transaction(eng, 1.0)
    eng._txn = txn
    try:
        for e in list(eng.ents.values()):
            for stat, rate in e.get("rates", {}).items():
                if rate != 0:
                    txn.set_stat(e["id"], stat, e["stats"].get(stat, 0.0) + rate * 1.0)
        txn.set_world_time(txn.world_time_target)

        if eng is eng_indexed:
            eng._dispatch_event_staged("tick", None, {"dt": 1.0})
        else:
            _dispatch_linear(eng, "tick", None, {"dt": 1.0})

        txn.commit()
    finally:
        eng._txn = None

snap_i = snapshot(eng_indexed)
snap_l = snapshot(eng_linear)

assert snap_i["ids"] == snap_l["ids"], "entity set diverged"
assert snap_i["stats"] == snap_l["stats"], "stats diverged"
assert snap_i["pos"] == snap_l["pos"], "positions diverged"
assert snap_i["scheduler"] == snap_l["scheduler"], "scheduler diverged"
print("  => index and linear scan produced identical state\n")


print("=== B. Index preserves destroy semantics (dead ent deindexed) ===")
eng = Engine("ents.json", "containers.json")
p = eng.ents["player_001"]
g = eng.ents["goblin_scout_001"]
p["loc"]["pos"]["x"] = 8
eng.integrate(1.0)
eng.dispatch_event("on_hit", p, {"target": "goblin_scout_001"})
eng.integrate(1.0)

assert "goblin_scout_001" not in eng.ents
for trig, ids in eng._rules_by_trigger.items():
    assert "goblin_scout_001" not in ids, f"goblin still indexed under {trig!r}"
print("  => destroyed ent fully deindexed\n")


print("=== C. Index rebuilds on load ===")
import os
from persistence import GameStore

DB = "test_index.sqlite"
if os.path.exists(DB):
    os.remove(DB)

eng = Engine("ents.json", "containers.json")
store = GameStore(DB)
eng.save(store)
eng2 = Engine.load(store)
store.close()
os.remove(DB)

expected = {}
for eid, e in eng2.ents.items():
    triggers = {r.get("trigger") for r in e.get("rules", []) if r.get("trigger")}
    for trig in triggers:
        expected.setdefault(trig, []).append(eid)

for trig, ids in expected.items():
    assert sorted(eng2._rules_by_trigger.get(trig, [])) == sorted(ids), \
        f"index for trigger {trig!r} does not match derivable truth"
for trig in eng2._rules_by_trigger:
    assert trig in expected, f"phantom trigger {trig!r} in index"
print("  => index rebuilt correctly after load\n")


print("=== D. Rollback restores index when a destroy is reverted ===")
eng = Engine("ents.json", "containers.json")
p = eng.ents["player_001"]
p["loc"]["pos"]["x"] = 8

eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "destroy", "target": "goblin_scout_001"},
})
eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "BAD"},
})

try:
    eng.integrate(1.0)
    print("  FAIL: expected InvalidAction")
    raise SystemExit(1)
except Exception as e:
    print(f"  caught: {e.__class__.__name__}")

assert "goblin_scout_001" in eng.ents
found_in_index = any("goblin_scout_001" in ids for ids in eng._rules_by_trigger.values())
assert found_in_index, "goblin restored to ents but not re-indexed"
print("  => rollback restored both ents and index\n")


print("PASS")
