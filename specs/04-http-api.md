# SPEC 04 — HTTP API

The Flask route surface: every endpoint the browser or external tools call,
what it does, and its request/response shape.

**Canonical for:** the full route table (28 routes), `/state` top-level
payload key inventory, server startup/shutdown behavior. **See also:**
[specs/01-architecture.md](01-architecture.md) (data flow, thin-viewer
contract), [specs/03-cognition.md](03-cognition.md) (what `run_agent_decision`
does — not repeated here), [specs/11-viewer.md](11-viewer.md) (polling
cadence and rendering), [specs/12-ops.md](12-ops.md) (log file formats/
retention for the `/log/*` and `/council-llm-log` endpoints).

## Route table

28 routes total (`@app.route` count in `simulation/server.py`; no other
route-registration mechanism is used) — 23 always-registered routes plus the
5 `/control/god/*` routes added in Phase 2, which are registered
unconditionally but only ever *answer* requests when `GOD_MODE_ENABLED`
(sim_engine.py) is configured at startup and, when `GOD_AUTH_REQUIRED` is
True (default False), a non-empty `SIM_GOD_TOKEN` (server.py) is also
configured; see "Sovereign God mode" below.

| Path | Method | Purpose | Request | Response |
|---|---|---|---|---|
| `/` | GET | Serve the viewer shell | — | `index.html` |
| `/viewer.css` | GET | Serve the viewer stylesheet | — | `viewer.css` |
| `/viewer.js` | GET | Serve the viewer client script | — | `viewer.js` |
| `/sprites.js` | GET | Serve the pure Canvas renderer | — | `sprites.js` |
| `/wildlife.png` | GET | Serve the wildlife spritesheet PNG (variable-size atlas from user PNGs; 404 falls back to canvas helpers / procedural grids in `sprites.js`) | — | `wildlife.png` |
| `/wildlife_refsheet.html` | GET | Dev/debug — labeled 4×4 grid calling live `drawWildlifeCreature`; not part of the sim viewer loop | — | `wildlife_refsheet.html` |
| `/roles.js` | GET | Serve role data as a JS global | — | `const ROLES = {...};` (`application/javascript`), sourced from the same `ROLES` dict server.py derives its maps from — `roles.json` stays the single edit point |
| `/log/event` | POST | Ingest a browser-origin activity/conversation event | `{type: "activity"\|"conversation", message/from/to, frame_tick, kind?, outcome?}` | `("", 204)` always |
| `/log/benchmark` | POST | Ingest a browser-origin benchmark metric | `{metric, value, frame_tick, detail?}` | `("", 204)` always |
| `/memory/store` | POST | Embed + persist one or more memories | `{entries: [...]}` or a single `{agent, text, salience?, kind?, frame_tick?, tier?}` | `{ok, stored, size}` |
| `/memory/query` | POST | Top-k cosine retrieval over the memory store | `{agent?, text, top_k?, tier?, kinds?}` | `{results: [{text, tier, kind, salience, frame_tick}, ...]}` |
| `/memory/summarize` | POST | Compress an agent's recent memories into one durable sentence | `{agent, frame_tick?}` | `{ok, summary, size}` or `{ok: false, reason}` |
| `/agent/module` | POST | Run one PIANO cognitive module (experimental, off by default) | `{module, agent, context, frame_tick?}` | `{text}` |
| `/meta/update` | POST | Build an autobiography + persona directive (experimental, off by default) | `{agent, report, frame_tick?}` | `{ok, autobiography, persona}` |
| `/memory/clean` | POST | Dedupe/trim the memory store | `{frame_tick?}` | `{ok, removed, size}` |
| `/agent/think` | POST | **Legacy** — calls `run_agent_decision()` directly | full think-payload dict (see specs/03) | validated decision dict |
| `/council-llm-log` | GET | Slim decision records (`llm.jsonl`) for a council frame window (blueprint pitches/verdicts only). Scans **every retained session directory** under `logs/` (not just the live one) — `frame_tick` is monotonic across restarts, so a past council meeting's window can fall entirely inside an older session's `llm.jsonl`; matches from all directories are merged and re-sorted by `frame_tick` | query params `start_frame`, `end_frame`, `agents` (comma-separated names) | `{entries: [{agent_name, frame_tick, ts, latency_ms, invention_only, decision, error}, ...]}` |
| `/state` | GET | Full world snapshot for the thin viewer | — | `engine.snapshot()` — see key inventory below |
| `/districts.js` | GET | Live districts/roads (despite the `.js` name, plain JSON — fetch()-polled, not `<script>`-injected) | — | `{districts: [...], roadNodes: {...}, roadEdges: [...]}` |
| `/control/pause` | POST | Pause the tick loop | — | `{ok: true, paused: true}` |
| `/control/resume` | POST | Resume the tick loop | — | `{ok: true, paused: false}` |
| `/control/reset` | POST | Reset the world, optionally with a new roster size (requires password) | `{password: string, agents?: int}` — `password` must match `SIM_RESET_PASSWORD` (server.py, read once at import; default `"reset"` when unset/blank); `agents` optional (omitted or invalid → keep current `roster_size`) | `{ok: true, agents: <new roster_size>}` on success; `{ok: false, error: "unauthorized"}` with HTTP 401 on wrong/missing password (no reset) |
| `/control/god/capabilities` | GET | Enabled command/effect names, bounds, duration caps, token status (requires God auth when `GOD_AUTH_REQUIRED`) | — | `{ok, godModeEnabled, tokenConfigured, kinds: {...}, previewTtlSeconds, activeEventsCap, compiler: {enabled, minIntervalSec, sessionCap, promptMaxChars}}` |
| `/control/god/sight` | GET | Authenticated private inspection, bounded and filterable (requires God auth when `GOD_AUTH_REQUIRED`) | — | `engine.god_sight()` |
| `/control/god/preview` | POST | Validate and normalize a god command without mutation (requires God auth when `GOD_AUTH_REQUIRED`) | `{kind, payload, expectedFrame?}` | `engine.god_preview(envelope)` |
| `/control/god/apply` | POST | Apply an exact previewed command (requires God auth when `GOD_AUTH_REQUIRED`) | `{previewId, requestId}` | `engine.god_apply(previewId, requestId)` |
| `/control/god/cancel` | POST | Cancel an active omen/providence/timed event (requires God auth when `GOD_AUTH_REQUIRED`) | `{targetId}` | `engine.god_cancel(targetId)` |
| `/control/god/compile` | POST | Optional Phase 8: compile free operator prose into a DRAFT `story_event` preview (requires God auth when `GOD_AUTH_REQUIRED`; also requires `GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED`, otherwise a clean rejection) | `{prose}` (string, up to `GOD_COMPILER_PROSE_MAX_CHARS = 800` chars) | `engine.god_compile_prose(prose)` — `{compileOk, previewId, commandDigest, previewOutcome, normalizedCommand, reversibilityClass, expiresAt}` or `{compileOk: false, reason}` |

