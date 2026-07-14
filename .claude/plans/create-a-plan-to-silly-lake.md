# Make LM Studio smarter/faster for the village sim

## Context

Review of [lms_config.md](../../Desktop/GitServ/simulation/lms_config.md) against the live pipeline found that the biggest cost in the sim's LLM loop is unintended: routine villager decisions (median ~18s, p90 ~22s) are still running Qwen's **thinking mode**, because the payload's `"thinking": {"type": "disabled", "budget_tokens": 0}` (server.py:2733 and :2354) is Anthropic-API format that LM Studio's OpenAI-compatible endpoint silently ignores — confirmed by `reasoning_content` being populated on every call (lms_config.md:62). Secondary gaps: sampling params beyond temperature are unpinned (drift across LM Studio presets), the per-agent persona is appended to the *system* prompt (server.py:2712-2713) which busts LM Studio's longest-common-prefix KV cache on every agent rotation, `DECISION_SCHEMA` has no `required` fields, and LM Studio perf features (flash attention, KV-cache quantization, speculative decoding) are unused and undocumented. There is no repeatable benchmark — the 2026-07-05 model comparison was ad-hoc.

Goal: cut routine decision latency substantially, pin reproducibility, raise throughput (parallel 3), and leave behind a repeatable replay benchmark to prove it.

