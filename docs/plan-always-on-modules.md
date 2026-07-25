# Plan: always-on PIANO modules (Sid's whiteboard, for real)

Status: proposed (2026-07-24). **Implementer: Codex** (OpenAI Codex CLI/agent),
not Claude. Depends on the `ollama-migration` branch state (two-model split,
`sim-fast`, the module cache/age-label/persistence work) — implement on top
of it, or on `main` after it merges.

## Implementation directives (Codex)

This plan is to be implemented by Codex. Codex MUST follow the same repo
guidelines Claude follows — they are the project's rules, not Claude's, and
[AGENTS.md](../AGENTS.md) already points Codex at [CLAUDE.md](../CLAUDE.md) as
the canonical agent guide. Read CLAUDE.md first and honor all of it; the
load-bearing items for this plan:

- **SDD invariant** — any code change that alters behavior, flags, constants,
  or data shapes updates the owning spec in the *same* change (specs 01/02/03
  here). No code-without-spec commits.
- **Feature-flag-dark / one-flag revert** — everything ships behind
  `ALWAYS_ON_MODULES = False`; flag-off behavior stays byte-identical to
  today. Same discipline as the existing `WIKI_MEMORY` flag.
- **Verification** — there is no test suite or linter. Both deterministic
  smokes MUST pass after every phase: `uv run python scripts/sid_parity_smoke.py`
  and `uv run python scripts/path1_smoke.py`. Live checks use the running
  server + JSONL logs (`llm.jsonl`, `benchmarks.jsonl`).
- **Server runs** — start/restart only in a titled, visible `cmd` window per
  CLAUDE.md's Commands section; kill any prior instance first; keep exactly
  one instance on port 5001. Never background/detach it.
- **Branch + commits** — work on a dedicated branch (e.g. `always-on-modules`),
  one reviewable commit per phase, do not commit directly to `main`.
