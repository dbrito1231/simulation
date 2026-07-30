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
  (index.html:1663-1664) — distinct from `lmStatus: "offline"` (Ollama
  unreachable, Flask up) and `"compute_error"` (GPU memory error), each with
  its own dot color/label (index.html:1654-1665).
- `DISTRICTS_POLL_MS = 3000` drives `pollDistricts()` (`GET /districts.js`)
  on a slower cadence since districts/roads change only when a district is
  founded server-side. The first terrain-cache build starts immediately at page
  kickoff (via `scheduleTerrainCacheBuild`) using `STARTER_DISTRICTS_JS` as a
  fallback — it does **not** wait for `/districts.js`. When the served
  district-id list later differs, `pollDistricts()` nulls `terrainCanvas` and
  rebuilds.
- The render loop is **decoupled from polling** via `requestAnimationFrame`:
  `tick()` (index.html:2899-2914) redraws every animation frame from
  whatever `world` currently holds, keeping ~60fps even though network polls
  land at ~10 Hz.
- **Render-error resilience**: `tick()` is a thin wrapper that calls the
  actual per-frame render work (`tickBody()`) inside a `try/catch`, with the
  `requestAnimationFrame(tick)` reschedule moved into a `finally` so it
  always runs. If `tickBody()` throws (e.g. a future rendering bug touching
  an unexpected field shape), the error is logged via `console.error` and
  that single frame is skipped, but the rAF chain keeps going — before this,
  any uncaught exception inside the rAF callback silently and permanently
  killed the render loop (no more frames ever painted) while `pollState()`'s
  independent `setInterval` kept fetching fresh `/state` data in the
  background that was never rendered.
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
  `scheduleTerrainCacheBuild`). Tiling uses `createPattern` in
  `fillRectWithTile` / `fillRectWithTiles` (sprites.js): each 16×16 tile grid
  is rasterized once, then repeated natively; `fillRectWithTiles` pattern-fills
  the whole rect with the base tile and overdrawing `PATH_CELLS` only for
  road cells. `scheduleTerrainCacheBuild` runs the build synchronously on
  the main thread (~10 ms after pattern-fill); there is no
  `requestIdleCallback` deferral. The `#worldLoading` overlay covers the build
  and is hidden in the same turn via `hideWorldLoading()`. The cache is
  invalidated on resize, a season change, a district-list change (when
  `districtsKey` from `/districts.js` differs), or (`CROP_GROWTH_ENABLED`,
  living-ecosystem Phase 2) a district's `districtEcology` stage change — see
  below. Optional load-perf timings: set `VIEWER_LOAD_DEBUG = true` in
  index.html to log build stage splits and performance marks.

**Crop/tree growth stages (`CROP_GROWTH_ENABLED`, living-ecosystem Phase 2):**
terrain — including crops and trees — is baked into the static
`terrainCanvas` above, so growth is **not** animated per frame; it is instead
a small number of discrete stages that key into the same cache-invalidation
mechanism the season tint already uses.

- `ecologyStagesForTerrain()` (index.html) reduces the top-level
  `world.districtEcology` list (a sibling of `civilization`, see
  [05-world.md](05-world.md)) to a `{districtId: stage}` map plus a
  stable string key. `buildTerrainCache()` passes the map through
  `drawTiledWorld` → `drawStarterProps` (sprites.js) and records the key in
  `lastEcologyStageKeyRendered`, mirroring `lastSeasonRendered`.
  `pollState()` compares the freshly computed key against
  `lastEcologyStageKeyRendered` each poll and rebuilds the cache exactly once
  on a mismatch — the same edge-triggered pattern as the season check, not a
  new timer.
- `drawStarterProps`/`drawCrop`/`drawTree` (sprites.js) all default their new
  `stage` parameter to `"lush"`, and the density/appearance logic at `"lush"`
  reproduces the pre-Phase-2 output exactly — so `CROP_GROWTH_ENABLED = false`
  (or an older snapshot with no `districtEcology`) renders byte-identical to
  before this phase.
- **Farm districts** (`farm_north`, `farm_south`): `shouldDrawCrop(fx, fy,
  stage, baseMod)` scales each site's original placement modulo by
  `CROP_STAGE_DENSITY_SCALE` (`lush: 1` = unchanged, `healthy: 1.5`,
  `sparse: 2.5` = thinner, `barren: null` = no crop cells drawn — the bare
  farm tile alone reads as empty soil). `drawCrop` additionally varies
  per-cell appearance: `winter`/`spring` seasons are unchanged (they already
  read as "not full growth"); otherwise `barren` draws a small dirt clod,
  `sparse` a single thin blade, `healthy`/`lush` the original mature crop.
- **Forest district** (`forest`): the 15 hand-placed `treeSpots` are always
  iterated (count never changes), but `TREE_STAGE_MOD` decides whether spot
  `i` gets a full canopy tree (`drawTree`) or a stump/sapling marker
  (`drawTreeStump`) — `lush`/`healthy` draw every spot as a full tree (mod 1,
  byte-identical to the original unconditional loop), `sparse` draws roughly
  one full tree per three spots (mod 3), `barren` draws stumps only (mod 0).
  `drawTree` also takes `stage`; `TREE_GRIDS` (sprites.js) is keyed by
  `` `${season}|${stage}` `` (was season-only) and only `"sparse"` produces a
  visually distinct (shorter) canopy grid — `"healthy"`/`"lush"` reuse the
  unmodified per-season rows.
- **Hysteresis** lives entirely in the server-side projection (see
  [05-world.md](05-world.md)) — the viewer just reads whatever `stage` string
  it's given, so a ratio hovering on a boundary can't thrash the terrain
  cache multiple times per second.
- **Seasonal color grading** (`applySeasonTint`, index.html:704-733) is baked
  into the terrain cache once at build time: autumn = warm multiply+overlay,
  winter = desaturate then cool overlay+lighter passes, spring = faint green
  overlay, summer = untinted baseline (no-op).
