# SPEC 01 — Architecture

Server-authoritative topology: the Python engine owns all simulation state and runs
headless; the browser is a thin, stateless viewer.

**Canonical for:** topology, data-flow chain, threading/lock discipline, the
action-sync invariant, the complete flag index (flag → owning spec).
**See also:** [02-engine-core.md](02-engine-core.md) for the tick loop itself;
[03-cognition.md](03-cognition.md) for prompt/LLM detail; [07-actions.md](07-actions.md)
for the action catalog.

## Topology

- `simulation/sim_engine.py` (`SimEngine`) holds ALL world state (the `civilization`
  dict + `agents` list + `frameTick`/`paused`) behind a single `threading.RLock`
  (`self.lock`). It runs a fixed-timestep daemon thread and dispatches LLM "think"
  jobs to a bounded worker pool.
- `simulation/server.py` is the Flask app plus the cognition layer: it builds
  prompts, calls Ollama, validates the response, and hands a decision back to the
  engine.
- `simulation/index.html` (shell) + `simulation/viewer.js` + `simulation/sprites.js`
  poll `GET /state` (~10 Hz) and render; closing the browser tab does not stop
  the simulation.

The engine mutates state only under `self.lock`; the full world is persisted to
`simulation/state.db` (see [02-engine-core.md](02-engine-core.md)).

## Data flow (one agent's think cycle)

1. Tick thread decrements `thinkTimer`; at 0 (and not already in-flight),
   `_schedule_think` submits a job to the executor (sim_engine.py:9362).
2. `_build_think_payload(agent)` (sim_engine.py:8527) snapshots the agent's
   context **under the lock**, then releases the lock before the network call.
3. `run_agent_decision(payload)` (server.py:2978) prompts Ollama and extracts
   JSON.
4. `normalize_decision` (server.py:2025) + `role_fallback_action` (server.py:1890)
   reject invalid actions and substitute a safe fallback.
5. Back inside `self.lock`, `apply_decision(agent, decision)` (sim_engine.py:7885)
   mutates the world.

Network calls (step 3) always happen **outside** the lock so one agent's LLM latency
never blocks the tick thread or other agents' movement/mutation.

## Threading model

- Tick daemon: `SimEngine.start()` spawns a `SimEngine` thread running `_run_loop`,
  which calls `_tick_once()` once per `TICK_DT = 1.0 / TICKS_PER_SEC` seconds
  (`TICKS_PER_SEC = 30`, sim_engine.py:238-239).
- A second daemon thread (`SimSaver`) autosaves on its own timer — see
  [02-engine-core.md](02-engine-core.md).
- LLM dispatch: `self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM)`
  (sim_engine.py:1267), with `MAX_CONCURRENT_LLM = 3` (sim_engine.py:444). An
  in-flight set (`self._inflight`) plus `LLM_MIN_GAP_MS = 250` (sim_engine.py:445)
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
| `DECISION_ACTIONS` | server.py:752 | The canonical action name list |
| `DECISION_SCHEMA` | server.py:780 | JSON-schema structured-output shape sent to Ollama |
| `SYSTEM_PROMPT` | server.py:885 | Prose description of each action for the model |
| `apply_decision` | sim_engine.py:7885 | Server-side effect when an action is chosen |
| `available_actions` (payload) | sim_engine.py:9143 | Flag-filtered action list actually offered to an agent this think |
| `ACTION_LABELS` | viewer.js | Human-readable label shown in the viewer (display only, no logic) |

Full action-by-action detail (params, gates, effects) lives in
[07-actions.md](07-actions.md) — this file only states the invariant.

The emergent-role actions (`propose_role`, `approve_role`, `reject_role`) follow
this complete table as well: their role-object field is in `DECISION_SCHEMA`,
their instructions are in `SYSTEM_PROMPT`, and the engine filters them when
`EMERGENT_ROLES` is disabled. Approval rebuilds the engine's live derived role
maps; server startup maps remain seed-only conveniences and are not a second role
registry.

## Control-plane data flow (Sovereign God mode)

A second, deliberately separate control plane exists alongside the normal
agent think cycle above: `/control/god/*` (server.py), gated by
`GOD_MODE_ENABLED` (sim_engine.py, always required) and, when
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

`GOD_MODE_ENABLED` is also the **first** env-var-backed flag in
`sim_engine.py` (`os.environ.get("SIM_GOD_MODE", ...)`, read once at import).
`GOD_AUTH_REQUIRED` is likewise env-backed in sim_engine.py (`SIM_GOD_AUTH`,
default False). Every prior env-var precedent (`SIM_HOST`, `SIM_PORT`,
`SIM_AGENTS`, `SIM_LOG_RETENTION`) lives only in server.py; sim_engine.py
previously read no environment state at all. The companion `SIM_GOD_TOKEN`
env var stays in server.py only, since the token check itself lives there.

## Flag index (complete — 52 module-level flags, sim_engine.py)

Semantics for each flag live in its owning spec; this table is the single
complete list and default state. "Echoed" = present in `/state`'s
`config.flags` (sim_engine.py:10023-10047).

