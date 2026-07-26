# Tasks pending

Running list of follow-up work identified during conversations with agents.
Not a plan itself — each item should get its own `docs/plan-*.md` before
implementation, per this repo's SDD conventions in [CLAUDE.md](CLAUDE.md).

**Guiding principle (2026-07-24):** the LLM stays the main orchestrator with
full, honest visibility into information — the engine's job is to pass
information in and out *efficiently*, never to pre-filter or hide what the
LLM gets to see/reason over, and never to bake judgment calls into opaque
weights in place of explicit, visible rules. Efficiency fixes (caching,
relocating identical content, reducing redundant transmission) are in scope;
anything that decides on the LLM's behalf what's relevant, or encodes rule
behavior implicitly instead of as reasoned-over text, is not. Ideas rejected under this principle (recorded so they aren't re-proposed):
- Deterministic RAG-style rule filtering (only ship rule sections whose
  action is in `available_actions`) — technically sound, but it moves a
  relevance decision off the LLM and onto engine heuristics.
- Fine-tune / LoRA adapter baking the rules into model weights (was a 2b
  variant) — strongest token savings, but rules become implicit trained-in
  behavior instead of explicit text the LLM visibly reasons over, and
  changing a rule means re-baking the adapter.

## 1. MemoryStore (semantic recall) doesn't survive a server restart

**Status: done (2026-07-24), see [docs/plan-tasks-pending-rollout.md](docs/plan-tasks-pending-rollout.md) Phase 1.**
`MemoryStore` is now constructed against the restart-stable
`simulation/memory_store.json`, loads on init (tolerating absent/corrupt
files), mirrors flushes into the per-session `memory.json` for inspection,
logs a startup line + `memory_store_loaded` benchmark, and is cleared (with
a stable-path flush) on `/control/reset`. See specs/03-cognition.md.

`agent["memory"]` (working/shortTerm/longTerm) persists via `state.db` and
survives restarts — this was confirmed working as part of
[docs/plan-cross-module-visibility.md](docs/plan-cross-module-visibility.md)
Phase B. But the separate `MemoryStore` embedding index (server.py) writes to
`memory.json` inside the per-session `simulation/logs/<timestamp>/` folder, so
a **new session directory means a fresh, empty semantic-recall index** — every
restart silently discards it, with no log line or benchmark flagging the loss.

Candidate fix: apply the same pattern as Phase B — mirror/rehydrate the
vector index against something restart-stable (e.g. a fixed path under
`simulation/`, or piggyback on `state.db`) instead of the per-session log
directory, so `memory_store.query()` recall survives restarts the same way
the tiered per-agent memory already does.

Needs its own plan doc before implementation.

## 2. Stop re-paying for the static prompt every decision turn

Today every routine decision turn re-sends the full `SYSTEM_PROMPT`
([server.py:963](simulation/server.py:963)) — ~26 rules + object schemas +
worked examples — even though any given agent uses only a couple of them. This
fixed cost is the main driver of the rare `context_overflow` events and a
large share of per-turn tokens/latency. Goal: pay for the static rules once,
not on every turn. Two solutions below, to be done in order (1 first — cheap,
safe, already half-built; 2 second — the real overflow fix).

### 2a. Solution 1 — prefix caching (RECOMMENDED starting point)

**Status: audit+harden done (2026-07-24), see
[docs/plan-tasks-pending-rollout.md](docs/plan-tasks-pending-rollout.md)
Phase 2.** Audit found no dynamic leak: `build_decision_payload` always uses
one of four static module-level prompt constants for the system message;
per-agent/per-tick data only ever renders into the user message. The one
`SYSTEM_PROMPT` reassignment (TECH_TREE_ENABLED tier-field rewrite) runs once
at module import, before any request is served, so it cannot invalidate the
prefix mid-session. Hardening added: startup `[server] system prompt
sha256=...` log, a rebuild-time log at the same site, and a log-once
mid-session mismatch guard (`_check_system_prompt_stability`) — see
specs/03-cognition.md. The 30-min before/after latency soak (item 4 of the
Phase 2 plan) is still outstanding.

Make the static block (`SYSTEM_PROMPT` + rules) a byte-for-byte identical
prefix at the very front of every decision payload, so LM Studio reuses its
KV cache by longest-common-prefix and never re-computes the rules. The repo
already relies on this — [build_decision_payload](simulation/server.py:3312)
deliberately puts the persona at the TOP OF THE USER MESSAGE (not appended to
the system prompt) precisely to keep the system prefix stable (see the note at
~server.py:3328). This task is to *audit and harden* that: confirm nothing
dynamic leaks into the system prefix, keep the prompt string stable across
turns, and verify cache reuse actually fires (watch `latency_ms` in
`lm_studio.jsonl`). Helps compute/latency; does NOT reduce raw token count.

Low risk, no behavior change. Verify via a soak comparing p50 decision
latency before/after.

### 2b. Solution 2 — move the rules to model load time (the real overflow fix)

Move the static rulebook out of the per-turn request entirely so each turn
carries only dynamic situation data:

- **LM Studio default system prompt / model preset** — set the rules as the
  model's built-in default at load time (via `scripts/lms_load.py` config or a
  preset), so the server sends only the dynamic user prompt. Rules stay
  editable (change preset + restart), no re-baking. **Use the `lms` CLI to set
  this**, not the GUI — `scripts/lms_load.py` already establishes the
  CLI-as-canonical-loader convention for this repo (REST rung first, `lms
  load` CLI as the fallback rung for anything REST can't configure, e.g.
  context/parallel today). Extend that same script/rung structure to also push
  the default system prompt / preset at load time, so the whole model
  configuration — context, parallel, flash attention, KV quant, *and* the
  baked-in rules — stays defined in one versioned, repeatable CLI-driven
  loader instead of a GUI setting nobody remembers to reapply after a model
  reload. Document the exact `lms` invocation in `lms_config.md` alongside the
  existing loader notes.
