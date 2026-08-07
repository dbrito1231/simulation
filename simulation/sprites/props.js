"use strict";

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
  const originX = x - 12;
  const originY = y - 12;
  drawPixelGrid(ctx, originX, originY, grid, 3, false);
  if (season === "winter" && (stage === "lush" || stage === "healthy")) {
    const width = grid.reduce((max, row) => Math.max(max, row.length), 0) * 3;
    drawSnowCap(ctx, originX, originY, width, 3, { capExtra: 2 });
  }
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
