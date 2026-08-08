# SPEC 11 — Viewer

The browser client: a pure, stateless renderer over the server-authoritative
world. No simulation logic lives here.

**Canonical for:** the thin-viewer contract, polling cadence, canvas/world
rendering pipeline (terrain cache, day/night, zoom/minimap), sidebar panel
inventory, `ACTION_LABELS` (display-only), and the `sprites/*.js` files' pure
drawing rules (structure sprite resolution order, seasonal variants).
**Files:** `simulation/index.html` (markup shell), `simulation/css/*.css`
(styles, split into 6 ordered files — see "css/*.css: split stylesheet"
below), `simulation/viewer/*.js` (polling, render loop, sidebar, Divine
Console; split into 16 ordered files — see "viewer/*.js: split viewer client
script" below), `simulation/sprites/*.js` (stateless Canvas helpers, split
into 8 ordered files — see "sprites/*.js: pure stateless drawing" below).
**See also:** [01-architecture.md](01-architecture.md) for the
server-authoritative topology this file implements the "thin viewer" half of;
[04-http-api.md](04-http-api.md) for `/state`/`/districts.js` payload shapes;
[07-actions.md](07-actions.md) for the action catalog `ACTION_LABELS` merely
labels.

## Thin-viewer contract

`simulation/viewer/setup.js` (first of the 16 split viewer files, see
"viewer/*.js: split viewer client script" below) states the whole viewer's
contract in a banner comment at the top of the file: it is a **PURE
RENDERER** — it polls `GET /state`
(~10 Hz), keeps the latest snapshot in a module-level `world` variable, and
draws agents/structures/sidebar from it. `simulation/index.html` is markup
only (panels, canvas, modals, script tags); `simulation/css/*.css` (6 plain
files loaded via ordered `<link>` tags — no bundler, no CSS preprocessor, in
load order `base.css`, `panels.css`, `agents.css`, `council.css`,
`divine.css`, `responsive.css`) holds layout and panel chrome. Closing the
browser tab does **not**
stop the simulation; all engine logic (decisions, movement, survival, rules,
memes, memory, build pipeline) runs server-side only. `simulation/sprites/*.js`
(8 plain files loaded via ordered `<script>` tags — no bundler, no ES
modules; every file shares one global scope, in load order `core.js`,
`tiles.js`, `props.js`, `structures.js`, `agents.js`, `world.js`,
`wildlife.js`, `shipments.js`) form a second, purely-functional layer:
stateless Canvas drawing helpers that take a `structure`/`agent` object and a
context and paint pixels — they hold no world state beyond a cached
palette/season key.

## Polling and render loop

- `STATE_POLL_MS = 100` (`viewer/polling.js`, `pollState()`) drives polling: the first
  successful fetch uses `GET /state` (full snapshot); thereafter
  `GET /state?since=<lastFrameTick>` unless an error or `stateGeneration`
  mismatch forces another full fetch. The client merges deltas into module-level
  `world` (`mergeStateDelta()`): full/`full: true` replaces wholesale;
  `unchanged: true` keeps `world`; partial payloads replace agents by `id`,
  deep-merge allowlisted `civilization` keys (structure upserts by `id`,
  `structuresRemoved` tombstones), and replace any other included top-level keys.
  The server emits only fields whose `lastMod` frame is greater than the
  client's `since` (within `STATE_DELTA_MAX_GAP`); multiple tabs with different
  `since` cursors each receive the same one-time updates.
  Responses with `frameTick` older than the last applied frame are ignored
  (stale poll race). On fetch failure, patches `world.lmStatus = "disconnected"`
  while keeping the last-known snapshot and sets `statePollFull` so the next
  poll retries with a full snapshot.
  **Offline behavior**: the last good frame stays on
  screen and the sidebar status dot goes gray (`#9E9E9E`, `renderSidebar()`)
  with the hint "Showing last frame; retrying /state…"
  — distinct from `lmStatus: "offline"` (Ollama
  unreachable, Flask up) and `"compute_error"` (GPU memory error), each with
  its own dot color/label in `renderSidebar()`.
- `DISTRICTS_POLL_MS = 3000` drives `pollDistricts()` (`GET /districts.js`
  with optional `?since=<districtsEpoch>`) on a slower cadence since
  districts/roads change only when district/tile/terrain/road data mutates
  server-side. The viewer tracks `districtsEpoch` from each full response;
  when the server returns `{unchanged: true, epoch}`, the last payload is
  kept (no parse/merge of district tiles/terrain). The first terrain-cache
  build starts immediately at page kickoff (via `scheduleTerrainCacheBuild`)
  using `STARTER_DISTRICTS_JS` as a fallback — it does **not** wait for
  `/districts.js`. When the served district-id list or `districtsEpoch`
  changes, `pollDistricts()` nulls `terrainCanvas` and rebuilds.
- The render loop is **decoupled from polling** via `requestAnimationFrame`:
  `tick()` (`viewer/renderloop.js`) redraws every animation frame from
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
  `postControl()` (`viewer/controls.js`) with optimistic local flips
  reconciled by the next poll; keyboard shortcut `R` also resets
  (`viewer/controls.js`), ignored while focus is in an input, textarea, select, or
  contenteditable field. Reset additionally prompts for the `SIM_RESET_PASSWORD`
  value (default `reset` when unset) after the confirm dialog; cancel/empty
  aborts, and HTTP 401 shows an alert — see [04-http-api.md](04-http-api.md).

## Canvas / world rendering

- `WORLD_W = 5200`, `WORLD_H = 5400` (`viewer/setup.js`) must match
  the `sim_engine` package's `WORLD_W`/`WORLD_H` (`constants.py:860-861`) exactly — the
  comment in `viewer/setup.js` says so explicitly.
- **Offscreen terrain cache**: static terrain (zones, crops, trees, dock,
  ocean) is rendered once into an offscreen `terrainCanvas` and blitted each
  frame instead of re-tiling per frame (`buildTerrainCache`/
  `scheduleTerrainCacheBuild`). Tiling uses `createPattern` in
  `fillRectWithTile` / `fillRectWithTiles` (sprites/tiles.js): each 16×16 tile grid
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
  `viewer/setup.js` to log build stage splits and performance marks.

**Crop/tree growth stages (`CROP_GROWTH_ENABLED`, living-ecosystem Phase 2):**
terrain — including crops and trees — is baked into the static
`terrainCanvas` above, so growth is **not** animated per frame; it is instead
a small number of discrete stages that key into the same cache-invalidation
mechanism the season tint already uses.

- `ecologyStagesForTerrain()` (`viewer/render.js`) reduces the top-level
  `world.districtEcology` list (a sibling of `civilization`, see
  [05-world.md](05-world.md)) to a `{districtId: stage}` map plus a
  stable string key. `buildTerrainCache()` passes the map through
  `drawTiledWorld` (sprites/world.js) → `drawStarterProps` (sprites/world.js)
  and records the key in
  `lastEcologyStageKeyRendered`, mirroring `lastSeasonRendered`.
  `pollState()` compares the freshly computed key against
  `lastEcologyStageKeyRendered` each poll and rebuilds the cache exactly once
  on a mismatch — the same edge-triggered pattern as the season check, not a
  new timer.
- `drawStarterProps` (sprites/world.js) / `drawCrop`/`drawTree` (sprites/props.js) all default their new
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
  `drawTree` also takes `stage`; `TREE_GRIDS` (sprites/props.js) is keyed by
  `` `${season}|${stage}` `` (was season-only) and only `"sparse"` produces a
  visually distinct (shorter) canopy grid — `"healthy"`/`"lush"` reuse the
  unmodified per-season rows.
- **Hysteresis** lives entirely in the server-side projection (see
  [05-world.md](05-world.md)) — the viewer just reads whatever `stage` string
  it's given, so a ratio hovering on a boundary can't thrash the terrain
  cache multiple times per second.
