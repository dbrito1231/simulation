# Implementation Plan — Sovereign God Mode

**Status:** Planned — not executing. Awaiting explicit implementation approval.
**Branch:** TBD (propose `codex/sovereign-god-mode` off the current integration branch).
**Delivery gate:** This document is a plan only. No feature code, spec behavior,
runtime configuration, or saved state is changed by it.

## Goal

Add an optional, disembodied God mode that combines:

1. **Sight** — authenticated access to private simulation context.
2. **Voice** — public proclamations and private omens.
3. **Providence** — temporary, non-binding guidance presented to agent cognition.
4. **Miracles** — bounded, direct mutations such as healing, resource grants,
   weather intervention, and structure repair or damage.
5. **Storytelling** — named, timed narrative events composed from the same
   validated effect primitives.
6. **Lawgiving** — temporary changes to an allowlisted set of world-system
   multipliers.

The user is never represented by an agent, sprite, inventory, position, or normal
agent action. Avatar God behavior is explicitly out of scope.

The intended experience is: **the user authors pressure, meaning, and exceptional
events; agents remain the protagonists who decide how to respond.**

## Product decision and scope honesty

This is a deliberate change to
[SPEC 00](../specs/00-overview.md), whose current non-goals permit no player
input beyond observation and pause/resume/reset/roster controls. God mode makes
GitServ partly interactive and game-like.

That change should be stated directly rather than hidden behind an "admin tool"
label:

- Normal simulation remains autonomous when God mode is disabled.
- God-enabled runs are still useful for observation and storytelling, but are
  not directly comparable to untouched autonomous runs.
- Every intervention must be attributable and replay-auditable.
- Divine influence must never masquerade as emergent agent behavior.

## Current architecture findings that shape the design

| Finding | Consequence |
|---|---|
| `SimEngine` owns all world state behind one `RLock`; the viewer is stateless. | The viewer may request interventions, but only the engine may validate and apply them. |
| Existing `/control/*` routes are unauthenticated and the server binds to `0.0.0.0` by default for trusted-LAN access. | Divine mutation and private sight require a separate token gate; the existing control security model is insufficient. |
| `/state` is a public-to-the-LAN projection. | Public divine events may appear there; private memories, private omens, and hidden context must use an authenticated endpoint. |
| `civilization["directive"]` already means a temporary elder instruction. | Divine providence needs its own field and prompt label. It must not overwrite or impersonate village leadership. |
| Agent decisions use a synchronized 42-action catalog and structured schema. | Divine commands are control-plane events, not agent actions. No `DECISION_ACTIONS` addition is planned. |
| Full `civilization` state already persists in `state.db`; restore uses additive `setdefault` migrations. | God state should live under `civilization["godState"]` and use the same backward-compatible restore pattern. |
| The Chronicle already carries bounded major events into prompts and the viewer. | Public divine events should reuse Chronicle/activity narration while retaining a separate divine audit record. |
| Weather, survival, resources, structures, relationships, and governance have separate owning specs and invariants. | Each miracle primitive must call or share an engine helper that preserves the affected subsystem's bounds and recovery rules. |
| There is no automated test suite, but deterministic smoke scripts are the accepted verification surface. | Add a direct-engine God-mode smoke harness and keep Ollama optional for baseline verification. |

## Core design rules

### 1. Separate control plane

God commands use dedicated engine methods and HTTP endpoints. They do not enter:

- `DECISION_ACTIONS`
- `DECISION_SCHEMA`
- `SYSTEM_PROMPT` as actions agents can choose
- `apply_decision()`
- `available_actions`
- `ACTION_LABELS`

The action-sync invariant is therefore **not triggered** by the baseline design.
If a later phase adds an agent response such as `pray`, `resist_omen`, or
`interpret_prophecy`, that must be a separate scoped change satisfying the full
action-sync invariant.

### 2. Typed effects, authored narrative

The user may write the title and narration of a divine event, but mechanics must
come from an allowlisted schema. Free prose never directly mutates state.

For example, "The Black River" may carry:

```json
{
  "title": "The Black River",
  "narration": "The river darkens, and fish become scarce.",
  "durationFrames": 2700,
  "effects": [
    {
      "kind": "system_modifier",
      "key": "fish_yield_multiplier",
      "value": 0.5
    }
  ]
}
```

The engine validates the title, narration, duration, effect count, effect key,
numeric range, target, and current-world prerequisites before accepting it.

### 3. Preview before apply

Every mutating command has a validation-only preview path. The preview returns:

- a cryptographically random, opaque `previewId`;
- a SHA-256 digest of the canonical normalized command;
- normalized command;
- affected targets;
- bounded/clamped values;
- conflicts with existing effects;
- whether the result is immediate, temporary, cancellable, or irreversible;
- the public narration that agents/viewers will see.

The engine keeps a non-persisted, bounded `_godPreviewCache` under its lock:
at most 32 previews, each valid for 60 wall-clock seconds. A cache entry binds
`previewId` to the canonical normalized command, its digest, preview frame, and
the target/precondition fingerprint that was validated. A server restart,
world reset, or restore invalidates every outstanding preview.

