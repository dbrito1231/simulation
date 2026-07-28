# SPEC 05 — World

World geometry, districts (starter core + frontier), roads, terrain tiles, ecology, and structures.

**Canonical for:** world dimensions, districts (data shape, list, founding), road graph,
zone kinds, ecology stocks/regrow/terraform, structure registry/levels/upgrades, Path-1
terrain grid + composable blocks (mechanics), cemetery/grave grid.
**See also:** [01-architecture.md](01-architecture.md) for the flag index (semantics of
`ECOLOGY_ENABLED`/`ROADS_ENABLED`/`STRUCTURE_UPGRADES_ENABLED`/`CEMETERY_ENABLED`/
`TERRAIN_TILES_ENABLED`/`COMPOSABLE_BUILD_ENABLED` live in their owning specs);
[10-path1.md](10-path1.md) for Path-1 flag semantics (industry, tool tiers, diplomacy);
[07-actions.md](07-actions.md) for the build/terraform/block/dig actions;
[08-systems-economy.md](08-systems-economy.md) for structure decay/repair/upkeep detail.

## World geometry

`WORLD_W = 5200`, `WORLD_H = 5400` (sim_engine.py:69-70). The ~2600×2700 "starter core"
(hand-authored districts) occupies the northwest corner; everything else is open
FRONTIER territory that new districts can be founded into at runtime. `index.html`'s
`WORLD_W`/`WORLD_H` must be kept in sync with the engine's (a manual invariant, not
enforced in code).

## Districts

`civilization["districts"]` is the live, runtime-mutable dict of all districts,
cold-started from `STARTER_DISTRICTS` (sim_engine.py:90-165) and appended to by
`_maybe_found_district()` as the frontier is settled. Every runtime function reads the
live dict, never the module constant.

**Entry shape** (frozen per sim_engine.py:79-81):

| Field | Type | Meaning |
|---|---|---|
| `kind` | str | Groups districts for resource/tile purposes (e.g. two districts can share `kind: "farm"`) |
| `tile` | str | Ground tile id used by the renderer |
| `label` | str \| None | Display label (None for districts with no on-screen banner, e.g. ocean) |
| `bounds` | `{x1,y1,x2,y2}` | Pairwise non-overlapping rectangle, enforced by `_validate_districts` at import time and after any founding |
| `build_grid` | `{x0,y0,cols,dx,dy,cap}` \| None | Structure-placement grid; `None` means the district can't host build projects |
| `entryNode` | str | This district's "front door" in the road graph (`STARTER_ROAD_NODES`) |
| `grave_grid` | `{x0,y0,cols,dx,dy,cap}` (cemetery only) | Separate grid for tombstone placement, same spacing convention as `build_grid` |

**Starter districts (12, verified `STARTER_DISTRICTS` sim_engine.py:90-165):**

| id | kind | label | build_grid? |
|---|---|---|---|
| `farm_north` | farm | FARM | yes (cap 30) |
| `forest` | forest | FOREST | no |
| `village_core` | village | VILLAGE | yes (cap 30) |
| `market` | market | MARKET | no |
| `beach` | beach | BEACH | no |
| `cave_east` | cave | CAVE | no |
| `ocean` | ocean | (none) | no |
| `farm_south` | farm | FARM (SOUTH FIELDS) | yes (cap 30) |
| `village_east` | village | EAST VILLAGE | yes (cap 30) |
| `workshop_row` | workshop | WORKSHOP ROW | yes (cap 24) |
| `cave_deep` | cave | DEEP CAVE | no |
| `cemetery_grounds` | cemetery | CEMETERY | yes (cap 1) + `grave_grid` (cap 48) |

The starter coast is deliberately oversized for visible vessels: `ocean` spans
`x=0..280, y=100..900` and `beach` spans `x=290..490, y=100..900`. Restore
migration updates the former narrow coastal bounds in existing saves. The
coast remains non-buildable and the beach road gate remains on shore.

`DISTRICT_KIND_TEMPLATES` (sim_engine.py:173-178) covers only the kinds that
`_maybe_found_district()` can instantiate anew: `farm`, `village`, `workshop`, `beach`.
Forest/cave/ocean/market are single-instance by design; a founded cave would need
per-district mining logic it doesn't have (covered by `cave_deep` already existing).
`PROJECT_KIND` (sim_engine.py:185-186) maps a project type to the district `kind` it
must be built in (falls back to `village` for unlisted/custom-blueprint types).