- **Seasonal color grading** (`applySeasonTintForKind`, `viewer/render.js`): baked into
  the terrain cache at build time via per-district, per-terrain-kind passes —
  forest/farm/beach/quarry/village each get distinct spring/autumn/winter
  grading clipped to that district's bounds; ecology `stage` scales tint
  strength through `STAGE_TINT_FACTOR`; barren districts get an extra brown
  overlay; farm summer gets a subtle lighter pass. Winter ground speckle
  (`drawWinterGroundAccent`, sprites/core.js) and tree snow caps
  (`drawSnowCap`, sprites/core.js) are drawn from `sprites/props.js` callers.
  The terrain cache key is
  `season|ecologyStageKey` (`terrainVisualCacheKey`).
- **Day/night overlay** (`nightAlpha`, `viewer/render.js`): ramps a navy overlay from
  `calendar.dayFraction` (night is the last 25% of each day). Wider twilight
  (`TWILIGHT_START = 0.62` … `TWILIGHT_END_DUSK = 0.78`, dawn from
  `TWILIGHT_START_DAWN = 0.92`); quadratic ease-in/out; peak
  `MAX_NIGHT_ALPHA = 0.58`; optional night desaturation pass when
  `na > 0.35`; golden hour (`ENV_EFFECTS_ENABLED`) peaks at
  `GOLDEN_HOUR_MAX = 0.22` over those bands plus a warm rim pass at band edges.
  Applied as full-canvas `fillRect` after agents/structures each frame.
- **World clock HUD** (`WORLD_CLOCK_HUD_ENABLED`): a fixed, non-interactive
  badge over the map projects the existing `calendar.season` and
  `calendar.dayFraction`/`isNight` as one of dawn, day, dusk, or night. It is
  a pure read of the latest `/state` snapshot and disappears cleanly when the
  echoed flag is off.
- **Light glow** (`ENV_EFFECTS_ENABLED`): while the night overlay is active,
  each structure flagged `light: true` in the `/state` structures payload
  whose district is in `civilization.litDistricts` gets a warm radial
  gradient composited over the night overlay — core glow (`LIGHT_GLOW_RADIUS
  = 200`) scaled by `na / MAX_NIGHT_ALPHA`, plus an outer halo
  (`LIGHT_GLOW_HALO_RADIUS = 280`) when deep night. No glow by day or for
  unfueled lights (they simply lack the flag/district entry that night).
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
- **Zoom**: `zoomLevel` (`viewer/setup.js`) scales `canvas.style.width/height`
  over the fixed-resolution backing store (`applyZoom`, `viewer/setup.js`);
  +/- buttons multiply by 1.25/0.8, scroll-wheel zoom
  is wired, and "Fit" computes the zoom that fits the whole
  world.
- **Minimap**: `#minimap` (220×160, `css/base.css`, with a
  `body.divine-bar-visible` bottom-offset override in `css/divine.css`),
  `renderMinimap()`
  (`viewer/minimap.js`) draws a scaled-down world plus a viewport rectangle
  from scroll position/`zoomLevel`; clicking it
  recenters the main view.
- **Two side panels**, both filled by `renderSidebar()`:
  - **Left panel** (`#convPanel`), titled "Agents & Activity": the **Agents**
    section (`#agentsPanel` — rollup header, living-agent list with
    vitals/crisis sort, and the selected-agent detail panel), then a
    **Council** section (shown when council data exists). A **Conversations**
    section (`#conversationLog`) and a **Settlements** section
    (`#settlementsSection`) also live here but are **hidden by default** behind
    the client-side viewer toggles `SHOW_CONVERSATIONS` / `SHOW_SETTLEMENTS`
    (`viewer/setup.js`, both `false`). These are viewer-only display flags, not
    server `config.flags`; flip either to `true` to restore its section. The
    underlying `world.conversation` and `civ.settlements` data still arrives in
    `/state` regardless. `#agentsPanel` is a flex child of `#convPanel` with its
    own `overflow-y: auto` (`css/council.css`), so a long agent roster scrolls
    within the section instead of being clipped by the panel's own
    `overflow: hidden`.
  - **Right panel** (`#sidebar`): the "AI Simulation World" title, LM/server
    status dot+label, then `#sidebarBody` (a flex column, `overflow: hidden`)
    holding **Time** (`#timePanel`, EST clock/uptime/calendar — fixed,
    `flex-shrink: 0`, natural height), **Civilization** (`#civPanel`
    era/level/structures/active builds/resources), and **Activity**
    (`#activityLog`, the world-event feed, `#actList`). Civilization and
    Activity are `flex: 1 1 0; min-height: 0; overflow-y: auto` (`#civPanel`
    rules in `css/panels.css`; `#activityLog` rules — plus the `overflow-y:
    auto` on its child `#actList` — in `css/council.css`),
    so they split the space remaining after Time equally and each
    scrolls independently. The Civilization panel's **Village resources**
    row (`#civResources` headline + `#civResourceList` chips) shows
    `civ.stockpile` **plus** every agent inventory, keyed through
    `resourceRegistry`, filtered to `n > 0` (retired or zero holdings never
    render), with chip colours from `resourceRegistry`. The headline count
    (`totalVillageResources()`) is the sum of `villageResourceBreakdown()` so
    the number and chips cannot disagree. Sidebar change detection includes
    `villageResourceBreakdown()` inside `sidebarKey` (`viewer/sidebar.js`); the
    raw `civ.stockpile` dict is intentionally **not** in the key — it is a
    ~40-key map that changes nearly every tick and would force a sidebar
    re-render on every poll; the breakdown is the stockpile's proxy in this
    key. A third scrollable **Chronicle** panel is a curated
    projection of top-level `world.chronicle`, distinct from the raw Activity
    feed. It preserves its scroll position across snapshot updates and is
    hidden cleanly when `CHRONICLE_ENABLED` is off.
- **`ACTION_LABELS`** (`viewer/sidebar.js`) maps each `DECISION_ACTIONS`
  name to a short display gerund (e.g. `collect_resource` → "gathering");
  `humanizeAction(agent)` (`viewer/sidebar.js`) special-cases
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
plan's stretch item): the static `terrainCanvas` cache (`buildTerrainCache`,
  `viewer/render.js`; `scheduleTerrainCacheBuild`, `viewer/setup.js`)
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

The history transcript modal (`openCouncilTranscript`, `viewer/council.js`) reads
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

## css/*.css: split stylesheet

`simulation/viewer.css` was split (Phase 3 of the file-modularization plan)
into 6 plain files, loaded via ordered `<link rel="stylesheet">` tags in
`index.html` and served from fixed Flask routes under `/css/<name>.css`
(see [12-ops.md](12-ops.md)). There is no bundler and no CSS
preprocessor — every file lands in the same document cascade, so load order
matters for specificity ties exactly as it did when the rules lived in one
file: a later file's same-specificity rule still overrides an earlier file's.
The split is a pure move (grouped by what the rules style; the original file
had almost no section comments to preserve) — no selector, property, or
value was changed, and no rule was reordered relative to its neighbors.

