# Plan: PIANO cross-module visibility (working-memory half-step)

Status: proposed (2026-07-23). Owner: orchestrator + `implementer` subagents per
[CLAUDE.md](../CLAUDE.md#model-policy) model policy.

## Motivation

Project Sid's PIANO runs its modules against a shared agent state: every module
sees what the others (and its own past self) concluded. Our turn-synchronous
port ([specs/03-cognition.md:284-314](../specs/03-cognition.md)) keeps modules
mutually blind — each Perception/Social/Desire/Reflection call gets only the
same engine-built snapshot string
(`_piano_module_context`, [sim_engine.py:10502](../simulation/sim_engine.py)),
never another module's report. The report cache
(`_piano_module_cache`, TTL 2 module-ticks) exists solely to fill off-tick
slots in the *decision* payload; modules themselves never read it.

This plan closes the visibility gap **without adding a single LLM call** and
without touching the concurrency model (`PIANO_CONCURRENT_LLM = 2`, 15 s
drop-not-retry timeout, stagger unchanged). Full Sid-style always-on modules
remain out of scope until the LLM call budget grows — see the verdict in
[plan-sid-parity-gaps.md](plan-sid-parity-gaps.md) Phase 1.

## Scope

| # | Change | Where | Phase |
| --- | --- | --- | --- |
| 1 | Modules see peers' (and their own) last reports | `_piano_module_context` / `_run_piano_modules` | A |
| 2 | Module prompts told to build on / correct prior reports | `MODULE_PROMPTS` (server.py) | A |
| 3 | Decision payload labels stale off-tick reports with age | `_run_piano_modules` | A |
| 4 | Working memory survives save/restore | agent dict + `restore_state` | B |

Out of scope: running modules between decision turns; structured (non-text)
agent state; any change to `MAX_CONCURRENT_LLM`/`PIANO_CONCURRENT_LLM`,
stagger cadence, or timeouts.

---

## Phase A — cross-module context injection

Files: `simulation/sim_engine.py`, `simulation/server.py`;
spec [03-cognition.md](../specs/03-cognition.md) (same change).

1. **New constant `PIANO_CROSS_CONTEXT_TTL = 6`** (module-ticks) next to
   `PIANO_MODULE_CACHE_TTL` (sim_engine.py:619). Cross-module context tolerates
   more staleness than the decision payload: a 6-tick-old Desire report is
   still useful *orientation* for Perception, while the decision payload keeps
   its tight 2-tick freshness bar. Keep the two TTLs separate constants — do
   not widen `PIANO_MODULE_CACHE_TTL`.
2. **Append a `last_reports=` suffix in `_run_piano_modules`**, not in
   `_piano_module_context`. The context string is built under the lock before
   dispatch, but the cache is only safely readable on the decision-worker
   thread that owns this agent's turn (one in-flight think per agent —
   `self._inflight` — makes per-agent cache access single-threaded; keep it
   that way). Before dispatching `to_run`, build one shared suffix from every
   cached report within `PIANO_CROSS_CONTEXT_TTL`, each entry as
   `module(N ago): text`, e.g.
   `last_reports=desire(1 ago): stockpile wood for the granary | social(2 ago): ask Sage about the blueprint`.
   All modules dispatched this turn get `context + "; " + suffix` — one string
   build, no per-module variants. A module seeing its *own* previous report is
   intentional (continuity, especially for Reflection).
3. **Token budget check (spec update, same change).** Reports are capped at
   200 chars (server.py:2692), so the suffix adds ≤ ~4 × 60 ≈ 240 tokens per
   module call on top of a currently tiny prompt (`max_tokens=60` output).
   This fits the existing ~3,400-token/slot formula with room to spare; state
   that explicitly in specs/03's context-formula section rather than changing
   the formula.
4. **Prompt updates in `MODULE_PROMPTS`** (server.py:2647): one added clause
   per module, e.g. Perception gains "If last_reports are present, build on or
   correct them rather than repeating them." Keep every prompt's ONE-sentence
   output contract untouched.