This genuinely removes the biggest chunk of every prompt, so `context_overflow`
should largely disappear and per-turn tokens/latency drop materially.

**Risk / watch-outs:** must confirm the model actually honors a load-time
default system prompt as well as it does inline rules (small local models can
"forget" a default system prompt under a long user message) — A/B the decision
quality (fallback rate, action distribution) against the current inline-rules
baseline before committing.

Both 2a and 2b need their own plan doc(s) before implementation. Keep the
existing `SYSTEM_PROMPT` as the source of truth for the rule text regardless of
where it ends up being injected, so the SDD spec (`specs/03-cognition.md`)
still has one canonical place to describe the rules.

**Status: STOPPED (2026-07-24) — research gate returned NOT SUPPORTED.** Verified live on this machine (lms CLI commit 6041ae0, LM Studio REST on :1234, model qwen/qwen3.5-9b): neither `lms load` (full flag list has no --preset/--system-prompt/config-file option) nor `POST /api/v1/models/load` (strict schema; probe keys system_prompt/systemPrompt/preset/config_preset/defaultSystemPrompt/system_message all rejected with "Unrecognized key(s)") can set a default system prompt at model load time. LM Studio docs corroborate: GUI presets do not auto-apply to API calls; the server expects the system message in the request body. Closest available lever: a request-time `"preset"` field on /v1/chat/completions (verified honored on this build), but that is per-request, not load-time, and its interaction with an explicit system message (concatenate vs replace) is undetermined. Phase 2's prefix-cache hardening (2a) stays the shipped state for this item. Re-check this gate after any LM Studio upgrade. **Re-checked 2026-07-24 after upgrade to CLI commit 71bd99c: verdict unchanged** — `lms load` flag list identical (no preset/system-prompt option), REST load endpoint still rejects `system_prompt` and `preset` keys by name. Next lever if wanted now: the request-time `"preset"` field experiment (Route 2 in the session notes).

