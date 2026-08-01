"use strict";

// Load-perf instrumentation (docs/plan-load-performance.md): set true to log
// terrain-cache build timings and performance marks.
const VIEWER_LOAD_DEBUG = false;
const VIEWER_LOAD_T0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
if (VIEWER_LOAD_DEBUG) {
  performance.mark("viewer:script-start");
  console.info("[viewer-load] script start", {
    devicePixelRatio: window.devicePixelRatio,
    msSinceNavigation: (() => {
      const nav = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
      return nav ? +(performance.now() - nav.startTime).toFixed(1) : null;
    })(),
  });
}

// =====================================================================
// THIN VIEWER — the simulation runs server-side (see server.py / sim_engine.py).
// This file is a PURE RENDERER: it polls GET /state (~10 Hz), keeps the latest
// snapshot in `world`, and draws agents + structures + sidebar from it
// (Contract 2 in .cursor/plans/engine-port-contracts.md). Closing the browser
// does NOT stop the sim. All engine logic (decisions, movement, survival,
// rules, memes, memory, build pipeline) was removed and now lives on the server.
// =====================================================================

const canvas = document.getElementById("world");
const ctx = canvas.getContext("2d");
const canvasWrapEl = document.getElementById("canvasWrap");

// Must match sim_engine.py's WORLD_W/WORLD_H exactly (server is authoritative;
// these are only used for local rendering/cache sizing before the first
// /state reply, and the mock config below).
const WORLD_W = 5200;
const WORLD_H = 5400;

// Static-terrain render cache (perf): the world's tiled terrain never changes,
// so it is rendered once into an offscreen canvas and blitted each frame instead
// of re-tiling ~2M pixels per frame. Invalidated on resize; rebuilt lazily.
let terrainCanvas = null;   // full static terrain (zones, crops, trees, dock…)
let oceanFrames = null;     // { bounds, frames:[16 pre-rendered foam phases] } for the ocean district
let terrainBuildScheduled = false;
let lastSeasonRendered = null; // season the terrain cache was last tinted for; drives cache invalidation on season change
// Living-ecosystem Phase 2 (CROP_GROWTH_ENABLED): the districtEcology stage
// key the terrain cache was last built with -- same invalidation mechanism
// as lastSeasonRendered above, just keyed on stage instead of season.
let lastEcologyStageKeyRendered = null;
let lastTerrainVisualKeyRendered = null;
const worldLoadingEl = document.getElementById("worldLoading");

const STAGE_TINT_FACTOR = { lush: 1.0, healthy: 0.85, sparse: 0.65, barren: 0.45 };
const BARREN_EXTRA_BROWN = "rgba(90,70,50,0.08)";

function terrainVisualCacheKey(season, ecologyStageKey) {
  return `${season || "summer"}|${ecologyStageKey || ""}`;
}

function districtTintAlpha(base, factor) {
  return Math.min(1, Math.max(0, base * factor));
}

function applyDistrictTintRect(ctx, bounds, op, rgba, factor) {
  if (!bounds || factor <= 0) return;
  const m = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
  if (!m) return;
  const alpha = districtTintAlpha(parseFloat(m[4] || "1"), factor);
  if (alpha <= 0) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(bounds.x1, bounds.y1, bounds.x2 - bounds.x1, bounds.y2 - bounds.y1);
  ctx.clip();
  ctx.globalCompositeOperation = op;
  ctx.fillStyle = `rgba(${m[1]},${m[2]},${m[3]},${alpha.toFixed(3)})`;
  ctx.fillRect(bounds.x1, bounds.y1, bounds.x2 - bounds.x1, bounds.y2 - bounds.y1);
  ctx.restore();
}

// Per terrain-kind seasonal grading during cache build.
function applySeasonTintForKind(ctx, season, kind, stage, bounds) {
  if (!season || season === "summer" || kind === "ocean") return;
  const factor = STAGE_TINT_FACTOR[stage] || STAGE_TINT_FACTOR.healthy;
  const k = kind || "village";
  if (season === "spring") {
    if (k === "forest") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(60,200,80,0.12)", factor);
    else if (k === "farm") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(180,210,80,0.10)", factor);
    else if (k === "beach") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(220,190,120,0.06)", factor);
    else if (k === "quarry") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(120,150,100,0.08)", factor);
    else applyDistrictTintRect(ctx, bounds, "overlay", "rgba(60,200,80,0.10)", factor);
  } else if (season === "autumn") {
    if (k === "forest") {
      applyDistrictTintRect(ctx, bounds, "multiply", "rgba(200,140,40,0.22)", factor);
      applyDistrictTintRect(ctx, bounds, "overlay", "rgba(120,80,30,0.08)", factor);
    } else if (k === "farm") applyDistrictTintRect(ctx, bounds, "multiply", "rgba(200,150,40,0.20)", factor);
    else if (k === "beach") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(200,140,80,0.10)", factor);
    else if (k === "quarry") applyDistrictTintRect(ctx, bounds, "overlay", "rgba(160,90,50,0.12)", factor);
    else applyDistrictTintRect(ctx, bounds, "multiply", "rgba(200,140,40,0.16)", factor);
  } else if (season === "winter") {
    if (k === "beach") {
      applyDistrictTintRect(ctx, bounds, "saturation", "rgba(128,128,128,0.25)", factor);
    } else if (k === "forest") {
      applyDistrictTintRect(ctx, bounds, "saturation", "rgba(128,128,128,0.55)", factor);
      applyDistrictTintRect(ctx, bounds, "overlay", "rgba(150,175,235,0.25)", factor);
    } else if (k === "farm") {
      applyDistrictTintRect(ctx, bounds, "saturation", "rgba(128,128,128,0.40)", factor);
      applyDistrictTintRect(ctx, bounds, "overlay", "rgba(110,85,60,0.15)", factor);
    } else if (k === "quarry") {
      applyDistrictTintRect(ctx, bounds, "saturation", "rgba(128,128,128,0.45)", factor);
      applyDistrictTintRect(ctx, bounds, "lighter", "rgba(200,210,230,0.15)", factor);
    } else {
      applyDistrictTintRect(ctx, bounds, "saturation", "rgba(128,128,128,0.50)", factor);
      applyDistrictTintRect(ctx, bounds, "overlay", "rgba(150,175,235,0.22)", factor);
    }
  }
  if (stage === "barren") applyDistrictTintRect(ctx, bounds, "overlay", BARREN_EXTRA_BROWN, 1);
  if (season === "summer" && k === "farm") {
    applyDistrictTintRect(ctx, bounds, "lighter", "rgba(255,230,150,0.08)", factor);
  }
}

function hideWorldLoading() {
  if (!worldLoadingEl || worldLoadingEl.classList.contains("hidden")) return;
  worldLoadingEl.classList.add("hidden");
  if (VIEWER_LOAD_DEBUG) {
    performance.mark("viewer:world-loading-hidden");
    console.info("[viewer-load] hideWorldLoading", {
      msSinceScriptStart: +(performance.now() - VIEWER_LOAD_T0).toFixed(1),
    });
  }
}

function scheduleTerrainCacheBuild() {
  if (terrainCanvas || terrainBuildScheduled) return;
  terrainBuildScheduled = true;
  try {
    buildTerrainCache();
  } finally {
    terrainBuildScheduled = false;
    hideWorldLoading();
  }
}

function setupCanvas() {
  const dpr = Math.max(1, window.devicePixelRatio || 1);
  canvas.width = Math.floor(WORLD_W * dpr);
  canvas.height = Math.floor(WORLD_H * dpr);
  // Backing-store resolution only; on-screen display size is controlled
  // separately by applyZoom() below so the world can be zoomed in/out.
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = false;
  // The cache is rendered in logical (WORLD_W×WORLD_H) pixels and blitted under
  // the DPR transform, so it survives resizes; invalidate anyway to be safe.
  terrainCanvas = null;
  oceanFrames = null;
  applyZoom();
}

// =====================================================================
// Zoom — implemented as a pure CSS display-size multiplier on the canvas;
// the backing store and all drawing stay in logical WORLD_W×WORLD_H space
// (sprites.js and drawTiledWorld are completely untouched by zoom). Lets
// the whole (now much larger) world be seen at once on small screens, or
// zoomed in anywhere, without changing how anything is drawn.
// =====================================================================
let zoomLevel = 1;
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 4;

function applyZoom() {
  canvas.style.width = `${WORLD_W * zoomLevel}px`;
  canvas.style.height = `${WORLD_H * zoomLevel}px`;
}

// Changes zoom while keeping the world point under (anchorClientX, anchorClientY)
// fixed on screen -- so scroll-to-zoom feels anchored at the cursor, and
// button-triggered zoom (no cursor position) anchors on the viewport center.
function setZoom(newZoom, anchorClientX, anchorClientY) {
  const clamped = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, newZoom));
  if (clamped === zoomLevel) return;
  const rect = canvasWrapEl.getBoundingClientRect();
  const ax = anchorClientX === undefined ? rect.left + canvasWrapEl.clientWidth / 2 : anchorClientX;
  const ay = anchorClientY === undefined ? rect.top + canvasWrapEl.clientHeight / 2 : anchorClientY;
  // World point currently under the anchor, using the OLD zoom level.
  const worldX = (canvasWrapEl.scrollLeft + (ax - rect.left)) / zoomLevel;
  const worldY = (canvasWrapEl.scrollTop + (ay - rect.top)) / zoomLevel;
  zoomLevel = clamped;
  applyZoom();
  // Re-scroll so that same world point lands back under the anchor.
  canvasWrapEl.scrollLeft = worldX * zoomLevel - (ax - rect.left);
  canvasWrapEl.scrollTop = worldY * zoomLevel - (ay - rect.top);
}

function zoomFit() {
  const fitZoom = Math.min(canvasWrapEl.clientWidth / WORLD_W, canvasWrapEl.clientHeight / WORLD_H);
  setZoom(fitZoom);
}

setupCanvas();
window.addEventListener("resize", setupCanvas);

const zoomInBtn = document.getElementById("zoomInBtn");
const zoomOutBtn = document.getElementById("zoomOutBtn");
const zoomFitBtn = document.getElementById("zoomFitBtn");
zoomInBtn.addEventListener("click", () => setZoom(zoomLevel * 1.25));
zoomOutBtn.addEventListener("click", () => setZoom(zoomLevel * 0.8));
zoomFitBtn.addEventListener("click", zoomFit);

// Ctrl+scroll (or trackpad pinch, which browsers report as a ctrlKey wheel
// event) zooms anchored at the cursor; a plain wheel keeps scrolling/panning
// exactly as before.
canvasWrapEl.addEventListener("wheel", (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  // 0.0015 keeps a single physical mouse-wheel notch (deltaY ~= +/-100) to a
  // gentle ~16% zoom step, while still accumulating smoothly for trackpad
  // pinch gestures that fire many small-delta events per second.
  const factor = Math.exp(-event.deltaY * 0.0015);
  setZoom(zoomLevel * factor, event.clientX, event.clientY);
}, { passive: false });

// --- Feature flags kept for rendering decisions only (the server is the source
// of truth; these mirror config.flags from /state and gate sidebar rows). They
// are overwritten from world.config.flags on every snapshot. ---
let SURVIVAL_ENABLED = true;
let CRAFTING_ENABLED = true;
let MEMES_ENABLED = true;
let RULES_ENABLED = true;
let MEMORY_ENABLED = true;
let BENCHMARKS_ENABLED = true;
let ECOLOGY_ENABLED = true;
let PIANO_MODULES = false;
let META_SYSTEM = false;
let PATH1_ENABLED = false;
let STRUCTURE_WEAR_ENABLED = true;
let ACTIVITY_CUES_ENABLED = true;
let SOCIAL_LAYER_ENABLED = true;
let CHRONICLE_ENABLED = true;
let FOUNDING_EVENTS_ENABLED = true;
let ENV_EFFECTS_ENABLED = true;
let WORLD_CLOCK_HUD_ENABLED = true;
let SEASONAL_AGENTS_ENABLED = true;
let CROP_GROWTH_ENABLED = true;
let WILDLIFE_ENABLED = true;
let CARAVAN_VISUALS_ENABLED = true;
let WEATHER_ENABLED = true;

// --- Viewer-only display toggles (client-side, not server flags). Flipping
// either to true fully restores that section with no other edits. ---
const SHOW_CONVERSATIONS = false;
const SHOW_SETTLEMENTS = false;

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

// =====================================================================
// Convenience accessors — render functions below read these like the old globals.
// =====================================================================
function getAgents() { return world.agents || []; }
function getLivingAgents() { return getAgents().filter((a) => !a.deceased); }
function getDeceasedAgents() { return getAgents().filter((a) => a.deceased); }
function getCiv() { return world.civilization || {}; }
function resourceRegistry() { return getCiv().resourceRegistry || {}; }

// =====================================================================
// Drawing — terrain cache + agents + structures (reuses sprites.js unchanged).
// =====================================================================

// Living-ecosystem Phase 2 (CROP_GROWTH_ENABLED): districtEcology's per-
// district stage, reduced to just the {districtId: stage} map drawStarterProps
// consumes, and a stable string key for cache-invalidation comparison (same
// role lastSeasonRendered plays for season). Returns (null, null) when the
// flag is off or the server hasn't sent the projection yet, so the terrain
// cache renders exactly as it did before this feature existed.
function ecologyStagesForTerrain() {
  if (!CROP_GROWTH_ENABLED) return [null, null];
  const eco = world.districtEcology;
  if (!eco || !eco.length) return [null, null];
  const stages = {};
  for (const e of eco) stages[e.districtId] = e.stage;
  const key = eco.map((e) => e.districtId + ":" + e.stage).sort().join(",");
  return [stages, key];
}

// Render the static terrain once into offscreen canvases. drawTiledWorld paints
// in logical coordinates, so a WORLD_W×WORLD_H buffer (no DPR transform) is
// blitted under the main ctx's DPR transform and stays crisp (imageSmoothing
// off). The animated ocean strip is pre-rendered as 16 foam phases so animation
// costs one drawImage/frame instead of re-tiling the strip. Rebuilt whenever
// districtsKey changes (a district was founded) via pollDistricts(), the
// season changes, or (CROP_GROWTH_ENABLED) a district's ecology stage changes.
function buildTerrainCache() {
  if (VIEWER_LOAD_DEBUG) performance.mark("viewer:terrain-build-start");
  const buildT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const stageTimings = VIEWER_LOAD_DEBUG ? {} : null;

  const districts = getDistricts();
  terrainCanvas = document.createElement("canvas");
  terrainCanvas.width = WORLD_W;
  terrainCanvas.height = WORLD_H;
  const tctx = terrainCanvas.getContext("2d");
  tctx.imageSmoothingEnabled = false;
  const season = world.calendar && world.calendar.season;
  const [ecologyStages, ecologyStageKey] = ecologyStagesForTerrain();

  const drawT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const terrainVisualOpts = { seasonalV2: true, applyDistrictTint: applySeasonTintForKind };
  drawTiledWorld(tctx, WORLD_W, WORLD_H, 0, null, districts,
                 districtsData.roadNodes, districtsData.roadEdges, season || "summer", ecologyStages, stageTimings, terrainVisualOpts);
  const drawTiledWorldMs = VIEWER_LOAD_DEBUG ? performance.now() - drawT0 : 0;

  const tintT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const seasonTintMs = VIEWER_LOAD_DEBUG ? performance.now() - tintT0 : 0;
  lastEcologyStageKeyRendered = ecologyStageKey;
  lastTerrainVisualKeyRendered = terrainVisualCacheKey(season, ecologyStageKey);

  // The ocean's animated foam strip is scoped to the ocean district's own
  // bounds (not the full world height) -- with the much larger world, a
  // full-height strip would paint a blue stripe down through the open
  // frontier below the starter core.
  const oceanBounds = findDistrictBounds("ocean") || { x1: 0, y1: 120, x2: 200, y2: 880 };
  oceanFrames = { bounds: oceanBounds, frames: [] };
  const ow = oceanBounds.x2 - oceanBounds.x1;
  const oh = oceanBounds.y2 - oceanBounds.y1;
  const oceanT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  for (let phase = 0; phase < 16; phase++) {
    const oc = document.createElement("canvas");
    oc.width = ow;
    oc.height = oh;
    const octx = oc.getContext("2d");
    octx.imageSmoothingEnabled = false;
    fillRectWithTile(octx, 0, 0, ow, oh, oceanTile(phase));
    oceanFrames.frames.push(oc);
  }
  const oceanFramesMs = VIEWER_LOAD_DEBUG ? performance.now() - oceanT0 : 0;
  lastSeasonRendered = season || null;

  if (VIEWER_LOAD_DEBUG) {
    const totalMs = performance.now() - buildT0;
    performance.mark("viewer:terrain-build-end");
    console.info("[viewer-load] buildTerrainCache", {
      totalMs: +totalMs.toFixed(1),
      drawTiledWorldMs: +drawTiledWorldMs.toFixed(1),
      baseFillMs: +(stageTimings.baseFillMs || 0).toFixed(1),
      districtPassesMs: +(stageTimings.districtPassesMs || 0).toFixed(1),
      propsMs: +(stageTimings.propsMs || 0).toFixed(1),
      seasonTintMs: +seasonTintMs.toFixed(1),
      oceanFramesMs: +oceanFramesMs.toFixed(1),
      msSinceScriptStart: +(performance.now() - VIEWER_LOAD_T0).toFixed(1),
      season: season || "summer",
      devicePixelRatio: window.devicePixelRatio,
    });
  }
}

// The dock (drawn at world 150,470, 144x36) straddles the beach/ocean line, so
// part of it falls inside the ocean strip the foam overlay repaints every
// frame. Re-blit those exact pixels from the static cache afterward so the
// jetty (incl. its pilings dipping into the water) isn't erased by the animation.
const DOCK_RECT = { x: 150, y: 470, w: 144, h: 36 };

// Night lighting: a cool translucent overlay whose alpha follows the day
// phase (calendar.dayFraction; night is the last 25% of each day). Wider
// twilight bands and deeper peak alpha than the pre-atmosphere-pack baseline.
const MAX_NIGHT_ALPHA = 0.58;
const TWILIGHT_START = 0.62;
const TWILIGHT_END_DUSK = 0.78;
const TWILIGHT_START_DAWN = 0.92;
const GOLDEN_HOUR_MAX = 0.22;
const LIGHT_GLOW_RADIUS = 200;
const LIGHT_GLOW_HALO_RADIUS = 280;
const NIGHT_DESAT_ALPHA = 0.08;

function nightAlpha(cal) {
  if (!cal) return 0;
  const f = cal.dayFraction;
  if (f == null) return cal.isNight ? MAX_NIGHT_ALPHA : 0;
  if (f < TWILIGHT_START) return 0;
  if (f < TWILIGHT_END_DUSK) {
    const t = (f - TWILIGHT_START) / (TWILIGHT_END_DUSK - TWILIGHT_START);
    return MAX_NIGHT_ALPHA * (t * t);
  }
  if (f < TWILIGHT_START_DAWN) return MAX_NIGHT_ALPHA;
  const t = (1.0 - f) / (1.0 - TWILIGHT_START_DAWN);
  return MAX_NIGHT_ALPHA * (1 - (1 - t) * (1 - t));
}

function goldenHourAlpha(cal) {
  if (!ENV_EFFECTS_ENABLED || !cal || cal.dayFraction == null) return 0;
  const f = cal.dayFraction;
  const band = (start, end) => {
    if (f < start || f >= end) return 0;
    return Math.sin(((f - start) / (end - start)) * Math.PI);
  };
  return GOLDEN_HOUR_MAX * Math.max(
    band(TWILIGHT_START, TWILIGHT_END_DUSK),
    band(TWILIGHT_START_DAWN, 1.00)
  );
}

