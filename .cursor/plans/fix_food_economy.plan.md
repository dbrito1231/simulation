---
name: Fix Food Economy Deficit and Starving-Agent Behavior
overview: "Log review of sessions 2026-07-01T20-07-21 and 2026-07-01T20-28-57 (sim_engine.py era) found the village food economy runs structurally negative. A first fix wave (already applied, uncommitted) stopped the death spiral: REVIVE_HUNGER=35 floor on both revival paths (was: revive at 0 hunger -> re-collapse in ~8s), food/fish exempt from the resource tax (stockpile is never consumed, so taxed edibles were deleted), EDIBLE_RESERVE=3 kept back from force-contribution, and a _share_edible_with() proximity backstop. Result: collapses dropped ~5x (36/5min -> 5/3.5min) and recover->collapse gap grew 8s -> ~2.5min — but the village still runs food-negative: ~2 edibles collected per 3.5min against a need of ~6.4/min (8 agents x 1 edible per 75s), and 0 auto-shares fired because the surplus holder (fisher at beach) is never within SHARE_RADIUS=120 of the starving (cave/farm/workshop). This plan closes the deficit: (1) halve the hunger drain (HUNGER_RATE 0.6 -> 0.3) so demand matches what ~3 part-time producers can sustain; (2) add a deterministic seek-food backstop (same _maybe_* shape as force-contribution) so a starving agent with nothing to eat heads to the nearest edible gather zone instead of waiting for the LLM to act on a nudge; (3) verify, in the same run, the also-unverified full-district fixes (_resolve_build_district cap check + _maybe_relocate_stuck_project) against the live stuck farm_north Farm Plot (30/30 structures, 100% funded)."
todos:
  - id: halve-hunger-rate
    content: "sim_engine.py: HUNGER_RATE 0.6 -> 0.3. Demand math: at 0.6/s each agent needs 1 edible per 75s (village of 8 = ~6.4/min); observed production is ~0.6/min. At 0.3 the need is ~3.2/min, within reach of 1 farmer + 2 fishers who also do build work. Full hunger now lasts ~5.5min instead of ~2.8min."
    status: completed
  - id: seek-food-constants
    content: "sim_engine.py: add STARVING_HUNGER = 10 (seek-food trigger threshold) next to the other survival constants (REVIVE_HUNGER / EDIBLE_RESERVE / SHARE_RADIUS block)."
    status: completed
  - id: maybe-feed-starving
    content: "sim_engine.py: add _maybe_feed_starving(), called from the existing RULES_TICK_FRAMES gate in _tick_once (alongside _maybe_relocate_stuck_project / _maybe_force_contribution). For each agent with hunger <= STARVING_HUNGER, not incapacitated, no edible held (_first_edible is None), and not a Sage-emergency responder: clear agent['goal'] and apply_decision collect_resource targeting the nearest edible source (food@farm vs fish@beach by _distance to the zone), then set agent['goal'] = _goal_for_decision(...) so the goal engine walks them there and gathers without further LLM calls. Auto-eat in _update_survival then feeds them on the first collect. Mirrors the rushToHeal precedent: survival overrides tasks deterministically; the existing hungry-NOTE nudge stays as coherence."
    status: completed
  - id: docs
    content: "CLAUDE.md: update the SURVIVAL_ENABLED bullet — hunger drains at 0.3/survival-tick; revival floors hunger at REVIVE_HUNGER; edibles are tax-exempt and force-contribution leaves EDIBLE_RESERVE; starving agents auto-share in proximity and deterministically seek the nearest food zone."
    status: completed
  - id: verify-food-economy
    content: "Run the server ~10 min from the persisted state.json (village lvl 28, 6/8 collapsed at last save). Expect in activity.jsonl: collapse count near zero after the first recovery wave (baseline: 5/3.5min post-wave-1, 36/5min pre-fix); 'ate food/fish' spread across most of the roster (baseline: only Colt+Marco); 'X shared food with Y' events now that seekers converge on food zones; no agent stuck in a collapse->recover cycle."
    status: completed
  - id: verify-build-stall-fixes
    content: "Same run — verify the already-applied (uncommitted, never yet run) full-district fixes: one 'The Farm Plot build moves to farm_south — farm_north has no land left' relocation event near startup; the relocated Farm Plot completes; zero new 'no room left to build' lines (baseline: 42/3.5min); no new project ever starts in a district at build-grid cap."
    status: completed
isProject: false
---

# Fix Food Economy Deficit and Starving-Agent Behavior

## Context

Two log-review sessions on 2026-07-01 diagnosed a starvation crisis: at worst **6 of 8 agents collapsed simultaneously**, with 36 collapses vs 9 revivals in ~5 minutes and the healer/elder locked in a mutual-rescue loop.