- **Day/night overlay** (`nightAlpha`, index.html): ramps a
  `MAX_NIGHT_ALPHA = 0.45` navy overlay in over `dayFraction` 0.70–0.80,
  holds through 0.95, ramps out to 1.00 — applied as a full-canvas `fillRect`
  after agents/structures each frame. When `ENV_EFFECTS_ENABLED` is on, a
  low-alpha golden band is composited during the same dusk (0.70–0.80) and
  dawn (0.95–1.00) ramps; it adds no state or separate feature flag.
- **World clock HUD** (`WORLD_CLOCK_HUD_ENABLED`): a fixed, non-interactive
  badge over the map projects the existing `calendar.season` and
  `calendar.dayFraction`/`isNight` as one of dawn, day, dusk, or night. It is
  a pure read of the latest `/state` snapshot and disappears cleanly when the
  echoed flag is off.
- **Light glow** (`ENV_EFFECTS_ENABLED`): while the night overlay is active,
  each structure flagged `light: true` in the `/state` structures payload
  whose district is in `civilization.litDistricts` gets a warm radial
  gradient (center ~`rgba(255,190,90,…)` fading to transparent, radius
  ~140 world px) composited over the night overlay so lit districts visibly
  push back the dark. No glow by day or for unfueled lights (they simply
  lack the flag/district entry that night).
- **Weather sky tint + particles** (`WEATHER_ENABLED`, living-ecosystem
  Phase 4): see the dedicated section below.
- **Structure wear** (`STRUCTURE_WEAR_ENABLED`): the server snapshot's
  `conditionTier` selects a deterministic visual pass over every structure:
  pristine has no treatment, worn adds subtle desaturation and edge gaps,
  crumbling adds dark cracks/corner damage, and ruin uses a dedicated rubble
  silhouette. The client accepts older snapshots by deriving the same tier from
  `condition`/`isRuin`, then defaults to pristine when neither exists. Turning
  the flag off leaves the pre-wear structure sprites untouched.
- **Activity cues** (`ACTIVITY_CUES_ENABLED`): the per-frame world pass adds
  small deterministic canvas-only smoke puffs above working heat/craft
  structures and brief dust puffs beneath agents whose last action is
  `build_structure`, `contribute_resources`, or `start_project`. The effects
  are keyed by existing ids and `frameTick`; they retain no viewer state and
  disappear entirely when the flag is off.
- **Social layer** (`SOCIAL_LAYER_ENABLED`): before agent sprites, the viewer
  draws at most a bounded number of thin relationship lines for `socialTies`
  whose two living endpoints are currently inside the canvas viewport and
  nearby in world space. Ally lines are warm and rival lines cool; alpha fades
  with distance. The pass does no all-world pair scan and is a clean no-op when
  the flag is off or an older snapshot lacks `socialTies`.
- **Zoom**: `zoomLevel` (index.html:780) scales `canvas.style.width/height`
  over the fixed-resolution backing store (`applyZoom`, index.html:784-786);
  +/- buttons multiply by 1.25/0.8 (index.html:819-820), scroll-wheel zoom
  is wired (index.html:833), and "Fit" computes the zoom that fits the whole
  world.
- **Minimap**: `#minimap` (220×160, index.html:585), `renderMinimap()`
  (index.html:2070+) draws a scaled-down world plus a viewport rectangle
  from scroll position/`zoomLevel` (index.html:2092-2100); clicking it
  recenters the main view (index.html:2114-2115).
- **Two side panels**, both filled by `renderSidebar()`:
  - **Left panel** (`#convPanel`), titled "Agents & Activity": the **Agents**
    section (`#agentsPanel` — rollup header, living-agent list with
    vitals/crisis sort, and the selected-agent detail panel), then a
    **Council** section (shown when council data exists). A **Conversations**
    section (`#conversationLog`) and a **Settlements** section
    (`#settlementsSection`) also live here but are **hidden by default** behind
    the client-side viewer toggles `SHOW_CONVERSATIONS` / `SHOW_SETTLEMENTS`
    (index.html ~1028, both `false`). These are viewer-only display flags, not
    server `config.flags`; flip either to `true` to restore its section. The
    underlying `world.conversation` and `civ.settlements` data still arrives in
    `/state` regardless. `#agentsPanel` is a flex child of `#convPanel` with its
    own `overflow-y: auto` (index.html ~608), so a long agent roster scrolls
    within the section instead of being clipped by the panel's own
    `overflow: hidden`.
  - **Right panel** (`#sidebar`): the "AI Simulation World" title, LM/server
    status dot+label, then `#sidebarBody` (a flex column, `overflow: hidden`)
    holding **Time** (`#timePanel`, EST clock/uptime/calendar — fixed,
    `flex-shrink: 0`, natural height), **Civilization** (`#civPanel`
    era/level/structures/active builds/resources), and **Activity**
    (`#activityLog`, the world-event feed, `#actList`). Civilization and
    Activity are `flex: 1 1 0; min-height: 0; overflow-y: auto` (index.html
    ~142-153), so they split the space remaining after Time equally and each
    scrolls independently. The Civilization panel's **Village resources**
    row (`#civResources` headline + `#civResourceList` chips) shows
    `civ.stockpile` **plus** every agent inventory, keyed through
    `resourceRegistry`, filtered to `n > 0` (retired or zero holdings never
    render), with chip colours from `resourceRegistry`. The headline count
    (`totalVillageResources()`) is the sum of `villageResourceBreakdown()` so
    the number and chips cannot disagree. Sidebar change detection includes
    `villageResourceBreakdown()` inside `sidebarKey` (index.html ~2582); the
    raw `civ.stockpile` dict is intentionally **not** in the key — it is a
    ~40-key map that changes nearly every tick and would force a sidebar
    re-render on every poll; the breakdown is the stockpile's proxy in this
    key. A third scrollable **Chronicle** panel is a curated
    projection of top-level `world.chronicle`, distinct from the raw Activity
    feed. It preserves its scroll position across snapshot updates and is
    hidden cleanly when `CHRONICLE_ENABLED` is off.
