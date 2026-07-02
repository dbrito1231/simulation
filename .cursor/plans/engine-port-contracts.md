# Engine-port interface contracts (FROZEN — Step 0)

Authoritative interfaces for the server-authoritative engine port
([server_authoritative_engine.plan.md](server_authoritative_engine.plan.md)),
extended by the world-expansion plan
([world_expansion_districts_roads.plan.md](world_expansion_districts_roads.plan.md))
for districts/roads/concurrent builds. Subagents build against THESE; do not
change them without the orchestrator.

These shapes are ported verbatim from the current browser engine
(`simulation/index.html`: `civilization` ~589, `makeAgents()` ~986). The Python
engine must preserve the same field names so behavior and the viewer match.

---

## Contract 1 — Python state model (in-memory, server-side)

`civilization` (dict):
```
level:int, structures:list, districtProjects:dict, districtLastContribution:dict,
completedProjects:int,
nextStructureId:int, resourceRegistry:dict, projectRegistry:dict,
pendingBlueprints:list, rejectedBlueprintIds:set, pendingRecipes:list,
rejectedRecipeIds:set, directive:str|None,
districts:dict, roadNodes:dict, roadEdges:list, frontierPlots:list,
kindLastActivityFrame:dict, lastDistrictFoundFrame:int, frontierExhaustedLogged:bool,
lastBlueprintActivityFrame:int,
lastCraftActivityFrame:int, lastRuleActivityFrame:int, lastRoleSwitchFrame:int,
collectAttempts:int, collectSuccesses:int,
rules:list, pendingRules:list, stockpile:dict, taxDue:int, taxPaid:int
```

World-expansion additions (all runtime state, seeded from the hand-authored
`STARTER_DISTRICTS`/`STARTER_ROAD_NODES`/`STARTER_ROAD_EDGES` module constants
at cold-start, mutated thereafter by district-founding -- NOT static
constants once the sim is running):
- `districts: {district_id: {kind, tile, label, bounds:{x1,y1,x2,y2},
  build_grid:{x0,y0,cols,dx,dy,cap}|None, entryNode}}` -- the live district
  registry. `kind` groups districts for resource/tile purposes (multiple
  districts can share a kind); `entryNode` names the district's road-graph
  "front door".
- `roadNodes: {node_id: {x,y}}`, `roadEdges: [[node_id, node_id], ...]`
  (undirected) -- the live road graph agents route through.
- `frontierPlots: [{id, x1,y1,x2,y2, claimed:bool, claimedBy:str|None}, ...]`
  -- the claimable frontier plot grid `_maybe_found_district()` allocates from.
- `districtProjects: {district_id: project|None}` (one key per buildable
  district) replaces the old singular `activeProject`; `project` shape is
  unchanged (`{type,name,needs,contributed,visualStyle,districtId}`).
- `districtLastContribution: {district_id: frame_tick}` replaces
  `lastProjectContributionFrame`.
- `kindLastActivityFrame: {kind: frame_tick}`, `lastDistrictFoundFrame:int`,
  `frontierExhaustedLogged:bool` -- founding-trigger bookkeeping.

`agent` (dict), one per villager:
```
id, name, role, personality, color,
x, y, targetX, targetY, speed,
memory:{working:[], shortTerm:[], longTerm:[]},
resources:dict, relationships:dict, inbox:list, beliefs:set, votes:dict,
currentZone, currentDistrict:str|None, waypoints:list,
message:str|None, messageTimer:int,
thinkTimer:int, thinkInterval:int, isThinking:bool,
lastAction:str|None, lastReasoning:str|None, consecutiveTalks:int,
pendingThink:bool, assignedTask:str|None, idleCycles:int,
lastTaskedFrame:int|None, lastContributedFrame:int|None, consecutiveIdleMoves:int,
hunger:int, health:int, incapacitated:bool, goal:dict|None,
actionCounts:dict, persona:str, modules:dict, idleFrames:int
```
`currentZone` keeps its pre-districts meaning (kind, back-compat).
`currentDistrict` (new) carries the specific district id, needed for
build-grid/road targeting. `waypoints` (new) is the cached road-node path (plus
a final random interior point) `_move_agent` walks through when `ROADS_ENABLED`.

`structures` entries gain `districtId:str` (which district a built structure
belongs to).

