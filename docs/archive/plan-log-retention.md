# Plan — Session-log retention policy

**Status:** in implementation (2026-07-26). Owner: orchestrator + one
`implementer` subagent per [CLAUDE.md](../CLAUDE.md#model-policy).
**Resolved defaults** (§7 open questions, decided 2026-07-26): count-based
retention, `LOG_RETENTION_SESSIONS = 20`. No one-time backfill needed (the
tree is already clean; the first server start after this lands prunes any
future pile-up automatically).

## 1. Problem

`SessionLogger` ([server.py:362](../simulation/server.py)) creates a fresh
`simulation/logs/<session_id>/` directory **every server start** and never
removes old ones. Because CLAUDE.md instructs restarting the server routinely
(own titled window per restart), a working tree accumulates one session folder
per run indefinitely — the user reported having to hand-delete "multiple
folders of logs" and expected an appendable single set instead. There is **no
pruning logic anywhere** (`grep` for rmtree/retention in server.py returns
nothing).

**This is intended behavior today, not a bug** — per-run isolation is
deliberate (`specs/12-ops.md`: "every server process gets exactly one session
folder for its lifetime"; the first `conversation.jsonl` line is a synthetic
`session_start`). The fix is **bounded retention**, not switching to one
appendable log — keeping per-session isolation (which the debugging workflow,
`soak_monitor.py`, and `llm.jsonl`-per-session forensics all rely on) while
capping how many sessions are kept on disk.

## 2. The single load-bearing safety constraint

`simulation/logs/` is **not** only session directories. Its root also holds
loose artifacts that are NOT session folders and must never be touched by
retention:

- `soak_monitor.py` writes `simulation/logs/soak-<label>.json` directly in the
  root (`specs/12-ops.md:95`).
- `path1_soak.py` result/timeline files have historically lived here
  (`path1_soak_<stamp>.json` / `.timeline.json`).
- Ad-hoc seed DBs / dumps (e.g. `always_on_modules_phase_b_seed.db`) get
  dropped here during investigations.
- Any non-timestamp subdirectory (e.g. `replay_bench/`).

Retention MUST prune **only** directories whose name exactly matches the
session-id format and MUST leave every loose file and every non-matching
directory untouched. Getting this wrong reproduces exactly the data-loss the
user is worried about, at a larger scale. This constraint is the primary
acceptance test (§6), not an afterthought.

## 3. Design

- **Count-based, keep-N-newest.** Retain the `LOG_RETENTION_SESSIONS` most
  recent session directories; delete the rest. Count-based (not age-based) is
  the most predictable answer to "I don't want dozens of folders" and has no
  wall-clock dependency (relevant because the engine forbids `Date.now()`-style
  nondeterminism in some contexts; here it keeps the smoke deterministic).
  Session-id names are ISO `%Y-%m-%dT%H-%M-%S`, so **lexicographic sort ==
  chronological sort** — newest-N is a sorted-name slice, no `stat()` needed.
- **Runs once, at `SessionLogger.__init__`, right after the new dir is
  created.** No background thread, no tick hook — matches the repo's "no new
  thread/lock" ethos and means pruning happens exactly when a new folder is
  added (the only moment the count can grow). The just-created current session
  is always the newest, so keep-N (N ≥ 1) can never delete it; an explicit
  `!= self.session_id` guard is added anyway.
- **Surgical target selection.** Only remove entries where **all** hold:
  (a) it is a directory, (b) `os.path.isdir`, (c) its basename fully matches
  `^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$` (a dedicated compiled `SESSION_DIR_RE`,
  not a loose prefix check), (d) it is not the current session. Everything else
  in `logs/` is ignored.
- **Config knob.** Module-level `LOG_RETENTION_SESSIONS = 20` in server.py
  beside `SessionLogger`, with an optional `SIM_LOG_RETENTION` env override
  (parsed defensively; bad value → fall back to the constant). `0` (or negative)
  **disables pruning entirely** — an explicit opt-out for anyone who wants to
  keep everything (e.g. mid-investigation).
- **Never-raise contract.** Pruning is wrapped so it can never abort server
  startup, mirroring `_append`'s existing `try/except OSError: pass` ("Logging
  must never break the simulation"). Best-effort per directory: a failure
  deleting one (permission, a file held open by another process) is swallowed
  and the loop continues to the next — one un-deletable folder never blocks the
  rest.
- **Deletion uses `shutil.rmtree`** (session dirs contain the four JSONL streams
  plus the per-session `memory.json`, which is an inspection artifact only —
  `state.db` is the authoritative memory store, `specs/12-ops.md:36-45`, so
  discarding old `memory.json` loses nothing load-bearing).

Sketch (illustrative, not final code — the implementer owns exact form):

```python
SESSION_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")
LOG_RETENTION_SESSIONS = int(os.environ.get("SIM_LOG_RETENTION", "20") or 20)

def _prune_old_sessions(self, logs_root):
    keep = LOG_RETENTION_SESSIONS
    if keep <= 0:
        return  # retention disabled
    try:
        names = sorted(
            n for n in os.listdir(logs_root)
            if SESSION_DIR_RE.match(n)
            and os.path.isdir(os.path.join(logs_root, n))
        )  # lexicographic == chronological
    except OSError:
        return
    stale = [n for n in names[:-keep] if n != self.session_id]
    for name in stale:
        try:
            shutil.rmtree(os.path.join(logs_root, name))
        except OSError:
            pass  # best-effort; never break startup
```

## 4. Alternatives considered (recorded so they aren't re-proposed)

- **Switch to one appendable log** (the user's first instinct). Rejected:
  breaks per-session forensics — `llm.jsonl` "what did the model return this
  run", the `session_start` marker, `soak_monitor.py`'s "newest session
  directory" selection, and clean before/after boundaries across restarts all
  assume one folder per run. Bounded retention keeps every one of those and
  still stops the pile-up.
- **Age-based retention** (delete dirs older than N days). Viable, but count is
  more predictable for this workload (restart frequency varies wildly; a quiet
  week then a soak day shouldn't behave differently) and needs no clock. Noted
  as a one-constant swap if the user later prefers it; the two could even
  compose (keep max-N AND max-age) but that's out of scope for v1.
- **Prune on a timer / tick.** Rejected: adds a thread or tick hook for a
  problem that only changes state at startup. Startup-time pruning is
  sufficient and simpler.
- **Size-cap the whole `logs/` tree.** Rejected: requires walking/`stat`-ing
  every file each start and interacts badly with the loose non-session
  artifacts we must not touch.

## 5. Phases & subagent dispatch

Small, self-contained change — two phases, each dispatched to one `implementer`
subagent (Sonnet 5). Specs first (SDD), per CLAUDE.md.

### Phase 0 — Spec-first
- `specs/12-ops.md` "SessionLogger" section: document the retention policy —
  `LOG_RETENTION_SESSIONS` + `SIM_LOG_RETENTION` env override, keep-N-newest
  semantics, the session-id-regex-only targeting, the loose-file/non-session-dir
  exclusion guarantee, `0`-disables, and the never-raise/best-effort contract.
- No other spec owns this; note it in `specs/00-overview.md` only if the
  ownership map needs a pointer (it already assigns `SessionLogger` to
  `specs/12-ops.md`, so likely no change).
- **Deliverable:** spec diff only.

### Phase 1 — Implement + deterministic smoke
- Add `SESSION_DIR_RE`, `LOG_RETENTION_SESSIONS`, the env parse, and
  `_prune_old_sessions()`; call it from `SessionLogger.__init__` after
  `os.makedirs`. Add `import shutil`/`import re` if not present.
- **Validation script → file:** `scripts/log_retention_smoke.py` (deterministic,
  **no server/Ollama** — operates on a throwaway temp `logs/` tree it builds
  itself, never the real one). It seeds a fixture and asserts:
  1. keep-N-newest keeps exactly the N newest session dirs, deletes older ones;
  2. **loose files survive** — plants `soak-x.json`, `path1_soak_x.json`,
     `foo.db` in the root and asserts all still present after pruning;
  3. **non-session dirs survive** — plants `replay_bench/` and a
     `not-a-session/` dir, asserts untouched;
  4. the current session dir is never deleted even when it would fall outside N;
  5. `LOG_RETENTION_SESSIONS = 0` prunes nothing;
  6. best-effort: a simulated un-deletable dir doesn't abort or skip the others;
  7. lexicographic-order correctness across a day boundary
     (`...T23-59-59` vs next-day `...T00-00-01`).
  Writes `scripts/out/log_retention_smoke.json` (top-level `pass` +
  per-assertion detail), matching the existing smoke-artifact convention so
  completion is checkable from a file.
- Sanity: `uv run python scripts/sid_parity_smoke.py` and `path1_smoke.py`
  still pass (they touch `SessionLogger` indirectly via `server` import).

## 6. Acceptance criteria

| MUST | Covered by |
|---|---|
| Old session folders no longer accumulate unbounded across restarts | §3 keep-N-newest at init |
| Loose files in `logs/` root (`soak-*.json`, `path1_soak_*`, `*.db`) never deleted | §2 constraint, §3 regex-only targeting, Phase 1 smoke assertion 2 |
| Non-session subdirs (`replay_bench/`) never deleted | §3 targeting, Phase 1 smoke assertion 3 |
| Current run's own folder never deleted | §3 `!= self.session_id` guard, smoke assertion 4 |
| Retention is configurable and can be fully disabled | §3 `LOG_RETENTION_SESSIONS` / `SIM_LOG_RETENTION`, `0` disables, smoke assertion 5 |
| Pruning can never break server startup | §3 never-raise/best-effort, smoke assertion 6 |
| Spec matches code (SDD) | Phase 0 |
| Long-to-verify step produces a reviewable file | `scripts/out/log_retention_smoke.json` |

## 7. Open questions

- **Default N.** Plan assumes `LOG_RETENTION_SESSIONS = 20`. Higher keeps more
  history; lower is tidier. One-line change — confirm the number.
- **Count vs. age.** Defaulting to count-based per §4; say the word if you'd
  rather prune by age (or both).
- **One-time backfill.** This plan only bounds *future* growth. The current
  tree is already cleaned (this session). If a stale pile ever rebuilds before
  this lands, the first server start after implementation prunes it down to N
  automatically — no separate migration needed.
