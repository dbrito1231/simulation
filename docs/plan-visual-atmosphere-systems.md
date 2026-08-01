# Plan — Atmosphere Systems (weather FX, God chrome, calendar, flags)

**Parent:** [two_sim_breakthroughs plan](../.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md) § E — Full atmosphere pack  
**Status:** Planned — documentation only (Phase 4a).  
**Branch:** `feature/four-breakthroughs-ace`  
**Companion docs:** [plan-visual-1-day-night-lighting.md](plan-visual-1-day-night-lighting.md), [plan-visual-2-seasonal-terrain-grading.md](plan-visual-2-seasonal-terrain-grading.md)

This sibling doc covers viewer weather particles, Divine Console chrome polish, the **calendar timing system retune** (engine, Phase 4b), and the four new visual feature flags.

---

## 1 — Weather particle redesign (`WEATHER_PARTICLES_V2_ENABLED`)

### Problem

Rain/snow/storm particles (index.html ~1883+, specs/11-viewer.md § Weather) work but read **thin and uniform**:

- Global cap `260` with divisor `9500`/`14000` — sparse at mid zoom.
- Rain streaks single-weight; sheet every 9th only.
- `gathering` state draws **no** particles (`WEATHER_STATE_INTENSITY.gathering = 0`) — missed “clouds building” cue.
- District-local storm rain (140 cap, divisor 6500) can overpower global layer or feel disconnected.
- Snow = same motion as rain with dot sprite; no drift pile or wind gust variance.

**Viewer-only.** No new `WEATHER_STATES`; still driven by `world.weather.state` + `calendar.season`.

### v2 design

| Area | Current | v2 |
|---|---|---|
| Gate | `WEATHER_ENABLED` | + `WEATHER_PARTICLES_V2_ENABLED` (default on) |
| `gathering` intensity | `0` | **`0.18`** — light drizzle, lower alpha |
| Storm density divisor | `9500` | **`7200`** |
| Clearing divisor | `14000` | **`11000`** |
| Global cap | `260` | **`380`** |
| Rain streak | fixed slant x-6,y+12 | **wind from `weatherParticleHash(i)`**: angle ±25°, length 8–16 |
| Sheet streaks | every 9th | every **6th**, width 2.5, alpha +20% |
| Snow | dot | **flake cluster** (3px cross + 2px dot), slower fall, slight horizontal wobble |
| Depth layers | single | **two layers**: background (60% count, alpha ×0.5, shorter) + foreground |
| District local rain | 140 cap | **100 cap**, higher alpha, only when v2 off or `storm`/`clearing` — v2 merges local into global with bounds clip (same clip rect, fewer duplicate particles) |
| Lightning | unchanged | unchanged (still rAF buckets); v2 may bump flash alpha cap +0.02 if storm particles darken foreground — QA only |

**Flag off:** restore exact current constants and `WEATHER_STATE_INTENSITY` table.

### Files (4c)

- `simulation/index.html`: `drawWeatherParticles`, `drawDistrictStormRain`, constants block.
- `simulation/sprites.js`: `drawWeatherParticle(ctx, kind, …)` — extend `rain`/`snow` branches.
- specs/11, specs/01 (flag).

### Verification

- [ ] `gathering`: visible light rain; `storm`: dense readable streaks; `clearing`: taper.
- [ ] Winter storm: snow not confused with rain.
- [ ] Zoom in/out: count scales with viewport (existing math preserved).
- [ ] Flag off → identical to pre-4c particles.
- [ ] `MAX_NIGHT_PLUS_WEATHER_ALPHA` stack with lighting v2 still readable.

---

## 2 — God console chrome polish (`GOD_CONSOLE_CHROME_V2_ENABLED`)

### Problem

Divine Console already relocated to bottom bar ([plan-divine-console-bottom-bar.md](plan-divine-console-bottom-bar.md), `#divineBar` in index.html). Functional but **utilitarian**:

- Preview → Apply affordance is easy to miss inside modal scroll.
- Command class badges (irreversible / conditional / cancellable) compete with form fields.
- Modal header does not show **pending preview** state at a glance.
- Bar brand shows only `locked` / `authorized` / `open` — no intervention count or last action hint.

**No new divine powers** beyond breakthrough A (`repair_structures`, `clear_ruins` — already specced in A). Chrome only.

### v2 design (viewer-only, thin client)

