# SPEC 08 — Systems: Economy

Flag semantics for the survival/production/goods/market layer: hunger and
health, crafting, deterministic goal-stepping, structure effects, physical
goods (spoilage/decay/disasters/shelter/vehicles), and the priced market.

**Canonical for:** `SURVIVAL_ENABLED`, `CRAFTING_ENABLED`, `USE_GOALS`,
`STRUCTURE_EFFECTS_ENABLED`, `GOODS_ENABLED`, `ECONOMY_ENABLED` semantics.
**See also:** [01-architecture.md](01-architecture.md) for the complete flag
index; [07-actions.md](07-actions.md) for action params/preconditions;
[05-world.md](05-world.md) for district stocks/ecology feeding scarcity;
[02-engine-core.md](02-engine-core.md) for the tick loop these systems ride.

## SURVIVAL_ENABLED

Runs every tick via `_update_survival(agent)` (sim_engine.py:1837), gated
`SURVIVAL_TICK_FRAMES = 30` (sim_engine.py:244) at the call site.

| Constant | Value | Meaning |
|---|---|---|
| `HUNGER_RATE` | 0.3/tick | passive hunger drain |
| `EAT_THRESHOLD` | 65 | auto-eats a held edible once hunger drops below this |
| `FOOD_RESTORE` | 45 | hunger restored per meal/heal-donation |
| `EDIBLE_RESOURCES` | `["food", "fish", "meat"]` | auto-eat candidates, checked in order |
| `HEALTH_RATE` | 2/tick | health lost while hunger is at 0 |
| `HEALTH_REGEN` | 1.5/tick | health regained while fed (hunger > 0) |
| `COLLAPSE_REGEN` | 0.5/tick | health regen while incapacitated |
| `COLLAPSE_REVIVE_HEALTH` | 15 | health at which a collapsed agent revives |
| `REVIVE_HUNGER` | 35 | hunger floor on revival (else 0-hunger re-collapse in ~8s) |
| `EDIBLE_RESERVE` | 3 | per-agent carry reserve for `EDIBLE_RESOURCES` only — food/fish/meat an agent keeps back from builds/auto-share; not used for village-wide scarcity or Daily Council agenda |
| `SHARE_RADIUS` | 120px | range for the anti-hoarding auto-share backstop |
| `STARVING_HUNGER` | 10 | below this a foodless agent deterministically seeks food |

Sequence each `_update_survival` call: auto-eat if hungry and holding an
edible → `_share_edible_with` backstop if starving (hunger ≤ 0) and holding
nothing (pulls one edible from any non-incapacitated neighbour within
`SHARE_RADIUS` holding more than `EDIBLE_RESERVE`) → hunger drains by
`HUNGER_RATE` → health rises/falls by `HEALTH_REGEN`/`HEALTH_RATE` → health
≤ 0 flips `incapacitated = True` (a *collapse*, distinct from
`LIFECYCLE_ENABLED` permanent death — a corpse, `deathFrame` set, is skipped
entirely). Collapse regen continues even while incapacitated; crossing
`COLLAPSE_REVIVE_HEALTH` clears `incapacitated` and floors hunger at
`REVIVE_HUNGER`.

**Sage emergency:** `_sage_emergency()` (sim_engine.py:1884) returns the
elder (or the healer, if the healer is the one incapacitated) whenever the
living elder is incapacitated or `health < SAGE_CRITICAL_HEALTH`. While a
target is returned, `_sage_responders()` picks the healer (if free) plus the
nearest other agent; the tick loop diverts those agents' think turns to
`_rush_to_heal` (walk within 80px, then apply `heal_agent`) every tick until
resolved, discarding any in-flight LLM decision for that agent so a stale
non-heal choice can never land mid-emergency.

Related actions: `heal_agent` (`HEAL_AMOUNT = 25` base, boosted by
`SKILL_HEAL_BONUS_PER_LEVEL` under `CULTURE_ENABLED`) — see
[07-actions.md](07-actions.md).

## Huntable wildlife yields (`WILDLIFE_ENABLED`)

Fauna population lives in `civilization["wildlife"]` (engine-owned creature
records — see [02-engine-core.md](02-engine-core.md)). It is **not** part of
`districtStocks` and does not deplete ecology gather stocks when a creature
is killed; ecology stage still gates spawn density only
([05-world.md](05-world.md)).

**`meat` good.** A base edible resource (in `BASE_RESOURCES` /
`resourceRegistry` alongside `food`/`fish`). Flows through the same
survival auto-eat, share, spoilage, storage-cap, contribute, trade, and
priced-market paths as the other edibles. `BASE_PRICE` treats `meat` like
`food`/`fish` (base 1). Kill grants never map land game into `food`.

**Kill yield table** (`WILDLIFE_YIELD` / kind pools; +1 unit on kill via
`hunt_wildlife`):

| District pool | Kinds | Yield resource |
|---|---|---|
| forest | `bird`, `squirrel`, `deer`, `fox`, `boar`, `owl` | `meat` |
| farm | `grazer`, `rabbit`, `chicken`, `mouse` | `meat` |
| farm | `butterfly` | none — decorative; not a valid hunt target |
| beach | `fish`, `crab`, `gull`, `turtle`, `seal` | `fish` |

Grant uses the same carry-cap / overflow-to-stockpile split every other
resource-gain path uses. Related action: `hunt_wildlife`
([07-actions.md](07-actions.md)); hunter specialty `meat`
([06-agents.md](06-agents.md)).

