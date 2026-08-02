# Implementation Plan — Sovereign God Mode (v2)

**Status:** Planned — not executing. Awaiting explicit implementation approval.
**Supersedes:** [plan-sovereign-god-mode.md](plan-sovereign-god-mode.md) (v1, retained for review history).
**Branch:** propose `feature/god-mode` off `main`.
**Delivery gate:** documentation only. No feature code, spec behavior, runtime
configuration, or saved state is changed by this file.

## Why v2 exists

v1 was reviewed against the actual repository rather than on its own terms. Its
security model, control-plane separation, and persistence approach held up. Four
things did not, and they are the reason for this rewrite:

1. **The gather arithmetic contract contradicted the code.** v1's ordering would
   have silently defeated its own headline `0.0` fish-modifier case.
2. **`weather_override` did not fit v1's own expiry architecture.** Weather is
   real world state on a different clock, not a modifier read through a helper.
3. **Divine-vs-emergent composition was unspecified.** Agent-authored governance
   rules already modify gather yield; v1 never said whether divine law multiplies
   village law.
4. **The survival modifier catalog had a trap and a gap.** A `0.0` regen
   multiplier permanently strands incapacitated agents, and starvation damage —
   the most story-relevant knob — was not covered at all.

Two further changes are scope judgments rather than defects: the SPEC 00 identity
amendment is split into its own commit, and the idempotency store moves in-memory.

## Goal

An optional, disembodied God mode combining **Sight** (authenticated private
inspection), **Voice** (proclamations and private omens), **Providence**
(temporary non-binding guidance in cognition), **Miracles** (bounded direct
mutations), **Storytelling** (timed events composed from validated primitives),
and **Lawgiving** (temporary allowlisted multipliers).

The user is never represented by an agent, sprite, inventory, position, or normal
agent action. Avatar God is out of scope.

The intended experience: **the user authors pressure and meaning; agents remain
the protagonists who decide how to respond.**

## Product decision

This changes [SPEC 00](../specs/00-overview.md), whose current non-goals permit no
player input beyond observation and pause/resume/reset/roster controls. State it
directly rather than hiding it behind an "admin tool" label:

- normal simulation remains autonomous when God mode is disabled;
- god-enabled runs are useful for observation and storytelling but are **not**
  comparable to untouched autonomous runs;
- every intervention is attributable and replay-auditable;
- divine influence must never masquerade as emergent agent behavior.

## Verified architecture facts

Every row below was checked against the working tree, not inferred. These are the
load-bearing facts the design depends on.

| Fact | Evidence | Consequence |
|---|---|---|
| Exactly 42 entries in `DECISION_ACTIONS` | [server.py:961](../simulation/server.py:961) | Control-plane commands genuinely do not trigger the action-sync invariant. |
| `/control/*` has three routes, none authenticated; `SIM_HOST` defaults to `0.0.0.0` | [server.py:4055](../simulation/server.py:4055), [server.py:4087](../simulation/server.py:4087) | A separate token gate is necessary, not defensive excess. |
| `snapshot()` builds an explicit allowlist dict, not a wholesale civ copy | [sim_engine.py:13725](../simulation/sim_engine.py:13725) | `civilization["godState"]` is excluded from `/state` **by construction**. Private omens cannot leak by omission. |
| `save_state()` persists the civ dict wholesale minus `_CIV_SET_KEYS` | [sim_engine.py:13156](../simulation/sim_engine.py:13156) | `godState` persists automatically; no serializer change needed. |
| `restore_state()` uses additive `setdefault` migration | [sim_engine.py:13230](../simulation/sim_engine.py:13230) | Old saves rehydrate safely with a default god state. |
| `_push_memory(self, agent, line, kind=None)` already accepts `kind` | [sim_engine.py:2150](../simulation/sim_engine.py:2150) | Phase 3's `kind="divine_omen"` needs no signature change. |
| `_tick_once()` advances `frameTick` at the top, under the lock, before all consumers | [sim_engine.py:12973](../simulation/sim_engine.py:12973) | Valid, single insertion point for expiry. |
| `escapeHtml` already used 65× in the viewer | `simulation/index.html` | The stored-text rendering contract has an existing idiom to follow. |
| `agent["id"]` are stable ints assigned at roster definition | [sim_engine.py:1530](../simulation/sim_engine.py:1530) | Omens key on `str(id)`; names are display snapshots only. |
| Survival constants are already floats (`HUNGER_RATE = 0.3`, `HEALTH_REGEN = 1.5`, `COLLAPSE_REGEN = 0.5`, `STRUCTURE_DECAY_PER_GOODS_TICK = 0.05`) | [sim_engine.py:519](../simulation/sim_engine.py:519), [869](../simulation/sim_engine.py:869) | Multiplying deltas introduces no state-shape change. Vitals are already fractional. |
| `SessionLogger` opens **four** streams, not three | [server.py:385](../simulation/server.py:385) | `benchmarks.jsonl` exists but is undocumented in CLAUDE.md. Fix that in the same commit that adds `divine.jsonl`. |
| Env-var config precedent exists only in `server.py`, never `sim_engine.py` | [server.py:372](../simulation/server.py:372) | The flag lives in `sim_engine.py` for flag-index consistency but must read env at import; note the new precedent explicitly in SPEC 01. |

