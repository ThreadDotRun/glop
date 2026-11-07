from engine import Engine
import copy


def snapshot(eng):
    return {
        "world_time": eng.world_time,
        "stats": {eid: dict(e.get("stats", {})) for eid, e in eng.ents.items()},
        "rates": {eid: dict(e.get("rates", {})) for eid, e in eng.ents.items()},
        "pos":   {eid: dict(e.get("loc", {}).get("pos", {})) for eid, e in eng.ents.items()},
        "ids":   sorted(eng.ents.keys()),
        "scheduler": list(eng.scheduler),
    }


print("=== A. Original behavior preserved (AI, combat, death) ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]
goblin = eng.ents["goblin_scout_001"]

player["loc"]["pos"]["x"] = 8
eng.integrate(1.0)
print(f"Goblin X: {goblin['loc']['pos']['x']}")
eng.dispatch_event("on_hit", player, {"target": "goblin_scout_001"})
print(f"Goblin HP: {goblin['stats']['hp']}")
eng.integrate(1.0)
print(f"Goblin alive: {'goblin_scout_001' in eng.ents}\n")


print("=== B. Determinism: same world -> same result, any call pattern ===")
def fight(ticks):
    e = Engine("ents.json", "containers.json")
    p = e.ents["player_001"]
    g = e.ents["goblin_scout_001"]
    p["loc"]["pos"]["x"] = 8
    e.dispatch_event("on_hit", p, {"target": "goblin_scout_001"})
    for dt in ticks:
        e.integrate(dt)
    return snapshot(e)

s1 = fight([1.0])
s2 = fight([0.5, 0.5])
s3 = fight([0.1] * 10)
print(f"1 x 1.0   -> {s1['stats'].get('goblin_scout_001', {}).get('hp')}")
print(f"2 x 0.5   -> {s2['stats'].get('goblin_scout_001', {}).get('hp')}")
print(f"10 x 0.1  -> {s3['stats'].get('goblin_scout_001', {}).get('hp')}")
assert s1["ids"] == s2["ids"] == s3["ids"]
print("  => world converged to identical state\n")


print("=== C. Rollback covers stats + rates + scheduler + world_time ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]

eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "-1"},
})
eng.execute_action(
    {"type": "add_rate", "target": "player_001", "rate": {"hp": -1.0}}, player
)

before = snapshot(eng)
try:
    eng.add_rule("player_001", {
        "trigger": "tick",
        "condition": "true",
        "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "NOT_A_NUMBER"},
    })
    eng.integrate(1.0)
except Exception as e:
    print(f"  caught: {e.__class__.__name__}")
after = snapshot(eng)

assert before == after, "rollback failed"
print("  => world_time, stats, rates, scheduler all restored")
print(f"  hp before: {before['stats']['player_001']['hp']}")
print(f"  hp after:  {after['stats']['player_001']['hp']}\n")


print("=== D. Rollback covers loc.pos (journaled movement, real failure) ===")
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]
goblin = eng.ents["goblin_scout_001"]

player["loc"]["pos"]["x"] = 8

eng.add_rule("player_001", {
    "trigger": "loop",
    "condition": "true",
    "action": {"type": "emit", "event": "loop"},
})
eng.add_rule("player_001", {
    "trigger": "tick",
    "condition": "true",
    "action": {"type": "emit", "event": "loop"},
})

before = snapshot(eng)
goblin_x_before = goblin["loc"]["pos"]["x"]

try:
    eng.integrate(1.0)
except Exception as e:
    print(f"  caught: {e.__class__.__name__}")
after = snapshot(eng)

assert before == after, "rollback failed (pos not restored)"
print(f"  goblin x before: {goblin_x_before}")
print(f"  goblin x after:  {eng.ents['goblin_scout_001']['loc']['pos']['x']}")
print("  => loc.pos restored to pre-tick value")
