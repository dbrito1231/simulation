"use strict";

// =====================================================================
// Ambient wildlife (WILDLIFE_ENABLED). Server-authoritative huntable fauna
// projected via world.wildlife; index.html culls and applies cosmetic bob.
//
// Render dispatch (drawWildlifeCreature):
//   1. PNG sheet — user art in simulation/assets/wildlife/ (16 kinds)
//   2. Canvas silhouette helpers — fallback when sheet missing/unready
//   3. Procedural pixel grids (WILDLIFE_SPRITES) — last resort
//
// Rebuild atlas: uv run python scripts/build_wildlife_sheet.py
// Caller-side bob in index.html drawWildlife is additive; frameTick never
// invents a second position.
//
// Size tiers (dest max side from build script):
//   large — ≈44 px: deer, boar, cow, seal
//   mid   — ≈34 px: fox, turtle, rabbit, chicken, gull
//   small — ≈26 px: mouse, fish, crab, bee
// =====================================================================
const WILDLIFE_TIER_SCALE = { large: 2, mid: 2, small: 1 };
const WILDLIFE_CANVAS_SCALE_BY_TIER = { large: 1.8, mid: 1.3, small: 1.0 };

function wildlifeCanvasScaleForKind(kind) {
  const tier = WILDLIFE_SIZE_TIER[kind] || "mid";
  return WILDLIFE_CANVAS_SCALE_BY_TIER[tier];
}

const WILDLIFE_SIZE_TIER = {
  deer: "large",
  boar: "large",
  cow: "large",
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
  bee: "small",
};

function wildlifeScaleForKind(kind) {
  const tier = WILDLIFE_SIZE_TIER[kind] || "mid";
  return WILDLIFE_TIER_SCALE[tier];
}

