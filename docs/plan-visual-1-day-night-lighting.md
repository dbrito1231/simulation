# Visual Plan 1: Day/Night Lighting Overlay

Status: PLANNED (not implemented). Effort: small (viewer-only + one tiny engine addition). Do this one first — cheapest change, biggest perceived impact.

## Context

The engine has a real day/night cycle (`DAY_FRAMES = 13500`, night = last 25% of the day via `NIGHT_FRACTION = 0.25`, `_is_night()` at sim_engine.py ~4320) that drives shelter/night-pressure mechanics, and since the 2026-07-14 time unification the viewer receives `world.calendar.isNight`. But the canvas never reflects it: `drawWorld()` (index.html ~995) blits the static terrain cache + ocean foam and nothing else. Night is only visible as text ("· night" in the Time panel and settlements meta). Goal: the world visibly darkens at night, with a smooth dusk/dawn transition rather than a hard flip.

## Design

A translucent darkening pass over the whole scene, drawn **after** structures/agents in `tick()` (index.html ~2182) so everything is dimmed together, plus warm "window glow" omitted (that belongs to Plan 3 territory). Smoothness comes from computing a continuous day-phase fraction instead of using the boolean `isNight`:

- Engine: extend `_calendar()` (sim_engine.py, next to `_current_season()`) with one field:
  `"dayFraction": (self.frameTick % DAY_FRAMES) / DAY_FRAMES` — 0.0 at dawn, night begins at 0.75 (= `1 - NIGHT_FRACTION`). Keep `isNight` (settlements panel + Time panel already use it).
- Viewer: derive a darkness alpha from `dayFraction` with a short ramp on each side of the night boundary so dusk fades in over ~10% of a day (~45 real seconds) and dawn fades out likewise:
  - `f < 0.70` → alpha 0 (full day)
  - `0.70–0.80` → ramp 0 → MAX
  - `0.80–0.95` → MAX (deep night)
  - `0.95–1.00` → ramp MAX → 0 (dawn)
  - `MAX_NIGHT_ALPHA ≈ 0.45`, color `#0a1030` (cool dark blue, not pure black) via a single `fillRect` with `ctx.fillStyle = rgba(...)` over `0,0,WORLD_W,WORLD_H`. Logical coordinates — the ctx already carries the DPR transform, so no scaling math needed.
- The viewer polls at ~10 Hz but renders at 60 fps; interpolate nothing — dayFraction changes ~0.0007 per poll, far below visible stepping.
- Since polls can lag, fall back gracefully: if `world.calendar?.dayFraction` is missing (old server), use `isNight ? MAX : 0`.

Optional polish (include if trivial during implementation, otherwise skip): dim the minimap with the same alpha in `renderMinimap()`.

## Steps (implementer subagent-ready)

1. **sim_engine.py** — in `_calendar()` add `"dayFraction": (self.frameTick % DAY_FRAMES) / DAY_FRAMES,`. Nothing else changes; `_is_night()` stays the mechanics authority.
2. **index.html** — add a module-level helper near `drawWorld()`:
   ```js
   const MAX_NIGHT_ALPHA = 0.45;
   function nightAlpha(cal) {
     if (!cal) return 0;
     const f = cal.dayFraction;
     if (f == null) return cal.isNight ? MAX_NIGHT_ALPHA : 0;
     if (f < 0.70 ) return 0;
     if (f < 0.80) return MAX_NIGHT_ALPHA * (f - 0.70) / 0.10;
     if (f < 0.95) return MAX_NIGHT_ALPHA;
     return MAX_NIGHT_ALPHA * (1.00 - f) / 0.05;
   }
   ```
3. **index.html** — in `tick()` (~2207), after the `drawList` loop and **before** `renderSidebar()`, add:
   ```js
   const na = nightAlpha(world.calendar);
   if (na > 0) {
     ctx.fillStyle = `rgba(10, 16, 48, ${na.toFixed(3)})`;
     ctx.fillRect(0, 0, WORLD_W, WORLD_H);
   }
   ```
   Note: this dims speech bubbles/labels too since they're drawn in the same pass — acceptable at alpha 0.45; if readability suffers, move the fillRect before the label-drawing helpers instead (drawAgent draws labels internally, so the simple version is fine to start).

## Verification

1. `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py` pass (calendar shape change only).
2. `curl http://127.0.0.1:5001/state` → `calendar.dayFraction` present, in `[0,1)`.
3. Browser at `http://127.0.0.1:5001`: temporarily verify the ramp without waiting ~6 min for dusk by evaluating in the console: `world.calendar.dayFraction = 0.85; // next poll overwrites` and confirming the canvas darkens for a moment. Then watch a real dusk boundary (night starts at 75% of each ~7.5-min day).
4. No console errors; FPS unaffected (one fillRect/frame).
