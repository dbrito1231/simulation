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