Gate: `GOD_CONSOLE_CHROME_V2_ENABLED` (default on). Requires `GOD_MODE_ENABLED`. Off = current bar/modal CSS + layout.

| Element | v2 change |
|---|---|
| `#divineBar` | Slightly taller (72→80px); active modal feature button gets **gold underline** + elevated bg |
| Modal header | **Sticky** preview strip: when preview JSON loaded, show command name + reversibility pill + **Apply** (primary) and **Discard** side-by-side top-right (duplicate of footer Apply OK — both wired to same handler) |
| Preview panel | Collapsible default **expanded** when preview exists; syntax-highlight keys via existing escaped text (no innerHTML) |
| Form sections | **Fieldset hierarchy**: H3 section titles, increased vertical rhythm; irreversible fields get left crimson border (reuse prototype tokens from [prototype-divine-console-bottom-bar.html](prototype-divine-console-bottom-bar.html)) |
| Tooltips | Unchanged engine; v2 adds **keyboard shortcut hints** in `data-tip` JSON `d` field: Esc close, Ctrl+Enter apply when preview valid |
| History tab | Last applied entry **pin row** at top of modal body (first 1 only, link to History tab) |
| Bar brand | Secondary line: `N interventions` from `world.godState.interventionCount` or history length (whichever exists in `/state`) |

### Files (4c)

- `simulation/index.html`: CSS block ~750+, `#divineBar`, `#divineModal`, `wireDivineForm`, `openDivineModal`.
- specs/11-viewer.md § Divine Console, specs/01 flag, specs/12-ops.md (UI only — no route changes).

### Verification

- [ ] Unlock → Sight → preview miracle → Apply visible without scrolling.
- [ ] Irreversible miracle: crimson affordance visible before Apply.
- [ ] `GOD_CONSOLE_CHROME_V2_ENABLED` off → matches pre-4c chrome.
- [ ] `GOD_MODE_ENABLED` off → bar hidden regardless of v2 flag.
- [ ] No regression to `wireDivineForm` idempotency / token 401 handling.

---

## 3 — Calendar timing retune (system, Phase 4b)

### Problem

Calendar is a **derived system** from `frameTick` (specs/02-engine-core.md § Time model), not a lone constant:

```
DAY_FRAMES → nightly shelter (_tick_shelter), daily council boundary, ruin cull age
YEAR_FRAMES = N × DAY_FRAMES  (N = days per year, currently 24)
SEASON_FRAMES = YEAR_FRAMES // 4
SEASON_REGROW_MULT → ecology regrowth
AGE_YEARS_PER_TICK = LIFECYCLE_TICK_FRAMES / YEAR_FRAMES → aging
WEATHER_SEASON_STORMINESS + clear dwell scaling → storm pacing
```

At **`TICKS_PER_SEC = 30`**, today’s values feel **rushed for atmosphere observation**: 7.5 min days make dusk/dawn easy to miss; 45 min seasons rotate before viewers absorb winter grading.

**Constraints (must hold):**

- `YEAR_FRAMES % DAY_FRAMES === 0` (whole days per year).
- `SEASON_FRAMES = YEAR_FRAMES // 4` (quarter-year seasons).
- `_calendar()` field **shapes** unchanged: `year`, `season`, `dayOfSeason`, `daysPerSeason`, `isNight`, `dayFraction`.
- `RUIN_CULL_AGE_FRAMES = DAY_FRAMES` (keep ~1 sim day cull age).

### Before / after (recommended)

Uniform **+33% real-time stretch** — preserves 24-day year and 6-day seasons, lengthens every cadence equally so sim balance ratios stay familiar.

| Constant | Before (frames) | After (frames) | Real-time before | Real-time after |
|---|---|---|---|---|
| `TICKS_PER_SEC` | 30 | 30 | — | — |
| **`DAY_FRAMES`** | **13,500** | **18,000** | **7.5 min** | **10.0 min** |
| **`YEAR_FRAMES`** | **324,000** | **432,000** | **3.0 h** | **4.0 h** |
| **`SEASON_FRAMES`** | **81,000** | **108,000** | **45 min** | **60 min** |
| Days per year | 24 | 24 | — | — |
| Days per season | 6 | 6 | — | — |
| `daysPerSeason` (API) | 6 | 6 | — | — |

**Derived impacts (auto from formulas — re-comment in sim_engine.py):**

