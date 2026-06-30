---
name: Server-authoritative engine — persistence, headless, LAN access
overview: "Today the entire simulation engine (the agents array, civilization object, the requestAnimationFrame tick loop, applyDecision, movement, survival, goals, rules, memes, the deterministic triggers — ~3,200 lines across 121 JS functions in index.html) runs in the BROWSER. The Flask server (server.py) is a stateless prompt proxy that binds to 127.0.0.1 only. Consequences: (1) progress vanishes on restart/reload because nothing is persisted; (2) the sim only advances while a focused browser tab is open, so it cannot run headless; (3) it is not reachable from other devices. Per the user's decision, port the engine into Python as a server-authoritative SimEngine that ticks in a background thread, persists FULL world state to disk, and exposes the world as a read-only snapshot; the browser becomes a thin viewer that polls /state and renders with the existing sprites.js. Bind 0.0.0.0 for LAN access. This keeps the project's minimal/single-process/no-external-services ethos (no Node, no headless Chromium). Much of the 'brain' already exists server-side (normalize_decision, role_fallback_action, MemoryStore, prompt building, lm_complete), so the port is mainly world-mutation/physics + the loop + state model."
todos:
  - id: orchestrate-contracts
    content: "Step 0 (orchestrator) — freeze the three interface contracts before dispatching subagents: (1) the /state JSON schema the viewer consumes, (2) the Python state model shape (civilization dict + agent dict keys), (3) the state.json persistence shape. Then dispatch subagents in their own git worktrees: Agent A = Infra/LAN (Phase 1, independent), Agent B = Engine core (Phases 2-5, critical path, single-threaded on server.py), Agent C = Persistence (Phase 7, against the frozen shape), Agent D = API + thin viewer (Phases 6+8, against the frozen /state contract). Orchestrator independently verifies each subagent's gate (does not trust self-reports), merges in order A->B->C->D, then runs full end-to-end verification and re-dispatches failures with evidence."
    status: pending
  - id: lan-bind
    content: "Phase 1 (req #3, ships independently) — server.py: bind app.run(host='0.0.0.0', port=5001, threaded=True) behind a HOST constant; document finding the host LAN IP and opening port 5001 in Windows Firewall; note the LAN-exposure caveat (also exposes the LM Studio proxy)."
    status: pending
  - id: state-model
    content: "Phase 2 — server.py: define the authoritative Python state model mirroring the browser: a civilization dict (level, structures, activeProject, registries, pendingBlueprints/recipes, rules/pendingRules, stockpile, tax counters, frame counters) and an agents list (name, role, x/y, targetX/Y, currentZone, speed, resources, hunger/health, incapacitated, relationships, beliefs set, inbox, goal, assignedTask, action/idle counters, memory tiers). Load roles.json (already loaded) for starting roster; keep feature flags (SURVIVAL_ENABLED, USE_GOALS, EMERGENT_ROLES, RULES_ENABLED, MEMES_ENABLED, CRAFTING_ENABLED, META_SYSTEM, PIANO_MODULES) as server config constants."
    status: pending
  - id: engine-tick
    content: "Phase 3 — server.py SimEngine: a background daemon thread running a fixed-timestep loop (e.g. 20-30 ticks/s via time.sleep, decoupled from any client) guarded by a state lock. Port the per-frame steps from index.html tick(): moveAgent (delta-time/step), updateSurvival (SURVIVAL_TICK_FRAMES), runMemoryMaintenance, maybeAutoSwitchRole, maybeAdvanceRules, spreadBeliefsByProximity, sampleBenchmarks (FIRST_BENCHMARK_FRAME + cadence), goal stepping (stepGoal), Sage-emergency rush (sageEmergency/sageResponders/rushToHeal), and think scheduling (thinkTimer/scheduleThink). Reuse existing SessionLogger + benchmark logging."
    status: pending
  - id: port-applydecision
    content: "Phase 4 — server.py: port applyDecision (the 27-case world-mutation switch) to Python: rest, move_to_* , move_to_agent, collect_resource, contribute_resources, build_structure, start_project, craft_item, trade_resource, heal_agent, talk_to_nearby, assign_task, switch_role/change_role, propose_blueprint/approve_blueprint/reject_blueprint, propose_recipe/approve_recipe/reject_recipe, propose_rule/vote_rule, plus helpers (goalForDecision, stepGoal, enforceResourceTax, transmitBelief, the message bus deliverMessage/inbox). normalize_decision/role_fallback_action already exist — wire them in front of applyDecision so the same validation guards the engine."
    status: pending
  - id: llm-worker-pool
    content: "Phase 5 — server.py: replace the browser's drainThinkQueue with a server-side bounded worker pool (ThreadPoolExecutor max_workers = MAX_CONCURRENT_LLM, default 2 to match LM Studio slots; LLM_MIN_GAP throttle). The tick thread enqueues a think job for an agent whose thinkTimer fired (never blocking on the LLM); the worker builds the prompt (existing path), calls LM Studio, runs normalize_decision, and applies the result via applyDecision UNDER THE STATE LOCK. Mirrors the browser's async queue + in-flight guard (incl. Sage-emergency discard)."
    status: pending
  - id: state-api
    content: "Phase 6 — server.py: GET /state returns a JSON snapshot (under lock) of everything the viewer needs to render: agents (positions, role, resources, hunger/health, message, incapacitated, beliefs), civilization (structures, project + progress, level, rules, registries, pending*), lmStatus, frameTick, and the latest benchmarks + recent activity/conversation tails. Add control endpoints: POST /control/pause|resume|reset and ?agents=N on reset. Keep /agent/think? No — thinking is now internal; remove client think calls."
    status: pending
  - id: persistence
    content: "Phase 7 (req #1, full state) — server.py: serialize the complete state (agents + civilization + memory store entries + frameTick + rng/counters) to logs/state.json (or a fixed simulation/state.json) with atomic os.replace; autosave every N seconds AND on SIGINT/atexit graceful shutdown. On startup, if state.json exists and is valid, restore from it instead of makeAgents(); else cold-start. Convert sets (beliefs, rejectedRecipeIds) to lists in JSON and back. Provide a reset that clears state.json."
    status: pending
  - id: thin-client
    content: "Phase 8 (req #2) — index.html: strip the engine. Remove the local agents/civilization state, tick() simulation steps, applyDecision, thinkAgent/queue, survival/goals/rules/meme logic. Keep ONLY the render path: a rAF loop that fetches GET /state (poll ~5-10/s), stores the snapshot, and draws it with the existing drawWorld/drawAgent/sprites.js + renderSidebar. Pause/Resume buttons call the control endpoints. The page is now a pure viewer; closing it does not stop the sim."
    status: pending
  - id: concurrency-safety
    content: "Cross-cutting — a single threading.Lock (or RLock) serializes all state mutation: the tick thread, the LLM worker callbacks (applyDecision), and the /state snapshot read all acquire it. Keep critical sections short (snapshot = copy under lock, serialize outside). Ensure persistence reads a consistent snapshot under lock."
    status: pending
  - id: verification
    content: "Verification — run server headless (no browser); confirm via JSONL logs + GET /state (curl) that frameTick advances and structures/projects progress with NO browser open. Restart the server and confirm /state resumes the same world (level, structures, agent positions, memory) from state.json. From a second device on the LAN, open http://<host-ip>:5001 and confirm the viewer renders the live world. Confirm pause/resume/reset endpoints work and LM context errors stay absent under the worker-pool cap."
    status: pending