**Wave 1 (already applied to `simulation/sim_engine.py`, uncommitted)** fixed the four mechanical flaws:

1. **Revival trap** — recovery restored 15 health but left hunger at 0, so health drained 2/s and the agent re-collapsed in ~8s. Both revival paths (self-recovery in `_update_survival`, heal-revive in `heal_agent`) now floor hunger at `REVIVE_HUNGER = 35`.
2. **Tax black hole** — every contribution was taxed +1 into `civilization["stockpile"]`, which nothing ever consumes; taxed food was deleted from the economy. `_enforce_resource_tax` now exempts `EDIBLE_RESOURCES`.
3. **Force-contribution death spiral** — builds need food/fish, and `_maybe_force_contribution` confiscated them from whoever held the most, i.e. the last agents standing. It now leaves each agent `EDIBLE_RESERVE = 3`.
4. **Hoarding** — auto-eat only touches your own inventory (fisher at 100 health with 9 fish next to a starving village). New `_share_edible_with()`: a starving agent receives 1 edible from a non-collapsed neighbour within `SHARE_RADIUS = 120` holding above the reserve.

**Verified outcome (session 2026-07-01T20-28-57, ~3.5 min):** collapses down ~5x, recover→collapse gap 8s → ~2.5 min, all collapses recovered. **Still broken:** only 2 edibles collected in 3.5 min against a ~6.4/min need, only 2 of 8 agents ever ate, and 0 auto-shares fired (surplus holder never within 120px of the starving). The death spiral became a slow deficit — demand still exceeds what ~3 part-time food producers can supply, and starving agents wait passively for LLM decisions that arrive too late.

## Changes (wave 2)

### 1. Halve the hunger drain
`HUNGER_RATE`: **0.6 → 0.3** (per survival tick, 1/s). Demand drops from ~6.4 to ~3.2 edibles/min for 8 agents — matching realistic production from 1 farmer + 2 fishers who also contribute to builds. This is the arithmetic fix; everything else is distribution.

### 2. Deterministic seek-food backstop
New `_maybe_feed_starving()` in the established `_maybe_*` backstop style, called from the existing `RULES_TICK_FRAMES` gate in `_tick_once`:

- **Trigger:** hunger ≤ `STARVING_HUNGER` (10), not incapacitated, holds no edible, not a Sage-emergency responder.
- **Action:** clear the agent's goal, `apply_decision` a `collect_resource` targeting the nearest edible source (food@farm or fish@beach, by distance), and install the corresponding persistent goal via `_goal_for_decision` so `_step_goal` walks them there and gathers with no LLM round-trips. `_update_survival`'s auto-eat feeds them on the first collect.
- **Precedent:** identical philosophy to `rushToHeal` — survival is too important to leave to prompt nudges; enforcement is deterministic, the existing "You are hungry and have no food" NOTE remains for coherence.
- Side effect: seekers converge on the farm/beach where surplus holders work, so the existing `_share_edible_with()` proximity backstop starts actually firing.

## Key existing code to reuse
- `_maybe_force_contribution` / `_maybe_relocate_stuck_project` — the tick-gated backstop shape and `apply_decision`-based mutation.
- `_goal_for_decision` + `_step_goal` — LLM-free goal execution for the walk-and-gather.
- `_first_edible`, `_share_edible_with`, `_gather_zone_for_resource`, `_sage_responders` — all already in `sim_engine.py`.

## Out of scope
Role rebalancing (more farmers), raising `SHARE_RADIUS`, elder task-assignment changes, and any prompt/schema changes. If the 10-minute verification still shows a deficit, revisit with a producer-count backstop (auto-switch an idle role to farmer) as wave 3.

## Verification (no test suite — run server + read JSONL logs)
1. `uv run python -m py_compile simulation/sim_engine.py`; start server; state resumes from `state.json` (village level 28, 6/8 collapsed at last save).
2. **Recovery wave:** all collapsed agents recover and *stay up* — no collapse→recover cycling.
3. **Food economy:** "ate food/fish" events across most of the roster (baseline: only Colt + Marco); "shared" events > 0 (baseline: 0); collapse count ~0 after the first recovery wave.
4. **Seek-food:** starving agents visibly head to farm/beach and collect within one tick-gate of hitting the threshold.
5. **Build-stall fixes (wave 1, never yet run):** exactly one relocation line for the stuck farm_north Farm Plot near startup; it completes in farm_south; zero new "no room left to build" (baseline: 42/3.5 min); no project starts in a full district.
6. `lm_studio.jsonl`: no "Context size has been exceeded" (LM Studio now at 32768 ctx / 4 slots); fallback rate near zero.