function drawGoldenHourOverlay(ctx, cal) {
  const gha = goldenHourAlpha(cal);
  if (gha <= 0) return;
  ctx.fillStyle = `rgba(255, 177, 78, ${gha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  const f = cal.dayFraction;
  const edgeBand = (start, end) => {
    if (f < start || f >= end) return 0;
    return Math.sin(((f - start) / (end - start)) * Math.PI);
  };
  const rimAlpha = GOLDEN_HOUR_MAX * 0.45 * Math.max(
    edgeBand(TWILIGHT_START, TWILIGHT_START + 0.04),
    edgeBand(TWILIGHT_END_DUSK - 0.04, TWILIGHT_END_DUSK),
    edgeBand(TWILIGHT_START_DAWN, TWILIGHT_START_DAWN + 0.04),
    edgeBand(0.98, 1.00)
  );
  if (rimAlpha <= 0) return;
  const prevOp = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = "lighter";
  ctx.fillStyle = `rgba(255, 140, 160, ${rimAlpha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  ctx.globalCompositeOperation = prevOp;
}

// Weather sky tint (WEATHER_ENABLED, living-ecosystem Phase 4): darkens the
// same full-canvas overlay stage as the night overlay/golden-hour band as
// weather.state worsens -- clear/gathering/storm/clearing map to a fixed
// per-state alpha (a step ramp across the state machine, not a within-state
// animation; weather.state is all the server sends). The base tint is
// world-wide; storm/clearing districts also get a local veil + extra rain
// clipped to weather.districts (see drawDistrictStormVeil).
const WEATHER_SKY_ALPHA = { clear: 0, gathering: 0.17, storm: 0.41, clearing: 0.20 };
const WEATHER_SKY_COLOR = "12, 28, 32"; // cooler green-slate storm sky, distinct from night's navy
// Clamp on night-overlay-alpha + weather-sky-alpha together so a winter
// storm at deep night in an unlit district can never compound into an
// unreadable screen.
const MAX_NIGHT_PLUS_WEATHER_ALPHA = 0.68;
function weatherSkyAlpha(weather, nightA) {
  if (!WEATHER_ENABLED || !weather) return 0;
  const raw = WEATHER_SKY_ALPHA[weather.state] || 0;
  return Math.max(0, Math.min(raw, MAX_NIGHT_PLUS_WEATHER_ALPHA - nightA));
}

// Rain/snow particles (WEATHER_ENABLED): deterministic from frameTick + a
// per-particle index hash, same no-retained-state discipline as the shipped
// smoke/dust/wildlife cues. Snow instead of rain when the current season
// (calendar.season) is winter. Particle count is capped by the CURRENT
// VISIBLE viewport area (scroll/zoom-derived, same helper drawWildlife/
// drawShipments use), not world area -- the world is much larger than any
// one screen, especially fully zoomed out.
const WEATHER_STATE_INTENSITY = { clear: 0, gathering: 0.18, storm: 1, clearing: 0.45 };
const WEATHER_PARTICLE_DENSITY_DIVISOR = 11000;
const WEATHER_STORM_DENSITY_DIVISOR = 7200;
const WEATHER_PARTICLE_CAP = 380;
const WEATHER_SHEET_EVERY_N = 6;
const WEATHER_SNOW_FALL_PERIOD = 650;
const WEATHER_RAIN_FALL_PERIOD = 130;
const LIGHTNING_BUCKET_FRAMES = 540; // 540 local frames ≈ ~9s at 60fps — rare full-canvas flash windows
function weatherParticleHash(i) {
  let h = (2166136261 ^ i) >>> 0;
  h = Math.imul(h, 16777619);
  h ^= h >>> 13;
  h = Math.imul(h, 2654435761);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967295;
}
function weatherParticleConfig() {
  return {
    intensityMap: WEATHER_STATE_INTENSITY,
    densityDivisor: WEATHER_PARTICLE_DENSITY_DIVISOR,
    stormDivisor: WEATHER_STORM_DENSITY_DIVISOR,
    cap: WEATHER_PARTICLE_CAP,
    sheetEvery: WEATHER_SHEET_EVERY_N,
    snowFallPeriod: WEATHER_SNOW_FALL_PERIOD,
    rainFallPeriod: WEATHER_RAIN_FALL_PERIOD,
  };
}

function drawWeatherParticleBatch(ctx, frameTick, weather, cal, layerOpts) {
  const cfg = weatherParticleConfig();
  const intensity = cfg.intensityMap[weather.state] || 0;
  if (intensity <= 0) return;
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const w = canvasWrapEl.clientWidth / zoomLevel;
  const h = canvasWrapEl.clientHeight / zoomLevel;
  if (w <= 0 || h <= 0) return;
  const isSnow = !!(cal && cal.season === "winter");
  const densityDiv = intensity >= 1 ? cfg.stormDivisor : cfg.densityDivisor;
  const countMult = layerOpts && layerOpts.countMult != null ? layerOpts.countMult : 1;
  const alphaMult = layerOpts && layerOpts.alphaMult != null ? layerOpts.alphaMult : 1;
  const fallMult = layerOpts && layerOpts.fallMult != null ? layerOpts.fallMult : 1;
  const count = Math.min(cfg.cap, Math.floor((w * h / densityDiv) * intensity * countMult));
  const fallPeriod = (isSnow ? cfg.snowFallPeriod : cfg.rainFallPeriod) * fallMult;
  for (let i = 0; i < count; i++) {
    const hx = weatherParticleHash(i * 2 + 1);
    const hy = weatherParticleHash(i * 2 + 2);
    const x = left + hx * w;
    const yFrac = ((frameTick + hy * fallPeriod) % fallPeriod) / fallPeriod;
    const y = top + yFrac * h;
    const sheet = !isSnow && (i % cfg.sheetEvery === 0);
    drawWeatherParticle(ctx, isSnow ? "snow" : "rain", x, y, i, {
      sheet,
      alphaMult,
      frameTick,
      windHash: weatherParticleHash(i * 3 + 7),
      lenHash: weatherParticleHash(i * 5 + 11),
    });
  }
}

function drawWeatherParticles(ctx, frameTick, weather, cal) {
  if (!WEATHER_ENABLED || !weather) return;
  drawWeatherParticleBatch(ctx, frameTick, weather, cal, { countMult: 0.6, alphaMult: 0.5, fallMult: 0.85 });
  drawWeatherParticleBatch(ctx, frameTick, weather, cal, { countMult: 0.4, alphaMult: 1.0, fallMult: 1.0 });
}

function drawDistrictStormVeil(ctx, weather) {
  if (!WEATHER_ENABLED || !weather) return;
  const state = weather.state;
  if (state !== "storm" && state !== "clearing") return;
  const ids = weather.districts;
  if (!ids || !ids.length) return;
  const alpha = state === "storm" ? 0.24 : 0.14;
  for (const id of ids) {
    const b = getDistrictBounds(id);
    if (!b) continue;
    ctx.fillStyle = `rgba(6, 16, 22, ${alpha.toFixed(3)})`;
    ctx.fillRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
  }
}

// Deterministic rare lightning during storm: full-canvas white/blue flash,
// keyed off local renderFrame (rAF), not sim frameTick — no server signal needed.
function drawLightningFlash(ctx, renderFrame, weather) {
  if (!WEATHER_ENABLED || !weather || weather.state !== "storm") return;
  const bucket = Math.floor(renderFrame / LIGHTNING_BUCKET_FRAMES);
  const trigger = weatherParticleHash(bucket + 0x5f3759df);
  if (trigger < 0.88) return; // ~12% of buckets (~every ~75s at 60fps) may flash
  const localFrame = renderFrame % LIGHTNING_BUCKET_FRAMES;
  const startFrame = Math.floor(weatherParticleHash(bucket + 1) * (LIGHTNING_BUCKET_FRAMES - 20));
  const duration = 8 + Math.floor(weatherParticleHash(bucket + 2) * 11);
  if (localFrame < startFrame || localFrame >= startFrame + duration) return;
  const alpha = 0.10 + weatherParticleHash(bucket + 3) * 0.10;
  const blueish = weatherParticleHash(bucket + 4) > 0.45;
  ctx.fillStyle = blueish
    ? `rgba(190, 215, 255, ${alpha.toFixed(3)})`
    : `rgba(255, 255, 255, ${alpha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
}

// Light glow (ENV_EFFECTS_ENABLED): warm radial glow over lit structures while
// the night overlay is active, pushing back the dark in lit districts.
function drawLightGlows(cal) {
  if (!ENV_EFFECTS_ENABLED) return;
  const na = nightAlpha(cal);
  if (na <= 0) return;
  const civ = getCiv();
  const litDistricts = civ.litDistricts;
  if (!litDistricts || !litDistricts.length) return;
  const structures = civ.structures || [];
  const strength = na / MAX_NIGHT_ALPHA;
  const prevOp = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = "lighter";
  const drawGlow = (cx, cy, radius, centerAlpha, midAlpha) => {
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0, `rgba(255, 200, 100, ${centerAlpha.toFixed(3)})`);
    grad.addColorStop(0.45, `rgba(255, 190, 90, ${midAlpha.toFixed(3)})`);
    grad.addColorStop(1, "rgba(255, 190, 90, 0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
  };
  for (const s of structures) {
    if (!s || s.light !== true) continue;
    if (!litDistricts.includes(s.districtId)) continue;
    const size = getStructureRenderSize(s);
    const cx = s.x + size.width / 2;
    const cy = s.y + size.height / 2;
    drawGlow(cx, cy, LIGHT_GLOW_RADIUS, 0.55 * strength, 0.22 * strength);
    if (na >= 0.4 * MAX_NIGHT_ALPHA) {
      drawGlow(cx, cy, LIGHT_GLOW_HALO_RADIUS, 0.12 * strength, 0.04 * strength);
    }
  }
  ctx.globalCompositeOperation = prevOp;
}

function drawWorld(ctx, frameTick) {
  if (!terrainCanvas) {
    scheduleTerrainCacheBuild();
    ctx.fillStyle = "#3d6b35";
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
    return;
  }
  ctx.drawImage(terrainCanvas, 0, 0, WORLD_W, WORLD_H);
  // Animated ocean foam overlay (the only moving terrain): blit the current phase.
  const foamOffset = Math.floor(frameTick / 8) % 16;
  const ob = oceanFrames.bounds;
  ctx.drawImage(oceanFrames.frames[foamOffset], ob.x1, ob.y1);
  ctx.drawImage(terrainCanvas, DOCK_RECT.x, DOCK_RECT.y, DOCK_RECT.w, DOCK_RECT.h,
                DOCK_RECT.x, DOCK_RECT.y, DOCK_RECT.w, DOCK_RECT.h);
}

// Poll GET /districts.js on a slow interval -- districts/roads are
// mostly-static and only change when _maybe_found_district() fires
// server-side (a rare, deterministic event), so this doesn't need /state's
// ~10Hz cadence. Rebuilds the terrain cache only when the served list
// actually changed (a district was founded), not on every poll.
const DISTRICTS_POLL_MS = 3000;
let districtsJsResolvedLogged = false;
async function pollDistricts() {
  try {
    const res = await fetch("/districts.js", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (!districtsJsResolvedLogged) {
      districtsJsResolvedLogged = true;
      if (VIEWER_LOAD_DEBUG) {
        performance.mark("viewer:districts-js-resolved");
        console.info("[viewer-load] /districts.js resolved", {
          msSinceScriptStart: +(performance.now() - VIEWER_LOAD_T0).toFixed(1),
        });
      }
    }
    const key = JSON.stringify((data.districts || []).map((d) => d.id));
    if (key !== districtsKey) {
      districtsKey = key;
      districtsData = data;
      terrainCanvas = null; // force rebuild with the new district list
      terrainBuildScheduled = false;
      scheduleTerrainCacheBuild();
    } else {
      districtsData = data; // road graph could still have grown even if district ids didn't
    }
  } catch (err) { /* keep last known districts; /state polling surfaces connectivity issues */ }
}

function drawResourceDots(ctx, agent, cx, cy) {
  const reg = resourceRegistry();
  const res = agent.resources || {};
  const totalDots = Object.keys(reg).reduce(
    (sum, key) => sum + Math.min(res[key] || 0, 5), 0
  );
  let dotX = cx - Math.max(0, (totalDots * 5 - 2) / 2);
  for (const [key, def] of Object.entries(reg)) {
    const count = Math.min(res[key] || 0, 5);
    for (let i = 0; i < count; i++) {
      ctx.fillStyle = (def && def.color) || "#BDBDBD";
      ctx.fillRect(dotX, cy, 3, 3);
      dotX += 5;
    }
  }
}

function drawSpeechBubble(ctx, agent) {
  if (!agent.message) return;
  const x = agent.x;
  const y = agent.y;
  const text = agent.message.length > 36
    ? agent.message.slice(0, 35) + "…"
    : agent.message;
  ctx.font = "10px monospace";
  const w = Math.min(220, Math.max(48, ctx.measureText(text).width + 14));
  const bx = Math.max(4, Math.min(WORLD_W - w - 4, Math.round(x - w / 2)));
  const by = Math.max(4, y - 78);
  ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
  ctx.fillRect(bx, by, w, 22);
  ctx.strokeStyle = "#111";
  ctx.lineWidth = 1;
  ctx.strokeRect(bx, by, w, 22);
  ctx.fillRect(Math.max(bx + 8, Math.min(bx + w - 14, x - 3)), by + 22, 6, 4);
  ctx.fillStyle = "#222";
  ctx.textAlign = "center";
  ctx.fillText(text, x, by + 15);
}

function drawHealthBar(ctx, agent) {
  if (!SURVIVAL_ENABLED) return;
  if ((agent.health >= 100 || agent.health == null) && !agent.incapacitated) return;
  const w = 24, h = 3;
  const bx = Math.round(agent.x - w / 2);
  const by = Math.round(agent.y - 30);
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(bx - 1, by - 1, w + 2, h + 2);
  const frac = Math.max(0, Math.min(1, (agent.health || 0) / 100));
  ctx.fillStyle = frac > 0.5 ? "#4CAF50" : frac > 0.2 ? "#FFC107" : "#F44336";
  ctx.fillRect(bx, by, Math.round(w * frac), h);
}

function drawAgentLabel(ctx, agent, prefix) {
  const label = `${prefix}·${agent.name}`;
  ctx.font = "10px monospace";
  ctx.textAlign = "center";
  const labelW = Math.ceil(ctx.measureText(label).width + 10);
  const labelX = Math.round(agent.x - labelW / 2);
  const labelY = Math.round(agent.y + 24);
  ctx.fillStyle = "rgba(0, 0, 0, 0.58)";
  ctx.fillRect(labelX, labelY, labelW, 14);
  ctx.fillStyle = agent.color;
  ctx.fillRect(labelX, labelY, 5, 14);
  ctx.fillStyle = "#fff";
  ctx.fillText(label, agent.x + 2, labelY + 10);
}

function drawAgent(ctx, agent, frameTick) {
  if (agent.deceased && agent.buried) {
    // Laid to rest in the cemetery: tombstone sprite + name marker only.
    drawAgentSprite(ctx, agent, frameTick);
    drawAgentLabel(ctx, agent, "†");
    return;
  }
  drawAgentSprite(ctx, agent, frameTick);
  if (agent.deceased && !agent.buried) {
    // Permanent death awaiting burial: body stays where they fell.
    ctx.fillStyle = "rgba(40, 40, 50, 0.65)";
    ctx.fillRect(agent.x - 14, agent.y - 28, 28, 34);
    ctx.fillStyle = "#ccc";
    ctx.font = "11px monospace";
    ctx.textAlign = "center";
    ctx.fillText("†", agent.x, agent.y - 12);
    drawAgentLabel(ctx, agent, "†");
    return;
  }
  if (agent.incapacitated) {
    // Grey out collapsed agents and tag them.
    ctx.fillStyle = "rgba(60, 60, 70, 0.55)";
    ctx.fillRect(agent.x - 12, agent.y - 26, 24, 30);
    ctx.fillStyle = "#fff";
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.fillText("☠", agent.x, agent.y - 14);
  }
  drawHealthBar(ctx, agent);
  drawAgentLabel(ctx, agent, agent.role.charAt(0).toUpperCase());
  if (isAgentInventoryVisible(agent)) {
    drawResourceDots(ctx, agent, agent.x, agent.y + 42);
  }
  if (agent.isThinking) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.7)";
    ctx.fillRect(agent.x + 12, agent.y - 46, 18, 12);
    ctx.fillStyle = "#fff";
    ctx.font = "bold 10px monospace";
    ctx.fillText("...", agent.x + 21, agent.y - 37);
  }
  drawSpeechBubble(ctx, agent);
}

function drawStructureWithShadow(structure) {
  const size = getStructureRenderSize(structure, STRUCTURE_WEAR_ENABLED);
  ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
  ctx.beginPath();
  ctx.ellipse(structure.x + size.width / 2, structure.y + size.height, size.width * 0.55, 7, 0, 0, Math.PI * 2);
  ctx.fill();
  drawStructure(ctx, structure, STRUCTURE_WEAR_ENABLED);
  if (STRUCTURE_WEAR_ENABLED && isStructureHitFlashing(structure.id)) {
    drawStructureHitFlash(ctx, structure, size);
  }
  drawStructureLabel(ctx, structure, size);
}

function drawStructureHitFlash(ctx, structure, size) {
  const pulse = 0.55 + 0.45 * Math.sin(renderFrame * 0.35);
  ctx.save();
  ctx.strokeStyle = `rgba(160, 240, 255, ${(0.75 * pulse).toFixed(3)})`;
  ctx.lineWidth = 2;
  ctx.strokeRect(structure.x - 1, structure.y - 1, size.width + 2, size.height + 2);
  ctx.fillStyle = `rgba(255, 255, 255, ${(0.22 * pulse).toFixed(3)})`;
  ctx.fillRect(structure.x, structure.y, size.width, size.height);
  ctx.restore();
}

function drawStructureLabel(ctx, structure, size) {
  const base = structure.name || structure.type;
  const lvl = structure.level != null ? structure.level : null;
  const label = lvl != null && lvl > 0 ? `${base} Lv.${lvl}` : base;
  ctx.font = "9px monospace";
  ctx.textAlign = "center";
  const labelW = Math.ceil(ctx.measureText(label).width + 8);
  const cx = structure.x + size.width / 2;
  const labelX = Math.round(cx - labelW / 2);
  const labelY = structure.y + size.height + 2;
  ctx.fillStyle = "rgba(0, 0, 0, 0.62)";
  ctx.fillRect(labelX, labelY, labelW, 12);
  ctx.fillStyle = "#fff7d6";
  ctx.fillText(label, cx, labelY + 9);
}

// =====================================================================
// Sidebar render — fills the Civilization / Agents panels and the
// Activity & Chat left panel from the snapshot.
// =====================================================================
const statusDot = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const agentListEl = document.getElementById("agentList");
const agentRollupEl = document.getElementById("agentRollup");
const agentDetailEl = document.getElementById("agentDetail");
const livingAgentCountEl = document.getElementById("livingAgentCount");
const deadAgentsBtn = document.getElementById("deadAgentsBtn");
const deadAgentsModal = document.getElementById("deadAgentsModal");
const deadAgentsListEl = document.getElementById("deadAgentsList");
const deadAgentsCloseBtn = document.getElementById("deadAgentsCloseBtn");
let selectedAgentId = null;
let hoveredAgentId = null;
let followAgentId = null;
const AGENT_HIT_RADIUS = 28;

// C5 follow-cam: center the scrollable canvas viewport on an agent, reusing the
// existing centerViewportOn() helper (defined near the minimap handlers) so the
// scroll/zoom math lives in exactly one place.
function centerCameraOnAgent(agent) {
  centerViewportOn(agent.x, agent.y);
}

function clientToWorld(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((clientX - rect.left) / rect.width) * WORLD_W,
    y: ((clientY - rect.top) / rect.height) * WORLD_H,
  };
}

function agentAtWorldPoint(wx, wy) {
  let best = null;
  let bestDist = AGENT_HIT_RADIUS;
  for (const a of getLivingAgents()) {
    const dx = a.x - wx;
    const dy = a.y - wy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < bestDist) {
      bestDist = dist;
      best = a;
    }
  }
  return best;
}

function isAgentInventoryVisible(agent) {
  const id = agent.id;
  return (hoveredAgentId != null && hoveredAgentId === id)
    || (selectedAgentId != null && selectedAgentId === id);
}

function syncAgentListSelection() {
  agentListEl.querySelectorAll("li.agent-row").forEach((li) => {
    li.classList.toggle("agent-selected", Number(li.dataset.agentId) === selectedAgentId);
  });
}
const convListEl = document.getElementById("convList");
const actListEl = document.getElementById("actList");
const chronicleLogEl = document.getElementById("chronicleLog");
const chronicleListEl = document.getElementById("chronicleList");
const timeNowEl = document.getElementById("timeNow");
const timeUptimeEl = document.getElementById("timeUptime");
const timeCalendarEl = document.getElementById("timeCalendar");
const worldClockHudEl = document.getElementById("worldClockHud");
const civLevelEl = document.getElementById("civLevel");
const civStructuresEl = document.getElementById("civStructures");
const civUpgradesRowEl = document.getElementById("civUpgradesRow");
const civUpgradesEl = document.getElementById("civUpgrades");
const civProjectCountEl = document.getElementById("civProjectCount");
const civProjectsListEl = document.getElementById("civProjectsList");
const civResourcesEl = document.getElementById("civResources");
const civResourceListEl = document.getElementById("civResourceList");
const civCustomBuildsEl = document.getElementById("civCustomBuilds");
const civCustomListEl = document.getElementById("civCustomList");
const civRecipeRow = document.getElementById("civRecipeRow");
const civRecipeCountEl = document.getElementById("civRecipeCount");
const civRecipeListEl = document.getElementById("civRecipeList");
const civCollectRateEl = document.getElementById("civCollectRate");
const civRulesRow = document.getElementById("civRulesRow");
const civRuleCountEl = document.getElementById("civRuleCount");
const civRuleListEl = document.getElementById("civRuleList");
const civBenchRow = document.getElementById("civBenchRow");
const civBenchEl = document.getElementById("civBench");
const civPendingRow = document.getElementById("civPendingRow");
const civPendingEl = document.getElementById("civPending");
const civProjectSprite = document.getElementById("civProjectSprite");
const civProjectSpriteCtx = civProjectSprite.getContext("2d");
const civLevelLabelEl = document.getElementById("civLevelLabel");
const councilBannerEl = document.getElementById("councilBanner");
const foundingBannerEl = document.getElementById("foundingBanner");
const disasterBannerEl = document.getElementById("disasterBanner");
const councilSectionEl = document.getElementById("councilSection");
const councilMetaEl = document.getElementById("councilMeta");
const councilCardsEl = document.getElementById("councilCards");
const councilHistoryEl = document.getElementById("councilHistory");

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Base project set used to distinguish "custom builds" (registry entries beyond
// the seed set) for the sidebar count, matching the old client behavior.
const BASE_PROJECT_IDS = new Set(["house", "farm_plot", "workshop", "wall", "granary"]);

function customProjectIds() {
  const reg = getCiv().projectRegistry || {};
  return Object.keys(reg).filter((id) => !BASE_PROJECT_IDS.has(id));
}

function totalVillageResources() {
  const breakdown = villageResourceBreakdown();
  return Object.values(breakdown).reduce((sum, n) => sum + n, 0);
}

function villageResourceBreakdown() {
  const reg = resourceRegistry();
  const stockpile = getCiv().stockpile || {};
  const out = {};
  for (const key of Object.keys(reg)) {
    let n = (stockpile[key] || 0);
    for (const agent of getAgents()) n += (agent.resources || {})[key] || 0;
    if (n > 0) out[key] = n;
  }
  return out;
}

function renderResourceChips(breakdown) {
  const reg = resourceRegistry();
  const keys = Object.keys(breakdown);
  if (keys.length === 0) return '<span class="civ-label">none</span>';
  return keys.map((key) => {
    const color = (reg[key] || {}).color || "#BDBDBD";
    return `<span class="res-chip"><span class="swatch" style="background:${color}"></span>${escapeHtml(key)} ${breakdown[key]}</span>`;
  }).join("");
}

const ACTION_LABELS = {
  collect_resource: "gathering",
  contribute_resources: "contributing",
  build_structure: "building",
  start_project: "starting a project",
  repair_structure: "repairing",
  upgrade_structure: "upgrading",
  submit_structure_sprite: "designing sprite",
  talk_to_nearby: "talking",
  found_belief: "founding a belief",
  trade_resource: "trading",
  propose_blueprint: "proposing a blueprint",
  approve_blueprint: "reviewing a blueprint",
  reject_blueprint: "reviewing a blueprint",
  assign_task: "assigning tasks",
  change_role: "changing role",
  switch_role: "retraining",
  propose_role: "proposing a role",
  approve_role: "reviewing a role",
  reject_role: "reviewing a role",
  propose_rule: "proposing a rule",
  vote_rule: "voting",
  repeal_rule: "proposing a repeal",
  move_to_agent: "approaching someone",
  heal_agent: "healing",
  craft_item: "crafting",
  propose_recipe: "proposing a recipe",
  approve_recipe: "reviewing a recipe",
  reject_recipe: "reviewing a recipe",
  bury_agent: "burying the dead",
  place_block: "placing a block",
  remove_block: "removing a block",
  dig_terrain: "digging terrain",
  plant_terrain: "planting terrain",
  propose_treaty: "proposing a treaty",
  vote_treaty: "voting on a treaty",
  deliver_caravan: "running a caravan",
  council_speak: "speaking at council",
  council_propose: "proposing at council",
  council_vote: "voting at council",
  hunt_wildlife: "hunting",
  confront_agent: "confronting",
  rest: "resting"
};
function humanizeAction(agent) {
  if (agent.deceased) return agent.buried ? "dead · buried" : "dead · unburied";
  if (agent.incapacitated) return "collapsed";
  if (agent.isThinking) return "thinking…";
  const a = agent.lastAction;
  if (!a) return "idle";
  return actionLabel(a);
}

// Shared with humanizeAction's action-name mapping (excludes the
// deceased/incapacitated/thinking special-cases, which don't apply to
// historical log entries).
function actionLabel(actionName) {
  if (!actionName) return "idle";
  if (actionName.startsWith("move_to_")) return "heading to the " + actionName.replace("move_to_", "");
  return ACTION_LABELS[actionName] || actionName.replace(/_/g, " ");
}

function renderBenchmarks() {
  const b = world.benchmarks;
  if (!b || Object.keys(b).length === 0) {
    return '<span class="civ-label">sampling…</span>';
  }
  const r2 = (n) => (n == null ? "0" : Math.round(n * 100) / 100);
  const MEME_SHORT_NAMES = {
    harvest_spirit: "Harvest",
    river_spirit: "River",
  };
  const renderGroup = (label, chips) => {
    if (!chips.length) return "";
    return `<div class="bench-group"><span class="bench-group-label">${label}</span>${chips.join("")}</div>`;
  };
  const culture = [];
  const governance = [];
  const systems = [];
  culture.push(`<span class="res-chip">entropy <span class="civ-value">${r2(b.entropy)}</span></span>`);
  if (MEMES_ENABLED) {
    const byMeme = b.adoptionByMeme || {};
    const memeIds = Object.keys(byMeme);
    if (memeIds.length) {
      memeIds.forEach((mid) => {
        const short = MEME_SHORT_NAMES[mid] || mid;
        culture.push(`<span class="res-chip">${escapeHtml(short)} <span class="civ-value">${byMeme[mid]}/${getLivingAgents().length}</span></span>`);
      });
    } else {
      culture.push(`<span class="res-chip">meme adopt <span class="civ-value">${b.adoption}/${getLivingAgents().length}</span></span>`);
    }
  }
  if (b.adherence !== null && b.adherence !== undefined) {
    governance.push(`<span class="res-chip">tax <span class="civ-value">${Math.round(b.adherence * 100)}%</span></span>`);
  }
  if (RULES_ENABLED) {
    governance.push(`<span class="res-chip">rules <span class="civ-value">${b.rules}</span></span>`);
    if (b.ruleKindDiversity != null) {
      governance.push(`<span class="res-chip">rule kinds <span class="civ-value">${b.ruleKindDiversity}</span></span>`);
    }
  }
  if (MEMORY_ENABLED) {
    systems.push(`<span class="res-chip">memory <span class="civ-value">${b.memory}</span></span>`);
  }
  if (b.effectThroughput != null && b.effectThroughput !== undefined) {
    systems.push(`<span class="res-chip">effects <span class="civ-value">${b.effectThroughput}</span></span>`);
  }
  if (b.ecologyScarcity != null && b.ecologyScarcity !== undefined) {
    systems.push(`<span class="res-chip">ecology <span class="civ-value">${Math.round(b.ecologyScarcity * 100)}%</span></span>`);
  }
  if (PIANO_MODULES || META_SYSTEM) {
    systems.push(`<span class="res-chip">modules <span class="civ-value">${b.moduleTotal}</span></span>`);
  }
  const html = renderGroup("Culture", culture)
    + renderGroup("Governance", governance)
    + renderGroup("Systems", systems);
  return html || '<span class="civ-label">sampling…</span>';
}

// Draw one active project's structure sprite into the civ-panel mini-canvas
// (the first active district build, if any), reusing the same grid/fallback
// helpers as the in-world drawStructure().
function renderProjectSprite(p) {
  const c = civProjectSpriteCtx;
  c.clearRect(0, 0, civProjectSprite.width, civProjectSprite.height);
  if (!p) {
    civProjectSprite.style.display = "none";
    return;
  }
  civProjectSprite.style.display = "block";
  const fake = { type: p.type, visualStyle: p.visualStyle, name: p.name };
  const grid = getStructureGrid(fake);
  if (grid) {
    const gw = grid.reduce((m, row) => Math.max(m, row.length), 0);
    const gh = grid.length;
    const scale = Math.max(1, Math.floor(Math.min(civProjectSprite.width / gw, civProjectSprite.height / gh)));
    const ox = Math.round((civProjectSprite.width - gw * scale) / 2);
    const oy = Math.round((civProjectSprite.height - gh * scale) / 2);
    drawPixelGrid(c, ox, oy, grid, scale, false);
  } else {
    drawGenericStructure(c, 8, 8, p.name || p.type, colorFromId(p.type));
  }
}

// Renders the "Active builds" list -- one row per district with a non-null
// project (the one required sidebar behavioral change of the world-expansion
// plan: districtProjects, plural, replaces the old singular activeProject).
function renderProjectsList() {
  const projects = getCiv().districtProjects || {};
  const entries = Object.entries(projects).filter(([, p]) => p);
  civProjectCountEl.textContent = entries.length;
  if (entries.length === 0) {
    civProjectsListEl.innerHTML = '<div class="civ-row"><span class="civ-label">none</span></div>';
  } else {
    civProjectsListEl.innerHTML = entries.map(([districtId, p]) => {
      const pct = p.progressPercent != null ? p.progressPercent : 0;
      return `<div class="civ-row civ-project-list-item">` +
        `<span class="civ-value">${escapeHtml(p.name)}</span> ` +
        `<span class="civ-label">in ${escapeHtml(districtId)} — ${escapeHtml(p.progressText || "")} (${pct}%)</span>` +
        `<div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>` +
        `</div>`;
    }).join("");
  }
  renderProjectSprite(entries.length ? entries[0][1] : null);
}

// Cheap change-detection so we don't rebuild innerHTML every frame.
let lastSidebarKey = "";
let lastAgentPanelKey = "";
let lastAgentDetailKey = "";
let lastLogKey = "";
let lastChronicleKey = "";
let foundingFramesSeen = null; // null until first snapshot processed (see founding-banner logic)
let foundingBannerTimer = null;
let disasterFramesSeen = null;
let disasterBannerTimer = null;
const prevStructureCondition = {}; // structure id -> { condition, isRuin }
const structureFlashUntil = {}; // structure id -> wall-clock ms deadline
const STRUCTURE_HIT_FLASH_MIN_DROP = 5; // ignore passive decay (~0.05/tick); catch disasters (40–70)

// Client-only diff of structure condition/isRuin between polls; flash on meaningful
// damage or a new ruin transition — not every passive decay tick.
function trackStructureConditionDeltas() {
  if (!STRUCTURE_WEAR_ENABLED) return;
  const structures = getCiv().structures || [];
  const liveIds = new Set();
  for (const s of structures) {
    if (s.id == null) continue;
    liveIds.add(s.id);
    const cond = s.condition != null ? Number(s.condition) : 100;
    const ruin = !!s.isRuin;
    const prev = prevStructureCondition[s.id];
    if (prev && ((prev.condition - cond) >= STRUCTURE_HIT_FLASH_MIN_DROP || (ruin && !prev.isRuin))) {
      structureFlashUntil[s.id] = Date.now() + 800;
    }
    prevStructureCondition[s.id] = { condition: cond, isRuin: ruin };
  }
  for (const id of Object.keys(prevStructureCondition)) {
    if (!liveIds.has(Number(id))) {
      delete prevStructureCondition[id];
      delete structureFlashUntil[id];
    }
  }
}

function isStructureHitFlashing(id) {
  if (id == null) return false;
  const until = structureFlashUntil[id];
  if (!until) return false;
  if (Date.now() > until) {
    delete structureFlashUntil[id];
    return false;
  }
  return true;
}

// --- Client-only per-agent activity history (Plan B1: no server endpoint,
// resets on reload). Newest entry is last in the array; capped per agent. ---
const AGENT_HISTORY_LIMIT = 8;
const agentHistory = new Map(); // agentId -> [{ action, label }]

function recordAgentHistory() {
  const living = getLivingAgents();
  const seenIds = new Set();
  for (const a of living) {
    seenIds.add(a.id);
    const action = a.lastAction || null;
    let buf = agentHistory.get(a.id);
    if (!buf) {
      buf = [];
      agentHistory.set(a.id, buf);
    }
    const last = buf.length ? buf[buf.length - 1] : null;
    if (!last || last.action !== action) {
      buf.push({ action, label: humanizeAction(a) });
      if (buf.length > AGENT_HISTORY_LIMIT) buf.shift();
    }
  }
  // Prune buffers for agents that no longer exist in the current world.
  for (const id of Array.from(agentHistory.keys())) {
    if (!seenIds.has(id)) agentHistory.delete(id);
  }
}

function agentAgeLabel(a) {
  if (a.age == null || Number.isNaN(Number(a.age))) return "";
  return ` · age ${Math.round(Number(a.age))}`;
}

function agentVitalsHtml(a) {
  if (!SURVIVAL_ENABLED) return "";
  if (a.deceased) {
    return ` <span class="agent-vitals agent-dead">${a.buried ? "† buried" : "† unburied"}</span>`;
  }
  if (a.incapacitated) {
    return ` <span class="agent-vitals">☠ collapsed</span>`;
  }
  const health = Math.round(a.health);
  const hunger = Math.round(a.hunger);
  const healthColor = health >= 60 ? "#5cb85c" : health >= 30 ? "#e0a63f" : "#d9534f";
  // hunger is 0-100 where HIGHER = HUNGRIER = worse, so the bar fills (and
  // reddens) as hunger rises — it reads as a "danger" meter, not a "fullness" one.
  const hungerColor = hunger <= 40 ? "#5cb85c" : hunger <= 70 ? "#e0a63f" : "#d9534f";
  return ` <span class="agent-vitals">` +
    `<span class="vital-row" title="Health ${health}">❤<span class="vital-bar"><span class="vital-fill" style="width:${health}%;background:${healthColor}"></span></span></span>` +
    `<span class="vital-row" title="Hunger ${hunger} (higher = hungrier)">🍗<span class="vital-bar"><span class="vital-fill" style="width:${hunger}%;background:${hungerColor}"></span></span></span>` +
    `</span>`;
}

// Truncated last-thought line for the main list row (replaces the old
// per-agent beliefs line — see docs/plan-agents-panel-3-*.md step 4b).
// Intentionally no "feeling" line: the decision `feeling` field is ~0%
// populated in practice, so showing it would be an empty column.
const AGENT_THOUGHT_TRUNCATE = 60;
function agentThoughtHtml(a) {
  if (a.isThinking) return `<span class="agent-thought">thinking…</span>`;
  const reasoning = a.lastReasoning;
  if (!reasoning) return `<span class="agent-thought muted">—</span>`;
  const truncated = reasoning.length > AGENT_THOUGHT_TRUNCATE
    ? reasoning.slice(0, AGENT_THOUGHT_TRUNCATE) + "…"
    : reasoning;
  return `<span class="agent-thought">${escapeHtml(truncated)}</span>`;
}

// Lower score = more urgent. Incapacitated agents always float to the top;
// otherwise rank by low health / high hunger.
function agentSeverityScore(a) {
  if (a.incapacitated) return -1000;
  const health = Number(a.health);
  const hunger = Number(a.hunger);
  const healthPenalty = Number.isFinite(health) ? (100 - health) : 0;
  const hungerPenalty = Number.isFinite(hunger) ? hunger : 0;
  return -(healthPenalty + hungerPenalty);
}

function isAgentCritical(a) {
  if (!SURVIVAL_ENABLED) return false;
  if (a.incapacitated) return true;
  const health = Number(a.health);
  const hunger = Number(a.hunger);
  return (Number.isFinite(health) && health < 30) || (Number.isFinite(hunger) && hunger > 85);
}

function renderDeceasedAgentRow(a) {
  const burial = a.buried ? "buried in the Cemetery" : "awaiting burial";
  return `<li><span class="dot" style="background:${a.color}"></span>` +
    `<span><span class="agent-main">${escapeHtml(a.name)} — ${escapeHtml(a.role)}${escapeHtml(agentAgeLabel(a))}</span>` +
    `<span class="agent-dead-detail">dead · ${burial}</span></span></li>`;
}

// Renders the inventory + activity-history detail panel for the currently
// selected agent (or clears it when nothing is selected). Kept separate from
// renderAgentPanel's own change-detection so selecting an agent (or new
// history entries) always refreshes it, even when the roster itself hasn't
// changed and renderAgentPanel's `lastAgentPanelKey` early-return fires.
function renderAgentDetail() {
  if (selectedAgentId == null) {
    if (lastAgentDetailKey !== "") {
      agentDetailEl.classList.remove("open");
      agentDetailEl.innerHTML = "";
      lastAgentDetailKey = "";
    }
    return;
  }
  const agent = getLivingAgents().find((a) => a.id === selectedAgentId);
  if (!agent) {
    if (lastAgentDetailKey !== "") {
      agentDetailEl.classList.remove("open");
      agentDetailEl.innerHTML = "";
      lastAgentDetailKey = "";
    }
    return;
  }
  const history = agentHistory.get(agent.id) || [];
  const detailKey = JSON.stringify({
    id: agent.id, resources: agent.resources || {},
    history: history.map((h) => h.label),
    relationships: agent.relationships || {},
    lastReasoning: agent.lastReasoning, isThinking: agent.isThinking,
    following: followAgentId === agent.id,
  });
  if (detailKey === lastAgentDetailKey) return;
  lastAgentDetailKey = detailKey;

  const resources = agent.resources || {};
  const breakdown = {};
  for (const key of Object.keys(resources)) {
    if (resources[key] > 0) breakdown[key] = resources[key];
  }

  const historyHtml = history.length
    ? history.slice().reverse().map((h) => `<li>${escapeHtml(h.label)}</li>`).join("")
    : `<li class="civ-label">No recent activity yet</li>`;

  const relationships = agent.relationships || {};
  const tieNames = Object.keys(relationships);
  const relationshipsHtml = tieNames.length
    ? tieNames.map((name) => {
        const tie = relationships[name];
        const cls = tie === "ally" ? "ally" : tie === "rival" ? "rival" : "";
        return `<span class="agent-tie-chip ${cls}">${escapeHtml(name)} (${escapeHtml(tie)})</span>`;
      }).join("")
    : `<span class="civ-label">no notable ties</span>`;

  const reasoningHtml = agent.isThinking
    ? `<span class="agent-reasoning">thinking…</span>`
    : agent.lastReasoning
      ? `<span class="agent-reasoning">${escapeHtml(agent.lastReasoning)}</span>`
      : `<span class="civ-label">no reasoning recorded</span>`;

  const following = followAgentId === agent.id;

  agentDetailEl.classList.add("open");
  agentDetailEl.innerHTML =
    `<h3>${escapeHtml(agent.name)}` +
    `<button type="button" id="agentFollowBtn" class="${following ? "active" : ""}">${following ? "Following" : "Follow"}</button>` +
    `</h3>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Inventory</span>` +
    renderResourceChips(breakdown) +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Relationships</span>` +
    relationshipsHtml +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Last reasoning</span>` +
    reasoningHtml +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Recent activity</span>` +
    `<ul class="agent-history">${historyHtml}</ul>` +
    `</div>`;

  const followBtn = document.getElementById("agentFollowBtn");
  if (followBtn) {
    followBtn.addEventListener("click", () => {
      followAgentId = followAgentId === agent.id ? null : agent.id;
      lastAgentDetailKey = ""; // force re-render to reflect pressed state
      renderAgentDetail();
    });
  }
}

// C4 roll-up header: counts by role, thinking, crisis, and avg hunger across
// living agents. Gated by its own key so it only touches the DOM on change.
let lastRollupKey = "";
function renderAgentRollup(living) {
  if (!living.length) {
    if (lastRollupKey !== "") {
      agentRollupEl.innerHTML = "";
      lastRollupKey = "";
    }
    return;
  }
  const byRole = {};
  let thinking = 0;
  let crisis = 0;
  let hungerTotal = 0;
  let hungerCount = 0;
  for (const a of living) {
    byRole[a.role] = (byRole[a.role] || 0) + 1;
    if (a.isThinking) thinking++;
    if (isAgentCritical(a)) crisis++;
    if (Number.isFinite(Number(a.hunger))) {
      hungerTotal += Number(a.hunger);
      hungerCount++;
    }
  }
  const avgHunger = hungerCount ? Math.round(hungerTotal / hungerCount) : null;
  const rollupKey = JSON.stringify({ byRole, thinking, crisis, avgHunger, n: living.length });
  if (rollupKey === lastRollupKey) return;
  lastRollupKey = rollupKey;

  const roleChips = Object.keys(byRole).sort().map((role) =>
    `<span class="rollup-chip">${escapeHtml(role)} ${byRole[role]}</span>`
  ).join("");
  const thinkingChip = `<span class="rollup-chip">thinking ${thinking}/${living.length}</span>`;
  const crisisChip = `<span class="rollup-chip${crisis ? " rollup-crisis" : ""}">crisis ${crisis}</span>`;
  const hungerChip = avgHunger != null ? `<span class="rollup-chip">avg hunger ${avgHunger}</span>` : "";
  agentRollupEl.innerHTML = roleChips + thinkingChip + crisisChip + hungerChip;
}

function renderAgentPanel() {
  if (selectedAgentId != null && !getLivingAgents().some((a) => a.id === selectedAgentId)) {
    selectedAgentId = null;
  }
  recordAgentHistory();
  renderAgentDetail();
  const living = getLivingAgents();
  const deceased = getDeceasedAgents();
  const panelKey = JSON.stringify({
    living: living.map((a) => [a.id, a.name, a.role, a.age, a.incapacitated, a.health, a.hunger, a.lastAction, a.isThinking, a.lastReasoning]),
    deceased: deceased.map((a) => [a.id, a.name, a.role, a.buried, a.age]),
  });
  renderAgentRollup(living);
  if (panelKey === lastAgentPanelKey) return;
  lastAgentPanelKey = panelKey;

  livingAgentCountEl.textContent = living.length ? `(${living.length} living)` : "";
  if (deceased.length > 0) {
    deadAgentsBtn.hidden = false;
    deadAgentsBtn.textContent = `Deceased (${deceased.length})`;
  } else {
    deadAgentsBtn.hidden = true;
  }

  if (living.length === 0) {
    agentListEl.innerHTML = '<li><span class="civ-label">No living villagers</span></li>';
  } else {
    // Float agents in crisis to the top; stable sort via original-index tiebreak.
    const sortedLiving = living
      .map((a, index) => ({ a, index, score: agentSeverityScore(a) }))
      .sort((x, y) => (x.score - y.score) || (x.index - y.index))
      .map((entry) => entry.a);
    agentListEl.innerHTML = sortedLiving.map((a) => {
      const selected = selectedAgentId === a.id;
      const critical = isAgentCritical(a);
      return `<li class="agent-row${selected ? " agent-selected" : ""}${critical ? " agent-critical" : ""}" data-agent-id="${a.id}">` +
        `<span class="dot" style="background:${a.color}"></span>` +
        `<span><span class="agent-main">${escapeHtml(a.name)} — ${escapeHtml(a.role)}${escapeHtml(agentAgeLabel(a))}</span>` +
        `<span class="agent-status">${escapeHtml(humanizeAction(a))}${agentVitalsHtml(a)}</span>` +
        agentThoughtHtml(a) +
        `</span></li>`;
    }).join("");
  }

  if (deadAgentsModal.classList.contains("open")) {
    deadAgentsListEl.innerHTML = deceased.length
      ? deceased.map(renderDeceasedAgentRow).join("")
      : '<li><span class="civ-label">No deceased villagers</span></li>';
  }
}

function openDeadAgentsModal() {
  deadAgentsListEl.innerHTML = getDeceasedAgents().map(renderDeceasedAgentRow).join("")
    || '<li><span class="civ-label">No deceased villagers</span></li>';
  deadAgentsModal.classList.add("open");
}

function closeDeadAgentsModal() {
  deadAgentsModal.classList.remove("open");
}

deadAgentsBtn.addEventListener("click", openDeadAgentsModal);
deadAgentsCloseBtn.addEventListener("click", closeDeadAgentsModal);
agentListEl.addEventListener("click", (event) => {
  const li = event.target.closest("li.agent-row");
  if (!li) return;
  const id = Number(li.dataset.agentId);
  const wasSelected = selectedAgentId === id;
  selectedAgentId = wasSelected ? null : id;
  if (wasSelected) {
    // Deselecting releases any active follow-cam lock on this agent.
    if (followAgentId === id) followAgentId = null;
  } else {
    // First-time selection: one-time jump-to-center on the agent.
    const agent = getLivingAgents().find((a) => a.id === id);
    if (agent) centerCameraOnAgent(agent);
  }
  syncAgentListSelection();
  renderAgentDetail();
});
canvasWrapEl.addEventListener("mousemove", (event) => {
  const { x, y } = clientToWorld(event.clientX, event.clientY);
  const hit = agentAtWorldPoint(x, y);
  hoveredAgentId = hit ? hit.id : null;
});
canvasWrapEl.addEventListener("mouseleave", () => {
  hoveredAgentId = null;
});
deadAgentsModal.addEventListener("click", (event) => {
  if (event.target === deadAgentsModal) closeDeadAgentsModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && deadAgentsModal.classList.contains("open")) {
    closeDeadAgentsModal();
  }
});

function formatEstClock(ms) {
  return new Date(ms).toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit",
  });
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return d > 0 ? `${d}d ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
}

setInterval(() => { timeNowEl.textContent = formatEstClock(Date.now()); }, 1000);

function renderSidebar() {
  const civ = getCiv();
  if (world.uptimeSeconds != null) timeUptimeEl.textContent = formatDuration(world.uptimeSeconds);
  const cal = world.calendar;
  if (cal) {
    const phase = cal.isNight ? "night" : "day";
    timeCalendarEl.textContent =
      `Year ${cal.year} · ${cal.season} · day ${cal.dayOfSeason}/${cal.daysPerSeason} · ${phase}`;
  }
  const lmStatus = world.lmStatus || "offline";
  const statusColor = lmStatus === "online" ? "#4CAF50"
    : lmStatus === "compute_error" ? "#FF9800" : "#F44336";
  const label = lmStatus === "online" ? "Ollama: online"
    : lmStatus === "compute_error" ? "Ollama: GPU memory error"
    : (lmStatus === "disconnected" ? "Server: disconnected" : "Ollama: offline");
  statusDot.style.background = lmStatus === "disconnected" ? "#9E9E9E" : statusColor;
  statusLabel.innerHTML = label + (lmStatus === "compute_error"
    ? '<span class="hint">Use a smaller model or Q4 quant</span>'
    : (lmStatus === "disconnected"
      ? '<span class="hint">Showing last frame; retrying /state…</span>'
      : ""));

  // Phase D: the era replaces the vanity level when the server sends one
  // (TECH_TREE_ENABLED); otherwise the classic level chip renders unchanged.
  if (civ.era) {
    civLevelLabelEl.textContent = "Era:";
    civLevelEl.textContent = civ.techTier ? `${civ.era} (tier ${civ.techTier})` : civ.era;
  } else {
    civLevelLabelEl.textContent = "Level:";
    civLevelEl.textContent = civ.level != null ? civ.level : 1;
  }
  civStructuresEl.textContent = (civ.structures || []).length;
  const upgradable = (civ.structures || []).filter((s) => !s.isRuin && (s.level == null || s.level < 100));
  if (STRUCTURE_UPGRADES_ENABLED && upgradable.length) {
    civUpgradesRowEl.style.display = "";
    const top = upgradable.slice().sort((a, b) => (b.level || 1) - (a.level || 1)).slice(0, 3);
    civUpgradesEl.textContent = `${upgradable.length} (${top.map((s) => `${s.name || s.type} Lv.${s.level || 1}`).join(", ")})`;
  } else {
    civUpgradesRowEl.style.display = "none";
  }
  renderProjectsList();

  // Phase D: "Council in session" banner, driven by civ.councilActive.
  const councilActive = civ.councilActive;
  if (councilActive && councilActive.active) {
    councilBannerEl.style.display = "block";
    councilBannerEl.textContent =
      `Council in session — ${councilActive.proposals || 0} proposal(s)` +
      (councilActive.proposers ? ` (${councilActive.proposers.join(", ")})` : "");
  } else {
    councilBannerEl.style.display = "none";
  }

  // --- Civilization detail rows (change-detected) ---
  const sidebarKey = JSON.stringify([
    // villageResourceBreakdown() is stockpile's proxy in this key (stockpile omitted — ~40 keys, changes every poll)
    civ.resourceRegistry, villageResourceBreakdown(), customProjectIds(),
    civ.recipes, civ.rules, civ.constitution, civ.pendingRules, civ.pendingBlueprints,
    world.benchmarks, civ.collectAttempts, civ.collectSuccesses,
    civ.councilLog, civ.era, civ.techTier,
    CRAFTING_ENABLED, RULES_ENABLED, BENCHMARKS_ENABLED, MEMES_ENABLED, MEMORY_ENABLED
  ]);
  if (sidebarKey !== lastSidebarKey) {
    lastSidebarKey = sidebarKey;

    civResourcesEl.textContent = totalVillageResources();
    civResourceListEl.innerHTML = renderResourceChips(villageResourceBreakdown());

    const customIds = customProjectIds();
    civCustomBuildsEl.textContent = customIds.length;
    civCustomListEl.innerHTML = customIds.map((id) =>
      `<span class="res-chip civ-value">${escapeHtml((civ.projectRegistry[id] || {}).name || id)}</span>`
    ).join("");

    civCollectRateEl.textContent = civ.collectAttempts > 0
      ? `${Math.round(100 * civ.collectSuccesses / civ.collectAttempts)}% (${civ.collectSuccesses}/${civ.collectAttempts})`
      : "—";

    // Recipes registry (server-supplied; gated by the crafting flag).
    const recipes = civ.recipes || {};
    if (CRAFTING_ENABLED && Object.keys(recipes).length > 0) {
      const recipeIds = Object.keys(recipes);
      civRecipeRow.style.display = "block";
      civRecipeCountEl.textContent = recipeIds.length;
      civRecipeListEl.innerHTML = recipeIds.map((id) => {
        const r = recipes[id];
        const ins = Object.entries(r.inputs || {}).map(([k, v]) => `${k}×${v}`).join("+");
        return `<span class="res-chip civ-value">${escapeHtml(r.name || id)} <span class="civ-label">(${escapeHtml(ins)})</span></span>`;
      }).join("");
    } else {
      civRecipeRow.style.display = "none";
    }

    const rules = civ.rules || [];
    const constitution = civ.constitution || [];
    const pendingRules = civ.pendingRules || [];
    if (RULES_ENABLED && (rules.length > 0 || constitution.length > 0 || pendingRules.length > 0)) {
      civRulesRow.style.display = "block";
      civRuleCountEl.textContent = rules.length;
      const enacted = (constitution.length ? constitution : rules).map((r) => {
        const status = r.status ? ` <span class="civ-label">[${escapeHtml(r.status)}]</span>` : "";
        const amendment = r.supersedes
          ? ` <span class="civ-label">→ ${escapeHtml(r.supersedes)}</span>` : "";
        return `<span class="res-chip civ-value">${escapeHtml(r.name || r.id)}${status}${amendment}</span>`;
      }
      ).join("");
      const pending = pendingRules.map((r) => {
        const votes = r.votes || {};
        const yes = Object.values(votes).filter((v) => v === "yes").length;
        const no = Object.values(votes).filter((v) => v === "no").length;
        const label = r.name || r.id;
        return `<span class="res-chip"><span class="civ-label">vote ${escapeHtml(label)} (${yes}/${no})</span></span>`;
      }).join("");
      civRuleListEl.innerHTML = enacted + pending;
    } else {
      civRulesRow.style.display = "none";
    }

    if (BENCHMARKS_ENABLED) {
      civBenchRow.style.display = "block";
      civBenchEl.innerHTML = renderBenchmarks();
    } else {
      civBenchRow.style.display = "none";
    }

    renderCouncil(civ);
    if (SHOW_SETTLEMENTS) {
      renderSettlements(civ);
    } else {
      settlementsSectionEl.style.display = "none";
    }

    const pendingBp = civ.pendingBlueprints || [];
    if (pendingBp.length > 0) {
      civPendingRow.style.display = "block";
      civPendingEl.innerHTML = pendingBp.slice(0, 3).map((b) =>
        `<div class="civ-value">${escapeHtml(b.name)} <span class="civ-label">by ${escapeHtml(b.proposedBy)}</span></div>`
      ).join("");
    } else {
      civPendingRow.style.display = "none";
    }
  }

  renderAgentPanel();

  // --- Activity & Chat panel (change-detected separately, higher churn) ---
  const conversation = world.conversation || [];
  const activity = world.activity || [];
  const logKey = JSON.stringify(SHOW_CONVERSATIONS ? [conversation, activity] : [activity]);
  if (logKey !== lastLogKey) {
    lastLogKey = logKey;
    if (SHOW_CONVERSATIONS) {
      const convScroll = convListEl.scrollTop;
      convListEl.innerHTML = conversation.map((c) => {
        const kindLabel = c.kind && c.kind !== "speech"
          ? `<span class="kind">[${escapeHtml(c.kind)}]</span> `
          : "";
        const msg = c.message
          ? `<span class="msg">${escapeHtml(c.message)}</span>`
          : (c.outcome ? `<span class="msg muted">(${escapeHtml(c.outcome)})</span>` : "");
        return `<li>${kindLabel}<span class="from">${escapeHtml(c.from)}</span> <span class="arrow">→</span> <span class="to">${escapeHtml(c.to)}</span>${msg ? ": " + msg : ""}</li>`;
      }).join("");
      convListEl.scrollTop = convScroll;
    }

    actListEl.innerHTML = activity.map((line) =>
      `<li>${escapeHtml(line)}</li>`
    ).join("");
  }

  if (!CHRONICLE_ENABLED) {
    chronicleLogEl.style.display = "none";
  } else {
    chronicleLogEl.style.display = "";
    const chronicle = world.chronicle || [];
    const chronicleKey = JSON.stringify(chronicle);
    if (chronicleKey !== lastChronicleKey) {
      lastChronicleKey = chronicleKey;
      const scrollTop = chronicleListEl.scrollTop;
      chronicleListEl.innerHTML = chronicle.slice().reverse().map((entry) => {
        const kind = String(entry.kind || "event").replace(/_/g, " ");
        const frame = entry.frame != null ? `frame ${entry.frame}` : "";
        return `<li><span class="chronicle-kind">${escapeHtml(kind)}</span> ` +
          `${escapeHtml(entry.text || "")}` +
          (frame ? ` <span class="chronicle-frame">${escapeHtml(frame)}</span>` : "") +
          `</li>`;
      }).join("") || `<li class="civ-label">No village milestones yet</li>`;
      chronicleListEl.scrollTop = scrollTop;
    }
  }

  // Founding banner: derived by diffing newly-appeared "district_founded"
  // chronicle entries rather than a dedicated server field (foundedFrame).
  // The client already parses the full chronicle array every poll (see
  // chronicleKey above), so remembering which frames have already been
  // banner-ed needs no new /state shape and no extra server-side state --
  // the simpler option per the phase brief.
  if (FOUNDING_EVENTS_ENABLED) {
    const foundingEntries = (world.chronicle || []).filter((e) => e.kind === "district_founded");
    if (foundingFramesSeen === null) {
      // First snapshot after page load/refresh: remember whatever foundings
      // already happened without banner-ing them, so resuming a long-running
      // village doesn't replay its whole settlement history as banners.
      foundingFramesSeen = new Set(foundingEntries.map((e) => e.frame));
    } else {
      const fresh = foundingEntries.find((e) => !foundingFramesSeen.has(e.frame));
      if (fresh) {
        foundingFramesSeen.add(fresh.frame);
        foundingBannerEl.textContent = fresh.text;
        foundingBannerEl.style.display = "block";
        if (foundingBannerTimer) clearTimeout(foundingBannerTimer);
        foundingBannerTimer = setTimeout(() => {
          foundingBannerEl.style.display = "none";
          foundingBannerTimer = null;
        }, 6000);
      }
      // Chronicle is a capped ring (CHRONICLE_CAP) -- drop frames that have
      // aged out so this set doesn't grow unboundedly across a long session.
      for (const f of Array.from(foundingFramesSeen)) {
        if (!foundingEntries.some((e) => e.frame === f)) foundingFramesSeen.delete(f);
      }
    }
  } else {
    foundingBannerEl.style.display = "none";
  }

  // Disaster banner: edge-detect new chronicle entries with kind === "disaster".
  if (CHRONICLE_ENABLED) {
    const disasterEntries = (world.chronicle || []).filter((e) => e.kind === "disaster");
    if (disasterFramesSeen === null) {
      disasterFramesSeen = new Set(disasterEntries.map((e) => e.frame));
    } else {
      const fresh = disasterEntries.find((e) => !disasterFramesSeen.has(e.frame));
      if (fresh) {
        disasterFramesSeen.add(fresh.frame);
        disasterBannerEl.textContent = fresh.text;
        disasterBannerEl.style.display = "block";
        if (disasterBannerTimer) clearTimeout(disasterBannerTimer);
        disasterBannerTimer = setTimeout(() => {
          disasterBannerEl.style.display = "none";
          disasterBannerTimer = null;
        }, 5500);
      }
      for (const f of Array.from(disasterFramesSeen)) {
        if (!disasterEntries.some((e) => e.frame === f)) disasterFramesSeen.delete(f);
      }
    }
  } else {
    disasterBannerEl.style.display = "none";
  }

  trackStructureConditionDeltas();

  renderDivineConsole();
}

function calendarPhase(cal) {
  if (!cal) return null;
  const f = cal.dayFraction;
  if (f == null) return cal.isNight ? "night" : "day";
  const duskStart = TWILIGHT_START;
  const duskEnd = TWILIGHT_END_DUSK;
  const nightEnd = TWILIGHT_START_DAWN;
  if (f < duskStart) return "day";
  if (f < duskEnd) return "dusk";
  if (f < nightEnd) return "night";
  return "dawn";
}

function weatherStateLabel(weather) {
  if (!weather || !weather.state) return null;
  const labels = { clear: "Clear", gathering: "Gathering", storm: "Storm", clearing: "Clearing" };
  return labels[weather.state] || null;
}

function renderWorldClockHud() {
  if (!WORLD_CLOCK_HUD_ENABLED) {
    worldClockHudEl.style.display = "none";
    return;
  }
  const cal = world.calendar;
  const phase = calendarPhase(cal);
  if (!cal || !cal.season || !phase) {
    worldClockHudEl.style.display = "none";
    return;
  }
  worldClockHudEl.style.display = "block";
  let label = `${cal.season} ${phase}`;
  if (WEATHER_ENABLED && world.weather) {
    const wl = weatherStateLabel(world.weather);
    if (wl) label += ` · ${wl}`;
  }
  worldClockHudEl.textContent = label;
}

// =====================================================================
// Phase D — Council panel (pure renderer over civ.councilLog, the engine's
// persisted debate records). Latest debate renders as a thread of proposal
// cards (winner highlighted, losers greyed with their rejection reasons);
// older debates collapse to a one-line history list.
// =====================================================================
// Debate records carry a wall-clock "ts" (ISO string) alongside "frame" as
// of 2026-07-07; records persisted before that change have no "ts", so fall
// back to the frame number rather than showing a blank/invalid time.
function formatCouncilTime(ts, frame) {
  if (ts) {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) {
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      return sameDay
        ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
  }
  return frame != null ? `frame ${frame}` : "unknown time";
}

function councilCardHtml(p, verdict) {
  const approvedId = verdict && verdict.approved_id;
  const reasons = (verdict && verdict.reasons_per_candidate) || {};
  const isWinner = approvedId && p.id === approvedId;
  const reason = reasons[p.id];
  const needs = Object.entries(p.needs || {}).map(([k, v]) => `${k}×${v}`).join(", ");
  const cls = isWinner ? "council-card winner" : (verdict ? "council-card loser" : "council-card");
  return `<div class="${cls}" role="button" tabindex="0">` +
    `<span class="cc-name">${escapeHtml(p.name || p.id)}</span> ` +
    `<span class="cc-by">by ${escapeHtml(p.proposer || "?")}</span>` +
    `<span class="cc-fn">${escapeHtml(p.function_summary || "")}${needs ? " · needs " + escapeHtml(needs) : ""}</span>` +
    (isWinner ? `<span class="cc-verdict">✔ approved</span>` : "") +
    (reason ? `<span class="cc-reason">✘ ${escapeHtml(reason)}</span>` : "") +
    `</div>`;
}

const councilTranscriptModal = document.getElementById("councilTranscriptModal");
const councilTranscriptBodyEl = document.getElementById("councilTranscriptBody");
const councilTranscriptTitleEl = document.getElementById("councilTranscriptTitle");
const councilTranscriptCloseBtn = document.getElementById("councilTranscriptCloseBtn");

// Daily Council is intentionally a read-only view of the serialized session.
// The only local state below is modal preference, so closing it never changes
// the simulation and a session cannot force the observer to keep it open.
const councilAssemblyModal = document.getElementById("councilAssemblyModal");
const councilAssemblyCanvas = document.getElementById("councilAssemblyCanvas");
const councilAssemblyCtx = councilAssemblyCanvas.getContext("2d");
const councilAssemblyPhaseEl = document.getElementById("councilAssemblyPhase");
const councilAssemblyAgendaEl = document.getElementById("councilAssemblyAgenda");
const councilAssemblyTallyEl = document.getElementById("councilAssemblyTally");
const councilAssemblyTranscriptEl = document.getElementById("councilAssemblyTranscript");
const councilAssemblyBallotSectionEl = document.getElementById("councilAssemblyBallotSection");
const councilAssemblyVerdictSectionEl = document.getElementById("councilAssemblyVerdictSection");
const councilAssemblyVerdictHeadingEl = document.getElementById("councilAssemblyVerdictHeading");
const councilAssemblyVerdictEl = document.getElementById("councilAssemblyVerdict");
const councilAssemblyCloseBtn = document.getElementById("councilAssemblyCloseBtn");
const councilAssemblyReopenBtn = document.getElementById("councilAssemblyReopenBtn");
let councilAssemblyDismissedId = null;
let councilAssemblyAutoOpenedId = null;

function dailyCouncilId(council) {
  return council ? `${council.day ?? "?"}:${council.frame ?? "?"}` : null;
}

function isDailyCouncilLive(council) {
  return !!(council && council.phase && council.phase !== "adjourned");
}

function closeCouncilAssembly(manual = true) {
  const council = getCiv().dailyCouncil;
  if (manual && council) councilAssemblyDismissedId = dailyCouncilId(council);
  councilAssemblyModal.classList.remove("open");
  if (isDailyCouncilLive(council)) councilAssemblyReopenBtn.classList.add("visible");
}

function openCouncilAssembly() {
  const council = getCiv().dailyCouncil;
  if (!isDailyCouncilLive(council)) return;
  councilAssemblyDismissedId = null;
  councilAssemblyAutoOpenedId = dailyCouncilId(council);
  councilAssemblyModal.classList.add("open");
  councilAssemblyReopenBtn.classList.remove("visible");
  renderDailyCouncil(council);
}

function dailyCouncilTranscriptEntry(entry) {
  const who = entry.who || entry.proposer || entry.elder || "Council";
  const text = entry.text || entry.message || entry.title || entry.outcome || entry.type || "event";
  const feeling = entry.feeling ? ` <span class="assembly-feeling">feeling: ${escapeHtml(entry.feeling)}</span>` : "";
  const time = formatCouncilTime(entry.ts, entry.frame);
  return `<div class="assembly-entry"><span class="assembly-time">${escapeHtml(time)}</span>` +
    `<span class="assembly-who">${escapeHtml(who)}</span>: ${escapeHtml(text)}${feeling}</div>`;
}

// Autoscroll only sticks the view to the newest entry while the observer is
// already at (or near) the bottom -- if they've scrolled up to read earlier
// discussion, the ~10Hz /state poll must not yank them back down every tick.
function isScrolledNearBottom(el, thresholdPx = 40) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
}

function renderDailyCouncil(council) {
  const live = isDailyCouncilLive(council);
  if (!live) {
    councilAssemblyModal.classList.remove("open");
    councilAssemblyReopenBtn.classList.remove("visible");
    return;
  }
  const id = dailyCouncilId(council);
  if (councilAssemblyAutoOpenedId !== id && councilAssemblyDismissedId !== id) {
    councilAssemblyAutoOpenedId = id;
    councilAssemblyModal.classList.add("open");
  }
  councilAssemblyReopenBtn.classList.toggle("visible", !councilAssemblyModal.classList.contains("open"));
  councilAssemblyPhaseEl.textContent = `${council.phase || "convening"} · day ${council.day ?? "?"}`;
  const agenda = council.agenda || [];
  councilAssemblyAgendaEl.innerHTML = agenda.length
    ? agenda.map((item) => `<li><strong>${escapeHtml(item.topic || "Topic")}</strong>${item.detail ? ` — ${escapeHtml(item.detail)}` : ""}</li>`).join("")
    : "<li>No agenda published yet.</li>";

  const ballot = council.ballot;
  councilAssemblyBallotSectionEl.style.display = ballot ? "block" : "none";
  if (ballot) {
    const votes = ballot.votes || {};
    const attendees = council.attendees || [];
    if (ballot.kind === "succession") {
      const candidates = ballot.candidates || [];
      const totals = Object.fromEntries(candidates.map((name) => [name, 0]));
      let abstain = 0;
      attendees.forEach((name) => {
        const vote = votes[name];
        if (vote in totals) totals[vote] += 1;
        else if (vote === "abstain") abstain += 1;
      });
      councilAssemblyTallyEl.innerHTML =
        `<div class="assembly-entry"><strong>${escapeHtml(ballot.title || "Choose the next elder")}</strong></div>` +
        candidates.map((name) => `<div class="assembly-vote yes">${escapeHtml(name)} ${totals[name]}</div>`).join("") +
        `<div class="assembly-vote abstain">Abstain ${abstain}</div>` +
        attendees.map((name) => {
          const vote = votes[name] || "pending";
          return `<div class="assembly-vote ${vote === "pending" ? "abstain" : "yes"}">${escapeHtml(name)}: ${escapeHtml(vote)}</div>`;
        }).join("");
    } else {
      const totals = { yes: 0, no: 0, abstain: 0 };
      attendees.forEach((name) => { const vote = votes[name]; if (vote in totals) totals[vote] += 1; });
      councilAssemblyTallyEl.innerHTML =
        `<div class="assembly-vote yes">Yes ${totals.yes}</div><div class="assembly-vote no">No ${totals.no}</div><div class="assembly-vote abstain">Abstain ${totals.abstain}</div>` +
        attendees.map((name) => {
          const vote = votes[name] || "pending";
          return `<div class="assembly-vote ${vote === "pending" ? "abstain" : vote}">${escapeHtml(name)}: ${escapeHtml(vote)}</div>`;
        }).join("");
    }
  }
  const verdict = council.verdict;
  councilAssemblyVerdictSectionEl.style.display = verdict ? "block" : "none";
  if (verdict) {
    const succession = ballot && ballot.kind === "succession";
    councilAssemblyVerdictHeadingEl.textContent = succession ? "Village verdict" : "Elder ruling";
    const ruling = verdict.outcome || verdict.elderRuling || verdict.winner ||
      (succession ? "Village verdict pending" : "Elder ruling pending");
    councilAssemblyVerdictEl.textContent = ruling;
  }
  const transcript = council.transcript || [];
  const stickToBottom = isScrolledNearBottom(councilAssemblyTranscriptEl);
  councilAssemblyTranscriptEl.innerHTML = transcript.length
    ? transcript.map(dailyCouncilTranscriptEntry).join("")
    : '<div class="assembly-entry">The council is gathering.</div>';
  if (stickToBottom) councilAssemblyTranscriptEl.scrollTop = councilAssemblyTranscriptEl.scrollHeight;
}

function drawCouncilAssemblyTable(council, frameTick) {
  if (!councilAssemblyModal.classList.contains("open") || !isDailyCouncilLive(council)) return;
  const ctx = councilAssemblyCtx, size = councilAssemblyCanvas.width, center = size / 2;
  const seats = [...(council.seats || [])].sort((a, b) => (a.seatIndex || 0) - (b.seatIndex || 0));
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#201b19"; ctx.fillRect(0, 0, size, size);
  const radius = Math.max(220, Math.min(285, 215 + seats.length * 6));
  ctx.fillStyle = "#4a2815"; ctx.beginPath(); ctx.arc(center, center, radius * .67, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#bd7b3f"; ctx.lineWidth = 10; ctx.beginPath(); ctx.arc(center, center, radius * .67, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = "#d3a65f"; ctx.font = "bold 16px system-ui"; ctx.textAlign = "center"; ctx.fillText("DAILY COUNCIL", center, center - 4);
  ctx.fillStyle = "#f1d69b"; ctx.font = "12px system-ui"; ctx.fillText(`${seats.length} seated · ${council.phase}`, center, center + 18);
  const agentsByName = new Map(getAgents().map((agent) => [agent.name, agent]));
  seats.forEach((seat, index) => {
    // seatIndex and isHead come from the engine; this only maps that serialized
    // ring order into the modal's pixel space.
    const angle = -Math.PI / 2 + ((seat.seatIndex ?? index) / Math.max(seats.length, 1)) * Math.PI * 2;
    const x = center + Math.cos(angle) * radius, y = center + Math.sin(angle) * radius;
    ctx.fillStyle = seat.isHead ? "#e7bd56" : "#795335";
    ctx.beginPath(); ctx.arc(x, y + 7, seat.isHead ? 31 : 25, 0, Math.PI * 2); ctx.fill();
    const agent = agentsByName.get(seat.name) || { name: seat.name, role: seat.role || "villager", color: "#BDBDBD" };
    const seated = { ...agent, x, y: y + 10, targetX: x, targetY: y + 10 };
    drawAgentSprite(ctx, seated, frameTick);
    ctx.fillStyle = seat.isHead ? "#ffe4a1" : "#e9e3d4"; ctx.font = seat.isHead ? "bold 13px system-ui" : "12px system-ui";
    ctx.textAlign = "center"; ctx.fillText(`${seat.isHead ? "★ " : ""}${seat.name}`, x, y + 48);
  });
}

function councilAgentNames(record) {
  const names = [...(record.proposers || [])];
  const elder = getAgents().find((a) => a.role === "elder" && !a.deceased);
  if (elder && !names.includes(elder.name)) names.push(elder.name);
  return names;
}

const COUNCIL_TRANSCRIPT_TYPES = new Set(["convene", "proposal", "verdict", "dissolve"]);
const SIM_FPS = 30;
let councilModalRecord = null;

function councilTranscriptEntries(record) {
  return (record.transcript || []).filter((e) => COUNCIL_TRANSCRIPT_TYPES.has(e.type));
}

// Two council systems both persist to record.transcript with different
// per-entry schemas: the legacy invention council (proposer/elder/blueprint
// fields, type in the 4-entry COUNCIL_TRANSCRIPT_TYPES set) and the Daily
// Council (who/text/feeling fields, plus phase/speak/vote/adjourn/etc types).
// Detect which one a record holds so the modal can render each correctly.
function isDailyCouncilRecord(record) {
  const transcript = (record && record.transcript) || [];
  return transcript.some((e) => e.who !== undefined || !COUNCIL_TRANSCRIPT_TYPES.has(e.type));
}

// Daily Council schema-tolerant renderer (mirrors dailyCouncilTranscriptEntry
// used by the live Assembly modal), but emits .ct-* markup to match the rest
// of the history transcript modal.
function renderDailyCouncilTranscriptEntry(entry) {
  const time = councilTimePrefix(entry, councilModalRecord);
  const who = entry.who || entry.proposer || entry.elder || entry.candidate || "Council";
  const text = entry.text || entry.message || entry.title || entry.topic || entry.outcome || entry.type || "event";
  const feeling = entry.feeling ? ` <span class="ct-reasoning">feeling: ${escapeHtml(entry.feeling)}</span>` : "";
  return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(who)}</span>: ${escapeHtml(text)}${feeling}</div>`;
}

