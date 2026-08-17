# SPEC 05 — World

World geometry, districts (starter core + frontier), roads, terrain tiles, ecology, and structures.

**Canonical for:** world dimensions, districts (data shape, list, founding), road graph,
zone kinds, ecology stocks/regrow/terraform, structure registry/levels/upgrades, Path-1
terrain grid + composable blocks (mechanics), cemetery/grave grid.
**See also:** [01-architecture.md](01-architecture.md) for the flag index (semantics of
`ECOLOGY_ENABLED`/`STRUCTURE_UPGRADES_ENABLED`/
`PATH1_ENABLED` (terrain tiles/composable blocks)/`WILDLIFE_ENABLED` live in their
owning specs); [10-path1.md](10-path1.md) for Path-1 flag semantics (industry, tool
tiers, diplomacy); [07-actions.md](07-actions.md) for the build/terraform/block/dig/
hunt actions; [08-systems-economy.md](08-systems-economy.md) for structure
decay/repair/upkeep detail and hunt yields.

## World geometry

`WORLD_W = 5200`, `WORLD_H = 5400` (sim_engine/constants.py:860-861). The ~2600×2700 "starter core"
(hand-authored districts) occupies the northwest corner; everything else is open
FRONTIER territory that new districts can be founded into at runtime. `index.html`'s
`WORLD_W`/`WORLD_H` must be kept in sync with the engine's (a manual invariant, not
enforced in code).

## Districts

`civilization["districts"]` is the live, runtime-mutable dict of all districts,
cold-started from `STARTER_DISTRICTS` (sim_engine/constants.py:881-956) and appended to by
`_maybe_found_district()` as the frontier is settled. Every runtime function reads the
live dict, never the module constant.

**Entry shape** (frozen per sim_engine/constants.py:870-872):

| Field | Type | Meaning |
|---|---|---|
| `kind` | str | Groups districts for resource/tile purposes (e.g. two districts can share `kind: "farm"`) |
| `tile` | str | Ground tile id used by the renderer |
| `label` | str \| None | Display label (None for districts with no on-screen banner, e.g. ocean) |
| `bounds` | `{x1,y1,x2,y2}` | Pairwise non-overlapping rectangle, enforced by `_validate_districts` at import time and after any founding |
| `build_grid` | `{x0,y0,cols,dx,dy,cap}` \| None | Structure-placement grid; `None` means the district can't host build projects |
| `entryNode` | str | This district's "front door" in the road graph (`STARTER_ROAD_NODES`) |
| `grave_grid` | `{x0,y0,cols,dx,dy,cap}` (cemetery only) | Separate grid for tombstone placement, same spacing convention as `build_grid` |

**Starter districts (12, verified `STARTER_DISTRICTS` sim_engine/constants.py:881-956):**

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

`DISTRICT_KIND_TEMPLATES` (sim_engine/constants.py) covers the single-plot kinds that
`_maybe_found_district()` can instantiate anew: `farm`, `village`, `workshop`.
Beach expansion uses **coastal pairs** instead (see below) — standalone beach is not
in `DISTRICT_KIND_TEMPLATES`. `COASTAL_PAIR_BEACH_TEMPLATE` and
`OCEAN_DISTRICT_TEMPLATE` supply the west-water / east-sand pair geometry.
Forest/cave/market starter districts are not frontier-founded; a founded cave would need
per-district mining logic it doesn't have (covered by `cave_deep` already existing).
Founded `ocean` districts are allowed when paired with an adjacent founded `beach` on
the frontier (starter `ocean` + `beach` remain the canonical west-coast pair).
`PROJECT_KIND` (sim_engine/constants.py:976-977) maps a project type to the district `kind` it
must be built in (falls back to `village` for unlisted/custom-blueprint types).

## Frontier founding

- `FRONTIER_PLOT_W = 500`, `FRONTIER_PLOT_H = 600` (sim_engine/constants.py:1019-1020): the grid
  size a new district plot is carved into.
