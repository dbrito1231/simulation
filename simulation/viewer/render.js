// =====================================================================
// Convenience accessors — render functions below read these like the old globals.
// =====================================================================
function getAgents() { return world.agents || []; }
function getLivingAgents() { return getAgents().filter((a) => !a.deceased); }
function getDeceasedAgents() { return getAgents().filter((a) => a.deceased); }
function getCiv() { return world.civilization || {}; }
function resourceRegistry() { return getCiv().resourceRegistry || {}; }

// =====================================================================
// Drawing — terrain cache + agents + structures (reuses sprites.js unchanged).
// =====================================================================

// Living-ecosystem Phase 2 (CROP_GROWTH_ENABLED): districtEcology's per-
// district stage, reduced to just the {districtId: stage} map drawStarterProps
// consumes, and a stable string key for cache-invalidation comparison (same
// role lastSeasonRendered plays for season). Returns (null, null) when the
// flag is off or the server hasn't sent the projection yet, so the terrain
// cache renders exactly as it did before this feature existed.
function ecologyStagesForTerrain() {
  if (!CROP_GROWTH_ENABLED) return [null, null];
  const eco = world.districtEcology;
  if (!eco || !eco.length) return [null, null];
  const stages = {};
  for (const e of eco) stages[e.districtId] = e.stage;
  const key = eco.map((e) => e.districtId + ":" + e.stage).sort().join(",");
  return [stages, key];
}

// Render the static terrain once into offscreen canvases. drawTiledWorld paints
// in logical coordinates, so a WORLD_W×WORLD_H buffer (no DPR transform) is
// blitted under the main ctx's DPR transform and stays crisp (imageSmoothing
// off). The animated ocean strip is pre-rendered as 16 foam phases so animation
// costs one drawImage/frame instead of re-tiling the strip. Rebuilt whenever
// districtsKey changes (a district was founded) via pollDistricts(), the
// season changes, or (CROP_GROWTH_ENABLED) a district's ecology stage changes.
function buildTerrainCache() {
  if (VIEWER_LOAD_DEBUG) performance.mark("viewer:terrain-build-start");
  const buildT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const stageTimings = VIEWER_LOAD_DEBUG ? {} : null;

  const districts = getDistricts();
  terrainCanvas = document.createElement("canvas");
  terrainCanvas.width = WORLD_W;
  terrainCanvas.height = WORLD_H;
  const tctx = terrainCanvas.getContext("2d");
  tctx.imageSmoothingEnabled = false;
  const season = world.calendar && world.calendar.season;
  const [ecologyStages, ecologyStageKey] = ecologyStagesForTerrain();

  const drawT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const terrainVisualOpts = { seasonalV2: true, applyDistrictTint: applySeasonTintForKind };
  drawTiledWorld(tctx, WORLD_W, WORLD_H, 0, null, districts,
                 districtsData.roadNodes, districtsData.roadEdges, season || "summer", ecologyStages, stageTimings, terrainVisualOpts);
  const drawTiledWorldMs = VIEWER_LOAD_DEBUG ? performance.now() - drawT0 : 0;

  const tintT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  const seasonTintMs = VIEWER_LOAD_DEBUG ? performance.now() - tintT0 : 0;
  lastEcologyStageKeyRendered = ecologyStageKey;
  lastTerrainVisualKeyRendered = terrainVisualCacheKey(season, ecologyStageKey);

  // The ocean's animated foam strip is scoped to the ocean district's own
  // bounds (not the full world height) -- with the much larger world, a
  // full-height strip would paint a blue stripe down through the open
  // frontier below the starter core.
  const oceanBounds = findDistrictBounds("ocean") || { x1: 0, y1: 120, x2: 200, y2: 880 };
  oceanFrames = { bounds: oceanBounds, frames: [] };
  const ow = oceanBounds.x2 - oceanBounds.x1;
  const oh = oceanBounds.y2 - oceanBounds.y1;
  const oceanT0 = VIEWER_LOAD_DEBUG ? performance.now() : 0;
  for (let phase = 0; phase < 16; phase++) {
    const oc = document.createElement("canvas");
    oc.width = ow;
    oc.height = oh;
    const octx = oc.getContext("2d");
    octx.imageSmoothingEnabled = false;
    fillRectWithTile(octx, 0, 0, ow, oh, oceanTile(phase));
    oceanFrames.frames.push(oc);
  }
  const oceanFramesMs = VIEWER_LOAD_DEBUG ? performance.now() - oceanT0 : 0;
  lastSeasonRendered = season || null;

  if (VIEWER_LOAD_DEBUG) {
    const totalMs = performance.now() - buildT0;
    performance.mark("viewer:terrain-build-end");
    console.info("[viewer-load] buildTerrainCache", {
      totalMs: +totalMs.toFixed(1),
      drawTiledWorldMs: +drawTiledWorldMs.toFixed(1),
      baseFillMs: +(stageTimings.baseFillMs || 0).toFixed(1),
      districtPassesMs: +(stageTimings.districtPassesMs || 0).toFixed(1),
      propsMs: +(stageTimings.propsMs || 0).toFixed(1),
      seasonTintMs: +seasonTintMs.toFixed(1),
      oceanFramesMs: +oceanFramesMs.toFixed(1),
      msSinceScriptStart: +(performance.now() - VIEWER_LOAD_T0).toFixed(1),
      season: season || "summer",
      devicePixelRatio: window.devicePixelRatio,
    });
  }
}

