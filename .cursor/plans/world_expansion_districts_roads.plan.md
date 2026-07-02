---
name: World expansion — districts, roads, and concurrent builds
overview: "The user expanded the world's height earlier (1600x1000 -> 1600x2700) purely to stop the village/farm build grids from overflowing off-canvas, but clarified afterward that 'expand the world' meant adding real additional terrain (more farmland, a real multi-block city, industry, mining, a road network) so the fixed 8-12 agent roster has enough distinct ground to build a full civilization -- not just a taller version of the same 7 cramped zones. The user then confirmed they want a genuinely OPEN world: the civilization should be able to keep expanding into new territory over time, not just live inside a bigger but still-finite hand-authored map (which would eventually hit the exact same overflow problem this plan was written to fix, just later). This plan generalizes the current 7 hardcoded zone rectangles into a districts model that is hand-authored for its starter layout but RUNTIME-MUTABLE and GROWABLE thereafter -- new district instances are founded into a reserved frontier region as existing districts near capacity -- adds a real road graph (also growable) agents traverse via waypoints, and replaces the single global build project with concurrent per-district build queues -- while preserving the existing server-authoritative engine, persistence contracts (engine-port-contracts.md), and deterministic-backstop pattern documented in CLAUDE.md. Population stays fixed (no agent spawning/growth) per explicit user decision; growth is about the world's footprint, not the roster. Execution must follow this session's established multi-agent pattern: an orchestrator (main agent) freezes shared data shapes up front, dispatches subagents in git worktrees per phase/area, independently verifies each phase's own gate before merging (never trusting a subagent's self-report), and only after everything is merged delivers the user a plain-language summary -- one simple sentence per change, no jargon, no file/function names."
todos:
  - id: districts-data-model
    content: "Phase 1 -- sim_engine.py: add a hand-authored STARTER_DISTRICTS dict (id -> {kind, bounds, tile, build_grid}), a byte-for-byte behavior-preserving equivalent of the current 7 ZONE_CENTERS/ZONE_BOUNDS rectangles (one district per existing zone, identical bounds), used only to seed civilization[\"districts\"] at cold-start -- civilization[\"districts\"] (not a static module dict) is the live, mutable, persisted registry all runtime code reads, which is what makes founding new districts later (open-world phase) possible without a parallel data model. Rewrite get_zone(x,y) (currently sim_engine.py:197-217, a literal duplicate of ZONE_BOUNDS) as a loop over civilization[\"districts\"] returning kind (back-compat), and add get_district(x,y) returning the specific district id. Add _validate_districts(districts_dict) callable both at module load (against STARTER_DISTRICTS) and at runtime (against civilization[\"districts\"] after any founding) asserting no two district rectangles overlap. Proves the registry-based get_zone/_find_structure_spot/_build_region_for refactor is safe in isolation before any new districts are added."
    status: completed
  - id: world-expansion
    content: "Phase 2 -- sim_engine.py + sprites.js: author new starter districts (second farm cluster, second village/residential block, workshop/industrial row, second mining site) into STARTER_DISTRICTS, occupying roughly the same footprint as before (~2600x2700), but set WORLD_W/WORLD_H generously larger (e.g. ~5200x5400) so the remainder of the map is reserved, open FRONTIER territory (plain grass, unclaimed) for districts founded later -- this is what makes the world open rather than just bigger. Reserve a simple frontier plot grid (fixed-size cells tiling the frontier region, tracked via civilization[\"frontierPlots\"] as an explicit claimed/unclaimed cell list) that _maybe_found_district() (added in the district-founding-frontier phase) allocates from. Each buildable starter district gets its own build_grid (replacing the two hardcoded grids in _build_region_for, sim_engine.py:633-636) so _find_structure_spot's collision check is scoped per-district instead of globally. Rewrite drawTiledWorld (sprites.js:899-971, currently ~12 hardcoded fillRectWithTile calls) as a data-driven loop over civilization[\"districts\"] (served to the client, see districts.js route) keyed by kind to the existing TILE_FARM/TILE_VILLAGE/etc. constants -- new districts sharing an existing kind, whether starter or founded later, need zero new tile code. Update index.html's WORLD_W/WORLD_H to match the generous new size; canvasWrap's existing overflow:auto scroll already handles a world this much larger than the viewport."
    status: completed
  - id: roads-waypoint-movement
    content: "Phase 3 -- sim_engine.py: add a small road graph seeded from hand-authored STARTER_ROAD_NODES/STARTER_ROAD_EDGES/DISTRICT_ENTRY_NODE into civilization[\"roadNodes\"]/civilization[\"roadEdges\"] (runtime-mutable, same rationale as districts -- founding a district later needs to extend the graph, not just read it) with all-pairs shortest paths recomputed via BFS into an in-memory ROAD_PATH_CACHE (cheap, dozens of nodes even after several foundings; recompute whenever the graph changes rather than only once at module load) plus _validate_road_graph(nodes, edges) raising at startup (and re-checked after any founding) if any district pair is unreachable. Rewrite _set_agent_target (sim_engine.py:454-464) to route through cached node paths into a new agent[\"waypoints\"] list; _move_agent (:482-503) pops the next waypoint on arrival instead of idling. Gate routing behind a new ROADS_ENABLED flag (matches the existing feature-flag convention) so straight-line movement stays A/B-comparable. Deliberately keep move_to_agent/trade/talk targeting AND Sage-emergency _rush_to_heal/_auto_move_toward_target on direct-line movement (bypassing roads) -- short local hops and the latency-critical emergency responder path shouldn't pay routing overhead for no benefit. Generalize sprites.js's existing markPathRect-based path system into markRoadEdges(edges, nodeCoords) looping over the served road-edge list instead of the 5 hardcoded connector strips."
    status: completed
  - id: concurrent-district-builds
    content: "Phase 4 (highest-risk, most call sites) -- sim_engine.py + server.py: replace civilization[\"activeProject\"] (singular) with civilization[\"districtProjects\"] (dict keyed by district id, None when idle) and lastProjectContributionFrame with districtLastContribution (dict keyed by district id); every function that reads/writes the global project (_start_project_for, _try_contribute_resource, _is_project_complete, _first_unmet_project_resource, _project_progress_text, _build_active_structure, _task_for_agent -- sim_engine.py:582-727, apply_decision call sites :1343-1453) gains a district_id parameter defaulting to agent[\"currentDistrict\"]. Generalize _maybe_force_contribution to loop all districts with an active project (same stall-gated guarantee, per-district). Add new backstop _maybe_start_idle_district_project (mirrors _maybe_advance_rules's shape) since nothing today encourages the LLM to spread work across multiple buildable districts. Add MAX_CONCURRENT_PROJECTS (start at 2-3, tune empirically). Breaking decision-schema change: replace the fixed move_to_farm/move_to_market/etc. DECISION_ACTIONS enum members (server.py:382-395) with a single generic move_to_district action whose target names a district id (hardcoding a move_to_X per district doesn't scale, and is architecturally impossible once districts are founded at runtime rather than fixed at code-authoring time); add optional target_district to DECISION_SCHEMA for start_project/contribute_resources/build_structure, defaulting to the agent's current district. In apply_decision, resolve an old kind name (e.g. \"farm\") to the nearest district of that kind rather than failing, as a hedge during prompt-tuning transition. Update role_fallback_action's ~8 call sites and SYSTEM_PROMPT to match. Serve civilization[\"districts\"]/roadNodes/roadEdges via a new GET /districts.js route (server.py) that reads current live state (not a static constant) so newly-founded districts/roads appear to the viewer without a reload, mirroring the existing /roles.js precedent, and update index.html's sidebar to render a list from districtProjects instead of the old singular activeProject (the one required client behavioral change)."
    status: completed
  - id: district-founding-frontier
    content: "Phase 5 (the open-world mechanism itself, depends on phases 1-4's runtime-mutable districts/roads/per-district-capacity signals) -- sim_engine.py: add _maybe_found_district(), a tick-gated deterministic backstop (same pattern as _maybe_advance_rules/_maybe_force_contribution) that, when every existing district of a given buildable kind is at/near its build_grid cap AND that kind's resource contribution keeps stalling (reusing the districtLastContribution stall signal from phase 4), claims the next unclaimed cell from the frontier plot grid (phase 2), instantiates a new district there from that kind's standard build_grid/tile template, appends it to civilization[\"districts\"], auto-generates an entry node connected to the nearest existing ROAD_NODE (straight-line nearest-neighbor is sufficient at this graph size) into civilization[\"roadNodes\"]/roadEdges, invalidates and recomputes ROAD_PATH_CACHE, and logs a _push_activity announcement (e.g. 'The village claims new land to the east for a third farm') so founding is observable in activity.jsonl exactly like every other deterministic trigger in this codebase. Add MAX_TOTAL_DISTRICTS (a generous but real safety valve, e.g. 24-30) since this app is designed to run persistently/headless for weeks (per the earlier persistence work) -- without a cap, an always-on server could in principle keep founding districts indefinitely; the cap is not expected to bind in normal play with a fixed 8-12 agent roster, it exists purely as insurance. _validate_districts()/_validate_road_graph() (phase 1/3) get re-run after every founding, not just at module load, so a bug in the founding logic itself fails loudly rather than silently corrupting the live world."
    status: completed
  - id: persistence-migration
    content: "Cross-cutting (lands incrementally alongside phases 1-5) -- extend the three frozen contracts in .cursor/plans/engine-port-contracts.md rather than inventing a parallel mechanism: Contract 1 gains civilization.districts/roadNodes/roadEdges/frontierPlots (all now runtime state, not static constants -- seeded from STARTER_DISTRICTS/STARTER_ROAD_NODES/STARTER_ROAD_EDGES at cold-start, mutated by district-founding thereafter), agent.currentDistrict/agent.waypoints, and civilization.districtProjects/districtLastContribution (replacing activeProject/lastProjectContributionFrame); structures gain districtId. Contract 2's GET /state civilization shape swaps activeProject for districtProjects; config.flags gains ROADS_ENABLED; config gains (or /districts.js serves) the live districts/roads so a founded district appears to a connected viewer without a reload. New fields round-trip through the existing generic serialize/restore copy path in _serialize_state()/restore_state() with no special-casing needed (unlike the existing _CIV_SET_KEYS sets) since they're plain dicts/lists. Add a one-time migration shim in restore_state(): if civilization.districts is absent from a loaded state.json (pre-this-plan save), seed it from STARTER_DISTRICTS/STARTER_ROAD_NODES/STARTER_ROAD_EDGES, seed districtProjects empty per buildable district, and drop any old in-flight activeProject (low-stakes, one-time loss of a build-in-progress only -- never agent identity/memory/resources/relationships). Bump STATE_VERSION from 1 to 2 to make the migration explicit and testable."
    status: completed
  - id: minimap-optional
    content: "Phase 6 (optional polish, independent of the rest, can land any time after phase 2) -- index.html only: add a small fixed-position minimap canvas showing district-bounds rectangles by kind (including any founded after cold-start), agent dots, and a viewport-outline, reading only from the existing GET /state poll + the live district list. More load-bearing than in a fixed-size world: once districts can be founded into a large open frontier, a minimap is the only way to notice new territory came online without scrolling to find it. No engine changes; index.html stays a pure renderer."
    status: pending
  - id: orchestrate-execution
    content: "Step 0 (orchestrator, before any subagent starts) -- freeze the exact shared data shapes (district entry, road node/edge, frontierPlots, districtProjects, GET /districts.js response) per the Persistence & contracts section, then dispatch subagents in their own git worktrees: Agent A = engine core (sim_engine.py, sequential across phases 1-5, the critical path), Agent B = rendering (sprites.js + index.html, starts once shapes are frozen, works against them independent of Agent A's actual merges), Agent C = server/prompt (server.py, dispatched once Agent A's phase-4 districtProjects lands). Orchestrator independently verifies each subagent's own phase gate (does not trust self-reports) before merging in dependency order, and runs the full end-to-end Verification only after every phase is merged."
    status: completed
  - id: final-user-summary
    content: "After all phases are merged and end-to-end Verification passes -- orchestrator delivers a plain-language summary to the user: one simple, jargon-free sentence per discrete change (what's different for the user/simulation, not which function or file changed), formatted as a short bullet list grouped loosely by topic (world size, roads/movement, building, growth). No code diffs, function names, or file paths in this summary."
    status: completed
