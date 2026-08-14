// =====================================================================
// World snapshot. Everything the renderer reads comes from here. Seeded with a
// Contract-2 mock so the page renders standalone when the server is unreachable
// (self-test / offline). Replaced wholesale by each successful /state poll.
// =====================================================================
const MOCK_STATE = {
  frameTick: 0,
  paused: false,
  lmStatus: "offline",
  agents: [
    {
      id: 0, name: "Sage", role: "elder", color: "#E1BEE7",
      x: 760, y: 730, currentZone: "village",
      resources: { wood: 2, food: 1 },
      hunger: 90, health: 100, incapacitated: false,
      message: "Let us build.", isThinking: false,
      beliefs: ["harvest_spirit"], lastAction: "assign_task", assignedTask: null
    },
    {
      id: 1, name: "Zara", role: "builder", color: "#90CAF9",
      x: 620, y: 640, currentZone: "village",
      resources: { wood: 3, stone: 1 },
      hunger: 70, health: 80, incapacitated: false,
      message: null, isThinking: true,
      beliefs: [], lastAction: "build_structure", assignedTask: "build the House"
    },
    {
      id: 2, name: "Marco", role: "farmer", color: "#A5D6A7",
      x: 700, y: 260, currentZone: "farm",
      resources: { food: 4 },
      hunger: 55, health: 100, incapacitated: false,
      message: "Gathering food.", isThinking: false,
      beliefs: ["harvest_spirit"], lastAction: "collect_resource", assignedTask: null
    }
  ],
  civilization: {
    level: 1,
    structures: [
      { id: 1, type: "house", x: 600, y: 600, visualStyle: "house", name: "House", districtId: "village_core" }
    ],
    districtProjects: {
      village_core: { name: "Workshop", type: "workshop",
                      progressText: "wood 2/3, stone 1/2", progressPercent: 50 },
      farm_north: { name: "Farm Plot", type: "farm_plot",
                   progressText: "wood 1/2, food 1/1", progressPercent: 65 },
      farm_south: null, village_east: null, workshop_row: null
    },
    completedProjects: 1,
    resourceRegistry: {
      food:  { name: "Food",  gatherZone: "farm",    color: "#4CAF50" },
      wood:  { name: "Wood",  gatherZone: "forest",  color: "#795548" },
      gold:  { name: "Gold",  gatherZone: "cave",    color: "#FFC107" },
      stone: { name: "Stone", gatherZone: "cave",    color: "#9E9E9E" },
      fish:  { name: "Fish",  gatherZone: "beach",   color: "#4FC3F7" },
      meat:  { name: "Meat",  gatherZone: null,      color: "#C62828" },
      herbs: { name: "Herbs", gatherZone: "forest",  color: "#8BC34A" },
      water: { name: "Water", gatherZone: "village", color: "#03A9F4" },
      planks:{ name: "Planks",gatherZone: null,      color: "#C19A6B", crafted: true },
      bricks:{ name: "Bricks",gatherZone: null,      color: "#B7410E", crafted: true }
    },
    projectRegistry: {
      house:    { name: "House" },
      workshop: { name: "Workshop" },
      watchtower:{ name: "Watchtower" }
    },
    recipes: {
      planks: { name: "Planks", inputs: { wood: 1 } },
      bricks: { name: "Bricks", inputs: { stone: 2 } }
    },
    pendingBlueprints: [
      { id: "watchtower", name: "Watchtower", proposedBy: "Sage" }
    ],
    pendingRecipes: [],
    rules: [ { id: "tax1", name: "Resource tax 1/turn" } ],
    pendingRules: [ { id: "rule2", votes: { Sage: "yes", Zara: "no" } } ],
    directive: "Build the Workshop.",
    stockpile: { wood: 1 },
    taxDue: 2, taxPaid: 1,
    collectAttempts: 12, collectSuccesses: 9
  },
  benchmarks: {
    entropy: 1.58, adoption: 2, adoptionRate: 0.66,
    adherence: 0.5, rules: 1, structures: 1, level: 1, memory: 14
  },
  activity: [
    "Marco gathered food",
    "Zara contributed wood to Workshop",
    "Civilization reached level 1!"
  ],
  conversation: [
    { kind: "speech", from: "Sage", to: "Zara", message: "Build the Workshop." },
    { kind: "speech", from: "Marco", to: "everyone", message: "Gathering food." }
  ],
  config: {
    WORLD_W: 5200, WORLD_H: 5400,
    flags: {
      SURVIVAL_ENABLED: true, CRAFTING_ENABLED: true, MEMES_ENABLED: true,
      RULES_ENABLED: true, MEMORY_ENABLED: true, BENCHMARKS_ENABLED: true,
      PIANO_MODULES: false, META_SYSTEM: false, ROADS_ENABLED: true
    }
  }
};

