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

`AGENT_DEFS` (`helpers.py:46-59`) is the fixed pool of hand-written agents
(12 legacy defs plus at least one `hunter` seed — see roles below):
`{id, name, role, personality, color, zone}`. Verified roster (legacy twelve;
hunter inclusion is an additional seed def with role `hunter` and a forest or
farm starting zone):

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
| 13 | Kane | hunter | forest |

`ROSTER` remains an ordered default-8 subset of the hand-written pool
(`helpers.py`); at least one hunter participates via `AGENT_DEFS` and the
generated-roster role rotation (below), even when the cold-start 8 omits them.

`MAX_ROSTER_SIZE = 20` (`helpers.py:69`, Sid-parity Phase 6) is the hard ceiling
on `roster_size` — headroom past the hand-written `AGENT_DEFS` so emergent
roles/belief factions have room to differentiate, deliberately not a bid at
Project Sid's ~500-agent scale (non-goal, specs/00-overview.md).

`_select_active_defs(roster_size)`: clamps `roster_size` to `[1,
MAX_ROSTER_SIZE]`.
- `roster_size <= len(AGENT_DEFS)` (today's 8-12+ default/range): if the
  request is the full hand-written pool it returns `AGENT_DEFS` unchanged.
  Otherwise it fills names from `ROSTER` in order up to `roster_size`, then
  backfills from `AGENT_DEFS` order for any remainder, and **forces Sage in**
  — if Sage isn't already selected, she overwrites the last slot. This
  guarantees an elder always exists regardless of roster size.
- `roster_size > len(AGENT_DEFS)`: all hand-written defs plus
  `_generated_agent_defs(roster_size - len(AGENT_DEFS))` for the remaining
  slots. Generation is deterministic: name and
  personality cycle through small fixed pools (`_GENERATED_AGENT_NAMES`,
  `_GENERATED_AGENT_PERSONALITIES`, 8 entries each — covering
  `MAX_ROSTER_SIZE - len(AGENT_DEFS)` headroom), role rotates across the
  non-elder seed roles (one generated agent per role before any repeats — no
  generated agent is ever seeded into the singular elder role; `hunter` is
  included in that rotation), and starting zone is
  copied from the hand-written def sharing that role. Generated agents are
  built by the same `_make_agents` as hand-written ones and are
  indistinguishable to every other system (roles, beliefs, relationships,
  think scheduling) — only their name/personality are pool-drawn instead of
  bespoke.

**Overrides:**
- `SIM_AGENTS` environment variable (server.py:2635) sets the roster size at
  process start (default 8, clamped to `MAX_ROSTER_SIZE`).
- `POST /control/reset` accepts a JSON body `{"agents": N}` (not a query
  parameter) to reset with a different roster size at runtime — see
  [04-http-api.md](04-http-api.md).

## roles.json schema

`simulation/roles.json` is the single source of truth for the **seed** role
definitions (one per `AGENT_DEFS` role, including `hunter`). Edit seed role
data there, never in code maps. At cold start, the engine copies those entries
into the persistent live `civilization["roleRegistry"]`; approved emergent
roles are added only to that per-world registry and therefore persist in
`state.db` without modifying the authoring file. Schema per seed entry (role
name -> object):

| Field | Type | Meaning |
|---|---|---|
| `skill` | string | One-line prose description folded into the agent's prompt |
| `specialty` | string[] | Resource ids this role gathers preferentially (empty list = non-gatherer) |
| `preferredProject` | string \| string[] | Project type(s) this role tends to start/lead |
| `leader` | bool (optional) | Present and `true` only for `elder` — marks the sole leader role |

Data itself (all seed roles' values) is not restated here — read `roles.json`.

**`hunter` seed role.** Specialty `["meat"]`; skill prose describes hunting
wildlife for meat and fish; preferred project is typically `"wall"` (or
similar non-farm structure). Any living agent may still choose
`hunt_wildlife` when prey is in range ([07-actions.md](07-actions.md)); the
hunter role specializes via prompt mention, `role_fallback_action` bias toward
`hunt_wildlife` when prey is near, and higher per-hit damage
(`HUNT_DAMAGE_HUNTER = 4` vs base `HUNT_DAMAGE = 2` — retuned in
[08-systems-economy.md](08-systems-economy.md#huntable-wildlife-yields-wildlife_enabled)).
Kill yields feed `meat`/`fish` per that section. Emergent-role auto-switch
promotes `hunter` ahead of unfilled `farmer`/`fisher` when wildlife is present,
meat (or total edible) is scarce, and farm/fish gathering is failing — see
**Survival role rebalance** below.

An emergent registry entry is keyed by its validated slug and additionally stores
its display `name`. Its `skill`, `specialty`, and `preferredProject` fields use
the same meanings and shapes as the seed schema. `leader` is never accepted for
an emergent role, so the single elder role remains a seed-only invariant.

## Agent state fields (`_make_agents`, `core.py:274-382`)

| Group | Fields |
|---|---|
| Identity | `id`, `name`, `role`, `personality`, `color` |
| Movement | `x`, `y`, `targetX`, `targetY`, `speed`, `waypoints`, `currentZone`, `currentDistrict` |
| Social | `relationships`, `inbox`, `beliefs`, `votes`, `message`, `messageTimer`, `consecutiveTalks`, `lastSpokeFrame` |
| Survival | `resources`, `hunger`, `health`, `incapacitated` |
| Cognition | `memory` (`{working, shortTerm, longTerm}`), `memoryWiki` (`{relationships, goals, lessons}`, each capped at `WIKI_SECTION_CHAR_CAP`=300 chars — see below), `thinkTimer`, `thinkInterval`, `isThinking`, `pendingThink`, `lastAction`, `lastReasoning`, `persona`, `idleFrames`, `moduleTick`, `modules` (`{perception, social, desire, reflection}` plus `theory_of_mind` when `THEORY_OF_MIND_ENABLED`), `moduleReports` (`{module: {tick, text}}` — persistence-only mirror of the engine's `_piano_module_cache` entry for this agent, written alongside `moduleTick` after every think; never read on the hot path, only rehydrated by `restore_state()`), `peerModel` (`THEORY_OF_MIND_ENABLED` only — `{peerIdStr: {wants, good_at, owes_me, trust, frame}}`, capped by `PEER_MODEL_MAX_PEERS` / `PEER_MODEL_FIELD_CHAR_CAP`, LRU eviction by `frame`), `goal` (kinds include `gather`, `deliver`, `build`, `craft_gather`, `plant_terrain`, `seek_shelter`, `dig_relocate`, `caravan`, `repair`, and **`hunt`** — forced starvation backstop; see [08-systems-economy.md](08-systems-economy.md#starvation-reflex-and-forced-hunt-precedence)), `commitment`, `actionCounts` |
| Task/build | `assignedTask`, `idleCycles`, `lastTaskedFrame`, `lastContributedFrame`, `consecutiveIdleMoves`, `homeStructureId`, `reorgTask` |
| Invention/sprite | `inventionTurn`, `inventionRetryUsed`, `inventionBuildContext`, `spriteDesignTurn` |
| Rejection-note fields | `lastBlueprintRejection`, `lastGatherRejection`, `lastUpgradeRejection`, `lastSpriteRejection`, `lastProjectRejection`, `lastTerraformRejection`, `lastCraftRejection`, `lastRepairRejection`, `lastRecipeRejection`, `lastBurialRejection`, `lastTradeRejection`, `lastShelterNote`, `lastHomelessNudgeFrame` — each surfaces *why* the agent's last attempt at that action was rejected, back into its next prompt |
| Lifecycle (`LIFECYCLE_ENABLED`) | `age` (float, `None` when disabled), `lastQuotaResetFrame`, `gatherCountThisPeriod`, `lastQuotaRejection`, `lastRationingRejection`, `parents`, `deathFrame`, `buried`, `restingPlaceId`, `restingDistrictId` |
| Culture (`CULTURE_ENABLED`) | `skills` (dict per `SKILL_KINDS`, starts at 0.0), `personalityTraits`, `lastTeachFrame` |
| Divine Matrix | `divineHold` (bool — veto hold or architect limbo pauses think/move), `godKeys` (set of god-granted key tags for architect door zones; persisted as sorted list), `architectLimbo` (`null` or `{zoneId, priorX, priorY, priorTargetX, priorTargetY, priorDistrict}` — Sight shows active/zoneId only) |

Post-build setup (`core.py:374-381`) staggers `thinkInterval = 360 + i*60`
(elder forced to `240`) and `thinkTimer = i*30` per roster index `i`, and sets each
agent's initial movement target to its starting district.

## `/state` agent snapshot (`SimEngine.snapshot()`, `mixin_snapshot.py:379-388`, per-agent row built by `_agent_snapshot_row`, `mixin_snapshot.py:119-138`)

Not every internal field above is echoed to the viewer's `agents` array in
`/state` (specs/04-http-api.md) — the snapshot is a filtered/derived view built
under the lock each poll. Two fields worth calling out because they're
transformed, not passed through raw:

- `relationships` — a **filtered** copy of the internal `relationships` dict
  (Social group, above): only non-`"neutral"` ties (`ally`/`rival`) are
  included, to keep the payload small. An agent with no allies/rivals sends
  `{}`.
- `lastReasoning` — the internal `lastReasoning` string (Cognition group,
  above), **capped to 160 characters**; empty/missing becomes `null` rather
  than `""`.

Both are unconditional (no feature flag gates them) since the underlying
fields always exist on every agent.

## Speeds

Set in `_make_agents` (`core.py:282-286`): default `2.8`; **Sage** (elder)
`1.4` — deliberately slow; **Ivy** and **Nova** (scout/explorer) `3.6` — deliberately
fast. All other agents use the `2.8` default.

## Lifecycle (`LIFECYCLE_ENABLED`, default True)

Constants (`constants.py:1576-1598`):

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
beyond the hand-written `AGENT_DEFS` pool get synthetic ids starting at
`nextGeneratedAgentId = 1000`
(`core.py:503`, incremented in `mixin_lifecycle.py:1108-1111`). Natural death rolls apply once past
`DEATH_CHANCE_START_AGE`, deferred by `POPULATION_FLOOR`. On the elder's death, a
succession election runs on the `propose_rule`/`vote_rule` machinery (kind
`"succession"`) with a deterministic tie-break if `SUCCESSION_ELECTION_TTL_FRAMES`
elapses without quorum — full election-flow rules: [09-systems-society.md](09-systems-society.md).

## Memory system (`MEMORY_ENABLED`, default True)

Three-tier per-agent structure (`agent["memory"]`): `working` (cap `WORKING_MEM_CAP
= 6`), `shortTerm` (cap `SHORT_MEM_CAP = 12`), `longTerm` (cap `LONG_MEM_CAP = 8`,
`constants.py:1272-1274`). `_push_memory` (`mixin_world_state.py:217-232`) appends to
`working`; on overflow, evicted entries with salience ≥ 0.7 promote into
`shortTerm`. Every push also writes into the in-process vector store
(`self.d["memory_store"]`, server.py) via a deterministic 128-dim hashing-trick
embedding (`MEMORY_DIM = 128`, `simulation/_server/memory_store.py:26`) — bag-of-tokens hashed (MD5) into
fixed dimension slots, L2-normalized so cosine similarity reduces to dot product;
no external embedding service. Global store cap: `MEMORY_MAX_ENTRIES = 1200`
(`simulation/_server/memory_store.py:27`), trimmed by a periodic cleaner.

Maintenance runs every `MEMORY_TICK_FRAMES = 1800` frames
(`constants.py:1036`, checked in `mixin_think_job.py:1470`) via `_run_memory_maintenance()` (`mixin_decisions.py:291-345`):
round-robins one agent per call; if it has ≥4 recent non-summary memories, an LLM
call compresses them into one first-person sentence, stored as a `longTerm` entry
(salience 0.9) and pushed to `_push_activity` as a "reflected:" log line. Every 4th
maintenance call also runs `memory_store.clean()` to scrub stale/poisoned vector
entries (guards against reasoning-model chain-of-thought scaffolding leaking into
memory — see server.py's scaffold-detection regexes).
`_memory_for_prompt(agent)` (`mixin_world_state.py:346-363`) composes the prompt's memory
section from the last 3 longTerm + 4 shortTerm + 4 working entries.

### Wiki-style compounding memory (`WIKI_MEMORY`, default True)

See [03-cognition.md](03-cognition.md#wiki-memory) for full merge/lint semantics.
Summary for the agent data-shape lens: `agent["memoryWiki"]` is always present
(`{"relationships": "", "goals": "", "lessons": ""}` initial shape from
`_make_agents`; `restore_state()` `setdefault`s it for old saves) so
persistence via `state.db` is automatic — same pattern as `moduleReports`, no
schema migration needed. Populated only when `WIKI_MEMORY` is True; each
section is hard-capped at `WIKI_SECTION_CHAR_CAP = 300` chars
(`constants.py:1277`, next to `LONG_MEM_CAP`). The flag reuses
`_run_memory_maintenance`'s existing round-robin slot — no new LLM call site
or cadence. When on, `_memory_for_prompt` prepends up to three
`"wiki <section>: ..."` lines ahead of the existing longTerm/shortTerm/working
slices (never replacing them); `server.py`'s `MEMORY_PROMPT_CHAR_BUDGET` was
raised 600 -> 900 so those lines have headroom instead of being the first
thing evicted by the char-budget's oldest-first trim.

### Testament inheritance (`TESTAMENT_ENABLED`, default True)

See [09-systems-society.md](09-systems-society.md#testament_enabled) for the
civilization ring and prompt line. Summary for the agent data-shape lens:
when `TESTAMENT_ENABLED` and `WIKI_MEMORY` are both True, `_spawn_newborn`
(`mixin_lifecycle.py`) seeds the newborn's `memoryWiki` from both parents'
sections plus the newest `TESTAMENT_PROMPT_ENTRIES` testament lines (each
section capped at `WIKI_SECTION_CHAR_CAP`). On death,
`_merge_testament_on_death` (`mixin_governance_culture.py`) folds the
deceased's `lessons` and optional `relationships` wiki text into
`civilization["testament"]` deterministically — no new LLM call. With the
flag off, birth and death paths are byte-identical to the pre-Testament
baseline.

## Sovereign God mode: per-agent omen state and sight (Phase 3)

Agents carry no new persisted field for this — a private omen lives entirely
in `civilization["godState"]["privateOmens"]`, keyed by `str(agent["id"])`
(never by name; names are display-only), not on the agent object itself.
`_find_agent_by_id(agent_id)` (`mixin_world_state.py:370-376`, next to `_find_agent` at `mixin_world_state.py:364-368`) is the
id-keyed lookup this and every other Phase 3 command use. `_god_apply_
private_omen` rejects an unknown id or a deceased target
(`agent.get("deathFrame") is not None`) before any text is even normalized —
"one living agent id" per the plan's catalog.

An active omen is reachable from cognition only through the dedicated
`divine_private_line` prompt field ([03-cognition.md](03-cognition.md)) —
never through `agent["memory"]`. Exactly once, when it closes (expiry,
revocation, or replacement — whichever happens first), its text is written
into the target's ordinary memory via
`_push_memory(agent, text, kind="divine_omen")`, guarded by a `memoryWritten`
flag on the omen record so a restore-time re-sweep of an already-closed omen
can never fire it twice. See [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance)
for the full closure/expiry mechanics.

`god_sight(filters)`'s per-agent projection exposes omen **status** only —
`{"active": true, "expiresFrame": int}` or `None` — never the raw omen text
in that specific field (the text is still reachable via the same response's
`recentInterventions`, and only through the authenticated
`/control/god/sight` route — never `/state`, activity, communication, or the
Chronicle). Divine Matrix Phase 3 adds per-agent `memoryCounts`
(`working`/`shortTerm` tier lengths) and `beliefCount` — counts only, never
memory text or planted belief tenets.

## Divine Matrix: memory surgery and belief planting (Phase 3)

`memory_insert` / `memory_delete` / `belief_plant` mutate `agent["memory"]`
tiers and/or `agent["beliefs"]` only through engine helpers
(`_god_memory_insert`, `_god_memory_delete`, `_god_belief_plant`). False
memories use kinds `divine_false_memory` (default insert) or `divine_belief`
(belief plant). They appear in the think payload's `memory` line like any
other recalled line, but never in public activity/communication/chronicle.
`belief_plant` may also write `civilization["memeTexts"]` when
`plantInMemeTexts` is true. See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance)
for command payloads and `MemoryStore.delete_where`.

## Divine Matrix: Identity Forge (Phase 8)

`identity_edit` / `identity_copy_overwrite` mutate `agent["persona"]`,
`agent["personality"]`, and/or `agent["role"]` on the live agent object.
`role` must exist in `roles.json` / `self.d["ROLES"]`. Dead agents are
rejected at preview. Elder role swaps warn in preview but are allowed at apply.
Restore state lives in `godState["identityForges"][str(agentId)]` with a
`snapshot` of the pre-intervention persona/personality/role — never on the
agent dict itself and never in `/state`. `identity_forge_cancel` and expiry
call `_restore_identity_forge_snapshot`. Copy mode does not change `role`.

## Sovereign God mode: `agent_vitals` miracle bounds (Phase 4)

`{"kind": "agent_vitals", "payload": {"targetId": int, "healthDelta":
number?, "hungerDelta": number?}}` — full command details in
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles);
this section documents the agent-state contract specifically. `targetId`
must resolve to a living agent via `_find_agent_by_id` (unknown or
`deathFrame is not None` both reject before any delta is checked); at least
one of `healthDelta`/`hungerDelta` must be present and non-zero; each is
independently capped at `GOD_VITALS_DELTA_MAX = 100` magnitude.

**The no-kill floor.** `agent["health"]` and `agent["hunger"]` are the same
two fields `_update_survival` maintains every tick, and this miracle clamps
them through the same conceptual `0..100` range — with one deliberate
exception. `_update_survival` treats `health <= 0` as the *incapacitation*
threshold (`agent["incapacitated"] = True`), not death: permanent death is a
separate, distinct transition that only ever happens through `_agent_dies`
(old age today; see [09-systems-society.md](09-systems-society.md) for the
lifecycle), and nothing in `_update_survival`'s health-loss branch calls it.
A negative `healthDelta` is nonetheless clamped to stop at
`GOD_VITALS_HEALTH_FLOOR = 1` — one full point above the incapacitation
threshold — rather than at `0`. This is the documented floor above death the
"cannot kill" contract requires: the miracle can never itself be the
mutation that flips `incapacitated = True`, and it never reads or writes
`deathFrame`, `incapacitated`, `goal`, `assignedTask`, or any other
lifecycle-succession field `_agent_dies` touches — it is a pure
`health`/`hunger` write, nothing else. Repeated large negative deltas floor
at `GOD_VITALS_HEALTH_FLOOR` every time (each command reads the agent's
*current* health before clamping), so there is no way to compound damage
below the floor across multiple commands.

Hunger carries no equivalent floor: `hunger <= 0` does not incapacitate or
kill an agent by itself (it only makes the *next* `_update_survival` tick
apply `HEALTH_RATE` loss instead of `HEALTH_REGEN` gain), so a negative
`hungerDelta` clamps to the ordinary `max(0, ...)` floor like every other
hunger-decreasing path in the engine (`HUNGER_RATE` drain, etc.). Positive
deltas on either field clamp at the ordinary `min(100, ...)` ceiling
`_update_survival` also uses.

The miracle calls `_mark_context_dirty(agent)` unconditionally after mutating
(`_update_survival` only does this on a health/hunger threshold crossing; the
miracle is more liberal so a divine vitals change is always reflected in the
agent's next think payload rather than waiting for the next survival tick to
happen to cross a threshold).

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

`_is_flexible_role(role)` (`mixin_backstops.py:67-68`): a role is "flexible" (eligible
for auto-switch) if it has no fixed specialty resource and isn't `elder`.
`_village_needed_role()` (`mixin_backstops.py:209-287`) detects an unfilled need in
priority order: (1) an active build project stalled on an unmet resource with no
living gatherer for it, (2) **survival-critical** when `SURVIVAL_ENABLED`
(stock-aware, wildlife-aware — full algorithm in
[08-systems-economy.md](08-systems-economy.md#survival-role-rebalance-emergent_roles)),
(3) ecology scarcity (including **`meat`** via village-held totals, not
`districtStocks`, because `meat` has `gatherZone: None`). Every
`ROLE_SWITCH_TICK_FRAMES = 120` frames (`constants.py:1212`, checked in `mixin_think_job.py:1474`),
`_maybe_auto_switch_role()` (`mixin_backstops.py:310-342`) checks the needed role
against a cooldown (`ROLE_SWITCH_COOLDOWN`) and, if a flexible-role candidate is
found (`_auto_switch_candidate`), deterministically applies a `switch_role`
decision on that agent's behalf — the same code path an LLM-chosen `switch_role`
action would take (see [07-actions.md](07-actions.md)).

### Agent conflict cooldown state (`confront_agent`)

Bounded PvP pair cooldowns live on civilization, not on individual agents:
`civilization["confrontCooldowns"]` maps a canonical pair key
(`"<minId>:<maxId>"` of the two agent ids) → `frameTick` when the cooldown
expires. Populated only after a successful `confront_agent` resolution;
`restore_state()` `setdefault`s an empty dict. Full combat contract:
[07-actions.md](07-actions.md) and [09-systems-society.md](09-systems-society.md#bounded-agent-conflict-confront_agent).
