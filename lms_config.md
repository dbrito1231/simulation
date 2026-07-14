# LM Studio config (simulation)

Target load for this project. If `lms ps` shows anything else after a restart or model reload, re-apply with `uv run python scripts/lms_load.py` (the canonical loader — see below).

## Required settings

| Setting | Value | How it's set | Why |
|---|---|---|---|
| Model / identifier | `qwen/qwen3.5-9b` | model key (REST load's id defaults to it) | Must match `MODEL_SMART` / `MODEL_FAST` in `simulation/server.py` |
| Context length | **20000** | `scripts/lms_load.py` (REST) or `lms load -c` | Per-slot budget must exceed the ~5.8k max prompt seen with all Path 1 flags on |
| Parallel slots | **3** | `scripts/lms_load.py` (REST) or `lms load --parallel` | Matches `MAX_CONCURRENT_LLM = 3` in `simulation/sim_engine.py`; dropped to 2 on 2026-07-14 for a Phase 2 thinking experiment, reverted back to 3 the same day (Phase 3) after the experiment showed no reasoning benefit — max routine-turn throughput with `THINKING_ENABLED_HIGH_STAKES = False` |
| Per-slot budget | ~6666 tokens (`20000 ÷ 3`) | derived | Covers the ~5,725-6,163 token max prompt seen with all Path 1 flags on; thinking is disabled on high-stakes turns so no extra completion headroom is needed |
| Flash attention | **on** | `scripts/lms_load.py` (REST only — no `lms load` flag) | Cheaper attention at 20k context; measured neutral-to-slightly-positive |
| API port | **1234** | LM Studio server settings | `http://localhost:1234` (OpenAI-compatible) |

Not applied (evaluated 2026-07-11): **KV-cache quantization** — only settable via the `lmstudio` Python SDK, which cannot set parallel slots, and the 20000/3 load fits VRAM without it. Revisit only if context needs to grow further.

### Wrong config (do not use)

| Setting | Bad value | Effect |
|---|---|---|
| Context | 13000 with parallel 3 | ~4333/slot, tight against the ~5.8k max prompt → risk of context-overflow fallbacks |
| Parallel | 4 @ 20000 | 5000/slot < max prompt; also no throughput gain (GPU-bound) |
| Parallel | 2 @ 20000 | ~10000/slot — this was the Phase 2 config, adopted to give high-stakes thinking turns room to finish `reasoning_content`. Phase 3 (2026-07-14) found thinking gives no measurable reasoning benefit, so this is no longer worth the 33% concurrency cost; parallel 3 is the current/correct config. |

## Restore after reset

PowerShell (from the repo root):

```powershell
uv run python scripts/lms_load.py
```

The script unloads everything, then loads via `POST /api/v1/models/load` with `context_length: 20000, parallel: 3, flash_attention: true` and prints the echoed load config; if the REST rung fails it falls back to `lms load qwen/qwen3.5-9b --context-length 20000 --parallel 3 --identifier "qwen/qwen3.5-9b" -y` (which cannot set flash attention). Readback only: `uv run python scripts/lms_load.py --check`.

Expected `lms ps` line:

```
IDENTIFIER         MODEL              STATUS    SIZE       CONTEXT    PARALLEL
qwen/qwen3.5-9b    qwen/qwen3.5-9b    IDLE      ~6.5 GB    20000      3
```

## Quick checks

```powershell
# Server up?
lms server status
# → The server is running on port 1234.

# Identifier visible to the sim?
# GET http://localhost:1234/v1/models should list "qwen/qwen3.5-9b"
```

If the loaded identifier does not match `MODEL_SMART`/`MODEL_FAST`, `run_agent_decision` prints a notice, disables routing for the session, and retries with `"local-model"` (works for a single loaded model, but prefer matching ids).

## Thinking control (the contract)

Routine villager turns run with reasoning **disabled** via a top-level `"reasoning_effort": "none"` payload field (`DISABLE_THINKING_ROUTINE` in `simulation/server.py`). High-stakes turns (elder, invention, sprite, invention-REQUIRED — `is_high_stakes_turn()`) keep thinking ON.

- Probed live 2026-07-11 against this LM Studio build: `reasoning_effort: "none"` is the only knob it honors. `chat_template_kwargs={"enable_thinking": false}`, Qwen's `/no_think` soft switch, and Anthropic-style `"thinking"` objects are all silently ignored.
- **Contract:** in `lm_studio.jsonl`, routine decisions must show populated `content` and empty `reasoning_content`. If the JSON starts landing in `reasoning_content` again, LM Studio regressed the field — re-probe and see `DISABLE_THINKING_ROUTINE`.

### Thinking on high-stakes turns (Phase 1/2 history)

A full session (6,320 calls) measured 57% of high-stakes/thinking turns — 65% of the elder's — returning `bad_response` (`finish_reason: "length"`, empty `content`): with thinking ON, the model spent its whole `max_tokens` budget (512-1024) on `reasoning_content` before ever emitting the decision JSON, because a thinking turn needs ~950-1,300 completion tokens to finish and the ~5,725-6,163 token prompt left no room in the old ~6,666-token/slot budget (parallel 3). `reasoning_effort: low/medium` is ignored on this build, so reasoning can't be bounded.

- **Phase 1** (2026-07-14): disabled thinking on high-stakes turns entirely (`THINKING_ENABLED_HIGH_STAKES = False`) to stop the epidemic immediately.
- **Phase 2** (2026-07-14): fixed the root cause — dropped `parallel` 3→2 (10,000 tokens/slot, same total VRAM) and added `HIGH_STAKES_MAX_TOKENS = 1600`, then re-enabled thinking (`THINKING_ENABLED_HIGH_STAKES = True`). 6,163 worst-case prompt + 1,600 = 7,763 < 10,000, so a thinking turn now has room to finish.
- **Phase 3 verdict** (2026-07-14): a live analysis of 48 diverse high-stakes samples (`assign_task`, `propose_blueprint`, `sage_review_blueprint`, `approve_blueprint`, `upgrade_structure`, `contribute_resources`, `collect_resource`, `move_to_district`) showed **zero measurable reasoning benefit** from thinking — with thinking on, the model emits the same direct JSON answer, just routed through `reasoning_content` instead of `content` (`THINKING_SAMPLING` doesn't set `reasoning_effort`, so nothing bounds or shapes the "reasoning"). The only sample with genuine descriptive text was `submit_structure_sprite`, an unrelated creative-task pattern (always high-stakes regardless of this flag). Since thinking has no measured upside but costs 33% concurrency, reverted to `THINKING_ENABLED_HIGH_STAKES = False` and `parallel = 3`. See `.claude/plans/only-create-the-plan-linear-iverson.md`.

## Benchmarking

Repeatable replay benchmark (replays logged decision calls from a session's `lm_studio.jsonl`):

```powershell
# Pause the sim first so its think traffic doesn't skew latency:
curl -X POST http://127.0.0.1:5001/control/pause
uv run python scripts/llm_replay_bench.py --as-logged --n 40   # baseline payload shape
uv run python scripts/llm_replay_bench.py --patched --n 40     # current payload shape
curl -X POST http://127.0.0.1:5001/control/resume
```

Reports go to `simulation/logs/replay_bench/`. Reference numbers (2026-07-11, session `2026-07-11T21-35-10`, RTX 3060 12 GB):

| Run | median | thinking leak | JSON valid | notes |
|---|---|---|---|---|
| as-logged @ 13000/2 | 12.2s | 100% | 100% | old payload: all output via `reasoning_content` |
| patched @ 13000/2 | 12.1s | 0% | 100% | `reasoning_effort: none` fixes the channel; latency is prompt-processing-bound |
| patched @ 20000/3, FA, 3 workers | 17.7s | 0% | 100% | per-call slower under 3-way concurrency; total throughput ≈ equal (GPU-bound) |

Measured cost split per routine call: ~3–4s prompt processing (~5.3k tokens) + ~4s decode (~80 tokens). Prefix-cache probe: identical repeat 7.3s→4.3s, but a *shared system prefix alone* gives no reuse on this build — the persona-in-user-message change (server.py `build_decision_payload`) is future-proofing, not a measured win yet.

## Related sim knobs (not LM Studio)

These live in code; they assume the LM Studio settings above:

- `MAX_CONCURRENT_LLM = 3` — `simulation/sim_engine.py`
- `DISABLE_THINKING_ROUTINE`, `THINKING_ENABLED_HIGH_STAKES`, `HIGH_STAKES_MAX_TOKENS`, `NON_THINKING_SAMPLING` / `THINKING_SAMPLING` (pinned top_p/top_k/min_p), `ROUTINE_PRESENCE_PENALTY` (experiment lever, off) — `simulation/server.py`
- `INVENTION_MAX_TOKENS = 1024`, `INVENTION_TEMPERATURE = 0.6` — `simulation/server.py`
- Context-overflow retry: `run_agent_decision()` slim-prompt retry + `context_overflow` log in `lm_studio.jsonl`

## Notes

- LM Studio does not always persist context/parallel across unload/reload or app restart — re-run `scripts/lms_load.py` if `lms ps` drifts.
- Speculative decoding: **evaluated 2026-07-13, rejected — do not re-try without new evidence.** `scripts/lms_load.py --draft {simple,mtp}` has both modes wired for re-testing.
  - `simple` (separate 0.8B draft, `qwen3.5-0.8b` on disk): blocked at load time on this build — "Load-time draft-model speculative decoding is only supported by the llama.cpp engine protocol runtime"; qwen3.5-9b loads under a different engine.
  - `mtp` (`qwen3.5-9b-mtp` weights, identifier kept `qwen/qwen3.5-9b`): loads and genuinely speculates (97% draft accept rate), but fails the adoption rule. Bench @ 20000/3, `--patched --n 40 --workers 3` vs baseline: median 19.3s vs 20.5s (−5.5%), p90/mean slightly WORSE, json_valid 97.5% vs 100%, and **thinking leak 100% vs 0%** — the MTP variant does not honor `reasoning_effort: "none"`, breaking the Thinking-control contract above. Reports: `simulation/logs/replay_bench/2026-07-14T01-16-52_patched.jsonl` (baseline) / `2026-07-14T01-22-20_patched.jsonl` (mtp). Also note the mtp rung loads via CLI (REST rejects it), which cannot set flash attention.