- **`ACTION_LABELS`** (index.html:1357-1390) maps each `DECISION_ACTIONS`
  name to a short display gerund (e.g. `collect_resource` → "gathering");
  `humanizeAction(agent)` (index.html:1391-1398) special-cases
  dead/incapacitated/thinking agents and falls back to
  `a.replace(/_/g, " ")` for any action missing from the map. Display-only —
  not the source of truth for what actions exist (see
  [07-actions.md](07-actions.md)); per the action-sync invariant in
  [01-architecture.md](01-architecture.md), a new action should get an entry
  but nothing breaks if briefly missing.

## Founding banner (`FOUNDING_EVENTS_ENABLED`)

`#foundingBanner` is a fixed, centered, non-modal banner (same positioning
family as `#councilBanner`, offset 30px lower so the two never overlap) that
names a newly founded district. Gated client-side by `FOUNDING_EVENTS_ENABLED`
(mirrors `config.flags.FOUNDING_EVENTS_ENABLED`, wired in `applyFlags` the same
way as `CHRONICLE_ENABLED`); when off, the element is force-hidden.

Unlike `#councilBanner` (which is level-triggered off `civ.councilActive.active`
every render), founding is a one-off event with no ongoing "active" state to
poll, so the trigger is edge-detected client-side: each render diffs
`world.chronicle` for `district_founded`-kind entries not yet seen (tracked in
a small in-memory `Set` of chronicle `frame` values, pruned as the
`CHRONICLE_CAP`-capped ring evicts old entries) and, on a fresh one, shows the
banner with that entry's text for 6s via `setTimeout`. On first snapshot after
a page load/refresh, existing foundings are recorded as "already seen" without
banner-ing them, so resuming a view of a long-running village does not replay
its whole settlement history as a burst of banners.

This client-diff approach was chosen over adding a per-district `foundedFrame`
field to avoid a new `/state` shape and new server-side bookkeeping — the
client already parses the full `chronicle` array every poll for the Chronicle
panel (see above), so no additional data is needed to detect a new founding.

