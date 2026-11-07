"""
Shadow interface: the bridge between the simulation engine and any
future 3D renderer.

Design:
  - The engine emits a Transaction per tick. The shadow consumes it and
    translates each journal entry, in order, into a semantic event.
  - No deduplication: if the player takes 5 damage twice in one tick,
    the shadow reports two damage events. Merging them would lose
    information a renderer may want (two separate flash triggers, two
    separate sound cues).
  - Two-tier output: FrameEvent (what happened) and RenderHint (how to
    present it). Plus ActiveEffect (transient effects still playing).
  - narrative(perspective) produces human-readable lines from this
    tick's events, for a text UI.
  - No engine changes. No JSON changes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================ Frame events
EVENT_ENTITY_SPAWNED = "ENTITY_SPAWNED"
EVENT_ENTITY_MOVED = "ENTITY_MOVED"
EVENT_ENTITY_DAMAGED = "ENTITY_DAMAGED"
EVENT_ENTITY_HEALED = "ENTITY_HEALED"
EVENT_ENTITY_DIED = "ENTITY_DIED"
EVENT_STAT_CHANGED = "STAT_CHANGED"
EVENT_STATUS_APPLIED = "STATUS_APPLIED"
EVENT_STATUS_EXPIRED = "STATUS_EXPIRED"
EVENT_CONTAINER_CHANGED = "CONTAINER_CHANGED"
EVENT_SCHEDULER_UPDATED = "SCHEDULER_UPDATED"
EVENT_TICK_ADVANCED = "TICK_ADVANCED"


@dataclass
class FrameEvent:
    kind: str
    ent_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RenderHint:
    ent_id: Optional[str] = None
    kind: str = ""
    duration: float = 0.0
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActiveEffect:
    ent_id: str
    kind: str
    started_at: float
    duration: float
    payload: Dict[str, Any] = field(default_factory=dict)

    def done(self, world_time: float) -> bool:
        return (world_time - self.started_at) >= self.duration


# ============================================================ Shadow
class Shadow:
    def __init__(self, engine):
        self.engine = engine
        self._frame_events: List[FrameEvent] = []
        self._render_hints: List[RenderHint] = []
        self._active_effects: List[ActiveEffect] = []

    def consume(self, txn):
        """Translate a committed Transaction into frame events + render hints."""
        self._frame_events = []
        self._render_hints = []

        if txn is None:
            return

        # Journal is an ordered list of (kind, path, old) tuples. Iterate
        # in order; emit one event per entry. No deduplication — the
        # renderer decides whether two damage events on the same ent
        # should be merged.
        for kind, path, old in txn.journal:
            try:
                if kind == "stat":
                    self._translate_stat(path, old)
                elif kind == "rate":
                    self._translate_rate(path, old)
                elif kind == "pos":
                    self._translate_pos(path, old)
                elif kind == "container":
                    self._translate_container(path, old)
                elif kind == "insert":
                    self._translate_insert(path)
                elif kind == "delete":
                    self._translate_delete(path, old)
                elif kind == "sched_push":
                    # push_scheduler journals ("sched_push", entry, None)
                    if isinstance(path, tuple) and len(path) >= 3:
                        at, seq, event = path[0], path[1], path[2]
                        self._frame_events.append(FrameEvent(
                            kind=EVENT_SCHEDULER_UPDATED,
                            data={"op": "push", "at": at, "event": event},
                        ))
                elif kind == "sched_pop":
                    # pop_scheduler journals ("sched_pop", None, entry)
                    if isinstance(old, tuple) and len(old) >= 3:
                        at, seq, event = old[0], old[1], old[2]
                        self._frame_events.append(FrameEvent(
                            kind=EVENT_SCHEDULER_UPDATED,
                            data={"op": "pop", "at": at, "event": event},
                        ))
            except Exception as e:
                # Never let a projection bug crash the tick — log and continue.
                print(f"  [SHADOW WARN] failed to translate journal entry {kind!r}: {e}")
                continue

        self._frame_events.append(FrameEvent(
            kind=EVENT_TICK_ADVANCED,
            data={"world_time": self.engine.world_time},
        ))

        wt = self.engine.world_time
        self._active_effects = [e for e in self._active_effects if not e.done(wt)]

    # ---- per-journal-kind translators ----
    def _translate_stat(self, path, old):
        ent_id, stat = path
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        new = ent["stats"].get(stat)
        if new == old:
            return
        name = ent.get("name", ent_id)

        if stat == "hp":
            if new is not None and old is not None and new < old:
                dmg = old - new
                self._frame_events.append(FrameEvent(
                    kind=EVENT_ENTITY_DAMAGED,
                    ent_id=ent_id,
                    data={"name": name, "amount": dmg, "hp": new, "hp_old": old},
                ))
                self._render_hints.append(RenderHint(
                    ent_id=ent_id, kind="damage_flash", duration=0.2,
                    payload={"amount": dmg},
                ))
                self._active_effects.append(ActiveEffect(
                    ent_id=ent_id, kind="damage_flash",
                    started_at=self.engine.world_time, duration=0.2,
                    payload={"amount": dmg},
                ))
                self._render_hints.append(RenderHint(
                    ent_id=ent_id, kind="floating_damage_number",
                    duration=0.8, payload={"amount": dmg},
                ))
                self._render_hints.append(RenderHint(
                    ent_id=ent_id, kind="sound_cue",
                    duration=0.0, payload={"cue": "hit"},
                ))
            elif new is not None and old is not None and new > old:
                self._frame_events.append(FrameEvent(
                    kind=EVENT_ENTITY_HEALED,
                    ent_id=ent_id,
                    data={"name": name, "amount": new - old, "hp": new, "hp_old": old},
                ))
        else:
            self._frame_events.append(FrameEvent(
                kind=EVENT_STAT_CHANGED,
                ent_id=ent_id,
                data={"name": name, "stat": stat, "old": old, "new": new},
            ))

    def _translate_rate(self, path, old):
        ent_id, stat = path
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        new = ent["rates"].get(stat)
        if new == old:
            return
        if (old or 0) == 0 and (new or 0) != 0:
            self._frame_events.append(FrameEvent(
                kind=EVENT_STATUS_APPLIED,
                ent_id=ent_id,
                data={"name": ent.get("name", ent_id), "stat": stat, "rate": new},
            ))
        elif (old or 0) != 0 and (new or 0) == 0:
            self._frame_events.append(FrameEvent(
                kind=EVENT_STATUS_EXPIRED,
                ent_id=ent_id,
                data={"name": ent.get("name", ent_id), "stat": stat},
            ))

    def _translate_pos(self, path, old):
        ent_id, axis = path
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        new = ent["loc"]["pos"].get(axis)
        if new == old:
            return
        self._frame_events.append(FrameEvent(
            kind=EVENT_ENTITY_MOVED,
            ent_id=ent_id,
            data={"name": ent.get("name", ent_id), "axis": axis, "old": old, "new": new},
        ))

    def _translate_container(self, path, old):
        ent_id, old_container = path
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        new_container = ent["loc"]["container"]
        if new_container == old_container:
            return
        self._frame_events.append(FrameEvent(
            kind=EVENT_CONTAINER_CHANGED,
            ent_id=ent_id,
            data={"name": ent.get("name", ent_id), "from": old_container, "to": new_container},
        ))

    def _translate_insert(self, ent_id):
        ent = self.engine.ents.get(ent_id)
        if ent is None:
            return
        self._frame_events.append(FrameEvent(
            kind=EVENT_ENTITY_SPAWNED,
            ent_id=ent_id,
            data={
                "name": ent.get("name", ent_id),
                "kind": ent.get("kind"),
                "container": ent.get("loc", {}).get("container"),
                "pos": ent.get("loc", {}).get("pos"),
            },
        ))
        self._render_hints.append(RenderHint(
            ent_id=ent_id, kind="spawn_effect", duration=0.5,
            payload={"pos": ent.get("loc", {}).get("pos")},
        ))

    def _translate_delete(self, ent_id, old):
        if isinstance(old, dict):
            container = old.get("loc", {}).get("container")
            name = old.get("name", ent_id)
            hp = old.get("stats", {}).get("hp")
            hp_max = old.get("stats", {}).get("hp_max")
        else:
            container = None
            name = ent_id
            hp = None
            hp_max = None
        self._frame_events.append(FrameEvent(
            kind=EVENT_ENTITY_DIED,
            ent_id=ent_id,
            data={"name": name, "container": container, "hp": hp, "hp_max": hp_max},
        ))
        self._render_hints.append(RenderHint(
            ent_id=ent_id, kind="death_effect", duration=0.6,
            payload={"container": container},
        ))
        self._render_hints.append(RenderHint(
            ent_id=ent_id, kind="sound_cue",
            duration=0.0, payload={"cue": "death"},
        ))

    # ---- public API ----
    def frame_events(self) -> List[FrameEvent]:
        return list(self._frame_events)

    def render_hints(self) -> List[RenderHint]:
        return list(self._render_hints)

    def entities(self) -> Dict[str, dict]:
        return self.engine.ents

    def effects(self) -> List[ActiveEffect]:
        return list(self._active_effects)

    def camera_hint(self) -> Dict[str, Any]:
        player = self.engine.ents.get("player_001")
        if player is None:
            return {}
        return {
            "follow": "player_001",
            "container": player["loc"]["container"],
            "pos": player["loc"]["pos"],
            "world_time": self.engine.world_time,
        }

    # ---- narrative: human-readable lines for a text UI ----
    def narrative(self, perspective: str = "player_001") -> List[str]:
        """
        Turn this tick's frame events into human-readable lines, filtered
        for readability. Movement and scheduler churn are silent; combat,
        death, gold, and status changes are narrated.
        """
        lines = []
        for ev in self._frame_events:
            k = ev.kind
            d = ev.data

            if k == EVENT_ENTITY_DAMAGED:
                who = d.get("name", ev.ent_id)
                amount = d.get("amount")
                if ev.ent_id == perspective:
                    lines.append(f"  You take {amount} damage. ({d.get('hp')} hp left)")
                else:
                    lines.append(f"  You hit {who} for {amount} damage. ({d.get('hp')} hp left)")

            elif k == EVENT_ENTITY_HEALED:
                who = d.get("name", ev.ent_id)
                amount = d.get("amount")
                if ev.ent_id == perspective:
                    lines.append(f"  You heal {amount} hp. ({d.get('hp')} hp left)")
                else:
                    lines.append(f"  {who} heals {amount} hp.")

            elif k == EVENT_STAT_CHANGED:
                who = d.get("name", ev.ent_id)
                stat = d.get("stat")
                old = d.get("old") or 0
                new = d.get("new") or 0
                delta = new - old
                sign = "+" if delta >= 0 else ""
                if ev.ent_id == perspective:
                    lines.append(f"  {stat}: {sign}{delta} (now {new})")
                else:
                    lines.append(f"  {who} {stat}: {sign}{delta} (now {new})")

            elif k == EVENT_ENTITY_DIED:
                who = d.get("name", ev.ent_id)
                if ev.ent_id != perspective:
                    lines.append(f"  *** {who} dies. ***")

            elif k == EVENT_STATUS_APPLIED:
                who = d.get("name", ev.ent_id)
                stat = d.get("stat")
                if ev.ent_id == perspective:
                    lines.append(f"  You are afflicted: {stat}.")
                else:
                    lines.append(f"  {who} is afflicted: {stat}.")

            elif k == EVENT_STATUS_EXPIRED:
                who = d.get("name", ev.ent_id)
                stat = d.get("stat")
                if ev.ent_id == perspective:
                    lines.append(f"  Your {stat} effect expires.")
                else:
                    lines.append(f"  {who}'s {stat} expires.")

            elif k == EVENT_ENTITY_SPAWNED:
                who = d.get("name", ev.ent_id)
                lines.append(f"  {who} appears.")

            elif k == EVENT_CONTAINER_CHANGED:
                who = d.get("name", ev.ent_id)
                to = d.get("to")
                if ev.ent_id == perspective:
                    lines.append(f"  You enter {to}.")
                else:
                    lines.append(f"  {who} moves to {to}.")

        return lines