5. **Age-label off-tick fills in the decision payload.** Where the cache fills
   an off-tick slot (sim_engine.py:10577), emit
   `social (2 turns ago): ...` instead of `social: ...` so the Cognitive
   Controller can discount stale advice. Fresh same-tick reports keep the bare
   `module:` form. `SYSTEM_PROMPT` rule 18 (Cognitive Controller) needs no
   change — the label is self-explanatory — but specs/03's module-report
   description must document both forms.

Verify (deterministic, no LM Studio):

6. **Extend `test_piano_stagger_offline`**
   ([scripts/sid_parity_smoke.py:720](../scripts/sid_parity_smoke.py)): swap the
   stub runner for one that records `(module, context)` calls; assert tick 1
   contexts contain no `last_reports`, tick 2 contexts contain
   `perception(1 ago)` and `desire(1 ago)`, and an off-tick fill renders as
   `social (2 turns ago):` in the returned report string. Assert a report
   older than `PIANO_CROSS_CONTEXT_TTL` is excluded from the suffix.

Verify (live): 30-min soak at roster 8 with LM Studio;
`piano_module_latency` and `piano_module_drops` in `benchmarks.jsonl` within
noise of a pre-change control run (the suffix grows module prompts, so latency
is the regression to watch); read `lm_studio.jsonl` to confirm modules
actually reference prior reports rather than echoing them.

## Phase B — persist working memory across save/restore

Files: `simulation/sim_engine.py`; specs
[03-cognition.md](../specs/03-cognition.md),
[06-agents.md](../specs/06-agents.md) (agent data shape, same change).

Today `_piano_module_cache` is engine-memory only — wiped by `/control/reset`
(sim_engine.py:11373) and lost on restart, so every restore begins with blind
modules for up to 3 module-ticks. Agents already persist to `state.db`, so
piggyback on that:

7. **Mirror the cache into the agent dict under the lock.** `_run_piano_modules`
   runs outside the lock and must stay that way; the mirror write goes in the
   post-think callback that already re-acquires the lock to set
   `agent["moduleTick"]` (sim_engine.py:10648-10650). Write
   `agent["moduleReports"] = {module: {"tick": int, "text": str}}` (copy of
   this agent's cache entry). Engine reads continue to use
   `_piano_module_cache`; the agent field is persistence-only.
8. **Rehydrate on restore.** In `restore_state()`, rebuild
   `_piano_module_cache` from each agent's `moduleReports` (tolerate the field
   being absent in old saves — default empty). `/control/reset` keeps its
   current behavior: full reset drops working memory along with the rest of
   the world.
9. **Spec the shape.** specs/06 gains `moduleReports` in the agent data-shape
   table; specs/03 notes the cache now survives restarts and cites the
   rehydrate path.

Verify: extend the smoke with a save→restore round-trip asserting the rebuilt
cache serves an off-tick fill on the first post-restore turn;
`uv run python scripts/sid_parity_smoke.py` and
`uv run python scripts/path1_smoke.py` still pass.

## Risks

- **Echo loops** — a module paraphrasing the suffix back instead of thinking.
  The prompt clause in step 4 is the mitigation; the live-soak log read in
  step 6 is the check. If echoing dominates, drop the module's own report from
  the suffix (peers only) before reaching for prompt surgery.
- **Prompt drift on a 9B model** — added clauses can degrade the one-sentence
  contract. `run_piano_module` already truncates at 200 chars, which bounds
  the damage; watch `piano_module_latency` for slow generations.
- **Stale-label confusion** — if the Controller starts citing "(2 turns ago)"
  text verbatim in messages, tighten the label to a parenthetical the
  `SYSTEM_PROMPT` examples never quote.

## Sequencing

Phase A is self-contained and shippable alone; Phase B depends on A's cache
semantics but not its prompts. One `implementer` dispatch per phase; specs
updated in the same change as the code they describe (SDD invariant,
[CLAUDE.md](../CLAUDE.md#critical-invariants)).
