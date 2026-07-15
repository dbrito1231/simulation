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
| `EDIBLE_RESOURCES` | `["food", "fish"]` | auto-eat candidates, checked in order |
| `HEALTH_RATE` | 2/tick | health lost while hunger is at 0 |
| `HEALTH_REGEN` | 1.5/tick | health regained while fed (hunger > 0) |
| `COLLAPSE_REGEN` | 0.5/tick | health regen while incapacitated |
| `COLLAPSE_REVIVE_HEALTH` | 15 | health at which a collapsed agent revives |
| `REVIVE_HUNGER` | 35 | hunger floor on revival (else 0-hunger re-collapse in ~8s) |
| `EDIBLE_RESERVE` | 3 | food/fish an agent keeps back from builds/auto-share |
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
`boosts`, `unlocks`, `stores`, `houses`, `modifies`) from
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
`len(AGENT_DEFS)` unless `LIFECYCLE_ENABLED` lifts it for generated
villagers). `_storage_capacity` sums `stores` capacity onto
`BASE_STORAGE_CAPACITY = 25`.

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
| **Structure decay** | `STRUCTURE_DECAY_PER_GOODS_TICK = 0.05`, `STRUCTURE_DISREPAIR_THRESHOLD = 30`, `REPAIR_CONDITION_RESTORE = 50` | `condition` starts at 100, decays 0.05/tick (~11.7h to disrepair, ~16.7h to full ruin at 0). Below the disrepair threshold a structure stops "working" (no produce/boost/houses/stores); at 0 it becomes a ruin. `repair_structure` restores `REPAIR_CONDITION_RESTORE`; rebuilding a ruin costs half the original needs (min 1 each). |
| **Disasters** | `DISASTER_PROB = 0.005`, `DISASTER_DAMAGE = (40, 70)` | ~0.5% chance per goods tick (≈once/100 real min) of random structure damage in that range. |
| **Shelter** | `DAY_FRAMES = 13500`, `HOUSE_SHELTER_OCCUPANTS = 2`, `SHELTER_HUNGER_PENALTY = 6`, `SHELTER_HUNGER_FLOOR = 20` | `_tick_shelter()` once per day-frame: each working house shelters up to 2 occupants (homeowners guaranteed their own home under `ECONOMY_ENABLED`, else nearest-first); unsheltered agents lose `SHELTER_HUNGER_PENALTY` hunger, floored at 20 (never into the `STARVING_HUNGER` band). |
| **Seasons** | `YEAR_FRAMES = 324,000`, `SEASON_FRAMES = 81,000` (4 seasons), `SEASON_REGROW_MULT = {spring: 2, summer: 1, autumn: 1, winter: 0}` | Pure function of `frameTick`; multiplies district ecology stock regrowth (winter halts it) — see [05-world.md](05-world.md). |
| **Vehicles/carry** | `CART_CARRY_BONUS = 20` (cart), `WAGON_CARRY_BONUS = 40`/`WAGON_SPEED_MULT = 1.4` (wagon, tier-2, `TECH_TREE_ENABLED`) | `_carry_cap`/`_vehicle_speed_mult` add query-time bonuses on top of `COLLECT_CAP` for the holder. |

Composable-build blocks with `shelter: True` (`wall`, `fence` — see
[10-path1.md](10-path1.md)) also count toward night shelter capacity via
`_composable_shelter_count`.

Related actions: `repair_structure`, `upgrade_structure`, `craft_item`
(cart/wagon recipes) — [07-actions.md](07-actions.md).

## ECONOMY_ENABLED

Activates once a market structure exists and is working
(`_market_active()`, sim_engine.py:2835 — any built type whose function
block `unlocks` a `pricing` kind).

**Pricing** (`_resource_price`, sim_engine.py:2847): `base * multiplier`,
no persisted state. `base` from `BASE_PRICE` (food/fish/water/wood/herbs=1,
stone/planks/bricks=2, tools=3, cart=4, wagon=6; gold is always 1).
`multiplier = 1 + (1 - scarcity) * (PRICE_SCARCITY_MULT - 1)`,
`PRICE_SCARCITY_MULT = 4.0`, floored at `PRICE_MIN = 1`. `scarcity`
(1.0 = comfortable, 0.0 = depleted) is the minimum of up to two signals:
average district-stock ratio (`ECOLOGY_ENABLED`) and village stockpile
depth vs. `_storage_capacity` (`GOODS_ENABLED`, edibles only) — either
signal alone can move price; both compound; if neither applies, scarcity
is 1.0 (base price).

**Relationship modifiers** (`_priced_trade_terms`, sim_engine.py:2894):
ally = `ALLY_PRICE_DISCOUNT = 0.75`×, rival = `RIVAL_PRICE_SURCHARGE =
1.5`×, from the *seller's* opinion of the buyer. A rival trade the buyer
can't afford even at the surcharge is refused outright (inventories
untouched); an ally/neutral trade the buyer can't afford falls back to a
1-for-1 barter swap instead of blocking. `trade_resource` with no market
active is always the flag-off barter swap.

**Property:** `_claim_home`/`_maybe_auto_claim_home` (sim_engine.py:2961) —
first agent to build or repair-from-ruin a house claims it (`homeOf` on the
structure, `homeStructureId` on the agent; one home at a time, claiming a
new one releases the old). Homeowners get the nightly shelter benefit in
their own house regardless of proximity. `HOMELESS_NUDGE_FRAMES` (~10 min)
periodically nudges a homeless agent's prompt.

**Wealth:** `_agent_wealth(agent)` (sim_engine.py:2989) = held gold + goods
valued at current prices; returns 0 when no market exists (goods aren't
tradeable-priced yet). Used in benchmark/prompt wealth signals.

Related actions: `trade_resource` — [07-actions.md](07-actions.md).
