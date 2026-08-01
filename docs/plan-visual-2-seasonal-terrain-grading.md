# Plan — Seasonal Terrain Grading v2

**Parent:** [two_sim_breakthroughs plan](../.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md) § E — Full atmosphere pack  
**Status:** Planned — documentation only (Phase 4a). Implementation in Phase 4c.  
**Branch:** `feature/four-breakthroughs-ace`  
**Gate:** `VISUAL_SEASONAL_TERRAIN_ENABLED` (default **on**; off = pre-v2 global `applySeasonTint` only)  
**Touches (4c):** `simulation/index.html`, `simulation/sprites.js`, `specs/11-viewer.md`, `specs/01-architecture.md` (flag index)

## Problem

Seasonal color grading exists (`applySeasonTint`, index.html ~1438) as a **single global multiply/overlay** on the whole terrain cache after `buildTerrainCache()`. Specs/11 documents autumn warm, winter desaturate+cool, spring green, summer baseline.

Gaps:

1. **Terrain kind is ignored** — beach, farm, forest, quarry, and village green read the same winter desaturation; beach should stay sandy, forest should go dormant brown, farm furrows should read fallow.
2. **Winter snow is generic** — `drawSnowCap` (sprites.js:118) on trees/rocks/structures only; ground tiles stay green-tinted under global winter pass.
3. **Ecology stage interaction is accidental** — sparse/barren stages shorten tree grids (specs/11) but seasonal palette does not shift with stage (e.g. autumn `healthy` vs `barren` should not look identical).
4. **Summer reads as “no season”** — intentional baseline, but v2 should add subtle heat haze on open/grass kinds only.

**Extends cache-time grading; does not animate per frame.**

---

## Goals

1. **Per terrain-kind seasonal palettes** — district `kind` / tile type selects tint recipe at cache build.
2. **Winter snow accents on ground** — sparse white speckle / edge frost on grass, farm, forest floor tiles; heavier caps on structures (existing `drawSnowCap` retained).
3. **Compose with ecology stages** — stage modulates palette intensity (lush = full season color; barren = muted + browner).
4. **Flagged rollback** — off = today’s single `applySeasonTint(canvas, season)` call.

---

## Architecture constraint (unchanged)

> Terrain stays in **`terrainCanvas`** offscreen cache. Season + ecology stage changes trigger rebuild (`lastSeasonRendered`, `lastEcologyStageKeyRendered`). v2 grading runs **inside** `buildTerrainCache()` / `drawDistrictTerrain`, not per frame.

District kind source: `/districts.js` payload (`kind`: `beach`, `farm`, `forest`, `quarry`, `village`, `ocean`, … — mirror `STARTER_DISTRICTS` in sim_engine.py / sprites.js `STARTER_DISTRICTS_JS`).

Ecology stage source: `world.districtEcology[]` keyed by `districtId` (default `healthy` when missing).

---

## Current baseline (flag off)

```javascript
// index.html applySeasonTint — whole canvas, season only
// summer: no-op
// autumn: multiply rgba(200,140,40,0.18) + overlay 0.10
// winter: saturation 0.60 + overlay cool + lighter pass
// spring: overlay green 0.10
```

Snow: `drawSnowCap` on trees, rocks, agent-built structures when `spriteSeason === "winter"`.

---

## v2 design

### A — Replace global tint with `applySeasonTintForKind(ctx, season, kind, stage)`

New function in **index.html** (or sprites.js if kind-specific pixel helpers needed — prefer index.html to keep sprites stateless except `drawSnowCap` extensions).

**Call site:** inside `drawDistrictTerrain` loop, after base tiles for that district are drawn to the cache context, before moving to next district — **not** one full-canvas pass at end.

| `kind` | spring | summer | autumn | winter |
|---|---|---|---|---|
| `forest` | +green overlay 0.12 | baseline | warm multiply 0.22 + brown overlay 0.08 | desat 0.55, cool blue 0.25, **ground frost speckle** |
| `farm` | +yellow-green 0.10 | **heat shimmer** optional 1px noise | harvest gold 0.20 | desat 0.40, **fallow brown** overlay 0.15, light frost on furrows |
| `beach` | +warm sand 0.06 | bright sand lighter 0.08 | mild warm 0.10 | **minimal** desat 0.25 (stay sandy, not grey) |
| `quarry` | grey-green moss 0.08 | dry dust multiply 0.10 | rust 0.12 | frost in cracks (lighter 0.15 on rock tiles only) |
| `village` / default | spring green 0.10 | baseline | autumn warm 0.16 | desat 0.50 + cool 0.22 |
| `ocean` | — | — | — | **no tint** (ocean handled by animated foam overlay) |

