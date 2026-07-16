# SPEC 04 — HTTP API

The Flask route surface: every endpoint the browser or external tools call,
what it does, and its request/response shape.

**Canonical for:** the full route table (18 routes), `/state` top-level
payload key inventory, server startup/shutdown behavior. **See also:**
[specs/01-architecture.md](01-architecture.md) (data flow, thin-viewer
contract), [specs/03-cognition.md](03-cognition.md) (what `run_agent_decision`
does — not repeated here), [specs/11-viewer.md](11-viewer.md) (polling
cadence and rendering), [specs/12-ops.md](12-ops.md) (log file formats/
retention for the `/log/*` and `/council-llm-log` endpoints).

## Route table

18 routes total (`@app.route` count in `simulation/server.py`; no other
route-registration mechanism is used).

| Path | Method | Purpose | Request | Response |
|---|---|---|---|---|
| `/` | GET | Serve the viewer shell | — | `index.html` |
| `/sprites.js` | GET | Serve the pure Canvas renderer | — | `sprites.js` |
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
| `/council-llm-log` | GET | Slim LM Studio decision records for a council frame window (blueprint pitches/verdicts only) | query params `start_frame`, `end_frame`, `agents` (comma-separated names) | `{entries: [{agent_name, frame_tick, ts, latency_ms, invention_only, decision, error}, ...]}` |
| `/state` | GET | Full world snapshot for the thin viewer | — | `engine.snapshot()` — see key inventory below |
| `/districts.js` | GET | Live districts/roads (despite the `.js` name, plain JSON — fetch()-polled, not `<script>`-injected) | — | `{districts: [...], roadNodes: {...}, roadEdges: [...]}` |
| `/control/pause` | POST | Pause the tick loop | — | `{ok: true, paused: true}` |
| `/control/resume` | POST | Resume the tick loop | — | `{ok: true, paused: false}` |
| `/control/reset` | POST | Reset the world, optionally with a new roster size | `{agents?: int}` (optional; omitted or invalid → keep current `roster_size`) | `{ok: true, agents: <new roster_size>}` |

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
| `lmStatus` | last-known LM Studio reachability |
| `agents` | per-agent view (position, resources, health, beliefs, skills, lifecycle fields, etc.) — specs/06-agents.md |
| `civilization` | structures, projects, resource/project registries, pending blueprints/recipes/rules, stockpile, and flag-gated sections (chronicle/library when `CULTURE_ENABLED`, era/tech-tier/council when `TECH_TREE_ENABLED`, market/prices when `ECONOMY_ENABLED`, settlements/treaties/`isNight` when Path 1 is on, `litDistricts` + per-structure `light` flag when `ENV_EFFECTS_ENABLED` — specs/08) — specs/05-world.md, specs/08-09-10 |
| `benchmarks` | latest benchmark metrics — specs/12-ops.md |
| `activity` | recent activity log entries |
| `conversation` | last 30 conversation log entries |
| `config` | `{WORLD_W, WORLD_H, flags: {...}}` — the full flag-value snapshot echoed to the viewer, see specs/01-architecture.md's flag index |

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

Graceful shutdown: `atexit.register(_flush_on_exit)` plus `SIGINT`/`SIGTERM`
handlers (server.py:3421-3441) both call a `threading.Event`-guarded
`_flush_on_exit()` exactly once, which stops the engine and calls
`engine.save_state()` to flush the full world to `simulation/state.json`
before the process exits — covers both normal exit (atexit doesn't fire on a
signal-killed process) and Ctrl-C/`kill`.

## Civ-1 state additions

When transit is enabled, `/state` includes `civilization.physicalProps`, a
read-only list of `{resource, count}` hints for the thin viewer. It derives up
to three boats from village stockpile quantity; the viewer places them at fixed
moorings in the starter ocean, rather than beside ordinary structures.

## Logging endpoints: fire-and-forget contract

`/log/event` and `/log/benchmark` both wrap their entire body in a bare
`try/except Exception: pass` and always return `("", 204)` — logging must
never break the simulation or the browser's fetch. `/memory/*` and
`/agent/module`/`/meta/update` follow the same pattern but return `{ok:
false}` (HTTP 200) instead of a bare 204 on failure, so callers can branch on
`ok` without a thrown exception ever reaching them. See
[specs/12-ops.md](12-ops.md) for the JSONL file formats these write to
(`activity.jsonl`, `conversation.jsonl`, `lm_studio.jsonl`, `benchmarks.jsonl`).