## Core design rules

### 1. Separate control plane

God commands use dedicated engine methods and HTTP routes. They do not enter
`DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT` as selectable actions,
`apply_decision()`, `available_actions`, or `ACTION_LABELS`. The action-sync
invariant is **not triggered**. A future agent action such as `pray` would be a
separate scoped change satisfying that invariant in full.

### 2. Typed effects, authored narrative

The user writes title and narration; mechanics come from an allowlisted schema.
Free prose never directly mutates state. The engine validates title, narration,
duration, effect count, effect key, numeric range, target, and current-world
preconditions before accepting a command.

### 3. Preview before apply

Every mutating command has a validation-only preview returning an opaque
`previewId`, a SHA-256 digest of the canonical normalized command, affected
targets, clamped values, conflicts, reversibility class, and the public narration.

The engine keeps a non-persisted, bounded `_godPreviewCache` under its lock: at
most 32 previews, each valid 60 wall-clock seconds. Restart, reset, or restore
invalidates every outstanding preview.

Apply accepts only `{previewId, requestId}`. Under the lock it checks the
idempotency record, resolves the cached preview, revalidates targets/conflicts/
bounds/preconditions against current state, recomputes and compares the digest,
applies atomically, stores the authoritative response, then consumes the preview.

**Change from v1:** the idempotency store is **in-memory, not persisted**. v1
persisted 100 `recentRequests` records into `state.db` while also invalidating
every preview on restart — so a persisted record could only ever serve a retry
within the same process lifetime that an in-memory cache serves equally well.
Persisting it bought a marginal case and created a permanent `state.db` migration
surface. The durable audit trail lives in `divine.jsonl`, where it belongs.

### 4. Honest reversibility

Three classes, and the UI must label which one applies:

| Class | Members | Contract |
|---|---|---|
| **Cancellable** | providence, private omens, timed law modifiers | Revocable before expiry; expire naturally otherwise. |
| **Irreversible** | vitals, resource grants, structure condition | Cannot be undone. An inverse mutation is a *new* intervention, not a rollback. |
| **Consequential** | any effect that triggers downstream world events | Cancelling the effect does not retract what it already caused. |

The third class is new in v2 and exists because of weather (see below).

### 5. Bounded cognition impact

At most one active public providence line and one active private omen per agent,
each capped at 240 characters. No raw intervention history enters prompts; public
story history continues through the existing bounded Chronicle line.

```text
Divine omen: Prepare for a difficult winter. You may interpret or ignore it.
Private omen: Seek reconciliation with Ash. You may interpret or ignore it.
```

The "may interpret or ignore it" contract preserves autonomy. Physically
observable divine effects (a storm) use their normal world-context lines rather
than duplicating the omen.

### 6. Intervention-aware evidence

Once a run receives any applied intervention it is marked `intervened`
(monotonic `false → true`). Benchmarks and reports expose it so autonomous and
god-influenced evidence cannot be mixed accidentally.

## Feature gate and security contract

`GOD_MODE_ENABLED` is an environment-backed, startup-only module flag in
`sim_engine.py`, echoed in `/state.config.flags`:

- `SIM_GOD_MODE` absent/blank/`0`/false-like → disabled (the default);
- `SIM_GOD_MODE=1` → enabled;
- read once at process start; no HTTP route may change it;
- toggling requires the normal single-instance restart.

Mutating and private routes additionally require a non-empty `SIM_GOD_TOKEN`:

- missing token at startup leaves all God routes disabled even when the flag is true;
- clients send `X-God-Token`; compared with `hmac.compare_digest`;
- the token is never written to `state.db`, `/state`, the DOM, any log stream, or
  exception text;
- the viewer holds it in memory only; optional `sessionStorage` behind an explicit
  "remember for this tab" choice; never `localStorage`;
- unauthorized responses use one uniform shape and never reveal whether a target
  exists;
- request-body size limits and per-route mutation throttling apply; the engine's
  idempotency guard remains authoritative if HTTP retries race.

God mode targets a trusted local/LAN deployment. This plan does not turn Flask's
development server into a hardened public service.

### Stored-content safety

Divine text is untrusted stored content even though its author holds the token:

- plain text only, normalized to Unicode NFC;
- reject NUL, C0/C1 controls other than ordinary space, and newlines in
  single-line fields;
- enforce limits after normalization, in both characters and UTF-8 bytes;
- store normalized plain text, never HTML;
- render through `textContent` or the existing `escapeHtml()` at every banner,
  Chronicle, history, preview, error, and agent-detail insertion;
- never place a serialized `normalized_command` into `innerHTML`;
- hostile-string smoke cases (`<script>`, event-handler attributes, quotes,
  ampersands, Unicode edge cases) must remain inert text.

This is security-sensitive precisely because stored narration renders on the same
origin as the in-memory God token.

## Authoritative state model

```json
{
  "godState": {
    "version": 1,
    "intervened": false,
    "nextInterventionSeq": 1,
    "providence": null,
    "privateOmens": {},
    "activeEvents": [],
    "recentInterventions": []
  }
}
```

