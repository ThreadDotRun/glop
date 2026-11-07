# dev/glop/games/dungeon_crawler/main.py
import sys
import math
import logging
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

LOG_DIR = Path("./logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "engine.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("civ_engine")

import builtins
_original_print = builtins.print
def _log_print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    if msg.strip():
        logger.info(msg)
    _original_print(*args, **kwargs)
builtins.print = _log_print

from flask import Flask, request, jsonify, render_template, send_from_directory
from engine import Engine
from input_bridge import InputBridge, InputRejected
from persistence import GameStore
from shadow import Shadow

SAVE_PATH = HERE / "save.sqlite"

EVENTS = {
    "found_city", "production_complete", "attack", "check_death", "build_phase"
}

BLOCKING_KINDS = {"unit"}


class Session:
    def __init__(self):
        self.store = GameStore(str(SAVE_PATH))
        if self.store.exists():
            logger.info("Loading existing save file...")
            self.engine = Engine.load(self.store)
            self.log = ["[loaded from save]"]
        else:
            logger.info("Starting new game...")
            self.engine = Engine(str(HERE / "ents.json"), str(HERE / "containers.json"))
            self.log = ["[new game]"]

        self.shadow = Shadow(self.engine)
        self.bridge = InputBridge(self.engine, source_ids={"rome", "aztec"}, events=EVENTS)
        self.selected_unit_id = self._first_roman_unit_id()
        logger.info("Session initialized.")

    def _first_roman_unit_id(self):
        for e in self.engine.query(container="plains_1"):
            if e.get("kind") == "unit" and "roman" in e.get("tags", []):
                return e["id"]
        return None

    def _advance_and_narrate(self):
        logger.info("--- ADVANCING TICK ---")
        txn = self.engine.integrate(1.0)
        self.shadow.consume(txn)
        lines = self.shadow.narrative(perspective="rome")
        if lines:
            self.log.extend(lines)
        self.log.append("")

    def _resolve_tile_click(self, target_tile):
        ents = [
            e for e in self.engine.query(container="plains_1")
            if e["loc"]["pos"].get("tile") == target_tile
            and e.get("stats", {}).get("hp", 1) > 0
            and e.get("kind") != "environment"
        ]
        logger.info(f"CLICK tile {target_tile}: {len(ents)} live ent(s)")

        my_units = [e for e in ents
                    if "roman" in e.get("tags", []) and e.get("kind") == "unit"]
        if my_units:
            self.selected_unit_id = my_units[0]["id"]
            self.log.append(f"  [selected] {my_units[0]['name']}")
            return

        enemies = [e for e in ents if "aztec" in e.get("tags", [])]
        if enemies:
            if self.selected_unit_id not in self.engine.ents:
                self.log.append("  [blocked] Select a unit first.")
                return
            self.log.append(f"  [attack] {self.selected_unit_id} -> {enemies[0]['id']}")
            self.bridge.input("rome", self.selected_unit_id, "attack",
                              {"target": enemies[0]["id"]})
            self._advance_and_narrate()
            return

        non_units = [e for e in ents if e.get("kind") not in BLOCKING_KINDS]
        if non_units:
            occ = non_units[0]
            self.log.append(
                f"  [blocked] Tile {target_tile} occupied by {occ['name']} "
                f"({occ.get('kind')})."
            )
            return

        if self.selected_unit_id not in self.engine.ents:
            self.log.append("  [blocked] Select a unit first.")
            return
        self._do_move(target_tile)

    def _do_move(self, target_tile):
        unit = self.engine.ents[self.selected_unit_id]
        current_tile = unit["loc"]["pos"].get("tile", 0)
        mp = unit.get("stats", {}).get("mp", 0.0)
        mp_max = unit.get("stats", {}).get("mp_max", mp)

        if mp > mp_max:
            logger.warning(f"[repair] {unit['id']} mp {mp} > mp_max {mp_max}; clamping.")
            unit["stats"]["mp"] = mp_max
            mp = mp_max

        cx, cy = current_tile % 10, math.floor(current_tile / 10)
        tx, ty = target_tile % 10, math.floor(target_tile / 10)
        dist = abs(cx - tx) + abs(cy - ty)

        if dist == 0:
            self.log.append("  [blocked] Unit is already on that tile.")
            return
        if dist > mp:
            self.log.append(f"  [blocked] {unit['name']} has {mp:.1f} MP, needs {dist}.")
            return

        logger.info(f"move {unit['id']} {current_tile}->{target_tile} (dist {dist})")
        self.engine.execute_action(
            {"type": "modify_stat", "target": self.selected_unit_id,
             "stat": "mp", "value": -dist}, unit)
        self.engine.execute_action(
            {"type": "move_to", "target": self.selected_unit_id,
             "tile": target_tile}, unit)
        self._advance_and_narrate()

    def handle(self, raw):
        raw = raw.strip()
        if not raw:
            return
        logger.info(f"USER INPUT: {raw}")
        parts = raw.split()
        cmd = parts[0].lower()
        self.log.append(f"> {raw}")

        try:
            if cmd == "help":
                self.log.extend([
                    "  wait", "  found", "  move <tile>",
                    "  attack <id>", "  activate <id>", "  save",
                ])
                return
            if cmd == "save":
                self.engine.save(self.store)
                self.log.append("[saved]")
                return
            if cmd == "wait":
                self._advance_and_narrate()
                self.log.append("  [Turn passed]")
                return
            if cmd == "found":
                if "unit_rome_settler" not in self.engine.ents:
                    self.log.append("  [no settler] You have no settler to found a city with.")
                    return
                if "city_rome_city" in self.engine.ents:
                    self.log.append("  [blocked] You have already founded Rome.")
                    return
                logger.info("Dispatching found_city...")
                self.bridge.input("rome", "rome", "found_city", {})
                self._advance_and_narrate()
                return
            if cmd == "select":
                if len(parts) < 2:
                    self.log.append("  usage: select <tile>")
                    return
                try:
                    target_tile = int(parts[1])
                except ValueError:
                    self.log.append("  invalid tile number")
                    return
                self._resolve_tile_click(target_tile)
                return
            if cmd == "activate":
                if len(parts) < 2:
                    return
                unit_id = parts[1]
                if unit_id in self.engine.ents:
                    self.selected_unit_id = unit_id
                    self.log.append(f"  [selected] {unit_id}")
                else:
                    self.log.append(f"  [blocked] No such unit: {unit_id}")
                return
            if cmd == "move":
                if len(parts) < 2:
                    self.log.append("  usage: move <tile>")
                    return
                try:
                    target_tile = int(parts[1])
                except ValueError:
                    self.log.append("  invalid tile number")
                    return
                if self.selected_unit_id not in self.engine.ents:
                    self.log.append("  [blocked] Selected unit is dead or missing.")
                    return
                occupants = [
                    e for e in self.engine.query(container="plains_1")
                    if e["loc"]["pos"].get("tile") == target_tile
                    and e["id"] != self.selected_unit_id
                    and e.get("stats", {}).get("hp", 1) > 0
                    and e.get("kind") != "environment"
                ]
                if occupants:
                    self.log.append(f"  [blocked] Tile {target_tile} occupied by {occupants[0]['name']}.")
                    return
                self._do_move(target_tile)
                return
            if cmd == "attack":
                if len(parts) < 2:
                    self.log.append("  usage: attack <target_id>")
                    return
                target_id = parts[1]
                if self.selected_unit_id not in self.engine.ents:
                    self.log.append("  [blocked] Selected unit is dead or missing.")
                    return
                if target_id not in self.engine.ents:
                    self.log.append(f"  [blocked] No such target: {target_id}")
                    return
                self.bridge.input("rome", self.selected_unit_id, "attack", {"target": target_id})
                self._advance_and_narrate()
                return
            self.log.append(f"  unknown command: {cmd}")
        except InputRejected as e:
            self.log.append(f"  [rejected] {e}")
        except Exception as e:
            self.log.append(f"  [error] {e}")
            logger.exception(f"Exception handling command {cmd}: {e}")


app = Flask(__name__, template_folder=str(HERE / "templates"))
_session = None


def session():
    global _session
    if _session is None:
        _session = Session()
    return _session


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/command", methods=["POST"])
def command():
    s = session()
    raw = (request.get_json(force=True) or {}).get("cmd", "")
    logger.info(f"API Request /command: {raw}")
    s.handle(raw)
    return jsonify({"log": s.log[-80:], "state": _state_payload(s)})


@app.route("/api/click", methods=["POST"])
def click():
    s = session()
    body = request.get_json(force=True) or {}
    try:
        tile = int(body.get("tile"))
    except (TypeError, ValueError):
        return jsonify({"error": "tile must be an integer"}), 400
    logger.info(f"API Request /click: tile={tile}")
    s.log.append(f"> click {tile}")
    s._resolve_tile_click(tile)
    return jsonify({"log": s.log[-80:], "state": _state_payload(s)})


@app.route("/api/state")
def state():
    s = session()
    return jsonify({"log": s.log[-80:], "state": _state_payload(s)})


@app.route("/style.css")
def serve_css():
    return send_from_directory(str(HERE / "templates"), "style.css")


@app.route("/2drender.js")
def serve_renderer():
    return send_from_directory(str(HERE / "templates"), "2drender.js")


def _state_payload(s):
    engine = s.engine
    plains = engine.containers.get("plains_1", {})
    units = []

    for e in engine.query(container="plains_1"):
        kind = e.get("kind", "?")
        if kind == "environment":
            continue
        stats = e.get("stats", {})
        units.append({
            "id": e["id"],
            "name": e.get("name", e["id"]),
            "kind": kind,
            "tags": e.get("tags", []),
            "hp": stats.get("hp"),
            "hp_max": stats.get("hp_max"),
            "mp": stats.get("mp"),
            "mp_max": stats.get("mp_max"),
            "pos": e.get("loc", {}).get("pos"),
        })

    rome = engine.ents.get("rome", {})
    stats = rome.get("stats", {})

    active = s.selected_unit_id
    if active is not None and active not in engine.ents:
        active = s._first_roman_unit_id()
        s.selected_unit_id = active

    return {
        "alive": True,
        "world_time": engine.world_time,
        "container_id": "plains_1",
        "container_name": plains.get("name", "Great Plains"),
        "container_desc": plains.get("meta", {}).get("description", ""),
        "container_bounds": plains.get("bounds", {"min": [0, 0], "max": [10, 10]}),
        "hammers": stats.get("hammers", 0),
        "beakers": stats.get("beakers", 0),
        "gold": stats.get("gold", 0),
        "food": stats.get("food", 0),
        "ready_to_build": stats.get("ready_to_build", 0.0),
        "active_unit": active,
        "entities": units,
    }


if __name__ == "__main__":
    logger.info("=== CIV ENGINE SERVER STARTING ===")
    app.run(host="127.0.0.1", port=5000, debug=False)