// The dock (drawn at world 150,470, 144x36) straddles the beach/ocean line, so
// part of it falls inside the ocean strip the foam overlay repaints every
// frame. Re-blit those exact pixels from the static cache afterward so the
// jetty (incl. its pilings dipping into the water) isn't erased by the animation.
const DOCK_RECT = { x: 150, y: 470, w: 144, h: 36 };

// Night lighting: a cool translucent overlay whose alpha follows the day
// phase (calendar.dayFraction; night is the last 25% of each day). Wider
// twilight bands and deeper peak alpha than the pre-atmosphere-pack baseline.
const MAX_NIGHT_ALPHA = 0.58;
const TWILIGHT_START = 0.62;
const TWILIGHT_END_DUSK = 0.78;
const TWILIGHT_START_DAWN = 0.92;
const GOLDEN_HOUR_MAX = 0.22;
const LIGHT_GLOW_RADIUS = 200;
const LIGHT_GLOW_HALO_RADIUS = 280;
const NIGHT_DESAT_ALPHA = 0.08;

function nightAlpha(cal) {
  if (!cal) return 0;
  const f = cal.dayFraction;
  if (f == null) return cal.isNight ? MAX_NIGHT_ALPHA : 0;
  if (f < TWILIGHT_START) return 0;
  if (f < TWILIGHT_END_DUSK) {
    const t = (f - TWILIGHT_START) / (TWILIGHT_END_DUSK - TWILIGHT_START);
    return MAX_NIGHT_ALPHA * (t * t);
  }
  if (f < TWILIGHT_START_DAWN) return MAX_NIGHT_ALPHA;
  const t = (1.0 - f) / (1.0 - TWILIGHT_START_DAWN);
  return MAX_NIGHT_ALPHA * (1 - (1 - t) * (1 - t));
}

function goldenHourAlpha(cal) {
  if (!ENV_EFFECTS_ENABLED || !cal || cal.dayFraction == null) return 0;
  const f = cal.dayFraction;
  const band = (start, end) => {
    if (f < start || f >= end) return 0;
    return Math.sin(((f - start) / (end - start)) * Math.PI);
  };
  return GOLDEN_HOUR_MAX * Math.max(
    band(TWILIGHT_START, TWILIGHT_END_DUSK),
    band(TWILIGHT_START_DAWN, 1.00)
  );
}

