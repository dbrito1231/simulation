# Plan — Day/Night Lighting v2

> **Superseded (2026-07-31):** `VISUAL_LIGHTING_V2_ENABLED` removed; v2 lighting is the
> permanent viewer default. See `specs/11-viewer.md` § Atmosphere rendering.

**Parent:** [two_sim_breakthroughs plan](../.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md) § E — Full atmosphere pack  
**Status:** Planned — documentation only (Phase 4a). Implementation in Phase 4c.  
**Branch:** `feature/four-breakthroughs-ace`  
**Gate:** `VISUAL_LIGHTING_V2_ENABLED` (default **on**; off = pre-v2 `nightAlpha` / `goldenHourAlpha` / `drawLightGlows` path)  
**Touches (4c):** `simulation/index.html`, `specs/11-viewer.md`, `specs/01-architecture.md` (flag index)

## Problem

Day/night lighting shipped in [plan-living-world-3-atmosphere.md](plan-living-world-3-atmosphere.md) (3B golden hour + existing `nightAlpha`, specs/11-viewer.md § day/night overlay) but remains **too subtle**:

- Dusk/dawn windows are only 10% of the day (`dayFraction` 0.70–0.80 and 0.95–1.00) on a **7.5 min** cycle — easy to miss while watching agents.
- `MAX_NIGHT_ALPHA = 0.45` navy overlay reads as “slightly dim” rather than night.
- Lit-district radial glow (`drawLightGlows`, `LIGHT_GLOW_RADIUS = 140`) barely pushes back the dark; unlit districts at deep night + weather can still feel flat.
- Golden hour (`goldenHourAlpha`, max 0.13) is a thin wash; it does not sell sunrise/sunset against the cool night ramp.

**Extends existing systems; does not replace calendar or engine night logic** (`NIGHT_FRACTION = 0.25`, `_is_night()` unchanged).

## Goals

1. **Stronger dusk/dawn** — wider, warmer transition bands with visible color shift before/after deep night.
2. **Deeper night** — higher peak darkness while preserving agent/structure readability (respect `MAX_NIGHT_PLUS_WEATHER_ALPHA` stacking with weather).
3. **Stronger lit radial pushback** — fuelled structures in `litDistricts` read as islands of warmth at night.
4. **Compose with ecology stages** — night overlay and golden hour sit *above* terrain cache (unchanged); ecology stage affects terrain brightness under the overlay, so v2 must not re-bake lighting into `terrainCanvas`.

## Non-goals

- No new `/state` calendar fields.
- No per-structure dynamic light sources beyond existing `light` + `litDistricts`.
- No changes to `ENV_EFFECTS_ENABLED` semantics (v2 is a separate flag for rollback).

---

## Current baseline (flag off / pre-v2)

| Symbol | Location | Value / behavior |
|---|---|---|
| `MAX_NIGHT_ALPHA` | index.html ~1841 | `0.45` |
| Dusk ramp | `nightAlpha` | `dayFraction` 0.70 → 0.80 linear |
| Deep night hold | `nightAlpha` | 0.80 → 0.95 at full alpha |
| Dawn ramp | `nightAlpha` | 0.95 → 1.00 linear out |
| `goldenHourAlpha` | index.html ~1854 | max `0.13`, sin bell in same 0.70–0.80 / 0.95–1.00 windows |
| `LIGHT_GLOW_RADIUS` | index.html ~2018 | `140` world px |
| Glow center alpha | `drawLightGlows` | `0.35 * (na / MAX_NIGHT_ALPHA)` |
| Compositing order | `renderWorld` pass | terrain → … → night fill → weather sky → golden hour → light glows → particles |

Engine night for gameplay (`PRESSURE_LOOP_ENABLED`, shelter, night-pressure) uses `isNight` / last quarter of day — **viewer-only** retune here does not change that.

---

## v2 design

### A — Wider transition bands (`nightAlphaV2`)

Stretch twilight so observers can *see* the cycle without fast-forward:

| Phase | `dayFraction` range | v2 behavior |
|---|---|---|
| Day | `< 0.62` | alpha `0` |
| Dusk in | `0.62 – 0.78` | ease-in quad to peak (was 0.70–0.80) |
| Deep night | `0.78 – 0.92` | hold at peak (was 0.80–0.95) |
| Dawn out | `0.92 – 1.00` | ease-out quad to `0` (was 0.95–1.00) |

Implementation: replace linear segments with smoothstep or quadratic ease; keep `nightAlpha(cal)` as the public name but branch on `VISUAL_LIGHTING_V2_ENABLED` at the top (flag off → delegate to today’s function body verbatim).

**Peak alpha:** raise `MAX_NIGHT_ALPHA` to **`0.58`** under v2 (from `0.45`). Recompute `MAX_NIGHT_PLUS_WEATHER_ALPHA` clamp if needed — propose keeping **`0.68`** combined cap but document that v2 worst-case (deep night + storm, no glow) is intentionally darker; verify agents remain identifiable in browser QA.

### B — Richer golden hour (`goldenHourAlphaV2`)