No terrain-cache tint/highlight for newly founded districts was added (the
plan's stretch item): the static `terrainCanvas` cache (index.html ~921/1233)
is invalidated only on season change and districts-list change, and adding a
time-limited "under construction" visual state would require either a new
per-frame cache-bust condition or extra state tracked outside that cache —
deferred as out of scope for this phase.

## Daily Council Assembly window

`#councilAssemblyModal` is a sibling of the existing invention-council
transcript modal and remains a pure projection of
`civilization.dailyCouncil` from `GET /state`. It auto-opens while a Daily
Council session is present, closes on `adjourned`, and provides a manual
close/re-open control so automatic opening never traps the observer.

The responsive assembly view prefers a clearly readable 640x640-or-larger round
table when the viewport permits. Its fixed 760x760 logical canvas scales down
with a square aspect ratio to fit short or narrow viewports, keeping the full
seat ring visible rather than clipping it. It renders every serialized attendee
at their deterministic seat using the existing stateless agent-sprite renderer
and a name label. The
elder's `isHead` seat is visually distinguished. A succession emergency visibly
starts headless, retains all normal agenda rows plus the named
`leadership_vacancy` topic, then marks the elected winner's refreshed head seat.
Alongside or below the table it streams the live transcript (including opinions,
feelings, and named candidate votes), shows the agenda, shows per-attendee
yes/no/abstain tally for ordinary ballots or per-candidate totals and each
voter's choice for succession, and highlights either the elder ruling or the
village-declared succession verdict. The verdict section heading changes to
`Village verdict` for succession rather than implying a nonexistent elder
ruling. It does not calculate seats, quorum, votes, or outcomes.
The existing Council sidebar/history continues to render bounded `councilLog`
records. `ACTION_LABELS` adds display gerunds for `council_speak`,
`council_propose`, and `council_vote`.

The history transcript modal (`openCouncilTranscript`, index.html) reads
`record.transcript` from a past `councilLog` entry, which may have been
written by either council system — the legacy invention council
(`proposer`/`elder`/`blueprint_name`/... fields, `type` in `convene`,
`proposal`, `verdict`, `dissolve`) or the Daily Council
(`who`/`text`/`feeling` fields, plus `phase`, `attendance`, `speak`,
`verdict_speech`, `vote`, `succession_ballot`, `succession_restart`, and
more). `isDailyCouncilRecord()` classifies a record by checking whether any
transcript entry carries a `who` field or a `type` outside the legacy
4-type set. Legacy records render through the existing field-rich
`renderTranscriptEntry()` (proposer/blueprint/needs/rejections/reasoning),
filtered to the 4 legacy types. Daily Council records render their entire
transcript, in order, through `renderDailyCouncilTranscriptEntry()` (the
same `who`/`text`/`feeling` fallback logic as the live Assembly window's
`dailyCouncilTranscriptEntry()`, emitting `.ct-*` markup to match the modal).
The modal's intro note and the "Blueprint pitches & verdict (LLM)" section
heading are chosen per the same classification.

## sprites.js: pure stateless drawing

- Every function takes `ctx` plus plain data and paints; the only module
  state is a season mirror plus viewer-set seasonal-agent-accent gate
  (`spriteSeason`/`seasonalAgentAccentsEnabled`, sprites.js:3-11) and a
  per-season tree-grid cache (`TREE_GRIDS`, sprites.js:280-284, built once per
  season).
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
  top edge (sprites.js:794-797). Seasonal agent accents also read the
  module-level `spriteSeason` mirror rather than an explicit `season` param.
  `setSpriteSeason(season)` (sprites.js:7) is called once per `/state` poll
  from `pollState()` (index.html:2170) so rendering tracks
  `calendar.season`. `SEASONAL_AGENTS_ENABLED` uses the same mirror and a
  viewer-set boolean to draw small post-body seasonal accessory pixels on
  living agents (winter wool cap/scarf, spring leaf pin, summer straw brim,
  autumn scarf); turning it off leaves their base and named accessory sprites
  unchanged.
- **`STARTER_DISTRICTS_JS`** (sprites.js:1443-1456, 12 entries) is a
  client-side fallback used before the first `/districts.js` fetch resolves;
  a comment flags it **"MUST be kept in sync with sim_engine.py's
  STARTER_DISTRICTS bounds/kind/label"** (sprites.js:1440-1442) — see
  [05-world.md](05-world.md) for the server-side list it mirrors.

## Civ-1 physical props

`civilization.physicalProps` renders up to three large, moored sailboats in the
expanded starter ocean beside the beach. Their positions are fixed coastal
moorings, not structure positions; they remain server-derived decoration and
do not create client-side resource or pathing state.

## Weather sky tint + particles (`WEATHER_ENABLED`, living-ecosystem Phase 4)

Reads the top-level `world.weather` projection (`{state, since, districts}`,
see [05-world.md](05-world.md)) — a sibling of `civilization`, same
placement as `socialTies`/`districtEcology`/`shipments`.

- **Sky tint** (`weatherSkyAlpha(weather, nightAlpha)`, index.html):
  composited in the **same full-canvas overlay stage** as the existing
  night overlay — drawn immediately after it, before the golden-hour band —
  so the two stack coherently instead of fighting. `WEATHER_SKY_ALPHA =
  {clear: 0, gathering: 0.10, storm: 0.26, clearing: 0.14}` is a step ramp
  keyed by `weather.state` (the server sends only the current state, not a
  within-state progress fraction, so this is a discrete ramp across state
  transitions rather than a continuous animation). Color is a slate-teal
  (`rgba(18, 26, 34, …)`), distinct from the night overlay's navy, so a
  storm during the day is still visually legible as weather rather than
  dusk.
- **Darkness clamp:** `MAX_NIGHT_PLUS_WEATHER_ALPHA = 0.68`. The weather
  alpha actually drawn is `min(rawWeatherAlpha, 0.68 - nightAlpha)`, so
  night + storm can never exceed a combined 0.68 before sequential
  `fillRect` alpha compounding (`1 - (1-night)*(1-weather)`). Worst case
  (winter storm, deep night `nightAlpha = MAX_NIGHT_ALPHA = 0.45`, no golden
  hour since deep night falls outside the golden-hour dawn/dusk bands, no
  light glow since the district is unlit): `weatherAlpha` clamps from a raw
  0.26 down to 0.23, combined visible darkening ≈ 0.577 — noticeably
  stormier than a plain night but still well short of opaque; verified by
  inspection in the Phase 4 report (agents/structures/terrain remain
  identifiable underneath the tint).
- **Locality tradeoff:** the tint (and particles, below) are **world-wide**,
  not clipped to `weather.districts`, even though `_maybe_disaster`'s damage
  targeting *is* localized to those districts (see
  [08-systems-economy.md](08-systems-economy.md)). Per-district clipping of
  a full-canvas overlay would fight the also-global night overlay for
  little visual payoff — a deliberate v1 simplification, not an oversight.
- **Particles** (`drawWeatherParticles`, index.html; `drawWeatherParticle`,
  sprites.js): drawn every frame (not baked into `terrainCanvas` — motion
  can't live in a static cache, same reasoning as `drawWildlife`). Active
  only during `storm` (full intensity) and `clearing` (45% intensity,
  tapering off); `clear`/`gathering` draw nothing (`WEATHER_STATE_INTENSITY`).
  Snow (`drawWeatherParticle(ctx, "snow", …)`, small drifting dot) replaces
  rain (a short diagonal streak) when `calendar.season === "winter"`.
  Positions are deterministic from `frameTick` and a per-particle-index hash
  (`weatherParticleHash`, FNV-1a-style) — no retained state, same discipline
  as `drawStructureSmoke`/`drawActivityDust`: `x` is a hashed fraction of
  the visible width, `y` is `frameTick` modulo a per-kind fall period
  (`130` frames for rain, `500` for the slower-drifting snow) mapped into
  the visible height, so particles continuously "fall" through the visible
  band without ever being stored between frames.
- **Cap by visible area, not world area:** particle count is
  `min(WEATHER_PARTICLE_CAP(260), floor(visibleW * visibleH /
  WEATHER_PARTICLE_DENSITY_DIVISOR(14000) * intensity))`, where
  `visibleW`/`visibleH` come from the same `canvasWrapEl.scrollLeft/scrollTop
  / zoomLevel` viewport math `drawWildlife`/`drawShipments` already use.
  Zooming out shrinks the effective per-particle screen size but the *count*
  is bounded by the actual visible pixel area, so a fully-zoomed-out view of
  the whole (5200x5400) world never spends time computing or drawing
  hundreds of off-screen or redundant particles.
- **Lightning:** intentionally **not implemented** in Phase 4 — the plan
  allowed skipping it ("keep it subtle and rate-limited... skip if unsure")
  and a flash tied to individual (per-goods-tick, server-side-only) damage
  events would need either new `/state` signal plumbing or client-side
  activity-log diffing to trigger correctly without becoming either a
  seizure risk or visually decoupled from the actual damage event: left out
  of scope rather than shipped half-verified.
- **Gate:** `WEATHER_ENABLED = false` — `weatherSkyAlpha` returns 0 (no sky
  change) and `drawWeatherParticles` returns immediately (nothing drawn),
  regardless of `world.weather`'s presence/content.

## Ambient wildlife (`WILDLIFE_ENABLED`)

Server-authoritative huntable fauna. Unlike terrain/crops, wildlife is
**not** baked into `terrainCanvas` — it is drawn every frame in `tickBody()`
(`drawWildlife(ctx, renderFrame)`, called right after `drawSocialTies`)
from the live `/state` projection. The viewer holds no fauna state of its
own and does **not** pathfind, spawn, or reposition creatures.

- **`/state` projection:** when `WILDLIFE_ENABLED`, `snapshot()` exposes a
  top-level `wildlife` list — `[{id, kind, districtId, x, y, hp, maxHp}]` —
  one entry per **alive** creature (same sibling placement as
  `districtEcology`/`socialTies`). Dead / pending-respawn creatures are
  omitted. Engine ownership, spawn/respawn, wander/flee, migration, and
  combat live in [02-engine-core.md](02-engine-core.md); hunt yield in
  [07-actions.md](07-actions.md) / [08-systems-economy.md](08-systems-economy.md).
- **Density (engine):** spawn caps still key off `world.districtEcology`
  stage per district ([05-world.md](05-world.md)):
  `WILDLIFE_STAGE_COUNT = {barren: 0, sparse: 1, healthy: 2, lush: 4}`,
  capped at `WILDLIFE_CAP_PER_DISTRICT = 4`. Only forest/farm/beach district
  kinds host fauna pools.
- **Kind pools (16 kinds):**

  | District | Kinds | Kill yield |
  |---|---|---|
  | forest | `bird`, `squirrel`, `deer`, `fox`, `boar`, `owl` | `meat` |
  | farm | `grazer`, `rabbit`, `chicken`, `mouse` | `meat` |
  | farm | `butterfly` | none — decorative, not huntable |
  | beach | `fish`, `crab`, `gull`, `turtle`, `seal` | `fish` |

- **Rendering (`sprites.js`):** `drawWildlifeCreature` tries a PNG
  spritesheet blit first, then **always** falls through to procedural
  pixel grids — nothing ever renders blank.
  - **Spritesheet loader:** at module load, `preloadWildlifeSheet()` fires
    a fire-and-forget `Image()` fetch for `/wildlife.png` (served beside
    `sprites.js`; see [12-ops.md](12-ops.md)). `_wildlifeSheetReady` is
    set only on successful load with non-zero dimensions; `onerror` or a
    missing file (404) leaves it `false` permanently for that page load
    and every kind uses procedural art. When ready **and** the kind has an
    entry in `WILDLIFE_SHEET_FRAMES`, `tryDrawWildlifeFromSheet` blits via
    `ctx.drawImage` with `imageSmoothingEnabled = false`, using optional
    `destW`/`destH` overrides or default `sw`/`sh` × tier scale. Extracted
    frame regions are cached in `_wildlifeSheetBlitCache` keyed by source
    rect (same module-level cache idiom as `_tileSourceCanvasCache`). Frame
    entries may be a bare `{ sx, sy, sw, sh, destW?, destH? }` or
    `{ stand, alt? }` with the same `frameTick` cadence as procedural
    animation. **`WILDLIFE_SHEET_FRAMES` is populated for all 16 kinds**
    (128×64 atlas built by `scripts/build_wildlife_sheet.py`; see
    [12-ops.md](12-ops.md) for art provenance).
  - **Procedural fallback:** each kind is a pixel-grid entry in the
    `WILDLIFE_SPRITES` table (flat-color, black-outline idiom matching
    agent sprites). An entry may be a bare grid (static kinds) or
    `{ stand, alt? }` (animated kinds). `drawWildlifeCreatureProcedural`
    resolves the grid: bare grids draw as-is; `{ stand, alt }` entries
    pick `alt` on an alternating `frameTick` cadence (per-kind 8–20
    ticks, default 12 — same idiom as agent stand/walk) when `alt` is
    present, otherwise `stand`. Drawing uses `drawPixelSprite` with
    **per-tier scale** via `WILDLIFE_SIZE_TIER` / `WILDLIFE_TIER_SCALE`
    (replacing the former single `WILDLIFE_SCALE = 2` for all kinds).
    Approximate on-screen sizes (grid width × tier scale; agents are
    16-wide × 2 = 32 px):

  | Tier | Scale | Grid width | On-screen | Kinds |
  |---|---|---|---|---|
  | large | 2 | 16 | ~32 px | `deer`, `boar`, `grazer`, `seal` |
  | mid | 2 | ~6–8 | ~12–16 px | `fox`, `owl`, `turtle`, `rabbit`, `chicken`, `gull`, `bird` |
  | small | 1 | ~8 | ~8 px | `mouse`, `squirrel`, `fish`, `crab`, `butterfly` |

  Large-tier grids are upsized toward 16×16 for agent parity; mid/small
  keep compact grids at their tier scale so relative scale stays believable
  (a mouse never reaches deer size). Cosmetic motion also includes the
  caller-side `bob` sine offset in `index.html` `drawWildlife`; `frameTick`
  drives frame swap only and does not invent a second position.
- **Positions** come exclusively from each `wildlife[]` entry's `x`/`y`
  (and `districtId`). The viewer does not seed positions client-side and
  does not run a road pathfinder for fauna — motion and cross-district
  migration are already resolved server-side between polls. `frameTick` is
  passed through for alt-frame animation; it does not invent a
  second position.
- **Viewport culling** mirrors `drawSocialTies`: cull by district bounds
  against the scroll/zoom viewport, then per-creature `(x, y)`.
- **Interaction:** fauna are huntable via the agent action `hunt_wildlife`
  (multi-hit HP; land kills grant `meat`, beach kills grant `fish`;
  `butterfly` is never a valid target) — see [07-actions.md](07-actions.md).
  The viewer does not resolve hunt hits; it only renders the projected
  alive set (optional cheap HP cue is allowed but not required).
- **Gate:** `WILDLIFE_ENABLED = false` → no `wildlife` key (or empty) and
  `drawWildlife` returns immediately; nothing is drawn beyond the flag
  check.

## Goods-in-motion shipments (`CARAVAN_VISUALS_ENABLED`, living-ecosystem Phase 3)

Purely cosmetic render pass over `world.shipments`, the read-only projection
of the engine's `self.shipments` ring (see
[08-systems-economy.md](08-systems-economy.md#caravan_visuals_enabled) for
the emission side and the hard non-gating constraint). Drawn in `tickBody()`
via `drawShipments(ctx, world.frameTick)`, called right after
`drawWildlife`, so it composites above terrain/wildlife and below agents.

- **Interpolation, not pathfinding:** each shipment already carries its
  resolved road-graph `path` (`{x, y}` waypoints between the two districts'
  `entryNode`s), computed once server-side by the same helper agent travel
  uses (`_road_path_between_districts`, backed by `ROAD_PATH_CACHE`). The
  viewer never re-derives a route — `shipmentPosition()` walks the polyline
  proportionally to `(frameTick - startFrame) / (endFrame - startFrame)`,
  clamped to `[0, 1]` and distributed across the path's segments. This is
  why the shape includes `path`: it lets the client stay a thin, stateless
  interpolator instead of duplicating the engine's BFS road-resolution
  logic in JS.
- **Sprite:** `drawShipment(ctx, mode, x, y, cargoColor)` (sprites.js) picks
  `drawCart` (land) or `drawShipmentBoat` (ocean, a smaller echo of the
  moored `physicalProps` boat art, not a shared code path with it) by
  `shipment.mode`. An optional small cargo-colored square is drawn using
  the resource → colour registry `drawResourceDots` already reads
  (`resourceRegistry()[shipment.resource].color`) — no separate colour
  table.
- **Viewport-culled + capped**, same pattern as `drawSocialTies`/
  `drawWildlife`: the current scroll/zoom viewport is computed once, each
  shipment's interpolated position is culled against it, and draws stop at
  `SHIPMENT_DRAW_CAP = 8` regardless of how many live shipments exist
  (the server-side ring is already capped at the same order of magnitude,
  so this is a second, independent bound).
- **Gate:** `CARAVAN_VISUALS_ENABLED = false` → `drawShipments` returns
  immediately; nothing is drawn. The moored `physicalProps` boats render
  through their own, entirely separate code path
  (`civilization.physicalProps`, gated by `TRANSIT_ENABLED`) and are
  unaffected either way — this flag never touches that block.
- **Restore safety:** shipments are not persisted (see 08); after a
  server restart `world.shipments` is simply absent/empty until new
  transfers occur, so there is nothing to orphan visually.

## Divine Console (Sovereign God mode, Phase 7)

The Divine Console is a fixed bottom action bar plus a large modal dialog —
not a sidebar panel. Eight feature buttons (**Unlock**, **Sight**, **Voice**,
**Miracles**, **Story**, **Laws**, **History**, plus **Compile** when the
server reports the Optional Phase 8 compiler enabled — see below) live in
`#divineBar` (`position: fixed; bottom: 0; left: 0; right: 0`). Clicking a
button opens `#divineModalScrim` / `#divineModal` (`role="dialog"`,
`aria-modal="true"`), whose body is `#divineModalBody`. At load time the eight
`#divineTab-<name>` panel nodes sit in a hidden holding container
`#divineTabHold` so `wireDivineForm()` and other `getElementById` bindings
still resolve at startup; **opening a feature reparents** (moves, never clones)
the matching `#divineTab-<name>` into `#divineModalBody`, and **closing**
returns it to `#divineTabHold`. A shared floating tooltip element `#tooltip`
serves every `data-tip` control in the bar and modal. `#godPublicBanner` remains
independent (fixed top-center; see "Public banner" below). The console is
strictly additive over
[docs/plan-sovereign-god-mode-v2.md](../docs/plan-sovereign-god-mode-v2.md)'s
already-shipped backend (Phases 2–6, see
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel)
and [04-http-api.md](04-http-api.md#sovereign-god-mode)) — no engine or route
code changed for this phase.

**Feature gate.** Bar visibility and modal behavior are driven by
`GOD_MODE_ENABLED_FLAG`, a module-level mirror of
`state.config.flags.GOD_MODE_ENABLED`, set in `applyFlags()` the same way every
other echoed flag is (see "Polling and render loop" above).
`updateGodModeGate()` (called once per `renderSidebar()`, itself once per
`pollState()` tick — **no new poll loop**) toggles `#divineBar`'s `display` and,
when off, force-hides `#godPublicBanner` too. When `GOD_MODE_ENABLED` is absent
or false — including an older snapshot from before this key existed, or a
snapshot from a `GOD_MODE_ENABLED`-off server — the console makes zero
`/control/god/*` requests except where noted below for open-mode bootstrap.

**Authorization gate (dual signal).** Whether the Unlock lifecycle runs is
driven by a second mirror, `GOD_AUTH_REQUIRED_FLAG` (from
`state.config.flags.GOD_AUTH_REQUIRED`, also set in `applyFlags()` and synced
on every `updateGodModeGate()` tick). When `GOD_AUTH_REQUIRED` is **false**
(the default): there is no Unlock step — the brand state reads `open` (green),
the Unlock bar button and its `statuspip` are hidden, every
`.gbtn.locked-dependent` is enabled immediately via
`godEffectivelyAuthorized()` (`godAuthorized || !GOD_AUTH_REQUIRED_FLAG`), and
`/control/god/capabilities` is fetched once without a token as soon as God mode
is confirmed on (`godOpenModeBootstrap()`, guarded so it runs at most once per
session and only while `GOD_MODE_ENABLED_FLAG` is true). `godLockConsole()` is
a no-op re-lock in this mode (console.warn only — a stray 401 must not pop the
hidden Unlock modal). `restoreGodTokenFromSession()` and the remember-checkbox
/sessionStorage wiring are skipped entirely.

When `GOD_AUTH_REQUIRED` is **true**: behavior matches the original Phase 7
contract — `godAuthorized` starts false, the Unlock button and pip are visible,
locked-dependent buttons stay disabled until `godConnect()` succeeds, a 401 from
any God call clears the token and re-locks via `godLockConsole()` (opening the
Unlock modal), and the remember-checkbox may mirror the token into
`sessionStorage`. The Unlock tab markup and `openDivineModal("unlock")` remain
in the codebase as dead-but-harmless paths for re-enabling auth.

When God mode is off, a flag-off render is byte-identical to the pre-Phase-7
page for every other panel; no bootstrap fetch runs.

**Modal UX.** `openDivineModal(name)` and `closeDivineModal()` are the
primary open/close API (replacing the prior in-sidebar `showGodTab()` toggle;
`showGodTab` may remain as a thin alias that delegates to `openDivineModal`).
Opening reparents `#divineTab-<name>` into `#divineModalBody`, sets the modal
header title/icon/subtitle, and shows `#divineModalScrim`. Closing via the ✕
button, a backdrop click on `#divineModalScrim`, or Escape reparents the panel
back to `#divineTabHold` and hides the scrim. Side effects that previously
fired on tab switch now fire on open: Sight → `refreshGodSight()` when
effectively authorized (`godEffectivelyAuthorized()`); Laws →
`renderGodLawsActive()`; History → `renderGodHistory()`.
The Compile bar button `#godCompileTabBtn` stays dual-gated via
`capabilities.compiler.enabled` (see Compile below).

**Tooltips.** Every interactive control, every fieldset legend, and every bar
button carries a `data-tip` attribute whose value is JSON `{t,d}` (short title
+ one-sentence description). A single shared engine on `#tooltip` shows on
`mouseenter`/`focusin` and hides on `mouseleave`/`focusout` (keyboard accessible,
not hover-only); positions above the target, flipping below if the viewport top
would clip; writes content via `escapeHtml`/`textContent` only — never raw HTML
from variables; and respects `prefers-reduced-motion` for show/hide transitions.

**Token handling (auth-required mode only).** When `GOD_AUTH_REQUIRED_FLAG` is
true, the token lives in one JS variable, `godToken`, held only in memory. The
Unlock feature window offers a "remember for this tab" checkbox
(`#godRememberCheckbox`, default unchecked) that, only when checked, mirrors the
token into `sessionStorage` — `localStorage` is never used anywhere in this
section. `godApiFetch()` (the single fetch wrapper every God call goes through)
clears `godToken`/`godAuthorized`/the cached capabilities/sight response and
re-locks the console (`godLockConsole()`, switches back to the Unlock feature
via `openDivineModal("unlock")`) on any `401` response, matching the backend's
uniform unauthorized shape (04-http-api.md). When `GOD_AUTH_REQUIRED_FLAG` is
false, `godApiFetch()` omits the `X-God-Token` header (no token in memory) and
`godLockConsole()` does not clear state or open the Unlock modal on 401.

**Rendering contract.** Every dynamic string this section writes into
`innerHTML` is escaped with the file's existing `escapeHtml()` helper before
insertion (this section alone adds dozens of call sites, on top of the
65+ pre-existing ones cited above) — narration, titles, error/rejection
reasons, agent names, resource/structure labels, and history entries all go
through it. `god_preview()`'s `normalizedCommand` field is deliberately
**never** written into `innerHTML` or otherwise rendered anywhere; every
preview/apply outcome shown to the operator is instead rebuilt field-by-field
from the response's typed keys (`renderGodPreviewOutcomeHtml()`/
`renderGodAppliedHtml()`), each value escaped individually.

**Preview → Apply.** `wireDivineForm(formSelector, opts)` is the one reusable
wiring helper behind every mutating subform (proclamation, providence,
private omen, the three miracles, story event, law): an `input`/`change`
listener on the whole `<fieldset>` invalidates any cached preview and
disables Apply; Preview posts the built envelope to
`/control/god/preview` and, on success, caches the response and renders
`reversibilityClass` as a color-coded badge
(`.divine-badge-irreversible`/`-consequential`/`-cancellable`) plus the
preview's outcome and any disclosed replacement (`fingerprint.outgoingId`,
rendered as an explicit "this will REPLACE …" warning for
providence/private_omen); Apply posts **only** `{previewId, requestId}` — the
client never re-sends the normalized command — and renders exactly what
`/control/god/apply` returns, clearing the cached preview either way (success
or rejection) so a second Apply always requires a fresh Preview.

Timed effects display both units together via `godDurationLabel()`
(`"Xs (Yf)"`, dividing by the same 30-ticks/s assumption the rest of the
viewer uses) and a live countdown via `godCountdownLabel()` computed against
the latest polled `world.frameTick`.

**Replacement-conflict asymmetry (Story/Laws).** Providence and private-omen
replacement is disclosed proactively by the preview response
(`fingerprint.outgoingId`), so the console can show the warning before the
operator ever needs to know an id. A `story_event` modifier-key conflict
(used by both the Story and Laws feature windows, since Laws submits a `story_event`
carrying only `modifiers`) is instead a **hard preview rejection** naming the
occupying event's id in the reason string (`_validate_god_story_event`, see
02-engine-core.md) — there is no disclosed `outgoingId` to read. The console
recovers the id by pattern-matching the rejection text
(`onPreviewRejected` in `wireDivineForm`), then offers a "Replace conflicting
effect" checkbox pre-filled with that id so a second Preview (now carrying
`replaceEffectId`) can succeed. This is a viewer-side accommodation of an
existing backend asymmetry, not a claim that the backend discloses the
conflict the same way — see this phase's report for the underlying gap.

