# SPEC 03 — Cognition (LLM Pipeline)

The LLM think pipeline: prompt construction, structured-output decoding,
decision validation/fallback, model routing, and retry/degradation behavior.

**Canonical for:** all LM Studio call settings (models, timeouts, sampling,
token budgets), `DECISION_SCHEMA`/structured-output mode, prompt template
sections, `normalize_decision`/`role_fallback_action` rules, model routing +
high-stakes policy, retry/degradation ladders, concurrency/context-sizing
constants. **See also:** [specs/01-architecture.md](01-architecture.md) (data
flow, flag index), [specs/02-engine-core.md](02-engine-core.md) (tick/think
scheduling, Sage emergency), [specs/04-http-api.md](04-http-api.md) (routes
that front this pipeline), [specs/07-actions.md](07-actions.md) (the action
catalog — not repeated here).

## LM Studio call settings

| Call type | System prompt | Model | max_tokens | temperature | timeout | sampling |
|---|---|---|---|---|---|---|
| Routine decision | `SYSTEM_PROMPT` (or `SYSTEM_PROMPT_SLIM` on retry) | `MODEL_FAST` | 512 | 0.4 | `DEFAULT_TIMEOUT_S`=30s | `NON_THINKING_SAMPLING` + `reasoning_effort:"none"` |
| High-stakes decision (elder / `invention_status` REQUIRED / rate-limited emergency,election,treaty_vote) | `SYSTEM_PROMPT`/slim | `MODEL_SMART` | 512 (1600 only if thinking re-enabled, currently dead code) | 0.4 | `THINKING_TIMEOUT_S`=75s | `THINKING_SAMPLING` if `THINKING_ENABLED_HIGH_STAKES`, else same as routine |
| Invention-only turn | `INVENTION_SYSTEM_PROMPT` | `MODEL_SMART` (sprite/invention always high-stakes) | `INVENTION_MAX_TOKENS`=1024 | `INVENTION_TEMPERATURE`=0.6 | 75s | as above |
| Sprite-design turn | `SPRITE_UPGRADE_SYSTEM_PROMPT` | `MODEL_SMART` | 768 | 0.3 | 75s | as above |
| Background `lm_complete` (memory summarizer, PIANO modules, meta system) | caller-supplied one-off prompt | `MODEL_FAST` always | caller-set (80/60/100/40 per call site) | caller-set (0.4-0.6) | 30s (hardcoded, not `DEFAULT_TIMEOUT_S`) | `NON_THINKING_SAMPLING` + `reasoning_effort:"none"` |

`MODEL_SMART` and `MODEL_FAST` both currently resolve to `"qwen/qwen3.5-9b"`
(server.py:49-50) — kept as two separate constants (not a single model
string) because `is_high_stakes_turn()` is a real predicate used for routing
*and* timeout/thinking selection, not just a model-id compare. Fallback: if
LM Studio rejects the routed model id, the server disables per-role routing
for the rest of the session and retries with `"local-model"` (see Retries).

`NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}`;
`THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}` (server.py:95-96) — Qwen
model-card-recommended pins, sent on every call so behavior doesn't drift with
LM Studio preset changes.

### Thinking-mode suppression (`THINKING_ENABLED_HIGH_STAKES`)

`DISABLE_THINKING_ROUTINE = True` (server.py:64): every routine turn sends
top-level `"reasoning_effort": "none"` — the only knob this LM Studio build
honors to suppress chain-of-thought (payload-format alternatives like
`chat_template_kwargs`/`/no_think` are ignored, a known LM Studio bug).

`THINKING_ENABLED_HIGH_STAKES = False` (server.py:89, current value) — high-
stakes turns do **not** get `reasoning_effort` suppressed, but since this flag
is False, `thinking_active` in `build_decision_payload` never goes True, so
high-stakes turns still use `NON_THINKING_SAMPLING` + `reasoning_effort:none`
in practice. **Consequence:** a 2026-07-14 live analysis of 48 high-stakes
samples (server.py:66-88) found reasoning content gave zero measurable
decision-quality benefit when it *was* enabled (Phase 2) — the model emitted
the identical JSON either way, just routed through `reasoning_content` — while
costing 33% concurrency (parallel 3→2 was needed for headroom). Reverted to
`False` + `MAX_CONCURRENT_LLM=3`. `HIGH_STAKES_MAX_TOKENS=1600` (server.py:126)
is therefore currently dead code, kept in case thinking is revisited.

