# SPEC 09 — Systems: Society

Flag semantics for governance and culture: the tech tree and invention
pipeline, the rules/voting system (including the anti-oscillation guard),
memes, culture (skills/teaching/library/chronicle), messaging, benchmarks,
and the governance-specific slice of lifecycle succession.

**Canonical for:** `TECH_TREE_ENABLED`, `SAGE_REVIEW_ENABLED`,
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

**Invention council** (plan Part 6): when `_maybe_invention_backstop()`
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

On the elder's natural death, `_start_succession_election()`
(sim_engine.py:5569) opens one pending `"succession"` rule per eligible
adult candidate (deterministic nomination, capped at
`max(2, MAX_PENDING_RULES)` candidates), tagged with a shared
`electionId`. Villagers vote via the ordinary `vote_rule` action;
`_vote_on_rule`'s exclusivity logic makes a "yes" on one candidate an
implicit "no" on the others in the same election. `_tally_and_maybe_enact`
detects `kind == "succession"` and routes to `_enact_succession_winner`
instead of appending to `civilization["rules"]` — succession ballots are a
leadership record, not an ongoing governance rule, and don't consume the
`MAX_ACTIVE_RULES` budget. The election auto-decides via
`_resolve_succession_tie` if no candidate reaches quorum within
`SUCCESSION_ELECTION_TTL_FRAMES = STALL_THRESHOLD * 8` (≈13 min). If the
winner died or collapsed during the window, a fresh election reopens among
the remaining candidates rather than crowning a corpse. State fields
(`age`, `deathFrame`, etc.) are documented in
[06-agents.md](06-agents.md); this section is the election mechanics only.

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
`run_belief_pitch` when LM Studio is available. The resulting `quality`, the
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
