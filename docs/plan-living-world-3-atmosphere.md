# Phase 3 — Atmosphere & Time-of-Day Polish

**Parent:** [plan-living-world-0-master.md](plan-living-world-0-master.md)
**Status:** Implemented — completed 2026-07-27.
**Payoff:** Polish — lowest "was dead, now alive" delta, but cheap and it makes
the existing day/night and season systems *legible* instead of subtle.
**Touches:** `simulation/index.html`, `simulation/sprites.js`, `specs/11`.

## Problem (confirmed by audit)

Day/night lighting (`nightAlpha`/`drawLightGlows`, index.html:1246+,
`ENV_EFFECTS_ENABLED`) and seasonal tint + winter snow caps (`applySeasonTint`,
`drawSnowCap`) **already exist** — but they are subtle and there is **no UI
indicator** telling the observer what season/time it currently is. The passage
of time is happening and nearly invisible.

**This phase extends existing systems; it does not build lighting or seasons
from scratch.**

## Deliverables

### 3A — Time & season indicator (`WORLD_CLOCK_HUD_ENABLED`)
- A small non-intrusive HUD badge showing current season and day phase
  (dawn/day/dusk/night), driven by the existing `world.calendar`
  (`season`, `dayFraction`, `isNight`). Pure read of present state.
- **Gate:** `WORLD_CLOCK_HUD_ENABLED`.

### 3B — Dusk/dawn warmth pass (extend `nightAlpha`)
- Today `nightAlpha` is a flat cool overlay ramping in at dusk. Add a warm
  dusk/dawn tint band (golden hour) at the 0.70–0.80 and 0.95–1.00 `dayFraction`
  windows so transitions read as sunrise/sunset, not just "getting dark."
- Reuses the existing overlay compositing in `drawWorld`; no new state.
- **Gate:** folded under the existing `ENV_EFFECTS_ENABLED` — **decided
  2026-07-26** (user chose fold-in over a dedicated flag). No new flag for 3B.

### 3C — Seasonal agent accents (`SEASONAL_AGENTS_ENABLED`)
- Extend the existing season plumbing (already threaded through `sprites.js` draw
  functions as a `season` param, and `spriteSeason` module mirror) to give agents
  a light seasonal accent (e.g. a winter scarf/hat pixel row) — matching the
  polished-pixel-art fidelity decision.
- **Gate:** `SEASONAL_AGENTS_ENABLED`.

## Files & functions

| File | Change |
|---|---|
| `index.html` | HUD badge element + render from `world.calendar`; extend `nightAlpha`/overlay for golden-hour band. Gate on flags. |
| `sprites.js` | Optional seasonal accessory rows in `drawAgentSprite` (reuse `spriteSeason`/`ACCESSORIES` pattern at sprites.js:1382). |
| `specs/11-viewer.md` | Document the HUD, the dusk/dawn warmth extension, seasonal agent accents, and flags. |

## Risks / notes
- Smallest phase; mostly reuses existing plumbing. Main care: don't regress the
  current night overlay or season cache invalidation (`lastSeasonRendered`,
  index.html:905).
- 3B flag question resolved: folded into `ENV_EFFECTS_ENABLED` (no separate flag).

## Verification
- Run server + preview across a full day cycle (or fast-forward). Screenshot
  dawn, day, dusk, night; confirm HUD matches and golden-hour reads correctly.
- Switch season; confirm terrain re-tints and agent accents appear/clear.
- Toggle flags off → clean no-op. Single-instance server check last.

**Implementation verification (2026-07-27):** Deterministic syntax, Python
compile, `/state` flag-echo, log-write, and diff checks completed. Browser visual
smoke was attempted but timed out; no screenshots are claimed.
