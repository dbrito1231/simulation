# SPEC 06 — Agents

Agent definitions, roster selection, roles.json schema, the full agent state field
table, speeds, lifecycle, memory, and emergent roles.

**Canonical for:** `AGENT_DEFS`/`ROSTER` and roster-selection rules, roles.json
schema (data stays in roles.json), agent state fields, movement speeds, lifecycle
constants (aging/births/deaths/succession), the memory system, emergent-role
auto-switch.
**See also:** [01-architecture.md](01-architecture.md) for the flag index
(`MEMORY_ENABLED`, `AGENT_MESSAGING`, `EMERGENT_ROLES`, `LIFECYCLE_ENABLED` echo
status); [09-systems-society.md](09-systems-society.md) for succession-election
detail beyond the summary here; [07-actions.md](07-actions.md) for `switch_role`/
`change_role`/`heal_agent`/`bury_agent`.

## AGENT_DEFS vs ROSTER

`AGENT_DEFS` (sim_engine.py:1093-1106) is the fixed pool of 12 possible agents:
`{id, name, role, personality, color, zone}`. Verified roster:

| id | name | role | starting zone |
|---|---|---|---|
| 1 | Aria | farmer | farm_north |
| 2 | Marco | trader | market |
| 3 | Zara | builder | village_core |
| 4 | Rex | guard | village_core |
| 5 | Luna | gatherer | forest |
| 6 | Finn | fisher | beach |
| 7 | Mia | healer | village_core |
| 8 | Colt | miner | cave_east |
| 9 | Ivy | scout | forest |
| 10 | Dex | blacksmith | market |
| 11 | Nova | explorer | beach |
| 12 | Sage | elder | village_core |

`ROSTER = ["Zara", "Sage", "Aria", "Luna", "Marco", "Colt", "Finn", "Mia"]`
(sim_engine.py:1107) is the ordered default-8 subset used at cold start.

`MAX_ROSTER_SIZE = 20` (sim_engine.py, Sid-parity Phase 6) is the hard ceiling
on `roster_size` — headroom past the 12 hand-written `AGENT_DEFS` so emergent
roles/belief factions have room to differentiate, deliberately not a bid at
Project Sid's ~500-agent scale (non-goal, specs/00-overview.md).

`_select_active_defs(roster_size)`: clamps `roster_size` to `[1,
MAX_ROSTER_SIZE]`.
- `roster_size <= len(AGENT_DEFS)` (today's 8-12 default/range, unchanged
  behavior): if the request is the full 12 it returns `AGENT_DEFS` unchanged.
  Otherwise it fills names from `ROSTER` in order up to `roster_size`, then
  backfills from `AGENT_DEFS` order for any remainder, and **forces Sage in**
  — if Sage isn't already selected, she overwrites the last slot. This
  guarantees an elder always exists regardless of roster size.
- `roster_size > len(AGENT_DEFS)`: all 12 hand-written defs plus
  `_generated_agent_defs(roster_size - len(AGENT_DEFS))` for the remaining
  slots (indices 12..roster_size-1). Generation is deterministic: name and
  personality cycle through small fixed pools (`_GENERATED_AGENT_NAMES`,
  `_GENERATED_AGENT_PERSONALITIES`, 8 entries each — exactly covering
  `MAX_ROSTER_SIZE - len(AGENT_DEFS)`), role rotates across the 11 non-elder
  seed roles (one generated agent per role before any repeats — no generated
  agent is ever seeded into the singular elder role), and starting zone is
  copied from the hand-written def sharing that role. Generated agents are
  built by the same `_make_agents` as hand-written ones and are
  indistinguishable to every other system (roles, beliefs, relationships,
  think scheduling) — only their name/personality are pool-drawn instead of
  bespoke.

**Overrides:**
- `SIM_AGENTS` environment variable (server.py:3692) sets the roster size at
  process start (default 8, clamped to `MAX_ROSTER_SIZE`).
- `POST /control/reset` accepts a JSON body `{"agents": N}` (not a query
  parameter) to reset with a different roster size at runtime — see
  [04-http-api.md](04-http-api.md).

## roles.json schema

`simulation/roles.json` is the single source of truth for the 12 **seed** role
definitions (one per `AGENT_DEFS` role). Edit seed role data there, never in code
maps. At cold start, the engine copies those entries into the persistent live
`civilization["roleRegistry"]`; approved emergent roles are added only to that
per-world registry and therefore persist in `state.db` without modifying the
authoring file. Schema per seed entry (role name -> object):

| Field | Type | Meaning |
|---|---|---|
| `skill` | string | One-line prose description folded into the agent's prompt |
| `specialty` | string[] | Resource ids this role gathers preferentially (empty list = non-gatherer) |
| `preferredProject` | string \| string[] | Project type(s) this role tends to start/lead |
| `leader` | bool (optional) | Present and `true` only for `elder` — marks the sole leader role |