`/agent/think` is legacy: the server-authoritative engine never calls it over
HTTP. Instead, `_ENGINE_DEPS["llm_decide"]` (server.py:3233-3254) is wired
directly to a thin in-process wrapper `_llm_decide()` (server.py:3228-3230)
that calls `run_agent_decision()` directly — the engine's think worker pool
invokes this Python function in-process, never round-tripping through Flask.
The route is kept only for external/manual testing.

There is **no `districts.js` file on disk** — the name matches the original
plan's route-naming convention, but the handler reads `engine.civilization`
live under the engine lock (same pattern as `/state`) and returns JSON; the
viewer's periodic `fetch()` re-parses it rather than re-injecting a `<script>`
tag (which would throw on re-declaring `const` globals every poll).

## `/state` payload — top-level keys

From `SimEngine.snapshot()` (sim_engine.py:9907-10049), returned under the
engine lock for a consistent read:

| Key | Contents (detail owned elsewhere) |
|---|---|
| `frameTick` | current tick counter — specs/02-engine-core.md |
| `paused` | bool |
| `uptimeSeconds` | process wall-clock uptime |
| `calendar` | day/season/year — specs/02-engine-core.md |
| `lmStatus` | last-known Ollama (LLM runtime) reachability |
| `agents` | per-agent view (position, resources, health, beliefs, skills, lifecycle fields, etc.) — specs/06-agents.md |
| `civilization` | structures, projects, resource/project registries, pending blueprints/recipes/rules, stockpile, and flag-gated sections (chronicle/library when `CULTURE_ENABLED`, era/tech-tier/council when `TECH_TREE_ENABLED`, market/prices when `ECONOMY_ENABLED`, settlements/**settlementStores**/treaties/`caravanLog`/`isNight` when Path 1 diplomacy is on, `litDistricts` + per-structure `light` flag when `ENV_EFFECTS_ENABLED` — specs/08) — specs/05-world.md, specs/08-09-10 |
| `benchmarks` | latest benchmark metrics — specs/12-ops.md |
| `activity` | recent activity log entries |
| `conversation` | last 30 conversation log entries |
| `config` | `{WORLD_W, WORLD_H, flags: {...}}` — the full flag-value snapshot echoed to the viewer, see specs/01-architecture.md's flag index |
| `god` | **Present only when `GOD_MODE_ENABLED`.** `{intervened, providence, activePublicEvents, recentPublicInterventions}` (Phase 3 adds `providence`, public by design — [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-and-providence)) — `snapshot()` builds `civ` as an explicit allowlist dict, so this key is opt-in by construction; `privateOmens`, the in-memory idempotency store, and the token can never leak by omission, and `recentPublicInterventions` is additionally filtered to `"public": True` records so a private omen's outcome can never appear here either. See "Sovereign God mode" below. |

