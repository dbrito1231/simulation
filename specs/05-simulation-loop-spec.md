# SPEC 05 — Simulation Loop (LLM, Roles, Relationships, UI)

**Build target.** Replaces rule-based movement with LLM-driven decisions, adds roles/relationships/trading and the UI panel. Gate: GATE E.

## Game loop

Use `requestAnimationFrame`. Each frame:

1. Clear canvas
2. `drawWorld(ctx)`
3. For each agent: `moveAgent(agent)`
4. For each agent: `drawAgent(ctx, agent)`
5. Update speech-bubble timers (decrement `messageTimer`, clear `message` at 0)
6. Decrement each agent's `thinkTimer`
7. If `thinkTimer <= 0` and `!agent.isThinking`: call `thinkAgent(agent)`, reset timer
8. `drawUIPanel(ctx)`

## Staggering LLM calls (prevents all 12 hitting LM Studio at once)

```javascript
agents.forEach((a, i) => {
  a.thinkInterval = 280 + i * 40;  // spread across ~8 seconds
  a.thinkTimer    = i * 40;         // stagger first calls
});
```

## thinkAgent(agent) — async

1. Set `agent.isThinking = true`.
2. Build request payload from agent state + `getNearbyAgents()` + `getZone()`.
3. `fetch("http://localhost:5000/agent/think", { method:"POST", ... })`.
4. On success: `applyDecision(agent, decision)`.
5. On failure: default to `rest`.
6. Set `agent.isThinking = false`.
7. Push a log line to the activity log.

## applyDecision(agent, decision)

| decision.action | Effect |
|-----------------|--------|
| `move_to_<zone>` | `setAgentTarget(agent, zone)` |
| `collect_resource` | add 1 of the current zone's resource (max 5) |
| `talk_to_nearby` | set `agent.message = decision.message`, `messageTimer = 180` |
| `trade_resource` | if target is nearby: move 1 resource agent→target; both nudge toward "ally" |
| `change_role` | `agent.role = decision.new_role` |
| `rest` | do nothing |

After applying: push a one-sentence summary into `agent.memory`, keep only last 5.
If `decision.relationship_update` is not null, merge it into `agent.relationships`.

## Resource collection rules

| Zone | Resource gained |
|------|-----------------|
| farm | food |
| forest | wood |
| cave | gold |
| beach (fishers only) | food |
| others | nothing |

Max 5 of any single resource per agent.

## Trade rules

When `trade_resource` and target agent is within 80px:
- Remove 1 resource from acting agent (pick the one it has most of).
- Add 1 of that resource to the target.
- Both agents push a memory line and nudge relationship toward "ally".
- If acting agent has no resources, the action becomes `rest`.

## Role evolution

Roles change only when the LLM returns `change_role` with a `new_role`. The code does not force role changes. Allowed roles are open-ended (the LLM may invent reasonable ones), but the agent's behavior is still driven by the same action set.

## UI panel (right 280px, drawn with Canvas)

| Element | Content |
|---------|---------|
| Title | "AI Simulation World" (white) |
| Status dot | green = LM Studio reachable, red = offline (based on last fetch result) |
| Agent list | colored dot + name + current role, for all 12 |
| Activity log | last 10 actions across all agents, newest at top, auto-trimmed |

Draw the panel as a dark semi-transparent `fillRect()` with text. No HTML elements except the one control below.

## Controls

One HTML `<button>` overlaid at the top-left: **Pause / Resume**.
- Paused: agents freeze, no `thinkAgent` calls, but `drawWorld` + `drawAgent` still run.

## Connection status detection

Track a global `lmStudioOnline` boolean. Set it `true` on any successful fetch, `false` when a fetch returns the `"LM Studio offline"` error. The status dot reflects this.

## Gate E pass condition

- All 12 agents call LM Studio (staggered) and act on real decisions.
- Speech bubbles, resource bars, role badges update live.
- Trading and relationship changes are observable.
- Activity log fills with real actions.
- Status dot reflects LM Studio reachability.
- Pause/Resume works.
- Runs 10+ minutes without crashing or blocking.
