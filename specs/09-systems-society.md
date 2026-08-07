# SPEC 09 — Systems: Society

Flag semantics for governance and culture: the tech tree and invention
pipeline, the rules/voting system (including the anti-oscillation guard),
memes, culture (skills/teaching/library/chronicle), messaging, benchmarks,
and the governance-specific slice of lifecycle succession.

**Canonical for:** `TECH_TREE_ENABLED`, `DAILY_COUNCIL_ENABLED`, `SAGE_REVIEW_ENABLED`,
`RULES_ENABLED`, `MEMES_ENABLED`, `CULTURE_ENABLED`, `AGENT_MESSAGING`,
`BENCHMARKS_ENABLED` semantics; the succession/harvest_quota/rationing rule
kinds under `LIFECYCLE_ENABLED`.
**See also:** [01-architecture.md](01-architecture.md) for the flag index;
[06-agents.md](06-agents.md) for lifecycle state fields, aging/birth/death
mechanics, and the `EMERGENT_ROLES` summary (this file covers only the rule
kinds elections ride on); [07-actions.md](07-actions.md) for action params;
[08-systems-economy.md](08-systems-economy.md) for structure effects the
tech tree gates.

## TECH_TREE_ENABLED

Every structure type and recipe carries a `tier` (default 1; seed tier-2
tech is the granary and cart). `_village_tech_tier()` (sim_engine/mixin_structures_economy.py:152)
= the highest `unlocks.tier` among built, *working* station structures
(floor 1; capped `MAX_TECH_TIER = 3`). Proposing/crafting/starting tier-T
tech requires village tier ≥ T; every refusal names the deterministic
escape (`_tier_gate_reason`) — the tier-T station is itself buildable one
tier lower (e.g. the Forge, tier-2 unlock, is plain tier-1 tech).

**Era ladder** (`ERA_LADDER`, sim_engine/constants.py:1505): Founding → Craftsman
(working craft station) → Forge (working tier-2 station) → Wagon (a
cart/wagon in village hands) → (`TIER3_CONTENT_ENABLED`) Harbor → Mill.
`_maybe_era_transition()` (sim_engine/mixin_structures_economy.py:251) is tick-gated and monotonic
— a broken Forge never un-names the era — and logs/benchmarks (`era`) on
advance.

