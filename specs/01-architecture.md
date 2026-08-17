# SPEC 01 — Architecture

Server-authoritative topology: the Python engine owns all simulation state and runs
headless; the browser is a thin, stateless viewer.

**Canonical for:** topology, data-flow chain, threading/lock discipline, the
action-sync invariant, the complete flag index (flag → owning spec).
**See also:** [02-engine-core.md](02-engine-core.md) for the tick loop itself;
[03-cognition.md](03-cognition.md) for prompt/LLM detail; [07-actions.md](07-actions.md)
for the action catalog.

## Topology

- `simulation/sim_engine/` (`SimEngine`, defined in `core.py`) holds ALL world
  state (the `civilization` dict + `agents` list + `frameTick`/`paused`) behind
  a single `threading.RLock` (`self.lock`). It runs a fixed-timestep daemon
  thread and dispatches LLM "think" jobs to a bounded worker pool. The package
  splits module-level data (`constants.py`, `persistence.py`, `helpers.py`)
  from `SimEngine` behavior: `core.py` holds construction only, and 22
  `mixin_*.py` topic files (e.g. `mixin_think_job.py`, `mixin_decisions.py`,
  `mixin_world_state.py`) are `exec()`'d into a shared namespace by
  `__init__.py` so every method still resolves as `self.<name>` on one
  `SimEngine` instance — a pure move split, no behavior change.
- `simulation/server.py` is the Flask app plus the cognition layer: it builds
  prompts, calls Ollama, validates the response, and hands a decision back to the
  engine. It is the directly-runnable entry point and stays the single source
  for every `@app.route`/`add_url_rule` handler, `DECISION_ACTIONS`,
  `DECISION_SCHEMA`, and `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM`. Non-route helper
  logic (decision/blueprint/role/sprite validation, prompt-context formatting,
  the in-process memory store, per-session JSONL logging, model routing,
  Ollama structured-output error parsing, and the roles.json loader) lives in
  sibling modules under `simulation/_server/` that server.py imports from and
  re-exports at module level, so `import server; server.<name>` still resolves
  every name external callers (scripts/) rely on — pure move split, no
  behavior change.
- `simulation/index.html` (shell) + `simulation/viewer/*.js` (21 files) +
  `simulation/sprites/*.js` (8 files) + `simulation/css/*.css` (6 files) poll
  `GET /state` (~10 Hz, delta
  after the first full snapshot) and render;
  closing the browser tab does not stop the simulation. Delta responses include
  only keys whose server-side `lastMod` frame is greater than the client's
  `?since=` value (within `STATE_DELTA_MAX_GAP`); maps are pruned, not cleared
  per poll, so multiple viewers with different `since` cursors each receive
  one-time updates.

The engine mutates state only under `self.lock`; the full world is persisted to
`simulation/state.db` (see [02-engine-core.md](02-engine-core.md)).

## Data flow (one agent's think cycle)

1. Tick thread decrements `thinkTimer`; at 0 (and not already in-flight),
   `_schedule_think` submits a job to the executor (sim_engine/mixin_think_job.py:1417).
2. `_build_think_payload(agent)` (sim_engine/mixin_think_job.py:35) snapshots the agent's
   context **under the lock**, then releases the lock before the network call.
3. `run_agent_decision(payload)` (server.py:2006) prompts Ollama and extracts
   JSON.
4. `normalize_decision` (`_server/decision_validation.py:928`) +
   `role_fallback_action` (`_server/decision_validation.py:527`) reject invalid
   actions and substitute a safe fallback (imported into server.py's
   namespace; see Topology above).
5. Back inside `self.lock`, `apply_decision(agent, decision)` (sim_engine/mixin_decisions.py:346)
   mutates the world.

Network calls (step 3) always happen **outside** the lock so one agent's LLM latency
never blocks the tick thread or other agents' movement/mutation.

## Threading model

- Tick daemon: `SimEngine.start()` spawns a `SimEngine` thread running `_run_loop`,
  which calls `_tick_once()` once per `TICK_DT = 1.0 / TICKS_PER_SEC` seconds
  (`TICKS_PER_SEC = 30`, sim_engine/constants.py:1029).
- A second daemon thread (`SimSaver`) autosaves on its own timer — see
  [02-engine-core.md](02-engine-core.md).
- LLM dispatch: `self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM)`
  (sim_engine/core.py:182), with `MAX_CONCURRENT_LLM = 3` (sim_engine/constants.py:1300). An
  in-flight set (`self._inflight`) plus `LLM_MIN_GAP_MS = 250` (sim_engine/constants.py:1343)
  throttle dispatch further.