**Sections:**

- **Unlock** (visible only when `GOD_AUTH_REQUIRED_FLAG` is true) — token
  field, "remember for this tab" checkbox, Connect button, and a status readout
  (`locked` / `Authorized.` / an unauthorized message). When auth is not
  required the bar button is hidden and the brand state shows `open` instead.
  A disabled-flag hint line was scoped out per the phase brief (the bar is
  never rendered at all when God mode is off, so there is nothing to hint from
  inside it).
- **Sight** — an agent selector (`getLivingAgents()`, the same public roster
  every other panel already reads) plus a Refresh button that calls
  `GET /control/god/sight` and renders the selected agent's health/hunger/
  incapacitated/district/resources/last action, private-omen status
  (active + countdown only, never the omen text — matching `god_sight()`'s
  own restraint), and every active effect from the authenticated
  `activeEvents` list (including private-visibility ones, tagged with a
  `private` badge) with a countdown each.
- **Voice** — three independent subforms (`proclamation`, `providence` with
  a duration field, `private_omen` with an agent selector and duration),
  each following the Preview → Apply contract above.
- **Miracles** — `agent_vitals` (agent + health delta + hunger delta),
  `grant_resource` (resource + amount + stockpile-or-agent target),
  `structure_condition` (structure + signed delta); all three are labeled
  `IRREVERSIBLE` in their preview badge, matching `_god_reversibility_class`.
