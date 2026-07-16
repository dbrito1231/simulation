# Visual Plan 2: Seasonal Terrain Color Grading

Status: PLANNED (not implemented). Effort: medium (viewer-only). Depends on nothing; composes with Plan 1 (night overlay draws on top).

## Context

Seasons (`_current_season()`, 45 real min each since the 2026-07-14 year unification) drive regrowth mechanics and reach the viewer via `world.calendar.season` / `civilization.season`, but the terrain looks identical year-round. The terrain is rendered **once** into an offscreen cache (`buildTerrainCache()`, index.html ~960, calling `drawTiledWorld()` in sprites.js ~1425) and only rebuilt when a district is founded. Goal: the map's palette shifts with the season — lush green spring, standard summer, golden autumn, pale/frosted winter — without paying any per-frame cost.

## Design

Tint the cached terrain canvas once per season change, not per frame. Two candidate mechanisms; use (a):

(a) **Post-tint the cache at build time.** After `drawTiledWorld` paints into `terrainCanvas`, apply a season tint to that offscreen canvas with `globalCompositeOperation`:
   - autumn: `fillStyle rgba(200,140,40,0.18)`, mode `"multiply"` then a light `"overlay"` warm pass
   - winter: desaturate-ish: `rgba(220,230,255,0.28)` with mode `"overlay"` + `rgba(255,255,255,0.10)` `"lighter"` for frost
   - spring: `rgba(60,200,80,0.10)` `"overlay"` (subtle extra lushness)
   - summer: no tint (baseline)
   Composite modes on an offscreen canvas are a one-time cost; `drawWorld()` keeps blitting the pre-tinted cache at zero extra per-frame cost.
   Exclusions: the **ocean foam frames** are blitted over the cache every frame from separate canvases (`oceanFrames`, built in the same function) — tint those frame canvases with the *same* pass so the ocean strip doesn't pop against tinted land (winter ocean reads slightly icy, which is desirable). The dock re-blit (DOCK_RECT, ~1007) copies from the tinted cache, so it stays consistent automatically.

(b) *(rejected)* Per-frame translucent fillRect over the terrain in `drawWorld()` — cheap to write but tints agents/structures ambiguously depending on draw order and stacks badly with the night overlay.

**Rebuild trigger:** track the last-rendered season in the viewer. In `pollState()` (or in `tick()` before drawing), if `world.calendar.season !== lastSeasonRendered`, set `terrainCanvas = null; terrainBuildScheduled = false; scheduleTerrainCacheBuild();` — exactly the mechanism `pollDistricts()` already uses for district changes (index.html ~1026). Rebuild happens at most once per 45 min plus on district founds.

**Fallback:** if `world.calendar` is absent (old server), behave as summer (no tint).

## Steps (implementer subagent-ready)

All in **simulation/index.html** (sprites.js untouched — this plan deliberately avoids touching tile/palette definitions; that's Plan 3):

1. Add module state near `terrainCanvas`: `let lastSeasonRendered = null;`
2. Add `function applySeasonTint(canvas, season)` implementing the mode/color table above (a `switch` writing onto `canvas.getContext("2d")` with save/restore of `globalCompositeOperation`).
3. In `buildTerrainCache()`:
   - after the `drawTiledWorld(...)` call, `applySeasonTint(terrainCanvas, world.calendar?.season)`;
   - after the ocean-frames loop, tint each `oceanFrames.frames[i]` the same way;
   - set `lastSeasonRendered = world.calendar?.season ?? null;`
4. In `pollState()` after `world = snapshot;`: 
   ```js
   const season = snapshot.calendar && snapshot.calendar.season;
   if (season && season !== lastSeasonRendered && terrainCanvas) {
     terrainCanvas = null; terrainBuildScheduled = false; scheduleTerrainCacheBuild();
   }
   ```
   (Guard on `terrainCanvas` so the first build isn't double-scheduled.)
5. Tune the four tint values by eye against a live world — the numbers above are starting points; winter must stay light enough that paths/roads and the cemetery tiles remain distinguishable.

## Verification

1. Smokes pass (no engine change at all in this plan).
2. Browser: force each season without waiting 45 min by rebuilding with a spoofed value in the console:
   `lastSeasonRendered = null; world.calendar.season = "winter"; terrainCanvas = null; scheduleTerrainCacheBuild();`
   Screenshot each of the four seasons; confirm ocean strip matches land tint, dock not double-tinted, roads/paths still readable.
3. Watch one real season turn (the "The season turns: …" activity line) and confirm the map re-tints within one districts-poll-free rebuild (~instant on next frame).
4. Confirm no per-frame cost: rebuild only logs/occurs on season change or district found.