## Frontier founding

- `FRONTIER_PLOT_W = 500`, `FRONTIER_PLOT_H = 600` (sim_engine.py:228-229): the grid
  size a new district plot is carved into.
- `CORE_RESERVED_BOUNDS = {"x1":0,"y1":0,"x2":2600,"y2":2700}` (sim_engine.py:230):
  frontier plots overlapping this rectangle are excluded (the starter core is
  reserved ground; see `_rects_overlap` check, sim_engine.py:1211).
- `MAX_TOTAL_DISTRICTS = 26` (sim_engine.py:231): a generous safety valve on total
  district count.
- `DISTRICT_FOUND_STALL_THRESHOLD = 900` frames (sim_engine.py:232): a `kind`
  qualifies for founding once `frameTick - kindLastActivityFrame[kind] >= 900` (no
  recent activity of that kind anywhere) — the stall signals real demand for more
  space of that kind. `_maybe_found_district` (sim_engine.py:7582-7596+) also checks
  `len(districts) < MAX_TOTAL_DISTRICTS` and a per-village cooldown
  (`lastDistrictFoundFrame`).
- `FOUNDING_EVENTS_ENABLED` (default True): when True, `_found_district()` pushes a
  `district_founded`-kind chronicle milestone ("`{label}` was founded on the
  frontier.") alongside the existing unconditional `_push_activity` line. Gates only
  this chronicle call — district creation itself, the road-graph extension, and
  `districtStocks` init are unaffected by the flag. No new district field was
  added: the viewer derives its founding banner (see
  [11-viewer.md](11-viewer.md)) by diffing newly-appeared `district_founded`
  chronicle entries against ones already seen, rather than from a per-district
  `foundedFrame` timestamp.

## Road network

`STARTER_ROAD_NODES` (sim_engine.py:194-207, 12 nodes) and `STARTER_ROAD_EDGES`
(sim_engine.py:208-219+, undirected `[a,b]` pairs) seed `civilization["roadNodes"]`/
`["roadEdges"]`, mutable at runtime the same way districts are (a founded district
extends the graph). `_recompute_road_paths()` (sim_engine.py:1663-1692) runs
all-pairs BFS on cold start and after any graph change, caching every
`(start,end) -> [node ids]` path in `self.ROAD_PATH_CACHE` — cheap at this graph's
size (a dozen-ish nodes even after several foundings), so it is never treated as a
one-time module-load constant. `_road_path_between(agent, dest_district_id)`
(sim_engine.py:1694-1707) resolves an agent's origin node (its current district's
`entryNode`, or the nearest road node by position) and the destination district's
`entryNode`, then looks up the cached path. Movement flag: `ROADS_ENABLED` (default
True; semantics/rendering owned here, echo status in
[01-architecture.md](01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy)).

## Zone kinds

`ZONE_NAMES = ["farm", "forest", "village", "market", "beach", "cave", "ocean",
"workshop", "cemetery"]` (sim_engine.py:234) — the fixed set of district `kind`
values the world understands. `get_zone(districts, x, y)` and
`get_district(districts, x, y)` (sim_engine.py:1218, 1229) resolve a world position
to its containing zone/district by bounds lookup.

## Ecology

Gated by `ECOLOGY_ENABLED` (default True). Each district carries a
`districtStocks[district_id][resource_id]` counter (lazily populated by
`_ensure_district_stocks`).

- **Deplete:** gathering removes `STOCK_DEPLETE_MULTIPLIER = 2` (sim_engine.py:316)
  units per unit collected (`_deplete_district_stock`, sim_engine.py:2088-2097). A
  stock hitting 0 blocks further gathering of that resource in that district
  (`_ecology_gather_gate`, sim_engine.py:2099-2116) until it regrows; yield scales
  down as stock falls below `STOCK_LOW_RATIO`, floored at `STOCK_MIN_YIELD_RATIO`.