**Stage modulation** (multiply all overlay alphas by):

| stage | factor |
|---|---|
| `lush` | 1.0 |
| `healthy` | 0.85 |
| `sparse` | 0.65 |
| `barren` | 0.45 + extra `rgba(90,70,50,0.08)` brown wash |

When `districtEcology` missing, treat as `healthy`.

### B — Winter ground snow accents (`drawWinterGroundAccent`)

New sprites.js helper (pure draw, no state):

- Input: `ctx`, tile rect, `kind`, hash seed from tile coords.
- **Forest/farm/village grass tiles:** 2–4 white pixels at `scale` 1–2 per 16×16 cell, ~15% of cells, y-biased to top edge of cell.
- **Farm crop cells:** frost line on soil row only (no snow on green crop pixels — use existing crop stage colors).
- **Beach/ocean:** no-op.
- Invoked from terrain cache build when `season === "winter"` and flag on.

Keep **`drawSnowCap`** on trees/rocks/structures; increase cap width by **+2px** on `lush`/`healthy` forest trees only (stage-aware parameter).

### C — Cache key extension

Add to invalidation key alongside season + ecology:

```javascript
const terrainVisualKey = `${season}|${ecologyStageKey}|${VISUAL_SEASONAL_TERRAIN_ENABLED ? "v2" : "v1"}`;
```

Toggle flag → one rebuild → correct path.

### D — Interaction with day/night v2

Terrain grading is cache-time; [plan-visual-1-day-night-lighting.md](plan-visual-1-day-night-lighting.md) overlays are post-cache. No coupling except QA: winter terrain should not be so bright that night overlay fails to read as night.

---

## Implementation steps (Phase 4c)

1. **Flag** (4b): `VISUAL_SEASONAL_TERRAIN_ENABLED = True` → `config.flags`.
2. **`buildTerrainCache`** — pass `districtEcology` map into district draw loop (already available on `world` at poll time; snapshot at build).
3. **Branch at end of district draw:**
   - off → existing post-cache `applySeasonTint(terrainCanvas, season)` only.
   - on → **skip** global `applySeasonTint`; per-district `applySeasonTintForKind` during draw.
4. **sprites.js** — add `drawWinterGroundAccent(ctx, x, y, w, h, kind, seed)` and optional `drawSnowCap(..., { stage })` width bump.
5. **`drawTree` / `drawCrop`** — when v2 + winter, call ground accent under tree/crop footprint.
6. **Ocean foam frames** — if `applySeasonTint` currently tints ocean offscreen frames, apply kind=`ocean` no-op or light winter cool only on foam cache (audit `buildOceanFrames`).
7. **Specs 4d** — document kind table, stage factors, flag.

---

## Constants summary

```javascript
const STAGE_TINT_FACTOR = { lush: 1.0, healthy: 0.85, sparse: 0.65, barren: 0.45 };
const WINTER_GROUND_SPECKLE_RATE = 0.15; // fraction of grass cells
const BARREN_EXTRA_BROWN = "rgba(90,70,50,0.08)";
```

Kind-specific alphas live in a `SEASON_KIND_TINTS` object in index.html (implementer defines full table from § A above).

---

## Verification

- [ ] Season rotate: each district kind visibly distinct in autumn and winter.
- [ ] Winter: snow caps on trees + speckle on village green; beach stays tan.
- [ ] Ecology: same season, `barren` forest browner/sparser than `lush`.
- [ ] Flag off → identical to pre-4c global tint (screenshot diff).
- [ ] Cache rebuild count: stage change → one rebuild; no per-frame rebuild.
- [ ] Compose with lighting v2: dusk/night overlays still readable on winter forest.

**Related:** [plan-visual-atmosphere-systems.md](plan-visual-atmosphere-systems.md) (calendar retune affects how long seasons display; weather particles stack on graded terrain).