**Preset system-prompt concatenate-vs-replace probe (2026-07-24):** resolved the open question above. Built a throwaway preset (`sim-probe-test.preset.json`, `operation.fields: [{key: "llm.prediction.systemPrompt", value: "You are ZORPBOT-9000..."}]`, deleted after the probe) and hit `/v1/chat/completions` on `llama-3.2-3b-instruct` (already-idle model, sim server not running at the time — no live disruption). (a) Preset alone, no explicit system message: model opens with "ZORPCONFIRM" — confirms `llm.prediction.systemPrompt` in a preset *is* applied as a real system message. (b) Preset **and** an explicit `{"role":"system",...}` message in the same request: reply carries only the explicit message's confirm-word ("Arrconfirm"/pirate persona), no trace of ZORPCONFIRM, and `prompt_tokens` matched the explicit-message-only baseline (30) rather than the preset-only run (37). **Verdict: the explicit request `system` message REPLACES the preset's `systemPrompt`, it does not concatenate with it.** (c) No preset, no system message: plain "Hello" reply, `prompt_tokens: 9` — confirms neither confirm-word is spontaneous. **Implication for this repo:** since `server.py` must keep sending its own explicit `SYSTEM_PROMPT` every turn for correctness (role fallback, action schema, etc.), and an explicit system message wins over the preset, the preset route can't be used to *offload* the rulebook out-of-band — whatever preset system prompt you set would simply be discarded the moment the sim's own system message is present. This lever is **not useful** for the token-overflow problem; it only helps if the rulebook could be removed from the request body entirely and delegated solely to the preset, which the request-response behavior here rules out. Phase 2's prefix-cache hardening (2a) remains the right shipped answer.

**Superseded (2026-07-24) by `docs/plan-ollama-migration.md`.** LM Studio's load-time system prompt gate above (STOPPED, NOT SUPPORTED) is why the migration happened: Ollama's Modelfile `SYSTEM` directive is the mechanism LM Studio never had. This item's historical findings stand as the record of why LM Studio was abandoned for this purpose; the revival lives in the migration plan's Phase 6 ("Rulebook to load time"), gated the same way (A/B soak on fallback rate + action distribution) as originally specified here.