- `CORE_RESERVED_BOUNDS = {"x1":0,"y1":0,"x2":2600,"y2":2700}` (sim_engine/constants.py:1021):
  frontier plots overlapping this rectangle are excluded (the starter core is
  reserved ground; see `_rects_overlap` check, sim_engine/helpers.py:124).
- `MAX_TOTAL_DISTRICTS = 26` (sim_engine/constants.py:1022): a generous safety valve on total
  district count.
- `DISTRICT_FOUND_STALL_THRESHOLD = 900` frames (sim_engine/constants.py:1023): a `kind`
  qualifies for founding once `frameTick - kindLastActivityFrame[kind] >= 900` (no
  recent activity of that kind anywhere) — the stall signals real demand for more
  space of that kind. `_maybe_found_district` (sim_engine/mixin_council_growth.py:1468) also checks
  `len(districts) < MAX_TOTAL_DISTRICTS` and a per-village cooldown
  (`lastDistrictFoundFrame`).
- `FOUNDING_EVENTS_ENABLED` (default True): when True, `_found_district()` pushes a
  `district_founded`-kind chronicle milestone ("`{label}` was founded on the
  frontier.") alongside the existing unconditional `_push_activity` line. Gates only
  this chronicle call — district creation itself, the road-graph extension, and
  `districtStocks` init are unaffected by the flag. Ocean districts (`label: null`)
  skip the chronicle line. No new district field was
  added: the viewer derives its founding banner (see
  [11-viewer.md](11-viewer.md)) by diffing newly-appeared `district_founded`
  chronicle entries against ones already seen, rather than from a per-district
  `foundedFrame` timestamp.

### Coastal pair founding (beach expansion)

When beach districts are at capacity and stall, or when `extend_beach` terraform
completes with `found_coastal_pair: true`, the engine claims **two adjacent
unclaimed frontier plots** and founds `ocean_N` (west/north water) plus `beach_N`
(east/south sand) in one operation:

- `_claim_adjacent_frontier_pair()` (sim_engine/mixin_council_growth.py) scans
  `frontierPlots` for edge-sharing unclaimed pairs. **Horizontal pairs are
  preferred** (lower `x1` = ocean, higher `x1` = beach — same west-water /
  east-sand read as the starter coast). **Vertical fallback:** lower `y1` =
  ocean (north water), higher `y1` = beach (south sand). If no pair exists, founding
  is skipped and a one-time activity line is logged.
- `_found_coastal_pair()` (sim_engine/mixin_council_growth.py) calls
  `_found_district()` for the beach plot (`COASTAL_PAIR_BEACH_TEMPLATE`: cols 3,
  cap 18) then the ocean plot (`OCEAN_DISTRICT_TEMPLATE`: `tile: ocean`,
  `label: null`, no `build_grid`). The ocean district reuses the beach plot's
  `{beach_N}_gate` as `entryNode` (no duplicate road node) — matching starter
  geometry where shore access is on the sand plot only.
- `_maybe_found_district()` runs the coastal-pair path when every beach district
  with a `build_grid` is full and beach-kind activity has stalled (same
  `DISTRICT_FOUND_STALL_THRESHOLD` / `lastDistrictFoundFrame` gates as other kinds).
  Pair founding requires room for **two** new districts (`len(districts) + 2 <=
  MAX_TOTAL_DISTRICTS`).
- Inland sand-only founding (single plot → lone `beach_N` without adjacent
  `ocean_N`) is **removed** — terraform `extend_beach` and the beach backstop both
  use coastal pairs only.

### Restore migration (inland-founded beaches)

On `restore_state()` (`sim_engine/mixin_persistence.py`), after civilization
and agents are rehydrated and before road-path recompute / district validation,
`_revert_inland_founded_beaches()` (`sim_engine/mixin_world_state.py`) removes
legacy inland coast districts from older saves:

- **Revert** any founded `beach_N` (`did != "beach"`) with **no edge-adjacent**
  `ocean` district (same edge-sharing geometry as `_claim_adjacent_frontier_pair` /
  `_district_bounds_share_edge`).
- **Revert** orphan founded `ocean_N` (`did != "ocean"`) with no edge-adjacent
  `beach` district (incomplete coastal pair).
- **Starter `beach` + `ocean` are never touched.** Valid adjacent founded pairs
  survive.

Per reverted district: unclaim the matching `frontierPlots[].claimedBy` plot
(back to grass), remove district records (`districts`, `districtProjects`,
`districtStocks`, `districtEcologyStage`, `districtWildlifeStage`,
`districtLastContribution`), drop
`{did}_gate` from the road graph when present, relocate structures to another
district of the same `PROJECT_KIND` kind or drop them (one activity line),
reassign agents off the removed district via nearest road node / village-or-beach
fallback, remove wildlife tied to the district, then `_recompute_road_paths()`,
`_validate_districts()`, and `_bump_districts_epoch()`.

## Road network

`STARTER_ROAD_NODES` (sim_engine/constants.py:985-998, 12 nodes) and `STARTER_ROAD_EDGES`
(sim_engine/constants.py:999-1011, undirected `[a,b]` pairs) seed `civilization["roadNodes"]`/
`["roadEdges"]`, mutable at runtime the same way districts are (a founded district
extends the graph). `_recompute_road_paths()` (sim_engine/mixin_world_state.py:523) runs
all-pairs BFS on cold start and after any graph change, caching every
`(start,end) -> [node ids]` path in `self.ROAD_PATH_CACHE` — cheap at this graph's
size (a dozen-ish nodes even after several foundings), so it is never treated as a
one-time module-load constant. `_road_path_between(agent, dest_district_id)`
(sim_engine/mixin_world_state.py:554) resolves an agent's origin node (its current district's
`entryNode`, or the nearest road node by position) and the destination district's
`entryNode`, then looks up the cached path. Road-node routing is unconditional
for general travel (idle wander, craft-station redirects, move_to_district);
Sage-emergency rescue and short local hops (move_to_agent, trade, talk) always
stay direct.

