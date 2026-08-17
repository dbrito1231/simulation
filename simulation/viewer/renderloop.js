// =====================================================================
// Render loop — pure draw from `world`, decoupled from polling so we stay at
// 60fps even when the network poll is slower (~10 Hz).
// =====================================================================
let renderFrame = 0;

function tick() {
  // Defense-in-depth: an uncaught exception inside a requestAnimationFrame
  // callback silently kills the rAF chain forever (no more frames ever
  // render, with no visible console surface unless devtools happens to be
  // open at that exact moment) while pollState()'s independent setInterval
  // keeps fetching /state in the background. Catching here means a future
  // rendering bug degrades to "one skipped frame + a logged error" instead
  // of "the viewer is frozen until the page is manually reloaded."
  try {
    tickBody();
  } catch (err) {
    console.error("tick() render error (frame skipped, loop continues):", err);
  } finally {
    requestAnimationFrame(tick);
  }
}

function tickBody() {
  renderFrame++;
  ctx.clearRect(0, 0, WORLD_W, WORLD_H);
  drawWorld(ctx, renderFrame);
  drawSocialTies(ctx);
  drawWildlife(ctx, renderFrame);
  drawShipments(ctx, world.frameTick);

  const agents = getAgents();
  // Derive facing/walk direction from last frame's positions (the server snapshot
  // has no targetX/targetY; drawAgentSprite reads them for the walk cycle).
  for (const a of agents) {
    const p = prevPos[a.id != null ? a.id : a.name] || { x: a.x, y: a.y };
    a.targetX = a.x + (a.x - p.x);
    a.targetY = a.y + (a.y - p.y);
    prevPos[a.id != null ? a.id : a.name] = { x: a.x, y: a.y };
  }

  const structures = getCiv().structures || [];
  const drawList = [
    ...structures.map((structure) => ({ type: "structure", y: structure.y + 34, structure })),
    ...agents.map((agent) => ({ type: "agent", y: agent.y, agent }))
  ].sort((a, b) => a.y - b.y);
  for (const item of drawList) {
    if (item.type === "structure") drawStructureWithShadow(item.structure);
    else drawAgent(ctx, item.agent, renderFrame);
  }
  drawDivineSightOverlays(ctx, renderFrame);
  if (VISUALS_ENABLED) {
    // Pure frame-derived accents: no activity state is retained in the viewer.
    for (const structure of structures) drawStructureSmoke(ctx, structure, renderFrame);
    for (const agent of agents) drawActivityDust(ctx, agent, renderFrame);
  }

  // Server-derived decoration only: boats remain resources, not entities.
  for (const prop of (getCiv().physicalProps || [])) {
    if (prop.resource !== "boat") continue;
    const moorings = [[58, 240], [112, 470], [42, 690]];
    for (let i = 0; i < Math.min(prop.count, moorings.length); i++) {
      const [x, y] = moorings[i];
      // Large moored sailboat in the expanded ocean, facing the beach.
      ctx.fillStyle = "rgba(8, 31, 57, 0.28)";
      ctx.fillRect(x - 4, y + 48, 128, 8);
      ctx.fillStyle = "#4A2714";
      ctx.beginPath();
      ctx.moveTo(x, y + 30); ctx.lineTo(x + 118, y + 30);
      ctx.lineTo(x + 94, y + 54); ctx.lineTo(x + 18, y + 54);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#B96D32";
      ctx.fillRect(x + 13, y + 28, 84, 8);
      ctx.fillStyle = "#5A321B";
      ctx.fillRect(x + 57, y - 78, 6, 108);
      ctx.fillStyle = "#F4E2B5";
      ctx.beginPath();
      ctx.moveTo(x + 65, y - 72); ctx.lineTo(x + 108, y + 14); ctx.lineTo(x + 65, y + 14);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#D8C08B";
      ctx.beginPath();
      ctx.moveTo(x + 55, y - 52); ctx.lineTo(x + 22, y + 14); ctx.lineTo(x + 55, y + 14);
      ctx.closePath(); ctx.fill();
      ctx.fillStyle = "#F7C948";
      ctx.fillRect(x + 21, y + 18, 12, 7);
    }
  }

  const na = nightAlpha(world.calendar);
  if (na > 0.35) {
    const prevOp = ctx.globalCompositeOperation;
    ctx.globalCompositeOperation = "saturation";
    ctx.fillStyle = `rgba(128, 128, 128, ${NIGHT_DESAT_ALPHA.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
    ctx.globalCompositeOperation = prevOp;
  }
  if (na > 0) {
    ctx.fillStyle = `rgba(10, 16, 48, ${na.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  }
  // Weather sky tint (WEATHER_ENABLED): same full-canvas overlay stage as
  // the night overlay above, alpha clamped against it (weatherSkyAlpha) so
  // the two combine coherently instead of stacking into an unreadable
  // screen -- see the worst case (winter storm, deep night, unlit
  // district) verified in the Phase 4 report.
  const wa = weatherSkyAlpha(world.weather, na);
  if (wa > 0) {
    ctx.fillStyle = `rgba(${WEATHER_SKY_COLOR}, ${wa.toFixed(3)})`;
    ctx.fillRect(0, 0, WORLD_W, WORLD_H);
  }
  drawLightningFlash(ctx, renderFrame, world.weather);
  drawGoldenHourOverlay(ctx, world.calendar);
  drawLightGlows(world.calendar);
  drawDistrictStormVeil(ctx, world.weather);
  drawWeatherParticles(ctx, world.frameTick, world.weather, world.calendar);

  // C5 follow-cam: re-center every frame on the followed agent while active;
  // release the lock once they're deselected or no longer living.
  if (followAgentId != null) {
    if (selectedAgentId !== followAgentId) {
      followAgentId = null;
    } else {
      const followed = getLivingAgents().find((a) => a.id === followAgentId);
      if (!followed) followAgentId = null;
      else centerCameraOnAgent(followed);
    }
  }

  renderSidebar();
  renderWorldClockHud();
  drawCouncilAssemblyTable((world.civilization || {}).dailyCouncil, renderFrame);
  renderMinimap();
}