function councilEntryTimeLabel(entry, record) {
  const frame = entry.frame != null ? entry.frame : entry.frame_tick;
  if (entry.ts) {
    const wall = formatCouncilTime(entry.ts, frame);
    return frame != null ? `${wall} · frame ${frame}` : wall;
  }
  if (frame != null && record) {
    const anchorTs = record.started_ts || record.ts;
    const anchorFrame = record.start_frame != null ? record.start_frame : record.frame;
    if (anchorTs && anchorFrame != null) {
      const d0 = new Date(anchorTs).getTime();
      if (!isNaN(d0)) {
        const est = new Date(d0 + ((frame - anchorFrame) / SIM_FPS) * 1000);
        return `${formatCouncilTime(est.toISOString(), frame)} · frame ${frame} (est.)`;
      }
    }
    return `frame ${frame}`;
  }
  return "";
}

function councilTimePrefix(entry, record) {
  const label = councilEntryTimeLabel(entry, record);
  return label ? `<span class="ct-time">${escapeHtml(label)}</span>` : "";
}

function renderTranscriptEntry(entry) {
  const time = councilTimePrefix(entry, councilModalRecord);
  const t = entry.type || "event";
  if (t === "convene") {
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.elder || "Elder")}</span> convenes the council` +
      (entry.proposers ? ` (${escapeHtml(entry.proposers.join(", "))})` : "") +
      (entry.message ? `: <span class="ct-action">${escapeHtml(entry.message)}</span>` : "") +
      `</div>`;
  }
  if (t === "proposal") {
    const needs = Object.entries(entry.needs || {}).map(([k, v]) => `${k}×${v}`).join(", ");
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.proposer || "?")}</span> proposes ` +
      `<span class="ct-action">${escapeHtml(entry.blueprint_name || entry.blueprint_id || "a blueprint")}</span>` +
      (entry.function_summary ? ` — ${escapeHtml(entry.function_summary)}` : "") +
      (needs ? ` · needs ${escapeHtml(needs)}` : "") +
      (entry.message ? `<br><span class="ct-reasoning">"${escapeHtml(entry.message)}"</span>` : "") +
      (entry.reasoning ? `<span class="ct-reasoning">${escapeHtml(entry.reasoning)}</span>` : "") +
      `</div>`;
  }
  if (t === "verdict") {
    const rej = entry.rejections || {};
    const rejText = Object.entries(rej).map(([id, r]) => `${id}: ${r}`).join("; ");
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.elder || "Elder")}</span> verdict: ` +
      `<span class="ct-action">${escapeHtml(entry.approved_name || entry.approved_id || "approved")}</span>` +
      (rejText ? `<br><span class="ct-reasoning">Rejected — ${escapeHtml(rejText)}</span>` : "") +
      (entry.message ? `<br><span class="ct-reasoning">"${escapeHtml(entry.message)}"</span>` : "") +
      (entry.reasoning ? `<span class="ct-reasoning">${escapeHtml(entry.reasoning)}</span>` : "") +
      `</div>`;
  }
  if (t === "dissolve") {
    return `<div class="ct-entry">${time}<span class="ct-action">${escapeHtml(entry.message || "Council dissolved")}</span></div>`;
  }
  return "";
}

function renderLlmTranscriptEntry(entry) {
  const time = councilTimePrefix(
    { ts: entry.ts, frame: entry.frame_tick },
    councilModalRecord,
  );
  const d = entry.decision || {};
  const action = d.action || entry.error || "unknown";
  const bp = d.blueprint_name ? ` (${d.blueprint_name})` : "";
  const inv = entry.invention_only ? " [invention]" : "";
  return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.agent_name || "?")}</span>` +
    ` <span class="ct-action">${escapeHtml(action)}${escapeHtml(bp)}</span>${inv}` +
    (d.reasoning ? `<span class="ct-reasoning">${escapeHtml(d.reasoning)}</span>` : "") +
    (d.message ? `<span class="ct-reasoning">"${escapeHtml(d.message)}"</span>` : "") +
    `</div>`;
}

