# SPEC 04 — HTTP API

The Flask route surface: every endpoint the browser or external tools call,
what it does, and its request/response shape.

**Canonical for:** the full route table (67 routes), `/state` top-level
payload key inventory, server startup/shutdown behavior. **See also:**
[specs/01-architecture.md](01-architecture.md) (data flow, thin-viewer
contract), [specs/03-cognition.md](03-cognition.md) (what `run_agent_decision`
does — not repeated here), [specs/11-viewer.md](11-viewer.md) (polling
cadence and rendering), [specs/12-ops.md](12-ops.md) (log file formats/
retention for the `/log/*` and `/council-llm-log` endpoints).

## Route table

67 routes total in `simulation/server.py`: 32 from their own `@app.route`
decorator, plus 35 more registered programmatically by three small
`add_url_rule` loops — `_register_sprite_route()` (called once per file in
`_SPRITE_FILES`, 8 iterations, serving `/sprites/<name>.js`),
`_register_css_route()` (called once per file in `_CSS_FILES`, 6 iterations,
serving `/css/<name>.css`), and `_register_viewer_route()` (called once per
file in `_VIEWER_FILES`, 21 iterations, serving `/viewer/<name>.js`) — added
by the Phase 2 (sprites), Phase 3 (CSS), and Phase 4 (viewer.js)
file-modularization splits. Of the 67, 6 are the `/control/god/*` routes
added in Phase 2 of Sovereign God mode (all `@app.route`-decorated); the
other 61 are always-registered non-god routes (32 decorated minus the 6 god
ones = 26, plus the 35 `add_url_rule` routes = 61). The god routes are registered
unconditionally but only ever *answer* requests when `GOD_MODE_ENABLED`
(`constants.py:644`) is configured at startup and, when `GOD_AUTH_REQUIRED` is
True (default False), a non-empty `SIM_GOD_TOKEN` (server.py) is also
configured; see "Sovereign God mode" below.