isProject: false
---

# World expansion: districts, roads, and concurrent builds

## Context

The user expanded the world's height earlier (1600x1000 → 1600x2700) purely to
stop the village/farm build grids from overflowing off-canvas. Seeing it
running, the user clarified that "expand the world" meant something different:
add real additional **terrain** (more farmland, a real multi-block city,
industry, mining, a road network) so the fixed 8-12 agent roster has enough
distinct ground to build a full civilization — not just a taller version of
the same 7 cramped zones. The current build feels "zoomed in," not bigger.

Investigated the live code (`simulation/sim_engine.py`, `simulation/server.py`,
`simulation/sprites.js`, `simulation/index.html`) and confirmed three
structural limits that block this:
1. **Exactly 7 hardcoded zone rectangles** (`ZONE_CENTERS`/`ZONE_BOUNDS`,
   `sim_engine.py:56-75`, and a literal-duplicate `get_zone()` at
   `sim_engine.py:197-217`) — one farm, one village, one cave, etc. No way to
   add a second farm cluster or a real multi-district city without a new data
   model.
2. **Zero pathfinding.** `_move_agent`/`_set_agent_target`
   (`sim_engine.py:454-503`) teleport-interpolate agents in a straight line to
   a random point inside a zone rectangle. There's a lightweight cosmetic
   path-tile system already (`PATH_CELLS`/`markPathRect` in `sprites.js`,
   5 hardcoded connector strips) but agents don't walk it — it's decoration.
