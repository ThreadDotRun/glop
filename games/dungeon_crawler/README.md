# Civ Engine

A small 4X simulation built entirely on a rule-driven engine. The game
is defined in `ents.json` and `containers.json`; `main.py` is the web
harness that reads input, advances the engine, and serves a browser UI.

## Running

    pip install flask
    python games/dungeon_crawler/main.py

Then open http://127.0.0.1:5000 in a browser.

If `save.sqlite` exists in this directory, the game resumes from it.
Otherwise it starts a new game from `ents.json`.

## Playing

Type commands into the input at the bottom, or click on the map:

    help                 show commands
    wait                 pass a turn (advances simulation by 1.0s)
    found                settle the plains (consumes the settler)
    move <tile>          move the selected unit to <tile> (costs 1 MP per Manhattan tile)
    attack <id>          attack the entity with that id
    activate <id>        select a roman unit
    save                 write to save.sqlite

The canvas accepts clicks:
- Click a blue unit to select it.
- Click a red enemy to attack it.
- Click an empty tile to move the selected unit there (costs MP).
- Click a tile occupied by a city: move is rejected with an explicit message.

Everything is echoed into the terminal scrollback. The room panel above
shows faction stats and the current entity list. The right sidebar has
a command cheatsheet.

## What the Stats Mean

| Stat | Meaning |
|---|---|
| **hammers** | Production accumulated at 2.0/turn (Rome) or 1.5/turn (Aztec). At 10, the faction builds a warrior / jaguar. |
| **beakers** | Science accumulated at 1.0/turn (Rome) or 0.5/turn (Aztec). Not spent yet; reserved for future techs. |
| **gold** | Reserved for future mechanics. |
| **food** | Reserved for future mechanics. |
| **build** (`ready_to_build`) | A per-faction flag that goes 1 → 0 on the turn a unit is built, then resets to 1 on the following turn via the `production_complete` reset rule. |

## How the Simulation Works

Every entity is defined in `ents.json`. The engine integrates a tick
(`engine.integrate(dt)`) which:
1. Applies all `rates` to stats (`hammers += 2.0`, etc.).
2. Advances `world_time`.
3. Drains the scheduler, dispatching scheduled events.
4. Fires the `tick` event on every entity (combat, regen, death checks).
5. Drains any queued player inputs.
6. Commits all staged actions atomically.

Rules are matched by trigger and evaluated at **dispatch time**, but
actions are staged and executed at **commit**. This means:
- Two rules that match the same event both fire, even if the first one
  would have made the second's condition false.
- To express "do X only if Y is present," you use the entity's existence
  in the condition: `distance(<id>) < 99` means "exists nearby",
  `distance(<id>) > 99` means "absent". (An absent entity makes
  `distance` return `inf`, so `> 99` is true.)
- To express "this action is for *me*, not the other faction," use
  `target == self`, where `target` comes from the event's `context`
  payload. The Turn Clock schedules `build_phase` with
  `context: {"target": "rome"}` / `{"target": "aztec"}`, so only the
  matching faction's rules fire.

## What's Here

- **ents.json** — the game's entities: Rome, Aztec, a settler, and the
  Turn Clock. Every behavior (production, combat, death, founding) is a
  rule.
- **containers.json** — the containers (plains, cities, forest). Only
  `plains_1` is rendered today.
- **shadow.py** — a projection layer that turns the engine's per-tick
  journal into a semantic event stream plus render hints, for a future
  3D client. The web UI narrates from it.
- **main.py** — the Flask wrapper and `/api/command` + `/api/click`
  endpoints.
- **test_shadow.py** — a smoke test that asserts the data file's
  invariants against the engine's semantics. Run with
  `python test_shadow.py`. Rename to `test_smoke.py` if you'd rather
  it not be confused with the shadow module it no longer tests.

## The Shadow Interface

`shadow.py` is a pure projection over the engine's transaction journal.
It does not modify the engine or the JSON. Any future 3D client can
consume it:

    from shadow import Shadow
    shadow = Shadow(engine)
    txn = engine.integrate(1.0)
    shadow.consume(txn)

    for event in shadow.frame_events():
        # ENTITY_SPAWNED, ENTITY_MOVED, ENTITY_DAMAGED,
        # ENTITY_DIED, CONTAINER_CHANGED, SCHEDULER_UPDATED, TICK_ADVANCED
        ...
    for hint in shadow.render_hints():
        # damage_flash, floating_damage_number, spawn_effect,
        # death_effect, sound_cue
        ...
    entities = shadow.entities()
    camera = shadow.camera_hint()

The web UI's `_advance_and_narrate` calls `shadow.narrative("rome")`
and appends the resulting lines to the console log.

## Known Limits / Future Work

- **No engine clamps.** `modify_stat` does not clamp `mp` or `hp` to
  `mp_max` / `hp_max`. The data file avoids overshoot by construction
  (regen guards on `< mp_max`, nothing adds HP), but a future rule that
  adds to `mp` or `hp` could exceed the max. Consider adding a clamp in
  the engine's `_apply_action` before adding such rules.
- **One-tick death delay.** A unit at `hp <= 0` is destroyed on its own
  `tick` rule, which fires on the *next* tick after the killing blow.
  So dead units linger for one turn. Not a stability issue, but a
  polish item.
- **No unit garrisoning.** Cities are impassable; friendly units cannot
  enter a city tile. If you want garrisoning, change
  `BLOCKING_KINDS` in `main.py` and add a "stack on tile" rule.
- **The `unit_rome_warrior` / `unit_aztec_jaguar` templates duplicate
  the top-level units that used to exist.** If you add a third faction,
  copy the spawn template structure — do not add a top-level unit.

## Adding to the Game

Everything new goes in `ents.json`:

- New unit: spawn template inside a faction's `build_phase` rule.
  Include `hp_max` and `mp_max` in the template's `stats`.
- New monster: add a top-level entity with `kind: "npc"`, tags, and
  `tick` / `attack` rules.
- New building: a `found_city` rule on a settler that spawns a `city`.
- New tech: a `beakers` stat accumulating on the faction, plus a
  `production_complete` rule that converts `beakers` into effects.

The engine has no knowledge of "warriors," "cities," or "settlers" —
only rules, events, and actions.
