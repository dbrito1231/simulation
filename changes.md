# Four Breakthroughs (A / C / B / E) — Branch Change Inventory

**Branch:** `feature/four-breakthroughs-ace`  
**Base:** `fad699d` (storm presence pack on main)  
**Scope:** A town integrity, C hunt/conflict, B real trade, E atmosphere. Excludes D (God Compiler Phase 8) and F (PIANO re-soak).

---

## A — Town integrity

### Constants (`simulation/sim_engine.py`)

- `STRUCTURE_DECAY_PER_GOODS_TICK`: `0.05` → **`0.025`** (~23.3 h to disrepair, ~33.3 h to ruin)
- `REPAIR_CAMPAIGN_RUIN_RATIO = 0.15`
- `REPAIR_CAMPAIGN_WORKING_FRAC = 0.5`
- `REPAIR_CAMPAIGN_MAX_ASSIGN = 2`
- `REPAIR_CAMPAIGN_GOAL_TTL = STALL_THRESHOLD * 2`
- `REPAIR_CAMPAIGN_CRITICAL_TYPES`: house, market, workshop, foundry, granary, farm_plot
- `RUIN_CULL_AGE_FRAMES = DAY_FRAMES`
- `RUIN_CULL_MIN_PER_CALL = 1`, `RUIN_CULL_MAX_PER_CALL = 3`
- `DISASTER_PROB`: `0.005` → **`0.002`** (legacy branch)
- `DISASTER_DAMAGE`: `(40, 70)` → **`(30, 55)`**
- `STORM_DISASTER_PROB = 0.32` (unchanged)

### Engine behavior

- `_village_repair_pressure()` — fires when ruin ratio ≥ 15% or working fraction < 50%
- `_maybe_repair_campaign()` — assigns up to 2 agents repair goals targeting worst structures
- `_maybe_repair_critical()` — widened to act under village repair pressure (not only at zero working instances)
- `_maybe_cull_ruins()` — deletes 1–3 aged unaffordable ruins; clears `homeStructureId` and `reorgTasks` references
- Repair goal kind `"repair"` routes to `repair_structure`

### God mode (`simulation/server.py`, engine divine kernel)

- New divine command kinds: **`repair_structures`**, **`clear_ruins`**
- `repair_structures`: batch condition restore / un-ruin with scoped targets (all critical, district, explicit ids)
- `clear_ruins`: registry deletion mirroring engine cull (audited in `divine.jsonl`)
- Amended specs/02 invariants: God can now un-ruin and remove structures from registry

### Specs

- `specs/08-systems-economy.md` — decay, campaigns, cull, disaster retune
- `specs/02-engine-core.md` — repair campaign batch, cull, God invariant amendments
- `specs/04-http-api.md` — divine command schema for mass-repair/clear
- `specs/12-ops.md` — operator escape documentation

### Smokes

- **`scripts/town_integrity_smoke.py`** (new) — decay math, campaign assignment, cull eligibility, disaster constants
- **`scripts/god_mode_smoke.py`** — added `repair_structures` and `clear_ruins` preview/apply tests

---

## C — Hunt + conflict

### Constants (`simulation/sim_engine.py`)

- `HUNT_DAMAGE`: `1` → **`2`**
- `HUNT_DAMAGE_HUNTER`: `2` → **`4`**
- `CONFRONT_CONTACT_DIST = 80`
- `CONFRONT_DAMAGE = 10`
- `CONFRONT_INCAP_HEALTH = 1`
- `CONFRONT_LETHAL_THRESHOLD = 15`
- `CONFRONT_FLEE_DIST = 60`
- `CONFRONT_COOLDOWN_FRAMES = STALL_THRESHOLD * 4`
- `CONFRONT_PRESSURE_WINDOW_FRAMES = STALL_THRESHOLD * 2`
- `FORCED_HUNT_GOAL_TTL = STALL_THRESHOLD`

### Engine behavior

- `_village_needed_role()` — stock-aware + wildlife-aware hunter precedence (hunter ahead of unfilled farmer/fisher when prey abundant and meat scarce)
- Ecology scarcity branch registers meat scarcity via village meat totals (not `districtStocks`)
- Forced hunt goals (`kind: "hunt"`) when starving, prey in range, no reachable gatherable edible
- Precedence vs starvation reflex documented: gatherable food wins; hunt only when no edible source reachable
- `confront_agent` in `apply_decision()` — contact-range PvP with rivalry/pressure gating, HP damage, optional edible steal, flee, pair cooldown

### New action (full action-sync)

- **`confront_agent`** — `DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT` (server.py); `apply_decision`, `available_actions` (sim_engine.py); `ACTION_LABELS` (index.html)

### Specs

- `specs/06-agents.md` — hunter specialization context
- `specs/07-actions.md` — `confront_agent` contract
- `specs/08-systems-economy.md` — hunt damage, forced hunt, precedence
- `specs/09-systems-society.md` — PvP social gating

### Smokes

- **`scripts/hunt_conflict_smoke.py`** (new) — damage table, precedence (wildlife-aware), forced hunt, PvP allowed/rejected, action-sync

---

## B — Real trade

### Constants (`simulation/sim_engine.py`)

- `SETTLEMENT_STRUCT_THRESHOLD = 5`
- `SETTLEMENT_POP_THRESHOLD = 6`
- `CARAVAN_CARRY_MIN = 3`
- `CARAVAN_VEHICLE_RESOURCES`: cart, wagon
- `TREATY_TARIFF_MAX = 0.25`