## Server startup/shutdown

Host/port: `SIM_HOST` env var (default `0.0.0.0`, binds all LAN interfaces —
intended for a trusted home LAN only) and `SIM_PORT` env var (default `5001`;
never use 5000 — macOS AirPlay claims it and returns 403). `app.run(...,
threaded=True)` so request handlers run concurrently alongside the engine's own
tick thread (server.py:3403-3443).

Startup order: `engine.start()` (spins up the 30/s tick daemon thread) runs
*before* `app.run()`, so the world ticks headless even before the HTTP server
accepts connections. Roster size at cold start comes from the `SIM_AGENTS`
env var (default 8, server.py:3256-3262) — distinct from the `/control/reset`
body field, which only takes effect on an explicit reset.

**Reset password:** `POST /control/reset` requires a JSON `password` field
compared with `hmac.compare_digest` against `RESET_PASSWORD` (server.py, read
once at import from `SIM_RESET_PASSWORD`, defaulting to `"reset"` when unset
or blank). Wrong or missing password returns `401 {"ok": false, "error":
"unauthorized"}` and does **not** call `engine.reset()`. This gate is separate
from Sovereign God mode token auth (`SIM_GOD_TOKEN` / `X-God-Token`).

Graceful shutdown: `atexit.register(_flush_on_exit)` plus `SIGINT`/`SIGTERM`
handlers (server.py:3421-3441) both call a `threading.Event`-guarded
`_flush_on_exit()` exactly once, which stops the engine and calls
`engine.save_state()` to flush the full world to `simulation/state.db`
before the process exits — covers both normal exit (atexit doesn't fire on a
signal-killed process) and Ctrl-C/`kill`.

## Civ-1 state additions

When transit is enabled, `/state` includes `civilization.physicalProps`, a
read-only list of `{resource, count}` hints for the thin viewer. It derives up
to three boats from village stockpile quantity; the viewer places them at fixed
moorings in the starter ocean, rather than beside ordinary structures.

