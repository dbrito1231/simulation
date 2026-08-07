# Phase 2 — Ecology Made Visible (crop/tree growth + wildlife)

**Parent:** [plan-living-ecosystem-0-master.md](plan-living-ecosystem-0-master.md)
**Status:** Planned — not executing.
**Solutions covered:** #2 (crop/tree growth lifecycle), #3 (ambient wildlife).
**Cost:** Medium.
**Touches:** `simulation/sim_engine.py`, `simulation/index.html`,
`simulation/sprites.js`, `specs/05`, `specs/11`.

**This is the phase that most directly answers the original complaint** — "I'm not
seeing flowers, plants, animals." Both deliverables read the *same* underlying
data, which is why they are one phase.

## Problem

The engine already runs a full resource ecology: per-district `districtStocks`
that deplete when gathered and regrow on a tick (`STOCK_REGROW_PER_TICK = 1`,
sim_engine.py:493), modulated by season (`SEASON_REGROW_MULT` — winter halts
regrowth entirely), with scarcity thresholds (`STOCK_LOW_RATIO = 0.25`) that
already narrate to the activity log (*"the forest in X is recovering — wood stock
is growing again"*).

**None of it is visible.** Two blockers, both confirmed:

1. **`districtStocks` is never exposed to `/state`.** It is engine-internal; the
   only snapshot-adjacent reference is the restore path (sim_engine.py:12806). The
   viewer literally cannot know how depleted a forest is.
2. **Terrain — including crops and trees — is baked into a static offscreen
   cache.** `terrainCanvas` (index.html:921) is built once by `buildTerrainCache()`
   (index.html:1233) and invalidated *only* on season change (index.html:2987) and
   district founding (index.html:1362). `drawDistrictTerrain`/`drawCrop`/`drawTree`
   all paint into that cache.

So a forest stripped to zero stock renders identically to a lush one.

## Critical architectural constraint (read before implementing)

> **Do NOT animate growth per-frame, and do NOT move crop/tree rendering out of
> the static terrain cache.** That cache exists specifically so the large world
> costs one `drawImage` per frame instead of re-tiling thousands of cells.
>
> **Correct approach:** quantize stock into a small number of **discrete growth
> stages** and invalidate the terrain cache when a district's stage changes —
> exactly the mechanism the existing season-change invalidation already uses
> (index.html:2987). Stage changes are rare (stock moves by 1/tick against a
> multi-hundred max), so rebuilds stay infrequent.
>
> Wildlife is the opposite: it is *not* terrain, so it belongs in the per-frame
> pass alongside the existing `physicalProps` boats.

## Deliverables

### 2A — Stock-ratio projection (shared prerequisite for both)
Add a compact read-only `/state` projection of per-district stock health — e.g.
`districtEcology: [{districtId, stage, ratio}]` — derived server-side from
`districtStocks` so thresholds stay authoritative (same pattern as
`conditionTier` in the last batch).

- Quantize to **3-4 stages** (e.g. `barren` / `sparse` / `healthy` / `lush`),
  reusing `STOCK_LOW_RATIO = 0.25` as one boundary so the visual matches the
  scarcity the engine already narrates.
- Aggregate per district over that district's gatherable resources (mirror the
  existing ratio logic near sim_engine.py:3490 / `_ecology_scarcity_index`) rather
  than inventing new math.
- Keep the payload small — it is polled at ~10 Hz.

### 2B — Crop/tree growth stages (`CROP_GROWTH_ENABLED`)
- `drawCrop` and `drawTree` (sprites.js) already accept a `season` param; extend
  them to also take the district's **stage**.
- Farm districts: bare soil → sprout → mature → harvested-stubble.
- Forest districts: stumps/saplings when barren → full canopy when lush (tree
  *density* per cell is the cheapest lever, and `TREE_GRIDS` is already cached
  per season — extend that cache key to include stage).
- **Cache invalidation:** add stage to the terrain cache key alongside
  `lastSeasonRendered`, so a stage change triggers exactly one rebuild.
- **Gate:** flag off → stage is ignored and today's rendering is byte-identical.

### 2C — Ambient wildlife (`WILDLIFE_ENABLED`)
Density scales with the *same* stage from 2A — so wildlife visibly thins as a
district is over-harvested and returns as it recovers.

- Fish surfacing near stocked beach/ocean districts; birds over lush forest;
  small grazers near healthy farms.
- **Follow the `physicalProps` precedent** (sim_engine.py:13259 → index.html:3108,
  *"boats remain resources, not entities"*): deterministic positions derived from
  district bounds + a seed, animated by `frameTick`. **No pathfinding, no AI, no
  per-creature state, no persistence.** They are decoration that reflects ecology,
  not agents.
- Cap count per district and skip off-viewport districts (the shipped social-tie
  pass at index.html already establishes this viewport-culling pattern — reuse it).
- **Gate:** flag off → no creatures drawn.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | `districtEcology` stage/ratio projection in `snapshot()`; new flags `CROP_GROWTH_ENABLED`, `WILDLIFE_ENABLED` echoed in `config.flags`. |
| `sprites.js` | `drawCrop`/`drawTree` accept a stage; extend `TREE_GRIDS` cache key with stage; new wildlife sprite helpers. |
| `index.html` | Include stage in the terrain-cache key so a stage change invalidates it (mirror `lastSeasonRendered`); per-frame wildlife pass with viewport culling; gate both on flags. |
| `specs/05-world.md` | Document the `districtEcology` projection, stage thresholds, and their relation to `STOCK_LOW_RATIO`. |
| `specs/11-viewer.md` | Document growth-stage rendering, the extended cache-invalidation key, and the wildlife pass. |
| `specs/01-architecture.md` | Flag index +2. |

No new agent actions → **action-sync invariant N/A**.

## Risks / notes
- **Cache-rebuild thrash** is the main risk. If stock hovers on a stage boundary
  it could rebuild repeatedly. Add hysteresis (require crossing a margin before
  the stage flips) if observed. Measure before optimizing.
- **Winter interaction:** `SEASON_REGROW_MULT` winter = 0, so stock only falls in
  winter. Combined with the existing winter tint and snow caps, a barren winter
  forest should look deliberate, not broken — sanity-check visually.
- **Do not let wildlife imply mechanics it lacks.** They cannot be hunted and do
  not feed the food supply. If that reads as misleading, either keep them subtle
  or note the limitation in the spec. Making them huntable is a *separate,
  much larger* change (new action, new resource path) and is explicitly out of scope.

## Verification
- Drain a district's stock in a scratch harness → confirm the projected stage drops,
  the terrain cache rebuilds once, and crops/trees visibly thin; then let it regrow
  and confirm recovery.
- Confirm wildlife density tracks stage and that off-viewport districts are skipped.
- Toggle each flag off → clean no-op (compare against a pre-change screenshot).
- Watch for frame-rate regression with the world zoomed out to full extent.
- Deterministic smokes + single-instance server check last.