Engine globals: `frameTick:int`, `paused:bool`, plus the feature-flag config
constants (SURVIVAL_ENABLED, USE_GOALS, EMERGENT_ROLES, RULES_ENABLED,
MEMES_ENABLED, CRAFTING_ENABLED, ROADS_ENABLED, META_SYSTEM=False,
PIANO_MODULES=False), `MAX_CONCURRENT_PROJECTS`, `MAX_TOTAL_DISTRICTS`, and the
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
    {"id","name","role","color","x","y","currentZone","currentDistrict",
     "waypoints": 2, "resources",
     "hunger","health","incapacitated","message","isThinking",
     "beliefs":[...], "lastAction","assignedTask"}
  ],
  "civilization": {
    "level","structures":[{"id","type","x","y","visualStyle","name","districtId"}],
    "districtProjects": {"<district_id>": {"name","type","progressText","progressPercent"} | null, ...},
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
  "config": { "WORLD_W":5200,"WORLD_H":5400,
              "flags": {"SURVIVAL_ENABLED":true, "ROADS_ENABLED":true, ...} }
}
```
`civilization.districtProjects` (plural, per-district) replaces the old
singular `activeProject`. `agents[].waypoints` here is the remaining-waypoint
*count* (an int), not the raw `{x,y}` list Contract 1's in-memory agent holds
-- enough to see it "drain as an agent travels" without bloating every ~10Hz
`/state` poll with coordinate arrays nothing in the viewer currently renders.
The viewer renders agents/structures with the existing `sprites.js` +
`drawWorld`/`drawAgent`, and fills the sidebar from `civilization` +
`benchmarks` + `activity` + `conversation`.

The live `districts`/`roadNodes`/`roadEdges` are served separately via
**`GET /districts.js`** (world-expansion plan) rather than bloating every
~10Hz `/state` poll with mostly-static data:
```json
{
  "districts": [{"id","kind","tile","label","bounds":{"x1","y1","x2","y2"},
                 "buildGrid":{"x0","y0","cols","dx","dy","cap"}|null}, ...],
  "roadNodes": {"<node_id>": {"x","y"}, ...},
  "roadEdges": [["<node_id>","<node_id>"], ...]
}
```
This route reads the engine's LIVE state under its lock (like `/state` does),
not a static constant, so a district founded mid-session appears to a
connected viewer on its next poll without a page reload. The viewer polls it
on a slower cadence (a few seconds) than `/state` since foundings are rare.

Control endpoints: `POST /control/pause`, `POST /control/resume`,
`POST /control/reset` (body `{agents:N}` optional).

---

## Contract 3 — state.json persistence shape (full resume)

Atomic write (`os.replace`) to `simulation/state.json`; restored on startup if
present and valid, else cold-start via the roster builder.
```json
{
  "version": 2,
  "frameTick": 1234,
  "savedAt": "ISO-8601",
  "civilization": { ...Contract-1 civilization, sets as arrays... },
  "agents": [ { ...Contract-1 agent, beliefs as array, memory tiers inline... } ],
  "memory": [ { "id","agent","text","salience","kind","tier","frame_tick","ts" } ]
}
```
`memory` is the MemoryStore's entries WITHOUT the `vec` field (recomputed on
load via `embed_text`). On restore, rehydrate sets from arrays and rebuild
MemoryStore (re-embedding each entry). The new `districts`/`roadNodes`/
`roadEdges`/`frontierPlots`/`districtProjects`/`districtLastContribution`
fields are plain dicts/lists and round-trip through the existing generic
serialize/restore copy path with no special-casing needed (unlike the
`_CIV_SET_KEYS` sets).

**Migration (`STATE_VERSION` 1 → 2):** `restore_state()` accepts a
pre-districts version-1 save and runs it through `_migrate_v1_to_v2()`, which
seeds `districts`/`roadNodes`/`roadEdges`/`frontierPlots` from the starter
blueprint, seeds `districtProjects`/`districtLastContribution` empty per
buildable district, derives each agent's `currentDistrict` from its old
`currentZone` (+ empty `waypoints`), and drops the old singular
`activeProject`/`lastProjectContributionFrame` (a one-time, low-stakes loss of
an in-flight build only -- agent identity/memory/resources/relationships all
carry over untouched). `_validate_districts()`/`_validate_road_graph()` re-run
against the migrated/restored state before it goes live.

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
