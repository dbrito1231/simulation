"use strict";

// Current season for seasonal sprite accents. Set by the viewer (setSpriteSeason)
// once per state poll. This and the viewer-controlled accent gate are the only
// mutable module state in this otherwise stateless file (documented exception
// -- see CLAUDE.md "pure, stateless").
let spriteSeason = "summer";
function setSpriteSeason(season) { spriteSeason = season || "summer"; }
let seasonalAgentAccentsEnabled = true;
function setSeasonalAgentAccentsEnabled(enabled) {
  seasonalAgentAccentsEnabled = enabled !== false;
}

const TILE = 16;

function tileFromStrings(rows, colorMap) {
  return rows.map((row) => {
    const cells = [];
    let i = 0;
    while (i < row.length) {
      if (row[i] === ".") {
        cells.push(null);
        i += 1;
        continue;
      }
      const three = row.slice(i, i + 3);
      const two = row.slice(i, i + 2);
      if (colorMap[three] !== undefined) {
        cells.push(colorMap[three]);
        i += 3;
      } else if (colorMap[two] !== undefined) {
        cells.push(colorMap[two]);
        i += 2;
      } else if (colorMap[row[i]] !== undefined) {
        cells.push(colorMap[row[i]]);
        i += 1;
      } else {
        cells.push(null);
        i += 1;
      }
    }
    return cells;
  });
}

function drawPixelGrid(ctx, originX, originY, grid, scale, flipX) {
  const h = grid.length;
  const w = grid.reduce((max, row) => Math.max(max, row.length), 0);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const color = grid[y][x];
      if (!color) continue;
      const px = flipX ? originX + (w - 1 - x) * scale : originX + x * scale;
      const py = originY + y * scale;
      ctx.fillStyle = color;
      ctx.fillRect(px, py, scale, scale);
    }
  }
}

function drawPixelSprite(ctx, cx, cy, grid, scale, flipX) {
  const w = grid[0].length * scale;
  const h = grid.length * scale;
  drawPixelGrid(ctx, Math.round(cx - w / 2), Math.round(cy - h + scale * 2), grid, scale, flipX);
}

// Tile→offscreen-canvas cache: render each 16×16 grid once, then repeat via
// createPattern on fill. Module tile constants key by object identity; ocean
// phases key through getOceanTile()'s stable memoized arrays.
const _tileSourceCanvasCache = new Map();

function createTileSourceCanvas(tile) {
  let canvas;
  if (typeof OffscreenCanvas !== "undefined") {
    canvas = new OffscreenCanvas(TILE, TILE);
  } else {
    canvas = document.createElement("canvas");
    canvas.width = TILE;
    canvas.height = TILE;
  }
  const tctx = canvas.getContext("2d");
  drawPixelGrid(tctx, 0, 0, tile, 1, false);
  return canvas;
}

function getTileSourceCanvas(tile) {
  let source = _tileSourceCanvasCache.get(tile);
  if (!source) {
    source = createTileSourceCanvas(tile);
    _tileSourceCanvasCache.set(tile, source);
  }
  return source;
}

function fillRectWithTile(ctx, x, y, w, h, tile) {
  const pattern = ctx.createPattern(getTileSourceCanvas(tile), "repeat");
  if (!pattern) return;
  ctx.save();
  ctx.translate(x, y);
  ctx.fillStyle = pattern;
  ctx.fillRect(0, 0, w, h);
  ctx.restore();
}

// Tiny deterministic hash -- NOT Math.random. Sprites (especially
// drawStructure) redraw every frame, so any per-draw randomness would
// flicker; this keeps the snow pattern stable for a given (x, step).
function snowHash(n) {
  return (Math.imul(n, 2654435761) >>> 0) % 8;
}

// Irregular snow clumps along a sprite's top edge, in place of a flat
// straight-line accent. Walks the width in `scale`-px steps; each step
// deterministically gets a 1- or 2-cell-tall clump, an occasional 1-step
// gap (bare edge), or a 1-cell overhang drooping below the edge line.
// (x, y, w) describe the sprite's drawn top-left corner and pixel width;
// `scale` is the sprite's per-cell pixel size.
function drawSnowCap(ctx, x, y, w, scale) {
  const step = Math.max(1, scale);
  const steps = Math.max(1, Math.round(w / step));
  ctx.fillStyle = C.sn;
  for (let i = 0; i < steps; i++) {
    const n = snowHash(Math.floor(x / step) + i);
    if (n === 0 || n === 4) continue; // 1-in-4 gap -- bare edge shows through
    const sx = x + i * step;
    const clumpH = n >= 6 ? 2 : 1; // occasional taller clump
    ctx.fillRect(sx, y, step, clumpH * step);
    if (n === 3) {
      // occasional overhang drooping a cell below the edge line
      ctx.fillRect(sx, y + clumpH * step, step, step);
    }
  }
}

// --- Path cells embedded in terrain (no brown overlay stripes) ---

const PATH_CELLS = new Set();

function markPathRect(x, y, w, h) {
  for (let py = y; py < y + h; py += TILE) {
    for (let px = x; px < x + w; px += TILE) {
      PATH_CELLS.add(`${px},${py}`);
    }
  }
}

// Generalizes the old 5 hardcoded connector strips into a loop over the
// SERVED road-edge list (world_expansion plan) -- founded districts' auto-
// generated connector edges render exactly the same way, with zero new code.
// Each edge is drawn as an L-shape (horizontal leg at nodeA's y, then a
// vertical leg at nodeB's x) so edges need not be axis-aligned themselves.
function markRoadEdges(edges, nodeCoords) {
  PATH_CELLS.clear();
  const half = 12;
  for (const edge of edges) {
    const a = nodeCoords[edge[0]];
    const b = nodeCoords[edge[1]];
    if (!a || !b) continue;
    const hx0 = Math.min(a.x, b.x) - half;
    const hw = Math.abs(b.x - a.x) + half * 2;
    markPathRect(hx0, a.y - half, hw, half * 2);
    const vy0 = Math.min(a.y, b.y) - half;
    const vh = Math.abs(b.y - a.y) + half * 2;
    markPathRect(b.x - half, vy0, half * 2, vh);
  }
}

// Starter road graph (mirrors sim_engine.py's STARTER_ROAD_NODES/EDGES) used
// as the initial fallback so the very first paint (before index.html's first
// /districts.js fetch resolves) still shows connecting paths. Real, possibly
// grown, live data from the server takes over via drawTiledWorld's roadNodes/
// roadEdges params on every subsequent frame. MUST be kept in sync with
// sim_engine.py's STARTER_ROAD_NODES/STARTER_ROAD_EDGES.
const STARTER_ROAD_NODES = {
  village_hub: { x: 740, y: 900 },
  farm_north_gate: { x: 740, y: 820 },
  forest_gate: { x: 1090, y: 460 },
  cave_east_gate: { x: 1270, y: 824 },
  beach_gate: { x: 400, y: 800 },
  market_gate: { x: 1040, y: 1000 },
  east_hub: { x: 1850, y: 900 },
  farm_south_gate: { x: 1850, y: 680 },
  village_east_gate: { x: 1850, y: 960 },
  workshop_row_gate: { x: 2300, y: 680 },
  cave_deep_gate: { x: 2300, y: 960 },
  cemetery_gate: { x: 380, y: 920 },
};
const STARTER_ROAD_EDGES = [
  ["farm_north_gate", "village_hub"],
  ["village_hub", "forest_gate"],
  ["village_hub", "cave_east_gate"],
  ["village_hub", "beach_gate"],
  ["beach_gate", "cemetery_gate"],
  ["village_hub", "market_gate"],
  ["village_hub", "east_hub"],
  ["east_hub", "farm_south_gate"],
  ["east_hub", "village_east_gate"],
  ["east_hub", "workshop_row_gate"],
  ["east_hub", "cave_deep_gate"],
];
markRoadEdges(STARTER_ROAD_EDGES, STARTER_ROAD_NODES);

const C = {
  g1: "#9bbf6a", g2: "#8aad5a", g3: "#7a9d4a",
  p1: "#c8a87a", p2: "#b8986a", p3: "#a8885a",
  o1: "#3daee9", o2: "#2d9ed9", o3: "#1d8ec9", ow: "#e8f8ff",
  s1: "#f5e6a3", s2: "#e5d693", s3: "#d5c683", sd: "#c4a574",
  f1: "#7ec850", f2: "#6eb840", f3: "#5ea830", fd: "#4a8828",
  fr1: "#2d6a2d", fr2: "#245a24", fr3: "#1a4a1a",
  v1: "#d4a96a", v2: "#c4995a", v3: "#b4894a",
  ts1: "#8a6a3a", ts2: "#6b4423",
  m1: "#c8874a", m2: "#b8773a", m3: "#a8672a", ma: "#ffae5e",
  cv1: "#555555", cv2: "#454545", cv3: "#353535", cv4: "#1a1a1a",
  k: "#111111", w: "#ffffff", br: "#8a5a2b", brd: "#5c3a1a",
  tr: "#6d4c2a", lf: "#3d8b37", lf2: "#2d6b27",
  fn: "#d4c4a0", rk: "#888888", rk2: "#666666",
  dk: "#6b4423", wl: "#4488cc",
  wk1: "#a8a89c", wk2: "#98988a", wk3: "#87877a", wk4: "#75756a",
  cm1: "#7a8a72", cm2: "#6a7a62", cm3: "#5a6a52",
  bl: "#e8a8c8", al1: "#c87830", al2: "#a05820", sn: "#eef4fa", st: "#9a7a48",
};

function makePathBlendTile(baseKeys) {
  const rows = [];
  for (let y = 0; y < TILE; y++) {
    const cells = [];
    for (let x = 0; x < TILE; x++) {
      const onStrip = x >= 6 && x <= 9;
      if (onStrip) {
        const pi = (x + y) % 3;
        cells.push(C[["p1", "p2", "p3"][pi]]);
      } else {
        cells.push(C[baseKeys[(x + y) % baseKeys.length]]);
      }
    }
    rows.push(cells);
  }
  return rows;
}

const PATH_BLEND_GRASS = makePathBlendTile(["g1", "g2", "g3"]);
const PATH_BLEND_BEACH = makePathBlendTile(["s1", "s2", "s3", "sd"]);
const PATH_BLEND_FARM = makePathBlendTile(["f1", "f2", "f3", "fd"]);
const PATH_BLEND_VILLAGE = makePathBlendTile(["v1", "v2", "v3"]);
const PATH_BLEND_CEMETERY = makePathBlendTile(["cm1", "cm2", "cm3"]);
const PATH_BLEND_BY_KIND = { farm: PATH_BLEND_FARM, village: PATH_BLEND_VILLAGE, beach: PATH_BLEND_BEACH, cemetery: PATH_BLEND_CEMETERY };

// Set by drawTiledWorld() before each pass of tile-fills so pathBlendForZone
// (called deep inside fillRectWithTiles) can look up "which district kind is
// this path tile inside" from the SERVED district list instead of the old
// hardcoded numeric ranges -- generalizes to any district, starter or founded.
let CURRENT_DISTRICTS_FOR_BLEND = [];

function pathBlendForZone(tx, ty) {
  for (const d of CURRENT_DISTRICTS_FOR_BLEND) {
    const b = d.bounds;
    if (tx >= b.x1 && tx < b.x2 && ty >= b.y1 && ty < b.y2) {
      return PATH_BLEND_BY_KIND[d.kind] || PATH_BLEND_GRASS;
    }
  }
  return PATH_BLEND_GRASS;
}

