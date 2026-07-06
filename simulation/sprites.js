"use strict";

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

function fillRectWithTile(ctx, x, y, w, h, tile) {
  for (let ty = y; ty < y + h; ty += TILE) {
    for (let tx = x; tx < x + w; tx += TILE) {
      drawPixelGrid(ctx, tx, ty, tile, 1, false);
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
};
const STARTER_ROAD_EDGES = [
  ["farm_north_gate", "village_hub"],
  ["village_hub", "forest_gate"],
  ["village_hub", "cave_east_gate"],
  ["village_hub", "beach_gate"],
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
const PATH_BLEND_BY_KIND = { farm: PATH_BLEND_FARM, village: PATH_BLEND_VILLAGE, beach: PATH_BLEND_BEACH };

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
  for (let ty = y; ty < y + h; ty += TILE) {
    for (let tx = x; tx < x + w; tx += TILE) {
      const key = `${tx},${ty}`;
      const tile = PATH_CELLS.has(key)
        ? (zoneHint ? zoneHint(tx, ty) : PATH_BLEND_GRASS)
        : baseTile;
      drawPixelGrid(ctx, tx, ty, tile, 1, false);
    }
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

function oceanTile(foamOffset) {
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

const TILE_BEACH = makeTile(["s1", "s2", "s3", "sd"]);
const TILE_FARM = makeTile(["f1", "f2", "f3", "fd"]);
const TILE_FOREST_FLOOR = makeTile(["fr1", "fr2", "fr3"]);
const TILE_VILLAGE = makeTile(["v1", "v2", "v3"]);
const TILE_MARKET = makeTile(["m1", "m2", "m3", "ma"]);
const TILE_CAVE = makeTile(["cv1", "cv2", "cv3"]);
const TILE_WORKSHOP = makeTile(["wk1", "wk2", "wk3", "wk4"]);

function drawTree(ctx, x, y) {
  const tree = tileFromStrings([
    "....lf2lf2lf2....",
    "...lf2lf2lf2lf2...",
    "..lf2lf2lf2lf2lf2.",
    ".lf2lf2lf2lf2lf2lf",
    "lf2lf2lf2lf2lf2lf2",
    "....trtrtrtr....",
    "....trtrtrtr....",
    "....trtrtrtr....",
  ], C);
  drawPixelGrid(ctx, x - 12, y - 12, tree, 3, false);
}

function drawHouse(ctx, x, y) {
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

function drawCrop(ctx, x, y) {
  ctx.fillStyle = C.fd;
  ctx.fillRect(x, y, 2, 6);
  ctx.fillStyle = C.f1;
  ctx.fillRect(x - 2, y - 2, 6, 4);
}

// --- World props ---

function drawFence(ctx, x, y) {
  const fence = tileFromStrings([
    "fnfnfnfnfnfnfnfn",
    "dkdkdkdkdkdkdkdk",
    "fnfnfnfnfnfnfnfn",
    "dkdkdkdkdkdkdkdk",
  ], C);
  drawPixelGrid(ctx, x, y, fence, 1, false);
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

function drawWell(ctx, x, y) {
  const well = tileFromStrings([
    "..cv1cv1cv1cv1..",
    ".cv1cv1cv1cv1cv1.",
    "cv1cv1wlwlcv1cv1",
    "cv1cv1wlwlcv1cv1",
    ".cv1cv1cv1cv1cv1.",
    "..cv1cv1cv1cv1..",
  ], C);
  drawPixelGrid(ctx, x, y, well, 4, false);
}

function drawRocks(ctx, x, y) {
  const rocks = tileFromStrings([
    "..rk2rk2....",
    ".rk2rk2rk2..",
    "rk2rk2rk2rk2",
    ".rk2rk2rk2..",
    "..rk2rk2....",
  ], C);
  drawPixelGrid(ctx, x - 9, y - 6, rocks, 3, false);
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
};

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

function getStructureGrid(structure) {
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
function getStructureRenderSize(structure) {
  const grid = getStructureGrid(structure);
  if (!grid) return { width: 8 * STRUCTURE_SCALE, height: 8 * STRUCTURE_SCALE };
  const width = grid.reduce((max, row) => Math.max(max, row.length), 0) * STRUCTURE_SCALE;
  return { width, height: grid.length * STRUCTURE_SCALE };
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

function drawStructure(ctx, structure) {
  const grid = getStructureGrid(structure);
  if (grid) {
    drawPixelGrid(ctx, structure.x, structure.y, grid, STRUCTURE_SCALE, false);
    return;
  }
  drawGenericStructure(
    ctx, structure.x, structure.y,
    structure.name || structure.type,
    colorFromId(structure.type)
  );
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

function drawAgentSprite(ctx, agent, frameTick) {
  const data = AGENT_SPRITES[agent.name];
  if (!data) return;
  const moving = Math.abs(agent.targetX - agent.x) > 1 || Math.abs(agent.targetY - agent.y) > 1;
  const walkFrame = moving && Math.floor(frameTick / 12) % 2 === 1;
  const grid = walkFrame ? data.walk : data.stand;
  const flipX = agent.targetX < agent.x - 0.5;
  const scale = 2;
  drawPixelSprite(ctx, agent.x, agent.y, grid, scale, flipX);

  const acc = ACCESSORIES[agent.name];
  if (acc) {
    const w = grid[0].length * scale;
    const h = grid.length * scale;
    const ox = Math.round(agent.x - w / 2);
    const oy = Math.round(agent.y - h + scale * 2);
    drawPixelGrid(ctx, ox + scale * 4, oy, acc, scale, flipX);
  }
}

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
};
const KIND_USES_PATH_BLEND = new Set(["farm", "village", "beach"]);

// Starter district list (mirrors sim_engine.py's STARTER_DISTRICTS) used as
// the initial fallback before index.html's first /districts.js fetch
// resolves, and as the shape reference for the served list thereafter. MUST
// be kept in sync with sim_engine.py's STARTER_DISTRICTS bounds/kind/label.
const STARTER_DISTRICTS_JS = [
  { id: "farm_north", kind: "farm", label: "FARM", bounds: { x1: 500, y1: 110, x2: 920, y2: 810 } },
  { id: "forest", kind: "forest", label: "FOREST", bounds: { x1: 1030, y1: 110, x2: 1550, y2: 450 } },
  { id: "village_core", kind: "village", label: "VILLAGE", bounds: { x1: 540, y1: 960, x2: 900, y2: 2540 } },
  { id: "market", kind: "market", label: "MARKET", bounds: { x1: 970, y1: 1020, x2: 1110, y2: 1120 } },
  { id: "beach", kind: "beach", label: "BEACH", bounds: { x1: 230, y1: 120, x2: 400, y2: 880 } },
  { id: "cave_east", kind: "cave", label: "CAVE", bounds: { x1: 1210, y1: 1150, x2: 1540, y2: 1360 } },
  { id: "ocean", kind: "ocean", label: null, bounds: { x1: 30, y1: 120, x2: 180, y2: 880 } },
  { id: "farm_south", kind: "farm", label: "FARM (SOUTH FIELDS)", bounds: { x1: 1650, y1: 110, x2: 2050, y2: 710 } },
  { id: "village_east", kind: "village", label: "EAST VILLAGE", bounds: { x1: 1650, y1: 960, x2: 2050, y2: 2540 } },
  { id: "workshop_row", kind: "workshop", label: "WORKSHOP ROW", bounds: { x1: 2100, y1: 110, x2: 2500, y2: 710 } },
  { id: "cave_deep", kind: "cave", label: "DEEP CAVE", bounds: { x1: 2100, y1: 960, x2: 2500, y2: 1560 } },
];

// Hand-placed decorative props for the starter core ONLY (bespoke, not
// generalized -- see world_expansion plan section 5: "inherently artistic
// placement, not worth generalizing"). A district founded at runtime renders
// with just its data-driven tile fill + label, no bespoke props here.
function drawStarterProps(ctx) {
  // Farm (north): crops + southern fence.
  for (let fx = 500; fx < 920; fx += 34) {
    for (let fy = 110; fy < 280; fy += 30) {
      if ((fx + fy) % 3 === 0) drawCrop(ctx, fx, fy);
    }
  }
  for (let fx = 480; fx < 940; fx += 16) drawFence(ctx, fx, 424);

  // Forest trees.
  const treeSpots = [
    [1060, 170], [1150, 130], [1240, 190], [1330, 140], [1420, 200], [1510, 150],
    [1090, 290], [1190, 340], [1290, 270], [1390, 350], [1490, 300], [1540, 410],
    [1130, 420], [1320, 430], [1480, 440],
  ];
  for (const [tx, ty] of treeSpots) drawTree(ctx, tx, ty);

  // Beach jetty straddling the beach/ocean line so it reads as a pier over water.
  drawDock(ctx, 150, 470);
  drawZoneLabel(ctx, "DOCK", 186, 520);

  // Village (core) well, houses, cave rocks + entrance, market stall.
  drawWell(ctx, 905, 1000);
  drawHouse(ctx, 985, 1200);
  drawHouse(ctx, 1085, 1200);
  drawRocks(ctx, 1260, 1200);
  drawRocks(ctx, 1430, 1260);
  drawRocks(ctx, 1340, 1330);
  drawCaveEntrance(ctx, 1380, 1280);
  drawMarketStall(ctx, 975, 1015);

  // Farm (south): a lighter second crop patch + fence, mirroring farm_north.
  for (let fx = 1650; fx < 2050; fx += 40) {
    for (let fy = 110; fy < 260; fy += 34) {
      if ((fx + fy) % 4 === 0) drawCrop(ctx, fx, fy);
    }
  }
  for (let fx = 1650; fx < 2050; fx += 16) drawFence(ctx, fx, 424);

  // East village: a couple of houses outside the build grid's footprint.
  drawHouse(ctx, 1990, 1300);
  drawHouse(ctx, 2010, 1420);

  // Deep cave: rock outcrops matching cave_east's look.
  drawRocks(ctx, 2200, 1100);
  drawRocks(ctx, 2380, 1200);
}

function drawTiledWorld(ctx, worldW, worldH, frameTick, structures, districts, roadNodes, roadEdges) {
  const foamOffset = Math.floor(frameTick / 8) % 16;
  const activeDistricts = (districts && districts.length) ? districts : STARTER_DISTRICTS_JS;
  CURRENT_DISTRICTS_FOR_BLEND = activeDistricts;
  if (roadEdges && roadEdges.length && roadNodes) markRoadEdges(roadEdges, roadNodes);

  // Base grass everywhere (frontier + gaps between districts), then a
  // data-driven pass over the served district list -- one tile-fill per
  // district, keyed by kind to the TILE_* constants above. New districts
  // (founded, or starter ones sharing an existing kind) need zero new tile
  // code; only the props below stay bespoke to the starter core.
  fillRectWithTiles(ctx, 0, 0, worldW, worldH, TILE_GRASS, pathBlendForZone);
  for (const d of activeDistricts) {
    const b = d.bounds;
    const w = b.x2 - b.x1, h = b.y2 - b.y1;
    if (d.kind === "ocean") {
      fillRectWithTile(ctx, b.x1, b.y1, w, h, oceanTile(foamOffset));
      continue;
    }
    const tile = KIND_TILE[d.kind];
    if (!tile) continue;
    if (KIND_USES_PATH_BLEND.has(d.kind)) {
      fillRectWithTiles(ctx, b.x1, b.y1, w, h, tile, pathBlendForZone);
    } else {
      fillRectWithTile(ctx, b.x1, b.y1, w, h, tile);
    }
  }

  drawStarterProps(ctx);

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
}