- **Story** — title, narration, visibility (public, or private with an
  agent target), duration, a shared 7-key modifier editor
  (`renderGodModifierEditor()`, bounds populated from
  `capabilities.modifierRanges` after Connect), an optional embedded
  providence, and a bounded (`capabilities`-reported `primitives.maxItems`,
  default 5) repeatable primitive-effect list reusing the same three
  Miracles payload shapes inline. `reversibilityClass` in the response
  badge flips from `cancellable` to `CONSEQUENTIAL` automatically once any
  primitive is present, per the engine's own rule.
- **Laws** — the same 7-key modifier editor submitted as a `story_event`
  with no primitives (so mechanically identical to Story minus title/
  narration ceremony — both are auto-filled with a sensible default if left
  blank rather than forced on the operator), plus a live "currently active"
  list (preferring the authenticated Sight projection when available, since
  it — unlike the public `/state` projection — can include a
  private-visibility law's modifiers; falling back to
  `state.god.activePublicEvents` otherwise) with a per-effect Cancel button
  wired straight to `/control/god/cancel`.
- **History** — the last (up to 50, newest first) entries from
  `state.god.recentPublicInterventions`, each tagged with a `public` badge.
  Private history (omens, private story events) is deliberately never shown
  here — per `snapshot()`'s allowlist (04-http-api.md), it is not present in
  `/state` at all and is reachable only through the authenticated Sight
  feature window.