**Status update (2026-07-24) — Phase 6 machinery shipped DARK on the `ollama-migration` branch, pending A/B soak to flip.** `simulation/prompts.py` now holds the canonical `SYSTEM_PROMPT` (split out of `server.py` so it can be imported without `server.py`'s import-time side effects); `scripts/ollama_setup.py --with-system` generates `ollama/Modelfile.smart.system` from it and creates a SEPARATE `sim-smart-sys` Ollama model (verified live: baked `SYSTEM` block reproduces `SYSTEM_PROMPT` byte-for-byte via `ollama show sim-smart-sys --system`; `/api/ps` confirmed the live `sim-smart`/`sim-fast` models were untouched by the create). `SYSTEM_PROMPT_AT_LOAD_TIME = False` in `server.py` gates the actual behavior change (omit system message + route to `sim-smart-sys` on routine/high-stakes decision turns only — slim-retry/invention/sprite turns keep sending their explicit prompts on `sim-smart`, unaffected). Nothing flips until the A/B soak (fallback rate + action distribution vs flag-off) specified above passes — see `specs/03-cognition.md` "Load-time rulebook" and `ollama_config.md` "Load-time rulebook (dark)" for the full design and flip procedure.

## 3. Agent long-term memory decays instead of compounding (LLM-Wiki pattern)

**Status: implemented behind `WIKI_MEMORY = False` (2026-07-24), see
[docs/plan-tasks-pending-rollout.md](docs/plan-tasks-pending-rollout.md)
Phase 4 — soak pending.** `agent["memoryWiki"]` (`relationships`, `goals`,
`lessons`, each capped at `WIKI_SECTION_CHAR_CAP=300`) now merges/reconciles
via a single call that replaces (not adds to) `_run_memory_maintenance`'s
existing one-call-per-pass slot — no new LLM call site or cadence.
Contradiction resolution rides the same prompt/response (logged to
`activity.jsonl`, zero extra calls); scaffold-text validation reuses
`is_scaffold_text`; `_memory_for_prompt` surfaces the sections alongside (not
instead of) the existing longTerm/shortTerm/working slices when the flag is
on. Village chronicle explicitly deferred. See specs/03-cognition.md and
specs/06-agents.md. Flag ships default off; a 30-min soak with the flag on
(module-drop-rate within noise of a flag-off control, spot-read merged
sections for coherence) is still outstanding before flipping default True.

Inspired by Karpathy's "LLM Wiki" note
(https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): instead
of an LLM re-deriving synthesis from raw sources every time, it maintains an
evolving set of its own notes — ingest new info into the *existing* relevant
pages, query the notes instead of raw material, and periodically lint for
contradictions/staleness. This does NOT apply to `SYSTEM_PROMPT` (that's
static and human-written, a caching problem — see item 2) — it applies to
this repo's per-agent and per-village memory, which today is lossy FIFO
capping, not synthesis:

- `agent["memory"]["longTerm"]` (cap 8, [sim_engine.py](simulation/sim_engine.py))
  — oldest entry just dropped when a new LLM-written summary arrives. No
  dedup, no reconciliation, no contradiction-checking against what's already
  there.
- The village `chronicle` (capped ring of major events) — same FIFO
  drop-the-oldest pattern, no cross-referencing between entries.
- `META_SYSTEM` autobiography — rewritten periodically as a fresh summary
  each pass, not "update the specific existing entries this new fact touches."

Candidate fix: give each agent (and/or the village) a small structured
"wiki" — a few persistent, named sections (e.g. relationships, goals,
lessons-learned, village history) that new memories get *merged into* rather
than appended-and-capped, plus an occasional lint pass (piggybacking on the
existing `_run_memory_maintenance` round-robin cadence) that asks the model to
flag/resolve contradictions between entries (e.g. "trusts Colt" vs. "Colt
betrayed me"). Should make agents feel like they accumulate a coherent life
story instead of a shrinking recent-events buffer.

**Relationship to item 1:** complementary, not overlapping — item 1 is about
whether memory *survives a restart*; item 3 is about whether memory
*meaningfully accumulates* while the server is running. Fix item 1 first (the
data has to persist before it's worth making it smarter).

**Risk / watch-outs:** every ingest/lint pass is an extra LLM call, competing
with the same LM Studio budget already strained by `PIANO_MODULES` — must be
cheap and infrequent (reuse `_run_memory_maintenance`'s existing round-robin
cadence rather than adding a new one) or it'll worsen the module-drop-rate
problem already being tracked from the 2026-07-23/24 sessions.

Needs its own plan doc before implementation.

## Ollama Phase 0 findings (2026-07-24)

Probed on the installed Ollama 0.32.3 (`http://localhost:11434`), RTX 3060
12 GB, per [docs/plan-ollama-migration.md](docs/plan-ollama-migration.md)
Phase 0. All calls were live against throwaway `probe-*` models; `probe-smart`
and `probe-fast` were left created (not loaded) for Phase 1 to pick up.

| # | Check | Result |
| --- | --- | --- |
| 1 | GGUF import (`ollama create -f Modelfile` with `FROM <lmstudio-cache-path>`) | **Pass.** `probe-smart` (Qwen3.5-9B-Q4_K_M) created in 52.2s. `probe-fast` (Llama-3.2-3B FP16) created in 134.8s (full re-verify/re-hash of a larger, uncompressed file — no smaller Q4 llama-3.2-3b GGUF exists in the cache, see note below). |
| 2 | `format` JSON schema (native `/api/chat`) | **Pass.** `format` as a full JSON-schema object is honored; `message.content` was valid JSON matching the schema in every trial. |
| 3 | Sampling `options` (`temperature`, `top_p`, `top_k`, `min_p`, `num_predict`) | **Pass.** All five accepted with no error; output was sane and `num_predict` was respected. |
| 4 | Thinking suppression (**CRITICAL GATE**) | **Pass, with an endpoint-specific caveat.** Native `/api/chat` + `think:false` fully suppresses reasoning: no `thinking` field, no `<think>` tags in `content`, `eval_count` dropped 1288→81 tokens, wall time 77s→4.8s. `think` unset on native `/api/chat` returns a **separate `thinking` field** (not inline in `content`) — content stays clean JSON either way, but the huge unset-case token/latency cost makes `think:false` mandatory. **OpenAI-compat `/v1/chat/completions` ignores `think:false` outright** — the `reasoning` field is populated and latency matches the unset case (19.5s) regardless of the parameter. Native `/api/chat` is therefore the only endpoint where thinking suppression actually works. |
| 5 | Truncation semantics | **Contradicts the plan's assumption.** Ollama 0.32.3 does **not** silently truncate — it returns HTTP 400 `{"error":{"code":400,"message":"request (N tokens) exceeds the available context size (512 tokens)...","type":"exceed_context_size_error","n_prompt_tokens":N,"n_ctx":512}}`. Reproduced at two overrun sizes (4650/512 and ~720/512 tokens); a request just under the limit (360/512) succeeded normally with `prompt_eval_count` populated. This is actually a **cleaner** overflow signal than LM Studio's error-string sniffing — Phase 2 should catch this HTTP 400 + `exceed_context_size_error` type directly rather than building a chars/4 heuristic vs. `prompt_eval_count`. |
| 6 | Modelfile `SYSTEM` apply/override semantics | **Pass, both directions confirmed.** No system message in the request → Modelfile `SYSTEM` ("...ZORPCONFIRM...") applied, `ZORPCONFIRM` appeared. Explicit system message in the request → it overrides the Modelfile `SYSTEM` entirely, `ARRCONFIRM` appeared and `ZORPCONFIRM` did not. |
| 7 | Concurrency + dual residency | **Parallel serving confirmed even with no env vars set** (defaults): 3 concurrent `/api/chat` calls to `probe-smart` completed in 5.0s vs. a 2.9s single warm-request baseline (would be ~8.7s if serialized) — Ollama's default parallelism is already non-1. **Dual residency requires `OLLAMA_MAX_LOADED_MODELS≥2`** — with nothing set, loading `probe-fast` evicted `probe-smart` from `ollama ps` every time (single-model residency is the 0.32.3 default). No `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS` user or machine env vars were set prior to this probe (both `[Environment]::GetEnvironmentVariable` calls returned empty) — Phase 1 starts from a clean slate. |
| 8 | Fast-model quality screen (8 real-shaped PIANO module prompts against `probe-fast`, `num_predict:60`, `temperature:0.5`) | **Mixed — 4/8 clean pass, 2/8 borderline (ungrounded but plausible invented detail), 2/8 fail.** Failures: a Perception-module call fabricated an unrelated "severe drought" not present in context, and another fabricated a cave "earthquake/collapse" — both ungrounded hallucinations a Cognitive Controller would ingest as fact. Two responses also leaked raw chat-template tokens into `content` (`<\|im_end\|>`, `<\|fim_end\|>` — **ChatML-style tokens, not Llama 3.2's native `<\|eot_id\|>`**), pointing to a mismatched/broken embedded chat template on this specific `beehive-lab/Llama-3.2-3B-Instruct-GGUF-FP16` file. Desire and Social module outputs were consistently good. |
| 9 | VRAM snapshot | Idle baseline: 1191/12288 MiB used (10925 MiB free). `probe-smart` alone (Q4_K_M, ctx=4096 default, not yet the target 20480): 6479 MiB used, 5637 MiB free. `probe-fast` alone (FP16, ctx=4096): ~8121 MiB used, ~3992 MiB free — notably heavier than the ~2-2.5 GB budgeted in the plan for a "3-4B Q4" fast model, because this GGUF is **FP16, not Q4** (see note below). True dual-residency VRAM (both loaded simultaneously) was not measured — Phase 0 correctly left `OLLAMA_MAX_LOADED_MODELS` unset per its own scope; Phase 1 must re-run this measurement once the env var is set, since ~6.5 GB + ~8 GB would not fit in 12 GB as-is. |

**Operational finding (not one of the 9, but load-bearing for Phase 2):**
Ollama does **not** cancel server-side generation when the client aborts/times
out a `stream:false` request. Three of my own timed-out PowerShell calls
queued up server-side; a subsequent trivial one-word request then measured
`total_duration: 366.5s` while its actual compute
(`load_duration`+`prompt_eval_duration`+`eval_duration`) was ~0.38s — the
rest was queue wait behind my own orphaned requests. Phase 2's retry/timeout
logic must not fire a second request on a client-side timeout without also
assuming the first one may still be consuming a GPU queue slot; naive
retry-on-timeout would compound queue depth rather than recovering from it.

**STOP conditions hit: none.** Thinking is suppressible (native endpoint);
GGUF import works; SYSTEM semantics work as designed. The fast-model quality
screen (test 8) did not hit the plan's stop-condition bar but is a genuine
quality concern worth a Phase 1 note, and the truncation-semantics finding
(test 5) means Phase 2 step 3 ("Truncation detection") should be rewritten
against the actual HTTP-400 contract, not the "silent truncation" assumption
in the plan text.

**GGUF paths for Phase 1:**
- `MODEL_SMART` source: `C:\Users\dbadmin\.lmstudio\models\lmstudio-community\Qwen3.5-9B-GGUF\Qwen3.5-9B-Q4_K_M.gguf` (5.24 GB; NOT the `unsloth\Qwen3.5-9B-MTP-GGUF` or any `mmproj-*` file in the same dirs — those are the draft/vision-projector variants, not the main text quant).
- `MODEL_FAST` source: `C:\Users\dbadmin\.lmstudio\models\beehive-lab\Llama-3.2-3B-Instruct-GGUF-FP16\beehive-llama-3.2-3b-instruct-fp16.gguf` (5.99 GB). **Caveat:** this is the only `llama-3.2-3b-instruct` GGUF in the LM Studio cache and it is **FP16, not Q4** — roughly 2.4x the VRAM/compute of the Q4 quant the plan's VRAM budget assumed, and its chat template appears to leak ChatML stop tokens (finding #8). Phase 1 should weigh re-downloading a Q4_K_M quant of `llama-3.2-3b-instruct` from a mainstream repo (e.g. `bartowski` or `unsloth`, both already used elsewhere in this cache) against keeping this FP16 file — the plan's own fallback ladder ("smaller fast-model quant" under the VRAM section) already anticipates needing this.

**Endpoint recommendation for Phase 2 cutover:** use **native `/api/chat`**,
not the OpenAI-compat `/v1/chat/completions` endpoint. Native is the only one
where `think:false` actually suppresses reasoning (finding #4) — OpenAI-compat
silently ignores it, which would reintroduce the 57%-bad-response epidemic
this migration exists to avoid. Native also gives a structured
`exceed_context_size_error` (finding #5) that OpenAI-compat likely wraps
differently (untested — out of scope once native was already established as
the target).

## 4. Always-on PIANO whiteboard — Phase B retry (attempt 2)

**STATUS: CLOSED/FAILED (2026-07-25).** Both attempt-2 treatment soaks missed
the gate (batch 2: latency +17.73%; batch 1 re-soak: freshness median 619.0s,
zero-work pulses 0) — rolled back to `ALWAYS_ON_MODULES = False`, batch 2;
feature stays dark pending a second GPU or a smaller/faster fast model.
CPU-offload was probed as a possible third option (2026-07-25) and came back
**NO-GO**: pinning a CPU-only throwaway model evicted `sim-fast` from Ollama's
resident-model set (confirmed via `/api/ps`), reintroducing contention as
reload latency on the same GPU-dependent model instead of eliminating it —
blocker hit before any throughput/tick measurement was possible. Full numbers
in `ollama_config.md`'s "CPU-offload probe (2026-07-25) — BLOCKED, NO-GO"
section. Full numbers for the earlier gate in `ollama_config.md`'s "FINAL
VERDICT" subsection. History below preserved as-is.

Attempt 1 (2026-07-25) FAILED its GPU-contention gate and was correctly
rolled back — `ALWAYS_ON_MODULES = False`, Phase A machinery intact and
smoke-covered on branch `codex/always-on-modules`. Full record:
`ollama_config.md` ("Always-on PIANO Phase B gate — FAIL / rolled back").

But it was not a clean falsification: the re-soak tuned the wrong lever
(shortened the pulse interval 45s→7s to chase a freshness miss, saturating
the pool), and the actual bottleneck signature — 18.6% refresh failures,
notes stale even at 7s pulses — points at the 15s module HTTP timeout, an
artifact of the old blocking design that no longer serves a purpose when
nothing waits on a refresh. That lever was never tried.

Retry recipe (full detail in
[docs/plan-always-on-modules.md](docs/plan-always-on-modules.md) "Phase B
attempt 1 ... retry recipe"): separate `MODULE_REFRESH_TIMEOUT_S = 60` for
pulse refreshes (blocking path keeps 15s), `MODULE_PULSE_MAX_BATCH = 2`,
interval stays 45s (binding tiebreak: the latency gate always wins — never
shorten the interval for freshness), freshness target relaxed to ≤120s
median, both soaks recorded in full. If refresh failures stay >9% even at
60s, record the second-GPU/smaller-model verdict and stop — no third tuning
attempt.