| Dependent | Before | After |
|---|---|---|
| `AGE_YEARS_PER_TICK` | `300/324000 = 1/1080` | `300/432000 = 1/1440` |
| Sim lifespan 0→90y | ~11.25 h wall | ~15.0 h wall |
| Shelter tick interval | 7.5 min | 10 min |
| `RUIN_CULL_AGE_FRAMES` | 13,500 (~7.5 min) | 18,000 (~10 min) |
| Ecology regrowth per wall min | unchanged per-season mults | same mults, longer seasons |
| `GOODS_TICK_FRAMES` | 900 (~30 s) | **unchanged** (decouple micro ticks from day length) |
| `WEATHER_DWELL_TICKS` | unchanged | **unchanged** initially; storms become slightly more frequent *relative to day* (more goods ticks per day). **Optional follow-up** if soak shows storm fatigue: scale clear dwell max `160 → 120` only — document in specs/05 if applied. |
| `LIFECYCLE_TICK_FRAMES` | 300 | **unchanged** |

### Implementation steps (4b)

1. Edit `simulation/sim_engine.py`: `DAY_FRAMES`, `YEAR_FRAMES`, `SEASON_FRAMES` + block comment at ~1077–1088.
2. Verify `_calendar()` still returns integer `daysPerSeason = SEASON_FRAMES // DAY_FRAMES`.
3. Grep repo for hardcoded `13500`, `324000`, `81000`, `7.5 min`, `3.0 h`, `45 min` in comments/specs — update in **Phase 4d** (specs/02, 08, 06, 05, 10).
4. **Do not** change `/state` JSON keys or calendar object shape.
5. Smokes: `sid_parity_smoke.py`, `path1_smoke.py` — audit for frame constants; update if any assert wall-clock comments.

### Alternative (document only, not recommended unless user rejects +33%)

**12-day year / 3-day seasons** for faster season cycling:

- `DAY_FRAMES = 18000` (10 min), `YEAR_FRAMES = 216,000` (2 h), `SEASON_FRAMES = 54,000` (30 min), 3 days/season.

Breaks the long-standing “24 day/night cycles per year” narrative in sim_engine comments; only use if playtests want faster seasons without longer days.

---

## 4 — New visual feature flags (Phase 4b + 4d)

All **default `True`**. Echoed in `/state` `config.flags`. Off = pre-atmosphere-pack code path (no partial hybrid).

| Flag | Default | Echo | Semantics owner | Off behavior |
|---|---|---|---|---|
| `VISUAL_LIGHTING_V2_ENABLED` | True | yes | specs/11 | Legacy `nightAlpha` / `goldenHourAlpha` / `drawLightGlows` |
| `VISUAL_SEASONAL_TERRAIN_ENABLED` | True | yes | specs/11 | Global `applySeasonTint` only |
| `WEATHER_PARTICLES_V2_ENABLED` | True | yes | specs/11 | Legacy particle constants + intensities |
| `GOD_CONSOLE_CHROME_V2_ENABLED` | True | yes | specs/11, 12 | Pre-4c bar/modal CSS/layout |

**Engine (4b):** module-level constants in `sim_engine.py` alongside other viewer-facing flags; add to `snapshot()["config"]["flags"]` dict (~18224).

**Viewer (4c):** read in `pollState()` with other flags; branch before drawing / God UI render.

**Specs (4d):** add four rows to specs/01-architecture.md flag index; expand specs/11 § feature flags / atmosphere; specs/02 § Time model for calendar numbers only.

**Not env-backed** — unlike `GOD_MODE_ENABLED`, these are code constants for instant rollback during QA.

---

## Phase map

| Phase | Deliverable |
|---|---|
| **4a** (this doc set) | Plans only |
| **4b** | Calendar constants + four flags in engine |
| **4c** | Viewer: lighting, terrain, particles, God chrome |
| **4d** | Specs sync to Implemented |

---

## Cross-verification checklist

- [ ] Full calendar cycle: World Clock HUD day phases align with longer dusk (lighting v2 bands).
- [ ] Winter + terrain v2 + snow particles + night v2: readable.
- [ ] Each of four flags off individually: no crash, legacy path.
- [ ] All four off: indistinguishable from pre-E atmosphere (modulo calendar 4b if merged).
- [ ] Single `simserver` after any server touch.
