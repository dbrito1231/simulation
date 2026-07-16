# Visual Plan 3: Seasonal Sprite Variants

Status: DONE (2026-07-14) — both the plumbing pass and the art pass are implemented and verified (pixel-sampled per-season sprite output; summer confirmed byte-identical to the pre-change art). Kept for the design record and the verification recipes.

Done (plumbing pass): every drawing entry point below accepts an optional trailing `season` parameter (default `"summer"`) and forwards it — `drawTiledWorld`, `drawStarterProps`, `drawDistrictTerrain`, `drawDistrictTiles`, `drawTree`, `drawCrop`, `drawHouse`, `drawWell`, `drawRocks`, `drawFence` (sprites.js); `drawDock`/`drawMarketStall`/`drawCaveEntrance` were deliberately left season-blind. `buildTerrainCache()` passes the current season into `drawTiledWorld`, and `pollState()` calls the new `setSpriteSeason()` each poll, keeping the module-level `spriteSeason` variable in sprites.js current (the file's one documented mutable-state exception). Nothing reads the parameter or `spriteSeason` yet, so rendering is byte-identical — verified by pixel sampling plus both smokes.

Remaining (art pass): original steps 1, 3, and the drawing half of step 5 below.

## Context

Props and vegetation are drawn with hardcoded colors inside the terrain cache: `drawTree()` (sprites.js ~243) uses fixed leaf (`lf`/`lf2`) and trunk (`tr`) palette keys, crops (`drawCrop` ~298), and the per-district props via `drawStarterProps()` (~1326) / `drawDistrictTiles()` (~1403) all render season-blind. Goal: discrete seasonal artwork — bare trees in autumn (falling-leaf accents), snow-capped trees/roofs and whitened crops in winter, blossom accents in spring — beyond what a global tint can express.

## Design

Thread a `season` string through the terrain-cache drawing path and branch inside the leaf-level sprite functions. Everything affected lives inside the cached terrain render, so **no per-frame cost**: the cache rebuild on season change (mechanism from Plan 2, index.html `pollState()`) is the only trigger. Structures drawn per-frame from `/state` (agent-built structures via `drawStructure()`) get one cheap winter accent only (snow line on roofs) to avoid re-processing procedural sprite grids every frame.

Scope by season:
- **spring**: trees get a few pink/white blossom pixels (extra color rows in the tree grid); crops young/bright green.
- **summer**: baseline art, unchanged.
- **autumn**: tree canopy rows swap `lf/lf2` → orange/brown keys; 2–3 scattered "fallen leaf" pixels on grass tiles adjacent to trees (drawn as part of the tree sprite footprint, not the tile system, to keep tiles season-free).
- **winter**: top 1–2 canopy rows → white (snow caps); crops rendered as stubble (shortened brown rows); well/rocks/fences get a 1-px white top edge; house/prop roofs in the *static* props get a white ridge row.

## Steps — ART PASS (implementer subagent-ready; plumbing already done)

All plumbing exists: the `season` string arrives at every function below, and `spriteSeason` in sprites.js is kept current by the viewer. Remaining work is in **sprites.js only**:

1. **Palette**: add seasonal keys to the color map `C` (blossom `bl`, autumn leaf `al1/al2`, snow `sn`, stubble `st`). One place; tileFromStrings picks them up by key.
2. **Art variants**: implement the season branches inside `drawTree`/`drawCrop`/props (`drawHouse`, `drawWell`, `drawRocks`, `drawFence`) per the scope table — the `season` parameter is already in each signature, currently unread. Keep each variant as a small row-string edit of the existing grids (the `tileFromStrings` format makes this a data change, not new drawing code). Cache the built grids per season in a module-level map (`TREE_GRIDS[season]`) so grids build once, not per draw call.
3. **Winter roof accent on agent-built structures (only per-frame piece)**: in `drawStructure()`/`drawGenericStructure()` (sprites.js ~638–670), when the module-level `spriteSeason === "winter"`, draw a 1-px-scaled white line across the sprite's top edge after the grid blit. `spriteSeason` is already maintained by `setSpriteSeason()` from `pollState()` — just read it; do not thread a parameter through the drawList.

## Verification

1. Smokes pass (`path1_smoke.py` includes a `py_compile`-style check of the server only; sprites.js has no test — verification is visual).
2. Browser: spoof each season and force a cache rebuild (console recipe from Plan 2's verification); screenshot all four seasons. Check: trees (starter forest + district forests), crops in the farm district, well/rocks/fences, static houses, and one agent-built structure with the winter roof line.
3. Confirm summer renders byte-identical to pre-change (screenshot diff or eyeball) — the default-param design guarantees any missed call site degrades to current art.
4. Watch a live season turn; confirm rebuild swaps the art without flicker and FPS is unchanged (all variant work happens in the cached build).
