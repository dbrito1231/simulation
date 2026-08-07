"use strict";

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

function drawTiledWorld(ctx, worldW, worldH, frameTick, structures, districts, roadNodes, roadEdges, season = "summer", ecologyStages = null, stageTimings = null, terrainVisualOpts = null) {
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
    if (terrainVisualOpts && terrainVisualOpts.seasonalV2 && terrainVisualOpts.applyDistrictTint) {
      const stage = (ecologyStages && ecologyStages[d.id]) || "healthy";
      terrainVisualOpts.applyDistrictTint(ctx, season, d.kind, stage, d.bounds);
      if (season === "winter") drawWinterGroundAccentsForDistrict(ctx, d, season);
    }
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
