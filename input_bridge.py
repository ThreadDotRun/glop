"""
Input bridge: external ingress for player intent.

Design principles:
  - Input is an event. It travels through the same dispatch path as ticks,
    scheduled events, and emits.
  - The engine does not know about "inputs" — it knows about events.
  - The bridge is a *boundary validator*, not a game system.
  - All inputs enter the engine during integrate(), inside the tick's
    transaction, so failures roll back like any other rule failure.
  - The bridge owns no world state. It holds a queue, the engine drains it.
"""

DEFAULT_INPUT_EVENTS = {"move", "attack", "interact", "use_item", "say", "emote"}


class InputRejected(Exception):
    """Raised when input fails boundary validation."""


class InputBridge:
    def __init__(self, engine, source_ids, events=None):
        """
        engine:     the Engine to attach to.
        source_ids: iterable of ent ids allowed to originate input.
        events:     set of legal event names; defaults to DEFAULT_INPUT_EVENTS.
        """
        self.engine = engine
        self.sources = set(source_ids)
        self.events = set(events) if events is not None else set(DEFAULT_INPUT_EVENTS)
        self._seq = 0
        if not hasattr(self.engine, "_input_queue") or self.engine._input_queue is None:
            self.engine._input_queue = []

    def input(self, source, actor, event, data=None):
        """
        Called by any external system (network, UI, script) to inject intent.

        Raises InputRejected if the source isn't allowed, the event isn't
        registered, or the actor doesn't exist. Successful inputs are
        queued and dispatched during the next integrate() call.
        """
        if source not in self.sources:
            raise InputRejected(f"source {source!r} not authorized")
        if actor not in self.engine.ents:
            raise InputRejected(f"actor {actor!r} does not exist")
        if event not in self.events:
            raise InputRejected(f"event {event!r} not registered")

        self._seq += 1
        entry = {
            "source": source,
            "actor": actor,
            "event": event,
            "data": data or {},
            "seq": self._seq,
        }
        self.engine._input_queue.append(entry)

    def pending(self):
        return list(self.engine._input_queue)