## CRAFTING_ENABLED

Adds a recipe registry (`SEED_RECIPES`, sim_engine.py:876) and crafted
resources (`CRAFTED_RESOURCES`, sim_engine.py:871): `planks` (1 wood),
`bricks` (2 stone), `tools` (2 wood + 1 stone) — all `station: "workshop"`.
`INDUSTRY_ENABLED` (path1) extends the registry with charcoal/ingots/
rope/cloth/tool-tier picks at the workshop or kiln (sim_engine.py:1036).

`_craft_item(agent, recipe_id)` (sim_engine.py:4658) gate order: station
built and working (`_craft_station_unlocked`, requires ≥1 working structure
whose function block `unlocks` a `craft` kind for that station) → tech-tier
gate (`TECH_TREE_ENABLED`: recipe `tier` ≤ `_village_tech_tier()`) → has
inputs (else routes to `_craft_input_reflex` and reports the missing
resource) → agent physically at the station zone (else walks there first).
On success: consumes inputs, output = 1 + `_craft_output_bonus` (workshop
count/`WORKSHOPS_PER_CRAFT_BONUS`, capped) + `CULTURE_ENABLED` skill bonus.

Custom recipes: `propose_recipe`/`approve_recipe`/`reject_recipe` mirror the
blueprint flow (no Sage two-stage review). `_validate_recipe`
(sim_engine.py:4728) caps proposals: `MAX_PENDING_BLUEPRINTS` pending slot
shared with blueprints, `MAX_CUSTOM_RECIPES = 12` approved custom recipes,
1–6 inputs each drawn from `resourceRegistry`, id/name format checks,
rejection blacklist (`rejectedRecipeIds`).

## USE_GOALS

Deterministic goal-stepping that runs *between* LLM think calls so routine
multi-tick actions (travel, relocate-and-retry) don't cost a think dispatch
each tick. In the main loop (sim_engine.py:9492): when an agent's think
timer elapses and it already holds a `goal` dict and has no unread inbox
message, `_step_goal(agent)` (sim_engine.py:8450) runs instead of
`_schedule_think`, and `thinkTimer` resets to `GOAL_STEP_FRAMES = 45`
(~1.5s) while the goal continues, or `1` (immediate re-think) once it ends.
Every goal carries a `ttl` that decrements each step; expiry silently clears
the goal (`ttl < 0`) as a deadlock-avoidance backstop.

Goal kinds (`g["kind"]`): `craft_gather` (walk to gather missing craft
inputs), `plant_terrain` (apply `plant_terrain` once), `seek_shelter`
(walk to a district with shelter, `PRESSURE_LOOP_ENABLED`), `dig_relocate`
(walk to a diggable district, then `_dig_terrain` until carry-capped),
`caravan` (walk to the other settlement, `PATH1_DIPLOMACY_ENABLED`), plus
generic `gather`/`deliver`/`build` goals resolved against a target district.
An incoming message always interrupts a goal (falls through to a normal
think that turn) so agents stay responsive to being talked to.

## STRUCTURE_EFFECTS_ENABLED

Every built structure type carries a **function block** (`produces`,
`boosts`, `unlocks`, `stores`, `houses`, `modifies`, and — when
`ENV_EFFECTS_ENABLED` — `shelter`, `light`, `upkeep`) from
`SEED_STRUCTURE_FUNCTIONS`/`PROJECT_TEMPLATES` or a custom blueprint's own
declaration; `_get_structure_function(type_)` (sim_engine.py:2541) resolves
it (empty dict, i.e. no effect, when the flag is off).

**Tick-time (`produces`):** `_tick_structure_effects()` (sim_engine.py:3040)
runs every `EFFECT_TICK_FRAMES = 150` ticks (~5s). Per built type with a
`produces` entry, fires once its own `every_ticks` interval has elapsed
(tracked per `type:resource:scope` key in `civilization["effectLastFire"]`),
depositing `amount * working_structure_count` of the resource — village-wide
or per-district per the entry's `scope`. Seed examples: wall produces 1
stone/1800 ticks; granary 1 food/1200 ticks (`CRAFTING_ENABLED`); forge 1
tools/2400 ticks (`TECH_TREE_ENABLED`).

**Query-time (`boosts`/`unlocks`/`houses`/`stores`):** evaluated on demand,
not ticked. `_gather_yield_bonus` adds gather bonus (farm plots:
`FARM_PLOTS_PER_EXTRA = 4` plots/+1, capped `FARM_YIELD_BONUS_CAP = 2`,
district scope). `_craft_output_bonus`/`_craft_station_unlocked` gate and
boost crafting (workshops: `WORKSHOPS_PER_CRAFT_BONUS = 3`/+1, village
scope, cap 1). `_population_cap` sums `houses` capacity
(`HOUSES_PER_NEW_VILLAGER = 3` houses/+1 cap, hard-floored at
`MAX_ROSTER_SIZE = 20` — see specs/02-engine-core.md — unless
`LIFECYCLE_ENABLED` lifts it further for generated villagers born past that
cap). `_storage_capacity` sums `stores` capacity onto
`BASE_STORAGE_CAPACITY = 25`.

**Environmental effects (`shelter`/`light`/`upkeep`, `ENV_EFFECTS_ENABLED`):**
three additional function-block keys, validated by `validate_function_block`
(server.py) and available to custom blueprints; the engine ignores all three
when the flag is off.

