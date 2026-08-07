// =====================================================================
// Minimap (Phase 6, optional polish) -- a small fixed-position canvas showing
// district-bounds rectangles by kind (including any founded after cold
// start), agent dots, and a viewport-outline. Pure renderer: reads only the
// existing GET /state poll (world.agents) + the live district list
// (getDistricts()); no engine changes. More load-bearing than in a
// fixed-size world -- once districts can be founded mid-session, this is the
// only way to notice new territory came online without scrolling to find it.
// =====================================================================
const minimapCanvas = document.getElementById("minimap");
const minimapCtx = minimapCanvas.getContext("2d");
const MINIMAP_KIND_COLORS = {
  farm: "#6eb840", forest: "#245a24", village: "#c4995a", market: "#b8773a",
  beach: "#e5d693", cave: "#454545", ocean: "#2d9ed9", workshop: "#98988a",
};

function renderMinimap() {
  const w = minimapCanvas.width, h = minimapCanvas.height;
  const sx = w / WORLD_W, sy = h / WORLD_H;
  minimapCtx.fillStyle = "#0e0e14";
  minimapCtx.fillRect(0, 0, w, h);

  for (const d of getDistricts()) {
    const b = d.bounds;
    minimapCtx.fillStyle = MINIMAP_KIND_COLORS[d.kind] || "#777";
    minimapCtx.fillRect(
      Math.round(b.x1 * sx), Math.round(b.y1 * sy),
      Math.max(1, Math.round((b.x2 - b.x1) * sx)), Math.max(1, Math.round((b.y2 - b.y1) * sy))
    );
  }

  for (const a of getAgents()) {
    minimapCtx.fillStyle = a.color || "#fff";
    minimapCtx.fillRect(Math.round(a.x * sx) - 1, Math.round(a.y * sy) - 1, 2, 2);
  }

  // Viewport outline: which part of the (now much larger) world canvasWrap's
  // scroll position is currently showing. scrollLeft/Top/clientWidth/Height
  // are in post-zoom CSS pixels, so divide by zoomLevel to get back to
  // logical world coordinates before applying the minimap's own sx/sy scale.
  minimapCtx.strokeStyle = "rgba(255, 255, 255, 0.85)";
  minimapCtx.lineWidth = 1;
  minimapCtx.strokeRect(
    Math.round((canvasWrapEl.scrollLeft / zoomLevel) * sx) + 0.5,
    Math.round((canvasWrapEl.scrollTop / zoomLevel) * sy) + 0.5,
    Math.max(1, Math.round((canvasWrapEl.clientWidth / zoomLevel) * sx)),
    Math.max(1, Math.round((canvasWrapEl.clientHeight / zoomLevel) * sy))
  );
}

// Click or drag anywhere on the minimap to jump the main viewport there.
function minimapToWorld(clientX, clientY) {
  const rect = minimapCanvas.getBoundingClientRect();
  const sx = WORLD_W / minimapCanvas.width, sy = WORLD_H / minimapCanvas.height;
  const mx = ((clientX - rect.left) / rect.width) * minimapCanvas.width;
  const my = ((clientY - rect.top) / rect.height) * minimapCanvas.height;
  return { x: mx * sx, y: my * sy };
}

function centerViewportOn(worldX, worldY) {
  canvasWrapEl.scrollLeft = worldX * zoomLevel - canvasWrapEl.clientWidth / 2;
  canvasWrapEl.scrollTop = worldY * zoomLevel - canvasWrapEl.clientHeight / 2;
}

let minimapDragging = false;

function navigateFromMinimapEvent(event) {
  const { x, y } = minimapToWorld(event.clientX, event.clientY);
  centerViewportOn(x, y);
}

minimapCanvas.addEventListener("mousedown", (event) => {
  minimapDragging = true;
  navigateFromMinimapEvent(event);
  event.preventDefault();
});
window.addEventListener("mousemove", (event) => {
  if (minimapDragging) navigateFromMinimapEvent(event);
});
// Listen on window (not just the small minimap) so dragging keeps working
// even if the cursor briefly overshoots the minimap's bounds.
window.addEventListener("mouseup", () => { minimapDragging = false; });