3. **Exactly one active build project for the whole civilization**
   (`civilization["activeProject"]`, `sim_engine.py:685-703` and ~20 other
   call sites) — nothing can be under construction in two places at once, so a
   bigger map wouldn't actually fill up faster.

The user made three explicit scope decisions (asked via AskUserQuestion):
- **Real waypoint-based road movement** (not just cosmetic path tinting) —
  agents should actually walk roads between districts.
- **Keep the fixed 8-12 agent roster** — no population growth/spawning in this
  plan; scope is terrain/building capacity only.
- **Allow concurrent builds** — replace the single global project with
  per-district build queues so multiple structures rise in parallel.

**Follow-up requirement (this revision): the world must be genuinely open,
not just bigger-but-finite.** A hand-authored fixed set of districts — even
a generous one — is still a hard ceiling; given enough uptime (this app
already runs persistently/headless, per the earlier persistence work) the
civilization would eventually fill every district's build grid and stall
exactly the way the original overflow bug did, just later. "Open" here means:
districts and the road graph are **runtime state that can grow**, seeded from
a hand-authored starter layout but extendable by the simulation itself into a
deliberately reserved frontier region, so there is always more room to expand
into rather than a final wall. This does NOT mean an infinite/streaming world
(no chunk-loading, no dynamically resizing canvas) — see "Districts: hand-
authored core + growable frontier" below for the concrete, bounded mechanism
that delivers this without that much larger engineering lift.

Intended outcome: a much larger, terrain-varied, **open-ended** world
(multiple farm clusters, a real multi-block city district, a workshop/
industrial area, market, one or more mining sites, forest, beach/dock, plus
room to found more of any of these later) connected by roads agents actually
traverse, with several districts under construction at once — analogous in
spirit to Project Sid's town scale, built on top of this project's existing
server-authoritative engine, persistence contracts
(`.cursor/plans/engine-port-contracts.md`), and deterministic-backstop
pattern (documented in `CLAUDE.md`) rather than replacing them.

## Approach

### 1. Districts: hand-authored core + growable frontier (open-world foundation)

This is the piece that actually makes the world open rather than merely
bigger. Two ideas, deliberately kept separate:

**(a) `STARTER_DISTRICTS`** — a hand-authored, immutable module-level
blueprint (hand-authored, not procedural: the render pipeline is hand-tuned
per rectangle today, and generating an *initial* good-looking city
procedurally would be a much bigger, riskier lift for no real benefit).
Shape unchanged from the original design:

```python
STARTER_DISTRICTS = {
    "farm_north":   {"kind": "farm", "bounds": {...}, "tile": "farm",
                      "build_grid": {"x0":..., "y0":..., "cols":5, "dx":90, "dy":80, "cap":30}},
    "farm_south":   {"kind": "farm", ...},          # second farm cluster
    "village_core": {"kind": "village", ..., "build_grid": {...}},
    "village_east": {"kind": "village", ...},        # second residential block
    "workshop_row": {"kind": "workshop", ..., "build_grid": {...}},
    "market": {"kind": "market", ..., "build_grid": None},
    "forest": {"kind": "forest", ..., "build_grid": None},
    "cave_east": {"kind": "cave", ..., "build_grid": None},
    "cave_deep": {"kind": "cave", ...},              # second mining site
    "beach": {..., "ocean": {...},
}
```

**(b) `civilization["districts"]`** — the LIVE registry, seeded by a deep
copy of `STARTER_DISTRICTS` at cold-start (`_reset_world`/`SimEngine.__init__`)
and the thing every runtime function actually reads/writes from this point
on. This is the key architectural difference from a "just bigger" map: since
districts live in mutable `civilization` state (persisted like everything
else, see section on persistence), the simulation itself can add new
district instances later (section "District founding," below) without any
new data model — founding just appends another entry with the same shape.

- `kind` groups districts for resource/tile purposes — multiple districts can
  share a kind (two `farm` districts = two farm clusters, and later a third
  founded one). Everything that today assumes `get_zone(x,y) == "farm"` means
  "the one farm" is generalized to mean "a district of kind farm."
- `build_grid` replaces the two hardcoded grids in `_build_region_for`
  (`sim_engine.py:633-636`) — every buildable district gets its own capped
  grid; `_find_structure_spot` (`:638-647`) scopes its collision check to
  structures already in that district (cheaper and more correct than today's
  global scan).
- `get_zone(x, y)` (`:197-217`) is rewritten as a loop over
  `civilization["districts"]` returning `kind`, plus a new `get_district(x, y)`
  returning the specific district id — `agent["currentZone"]` keeps its
  existing meaning (kind, back-compat), a new `agent["currentDistrict"]`
  field carries the specific id needed for build-grid/road targeting.
- `_validate_districts(districts_dict)` takes the dict as a parameter (not a
  global) so it can run both at module load (against `STARTER_DISTRICTS`) and
  again at runtime after any founding (against the live
  `civilization["districts"]`) — asserts no two district rectangles overlap,
  so a bad hand-authored edit *or* a bug in the founding logic fails loudly
  instead of producing silently-wrong `get_district` results.

