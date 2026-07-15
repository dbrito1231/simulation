# SPEC 11 — Viewer

The browser client: a pure, stateless renderer over the server-authoritative
world. No simulation logic lives here.

**Canonical for:** the thin-viewer contract, polling cadence, canvas/world
rendering pipeline (terrain cache, day/night, zoom/minimap), sidebar panel
inventory, `ACTION_LABELS` (display-only), and `sprites.js`'s pure drawing
rules (structure sprite resolution order, seasonal variants).
**See also:** [01-architecture.md](01-architecture.md) for the
server-authoritative topology this file implements the "thin viewer" half of;
[04-http-api.md](04-http-api.md) for `/state`/`/districts.js` payload shapes;
[07-actions.md](07-actions.md) for the action catalog `ACTION_LABELS` merely
labels.

## Thin-viewer contract

`simulation/index.html` states its own contract in a banner comment
(index.html:673-680): it is a **PURE RENDERER** — it polls `GET /state`
(~10 Hz), keeps the latest snapshot in a module-level `world` variable, and
draws agents/structures/sidebar from it. Closing the browser tab does **not**
stop the simulation; all engine logic (decisions, movement, survival, rules,
memes, memory, build pipeline) runs server-side only. `simulation/sprites.js`
is a second, purely-functional file: stateless Canvas drawing helpers that
take a `structure`/`agent` object and a context and paint pixels — it holds no
world state beyond a cached palette/season key.

## Polling and render loop

- `STATE_POLL_MS = 100` (index.html:2142) drives `pollState()`: fetches
  `GET /state`, replaces `world` wholesale, and on fetch failure patches
  `world.lmStatus = "disconnected"` while keeping the last-known snapshot
  (index.html:2182-2190). **Offline behavior**: the last good frame stays on
  screen and the sidebar status dot goes gray (`#9E9E9E`, index.html:1660)
  with the hint "Showing last frame; retrying /state…"
  (index.html:1663-1664) — distinct from `lmStatus: "offline"` (LM Studio
  unreachable, Flask up) and `"compute_error"` (GPU memory error), each with
  its own dot color/label (index.html:1654-1665).
- `DISTRICTS_POLL_MS = 3000` (index.html:1069) drives `pollDistricts()`
  (`GET /districts.js`) on a slower cadence since districts/roads change
  only when a district is founded server-side; rebuilds the terrain cache
  only when the served district-id list actually changed (index.html:1064-1086).
- The render loop is **decoupled from polling** via `requestAnimationFrame`:
  `tick()` (index.html:2244-2278) redraws every animation frame from
  whatever `world` currently holds, keeping ~60fps even though network polls
  land at ~10 Hz (index.html:2239-2241).
- Controls (Pause/Resume/Reset) POST to `/control/pause|resume|reset` via
  `postControl()` (index.html:2202-2224) with optimistic local flips
  reconciled by the next poll; keyboard shortcut `R` also resets
  (index.html:2229-2232).

## Canvas / world rendering

- `WORLD_W = 5200`, `WORLD_H = 5400` (index.html:689-690) must match
  `sim_engine.py`'s `WORLD_W`/`WORLD_H` (sim_engine.py:69-70) exactly — the
  comment at index.html:686-688 says so explicitly.
- **Offscreen terrain cache**: static terrain (zones, crops, trees, dock,
  ocean) is rendered once into an offscreen `terrainCanvas` and blitted each
  frame instead of re-tiling per frame (`buildTerrainCache`/
  `scheduleTerrainCacheBuild`, index.html:740-756), invalidated on resize, a
  season change (index.html:2173-2178), or a district-list change
  (index.html:1079-1081).
- **Seasonal color grading** (`applySeasonTint`, index.html:704-733) is baked
  into the terrain cache once at build time: autumn = warm multiply+overlay,
  winter = desaturate then cool overlay+lighter passes, spring = faint green
  overlay, summer = untinted baseline (no-op).
- **Day/night overlay** (`nightAlpha(cal)`, index.html:1038-1046): ramps a
  `MAX_NIGHT_ALPHA = 0.45` navy overlay in over `dayFraction` 0.70–0.80,
  holds through 0.95, ramps out to 1.00 — applied as a full-canvas `fillRect`
  after agents/structures each frame (index.html:2269-2273).
- **Zoom**: `zoomLevel` (index.html:780) scales `canvas.style.width/height`
  over the fixed-resolution backing store (`applyZoom`, index.html:784-786);
  +/- buttons multiply by 1.25/0.8 (index.html:819-820), scroll-wheel zoom
  is wired (index.html:833), and "Fit" computes the zoom that fits the whole
  world.
- **Minimap**: `#minimap` (220×160, index.html:585), `renderMinimap()`
  (index.html:2070+) draws a scaled-down world plus a viewport rectangle
  from scroll position/`zoomLevel` (index.html:2092-2100); clicking it
  recenters the main view (index.html:2114-2115).
