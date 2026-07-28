# Master Plan — "Living Ecosystem" (tier 2: new mechanics)

**Status:** Planned — not executing. Awaiting go-ahead.
**Branch:** TBD (propose `feature/living-ecosystem` off `main`).
**Delivery gate:** Nothing merges to `main` without the user's explicit approval.

## Why this plan exists

The [Living World](plan-living-world-0-master.md) batch (shipped 2026-07-27,
commit `ab23244`) surfaced state the engine *already tracked* — structure wear,
social ties, chronicle, activity cues, world clock. That closed the
"nothing looks alive" gap without adding simulation.

This batch is the **next tier**: the six items below add or extend *actual
mechanics*, because the remaining gaps (no weather, no plant growth, no wildlife,
no visible goods movement) cannot be solved by projection alone — the underlying
state does not exist yet, or exists but is architecturally unreachable by the
renderer.

**Scope honesty:** this is a real departure from the Living World batch's
"surface existing state, add nothing" principle, and therefore a partial move
toward the "not a game" non-goal in
[specs/00-overview.md](../specs/00-overview.md). Each phase is flag-gated and
independently revertible so the project can stop at any point. Phase 4 (weather)
is the largest single lift in the set and the one most worth deferring if the
appetite is limited.

## Audit findings that shaped this plan

Verified against the current code before writing. These change the naive plan:

| Finding | Consequence |
|---|---|
| **Zero weather concept exists** (`grep -c weather` → 0) | #1 is genuinely new engine state. But `_maybe_disaster` (sim_engine.py:3851) already narrates *"DISASTER! A storm tears through…"* — weather must **drive** that existing roll, not replace it. |
| **`districtStocks` is never exposed to `/state`** (engine-internal only; restore path sim_engine.py:12806) | #2 (crops) and #3 (wildlife) share one prerequisite — a read-only stock-ratio projection. They belong in the **same phase**, #2 establishing what #3 reuses. |
| **Terrain — including crops and trees — renders into a STATIC offscreen cache** (`terrainCanvas`, index.html:921/1233), invalidated only on season change (index.html:2987) and district founding (index.html:1362) | Crop growth **must** use a few discrete stages that trigger a cache rebuild, exactly like the existing season invalidation. Per-frame growth animation would defeat the cache that exists for performance. This is the single most important constraint in the batch. |
| **`physicalProps` is the existing precedent for state-derived decoration** (engine sim_engine.py:13259 → viewer index.html:3108, *"boats remain resources, not entities"*) | The template for wildlife (#3) and caravans (#4): derive decoration from state at known positions rather than introducing a real entity type with its own tick cost. |
| **Disasters are NOT in `CHRONICLE_MILESTONE_KINDS`** (sim_engine.py:990-993) | Storm damage never reaches the Chronicle panel that just shipped. A one-line fix — the cheapest win in the whole batch (folded into Phase 1). |
| **`GOODS_TICK_FRAMES = 900` (~30s)** already hosts spoilage + decay + disaster (sim_engine.py:727) | The natural cadence for a weather state machine — no new timer needed. |
| **An ocean transit/caravan concept already exists** (`_has_ocean_transit` sim_engine.py:5006; activity line *"Ocean caravan waits for transit supplies"* sim_engine.py:5016) | #4 extends existing transit + the road graph rather than inventing movement. |
| **Ecology regrowth already reacts to season** (`SEASON_REGROW_MULT`, winter = 0) and already narrates recovery/scarcity to the activity log (sim_engine.py ~2750) | #6's weather→ecology suppression has a natural hook: modulate the same regrowth multiplier. |

## The six solutions, phased

Ordered by **payoff-per-effort**. Each phase is independently shippable.

| # | Phase | Solutions | Cost | Child plan |
|---|-------|-----------|------|-----------|
| 1 | Events made visible | #5 district founding, + disaster→chronicle | **Low** | [1-events-and-founding](plan-living-ecosystem-1-events-and-founding.md) |
| 2 | Ecology made visible | #2 crop/tree growth, #3 wildlife | Medium | [2-ecology-visible](plan-living-ecosystem-2-ecology-visible.md) |
| 3 | Goods in motion | #4 caravans/boats | Medium | [3-goods-in-motion](plan-living-ecosystem-3-goods-in-motion.md) |
| 4 | Weather system | #1 weather state machine | **High** | [4-weather](plan-living-ecosystem-4-weather.md) |
| 5 | Weather → governance | #6 ecology/rule feedback | Low* | [5-weather-governance-feedback](plan-living-ecosystem-5-weather-governance-feedback.md) |

\* Phase 5 is cheap in isolation but **hard-depends on Phase 4** — it has no
meaning without weather state. Everything else is independent.

**Recommended entry point: Phase 1.** It extends work that just shipped, is
almost entirely additive, and includes the disaster→chronicle one-liner that
finally makes storm damage legible in the UI.

**Phase 2 is the one that answers the original complaint** ("I'm not seeing
plants, animals") most directly.

## Cross-cutting rules (every phase)

- **Per-item flags**, echoed via `/state` `config.flags` per the convention locked
  in the Living World batch: `FOUNDING_EVENTS_ENABLED`, `CROP_GROWTH_ENABLED`,
  `WILDLIFE_ENABLED`, `CARAVAN_VISUALS_ENABLED`, `WEATHER_ENABLED`,
  `WEATHER_GOVERNANCE_ENABLED`. Every flag must no-op cleanly to today's behavior.
- **Read-only `/state` additions are allowed** (locked decision). Derive on the
  server so thresholds stay authoritative and the viewer stays thin.
- **Tick-cost discipline.** Anything new runs on an existing slow cadence
  (`GOODS_TICK_FRAMES = 900`, ecology tick) — never per-frame in the engine.
  `MAX_CONCURRENT_LLM = 3` cognition must not be starved; the LLM loop is the
  point of the project and takes priority over ambience.
- **No new LLM calls.** Every mechanic here is deterministic. Weather must not
  consult the model.
- **Spec updates in the same change** (SDD, mandatory): [05](../specs/05-world.md)
  world/ecology/terrain, [08](../specs/08-systems-economy.md) disasters/decay,
  [09](../specs/09-systems-society.md) governance, [11](../specs/11-viewer.md)
  every render pass, [01](../specs/01-architecture.md) flag index (currently
  documents 43 flags — update the count).
- **Action-sync invariant** applies only if a phase adds an agent action. As
  scoped, none do — verify per phase.
- **Model policy** ([CLAUDE.md](../CLAUDE.md)): orchestrator plans/reviews; all
  implementation dispatched to `implementer` subagents on Sonnet.
- **Server discipline:** single-instance rule, and remember `uv run` legitimately
  shows **two** `python.exe` (wrapper → interpreter parent/child) = one instance.
  Verify `ParentProcessId`/port 5001 before killing anything.

## Status log

- 2026-07-27 — Master + 5 child plans drafted from a fresh code audit. Not
  executing. Awaiting go-ahead and phase selection.
