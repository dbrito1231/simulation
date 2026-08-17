"use strict";

// Load-perf instrumentation (docs/archive/plan-load-performance.md): set true to log
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
let PATH1_ENABLED = false;
let VISUALS_ENABLED = true;
let CHRONICLE_SAGA_ENABLED = true;
let PREDICTION_MARKET_ENABLED = true;
let ENV_EFFECTS_ENABLED = true;
let CROP_GROWTH_ENABLED = true;
let WILDLIFE_ENABLED = true;
let WEATHER_ENABLED = true;
let DYNASTY_TREE_ENABLED = true;
let RAIDERS_CONTAGION_ENABLED = true;

// --- Viewer-only display toggles (client-side, not server flags). Flipping
// either to true fully restores that section with no other edits. ---
const SHOW_CONVERSATIONS = false;
const SHOW_SETTLEMENTS = false;