let world = MOCK_STATE;
// Per-agent previous position, so we can drive walk-cycle / facing direction
// (drawAgentSprite reads agent.targetX/targetY) without server-supplied targets.
const prevPos = {};

// Live districts/roads (world-expansion plan): served separately from /state
// via GET /districts.js since they're mostly-static and would otherwise bloat
// every ~10Hz /state poll. Polled on a much slower interval (districts only
// change when one is founded, a rare event) -- see pollDistricts() below.
// Falls back to sprites.js's STARTER_DISTRICTS_JS/STARTER_ROAD_NODES/EDGES
// until the first fetch resolves.
let districtsData = { districts: [], roadNodes: {}, roadEdges: [] };
let districtsKey = "";
let districtsEpoch = 0;

// /state delta protocol: after the first full snapshot, poll with ?since=lastFrameTick.
let lastFrameTick = 0;
let stateGeneration = 0;
let statePollFull = true;

/** Merge a partial /state delta into the cached world (omitted key = unchanged). */
function mergeStateDelta(prev, delta) {
  if (!prev || delta.full) return delta;
  if (delta.unchanged) return prev;
  const next = Object.assign({}, prev);
  if (delta.frameTick != null) next.frameTick = delta.frameTick;
  if (delta.stateGeneration != null) next.stateGeneration = delta.stateGeneration;
  if (delta.paused !== undefined) next.paused = delta.paused;
  if (delta.uptimeSeconds !== undefined) next.uptimeSeconds = delta.uptimeSeconds;
  if (delta.calendar) next.calendar = delta.calendar;
  if (delta.lmStatus !== undefined) next.lmStatus = delta.lmStatus;
  if (delta.agents && delta.agents.length) {
    const byId = Object.create(null);
    for (const a of prev.agents || []) byId[a.id] = a;
    for (const a of delta.agents) byId[a.id] = a;
    next.agents = Object.values(byId);
  }
  if (delta.civilization) {
    const civ = Object.assign({}, prev.civilization || {});
    const patch = delta.civilization;
    if (patch.structures && patch.structures.length) {
      const byId = Object.create(null);
      for (const s of civ.structures || []) byId[s.id] = s;
      for (const s of patch.structures) {
        const prior = byId[s.id] || {};
        byId[s.id] = s.sprite !== undefined ? s : Object.assign({}, prior, s);
      }
      civ.structures = Object.values(byId);
    }
    if (patch.structuresRemoved && patch.structuresRemoved.length) {
      const removed = new Set(patch.structuresRemoved);
      civ.structures = (civ.structures || []).filter((s) => !removed.has(s.id));
    }
    for (const key of Object.keys(patch)) {
      if (key === "structures" || key === "structuresRemoved") continue;
      civ[key] = patch[key];
    }
    next.civilization = civ;
  }
  for (const key of ["benchmarks", "activity", "conversation", "config", "god",
    "socialTies", "chronicle", "saga", "districtEcology", "wildlife", "shipments", "weather"]) {
    if (delta[key] !== undefined) next[key] = delta[key];
  }
  return next;
}

function getDistricts() {
  return (districtsData.districts && districtsData.districts.length)
    ? districtsData.districts : STARTER_DISTRICTS_JS;
}

function findDistrictBounds(kind) {
  const d = getDistricts().find((x) => x.kind === kind);
  return d ? d.bounds : null;
}

function getDistrictBounds(id) {
  const d = getDistricts().find((x) => x.id === id);
  return d ? d.bounds : null;
}

