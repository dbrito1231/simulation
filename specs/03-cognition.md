# SPEC 03 — Cognition (LLM Pipeline)

The LLM think pipeline: prompt construction, structured-output decoding,
decision validation/fallback, model routing, and retry/degradation behavior.

**Canonical for:** all Ollama call settings (models, timeouts, sampling,
token budgets), `DECISION_SCHEMA`/structured-output mode, prompt template
sections, `normalize_decision`/`role_fallback_action` rules, model routing +
high-stakes policy, retry/degradation ladders, concurrency/context-sizing
constants. **See also:** [specs/01-architecture.md](01-architecture.md) (data
flow, flag index), [specs/02-engine-core.md](02-engine-core.md) (tick/think
scheduling, Sage emergency), [specs/04-http-api.md](04-http-api.md) (routes
that front this pipeline), [specs/07-actions.md](07-actions.md) (the action
catalog — not repeated here).

> **Migration note (2026-07-24):** the sim's LLM runtime has cut over from LM
> Studio to Ollama (`docs/plan-ollama-migration.md` Phases 2-3). LM Studio is
> permanently unavailable. See [ollama_config.md](../ollama_config.md) for
> the full settings/contract reference (required env vars, VRAM gate,
> restore procedure). This spec documents what `simulation/server.py`
> actually sends as of the Phase 3 revision: native
> `POST http://localhost:11434/api/chat` (`OLLAMA_CHAT_URL`), `stream:false`.
> `MODEL_SMART = "sim-smart"` (qwen3.5 9B, `num_ctx=20480`) and
> `MODEL_FAST = "sim-fast"` (`llama3.2:3b`, `num_ctx=4096`) are genuinely
> distinct models split **by workload kind, not by decision stakes**: ALL
> decision turns (routine and high-stakes) route to `MODEL_SMART`;
> `MODEL_FAST` serves only background cognition (PIANO modules, memory
> summarizer/wiki merge, meta system, belief pitch — every direct
> `lm_complete()` caller). This supersedes an initial Phase 3 attempt that
> also routed routine decisions to `sim-fast`: a live soak measured
> `piano_module_drops` climbing to ~25-38% (vs. the ~9% pre-migration
> reference) and module latencies rising instead of falling, because routine
> decisions and PIANO modules were contending for `sim-fast`'s
> `OLLAMA_NUM_PARALLEL=3` slots. Keeping decisions entirely on `sim-smart`
> (a separate, uncontended slot pool) removes that contention. Both models
> are created via `scripts/ollama_setup.py` and kept resident with
> `OLLAMA_KEEP_ALIVE=-1`. A module-init check in server.py prints a loud
> `[server] WARNING: MODEL_FAST == MODEL_SMART` if the two constants ever
> collapse back to the same value (env override, hotfix, etc.) — implemented
> as a **warning, not a hard assert/crash**: the plan called for a startup
> assert enforcing the "permanent two-model MUST", but a hard crash on a
> config regression would strand the 24/7 sim server with no rollback runtime
> (LM Studio is gone). The warning fires once at import time and the server
> continues to start; decisions and background cognition would silently
> share the smart model's queue until the regression is fixed. Historical LM
> Studio numbers/behavior remain in comments and `ollama_config.md`'s
> "Thinking-epidemic history" section (the original record, `lms_config.md`,
> was removed in the migration's Phase 5) for context but no longer describe
> the running system.

## Ollama call settings

| Call type | System prompt | Model | max_tokens (→ options.num_predict) | temperature | timeout | sampling |
|---|---|---|---|---|---|---|
| Routine decision | `SYSTEM_PROMPT` (or `SYSTEM_PROMPT_SLIM` on retry) | `MODEL_SMART` (`sim-smart`, qwen3.5 9B) | 512 | 0.4 | `DEFAULT_TIMEOUT_S`=30s | `NON_THINKING_SAMPLING` + `think:false` |
| High-stakes decision (elder / `invention_status` REQUIRED / rate-limited emergency,election,treaty_vote) | `SYSTEM_PROMPT`/slim | `MODEL_SMART` (`sim-smart`, qwen3.5 9B) | 512 (1600 only if thinking re-enabled, currently dead code) | 0.4 | `THINKING_TIMEOUT_S`=75s | `THINKING_SAMPLING` if `THINKING_ENABLED_HIGH_STAKES` (omits `think`, i.e. thinking on), else same as routine |
| Invention-only turn | `INVENTION_SYSTEM_PROMPT` | `MODEL_SMART` (sprite/invention always high-stakes) | `INVENTION_MAX_TOKENS`=1024 | `INVENTION_TEMPERATURE`=0.6 | 75s | as above |
| Daily Council turn | slim council-turn prompt | `MODEL_SMART`; routine settings except an elder verdict uses the high-stakes settings | bounded routine output | routine temperature | routine timeout, or 75s for elder verdict | routine sampling, or high-stakes sampling for elder verdict |
| Sprite-design turn | `SPRITE_UPGRADE_SYSTEM_PROMPT` | `MODEL_SMART` | 768 | 0.3 | 75s | as above |
| Background `lm_complete` (memory summarizer/wiki merge, PIANO modules, meta system/autobiography, belief-pitch scoring) | caller-supplied one-off prompt | `MODEL_FAST` always (`sim-fast`, llama3.2:3b) | caller-set (8/40/80/90/100/220 per call site; PIANO=`PIANO_MODULE_MAX_TOKENS`=90) | caller-set (0.0-0.6) | 30s (hardcoded, not `DEFAULT_TIMEOUT_S`); PIANO fan-out 15s (`PIANO_MODULE_TIMEOUT_S`), always-on refresh 60s (`MODULE_REFRESH_TIMEOUT_S`) | `NON_THINKING_SAMPLING` + `think:false` |

Note the Routine-decision and High-stakes-decision rows both resolve to
`MODEL_SMART` now — they're kept as separate table rows because
`is_high_stakes_turn` still fully determines timeout, max_tokens, and
thinking/sampling for each, independent of the (now-constant) model choice.

Phase 3 input-size sanity check (all against `sim-fast`'s `num_ctx=4096`):
the largest background call is the wiki-memory merge
(`sim_engine.py:_run_wiki_memory_merge`, `max_tokens=220`) — its prompt is 3
wiki sections (`WIKI_SECTION_CHAR_CAP=300` chars each, ~225 tokens) plus up to
12 recent memories (each capped at 280 chars on store, ~840 tokens) plus a
~180-token system prompt, roughly 1,250 input tokens. The meta/autobiography
call (`run_meta_update`, `max_tokens=100`) joins up to 14 memories
(~980 tokens) plus report fields and a ~150-token system prompt, roughly
1,150 input tokens; the follow-up persona call adds the ~300-char
autobiography, still under 1,500 input tokens. All are well under half of
4096, so no truncation risk and no input capping was added — this is a
documented finding, not a code change.

`to_ollama_body(payload)` lives in `simulation/llm_wire.py` (moved out of
`server.py`, docs/plan-ollama-migration.md Phase 4, so
`scripts/llm_replay_bench.py` can import the exact conversion `server.py`
uses without importing `server.py` itself — that module has import-time side
effects, see `simulation/prompts.py`'s docstring). `server.py` imports it
(`import llm_wire as _llm_wire; to_ollama_body = _llm_wire.to_ollama_body`)
right after the `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` import near the top of
the module, and every call site (next to `build_response_format`, and both
`run_agent_decision`/`lm_complete`'s POST sites) still refers to it as
`to_ollama_body`. It is the single conversion point every call site routes
through: it takes the
internal OpenAI-chat-completions-shaped payload this module builds (model,
messages, max_tokens, temperature, sampling keys, an optional
`response_format`, an optional boolean `think`) and produces the Ollama wire
body — `messages` pass through; `max_tokens` → `options.num_predict`;
`temperature`/`top_p`/`top_k`/`min_p`/`presence_penalty` → `options.*`;
`response_format` (json_schema nesting, see `build_response_format`) →
`format` (the extracted schema object, or `"json"` for `json_object` mode);
`think` passes through under its own Ollama-native name if present (omitted
= let the model think).

`MODEL_SMART = "sim-smart"` and `MODEL_FAST = "sim-fast"` (server.py).
`model_for_decision(data)` always returns `MODEL_SMART` — decisions never
route to `sim-fast`, regardless of `is_high_stakes_turn(data)`.
`is_high_stakes_turn()` remains a real, actively-used predicate: it still
selects timeout (`THINKING_TIMEOUT_S` vs `DEFAULT_TIMEOUT_S`), max_tokens
(`HIGH_STAKES_MAX_TOKENS`), and sampling/thinking (`THINKING_SAMPLING` vs
`NON_THINKING_SAMPLING`) — only its role in picking the model id was removed.
`MODEL_FAST` is reserved for background cognition only (PIANO modules, the
memory summarizer/wiki merge, the meta system, belief-pitch scoring — every
direct `lm_complete()` caller); see Concurrency & context sizing for why
(decision/background contention on `sim-fast`'s slot pool measured in the
first Phase 3 attempt). Fallback: Ollama has no LM-Studio-style
`"local-model"` alias to retry with — if `looks_like_model_not_found_error`
fires, that is treated as a **setup failure**: the server logs a `[server]`
line pointing at `uv run python scripts/ollama_setup.py`, disables
`_model_routing_enabled` (session-wide, to avoid repeat-logging), and returns
`{"error": "llm offline", "action": "rest"}` immediately — no retry (see
Retries).

`NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}`;
`THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}` (server.py) — Qwen
model-card-recommended pins, sent on every call (routed into Ollama's
`options` object by `to_ollama_body`) so behavior doesn't drift with preset
changes.

### Thinking-mode suppression (`THINKING_ENABLED_HIGH_STAKES`)

`DISABLE_THINKING_ROUTINE = True` (server.py): every routine turn sends
top-level `"think": false` in the Ollama `/api/chat` body — the native
endpoint's own thinking-suppression contract (Phase 0 finding #4, verified
live: `eval_count` dropped 1288→81 tokens, wall time 77s→4.8s when
`think:false` was set vs. left unset). The OpenAI-compat
`/v1/chat/completions` endpoint silently ignores `think:false` entirely,
which is why this repo targets native `/api/chat` exclusively — see
`OLLAMA_CHAT_URL`. (Historical: LM Studio's equivalent knob was a top-level
`"reasoning_effort": "none"` field; that mechanism no longer exists.)

`THINKING_ENABLED_HIGH_STAKES = False` (server.py, current value) — high-
stakes turns do **not** omit `think:false`, but since this flag is False,
`thinking_active` in `build_decision_payload` never goes True, so high-stakes
turns still use `NON_THINKING_SAMPLING` + `think:false` in practice.
**Consequence (LM Studio-era finding, semantics carried over unchanged):** a
2026-07-14 live analysis of 48 high-stakes samples (server.py comments) found
reasoning content gave zero measurable decision-quality benefit when it *was*
enabled — the model emitted the identical JSON either way, just routed
through the reasoning channel — while costing 33% concurrency (parallel 3→2
was needed for headroom). Reverted to `False` + `MAX_CONCURRENT_LLM=3`.
`HIGH_STAKES_MAX_TOKENS=1600` (server.py) is therefore currently dead code,
kept in case thinking is revisited — if it is, the same `think:false`
contract above is not optional (see ollama_config.md's thinking-epidemic
history note).

## Structured output

`STRUCTURED_OUTPUT_MODE = "json_schema"` (server.py:743). `build_response_format()`
(server.py:861-872) returns `{"type": "json_schema", "json_schema": {"name":
"agent_decision", "schema": DECISION_SCHEMA}}`, or `{"type": "json_object"}` if
the mode were `"json_object"`, or `None` if `"off"` or auto-disabled.

`DECISION_SCHEMA` (server.py:780-839): `additionalProperties: False`;
`required: ["action", "reasoning"]`. Key properties: `action` (enum =
`DECISION_ACTIONS`, 43 entries — see specs/07-actions.md, not repeated here),
`divine_response` (nullable object, **required on every decision turn while
active Voice guidance is unacknowledged** — see "Voice binding guidance"
below; omitted on turns with no active unacked guidance), `target`/
`target_district`/`message`/`new_role` (nullable strings),
`relationship_update` (nullable object, values constrained to
ally/neutral/rival), `blueprint` (nullable object: id/name/needs/new_resources/
visual_style/sprite/function), `recipe` (nullable object: id/name/inputs/
station), `rule` (nullable object: id/name/kind/value/description), `vote`
(nullable string), `sage_decision` (nullable enum approve/deny), `sprite`
(nullable object: palette/grid). **TECH_TREE_ENABLED import-time addition**
(server.py:3208-3215, applied only if the engine flag is on so flag-off
prompts stay byte-identical): adds `verdict` (nullable object with
`rejections`) and `blueprint.tier` (nullable integer) to the schema, and
rewrites `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` to document the tier field.

**Auto-disable on rejection:** `_structured_output_enabled` (module-level)
flips to `False` for the rest of the session — and the retry drops
`response_format` from the payload — the first time Ollama responds with an
HTTP 400 or an error body mentioning `format`/`response_format`/
`json_schema`/`grammar`/`schema` (`looks_like_response_format_error`). In
practice Ollama's `format` field is stable (Phase 0 finding #2: a full JSON
schema was honored in every trial), so this is a safety net rather than an
expected path.

## Prompt construction

`SYSTEM_PROMPT` and `SYSTEM_PROMPT_SLIM` live in `simulation/prompts.py`
(moved out of server.py 2026-07-24, docs/plan-ollama-migration.md Phase 6) —
the single source of truth both `server.py` (`import prompts as _prompts`,
aliased at module scope so every existing `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM`
reference below is unchanged) and `scripts/ollama_setup.py --with-system`
import from, so the rulebook text is never duplicated. `prompts.py` also owns
the one `TECH_TREE_ENABLED`-gated rewrite (documents the optional blueprint
`"tier"` field) and the two `[server] system prompt sha256=...` startup-proof
prints — both now fire at `prompts.py`'s import time (which server.py triggers
near the top of its own module, before Flask is constructed), not at the
bottom of server.py where they used to live. `prompts.py` is importable on its
own (it only imports `sim_engine.py` for the `TECH_TREE_ENABLED` flag, which
has no import-time side effects) without importing `server.py` — `server.py`
has module-level side effects on import (opens a new session log directory,
constructs the live `SimEngine`) that make it unsafe for a setup script to
import.

`SYSTEM_PROMPT` (~20 numbered rule groups): talk-gating,
build-project/district steering, ecology/terraform, blueprints (two-stage Sage
review then approve/reject), survival (hunger/health/heal), crafting/recipes,
Sage-priority-absolute emergency response, Path 1 (tools/blocks/treaties),
emergent roles (switch_role), collective rules/voting, Cognitive Controller
(PIANO module weighing), upkeep/seasons (repair/spoilage), market/trade/
property (homes), population/governance (succession, quotas/rationing),
knowledge/culture (skill teaching), followed by the JSON output contract and
worked examples per action family (rest, contribute, talk, propose_blueprint,
sage_review_blueprint, approve_blueprint).

`SYSTEM_PROMPT_SLIM` (server.py:1119-1122): `SYSTEM_PROMPT` sliced at the
first `"\nEXAMPLE ("` marker — same rules and JSON schema, no worked examples.
Used for the context-overflow retry (see Retries).

`INVENTION_SYSTEM_PROMPT` (server.py:1135-1174): a dedicated, ~85%-smaller
system prompt for invention-only turns — output-format contract + blueprint
schema/example only, no village rulebook (irrelevant to authoring a blueprint
and was wasting the token budget on every council member's turn).

`SPRITE_UPGRADE_SYSTEM_PROMPT` (server.py:2667+): a dedicated prompt for the
sprite-design-only turn that follows a blueprint's mechanical approval.

`USER_PROMPT_TEMPLATE` (server.py:1176-1213): ordered sections — identity
(name/role/skill/personality/memory), vitals (resources/hunger/health/
relationships/beliefs), spatial (nearby agents/zone/district/known districts/
local stocks/terraform targets), flag-gated single lines (`season_line`,
`prices_line`, `weather_line`, `chronicle_line`, `path1_lines`, `level_line` —
each renders empty when its owning flag is off so prompts stay byte-identical
across flag states, per `build_user_prompt` server.py:2787-2904), build state
(structures/active project/progress), civilization state (directive,
`divine_lines` — see Sovereign God mode below, invention status, commitment,
idle agents, known resources/recipes, pending/rejected blueprints/recipes/
rules, reserved structure ids), social (recent conversations, inbox, module
reports), a `behavior_nudge` line, and finally `available_actions`.

`weather_line` (living-ecosystem Phase 5, `WEATHER_GOVERNANCE_ENABLED`):
`_weather_prompt_line()` (sim_engine.py) returns one short "Weather: ..."
line — but only while `civilization["weather"]["state"]` is `"storm"` or
`"clearing"`; `None` (and so an empty template slot) the rest of the time,
including whenever the flag or `WEATHER_ENABLED` is off. Follows the exact
`chronicle_line`/`council_digest_line` pattern (engine computes the string,
server folds it in only when set) and rides the existing think cycle — no new
LLM call, no new context section, just this one line so agents can reference
storm conditions in council.

### Sovereign God mode (Phase 3): Voice binding guidance

`_build_think_payload` computes `divine_public_line`/`divine_private_line` per
agent via `_divine_prompt_lines(agent)` (sim_engine.py) — placed immediately
next to `directive` in the payload but returned by a separate helper and
carried in **separate keys**, never folded into or read from
`civilization["directive"]`. `_divine_prompt_lines` re-checks
`startFrame <= frame_tick < expiresFrame` itself for whichever of
`godState["providence"]` / `godState["privateOmens"][str(agent["id"])]` is
present, rather than trusting `_expire_divine_effects` to have already swept
that exact tick — an expired-but-not-yet-closed record must never reach a
prompt. Whisper campaigns apply one private omen per target; each agent's
`divine_private_line` reflects only their own omen text (campaign theme is
operator metadata, not injected). Both are `None` outright when `GOD_MODE_ENABLED` is off.

`build_user_prompt` (server.py) folds each into its own line, rendered ONLY
when set — the same fold-in-only-when-set pattern as `weather_line` above, so
flag-off / no-active-guidance prompts stay byte-identical to before this
phase — immediately after the `Civilization directive: {directive}` line via
a `{divine_lines}` template slot:

```text
Divine guidance (binding): Prepare for a difficult winter. State whether you follow or continue in divine_response.
Private guidance (binding): Seek reconciliation with Ash. State whether you follow or continue in divine_response.
```

**Binding contract.** Public providence and private omens are **binding Voice
guidance**, not optional flavor. Every routine/high-stakes decision turn while
an agent has **active, unacknowledged** Voice guidance (public providence
and/or a private omen aimed at that agent) must include a `divine_response`
object in the returned JSON:

```json
{"stance": "follow" | "continue", "reason": "<short string>"}
```

`stance: "follow"` means the agent accepts the guidance as governing intent
for this turn; the engine clears `goal` and `assignedTask` before applying the
chosen `action` (the LLM still picks the concrete action — guidance steers
intent, it does not pin the action). `stance: "continue"` means the agent
explicitly declines to let the guidance override current plans; goals and
assigned tasks are left intact. The `reason` is a short, human-readable
explanation surfaced to operators in Sight and the Voice Adherence panel.

**Missing/invalid `divine_response`.** When Voice guidance is active and
unacknowledged but the model omits `divine_response`, returns a malformed
object, or supplies an unknown `stance`, `normalize_decision` **does not**
reject the turn — it **synthesizes** `{"stance": "continue", "reason":
"missing_divine_response"}` and still applies the validated `action`. This is
an explicit non-compliance signal for operators, not a hard fallback to
`rest`. The synthesized record is written to the divine-response log (see
[02-engine-core.md](02-engine-core.md)) exactly once per guidance ack.

**Acknowledgement.** A turn counts as acknowledged when a valid or
synthesized `divine_response` is recorded against the active guidance id(s).
Until then, every subsequent think for that agent carries the same binding
prompt lines and the `divine_response` requirement.

**Special-turn cancellation.** While Voice guidance is active and
unacknowledged for an agent, any pending `sprite_design_only` or
`invention_only` special turn for that agent is **cancelled/dropped** — not
soft-deferred. The engine clears the special-turn flag and returns the agent
to the ordinary decision path on the next think. Applying new Voice guidance
(providence, private omen, whisper target, or a proclamation that auto-applies
as providence) also cancels any in-flight special turn for affected agents at
apply time. Matrix anoint/bush/story soft prompt lines are unchanged — only
providence/private-omen Voice guidance participates in this binding contract.

At most one public line and one private line, each already capped at
`GOD_TEXT_MAX_CHARS = 240`

**Divine Matrix memory surgery (Phase 3).** `memory_insert` and `belief_plant`
write into the same `memory` slice `_memory_for_prompt` composes — via
`_god_memory_insert` with kinds `divine_false_memory` / `divine_belief` —
without ever touching public activity/communication/chronicle. `memory_delete`
removes matching `MemoryStore` rows and keyword-matching local tier lines.
These are distinct from private omens: false memories are ordinary recall
lines immediately, not deferred until omen closure.
characters (`GOD_TEXT_MAX_BYTES = 600` bytes) by `_normalize_divine_text` at
write time (sim_engine.py, [02](02-engine-core.md)), so the two lines add a
small, precisely bounded amount of prompt text even at maximum omen length —
no raw intervention history, no Chronicle duplication of private content ever
enters a prompt. The elder `directive` line is completely unaffected: both
fields can be active simultaneously, are rendered on separate template lines
with separate labels, and an agent sees them as two distinct sources of
guidance (village leadership vs. an unexplained divine signal), never as one
merged instruction.

**Measurement gate.** Binding Voice guidance ships with the providence/omen
apply path; operators should still spot-check `llm.jsonl` for
`divine_response` presence and `divine.jsonl` / Sight for adherence before
recommending an always-on god-guided deployment. `SIM_GOD_MODE` ships dark
(see [01](01-architecture.md)), so no default-on run is affected.

### Divine Matrix Phase 2: per-agent sampling overlay (Temperature Dial)

`_build_think_payload` may attach `divine_sampling` when
`godState["agentSampling"][str(agentId)]` is active (not expired, agent
living). Shape: `{model: "sim-smart"|"sim-fast", temperature, top_p?, top_k?,
min_p?}` — status metadata only; never prompt text.

`build_decision_payload` (server.py) applies routine defaults first (model via
`model_for_decision` / `MODEL_SMART_SYS` when `SYSTEM_PROMPT_AT_LOAD_TIME`,
`temperature` 0.4, `NON_THINKING_SAMPLING` or `THINKING_SAMPLING`), then
**overlays** `divine_sampling` when present: `sim-fast` forces `MODEL_FAST`;
`sim-smart` uses `MODEL_SMART_SYS` on the load-time-system path else
`MODEL_SMART`; temperature and any supplied `top_p`/`top_k`/`min_p` replace
the defaults. `model_for_decision(data)` honors `divine_sampling["model"]` for
callers that read the model id outside `build_decision_payload` (timeout
selection, etc.). This path affects **agent decision turns only** — PIANO,
`lm_complete`, sprite/invention prompts are unchanged.

**Concurrency risk:** routing decisions to `sim-fast` contends with PIANO on the
same Ollama pool. Preview/apply refuses a second living agent's `sim-fast`
override while one is already active (cap = 1). Prefer `sim-smart` for routine
operator overrides.

### Divine Matrix Phase 4: context masks (Reality Distortion)

After `_build_think_payload` builds the true snapshot and attaches
`divine_public_line` / `divine_private_line` (via `_divine_prompt_lines`) plus
`divine_public_event_line` (active public `story_event` narration), the engine
calls `_apply_context_mask(agent, payload)` before returning. This layer reads
`godState["contextMasks"][str(agentId)]` when active (same frame-window /
expiry discipline as omens). Mask modes:

- **`blue_pill`** — nulls divine cognition lines and filters divine chat from
  the `recent_conversations` string in the payload only.
- **`red_pill`** — sets `divine_simulation_truth_line` (capped at
  `GOD_TEXT_MAX_CHARS`); `build_user_prompt` renders it as its own line. Never
  includes other agents' private omen text.
- **`dream`** — overwrites allowlisted payload keys from the stored
  `dreamSnapshot` record (deep-copied values).
- **`whisper_chain`** — replaces `recent_conversations` with forged
  `from -> to: message` slices from `forgedConversations`.

`contextMasks` is private — absent from `/state` god allowlist. Sight exposes
per-agent status only: `{active, mode, expiresFrame}`. Batch **`dream_broadcast`**
parents fan out one shared `dreamSnapshot` per target; child masks carry
`dreamBroadcastId` for cancel-all semantics (runtime apply path is identical to
single-target `context_mask` mode `dream`).

### Divine Matrix Phase 5: decision gate (Possession pipeline)

After the think payload is built (and optional context mask applied), the
decision path branches:

1. **Pre-LLM** — if `decisionGates[agentId].mode == "possession"` and
   `bypassLlm`, `_think_job` skips `llm_decide` entirely and applies the
   pinned/queued decision under lock (`_log_benchmark("divine_possession_skip")`
   records the skip).
2. **Post-LLM** — `_apply_gated_decision(agent, decision)` runs immediately
   before every normal `apply_decision` on the LLM path (including error→`rest`
   and rule-based fallback council branches). Compulsion replaces; veto holds;
   possession forces pin.

Pinned decisions are validated at god preview/apply via `normalize_decision`
(engine deps: `normalize_decision`, `build_agent_data` from `server.py`).
`decisionGates` is private. Sight exposes per-agent gate status:
`{active, mode, armed?, status?, expiresFrame?, hasPending?, pinnedAction?}`
plus `divineHold` on the agent row — never full pinned JSON in `/state`.
Batch **`crowd_compulsion`** parents fan out per-target compulsion gates with
shared duration/turns; child gates carry `crowdCompulsionId` for cancel-all
semantics (runtime apply path is identical to single-target `decision_compulsion`).

### Divine Matrix Phase 6: Burning Bush + Merovingian Bargain

`_build_think_payload` adds `divine_burning_bush_line` from
`_burning_bush_prompt_line(agent)` — distinct from `divine_private_line`
(omens) and `divine_public_line` (providence). `build_user_prompt` renders
it as `Divine audience: … You may respond in talk or reason.` Active bargain
terms are folded into the same line. Thread text never appears in `/state`.

Agent replies are captured when `apply_decision` runs talk or when reasoning is
present (`_capture_burning_bush_reply`). Bargain auto-settlement runs on tick
via `_tick_divine_bargains` (see [02-engine-core.md](02-engine-core.md)).

### Divine Matrix Phase 7: Anointed

`_build_think_payload` adds `divine_anointment_line` from
`_anointment_prompt_line(agent)` — private destiny plus oracle hints whose
`revealFrame <= frameTick`. `build_user_prompt` renders it as
`Anointed destiny: … You may interpret or ignore it.` Stigmata tags on
**other** agents appear only in `nearby_agents` / `format_nearby_agents`
(`signs: tag1, tag2`) via `_get_nearby_detailed`; they never appear in
`/state` or on the anointed agent's own prompt line. Thread/oracle scheduling
and expiry: [02-engine-core.md](02-engine-core.md#anointed-divine-matrix-phase-7).

### Divine Matrix Phase 8: Identity Forge

`identity_edit` mutates `agent["persona"]`, `agent["personality"]`, and/or
`agent["role"]` immediately. `_build_think_payload` reflects changes on the next
think: `personality` via `_personality_with_drift(agent)`, `role` and
`role_skill` from the updated role, and `self_prompt` from `agent["persona"]`
when `META_SYSTEM` is on (set in `_think_job` before the LLM call).
`identity_copy_overwrite` blends persona/personality toward a source agent each
think (`_advance_identity_forge_on_think`). Optional `syncMemories` plants up
to three source memory lines — never a full clone. Restore semantics:
[02-engine-core.md](02-engine-core.md#identity-forge-divine-matrix-phase-8).

### Sovereign God mode (Optional Phase 8): free-prose story compiler

`sim_engine.god_compile_prose(prose)` is a **distinct LLM path**, entirely
separate from the agent-cognition prompts documented above — it never reads
`civilization["directive"]`, never appears in `_build_think_payload`, and its
call never counts as a "decision" or "module" turn in any benchmark. It turns
operator-authored free prose into a draft `story_event` command that lands in
the SAME `_god_preview_cache` Phase 2 already established; the operator still
has to explicitly Preview (again, through the normal Story tab) and Apply.
The compiler itself never mutates state and never calls `god_apply`.

**Concurrency pool.** A compile is a genuinely blocking `self.d["lm_complete"]`
call made **while holding `self.lock`** (the engine's `threading.RLock`),
directly inside `god_compile_prose` — unlike agent decisions (`self._executor`,
`MAX_CONCURRENT_LLM = 3`) or PIANO modules (`self.piano_workers`,
`PIANO_CONCURRENT_LLM = 2`), the compiler does **not** use either pool. It is
routed to `MODEL_SMART` ("sim-smart") via `lm_complete(..., model="sim-smart")`
— `lm_complete`'s `model` parameter defaults to `None` (which resolves to
`MODEL_FAST`) for every pre-existing caller; the compiler is the one caller
that overrides it. This means a compile call temporarily blocks the tick
thread for up to `GOD_COMPILER_TIMEOUT_SEC = 10.0` seconds (an aggressive
timeout specifically to bound that exposure) and consumes one of
`sim-smart`'s own `OLLAMA_NUM_PARALLEL` slots — the same slots agent
decisions use — for the duration of the call. `GOD_COMPILER_MIN_INTERVAL_SEC
= 5.0` and `GOD_COMPILER_SESSION_CAP = 60` (module constants, sim_engine.py)
bound how often this can happen; there is no queue — an over-budget compile
request is rejected immediately, never buffered.

**Model routing — deliberately NOT `sim-fast`.** `sim-fast` already serves
`PIANO_MODULES` background cognition, and past `sim-fast` contention has
measurably increased PIANO module drops (see the Concurrency & context
sizing section above). The plan is explicit that this compiler must not
default onto that tier. Routing instead to `sim-smart` avoids reproducing
that specific regression, but it is a NEW, not-yet-measured source of
contention on `sim-smart`'s pool (the same pool every agent decision uses) —
see [specs/12-ops.md](12-ops.md) "Optional Phase 8" for the recommended A/B
contention protocol and the explicit statement that it has not been run.

**Schema-locked output, not free text.** The prompt built by
`_god_compiler_prompt` lists every `GOD_MODIFIER_RANGES` key and bound and
the three `_GOD_PRIMITIVE_KINDS` shapes inline, with two worked few-shot
examples (a small model needs the shape shown, not merely described). The
model's raw response must parse as `{"kind": "story_event", "payload": {...}}`
exactly (`_god_compiler_parse`); the `payload` is then run through the SAME
`_validate_god_story_event` every other story_event command uses
([02-engine-core.md](02-engine-core.md)) — an unknown modifier key,
out-of-range value, or unknown resource/structure/agent id is rejected with
the SAME error the validator would give a malformed hand-authored command,
never silently clamped or dropped.

**Dual gate.** `GOD_COMPILER_ENABLED` (env `SIM_GOD_COMPILER`, read once at
import, module-level, alongside `GOD_MODE_ENABLED`) is a SECOND flag —
`god_compile_prose` and the `/control/god/compile` route both require
`GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED`. Ships off by default; see
[specs/12-ops.md](12-ops.md) for why the flag stays off until an A/B
contention measurement is actually run.

**No god token.** `god_compile_prose(prose)` has no parameter for
`SIM_GOD_TOKEN` and never reads it — the token gate lives entirely in
server.py's route handler, checked BEFORE `engine.god_compile_prose` is ever
called, exactly like every other `/control/god/*` route.

### Daily Council prompt contract

When the engine sets `councilTurn` for a seated attendee, it routes that normal
think slot through a slim council-turn prompt rather than the ordinary prompt.
The prompt contains only the compact assembly state needed to deliberate:
era/tech tier, active projects and stalls, resource pressure, active rules,
whether an invention is required, the current agenda, and any open ballot. It
asks the agent to state an opinion and a feeling about the village's path toward
evolution, then to speak, optionally propose, or vote using the council actions.
The elder's turn additionally requests a verdict/ruling once the ballot can be
decided; that verdict turn is high-stakes. Council turns replace ordinary think
turns and do not increase `MAX_CONCURRENT_LLM` or dispatch an extra call.

For a leaderless succession council, the same bounded prompt includes the
`leadership_vacancy` agenda item and the succession ballot's candidate list.
Discussion turns explicitly ask villagers to compare those named candidates.
Voting output uses `council_vote` with `candidate` set to one current candidate,
or `vote: "abstain"`; ordinary ballots continue using `vote:
"yes"|"no"|"abstain"`. There is no elder-verdict request until the recorded
village result has promoted a winner and roster refresh has seated that new
elder. Invalid/offline succession-vote fallback abstains instead of silently
supporting whichever candidate was listed first.

Every think payload, including non-council turns, receives at most
`COUNCIL_DIGEST_PROMPT_ENTRIES = 2` newest compact entries from
`civilization["councilDigests"]`, rendered as one short `Recent council:` line
in the same bounded context area as Chronicle history. The full
`council_transcript` audit table is never prompt material. This keeps council
continuity available to every agent, including agents who did not attend or
arrived later, while bounding context growth.

**`behavior_nudge` composition** (`_build_think_payload`, sim_engine.py
~8888-9330): candidate nudges are collected as `(priority, text)` pairs via a
local `note(prio, text)` helper, then capped to `MAX_BEHAVIOR_NUDGES = 3`
(sim_engine.py:467) — all P0 nudges are kept, then remaining slots fill from
P1/P2/P3 in ascending priority order (stable sort preserves emission order
within a class). Lower number = more urgent: P0 emergency/survival, P1
governance/commitment (succession vote, ruin-pressure), P2 rejection-recovery/
stall, P3 opportunity/idle. Invention-only and sprite-design-only turns bypass
this cap entirely with their own single-nudge override.

Repair/decay nudges use condition-dependent priority so decay competes fairly
for the 3 slots instead of being starved by P2 rejection notes every turn: a
locally-visible structure in disrepair (`condition < STRUCTURE_DISREPAIR_
THRESHOLD`, not a ruin) nudges at P2; a locally-visible ruin nudges at P1. A
second, village-wide ruin-pressure nudge (P1, independent of the agent's
current district) fires when either (a) more than 25% of all structures in the
civilization are ruins, or (b) any of the categories `house`, `market`,
`workshop`, `foundry`, `granary`, `farm_plot` has at least one built instance
but zero instances currently working (`condition >= STRUCTURE_DISREPAIR_
THRESHOLD` and not a ruin) — it names up to the 3 worst (lowest-condition)
structures village-wide with their `districtId` so an agent elsewhere can
travel and `repair_structure`.

**Per-kind rejection-nudge cooldown** (`_should_renudge`, sim_engine.py, just
above `_build_think_payload`): P2 rejection-recovery notes (gather, craft,
project, trade, recipe, upgrade, repair) previously re-fired identically on
every think turn for the full `DIRECTIVE_TTL_FRAMES` window even when nothing
about the rejection had changed, permanently crowding out the other 2 nudge
slots. Each agent now tracks a `lastRejectionNudgeFrame` dict keyed by
rejection kind, storing the rejection's own `frame` and the tick it was last
actually emitted as a nudge. A nudge for a given kind re-emits only if the
underlying rejection is new (its `frame` differs from what was last nudged) or
`DIRECTIVE_TTL_FRAMES` has fully elapsed since that kind was last nudged.
Other P2/P3 nudges (spoilage, shelter, homeless, blueprint/terraform/sprite/
quota/rationing/burial/abandonment rejections, idle/opportunity notes) are
unaffected.

**Persona-at-top-of-user-message rationale** (server.py:2923-2927): the
per-agent persona line is prepended to the *user* message, not appended to the
system prompt, because Ollama (llama.cpp-based, same as LM Studio) reuses KV
cache by longest common prefix per slot — per-agent text inside the system
message forced a full ~5k-token
reprocess on every agent rotation; keeping the system prompt byte-identical
across agents makes it a shared cached prefix instead.

### KV-cache prefix stability (Phase 2, TASKS_PENDING #2a)

The system message is designed to be byte-identical across every routine
decision turn, for every agent, all session long, so Ollama's
longest-common-prefix KV-cache reuse always fires for that shared prefix
(prefix-cache posture carries over unchanged from LM Studio — Ollama is
llama.cpp-based and reuses prompt prefixes per slot the same way)
(this is also why the persona line lives at the top of the *user* message,
above — see the rationale note just above this one).

- **Audit result:** every per-agent/per-tick/per-request value (name, role,
  hunger, resources, nearby agents, timestamps, districts, etc.) is rendered
  by `build_user_prompt`/`USER_PROMPT_TEMPLATE` into the *user* message only.
  `build_decision_payload` (server.py:~3361) never concatenates request data
  into `system_content`; `system_content` is always one of the four static
  module-level string constants (`SYSTEM_PROMPT`, `SYSTEM_PROMPT_SLIM`,
  `INVENTION_SYSTEM_PROMPT`, `SPRITE_UPGRADE_SYSTEM_PROMPT`).
- **The one SYSTEM_PROMPT reassignment** (server.py:~3750, gated on
  `TECH_TREE_ENABLED`) rewrites `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` once via
  `.replace()` to document the optional blueprint `"tier"` field.
  `TECH_TREE_ENABLED` is a hardcoded module constant in sim_engine.py (never
  flipped by a route or control endpoint), and this code runs at module
  import time, before the Flask app serves any request — so the rewrite
  cannot fire mid-session; whatever `SYSTEM_PROMPT` is after import is what
  every routine turn for the rest of the process sees.
- **Slim-retry exception (by design):** the context-overflow retry path
  (`run_agent_decision`, `slim=True`) deliberately swaps `SYSTEM_PROMPT` for
  `SYSTEM_PROMPT_SLIM` — a different, shorter prefix — and therefore forfeits
  KV-cache reuse for that one retried call. This is expected, rare (only
  fires after a context-overflow on the primary call), and acceptable; it is
  not tracked by the mismatch guard below.
- **Startup proof:** on boot (and again immediately if the `TECH_TREE_ENABLED`
  rewrite fires), the server prints a `[server] system prompt sha256=<first
  12 hex chars>` line so a soak's stdout/log can show the hash appears
  exactly once (or, if rewritten, exactly twice, both before any traffic)
  and never changes again for the life of the process.
- **Mid-session mismatch guard:** `_check_system_prompt_stability()` (server.py,
  just above `build_decision_payload`) runs only on the primary (non-slim)
  routine-turn dispatch, and only when `SYSTEM_PROMPT_AT_LOAD_TIME` is False
  (see below) — an intentionally *omitted* system message is not a "changed
  prefix" and must not trip this guard. Fast path is an `is` identity check
  against the system string used on the previous routine turn (near-zero
  cost, since `SYSTEM_PROMPT` is a stable module global); only on an identity
  mismatch does it fall back to a value/hash comparison. If the content has
  actually changed, it prints one `[server] WARNING: system prompt changed
  mid-session (cache invalidated) old_sha256=... new_sha256=...` line
  (log-once, never raises) so a regression that silently reintroduces a
  per-call system rebuild is observable in soak logs instead of only showing
  up as an unexplained latency regression.

### Load-time rulebook (`SYSTEM_PROMPT_AT_LOAD_TIME`, default False, dark) {#load-time-rulebook}

Phase 6 of `docs/plan-ollama-migration.md` (TASKS_PENDING item 2b revived —
LM Studio could never set a default system prompt at load time, see that
item's history; Ollama's Modelfile `SYSTEM` directive is the mechanism that
was missing). Ships **dark** (flag False) — machinery is in place but
inactive pending an A/B soak.

- **The flag** (`server.py`, near `MODEL_SMART`/`MODEL_FAST`): when True, the
  primary (non-slim) dispatch of every routine/high-stakes decision turn that
  is not `sprite_design_only` or `invention_only` — i.e. the same "else"
  branch of `build_decision_payload` that already used `SYSTEM_PROMPT`/
  `SYSTEM_PROMPT_SLIM` — omits the system message from `messages` entirely
  and routes to `MODEL_SMART_SYS` (`"sim-smart-sys"`) instead of
  `MODEL_SMART`. The slim-retry path (`slim=True`), `invention_only`, and
  `sprite_design_only` turns are **unaffected**: they always send their own
  explicit system message (`SYSTEM_PROMPT_SLIM` / `INVENTION_SYSTEM_PROMPT` /
  `SPRITE_UPGRADE_SYSTEM_PROMPT` respectively) and stay on `MODEL_SMART` for
  cache-locality — their prompts differ from the baked `SYSTEM_PROMPT`, and
  Ollama's verified semantics (an explicit request-time system message always
  *replaces*, never concatenates with, a Modelfile `SYSTEM` directive — see
  `ollama_config.md` "Modelfile SYSTEM semantics") make them correct either
  way, so there's no correctness reason to route them to `sim-smart-sys`.
- **`sim-smart-sys`**: a Modelfile-generated Ollama model, SEPARATE from
  `sim-smart` (same base GGUF/`num_ctx`/sampling params as
  `ollama/Modelfile.smart`, plus a `SYSTEM """..."""` block). Generated —
  never hand-copied — by `uv run python scripts/ollama_setup.py
  --with-system`, which imports `simulation/prompts.py`'s `SYSTEM_PROMPT`
  (the canonical text stays in `prompts.py`; the Modelfile is a build
  artifact) and writes `ollama/Modelfile.smart.system` (git-ignored-style
  generated file, DO-NOT-EDIT header, regenerate after any `SYSTEM_PROMPT`
  change) before running `ollama create sim-smart-sys -f
  ollama/Modelfile.smart.system`. Creating/updating `sim-smart-sys` never
  touches the live `sim-smart`/`sim-fast` models (distinct name, safe to run
  while the sim server is up — verified live 2026-07-24: `/api/ps` showed
  `sim-smart`/`sim-fast` `size_vram`/`expires_at` unchanged across the
  `--with-system` run).
- **Gate before flipping** (same bar TASKS_PENDING item 2b originally
  specified): an A/B soak comparing decision fallback rate (`bad_response` +
  `role_fallback`) and action distribution against a flag-off session of
  similar length. The tripwire is "the model forgets a distant/baked system
  prompt under a long user message" — a real risk for small local models that
  was never actually tested here (LM Studio could not reach this experiment
  at all; see TASKS_PENDING item 2b's history). Flip procedure:
  `ollama_config.md` "Load-time rulebook (dark)".
- **Payoff if the gate passes:** ~3k tokens off every routine decision turn
  (the rulebook is baked in instead of resent), the `context_overflow` class
  should largely disappear, and per-turn prompt processing drops by roughly
  half.

Measured prompt size: ~3,100-3,400 prompt tokens per routine decision call
(docs/REFERENCE.md:40); invention-only prompts run larger due to the
function-block schema and sprite few-shot example (worst case ~6,163 tokens
measured, per the `HIGH_STAKES_MAX_TOKENS` comment, server.py:120-122).

`MEMORY_PROMPT_CHAR_BUDGET = 900` (server.py, raised from 600 — see
[Wiki-style compounding memory](#wiki-memory) below) caps the composed
"Recent memory:" line; `compose_memory()` (server.py:1238-1271) merges the
client's compacted memory slice with up to 4 salient entries retrieved from
the in-process hashing-trick vector store (128-dim, `MEMORY_DIM`), dropping
oldest lines first and hard-truncating if still over budget.

### MemoryStore persistence (restart-stable, Phase 1 TASKS_PENDING #1)

`MemoryStore` (server.py:416) is constructed against `MEMORY_STORE_PATH =
simulation/memory_store.json` (server.py, next to `state.db`'s own
`os.path.dirname(os.path.abspath(__file__))` derivation) — a restart-stable
path, not the per-session log directory. This matters because `agent
["memory"]` tiers persist via `state.db` already, but the *semantic-recall
embedding index* (`memory_store`) previously lived only in
`simulation/logs/<timestamp>/memory.json`, so every server restart silently
started the index empty even though agent-visible memory survived.

- **Load-on-init.** `MemoryStore.__init__` calls `_load_locked_startup()`,
  which reads `MEMORY_STORE_PATH` if present and rebuilds entries via
  `import_entries()` (re-embedding each text with the same offline
  hashing-trick `embed_text()` — no LLM call, safe at cold boot).
  Absent file → starts empty (`_load_status = ("absent", 0)`). Corrupt/
  unparseable file (bad JSON, wrong shape) → also starts empty but tagged
  `("corrupt", 0)`, and never raises — persistence failures must not break
  server startup.
- **Startup observability.** One `[server]` line logs the load status,
  entry count, and path (e.g. `[server] MemoryStore loaded (1200 entries)
  from .../simulation/memory_store.json`), and `session_logger.log_benchmark
  ("memory_store_loaded", entry_count)` is emitted once so a future
  regression to empty-on-restart shows up in `benchmark.jsonl`, not just a
  console line.
- **Per-session inspection mirror.** `memory_store` is constructed with
  `mirror_path=os.path.join(session_logger.dir, "memory.json")`. Every
  `_persist()` (debounced by `MEMORY_PERSIST_EVERY`, and always flushed on
  `clean()`) writes the stable path first, then best-effort mirrors the same
  payload (entries minus the recomputable `vec` field) into the session
  log dir's `memory.json` for human inspection. The mirror write is wrapped
  in its own `try/except OSError` — a failed mirror write never affects the
  stable store.
- **Reset semantics.** `/control/reset` → `SimEngine.reset()` (sim_engine.py)
  calls `memory_store.clear()`, which wipes in-memory entries AND flushes
  the now-empty store to `MEMORY_STORE_PATH` (matching the existing
  `_piano_module_cache` wipe-on-reset precedent) — a reset drops the whole
  world, so a restart afterward must not resurrect pre-reset semantic
  memories. `reset()` logs a `[server]` line confirming the clear.

### Wiki-style compounding memory (`WIKI_MEMORY`, default False) {#wiki-memory}

TASKS_PENDING item 3 / plan Phase 4. Goal: long-term memory that merges and
reconciles instead of FIFO-dropping (Karpathy LLM-Wiki pattern), without
adding any new LLM call site or timer — it upgrades what
`_run_memory_maintenance`'s existing round-robin call already does.

- **Structure.** `agent["memoryWiki"]` is a dict of three named sections —
  `relationships`, `goals`, `lessons` — each hard-capped at
  `WIKI_SECTION_CHAR_CAP = 300` chars (sim_engine.py, next to `LONG_MEM_CAP`).
  Always present (`{}` initial shape, populated only when the flag is on) so
  persistence via `state.db` is free — same pattern as `moduleReports`. See
  [06-agents.md](06-agents.md#wiki-style-compounding-memory-wiki_memory-default-false)
  for the agent data-shape entry.
- **One-call budget, preserved.** `_run_memory_maintenance` still fires only
  every `MEMORY_TICK_FRAMES = 1800` frames, one agent per pass, and still
  makes exactly one `lm_complete` call per pass when the agent has ≥4 recent
  non-summary memories. When `WIKI_MEMORY` is False, that call is byte-for-
  byte the original summarize-and-append call (one-flag revert guarantee).
  When True, `_run_wiki_memory_merge()` replaces it with a single merge call:
  system prompt casts the model as the agent's memory keeper, given the
  three current wiki sections plus the batch of recent raw memories, and
  asks it to return updated sections that merge new facts in (dedup,
  reconcile, drop nothing still relevant); `max_tokens=220`,
  `temperature=0.4`, via `self.d["lm_complete"]` (MODEL_FAST) — same
  caller-set budget shape as the flag-off call.
- **Deterministic parse, no `json.loads`.** The prompt demands exactly three
  labeled lines (`RELATIONSHIPS: ...`, `GOALS: ...`, `LESSONS: ...`) plus an
  optional fourth (`CONTRADICTION: ...`, only when the model resolved a
  conflict). Parsing is by line-prefix (case-insensitive match, original
  case kept for content); a missing line keeps the prior section text
  unchanged, extra/unrecognized lines are ignored.
- **Lint = same prompt, zero extra calls.** The contradiction-resolution
  instruction from the merge prompt doubles as the lint pass — no separate
  call. When the response includes a `CONTRADICTION:` line, it is pushed to
  `activity.jsonl` via `_push_activity(f"{agent['name']} reconciled a
  memory: {note}")` — observable, no new call site.
- **Poisoning guard reused.** Each parsed section is validated with the same
  `is_scaffold_text` d-hook the flag-off longTerm-cleaning pass already uses;
  a scaffold-flagged section is discarded and the prior section text is kept
  instead. Every section (parsed or carried over) is hard-truncated at
  `WIKI_SECTION_CHAR_CAP` before being written back.
- **Semantic recall still fed, both modes.** The merged `lessons` section is
  stored into the vector store as `kind="summary"` (mirroring the flag-off
  path's own `ms.store(...)` call for its one-sentence summary), so semantic
  recall keeps working whichever mode is active. The existing `longTerm`
  list is untouched by the wiki path (it only grows via the flag-off branch).
- **Prompt surface.** `_memory_for_prompt(agent)` prepends up to three
  `"wiki <section>: ..."` lines (only for non-empty sections) ahead of the
  existing last-3-longTerm + last-4-shortTerm + last-4-working slice —
  additive, never a replacement, per the guiding principle that the LLM
  never loses what it previously saw. `MEMORY_PROMPT_CHAR_BUDGET` was raised
  600 → 900 (server.py) so the wiki lines have real headroom instead of
  being the first thing `_cap_memory_text`'s oldest-first eviction drops
  (wiki lines are prepended, i.e. logically "oldest" in list order).
- **`/state` echo.** `WIKI_MEMORY` is echoed in `config.flags` alongside the
  other cognition flags (sim_engine.py `_serialize_state`-adjacent state
  payload build).

## Decision handling

`extract_json_decision(text)` (server.py) fallback ladder: (1) strip
markdown code fences, try `json.loads` on the whole text; (2) scan for the
first balanced `{...}` block via brace-depth counting and parse that; (3) regex
for a bare `"action": "..."` (and best-effort `target`/`message`) to build a
minimal decision dict when the JSON is truncated/malformed. Returns `None` if
even the action regex fails. `lm_message_text()` (server.py) reads Ollama's
`message.content` — there is no `reasoning_content` fallback channel anymore
(that was an LM Studio quirk); with `think:false` sent on every call, content
should already be clean JSON, but as defense in depth any `<think>...</think>`
block that leaks in anyway is stripped rather than fed to the JSON parser.

`normalize_decision(decision, agent_data)` (server.py:2025-2173) — per-action
validation, each failure substituting `role_fallback_action()` with a note
appended to `reasoning` (and often a `*_rejection_note` field the engine
surfaces to the agent's next prompt):

| Action | Validation |
|---|---|
| `start_terraform` | must infer a valid terraform target (`_infer_terraform_decision`) |
| `upgrade_structure` | target must match an id/type/name in `upgradeable_structures` |
| `submit_structure_sprite` | only valid during a `sprite_design_only` turn; sprite must pass `validate_sprite_block` (min rows/cols) |
| `propose_blueprint` | `validate_blueprint()` (id/needs/function/tier rules — see specs/09) |
| `sage_review_blueprint` | role must be elder; target must be a pending id; `sage_decision` in approve/deny |
| `approve_blueprint`/`reject_blueprint` | role must be elder; target must be a pending id |
| `assign_task` | role must be elder; target must be an idle agent name; message required |
| `switch_role` | `new_role` (or `target`) must be a key in `ROLES` |
| `move_to_district` | promotes `target_district` into `target` if target is empty (the engine only reads `target`) |
| `talk_to_nearby` | target/message both required, target must be in the nearby-agents list, nearby list non-empty |
| `divine_response` (when active Voice guidance is unacknowledged) | must be an object with `stance` in `follow`/`continue` and a non-empty `reason` string; missing/invalid values are **not** rejected — see Voice binding guidance above |
| every other action | passed through as-is (any `blueprint` key stripped unless the action is one of the blueprint-carrying ones) |

`role_fallback_action(role, agent_data)` (server.py:1890-2022) priority
ladder: (1) `switch_role` if the village needs a role this agent can fill and
it isn't already elder/builder/healer; (2) elder-only: resume a pending
blueprint review (`approve_blueprint` if a review is ready, else
`sage_review_blueprint`); (3) elder-only: `assign_task` to the longest-idle
agent; (4) `upgrade_structure` if an upgradeable structure exists and there's
no active project; (5) `start_project`/`collect_resource` if no active project
(gate on `invention_status` REQUIRED); (6) `contribute_resources` if the agent
already holds a resource the project needs; (7) role-specific defaults
(farmer/fisher/gatherer → move-then-collect; miner → move-then-mine; builder →
contribute; trader → move to market; guard/scout/explorer → patrol;
healer/elder/blacksmith → contribute or return to village); (8) generic
`collect_resource` fallback.

**Error paths surfaced by `run_agent_decision`** (not normalized, returned
directly to the engine as `{"error": ..., "action": "rest"}`): `"llm
offline"` (request exception on every attempt including retries, or a
detected missing-model setup failure — see Routing), `"compute_error"`
(an Ollama compute-error body), `"server_error"` (any uncaught exception).
Anything else that fails JSON decoding or schema extraction becomes a
`role_fallback_action` result tagged with error `"bad_response"` (or
`"context_overflow"` if the slim retry still failed) — the engine never sees
a raw error for these, only a normal-looking decision.

## Retries & degradation

All in `run_agent_decision()` (server.py), each a single retry (no loops).
**Orphan caveat (Phase 0 operational finding):** Ollama does not cancel
server-side generation when a client aborts/times out a `stream:false`
request — an orphaned timed-out request keeps consuming a queue slot. None of
the paths below retry on a `requests.exceptions.RequestException` (including
`Timeout`); every one returns the offline fallback immediately instead, so
this code never compounds queue depth with a naive retry-on-timeout loop.

1. **format-rejected retry**: on `looks_like_response_format_error`, disable
   `_structured_output_enabled` session-wide, drop `response_format`, retry
   once.
2. **missing-model setup failure (no retry)**: on
   `looks_like_model_not_found_error`, log a `[server]` line once (pointing
   at `uv run python scripts/ollama_setup.py`), disable
   `_model_routing_enabled` session-wide (avoids repeat-logging only — there
   is no alternate model id to fall back to), and return `{"error": "llm
   offline", "action": "rest"}` immediately. Unlike LM Studio's
   `"local-model"` alias retry, a missing `sim-smart`/`sim-fast` is a setup
   problem, not a transient one.
3. **context-overflow retry**: on `is_context_overflow_error` — Ollama's
   structured HTTP 400 `{"error": {"type": "exceed_context_size_error",
   "message": ..., "n_prompt_tokens":, "n_ctx":}}` (or, as a fallback for any
   future Ollama build that changes the type string, a message containing
   both "context" and "exceed") — rebuild the payload with `slim=True`
   (`SYSTEM_PROMPT_SLIM`, no memory line, no recent conversations) and retry
   once; any further failure falls through to `bad_response_fallback` tagged
   `error="context_overflow"`. This replaces LM Studio's string-sniffed
   "Context size has been exceeded." error and its silent-truncation
   assumption — Ollama 0.32.3 does **not** silently truncate, it errors
   instead (Phase 0 finding #5, ollama_config.md).

## Civ-1 library lessons

When `LIBRARY_SCALING_ENABLED` is enabled, an agent in a district with a
working Library receives a `library_lessons` prompt line. It contains at most
three highest preserved skill records and two newest chronicle entries, with a
480-character cap; it is omitted otherwise.

## Routing

`model_for_decision(data)` = `MODEL_SMART` unconditionally (server.py) —
every decision turn, routine or high-stakes, routes to `sim-smart`.
`is_high_stakes_turn(data)` is still computed and still fully controls
timeout/max_tokens/thinking-sampling selection (see the Ollama call settings
table); it no longer affects which model id is used. No fallback id — see
Retries for what happens if the routed id isn't created in Ollama.
`_base_high_stakes(data)` (server.py, unbudgeted): `sprite_design_only`,
`invention_only`, `role=="elder"`, or `invention_status` starting with
`"REQUIRED"`. `HIGH_STAKES_ENABLED_REASONS = {"emergency", "election",
"treaty_vote"}` (server.py:160) — extra reasons that ALSO route to
`THINKING_TIMEOUT_S`/thinking-on, gated by a rolling-window limiter:
`EXTRA_THINKING_PER_WINDOW=4` per `EXTRA_THINKING_WINDOW_S=60` seconds
(server.py:168-185), thread-safe via `_extra_thinking_lock`. Deliberately
excluded from the enabled-reasons set: `elder_blueprint_review` (redundant —
already high-stakes via the elder-role check) and `repeated_rejections` (too
frequent, would dominate the budget).

`resolve_high_stakes(data)` (server.py:221-241) resolves `is_high_stakes_turn`
exactly ONCE per request and stamps `data["_high_stakes_resolved"]`, because
the reasons budget is stateful and `is_high_stakes_turn()` is called from
multiple sites per request (payload build, timeout choice, the context-
overflow retry) that must all agree without re-consuming the budget.

## Concurrency & context sizing

`MAX_CONCURRENT_LLM = 3` (sim_engine.py, `ThreadPoolExecutor` bound on the
engine's decision-think worker pool, `self._executor`) — matches
`OLLAMA_NUM_PARALLEL=3` (ollama_config.md). `LLM_MIN_GAP_MS = 250` (minimum
spacing between decision dispatches). Context formula under Ollama is
**per-model**, not a shared token-budget-divided-by-slots formula like LM
Studio's: each model (`sim-smart`, `sim-fast`) has its own fixed `num_ctx`
baked into its Modelfile (20480 / 4096 respectively — see
`ollama/Modelfile.smart`, `ollama/Modelfile.fast`), and `OLLAMA_NUM_PARALLEL`
governs how many concurrent requests share that per-model context budget
(each parallel slot gets a fraction of `num_ctx`, similar in spirit to LM
Studio's old per-slot division, but configured per-model rather than as one
combined pool). Decision-call prompts (~3,100-3,400 routine tokens, up to
~6,163 for invention-only) must stay under the `sim-smart` per-slot share at
`OLLAMA_NUM_PARALLEL=3` against `num_ctx=20480`; the structured
`exceed_context_size_error` (see Retries) is the enforced backstop if a
prompt exceeds it.

`PIANO_MODULES` (sim_engine.py, default `True` since Sid-parity Phase 1) — the
Perception/Social/Desire/Reflection module fan-out is the default cognition
path, not experimental. Module calls run on their own pool,
`self.piano_workers` (`PIANO_CONCURRENT_LLM = 2`), bounded independently of
`MAX_CONCURRENT_LLM` so a module backlog can never starve the decision path —
`_run_piano_modules` submits to `piano_workers` and waits on the futures, it
never dispatches into `self._executor`. Every module call routes to
`MODEL_FAST` with a hard, non-blocking `PIANO_MODULE_TIMEOUT_S = 15s` timeout
(server.py `run_piano_module`); a timeout is dropped, never retried (per the
orphan caveat in Retries), logged to `llm.jsonl` with `"error":
"piano_module_timeout"`, and counted in the
`piano_module_drops` benchmark. Reports are cached per `(agent, module)` with
a `PIANO_MODULE_CACHE_TTL = 2` module-tick TTL so the perception/social/desire/
reflection stagger (perception+desire every module-tick, social every 2nd,
reflection every 3rd) fills an off-tick module's slot from its last real
report instead of an empty one. That decision-payload fill is age-labeled —
a fresh, same-tick report renders as the bare `module: text` form, while an
off-tick fill served from cache renders as `module (N turns ago): text` so
the Cognitive Controller can discount stale advice.

**Module prompt contract (Phase 1).** `run_piano_module` leads its user
message with `You ARE {agent_name}. Context: {context}` so the report's acting
agent is explicit rather than merely embedded in context. Each of the four
static `MODULE_PROMPTS` requires references only to agents, resources, and
numbers present in that context and prohibits inventing a name, quantity, or
statistic. The Social module additionally must never suggest coordinating
with, messaging, or requesting from that same agent. These reports remain
advisory input to the Cognitive Controller; this contract does not alter
decision actions or validation. `PIANO_MODULE_MAX_TOKENS = 90` is the
production one-sentence output budget (raised from 60 after the repeatable
module-quality screen's guarded 90-token variant reduced the recorded Phase 0
grounded-wrong modal count 1→0 with no category regression). The screen reads
that constant directly from `server.py`; its `--max-tokens 60` override
preserves the Phase 0 budget for controlled comparisons.

### Gated always-on PIANO (`ALWAYS_ON_MODULES`, default False)

`ALWAYS_ON_MODULES` is a dark, one-flag-revert scheduler change. When false,
the per-decision PIANO fan-out above is unchanged. When true,
`MODULE_PULSE_INTERVAL_S = 45` wakes the tick loop, builds a due-list, and
returns without submitting work if it is empty. A note is due only when its
agent's `contextDirty` flag is set by a meaningful context event, or its
wall-clock age reaches the long fossilization backstop
`MODULE_NOTE_MAX_AGE_S = 600`; a stale-but-correct quiet note is deliberately
not refreshed merely because a decision occurs. Incapacitated agents are
skipped when `MODULE_REFRESH_IDLE_SKIP` is true. Phase A does not add a
night-wide throttle. The scheduler remains dark by default; its 45-second
interval restores the existing GPU-rest window when the gate is off. Phase C's
optional night backstop has not been attempted.

The pulse orders dirty work before old work and retains the legacy
perception/desire, social x2, reflection x3 cadence as priority weights. It
submits at most `MODULE_PULSE_MAX_BATCH = 2` and the currently free
`PIANO_CONCURRENT_LLM` slots to `piano_workers`; only these always-on refresh
calls pass `MODULE_REFRESH_TIMEOUT_S = 60` to `run_piano_module`. The legacy
per-decision fan-out retains its 15-second HTTP / 18-second future-wait
behavior. Completions re-acquire the engine lock and write `{tick, text,
wall_ts}` into both the hot cache and the persistent `agent["moduleReports"]`
mirror. Failures preserve the old note and leave the agent dirty for the next
pulse. The Attempt-2 freshness target was median note age <=120 seconds, with
decision p50 as the latency tiebreak after freshness and refresh-failure rate
are acceptable.

**Phase B gate outcome: FAILED, machinery stays dark.** Attempt 2's first
treatment soak (batch 2) missed both latency (+17.73%) and freshness gates.
Its permitted retune, batch 1, passed latency on re-soak (+13.47%, within
+15%) but failed freshness (median note age 619.0s, all 39 pulses dispatching
exactly 1 refresh with zero empty pulses) — one refresh per 45-second pulse
cannot keep the full note set fresh. No `MODULE_PULSE_MAX_BATCH` value
satisfies both gates simultaneously on the single-GPU reference hardware
(contention favors latency at low batch, throughput favors freshness at
higher batch). Per the two-soak stop rule, `ALWAYS_ON_MODULES` is rolled back
to `False` and `MODULE_PULSE_MAX_BATCH` restored to its Phase A default of 2;
full numbers are recorded in `ollama_config.md`. The scheduler code above
remains intact and exercised by the deterministic smoke, dark until a second
GPU or a materially faster/smaller fast model is available to retry.

In this mode `_run_piano_modules` is decision-path assembly only: it launches
no futures and labels cached reports with wall-clock age, for example
`social (73s ago): ...`. Notes older than twice `MODULE_NOTE_MAX_AGE_S` are
omitted. Benchmark periods log `module_note_age` (average/max of read notes),
`module_pulse_work` (the per-pulse dispatch counts, including zero-work
pulses), and `module_refresh_failures`, alongside existing
`piano_module_latency`.

**Working memory survives save/restore.** `_piano_module_cache` is
engine-memory, but after every think the post-think callback (still holding
`self.lock`, right after it sets `agent["moduleTick"]`) mirrors that agent's
cache entry into `agent["moduleReports"] = {module: {"tick", "text"}}` — a
persistence-only field the hot path never reads. Because `state.db` already
serializes each agent dict wholesale (`_serialize_state`), this rides along
for free. `restore_state()` rebuilds `self._piano_module_cache` from every
restored agent's `moduleReports` (defaulting to `{}` for pre-Phase-B saves),
so a restart no longer runs every module blind for up to
`PIANO_MODULE_CACHE_TTL` ticks. `/control/reset` is unchanged — it still wipes
`_piano_module_cache` to `{}` — only `restore_state` rehydrates it.

**Cross-module visibility (working-memory half-step).** Before dispatching
`to_run` for an agent's turn, `_run_piano_modules` builds one shared
`last_reports=` suffix from every cached report still within
`PIANO_CROSS_CONTEXT_TTL = 6` module-ticks (a separate, more tolerant TTL
than `PIANO_MODULE_CACHE_TTL` above, which only gates the decision payload's
off-tick fills) and appends it (`context + "; " + suffix`) to the context
string every module dispatched this turn receives, e.g.
`last_reports=desire(1 ago): stockpile wood | social(2 ago): ask Sage about
the blueprint`. Entries are formatted `module(N ago): text`; a module seeing
its own previous report is intentional (continuity, especially for
Reflection). `MODULE_PROMPTS` (server.py) each carry one added clause telling
the module to build on or correct prior reports rather than repeat them. No
extra LLM call is added — the suffix rides on the existing module calls.
Reports are capped at 200 chars (server.py), so the suffix adds at most
~4 × 60 ≈ 240 tokens per module call, comfortably inside the ~3,400-token/slot
formula above with no change to the formula itself.

Revised context picture with PIANO on, under Ollama's per-model `num_ctx`
scheme (see "Concurrency & context sizing" above): decision calls
(`MAX_CONCURRENT_LLM=3`) run against `sim-smart`/`sim-fast`'s `num_ctx=20480`
(decision prompts, ~3,400-6,163 tokens, fit comfortably even split across 3
parallel slots); module calls (`PIANO_CONCURRENT_LLM=2`) run against
`sim-fast`'s `num_ctx=4096` (module prompts are ~1k tokens, generous
headroom). `scripts/ollama_setup.py` applies the target config (see
ollama_config.md's required-settings table): `OLLAMA_NUM_PARALLEL=3`,
`OLLAMA_MAX_LOADED_MODELS=2` (dual residency — the Ollama 0.32.3 default of 1
evicts one model whenever the other is called), `OLLAMA_FLASH_ATTENTION=1`,
`OLLAMA_KEEP_ALIVE=-1`. Both `num_ctx` values are sufficient for the default
roster (VRAM gate passed at rung 0, no fallback needed — ollama_config.md);
if VRAM pressure appears (e.g. Path 1 flags growing prompt size further),
`OLLAMA_KV_CACHE_TYPE=q8_0` is the first lever, not yet applied. Roster size
is set via a JSON POST body field (`{"agents": N}`) on `/control/reset` (see
specs/04-http-api.md), not a URL query parameter, or via the `SIM_AGENTS`
environment variable at server startup (server.py, default 8, clamped to
`MAX_ROSTER_SIZE = 20` — see specs/02-engine-core.md and specs/06-agents.md).

`META_SYSTEM` (sim_engine.py, default `True` since Sid-parity Phase 3) —
autobiography/persona meta update, still bounded by `MAX_CONCURRENT_LLM`
(runs inline on the decision path, not on `piano_workers`). Authored beliefs
and adoption events give the rotating autobiography update material to
summarize.

### Scale headroom (Phase 6) — concurrency unchanged

Sid-parity Phase 6 raises the roster ceiling to `MAX_ROSTER_SIZE = 20` (see
specs/02-engine-core.md) but deliberately does **not** raise
`MAX_CONCURRENT_LLM` or `PIANO_CONCURRENT_LLM` — the concurrency budget above
was sized for the loaded Ollama config (`OLLAMA_NUM_PARALLEL=3`,
`OLLAMA_MAX_LOADED_MODELS=2`) and reopening it needs its own measured soak,
not a side effect of a roster-size change. `MAX_CONCURRENT_LLM` +
`LLM_MIN_GAP_MS` are the throughput cap regardless of roster size; the actual
scaling risk at a bigger roster was *fairness*, not throughput — more agents
contending for the same fixed number of slots meant some agents could lose
the pool-full race indefinitely under the old fixed-roster-order dispatch
(see "Dispatch fairness (Phase 6)" in specs/02-engine-core.md). That ordering
fix is what keeps average think latency reasonable at roster 20 without
touching either concurrency constant; per-agent `thinkInterval` staggering is
otherwise unchanged (still `360 + i*60`, `240` for the elder), so total LLM
call volume per unit time still scales with roster size — a roster of 20 at
the default cadence does dispatch more decision calls/minute in aggregate
than a roster of 8, but no single agent is starved by it, and the existing
worker-pool cap prevents that aggregate demand from exceeding what the loaded
Ollama config already serves for the default roster.