- `shelter: {"capacity": 1-4}` — query-time. Each *working* structure with a
  shelter effect adds `capacity` night-shelter slots, counted by both
  `_tick_shelter()` (hunger penalty, GOODS) and `_tick_night_pressure()`
  (health damage, PRESSURE_LOOP — [10-path1.md](10-path1.md)). Houses are
  unchanged: `houses` still grants `HOUSE_SHELTER_OCCUPANTS = 2` beds
  implicitly; a block declaring both stacks both.
- `light: {"scope": "district"}` (only valid scope) — a working **and
  fueled** light structure marks its district *lit* for the current night.
  Living agents standing in a lit district take no `NIGHT_EXPOSURE_DAMAGE`
  from `_tick_night_pressure()` (the hunger-side `_tick_shelter()` penalty is
  NOT waived — light is warmth, not a bed). Lit district ids are echoed in
  `/state` as `civilization["litDistricts"]` while night lasts (empty by
  day), and working light structures carry `"light": true` in the structures
  payload so the viewer can draw a glow ([11-viewer.md](11-viewer.md)).
- `upkeep: {"resource": <id>, "amount": 1-5}` — nightly fuel. At the first
  night-pressure tick of each day (`frameTick // DAY_FRAMES` changes,
  tracked in `civilization["upkeepLastDay"]` per structure type), each
  working structure whose function declares `upkeep` consumes
  `amount` of `resource` — district stock first, then village stockpile. If
  unaffordable, the structure is **unfueled** until the next day: its
  `light` effect is inactive (other effect keys are unaffected in Phase 1;
  upkeep generalizes in the Civ-1 Phase 4 plan). Fired consumption logs an
  activity line (e.g. "The Hearth burns 1 charcoal through the night").

Seed/migration: the save-time registry migration in `restore_state()` adds
`light: {"scope": "district"}` + `upkeep: {"resource": "charcoal",
"amount": 1}` to the custom registry types `hearth` and `lighthouse` when
present and lacking a light effect. If an older save retains a built or ruined
Hearth/Lighthouse instance but lost its registry entry, restore reconstructs a
minimal registry entry from that instance, so `repair_structure` restores both
the structure and its light behavior.

**Saturation:** `_type_saturated(type_)` (sim_engine.py:3705) flags a
structure type as not worth building more of once its effect is maxed —
houses beyond current cap headroom, farm-boost structures beyond
`every_n * max_bonus * farm_districts`, craft-boost structures beyond
`WORKSHOP_DISTRICT_CAP = 3` per eligible district, walls beyond
`WALL_SOFT_CAP = 10`, anything else beyond `CUSTOM_SOFT_CAP = 5`. Saturated
types are skipped by role defaults, refused by `_start_project_for`, and
count toward the invention gate (see [09](09-systems-society.md)).

Related actions: `build_structure`, `upgrade_structure`, `craft_item`,
`start_project`, `contribute_resources` — [07-actions.md](07-actions.md).

## GOODS_ENABLED

Slow tick `_tick_goods()` (sim_engine.py:3088), gated
`GOODS_TICK_FRAMES = 900` (~30s), runs season bookkeeping + three
sub-systems, all deterministic (no LLM).

| Sub-system | Constants | Behavior |
|---|---|---|
| **Spoilage** | `SPOILAGE_RATIO = 0.25` | `_tick_spoilage`: edible overflow beyond `_storage_capacity` rots at 25% (min 1) per tick — stockpile first, then largest holders, never below `EDIBLE_RESERVE` per agent. Escape: build storage (granary `stores`), eat, or contribute. |
| **Structure decay** | `STRUCTURE_DECAY_PER_GOODS_TICK = 0.05`, `STRUCTURE_DISREPAIR_THRESHOLD = 30`, `REPAIR_CONDITION_RESTORE = 50` | `condition` starts at 100, decays 0.05/tick (~11.7h to disrepair, ~16.7h to full ruin at 0). Below the disrepair threshold a structure stops "working" (no produce/boost/houses/stores); at 0 it becomes a ruin. `repair_structure` restores `REPAIR_CONDITION_RESTORE`; rebuilding a ruin costs half the original needs (min 1 each). `/state` surfaces the raw `condition`/`isRuin` plus server-derived `conditionTier`: `pristine` (>=60), `worn` (>=30 and <60), `crumbling` (<30), or `ruin` (`isRuin` or <=0). |
| **Disasters** | `DISASTER_PROB = 0.005`, `DISASTER_DAMAGE = (40, 70)`, `STORM_DISASTER_PROB = 0.32` | See below — storm-gated when `WEATHER_ENABLED`, legacy random roll otherwise. |
| **Shelter** | `DAY_FRAMES = 13500`, `HOUSE_SHELTER_OCCUPANTS = 2`, `SHELTER_HUNGER_PENALTY = 6`, `SHELTER_HUNGER_FLOOR = 20` | `_tick_shelter()` once per day-frame: each working house shelters up to 2 occupants (homeowners guaranteed their own home under `ECONOMY_ENABLED`, else nearest-first); unsheltered agents lose `SHELTER_HUNGER_PENALTY` hunger, floored at 20 (never into the `STARVING_HUNGER` band). |
| **Seasons** | `YEAR_FRAMES = 324,000`, `SEASON_FRAMES = 81,000` (4 seasons), `SEASON_REGROW_MULT = {spring: 2, summer: 1, autumn: 1, winter: 0}` | Pure function of `frameTick`; multiplies district ecology stock regrowth (winter halts it) — see [05-world.md](05-world.md). |
| **Vehicles/carry** | `CART_CARRY_BONUS = 20` (cart), `WAGON_CARRY_BONUS = 40`/`WAGON_SPEED_MULT = 1.4` (wagon, tier-2, `TECH_TREE_ENABLED`) | `_carry_cap`/`_vehicle_speed_mult` add query-time bonuses on top of `COLLECT_CAP` for the holder. |

