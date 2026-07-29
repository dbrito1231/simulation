# SPEC 12 — Operations: Logging & Scripts

How the sim is observed and debugged in the absence of a test suite: JSONL
session logs, `/log/*` ingestion, and the `scripts/` toolbox.

**Canonical for:** `SessionLogger`'s file layout and record shapes, the
never-raise logging contract, Ollama's own server log location, and what
each of the `scripts/*.py` tools does and whether it needs Ollama.
**See also:** [04-http-api.md](04-http-api.md) for `/log/event` and
`/log/benchmark` route shapes (not repeated here); [03-cognition.md](03-cognition.md)
for what's inside an LLM request/response payload; [CLAUDE.md](../CLAUDE.md)
for the no-test-suite verification workflow this spec elaborates.

## SessionLogger

`SessionLogger` (server.py:248) is constructed once at import time —
`session_logger = SessionLogger(...)` (server.py:321) — so every server
process (`uv run python simulation/server.py`) gets exactly one session
folder for its lifetime.

- **Folder naming**: `simulation/logs/<session_id>/` where `session_id =
  datetime.now().strftime("%Y-%m-%dT%H-%M-%S")` (server.py:252-253), e.g.
  `simulation/logs/2026-07-15T09-30-00/`. The whole `logs/` tree is
  gitignored.
- **Retention (`docs/plan-log-retention.md`)**: count-based, keep-N-newest.
  Module constant `LOG_RETENTION_SESSIONS = 20` (server.py, beside
  `SessionLogger`), overridable via the `SIM_LOG_RETENTION` env var (parsed
  defensively -- a missing, blank, or malformed value falls back to the `20`
  constant rather than raising at import). `0` (or negative) **disables
  pruning entirely**, an explicit opt-out for mid-investigation runs.
  Pruning runs **once**, inside `SessionLogger.__init__` immediately after
  `os.makedirs(self.dir, exist_ok=True)` creates the current session's own
  directory -- there is no background thread or tick hook; the folder count
  only changes at startup, so that is the only moment pruning needs to run.
  `_prune_old_sessions(logs_root)` lists `logs_root`, keeps only entries that
  are directories **and** whose basename fully matches the dedicated
  `SESSION_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")`
  (a full match, not a loose prefix check), sorts those names (session-id
  names are ISO `%Y-%m-%dT%H-%M-%S`, so lexicographic sort == chronological
  sort -- no `stat()` needed), and deletes every entry beyond the newest
  `LOG_RETENTION_SESSIONS` via `shutil.rmtree`, excluding the current
  session's own id (`self.session_id`) as an explicit belt-and-suspenders
  guard even though the just-created directory is always the newest and so
  can never fall outside the keep-N window for any N >= 1. Everything in
  `logs/` that isn't a session directory is never a candidate: loose files
  in the root (`soak-<label>.json` from `soak_monitor.py`, `path1_soak_*`
  artifacts, ad-hoc `.db` dumps) and non-session subdirectories (e.g.
  `replay_bench/`) are left untouched regardless of age or count, because
  they never match `SESSION_DIR_RE`. Pruning is wrapped so it can **never**
  raise into `__init__` and abort server startup, mirroring `_append`'s
  "logging must never break the simulation" contract: a failure listing
  `logs_root` aborts pruning for that run (nothing deleted), and a failure
  deleting one specific session directory (permission, a file held open by
  another process) is caught and swallowed per-directory so the loop
  continues to the next candidate -- one un-deletable folder never blocks
  the rest. Deleting a session directory also discards its per-session
  `memory.json`; that is safe by the same reasoning as the paragraph below
  (`state.db` is the authoritative memory store across restarts, so an old
  session's `memory.json` is pure disk bloat once its directory ages out of
  the retention window).
- **`/council-llm-log` searches across retained sessions, not just the live
  one**: `frame_tick` is a global counter persisted in `state.db` that never
  resets on restart, so a council meeting's `[start_frame, end_frame]` window
  can fall entirely inside an *older* session's `llm.jsonl` if the server was
  restarted since that meeting happened. The route lists every subdirectory
  of `logs/` matching `SESSION_DIR_RE` (same regex `_prune_old_sessions`
  uses, so it never looks past the retention window), sorts them
  lexicographically (== chronologically), applies the same per-line frame/
  agent/action filter to each directory's `llm.jsonl` via a shared helper
  (`_council_llm_entries_from_file`), and merges + re-sorts all matches by
  `frame_tick`. This is a small, bounded scan (at most `LOG_RETENTION_SESSIONS`
  files) — no attempt is made to guess which single session a frame range
  belongs to via mtime/stat, since simplicity/correctness matters more than
  the marginal cost of reading a few extra small JSONL files.
