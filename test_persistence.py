import os
import heapq
from engine import Engine
from persistence import GameStore


def snapshot(eng):
    return {
        "world_time": eng.world_time,
        "sched_seq": eng._sched_seq,
        "stats": {eid: dict(e.get("stats", {})) for eid, e in eng.ents.items()},
        "rates": {eid: dict(e.get("rates", {})) for eid, e in eng.ents.items()},
        "pos":   {eid: dict(e.get("loc", {}).get("pos", {})) for eid, e in eng.ents.items()},
        "ids":   sorted(eng.ents.keys()),
        "scheduler": sorted(eng.scheduler),
    }


DB = "test_world.sqlite"
if os.path.exists(DB):
    os.remove(DB)

print("=== 1. Build a world with a pending scheduled event ===")
store = GameStore(DB)
eng = Engine("ents.json", "containers.json")
player = eng.ents["player_001"]

# Move player near goblin and hit it, then schedule a future event
player["loc"]["pos"]["x"] = 8
eng.dispatch_event("on_hit", player, {"target": "goblin_scout_001"})
eng.integrate(1.0)

# Schedule a buff_expired at t=3.0 (world_time is 1.0, so it fires in 2s)
# Attach a rule to the player that reacts to buff_expired.
player["rules"].append({
    "trigger": "buff_expired",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "-42"},
})
eng.execute_action(
    {"type": "schedule", "in": 2.0, "event": "buff_expired", "target": "player_001"},
    player,
)

snap_before = snapshot(eng)
print(f"  world_time={snap_before['world_time']}")
print(f"  goblin alive: {'goblin_scout_001' in eng.ents}")
print(f"  scheduler depth: {len(snap_before['scheduler'])}")
print(f"  sched_seq: {snap_before['sched_seq']}\n")


print("=== 2. Persist atomically ===")
eng.save(store)
print(f"  rows in db: {store._conn.execute('SELECT COUNT(*) FROM blobs').fetchone()[0]}\n")


print("=== 3. Wipe the in-memory engine entirely ===")
del eng, player


print("=== 4. Reload from the store ===")
eng = Engine.load(store)
snap_after = snapshot(eng)
print(f"  world_time={snap_after['world_time']}")
print(f"  goblin alive: {'goblin_scout_001' in eng.ents}")
print(f"  scheduler depth: {len(snap_after['scheduler'])}")
print(f"  sched_seq: {snap_after['sched_seq']}\n")


print("=== 5. Prove byte-equivalence of pre-save and post-load snapshots ===")
assert snap_before == snap_after, "reload did not reproduce identical state"
print("  => state identical\n")


print("=== 6. Prove scheduled event survives reload and fires correctly ===")
# world_time is 1.0. Pending buff_expired fires at t=3.0.
p = eng.ents["player_001"]
hp_at_reload = p["stats"]["hp"]
print(f"  hp at reload: {hp_at_reload}")

eng.integrate(1.5)   # world_time -> 2.5. Event has NOT fired yet.
print(f"  hp at t=2.5: {p['stats']['hp']}  (should still be {hp_at_reload})")

eng.integrate(1.0)   # world_time -> 3.5. Event FIRES.
print(f"  hp at t=3.5: {p['stats']['hp']}  (should be {hp_at_reload - 42})")
assert p["stats"]["hp"] == hp_at_reload - 42
print("  => scheduled event preserved across reload\n")


print("=== 7. Continue creating new scheduled events after reload ===")
# Prove _sched_seq counter kept advancing correctly, no collisions.
eng.execute_action(
    {"type": "schedule", "in": 1.0, "event": "buff_expired", "target": "player_001"},
    p,
)
assert len(eng.scheduler) == 1
print(f"  new event scheduled; scheduler depth = {len(eng.scheduler)}")
print("  => scheduler remains consistent after reload\n")


print("=== 8. Wipe the DB (demonstrates nuke) ===")
store.wipe()
print(f"  rows after wipe: {store._conn.execute('SELECT COUNT(*) FROM blobs').fetchone()[0]}")
print(f"  exists() = {store.exists()}")


store.close()
os.remove(DB)
print("\nPASS")