- **Sidebar panel** (`#sidebar`, index.html:588+), via `renderSidebar()`
  (index.html:1645+): Time (EST clock, uptime, calendar string), LM
  Studio/server status dot+label, Civilization (era/level, structures,
  active builds, resources), Agents, Council, Conversations/Activity
  (`#convPanel`, index.html:552-577), and a conditionally-shown Settlements
  section (index.html:562-568, hidden until diplomacy data arrives).
- **`ACTION_LABELS`** (index.html:1357-1390) maps each `DECISION_ACTIONS`
  name to a short display gerund (e.g. `collect_resource` → "gathering");
  `humanizeAction(agent)` (index.html:1391-1398) special-cases
  dead/incapacitated/thinking agents and falls back to
  `a.replace(/_/g, " ")` for any action missing from the map. Display-only —
  not the source of truth for what actions exist (see
  [07-actions.md](07-actions.md)); per the action-sync invariant in
  [01-architecture.md](01-architecture.md), a new action should get an entry
  but nothing breaks if briefly missing.

## sprites.js: pure stateless drawing

- Every function takes `ctx` plus plain data and paints; the only module
  state is a season mirror (`spriteSeason`, sprites.js:3-7) and a per-season
  tree-grid cache (`TREE_GRIDS`, sprites.js:280-284, built once per season).
- **Agent sprites**: `buildAgentSprite(palette, standRows, walkRows)`
  (sprites.js:833) composes stand + walk-cycle frames per agent palette;
  `genericAgentSprite(agent)` (sprites.js:1307) is the deterministic
  fallback. Living agents tint by dominant belief id via
  `BELIEF_TINTS[beliefIds[0]]` (sprites.js:1391-1394). Deceased/buried
  agents render a cached `tombstoneSprite(agent)`
  (sprites.js:1323-1349, `_tombstoneSpriteCache` keyed by name) instead of
  the living sprite (sprites.js:1368-1372), color-derived and deterministic
  per agent so repeat draws don't regenerate the grid.
- **Structure grid resolution order** — `getStructureGrid(structure)`
  (sprites.js:719-751), in order: (1) canonical level-30 house
  (`type === "house" && level >= 30` → `LEVEL30_HOUSE_GRID` always,
  sprites.js:721-726); (2) persisted LLM sprite, if upgraded with a
  non-degenerate `structure.sprite` spec (sprites.js:729-735); (3) upscaled
  seed grid — `upgradedSeedGrid` scales the built-in `STRUCTURE_GRIDS` entry
  by `min(visualTier, 3)` (sprites.js:705-711, 736-739); (4)
  `STRUCTURE_GRIDS[structure.type]` tier-1 seed grid (sprites.js:740-741);
  (5) `spriteGridFromSpec(type, sprite)` for a first-time custom sprite
  (sprites.js:742-745); (6) `STRUCTURE_GRIDS[structure.visualStyle]`, a
  named built-in style borrowed by a custom blueprint (sprites.js:746-749);
  (7) procedural fallback, or `drawGenericStructure` (colored block with the
  type's first letter, sprites.js:763-781) if nothing resolves.
- **Seasonal variants**: tree grids are cached per season
  (`TREE_GRIDS[season]`, sprites.js:284, 341); in winter `drawSnowCap`
  (sprites.js:83) layers onto trees, rocks, and any agent-built structure's
  top edge (sprites.js:794-797) — the only place in the file reading the
  module-level `spriteSeason` mirror instead of an explicit `season` param.
  `setSpriteSeason(season)` (sprites.js:7) is called once per `/state` poll
  from `pollState()` (index.html:2170) so rendering tracks
  `calendar.season`.
- **`STARTER_DISTRICTS_JS`** (sprites.js:1443-1456, 12 entries) is a
  client-side fallback used before the first `/districts.js` fetch resolves;
  a comment flags it **"MUST be kept in sync with sim_engine.py's
  STARTER_DISTRICTS bounds/kind/label"** (sprites.js:1440-1442) — see
  [05-world.md](05-world.md) for the server-side list it mirrors.

## Active viewer work

Three open design docs describe further, not-yet-fully-landed viewer polish
(verified status lines as of this writing):

- [docs/plan-visual-1-day-night-lighting.md](../docs/plan-visual-1-day-night-lighting.md)
  — **PLANNED (not implemented)**, viewer-only + one small engine addition.
- [docs/plan-visual-2-seasonal-terrain-grading.md](../docs/plan-visual-2-seasonal-terrain-grading.md)
  — **PLANNED (not implemented)**, viewer-only, composes with Plan 1.
- [docs/plan-visual-3-seasonal-sprite-variants.md](../docs/plan-visual-3-seasonal-sprite-variants.md)
  — **DONE** (plumbing + art passes both shipped and verified; kept for the
  design record). The `setSpriteSeason`/`TREE_GRIDS`/winter-snow-cap
  behavior documented above is this plan's shipped result.