When `PATH1_DIPLOMACY_ENABLED` is on, `/state` also includes
`civilization.settlementStores` — a map `{settlement_id: {resource_id: qty}}`
mirroring the think-payload summary agents see when planning caravans and local
spending ([08-systems-economy.md](08-systems-economy.md#settlement-stores-and-inter-settlement-trade-path1_diplomacy_enabled)).
Each settlement id matches `civilization.settlements[*].id`; missing keys
migrate to `{}` on restore.

## Sovereign God mode

The five `/control/god/*` routes (docs/plan-sovereign-god-mode-v2.md) form a
deliberately separate, optional control plane. All five share one gate and
one uniform failure shape:

- **Gate:** `GOD_MODE_ENABLED` (sim_engine.py, env-backed `SIM_GOD_MODE`,
  read once at import — see [01-architecture.md](01-architecture.md)) must
  be configured. When `GOD_AUTH_REQUIRED` is also True (env-backed
  `SIM_GOD_AUTH`, default **False** — see [12-ops.md](12-ops.md)), a
  non-empty `SIM_GOD_TOKEN` (server.py, also read once at import) must
  additionally be configured or routes stay disabled. If the flag is on,
  auth is required, and the token is missing, server.py prints one startup
  warning that contains no secret (there is none to reveal) and every route
  below stays disabled until a restart supplies a token. When auth is off
  (the default), routes go live as soon as the flag is on — no token needed.
- **Auth (when `GOD_AUTH_REQUIRED`):** clients send `X-God-Token`; compared
  against `SIM_GOD_TOKEN` with `hmac.compare_digest`. When auth is off,
  the header is ignored and tokenless requests succeed. Neither header
  contents nor the token are ever logged, persisted, or echoed back.
- **Uniform failure:** a disabled flag, inactive routes (auth required but
  token unset at startup), or — when auth is required — a missing header or
  wrong token are all indistinguishable from the outside — every case returns
  the identical `401 {"error": "unauthorized"}`. No God response ever reveals
  whether a target or event exists to an unauthorized caller.
- **Body size limit:** POST bodies over `GOD_MAX_BODY_BYTES = 8192` bytes are
  rejected with `413 {"error": "payload_too_large"}` before JSON parsing.
- **Never client-authoritative:** `/control/god/apply` accepts only
  `{previewId, requestId}` — the normalized command itself is never accepted
  from the client at apply time; the engine resolves its own server-held
  preview. See [02-engine-core.md](02-engine-core.md) for the full
  preview/idempotency/expiry contract these routes front.

Phase 2 shipped exactly one applyable command kind, `proclamation`; Phase 3
added `providence`/`private_omen`/`revoke_guidance`; Divine Matrix Phase 1 adds
`whisper_campaign` (batch private omens); Divine Matrix Phase 2 adds
`agent_sampling` / `revoke_agent_sampling` (per-agent LLM sampling overlay,
private); Divine Matrix Phase 3 adds `memory_insert` / `memory_delete` /
`belief_plant` (engine-mediated memory surgery — private, irreversible,
outcomes are counts/metadata only in Sight); Divine Matrix Phase 4 adds
`context_mask` (reality-distortion think-payload layer — private, cancellable,
Sight shows mode/expiry only); Divine Matrix Phase 5 adds
`decision_compulsion`, `decision_veto_arm`, `decision_veto_resolve`,
`agent_possession`, and `revoke_decision_gate` (decision gate / possession —
private, cancellable except `decision_veto_resolve`; Sight shows gate status
summaries and `pinnedAction` for operator UX); Divine Matrix Phase 6 adds
`burning_bush_message`, `burning_bush_close`, `merovingian_bargain`, and
`bargain_settle` (private bush thread + timed bargain with allowlisted
predicates; Sight shows `messageCount`/`bargainActive` only); Divine Matrix
Phase 7 adds `anoint` and `revoke_anoint` (private destiny/oracle; stigmata in
neighbor think prompts only; Sight shows `tagCount`/`nextOracleFrame`/`expiresFrame`
without secret text); Divine Matrix Phase 8 adds `identity_edit`,
`identity_copy_overwrite`, and `identity_forge_cancel` (mutate/blend
persona/personality/role; private map; Sight shows `progress`/`rate`/
`copyFromId`/`expiresFrame` only; elder role swap warns in preview); Divine
Matrix Phase 9 adds `architect_zone`, `architect_zone_cancel`, and
`architect_release_hold` (Path1 terrain paint, keyed door movement gate, limbo
hold at `GOD_LIMBO_STATION`; `architectZones` omitted from `/state`; Sight
summaries only; paint audit `public: true`, door/limbo `public: false`); Divine
Matrix Phase 10 adds `checkpoint_create`, `checkpoint_restore`, and
`deja_vu_replay` (stub; `GOD_DEJA_VU_REPLAY` gate); checkpoint metadata in
Sight only, not `/state`; restore is irreversible world replace; Phase 4 added
`agent_vitals`/`grant_resource`/`structure_condition`; town-integrity adds
`repair_structures`/`clear_ruins`; Phase 5 adds
`story_event` (timed modifiers + zero or more Phase 4 primitives + optional
providence, composed atomically). `/control/god/capabilities` echoes the
full current catalog — payload shape, bounds, and `reversibilityClass` per
kind (`story_event`'s is `"cancellable"` with no primitives, `"consequential"`
with any) — plus `modifierRanges` for the seven timed-lawgiver keys. Mass
structure commands (`repair_structures`, `clear_ruins`) are documented in
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-town-integrity--mass-structure-repair-and-ruin-clearance).
See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel)
for the command catalog and stored-text contract, and
[12-ops.md](12-ops.md) for `divine.jsonl`.