Composable-build blocks with `shelter: True` (`wall`, `fence` — see
[10-path1.md](10-path1.md)) also count toward night shelter capacity via
`_composable_shelter_count`.

**Disasters, storm-gated (`WEATHER_ENABLED`, living-ecosystem Phase 4).**
`_maybe_disaster` (sim_engine.py) has two mutually exclusive branches:

- **`WEATHER_ENABLED = False`:** byte-identical to the pre-Phase-4 behavior
  — `DISASTER_PROB = 0.005` chance per goods tick (≈once/100 real min) of
  damage (`DISASTER_DAMAGE = (40, 70)`) to a random structure anywhere.
- **`WEATHER_ENABLED = True`:** damage only fires while
  `civilization["weather"]["state"] == "storm"` (see the weather state
  machine, [05-world.md](05-world.md)), at `STORM_DISASTER_PROB = 0.32` per
  goods tick while storming, preferring a structure inside one of the
  storm's `districts` (falls back to any structure if none qualify there).

Either branch writes the same `activity` line ("DISASTER! A storm tears
through…") and a `disaster`-kind chronicle milestone
(`"A storm damaged/ruined the {name} in {district}."`) — unconditional, no
flag, folded into the `CHRONICLE_MILESTONE_KINDS` set documented in
[09-systems-society.md](09-systems-society.md).

**Rate-calibration arithmetic (the top risk of Phase 4).** Model one
`clear -> gathering -> (storm -> clearing | nothing) -> clear` cycle per
season, with `P_storm = clip(WEATHER_BASE_STORM_CHANCE(0.5) *
WEATHER_SEASON_STORMINESS[season], 0.05, 0.95)` and a season-scaled `clear`
dwell (see 05). Expected cycle length:

```
E[cycle] = E[clear] + E[gathering] + P_storm * (E[storm] + E[clearing])
```

using the midpoints of `WEATHER_DWELL_TICKS` (`E[gathering]=3.5`,
`E[storm]=4`, `E[clearing]=3`). The storm-time fraction of that cycle is
`P_storm * E[storm] / E[cycle]` (the `P_storm` factor on the numerator
matters — only `P_storm` of cycles pass through a storm at all). With the
shipped constants this comes out to **~2.2% (spring), ~0.7% (summer), ~3.1%
(autumn), ~1.9% (winter)** of goods ticks, averaging **~1.97%** across the
four equal-length seasons (equal weighting is valid since `SEASON_FRAMES`
is identical for all four). The naive analytic pick from that number
(`0.005 / 0.0197 ≈ 0.25`) undershot in a 200,000-goods-tick empirical
harness run — a single-seed measurement came in around 0.0040 against a
measured legacy baseline of ~0.0051, likely because the independent-cycle
model doesn't capture correlated timing effects (e.g. transitions
clustering near season boundaries). `STORM_DISASTER_PROB` was therefore
tuned empirically to **0.32**, which lands a 5-seed mean on-rate of
**~0.0051 events/goods-tick** against a 5-seed mean off-rate of **~0.0051
events/goods-tick** (measured via a standalone `SimEngine`, `random.seed`
per run, `WEATHER_ENABLED` toggled, 200,000 goods ticks/seed with damaged
structures repaired between checks so the candidate pool never runs dry) —
i.e. turning weather on does **not** measurably change the long-run damage
rate. See the Phase 4 implementation report for the full seed-by-seed
numbers.

**Critical-structure repair backstop.** `_maybe_repair_critical` (sim_engine.py,
called unconditionally once per RULES_TICK gate, [02-engine-core.md](02-engine-core.md))
is a deterministic escape for when an entire structure category has zero
working instances village-wide — `repair_structure` is reachable by the LLM
and funds itself from the stockpile, but under survival pressure agents
reliably lose the priority contest and never pick it, permanently locking the
category. Table-driven (`_critical_structure_categories`): walks an ordered
list of `(type_, guard, trigger, message)` entries and repairs at most ONE
category per call (so competing emergencies don't drain the same scarce
stockpile in a single tick), using `_repair_backstop_agent` to pick the
nearest living, non-Sage-responding agent who can fund the repair. Categories
covered, in priority order:

| Type | Guard | Trigger |
|---|---|---|
| `house` | `GOODS_ENABLED` | zero working houses |
| `market` | `GOODS_ENABLED`, `ECONOMY_ENABLED`, at least one market built | `not _market_active()` (no pricing-unlock market working) |
| `workshop` | `GOODS_ENABLED`, at least one workshop built | zero working workshops |
| `foundry` | `GOODS_ENABLED`, `path1_on("TIER3_CONTENT_ENABLED")`, at least one foundry built | zero working foundries |
| `granary` | `GOODS_ENABLED`, `CRAFTING_ENABLED`, at least one granary built | zero working granaries |
| `farm_plot` | `GOODS_ENABLED`, at least one farm plot built | zero working farm plots |