**Legacy invention council (only while `DAILY_COUNCIL_ENABLED` is off).** When `_maybe_invention_backstop()`
(sim_engine/mixin_council_growth.py:1066) fires — `_invention_required()` (sim_engine/mixin_project_helpers.py:122) has held true for
`INVENTION_BACKSTOP_STREAK = 3` consecutive elder think turns and no
blueprint is pending — up to `INVENTION_COUNCIL_SIZE = 3` idle villagers
(only when ≥2 are idle) get parallel invention-only think turns (each
*replaces* that villager's normal turn, no added LLM call volume) and walk
to the elder, who judges proposals comparatively. After
`INVENTION_ELDER_TAKEOVER = 3` backstop delegations land no valid proposal,
or no villager is free, the elder drafts one himself. A council with no
verdict for `COUNCIL_TTL_FRAMES = STALL_THRESHOLD * 20` (≈6.7 min,
`STALL_THRESHOLD = 600`) dissolves (`_maybe_dissolve_council`); records are
capped at `COUNCIL_LOG_CAP = 12`.

## DAILY_COUNCIL_ENABLED

`DAILY_COUNCIL_ENABLED = True` replaces the reactive invention-council fan-out
as the village's primary deliberation surface. Once per in-world day, at the
deterministic boundary `frameTick % DAY_FRAMES == 0`, the engine convenes one
Daily Council if at least `DAILY_COUNCIL_MIN_LIVING = 2` living agents exist.
This is a floor against a one-survivor world, never a subset selector. Ordinary
convening also requires a living, non-incapacitated elder: without an available
elder there is no head/ratifier, so the scheduled meeting defers. The explicit
exception is a lifecycle succession emergency: once a living village has no
living formal elder and `_ensure_succession_election()` has opened a valid
election, an emergency Daily Council convenes on the next
`RULES_TICK_FRAMES` recovery pass rather than waiting for a day boundary. This
emergency may convene with one available survivor because leaving that survivor
leaderless is precisely the condition it must repair.
With the flag off, even a session restored from a mid-meeting save is inert:
attendees resume ordinary scheduling and no council action is offered or
accepted; the legacy invention-council path remains available.

### Attendance, seating, and agenda

Attendance is mandatory for every living agent except an incapacitated/collapsed
agent, who is recorded in `excused` and excluded from the quorum denominator.
Dead agents do not attend. They remain in `self.agents` for lifecycle/history
rendering and are identified by non-null `deathFrame`; attendee-derived state
filters those retained roster entries explicitly. A death during a meeting is
removed from seats and the denominator on the next phase-advance tick. Convening
interrupts each attendee's goal/task, sets `councilTurn`, and directs them to a
deterministic seat. `_assign_council_seats()` puts every attendee around one
circle sized to the full attendance; the elder occupies the distinguished head
seat and everyone else fills clockwise by stable `(role, name)` order. A
leaderless succession council begins with no head seat and stable `(role, name)`
ordering; after election, roster refresh moves the winner into the head seat for
verdict/adjourn. Persisted world-coordinate seats are the sole geometry source
for movement and the viewer.

The session agenda always covers current world status, ongoing/stalled projects,
resource limitations, active rules, ideas/proposals, and agents' feelings about
the village's evolution. The **limitations** topic reports a resource as low
stores only when it is *obtainable* (gather zone, recipe or pending recipe,
structure function that produces it, or — for `coin` only — an active mint) and
**village-wide holdings** (stockpile plus every agent inventory, deliberately
excluding `districtStocks` in-ground deposits, which have their own prompt
channel) are at or below `DAILY_COUNCIL_SCARCITY_THRESHOLD` (default 3). Up to
`DAILY_COUNCIL_SCARCITY_TOPICS` (default 8) scarce ids and active projects are
listed. `EDIBLE_RESERVE` is **not** used for this agenda scan. If
`_invention_required()` is true, the demand appears as an agenda item. A
succession emergency adds `leadership_vacancy`, explicitly
naming every current candidate and directing discussion to compare their
suitability; it does not replace any normal topic. With this flag on,
`_maybe_invention_backstop()` does not
create its 2-3 idle-agent `councilActive` fan-out; its elder-self-draft escape
remains as a deterministic safety net if no assembly produces a blueprint in the
existing timeout path.

### Persisted session and deterministic lifecycle

`civilization["dailyCouncil"]` is `None` outside a meeting. An active session
contains `phase` (`convening`, `discussion`, `proposal`, `voting`, `verdict`, or
`adjourned`), day/frame/timestamp, a frame-derived unique `meetingId`, `trigger`
(`daily` or `succession`), `seats`, `attendees`, `excused`, agenda,
round/maxRounds, speaking order/next index, a full live `transcript`, optional
`ballot`, and optional `verdict`. An ordinary ballot has kind `rule`,
`blueprint`, or `idea`, stable id/title/proposer, per-name yes/no/abstain votes,
and quorum. A succession ballot has kind `succession`, its election id,
candidate list, and per-voter candidate-name/abstain choices. A verdict records
winner, tally, elder ruling (null for the leaderless declaration), and outcome.

`_maybe_advance_daily_council()` makes every transition tick-gated and
deterministic. `DAILY_COUNCIL_DISCUSSION_ROUNDS = 2` bounds round-robin speech;
`DAILY_COUNCIL_PHASE_TTL_FRAMES = STALL_THRESHOLD * 8` guarantees a stuck phase
advances/adjourns; and `DAILY_COUNCIL_SESSION_TTL_FRAMES = STALL_THRESHOLD * 30`
guarantees a stuck meeting closes. Adjourn always clears council-turn state and
writes a record, including an unresolved result when necessary.

### Majority, proposals, and elder ruling

The ballot denominator is the living, non-excused attendee count; quorum is its
strict majority, `(attendees // 2) + 1`. Thus the full available village, not a
voluntary subset, controls the result. `council_propose` may open a rule,
blueprint, or advisory idea ballot; rule and blueprint validation reuse the
existing validators and all their caps. `council_vote` records a yes/no/abstain
vote. A passed rule delegates to `_tally_and_maybe_enact` rather than mutating
`civilization["rules"]` directly. Yes or no reaching quorum resolves normally.
An exact yes/no tie below quorum is the explicit exception: the seated elder's
personal yes/no vote breaks it (default no if the elder is absent or abstains),
and the elder then ratifies that result. For a tied rule ballot, ratification is
represented as one synthetic elder vote passed through `_tally_and_maybe_enact`,
so only the existing enactment path mutates rules. Any non-tied sub-quorum
plurality/abstention result rejects as "no whole-village majority"; abstentions
never lower the threshold. An idea records an outcome but has no direct
mechanical effect.

A succession council arrives with its election ballot already open, retains the
ordinary convening and discussion phases, passes through proposal without
soliciting a competing proposal, and then gives every seated villager one
`council_vote` turn. Each voter names exactly one current candidate or abstains;
the transcript records the choice and the live ballot exposes per-candidate
totals. Completion waits for every eligible attendee or the phase TTL. The
candidate with the most recorded votes wins; an exact tie, including an
all-abstain/no-vote timeout, is broken by lowest stable agent id (seniority).
The village declares this result without elder ratification and calls only
`_enact_succession_winner()` to assign office. Roster refresh then seats the new
elder at the head; they may speak the normal verdict turn before adjourn.

### Two record tiers

Every speech, proposal, vote, and verdict is kept verbatim in the session
transcript and, at append time, in the `state.db` `council_transcript` audit
table documented in [02-engine-core.md](02-engine-core.md). On adjourn the same
frame-derived `meetingId` prevents an emergency session from colliding with an
earlier scheduled meeting on the same in-world day. The same
verbatim transcript is archived in the bounded `councilLog` viewer record
(`DAILY_COUNCIL_LOG_CAP = 12`). Its separate prompt-facing record is a
deterministically derived digest, not an LLM call:

`civilization["councilDigests"]` is a newest-first ring capped at
`DAILY_COUNCIL_DIGEST_CAP = 5`. Each entry records day/frame/timestamp, short
agenda topics, one-line proposal outcomes, verdict (if any), and a bounded mood
folded from transcript feelings. Digests give every later agent bounded
continuity; raw transcript rows remain human/audit data only. Transcript-table
retention keeps the newest 30 meeting ids, as specified in [02](02-engine-core.md).

**Invention safeguards** (deadlock-avoidance backstops, all deterministic):

| Guard | Constant | Behavior |
|---|---|---|
| Approval ceiling | `MAX_APPROVED_CUSTOM = 15` | `_maybe_retire_blueprint`: once reached, retires the oldest *built* custom type from the registry to free a slot. Retirement means the recipe is forgotten; standing structures keep their name/visuals. Code that attaches semantics to a registry entry must tolerate its absence or recreate a minimal entry from a standing instance before attaching them. |
| Resource/recipe ceilings | `MAX_CUSTOM_RECIPES = 12` | `_validate_recipe` rejects new recipe proposals past this. `MAX_CUSTOM_RESOURCES = 10` is **not enforced** — `validate_blueprint` (server.py) ignores the cap by policy; invention is unlimited. |
| Orphan resource retirement | `CUSTOM_RESOURCE_RETIRE_FRAMES = STALL_THRESHOLD * 120` (~40 min) | `_maybe_retire_custom_resource`: prunes `resourceRegistry` + `stockpile` + `districtStocks` for any custom resource unreferenced for the full window; no cap, stamp-on-first-sight clock (ids restored from old saves start their clock at first tick after deploy), retired ids re-inventable (no tombstone). Predicate: `_custom_resource_referenced` (shared obtainability spine via `_resource_is_obtainable`). See approval-ceiling row above for blueprint retirement — standing structures still protect their produce ids even after registry archival. |
| Rejection amnesty | `BLUEPRINT_AMNESTY_FRAMES = STALL_THRESHOLD * 60` (~20 min) | `_maybe_amnesty_rejected_blueprints`: a rejected id is no longer a permanent blacklist — it expires and can be re-proposed. |
| Sage review timeout | `SAGE_REVIEW_TIMEOUT_FRAMES = STALL_THRESHOLD * 20` (~6.7 min) | `_maybe_skip_sage_review`: if no living, non-incapacitated elder exists, a pending review auto-skips rather than blocking forever. |
| Denied-review amnesty | same `BLUEPRINT_AMNESTY_FRAMES` | `_maybe_amnesty_denied_sage_reviews`: a sage-denied proposal is withdrawn and blacklisted (subject to the same rejection amnesty) after the window. |

**`SAGE_REVIEW_ENABLED`** — two-stage blueprint approval: the elder must
`sage_review_blueprint` (a geography/resource sanity pass, verdict
`approved`/`denied`) before `approve_blueprint`/`reject_blueprint` is
accepted on that id. `_is_sage_reviewer` is any agent with `role == "elder"`
(no separate Sage role). Flag-off: `approve_blueprint` behaves exactly as
before (no review gate).

Related actions: `propose_blueprint`, `sage_review_blueprint`,
`approve_blueprint`, `reject_blueprint`, `craft_item` (tier gate) —
[07-actions.md](07-actions.md).

## Library scaling

`LIBRARY_SCALING_ENABLED` defaults to True. The strongest working Library in
the agent's district scales preservation capacity and study gain by its upgrade
weight (`max(1, level // UPGRADE_STAT_STEP)`). The knowledge-capacity
multiplier is capped at 10; the study-gain multiplier is capped at
`LIBRARY_STUDY_WEIGHT_CAP = 5` (max 2.0 skill/session) — uncapped, a
max-level library's 4.0/session equals ~27 practice actions
(`SKILL_PRACTICE_GAIN = 0.15`) and instantly grants a `_skill_bonus` tier,
trivializing skills-by-practice. Prompt lessons are defined in
[03-cognition.md](03-cognition.md).

The final Civic Era is monotonic and requires both a working light structure
and working ocean transit. This makes the environmental and transit effect
kinds load-bearing without requiring lights to be fueled during daytime era
checks.

## RULES_ENABLED

Rule kinds: `RULE_KINDS = {"resource_tax", "custom", "priority"}`
(sim_engine/constants.py:1281), unioned with `{"harvest_quota", "rationing",
"succession"}` when `LIFECYCLE_ENABLED`, and `{"treaty"}` when
`PATH1_DIPLOMACY_ENABLED` (see [10-path1.md](10-path1.md) for treaty
mechanics). `_validate_rule` caps pending at `MAX_PENDING_RULES = 4` and
enacted at `MAX_ACTIVE_RULES = 8`.

**Treaty proposals (`kind: "treaty"`).** Reuse the shared propose/vote
scaffold via `propose_treaty`/`vote_treaty` ([07-actions.md](07-actions.md)).
The `rule` object requires `id` and `name`; optional fields include `value`
(trade pact label, default `"trade"`), `description`, and **`tariff`** — a
fraction `0`–`0.25` (default `0`) applied on caravan delivery: the tariff
share credits the source settlement store (or village `stockpile` when
unset); the remainder credits the destination store
([10-path1.md](10-path1.md#treaty-tariffs)). `_validate_rule` rejects
out-of-range `tariff` values; `_propose_treaty` copies `tariff` onto the
pending entry and enacted `civilization["treaties"]` record. Council
`council_propose` with `kind: "rule"` may include the same `tariff` field when
opening a treaty ballot.

**Effectful custom rules.** A `kind: "custom"` proposal may include one safe
`effect` object; arbitrary code, expressions, and free-form selectors are
never evaluated. Its grammar is:

```json
{
  "subject": {"resource" | "role" | "district" | "action": "<whitelisted id>"},
  "condition": {"action": "collect_resource|contribute_resources|craft_item",
                "resource"?: "<known resource>", "role"?: "<known role>",
                "district"?: "<known live district>"},
  "modifier": {"kind": "add", "value": 1}
}
```

`subject` has exactly one selector. `condition.action` is required unless the
subject itself is `action`; optional condition selectors further narrow the
match. District selectors may name any current live district (including a
non-buildable forest, market, beach, cave, or ocean district). Selector values
must be current registry ids and a subject/action pair
must name one of the three supported downstream computations. The sole
modifier is bounded integer addition (`1..3`): it adds units to a matching
collect, contribution, or craft output. `_validate_rule` normalizes this
grammar, and `_apply_governance_rule` compiles enacted effects into the
persisted `customRuleModifiers` lookup. The three computations query that
lookup deterministically; `_clear_governance_rule` removes an entry on repeal
or supersession.

**Propose → vote → enact:** `propose_rule` validates and appends to
`pendingRules` with the proposer's own `"yes"` vote pre-cast, then calls
`_tally_and_maybe_enact` (sim_engine/mixin_crafting_rules.py:498) immediately (so a lone
proposer can pass a rule alone if quorum is 1). `vote_rule` adds a vote and
re-tallies. Quorum = `(active_agent_count // 2) + 1`
(`_vote_quorum`, sim_engine/mixin_crafting_rules.py:246). Reaching `yes ≥ quorum` enacts (moves
into `civilization["rules"]`, stamps `enactedFrame`, applies mechanical
effect via `_apply_governance_rule`); `no ≥ quorum` rejects and discards.
`harvest_quota` and `rationing` get real teeth once enacted: `harvest_quota`
writes `harvestQuotas[id] = {"value": N}` (gather cap per resource per
district per `HARVEST_QUOTA_PERIOD_FRAMES = STALL_THRESHOLD * 3` ≈5 min);
`rationing` writes `rationingActive[id] = {"value": N}`
(`RATIONING_WITHDRAW_CAP = 3` default, checked at withdrawal time by
`_rationing_gate`, and only actually restricts while village storage
utilization is below `RATIONING_STORAGE_LOW_RATIO = 0.5` — it self-lifts
once storage recovers).

**Constitution.** `civilization["constitution"]` is a persisted, ordered
ledger of enacted ongoing rules. A provision records its rule id, name, kind,
description, effect (when any), `enactedFrame`, and status (`"active"`,
`"superseded"`, or `"repealed"`). It is rendered in the think payload and
the read-only viewer. An ordinary enactment appends an active provision. An
amendment supplies `supersedes: "<active rule id>"`: validation requires that
target to be active, enactment clears/removes the target's live effect, marks
its provision superseded with `supersededBy`, then appends the new active
provision. It therefore replaces a provision without exceeding the same
active-rule budget of eight. Repeal clears/removes the target's live effect
and marks its active provision repealed; it does not automatically revive an
older superseded provision. Old saves derive active provisions from their
ordered `rules` list and rebuild the compiled custom-effect lookup on restore.
The same active-target and projected-`MAX_ACTIVE_RULES` checks run again at
enactment under the engine lock: if a pending amendment loses its target, or a
pending ordinary rule loses its budget slot, its passed ballot is discarded as
rejected without mutating effects or the constitution.

**Rule ids are globally non-reusable** (`_ensure_constitution`,
sim_engine/mixin_crafting_rules.py:350): `_validate_rule` (sim_engine/mixin_crafting_rules.py:419) rejects any id already present in
`civilization["constitution"]` regardless of status, so a repealed id can
never be re-enacted under the same id. The deterministic priority-rule
auto-proposer in `_maybe_advance_rules` therefore mints a **unique
per-enactment instance id** — `priority_<resource>_<token>`, where `<token>`
is a compact lowercase base36 counter (`civilization["priorityRuleSeq"]`,
via the shared `_next_rule_seq_token(counter_key)` helper, kept as the
back-compat wrapper `_next_priority_rule_seq_token()`) rather than the raw
frame number, so the id stays short indefinitely on a long-running/24-7
server instead of eventually exceeding `SLUG_RE`'s 25-character cap.
`_active_priority_resource` matches enacted priority rules by
`kind == "priority"` and `value` (the resource), never by id, so unique
instance ids do not affect priority-rule lookup or mutual exclusion. The
branch also pre-validates the candidate rule (`_validate_rule`) before
calling `propose_rule`, so a known-invalid candidate (e.g. the pending/active
rule budget is already full) is skipped rather than knowingly emitted as an
invalid action.

The same defect and fix apply to the auto-proposed **resource tax**: an
LLM-driven `repeal_rule` can target the tax even though the deterministic
repeal backstop deliberately excludes it (see "Anti-oscillation guard"
below), so a repealed `"resource_tax"` id could otherwise never be
re-enacted. The tax auto-proposer therefore mints
`resource_tax_<token>` using the same shared counter helper with its own
persisted field, `civilization["taxRuleSeq"]`, and pre-validates the
candidate before proposing, mirroring the priority-rule branch exactly.
`_active_resource_tax` matches by `kind == "resource_tax"`, never by id, so
this is safe for every consumer (belief-affinity sets, `RULE_KINDS`, the
vote-bias heuristic, etc. all key off `kind`, not the literal id).

**A third auto-proposal branch — emergency storm rationing
(`WEATHER_GOVERNANCE_ENABLED`, living-ecosystem Phase 5).** Checked first in
`_maybe_advance_rules` (ahead of the priority/tax branches above), but only
ever acts when the condition is real: while `civilization["weather"]["state"]`
is `"storm"` or `"clearing"` AND at least one of the storm's named districts
(`weather["districts"]`) has an ecology ratio (`_district_ecology_ratio`)
below `STOCK_LOW_RATIO`. Reuses an **existing** `RULE_KINDS` entry —
`"rationing"` (already gated `LIFECYCLE_ENABLED`, default True) — never a new
kind; enacting it caps stockpile withdrawals via the same
`_apply_governance_rule`/`rationingActive` mechanism an LLM-proposed
rationing rule would use. Mints a unique per-enactment id,
`emerg_<token>`, via the same shared `_next_rule_seq_token(counter_key)`
helper and its own persisted counter field, `civilization["emergencyRuleSeq"]`
— identical rationale to `priorityRuleSeq`/`taxRuleSeq`: rule ids are
globally non-reusable, so a deterministic id would recreate the exact
permanently-blocked-id loop fixed in `6a78162` the first time this rationing
rule is ever repealed. Pre-validates (`_validate_rule`) before proposing and
advances `lastRuleAttemptFrame` on a known-invalid candidate instead of
knowingly emitting an invalid action, mirroring the priority/tax branches
exactly. Governed by the **same** `RULE_PROPOSE_COOLDOWN`/
`lastRuleAttemptFrame` gate as every other branch in this function (checked
once, above all three branches) — the emergency branch cannot fire more often
than the ordinary ones. A dedicated `_active_or_pending_rationing()` guard
additionally skips the branch entirely while a rationing rule (auto-proposed
or LLM-authored) is already active or awaiting a vote, so a storm that
lingers for its full `WEATHER_DWELL_TICKS` window proposes at most **one**
emergency measure rather than re-proposing every cooldown window — the
governance-churn safeguard the plan calls for (`MAX_PENDING_RULES = 4` is
shared budget with ordinary priority/tax proposals). On enactment, the
`emerg_` id prefix is detected in `_tally_and_maybe_enact` (the pendingRules
entry `_propose_rule` builds only whitelists
id/name/kind/value/description/proposedBy/enacted/votes(+effect/supersedes),
so an arbitrary extra key on the input rule dict would not itself have
survived to the enacted copy) and pushes a `"emergency_measure"` chronicle
milestone (added to `CHRONICLE_MILESTONE_KINDS`) — the village's disaster
response becomes recorded history, same pattern as the Phase 1
disaster/district_founded milestones. `civilization["emergencyRuleSeq"]` is
restore-safe (`setdefault`-backfilled to `0`), same precedent as
`priorityRuleSeq`/`taxRuleSeq`. `WEATHER_GOVERNANCE_ENABLED` off (or
`WEATHER_ENABLED`/`LIFECYCLE_ENABLED` off): the branch is a complete no-op —
`_maybe_advance_rules` behaves exactly as Phase 4 alone.

**Constitution history cap.** Unlike the live `MAX_ACTIVE_RULES` (8) budget,
`civilization["constitution"]` is an append-only historical ledger with no
cap of its own; unique priority-rule instance ids mean long soaks of
enact/repeal cycles would otherwise grow it unboundedly. `_ensure_constitution`
trims the ledger to `MAX_CONSTITUTION_HISTORY = 200` rows once exceeded,
dropping the oldest **non-`"active"`** rows first (chronological order is
preserved); every currently-`"active"` provision is always kept regardless
of age or count, so a live law can never be silently dropped by the trim.

**Failed-proposal cooldown.** `_propose_rule` returns early on a validation
failure (invalid rule shape, colliding id, budget full, etc.) *before* the
success-only `c["lastRuleActivityFrame"] = self.frameTick` line, so that
field alone cannot be used to gate retries after a failure — it also
backstops blueprint-stall detection elsewhere and must only reflect real
governance activity. A separate `civilization["lastRuleAttemptFrame"]`
advances on every `propose_rule` attempt, success or failure, and the
`_maybe_advance_rules` cooldown guard checks
`max(lastRuleActivityFrame, lastRuleAttemptFrame)` against
`RULE_PROPOSE_COOLDOWN`. This keeps a rejected auto-proposal on the full
~50s cooldown instead of retrying every `RULES_TICK_FRAMES` (~5s) window —
a 10x amplification that previously spammed `"<elder> drafted an invalid
rule"` into the activity log whenever the priority-rule id was permanently
blocked (the same 10x amplification and permanently-blocked-id risk applied
to the resource-tax auto-proposer once its id was uniquified, since it shares
this same cooldown guard). All new fields — `lastRuleAttemptFrame`,
`priorityRuleSeq`, `taxRuleSeq` — are restore-safe (`setdefault`-backfilled to
`0`/`None` on load; a pre-fix save simply resumes as if no attempt/enactment
had yet happened).

**`repeal_rule`** action → `_propose_repeal` (sim_engine/mixin_crafting_rules.py:687): opens a
new pending ballot (kind `"repeal"`, id `repeal_<target>`) reusing the same
vote/quorum scaffold; `_enact_repeal` removes the target from
`civilization["rules"]`, marks its constitution provision repealed, and
reverses its governance effect (`_clear_governance_rule`) on success.

**Anti-oscillation guard** (implemented 2026-07-12; the archived
`docs/archive/rule-oscillation-fix-plan.md` describes the incident this
fixed — this section is the current, load-bearing behavior). The
deterministic elder backstop `_maybe_advance_rules` (sim_engine/mixin_council_growth.py:1519,
runs on `RULE_PROPOSE_COOLDOWN = 1500` ticks ≈50s cooldown when nothing is
pending) has a "keep village law lean" branch that proposes repealing the
oldest non-tax rule once ≥2 rules are active — but only rules eligible by
`RULE_REPEAL_MIN_AGE_FRAMES = RULE_PROPOSE_COOLDOWN * 4` (≈3.3 min since
`enactedFrame`) are candidates (sim_engine/mixin_council_growth.py:1654-1660). Without this age
floor, the normal tax+priority two-rule steady state caused the repeal
branch to fire the very next cooldown window after the propose branch
enacted the priority rule, undoing it immediately and oscillating
propose/repeal forever. The floor lets a freshly-enacted rule stand for
several cooldown cycles before it becomes eligible for this "exercise
amendment" repeal, breaking the loop. This guard governs every ongoing
non-tax rule, including an effectful `custom` rule, but only the
*deterministic backstop's own repeal proposals*; an LLM-driven
`repeal_rule` call is unaffected and can target any enacted rule at any
time.

Related actions: `propose_rule`, `vote_rule`, `repeal_rule` —
[07-actions.md](07-actions.md).

## Succession (LIFECYCLE_ENABLED, governance slice)

On the elder's natural death, `_start_succession_election()` opens one
pending `"succession"` rule per eligible adult candidate (deterministic
nomination, capped at `max(2, MAX_PENDING_RULES)` candidates), tagged with a
shared `electionId`. If no non-incapacitated adult exists, nomination falls
back to any living, non-incapacitated villager; if every survivor is
incapacitated, election creation defers until someone can safely serve.
With `DAILY_COUNCIL_ENABLED`, these rule-shaped entries are persistence and
winner-enactment records; visible deliberation and voting occur through the
emergency Daily Council described above, and the ordinary
`_maybe_advance_rules()` backstop never manufactures candidate support. With
the council flag off, legacy `vote_rule` remains available and its exclusivity
logic makes a "yes" on one candidate an implicit "no" on the others. Succession
never appends to `civilization["rules"]` and does not consume the
`MAX_ACTIVE_RULES` budget. If council cognition fails, its phase/session TTLs
still finish discussion and voting; the recorded plurality/seniority rule is
the deterministic no-vote fallback. If no Daily Council is available, the
election deadline `SUCCESSION_ELECTION_TTL_FRAMES = STALL_THRESHOLD * 8`
(≈13 min) retains the legacy bounded resolver. If a winner died or collapsed
during the window, a fresh election reopens among the remaining candidates
rather than crowning a corpse. State fields
(`age`, `deathFrame`, etc.) are documented in
[06-agents.md](06-agents.md); this section is the election mechanics only.

`_ensure_succession_election()` runs on the existing `RULES_TICK_FRAMES`
cadence whenever lifecycle is enabled. A living village with no living agent
whose formal role is `"elder"` must have one structurally valid election. A
valid election has a bounded deadline, a unique non-empty candidate list,
exactly one matching succession ballot per candidate under the same
`electionId`, and only candidates who remain living and non-incapacitated.
Missing state, orphaned/mismatched ballots, corrupt metadata, or an ineligible
candidate causes the old succession ballots to be replaced by one fresh
deterministic election. A valid election is left untouched, so the recovery
gate cannot reset its deadline or spam elections. If a living formal elder
exists, even while temporarily incapacitated, succession does not begin:
Sage emergency/recovery retains authority, and any stray succession state is
cancelled so a late ballot cannot create two elders. Expired but otherwise
valid elections are not restarted: the Daily Council phase/session TTL owns
completion while enabled, and only the flag-off legacy path uses the direct
election-deadline tiebreak.

## MEMES_ENABLED

Seed memes (`harvest_spirit` and rival `river_spirit`, `MEME_SEED_IDS`) give
a new village two starting points, but are ordinary live beliefs rather than a
closed catalogue. Any agent may take `found_belief` at any time with `{id,
name, tenet, affinity}`. Ids use the normal slug rule; names and tenets are short bounded text;
`affinity` is a bounded subset of `RULE_KINDS`. `MAX_BELIEFS = 6` caps the
live registry, including the seeds. Beliefs, their author, and affinities live
in `civilization["beliefRegistry"]`, so they persist with state.db; legacy
seed text/affinities remain the compatible fallback if an old save has no
registry.

The resolved Phase-3 mix ships as three **authoring exemplars** in
`BELIEF_ARCHETYPES`: `forest_steward` (practical), `egalitarian` (political),
and `dreamwalker` (outlier). They are supplied in the prompt/catalog but are
not pre-adopted or inserted into `beliefRegistry`: this preserves the existing
competing dual-seed opening and leaves the live `MAX_BELIEFS` budget open for
agent authorship. Agents may use an exemplar exactly, adapt it, or author an
unrelated belief.

There is no periodic proximity-conversion roll. The retained
`_spread_beliefs_by_proximity` tick hook performs no conversion; adjacent
mixed-belief pairs are exposed in think payloads so the holder can use
`talk_to_nearby`. A talk can carry a `belief_pitch` object identifying one
belief and its pitch text. `_maybe_spread_beliefs` evaluates that pitch through
`run_belief_pitch` when Ollama is available. The resulting `quality`, the
speaker/listener relationship, and the listener's current beliefs determine
the conversion chance. Both the scorer and engine require the target to be in
the existing 80px nearby-talk radius; ordinary distant `talk_to_nearby` still
moves/delivers as before, but cannot score or convert a belief until contact.
Calls are bounded by `BELIEF_PITCH_SESSION_CAP = 30`,
following the mutation-session-cap pattern; unavailable, malformed, or
over-budget LLM scoring uses the deterministic `BELIEF_FALLBACK_QUALITY`
instead, keeping offline behavior reproducible. A successful adoption is
logged, messaged, remembered, and added to the chronicle.

The engine increments the cap under its lock when it applies a scored pitch.
Because scoring follows an already-dispatched decision request outside that
lock, concurrent workers can race on a stale remaining-budget value; at most
`MAX_CONCURRENT_LLM` (3) surplus model score calls can occur, and scores that
arrive after the cap are ignored without changing belief state.

Beliefs have mechanical consequences beyond votes. Their affinity continues
to bias `_belief_biased_vote`; believers prefer matching projects when choosing
the role-default project and co-believers receive a reciprocal relationship
bonus on adoption/persuasion. `HARVEST_SPIRIT_CONTRIB_BOOST = True` remains a
small compatible food-contribution tilt. `meme_adoption` benchmarks include
all live beliefs with a per-belief holder breakdown, including authored
beliefs.

A successful adoption, gated additionally by `CULTURE_ENABLED`, also rolls a
chance to drift the spreading belief's wording. `_maybe_mutate_meme` fires
after the recipient adopts and before the adoption is logged/messaged/
remembered, so the recipient's own memory of the belief already carries any
drifted text — the mutation is one probability roll (`MEME_MUTATION_PROB`)
against a hard, process-lifetime `MEME_MUTATION_SESSION_CAP = 30`, the same
budget discipline as the pitch-scoring cap above, and each attempt makes at
most one `lm_complete` call to reword the current text. A failed, empty, or
rejected (meta-commentary, echoed instructions, no real change) rewrite is a
silent no-op; the belief keeps its prior text. A successful mutation rewrites
the live `beliefRegistry` entry's `tenet` in place — preserving the
pre-mutation wording once in `originalTenet` via `setdefault`, so repeated
drift never loses the true original and authorship (`authoredBy`) is left
untouched — and only falls back to writing `civilization["memeTexts"]` when
the belief has no registry entry at all. `_belief_text` still resolves a
registry `tenet` first; `memeTexts` is read only as a legacy fallback for
entries with no tenet, e.g. when restoring an old save. Each successful
mutation is logged as activity and recorded in the chronicle under the
`meme_mutation` kind, and increments `civilization["memeMutations"]`, which
also feeds the `meme_mutations` benchmark.

## CULTURE_ENABLED

**Skills:** `SKILL_KINDS = ("gather", "craft", "build", "heal", "reflection")`, one float
level `0..SKILL_MAX_LEVEL = 10.0` per verb, rising
`SKILL_PRACTICE_GAIN = 0.15` per successful practice (deterministic, no
roll). Feeds a small yield/output bonus every `SKILL_BONUS_DIVISOR = 4.0`
levels (`SKILL_HEAL_BONUS_PER_LEVEL = 0.6` extra health per heal-skill
level, applied directly rather than via the divisor). A completed PIANO
Reflection report also practices `reflection`; it has no yield bonus.

**Teaching:** a `talk_to_nearby` message containing a teach-intent keyword
(`TEACH_KEYWORDS`: teach/train/"show you how"/apprentice/mentor) plus a
recognized skill kind transfers `TEACH_TRANSFER_FRACTION = 0.3` of the
speaker's level to the recipient — deterministic keyword check, no new
action verb.

**Library:** a seed station structure; while working, persists a dying
agent's best skill (capped `LIBRARY_KNOWLEDGE_CAP = 12` entries, oldest
retires first) so studying agents can still learn it
(`LIBRARY_STUDY_GAIN = 0.4` per session, via `_maybe_study_at_library`).

**Chronicle:** a capped ring (`CHRONICLE_CAP = 100`) of major village
events, folded into prompts as one "Village history: ..." line
(`CHRONICLE_PROMPT_ENTRIES = 3` most recent, sliced independently of
`CHRONICLE_CAP` — raising the ring size never changes prompt length).
`CHRONICLE_ENABLED` is a viewer-projection gate (default True): when enabled,
`/state` adds a bounded top-level `chronicle` projection of that existing
ring. It never creates a second event store and never changes prompt history.
The projection admits only the named milestone kinds `death`, `burial`,
`election`, `belief_founded`, `belief_adoption`, `meme_mutation`,
`knowledge_preserved`, `disaster`, `district_founded`, `emergency_measure`,
and `divine`; routine gather, talk, craft, and build activity remains
exclusively in `activity`. `disaster` entries are pushed unconditionally from
`_maybe_disaster` (see [08](08-systems-economy.md)); `district_founded`
entries are pushed from `_found_district` only when `FOUNDING_EVENTS_ENABLED`
is True (see [05](05-world.md)). `CHRONICLE_CAP` was raised from 20 to 100
(living-ecosystem Phase 2, item 0) after live verification showed a
storm-heavy stretch (`DISASTER_PROB` fires roughly every 100 simulated
minutes) evicting real history (deaths/elections/beliefs) within about a day
at the old cap; 100 entries absorbs many more disasters before crowding out
anything else, at a negligible cost (~80 extra short strings in `/state` and
`state.db`).

**Divine communication (Sovereign God mode, `GOD_MODE_ENABLED`):** an applied
`proclamation` (which auto-applies as timed providence) or standalone
`providence` command pushes to all three of `activity`,
`conversationLog` (`kind="divine_proclamation"`/`"divine_providence"`), and
Chronicle (`kind="divine"`) in one call, each entry carrying explicit
`source="divine"` attribution so it never masquerades as emergent agent
speech. `providence`'s expiry and `revoke_guidance` targeting it additionally
push one plain `activity` line ("fades"/"is revoked") with no matching
Chronicle/communication duplication.

**Voice adherence (`divine_response`).** When an agent's think records a valid
or synthesized `divine_response` against active binding guidance, the engine
appends one `conversationLog` entry (`kind="divine_response"`,
`source="divine"`, `from` = agent name, `to` = `"divine"`, `message` =
`"{stance}: {reason}"`, `outcome` = the applied `action`) and one plain
`activity` line summarizing stance + agent + guidance kind (e.g. "Ash
continued private guidance: …"). These are **public** audit surfaces — they
expose adherence stance and the agent's stated reason, never the private omen
text itself. Synthetic `missing_divine_response` records use the same shape
with `synthetic: true` in the divine-response log ([02](02-engine-core.md)).
No Chronicle milestone — routine adherence stays in `activity`/
`conversation.jsonl`.

**Private omens are the deliberate opposite for guidance text:** a
`private_omen` apply, replace, expiry, or revocation never writes the omen
text to `activity`, `conversationLog`, or the Chronicle under any
circumstance — the only place its content is ever readable outside the
target's own (eventual, exactly-once) memory is the authenticated
`/control/god/sight` route (see [02](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance)
and [06](06-agents.md)). `snapshot()`'s `god.recentPublicInterventions` is
filtered to `"public": True` records for the same reason — a private omen's
outcome record is written with `"public": False` and is excluded from
`/state` by that filter, not merely by omission.

**Storyteller events (Sovereign God mode Phase 5, `story_event`):** a
**public** event pushes the same `activity`/`conversationLog`
(`kind="divine_story_event"`)/Chronicle trio, `source="divine"`, using its
title and narration (`"{title}: {narration}"` in the Chronicle entry); a
**private** event (bound to one living `targetId`) writes none of those,
matching the private-omen visibility boundary exactly — its title/narration
never reach public `activity`, `conversationLog`, `/state`, or the
Chronicle, only the authenticated `/control/god/sight` route. Any embedded
`agent_vitals`/`grant_resource`/`structure_condition` primitive still writes
its own **public** activity/communication/Chronicle line regardless of the
parent event's visibility — Phase 4 miracles have no private-visibility
concept of their own — so a "private" story event's narrative framing can
stay hidden while a primitive it triggers remains an observable public
event, same as it would applied standalone. Expiry and cancellation each
push one plain `activity` line ("A divine story fades: ..." /
"A divine story is cut short: ...") for public events only, with no matching
Chronicle/communication duplication, mirroring providence's own
expiry/revocation narration. See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-5--storyteller-events-and-timed-lawgiver-modifiers)
for the full command/composition/closure contract.

**Social ties:** `SOCIAL_LAYER_ENABLED` (default True) is another read-only
viewer gate. `/state.socialTies` is a compact, deduplicated list of non-neutral
relationships between living agents, shaped as `{from, to, valence}` where
`from` and `to` are agent ids and `valence` is `ally` or `rival`. A reciprocal
disagreement resolves conservatively to `rival`. The browser uses this
authoritative projection only to render nearby relationship cues; it does not
derive or mutate social state.

## Bounded agent conflict (`confront_agent`)

Deterministic, opt-in agent-vs-agent friction — not a combat minigame and not
a free-for-all raid system. Full action params and `apply_decision` effects:
[07-actions.md](07-actions.md). Pair cooldown persistence:
[06-agents.md](06-agents.md).

**Design intent.** Conflict resolves scarce-food pressure and existing social
tension without introducing always-on PvP. Most agent pairs never qualify.

### Social gate

`confront_agent` appears in `available_actions` only when the actor can name a
valid target and at least one **authorization** holds:

| Authorization | Condition |
|---|---|
| Rivalry | Actor's `relationships[targetName] == "rival"` (seller-side opinion semantics — the actor must personally hold the rival tie toward the named target). |
| Path-1 pressure context | `path1_on("PRESSURE_LOOP_ENABLED")` **and** (`_is_night()` with actor unsheltered **or** actor was startled by Path-1 forest wildlife within `CONFRONT_PRESSURE_WINDOW_FRAMES = STALL_THRESHOLD * 2` — i.e. recent `lastNightNote` / wildlife-attack frame). |

Neutral and ally pairs **reject** at validation (`normalize_decision` /
`apply_decision`) even if the action were forced. Sage (`role == "elder"`) is
never a valid target — attempts log a rejection note and do nothing.

### Resolution (contact range)

Contact radius `CONFRONT_CONTACT_DIST = 80` px (matches heal/bury/trade
adjacency). Out of range: action sets movement toward target (same pattern as
`trade_resource` / `heal_agent`).

### Divine Matrix Phase 5: decision-gate attribution

Compelled, possessed, and veto-resolved actions that mutate the world must not
read as emergent agent initiative. The engine writes explicit
`source="divine"` entries to `conversationLog` (kinds like `divine_compulsion`,
`divine_possession`, `divine_veto_hold`, `divine_veto_resolve`) and to
`chronicle` for consequential actions (not plain `rest` / `talk_to_nearby`).
Routine `apply_decision` activity lines may still describe the mechanical
outcome; divine attribution is additive via the communication/chronicle path
(same discipline as `agent_vitals` / `grant_resource` in Phase 4).

**Anointed (Phase 7):** destiny and oracle hints are cognition-only — they
must not appear in `activity`, `conversationLog`, or `chronicle`. Stigmata tags
are folded into neighbor **prompt** text only (`format_nearby_agents`); they
are not broadcast proclamations and must not masquerade as emergent social
status on `/state`.

On contact, deterministic order:

1. **Damage** — subtract `CONFRONT_DAMAGE = 10` from target `health`.
   - **Non-lethal default:** clamp so target `health` never drops below
     `CONFRONT_INCAP_HEALTH = 1` (mirrors God vitals floor — cannot
     incapacitate a healthy target in one swing).
   - **Lethal exception:** if target `health` was already `<=
     CONFRONT_LETHAL_THRESHOLD = 15` before damage, allow `health` to reach `0`
     and flip `incapacitated = True` through the ordinary survival path — no
     instant `_agent_dies` / permanent death.
2. **Steal (optional)** — if target holds any edible above `EDIBLE_RESERVE`,
   transfer `1` unit of the target's most-abundant edible (`food`/`fish`/`meat`)
   to the actor (carry-cap overflow routes to village stockpile like any other
   transfer).
3. **Flee** — actor retargets ~`CONFRONT_FLEE_DIST = 60` px away from target
   (short disengage, not map-wide flight).
4. **Social hit** — if relationship was neutral, set actor→target to `rival`;
   if already `rival`, leave unchanged (reinforced by activity copy only).
5. **Cooldown** — write `civilization["confrontCooldowns"][pairKey] =
   frameTick + CONFRONT_COOLDOWN_FRAMES` where `CONFRONT_COOLDOWN_FRAMES =
   STALL_THRESHOLD * 4` (~4 min real time) and `pairKey` is
   `"<minId>:<maxId>"`.

**Activity + memory.** One activity line (e.g. confrontation + optional steal);
optional `_push_memory` on both parties with salience proportional to outcome.
No Chronicle milestone — routine conflict stays in `activity.jsonl`.

**Explicit non-goals.** No multi-agent brawls, no structure damage, no
settlement raids, no confronting incapacitated or dead agents, no bypass of
Sage emergency responder exemption (actors in `_sage_responders()` reject).

**Personality drift:** major life events (collapse, etc.) append one short
deterministic trait clause to an agent's persona text, capped at
`PERSONALITY_DRIFT_CAP = 3` clauses so a long-lived elder's persona doesn't
run on unbounded.

## AGENT_MESSAGING

A simple per-agent inbox: `_deliver_message(from, to, text, kind)`
(sim_engine/mixin_backstops.py:38) appends `{from, text, kind, frame}` to every matching
recipient's `inbox` (broadcast when `to` is `"everyone"`/`"all"`/`None`),
trimmed to `INBOX_CAP = 6` most-recent entries. `_drain_inbox(agent)`
(called once per think-payload build) folds the inbox into the prompt as a
single joined line and clears it — messages are consumed exactly once, on
the recipient's next think. `_has_unread(agent)` also gates `USE_GOALS`
(an unread message interrupts an in-progress goal so the agent responds
promptly — see [08-systems-economy.md](08-systems-economy.md)).

## BENCHMARKS_ENABLED

`_sample_benchmarks()` (sim_engine/mixin_decisions.py:53) runs on
`BENCHMARK_TICK_FRAMES = 600` (20s) plus once at `FIRST_BENCHMARK_FRAME =
60`. Always samples: role-specialization entropy (Shannon entropy over
role counts), rule adherence (tax paid/due ratio), meme adoption count +
rate + per-meme breakdown, active rule count, structure count, memory-store
size, effect throughput (`STRUCTURE_EFFECTS_ENABLED`), ecology scarcity
index (`ECOLOGY_ENABLED`), role-rebalance latency (`EMERGENT_ROLES`), rule
kind diversity (`RULES_ENABLED`); plus era name/tech tier
(`TECH_TREE_ENABLED`), module-total (`PIANO_MODULES`/`META_SYSTEM`). Each
metric is written to `SessionLogger`'s `benchmarks` stream via
`_log_benchmark` — see [12-ops.md](12-ops.md) for the JSONL sink.
