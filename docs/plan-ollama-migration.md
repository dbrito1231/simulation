# Plan: full switchover from LM Studio to Ollama

Status: proposed (2026-07-24). Owner: orchestrator + subagents per
[CLAUDE.md](../CLAUDE.md#model-policy). Plan only — no implementation here.

Decision context: LM Studio cannot set a system prompt at model load time on
any rung (verified twice, plus a request-time preset probe that proved
replace-not-concatenate semantics — see TASKS_PENDING.md item 2b and
lms_config.md). Ollama's Modelfile `SYSTEM` directive is exactly that missing
mechanism, and Ollama also runs two resident models natively
(`OLLAMA_MAX_LOADED_MODELS`), which the repo's `MODEL_SMART`/`MODEL_FAST`
routing was designed for but never got.

**LM Studio is permanently unavailable (user decision, 2026-07-24).**
Verified at planning time: `:1234` is dead, the sim server is down with it,
Ollama 0.32.3 is live at `http://localhost:11434` with zero models pulled.
Consequences the plan is built around: (a) **there is no rollback runtime** —
git history and the recorded benchmark numbers in `lms_config.md` are the
only baseline; (b) **the sim is offline until Phase 2 completes**, so
Phases 0–2 are the critical path and run back-to-back; (c) performance
comparisons in Phases 3–4 are against recorded historical numbers, not
re-runnable controls. Hardware: RTX 3060 12 GB (~11 GB free). LM Studio's
GGUF cache is still on disk under `%USERPROFILE%\.lmstudio\models\` — the
app is gone but the model files import cleanly via Modelfile `FROM <path>`,
saving a ~7 GB download; registry pull is the fallback if the import path
proves brittle.

**Guiding principle (carried from TASKS_PENDING.md):** the LLM keeps full,
explicit visibility. A Modelfile `SYSTEM` block is the same rule text,
version-controlled and human-editable, relocated for efficiency — compliant.
Nothing in this plan filters what the model sees or bakes behavior into
weights.

## Acceptance mapping

| Requirement | Where satisfied |
| --- | --- |
| Ollama replaces LM Studio | Phases 1–2 |
| Remove all traces of LM Studio | Phase 5 |
| Configure Ollama for this repo's needs | Phase 1 (env + Modelfiles + loader script) |
| Performance not drastically degraded | Phase 4 gates (thresholds below) + Phase 0/1 VRAM gates |
| Spec files updated | every phase (SDD, same-change rule) + Phase 5 sweep |
| Features LM Studio couldn't do | Phase 6 (Modelfile SYSTEM rulebook offload = TASKS_PENDING 2b revival; KV-cache quant env) |
| Two distinct models as designed | Phase 3 (`MODEL_FAST` ≠ `MODEL_SMART`, enforced by a startup assert) |

## Model assignments (Claude, per CLAUDE.md policy)

| Work | Agent | Model |
| --- | --- | --- |
| Phase 0 capability probes (read-only + throwaway API calls) | general-purpose | Sonnet 5 |
| All code changes (Phases 1–3, 5, 6) | implementer | Sonnet 5 |
| Soak/bench log analysis (Phases 3, 4, 6) | general-purpose | Sonnet 5 |
| Doc-only sweeps that follow reviewed diffs | implementer | Haiku 4.5 |

Orchestrator (any tier) plans, dispatches, reviews; writes no implementation
code. One implementer dispatch per phase unless files are disjoint.

**Usage-limit rule (binding, per [CLAUDE.md](../CLAUDE.md#model-policy)):**
if a usage limit is hit while any phase is mid-implementation, **all
implementation pauses immediately** — no continuing, no retrying, no
switching tiers to push through, and no new subagent dispatch until the
limit clears. On resume, first review the in-flight change's state (diff +
smoke result of the last completed step) before dispatching anything new.
This matters doubly here: Phases 0–2 are the critical path with the sim
offline, and the temptation will be to push through a limit to restore
service — don't; a half-applied cutover in server.py is strictly worse than
a paused one, because the smokes and the session log are the only safety
net this migration has.

## Sim-model choices (the two Ollama models)

- **`MODEL_SMART` = `qwen3.5:9b`** (or GGUF-imported equivalent of the
  current `qwen/qwen3.5-9b`): keeps the proven decision model — every prompt
  contract, sampling setting, and quality baseline in this repo was measured
  against it. Migration risk stays isolated to the runtime, not the model.
- **`MODEL_FAST` = a distinct ~3–4 B instruct model** (candidates, in order:
  a Qwen3.5 ~4 B tier if published in the Ollama library; else
  `llama3.2:3b`, whose GGUF is already in the LM Studio cache). Serves PIANO
  modules, memory summarizer/wiki merge, meta system, belief-pitch scoring —
  all one-sentence/one-number outputs. Phase 0 gates its quality; Phase 3
  gates it live. `qwen3.5-0.8b` is out (rejected in prior repo evaluations as
  too weak).
- VRAM budget on 12 GB: 9 B Q4 ≈ 6.5 GB (num_ctx 20k, KV offloaded) + 3–4 B
  Q4 ≈ 2–2.5 GB (num_ctx 4k — module prompts are ~1k tokens) + KV. Estimated
  fit is tight but plausible; Phase 1 measures it before anything depends on
  it. Fallback if it doesn't fit: `OLLAMA_KV_CACHE_TYPE=q8_0` first, smaller
  fast-model quant second, single-model mode (both constants → 9 B, dual
  residency deferred) as the documented last resort — that last state fails
  the two-model MUST, so it is a stop-and-report condition, not a silent
  fallback.

---

## Phase 0 — capability verification (gate for everything)

Read-only probes + throwaway API calls; no repo changes except recording
results. Verify on the installed 0.32.3, not from docs alone:

1. Native `/api/chat`: `options` honors `temperature`, `top_p`, `top_k`,
   `min_p`, `num_predict`; `format` accepts a full JSON schema (the repo's
   `DECISION_SCHEMA`) and constrains output; `stream: false` returns one
   body. The OpenAI-compat endpoint is the fallback rung only if native
   lacks something — decide and record which endpoint the port targets.
2. Thinking control for Qwen3.5 on Ollama: does `think: false` (or the
   model's template) suppress reasoning output? The repo's contract is
   routine turns emit JSON directly (`DISABLE_THINKING_ROUTINE`,
   lms_config.md "Thinking control"). If thinking cannot be suppressed,
   that is a **stop condition** — the 57%-bad-response epidemic documented
   in lms_config.md would return.
3. Context/truncation semantics: Ollama **silently truncates** input beyond
   `num_ctx` rather than erroring. Confirm `prompt_eval_count` in responses
   is usable to detect truncation (sent-tokens estimate vs evaluated).
   This replaces LM Studio's `context_overflow` error contract and Phase 2
   must re-implement overflow detection on top of it.
4. Modelfile `SYSTEM` semantics: request WITHOUT a system message gets the
   Modelfile SYSTEM applied; request WITH one overrides it. Confirm both
   directions with a ZORPCONFIRM-style probe (same method as the preset
   experiment). This gates Phase 6.
5. Concurrency: `OLLAMA_NUM_PARALLEL=3` on the smart model + parallel
   requests to the fast model — confirm both models serve simultaneously
   under `OLLAMA_MAX_LOADED_MODELS=2` without evicting each other.
6. GGUF reuse: confirm `ollama create` with `FROM <lmstudio gguf path>`
   imports the existing qwen3.5-9b file (saves a ~7 GB download; if the
   import path is brittle, pulling from the registry is the fallback).
7. Fast-model quality screen: run 10 representative PIANO-module prompts
   (pull real ones from a recent `lm_studio.jsonl`) against the candidate
   3–4 B model; a human-readable one-sentence report per prompt passes.

Deliverable: findings table appended to the new `ollama_config.md` (created
Phase 1; findings go in the plan-tracking section of TASKS_PENDING.md until
then). Any stop condition → report it, then continue to the best achievable
cutover per the rollout section (the sim must not stay offline); the failed
MUST is surfaced, never silently waived.

## Phase 1 — provisioning and configuration

Files: new `Modelfile.smart`, `Modelfile.fast` (repo root or `ollama/` dir),
new `scripts/ollama_setup.py`, new `ollama_config.md`; spec
`specs/03-cognition.md`.

1. **Modelfiles, version-controlled.** `Modelfile.smart`: `FROM` the 9 B
   GGUF, `PARAMETER num_ctx 20480`, sampling params matching
   `NON_THINKING_SAMPLING` defaults. `Modelfile.fast`: `FROM` the 3–4 B
   model, `num_ctx 4096`. No `SYSTEM` directive yet — that is Phase 6,
   gated. `ollama create sim-smart -f Modelfile.smart`, `sim-fast` likewise.
2. **Server environment.** `OLLAMA_NUM_PARALLEL=3`,
   `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_FLASH_ATTENTION=1`,
   `OLLAMA_KEEP_ALIVE=-1` (both models stay resident; the sim runs 24/7).
   On Windows these are user environment variables + an Ollama service
   restart — `scripts/ollama_setup.py` sets them (setx), restarts Ollama,
   creates/updates both models, then verifies via `/api/ps` the way
   `lms_load.py --check` did. This script is the canonical loader,
   replacing `scripts/lms_load.py`'s role.
3. **`ollama_config.md`** — successor to `lms_config.md`, same structure:
   required settings table, wrong-config table (as measured), restore
   procedure (`uv run python scripts/ollama_setup.py`), expected
   `ollama ps` output, thinking-control contract, benchmark reference
   numbers (filled by Phase 4).
4. **VRAM gate:** with both models resident, drive 3 concurrent smart
   requests + 2 fast requests (mirrors `MAX_CONCURRENT_LLM=3` +
   `PIANO_CONCURRENT_LLM=2`); record `nvidia-smi` peak and confirm no
   eviction/offload thrash in `ollama ps`. Fallback ladder from the model
   section applies.

## Phase 2 — server cutover

Files: `simulation/server.py` (all LM Studio call sites), `simulation/
sim_engine.py` (only if error-string constants leak there); specs 03.

1. Replace `LM_STUDIO_URL` with `OLLAMA_URL` targeting the endpoint chosen
   in Phase 0; one request-builder function converts the existing payload
   shape (messages, sampling, max_tokens, response_format/schema, thinking
   flag) to Ollama's. Everything flows through `run_agent_decision` /
   `lm_complete` already — the choke points are known.
2. Map the contracts: `response_format` json_schema → `format`;
   `reasoning_effort:"none"` → the Phase 0-verified thinking suppression;
   `MODEL_SMART`/`MODEL_FAST` → `sim-smart`/`sim-fast`; timeouts unchanged.
3. **Truncation detection** replaces `is_context_overflow_error`: estimate
   sent tokens (chars/4 heuristic is enough), compare `prompt_eval_count`,
   and on detected truncation fire the existing slim-retry path and log
   `context_overflow` to the session log exactly as today — downstream
   tooling (benchmarks, specs) keeps its vocabulary.
4. `"LM Studio offline"` error string → `"llm offline"` (grep for every
   consumer first — engine fallback paths match on it).
5. Prefix-cache posture carries over unchanged (Ollama is llama.cpp-based
   and reuses prompt prefixes per slot): the Phase 2a hardening (stable
   system string, sha256 logs) stays as-is.
6. Offline verification: both smokes (they stub the LLM). Live: start the
   server, watch one full think round-trip per agent in the session log,
   confirm `content` populated / no thinking leak / decisions applied.

## Phase 3 — two-model split, live

Config-level change (`MODEL_FAST = "sim-fast"`) + a startup assert
`MODEL_FAST != MODEL_SMART` (the repo's routing predicate finally does real
work; the assert enforces the two-model MUST permanently).

Gate — 45-minute soak vs. the last single-model session: `piano_module_drops`
rate not worse than the current ~9% reference; module latencies expected to
*improve* (a 3–4 B model answering module one-liners instead of queueing
behind the 9 B); memory-maintenance/meta outputs spot-read for coherence
(the summarizer moves to the fast model — quality screen from Phase 0 gets
its live confirmation). Regression → revert `MODEL_FAST` to `sim-smart`,
record findings, keep the rest of the migration (two-model MUST then needs a
different fast-model candidate, not abandonment).

## Phase 4 — performance validation (the drastic-degradation gate)

Port `scripts/llm_replay_bench.py` to the Ollama endpoint (it replays logged
decision calls). Baselines are already recorded in lms_config.md (2026-07-11
reference: patched median 12.1 s @ 13000/2; 17.7 s @ 20000/3-way-concurrent).

Acceptance thresholds (all must hold):
- Replay p50 within **+15%** of the LM Studio 3-way-concurrent reference on
  the same replayed calls; JSON-valid rate 100%; thinking leak 0%.
- 45-min live soak: decision fallback rate (`bad_response` +
  `role_fallback`) not worse than the current session's; `piano_module_drops`
  per Phase 3 gate; **zero undetected truncations** (every truncation event
  must have fired the slim retry).
- Results written into `ollama_config.md`'s benchmark table.

Failure → tune (num_ctx, parallel, KV quant) and re-run once; a second
failure is a stop-and-report with findings — but the sim **stays on Ollama**
(there is nothing to roll back to), running at whatever performance was
achieved, while the tuning findings drive the next attempt. The thresholds
gate Phase 6's optimizations, not the sim's continued operation.

## Phase 5 — remove all traces of LM Studio

Runs any time after Phase 2 is verified (with LM Studio permanently gone
there is no rollback to preserve; keeping references only invites a future
session to chase a dead runtime). Sweep targets (grep
`-i "lm.studio\|lms\b\|1234"` across the repo, excluding `docs/archive/`
which stays untouched per policy):

1. Delete `scripts/lms_load.py`; delete `lms_config.md` (its still-relevant
   content — thinking history, wrong-config lessons — already migrated into
   `ollama_config.md` in Phase 1; git history preserves the rest).
2. `SessionLogger`: rename the `lm_studio.jsonl` stream to `llm.jsonl`;
   update every reader (`llm_replay_bench.py`, smokes if any, specs 03/04,
   CLAUDE.md Logs section, HANDOFF.md).
3. CLAUDE.md: Commands section (LM Studio bullet → Ollama equivalent:
   service expected at `:11434`, `scripts/ollama_setup.py` as the loader),
   Critical invariants (context-formula line now cites ollama_config.md),
   Logs section filename.
4. specs/01/03/04 + any other spec naming LM Studio: full sweep, same
   change. TASKS_PENDING.md: item 2b's gate notes get a closing line —
   superseded by the Ollama migration; the LM Studio findings remain as
   history.
5. `simulation/logs/lm_studio_server.log` note in CLAUDE.md → Ollama's own
   log location (`%LOCALAPPDATA%\Ollama\server.log`).
6. The LM Studio application is already unavailable (user handled it);
   once Phase 1 confirms the GGUF import succeeded, the leftover
   `%USERPROFILE%\.lmstudio\` cache directory is the user's to delete or
   keep — flag it in the Phase 5 report, agents do not delete it.

## Phase 6 — Ollama-only features (what LM Studio blocked)

1. **Rulebook to load time — TASKS_PENDING item 2b, revived.** Add `SYSTEM
   <SYSTEM_PROMPT text>` to `Modelfile.smart`, generated (never hand-copied)
   by `scripts/ollama_setup.py` from server.py's `SYSTEM_PROMPT` constant so
   the single-source-of-truth rule holds; `ollama create` re-bakes it in
   seconds, keeping rules instantly editable. New server flag
   `SYSTEM_PROMPT_AT_LOAD_TIME = False`: when flipped, routine decision
   turns omit the system message (Modelfile SYSTEM applies, per the Phase 0
   probe); slim-retry/invention/sprite turns keep sending their own (they
   need different prompts, and an explicit system message overrides the
   baked one — verified semantics, not assumed). Gate exactly as the old 2b
   specified: A/B soak on fallback rate + action distribution before the
   flag flips; the "model forgets a distant system prompt" risk is the
   tripwire. Payoff if it holds: ~3 k tokens off every routine turn — the
   `context_overflow` class disappears and per-turn prompt processing drops
   by roughly half.
2. **KV-cache quantization**, which LM Studio could only set via an SDK
   that couldn't set parallel slots: `OLLAMA_KV_CACHE_TYPE=q8_0` measured
   as a Phase 4-style replay comparison; keep only if latency-neutral.
3. Update TASKS_PENDING item 2/2b status to reflect the new state when the
   flag flips.

## Branching (mandatory)

All migration work happens on a dedicated branch — `ollama-migration` — cut
from `main` before Phase 0 records anything in the repo. No phase commits
land on `main` directly. Rationale: with no LM Studio runtime to fall back
to, git is the only rollback this migration has, and a clean branch keeps
`main` at the last-known-good LM Studio state until the cutover is proven.
Merge to `main` only after Phase 4's gate has run (pass or documented
miss) — i.e. the sim is live on Ollama with measured numbers — and the user
approves the merge. Commit per phase (one reviewable commit per phase
completion, same style as the repo's existing phase commits), so a failed
phase can be reverted without unwinding its predecessors.

## Rollout order and stop conditions

0 → 1 → 2 (critical path, back-to-back — the sim is offline until 2 lands)
→ 3 → 4 → 5 → 6, each phase verified before the next; Phase 5 may run any
time after Phase 2. Stop conditions: thinking unsuppressible (Phase 0) and
two-models-don't-fit-VRAM after the fallback ladder (Phase 1) halt the
*optimization* path with findings in TASKS_PENDING.md — but because there
is no LM Studio to return to, a Phase 0/1 stop still ends with the best
achievable single-model Ollama cutover (Phase 2 with both constants on the
9 B) so the sim is not left offline; the unmet MUSTs are reported
explicitly rather than silently waived. A Phase 4 threshold miss never
takes the sim down — see Phase 4.
