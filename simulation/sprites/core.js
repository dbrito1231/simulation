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
function drawSnowCap(ctx, x, y, w, scale, opts) {
  opts = opts || {};
  const capExtra = opts.capExtra || 0;
  const step = Math.max(1, scale);
  const drawW = w + capExtra;
  const steps = Math.max(1, Math.round(drawW / step));
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

// Winter ground frost speckle on grass/farm/village floor tiles (viewer v2).
const WINTER_GROUND_SPECKLE_RATE = 0.15;

function drawWinterGroundAccent(ctx, x, y, w, h, kind, seed) {
  if (kind === "beach" || kind === "ocean" || kind === "quarry") return;
  const cellSeed = (Math.imul(seed, 2654435761) >>> 0) % 1000;
  if (cellSeed / 1000 > WINTER_GROUND_SPECKLE_RATE) return;
  const px = x + (cellSeed % 11) + 2;
  const py = y + (cellSeed % 5);
  const scale = (cellSeed % 2) + 1;
  ctx.fillStyle = C.sn || "#FFFFFF";
  ctx.fillRect(px, py, scale, scale);
  if (cellSeed % 7 === 0) ctx.fillRect(px + scale, py, scale, 1);
}

function drawWinterGroundAccentsForDistrict(ctx, district, season) {
  if (season !== "winter" || district.kind === "ocean" || district.kind === "beach") return;
  const b = district.bounds;
  for (let ty = b.y1; ty < b.y2; ty += TILE) {
    for (let tx = b.x1; tx < b.x2; tx += TILE) {
      drawWinterGroundAccent(ctx, tx, ty, TILE, TILE, district.kind, tx * 31 + ty * 17);
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