Related actions: `repair_structure`, `upgrade_structure`, `craft_item`
(cart/wagon recipes) — [07-actions.md](07-actions.md).

**`structure_health` benchmark.** `_tick_structure_health_benchmark`
(sim_engine.py) logs a `structure_health` benchmark every `GOODS_TICK_FRAMES`
goods tick (same cadence as `_tick_goods`/`_tick_structure_decay`), gated on
`GOODS_ENABLED`, so mass structural decay shows up in `benchmarks.jsonl`
automatically during any soak or test run instead of requiring an ad-hoc
`/state` query to discover it — the same silent-decay blind spot that
motivated the `_maybe_repair_critical` backstop above (a live world once
decayed to 54/66 structures ruined with nobody noticing). Skips logging
entirely if no structures exist yet. The benchmark `value` is the working
fraction (`working / total`, rounded to 2 decimals); `detail` carries the
exact `total`, `working`, `disrepaired` (below `STRUCTURE_DISREPAIR_THRESHOLD`
but not yet a ruin), and `ruined` (`isRuin`) counts so a soak-analysis script
can reconstruct the full picture, not just the ratio.

## ECONOMY_SINKS_ENABLED

`ECONOMY_SINKS_ENABLED` defaults to True. Repairs prefer one plank when
available; tier-2+ projects add one crafted material (planks, then bricks,
then tools); and comfort consumption *opportunistically* drains one `pottery`
or `dried_fish` per living agent every `COMFORT_EVERY_N_GOODS_TICKS = 4`
goods ticks (i.e. every ~2 real minutes) when stock is available — neither id
is seeded with a producer; the sink fires only if an invention supplies them,
and either id may be pruned by the orphan GC (`_maybe_retire_custom_resource`).
Each firing gives a small hunger (+2) and health (+1) benefit, capped at one
unit per agent per firing.

Drain arithmetic (why every 4th tick): a goods tick fires every 30 real
seconds, so per-tick consumption would drain ~1,080 goods/hour at ~9 living
agents — a ~15k comfort backlog gone in ~14 real hours. Sampling every 4th
tick gives ~270/hour ≈ 2.3 real days for the same backlog, matching the
Civ-1 plan's "saturated stockpiles drain over ~2-3 real-time days" target
while production continues underneath.

## ECONOMY_ENABLED

Activates once a market structure exists and is working
(`_market_active()` — any built type whose function block `unlocks` a
`pricing` kind).

**Pricing** (`_resource_price`): `base * multiplier`,
no persisted state. `base` from `BASE_PRICE` (food/fish/meat/water/wood/herbs=1,
stone/planks/bricks=2, tools=3, cart=4, wagon=6; gold and coin are always 1
— a currency never prices itself, whichever one is the active trade medium).
`multiplier = 1 + (1 - scarcity) * (PRICE_SCARCITY_MULT - 1)`,
`PRICE_SCARCITY_MULT = 4.0`, floored at `PRICE_MIN = 1`. `scarcity`
(1.0 = comfortable, 0.0 = depleted) is the minimum of up to two signals:
average district-stock ratio (`ECOLOGY_ENABLED`) and village stockpile
depth vs. `_storage_capacity` (`GOODS_ENABLED`, edibles only) — either
signal alone can move price; both compound; if neither applies, scarcity
is 1.0 (base price).

**Relationship modifiers** (`_priced_trade_terms`):
ally = `ALLY_PRICE_DISCOUNT = 0.75`×, rival = `RIVAL_PRICE_SURCHARGE =
1.5`×, from the *seller's* opinion of the buyer. A rival trade the buyer
can't afford even at the surcharge is refused outright (inventories
untouched); an ally/neutral trade the buyer can't afford falls back to a
1-for-1 barter swap instead of blocking. `trade_resource` with no market
active is always the flag-off barter swap.

**Property:** `_claim_home`/`_maybe_auto_claim_home` —
first agent to build or repair-from-ruin a house claims it (`homeOf` on the
structure, `homeStructureId` on the agent; one home at a time, claiming a
new one releases the old). Homeowners get the nightly shelter benefit in
their own house regardless of proximity. `HOMELESS_NUDGE_FRAMES` (~10 min)
periodically nudges a homeless agent's prompt.

**Wealth:** `_agent_wealth(agent)` = held gold + held coin + goods valued
at current prices. Gold/coin always count (a currency is always valuable);
goods only count once a market exists (0 signal otherwise — nothing
tradeable-priced yet). Used in benchmark/prompt wealth signals.

### Mint / coin (currency, distinct from gold)

`coin` is a separate resource from `gold`, seeded into `resourceRegistry`
whenever `ECONOMY_ENABLED` (`BASE_RESOURCES["coin"]`, no `gatherZone` — it
can never be foraged or mined, only minted). `gold` is unchanged: it stays a
minable commodity and structure-cost input forever, and remains the
fallback trade currency for as long as no mint exists.

**Mint structure:** `PROJECT_TEMPLATES["mint"]` (needs `stone: 3, gold: 3`),
gated `ECONOMY_ENABLED`, same plain tier-1/any-village-district buildability
as market/library/cemetery. Its function block unlocks a new kind,
`"currency"`, mirroring how `market` unlocks `"pricing"`.