- **Six JSONL streams**, each created empty on startup (server.py:255-264):
  | File | Written by | Record `type` |
  |---|---|---|
  | `activity.jsonl` | `log_activity(message, frame_tick)` (server.py:286-289) | `"activity"` |
  | `conversation.jsonl` | `log_conversation(sender, recipient, message, frame_tick, kind, outcome)` (server.py:291-303) | `"conversation"` |
  | `llm.jsonl` | `log_lm_exchange(record)` (server.py:305-307) | `"llm"` (sessions predating the Ollama migration, `docs/plan-ollama-migration.md` Phase 5, wrote `lm_studio.jsonl` with `type: "lm_studio"`) |
  | `benchmarks.jsonl` | `log_benchmark(metric, value, frame_tick, detail)` (server.py:309-318) | `"benchmark"` |
  | `divine.jsonl` | `log_divine(intervention_id, request_id, frame_tick, kind, normalized_command, outcome, status, public)` — Sovereign God mode Phase 2 | `"divine"` |
  | `compiler.jsonl` | `log_compiler(prose, model, latency_ms, status, reason, preview_id)` — Sovereign God mode Optional Phase 8 | `"compiler"` |
- Every record passes through `_append()` (server.py:272-284), which stamps
  `ts` (UTC ISO-8601) and `session_id` onto whatever fields the caller
  supplied, then appends one JSON line. The first `conversation.jsonl` line
  is always a synthetic `kind: "session_start"` entry (server.py:265-270).
- **Per-session `memory.json`**: the in-process vector `MemoryStore`
  persists to `session_logger.dir/memory.json` (server.py:620) — debounced
  (`MEMORY_PERSIST_EVERY = 12` stores, server.py:333) plus always-flushed on
  `clean()`/`clear()` (server.py:535-536, 581-588) via atomic
  write-tmp-then-`os.replace` (server.py:611-614). Shape: `{session_id,
  size, entries: [{id, agent, text, salience, kind, tier, frame_tick, ts}]}`
  — the 128-float `vec` is stripped before writing (recomputable, pure disk
  bloat, server.py:600-609). It's a per-session **inspection artifact
  only**, never read back by the running server (state.db carries the
  authoritative memory export across restarts).