| Flag | Default | Echoed to viewer | Owning spec |
|---|---|---|---|
| `SURVIVAL_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `CRAFTING_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `USE_GOALS` | True | yes | [08](08-systems-economy.md) |
| `STRUCTURE_EFFECTS_ENABLED` | True | no | [08](08-systems-economy.md) |
| `MEMORY_ENABLED` | True | no | [06](06-agents.md) |
| `AGENT_MESSAGING` | True | no | [06](06-agents.md) |
| `PIANO_MODULES` | True | yes | [03](03-cognition.md) |
| `ALWAYS_ON_MODULES` | False | no | [03](03-cognition.md) |
| `META_SYSTEM` | True | yes | [03](03-cognition.md) |
| `EMERGENT_ROLES` | True | yes | [06](06-agents.md) |
| `RULES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `MEMES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `BENCHMARKS_ENABLED` | True | no | [12](12-ops.md) |
| `ECOLOGY_ENABLED` | True | yes | [05](05-world.md) |
| `ROADS_ENABLED` | True | yes | [05](05-world.md) |
| `STRUCTURE_UPGRADES_ENABLED` | True | yes | [05](05-world.md) |
| `STRUCTURE_WEAR_ENABLED` | True | yes | [11](11-viewer.md) |
| `ACTIVITY_CUES_ENABLED` | True | yes | [11](11-viewer.md) |
| `SOCIAL_LAYER_ENABLED` | True | yes | [09](09-systems-society.md) |
| `CHRONICLE_ENABLED` | True | yes | [09](09-systems-society.md) |
| `FOUNDING_EVENTS_ENABLED` | True | yes | [05](05-world.md) |
| `WORLD_CLOCK_HUD_ENABLED` | True | yes | [11](11-viewer.md) |
| `SEASONAL_AGENTS_ENABLED` | True | yes | [11](11-viewer.md) |
| `GOODS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `TECH_TREE_ENABLED` | True | yes | [09](09-systems-society.md) |
| `DAILY_COUNCIL_ENABLED` | True | no | [09](09-systems-society.md) |
| `SAGE_REVIEW_ENABLED` | True | no | [09](09-systems-society.md) |
| `ECONOMY_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `LIFECYCLE_ENABLED` | True | yes | [06](06-agents.md) |
| `CULTURE_ENABLED` | True | yes | [09](09-systems-society.md) |
| `CEMETERY_ENABLED` | True | yes | [05](05-world.md) |
| `PATH1_ENABLED` | True | yes | [10](10-path1.md) |
| `INDUSTRY_ENABLED` | True | yes (as `INDUSTRY_ENABLED`) | [10](10-path1.md) |
| `TOOL_TIERS_ENABLED` | True | yes | [10](10-path1.md) |
| `COMPOSABLE_BUILD_ENABLED` | True | yes | [10](10-path1.md) |
| `TERRAIN_TILES_ENABLED` | True | yes | [10](10-path1.md) |
| `PATH1_DIPLOMACY_ENABLED` | True | yes (as `DIPLOMACY_ENABLED`) | [10](10-path1.md) |
| `TIER3_CONTENT_ENABLED` | True | yes | [10](10-path1.md) |
| `PRESSURE_LOOP_ENABLED` | True | yes | [10](10-path1.md) |
| `ENV_EFFECTS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `LIBRARY_SCALING_ENABLED` | True | yes | [09](09-systems-society.md) |
| `TRANSIT_ENABLED` | True | yes | [10](10-path1.md) |
| `ECONOMY_SINKS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `WIKI_MEMORY` | False | yes | [03](03-cognition.md) |
| `CROP_GROWTH_ENABLED` | True | yes | [05](05-world.md) |
| `WILDLIFE_ENABLED` | True | yes | [05](05-world.md) (authoritative fauna + hunt + motion; also [02](02-engine-core.md), [07](07-actions.md), [08](08-systems-economy.md), [11](11-viewer.md)) |
| `CARAVAN_VISUALS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `WEATHER_ENABLED` | True | yes | [05](05-world.md) |
| `WEATHER_GOVERNANCE_ENABLED` | True | yes | [05](05-world.md) |
| `GOD_MODE_ENABLED` | True (env-backed, `SIM_GOD_MODE`; disable via `0`/`false`/`no`/`off`) | yes | [02](02-engine-core.md), [04](04-http-api.md) |
| `GOD_AUTH_REQUIRED` | False (env-backed, `SIM_GOD_AUTH`; enable via `1`/`true`/`yes`/`on`) | yes | [04](04-http-api.md), [12](12-ops.md) |
| `GOD_COMPILER_ENABLED` | False (env-backed, `SIM_GOD_COMPILER`) | no (advertised only via `/control/god/capabilities`'s `compiler.enabled`, not `config.flags`) | [03](03-cognition.md), [04](04-http-api.md), [12](12-ops.md) |
| `GOD_DEJA_VU_REPLAY` | False (env-backed, `SIM_GOD_DEJA_VU_REPLAY`) | yes | [02](02-engine-core.md), [04](04-http-api.md), [12](12-ops.md) |

`civilization["godState"]["version"]` is `GOD_STATE_VERSION` (`2` after Divine
Matrix scaffolding); persisted private maps from that shape never appear in
`/state` `god` (see [02-engine-core.md](02-engine-core.md)).
