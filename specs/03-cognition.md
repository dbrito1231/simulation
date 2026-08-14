# SPEC 03 — Cognition (LLM Pipeline)

The LLM think pipeline: prompt construction, structured-output decoding,
decision validation/fallback, model routing, and retry/degradation behavior.

**Canonical for:** all Ollama call settings (models, timeouts, sampling,
token budgets), `DECISION_SCHEMA`/structured-output mode, prompt template
sections, `normalize_decision`/`role_fallback_action` rules, decision-audit
correlation id (`_decision_id` minting), model routing +
high-stakes policy, retry/degradation ladders, concurrency/context-sizing
constants. **See also:** [specs/01-architecture.md](01-architecture.md) (data
flow, flag index), [specs/02-engine-core.md](02-engine-core.md) (tick/think
scheduling, Sage emergency), [specs/04-http-api.md](04-http-api.md) (routes
that front this pipeline), [specs/07-actions.md](07-actions.md) (the action
catalog — not repeated here).

> **Migration note (2026-07-24):** LLM runtime cut over from LM Studio to
> Ollama (`docs/archive/plan-ollama-migration.md` Phases 2-3). LM Studio is
> permanently unavailable. See [ollama_config.md](../ollama_config.md) for
> settings/contract reference (required env vars, VRAM gate, restore
> procedure). This spec documents what `simulation/server.py` sends: native
> `POST …/api/chat` (`OLLAMA_CHAT_URL`), `stream:false`. Host:port comes from
> `SIM_OLLAMA_HOST` (default `localhost:11434`); `/api/chat` is fixed in code
> (not configurable). Unset env → `http://localhost:11434/api/chat`. At import,
> `server.py` validates `SIM_OLLAMA_HOST` is **host:port only** (rejects empty/
> whitespace, schemes, paths, or a missing port) and raises `ValueError` on
> misconfiguration — same fail-fast class as a bad `SIM_PORT`.
> `MODEL_SMART = "sim-smart"` (qwen3.5 9B, `num_ctx=20480`) and
> `MODEL_FAST = "sim-fast"` (`llama3.2:3b`, `num_ctx=4096`) are distinct
> models split **by workload kind, not by decision stakes**: ALL decision
> turns route to `MODEL_SMART`; `MODEL_FAST` serves only background cognition
> (PIANO modules, memory summarizer/wiki merge, meta system, belief pitch,
> chronicle saga — every direct `lm_complete()` caller). This supersedes an initial Phase 3
> attempt that routed routine decisions to `sim-fast`: a live soak measured
> `piano_module_drops` at ~25-38% (vs. ~9% pre-migration) and rising module
> latencies, because routine decisions and PIANO modules contended for
> `sim-fast`'s `OLLAMA_NUM_PARALLEL=3` slots. Decisions on `sim-smart` use a
> separate slot pool. Both models are created via `scripts/ollama_setup.py`
> and kept resident with `OLLAMA_KEEP_ALIVE=-1`. A module-init check prints
> `[server] WARNING: MODEL_FAST == MODEL_SMART` if the constants collapse
> (env override, hotfix, etc.) — a **warning, not a hard assert/crash**: a
> hard crash on config regression would strand the 24/7 server with no
> rollback runtime (LM Studio is gone). The warning fires once at import;
> the server continues; decisions and background cognition would share the
> smart model's queue until fixed. Historical LM Studio numbers remain in
> comments and `ollama_config.md`'s "Thinking-epidemic history" section
> (`lms_config.md` removed in Phase 5) but no longer describe the running
> system.

## Ollama call settings

| Call type | System prompt | Model | max_tokens (→ options.num_predict) | temperature | timeout | sampling |
|---|---|---|---|---|---|---|
| Routine decision | `SYSTEM_PROMPT` (or `SYSTEM_PROMPT_SLIM` on retry) | `MODEL_SMART` (`sim-smart`, qwen3.5 9B) | 512 | 0.4 | `DEFAULT_TIMEOUT_S`=30s | `NON_THINKING_SAMPLING` + `think:false` |
| High-stakes decision (elder / `invention_status` REQUIRED / rate-limited emergency,election,treaty_vote) | `SYSTEM_PROMPT`/slim | `MODEL_SMART` (`sim-smart`, qwen3.5 9B) | 512 (1600 only if thinking re-enabled, currently dead code) | 0.4 | `THINKING_TIMEOUT_S`=75s | `THINKING_SAMPLING` if `THINKING_ENABLED_HIGH_STAKES` (omits `think`, i.e. thinking on), else same as routine |
| Invention-only turn | `INVENTION_SYSTEM_PROMPT` | `MODEL_SMART` (sprite/invention always high-stakes) | `INVENTION_MAX_TOKENS`=1024 | `INVENTION_TEMPERATURE`=0.6 | 75s | as above |
| Daily Council turn | slim council-turn prompt | `MODEL_SMART`; routine settings except an elder verdict uses the high-stakes settings | bounded routine output | routine temperature | routine timeout, or 75s for elder verdict | routine sampling, or high-stakes sampling for elder verdict |
| Sprite-design turn | `SPRITE_UPGRADE_SYSTEM_PROMPT` | `MODEL_SMART` | 768 | 0.3 | 75s | as above |
| Background `lm_complete` (memory summarizer/wiki merge, PIANO modules, meta system/autobiography, belief-pitch scoring, chronicle saga) | caller-supplied one-off prompt | `MODEL_FAST` always (`sim-fast`, llama3.2:3b) | caller-set (8/40/80/90/100/220 per call site; PIANO=`PIANO_MODULE_MAX_TOKENS`=90; saga ≈150-word target at implementation) | caller-set (0.0-0.6) | 30s (hardcoded, not `DEFAULT_TIMEOUT_S`); PIANO fan-out 15s (`PIANO_MODULE_TIMEOUT_S`), always-on refresh 60s (`MODULE_REFRESH_TIMEOUT_S`) | `NON_THINKING_SAMPLING` + `think:false` |
| Chronicle saga (day boundary, `CHRONICLE_SAGA_ENABLED`) | caller-built prompt: day's chronicle window + bounded `conversation.jsonl` excerpt + civ counters | `MODEL_FAST` (`sim-fast`) | caller-set (~150-word target) | caller-set (~0.4) | 30s (`lm_complete` default) | `NON_THINKING_SAMPLING` + `think:false` |

Routine and high-stakes rows both use `MODEL_SMART`; separate rows because
`is_high_stakes_turn` still determines timeout, max_tokens, and
thinking/sampling per turn.

