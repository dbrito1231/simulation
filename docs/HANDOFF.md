# Handoff — Four Breakthroughs (A / C / B / E)

**Updated:** 2026-07-31

**Branch:** `feature/four-breakthroughs-ace`

**HEAD:** `b0584ef docs(sdd): note atmosphere rendering is permanent in specs/11`

**Status:** Four breakthroughs **A**, **C**, **B**, and **E** are implemented on this branch (based on the God-mode feature stack). Plan-level exclusions **D** (God Compiler Phase 8) and **F** (`ALWAYS_ON_MODULES` / PIANO re-soak) remain out of scope.

## Start here

Read [CLAUDE.md](../CLAUDE.md) first — server-authoritative architecture, action-sync invariants, and the orchestrator/Sonnet-5 model policy.

Active delivery plan:

- [.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md](../.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md) — phased A → C → B → E breakdown, verification gates, and exclusions

## What landed (this branch)

Commits since `fad699d` (storm presence pack on main):

| Phase | Breakthrough | Summary |
|---|---|---|
| 1 | **A — Town integrity** | Slower decay (`0.025`/goods tick), autonomous repair campaigns + widened critical backstop, in-sim ruin cull, disaster retune (`DISASTER_PROB=0.002`, damage `(30,55)`), God `repair_structures` + `clear_ruins` |
| 2 | **C — Hunt + conflict** | Stock/wildlife-aware hunter precedence, hunt damage retune (`HUNT_DAMAGE=2`, `HUNT_DAMAGE_HUNTER=4`), forced hunt goals when starving with prey but no gatherable food, bounded PvP via `confront_agent` |
| 3 | **B — Real trade** | Per-settlement `settlementStores`, authoritative caravan delivery + ocean water pathing, treaty tariffs (`TREATY_TARIFF_MAX=0.25`), new `deliver_caravan` action (full action-sync) |
| 4 | **E — Atmosphere** | Calendar stretch (`DAY_FRAMES=18000`, `YEAR_FRAMES=432000`, `SEASON_FRAMES=108000`), permanent viewer lighting/seasonal terrain/weather particles/God console chrome |

### A — Town integrity

- `STRUCTURE_DECAY_PER_GOODS_TICK = 0.025` (~23.3 h to disrepair, ~33.3 h to ruin)
- Repair campaigns: `REPAIR_CAMPAIGN_RUIN_RATIO=0.15`, `WORKING_FRAC=0.5`, `MAX_ASSIGN=2`
- Ruin cull: `_maybe_cull_ruins()` removes 1–3 aged unaffordable ruins per tick when pressure is high
- Disaster retune: legacy branch `DISASTER_PROB=0.002`; storm branch unchanged at `STORM_DISASTER_PROB=0.32`
- God mass-repair: `repair_structures` (batch condition restore / un-ruin) and `clear_ruins` (registry deletion)

### C — Hunt + conflict

- Hunter promoted ahead of unfilled farmer/fisher when wildlife present and meat scarce
- Forced hunt goals when `hunger ≤ STARVING_HUNGER`, prey in range, no reachable gatherable edible
- `confront_agent` action: contact-range PvP with rivalry/pressure gating, steal + flee, non-lethal floor unless target already critical

### B — Real trade

- `civilization["settlementStores"][sid]` — local gather overflow and caravan credits prefer settlement store; repair/craft draws own store then village stockpile fallback
- `_deliver_caravan` debits traveler, credits destination store, applies treaty tariff split
- Ocean corridor pathing when transit unlocked; `deliver_caravan` in full action-sync chain

### E — Atmosphere

- **Calendar stretch (+33% real-time cadence):** 7.5 → 10 min days, 45 → 60 min seasons, 4 h in-world year (24 day/night cycles)
- Viewer: stronger day/night lighting, seasonal terrain palettes, weather particles, Divine Console chrome (all permanent defaults when their parent gates are on)
- Plan docs: `docs/plan-visual-1-day-night-lighting.md`, `docs/plan-visual-2-seasonal-terrain-grading.md`, `docs/plan-visual-atmosphere-systems.md`

## How to verify

Deterministic smokes (no Ollama, no live `state.db`):

```bash
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
uv run python scripts/god_mode_smoke.py
uv run python scripts/town_integrity_smoke.py
uv run python scripts/hunt_conflict_smoke.py
```

Live soak (optional): start server in a titled `simserver` cmd window, open `http://127.0.0.1:5001`, watch browser + `simulation/logs/<timestamp>/`. After any server touch, confirm only one `simulation/server.py` process (see CLAUDE.md).

Branch change inventory: [changes.md](../changes.md) at repo root.

## God mode (already implemented upstream)

This branch builds on the God-mode feature stack already merged upstream of these breakthroughs. The original planning artifacts remain useful context but **implementation is done** — do not treat them as pending work:

- [plan-sovereign-god-mode-v2.md](plan-sovereign-god-mode-v2.md) — contract reference (preview/apply, idempotency, modifier arithmetic)
- [plan-sovereign-god-mode.md](plan-sovereign-god-mode.md) — superseded history

Shipped God-mode surface (unchanged by A/C/B/E except A's mass-repair additions):

- Startup-only `SIM_GOD_MODE=1` + required `SIM_GOD_TOKEN`
- Authenticated `/control/god/*` routes (Sight, Voice, Providence, Miracles, Storyteller, Laws)
- Divine Console in viewer; `divine.jsonl` audit stream
- `civilization["godState"]` persistence

Phase **A** extended God mode with `repair_structures` and `clear_ruins` (amended specs/02 invariants and updated `god_mode_smoke.py`).

## Exclusions (still out of scope)

- **D** — God Compiler Phase 8 (free-prose story compiler)
- **F** — `ALWAYS_ON_MODULES` / PIANO re-soak

## Next-agent checklist

1. Read `CLAUDE.md` and the plan at `.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md`.
2. Run all five smokes above before merging or starting new work.
3. SDD: edit owning specs before code; keep specs synchronized with behavior.
4. New/changed agent actions require full action-sync (server.py, sim_engine.py, index.html, specs/07).
5. God routes stay off the agent decision catalog.
6. Do not commit `state.db`, `simulation/logs/`, or credentials.
7. Branch stays open until user approves merge/PR to main.
