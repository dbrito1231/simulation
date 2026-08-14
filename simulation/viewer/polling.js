// =====================================================================
// State polling (~10 Hz). On failure we keep the last frame and flip the
// status to "disconnected" so the page never goes blank; the sim itself keeps
// running server-side regardless of the browser.
// =====================================================================
const STATE_POLL_MS = 100;
let polling = false;

let STRUCTURE_UPGRADES_ENABLED = true;
// Sovereign God mode Phase 7: the ONLY thing gating the entire Divine Console
// (DOM visibility, fetches, banner) -- mirrors config.flags.GOD_MODE_ENABLED,
// always echoed by /state regardless of the flag's value (see
// specs/01-architecture.md flag index). false until the first /state poll.
let GOD_MODE_ENABLED_FLAG = false;
// Mirrors config.flags.GOD_AUTH_REQUIRED (default false = open Divine Console).
let GOD_AUTH_REQUIRED_FLAG = false;
// Mirrors config.flags.WORLD_WIKI_ENABLED — gates all wiki polling and links.
let WORLD_WIKI_ENABLED_FLAG = true;

function applyFlags(flags) {
  if (!flags) return;
  if ("SURVIVAL_ENABLED" in flags) SURVIVAL_ENABLED = !!flags.SURVIVAL_ENABLED;
  if ("STRUCTURE_UPGRADES_ENABLED" in flags) STRUCTURE_UPGRADES_ENABLED = !!flags.STRUCTURE_UPGRADES_ENABLED;
  if ("CRAFTING_ENABLED" in flags) CRAFTING_ENABLED = !!flags.CRAFTING_ENABLED;
  if ("MEMES_ENABLED" in flags) MEMES_ENABLED = !!flags.MEMES_ENABLED;
  if ("RULES_ENABLED" in flags) RULES_ENABLED = !!flags.RULES_ENABLED;
  if ("MEMORY_ENABLED" in flags) MEMORY_ENABLED = !!flags.MEMORY_ENABLED;
  if ("BENCHMARKS_ENABLED" in flags) BENCHMARKS_ENABLED = !!flags.BENCHMARKS_ENABLED;
  if ("ECOLOGY_ENABLED" in flags) ECOLOGY_ENABLED = !!flags.ECOLOGY_ENABLED;
  if ("PIANO_MODULES" in flags) PIANO_MODULES = !!flags.PIANO_MODULES;
  if ("META_SYSTEM" in flags) META_SYSTEM = !!flags.META_SYSTEM;
  if ("PATH1_ENABLED" in flags) PATH1_ENABLED = !!flags.PATH1_ENABLED;
  if ("STRUCTURE_WEAR_ENABLED" in flags) STRUCTURE_WEAR_ENABLED = !!flags.STRUCTURE_WEAR_ENABLED;
  if ("ACTIVITY_CUES_ENABLED" in flags) ACTIVITY_CUES_ENABLED = !!flags.ACTIVITY_CUES_ENABLED;
  if ("SOCIAL_LAYER_ENABLED" in flags) SOCIAL_LAYER_ENABLED = !!flags.SOCIAL_LAYER_ENABLED;
  if ("CHRONICLE_ENABLED" in flags) CHRONICLE_ENABLED = !!flags.CHRONICLE_ENABLED;
  if ("CHRONICLE_SAGA_ENABLED" in flags) CHRONICLE_SAGA_ENABLED = !!flags.CHRONICLE_SAGA_ENABLED;
  if ("FOUNDING_EVENTS_ENABLED" in flags) FOUNDING_EVENTS_ENABLED = !!flags.FOUNDING_EVENTS_ENABLED;
  if ("ENV_EFFECTS_ENABLED" in flags) ENV_EFFECTS_ENABLED = !!flags.ENV_EFFECTS_ENABLED;
  if ("WORLD_CLOCK_HUD_ENABLED" in flags) WORLD_CLOCK_HUD_ENABLED = !!flags.WORLD_CLOCK_HUD_ENABLED;
  if ("SEASONAL_AGENTS_ENABLED" in flags) SEASONAL_AGENTS_ENABLED = !!flags.SEASONAL_AGENTS_ENABLED;
  if ("CROP_GROWTH_ENABLED" in flags) CROP_GROWTH_ENABLED = !!flags.CROP_GROWTH_ENABLED;
  if ("WILDLIFE_ENABLED" in flags) WILDLIFE_ENABLED = !!flags.WILDLIFE_ENABLED;
  if ("CARAVAN_VISUALS_ENABLED" in flags) CARAVAN_VISUALS_ENABLED = !!flags.CARAVAN_VISUALS_ENABLED;
  if ("WEATHER_ENABLED" in flags) WEATHER_ENABLED = !!flags.WEATHER_ENABLED;
  if ("GOD_MODE_ENABLED" in flags) GOD_MODE_ENABLED_FLAG = !!flags.GOD_MODE_ENABLED;
  if ("GOD_AUTH_REQUIRED" in flags) GOD_AUTH_REQUIRED_FLAG = !!flags.GOD_AUTH_REQUIRED;
  if ("WORLD_WIKI_ENABLED" in flags) WORLD_WIKI_ENABLED_FLAG = !!flags.WORLD_WIKI_ENABLED;
  applyGodDejaVuAvailability();
}

