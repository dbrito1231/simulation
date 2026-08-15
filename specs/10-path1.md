# SPEC 10 — Path 1: Minecraft-Like World Depth

The `PATH1_ENABLED` bundle: industry (ores/smelting), tool tiers, composable
blocks, diggable terrain tiles, a second-settlement diplomacy layer, tier-3
content, and a day/night pressure loop.

**Canonical for:** `PATH1_ENABLED`/`path1_on()` semantics,
`INDUSTRY_ENABLED`, `TOOL_TIERS_ENABLED`, `COMPOSABLE_BUILD_ENABLED`,
`TERRAIN_TILES_ENABLED`, `PATH1_DIPLOMACY_ENABLED`, `TIER3_CONTENT_ENABLED`,
`PRESSURE_LOOP_ENABLED`, `RAIDERS_CONTAGION_ENABLED` semantics.
**See also:** [01-architecture.md](01-architecture.md) for the flag index;
[05-world.md](05-world.md) for district/terrain geometry (`TILE_CELL`
cross-link); [02-engine-core.md](02-engine-core.md) for day/night/season
constants; [07-actions.md](07-actions.md) for action params;
[08-systems-economy.md](08-systems-economy.md) for crafting/goods this
bundle extends.

## `PATH1_ENABLED` and `path1_on()`