async function openCouncilTranscript(idx) {
  const record = (getCiv().councilLog || [])[idx];
  if (!record) return;
  councilModalRecord = record;
  councilTranscriptTitleEl.textContent =
    `Council — ${formatCouncilTime(record.ts, record.frame)}: ${record.outcome || "debate"}`;
  const proposals = record.proposals || [];
  const dailyCouncil = isDailyCouncilRecord(record);
  const sequence = dailyCouncil ? (record.transcript || []) : councilTranscriptEntries(record);
  let html = dailyCouncil
    ? `<p class="ct-note">Daily Council session — a live village gathering where attendees speak, debate ` +
      `proposals, vote, and the elder delivers a verdict.</p>`
    : `<p class="ct-note">Invention councils are blueprint pitches, not live debate chat. ` +
      `Villagers each propose a structure design; the elder compares them and picks a winner.</p>`;
  if (!sequence.length && (!record.transcript || !record.transcript.length)) {
    html += `<p class="ct-note">Full timeline available for councils held after the transcript update.</p>`;
  } else if (sequence.length) {
    html += `<div class="ct-section"><h4>Council timeline</h4>` +
      sequence.map(dailyCouncil ? renderDailyCouncilTranscriptEntry : renderTranscriptEntry).join("") + `</div>`;
  } else if (record.transcript && record.transcript.length) {
    html += `<p class="ct-note">Older record — only structured events are shown (random village chat omitted).</p>`;
  }
  if (proposals.length) {
    html += `<div class="ct-section"><h4>Proposals compared</h4>` +
      proposals.map((p) => councilCardHtml(p, record.verdict)).join("") + `</div>`;
  }
  const llmHeading = dailyCouncil ? "Council speeches &amp; verdict (LLM)" : "Blueprint pitches &amp; verdict (LLM)";
  councilTranscriptBodyEl.innerHTML = html + `<div class="ct-section" id="councilLlmSection"><h4>${llmHeading}</h4><span class="civ-label">Loading…</span></div>`;
  councilTranscriptModal.classList.add("open");

  const start = record.start_frame != null ? record.start_frame : record.frame;
  const end = record.end_frame != null ? record.end_frame : record.frame;
  const agents = councilAgentNames(record).join(",");
  const llmSection = document.getElementById("councilLlmSection");
  try {
    const resp = await fetch(`/council-llm-log?start_frame=${start}&end_frame=${end}&agents=${encodeURIComponent(agents)}`);
    const data = await resp.json();
    const entries = data.entries || [];
    llmSection.innerHTML = `<h4>${llmHeading}</h4>` +
      (entries.length
        ? entries.map(renderLlmTranscriptEntry).join("")
        : `<span class="civ-label">No blueprint or verdict LLM turns logged for this council window.</span>`);
  } catch (_err) {
    llmSection.innerHTML = `<h4>${llmHeading}</h4><span class="civ-label">Could not load LM records.</span>`;
  }
}

function closeCouncilTranscript() {
  councilTranscriptModal.classList.remove("open");
  councilModalRecord = null;
}

const settlementsSectionEl = document.getElementById("settlementsSection");
const settlementsMetaEl = document.getElementById("settlementsMeta");
const settlementsListEl = document.getElementById("settlementsList");

function renderSettlements(civ) {
  if (!PATH1_ENABLED) {
    settlementsSectionEl.style.display = "none";
    return;
  }
  const settlements = civ.settlements || [];
  if (!settlements.length) {
    settlementsSectionEl.style.display = "none";
    return;
  }
  settlementsSectionEl.style.display = "block";
  const nightNote = civ.isNight ? " · night" : "";
  settlementsMetaEl.textContent = `${settlements.length} settlement(s)${nightNote}`;
  settlementsListEl.innerHTML = settlements.map((s) => {
    const districts = (s.districts || []).length;
    return `<li><span class="civ-value">${escapeHtml(s.name || s.id)}</span> ` +
      `<span class="civ-label">(${districts} district${districts === 1 ? "" : "s"})</span></li>`;
  }).join("");
}

function renderCouncil(civ) {
  const log = civ.councilLog || [];
  if (!log.length) {
    councilSectionEl.style.display = "none";
    return;
  }
  councilSectionEl.style.display = "block";
  const latest = log[0];
  councilMetaEl.textContent =
    `Latest debate (${formatCouncilTime(latest.ts, latest.frame)}, ` +
    `${latest.trigger || "?"}): ${latest.outcome || "in progress"}`;
  councilMetaEl.classList.add("council-clickable");
  councilMetaEl.dataset.councilIdx = "0";
  const proposals = latest.proposals || [];
  councilCardsEl.innerHTML = proposals.length
    ? proposals.map((p) => councilCardHtml(p, latest.verdict)).join("")
    : '<span class="civ-label">no proposals recorded</span>';
  councilHistoryEl.innerHTML = log.slice(1, 8).map((r, i) => {
    const n = (r.proposals || []).length;
    return `<li data-council-idx="${i + 1}">${formatCouncilTime(r.ts, r.frame)} — ${n} proposal(s) — ${escapeHtml(r.outcome || "?")}</li>`;
  }).join("");
}

councilMetaEl.addEventListener("click", () => {
  const idx = Number(councilMetaEl.dataset.councilIdx);
  if (!Number.isNaN(idx)) openCouncilTranscript(idx);
});
councilCardsEl.addEventListener("click", () => openCouncilTranscript(0));
councilHistoryEl.addEventListener("click", (event) => {
  const li = event.target.closest("li[data-council-idx]");
  if (!li) return;
  openCouncilTranscript(Number(li.dataset.councilIdx));
});
councilTranscriptCloseBtn.addEventListener("click", closeCouncilTranscript);
councilTranscriptModal.addEventListener("click", (event) => {
  if (event.target === councilTranscriptModal) closeCouncilTranscript();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && councilTranscriptModal.classList.contains("open")) {
    closeCouncilTranscript();
  }
});
councilAssemblyCloseBtn.addEventListener("click", () => closeCouncilAssembly(true));
councilAssemblyReopenBtn.addEventListener("click", openCouncilAssembly);
councilAssemblyModal.addEventListener("click", (event) => {
  if (event.target === councilAssemblyModal) closeCouncilAssembly(true);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && councilAssemblyModal.classList.contains("open")) {
    closeCouncilAssembly(true);
  }
});

// =====================================================================
// Minimap (Phase 6, optional polish) -- a small fixed-position canvas showing
// district-bounds rectangles by kind (including any founded after cold
// start), agent dots, and a viewport-outline. Pure renderer: reads only the
// existing GET /state poll (world.agents) + the live district list
// (getDistricts()); no engine changes. More load-bearing than in a
// fixed-size world -- once districts can be founded mid-session, this is the
// only way to notice new territory came online without scrolling to find it.
// =====================================================================
const minimapCanvas = document.getElementById("minimap");
const minimapCtx = minimapCanvas.getContext("2d");
const MINIMAP_KIND_COLORS = {
  farm: "#6eb840", forest: "#245a24", village: "#c4995a", market: "#b8773a",
  beach: "#e5d693", cave: "#454545", ocean: "#2d9ed9", workshop: "#98988a",
};

function renderMinimap() {
  const w = minimapCanvas.width, h = minimapCanvas.height;
  const sx = w / WORLD_W, sy = h / WORLD_H;
  minimapCtx.fillStyle = "#0e0e14";
  minimapCtx.fillRect(0, 0, w, h);

  for (const d of getDistricts()) {
    const b = d.bounds;
    minimapCtx.fillStyle = MINIMAP_KIND_COLORS[d.kind] || "#777";
    minimapCtx.fillRect(
      Math.round(b.x1 * sx), Math.round(b.y1 * sy),
      Math.max(1, Math.round((b.x2 - b.x1) * sx)), Math.max(1, Math.round((b.y2 - b.y1) * sy))
    );
  }

  for (const a of getAgents()) {
    minimapCtx.fillStyle = a.color || "#fff";
    minimapCtx.fillRect(Math.round(a.x * sx) - 1, Math.round(a.y * sy) - 1, 2, 2);
  }

  // Viewport outline: which part of the (now much larger) world canvasWrap's
  // scroll position is currently showing. scrollLeft/Top/clientWidth/Height
  // are in post-zoom CSS pixels, so divide by zoomLevel to get back to
  // logical world coordinates before applying the minimap's own sx/sy scale.
  minimapCtx.strokeStyle = "rgba(255, 255, 255, 0.85)";
  minimapCtx.lineWidth = 1;
  minimapCtx.strokeRect(
    Math.round((canvasWrapEl.scrollLeft / zoomLevel) * sx) + 0.5,
    Math.round((canvasWrapEl.scrollTop / zoomLevel) * sy) + 0.5,
    Math.max(1, Math.round((canvasWrapEl.clientWidth / zoomLevel) * sx)),
    Math.max(1, Math.round((canvasWrapEl.clientHeight / zoomLevel) * sy))
  );
}

// Click or drag anywhere on the minimap to jump the main viewport there.
function minimapToWorld(clientX, clientY) {
  const rect = minimapCanvas.getBoundingClientRect();
  const sx = WORLD_W / minimapCanvas.width, sy = WORLD_H / minimapCanvas.height;
  const mx = ((clientX - rect.left) / rect.width) * minimapCanvas.width;
  const my = ((clientY - rect.top) / rect.height) * minimapCanvas.height;
  return { x: mx * sx, y: my * sy };
}

function centerViewportOn(worldX, worldY) {
  canvasWrapEl.scrollLeft = worldX * zoomLevel - canvasWrapEl.clientWidth / 2;
  canvasWrapEl.scrollTop = worldY * zoomLevel - canvasWrapEl.clientHeight / 2;
}

let minimapDragging = false;

function navigateFromMinimapEvent(event) {
  const { x, y } = minimapToWorld(event.clientX, event.clientY);
  centerViewportOn(x, y);
}

minimapCanvas.addEventListener("mousedown", (event) => {
  minimapDragging = true;
  navigateFromMinimapEvent(event);
  event.preventDefault();
});
window.addEventListener("mousemove", (event) => {
  if (minimapDragging) navigateFromMinimapEvent(event);
});
// Listen on window (not just the small minimap) so dragging keeps working
// even if the cursor briefly overshoots the minimap's bounds.
window.addEventListener("mouseup", () => { minimapDragging = false; });

// =====================================================================
// State polling (~10 Hz). On failure we keep the last frame and flip the
// status to "disconnected" so the page never goes blank; the sim itself keeps
// running server-side regardless of the browser.
// =====================================================================
const STATE_POLL_MS = 100;
let polling = false;

let STRUCTURE_UPGRADES_ENABLED = true;
// Sovereign God mode Phase 7: the ONLY thing gating the entire Divine Console
// (DOM visibility, fetches, banner) -- mirrors config.flags.GOD_MODE_ENABLED,
// always echoed by /state regardless of the flag's value (see
// specs/01-architecture.md flag index). false until the first /state poll.
let GOD_MODE_ENABLED_FLAG = false;
// Mirrors config.flags.GOD_AUTH_REQUIRED (default false = open Divine Console).
let GOD_AUTH_REQUIRED_FLAG = false;

function applyFlags(flags) {
  if (!flags) return;
  if ("SURVIVAL_ENABLED" in flags) SURVIVAL_ENABLED = !!flags.SURVIVAL_ENABLED;
  if ("STRUCTURE_UPGRADES_ENABLED" in flags) STRUCTURE_UPGRADES_ENABLED = !!flags.STRUCTURE_UPGRADES_ENABLED;
  if ("CRAFTING_ENABLED" in flags) CRAFTING_ENABLED = !!flags.CRAFTING_ENABLED;
  if ("MEMES_ENABLED" in flags) MEMES_ENABLED = !!flags.MEMES_ENABLED;
  if ("RULES_ENABLED" in flags) RULES_ENABLED = !!flags.RULES_ENABLED;
  if ("MEMORY_ENABLED" in flags) MEMORY_ENABLED = !!flags.MEMORY_ENABLED;
  if ("BENCHMARKS_ENABLED" in flags) BENCHMARKS_ENABLED = !!flags.BENCHMARKS_ENABLED;
  if ("ECOLOGY_ENABLED" in flags) ECOLOGY_ENABLED = !!flags.ECOLOGY_ENABLED;
  if ("PIANO_MODULES" in flags) PIANO_MODULES = !!flags.PIANO_MODULES;
  if ("META_SYSTEM" in flags) META_SYSTEM = !!flags.META_SYSTEM;
  if ("PATH1_ENABLED" in flags) PATH1_ENABLED = !!flags.PATH1_ENABLED;
  if ("STRUCTURE_WEAR_ENABLED" in flags) STRUCTURE_WEAR_ENABLED = !!flags.STRUCTURE_WEAR_ENABLED;
  if ("ACTIVITY_CUES_ENABLED" in flags) ACTIVITY_CUES_ENABLED = !!flags.ACTIVITY_CUES_ENABLED;
  if ("SOCIAL_LAYER_ENABLED" in flags) SOCIAL_LAYER_ENABLED = !!flags.SOCIAL_LAYER_ENABLED;
  if ("CHRONICLE_ENABLED" in flags) CHRONICLE_ENABLED = !!flags.CHRONICLE_ENABLED;
  if ("FOUNDING_EVENTS_ENABLED" in flags) FOUNDING_EVENTS_ENABLED = !!flags.FOUNDING_EVENTS_ENABLED;
  if ("ENV_EFFECTS_ENABLED" in flags) ENV_EFFECTS_ENABLED = !!flags.ENV_EFFECTS_ENABLED;
  if ("WORLD_CLOCK_HUD_ENABLED" in flags) WORLD_CLOCK_HUD_ENABLED = !!flags.WORLD_CLOCK_HUD_ENABLED;
  if ("SEASONAL_AGENTS_ENABLED" in flags) SEASONAL_AGENTS_ENABLED = !!flags.SEASONAL_AGENTS_ENABLED;
  if ("CROP_GROWTH_ENABLED" in flags) CROP_GROWTH_ENABLED = !!flags.CROP_GROWTH_ENABLED;
  if ("WILDLIFE_ENABLED" in flags) WILDLIFE_ENABLED = !!flags.WILDLIFE_ENABLED;
  if ("CARAVAN_VISUALS_ENABLED" in flags) CARAVAN_VISUALS_ENABLED = !!flags.CARAVAN_VISUALS_ENABLED;
  if ("WEATHER_ENABLED" in flags) WEATHER_ENABLED = !!flags.WEATHER_ENABLED;
  if ("GOD_MODE_ENABLED" in flags) GOD_MODE_ENABLED_FLAG = !!flags.GOD_MODE_ENABLED;
  if ("GOD_AUTH_REQUIRED" in flags) GOD_AUTH_REQUIRED_FLAG = !!flags.GOD_AUTH_REQUIRED;
  const dejaVuEl = document.getElementById("godDejaVuFieldset");
  if (dejaVuEl) dejaVuEl.disabled = !flags.GOD_DEJA_VU_REPLAY;
}