### Engine behavior

- `civilization["settlementStores"][sid]` — per-settlement resource buckets; migrated empty on `restore_state()`
- Local gather overflow credits settlement store; repair/craft draws own store then village stockpile fallback
- `_deliver_caravan()` — debits traveler bundle, credits destination `settlementStores`, applies tariff split to source
- Ocean corridor pathing for caravan goals when transit unlocked (bounded to caravan/transit use)
- `_caravan_trade_bundle()`, `_emit_shipment()`, enriched `caravanLog`

### New action (full action-sync)

- **`deliver_caravan`** — full chain: server.py, sim_engine.py, index.html, prompts.py

### Treaty tariffs

- Enacted `kind: "treaty"` rules accept optional `tariff` (0–0.25); delivery splits bundle between source and destination stores

### Specs

- `specs/10-path1.md` — stores, delivery, water pathing (replaced "no water pathing" non-goal), tariff; fixed `_maybe_caravan_goal` cite
- `specs/08-systems-economy.md` — settlement store semantics
- `specs/07-actions.md` — `deliver_caravan`
- `specs/05-world.md` — ocean corridor movement
- `specs/04-http-api.md` — `/state` settlementStores projection

### Smokes

- **`scripts/path1_smoke.py`** — settlement store isolation, delivery transfer, tariff split, ocean pathing, `deliver_caravan` gating and action-sync

---

## E — Atmosphere

### Calendar retune (`simulation/sim_engine.py`)

- `DAY_FRAMES`: `13500` → **`18000`** (~10.0 min real at 30/s)
- `YEAR_FRAMES`: `324_000` → **`432_000`** (4 real h = exactly 24 day/night cycles)
- `SEASON_FRAMES`: `81_000` → **`108_000`** (~60 min = 6 day/night cycles per season)
- Aging/lifecycle, shelter ticks, season regrowth, and weather storminess all derive from these identities

### Viewer (`simulation/index.html`, `simulation/sprites.js`)

- Stronger dusk/dawn twilight bands and deeper peak night alpha (permanent)
- Per-terrain-kind seasonal palettes; winter snow accents (permanent)
- Redesigned rain/snow/storm particle passes keyed by weather state (permanent)
- Divine Console chrome polish (`#divineBar`, preview/apply hierarchy) — permanent when God mode is on

### Plan docs (new)

- `docs/plan-visual-1-day-night-lighting.md`
- `docs/plan-visual-2-seasonal-terrain-grading.md`
- `docs/plan-visual-atmosphere-systems.md`

### Specs

- `specs/11-viewer.md` — lighting, terrain, particles, God chrome, flag semantics
- `specs/01-architecture.md` — four new flags in flag index
- `specs/02-engine-core.md` — calendar constants and real-time cadence
- `specs/12-ops.md` — visual flag operator notes

---

## Infra / docs / smokes

### New smoke scripts

| Script | Phase | Covers |
|---|---|---|
| `scripts/town_integrity_smoke.py` | A | Decay, campaigns, cull, disaster constants |
| `scripts/hunt_conflict_smoke.py` | C | Precedence, forced hunt, PvP, action-sync |
| `scripts/god_mode_smoke.py` (extended) | A | `repair_structures`, `clear_ruins` |
| `scripts/path1_smoke.py` (extended) | B | Stores, delivery, tariffs, `deliver_caravan` |

### Existing smokes (regression baseline)

- `scripts/sid_parity_smoke.py` — unchanged contract; must stay green
- `scripts/path1_smoke.py` — extended with trade cases

### Handoff / inventory

- `docs/HANDOFF.md` — refreshed for branch state (Phase 5)
- `changes.md` — this file

### Plan artifact

- `.cursor/plans/two_sim_breakthroughs_634bb2dd.plan.md` — delivery plan (A → C → B → E)

### Commit sequence (since `fad699d`)

```
a8aad45 docs(sdd): town integrity decay, cull, disaster, god mass-repair
bc2adab engine: town integrity decay, repair campaigns, cull, disaster, god mass-repair
94652b8 test: town integrity smokes for decay, campaigns, cull, god mass-repair
50ddfe0 docs(sdd): hunt rebalance, forced hunt, damage, bounded PvP
f032df8 engine: hunt rebalance, forced hunt, damage retune, confront_agent
af09b8a test: hunt conflict smokes for precedence, forced hunt, PvP
ad4baba docs(sdd): settlement stores, caravan delivery, water pathing, tariffs
844f88f engine: settlement stores, caravan delivery, water pathing, tariffs
21432ae test: path1 trade smokes for stores, delivery, tariffs, deliver_caravan
dd4a47b docs: plan visual atmosphere pack
4ae6707 engine: calendar stretch + visual atmosphere flags
f184df4 viewer: atmosphere pack lighting, seasonal terrain, weather FX, god chrome
3576656 docs(sdd): atmosphere calendar, visual flags, viewer v2
d72dc0b docs: HANDOFF refresh after four breakthroughs A/C/B/E
b357b50 docs: changes.md for four breakthroughs ACE
```

---

## Excluded (not on this branch)

- **D** — God Compiler Phase 8 (`GOD_COMPILER_ENABLED`, free-prose story compiler)
- **F** — `ALWAYS_ON_MODULES` / PIANO re-soak