**`_mint_active()`** mirrors `_market_active()` exactly: true while at least
one WORKING mint's function unlocks `kind == "currency"`.

**Minting** (`_maybe_mint_coin`): a deterministic backstop in the same
`RULES_TICK_FRAMES` (150-frame / ~5s) unconditional batch as
`_maybe_repair_critical` etc. While a mint is working, converts up to
`MINT_RATE = 1` gold from the **village stockpile** (not agents' held gold)
into that many coin, each call — slow and small by design so a fresh mint
can't drain an entire treasury in one tick. No agent/LLM action is involved;
minting is infrastructure, the same way `_market_active` itself needs no
action.

**`_active_currency()`**: returns `"coin"` once `_mint_active()`, else
`"gold"` (the pre-mint, byte-identical default). Consulted only from
`_priced_trade`, which is itself already gated on `_market_active()` at its
call site (`apply_decision`'s `trade_resource` handler) — this only decides
*which* resource settles the price once **both** a market and a mint exist.
`_priced_trade` reads/writes the active currency on both sides of the trade
instead of a hardcoded `"gold"` key; refusal/barter-fallback messages report
whichever currency is active.

**Treasury tax collection** (`_enforce_resource_tax`): once a mint exists
and the taxed agent holds coin, `resource_tax` collects coin from the
agent's balance into the village coin stockpile *instead of* the
just-gathered/contributed resource (never both, so a single tax event can't
double-charge). Falls back to the original per-resource tax whenever the
agent holds no coin — and is a complete no-op behavior change with no mint
built, keeping `test_priority_and_repeal`/`test_repeal_backstop_age_gate`
unaffected.

**Spending the treasury** (`_maybe_fund_project_coin`): another
`RULES_TICK_FRAMES`-batch backstop — once a mint exists, the village coin
stockpile tops up any active district project's `needs["coin"]` directly, no
elder-only gate (coin is spendable like any other stockpiled resource once
minted; the elder's role in provisioning the treasury is upstream, via the
`resource_tax` rule they enact). A no-op today since no seed project needs
coin yet — the mechanism is ready the moment one does.

Related actions: `trade_resource` — [07-actions.md](07-actions.md).

## CARAVAN_VISUALS_ENABLED

`CARAVAN_VISUALS_ENABLED` defaults to True. Living-ecosystem Phase 3
("goods in motion"): a short-lived, **purely cosmetic** in-flight shipment
record, emitted so the viewer has something to animate along the road
graph when goods move between districts.

**Hard constraint — shipments never gate the economy.** `_emit_shipment`
is called strictly *after* the authoritative transfer has already mutated
agent inventories / district-project `contributed` / the village stockpile
— trade, contributions, and market settlement remain exactly as
instantaneous as they were before this flag existed. A shipment is an
animation receipt of a transfer that already happened, never a precondition
for one. If any code path were made to wait on a shipment, that would be a
regression of this invariant.

**Emission sites** (all existing transfer paths — no new tick, no new LLM
call):
- `apply_decision`'s `trade_resource` barter branch (agent → agent, no
  market).
- `_priced_trade`'s two success paths (the barter-fallback when the buyer
  is short on the active currency, and the paid purchase) — the
  `ECONOMY_ENABLED` market-settlement path.
- `_try_contribute_resource` — an agent contributing carried resources to a
  district project, which can target a district other than the one the
  agent currently stands in (`_resolve_contribution_district`'s
  most-stalled-district fallback), i.e. a genuine stockpile transfer
  between districts.

**Shape**: `{id, fromDistrict, toDistrict, resource, path, startFrame,
endFrame, mode}`. `path` is the list of `{x, y}` road-graph waypoints
between the two districts' `entryNode`s, resolved once at emission time by
`_road_path_between_districts` (same `ROAD_PATH_CACHE` agent travel already
populates via `_recompute_road_paths` — no second pathfinder). `mode` is
`"boat"` when the shipment crosses a settlement boundary
(`districts[*].settlementId`, `PATH1_DIPLOMACY_ENABLED`) and
`_has_ocean_transit()` is true (reusing the existing ocean-caravan
foothold — `_has_ocean_transit`/`_consume_ocean_transit`,
[10-path1.md](10-path1.md)), else `"cart"`.

**Skips cleanly, never fabricates a straight line**: `_emit_shipment` is a
no-op when `fromDistrict == toDistrict`, when either district or its
`entryNode` is missing, or when no cached road path connects them (fewer
than 2 resolved waypoints). The economic transfer itself is unaffected
either way — only the cosmetic receipt is skipped.

**Bounded, not persisted**: shipments live in `self.shipments` on the
engine instance — deliberately **not** part of `self.civilization`, so they
are never written to `state.db` and simply vanish (harmlessly) across a
restore/restart. `_emit_shipment` caps the list at `SHIPMENT_RING_CAP = 8`
(oldest dropped first); `_prune_shipments` (called from the existing
`_tick_goods` `GOODS_TICK_FRAMES` cadence — no new timer) additionally
drops any shipment whose `endFrame` has passed. `SHIPMENT_TRAVEL_FRAMES =
240` (~8s at 30 ticks/s) sets how long a shipment animates.

