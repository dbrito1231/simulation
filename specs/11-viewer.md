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
- `DISTRICTS_POLL_MS = 3000` (index.html:1069) drives `pollDistricts()`
  (`GET /districts.js`) on a slower cadence since districts/roads change
  only when a district is founded server-side; rebuilds the terrain cache
  only when the served district-id list actually changed (index.html:1064-1086).
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
  `scheduleTerrainCacheBuild`, index.html:740-756), invalidated on resize, a
  season change (index.html:2173-2178), or a district-list change
  (index.html:1079-1081).
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
    scrolls independently. A third scrollable **Chronicle** panel is a curated
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