Data itself (all 12 roles' values) is not restated here — read `roles.json`.

An emergent registry entry is keyed by its validated slug and additionally stores
its display `name`. Its `skill`, `specialty`, and `preferredProject` fields use
the same meanings and shapes as the seed schema. `leader` is never accepted for
an emergent role, so the single elder role remains a seed-only invariant.

## Agent state fields (`_make_agents`, sim_engine.py:1298-1388)

| Group | Fields |
|---|---|
| Identity | `id`, `name`, `role`, `personality`, `color` |
| Movement | `x`, `y`, `targetX`, `targetY`, `speed`, `waypoints`, `currentZone`, `currentDistrict` |
| Social | `relationships`, `inbox`, `beliefs`, `votes`, `message`, `messageTimer`, `consecutiveTalks`, `lastSpokeFrame` |
| Survival | `resources`, `hunger`, `health`, `incapacitated` |
| Cognition | `memory` (`{working, shortTerm, longTerm}`), `thinkTimer`, `thinkInterval`, `isThinking`, `pendingThink`, `lastAction`, `lastReasoning`, `persona`, `idleFrames`, `moduleTick`, `modules` (`{perception, social, desire, reflection}`), `moduleReports` (`{module: {tick, text}}` — persistence-only mirror of the engine's `_piano_module_cache` entry for this agent, written alongside `moduleTick` after every think; never read on the hot path, only rehydrated by `restore_state()`), `goal`, `commitment`, `actionCounts` |
| Task/build | `assignedTask`, `idleCycles`, `lastTaskedFrame`, `lastContributedFrame`, `consecutiveIdleMoves`, `homeStructureId`, `reorgTask` |
| Invention/sprite | `inventionTurn`, `inventionRetryUsed`, `inventionBuildContext`, `spriteDesignTurn` |
| Rejection-note fields | `lastBlueprintRejection`, `lastGatherRejection`, `lastUpgradeRejection`, `lastSpriteRejection`, `lastProjectRejection`, `lastTerraformRejection`, `lastCraftRejection`, `lastRepairRejection`, `lastRecipeRejection`, `lastBurialRejection`, `lastTradeRejection`, `lastShelterNote`, `lastHomelessNudgeFrame` — each surfaces *why* the agent's last attempt at that action was rejected, back into its next prompt |
| Lifecycle (`LIFECYCLE_ENABLED`) | `age` (float, `None` when disabled), `lastQuotaResetFrame`, `gatherCountThisPeriod`, `lastQuotaRejection`, `lastRationingRejection`, `parents`, `deathFrame`, `buried`, `restingPlaceId`, `restingDistrictId` |
| Culture (`CULTURE_ENABLED`) | `skills` (dict per `SKILL_KINDS`, starts at 0.0), `personalityTraits`, `lastTeachFrame` |

Post-build setup (sim_engine.py:1381-1387) staggers `thinkInterval = 360 + i*60`
(elder forced to `240`) and `thinkTimer = i*30` per roster index `i`, and sets each
agent's initial movement target to its starting district.

## Speeds

Set in `_make_agents` (sim_engine.py:1306-1310): default `2.8`; **Sage** (elder)
`1.4` — deliberately slow; **Ivy** and **Nova** (scout/explorer) `3.6` — deliberately
fast. All other agents use the `2.8` default.

## Lifecycle (`LIFECYCLE_ENABLED`, default True)

Constants (sim_engine.py:629-651):

| Constant | Value | Meaning |
|---|---|---|
| `LIFECYCLE_TICK_FRAMES` | 300 | Aging-gate interval |
| `AGE_YEARS_PER_TICK` | `LIFECYCLE_TICK_FRAMES / YEAR_FRAMES` (= 1/1080) | Exactly 1 year per `YEAR_FRAMES` (locks aging to the season/calendar clock — see [02-engine-core.md](02-engine-core.md)) |
| `ADULT_AGE` | 18 | Below this, cannot be a birth parent or election candidate |
| `ELDER_AGE` | 55 | Life-stage label switches to "elder" (age word only — distinct from the elder *role*) |
| `MAX_LIFE_EXPECTANCY` | 90 | Death chance saturates approaching this age |
| `DEATH_CHANCE_START_AGE` | 65 | Natural-death rolls begin at this age |
| `DEATH_CHANCE_PER_TICK` | 0.0006 | Base per-gate roll past `DEATH_CHANCE_START_AGE`, scaled by age |
| `POPULATION_FLOOR` | 4 | Death defers (logged, not executed) if it would drop non-incapacitated adults below this |
| `BIRTH_CHECK_FRAMES` | = `LIFECYCLE_TICK_FRAMES` | Birth-eligibility check cadence |
| `BIRTH_FOOD_SURPLUS_PER_AGENT` | 4 | Stockpile + carried edibles must exceed this × population |
| `BIRTH_MIN_INTERVAL_FRAMES` | `STALL_THRESHOLD * 6` (~2 min) | Cooldown between births village-wide |
| `NEWBORN_GOODS_SHARE` | 0.15 | Fraction of a parent's held goods a newborn inherits |
| `SUCCESSION_ELECTION_TTL_FRAMES` | `STALL_THRESHOLD * 8` (~13 min) | Deadline before a deterministic tiebreak resolves an elder-succession election |

Starting ages are staggered deterministically (not randomly) at cold start: the
elder starts at `ELDER_AGE + 5`; every other agent at `ADULT_AGE + 2 + (i*7) % 30`
by roster index `i` — so a fresh world already spans young/adult ages, not one
generation.

Births need housing headroom, the food surplus above, and two ally adults sharing
a district; capped at one per `BIRTH_MIN_INTERVAL_FRAMES`. Newly-generated agents
beyond the 12-def pool get synthetic ids starting at `nextGeneratedAgentId = 1000`
(sim_engine.py:1465, 5759). Natural death rolls apply once past
`DEATH_CHANCE_START_AGE`, deferred by `POPULATION_FLOOR`. On the elder's death, a
succession election runs on the `propose_rule`/`vote_rule` machinery (kind
`"succession"`) with a deterministic tie-break if `SUCCESSION_ELECTION_TTL_FRAMES`
elapses without quorum — full election-flow rules: [09-systems-society.md](09-systems-society.md).

## Memory system (`MEMORY_ENABLED`, default True)

Three-tier per-agent structure (`agent["memory"]`): `working` (cap `WORKING_MEM_CAP
= 6`), `shortTerm` (cap `SHORT_MEM_CAP = 12`), `longTerm` (cap `LONG_MEM_CAP = 8`,
sim_engine.py:426-428). `_push_memory` (sim_engine.py:1558-1572) appends to
`working`; on overflow, evicted entries with salience ≥ 0.7 promote into
`shortTerm`. Every push also writes into the in-process vector store
(`self.d["memory_store"]`, server.py) via a deterministic 128-dim hashing-trick
embedding (`MEMORY_DIM = 128`, server.py:331) — bag-of-tokens hashed (MD5) into
fixed dimension slots, L2-normalized so cosine similarity reduces to dot product;
no external embedding service. Global store cap: `MEMORY_MAX_ENTRIES = 1200`
(server.py:332), trimmed by a periodic cleaner.

Maintenance runs every `MEMORY_TICK_FRAMES = 1800` frames
(sim_engine.py:245, 9402) via `_run_memory_maintenance()` (sim_engine.py:7833-7866+):
round-robins one agent per call; if it has ≥4 recent non-summary memories, an LLM
call compresses them into one first-person sentence, stored as a `longTerm` entry
(salience 0.9) and pushed to `_push_activity` as a "reflected:" log line. Every 4th
maintenance call also runs `memory_store.clean()` to scrub stale/poisoned vector
entries (guards against reasoning-model chain-of-thought scaffolding leaking into
memory — see server.py's scaffold-detection regexes).
`_memory_for_prompt(agent)` (sim_engine.py:1574-1576) composes the prompt's memory
section from the last 3 longTerm + 4 shortTerm + 4 working entries.

## Emergent roles (`EMERGENT_ROLES`, default True)

Any agent may submit `propose_role` with a role object containing `slug`, `name`,
`specialty`, `preferredProject`, and `skill`. The proposal is held in
`civilization["pendingRoles"]` until an elder uses `approve_role` or
`reject_role`. Approval validates the slug, display name, one-line skill,
known-resource specialties, and project preference; it rejects collisions with
the live registry and caps approvals at `MAX_EMERGENT_ROLES = 8` beyond the seed
set. The pending queue is independently capped at `MAX_PENDING_ROLES = 5`, so
additional proposals are rejected until the elder resolves one. Rejected
proposals are discarded. On approval, the engine rebuilds its
derived `ROLE_PROJECT`, `ROLE_SKILLS`, `ROLE_PRIMARY_RESOURCE`, and
`RESOURCE_GATHER_ROLES` maps from the live registry before any future prompt,
need-detection, or role-switch read. `switch_role` may then select the approved
slug exactly as it can a seed role. Each think payload also carries these live
role maps, so server-side fallback/project/task helpers use the world's approved
roles rather than the process-start seed-map conveniences; separate engine worlds
therefore cannot leak role specializations into one another.

`_is_flexible_role(role)` (sim_engine.py:6401-6402): a role is "flexible" (eligible
for auto-switch) if it has no fixed specialty resource and isn't `elder`.
`_village_needed_role()` (sim_engine.py:6414+) detects an unfilled need in
priority order: (1) an active build project stalled on an unmet resource with no
living gatherer for it, (2) survival-critical (starving agents, no living
food/fish gatherer) when `SURVIVAL_ENABLED`, (3) ecology scarcity. Every
`ROLE_SWITCH_TICK_FRAMES = 120` frames (sim_engine.py:393, 9406),
`_maybe_auto_switch_role()` (sim_engine.py:6502-6532) checks the needed role
against a cooldown (`ROLE_SWITCH_COOLDOWN`) and, if a flexible-role candidate is
found (`_auto_switch_candidate`), deterministically applies a `switch_role`
decision on that agent's behalf — the same code path an LLM-chosen `switch_role`
action would take (see [07-actions.md](07-actions.md)).