// Per-kind alt-frame cadence (ticks per swap); defaults to 12 in drawer.
const WILDLIFE_ANIM_CADENCE = {
  bee: 8,
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

  cow: tileFromStrings([
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

  bee: {
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

// Wildlife PNG spritesheet. One preload, ready flag.
// Atlas built by scripts/build_wildlife_sheet.py from simulation/assets/wildlife/*.png.
// ---------------------------------------------------------------------
const WILDLIFE_SHEET_URL = "/wildlife.png";

// Kind → source rect(s). All 16 user-art kinds; canvas helpers remain fallback.
const WILDLIFE_SHEET_FRAMES = {
  deer: { sx: 2, sy: 2, sw: 70, sh: 125, destW: 25, destH: 44 },
  boar: { sx: 74, sy: 2, sw: 256, sh: 181, destW: 44, destH: 31 },
  cow: { sx: 332, sy: 2, sw: 195, sh: 119, destW: 44, destH: 27 },
  seal: { sx: 529, sy: 2, sw: 215, sh: 106, destW: 44, destH: 22 },
  fox: { sx: 746, sy: 2, sw: 83, sh: 87, destW: 32, destH: 34 },
  turtle: { sx: 2, sy: 185, sw: 198, sh: 107, destW: 34, destH: 18 },
  rabbit: { sx: 202, sy: 185, sw: 64, sh: 68, destW: 32, destH: 34 },
  chicken: { sx: 268, sy: 185, sw: 78, sh: 82, destW: 32, destH: 34 },
  gull: { sx: 348, sy: 185, sw: 173, sh: 104, destW: 34, destH: 20 },
  bird: { sx: 523, sy: 185, sw: 191, sh: 173, destW: 34, destH: 31 },
  owl: { sx: 716, sy: 185, sw: 157, sh: 206, destW: 26, destH: 34 },
  mouse: { sx: 875, sy: 185, sw: 96, sh: 82, destW: 26, destH: 22 },
  fish: { sx: 2, sy: 393, sw: 102, sh: 78, destW: 26, destH: 20 },
  crab: { sx: 106, sy: 393, sw: 168, sh: 133, destW: 26, destH: 21 },
  bee: { sx: 276, sy: 393, sw: 186, sh: 206, destW: 23, destH: 26 },
  squirrel: { sx: 464, sy: 393, sw: 179, sh: 143, destW: 26, destH: 21 },
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

// Canvas silhouette helpers (pre-sheet art, b28c054). Animated where noted;
// helpers draw relative to (x, y) as body center / anchor.
function drawFishRipple(ctx, x, y, frameTick) {
  const wobble = Math.sin(frameTick / 12) * 2;
  ctx.fillStyle = "#4FC3F7";
  ctx.beginPath();
  ctx.ellipse(x + wobble, y, 5, 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#0288D1";
  ctx.beginPath();
  ctx.moveTo(x - 5 + wobble, y);
  ctx.lineTo(x - 8 + wobble, y - 2);
  ctx.lineTo(x - 8 + wobble, y + 2);
  ctx.closePath();
  ctx.fill();
}

function drawBird(ctx, x, y, frameTick) {
  const flap = Math.abs(Math.sin(frameTick / 8)) * 4;
  ctx.strokeStyle = "#3E3226";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x - 5, y - flap);
  ctx.lineTo(x, y);
  ctx.lineTo(x + 5, y - flap);
  ctx.stroke();
}

function drawSquirrel(ctx, x, y, frameTick) {
  const flick = Math.sin(frameTick / 10) * 1.5;
  ctx.fillStyle = "#8D6E63";
  ctx.beginPath();
  ctx.ellipse(x, y, 3.5, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 3, y - 2, 2, 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#6D4C41";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(x - 3, y);
  ctx.quadraticCurveTo(x - 7, y - 5 + flick, x - 2, y - 6);
  ctx.stroke();
}

function drawDeer(ctx, x, y) {
  ctx.fillStyle = "#A1887F";
  ctx.beginPath();
  ctx.ellipse(x, y, 6, 3.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 5, y - 3, 2.5, 2.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#6D4C41";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(x + 4, y - 5);
  ctx.lineTo(x + 3, y - 8);
  ctx.moveTo(x + 6, y - 5);
  ctx.lineTo(x + 7, y - 8);
  ctx.stroke();
  ctx.fillStyle = "#5D4037";
  ctx.fillRect(x - 4, y + 2, 1.5, 3);
  ctx.fillRect(x + 2, y + 2, 1.5, 3);
}

function drawFox(ctx, x, y) {
  ctx.fillStyle = "#E67E22";
  ctx.beginPath();
  ctx.ellipse(x, y, 5, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.moveTo(x + 4, y - 1);
  ctx.lineTo(x + 7, y - 3);
  ctx.lineTo(x + 7, y + 1);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#F5D6A8";
  ctx.beginPath();
  ctx.ellipse(x - 4, y, 2, 1.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#3E3226";
  ctx.fillRect(x - 3, y + 2, 1.5, 2);
  ctx.fillRect(x + 2, y + 2, 1.5, 2);
}

function drawBoar(ctx, x, y) {
  ctx.fillStyle = "#5D4037";
  ctx.beginPath();
  ctx.ellipse(x, y, 5.5, 3.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 4, y - 1, 2.5, 2.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#EFEBE0";
  ctx.fillRect(x + 5, y + 1, 2, 1.5);
  ctx.fillStyle = "#3E2723";
  ctx.fillRect(x - 3, y + 2, 2, 2);
  ctx.fillRect(x + 1, y + 2, 2, 2);
}

function drawOwl(ctx, x, y, frameTick) {
  const blink = Math.abs(Math.sin(frameTick / 40)) > 0.95 ? 0.5 : 1.5;
  ctx.fillStyle = "#795548";
  ctx.beginPath();
  ctx.ellipse(x, y, 4, 4.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#EFEBE0";
  ctx.beginPath();
  ctx.ellipse(x - 1.5, y - 1, 1.5, blink, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 1.5, y - 1, 1.5, blink, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#F4A261";
  ctx.beginPath();
  ctx.moveTo(x, y + 0.5);
  ctx.lineTo(x - 1.5, y + 2);
  ctx.lineTo(x + 1.5, y + 2);
  ctx.closePath();
  ctx.fill();
}

function drawRabbit(ctx, x, y, frameTick) {
  const ear = Math.sin(frameTick / 15) * 0.5;
  ctx.fillStyle = "#D7CCC8";
  ctx.beginPath();
  ctx.ellipse(x, y, 3.5, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x - 1.5, y - 5 + ear, 1.2, 3, -0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 1.5, y - 5 - ear, 1.2, 3, 0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#3E3226";
  ctx.fillRect(x - 1, y - 0.5, 1, 1);
}

function drawMouse(ctx, x, y, frameTick) {
  const twitch = Math.sin(frameTick / 8) * 1;
  ctx.fillStyle = "#9E9E9E";
  ctx.beginPath();
  ctx.ellipse(x, y, 3, 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 2.5, y - 0.5, 1.5, 1.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#757575";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(x - 3, y);
  ctx.quadraticCurveTo(x - 6, y + twitch, x - 5, y - 3);
  ctx.stroke();
  ctx.fillStyle = "#3E3226";
  ctx.fillRect(x + 3, y - 0.5, 1, 1);
}

function drawBee(ctx, x, y, frameTick) {
  const flap = Math.abs(Math.sin(frameTick / 6)) * 3;
  ctx.fillStyle = "#FFD54F";
  ctx.beginPath();
  ctx.ellipse(x - 2 - flap * 0.2, y - 1, 2.5 + flap * 0.15, 3.5, -0.3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#FFF8E1";
  ctx.beginPath();
  ctx.ellipse(x + 2 + flap * 0.2, y - 1, 2.5 + flap * 0.15, 3.5, 0.3, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#F9A825";
  ctx.beginPath();
  ctx.ellipse(x, y, 2, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#3E3226";
  ctx.fillRect(x - 0.5, y - 2, 1, 4);
}

function drawCrab(ctx, x, y, frameTick) {
  const scuttle = Math.sin(frameTick / 10) * 1;
  ctx.fillStyle = "#EF5350";
  ctx.beginPath();
  ctx.ellipse(x + scuttle, y, 4, 2.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#C62828";
  ctx.lineWidth = 1.2;
  ctx.beginPath();
  ctx.moveTo(x - 3 + scuttle, y - 1);
  ctx.lineTo(x - 6 + scuttle, y - 3);
  ctx.moveTo(x + 3 + scuttle, y - 1);
  ctx.lineTo(x + 6 + scuttle, y - 3);
  ctx.stroke();
  ctx.fillStyle = "#B71C1C";
  ctx.fillRect(x - 4 + scuttle, y + 2, 1.5, 2);
  ctx.fillRect(x + 2 + scuttle, y + 2, 1.5, 2);
}

function drawGull(ctx, x, y, frameTick) {
  const flap = Math.abs(Math.sin(frameTick / 9)) * 3.5;
  ctx.strokeStyle = "#ECEFF1";
  ctx.lineWidth = 1.8;
  ctx.beginPath();
  ctx.moveTo(x - 6, y - flap);
  ctx.lineTo(x, y);
  ctx.lineTo(x + 6, y - flap);
  ctx.stroke();
  ctx.fillStyle = "#FFB74D";
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(x + 3, y + 1);
  ctx.lineTo(x, y + 2);
  ctx.closePath();
  ctx.fill();
}

function drawTurtle(ctx, x, y) {
  ctx.fillStyle = "#558B2F";
  ctx.beginPath();
  ctx.ellipse(x, y, 5, 3.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#33691E";
  ctx.beginPath();
  ctx.ellipse(x, y, 3.5, 2.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#7CB342";
  ctx.beginPath();
  ctx.ellipse(x + 5, y, 2, 1.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillRect(x - 5, y + 1, 2, 2);
  ctx.fillRect(x + 1, y + 2, 2, 2);
}

function drawSeal(ctx, x, y, frameTick) {
  const glide = Math.sin(frameTick / 14) * 1.5;
  ctx.fillStyle = "#607D8B";
  ctx.beginPath();
  ctx.ellipse(x + glide, y, 6, 3, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.ellipse(x + 5 + glide, y - 1, 2.5, 2.5, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#455A64";
  ctx.beginPath();
  ctx.moveTo(x - 5 + glide, y);
  ctx.lineTo(x - 8 + glide, y - 2);
  ctx.lineTo(x - 7 + glide, y + 2);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#263238";
  ctx.fillRect(x + 6 + glide, y - 1.5, 1, 1);
}

const WILDLIFE_CANVAS_HELPERS = {
  fish: drawFishRipple,
  bird: drawBird,
  squirrel: drawSquirrel,
  deer: drawDeer,
  fox: drawFox,
  boar: drawBoar,
  owl: drawOwl,
  rabbit: drawRabbit,
  mouse: drawMouse,
  bee: drawBee,
  crab: drawCrab,
  gull: drawGull,
  turtle: drawTurtle,
  seal: drawSeal,
};

function drawWildlifeCreatureWithCanvasHelper(ctx, kind, x, y, frameTick) {
  const helper = WILDLIFE_CANVAS_HELPERS[kind];
  if (!helper) return false;
  const s = wildlifeCanvasScaleForKind(kind);
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(s, s);
  helper(ctx, 0, 0, frameTick);
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
  if (drawWildlifeCreatureWithCanvasHelper(ctx, kind, x, y, frameTick)) return;
  drawWildlifeCreatureProcedural(ctx, kind, x, y, frameTick);
}

preloadWildlifeSheet();
