import json, sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("pip install jsonschema"); sys.exit(1)

HERE = Path(__file__).parent
ENTS = json.loads((HERE / "ents.json").read_text())
SCHEMA = json.loads((HERE / "ents.schema.json").read_text())

def api_ents(store, **q):
    out = store["ents"]
    for k, v in q.items():
        if k == "container":
            out = [e for e in out if e.get("loc", {}).get("container") == v]
        elif k == "kind":
            out = [e for e in out if e.get("kind") == v]
        elif k == "subtype":
            out = [e for e in out if e.get("subtype") == v]
        elif k == "tag":
            out = [e for e in out if v in e.get("tags", [])]
        elif k == "id":
            out = [e for e in out if e.get("id") == v]
    return out

def check(label, cond):
    print(("  ok  " if cond else " FAIL ") + label)
    return cond

ok = True
print("schema validation:")
try:
    jsonschema.validate(ENTS, SCHEMA); print("  ok  validates against schema")
except jsonschema.ValidationError as e:
    print(f" FAIL  {e.message}"); ok = False

print("\napi queries:")
ok &= check("all ents -> 3",                    len(api_ents(ENTS)) == 3)
ok &= check("container=entrance_hall -> 3",     len(api_ents(ENTS, container="entrance_hall")) == 3)
ok &= check("container=nope -> 0",              len(api_ents(ENTS, container="nope")) == 0)
ok &= check("kind=player -> 1",                 len(api_ents(ENTS, kind="player")) == 1)
ok &= check("kind=npc -> 1",                    len(api_ents(ENTS, kind="npc")) == 1)
ok &= check("subtype=enemy -> 1",               len(api_ents(ENTS, subtype="enemy")) == 1)
ok &= check("tag=hostile -> 1",                 len(api_ents(ENTS, tag="hostile")) == 1)
ok &= check("id=player_001 -> 1",               len(api_ents(ENTS, id="player_001")) == 1)

print("\ninvariants:")
ids = [e["id"] for e in ENTS["ents"]]
ok &= check("ids unique",                       len(ids) == len(set(ids)))
ok &= check("every ent has loc.container",      all(e.get("loc", {}).get("container") for e in ENTS["ents"]))
ok &= check("every ent has loc.pos dict",       all(isinstance(e.get("loc", {}).get("pos"), dict) for e in ENTS["ents"]))
ok &= check("hp <= hp_max everywhere",          all(e.get("stats", {}).get("hp", 0) <= e.get("stats", {}).get("hp_max", 0) for e in ENTS["ents"]))

print("\nnegative cases (should fail validation):")
def fails(mut):
    doc = json.loads(json.dumps(ENTS)); mut(doc)
    try: jsonschema.validate(doc, SCHEMA); return False
    except jsonschema.ValidationError: return True

ok &= check("missing loc rejected",             fails(lambda d: d["ents"][0].pop("loc")))
ok &= check("unknown ent field rejected",       fails(lambda d: d["ents"][0].__setitem__("bogus", 1)))
ok &= check("root extra key rejected",          fails(lambda d: d.__setitem__("rules", {})))
ok &= check("bad version rejected",             fails(lambda d: d.__setitem__("schema_version", "1.0")))

print("\nmeta escape hatch:")
doc = json.loads(json.dumps(ENTS))
doc["ents"][1]["meta"] = {"xp_reward": 15, "boss_phases": [{"hp_pct": 0.5, "summon": "add_01"}]}
try:
    jsonschema.validate(doc, SCHEMA); print("  ok  arbitrary meta accepted")
except jsonschema.ValidationError as e:
    print(f" FAIL  {e.message}"); ok = False

print("\nPASS" if ok else "\nFAIL")
sys.exit(0 if ok else 1)