// Bounded viewer-only social pass. The engine has already filtered and
// deduplicated `socialTies`; this just culls to visible, nearby living pairs.
const SOCIAL_TIE_RADIUS = 180;
const SOCIAL_TIE_CAP = 36;
function drawSocialTies(ctx) {
  if (!SOCIAL_LAYER_ENABLED) return;
  const ties = world.socialTies || [];
  if (!ties.length) return;
  const agentsById = new Map(getLivingAgents().map((agent) => [agent.id, agent]));
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  let drawn = 0;
  for (const tie of ties) {
    if (drawn >= SOCIAL_TIE_CAP) break;
    const from = agentsById.get(tie.from);
    const to = agentsById.get(tie.to);
    if (!from || !to || from.x < left || from.x > right || from.y < top || from.y > bottom
        || to.x < left || to.x > right || to.y < top || to.y > bottom) continue;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const distance = Math.hypot(dx, dy);
    if (distance > SOCIAL_TIE_RADIUS) continue;
    const alpha = Math.max(0.16, 0.58 * (1 - distance / SOCIAL_TIE_RADIUS));
    ctx.strokeStyle = tie.valence === "rival"
      ? `rgba(105, 183, 234, ${alpha})`
      : `rgba(242, 164, 93, ${alpha})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y - 14);
    ctx.lineTo(to.x, to.y - 14);
    ctx.stroke();
    drawn++;
  }
}

// Ambient wildlife (WILDLIFE_ENABLED): server-authoritative huntable fauna.
// Positions/kinds come from world.wildlife (alive creatures only); the viewer
// does not spawn or pathfind. Cosmetic bob from frameTick; drawn every tick
// (not baked into terrainCanvas).
function drawWildlife(ctx, frameTick) {
  if (!WILDLIFE_ENABLED) return;
  const fauna = world.wildlife;
  if (!fauna || !fauna.length) return;
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  for (let i = 0; i < fauna.length; i++) {
    const creature = fauna[i];
    const x = creature.x;
    const y = creature.y;
    if (x < left || x > right || y < top || y > bottom) continue;
    const bob = Math.sin((frameTick + i * 17) / 20) * 3;
    drawWildlifeCreature(ctx, creature.kind, x, y + bob, frameTick);
  }
}

// Goods-in-motion (Phase 3 living-ecosystem, CARAVAN_VISUALS_ENABLED). The
// engine emits a shipment AFTER a transfer already completed and moved the
// authoritative resource counts -- this pass never touches simulation
// state, it only interpolates the already-resolved road-graph `path` the
// server embedded (same helper agent travel uses, _road_path_between_
// districts) between startFrame/endFrame. Route resolution failures are
// handled server-side (no shipment emitted at all); the viewer only ever
// sees valid, drawable records. Viewport-culled + capped, same pattern as
// drawSocialTies/drawWildlife.
const SHIPMENT_DRAW_CAP = 8;
function shipmentPosition(shipment, frameTick) {
  const path = shipment.path;
  if (!path || path.length < 2) return null;
  const span = shipment.endFrame - shipment.startFrame;
  const t = span > 0 ? (frameTick - shipment.startFrame) / span : 1;
  const clamped = Math.max(0, Math.min(1, t));
  const segCount = path.length - 1;
  const scaled = clamped * segCount;
  const segIndex = Math.min(segCount - 1, Math.floor(scaled));
  const segT = scaled - segIndex;
  const a = path[segIndex];
  const b = path[segIndex + 1];
  return { x: a.x + (b.x - a.x) * segT, y: a.y + (b.y - a.y) * segT };
}

function drawShipments(ctx, frameTick) {
  if (!CARAVAN_VISUALS_ENABLED) return;
  const shipments = world.shipments || [];
  if (!shipments.length) return;
  const reg = resourceRegistry();
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  let drawn = 0;
  for (const shipment of shipments) {
    if (drawn >= SHIPMENT_DRAW_CAP) break;
    if (frameTick < shipment.startFrame || frameTick > shipment.endFrame) continue;
    const pos = shipmentPosition(shipment, frameTick);
    if (!pos) continue;
    if (pos.x < left || pos.x > right || pos.y < top || pos.y > bottom) continue;
    const cargoColor = (reg[shipment.resource] && reg[shipment.resource].color) || null;
    drawShipment(ctx, shipment.mode, pos.x, pos.y, cargoColor);
    drawn++;
  }
}

async function pollState() {
  if (polling) return;
  polling = true;
  try {
    const res = await fetch("/state", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const snapshot = await res.json();
    world = snapshot;
    setSpriteSeason(snapshot.calendar && snapshot.calendar.season);
    // Season changed since the terrain cache was last tinted: rebuild it
    // (same lazy invalidate-and-reschedule mechanism pollDistricts() uses).
    const season = snapshot.calendar && snapshot.calendar.season;
    if (season && season !== lastSeasonRendered && terrainCanvas) {
      terrainCanvas = null;
      terrainBuildScheduled = false;
      scheduleTerrainCacheBuild();
    }
    // Living-ecosystem Phase 2: a district's ecology stage changed since the
    // terrain cache was last built -- rebuild exactly once, same mechanism
    // as the season check above. No-op (both keys stay null) when
    // CROP_GROWTH_ENABLED is off or the server hasn't sent districtEcology.
    if (terrainCanvas && !terrainBuildScheduled) {
      const [, ecologyStageKey] = ecologyStagesForTerrain();
      if (ecologyStageKey !== lastEcologyStageKeyRendered) {
        terrainCanvas = null;
        terrainBuildScheduled = false;
        scheduleTerrainCacheBuild();
      }
    }
    applyFlags(snapshot.config && snapshot.config.flags);
    setSeasonalAgentAccentsEnabled(SEASONAL_AGENTS_ENABLED);
    if (terrainCanvas && !terrainBuildScheduled) {
      const [, ecologyStageKey] = ecologyStagesForTerrain();
      const seasonNow = snapshot.calendar && snapshot.calendar.season;
      const visualKey = terrainVisualCacheKey(seasonNow, ecologyStageKey);
      if (visualKey !== lastTerrainVisualKeyRendered) {
        terrainCanvas = null;
        terrainBuildScheduled = false;
        scheduleTerrainCacheBuild();
      }
    }
    renderDailyCouncil((snapshot.civilization || {}).dailyCouncil);
    syncPauseButton();
    if (terrainCanvas) hideWorldLoading();
  } catch (err) {
    // Keep last frame; surface a disconnected status (but not over a real
    // server status we already have — only mark disconnected on fetch failure).
    if (world && world.lmStatus !== "disconnected") {
      world = Object.assign({}, world, { lmStatus: "disconnected" });
    }
  } finally {
    polling = false;
  }
}

// =====================================================================
// Controls — Pause / Resume / Reset are server-side now.
// =====================================================================
const pauseBtn = document.getElementById("pauseBtn");

function syncPauseButton() {
  pauseBtn.textContent = world.paused ? "Resume" : "Pause";
}

async function postControl(path, body) {
  try {
    return await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
  } catch (err) { /* ignore; next poll reflects real state */ }
  return null;
}

pauseBtn.addEventListener("click", async () => {
  const wantPause = !world.paused;
  await postControl(wantPause ? "/control/pause" : "/control/resume");
  // Optimistic flip; reconciled by the next /state poll.
  world.paused = wantPause;
  syncPauseButton();
  pollState();
});

const resetBtn = document.getElementById("resetBtn");
resetBtn.title = "Requires password (SIM_RESET_PASSWORD)";
function doReset() {
  if (!window.confirm("Reset the simulation? This restarts the village.")) return;
  const password = window.prompt("Type the reset password to wipe the world:");
  if (password === null || password === "") return;
  postControl("/control/reset", { password }).then(async (res) => {
    if (res && res.status === 401) {
      window.alert("Reset refused — wrong password (SIM_RESET_PASSWORD).");
      return;
    }
    pollState();
  });
}
resetBtn.addEventListener("click", doReset);

// Keyboard shortcut (R) kept for convenience alongside the visible button.
document.addEventListener("keydown", (e) => {
  if (e.key === "r" || e.key === "R") doReset();
});

// (No tab-hidden warning anymore: the legacy client sim paused when the tab
// was hidden, but the server-authoritative engine keeps running regardless —
// a background tab merely stops rendering until it's visible again.)

// =====================================================================
// Render loop — pure draw from `world`, decoupled from polling so we stay at
// 60fps even when the network poll is slower (~10 Hz).
// =====================================================================
let renderFrame = 0;

function tick() {
  // Defense-in-depth: an uncaught exception inside a requestAnimationFrame
  // callback silently kills the rAF chain forever (no more frames ever
  // render, with no visible console surface unless devtools happens to be
  // open at that exact moment) while pollState()'s independent setInterval
  // keeps fetching /state in the background. Catching here means a future
  // rendering bug degrades to "one skipped frame + a logged error" instead
  // of "the viewer is frozen until the page is manually reloaded."
  try {
    tickBody();
  } catch (err) {
    console.error("tick() render error (frame skipped, loop continues):", err);
  } finally {
    requestAnimationFrame(tick);
  }
}

function tickBody() {
  renderFrame++;
  ctx.clearRect(0, 0, WORLD_W, WORLD_H);
  drawWorld(ctx, renderFrame);
  drawSocialTies(ctx);
  drawWildlife(ctx, renderFrame);
  drawShipments(ctx, world.frameTick);

  const agents = getAgents();
  // Derive facing/walk direction from last frame's positions (the server snapshot
  // has no targetX/targetY; drawAgentSprite reads them for the walk cycle).
  for (const a of agents) {
    const p = prevPos[a.id != null ? a.id : a.name] || { x: a.x, y: a.y };
    a.targetX = a.x + (a.x - p.x);
    a.targetY = a.y + (a.y - p.y);
    prevPos[a.id != null ? a.id : a.name] = { x: a.x, y: a.y };
  }

  const structures = getCiv().structures || [];
  const drawList = [
    ...structures.map((structure) => ({ type: "structure", y: structure.y + 34, structure })),
    ...agents.map((agent) => ({ type: "agent", y: agent.y, agent }))
  ].sort((a, b) => a.y - b.y);
  for (const item of drawList) {
    if (item.type === "structure") drawStructureWithShadow(item.structure);
    else drawAgent(ctx, item.agent, renderFrame);
  }
  if (ACTIVITY_CUES_ENABLED) {
    // Pure frame-derived accents: no activity state is retained in the viewer.
    for (const structure of structures) drawStructureSmoke(ctx, structure, renderFrame);
    for (const agent of agents) drawActivityDust(ctx, agent, renderFrame);
  }

  // Server-derived decoration only: boats remain resources, not entities.
  for (const prop of (getCiv().physicalProps || [])) {
    if (prop.resource !== "boat") continue;
    const moorings = [[58, 240], [112, 470], [42, 690]];
    for (let i = 0; i < Math.min(prop.count, moorings.length); i++) {
      const [x, y] = moorings[i];
      // Large moored sailboat in the expanded ocean, facing the beach.
      ctx.fillStyle = "rgba(8, 31, 57, 0.28)";
      ctx.fillRect(x - 4, y + 48, 128, 8);
      ctx.fillStyle = "#4A2714";
      ctx.beginPath();
      ctx.moveTo(x, y + 30); ctx.lineTo(x + 118, y + 30);
      ctx.lineTo(x + 94, y + 54); ctx.lineTo(x + 18, y + 54);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#B96D32";
      ctx.fillRect(x + 13, y + 28, 84, 8);
      ctx.fillStyle = "#5A321B";
      ctx.fillRect(x + 57, y - 78, 6, 108);
      ctx.fillStyle = "#F4E2B5";
      ctx.beginPath();
      ctx.moveTo(x + 65, y - 72); ctx.lineTo(x + 108, y + 14); ctx.lineTo(x + 65, y + 14);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#D8C08B";
      ctx.beginPath();
      ctx.moveTo(x + 55, y - 52); ctx.lineTo(x + 22, y + 14); ctx.lineTo(x + 55, y + 14);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#F7C948";
      ctx.fillRect(x + 21, y + 18, 12, 7);
    }
  }

  const na = nightAlpha(world.calendar);
  if (na > 0.35) {
    const prevOp = ctx.globalCompositeOperation;
    ctx.globalCompositeOperation = "saturation";
    ctx.fillStyle = `rgba(128, 128, 128, ${NIGHT_DESAT_ALPHA.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
    ctx.globalCompositeOperation = prevOp;
  }
  if (na > 0) {
    ctx.fillStyle = `rgba(10, 16, 48, ${na.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  }
  // Weather sky tint (WEATHER_ENABLED): same full-canvas overlay stage as
  // the night overlay above, alpha clamped against it (weatherSkyAlpha) so
  // the two combine coherently instead of stacking into an unreadable
  // screen -- see the worst case (winter storm, deep night, unlit
  // district) verified in the Phase 4 report.
  const wa = weatherSkyAlpha(world.weather, na);
  if (wa > 0) {
    ctx.fillStyle = `rgba(${WEATHER_SKY_COLOR}, ${wa.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  }
  drawLightningFlash(ctx, renderFrame, world.weather);
  drawGoldenHourOverlay(ctx, world.calendar);
  drawLightGlows(world.calendar);
  drawDistrictStormVeil(ctx, world.weather);
  drawWeatherParticles(ctx, world.frameTick, world.weather, world.calendar);

  // C5 follow-cam: re-center every frame on the followed agent while active;
  // release the lock once they're deselected or no longer living.
  if (followAgentId != null) {
    if (selectedAgentId !== followAgentId) {
      followAgentId = null;
    } else {
      const followed = getLivingAgents().find((a) => a.id === followAgentId);
      if (!followed) followAgentId = null;
      else centerCameraOnAgent(followed);
    }
  }

  renderSidebar();
  renderWorldClockHud();
  drawCouncilAssemblyTable((world.civilization || {}).dailyCouncil, renderFrame);
  renderMinimap();
}

// =====================================================================
// Divine Console (Sovereign God mode Phase 7, docs/plan-sovereign-god-mode-
// v2.md "Viewer: Divine Console"). Strictly additive and feature-gated on
// GOD_MODE_ENABLED_FLAG (mirrors config.flags.GOD_MODE_ENABLED from /state,
// see applyFlags above): every function below is a no-op, and the panel/
// banner stay display:none, unless the flag is true. No new poll loop is
// added -- this reuses the existing pollState()->renderSidebar() cadence for
// the passive History/Laws/banner projections, and only fetches
// /control/god/* on an explicit user click (Connect/Preview/Apply/Cancel/
// Refresh).
//
// Security contract (docs/plan "Feature gate and security contract" +
// "Stored-content safety"):
//   - the token lives in the `godToken` variable only -- never localStorage,
//     optionally sessionStorage behind the explicit "remember" checkbox;
//   - every dynamic string reaching innerHTML goes through escapeHtml();
//     normalizedCommand is NEVER rendered raw -- previews/results are
//     rebuilt field-by-field from known, typed response keys;
//   - a 401 from any /control/god/* call clears the in-memory token and
//     re-locks the console (godApiFetch below);
//   - Apply always sends only {previewId, requestId} -- see wireDivineForm.
// =====================================================================

let godToken = null;               // in-memory ONLY
let godAuthorized = false;
let godCapabilities = null;        // last /control/god/capabilities response
let godLastSight = null;           // last /control/god/sight response
let godActiveTab = "unlock";
let godLastStateKey = null;        // change-detect for world.god (History/Laws/banner)
let godSeenInterventionIds = null; // Set of ids already shown in the public banner (edge-detected, like foundingFramesSeen)
let godBannerTimer = null;

const godTokenInput = document.getElementById("godTokenInput");
const godRememberCheckbox = document.getElementById("godRememberCheckbox");
const godConnectBtn = document.getElementById("godConnectBtn");
const godAuthStatusEl = document.getElementById("godAuthStatus");
const divineBarEl = document.getElementById("divineBar");
const divineBarBrandStateEl = document.getElementById("divineBarBrandState");
const divineBarInterventionCountEl = document.getElementById("divineBarInterventionCount");
const divinePreviewStripEl = document.getElementById("divinePreviewStrip");
const divinePreviewStripLabelEl = document.getElementById("divinePreviewStripLabel");
const divinePreviewApplyBtnEl = document.getElementById("divinePreviewApplyBtn");
const divinePreviewDiscardBtnEl = document.getElementById("divinePreviewDiscardBtn");
const divineUnlockPipEl = document.getElementById("divineUnlockPip");
const divineModalScrimEl = document.getElementById("divineModalScrim");
const divineModalBodyEl = document.getElementById("divineModalBody");
const divineModalIconEl = document.getElementById("divineModalIcon");
const divineModalTitleEl = document.getElementById("divineModalTitle");
const divineModalSubEl = document.getElementById("divineModalSub");
const divineTabHoldEl = document.getElementById("divineTabHold");
const divineTooltipEl = document.getElementById("tooltip");
const godPublicBannerEl = document.getElementById("godPublicBanner");
const godHistoryListEl = document.getElementById("godHistoryList");
const godLawsActiveEl = document.getElementById("godLawsActive");
const godSightOutputEl = document.getElementById("godSightOutput");
const godSightAgentSelectEl = document.getElementById("godSightAgentSelect");

const DIVINE_FEATURE_ICONS = {
  unlock:  '<svg viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2"/></svg>',
  sight:   '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.5"/></svg>',
  voice:   '<svg viewBox="0 0 24 24"><path d="M4 8h9l5-3v14l-5-3H4z"/><path d="M20 9a4 4 0 0 1 0 6"/></svg>',
  matrix:  '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v16"/></svg>',
  miracles:'<svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.4L18 9l-4.2 1.6L12 15l-1.8-4.4L6 9l4.2-1.6z"/><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"/></svg>',
  story:   '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h6v18H6a2 2 0 0 1-2-2z"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></svg>',
  laws:    '<svg viewBox="0 0 24 24"><path d="M12 3v18M5 7h14M7 7l-3 6h6zM17 7l3 6h-6z"/></svg>',
  history: '<svg viewBox="0 0 24 24"><path d="M3 5h14v14H3z"/><path d="M7 9h6M7 13h6M7 5v14"/></svg>',
  compile: '<svg viewBox="0 0 24 24"><path d="M6 20l9-14M15 6l3 1-1 3M9 20l-3-1 1-3"/></svg>',
};
const DIVINE_FEATURES = {
  unlock:  { title: "Unlock the Divine Console", sub: "Authenticate with the God token to enable every other tool.", gated: false },
  sight:   { title: "Sight — private inspection", sub: "See what agents never expose publicly: vitals, ties, omens, active effects.", gated: true },
  voice:   { title: "Voice — proclamations & omens", sub: "Speak to the village, or whisper a private omen to one agent.", gated: true },
  matrix:  { title: "Matrix — brain & world interventions", sub: "Override agent brains, memories, perception, possession, identity, zones, and checkpoints — mostly private; Preview validates, Apply commits.", gated: true },
  miracles:{ title: "Miracles — direct intervention", sub: "Heal, grant resources, or repair/damage a structure. Irreversible once applied.", gated: true },
  story:   { title: "Story — timed narrative events", sub: "Compose a titled event from validated effect primitives.", gated: true },
  laws:    { title: "Laws — temporary world modifiers", sub: "Bend gather yield, hunger, spoilage and more for a bounded time.", gated: true },
  history: { title: "History — intervention log", sub: "Every applied intervention, public and private, most recent first.", gated: true },
  compile: { title: "Compile — prose to event", sub: "Turn narrative prose into a typed draft. Dark until contention is measured.", gated: true },
};

function godFramesToSeconds(frames) {
  return Math.round((Number(frames) || 0) / 30);
}

// Both simulation time AND raw frames, per docs/plan UX requirement.
function godDurationLabel(frames) {
  return `${godFramesToSeconds(frames)}s (${Math.round(Number(frames) || 0)}f)`;
}

function godCountdownLabel(expiresFrame) {
  const now = world.frameTick || 0;
  const remaining = Math.max(0, (Number(expiresFrame) || 0) - now);
  return remaining > 0 ? `expires in ${godDurationLabel(remaining)}` : "expired (awaiting cleanup)";
}

function godAgentOptionsHtml(selectedId) {
  return getLivingAgents().map((a) =>
    `<option value="${a.id}"${a.id === selectedId ? " selected" : ""}>${escapeHtml(a.name)} (#${a.id}, ${escapeHtml(a.role)})</option>`
  ).join("") || `<option value="">(no living agents)</option>`;
}

function populateGodAgentSelects() {
  const html = godAgentOptionsHtml(null);
  [godSightAgentSelectEl, document.getElementById("godOmenAgentSelect"),
   document.getElementById("godVitalsAgentSelect"), document.getElementById("godGrantAgentSelect"),
   document.getElementById("godStoryTargetSelect"),
   document.getElementById("godSamplingAgentSelect"),
   document.getElementById("godSamplingRevokeAgentSelect"),
   document.getElementById("godMemoryInsertAgentSelect"),
   document.getElementById("godMemoryDeleteAgentSelect"),
   document.getElementById("godBeliefPlantAgentSelect"),
   document.getElementById("godDistortionAgentSelect"),
   document.getElementById("godCompulsionAgentSelect"),
   document.getElementById("godVetoArmAgentSelect"),
   document.getElementById("godVetoResolveAgentSelect"),
   document.getElementById("godPossessionAgentSelect"),
   document.getElementById("godGateRevokeAgentSelect"),
   document.getElementById("godBurningBushAgentSelect"),
   document.getElementById("godBurningBushCloseAgentSelect"),
   document.getElementById("godBargainAgentSelect"),
   document.getElementById("godBargainSettleAgentSelect"),
   document.getElementById("godAnointAgentSelect"),
   document.getElementById("godAnointRevokeAgentSelect"),
   document.getElementById("godIdentityEditAgentSelect"),
   document.getElementById("godIdentityCopyTargetSelect"),
   document.getElementById("godIdentityCopySourceSelect"),
   document.getElementById("godIdentityCancelAgentSelect")].forEach((el) => {
    if (!el) return;
    const prior = el.value;
    el.innerHTML = html;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
  const resourceSelect = document.getElementById("godGrantResourceSelect");
  if (resourceSelect) {
    const reg = resourceRegistry();
    resourceSelect.innerHTML = Object.keys(reg).map((id) =>
      `<option value="${escapeHtml(id)}">${escapeHtml(reg[id].label || id)}</option>`
    ).join("");
  }
  const structureSelect = document.getElementById("godStructureSelect");
  if (structureSelect) {
    const structures = (getCiv().structures || []).filter((s) => !s.isRuin && (s.condition == null || s.condition > 0));
    structureSelect.innerHTML = structures.map((s) =>
      `<option value="${s.id}">${escapeHtml(s.name || s.type)} (#${s.id})</option>`
    ).join("") || `<option value="">(no structures)</option>`;
  }
  const districtOptions = getDistricts().map((d) =>
    `<option value="${escapeHtml(d.id)}">${escapeHtml(d.label || d.id)}</option>`
  ).join("") || `<option value="">(no districts)</option>`;
  ["godMassRepairDistrict", "godClearRuinsDistrict", "godArchitectDistrict"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const prior = el.value;
    el.innerHTML = districtOptions;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
  ["godArchitectGrantKeyAgents", "godArchitectHoldAgents", "godArchitectReleaseAgents"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const selected = new Set(Array.from(el.selectedOptions || []).map((o) => o.value));
    el.innerHTML = getLivingAgents().map((a) =>
      `<option value="${a.id}">${escapeHtml(a.name)} (#${a.id})</option>`
    ).join("") || "";
    Array.from(el.options).forEach((o) => { o.selected = selected.has(o.value); });
  });
  const whisperRows = document.querySelectorAll("#godWhisperRows .god-whisper-row");
  whisperRows.forEach((row) => {
    const sel = row.querySelector(".god-whisper-agent");
    if (!sel) return;
    const prior = sel.value;
    const priorId = prior ? parseInt(prior, 10) : null;
    sel.innerHTML = godAgentOptionsHtml(priorId);
    if (prior && Array.from(sel.options).some((o) => o.value === prior)) sel.value = prior;
  });
  if (!whisperRows.length) initGodWhisperRows();
  populateGodPinActionSelects();
}

const GOD_PIN_ACTIONS = [
  "rest", "collect_resource", "contribute_resources", "build_structure",
  "talk_to_nearby", "move_to_district", "move_to_agent", "craft_item", "heal_agent",
];

function populateGodPinActionSelects() {
  const html = GOD_PIN_ACTIONS.map((a) =>
    `<option value="${escapeHtml(a)}">${escapeHtml(actionLabel(a))}</option>`
  ).join("");
  ["godCompulsionAction", "godPossessionAction", "godVetoRewriteAction"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const prior = el.value;
    el.innerHTML = html;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
}

// --- Auth / fetch plumbing --------------------------------------------
async function godApiFetch(path, opts) {
  opts = opts || {};
  const headers = {};
  if (godToken) headers["X-God-Token"] = godToken;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    return { ok: false, status: 0, data: { ok: false, reason: "network error" } };
  }
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (res.status === 401) {
    godLockConsole("Authorization failed — token cleared. Re-enter it to continue.");
  }
  return { ok: res.ok, status: res.status, data: data || { ok: false, reason: `HTTP ${res.status}` } };
}

function updateGodAuthStatus(text, cls) {
  godAuthStatusEl.textContent = text;
  godAuthStatusEl.className = "divine-status" + (cls ? " " + cls : "");
}

function godEffectivelyAuthorized() {
  return godAuthorized || !GOD_AUTH_REQUIRED_FLAG;
}

function godDefaultDayFrames() {
  const cfg = world.config || {};
  if (cfg.DAY_FRAMES != null) return Number(cfg.DAY_FRAMES) || 18000;
  return 18000;
}

function godInterventionCount() {
  const pub = (world.god && world.god.recentPublicInterventions) || [];
  if (pub.length) return pub.length;
  const gs = (getCiv().godState && getCiv().godState.recentInterventions) || [];
  return gs.length;
}

let divinePreviewController = null;
let divinePreviewOwnerForm = null;
let godLastAppliedPin = null;

function clearDivinePreviewStrip() {
  divinePreviewController = null;
  divinePreviewOwnerForm = null;
  if (divinePreviewStripEl) divinePreviewStripEl.classList.remove("visible");
}

function showDivinePreviewStrip(label, data, applyFn, discardFn) {
  if (!divinePreviewStripEl) return;
  divinePreviewController = { label, data, applyFn, discardFn };
  if (divinePreviewStripLabelEl) {
    const kind = data.normalizedCommand && data.normalizedCommand.kind;
    divinePreviewStripLabelEl.innerHTML =
      godReversibilityBadge(data.reversibilityClass) +
      `<span>${escapeHtml(label || kind || "command")} — preview ready</span>`;
  }
  divinePreviewStripEl.classList.add("visible");
}

function renderGodPinRow() {
  if (!divineModalBodyEl || !godLastAppliedPin) return;
  let pin = document.getElementById("divinePinRow");
  if (!pin) {
    pin = document.createElement("div");
    pin.id = "divinePinRow";
    pin.className = "divine-pin-row";
    divineModalBodyEl.insertBefore(pin, divineModalBodyEl.firstChild);
  }
  const p = godLastAppliedPin;
  pin.innerHTML = `Last applied: <strong>${escapeHtml(p.label)}</strong> (id ${escapeHtml(String(p.id))}) — ` +
    `<a href="#" id="divinePinHistoryLink">View in History</a>`;
  const link = document.getElementById("divinePinHistoryLink");
  if (link) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openDivineModal("history");
    });
  }
}

if (divinePreviewApplyBtnEl) {
  divinePreviewApplyBtnEl.addEventListener("click", () => {
    if (divinePreviewController && divinePreviewController.applyFn) divinePreviewController.applyFn();
  });
}
if (divinePreviewDiscardBtnEl) {
  divinePreviewDiscardBtnEl.addEventListener("click", () => {
    if (divinePreviewController && divinePreviewController.discardFn) divinePreviewController.discardFn();
    clearDivinePreviewStrip();
  });
}

function updateDivineBarAuthUi() {
  if (divineBarBrandStateEl) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      divineBarBrandStateEl.textContent = "open";
      divineBarBrandStateEl.style.color = "#a5d6a7";
    } else {
      divineBarBrandStateEl.textContent = godAuthorized ? "authorized" : "locked";
      divineBarBrandStateEl.style.color = godAuthorized ? "#a5d6a7" : "#6f6f7c";
    }
  }
  const unlockBtn = divineBarEl && divineBarEl.querySelector(".gbtn.unlock");
  if (unlockBtn) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      unlockBtn.style.display = "none";
    } else {
      unlockBtn.style.display = "";
      unlockBtn.dataset.state = godAuthorized ? "unlocked" : "locked";
    }
  }
  if (divineUnlockPipEl) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      divineUnlockPipEl.style.display = "none";
      divineUnlockPipEl.textContent = "";
    } else {
      divineUnlockPipEl.style.display = "";
      divineUnlockPipEl.textContent = godAuthorized ? "ready" : "locked";
    }
  }
  if (divineBarInterventionCountEl) {
    if (GOD_MODE_ENABLED_FLAG) {
      const n = godInterventionCount();
      divineBarInterventionCountEl.textContent = n ? `${n} intervention${n === 1 ? "" : "s"}` : "no interventions yet";
      divineBarInterventionCountEl.style.display = "";
    } else {
      divineBarInterventionCountEl.textContent = "";
      divineBarInterventionCountEl.style.display = "none";
    }
  }
  if (!divineBarEl) return;
  const effective = godEffectivelyAuthorized();
  divineBarEl.querySelectorAll(".gbtn.locked-dependent").forEach((btn) => {
    // Compile stays dual-gated by capabilities display; still lock-disable when unauthorized.
    btn.disabled = !effective;
  });
}