**The frontier (what makes growth possible without a much bigger engineering
lift):** set `WORLD_W`/`WORLD_H` generously larger than the starter core
needs — roughly **~5200x5400** against a starter footprint of ~2600x2700 (the
core occupies well under half the map). The remaining space is plain,
undifferentiated grass (`get_district` returns `None`/"path" there, exactly
like today's unclaimed space) reserved as **frontier**: a simple fixed-size
plot grid (e.g. a grid of ~2600x2700-sized cells tiling the open area,
tracked as a claimed/unclaimed list in `civilization["frontierPlots"]`) that
district-founding (see below) claims from, one plot at a time, as the
civilization needs more room of a given kind. This deliberately stops short
of a fully dynamic/streaming world (no chunk loading, no runtime canvas
resize, no re-baking an ever-growing terrain cache) — the world's *outer*
bound is still fixed and known upfront, but the *interior* is genuinely
open/unclaimed until the simulation grows into it, which is the practical
way to deliver "open world" within this project's single-canvas rendering
model. If the frontier itself is ever exhausted (all plots claimed), that's
a real, if very distant, ceiling — `MAX_TOTAL_DISTRICTS` (see "District
founding," below) is set well below the plot count so this isn't reachable
in practice with a fixed 8-12 agent roster.

### 2. Road network + waypoint movement

Same starter/live split as districts: a hand-authored `STARTER_ROAD_NODES`/
`STARTER_ROAD_EDGES`/`DISTRICT_ENTRY_NODE` blueprint seeds
`civilization["roadNodes"]`/`civilization["roadEdges"]` at cold-start, and
all runtime code (routing, rendering) reads the live copy — required so that
district-founding (below) can extend the graph later, not just read a frozen
one.

```python
STARTER_ROAD_NODES = {"village_hub": {"x":840,"y":950}, "farm_north_gate": {...}, ...}
STARTER_ROAD_EDGES = [("farm_north_gate", "village_hub"), ...]  # undirected
DISTRICT_ENTRY_NODE = {"farm_north": "farm_north_gate", ...}  # each district's "front door"
```

Compute all-pairs shortest paths via BFS into an in-memory `ROAD_PATH_CACHE`
whenever the graph changes (once at cold-start, and again after any
founding — cheap regardless, dozens of nodes even after several foundings) —
not a one-time module-load constant, since the graph itself isn't one
anymore. Add `_validate_road_graph(nodes, edges)` alongside
`_validate_districts()`: run the same BFS and raise (at startup, and
re-check after every founding) if any district pair is unreachable, so a
missing/typo'd edge — or a founding-logic bug — fails loudly rather than
silently stranding a district.

`_set_agent_target(agent, district_id)` (`:454-464`) is rewritten to: look up
the agent's current district's entry node and the destination district's
entry node, pull the cached node path between them, and set
`agent["waypoints"]` (new list field) to that path plus a final random point
inside the destination's bounds. `_move_agent` (`:482-503`) gets one addition:
when it arrives at `targetX/targetY` and `waypoints` is non-empty, pop the
next waypoint instead of falling into the idle/wander branch. The core
interpolation loop is otherwise unchanged.

**What routes vs. stays direct** (deliberate, not uniform):
- `move_to_district` (general travel, idle wander, craft-station redirects) —
  **routes via roads.** This is the actual feature being asked for.
- `move_to_agent` / trade / talk targeting — **stays direct** (short local
  hops within the existing ~80px proximity check; forcing these through a
  road node first would be visually silly for zero gameplay value).
- **Sage-emergency `_rush_to_heal`/`_auto_move_toward_target` — stays direct,
  bypasses roads entirely.** This is the deterministic, latency-critical
  responder path CLAUDE.md documents; adding routing overhead to an emergency
  rescue would be a strict regression for no benefit (there's no collision
  system today for roads to meaningfully route around anyway).

Gate the routing behavior behind a new `ROADS_ENABLED` flag (matches the
project's existing feature-flag convention) so straight-line movement can
still be A/B compared: when off, `_set_agent_target` skips straight to the
random interior point exactly as it does today.

### 3. Concurrent district builds

Replace `civilization["activeProject"]` (singular) with
`civilization["districtProjects"]` (dict keyed by district id, `None` when
idle — only districts with a `build_grid` get a key) and
`lastProjectContributionFrame` with `districtLastContribution` (dict keyed by
district id). Keep `directive` (elder's latest headline text) unchanged.

Every function that currently reads/writes the single global project gains a
`district_id` parameter (defaulting to `agent["currentDistrict"]` when the
decision doesn't specify one — see below): `_start_project_for`,
`_try_contribute_resource`, `_is_project_complete`,
`_first_unmet_project_resource`, `_project_progress_text`,
`_build_active_structure`, `_task_for_agent` (`sim_engine.py:582-727`, all
call sites in `apply_decision` at `:1343-1453`).

- **`_maybe_force_contribution`** (the existing deterministic stall backstop,
  already generalized once earlier this session) becomes a loop over all
  districts with an active project instead of checking one global project —
  same "fires only after a real stall" guarantee, per-district.
- **`_task_for_agent`/elder task-assignment** picks the most-stalled active
  district to steer idle agents toward, instead of describing "the" project.
- **New backstop: `_maybe_start_idle_district_project`.** With multiple
  buildable districts, nothing today encourages the LLM to spread work across
  them — it's plausible the model fixates on one district indefinitely. Add a
  tick-gated method (mirrors `_maybe_advance_rules`'s shape) that
  deterministically starts a project in a buildable district that's been idle
  too long, if an agent happens to be standing in it.
- Add `MAX_CONCURRENT_PROJECTS` (start conservative, e.g. 2-3) so the small
  roster doesn't spread across so many simultaneous builds that none ever
  finishes — a tuning constant, validate empirically once running.

**Decision-schema change (the one deliberately breaking change):** replace
the fixed `move_to_farm`/`move_to_market`/etc. enum members in
`DECISION_ACTIONS` (`server.py:382-395`) with a single generic
`move_to_district` action whose `target` names a district id — hardcoding a
`move_to_X` per district as more districts are added would bloat
`DECISION_ACTIONS`/`SYSTEM_PROMPT` unmanageably. Add an optional
`target_district` field to `DECISION_SCHEMA` for `start_project`/
`contribute_resources`/`build_structure`, defaulting to the agent's current
district so existing decision payloads that omit it keep working. In
`apply_decision`, if the LLM still emits an old kind name (e.g. `"farm"`)
instead of a district id, resolve to the nearest district of that kind rather
than failing — a deliberate hedge during the prompt-tuning transition.
Update `role_fallback_action`'s ~8 call sites and `SYSTEM_PROMPT` to match
(`server.py`).

### 4. District founding (the open-world mechanism)

This is the section that actually delivers "open world": a deterministic
tick-gated backstop, `_maybe_found_district()`, following the exact same
shape as the existing `_maybe_advance_rules`/`_maybe_force_contribution`
pattern (cooldown/stall-gated, calls into normal state mutation, logged via
`_push_activity` so it's observable in `activity.jsonl` like every other
deterministic trigger in this codebase):

- **Trigger:** every existing district of a given buildable `kind` is at or
  near its `build_grid` cap, AND that kind's contribution keeps stalling
  (reuses the `districtLastContribution` stall signal already introduced for
  concurrent builds) — i.e. the civilization has run out of room to build
  more of something and is actively trying to.
- **Action:** claim the next unclaimed cell from `civilization["frontierPlots"]`
  (section 1), instantiate a new district there using that `kind`'s standard
  `build_grid`/`tile` template (same shape as any `STARTER_DISTRICTS` entry),
  append it to `civilization["districts"]`, auto-generate an entry node for
  it (nearest-neighbor straight-line connection to the closest existing
  `ROAD_NODE` — sufficient at this graph's small size, no real pathfinding
  needed to place the connector itself) into
  `civilization["roadNodes"]`/`roadEdges`, invalidate and recompute
  `ROAD_PATH_CACHE`, then re-run `_validate_districts()`/`_validate_road_graph()`
  against the updated live state so a bug here fails loudly rather than
  quietly corrupting the world.
- **Observability:** log an activity entry, e.g. *"The village claims new
  land to the east for a third farm,"* so founding a district is as visible
  in the logs/Activity panel as any other civilization milestone.
- **Safety valve:** `MAX_TOTAL_DISTRICTS` (generous, e.g. 24-30) — this app
  is designed to run persistently/headless for weeks at a time (per the
  earlier persistence work), so an unbounded founding loop is a real
  (if slow-moving) risk over a very long uptime even though it won't bind in
  normal play with a fixed 8-12 agent roster. Purely insurance, not expected
  to matter.

This keeps growth **deterministic and observable** rather than another
LLM-opt-in action the model might never volunteer (the exact failure mode
CLAUDE.md already documents for `start_project`, and the reason every other
civilizational trigger in this codebase — role-switching, rule-proposing,
stalled-contribution — ended up as a backstop instead of a pure LLM choice).
No new LLM-facing action is needed for this to work.

### 5. Rendering (`sprites.js` / `index.html`)

- `drawTiledWorld` (`sprites.js:899-971`, currently ~12 hardcoded
  `fillRectWithTile(s)` calls + one hardcoded prop/label list) becomes a
  data-driven loop over the served district list (see below): one tile-fill +
  one zone label per district entry, keyed by `kind` to the existing
  `TILE_FARM`/`TILE_VILLAGE`/etc. constants (new districts sharing an
  existing kind — starter or founded later — need zero new tile code).
  Hand-placed decorative props (trees, dock, wells, rocks, crops, fences,
  cave entrances) stay as bespoke per-spot calls for the starter core —
  inherently artistic placement, not worth generalizing; a founded district
  can render with just its tile fill + label and no bespoke props, which is
  an acceptable (and arguably fitting) "frontier settlement" look.
- Generalize the existing `markPathRect`-based path system into
  `markRoadEdges(edges, nodeCoords)`: loop over the served road-edge list
  instead of the 5 hardcoded connector strips, reusing `markPathRect`/
  `fillRectWithTiles`/`pathBlendForZone` exactly as they work today. Author
  starter road edges as axis-aligned or L-shaped (two segments) to avoid
  writing new line rasterization code; founded districts' auto-generated
  connector edges (section 4) should follow the same constraint.
- Serve `civilization["districts"]`/`roadNodes`/`roadEdges` to the browser via
  a new `GET /districts.js` route (`server.py`) that reads the **live**
  engine state (not a static constant) so a newly-founded district/road shows
  up to a connected viewer on its next poll without a page reload — this is
  the one place the existing `/roles.js` precedent (`server.py` ~line 1191,
  which serves a genuinely static file) doesn't quite fit as-is; the route
  handler needs to read `engine.civilization` under the lock like `/state`
  does, not just return a fixed string.
- Update `index.html`'s `WORLD_W`/`WORLD_H` consts to match the larger,
  generously-sized world (section 1), and the one **required behavioral**
  change: the sidebar code currently reading
  `world.civilization.activeProject` must instead render a list from
  `districtProjects` (filter nulls, one row per active build). Everything
  else in `index.html` stays a pure renderer of `GET /state`.
- **Add a minimap** (`index.html`, purely additive, no engine changes): a
  small fixed-position canvas showing district-bounds rectangles by kind
  (including any founded after cold-start), agent dots, and a
  viewport-outline. More load-bearing here than in a merely-bigger fixed map:
  once districts can appear during play, a minimap is the only way to notice
  new territory came online without manually scrolling to stumble on it.
  Optional polish, can land independently/last.

### 6. Persistence & contracts

Extend the existing three contracts (`.cursor/plans/engine-port-contracts.md`)
rather than inventing a parallel mechanism:
- **Contract 1** (state model): `civilization.activeProject` →
  `districtProjects`/`districtLastContribution`; add
  `civilization.districts`/`roadNodes`/`roadEdges`/`frontierPlots` (all now
  runtime state seeded from `STARTER_DISTRICTS`/`STARTER_ROAD_NODES`/
  `STARTER_ROAD_EDGES` at cold-start, mutated by district-founding
  thereafter — NOT static module constants); add `agent.currentDistrict` and
  `agent.waypoints`; structures gain `districtId`.
- **Contract 2** (`GET /state`): `civilization.districtProjects` replaces
  `activeProject`; `config.flags` gains `ROADS_ENABLED`. The live
  districts/roads are served via `/districts.js` (section 5) rather than
  bloating every `/state` poll with mostly-static data.
- **Contract 3** (`state.json`): new fields (including `districts`/
  `roadNodes`/`roadEdges`/`frontierPlots`, all plain dicts/lists) round-trip
  through the existing generic serialize/restore copy path with no special
  casing needed (unlike the existing `_CIV_SET_KEYS` sets).
- **Migration of existing saves:** old `state.json` files have the singular
  `activeProject`, no `districts`/`roadNodes`/`roadEdges`/`frontierPlots` at
  all (they were static constants before this plan), and no
  `districtProjects`/`currentDistrict`/`waypoints`. `restore_state()` gets a
  one-time migration shim: if `civilization["districts"]` is absent, seed it
  (and roadNodes/roadEdges/frontierPlots) from
  `STARTER_DISTRICTS`/`STARTER_ROAD_NODES`/`STARTER_ROAD_EDGES`, seed
  `districtProjects` empty per buildable district, and drop any old
  in-flight `activeProject` (a one-time, low-stakes loss of a
  build-in-progress — never agent identity, memory, resources, or
  relationships, all of which stay fully intact). Bump `STATE_VERSION` from
  1 to 2 to make this explicit and testable rather than silently
  reinterpreting old files.

### Known risks (flagged, not blocking)

- **Prompt token growth.** New `known_districts`/`district_projects` payload
  fields add to an already-documented context-sensitive prompt (CLAUDE.md's
  LM Studio context-length guidance). Keep these terse (id+kind only, only
  active entries) and measure actual token count after the concurrent-builds
  phase lands; trim further if needed.
- **`get_zone()` kind-vs-district drift.** The subtlest risk in the whole
  plan: existing code assuming `get_zone(x,y) == "farm"` means "the one farm"
  will misbehave once a second farm district exists. Mitigate via an explicit
  audit pass (Phase 1 is specifically designed to make this tractable)
  converting each "needs the specific district" call site to
  `get_district`/`currentDistrict`.
- Perf impact of a larger world + road graph is expected to be negligible
  (terrain is baked once at page load; the road graph is a few dozen nodes
  recomputed only on founding events, which are rare; per-tick agent movement
  cost is unchanged big-O) — not a real concern at this project's scale
  (8-12 agents).
- **Unbounded growth over very long uptime.** Since this app is designed to
  run persistently/headless for weeks, an always-on server could in
  principle keep founding districts indefinitely if the stall/capacity
  trigger fires repeatedly. Mitigated by `MAX_TOTAL_DISTRICTS` (section 4) —
  a real, generous cap, not expected to bind with a fixed roster, but present
  specifically because "runs forever" is this project's actual deployment
  model now, not a hypothetical.
- **Frontier plot exhaustion.** If `MAX_TOTAL_DISTRICTS` is ever set too high
  relative to the frontier's actual plot count, founding could fail to find
  an unclaimed cell. `_maybe_found_district()` should treat "no unclaimed
  frontier plot" as a silent no-op (log once, then stop retrying every tick
  cadence) rather than an error — this is an extremely distant edge case
  given the frontier is sized generously relative to the cap, but worth a
  one-line defensive check since it's cheap.
- **Rendering a founded district with only generic props** (no hand-placed
  trees/wells/etc., section 5) is a deliberate scope cut to avoid needing a
  "scatter props procedurally" system just for this plan — flagged as an
  acceptable, visually-plainer-but-functional look for frontier expansions,
  not an oversight.

## Suggested phasing

No test suite exists; verify each phase by running the server, reading
`activity.jsonl`/`lm_studio.jsonl`, and polling `GET /state` (per CLAUDE.md).
Each phase should leave the app fully runnable end to end.

1. **Districts data model only** — introduce `STARTER_DISTRICTS`/
   `civilization["districts"]`/`get_district()` as a byte-for-byte
   behavior-preserving refactor of the current 7 zones (one district per
   existing zone, identical bounds) before adding anything new. Proves the
   registry-based `get_zone`/`_find_structure_spot`/`_build_region_for`
   refactor — and the runtime-mutable-state model itself — is safe in
   isolation.
2. **World expansion** — author the new farm/village/cave/workshop clusters
   into `STARTER_DISTRICTS`, set the generously larger `WORLD_W`/`WORLD_H`,
   reserve the frontier plot grid, update `sprites.js`'s district-loop
   rendering. Builds still serialize through one global project for now
   (concurrency is phase 4), but structure placement already needs
   per-district grids since there are now multiple farm/village districts.
3. **Roads + waypoint movement** — add the road graph (seeded live, same
   mutability model as districts), waypoint-aware `_set_agent_target`/
   `_move_agent`, road-segment rendering. Explicitly verify Sage-emergency
   rescue still moves directly (unaffected).
4. **Concurrent district builds** — the highest-risk phase (most call sites,
   the one breaking `move_to_district` schema change): replace the global
   project with per-district queues, add `target_district`, the new
   idle-district backstop, update `SYSTEM_PROMPT`/`role_fallback_action`, and
   the Contract 2/3 + migration-shim updates. This phase's per-district
   stall/capacity signals are what phase 5's founding trigger reuses.
5. **District founding (the open-world payoff)** — add
   `_maybe_found_district()`, the frontier-claiming logic, auto-generated
   road connectors, `MAX_TOTAL_DISTRICTS`, and the live `/districts.js`
   route so a founded district/road is visible to a connected viewer without
   a reload. Verify end-to-end: force a district near capacity (or lower
   its `cap` temporarily for a test run), confirm a new district appears in
   `civilization["districts"]`, gets a working road connection, and agents
   route to/build in it.
6. **Minimap** (optional polish) — `index.html` only, independent of the rest,
   can land any time after phase 2 (more useful once phase 5 exists, since
   that's when new territory can appear mid-session).

## Multi-agent execution (subagents + orchestrator)

Given the size of this work (a full architecture change spanning
`sim_engine.py`/`server.py`/`sprites.js`/`index.html`), this plan must be
executed as a team of subagents coordinated by a single **orchestrator**
(the main agent), the same way the earlier server-authoritative engine port
was done in this session — not as one large in-place change by a single
agent.

**Step 0 — Orchestrator freezes the exact shapes before dispatching anyone.**
Before any subagent starts, write down the precise field names/types for
everything section 6 (Persistence & contracts) and sections 1-2 describe in
prose: the `STARTER_DISTRICTS`/`civilization["districts"]` entry shape
(`kind`/`bounds`/`tile`/`build_grid`), the road node/edge shape, the
`frontierPlots` shape, the `districtProjects`/`districtLastContribution`
shape, and the `GET /districts.js` response shape. Subagents build against
these frozen shapes so engine and rendering work can proceed without
colliding or guessing at each other's data.

**Dispatch, in git worktrees (`isolation: "worktree"`), roughly as follows —
adjust based on what's already merged when execution actually starts:**
- **Agent A — Engine core, sequential (owns `sim_engine.py`'s state model and
  tick logic).** Phases 1 → 2 (engine half only) → 3 → 4 → 5 are each their
  own dispatch, run one at a time: phase *N* must be merged and independently
  verified before phase *N+1*'s subagent is dispatched, since each phase's
  data model depends on the previous one actually existing (districts before
  roads, roads before founding needs to extend them, districtProjects before
  the founding trigger can read stall signals). This mirrors the original
  engine port's finding that single-file, interdependent work doesn't
  parallelize — it's the critical path.
- **Agent B — Rendering (owns `sprites.js` + `index.html`).** Can start as
  soon as Step 0's shapes are frozen, working against those frozen shapes
  (not waiting on Agent A's actual merges) the same way the original port's
  viewer subagent built against a stubbed `/state` contract. Covers phase 2's
  district-loop rendering, phase 3's `markRoadEdges`, the sidebar's
  `districtProjects` rendering (phase 4), and the optional minimap (phase 6)
  as one continuous track, integrated against Agent A's real merges once
  each corresponding phase lands.
- **Agent C — Server/prompt (owns `server.py`'s decision schema and
  prompt).** Dispatched once Agent A's phase 4 (districtProjects) is merged,
  since `move_to_district`/`target_district`/`SYSTEM_PROMPT`/
  `role_fallback_action` all depend on that shape existing. Also owns the
  live `GET /districts.js` route.
- Persistence/migration work (the `persistence-migration` item) is NOT a
  separate subagent — it lands incrementally inside each of Agent A's phase
  dispatches (each phase's `_serialize_state`/`restore_state` additions are
  part of that phase's own diff), per the plan's own framing of it as
  cross-cutting rather than standalone.

**Orchestrator responsibilities throughout:**
- Author and freeze Step 0's shapes; hand each subagent its scope, the
  relevant frozen shape, and its exact gate (the bullet(s) from this plan's
  Verification section that apply to that phase).
- Sequence and merge worktrees in dependency order (Agent A's phases in
  strict sequence; Agent B/C merged in once their corresponding Agent-A
  phase is live).
- After each subagent reports done, **independently verify — do not take the
  subagent's word for it.** Re-run that phase's own verification bullet
  yourself (curl `GET /state`/`/districts.js`, headless tick check, a
  restart-resume check where relevant, a visual check in the browser) and
  read the relevant JSONL logs, exactly as was done for the engine-port
  subagents earlier this session.
- Only after ALL phases are merged, run the full end-to-end Verification
  section as a whole (not just per-phase) before considering the work done.
- Keep the currently-running server/port untouched by subagent test runs —
  every subagent gate must run on a throwaway port, never the user's live
  instance, matching the discipline already established this session.

## Files to modify

- `simulation/sim_engine.py` — `STARTER_DISTRICTS`/`STARTER_ROAD_NODES`/
  `STARTER_ROAD_EDGES` blueprints, `civilization["districts"/"roadNodes"/
  "roadEdges"/"frontierPlots"]` runtime state, per-district build state,
  `_maybe_found_district()` and the other deterministic backstops,
  `apply_decision` district-targeting.
- `simulation/server.py` — `DECISION_ACTIONS`/`DECISION_SCHEMA`/
  `SYSTEM_PROMPT`, `role_fallback_action`, `normalize_decision`,
  `_build_think_payload` additions, new live-reading `/districts.js` route.
- `simulation/sprites.js` — data-driven `drawTiledWorld` district loop,
  `markRoadEdges`.
- `simulation/index.html` — `WORLD_W`/`WORLD_H`, district-projects sidebar
  rendering, minimap (optional).
- `.cursor/plans/engine-port-contracts.md` — Contract 1/2/3 updates
  (including the districts/roads/frontier fields moving from static
  constants to persisted runtime state).

## Verification

- Server boots headless (no browser) at each phase; `frameTick` advances,
  `activity.jsonl` shows agents moving between/building in the new districts.
- `GET /state` shows `agents[].currentDistrict`/`waypoints` populated
  correctly and draining as agents travel; `civilization.districtProjects`
  shows more than one non-null entry simultaneously over a longer run.
- Visual check in the browser: new districts render distinctly, agents
  visibly walk roads between them (not straight lines through blank grass),
  Sage-emergency rescue still looks instantaneous/direct.
- **Open-world check (phase 5):** with a district's `cap` temporarily lowered
  for a test run (or over a long enough natural run), confirm
  `_maybe_found_district()` fires: a new entry appears in
  `civilization["districts"]`, `/districts.js`'s next response includes it,
  a connected viewer renders it without a page reload, agents successfully
  route to it via the auto-generated road connector, and
  `_validate_districts()`/`_validate_road_graph()` pass against the updated
  live state (no overlap, no unreachable district).
- Restart the server against a pre-phase-4 `state.json` (no `districts` key
  at all) and confirm the migration shim cold-starts
  `districts`/`roadNodes`/`roadEdges`/`districtProjects` cleanly from the
  starter blueprint rather than crashing or silently losing agent
  memory/relationships. Separately, restart against a post-phase-5
  `state.json` that already contains a founded district and confirm it
  survives the restart (not just the starter set).
- No regression in `lm_studio_server.log`/`lm_studio.jsonl` (no new
  `"Context size has been exceeded"` bursts from the larger prompt payload).

## Final summary for the user

Only after every phase is merged and the full Verification above passes, the
orchestrator gives the user a plain-language summary — **not** a code diff or
a technical changelog:
- **One sentence per discrete change**, in simple, non-technical language —
  describe what's different for the user/the simulation, not which function
  or file changed. E.g. "The world is now much bigger, with two farm areas
  instead of one" rather than "Added STARTER_DISTRICTS with farm_north/
  farm_south entries."
- Format as a short bullet list, grouped loosely by topic (world size,
  roads/movement, building, growth) so it's easy to scan.
- Skip internal implementation details (function names, file paths, data
  shapes) entirely in this summary — those belong in commit messages/PR
  descriptions, not in the user-facing recap.