Legacy single-file `/viewer.css`, `/sprites.js`, and `/viewer.js` routes return
404; see the route table below and
[12-ops.md](12-ops.md#viewer-static-assets) / [11-viewer.md](11-viewer.md) for
per-file detail.

| Path | Method | Purpose | Request | Response |
|---|---|---|---|---|
| `/` | GET | Serve the viewer shell | — | `index.html` |
| `/css/<name>.css` | GET | Serve one of the 6 split viewer stylesheets (`base.css`, `panels.css`, `agents.css`, `council.css`, `divine.css`, `responsive.css`) — see [12-ops.md](12-ops.md#viewer-static-assets) | — | the named `.css` file |
| `/viewer/<name>.js` | GET | Serve one of the 21 split viewer client script files (`setup.js`, `state.js`, `render.js`, `panels.js`, `sidebar.js`, `decision-audit.js`, `council.js`, `predictions.js`, `minimap.js`, `polling.js`, `anomaly.js`, `controls.js`, `renderloop.js`, `divine-bootstrap.js`, `divine-auth-sight.js`, `divine-modal.js`, `divine-sight-voice.js`, `divine-voice.js`, `divine-miracles-story.js`, `divine-history.js`, `world-wiki.js`) — see [12-ops.md](12-ops.md#viewer-static-assets) | — | the named `.js` file |
| `/sprites/<name>.js` | GET | Serve one of the 8 split pure Canvas renderer files (`core.js`, `tiles.js`, `props.js`, `structures.js`, `agents.js`, `world.js`, `wildlife.js`, `shipments.js`) — see [12-ops.md](12-ops.md#viewer-static-assets) | — | the named `.js` file |
| `/wildlife.png` | GET | Serve the wildlife spritesheet PNG (variable-size atlas from user PNGs; 404 falls back to canvas helpers / procedural grids in `sprites/wildlife.js`) | — | `wildlife.png` |
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
| `/agent/interview` | POST | Read-only, out-of-world Q&A over one agent's own memory/relationships/beliefs (no auth, not a God-mode intervention) — see **Agent interview route** below | `{agentId, question}` (`question` capped at `INTERVIEW_QUESTION_MAX_CHARS = 500` chars) | `{ok: true, agentId, agentName, answer}` or `{ok: false, reason}` — see below |
| `/council-llm-log` | GET | Slim decision records (`llm.jsonl`) for a council frame window (blueprint pitches/verdicts only). **Scans the live session's `llm.jsonl` first**; only reads older retained session directories when the requested `[start_frame, end_frame]` is not fully covered by the live file's frame range (`frame_tick` is monotonic across restarts, but each session only spans frames recorded while that server run was alive — a past council window may fall entirely in an older session). Out-of-range files are skipped using cached per-file `(min_frame, max_frame)` when possible. Matches from all scanned directories are merged and re-sorted by `frame_tick` | query params `start_frame`, `end_frame`, `agents` (comma-separated names) | `{entries: [{agent_name, frame_tick, ts, latency_ms, invention_only, decision, error}, ...]}` |
| `/state` | GET | World snapshot for the thin viewer (full or delta via `?since=`) | query param `since` (int, optional) — client's last applied `frameTick`; omit or `0` for full | See **/state delta protocol** below and key inventory |
| `/districts.js` | GET | Live districts/roads (despite the `.js` name, plain JSON — fetch()-polled, not `<script>`-injected). Supports conditional polls via `districtsEpoch` | query param `since` (int, optional) — last seen `epoch` from a prior response | **First / gap:** `{districts: [...], roadNodes: {...}, roadEdges: [...], epoch: int}`. **Unchanged:** when `since == engine.districtsEpoch`, HTTP 200 with tiny body `{unchanged: true, epoch: int}` (no district/road payload). `districtsEpoch` bumps on district founding, tile place/remove, terrain dig/plant, road-graph change, architect paint/revert, restore, and reset |
| `/wiki` | GET | World wiki - cross-linked page model for all twelve entity kinds. Gated by `WORLD_WIKI_ENABLED`; returns `{ok: false, reason: "disabled"}` when flag is off. No LLM calls (skeleton-only). Merges `/state`-side entities with district/road data from the internal `_districts_snapshot_payload(engine)` helper in-process - no HTTP round-trip to `/districts.js` | - | See *World wiki route* below |
| `/control/pause` | POST | Pause the tick loop | — | `{ok: true, paused: true}` |
| `/control/resume` | POST | Resume the tick loop | — | `{ok: true, paused: false}` |
| `/control/reset` | POST | Reset the world, optionally with a new roster size (requires password) | `{password: string, agents?: int}` — `password` must match `SIM_RESET_PASSWORD` (server.py, read once at import; default `"reset"` when unset/blank); `agents` optional (omitted or invalid → keep current `roster_size`) | `{ok: true, agents: <new roster_size>}` on success; `{ok: false, error: "unauthorized"}` with HTTP 401 on wrong/missing password (no reset) |
| `/control/god/capabilities` | GET | Enabled command/effect names, bounds, duration caps, token status (requires God auth when `GOD_AUTH_REQUIRED`) | — | `{ok, godModeEnabled, tokenConfigured, kinds: {...}, previewTtlSeconds, activeEventsCap, compiler: {enabled, minIntervalSec, sessionCap, promptMaxChars}}` |
| `/control/god/sight` | GET | Authenticated private inspection, bounded and filterable (requires God auth when `GOD_AUTH_REQUIRED`) | — | `engine.god_sight()` |
| `/control/god/preview` | POST | Validate and normalize a god command without mutation (requires God auth when `GOD_AUTH_REQUIRED`) | `{kind, payload, expectedFrame?}` | `engine.god_preview(envelope)` |
| `/control/god/apply` | POST | Apply an exact previewed command (requires God auth when `GOD_AUTH_REQUIRED`) | `{previewId, requestId}` | `engine.god_apply(previewId, requestId)` |
| `/control/god/cancel` | POST | Cancel an active omen/providence/timed event (requires God auth when `GOD_AUTH_REQUIRED`) | `{targetId}` | `engine.god_cancel(targetId)` |
| `/control/god/compile` | POST | Optional Phase 8: compile free operator prose into a DRAFT `story_event` preview (requires God auth when `GOD_AUTH_REQUIRED`; also requires `GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED`, otherwise a clean rejection) | `{prose}` (string, up to `GOD_COMPILER_PROSE_MAX_CHARS = 800` chars) | `engine.god_compile_prose(prose)` — `{compileOk, previewId, commandDigest, previewOutcome, normalizedCommand, reversibilityClass, expiresAt}` or `{compileOk: false, reason}` |
| `/anomalies` | GET | Anomaly radar (idea-07, expanded idea-07b): read-side, server-side reader over the current run's `benchmarks.jsonl`, gated by `ANOMALY_RADAR_ENABLED` (see [01-architecture.md](01-architecture.md)) | — | see "Anomaly radar" below |
| `/decision-audit` | GET | Read-only decision-intent audit over the current session's `llm.jsonl` and `activity.jsonl`, gated by `DECISION_AUDIT_ENABLED` | — | see "Decision audit route" below |

`/agent/think` is legacy: the server-authoritative engine never calls it over
HTTP. Instead, `_ENGINE_DEPS["llm_decide"]` (server.py:2604-2633) is wired
directly to a thin in-process wrapper `_llm_decide()` (server.py:2599-2601)
that calls `run_agent_decision()` directly — the engine's think worker pool
invokes this Python function in-process, never round-tripping through Flask.
The route is kept only for external/manual testing.

There is **no `districts.js` file on disk** — the name matches the original
plan's route-naming convention, but the handler reads `engine.civilization`
live under the engine lock (same pattern as `/state`) and returns JSON; the
viewer's periodic `fetch()` re-parses it rather than re-injecting a `<script>`
tag (which would throw on re-declaring `const` globals every poll).

**`/districts.js` conditional poll.** The viewer tracks `districtsEpoch`
(engine attribute, not in `/state`). Each poll may send `GET
/districts.js?since=<epoch>`. When `since` equals the current epoch, the
handler returns only `{unchanged: true, epoch}` under the lock without
copying district tiles/terrain; JSON is assembled after the lock is released.
When the epoch differs (or `since` is omitted), the handler shallow-copies
district/road data into plain dicts/lists under the lock, then `jsonify`s
outside the lock. The viewer keeps its last full payload on `unchanged`
responses.

## Prediction-market routes

| `/predictions/submit` | POST | Store a pending spectator prediction against an open Daily Council ballot. Gated by `PREDICTION_MARKET_ENABLED` | `{kind, question, pick, ballot_frame_tick}` | `{ok, id}` |
| `/predictions/resolve` | POST | Mark a pending prediction correct/incorrect after the ballot verdict is known. Gated by `PREDICTION_MARKET_ENABLED` | `{id, correct, verdict, resolved_frame_tick?}` | `{ok}` |
| `/predictions/history` | GET | Shared calibration history and hit-rate for the prediction panel. Gated by `PREDICTION_MARKET_ENABLED` | â€” | `{enabled, predictions, hitRate}` |

## `/state` delta protocol

`SimEngine.snapshot_delta(since)` (server route: `GET /state?since=<N>`).

| Request | Response |
|---------|----------|
| `GET /state` or `?since=0` / missing `since` | Full snapshot (`full: true`; same top-level keys as before) |
| `GET /state?since=<N>` and `N == frameTick` and no state changed with `lastMod > N` | `{frameTick, stateGeneration, unchanged: true}` |
| `GET /state?since=<N>` contiguous (`since < frameTick`, gap ≤ `STATE_DELTA_MAX_GAP` ≈ 90 frames) | `{frameTick, baseFrame: N, stateGeneration, calendar, uptimeSeconds, paused? (if changed), ...partial}` — omitted key = unchanged on the client; each included field was last modified at a frame `> N` within the gap window |
| Gap > 90 frames / reset / `since > frameTick` / `since < last_reset_frame` | Full snapshot + `full: true`; `stateGeneration` bumps on reset/restore |

Partial rules: dirty agents only in `agents[]`; dirty civ subkeys only (structure upserts may omit `sprite` unless create/upgrade/sprite-submit — full snapshots and the first poll always include `sprite` when present; the viewer keeps prior sprites when a delta upsert omits the field — `structuresRemoved` lists deletions); `config` only on full or when flags change. The engine tracks per-key `lastMod` frame stamps (not cleared per poll) and emits entries with `lastMod > since`, pruning entries older than `frameTick - STATE_DELTA_MAX_GAP` so multiple clients with different `since` values each receive one-time updates within the gap window. Lock discipline: copy/dirty under lock; JSON assembly after release where practical.

A civ subkey only ever appears in a delta when some write site explicitly calls `self._mark_civ_dirty(key, ...)` — an in-place mutation of a dict/list already referenced by `self.civilization` produces no automatic diff. `civilization.dailyCouncil`, `councilLog`, `councilDigests`, and `councilActive` (Daily Council Assembly and the legacy invention council, `mixin_council_growth.py`) participate in this protocol: convene, per-tick phase advance, and adjourn each call `_mark_civ_dirty` on their affected keys. In particular, the live `dailyCouncil` dict is re-marked dirty on every tick a council session is in session (not just on phase transitions), because `apply_decision`'s `council_speak`/`council_propose`/`council_vote` handlers and the tick-driven roster refresh all mutate that same dict object in place — the per-tick mark is the delta-protocol safety net for all of them. This means `dailyCouncil` rides every delta poll for the full duration of a session (bounded by `DAILY_COUNCIL_SESSION_TTL_FRAMES`), a deliberate cost accepted so the live Assembly modal and sidebar panel update without a page refresh.

## `/state` payload — top-level keys

From `SimEngine.snapshot()` (`mixin_snapshot.py:379-388`) / `SimEngine.snapshot_delta()`
(`mixin_snapshot.py:389+`), returned under the engine lock for a consistent read. Full responses include
every key below; delta responses omit unchanged keys (client merges — see
[specs/11-viewer.md](11-viewer.md)). Both modes always include `frameTick`
and `stateGeneration`; full snapshots also set `full: true`.

| Key | Contents (detail owned elsewhere) |
|---|---|
| `frameTick` | current tick counter — specs/02-engine-core.md |
| `stateGeneration` | monotonic counter bumped on reset/restore; client forces a full resync when it changes |
| `full` | present and `true` only on full snapshots (omit on delta/unchanged) |
| `unchanged` | present and `true` only when `?since=frameTick` and nothing has `lastMod > since` |
| `baseFrame` | on deltas only — the client's `since` value this patch applies after |
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
| `god` | **Present only when `GOD_MODE_ENABLED`.** `{intervened, providence, activePublicEvents, recentPublicInterventions}` (Phase 3 adds `providence`, public by design — [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance)) — `snapshot()` builds `civ` as an explicit allowlist dict, so this key is opt-in by construction; `privateOmens`, `recentDivineResponses`, the in-memory idempotency store, and the token can never leak by omission, and `recentPublicInterventions` is additionally filtered to `"public": True` records so a private omen's outcome can never appear here either. See "Sovereign God mode" below. |

## Server startup/shutdown

Host/port: `SIM_HOST` env var (default `0.0.0.0`, binds all LAN interfaces —
intended for a trusted home LAN only) and `SIM_PORT` env var (default `5001`;
never use 5000 — macOS AirPlay claims it and returns 403). `app.run(...,
threaded=True)` so request handlers run concurrently alongside the engine's own
tick thread (server.py:3745-3788, the `if __name__ == "__main__":` block).

Startup order: `engine.start()` (spins up the 30/s tick daemon thread) runs
*before* `app.run()`, so the world ticks headless even before the HTTP server
accepts connections. Roster size at cold start comes from the `SIM_AGENTS`
env var (default 8, server.py:2635-2639) — distinct from the `/control/reset`
body field, which only takes effect on an explicit reset.

**Reset password:** `POST /control/reset` requires a JSON `password` field
compared with `hmac.compare_digest` against `RESET_PASSWORD` (server.py, read
once at import from `SIM_RESET_PASSWORD`, defaulting to `"reset"` when unset
or blank). Wrong or missing password returns `401 {"ok": false, "error":
"unauthorized"}` and does **not** call `engine.reset()`. This gate is separate
from Sovereign God mode token auth (`SIM_GOD_TOKEN` / `X-God-Token`).

Graceful shutdown: `atexit.register(_flush_on_exit)` plus `SIGINT`/`SIGTERM`
handlers (server.py:3766-3786) both call a `threading.Event`-guarded
`_flush_on_exit()` exactly once, which stops the engine and calls
`engine.save_state()` to flush the full world to `simulation/state.db`
before the process exits — covers both normal exit (atexit doesn't fire on a
signal-killed process) and Ctrl-C/`kill`.

## Civ-1 state additions

When transit is enabled, `/state` includes `civilization.physicalProps`, a
read-only list of `{resource, count}` hints for the thin viewer. It derives up
to three boats from village stockpile quantity; the viewer places them at fixed
moorings in the starter ocean, rather than beside ordinary structures.

When `PATH1_ENABLED` is on, `/state` also includes
`civilization.settlementStores` — a map `{settlement_id: {resource_id: qty}}`
mirroring the think-payload summary agents see when planning caravans and local
spending ([08-systems-economy.md](08-systems-economy.md#settlement-stores-and-inter-settlement-trade-path1_enabled)).
Each settlement id matches `civilization.settlements[*].id`; missing keys
migrate to `{}` on restore.

## World wiki route (`WORLD_WIKI_ENABLED`)

**Grounded in:** plan `docs/plans/idea-09-world-wiki/plan.md` §2 Answers 1–6.

`GET /wiki` returns a read-only cross-linked page model assembled entirely in-process
from two internal data sources under `engine.lock`: the engine's existing snapshot
machinery (same functions `/state` already calls) and the extracted
`_districts_snapshot_payload(engine)` district/road helper (see *Districts merge
mechanism* below). No HTTP round-trip to `/districts.js`. No LLM calls — skeleton-only
(Answer 4). Zero new world-state mutation.

**Reuse contract (idea-03-agent-interview).** This route and its entity projection are
explicitly designed as a reusable surface. `idea-03-agent-interview` (production order
item 7) is expected to build on this same projection rather than reconstruct it.
Implementers of idea-03 must not fork or duplicate the entity read logic; they extend
the wiki route or compose with its output.

### Gate

`WORLD_WIKI_ENABLED` (`sim_engine/constants.py`, default `True`, env-backed name TBD
by Phase 2a implementer if an env override is added). When `False`, `GET /wiki`
returns `{"ok": false, "reason": "disabled"}` (HTTP 200). The flag is echoed to the viewer
via `/state` `config.flags` (`_build_snapshot_config()`,
`sim_engine/mixin_snapshot.py`), so the viewer can show or hide wiki UI without probing
`/wiki` directly.

### Response shape

When enabled, `GET /wiki` returns:

```json
{
  "ok": true,
  "pages": {
    "agent":      [ {<agent page>} ],
    "structure":  [ {<structure page>} ],
    "belief":     [ {<belief page>} ],
    "rule":       [ {<rule page>} ],
    "chronicle":  [ {<chronicle page>} ],
    "district":   [ {<district page>} ],
    "settlement": [ {<settlement page>} ],
    "treaty":     [ {<treaty page>} ],
    "resource":   [ {<resourceRegistry page>} ],
    "project":    [ {<projectRegistry page>} ],
    "recipe":     [ {<recipe page>} ]
  }
}
```

`settlement` and `treaty` are omitted (empty or absent) when
`PATH1_ENABLED` is off.

**Phase 2a / 2b complete.** The route now returns all eleven page-kind arrays. Phase 2a
implemented `agent`, `structure`, `belief`, `rule`, and `chronicle`. Phase 2b added
`district`, `settlement`, `treaty`, `resource`, `project`, `recipe` and social-tie
cross-links on agent pages.

Social ties are **not** a standalone page kind — they appear as labeled `links` entries
on the two agent pages they connect (ally/rival). Each page object carries at minimum:
`{id, kind, fields: {...}, links: [{targetKind, targetId, relation}]}`. The exact
per-field inventory is documented in the owning spec section for each entity kind
(specs/05, 08, 09, 10).

**Live cadence (Answer 5).** The route is polled fresh on every request — no dirty-
tracking, no separate epoch. It piggybacks on the same underlying data that
`/state`/`/districts.js` already read; the viewer polls it on the same cadence it
already polls `/state` (~10 Hz or on demand when the wiki modal is open).

### Districts merge mechanism (Answer 3)

The district/road shallow-copy logic from `districts_js()` (`simulation/server.py`) has
been extracted into a small internal helper, `_districts_snapshot_payload(engine)`, in
`server.py` — a **mechanical move of existing lines, no new logic**.
Both `districts_js()` and the wiki route call this helper under `engine.lock`.
`/districts.js`'s own `districtsEpoch` conditional-poll protocol and its existing
viewer consumer are untouched; the wiki route is an independent read-side consumer of
the same underlying data.

### Cross-link table (Answer 2 — structured fields only)

Only real structured references get auto-linked. Free-text fields (chronicle `text`,
agent `lastReasoning`, rule description prose) are never scanned.

| Source field | Target kind | Linkable? | Reason |
|---|---|---|---|
| agent `relationships` (name-keyed) | agent | yes | resolves to exactly one agent by name |
| agent `homeDistrict`/`district` | district | yes | district id |
| structure `homeOf` | agent | yes | agent id |
| structure `districtId` | district | yes | district id |
| district `settlementId` | settlement | yes | settlement id |
| settlement `districts[]` | district | yes | district ids (reverse of above) |
| `socialTies` `from`/`to` | agent | yes | agent ids; rendered as one labeled link on each of the two agent pages, not a standalone page (server-canonicalized to one entry per pair with valence conflicts resolved to `"rival"` — `_social_ties_snapshot()`, `mixin_snapshot.py:79-103`) |
| projectRegistry `needs` (keys) | resource | yes | resource ids |
| recipe `output` | resource | yes | produced resource id |
| recipe `inputs` (keys) | resource | yes | resource ids |
| belief `affinity` | rule | **no** | names a rule *kind* (e.g. `resource_tax`), not a rule id; no single target instance |
| recipe `station` | structure | **no** | names a structure *type* (e.g. `"workshop"`), not a specific built structure's id; zero, one, or many instances may exist |
| resourceRegistry `gatherZone` | district | **no** | names a district *kind* (e.g. `"forest"`), not a specific district id; multiple instances may exist |
| treaty (any field) | settlement | **no** | verified treaty shape carries no settlement id field (`mixin_diplomacy.py:802-844`) |
| chronicle `text` | any | **no** | free prose; excluded per Answer 2 |

The exclusion pattern: any field naming a **kind/category/type string** rather than a
**specific instance id** does not get auto-linked — there is no guaranteed single target
page to resolve to.


## Sovereign God mode

The five `/control/god/*` routes (docs/archive/plan-sovereign-god-mode-v2.md) form a
deliberately separate, optional control plane. All five share one gate and
one uniform failure shape:

- **Gate:** `GOD_MODE_ENABLED` (`constants.py:644`, env-backed `SIM_GOD_MODE`,
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

Phase 2 shipped exactly one applyable command kind, `proclamation` (which
auto-applies as timed providence — see
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-3--voice-binding-guidance));
Phase 3 added `providence`/`private_omen`/`revoke_guidance`; Divine Matrix Phase 1 adds
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
Matrix Phase 10 adds `checkpoint_create` and `checkpoint_restore`; checkpoint
metadata in Sight only, not `/state`; restore is irreversible world replace.
Divine Console Phase 8 adds applyable `deja_vu_replay` when
`GOD_DEJA_VU_REPLAY` is on (`{targetId, maxSteps?}`; cancellable parent
sequencing compulsion gates from `decisionDigests`); digest summaries in Sight
only, not `/state`. Divine Console Phase 9 adds `crowd_compulsion` (batch
decision gates from shared duration/turns + per-target pinned decisions;
parent `crowdCompulsions`; private) and `dream_broadcast` (batch dream
`context_mask` from one shared snapshot; parent `dreamBroadcasts`; private).
Phase 4 added
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
`crowdCompulsions` entry (by parent id — closes all linked decision gates),
every `dreamBroadcasts` entry (by parent id — closes all linked dream masks),
every `dejaVuReplays` entry (by replay parent id — clears remaining sequenced
compulsion gates), every
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
including an id minted by an irreversible Phase 4 miracle, none of which is
ever stored in any of the searched stores — returns `{"ok": true,
"cancelled": false, "reason": "nothing to cancel", "targetId"}`, so miracle
ids are refused by construction rather than through a special-cased error.
(Proclamation applies as providence and is cancellable when still active.)
See
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-5--storyteller-events-and-timed-lawgiver-modifiers)
for the full Phase 5 contract.

**Viewer consumer (Phase 7).** The Divine Console (`simulation/index.html`,
see [11-viewer.md](11-viewer.md#divine-console-sovereign-god-mode-phase-7))
is the first and only client of all five routes. It reads
`previewOutcome`/`fingerprint.outgoingId`/`reversibilityClass` straight off
`god_preview()`'s response (documented in
[02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-2--secure-kernel))
to drive its preview→apply flow, and reads `god_sight()`'s `agents`/
`activeEvents`/`recentInterventions`/`recentDivineResponses`/`pulse` for its Sight
and Voice Adherence panels.

**Village pulse (Divine Console improvements, Phase 10).** Successful
`GET /control/god/sight` responses include an additive top-level `pulse`
object — **ephemeral**, derived live from world state at request time, never
stored in `godState` or persisted by save/restore. Shape:

| Field | Type | Notes |
|---|---|---|
| `crisisAgents` | `{id, name, reason}[]` | Living agents in survival crisis (incapacitated, low/critical health, starving/very hungry); capped ~8, worst first |
| `stockpileTotals` | `object` | Positive resource totals from `civilization.stockpile` |
| `openProjectsCount` | `int` | Count of active `districtProjects` entries |
| `sageStatus` | `object` | Elder summary: `present`, `status` (`living`/`critical`/`incapacitated`/`absent`), optional `name`/`role`/`health`/`hunger` |
| `weather` | `object` | Same projection as `/state` weather (`state`, `since`, `districts`) |
| `activeEventTitles` | `string[]` | Public-safe titles from active `activeEvents` (`title` for story events, else `kind`) |
| `providence` | `{active: bool, expiresFrame?}` | Timed providence window only — no guidance text |

No LLM calls; no `GOD_STATE_VERSION` bump. `/control/god/capabilities` documents that
`proclamation` applies as timed providence (optional `durationFrames`, same
slot/revoke/expiry as `providence`). Both `proclamation` and `providence`
payloads accept an optional cosmetic `presentation` enum (`"soft"` \| `"thunder"`;
default omit/`"soft"`) — validated at preview, audited in `divine.jsonl` via
the normalized command, stored on intervention/providence records and public
chronicle entries for viewer banner/chronicle styling only (cognition text
unchanged). No new preview/apply response fields — the viewer reads
`presentation` from `/state` `god.recentPublicInterventions`, `god.providence`,
and `world.chronicle` entries.

**Preview warnings (Divine Console improvements, Phase 7).** A successful
`POST /control/god/preview` response (`ok: true`) may include an additive
`warnings: string[]` field. Each entry is a short, secret-free human message
about non-fatal concerns in the normalized command — today, semantically
opposing timed-modifier keys on `story_event` (including Laws submissions,
which are `story_event` with modifiers only). Warnings never change
`ok`, never block Apply, and are omitted (or `[]`) when there is nothing to
report. Fatal validation still returns `ok: false` with `reason` only.
See [02-engine-core.md](02-engine-core.md#sovereign-god-mode-phase-5--storyteller-events-and-timed-lawgiver-modifiers)
for the conflict table evaluated at preview time.

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

## Anomaly radar (idea-07)

Expanded metric coverage and payload fields per idea-07b — anchor kept
stable as `#anomaly-radar-idea-07` for existing cross-references from
[11-viewer.md](11-viewer.md) and [12-ops.md](12-ops.md).

`GET /anomalies` is a **read-only** route (docs/plans/idea-07-anomaly-radar/plan.md
§2; expanded coverage and payload fields per
docs/plans/idea-07b-anomaly-console/plan.md §2). It adds no engine state and
no `/state` key — `simulation/sim_engine/mixin_decisions.py` and
`mixin_snapshot.py` are untouched (idea-07 Answers 1, 5; idea-07b confirms
the same — see idea-07b §3 "Not owning"). The engine is not modified; the
route is purely a server-side reader. No new flag: this route stays gated by
the existing `ANOMALY_RADAR_ENABLED` (idea-07b §6 — see "Combined kill
switch" below).

**Gate:** `ANOMALY_RADAR_ENABLED` (`sim_engine/constants.py`, default `True` —
see [01-architecture.md](01-architecture.md)'s flag index). Because the flag
gates only this route/reader and no engine mechanic, and `mixin_snapshot.py`
is out of scope for this feature, the flag's on/off state is **not** present
in `/state` `config.flags`. Instead the route's own response carries it (per
plan §6):

- **Flag off:** `{ok: true, enabled: false, anomalies: []}` — a clean no-op
  shape, not a 404/disabled error.
- **Flag on:** `{ok: true, enabled: true, anomalies: [...]}`.

**Combined kill switch (idea-07b §6).** `ANOMALY_RADAR_ENABLED = False` is
the sole flag for this whole feature — idea-07b deliberately adds no second
flag for the divine-bar button/modal it introduces. The route's `enabled`
field above is the single signal both viewer surfaces read to hide
themselves: the sidebar `#anomalySection` (wrapping the `#anomalyPanel`
`<details>`) and the divine-bar Anomaly `.gbtn` both key off the most recent
`GET /anomalies` response's `enabled` field, not
off `config.flags` (which does not carry this flag — see "Gate" above). See
[11-viewer.md](11-viewer.md#anomaly-panel-anomaly_radar_enabled) for exactly
how each surface hides.

**Detection source (Answers 1-2).** The handler reads a single thing,
scoped to the *current* server process's own run — no cross-run tailing of
older `simulation/logs/<timestamp>/` directories:

1. The current run's `benchmarks.jsonl`, located via the existing
   module-level `session_logger` reference (`session_logger.benchmark_path`
   / `session_logger.dir`, `simulation/_server/logging_session.py`) — the
   same in-process reference `server.py` already holds, not a fresh
   directory discovery/glob. Records already flushed to disk are visible;
   `log_benchmark`'s in-memory buffer (`BENCHMARK_BUFFER_MAX`, see
   [12-ops.md](12-ops.md)) flushes on its existing schedule — this route adds
   no new forced flush.

All three detected anomaly kinds, including faction_split, come from this single
source: no live-`SimEngine`/`civilization["chronicle"]` access and no
`self.lock` acquisition is needed. Faction split already writes a `metric: "faction_split"`
record to `benchmarks.jsonl` on every occurrence, independent of
`_sample_benchmarks()`'s periodic sampling — `_execute_faction_split`
(`simulation/sim_engine/mixin_governance_culture.py:486-491`) calls
`self._log_benchmark("faction_split", len(agents), {"parent": ..., "child": ...,
"belief": ..., "rule": ...})` directly, gated by `FACTION_SPLIT_ENABLED`/
`BENCHMARKS_ENABLED` (both default True), not by `CULTURE_ENABLED` (which
gates only the separate chronicle push a few lines earlier). This makes the
faction_split source consistent with the other two kinds — a pure
`benchmarks.jsonl` read.

**Detected anomaly kinds (idea-07 Answers 2-4, idea-07b §2 Answer 4; no other
kind is in scope — an "unusual death cluster" from the original idea text
was never answered in either plan and is not implemented):**

| `kind` | Source | Detection rule | Answer |
|---|---|---|---|
| `range_break` | `benchmarks.jsonl`, records whose `metric` is one of the **`RANGE_BREAK_METRICS` allowlist** (below) | Fires when a sampled value for an allowlisted metric exceeds every prior value seen so far **this run** for that same metric (a new session-lifetime max) or falls below every prior value seen so far this run for that same metric (a new session-lifetime min). Session-lifetime max/min tracking is per-metric (a separate running max/min per allowlisted metric name, not one shared max/min across all of them). No computed theoretical ceiling/floor; no bound recomputed as emergent roles are added. | idea-07 §2 Answer 3; idea-07b §2 Answer 4 |
| `new_rule_kind` | `benchmarks.jsonl`, `metric: "rule_kind_diversity"` records, `detail.kinds` | Fires on the first time a rule kind appears in `detail.kinds` that has not appeared in any earlier `rule_kind_diversity` record this run (mirrors `civilization["ruleKindsEverEnacted"]`). No per-rule-id tracking; no proposer-origin (LLM vs. deterministic auto-proposed) distinction. | idea-07 §2 Answer 4 |
| `faction_split` | `benchmarks.jsonl`, `metric: "faction_split"` records | Every `metric: "faction_split"` record is reported — written directly by `_execute_faction_split` on every faction split, independent of `_sample_benchmarks()`. | idea-07 §2 Answer 2 |

**`RANGE_BREAK_METRICS` allowlist (idea-07b §2 Answer 4, exhaustive — exactly
these 12, no others, no monotonic-counter auto-detection).** Named
module-level constant in `simulation/_server/anomaly_radar.py` so this list
and the code cannot drift:

```
specialization_entropy, rule_adherence, meme_adoption,
ecology_scarcity_index, wealth_gini, skill_spread, cultural_carryover,
peer_prediction_accuracy, contract_default_rate, storage_utilization,
structure_condition, population_median_age
```

All 12 are bounded/ratio metrics emitted by `_sample_benchmarks()`
(`simulation/sim_engine/mixin_decisions.py`). Monotonic counters emitted by
the same function — `memory_store_size`, `chronicle_size`,
`god_interventions`, `contracts_opened`, `contracts_fulfilled` — are
deliberately **excluded**: they set a new session max on nearly every sample
and would bury real signal under constant `range_break` noise. This is a
fixed curated list, not a computed "all numeric metrics minus detected
counters" heuristic — that broader option was considered and explicitly not
chosen (idea-07b §2 Answer 4).

**Response shape.** Each entry in `anomalies` is:

```
{timestamp, metric, kind, value, detail?, severity}
```

- `timestamp` — the record's `frame_tick` field, from the `benchmarks.jsonl`
  record, for all three kinds (including `faction_split`). This keeps `timestamp`
  one consistent type (frame tick, not wall-clock `ts`) across all three
  kinds, matching every other frame-tick-based field in `/state`. This same
  field **is** the "jump-to frame" reference the original idea-07 idea text
  asked for (idea-07b §2 Answer 2, "a jump-to frame reference — the original
  idea-07 text's 'jump-to link', which was never implemented"): no separate
  duplicate field is added, since `timestamp` already carries the frame
  number for every kind. The viewer surfaces it as a clearly labeled frame
  reference (see [11-viewer.md](11-viewer.md#anomaly-panel-anomaly_radar_enabled));
  there is no click-to-navigate/scrub behavior implemented, because this
  codebase has no timeline-scrub or replay UI to jump to — the reference is
  for the operator to manually correlate against `benchmarks.jsonl` or other
  log tooling, not an in-app navigation target.
- `metric` — for `range_break`, one of the 12 `RANGE_BREAK_METRICS` allowlist
  names above (was `"specialization_entropy"`-only before idea-07b); for
  `new_rule_kind`, `"rule_kind_diversity"`; for `faction_split`, `"faction_split"`.
- `value` — the triggering value: the allowlisted metric's float value
  (`range_break`), the new rule kind string (`new_rule_kind`), or the
  `faction_split` record's `value` field (agent count in the seceding cluster,
  `faction_split`).
- `detail` (optional) — kind-specific extra context, e.g. `{direction: "max"|"min"}`
  for `range_break`, or the `faction_split` record's own `detail`
  (`{parent, child, belief, rule}`) for `faction_split`.
- `severity` (new, idea-07b §2 Answer 2) — one of `"high"` / `"medium"` /
  `"low"`. `range_break` is **magnitude-scaled** against the prior
  session-lifetime bound it broke; `faction_split` and `new_rule_kind` have no
  magnitude to scale and use a fixed severity each (final decision,
  superseding the earlier fixed-per-`kind` mapping design, which was flagged
  during Phase 1 review as conveying no information beyond `kind` itself).
  All three rules below are computable during the single forward pass
  `compute_anomalies()` already makes over `benchmarks.jsonl` — no persisted
  detection state, no engine access, no lock, per idea-07 Answer 1.

  **`range_break` — magnitude-scaled.** For each allowlisted metric,
  `compute_anomalies()` already tracks a running `(prior_max, prior_min)`
  per metric name as it walks the file in order (needed to detect the break
  itself — see the `range_break` detection rule above). At the moment a
  record breaks the bound, the running `(prior_max, prior_min)` pair reflects
  every value seen for that metric **before** this record (i.e. captured
  prior to folding the current value into the running max/min). Compute:

  ```
  break_amount = value - prior_max   (max-break, direction == "max")
  break_amount = prior_min - value   (min-break, direction == "min")
  prior_range  = prior_max - prior_min
  ```

  `break_amount` is always `>= 0` by construction (the record only fires
  `range_break` because it exceeded that specific bound). Normalization
  basis is **`prior_range`** (the prior observed session-lifetime spread for
  that metric), not the bound value itself: all 12 `RANGE_BREAK_METRICS` are
  bounded/ratio metrics that can legitimately sit at or near 0 (e.g.
  `wealth_gini`, `contract_default_rate`), so normalizing against the raw
  bound (`value / prior_max`) would blow up or flip sign whenever a bound is
  0 or negative-adjacent; normalizing against the metric's own observed
  spread this run is well-defined for every metric in the allowlist and
  expresses "how big is this break relative to how much this metric has
  actually moved so far" — exactly the magnitude signal severity is meant to
  carry.

  ```
  ratio = break_amount / prior_range   (only when prior_range > 0)

  ratio <  0.25                -> "low"
  0.25 <= ratio < 1.0           -> "medium"
  ratio >= 1.0                  -> "high"
  ```

  Tier boundaries are exclusive-low/inclusive-high as written above: a break
  smaller than a quarter of the metric's entire prior spread is `"low"`; a
  break between a quarter and a full prior spread is `"medium"`; a break
  that equals or exceeds the metric's entire prior spread (a swing bigger
  than everything seen so far this run, combined) is `"high"`.

  **Degenerate case — `prior_range == 0`.** This is guaranteed on a metric's
  very first `range_break` this run (only one distinct value has been seen,
  so `prior_max == prior_min`) and can also recur later for a metric that
  was perfectly flat since the run started before this break. `ratio` is
  undefined here (0/0), so `compute_anomalies()` MUST special-case
  `prior_range == 0` and skip the division entirely — it never divides by
  `prior_range` without first checking it is `> 0`. The fixed result for
  this case is `"medium"`: there is no historical spread yet to judge
  magnitude against, so the reader falls back to the same neutral tier the
  old fixed-per-`kind` mapping used unconditionally for every `range_break`,
  rather than overstating an unmeasurable jump as `"high"` or dismissing it
  as `"low"`. (There is no separate "prior bound is 0" case to handle: 0 is
  a normal value for these bounded/ratio metrics and is never itself a
  denominator under range-based normalization — the only denominator is
  `prior_range`, already covered above.)

  **`faction_split` — fixed `"high"`.** Always `"high"`: a faction split is a
  civilization-splitting event structurally, not a continuous quantity —
  there is nothing to scale a magnitude against (the `value` field is the
  seceding cluster's agent count, not a bound the record broke).

  **`new_rule_kind` — fixed `"low"`.** Always `"low"`: a "new rule kind" is
  an inherently binary event (a kind either has appeared before this run or
  it hasn't) with no continuous quantity to normalize — routine cultural
  evolution, matching the original design's rationale.

**No per-kind grouping field added to the response.** idea-07b §2 Answer 2's
"grouping by kind, per-kind counts" is implemented entirely in the viewer,
client-side, from the same flat `anomalies` array documented above (see
[11-viewer.md](11-viewer.md#anomaly-panel-anomaly_radar_enabled)) — grouping
already-returned data by an existing field is not detection logic, so it
does not need a server-side change or violate the "pure renderer" viewer
contract. `GET /anomalies`'s shape is otherwise unchanged: `{ok, enabled,
anomalies: [...]}` at the top level.

No pagination/`since` cursor: the route recomputes the full anomaly list for
the current run's `benchmarks.jsonl` on every request (stateless server-side
reader, no new persisted detection state — idea-07 Answer 1).

**Consumer:** the viewer's anomaly panel and the divine-bar Anomaly modal
view (see
[11-viewer.md](11-viewer.md#anomaly-panel-anomaly_radar_enabled)), both
polling this route on their own cadence separate from `/state`.

## Decision audit route

`GET /decision-audit` is a read-only current-session join of `llm.jsonl` and
`activity.jsonl`. `DECISION_AUDIT_ENABLED` defaults to `True`; when disabled,
the route returns HTTP 200 with `{enabled: false, agents: [], recent: []}`
without reading either log. When enabled it returns `{enabled: true,
session_id, agents, recent}`. `agents` contains per-agent scored/match/
mismatch aggregates, and `recent` is a bounded newest-first list of scored
comparisons. The reader caches parsed sources by path, byte size, and mtime so
the viewer's three-second poll does not reparse unchanged logs. Correlation and
scoring semantics are canonical in [03-cognition.md](03-cognition.md) and
[12-ops.md](12-ops.md).

## Agent interview route {#agent-interview-route}

`POST /agent/interview` — read-only, out-of-world debug Q&A over a single
agent's own memory, relationships, and beliefs. "Click a villager, ask a
question, get an answer generated strictly from *that agent's* memory store,
relationships, and beliefs" (idea text). Distinct from
`burning_bush_message` (God speaking as a voice inside the world): an
interview answer is never authored *into* the world, is excluded from the
emergence record, and mutates nothing.

**No auth.** Unlike the `/control/god/*` routes, this route requires no
`X-God-Token`, regardless of `GOD_AUTH_REQUIRED`, and is reachable by direct
HTTP call whenever `AGENT_INTERVIEW_ENABLED` is on — independent of whether
`GOD_MODE_ENABLED` is on or off. This mirrors `/state`'s own no-auth
contract: an interview answer is generated strictly from private per-agent
state (memory tiers, `relationships`, `beliefs`) that is otherwise exposed,
filtered, only through `/state`'s agent snapshot (non-neutral `relationships`
only, no raw memory — [06-agents.md](06-agents.md)) or through the
authenticated `GET /control/god/sight`. The Divine Console's own trigger
button for this route is separately gated on `GOD_MODE_ENABLED` — see
[11-viewer.md](11-viewer.md#divine-console-sovereign-god-mode-phase-7) — but
that is a UI-visibility gate on the *button*, not on the route itself; a
server administrator with `GOD_MODE_ENABLED=False` still has a live,
unauthenticated `/agent/interview` route reachable by direct HTTP call (e.g.
`curl`), with no viewer button to trigger it.

**Not a God-mode intervention.** The route reads an agent's existing
`_agent_snapshot_row()` projection plus `agent["memory"]`/`agent["memoryWiki"]`
directly and returns an answer to the operator only. It writes nothing to
`agent["memory"]`, `beliefs`, `relationships`, `civilization`, or any
snapshot field; sets no `intervened` mark; and produces no `divine.jsonl`
entry, `divine_response`, activity line, or chronicle line. It never calls
`god_preview()`/`god_apply()` and is never listed in
`/control/god/capabilities`'s `kinds`. See
[01-architecture.md](01-architecture.md#control-plane-data-flow-sovereign-god-mode)
for the general God-mode control-plane boundary this route sits outside of.

**Flag gate.** `AGENT_INTERVIEW_ENABLED` (`simulation/sim_engine/constants.py`,
default `True`). When off, the route returns HTTP 200 with the clean-error
body shape below (`{"ok": false, "reason": "agent interview disabled"}`) and
performs no context assembly or LLM call — same "true no-op on the write
path, plain-200 body" shape `DECISION_AUDIT_ENABLED` uses above
(`{"enabled": False, "agents": [], "recent": []}`), not a silently-degraded
answer and not a distinct HTTP error status (Phase 2 implementer note:
resolves this section's earlier "non-200" wording in favor of matching
`DECISION_AUDIT_ENABLED`'s own actual precedent and the single unified
`{"ok": false, "reason": ...}` shape documented for every clean-error case
in the Response section above). Echoed in `/state` `config.flags` (see
[01-architecture.md](01-architecture.md)).

**Request.**

```json
{"agentId": 3, "question": "Who do you trust in the village, and why?"}
```

- `agentId` — required; must resolve to a living agent (an unknown or
  deceased agent id is rejected, not silently substituted).
- `question` — required, non-empty string, capped at
  `INTERVIEW_QUESTION_MAX_CHARS = 500` chars
  (`simulation/sim_engine/constants.py`) — see
  [03-cognition.md](03-cognition.md#agent-interview-operator-qa-out-of-world-debug-surface).
  A missing/non-string/empty/oversized `question` is rejected by the route
  itself, before any engine read or LLM call — following the same
  "clear error naming the offending field, reject rather than truncate"
  contract `GOD_COMPILER_PROSE_MAX_CHARS`'s `prose` validation uses.

**Response (success).**

```json
{
  "ok": true,
  "agentId": 3,
  "agentName": "Sage",
  "answer": "<agent-voiced answer, generated from that agent's own memory/relationships/beliefs>"
}
```

**Response (clean error — flag off, unknown agent, oversized/missing
question, occupied interview capacity, or `MEMORY_ENABLED`/`WIKI_MEMORY`
unavailable).**

```json
{"ok": false, "reason": "<short, specific machine-readable reason string>"}
```

**`MEMORY_ENABLED`/`WIKI_MEMORY` degrade path (non-default choice — refuse,
do not thin the answer).** The idea text promises an answer "generated
strictly from that agent's memory store, relationships, and beliefs."
`relationships` and `beliefs` are unconditional agent fields and always
exist, but the memory-store half (`agent["memory"]`'s three tiers, and the
`agent["memoryWiki"]` sections `WIKI_MEMORY` populates) exists only when
`MEMORY_ENABLED` is True. `run_agent_interview()` checks both flags
independently, in the same clean-error position (before agent lookup, before
any LLM call): when `MEMORY_ENABLED` is off, or when `WIKI_MEMORY` is off,
the route returns the clean-error shape above with a distinct `reason`
string naming the off flag, and makes **no** LLM call — it must not silently
answer from relationships/beliefs alone (or with an empty memoryWiki
section), since a thinner, unflagged answer would look identical to a full
one to the operator reading it. This is the opposite of
`god_compile_prose`'s pattern (which has no comparable partial-context mode
to begin with) and is a deliberate, non-default design choice for this route
specifically.

**Prompt construction, model, and concurrency pool.** Fully specced in
[03-cognition.md](03-cognition.md#agent-interview-operator-qa-out-of-world-debug-surface):
`_agent_snapshot_row()` called in-process (no HTTP round-trip) plus direct
`agent["memory"]`/`agent["memoryWiki"]` reads, `sim-smart` model, and the new
`INTERVIEW_CONCURRENT_LLM = 1` pool — independent of `MAX_CONCURRENT_LLM` and
`PIANO_CONCURRENT_LLM`.

Acquiring the dedicated slot is timed for one second. If another interview
still holds it, the route returns HTTP 200 with
`{"ok": false, "reason": "agent interview capacity unavailable; try again shortly"}`
and makes no LLM call; it never leaves a Flask worker blocked indefinitely.

**Never in the action-sync set.** This route adds no agent-facing action; it
never appears in `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/
`apply_decision`/`available_actions`/`ACTION_LABELS` — see
[01-architecture.md](01-architecture.md#action-sync-invariant). No agent ever
sees or chooses an "interview" action.

**Viewer.** [11-viewer.md](11-viewer.md#divine-console-sovereign-god-mode-phase-7)
— a new `.gbtn interview` Divine Console button, dual-gated on
`AGENT_INTERVIEW_ENABLED` and `GOD_MODE_ENABLED`, is this route's only
current client; the route itself remains callable independent of that UI
(see "No auth" above).

## Logging endpoints: fire-and-forget contract

`/log/event` and `/log/benchmark` both wrap their entire body in a bare
`try/except Exception: pass` and always return `("", 204)` — logging must
never break the simulation or the browser's fetch. `/memory/*` and
`/agent/module`/`/meta/update` follow the same pattern but return `{ok:
false}` (HTTP 200) instead of a bare 204 on failure, so callers can branch on
`ok` without a thrown exception ever reaching them. See
[specs/12-ops.md](12-ops.md) for the JSONL file formats these write to
(`activity.jsonl`, `conversation.jsonl`, `llm.jsonl`, `benchmarks.jsonl`).
