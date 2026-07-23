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
  prompts, calls LM Studio, validates the response, and hands a decision back to the
  engine.
- `simulation/index.html` + `simulation/sprites.js` poll `GET /state` (~10 Hz) and
  render; closing the browser tab does not stop the simulation.

The engine mutates state only under `self.lock`; the full world is persisted to
`simulation/state.db` (see [02-engine-core.md](02-engine-core.md)).

## Data flow (one agent's think cycle)

1. Tick thread decrements `thinkTimer`; at 0 (and not already in-flight),
   `_schedule_think` submits a job to the executor (sim_engine.py:9362).
2. `_build_think_payload(agent)` (sim_engine.py:8527) snapshots the agent's
   context **under the lock**, then releases the lock before the network call.
3. `run_agent_decision(payload)` (server.py:2978) prompts LM Studio and extracts
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
| `DECISION_SCHEMA` | server.py:780 | JSON-schema structured-output shape sent to LM Studio |
| `SYSTEM_PROMPT` | server.py:885 | Prose description of each action for the model |
| `apply_decision` | sim_engine.py:7885 | Server-side effect when an action is chosen |
| `available_actions` (payload) | sim_engine.py:9143 | Flag-filtered action list actually offered to an agent this think |
| `ACTION_LABELS` | index.html:1357 | Human-readable label shown in the viewer (display only, no logic) |

Full action-by-action detail (params, gates, effects) lives in
[07-actions.md](07-actions.md) — this file only states the invariant.

## Flag index (complete — 34 module-level flags, sim_engine.py)

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
| `PIANO_MODULES` | False | yes | [03](03-cognition.md) |
| `META_SYSTEM` | False | yes | [03](03-cognition.md) |
| `EMERGENT_ROLES` | True | yes | [06](06-agents.md) |
| `RULES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `MEMES_ENABLED` | True | yes | [09](09-systems-society.md) |
| `BENCHMARKS_ENABLED` | True | no | [12](12-ops.md) |
| `ECOLOGY_ENABLED` | True | yes | [05](05-world.md) |
| `ROADS_ENABLED` | True | yes | [05](05-world.md) |
| `STRUCTURE_UPGRADES_ENABLED` | True | yes | [05](05-world.md) |
| `GOODS_ENABLED` | True | yes | [08](08-systems-economy.md) |
| `TECH_TREE_ENABLED` | True | yes | [09](09-systems-society.md) |
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