Apply accepts only `{previewId, requestId}`; the client never sends a normalized
command back as authority. Under the engine lock, apply:

1. checks the persisted idempotency record for `requestId`;
2. resolves the exact cached preview;
3. revalidates its targets, conflicts, bounds, and material preconditions against
   current world state;
4. recomputes and compares the canonical command digest;
5. applies the complete command atomically;
6. stores the authoritative response in the idempotency record;
7. consumes the preview only after successful application.

A missing, expired, changed, or materially stale preview is rejected and must be
previewed again. Harmless `frameTick` drift is acceptable only when every
recorded target and precondition still matches.

The engine stores bounded request records—not bare IDs—so a network retry or
double click returns the original authoritative response without applying the
miracle again. Reusing a `requestId` with a different `previewId` or command
digest is a conflict and never returns or applies either command.

### 4. Honest reversibility

- A temporary effect can be cancelled or can expire naturally.
- A directive or omen can be revoked before expiry.
- An immediate mutation cannot claim to be undone after downstream simulation
  has consumed it. Removing five granted food later is a new intervention, not a
  rollback.
- The UI must label immediate miracles as **irreversible once applied**.

### 5. Bounded cognition impact

Divine context is short and independently capped:

- at most one active public providence line;
- at most one active private omen line per agent;
- each line capped at 240 characters;
- no raw intervention history is placed in every prompt;
- public story history continues through the existing bounded Chronicle line.

Suggested prompt labels:

```text
Divine omen: Prepare for a difficult winter. You may interpret or ignore it.
Private omen: Seek reconciliation with Ash. You may interpret or ignore it.
```

The "may interpret or ignore it" contract preserves agent autonomy. Divine
effects that are physically observable, such as a storm, continue to use their
normal world-context lines rather than duplicating the omen.

### 6. Intervention-aware evidence

Once a run receives any applied intervention, it is marked as intervened.
Benchmarks and reports must expose that fact so autonomous and god-influenced
evidence cannot be mixed accidentally.

## Feature gate and security contract

Add `GOD_MODE_ENABLED` as an environment-backed, startup-only module flag and
echo it in `/state.config.flags`:

- `SIM_GOD_MODE` absent, blank, `0`, or false-like -> disabled (the default);
- `SIM_GOD_MODE=1` or another explicitly documented true-like value -> enabled;
- the value is read once at process startup and cannot be changed by an HTTP
  route;
- enabling/disabling requires the normal single-instance server restart.

This retains a dark default without requiring the operator to edit source code.
God mode becomes usable only when both `GOD_MODE_ENABLED` is true and a non-empty
`SIM_GOD_TOKEN` is configured.

Mutating or private routes additionally require `SIM_GOD_TOKEN`:

- Missing/blank token at server startup leaves all God routes disabled even if
  the feature flag is true.
- Clients send the token in `X-God-Token`.
- The token is compared with `hmac.compare_digest`.
- The token is never written to `state.db`, `/state`, the DOM, activity logs,
  conversation logs, divine logs, or exception text.
- The viewer asks for the token when the Divine Console is unlocked and retains
  it only in memory by default. Optional `sessionStorage` persistence may be
  offered behind an explicit "remember for this tab" choice; never use
  `localStorage`.
- Unauthorized responses use one uniform shape and do not reveal whether a
  target agent or event exists.
- Add request-body size limits and per-route mutation throttling. The engine's
  idempotency guard remains authoritative even if HTTP retries race.

God mode remains intended for a trusted local/LAN deployment, not Internet
exposure. The plan does not turn Flask's development server into a hardened
public service.

### Stored-content safety

Titles, proclamations, omens, providence, and story narration are untrusted
stored text even though their author holds the God token:

- accept plain text only;
- normalize to Unicode NFC;
- reject NUL, C0/C1 control characters other than ordinary spaces, and embedded
  newlines where the field contract is single-line;
- enforce the documented limits after normalization and in UTF-8 bytes as well
  as characters;
- store the normalized plain text, never HTML;
- render it with DOM `textContent` or the existing `escapeHtml()` helper at
  every banner, Chronicle, history, preview, error, and agent-detail insertion;
- never place a serialized `normalized_command` into `innerHTML`;
- add hostile-string smoke cases (`<script>`, event-handler attributes, quotes,
  ampersands, and Unicode edge cases) and verify they remain inert text.

This is security-sensitive because stored narration is rendered on the same
origin as the in-memory God token.

## Authoritative state model

Add this additive, persisted shape:

```json
{
  "godState": {
    "version": 1,
    "intervened": false,
    "nextInterventionSeq": 1,
    "providence": null,
    "privateOmens": {},
    "activeEvents": [],
    "recentInterventions": [],
    "recentRequests": []
  }
}
```

### Field contracts

- `version`: schema version for the nested God-state object.
- `intervened`: monotonic `false -> true` after the first successful mutation.
- `nextInterventionSeq`: stable per-world sequence used in IDs such as
  `divine-42`; never derived from list length.
- `providence`: one active public, non-binding directive with `id`, `text`,
  `createdFrame`, `expiresFrame`, and `visibility`.