function godLockConsole(message) {
  if (!GOD_AUTH_REQUIRED_FLAG) {
    console.warn(message || "godLockConsole ignored — Divine Console is in open mode (GOD_AUTH_REQUIRED off).");
    return;
  }
  godToken = null;
  godAuthorized = false;
  godCapabilities = null;
  godLastSight = null;
  try { sessionStorage.removeItem("godToken"); } catch (err) { /* ignore */ }
  updateGodAuthStatus(message || "Locked.", "divine-status-locked");
  updateDivineBarAuthUi();
  if (GOD_MODE_ENABLED_FLAG) openDivineModal("unlock");
  else {
    godActiveTab = "unlock";
    closeDivineModal();
  }
}

let godOpenModeBootstrapped = false;
async function godOpenModeBootstrap() {
  if (GOD_AUTH_REQUIRED_FLAG || godOpenModeBootstrapped || !GOD_MODE_ENABLED_FLAG) return;
  godOpenModeBootstrapped = true;
  godAuthorized = true;
  updateDivineBarAuthUi();
  const resp = await godApiFetch("/control/god/capabilities");
  if (resp.ok && resp.data && resp.data.ok) {
    godCapabilities = resp.data;
    applyGodCapabilitiesToForms();
    populateGodAgentSelects();
  }
}

async function godConnect(tokenValue) {
  if (!tokenValue) {
    updateGodAuthStatus("Enter a token first.", "divine-status-locked");
    return;
  }
  godToken = tokenValue;
  const resp = await godApiFetch("/control/god/capabilities");
  if (resp.ok && resp.data && resp.data.ok) {
    godAuthorized = true;
    godCapabilities = resp.data;
    updateGodAuthStatus("Authorized.", "divine-status-ok");
    updateDivineBarAuthUi();
    applyGodCapabilitiesToForms();
    populateGodAgentSelects();
    showGodTab("sight");
  } else {
    godToken = null;
    godAuthorized = false;
    updateDivineBarAuthUi();
    updateGodAuthStatus("Unauthorized — check the token (or god mode may be disabled).", "divine-status-locked");
  }
}

godConnectBtn.addEventListener("click", () => godConnect(godTokenInput.value.trim()));

// Apply capabilities-reported bounds/defaults to number/text inputs so the
// forms are driven by the server's allowlist rather than hardcoded twins.
function applyGodCapabilitiesToForms() {
  if (!godCapabilities) return;
  const kinds = godCapabilities.kinds || {};
  const setBounds = (id, field) => {
    const el = document.getElementById(id);
    if (!el || !field) return;
    if (field.min != null) el.min = field.min;
    if (field.max != null) el.max = field.max;
    if (field.default != null && !el.value) el.value = field.default;
    if (field.maxChars != null) el.maxLength = field.maxChars;
  };
  const prov = (kinds.providence || {}).payload || {};
  setBounds("godProvDuration", prov.durationFrames);
  setBounds("godProvText", prov.text);
  const omen = (kinds.private_omen || {}).payload || {};
  setBounds("godOmenDuration", omen.durationFrames);
  setBounds("godOmenText", omen.text);
  const proc = (kinds.proclamation || {}).payload || {};
  setBounds("godProcText", proc.text);
  const vitals = (kinds.agent_vitals || {}).payload || {};
  if (vitals.healthDelta) { setBounds("godVitalsHealth", vitals.healthDelta); }
  if (vitals.hungerDelta) { setBounds("godVitalsHunger", vitals.hungerDelta); }
  const grant = (kinds.grant_resource || {}).payload || {};
  setBounds("godGrantAmount", grant.amount);
  const structure = (kinds.structure_condition || {}).payload || {};
  setBounds("godStructureDelta", structure.delta);
  const story = (kinds.story_event || {}).payload || {};
  setBounds("godStoryTitle", story.title);
  setBounds("godStoryNarration", story.narration);
  setBounds("godStoryDuration", story.durationFrames);
  setBounds("godLawDuration", story.durationFrames);
  const sampling = (kinds.agent_sampling || {}).payload || {};
  setBounds("godSamplingDuration", sampling.durationFrames);
  if (sampling.temperature) {
    setBounds("godSamplingTemp", sampling.temperature);
    const tempEl = document.getElementById("godSamplingTemp");
    const outEl = document.getElementById("godSamplingTempOut");
    if (tempEl && outEl) outEl.textContent = tempEl.value;
  }
  if (sampling.top_p) setBounds("godSamplingTopP", sampling.top_p);
  if (sampling.top_k) setBounds("godSamplingTopK", sampling.top_k);
  if (sampling.min_p) setBounds("godSamplingMinP", sampling.min_p);
  renderGodModifierEditor("godStoryModifiers", "gs");
  renderGodModifierEditor("godLawModifiers", "gl");
  // Sovereign God mode Optional Phase 8: the Compile tab is dual-gated --
  // only shown when the server reports GOD_MODE_ENABLED AND the separate
  // GOD_COMPILER_ENABLED dark flag both on (capabilities.compiler.enabled
  // already folds both together server-side).
  const compiler = godCapabilities.compiler || {};
  const compileTabBtn = document.getElementById("godCompileTabBtn");
  if (compileTabBtn) compileTabBtn.style.display = compiler.enabled ? "" : "none";
  const proseEl = document.getElementById("godCompileProse");
  if (proseEl && compiler.promptMaxChars) proseEl.maxLength = compiler.promptMaxChars;
  godCompilerMinIntervalSec = compiler.minIntervalSec || 5;
  if (!compiler.enabled && godActiveTab === "compile") showGodTab("sight");
}

// --- sessionStorage "remember for this tab" (never localStorage) ------
if (GOD_AUTH_REQUIRED_FLAG) {
  (function restoreGodTokenFromSession() {
    try {
      const stored = sessionStorage.getItem("godToken");
      if (stored) {
        godTokenInput.value = stored;
        godRememberCheckbox.checked = true;
      }
    } catch (err) { /* ignore */ }
  })();
  godRememberCheckbox.addEventListener("change", () => {
    if (!godRememberCheckbox.checked) {
      try { sessionStorage.removeItem("godToken"); } catch (err) { /* ignore */ }
    } else if (godToken) {
      try { sessionStorage.setItem("godToken", godToken); } catch (err) { /* ignore */ }
    }
  });
  // Re-derive the "remember" persistence on every successful connect (not just
  // the change event) so a token typed AFTER checking the box is still saved.
  const _godConnectOriginal = godConnect;
  godConnect = async function (tokenValue) {
    await _godConnectOriginal(tokenValue);
    if (godAuthorized && godRememberCheckbox.checked) {
      try { sessionStorage.setItem("godToken", tokenValue); } catch (err) { /* ignore */ }
    }
  };
}

// --- Bottom bar + modal (relocated from sidebar tabs) --------------------
const GOD_TABS = ["unlock", "sight", "voice", "matrix", "miracles", "story", "laws", "history", "compile"];
const godBarButtons = Array.from(document.querySelectorAll("#divineBar .gbtn"));
let divineModalOpenFeature = null;

function closeDivineModal() {
  if (divineModalOpenFeature) {
    const panel = document.getElementById("divineTab-" + divineModalOpenFeature);
    if (panel && divineTabHoldEl) divineTabHoldEl.appendChild(panel);
    divineModalOpenFeature = null;
  }
  if (divineModalScrimEl) divineModalScrimEl.classList.remove("open");
  godBarButtons.forEach((btn) => btn.classList.remove("active"));
  hideDivineTip();
  clearDivinePreviewStrip();
}

function openDivineModal(name) {
  if (GOD_TABS.indexOf(name) === -1) name = "unlock";
  const feature = DIVINE_FEATURES[name] || DIVINE_FEATURES.unlock;
  // Reparent any currently open panel back to the hold first.
  if (divineModalOpenFeature && divineModalOpenFeature !== name) {
    const prev = document.getElementById("divineTab-" + divineModalOpenFeature);
    if (prev && divineTabHoldEl) {
      prev.style.display = "none";
      divineTabHoldEl.appendChild(prev);
    }
  }
  godActiveTab = name;
  godBarButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.feature === name));
  if (divineModalIconEl) divineModalIconEl.innerHTML = DIVINE_FEATURE_ICONS[name] || "";
  if (divineModalTitleEl) divineModalTitleEl.textContent = feature.title;
  if (divineModalSubEl) divineModalSubEl.textContent = feature.sub;
  const panel = document.getElementById("divineTab-" + name);
  if (panel && divineModalBodyEl) {
    divineModalBodyEl.appendChild(panel);
    panel.style.display = "";
  }
  // Hide siblings still in the hold (not the open panel).
  GOD_TABS.forEach((t) => {
    if (t === name) return;
    const el = document.getElementById("divineTab-" + t);
    if (el && el.parentElement === divineTabHoldEl) el.style.display = "none";
  });
  divineModalOpenFeature = name;
  if (divineModalScrimEl) divineModalScrimEl.classList.add("open");
  if (name === "sight" && godEffectivelyAuthorized()) refreshGodSight();
  if (name === "laws") renderGodLawsActive();
  if (name === "history") renderGodHistory();
  renderGodPinRow();
}

// Thin alias: existing callers (connect→sight, lock→unlock, compile→story) open the modal.
function showGodTab(name) {
  openDivineModal(name);
}

godBarButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    openDivineModal(btn.dataset.feature);
  });
});
const divineModalCloseBtn = document.getElementById("divineModalClose");
if (divineModalCloseBtn) divineModalCloseBtn.addEventListener("click", closeDivineModal);
if (divineModalScrimEl) {
  divineModalScrimEl.addEventListener("click", (e) => {
    if (e.target === divineModalScrimEl) closeDivineModal();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && divineModalScrimEl && divineModalScrimEl.classList.contains("open")) {
    closeDivineModal();
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter"
      && divineModalScrimEl && divineModalScrimEl.classList.contains("open")
      && divinePreviewController) {
    e.preventDefault();
    divinePreviewController.applyFn();
  }
});

// --- Shared tooltip engine (delegated; safe for late-rendered sight/history) ---
let divineTipHideTimer = null;
function hideDivineTip() {
  if (divineTooltipEl) divineTooltipEl.classList.remove("show");
}
function showDivineTip(el) {
  if (!divineTooltipEl || !el) return;
  let raw = el.getAttribute("data-tip");
  if (!raw) return;
  let data;
  try {
    data = JSON.parse(raw.replace(/&#39;/g, "'"));
  } catch (err) {
    return;
  }
  const title = data && data.t != null ? String(data.t) : "";
  const desc = data && data.d != null ? String(data.d) : "";
  divineTooltipEl.innerHTML = '<span class="t-title">' + escapeHtml(title) + "</span>" + escapeHtml(desc);
  divineTooltipEl.classList.add("show");
  const r = el.getBoundingClientRect();
  const tw = divineTooltipEl.offsetWidth;
  const th = divineTooltipEl.offsetHeight;
  let x = r.left + r.width / 2 - tw / 2;
  let y = r.top - th - 8;
  if (y < 8) y = r.bottom + 8;
  x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
  divineTooltipEl.style.left = x + "px";
  divineTooltipEl.style.top = y + "px";
}
document.addEventListener("mouseenter", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (!el) return;
  clearTimeout(divineTipHideTimer);
  showDivineTip(el);
}, true);
document.addEventListener("mouseleave", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (!el) return;
  divineTipHideTimer = setTimeout(hideDivineTip, 60);
}, true);
document.addEventListener("focusin", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (el) showDivineTip(el);
});
document.addEventListener("focusout", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (el) hideDivineTip();
});

if (!GOD_AUTH_REQUIRED_FLAG) godAuthorized = true;
updateDivineBarAuthUi();
["godVitalsFieldset", "godGrantFieldset", "godStructureFieldset", "godMassRepairFieldset", "godClearRuinsFieldset"]
  .forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("divine-fieldset-irreversible");
  });

// --- Preview -> Apply generic wiring ------------------------------------
// One reusable helper per mutating form (docs/plan: "no Apply before a
// successful preview; any field edit invalidates it"). `resultEl` renders
// via escapeHtml()-composed HTML fragments only -- normalizedCommand itself
// is never inserted into innerHTML (rule: stored-content safety).
function wireDivineForm(formSelector, opts) {
  const formEl = document.querySelector(formSelector);
  const previewBtn = document.getElementById(opts.previewBtnId);
  const applyBtn = document.getElementById(opts.applyBtnId);
  const resultEl = document.getElementById(opts.resultElId);
  let previewState = null;

  function invalidate() {
    previewState = null;
    applyBtn.disabled = true;
    if (divinePreviewOwnerForm === formEl) clearDivinePreviewStrip();
  }
  formEl.addEventListener("input", invalidate);
  formEl.addEventListener("change", invalidate);

  async function doApply() {
    if (!previewState) return;
    applyBtn.disabled = true;
    const requestId = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : ("req-" + Date.now() + "-" + Math.random().toString(36).slice(2));
    const resp = await godApiFetch("/control/god/apply", {
      method: "POST",
      body: { previewId: previewState.previewId, requestId },
    });
    previewState = null;
    clearDivinePreviewStrip();
    if (!resp.data || !resp.data.ok) {
      renderGodError(resultEl, (resp.data && resp.data.reason) || "apply failed");
      return;
    }
    resultEl.innerHTML = renderGodAppliedHtml(resp.data);
    godLastAppliedPin = {
      label: opts.label || (resp.data.outcome && resp.data.outcome.kind) || "intervention",
      id: resp.data.interventionId,
    };
    renderGodPinRow();
    if (opts.onApplied) opts.onApplied(resp.data);
    refreshGodSightIfOpen();
  }

  previewBtn.addEventListener("click", async () => {
    invalidate();
    const built = opts.buildEnvelope();
    if (!built || built.error) {
      renderGodError(resultEl, (built && built.error) || "invalid form input");
      return;
    }
    const resp = await godApiFetch("/control/god/preview", { method: "POST", body: built.envelope });
    if (!resp.data || !resp.data.ok) {
      renderGodError(resultEl, (resp.data && resp.data.reason) || "preview failed");
      if (opts.onPreviewRejected) opts.onPreviewRejected(resp.data && resp.data.reason);
      return;
    }
    previewState = resp.data;
    resultEl.innerHTML = renderGodPreviewHtml(resp.data, opts.label);
    applyBtn.disabled = false;
    divinePreviewOwnerForm = formEl;
    showDivinePreviewStrip(opts.label, resp.data, doApply, invalidate);
  });

  applyBtn.addEventListener("click", doApply);
}

function refreshGodSightIfOpen() {
  if (godActiveTab === "sight" && godEffectivelyAuthorized()) refreshGodSight();
  if (godActiveTab === "laws") renderGodLawsActive();
}

function renderGodError(el, message) {
  el.innerHTML = `<div class="divine-error">${escapeHtml(String(message))}</div>`;
}

function godReversibilityBadge(cls) {
  const norm = String(cls || "irreversible");
  const label = norm === "irreversible" ? "IRREVERSIBLE" : norm === "consequential" ? "CONSEQUENTIAL" : "CANCELLABLE";
  const css = norm === "irreversible" ? "divine-badge-irreversible"
    : norm === "consequential" ? "divine-badge-consequential" : "divine-badge-cancellable";
  return `<span class="divine-badge ${css}">${escapeHtml(label)}</span>`;
}

// Every value inserted below is either a known numeric/boolean field or run
// through escapeHtml() -- never the raw normalizedCommand object.
function renderGodPreviewOutcomeHtml(kind, outcome) {
  if (!outcome) return "";
  const rows = [];
  if (kind === "agent_vitals") {
    rows.push(`Target: ${escapeHtml(String(outcome.targetName))} (#${escapeHtml(String(outcome.targetId))})`);
    rows.push(`Health: ${escapeHtml(String(outcome.oldHealth))} &rarr; ${escapeHtml(String(outcome.newHealth))}`);
    rows.push(`Hunger: ${escapeHtml(String(outcome.oldHunger))} &rarr; ${escapeHtml(String(outcome.newHunger))}`);
  } else if (kind === "grant_resource") {
    rows.push(`Resource: ${escapeHtml(String(outcome.resourceId))} &times; ${escapeHtml(String(outcome.amount))}`);
    if (outcome.targetKind === "agent") {
      rows.push(`To ${escapeHtml(String(outcome.targetName))}: +${escapeHtml(String(outcome.agentAdded))} carried` +
        (outcome.stockpileAdded ? `, +${escapeHtml(String(outcome.stockpileAdded))} overflow to stockpile` : ""));
    } else {
      rows.push(`To village stockpile: +${escapeHtml(String(outcome.stockpileAdded))}`);
    }
  } else if (kind === "structure_condition") {
    rows.push(`Structure: ${escapeHtml(String(outcome.structureName))} (#${escapeHtml(String(outcome.structureId))})`);
    rows.push(`Condition: ${escapeHtml(String(outcome.oldCondition))} &rarr; ${escapeHtml(String(outcome.newCondition))}`);
    if (outcome.wouldBecomeRuin) rows.push(`<span class="divine-warning">This would reduce the structure to ruin.</span>`);
  } else if (kind === "story_event" || kind === "law") {
    const mods = outcome.modifiers || {};
    if (Object.keys(mods).length) {
      rows.push("Modifiers: " + Object.entries(mods).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", "));
    }
    if (outcome.customRuleContext && outcome.customRuleContext.length) {
      rows.push("Composes with active village custom rule(s) on gather: " +
        outcome.customRuleContext.map((r) => `${escapeHtml(r.ruleId)} (modifier ${escapeHtml(String(r.value))})`).join(", "));
    }
    if (outcome.primitives && outcome.primitives.length) {
      rows.push(`${outcome.primitives.length} immediate primitive effect(s) will apply atomically with this event.`);
    }
    if (outcome.providenceOutgoingId) {
      rows.push(`<span class="divine-warning">This will REPLACE the active providence (id ${escapeHtml(String(outcome.providenceOutgoingId))}).</span>`);
    }
  } else if (kind === "checkpoint_create") {
    rows.push(`Label: ${escapeHtml(String(outcome.label || ""))}`);
    rows.push(`Frame tick: ${escapeHtml(String(outcome.frameTick))}`);
    rows.push(`Checkpoints: ${escapeHtml(String(outcome.checkpointCount))}`);
    if (outcome.willReplaceOldest) {
      rows.push(`<span class="divine-warning">At cap — oldest checkpoint will be dropped.</span>`);
    }
  } else if (kind === "checkpoint_restore") {
    rows.push(`Checkpoint: ${escapeHtml(String(outcome.label || outcome.checkpointId || ""))}`);
    if (outcome.checkpointFrameTick != null) {
      rows.push(`Snapshot frame: ${escapeHtml(String(outcome.checkpointFrameTick))}`);
    }
    rows.push(`Current frame: ${escapeHtml(String(outcome.currentFrameTick))}`);
    if (outcome.irreversibleWarning) {
      rows.push(`<span class="divine-warning">${escapeHtml(String(outcome.irreversibleWarning))}</span>`);
    }
  }
  return rows.map((r) => `<div>${r}</div>`).join("");
}

function renderGodPreviewHtml(data, label) {
  let html = godReversibilityBadge(data.reversibilityClass);
  if (data.fingerprint && data.fingerprint.outgoingId) {
    html += `<div class="divine-warning">This will REPLACE the active ${escapeHtml(label || "guidance")} (id ${escapeHtml(String(data.fingerprint.outgoingId))}).</div>`;
  }
  const kind = data.normalizedCommand && data.normalizedCommand.kind;
  html += renderGodPreviewOutcomeHtml(kind, data.previewOutcome);
  const secsLeft = Math.max(0, Math.round((data.expiresAt || 0) - Date.now() / 1000));
  html += `<div class="divine-meta">Preview valid ~${secsLeft}s. Any field edit invalidates it.</div>`;
  return html;
}

function godRenderKeyValueRows(obj, skipKeys) {
  const skip = new Set(skipKeys || []);
  return Object.entries(obj || {})
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `<div><span class="divine-kv-key">${escapeHtml(k)}:</span> ${escapeHtml(String(v))}</div>`)
    .join("");
}

function renderGodAppliedHtml(data) {
  const outcome = data.outcome || {};
  let html = `<div class="divine-applied-banner">Applied — intervention ${escapeHtml(String(data.interventionId))}, frame ${escapeHtml(String(data.appliedFrame))}.</div>`;
  if (outcome.kind === "story_event") {
    html += `<div>Title: ${escapeHtml(String(outcome.title || ""))}</div>`;
    const mods = outcome.modifiers || {};
    if (Object.keys(mods).length) {
      html += "<div>Modifiers: " + Object.entries(mods).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", ") + "</div>";
    }
    if (outcome.replacedEventId) html += `<div>Replaced event: ${escapeHtml(String(outcome.replacedEventId))}</div>`;
    if (outcome.primitiveOutcomes && outcome.primitiveOutcomes.length) {
      html += `<div>${outcome.primitiveOutcomes.length} primitive effect(s) applied.</div>`;
    }
    html += `<div>Expires: ${godDurationLabel((outcome.expiresFrame || 0) - (world.frameTick || 0))}</div>`;
  } else {
    html += godRenderKeyValueRows(outcome, ["interventionId", "kind"]);
    if (outcome.expiresFrame != null) {
      html += `<div>Expires: ${godDurationLabel(outcome.expiresFrame - (world.frameTick || 0))}</div>`;
    }
  }
  return html;
}

// --- Sight ---------------------------------------------------------------
async function refreshGodSight() {
  const resp = await godApiFetch("/control/god/sight");
  if (!resp.data || !resp.data.ok) {
    godLastSight = null;
    renderGodError(godSightOutputEl, (resp.data && resp.data.reason) || "sight unavailable");
    return;
  }
  godLastSight = resp.data;
  renderGodSight();
  populateGodCheckpointRestoreSelect();
  if (godActiveTab === "laws") renderGodLawsActive();
}
document.getElementById("godSightRefreshBtn").addEventListener("click", refreshGodSight);

