"use strict";

// --- Agent sprites: role-keyed 24×32 generator + legacy 16×16 name grids ---

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

// Role-keyed sprites ported from the Claude Design generator
// (.cursor/plans/Council Sprites.dc.html). Each role maps to a procedural
// 24×32 grid; drawn at scale=1 so on-screen height stays 32px (same anchor
// math as the legacy 16-row grids at scale 2). makeSprite is expensive (two
// full-buffer neighbor passes) — never call per draw; cache per role+frame.
const _ROLE_SPRITE_W = 24;
const _ROLE_SPRITE_H = 32;

function _roleSpriteHx(c) {
  c = c.replace("#", "");
  return [parseInt(c.slice(0, 2), 16), parseInt(c.slice(2, 4), 16), parseInt(c.slice(4, 6), 16)];
}
function _roleSpriteMix(a, b, t) {
  const A = _roleSpriteHx(a), B = _roleSpriteHx(b);
  return [
    Math.round(A[0] + (B[0] - A[0]) * t),
    Math.round(A[1] + (B[1] - A[1]) * t),
    Math.round(A[2] + (B[2] - A[2]) * t),
  ];
}
function _roleSpriteRgbHex(r) {
  return "#" + r.map((v) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, "0")).join("");
}
function _roleSpriteShift(hex, deg) {
  if (!deg) return hex;
  let [r, g, b] = _roleSpriteHx(hex).map((v) => v / 255);
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  const l = (mx + mn) / 2;
  const s = d === 0 ? 0 : d / (1 - Math.abs(2 * l - 1));
  if (d !== 0) {
    if (mx === r) h = ((g - b) / d) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  h = (h + deg + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  let p = [0, 0, 0];
  if (h < 60) p = [c, x, 0];
  else if (h < 120) p = [x, c, 0];
  else if (h < 180) p = [0, c, x];
  else if (h < 240) p = [0, x, c];
  else if (h < 300) p = [x, 0, c];
  else p = [c, 0, x];
  return _roleSpriteRgbHex(p.map((v) => Math.round((v + m) * 255)));
}

function _roleSpriteBufToGrid(buf) {
  const rows = [];
  for (let y = 0; y < _ROLE_SPRITE_H; y++) {
    const row = [];
    for (let x = 0; x < _ROLE_SPRITE_W; x++) row.push(buf[y * _ROLE_SPRITE_W + x] || null);
    rows.push(row);
  }
  return rows;
}

function _roleSpriteMake(def, hue, walk) {
  const W = _ROLE_SPRITE_W, H = _ROLE_SPRITE_H;
  const buf = new Array(W * H).fill(null);
  const P = {};
  Object.keys(def.pal).forEach((k) => {
    P[k] = (k === "skin" || k === "eye") ? def.pal[k] : _roleSpriteShift(def.pal[k], hue);
  });
  const set = (x, y, c) => { if (x >= 0 && x < W && y >= 0 && y < H && c) buf[y * W + x] = c; };
  const rect = (x, y, w, h, c) => { for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) set(x + i, y + j, c); };

  const broad = def.build === "broad";
  const tx = broad ? 7 : 8, tw = broad ? 10 : 8;
  const dark = (c) => _roleSpriteRgbHex(_roleSpriteMix(c, "#120a08", 0.32));
  const lite = (c) => _roleSpriteRgbHex(_roleSpriteMix(c, "#ffffff", 0.18));

  // Walk frame: spread leg/boot columns ±1px (mirrors GENERIC_AGENT_WALK).
  const legL = walk ? 7 : 8;
  const legR = walk ? 14 : 13;

  if (def.cape) { rect(tx - 1, 15, tw + 2, 12, dark(P.accent)); rect(tx, 26, tw, 1, dark(P.accent)); }

  rect(legL, 24, 3, 7, P.pants);
  rect(legR, 24, 3, 7, P.pants);
  rect(legL, 30, 3, 2, P.boot);
  rect(legR, 30, 3, 2, P.boot);
  rect(tx, 15, tw, 10, P.cloth);
  rect(tx, 22, tw, 1, P.belt);
  set(11, 22, P.metal);
  set(12, 22, P.metal);
  rect(tx - 2, 16, 2, 7, dark(P.cloth));
  rect(tx + tw, 16, 2, 7, dark(P.cloth));
  rect(tx - 2, 23, 2, 2, P.skin);
  rect(tx + tw, 23, 2, 2, P.skin);
  if (broad) { rect(tx - 1, 15, 3, 2, P.accent); rect(tx + tw - 2, 15, 3, 2, P.accent); }
  rect(11, 13, 2, 2, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#000000", 0.22)));
  rect(8, 5, 8, 9, P.skin);
  rect(8, 5, 8, 1, lite(P.skin));

  const hair = P.hair;
  if (def.hair === "short") { rect(8, 4, 8, 3, hair); rect(8, 7, 1, 3, hair); rect(15, 7, 1, 3, hair); }
  else if (def.hair === "long") { rect(8, 4, 8, 3, hair); rect(7, 7, 2, 9, hair); rect(15, 7, 2, 9, hair); }
  else if (def.hair === "bun") { rect(8, 4, 8, 3, hair); rect(10, 1, 4, 3, hair); rect(8, 7, 1, 2, hair); rect(15, 7, 1, 2, hair); }
  else if (def.hair === "mohawk") { rect(11, 1, 3, 4, hair); rect(8, 4, 8, 2, dark(hair)); }
  else if (def.hair === "flow") { rect(8, 4, 8, 3, hair); rect(15, 7, 2, 7, hair); rect(8, 7, 1, 2, hair); }
  else if (def.hair === "bald") { rect(8, 4, 8, 2, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#000000", 0.12))); }

  if (def.gear === "hood") {
    rect(7, 3, 10, 5, P.accent);
    rect(7, 8, 2, 7, P.accent);
    rect(15, 8, 2, 7, P.accent);
    rect(8, 8, 8, 1, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#000000", 0.3)));
  } else if (def.gear === "helm") {
    rect(7, 3, 10, 5, P.metal);
    rect(7, 3, 10, 1, lite(P.metal));
    rect(11, 7, 2, 5, P.metal);
    rect(7, 8, 1, 4, P.metal);
    rect(16, 8, 1, 4, P.metal);
  } else if (def.gear === "cap") {
    rect(7, 3, 10, 3, P.accent);
    rect(6, 6, 12, 1, dark(P.accent));
  } else if (def.gear === "crown") {
    rect(8, 3, 8, 2, P.metal);
    set(8, 2, P.metal);
    set(11, 1, P.metal);
    set(12, 1, P.metal);
    set(15, 2, P.metal);
  } else if (def.gear === "bandana") {
    rect(7, 4, 10, 2, P.accent);
    rect(16, 6, 2, 5, P.accent);
  }

  const eye = P.eye || "#20140f";
  rect(10, 9, 1, 2, eye);
  rect(13, 9, 1, 2, eye);
  set(9, 9, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#ffffff", 0.25)));
  set(14, 9, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#ffffff", 0.25)));
  rect(11, 12, 2, 1, _roleSpriteRgbHex(_roleSpriteMix(P.skin, "#000000", 0.28)));

  if (def.prop === "staff") { rect(19, 8, 1, 18, P.wood); rect(18, 5, 3, 3, P.accent); set(19, 4, lite(P.accent)); }
  else if (def.prop === "sword") { rect(19, 11, 1, 10, P.metal); rect(18, 21, 3, 1, P.wood); rect(19, 22, 1, 2, P.wood); }
  else if (def.prop === "book") { rect(17, 19, 4, 4, P.accent); rect(18, 20, 2, 2, "#efe0c4"); }
  else if (def.prop === "spear") { rect(19, 4, 1, 22, P.wood); rect(18, 2, 3, 3, P.metal); }

  const out = new Array(W * H).fill(null);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const c = buf[y * W + x];
    if (!c) continue;
    const right = x + 1 < W ? buf[y * W + x + 1] : null;
    const left = x - 1 >= 0 ? buf[y * W + x - 1] : null;
    const below = y + 1 < H ? buf[(y + 1) * W + x] : null;
    if (!right || !below) out[y * W + x] = _roleSpriteRgbHex(_roleSpriteMix(c, "#180d09", 0.28));
    else if (!left && y < 18) out[y * W + x] = _roleSpriteRgbHex(_roleSpriteMix(c, "#fff2d8", 0.16));
    else out[y * W + x] = c;
  }
  const fin = out.slice();
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (out[y * W + x]) continue;
    const n = [[1, 0], [-1, 0], [0, 1], [0, -1]].map((d) => {
      const nx = x + d[0], ny = y + d[1];
      return (nx >= 0 && nx < W && ny >= 0 && ny < H) ? out[ny * W + nx] : null;
    }).filter(Boolean);
    if (n.length) fin[y * W + x] = _roleSpriteRgbHex(_roleSpriteMix(n[0], "#0d0705", 0.62));
  }
  return _roleSpriteBufToGrid(fin);
}

const _ROLE_SPRITE_METAL = "#c9ae86";
const _ROLE_SPRITE_WOOD = "#7a4c28";
const _ROLE_SPRITE_EYE = "#2a160f";

const ROLE_SPRITE_DEFS = {
  guard: {
    build: "broad", hair: "short", gear: "helm", cape: true, prop: "sword",
    pal: { skin: "#e0a77f", eye: _ROLE_SPRITE_EYE, hair: "#6d3a26", cloth: "#c4392b", pants: "#4a2a20", boot: "#2e1b14", belt: "#6b3a24", accent: "#e0a63c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  healer: {
    build: "slim", hair: "long", gear: "hood", cape: false, prop: "staff",
    pal: { skin: "#d99a72", eye: _ROLE_SPRITE_EYE, hair: "#5c3a22", cloth: "#dcae3c", pants: "#5a3a22", boot: "#33201a", belt: "#7a4c28", accent: "#b8862c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  scout: {
    build: "slim", hair: "flow", gear: "cap", cape: false, prop: "none",
    pal: { skin: "#f0bb92", eye: _ROLE_SPRITE_EYE, hair: "#a8412a", cloth: "#dd7a38", pants: "#54321f", boot: "#33201a", belt: "#6b3a24", accent: "#8f3b26", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  trader: {
    build: "broad", hair: "short", gear: "bandana", cape: false, prop: "none",
    pal: { skin: "#c98a5f", eye: _ROLE_SPRITE_EYE, hair: "#d6b25c", cloth: "#2f6f6b", pants: "#3f2a20", boot: "#2b1a13", belt: "#6b3a24", accent: "#e0a63c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  builder: {
    build: "broad", hair: "bald", gear: "helm", cape: false, prop: "spear",
    pal: { skin: "#b8825c", eye: _ROLE_SPRITE_EYE, hair: "#3a2a20", cloth: "#5a7268", pants: "#3a3230", boot: "#241a16", belt: "#5a4030", accent: "#8fa3a8", metal: "#b8c2c4", wood: "#6b4526" },
  },
  gatherer: {
    build: "slim", hair: "long", gear: "hood", cape: true, prop: "none",
    pal: { skin: "#e8b087", eye: _ROLE_SPRITE_EYE, hair: "#2f5a2a", cloth: "#4d8a3c", pants: "#3f3222", boot: "#2b1f14", belt: "#6b4a24", accent: "#356b2e", metal: _ROLE_SPRITE_METAL, wood: "#6b4526" },
  },
  elder: {
    build: "slim", hair: "bun", gear: "none", cape: false, prop: "book",
    pal: { skin: "#f0c39a", eye: _ROLE_SPRITE_EYE, hair: "#d8b45c", cloth: "#6b4a8a", pants: "#3a3a3a", boot: "#241c1a", belt: "#5f4530", accent: "#c96a3c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  hunter: {
    build: "broad", hair: "mohawk", gear: "none", cape: false, prop: "sword",
    pal: { skin: "#c78a5e", eye: _ROLE_SPRITE_EYE, hair: "#8f3b26", cloth: "#a86a2c", pants: "#4a3020", boot: "#2b1a13", belt: "#6b3a24", accent: "#e0a63c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  farmer: {
    build: "slim", hair: "short", gear: "cap", cape: false, prop: "none",
    pal: { skin: "#e0a77f", eye: _ROLE_SPRITE_EYE, hair: "#6d3a26", cloth: "#8fa84a", pants: "#4a2a20", boot: "#2e1b14", belt: "#6b3a24", accent: "#e0a63c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  fisher: {
    build: "slim", hair: "flow", gear: "bandana", cape: false, prop: "spear",
    pal: { skin: "#e0a77f", eye: _ROLE_SPRITE_EYE, hair: "#5c3a22", cloth: "#3a7fa8", pants: "#3f2a20", boot: "#2b1a13", belt: "#6b3a24", accent: "#5a9fc0", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  miner: {
    build: "slim", hair: "short", gear: "helm", cape: false, prop: "none",
    pal: { skin: "#d99a72", eye: _ROLE_SPRITE_EYE, hair: "#3a2a20", cloth: "#5f5a80", pants: "#3a3230", boot: "#241a16", belt: "#5a4030", accent: "#8fa3a8", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  blacksmith: {
    build: "broad", hair: "short", gear: "cap", cape: false, prop: "staff",
    pal: { skin: "#c98a5f", eye: _ROLE_SPRITE_EYE, hair: "#6d3a26", cloth: "#4a3f3a", pants: "#3a2a20", boot: "#2b1a13", belt: "#6b3a24", accent: "#c96a3c", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
  explorer: {
    build: "slim", hair: "short", gear: "cap", cape: true, prop: "staff",
    pal: { skin: "#c78a5e", eye: _ROLE_SPRITE_EYE, hair: "#5c3a22", cloth: "#c9b58a", pants: "#3f3222", boot: "#2b1f14", belt: "#6b4a24", accent: "#6b4526", metal: _ROLE_SPRITE_METAL, wood: _ROLE_SPRITE_WOOD },
  },
};

const _roleSpriteCache = {};
function roleAgentSprite(role, walk) {
  const frame = walk ? "walk" : "stand";
  const key = role + ":" + frame;
  if (_roleSpriteCache[key]) return _roleSpriteCache[key];
  const def = ROLE_SPRITE_DEFS[role];
  if (!def) return null;
  const grid = _roleSpriteMake(def, 0, walk);
  _roleSpriteCache[key] = grid;
  return grid;
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

// Small post-body pixels keep seasonal dress readable without replacing the
// baked-in role art (hats/hoods/props) or the legacy named accessory strips.
// Offsets are derived from the drawn grid's real w/h so they land on the same
// anatomy at both scale=1 (24×32 role sprites) and scale=2 (16×16 legacy).
function drawSeasonalAgentAccent(ctx, agent, grid, scale, flipX) {
  if (!seasonalAgentAccentsEnabled || agent.deceased) return;
  const w = grid[0].length * scale;
  const h = grid.length * scale;
  const ox = Math.round(agent.x - w / 2);
  const oy = Math.round(agent.y - h + scale * 2);
  const side = flipX ? ox + Math.round(w * 3 / 16) : ox + w - Math.round(w * 4 / 16);
  ctx.save();
  if (spriteSeason === "winter") {
    ctx.fillStyle = "#DCEAF2"; // wool cap + scarf
    ctx.fillRect(ox + Math.round(w / 4), oy + Math.round(h / 8), Math.round(w / 2), scale);
    ctx.fillRect(ox + Math.round(w * 5 / 16), oy + Math.round(h / 2), Math.round(w * 7 / 16), scale);
    ctx.fillRect(side, oy + Math.round(h * 9 / 16), scale, scale * 2);
  } else if (spriteSeason === "spring") {
    ctx.fillStyle = "#8BC34A"; // leaf pin
    ctx.fillRect(side, oy + Math.round(h * 7 / 16), scale, scale);
    ctx.fillStyle = "#C5E86C";
    ctx.fillRect(side + (flipX ? scale : -scale), oy + Math.round(h * 6 / 16), scale, scale);
  } else if (spriteSeason === "summer") {
    ctx.fillStyle = "#F7D774"; // straw hat brim
    ctx.fillRect(ox + Math.round(w * 3 / 16), oy + Math.round(h / 8), Math.round(w * 10 / 16), scale);
    ctx.fillStyle = "#C8923F";
    ctx.fillRect(ox + Math.round(w * 5 / 16), oy + Math.round(h / 16), Math.round(w * 6 / 16), scale);
  } else if (spriteSeason === "autumn") {
    ctx.fillStyle = "#C96C35"; // warm scarf
    ctx.fillRect(ox + Math.round(w * 5 / 16), oy + Math.round(h / 2), Math.round(w * 7 / 16), scale);
    ctx.fillRect(side, oy + Math.round(h * 9 / 16), scale, scale * 2);
  }
  ctx.restore();
}

function drawAgentSprite(ctx, agent, frameTick) {
  if (agent.deceased && agent.buried) {
    // Permanent death, laid to rest: tombstone in the cemetery grid only.
    drawPixelSprite(ctx, agent.x, agent.y, tombstoneSprite(agent), 2, false);
    return;
  }
  const moving = Math.abs(agent.targetX - agent.x) > 1 || Math.abs(agent.targetY - agent.y) > 1;
  const walkFrame = moving && Math.floor(frameTick / 12) % 2 === 1;
  const flipX = agent.targetX < agent.x - 0.5;

  let grid;
  let scale;
  let drewRoleSprite = false;
  if (ROLE_SPRITE_DEFS[agent.role]) {
    grid = roleAgentSprite(agent.role, walkFrame);
    scale = 1; // 32 rows × 1 = 32px tall (matches legacy 16-row @ scale 2)
    drewRoleSprite = true;
  } else {
    const data = AGENT_SPRITES[agent.name] || genericAgentSprite(agent);
    grid = walkFrame ? data.walk : data.stand;
    scale = 2;
  }
  drawPixelSprite(ctx, agent.x, agent.y, grid, scale, flipX);

  if (!drewRoleSprite) {
    const acc = ACCESSORIES[agent.name];
    if (acc) {
      const w = grid[0].length * scale;
      const h = grid.length * scale;
      const ox = Math.round(agent.x - w / 2);
      const oy = Math.round(agent.y - h + scale * 2);
      drawPixelGrid(ctx, ox + scale * 4, oy, acc, scale, flipX);
    }
  }
  drawSeasonalAgentAccent(ctx, agent, grid, scale, flipX);

  // Sid-parity Phase 3: tint living agents by dominant belief id.
  const beliefIds = agent.beliefIds || [];
  if (beliefIds.length && !agent.deceased && !agent.incapacitated) {
    const tint = BELIEF_TINTS[beliefIds[0]];
    if (tint) {
      const h = grid.length * scale;
      const oy = Math.round(agent.y - h + scale * 2);
      ctx.save();
      ctx.globalAlpha = 0.28;
      ctx.fillStyle = tint;
      ctx.beginPath();
      // Head center ~row 9 on role sprites; oy+4 still lands on the head at 32px.
      ctx.arc(agent.x, oy + Math.max(4, Math.round(h * 9 / 32)), 5, 0, Math.PI * 2);
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
