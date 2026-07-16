# SPEC 10 — Path 1: Minecraft-Like World Depth

The `PATH1_ENABLED` bundle: industry (ores/smelting), tool tiers, composable
blocks, diggable terrain tiles, a second-settlement diplomacy layer, tier-3
content, and a day/night pressure loop.

**Canonical for:** `PATH1_ENABLED`/`path1_on()` semantics,
`INDUSTRY_ENABLED`, `TOOL_TIERS_ENABLED`, `COMPOSABLE_BUILD_ENABLED`,
`TERRAIN_TILES_ENABLED`, `PATH1_DIPLOMACY_ENABLED`, `TIER3_CONTENT_ENABLED`,
`PRESSURE_LOOP_ENABLED` semantics.
**See also:** [01-architecture.md](01-architecture.md) for the flag index;
[05-world.md](05-world.md) for district/terrain geometry (`TILE_CELL`
cross-link); [02-engine-core.md](02-engine-core.md) for day/night/season
constants; [07-actions.md](07-actions.md) for action params;
[08-systems-economy.md](08-systems-economy.md) for crafting/goods this
bundle extends.

## `PATH1_ENABLED` and `path1_on()`

`PATH1_ENABLED = True` is the master bundle switch (sim_engine.py:725);
`path1_on(subflag=None)` (sim_engine.py:735) returns `True` unconditionally
when `PATH1_ENABLED` is set, else falls back to the named sub-flag's own
value. All seven sub-flags (`INDUSTRY_ENABLED`, `TOOL_TIERS_ENABLED`,
`COMPOSABLE_BUILD_ENABLED`, `TERRAIN_TILES_ENABLED`,
`PATH1_DIPLOMACY_ENABLED`, `TIER3_CONTENT_ENABLED`, `PRESSURE_LOOP_ENABLED`)
default `True`. Every call site in this bundle gates through `path1_on(...)`
rather than reading the sub-flag global directly, so flipping the master
flag off disables the whole bundle regardless of sub-flag state.

## INDUSTRY_ENABLED

Extends `BASE_RESOURCES` with clay/sand (beach) and copper/iron ore (cave),
and `CRAFTED_RESOURCES` with charcoal, copper/iron ingots, rope, cloth, and
three tool tiers (sim_engine.py:1017-1047). New workshop recipes: charcoal
(2 wood), copper/iron ingot (1 ore + 1 charcoal), rope (1 wood), cloth
(2 herbs), wooden/stone/iron pick. Adds the **Kiln** structure (needs 3
stone + 2 wood, tier 1): unlocks `craft` at station `kiln`, produces 1
charcoal per 1800 ticks per district. `_path1_industry_benchmark()` samples
industry throughput on the slow tick alongside the other `_maybe_*`
backstops.

`TIER3_CONTENT_ENABLED` (below) layers Harbor/Mill/Foundry on top of this
registry.

## TOOL_TIERS_ENABLED

Gates certain gathers on a held tool. `TOOL_TIER_ORDER = ("wooden_pick",
"stone_pick", "iron_pick")`, `TOOL_TIER_LEVEL` = 1/2/3.
`RESOURCE_MIN_TOOL` = `{"stone": "wooden_pick", "copper_ore": "stone_pick",
"iron_ore": "iron_pick"}`. `_can_gather_resource` (sim_engine.py:3898)
compares `_gather_tool_tier(agent)` (highest-tier pick currently held)
against the resource's requirement; below it, the gather is refused with a
named reason ("`<resource>` needs a `<tool>` (you have tier `<n>` tools)").
`TOOL_YIELD_BONUS = 1` — holding the *exact* required tier (not just
meeting the minimum) adds a small yield bonus on top of `_gather_yield_bonus`.

When `TOOL_TIERS_ENABLED` and `TERRAIN_TILES_ENABLED` are both on,
`_pickless_stone_route` (sim_engine.py:3971) reroutes a stone-seeker without
a pick to dig terrain directly (stone's nominal cave gather zone has no
diggable ground) instead of bouncing between cave and farm forever — the
bootstrap escape for a fresh world with no Workshop yet (digging itself is
deliberately tool-free).

## COMPOSABLE_BUILD_ENABLED