function renderGodSight() {
  if (!godLastSight) { godSightOutputEl.innerHTML = ""; return; }
  const selectedId = godSightAgentSelectEl.value ? Number(godSightAgentSelectEl.value) : null;
  const agent = (godLastSight.agents || []).find((a) => a.id === selectedId) || (godLastSight.agents || [])[0];
  if (!agent) { godSightOutputEl.innerHTML = `<div class="divine-note">No agents.</div>`; return; }
  const resourceRows = Object.entries(agent.resources || {})
    .map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(String(v))}`).join(", ") || "(none)";
  const omen = agent.omen
    ? `active, ${escapeHtml(godCountdownLabel(agent.omen.expiresFrame))}`
    : "none";
  const sampling = agent.sampling
    ? `active, ${escapeHtml(agent.sampling.model || "?")} @ ${escapeHtml(String(agent.sampling.temperature))}${agent.sampling.expiresFrame ? `, ${escapeHtml(godCountdownLabel(agent.sampling.expiresFrame))}` : " (until revoke)"}`
    : "none";
  const contextMask = agent.contextMask
    ? `active, ${escapeHtml(agent.contextMask.mode || "?")}, ${escapeHtml(godCountdownLabel(agent.contextMask.expiresFrame))}`
    : "none";
  const active = (godLastSight.activeEvents || [])
    .filter((e) => e.status === "active")
    .map((e) => `<li>${escapeHtml(e.kind === "story_event" ? (e.title || e.kind) : e.kind)} — ${escapeHtml(godCountdownLabel(e.expiresFrame))}${e.visibility === "private" ? " <span class=\"divine-history-badge divine-history-private\">private</span>" : ""}</li>`)
    .join("") || "<li>(none)</li>";
  godSightOutputEl.innerHTML =
    `<div><span class="divine-kv-key">Health:</span> ${escapeHtml(String(agent.health))} &nbsp; <span class="divine-kv-key">Hunger:</span> ${escapeHtml(String(agent.hunger))}</div>` +
    `<div><span class="divine-kv-key">Incapacitated:</span> ${escapeHtml(String(!!agent.incapacitated))} &nbsp; <span class="divine-kv-key">District:</span> ${escapeHtml(String(agent.currentDistrict || "—"))}</div>` +
    `<div><span class="divine-kv-key">Resources:</span> ${resourceRows}</div>` +
    `<div><span class="divine-kv-key">Last action:</span> ${escapeHtml(String(agent.lastAction || "—"))}</div>` +
    `<div><span class="divine-kv-key">Private omen:</span> ${omen}</div>` +
    `<div><span class="divine-kv-key">Sampling override:</span> ${sampling}</div>` +
    `<div><span class="divine-kv-key">Context mask:</span> ${contextMask}</div>` +
    `<div><span class="divine-kv-key">Memory tiers:</span> working ${escapeHtml(String((agent.memoryCounts || {}).working ?? 0))}, shortTerm ${escapeHtml(String((agent.memoryCounts || {}).shortTerm ?? 0))}</div>` +
    `<div><span class="divine-kv-key">Beliefs held:</span> ${escapeHtml(String(agent.beliefCount ?? 0))}</div>` +
    `<div><span class="divine-kv-key">Active effects (village-wide, this authenticated view):</span><ul>${active}</ul></div>`;
  const checkpoints = (godLastSight.checkpoints || [])
    .map((c) => `<li>${escapeHtml(String(c.label || c.id))} — frame ${escapeHtml(String(c.frameTick))} (${escapeHtml(String(c.id))})</li>`)
    .join("") || "<li>(none)</li>";
  godSightOutputEl.innerHTML +=
    `<div><span class="divine-kv-key">Checkpoints:</span><ul>${checkpoints}</ul></div>`;
}
godSightAgentSelectEl.addEventListener("change", renderGodSight);

function populateGodCheckpointRestoreSelect() {
  const sel = document.getElementById("godCheckpointRestoreSelect");
  if (!sel) return;
  const prev = sel.value;
  const list = (godLastSight && godLastSight.checkpoints) || [];
  sel.innerHTML = list.length
    ? list.map((c) =>
      `<option value="${escapeHtml(String(c.id))}">${escapeHtml(String(c.label || c.id))} (frame ${escapeHtml(String(c.frameTick))})</option>`
    ).join("")
    : "<option value=\"\">— refresh Sight or create first —</option>";
  if (prev && list.some((c) => c.id === prev)) sel.value = prev;
}

// --- Voice: proclamation / providence / private omen ---------------------
wireDivineForm("#godProclamationFieldset", {
  previewBtnId: "godProcPreviewBtn", applyBtnId: "godProcApplyBtn", resultElId: "godProcResult",
  label: "proclamation",
  buildEnvelope: () => {
    const text = document.getElementById("godProcText").value;
    if (!text.trim()) return { error: "text is required" };
    return { envelope: { kind: "proclamation", payload: { text } } };
  },
});

wireDivineForm("#godProvidenceFieldset", {
  previewBtnId: "godProvPreviewBtn", applyBtnId: "godProvApplyBtn", resultElId: "godProvResult",
  label: "providence",
  buildEnvelope: () => {
    const text = document.getElementById("godProvText").value;
    if (!text.trim()) return { error: "text is required" };
    const durationRaw = document.getElementById("godProvDuration").value;
    const payload = { text };
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "providence", payload } };
  },
});

wireDivineForm("#godOmenFieldset", {
  previewBtnId: "godOmenPreviewBtn", applyBtnId: "godOmenApplyBtn", resultElId: "godOmenResult",
  label: "private omen",
  buildEnvelope: () => {
    const targetId = document.getElementById("godOmenAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const text = document.getElementById("godOmenText").value;
    if (!text.trim()) return { error: "text is required" };
    const durationRaw = document.getElementById("godOmenDuration").value;
    const payload = { targetId: parseInt(targetId, 10), text };
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "private_omen", payload } };
  },
});

const GOD_WHISPER_CAMPAIGN_MAX_TARGETS = 12;

function addGodWhisperRow(selectedId, text) {
  const container = document.getElementById("godWhisperRows");
  if (!container) return;
  const rows = container.querySelectorAll(".god-whisper-row");
  if (rows.length >= GOD_WHISPER_CAMPAIGN_MAX_TARGETS) return;
  const row = document.createElement("div");
  row.className = "divine-row god-whisper-row";
  row.innerHTML =
    `<label>Agent <select class="god-whisper-agent">${godAgentOptionsHtml(selectedId)}</select></label>` +
    `<label>Text <textarea class="god-whisper-text"></textarea></label>` +
    `<button type="button" class="god-whisper-remove">Remove</button>`;
  const textEl = row.querySelector(".god-whisper-text");
  if (textEl && text) textEl.value = text;
  row.querySelector(".god-whisper-remove").addEventListener("click", () => {
    row.remove();
    document.getElementById("godWhisperCampaignFieldset").dispatchEvent(new Event("input", { bubbles: true }));
  });
  container.appendChild(row);
}

function initGodWhisperRows() {
  const container = document.getElementById("godWhisperRows");
  if (!container || container.querySelector(".god-whisper-row")) return;
  const agents = getLivingAgents();
  if (agents.length >= 2) {
    addGodWhisperRow(agents[0].id, "");
    addGodWhisperRow(agents[1].id, "");
  } else if (agents.length === 1) {
    addGodWhisperRow(agents[0].id, "");
  }
}

const godWhisperAddRowBtn = document.getElementById("godWhisperAddRow");
if (godWhisperAddRowBtn) {
  godWhisperAddRowBtn.addEventListener("click", () => addGodWhisperRow(null, ""));
}

wireDivineForm("#godWhisperCampaignFieldset", {
  previewBtnId: "godWhisperPreviewBtn", applyBtnId: "godWhisperApplyBtn", resultElId: "godWhisperResult",
  label: "whisper campaign",
  buildEnvelope: () => {
    const theme = document.getElementById("godWhisperTheme").value;
    if (!theme.trim()) return { error: "theme is required" };
    const rows = document.querySelectorAll("#godWhisperRows .god-whisper-row");
    if (!rows.length) return { error: "add at least one whisper target" };
    if (rows.length > GOD_WHISPER_CAMPAIGN_MAX_TARGETS) {
      return { error: `whispers may include at most ${GOD_WHISPER_CAMPAIGN_MAX_TARGETS} targets` };
    }
    const whispers = [];
    const seen = new Set();
    for (const row of rows) {
      const targetId = row.querySelector(".god-whisper-agent").value;
      if (!targetId) return { error: "select an agent for each whisper row" };
      const tid = parseInt(targetId, 10);
      if (seen.has(tid)) return { error: "duplicate agent in whisper rows" };
      seen.add(tid);
      const text = row.querySelector(".god-whisper-text").value;
      if (!text.trim()) return { error: "text is required for each whisper" };
      whispers.push({ targetId: tid, text });
    }
    const payload = { theme, whispers };
    const durationRaw = document.getElementById("godWhisperDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "whisper_campaign", payload } };
  },
});

const godSamplingTempEl = document.getElementById("godSamplingTemp");
const godSamplingTempOutEl = document.getElementById("godSamplingTempOut");
if (godSamplingTempEl && godSamplingTempOutEl) {
  const syncSamplingTemp = () => { godSamplingTempOutEl.textContent = godSamplingTempEl.value; };
  godSamplingTempEl.addEventListener("input", syncSamplingTemp);
  syncSamplingTemp();
}

wireDivineForm("#godSamplingFieldset", {
  previewBtnId: "godSamplingPreviewBtn", applyBtnId: "godSamplingApplyBtn", resultElId: "godSamplingResult",
  label: "agent sampling",
  buildEnvelope: () => {
    const targetId = document.getElementById("godSamplingAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const temperature = parseFloat(document.getElementById("godSamplingTemp").value);
    if (!Number.isFinite(temperature)) return { error: "temperature must be a number" };
    const payload = {
      targetId: parseInt(targetId, 10),
      model: document.getElementById("godSamplingModel").value || "sim-smart",
      temperature,
    };
    const durationRaw = document.getElementById("godSamplingDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    const topP = document.getElementById("godSamplingTopP").value;
    if (topP) payload.top_p = parseFloat(topP);
    const topK = document.getElementById("godSamplingTopK").value;
    if (topK) payload.top_k = parseInt(topK, 10);
    const minP = document.getElementById("godSamplingMinP").value;
    if (minP) payload.min_p = parseFloat(minP);
    return { envelope: { kind: "agent_sampling", payload } };
  },
});

wireDivineForm("#godSamplingRevokeFieldset", {
  previewBtnId: "godSamplingRevokePreviewBtn", applyBtnId: "godSamplingRevokeApplyBtn",
  resultElId: "godSamplingRevokeResult",
  label: "revoke agent sampling",
  buildEnvelope: () => {
    const targetId = document.getElementById("godSamplingRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_agent_sampling", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godMemoryInsertFieldset", {
  previewBtnId: "godMemoryInsertPreviewBtn", applyBtnId: "godMemoryInsertApplyBtn",
  resultElId: "godMemoryInsertResult",
  label: "memory insert",
  buildEnvelope: () => {
    const targetId = document.getElementById("godMemoryInsertAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const text = document.getElementById("godMemoryInsertText").value;
    if (!text.trim()) return { error: "text is required" };
    const salience = parseFloat(document.getElementById("godMemoryInsertSalience").value);
    if (!Number.isFinite(salience)) return { error: "salience must be a number" };
    const payload = { targetId: parseInt(targetId, 10), text, salience };
    const kind = document.getElementById("godMemoryInsertKind").value.trim();
    if (kind) payload.kind = kind;
    return { envelope: { kind: "memory_insert", payload } };
  },
});

wireDivineForm("#godMemoryDeleteFieldset", {
  previewBtnId: "godMemoryDeletePreviewBtn", applyBtnId: "godMemoryDeleteApplyBtn",
  resultElId: "godMemoryDeleteResult",
  label: "memory delete",
  buildEnvelope: () => {
    const targetId = document.getElementById("godMemoryDeleteAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const keyword = document.getElementById("godMemoryDeleteKeyword").value.trim();
    if (keyword) payload.keyword = keyword;
    const frameFromRaw = document.getElementById("godMemoryDeleteFrameFrom").value;
    if (frameFromRaw) payload.frameFrom = parseInt(frameFromRaw, 10);
    const frameToRaw = document.getElementById("godMemoryDeleteFrameTo").value;
    if (frameToRaw) payload.frameTo = parseInt(frameToRaw, 10);
    const kindsRaw = document.getElementById("godMemoryDeleteKinds").value.trim();
    if (kindsRaw) {
      payload.kinds = kindsRaw.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (!payload.keyword && payload.frameFrom == null && payload.frameTo == null && !payload.kinds) {
      return { error: "at least one filter (keyword, frame range, or kinds) is required" };
    }
    return { envelope: { kind: "memory_delete", payload } };
  },
});

wireDivineForm("#godBeliefPlantFieldset", {
  previewBtnId: "godBeliefPlantPreviewBtn", applyBtnId: "godBeliefPlantApplyBtn",
  resultElId: "godBeliefPlantResult",
  label: "belief plant",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBeliefPlantAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const beliefId = document.getElementById("godBeliefPlantId").value.trim();
    const text = document.getElementById("godBeliefPlantText").value.trim();
    if (!beliefId && !text) return { error: "belief id or text is required" };
    const salience = parseFloat(document.getElementById("godBeliefPlantSalience").value);
    if (!Number.isFinite(salience)) return { error: "salience must be a number" };
    const payload = {
      targetId: parseInt(targetId, 10),
      plantInMemeTexts: document.getElementById("godBeliefPlantMemeTexts").checked,
      salience,
    };
    if (beliefId) payload.beliefId = beliefId;
    if (text) payload.text = text;
    return { envelope: { kind: "belief_plant", payload } };
  },
});

function syncGodDistortionModeFields() {
  const mode = document.querySelector('input[name="godDistortionMode"]:checked')?.value || "blue_pill";
  const dreamRow = document.getElementById("godDistortionDreamRow");
  const whisperRow = document.getElementById("godDistortionWhisperRow");
  if (dreamRow) dreamRow.style.display = mode === "dream" ? "" : "none";
  if (whisperRow) whisperRow.style.display = mode === "whisper_chain" ? "" : "none";
}
document.querySelectorAll('input[name="godDistortionMode"]').forEach((el) => {
  el.addEventListener("change", syncGodDistortionModeFields);
});
syncGodDistortionModeFields();

wireDivineForm("#godDistortionFieldset", {
  previewBtnId: "godDistortionPreviewBtn", applyBtnId: "godDistortionApplyBtn",
  resultElId: "godDistortionResult",
  label: "context mask",
  buildEnvelope: () => {
    const targetId = document.getElementById("godDistortionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const mode = document.querySelector('input[name="godDistortionMode"]:checked')?.value;
    if (!mode) return { error: "select a mask mode" };
    const payload = { targetId: parseInt(targetId, 10), mode };
    const durationRaw = document.getElementById("godDistortionDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    if (mode === "dream") {
      const raw = document.getElementById("godDistortionDreamJson").value.trim();
      if (!raw) return { error: "dream snapshot JSON is required for dream mode" };
      try {
        payload.dreamSnapshot = JSON.parse(raw);
      } catch (err) {
        return { error: "dream snapshot must be valid JSON" };
      }
    }
    if (mode === "whisper_chain") {
      const raw = document.getElementById("godDistortionWhisperJson").value.trim();
      if (!raw) return { error: "forged conversations JSON is required for whisper chain" };
      try {
        payload.forgedConversations = JSON.parse(raw);
      } catch (err) {
        return { error: "forged conversations must be valid JSON" };
      }
    }
    return { envelope: { kind: "context_mask", payload } };
  },
});

function syncGodVetoResolveFields() {
  const mode = document.getElementById("godVetoResolveMode")?.value;
  const row = document.getElementById("godVetoRewriteRow");
  if (row) row.style.display = mode === "rewrite" ? "" : "none";
}
const godVetoResolveModeEl = document.getElementById("godVetoResolveMode");
if (godVetoResolveModeEl) {
  godVetoResolveModeEl.addEventListener("change", syncGodVetoResolveFields);
  syncGodVetoResolveFields();
}

wireDivineForm("#godCompulsionFieldset", {
  previewBtnId: "godCompulsionPreviewBtn", applyBtnId: "godCompulsionApplyBtn",
  resultElId: "godCompulsionResult", label: "decision compulsion",
  buildEnvelope: () => {
    const targetId = document.getElementById("godCompulsionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const action = document.getElementById("godCompulsionAction").value;
    const payload = {
      targetId: parseInt(targetId, 10),
      pinnedDecision: { action, reasoning: "Divine compulsion." },
    };
    const durationRaw = document.getElementById("godCompulsionDuration").value;
    const turnsRaw = document.getElementById("godCompulsionTurns").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    if (turnsRaw) payload.remainingTurns = parseInt(turnsRaw, 10);
    if (!payload.durationFrames && !payload.remainingTurns) {
      return { error: "set duration (frames) or remaining turns" };
    }
    return { envelope: { kind: "decision_compulsion", payload } };
  },
});

wireDivineForm("#godVetoArmFieldset", {
  previewBtnId: "godVetoArmPreviewBtn", applyBtnId: "godVetoArmApplyBtn",
  resultElId: "godVetoArmResult", label: "veto arm",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVetoArmAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const durationRaw = document.getElementById("godVetoArmDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "decision_veto_arm", payload } };
  },
});

wireDivineForm("#godVetoResolveFieldset", {
  previewBtnId: "godVetoResolvePreviewBtn", applyBtnId: "godVetoResolveApplyBtn",
  resultElId: "godVetoResolveResult", label: "veto resolve",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVetoResolveAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const resolution = document.getElementById("godVetoResolveMode").value;
    const payload = { targetId: parseInt(targetId, 10), resolution };
    if (resolution === "rewrite") {
      const action = document.getElementById("godVetoRewriteAction").value;
      payload.rewrittenDecision = { action, reasoning: "Divine veto rewrite." };
    }
    return { envelope: { kind: "decision_veto_resolve", payload } };
  },
});

wireDivineForm("#godPossessionFieldset", {
  previewBtnId: "godPossessionPreviewBtn", applyBtnId: "godPossessionApplyBtn",
  resultElId: "godPossessionResult", label: "agent possession",
  buildEnvelope: () => {
    const targetId = document.getElementById("godPossessionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const action = document.getElementById("godPossessionAction").value;
    const payload = {
      targetId: parseInt(targetId, 10),
      pinnedDecision: { action, reasoning: "Divine possession." },
    };
    const durationRaw = document.getElementById("godPossessionDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "agent_possession", payload } };
  },
});

wireDivineForm("#godGateRevokeFieldset", {
  previewBtnId: "godGateRevokePreviewBtn", applyBtnId: "godGateRevokeApplyBtn",
  resultElId: "godGateRevokeResult", label: "revoke decision gate",
  buildEnvelope: () => {
    const targetId = document.getElementById("godGateRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_decision_gate", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

function godBuildBargainPredicate(kind, arg1, arg2) {
  if (kind === "agent_has_resource") {
    if (!arg1) return { error: "resource id required for agent_has_resource" };
    const pred = { kind, resourceId: arg1.trim() };
    if (arg2) pred.amount = parseInt(arg2, 10);
    return { predicate: pred };
  }
  if (kind === "structure_built") {
    if (!arg1) return { error: "structure type required for structure_built" };
    return { predicate: { kind, structureType: arg1.trim() } };
  }
  if (kind === "frame_reached") {
    if (!arg1) return { error: "frame required for frame_reached" };
    return { predicate: { kind, frame: parseInt(arg1, 10) } };
  }
  if (kind === "agent_health_below") {
    if (!arg1) return { error: "threshold required for agent_health_below" };
    return { predicate: { kind, threshold: parseFloat(arg1) } };
  }
  return { error: "unknown predicate kind" };
}

wireDivineForm("#godBurningBushFieldset", {
  previewBtnId: "godBurningBushPreviewBtn", applyBtnId: "godBurningBushApplyBtn",
  resultElId: "godBurningBushResult", label: "burning bush message",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBurningBushAgentSelect").value;
    const text = (document.getElementById("godBurningBushText").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!text) return { error: "message text required" };
    return { envelope: { kind: "burning_bush_message", payload: { targetId: parseInt(targetId, 10), text } } };
  },
});

wireDivineForm("#godBurningBushCloseFieldset", {
  previewBtnId: "godBurningBushClosePreviewBtn", applyBtnId: "godBurningBushCloseApplyBtn",
  resultElId: "godBurningBushCloseResult", label: "burning bush close",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBurningBushCloseAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "burning_bush_close", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godBargainFieldset", {
  previewBtnId: "godBargainPreviewBtn", applyBtnId: "godBargainApplyBtn",
  resultElId: "godBargainResult", label: "merovingian bargain",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBargainAgentSelect").value;
    const termsText = (document.getElementById("godBargainTerms").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!termsText) return { error: "bargain terms required" };
    const succKind = document.getElementById("godBargainSuccessKind").value;
    const succBuilt = godBuildBargainPredicate(
      succKind,
      document.getElementById("godBargainSuccessArg1").value,
      document.getElementById("godBargainSuccessArg2").value,
    );
    if (succBuilt.error) return succBuilt;
    const payload = {
      targetId: parseInt(targetId, 10),
      termsText,
      successPredicate: succBuilt.predicate,
    };
    const durationRaw = document.getElementById("godBargainDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    const rewardKind = document.getElementById("godBargainRewardKind").value;
    if (rewardKind === "grant_resource") {
      const resourceId = (document.getElementById("godBargainRewardResource").value || "").trim();
      const amount = parseInt(document.getElementById("godBargainRewardAmount").value, 10) || 1;
      if (!resourceId) return { error: "reward resource id required" };
      payload.rewardPrimitive = {
        kind: "grant_resource",
        payload: { resourceId, amount, target: { agentId: parseInt(targetId, 10) } },
      };
    } else if (rewardKind === "agent_vitals") {
      payload.rewardPrimitive = {
        kind: "agent_vitals",
        payload: { targetId: parseInt(targetId, 10), healthDelta: 10, hungerDelta: 0 },
      };
    }
    const punishKind = document.getElementById("godBargainPunishKind").value;
    if (punishKind === "agent_vitals") {
      const healthDelta = parseFloat(document.getElementById("godBargainPunishHealth").value) || -10;
      payload.punishPrimitive = {
        kind: "agent_vitals",
        payload: { targetId: parseInt(targetId, 10), healthDelta, hungerDelta: 0 },
      };
    }
    return { envelope: { kind: "merovingian_bargain", payload } };
  },
});

wireDivineForm("#godBargainSettleFieldset", {
  previewBtnId: "godBargainSettlePreviewBtn", applyBtnId: "godBargainSettleApplyBtn",
  resultElId: "godBargainSettleResult", label: "bargain settle",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBargainSettleAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const outcome = document.getElementById("godBargainSettleOutcome").value;
    return { envelope: { kind: "bargain_settle", payload: { targetId: parseInt(targetId, 10), outcome } } };
  },
});

function parseGodOracleHints(raw) {
  const hints = [];
  for (const line of (raw || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const pipe = trimmed.indexOf("|");
    if (pipe < 1) return { error: `oracle hint must be revealFrame|text: ${trimmed}` };
    const revealFrame = parseInt(trimmed.slice(0, pipe).trim(), 10);
    const text = trimmed.slice(pipe + 1).trim();
    if (!Number.isFinite(revealFrame) || revealFrame < 0) {
      return { error: `invalid revealFrame in: ${trimmed}` };
    }
    if (!text) return { error: `oracle hint text required: ${trimmed}` };
    hints.push({ revealFrame, text });
  }
  return { hints };
}

wireDivineForm("#godAnointFieldset", {
  previewBtnId: "godAnointPreviewBtn", applyBtnId: "godAnointApplyBtn",
  resultElId: "godAnointResult", label: "anoint",
  buildEnvelope: () => {
    const targetId = document.getElementById("godAnointAgentSelect").value;
    const destinyText = (document.getElementById("godAnointDestiny").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!destinyText) return { error: "destiny text required" };
    const payload = {
      targetId: parseInt(targetId, 10),
      destinyText,
    };
    const stigmataRaw = (document.getElementById("godAnointStigmata").value || "").trim();
    if (stigmataRaw) {
      payload.stigmataTags = stigmataRaw.split(",").map((t) => t.trim()).filter(Boolean);
    }
    const oracleParsed = parseGodOracleHints(document.getElementById("godAnointOracle").value);
    if (oracleParsed.error) return oracleParsed;
    if (oracleParsed.hints.length) payload.oracleHints = oracleParsed.hints;
    const durationRaw = document.getElementById("godAnointDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "anoint", payload } };
  },
});

wireDivineForm("#godAnointRevokeFieldset", {
  previewBtnId: "godAnointRevokePreviewBtn", applyBtnId: "godAnointRevokeApplyBtn",
  resultElId: "godAnointRevokeResult", label: "revoke anoint",
  buildEnvelope: () => {
    const targetId = document.getElementById("godAnointRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_anoint", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godIdentityEditFieldset", {
  previewBtnId: "godIdentityEditPreviewBtn", applyBtnId: "godIdentityEditApplyBtn",
  resultElId: "godIdentityEditResult", label: "identity edit",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityEditAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const persona = (document.getElementById("godIdentityEditPersona").value || "").trim();
    const personality = (document.getElementById("godIdentityEditPersonality").value || "").trim();
    const role = (document.getElementById("godIdentityEditRole").value || "").trim();
    if (persona) payload.persona = persona;
    if (personality) payload.personality = personality;
    if (role) payload.role = role;
    if (!persona && !personality && !role) {
      return { error: "enter at least one of persona, personality, or role" };
    }
    const durationRaw = document.getElementById("godIdentityEditDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "identity_edit", payload } };
  },
});

wireDivineForm("#godIdentityCopyFieldset", {
  previewBtnId: "godIdentityCopyPreviewBtn", applyBtnId: "godIdentityCopyApplyBtn",
  resultElId: "godIdentityCopyResult", label: "identity copy",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityCopyTargetSelect").value;
    const sourceId = document.getElementById("godIdentityCopySourceSelect").value;
    if (!targetId || !sourceId) return { error: "select target and source agents" };
    if (targetId === sourceId) return { error: "target and source must differ" };
    const rate = parseFloat(document.getElementById("godIdentityCopyRate").value);
    if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
      return { error: "rate per think must be 0.0–1.0" };
    }
    const payload = {
      targetId: parseInt(targetId, 10),
      sourceId: parseInt(sourceId, 10),
      ratePerThink: rate,
      syncMemories: document.getElementById("godIdentityCopySyncMemories").checked,
    };
    const durationRaw = document.getElementById("godIdentityCopyDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    return { envelope: { kind: "identity_copy_overwrite", payload } };
  },
});

wireDivineForm("#godIdentityCancelFieldset", {
  previewBtnId: "godIdentityCancelPreviewBtn", applyBtnId: "godIdentityCancelApplyBtn",
  resultElId: "godIdentityCancelResult", label: "identity forge cancel",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityCancelAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "identity_forge_cancel", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

function parseGodArchitectCells(raw) {
  const text = (raw || "").trim();
  if (!text) return { error: "cells are required" };
  if (text.startsWith("{")) {
    try {
      const bounds = JSON.parse(text);
      return { cells: [bounds] };
    } catch (_e) {
      return { error: "bounds JSON is invalid" };
    }
  }
  const cells = [];
  for (const line of text.split(/\r?\n/)) {
    const part = line.trim();
    if (!part) continue;
    if (!/^\d+\s*,\s*\d+$/.test(part)) return { error: `invalid cell line: ${part}` };
    cells.push(part.replace(/\s+/g, ""));
  }
  if (!cells.length) return { error: "cells are required" };
  return { cells };
}

function selectedGodAgentIds(selectId) {
  const el = document.getElementById(selectId);
  if (!el) return [];
  return Array.from(el.selectedOptions || []).map((o) => parseInt(o.value, 10)).filter((n) => Number.isFinite(n));
}

wireDivineForm("#godArchitectZoneFieldset", {
  previewBtnId: "godArchitectZonePreviewBtn", applyBtnId: "godArchitectZoneApplyBtn",
  resultElId: "godArchitectZoneResult", label: "architect zone",
  buildEnvelope: () => {
    const zoneKind = document.getElementById("godArchitectZoneKind").value;
    const districtId = document.getElementById("godArchitectDistrict").value;
    const parsed = parseGodArchitectCells(document.getElementById("godArchitectCells").value);
    if (parsed.error) return parsed;
    const payload = { zoneKind, cells: parsed.cells };
    if (districtId) payload.districtId = districtId;
    const durationRaw = document.getElementById("godArchitectDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    if (zoneKind === "paint") {
      if (!districtId) return { error: "district is required for paint zones" };
      payload.paintTerrain = document.getElementById("godArchitectPaintTerrain").value;
      payload.reversible = document.getElementById("godArchitectReversible").checked;
    }
    if (zoneKind === "door") {
      if (!districtId) return { error: "district is required for door zones" };
      const keyId = (document.getElementById("godArchitectKeyId").value || "").trim();
      if (!keyId) return { error: "key id is required for door zones" };
      payload.keyId = keyId;
      const grantIds = selectedGodAgentIds("godArchitectGrantKeyAgents");
      if (grantIds.length) payload.grantKeyAgentIds = grantIds;
    }
    if (zoneKind === "limbo") {
      const holdIds = selectedGodAgentIds("godArchitectHoldAgents");
      if (!holdIds.length) return { error: "select at least one limbo hold agent" };
      payload.holdAgentIds = holdIds;
    }
    return { envelope: { kind: "architect_zone", payload } };
  },
});

wireDivineForm("#godArchitectCancelFieldset", {
  previewBtnId: "godArchitectCancelPreviewBtn", applyBtnId: "godArchitectCancelApplyBtn",
  resultElId: "godArchitectCancelResult", label: "architect zone cancel",
  buildEnvelope: () => {
    const zoneId = (document.getElementById("godArchitectCancelZoneId").value || "").trim();
    if (!zoneId) return { error: "zone id is required" };
    return { envelope: { kind: "architect_zone_cancel", payload: { zoneId } } };
  },
});

wireDivineForm("#godArchitectReleaseFieldset", {
  previewBtnId: "godArchitectReleasePreviewBtn", applyBtnId: "godArchitectReleaseApplyBtn",
  resultElId: "godArchitectReleaseResult", label: "architect release hold",
  buildEnvelope: () => {
    const zoneId = (document.getElementById("godArchitectReleaseZoneId").value || "").trim();
    if (!zoneId) return { error: "zone id is required" };
    const payload = { zoneId };
    const agentIds = selectedGodAgentIds("godArchitectReleaseAgents");
    if (agentIds.length) payload.agentIds = agentIds;
    return { envelope: { kind: "architect_release_hold", payload } };
  },
});

wireDivineForm("#godCheckpointCreateFieldset", {
  previewBtnId: "godCheckpointCreatePreviewBtn", applyBtnId: "godCheckpointCreateApplyBtn",
  resultElId: "godCheckpointCreateResult", label: "checkpoint create",
  buildEnvelope: () => {
    const label = (document.getElementById("godCheckpointLabel").value || "").trim();
    if (!label) return { error: "label is required" };
    const payload = { label };
    if (document.getElementById("godCheckpointReplaceOldest").checked) {
      payload.replaceOldest = true;
    }
    return { envelope: { kind: "checkpoint_create", payload } };
  },
  onApplied: () => refreshGodSight(),
});

wireDivineForm("#godCheckpointRestoreFieldset", {
  previewBtnId: "godCheckpointRestorePreviewBtn", applyBtnId: "godCheckpointRestoreApplyBtn",
  resultElId: "godCheckpointRestoreResult", label: "checkpoint restore",
  buildEnvelope: () => {
    const checkpointId = (document.getElementById("godCheckpointRestoreSelect").value || "").trim();
    if (!checkpointId) return { error: "select a checkpoint" };
    return { envelope: { kind: "checkpoint_restore", payload: { checkpointId } } };
  },
  onApplied: () => refreshGodSight(),
});

// --- Miracles: agent_vitals / grant_resource / structure_condition -------
wireDivineForm("#godVitalsFieldset", {
  previewBtnId: "godVitalsPreviewBtn", applyBtnId: "godVitalsApplyBtn", resultElId: "godVitalsResult",
  label: "agent vitals",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVitalsAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const health = parseFloat(document.getElementById("godVitalsHealth").value || "0");
    const hunger = parseFloat(document.getElementById("godVitalsHunger").value || "0");
    if (!health && !hunger) return { error: "at least one of health/hunger delta must be non-zero" };
    const payload = { targetId: parseInt(targetId, 10) };
    if (health) payload.healthDelta = health;
    if (hunger) payload.hungerDelta = hunger;
    return { envelope: { kind: "agent_vitals", payload } };
  },
});

wireDivineForm("#godGrantFieldset", {
  previewBtnId: "godGrantPreviewBtn", applyBtnId: "godGrantApplyBtn", resultElId: "godGrantResult",
  label: "grant",
  buildEnvelope: () => {
    const resourceId = document.getElementById("godGrantResourceSelect").value;
    if (!resourceId) return { error: "select a resource" };
    const amount = parseInt(document.getElementById("godGrantAmount").value || "0", 10);
    if (!amount || amount <= 0) return { error: "amount must be positive" };
    const targetKind = document.getElementById("godGrantTargetKind").value;
    let target = "stockpile";
    if (targetKind === "agent") {
      const agentId = document.getElementById("godGrantAgentSelect").value;
      if (!agentId) return { error: "select a target agent" };
      target = { agentId: parseInt(agentId, 10) };
    }
    return { envelope: { kind: "grant_resource", payload: { resourceId, amount, target } } };
  },
});

wireDivineForm("#godStructureFieldset", {
  previewBtnId: "godStructurePreviewBtn", applyBtnId: "godStructureApplyBtn", resultElId: "godStructureResult",
  label: "structure condition",
  buildEnvelope: () => {
    const structureId = document.getElementById("godStructureSelect").value;
    if (!structureId) return { error: "select a structure" };
    const delta = parseFloat(document.getElementById("godStructureDelta").value || "0");
    if (!delta) return { error: "delta must be non-zero" };
    return { envelope: { kind: "structure_condition", payload: { structureId: parseInt(structureId, 10), delta } } };
  },
});

wireDivineForm("#godMassRepairFieldset", {
  previewBtnId: "godMassRepairPreviewBtn", applyBtnId: "godMassRepairApplyBtn", resultElId: "godMassRepairResult",
  label: "mass repair structures",
  buildEnvelope: () => {
    const scopeMode = document.getElementById("godMassRepairScope").value;
    const payload = { unRuin: document.getElementById("godMassRepairUnRuin").checked };
    const targetRaw = document.getElementById("godMassRepairTarget").value;
    if (targetRaw) {
      const conditionTarget = parseFloat(targetRaw);
      if (!Number.isFinite(conditionTarget) || conditionTarget < 0 || conditionTarget > 100) {
        return { error: "condition target must be 0–100" };
      }
      payload.conditionTarget = conditionTarget;
    }
    if (scopeMode === "ids") {
      const raw = document.getElementById("godMassRepairIds").value.trim();
      if (!raw) return { error: "enter comma-separated structure ids" };
      const structureIds = raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n));
      if (!structureIds.length) return { error: "invalid structure ids" };
      payload.scope = "ids";
      payload.structureIds = structureIds;
    } else if (scopeMode === "district") {
      const districtId = document.getElementById("godMassRepairDistrict").value;
      if (!districtId) return { error: "select a district" };
      payload.scope = { districtId };
    } else {
      payload.scope = "all_critical";
    }
    return { envelope: { kind: "repair_structures", payload } };
  },
});

wireDivineForm("#godClearRuinsFieldset", {
  previewBtnId: "godClearRuinsPreviewBtn", applyBtnId: "godClearRuinsApplyBtn", resultElId: "godClearRuinsResult",
  label: "clear ruins",
  buildEnvelope: () => {
    const mode = document.getElementById("godClearRuinsMode").value;
    const payload = {};
    const minAgeRaw = document.getElementById("godClearRuinsMinAge").value;
    if (minAgeRaw) {
      const minAgeFrames = parseInt(minAgeRaw, 10);
      if (!Number.isFinite(minAgeFrames) || minAgeFrames < 0) return { error: "min age must be a non-negative integer" };
      payload.minAgeFrames = minAgeFrames;
    }
    if (mode === "ids") {
      const raw = document.getElementById("godClearRuinsIds").value.trim();
      if (!raw) return { error: "enter comma-separated ruin ids" };
      const structureIds = raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n));
      if (!structureIds.length) return { error: "invalid ruin ids" };
      payload.structureIds = structureIds;
    } else if (mode === "district") {
      const districtId = document.getElementById("godClearRuinsDistrict").value;
      if (!districtId) return { error: "select a district" };
      payload.districtId = districtId;
      if (!("minAgeFrames" in payload)) payload.minAgeFrames = godDefaultDayFrames();
    } else {
      if (!("minAgeFrames" in payload)) payload.minAgeFrames = godDefaultDayFrames();
    }
    return { envelope: { kind: "clear_ruins", payload } };
  },
});

// --- Shared modifier editor (Story + Laws both submit story_event) -------
const GOD_MODIFIER_KEYS = [
  "gather_yield_multiplier", "fish_yield_multiplier", "hunger_drain_multiplier",
  "health_regen_multiplier", "starvation_damage_multiplier", "structure_decay_multiplier",
  "spoilage_multiplier",
];
function renderGodModifierEditor(containerId, prefix) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const ranges = (godCapabilities && godCapabilities.modifierRanges) || {};
  el.innerHTML = GOD_MODIFIER_KEYS.map((key) => {
    const range = ranges[key] || [0, 3];
    return `<label style="flex-direction:row; align-items:center; gap:6px;">` +
      `<input type="checkbox" class="${prefix}-mod-enable" data-key="${escapeHtml(key)}" style="width:auto;" /> ` +
      `${escapeHtml(key)} <input type="number" class="${prefix}-mod-value" data-key="${escapeHtml(key)}" ` +
      `min="${range[0]}" max="${range[1]}" step="0.05" value="1.0" style="width:80px;" />` +
      ` <span class="divine-meta">(${range[0]}..${range[1]})</span></label>`;
  }).join("");
}
function godReadModifiers(prefix) {
  const modifiers = {};
  document.querySelectorAll(`.${prefix}-mod-enable`).forEach((cb) => {
    if (!cb.checked) return;
    const key = cb.dataset.key;
    const valueInput = document.querySelector(`.${prefix}-mod-value[data-key="${key}"]`);
    modifiers[key] = parseFloat(valueInput ? valueInput.value : "1.0");
  });
  return modifiers;
}

// --- Story primitives editor (bounded, up to GOD_STORY_EVENT_MAX_PRIMITIVES) ---
let godStoryPrimitiveCount = 0;
function godPrimitiveFieldsHtml(kind, index) {
  if (kind === "agent_vitals") {
    return `<select class="godPrimAgent">${godAgentOptionsHtml(null)}</select>` +
      `<input type="number" class="godPrimHealth" placeholder="health delta" value="0" style="width:90px;" />` +
      `<input type="number" class="godPrimHunger" placeholder="hunger delta" value="0" style="width:90px;" />`;
  }
  if (kind === "grant_resource") {
    const reg = resourceRegistry();
    const resOptions = Object.keys(reg).map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(reg[id].label || id)}</option>`).join("");
    return `<select class="godPrimResource">${resOptions}</select>` +
      `<input type="number" class="godPrimAmount" placeholder="amount" value="1" min="1" style="width:70px;" />` +
      `<select class="godPrimTargetKind"><option value="stockpile">Stockpile</option><option value="agent">Agent</option></select>` +
      `<select class="godPrimTargetAgent">${godAgentOptionsHtml(null)}</select>`;
  }
  if (kind === "structure_condition") {
    const structures = (getCiv().structures || []).filter((s) => !s.isRuin && (s.condition == null || s.condition > 0));
    const structOptions = structures.map((s) => `<option value="${s.id}">${escapeHtml(s.name || s.type)} (#${s.id})</option>`).join("");
    return `<select class="godPrimStructure">${structOptions}</select>` +
      `<input type="number" class="godPrimDelta" placeholder="delta" value="0" style="width:80px;" />`;
  }
  return "";
}
document.getElementById("godStoryAddPrimitiveBtn").addEventListener("click", () => {
  const cap = (godCapabilities && godCapabilities.kinds && godCapabilities.kinds.story_event &&
    godCapabilities.kinds.story_event.payload.primitives && godCapabilities.kinds.story_event.payload.primitives.maxItems) || 5;
  if (godStoryPrimitiveCount >= cap) return;
  const container = document.getElementById("godStoryPrimitives");
  const row = document.createElement("div");
  row.className = "divine-primitive-row";
  const index = godStoryPrimitiveCount++;
  row.dataset.index = index;
  row.innerHTML = `<select class="godPrimKind">` +
    `<option value="agent_vitals">Agent vitals</option>` +
    `<option value="grant_resource">Grant resource</option>` +
    `<option value="structure_condition">Structure condition</option>` +
    `</select><span class="godPrimFields">${godPrimitiveFieldsHtml("agent_vitals", index)}</span>` +
    `<button type="button" class="godPrimRemove">Remove</button>`;
  container.appendChild(row);
  row.querySelector(".godPrimKind").addEventListener("change", (e) => {
    row.querySelector(".godPrimFields").innerHTML = godPrimitiveFieldsHtml(e.target.value, index);
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });
  row.querySelector(".godPrimRemove").addEventListener("click", () => {
    row.remove();
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });
  container.dispatchEvent(new Event("change", { bubbles: true }));
});

