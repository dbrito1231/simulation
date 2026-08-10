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

`SessionLogger` (`simulation/_server/logging_session.py:61`) is constructed once at import time —
`session_logger = SessionLogger(...)` (server.py:269) — so every server
process (Docker container or `uv run python simulation/server.py`) gets
exactly one session folder for its lifetime. A new container start or native
launch is a new server process and therefore a new `session_id` folder (same
as any server restart today).

- **Docker bind mounts:** when run via the supported Docker path, `simulation/logs/`
  is bind-mounted to the host repo path — JSONL files land at the same
  `simulation/logs/<session_id>/` locations as native runs; no `docker cp` is
  needed for debugging. `state.db` and `memory_store.json` use the same
  host bind-mount pattern (see [CLAUDE.md](../CLAUDE.md#commands)).
- **Folder naming**: `simulation/logs/<session_id>/` where `session_id =
  datetime.now().strftime("%Y-%m-%dT%H-%M-%S")` (`simulation/_server/logging_session.py:65-66`), e.g.
  `simulation/logs/2026-07-15T09-30-00/`. The whole `logs/` tree is
  gitignored.
- **Retention (`docs/archive/plan-log-retention.md`)**: count-based, keep-N-newest.
  Module constant `LOG_RETENTION_SESSIONS = 20` (`simulation/_server/logging_session.py`, beside
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
- **`/council-llm-log` prefers the live session, then older retained sessions
  only when needed**: `frame_tick` is a global counter persisted in
  `state.db` that never resets on restart, so a council meeting's
  `[start_frame, end_frame]` window can fall entirely inside an *older*
  session's `llm.jsonl` if the server was restarted since that meeting
  happened. The route **always scans the live session's `llm.jsonl` first**
  via `_scan_council_llm_file` (shared council filter with
  `_council_llm_entries_from_file`). If the live file's `(min_frame,
  max_frame)` fully covers the requested window, older directories are not
  read. Otherwise it lists every other subdirectory of `logs/` matching
  `SESSION_DIR_RE` (same regex `_prune_old_sessions` uses, so it never
  looks past the retention window), skips files whose cached or computed
  bounds do not overlap the window (`_llm_jsonl_frame_bounds`), applies the
  same per-line filter to the rest, and merges + re-sorts all matches by
  `frame_tick`. Per-file bounds are cached in memory keyed by path with
  mtime+size invalidation so steady-state council lookups after a long run
  typically touch only the live `llm.jsonl`.
- **Six JSONL streams**, each created empty on startup (`simulation/_server/logging_session.py:93-95`):
  | File | Written by | Record `type` |
  |---|---|---|
  | `activity.jsonl` | `log_activity(message, frame_tick)` (`simulation/_server/logging_session.py:144-147`) | `"activity"` |
  | `conversation.jsonl` | `log_conversation(sender, recipient, message, frame_tick, kind, outcome)` (`simulation/_server/logging_session.py:149-161`) | `"conversation"` |
  | `llm.jsonl` | `log_lm_exchange(record)` (`simulation/_server/logging_session.py:163-172`) | `"llm"` (sessions predating the Ollama migration, `docs/archive/plan-ollama-migration.md` Phase 5, wrote `lm_studio.jsonl` with `type: "lm_studio"`) |
  | `benchmarks.jsonl` | `log_benchmark(metric, value, frame_tick, detail)` (`simulation/_server/logging_session.py:181-193`); flushed via `flush_benchmarks()` | `"benchmark"` |
  | `divine.jsonl` | `log_divine(intervention_id, request_id, frame_tick, kind, normalized_command, outcome, status, public)` — Sovereign God mode Phase 2 | `"divine"` |
  | `compiler.jsonl` | `log_compiler(prose, model, latency_ms, status, reason, preview_id)` — Sovereign God mode Optional Phase 8 | `"compiler"` |
- Every record passes through `_append()` (`simulation/_server/logging_session.py:130-142`), which stamps
  `ts` (UTC ISO-8601) and `session_id` onto whatever fields the caller
  supplied, then appends one JSON line. The first `conversation.jsonl` line
  is always a synthetic `kind: "session_start"` entry (`simulation/_server/logging_session.py:96-101`).
- **Per-session `memory.json`**: the in-process vector `MemoryStore`
  persists to `session_logger.dir/memory.json` (mirror_path wired at
  server.py:289, written by `simulation/_server/memory_store.py:401-409`) —
  debounced (`MEMORY_PERSIST_EVERY = 12` stores, `simulation/_server/memory_store.py:28`)
  plus always-flushed on `clean()`/`clear()` (`simulation/_server/memory_store.py:295-317, 361-368`)
  via atomic write-tmp-then-`os.replace` (`simulation/_server/memory_store.py:393-397`). Shape: `{session_id,
  size, entries: [{id, agent, text, salience, kind, tier, frame_tick, ts}]}`
  — the 128-float `vec` is stripped before writing (recomputable, pure disk
  bloat, `simulation/_server/memory_store.py:384-388`). It's a per-session **inspection artifact
  only**, never read back by the running server (state.db carries the
  authoritative memory export across restarts).
- **Record shapes** beyond the common `ts`/`session_id` envelope:
  - `activity`: `{type, message, frame_tick}`.
  - `conversation`: `{type, kind, from, to, message, frame_tick, outcome?}`.
  - `llm`: built per decision call by closure `log_lm(...)`
    (server.py:2038-2078), stripped in `log_lm_exchange` unless full logging
    is enabled. **Default (slim):** `{agent_name, frame_tick, latency_ms,
    invention_only, sprite_design_only, high_stakes_reason,
    high_stakes_active, high_stakes_capped, prompt_chars, system_chars,
    nudges_total, nudges_dropped, response_preview?, http_status, decision,
    error, module?, timeout_s?}` — omits full `request`/`response` bodies;
    `response_preview` is a short excerpt of assistant text when present.
    `decision` is the normalized/applied decision or fallback. PIANO module
    timeout records omit bodies by default and may include `module` /
    `timeout_s`. **`SIM_LLM_LOG_FULL=1`** (env, parsed like other `SIM_*`
    truthy flags — `1`/`true`/`yes`/`on`, read once at import) restores the
    legacy shape with full `request` (exact payload sent, post any slim-retry
    swap) and full `response` (Ollama JSON body). Required for
    `scripts/llm_replay_bench.py`, which replays logged `request.messages`.
    `/council-llm-log` uses slim fields only (`decision`, `invention_only`,
    etc.) and does not need full bodies when `invention_only` is stamped on
    the record.
  - `benchmark`: `{type, metric, value, frame_tick, detail?}`.
- **`benchmarks.jsonl` buffered flush (perf, docs/archive/plan-perf-degradation-fixes.md
  Agent 8):** `log_benchmark` appends to an in-memory buffer (cap
  `BENCHMARK_BUFFER_MAX = 256`, server.py beside `SessionLogger`) instead of
  opening the file per record. `_sample_benchmarks()` ends with
  `flush_benchmarks()` (wired through engine deps as `flush_benchmarks`) so
  each ~20s sample burst becomes one multi-line append. The buffer auto-flushes
  when full; `atexit` and graceful shutdown also call `flush_benchmarks()` so
  event-driven metrics are not lost. Record shape is unchanged — each flushed
  line is still one stamped JSON object with `ts` and `session_id`.
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
    HTTP route; the `public` field (`False` for every `private_omen` and
    `whisper_campaign` parent record, every `agent_sampling` record, every
    `context_mask` record, and every
    `memory_insert` / `memory_delete` / `belief_plant` record) is what gates *that* content out of
    `/state`, `activity`,
    `conversationLog`, and the Chronicle, not this log. Preview-only calls
    are validation-only and never write a `divine` record — see
    [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel),
    [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance),
    and [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-4--bounded-immediate-miracles).
    Phase 4's `agent_vitals`/`grant_resource`/`structure_condition` write only
    the `applied` status (they are irreversible, one-shot, and `public: True`
    — there is no timed/pending state of theirs for `replaced`/`revoked`/
    `restore-closed` to apply to). **Voice binding:** the JSON schema now
    requires `divine_response` (as a non-null object) whenever guidance is
    active for that agent's request (`build_response_format(
    require_divine_response=True)`, [03](03-cognition.md)), but a missing/
    invalid value is still not rejected as a hard fallback —
    `_record_divine_response_adherence` (`mixin_divine_matrix.py:196`)
    records a `divine_response` (valid/genuine or synthetic
    `missing_divine_response`) on **every** think turn while guidance remains
    active and unacknowledged, writing an additional `divine.jsonl` record
    each time with `status: "adherence"`, `kind` `divine_response`, `public:
    true` for public providence guidance and `public: false` when the ack
    targets a private omen (the log carries stance/reason/action only — never
    private omen text), plus `skipCount` (the running consecutive-synthetic
    count for this guidance id, `null` for a genuine response) and `capped`
    (`true` only on the turn that force-acks the guidance after
    `GOD_VOICE_ACK_SKIP_CAP` consecutive synthetic turns). A genuine response
    acks immediately (one adherence record for that guidance id); a synthetic
    one keeps writing adherence records — one per turn — until either a
    genuine response arrives or the skip cap forces the close. Town-integrity kinds `repair_structures` /
    `clear_ruins` ([02-engine-core.md](02-engine-core.md#sovereign-god-mode-town-integrity--mass-structure-repair-and-ruin-clearance))
    follow the same irreversible one-shot pattern: each successful apply writes
    exactly one `divine.jsonl` `"applied"` record (`public: True`) with
    `kind`, `normalized_command`, and `outcome` describing the batch restore
    or ruin deletion (structure ids affected, condition deltas, registry
    removals). Huntable-wildlife god kinds
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
    `status: "adherence"` is Voice-binding only — one record per think turn
    while guidance is active and unacknowledged for that agent (including
    synthetic `missing_divine_response` turns, which now accumulate against
    the skip cap rather than acking immediately — see above); never written
    for Matrix anoint/bush/story soft guidance.
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
    the first place. Every compile attempt that passes prose-normalization,
    rate-limit, and session-cap gates writes exactly one record here. Requests
    rejected before the model-call path do **not** write a `compiler.jsonl`
    record — those rejections are
    reflected in the returned `{compileOk: false, reason}` response and in
    `self._god_compiler_state["compileCount"]` (in-memory only, never logged
    or persisted) instead. See
    [03-cognition.md](03-cognition.md#sovereign-god-mode-optional-phase-8-free-prose-story-compiler).
- **Never-raise contract**: `_append()` wraps its write in
  `try/except OSError: pass` (`simulation/_server/logging_session.py:137-142`, "Logging must never break
  the simulation"); `_persist()` for `memory.json` has the identical guard
  (`simulation/_server/memory_store.py:393-400`). `/log/event` and `/log/benchmark` wrap their entire
  body in `try/except Exception: pass` too (server.py:849-888) — a
  malformed browser-origin log POST can never 500 or disturb the sim.
- **`/log/event`**/**`/log/benchmark`** (server.py:849-888) let the
  browser forward client-origin events into the same session streams; full
  request/response shapes are in [04-http-api.md](04-http-api.md).
- **Ollama's own server log** (not written by `SessionLogger`, and not
  under `simulation/logs/`) lives at `%LOCALAPPDATA%\Ollama\server.log` —
  token usage and per-request checkpoints, useful alongside `llm.jsonl`.

## Sovereign God mode — security contract

The God control plane (`/control/god/*`) uses a **two-gate model**:

1. **`GOD_MODE_ENABLED`** (`constants.py:644`, env `SIM_GOD_MODE`, default on) —
   the master switch. When off, every God route returns the uniform
   `401 {"error": "unauthorized"}` regardless of any token.
2. **`GOD_AUTH_REQUIRED`** (`constants.py:650`, env `SIM_GOD_AUTH`, default **off**)
   — an optional second gate. When off (the shipped default), routes go live
   as soon as the flag is on; no token is read or checked. When on, a
   non-empty `SIM_GOD_TOKEN` (server.py, read once at import) must also be
   configured or routes stay disabled until restart.

**Default posture (auth off):** any client that can reach the HTTP server
can mutate the world through `/control/god/*` without credentials. This is
deliberate for trusted home-LAN deployments where the viewer must be
reachable from phones/tablets on the network.

**Bind host interaction:** the server binds `SIM_HOST` (default
`0.0.0.0` — all interfaces) so LAN devices can load the viewer. With auth
off, that same bind exposes the unauthenticated God API to every device on
the LAN, not just the operator's browser. server.py prints a one-line
**security banner** at import naming the bind host whenever God mode is on
and auth is off, so an unauthenticated God API is never silent. When auth
is required but the token is missing, the prior startup **warning** is
printed instead and routes stay disabled.

**Restoring token gating:** set `SIM_GOD_AUTH=1` (or `true`/`yes`/`on`)
and supply `SIM_GOD_TOKEN=<secret>`, then restart — both are read once at
import. The viewer's Divine Console unlock flow (when auth is on) is
documented in [11-viewer.md](11-viewer.md).

**Audit trail:** every applied God intervention is logged to `divine.jsonl`
regardless of auth mode — unexpected mutations remain attributable after the
fact. The token and raw HTTP headers are never written to any log stream.

**World reset password:** `POST /control/reset` requires a JSON `password`
field checked against `SIM_RESET_PASSWORD` (server.py, read once at import;
defaults to `"reset"` when unset or blank) via `hmac.compare_digest`. This is
independent of God mode token auth — operators can wipe the village from the
viewer Reset button or any HTTP client without `X-God-Token`, but must supply
the reset password. See [04-http-api.md](04-http-api.md) and
[11-viewer.md](11-viewer.md).

**Divine Console chrome:** viewer-only UX polish (sticky preview strip,
intervention count on the bar, pin row, keyboard shortcuts) — permanent default
when God mode is on; no flag gate. No new routes, no `divine.jsonl` shape
changes, no benchmark keys. Requires `GOD_MODE_ENABLED`; when God mode is off
the bar is hidden.

**Future option (not implemented):** a source-IP allowlist on God routes only
(`GOD_ALLOWED_IPS`) could restrict mutation to named hosts while preserving
LAN-wide viewing. See `docs/archive/plan-god-always-unlocked.md` Phase 4-alt.

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
and town-integrity `repair_structures`/`clear_ruins` increment the same
counter proclamation/providence/private_omen/
revoke_guidance already do, not a separate one. Phase 4 also introduces one
additional in-memory, non-persisted, non-benchmarked counter:
`self._god_grant_session_total` (cumulative `grant_resource` units applied
this process lifetime, bounding it against `GOD_GRANT_SESSION_CAP` — see
[08-systems-economy.md](08-systems-economy.md#sovereign-god-mode-grant_resource-semantics-phase-4)).
It is reset by `reset()` exactly like `self._god_preview_cache`/
`self._god_requests`/`self._god_rejected_count`, and is not itself logged to
any JSONL stream or benchmark.

## Optional Phase 8: free-prose story compiler

`GOD_COMPILER_ENABLED` (`constants.py:743`, env-backed `SIM_GOD_COMPILER`, read
once at import — same idiom as `GOD_MODE_ENABLED`, see
[01-architecture.md](01-architecture.md)) gates `engine.god_compile_prose`
and the `/control/god/compile` route in addition to, not instead of,
`GOD_MODE_ENABLED` — both must be true. It ships **off by default** and
should stay off in any deployment this repository has actually measured.

**The contention gate is not cleared by shipping this code.**
`docs/archive/plan-sovereign-god-mode-v2.md`'s "Optional Phase 8" section is explicit
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

1. Start a control run: `SIM_GOD_MODE=1`, `SIM_GOD_AUTH=1`, `SIM_GOD_TOKEN=<set>`,
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

**Measurement status (2026-08-01, Divine Console Phase 11):** the full A/B
contention protocol above was **not run** in this environment (requires two
long-lived comparable server sessions with fresh post-restart
`benchmarks.jsonl` / `llm.jsonl` baselines plus sustained compile load at
max rate). Do **not** infer green A/B results from shipping compiler UX
promotion alone. A lightweight compile-path sanity check may be noted in
`docs/HANDOFF.md` when Ollama was reachable; that is not a substitute for
the protocol.

**Viewer UX status:** when `capabilities.compiler.enabled` is true (both
`GOD_MODE_ENABLED` and `SIM_GOD_COMPILER=1`), the Compile bar button and
modal are **supported experimental** — solid chrome (no dashed “dark”
styling), help text cites `SIM_GOD_COMPILER=1`, and a successful compile
hands off into Story form fields plus the sticky preview strip so the
operator can Apply without re-Previewing. Default remains **off**
(`GOD_COMPILER_ENABLED` false at import unless the env var is set).

## Debugging workflow

There is **no automated test suite or linter** in this repo. Verification is
by observation: run the server (Docker foreground container or native — own
titled window per [CLAUDE.md](../CLAUDE.md#commands)), watch the browser
render, and read the JSONL logs for the current session on the host under
`simulation/logs/<session_id>/`. `llm.jsonl` is the **primary
debugging surface** — every record carries the exact `request` payload, the
raw `response`, and the resulting `decision`, answering "what did the model
return, and which fallback (if any) fired" without reproducing the call.
Cross-check `activity.jsonl`/`conversation.jsonl` for the world-visible
effect and `benchmarks.jsonl` for aggregate metrics (specialization index,
rule adherence, meme adoption, memory-store size — see
[09-systems-society.md](09-systems-society.md)). For full determinism
without an Ollama dependency, use the smoke scripts below instead.

## Viewer static assets

The thin viewer loads a few files from the Flask app beside `index.html`
(see [04-http-api.md](04-http-api.md)):

| Path | File | Notes |
|---|---|---|
| `/viewer/setup.js` | `simulation/viewer/setup.js` | Required — viewer 1/16: boot/canvas setup, zoom, feature flags |
| `/viewer/state.js` | `simulation/viewer/state.js` | Required — viewer 2/16: world snapshot (`MOCK_STATE`, `world`), delta merge, districts cache |
| `/viewer/render.js` | `simulation/viewer/render.js` | Required — viewer 3/16: convenience accessors + drawing (terrain cache, weather/night overlays, agents, structures) |
| `/viewer/sidebar.js` | `simulation/viewer/sidebar.js` | Required — viewer 4/16: sidebar render (Civilization/Agents panels, `ACTION_LABELS`, benchmarks, world clock HUD) |
| `/viewer/council.js` | `simulation/viewer/council.js` | Required — viewer 5/16: Council panel, Council Assembly modal, settlements |
| `/viewer/minimap.js` | `simulation/viewer/minimap.js` | Required — viewer 6/16: minimap render + navigation |
| `/viewer/polling.js` | `simulation/viewer/polling.js` | Required — viewer 7/16: `/state` polling, flag sync, social ties/wildlife/shipment drawing |
| `/viewer/controls.js` | `simulation/viewer/controls.js` | Required — viewer 8/16: Pause/Resume/Reset controls |
| `/viewer/renderloop.js` | `simulation/viewer/renderloop.js` | Required — viewer 9/16: `tick`/`tickBody` render loop |
| `/viewer/divine-bootstrap.js` | `simulation/viewer/divine-bootstrap.js` | Required — viewer 10/16: Divine Console state, DOM refs, feature registry/guide, agent/pin selects |
| `/viewer/divine-auth-sight.js` | `simulation/viewer/divine-auth-sight.js` | Required — viewer 11/16: Divine Console auth/fetch plumbing, Sight overlays/diff, bar effects/pips, preview controller, favorites |
| `/viewer/divine-modal.js` | `simulation/viewer/divine-modal.js` | Required — viewer 12/16: Divine Console bottom bar/modal/tabs, tooltip engine, preview→apply generic wiring |
| `/viewer/divine-sight-voice.js` | `simulation/viewer/divine-sight-voice.js` | Required — viewer 13/16: Sight tab render + checkpoint restore, Voice presets |
| `/viewer/divine-voice.js` | `simulation/viewer/divine-voice.js` | Required — viewer 14/16: Voice tab (proclamation/providence/private omen, whisper/crowd/dream, bargain/oracle/architect) |
| `/viewer/divine-miracles-story.js` | `simulation/viewer/divine-miracles-story.js` | Required — viewer 15/16: Miracles tab, shared modifier editor, Story/Compile/Laws tabs |
| `/viewer/divine-history.js` | `simulation/viewer/divine-history.js` | Required — viewer 16/16: History power tools, gate/passive refresh, public banner, `renderDivineConsole` entry point, poll/render loop kickoff |
| `/css/base.css` | `simulation/css/base.css` | Required — stylesheet 1/6: reset, `#wrap`/`#canvasWrap`/`#world`, map controls, `#worldClockHud`, `#minimap` |
| `/css/panels.css` | `simulation/css/panels.css` | Required — stylesheet 2/6: `#sidebar`/`#convPanel` shared chrome, `#civPanel` civilization stats |
| `/css/agents.css` | `simulation/css/agents.css` | Required — stylesheet 3/6: `#agentList`/`#agentRollup`/`#agentDetail`, deceased-agents modal |
| `/css/council.css` | `simulation/css/council.css` | Required — stylesheet 4/6: council transcript modal, conversation/activity/chronicle lists, council banner/panel, Daily Council Assembly modal |
| `/css/divine.css` | `simulation/css/divine.css` | Required — stylesheet 5/6: Divine Console bottom bar and modal, `#tooltip`, `#godPublicBanner` |
| `/css/responsive.css` | `simulation/css/responsive.css` | Required — stylesheet 6/6: the two `@media` breakpoint blocks |
| `/sprites/core.js` | `simulation/sprites/core.js` | Required — Canvas renderer, part 1/8: shared state (`spriteSeason`), pixel-grid primitives, snow-cap helper, road-edge path cells |
| `/sprites/tiles.js` | `simulation/sprites/tiles.js` | Required — Canvas renderer, part 2/8: color palette `C`, path-blend tiles, terrain `TILE_*` grids, ocean tile builder |
| `/sprites/props.js` | `simulation/sprites/props.js` | Required — Canvas renderer, part 3/8: starter-world decor (trees, crops, fences, dock, well, rocks, decorative house/market-stall/cave-entrance) |
| `/sprites/structures.js` | `simulation/sprites/structures.js` | Required — Canvas renderer, part 4/8: agent-built structure grids, wear/ruin rendering, forge smoke, weather-particle and activity-dust helpers |
| `/sprites/agents.js` | `simulation/sprites/agents.js` | Required — Canvas renderer, part 5/8: agent sprite palettes/grids, accessories, tombstones, belief tints |
| `/sprites/world.js` | `simulation/sprites/world.js` | Required — Canvas renderer, part 6/8: district tile compositing, starter district list, `drawTiledWorld` |
| `/sprites/wildlife.js` | `simulation/sprites/wildlife.js` | Required — Canvas renderer, part 7/8: ambient wildlife (PNG sheet, canvas-helper, and procedural-grid fallbacks) |
| `/sprites/shipments.js` | `simulation/sprites/shipments.js` | Required — Canvas renderer, part 8/8: goods-in-motion cart/boat sprites |
| `/wildlife.png` | `simulation/wildlife.png` | Wildlife spritesheet (variable-size atlas from user PNGs). When absent (404), `sprites/wildlife.js` keeps `_wildlifeSheetReady = false` and draws canvas helpers / procedural `WILDLIFE_SPRITES` grids — first paint is never blocked. |
| `/wildlife_refsheet.html` | `simulation/wildlife_refsheet.html` | Dev/debug only — labeled 4×4 grid calling live `drawWildlifeCreature`; not part of the sim viewer loop. |

**Wildlife art provenance:** User-provided PNGs in `simulation/assets/wildlife/` (16 kinds: `bee`, `bird`, `boar`, `chicken`, `cow`, `crab`, `deer`, `fish`, `fox`, `gull`, `mouse`, `owl`, `rabbit`, `seal`, `squirrel`, `turtle`). `bee` replaces the former decorative farm kind `butterfly` (same role — not huntable). `cow` replaces the former farm kind `grazer` (save migration in `_normalize_wildlife_records`). Rebuild atlas: `uv run python scripts/build_wildlife_sheet.py` (keys square backdrops via border flood-fill, trims transparency; `bee.png` mint-green backdrop keyed from corner samples). Committed outputs: `wildlife.png`, source PNGs, and the build script. Preview (gitignored): `simulation/_vendor/wildlife-preview-4x.png`.

## Scripts (`scripts/`, repo root)

| Script | Needs Ollama? | What it does |
|---|---|---|
| `sid_parity_smoke.py` | No | Deterministic smoke harness for Sid-parity Phases 1–3: specialization-need signals, priority/repeal governance, competing memes, belief-biased votes — drives `sim_engine` directly (imports the `sim_engine` package/`roles.json`, no network). Run: `uv run python scripts/sid_parity_smoke.py`. |
| `path1_smoke.py` | No | Deterministic smoke harness for the Path 1 bundle (industry, tool tiers, terrain, diplomacy, pressure loop) — same direct-import approach as `sid_parity_smoke.py`. Run: `uv run python scripts/path1_smoke.py`. |
| `path1_soak.py` | Mode-dependent | "SA-9 Path 1 soak verifier": live soak orchestration + log audit for the 2h mini-soak from the archived Path 1 plan. Subcommands: `report`/`prompt-check`/`audit LOG_DIR` need no Ollama; `run [--duration S] [--agents N]` is a live soak against a running server (Ollama optional, recommended for one check). |
| `blueprint_smoke.py` | No | Deterministic blueprint validation/recovery checks — imports `server.py`/the `sim_engine` package directly to exercise proposal/approval edge cases (e.g. duplicate-effect detection) with no live LLM call. |
| `god_mode_smoke.py` | No | Deterministic smoke harness for Sovereign God mode Phases 2–6 plus Divine Matrix Phases 1–10 (docs/archive/plan-sovereign-god-mode-v2.md, docs/archive/plan-divine-matrix-interventions.md): flag/auth gate, preview/idempotency/expiry, the stored-text contract (including hostile-string round-tripping), godState persistence/restore/reset, the five `/control/god/*` routes via a real `server.app.test_client()` (Phase 2); **Voice binding** — `divine_response` schema-required (non-null object) while guidance unacked (`build_response_format(require_divine_response=True)`), `follow` clears goal/assignedTask, missing/invalid synthesizes `continue` + `missing_divine_response` without rejecting the action, a synthetic (non-genuine) response no longer auto-acks — it increments a per-guidance skip counter (providence `skipCounts`, omen `skipCount`) and only force-acks (`capped: true`) after `GOD_VOICE_ACK_SKIP_CAP` consecutive synthetic turns for the same guidance id, sprite/invention special turns cancelled (not deferred) on Voice apply and while guidance unacked, proclamation auto-applies as providence (same slot/duration/revoke), `recentDivineResponses` in Sight only, adherence `divine.jsonl` `status: "adherence"` records; providence/private-omen set/replace/revoke/expire and the divine-vs-directive prompt separation (Phase 3); whisper campaigns, agent sampling, memory surgery, context masks (Matrix Phases 1–4); **decision gate / possession** — compulsion forces pinned `rest`, possession skips `llm_decide`, veto hold+resolve, Sage `_rush_to_heal` bypass, veto hold cap, `decisionGates` privacy (Matrix Phase 5); **Burning Bush / bargain** — thread privacy, bargain success grant, expiry failure path (Matrix Phase 6); **Anointed** — destiny/oracle target-only prompt, stigmata in neighbor `nearby_agents`, oracle gated by `revealFrame`, revoke clears, no `/state` leak (Matrix Phase 7); **Identity Forge** — role swap changes `role_skill` in think payload, copy progresses across N thinks, cancel restores snapshot, `identityForges` privacy (Matrix Phase 8); **Architect Zones** — door blocks move without `godKeys` and allows with key, limbo sets `divineHold` and release restores, paint cancel reverts terrain, `architectZones`/key privacy (Matrix Phase 9); **Reload checkpoints** — create → mutate → restore roundtrip on temp `DB_PATH`/`GOD_CHECKPOINT_ROOT`, cap reject + `replaceOldest`, Sight summaries without path leak, `deja_vu_replay` rejects when flag off (Matrix Phase 10); **Déjà Vu replay** — `GOD_STATE_VERSION` 3 default/normalize, digest capture on gated apply, apply/cancel when `GOD_DEJA_VU_REPLAY` on, digests not in `/state` (Divine Console Phase 8, docs/archive/plan-divine-console-improvements.md); the three immediate miracles and their shared `irreversible` class (Phase 4); Phase 5's `_divine_modifier`/`story_event` layer — every one of the seven modifier keys at `0.0`/fractional/`1.0`/max, the gather zero-path returning before the carry-cap clamp with `collectSuccesses`/the tool benchmark untouched, `fish_yield_multiplier` replacing (not multiplying with) `gather_yield_multiplier`, a collapsed agent still recovering under an active `0.0` `health_regen_multiplier`, an all-`1.0` run proving byte-identical to a feature-off baseline built the same way, carry-cap and low-ecology-stock boundaries, spoilage never exceeding eligible overflow, one-value-per-key rejection with `replaceEffectId` acceptance, `story_event` atomicity (one invalid sub-component changes nothing) and its full modifiers+primitives+providence composition, expiry closing an event (and its linked providence) exactly once with the modifier stopping exactly at `expiresFrame` before cleanup runs, `god_cancel` closing an active event while refusing an irreversible miracle's id, active events surviving save/restore with an absolute `expiresFrame`, and preview disclosing the divine and custom-rule gather contributions separately; and Phase 6's `weather_override` — forced entry drawing zero RNG (`random.getstate()` byte-identical before/after apply) with `weather["state"]`/`exitFrame`/`districts` set from the operator input, `exitFrame` matching the event's `expiresFrame` exactly, the natural `_tick_weather` never transitioning while an override holds, expiry handoff to the natural cycle's successor for all four overridden states (both `gathering` probability branches forced via a `random.random` monkeypatch), `god_cancel` running the same handoff, unknown-state/unknown-district/`WEATHER_ENABLED=False` rejections, the `consequential` reversibility class with preview disclosing the affected districts and non-ruined at-risk structure count, `replaceEffectId` one-active-override enforcement (closing the previous as `replaced`), save/restore round-tripping an absolute `expiresFrame`, restore-time expiry-plus-handoff firing exactly once, and a seeded-RNG natural-cycle trajectory proving byte-identical whether `GOD_MODE_ENABLED` is off or on-but-unused. Sets `SIM_GOD_MODE`/`SIM_GOD_TOKEN`/`SIM_GOD_AUTH=1` before importing `sim_engine`/`server`, then toggles other on/off scenarios by monkeypatching the already-imported modules' plain globals (the same idiom `sid_parity_smoke.py` uses for `PIANO_MODULES`/`ALWAYS_ON_MODULES`) rather than re-importing. Never calls `save_state()`/`reset()`/`clear_state()` against the real `state.db`, so it is safe to run alongside a live server process. Run: `uv run python scripts/god_mode_smoke.py`. |
| `llm_replay_bench.py` | Yes | Replay-benchmarks previously-logged decision calls (from a session's `llm.jsonl`, falling back to the pre-rename `lm_studio.jsonl` for old sessions) against Ollama's native `/api/chat` endpoint (ported `docs/archive/plan-ollama-migration.md` Phase 4, 2026-07-24 — ~~formerly targeted LM Studio's OpenAI-compat endpoint~~). Extracts the portable fields (messages, max_tokens, temperature, response_format, the logged `reasoning_effort` marker) from each logged request and rebuilds a real Ollama request body via `simulation/llm_wire.to_ollama_body()` — the same wire-format conversion `server.py` itself uses at its POST call sites (imported, not re-implemented, so the bench can't drift from production). Modes: `--as-logged` (translate the logged `reasoning_effort=="none"` marker 1:1 to `think:false`, otherwise pass sampling/max_tokens through unchanged) and `--patched` (ignore the logged transform and apply the CURRENT production one from `build_decision_payload`: routine turns get `think:false` + `NON_THINKING_SAMPLING`, invention/high-stakes turns get `THINKING_SAMPLING` with thinking left on). Reports median/p90 latency, JSON validity, think-leak (`<think>` in `message.content`), and `prompt_eval_count`/`eval_count`. Always targets `sim-smart` (the decision model). Usage: `uv run python scripts/llm_replay_bench.py --as-logged [--session PATH] [--n N] [--workers N]`; pause the sim server first (`POST /control/pause`) so its own think traffic doesn't contend for `sim-smart`'s `OLLAMA_NUM_PARALLEL` slots and skew latencies; `--workers 1` measures sequential latency, `--workers 3` mirrors `MAX_CONCURRENT_LLM`. Phase 4 results: `ollama_config.md`'s Benchmarking table. |
| `ollama_setup.py` | Yes (configures Ollama itself) | Canonical CLI loader for the sim's Ollama models: sets `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS`/`OLLAMA_FLASH_ATTENTION`/`OLLAMA_KEEP_ALIVE` env vars, restarts the Ollama service, pulls/creates `sim-smart`/`sim-fast` from `ollama/Modelfile.{smart,fast}` (idempotent), warms both, and verifies dual residency via `/api/ps`. Usage: `uv run python scripts/ollama_setup.py` (apply) or `--check` (readback only). Successor to the removed `scripts/lms_load.py`, per `docs/archive/plan-ollama-migration.md` Phase 5. |
| `determinism_proof.py` | No | Emergence Breakthroughs Phase A0/A1 — determinism probe for Feature 5: two identical headless `SimEngine` runs with stubbed `llm_decide` (`rest`), benchmark trajectories captured via list-capturing `log_benchmark`. **Modes:** `a0` (benchmark-only baseline, default); hard cases `unseeded-twin` (no `random.seed` between twin runs — exposes process-global RNG carryover via world-state fingerprint), `no-drain` (skip async drain), `tick-thread` (`engine.start()` loop), `checkpoint` (save/restore twin fork from in-memory checkpoint bytes). Reports `DETERMINISM: BIT-IDENTICAL YES/NO` and first-diff details (benchmark metric and/or world fingerprint). **`--pin`** enables `DETERMINISM_PINNING` (re-seed at engine init, synchronous sorted think drain per tick, frameTick-based LLM gap/cooldown) — required for `unseeded-twin` to pass. Flag off leaves live sim behavior unchanged; `a0` still passes without `--pin`. **Limitations:** proof/probe only — not a one-variable A/B harness (`fork_compare.py`); live LLM runs out of scope; `no-drain` / `tick-thread` / `checkpoint` stay bit-identical when each run re-seeds (only `unseeded-twin` reliably diverges without pin). Uses temp `state.db` only — never touches `simulation/state.db`. Run: `uv run python scripts/determinism_proof.py [--mode MODE] [--pin] [--ticks N] [--roster N] [--seed N]`. |
| `fork_compare.py` | No | Emergence Breakthroughs Phase A2 — Feature 5 fork harness: cold-start (or optional `--checkpoint-prep-ticks N` in-memory restore) two headless `SimEngine` runs with stubbed `llm_decide` (`rest`), fork B differing from baseline fork A in **exactly one** `sim_engine` module flag or documented harness knob (`--var NAME=VALUE`). Compares **benchmark trajectories and world fingerprint** (both required — benchmarks alone can mask divergence under `rest` stubs). **`--identical`** sanity mode: two identical forks must be bit-identical (implies `--pin` / `DETERMINISM_PINNING`). **`--pin`** enables the same pinning surface as `determinism_proof.py --pin` — required for the identical-input bit-identical guarantee. Optional `--out DIR` writes `fork_compare_summary.json` plus per-fork benchmark JSONL (not under `simulation/logs/`). **Limitations:** harness only — not equivalent to live or replayed LLM forks; one-variable diffs are observational (readable diff, not pass/fail unless `--identical`). Never writes `simulation/state.db` (temp dirs only). Run: `uv run python scripts/fork_compare.py --ticks N [--roster N] [--pin] (--identical | --var FLAG=true) [--seed N] [--checkpoint-prep-ticks N] [--out DIR]`. |
| `peer_model_smoke.py` | No | Emergence Breakthroughs F2 — deterministic Theory of Mind smoke: flag-off shape (no `peerModel`), `PEER_MODEL_*` caps + LRU eviction, module drop/timeout leaves prior model intact, `peer_prediction_accuracy` scoring, `peerModel` save/restore round-trip, nearby `[think: …]` prompt fold-in, piano stagger swap (`theory_of_mind` replaces social every 4th module-tick). Run: `uv run python scripts/peer_model_smoke.py`. |
| `testament_smoke.py` | No | Emergence Breakthroughs F1 — deterministic Testament smoke: flag-off shape (no testament ring / no death merge), deathbed wiki fold + dedupe + `WIKI_SECTION_CHAR_CAP`, ring cap at `TESTAMENT_CAP`, newborn `memoryWiki` inheritance from parents + newest testament lines, bounded `format_testament_prompt_line` slice, testament save/restore round-trip. Run: `uv run python scripts/testament_smoke.py`. |
| `contract_smoke.py` | No | Emergence Breakthroughs F3.2 — deterministic contracts/escrow smoke: flag-off apply no-op, coin conservation across offer/fulfill/default/expiry (`_total_tracked_coin`), open contracts + escrow save/restore round-trip. Run: `uv run python scripts/contract_smoke.py`. |
| `soak_monitor.py` | No (tails a live session's existing logs) | Read-only Always-on-PIANO soak observer. At start selects the newest session directory, tails its `llm.jsonl` and `benchmarks.jsonl`, prints one progress line every 60 seconds (elapsed, decision count/p50, module failures), and writes `simulation/logs/soak-<label>.json`. The summary separates decision records from module records; reports decision p50/p90, error/fallback rates, literal `module_pulse_work`, note-age and pulse observations, plus three decision prompt module-report samples. It selects `module_refresh_failures` (always-on, rate against dispatched pulse work) or `piano_module_drops` (flag-off, rate against per-module latency counts when emitted, otherwise successful `module_total` plus drops), exposing metric name/count/attempts/rate. Usage: `uv run python scripts/soak_monitor.py --label attempt2 [--minutes 45]`. |
| `tom_contention_soak.py` | Yes (starts/stops native `simulation/server.py` twice; Ollama recommended) | F2 Theory of Mind contention gate orchestrator: refuses if Docker `gitserv-sim` is running, any native `simulation/server.py` or soak harness (`tom_contention_soak`/`soak_monitor`) is active, or port `SIM_PORT` (default 5001) is occupied/listening; runs matched native server soaks flag-off then `SIM_THEORY_OF_MIND=1`, each observed by `soak_monitor.py` (`--label tom-baseline` / `tom-flagon`). Starts the server with `sys.executable` (not `uv run`) and stops via process-tree kill (`taskkill /T /F` on Windows, process-group signals on Unix) plus a sweep of orphan `simulation/server.py` PIDs — terminating only the `uv` wrapper on Windows leaves a child `python.exe` serving stale `/state` (wrong `THEORY_OF_MIND_ENABLED` on the flag-on phase). Between phases waits until `/state` is unreachable (hard-fail on timeout); after each start hard-asserts `config.flags.THEORY_OF_MIND_ENABLED` via `/state` matches the phase (`False` baseline, `True` flag-on) and ties readiness to a new log session before `soak_monitor` runs. On exit/failure, `cleanup_all_native_sim_servers` kills native servers and port listeners. Writes per-phase `simulation/logs/soak-tom-baseline.json` and `soak-tom-flagon.json`, combined `simulation/logs/tom-contention-soak-result.json`, and progress to `simulation/logs/tom-contention-soak.log`. `--flagon-only` skips baseline (requires existing `soak-tom-baseline.json`), reruns flag-on only, and rewrites the combined result merging preserved baseline + new flagon. Does not flip `THEORY_OF_MIND_ENABLED` default in code. Usage: `uv run python scripts/tom_contention_soak.py [--minutes 45]` or `--flagon-only`. |

`tom_contention_soak.py` process hygiene: always stop native soak servers with
process-tree kill, not `Popen.terminate()` on a `uv run` parent alone. On
Windows the child `python.exe` running `simulation/server.py` can survive as an
orphan and keep serving `/state` with the prior phase's flags; the next phase
then probes the stale server and hard-asserts fail (e.g. flag-on expecting
`THEORY_OF_MIND_ENABLED=True` while the orphan still reports `False`). Prefer
`sys.executable simulation/server.py` for soak starts; use `taskkill /PID … /T
/F` or equivalent and sweep `simulation/server.py` PIDs before the next phase.

`llm_replay_bench.py` and `ollama_setup.py` are the two LLM-runtime-dependent
tools; the rest are pure Python harnesses against `sim_engine`/`server`
module code or existing log files, safe to run with Ollama offline.
