"""
Smoke test for the civ data set. Asserts the invariants that the
data-only stability plan depends on. Run with:

    python test_smoke.py

If an assertion fails, the data file has drifted from the engine's
actual semantics. Each block names the engine behavior being relied on.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent))
sys.path.insert(0, str(HERE))

from engine import Engine
from input_bridge import InputBridge

EVENTS = {"found_city", "production_complete", "attack", "check_death", "build_phase"}


def fresh():
    eng = Engine(str(HERE / "ents.json"), str(HERE / "containers.json"))
    bridge = InputBridge(eng, source_ids={"rome", "aztec"}, events=EVENTS)
    return eng, bridge


def tick(eng, n=1):
    for _ in range(n):
        eng.integrate(1.0)


print("=== 1. cities and combat units do not pre-exist ===")
eng, _ = fresh()
for absent in ("city_rome_city", "city_tenochtitlan_city",
               "unit_rome_warrior", "unit_aztec_jaguar"):
    assert absent not in eng.ents, f"{absent} must not pre-exist (defeats distance > 99 guard)"
print("  no pre-existing cities or warriors\n")

print("=== 2. MP regen is +1/tick and never overshoots ===")
eng, _ = fresh()
s = eng.ents["unit_rome_settler"]
s["stats"]["mp"] = 0.0
tick(eng, 1)
assert s["stats"]["mp"] == 1.0, f"mp should be 1.0 after one tick from 0, got {s['stats']['mp']}"
tick(eng, 1)
assert s["stats"]["mp"] == 2.0, f"mp should be 2.0 after two ticks from 0, got {s['stats']['mp']}"
tick(eng, 5)
assert s["stats"]["mp"] == 2.0, f"mp must cap at 2.0, got {s['stats']['mp']}"
print("  mp 0 -> 1 -> 2 -> 2 (capped)\n")

print("=== 3. found_city spawns city and consumes settler ===")
eng, bridge = fresh()
assert "unit_rome_settler" in eng.ents
bridge.input("rome", "rome", "found_city", {})
tick(eng, 1)
assert "city_rome_city" in eng.ents, "found must create city"
assert "unit_rome_settler" not in eng.ents, "found must consume settler"
print("  city spawned, settler destroyed\n")

print("=== 4. build_phase spawns warrior exactly once per faction ===")
eng, _ = fresh()
# Force both factions over the hammer threshold.
eng.ents["rome"]["stats"]["hammers"] = 10
eng.ents["aztec"]["stats"]["hammers"] = 10
# Turn 1: Turn Clock schedules build_phase for t+1.
# Turn 2: build_phase fires for both factions.
tick(eng, 2)
assert "unit_rome_warrior" in eng.ents, "Rome must produce a warrior"
assert "unit_aztec_jaguar" in eng.ents, "Aztec must produce a jaguar"
assert eng.ents["rome"]["stats"]["hammers"] < 10, "Rome hammers must be spent"
assert eng.ents["aztec"]["stats"]["hammers"] < 10, "Aztec hammers must be spent"
print(f"  rome hammers={eng.ents['rome']['stats']['hammers']}, "
      f"aztec hammers={eng.ents['aztec']['stats']['hammers']}\n")

print("=== 5. ready_to_build stays in [0, 1] ===")
eng, _ = fresh()
eng.ents["rome"]["stats"]["hammers"] = 10
for i in range(10):
    tick(eng, 1)
    rtb = eng.ents["rome"]["stats"].get("ready_to_build", 0)
    assert 0 <= rtb <= 1, f"ready_to_build out of range at tick {i}: {rtb}"
print("  ready_to_build stayed in [0, 1] across 10 ticks\n")

print("=== 6. attack event does not self-damage the target's faction unit ===")
eng, _ = fresh()
eng.ents["rome"]["stats"]["hammers"] = 10
eng.ents["aztec"]["stats"]["hammers"] = 10
tick(eng, 2)
r = eng.ents["unit_rome_warrior"]
a = eng.ents["unit_aztec_jaguar"]
r["loc"]["pos"]["tile"] = 50
a["loc"]["pos"]["tile"] = 51
r_hp_before = r["stats"]["hp"]
a_hp_before = a["stats"]["hp"]
# Player attacks jaguar: only warrior's attack rule should fire.
eng.dispatch_event("attack", r, {"target": "unit_aztec_jaguar"})
eng._txn = None
# dispatch_event runs immediately (no txn), so damage is applied.
assert a["stats"]["hp"] == a_hp_before - 4, \
    f"jaguar should take 4 from warrior, got {a['stats']['hp']} (was {a_hp_before})"
assert r["stats"]["hp"] == r_hp_before, \
    f"warrior must not be damaged by this event, got {r['stats']['hp']}"
assert a["stats"]["hp"] > a_hp_before - 10, \
    f"jaguar must not self-damage on top of warrior's hit"
print(f"  warrior hp {r_hp_before}->{r['stats']['hp']}, "
      f"jaguar hp {a_hp_before}->{a['stats']['hp']} (-4 only)\n")

print("=== 7. tick combat does not self-damage ===")
eng, _ = fresh()
eng.ents["rome"]["stats"]["hammers"] = 10
eng.ents["aztec"]["stats"]["hammers"] = 10
tick(eng, 2)
r = eng.ents["unit_rome_warrior"]
a = eng.ents["unit_aztec_jaguar"]
r["loc"]["pos"]["tile"] = 50
a["loc"]["pos"]["tile"] = 51
r_hp_before = r["stats"]["hp"]
a_hp_before = a["stats"]["hp"]
tick(eng, 1)
# Both should have taken damage from the other, not from themselves.
assert a["stats"]["hp"] < a_hp_before, "jaguar should take damage from warrior's tick attack"
assert r["stats"]["hp"] < r_hp_before, "warrior should take damage from jaguar's tick attack"
print(f"  warrior {r_hp_before}->{r['stats']['hp']}, jaguar {a_hp_before}->{a['stats']['hp']}\n")

print("=== 8. dead units are removed by their tick rule ===")
eng, _ = fresh()
eng.ents["rome"]["stats"]["hammers"] = 10
tick(eng, 2)
r = eng.ents["unit_rome_warrior"]
r["stats"]["hp"] = 0
tick(eng, 1)
assert "unit_rome_warrior" not in eng.ents, "0-HP unit must be destroyed by tick rule"
print("  dead warrior removed\n")

print("PASS")
