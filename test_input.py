from engine import Engine, MAX_SCHED_EVENTS_PER_TICK, SchedulerOverflow
from input_bridge import InputBridge, InputRejected
import os
import heapq


print("=== A. Bridge rejects unauthorized sources ===")
eng = Engine("ents.json", "containers.json")
bridge = InputBridge(eng, source_ids={"player_001"})

try:
    bridge.input("goblin_scout_001", "goblin_scout_001", "attack", {})
    print("  FAIL: expected InputRejected")
    raise SystemExit(1)
except InputRejected as e:
    print(f"  caught: {e}")
print("  => unauthorized source rejected\n")


print("=== B. Bridge rejects unknown actors ===")
try:
    bridge.input("player_001", "nonexistent_ent", "attack", {})
    print("  FAIL: expected InputRejected")
    raise SystemExit(1)
except InputRejected as e:
    print(f"  caught: {e}")
print("  => unknown actor rejected\n")


print("=== C. Bridge rejects unregistered events ===")
try:
    bridge.input("player_001", "player_001", "nuke", {})
    print("  FAIL: expected InputRejected")
    raise SystemExit(1)
except InputRejected as e:
    print(f"  caught: {e}")
print("  => unknown event rejected\n")


print("=== D. Valid input queues and drains during integrate ===")
eng = Engine("ents.json", "containers.json")
bridge = InputBridge(eng, source_ids={"player_001"})

eng.add_rule("player_001", {
    "trigger": "move",
    "condition": "actor == self.id",
    "action": {"type": "modify_stat", "target": "self", "stat": "x_moves", "value": 1},
})

player = eng.ents["player_001"]
bridge.input("player_001", "player_001", "move", {"direction": "east"})
bridge.input("player_001", "player_001", "move", {"direction": "east"})
print(f"  pending inputs: {len(bridge.pending())}")
print(f"  x_moves before: {player['stats'].get('x_moves', 0)}")

eng.integrate(1.0)

print(f"  pending after integrate: {len(bridge.pending())}")
print(f"  x_moves after: {player['stats'].get('x_moves', 0)}")
assert player["stats"].get("x_moves", 0) == 2
print("  => inputs dispatched atomically\n")


print("=== E. Bad input rule rolls back atomically ===")
eng = Engine("ents.json", "containers.json")
bridge = InputBridge(eng, source_ids={"player_001"})

eng.add_rule("player_001", {
    "trigger": "attack",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "hp", "value": "NOT_A_NUMBER"},
})

player = eng.ents["player_001"]
hp_before = player["stats"]["hp"]
bridge.input("player_001", "player_001", "attack", {})

try:
    eng.integrate(1.0)
    print("  FAIL: expected InvalidAction")
    raise SystemExit(1)
except Exception as e:
    print(f"  caught: {e.__class__.__name__}")

print(f"  hp before: {hp_before}")
print(f"  hp after:  {player['stats']['hp']}")
assert player["stats"]["hp"] == hp_before
print("  => bad input rolled back atomically\n")


print("=== F. Scheduler self-feed is deferred, not infinite ===")
eng = Engine("ents.json", "containers.json")
eng.add_rule("player_001", {
    "trigger": "self_feed",
    "condition": "true",
    "action": {"type": "schedule", "in": 0, "event": "self_feed", "target": "self"},
})
player = eng.ents["player_001"]

eng.execute_action(
    {"type": "schedule", "in": 0, "event": "self_feed", "target": "player_001"},
    player,
)
assert len(eng.scheduler) == 1

eng.integrate(1.0)
assert len(eng.scheduler) == 1, f"expected 1 deferred event, got {len(eng.scheduler)}"
print(f"  after tick 1: {len(eng.scheduler)} event deferred")

eng.integrate(1.0)
assert len(eng.scheduler) == 1
print(f"  after tick 2: {len(eng.scheduler)} event deferred")

for _ in range(100):
    eng.integrate(1.0)
assert len(eng.scheduler) == 1, f"scheduler accumulated {len(eng.scheduler)} entries"
print(f"  after 100 ticks: {len(eng.scheduler)} event deferred (bounded)")
print("  => self-feed is one-per-tick, structurally bounded\n")


print("=== F2. Guard fires if scheduler is overfilled ===")
eng = Engine("ents.json", "containers.json")
for i in range(MAX_SCHED_EVENTS_PER_TICK + 5):
    heapq.heappush(eng.scheduler, (0.0, i, "noop", "player_001", {}))

try:
    eng.integrate(1.0)
    print("  FAIL: expected SchedulerOverflow")
    raise SystemExit(1)
except SchedulerOverflow as e:
    print(f"  caught: {e}")
print("  => guard fires when scheduler is overfilled\n")


print("=== G. Input survives persistence ===")
from persistence import GameStore

DB = "test_input.sqlite"
if os.path.exists(DB):
    os.remove(DB)

eng = Engine("ents.json", "containers.json")
bridge = InputBridge(eng, source_ids={"player_001"})
eng.add_rule("player_001", {
    "trigger": "move",
    "condition": "true",
    "action": {"type": "modify_stat", "target": "self", "stat": "x_moves", "value": 1},
})

store = GameStore(DB)
eng.save(store)
eng2 = Engine.load(store)
bridge2 = InputBridge(eng2, source_ids={"player_001"})

bridge2.input("player_001", "player_001", "move", {})
eng2.integrate(1.0)
assert eng2.ents["player_001"]["stats"]["x_moves"] == 1
print(f"  reloaded world responded to input; x_moves = {eng2.ents['player_001']['stats']['x_moves']}")
print("  => input bridge works on reloaded engines\n")

store.close()
os.remove(DB)
print("PASS")