- **Lock discipline invariant:** every read/write of `civilization`/`agents`/
  `frameTick`/`paused` happens under `self.lock`. The only code that runs outside
  the lock is the LLM network call itself (step 3 above) and pure computation on a
  locally-copied payload.

## Action-sync invariant

Adding or changing a decision action requires touching every one of these
locations, or the engine and the LLM-facing schema will silently diverge:

| Location | File | What it defines |
|---|---|---|
| `DECISION_ACTIONS` | server.py:322 | The canonical action name list |
| `DECISION_SCHEMA` | server.py:357 | JSON-schema structured-output shape sent to Ollama |
| `SYSTEM_PROMPT` | server.py:32 (re-exported from prompts.py) | Prose description of each action for the model |
| `apply_decision` | sim_engine/mixin_decisions.py:346 | Server-side effect when an action is chosen |
| `available_actions` (payload) | sim_engine/mixin_think_job.py:667 | Flag-filtered action list actually offered to an agent this think |
| `ACTION_LABELS` | viewer/sidebar.js | Human-readable label shown in the viewer (display only, no logic) |

Full action-by-action detail (params, gates, effects) lives in
[07-actions.md](07-actions.md) — this file only states the invariant.

The emergent-role actions (`propose_role`, `approve_role`, `reject_role`) follow
this complete table as well: their role-object field is in `DECISION_SCHEMA`,
their instructions are in `SYSTEM_PROMPT`, and they are always offered.
Approval rebuilds the engine's live derived role maps; server startup maps
remain seed-only conveniences and are not a second role registry.

## Control-plane data flow (Sovereign God mode)