- **Record shapes** beyond the common `ts`/`session_id` envelope:
  - `activity`: `{type, message, frame_tick}`.
  - `conversation`: `{type, kind, from, to, message, frame_tick, outcome?}`.
  - `llm`: built per decision call by closure `log_lm(...)`
    (server.py:3010-3035): `{agent_name, frame_tick, latency_ms,
    invention_only, sprite_design_only, high_stakes_reason,
    high_stakes_active, high_stakes_capped, prompt_chars, system_chars,
    nudges_total, nudges_dropped, request, response, http_status, decision,
    error}` — `request` is the exact payload sent (post any slim-retry
    swap), `decision` is the normalized/applied decision or fallback.
  - `benchmark`: `{type, metric, value, frame_tick, detail?}`.
  - `divine`: `{type, intervention_id, request_id, frame_tick, kind,
    normalized_command, outcome, status, public}`. `request_id` is a
    truncated SHA-256 hash of the client-supplied requestId, never the raw
    value; the God token and raw HTTP headers are never written to this (or
    any) stream. Distinct `status` values: `applied`, `cancelled`,
    `expired`, `rejected` (a command that failed apply-time revalidation
    after passing preview), `restore-closed` (a timed effect, providence, or
    private omen already past its `expiresFrame` when `restore_state()`
    rehydrated it), `replaced` (Phase 3: a providence or private omen closed
    because a new one took its slot — logged via `_close_providence`/
    `_close_omen` exactly once, distinct from the separate `applied` record
    the replacement itself writes), and `revoked` (Phase 3: closed early by
    `revoke_guidance`). A `private_omen` record's `outcome`/
    `normalized_command` may carry the omen's text — safe here because
    `divine.jsonl` is a server-side-only artifact, never served over any
    HTTP route; the `public` field (`False` for every `private_omen` record)
    is what gates *that* content out of `/state`, `activity`,
    `conversationLog`, and the Chronicle, not this log. Preview-only calls
    are validation-only and never write a `divine` record — see
    [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel),
    [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-and-providence),
    and [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles).
    Phase 4's `agent_vitals`/`grant_resource`/`structure_condition` write only
    the `applied` status (they are irreversible, one-shot, and `public: True`
    — there is no timed/pending state of theirs for `replaced`/`revoked`/
    `restore-closed` to apply to). Huntable-wildlife god kinds
    (`wildlife_spawn` / `wildlife_despawn` / `wildlife_set_hp` —
    [02-engine-core.md](02-engine-core.md#sovereign-god-mode-wildlife-kinds))
    follow the same irreversible one-shot pattern: each successful apply
    writes exactly one `divine.jsonl` `"applied"` record (`public: True`)
    with `kind` and `normalized_command`/`outcome` describing the spawn,
    despawn, or HP mutation; they never write `replaced`/`revoked`/
    `restore-closed`/`cancelled`. Phase 6's `weather_override` reuses this
    same status vocabulary rather than adding new values: `applied` on entry,
    `expired`/`restore-closed`/`cancelled` on close (all three routed through
    `_close_weather_override`, which — unlike `_close_story_event` — always
    performs the natural-cycle handoff as part of closing, so the log record
    and the handoff are written together), and `replaced` when a
    `replaceEffectId` command supersedes the currently active override (the
    superseded record's `outcome` still carries its own `state`/`districts`,
    distinct from the new `applied` record the replacement itself writes).
    `weather_override` never writes `revoked` — it has no `revoke_guidance`
    counterpart; only `providence`/`private_omen` are revocable that way (see
    [05-world.md](05-world.md#divine-weather-override-sovereign-god-mode-phase-6-weather_override)).
  - `compiler`: `{type, prose, model, latency_ms, status, reason?, preview_id?}`
    — Sovereign God mode Optional Phase 8. `prose` is the operator's
    already-normalized free-text input (never raw, never HTML); `status` is
    `"draft"` (a compiled command passed validation and entered
    `_god_preview_cache`) or `"rejected"` (any failure — rate limit, session
    cap, model timeout/empty response, non-JSON output, wrong `kind`, or a
    `_validate_god_story_event` rejection), with `reason` set only for
    rejections. `preview_id` is set only on a `"draft"` record. The God token
    is never accepted by `log_compiler`'s signature and therefore never
    reaches this stream — the same structural guarantee `divine.jsonl`'s
    `request_id` hashing relies on, but here there is nothing to redact in
    the first place. Every compile attempt that passes the prose-normalization,
    rate-limit, and session-cap gates writes exactly one record here, whether
    it ends in `"draft"` or `"rejected"` (model timeout/empty response,
    non-JSON output, wrong `kind`, or a `_validate_god_story_event`
    rejection). A request rejected by prose normalization, the rate limit, or
    the session cap itself does **not** write a `compiler.jsonl` record (it
    never reaches the model-call path at all) — those rejections are
    reflected in the returned `{compileOk: false, reason}` response and in
    `self._god_compiler_state["compileCount"]` (in-memory only, never logged
    or persisted) instead. See
    [03-cognition.md](03-cognition.md#sovereign-god-mode-optional-phase-8-free-prose-story-compiler).
- **Never-raise contract**: `_append()` wraps its write in
  `try/except OSError: pass` (server.py:279-284, "Logging must never break
  the simulation"); `_persist()` for `memory.json` has the identical guard
  (server.py:615-617). `/log/event` and `/log/benchmark` wrap their entire
  body in `try/except Exception: pass` too (server.py:2194-2233) — a
  malformed browser-origin log POST can never 500 or disturb the sim.
- **`/log/event`**/**`/log/benchmark`** (server.py:2194-2233) let the
  browser forward client-origin events into the same session streams; full
  request/response shapes are in [04-http-api.md](04-http-api.md).
- **Ollama's own server log** (not written by `SessionLogger`, and not
  under `simulation/logs/`) lives at `%LOCALAPPDATA%\Ollama\server.log` —
  token usage and per-request checkpoints, useful alongside `llm.jsonl`.

## Sovereign God mode metrics

When `GOD_MODE_ENABLED`, `_sample_benchmarks()` additionally sets
`lastBenchmarks["intervened"]` (bool, monotonic `false → true` for the
session) and logs a `god_interventions` benchmark record whose `value` is
the current `recentInterventions` count and whose `detail` carries
`{intervened, active_effects, rejected_commands}` — `active_effects` is
`len(activeEvents)` (0 until a `story_event` is applied — Phase 5 is the
first phase that ever populates this list; each closed/expired/cancelled
entry still counts until the bounded ring evicts it), `rejected_commands` is
a session-lifetime counter incremented on every preview/apply rejection
(missing/invalid envelope, expired/tampered preview, digest mismatch,
idempotency conflict, an occupied modifier key without a matching
`replaceEffectId`, or any invalid `story_event` sub-component — Phase 5 adds
no new rejection *category*, only new ways to hit the existing "invalid
envelope" branch).
Redaction discipline for this metric matches every other stream: no token,
no raw requestId, ever.

`recentInterventions` (and therefore this metric's `value`) is a single
shared, bounded ring across every God-mode kind — Phase 4's three miracles
increment the same counter proclamation/providence/private_omen/
revoke_guidance already do, not a separate one. Phase 4 also introduces one
additional in-memory, non-persisted, non-benchmarked counter:
`self._god_grant_session_total` (cumulative `grant_resource` units applied
this process lifetime, bounding it against `GOD_GRANT_SESSION_CAP` — see
[08-systems-economy.md](08-systems-economy.md#sovereign-god-mode-grant_resource-semantics-phase-4)).
It is reset by `reset()` exactly like `self._god_preview_cache`/
`self._god_requests`/`self._god_rejected_count`, and is not itself logged to
any JSONL stream or benchmark.

## Optional Phase 8: free-prose story compiler

`GOD_COMPILER_ENABLED` (sim_engine.py, env-backed `SIM_GOD_COMPILER`, read
once at import — same idiom as `GOD_MODE_ENABLED`, see
[01-architecture.md](01-architecture.md)) gates `engine.god_compile_prose`
and the `/control/god/compile` route in addition to, not instead of,
`GOD_MODE_ENABLED` — both must be true. It ships **off by default** and
should stay off in any deployment this repository has actually measured.

**The contention gate is not cleared by shipping this code.**
`docs/plan-sovereign-god-mode-v2.md`'s "Optional Phase 8" section is explicit
that the compiler "needs its own A/B contention check" and that check "is
not required for a complete structured Storyteller God" — i.e. the rest of
Sovereign God mode (Phases 1-7) is complete and usable with the compiler left
off. The reason the check matters: the compiler routes to `sim-smart`
([03-cognition.md](03-cognition.md#sovereign-god-mode-optional-phase-8-free-prose-story-compiler))
— the SAME tier every agent decision uses — for a call made while holding
the engine's lock, up to `GOD_COMPILER_TIMEOUT_SEC = 10.0` seconds. That is a
plausible new source of decision-latency contention that did not exist
before this phase, and no live measurement of its actual effect has been
performed as part of delivering this phase.

**Recommended measurement protocol** (not yet run — this is what "enabling
`GOD_COMPILER_ENABLED` in a live run" should require first):

1. Start a control run: `SIM_GOD_MODE=1`, `SIM_GOD_TOKEN=<set>`,
   `SIM_GOD_COMPILER=0` (compiler present in capabilities as `enabled:
   false`, never called). Let it run long enough to gather a stable
   `piano_module_drops` rate and decision-latency baseline from
   `benchmarks.jsonl` / `llm.jsonl` (fresh post-restart data only — cached
   restored PIANO reports do not count, per
   [00-overview.md](00-overview.md)'s verification-matrix precedent).
2. Start an otherwise-identical run with `SIM_GOD_COMPILER=1`, and drive
   `/control/god/compile` at its maximum allowed rate (one call every
   `GOD_COMPILER_MIN_INTERVAL_SEC = 5.0` seconds, up to
   `GOD_COMPILER_SESSION_CAP = 60` calls, i.e. sustained for up to 5 minutes
   of wall clock) while the rest of the world runs normally.
3. Compare the two runs' `piano_module_drops` benchmark and decision-latency
   distribution (`llm.jsonl` `latency_ms`, agent-cognition records only —
   `compiler.jsonl`'s own `latency_ms` is a separate, non-comparable metric
   for the compiler call itself). A measurable regression in either metric
   during the compiler-active window is the signal this gate exists to
   catch; only a run showing no such regression justifies flipping
   `GOD_COMPILER_ENABLED` on by default anywhere.

Until that protocol has been run and its result recorded here, treat
`GOD_COMPILER_ENABLED=1` as an experimental, operator-opt-in-only setting —
never a default for any deployment profile.

## Debugging workflow

There is **no automated test suite or linter** in this repo. Verification is
by observation: run the server (own titled window per
[CLAUDE.md](../CLAUDE.md#commands)), watch the browser render, and read the
JSONL logs for the current session. `llm.jsonl` is the **primary
debugging surface** — every record carries the exact `request` payload, the
raw `response`, and the resulting `decision`, answering "what did the model
return, and which fallback (if any) fired" without reproducing the call.
Cross-check `activity.jsonl`/`conversation.jsonl` for the world-visible
effect and `benchmarks.jsonl` for aggregate metrics (specialization index,
rule adherence, meme adoption, memory-store size — see
[09-systems-society.md](09-systems-society.md)). For full determinism
without an Ollama dependency, use the smoke scripts below instead.

## Scripts (`scripts/`, repo root)

| Script | Needs Ollama? | What it does |
|---|---|---|
| `sid_parity_smoke.py` | No | Deterministic smoke harness for Sid-parity Phases 1–3: specialization-need signals, priority/repeal governance, competing memes, belief-biased votes — drives `sim_engine` directly (imports `sim_engine.py`/`roles.json`, no network). Run: `uv run python scripts/sid_parity_smoke.py`. |
| `path1_smoke.py` | No | Deterministic smoke harness for the Path 1 bundle (industry, tool tiers, terrain, diplomacy, pressure loop) — same direct-import approach as `sid_parity_smoke.py`. Run: `uv run python scripts/path1_smoke.py`. |
| `path1_soak.py` | Mode-dependent | "SA-9 Path 1 soak verifier": live soak orchestration + log audit for the 2h mini-soak from the archived Path 1 plan. Subcommands: `report`/`prompt-check`/`audit LOG_DIR` need no Ollama; `run [--duration S] [--agents N]` is a live soak against a running server (Ollama optional, recommended for one check). |
| `blueprint_smoke.py` | No | Deterministic blueprint validation/recovery checks — imports `server.py`/`sim_engine.py` directly to exercise proposal/approval edge cases (e.g. duplicate-effect detection) with no live LLM call. |
| `god_mode_smoke.py` | No | Deterministic smoke harness for Sovereign God mode Phases 2–6 (docs/plan-sovereign-god-mode-v2.md): flag/token gate, preview/idempotency/expiry, the stored-text contract (including hostile-string round-tripping), godState persistence/restore/reset, the five `/control/god/*` routes via a real `server.app.test_client()` (Phase 2); providence/private-omen set/replace/revoke/expire and the divine-vs-directive prompt separation (Phase 3); the three immediate miracles and their shared `irreversible` class (Phase 4); Phase 5's `_divine_modifier`/`story_event` layer — every one of the seven modifier keys at `0.0`/fractional/`1.0`/max, the gather zero-path returning before the carry-cap clamp with `collectSuccesses`/the tool benchmark untouched, `fish_yield_multiplier` replacing (not multiplying with) `gather_yield_multiplier`, a collapsed agent still recovering under an active `0.0` `health_regen_multiplier`, an all-`1.0` run proving byte-identical to a feature-off baseline built the same way, carry-cap and low-ecology-stock boundaries, spoilage never exceeding eligible overflow, one-value-per-key rejection with `replaceEffectId` acceptance, `story_event` atomicity (one invalid sub-component changes nothing) and its full modifiers+primitives+providence composition, expiry closing an event (and its linked providence) exactly once with the modifier stopping exactly at `expiresFrame` before cleanup runs, `god_cancel` closing an active event while refusing an irreversible miracle's id, active events surviving save/restore with an absolute `expiresFrame`, and preview disclosing the divine and custom-rule gather contributions separately; and Phase 6's `weather_override` — forced entry drawing zero RNG (`random.getstate()` byte-identical before/after apply) with `weather["state"]`/`exitFrame`/`districts` set from the operator input, `exitFrame` matching the event's `expiresFrame` exactly, the natural `_tick_weather` never transitioning while an override holds, expiry handoff to the natural cycle's successor for all four overridden states (both `gathering` probability branches forced via a `random.random` monkeypatch), `god_cancel` running the same handoff, unknown-state/unknown-district/`WEATHER_ENABLED=False` rejections, the `consequential` reversibility class with preview disclosing the affected districts and non-ruined at-risk structure count, `replaceEffectId` one-active-override enforcement (closing the previous as `replaced`), save/restore round-tripping an absolute `expiresFrame`, restore-time expiry-plus-handoff firing exactly once, and a seeded-RNG natural-cycle trajectory proving byte-identical whether `GOD_MODE_ENABLED` is off or on-but-unused. Sets `SIM_GOD_MODE`/`SIM_GOD_TOKEN` before importing `sim_engine`/`server`, then toggles other on/off scenarios by monkeypatching the already-imported modules' plain globals (the same idiom `sid_parity_smoke.py` uses for `PIANO_MODULES`/`ALWAYS_ON_MODULES`) rather than re-importing. Never calls `save_state()`/`reset()`/`clear_state()` against the real `state.db`, so it is safe to run alongside a live server process. Run: `uv run python scripts/god_mode_smoke.py`. |
| `llm_replay_bench.py` | Yes | Replay-benchmarks previously-logged decision calls (from a session's `llm.jsonl`, falling back to the pre-rename `lm_studio.jsonl` for old sessions) against Ollama's native `/api/chat` endpoint (ported `docs/plan-ollama-migration.md` Phase 4, 2026-07-24 — ~~formerly targeted LM Studio's OpenAI-compat endpoint~~). Extracts the portable fields (messages, max_tokens, temperature, response_format, the logged `reasoning_effort` marker) from each logged request and rebuilds a real Ollama request body via `simulation/llm_wire.to_ollama_body()` — the same wire-format conversion `server.py` itself uses at its POST call sites (imported, not re-implemented, so the bench can't drift from production). Modes: `--as-logged` (translate the logged `reasoning_effort=="none"` marker 1:1 to `think:false`, otherwise pass sampling/max_tokens through unchanged) and `--patched` (ignore the logged transform and apply the CURRENT production one from `build_decision_payload`: routine turns get `think:false` + `NON_THINKING_SAMPLING`, invention/high-stakes turns get `THINKING_SAMPLING` with thinking left on). Reports median/p90 latency, JSON validity, think-leak (`<think>` in `message.content`), and `prompt_eval_count`/`eval_count`. Always targets `sim-smart` (the decision model). Usage: `uv run python scripts/llm_replay_bench.py --as-logged [--session PATH] [--n N] [--workers N]`; pause the sim server first (`POST /control/pause`) so its own think traffic doesn't contend for `sim-smart`'s `OLLAMA_NUM_PARALLEL` slots and skew latencies; `--workers 1` measures sequential latency, `--workers 3` mirrors `MAX_CONCURRENT_LLM`. Phase 4 results: `ollama_config.md`'s Benchmarking table. |
| `ollama_setup.py` | Yes (configures Ollama itself) | Canonical CLI loader for the sim's Ollama models: sets `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS`/`OLLAMA_FLASH_ATTENTION`/`OLLAMA_KEEP_ALIVE` env vars, restarts the Ollama service, pulls/creates `sim-smart`/`sim-fast` from `ollama/Modelfile.{smart,fast}` (idempotent), warms both, and verifies dual residency via `/api/ps`. Usage: `uv run python scripts/ollama_setup.py` (apply) or `--check` (readback only). Successor to the removed `scripts/lms_load.py`, per `docs/plan-ollama-migration.md` Phase 5. |
| `soak_monitor.py` | No (tails a live session's existing logs) | Read-only Always-on-PIANO soak observer. At start selects the newest session directory, tails its `llm.jsonl` and `benchmarks.jsonl`, prints one progress line every 60 seconds (elapsed, decision count/p50, module failures), and writes `simulation/logs/soak-<label>.json`. The summary separates decision records from module records; reports decision p50/p90, error/fallback rates, literal `module_pulse_work`, note-age and pulse observations, plus three decision prompt module-report samples. It selects `module_refresh_failures` (always-on, rate against dispatched pulse work) or `piano_module_drops` (flag-off, rate against per-module latency counts when emitted, otherwise successful `module_total` plus drops), exposing metric name/count/attempts/rate. Usage: `uv run python scripts/soak_monitor.py --label attempt2 [--minutes 45]`. |

`llm_replay_bench.py` and `ollama_setup.py` are the two LLM-runtime-dependent
tools; the rest are pure Python harnesses against `sim_engine`/`server`
module code or existing log files, safe to run with Ollama offline.