- `version` — schema version for the nested object.
- `intervened` — monotonic `false → true` after the first successful mutation.
- `nextInterventionSeq` — stable per-world sequence for IDs like `divine-42`;
  never derived from list length.
- `providence` — one active public non-binding directive: `id`, `text`,
  `createdFrame`, `expiresFrame`, `visibility`.
- `privateOmens` — keyed **only** by `str(agent["id"])`. Each record keeps
  `targetId` plus a non-authoritative `targetName` display snapshot. Restore
  prunes malformed records only, never valid omens for agents who later die.
- `activeEvents` — bounded timed effects (cap 8), each with an immutable source
  command, effect list, start/end frames, and status.
- `recentInterventions` — last 100 normalized outcomes for viewer history.

`recentRequests` is deliberately **absent** from persisted state (see rule 3).
Full audit history lives in session JSONL, not an unbounded `civilization` list.

Restore: old saves receive `_default_god_state()` via `setdefault`; malformed
nested fields normalize conservatively; timed effects retain absolute
`expiresFrame`; effects already expired at the restored `frameTick` close once,
log once, and never re-apply; reset creates fresh god state; pausing freezes
frame-based durations, consistent with every other frame-based system.

### Expiry ownership

A timed effect is mechanically active only while:

```text
startFrame <= frameTick < expiresFrame
```

Every `_divine_modifier()` and prompt/projection lookup enforces that predicate,
so an expired effect cannot influence even one extra tick while awaiting cleanup.

One bounded `_expire_divine_effects()` call goes at the start of `_tick_once()`,
immediately after `frameTick` advances ([sim_engine.py:12973](../simulation/sim_engine.py:12973))
and before survival/weather/goods consumers. With `activeEvents` capped at 8 this
is a small bounded scan, not a new timer or thread. It marks newly expired events
exactly once, clears expired guidance, emits one expiry audit record plus any
allowed narration, and leaves closed entries untouched. Restore performs the same
cleanup once after rehydration.

**Scope limit, stated honestly:** the modifier-lookup predicate is a correctness
backstop **only for `_divine_modifier` reads**. It does not protect any effect
that writes real world state. That limitation is why weather changes class in v2.

## Command and effect catalog

### Voice and providence

| Command | Target | Effect |
|---|---|---|
| `proclamation` | Everyone | Public communication/activity/Chronicle entry; no prompt persistence unless paired with providence. |
| `private_omen` | One living agent id | Temporary private cognition line, absent from public `/state`. On expiry/revocation written once via `_push_memory(..., kind="divine_omen")`. |
| `providence` | Everyone | One temporary non-binding cognition line; replaces the prior one only after preview discloses the replacement. |
| `revoke_guidance` | Providence or omen id | Ends guidance early; records revocation. |

### Immediate miracles

| Effect | Behavior | Bounds |
|---|---|---|
| `agent_vitals` | Add health and/or hunger to one living agent. Negative "curse" deltas only after the positive path ships. | Clamp `0..100`; v1 cannot kill, resurrect, alter `deathFrame`, or bypass lifecycle succession. |
| `grant_resource` | Add a known resource to the village stockpile or one living agent. | Known registry id only; per-command and per-session caps; storage semantics preserved explicitly. |
| `structure_condition` | Repair or damage one existing non-ruined structure. | Reuse condition/ruin helpers; cannot recreate a destroyed structure; damage cannot delete registry state. |

**`weather_override` is deferred out of the baseline.** See below.

Also deferred: resurrection, forced death, teleportation, agent creation/deletion,
direct belief adoption/removal, direct relationship rewriting, forced council or
election outcomes, arbitrary structure spawning, arbitrary state-path editing.

### Why weather is deferred

v1 listed `weather_override` as a baseline immediate miracle. Reading the actual
machine ([sim_engine.py:3588–3643](../simulation/sim_engine.py:3588)) shows it does
not fit the architecture:

- **Dual clocks.** Weather is real state in `civilization["weather"]` with its own
  `exitFrame`, advanced only on the `GOODS_TICK_FRAMES` cadence from
  `_tick_goods`. An override would put duration in two places —
  `activeEvents[].expiresFrame` and `weather["exitFrame"]` — on different
  cadences, with no stated winner.
- **No backstop.** Weather is not read through `_divine_modifier`, so the expiry
  predicate cannot protect it. An expiry bug leaves a real storm running.
- **RNG contamination.** `_weather_enter()` calls `random.randint` and
  `random.sample`. Routing an override through it consumes RNG and undermines the
  deterministic-smoke guarantee; bypassing it desyncs the strict
  clear→gathering→storm→clearing→clear cycle.
- **Hidden irreversibility.** Forcing `storm` inflicts real structure damage.
  That is a *consequential* effect wearing a temporary effect's clothing.

None of this is unsolvable, but it needs its own design with its own restore
transition and RNG discipline. It should not ride along inside a phase whose
other three primitives are simple clamped writes. Weather becomes **Phase 6**,
gated on the rest of the system proving stable.

### Timed lawgiver modifiers