- `privateOmens`: keyed only by `str(agent["id"])`, never by mutable/display
  name. The record keeps `targetId` plus a non-authoritative `targetName`
  snapshot for display/audit. Dead/missing IDs are rejected on application;
  restore prunes only malformed records, not valid omens for agents who later
  die.
- `activeEvents`: bounded timed story/law effects. Each entry has an immutable
  source command, effect list, start/end frames, and status.
- `recentInterventions`: last 100 normalized outcomes for viewer history and
  state-local audit convenience.
- `recentRequests`: last 100 successful request records shaped as
  `{requestId, previewId, commandDigest, interventionId, status, response}`.
  `response` is the bounded authoritative API response required to answer an
  identical retry. Request IDs are unique within the saved world.

Full retained audit history belongs in session JSONL, not in an unbounded
`civilization` list.

Restore behavior:

- old saves receive `_default_god_state()` via `setdefault`;
- malformed nested fields are normalized conservatively;
- active timed effects retain absolute `expiresFrame` values;
- effects already expired at restored `frameTick` are closed once, logged once,
  and never re-applied;
- resetting the world creates a fresh God state and clears idempotency history;
- pausing freezes frame-based divine durations, consistent with other
  frame-based systems.

### Expiry ownership and cadence

Timed state is mechanically active only while:

```text
startFrame <= frameTick < expiresFrame
```

Every `_divine_modifier()` and prompt/projection lookup enforces that predicate,
so an expired effect cannot influence even one extra system tick while awaiting
cleanup.

Add one bounded `_expire_divine_effects()` call at the start of `_tick_once()`,
immediately after `frameTick` advances and before survival/weather/goods or other
effect consumers. The active list is capped at 8 events, so this is a small
bounded scan, not a new timer or thread. It:

- marks newly expired events exactly once;
- clears expired providence/private-omen active state;
- emits one expiry audit record and any allowed public narration;
- removes or archives expired active entries without growing state;
- leaves already-closed entries untouched.

Restore performs the same cleanup once after rehydration. Modifier lookups remain
the correctness backstop even if logging fails. Paused worlds do not advance
`frameTick`, so neither mechanical activity nor cleanup expires while paused.

## Command and effect catalog

### Voice and providence

| Command | Target | Mechanical effect |
|---|---|---|
| `proclamation` | Everyone | Public communication/activity/Chronicle entry; no prompt persistence unless paired with providence. |
| `private_omen` | One living agent ID | Temporary private cognition line; not exposed in public `/state`. On expiry/revocation it is written once through `_push_memory(..., kind="divine_omen")`, becoming an ordinary remembered event only after it stops being the dedicated active prompt line. |
| `providence` | Everyone | One temporary, non-binding cognition line; replaces the prior providence only after preview discloses the replacement. |
| `revoke_guidance` | Providence or omen ID | Ends the guidance early and records revocation. |

### Immediate miracles

Baseline primitives should be deliberately narrow:

| Effect | Allowed behavior | Bounds/invariants |
|---|---|---|
| `agent_vitals` | Add health and/or hunger to one living agent; allow negative "curse" deltas only after the positive path is proven. | Clamp to normal `0..100`; v1 cannot directly kill, resurrect, change `deathFrame`, or bypass lifecycle succession. |
| `grant_resource` | Add a known resource to village stockpile or one living agent. | Known registry ID only; per-command and per-session quantity caps; preserve storage semantics explicitly. |
| `structure_condition` | Repair or damage one existing non-ruined structure. | Reuse condition/ruin helpers; v1 repair cannot recreate a destroyed/retired structure and v1 damage cannot delete registry state. |
| `weather_override` | Enter an existing weather state for selected valid districts and bounded duration. | Requires weather flag; after expiry, return to the normal state machine through an explicit transition, not an unlogged assignment. |

Deferred immediate primitives:

- resurrection;
- forced death;
- teleportation;
- creating or deleting agents;
- direct belief adoption/removal;
- direct relationship rewriting;
- direct council votes or election outcomes;
- arbitrary structure spawning;
- arbitrary state-path editing.

These are deferred because they bypass lifecycle, spatial, social, governance,
or construction invariants. They require separate designs if later approved.

### Timed lawgiver modifiers

Start with a small allowlist:

| Key | Range | Consumer |
|---|---:|---|
| `gather_yield_multiplier` | `0.25..3.0` | Successful collection yield after existing role/tool/ecology calculations. |
| `fish_yield_multiplier` | `0.0..3.0` | Fish collection only; supports river stories without changing all gathering. |
| `hunger_drain_multiplier` | `0.0..3.0` | Passive hunger drain only. |
| `health_regen_multiplier` | `0.0..3.0` | Passive fed regeneration only. |
| `structure_decay_multiplier` | `0.0..3.0` | Normal structure wear/decay, not direct disaster damage. |
| `spoilage_multiplier` | `0.0..3.0` | Existing spoilage calculation. |

Composition rule for v1: **one active value per key**. A new event that uses an
occupied key is rejected unless it explicitly declares `replaceEffectId`.
This is easier to explain, preview, cancel, persist, and test than multiplicative
stacking.

