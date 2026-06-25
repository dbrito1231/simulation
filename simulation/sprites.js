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
      const two = row.slice(i, i + 2);
      if (colorMap[two] !== undefined) {
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
  const w = grid[0].length;
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

markPathRect(300, 350, 380, 20);
markPathRect(490, 200, 20, 180);
markPathRect(600, 230, 20, 150);
markPathRect(680, 130, 150, 20);
markPathRect(810, 250, 20, 340);
markPathRect(600, 430, 230, 20);

const C = {
  g1: "#9bbf6a", g2: "#8aad5a", g3: "#7a9d4a",
  p1: "#c8a87a", p2: "#b8986a", p3: "#a8885a",
  o1: "#3daee9", o2: "#2d9ed9", o3: "#1d8ec9", ow: "#e8f8ff",
  s1: "#f5e6a3", s2: "#e5d693", s3: "#d5c683", sd: "#c4a574",
  f1: "#7ec850", f2: "#6eb840", f3: "#5ea830", fd: "#4a8828",
  fr1: "#2d6a2d", fr2: "#245a24", fr3: "#1a4a1a",
  v1: "#d4a96a", v2: "#c4995a", v3: "#b4894a",
  m1: "#c8874a", m2: "#b8773a", m3: "#a8672a", ma: "#ffae5e",
  cv1: "#555555", cv2: "#454545", cv3: "#353535", cv4: "#1a1a1a",
  k: "#111111", w: "#ffffff", br: "#8a5a2b", brd: "#5c3a1a",
  tr: "#6d4c2a", lf: "#3d8b37", lf2: "#2d6b27",
  fn: "#d4c4a0", rk: "#888888", rk2: "#666666",
  dk: "#6b4423", wl: "#4488cc",
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

function pathBlendForZone(tx, ty) {
  if (tx >= 380 && tx < 640 && ty >= 40 && ty < 220) return PATH_BLEND_FARM;
  if (tx >= 420 && tx < 700 && ty >= 280 && ty < 460) return PATH_BLEND_VILLAGE;
  if (tx >= 150 && tx < 320) return PATH_BLEND_BEACH;
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
  drawPixelGrid(ctx, x - 8, y - 8, tree, 2, false);
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
  drawPixelGrid(ctx, x, y, house, 2, false);
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
  drawPixelGrid(ctx, x, y, stall, 2, false);
}

function drawCaveEntrance(ctx, x, y) {
  ctx.fillStyle = C.cv4;
  ctx.beginPath();
  ctx.arc(x, y, 28, Math.PI, 0, false);
  ctx.fill();
  ctx.fillRect(x - 28, y, 56, 36);
  ctx.fillStyle = C.k;
  ctx.beginPath();
  ctx.arc(x, y + 4, 20, Math.PI, 0, false);
  ctx.fill();
  ctx.strokeStyle = C.cv1;
  ctx.lineWidth = 2;
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
  const dock = tileFromStrings([
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
    "br..br..br..br..",
    "brbrbrbrbrbrbrbr",
    "wlwlwlwlwlwlwlwl",
    "o1o2o1o2o1o2o1o2",
  ], C);
  drawPixelGrid(ctx, x, y, dock, 2, false);
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
  drawPixelGrid(ctx, x, y, well, 2, false);
}

function drawRocks(ctx, x, y) {
  const rocks = tileFromStrings([
    "..rk2rk2....",
    ".rk2rk2rk2..",
    "rk2rk2rk2rk2",
    ".rk2rk2rk2..",
    "..rk2rk2....",
  ], C);
  drawPixelGrid(ctx, x - 6, y - 4, rocks, 2, false);
}

// --- Agent-built structures ---

const STRUCTURE_GRIDS = {
  house: tileFromStrings([
    "..brdbrdbrdbrd..",
    ".brdbrdbrdbrdbrd.",
    "brbrbrbrbrbrbrbr",
    "brw..brbr..wbrbr",
    "brw..brbr..wbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
    "brbrbrbrbrbrbrbr",
  ], C),
  farm_plot: tileFromStrings([
    "fdfdfdfdfdfdfdfd",
    "f1f1f1f1f1f1f1f1",
    "fdfdfdfdfdfdfdfd",
    "f1f1f1f1f1f1f1f1",
    "fdfdfdfdfdfdfdfd",
    "f1f1f1f1f1f1f1f1",
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
    ".brdbrdbrdbrdbrd.",
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

// Fallback for custom blueprints with no built-in sprite: a simple block
// with the structure's first letter in a deterministic accent color.
function drawGenericStructure(ctx, x, y, label, accentColor) {
  const scale = 2;
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
  const scale = 2;
  let grid = STRUCTURE_GRIDS[structure.type];
  if (!grid && structure.visualStyle && structure.visualStyle !== "generic") {
    grid = STRUCTURE_GRIDS[structure.visualStyle];
  }
  if (grid) {
    drawPixelGrid(ctx, structure.x, structure.y, grid, scale, false);
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

function drawTiledWorld(ctx, worldW, worldH, frameTick, structures) {
  const foamOffset = Math.floor(frameTick / 8) % 16;

  fillRectWithTiles(ctx, 0, 0, worldW, worldH, TILE_GRASS, pathBlendForZone);
  fillRectWithTile(ctx, 0, 0, 150, worldH, oceanTile(foamOffset));
  fillRectWithTiles(ctx, 150, 0, 170, worldH, TILE_BEACH, pathBlendForZone);
  fillRectWithTiles(ctx, 380, 40, 260, 180, TILE_FARM, pathBlendForZone);
  fillRectWithTile(ctx, 700, 40, 260, 220, TILE_FOREST_FLOOR);
  fillRectWithTiles(ctx, 420, 280, 280, 180, TILE_VILLAGE, pathBlendForZone);
  fillRectWithTile(ctx, 560, 360, 100, 80, TILE_MARKET);
  fillRectWithTile(ctx, 720, 520, 220, 160, TILE_CAVE);

  // Farm crops
  for (let fx = 400; fx < 630; fx += 32) {
    for (let fy = 60; fy < 200; fy += 28) {
      if ((fx + fy) % 3 === 0) drawCrop(ctx, fx, fy);
    }
  }

  // Farm fence along southern edge
  for (let fx = 380; fx < 640; fx += 16) {
    drawFence(ctx, fx, 218);
  }

  // Forest trees
  const treeSpots = [
    [730, 80], [780, 120], [830, 75], [890, 110], [930, 160],
    [720, 170], [770, 210], [840, 200], [900, 230], [860, 150],
  ];
  for (const [tx, ty] of treeSpots) drawTree(ctx, tx, ty);

  // Beach dock
  drawDock(ctx, 200, 320);

  // Village well
  drawWell(ctx, 530, 370);

  // Cave rocks
  drawRocks(ctx, 760, 540);
  drawRocks(ctx, 900, 620);
  drawRocks(ctx, 740, 650);

  // Village houses (static scenery), kept away from player-built structure spots.
  drawHouse(ctx, 690, 300);
  drawHouse(ctx, 690, 410);

  // Market stall
  drawMarketStall(ctx, 568, 368);
  drawZoneLabel(ctx, "MARKET", 610, 358);
  drawZoneLabel(ctx, "FARM", 500, 64);
  drawZoneLabel(ctx, "FOREST", 820, 64);
  drawZoneLabel(ctx, "VILLAGE", 550, 292);
  drawZoneLabel(ctx, "BEACH", 235, 72);
  drawZoneLabel(ctx, "CAVE", 820, 542);

  // Cave entrance
  drawCaveEntrance(ctx, 830, 600);

  // Agent-built structures
  if (structures) {
    for (const s of structures) {
      drawStructure(ctx, s);
    }
  }
}
