# Ollama config (simulation)

Successor to `lms_config.md` (LM Studio is permanently unavailable — user
decision 2026-07-24, see `docs/plan-ollama-migration.md`). Target load for
this project on Ollama 0.32.3. If `ollama ps` / `/api/ps` shows anything
else after a restart, re-apply with `uv run python scripts/ollama_setup.py`
(the canonical loader — see below).

## Required settings

| Setting | Value | How it's set | Why |
|---|---|---|---|
| Smart model | `sim-smart` (from `ollama/Modelfile.smart`, base GGUF: Qwen3.5-9B-Q4_K_M imported from the old LM Studio cache) | `scripts/ollama_setup.py` → `ollama create sim-smart -f ollama/Modelfile.smart` | Matches `MODEL_SMART` in `simulation/server.py`. Serves **every decision turn** — routine and high-stakes alike (`model_for_decision` always returns `MODEL_SMART`), plus invention/sprite turns |
| Fast model | `sim-fast` (from `ollama/Modelfile.fast`, base: registry `llama3.2:3b` Q4_K_M) | `scripts/ollama_setup.py` → `ollama pull llama3.2:3b` then `ollama create sim-fast -f ollama/Modelfile.fast` | Matches `MODEL_FAST` (Phase 3, 2026-07-24 — live, revised). Serves **only background cognition**: all PIANO modules, the memory summarizer/wiki merge, meta system/autobiography, and belief-pitch scoring — never decisions. An initial Phase 3 attempt also routed routine decisions here; a live soak found `piano_module_drops` rising to ~25-38% (vs. ~9% pre-migration) from decision/module contention on `sim-fast`'s parallel slots, so decisions were moved back to `sim-smart` entirely. Distinct from `sim-smart` so the two-model MUST is real (was a no-op under LM Studio) |
| Smart context length | **20480** (`num_ctx`) | `ollama/Modelfile.smart` `PARAMETER num_ctx` | Successor to LM Studio's 20000/parallel-3 budget; round number above the old value, per-slot budget below |
| Fast context length | **4096** (`num_ctx`) | `ollama/Modelfile.fast` `PARAMETER num_ctx` | PIANO module / summarizer / meta-system prompts run ~1k tokens; 4096 leaves generous headroom without wasting VRAM |
| Sampling defaults | `temperature 0.4` (smart) / `0.5` (fast), `top_p 0.8`, `top_k 20`, `min_p 0` | `PARAMETER` lines in both Modelfiles | Mirrors `simulation/server.py`'s `NON_THINKING_SAMPLING` dict + the routine-decision (0.4) / `lm_complete` (0.5) default temperatures. Per-request `options` sent by server.py still win — these are Modelfile-level defaults for standalone/manual use |
| Parallel slots | **3** (`OLLAMA_NUM_PARALLEL`) | user env var, `scripts/ollama_setup.py` | Matches `MAX_CONCURRENT_LLM = 3` in `simulation/sim_engine.py` |
| Max loaded models | **2** (`OLLAMA_MAX_LOADED_MODELS`) | user env var, `scripts/ollama_setup.py` | Required for dual residency — default (1) evicts one model whenever the other is called (Phase 0 finding #7) |
| Flash attention | **on** (`OLLAMA_FLASH_ATTENTION=1`) | user env var, `scripts/ollama_setup.py` | Cheaper attention at 20k context |
| Keep-alive | **-1** (`OLLAMA_KEEP_ALIVE=-1`) | user env var, `scripts/ollama_setup.py` | Sim runs 24/7 — never unload either model between calls |
| API port | **11434** | Ollama default | `http://localhost:11434` |
| Endpoint | **native `/api/chat`** — NOT `/v1/chat/completions` | server.py call sites (Phase 2) | Only the native endpoint honors `think:false` (Phase 0 finding #4); OpenAI-compat silently ignores it and would reintroduce the thinking-leak epidemic |

### Per-slot budget lesson (carried from `lms_config.md`, still applies)

Ollama splits `num_ctx` across `OLLAMA_NUM_PARALLEL` slots the same way LM
Studio's context/parallel split worked (llama.cpp-based server underneath
both). The load-bearing arithmetic from the LM Studio era carries over
unchanged: `num_ctx ÷ parallel` must clear the largest real prompt, which
measured ~5,725–6,163 tokens with all Path 1 flags on. At `num_ctx=20480` /
`parallel=3` that is ~6,827 tokens/slot — comfortable headroom, mirroring why
LM Studio's `20000/3` (~6,666/slot) was the last-good config there. The
LM Studio-era "wrong config" combinations to avoid if `parallel` or `num_ctx`
is ever retuned: too-low context at a given parallel count starves the
per-slot budget below the max prompt (risking truncation — see the Overflow
contract below); raising `parallel` without raising `num_ctx` proportionally
shrinks per-slot budget for no throughput gain once GPU-bound. If this repo's
`num_ctx`/`OLLAMA_NUM_PARALLEL` pair is ever changed, redo this division
against the current max-prompt figure before shipping it.

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

Routine turns must suppress reasoning entirely — same contract that the
former LM Studio runtime enforced via `reasoning_effort: "none"`, ported to
Ollama's mechanism:

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
- **Contract:** decision logs (`llm.jsonl`, successor to the former LM
  Studio-era `lm_studio.jsonl`) must show populated `message.content` and no
  `message.thinking` field on routine turns. If `thinking` starts appearing
  again, something is sending `think` unset or hit the OpenAI-compat
  endpoint by mistake.

### Thinking-epidemic history (carried from the former LM Studio runtime, still load-bearing)

Under the former LM Studio runtime, a full session (6,320 calls) once
measured 57% of high-stakes/thinking turns — 65% of the elder's — returning
`bad_response` (`finish_reason: "length"`, empty content): with thinking ON,
the model spent its whole completion budget on reasoning before ever
emitting the decision JSON. `THINKING_ENABLED_HIGH_STAKES = False`
(server.py) is the fix currently shipped and unaffected by this migration —
Ollama inherits the same risk profile if that flag is ever flipped back on,
so the `think:false` contract above is not optional even for what LM Studio
called "high-stakes" turns. Full Phase 1/2/3 history of that investigation
(unchanged, historical record):

- **Phase 1** (2026-07-14): disabled thinking on high-stakes turns entirely
  (`THINKING_ENABLED_HIGH_STAKES = False`) to stop the epidemic immediately.
- **Phase 2** (2026-07-14): tried fixing the root cause instead — dropped
  LM Studio's `parallel` 3→2 (10,000 tokens/slot, same total VRAM) and added
  `HIGH_STAKES_MAX_TOKENS = 1600`, then re-enabled thinking
  (`THINKING_ENABLED_HIGH_STAKES = True`). 6,163 worst-case prompt + 1,600 =
  7,763 < 10,000, so a thinking turn had room to finish.
- **Phase 3 verdict** (2026-07-14): a live analysis of 48 diverse
  high-stakes samples (`assign_task`, `propose_blueprint`,
  `sage_review_blueprint`, `approve_blueprint`, `upgrade_structure`,
  `contribute_resources`, `collect_resource`, `move_to_district`) showed
  **zero measurable reasoning benefit** from thinking — with thinking on,
  the model emitted the same direct JSON answer, just routed through
  `reasoning_content` instead of `content`. Since thinking had no measured
  upside but cost 33% concurrency, reverted to
  `THINKING_ENABLED_HIGH_STAKES = False` and `parallel = 3`. This verdict is
  why the flag ships `False` today and is unaffected by the Ollama cutover.

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

This is a **cleaner** signal than the former LM Studio runtime's
error-string sniffing (`is_context_overflow_error`). `is_context_overflow_error`
(server.py) catches this HTTP 400 + `exceed_context_size_error` `type` field
directly — no chars/4 heuristic needed, no comparison against
`prompt_eval_count` required for detection (though `prompt_eval_count`
remains available on successful requests for logging/telemetry). A request
just under the limit succeeds normally with `prompt_eval_count` populated
(verified at 360/512 tokens).

## Modelfile `SYSTEM` semantics (gates Phase 6)

Verified both directions live (Phase 0 finding #6, `ZORPCONFIRM`/
`ARRCONFIRM` probe): no system message in the request → the Modelfile's
`SYSTEM` directive applies; an explicit system message in the request →
**overrides** the Modelfile `SYSTEM` entirely (replace, not concatenate —
the former LM Studio runtime's preset mechanism turned out to have the same
replace-not-concatenate semantics, researched 2026-07-24 before it went
permanently offline: neither `lms load` nor `POST /api/v1/models/load`
could set a default system prompt at load time on that runtime at all, and
its request-time `"preset"` field's `systemPrompt` was proven to be
*replaced*, not merged, by an explicit request `system` message — see
TASKS_PENDING.md item 2b for the full probe). Neither `ollama/
Modelfile.smart` nor `ollama/Modelfile.fast` carries a `SYSTEM` directive —
both stay exactly as Phase 1 shipped them. Phase 6 (2026-07-24) instead adds
a `SYSTEM` directive to a SEPARATE generated Modelfile/model,
`ollama/Modelfile.smart.system` / `sim-smart-sys`, so the live `sim-smart`
the sim server actually uses is untouched. See "Load-time rulebook (dark)"
below. Until `SYSTEM_PROMPT_AT_LOAD_TIME` (server.py) is flipped True,
server.py's own per-request system message is authoritative on every routine
call, which the override semantics above make safe (nothing on `sim-smart`
itself to shadow).

## Load-time rulebook (dark) — Phase 6

Machinery shipped 2026-07-24 on the `ollama-migration` branch; **not yet
flipped on** (`SYSTEM_PROMPT_AT_LOAD_TIME = False` in `simulation/server.py`).
Revives TASKS_PENDING item 2b (LM Studio could never set a default system
prompt at load time — verified dead twice; see that item's history) using
Ollama's Modelfile `SYSTEM` directive, the mechanism LM Studio lacked. Full
design/gate: `specs/03-cognition.md` "Load-time rulebook
(`SYSTEM_PROMPT_AT_LOAD_TIME`...)".

What exists now:
- `simulation/prompts.py` — canonical `SYSTEM_PROMPT` source (moved out of
  `server.py` so a setup script can import it without importing `server.py`
  itself, which has import-time side effects — session log directory,
  live `SimEngine` construction).
- `scripts/ollama_setup.py --with-system` — generates
  `ollama/Modelfile.smart.system` (copy of `Modelfile.smart` + a `SYSTEM
  """..."""` block baked from `prompts.py`'s `SYSTEM_PROMPT`, DO-NOT-EDIT
  header) and runs `ollama create sim-smart-sys -f
  ollama/Modelfile.smart.system`. Verified live 2026-07-24: `sim-smart-sys`
  created successfully (`ollama show sim-smart-sys --system` reproduces
  `SYSTEM_PROMPT` byte-for-byte); `/api/ps` showed `sim-smart`/`sim-fast`
  `size_vram`/`expires_at` unchanged before/after — the live sim server's
  models were not touched or evicted.
- `SYSTEM_PROMPT_AT_LOAD_TIME` (server.py, default False) — when flipped,
  routine/high-stakes decision turns (not sprite/invention/slim-retry) omit
  the system message and route to `sim-smart-sys` instead of `sim-smart`.

**Flip procedure** (do not skip the soak):
1. `uv run python scripts/ollama_setup.py --with-system` (regenerates
   `Modelfile.smart.system` from the current `prompts.py` and re-bakes
   `sim-smart-sys` — idempotent, safe with the sim server running).
2. Set `SYSTEM_PROMPT_AT_LOAD_TIME = True` in `simulation/server.py`.
3. Restart the sim server (per CLAUDE.md's restart procedure).
4. A/B soak: compare decision fallback rate (`bad_response` +
   `role_fallback`) and action distribution against a flag-off session of
   similar length. Revert the flag on regression; findings go in
   TASKS_PENDING.md item 2b.

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

### KV-cache quantization (measured-if-needed, not applied)

`OLLAMA_KV_CACHE_TYPE=q8_0` is documented, not set — the VRAM gate above
passed at rung 0 (default `f16` KV cache) with the current config, so there
is nothing to fix yet. If VRAM pressure appears later (see the fallback
ladder note just above), this is the lever: set the env var (same
`setx` + Ollama-restart mechanism `scripts/ollama_setup.py` already uses for
the other four env vars — not currently wired into the script since it isn't
part of the target state), then measure with a Phase 4-style replay
comparison (`scripts/llm_replay_bench.py` against the same replayed calls,
before/after) — keep only if latency-neutral (median within noise of the
`f16` baseline in the Benchmarking table below); a regression means the VRAM
savings weren't worth the quality/speed cost and the env var should be
unset.

## Benchmarking

Reference numbers from the former LM Studio runtime (2026-07-11, RTX 3060
12 GB; full history in git — the runtime-specific config file this table
came from, `lms_config.md`, was deleted in Phase 5) are the baseline Phase 4
measured against, using `scripts/llm_replay_bench.py` ported to Ollama's
native `/api/chat` endpoint (`--patched`, replaying 40 logged decision calls
from `simulation/logs/2026-07-23T23-55-58/lm_studio.jsonl`, an LM
Studio-era session with 3,522 logged calls):

| Run | median | p90 | thinking leak | JSON valid | notes |
|---|---|---|---|---|---|
| LM Studio patched @ 13000/2 | 12.1s | — | 0% | 100% | historical baseline (former LM Studio runtime, now removed), sequential/low-parallel reference |
| LM Studio patched @ 20000/3, FA, 3 workers | 17.7s | — | 0% | 100% | historical baseline, 3-way concurrent — this is the reference Phase 4's +15% threshold is measured against |
| Ollama replay bench, `--patched`, workers=1 (sequential) | 5.03s | 6.12s | 0% | 100% | 40 calls, 0 errors; compare against the 12.1s sequential-ish reference |
| Ollama replay bench, `--patched`, workers=3 (concurrent) | **9.65s** | 10.87s | 0% | 100% | 40 calls, 0 errors; compare against the 17.7s 3-way-concurrent reference — **45.5% faster**, well inside the +15% (≤20.4s) threshold |
| Ollama replay bench, `--as-logged`, workers=3 | 8.95s | 10.67s | 0% | 100% | 40 calls, 0 errors; sanity check replaying the logged `reasoning_effort` marker as `think:false` 1:1 instead of the current-production transform — consistent with the `--patched` number |
| Ollama 45-min live soak (Phase 4) | p50 6.20s | p90 8.25s | n/a (no `<think>` leaks observed) | n/a (0/36 decision errors) | measured against the CURRENT live session at the time of the Phase 4 run (`simulation/logs/2026-07-24T21-31-59/llm.jsonl`), which had only been running ~13 min (36 decision calls) — "whatever window exists" per the Phase 4 task; decision error/fallback count 0/36 (0%); `piano_module_drops` cumulative 9 vs. 103 successful module runs across the session's benchmark periods = **8.0%** (reference ~9%); zero `context_overflow` occurrences in the session's `llm.jsonl` (grep clean) |

### Phase 4 threshold verdicts

| Threshold | Target | Measured | Verdict |
|---|---|---|---|
| Replay p50 within +15% of the 17.7s 3-way-concurrent reference | ≤ 20.4s | 9.65s (workers=3, `--patched`) | **PASS** (45.5% faster than reference) |
| Replay JSON-valid rate | 100% | 100% (all three replay runs, 40/40 each) | **PASS** |
| Replay thinking leak | 0% | 0% (all three replay runs) | **PASS** |
| Live-soak decision fallback rate (`bad_response`+`role_fallback`) not worse than pre-migration (~0 reference) | ≈0 | 0/36 (0%) | **PASS** (small sample — session had only run ~13 min at measurement time) |
| Live-soak `piano_module_drops` | ≤ ~9% | 8.0% (9 drops / 112 attempted) | **PASS** |
| Live-soak zero undetected truncations | 0 unrecovered `context_overflow` | 0 `context_overflow` occurrences found in the session's `llm.jsonl` | **PASS** (vacuously — none occurred to test recovery on) |

### Always-on PIANO Phase B gate (2026-07-25) — FAIL / rolled back

| Measure | Target | Final 7 s re-soak | Verdict |
|---|---|---|---|
| Decision p50 regression | <= +15% vs. 7,423 ms baseline | 8,568 ms (**+15.4%**) | **FAIL** |
| Median / worst note age | <=90 s / <=600 s | 94.1 s / 214.1 s | **FAIL** (median) |
| Pulse work | healthy empty/near-empty share | avg 2; no empty values | unfavorable |
| Refresh failures | <=~9% old-drop reference | 48 / 258 (**18.6%**) | **FAIL** |

The second latency miss closes the gate: `ALWAYS_ON_MODULES` is **OFF** and
`MODULE_PULSE_INTERVAL_S` is restored to its 45-second dark default. Phase C
was not attempted.

### Always-on PIANO Phase B gate attempt 2 — first treatment / one retune pending

| Run | Duration | Decisions | p50 / p90 | Errors / fallbacks | Module result |
|---|---:|---:|---:|---:|---|
| Control | 2700.9 s | 470 | 7,479 / 10,457 ms | 0 / 0 | `piano_module_drops` 117 / 1,325 (**8.83%**) |
| Treatment, 45 s pulse / batch 2 | 2700.5 s | 778 | 8,805 / 11,229 ms | 0 / 0 | `module_refresh_failures` 6 / 78 (**7.69%**) |

Treatment p50 is **+17.73%** over control, missing the +15% latency gate.
Its note-age median/max is **576.8 / 667.8 s**, missing the Attempt-2 median
freshness target (<=120 s) and exceeding the 600 s fossilization backstop at
the observed maximum. All 39 pulses dispatched 2 refreshes (average 2;
zero-work pulses 0), and sampled decision prompt reports were `none`.
Refresh-failure rate passed the ~9% reference, and decision errors/fallbacks
remained zero, but those do not offset the latency/freshness misses. The one
allowed retune is `MODULE_PULSE_MAX_BATCH = 1` with the 45-second interval
unchanged; re-soak before any further decision.

Caveat: the live-soak sample is small (36 decision calls, ~13 minutes) because
Phase 4 measured against whatever window the current session had accumulated
at run time, per the task's instruction to use "whatever window exists"
rather than waiting out a full 45-minute soak inline. The numbers are
internally consistent with the rest of this document's larger live
observations (e.g. the VRAM gate's burst test, the thinking-control
contract's live latency figures) and comfortably clear every threshold, but a
longer continuous soak would give a tighter confidence interval on the
fallback-rate and drop-rate figures specifically.

### Always-on PIANO Phase B gate attempt 2 — second treatment (batch 1 re-soak) / FINAL VERDICT

| Run | Duration | Decisions | p50 / p90 | Errors / fallbacks | Module result |
|---|---:|---:|---:|---:|---|
| Control (attempt 2) | 2700.9 s | 470 | 7,479 / 10,457 ms | 0 / 0 | `piano_module_drops` 117 / 1,325 (8.83%) |
| Treatment, 45 s pulse / batch 1 re-soak | 2700.8 s | 813 | 8,487 / 10,898 ms | 0 / 0 | `module_refresh_failures` 3 / 39 (7.69%) |

Treatment p50 is **+13.47%** over the attempt-2 control (8,487 ms vs. the
+15%/8,600.85 ms gate) — **latency PASSED**. Note-age median/max is **619.0 /
793.6 s**, failing both the <=120 s median freshness gate and the <=600 s
fossilization backstop (**FAIL**). All 39 pulses dispatched exactly 1 refresh
(`module_pulse_work` uniformly 1; zero-work pulses = 0), so the GPU-rest
signal also failed to appear (**FAIL**). Refresh-failure rate 3/39 = 7.69%,
within the ~9% reference (pass), and decision errors/fallbacks stayed at 0/0.

**FINAL VERDICT: gate FAILED after two treatment soaks.** Batch 2 (first
treatment) missed latency (+17.73%); batch 1 (this re-soak) fixes latency but
starves refresh throughput (one refresh per 45 s pulse cannot keep ~32 notes
fresh, producing 10-17-minute-stale notes) — missing both the median-freshness
and zero-work-pulse gates. No value of `MODULE_PULSE_MAX_BATCH` satisfies
latency and freshness simultaneously on this hardware: this is a single-GPU
throughput-vs-contention squeeze, not a mistuned constant. Per the retry
recipe, this is the second and last treatment — no third tuning attempt.
Rolled back: `ALWAYS_ON_MODULES = False`, `MODULE_PULSE_MAX_BATCH` restored to
2 (`MODULE_REFRESH_TIMEOUT_S = 60` and `MODULE_PULSE_INTERVAL_S = 45` are left
in place as inert attempt-2-start values, documenting the machinery while the
flag is dark). The always-on scheduler code remains intact and untouched
behind the flag. This feature is architecturally sound and stays dark;
revisit only with a second GPU or a materially faster/smaller `sim-fast`
model.

## CPU-offload probe (2026-07-25) — BLOCKED, NO-GO — SUPERSEDED

**SUPERSEDED (2026-07-25).** The diagnosis and verdict below were wrong: no
`OLLAMA_*` env var was actually "unset" on this box at the time of this
probe — `OLLAMA_MAX_LOADED_MODELS=2` (this repo's own Phase 1 config, set by
`scripts/ollama_setup.py`) WAS present in `HKCU:\Environment`, confirmed by
direct registry readback. The prior agent's shell simply didn't have it in
its inherited process environment when it checked, so it wrongly concluded
Ollama's untouched *default* cap (1) was in effect and that CPU/GPU models
share one slot-accounting pool as an inherent Ollama limitation. The real
mechanism: with the cap set to 2, loading a 3rd model (`probe-fast-cpu`,
CPU-pinned or not) evicted one of the two already-resident models exactly as
`OLLAMA_MAX_LOADED_MODELS=2` is documented to do — this was our own
configured cap doing its job, not a CPU/GPU pool-sharing limitation. The
factual observations below (num_gpu 0 works, CPU residency confirmed via
`ollama ps`/`size_vram: 0`, `sim-fast` was evicted) are correct and
preserved as-is; only the "why" and the NO-GO verdict are corrected. See the
new dated subsection below, "CPU-offload probe (2026-07-25, corrected retry)
— raised MAX_LOADED_MODELS to 3", for the re-run that raises the cap to
cover 3 truly-concurrent models and continues from where this attempt
stopped.

Probed per `docs/plan-cpu-offload-probe.md`: whether pinning a throwaway
`sim-fast`-equivalent model (`llama3.2:3b`, `PARAMETER num_gpu 0`, plus a
`num_thread 8` variant) to the CPU would decouple decision latency (GPU,
`sim-smart`) from module-refresh throughput (would-be CPU, `sim-fast`),
answering the "second GPU or smaller model" open question for a retry of
the always-on-modules Phase B gate on this box (i7-12700KF 12C/20T, 32 GB
RAM, RTX 3060 12 GB, Ollama 0.32.3).

**Blocker hit at probe step 1 (model creation + residency check), before
any throughput or tick-integrity measurement.** `ollama create probe-fast-cpu
-f <scratch Modelfile with PARAMETER num_gpu 0>` succeeded, and a warm
`/api/chat` call to it correctly reported `100% CPU` in `ollama ps`/
`size_vram: 0` — the CPU pin itself works as documented. But the same warm
call **evicted `sim-fast` from residency**: immediately after, `/api/ps`
listed only `probe-fast-cpu` (CPU) and `sim-smart` (GPU) — `sim-fast` was
gone, despite `keep_alive: -1` on all three models and despite
`probe-fast-cpu` needing zero VRAM (a purely CPU-side model competing with
GPU models for VRAM was the failure mode this probe was designed to avoid).
A follow-up direct call to `sim-fast` succeeded (it reloads on demand,
~246 ms `load_duration` for this small model) but did **not** restore it to
the resident set — the very next `/api/ps` still showed only `probe-fast-cpu`
+ `sim-smart`.

**Corrected diagnosis (was wrong at the time):** the prior write-up here
claimed "No `OLLAMA_MAX_LOADED_MODELS` (or other `OLLAMA_*`) env var is set
on this box" and concluded Ollama's *default* loaded-model cap (1) was in
effect, i.e. that CPU-resident and GPU-resident models count against the
same slot budget as an inherent Ollama behavior. That claim was checked
against the wrong environment: `HKCU:\Environment` was never actually read
directly during this attempt (the prior agent's shell simply lacked the
inherited env var, which is not the same as it being unset system-wide). A
direct registry read (`Get-ItemProperty HKCU:\Environment`, fresh process)
confirms `OLLAMA_MAX_LOADED_MODELS=2` WAS set the whole time — this repo's
own Phase 1 config from `scripts/ollama_setup.py`. With the cap at 2, a 3rd
model request (the CPU-pinned `probe-fast-cpu`) evicting one of the two
already-resident models (`sim-fast`) is exactly what `OLLAMA_MAX_LOADED_MODELS=2`
is documented to do — nothing here demonstrated that CPU and GPU models
share a pool as some special inherent limitation; it was our own configured
cap of 2 being hit by a 3rd model, regardless of that 3rd model's compute
target. This does NOT retroactively validate the plan's "stop, don't chase
`OLLAMA_MAX_LOADED_MODELS` tuning" instinct being followed here — following
the plan's stated timebox was the right call given the (mis-)diagnosis
available at the time, but the diagnosis itself was wrong, so the resulting
NO-GO verdict below does not stand. Steps 2-4 (thread-cap variant,
throughput measurement, 10-minute tick-integrity soak) were not run in this
attempt — see the corrected-retry subsection below for their completion.

Cleanup performed: `ollama rm probe-fast-cpu probe-fast-cpu8`; scratch
Modelfiles deleted from the agent scratchpad (never written under `ollama/`);
warmed `sim-fast` and `sim-smart` directly afterward and confirmed both
resident together again via `/api/ps`; confirmed the live sim server
untouched throughout (`GET /state` 200 before, during, and after; no control
routes called).

**Verdict (original, this attempt): NO-GO for CPU-offload as the third retry
option, on this specific Ollama 0.32.3 install/box — SUPERSEDED, see below.**
The mechanism this plan bet on — CPU inference as a separate compute pool
from the GPU decision path — does not hold here because Ollama's
loaded-model slot accounting evicts a GPU-resident model to make room for a
CPU-resident one, reintroducing the exact contention (now as reload-latency
spikes on `sim-fast`, which background cognition depends on) the probe was
meant to eliminate. This does not by itself rule out CPU offload on a
different Ollama version/config (e.g. one with `OLLAMA_MAX_LOADED_MODELS`
raised), but that tuning is out of this probe's scope and was left untouched
per the plan's constraints. The always-on Phase B retry options remain: a
second GPU, or a materially faster/smaller `sim-fast` model — unchanged from
the existing verdict above, now with this CPU-offload avenue closed on
evidence rather than assumption.

**This verdict is SUPERSEDED (2026-07-25).** The specific claim that "no
`OLLAMA_MAX_LOADED_MODELS` env var is set" was wrong — it was set to 2 by
this repo's own `scripts/ollama_setup.py`, and that IS part of why a 3rd
model evicted one of the two GPU-resident ones. But raising the cap to 3 and
confirming it genuinely active on the live process did **not** fix the
eviction — confirmed via a fresh, isolated retry below, the actual root
cause is VRAM headroom exhaustion (sim-smart+sim-fast alone leave only
~225–1,400 MiB free of 12,288 MiB), not the loaded-models count ceiling.
See "CPU-offload probe (2026-07-25, corrected retry) — raised
MAX_LOADED_MODELS to 3" below for the corrected diagnosis and the final,
still-NO-GO result.

## CPU-offload probe (2026-07-25, corrected retry) — raised MAX_LOADED_MODELS to 3

Continuation of the corrected-record note above, per
`docs/plan-cpu-offload-probe.md` steps 1–6 (steps proving `num_gpu 0` works
and CPU residency reports correctly were not redone — already proven in the
first attempt). Orchestrator-directed correction: a direct `HKCU:\Environment`
registry read confirmed `OLLAMA_MAX_LOADED_MODELS=2` (this repo's own Phase 1
config, `scripts/ollama_setup.py`) WAS set the whole time during the first
attempt — the eviction of `sim-fast` when a 3rd model loaded was that cap
being hit, not an "unset env var / Ollama default cap" as the first
write-up claimed.

### Step 1 — raise the cap, and a real bug found along the way

`scripts/ollama_setup.py`'s `ENV_VARS["OLLAMA_MAX_LOADED_MODELS"]` changed
`"2"` → `"3"` (plus docstring/comment updates at the top of the file). First
run of `uv run python scripts/ollama_setup.py` completed successfully and
`--check`/a fresh registry read confirmed `OLLAMA_MAX_LOADED_MODELS=3` — but
loading a 3rd model (`probe-fast-cpu`, see below) still evicted `sim-fast`
exactly as before. Root cause: `setx` writes the new value to
`HKCU\Environment` but does **not** update the already-running Python
process's own `os.environ`, and `restart_ollama()`'s
`subprocess.Popen([OLLAMA_APP_EXE])` had no `env=` argument, so the
relaunched Ollama process **inherited the stale (pre-setx) environment
block** from the still-running setup script, not a fresh registry read.
(Windows only re-reads `HKCU\Environment` into a new process's env when the
parent is Explorer/a shell launched via `ShellExecute`, e.g. PowerShell's
`Start-Process` — a child `Popen`'d directly from an already-running script
does not get this refresh.) **Fix applied** (in scope — a correctness bug in
the exact restart path this task's Step 1 depends on):
`restart_ollama()` now builds `child_env = dict(os.environ); child_env.update(ENV_VARS)`
and passes `env=child_env` to both `Popen` call sites, guaranteeing the
relaunched process sees the target values regardless of registry-propagation
timing. Re-ran `scripts/ollama_setup.py`; confirmed via new Ollama process
PIDs (`ollama` 29344→42924, `ollama app` 38600→43848 across the two runs)
that this was a genuinely fresh process, `/api/ps` showed sim-smart/sim-fast
dual-resident, and `GET /state` on the live sim server returned 200
throughout (sim server process itself was never restarted).

### Step 2 — the actual structural blocker: VRAM headroom, not model count

Created `probe-fast-cpu` (copy of `ollama/Modelfile.fast` + `PARAMETER num_gpu 0`,
scratch Modelfile in the agent scratchpad, not under `ollama/`). To rule out
any remaining doubt about whether `OLLAMA_MAX_LOADED_MODELS=3` was actually
reaching the live process, ran an isolated control: killed all Ollama
processes, launched `ollama serve` directly from this shell with
`OLLAMA_MAX_LOADED_MODELS=3` (and the other three vars) explicitly exported
inline — no dependency on `setx`/registry timing at all. Warmed sim-smart and
sim-fast (both resident, confirmed via `/api/ps`), then warmed
`probe-fast-cpu`: **`sim-fast` was evicted again** — `/api/ps` showed only
`probe-fast-cpu` (100% CPU, `size_vram: 0`) + `sim-smart`. Reloading
`sim-fast` immediately after then evicted `probe-fast-cpu` in turn — i.e. the
live server alternates between exactly 2 resident models no matter which
pair, even with a verified-active `OLLAMA_MAX_LOADED_MODELS=3` on the exact
process serving the request. `nvidia-smi` during this window: sim-smart +
sim-fast alone leave only **225–1,428 MiB free of 12,288 MiB total** (matches
the VRAM gate table earlier in this file: "Both resident, idle: 11,899 MiB"
used, ~389 MiB free) — Ollama's scheduler appears to require some minimum
VRAM staging/compute-buffer headroom to keep a 3rd model resident even when
that model's weights are fully CPU-offloaded (`num_gpu 0`), and this box does
not have that headroom once both required models (`sim-smart`, `sim-fast`)
are loaded. This is a genuinely different root cause than the first attempt's
(wrong) "no env var set" diagnosis, but it converges on the same practical
outcome: a 3rd model cannot stay resident alongside `sim-smart`+`sim-fast` on
this specific box.

Per `docs/plan-cpu-offload-probe.md`'s own timebox condition and this task's
explicit constraint ("If anything else structurally blocks... stop and
report — don't improvise further tuning"), stopped here. Steps 2 (thread-cap
variant), 3 (throughput measurement), and 4 (10-minute tick-integrity soak)
were **not run** — there is no value in measuring throughput for a
configuration that cannot stay resident under real concurrent load in the
first place, and chasing further VRAM tuning (e.g. `OLLAMA_KV_CACHE_TYPE=q8_0`,
lowering `sim-smart`'s `num_ctx`) is out of this probe's scope per the plan's
constraints ("do not touch `ollama/Modelfile.*`").

### Cleanup and verification

`ollama rm probe-fast-cpu` (no `probe-fast-cpu8` was ever created — the
thread-cap variant was never reached); both scratch Modelfiles deleted from
the agent scratchpad. Killed the manually-launched isolated `ollama serve`
process and restarted Ollama via the canonical `scripts/ollama_setup.py` to
restore the normal app-managed process; confirmed `sim-smart`/`sim-fast`
dual residency and `GET /state` 200 on the live sim server afterward.
`git status` shows only `ollama_config.md`, `TASKS_PENDING.md`, and
`scripts/ollama_setup.py` modified — no `simulation/` source files or
`ollama/Modelfile.*` touched.

### Verdict: NO-GO (confirmed, corrected root cause)

**NO-GO for CPU-offload as the always-on Phase B retry option, on this
specific box (i7-12700KF, 32 GB RAM, RTX 3060 12 GB, Ollama 0.32.3).** The
original NO-GO verdict's *reasoning* was wrong (an env var that was actually
set was reported as unset) and has been corrected above, but the *practical
conclusion stands*: a 3rd model cannot be kept resident alongside
`sim-smart`+`sim-fast` on this hardware, now confirmed to be a VRAM-headroom
constraint (only ~225–1,400 MiB free with both required models loaded, not
enough for any 3rd model's minimum footprint) rather than a
`OLLAMA_MAX_LOADED_MODELS` misconfiguration — raising the cap to 3 (verified
genuinely active on the live process via an isolated, registry-independent
test) did not change the outcome. This closes the loaded-models-count
avenue specifically; it does not test whether freeing VRAM first (e.g. a
smaller `sim-smart` quant, reduced `num_ctx`, or `OLLAMA_KV_CACHE_TYPE=q8_0`)
would create enough headroom for a 3rd resident model, but that tuning is
out of this probe's scope. The always-on Phase B retry options remain
unchanged: a second GPU, or a materially faster/smaller `sim-fast` model.
`OLLAMA_MAX_LOADED_MODELS=3` and the `restart_ollama()` env-propagation fix
are kept as the new shipped baseline regardless of this NO-GO — they are
correct, general-purpose fixes (a real config bump plus a real restart-path
bug fix) independent of the specific CPU-offload question, and do no harm at
2-model dual residency (verified: `sim-smart`+`sim-fast` resident and stable
after every restart in this session).

## Smaller `sim-fast` quality screen (2026-07-25) — NO-GO

Phase 0 used 12 prompts: each of the four real `MODULE_PROMPTS` system texts
from `simulation/server.py`, paired with three realistic synthetic contexts.
All candidates used the server module sampling settings: temperature 0.5,
`top_p` 0.8, `top_k` 20, `min_p` 0, and `num_predict` 60.

This was a **manual qualitative review**, not a scored benchmark. The
case-level defects retained from the 12-case side-by-side are below;
`module-N` identifies the real module prompt and its synthetic context.

| Model | Exact failed cases | Screening category |
| --- | --- | --- |
| `sim-fast` / `llama3.2:3b` baseline | `social-1` reversed Toma's wood request; `perception-2` reached the 60-token limit; `social-2` reached the limit and incorrectly requested extra stone; `social-3` invented Kael; `reflection-3` invented a 25% metric | Grounding and clean/coherent-format defects; imperfect, but materially more grounded overall |
| `llama3.2:1b` | `perception-1` reached the 60-token limit on a repeat; `social-1` recommended coordinating with Mara herself; `desire-1` proposed a bridge with 3 wheat/1 wood; `desire-2` proposed a bridge with 5 wood/1 stone; `reflection-2` invented a wood-5/stone-8 stockpile target; `social-3` invented Kaito | Coherent single-sentence/clean failure for truncation; multiple grounding failures and self-coordination |
| `smollm2:1.7b` | `perception-1` reached the 60-token limit; `social-1` self-coordinated Mara and asked Toma for wood despite his request; `desire-1` combined harvest and bridge goals; `reflection-1` changed Toma's two wood to two logs; `desire-2` said to construct the bridge before Nia delivered the needed wood; `social-3` invented Rael | Coherent single-sentence/clean failure for truncation; grounding, self-coordination, and multi-goal failures |

**Verdict: STOP / NO-GO.** Both candidates failed qualitative review due to
multiple material factual and formatting regressions. The plan's relative
pass-count threshold could not be established with confidence because the
baseline itself has defects and is a poor numeric comparator. `sim-fast`
therefore remains unchanged and the always-on retry stays hardware-blocked.
Do not change `ollama/Modelfile.fast`, setup, specifications, or
`ALWAYS_ON_MODULES` from this plan. Phases 1–3 are not authorized; a second
GPU is the remaining stated lever.

## Related sim knobs (not Ollama)

These live in code; Phase 2 has landed, so they now target Ollama:

- `MAX_CONCURRENT_LLM = 3` — `simulation/sim_engine.py`
- `DISABLE_THINKING_ROUTINE`, `THINKING_ENABLED_HIGH_STAKES`,
  `HIGH_STAKES_MAX_TOKENS`, `NON_THINKING_SAMPLING` / `THINKING_SAMPLING`,
  `ROUTINE_PRESENCE_PENALTY` — `simulation/server.py`
- `INVENTION_MAX_TOKENS = 1024`, `INVENTION_TEMPERATURE = 0.6` —
  `simulation/server.py`
- `OLLAMA_CHAT_URL` / `is_context_overflow_error` — `simulation/server.py`
  (successor to the former `LM_STUDIO_URL` / error-string-sniffing call
  sites; see the plan's Phase 2 for the cutover this replaced)

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
  user's to delete or keep, agents do not delete it. **Verified (Phase 5,
  2026-07-24): `sim-smart` does NOT depend on this path at runtime.**
  `ollama create` copies the GGUF into Ollama's own blob store
  (`%USERPROFILE%\.ollama\models\blobs\sha256-...`) at create time —
  confirmed live via `ollama show sim-smart --modelfile`, whose `FROM` line
  resolves to a blob path, not `.lmstudio`. The cache directory is only
  needed again if `ollama create sim-smart -f ollama/Modelfile.smart` is
  ever re-run (e.g. after `ollama rm sim-smart`); `ollama/Modelfile.smart`
  now carries a commented registry-pull alternative
  (`ollama pull hf.co/lmstudio-community/Qwen3.5-9B-GGUF:Q4_K_M`) for that
  case if the user has since deleted the `.lmstudio` cache.