| Parameter | Current | v2 |
|---|---|---|
| Max alpha | `0.13` | **`0.22`** |
| Dusk band | 0.70–0.80 | **0.62–0.78** (match dusk ramp) |
| Dawn band | 0.95–1.00 | **0.92–1.00** |
| Color | single `rgba(255, 180, 80, …)` fill | **dual pass**: warm orange base + narrower pink rim at band edges |

Draw order unchanged: after weather sky tint, before light glows. Use `globalCompositeOperation = "lighter"` for the rim pass only.

Gate: requires `ENV_EFFECTS_ENABLED` **and** `VISUAL_LIGHTING_V2_ENABLED` (golden hour v2 is part of lighting v2; if `ENV_EFFECTS_ENABLED` is off, skip golden hour entirely — same as today).

### C — Lit radial pushback (`drawLightGlowsV2`)

| Parameter | Current | v2 |
|---|---|---|
| `LIGHT_GLOW_RADIUS` | `140` | **`200`** |
| Center alpha | `0.35 * strength` | **`0.55 * strength`** |
| Falloff | single stop | **three-stop gradient**: hot core → mid → transparent |
| District bleed | none | optional **second wider halo** at `0.12 * strength`, radius `280`, only when `na >= 0.4 * MAX_NIGHT_ALPHA` |

Still keyed on `structure.light === true` and `litDistricts.includes(s.districtId)` — no new server fields.

### D — Subtle day desaturation (optional, v2 only)

During deep night (`na > 0.35`), apply a **very low** full-canvas desaturate pass (`saturation` composite, alpha `0.08`) *before* the navy overlay so terrain/ecology stages read “cooled” under moonlight. Skip when `na === 0`. Keeps ecology stage visibility: barren districts look dimmer/sparser; lush districts still read green/brown through the stack.

---

## Ecology composition

Ecology stages (`districtEcology[].stage`: `barren` / `sparse` / `healthy` / `lush`) are baked into `terrainCanvas` at cache build time (specs/11-viewer.md § ecology stages). Lighting v2 is **post-cache overlay only**:

- Do **not** multiply stage into `applySeasonTint` or terrain tiles.
- Do invalidate terrain cache on season change only (existing `lastSeasonRendered` / `lastEcologyStageKeyRendered` keys unchanged).
- QA: screenshot same district at `healthy` vs `barren` at dusk and deep night; stage difference must remain visible through v2 overlays.

---

## Implementation steps (Phase 4c)

1. **Engine flag** (Phase 4b, but listed here for contract): add `VISUAL_LIGHTING_V2_ENABLED = True` in `sim_engine.py`, echo in `config.flags`.
2. **Viewer flag read** — in `pollState()` flag block (~index.html:3807), set module boolean `VISUAL_LIGHTING_V2_ENABLED`.
3. **`nightAlpha`** — branch: off → current body; on → v2 bands + `MAX_NIGHT_ALPHA_V2 = 0.58`.
4. **`goldenHourAlpha`** — branch on v2 + `ENV_EFFECTS_ENABLED`.
5. **`drawLightGlows`** — branch radii/alphas; extract constants to top of file with `_V2` suffix for diff clarity.
6. **`renderWorld` overlay stage** — insert optional desaturate pass when v2 + `na > 0.35`.
7. **World Clock HUD** — no change required (still maps `dayFraction` to dawn/day/dusk/night); optional: widen HUD “dusk” label threshold to match 0.62 if it uses hardcoded cutoffs (audit `renderWorldClockHud`).
8. **Specs** — Phase 4d: document v2 constants, flag, and fallback in specs/11 + flag row in specs/01.

---

## Constants summary (implementer copy-paste)

```javascript
// Flag off: keep existing MAX_NIGHT_ALPHA = 0.45 and current functions.
const MAX_NIGHT_ALPHA_V2 = 0.58;
const LIGHT_GLOW_RADIUS_V2 = 200;
const LIGHT_GLOW_HALO_RADIUS_V2 = 280;
const GOLDEN_HOUR_MAX_V2 = 0.22;
const TWILIGHT_START_V2 = 0.62;
const TWILIGHT_END_DUSK_V2 = 0.78;
const TWILIGHT_START_DAWN_V2 = 0.92;
const NIGHT_DESAT_ALPHA_V2 = 0.08;
```

---

## Verification

- [ ] Full day cycle at default zoom: dusk/dawn visibly warmer and longer than v1; deep night clearly darker.
- [ ] Lit workshop/house district: warm pool visible; unlit adjacent district darker (pushback obvious).
- [ ] Winter storm + deep night + unlit: combined alpha ≤ clamp; agents still clickable/readable.
- [ ] Ecology: `barren` vs `lush` forest distinguishable at night.
- [ ] `VISUAL_LIGHTING_V2_ENABLED` off → pixel-identical to pre-4c lighting (compare screenshot or diff overlay alphas).
- [ ] `ENV_EFFECTS_ENABLED` off → no golden hour with or without v2.
- [ ] Single `simserver` instance after viewer touch.

**Depends on:** Phase 4b calendar retune optional — longer `DAY_FRAMES` (see [plan-visual-atmosphere-systems.md](plan-visual-atmosphere-systems.md)) makes v2 transitions easier to observe but is not required for v2 code land.