function drawGoldenHourOverlay(ctx, cal) {
  const gha = goldenHourAlpha(cal);
  if (gha <= 0) return;
  ctx.fillStyle = `rgba(255, 177, 78, ${gha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  const f = cal.dayFraction;
  const edgeBand = (start, end) => {
    if (f < start || f >= end) return 0;
    return Math.sin(((f - start) / (end - start)) * Math.PI);
  };
  const rimAlpha = GOLDEN_HOUR_MAX * 0.45 * Math.max(
    edgeBand(TWILIGHT_START, TWILIGHT_START + 0.04),
    edgeBand(TWILIGHT_END_DUSK - 0.04, TWILIGHT_END_DUSK),
    edgeBand(TWILIGHT_START_DAWN, TWILIGHT_START_DAWN + 0.04),
    edgeBand(0.98, 1.00)
  );
  if (rimAlpha <= 0) return;
  const prevOp = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = "lighter";
  ctx.fillStyle = `rgba(255, 140, 160, ${rimAlpha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  ctx.globalCompositeOperation = prevOp;
}

// Weather sky tint (WEATHER_ENABLED, living-ecosystem Phase 4): darkens the
// same full-canvas overlay stage as the night overlay/golden-hour band as
// weather.state worsens -- clear/gathering/storm/clearing map to a fixed
// per-state alpha (a step ramp across the state machine, not a within-state
// animation; weather.state is all the server sends). The base tint is
// world-wide; storm/clearing districts also get a local veil + extra rain
// clipped to weather.districts (see drawDistrictStormVeil).
const WEATHER_SKY_ALPHA = { clear: 0, gathering: 0.17, storm: 0.41, clearing: 0.20 };
const WEATHER_SKY_COLOR = "12, 28, 32"; // cooler green-slate storm sky, distinct from night's navy
// Clamp on night-overlay-alpha + weather-sky-alpha together so a winter
// storm at deep night in an unlit district can never compound into an
// unreadable screen.
const MAX_NIGHT_PLUS_WEATHER_ALPHA = 0.68;
function weatherSkyAlpha(weather, nightA) {
  if (!WEATHER_ENABLED || !weather) return 0;
  const raw = WEATHER_SKY_ALPHA[weather.state] || 0;
  return Math.max(0, Math.min(raw, MAX_NIGHT_PLUS_WEATHER_ALPHA - nightA));
}

// Rain/snow particles (WEATHER_ENABLED): deterministic from frameTick + a
// per-particle index hash, same no-retained-state discipline as the shipped
// smoke/dust/wildlife cues. Snow instead of rain when the current season
// (calendar.season) is winter. Particle count is capped by the CURRENT
// VISIBLE viewport area (scroll/zoom-derived, same helper drawWildlife/
// drawShipments use), not world area -- the world is much larger than any
// one screen, especially fully zoomed out.
const WEATHER_STATE_INTENSITY = { clear: 0, gathering: 0.18, storm: 1, clearing: 0.45 };
const WEATHER_PARTICLE_DENSITY_DIVISOR = 11000;
const WEATHER_STORM_DENSITY_DIVISOR = 7200;
const WEATHER_PARTICLE_CAP = 380;
const WEATHER_SHEET_EVERY_N = 6;
const WEATHER_SNOW_FALL_PERIOD = 650;
const WEATHER_RAIN_FALL_PERIOD = 130;
const LIGHTNING_BUCKET_FRAMES = 540; // 540 local frames ≈ ~9s at 60fps — rare full-canvas flash windows
function weatherParticleHash(i) {
  let h = (2166136261 ^ i) >>> 0;
  h = Math.imul(h, 16777619);
  h ^= h >>> 13;
  h = Math.imul(h, 2654435761);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967295;
}
function weatherParticleConfig() {
  return {
    intensityMap: WEATHER_STATE_INTENSITY,
    densityDivisor: WEATHER_PARTICLE_DENSITY_DIVISOR,
    stormDivisor: WEATHER_STORM_DENSITY_DIVISOR,
    cap: WEATHER_PARTICLE_CAP,
    sheetEvery: WEATHER_SHEET_EVERY_N,
    snowFallPeriod: WEATHER_SNOW_FALL_PERIOD,
    rainFallPeriod: WEATHER_RAIN_FALL_PERIOD,
  };
}

function drawWeatherParticleBatch(ctx, frameTick, weather, cal, layerOpts) {
  const cfg = weatherParticleConfig();
  const intensity = cfg.intensityMap[weather.state] || 0;
  if (intensity <= 0) return;
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const w = canvasWrapEl.clientWidth / zoomLevel;
  const h = canvasWrapEl.clientHeight / zoomLevel;
  if (w <= 0 || h <= 0) return;
  const isSnow = !!(cal && cal.season === "winter");
  const densityDiv = intensity >= 1 ? cfg.stormDivisor : cfg.densityDivisor;
  const countMult = layerOpts && layerOpts.countMult != null ? layerOpts.countMult : 1;
  const alphaMult = layerOpts && layerOpts.alphaMult != null ? layerOpts.alphaMult : 1;
  const fallMult = layerOpts && layerOpts.fallMult != null ? layerOpts.fallMult : 1;
  const count = Math.min(cfg.cap, Math.floor((w * h / densityDiv) * intensity * countMult));
  const fallPeriod = (isSnow ? cfg.snowFallPeriod : cfg.rainFallPeriod) * fallMult;
  for (let i = 0; i < count; i++) {
    const hx = weatherParticleHash(i * 2 + 1);
    const hy = weatherParticleHash(i * 2 + 2);
    const x = left + hx * w;
    const yFrac = ((frameTick + hy * fallPeriod) % fallPeriod) / fallPeriod;
    const y = top + yFrac * h;
    const sheet = !isSnow && (i % cfg.sheetEvery === 0);
    drawWeatherParticle(ctx, isSnow ? "snow" : "rain", x, y, i, {
      sheet,
      alphaMult,
      frameTick,
      windHash: weatherParticleHash(i * 3 + 7),
      lenHash: weatherParticleHash(i * 5 + 11),
    });
  }
}

function drawWeatherParticles(ctx, frameTick, weather, cal) {
  if (!WEATHER_ENABLED || !weather) return;
  drawWeatherParticleBatch(ctx, frameTick, weather, cal, { countMult: 0.6, alphaMult: 0.5, fallMult: 0.85 });
  drawWeatherParticleBatch(ctx, frameTick, weather, cal, { countMult: 0.4, alphaMult: 1.0, fallMult: 1.0 });
}

function drawDistrictStormVeil(ctx, weather) {
  if (!WEATHER_ENABLED || !weather) return;
  const state = weather.state;
  if (state !== "storm" && state !== "clearing") return;
  const ids = weather.districts;
  if (!ids || !ids.length) return;
  const alpha = state === "storm" ? 0.24 : 0.14;
  for (const id of ids) {
    const b = getDistrictBounds(id);
    if (!b) continue;
    ctx.fillStyle = `rgba(6, 16, 22, ${alpha.toFixed(3)})`;
    ctx.fillRect(b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
  }
}

// Deterministic rare lightning during storm: full-canvas white/blue flash,
// keyed off local renderFrame (rAF), not sim frameTick — no server signal needed.
function drawLightningFlash(ctx, renderFrame, weather) {
  if (!WEATHER_ENABLED || !weather || weather.state !== "storm") return;
  const bucket = Math.floor(renderFrame / LIGHTNING_BUCKET_FRAMES);
  const trigger = weatherParticleHash(bucket + 0x5f3759df);
  if (trigger < 0.88) return; // ~12% of buckets (~every ~75s at 60fps) may flash
  const localFrame = renderFrame % LIGHTNING_BUCKET_FRAMES;
  const startFrame = Math.floor(weatherParticleHash(bucket + 1) * (LIGHTNING_BUCKET_FRAMES - 20));
  const duration = 8 + Math.floor(weatherParticleHash(bucket + 2) * 11);
  if (localFrame < startFrame || localFrame >= startFrame + duration) return;
  const alpha = 0.10 + weatherParticleHash(bucket + 3) * 0.10;
  const blueish = weatherParticleHash(bucket + 4) > 0.45;
  ctx.fillStyle = blueish
    ? `rgba(190, 215, 255, ${alpha.toFixed(3)})`
    : `rgba(255, 255, 255, ${alpha.toFixed(3)})`;
  ctx.fillRect(0, 0, WORLD_W, WORLD_H);
}

// Light glow (ENV_EFFECTS_ENABLED): warm radial glow over lit structures while
// the night overlay is active, pushing back the dark in lit districts.
function drawLightGlows(cal) {
  if (!ENV_EFFECTS_ENABLED) return;
  const na = nightAlpha(cal);
  if (na <= 0) return;
  const civ = getCiv();
  const litDistricts = civ.litDistricts;
  if (!litDistricts || !litDistricts.length) return;
  const structures = civ.structures || [];
  const strength = na / MAX_NIGHT_ALPHA;
  const prevOp = ctx.globalCompositeOperation;
  ctx.globalCompositeOperation = "lighter";
  const drawGlow = (cx, cy, radius, centerAlpha, midAlpha) => {
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
    grad.addColorStop(0, `rgba(255, 200, 100, ${centerAlpha.toFixed(3)})`);
    grad.addColorStop(0.45, `rgba(255, 190, 90, ${midAlpha.toFixed(3)})`);
    grad.addColorStop(1, "rgba(255, 190, 90, 0)");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fill();
  };
  for (const s of structures) {
    if (!s || s.light !== true) continue;
    if (!litDistricts.includes(s.districtId)) continue;
    const size = getStructureRenderSize(s);
    const cx = s.x + size.width / 2;
    const cy = s.y + size.height / 2;
    drawGlow(cx, cy, LIGHT_GLOW_RADIUS, 0.55 * strength, 0.22 * strength);
    if (na >= 0.4 * MAX_NIGHT_ALPHA) {
      drawGlow(cx, cy, LIGHT_GLOW_HALO_RADIUS, 0.12 * strength, 0.04 * strength);
    }
  }
  ctx.globalCompositeOperation = prevOp;
}

function drawWorld(ctx, frameTick) {
  if (!terrainCanvas) {
    scheduleTerrainCacheBuild();
    ctx.fillStyle = "#3d6b35";
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
    return;
  }
  ctx.drawImage(terrainCanvas, 0, 0, WORLD_W, WORLD_H);
  // Animated ocean foam overlay (the only moving terrain): blit the current phase.
  const foamOffset = Math.floor(frameTick / 8) % 16;
  const ob = oceanFrames.bounds;
  ctx.drawImage(oceanFrames.frames[foamOffset], ob.x1, ob.y1);
  ctx.drawImage(terrainCanvas, DOCK_RECT.x, DOCK_RECT.y, DOCK_RECT.w, DOCK_RECT.h,
                DOCK_RECT.x, DOCK_RECT.y, DOCK_RECT.w, DOCK_RECT.h);
}

// Poll GET /districts.js on a slow interval -- districts/roads are
// mostly-static and only change when district/tile/terrain/road data
// mutates server-side, so this doesn't need /state's ~10Hz cadence.
// Sends ?since=<districtsEpoch>; unchanged polls keep the last payload.
const DISTRICTS_POLL_MS = 3000;
let districtsJsResolvedLogged = false;
async function pollDistricts() {
  try {
    const sinceParam = districtsEpoch > 0 ? `?since=${districtsEpoch}` : "";
    const res = await fetch(`/districts.js${sinceParam}`, { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (data.unchanged) {
      if (data.epoch != null) districtsEpoch = data.epoch;
      return;
    }
    if (!districtsJsResolvedLogged) {
      districtsJsResolvedLogged = true;
      if (VIEWER_LOAD_DEBUG) {
        performance.mark("viewer:districts-js-resolved");
        console.info("[viewer-load] /districts.js resolved", {
          msSinceScriptStart: +(performance.now() - VIEWER_LOAD_T0).toFixed(1),
        });
      }
    }
    const newEpoch = data.epoch;
    const key = JSON.stringify((data.districts || []).map((d) => d.id));
    const epochChanged = newEpoch != null && newEpoch !== districtsEpoch;
    if (key !== districtsKey || epochChanged) {
      districtsKey = key;
      if (newEpoch != null) districtsEpoch = newEpoch;
      districtsData = data;
      terrainCanvas = null; // force rebuild when district list or content revision changes
      terrainBuildScheduled = false;
      scheduleTerrainCacheBuild();
    } else {
      districtsData = data;
      if (newEpoch != null) districtsEpoch = newEpoch;
    }
  } catch (err) { /* keep last known districts; /state polling surfaces connectivity issues */ }
}

function drawResourceDots(ctx, agent, cx, cy) {
  const reg = resourceRegistry();
  const res = agent.resources || {};
  const totalDots = Object.keys(reg).reduce(
    (sum, key) => sum + Math.min(res[key] || 0, 5), 0
  );
  let dotX = cx - Math.max(0, (totalDots * 5 - 2) / 2);
  for (const [key, def] of Object.entries(reg)) {
    const count = Math.min(res[key] || 0, 5);
    for (let i = 0; i < count; i++) {
      ctx.fillStyle = (def && def.color) || "#BDBDBD";
      ctx.fillRect(dotX, cy, 3, 3);
      dotX += 5;
    }
  }
}

function drawSpeechBubble(ctx, agent) {
  if (!agent.message) return;
  const x = agent.x;
  const y = agent.y;
  const text = agent.message.length > 36
    ? agent.message.slice(0, 35) + "…"
    : agent.message;
  ctx.font = "10px monospace";
  const w = Math.min(220, Math.max(48, ctx.measureText(text).width + 14));
  const bx = Math.max(4, Math.min(WORLD_W - w - 4, Math.round(x - w / 2)));
  const by = Math.max(4, y - 94);
  ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
  ctx.fillRect(bx, by, w, 22);
  ctx.strokeStyle = "#111";
  ctx.lineWidth = 1;
  ctx.strokeRect(bx, by, w, 22);
  ctx.fillRect(Math.max(bx + 8, Math.min(bx + w - 14, x - 3)), by + 22, 6, 4);
  ctx.fillStyle = "#222";
  ctx.textAlign = "center";
  ctx.fillText(text, x, by + 15);
}

function drawHealthBar(ctx, agent) {
  if (!SURVIVAL_ENABLED) return;
  if ((agent.health >= 100 || agent.health == null) && !agent.incapacitated) return;
  const w = 24, h = 3;
  const bx = Math.round(agent.x - w / 2);
  const by = Math.round(agent.y - 46);
  ctx.fillStyle = "rgba(0,0,0,0.55)";
  ctx.fillRect(bx - 1, by - 1, w + 2, h + 2);
  const frac = Math.max(0, Math.min(1, (agent.health || 0) / 100));
  ctx.fillStyle = frac > 0.5 ? "#4CAF50" : frac > 0.2 ? "#FFC107" : "#F44336";
  ctx.fillRect(bx, by, Math.round(w * frac), h);
}

function drawAgentLabel(ctx, agent, prefix) {
  const label = `${prefix}·${agent.name}`;
  ctx.font = "10px monospace";
  ctx.textAlign = "center";
  const labelW = Math.ceil(ctx.measureText(label).width + 10);
  const labelX = Math.round(agent.x - labelW / 2);
  const labelY = Math.round(agent.y + 24);
  ctx.fillStyle = "rgba(0, 0, 0, 0.58)";
  ctx.fillRect(labelX, labelY, labelW, 14);
  ctx.fillStyle = agent.color;
  ctx.fillRect(labelX, labelY, 5, 14);
  ctx.fillStyle = "#fff";
  ctx.fillText(label, agent.x + 2, labelY + 10);
}

function drawAgent(ctx, agent, frameTick) {
  if (agent.deceased && agent.buried) {
    // Laid to rest in the cemetery: tombstone sprite + name marker only.
    drawAgentSprite(ctx, agent, frameTick);
    drawAgentLabel(ctx, agent, "†");
    return;
  }
  drawAgentSprite(ctx, agent, frameTick);
  if (agent.deceased && !agent.buried) {
    // Permanent death awaiting burial: body stays where they fell.
    ctx.fillStyle = "rgba(40, 40, 50, 0.65)";
    ctx.fillRect(agent.x - 14, agent.y - 28, 28, 34);
    ctx.fillStyle = "#ccc";
    ctx.font = "11px monospace";
    ctx.textAlign = "center";
    ctx.fillText("†", agent.x, agent.y - 12);
    drawAgentLabel(ctx, agent, "†");
    return;
  }
  if (agent.incapacitated) {
    // Grey out collapsed agents and tag them.
    ctx.fillStyle = "rgba(60, 60, 70, 0.55)";
    ctx.fillRect(agent.x - 18, agent.y - 42, 36, 42);
    ctx.fillStyle = "#fff";
    ctx.font = "12px monospace";
    ctx.textAlign = "center";
    ctx.fillText("☠", agent.x, agent.y - 28);
  }
  drawHealthBar(ctx, agent);
  drawAgentLabel(ctx, agent, agent.role.charAt(0).toUpperCase());
  if (isAgentInventoryVisible(agent)) {
    drawResourceDots(ctx, agent, agent.x, agent.y + 42);
  }
  if (agent.isThinking) {
    ctx.fillStyle = "rgba(0, 0, 0, 0.7)";
    ctx.fillRect(agent.x + 12, agent.y - 62, 18, 12);
    ctx.fillStyle = "#fff";
    ctx.font = "bold 10px monospace";
    ctx.fillText("...", agent.x + 21, agent.y - 53);
  }
  drawSpeechBubble(ctx, agent);
}

function drawStructureWithShadow(structure) {
  const size = getStructureRenderSize(structure, VISUALS_ENABLED);
  ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
  ctx.beginPath();
  ctx.ellipse(structure.x + size.width / 2, structure.y + size.height, size.width * 0.55, 7, 0, 0, Math.PI * 2);
  ctx.fill();
  drawStructure(ctx, structure, VISUALS_ENABLED);
  if (VISUALS_ENABLED && isStructureHitFlashing(structure.id)) {
    drawStructureHitFlash(ctx, structure, size);
  }
  drawStructureLabel(ctx, structure, size);
}

function drawStructureHitFlash(ctx, structure, size) {
  const pulse = 0.55 + 0.45 * Math.sin(renderFrame * 0.35);
  ctx.save();
  ctx.strokeStyle = `rgba(160, 240, 255, ${(0.75 * pulse).toFixed(3)})`;
  ctx.lineWidth = 2;
  ctx.strokeRect(structure.x - 1, structure.y - 1, size.width + 2, size.height + 2);
  ctx.fillStyle = `rgba(255, 255, 255, ${(0.22 * pulse).toFixed(3)})`;
  ctx.fillRect(structure.x, structure.y, size.width, size.height);
  ctx.restore();
}

function drawStructureLabel(ctx, structure, size) {
  const base = structure.name || structure.type;
  const lvl = structure.level != null ? structure.level : null;
  const label = lvl != null && lvl > 0 ? `${base} Lv.${lvl}` : base;
  ctx.font = "9px monospace";
  ctx.textAlign = "center";
  const labelW = Math.ceil(ctx.measureText(label).width + 8);
  const cx = structure.x + size.width / 2;
  const labelX = Math.round(cx - labelW / 2);
  const labelY = structure.y + size.height + 2;
  ctx.fillStyle = "rgba(0, 0, 0, 0.62)";
  ctx.fillRect(labelX, labelY, labelW, 12);
  ctx.fillStyle = "#fff7d6";
  ctx.fillText(label, cx, labelY + 9);
}