- **Regrow:** `_tick_ecology_regrow()` (sim_engine.py:2172-2198) adds
  `STOCK_REGROW_PER_TICK` per tick to every below-cap stock. When `GOODS_ENABLED`,
  the amount is multiplied by season via `SEASON_REGROW_MULT = {"spring": 2,
  "summer": 1, "autumn": 1, "winter": 0}` (sim_engine.py:526) — winter regrowth is
  fully suppressed. Season mechanics themselves: [02-engine-core.md](02-engine-core.md).
  **Weather term (`WEATHER_GOVERNANCE_ENABLED`, living-ecosystem Phase 5):**
  extends this SAME multiplier chain per-district — never a second, parallel
  regrowth mechanism. For whichever district(s) `civilization["weather"]["districts"]`
  currently names, the (already season-scaled) per-tick amount is further
  multiplied by `WEATHER_STORM_REGROW_MULT = 0.3` while weather state is
  `"storm"` (suppression) or `WEATHER_CLEARING_REGROW_MULT = 1.5` while
  `"clearing"` (a partial rain-boosted recovery right after the storm passes,
  so weather isn't purely punitive). Every other district, and every district
  while weather is `"clear"`/`"gathering"`, is unaffected (multiplier 1). The
  multiplier is deliberately fractional and never floored to exactly 0:
  combined with `WEATHER_DWELL_TICKS` already bounding how long `"storm"`/
  `"clearing"` can last (a few minutes, see the Weather section below), a
  district always keeps inching toward recovery even mid-storm — the
  plan-mandated floor against an unrecoverable starvation spiral, achieved
  without a second bespoke duration/cap mechanism. `WEATHER_GOVERNANCE_ENABLED`
  off is byte-identical to Phase 4 alone. Reuses the exact scarcity/recovery
  narration lines above (no new logging code) — see
  [09-systems-society.md](09-systems-society.md) for the emergency-rule
  branch this same threshold check also feeds, and
  [03-cognition.md](03-cognition.md) for the one-line prompt surface.
- **Terraform:** `TERRAFORM_TEMPLATES` (sim_engine.py:820-858) — three templates,
  each funded like a build project (`needs`) and restricted to a district `kind`:
  `plant_grove` (forest; boosts wood/herbs stock ratio to 0.85), `clear_field`
  (farm; food stock ratio to 1.0), `extend_beach` (beach; fish stock ratio to 0.9,
  and can additionally found a new beach district via `found_district`). Started
  via `start_terraform`, funded via `contribute_resources`, applied by
  `_complete_terraform` (sim_engine.py:2469+), which calls
  `_apply_terraform_modifiers` to mutate district stocks per the template's
  `function.modifies` list.

**Ecology visibility projection (`districtEcology`, living-ecosystem Phase 2):**
`districtStocks` is engine-internal and never exposed to `/state` directly. When
`CROP_GROWTH_ENABLED` or `WILDLIFE_ENABLED` is True (both default True),
`snapshot()` adds a compact read-only top-level `districtEcology` list —
`[{districtId, stage, ratio}]` — the same placement as `socialTies`/`chronicle`
(a sibling of `civilization`, not nested inside it). One entry per district
that has ecology stocks: farm/forest/beach/cave kinds (their primary gathered
resource) **and village kinds** (the `water` resource's `gatherZone` is
`"village"`); market/workshop/cemetery/ocean kinds have no gatherable
resource and are omitted. Omitted entirely when both flags are off.

- `ratio` (`_district_ecology_ratio`, sim_engine.py) is the average
  `min(1.0, stock/STOCK_DEFAULT_MAX)` across that district's gatherable
  resources — the same per-resource ratio `_resource_price` and
  `_ecology_scarcity_index` already compute, just scoped to one district.
- `stage` quantizes `ratio` into `barren` / `sparse` / `healthy` / `lush`
  (`DISTRICT_ECOLOGY_STAGES`), reusing the exact boundaries
  `_format_district_stocks_for_prompt` already narrates to agents
  (`ratio <= 0` → barren/depleted, `< STOCK_LOW_RATIO` → sparse/low, `< 0.5` →
  healthy/fair, else lush/ok) — the viewer's stage always matches what the
  engine tells the LLM.
- **Hysteresis:** `DISTRICT_ECOLOGY_HYSTERESIS = 0.05`. The previous stage
  index is persisted per district in `civilization["districtEcologyStage"]`
  (survives restore); a stage only moves one step, and only once `ratio`
  clears the relevant boundary by the hysteresis margin
  (`_district_ecology_stage_with_hysteresis`). This exists because the viewer
  keys its terrain-cache rebuild off `stage` (see
  [11-viewer.md](11-viewer.md)) — without the margin, a ratio sitting exactly
  on a boundary could rebuild the cache every ~100ms poll.
- The projection is computed inside `snapshot()` itself (no new engine tick);
  it is cheap (one pass over already-in-memory `districtStocks`) and safe to
  recompute on every poll since hysteresis state is idempotent between calls.
- Consumers: viewer crop/tree growth-stage terrain and ambient-wildlife
  density both key off this same `stage`, per district — see
  [11-viewer.md](11-viewer.md).

## Structures

`civilization["structures"]` is a flat list of built structure instances
(`{id, type, districtId, condition, level, visualTier, renderScale, isRuin, ...}`,
sim_engine.py:3786-3808). Structure *types* are declared once via two registries:

- `PROJECT_TEMPLATES` (sim_engine.py:754-765, extended by flag-gated blocks like
  `granary` under `CRAFTING_ENABLED`, `kiln`/`harbor`/`mill`/`foundry` under Path-1
  industry flags): `{name, needs: {resource: qty}, visualStyle[, tier]}` — this is
  the build-cost recipe, consumed by `start_project`/`contribute_resources`.
- `SEED_STRUCTURE_FUNCTIONS` (sim_engine.py:769-817+): each built type's mechanical
  effect vector — `houses` (population-cap contribution), `boosts` (gather/craft
  yield bonuses), `produces` (periodic resource generation), `unlocks` (craft
  stations), `stores` (storage capacity, `GOODS_ENABLED` only). Custom blueprints
  supply their own `function` block at proposal time (see
  [07-actions.md](07-actions.md#the-build-pipeline)).

**Condition/ruin transitions** (decay, disrepair, ruin — the `condition`,
`isRuin`, and `homeOf`/`homeStructureId` fields above) go through one shared
helper, `_apply_structure_condition_delta(structure, delta)`, extracted
specifically so the passive per-goods-tick decay (`_tick_structure_decay`,
always a negative delta) and the Sovereign God mode `structure_condition`
miracle (repair with a positive delta, damage with a negative one) fire
identical `STRUCTURE_DISREPAIR_THRESHOLD`-crossing and ruin-transition
narration — including clearing `homeOf`/`homeStructureId` and the
"left homeless" line when a structure someone lives in collapses into a
ruin — rather than the miracle taking a parallel shortcut. See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles)
and [08-systems-economy.md](08-systems-economy.md) (the "Structure decay"
row) for the full decay-rate/threshold/repair-cost contract this reuses.

**Levels/upgrades:** gated by `STRUCTURE_UPGRADES_ENABLED` (default True).
`MAX_STRUCTURE_LEVEL = 100` (sim_engine.py:277); `LEVEL_STEP = 1` per
`upgrade_structure` call (sim_engine.py:278). `structure["visualTier"]` (1-3,
sim_engine.py:3451, 3548) drives which of up to 3 sprite render variants is shown,
distinct from numeric `level`. `structure["renderScale"]` grows with level for a
visible size cue. Decay (`condition` 0-100, disrepair threshold, ruin collapse) and
`repair_structure` restore mechanics: [08-systems-economy.md](08-systems-economy.md).

## Path-1 terrain grid + composable blocks

Mechanics only — flag semantics (`TERRAIN_TILES_ENABLED`, `COMPOSABLE_BUILD_ENABLED`)
are owned by [10-path1.md](10-path1.md).

- **Grid:** each district has a fixed `PATH1_GRID_COLS = 8` × `PATH1_GRID_ROWS = 8`
  cell grid (sim_engine.py:1014-1015) at `TILE_CELL = 40` px per cell
  (sim_engine.py:986). `_pos_to_grid(agent)` (sim_engine.py:3917-3926) converts an
  agent's world position to a clamped `(gx, gy)` in its current district.
- **Terrain layer** (`district["terrain"][gx,gy] -> kind`): lazily initialized by
  `_ensure_district_terrain` to a per-kind default (`forest`→`grove`, `farm`→`soil`,
  `beach`→`sand`, `cave`→`rock`, `ocean`→`water`, else `soil`). `dig_terrain` and
  `plant_terrain` (`_dig_terrain`/`_plant_terrain`, sim_engine.py:4082, 4150) mutate
  individual cells; `_find_nearby_terrain` does a bounded scan for the nearest cell
  of a given kind.
- **Composable/build layer** (`district["tiles"][gx,gy] -> block_type`), capped at
  `TILE_CAP_PER_DISTRICT = 200` (sim_engine.py:987) per district. `BLOCK_TYPES`
  (sim_engine.py:1001-1006): `wall` (1 wood, shelter), `floor` (1 wood, no shelter),
  `door` (2 wood, no shelter), `fence` (1 wood, shelter). `place_block`/
  `remove_block` (`_place_block` sim_engine.py:4011-4047, `_remove_block`) charge/
  refund the block's resource cost and reject on unknown type, out-of-district,
  tile-cap, or occupied-cell. Shelter blocks count toward night-exposure protection
  (`NIGHT_EXPOSURE_DAMAGE`) alongside houses — see [10-path1.md](10-path1.md).

## Weather (`WEATHER_ENABLED`, living-ecosystem Phase 4)

A deterministic, LLM-free state machine advanced on the existing
`GOODS_TICK_FRAMES = 900` (~30s) cadence, called from `_tick_goods()`
(`_tick_weather`, sim_engine.py) — the same tick that already hosts spoilage,
structure decay, and the disaster roll. **No new timer.**

- **States** (`WEATHER_STATES`): `clear -> gathering -> storm -> clearing ->
  clear`. `clear` always advances to `gathering`; `gathering` resolves to
  either `storm` or straight back to `clear` (a storm that didn't
  materialize); `storm` always advances to `clearing`; `clearing` always
  advances to `clear`.
- **Dwell durations** (`WEATHER_DWELL_TICKS`, in `GOODS_TICK_FRAMES` units,
  drawn via `random.randint` when a state is entered): `clear` 40-160
  (season-scaled, see below), `gathering` 2-5, `storm` 2-6, `clearing` 2-4.
  The drawn dwell is stored as an absolute `exitFrame`; `_tick_weather` only
  checks `frameTick >= exitFrame` and transitions at most once per call — no
  per-tick probability roll for "are we still in this state."
- **Season-weighted:** `WEATHER_SEASON_STORMINESS = {spring: 1.1, summer:
  0.6, autumn: 1.3, winter: 1.0}` (reuses `_current_season()`). For `clear`,
  the dwell range is divided by storminess (stormier season -> shorter gap
  between storm attempts). The `gathering -> storm` probability is
  `clip(WEATHER_BASE_STORM_CHANCE(=0.5) * storminess, 0.05, 0.95)`. The four
  multipliers are chosen to average exactly 1.0 across the four
  equal-length seasons, which is what keeps the long-run damage rate (see
  [08-systems-economy.md](08-systems-economy.md)) close to the legacy
  baseline without per-season recalibration.
- **Locality:** entering `storm` picks 1 (usually) or 2 districts at random
  (`civilization["weather"]["districts"]`) and narrates "Storm clouds break
  over {districts}." `_maybe_disaster` (see 08) prefers a structure inside
  those districts, so the fiction and the damage target agree. The sky tint
  and particle effects (see [11-viewer.md](11-viewer.md)) are, for v1,
  world-wide rather than clipped to those districts — a documented
  simplification, not an oversight.
- **Persistence + restore:** `civilization["weather"] = {state, since,
  exitFrame, districts}`. Cold start seeds it via `_weather_default(0)`
  (`"clear"` from frame 0). `restore_state()` backfills a missing key with
  the same default via `civ.setdefault("weather", ...)` — the precedent used
  by `lastRuleAttemptFrame`/`priorityRuleSeq`/`taxRuleSeq` — which means an
  old (pre-Phase-4) save starts at `"clear"` on its first post-restore goods
  tick, but a save that **already has** real weather state is left
  completely untouched (no re-roll of in-progress weather on load).
- **`/state` projection:** when `WEATHER_ENABLED`, `snapshot()` adds a
  top-level `weather: {state, since, districts}` (a sibling of
  `civilization`, same placement as `socialTies`/`districtEcology`/
  `shipments`). `since`/`districts` are exposed for the viewer's narration
  and locality use; `exitFrame` (an internal scheduling detail) is not.
- **Gate:** `WEATHER_ENABLED = False` — `_tick_weather()` is a complete
  no-op (the `weather` key is never read or mutated further after cold
  start/restore), `/state` omits `weather`, and `_maybe_disaster` reverts to
  its pre-Phase-4 behavior (see 08).

### Divine weather override (Sovereign God mode Phase 6, `weather_override`)

A `god_mode` intervention kind (see [03-cognition.md](03-cognition.md) for the
preview/apply/cancel envelope shared by every divine command) that forces the
natural weather machine above into an operator-chosen `state` + `districts`
for a bounded duration, then hands control back to the natural cycle. Four
behaviors, all enforced in `sim_engine.py`:

- **Event-authoritative clock.** `godState["activeEvents"][].expiresFrame` is
  the single source of truth for when the override ends.
  `_god_apply_weather_override` sets `civilization["weather"]["exitFrame"]`
  to that **same** absolute frame, so `_tick_weather`'s existing
  `frameTick < exitFrame` early-return defers to the override automatically —
  there is only one clock value, read by both the natural machine and the
  override, so it cannot drift out of sync.
- **RNG-free forced entry.** `_weather_enter_forced(state, districts,
  exit_frame)` sets `state`/`since`/`exitFrame`/`districts` directly from the
  already-validated command and draws no RNG at all — no `random.randint`,
  no `random.sample` — and emits the same narration `_weather_enter` emits
  for that state, so a forced storm/clearing reads identically to a natural
  one in activity. `_weather_enter` itself (the RNG-driven natural-cycle
  entry point) is untouched and only ever called from the natural
  `_tick_weather` cycle or from the handoff below.
- **Handoff to the natural cycle's successor, not back to the pre-override
  state.** Ending an override — via expiry (`_expire_divine_effects`), cancel
  (`god_cancel`), or a `replaceEffectId` replacement — always calls
  `_close_weather_override(event, status)`, which closes the
  `weather_override` `activeEvents` record exactly once and then calls
  `_weather_handoff_successor(event["state"])`: the natural-cycle successor
  of the **overridden** state (`clear -> gathering`, `gathering -> storm` or
  `clear` per the normal probability roll, `storm -> clearing`, `clearing ->
  clear`), entered through the same RNG-drawing `_weather_enter`. The
  override's `priorState` is recorded on the event for audit only and is
  never restored — restoring it would double back and desync the strict
  cycle.
- **Consequential reversibility.** `_god_reversibility_class` reports
  `weather_override` as `"consequential"`, not merely `"cancellable"`:
  entering `"storm"` can trigger real, permanent structure damage through the
  normal `_maybe_disaster` path, and neither cancelling the override nor
  letting it expire retracts that damage. `_god_preview_outcome` discloses
  this explicitly — the target `state`/`districts`, the count of currently
  non-ruined structures at risk in those districts, and (for `state ==
  "storm"`) a warning that any damage dealt stands regardless of how the
  override ends.

Validation (`_validate_god_weather_override`) requires `WEATHER_ENABLED`,
`state` to be one of `WEATHER_STATES`, real district ids (empty only for
`"clear"`, at least one required for every other state — matching what
`_weather_enter` itself does for each), and enforces "one active weather
override at a time" unless `replaceEffectId` names the currently active one
(`_god_active_weather_override`) — the same one-active-per-slot discipline
`story_event`'s `replaceEffectId` uses for a modifier key. Replacing closes
the previous override's `activeEvents` record with status `"replaced"` (see
[12-ops.md](12-ops.md) for the full status vocabulary). An active override's
`expiresFrame` is absolute and round-trips `save_state`/`restore_state`
unchanged; a save captured after an override's clock ran out but before the
next `_expire_divine_effects` sweep closes it, and hands off, exactly once on
restore (status `"restore-closed"`).

## Cemetery + grave grid

Gated by `CEMETERY_ENABLED` (default True). The `cemetery_grounds` district's
`grave_grid` (48 slots, same `{x0,y0,cols,dx,dy}` spacing convention as `build_grid`)
holds tombstone positions distinct from its 1-slot `build_grid` (the Cemetery
structure itself). `_grave_grid_position(district_id, index)`
(sim_engine.py:5300+) resolves a burial slot; `_bury_agent_at` (sim_engine.py:5328+)
assigns the next free slot to a corpse via the `bury_agent` action
([07-actions.md](07-actions.md)). A working cemetery structure (not disrepaired)
is required before burial succeeds; a district with `kind == "cemetery"` bypasses
the normal `PROJECT_KIND` build-district resolution.