**`/control/god/cancel`** is a direct, lock-held mutation with no
preview/apply step (unlike every other mutating God route). It searches, in
order, the active `providence` slot, every `privateOmens` record, every
`whisperCampaigns` entry (by campaign id — revokes all linked omens), every
`agentSampling` override (by intervention `id` or via `revoke_agent_sampling`),
every `contextMasks` entry (by mask intervention `id`), every
`decisionGates` entry (by gate intervention `id`), every
`burningBush` session or open `merovingian_bargain` (by bush/bargain `id`), every
`anointments` entry (by intervention `id`), every `identityForges` entry (by
intervention `id`), every active `architectZones` entry (by zone `id`), then every
`"active"` `activeEvents` entry for a matching `id`, closing whichever it
finds through that record's normal closure path (also closing a
`story_event`'s linked providence, if any, in the same step) and returning
`{"ok": true, "cancelled": true, "targetId", "targetKind"}`. No match —
including an id minted by an irreversible Phase 4 miracle or a one-shot
`proclamation`, neither of which is ever stored in any of the three searched
stores — returns `{"ok": true, "cancelled": false, "reason": "nothing to
cancel", "targetId"}`, so miracle ids are refused by construction rather than
through a special-cased error. See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-5--storyteller-events-and-timed-lawgiver-modifiers)
for the full Phase 5 contract.

**Viewer consumer (Phase 7).** The Divine Console (`simulation/index.html`,
see [11-viewer.md](11-viewer.md#divine-console-sovereign-god-mode-phase-7))
is the first and only client of all five routes. It reads
`previewOutcome`/`fingerprint.outgoingId`/`reversibilityClass` straight off
`god_preview()`'s response (documented in
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel))
to drive its preview→apply flow, and reads `god_sight()`'s `agents`/
`activeEvents`/`recentInterventions` for its Sight tab. No new response shape
was needed for the viewer — every field it reads was already documented by
Phases 2–6.

### Optional Phase 8: `/control/god/compile`

Auth-gated exactly like the other five routes above (when
`GOD_AUTH_REQUIRED`), with one additional gate: it also requires the SECOND,
independent `GOD_COMPILER_ENABLED` dark flag (env `SIM_GOD_COMPILER`, read
once at import — see [01-architecture.md](01-architecture.md) and
[12-ops.md](12-ops.md#optional-phase-8-free-prose-story-compiler)). The token
itself is **never forwarded** to `engine.god_compile_prose` — the route
handler checks authorization before calling the engine method, and that method
has no parameter to receive it.

This route **never mutates and never applies**. A successful compile
produces a normal preview record in the SAME `_god_preview_cache` slot
`/control/god/preview` uses — `previewId` is applyable through the ordinary
`/control/god/apply` route exactly like a hand-authored `story_event`, with
the same revalidation. There is no `/control/god/compile-and-apply`
shortcut.

Body: `{"prose": "<string, up to GOD_COMPILER_PROSE_MAX_CHARS=800 chars>"}`.
A missing/non-string/oversized `prose` field is rejected by the route itself
before reaching the engine. Rejection reasons (rate limit, session cap,
schema mismatch, unknown modifier key, non-JSON model output, timeout) are
short and secret-free — the same "clear error naming the offending field"
contract every other God validator follows. See
[02-engine-core.md](02-engine-core.md) for `_validate_god_story_event`,
which every compiled draft is revalidated against before it reaches the
preview cache, and [03-cognition.md](03-cognition.md#sovereign-god-mode-optional-phase-8-free-prose-story-compiler)
for the model-routing and concurrency-pool contract.

`/control/god/capabilities`'s response additionally carries a `compiler` key
—`{enabled, minIntervalSec, sessionCap, promptMaxChars}` — so the viewer can
render or hide its Compile tab without probing `/control/god/compile`
directly; `enabled` already folds both `GOD_MODE_ENABLED` and
`GOD_COMPILER_ENABLED` together.

## Logging endpoints: fire-and-forget contract

`/log/event` and `/log/benchmark` both wrap their entire body in a bare
`try/except Exception: pass` and always return `("", 204)` — logging must
never break the simulation or the browser's fetch. `/memory/*` and
`/agent/module`/`/meta/update` follow the same pattern but return `{ok:
false}` (HTTP 200) instead of a bare 204 on failure, so callers can branch on
`ok` without a thrown exception ever reaching them. See
[specs/12-ops.md](12-ops.md) for the JSONL file formats these write to
(`activity.jsonl`, `conversation.jsonl`, `llm.jsonl`, `benchmarks.jsonl`).