function applyGodDejaVuAvailability() {
  const dejaVuEl = document.getElementById("godDejaVuFieldset");
  if (!dejaVuEl) return;
  const kind = godCapabilities && godCapabilities.kinds && godCapabilities.kinds.deja_vu_replay;
  if (kind) {
    dejaVuEl.disabled = !kind.applyable;
  } else {
    dejaVuEl.disabled = true;
  }
}

// Bounded viewer-only social pass. The engine has already filtered and
// deduplicated `socialTies`; this just culls to visible, nearby living pairs.
const SOCIAL_TIE_RADIUS = 180;
const SOCIAL_TIE_CAP = 36;
function drawSocialTies(ctx) {
  if (!SOCIAL_LAYER_ENABLED) return;
  const ties = world.socialTies || [];
  if (!ties.length) return;
  const agentsById = new Map(getLivingAgents().map((agent) => [agent.id, agent]));
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  let drawn = 0;
  for (const tie of ties) {
    if (drawn >= SOCIAL_TIE_CAP) break;
    const from = agentsById.get(tie.from);
    const to = agentsById.get(tie.to);
    if (!from || !to || from.x < left || from.x > right || from.y < top || from.y > bottom
        || to.x < left || to.x > right || to.y < top || to.y > bottom) continue;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const distance = Math.hypot(dx, dy);
    if (distance > SOCIAL_TIE_RADIUS) continue;
    const alpha = Math.max(0.16, 0.58 * (1 - distance / SOCIAL_TIE_RADIUS));
    ctx.strokeStyle = tie.valence === "rival"
      ? `rgba(105, 183, 234, ${alpha})`
      : `rgba(242, 164, 93, ${alpha})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(from.x, from.y - 14);
    ctx.lineTo(to.x, to.y - 14);
    ctx.stroke();
    drawn++;
  }
}

// Ambient wildlife (WILDLIFE_ENABLED): server-authoritative huntable fauna.
// Positions/kinds come from world.wildlife (alive creatures only); the viewer
// does not spawn or pathfind. Cosmetic bob from frameTick; drawn every tick
// (not baked into terrainCanvas).
function drawWildlife(ctx, frameTick) {
  if (!WILDLIFE_ENABLED) return;
  const fauna = world.wildlife;
  if (!fauna || !fauna.length) return;
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  for (let i = 0; i < fauna.length; i++) {
    const creature = fauna[i];
    const x = creature.x;
    const y = creature.y;
    if (x < left || x > right || y < top || y > bottom) continue;
    const bob = Math.sin((frameTick + i * 17) / 20) * 3;
    drawWildlifeCreature(ctx, creature.kind, x, y + bob, frameTick);
  }
}

// Goods-in-motion (Phase 3 living-ecosystem, CARAVAN_VISUALS_ENABLED). The
// engine emits a shipment AFTER a transfer already completed and moved the
// authoritative resource counts -- this pass never touches simulation
// state, it only interpolates the already-resolved road-graph `path` the
// server embedded (same helper agent travel uses, _road_path_between_
// districts) between startFrame/endFrame. Route resolution failures are
// handled server-side (no shipment emitted at all); the viewer only ever
// sees valid, drawable records. Viewport-culled + capped, same pattern as
// drawSocialTies/drawWildlife.
const SHIPMENT_DRAW_CAP = 8;
function shipmentPosition(shipment, frameTick) {
  const path = shipment.path;
  if (!path || path.length < 2) return null;
  const span = shipment.endFrame - shipment.startFrame;
  const t = span > 0 ? (frameTick - shipment.startFrame) / span : 1;
  const clamped = Math.max(0, Math.min(1, t));
  const segCount = path.length - 1;
  const scaled = clamped * segCount;
  const segIndex = Math.min(segCount - 1, Math.floor(scaled));
  const segT = scaled - segIndex;
  const a = path[segIndex];
  const b = path[segIndex + 1];
  return { x: a.x + (b.x - a.x) * segT, y: a.y + (b.y - a.y) * segT };
}

function drawShipments(ctx, frameTick) {
  if (!CARAVAN_VISUALS_ENABLED) return;
  const shipments = world.shipments || [];
  if (!shipments.length) return;
  const reg = resourceRegistry();
  const left = canvasWrapEl.scrollLeft / zoomLevel;
  const top = canvasWrapEl.scrollTop / zoomLevel;
  const right = left + canvasWrapEl.clientWidth / zoomLevel;
  const bottom = top + canvasWrapEl.clientHeight / zoomLevel;
  let drawn = 0;
  for (const shipment of shipments) {
    if (drawn >= SHIPMENT_DRAW_CAP) break;
    if (frameTick < shipment.startFrame || frameTick > shipment.endFrame) continue;
    const pos = shipmentPosition(shipment, frameTick);
    if (!pos) continue;
    if (pos.x < left || pos.x > right || pos.y < top || pos.y > bottom) continue;
    const cargoColor = (reg[shipment.resource] && reg[shipment.resource].color) || null;
    drawShipment(ctx, shipment.mode, pos.x, pos.y, cargoColor);
    drawn++;
  }
}

async function pollState() {
  if (polling) return;
  polling = true;
  try {
    const url = statePollFull || lastFrameTick <= 0
      ? "/state"
      : `/state?since=${lastFrameTick}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const payload = await res.json();
    if (payload.frameTick != null && payload.frameTick < lastFrameTick && !payload.full) {
      return;
    }
    if (payload.unchanged) {
      if (payload.stateGeneration != null) stateGeneration = payload.stateGeneration;
      return;
    }
    let snapshot;
    if (payload.full || statePollFull || !world || !world.agents) {
      snapshot = payload;
      statePollFull = false;
    } else if (payload.stateGeneration != null && stateGeneration > 0
        && payload.stateGeneration !== stateGeneration) {
      statePollFull = true;
      return;
    } else {
      snapshot = mergeStateDelta(world, payload);
    }
    if (payload.stateGeneration != null) stateGeneration = payload.stateGeneration;
    if (payload.frameTick != null) lastFrameTick = payload.frameTick;
    world = snapshot;
    setSpriteSeason(snapshot.calendar && snapshot.calendar.season);
    // Season changed since the terrain cache was last tinted: rebuild it
    // (same lazy invalidate-and-reschedule mechanism pollDistricts() uses).
    const season = snapshot.calendar && snapshot.calendar.season;
    if (season && season !== lastSeasonRendered && terrainCanvas) {
      terrainCanvas = null;
      terrainBuildScheduled = false;
      scheduleTerrainCacheBuild();
    }
    // Living-ecosystem Phase 2: a district's ecology stage changed since the
    // terrain cache was last built -- rebuild exactly once, same mechanism
    // as the season check above. No-op (both keys stay null) when
    // CROP_GROWTH_ENABLED is off or the server hasn't sent districtEcology.
    if (terrainCanvas && !terrainBuildScheduled) {
      const [, ecologyStageKey] = ecologyStagesForTerrain();
      if (ecologyStageKey !== lastEcologyStageKeyRendered) {
        terrainCanvas = null;
        terrainBuildScheduled = false;
        scheduleTerrainCacheBuild();
      }
    }
    applyFlags(snapshot.config && snapshot.config.flags);
    setSeasonalAgentAccentsEnabled(SEASONAL_AGENTS_ENABLED);
    if (terrainCanvas && !terrainBuildScheduled) {
      const [, ecologyStageKey] = ecologyStagesForTerrain();
      const seasonNow = snapshot.calendar && snapshot.calendar.season;
      const visualKey = terrainVisualCacheKey(seasonNow, ecologyStageKey);
      if (visualKey !== lastTerrainVisualKeyRendered) {
        terrainCanvas = null;
        terrainBuildScheduled = false;
        scheduleTerrainCacheBuild();
      }
    }
    renderDailyCouncil((snapshot.civilization || {}).dailyCouncil);
    syncPauseButton();
    if (terrainCanvas) hideWorldLoading();
  } catch (err) {
    statePollFull = true;
    // Keep last frame; surface a disconnected status (but not over a real
    // server status we already have — only mark disconnected on fetch failure).
    if (world && world.lmStatus !== "disconnected") {
      world = Object.assign({}, world, { lmStatus: "disconnected" });
    }
  } finally {
    polling = false;
  }
}