- **Usage-limit pause rule** — if Codex hits a usage/rate limit mid-phase,
  pause implementation immediately rather than pushing a partial, unreviewed
  edit through; resume only after reviewing the last completed step's diff +
  smoke result. (Mirrors CLAUDE.md's Claude-only rule, applied to Codex.)

## Model assignments (Codex tiers, mirroring Claude's policy)

CLAUDE.md's model policy is "one orchestrator, delegate implementation to a
capable-but-not-top tier, drop to a cheaper tier for mechanical work." Apply
the same shape with Codex's model lineup — pick the current Codex equivalents
of each Claude tier (substitute exact model ids for whatever Codex exposes at
implementation time; these are role mappings, not hard-pinned versions):

| Work | Claude tier this replaces | Codex tier to use |
| --- | --- | --- |
| Phases A/C implementation (engine + specs) | Sonnet 5 `implementer` | Codex's strongest coding model at **high** reasoning effort (the `gpt-5-codex` / `gpt-5` high tier) |
| Phase B soak analysis (log parsing + verdict) | Sonnet 5 `general-purpose` | Same strong model, **medium** reasoning effort |
| Post-review spec/doc wording passes | Haiku 4.5 | A lighter/cheaper Codex tier or **low** reasoning effort — mechanical, low-ambiguity |

Keep the orchestrate-vs-implement split in spirit: plan and review at the
strong tier, and don't do the mechanical doc passes at the expensive setting.
If Codex runs single-agent rather than orchestrator+subagents, execute the
phases sequentially in that spirit — one phase per session/commit, reviewed
before the next — rather than one long unbroken pass.

## Motivation

The last meaningful architectural gap with Project Sid: their modules run
continuously against shared agent state and the Cognitive Controller reads
whatever is current; ours run as a per-turn fan-out the decision blocks on
(up to `PIANO_MODULE_TIMEOUT_WAIT_S = 18` s), and a slow module's work is
dropped. Everything else the whiteboard needs already exists after this
week's work: the per-agent report cache, cross-module visibility with age
labels, `agent["moduleReports"]` persistence, a dedicated worker pool
(`piano_workers`, `PIANO_CONCURRENT_LLM = 2`), and a dedicated cheap model
(`sim-fast`) that answers a module prompt in a few seconds. The missing
piece is only the *scheduler*: refresh notes on the world's clock instead of
the decision's.

Payoffs if the gate holds: decision turns stop waiting on modules entirely
(today's think job blocks on the fan-out); module work is never discarded
(staleness replaces drops); the sim moves from "per-turn huddle" to Sid's
"read the whiteboard" without touching the Cognitive Controller contract.

Guiding principle (TASKS_PENDING.md) is satisfied: nothing is hidden — the
controller reads all four notes plus their ages, all visible in the prompt.

## Known costs to gate, not assume away

1. **GPU contention** — one RTX 3060 serves both models; background
   inference on `sim-fast` taxes `sim-smart`'s decision latency (currently
   p50 ~6.2 s). This is THE go/no-go measurement. The gated-pulse design
   (Phase A) bounds it two ways: pulses are capped (`MODULE_PULSE_MAX_BATCH`)
   so a contention spike is short, and an empty due-list makes a pulse a
   no-op, so contention only occurs when the world is actually changing.
2. **24/7 sustained load** — the concern is a home server whose GPU never
   rests. The gated-pulse + event-gating design (Phase A) resolves this by
   construction, not by a bolt-on throttle: between pulses the GPU is idle,
   and a pulse with nothing due does no work, so a quiet or sleeping village
   produces near-zero module inference. Load is proportional to world
   activity. `module_pulse_work` (Phase A benchmark) is the direct evidence;
   Phase C only adds a coarser night-level backstop on top.

---

## Phase A — gated-pulse scheduler + read-only decision path (ships dark)

Files: `simulation/sim_engine.py` (tick loop, `_run_piano_modules`,
`_schedule_think` area, the engine event sites that mark context dirty),
`simulation/server.py` (only if `run_piano_module` needs a tweak);
specs 01/02/03.

Design principle: "always-on" means notes are refreshed *independently of
decision turns* — NOT that the GPU is continuously busy. The scheduler is a
**gated interval pulse**, not a steady trickle: it wakes on a fixed
interval, refreshes only what is genuinely due, then goes fully idle until
the next wake. GPU load becomes proportional to how much is actually
changing in the world — a quiet or sleeping village produces near-zero
module inference, so the GPU gets real rest windows without a special
night mode.

1. **Flag + constants** (sim_engine.py, with the other cognition flags):
   `ALWAYS_ON_MODULES = False`. Constants:
   `MODULE_PULSE_INTERVAL_S = 45` (scheduler wakes this often; between
   wakes it does zero module work — this is the knob that trades GPU rest
   against note freshness), `MODULE_PULSE_MAX_BATCH = 4` (cap on refreshes
   dispatched per pulse, so a pulse is always short and its contention with
   decisions is bounded), `MODULE_NOTE_MAX_AGE_S = 600` (the *only*
   time-based force-refresh — deliberately long, see step 2's rationale on
   stale-but-correct notes), `MODULE_REFRESH_IDLE_SKIP = True`
   (sleeping/collapsed agents are never refreshed).
2. **Dirty flag drives refresh — a note is due only when its subject
   changed.** `agent["contextDirty"] = True` set by the engine events that
   change what a module would say: district arrival, inbox delivery,
   hunger/health threshold crossings, role/belief changes, plus
   *village-level* events that move a module's answer without the agent
   acting (project start/completion, an enacted rule, a season turn — these
   mark the affected agents dirty so `desire`/`reflection` don't drift
   unseen). Cleared when a pulse refreshes that agent. Rationale for the
   long `MODULE_NOTE_MAX_AGE_S`: a note is often *stale but still correct* —
   a `perception` note for an agent standing still in an unchanged district
   is accurate minutes later and does not need re-inferring. Event-gating,
   not a short timer, is what keeps notes fresh; the max-age is only a
   fossilization backstop for the rare quiet-but-drifting case. This is the
   core of the GPU-rest win — no "just in case" refreshes.
3. **Gated-pulse scheduler.** In the existing tick loop, every
   `MODULE_PULSE_INTERVAL_S` (converted to frames): build the due-list —
   agents/modules that are dirty OR whose note exceeds
   `MODULE_NOTE_MAX_AGE_S`, excluding idle-skipped agents. **If the
   due-list is empty, the pulse does nothing and returns immediately —
   the GPU stays idle.** Otherwise, order the due-list (dirty first, then
   oldest note; keep the perception/desire-every-time, social ×2,
   reflection ×3 staleness ratios as *priority weights*, not separate
   timers) and submit up to `min(MODULE_PULSE_MAX_BATCH,
   PIANO_CONCURRENT_LLM free slots)` refreshes to `piano_workers`; anything
   still due rolls to the next pulse. Context is built under the lock
   (reuse `_piano_module_context` + the cross-context suffix); each
   completion callback re-acquires the lock and writes `{tick, text,
   wall_ts}` into `_piano_module_cache` and `agent["moduleReports"]`, and
   clears that agent's dirty flag once all its due modules are written.
   Never joined, never waited on. A failed/timed-out refresh leaves the old
   note in place (counted as `module_refresh_failures`, replacing
   `piano_module_drops` semantics when the flag is on) and does NOT clear
   the dirty flag, so it retries next pulse.
4. **Decision path.** When `ALWAYS_ON_MODULES` is on, `_run_piano_modules`
   becomes assemble-only: read all four cached notes, label every one with
   its wall-clock age (`social (73s ago): ...` — extend the existing
   age-label format from turn-units to seconds when the flag is on), no
   dispatch, no futures, no wait. TTL check uses `MODULE_NOTE_MAX_AGE_S`
   ×2 as the include bound (an ancient note is worse than none). Flag off:
   byte-identical current behavior — one-flag revert, same pattern as
   `WIKI_MEMORY`.
5. **Benchmarks.** New: `module_note_age` (avg + max age of notes actually
   read at decision time, per benchmark period), `module_pulse_work`
   (refreshes dispatched per pulse — its distribution shows how often
   pulses are no-ops, i.e. how much the GPU actually rests), and
   `module_refresh_failures`. Keep `piano_module_latency`. These are the
   observability for Phase B, and `module_pulse_work` near zero during
   quiet periods is the direct evidence that the GPU-rest goal is met.
6. **Specs.** 03 (cognition path, flag semantics, note-age labels, the
   pulse/dirty/event-gating model), 02 (tick-loop pulse hook + the event
   sites that mark dirty), 01 (flag index).
7. **Offline verification** (extend `sid_parity_smoke.py`): stub runner;
   assert (a) an all-clean, all-fresh roster produces an empty pulse (zero
   dispatches — the GPU-rest invariant), (b) a dirty agent is picked next
   pulse and the batch cap is honored, (c) decision assembly never blocks
   and includes wall-clock age labels, (d) a failed refresh keeps the prior
   note AND leaves the agent dirty for retry, (e) flag-off path unchanged,
   (f) `moduleReports` persistence still round-trips.

## Phase B — the GPU-contention gate (go/no-go)

Two 45-minute soaks at the current roster, flag off vs on, same world save.
Compare from `llm.jsonl` + `benchmarks.jsonl`:

- **Decision p50/p90** — the gate: flag-on p50 within **+15%** of flag-off
  (reference ~6.2 s). This is the cost of the pulses' background load on the
  shared GPU; because pulses are gated and capped, the spike is intermittent
  and short rather than sustained.
- **Note freshness** — median `module_note_age` at decision time ≤ 120 s,
  max ≤ `MODULE_NOTE_MAX_AGE_S`. (Was 90 s in attempt 1 — see the retry
  recipe: 90 s proved structurally tight against a 45 s pulse plus queue
  time, and the 94 s "miss" it produced steered tuning the wrong way.)
- **TIEBREAK RULE (binding): the latency gate always wins.** Never shorten
  `MODULE_PULSE_INTERVAL_S` to chase a freshness miss — attempt 1 did
  exactly that (45 s → 7 s), which saturated the pool and failed latency,
  freshness, AND refresh-rate at once. If freshness misses while latency
  passes, the correct levers are the refresh *timeout* and *batch* knobs
  (see retry recipe), never pulse frequency.
- **GPU rest** — `module_pulse_work` distribution shows a healthy share of
  empty/near-empty pulses during quiet periods (the whole point of the
  gated design). If nearly every pulse is maxed out, the world is busier
  than the budget and the freshness/latency trade needs retuning.
- **Refresh failure rate** ≤ the old drop reference (~9%) — failures now
  cost freshness, not decisions, but a high rate still signals saturation.
- Decision fallback rate and action distribution within noise of flag-off.

Pass → flag stays on; record numbers in `ollama_config.md`'s benchmark
section — **record BOTH soaks' full numbers, not just the final one**
(attempt 1 recorded only its re-soak, leaving a gap in the findings trail).
Miss on decision latency → lengthen `MODULE_PULSE_INTERVAL_S` (fewer
pulses) or lower `MODULE_PULSE_MAX_BATCH` (shorter pulses); try one and
re-soak once; a second miss → flag off, findings recorded (the whiteboard
waits for a second GPU or a smaller fast model, not forced through).

## Phase B attempt 1 (2026-07-25): FAIL — and the retry recipe

Attempt 1 is recorded in `ollama_config.md` ("Always-on PIANO Phase B gate —
FAIL / rolled back"). Outcome: first soak missed freshness (median 94.1 s vs
the then-90 s target); the re-soak shortened the pulse interval 45 s → 7 s to
chase it — the wrong lever — and failed everything: decision p50 +15.4%,
median age still 94.1 s, **18.6% refresh failures**, zero empty pulses.
Rollback was executed correctly (flag off, 45 s restored, Phase C skipped).

**Diagnosis (why this was not a clean falsification):** even at 7 s pulses
the notes stayed old — so pulse frequency was never the bottleneck; refresh
*throughput* was. The failure signature points at
`PIANO_MODULE_TIMEOUT_S = 15` (server.py): a 15 s HTTP timeout is an
artifact of the old blocking fan-out, where a slow module held a decision
hostage. Under always-on, nothing waits on a refresh — a module taking 25 s
hurts nobody — but the 15 s timeout kills it anyway under GPU contention,
wasting the compute already spent, leaving the note stale, leaving the agent
dirty, and re-queueing the same work next pulse: a self-defeating loop. This
lever was never tried.

**Retry recipe (attempt 2 — implement, then re-run Phase B):**

1. **Separate timeout for background refreshes.** New constant
   `MODULE_REFRESH_TIMEOUT_S = 60` (sim_engine.py, beside the pulse knobs).
   `run_piano_module` (server.py) gains an optional `timeout_s` parameter
   defaulting to the existing `PIANO_MODULE_TIMEOUT_S = 15`; the pulse path
   (`_run_always_on_module`) passes `MODULE_REFRESH_TIMEOUT_S`. The blocking
   fan-out path keeps 15 s untouched (its wait-budget contract with
   `PIANO_MODULE_TIMEOUT_WAIT_S = 18` still holds when the flag is off).
2. **`MODULE_PULSE_MAX_BATCH = 2`** (was 4): with 60 s refreshes possible, a
   batch of 4 could hold both `piano_workers` slots across multiple pulses;
   2 keeps one pulse's work from cascading and halves worst-case contention
   with decisions.
3. **`MODULE_PULSE_INTERVAL_S` stays 45.** Not a tuning lever for this
   attempt (tiebreak rule above).
4. **Freshness target ≤ 120 s median** (updated above) — the honest floor
   for a 45 s pulse with queueing, and the metric that matters is "note
   reflects the agent's current situation," which event-gating already
   guarantees at dirty-time; age is a proxy, not the product.
5. Re-run Phase B exactly as specified: two 45-minute soaks, same world
   save, both recorded in full. Expected signature if the diagnosis is
   right: refresh failures collapse toward ~0 (few module calls genuinely
   need >60 s), median age drops well under 120 s (successful refreshes stop
   re-queueing), and empty pulses appear during quiet stretches. If refresh
   failures stay >9% even at 60 s, the fast model genuinely can't keep up
   under contention and the honest verdict is the second-GPU/smaller-model
   one — record and stop, no third tuning attempt.

## Phase C — night backstop (optional, coarse)

Most of the load-shaping the 24/7 box needs is already automatic from Phase
A's event-gating: sleeping agents are idle-skipped and their worlds don't
change, so nights are near-empty pulses without any special handling. Phase
C adds only a coarse backstop for belt-and-suspenders: when the engine's
day/night state is night, multiply `MODULE_PULSE_INTERVAL_S` ×4 so even the
occasional night-time dirty event waits longer. This is a small, optional
addition — implement only if a Phase B day-night soak shows night pulses are
not already near-zero from event-gating alone. Verify with a day-night soak
showing `module_pulse_work` visibly lower at night with no decision-quality
change.

## Stop conditions

Phase B's two-strike latency gate is the only hard one. No stop condition
leaves the sim degraded: the flag defaults off, every phase is a one-flag
revert, and the current per-turn fan-out remains fully functional
underneath until the flag is proven.