The base module constants remain unchanged. Systems query an engine helper such
as `_divine_modifier(key, default=1.0)` at the existing calculation site. When
no effect is active, the helper returns exactly `1.0`.

#### Arithmetic and ordering contract

The implementation must not leave rounding or zero behavior to individual
callers:

- **Gathering:** compute the existing role/tool/ecology/terrain base yield first.
  Select `fish_yield_multiplier` for fish when it is active; otherwise select
  `gather_yield_multiplier`. The resource-specific value replaces the general
  value rather than multiplying with it. Compute
  `floor(baseYield * multiplier)`, then apply district-stock and carry-cap
  limits. A result of zero counts as a collection attempt but not a success:
  add no resource, practice no gather skill, and deplete no ecology stock.
  This divine multiplication occurs after the existing `max(1, ...)` base-yield
  calculation so a `0.0` fish modifier actually produces zero.
- **Hunger drain and health regeneration:** multiply the existing floating-point
  per-tick delta before applying the existing `0..100` clamp. `0.0` suppresses
  that delta without suppressing unrelated survival effects.
- **Structure decay:** multiply the existing floating-point condition loss
  before the condition/ruin threshold logic. Direct disasters are unchanged.
- **Spoilage:** compute the existing integer candidate amount, then use
  `floor(baseAmount * multiplier)` before stock removal. Zero means no spoilage
  for that tick; never remove more than the existing eligible amount.
- **Identity path:** an effective value of exactly `1.0` must execute the same
  arithmetic path and produce byte-for-byte equivalent state/results to the
  feature-off baseline.

Preview shows both the selected modifier and its arithmetic consequence for the
current target where one can be computed. Deterministic smoke cases cover `0.0`,
fractional, `1.0`, and maximum values, including carry capacity and low stock.

### Storyteller events

A story event contains:

- bounded title and narration;
- visibility (`public` or one private target);
- start and expiry frames;
- zero or more timed allowlisted modifiers;
- zero or more immediate miracle primitives;
- optional public providence;
- a single event ID tying every sub-effect and log entry together.

Story events are atomic: preview validates every component, and apply either
accepts all components or changes nothing.

Provide a small initial template catalog in the viewer, implemented as client
form presets that still submit the normal schema:

- **Bountiful Harvest** — temporary gather bonus and public providence.
- **Black River** — temporary fish penalty with public narration.
- **Merciful Rain** — weather transition plus limited recovery narration.
- **Long Winter** — hunger/spoilage pressure with an explicit duration.
- **Festival of Kinship** — proclamation and providence only in v1; no forced
  relationship changes.

Templates are conveniences, not a second mechanical registry. The engine
accepts only the canonical typed command.

## HTTP API proposal

Add five authenticated routes:

| Route | Method | Purpose |
|---|---|---|
| `/control/god/capabilities` | GET | Return enabled command/effect names, bounds, duration caps, token status, and feature availability. |
| `/control/god/sight` | GET | Return authenticated private inspection state, bounded and filterable by agent/event. |
| `/control/god/preview` | POST | Validate and normalize a command without mutation. |
| `/control/god/apply` | POST | Apply an exact previewed command with `requestId`; return the authoritative outcome. |
| `/control/god/cancel` | POST | Cancel an active omen, providence, or timed event; never pretend to undo immediate effects. |

Do not create one route per miracle. One typed command envelope keeps validation,
idempotency, audit, and client handling consistent.

Suggested envelope:

```json
{
  "kind": "story_event",
  "payload": {},
  "expectedFrame": 123456
}
```

That envelope is sent to `/preview`. A successful response returns
`{previewId, commandDigest, previewFrame, expiresAt, normalizedCommand, ...}`.
The apply body is only:

```json
{
  "previewId": "opaque-server-preview-id",
  "requestId": "browser-generated-uuid"
}
```

`expectedFrame` is advisory concurrency protection during preview. Apply uses
the server-held preview and repeats material validation as specified above.

Public `/state` additions:

- `god`: `{intervened, providence?, activePublicEvents, recentPublicInterventions}`
  when the feature is enabled;
- `config.flags.GOD_MODE_ENABLED`.

Private `/control/god/sight` additions may include:

- unfiltered living-agent relationships;
- private omen status;
- current vitals/resources;
- last action/reasoning and current cognitive reports already held by engine
  state;
- active divine effects and their exact remaining frames;
- intervention IDs and outcomes.

Do not expose raw full memory-store embeddings, authentication material, or
unbounded logs through this endpoint.

## Logging and observability

Extend `SessionLogger` with `divine.jsonl`.

Each record contains:

```json
{
  "type": "divine",
  "intervention_id": "divine-42",
  "request_id": "redacted-or-hashed-id",
  "frame_tick": 123456,
  "kind": "story_event",
  "normalized_command": {},
  "outcome": {},
  "status": "applied",
  "public": true
}
```

Rules:

- never log the token or request headers;
- preview-only calls are not world events and need not enter the audit log,
  though validation failures may increment a bounded metric;
- application, cancellation, expiry, rejection-after-preview, and restore-time
  closure have distinct statuses;
- world-visible effects also write activity/communication/Chronicle entries with
  `source: "divine"` or an equivalent explicit attribution;