| Key | Range | Consumer |
|---|---:|---|
| `gather_yield_multiplier` | `0.25..3.0` | General collection yield. |
| `fish_yield_multiplier` | `0.0..3.0` | Fish collection only. |
| `hunger_drain_multiplier` | `0.0..3.0` | Passive hunger drain only. |
| `health_regen_multiplier` | `0.0..3.0` | Passive **fed** regeneration only — explicitly **not** collapse recovery. |
| `starvation_damage_multiplier` | `0.0..3.0` | Starvation health loss (`HEALTH_RATE`). New in v2. |
| `structure_decay_multiplier` | `0.0..3.0` | Normal wear only, not disaster damage. |
| `spoilage_multiplier` | `0.0..3.0` | Existing spoilage calculation. |

Composition: **one active value per key.** A new event using an occupied key is
rejected unless it declares `replaceEffectId`. Easier to explain, preview, cancel,
persist, and test than multiplicative stacking.

Base module constants are unchanged. Systems query `_divine_modifier(key,
default=1.0)` at the existing calculation site. With no active effect the helper
returns exactly `1.0`.

#### Exact consumer sites and arithmetic

v1 specified this contract in prose that conflicted with the code. v2 names the
lines.

**Gathering — [sim_engine.py:4953–4972](../simulation/sim_engine.py:4953)**

The existing sequence builds `amount`, applies ecology scale (`max(1, ...)`,
line 4963), grove ratio (`max(1, ...)`, line 4968), then the carry cap
(`max(1, min(...))`, line 4969), then increments `collectSuccesses` and the tool
benchmark (4971–4972).

Insert the divine step **after line 4968 and before line 4969**:

```text
mult   = fish_yield_multiplier if resource is fish and active, else gather_yield_multiplier
amount = floor(amount * mult)
if amount <= 0:
    record the attempt; return the "found nothing — the river runs black" narration
    WITHOUT: adding resource, incrementing collectSuccesses, calling
             _path1_tool_benchmark(resource, True), depleting ecology stock,
             recording harvest quota use, or practicing the gather skill
```

Three details v1 got wrong or omitted, each of which would have been a live bug:

1. Line 4969's carry-cap clamp is `max(1, min(...))`. Applying the multiplier
   *before* that clamp — as v1's "then apply carry-cap limits" ordering required —
   resurrects a `0.0` result back to `1`, silently defeating the headline case.
   The zero path must return **before** line 4969.
2. v1 referred to "the existing `max(1, ...)` calculation" in the singular. There
   are three, with different meanings.
3. v1's skip list named resource, skill, and ecology but omitted
   `collectSuccesses` (4971) and `_path1_tool_benchmark(resource, True)` (4972).
   Leaving those in would inflate the success benchmark on a divinely-nulled
   gather — corrupting exactly the evidence stream the plan promises to keep clean.

**Resource-specific precedence:** `fish_yield_multiplier` *replaces*
`gather_yield_multiplier` for fish rather than multiplying with it.

