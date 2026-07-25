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

Caveat: the live-soak sample is small (36 decision calls, ~13 minutes) because
Phase 4 measured against whatever window the current session had accumulated
at run time, per the task's instruction to use "whatever window exists"
rather than waiting out a full 45-minute soak inline. The numbers are
internally consistent with the rest of this document's larger live
observations (e.g. the VRAM gate's burst test, the thinking-control
contract's live latency figures) and comfortably clear every threshold, but a
longer continuous soak would give a tighter confidence interval on the
fallback-rate and drop-rate figures specifically.

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