## Structured output

`STRUCTURED_OUTPUT_MODE = "json_schema"` (server.py:743). `build_response_format()`
(server.py:861-872) returns `{"type": "json_schema", "json_schema": {"name":
"agent_decision", "schema": DECISION_SCHEMA}}`, or `{"type": "json_object"}` if
the mode were `"json_object"`, or `None` if `"off"` or auto-disabled.

`DECISION_SCHEMA` (server.py:780-839): `additionalProperties: False`;
`required: ["action", "reasoning"]`. Key properties: `action` (enum =
`DECISION_ACTIONS`, 35 entries — see specs/07-actions.md, not repeated here),
`target`/`target_district`/`message`/`new_role` (nullable strings),
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

**Auto-disable on rejection:** `_structured_output_enabled` (module-level,
server.py:842) flips to `False` for the rest of the session — and the retry
drops `response_format` from the payload — the first time LM Studio responds
with an HTTP 400 or an error body mentioning `response_format`/`json_schema`/
`grammar`/`schema` (`looks_like_response_format_error`, server.py:875-883).

## Prompt construction

`SYSTEM_PROMPT` (server.py:885-1111, ~20 numbered rule groups): talk-gating,
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
`prices_line`, `chronicle_line`, `path1_lines`, `level_line` — each renders
empty when its owning flag is off so prompts stay byte-identical across flag
states, per `build_user_prompt` server.py:2787-2904), build state
(structures/active project/progress), civilization state (directive,
invention status, commitment, idle agents, known resources/recipes, pending/
rejected blueprints/recipes/rules, reserved structure ids), social (recent
conversations, inbox, module reports), a `behavior_nudge` line, and finally
`available_actions`.

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
system prompt, because LM Studio reuses KV cache by longest common prefix per
slot — per-agent text inside the system message forced a full ~5k-token
reprocess on every agent rotation; keeping the system prompt byte-identical
across agents makes it a shared cached prefix instead.

Measured prompt size: ~3,100-3,400 prompt tokens per routine decision call
(docs/REFERENCE.md:40); invention-only prompts run larger due to the
function-block schema and sprite few-shot example (worst case ~6,163 tokens
measured, per the `HIGH_STAKES_MAX_TOKENS` comment, server.py:120-122).

`MEMORY_PROMPT_CHAR_BUDGET = 600` (server.py:1219) caps the composed "Recent
memory:" line; `compose_memory()` (server.py:1238-1271) merges the client's
compacted memory slice with up to 4 salient entries retrieved from the
in-process hashing-trick vector store (128-dim, `MEMORY_DIM`), dropping oldest
lines first and hard-truncating if still over budget.

## Decision handling

