"use strict";

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