Free-form single-tile placement on a per-district 8×8 grid
(`PATH1_GRID_COLS = PATH1_GRID_ROWS = 8`, cell size `TILE_CELL = 40` —
geometry detail cross-linked from [05-world.md](05-world.md)).
`BLOCK_TYPES` (sim_engine.py:1001): `wall` (1 wood, shelter), `floor`
(1 wood), `door` (2 wood), `fence` (1 wood, shelter).

`place_block` (`_place_block`, sim_engine.py:4011): resolves target cell
(explicit `gx,gy` or the agent's current cell via `_pos_to_grid`), rejects
on unknown block type, no district, the district's `TILE_CAP_PER_DISTRICT
= 200` reached, the target cell already occupied, or insufficient
resources for the block's cost (each rejection sets `lastBlockRejection`
with a reason, read by the next prompt). On success, deducts cost, stores
the block in `district["tiles"][gx,gy]`, logs a `composable_placements`
benchmark. `remove_block` (`_remove_block`, sim_engine.py:4049) clears a
tile; `BLOCK_REFUND_RATIO = 0.5` refunds half the placement cost.
Shelter-flagged blocks (`wall`/`fence`) count toward night shelter capacity
via `_composable_shelter_count` (see [08](08-systems-economy.md)).

## TERRAIN_TILES_ENABLED

Each district lazily gets a per-cell terrain grid
(`_ensure_district_terrain`, sim_engine.py:4001) over the same 8×8 grid,
defaulting by district kind: forest→grove, farm→soil, beach→sand,
cave→rock, ocean→water, else soil. `TERRAIN_TYPES = ("soil", "rock",
"grove", "water")`. `NON_DIGGABLE_DISTRICT_KINDS = {"forest", "beach",
"cave", "ocean"}` — these kinds' grids never contain soil, so
`dig_terrain` there always fails or relocates.

`dig_terrain` (`_dig_terrain`, sim_engine.py:4082): grove→soil (clears a
grove tile, no yield); soil→rock (yields 1 stone up to carry cap); any
other current terrain (already rock/sand/water) is exhausted — the agent is
routed to the nearest fresh soil tile in the same district
(`_find_nearby_terrain`) or, if none exists district-wide, to the nearest
other diggable district (`_nearest_diggable_district`, nearest by
district-center distance when an agent is given), setting a `dig_relocate`
goal (`USE_GOALS`) so the trip completes deterministically rather than
re-deciding every LLM think. Successful digs log a `terrain_mutations`
benchmark.

`plant_terrain` (`_plant_terrain`, sim_engine.py:4150): costs 1 wood,
converts the agent's current tile toward `grove` (farm districts use this
to counteract dig-driven grove loss; `_maybe_expand_field` auto-assigns a
`plant_terrain` goal when a farm district's grove ratio drops below 0.3).

## PATH1_DIPLOMACY_ENABLED

`_init_settlements()` (sim_engine.py:4198) seeds a single `"home"`
settlement owning every starter district. `_maybe_found_settlement()`
(tick-gated backstop) founds a second settlement — `"outpost"`, on a
claimed frontier plot — once `structures ≥ SETTLEMENT_STRUCT_THRESHOLD = 5`
(non-ruin) and `living ≥ SETTLEMENT_POP_THRESHOLD = 6`; caps at 2
settlements total.

**Treaties:** `RULE_KINDS` gains `"treaty"` under this flag (see
[09-systems-society.md](09-systems-society.md) for the shared propose/vote
scaffold). `propose_treaty`/`vote_treaty`
(`_propose_treaty`/`_vote_treaty`, sim_engine.py:4282) reuse the rules
`pendingRules`/`_tally_and_maybe_enact` machinery directly — a treaty is a
rule with `kind: "treaty"`, requiring `id`/`name` on the proposal.

**Caravans:** `_maybe_caravan_goal` (sim_engine.py:4256) — an agent holding
a cart/wagon (raising `_carry_cap`) and at least `CARAVAN_CARRY_MIN = 3`
total resources, once a second settlement exists, is assigned a `caravan`
goal (`USE_GOALS`) to walk to the other settlement's first district; on
arrival it logs to `civilization["caravanLog"]` and an
`inter_village_trades` benchmark. `_border_settlement_agent` flags an agent
within 150px of an agent from a different settlement (used for
diplomacy-flavored prompt lines).

## TRANSIT_ENABLED

`TRANSIT_ENABLED` defaults to True and requires diplomacy. The `transit`
unlock has the shape `{"kind":"transit","terrain":"ocean",
"consumes":{"boat":1}}`; `terrain` is currently limited to `ocean` and all
consumed resource ids must be known positive quantities. A working transit
structure permits ocean-zone gathering and consumes its cost when an abstract
caravan arrives at another settlement. Agents retain ordinary district/road
movement: this does not add water pathing or vehicle entities.

Save migration (`restore_state()`): the `dock` and `shipyard` types gain a
`{"kind":"transit","terrain":"ocean","consumes":{"boat":1}}` unlock when
missing. Like the hearth/lighthouse light migration, when the registry entry
itself is gone (the approved-custom registry caps at 15 and retires old
entries) but a structure instance of the type still stands, a minimal
registry entry is recreated from the instance so old saves regain transit —
otherwise the migration would silently no-op on exactly the saves that need
it. Idempotent.

## TIER3_CONTENT_ENABLED

Layered on top of `INDUSTRY_ENABLED` (sim_engine.py:1058): three tier-2/3
structures — **Harbor** (beach district, tier 2: produces +1 fish/1500
ticks/district, boosts fish gather up to +2), **Mill** (village, tier 2:
boosts edible gather up to +2/district), **Foundry** (village, tier 3:
unlocks tier-3 `craft`, produces 1 iron ingot/2400 ticks village-wide).
Extends `ERA_LADDER` with Harbor Era and Mill Era
(`TECH_TREE_ENABLED`) — see [09](09-systems-society.md).

## PRESSURE_LOOP_ENABLED

**Night exposure:** `_tick_night_pressure` (sim_engine.py:4327) runs every 30 ticks while
`_is_night()` is true (night = `NIGHT_FRACTION = 0.25` of each
`DAY_FRAMES` cycle — see [02-engine-core.md](02-engine-core.md) for the
canonical day/night/season clock). Computes total shelter slots (working
houses × `HOUSE_SHELTER_OCCUPANTS`, plus composable shelter blocks); if
slots cover the living population, everyone is sheltered and nothing
happens. Otherwise homeowners are sheltered first (`ECONOMY_ENABLED`), then
remaining slots fill by proximity; every unsheltered, non-incapacitated
agent above 10 health takes `NIGHT_EXPOSURE_DAMAGE = 2` health, floored at
10, and logs a `night_shelter_rate` benchmark.

`ENV_EFFECTS_ENABLED` extensions ([08-systems-economy.md](08-systems-economy.md)):
working structures with a `shelter` function effect add their `capacity` to
the slot total, and an unsheltered agent standing in a *lit* district (a
working, fueled `light` structure — nightly `upkeep` fuel charged at the
first night-pressure tick of the day) is exempt from the exposure damage.
The `night_shelter_rate` benchmark payload gains a `lit` count when any
agent was spared by light.

**Wildlife:** `_tick_wildlife()` runs on the `GOODS_TICK_FRAMES` gate
(900 ticks) with `WILDLIFE_EVENT_PROB = 0.02` chance per check
(only when `SURVIVAL_ENABLED`). Picks a random living, non-incapacitated
forest-district agent as a candidate victim; if any non-incapacitated guard
is within `WILDLIFE_GUARD_RADIUS = 120` of the victim, the attack is
deterred (activity log only); otherwise the victim takes 5 health damage
(floored at 5).

**Shelter-seeking:** `_maybe_seek_shelter(agent)` (sim_engine.py:4387) — at night, an
unsheltered agent with no active goal is assigned a `seek_shelter` goal
(`USE_GOALS`) toward the nearest district offering shelter capacity.

## Historical rationale and verification

The design rationale for this bundle (motivation, phased rollout, original
acceptance criteria) is preserved, marked historical, at
[docs/archive/path-1-minecraft-like-world-plan.md](../docs/archive/path-1-minecraft-like-world-plan.md)
and `.cursor/path-1-integration-contract.json` — this spec is the current
behavior; the archived plan is not load-bearing for rebuilding the system.
Deterministic smoke (no LM Studio needed):
`uv run python scripts/path1_smoke.py`.