| Order | File | Contents |
|---|---|---|
| 1 | `css/base.css` | Reset, `#wrap`/`#canvasWrap`/`#world`, map controls (`#pauseBtn`/`#resetBtn`/`#zoomInBtn`/`#zoomOutBtn`/`#zoomFitBtn`), `#worldClockHud`, `#minimap` |
| 2 | `css/panels.css` | `#sidebar`/`#convPanel` shared chrome (headers, `#lmStatus`, `#sidebarBody`, `.panel-section`), `#civPanel` civilization stats (project sprite, resource/custom/recipe/rule chip lists, progress bar, bench groups) |
| 3 | `css/agents.css` | `#agentList`, `#agentRollup`, `#agentDetail`, `#agentFollowBtn`, `.agents-panel-head`, the deceased-agents modal (`#deadAgentsBtn`/`#deadAgentsModal`/`#deadAgentsDialog`/`#deadAgentsList`) |
| 4 | `css/council.css` | Council transcript modal, `#worldLoading`, conversation/activity/chronicle lists (`#convList`/`#actList`/`#chronicleList`), council banner/panel (`#councilBanner`, `.council-card`, `#councilHistory`), the Daily Council Assembly modal (`#councilAssemblyModal` and its canvas/tally/transcript rules) |
| 5 | `css/divine.css` | Divine Console bottom bar and modal (`.divine-bar`, `.gbtn`, `.modal`, every `.divine-*`/`.god-sight-*` rule), `#tooltip`, `#godPublicBanner`, `.chronicle-presentation-thunder` |
| 6 | `css/responsive.css` | The two `@media (max-width: …)` blocks (900px and 620px breakpoints) |

## sprites/*.js: pure stateless drawing

`simulation/sprites.js` was split (Phase 2 of the file-modularization plan)
into 8 plain files, loaded via ordered `<script>` tags in `index.html` and
served from fixed Flask routes under `/sprites/<name>.js`
(see [12-ops.md](12-ops.md)). There is no bundler and no ES modules — every
file executes in the same shared global scope, so load order encodes the
dependency order: a later file may reference a const/function from an
earlier one, never the reverse.

| Order | File | Contents |
|---|---|---|
| 1 | `sprites/core.js` | Shared mutable state (`spriteSeason`, `seasonalAgentAccentsEnabled`), pixel-grid primitives (`tileFromStrings`, `drawPixelGrid`, `drawPixelSprite`), tile-source canvas cache, `drawSnowCap`/`drawWinterGroundAccent`, road-edge `PATH_CELLS` plumbing |
| 2 | `sprites/tiles.js` | Color palette `C`, path-blend tiles, `fillRectWithTile(s)`, all terrain `TILE_*` grids, ocean tile builder |
| 3 | `sprites/props.js` | Starter-world decor: trees (`TREE_GRIDS`, `drawTree`/`drawTreeStump`), decorative house/market-stall/cave-entrance, crops, fences, dock, well, rocks |
| 4 | `sprites/structures.js` | Agent-built `STRUCTURE_GRIDS`, `getStructureGrid` resolution, wear/ruin rendering, forge smoke, weather-particle and activity-dust helpers |
| 5 | `sprites/agents.js` | Role-keyed 24×32 generator (`ROLE_SPRITE_DEFS`, `_roleSpriteCache`), legacy name grids (`AGENT_SPRITES`), accessories, `tombstoneSprite`, `BELIEF_TINTS`, `drawAgentSprite` |
| 6 | `sprites/world.js` | `KIND_TILE`, `STARTER_DISTRICTS_JS`, `drawStarterProps`, district terrain/tile overlays, `drawTiledWorld` |
| 7 | `sprites/wildlife.js` | Ambient wildlife: PNG-sheet blit, canvas-helper, and procedural-grid fallbacks |
| 8 | `sprites/shipments.js` | Goods-in-motion cart/boat sprites |

- Every function takes `ctx` plus plain data and paints; the only module
  state is a season mirror plus viewer-set seasonal-agent-accent gate
  (`spriteSeason`/`seasonalAgentAccentsEnabled`, sprites/core.js) and a
  per-season tree-grid cache (`TREE_GRIDS`, sprites/props.js, built once per
  season).
- **Agent sprites**: living agents resolve in `drawAgentSprite`
  (sprites/agents.js) in order: (1) `tombstoneSprite(agent)` when
  `deceased && buried` (16×16 grid, scale 2 → 32px); (2) role-keyed sprite via
  `ROLE_SPRITE_DEFS[agent.role]` + lazy `_roleSpriteCache` per role and
  stand/walk frame (24×32 generator ported from Claude Design, hue 0, scale 1 →
  32px); (3) `AGENT_SPRITES[agent.name]`; (4) `genericAgentSprite(agent)`.
  Steps 3–4 use the legacy 16×16 ASCII body at scale 2. Role sprites suppress
  the `ACCESSORIES` overlay (gear is baked in); name/generic paths still draw
  accessories. Walk motion spreads leg columns ±1px on the walk frame (same
  cadence as before). Seasonal accents (`drawSeasonalAgentAccent`) and belief
  tints (`BELIEF_TINTS[beliefIds[0]]`) position from the drawn grid's real
  width/height so they land on head/neck at both scales. `genericAgentSprite`
  and `tombstoneSprite` stay cached per agent name.
- **Structure grid resolution order** — `getStructureGrid(structure)`
  (sprites/structures.js), in order: (1) canonical level-30 house
  (`type === "house" && level >= 30` → `LEVEL30_HOUSE_GRID` always); (2)
  persisted LLM sprite, if upgraded with a non-degenerate `structure.sprite`
  spec; (3) upscaled seed grid — `upgradedSeedGrid` scales the built-in
  `STRUCTURE_GRIDS` entry by `min(visualTier, 3)`; (4)
  `STRUCTURE_GRIDS[structure.type]` tier-1 seed grid; (5)
  `spriteGridFromSpec(type, sprite)` for a first-time custom sprite; (6)
  `STRUCTURE_GRIDS[structure.visualStyle]`, a named built-in style borrowed
  by a custom blueprint; (7) procedural fallback, or `drawGenericStructure`
  (colored block with the type's first letter) if nothing resolves — all in
  `sprites/structures.js`.
- **Seasonal variants**: tree grids are cached per season
  (`TREE_GRIDS[season]`, sprites/props.js); in winter `drawSnowCap`
  (sprites/core.js) layers onto trees, rocks (sprites/props.js), and any
  agent-built structure's top edge (`drawStructure`, sprites/structures.js).
  Seasonal agent accents also read the module-level `spriteSeason` mirror
  rather than an explicit `season` param.
  `setSpriteSeason(season)` (sprites/core.js) is called once per `/state` poll
  from `pollState()` (`viewer/polling.js`) so rendering tracks
  `calendar.season`. `SEASONAL_AGENTS_ENABLED` uses the same mirror and a
  viewer-set boolean to draw small post-body seasonal accessory pixels on
  living agents (winter wool cap/scarf, spring leaf pin, summer straw brim,
  autumn scarf); turning it off leaves their base and named accessory sprites
  unchanged.
- **`STARTER_DISTRICTS_JS`** (sprites/world.js, 12 entries) is a
  client-side fallback used before the first `/districts.js` fetch resolves;
  a comment flags it **"MUST be kept in sync with sim_engine.py's
  STARTER_DISTRICTS bounds/kind/label"** — see
  [05-world.md](05-world.md) for the server-side list it mirrors.

## viewer/*.js: split viewer client script

`simulation/viewer.js` was split (Phase 4 of the file-modularization plan)
into 16 plain files, loaded via ordered `<script>` tags in `index.html`
(after `sprites/*.js`, in the same relative position the single
`viewer.js` tag occupied before) and served from fixed Flask routes under
`/viewer/<name>.js` (see [12-ops.md](12-ops.md)). There is no bundler and no
ES modules — every file executes in the same shared global scope (including
the one `sprites/*.js` and `roles.js` already populate), so load order
encodes the dependency order: a later file may reference a const/function
from an earlier one, never the reverse. The split follows the original
file's own `// ====...` banner comments (and, inside the large Divine
Console banner, its own lighter-weight `// ---...` sub-banners) — a pure
move, no logic changed.

