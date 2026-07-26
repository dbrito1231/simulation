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
tech is the granary and cart). `_village_tech_tier()` (sim_engine.py:2629)
= the highest `unlocks.tier` among built, *working* station structures
(floor 1; capped `MAX_TECH_TIER = 3`). Proposing/crafting/starting tier-T
tech requires village tier ≥ T; every refusal names the deterministic
escape (`_tier_gate_reason`) — the tier-T station is itself buildable one
tier lower (e.g. the Forge, tier-2 unlock, is plain tier-1 tech).

**Era ladder** (`ERA_LADDER`, sim_engine.py:565): Founding → Craftsman
(working craft station) → Forge (working tier-2 station) → Wagon (a
cart/wagon in village hands) → (`TIER3_CONTENT_ENABLED`) Harbor → Mill.
`_maybe_era_transition()` (sim_engine.py:2723) is tick-gated and monotonic
— a broken Forge never un-names the era — and logs/benchmarks (`era`) on
advance.

**Legacy invention council (only while `DAILY_COUNCIL_ENABLED` is off).** When `_maybe_invention_backstop()`
(sim_engine.py:7191) fires — `_invention_required()` has held true for
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
the village's evolution. If `_invention_required()` is true, the demand appears
as an agenda item. A succession emergency adds `leadership_vacancy`, explicitly
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
| Resource/recipe ceilings | `MAX_CUSTOM_RESOURCES = 10`, `MAX_CUSTOM_RECIPES = 12` | `_validate_blueprint`/`_validate_recipe` reject new proposals past these. |
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
(sim_engine.py:432), unioned with `{"harvest_quota", "rationing",
"succession"}` when `LIFECYCLE_ENABLED`, and `{"treaty"}` when
`PATH1_DIPLOMACY_ENABLED` (see [10-path1.md](10-path1.md) for treaty
mechanics). `_validate_rule` caps pending at `MAX_PENDING_RULES = 4` and
enacted at `MAX_ACTIVE_RULES = 8`.

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
`_tally_and_maybe_enact` (sim_engine.py:4891) immediately (so a lone
proposer can pass a rule alone if quorum is 1). `vote_rule` adds a vote and
re-tallies. Quorum = `(active_agent_count // 2) + 1`
(`_vote_quorum`, sim_engine.py:4826). Reaching `yes ≥ quorum` enacts (moves
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

**`repeal_rule`** action → `_propose_repeal` (sim_engine.py:5008): opens a
new pending ballot (kind `"repeal"`, id `repeal_<target>`) reusing the same
vote/quorum scaffold; `_enact_repeal` removes the target from
`civilization["rules"]`, marks its constitution provision repealed, and
reverses its governance effect (`_clear_governance_rule`) on success.

**Anti-oscillation guard** (implemented 2026-07-12; the archived
`docs/archive/rule-oscillation-fix-plan.md` describes the incident this
fixed — this section is the current, load-bearing behavior). The
deterministic elder backstop `_maybe_advance_rules` (sim_engine.py:7605,
runs on `RULE_PROPOSE_COOLDOWN = 1500` ticks ≈50s cooldown when nothing is
pending) has a "keep village law lean" branch that proposes repealing the
oldest non-tax rule once ≥2 rules are active — but only rules eligible by
`RULE_REPEAL_MIN_AGE_FRAMES = RULE_PROPOSE_COOLDOWN * 4` (≈3.3 min since
`enactedFrame`) are candidates (sim_engine.py:7676-7678). Without this age
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

**Chronicle:** a capped ring (`CHRONICLE_CAP = 20`) of major village
events, folded into prompts as one "Village history: ..." line
(`CHRONICLE_PROMPT_ENTRIES = 3` most recent).

**Personality drift:** major life events (collapse, etc.) append one short
deterministic trait clause to an agent's persona text, capped at
`PERSONALITY_DRIFT_CAP = 3` clauses so a long-lived elder's persona doesn't
run on unbounded.

## AGENT_MESSAGING

A simple per-agent inbox: `_deliver_message(from, to, text, kind)`
(sim_engine.py:6373) appends `{from, text, kind, frame}` to every matching
recipient's `inbox` (broadcast when `to` is `"everyone"`/`"all"`/`None`),
trimmed to `INBOX_CAP = 6` most-recent entries. `_drain_inbox(agent)`
(called once per think-payload build) folds the inbox into the prompt as a
single joined line and clears it — messages are consumed exactly once, on
the recipient's next think. `_has_unread(agent)` also gates `USE_GOALS`
(an unread message interrupts an in-progress goal so the agent responds
promptly — see [08-systems-economy.md](08-systems-economy.md)).

## BENCHMARKS_ENABLED

`_sample_benchmarks()` (sim_engine.py:7713) runs on
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
