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
- **Terraform:** `TERRAFORM_TEMPLATES` (sim_engine.py:820-858) — three templates,
  each funded like a build project (`needs`) and restricted to a district `kind`:
  `plant_grove` (forest; boosts wood/herbs stock ratio to 0.85), `clear_field`
  (farm; food stock ratio to 1.0), `extend_beach` (beach; fish stock ratio to 0.9,
  and can additionally found a new beach district via `found_district`). Started
  via `start_terraform`, funded via `contribute_resources`, applied by
  `_complete_terraform` (sim_engine.py:2469+), which calls
  `_apply_terraform_modifiers` to mutate district stocks per the template's
  `function.modifies` list.

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