**Divine law vs. village law:** line 4961 already applies
`_custom_rule_modifier("collect_resource", agent, resource)` — an
**agent-authored governance rule**. The divine multiplier is applied *after* it,
which means divine law scales whatever the village voted for. This is the correct
default (a divine famine should suppress a village's own harvest bonus too), but
it must be stated in SPEC 08 and surfaced in preview: when a custom rule is
active, preview shows both contributions separately so the operator can see they
are amplifying an emergent effect rather than replacing it.

**Hunger drain — [sim_engine.py:2546](../simulation/sim_engine.py:2546)**
Multiply `HUNGER_RATE` before the existing `max(0, ...)` clamp. `0.0` suppresses
drain without touching unrelated survival effects.

**Fed health regen — [sim_engine.py:2557](../simulation/sim_engine.py:2557)**
Multiply `HEALTH_REGEN` before the existing `min(100, ...)` clamp.

**Starvation damage — [sim_engine.py:2555](../simulation/sim_engine.py:2555)**
Multiply `HEALTH_RATE` before the existing `max(0, ...)` clamp. New in v2: v1's
catalog covered regeneration but not damage, leaving the "Merciful Rain" story
template with no mechanism for actual mercy.

**Collapse recovery — [sim_engine.py:2548](../simulation/sim_engine.py:2548) — deliberately excluded**
`COLLAPSE_REGEN` is what lets an incapacitated agent climb back to
`COLLAPSE_REVIVE_HEALTH = 15`. A `0.0` regen multiplier applied here traps every
collapsed agent permanently incapacitated with no deterministic escape — the exact
"permanent stall after expiry" failure v1's own balance gate warned about, sitting
undetected in its own modifier table. `health_regen_multiplier` must **not** reach
this line. A smoke case asserts a collapsed agent still recovers under a `0.0`
regen modifier.

**Structure decay — [sim_engine.py:4145](../simulation/sim_engine.py:4145)**
Multiply `STRUCTURE_DECAY_PER_GOODS_TICK` before the `max(0.0, ...)` and before
the disrepair/ruin threshold logic. Direct disaster damage is unchanged.

**Spoilage — [sim_engine.py:4113](../simulation/sim_engine.py:4113)**
Apply to the computed `to_spoil`: `floor(to_spoil * mult)`, then the existing
`min(overflow, ...)` bound. Zero means no spoilage that tick; never remove more
than the eligible amount.

**Identity path:** an effective `1.0` must execute the same arithmetic and produce
byte-identical state to the feature-off baseline. Deterministic smokes cover
`0.0`, fractional, `1.0`, and maximum values, including carry-cap and low-stock
boundaries.

### Storyteller events

A story event carries a bounded title and narration, visibility (public or one
private target), start/expiry frames, zero or more timed modifiers, zero or more
immediate primitives, optional public providence, and a single event id tying
every sub-effect and log entry together. Events are **atomic**: preview validates
every component; apply accepts all or changes nothing.

Initial viewer templates (client form presets submitting the normal schema):

- **Bountiful Harvest** — temporary gather bonus plus public providence.
- **Black River** — `fish_yield_multiplier` penalty with public narration.
- **Long Winter** — hunger and spoilage pressure with an explicit duration.
- **Merciful Rain** — reduced starvation damage plus recovery narration.
  *(v2 makes this template mechanically possible; under v1 it had no knob.)*
- **Festival of Kinship** — proclamation and providence only; no forced
  relationship changes.

Templates are conveniences, not a second mechanical registry.

## HTTP API

| Route | Method | Purpose |
|---|---|---|
| `/control/god/capabilities` | GET | Enabled command/effect names, bounds, duration caps, token status. |
| `/control/god/sight` | GET | Authenticated private inspection, bounded and filterable. |
| `/control/god/preview` | POST | Validate and normalize without mutation. |
| `/control/god/apply` | POST | Apply an exact previewed command with `requestId`. |
| `/control/god/cancel` | POST | Cancel an active omen, providence, or timed event. |

One typed envelope, not one route per miracle:

```json
{ "kind": "story_event", "payload": {}, "expectedFrame": 123456 }
```

Preview returns `{previewId, commandDigest, previewFrame, expiresAt,
normalizedCommand, reversibilityClass, ...}`. Apply sends only
`{previewId, requestId}`. `expectedFrame` is advisory concurrency protection at
preview time; apply repeats material validation under the lock.

Public `/state` gains `god: {intervened, providence?, activePublicEvents,
recentPublicInterventions}` when enabled, plus `config.flags.GOD_MODE_ENABLED`.
Because `snapshot()` is an explicit allowlist, this addition is opt-in by
construction — private state cannot leak by forgetting to filter it.

`/control/god/sight` may expose unfiltered relationships, omen status, vitals and
resources, last action/reasoning, active effects with exact remaining frames, and
intervention outcomes. It must **not** expose raw memory-store embeddings,
authentication material, or unbounded logs.

## Logging

Extend `SessionLogger` with `divine.jsonl` (the fifth stream — see the
architecture table; the same commit fixes CLAUDE.md's Logs section, which
currently lists three of the existing four).

```json
{
  "type": "divine",
  "intervention_id": "divine-42",
  "request_id": "hashed-id",
  "frame_tick": 123456,
  "kind": "story_event",
  "normalized_command": {},
  "outcome": {},
  "status": "applied",
  "public": true
}
```

Never log the token or request headers. Preview-only calls are not world events
and need not enter the audit log, though validation failures may increment a
bounded metric. Application, cancellation, expiry, rejection-after-preview, and
restore-time closure have distinct statuses. World-visible effects also write
activity/communication/Chronicle entries with explicit `source: "divine"`
attribution. Private omens never enter public activity or `/state`. Emit
benchmark metrics for intervention count, active-effect count, command kind, and
rejected-command count; expose `intervened` in benchmark metadata.

## Viewer: Divine Console

A collapsed, feature-gated panel — not controls embedded into the existing
Civilization or Agents panels. Sections: **Unlock**, **Sight**, **Voice**,
**Miracles**, **Story**, **Laws**, **History**.

Every mutation follows `edit → Preview → review → Apply → authoritative result`.

- no Apply before a successful preview; any field edit invalidates it;
- immediate effects show an irreversible warning; consequential effects show a
  "this may cause lasting downstream events" warning;
- timed effects show duration in both simulation time and frames;
- replacement conflicts are explicit;
- failed authorization clears the in-memory token;
- controls stay usable while paused;
- the panel never predicts success — it renders the engine response.

Public rendering stays restrained: a brief banner for new public events, a small
active-effect indicator, Chronicle entries for major interventions. No full-screen
flashing, no compulsory camera movement, no public cue for private omens.

## Phased implementation

Each phase is independently reviewable and updates its owning specs in the same
commit. Specs are edited first within each phase.

### Phase 0 — Contract freeze (planning only)

Approve the observer→interactive product change, the control-plane separation and
action-sync non-applicability, the state/security/preview/idempotency/arithmetic/
conflict/expiry/content-safety/reversibility contracts, the final numeric limits,
and the dual `SIM_GOD_MODE` + `SIM_GOD_TOKEN` startup requirement.

Phase 0 does not edit `specs/` — canonical specs describe implemented behavior and
must never advertise what does not exist. Committed only as this planning artifact.

### Phase 1 — SPEC 00 identity amendment *(new in v2)*

**Goal:** land the irreversible product decision as its own reviewable commit.

Touches: `specs/00-overview.md` only.

Amend the non-goals to state that the simulation supports an optional, default-off
intervention mode, and that intervened runs are not comparable to autonomous ones.
No code, no flag, no route.

v1 bundled this amendment into the security-kernel commit, putting the project's
identity decision in the same diff as its largest and most security-sensitive code
drop. Separating them means the product question can be approved, reverted, or
argued about on its own.

### Phase 2 — Secure kernel, persistence, preview, audit

Touches: `simulation/sim_engine.py`, `simulation/server.py`,
`specs/01-architecture.md`, `specs/02-engine-core.md`, `specs/04-http-api.md`,
`specs/12-ops.md`, new `scripts/god_mode_smoke.py`, `CLAUDE.md` (log-stream fix).

Deliverables: env-backed dark flag and token gate; `_default_god_state()` with
save/reset/restore; canonical envelope validator; bounded non-persisted preview
cache with digest and precondition binding; in-memory idempotency store;
preview/apply/cancel entry points with apply-time revalidation; expiry predicate
and `_expire_divine_effects()`; the five routes; `divine.jsonl`;
intervention-aware benchmark metadata; normalized plain-text contract and
stored-XSS regression cases; deterministic smokes for flag-off, authorization,
malformed payloads, preview tampering and expiry, idempotency, bounds,
persistence, reset, content escaping, and log redaction.

The only applyable command in this phase is a no-mechanics `proclamation`, so the
pipeline is proven before miracles exist.

### Phase 3 — Voice and providence

Touches: `sim_engine.py`, `server.py`, `prompts.py` (only if stable rulebook
wording is required; prefer dynamic user-prompt lines), `specs/03-cognition.md`,
`specs/06-agents.md`, `specs/09-systems-society.md`, `specs/12-ops.md`, the smoke
script.

Deliverables: separate public providence and per-agent omen state keyed by
`agent["id"]`; bounded dynamic prompt lines with explicit autonomy wording;
public-vs-private log and Chronicle behavior; omens excluded from ordinary memory
while active, then written once via `_push_memory(..., kind="divine_omen")` on
expiry or revocation; expiry, replacement, revocation, and restore coverage;
prompt-size assertions; proof that elder `civilization["directive"]` remains
independent and distinctly labeled.

**Measurement gate (schedule risk — call it out).** Run a fixed-case cognition
comparison across no omen / neutral omen / strong omen; verify agents can both
follow and ignore guidance without invalid decisions; verify no context overflow;
record results in `specs/03-cognition.md` before enabling guidance by default.
This is open-ended research with an unbounded outcome, not a checkbox, and it sits
inside the recommended first delivery slice.

### Phase 4 — Bounded immediate miracles

Touches: `sim_engine.py`, `specs/02-engine-core.md`, `specs/06-agents.md`,
`specs/08-systems-economy.md`, `specs/12-ops.md`, the smoke script.

Deliverables: `agent_vitals`, `grant_resource`, `structure_condition`;
subsystem-specific validation and engine helpers; source-attributed narration;
cap and cooldown configuration; negative tests for dead agents, unknown
resources, missing structures, bounds, duplicate requests, and expired previews.

Stop gate: no resurrection, forced death, social rewriting, council control,
teleportation, arbitrary state mutation — and no weather.

### Phase 5 — Storyteller events and temporary laws

Touches: `sim_engine.py`, `specs/02-engine-core.md`, `specs/04-http-api.md`,
`specs/08-systems-economy.md`, `specs/09-systems-society.md`, `specs/12-ops.md`,
the smoke script.

Deliverables: `_divine_modifier()` reads at the seven line-exact consumer sites
above; one-active-value-per-key conflict policy; fish-over-general precedence;
the documented zero/rounding/identity contract including the gather early-return
and the `COLLAPSE_REGEN` exclusion; divine-vs-custom-rule composition surfaced in
preview; atomic multi-effect events; expiry and cancellation; restore-safe active
effects; template semantics; deterministic proof that every `1.0` path preserves
baseline behavior byte-for-byte.

Balance gate: exercise min/max values in a deterministic accelerated run; confirm
no multiplier produces negative resources, invalid vitals, unbounded state, or a
permanent stall after expiry — explicitly including a collapsed agent under a
`0.0` regen modifier; decide whether per-session budgets suffice or each effect
also needs a cooldown.

### Phase 6 — Weather override *(promoted out of the baseline in v2)*

Touches: `sim_engine.py`, `specs/05-world.md`, `specs/02-engine-core.md`, the
smoke script.

Prerequisite design decisions, none of which the baseline can assume:

- which clock owns duration — `activeEvents[].expiresFrame` or
  `weather["exitFrame"]` — and how the loser is kept consistent;
- how an override returns to the natural cycle without desyncing the strict
  clear→gathering→storm→clearing→clear order;
- how to enter a state without consuming RNG through `_weather_enter()`, or how
  smokes seed around it;
- how storm-induced structure damage is disclosed as consequential and
  irreversible in preview.

### Phase 7 — Divine Console and public presentation

Touches: `simulation/index.html`, `simulation/sprites.js` (only if a reusable
banner helper is justified), `specs/11-viewer.md`, `specs/04-http-api.md`.

Deliverables: token unlock flow; all seven sections; capabilities-driven forms;
preview/apply/cancel workflow; banners and active-effect indicators; private-state
isolation; plain-text-only rendering through `textContent`/`escapeHtml` including
preview details, errors, banners, history, and hostile fixtures; narrow-screen and
scroll behavior; keyboard/focus/accessibility states; screenshots for normal,
locked, preview, applied, expired, rejected, and private-omen cases.

### Optional Phase 8 — Free-prose story compiler

Explicitly deferred. An LLM drafts a **typed** event from narrative prose:
performs no mutation, uses a strict schema limited to the existing catalog,
returns a preview requiring explicit confirmation, never receives the God token,
cannot invent keys or bypass bounds, logs separately from agent cognition, and has
its own rate limit and timeout.

Model routing must be measured, not assumed. `sim-fast` already serves
PIANO/background cognition and past contention increased module drops; `sim-smart`
serves every agent decision. This needs its own A/B contention check and is not
required for a complete structured Storyteller God.

## Model routing and implementation ownership

v1's routing section requested GPT-5.6 Sol/Terra, then noted the session lacked a
Sonnet 5 override and concluded implementation must stop until the mismatch
resolved — blocking Phase 1 on a problem that does not exist in this harness.
v2 states the actual arrangement:

- the orchestrating session (any tier) plans, sequences, dispatches, and reviews;
- all implementation goes to the `implementer` subagent
  (`.claude/agents/implementer.md`), pinned to `model: sonnet`, satisfying
  CLAUDE.md's Sonnet-5-or-lower policy directly;
- the orchestrator writes no implementation code beyond trivial one-line fixes.

| Phase | Ownership |
|---|---|
| 0 | Orchestrator/user contract review; no agent, no spec edits. |
| 1 | Orchestrator may edit `specs/00-overview.md` directly (prose-only, no code). |
| 2 | One `implementer` for engine/server, then an independent review pass. |
| 3 | One `implementer` for cognition, then a measured prompt review. |
| 4 | One `implementer`; subsystem invariants. |
| 5 | One `implementer`; compositional effects and deterministic balance. |
| 6 | One `implementer` after the weather design decisions are answered. |
| 7 | One `implementer`; viewer and visual QA. |
| 8 | Separate `implementer` after a fresh go/no-go. |

Phases 2–5 overlap heavily in `sim_engine.py` and must run sequentially. Phase 7
may begin its static layout shell once Phase 2's API contract is frozen, but
dynamic forms wait for Phases 4–5 capabilities. Do not run overlapping engine
phases in parallel worktrees.

Per CLAUDE.md's usage-limit rule: if a limit is hit mid-phase, pause immediately
rather than continuing, retrying, or escalating tiers to push through. Resume only
after confirming the last completed step's diff and smoke result.

## Verification matrix

### Deterministic, no Ollama

- `SIM_GOD_MODE` absent/false: no god projection, no prompt line, mutation routes
  disabled, existing smokes unchanged;
- flag set with no token: routes disabled, startup reports the misconfiguration
  without exposing secrets;
- missing/wrong token: uniform unauthorized response, zero mutation;
- the token never appears in logs, state, responses, or rendered HTML;
- preview is side-effect free; changed/expired/missing/stale previews are rejected;
- apply uses the server-held command, never client-returned authority;
- repeated `requestId` applies once and returns the original response; reuse with a
  different preview or digest is rejected;
- invalid target/effect/duration/value rejects atomically;
- miracle clamps and subsystem invariants hold;
- timed effects activate, conflict, cancel, expire, and restore;
- modifier lookup stops an effect exactly at `expiresFrame`, before cleanup runs;
- **gather zero-path returns before the carry-cap clamp and increments neither
  `collectSuccesses` nor the tool benchmark;**
- **a collapsed agent still recovers under a `0.0` `health_regen_multiplier`;**
- **preview shows divine and custom-rule contributions separately when both are
  active;**
- spoilage/decay/survival arithmetic matches the documented zero, fractional,
  identity, and maximum cases;
- old saves restore with default god state; new saves round-trip every field;
  reset clears intervention state; paused time consumes no duration;
- public/private projection boundary holds; omens target stable ids and enter
  memory exactly once, only on expiry or revocation;
- hostile stored text stays inert on every viewer surface;
- state stays bounded while JSONL stays complete within retention;
- `sid_parity_smoke.py` and `path1_smoke.py` remain green;
- `git diff --check` and Python/JavaScript syntax checks pass.

### Ollama-required

- public providence appears once, within its character and token cap;
- a private omen reaches only its target;
- elder directive and divine providence stay separately labeled;
- invention, sprite-design, and council prompts receive divine context only where
  the cognition contract specifies;
- decision JSON validity and fallback rate do not regress;
- the fixed-case comparison shows agents can both follow and ignore guidance;
- no increase in PIANO drops or decision latency (Phases 2–7 add no LLM calls).

Only fresh post-restart reports count. Cached restored PIANO reports do not.

### Live and visual

Lock/unlock/error states; preview invalidation after edits; duplicate-click
idempotency; proclamation and Chronicle attribution; private omens absent from
public panels; countdown, cancel, expiry, and restored-server continuity; banner
across day, night, weather, and narrow viewport; no regression in Agents,
Activity, Civilization, Chronicle, Daily Council, or follow-camera interactions;
screenshots for visible changes; **exactly one server instance on port 5001 at
final verification** (remembering that `uv run` legitimately shows two
`python.exe` — wrapper and interpreter — which is one instance, not a duplicate).

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| God mode erodes the autonomous-simulation identity. | Default off; monotonic `intervened` marker; fully autonomous path preserved; the product change lands as its own SPEC 00 commit. |
| LAN users gain arbitrary mutation or private inspection. | Separate token gate, no persistence or logging of the token, uniform errors, dedicated sight endpoint. |
| Stored narration executes on the token's origin. | Normalize and store plain text; render via `textContent`/`escapeHtml`; hostile-string regressions. |
| Free text becomes arbitrary state mutation. | Narrative is free; mechanics are typed, allowlisted, bounded, previewed, engine-validated. |
| A preview is tampered with or goes stale. | Opaque server-held preview, canonical digest, short TTL, precondition fingerprint, apply-time revalidation. |
| A retry applies a miracle twice. | In-memory authoritative response keyed by request id; mismatched reuse rejects. |
| "Undo" corrupts causality. | Three explicit reversibility classes; cancel touches only active timed effects. |
| Timed laws stack into extremes. | One value per key, explicit replacement, narrow ranges, expiry, deterministic stress. |
| A modifier creates an inescapable state. | `COLLAPSE_REGEN` excluded from divine scaling; balance gate asserts recovery under `0.0`. |
| Divine effects silently amplify emergent governance. | Composition documented in SPEC 08 and shown separately in preview. |
| Weather override desyncs the state machine or leaks RNG. | Deferred to its own phase with its own design questions answered first. |
| Divine text overwhelms cognition. | One public and one private line, strict caps, fixed-case measurement, no history dump. |
| Benchmarks mix emergent and manipulated runs. | `intervened` marker, divine metrics, audit stream, and a zero-path that does not inflate `collectSuccesses`. |
| Restore replays immediate effects or drops timed ones. | Persist outcomes and absolute expiry; never replay applied commands. |
| The viewer becomes authoritative. | Preview and apply are requests; render engine responses only. |

## Non-goals for the first implementation

No avatar or user-controlled agent. No arbitrary Python, JSONPath, database, or
state-editor access. No forced agent decision or fabricated LLM response. No
direct control of council votes, succession, or Sage verdicts. No resurrection,
forced death, agent creation/deletion, or teleportation. No forced beliefs,
memories, or relationship values. No new agent action such as prayer. No
unrestricted prose-to-state execution. No weather control in the baseline. No
Internet-grade account system. No claim that an intervened run is comparable to an
autonomous control run.

## Commit sequence

1. `docs(plan): plan sovereign god mode v2` — this file only.
2. `spec(00): permit optional default-off intervention mode` — prose only.
3. `god: add secure intervention kernel` — with SPEC 01/02/04/12 and the CLAUDE.md log fix.
4. `god: add voice and providence` — with cognition/agent/society specs.
5. `god: add bounded miracles` — with world/agent/economy specs.
6. `god: add storyteller events and laws` — with every affected system spec.
7. `god: add weather override` — with SPEC 05.
8. `viewer: add divine console` — with viewer/API specs.
9. Optional: `god: add preview-only story compiler`.

PR notes must cover behavior and security changes, the flag default, the
`SIM_GOD_MODE` restart contract, the `SIM_GOD_TOKEN` assumption, the additive
`state.db` shape and restore behavior, new routes and the JSONL stream, benchmark
implications, Ollama assumptions, and verification commands plus screenshots.

## Decisions required before implementation

1. God mode ships dark; absent/false `SIM_GOD_MODE` resolves to disabled.
2. Enabling requires a restart; runtime enable routes are forbidden.
3. A non-empty `SIM_GOD_TOKEN` is mandatory for all private and mutating routes.
4. Apply accepts only a short-lived server-held preview id plus an idempotent
   request id; client-returned commands are never authoritative.
5. The idempotency store is in-memory, not persisted to `state.db`. *(changed in v2)*
6. Negative vital miracles cannot directly kill in v1.
7. Resurrection, belief changes, relationship changes, teleportation, and council
   control remain deferred.
8. **Weather override is deferred out of the baseline to its own phase.** *(changed in v2)*
9. Law effects do not stack per key; fish modifiers replace rather than multiply
   the general gather modifier.
10. **A divine gather modifier scales agent-authored custom rules rather than
    replacing them, and preview discloses both.** *(new in v2)*
11. **`health_regen_multiplier` excludes collapse recovery, and
    `starvation_damage_multiplier` is added.** *(new in v2)*
12. Immediate miracles are not undoable; consequential effects are labeled as such.
13. Storyteller mechanics use structured effects; prose compilation is optional and later.
14. Intervened runs are visibly marked and excluded from autonomous validation claims.

## Recommended first delivery slice

Phases 1–3: the SPEC 00 amendment, the secure control plane with persisted and
audited god state, authenticated Sight, public proclamation, one non-binding
providence, and one private omen per agent.

That is already a meaningful all-seeing, all-speaking God while preserving agent
autonomy, and it proves the security, persistence, cognition, and audit contracts
before any miracle can alter material simulation balance. Phase 3's cognition
measurement is the schedule risk in this slice and should be scoped before it
starts, not discovered during it.
