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
Console, World Wiki; split into 19 ordered files — see "viewer/*.js: split viewer client
script" below), `simulation/sprites/*.js` (stateless Canvas helpers, split
into 8 ordered files — see "sprites/*.js: pure stateless drawing" below).
**See also:** [01-architecture.md](01-architecture.md) for the
server-authoritative topology this file implements the "thin viewer" half of;
[04-http-api.md](04-http-api.md) for `/state`/`/districts.js` payload shapes;
[07-actions.md](07-actions.md) for the action catalog `ACTION_LABELS` merely
labels.

## Thin-viewer contract

`simulation/viewer/setup.js` (first of the 19 split viewer files, see
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
    era/level/structures/active builds/resources), then `#sidebarLogsScroll`
    (shared overflow region for the log panels below). Civilization scrolls
    on its own with a capped height (`flex: 0 1 auto; max-height:
    min(180px, 22vh); overflow-y: auto` in `css/panels.css`). Inside
    `#sidebarLogsScroll` (`flex: 1 1 0; min-height: 0; overflow-y: auto` —
    same file), in order: **Activity** (`#activityLog`, world-event feed,
    `#actList`), **Decision audit** (`#decisionAuditPanel` — see
    [Decision audit panel](#decision-audit-panel)), **Chronicle**
    (`#chronicleLog`, `#chronicleList`). Nested list `max-height` rules in
    `css/council.css` apply to `#actList` / `#chronicleList` / decision-audit
    sub-lists; the outer `#sidebarLogsScroll` wheel target prevents nested
    overflow from trapping scroll. The Civilization panel's **Village resources**
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
    key. **Chronicle** is a curated projection of top-level `world.chronicle`,
    distinct from the raw Activity feed; it preserves scroll position across
    snapshot updates and is hidden cleanly when `CHRONICLE_ENABLED` is off.
    **Village paper** (`#sagaLog`, `#sagaList`) is a pure renderer for top-level
    `world.saga` (daily LLM narrative entries `{text, frame, dayIndex}` from the
    engine's saga ring — not folded into chronicle or founding/disaster banners).
    It renders newest-first, preserves scroll position across snapshot updates
    (`lastSagaKey` change detection in `viewer/sidebar.js`), escapes dynamic
    text, hides when `CHRONICLE_SAGA_ENABLED` is off, and uses a muted placeholder
    when empty. Delta merge in `viewer/state.js` includes `"saga"` alongside
    `"chronicle"` so incremental polls retain the array.
    **Decision audit** polls `GET /decision-audit` on its own cadence (see
    [Decision audit panel](#decision-audit-panel)); when the route returns
    `enabled: false` the section stays visible with empty-state copy rather
    than force-hiding.
- **`ACTION_LABELS`** (`viewer/sidebar.js`) maps each `DECISION_ACTIONS`
  name to a short display gerund (e.g. `collect_resource` → "gathering");
  `humanizeAction(agent)` (`viewer/sidebar.js`) special-cases
  dead/incapacitated/thinking agents and falls back to
  `a.replace(/_/g, " ")` for any action missing from the map. Display-only —
  not the source of truth for what actions exist (see
  [07-actions.md](07-actions.md)); per the action-sync invariant in
  [01-architecture.md](01-architecture.md), a new action should get an entry
  but nothing breaks if briefly missing.

## Decision audit panel

Dedicated observability panel for the idea-10 "Why did you do that?" audit —
surfaces per-agent self-model mismatch aggregates from the server-side reader,
**not** computed in the browser.

**Data source.** Polls `GET /decision-audit` **without** `view=full` on its own
cadence (`DECISION_AUDIT_POLL_MS`, viewer constant — slower than `/state`,
similar to `/districts.js` / council-llm-log cadence). The panel is a **pure
renderer** of the default route payload: no client-side join, category
classification, or scoring. Outcome axis and full entry list are **not** shown
here — see [Divine Audit tab](#divine-audit-tab).

**Visibility.** Driven by the route's `enabled` field — **not** `/state`
`config.flags` (the flag is not echoed there). When `enabled: false`, the panel
**stays visible** and shows empty-state copy ("Decision audit disabled") in
`#decisionAuditAgentList`; it is **not** force-hidden (force-hide reads as empty
black space). The recent `<details>` wrap is hidden and polling stops. When
`enabled: true`, the panel renders aggregates as below. Full entry list and
outcome axis remain on the [Divine Audit tab](#divine-audit-tab) only.

**Placement.** Right sidebar (`#sidebar` / `#sidebarBody`), inside
`#sidebarLogsScroll` with Activity and Chronicle — **not** below Chronicle.
Order: Activity (`#activityLog`) → **Decision audit** (`#decisionAuditPanel`)
→ Chronicle (`#chronicleLog`). `#sidebarLogsScroll` is the shared overflow-y
region (`flex: 1 1 0; min-height: 0; overflow-y: auto` in `css/panels.css`) so
nested list overflow cannot trap the wheel. `#decisionAuditPanel` is
`flex-shrink: 0` with a larger min-height (`min-height: min(360px, 42vh)` in
`css/panels.css`). Element ids: `#decisionAuditPanel` container,
`#decisionAuditAgentList` for per-agent rows, `#decisionAuditRecent` for the
bounded `recent[]` drill-down list (wrapped in `#decisionAuditRecentWrap`
`<details>`).

**Implementation.** `viewer/decision-audit.js` (loaded after `sidebar.js` for
`escapeHtml`): `DECISION_AUDIT_POLL_MS = 3000`, `pollDecisionAudit()` fetches
`GET /decision-audit`, `renderDecisionAuditPanel()` paints agent rows and the
optional recent list with scroll preservation via a change-detect key (same
pattern as Chronicle). Bootstrap kickoff — `pollDecisionAudit()` plus
`setInterval(pollDecisionAudit, DECISION_AUDIT_POLL_MS)` — runs at **module
load** in `decision-audit.js` itself; **not** in `divine-history.js`. Markup in
`index.html`; layout rules in `css/panels.css` (`#sidebarLogsScroll`,
`#decisionAuditPanel`); list/badge rules in `css/council.css` (badges reuse
divine semantic green/red).

**Per-agent row.** One row per `agents[]` entry: agent name, `scored` count,
`matches` / `mismatches`, and `mismatch_rate` as a percentage. Rows follow
server rank order (worst mismatch rate first). Agents with `scored == 0` may be
omitted or shown in a muted "no scored decisions yet" state — implementation
choice; the spec requires at least the ranked `scored >= 1` agents.

**Recent drill-down (optional sub-list).** When present, renders `recent[]`
entries: `frame_tick`, `action`, `reasoning_category`, `score`
(match/mismatch badge), truncated `activity_message`. Newest first; preserves
scroll position across polls when the list content is unchanged (same
change-detect key pattern as Chronicle).

**Styling.** Lives in `css/council.css` alongside Activity/Chronicle list rules;
reuses existing `.panel-section` header/body chrome. Match/mismatch badges use
existing semantic colors (`.decision-audit-badge-match` /
`.decision-audit-badge-mismatch`, same green/red family as divine badges).

**Out of scope for the viewer.** Correlation-id minting, log joins, fallback
filtering, category keyword matching, and outcome classification all stay
server-side ([12-ops.md](12-ops.md#decision-audit--log-reading-pattern-and-scoring-semantics),
[04-http-api.md](04-http-api.md#decision-audit-route)).

## Divine Audit tab

Read-only Divine Console feature for the idea-10 full decision audit — every
`llm.jsonl` decision in the session with both scoring axes (intent +
outcome), filters, and per-entry reasoning vs action vs activity line. **Does
not** replace or change the sidebar [Decision audit panel](#decision-audit-panel).

**Data source.** Fetches `GET /decision-audit?view=full` on its own cadence
only while the Audit tab is open (`renderGodDecisionAudit()` /
`pollGodDecisionAudit()` in `viewer/decision-audit.js` — same module as the
sidebar panel, separate poll state). Closing the tab stops full-view polling.
The client filters `entries[]` locally (agent, intent, outcome) but performs
**no** join, category classification, intent scoring, or outcome scoring.

**Visibility / gate.** Same unlock gate as History: `DIVINE_FEATURES.audit`
registers `gated: true`; the bar button carries `.locked-dependent` and stays
disabled until `godEffectivelyAuthorized()` (Unlock when
`GOD_AUTH_REQUIRED`, or immediately when auth is off). No Preview/Apply —
read-only observability only. When the route returns `enabled: false`, the tab
may still open but renders an empty state (no log I/O server-side).

**Bar placement.** Button label **Audit**, in `#divineBar` **after History /
before Compile** (`#godAuditTabBtn`). Panel `#divineTab-audit` follows the
existing reparent pattern (`#divineTabHold` at load → `#divineModalBody` on
`openDivineModal("audit")`).

**Registry wiring** (`viewer/divine-bootstrap.js`, `viewer/divine-modal.js`):

- `DIVINE_FEATURES.audit` — title, subtitle, icon, `gated: true`.
- `"audit"` in `GOD_TABS` (9th feature tab after History, before Compile when
  compiler visible).
- `"audit"` in `DIVINE_WIDE_MODAL_FEATURES` — modal gets the `wide` class
  (`min(960px, 96vw)`), same family as matrix/story/laws/compile.

**Panel layout** (`#divineTab-audit`, markup in `index.html`; styles in
`css/divine.css`):

- **Filters** — agent name/id substring or select, intent bucket
  (`match`/`mismatch`/`unclassified`/`uncorrelated`/`fallback`), outcome
  (`ok`/`fail`/`unknown`). Client-side filter over server `entries[]` only.
- **Two-axis legend** — short callout that **Intent** (reasoning category vs
  `action`) and **Outcome** (activity summary heuristic) are independent;
  `ok` on outcome means the engine wrote an activity summary, not that the
  action succeeded (e.g. `heads to gather…` is `ok`).
- **Agent summary table** — per `agents[]` row: intent aggregates (`scored`,
  `matches`, `mismatches`, `mismatch_rate`) plus `outcome_ok` /
  `outcome_fail` / `outcome_unknown` from the full view.
- **Scrollable entry list** — each filtered `entries[]` row shows reasoning,
  `action`, correlated `activity_message` (when present), intent badge
  (match/mismatch or bucket label), and outcome badge (ok/fail/unknown).
  Newest first (server order); scroll preservation via change-detect key
  (same pattern as History/Chronicle).

**Implementation notes.** Rendering lives in existing
`viewer/decision-audit.js` (no new viewer JS file). `divine-modal.js`
`openDivineModal("audit")` triggers initial fetch/render. Sidebar
`pollDecisionAudit()` continues to call `/decision-audit` without
`view=full`.

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

## Anomaly panel (`ANOMALY_RADAR_ENABLED`)

Two surfaces over one route (docs/plans/idea-07-anomaly-radar/plan.md §2
Answer 6; expanded to two surfaces + richer rendering + expanded server-side
detection by docs/plans/idea-07b-anomaly-console/plan.md §2 Answers 1-4):
a compact, always-available sidebar panel, and a fuller divine-bar console
view. Both read the same read-only anomaly radar route (`GET /anomalies`,
see [04-http-api.md](04-http-api.md#anomaly-radar-idea-07)) and both are pure
renderers — neither performs threshold comparison, first-seen tracking, or
any other detection logic client-side; that would violate the pure-renderer
contract (no simulation/detection logic in the browser). Each entry the
route returns carries **timestamp** (engine frame tick — also the "jump-to
frame" reference, see [04-http-api.md](04-http-api.md#anomaly-radar-idea-07)),
**metric** (one of the 12 `RANGE_BREAK_METRICS` allowlist names for
`range_break`, `rule_kind_diversity` for `new_rule_kind`, or `schism` for
`schism`), **kind**, **value**, optional **detail**, and **severity**
(`"high"` / `"medium"` / `"low"` — magnitude-scaled against the prior
session-lifetime bound for `range_break`, fixed for `schism` (`"high"`) and
`new_rule_kind` (`"low"`) — see
[04-http-api.md](04-http-api.md#anomaly-radar-idea-07) for the full shape,
thresholds, and rationale).

**Own poll cadence, not `/state`.** This feature adds no `/state` key (idea-07
Answers 1, 5) — both surfaces poll `GET /anomalies` on their own schedule,
independent of `pollState()`'s `STATE_POLL_MS` cadence and independent of
`pollDistricts()`'s `DISTRICTS_POLL_MS` cadence (see "Polling and render
loop" above). Neither blocks or gates on the `/state` poll succeeding.

**Implementation — sidebar panel.** `viewer/anomaly.js` (loaded after
`viewer/polling.js` in `index.html`) owns the sidebar panel: it fires
`pollAnomalies()` immediately on load, then every `ANOMALY_POLL_MS` (5000ms —
slower than `STATE_POLL_MS`'s 100ms, since anomalies are rare events, and
independent of `DISTRICTS_POLL_MS`'s 3000ms). The panel markup converts from
a plain `<section>` to the same collapsible pattern
`#settlementsPanel`/`#councilPanel` already use in `index.html`
(idea-07b §2 Answer 3):

```html
<section class="panel-section" id="anomalySection" style="display:none">
  <details id="anomalyPanel" open>
    <summary>Anomaly radar</summary>
    <ul id="anomalyList"></ul>
  </details>
</section>
```

The outer `<section id="anomalySection">` (not the inner `<details>`) is what
`renderAnomalies()` hides/shows via inline `style.display` for the
flag-off/flag-on switch described below — mirroring `#settlementsSection`
wrapping `#settlementsPanel` and `#councilSection` wrapping `#councilPanel`,
so this feature does not fight `<details>`'s own `open`/closed state with a
second, competing `display:none`. `<summary>Anomaly radar</summary>` is the
clickable expand/collapse header; clicking it toggles native `<details>`
open/closed state — no custom toggle JS, matching the existing pattern
exactly. `#anomalyList` is styled in `css/council.css` alongside
`#chronicleList`'s rules. Each row renders `kind` (human label: "Range
break" / "New rule kind" / "Schism"), `metric`, `value`, `severity`, and
`timestamp` as `frame <n>` — same "newest first" ordering as
`#chronicleList`. A `JSON.stringify(anomalies)` key-diff (same technique
`#chronicleList` uses for `world.chronicle`) skips re-render when the list is
unchanged between polls.

**Implementation — divine-bar button + modal (idea-07b §2 Answer 1, §4).** A
new `.gbtn` in `#divineBar`'s `.bar-buttons` (`simulation/index.html`),
`data-feature="anomaly"` `data-tab="anomaly"`, following the markup shape of
the other `.gbtn` elements (icon svg, `<span class="lbl">`, `data-tip`) —
**with two deliberate omissions that are this feature's security boundary**:

- It does **not** carry the `locked-dependent` class and is **not**
  `disabled`. Every other `.gbtn` except `unlock` (`sight`, `voice`,
  `matrix`, `miracles`, `story`, `laws`, `history`, `compile`) carries both,
  and `divine-auth-sight.js`'s `updateDivineBarAuthUi()` re-enables them via
  `divineBarEl.querySelectorAll(".gbtn.locked-dependent").forEach((btn) => {
  btn.disabled = !effective; })` (`divine-auth-sight.js:861`) only once
  `godEffectivelyAuthorized()` is true. Omitting `locked-dependent` means this
  selector never touches the Anomaly button — it is clickable immediately,
  without connecting a god token, mirroring only the `unlock` button's
  always-enabled precedent.
- Its `DIVINE_FEATURES.anomaly` entry (`viewer/divine-bootstrap.js`, added
  alongside the existing `unlock`/`sight`/`voice`/`matrix`/`miracles`/
  `story`/`laws`/`history`/`compile` entries) sets `gated: false` — the same
  shape as the `unlock` entry (`title`, `sub`, `guide`, `gated`). `unlock` is
  currently the sole `gated: false` precedent in this registry; `anomaly` is
  the second.

**Still hidden when god mode is off.** Both omissions above only affect
behavior once `#divineBar` itself is visible. `#divineBar` ships
`style="display:none"` and is revealed only when `GOD_MODE_ENABLED_FLAG`
(mirroring `/state`'s `config.flags.GOD_MODE_ENABLED`) is true — this is
unchanged by idea-07b and applies to the Anomaly button like every other
`.gbtn`. This is accepted as correct (idea-07b §2 Answer 1) precisely
because the sidebar panel above is the always-available surface regardless
of god-mode state; the divine-bar button is an additional, richer view for
operators who already have the bar open, not the feature's only surface.

**Not a god intervention.** The Anomaly view fetches via plain
`fetch("/anomalies")`, exactly like `viewer/anomaly.js`'s existing
`pollAnomalies()` — **never** `godApiFetch()` (`viewer/divine-auth-sight.js`),
which attaches the `X-God-Token` header and is reserved for
`/control/god/*` routes. The Anomaly view never calls any `/control/god/*`
route, and using it writes nothing to `divine.jsonl` (see
[12-ops.md](12-ops.md#anomaly-radar-log-reading-anomaly_radar_enabled-idea-07)).
It is read-only observability borrowing the bar's chrome, not a divine
intervention.

**`#divineTab-anomaly` panel.** A new `.divine-tab-panel` inside
`#divineTabHold` (`simulation/index.html`), alongside the existing 8
`#divineTab-<name>` panels (`unlock`, `sight`, `voice`, `matrix`, `miracles`,
`story`, `laws`, `history`, plus the optional `compile`), wired through the
same `openDivineModal`/`showGodTab` tab-switch machinery
(`viewer/divine-modal.js`) every other tab uses — `data-tab="anomaly"` on the
new `.gbtn` routes clicks there. Unlike the other 8 tabs, its render path
never touches `godCapabilities`/`godToken`/`godAuthorized`; it is a
self-contained poll+render pair independent of the auth state machine.
Inside `#divineTab-anomaly`, anomalies are grouped by `kind` into three
collapsible `<details>` sections (idea-07b §2 Answers 2-3), reusing the same
native `<details>/<summary>` pattern as the sidebar panel and
`#settlementsPanel`/`#councilPanel` — no custom toggle JS:

```html
<details class="divine-anomaly-group" open>
  <summary>Range break (<span class="anomaly-group-count"></span>)</summary>
  <ul></ul>
</details>
<!-- one such <details> per kind: range_break, new_rule_kind, schism -->
```

Each kind's `<summary>` shows a per-kind count (idea-07b §2 Answer 2,
"grouping by kind, per-kind counts") computed client-side by filtering the
same flat `anomalies` array the sidebar panel already receives — grouping
already-returned data by an existing field (`kind`) is not detection logic,
so no server-side payload change is needed for this (confirmed in
[04-http-api.md](04-http-api.md#anomaly-radar-idea-07), "No per-kind
grouping field added to the response"). Each row inside a group renders the
same fields as the sidebar row plus the previously-unused `detail` field
(idea-07b §2 Answer 2), rendered as a small key/value list when present
(e.g. `direction: max` for `range_break`, or `parent`/`child`/`belief`/`rule`
for `schism`) — every dynamic string routed through `escapeHtml()`, same
discipline as every other Divine Console render path.

**Flag gating is different from every other flagged panel in this file, and
now spans two surfaces.** Every other flag-gated element above (e.g. the
Founding banner) reads its gate from `world.config.flags.<FLAG>` inside
`applyFlags`, because that flag is echoed in `/state`. `ANOMALY_RADAR_ENABLED`
is **not** echoed in `config.flags` (see
[01-architecture.md](01-architecture.md)'s flag index) — per idea-07 plan §6,
its on/off state is carried by `GET /anomalies`'s own `enabled` field
instead, and idea-07b §6 confirms this is still the single flag that must
gate **both** surfaces (no second flag is added for the divine-bar button).
Concretely:

- `renderAnomalies()` in `viewer/anomaly.js` sets `#anomalySection`'s inline
  `style.display` to `"none"` when `data.enabled` is falsy and to `""`
  otherwise; the section starts hidden (`style="display:none"` in
  `index.html`) until the first successful poll confirms the flag is on, so
  a slow first response never briefly shows a stale/empty list as if the
  feature were on.
- The divine-bar Anomaly `.gbtn` reads the same `enabled` field from its own
  poll (or the sidebar's already-fetched response, to avoid a duplicate
  request on every page load) and sets its own `style.display` to `"none"`
  when falsy — independent of `GOD_MODE_ENABLED_FLAG`'s show/hide of
  `#divineBar` as a whole. Both conditions apply: the button is visible only
  when god mode is on **and** `ANOMALY_RADAR_ENABLED` reports `enabled: true`.

**No client-side detection logic.** Neither surface performs threshold
comparison or first-seen tracking; both only render the `anomalies` array
(and, for the modal, `detail`/`severity`, plus client-side grouping by the
existing `kind` field) the route already computed — same discipline as the
Chronicle panel reading its `world.chronicle` array.

**Implementation notes (Phase 3, concrete details).** Both surfaces are
implemented in the single existing `viewer/anomaly.js` (no new viewer file,
so `_VIEWER_FILES` in `simulation/server.py` is unchanged) and both are
driven by one `pollAnomalies()`/`renderAnomalies()` pair — there is no
separate modal-only poll loop; `#divineAnomalyGroups` (see below) is kept
up to date on every 5s tick regardless of whether the modal is currently
open, since the tab panel simply sits hidden in `#divineTabHold` until
opened.

- `#divineAnomalyGroups` — the container `<div>` inside
  `#divineTab-anomaly` that `renderAnomalyModal()` fills with one
  `.divine-anomaly-group` `<details>` per kind, in the fixed order
  `range_break`, `new_rule_kind`, `schism` (`ANOMALY_KIND_ORDER` in
  `viewer/anomaly.js`) — always all three, even when a kind currently has
  zero entries (rendered as `<li class="civ-label">None detected yet</li>`),
  so the section list itself never reflows as new kinds first appear.
- The divine-bar Anomaly `.gbtn` carries no `id` (matching every other
  `.gbtn` except the ones with a `data-tip` status pip); `viewer/anomaly.js`
  selects it via `document.querySelector('.gbtn[data-feature="anomaly"]')`.
  It ships `style="display:none"` in `simulation/index.html` and is
  revealed only by `updateAnomalyBarButton()` once a poll confirms
  `enabled: true` — mirroring `#anomalySection`'s own hidden-until-confirmed
  start state.
- `viewer/divine-modal.js`'s `GOD_TABS` array (used by `openDivineModal()`
  to validate/reset the requested tab name and to hide sibling panels) gains
  `"anomaly"` as a 10th entry. This is a required, minimal edit to a file
  outside this feature's normal ownership (`viewer/divine-modal.js` is not
  listed as in-scope for the anomaly console) — without it,
  `openDivineModal("anomaly")` would silently reset to `"unlock"` and the
  new button would never open its tab. The anomaly tab still bypasses every
  other piece of that file's auth-dependent machinery (no
  `godCapabilities`/`godToken`/`godAuthorized` checks in its render path).
- Severity renders as an uppercase badge, `<span class="anomaly-severity
  anomaly-severity-{high|medium|low}">` — shared CSS class names between
  both surfaces (`css/council.css` for `.anomaly-severity*`/
  `.anomaly-metric`/`.anomaly-value`/`.anomaly-frame`; `css/divine.css` for
  `.divine-anomaly-group`/`.anomaly-detail-row`/`.anomaly-detail-kv`
  covering the modal's per-kind `<details>` chrome and the `detail`
  key/value rendering). The sidebar row and each modal row use the same
  class names so the two surfaces stay visually consistent without
  duplicating rules.
- `#anomalyPanel summary` and `.divine-anomaly-group summary` follow the
  same clickable-header styling convention as `#councilPanel summary`
  (`css/council.css`) and `.divine-preview-panel summary` (`css/divine.css`)
  respectively — no new interaction pattern, only new selectors.

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
`council_propose`, and `council_vote`, and for `offer_contract` /
`accept_contract` when contracts are enabled in the world.

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
| 4 | `css/council.css` | Council transcript modal, `#worldLoading`, conversation/activity/chronicle/saga/decision-audit lists (`#convList`/`#actList`/`#chronicleList`/`#sagaList`/`#decisionAuditAgentList`/`#decisionAuditRecent`), council banner/panel (`#councilBanner`, `.council-card`, `#councilHistory`), the Daily Council Assembly modal (`#councilAssemblyModal` and its canvas/tally/transcript rules) |
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
into 19 plain files (18 original + `world-wiki.js` added in idea-09 Phase 3),
loaded via ordered `<script>` tags in `index.html`
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
| 1 | `viewer/setup.js` | Thin-viewer contract banner, canvas/DPR setup, offscreen-terrain-cache scaffolding, zoom (`zoomLevel`/`applyZoom`/`zoomFit`), all feature flags (`SURVIVAL_ENABLED`, `DYNASTY_TREE_ENABLED`, etc.), viewer-only display toggles (`SHOW_CONVERSATIONS`/`SHOW_SETTLEMENTS`) |
| 2 | `viewer/state.js` | World snapshot: `MOCK_STATE`, module-level `world`, `mergeStateDelta()`, districts cache (`districtsData`/`districtsKey`/`districtsEpoch`), `getDistricts`/`findDistrictBounds`/`getDistrictBounds` |
| 3 | `viewer/render.js` | Convenience accessors (`getAgents`/`getCiv`/etc.) plus drawing: terrain cache build (`buildTerrainCache`), season/night/golden-hour/weather overlays, `drawWorld`, per-agent/structure drawing (`drawAgent`, `drawStructureWithShadow`, hit-flash) |
| 4 | `viewer/sidebar.js` | Sidebar render: `renderSidebar()`, `ACTION_LABELS`/`humanizeAction`, benchmarks, agent detail/rollup/panel, deceased-agents modal, founding/disaster banners, `renderWorldClockHud` |
| 5 | `viewer/decision-audit.js` | Decision audit: sidebar panel (`pollDecisionAudit()`/`renderDecisionAuditPanel()` on default `GET /decision-audit`; poll bootstraps at module load here) + Divine Audit tab (`pollGodDecisionAudit()`/`renderGodDecisionAudit()` on `?view=full` when tab open); pure renderer |
| 6 | `viewer/council.js` | Council panel (`renderCouncil`), council transcript modal (`openCouncilTranscript`), Daily Council Assembly modal, settlements |
| 7 | `viewer/minimap.js` | Minimap render (`renderMinimap`) and click/drag-to-navigate |
| 8 | `viewer/polling.js` | `/state` polling (`pollState`), `applyFlags`, social-tie/wildlife/shipment drawing (`drawSocialTies`, `drawWildlife`, `drawShipments`) |
| 9 | `viewer/anomaly.js` | Anomaly Radar sidebar panel and Divine Console anomaly tab: `pollAnomalies()` / `renderAnomalies()` plus modal rendering; read-only `GET /anomalies` polling |
| 10 | `viewer/controls.js` | Pause/Resume/Reset controls (`postControl`, `syncPauseButton`, `doReset`), reset keyboard shortcut |
| 11 | `viewer/renderloop.js` | Render loop (`tick`/`tickBody`), decoupled from polling |
| 12 | `viewer/divine-bootstrap.js` | Divine Console (Sovereign God mode Phase 7) state vars, DOM element refs, `DIVINE_FEATURES` registry, feature guide, agent/pin action select population |
| 13 | `viewer/divine-auth-sight.js` | Divine Console auth/fetch plumbing (`godApiFetch`), Sight intervene helpers/diff, bottom-bar effects/pips/pulse, sight overlay drawing, preview controller + irreversible-form helpers, favorites |
| 14 | `viewer/divine-modal.js` | Divine Console bottom bar/modal/tab wiring (`openDivineModal`/`showGodTab`), shared tooltip engine, generic preview→apply wiring (`wireDivineForm`), preview/outcome/error render helpers |
| 15 | `viewer/divine-sight-voice.js` | Divine Console Sight tab render (`renderGodSight`) + checkpoint restore, Voice presets (load/save/apply) |
| 16 | `viewer/divine-voice.js` | Divine Console Voice tab: proclamation/providence/private omen, whisper campaign, sampling/distortion, crowd compulsion, dream broadcast, veto resolve, bargain predicate, oracle hints, architect cells |
| 17 | `viewer/divine-miracles-story.js` | Divine Console Miracles tab (`agent_vitals`/`grant_resource`/`structure_condition`), shared Story/Laws modifier editor, story primitives editor, Story/Compile/Laws tabs |
| 18 | `viewer/divine-history.js` | Divine Console History power tools, gate + passive per-poll refresh, public banner, `renderDivineConsole()` entry point, and the page's bootstrap kickoff (`requestAnimationFrame(tick)`, `pollState()`, `pollDistricts()`). Decision-audit polling starts in `decision-audit.js` itself. |
| 19 | `viewer/world-wiki.js` | World Wiki modal: `openWorldWiki(kind, id)` global entry point, `fetchWiki()` / `renderWikiModal()` / `renderWikiIndex()` / `renderWikiPageContent()`, back-navigation stack, delegated `.wiki-link` click handler (capture phase, stops propagation). Gated on `WORLD_WIKI_ENABLED_FLAG` (set in `polling.js`). Polls `GET /wiki` every 3 s while modal is open; no polls when closed. Pure renderer — no world-state mutation. |
| 20 | `viewer/divine-modal.js` | Divine Console Lineage tab render (`renderGodLineage`/`applyDynastyTreeLineageGate`) and modal-tab wiring; read-only family tree from `/state`. |

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
- **Density (engine):** spawn caps key off per-district wildlife stage
  (`WILDLIFE_STAGE_COUNT = {barren: 0, sparse: 1, healthy: 2, lush: 4}`,
  capped at `WILDLIFE_CAP_PER_DISTRICT = 4`). Forest and farm use the same
  averaged stock ratio as `world.districtEcology`; **beach fauna density
  follows fish stock only** (see [02-engine-core.md](02-engine-core.md)) —
  depleted fish reduces fish/crab/gull/turtle/seal counts even when
  `districtEcology` still looks healthy from clay/sand. Only
  forest/farm/beach district kinds host fauna pools.
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
not a sidebar panel. Twelve feature buttons (**Unlock**, **Sight**, **Voice**,
**Matrix**, **Miracles**, **Story**, **Laws**, **History**, **Audit**, **Anomaly**,
**Lineage**, plus **Compile** when the server reports the Optional Phase 8 compiler enabled — see below) live in
`#divineBar` (`position: fixed; bottom: 0; left: 0; right: 0`). Clicking a
button opens `#divineModalScrim` / `#divineModal` (`role="dialog"`,
`aria-modal="true"`), whose body is `#divineModalBody`. At load time the nine
`#divineTab-<name>` panel nodes (including `#divineTab-audit` and `#divineTab-lineage`) sit in a hidden holding container
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
`renderGodLawsActive()`; History → `renderGodHistory()`; Lineage →
`renderGodLineage()`.
The Compile bar button `#godCompileTabBtn` stays dual-gated via
`capabilities.compiler.enabled` (see Compile below).

**Modal width (Divine Console improvements, Phase 1).** Default `#divineModal`
width is `min(680px, 96vw)`. `openDivineModal(name)` toggles a `wide` class on
`#divineModal` for **matrix**, **story**, **laws**, **compile**, and **audit**
(`DIVINE_WIDE_MODAL_FEATURES`); `closeDivineModal()` removes it. Wide modals use
`min(960px, 96vw)`. All other features keep the default width. Presentation-only
— no route or engine changes.

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
- **Audit tab open.** `openDivineModal("audit")` calls `renderGodDecisionAudit()`
  (initial fetch of `GET /decision-audit?view=full`); see
  [Divine Audit tab](#divine-audit-tab).
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
  Story, Audit, and Compile stay clean unless a future phase adds signal.
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
- **Audit** — read-only decision audit over `GET /decision-audit?view=full`;
  gated like History (`gated: true`, `.locked-dependent`). Filters, agent
  summary table, two-axis legend, and scrollable entry list — see
  [Divine Audit tab](#divine-audit-tab). No Preview/Apply.
- **Lineage** (`DYNASTY_TREE_ENABLED`) — read-only family tree panel in the
  Divine Console (idea-02 dynasty tree, Phase 3). `#godLineageTabBtn`
  (`.gbtn.lineage`, `data-feature="lineage"`) sits in `#divineBar` after
  **History** / before **Compile**; opens `#divineTab-lineage` via the same
  reparent-on-open / reparent-back-on-close modal-tab pattern as History.
  Registry: `DIVINE_FEATURES.lineage` in `viewer/divine-bootstrap.js`, `"lineage"`
  in `GOD_TABS` (`viewer/divine-modal.js`). Same unlock gate as other
  `.locked-dependent` tabs (`godEffectivelyAuthorized()`). No Preview/Apply —
  pure renderer from `/state` agent fields only.
  - **Kill switch.** `DYNASTY_TREE_ENABLED` mirrors
    `config.flags.DYNASTY_TREE_ENABLED` (default `true` in
    `viewer/setup.js`; applied in `applyFlags()`). When false,
    `#godLineageTabBtn` is `display:none` (same pattern as the Compile tab
    hide in `applyGodCapabilitiesToForms()` / `applyDynastyTreeLineageGate()`);
    if Lineage is open, the modal switches to History.
  - **Agent picker.** `#godLineageAgentSelect` lists **all** agents from
    `getAgents()` / `world.agents`, including deceased (`"Name (deceased)"`).
    Does not use `populateGodAgentSelects()` (living-only).
  - **Panel fields** for the selected agent: **Parents** (`agent.parents` —
    empty/`null` → "Founding generation — no parents"; each name is a button
    that selects that agent in the same panel); **Children** (`agent.children` —
    empty → "No children"; same link navigation); **Inherited testament**
    (`agent.inheritedTestament` entries `{text, author, frame, generation}` —
    empty → "None"); **Inherited beliefs** (`agent.inheritedBeliefs` list —
    empty → "None"). All dynamic strings via `escapeHtml()`; no client-side
    simulation, no extra fetches.
  - **Refresh.** `renderGodLineage()` runs on open (`openDivineModal("lineage")`
    in `viewer/divine-modal.js`) and on every poll while Lineage is the active
    tab: `renderDivineConsole()` in `viewer/divine-history.js` calls it **before**
    the `world.god` `godLastStateKey` early return, because lineage fields live on
    `world.agents`, not `world.god`. Safe every poll — internal `contentKey`
    change detection skips DOM work when parents/children/testament/beliefs are
    unchanged. Select value is preserved across poll refreshes.
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

## World Wiki modal (`WORLD_WIKI_ENABLED`)

**Grounded in:** plan §2 Answers 5, 6.

A new **full-screen modal** (Answer 6), mirroring the existing Council transcript modal
pattern (`simulation/css/council.css`, `simulation/viewer/council.js`). The wiki modal
is the first and only viewer consumer of `GET /wiki`.

**Placement and trigger.** "Click a name anywhere, land on its page" — every existing
name/id render site in the viewer (agent list, structure list, chronicle, council
transcript, district/settlement panel, etc.) that already displays one of the twelve
in-scope entity names links into the wiki modal for that entity. The modal is not a
standalone panel; it overlays the existing viewer when triggered and closes on dismiss
(same pattern as the Council transcript modal).

**Content.** The modal displays the clicked entity's page: its fields and its
cross-links (as clickable hyperlinks that navigate to another entity's wiki page within
the same modal). Covers all twelve entity kinds: agent, structure, belief, rule,
chronicle event, district, settlement, treaty, resourceRegistry entry, projectRegistry
entry, recipe, and — on agent pages only — social ties displayed as labeled ally/rival
links.

**Pure renderer.** The wiki modal is a pure rendering surface — no client-side
simulation logic, no decisions, no world-state mutation. It reads only from the `GET
/wiki` JSON payload (Answer 3's server-side cross-link index) and the existing `world`
snapshot already in `viewer/state.js`. The thin-viewer contract is preserved.

**Live updates (Answer 5).** When the wiki modal is open, the viewer polls `GET /wiki`
every 3 s (WIKI_POLL_MS). The open page re-renders on each fresh payload. No separate
dirty-tracking; the route returns fresh data on every call. Polling stops when the
modal is closed.

**Flag echo.** The viewer reads `world.config.flags.WORLD_WIKI_ENABLED` (echoed by the
server via `_build_snapshot_config()`), stored as `WORLD_WIKI_ENABLED_FLAG` in
`viewer/polling.js`. When `false`, no wiki links are rendered and no `GET /wiki` polls
are sent.

**Viewer files (Phase 3 implementation).** The wiki modal lives in:
- `simulation/viewer/world-wiki.js` — all modal logic (file 19 in the viewer split
  table above). Entry point: global `openWorldWiki(kind, id)`.
- `simulation/css/council.css` — wiki modal CSS appended to the existing council CSS
  file (same file that owns `#councilTranscriptModal` and `#councilAssemblyModal`
  styles).
- `simulation/index.html` — modal markup `#worldWikiModal` / `#worldWikiDialog`
  added alongside `#councilTranscriptModal`.
- `simulation/viewer/polling.js` — `WORLD_WIKI_ENABLED_FLAG` variable + `applyFlags`
  entry.
- `simulation/viewer/sidebar.js` — wiki-link chips wired into: agent list rows
  (`.agent-wiki-btn` chip on each agent name), agent detail relationship chips
  (resolves peer agent by name via `getAgents()`), rule chips in civ panel, recipe
  chips in civ panel.
- `simulation/viewer/council.js` — settlement name links (settlement list) and daily
  council transcript `ct-who` spans (resolves agent by name via `getAgents()`).

**Click-through wiring.** A capture-phase delegated `click` handler on `document`
catches all `[data-wiki-kind][data-wiki-id]` elements and calls
`openWorldWiki(kind, id)`, stopping propagation so existing list-item selection
handlers are not also triggered. All wiki-linkable elements carry these two
`data-*` attributes and the `wiki-link` class (for styling).

**Agent → structure → district → settlement chain.** The wiki modal renders
reverse links: on an agent's page, `viewer/world-wiki.js` scans all structure pages
in the fetched `/wiki` payload to find those whose `homeOf` link targets this agent,
and renders them as clickable "Home structure" links. From the structure page, the
`districtId` forward link goes to a district page; from the district page, the
`settlementId` link goes to a settlement page. This satisfies the required
click-through chain without any server-side change.


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