| Order | File | Contents |
|---|---|---|
| 1 | `viewer/setup.js` | Thin-viewer contract banner, canvas/DPR setup, offscreen-terrain-cache scaffolding, zoom (`zoomLevel`/`applyZoom`/`zoomFit`), all feature flags (`SURVIVAL_ENABLED` etc.), viewer-only display toggles (`SHOW_CONVERSATIONS`/`SHOW_SETTLEMENTS`) |
| 2 | `viewer/state.js` | World snapshot: `MOCK_STATE`, module-level `world`, `mergeStateDelta()`, districts cache (`districtsData`/`districtsKey`/`districtsEpoch`), `getDistricts`/`findDistrictBounds`/`getDistrictBounds` |
| 3 | `viewer/render.js` | Convenience accessors (`getAgents`/`getCiv`/etc.) plus drawing: terrain cache build (`buildTerrainCache`), season/night/golden-hour/weather overlays, `drawWorld`, per-agent/structure drawing (`drawAgent`, `drawStructureWithShadow`, hit-flash) |
| 4 | `viewer/sidebar.js` | Sidebar render: `renderSidebar()`, `ACTION_LABELS`/`humanizeAction`, benchmarks, agent detail/rollup/panel, deceased-agents modal, founding/disaster banners, `renderWorldClockHud` |
| 5 | `viewer/council.js` | Council panel (`renderCouncil`), council transcript modal (`openCouncilTranscript`), Daily Council Assembly modal, settlements |
| 6 | `viewer/minimap.js` | Minimap render (`renderMinimap`) and click/drag-to-navigate |
| 7 | `viewer/polling.js` | `/state` polling (`pollState`), `applyFlags`, social-tie/wildlife/shipment drawing (`drawSocialTies`, `drawWildlife`, `drawShipments`) |
| 8 | `viewer/controls.js` | Pause/Resume/Reset controls (`postControl`, `syncPauseButton`, `doReset`), reset keyboard shortcut |
| 9 | `viewer/renderloop.js` | Render loop (`tick`/`tickBody`), decoupled from polling |
| 10 | `viewer/divine-bootstrap.js` | Divine Console (Sovereign God mode Phase 7) state vars, DOM element refs, `DIVINE_FEATURES` registry, feature guide, agent/pin action select population |
| 11 | `viewer/divine-auth-sight.js` | Divine Console auth/fetch plumbing (`godApiFetch`), Sight intervene helpers/diff, bottom-bar effects/pips/pulse, sight overlay drawing, preview controller + irreversible-form helpers, favorites |
| 12 | `viewer/divine-modal.js` | Divine Console bottom bar/modal/tab wiring (`openDivineModal`/`showGodTab`), shared tooltip engine, generic preview→apply wiring (`wireDivineForm`), preview/outcome/error render helpers |
| 13 | `viewer/divine-sight-voice.js` | Divine Console Sight tab render (`renderGodSight`) + checkpoint restore, Voice presets (load/save/apply) |
| 14 | `viewer/divine-voice.js` | Divine Console Voice tab: proclamation/providence/private omen, whisper campaign, sampling/distortion, crowd compulsion, dream broadcast, veto resolve, bargain predicate, oracle hints, architect cells |
| 15 | `viewer/divine-miracles-story.js` | Divine Console Miracles tab (`agent_vitals`/`grant_resource`/`structure_condition`), shared Story/Laws modifier editor, story primitives editor, Story/Compile/Laws tabs |
| 16 | `viewer/divine-history.js` | Divine Console History power tools, gate + passive per-poll refresh, public banner, `renderDivineConsole()` entry point, and the page's bootstrap kickoff (`requestAnimationFrame(tick)`, `pollState()`, `pollDistricts()`) |

## Civ-1 physical props

`civilization.physicalProps` renders up to three large, moored sailboats in the
expanded starter ocean beside the beach. Their positions are fixed coastal
moorings, not structure positions; they remain server-derived decoration and
do not create client-side resource or pathing state.

## Weather sky tint + particles (`WEATHER_ENABLED`, living-ecosystem Phase 4)

Reads the top-level `world.weather` projection (`{state, since, districts}`,
see [05-world.md](05-world.md)) — a sibling of `civilization`, same
placement as `socialTies`/`districtEcology`/`shipments`.

- **Sky tint** (`weatherSkyAlpha(weather, nightAlpha)`, `viewer/render.js`):
  composited in the **same full-canvas overlay stage** as the existing
  night overlay — drawn immediately after it, before the golden-hour band —
  so the two stack coherently instead of fighting. `WEATHER_SKY_ALPHA =
  {clear: 0, gathering: 0.17, storm: 0.41, clearing: 0.20}` is a step ramp
  keyed by `weather.state` (the server sends only the current state, not a
  within-state progress fraction, so this is a discrete ramp across state
  transitions rather than a continuous animation). Color is a cooler
  green-slate (`rgba(12, 28, 32, …)`), distinct from the night overlay's
  navy, so a storm during the day is still visually legible as weather
  rather than dusk.
- **Darkness clamp:** `MAX_NIGHT_PLUS_WEATHER_ALPHA = 0.68`. The weather
  alpha actually drawn is `min(rawWeatherAlpha, 0.68 - nightAlpha)`, so
  night + storm can never exceed a combined 0.68 before sequential
  `fillRect` alpha compounding (`1 - (1-night)*(1-weather)`). Worst case
  (winter storm, deep night `nightAlpha` up to `MAX_NIGHT_ALPHA = 0.58`, no
  golden hour since deep night falls outside the golden-hour dawn/dusk bands,
  no light glow since the district is unlit): `weatherAlpha` clamps from a raw
  0.41 down to 0.23, combined visible darkening remains well short of
  opaque; agents/structures/terrain stay identifiable underneath the tint.
- **Locality split:** the **base sky tint** is **world-wide** (same stage as
  the also-global night overlay). During `storm`/`clearing`, districts listed
  in `weather.districts` additionally receive a **local storm veil**
  (`drawDistrictStormVeil`): a darker translucent fill clipped to each
  district's `bounds` rect from `/districts.js` (`getDistrictBounds(id)`).
  The global tint reads as atmosphere; the veil reads as "the storm is here"
  while damage targeting stays localized per [08-systems-economy.md](08-systems-economy.md).
- **Particles** (`drawWeatherParticles`, `viewer/render.js`; `drawWeatherParticle`,
  sprites/structures.js): drawn every frame (not baked into `terrainCanvas`). Active during
  `storm` (full intensity), `clearing` (45% intensity), and `gathering`
  (light drizzle at intensity `0.18`). Cap **`380`**; divisors
  **`11000`/`7200`**; sheet every **6th** rain streak; **two depth layers**
  (background 60% count ×0.5 alpha, foreground 40%); wind-varied rain
  angles/lengths from per-particle hash; snow uses a flake cluster with slower
  fall and horizontal wobble. District rain merges into the global layer with
  bounds clip (no separate district-local rain pass). Snow replaces rain when
  `calendar.season === "winter"`. Positions deterministic from `frameTick` and
  `weatherParticleHash` — no retained state.
- **Cap by visible area, not world area:** global particle count is
  `min(380, floor(visibleW * visibleH / densityDiv * intensity))`, where
  `visibleW`/`visibleH` come from the same `canvasWrapEl.scrollLeft/scrollTop
  / zoomLevel` viewport math `drawWildlife`/`drawShipments` already use.
- **Lightning** (`drawLightningFlash`, `viewer/render.js`): during `storm` only,
  deterministic rare full-canvas white/blue flashes (~8–18 display frames,
  ~130–300ms at 60fps), keyed off local `renderFrame` (rAF), not
  `frameTick`, in `LIGHTNING_BUCKET_FRAMES` (540) windows — no new
  `/state` fields. Drawn immediately after the sky tint, before golden hour;
  alpha ~0.10–0.20 so readability is preserved. ~12% of buckets may flash
  (~every ~75s at 60fps), not every second.
- **Weather chip on World Clock HUD** (`renderWorldClockHud`, `viewer/sidebar.js`,
  `WORLD_CLOCK_HUD_ENABLED`): when `WEATHER_ENABLED` and `world.weather` are
  present, appends the title-case state label after season+phase, e.g.
  `spring day · Storm`.