## Inter-settlement movement (ocean corridor)

When `PATH1_ENABLED` and `_has_ocean_transit()`
are both true, **caravan goals only** that travel between different
`settlementId`s may route through a bounded ocean corridor instead of
road-only paths:

1. Leave the source settlement via its dock or shipyard district (working
   structure with a `transit`/`ocean` unlock).
2. Traverse ocean waypoint(s) on the road graph (no free-swim for ordinary
   `move_to_district`, gather, or non-caravan goals).
3. Enter the destination settlement at its dock/district.

Transit cost is consumed once per crossing via `_consume_ocean_transit`
([10-path1.md](10-path1.md#ocean-transit)). `_set_agent_target_once` /
`_step_goal` for `kind: "caravan"` selects the corridor when the destination
district's settlement differs from the actor's. Shipment visuals use
`mode: "boat"` under the same boundary signal
([08-systems-economy.md](08-systems-economy.md#caravan_visuals_enabled)).
No persistent vehicle entities are spawned — movement remains agent-centric.

**Faction split (`FACTION_SPLIT_ENABLED`):** domestic governance law may diverge per
settlement when enabled; secession reuses `_claim_frontier_plot` /
`_found_district` (or an existing frontier settlement). Inter-settlement
trade/treaty/tariff surfaces stay global. See
[09-systems-society.md](09-systems-society.md#faction_split_enabled).

## Zone kinds

`ZONE_NAMES = ["farm", "forest", "village", "market", "beach", "cave", "ocean",
"workshop", "cemetery"]` (sim_engine/constants.py:1025) — the fixed set of district `kind`
values the world understands. `get_zone(districts, x, y)` (sim_engine/helpers.py:226) and
`get_district(districts, x, y)` (sim_engine/helpers.py:237) resolve a world position
to its containing zone/district by bounds lookup.

## Ecology

Gated by `ECOLOGY_ENABLED` (default True). Each district carries a
`districtStocks[district_id][resource_id]` counter (lazily populated by
`_ensure_district_stocks`).

- **Deplete:** gathering removes `STOCK_DEPLETE_MULTIPLIER = 2` (sim_engine/constants.py:1110)
  units per unit collected (`_deplete_district_stock`, sim_engine/mixin_world_state.py:1030). A
  stock hitting 0 blocks further gathering of that resource in that district
  (`_ecology_gather_gate`, sim_engine/mixin_world_state.py:1041) until it regrows; yield scales
  down as stock falls below `STOCK_LOW_RATIO`, floored at `STOCK_MIN_YIELD_RATIO`.
- **Regrow:** `_tick_ecology_regrow()` (sim_engine/mixin_world_state.py:1114) adds
  `STOCK_REGROW_PER_TICK` per tick to every below-cap stock. When `GOODS_ENABLED`,
  the amount is multiplied by season via `SEASON_REGROW_MULT = {"spring": 2,
  "summer": 1, "autumn": 1, "winter": 0}` (sim_engine/constants.py:1453) — winter regrowth is
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
  district always keeps inching toward recovery even mid-storm.
  `WEATHER_GOVERNANCE_ENABLED`
  off is byte-identical to Phase 4 alone. Reuses the exact scarcity/recovery
  narration lines above (no new logging code) — see
  [09-systems-society.md](09-systems-society.md) for the emergency-rule
  branch this same threshold check also feeds, and
  [03-cognition.md](03-cognition.md) for the one-line prompt surface.
- **Terraform:** `TERRAFORM_TEMPLATES` (sim_engine/constants.py:1793-1831) — three templates,
  each funded like a build project (`needs`) and restricted to a district `kind`:
  `plant_grove` (forest; boosts wood/herbs stock ratio to 0.85), `clear_field`
  (farm; food stock ratio to 1.0), `extend_beach` (beach; fish stock ratio to 0.9,
  and can additionally found a new **coastal pair** — adjacent `ocean` + `beach`
  frontier plots — via `found_coastal_pair`). Started
  via `start_terraform`, funded via `contribute_resources`, applied by
  `_complete_terraform` (sim_engine/mixin_world_state.py:1514), which calls
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

- `ratio` (`_district_ecology_ratio`, sim_engine/mixin_world_state.py:1167) is the average
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
- Consumers: viewer crop/tree growth-stage terrain keys off this `stage` per
  district. Huntable-wildlife spawn caps use the same stage for farm/forest;
  beach fauna uses fish-only ratio with hysteresis in
  `districtWildlifeStage` — see [11-viewer.md](11-viewer.md).

## Huntable wildlife (`WILDLIFE_ENABLED`)

`WILDLIFE_ENABLED` (default True; flag index [01-architecture.md](01-architecture.md))
gates the **server-authoritative** fauna subsystem: engine-owned creature
state in `civilization["wildlife"]`, every-tick motion (`_move_wildlife`),
slower spawn/respawn/migration (`_tick_huntable_wildlife`), the agent action
`hunt_wildlife`, hunter specialty yields (`meat` / `fish`), and the viewer
`/state` `wildlife[]` projection. Off → no fauna state, no hunt in
`available_actions`, viewer draw no-ops.

This is **not** Path-1's `_tick_wildlife` forest-attack pressure event
(gated by `PATH1_ENABLED` — [10-path1.md](10-path1.md)); the names
are adjacent but the systems are unrelated.

Full tick/combat/migration/god-kind/behavior-state-machine detail:
[02-engine-core.md](02-engine-core.md#huntable-wildlife-wildlife_enabled).
Yield table and `meat` edible:
[08-systems-economy.md](08-systems-economy.md#huntable-wildlife-yields-wildlife_enabled).
Viewer contract: [11-viewer.md](11-viewer.md#ambient-wildlife-wildlife_enabled).
Action: [07-actions.md](07-actions.md). Role: [06-agents.md](06-agents.md).

Per-district wildlife spawn caps (`WILDLIFE_STAGE_COUNT` /
`WILDLIFE_CAP_PER_DISTRICT = 4`) follow `districtEcologyStage` on farm/forest
(same ratio as `districtEcology`); beach uses fish-only ratio in
`districtWildlifeStage`. Fauna population is separate from `districtStocks`
(killing a creature does not decrement ecology gather stocks).

## Structures

`civilization["structures"]` is a flat list of built structure instances
(`{id, type, districtId, condition, level, visualTier, renderScale, isRuin, ...}`,
built by `_build_active_structure`, sim_engine/mixin_structures_economy.py:1702-1739). In-memory and full `/state` snapshots include an
optional `sprite` grid per structure when present; delta `/state` upserts omit
`sprite` unless the structure was created, visually upgraded, or received a
custom `submit_structure_sprite` since the client's last applied frame (the
viewer merges partial rows and keeps prior sprites). On disk (`state.db`), sprite
grids are stored in the `structure_sprites` table, not embedded in the civ
JSON blob — see [02-engine-core.md](02-engine-core.md#persistence).

- `PROJECT_TEMPLATES` (sim_engine/constants.py:1727-1738, extended by flag-gated blocks like
  `granary` under `CRAFTING_ENABLED`, `kiln`/`harbor`/`mill`/`foundry` under Path-1
  industry flags): `{name, needs: {resource: qty}, visualStyle[, tier]}` — this is
  the build-cost recipe, consumed by `start_project`/`contribute_resources`.
- `SEED_STRUCTURE_FUNCTIONS` (sim_engine/constants.py:1742-1791): each built type's mechanical
  effect vector — `houses` (population-cap contribution), `boosts` (gather/craft
  yield bonuses), `produces` (periodic resource generation), `unlocks` (craft
  stations), `stores` (storage capacity, `GOODS_ENABLED` only), `mitigates`
  (`RAIDERS_CONTAGION_ENABLED` — wall raid defense), `heals`
  (`RAIDERS_CONTAGION_ENABLED` — clinic contagion recovery). Custom blueprints
  supply their own `function` block at proposal time (see
  [07-actions.md](07-actions.md#the-build-pipeline)).

**Condition/ruin transitions** (decay, disrepair, ruin — the `condition`,
`isRuin`, and `homeOf`/`homeStructureId` fields above) go through one shared
helper, `_apply_structure_condition_delta(structure, delta)`, extracted
specifically so the passive per-goods-tick decay (`_tick_structure_decay`,
always a negative delta), Sovereign God mode `structure_condition`
miracle (repair with a positive delta, damage with a negative one), and
**raid structure damage** (`RAIDERS_CONTAGION_ENABLED` —
`-RAID_STRUCTURE_DAMAGE` toward the existing ruin threshold) fire
identical `STRUCTURE_DISREPAIR_THRESHOLD`-crossing and ruin-transition
narration — including clearing `homeOf`/`homeStructureId` and the
"left homeless" line when a structure someone lives in collapses into a
ruin — rather than any parallel shortcut. See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles)
and [08-systems-economy.md](08-systems-economy.md#raiders_contagion_enabled)
for the full decay-rate/threshold/repair-cost contract this reuses.

**Wall structure type (`type == "wall"`).** A village-scale structure built via
`start_project`/`build_structure` (not the unrelated Path-1 composable tile
`BLOCK_TYPES["wall"]` — individual grid blocks with `shelter: True` in
[10-path1.md](10-path1.md)). Seed tables today:

- `PROJECT_TEMPLATES["wall"]`: buildable project, needs `{"stone": 3, "gold": 1}`.
- `PROJECT_ORDER` includes `"wall"`.
- `SEED_STRUCTURE_FUNCTIONS["wall"]` (existing): `{"produces": [{"resource": "stone", "amount": 1, "every_ticks": 1800, "scope": "village"}]}` —
  passive stone production, gated by `STRUCTURE_EFFECTS_ENABLED`.
- **Raid defense (new, `RAIDERS_CONTAGION_ENABLED`):** the same function block
  gains an additional `"mitigates"` effect kind (orthogonal to `"produces"`):
  `"mitigates": [{"kind": "raid", "amount": RAID_WALL_MITIGATION, "scope": "district"}]`
  with `RAID_WALL_MITIGATION = 0.20`. A standing, non-ruined (`condition >=
  STRUCTURE_DISREPAIR_THRESHOLD`, not `isRuin`) `wall` in the raid's targeted
  district contributes flat raid mitigation — see
  [08-systems-economy.md](08-systems-economy.md#raiders_contagion_enabled).

**Levels/upgrades:** gated by `STRUCTURE_UPGRADES_ENABLED` (default True).
`MAX_STRUCTURE_LEVEL = 100` (sim_engine/constants.py:1070); `LEVEL_STEP = 1` per
`upgrade_structure` call (sim_engine/constants.py:1071). `structure["visualTier"]` (1-3,
initial value at sim_engine/mixin_structures_economy.py:1713/1729, updated by `_apply_visual_tier` at :1436-1437) drives which of up to 3 sprite render variants is shown,
distinct from numeric `level`. `structure["renderScale"]` grows with level for a
visible size cue. Decay (`condition` 0-100, disrepair threshold, ruin collapse) and
`repair_structure` restore mechanics: [08-systems-economy.md](08-systems-economy.md).

**Sprite-design turn at a visual-tier upgrade.** When an `upgrade_structure`
call crosses a visual-tier boundary, `_upgrade_structure` (sim_engine/mixin_structures_economy.py:1486)
compares the structure's current sprite dimensions against the schema-level
`SPRITE_GRID_MAX` (14, [03-cognition.md](03-cognition.md#structured-output)):
if the sprite is already at the cap in **both** rows and columns, no
`spriteDesignTurn` is issued at all — the procedural tier sprite from
`_apply_visual_tier` stands, since asking for a bigger grid is unsatisfiable
and a same-size redraw would burn an LLM turn for no visual change. If only
one dimension is at cap, that dimension's `minRows`/`minCols` is set to `0`
(no growth required on that axis) while the other keeps its normal "must grow"
minimum; below cap on both axes, behavior is unchanged from before. A
sprite-design turn now expires: the shared `SimEngine._count_sprite_design_failure`
helper counts attempts and, at `SPRITE_DESIGN_MAX_ATTEMPTS = 3` (sim_engine/constants.py:1075)
failed think cycles, clears the pending turn, keeps the existing (procedural)
sprite, and logs "`<name> gave up refining the sprite for the <structure>`" —
previously a rejected design turn was re-offered every think cycle
indefinitely. An attempt is counted for **any** think cycle where the turn
fails to produce an applied `submit_structure_sprite`, not only an
`_apply_structure_sprite` rejection from `validate_sprite_block`/the
degenerate-sprite check: `apply_decision` also counts the case where the
decision's action comes back as something other than
`submit_structure_sprite` entirely — most commonly a sprite reply rejected
server-side by `normalize_decision()` in server.py and replaced with a
_fallback-stamped role fallback action (the missing-case check requires
`decision["_fallback"]`; infra/network rests from `_think_job` such as bare
`{"action": "rest"}` with no `_fallback` stamp do not count), which
previously left the turn untouched and re-prompted for the same sprite
forever. That missing-case check only fires
when the turn captured at the start of `apply_decision` is still the exact
same object afterward, so it can't double-count a turn `_apply_structure_sprite`
already handled or a turn cancelled for an unrelated reason (e.g. active voice
guidance clears `spriteDesignTurn` in `_build_think_payload` before the prompt
is even built, so that cycle's decision never reaches this check with a
pending turn to count).

**Restore-time sanitation for pre-fix turns.** `restore_state` (sim_engine/mixin_persistence.py:163)
re-applies the same cap rule above to every restored agent's
`spriteDesignTurn`, healing worlds saved before `_upgrade_structure` learned
to zero out a capped dimension: if a restored turn's `minRows` and `minCols`
are both at/over `SPRITE_GRID_MAX`, the turn is unsatisfiable and is cleared
silently (no activity line — this is load-time housekeeping, not an in-world
event); if only one dimension is at/over cap, that dimension's minimum is
rewritten to `0` to match what a freshly issued turn would carry. Malformed
restored turns (non-dict, or with non-numeric `minRows`/`minCols`) are
dropped defensively rather than raising; missing `minRows`/`minCols` default
to `0`, same as a freshly issued turn.

## Path-1 terrain grid + composable blocks

Mechanics only — flag semantics (`PATH1_ENABLED`) are owned by
[10-path1.md](10-path1.md).

- **Grid:** each district has a fixed `PATH1_GRID_COLS = 8` × `PATH1_GRID_ROWS = 8`
  cell grid (sim_engine/constants.py:2019-2020) at `TILE_CELL = 40` px per cell
  (sim_engine/constants.py:1987). `_pos_to_grid(agent)` (sim_engine/mixin_diplomacy.py:67) converts an
  agent's world position to a clamped `(gx, gy)` in its current district.
- **Terrain layer** (`district["terrain"][gx,gy] -> kind`): lazily initialized by
  `_ensure_district_terrain` to a per-kind default (`forest`→`grove`, `farm`→`soil`,
  `beach`→`sand`, `cave`→`rock`, `ocean`→`water`, else `soil`). `dig_terrain` and
  `plant_terrain` (`_dig_terrain`/`_plant_terrain`, sim_engine/mixin_diplomacy.py:234, 304) mutate
  individual cells; `_find_nearby_terrain` does a bounded scan for the nearest cell
  of a given kind.
- **Composable/build layer** (`district["tiles"][gx,gy] -> block_type`), capped at
  `TILE_CAP_PER_DISTRICT = 200` (sim_engine/constants.py:1988) per district. `BLOCK_TYPES`
  (sim_engine/constants.py:2003-2008): `wall` (1 wood, shelter), `floor` (1 wood, no shelter),
  `door` (2 wood, no shelter), `fence` (1 wood, shelter). `place_block`/
  `remove_block` (`_place_block` sim_engine/mixin_diplomacy.py:161-199, `_remove_block` sim_engine/mixin_diplomacy.py:200) charge/
  refund the block's resource cost and reject on unknown type, out-of-district,
  tile-cap, or occupied-cell. Shelter blocks count toward night-exposure protection
  (`NIGHT_EXPOSURE_DAMAGE`) alongside houses — see [10-path1.md](10-path1.md).

### Architect Zones (Divine Matrix Phase 9)

God paint/door overlays on the Path1 terrain grid — not composable `tiles` blocks.
See [02-engine-core.md](02-engine-core.md#architect-zones-divine-matrix-phase-9).

- **Paint:** `architect_zone` with `zoneKind: paint` writes `district["terrain"]`
  cells (`paintTerrain` ∈ `GOD_ARCHITECT_PAINT_TERRAINS`). Reversible zones store
  `revertSnapshot` and restore on cancel/expiry.
- **Door:** `zoneKind: door` with `keyId` — `_move_agent` calls
  `_architect_door_blocks_move`; agents lacking the matching `godKeys` tag bounce
  in place (no crash). Optional `grantKeyAgentIds` grants the tag on apply.
- **Limbo:** `zoneKind: limbo` teleports `holdAgentIds` to `GOD_LIMBO_STATION`
  `(140, 500)` in the ocean district and sets `divineHold` (think/move pause).
  `architect_release_hold` or zone cancel/expiry restores prior pose when safe.

## Weather (`WEATHER_ENABLED`, living-ecosystem Phase 4)

A deterministic, LLM-free state machine advanced on the existing
`GOODS_TICK_FRAMES = 900` (~30s) cadence, called from `_tick_goods()`
(`_tick_weather`, sim_engine/mixin_structures_economy.py:394) — the same tick that already hosts spoilage,
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
behaviors, enforced across `sim_engine/mixin_god_miracles.py`
(`_god_apply_weather_override`, `_close_weather_override`) and
`sim_engine/mixin_structures_economy.py` (`_weather_enter_forced`,
`_weather_handoff_successor`):

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

The `cemetery_grounds` district's
`grave_grid` (48 slots, same `{x0,y0,cols,dx,dy}` spacing convention as `build_grid`)
holds tombstone positions distinct from its 1-slot `build_grid` (the Cemetery
structure itself). `_grave_grid_position(district_id, index)`
(sim_engine/mixin_lifecycle.py:229) resolves a burial slot; `_bury_agent_at` (sim_engine/mixin_lifecycle.py:257)
assigns the next free slot to a corpse via the `bury_agent` action
([07-actions.md](07-actions.md)). A working cemetery structure (not disrepaired)
is required before burial succeeds; a district with `kind == "cemetery"` bypasses
the normal `PROJECT_KIND` build-district resolution.

## World Wiki — district and structure pages (`WORLD_WIKI_ENABLED`)

**Grounded in:** plan §2 Answers 1, 2, 3.

This section documents the wiki page shapes for the two entity kinds owned by this spec:
**district** and **structure**. Both are read-only projections over existing engine state;
the wiki route (`GET /wiki`, [specs/04-http-api.md](04-http-api.md)) assembles them
in-process.

### District page

Source: `civilization["districts"]` (live runtime dict, cold-started from
`STARTER_DISTRICTS`) and district/road data from `_districts_snapshot_payload(engine)`
(extracted from the `districts_js()` inline block; see
[specs/04-http-api.md](04-http-api.md) — Districts merge mechanism).

Fields projected onto a district page:

| Field | Source | Notes |
|---|---|---|
| `id` | district key | |
| `kind` | `district["kind"]` | e.g. `"farm"`, `"village"` |
| `tile` | `district["tile"]` | ground tile id used by the renderer |
| `label` | `district["label"]` | display name; `null` for ocean |
| `bounds` | `district["bounds"]` | `{x1,y1,x2,y2}` |
| `settlementId` | `district["settlementId"]` (when present) | links to settlement page |

**Structured links** (from the Answer 2 cross-link table):

- `settlementId` → settlement page (when present; district is a member of that settlement)

**Not linked:** district `kind` is a category string, not an instance id — no
auto-link is generated to other districts of the same kind.

### Structure page

Source: `civilization["structures"]` list (`_structure_snapshot_row()`,
`mixin_snapshot.py:136-156`).

Fields projected onto a structure page:

| Field | Source | Notes |
|---|---|---|
| `id` | `structure["id"]` | |
| `type` | `structure["type"]` | e.g. `"house"`, `"workshop"` |
| `districtId` | `structure["districtId"]` | links to district page |
| `homeOf` | `structure["homeOf"]` | agent id; links to agent page |
| `condition` | `structure["condition"]` | 0–100 |
| `isRuin` | `structure["isRuin"]` | bool |
| `level` | `structure["level"]` | numeric level |
| `visualTier` | `structure["visualTier"]` | 1–3 |
| `name` | `structure["name"]` | custom blueprint name if present |

**Structured links** (from the Answer 2 cross-link table):

- `homeOf` → agent page (when set)
- `districtId` → district page

**Not linked:** structure `type` is a category string — a recipe's `station` field
names this type string, not a specific built structure id, so that cross-reference
is intentionally excluded from auto-linking (see Answer 2 table in specs/04).