Verified externally:
- The correct disable mechanism is top-level `"chat_template_kwargs": {"enable_thinking": false}` in the request body ([Qwen discussion](https://github.com/QwenLM/Qwen3/discussions/1300)); there is a [known LM Studio bug](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1990) where it's sometimes ignored, so we also send Qwen's `/no_think` soft switch as belt-and-braces and verify empirically.
- `lms load` has **no flags** for flash attention / KV quant / draft model ([docs](https://lmstudio.ai/docs/cli/local-models/load)). CLI-scriptable alternatives (no GUI, per user requirement): the REST load endpoint `POST http://localhost:1234/api/v1/models/load` accepts `flash_attention` + `context_length` ([docs](https://lmstudio.ai/docs/developer/rest/load)); the `lmstudio` Python SDK load config accepts `flashAttention`, `llamaKCacheQuantizationType`, `llamaVCacheQuantizationType`, `contextLength` ([config reference](https://lmstudio.ai/docs/typescript/api-reference/llm-load-model-config)). Speculative decoding has no documented CLI/API path for the OpenAI endpoint — optional, only if the installed `lms load --help` reveals a draft flag.

## Phase 1 — Replay benchmark script (new: `scripts/llm_replay_bench.py`)

Build this FIRST so every later change gets a before/after number.

- Input: a session log `simulation/logs/<ts>/lm_studio.jsonl` (arg or latest session by default), replay up to `--n 100` logged decision requests against `http://localhost:1234/v1/chat/completions`.
- Two modes: `--as-logged` (resend the logged payload verbatim = baseline) and `--patched` (apply the Phase 2 payload transforms: chat_template_kwargs, /no_think, sampling pins — import nothing from server.py; implement the same small patch inline so the script stays standalone).
- Metrics per run, printed as a summary table + written to `scratch` JSONL: latency median/p90, `finish_reason == "length"` rate, `content` empty w/ `reasoning_content` populated rate (thinking-leak detector), JSON-parse validity, action distribution (distinct actions, `move_to_district` share — comparable to the 2026-07-05 numbers at server.py:41-46).
- Graceful failure if LM Studio is down (same style as `scripts/path1_soak.py`'s prompt-check).
- Run baseline now and save the report before touching anything.

## Phase 2 — Code changes in `simulation/server.py`

### 2a. Actually disable thinking on routine turns
- Add module constant `DISABLE_THINKING_ROUTINE = True` next to the model-routing constants (~line 47).
- In `build_decision_payload` (:2696): replace the bogus `"thinking"` key with `"chat_template_kwargs": {"enable_thinking": False}` **only when** `model_for_decision(data) == MODEL_FAST` (i.e. routine villager turns). Elder / `invention_only` / `sprite_design_only` / invention-REQUIRED turns keep thinking ON — this makes the smart/fast routing real even with one loaded model.
- Belt-and-braces for the known LM Studio bug: on the same routine turns, append `\n/no_think` to the end of the user message. Harmless if the template kwarg already works.
- In `lm_complete` (:2333, background cognition — memory summaries, chatter): same treatment, always disabled (all its call sites are low-stakes).
- Remove the dead `"thinking"` key from both places.
- Do NOT lower `max_tokens: 512` — with reasoning off it becomes genuine headroom.

### 2b. Pin sampling parameters
- Routine (non-thinking) turns and `lm_complete`: add `"top_p": 0.8, "top_k": 20, "min_p": 0` (Qwen non-thinking recommendation; keep existing temperatures — they're behavior-tuned).
- Thinking turns (invention/elder/sprite): `"top_p": 0.95, "top_k": 20`.
- Optional experiment constant `ROUTINE_PRESENCE_PENALTY = 0.0` (off by default) with a comment pointing at the move_to_district-fixation benchmark, so it's a one-line flip later.

### 2c. Tighten `DECISION_SCHEMA`
- Add `"required": ["action", "reasoning"]` (:629). `normalize_decision` (:1843) already tolerates absence, so this is pure upside at decode time.

### 2d. Persona → user prompt (KV-cache prefix reuse)
- In `build_decision_payload` (:2706-2713): stop appending `YOUR PERSONA (act in character): ...` to `system_content`; instead prepend that same line to the **user** message content (all three paths — decision, invention, sprite — currently share the append, keep that uniformity).
- Result: the large static system prompt becomes a shared cacheable prefix across all 8 agents rotating through the slots. Token count is unchanged (matters for the open HANDOFF prompt-budget issue).

## Phase 3 — LM Studio perf settings (CLI-only) and throughput bump

All LM Studio actions via command line — no GUI. Because the installed LM Studio version determines which surface exposes which knob, start with discovery and walk a fallback ladder:

1. **Discovery**: run `lms version`, `lms load --help`, and `lms ps`. If the installed CLI has newer undocumented flags (flash attention, KV quant, draft model — it already has `--parallel`, which the online docs omit), prefer them and skip the ladder below.
2. **New loader script `scripts/lms_load.py`** (the canonical restore command going forward): uses the `lmstudio` Python SDK (`uv add lmstudio` or `uv run --with lmstudio`) to load `qwen/qwen3.5-9b` with `{"contextLength": 20000, "flashAttention": True, "llamaKCacheQuantizationType": "q8_0", "llamaVCacheQuantizationType": "q8_0"}` and the identifier the sim expects. If the SDK's load config rejects a parallel-slots field (likely — undocumented), do the load in two steps: `lms load qwen/qwen3.5-9b --context-length 20000 --parallel 3 --identifier "qwen/qwen3.5-9b" -y` first, then verify whether flash-attention/KV-quant survived via the script's `echo_load_config`-style readback; if the two mechanisms can't be combined in the installed version, fall back to `lms load` (ctx+parallel) + REST `POST /api/v1/models/load` with `"flash_attention": true` (curl/Invoke-RestMethod), and accept losing KV quant — then keep context at 13000–16000 instead of 20000.
3. **Speculative decoding — optional**: only if step 1 revealed a CLI draft-model flag. If so: `lms get` the smallest same-family qwen3.5 instruct draft (0.5–1B GGUF), load with it, keep only if the replay bench shows a latency win at equal validity. Otherwise drop it (no GUI allowed, no documented API path).
4. `simulation/sim_engine.py`: `MAX_CONCURRENT_LLM = 2 → 3` (:406) — only after the reload sticks with parallel 3; comment updated. CLAUDE.md context-sizing note (`3400 × parallel slots`) still holds at 20000/3.
5. VRAM check before committing: `lms load --estimate-only` with the target settings on the 12GB card; priority order if tight: parallel 3 + flash attention first, KV quant next, draft model last.

## Phase 4 — Docs: rewrite `lms_config.md`

- Required-settings table: context **20000**, parallel **3**, plus new rows for flash attention / KV cache Q8_0 noting which CLI surface sets each (`lms load` flag vs `scripts/lms_load.py` SDK loader vs REST load endpoint), per what Phase 3 discovery actually found.
- Restore-after-reset section: replace the raw `lms load` line with the Phase 3 CLI sequence (or `uv run python scripts/lms_load.py`) + a readback checklist.
- Notes: replace the "thinking model / reasoning_content" note with the new contract — routine turns must show empty `reasoning_content`; if not, the LM Studio bug regressed, see `DISABLE_THINKING_ROUTINE`.
- Add a "Benchmarking" section: how to run `scripts/llm_replay_bench.py` and the baseline numbers captured in Phase 1.
- Update the related-sim-knobs list (`MAX_CONCURRENT_LLM = 3`).

## Files touched

| File | Change |
|---|---|
| `scripts/llm_replay_bench.py` | new — replay benchmark |
| `scripts/lms_load.py` | new — CLI loader applying flash attention / KV quant via the lmstudio SDK |
| `simulation/server.py` | 2a–2d (build_decision_payload, lm_complete, DECISION_SCHEMA, constants) |
| `simulation/sim_engine.py` | `MAX_CONCURRENT_LLM = 3` |
| `lms_config.md` | Phase 4 rewrite |
| `CLAUDE.md` | one-line updates: MAX_CONCURRENT_LLM, context target line |

## Verification

1. **Baseline first**: Phase 1 bench `--as-logged` on the latest session logs → save report.
2. After Phase 2: bench `--patched` → expect latency median well under baseline, thinking-leak rate ~0% on routine prompts, JSON validity ≥ baseline, action diversity not worse (move_to_district share ≤ ~35%).
3. After Phase 3 reload: `lms ps` shows context 20000 / parallel 3; rerun bench; then restart the sim server per CLAUDE.md (titled cmd window) and watch a live session's `lm_studio.jsonl` for: routine decisions with populated `content` + empty `reasoning_content`, no `context size has been exceeded`, no `finish_reason: "length"` on routine turns; invention turns still produce valid blueprints (thinking intact).
4. Deterministic regressions: `uv run python scripts/path1_smoke.py` and `uv run python scripts/sid_parity_smoke.py` (no LM Studio needed).
5. Soak sanity: `uv run python scripts/path1_soak.py prompt-check` (prompt-token budget unaffected by the persona move).

## Rollback levers

- `DISABLE_THINKING_ROUTINE = False` restores thinking everywhere.
- `lms load ... --context-length 13000 --parallel 2` + `MAX_CONCURRENT_LLM = 2` restores old throughput shape.
- Flash attention / KV quant / draft model are independent load options in `scripts/lms_load.py` — drop individually and reload if quality dips.