`extract_json_decision(text)` (server.py:2599-2649) fallback ladder: (1) strip
markdown code fences, try `json.loads` on the whole text; (2) scan for the
first balanced `{...}` block via brace-depth counting and parse that; (3) regex
for a bare `"action": "..."` (and best-effort `target`/`message`) to build a
minimal decision dict when the JSON is truncated/malformed. Returns `None` if
even the action regex fails. `lm_message_text()` (server.py:2528-2535) reads
`content` first, falling back to `reasoning_content` (reasoning models that
route their whole answer there).

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
directly to the engine as `{"error": ..., "action": "rest"}`): `"LM Studio
offline"` (request exception on every attempt including retries),
`"compute_error"` (LM Studio's own compute-error body), `"server_error"`
(any uncaught exception). Anything else that fails JSON decoding or schema
extraction becomes a `role_fallback_action` result tagged with error
`"bad_response"` (or `"context_overflow"` if the slim retry still failed) —
the engine never sees a raw error for these, only a normal-looking decision.

## Retries & degradation

All in `run_agent_decision()` (server.py:2978-3172), each a single retry (no
loops):

1. **response_format-rejected retry**: on `looks_like_response_format_error`,
   disable `_structured_output_enabled` session-wide, drop `response_format`,
   retry once.
2. **unknown-model-id retry**: on `looks_like_model_not_found_error`, disable
   `_model_routing_enabled` session-wide, set `payload["model"]="local-model"`,
   retry once. Both auto-degrades are one-way for the session (never re-enabled).
3. **context-overflow retry**: on `is_context_overflow_error` (LM Studio's
   "Context size has been exceeded." body), rebuild the payload with
   `slim=True` (`SYSTEM_PROMPT_SLIM`, no memory line, no recent conversations)
   and retry once; any further failure falls through to `bad_response_fallback`
   tagged `error="context_overflow"`.

## Civ-1 library lessons

When `LIBRARY_SCALING_ENABLED` is enabled, an agent in a district with a
working Library receives a `library_lessons` prompt line. It contains at most
three highest preserved skill records and two newest chronicle entries, with a
480-character cap; it is omitted otherwise.

## Routing

`model_for_decision(data)` = `MODEL_SMART` if `is_high_stakes_turn(data)` else
`MODEL_FAST` (server.py:244-245; `local-model` if routing disabled).
`_base_high_stakes(data)` (server.py:188-199, unbudgeted): `sprite_design_only`,
`invention_only`, `role=="elder"`, or `invention_status` starting with
`"REQUIRED"`. `HIGH_STAKES_ENABLED_REASONS = {"emergency", "election",
"treaty_vote"}` (server.py:160) — extra reasons that ALSO route to
`MODEL_SMART`/`THINKING_TIMEOUT_S`, gated by a rolling-window limiter:
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
engine's decision-think worker pool, `self._executor`). `LLM_MIN_GAP_MS = 250`
(minimum spacing between decision dispatches). Context formula (decision-only
budget): LM Studio's context length must be ≥ ~3,100-3,400 tokens ×
`MAX_CONCURRENT_LLM` parallel slots.

`PIANO_MODULES` (sim_engine.py, default `True` since Sid-parity Phase 1) — the
Perception/Social/Desire/Reflection module fan-out is the default cognition
path, not experimental. Module calls run on their own pool,
`self.piano_workers` (`PIANO_CONCURRENT_LLM = 2`), bounded independently of
`MAX_CONCURRENT_LLM` so a module backlog can never starve the decision path —
`_run_piano_modules` submits to `piano_workers` and waits on the futures, it
never dispatches into `self._executor`. Every module call routes to
`MODEL_FAST` with a hard, non-blocking `PIANO_MODULE_TIMEOUT_S = 15s` timeout
(server.py `run_piano_module`); a timeout is dropped, never retried, logged to
`lm_studio.jsonl` with `"error": "piano_module_timeout"`, and counted in the
`piano_module_drops` benchmark. Reports are cached per `(agent, module)` with
a `PIANO_MODULE_CACHE_TTL = 2` module-tick TTL so the perception/social/desire/
reflection stagger (perception+desire every module-tick, social every 2nd,
reflection every 3rd) fills an off-tick module's slot from its last real
report instead of an empty one.

Revised context formula with PIANO on: LM Studio's context length must be ≥
~3,400 tokens × (`MAX_CONCURRENT_LLM` + `PIANO_CONCURRENT_LLM`) = ~3,400 × 5 =
~17,000 tokens minimum. `scripts/lms_load.py` applies the target config:
`qwen/qwen3.5-9b`, context 20,000, parallel 3 (~6,666 tokens/slot per decision
slot; the 2 module slots draw from the same loaded context budget), flash
attention on — the canonical CLI loader (REST-load rung with a `lms load` CLI
fallback for context+parallel only; KV-cache quantization/speculative-decoding
flags are GUI/SDK-only or build-dependent, see the script's docstring). 20,000
is sufficient for the default roster; if LM Studio can't stretch the context
budget further, reduce the roster to 6 before enabling `PIANO_MODULES` rather
than reverting the flag — reduce it via a JSON POST body field
(`{"agents": N}`) on `/control/reset` (see specs/04-http-api.md), not a URL
query parameter, or via the `SIM_AGENTS` environment variable at server
startup (server.py, default 8, clamped to the `AGENT_DEFS` count — see
specs/06-agents.md).

`META_SYSTEM` (sim_engine.py, default `True` since Sid-parity Phase 3) —
autobiography/persona meta update, still bounded by `MAX_CONCURRENT_LLM`
(runs inline on the decision path, not on `piano_workers`). Authored beliefs
and adoption events give the rotating autobiography update material to
summarize.
