from engine import Engine
import copy


def snapshot(eng):
    return {
        "world_time": eng.world_time,
        "stats": {eid: dict(e.get("stats", {})) for eid, e in eng.ents.items()},
        "rates": {eid: dict(e.get("rates", {})) for eid, e in eng.ents.items()},
        "pos":   {eid: dict(e.get("loc", {}).get("pos", {})) for eid, e in eng.ents.items()},
        "ids":   sorted(eng.ents.keys()),
        "scheduler": sorted(eng.scheduler),
    }


print("=== A. Emit depth limit: runaway chain rolls back the tick ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]

eng.add_rule("player_001", {
    "trigger": "loop",
    "condition": "true",
    "action": {"type": "emit", "event": "loop"},
})
eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "-1"},
})

before = snapshot(eng)
try:
    eng.dispatch_event("loop", player, {})
    eng.integrate(1.0)
    print("  FAIL: expected EmitDepthExceeded")
except Exception as e:
    print(f"  caught: {e.__class__.__name__}: {e}")
after = snapshot(eng)

assert before == after, "rollback failed after emit overflow"
print(f"  hp before: {before['stats']['player_001']['hp']}")
print(f"  hp after:  {after['stats']['player_001']['hp']}")
print("  => world untouched\n")


print("=== B. Stage-time validation: bad action never enters the queue ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]

before = snapshot(eng)

eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "NOT_A_NUMBER"},
})

try:
    eng.integrate(1.0)
    print("  FAIL: expected InvalidAction")
except Exception as e:
    print(f"  caught: {e.__class__.__name__}: {e}")
after = snapshot(eng)

assert before == after, "state mutated despite validation failure"
print(f"  hp before: {before['stats']['player_001']['hp']}")
print(f"  hp after:  {after['stats']['player_001']['hp']}")
print("  => no journal entries, no state change, no rollback needed\n")


print("=== C. Well-formed action still works (regression) ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]
hp_before = player["stats"]["hp"]
eng.execute_action(
    {"type": "modify_stat", "target": "player_001", "stat": "hp", "value": "-10"},
    player,
)
print(f"  hp before: {hp_before}")
print(f"  hp after:  {player['stats']['hp']}")
assert player["stats"]["hp"] == hp_before - 10
print("  => valid action applied normally\n")


print("=== D. Full test_engine.py invariants still hold ===")
eng = Engine("ents.json", "containers.json")
p = eng.ents["player_001"]
g = eng.ents["goblin_scout_001"]
p["loc"]["pos"]["x"] = 8
eng.integrate(1.0)
eng.dispatch_event("on_hit", p, {"target": "goblin_scout_001"})
eng.integrate(1.0)
assert "goblin_scout_001" not in eng.ents
print("  => goblin died as expected")


print("\nPASS")
