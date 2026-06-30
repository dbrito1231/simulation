# Engine-port interface contracts (FROZEN — Step 0)

Authoritative interfaces for the server-authoritative engine port
([server_authoritative_engine.plan.md](server_authoritative_engine.plan.md)).
Subagents build against THESE; do not change them without the orchestrator.

These shapes are ported verbatim from the current browser engine
(`simulation/index.html`: `civilization` ~589, `makeAgents()` ~986). The Python
engine must preserve the same field names so behavior and the viewer match.

---

## Contract 1 — Python state model (in-memory, server-side)

`civilization` (dict):
```
level:int, structures:list, activeProject:dict|None, completedProjects:int,
nextStructureId:int, resourceRegistry:dict, projectRegistry:dict,
pendingBlueprints:list, rejectedBlueprintIds:set, pendingRecipes:list,
rejectedRecipeIds:set, directive:str|None,
lastProjectContributionFrame:int, lastBlueprintActivityFrame:int,
lastCraftActivityFrame:int, lastRuleActivityFrame:int, lastRoleSwitchFrame:int,
collectAttempts:int, collectSuccesses:int,
rules:list, pendingRules:list, stockpile:dict, taxDue:int, taxPaid:int
```

`agent` (dict), one per villager:
```
id, name, role, personality, color,
x, y, targetX, targetY, speed,
memory:{working:[], shortTerm:[], longTerm:[]},
resources:dict, relationships:dict, inbox:list, beliefs:set, votes:dict,
currentZone, message:str|None, messageTimer:int,
thinkTimer:int, thinkInterval:int, isThinking:bool,
lastAction:str|None, lastReasoning:str|None, consecutiveTalks:int,
pendingThink:bool, assignedTask:str|None, idleCycles:int,
lastTaskedFrame:int|None, lastContributedFrame:int|None, consecutiveIdleMoves:int,
hunger:int, health:int, incapacitated:bool, goal:dict|None,
actionCounts:dict, persona:str, modules:dict, idleFrames:int
```

Engine globals: `frameTick:int`, `paused:bool`, plus the feature-flag config
constants (SURVIVAL_ENABLED, USE_GOALS, EMERGENT_ROLES, RULES_ENABLED,
MEMES_ENABLED, CRAFTING_ENABLED, META_SYSTEM=False, PIANO_MODULES=False) and the
cadence/tuning constants ported from index.html.

Sets (`beliefs`, `rejectedBlueprintIds`, `rejectedRecipeIds`) are Python sets in
memory and serialize to JSON arrays.

---

## Contract 2 — GET /state response (what the viewer consumes)

Returns a consistent snapshot (taken under the engine lock):
```json
{
  "frameTick": 1234,
  "paused": false,
  "lmStatus": "online",
  "agents": [
    {"id","name","role","color","x","y","currentZone","resources",
     "hunger","health","incapacitated","message","isThinking",
     "beliefs":[...], "lastAction","assignedTask"}
  ],
  "civilization": {
    "level","structures":[{"id","type","x","y","visualStyle","name"}],
    "activeProject": {"name","type","progressText","progressPercent"} | null,
    "completedProjects",
    "resourceRegistry","projectRegistry",
    "pendingBlueprints","pendingRecipes","rules","pendingRules",
    "directive","stockpile","taxDue","taxPaid",
    "collectAttempts","collectSuccesses"
  },
  "benchmarks": { "entropy","adoption","adoptionRate","adherence","rules",
                  "structures","level","memory" },
  "activity": ["...up to ~30 recent lines..."],
  "conversation": [{"kind","from","to","message"}],
  "config": { "WORLD_W":1600,"WORLD_H":1000,
              "flags": {"SURVIVAL_ENABLED":true, ...} }
}
```
The viewer renders agents/structures with the existing `sprites.js` +
`drawWorld`/`drawAgent`, and fills the sidebar from `civilization` + `benchmarks`
+ `activity` + `conversation`. Field names match the current renderer's
expectations so `sprites.js` is reused unchanged.

Control endpoints: `POST /control/pause`, `POST /control/resume`,
`POST /control/reset` (body `{agents:N}` optional).

---

## Contract 3 — state.json persistence shape (full resume)

Atomic write (`os.replace`) to `simulation/state.json`; restored on startup if
present and valid, else cold-start via the roster builder.
```json
{
  "version": 1,
  "frameTick": 1234,
  "savedAt": "ISO-8601",
  "civilization": { ...Contract-1 civilization, sets as arrays... },
  "agents": [ { ...Contract-1 agent, beliefs as array, memory tiers inline... } ],
  "memory": [ { "id","agent","text","salience","kind","tier","frame_tick","ts" } ]
}
```
`memory` is the MemoryStore's entries WITHOUT the `vec` field (recomputed on
load via `embed_text`). On restore, rehydrate sets from arrays and rebuild
MemoryStore (re-embedding each entry).

---

## Ownership (who writes what — avoids file collisions)

- **Agent B** owns all of `simulation/server.py` engine + API (Contracts 1 & 2):
  state model, SimEngine tick thread, ported `applyDecision`, LLM worker pool,
  `GET /state` + control endpoints.
- **Agent D** owns `simulation/index.html` only: strip the engine, render from
  `GET /state` (Contract 2). Reuses `simulation/sprites.js` unchanged. May build
  against a small inline mock of Contract 2 for self-test; final validation is
  post-merge by the orchestrator.
- **Agent C** adds persistence (Contract 3) to `simulation/server.py` AFTER B is
  merged (same file → sequential).
- **Orchestrator** owns merges and end-to-end validation.
