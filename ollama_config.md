# Ollama config (simulation)

Successor to `lms_config.md` (LM Studio is permanently unavailable — user
decision 2026-07-24, see `docs/plan-ollama-migration.md`). Target load for
this project on Ollama 0.32.3. If `ollama ps` / `/api/ps` shows anything
else after a restart, re-apply with `uv run python scripts/ollama_setup.py`
(the canonical loader — see below).

## Required settings

| Setting | Value | How it's set | Why |
|---|---|---|---|
| Smart model | `sim-smart` (from `ollama/Modelfile.smart`, base GGUF: Qwen3.5-9B-Q4_K_M imported from the old LM Studio cache) | `scripts/ollama_setup.py` → `ollama create sim-smart -f ollama/Modelfile.smart` | Must match `MODEL_SMART` in `simulation/server.py` once Phase 2 lands (currently still `qwen/qwen3.5-9b`, pending cutover) |
| Fast model | `sim-fast` (from `ollama/Modelfile.fast`, base: registry `llama3.2:3b` Q4_K_M) | `scripts/ollama_setup.py` → `ollama pull llama3.2:3b` then `ollama create sim-fast -f ollama/Modelfile.fast` | Must match `MODEL_FAST` once Phase 2/3 land; distinct from `sim-smart` so the two-model MUST is real (was a no-op under LM Studio) |
| Smart context length | **20480** (`num_ctx`) | `ollama/Modelfile.smart` `PARAMETER num_ctx` | Successor to LM Studio's 20000/parallel-3 budget; round number above the old value, per-slot budget below |
| Fast context length | **4096** (`num_ctx`) | `ollama/Modelfile.fast` `PARAMETER num_ctx` | PIANO module / summarizer / meta-system prompts run ~1k tokens; 4096 leaves generous headroom without wasting VRAM |
| Sampling defaults | `temperature 0.4` (smart) / `0.5` (fast), `top_p 0.8`, `top_k 20`, `min_p 0` | `PARAMETER` lines in both Modelfiles | Mirrors `simulation/server.py`'s `NON_THINKING_SAMPLING` dict + the routine-decision (0.4) / `lm_complete` (0.5) default temperatures. Per-request `options` sent by server.py still win — these are Modelfile-level defaults for standalone/manual use |
| Parallel slots | **3** (`OLLAMA_NUM_PARALLEL`) | user env var, `scripts/ollama_setup.py` | Matches `MAX_CONCURRENT_LLM = 3` in `simulation/sim_engine.py` |
| Max loaded models | **2** (`OLLAMA_MAX_LOADED_MODELS`) | user env var, `scripts/ollama_setup.py` | Required for dual residency — default (1) evicts one model whenever the other is called (Phase 0 finding #7) |
| Flash attention | **on** (`OLLAMA_FLASH_ATTENTION=1`) | user env var, `scripts/ollama_setup.py` | Cheaper attention at 20k context |
| Keep-alive | **-1** (`OLLAMA_KEEP_ALIVE=-1`) | user env var, `scripts/ollama_setup.py` | Sim runs 24/7 — never unload either model between calls |
| API port | **11434** | Ollama default | `http://localhost:11434` |
| Endpoint | **native `/api/chat`** — NOT `/v1/chat/completions` | server.py call sites (Phase 2) | Only the native endpoint honors `think:false` (Phase 0 finding #4); OpenAI-compat silently ignores it and would reintroduce the thinking-leak epidemic |

## Restore after reset

PowerShell (from the repo root):

```powershell
uv run python scripts/ollama_setup.py
```

The script: sets the four env vars via `setx`, restarts the Ollama app/
service so they take effect, pulls `llama3.2:3b` if missing, runs `ollama
create` for both `sim-smart`/`sim-fast` (idempotent — safe to re-run),
warms both with a trivial `/api/chat` call (`keep_alive: -1`), and verifies
dual residency via `/api/ps`. Readback only: `uv run python
scripts/ollama_setup.py --check`.

Expected `ollama ps` / `/api/ps` output (both models resident
simultaneously):

```
NAME                MODEL              SIZE      PROCESSOR    UNTIL
sim-smart:latest    sim-smart:latest   6.5 GB     100% GPU     Forever
sim-fast:latest     sim-fast:latest    2.0 GB     100% GPU     Forever
```

(measured live 2026-07-24: `size_vram` 6,526,903,254 + 2,720,383,630 bytes,
both 100% GPU-offloaded, `expires_at` far-future because `keep_alive: -1`).

## Thinking control (the contract)

Routine turns must suppress reasoning entirely — same contract as
`lms_config.md`'s "Thinking control" section, ported to Ollama's mechanism:

- **Endpoint matters.** Native `/api/chat` + `"think": false` fully
  suppresses reasoning: no `thinking` field in the response, no `<think>`
  tags leaking into `content`. Verified live (Phase 0 finding #4):
  `eval_count` dropped 1288→81 tokens, wall time 77s→4.8s when `think:false`
  was set vs. left unset.
- `"think"` **unset** on native `/api/chat` returns a *separate* `thinking`
  field (not inline in `content`, unlike LM Studio's `reasoning_content`
  channel) — but the huge unset-case token/latency cost still makes
  `think:false` mandatory on every routine call.
- **OpenAI-compat `/v1/chat/completions` ignores `think:false` outright** —
  confirmed live, `reasoning` field populated and latency matched the
  unset-native case (19.5s) regardless of the parameter. This is why native
  `/api/chat` is the only acceptable endpoint for this repo (see the
  required-settings table above).
- **Contract:** once Phase 2 lands, decision logs (`llm.jsonl`, successor to
  `lm_studio.jsonl`) must show populated `message.content` and no
  `message.thinking` field on routine turns. If `thinking` starts appearing
  again, something is sending `think` unset or hit the OpenAI-compat
  endpoint by mistake.

### Thinking-epidemic history (carried from LM Studio, still load-bearing)

A full LM Studio session (6,320 calls) once measured 57% of high-stakes/
thinking turns — 65% of the elder's — returning `bad_response`
(`finish_reason: "length"`, empty content): with thinking ON, the model
spent its whole completion budget on reasoning before ever emitting the
decision JSON. `THINKING_ENABLED_HIGH_STAKES = False` (server.py) is the
fix currently shipped and unaffected by this migration — Ollama inherits
the same risk profile if that flag is ever flipped back on, so the
`think:false` contract above is not optional even for what LM Studio called
"high-stakes" turns. See `lms_config.md`'s "Thinking on high-stakes turns"
section for the full Phase 1/2/3 history (unchanged, historical record).

## Overflow / truncation contract

**Ollama 0.32.3 does NOT silently truncate** (contradicts the migration
plan's original assumption — see TASKS_PENDING.md "Ollama Phase 0 findings"
finding #5). It returns a structured error instead:

```
HTTP 400
{"error":{"code":400,"message":"request (N tokens) exceeds the available
context size (num_ctx tokens)...","type":"exceed_context_size_error",
"n_prompt_tokens":N,"n_ctx":num_ctx}}
```

This is a **cleaner** signal than LM Studio's error-string sniffing
(`is_context_overflow_error`). Phase 2 must catch this HTTP 400 +
`exceed_context_size_error` `type` field directly — no chars/4 heuristic
needed, no comparison against `prompt_eval_count` required for detection
(though `prompt_eval_count` remains available on successful requests for
logging/telemetry). A request just under the limit succeeds normally with
`prompt_eval_count` populated (verified at 360/512 tokens).

## Modelfile `SYSTEM` semantics (gates Phase 6)

Verified both directions live (Phase 0 finding #6, `ZORPCONFIRM`/
`ARRCONFIRM` probe): no system message in the request → the Modelfile's
`SYSTEM` directive applies; an explicit system message in the request →
**overrides** the Modelfile `SYSTEM` entirely (replace, not concatenate —
same semantics LM Studio's preset mechanism turned out to have, see
`lms_config.md`'s "Load-time system prompt research"). Neither `ollama/
Modelfile.smart` nor `ollama/Modelfile.fast` carries a `SYSTEM` directive
yet — Phase 6 adds one to `Modelfile.smart` (generated from server.py's
`SYSTEM_PROMPT` constant, never hand-copied) once the load-time-rulebook
feature is built and gated behind a flag. Until then server.py's own
per-request system message is authoritative on every call, which the
override semantics above make safe (nothing baked in to shadow yet).

## VRAM gate (Phase 1, measured 2026-07-24)

Hardware: RTX 3060 12 GB (12,288 MiB total).

| Point | VRAM used |
|---|---|
| Idle (Ollama running, no models loaded) | 1,191 MiB (Phase 0 baseline) |
| `sim-smart` alone resident (num_ctx 20480) | — (not isolated in Phase 1; see dual-resident numbers below) |
| Both `sim-smart` + `sim-fast` resident, idle (post `ollama_setup.py`) | 11,899 MiB |
| Both resident, **during** 3 concurrent `sim-smart` + 2 concurrent `sim-fast` `/api/chat` calls (mirrors `MAX_CONCURRENT_LLM=3` / `PIANO_CONCURRENT_LLM=2`) | 11,921–11,923 MiB peak |
| Both resident, after the concurrent burst | 11,923 MiB, both still 100% GPU / fully resident in `/api/ps` (`size_vram` unchanged, no eviction) |

**Result: PASS at rung 0 (no fallback needed).** All 5 concurrent calls
completed successfully (wall time 5.63s for the batch; individual calls
2.89s–5.63s), `/api/ps` showed both models with unchanged `size_vram` and
`100%` GPU processor share before and after — no eviction, no CPU offload
thrash. Headroom is thin (~365–389 MiB free of 12,288 MiB) but stable
across repeated bursts in this test; the fallback ladder
(`OLLAMA_KV_CACHE_TYPE=q8_0` first, per the migration plan) was **not
needed** and was not applied. If future VRAM pressure appears (e.g. Path 1
flags growing prompt size further, or the browser/OS consuming more GPU
memory concurrently), that env var is the first lever — not yet set.

## Benchmarking

Reference numbers from the LM Studio era (`lms_config.md`, 2026-07-11,
RTX 3060 12 GB) remain the baseline until Phase 4 ports
`scripts/llm_replay_bench.py` to the Ollama endpoint and re-measures:

| Run | median | thinking leak | JSON valid | notes |
|---|---|---|---|---|
| LM Studio patched @ 13000/2 | 12.1s | 0% | 100% | historical baseline (`lms_config.md`) |
| LM Studio patched @ 20000/3, FA, 3 workers | 17.7s | 0% | 100% | historical baseline, 3-way concurrent — this is the reference Phase 4's +15% threshold is measured against |
| Ollama replay bench (Phase 4) | _pending_ | _pending_ | _pending_ | fill in once `llm_replay_bench.py` is ported |
| Ollama 45-min live soak (Phase 4) | _pending_ | _pending_ | _pending_ | fallback rate / `piano_module_drops` / truncation-detection audit |

## Related sim knobs (not Ollama)

These live in code; today they still target LM Studio (Phase 2 will cut
them over):

- `MAX_CONCURRENT_LLM = 3` — `simulation/sim_engine.py`
- `DISABLE_THINKING_ROUTINE`, `THINKING_ENABLED_HIGH_STAKES`,
  `HIGH_STAKES_MAX_TOKENS`, `NON_THINKING_SAMPLING` / `THINKING_SAMPLING`,
  `ROUTINE_PRESENCE_PENALTY` — `simulation/server.py`
- `INVENTION_MAX_TOKENS = 1024`, `INVENTION_TEMPERATURE = 0.6` —
  `simulation/server.py`
- `LM_STUDIO_URL` / overflow-detection call sites — still LM Studio-shaped
  until Phase 2 lands (see plan Phase 2 for the full cutover list)

## Notes

- Fast-model quality caveat (Phase 0 finding #8, screened against the
  now-removed `probe-fast`, an FP16 GGUF with a broken template — NOT the
  model `sim-fast` is built from): 4/8 clean pass, 2/8 borderline, 2/8
  fail (ungrounded hallucinated detail). `sim-fast` uses a different base
  (`llama3.2:3b`, clean Q4_K_M from the registry, matching Llama 3.2
  template) specifically to avoid the template-leak failure mode; its own
  quality screen against the same 10-prompt set is Phase 3's live gate, not
  yet re-run against this exact model.
- `probe-smart` and `probe-fast` (Phase 0 throwaway models) were removed
  2026-07-24 once `sim-smart`/`sim-fast` were confirmed working, to avoid
  confusion in `ollama list`.
- Ollama does **not** cancel server-side generation when a client aborts/
  times out a `stream:false` request (Phase 0 operational finding) —
  orphaned timed-out requests keep consuming a queue slot. Phase 2's
  retry/timeout logic must account for this; naive retry-on-timeout would
  compound queue depth rather than recovering from it.
- The leftover `%USERPROFILE%\.lmstudio\` GGUF cache directory (source of
  the `sim-smart` base model) is left in place — Phase 5 flags it as the
  user's to delete or keep, agents do not delete it.
