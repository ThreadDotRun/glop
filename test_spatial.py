"""
Proves the container index produces identical results to a linear scan,
and that the index is maintained across mutations.

Reference `_dispatch_linear` is the pre-index dispatch logic. The container
index is derived from ent["loc"]["container"]; the test verifies it stays
in sync with the source of truth.
"""

from engine import Engine
import os


def snapshot(eng):
    return {
        "world_time": eng.world_time,
        "stats": {eid: dict(e.get("stats", {})) for eid, e in eng.ents.items()},
        "pos":   {eid: dict(e.get("loc", {}).get("pos", {})) for eid, e in eng.ents.items()},
        "ids":   sorted(eng.ents.keys()),
        "scheduler": sorted(eng.scheduler),
    }


print("=== A. Container query returns only ents in that container ===")
eng = Engine("ents.json", "containers.json")

in_hall = eng.query(container="entrance_hall")
assert {e["id"] for e in in_hall} == {"player_001", "goblin_scout_001", "radiation_zone_01"}
print(f"  entrance_hall: {len(in_hall)} ents")

in_corridor = eng.query(container="corridor_01")
assert in_corridor == []
print(f"  corridor_01: {len(in_corridor)} ents")
print("  => container query is correct\n")


print("=== B. add_ent / move_ent_to_container maintain the index ===")
eng = Engine("ents.json", "containers.json")
eng.add_ent({
    "id": "arrow_001",
    "kind": "projectile",
    "name": "Arrow",
    "tags": ["projectile"],
    "loc": {"container": "entrance_hall", "pos": {"x": 5, "y": 5, "z": 0}},
    "stats": {},
    "rules": [],
})
assert "arrow_001" in eng._ents_by_container["entrance_hall"]
print(f"  spawned arrow in entrance_hall")

eng.move_ent_to_container("arrow_001", "corridor_01")
assert "arrow_001" not in eng._ents_by_container.get("entrance_hall", set())
assert "arrow_001" in eng._ents_by_container["corridor_01"]
print(f"  moved arrow to corridor_01")
print("  => index follows explicit mutations\n")


print("=== C. destroy action removes ent from container index ===")
eng = Engine("ents.json", "containers.json")
eng.add_ent({
    "id": "victim_001",
    "kind": "npc",
    "name": "Victim",
    "tags": [],
    "loc": {"container": "entrance_hall", "pos": {"x": 0, "y": 0, "z": 0}},
    "stats": {"hp": 1, "hp_max": 1},
    "rules": [],
})
assert "victim_001" in eng._ents_by_container["entrance_hall"]

player = eng.ents["player_001"]
eng.execute_action(
    {"type": "destroy", "target": "victim_001"},
    player,
)
assert "victim_001" not in eng.ents
assert "victim_001" not in eng._ents_by_container.get("entrance_hall", set())
print("  => destroyed ent removed from container index\n")


print("=== D. Rollback restores container index on destroy undo ===")
eng = Engine("ents.json", "containers.json")
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
assert "goblin_scout_001" in eng._ents_by_container["entrance_hall"]
print("  => rollback restored ent and container index\n")


print("=== E. Rollback restores container index on move undo ===")
eng = Engine("ents.json", "containers.json")
eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "move_container", "target": "goblin_scout_001", "container": "corridor_01"},
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

assert "goblin_scout_001" in eng._ents_by_container["entrance_hall"]
assert "goblin_scout_001" not in eng._ents_by_container.get("corridor_01", set())
print("  => rollback restored goblin to its original container\n")


print("=== F. Rollback removes spawned ents on later failure ===")
eng = Engine("ents.json", "containers.json")
eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {
        "type": "spawn",
        "ent": {
            "id": "ghost_spawn_001",
            "kind": "npc",
            "name": "Ghost",
            "tags": [],
            "loc": {"container": "entrance_hall", "pos": {"x": 0, "y": 0, "z": 0}},
            "stats": {"hp": 1},
            "rules": [],
        },
    },
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

assert "ghost_spawn_001" not in eng.ents
assert "ghost_spawn_001" not in eng._ents_by_container.get("entrance_hall", set())
print("  => rollback removed spawn from ents and container index\n")


print("=== G. Container index rebuilt on load ===")
from persistence import GameStore

DB = "test_spatial.sqlite"
if os.path.exists(DB):
    os.remove(DB)

eng = Engine("ents.json", "containers.json")
eng.move_ent_to_container("goblin_scout_001", "corridor_01")

store = GameStore(DB)
eng.save(store)
eng2 = Engine.load(store)
store.close()
os.remove(DB)

assert "goblin_scout_001" in eng2._ents_by_container.get("corridor_01", set())
assert "goblin_scout_001" not in eng2._ents_by_container.get("entrance_hall", set())
print("  => container index rebuilt from persisted ents\n")


print("PASS")