Phase 3 input-size sanity check (all against `sim-fast`'s `num_ctx=4096`):
the largest background call is the wiki-memory merge
(`mixin_decisions.py:_run_wiki_memory_merge`, `max_tokens=220`) — its prompt is 3
wiki sections (`WIKI_SECTION_CHAR_CAP=300` chars each, ~225 tokens) plus up to
12 recent memories (each capped at 280 chars on store, ~840 tokens) plus a
~180-token system prompt, roughly 1,250 input tokens. The meta/autobiography
call (`run_meta_update`, `max_tokens=100`) joins up to 14 memories
(~980 tokens) plus report fields and a ~150-token system prompt, roughly
1,150 input tokens; the follow-up persona call adds the ~300-char
autobiography, still under 1,500 input tokens. All are well under half of 4096 — no truncation risk; documented finding, not
a code change.

`to_ollama_body(payload)` lives in `simulation/llm_wire.py` (moved from
`server.py`, docs/archive/plan-ollama-migration.md Phase 4, so
`scripts/llm_replay_bench.py` can import the conversion without importing
`server.py` — that module has import-time side effects; see
`simulation/prompts.py`'s docstring). `server.py` imports it
(`import llm_wire as _llm_wire; to_ollama_body = _llm_wire.to_ollama_body`)
after the `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` import; every call site still
refers to it as `to_ollama_body`. Single conversion point for all call sites:
takes the internal OpenAI-chat-completions-shaped payload (model, messages,
max_tokens, temperature, sampling keys, optional `response_format`, optional
boolean `think`) and produces the Ollama wire body — `messages` pass through;
`max_tokens` → `options.num_predict`; `temperature`/`top_p`/`top_k`/`min_p`/
`presence_penalty` → `options.*`; `response_format` (json_schema nesting, see
`build_response_format`) → `format` (extracted schema object, or `"json"` for
`json_object` mode); `think` passes through if present (omitted = let the
model think).

`MODEL_SMART = "sim-smart"` and `MODEL_FAST = "sim-fast"` (server.py).
`model_for_decision(data)` always returns `MODEL_SMART` — decisions never
route to `sim-fast`, regardless of `is_high_stakes_turn(data)`.
`is_high_stakes_turn()` still selects timeout (`THINKING_TIMEOUT_S` vs
`DEFAULT_TIMEOUT_S`), max_tokens (`HIGH_STAKES_MAX_TOKENS`), and
sampling/thinking (`THINKING_SAMPLING` vs `NON_THINKING_SAMPLING`) — only
model-id selection was removed. `MODEL_FAST` is for background cognition only
(PIANO modules, memory summarizer/wiki merge, meta system, belief-pitch
scoring, chronicle saga — every direct `lm_complete()` caller); see Concurrency & context
sizing for contention rationale. Fallback: Ollama has no LM-Studio-style
`"local-model"` alias — if `looks_like_model_not_found_error` fires, that is
a **setup failure**: the server logs a `[server]` line pointing at
`uv run python scripts/ollama_setup.py`, disables `_model_routing_enabled`
(session-wide, to avoid repeat-logging), and returns
`{"error": "llm offline", "action": "rest"}` immediately — no retry (see
Retries).

`NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}`;
`THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}` (server.py) — Qwen
model-card pins, sent on every call via `to_ollama_body` → `options`.

### Thinking-mode suppression (`THINKING_ENABLED_HIGH_STAKES`)

`DISABLE_THINKING_ROUTINE = True` (server.py): every routine turn sends
top-level `"think": false` in the Ollama `/api/chat` body (Phase 0 finding
#4: `eval_count` dropped 1288→81 tokens, wall time 77s→4.8s with
`think:false` vs. unset). The OpenAI-compat `/v1/chat/completions` endpoint
ignores `think:false` — this repo targets native `/api/chat` only (see
`OLLAMA_CHAT_URL`). (Historical: LM Studio used `"reasoning_effort": "none"`.)

`THINKING_ENABLED_HIGH_STAKES = False` (server.py) — since this flag is
False, `thinking_active` in `build_decision_payload` never goes True, so
high-stakes turns still use `NON_THINKING_SAMPLING` + `think:false`.
**Consequence (LM Studio-era finding, semantics unchanged):** a 2026-07-14
analysis of 48 high-stakes samples found reasoning content gave zero
measurable decision-quality benefit — identical JSON either way, routed through
the reasoning channel — while costing 33% concurrency (parallel 3→2 for
headroom). Reverted to `False` + `MAX_CONCURRENT_LLM=3`.
`HIGH_STAKES_MAX_TOKENS=1600` (server.py) is dead code, kept if thinking is
revisited — the `think:false` contract above is not optional (see
ollama_config.md's thinking-epidemic history note).

## Structured output

`STRUCTURED_OUTPUT_MODE = "json_schema"` (server.py:313). `build_response_format()`
(server.py:592-624) returns `{"type": "json_schema", "json_schema": {"name":
"agent_decision", "schema": DECISION_SCHEMA}}`, or `{"type": "json_object"}` if
the mode were `"json_object"`, or `None` if `"off"` or auto-disabled.
`build_response_format` itself stays in server.py (not split into
`simulation/_server/`) because it reads `DECISION_SCHEMA` directly and the
action-sync invariant requires `DECISION_SCHEMA` stay in server.py — see
[01-architecture.md](01-architecture.md).

`DECISION_SCHEMA` (server.py:357-527): `additionalProperties: False`;
`required: ["action", "reasoning"]`. Key properties: `action` (enum =
`DECISION_ACTIONS`, 47 entries — see specs/07-actions.md, not repeated here),
`divine_response` (nullable object, **required on every decision turn while
active Voice guidance is unacknowledged** — see "Voice binding guidance"
below; omitted on turns with no active unacked guidance), `target`/
`target_district`/`message`/`new_role` (nullable strings),
`relationship_update` (nullable object, values constrained to
ally/neutral/rival), `blueprint` (nullable object: id/name/needs/new_resources/
visual_style/sprite/function), `recipe` (nullable object: id/name/inputs/
station), `contract` (nullable object: `want` resource id, `qty`, `pay_coin`,
`deadline_frames` — all positive integers; `want` must be a known resource id),
`rule` (nullable object: id/name/kind/value/description), `vote`
(nullable string), `sage_decision` (nullable enum approve/deny), `sprite`
(nullable object: palette/grid — **bounded**, see below). **TECH_TREE_ENABLED import-time addition**
(server.py:2584-2591, applied only if the engine flag is on so flag-off
prompts stay byte-identical): adds `verdict` (nullable object with
`rejections`) and `blueprint.tier` (nullable integer) to the schema, and
rewrites `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` to document the tier field.

**Auto-disable on rejection:** `_structured_output_enabled` (module-level)
flips to `False` for the rest of the session — and the retry drops
`response_format` — on the first Ollama HTTP 400 or error body mentioning
`format`/`response_format`/`json_schema`/`grammar`/`schema`
(`looks_like_response_format_error`). Ollama's `format` field is stable (Phase
0 finding #2: full JSON schema honored in every trial); this is a safety net.

**Bounded sprite grid (grammar-level, not just post-hoc validation).**
`SPRITE_GRID_MIN = 4` / `SPRITE_GRID_MAX = 14` (server.py, above
`DECISION_SCHEMA`, exported via `_ENGINE_DEPS`). Top-level `sprite`
(`submit_structure_sprite`) is bounded at JSON-schema level: `palette` array
`minItems: 2` / `maxItems: 5`; `grid` array `minItems`/`maxItems` =
`SPRITE_GRID_MIN`/`SPRITE_GRID_MAX`, each row string same `minLength`/
`maxLength`. Phase 0 probe: 5/5 sprite-turn failures were Ollama
`done_reason: "length"` at `eval_count: 768` — generation truncated mid-JSON
with 30-100+ unbounded rows. The bound makes runaway unrepresentable at
generation time, grammar-enforcing limits `validate_sprite_block()` already
checked post-hoc. **Blueprint-nested** `sprite` (`blueprint.sprite`,
`propose_blueprint`/invention turns) stays unbounded — optional, never
rejected, no runaway risk.

`build_sprite_upgrade_prompt()`/`_sprite_upgrade_size_requirement()`
(server.py) render the per-dimension growth requirement for a sprite-design
turn rather than collapsing a `0` minimum (meaning "no growth required on
this axis" — see [05-world.md](05-world.md#structures)) into a fake floor of
`SPRITE_GRID_MIN`: "strictly more than N rows AND strictly more than N
columns", or just one axis, or "no minimum size requirement this turn" when
both are `0`, always followed by the stated `SPRITE_GRID_MIN`-`SPRITE_GRID_MAX`
ceiling. `SPRITE_UPGRADE_SYSTEM_PROMPT` rule 4 states the same per-dimension
growth-plus-ceiling contract instead of an unconditional "more rows AND more
columns".

## Prompt construction

`SYSTEM_PROMPT` and `SYSTEM_PROMPT_SLIM` live in `simulation/prompts.py`
(moved from server.py 2026-07-24, docs/archive/plan-ollama-migration.md Phase 6) —
single source of truth for `server.py` (`import prompts as _prompts`, aliased
at module scope) and `scripts/ollama_setup.py --with-system`. `prompts.py`
owns the `TECH_TREE_ENABLED`-gated rewrite (blueprint `"tier"` field) and the
two `[server] system prompt sha256=...` startup-proof prints — both fire at
`prompts.py` import (triggered by server.py before Flask construction).
`prompts.py` imports only `sim_engine` for `TECH_TREE_ENABLED` (no import-time
side effects); `server.py` is unsafe to import from setup scripts (opens
session log dir, constructs `SimEngine`).

`SYSTEM_PROMPT` (~20 numbered rule groups): talk-gating,
build-project/district steering, ecology/terraform, blueprints (two-stage Sage
review then approve/reject), survival (hunger/health/heal), crafting/recipes,
Sage-priority-absolute emergency response, Path 1 (tools/blocks/treaties),
emergent roles (switch_role), collective rules/voting, Cognitive Controller
(PIANO module weighing), upkeep/seasons (repair/spoilage), market/trade/
property (homes), population/governance (succession,
quotas/rationing), knowledge/culture (skill teaching), followed by the JSON
output contract and worked examples per action family (rest, contribute, talk,
propose_blueprint, sage_review_blueprint, approve_blueprint). Contracts
(`CONTRACTS_ENABLED`): rules C1/C2 + `contract` JSON field/schema are **not**
in the static rulebook — `prompts.CONTRACTS_SYSTEM_ADDENDUM` is appended by
`append_contracts_addendum()` in `build_decision_payload()` only when the flag
is on (D9 trim; flag-off routine system cost ≈0).

`SYSTEM_PROMPT_SLIM` is defined in `simulation/prompts.py` and re-exported at
server.py:33 (`SYSTEM_PROMPT_SLIM = _prompts.SYSTEM_PROMPT_SLIM`): `SYSTEM_PROMPT`
sliced at the first `"\nEXAMPLE ("` marker — same rules and JSON schema, no
worked examples. Used for the context-overflow retry (see Retries).

`INVENTION_SYSTEM_PROMPT` (server.py:647-686): dedicated ~85%-smaller
prompt for invention-only turns — output-format contract + blueprint
schema/example only, no village rulebook.

`SPRITE_UPGRADE_SYSTEM_PROMPT` (server.py:1388+): a dedicated prompt for the
sprite-design-only turn that follows a blueprint's mechanical approval.

`USER_PROMPT_TEMPLATE` (server.py:689-732): ordered sections — identity
(name/role/skill/personality/memory), vitals (resources/hunger/health/
relationships/beliefs), spatial (nearby agents/zone/district/known districts/
local stocks/terraform targets), flag-gated single lines (`season_line`,
`prices_line`, `weather_line`, `chronicle_line`, `path1_lines`,
`contracts_line`, `level_line` — each renders empty when its owning flag is
off or has nothing to show so prompts stay byte-identical across flag states,
per `build_user_prompt` server.py:1542-1732), build state
(structures/active project/progress), civilization state (directive,
`divine_lines` — see Sovereign God mode below, invention status, commitment,
idle agents, known resources/recipes, pending/rejected blueprints/recipes/
rules, reserved structure ids), social (recent conversations, inbox, module
reports), a `behavior_nudge` line, and finally `available_actions`.

`weather_line` (living-ecosystem Phase 5, `WEATHER_GOVERNANCE_ENABLED`):
`_weather_prompt_line()` (`mixin_structures_economy.py:433`) returns one short "Weather: ..."
line — but only while `civilization["weather"]["state"]` is `"storm"` or
`"clearing"`; `None` (and so an empty template slot) the rest of the time,
including whenever the flag or `WEATHER_ENABLED` is off. Follows the exact
`chronicle_line`/`council_digest_line` pattern (engine computes the string,
server folds it in only when set) and rides the existing think cycle — no new
LLM call, no new context section, just this one line so agents can reference
storm conditions in council.

`contracts_line` (`CONTRACTS_ENABLED`, F3.3): `_format_contracts_for_prompt()`
(`mixin_structures_economy.py`) returns a compact `"; "`-joined summary of up
to `MAX_CONTRACTS_PROMPT` (6) open/accepted contracts relevant to the actor
(own offers, bindings, or open/directed offers they may accept). The engine
sets `contracts_line` on the think payload only when the flag is on;
`build_user_prompt()` renders `Open contracts: ...` only when non-empty, so
flag-off prompts stay byte-identical and flag-on prompts with no contracts
add no line.

### Sovereign God mode (Phase 3): Voice binding guidance

`_build_think_payload` computes `divine_public_line`/`divine_private_line` per
agent via `_divine_prompt_lines(agent)` (`mixin_divine_matrix.py:44`) —
separate keys, never folded into `civilization["directive"]`.
`_divine_prompt_lines` re-checks `startFrame <= frame_tick < expiresFrame` for
`godState["providence"]` / `godState["privateOmens"][str(agent["id"])]` —
expired-but-not-yet-closed records must never reach a prompt. Whisper
campaigns apply one private omen per target; each agent's
`divine_private_line` reflects only their own omen text (campaign theme is
operator metadata, not injected). Both are `None` when `GOD_MODE_ENABLED` is off.

`build_user_prompt` (server.py) folds each into its own line when set —
same fold-in-only-when-set pattern as `weather_line` — immediately after
`Civilization directive: {directive}` via `{divine_lines}`:

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

`stance: "follow"` clears `goal` and `assignedTask` before applying the
chosen `action` (the LLM still picks the concrete action — guidance steers
intent, not the action). `stance: "continue"` leaves goals and assigned tasks
intact. `reason` is a short explanation surfaced in Sight and the Voice
Adherence panel.

**Schema-required while active.** `build_response_format()` (server.py) is
called per-request with `require_divine_response=bool(data.get(
"voice_guidance_active"))`. When True it builds a shallow copy of
`DECISION_SCHEMA` (never mutating the module-level schema — `MAX_CONCURRENT_LLM
= 3` means concurrent think-workers) that adds `"divine_response"` to
`required` and tightens its `type` from `["object", "null"]` to `"object"`.
When guidance is inactive the field stays optional/nullable.

**Missing/invalid `divine_response`.** `synthesize_divine_response()` is a
safety net for malformed objects, retries, and unstructured-mode degradation.
When Voice guidance is active and unacknowledged but the model omits
`divine_response`, returns a malformed object, or supplies an unknown
`stance`, `normalize_decision` **does not** reject the turn — it **synthesizes**
`{"stance": "continue", "reason": "missing_divine_response"}` and still
applies the validated `action`. Non-compliance signal for operators, not a
hard fallback to `rest`.

**Acknowledgement and the skip cap.** A turn with a **genuine** (non-
synthetic) `divine_response` acks its guidance entry immediately. A
**synthetic** response no longer auto-acks — `_record_divine_response_adherence`
(`mixin_divine_matrix.py:196`) increments a per-guidance skip counter
(providence's `skipCounts[agentIdStr]`, or the private omen's `skipCount` —
see [02-engine-core.md](02-engine-core.md)) via `_bump_voice_guidance_skip`.
Only at `GOD_VOICE_ACK_SKIP_CAP` (3) consecutive synthetic turns for the same
guidance id is the entry force-acked (`capped: true`) — same effect as a
genuine ack, logged as non-compliance. Until acked, every subsequent think
carries the same binding prompt lines and `divine_response` requirement; the
skip counter resets when guidance is replaced/expired/closed.

**Special-turn cancellation.** While Voice guidance is active and
unacknowledged, any pending `sprite_design_only` or `invention_only` special
turn for that agent is **cancelled/dropped** — not soft-deferred. The engine
clears the special-turn flag and returns the agent to the ordinary decision
path on the next think. Applying new Voice guidance (providence, private omen,
whisper target, or a proclamation that auto-applies as providence) also
cancels in-flight special turns for affected agents at apply time. Matrix
anoint/bush/story soft prompt lines are unchanged — only providence/private-
omen Voice guidance participates in this binding contract.

At most one public line and one private line, each capped at
`GOD_TEXT_MAX_CHARS = 240` characters (`GOD_TEXT_MAX_BYTES = 600` bytes) by
`_normalize_divine_text` at write time (`mixin_persistence.py:1149`, [02](02-engine-core.md)).

**Divine Matrix memory surgery (Phase 3).** `memory_insert` and `belief_plant`
write into the same `memory` slice `_memory_for_prompt` composes — via
`_god_memory_insert` with kinds `divine_false_memory` / `divine_belief` —
without touching public activity/communication/chronicle. `memory_delete`
removes matching `MemoryStore` rows and keyword-matching local tier lines.
Distinct from private omens: false memories are ordinary recall lines
immediately, not deferred until omen closure. The two divine lines add bounded
prompt text at maximum omen length — no raw intervention history, no Chronicle
duplication of private content. The elder `directive` line is unaffected: both
fields can be active simultaneously on separate template lines; agents see
village leadership vs. divine signal as distinct sources, never merged.

**Measurement gate.** Binding Voice guidance ships with the providence/omen
apply path; spot-check `llm.jsonl` for `divine_response` presence and
`divine.jsonl` / Sight for adherence before always-on god-guided deployment.
`SIM_GOD_MODE` ships dark (see [01](01-architecture.md)).

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
override while one is active (cap = 1). Prefer `sim-smart` for operator
overrides.

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

`sim_engine.god_compile_prose(prose)` is a **distinct LLM path**, separate
from agent-cognition prompts — it never reads `civilization["directive"]`,
never appears in `_build_think_payload`, and its call never counts as a
"decision" or "module" turn in any benchmark. It turns operator-authored free
prose into a draft `story_event` command in `_god_preview_cache`; the operator
still must Preview and Apply through the normal Story tab. The compiler never
mutates state and never calls `god_apply`.

**Concurrency pool.** A compile is a blocking `self.d["lm_complete"]` call
**while holding `self.lock`** (`threading.RLock`), inside `god_compile_prose` —
unlike agent decisions (`self._executor`, `MAX_CONCURRENT_LLM = 3`) or PIANO
modules (`self.piano_workers`, `PIANO_CONCURRENT_LLM = 2`), the compiler uses
neither pool. Routed to `MODEL_SMART` via `lm_complete(..., model="sim-smart")`
— `lm_complete`'s `model` defaults to `None` → `MODEL_FAST` for all other
callers; the compiler is the one override. A compile blocks the tick thread
for up to `GOD_COMPILER_TIMEOUT_SEC = 10.0` and consumes one of `sim-smart`'s
`OLLAMA_NUM_PARALLEL` slots (same slots agent decisions use).
`GOD_COMPILER_MIN_INTERVAL_SEC = 5.0` and `GOD_COMPILER_SESSION_CAP = 60`
(`constants.py:746-747`) bound frequency; no queue — over-budget requests are
rejected immediately.

**Model routing — NOT `sim-fast`.** `sim-fast` already serves PIANO background
cognition; past contention measurably increased PIANO module drops (see
Concurrency & context sizing). Routing to `sim-smart` avoids that regression
but is a new, not-yet-measured source of contention on `sim-smart`'s pool
(same pool every agent decision uses) — see [specs/12-ops.md](12-ops.md)
"Optional Phase 8" for the recommended A/B contention protocol (not yet run).

**Schema-locked output.** `_god_compiler_prompt` lists every
`GOD_MODIFIER_RANGES` key and bound and the three `_GOD_PRIMITIVE_KINDS`
shapes inline, with two worked few-shot examples. The model's raw response
must parse as `{"kind": "story_event", "payload": {...}}`
(`_god_compiler_parse`); the `payload` runs through `_validate_god_story_event`
(same validator as hand-authored story_event commands —
[02-engine-core.md](02-engine-core.md)) — unknown modifier key, out-of-range
value, or unknown resource/structure/agent id is rejected with the same error
as a malformed hand-authored command, never silently clamped or dropped.

**Dual gate.** `GOD_COMPILER_ENABLED` (env `SIM_GOD_COMPILER`, import-time,
alongside `GOD_MODE_ENABLED`) is a second flag — `god_compile_prose` and
`/control/god/compile` both require `GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED`.
Ships off by default; see [specs/12-ops.md](12-ops.md).

**No god token.** `god_compile_prose(prose)` has no `SIM_GOD_TOKEN` parameter
— the token gate lives in server.py's route handler, checked before
`engine.god_compile_prose` is called, like every other `/control/god/*` route.

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

**`normalize_decision` council-branch responsibilities (server.py).** Phase/
seating eligibility is authoritative only at apply time —
`normalize_decision()` enforces shape and coarse session existence only: a
seated attendee (`council_turn and council_seated`) choosing a non-council
action is rejected to the role fallback (`council_rejection_note: "not a
seated active council turn"`); a council action with no council session/phase
(`not council or not phase`) is rejected (`"no active council session"`).
Per-action payload shape is still fully checked (`council_speak` requires
non-empty `message`; `council_vote` requires valid `vote`/succession
`candidate`; `council_propose` requires valid `rule`/`blueprint`/idea payload
per `kind`). Per-turn/per-phase eligibility (ballot open, discussion ended,
etc.) is **not** re-checked here — `council_turn`/`phase` were snapshotted
before the LLM call and can go stale; `apply_decision()`'s
`_daily_council_actor()` (`mixin_council_growth.py:399`) is the live authority.
See [07-actions.md](07-actions.md) for the full phase/seating-authority split.

Every think payload, including non-council turns, receives at most
`COUNCIL_DIGEST_PROMPT_ENTRIES = 2` newest compact entries from
`civilization["councilDigests"]`, rendered as one short `Recent council:` line
in the same bounded context area as Chronicle history. The full
`council_transcript` audit table is never prompt material. This keeps council
continuity available to every agent, including agents who did not attend or
arrived later, while bounding context growth.

**`behavior_nudge` composition** (`_build_think_payload`, `mixin_think_job.py:35-878`):
candidate nudges are collected as `(priority, text)` pairs via a
local `note(prio, text)` helper, then capped to `MAX_BEHAVIOR_NUDGES = 3`
(`constants.py:1142`) — all P0 nudges are kept, then remaining slots fill from
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

**Per-kind rejection-nudge cooldown** (`_should_renudge`, `mixin_decisions.py:1273`):
P2 rejection-recovery notes (gather, craft,
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

**Persona-at-top-of-user-message rationale** (server.py:1832-1837): the
per-agent persona line is prepended to the *user* message, not the system
prompt, because Ollama (llama.cpp-based, same as LM Studio) reuses KV cache
by longest common prefix per slot — per-agent text in the system message forced
a full ~5k-token reprocess on every agent rotation; a byte-identical system
prompt across agents is a shared cached prefix.

### KV-cache prefix stability (Phase 2, TASKS_PENDING #2a)

The system message is byte-identical across every routine decision turn, for
every agent, all session long, so Ollama's longest-common-prefix KV-cache reuse
always fires (prefix-cache posture unchanged from LM Studio — Ollama is
llama.cpp-based and reuses prompt prefixes per slot the same way; also why
the persona line lives at the top of the *user* message above).

- **Audit result:** every per-agent/per-tick/per-request value (name, role,
  hunger, resources, nearby agents, timestamps, districts, etc.) is rendered
  by `build_user_prompt`/`USER_PROMPT_TEMPLATE` into the *user* message only.
  `build_decision_payload` (server.py:1779) never concatenates request data
  into `system_content`; `system_content` is always one of the four static
  module-level string constants (`SYSTEM_PROMPT`, `SYSTEM_PROMPT_SLIM`,
  `INVENTION_SYSTEM_PROMPT`, `SPRITE_UPGRADE_SYSTEM_PROMPT`).
- **The one SYSTEM_PROMPT reassignment** (`simulation/prompts.py:415`, gated on
  `TECH_TREE_ENABLED`) rewrites `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM` once via
  `.replace()` to document the optional blueprint `"tier"` field.
  `TECH_TREE_ENABLED` is a hardcoded module constant in `constants.py` (never
  flipped by a route or control endpoint), and this code runs at module
  import time, before the Flask app serves any request — so the rewrite
  cannot fire mid-session; whatever `SYSTEM_PROMPT` is after import is what
  every routine turn for the rest of the process sees.
- **Slim-retry exception (by design):** context-overflow retry (`slim=True`)
  swaps `SYSTEM_PROMPT` for `SYSTEM_PROMPT_SLIM` — a different, shorter
  prefix — forfeiting KV-cache reuse for that one call. Expected, rare (only
  after context-overflow on the primary call); not tracked by the mismatch
  guard below.
- **Startup proof:** on boot (and again if the `TECH_TREE_ENABLED` rewrite
  fires), the server prints `[server] system prompt sha256=<first 12 hex
  chars>` — a soak's stdout should show the hash once (or twice if rewritten,
  both before any traffic) and never again.
- **Mid-session mismatch guard:** `_check_system_prompt_stability()` (server.py,
  above `build_decision_payload`) runs only on primary (non-slim) routine-turn
  dispatch, only when `SYSTEM_PROMPT_AT_LOAD_TIME` is False — an omitted
  system message is not a "changed prefix". Fast path: `is` identity check
  against the previous routine turn's system string; on mismatch, value/hash
  comparison. If content changed, one `[server] WARNING: system prompt changed
  mid-session (cache invalidated) old_sha256=... new_sha256=...` line
  (log-once, never raises).

### Load-time rulebook (`SYSTEM_PROMPT_AT_LOAD_TIME`, default False, dark) {#load-time-rulebook}

Phase 6 of `docs/archive/plan-ollama-migration.md` (TASKS_PENDING item 2b —
LM Studio could never set a default system prompt at load time; Ollama's
Modelfile `SYSTEM` directive is the missing mechanism). Ships **dark** (flag
False) — machinery in place, inactive pending A/B soak.

- **The flag** (`server.py`, near `MODEL_SMART`/`MODEL_FAST`): when True, the
  primary (non-slim) dispatch of every routine/high-stakes decision turn that
  is not `sprite_design_only` or `invention_only` omits the system message
  from `messages` and routes to `MODEL_SMART_SYS` (`"sim-smart-sys"`) instead
  of `MODEL_SMART`. Slim-retry (`slim=True`), `invention_only`, and
  `sprite_design_only` turns are **unaffected**: they always send their own
  system message and stay on `MODEL_SMART` — their prompts differ from the
  baked `SYSTEM_PROMPT`, and Ollama's semantics (request-time system message
  *replaces*, never concatenates with, Modelfile `SYSTEM` — see
  `ollama_config.md` "Modelfile SYSTEM semantics") make them correct either way.
- **`sim-smart-sys`**: Modelfile-generated Ollama model, separate from
  `sim-smart` (same base GGUF/`num_ctx`/sampling as `ollama/Modelfile.smart`,
  plus `SYSTEM """..."""`). Generated by `uv run python scripts/ollama_setup.py
  --with-system`, which imports `simulation/prompts.py`'s `SYSTEM_PROMPT` and
  writes `ollama/Modelfile.smart.system` (generated, DO-NOT-EDIT, regenerate
  after any `SYSTEM_PROMPT` change) before `ollama create sim-smart-sys -f
  ollama/Modelfile.smart.system`. Creating/updating `sim-smart-sys` never
  touches live `sim-smart`/`sim-fast` (verified 2026-07-24: `/api/ps` showed
  `size_vram`/`expires_at` unchanged across `--with-system`).
- **Gate before flipping** (TASKS_PENDING item 2b bar): A/B soak comparing
  decision fallback rate (`bad_response` + `role_fallback`) and action
  distribution against a flag-off session. Tripwire: "model forgets a distant/
  baked system prompt under a long user message" — never tested here (LM
  Studio could not reach this experiment). Flip procedure: `ollama_config.md`
  "Load-time rulebook (dark)".
- **Payoff if gate passes:** ~3k tokens off every routine decision turn,
  `context_overflow` class largely disappears, per-turn prompt processing
  drops by roughly half.

Measured prompt size: ~3,100-3,400 prompt tokens per routine decision call
(docs/REFERENCE.md:40); invention-only prompts run larger due to the
function-block schema and sprite few-shot example (worst case ~6,163 tokens
measured, per the `HIGH_STAKES_MAX_TOKENS` comment, server.py:223-232).

**Contracts prompt growth (D9, F3.3, measured 2026-08-09, D9 trim):** pre-trim
F3.3 left rules C1/C2, the `contract` JSON field/schema, and one
`offer_contract` worked example in static `SYSTEM_PROMPT` (**+286 tokens**
flag-off vs pre-F3.3 baseline, `chars/4`). D9 trim removes that block from
`SYSTEM_PROMPT` and injects `CONTRACTS_SYSTEM_ADDENDUM` via
`append_contracts_addendum()` only when `CONTRACTS_ENABLED` is on
(`build_decision_payload`, `server.py`). **Flag-off system addendum: ~0**
(measured delta vs trimmed static prompt: **0 tokens**). **Flag-on system
addendum: ~136 tokens** (546 chars, terse C1/C2 + schema, no worked example).
With `CONTRACTS_ENABLED` on, a filled `contracts_line` at the
`MAX_CONTRACTS_PROMPT` cap (6 entries) still adds **~127 tokens** to the user
message vs flag-off on the same snapshot (~125 tokens for the line body alone).
Representative full routine payload (`build_decision_payload`, sample engine
snapshot): flag-off **~7,010** vs flag-on **~7,137** estimated when six open
contracts are shown — dominated by the user `contracts_line`, not static
rulebook cost. Re-measure after any further prompt edits.

`MEMORY_PROMPT_CHAR_BUDGET = 900` (`simulation/_server/prompt_format.py`,
raised from 600 — see [Wiki-style compounding memory](#wiki-memory) below)
caps the composed "Recent memory:" line; `compose_memory()`
(`simulation/_server/prompt_format.py`) merges the client's compacted memory
slice with up to 4 salient entries retrieved from the in-process
hashing-trick vector store (128-dim, `MEMORY_DIM`), dropping oldest lines
first and hard-truncating if still over budget. `compose_memory` reads the
live `memory_store` singleton via a module-attribute injected by server.py
right after construction (`_prompt_format.memory_store = memory_store`) —
`server.py` imports `compose_memory` FROM `_server/prompt_format.py`, so the
reverse import would be circular; see that module's docstring.

### MemoryStore persistence (restart-stable, Phase 1 TASKS_PENDING #1)

`MemoryStore` (class: `simulation/_server/memory_store.py:112`; singleton:
server.py:288) is constructed against `MEMORY_STORE_PATH =
simulation/memory_store.json` (server.py:286) — restart-stable, not the
per-session log directory. Singleton construction stays in server.py because
`mirror_path` depends on `session_logger.dir` — see
[01-architecture.md](01-architecture.md). `agent["memory"]` tiers persist via
`state.db`, but the semantic-recall embedding index (`memory_store`)
previously lived only in `simulation/logs/<timestamp>/memory.json`, so every
restart started the index empty despite surviving agent-visible memory.

- **Load-on-init.** `MemoryStore.__init__` calls `_load_locked_startup()`,
  reads `MEMORY_STORE_PATH` if present, rebuilds via `import_entries()`
  (re-embedding with offline hashing-trick `embed_text()` — no LLM call).
  Absent file → empty (`_load_status = ("absent", 0)`). Corrupt/unparseable →
  empty, tagged `("corrupt", 0)`, never raises.
- **Startup observability.** One `[server]` line logs load status, entry count,
  path; `session_logger.log_benchmark("memory_store_loaded", entry_count)` once.
- **Per-session inspection mirror.** `mirror_path=os.path.join(
  session_logger.dir, "memory.json")`. Every `_persist()` (debounced by
  `MEMORY_PERSIST_EVERY`, flushed on `clean()`) writes stable path first,
  then best-effort mirrors (entries minus `vec`) to session log dir. Mirror
  write in its own `try/except OSError` — failure never affects stable store.
- **Reset semantics.** `/control/reset` → `SimEngine.reset()`
  (`mixin_snapshot.py:52`) calls `memory_store.clear()`, wiping in-memory
  entries AND flushing empty store to `MEMORY_STORE_PATH` (matching
  `_piano_module_cache` wipe-on-reset precedent).

### Wiki-style compounding memory (`WIKI_MEMORY`, default True) {#wiki-memory}

TASKS_PENDING item 3 / plan Phase 4. Goal: long-term memory that merges and
reconciles instead of FIFO-dropping (Karpathy LLM-Wiki pattern), without
adding any new LLM call site or timer — it upgrades what
`_run_memory_maintenance`'s existing round-robin call already does.
Default flipped to `True` on `main` after D2 soak (session `2026-08-09T19-47-41`);
flag-off remains a one-flag revert.

- **Structure.** `agent["memoryWiki"]` is a dict of three named sections —
  `relationships`, `goals`, `lessons` — each hard-capped at
  `WIKI_SECTION_CHAR_CAP = 300` chars (`constants.py`, next to `LONG_MEM_CAP`).
  Always present (`{}` initial shape, populated only when the flag is on) so
  persistence via `state.db` is free — same pattern as `moduleReports`. See
  [06-agents.md](06-agents.md#wiki-style-compounding-memory-wiki_memory-default-true)
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
  other cognition flags (`mixin_persistence.py`'s `_serialize_state`-adjacent state
  payload build).

## Decision handling

`extract_json_decision(text)` (server.py) fallback ladder: (1) strip markdown
fences, `json.loads` on whole text; (2) first balanced `{...}` via brace-depth;
(3) regex for bare `"action": "..."` (and best-effort `target`/`message`) when
JSON is truncated/malformed. Returns `None` if action regex fails.
`lm_message_text()` (server.py) reads Ollama's `message.content` — no
`reasoning_content` fallback (LM Studio quirk); with `think:false` on every
call, content should be clean JSON, but any `<think>...</think>`
block that leaks is stripped before the JSON parser.

`normalize_decision(decision, agent_data)` (`simulation/_server/decision_validation.py:928`,
imported into server.py's namespace — see [01-architecture.md](01-architecture.md)) — per-action
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
| `offer_contract` | `target` required (agent name or `"open"`); `contract` must pass `validate_contract()` — failures stamp `contract_rejection_note` |
| `accept_contract` | `target` required (contract id) — failures stamp `contract_rejection_note` |
| `move_to_district` | promotes `target_district` into `target` if target is empty (the engine only reads `target`) |
| `talk_to_nearby` | target/message both required, target must be in the nearby-agents list, nearby list non-empty |
| `divine_response` (when active Voice guidance is unacknowledged) | must be an object with `stance` in `follow`/`continue` and a non-empty `reason` string; the JSON schema now *requires* the field's presence (as a non-null object) for this request via `build_response_format(require_divine_response=True)`, but missing/invalid *values* are still **not** rejected as a hard fallback to `rest` — instead of an immediate ack, non-response is now capped: it increments a per-guidance skip counter and only force-acks after `GOD_VOICE_ACK_SKIP_CAP` consecutive synthetic turns — see Voice binding guidance above |
| every other action | passed through as-is (any `blueprint` key stripped unless the action is one of the blueprint-carrying ones) |

`role_fallback_action(role, agent_data)` (`simulation/_server/decision_validation.py:527`,
imported into server.py's namespace) priority
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
offline"` (non-timeout `RequestException` on every attempt including retries,
or a detected missing-model setup failure — see Routing), `"llm timeout"`
(`requests.exceptions.Timeout` only — distinct from offline so the engine can
apply orphan backpressure; never retried), `"compute_error"` (an Ollama
compute-error body), `"server_error"` (any uncaught exception). Anything else
that fails JSON decoding or schema extraction becomes a `role_fallback_action`
result tagged with error `"bad_response"` (or `"context_overflow"` if the slim
retry still failed) — the engine never sees a raw error for these, only a
normal-looking decision.

## Retries & degradation

All in `run_agent_decision()` (server.py), each a single retry (no loops).

**Per-turn call budget.** `LLM_CALLS_PER_TURN_MAX = 4` (server.py) bounds the
total number of `requests.post(OLLAMA_CHAT_URL, ...)` calls one
`run_agent_decision()` invocation may make. Every POST site in the
function — the initial call, the format-degrade retry (item 1 below), the
context-overflow slim retry (item 3), and the answer-quality decision retry
(below) — routes through a local `_post_ollama(body, timeout)` closure that
counts calls (`llm_calls_made`, local to the invocation, not shared/global)
and raises `LLMBudgetExhausted` once the budget is spent, rather than posting
a 5th request. `LLMBudgetExhausted` is **not** a `requests.exceptions.RequestException`
subclass, so it cannot be mistaken for a network failure; every catch site
returns `{"error": "llm budget exhausted", "action": "rest"}` — distinct from
`"llm offline"`/`"llm timeout"`. Worst case: initial + format-degrade +
context-overflow slim retry + one answer-quality retry = 4 calls.

**Orphan caveat (Phase 0 operational finding):** Ollama does not cancel
server-side generation when a client aborts/times out a `stream:false`
request — orphaned timed-out requests keep consuming a queue slot. No path
below retries on `requests.exceptions.RequestException` (including `Timeout`);
each returns immediately. Timeouts are tagged `"llm timeout"` (not `"llm
offline"`) so the engine can distinguish orphan pressure from a dead endpoint.

**Orphan timeout backpressure (engine):** `_think_job` increments
`_llm_orphan_timeouts` on `"llm timeout"`; `run_piano_module` HTTP timeouts
(`"piano_module_timeout"`) also call `_record_llm_orphan_timeout()` because
they occupy the same Ollama parallel budget. When the counter reaches
`LLM_ORPHAN_TIMEOUT_THRESHOLD` (3), the engine sets `llm_cooldown_until` for
`LLM_ORPHAN_COOLDOWN_S` (30s) and resets the counter — `_schedule_think`
already skips dispatch while `time.time() < llm_cooldown_until`, pausing new
decision calls so in-flight orphans can drain. A successful decision (no
error) clears both `llm_cooldown_until` and `_llm_orphan_timeouts`.
`compute_error` uses the same cooldown duration constant.

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
3. **context-overflow retry**: on `is_context_overflow_error` — Ollama HTTP 400
   `{"error": {"type": "exceed_context_size_error", "message": ...,
   "n_prompt_tokens":, "n_ctx":}}` (or fallback: message containing both
   "context" and "exceed") — rebuild with `slim=True` (`SYSTEM_PROMPT_SLIM`,
   no memory line, no recent conversations) and retry once; further failure →
   `bad_response_fallback` tagged `error="context_overflow"`. Replaces LM
   Studio's string-sniffed "Context size has been exceeded." — Ollama 0.32.3
   does **not** silently truncate, it errors (Phase 0 finding #5,
   ollama_config.md).

**Same-turn answer-quality retry (`DECISION_RETRY_ENABLED`, default on).**
Retries once, same turn, on *answer quality* — unparseable JSON or
`normalize_decision()` raw result carrying `_fallback`. At most **one** retry
per turn across both triggers (shared `decision_retries` local):

- **Trigger 1 — unparseable JSON.** `extract_json_decision()` returns `None`.
  Truncation-specific feedback when `done_reason == "length"` (Phase 0:
  dominant cause) — `"your previous reply was cut off before it finished ...;
  reply with a smaller, more compact JSON object"` (plus sprite-grid hint on
  `sprite_design_only` turns) — otherwise generic parse-failure message.
- **Trigger 2 — `_fallback` on raw `normalize_decision()` result.** Feedback
  is the concrete rejection reason via `_rejection_feedback_text()` on
  whichever `*_rejection_note` key is present (`_REJECTION_NOTE_KEYS`); generic
  "valid JSON but rejected during validation" when no note (e.g. invalid
  `talk_to_nearby`).

`retry_feedback` threads through `build_decision_payload()` →
`build_user_prompt()` → `build_sprite_upgrade_prompt()` /
`build_invention_prompt()`. On a **council turn** prefixed onto the user
message as `"RETRY (previous reply rejected): {feedback}\n\n" + user_content`
inside `build_decision_payload()`; otherwise prepended to `behavior_nudge` in
`build_user_prompt()` (and special-turn prompts' feedback line). Retry via
`_decision_retry(feedback_text, slim_used)` through `_post_ollama`, reusing
`slim` state. Does **not** catch `LLMBudgetExhausted` /
`requests.exceptions.RequestException` — network failures never retry. New
`llm.jsonl` field **`decision_retries`**: `0` common path, `1` when retry fired.

**Terminal candidate-choice step (`FALLBACK_AI_CHOICE_ENABLED`, default on).**
Reached only once the answer-quality retry above is exhausted with no
usable decision, from **both** of `run_agent_decision`'s terminal
answer-quality fallback sites: `bad_response_fallback` (unparseable/
non-recoverable reply) and the post-retry path where `normalize_decision()`
still returns a `_fallback`-stamped decision. Never reached for a network
failure — every `llm offline`/`llm timeout`/`compute_error`/
`model_not_found`/`llm budget exhausted` path returns directly from its own
call site before ever reaching a fallback call site.

Instead of always silently taking the highest-priority
`role_fallback_action()` ladder entry, `role_fallback_candidates(role,
agent_data, limit=3)` collects up to 3 candidates in the ladder's existing
priority order. Both `_role_fallback_action()` (first match wins) and
`role_fallback_candidates()` (accumulate up to `limit` matches) iterate the
same ordered list of branch closures, `_role_fallback_candidate_checks()`,
so the two cannot silently drift; duplicate `(action, target)` candidates
are trimmed. When `council_turn` and `council_seated` are both set,
`role_fallback_candidates()` stops after the first match (the council
ladder entry) so the terminal AI-choice step cannot swap a council beat for
village work. With **≥2** candidates and budget remaining
(`llm_calls_made < LLM_CALLS_PER_TURN_MAX`), one minimal A/B/C-style prompt
(not the full decision schema, routed to `MODEL_FAST`, `max_tokens=5`,
`FALLBACK_AI_CHOICE_TIMEOUT_S = 10`, never retried) asks the model to pick a
lettered option among the candidates; a non-matching or failed/timed-out
reply falls back to the highest-priority candidate rather than retrying.
With fewer than 2 candidates, or no budget left to ask, the
highest-priority candidate is used directly with no extra call. Where the
rejection path came from a `normalize_decision()` fallback (as opposed to
`bad_response_fallback`), the original `*_rejection_note` and `reasoning`
are carried onto the replacement decision so diagnostics survive the swap.

New `llm.jsonl` fields, **absent on ordinary turns** (present only when this
step actually fires): `fallback_triggered` (`True`), `fallback_candidate_count`,
`fallback_selection_method` (`"single_candidate"` — fewer than 2 candidates;
`"ai_choice"` — the A/B/C call returned a matching letter; `"priority_default"`
— no call was made, or it failed/timed out/returned no match),
`fallback_candidates` (the candidate list's `action`/`target` pairs), and
`fallback_ai_latency_ms` (present only when the choice call was actually
attempted).

**`_fallback` sentinel.** `role_fallback_action()` wraps `_role_fallback_action()`
and stamps `decision["_fallback"] = True` on every return path — one wrapper
covers all branches. Inert for `apply_decision()` but greppable in `llm.jsonl`;
key for same-turn retry (trigger 2) and terminal candidate-choice step.

## Decision audit correlation id (`DECISION_AUDIT_ENABLED`)

When `DECISION_AUDIT_ENABLED` is on (default), each successful
`run_agent_decision()` turn that reaches the final `decision` dict — immediately
**after** `DECISION_SCHEMA` validation/normalization and the
`synthesize_divine_response(score_belief_pitch_decision(...))` pipeline, and
**immediately before** the terminal `log_lm(...)` call that writes the
`llm.jsonl` record (`server.py:2593-2594`) — receives a freshly minted
per-decision correlation id.

**Mint site.** One id per logged decision, stamped in `run_agent_decision`'s
success path only (the same call site that passes `decision=decision` into
`log_lm`). Error-only returns (`{"error": …, "action": "rest"}`) and paths
that never produce a normalized decision dict for logging do not mint an id.
This mirrors the established internal-field pattern used by `_fallback`
(`simulation/_server/decision_validation.py:550-566`): added to the Python
`decision` dict **after** schema validation, never part of
`DECISION_SCHEMA`/`SYSTEM_PROMPT`, never sent to or requested from the model.

**Key name.** `_decision_id` — underscore-prefixed internal field on the
`decision` dict (alongside `_fallback`). Value: a unique string (UUID4 hex,
implementation choice). When the flag is off, `run_agent_decision` does not
stamp `_decision_id` at all.

**Threading to `activity.jsonl`.** The engine's `apply_decision()` reads
`decision.get("_decision_id")` at its single unconditional tail call
`self._push_activity(summary)` (`simulation/sim_engine/mixin_decisions.py:1257`)
and passes it through `_push_activity(..., decision_id=…)` →
`log_activity(..., decision_id=…)`. Only decision-outcome activity lines carry
the id; every other `_push_activity` call site omits the parameter unchanged.
The logged `activity.jsonl` field name is top-level `decision_id` (no
underscore — it is a log-record field, not a schema decision property).

**Coexistence.** `_decision_id` / `decision_id` are optional: absent on records
from before the feature shipped, from flag-off runs, or from non-decision
activity lines. Readers treat a missing id as uncorrelated, not an error. The
id is minted per call and is not persisted in `state.db`.

**Slim-log sufficient.** The audit reader uses default slim `llm.jsonl` records
(`decision.reasoning` + `decision.action`); it never requires
`SIM_LLM_LOG_FULL=1`.

See [12-ops.md](12-ops.md#record-shapes) for both log-stream field shapes and
[04-http-api.md](04-http-api.md#decision-audit-route) for the reader route's
scoring semantics.

**Four server-side cognition flags.** `DECISION_RETRY_ENABLED`,
`FALLBACK_AI_CHOICE_ENABLED`, `FALLBACK_AI_CHOICE_TIMEOUT_S`, and
`LLM_CALLS_PER_TURN_MAX` are module-level constants in `server.py`, not
the `sim_engine` package — they are **not** part of the
[01-architecture.md](01-architecture.md#flag-index-complete--52-module-level-flags-sim_enginepy)
module-level flag index, which is specifically the `sim_engine` engine
flag list. `SPRITE_DESIGN_MAX_ATTEMPTS = 3` is the one related constant that
**is** engine-side (`constants.py:1075`, gates sprite-design-turn retirement —
see [05-world.md](05-world.md#structures) and
[07-actions.md](07-actions.md)).

## Civ-1 library lessons

When `LIBRARY_SCALING_ENABLED` is enabled, an agent in a district with a
working Library receives a `library_lessons` prompt line. It contains at most
three highest preserved skill records and two newest chronicle entries, with a
480-character cap; it is omitted otherwise.

## Routing

Model routing (`MODEL_SMART`/`MODEL_FAST`, `is_high_stakes_turn`/
`_base_high_stakes`/`resolve_high_stakes`/`model_for_decision`) lives in
`simulation/_server/model_routing.py`, imported into server.py's namespace —
see [01-architecture.md](01-architecture.md).

`model_for_decision(data)` (`_server/model_routing.py:153`) = `MODEL_SMART`
unconditionally — every decision turn, routine or high-stakes, routes to
`sim-smart`. `is_high_stakes_turn(data)` is still computed and still fully
controls timeout/max_tokens/thinking-sampling selection (see the Ollama call
settings table); it no longer affects which model id is used. No fallback id
— see Retries for what happens if the routed id isn't created in Ollama.
`_base_high_stakes(data)` (`_server/model_routing.py:94`, unbudgeted):
`sprite_design_only`, `invention_only`, `role=="elder"`, or
`invention_status` starting with `"REQUIRED"`.
`HIGH_STAKES_ENABLED_REASONS = {"emergency", "election", "treaty_vote"}`
(`_server/model_routing.py:66`) — extra reasons routing to
`THINKING_TIMEOUT_S`/thinking-on, gated by rolling-window limiter:
`EXTRA_THINKING_PER_WINDOW=4` per `EXTRA_THINKING_WINDOW_S=60` seconds
(`_server/model_routing.py:74-76`), thread-safe via `_extra_thinking_lock`.
Excluded: `elder_blueprint_review` (redundant — already high-stakes via elder
role) and `repeated_rejections` (too frequent, would dominate budget).

`resolve_high_stakes(data)` (`_server/model_routing.py:130`) resolves
`is_high_stakes_turn` once per request and stamps `data["_high_stakes_resolved"]`
— the reasons budget is stateful and `is_high_stakes_turn()` is called from
multiple sites per request (payload build, timeout choice, context-overflow
retry) that must agree without re-consuming the budget.

## Concurrency & context sizing

`MAX_CONCURRENT_LLM = 3` (`constants.py:1300`, `ThreadPoolExecutor` on
`self._executor`) — matches `OLLAMA_NUM_PARALLEL=3` (ollama_config.md).
`LLM_MIN_GAP_MS = 250`. `LLM_ORPHAN_TIMEOUT_THRESHOLD = 3` and
`LLM_ORPHAN_COOLDOWN_S = 30.0` gate new decision dispatches after repeated
client-side timeouts (see Retries). Context formula under Ollama is **per-model**,
not shared token-budget-divided-by-slots like LM Studio: each model has its
own fixed `num_ctx` in its Modelfile (20480 / 4096 — `ollama/Modelfile.smart`,
`ollama/Modelfile.fast`); `OLLAMA_NUM_PARALLEL` governs concurrent requests
sharing that per-model context budget. Decision prompts (~3,100-3,400 routine,
up to ~6,163 invention-only) must stay under `sim-smart`'s per-slot share at
`OLLAMA_NUM_PARALLEL=3` against `num_ctx=20480`; `exceed_context_size_error`
(see Retries) is the enforced backstop.

### Chronicle saga LLM (`CHRONICLE_SAGA_ENABLED`)

One `lm_complete` call per sim day at the day boundary
(`frameTick % DAY_FRAMES == 0`, gated by `CHRONICLE_SAGA_ENABLED` — trigger
details in [02](02-engine-core.md#chronicle-saga-chronicle_saga_enabled)). This
is a **background** call site: always `MODEL_FAST`/`sim-fast` via
`self.d["run_chronicle_saga"](saga_context)` (server wrapper around
`lm_complete` — `server.py:1312-1316`).

**Lock discipline (contrast with `_spawn_newborn`).** All saga inputs are
snapshotted under `self.lock` (chronicle-ring window, `births`/`deaths`
counters, and a bounded dialogue excerpt returned by
`self.d["read_conversation_window"](start_frame, end_frame)` — [12](12-ops.md)).
The network call runs **fully outside** the lock, then the lock is re-acquired
only to append into `civilization["saga"]`. Same snapshot → release → dispatch
→ reacquire → write shape as `_build_think_payload` /
`run_agent_decision` / `apply_decision` (`mixin_think_job.py:35,1417`,
`server.py:2006`) — **not** the single synchronous in-lock `lm_complete` in
`_spawn_newborn` (`mixin_lifecycle.py:1186-1253`).

**Pool.** Dispatched onto `self.piano_workers` (`PIANO_CONCURRENT_LLM = 2`,
`constants.py:1358`) — shared capacity with PIANO module calls, never
`self._executor` / `MAX_CONCURRENT_LLM`.

**Prompt composition.** The user prompt combines: (1) chronicle entries from
the completed day's window in `civilization["chronicle"]`; (2) a **bounded**
excerpt of the day's `conversation.jsonl` dialogue (via the injected read
function — capped at `SAGA_DIALOGUE_EXCERPT_CAP = 10` lines in
`constants.py`, a small multiple of `CHRONICLE_PROMPT_ENTRIES = 3`, not the
full day's transcript). Prompt excerpts normalize CRLF/CR to LF, collapse
each rendered excerpt to one line, cap each normalized message at
`SAGA_PROMPT_EXCERPT_CHAR_CAP = 300` characters, and stop at 10 rendered lines
before prompt construction; (3) civilization counters and daily-council verdict
context when present. Saga text is **never** folded into `_chronicle_prompt_line()` or any
agent think payload ([09](09-systems-society.md#saga-chronicle_saga_enabled)).

**Always-fire / degradation.** The call fires every day boundary even when
both chronicle and dialogue windows are empty (explicit quiet-day context in
the user prompt via `quiet_day: true` on `saga_context`). On `lm_complete`
failure/timeout/empty response, the worker writes the deterministic fallback
`SAGA_FALLBACK_TEXT = "A quiet day passed in the village; little was recorded."`
(`constants.py`) to `civilization["saga"]` instead of blocking the tick loop.
Server-side dispatch: `run_chronicle_saga(saga_context)` in `server.py`, injected
as `"run_chronicle_saga"` in `_ENGINE_DEPS`. Distinct `llm.jsonl` logging via
`session_logger.log_lm_exchange` with `"module": "chronicle_saga"` ([12](12-ops.md)).

`PIANO_MODULES` (`constants.py:483`, default `True` since Sid-parity Phase 1) —
Perception/Social/Desire/Reflection module fan-out is the default cognition
path. When `THEORY_OF_MIND_ENABLED` (default `False`, Emergence Breakthroughs
F2) is on, `theory_of_mind` joins the same staggered fan-out by **swapping
into the social slot every 4th module-tick** (`tick % 4 == 0` and
`tick % 2 == 0`) — no extra call per turn, same `PIANO_CONCURRENT_LLM`
budget. Both decision-path `_piano_to_run` and gated always-on
`_pulse_piano_modules` enumerate exactly four module slots via
`_piano_social_slot_module` (never a fifth name). Module calls run on
`self.piano_workers` (`PIANO_CONCURRENT_LLM = 2`), bounded independently of
`MAX_CONCURRENT_LLM` — `_run_piano_modules` submits to `piano_workers`, never
`self._executor`. Decision-path fan-out and the gated always-on pulse share
`_piano_refresh_inflight` (keyed by
`(agent_name, module)`): before each submit wave `_run_piano_modules` checks
`_piano_free_slots()` and never queues more than
`PIANO_CONCURRENT_LLM - len(_piano_refresh_inflight)`. Within one turn it
submits in waves (wait for a slot, dispatch next due module). When pool is
saturated at think snapshot time (`free slots == 0`), `_think_job` passes
`force_cache_only=True`. Modules skipped for saturation increment
`piano_module_drops` alongside timeouts/failures. Every module call routes to
`MODEL_FAST` with `PIANO_MODULE_TIMEOUT_S = 15s` (`run_piano_module`); timeout
is dropped, never retried, logged `"error": "piano_module_timeout"`. Reports
cached per `(agent, module)` with `PIANO_MODULE_CACHE_TTL = 2` module-tick TTL
(perception+desire every module-tick, social every 2nd, reflection every 3rd);
fresh same-tick reports render as bare `module: text`, off-tick fills as
`module (N turns ago): text` so the Cognitive Controller can discount stale
advice.

**Module prompt contract (Phase 1).** `run_piano_module` leads with
`You ARE {agent_name}. Context: {context}`. Each `MODULE_PROMPTS` requires
references only to agents, resources, and numbers present in context and
prohibits inventing names, quantities, or statistics. Social module must never
suggest coordinating with, messaging, or requesting from that same agent.
Advisory input to the Cognitive Controller only. `PIANO_MODULE_MAX_TOKENS = 90`
(raised from 60 after module-quality screen: guarded 90-token variant reduced
grounded-wrong modal count 1→0 with no category regression).

### Theory of Mind (`THEORY_OF_MIND_ENABLED`, default False)

Env override: `SIM_THEORY_OF_MIND=1` (or `true`/`yes`/`on`) forces the flag on
at import time without editing `constants.py` — same truthy pattern as
`SIM_DETERMINISM_PINNING`. Unset or `0`/`false` leaves default off. Used by
`scripts/tom_contention_soak.py` for matched flag-off vs flag-on contention
soaks before flipping the code default.

Emergence Breakthroughs F2. When on, agents maintain a bounded
`agent["peerModel"][peerIdStr] = {wants, good_at, owes_me, trust, frame}`
(updated by the `theory_of_mind` PIANO module on successful parse only).
Caps: `PEER_MODEL_MAX_PEERS = 8` (LRU eviction by `frame`),
`PEER_MODEL_FIELD_CHAR_CAP = 48` per string field. Module output uses a
pipe-delimited line (`PEER=… | wants=… | good_at=… | owes=… | trust=… |
expect=…`); timeouts/drops leave prior `peerModel` intact.

**Prompt surface:** one `[think: …]` suffix per *nearby* peer only, folded
into `format_nearby_agents()` — never the full table.

**Benchmark:** `peer_prediction_accuracy` — when a parsed module report
includes `expect=<action>`, the engine records a pending prediction for that
peer; the next `apply_decision()` on the peer scores hit/miss against
`lastAction`. Sampled in `_sample_benchmarks()` when the flag is on.

**Default-on gate:** flipping default to `True` requires a soak comparison
(`scripts/soak_monitor.py`) showing `piano_module_drops` / `module_refresh_failures`
not materially worse than a flag-off soak of the same length (see plan).

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
perception/desire, social x2, reflection x3 cadence as priority weights.
When `THEORY_OF_MIND_ENABLED`, the social priority slot is
`theory_of_mind` on the same swap ticks as `_piano_to_run` (using
`agent["moduleTick"] + 1`) — never both social and theory_of_mind in one
pulse due-list. It
submits at most `MODULE_PULSE_MAX_BATCH = 2` and the currently free slots from
`_piano_free_slots()` (same `_piano_refresh_inflight` budget as decision-path
fan-out) to `piano_workers`; only these always-on refresh
calls pass `MODULE_REFRESH_TIMEOUT_S = 60` to `run_piano_module`. The legacy
per-decision fan-out retains its 15-second HTTP / 18-second future-wait
behavior. Completions re-acquire the engine lock and write `{tick, text,
wall_ts}` into both the hot cache and the persistent `agent["moduleReports"]`
mirror. Failures preserve the old note and leave the agent dirty for the next
pulse. The Attempt-2 freshness target was median note age <=120 seconds, with
decision p50 as the latency tiebreak after freshness and refresh-failure rate
are acceptable.

**Phase B gate outcome: FAILED, machinery stays dark.** Attempt 2 batch 2
missed latency (+17.73%) and freshness gates. Batch 1 retune passed latency
(+13.47%, within +15%) but failed freshness (median note age 619.0s, all 39
pulses dispatching exactly 1 refresh, zero empty pulses) — one refresh per
45-second pulse cannot keep the full note set fresh. No `MODULE_PULSE_MAX_BATCH`
value satisfies both gates on single-GPU reference hardware. Per two-soak stop
rule, `ALWAYS_ON_MODULES` rolled back to `False`, `MODULE_PULSE_MAX_BATCH`
restored to 2; full numbers in `ollama_config.md`. Scheduler code remains,
exercised by deterministic smoke, dark until second GPU or faster/smaller fast
model.

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
`to_run`, `_run_piano_modules` builds one shared `last_reports=` suffix from
every cached report within `PIANO_CROSS_CONTEXT_TTL = 6` module-ticks (more
tolerant than `PIANO_MODULE_CACHE_TTL` above) and appends it to every module's
context this turn, e.g. `last_reports=desire(1 ago): stockpile wood | social(2
ago): ask Sage about the blueprint`. `MODULE_PROMPTS` each carry one added
clause to build on or correct prior reports. No extra LLM call — suffix rides
on existing module calls. Reports capped at 200 chars (server.py), suffix adds
at most ~4 × 60 ≈ 240 tokens per call.

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

`META_SYSTEM` (`constants.py:488`, default `True` since Sid-parity Phase 3) —
autobiography/persona meta update, still bounded by `MAX_CONCURRENT_LLM`
(runs inline on the decision path, not on `piano_workers`). Authored beliefs
and adoption events give the rotating autobiography update material to
summarize.

### Scale headroom (Phase 6) — concurrency unchanged

Sid-parity Phase 6 raises roster ceiling to `MAX_ROSTER_SIZE = 20` (see
specs/02-engine-core.md) but does **not** raise `MAX_CONCURRENT_LLM` or
`PIANO_CONCURRENT_LLM` — concurrency budget was sized for loaded Ollama config
(`OLLAMA_NUM_PARALLEL=3`, `OLLAMA_MAX_LOADED_MODELS=2`) and reopening it
needs its own measured soak. `MAX_CONCURRENT_LLM` + `LLM_MIN_GAP_MS` are the
throughput cap regardless of roster size; the scaling risk at bigger roster was
*fairness*, not throughput — more agents contending for fixed slots under old
fixed-roster-order dispatch (see "Dispatch fairness (Phase 6)" in
specs/02-engine-core.md). That ordering fix keeps average think latency
reasonable at roster 20 without touching concurrency constants;
`thinkInterval` staggering unchanged (`360 + i*60`, `240` for elder). Roster
20 at default cadence dispatches more decision calls/minute in aggregate than
roster 8, but no single agent is starved, and the worker-pool cap prevents
aggregate demand from exceeding what the loaded Ollama config serves.

## Agent interview (operator Q&A, out-of-world debug surface) {#agent-interview-operator-qa-out-of-world-debug-surface}

`POST /agent/interview` (route/response shape:
[04-http-api.md](04-http-api.md#agent-interview-route)) is a **third, distinct
LLM call site** alongside agent decisions (`self._executor`,
`MAX_CONCURRENT_LLM = 3`) and PIANO modules (`self.piano_workers`,
`PIANO_CONCURRENT_LLM = 2`) — see "Concurrency & context sizing" above. It is
operator-triggered (a human clicking the Divine Console's interview button, or
a direct HTTP call — [04-http-api.md](04-http-api.md#agent-interview-route)),
off the tick loop, never gated by `is_high_stakes_turn`, and never counted as
a "decision" or "module" turn in any benchmark — the same non-cognition-path
status the Optional Phase 8 free-prose compiler has (see "Sovereign God mode
(Optional Phase 8)" above), though the interview call is not itself a God-mode
command (it is gated only on its own flag, `AGENT_INTERVIEW_ENABLED` — see
[01-architecture.md](01-architecture.md), not `GOD_MODE_ENABLED`).

**Zero mutation, not an intervention.** The handler reads world state and
returns an answer to the operator; it writes nothing to `agent["memory"]`,
`agent["memoryWiki"]`, `agent["relationships"]`, `agent["beliefs"]`,
`civilization`, or any other snapshot field. It sets no `intervened` flag and
writes no `divine.jsonl` record — it is not one of the five `/control/god/*`
routes' applyable commands and never touches `godState` (see
[01-architecture.md](01-architecture.md#control-plane-data-flow-sovereign-god-mode)).

**Model and concurrency pool.** Every interview call routes to `MODEL_SMART`
(`sim-smart`) — the same model agent decisions use, chosen because a human
operator reads the answer directly and coherence matters more here than for a
background module call (`sim-fast` already serves PIANO; routing interviews
there would risk the same contention the compiler avoids by not using
`sim-fast` — see "Model routing — NOT `sim-fast`" above). Interview calls run
on a **new, independently bounded** pool/semaphore,
**`INTERVIEW_CONCURRENT_LLM = 1`**, in `simulation/sim_engine/constants.py`
alongside `MAX_CONCURRENT_LLM` and `PIANO_CONCURRENT_LLM` — a third,
dedicated concurrency budget so an interview call can never queue against, or
be starved by, either existing pool. `1` (not a larger number) matches this
being a low-frequency, single-human-operator-triggered feature — there is no
structural reason for multiple interviews to be in flight simultaneously in
normal operation, unlike per-tick decision/module fan-out across the roster.
Raising the constant later, if a concrete need for concurrent interviews
emerges, is a one-line change.

Semaphore acquisition is itself bounded: `run_agent_interview()` waits at
most one second for the dedicated slot. If it is occupied, the request returns
`{ok: false, reason: "agent interview capacity unavailable; try again shortly"}`
without calling the model. A Flask worker therefore never waits indefinitely
behind another interview.

**Question cap.** Free-text operator questions are validated against
**`INTERVIEW_QUESTION_MAX_CHARS = 500`** (`simulation/sim_engine/constants.py`)
before the LLM call — a question over the cap is **rejected**, never silently
truncated, following the same cap-and-reject pattern
`GOD_COMPILER_PROSE_MAX_CHARS = 800` (`constants.py:797`) established for the
free-prose story compiler's `prose` field (see "Sovereign God mode (Optional
Phase 8)" above) — a distinct constant, not a reuse of that one, because an
interview question is a single question directed at one agent, not free
prose describing a world event. The cap is enforced server-side (client-side
`maxlength` on the Divine Console's textarea, if present, is UX only and never
load-bearing — [11-viewer.md](11-viewer.md#divine-console-sovereign-god-mode-phase-7)).
`500` is this spec's currently documented value; a Phase 2 implementer may
adjust it for a concrete reason (e.g. matching a chosen UI input's
`maxlength`), but must update this value and its reasoning here in the same
change that changes it.

**Prompt construction — reuse boundary with `idea-09-world-wiki`.** The
context-fetch step calls `_agent_snapshot_row(a)`
(`simulation/sim_engine/mixin_snapshot.py:113-134`) **server-side, in-process,
under `self.lock`**, for the requested agent's structured fields — the same
in-process-call pattern `idea-09-world-wiki`'s own wiki route uses to merge
`/districts.js` data (its own §2 Answer 3 precedent), never an HTTP
round-trip to another route. `_agent_snapshot_row()` is pre-existing repo
code (not authored by, or owned by, this feature) that already returns, per
agent: `id`, `name`, `role`, `color`, `x`/`y`, `currentZone`,
`currentDistrict`, a `waypoints` **count** (not the path itself), `resources`,
`hunger`, `health`, `incapacitated`, `message`, `isThinking`, `beliefs` (text,
via `_belief_text`) and `beliefIds`, `lastAction`, `assignedTask`, `age` and
`lifeStage` (when `LIFECYCLE_ENABLED`), `skills` and `personalityTraits`
(when `CULTURE_ENABLED`), `deceased`, `buried`, `relationships` (non-neutral
only), and `lastReasoning` (truncated to 160 chars). It does **not** include
`memoryWiki` sections, raw `agent["memory"]` tiers, or any other private
engine state — that is `_agent_snapshot_row()`'s existing, unmodified
contract (it already serves the `/state` agent snapshot with exactly this
field set; see [06-agents.md](06-agents.md)). Because the idea text promises
an answer "generated strictly from that agent's memory store, relationships,
and beliefs," and `_agent_snapshot_row()` deliberately omits the memory-store
half, the interview context assembly **additionally reads directly**:
`agent["memory"]` (the three-tier `working`/`shortTerm`/`longTerm` structure —
[06-agents.md](06-agents.md#memory-system-memory_enabled-default-true)) and
`agent["memoryWiki"]` (the `relationships`/`goals`/`lessons` sections —
[06-agents.md](06-agents.md#wiki-style-compounding-memory-wiki_memory-default-true)).
`agent["relationships"]` and `agent["beliefs"]` are already carried on the
`_agent_snapshot_row()` projection, so no second direct read of those two
fields is needed. This plan does not modify `_agent_snapshot_row()` itself,
add a field to it, or duplicate its entity-resolution logic — it is called
exactly as `idea-09-world-wiki` calls it, read-only, for one agent id
resolved from the request body.

**Clean-error degrade when memory is unavailable (non-default choice).** The
idea text's promised context — memory store, relationships, beliefs — is only
fully available when `MEMORY_ENABLED` is True (and, for the wiki-compounded
sections, `WIKI_MEMORY` also True); `relationships` and `beliefs` are
unconditional agent fields and always exist. `run_agent_interview()`
(`_server/agent_interview.py`) gates on both flags independently: when
`MEMORY_ENABLED` is off, or when `WIKI_MEMORY` is off, the route **refuses
with a clean error and makes no LLM call** — it must NOT silently degrade to
answering from relationships/beliefs alone (or with an empty memoryWiki
section). This is the non-default choice: the alternative (answer from
whatever remains) was considered and rejected, because a thinner, unflagged
answer would look identical to a full one to the operator reading it. See
[04-http-api.md](04-http-api.md#agent-interview-route) for the exact error
response shape.