function fillRectWithTiles(ctx, x, y, w, h, baseTile, zoneHint) {
  fillRectWithTile(ctx, x, y, w, h, baseTile);
  const x2 = x + w;
  const y2 = y + h;
  const blendFn = zoneHint || (() => PATH_BLEND_GRASS);
  for (const key of PATH_CELLS) {
    const comma = key.indexOf(",");
    const tx = Number(key.slice(0, comma));
    const ty = Number(key.slice(comma + 1));
    if (tx < x || tx >= x2 || ty < y || ty >= y2) continue;
    fillRectWithTile(ctx, tx, ty, TILE, TILE, blendFn(tx, ty));
  }
}

function makeTile(colorKeys) {
  const rows = [];
  for (let y = 0; y < TILE; y++) {
    const cells = [];
    for (let x = 0; x < TILE; x++) {
      cells.push(C[colorKeys[(x + y) % colorKeys.length]]);
    }
    rows.push(cells);
  }
  return rows;
}

const TILE_GRASS = makeTile(["g1", "g2", "g3"]);
const TILE_PATH = makeTile(["p1", "p2", "p3"]);

const OCEAN_TILES_BY_PHASE = [];

function buildOceanTileGrid(foamOffset) {
  const rows = [];
  for (let y = 0; y < TILE; y++) {
    const cells = [];
    for (let x = 0; x < TILE; x++) {
      const wave = ((x + y + foamOffset) % 6) < 2;
      if (y < 2 && wave) cells.push(C.ow);
      else if ((x + y) % 4 === 0) cells.push(C.o2);
      else cells.push(C.o1);
    }
    rows.push(cells);
  }
  return rows;
}

function getOceanTile(phase) {
  const p = ((Math.floor(phase) % 16) + 16) % 16;
  if (!OCEAN_TILES_BY_PHASE[p]) {
    OCEAN_TILES_BY_PHASE[p] = buildOceanTileGrid(p);
  }
  return OCEAN_TILES_BY_PHASE[p];
}

function oceanTile(foamOffset) {
  return getOceanTile(foamOffset);
}

const TILE_BEACH = makeTile(["s1", "s2", "s3", "sd"]);
const TILE_FARM = makeTile(["f1", "f2", "f3", "fd"]);
const TILE_FOREST_FLOOR = makeTile(["fr1", "fr2", "fr3"]);
const TILE_VILLAGE = makeTile(["v1", "v2", "v3"]);
const TILE_MARKET = makeTile(["m1", "m2", "m3", "ma"]);
const TILE_CAVE = makeTile(["cv1", "cv2", "cv3"]);
const TILE_WORKSHOP = makeTile(["wk1", "wk2", "wk3", "wk4"]);
const TILE_CEMETERY = makeTile(["cm1", "cm2", "cm3"]);

// Season+stage-keyed cache of built tree grids -- built once per key on
// first use, never rebuilt per draw call (drawTree is called many times per
// terrain-cache build). "summer"/"lush" rows are byte-identical to the
// original (pre-seasonal, pre-ecology-stage) tree art. Stage is the Phase 2
// living-ecosystem addition (CROP_GROWTH_ENABLED): keying the cache by
// `${season}|${stage}` means a stage change invalidates only the tree grids,
// same mechanism the pre-existing season keying already used.
const TREE_GRIDS = {};

function buildTreeGrid(season, stage = "lush") {
  let rows;
  if (season === "autumn") {
    rows = [
      "....al1al2al1....",
      "...al2al1al2al1...",
      "..al1al2al1al2al1.",
      ".al2al1al2al1al2al2",
      "al2al1al2al1al2al1",
      "al1...trtrtrtr....",
      "....trtrtrtr...al1",
      "..al1.trtrtrtr....",
    ];
  } else if (season === "winter") {
    // Snow follows the rounded canopy shape rather than a wholesale color
    // swap: fully covered at the narrow top, tapering to scattered
    // drip/clump cells lower down, and untouched canopy/trunk below that.
    rows = [
      "....snsnsn....",
      "...lf2snsnlf2...",
      "..lf2snlf2snlf2.",
      ".lf2lf2lf2lf2lf2lf",
      "lf2lf2lf2lf2lf2lf2",
      "....trtrtrtr....",
      "....trtrtrtr....",
      "....trtrtrtr....",
    ];
  } else if (season === "spring") {
    rows = [
      "....lf2bllf2....",
      "...lf2lf2bllf2...",
      "..lf2lf2lf2lf2bl.",
      ".lf2lf2lf2lf2lf2lf",
      "lf2lf2lf2lf2lf2lf2",
      "....trtrtrtr....",
      "....trtrtrtr....",
      "....trtrtrtr....",
    ];
  } else {
    // summer (default/fallback) -- rows verbatim from the original art.
    rows = [
      "....lf2lf2lf2....",
      "...lf2lf2lf2lf2...",
      "..lf2lf2lf2lf2lf2.",
      ".lf2lf2lf2lf2lf2lf",
      "lf2lf2lf2lf2lf2lf2",
      "....trtrtrtr....",
      "....trtrtrtr....",
      "....trtrtrtr....",
    ];
  }
  if (stage === "sparse") {
    // Thinned canopy for a stock-starved forest: drop the widest top rows
    // so the remaining trees read visibly smaller than a healthy/lush
    // canopy of the same season. "healthy" and "lush" both use the
    // unmodified rows above -- only "sparse" gets a distinct grid.
    rows = rows.slice(2);
  }
  return tileFromStrings(rows, C);
}

function drawTree(ctx, x, y, season = "summer", stage = "lush") {
  const key = season + "|" + stage;
  const grid = TREE_GRIDS[key] || (TREE_GRIDS[key] = buildTreeGrid(season, stage));
  drawPixelGrid(ctx, x - 12, y - 12, grid, 3, false);
}

// Barren-forest marker (Phase 2 living-ecosystem, CROP_GROWTH_ENABLED): a
// small stump/sapling in place of a full canopy tree when a forest cell's
// stage is "barren" or is thinned out at "sparse". Deliberately tiny/plain
// so it reads as "not grown back yet" rather than a new prop type.
function drawTreeStump(ctx, x, y) {
  ctx.fillStyle = C.tr || "#5D4037";
  ctx.fillRect(x - 3, y + 4, 6, 5);
  ctx.fillStyle = C.lf2 || "#66BB6A";
  ctx.fillRect(x - 1, y, 2, 5);
}

function drawHouse(ctx, x, y, season = "summer") {
  const house = tileFromStrings([
    "..brdbrdbrdbrd..",
    ".brdbrdbrdbrdbrd.",
    "brbrbrbrbrbrbrbr",
    "brw..brbr..wbrbr",
    "brw..brbr..wbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
  ], C);
  drawPixelGrid(ctx, x, y, house, 4, false);
  if (season === "winter") {
    // Clumped snow accumulation along the roof's top edge.
    const scale = 4;
    const width = house.reduce((max, row) => Math.max(max, row.length), 0) * scale;
    drawSnowCap(ctx, x, y, width, scale);
  }
}

function drawMarketStall(ctx, x, y) {
  const stall = tileFromStrings([
    "mamamamamamamama",
    "m1m1m1m1m1m1m1m1",
    "m1w..m1m1..wm1m1",
    "m1m1m1m1m1m1m1m1",
    "m2m2m2m2m2m2m2m2",
    "m2m2m2m2m2m2m2m2",
  ], C);
  drawPixelGrid(ctx, x, y, stall, 3, false);
}

function drawCaveEntrance(ctx, x, y) {
  ctx.fillStyle = C.cv4;
  ctx.beginPath();
  ctx.arc(x, y, 40, Math.PI, 0, false);
  ctx.fill();
  ctx.fillRect(x - 40, y, 80, 50);
  ctx.fillStyle = C.k;
  ctx.beginPath();
  ctx.arc(x, y + 6, 28, Math.PI, 0, false);
  ctx.fill();
  ctx.strokeStyle = C.cv1;
  ctx.lineWidth = 3;
  ctx.stroke();
}

// stage is the Phase 2 living-ecosystem addition (CROP_GROWTH_ENABLED):
// "lush" (default) reproduces the original season-only art byte-for-byte, so
// flag-off / unspecified-stage callers are unaffected. Winter stubble and
// spring shoots are left as-is regardless of stage -- both already read as
// "not full growth", and layering stage on top would be visually redundant.
function drawCrop(ctx, x, y, season = "summer", stage = "lush") {
  if (season === "winter") {
    // Stubble: short, shortened brown rows -- no live green top.
    ctx.fillStyle = C.st;
    ctx.fillRect(x, y + 2, 2, 3);
    ctx.fillStyle = C.st;
    ctx.fillRect(x - 1, y + 3, 4, 2);
    return;
  }
  if (season === "spring") {
    // Young shoots: brighter, smaller than the full summer crop.
    ctx.fillStyle = C.fd;
    ctx.fillRect(x, y + 1, 2, 5);
    ctx.fillStyle = C.f1;
    ctx.fillRect(x - 2, y - 1, 6, 3);
    return;
  }
  if (stage === "barren") {
    // Bare soil: a small dirt clod, no live growth at all.
    ctx.fillStyle = C.st;
    ctx.fillRect(x - 1, y + 3, 3, 2);
    return;
  }
  if (stage === "sparse") {
    // A single thin blade -- visibly smaller than the mature crop below.
    ctx.fillStyle = C.fd;
    ctx.fillRect(x, y + 2, 1, 3);
    ctx.fillStyle = C.f1;
    ctx.fillRect(x - 1, y + 1, 2, 2);
    return;
  }
  ctx.fillStyle = C.fd;
  ctx.fillRect(x, y, 2, 6);
  ctx.fillStyle = C.f1;
  ctx.fillRect(x - 2, y - 2, 6, 4);
}

// Density scaler shared by both starter farm patches: at "lush" (default)
// this reproduces the original per-site modulo exactly (byte-identical
// rendering when CROP_GROWTH_ENABLED is off, since callers always pass
// stage="lush" in that case). Lower stages thin the crop grid out; "barren"
// draws no crop cells at all -- the bare farm tile fill alone reads as
// empty soil, so drawCrop's own "barren" clod branch above is reachable
// only via direct calls, not through this density gate.
const CROP_STAGE_DENSITY_SCALE = { barren: null, sparse: 2.5, healthy: 1.5, lush: 1 };
function shouldDrawCrop(fx, fy, stage, baseMod) {
  const scale = CROP_STAGE_DENSITY_SCALE[stage];
  if (scale == null) return false;
  const mod = Math.max(1, Math.round(baseMod * scale));
  return (fx + fy) % mod === 0;
}

// --- World props ---

function drawFence(ctx, x, y, season = "summer") {
  const fence = tileFromStrings([
    "fnfnfnfnfnfnfnfn",
    "dkdkdkdkdkdkdkdk",
    "fnfnfnfnfnfnfnfn",
    "dkdkdkdkdkdkdkdk",
  ], C);
  drawPixelGrid(ctx, x, y, fence, 1, false);
  if (season === "winter") {
    const scale = 1;
    const width = fence.reduce((max, row) => Math.max(max, row.length), 0) * scale;
    drawSnowCap(ctx, x, y, width, scale);
  }
}

function drawDock(ctx, x, y) {
  // A horizontal wooden jetty reaching from the beach out over the water,
  // large enough to read clearly at map scale (12 cells wide at scale 6 = 72px).
  const dock = tileFromStrings([
    "brdbrdbrdbrdbrdbrdbrdbrdbrdbrdbrdbrd",
    "brbrbrbrbrbrbrbrbrbrbrbr",
    "brdkbrdkbrdkbrdkbrdkbrdk",
    "brbrbrbrbrbrbrbrbrbrbrbr",
    "brdbrdbrdbrdbrdbrdbrdbrdbrdbrdbrdbrd",
    "k..k..k..k..k..k..k..k..",
  ], C);
  drawPixelGrid(ctx, x, y, dock, 6, false);
}