isProject: false
---

# Server-authoritative engine: persistence, headless, LAN access

## Context

The simulation engine currently runs entirely in the **browser**
(`index.html`: the `agents` array at `~1035`, `civilization` at `~589`, the
`tick()` rAF loop at `~3132`, `applyDecision` at `~2061`, plus movement,
survival, goals, rules, memes, and the deterministic triggers — ~3,200 lines /
121 functions). `server.py` is a **stateless prompt proxy** that binds to
`127.0.0.1:5001` only (`~1668`). That produces the three problems the user wants
fixed:

1. **No persistence** — restart/reload reinitializes the world from scratch.
2. **Not headless** — the sim only advances while a focused browser tab is open
   (it's driven by `requestAnimationFrame`).
3. **Not networked** — `127.0.0.1` bind means no other device can reach it.

**Decision (user):** port the engine into Python as a **server-authoritative
SimEngine** (background thread), persist **full world state**, and make the
browser a **thin viewer**. No Node, no headless Chromium — consistent with the
project's minimal/single-process/no-external-services ethos.

## Target architecture

```
server.py
  ├─ SimEngine (daemon thread): fixed-timestep loop, owns ALL state, holds a Lock
  │     tick(): move • survival • memory/role/rule/meme triggers • goals •
  │             Sage-emergency • benchmark sample • dispatch think jobs
  ├─ LLM worker pool (ThreadPoolExecutor, max = MAX_CONCURRENT_LLM):
  │     build prompt → LM Studio → normalize_decision → applyDecision (under Lock)
  ├─ Persistence: autosave full state → state.json (atomic), restore on startup
  └─ HTTP: GET /state (snapshot) • POST /control/{pause,resume,reset} • static files

index.html (thin viewer)
  └─ rAF loop: GET /state (poll) → render with sprites.js / drawAgent / renderSidebar
     (no engine, no think calls; closing the tab does not stop the sim)
```

The server already has a head start: `normalize_decision()`,
`role_fallback_action()`, `MemoryStore`, the prompt builder, and `lm_complete()`
all exist and are reused. The port is mainly the **world-mutation/physics** side
(`applyDecision`, movement, survival, goals, the deterministic triggers) plus
the loop, the state model, persistence, and the state API.

## Phases (suggested order)

1. **LAN bind (#3)** — smallest, ships first and independently: `host="0.0.0.0"`,
   `threaded=True`, firewall + host-IP docs, exposure caveat.
2. **State model** — the authoritative Python `civilization` dict + `agents` list.
3. **Engine tick** — the background fixed-timestep loop + ported per-frame steps.
4. **Port `applyDecision`** — the 27-action world-mutation switch + helpers,
   fronted by the existing `normalize_decision`/`role_fallback_action`.
5. **LLM worker pool** — non-blocking think dispatch + apply-under-lock.
6. **State API + controls** — `GET /state`, pause/resume/reset.
7. **Persistence (#1)** — full-state `state.json`, atomic autosave + restore.
8. **Thin client (#2)** — strip the engine from `index.html`, keep rendering.

Build the SimEngine alongside the working browser engine; flip the client to
viewer-mode only once `/state` parity is validated, to avoid a long broken state.

## Multi-agent execution (subagents + orchestrator)

Execute this plan as a team of subagents coordinated by a single **orchestrator
agent**. Because `server.py` and `index.html` are each one tightly-coupled file,
parallelism is along **file / interface-contract boundaries**, not by splitting a
file across simultaneous editors. Each subagent works in its **own git worktree**
(`isolation: "worktree"`) so edits don't collide; the orchestrator integrates and
validates.

**Step 0 — Orchestrator freezes the contracts (before any subagent starts):**
1. The **`/state` JSON schema** (exact fields the viewer consumes — agents,
   civilization, lmStatus, frameTick, benchmarks, log tails).
2. The **Python state model shape** (the `civilization` dict + `agent` dict keys),
   which Phases 2–7 all build against.
3. The **persistence file shape** (`state.json` top-level keys).
Freezing these lets the engine and viewer subagents work in parallel against a
stable interface.

**Subagents (run in parallel where dependencies allow):**
- **Agent A — Infra/LAN (Phase 1).** Independent; can run immediately and merge
  first. Deliverable: `0.0.0.0` bind + firewall/host-IP docs. Gate: server
  reachable from a second device.
- **Agent B — Engine core (Phases 2–5).** The critical path, all in `server.py`:
  state model, SimEngine tick thread, `applyDecision` port, LLM worker pool. Done
  by **one** subagent (sequential, single file). Gate: headless run advances
  `frameTick` and progresses projects with no browser.
- **Agent C — Persistence (Phase 7).** Depends on B's state model (Step-0
  contract #2/#3); may start against the frozen shape and integrate after B. Gate:
  restart resumes an identical world from `state.json`.
- **Agent D — API + thin client (Phases 6 + 8).** Phase 6 (`/state` + controls)
  in `server.py` and Phase 8 (viewer) in `index.html`, both written against the
  Step-0 `/state` contract — so D can build the viewer in parallel with B and
  validate against a stub `/state` until B is merged. Gate: viewer renders the
  live world and controls work.

**Orchestrator responsibilities:**
- Author and freeze the Step-0 contracts; hand each subagent its scope + the
  relevant contract.
- Sequence/merge worktrees in dependency order (A → B → C → D), resolving any
  `server.py` integration seams (B owns the engine block; C and D append their
  own endpoints/sections).
- After each subagent reports done, **independently verify** — do not take the
  subagent's word: run the subagent's gate (curl `/state`, headless tick check,
  restart-resume, LAN open) and read the JSONL logs.
- Run the **full end-to-end Verification** (below) only after all merges, and
  drive the fixes for any subagent whose gate fails (re-dispatch with the
  failure evidence rather than patching silently).
- Keep the browser engine working until D's viewer reaches `/state` parity, then
  flip to viewer-only.

> Note: subagents are the expensive path and start cold — the orchestrator must
> give each one self-contained context (the frozen contract + file paths + its
> gate). Parallel gains are real for A and D; B is inherently sequential.

## Key risks / decisions

- **Faithful port.** The deterministic triggers, Sage-emergency, goals, tax, and
  meme/role logic must match the JS behavior; port function-by-function and spot-
  check against the current browser behavior. Highest-effort, highest-risk part.
- **Tick rate.** Run the engine at a modest fixed rate (e.g. 20–30 ticks/s) to
  cut CPU vs. the browser's 60fps; movement uses the existing step logic. The
  viewer polls independently (~5–10 Hz) and interpolates if needed.
- **Threading.** One `Lock` serializes tick, LLM-callback `applyDecision`, the
  `/state` snapshot, and persistence. The tick thread NEVER blocks on the LLM
  (jobs go to the pool); results apply on callback under the lock.
- **Sets in JSON.** `beliefs`, `rejectedRecipeIds`, etc. serialize as lists and
  rehydrate as sets on load.
- **Feature flags** move from `index.html` consts to server config; the viewer
  reflects them via `/state`.
- **Security.** `0.0.0.0` exposes the server (and the LM Studio proxy) to the
  LAN — acceptable on a trusted home network; documented, not hardened.

## Verification

- **Headless (#2):** start the server with **no browser open**; `curl` `GET /state`
  repeatedly and watch `logs/<ts>/activity.jsonl` — `frameTick` advances and
  projects/structures progress with no client.
- **Persistence (#1):** let it build a few structures, restart the server, and
  confirm `GET /state` resumes the same world (level, structures, agent
  positions, rules, memory) from `state.json`.
- **LAN (#3):** from a phone/laptop on the same network open
  `http://<host-ip>:5001` and confirm the live world renders; pause/resume/reset
  work from the viewer.
- **Stability:** under the worker-pool cap, confirm no `Context size has been
  exceeded` in `lm_studio_server.log` and no fallback storms in `lm_studio.jsonl`.