- **Disaster banner** (`#disasterBanner`, `viewer/sidebar.js`): mirrors the founding
  banner pattern — edge-detects new `world.chronicle` entries with
  `kind === "disaster"` via a first-snapshot-seen `Set` on `frame` (no replay
  on page load). Shows storm-colored slate/teal banner for 5.5s with entry
  text. Gated on `CHRONICLE_ENABLED`; edge-detection mirrors founding (runs
  whenever enabled — empty chronicle clears the seen Set).
- **Structure hit flash** (`trackStructureConditionDeltas`, `viewer/sidebar.js`;
  `drawStructureHitFlash`, `viewer/render.js`): client-only diff of structure
  `condition`/`isRuin` between polls; flashes when condition drops by at
  least `STRUCTURE_HIT_FLASH_MIN_DROP` (5 — enough to ignore passive decay
  ~0.025 per goods tick, still catches disasters at 40–70) or when
  `isRuin` newly becomes true. That id flashes white/cyan outline + bright
  overlay for ~800ms wall clock. Gated on `STRUCTURE_WEAR_ENABLED`.
- **Gate:** `WEATHER_ENABLED = false` — `weatherSkyAlpha` returns 0 (no sky
  change), lightning/veil/particles all no-op, and the World Clock HUD omits
  the weather chip, regardless of `world.weather`'s presence/content.

## Atmosphere rendering (permanent defaults)

The Phase 4 atmosphere pack (lighting, seasonal terrain grading, weather
particles, Divine Console chrome) is **always on** in the viewer — not gated by
module-level flags. Only `WEATHER_ENABLED`, `ENV_EFFECTS_ENABLED`, and
`GOD_MODE_ENABLED` still toggle their respective subsystems.
`docs/archive/plan-visual-*.md` files are historical design records (superseded banners
note the removed flag gates); this section and the viewer implementation are the
runtime contract. Implementation plans: `docs/archive/plan-visual-1-day-night-lighting.md`,
`docs/archive/plan-visual-2-seasonal-terrain-grading.md`,
`docs/archive/plan-visual-atmosphere-systems.md`.

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
  | farm | `cow`, `rabbit`, `chicken`, `mouse` | `meat` |
  | farm | `bee` | none — decorative, not huntable |
  | beach | `fish`, `crab`, `gull`, `turtle`, `seal` | `fish` |

- **Rendering (`sprites/wildlife.js`):** `drawWildlifeCreature` dispatches in order:
  sheet blit for kinds in `WILDLIFE_SHEET_FRAMES` (all 16 when atlas present)
  → canvas silhouette helpers → procedural pixel grids — nothing ever renders
  blank.
  - **Spritesheet loader:** at module load, `preloadWildlifeSheet()` fires
    a fire-and-forget `Image()` fetch for `/wildlife.png` (served beside the
    `sprites/*.js` files; see [12-ops.md](12-ops.md)). `_wildlifeSheetReady` is
    set only on successful load with non-zero dimensions; `onerror` or a
    missing file (404) leaves it `false` permanently for that page load.
    When ready **and** the kind has an entry in `WILDLIFE_SHEET_FRAMES`,
    `tryDrawWildlifeFromSheet` blits via `ctx.drawImage` with
    `imageSmoothingEnabled = false`, using optional `destW`/`destH` overrides.
    Extracted frame regions are cached in `_wildlifeSheetBlitCache` keyed by
    source rect (same module-level cache idiom as `_tileSourceCanvasCache`).
    Frame entries may be a bare `{ sx, sy, sw, sh, destW?, destH? }` or
    `{ stand, alt? }` with the same `frameTick` cadence as procedural
    animation. **`WILDLIFE_SHEET_FRAMES` maps all 16 user-art kinds** packed from
    `simulation/assets/wildlife/*.png` into `/wildlife.png` (variable source
    rects; dest sizes fit tier boxes preserving aspect ratio).
  - **Canvas silhouette helpers:** fallback when the sheet is missing or not
    ready; restored pre-sheet canvas primitives (V-wing birds, owl blink,
    squirrel tail-flick, bee wing flap, etc.) via `WILDLIFE_CANVAS_HELPERS`. Each helper is drawn with a tier scale transform (`WILDLIFE_CANVAS_SCALE_BY_TIER`:
    large ~1.8, mid ~1.3, small 1.0) around the creature anchor so size tiers
    remain visible. Per-kind alt-frame cadence matches the former helpers
    (8–20 ticks where animated).
  - **Procedural fallback:** each kind also has a pixel-grid entry in the
    `WILDLIFE_SPRITES` table (flat-color, black-outline idiom matching
    agent sprites). `drawWildlifeCreatureProcedural` is the last resort when
    neither sheet nor canvas helper applies. An entry may be a bare grid
    (static kinds) or `{ stand, alt? }` (animated kinds). Drawing uses
    `drawPixelSprite` with **per-tier scale** via `WILDLIFE_SIZE_TIER` /
    `WILDLIFE_TIER_SCALE`. Approximate on-screen sizes:

  | Tier | Sheet dest | Canvas scale | Procedural scale | Kinds |
  |---|---|---|---|---|
  | large | ≈44 px max side | ~1.8 | 2 | `deer`, `boar`, `cow`, `seal` |
  | mid | ≈34 px max side | ~1.3 | 2 | `fox`, `owl`, `turtle`, `rabbit`, `chicken`, `gull`, `bird` |
  | small | ≈26 px max side | 1.0 | 1 | `mouse`, `squirrel`, `fish`, `crab`, `bee` |

  Cosmetic motion also includes the caller-side `bob` sine offset in
  `drawWildlife` (`viewer/polling.js`); `frameTick` drives frame swap only and does
  not invent a second position.
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
  `bee` is never a valid target) — see [07-actions.md](07-actions.md).
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
- **Sprite:** `drawShipment(ctx, mode, x, y, cargoColor)` (sprites/shipments.js) picks
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
[docs/archive/plan-sovereign-god-mode-v2.md](../docs/archive/plan-sovereign-god-mode-v2.md)'s
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

**Divine Console chrome (permanent default).** When `GOD_MODE_ENABLED` is on,
the bar uses 100px viewport clearance, a taller bar with gold underline on the
active feature button, a sticky **preview strip** at modal top
(`#divinePreviewStrip`) showing command name + reversibility badge +
Apply/Discard when a preview is cached, collapsible preview panel
default-expanded, fieldset hierarchy with crimson left border on irreversible
sections, a **pin row** for the last applied intervention (link to History), bar
brand secondary line `N interventions` from `recentPublicInterventions` length,
and **Ctrl/Cmd+Enter** applies when a preview is valid. All UX is viewer-only
— no new routes or engine mutations. **`GOD_MODE_ENABLED` off:** bar hidden.

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

**Modal width (Divine Console improvements, Phase 1).** Default `#divineModal`
width is `min(680px, 96vw)`. `openDivineModal(name)` toggles a `wide` class on
`#divineModal` for **matrix**, **story**, **laws**, and **compile**; `closeDivineModal()`
removes it. Wide modals use `min(960px, 96vw)`. All other features keep the
default width. Presentation-only — no route or engine changes.

**Operator context + speed (Divine Console improvements, Phase 2).** Viewer-only
UX; engine preview/apply payloads unchanged (durations remain frames server-side).

- **Agent focus (`godFocusAgentId`).** Module-level focus id (initially `null`).
  Sidebar agent selection sets `godFocusAgentId` to match `selectedAgentId`
  (cleared on deselect). `setGodFocusAgent(id, opts)` sets focus, optionally
  mirrors sidebar selection, and refreshes agent `<select>`s. `populateGodAgentSelects()`
  rebuilds options via `godAgentOptionsHtml(preferredId)` where `preferredId`
  is `godFocusAgentId` then `selectedAgentId`, but still preserves an open
  dropdown's current value when that agent is still living (existing
  preserve-value pattern). `#godAgentFilter` (modal head) narrows living-agent
  option lists by name/role/id substring; repopulates on filter change.
