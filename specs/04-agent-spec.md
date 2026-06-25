# SPEC 04 — Agents (Sprites, State, Movement)

**Build target.** Adds a configurable active roster to `index.html`: drawn, then moving with rule-based logic. No LLM yet. Gate: GATE D.

## The 8 active agents by default

The simulation defaults to 8 active agents to improve local LLM throughput and reduce canvas clutter. A URL override may raise the active count for experiments, but the default roster must include the builder and elder.

| # | Name | Start Role | Personality | Body Color |
|---|------|-----------|-------------|-----------|
| 1 | Zara | builder | creative, methodical | `#9C27B0` |
| 2 | Sage | elder | wise, slow-moving | `#FFC107` |
| 3 | Aria | farmer | hardworking, cautious | `#4CAF50` |
| 4 | Luna | gatherer | curious, adventurous | `#2196F3` |
| 5 | Marco | trader | sociable, opportunistic | `#FF9800` |
| 6 | Colt | miner | stubborn, hardworking | `#795548` |
| 7 | Finn | fisher | patient, quiet | `#00BCD4` |
| 8 | Mia | healer | empathetic, generous | `#E91E63` |

Inactive by default but available through roster overrides: Rex (guard), Ivy (scout), Dex (blacksmith), and Nova (explorer).

## Original sprite art (do NOT use the uploaded character image)

Each agent is drawn with Canvas primitives, ~24×32 px:

1. Head: `arc()` circle, 10px radius, skin tone `#FDBCB4`
2. Body: `fillRect()` 12×16px in the agent's unique color
3. Arms: two short `lineTo()` strokes
4. Legs: two short `lineTo()` strokes
5. Name: white 10px text centered below the sprite
6. Role badge: tiny colored dot + role initial above the head
7. Speech bubble: white rounded rect above head when `agent.message` is set
8. Resource bar: tiny dots below the name (green=food, brown=wood, yellow=gold)

Write one reusable function: `drawAgent(ctx, agent)`.

## Agent state object

```javascript
{
  id: 1,
  name: "Aria",
  role: "farmer",
  personality: "hardworking and cautious",
  color: "#4CAF50",
  x: 500, y: 120,
  targetX: 500, targetY: 120,
  speed: 1.2,
  memory: [],
  resources: { food: 2, wood: 0, gold: 0 },
  relationships: {},
  currentZone: "farm",
  message: null,
  messageTimer: 0,
  thinkTimer: 0,
  thinkInterval: 300,
  isThinking: false,
  lastAction: null,
  lastReasoning: null,
  assignedTask: null
}
```

## Starting positions

Spawn active agents spread across zones (not all in one spot). Suggested: Aria→farm, Marco→market, Zara→village, Luna→forest, Finn→beach, Colt→cave, Sage→village, Mia→village. Use `ZONE_CENTERS` from Spec 03.

## Special personality note

Sage (elder) is slow: set `speed: 0.6`, but give Sage a shorter think interval so the leader acts often. Ivy (scout) and Nova (explorer) are fast when included through a roster override.

## Helper functions to write

| Function | Behavior |
|----------|----------|
| `getNearbyAgents(agent, allAgents)` | returns names of agents within 80px |
| `moveAgent(agent)` | moves toward `targetX/targetY` by `speed`; stops when reached |
| `setAgentTarget(agent, zoneName)` | sets target to that zone's center coords |

## Rule-based movement for THIS gate (no LLM)

So GATE D is testable without LM Studio, give each agent simple placeholder behavior: when an agent reaches its target, pick a random zone and move there. This is temporary scaffolding — Spec 05 replaces it with LLM decisions.

## Gate D pass condition

- The default 8 active agents are drawn as distinct sprites with names and role badges.
- Agents move smoothly between zones (rule-based).
- Speech bubble and resource-bar drawing code exists (even if unused yet).
- No console errors. No LLM calls yet.
