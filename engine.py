# dev/glop/engine.py
import json
import math
import ast
import heapq
import operator
import copy
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ============================================================ Constants
MAX_EMIT_DEPTH = 8
MAX_SCHED_EVENTS_PER_TICK = 1000


class EmitDepthExceeded(Exception):
    """Raised when an emit chain exceeds MAX_EMIT_DEPTH."""


class SchedulerOverflow(Exception):
    """Raised when a single integrate() drains more than MAX_SCHED_EVENTS_PER_TICK."""


class InvalidAction(Exception):
    """Raised when an action fails stage-time validation."""


# ============================================================ PendingAction
@dataclass(frozen=True)
class PendingAction:
    priority: int
    seq: int
    source_id: Optional[str]
    action: Dict[str, Any]
    context: Dict[str, Any] = field(default_factory=dict)


# ============================================================ Transaction
class Transaction:
    def __init__(self, engine, dt):
        self.engine = engine
        self.dt = dt
        self.pending: List[PendingAction] = []
        self.seq = 0
        self.journal: List[Tuple[str, Any, Any]] = []
        self.world_time_target = engine.world_time + dt

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def queue(self, action, source_id, context=None, priority=50):
        self.pending.append(
            PendingAction(priority, self.next_seq(), source_id, action, context or {})
        )

    def set_stat(self, ent_id, stat, value):
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        old = ent["stats"].get(stat)
        self.journal.append(("stat", (ent_id, stat), old))
        ent["stats"][stat] = value

    def set_rate(self, ent_id, stat, value):
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        old = ent["rates"].get(stat)
        self.journal.append(("rate", (ent_id, stat), old))
        ent["rates"][stat] = value

    def set_pos(self, ent_id, axis, value):
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        old = ent["loc"]["pos"].get(axis)
        self.journal.append(("pos", (ent_id, axis), old))
        ent["loc"]["pos"][axis] = value

    def set_container(self, ent_id, container_id):
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        old = ent["loc"].get("container")
        if old == container_id:
            return
        self.journal.append(("container", (ent_id, old), None))
        ent["loc"]["container"] = container_id
        self.engine._move_container_index(ent_id, old, container_id)

    def insert_ent(self, ent_id, ent):
        self.journal.append(("insert", ent_id, None))
        self.engine._insert_ent_raw(ent_id, ent)

    def delete_ent(self, ent_id):
        if ent_id in self.engine.ents:
            self.journal.append(("delete", ent_id, copy.deepcopy(self.engine.ents[ent_id])))
            self.engine._remove_ent_raw(ent_id)

    def set_world_time(self, value):
        self.journal.append(("world_time", None, self.engine.world_time))
        self.engine.world_time = value

    def push_scheduler(self, entry):
        self.journal.append(("sched_push", entry, None))
        heapq.heappush(self.engine.scheduler, entry)

    def pop_scheduler(self):
        if not self.engine.scheduler:
            return None
        entry = heapq.heappop(self.engine.scheduler)
        self.journal.append(("sched_pop", None, entry))
        return entry

    def rollback(self):
        had_changes = any(kind != "world_time" for kind, _, _ in self.journal) or bool(self.pending)

        for kind, path, old in reversed(self.journal):
            if kind == "stat":
                ent_id, stat = path
                ent = self.engine.ents.get(ent_id)
                if ent is not None:
                    if old is None:
                        ent["stats"].pop(stat, None)
                    else:
                        ent["stats"][stat] = old
            elif kind == "rate":
                ent_id, stat = path
                ent = self.engine.ents.get(ent_id)
                if ent is not None:
                    if old is None:
                        ent["rates"].pop(stat, None)
                    else:
                        ent["rates"][stat] = old
            elif kind == "pos":
                ent_id, axis = path
                ent = self.engine.ents.get(ent_id)
                if ent is not None:
                    if old is None:
                        ent["loc"]["pos"].pop(axis, None)
                    else:
                        ent["loc"]["pos"][axis] = old
            elif kind == "container":
                ent_id, old_container = path
                ent = self.engine.ents.get(ent_id)
                if ent is not None:
                    current = ent["loc"].get("container")
                    ent["loc"]["container"] = old_container
                    self.engine._move_container_index(ent_id, current, old_container)
            elif kind == "insert":
                if path in self.engine.ents:
                    self.engine._remove_ent_raw(path)
            elif kind == "delete":
                self.engine._insert_ent_raw(path, old)
            elif kind == "world_time":
                self.engine.world_time = old
            elif kind == "sched_push":
                try:
                    self.engine.scheduler.remove(path)
                    heapq.heapify(self.engine.scheduler)
                except ValueError:
                    pass
            elif kind == "sched_pop":
                heapq.heappush(self.engine.scheduler, old)
        self.journal.clear()
        self.pending.clear()
        return had_changes

    def commit(self):
        self.pending.sort(key=lambda p: (p.priority, p.seq))
        for pa in self.pending:
            src = self.engine.ents.get(pa.source_id) if pa.source_id else None
            self.engine._apply_action(pa.action, src, pa.context, self)
        self.pending.clear()