- private omens never leak into public activity or `/state`;
- emit benchmark metrics for intervention count, active-effect count, command
  kind, and rejected-command count;
- expose `intervened` in benchmark/report metadata.

## Viewer: Divine Console

Add a collapsed, feature-gated panel rather than embedding controls into the
existing Civilization or Agents panels.

Suggested sections:

1. **Unlock** — enter token; show locked/authorized/disabled status.
2. **Sight** — select an agent and inspect authenticated private context.
3. **Voice** — proclamation, public providence, or private omen.
4. **Miracles** — target/type form built from `/capabilities`.
5. **Story** — title, narration, duration, templates, and selected effects.
6. **Laws** — active modifier list with expiry and cancel controls.
7. **History** — recent interventions with public/private badges and outcomes.

Every mutation follows:

```text
edit form -> Preview -> review normalized effect -> Apply -> authoritative result
```

UX requirements:

- no Apply button before a successful preview;
- changing any field invalidates the preview;
- immediate effects show an irreversible warning;
- timed effects show exact duration in simulation time and frames;
- replacement conflicts are explicit;
- failed authorization clears the in-memory token;
- controls remain usable when the simulation is paused;
- cancel buttons apply only to active cancellable effects;
- the panel never predicts success optimistically—render the engine response.

Public world rendering should remain restrained:

- a brief, readable banner for new public divine events;
- a small active-omen/effect indicator;
- Chronicle entries for major public interventions;
- no full-screen flashing or compulsory camera movement;
- private omens have no public visual cue.

## Phased implementation

Each phase is independently reviewable and keeps specs synchronized in the same
commit. Specs are edited first within each phase.

### Phase 0 — Plan approval and contract freeze

**Goal:** Freeze the product and technical contracts in this non-canonical plan
before code. Phase 0 does not edit `specs/`, because canonical specs describe the
implemented repository and must never advertise behavior that does not exist.

Deliverables:

- approve this plan's observer-to-interactive product change;
- confirm the listed spec ownership for later behavior commits;
- approve the control-plane separation and action-sync non-applicability;
- freeze the state, security, preview binding, idempotency, command, effect,
  arithmetic, conflict, expiry, content-safety, and reversibility contracts;
- decide final numeric limits using existing system constants and state ranges;
- approve the startup contract: both `SIM_GOD_MODE=1` and a non-empty
  `SIM_GOD_TOKEN` are required.

Gate: no implementation or canonical-spec edits begin until the user approves
the concrete command catalog and contracts. Phase 0 may be committed only as
this `docs/plan-*.md` planning artifact.

### Phase 1 — Secure kernel, persistence, preview, and audit

**Goal:** Establish the safe intervention substrate without world-changing
miracle types.

Touches:

- `simulation/sim_engine.py`
- `simulation/server.py`
- `specs/00-overview.md`
- `specs/01-architecture.md`
- `specs/02-engine-core.md`
- `specs/04-http-api.md`
- `specs/12-ops.md`
- new `scripts/god_mode_smoke.py`

Deliverables:

- environment-backed dark flag (`SIM_GOD_MODE`) and environment-token gate;
- canonical spec changes for the product/non-goal shift, flag, state, routes,
  and audit stream in the same commit as this behavior;
- `_default_god_state()` plus save/reset/restore handling;
- canonical command-envelope validator;
- bounded, non-persisted preview cache with digest/precondition binding;
- preview/apply/cancel engine entry points with apply-time revalidation;
- persisted `recentRequests` idempotency responses and bounded histories;
- expiry predicate and `_expire_divine_effects()` ownership;
- capabilities, sight, preview, apply, and cancel routes;
- `divine.jsonl`;
- intervention-aware benchmark metadata;
- normalized plain-text contract and stored-XSS regression cases;
- deterministic smoke coverage for flag-off, authorization, malformed payloads,
  preview tampering/expiry, idempotency, bounds, persistence, reset, content
  escaping, and log redaction.

Phase 1's only applyable test command may be a no-mechanics `proclamation`, so
the pipeline can be proven before miracles exist.

### Phase 2 — Voice and providence

**Goal:** Add public proclamations, private omens, and non-binding guidance.

Touches:

- `simulation/sim_engine.py`
- `simulation/server.py`
- `simulation/prompts.py` only if general rulebook wording is required;
  otherwise prefer dynamic user-prompt lines
- `specs/03-cognition.md`
- `specs/06-agents.md`
- `specs/09-systems-society.md`
- `specs/12-ops.md`
- `scripts/god_mode_smoke.py`

Deliverables:

- separate public providence and per-agent private omen state;
- private omen records keyed only by stable `agent["id"]`, with names retained
  solely as display snapshots;
- bounded dynamic prompt lines;
- explicit autonomy wording;
- public vs private log/Chronicle behavior;
- active private omens stay out of ordinary memory to avoid duplicate prompt
  influence, then enter `_push_memory(..., kind="divine_omen")` exactly once on
  expiry or revocation;
- expiry, replacement, revocation, and restore coverage;
- prompt-size assertions;
- proof that elder `civilization["directive"]` remains independent and both
  instructions are distinctly labeled.

