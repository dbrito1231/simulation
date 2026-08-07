"use strict";

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
function drawWeatherParticle(ctx, kind, x, y, index, opts) {
  opts = opts || {};
  const alphaMult = opts.alphaMult != null ? opts.alphaMult : 1;
  ctx.save();
  if (kind === "snow") {
    const drift = Math.sin((x + index) * 0.07 + (opts.frameTick || 0) * 0.02) * 2;
    const px = Math.round(x + drift);
    const py = Math.round(y);
    const alpha = (0.82 * alphaMult).toFixed(3);
    ctx.fillStyle = `rgba(235, 245, 255, ${alpha})`;
    ctx.fillRect(px - 1, py, 3, 1);
    ctx.fillRect(px, py - 1, 1, 3);
    ctx.beginPath();
    ctx.arc(px, py, 1, 0, Math.PI * 2);
    ctx.fill();
  } else {
    const sheet = opts.sheet === true;
    const angleDeg = ((opts.windHash != null ? opts.windHash : 0.5) * 50) - 25;
    const len = 8 + (opts.lenHash != null ? opts.lenHash : 0.5) * 8;
    const rad = angleDeg * Math.PI / 180;
    const xOff = Math.sin(rad) * len;
    const yOff = Math.cos(rad) * len;
    const baseAlpha = sheet ? 0.86 : 0.68;
    const alpha = Math.min(1, baseAlpha * alphaMult).toFixed(3);
    ctx.strokeStyle = `rgba(190, 210, 230, ${alpha})`;
    ctx.lineWidth = sheet ? 2.5 : 1;
    ctx.beginPath();
    ctx.moveTo(Math.round(x), Math.round(y));
    ctx.lineTo(Math.round(x + xOff), Math.round(y + yOff));
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