function drawWell(ctx, x, y, season = "summer") {
  const well = tileFromStrings([
    "..cv1cv1cv1cv1..",
    ".cv1cv1cv1cv1cv1.",
    "cv1cv1wlwlcv1cv1",
    "cv1cv1wlwlcv1cv1",
    ".cv1cv1cv1cv1cv1.",
    "..cv1cv1cv1cv1..",
  ], C);
  drawPixelGrid(ctx, x, y, well, 4, false);
  if (season === "winter") {
    const scale = 4;
    const width = well.reduce((max, row) => Math.max(max, row.length), 0) * scale;
    drawSnowCap(ctx, x, y, width, scale);
  }
}

function drawRocks(ctx, x, y, season = "summer") {
  const rocks = tileFromStrings([
    "..rk2rk2....",
    ".rk2rk2rk2..",
    "rk2rk2rk2rk2",
    ".rk2rk2rk2..",
    "..rk2rk2....",
  ], C);
  const rocksOriginX = x - 9;
  const rocksOriginY = y - 6;
  drawPixelGrid(ctx, rocksOriginX, rocksOriginY, rocks, 3, false);
  if (season === "winter") {
    const scale = 3;
    const width = rocks.reduce((max, row) => Math.max(max, row.length), 0) * scale;
    drawSnowCap(ctx, rocksOriginX, rocksOriginY, width, scale);
  }
}

// --- Agent-built structures ---

const STRUCTURE_GRIDS = {
  house: tileFromStrings([
    "..brdbrdbrdbrd..",
    "brdbrdbrdbrdbrdbrdbrdbrd",
    "brbrbrbrbrbrbrbr",
    "brwbrbrbrbrwbr",
    "brwbrbrbrbrwbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
  ], C),
  farm_plot: tileFromStrings([
    "ts2ts1ts2ts1ts2ts1ts2ts1",
    "ts1ts2ts1ts2ts1ts2ts1ts2",
    "ts2f3ts2f3ts2f3ts2f3",
    "f3ts2f3ts2f3ts2f3ts2",
    "ts1ts2ts1ts2ts1ts2ts1ts2",
    "ts2ts1ts2ts1ts2ts1ts2ts1",
  ], C),
  wall: tileFromStrings([
    "cv1cv1cv1cv1cv1cv1",
    "cv2cv2cv2cv2cv2cv2",
    "cv1cv1cv1cv1cv1cv1",
    "cv2cv2cv2cv2cv2cv2",
    "cv1cv1cv1cv1cv1cv1",
    "cv2cv2cv2cv2cv2cv2",
  ], C),
  workshop: tileFromStrings([
    "..brdbrdbrdbrd..",
    "brdbrdbrdbrdbrdbrdbrdbrd",
    "brbrbrbrbrbrbrbr",
    "brmamamamamabrbr",
    "brm1m1m1m1m1brbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
  ], C),
  cemetery: tileFromStrings([
    "fnfnfnfnfnfn",
    "g1g1g1g1g1g1",
    "g1g1rkrkg1g1",
    "g1rkowowrkg1",
    "g2g2g2g2g2g2",
    "fnfnfnfnfnfn",
  ], C),
};

// A collapsed silhouette deliberately shared by every ruined structure. It is
// visually distinct from a darkened intact building and keeps the sprite art
// legible even for custom blueprint types with no seed grid.
const RUIN_GRID = tileFromStrings([
  "................",
  "....br..........",
  "..brbr..s1......",
  ".dkbrs1br..dk...",
  "br..dk..brbr....",
  ".g1....g1....g1.",
], C);

function colorFromId(id) {
  const str = String(id || "structure");
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 31 + str.charCodeAt(i)) & 0xffffff;
  }
  const hue = hash % 360;
  return `hsl(${hue}, 55%, 55%)`;
}

// Built structures render larger than agent sprites (agents are 16x16 cells
// at scale 2 = 32x32px) so a house reads as a building, not a doll-sized prop.
const STRUCTURE_SCALE = 5;

// Canonical silhouette for the high-level seed house. Level 30 houses may
// carry an LLM-authored sprite in persisted state, but those sprites can be
// attractive blobs without the visual landmarks that make a house readable.
// Keep the built-in house recognizable: pitched roof, chimney, two windows,
// walls, and a centered door.
function makeLevel30HouseGrid() {
  const width = 17;
  const height = 14;
  const grid = Array.from({ length: height }, () => Array(width).fill(null));
  const roof = C.br;
  const roofLight = C.m1;
  const wall = C.s1;
  const outline = C.brd;
  const window = C.o1;
  const windowLight = C.ow;
  const door = C.dk;

  // Pitched roof with a chimney on the right.
  for (let y = 0; y <= 6; y++) {
    const left = 8 - y;
    const right = 8 + y;
    for (let x = left; x <= right; x++) grid[y][x] = roof;
  }
  grid[0][11] = outline;
  grid[1][11] = outline;
  grid[2][11] = outline;
  for (let x = 1; x < width - 1; x++) grid[6][x] = roofLight;

  // Façade, with a dark outline and warm wall fill.
  for (let y = 7; y < height; y++) {
    for (let x = 1; x < width - 1; x++) {
      grid[y][x] = (x === 1 || x === width - 2 || y === height - 1)
        ? outline : wall;
    }
  }

  // Window frames and panes.
  for (const start of [3, 11]) {
    for (let y = 9; y <= 10; y++) {
      for (let x = start; x < start + 3; x++) grid[y][x] = window;
    }
    grid[9][start + 1] = windowLight;
    grid[10][start + 1] = windowLight;
  }

  // Centered door and a small brass handle.
  for (let y = 11; y < height; y++) {
    for (let x = 7; x <= 9; x++) grid[y][x] = door;
  }
  grid[12][8] = C.ma;
  return grid;
}

const LEVEL30_HOUSE_GRID = makeLevel30HouseGrid();

// LLM-authored sprites: blueprints may carry {palette: ["#RRGGBB",...],
// grid: [".aab.", ...]} (validated server-side). Convert to the color-row
// format drawPixelGrid consumes; cache per structure type.
const _specGridCache = new Map();
function spriteGridFromSpec(typeId, spec) {
  if (!spec || !Array.isArray(spec.palette) || !Array.isArray(spec.grid)) return null;
  const key = typeId || JSON.stringify(spec.grid);
  if (_specGridCache.has(key)) return _specGridCache.get(key);
  let grid = null;
  try {
    grid = spec.grid.map((row) => Array.from(String(row)).map((ch) => {
      if (ch === ".") return null;
      const idx = ch.charCodeAt(0) - 97;
      return spec.palette[idx] || null;
    }));
    if (!grid.length || grid.every((r) => r.every((c) => !c))) grid = null;
  } catch (e) { grid = null; }
  _specGridCache.set(key, grid);
  return grid;
}

// Procedural sprite for customs with no LLM sprite (incl. pre-sprite saves):
// a deterministic little building composed from the type id's hash, so every
// invention looks distinct and the letter-in-a-box fallback never shows.
const _PROC_PALETTES = [
  ["#8B5A2B", "#C62828", "#F5E6C8"], ["#78909C", "#37474F", "#FFD54F"],
  ["#A1887F", "#4E342E", "#AED581"], ["#90A4AE", "#B71C1C", "#E3F2FD"],
  ["#BCAAA4", "#33691E", "#FFF176"], ["#D7CCC8", "#1565C0", "#FFAB91"],
  ["#795548", "#F9A825", "#B3E5FC"], ["#607D8B", "#6A1B9A", "#DCEDC8"],
];
const _procGridCache = new Map();
function proceduralGridForStructure(structure) {
  const key = structure.type || structure.name || "?";
  if (_procGridCache.has(key)) return _procGridCache.get(key);
  let h = 0;
  for (let i = 0; i < key.length; i++) h = ((h << 5) - h + key.charCodeAt(i)) | 0;
  h = Math.abs(h);
  const [wall, roof, accent] = _PROC_PALETTES[h % _PROC_PALETTES.length];
  const W = 10, H = 9, roofStyle = (h >> 3) % 3, winStyle = (h >> 5) % 3;
  const chimney = ((h >> 7) % 2) === 0;
  const grid = [];
  for (let y = 0; y < H; y++) {
    const row = [];
    for (let x = 0; x < W; x++) {
      let c = null;
      if (y < 3) {
        if (roofStyle === 0) c = roof;
        else if (roofStyle === 1) { const inset = 2 - y; c = (x >= inset && x < W - inset) ? roof : null; }
        else { const mid = W / 2, spread = y * 2 + 2; c = Math.abs(x - mid + 0.5) < spread / 2 ? roof : null; }
        if (chimney && y === 0 && x === W - 3) c = wall;
      } else {
        c = wall;
        if (y >= 6 && x >= 4 && x <= 5) c = accent;
        else if (winStyle === 0 && y === 4 && (x === 2 || x === 7)) c = accent;
        else if (winStyle === 1 && y % 2 === 0 && (x === 1 || x === 8)) c = accent;
        else if (winStyle === 2 && y === 4 && x >= 2 && x <= 7 && x % 2 === 0) c = accent;
      }
      row.push(c);
    }
    grid.push(row);
  }
  _procGridCache.set(key, grid);
  return grid;
}

function structureRenderScale(structure) {
  const s = Number(structure && structure.renderScale);
  return Number.isFinite(s) && s > 0 ? s : 1;
}

function spriteSpecIsDegenerate(spec) {
  if (!spec || !Array.isArray(spec.grid)) return true;
  const counts = {};
  let total = 0;
  const colorsUsed = new Set();
  for (const row of spec.grid) {
    for (const ch of String(row)) {
      if (ch === ".") continue;
      counts[ch] = (counts[ch] || 0) + 1;
      total++;
      const idx = ch.charCodeAt(0) - 97;
      if (spec.palette && spec.palette[idx]) colorsUsed.add(spec.palette[idx]);
    }
  }
  if (total < 4) return true;
  if (colorsUsed.size < 2) return true;
  const maxCount = Math.max(...Object.values(counts));
  return maxCount / total > 0.82;
}

function upscaleColorGrid(grid, factor) {
  if (!grid || factor <= 1) return grid;
  const out = [];
  for (let y = 0; y < grid.length; y++) {
    for (let sy = 0; sy < factor; sy++) {
      const row = [];
      for (let x = 0; x < grid[y].length; x++) {
        const c = grid[y][x];
        for (let sx = 0; sx < factor; sx++) row.push(c);
      }
      out.push(row);
    }
  }
  return out;
}

function upgradedSeedGrid(structure) {
  const base = STRUCTURE_GRIDS[structure.type];
  if (!base) return null;
  const tier = Math.max(1, Number(structure.visualTier) || 1);
  const factor = Math.min(tier, 3);
  return factor > 1 ? upscaleColorGrid(base, factor) : base;
}

function structureIsUpgraded(structure) {
  const lvl = Number(structure && structure.level);
  const vt = Number(structure && structure.visualTier);
  return (Number.isFinite(lvl) && lvl > 1) || (Number.isFinite(vt) && vt > 1);
}

function structureConditionTier(structure) {
  if (!structure) return "pristine";
  const supplied = structure.conditionTier;
  if (["pristine", "worn", "crumbling", "ruin"].includes(supplied)) return supplied;
  const condition = Number(structure.condition);
  if (structure.isRuin || (Number.isFinite(condition) && condition <= 0)) return "ruin";
  if (!Number.isFinite(condition)) return "pristine"; // older snapshots
  if (condition < 30) return "crumbling";
  if (condition < 60) return "worn";
  return "pristine";
}