- **Canvas pick.** When `GOD_MODE_ENABLED_FLAG` is true, a canvas click on a
  living agent (via `clientToWorld` + `agentAtWorldPoint`) sets both
  `selectedAgentId` and `godFocusAgentId`, syncs sidebar selection/detail,
  centers the camera (`centerCameraOnAgent`), and refreshes agent selects.
  Empty-space clicks do **not** clear focus. Hover (`hoveredAgentId`) and
  existing camera controls are unchanged. **Priority:** agent hit-test wins
  over structure pick (Phase 7).
- **Canvas structure pick (Phase 7).** While the Miracles or Story divine
  modal is open and God mode is on, a canvas click that misses agents but
  hits a non-ruin structure (`structureAtWorldPoint`, bounding box from
  `getStructureRenderSize`) sets structure targets: `#godStructureSelect` on
  Miracles; every `.godPrimStructure` select in Story primitive rows on Story.
  Does not invalidate an cached preview until the operator edits a wired field.
- **Seconds-first durations.** Divine Console duration number inputs display
  **seconds** (labels/placeholders). Preview envelope builders convert with
  `godSecondsToFrames(sec)` → `Math.round(sec * 30)` before setting
  `durationFrames`; blank still means until-revoke / omit. Capability bounds
  from `applyGodCapabilitiesToForms()` are converted frames→seconds for
  `min`/`max`/`default` on those inputs. Display helpers (`godDurationLabel`,
  Sight countdowns) still show both seconds and frames.
- **Keyboard (modal open).** Shortcuts are ignored while focus is in
  `input`/`textarea`/`select` except **Ctrl/Cmd+Enter** (Apply). With the
  modal open: digit keys **1–9** open the Nth **visible, enabled** bar feature
  in DOM order (Unlock skipped when `GOD_AUTH_REQUIRED_FLAG` is false; Compile
  skipped when hidden); **`/`** focuses `#godAgentFilter`; **`S`** calls
  `refreshGodSight()` when effectively authorized; **Ctrl/Cmd+Enter** applies
  the cached preview (same irreversible guard as the Apply button).
- **Favorites.** Up to four shortcuts in `sessionStorage` key
  `divineFavorites`: `{feature, fieldsetId?, label}`. `#divineBarFavorites`
  renders chips on the bar; click opens the feature and scrolls to
  `fieldsetId` when set. **Pin this section** (`#divinePinSectionBtn`, modal
  head) pins the current scroll target; fieldset legends with `data-fav` support
  double-click pin. Token is never stored in favorites or `localStorage`.
- **Irreversible confirm.** Apply on `.divine-fieldset-irreversible` forms and
  on `#divinePreviewStrip` when the owning fieldset is irreversible requires
  **hold Apply ~400ms** or typing the target agent's name (first agent
  `<select>` in the fieldset, else `godFocusAgentId` / `selectedAgentId`) into
  `#divineIrreversibleConfirmInput` (shown in the preview strip when relevant).
  Agent-less irreversible forms (e.g. mass repair) accept hold-only. Crimson
  `.divine-fieldset-irreversible` styling unchanged; reversible applies
  unchanged.

**Bar situational awareness (Divine Console improvements, Phase 3).**
Viewer-only HUD on `#divineBar`; no new poll loop — wired into the existing
`updateGodModeGate()` / `renderDivineConsole()` / `pollState()` →
`renderSidebar()` path.

- **`#divineBarEffects` chips** (between `.bar-brand` and `.bar-buttons`):
  compact clickable chips for **Providence** (on/off), **Omens** (count),
  **Laws/events** (active count), **Gates** (gate + possession aggregate),
  **Zones** (architect zones), and **Sampling** (agent sampling overrides).
  **Data sources:** when `godEffectivelyAuthorized()`, prefer the last Sight
  snapshot (`godLastSight` from `GET /control/god/sight`); always merge public
  fields from `/state` `world.god` (`providence`, `activePublicEvents`,
  `recentPublicInterventions` for pulse only). **Private counts** (omens,
  gates, sampling, zones) render only after Sight has been fetched while
  authorized — chips are omitted (not shown as "—") until then. **Providence**
  and **public law/event** counts may render from `/state` alone. Clicking a
  chip calls `openDivineModal()` for the owning feature and
  `scrollIntoView()` on a relevant fieldset/section when one exists (Voice →
  providence/omen fieldsets; Laws → `#godLawsActive`; Matrix → Mind/Will/Place
  sections).
- **Status pips on feature buttons.** Small count badges (`.gbtn-countpip`) on
  `.gbtn.voice`, `.gbtn.laws`, and `.gbtn.matrix`: Voice shows providence/omen
  activity; Laws shows active timed law/event count; Matrix shows
  gate+possession+sampling+zone aggregate. Unlock, History, Sight, Miracles,
  Story, and Compile stay clean unless a future phase adds signal.
- **Bar pulse.** When `recentPublicInterventions` gains a new id, edge-detect
  with the same `godSeenInterventionIds` set as `#godPublicBanner` and briefly
  add `.divine-bar-pulse` on `#divineBar` (CSS keyframe; disabled under
  `prefers-reduced-motion`, falling back to a static gold top border).
- **Sight soft-refresh for bar counts.** When authorized, no divine modal is
  open, and the last Sight fetch is older than ~30s (or Sight was never
  fetched), `maybeRefreshGodSight()` may call `refreshGodSight()` once —
  throttled, not on every 100ms poll — so private chips populate without
  spamming `/control/god/sight`. Between refreshes, chips and pips update from
  `godLastSight` plus public `/state` each poll.

**Sight HUD (Divine Console improvements, Phase 4).** Viewer-only; no new routes
unless noted. Engine `god_sight()` shape unchanged — architect zone summaries
still expose `id`, `kind`, `districtId`, `cellCount`, `expiresFrame`, and
optional `holdCount` only (no per-cell bounds in Sight).

- **Live Sight soft-refresh.** `maybeRefreshGodSight()` (same hook as bar
  refresh inside `updateGodModeGate()` / `pollState()` → `renderSidebar()`) uses
  two throttles: **~30s** when no divine modal is open (bar private chips); **~1.5s**
  when the Sight feature modal or Voice feature modal (adherence panel) is open
  and the operator is effectively authorized. No second poll loop.
- **Client-side diff.** Each successful `refreshGodSight()` compares the new
  snapshot to the prior one. For the Sight-selected agent (prefer
  `godFocusAgentId` when set) show detailed vitals/`lastAction`/`decisionGate`/
  `divineHold` deltas; other agents contribute compact one-line entries when
  changed. A **Changed since last look** strip at the top of `#godSightOutput`
  escapes all dynamic text via `escapeHtml()`.
- **One-click intervene.** Sight agent rows and relevant effect summaries expose
  small buttons (**Omen**, **Heal**, **Possess**, **Sampling**) that call
  `setGodFocusAgent()` + `openDivineModal(feature)` + `scrollIntoView()` on the
  owning fieldset (`godOmenFieldset`, `godVitalsFieldset`, `godPossessionFieldset`,
  `godSamplingFieldset`), reusing Phase 2/3 scroll helpers.