**`/state` exposure**: `snapshot["shipments"]` (only still-live records,
via `_shipment_snapshot`) is present only when the flag is on; the flag
itself is echoed in `config.flags.CARAVAN_VISUALS_ENABLED`. Off means the
key is simply absent and the viewer draws nothing extra — the moored
`physicalProps` boats (`TRANSIT_ENABLED`, [11](11-viewer.md)) are entirely
unaffected by this flag either way.

## Sovereign God mode: `grant_resource` semantics (Phase 4)

`{"kind": "grant_resource", "payload": {"resourceId": str, "amount": int,
"target": "stockpile" | {"agentId": int}?}}` — full command/reversibility
details in
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles);
this section documents the resource-transfer contract specifically.

**Known-resource gate.** `resourceId` must already exist as a key in the live
`civilization["resourceRegistry"]` — the same registry `_perform_gather`,
crafting, invention, and cognition's `known_resource_ids` all read from
(seeded from `BASE_RESOURCES`/`CRAFTED_RESOURCES` plus any invented custom
resources). There is no separate "known resource" allowlist for God mode; a
`resourceId` that has never been seeded or invented is rejected as "unknown
resource id" before any other check. A retired custom resource id fails this
gate until it is re-invented.

**Caps.** `amount` is a positive integer, capped per command at
`GOD_GRANT_PER_COMMAND_CAP = 200`. A second, cumulative cap,
`GOD_GRANT_SESSION_CAP = 2000`, bounds the running total of every
`grant_resource` command *applied* (not merely previewed) since process
start, tracked by `self._god_grant_session_total` — in-memory only, like
`self._god_preview_cache`/`self._god_requests`, reset by `reset()` and never
written to `state.db`. A command whose `amount` would push the running total
past the session cap is rejected at validation time (both at preview and at
apply-time revalidation), applying nothing.

**Storage/carry semantics, preserved explicitly.** `target` omitted or the
literal string `"stockpile"` adds `amount` straight to
`civilization["stockpile"][resourceId]` — identical to how tax collection,
rationing, and every other stockpile-crediting path already writes it.
`target: {"agentId": int}` must resolve to a living agent, and the grant is
split through the exact same two sinks `_perform_gather`'s carry-cap clamp
and every other gain-resource path already use, via `_carry_cap(agent)`:

```text
cap    = _carry_cap(agent)                       # COLLECT_CAP (+cart/wagon bonus)
held   = agent["resources"].get(resourceId, 0)
room   = max(0, cap - held)
agent_added     = min(amount, room)               # fills the agent's carry room first
stockpile_added = amount - agent_added            # remainder routes to the village stockpile
```

This is the plan's chosen resolution for "if a grant would exceed carry
capacity, either clamp to capacity or route the remainder to the
stockpile": the remainder is **routed to the stockpile**, never clamped away
and lost, and never a third bypass sink distinct from the two every ordinary
resource-gain path already writes to. `god_preview()`'s `previewOutcome` for
this kind reports `agentAdded`/`stockpileAdded` computed against the agent's
*current* held amount, so the operator sees the exact split before
committing to apply.

## Sovereign God mode: timed lawgiver modifiers — the arithmetic contract (Phase 5)

Seven allowlisted `float` keys, each read through one helper —
`_divine_modifier(key, default=1.0)` — at the exact existing calculation
site each already had, in the exact existing order, before the existing
clamp. With no active effect for a key (flag off, or flag on with nothing
occupying that key) the helper returns exactly `1.0`, so multiplying by it
is always safe to leave in the code path unconditionally: `1.0 * x == x`
(for the `int`/`floor` cases below, `math.floor(x * 1.0) == x` for any `x`
already an `int`), which is *why* an untouched or all-`1.0` run is
byte-identical to the feature-off baseline rather than merely similar to it.
See [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-5--storyteller-events-and-timed-lawgiver-modifiers)
for the `activeEvents`/`story_event` state this reads and the
one-value-per-key composition rule.

**Gathering (`_perform_gather`).** The existing sequence: base `amount`,
then `+= self._custom_rule_modifier("collect_resource", ...)` (an
agent-authored village-law addition), then an ecology-scale
`max(1, int(amount * scale))`, then a grove-ratio `max(1, int(amount *
grove_mult))`, then a carry-cap clamp `max(1, min(amount, cap_room))`, then
`collectSuccesses += 1` and the tool benchmark. The divine step is inserted
**after** the grove-ratio line and **before** the carry-cap clamp:

```text
mult   = fish_yield_multiplier if resource == "fish" and active
         else gather_yield_multiplier
amount = floor(amount * mult)
if amount <= 0:
    return early — before the carry-cap clamp
```

**Ordering is load-bearing, not stylistic.** The carry-cap clamp is `max(1,
min(...))` — applying the divine multiplier *after* that clamp would
resurrect a `0.0`/near-zero result back to `1`, silently defeating the
headline "a divine famine nulls the harvest" case. Multiplying *before* the
clamp and returning early on a non-positive result is the only ordering that
lets a divine multiplier actually produce zero.

**The zero-path is a full early return**, not merely skipping the resource
add. It happens *before*: the resource being added to `agent["resources"]`,
`c["collectSuccesses"] += 1`, `self._path1_tool_benchmark(resource, True)`,
ecology stock depletion (`_deplete_district_stock`), harvest-quota recording
(`_record_harvest_quota_use`), and gather-skill practice (`_practice_skill`).
It still sets `agent["lastGatherRejection"]` (the same field every other
gather-rejection branch sets) and returns a "found nothing" narration in the
same voice as its neighbors, so a divinely-nulled gather is indistinguishable
from any other legitimate rejection in the agent's own eyes — and, crucially,
never inflates `collectSuccesses` or the tool-tier benchmark, which is
exactly the evidence stream God-mode's `intervened` marker exists to keep
honest.