Measurement gate:

- run a fixed-case cognition comparison with no omen vs neutral omen vs strong
  omen;
- verify agents can follow or ignore it without invalid decisions;
- verify no prompt/context overflow regression;
- record results in `specs/03-cognition.md` before enabling guidance by default.

### Phase 3 — Bounded immediate miracles

**Goal:** Add the four baseline miracle primitives.

Touches:

- `simulation/sim_engine.py`
- `specs/02-engine-core.md`
- `specs/05-world.md`
- `specs/06-agents.md`
- `specs/08-systems-economy.md`
- `specs/12-ops.md`
- `scripts/god_mode_smoke.py`

Deliverables:

- agent vitals blessing;
- resource grant;
- structure condition change;
- weather override;
- subsystem-specific validation and engine helpers;
- source-attributed narration;
- cap/cooldown configuration;
- negative tests for dead agents, unknown resources, missing structures,
  incompatible flags, bounds, duplicate requests, and expired previews.

Stop gate: do not add resurrection, forced death, social rewriting, council
outcome control, teleportation, or arbitrary state mutation during this phase.

### Phase 4 — Storyteller events and temporary laws

**Goal:** Compose narrative and mechanical primitives into atomic timed events.

Touches:

- `simulation/sim_engine.py`
- affected system specs: `05`, `08`, and `09`
- `specs/02-engine-core.md`
- `specs/04-http-api.md`
- `specs/12-ops.md`
- `scripts/god_mode_smoke.py`

Deliverables:

- allowlisted `_divine_modifier()` reads at the documented consumer sites;
- one-active-value-per-key conflict policy;
- resource-specific-over-general precedence and the exact gathering, survival,
  decay, and spoilage arithmetic/rounding contract;
- atomic multi-effect story events;
- expiry and cancellation;
- restore-safe active effects;
- public/private visibility;
- initial template semantics;
- deterministic comparisons proving every modifier's `1.0` path preserves
  baseline behavior.

Balance gate:

- exercise minimum/maximum values in a deterministic accelerated run;
- confirm no multiplier can create negative resources, invalid vitals, runaway
  unbounded state, or a permanent stall after expiry;
- decide whether per-session miracle budgets are sufficient or whether each
  effect also needs a cooldown.

### Phase 5 — Divine Console and public presentation

**Goal:** Deliver the full user-facing workflow after backend contracts stabilize.

Touches:

- `simulation/index.html`
- `simulation/sprites.js` only if a reusable banner/icon drawing helper is
  justified
- `specs/11-viewer.md`
- `specs/04-http-api.md`

Deliverables:

- token unlock flow;
- Sight, Voice, Miracles, Story, Laws, and History sections;
- capabilities-driven forms;
- preview/apply/cancel workflow;
- public event banners and active-effect indicators;
- private-state isolation;
- plain-text-only rendering through `textContent`/`escapeHtml`, including preview
  details, errors, banners, history, and hostile stored-string fixtures;
- narrow-screen and panel-scroll behavior;
- keyboard/focus/accessibility states;
- screenshots for normal, locked, preview, applied, expired, rejected, and
  private-omen cases.

### Optional Phase 6 — Free-prose story compiler

**Status:** Explicitly deferred from the baseline.

Allow the user to type a narrative and ask an LLM to produce a **draft typed
event**. The compiler:

- performs no mutation;
- uses a strict JSON schema limited to the existing effect catalog;
- returns a preview requiring explicit user confirmation;
- does not receive the God token;
- cannot invent effect keys or bypass bounds;
- logs model usage separately from agent cognition;
- has its own rate limit and timeout;
- is measured for latency and contention before shipping.

Model choice and concurrency must be measured. Do not assume `sim-fast` is safe:
it already serves PIANO/background cognition, and past contention increased
module drops. Do not assume `sim-smart` is free: it serves every agent decision.
This phase needs a separate A/B contention check and is not required for a
complete structured Storyteller God.

## File and spec ownership map

| File | Planned responsibility |
|---|---|
| `simulation/sim_engine.py` | God state, validation, authoritative application, expiry, modifiers, persistence, snapshots. |
| `simulation/server.py` | Token verification, route schemas, SessionLogger divine stream, request-size/throttle handling. |
| `simulation/prompts.py` | Only stable rulebook wording if needed; dynamic omen text stays in the user prompt path. |
| `simulation/index.html` | Divine Console, authenticated requests, preview/apply UX, public indicators. |
| `simulation/sprites.js` | Optional pure/stateless divine visual helpers only. |
| `scripts/god_mode_smoke.py` | Deterministic direct-engine and HTTP-contract verification with no Ollama dependency for Phases 1, 3, and 4. |
| `specs/00-overview.md` | Product/non-goal change and autonomous-vs-intervened distinction. |
| `specs/01-architecture.md` | Control-plane data flow, lock discipline, flag index. |
| `specs/02-engine-core.md` | State shape, timing, persistence, reset/restore, idempotency. |
| `specs/03-cognition.md` | Providence/private-omen prompt construction, caps, and measured behavior. |
| `specs/04-http-api.md` | Authenticated God routes and public/private projections. |
| `specs/05-world.md` | Weather and world-targeted miracle semantics. |
| `specs/06-agents.md` | Sight projection boundaries, vitals and private omen effects. |
| `specs/08-systems-economy.md` | Resource, survival, structure, spoilage, and yield modifiers. |
| `specs/09-systems-society.md` | Chronicle/communication semantics and governance exclusions. |
| `specs/11-viewer.md` | Divine Console and public visual presentation. |
| `specs/12-ops.md` | `divine.jsonl`, metrics, redaction, and smoke command. |