# ============================================================ Engine
class Engine:
    """
    Deterministic, transactional simulation kernel.
    """
    def __init__(self, ents_path, containers_path):
        self.ents: Dict[str, dict] = {}
        self.containers: Dict[str, dict] = {}
        self.world_time = 0.0
        self.scheduler: List[Tuple[float, int, str, str, dict]] = []
        self._sched_seq = 0
        self._input_queue: List[dict] = []
        self._rules_by_trigger: Dict[str, List[str]] = {}
        self._ents_by_container: Dict[str, Set[str]] = {}
        self._load(ents_path, containers_path)

        self.operators = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Lt: operator.lt, ast.Gt: operator.gt,
            ast.LtE: operator.le, ast.GtE: operator.ge,
            ast.Eq: operator.eq, ast.NotEq: operator.ne,
            ast.And: lambda a, b: a and b, ast.Or: lambda a, b: a or b,
        }

    def _load(self, ents_path, containers_path):
        ents_data = json.loads(Path(ents_path).read_text())
        for e in ents_data.get("ents", []):
            e.setdefault("t", 0.0)
            e.setdefault("rates", {})
            e.setdefault("schedule", [])
            e.setdefault("stats", {})
            e.setdefault("status", [])
            self.ents[e["id"]] = e
            self._index_ent(e["id"])
            self._index_container(e["id"])
        cont_data = json.loads(Path(containers_path).read_text())
        for c in cont_data.get("containers", []):
            self.containers[c["id"]] = c

    # ---- internal index maintenance: rules ----
    def _index_ent(self, ent_id):
        ent = self.ents.get(ent_id)
        if not ent:
            return
        for rule in ent.get("rules", []):
            trig = rule.get("trigger")
            if trig is None:
                continue
            bucket = self._rules_by_trigger.setdefault(trig, [])
            if ent_id not in bucket:
                bucket.append(ent_id)

    def _deindex_ent(self, ent_id):
        for bucket in self._rules_by_trigger.values():
            try:
                bucket.remove(ent_id)
            except ValueError:
                pass
        empty = [k for k, v in self._rules_by_trigger.items() if not v]
        for k in empty:
            del self._rules_by_trigger[k]

    def _rebuild_index(self):
        self._rules_by_trigger = {}
        for ent_id in self.ents:
            self._index_ent(ent_id)

    # ---- internal index maintenance: containers ----
    def _index_container(self, ent_id):
        ent = self.ents.get(ent_id)
        if not ent:
            return
        container = ent.get("loc", {}).get("container")
        if container is None:
            return
        self._ents_by_container.setdefault(container, set()).add(ent_id)

    def _deindex_container(self, ent_id):
        for bucket in self._ents_by_container.values():
            bucket.discard(ent_id)
        empty = [k for k, v in self._ents_by_container.items() if not v]
        for k in empty:
            del self._ents_by_container[k]

    def _move_container_index(self, ent_id, old, new):
        if old is not None:
            bucket = self._ents_by_container.get(old)
            if bucket is not None:
                bucket.discard(ent_id)
                if not bucket:
                    del self._ents_by_container[old]
        if new is not None:
            self._ents_by_container.setdefault(new, set()).add(ent_id)

    def _rebuild_container_index(self):
        self._ents_by_container = {}
        for ent_id in self.ents:
            self._index_container(ent_id)

    def _insert_ent_raw(self, ent_id, ent):
        self.ents[ent_id] = ent
        self._index_ent(ent_id)
        self._index_container(ent_id)

    def _remove_ent_raw(self, ent_id):
        if ent_id in self.ents:
            del self.ents[ent_id]
        self._deindex_ent(ent_id)
        self._deindex_container(ent_id)

    # ---- public ent mutation API ----
    def add_ent(self, ent):
        ent_id = ent.get("id")
        if not ent_id:
            raise ValueError("ent missing id")
        if ent_id in self.ents:
            raise ValueError(f"ent {ent_id!r} already exists")
        ent.setdefault("t", 0.0)
        ent.setdefault("rates", {})
        ent.setdefault("schedule", [])
        ent.setdefault("stats", {})
        ent.setdefault("status", [])
        self._insert_ent_raw(ent_id, ent)

    def remove_ent(self, ent_id):
        self._remove_ent_raw(ent_id)

    def move_ent_to_container(self, ent_id, container_id):
        ent = self.ents.get(ent_id)
        if ent is None:
            raise KeyError(f"ent {ent_id!r} not found")
        old = ent["loc"].get("container")
        ent["loc"]["container"] = container_id
        self._move_container_index(ent_id, old, container_id)

    def add_rule(self, ent_id, rule):
        ent = self.ents.get(ent_id)
        if ent is None:
            raise KeyError(f"ent {ent_id!r} not found")
        ent.setdefault("rules", []).append(rule)
        trig = rule.get("trigger")
        if trig is not None:
            bucket = self._rules_by_trigger.setdefault(trig, [])
            if ent_id not in bucket:
                bucket.append(ent_id)

    def remove_rule(self, ent_id, rule):
        ent = self.ents.get(ent_id)
        if ent is None:
            return
        rules = ent.get("rules", [])
        try:
            rules.remove(rule)
        except ValueError:
            return
        trig = rule.get("trigger")
        if trig is not None:
            still_used = any(r.get("trigger") == trig for r in rules)
            if not still_used:
                bucket = self._rules_by_trigger.get(trig, [])
                try:
                    bucket.remove(ent_id)
                except ValueError:
                    pass
                if not bucket:
                    self._rules_by_trigger.pop(trig, None)

    # ---- queries ----
    def query(self, **kwargs):
        out = list(self.ents.values())
        for k, v in kwargs.items():
            if k == "container":
                ids = self._ents_by_container.get(v, ())
                out = [self.ents[i] for i in ids if i in self.ents]
            elif k == "kind":
                out = [e for e in out if e.get("kind") == v]
            elif k == "tag":
                out = [e for e in out if v in e.get("tags", [])]
        return out

    def get_distance(self, e1, e2):
        p1 = e1["loc"]["pos"]
        p2 = e2["loc"]["pos"]
        vals1 = [float(v) for v in p1.values() if isinstance(v, (int, float))]
        vals2 = [float(v) for v in p2.values() if isinstance(v, (int, float))]
        if len(vals1) != len(vals2):
            return float('inf')
        return math.dist(vals1, vals2)

    def eval_ast(self, node, source_ent, context):
        if isinstance(node, ast.Expression):
            return self.eval_ast(node.body, source_ent, context)
        elif isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.Name):
            if node.id == "true": return True
            if node.id == "false": return False
            if node.id == "self":
                return source_ent["id"] if source_ent else None
            if node.id in context: return context[node.id]
            if node.id in self.ents: return self.ents[node.id]
            return source_ent.get("stats", {}).get(node.id, 0.0)
        elif isinstance(node, ast.Attribute):
            parts = []
            curr = node
            while isinstance(curr, ast.Attribute):
                parts.append(curr.attr); curr = curr.value
            if isinstance(curr, ast.Name):
                parts.append(curr.id)
            parts.reverse()

            if parts[0] == "self":
                val = source_ent
                start_idx = 1
            elif parts[0] in context:
                val = self.ents.get(context[parts[0]], context[parts[0]])
                start_idx = 1
            elif parts[0] in self.ents:
                val = self.ents[parts[0]]
                start_idx = 1
            else:
                val = source_ent
                start_idx = 0

            for p in parts[start_idx:]:
                if isinstance(val, dict): val = val.get(p, 0.0)
                else: return 0.0
            return float(val) if isinstance(val, (int, float)) else val
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "distance":
                target_id = node.args[0].id
                target = self.ents.get(target_id)
                if target: return self.get_distance(source_ent, target)
            return float('inf')
        elif isinstance(node, ast.BinOp):
            left = self.eval_ast(node.left, source_ent, context)
            right = self.eval_ast(node.right, source_ent, context)
            op_type = type(node.op)
            if op_type in self.operators:
                return self.operators[op_type](left, right)
            return 0.0
        elif isinstance(node, ast.Compare):
            left = self.eval_ast(node.left, source_ent, context)
            for op, comparator in zip(node.ops, node.comparators):
                right = self.eval_ast(comparator, source_ent, context)
                op_type = type(op)
                if op_type not in self.operators: return False
                if not self.operators[op_type](left, right): return False
                left = right
            return True
        elif isinstance(node, ast.BoolOp):
            if isinstance(node.op, ast.And):
                return all(self.eval_ast(v, source_ent, context) for v in node.values)
            elif isinstance(node.op, ast.Or):
                return any(self.eval_ast(v, source_ent, context) for v in node.values)
            return False
        return False

    def evaluate_condition(self, rule_cond, source_ent, event_context=None):
        context = event_context or {}
        if "target" in rule_cond and "target" not in context:
            hostile = next((e for e in self.ents.values() if "hostile" in e.get("tags", [])), None)
            if hostile:
                context["target"] = hostile["id"]
                rule_cond = rule_cond.replace("target.", f"{hostile['id']}.")
        try:
            tree = ast.parse(rule_cond, mode='eval')
            return bool(self.eval_ast(tree, source_ent, context))
        except Exception as e:
            print(f"  [WARN] Condition eval failed: '{rule_cond}' -> {e}")
            return False

    # ============================================================ validation
    _REQUIRED_FIELDS = {
        "modify_stat": ["stat", "value"],
        "move_toward":  ["target"],
        "apply_status": ["status_id"],
        "add_rate":     ["rate"],
        "schedule":     ["in", "event"],
        "destroy":      [],
        "emit":         ["event"],
        "spawn":        ["ent"],
        "move_container": ["container"],
        "move_to":      ["tile"], # NEW ACTION
    }

    _NUMERIC_FIELDS = {
        "modify_stat": ["value"],
        "move_toward":  ["speed"],
        "apply_status": ["duration"],
        "schedule":     ["in"],
        "move_to":      ["tile"], # NEW ACTION
    }

    def _validate_action(self, action):
        if not isinstance(action, dict):
            raise InvalidAction(f"action is not a dict: {action!r}")
        a_type = action.get("type")
        if a_type not in self._REQUIRED_FIELDS:
            raise InvalidAction(f"unknown action type: {a_type!r}")
        for required in self._REQUIRED_FIELDS[a_type]:
            if required not in action:
                raise InvalidAction(f"{a_type} missing required field {required!r}: {action!r}")
        for field_name in self._NUMERIC_FIELDS.get(a_type, []):
            if field_name in action:
                try:
                    float(action[field_name])
                except (TypeError, ValueError):
                    raise InvalidAction(
                        f"{a_type}.{field_name} not numeric: {action[field_name]!r}"
                    )
        if a_type == "add_rate":
            if not isinstance(action["rate"], dict):
                raise InvalidAction(f"add_rate.rate must be a dict: {action['rate']!r}")
            for k, v in action["rate"].items():
                try:
                    float(v)
                except (TypeError, ValueError):
                    raise InvalidAction(f"add_rate.rate[{k!r}] not numeric: {v!r}")
        if a_type == "apply_status" and "rate" in action:
            if not isinstance(action["rate"], dict):
                raise InvalidAction(f"apply_status.rate must be a dict: {action['rate']!r}")
            for k, v in action["rate"].items():
                try:
                    float(v)
                except (TypeError, ValueError):
                    raise InvalidAction(f"apply_status.rate[{k!r}] not numeric: {v!r}")
        if a_type == "spawn":
            if not isinstance(action["ent"], dict):
                raise InvalidAction(f"spawn.ent must be a dict: {action['ent']!r}")
            if "id" not in action["ent"]:
                raise InvalidAction(f"spawn.ent missing id: {action['ent']!r}")

    def _stage(self, action, source_id, context=None, priority=50):
        self._validate_action(action)
        txn = getattr(self, "_txn", None)
        if txn is None:
            src = self.ents.get(source_id) if source_id else None
            self._apply_action(action, src, context or {}, None)
        else:
            txn.queue(action, source_id, context, priority)

    # ============================================================ apply
    def _apply_action(self, action, source_ent, context, txn):
        a_type = action.get("type")
        context = context or {}
        depth = int(context.get("_depth", 0))

        def resolve_id(id_str):
            if id_str == "target" and "target" in context:
                return context["target"]
            if id_str == "self":
                return source_ent["id"] if source_ent else None
            return id_str

        def write_stat(ent_id, stat, value):
            if txn is not None: txn.set_stat(ent_id, stat, value)
            else: self.ents[ent_id]["stats"][stat] = value

        def write_rate(ent_id, stat, value):
            if txn is not None: txn.set_rate(ent_id, stat, value)
            else: self.ents[ent_id]["rates"][stat] = value

        def write_pos(ent_id, axis, value):
            if txn is not None: txn.set_pos(ent_id, axis, value)
            else: self.ents[ent_id]["loc"]["pos"][axis] = value

        if a_type == "modify_stat":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            target = self.ents.get(target_id)
            if target:
                stat = action["stat"]
                delta = float(action["value"])
                current = target["stats"].get(stat, 0)
                write_stat(target_id, stat, current + delta)
                print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} modified {target['name']} {stat} by {delta} (now {current + delta})")

        elif a_type == "move_toward":
            target_id = resolve_id(action.get("target"))
            target = self.ents.get(target_id)
            if target and source_ent:
                sp = source_ent["loc"]["pos"]; tp = target["loc"]["pos"]
                speed = float(action.get("speed", 1))
                if "x" in sp and "x" in tp:
                    new_x = sp["x"]
                    if new_x < tp["x"]: new_x = min(new_x + speed, tp["x"])
                    elif new_x > tp["x"]: new_x = max(new_x - speed, tp["x"])
                    if new_x != sp["x"]:
                        write_pos(source_ent["id"], "x", new_x)
                print(f"  [ACTION] {source_ent['name']} moved toward {target['name']} (now at x={self.ents[source_ent['id']]['loc']['pos']['x']})")

        elif a_type == "move_to":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            target = self.ents.get(target_id)
            if target:
                new_tile = int(action["tile"])
                if target["loc"]["pos"].get("tile") != new_tile:
                    write_pos(target_id, "tile", new_tile)
                    print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} moved {target['name']} to tile {new_tile}")

        elif a_type == "apply_status":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            target = self.ents.get(target_id)
            if target:
                effect = {
                    "id": action["status_id"],
                    "duration": float(action.get("duration", 5.0)),
                    "source": source_ent["id"] if source_ent else None,
                }
                if "rate" in action:
                    effect["rate"] = action["rate"]
                    for stat, delta in action["rate"].items():
                        write_rate(target_id, stat, target["rates"].get(stat, 0.0) + float(delta))
                target.setdefault("status", []).append(effect)
                print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} applied {action['status_id']} to {target['name']}")

        elif a_type == "add_rate":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            target = self.ents.get(target_id)
            if target:
                for stat, delta in action["rate"].items():
                    write_rate(target_id, stat, target["rates"].get(stat, 0.0) + float(delta))
                print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} modified {target['name']} rates by {action['rate']}")

        elif a_type == "schedule":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            at = self.world_time + float(action["in"])
            self._sched_seq += 1
            entry = (at, self._sched_seq, action["event"], target_id, action.get("context", {}))
            if txn is not None: txn.push_scheduler(entry)
            else: heapq.heappush(self.scheduler, entry)
            print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} scheduled '{action['event']}' for {target_id} at t={at:.2f}")

        elif a_type == "destroy":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            if target_id in self.ents:
                print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} destroyed {self.ents[target_id]['name']}")
                if txn is not None: txn.delete_ent(target_id)
                else: self._remove_ent_raw(target_id)

        elif a_type == "spawn":
            new_ent = copy.deepcopy(action["ent"])
            new_id = new_ent.get("id")
            if not new_id:
                raise InvalidAction(f"spawn action produced ent without id: {new_ent!r}")
            if new_id in self.ents:
                print(f"  [ACTION] spawn skipped, {new_id!r} already exists")
                return
            new_ent.setdefault("t", 0.0)
            new_ent.setdefault("rates", {})
            new_ent.setdefault("schedule", [])
            new_ent.setdefault("stats", {})
            new_ent.setdefault("status", [])
            if txn is not None: txn.insert_ent(new_id, new_ent)
            else: self._insert_ent_raw(new_id, new_ent)
            print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} spawned {new_id}")

        elif a_type == "move_container":
            target_id = resolve_id(action.get("target", source_ent["id"] if source_ent else None))
            if target_id in self.ents:
                new_container = action["container"]
                if txn is not None: txn.set_container(target_id, new_container)
                else:
                    old = self.ents[target_id]["loc"].get("container")
                    self.ents[target_id]["loc"]["container"] = new_container
                    self._move_container_index(target_id, old, new_container)
                print(f"  [ACTION] {source_ent['name'] if source_ent else 'engine'} moved {target_id} to container {new_container}")

        elif a_type == "emit":
            new_ctx = dict(context)
            new_ctx["_depth"] = depth + 1
            if new_ctx["_depth"] > MAX_EMIT_DEPTH:
                raise EmitDepthExceeded(
                    f"emit chain exceeded {MAX_EMIT_DEPTH} levels "
                    f"(source={source_ent['id'] if source_ent else 'engine'}, event={action['event']!r})"
                )
            self._dispatch_event_staged(action["event"], source_ent["id"] if source_ent else None, new_ctx)

    def dispatch_event(self, event_name, source_ent, context=None):
        self._dispatch_event_staged(event_name, source_ent["id"] if source_ent else None, context)

    def _dispatch_event_staged(self, event_name, source_id, context=None):
        context = context or {}
        for ent_id in list(self._rules_by_trigger.get(event_name, ())):
            e = self.ents.get(ent_id)
            if e is None:
                continue
            for rule in e.get("rules", []):
                if rule["trigger"] == event_name:
                    if self.evaluate_condition(rule["condition"], e, context):
                        self._stage(rule["action"], e["id"], context)

    # ============================================================ integrate
    def integrate(self, dt):
        if dt <= 0:
            return None
        txn = Transaction(self, dt)
        self._txn = txn
        try:
            for e in list(self.ents.values()):
                for stat, rate in e.get("rates", {}).items():
                    if rate != 0:
                        txn.set_stat(e["id"], stat, e["stats"].get(stat, 0.0) + rate * dt)

            txn.set_world_time(txn.world_time_target)

            drained = 0
            while self.scheduler and self.scheduler[0][0] <= self.world_time:
                drained += 1
                if drained > MAX_SCHED_EVENTS_PER_TICK:
                    raise SchedulerOverflow(
                        f"scheduled event drain exceeded {MAX_SCHED_EVENTS_PER_TICK} in one tick"
                    )
                entry = txn.pop_scheduler()
                _, _, event, target_id, ctx = entry
                if target_id in self.ents:
                    self._dispatch_event_staged(event, target_id, ctx)

            for e in list(self.ents.values()):
                remaining = []
                for status in e.get("status", []):
                    status["duration"] -= dt
                    if status["duration"] > 0:
                        remaining.append(status)
                    else:
                        for stat, delta in status.get("rate", {}).items():
                            txn.set_rate(e["id"], stat, e["rates"].get(stat, 0.0) - float(delta))
                        print(f"  [STATUS] {e['name']} lost {status['id']}")
                e["status"] = remaining

            self._dispatch_event_staged("tick", None, {"dt": dt})

            if self._input_queue:
                for inp in self._input_queue:
                    self._dispatch_event_staged(
                        inp["event"],
                        inp["source"],
                        {"source": inp["source"], "actor": inp["actor"], **inp["data"]},
                    )
                self._input_queue.clear()

            txn.commit()
            return txn
        except Exception as e:
            had = txn.rollback()
            if had:
                print(f"  [ROLLBACK] tick failed: {e}")
            else:
                print(f"  [ABORT] tick failed before any mutation: {e}")
            raise
        finally:
            self._txn = None

    def execute_action(self, action, source_ent, event_context=None):
        self._apply_action(action, source_ent, event_context or {}, None)

    def save(self, store):
        store.save(self)

    @classmethod
    def load(cls, store, ents_path=None, containers_path=None):
        engine = cls.__new__(cls)
        engine.ents = {}
        engine.containers = {}
        engine.world_time = 0.0
        engine.scheduler = []
        engine._sched_seq = 0
        engine._input_queue = []
        engine._rules_by_trigger = {}
        engine._ents_by_container = {}
        engine._txn = None
        import ast, operator
        engine.operators = {
            ast.Add: operator.add, ast.Sub: operator.sub,
            ast.Mult: operator.mul, ast.Div: operator.truediv,
            ast.Lt: operator.lt, ast.Gt: operator.gt,
            ast.LtE: operator.le, ast.GtE: operator.ge,
            ast.Eq: operator.eq, ast.NotEq: operator.ne,
            ast.And: lambda a, b: a and b, ast.Or: lambda a, b: a or b,
        }
        store.load(engine)
        engine._rebuild_index()
        engine._rebuild_container_index()
        return engine