function godReadStoryPrimitives() {
  const rows = Array.from(document.querySelectorAll("#godStoryPrimitives .divine-primitive-row"));
  const primitives = [];
  for (const row of rows) {
    const kind = row.querySelector(".godPrimKind").value;
    if (kind === "agent_vitals") {
      const agentId = row.querySelector(".godPrimAgent").value;
      if (!agentId) return { error: "primitive: select an agent" };
      const health = parseFloat(row.querySelector(".godPrimHealth").value || "0");
      const hunger = parseFloat(row.querySelector(".godPrimHunger").value || "0");
      if (!health && !hunger) return { error: "primitive: health/hunger delta required" };
      const payload = { targetId: parseInt(agentId, 10) };
      if (health) payload.healthDelta = health;
      if (hunger) payload.hungerDelta = hunger;
      primitives.push({ kind: "agent_vitals", payload });
    } else if (kind === "grant_resource") {
      const resourceId = row.querySelector(".godPrimResource").value;
      const amount = parseInt(row.querySelector(".godPrimAmount").value || "0", 10);
      if (!resourceId || !amount) return { error: "primitive: resource + amount required" };
      const targetKind = row.querySelector(".godPrimTargetKind").value;
      let target = "stockpile";
      if (targetKind === "agent") {
        const agentId = row.querySelector(".godPrimTargetAgent").value;
        if (!agentId) return { error: "primitive: select a target agent" };
        target = { agentId: parseInt(agentId, 10) };
      }
      primitives.push({ kind: "grant_resource", payload: { resourceId, amount, target } });
    } else if (kind === "structure_condition") {
      const structureId = row.querySelector(".godPrimStructure").value;
      const delta = parseFloat(row.querySelector(".godPrimDelta").value || "0");
      if (!structureId || !delta) return { error: "primitive: structure + non-zero delta required" };
      primitives.push({ kind: "structure_condition", payload: { structureId: parseInt(structureId, 10), delta } });
    }
  }
  return { primitives };
}

// --- Story tab ------------------------------------------------------------
wireDivineForm("#godStoryFieldset", {
  previewBtnId: "godStoryPreviewBtn", applyBtnId: "godStoryApplyBtn", resultElId: "godStoryResult",
  label: "story event",
  buildEnvelope: () => {
    const title = document.getElementById("godStoryTitle").value;
    const narration = document.getElementById("godStoryNarration").value;
    if (!title.trim() || !narration.trim()) return { error: "title and narration are required" };
    const visibility = document.getElementById("godStoryVisibility").value;
    const payload = { title, narration, visibility };
    if (visibility === "private") {
      const targetId = document.getElementById("godStoryTargetSelect").value;
      if (!targetId) return { error: "select a private target agent" };
      payload.targetId = parseInt(targetId, 10);
    }
    const durationRaw = document.getElementById("godStoryDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    const modifiers = godReadModifiers("gs");
    if (Object.keys(modifiers).length) payload.modifiers = modifiers;
    const primResult = godReadStoryPrimitives();
    if (primResult.error) return { error: primResult.error };
    if (primResult.primitives.length) payload.primitives = primResult.primitives;
    if (document.getElementById("godStoryProvidenceCheckbox").checked) {
      const provText = document.getElementById("godStoryProvidenceText").value;
      if (!provText.trim()) return { error: "providence text is required when the checkbox is on" };
      payload.providence = { text: provText };
    }
    const replaceCheckbox = document.getElementById("godStoryReplaceCheckbox");
    const replaceId = document.getElementById("godStoryReplaceId").value;
    if (replaceCheckbox.checked && replaceId) payload.replaceEffectId = replaceId;
    return { envelope: { kind: "story_event", payload } };
  },
  onPreviewRejected: (reason) => {
    // Hard-reject conflict (backend rejects rather than disclosing an
    // outgoingId for modifier keys — see report's backend-gap note): parse
    // the id out of the reason string so the operator can opt into replacing
    // it without having to already know the id.
    const match = /\(id ([^)]+)\)/.exec(String(reason || ""));
    const replaceCheckbox = document.getElementById("godStoryReplaceCheckbox");
    const replaceIdInput = document.getElementById("godStoryReplaceId");
    if (match) {
      replaceCheckbox.disabled = false;
      replaceIdInput.value = match[1];
    } else {
      replaceCheckbox.disabled = true;
      replaceCheckbox.checked = false;
      replaceIdInput.value = "";
    }
  },
  onApplied: () => {
    document.getElementById("godStoryReplaceCheckbox").checked = false;
    document.getElementById("godStoryReplaceCheckbox").disabled = true;
    document.getElementById("godStoryReplaceId").value = "";
  },
});

// --- Compile tab (Sovereign God mode Optional Phase 8, docs/plan-sovereign-
// god-mode-v2.md "Free-prose story compiler") ---------------------------
// Deliberately NOT wired through wireDivineForm/Apply: a successful compile
// fills the Story tab and switches to it -- the operator still has to
// explicitly Preview (again, through the normal Story tab flow) and Apply
// there. No shortcut Apply button exists on this tab.
let godCompilerMinIntervalSec = 5;

// Populates the Story tab's own fields from a compiled draft's
// normalizedCommand, then invalidates any stale Story preview (any field
// edit already invalidates a preview per the standard contract) so the
// operator must re-Preview before Applying -- matching how every other
// Voice/Miracle/Law/Story action already works.
function godPopulateStoryFromCompiled(normalizedCommand) {
  const payload = (normalizedCommand && normalizedCommand.payload) || {};
  document.getElementById("godStoryTitle").value = payload.title || "";
  document.getElementById("godStoryNarration").value = payload.narration || "";
  const visibilityEl = document.getElementById("godStoryVisibility");
  visibilityEl.value = payload.visibility === "private" ? "private" : "public";
  if (payload.visibility === "private" && payload.targetId != null) {
    document.getElementById("godStoryTargetSelect").value = String(payload.targetId);
  }
  document.getElementById("godStoryDuration").value = payload.durationFrames || "";

  document.querySelectorAll(".gs-mod-enable").forEach((cb) => { cb.checked = false; });
  const modifiers = payload.modifiers || {};
  Object.keys(modifiers).forEach((key) => {
    const cb = document.querySelector(`.gs-mod-enable[data-key="${key}"]`);
    const valueInput = document.querySelector(`.gs-mod-value[data-key="${key}"]`);
    if (cb) cb.checked = true;
    if (valueInput) valueInput.value = modifiers[key];
  });

  const primContainer = document.getElementById("godStoryPrimitives");
  primContainer.innerHTML = "";
  godStoryPrimitiveCount = 0;
  const addBtn = document.getElementById("godStoryAddPrimitiveBtn");
  (payload.primitives || []).forEach((prim) => {
    addBtn.click();
    const row = primContainer.lastElementChild;
    if (!row) return;
    const kindSelect = row.querySelector(".godPrimKind");
    kindSelect.value = prim.kind;
    kindSelect.dispatchEvent(new Event("change"));
    const p = prim.payload || {};
    if (prim.kind === "agent_vitals") {
      if (p.targetId != null) row.querySelector(".godPrimAgent").value = String(p.targetId);
      if (p.healthDelta != null) row.querySelector(".godPrimHealth").value = p.healthDelta;
      if (p.hungerDelta != null) row.querySelector(".godPrimHunger").value = p.hungerDelta;
    } else if (prim.kind === "grant_resource") {
      if (p.resourceId) row.querySelector(".godPrimResource").value = p.resourceId;
      if (p.amount != null) row.querySelector(".godPrimAmount").value = p.amount;
      if (p.target && typeof p.target === "object" && p.target.agentId != null) {
        row.querySelector(".godPrimTargetKind").value = "agent";
        row.querySelector(".godPrimTargetAgent").value = String(p.target.agentId);
      }
    } else if (prim.kind === "structure_condition") {
      if (p.structureId != null) row.querySelector(".godPrimStructure").value = String(p.structureId);
      if (p.delta != null) row.querySelector(".godPrimDelta").value = p.delta;
    }
  });

  if (payload.providence) {
    document.getElementById("godStoryProvidenceCheckbox").checked = true;
    document.getElementById("godStoryProvidenceText").value = payload.providence.text || "";
  } else {
    document.getElementById("godStoryProvidenceCheckbox").checked = false;
    document.getElementById("godStoryProvidenceText").value = "";
  }
  document.getElementById("godStoryReplaceCheckbox").checked = false;
  document.getElementById("godStoryReplaceCheckbox").disabled = true;
  document.getElementById("godStoryReplaceId").value = "";

  // Any field edit invalidates a stale preview per the standard contract
  // (wireDivineForm's invalidate() listens for "input"/"change" directly on
  // the fieldset) -- fire one so a leftover Story preview from before this
  // compile can never be Applied unreviewed.
  document.getElementById("godStoryFieldset").dispatchEvent(new Event("input"));
}

const godCompileBtnEl = document.getElementById("godCompileBtn");
const godCompileResultEl = document.getElementById("godCompileResult");
if (godCompileBtnEl) {
  godCompileBtnEl.addEventListener("click", async () => {
    const proseEl = document.getElementById("godCompileProse");
    const prose = proseEl.value;
    if (!prose || !prose.trim()) {
      godCompileResultEl.textContent = "Enter some prose first.";
      return;
    }
    godCompileBtnEl.disabled = true;
    godCompileResultEl.textContent = "Compiling...";
    const resp = await godApiFetch("/control/god/compile", { method: "POST", body: { prose } });
    const data = resp.data || {};
    if (data.compileOk) {
      godCompileResultEl.textContent = "Compiled -- review and Apply from the Story tab.";
      godPopulateStoryFromCompiled(data.normalizedCommand);
      showGodTab("story");
    } else {
      // Rejection or error -- render the reason as plain text only (rule:
      // stored-content safety -- never innerHTML for anything the compiler
      // or its model produced).
      godCompileResultEl.textContent = data.reason || "compile failed";
    }
    // Client-side rate-limit UX only -- the server's GOD_COMPILER_MIN_INTERVAL_SEC
    // check is authoritative regardless of what this timer does.
    setTimeout(() => { godCompileBtnEl.disabled = false; }, godCompilerMinIntervalSec * 1000);
  });
}

// --- Laws tab (a story_event carrying ONLY modifiers, no primitives) -----
wireDivineForm("#godLawFieldset", {
  previewBtnId: "godLawPreviewBtn", applyBtnId: "godLawApplyBtn", resultElId: "godLawResult",
  label: "law",
  buildEnvelope: () => {
    const modifiers = godReadModifiers("gl");
    if (!Object.keys(modifiers).length) return { error: "enable at least one modifier" };
    let title = document.getElementById("godLawTitle").value.trim();
    let narration = document.getElementById("godLawNarration").value.trim();
    if (!title) title = "Divine decree";
    if (!narration) {
      narration = "The village feels a shift: " +
        Object.entries(modifiers).map(([k, v]) => `${k} set to ${v}`).join(", ") + ".";
    }
    const payload = { title, narration, visibility: "public", modifiers };
    const durationRaw = document.getElementById("godLawDuration").value;
    if (durationRaw) payload.durationFrames = parseInt(durationRaw, 10);
    const replaceCheckbox = document.getElementById("godLawReplaceCheckbox");
    const replaceId = document.getElementById("godLawReplaceId").value;
    if (replaceCheckbox.checked && replaceId) payload.replaceEffectId = replaceId;
    return { envelope: { kind: "story_event", payload } };
  },
  onPreviewRejected: (reason) => {
    const match = /\(id ([^)]+)\)/.exec(String(reason || ""));
    const replaceCheckbox = document.getElementById("godLawReplaceCheckbox");
    const replaceIdInput = document.getElementById("godLawReplaceId");
    if (match) {
      replaceCheckbox.disabled = false;
      replaceIdInput.value = match[1];
    } else {
      replaceCheckbox.disabled = true;
      replaceCheckbox.checked = false;
      replaceIdInput.value = "";
    }
  },
  onApplied: () => {
    document.getElementById("godLawReplaceCheckbox").checked = false;
    document.getElementById("godLawReplaceCheckbox").disabled = true;
    document.getElementById("godLawReplaceId").value = "";
    renderGodLawsActive();
  },
});

async function godCancelEffect(targetId) {
  const resp = await godApiFetch("/control/god/cancel", { method: "POST", body: { targetId } });
  refreshGodSightIfOpen();
  return resp;
}

function renderGodLawsActive() {
  // Prefer the authenticated Sight projection (includes private-visibility
  // law events, which the public /state god.activePublicEvents omits) --
  // falls back to the public projection when Sight hasn't been fetched yet.
  const events = (godLastSight && godLastSight.activeEvents)
    || (world.god && world.god.activePublicEvents) || [];
  const lawEvents = events.filter((e) => e && e.status === "active" && e.modifiers && Object.keys(e.modifiers).length);
  if (!lawEvents.length) {
    godLawsActiveEl.innerHTML = `<div class="divine-note">No active timed law modifiers.</div>`;
    return;
  }
  godLawsActiveEl.innerHTML = lawEvents.map((e) => {
    const mods = Object.entries(e.modifiers).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", ");
    return `<div class="divine-history-item">` +
      `<div>${escapeHtml(e.title || "law")} — ${mods}</div>` +
      `<div class="divine-meta">${escapeHtml(godCountdownLabel(e.expiresFrame))}</div>` +
      `<div class="divine-actions"><button type="button" class="godCancelLawBtn" data-id="${escapeHtml(e.id)}">Cancel</button></div>` +
      `</div>`;
  }).join("");
  godLawsActiveEl.querySelectorAll(".godCancelLawBtn").forEach((btn) => {
    btn.setAttribute("data-tip", JSON.stringify({
      t: "Cancel law",
      d: "End this timed modifier early. Does not roll back past effects.",
    }));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await godCancelEffect(btn.dataset.id);
      renderGodLawsActive();
    });
  });
}

// --- History (public interventions only; private history is Sight-only) --
function renderGodHistory() {
  const records = ((world.god && world.god.recentPublicInterventions) || []).slice().reverse().slice(0, 50);
  if (!records.length) {
    godHistoryListEl.innerHTML = `<li class="divine-note">No public interventions yet.</li>`;
    return;
  }
  godHistoryListEl.innerHTML = records.map((r) => {
    const badge = `<span class="divine-history-badge divine-history-public">public</span>`;
    const label = escapeHtml(String(r.title || r.text || r.kind || "intervention"));
    return `<li class="divine-history-item">${badge}` +
      `<span class="divine-kv-key">${escapeHtml(String(r.kind || ""))}</span> — ${label}` +
      `<div class="divine-meta">frame ${escapeHtml(String(r.frameTick))}, id ${escapeHtml(String(r.id))}</div></li>`;
  }).join("");
}

// --- Gate + passive per-poll refresh (History/Laws/banner) ---------------
let godLastAgentListKey = null;
function updateGodModeGate() {
  const flags = (world.config && world.config.flags) || {};
  const enabled = !!flags.GOD_MODE_ENABLED;
  if (enabled !== GOD_MODE_ENABLED_FLAG) GOD_MODE_ENABLED_FLAG = enabled;
  const authRequired = !!flags.GOD_AUTH_REQUIRED;
  const authFlagChanged = authRequired !== GOD_AUTH_REQUIRED_FLAG;
  if (authFlagChanged) GOD_AUTH_REQUIRED_FLAG = authRequired;
  if (divineBarEl) divineBarEl.style.display = GOD_MODE_ENABLED_FLAG ? "" : "none";
  document.body.classList.toggle("divine-bar-visible", !!GOD_MODE_ENABLED_FLAG);
  if (!GOD_MODE_ENABLED_FLAG) {
    closeDivineModal();
    godPublicBannerEl.style.display = "none";
    return;
  }
  if (authFlagChanged) {
    if (GOD_AUTH_REQUIRED_FLAG) {
      if (!godToken) godAuthorized = false;
    } else {
      godAuthorized = true;
      godOpenModeBootstrapped = false;
    }
    updateDivineBarAuthUi();
    if (!GOD_AUTH_REQUIRED_FLAG) godOpenModeBootstrap();
  }
  if (!GOD_AUTH_REQUIRED_FLAG) godOpenModeBootstrap();
  // Change-detected, not every-poll: repopulating <select> elements on every
  // 100ms tick would reset an in-progress dropdown selection.
  const agentKey = JSON.stringify([
    getLivingAgents().map((a) => [a.id, a.name, a.role]),
    Object.keys(resourceRegistry()),
    (getCiv().structures || []).map((s) => [s.id, s.isRuin, s.condition]),
  ]);
  if (agentKey !== godLastAgentListKey) {
    godLastAgentListKey = agentKey;
    populateGodAgentSelects();
  }
}

function renderGodPublicBanner() {
  const records = (world.god && world.god.recentPublicInterventions) || [];
  if (godSeenInterventionIds === null) {
    // First snapshot after page load: remember existing history without
    // banner-ing it (same edge-detection precedent as the founding banner).
    godSeenInterventionIds = new Set(records.map((r) => r.id));
    return;
  }
  const fresh = records.find((r) => !godSeenInterventionIds.has(r.id));
  if (!fresh) return;
  godSeenInterventionIds.add(fresh.id);
  const label = fresh.title || fresh.text || fresh.kind || "A divine intervention";
  godPublicBannerEl.textContent = `Divine: ${String(label)}`;
  godPublicBannerEl.style.display = "block";
  if (godBannerTimer) clearTimeout(godBannerTimer);
  godBannerTimer = setTimeout(() => {
    godPublicBannerEl.style.display = "none";
    godBannerTimer = null;
  }, 6000);
  // Bounded like foundingFramesSeen: drop ids that aged out of the ring.
  const stillPresent = new Set(records.map((r) => r.id));
  for (const id of Array.from(godSeenInterventionIds)) {
    if (!stillPresent.has(id)) godSeenInterventionIds.delete(id);
  }
}

function renderDivineConsole() {
  updateGodModeGate();
  if (!GOD_MODE_ENABLED_FLAG) return;
  const key = JSON.stringify(world.god || null);
  if (key === godLastStateKey) return;
  godLastStateKey = key;
  renderGodPublicBanner();
  updateDivineBarAuthUi();
  if (godActiveTab === "history") renderGodHistory();
  if (godActiveTab === "laws" && !(godLastSight && godLastSight.activeEvents)) renderGodLawsActive();
}

// Kick off: render immediately (mock or last frame), poll on an interval.
syncPauseButton();
scheduleTerrainCacheBuild(); // speculative build via STARTER_DISTRICTS_JS (~10 ms)
requestAnimationFrame(tick);
pollState();
setInterval(pollState, STATE_POLL_MS);
pollDistricts();
setInterval(pollDistricts, DISTRICTS_POLL_MS);