- **Compile** (Optional Phase 8, dual-gated) — `#godCompileTabBtn` stays
  `display:none` until `applyGodCapabilitiesToForms()` sees
  `capabilities.compiler.enabled === true` (itself already the AND of
  `GOD_MODE_ENABLED` and the separate `GOD_COMPILER_ENABLED` dark flag on the
  server — see [04-http-api.md](04-http-api.md#optional-phase-8-controlgodcompile)
  and [12-ops.md](12-ops.md#optional-phase-8-free-prose-story-compiler)); the
  same check also bounds the prose textarea's `maxLength` to
  `capabilities.compiler.promptMaxChars` and stores
  `capabilities.compiler.minIntervalSec` for the client-side rate-limit UX
  below. The feature window holds one textarea and a single Compile button —
  **no Apply button of its own, on purpose**. Clicking Compile posts `{prose}` to
  `POST /control/god/compile`; on `compileOk: true` it calls
  `godPopulateStoryFromCompiled(normalizedCommand)` — which fills the Story
  feature window's title/narration/visibility/target/duration/modifier-checkboxes/
  primitive rows/providence fields from the compiled draft, explicitly
  invalidates any stale Story preview (dispatches the same `input` event
  `wireDivineForm`'s own listener already watches), then calls
  `openDivineModal("story")` — so the operator reviews and Applies through the
  **exact same** Preview → Apply flow every other Voice/Miracle/Law/Story
  action already uses, documented above. On rejection or a network error the
  reason is written via `.textContent` only (never `innerHTML`) into
  `#godCompileResult` — the compiler's raw model output can appear inside
  that reason string (e.g. a truncated non-JSON response), so it gets the
  same plain-text-only treatment as every other stored/adversarial string in
  this console. After every click (success, rejection, or error alike) the
  Compile button disables itself client-side for `minIntervalSec` seconds —
  advisory only; `GOD_COMPILER_MIN_INTERVAL_SEC` on the server is the
  authoritative rate limit and rejects independently of what the client UI
  does.

**Public banner.** `#godPublicBanner` (fixed, top-center, styled distinctly
from `#councilBanner`/`#foundingBanner`) fires on the same client-diff
edge-detection pattern the Founding banner already established: each
`renderDivineConsole()` call (itself gated by a `JSON.stringify(world.god)`
change-detect key, mirroring `lastChronicleKey`) diffs
`state.god.recentPublicInterventions` for an id not yet in a small in-memory
`Set` (`godSeenInterventionIds`, pruned as the bounded ring evicts old
entries), shows one line of `textContent` for 6s on a fresh one, and — on
the very first snapshot after a page load — records existing history as
"already seen" without banner-ing it, so resuming a long `GOD_MODE_ENABLED`
session doesn't replay its whole intervention history as a banner burst.
There is no full-screen flash, no forced camera movement, and no banner (or
any other public surface) for a private omen or private story event.

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