`PATH1_ENABLED = True` is the master bundle switch (sim_engine/constants.py:1693);
`path1_on(subflag=None)` (sim_engine/constants.py:1708) returns `True` unconditionally
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
three tool tiers (sim_engine/constants.py:2022-2062). New workshop recipes: charcoal
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
"iron_ore": "iron_pick"}`. `_can_gather_resource` (sim_engine/mixin_diplomacy.py:48)
compares `_gather_tool_tier(agent)` (highest-tier pick currently held)
against the resource's requirement; below it, the gather is refused with a
named reason ("`<resource>` needs a `<tool>` (you have tier `<n>` tools)").
`TOOL_YIELD_BONUS = 1` — holding the *exact* required tier (not just
meeting the minimum) adds a small yield bonus on top of `_gather_yield_bonus`.

When `TOOL_TIERS_ENABLED` and `TERRAIN_TILES_ENABLED` are both on,
`_pickless_stone_route` (sim_engine/mixin_diplomacy.py:121) reroutes a stone-seeker without
a pick to dig terrain directly (stone's nominal cave gather zone has no
diggable ground) instead of bouncing between cave and farm forever — the
bootstrap escape for a fresh world with no Workshop yet (digging itself is
deliberately tool-free).

## COMPOSABLE_BUILD_ENABLED

Free-form single-tile placement on a per-district 8×8 grid
(`PATH1_GRID_COLS = PATH1_GRID_ROWS = 8`, cell size `TILE_CELL = 40` —
geometry detail cross-linked from [05-world.md](05-world.md)).
`BLOCK_TYPES` (sim_engine/constants.py:2003-2008): `wall` (1 wood, shelter), `floor`
(1 wood), `door` (2 wood), `fence` (1 wood, shelter).

`place_block` (`_place_block`, sim_engine/mixin_diplomacy.py:161): resolves target cell
(explicit `gx,gy` or the agent's current cell via `_pos_to_grid`), rejects
on unknown block type, no district, the district's `TILE_CAP_PER_DISTRICT
= 200` reached, the target cell already occupied, or insufficient
resources for the block's cost (each rejection sets `lastBlockRejection`
with a reason, read by the next prompt). On success, deducts cost, stores
the block in `district["tiles"][gx,gy]`, logs a `composable_placements`
benchmark. `remove_block` (`_remove_block`, sim_engine/mixin_diplomacy.py:200) clears a
tile; `BLOCK_REFUND_RATIO = 0.5` refunds half the placement cost.
Shelter-flagged blocks (`wall`/`fence`) count toward night shelter capacity
via `_composable_shelter_count` (see [08](08-systems-economy.md)).

## TERRAIN_TILES_ENABLED

Each district lazily gets a per-cell terrain grid
(`_ensure_district_terrain`, sim_engine/mixin_diplomacy.py:151) over the same 8×8 grid,
defaulting by district kind: forest→grove, farm→soil, beach→sand,
cave→rock, ocean→water, else soil. `TERRAIN_TYPES = ("soil", "rock",
"grove", "water")`. `NON_DIGGABLE_DISTRICT_KINDS = {"forest", "beach",
"cave", "ocean"}` — these kinds' grids never contain soil, so
`dig_terrain` there always fails or relocates.

`dig_terrain` (`_dig_terrain`, sim_engine/mixin_diplomacy.py:234): grove→soil (clears a
grove tile, no yield); soil→rock (yields 1 stone up to carry cap); any
other current terrain (already rock/sand/water) is exhausted — the agent is
routed to the nearest fresh soil tile in the same district
(`_find_nearby_terrain`) or, if none exists district-wide, to the nearest
other diggable district (`_nearest_diggable_district`, nearest by
district-center distance when an agent is given), setting a `dig_relocate`
goal (`USE_GOALS`) so the trip completes deterministically rather than
re-deciding every LLM think. Successful digs log a `terrain_mutations`
benchmark.

`plant_terrain` (`_plant_terrain`, sim_engine/mixin_diplomacy.py:304): costs 1 wood,
converts the agent's current tile toward `grove` (farm districts use this
to counteract dig-driven grove loss; `_maybe_expand_field` auto-assigns a
`plant_terrain` goal when a farm district's grove ratio drops below 0.3).

## PATH1_DIPLOMACY_ENABLED

`_init_settlements()` (sim_engine/mixin_diplomacy.py:353) seeds a single `"home"`
settlement owning every starter district. `_maybe_found_settlement()`
(tick-gated backstop) founds a second settlement — `"outpost"`, on a
claimed frontier plot — once `structures ≥ SETTLEMENT_STRUCT_THRESHOLD = 5`
(non-ruin) and `living ≥ SETTLEMENT_POP_THRESHOLD = 6`; caps at 2
settlements total.

**Settlement stores:** `civilization["settlementStores"][settlement_id] =
{resource_id: qty}` — per-settlement stockpiles distinct from the
village-wide `stockpile`. Initialized empty for every known settlement on
cold start and migrated empty on `restore_state()` (`setdefault` per
settlement id). Local gather overflow and caravan delivery credits prefer
the agent's current settlement store; local repair/craft funding draws from
the agent's settlement store first, then falls back to the village
`stockpile` (same district-store-first pattern as upkeep — see
[08-systems-economy.md](08-systems-economy.md#settlement-stores-and-inter-settlement-trade-path1_diplomacy_enabled)).
Think payload and `/state` expose per-settlement stores when this flag is on.

**Schism (`SCHISM_ENABLED`, default on):** domestic
rules/belief registries are keyed by settlement id (`"home"` primary); treaties,
caravan tariffs, and `settlementStores` stay as documented here and in
[09-systems-society.md](09-systems-society.md#schism_enabled). F4.3 adds the
deterministic schism trigger, secession via frontier founding, and per-settlement
elder succession — inter-settlement trade remains treaty/caravan/tariff only.

**Treaties:** `RULE_KINDS` gains `"treaty"` under this flag (see
[09-systems-society.md](09-systems-society.md) for the shared propose/vote
scaffold). `propose_treaty`/`vote_treaty`
(`_propose_treaty` sim_engine/mixin_diplomacy.py:814/`_vote_treaty` sim_engine/mixin_diplomacy.py:839) reuse the rules
`pendingRules`/`_tally_and_maybe_enact` machinery directly — a treaty is a
rule with `kind: "treaty"`, requiring `id`/`name` on the proposal. Enacted
treaties may carry an optional `tariff` fraction (`0`–`0.25`, default `0`);
see [Treaty tariffs](#treaty-tariffs) below.

**Caravans:** `_maybe_caravan_goal` (sim_engine/mixin_diplomacy.py:677) — an agent holding
a cart/wagon (raising `_carry_cap`) and at least `CARAVAN_CARRY_MIN = 3`
total resources, once a second settlement exists, is assigned a `caravan`
goal (`USE_GOALS`) to walk to the other settlement's first district. On
arrival, `_caravan_trade_bundle` + `_deliver_caravan` debit the traveler's
trade goods, credit the destination `settlementStores`, apply treaty
tariffs, call `_emit_shipment` per transferred resource, and append an
enriched `caravanLog` entry (`goods`, `from`, `to`, `frame`) plus an
`inter_village_trades` benchmark. Live persisted `civilization["caravanLog"]`
is capped at `CARAVAN_LOG_CAP = 20` (oldest entries drop on each append);
`/state` projects the same bounded tail. The LLM may also start a caravan run via
the `deliver_caravan` action ([07-actions.md](07-actions.md)); the goal
still completes authoritative delivery on arrival. `_border_settlement_agent`
flags an agent within 150px of an agent from a different settlement (used
for diplomacy-flavored prompt lines).

### Treaty tariffs

Enacted `kind: "treaty"` rules (and matching entries in
`civilization["treaties"]`) gain an optional `tariff` field: a fraction
`0`–`0.25`, default `0`. On caravan delivery, the tariff share of each
resource in the bundle is credited to the **source** settlement's store (or
the village `stockpile` when the source settlement has no store entry); the
remainder is credited to the destination settlement store. Proposers supply
`tariff` through the existing `propose_treaty` `rule` object (schema detail
in [09-systems-society.md](09-systems-society.md)).

## TRANSIT_ENABLED

`TRANSIT_ENABLED` defaults to True and requires diplomacy. The `transit`
unlock has the shape `{"kind":"transit","terrain":"ocean",
"consumes":{"boat":1}}`; `terrain` is currently limited to `ocean` and all
consumed resource ids must be known positive quantities. A working transit
structure permits ocean-zone gathering and consumes its cost via
`_consume_ocean_transit` when a caravan crosses settlement boundaries by
water.

**Bounded ocean corridor (caravan/transit only):** when `TRANSIT_ENABLED`
and `_has_ocean_transit()` are true, inter-settlement `caravan` goals that
cross settlement boundaries route through an ocean corridor — source
dock/shipyard district → ocean waypoint(s) → destination dock/district —
instead of road-only arrival. Transit cost is consumed once per crossing via
`_consume_ocean_transit` (village stockpile first today; settlement-store
precedence lands in Phase 3b). Ordinary agent movement, gather, and
non-caravan goals remain district/road-bound; there is no free-swim
everywhere and no persistent vehicle entities. Movement geometry is owned
by [05-world.md](05-world.md#inter-settlement-movement-ocean-corridor); shipment
`mode: "boat"` visuals reuse the same boundary signal
([08-systems-economy.md](08-systems-economy.md#caravan_visuals_enabled)).

Save migration (`restore_state()`): the `dock` and `shipyard` types gain a
`{"kind":"transit","terrain":"ocean","consumes":{"boat":1}}` unlock when
missing. Like the hearth/lighthouse light migration, when the registry entry
itself is gone (the approved-custom registry caps at 15 and retires old
entries) but a structure instance of the type still stands, a minimal
registry entry is recreated from the instance so old saves regain transit —
otherwise the migration would silently no-op on exactly the saves that need
it. Idempotent.

## TIER3_CONTENT_ENABLED

Layered on top of `INDUSTRY_ENABLED` (sim_engine/constants.py:1694): three tier-2/3
structures — **Harbor** (beach district, tier 2: produces +1 fish/1500
ticks/district, boosts fish gather up to +2), **Mill** (village, tier 2:
boosts edible gather up to +2/district), **Foundry** (village, tier 3:
unlocks tier-3 `craft`, produces 1 iron ingot/2400 ticks village-wide).
Extends `ERA_LADDER` with Harbor Era and Mill Era
(`TECH_TREE_ENABLED`) — see [09](09-systems-society.md).

## PRESSURE_LOOP_ENABLED

**Night exposure:** `_tick_night_pressure` (sim_engine/mixin_wildlife.py:136) runs every 30 ticks while
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

**Wildlife (pressure event):** `_tick_wildlife()` runs on the
`GOODS_TICK_FRAMES` gate (900 ticks) with `WILDLIFE_EVENT_PROB = 0.02` chance
per check (only when `SURVIVAL_ENABLED`). Picks a random living,
non-incapacitated forest-district agent as a candidate victim; if any
non-incapacitated guard is within `WILDLIFE_GUARD_RADIUS = 120` of the
victim, the attack is deterred (activity log only); otherwise the victim
takes 5 health damage (floored at 5). **Name disambiguation:** this Path-1
pressure helper is unrelated to huntable fauna
(`_move_wildlife` / `_tick_huntable_wildlife` under `WILDLIFE_ENABLED` —
[02-engine-core.md](02-engine-core.md), [05-world.md](05-world.md)); do not
reuse or conflate them.

**Shelter-seeking:** `_maybe_seek_shelter(agent)` (sim_engine/mixin_wildlife.py:764) — at night, an
unsheltered agent with no active goal is assigned a `seek_shelter` goal
(`USE_GOALS`) toward the nearest district offering shelter capacity.

## World Wiki — settlement and treaty pages (`WORLD_WIKI_ENABLED`)

**Grounded in:** plan §2 Answers 1, 2, 3.

This section documents the wiki page shapes for the two entity kinds owned by this spec:
**settlement** and **treaty**. Both are Path 1 diplomacy data
(`mixin_diplomacy.py`, gated by `path1_on("PATH1_DIPLOMACY_ENABLED")`). Both are
read-only projections over existing engine state; the wiki route (`GET /wiki`,
[specs/04-http-api.md](04-http-api.md)) assembles them in-process and omits both
page kinds when `PATH1_DIPLOMACY_ENABLED` is off.

### Settlement page

Source: `civilization["settlements"]` (`mixin_snapshot.py:230-238`).

Fields projected onto a settlement page:

| Field | Source | Notes |
|---|---|---|
| `id` | settlement id | |
| `name` | `settlement["name"]` | |
| `districts` | `settlement["districts"]` | list of district ids; links to district pages |

**Structured links** (from the Answer 2 cross-link table):

- `districts[]` items → district pages (each is a district id)

### Treaty page

Source: `civilization["treaties"]` (enacted treaties only). Enacted treaty shape
(`mixin_diplomacy.py:802-844`): `{id, name, value, tariff, frame}`.

Fields projected onto a treaty page:

| Field | Source | Notes |
|---|---|---|
| `id` | treaty id | |
| `name` | `treaty["name"]` | |
| `value` | `treaty["value"]` | numeric treaty value |
| `tariff` | `treaty["tariff"]` | numeric tariff rate |
| `frame` | `treaty["frame"]` | tick frame enacted |

**No settlement link (Answer 2 — verified).** The enacted treaty shape carries no
settlement id field. Treaties do not structurally link to settlement pages in v1.

## RAIDERS_CONTAGION_ENABLED

**Third pressure mechanism — three-way disambiguation.** This flag gates a
deterministic external-pressure subsystem (raids + contagion) implemented in
`simulation/sim_engine/mixin_pressure_raiders.py`. It is **not** part of
`PRESSURE_LOOP_ENABLED` above (night exposure + the `_tick_wildlife` pressure
event in `mixin_wildlife.py`) and **not** part of `WILDLIFE_ENABLED`'s huntable
fauna (`_move_wildlife` / `_tick_huntable_wildlife`). Do not fold raid/contagion
logic into `mixin_wildlife.py` or conflate it with either existing system.

**Kill switch:** when `RAIDERS_CONTAGION_ENABLED = False`, both raid and
contagion tick functions early-return before any RNG or mutation (same pattern
as `_tick_wildlife`'s `if not SURVIVAL_ENABLED: return`). The flag is echoed in
`/state` `config.flags` ([01-architecture.md](01-architecture.md)).

**Shared tick gate:** both mechanics roll on the `GOODS_TICK_FRAMES = 900`
(~30s) gate — reuse `GOODS_TICK_FRAMES`; no separate `RAID_CONTAGION_TICK_GATE`
constant. Matches wildlife's cadence pattern ([08-systems-economy.md](08-systems-economy.md)).

| Constant | Value | Role |
|---|---|---|
| `RAID_EVENT_PROB` | `0.01` | Per-gate probability of scheduling a raid (half of `WILDLIFE_EVENT_PROB = 0.02`; raids are more consequential per instance) |
| `CONTAGION_EVENT_PROB` | `0.015` | Per-gate probability of scheduling a contagion outbreak (milder per instance than a raid, but sustained spread makes it proportionately rarer than wildlife's 0.02) |

**Telegraph (both events):** when a roll succeeds, the engine enters a warning
state `RAID_TELEGRAPH_LEAD_FRAMES = 300` (~10s at 30 ticks/s) **before** impact.
During the lead window the viewer and/or agent think payloads surface the pending
event so agents can organize (guards, healing, quarantine prep). Engine hooks
(Phase 2): `_begin_raid_telegraph` schedules `civilization["pressureTelegraph"]`;
`_tick_pressure_raiders` resolves every tick when `frameTick >= impactFrame`;
think payloads expose `pressure_warning_line`; `/state` exposes `pressureTelegraph`
when active. See
[11-viewer.md](11-viewer.md) for the viewer contract; think-payload injection
follows the existing emergency-context pattern in `mixin_think_job.py`.

**Raid resolution (summary):** on impact, a raid removes bounded resources from
`civilization["stockpile"]` and/or `districtStocks`, applies structure-condition
damage via `_apply_structure_condition_delta`, and applies contact health damage
to eligible agents present — all scaled by guard-count + wall mitigation. Full
effect shapes, counters, and formulas: [08-systems-economy.md](08-systems-economy.md)
(raid stockpile/structure/contact + `"mitigates"`/`"heals"` effect kinds),
[05-world.md](05-world.md) (structure damage/ruin reuse), [06-agents.md](06-agents.md)
(contact health routing), [02-engine-core.md](02-engine-core.md) (elder exclusion).

**Contagion resolution (summary):** on impact, seeds `agent["infected"]` /
`agent["infectionFrame"]` on patient zero; each subsequent goods-tick gate runs
proximity spread, per-gate health loss, duration/recovery math, and healer/clinic
coverage bonuses. Constants and recovery math: [08-systems-economy.md](08-systems-economy.md),
agent fields: [06-agents.md](06-agents.md).

**Governance counter:** `"quarantine"` rule kind (movement + trading restriction)
— [09-systems-society.md](09-systems-society.md). Agents respond via existing
actions only — [07-actions.md](07-actions.md#raiders-and-contagion-no-new-actions).

## Historical rationale and verification

The design rationale for this bundle (motivation, phased rollout, original
acceptance criteria) is preserved, marked historical, at
[docs/archive/path-1-minecraft-like-world-plan.md](../docs/archive/path-1-minecraft-like-world-plan.md)
and `.cursor/path-1-integration-contract.json` — this spec is the current
behavior; the archived plan is not load-bearing for rebuilding the system.
Deterministic smoke (no Ollama needed):
`uv run python scripts/path1_smoke.py`.