- **Canvas overlays (divine modal open).** While `#divineModalScrim` is open,
  the render pass draws viewer-only overlays from Sight (when authorized) plus
  public agent positions from `/state`:
  - **Focus ring** — gold ellipse on `godFocusAgentId` (else `selectedAgentId`).
  - **Architect zones** — dashed district-bounds rectangle per
    `godLastSight.architectZones[]` entry, colored by `kind` (`paint` gold,
    `door` cyan, `limbo` violet); cell-level outlines deferred until Sight
    exposes cells.
  - **Limbo / divine hold** — pulsing violet ring on agents with
    `architectLimbo.active` or `divineHold` in Sight.
  - **Anointed** — warm halo on agents with `anointment.active` in Sight.
  Does not mutate world state.

**Village pulse (Divine Console improvements, Phase 10).** When
`godLastSight.pulse` is present, a compact **Village pulse** card renders in
`#godSightOutput` immediately below the Phase 4 diff strip and above the
per-agent detail / intervene row. All dynamic text uses `escapeHtml()`. The
card summarizes: crisis agents (name + reason, capped display), stockpile
totals, open project count, Sage/elder status, weather state + affected
districts, active event titles, and providence active/expiry — mirroring the
engine `pulse` object without secret omen/providence text.

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
private omen, miracle subforms, story event, law): an `input`/`change`
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
  (active + countdown + `unacked` flag only, never the omen text — matching
  `god_sight()`'s own restraint), every active effect from the authenticated
  `activeEvents` list (including private-visibility ones, tagged with a
  `private` badge) with a countdown each, and a **Voice adherence** subsection
  listing `recentDivineResponses` entries for the selected agent (newest first,
  capped display): agent name, guidance kind/id, `follow`/`continue` stance,
  reason (including `missing_divine_response` when synthetic), frame, and the
  applied `action`. Private omen text never appears in this list.
- **Voice** — four independent subforms (`proclamation`, `providence` with
  a duration field, `private_omen` with an agent selector and duration, and
  `whisper_campaign` with shared theme + per-agent whisper rows up to 12),
  each following the Preview → Apply contract above. **Proclamation** applies
  as timed providence (same slot, duration, revoke) per
  [02-engine-core.md](02-engine-core.md); capabilities echoes optional
  `durationFrames` on the proclamation kind.
- **Voice presets (Phase 5).** `sessionStorage` key `divineVoicePresets` holds
  named presets for `proclamation`, `providence`, and `private_omen` (text,
  duration seconds where applicable, optional `presentation` for public kinds).
  Minimal controls at the top of the Voice tab: preset `<select>`, **Load**,
  **Save current** (prompts for a label), **Delete**. Loading a preset fills
  the matching fieldset inputs only — never auto-previews or applies.
- **Voice Adherence (Phase 5).** A dedicated panel section (reachable from the
  Voice feature window and cross-linked from Sight) fed by the authenticated
  `recentDivineResponses` ring from the latest `god_sight()` refresh. Two
  sub-panels beside each other:
  - **Adherence timeline** — compact chronological list (newest first, capped)
    of `follow`/`continue` entries with agent name, stance badge, reason
    snippet (~80 chars), and frame. All dynamic text via `escapeHtml`.
  - **Reply inbox** — same ring, reason-focused: agent name + full stated
    reason (truncated for layout), newest first; never shows private omen text
    or raw `normalizedCommand`. Distinct layout from the timeline so operators
    can scan stances vs read replies.
  Refresh reuses the same Sight fetch. Sight's per-agent subsection keeps a
  compact table (hide agent column) cross-linked to Voice → Adherence.
- **Voice presentation (Phase 5).** Proclamation and Providence fieldsets
  expose a **Presentation** control (`soft` \| `thunder`, default soft) passed
  through preview builders. `#godPublicBanner` toggles CSS classes
  `divine-banner-soft` / `divine-banner-thunder` from
  `recentPublicInterventions[].presentation` (setdefault `"soft"`). Chronicle
  list items add `chronicle-presentation-thunder` when `entry.presentation ===
  "thunder"`. Cognition/prompt text is unchanged server-side.
**Plain-language operator help (Divine Console improvements).** `DIVINE_FEATURES`
in `viewer/divine-bootstrap.js` is the source of truth for modal title, subtitle, bar tooltip,
and the always-visible `#divineFeatureGuide` callout (`.divine-feature-guide`)
inserted at the top of `#divineModalBody` on `openDivineModal()` and removed on
`closeDivineModal()`. Guide copy uses `textContent` only. Individual controls
keep delegated `#tooltip` hovers via `data-tip` JSON (`t` title, `d` detail) —
operator-facing strings use village/villager vocabulary (no API paths, no
preview/apply protocol jargon); Preview ≈ “check without changing the village”,
Apply ≈ “make it real”. Irreversible fieldsets retain crimson styling and say
“cannot be undone” in legend or `.divine-help`.

- **Matrix** — brain, memory, distortion, possession, dialogue, identity, zone,
  and checkpoint interventions (see phase list below). Each tool fieldset shows
  an always-visible `.divine-help` blurb under its legend plus `data-tip`
  tooltips on labels, controls, and Preview/Apply buttons (same delegated
  `#tooltip` handler as Voice). The panel opens with a short intro paragraph;
  tools are grouped under `.divine-matrix-section` blocks with stable ids
  (`#matrix-sec-mind`, `#matrix-sec-memory`, …) and section headings (Brain,
  Memory, Distortion, Possession, Dialogue & Bargain, Identity, Zones, Reload).
  **Matrix category nav (Phase 1):** a sticky chip row (`.divine-matrix-nav`)
  at the top of `#divineTab-matrix` scrolls the modal body to the matching
  section via `scrollIntoView` — chip labels Mind / Memory / Distortion / Will /
  Covenant / Form / Place / Time map to those sections (Brain→Mind,
  Possession→Will, Dialogue & Bargain→Covenant, Identity→Form, Zones→Place,
  Reload→Time). Sections use `scroll-margin-top` so headings clear the sticky
  chips. Phase 2 ships **Brain / Temperature
  Dial** (`agent_sampling`: agent + model + temperature slider + optional
  `top_p`/`top_k`/`min_p` + duration; `revoke_agent_sampling` to clear).
  Phase 3 adds **Memory Surgery** (`memory_insert`, `memory_delete`,
  `belief_plant` — three independent fieldsets with agent selectors).
  Phase 4 adds **Reality distortion** (`context_mask`: agent + mode radio
  — blue pill / red pill / dream / whisper chain — plus duration; dream and
  whisper modes accept JSON field inputs). Phase 5 adds **Possession pipeline**
  fieldsets: `decision_compulsion` (agent + pin action + duration/turns),
  `decision_veto_arm`, `decision_veto_resolve` (approve/reject/rewrite),
  `agent_possession` (agent + pin action + duration), and `revoke_decision_gate`.
  Phase 6 adds **Burning Bush** (`burning_bush_message`, `burning_bush_close`)
  and **Merovingian Bargain** (`merovingian_bargain`, `bargain_settle`) with
  predicate dropdowns and grant/vitals primitive fields. Pin actions use a curated `GOD_PIN_ACTIONS` select (labels from
  `ACTION_LABELS`). Preview → Apply via `wireDivineForm`. Sight shows gate
  status (`decisionGate`, `divineHold`) and pinned action summary; never
  `decisionGates` map contents on `/state`. Whisper campaigns remain under Voice.
  Phase 7 adds **Anoint** (`anoint`: agent + destiny + comma-separated stigmata
  tags + oracle hints textarea `revealFrame|text` per line + duration; `revoke_anoint`
  for one agent). Destiny/oracle never in `/state`; Sight shows anointment status
  summary only. Phase 8 adds **Identity** (`identity_edit`: agent + optional
  persona/personality/role + duration; `identity_copy_overwrite`: target + source
  + rate + optional sync memories + duration; `identity_forge_cancel` for one
  agent). `identityForges` never in `/state`; Sight shows forge progress summary.
  Phase 9 adds **Architect Zones** (`architect_zone`: kind select paint/door/limbo,
  district + cells textarea, paint terrain, key id, grant-key / limbo-hold agent
  multi-selects, duration, reversible paint; `architect_zone_cancel`; `architect_release_hold`).
  `architectZones` never in `/state`; paint is world-visible via terrain; door/limbo
  audit `public: false`. Sight: zone summaries + per-agent `architectLimbo` status.
  Phase 10 adds **Reload** (`checkpoint_create`: label + optional `replaceOldest`;
  `checkpoint_restore`: checkpoint picker from Sight; irreversible fieldset +
  strong confirm copy in preview). `checkpoints` never in `/state`; Sight lists
  id/label/frameTick/createdAt only. **Déjà Vu** (`deja_vu_replay`): enabled
  when `/control/god/capabilities` reports `kinds.deja_vu_replay.applyable`
  (requires `GOD_DEJA_VU_REPLAY`); agent picker + optional max steps; wired
  through `wireDivineForm`; Sight shows recent `decisionDigests` snippets when
  authorized. **Crowd compulsion** (`crowd_compulsion`): optional theme +
  shared duration (seconds) or remaining turns + repeatable target rows (agent +
  pinned action, max 12) under Will; Preview→Apply via `wireDivineForm`; cancel
  parent id clears all linked gates. **Dream broadcast** (`dream_broadcast`):
  shared duration + dream snapshot JSON + multi-select target agents (max 12)
  under Distortion; private dream text never in `/state`; cancel parent clears
  all linked dream masks.
- **Miracles** — Phase 4 trio (`agent_vitals`, `grant_resource`,
  `structure_condition`) plus town-integrity kinds (`repair_structures`,
  `clear_ruins` — [02-engine-core.md](02-engine-core.md)); all labeled
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
- **History** — intervention log with filter/search, re-run, soft undo, and
  narrative export (Divine Console improvements, Phase 6). Default source is
  `state.god.recentPublicInterventions` (newest first, up to 50 displayed
  after filter). When effectively authorized and `godLastSight.recentInterventions`
  exists, an **Include private (Sight)** toggle merges the fuller Sight ring
  (deduped by id). Controls: kind substring/select, agent id/name substring,
  **Public only** toggle, frame from/to. `#godHistoryList` re-renders from the
  filtered list; every dynamic string uses `escapeHtml()`.
  - **Re-run** — per-row **Re-run** rebuilds the owning form from typed keys
    present on the History/Sight record only (never `normalizedCommand` into
    DOM). Opens the correct feature modal, fills fields, scrolls to the
    fieldset, invalidates any cached preview — operator must **Preview** again
    (never auto-apply). Kinds without enough stored payload disable Re-run or
    show a short reason (e.g. whisper per-agent text, law modifier values,
    matrix compulsion pinned action).
  - **Soft undo** — `#divinePinRow` adds **Revoke last cancellable** when the
    last applied intervention id (or the newest still-active cancellable entry
    from Sight `recentInterventions`) matches a kind `POST /control/god/cancel`
    accepts and is likely still active (providence slot, `activeEvents`, zone
    summaries, or `expiresFrame` vs `world.frameTick`). Irreversible kinds hide
    the control. Reuses `godCancelEffect()` / Laws cancel wiring.
  - **Narrative export** — **Export Markdown** downloads the currently filtered
    list (kind, frame, id, public flag, short text/title fields) for demos.
  Private-only entries never appear without Sight authorization + toggle.
- **Miracles / Story / Laws QoL (Phase 7).**
  - **Law conflict warnings.** Successful previews of modifier-bearing
    `story_event` commands (Story tab and Laws tab) may return additive
    `warnings: string[]` from the engine. `#divinePreviewStrip` and the form
    result panel render each warning through `escapeHtml()` in
    `.divine-warning` styling; Apply stays enabled.
  - **Story recipes.** `#godStoryRecipeSelect` + **Apply recipe to form**
    (`#godStoryRecipeApplyBtn`) expand a named client-side bundle (Festival,
    Famine week, Plague scare, Harsh winter, Bountiful seas) into Story title,
    narration, duration (seconds), and modifier checkboxes/values only — does
    not call Preview or Apply automatically; operator must Preview again.
- **Compile** (Optional Phase 8, dual-gated, **supported experimental when
  enabled**) — `#godCompileTabBtn` stays `display:none` until
  `applyGodCapabilitiesToForms()` sees `capabilities.compiler.enabled ===
  true` (the AND of `GOD_MODE_ENABLED` and `GOD_COMPILER_ENABLED` /
  `SIM_GOD_COMPILER=1` on the server — see
  [04-http-api.md](04-http-api.md#optional-phase-8-controlgodcompile) and
  [12-ops.md](12-ops.md#optional-phase-8-free-prose-story-compiler)); when
  visible it uses the same solid `.gbtn` chrome as other bar buttons (no
  dashed “dark/experimental” border). Help text: experimental — enable with
  `SIM_GOD_COMPILER=1`; contention A/B measurement is documented in
  [12-ops.md](12-ops.md#optional-phase-8-free-prose-story-compiler) and is
  **not** claimed green until run. The same capability check bounds the prose
  textarea's `maxLength` to `capabilities.compiler.promptMaxChars` and stores
  `capabilities.compiler.minIntervalSec` for the client-side rate-limit UX
  below. The feature window holds one textarea and a single Compile button —
  **no Apply button of its own, on purpose**. Clicking Compile posts `{prose}` to
  `POST /control/god/compile`; on `compileOk: true` the server returns the
  same preview record shape as `POST /control/god/preview` (`previewId`,
  `normalizedCommand`, `reversibilityClass`, `previewOutcome`, …). The
  client calls `godPopulateStoryFromCompiled(normalizedCommand, {
  skipPreviewInvalidate: true })` — filling Story title/narration/visibility/
  target/duration/modifier-checkboxes/primitive rows/providence from the
  draft without clearing an in-flight preview — then
  `godDivineFormControllers["#godStoryFieldset"].acceptServerPreview(...)`
  wires the compile preview into `#godStoryResult`, enables Story Apply, and
  shows `#divinePreviewStrip`; finally `openDivineModal("story")`. The
  operator may edit fields (which invalidates the handoff per the standard
  `wireDivineForm` contract) or Apply directly from the sticky strip. On
  rejection or a network error the reason is written via `.textContent` only
  (never `innerHTML`) into `#godCompileResult`. After every click (success,
  rejection, or error alike) the Compile button disables itself client-side
  for `minIntervalSec` seconds — advisory only;
  `GOD_COMPILER_MIN_INTERVAL_SEC` on the server is authoritative.

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
Presentation class: `divine-banner-soft` (default) or `divine-banner-thunder`
from the intervention's optional `presentation` field. There is no full-screen
flash, no forced camera movement, and no banner (or any other public surface)
for a private omen or private story event.

## Active viewer work

Atmosphere pack (Phase 4) shipped lighting v2, seasonal terrain v2, weather
particles v2, God console chrome v2, and the calendar retune — see
[docs/archive/plan-visual-atmosphere-systems.md](../docs/archive/plan-visual-atmosphere-systems.md).
Earlier plan docs remain as design records:

- [docs/archive/plan-visual-1-day-night-lighting.md](../docs/archive/plan-visual-1-day-night-lighting.md)
  — **DONE** (atmosphere pack lighting; permanent default in viewer).
- [docs/archive/plan-visual-2-seasonal-terrain-grading.md](../docs/archive/plan-visual-2-seasonal-terrain-grading.md)
  — **DONE** (atmosphere pack seasonal terrain; permanent default in viewer).
- [docs/archive/plan-visual-3-seasonal-sprite-variants.md](../docs/archive/plan-visual-3-seasonal-sprite-variants.md)
  — **DONE** (plumbing + art passes both shipped and verified; kept for the
  design record). The `setSpriteSeason`/`TREE_GRIDS`/winter-snow-cap
  behavior documented above is this plan's shipped result.