## Model routing and implementation ownership

Repository policy remains authoritative:

- the primary model orchestrates, sequences, and reviews;
- implementation is dispatched to Sonnet 5 or lower implementers;
- the orchestrator does not write implementation code beyond trivial one-line
  fixes.

Requested orchestration/review preference:

- **Preferred:** GPT-5.6 Sol, high reasoning.
- **Fallback:** GPT-5.6 Terra, high reasoning for cross-phase integration;
  medium is acceptable for bounded documentation or viewer-only review.

Implementation assignments:

| Phase | Implementer ownership | Required capability |
|---|---|---|
| 0 | Orchestrator/user contract review only; no implementation agent | Planning review; canonical specs remain untouched. |
| 1 | One engine/server implementer, then one independent reviewer | Sonnet 5; security, persistence, concurrency. |
| 2 | One cognition implementer, then measured prompt reviewer | Sonnet 5; prompt construction and live-report analysis. |
| 3 | One engine implementer | Sonnet 5; subsystem invariants. |
| 4 | One engine/economy implementer | Sonnet 5; compositional effects and deterministic balance. |
| 5 | One viewer implementer | Sonnet 5 or lower; HTML/CSS/JS and visual QA. |
| 6 optional | Separate cognition implementer after a new go/no-go | Sonnet 5; structured output and contention measurement. |

Availability note: this Codex session exposes GPT-5.6 Sol/Terra agent variants,
but no Sonnet 5 override. Under the current repo policy, GPT-5.6 must not be
silently substituted as an implementation model. If implementation is later
requested in an environment without Sonnet 5, stop and resolve the policy/model
availability mismatch first.

Phases 1–4 overlap heavily in `sim_engine.py` and must be sequential. Phase 5 may
begin its static layout shell after Phase 1's API contract is frozen, but dynamic
forms must wait for Phase 3/4 capabilities. Do not run overlapping engine phases
in parallel worktrees.

## Verification matrix

### Deterministic, no Ollama

- `SIM_GOD_MODE` absent/false: no God state projection, no prompt line, mutation
  routes disabled, existing smokes unchanged;
- `SIM_GOD_MODE=1` with no token: routes remain disabled and startup reports the
  configuration error without exposing secrets;
- missing/wrong token: uniform unauthorized response and zero state mutation;
- valid token never appears in logs, state, response bodies, or rendered HTML;
- preview is side-effect free;
- a changed, expired, missing, or stale preview is rejected;
- apply uses the server-held normalized command rather than client-returned
  authority;
- repeated `requestId` applies once and returns the original authoritative
  outcome;
- reusing a `requestId` with a different preview/digest is rejected;
- invalid target/effect/duration/value is rejected atomically;
- immediate miracle clamps and subsystem invariants hold;
- temporary effects activate, conflict as specified, cancel, expire, and restore;
- modifier lookup stops an effect exactly at `expiresFrame`, even before cleanup
  narration runs;
- gather/spoilage/decay/survival arithmetic matches the documented zero,
  fractional, identity, and maximum cases;
- old save restores with default God state;
- new save round-trips every God-state field;
- reset clears intervention state;
- paused time does not consume frame-based duration;
- public/private projection boundary holds;
- private omens target stable agent IDs and enter ordinary agent/vector memory
  exactly once only when expired or revoked;
- hostile stored text remains inert in every public/private viewer surface;
- full event log remains bounded in state and complete in JSONL within retention;
- baseline `sid_parity_smoke.py` and `path1_smoke.py` remain green;
- `git diff --check` and Python/JavaScript syntax checks pass.

### Ollama-required cognition checks

- public providence appears once and stays within its character/token cap;
- private omen reaches only its intended agent;
- elder directive and divine providence remain separately labeled;
- invention-only, sprite-design, and council-special prompts receive divine
  context only if explicitly specified by the cognition contract;
- decision JSON validity and fallback rate do not regress;
- fixed-case comparison measures whether agents can both follow and ignore a
  non-binding omen;
- no increase in PIANO drops or decision latency for Phases 1–5, which add no LLM
  calls.

Only fresh post-restart reports count as cognition validation. Cached restored
PIANO reports do not.

### Live and visual checks

- lock/unlock/error states;
- preview invalidation after edits;
- duplicate-click idempotency;
- public proclamation and Chronicle attribution;
- private omen absence from public panels;
- active effect countdown, cancel, expiry, and restored-server continuity;
- divine banner during day, night, weather, and narrow viewport;
- console scrolling does not break existing Agents, Activity, Civilization,
  Chronicle, Daily Council, or follow-camera interactions;