**Fish precedence.** `fish_yield_multiplier` *replaces* — never multiplies
with — `gather_yield_multiplier` for `resource == "fish"`. A fish gather
reads only `_divine_modifier("fish_yield_multiplier")`; it never falls back
to or combines with the general key, even when only the general key is
active. `gather_yield_multiplier`'s range is `0.25..3.0` (it can never reach
`0.0` on its own — a base `amount == 1` gather already floors to `0` at its
own minimum, `floor(1 * 0.25) == 0`, so the zero-path is still reachable
through the general key at its floor); `fish_yield_multiplier`'s range is
`0.0..3.0`, reaching true zero directly.

**Divine law scales village law, not the other way around.** The divine
multiplier is applied *after* `_custom_rule_modifier`'s additive village-law
bonus (an agent-authored governance effect, e.g. a "Wood Charter" custom
rule), not instead of it — a divine famine suppresses a village's own
harvest bonus too, which is the intended default. `god_preview()`'s
`previewOutcome` for a `story_event` naming `gather_yield_multiplier` or
`fish_yield_multiplier` adds a `customRuleContext` list (one `{ruleId,
subject, value}` entry per currently-enacted village rule that modifies
`collect_resource`) alongside the proposed `modifiers`, so an operator sees
both contributions **named separately** — the divine value is never merged
into or replacing the custom-rule value in the preview response, only in the
live arithmetic where composition is genuinely intended.

**Hunger drain (`_update_survival`).** `agent["hunger"] = max(0,
agent["hunger"] - HUNGER_RATE * _divine_modifier("hunger_drain_multiplier"))`
— the multiplier scales the delta *before* the existing `max(0, ...)` clamp.
`0.0` suppresses drain entirely for that tick without touching any other
survival effect.

**Starvation damage (`_update_survival`, the `hunger <= 0` branch).**
`agent["health"] = max(0, agent["health"] - HEALTH_RATE *
_divine_modifier("starvation_damage_multiplier"))` — same before-the-clamp
ordering. New relative to the vitals-miracle catalog: this is the only knob
that lets a divine effect reduce *ongoing* starvation damage rather than
granting a one-off vitals bump, which is what makes a "Merciful Rain" story
template mechanically possible.

**Fed regen (`_update_survival`, the `hunger > 0`, not-incapacitated
branch).** `agent["health"] = min(100, agent["health"] + HEALTH_REGEN *
_divine_modifier("health_regen_multiplier"))` — same before-the-clamp
ordering. This is the **only** consumer site `health_regen_multiplier`
reaches.

**`COLLAPSE_REGEN` is deliberately excluded from divine scaling.** The
`agent["incapacitated"]` branch — `agent["health"] = min(100,
agent["health"] + COLLAPSE_REGEN)` — is left completely untouched by
`health_regen_multiplier` or any other divine modifier. `COLLAPSE_REGEN` is
what lets an incapacitated agent climb back to `COLLAPSE_REVIVE_HEALTH = 15`;
if a `0.0` `health_regen_multiplier` reached this line, a collapsed agent
would be permanently stranded below the revive threshold with no
deterministic escape. This is the one line in the entire seven-key catalog
where "scale everything uniformly" was rejected on purpose — the smoke suite
proves a collapsed agent still recovers under an active `0.0`
`health_regen_multiplier`.

**Structure decay (`_tick_structure_decay`).** The passive per-goods-tick
delta is scaled before being handed to the shared helper: `decay =
STRUCTURE_DECAY_PER_GOODS_TICK * _divine_modifier("structure_decay_multiplier")`,
then `self._apply_structure_condition_delta(s, -decay)` for every
non-ruined structure — the identical helper the `structure_condition`
miracle (Phase 4) and direct disaster damage both call with their own
deltas. Only *this* passive-decay call site is scaled; a miracle's own
`delta` and any disaster-damage delta are untouched by
`structure_decay_multiplier`.

**Spoilage (`_tick_spoilage`).** The existing computation — `base_spoil =
max(1, int(overflow * SPOILAGE_RATIO))` (the floor-of-1 guarantee that
normal spoilage always removes at least one unit once there is any overflow
at all) — is now followed by `to_spoil = min(overflow, floor(base_spoil *
_divine_modifier("spoilage_multiplier")))`. The divine multiplier is applied
to `base_spoil` **before** the existing `min(overflow, ...)` bound is
re-applied, so spoilage can never remove more than the eligible overflow
regardless of how large the multiplier is (`3.0` max), and an active `0.0`
overrides the normal floor-of-1 guarantee entirely — no spoilage happens
that tick.

**Identity.** For every site above, an effective `1.0` — flag off, or flag
on with nothing occupying that key — executes the identical multiplication
and produces the identical clamp result as the feature-off baseline; no
site special-cases "is God mode on" to skip the multiplication, because
`_divine_modifier`'s own default already makes doing so unnecessary. The
smoke suite proves this for every site (gather, survival, structure decay,
spoilage) by running identical operations against two independently
constructed engines — one with the flag off, one with the flag on and every
key pinned to `1.0` — and asserting the observable results match exactly.
