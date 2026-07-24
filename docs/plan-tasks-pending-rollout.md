# Plan: TASKS_PENDING rollout (memory persistence, prompt cost, wiki memory)

Status: proposed (2026-07-24). Owner: orchestrator + subagents per
[CLAUDE.md](../CLAUDE.md#model-policy) model policy. Source of scope:
[TASKS_PENDING.md](../TASKS_PENDING.md) — items 1, 2a, 2b, 3. Plan only — no
implementation in this doc.

**Guiding principle (binding, from TASKS_PENDING.md):** the LLM stays the
main orchestrator with full, honest visibility; the engine passes information
in and out efficiently but never pre-filters what the LLM sees or bakes rules
into opaque weights. Every phase below must hold to this. The rejected-ideas
list in TASKS_PENDING.md (RAG rule filtering, LoRA fine-tune) stays rejected.

## Scope and sequencing

| Phase | TASKS_PENDING item | Depends on | Risk |
| --- | --- | --- | --- |
| 1 | #1 MemoryStore restart persistence | — | Low |
| 2 | #2a Prefix-cache audit/harden | — | Low |
| 3 | #2b Rules to model load time (lms CLI) | Phase 2 | Medium |
| 4 | #3 Wiki-style compounding memory | Phase 1 | Medium |

Phases 1 and 2 are independent of each other but both touch `server.py`;
run them **sequentially** (or with worktree isolation if parallel dispatch is
wanted). Phase 3 must follow Phase 2 (2a is the cheap baseline; 2b's A/B soak
needs 2a's latency numbers as its control). Phase 4 must follow Phase 1 (make
memory survive restarts before making it smarter).

**SDD invariant:** every phase updates its owning spec in the same change as
the code — specs/03-cognition.md for all four phases; specs/06-agents.md
additionally for Phase 4. No test suite exists; verification is the two
deterministic smokes (`scripts/sid_parity_smoke.py`, `scripts/path1_smoke.py`)
plus live soaks read from `simulation/logs/<ts>/`.

## Model assignments (per CLAUDE.md model policy)

The orchestrator (any tier — Fable/Opus/Sonnet) plans, dispatches, and
reviews; it writes no implementation code beyond trivial one-liners. All
implementation goes to subagents on **Sonnet 5 or lower**:

| Work | Agent type | Model | Why |
| --- | --- | --- | --- |
| Engine/server code changes (all phases) | `implementer` | **Sonnet 5** (`model: sonnet`, pinned in `.claude/agents/implementer.md`) | Multi-file changes with lock/threading invariants need the strongest allowed implementation tier |
| Phase 3 research rung (lms CLI / REST capability probe) | `Explore` or `general-purpose` | **Sonnet 5** | Read-only investigation + external doc reading; conclusions feed a go/no-go |
| Doc-only edits (lms_config.md notes, spec wording passes that follow an already-reviewed code diff) | `implementer` | **Haiku 4.5** (`model: haiku`) | Mechanical, low-ambiguity; cheapest sufficient tier |
| Log-analysis verification (soak before/after comparisons) | `general-purpose` | **Sonnet 5** | Parsing JSONL + statistical comparison; needs judgment, not just extraction |

One `implementer` dispatch per phase by default; split a phase into multiple
dispatches only where steps touch disjoint files. Never dispatch
implementation to Opus/Fable.

---

## Phase 1 — MemoryStore survives restarts (item 1)

Files: `simulation/server.py` (MemoryStore construction ~line 620, class
~line 416); spec `specs/03-cognition.md`.

Today `memory_store = MemoryStore(os.path.join(session_logger.dir,
"memory.json"))` binds the semantic-recall index to the per-session log
directory, so every restart starts empty, silently.

1. **Stable home.** Construct MemoryStore against a restart-stable path
   (`simulation/memory_store.json`, gitignored) and load it on init if
   present (tolerate absent/corrupt → start empty, log which). Keep the
   debounced persist (`MEMORY_PERSIST_EVERY`) and clean()-flush behavior.
2. **Keep the per-session inspection artifact.** `memory.json` in the session
   log dir is documented as an inspection artifact; keep writing a copy there
   (or a symlink-equivalent note in the session dir) so the debugging surface
   is unchanged.
3. **Make the loss visible either way.** On startup, log one line with the
   loaded entry count and emit a `memory_store_loaded` benchmark so a future
   regression to empty-on-restart is observable, not silent.
4. **Reset semantics.** `/control/reset` drops the world; decide-and-spec
   whether it also clears the semantic store (recommended: yes, for
   consistency with Phase B's cache-wipe-on-reset precedent) and implement
   that explicitly.
5. **Spec** the new path, load/persist lifecycle, and reset behavior in
   specs/03.

Verify: offline — extend a smoke to store → flush → re-init from the stable
path → assert `query()` returns the stored entry. Live — restart the server
once; startup line shows nonzero loaded count; `(recalled: ...)` lines appear
in `lm_studio.jsonl` prompts in the first minutes (previously impossible
right after restart). Dispatch: one `implementer` (Sonnet 5).

## Phase 2 — Prefix-cache audit and harden (item 2a)

Files: `simulation/server.py` (`build_decision_payload` ~3312, SYSTEM_PROMPT
constants, the ~3662 SYSTEM_PROMPT reassignment); spec `specs/03-cognition.md`.

Goal: guarantee the system message is byte-for-byte identical across routine
turns so LM Studio's longest-common-prefix KV reuse always fires. No behavior
change; no reduction in what the LLM sees.

1. **Audit** every content source that lands in the system message across
   turn types (routine, slim retry, invention, sprite) and across agents;
   confirm nothing per-agent or per-tick (names, counts, timestamps, roles
   refresh at ~server.py:3662) mutates the routine-turn system string
   mid-session. Produce a short findings list before changing code.
2. **Harden**: fix any leaks found; add a startup log of
   `sha256(SYSTEM_PROMPT)[:12]` so soak logs can prove the prefix never
   changed mid-run; assert (log-once, not crash) if the system string differs
   between two routine dispatches.
3. **Slim-retry note**: document (specs/03) that the overflow retry
   deliberately swaps prefixes and therefore forfeits cache reuse for that
   one call — expected, rare, acceptable.
4. **Measure**: 30-min soak before/after at roster 8 comparing p50/p90
   decision `latency_ms` from `lm_studio.jsonl`, plus LM Studio's own
   per-slot context checkpoints (`simulation/logs/lm_studio_server.log`) if
   present. Success = p50 no worse, ideally improved; any regression is a
   stop-and-investigate.

Dispatch: one `implementer` (Sonnet 5) for audit+harden; one
`general-purpose` (Sonnet 5) for the soak comparison.

## Phase 3 — Rules move to model load time via lms CLI (item 2b)

Files: `scripts/lms_load.py`, `simulation/server.py`, `lms_config.md`; spec
`specs/03-cognition.md`.

**Gate first — research rung (no code):** dispatch an `Explore`/
`general-purpose` agent to establish whether LM Studio (installed version)
supports setting a default system prompt / preset at load time via REST
and/or the `lms` CLI, matching `scripts/lms_load.py`'s existing
rung-with-fallback structure. Deliverable: the exact invocation(s), which
rung supports it, and whether the per-request system message then becomes
optional or is concatenated. **If unsupported on both rungs: stop, record
findings in TASKS_PENDING.md item 2b, and keep Phase 2 as the shipped state.**

If supported:

1. **Loader**: extend `scripts/lms_load.py` to push the rule text at load
   time within its existing REST-first/CLI-fallback structure. The loader
   reads the text from the single source of truth (import or parse
   `SYSTEM_PROMPT` from server.py — never a duplicated copy).
2. **Server flag**: new module-level flag (e.g.
   `SYSTEM_PROMPT_AT_LOAD_TIME = False` default) in server.py; when True,
   `build_decision_payload` omits the system message for routine turns.
   Slim-retry, invention, and sprite turns keep their explicit prompts
   unless the research rung proves per-request override works cleanly.
   Flag order: ship dark (False), A/B, then flip.
3. **A/B soak (the go/no-go)**: two 30-min soaks at roster 8, flag off vs
   on. Compare: fallback rate (`bad_response` / `role_fallback` in
   `lm_studio.jsonl`), action distribution shape, `context_overflow` count,
   p50 latency, prompt_chars. Success = quality metrics within noise AND
   prompt size/latency materially down. Small local models can "forget" a
   load-time default under a long user message — the fallback-rate metric is
   the tripwire. Regression → flag stays False, findings recorded.
4. **Docs**: exact `lms` invocation in `lms_config.md`; flag semantics +
   flag-index entry in specs/03 (and specs/01 flag index if echoed to
   `/state`). SYSTEM_PROMPT in server.py stays the canonical rule text
   regardless of where it's injected.

Dispatch: research (Sonnet 5) → implementer (Sonnet 5) → soak analysis
(Sonnet 5) → doc pass (Haiku 4.5).

## Phase 4 — Wiki-style compounding memory (item 3)

Files: `simulation/sim_engine.py` (`_run_memory_maintenance` ~9030,
`_push_memory`, `_memory_for_prompt`), `simulation/server.py` (summarizer
call sites); specs `specs/03-cognition.md`, `specs/06-agents.md`.

Goal: long-term memory that merges and reconciles instead of FIFO-dropping.
Hard budget constraint: **no net-new LLM call cadence** — reuse
`_run_memory_maintenance`'s existing round-robin slot (every
`MEMORY_TICK_FRAMES`, one agent per pass), upgrading what that one call does
rather than adding calls beside it (the module-drop-rate regression from the
2026-07-23/24 sessions is still open; this phase must not worsen it).

1. **Structure**: `agent["memoryWiki"]` — a small dict of named sections
   (`relationships`, `goals`, `lessons`, capped chars per section), living in
   the agent dict so persistence via `state.db` is free (same pattern as
   Phase B's `moduleReports`).
2. **Merge-on-maintenance**: replace the current summarize-and-append call
   in `_run_memory_maintenance` with a merge prompt: given the agent's
   current wiki sections + the recent raw memories, return updated sections
   (same one-call budget, slightly larger max_tokens). The existing
   `longTerm` list stays as-is during rollout (feature-flagged, e.g.
   `WIKI_MEMORY = False` default) so the old path is a one-flag revert.
3. **Lint, cheaply**: fold contradiction-checking into the same merge prompt
   ("resolve or flag contradictions between sections") rather than a
   separate pass. A flagged contradiction is logged to `activity.jsonl` as a
   reflection event — observable, zero extra calls.
4. **Prompt surface**: `_memory_for_prompt` includes the wiki sections
   (char-capped) when the flag is on, alongside — not replacing — recent
   working/shortTerm lines. The LLM sees more coherent history; nothing it
   previously saw is removed (guiding principle).
5. **Village chronicle**: explicitly deferred to a follow-up — agent wiki
   first, prove the pattern, then decide if the chronicle needs it.
6. **Spec** the shape (specs/06 agent table), the merge/lint semantics and
   flag (specs/03), and scaffold-text validation reuse (`is_scaffold_text`)
   on merge output.

Verify: offline — smoke with a stubbed `lm_complete` asserting merge output
lands in sections, caps hold, flag-off path unchanged, save/restore
round-trips `memoryWiki`. Live — 30-min soak with flag on: `module_total`
and `piano_module_drops` within noise of a flag-off control;
`memory.jsonl`/`activity.jsonl` show merge/lint events; spot-read merged
sections for coherence. Dispatch: one `implementer` (Sonnet 5); soak
analysis (Sonnet 5).

## Rollout order and stop conditions

1 → 2 → 3 → 4, one phase merged and verified before the next starts. Each
phase is independently shippable and independently revertible (Phases 3 and 4
behind default-off flags). Stop conditions: Phase 3 research rung finds no
load-time support (record and stop); any A/B soak shows decision-quality
regression (flag stays off, findings recorded in TASKS_PENDING.md rather
than force-fixed forward).

On completion of each phase, tick the corresponding item in
TASKS_PENDING.md (leave the rejected-ideas record untouched).