- screenshot evidence for visible UI changes;
- exactly one server instance/listener on port 5001 at final verification.

## Primary risks and mitigations

| Risk | Mitigation |
|---|---|
| God mode destroys the project's autonomous-simulation identity. | Default off; mark intervened runs; preserve a fully autonomous path; state the product change in SPEC 00. |
| LAN users gain arbitrary world mutation or private inspection. | Separate token gate, no token persistence/logging, uniform unauthorized errors, private sight endpoint. |
| Stored narration executes in the same origin as the God token. | Normalize and store plain text only; render with `textContent`/`escapeHtml`; hostile-string regression coverage. |
| Free text becomes arbitrary code/state mutation. | Narrative is free text; mechanics are typed, allowlisted, bounded, previewed, and engine-validated. |
| A preview is tampered with or becomes stale before apply. | Opaque server-held preview, canonical digest, short TTL, target/precondition fingerprint, and apply-time revalidation. |
| A retry applies a miracle twice. | Persist the bounded authoritative response keyed by request ID; same request returns it, mismatched reuse rejects. |
| "Undo" corrupts causality. | Cancel only active timed effects; treat inverse immediate mutations as new interventions. |
| Timed laws stack into extreme values. | One active effect per key, explicit replacement, narrow ranges, expiry, deterministic stress checks. |
| Miracles bypass lifecycle/governance invariants. | Small baseline catalog; defer resurrection, death, belief, relationship, election, and arbitrary-state powers. |
| Divine prompt text overwhelms cognition. | One public and one private line, strict character caps, fixed-case measurement, no history dump. |
| Benchmarks silently mix emergent and manipulated runs. | Monotonic `intervened` marker plus divine metrics and audit stream. |
| Restore re-applies immediate effects or loses timed effects. | Persist outcomes and absolute expiry; never replay applied commands during restore. |
| Viewer becomes authoritative through optimistic updates. | Preview and apply are requests only; render authoritative engine responses. |
| Optional story compiler contends with agent cognition. | Deferred phase, preview-only output, measured model routing and concurrency gate. |

## Explicit non-goals for the first implementation

- No avatar or user-controlled agent.
- No arbitrary Python, JSONPath, database, or state-editor access.
- No forced agent decision or fabricated LLM response.
- No direct manipulation of council votes, succession winners, or Sage verdicts.
- No resurrection, forced death, agent creation/deletion, or teleportation.
- No forced beliefs, memories, or relationship values.
- No new agent action such as prayer.
- No unrestricted free-prose-to-state LLM execution.
- No Internet-grade account system or multi-user role hierarchy.
- No claim that an intervened run is comparable to an autonomous control run.

## Delivery and commit sequence

Recommended commits:

1. `docs(plan): plan sovereign god mode` — this plan only; no canonical specs.
2. `god: add secure intervention kernel` — includes matching SPEC 00/01/02/04/12 changes.
3. `god: add voice and providence` — includes matching cognition/agent/society specs.
4. `god: add bounded miracles` — includes matching world/agent/economy specs.
5. `god: add storyteller events and laws` — includes every affected system spec.
6. `viewer: add divine console` — includes viewer/API specs.
7. Optional: `god: add preview-only story compiler` — includes cognition/ops specs.

Each behavior commit includes its owning spec changes. PR notes must include:

- behavior and security changes;
- feature-flag default;
- `SIM_GOD_MODE` startup/restart contract;
- `SIM_GOD_TOKEN` assumption;
- `state.db` additive shape and restore behavior;
- new routes and JSONL stream;
- autonomous-vs-intervened benchmark implications;
- Ollama assumptions, especially if optional Phase 6 is included;
- verification commands and screenshots.

## Decisions required before implementation

The plan recommends defaults, but the user should explicitly approve these before
Phase 1:

1. God mode ships dark: absent/false `SIM_GOD_MODE` resolves
   `GOD_MODE_ENABLED` to false at startup.
2. Enabling requires a startup restart with `SIM_GOD_MODE=1`; runtime enable
   routes are forbidden.
3. A non-empty `SIM_GOD_TOKEN` is mandatory for all private/mutating routes.
4. Apply accepts only a short-lived server-held preview ID plus an idempotent
   request ID; client-returned normalized commands are never authoritative.
5. Negative vital miracles cannot directly kill in v1.
6. Resurrection, belief changes, relationship changes, teleportation, and
   council outcome control remain deferred.
7. Temporary law effects do not stack per key; resource-specific gather
   modifiers replace the general gather modifier rather than multiplying.
8. Immediate miracles are not undoable.
9. Storyteller mechanics use structured effects; free-prose compilation is an
   optional later phase.
10. Intervened runs are visibly marked and excluded from autonomous validation
   claims.

## Recommended first delivery slice

After Phase 0's plan approval, implement Phases 1–2 first:

- secure control plane;
- persisted/audited God state;
- omniscient authenticated Sight;
- public proclamation;
- one non-binding public providence;
- one private omen per agent.

This is already a meaningful all-seeing, all-speaking God mode while preserving
agent autonomy. It proves the security, persistence, cognition, and audit
contracts before direct miracles can alter material simulation balance.
