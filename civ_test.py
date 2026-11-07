import json
import os
from engine import Engine
from input_bridge import InputBridge

HERE = os.path.dirname(os.path.abspath(__file__))
GAME_DIR = os.path.join(HERE, "games", "dungeon_crawler")

save_path = os.path.join(GAME_DIR, "save.sqlite")
if os.path.exists(save_path):
    os.remove(save_path)

engine = Engine(os.path.join(GAME_DIR, "ents.json"), os.path.join(GAME_DIR, "containers.json"))
bridge = InputBridge(engine, source_ids={"rome"}, events={"found_city", "production_complete", "attack", "check_death"})

# 1. Found the city
bridge.input("rome", "rome", "found_city", {})
engine.integrate(1.0)

print(f"{'Turn':<5} | {'Rome Hammers':<13} | {'Aztec Hammers':<14} | {'Roman Units':<12} | {'Aztec Units':<12}")
print("-" * 65)

for turn in range(2, 16):
    engine.integrate(1.0)
    
    rome_h = engine.ents['rome']['stats']['hammers']
    aztec_h = engine.ents['aztec']['stats']['hammers']
    
    rome_units = [e for e in engine.ents.values() if "military" in e.get("tags", []) and "roman" in e.get("tags", [])]
    aztec_units = [e for e in engine.ents.values() if "aztec" in e.get("tags", []) and e["kind"] == "unit"]
    
    print(f"{turn:<5} | {rome_h:<13.1f} | {aztec_h:<14.1f} | {len(rome_units):<12} | {len(aztec_units):<12}")

print("\n--- Battle Log (Final State) ---")
warriors = [e for e in engine.ents.values() if e["id"] == "unit_rome_warrior"]
jaguars = [e for e in engine.ents.values() if e["id"] == "unit_aztec_jaguar"]

if not warriors:
    print("  Roman Warrior: DEAD")
else:
    print(f"  Roman Warrior: ALIVE (HP: {warriors[0]['stats']['hp']})")

if not jaguars:
    print("  Aztec Jaguar: DEAD")
else:
    print(f"  Aztec Jaguar: ALIVE (HP: {jaguars[0]['stats']['hp']})")