function structureRenderGrid(structure, wearEnabled = true) {
  if (wearEnabled && structureConditionTier(structure) === "ruin") return RUIN_GRID;
  return getStructureGrid(structure);
}

function getStructureGrid(structure) {
  const upgraded = structureIsUpgraded(structure);
  // Seed houses need a stable architectural silhouette at the milestone
  // level. Do not let a persisted LLM sprite replace the pitched roof and
  // façade that identify this built-in structure as a house.
  if (structure.type === "house" && Number(structure.level) >= 30) {
    return LEVEL30_HOUSE_GRID;
  }
  // Upgraded seed types store a bigger sprite on the instance — prefer it
  // when it has real detail; flat gray LLM blobs fall back to upscaled seeds.
  if (upgraded && structure.sprite && !spriteSpecIsDegenerate(structure.sprite)) {
    const cacheId = structure.id != null
      ? `${structure.type}:${structure.id}`
      : structure.type;
    const upgradedGrid = spriteGridFromSpec(cacheId, structure.sprite);
    if (upgradedGrid) return upgradedGrid;
  }
  if (upgraded) {
    const seedGrid = upgradedSeedGrid(structure);
    if (seedGrid) return seedGrid;
  }
  let grid = STRUCTURE_GRIDS[structure.type];
  if (grid) return grid;
  if (structure.sprite) {
    grid = spriteGridFromSpec(structure.type, structure.sprite);
    if (grid) return grid;
  }
  if (structure.visualStyle && structure.visualStyle !== "generic") {
    grid = STRUCTURE_GRIDS[structure.visualStyle];
    if (grid) return grid;
  }
  return proceduralGridForStructure(structure);
}

// Pixel footprint of a structure's sprite, used by index.html to place the
// shadow and name label regardless of grid size or fallback type.
function getStructureRenderSize(structure, wearEnabled = true) {
  const grid = structureRenderGrid(structure, wearEnabled);
  const scale = STRUCTURE_SCALE * structureRenderScale(structure);
  if (!grid) return { width: 8 * scale, height: 8 * scale };
  const width = grid.reduce((max, row) => Math.max(max, row.length), 0) * scale;
  return { width, height: grid.length * scale };
}

