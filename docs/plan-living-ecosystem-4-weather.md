# Phase 4 — Weather System

**Parent:** [plan-living-ecosystem-0-master.md](plan-living-ecosystem-0-master.md)
**Status:** Planned — not executing.
**Solutions covered:** #1 (real weather with visible causes).
**Cost:** **High — the largest single lift in the batch.** Defer this phase if
appetite is limited; Phases 1-3 deliver more per unit of effort.
**Touches:** `simulation/sim_engine.py`, `simulation/index.html`,
`simulation/sprites.js`, `specs/05`, `specs/08`, `specs/11`.

## Problem

**No weather concept exists anywhere in the engine** — `grep -c "weather"` over
`sim_engine.py` returns **0**. What exists instead is a bare probability roll:

`_maybe_disaster` (sim_engine.py:3851) fires at `DISASTER_PROB = 0.005` per goods
tick, picks a random structure, and subtracts `DISASTER_DAMAGE = (40, 70)`. It
already narrates *"DISASTER! A storm tears through the {name} in {district}"* — so
the fiction claims a storm, but there is no storm: no buildup, no sky change, no
rain, no warning, and no spatial locality (the "storm" hits exactly one structure
anywhere in the world).

The only atmospheric systems today are the **season tint** (baked into the terrain
cache) and the **day/night overlay** + light glow (`nightAlpha`,
`drawLightGlows`, `ENV_EFFECTS_ENABLED`) — both shipped and both unrelated to weather.

## Deliverables

### 4A — Weather state machine (`WEATHER_ENABLED`)
A deterministic, LLM-free state machine on the **existing** `GOODS_TICK_FRAMES = 900`
cadence (~30s) — the same tick that already hosts spoilage, decay, and disaster,
so **no new timer is introduced**.

States: `clear → gathering → storm → clearing → clear`, with per-state dwell
durations and transition probabilities. Persist current state + entered-frame in
`civilization` (restore-safe via `setdefault`, following the
`lastRuleAttemptFrame`/`priorityRuleSeq` precedent).

- **Season-weighted probabilities** so weather reads as seasonal (reuse
  `_current_season()`); winter storms vs summer clear spells.
- **Optional but recommended: spatial locality** — a storm targets one or a few
  districts rather than the whole world, so the sky effect and the damage agree.
  If that proves fiddly, a world-wide storm is acceptable for v1 — but then the
  damage roll must also stop pretending to be localized.
- Expose read-only in `/state`: `weather: {state, since, districts?}`.

### 4B — Disaster becomes storm-caused (behavior change — flag-gated)
Rewire `_maybe_disaster` so damage fires **only during the `storm` state** (and
optionally at elevated probability), instead of firing at random during clear skies.

> **This changes existing behavior and must be gated.** With `WEATHER_ENABLED`
> off, `_maybe_disaster` must behave *exactly* as today. With it on, the long-run
> damage rate should be roughly **calibrated to today's expected rate** (~once per
> 100 real minutes) rather than silently making the world harsher — otherwise
> structure decay pressure, repair economics, and ruin counts all shift as a side
> effect of an ambience feature. State the calibration explicitly in the spec.

Also emit a chronicle milestone for storms (Phase 1 adds the `disaster` kind — if
Phase 1 has not shipped, include that here).

### 4C — Viewer: visible sky and precipitation
- **Sky tint** ramping with state (darkening through `gathering`, heaviest in
  `storm`), composited in the same full-canvas overlay stage as the existing night
  overlay so the two combine coherently rather than fighting.
- **Rain/snow particles** (snow when `_current_season()` is winter), drawn in the
  per-frame pass — deterministic from `frameTick`, no retained state, same
  discipline as the shipped smoke/dust cues.
- **Optional:** brief lightning flash on a damage event, giving the storm a visible
  moment of cause. Keep it subtle and rate-limited — this must not become a
  seizure risk or a distraction.
- **Gate:** `WEATHER_ENABLED` off → no sky change, no particles.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | Weather state machine on the `GOODS_TICK_FRAMES` branch; persisted state + restore `setdefault`; `_maybe_disaster` rewired behind the flag with calibrated rate; `/state.weather` projection; `WEATHER_ENABLED` echoed in `config.flags`. |
| `sprites.js` | Rain/snow particle helpers; optional lightning. |
| `index.html` | Sky-tint overlay integrated with the existing night overlay stage; particle pass; gate on the flag. |
| `specs/08-systems-economy.md` | Rewrite the **Disasters** row: storm-gated, with the rate calibration stated. |
| `specs/05-world.md` | Document the weather state machine, states, cadence, season weighting, and locality. |
| `specs/11-viewer.md` | Document sky tint, particles, and their interaction with the night overlay. |
| `specs/01-architecture.md` | Flag index +1. |

No new agent actions in this phase (agents do not yet *react* to weather — that is
Phase 5) → **action-sync invariant N/A**.

## Risks / notes
- **Rate calibration is the top risk.** If storm-gating raises effective damage,
  the village gets measurably harsher and the existing decay/repair balance shifts.
  Compute the expected long-run rate and match today's before shipping.
- **Overlay stacking:** night overlay + golden-hour band (shipped) + storm tint
  can compound into an unreadably dark screen. Clamp total darkness and test at
  the worst case (winter storm at deep night in an unlit district).
- **Particle cost** at full zoom-out over a large world — cap particle count by
  visible area, not world area.
- **Determinism:** the engine uses `random` for disasters today, which is fine, but
  weather state must survive save/restore coherently (persist the state, do not
  re-roll it on load).
- **Non-goal creep:** weather that damages crops, blocks gathering, or strands
  agents is Phase 5 / beyond. This phase is state + visuals + rewired damage only.

## Verification
- Force each state in a scratch harness → confirm sky/particles match and
  transitions are reachable; confirm state survives `save_state`/`restore_state`.
- Measure damage rate over a long simulated run with the flag on vs off → confirm
  comparable (calibration check).
- Visual worst case: winter + storm + deep night + unlit district → still readable.
- Toggle `WEATHER_ENABLED` off → `_maybe_disaster` behaves byte-identically to
  today; no visual change.
- Deterministic smokes + single-instance server check last.
