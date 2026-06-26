# SPEC 05 — Simulation Loop (LLM, Roles, Relationships, Building, UI)

**Build target.** Replaces rule-based movement with LLM-driven decisions; adds
roles/relationships/trading, the **civilization build pipeline**, the blueprint
flow, and the HTML sidebar. Gate: GATE E.

> The build/blueprint pipeline below was added after the original gates. See
> `.cursor/plans/fix_build_progression.plan.md` and
> `.cursor/plans/propose_blueprint_flow_6aa2dfb3.plan.md` for the design context.

## Game loop (`tick`, via `requestAnimationFrame`)

Each frame:

1. `frameTick++`, clear canvas, `drawWorld(ctx, frameTick)`.
2. For each agent: `moveAgent(agent)` (skipped while paused).
3. **Depth-sorted draw:** build one list of structures (`y + 34`) and agents
   (`y`), sort by `y`, then draw each — so closer things paint over farther ones
   and agents occlude/are-occluded by buildings correctly. Structures draw with a
   soft shadow.
4. Decrement each agent's `messageTimer`; clear `message` at 0.
5. While not paused, decrement each agent's `thinkTimer`; when it hits 0 and the
   agent is not already thinking or queued, `scheduleThink(agent)` and reset the
   timer.
6. Drain the think queue (see below).
7. `renderSidebar()` (HTML DOM, not canvas).

## Staggering and the concurrent LLM queue

Agents are staggered at startup, with the elder thinking more often:

```javascript
agents.forEach((a, i) => {
  a.thinkInterval = 360 + i * 60;   // spread out
  if (a.role === "elder") a.thinkInterval = 240;  // leader acts often
  a.thinkTimer = i * 30;            // stagger first calls
});
```

Decisions run through a **bounded-concurrency queue**, not one-at-a-time:

```javascript
const LLM_MIN_GAP_MS = 250;     // min spacing between dispatches
const MAX_CONCURRENT_LLM = 3;   // up to 3 calls in flight
```

`drainThinkQueue()` dispatches while `llmInFlight < MAX_CONCURRENT_LLM`, respecting
the min gap and a cooldown. The **elder is prioritized**: if the elder is anywhere
in the queue it is pulled to the front. This keeps throughput up and ensures the
leader gets turns to assign tasks and approve blueprints.

## thinkAgent(agent) — async

1. Mark `agent.isThinking`.
2. Build the request payload from agent state + civilization context
   (`getNearbyAgents()`, `getZone()`, active project, idle agents, known resources,
   pending blueprints, recent conversations, an optional behavior nudge, etc.).
3. `fetch("/agent/think", { method: "POST", ... })` — a **relative** URL; the page
   is served by Flask on port 5001.
4. On success: `applyDecision(agent, decision)` and update `lmStudioOnline`.
5. On failure / offline: rule-based wander fallback, `lmStudioOnline = false`.
6. Clear `agent.isThinking`; the queue drains the next agent.
7. Log the activity and (if a talk) the conversation via `POST /log/event`.

## applyDecision(agent, decision)

The action set is the full `AVAILABLE_ACTIONS` list (19 actions):

| decision.action | Effect |
|-----------------|--------|
| `move_to_<zone>` | `setAgentTarget(agent, zone)` |
| `move_to_agent` | move toward the target agent |
| `collect_resource` | add 1 of the current zone's resource (base or custom), max 5 |
| `talk_to_nearby` | set `message` + `messageTimer`; log the conversation |
| `trade_resource` | if target nearby: move 1 resource agent→target; nudge toward "ally" |
| `start_project` | `startProjectFor(agent, target)` — **any role** may start one |
| `contribute_resources` | add a needed resource to the active project (auto-starts a project, or routes the agent to gather, if none/insufficient) |
| `build_structure` | builder completes a fully-funded project → place structure, `completedProjects++`, `checkCivilizationLevel()` (auto-starts a project if none) |
| `propose_blueprint` | validate + queue a new structure/resource blueprint |
| `approve_blueprint` | elder only: merge the blueprint into the live registries |
| `reject_blueprint` | elder only: record the id as rejected |
| `assign_task` | elder only: set an idle agent's directive/assigned task |
| `change_role` | `agent.role = decision.new_role` |
| `rest` | do nothing |

After applying: push a one-sentence summary into `agent.memory` (keep last 5) and,
if `decision.relationship_update` is set, merge it into `agent.relationships`.

**Build-pipeline resilience (key fix):** `contribute_resources` and
`build_structure` with no active project no longer dead-end — they fall through to
`startProjectFor`, so the civilization always makes progress instead of stalling on
a missing `start_project` call.

## Resource collection rules

| Zone | Resource gained |
|------|-----------------|
| farm | food |
| forest | wood |
| cave | gold |
| beach (fishers only) | food |
| any zone | a custom resource whose `gatherZone` matches |

Max 5 of any single resource per agent.

## Trade rules

When `trade_resource` and the target is within 80px: remove 1 of the acting agent's
most-held resource, add 1 to the target, both push a memory line and nudge toward
"ally". If the acting agent has no resources, the action becomes `rest`.

## Building and the blueprint flow

- **Projects:** `civilization.activeProject` holds `needs` vs `contributed`. Seed
  templates: house, farm_plot, workshop, wall (`PROJECT_TEMPLATES`). Completing a
  project places a structure and may raise `civilization.level`.
- **Blueprints:** agents `propose_blueprint` to invent new structure types
  (optionally bundling new gatherable resources). Proposals are validated
  client-side (mirroring the server) and queued in `pendingBlueprints`. The **elder
  approves or rejects**; approval merges the new project/resources into
  `resourceRegistry` / `projectRegistry`; rejection records the id so it is not
  re-proposed.

## Role evolution

Roles change only when the LLM returns `change_role` with a `new_role`. The code
does not force role changes. Roles are open-ended; behavior is still driven by the
same action set.

## UI panel (HTML sidebar, `renderSidebar`)

The panel is an HTML `<aside id="sidebar">` (not canvas-drawn), updated from the
DOM:

| Element | Content |
|---------|---------|
| Title | "AI Simulation World" |
| Status dot | green = LM Studio reachable, red = offline (last fetch result) |
| Civilization stats | level, structures built, active project + progress bar, total village resources, custom builds, pending blueprints |
| Agent list | colored dot + name + current role, for the active roster |
| Conversation log | recent agent dialogue |
| Activity log | recent actions across all agents, newest first, auto-trimmed |

## Controls

- One HTML **Pause / Resume** button overlaid at the top-left. Paused: agents
  freeze and no `thinkAgent` calls fire, but `drawWorld` + `drawAgent` still run.
- A **tab-hidden warning** banner shows when the tab is backgrounded (the browser
  throttles `requestAnimationFrame`, which would starve the loop).

## Connection status detection

A global `lmStudioOnline` boolean is set `true` on any successful fetch and `false`
on an offline error. The status dot reflects it.

## Roster note

The simulation runs the **default 8-agent roster** (see `specs/04-agent-spec.md`),
configurable via `?agents=N`. References to "12 agents" in earlier drafts describe
the full roster, not the default.

## Gate E pass condition

- Active agents call LM Studio (staggered, up to 3 concurrent) and act on real
  decisions.
- Speech bubbles, resource bars, role badges update live.
- Trading, relationship changes, **project building, and the blueprint flow** are
  observable; the civilization level advances.
- Conversation and activity logs fill with real events.
- Status dot reflects LM Studio reachability; Pause/Resume works.
- Runs 10+ minutes without crashing or blocking.