A second, deliberately separate control plane exists alongside the normal
agent think cycle above: `/control/god/*` (server.py), gated by
`GOD_MODE_ENABLED` (sim_engine/constants.py:644, always required) and, when
`GOD_AUTH_REQUIRED` is True (default False), a token check (server.py). It never
enters `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/`apply_decision`/
`available_actions`/`ACTION_LABELS` — the action-sync invariant above does not
apply to it, by design (a future agent-facing action, e.g. `pray`, would be a
separate change that does). God wildlife kinds (`wildlife_spawn`,
`wildlife_despawn`, `wildlife_set_hp` — [02-engine-core.md](02-engine-core.md))
are control-plane interventions under this same rule: they never join the
decision action-sync set.

- server.py checks the module flag before a request ever reaches the engine;
  when `GOD_AUTH_REQUIRED` is True, it also checks `X-God-Token` (constant-
  time compare). Every God route acquires `self.lock` itself once it does.
- `god_preview()` validates and normalizes a command with NO mutation,
  returning a short-lived, bounded, in-memory preview record.
- `god_apply()` accepts only `{previewId, requestId}` — never a client-
  returned command — resolves the server-held preview, revalidates it against
  current state, and applies atomically under the lock.
- `god_cancel()` / `god_sight()` are the remaining two entry points; see
  [02-engine-core.md](02-engine-core.md) for their Phase 2 shape.
- `god_compile_prose()` (Optional Phase 8) is a SECOND-gated entry point —
  `GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED` — that only ever produces a
  `god_preview()`-shaped draft; it never mutates and has no apply-adjacent
  behavior of its own. See [03-cognition.md](03-cognition.md#sovereign-god-mode-optional-phase-8-free-prose-story-compiler)
  and [12-ops.md](12-ops.md#optional-phase-8-free-prose-story-compiler).

`GOD_MODE_ENABLED` and `GOD_AUTH_REQUIRED` are env-backed in
`sim_engine/constants.py:644` / `:650` (`SIM_GOD_MODE`, `SIM_GOD_AUTH`; read
once at import). `SIM_GOD_TOKEN` stays in server.py only (token check lives
there).

## Flag index (complete — 55 module-level flags, sim_engine.py)

Semantics for each flag live in its owning spec; this table is the single
complete list and default state. "Echoed" = present in `/state`'s
`config.flags` (sim_engine/mixin_snapshot.py:256-298, built by `_build_snapshot_config`).

| Flag | Default | Echoed to viewer | Owning spec |
|---|---|---|---|
| `SURVIVAL_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `CRAFTING_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `STRUCTURE_EFFECTS_ENABLED` | True | no | [08](08-systems-economy.md) |
| `MEMORY_ENABLED` | True | no | [06](06-agents.md) |
| `PIANO_MODULES` | True | yes | [03](03-cognition.md) |
| `ALWAYS_ON_MODULES` | False | no | [03](03-cognition.md) |
| `RULES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `MEMES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `BENCHMARKS_ENABLED` | True | no | [12](12-ops.md) |
| `DETERMINISM_PINNING` | False (env-backed, `SIM_DETERMINISM_PINNING`) | no | [02](02-engine-core.md), [12](12-ops.md) |
| `ECOLOGY_ENABLED` | True | yes | [05](05-world.md) |
| `STRUCTURE_UPGRADES_ENABLED` | True | yes | [05](05-world.md) |
| `STRUCTURE_WEAR_ENABLED` | True | yes | [11](11-viewer.md) |
| `ACTIVITY_CUES_ENABLED` | True | yes | [11](11-viewer.md) |
| `CHRONICLE_SAGA_ENABLED` | True | yes | [09](09-systems-society.md#saga-chronicle_saga_enabled), [02](02-engine-core.md#chronicle-saga-chronicle_saga_enabled), [03](03-cognition.md), [12](12-ops.md) |
| `FOUNDING_EVENTS_ENABLED` | True | yes | [05](05-world.md) |
| `WORLD_CLOCK_HUD_ENABLED` | True | yes | [11](11-viewer.md) |
| `SEASONAL_AGENTS_ENABLED` | True | yes | [11](11-viewer.md) |
| `GOODS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `TECH_TREE_ENABLED` | True | yes | [09](09-systems-society.md) |
| `DAILY_COUNCIL_ENABLED` | True | no | [09](09-systems-society.md) |
| `ECONOMY_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `CONTRACTS_ENABLED` | True | yes | [07](07-actions.md), [08](08-systems-economy.md) |
| `LIFECYCLE_ENABLED` | True | yes | [06](06-agents.md) |
| `DYNASTY_TREE_ENABLED` | True | yes | [06](06-agents.md) (Divine Lineage viewer — Phase 3) |
| `CULTURE_ENABLED` | True | yes | [09](09-systems-society.md) |
| `PATH1_ENABLED` | True | yes | [10](10-path1.md) |
| `INDUSTRY_ENABLED` | True | yes (as `INDUSTRY_ENABLED`) | [10](10-path1.md) |
| `TOOL_TIERS_ENABLED` | True | yes | [10](10-path1.md) |
| `COMPOSABLE_BUILD_ENABLED` | True | yes | [10](10-path1.md) |
| `TERRAIN_TILES_ENABLED` | True | yes | [10](10-path1.md) |
| `PATH1_DIPLOMACY_ENABLED` | True | yes (as `DIPLOMACY_ENABLED`) | [10](10-path1.md) |
| `TIER3_CONTENT_ENABLED` | True | yes | [10](10-path1.md) |
| `PRESSURE_LOOP_ENABLED` | True | yes | [10](10-path1.md) |
| `RAIDERS_CONTAGION_ENABLED` | True | yes | [10](10-path1.md) |
| `ENV_EFFECTS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `WIKI_MEMORY` | True (D2 soak on `main` — see [03](03-cognition.md)) | yes | [03](03-cognition.md) |
| `TESTAMENT_ENABLED` | True | yes | [06](06-agents.md), [09](09-systems-society.md) |
| `THEORY_OF_MIND_ENABLED` | False (env-backed, `SIM_THEORY_OF_MIND`) | yes | [03](03-cognition.md) |
| `FACTION_SPLIT_ENABLED` | True | yes | [09](09-systems-society.md#faction_split_enabled) |
| `CROP_GROWTH_ENABLED` | True | yes | [05](05-world.md) |
| `WILDLIFE_ENABLED` | True | yes | [05](05-world.md) (authoritative fauna + hunt + motion; also [02](02-engine-core.md), [07](07-actions.md), [08](08-systems-economy.md), [11](11-viewer.md)) |
| `WILDLIFE_BEHAVIOR_ENABLED` | True | yes | [02](02-engine-core.md#huntable-wildlife-wildlife_enabled) (graze/wander/flee/rest state machine + loose herding) |
| `CARAVAN_VISUALS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `WEATHER_ENABLED` | True | yes | [05](05-world.md) |
| `WEATHER_GOVERNANCE_ENABLED` | True | yes | [05](05-world.md) |
| `GOD_MODE_ENABLED` | True (env-backed, `SIM_GOD_MODE`; disable via `0`/`false`/`no`/`off`) | yes | [02](02-engine-core.md), [04](04-http-api.md) |
| `GOD_AUTH_REQUIRED` | False (env-backed, `SIM_GOD_AUTH`; enable via `1`/`true`/`yes`/`on`) | yes | [04](04-http-api.md), [12](12-ops.md) |
| `GOD_COMPILER_ENABLED` | False (env-backed, `SIM_GOD_COMPILER`) | no (advertised only via `/control/god/capabilities`'s `compiler.enabled`, not `config.flags`) | [03](03-cognition.md), [04](04-http-api.md), [12](12-ops.md) |
| `GOD_DEJA_VU_REPLAY` | False (env-backed, `SIM_GOD_DEJA_VU_REPLAY`) | yes | [02](02-engine-core.md), [04](04-http-api.md), [12](12-ops.md) |
| `ANOMALY_RADAR_ENABLED` | True | no (own state carried by `GET /anomalies`'s `enabled` field, not `config.flags` — the engine is not modified, so there is no `/state` key to echo it into) | [04](04-http-api.md), [12](12-ops.md) |
| `DECISION_AUDIT_ENABLED` | True | no (own state carried by `GET /decision-audit`'s `enabled` field, not `config.flags`) | [03](03-cognition.md), [04](04-http-api.md), [12](12-ops.md) |
| `WORLD_WIKI_ENABLED` | True | yes | [04](04-http-api.md), [11](11-viewer.md) |
| `PREDICTION_MARKET_ENABLED` | True | yes | [04](04-http-api.md), [11](11-viewer.md) |
| `AGENT_INTERVIEW_ENABLED` | True | yes | [03](03-cognition.md), [04](04-http-api.md), [11](11-viewer.md) |

`DECISION_AUDIT_ENABLED` gates both engine-side correlation-id minting
(`run_agent_decision` to `llm.jsonl` and `apply_decision` to `activity.jsonl`)
and the dedicated `/decision-audit` reader. When off, neither log stream
carries the field and the route returns `{enabled: false, ...}`. The viewer
learns the state from that route's `enabled` field.
**`DYNASTY_TREE_ENABLED` kill switch and scoping.** Plain module-level boolean
(`simulation/sim_engine/constants.py`, Phase 1) — default **True**, echoed in
`/state` `config.flags`. Kill switch: flip the constant to `False` and restart;
no env override by default (consistent with most flags in this index). Because
`parents`/`children` recording is folded into the existing
`LIFECYCLE_ENABLED`-gated birth path, this flag gates the **viewer Divine
Lineage panel** (Phase 3) and the `_heirs_of` children-array read — **not**
whether `children` is written at birth (the write stays unconditional within
`LIFECYCLE_ENABLED`, same as `parents` today).

`DECISION_AUDIT_ENABLED` gates **both** engine-side correlation-id minting
(`run_agent_decision` → `llm.jsonl` `decision._decision_id` and
`apply_decision` → `activity.jsonl` `decision_id`) **and** the dedicated
`/decision-audit` reader route. When off, neither log stream carries the field
and the route returns `{enabled: false, …}` — a true no-op on both write and
read paths, not merely a disabled panel. The flag is **not** echoed in `/state`
`config.flags`; the viewer learns on/off state from the route's own `enabled`
field (same pattern as idea-07's dedicated-route viewer echo). Whether an env
override (e.g. `SIM_DECISION_AUDIT`) is wired is an implementer-phase choice,
documented in [03-cognition.md](03-cognition.md) once made.

`PREDICTION_MARKET_ENABLED` gates **server-side route behavior** on the three
`/predictions/*` routes (`POST /predictions/submit`, `POST /predictions/resolve`,
`GET /predictions/history`) — when off, they perform no `predictions.json`
file I/O and return their disabled shapes (see [04-http-api.md](04-http-api.md#prediction-market-routes))
— **and** viewer rendering of the prediction panel. It is distinct from
engine-mechanic flags: no `_tick_once` system reads it; the only
`sim_engine/` footprint is the `constants.py` definition plus the
`config.flags` echo line in `_build_snapshot_config`
(`mixin_snapshot.py:256-298`). Unlike `memory_store.json`, whose entries mirror
into `state.db`'s `memory` table and can reach agent think-payloads,
`predictions.json` is never read by `_build_think_payload()`, any
`mixin_*.py`, or `save_state()` / `restore_state()` — see
[02-engine-core.md](02-engine-core.md#persistence).

`AGENT_INTERVIEW_ENABLED` gates the read-only `POST /agent/interview` route
and is echoed in `/state` `config.flags`. The route is independent of God-mode
auth and never mutates world state; its Divine Console button is separately
visible only while both `AGENT_INTERVIEW_ENABLED` and `GOD_MODE_ENABLED` are
true. Concurrency and prompt behavior are canonical in
[03-cognition.md](03-cognition.md#agent-interview-operator-qa-out-of-world-debug-surface).

`civilization["godState"]["version"]` is `GOD_STATE_VERSION` (`3` after Divine
Console Phase 8 — `decisionDigests`, `dejaVuReplays`, and Phase 9 placeholder
maps); persisted private maps from that shape never appear in `/state` `god`
(see [02-engine-core.md](02-engine-core.md)).
