"use strict";

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

// Each "arm" is the half of the path strip reaching from the tile centre
// (the 6..9 band, unchanged from the original single-stripe design) out to
// one edge of the tile. A variant is just the union of the arms for the
// sides that a road cell is actually connected to, so a straight run keeps
// today's full-width stripe (its two opposite arms overlap into one solid
// band) while corners/T-junctions/isolated cells get a shape that matches
// their neighbours instead of always drawing a north-south line.
function armOnStrip(dir, x, y) {
  switch (dir) {
    case "up": return x >= 6 && x <= 9 && y <= 9;
    case "down": return x >= 6 && x <= 9 && y >= 6;
    case "left": return y >= 6 && y <= 9 && x <= 9;
    case "right": return y >= 6 && y <= 9 && x >= 6;
    default: return false;
  }
}

const PATH_VARIANT_ARMS = {
  vertical: ["up", "down"],
  horizontal: ["left", "right"],
  cross: ["up", "down", "left", "right"],
  ne: ["up", "right"],
  nw: ["up", "left"],
  se: ["down", "right"],
  sw: ["down", "left"],
  tUp: ["up", "left", "right"],
  tDown: ["down", "left", "right"],
  tLeft: ["up", "down", "left"],
  tRight: ["up", "down", "right"],
};

function makePathBlendTile(baseKeys, variant) {
  const arms = PATH_VARIANT_ARMS[variant] || PATH_VARIANT_ARMS.vertical;
  const rows = [];
  for (let y = 0; y < TILE; y++) {
    const cells = [];
    for (let x = 0; x < TILE; x++) {
      const onStrip = arms.some((dir) => armOnStrip(dir, x, y));
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

const PATH_BLEND_BASE_KEYS = {
  grass: ["g1", "g2", "g3"],
  beach: ["s1", "s2", "s3", "sd"],
  farm: ["f1", "f2", "f3", "fd"],
  village: ["v1", "v2", "v3"],
  cemetery: ["cm1", "cm2", "cm3"],
};

// Lazily built and memoized per (kind, variant) pair -- 5 kinds x 11
// variants max, built once per pair the first time it's actually painted
// and reused for the life of the page (tile grids never change).
const PATH_BLEND_CACHE = new Map();

function pathBlendTile(kind, variant) {
  const cacheKey = kind + "|" + variant;
  let tile = PATH_BLEND_CACHE.get(cacheKey);
  if (!tile) {
    tile = makePathBlendTile(PATH_BLEND_BASE_KEYS[kind] || PATH_BLEND_BASE_KEYS.grass, variant);
    PATH_BLEND_CACHE.set(cacheKey, tile);
  }
  return tile;
}

// Set by drawTiledWorld() before each pass of tile-fills so pathBlendForZone
// (called deep inside fillRectWithTiles) can look up "which district kind is
// this path tile inside" from the SERVED district list instead of the old
// hardcoded numeric ranges -- generalizes to any district, starter or founded.
let CURRENT_DISTRICTS_FOR_BLEND = [];

function pathBlendForZone(tx, ty, variant) {
  for (const d of CURRENT_DISTRICTS_FOR_BLEND) {
    const b = d.bounds;
    if (tx >= b.x1 && tx < b.x2 && ty >= b.y1 && ty < b.y2) {
      return pathBlendTile(PATH_BLEND_BASE_KEYS[d.kind] ? d.kind : "grass", variant);
    }
  }
  return pathBlendTile("grass", variant);
}

// Road cells from two different edges usually don't land on the exact same
// 16px grid offset (markPathRect steps by TILE from an edge-specific origin,
// see core.js), so a cell right at that boundary often finds only the
// neighbour(s) from its own edge as exact-key matches. That's fine: within a
// single straight leg every cell resolves to "vertical"/"horizontal" exactly
// as before, and where two edges' bands overlap near a corner the two solid
// strips already read as a corner visually -- no cross-edge grid snapping.
function pathCellVariant(tx, ty) {
  const up = PATH_CELLS.has(`${tx},${ty - TILE}`);
  const down = PATH_CELLS.has(`${tx},${ty + TILE}`);
  const left = PATH_CELLS.has(`${tx - TILE},${ty}`);
  const right = PATH_CELLS.has(`${tx + TILE},${ty}`);
  const count = (up ? 1 : 0) + (down ? 1 : 0) + (left ? 1 : 0) + (right ? 1 : 0);
  if (count === 4) return "cross";
  if (count === 3) {
    if (!down) return "tUp";
    if (!up) return "tDown";
    if (!right) return "tLeft";
    return "tRight";
  }
  if (count === 2) {
    if (up && down) return "vertical";
    if (left && right) return "horizontal";
    if (up && right) return "ne";
    if (up && left) return "nw";
    if (down && right) return "se";
    return "sw";
  }
  if (left || right) return "horizontal";
  return "vertical";
}

function fillRectWithTiles(ctx, x, y, w, h, baseTile, zoneHint) {
  fillRectWithTile(ctx, x, y, w, h, baseTile);
  const x2 = x + w;
  const y2 = y + h;
  const blendFn = zoneHint || ((tx, ty, variant) => pathBlendTile("grass", variant));
  for (const key of PATH_CELLS) {
    const comma = key.indexOf(",");
    const tx = Number(key.slice(0, comma));
    const ty = Number(key.slice(comma + 1));
    if (tx < x || tx >= x2 || ty < y || ty >= y2) continue;
    const variant = pathCellVariant(tx, ty);
    fillRectWithTile(ctx, tx, ty, TILE, TILE, blendFn(tx, ty, variant));
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