// Fallback for custom blueprints with no built-in sprite: a simple block
// with the structure's first letter in a deterministic accent color.
function drawGenericStructure(ctx, x, y, label, accentColor) {
  const scale = STRUCTURE_SCALE;
  const size = 8 * scale;
  ctx.fillStyle = accentColor;
  ctx.fillRect(x, y, size, size);
  ctx.fillStyle = "#1a1a1a";
  ctx.fillRect(x, y, size, scale);
  ctx.fillRect(x, y, scale, size);
  ctx.fillRect(x + size - scale, y, scale, size);
  ctx.fillRect(x, y + size - scale, size, scale);
  const letter = (String(label || "?").charAt(0) || "?").toUpperCase();
  ctx.fillStyle = "#ffffff";
  ctx.font = `bold ${size - scale * 2}px monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(letter, x + size / 2, y + size / 2 + 1);
  ctx.textBaseline = "alphabetic";
}

function drawStructure(ctx, structure, wearEnabled = true) {
  const grid = structureRenderGrid(structure, wearEnabled);
  const scale = STRUCTURE_SCALE * structureRenderScale(structure);
  if (grid) {
    drawPixelGrid(ctx, structure.x, structure.y, grid, scale, false);
    if (wearEnabled) drawStructureWear(ctx, structure, grid, scale);
    // Cheap per-frame winter accent for agent-built structures: clumped
    // snow along the sprite's top edge. spriteSeason is the module-level
    // mirror of the viewer's current season (set via setSpriteSeason) --
    // this is the only place in this file that reads it; every other
    // seasonal branch takes an explicit `season` parameter instead.
    if (spriteSeason === "winter") {
      const width = grid.reduce((max, row) => Math.max(max, row.length), 0) * scale;
      drawSnowCap(ctx, structure.x, structure.y, width, Math.max(1, scale));
    }
    return;
  }
  drawGenericStructure(
    ctx, structure.x, structure.y,
    structure.name || structure.type,
    colorFromId(structure.type)
  );
}

function deterministicSeed(value) {
  const str = String(value == null ? "structure" : value);
  let hash = 2166136261;
  for (let i = 0; i < str.length; i++) {
    hash ^= str.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function drawStructureWear(ctx, structure, grid, scale) {
  const tier = structureConditionTier(structure);
  if (tier === "pristine" || tier === "ruin") return;
  const width = grid.reduce((max, row) => Math.max(max, row.length), 0) * scale;
  const height = grid.length * scale;
  const x = structure.x, y = structure.y;
  const seed = deterministicSeed(structure.id != null ? structure.id : structure.type);
  ctx.save();
  // source-atop keeps the wash constrained to painted sprite pixels.
  ctx.globalCompositeOperation = "source-atop";
  ctx.fillStyle = tier === "worn" ? "rgba(90, 95, 86, 0.25)" : "rgba(28, 25, 23, 0.42)";
  ctx.fillRect(x, y, width, height);
  ctx.restore();

  if (tier === "worn") {
    ctx.fillStyle = "rgba(45, 40, 34, 0.48)";
    for (let i = 0; i < 3; i++) {
      const px = x + ((seed >>> (i * 5)) % Math.max(1, Math.floor(width / scale))) * scale;
      const py = y + ((i % 2 ? grid.length - 1 : 1) * scale);
      ctx.fillRect(px, py, scale, scale);
    }
    return;
  }
  ctx.fillStyle = "rgba(42, 30, 26, 0.86)";
  const cracks = 4;
  for (let i = 0; i < cracks; i++) {
    const px = x + ((seed >>> (i * 6)) % Math.max(1, Math.floor(width / scale))) * scale;
    const py = y + (1 + ((seed >>> (i * 4 + 2)) % Math.max(1, grid.length - 2))) * scale;
    ctx.fillRect(px, py, scale, scale * 2);
    if (i % 2 === 0) ctx.fillRect(px - scale, py + scale, scale, scale);
  }
  ctx.fillRect(x, y + height - scale, scale * 2, scale);
  ctx.fillRect(x + width - scale * 2, y + height - scale, scale * 2, scale);
}

const HEAT_CRAFT_TYPES = /(?:forge|kiln|furnace|smelter|charcoal)/;

function drawStructureSmoke(ctx, structure, frameTick) {
  if (!HEAT_CRAFT_TYPES.test(String(structure.type || "").toLowerCase())) return;
  const tier = structureConditionTier(structure);
  if (tier === "ruin" || tier === "crumbling" || Number(structure.condition) < 30) return;
  const phase = Math.floor(frameTick / 9);
  const seed = deterministicSeed(structure.id != null ? structure.id : structure.type);
  const baseX = structure.x + 20 + (seed % 18);
  const baseY = structure.y - 4;
  ctx.save();
  for (let i = 0; i < 3; i++) {
    const age = (phase + i * 3 + (seed % 7)) % 12;
    const size = 3 + age * 0.45;
    ctx.globalAlpha = Math.max(0, 0.28 - age * 0.018);
    ctx.fillStyle = "#d8d2c5";
    ctx.fillRect(Math.round(baseX + ((seed >>> (i * 4)) % 7) - age * 0.35),
                 Math.round(baseY - age * 2.2), Math.ceil(size), Math.ceil(size));
  }
  ctx.restore();
}

// Weather particles (living-ecosystem Phase 4, WEATHER_ENABLED): a single
// rain streak or snow dot. Pure per-call draw, no retained state -- the
// caller (drawWeatherParticles, index.html) derives x/y deterministically
// from frameTick each frame.
function drawWeatherParticle(ctx, kind, x, y, index) {
  ctx.save();
  if (kind === "snow") {
    const drift = Math.sin((x + index) * 0.05) * 1.5;
    ctx.fillStyle = "rgba(235, 245, 255, 0.75)";
    ctx.beginPath();
    ctx.arc(Math.round(x + drift), Math.round(y), 1.6, 0, Math.PI * 2);
    ctx.fill();
  } else {
    ctx.strokeStyle = "rgba(190, 210, 230, 0.55)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(x), Math.round(y));
    ctx.lineTo(Math.round(x - 2), Math.round(y + 10));
    ctx.stroke();
  }
  ctx.restore();
}

function drawActivityDust(ctx, agent, frameTick) {
  if (!["build_structure", "contribute_resources", "start_project"].includes(agent.lastAction)) return;
  const phase = Math.floor(frameTick / 5) % 8;
  if (phase > 3) return; // brief, looping puffs without retaining client state
  const seed = deterministicSeed(agent.id != null ? agent.id : agent.name);
  ctx.save();
  ctx.globalAlpha = 0.34 - phase * 0.06;
  ctx.fillStyle = "#c9ad78";
  for (let i = 0; i < 3; i++) {
    const size = 2 + phase;
    ctx.fillRect(Math.round(agent.x - 8 + i * 6 + ((seed >>> (i * 3)) % 3)),
                 Math.round(agent.y + 2 - phase - (i % 2)), size, Math.max(2, size - 1));
  }
  ctx.restore();
}

// --- Agent sprites (16x24), unique per agent ---

const SKIN = "#FDBCB4";
const OUT = "#111111";
const SHOE = "#333333";

function makeAgentPalette(main, accent, extra) {
  return {
    ".": null,
    k: OUT,
    s: SKIN,
    m: main,
    a: accent,
    e: extra || main,
    h: SHOE,
  };
}

function makeStand(rows, palette) {
  return tileFromStrings(rows, palette);
}

function makeWalk(rows, palette) {
  return tileFromStrings(rows, palette);
}

function buildAgentSprite(palette, standRows, walkRows) {
  return {
    stand: makeStand(standRows, palette),
    walk: makeWalk(walkRows, palette),
  };
}

const AGENT_SPRITES = {
  Aria: buildAgentSprite(makeAgentPalette("#4CAF50", "#FFD54F", "#8D6E63"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Marco: buildAgentSprite(makeAgentPalette("#FF9800", "#FFC107", "#795548"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmaamk..",
    "..kmmmmmaamk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmaamk..",
    "..kmmmmmaamk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Zara: buildAgentSprite(makeAgentPalette("#9C27B0", "#CE93D8", "#607D8B"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Rex: buildAgentSprite(makeAgentPalette("#F44336", "#B71C1C", "#9E9E9E"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Luna: buildAgentSprite(makeAgentPalette("#2196F3", "#90CAF9", "#795548"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Finn: buildAgentSprite(makeAgentPalette("#00BCD4", "#4DD0E1", "#1565C0"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Mia: buildAgentSprite(makeAgentPalette("#E91E63", "#F48FB1", "#FFFFFF"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Colt: buildAgentSprite(makeAgentPalette("#795548", "#A1887F", "#FFD54F"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Ivy: buildAgentSprite(makeAgentPalette("#8BC34A", "#558B2F", "#33691E"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Dex: buildAgentSprite(makeAgentPalette("#607D8B", "#90A4AE", "#455A64"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Nova: buildAgentSprite(makeAgentPalette("#FF5722", "#FFAB91", "#BF360C"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
  Sage: buildAgentSprite(makeAgentPalette("#FFC107", "#FFF176", "#8D6E63"), [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "...kmm..mmk....",
    "...kmm..mmk....",
    "...khh..hhk....",
    "...khh..hhk....",
    "....hh..hh....",
  ], [
    "....kkkkkkkk....",
    "...kaaaaaaaa...",
    "..kaaaaaaaaaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "..kaassssskaa..",
    "...kmmmmmmk....",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "..kmmmmmmmmk..",
    "...kmmmmmmk....",
    "..kmm....mmk..",
    ".kmm......mmk.",
    ".khh......hhk.",
    "..khh....hhk..",
    "...hh....hh...",
  ]),
};

// Generic fallback body for any agent name not in AGENT_SPRITES above (e.g.
// Phase F/G newcomers/births -- "Villager1000" etc -- created after the fixed
// roster was hand-drawn). Every hand-drawn entry above shares this exact body
// shape and only varies by color palette, so reusing it here + palette-izing
// off the agent's own `color` field gives every future agent a real sprite
// instead of silently rendering nothing (see drawAgentSprite below -- the
// same "no more decorative fallback" principle as drawGenericStructure).
const GENERIC_AGENT_STAND = [
  "....kkkkkkkk....",
  "...kaaaaaaaa...",
  "..kaaaaaaaaaa..",
  "..kaassssskaa..",
  "..kaassssskaa..",
  "..kaassssskaa..",
  "...kmmmmmmk....",
  "..kmmmmmmmmk..",
  "..kmmmmmmmmk..",
  "..kmmmmmmmmk..",
  "...kmmmmmmk....",
  "...kmm..mmk....",
  "...kmm..mmk....",
  "...khh..hhk....",
  "...khh..hhk....",
  "....hh..hh....",
];
const GENERIC_AGENT_WALK = [
  "....kkkkkkkk....",
  "...kaaaaaaaa...",
  "..kaaaaaaaaaa..",
  "..kaassssskaa..",
  "..kaassssskaa..",
  "..kaassssskaa..",
  "...kmmmmmmk....",
  "..kmmmmmmmmk..",
  "..kmmmmmmmmk..",
  "..kmmmmmmmmk..",
  "...kmmmmmmk....",
  "..kmm....mmk..",
  ".kmm......mmk.",
  ".khh......hhk.",
  "..khh....hhk..",
  "...hh....hh...",
];
const _genericAgentSpriteCache = {};
function genericAgentSprite(agent) {
  const cached = _genericAgentSpriteCache[agent.name];
  if (cached && cached.color === agent.color) return cached.sprite;
  const palette = makeAgentPalette(agent.color || "#9E9E9E", "#FFD54F", "#8D6E63");
  const sprite = buildAgentSprite(palette, GENERIC_AGENT_STAND, GENERIC_AGENT_WALK);
  _genericAgentSpriteCache[agent.name] = { color: agent.color, sprite };
  return sprite;
}

// Tombstone: replaces the agent body entirely (not an overlay -- see
// drawAgentSprite below) once a permanent death (CEMETERY_ENABLED/
// LIFECYCLE_ENABLED, `agent.deceased`) is confirmed. Grey stone arch on a
// grass mound, sized to the same 16x16 footprint as every living sprite so
// drawPixelSprite's existing origin math needs no changes. The small moss
// tuft is tinted with the agent's own color as a quiet personal touch, but
// the stone itself stays a uniform grey regardless of who's buried.
const _tombstoneSpriteCache = {};
function tombstoneSprite(agent) {
  const cached = _tombstoneSpriteCache[agent.name];
  if (cached && cached.color === agent.color) return cached.grid;
  const palette = {
    ".": null, k: "#111111", s: "#9E9E9E", h: "#BDBDBD",
    d: "#5c3a1a", g: "#5ea830", m: agent.color || "#5ea830",
  };
  const grid = tileFromStrings([
    "................",
    "....kkkkkkkk....",
    "...kssssssssk...",
    "..kssssssssssk..",
    "..kshhhhhhhssk..",
    "..ksh......hssk..",
    "..ksh......hssk..",
    "..kssssssssssk..",
    "..kssssssssssk..",
    "..kssssssssssk..",
    "..kssssssssssk..",
    "..kkkkkkkkkkkk..",
    "...gggggggggg...",
    "..gggmggggmggg..",
    "..gggggggggggg..",
    "................",
  ], palette);
  _tombstoneSpriteCache[agent.name] = { color: agent.color, grid };
  return grid;
}

const ACCESSORIES = {
  Aria: tileFromStrings(["..a.a...",".aaaaaa.","..aaaa..","...aa...","....e..."], makeAgentPalette("#FFD54F", "#8D6E63")),
  Marco: tileFromStrings(["...aa...","..aaaa..",".aaaaaa.","..aa...."], makeAgentPalette("#FFC107", "#795548")),
  Zara: tileFromStrings(["....aa..","...aaaa.","..aaaa..","...aa...","....aa..","...e...."], makeAgentPalette("#9C27B0", "#607D8B")),
  Rex: tileFromStrings([".kkkkkk.",".k....k.",".k....k.","..eeee.."], { k: "#9E9E9E", e: "#F44336", ".": null }),
  Luna: tileFromStrings(["..aaaa..",".aaaaaa.","..aaaa..","...aa...","..e..e.."], makeAgentPalette("#2196F3", "#795548")),
  Finn: tileFromStrings(["..aaaa..",".aaaaaa.","..aaaa..","....e...","...e...."], makeAgentPalette("#1565C0", "#00BCD4")),
  Mia: tileFromStrings(["...aa...","..aaaa..","...aa...","....a...","...e...."], makeAgentPalette("#FFFFFF", "#E91E63")),
  Colt: tileFromStrings([".aaaaaa.","aaaaaaa.",".a...a..","...e...."], makeAgentPalette("#FFD54F", "#795548")),
  Ivy: tileFromStrings(["..aaa...",".aaaaa..","..aaa...",".a...a..","..e..e.."], makeAgentPalette("#33691E", "#558B2F")),
  Dex: tileFromStrings(["..aaaa..",".aaaaaa.","..aaaa..","...e...."], makeAgentPalette("#455A64", "#90A4AE")),
  Nova: tileFromStrings(["...aa...","..aaaa..",".aaaaaa.","..aaaa..","...e...."], makeAgentPalette("#FF5722", "#FFAB91")),
  Sage: tileFromStrings(["...ee...","..eeee..",".eeeeee.","...ee...","....e..."], makeAgentPalette("#8D6E63", "#FFF176")),
};

// Small post-body pixels keep seasonal dress readable at the existing 2x
// sprite scale without replacing the named accessory art or altering agents.
function drawSeasonalAgentAccent(ctx, agent, grid, scale, flipX) {
  if (!seasonalAgentAccentsEnabled || agent.deceased) return;
  const w = grid[0].length * scale;
  const h = grid.length * scale;
  const ox = Math.round(agent.x - w / 2);
  const oy = Math.round(agent.y - h + scale * 2);
  const side = flipX ? ox + scale * 3 : ox + w - scale * 4;
  ctx.save();
  if (spriteSeason === "winter") {
    ctx.fillStyle = "#DCEAF2"; // wool cap + scarf
    ctx.fillRect(ox + scale * 4, oy + scale * 2, scale * 8, scale);
    ctx.fillRect(ox + scale * 5, oy + scale * 8, scale * 7, scale);
    ctx.fillRect(side, oy + scale * 9, scale, scale * 2);
  } else if (spriteSeason === "spring") {
    ctx.fillStyle = "#8BC34A"; // leaf pin
    ctx.fillRect(side, oy + scale * 7, scale, scale);
    ctx.fillStyle = "#C5E86C";
    ctx.fillRect(side + (flipX ? scale : -scale), oy + scale * 6, scale, scale);
  } else if (spriteSeason === "summer") {
    ctx.fillStyle = "#F7D774"; // straw hat brim
    ctx.fillRect(ox + scale * 3, oy + scale * 2, scale * 10, scale);
    ctx.fillStyle = "#C8923F";
    ctx.fillRect(ox + scale * 5, oy + scale, scale * 6, scale);
  } else if (spriteSeason === "autumn") {
    ctx.fillStyle = "#C96C35"; // warm scarf
    ctx.fillRect(ox + scale * 5, oy + scale * 8, scale * 7, scale);
    ctx.fillRect(side, oy + scale * 9, scale, scale * 2);
  }
  ctx.restore();
}

function drawAgentSprite(ctx, agent, frameTick) {
  const scale = 2;
  if (agent.deceased && agent.buried) {
    // Permanent death, laid to rest: tombstone in the cemetery grid only.
    drawPixelSprite(ctx, agent.x, agent.y, tombstoneSprite(agent), scale, false);
    return;
  }
  const data = AGENT_SPRITES[agent.name] || genericAgentSprite(agent);
  const moving = Math.abs(agent.targetX - agent.x) > 1 || Math.abs(agent.targetY - agent.y) > 1;
  const walkFrame = moving && Math.floor(frameTick / 12) % 2 === 1;
  const grid = walkFrame ? data.walk : data.stand;
  const flipX = agent.targetX < agent.x - 0.5;
  drawPixelSprite(ctx, agent.x, agent.y, grid, scale, flipX);

  const acc = ACCESSORIES[agent.name];
  if (acc) {
    const w = grid[0].length * scale;
    const h = grid.length * scale;
    const ox = Math.round(agent.x - w / 2);
    const oy = Math.round(agent.y - h + scale * 2);
    drawPixelGrid(ctx, ox + scale * 4, oy, acc, scale, flipX);
  }
  drawSeasonalAgentAccent(ctx, agent, grid, scale, flipX);

  // Sid-parity Phase 3: tint living agents by dominant belief id.
  const beliefIds = agent.beliefIds || [];
  if (beliefIds.length && !agent.deceased && !agent.incapacitated) {
    const tint = BELIEF_TINTS[beliefIds[0]];
    if (tint) {
      const w = grid[0].length * scale;
      const h = grid.length * scale;
      const ox = Math.round(agent.x - w / 2);
      const oy = Math.round(agent.y - h + scale * 2);
      ctx.save();
      ctx.globalAlpha = 0.28;
      ctx.fillStyle = tint;
      ctx.beginPath();
      ctx.arc(agent.x, oy + 4, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }
  }
}

// Belief tint colors for competing memes (Sid-parity Phase 3).
const BELIEF_TINTS = {
  harvest_spirit: "#c9a227",
  river_spirit: "#3a8fd4",
};

function drawZoneLabel(ctx, text, x, y) {
  ctx.font = "bold 14px monospace";
  ctx.textAlign = "center";
  const w = ctx.measureText(text).width + 14;
  ctx.fillStyle = "rgba(0, 0, 0, 0.62)";
  ctx.fillRect(Math.round(x - w / 2), y - 13, w, 18);
  ctx.strokeStyle = "rgba(255, 255, 255, 0.35)";
  ctx.strokeRect(Math.round(x - w / 2), y - 13, w, 18);
  ctx.fillStyle = "#fff7d6";
  ctx.fillText(text, x, y + 1);
}

// kind -> base tile, and which kinds use the path-blend fill (farm/village/
// beach originally did; forest/market/cave/workshop use a plain fill, exactly
// matching pre-districts behavior for the starter core).
const KIND_TILE = {
  farm: TILE_FARM, forest: TILE_FOREST_FLOOR, village: TILE_VILLAGE,
  market: TILE_MARKET, cave: TILE_CAVE, workshop: TILE_WORKSHOP, beach: TILE_BEACH,
  cemetery: TILE_CEMETERY,
};
const KIND_USES_PATH_BLEND = new Set(["farm", "village", "beach", "cemetery"]);

// Starter district list (mirrors sim_engine.py's STARTER_DISTRICTS) used as
// the initial fallback before index.html's first /districts.js fetch
// resolves, and as the shape reference for the served list thereafter. MUST
// be kept in sync with sim_engine.py's STARTER_DISTRICTS bounds/kind/label.
const STARTER_DISTRICTS_JS = [
  { id: "farm_north", kind: "farm", label: "FARM", bounds: { x1: 500, y1: 110, x2: 920, y2: 810 } },
  { id: "forest", kind: "forest", label: "FOREST", bounds: { x1: 1030, y1: 110, x2: 1550, y2: 450 } },
  { id: "village_core", kind: "village", label: "VILLAGE", bounds: { x1: 540, y1: 960, x2: 900, y2: 2540 } },
  { id: "market", kind: "market", label: "MARKET", bounds: { x1: 970, y1: 1020, x2: 1110, y2: 1120 } },
  { id: "beach", kind: "beach", label: "BEACH", bounds: { x1: 290, y1: 100, x2: 490, y2: 900 } },
  { id: "cave_east", kind: "cave", label: "CAVE", bounds: { x1: 1210, y1: 1150, x2: 1540, y2: 1360 } },
  { id: "ocean", kind: "ocean", label: null, bounds: { x1: 0, y1: 100, x2: 280, y2: 900 } },
  { id: "farm_south", kind: "farm", label: "FARM (SOUTH FIELDS)", bounds: { x1: 1650, y1: 110, x2: 2050, y2: 710 } },
  { id: "village_east", kind: "village", label: "EAST VILLAGE", bounds: { x1: 1650, y1: 960, x2: 2050, y2: 2540 } },
  { id: "workshop_row", kind: "workshop", label: "WORKSHOP ROW", bounds: { x1: 2100, y1: 110, x2: 2500, y2: 710 } },
  { id: "cave_deep", kind: "cave", label: "DEEP CAVE", bounds: { x1: 2100, y1: 960, x2: 2500, y2: 1560 } },
  { id: "cemetery_grounds", kind: "cemetery", label: "CEMETERY", bounds: { x1: 230, y1: 900, x2: 530, y2: 2200 } },
];

// Hand-placed decorative props for the starter core ONLY (bespoke, not
// generalized -- see world_expansion plan section 5: "inherently artistic
// placement, not worth generalizing"). A district founded at runtime renders
// with just its data-driven tile fill + label, no bespoke props here.
//
// ecologyStages (Phase 2 living-ecosystem, CROP_GROWTH_ENABLED): an optional
// {districtId: stage} map. Omitted/null (the flag-off / no-data case) makes
// every stage lookup below default to "lush", which reproduces the original
// unconditional crop/tree rendering byte-for-byte.
function drawStarterProps(ctx, season = "summer", ecologyStages = null) {
  const stageOf = (did) => (ecologyStages && ecologyStages[did]) || "lush";

  // Farm (north): crops + southern fence.
  const farmNorthStage = stageOf("farm_north");
  for (let fx = 500; fx < 920; fx += 34) {
    for (let fy = 110; fy < 280; fy += 30) {
      if (shouldDrawCrop(fx, fy, farmNorthStage, 3)) drawCrop(ctx, fx, fy, season, farmNorthStage);
    }
  }
  for (let fx = 480; fx < 940; fx += 16) drawFence(ctx, fx, 424, season);

  // Forest trees. Stage thins the canopy: "lush"/"healthy" draw every spot
  // (byte-identical to the original at "lush"), "sparse" draws roughly a
  // third as full trees (the rest as stumps), "barren" draws stumps only.
  const forestStage = stageOf("forest");
  const treeSpots = [
    [1060, 170], [1150, 130], [1240, 190], [1330, 140], [1420, 200], [1510, 150],
    [1090, 290], [1190, 340], [1290, 270], [1390, 350], [1490, 300], [1540, 410],
    [1130, 420], [1320, 430], [1480, 440],
  ];
  const TREE_STAGE_MOD = { barren: 0, sparse: 3, healthy: 1, lush: 1 };
  const treeMod = TREE_STAGE_MOD[forestStage] ?? 1;
  treeSpots.forEach(([tx, ty], i) => {
    if (treeMod > 0 && i % treeMod === 0) drawTree(ctx, tx, ty, season, forestStage);
    else drawTreeStump(ctx, tx, ty);
  });

  // Beach jetty straddling the beach/ocean line so it reads as a pier over water.
  drawDock(ctx, 150, 470);
  drawZoneLabel(ctx, "DOCK", 186, 520);

  // Village (core) well, houses, cave rocks + entrance, market stall.
  drawWell(ctx, 905, 1000, season);
  drawHouse(ctx, 985, 1200, season);
  drawHouse(ctx, 1085, 1200, season);
  drawRocks(ctx, 1260, 1200, season);
  drawRocks(ctx, 1430, 1260, season);
  drawRocks(ctx, 1340, 1330, season);
  drawCaveEntrance(ctx, 1380, 1280);
  drawMarketStall(ctx, 975, 1015);

  // Farm (south): a lighter second crop patch + fence, mirroring farm_north.
  const farmSouthStage = stageOf("farm_south");
  for (let fx = 1650; fx < 2050; fx += 40) {
    for (let fy = 110; fy < 260; fy += 34) {
      if (shouldDrawCrop(fx, fy, farmSouthStage, 4)) drawCrop(ctx, fx, fy, season, farmSouthStage);
    }
  }
  for (let fx = 1650; fx < 2050; fx += 16) drawFence(ctx, fx, 424, season);

  // East village: a couple of houses outside the build grid's footprint.
  drawHouse(ctx, 1990, 1300, season);
  drawHouse(ctx, 2010, 1420, season);

  // Deep cave: rock outcrops matching cave_east's look.
  drawRocks(ctx, 2200, 1100, season);
  drawRocks(ctx, 2380, 1200, season);

  // Cemetery grounds: a quiet fenced plot west of the village.
  for (let fx = 230; fx < 530; fx += 16) drawFence(ctx, fx, 900, season);
  for (let fx = 230; fx < 530; fx += 16) drawFence(ctx, fx, 2184, season);
  for (let fy = 900; fy < 2200; fy += 16) drawFence(ctx, 230, fy, season);
  for (let fy = 900; fy < 2200; fy += 16) drawFence(ctx, 514, fy, season);
}

const TILE_CELL_PX = 40;
const TERRAIN_COLORS = { soil: "#6D4C41", rock: "#757575", grove: "#2E7D32", water: "#1565C0" };
const BLOCK_COLORS = { wall: "#5D4037", floor: "#8D6E63", door: "#A1887F", fence: "#795548" };

function drawDistrictTerrain(ctx, district, season = "summer") {
  const terrain = district.terrain;
  if (!terrain || !Object.keys(terrain).length) return;
  const b = district.bounds;
  const cols = 8;
  const rows = 8;
  const cellW = Math.max(8, (b.x2 - b.x1) / cols);
  const cellH = Math.max(8, (b.y2 - b.y1) / rows);
  for (const [key, kind] of Object.entries(terrain)) {
    const [gx, gy] = key.split(",").map(Number);
    if (Number.isNaN(gx) || Number.isNaN(gy)) continue;
    const color = TERRAIN_COLORS[kind] || TERRAIN_COLORS.soil;
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.35;
    ctx.fillRect(b.x1 + gx * cellW, b.y1 + gy * cellH, cellW, cellH);
    ctx.globalAlpha = 1;
  }
}

function drawDistrictTiles(ctx, district, season = "summer") {
  const tiles = district.tiles;
  if (!tiles || !Object.keys(tiles).length) return;
  const b = district.bounds;
  const cols = 8;
  const rows = 8;
  const cellW = Math.max(8, (b.x2 - b.x1) / cols);
  const cellH = Math.max(8, (b.y2 - b.y1) / rows);
  for (const [key, block] of Object.entries(tiles)) {
    const [gx, gy] = key.split(",").map(Number);
    if (Number.isNaN(gx) || Number.isNaN(gy)) continue;
    const x = b.x1 + gx * cellW;
    const y = b.y1 + gy * cellH;
    ctx.fillStyle = BLOCK_COLORS[block] || "#9E9E9E";
    ctx.fillRect(x + 2, y + 2, cellW - 4, cellH - 4);
    if (block === "door") {
      ctx.fillStyle = "#3E2723";
      ctx.fillRect(x + cellW * 0.35, y + cellH * 0.2, cellW * 0.3, cellH * 0.6);
    }
  }
}

function drawTiledWorld(ctx, worldW, worldH, frameTick, structures, districts, roadNodes, roadEdges, season = "summer", ecologyStages = null, stageTimings = null) {
  const foamOffset = Math.floor(frameTick / 8) % 16;
  const activeDistricts = (districts && districts.length) ? districts : STARTER_DISTRICTS_JS;
  CURRENT_DISTRICTS_FOR_BLEND = activeDistricts;
  if (roadEdges && roadEdges.length && roadNodes) markRoadEdges(roadEdges, roadNodes);

  // Base grass everywhere (frontier + gaps between districts), then a
  // data-driven pass over the served district list -- one tile-fill per
  // district, keyed by kind to the TILE_* constants above. New districts
  // (founded, or starter ones sharing an existing kind) need zero new tile
  // code; only the props below stay bespoke to the starter core.
  let stageT0 = stageTimings ? performance.now() : 0;
  fillRectWithTiles(ctx, 0, 0, worldW, worldH, TILE_GRASS, pathBlendForZone);
  if (stageTimings) stageTimings.baseFillMs = performance.now() - stageT0;

  stageT0 = stageTimings ? performance.now() : 0;
  for (const d of activeDistricts) {
    const b = d.bounds;
    const w = b.x2 - b.x1, h = b.y2 - b.y1;
    if (d.kind === "ocean") {
      fillRectWithTile(ctx, b.x1, b.y1, w, h, getOceanTile(foamOffset));
      continue;
    }
    const tile = KIND_TILE[d.kind];
    if (!tile) continue;
    if (KIND_USES_PATH_BLEND.has(d.kind)) {
      fillRectWithTiles(ctx, b.x1, b.y1, w, h, tile, pathBlendForZone);
    } else {
      fillRectWithTile(ctx, b.x1, b.y1, w, h, tile);
    }
    drawDistrictTerrain(ctx, d, season);
    drawDistrictTiles(ctx, d, season);
  }
  if (stageTimings) stageTimings.districtPassesMs = performance.now() - stageT0;

  stageT0 = stageTimings ? performance.now() : 0;
  drawStarterProps(ctx, season, ecologyStages);

  for (const d of activeDistricts) {
    if (!d.label) continue;
    const b = d.bounds;
    drawZoneLabel(ctx, d.label, Math.round((b.x1 + b.x2) / 2), b.y1 - 15);
  }

  // Agent-built structures.
  if (structures) {
    for (const s of structures) {
      drawStructure(ctx, s);
    }
  }
  if (stageTimings) stageTimings.propsMs = performance.now() - stageT0;
}

// =====================================================================
// Ambient wildlife (WILDLIFE_ENABLED). Server-authoritative huntable fauna
// projected via world.wildlife; index.html culls and applies cosmetic bob.
// Small chunky pixel-grid sprites (flat-color, black-outline idiom matching
// AGENT_SPRITES). Convention: static kinds are bare grids; animated kinds
// use { stand, alt? } and drawWildlifeCreature alternates alt on frameTick
// (same idiom as drawAgentSprite stand/walk). Caller-side bob in index.html
// drawWildlife is additive; frameTick never invents a second position.
//
// Size tiers (sheet uses destW/destH overrides; procedural = 16-wide grid × tier scale):
//   large — dest 32×32 (procedural scale 2 → ~32 px): deer, boar, grazer, seal
//   mid   — dest 28×28 (procedural scale 2 → ~32 px fallback): fox, owl, turtle,
//           rabbit, chicken, gull, bird
//   small — dest 14×14 (procedural scale 1 → ~16 px fallback): mouse, squirrel,
//           fish, crab, butterfly
// =====================================================================
const WILDLIFE_TIER_SCALE = { large: 2, mid: 2, small: 1 };

const WILDLIFE_SIZE_TIER = {
  deer: "large",
  boar: "large",
  grazer: "large",
  seal: "large",
  fox: "mid",
  owl: "mid",
  turtle: "mid",
  rabbit: "mid",
  chicken: "mid",
  gull: "mid",
  bird: "mid",
  mouse: "small",
  squirrel: "small",
  fish: "small",
  crab: "small",
  butterfly: "small",
};

function wildlifeScaleForKind(kind) {
  const tier = WILDLIFE_SIZE_TIER[kind] || "mid";
  return WILDLIFE_TIER_SCALE[tier];
}

// Per-kind alt-frame cadence (ticks per swap); defaults to 12 in drawer.
const WILDLIFE_ANIM_CADENCE = {
  butterfly: 8,
  fish: 12,
  squirrel: 10,
  bird: 8,
  owl: 20,
  rabbit: 15,
  chicken: 12,
  gull: 9,
};

// Tiny Farm outline idiom for procedural wildlife fallback (matches wildlife.png).
const WILDLIFE_OUT = "#3F2631";

const WILDLIFE_PAL = {
  ".": null,
  k: WILDLIFE_OUT,
  w: "#FFFFFF",
  l: "#C0CBDC",
  m: "#8B9BB4",
  d: "#52607C",
  h: "#5A6988",
  b: "#A0785A",
  j: "#C49A6C",
  A: "#AA7850",
  J: "#DCBE96",
  H: "#5A4637",
  B: "#DCC8AA",
  S: "#B47846",
  T: "#B46432",
  F: "#6482A0",
  W: "#8B7355",
  U: "#B4A082",
  R: "#B4B4B4",
  L: "#969696",
  n: "#3E4E6E",
  o: "#E19A65",
  p: "#F7C282",
  y: "#E38628",
  g: "#789155",
  s: "#55733C",
  e: "#8CAF5F",
  f: "#64B4DC",
  t: "#3C82B4",
  c: "#DC5A50",
  u: "#B43C37",
  E: "#282828",
  a: "#AA64D2",
  v: "#8246B4",
  i: "#FFB4B4",
  q: "#262B44",
  x: "#6E5541",
  Q: "#5A4632",
  z: "#F0E6D2",
};

const WILDLIFE_SPRITES = {
  fish: {
    stand: tileFromStrings([
      "................",
      "................",
      "...kkkkkk.......",
      "..kffffffffk....",
      ".tkfffffffffnk..",
      "kfffffffffffffk.",
      "kfffffffffffffk.",
      "kfffffffffffffk.",
      ".kfffffffffffk..",
      "..kfffffffffk...",
      "...kffffffffk...",
      "....kkkkkkkk....",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "................",
      "....kkkkkk......",
      "...kffffffffk...",
      "..tkfffffffffnk.",
      ".kfffffffffffffk",
      ".kfffffffffffffk",
      "..kffffffffffffk",
      "...kfffffffffffk",
      "....kfffffffffk.",
      ".....kffffffffk.",
      "......kkkkkkkk..",
    ], { ...WILDLIFE_PAL }),
  },

  bird: {
    stand: tileFromStrings([
      "................",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkbbbbbkk",
      ".kkkkkkhhhhhyek.",
      "kkkbbbbhhhhhhhk.",
      "kkbbbbbbbfbbfkkk",
      "kkbbbbbbbhhhkkk.",
      "kkbllbbbbbnhhkk.",
      "kkbllbbbbbbkkkk.",
      ".kbbbbbbbbbbkk..",
      "..kbbbbbbbbk....",
      "...kdd...ddk....",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkmbbbbkk",
      ".kkkkkkhhhhhyek.",
      "kkkmbbbbhhhhhhhk",
      "kkbbbbbbbfbbfkkk",
      "kkbbbbbbbhhhkkk.",
      "kkbllbbbbbnhhkk.",
      "kkbllbbbbbbkkkk.",
      ".kbbbbbbbbbbkk..",
      "..kbbbbbbbbk....",
      "...kdd...ddk....",
    ], { ...WILDLIFE_PAL }),
  },

  grazer: tileFromStrings([
    "....kkkkkkkk....",
    "...kwwwwwwwwk...",
    "..kwwwwwwwwwwk..",
    ".kwwwwwwwwwwwwk.",
    "kwwwwwwwwwwwwwwk",
    "kwwwwwhhwwwhhwwk",
    "kwwwwwwywwwwwwwk",
    "kwwwwwwwwwwwwwwk",
    "kwwwwwwwwwwwwwwk",
    "kwwwwwwwwwwwwwwk",
    ".kwwwwwwwwwwwwk.",
    "..kwwwwwwwwwwk..",
    "...dd......dd...",
    "...dd......dd...",
  ], { ".": null, k: WILDLIFE_OUT, w: "#EFEBE0", h: "#8D6E63", y: "#2B2B2B", d: "#52607C" }),

  squirrel: {
    stand: tileFromStrings([
      "................",
      "....TTTT........",
      "...kTTTTTTk.....",
      "..kTTTTTTTTk....",
      ".kTTTSSSSSSSk...",
      "kTTTTkkSSSSSSSk.",
      "kTTTTkSSSSSSSSSk",
      "kTTTkSSSSSSSSSSk",
      ".kTTkSSSSSSSSSk.",
      "..kkSSSSSSSSSSk.",
      "...kSSSSSSSSSSk.",
      "....kSSSSSSSSk..",
      ".....kdd...ddk..",
      "......dd...dd...",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "....TTTT.t......",
      "...kTTTTTTk.....",
      "..kTTTTTTTTk....",
      ".kTTTSSSSSSSk...",
      "kTTTTkkSSSSSSSk.",
      "kTTTTkSSSSSSSSSk",
      "kTTTkSSSSSSSSSSk",
      ".kTTkSSSSSSSSSk.",
      "..kkSSSSSSSSSSk.",
      "...kSSSSSSSSSSk.",
      "....kSSSSSSSSk..",
      ".....kdd...ddk..",
      "......dd...dd...",
    ], { ...WILDLIFE_PAL }),
  },

  deer: tileFromStrings([
    "................",
    "....k..B..k.....",
    "...k.Bk.k.Bk....",
    "..k..B...B..k...",
    "........kkkkkk..",
    "......kkkkkkkkk.",
    "..kkkkkkkjjjjkkk",
    ".kkkkkkHHHHHHkkk",
    "kkkjjjjHHHHHHkkk",
    "kkjJJjjjHHHHHkkk",
    "kkjAAjjjHHHHkkk.",
    "kkjJJjjjjjjjkkk.",
    "kkjAAjjjAAjjkkk.",
    "kkkjjjjjjjjjkk..",
    ".kkHkHkkHkHkkk..",
    ".kkHkHkkHkHkkk..",
  ], { ...WILDLIFE_PAL }),

  fox: tileFromStrings([
    "................",
    "..k.........k...",
    ".kok.......kok..",
    "kTTT..kkkkkkkk..",
    "kTTT.kkkkkkkkkk.",
    "kTTTkkkkkkoooooo",
    "kTT.kkkkkooooooe",
    "kTTkkkkooooooooo",
    "kkoooooooooooooo",
    "kkooooppppppooTT",
    "kkoooooooddddook",
    ".kooooooddddook.",
    "..kdddd..ddddk..",
    "...kddd...dddk..",
    "....dd.....dd...",
  ], { ...WILDLIFE_PAL }),

  boar: tileFromStrings([
    "................",
    "....k.k......k.k",
    "...k.k.k....k.k.",
    "....k.k......k.k",
    "........kkkkkk..",
    "......kkkkkkkkk.",
    "..kkkkkkkxxxxkkk",
    ".kkkkkkxxxxxxyxk",
    "kkkxxxxxxxxxxyxk",
    "kkxxxxxxxxxHHzkk",
    "kkxxxxxxxxxHHzkk",
    "kkxxxxxxxxxxkkk.",
    "kkkxxxxxxxxkkk..",
    ".kxxkxxkxxkk....",
    "...xx...xx......",
  ], { ...WILDLIFE_PAL }),

  owl: {
    stand: tileFromStrings([
      "................",
      ".....kkkk.......",
      "....kWUUUWk.....",
      "...kWyiiiyWk....",
      "..kWUUUUUUUWk...",
      ".kWWWWWWWWWWk...",
      "kWWWWWWWWWWWWk..",
      "kWWWWWWWWWWWWk..",
      "kWWWWWWWWWWWWk..",
      ".kWWWWWWWWWWk...",
      "..kWWWWWWWWk....",
      "...kWWWWWWk.....",
      "....kdd.ddk.....",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      ".....kkkk.......",
      "....kWUUUWk.....",
      "...kWykkkyWk....",
      "..kWUUUUUUUWk...",
      ".kWWWWWWWWWWk...",
      "kWWWWWWWWWWWWk..",
      "kWWWWWWWWWWWWk..",
      "kWWWWWWWWWWWWk..",
      ".kWWWWWWWWWWk...",
      "..kWWWWWWWWk....",
      "...kWWWWWWk.....",
      "....kdd.ddk.....",
    ], { ...WILDLIFE_PAL }),
  },

  rabbit: {
    stand: tileFromStrings([
      "................",
      "....k.....k.....",
      "....i.....i.....",
      "....w.....w.....",
      "....w.....w.....",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkwwwwkkk",
      ".kkkkkkwwniwwwek",
      "kkkwwwwwwwwwwwkk",
      "kkwwwwwwwqwwqkkk",
      "kkwwwwwwwwwwwkkk",
      "kkwllwwwwwwllkk.",
      ".kwwwwwwwwwwwk..",
      "..kdd.....ddk...",
      "...dd.....dd....",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "....k.....k.....",
      ".....i...i......",
      "....w.w.........",
      "....w.....w.....",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkwwwwkkk",
      ".kkkkkkwwniwwwek",
      "kkkwwwwwwwwwwwkk",
      "kkwwwwwwwqwwqkkk",
      "kkwwwwwwwwwwwkkk",
      "kkwllwwwwwwllkk.",
      ".kwwwwwwwwwwwk..",
      "..kdd.....ddk...",
      "...dd.....dd....",
    ], { ...WILDLIFE_PAL }),
  },

  chicken: {
    stand: tileFromStrings([
      "...ccc...",
      "..kbbbk..",
      ".kybbbbke",
      "kbbbbbbbk",
      "kbbbbbbbk",
      "kbbbbbbbk",
      ".kbbbbbk.",
      "..ff.ff..",
    ], { ".": null, k: OUT, b: "#FFF8E1", c: "#E53935", y: "#2B2B2B", e: "#FF9800", f: "#F4A261" }),
    alt: tileFromStrings([
      "...ccc...",
      "..kbbbk..",
      "kbbbbbbbk",
      "kbbbbbbbk",
      ".kybbbbke",
      "kbbbbbbbk",
      ".kbbbbbk.",
      "..ff.ff..",
    ], { ".": null, k: OUT, b: "#FFF8E1", c: "#E53935", y: "#2B2B2B", e: "#FF9800", f: "#F4A261" }),
  },

  mouse: tileFromStrings([
    "................",
    "................",
    "...kk.....kk....",
    "...Ri.....iR....",
    "..kRRRRRRRRRk...",
    ".tkRRRRnRRRRRk..",
    "kRRRRRRRRRRRRRk.",
    "kRRRRRRRRRRRRRk.",
    ".kRRRRRRRRRRRk..",
    "..kRRRRRRRRRk...",
    "...kdd.....ddk..",
    "....LL.....dd...",
    "....kLL.........",
    ".....LL.........",
  ], { ...WILDLIFE_PAL }),

  butterfly: {
    stand: tileFromStrings([
      "................",
      "aaa.......bbb...",
      "aaaak.....k.bbbb",
      "vaaak.....k.bbbb",
      "aaaak.....k.vbbb",
      "aaaak.....k.bbbb",
      "aaaak.....k.bbbb",
      ".aaa.......bbb..",
      "......k.k.......",
      "......k.k.......",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "......k.k.......",
      ".....kvvvvk.....",
      "....kvvvvvvk....",
      "....kvvvvvvk....",
      ".....kvvvvk.....",
      "......k.k.......",
    ], { ...WILDLIFE_PAL }),
  },

  crab: tileFromStrings([
    "................",
    ".c.....kkkk....c",
    "c..kcccccccck..c",
    "..ckcccccccccck.",
    ".ckccccccccccck.",
    "kcccccccccccccck",
    "kcccccccccccccck",
    ".ckcccccccccEck.",
    "..kcccccccccck..",
    "...kuuuuuuuuk...",
    "....kuuuuuuk....",
    ".....kuuuk......",
  ], { ...WILDLIFE_PAL }),

  gull: {
    stand: tileFromStrings([
      "................",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkwwwwwkk",
      ".kkkkkkhhhhhyek.",
      "kkkwwwwhhhhhhhk.",
      "kkwwwwwwwqwwqkkk",
      "kkwwwwwwwhhhhkkk",
      "kkwllwwwwwnhhkk.",
      "kkwllwwwwwwkkkk.",
      ".kwwwwwwwwwwkk..",
      "..kwwwwwwwwk....",
      "...kdd...ddk....",
    ], { ...WILDLIFE_PAL }),
    alt: tileFromStrings([
      "................",
      "........kkkkkk..",
      "......kkkkkkkkk.",
      "..kkkkkkkmwwwwkk",
      ".kkkkkkhhhhhyek.",
      "kkkmwwwwhhhhhhhk",
      "kkwwwwwwwqwwqkkk",
      "kkwwwwwwwhhhhkkk",
      "kkwllwwwwwnhhkk.",
      "kkwllwwwwwwkkkk.",
      ".kwwwwwwwwwwkk..",
      "..kwwwwwwwwk....",
      "...kdd...ddk....",
    ], { ...WILDLIFE_PAL }),
  },

  turtle: tileFromStrings([
    "................",
    "....kkkkkkk.....",
    "...kgssssssgk...",
    "..kgsheeehsgk...",
    ".kgggggggggggk..",
    "kggggggggggggggk",
    "kggggggggggeekk.",
    "kgggggggggggkk..",
    ".kggggggggggk...",
    "..kd....d..dk...",
    "..kd....d..dk...",
  ], { ...WILDLIFE_PAL }),

  seal: tileFromStrings([
    "................",
    "................",
    "....kkkkkkkkk...",
    "...kmmmmmmmmmk..",
    "..kmmmmmmmmmmmk.",
    ".kmmmmmmmmmmmmmk",
    "kmmmmmmmmmmmmmmk",
    "kmmmmmmmmmnmmmmk",
    "kmmmmmmmmFmmmmmk",
    ".kmmmmmmmmmmmmk.",
    "..kkkkkkkkkkkk..",
  ], { ...WILDLIFE_PAL }),
};

function resolveWildlifeGrid(entry, kind, frameTick) {
  if (!entry) return null;
  if (Array.isArray(entry)) return entry;
  if (entry.stand) {
    const cadence = WILDLIFE_ANIM_CADENCE[kind] || 12;
    const useAlt = entry.alt && Math.floor(frameTick / cadence) % 2 === 1;
    return useAlt ? entry.alt : entry.stand;
  }
  return null;
}

// ---------------------------------------------------------------------
// Wildlife PNG spritesheet (Phase 3 pipeline). One preload, ready flag,
// mandatory procedural fall-through — never render blank.
// Atlas 128×64 (8×4 cells). Built by scripts/build_wildlife_sheet.py.
// ---------------------------------------------------------------------
const WILDLIFE_SHEET_URL = "/wildlife.png";

// Kind → source rect(s). Bare { sx, sy, sw, sh, destW?, destH? } for static
// kinds; { stand, alt? } mirrors WILDLIFE_SPRITES animation idiom.
const WILDLIFE_SHEET_FRAMES = {
  deer: { sx: 0, sy: 0, sw: 16, sh: 16, destW: 32, destH: 32 },
  boar: { sx: 16, sy: 0, sw: 16, sh: 16, destW: 32, destH: 32 },
  grazer: { sx: 32, sy: 0, sw: 16, sh: 16, destW: 32, destH: 32 },
  seal: { sx: 48, sy: 0, sw: 16, sh: 16, destW: 32, destH: 32 },
  fox: { sx: 64, sy: 0, sw: 16, sh: 16, destW: 28, destH: 28 },
  owl: {
    stand: { sx: 80, sy: 0, sw: 16, sh: 16, destW: 28, destH: 28 },
    alt: { sx: 96, sy: 0, sw: 16, sh: 16, destW: 28, destH: 28 },
  },
  turtle: { sx: 112, sy: 0, sw: 16, sh: 16, destW: 28, destH: 28 },
  rabbit: {
    stand: { sx: 0, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
    alt: { sx: 16, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
  },
  chicken: {
    stand: { sx: 32, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
    alt: { sx: 48, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
  },
  gull: {
    stand: { sx: 64, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
    alt: { sx: 80, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
  },
  bird: {
    stand: { sx: 96, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
    alt: { sx: 112, sy: 16, sw: 16, sh: 16, destW: 28, destH: 28 },
  },
  mouse: { sx: 0, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
  squirrel: {
    stand: { sx: 16, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
    alt: { sx: 32, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
  },
  fish: {
    stand: { sx: 48, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
    alt: { sx: 64, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
  },
  crab: { sx: 80, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
  butterfly: {
    stand: { sx: 96, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
    alt: { sx: 112, sy: 32, sw: 16, sh: 16, destW: 14, destH: 14 },
  },
};

const _wildlifeSheetBlitCache = new Map();
let _wildlifeSheetImage = null;
let _wildlifeSheetReady = false;

function _wildlifeSheetFrameKey(frame) {
  return `${frame.sx},${frame.sy},${frame.sw},${frame.sh}`;
}

function preloadWildlifeSheet() {
  if (_wildlifeSheetImage) return;
  const img = new Image();
  img.onload = () => {
    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
      _wildlifeSheetReady = true;
    }
  };
  img.onerror = () => {
    _wildlifeSheetReady = false;
  };
  img.src = WILDLIFE_SHEET_URL;
  _wildlifeSheetImage = img;
}

function resolveWildlifeSheetFrame(kind, frameTick) {
  const entry = WILDLIFE_SHEET_FRAMES[kind];
  if (!entry) return null;
  if (entry.sx !== undefined) return entry;
  if (entry.stand) {
    const cadence = WILDLIFE_ANIM_CADENCE[kind] || 12;
    const useAlt = entry.alt && Math.floor(frameTick / cadence) % 2 === 1;
    return useAlt ? entry.alt : entry.stand;
  }
  return null;
}

function getWildlifeSheetFrameCanvas(frame) {
  const key = _wildlifeSheetFrameKey(frame);
  let source = _wildlifeSheetBlitCache.get(key);
  if (source || !_wildlifeSheetReady || !_wildlifeSheetImage) return source;
  let canvas;
  if (typeof OffscreenCanvas !== "undefined") {
    canvas = new OffscreenCanvas(frame.sw, frame.sh);
  } else {
    canvas = document.createElement("canvas");
    canvas.width = frame.sw;
    canvas.height = frame.sh;
  }
  const fctx = canvas.getContext("2d");
  fctx.imageSmoothingEnabled = false;
  fctx.drawImage(
    _wildlifeSheetImage,
    frame.sx, frame.sy, frame.sw, frame.sh,
    0, 0, frame.sw, frame.sh,
  );
  _wildlifeSheetBlitCache.set(key, canvas);
  return canvas;
}

function tryDrawWildlifeFromSheet(ctx, kind, x, y, frameTick) {
  if (!_wildlifeSheetReady) return false;
  const frame = resolveWildlifeSheetFrame(kind, frameTick);
  if (!frame) return false;
  const source = getWildlifeSheetFrameCanvas(frame);
  if (!source) return false;

  const scale = wildlifeScaleForKind(kind);
  const destW = frame.destW ?? frame.sw * scale;
  const destH = frame.destH ?? frame.sh * scale;
  const dx = Math.round(x - destW / 2);
  const dy = Math.round(y - destH + scale * 2);

  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(source, dx, dy, destW, destH);
  ctx.restore();
  return true;
}

function drawWildlifeCreatureProcedural(ctx, kind, x, y, frameTick) {
  const grid = resolveWildlifeGrid(WILDLIFE_SPRITES[kind], kind, frameTick);
  if (!grid) return;
  drawPixelSprite(ctx, x, y, grid, wildlifeScaleForKind(kind), false);
}

function drawWildlifeCreature(ctx, kind, x, y, frameTick) {
  if (tryDrawWildlifeFromSheet(ctx, kind, x, y, frameTick)) return;
  drawWildlifeCreatureProcedural(ctx, kind, x, y, frameTick);
}

preloadWildlifeSheet();

// =====================================================================
// Goods-in-motion shipments (Phase 3 living-ecosystem, CARAVAN_VISUALS_
// ENABLED). Purely cosmetic: index.html computes the position by
// interpolating along the road-graph path the engine already embedded in
// the shipment record, and passes it in here. These are stateless draw
// calls only -- no simulation state lives in this file. `cargoColor` comes
// from the same resource -> colour registry drawResourceDots already uses.
// =====================================================================
function drawCart(ctx, x, y, cargoColor) {
  ctx.fillStyle = "#5A321B";
  ctx.fillRect(x - 6, y - 4, 12, 7);
  ctx.fillStyle = "#3E3226";
  ctx.beginPath();
  ctx.arc(x - 4, y + 4, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x + 4, y + 4, 2.5, 0, Math.PI * 2);
  ctx.fill();
  if (cargoColor) {
    ctx.fillStyle = cargoColor;
    ctx.fillRect(x - 3, y - 7, 6, 3);
  }
}

function drawShipmentBoat(ctx, x, y, cargoColor) {
  // Small moving vessel -- a scaled-down echo of the moored physicalProps
  // boat art (index.html), not a copy of the DOM/state wiring around it.
  ctx.fillStyle = "#4A2714";
  ctx.beginPath();
  ctx.moveTo(x - 9, y - 2);
  ctx.lineTo(x + 9, y - 2);
  ctx.lineTo(x + 6, y + 5);
  ctx.lineTo(x - 6, y + 5);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#5A321B";
  ctx.fillRect(x - 1, y - 12, 2, 11);
  ctx.fillStyle = "#F4E2B5";
  ctx.beginPath();
  ctx.moveTo(x + 1, y - 11);
  ctx.lineTo(x + 8, y - 1);
  ctx.lineTo(x + 1, y - 1);
  ctx.closePath();
  ctx.fill();
  if (cargoColor) {
    ctx.fillStyle = cargoColor;
    ctx.fillRect(x - 3, y, 4, 3);
  }
}

function drawShipment(ctx, mode, x, y, cargoColor) {
  if (mode === "boat") drawShipmentBoat(ctx, x, y, cargoColor);
  else drawCart(ctx, x, y, cargoColor);
}
