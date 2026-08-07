"use strict";

// =====================================================================
// Goods-in-motion shipments (Phase 3 living-ecosystem, CARAVAN_VISUALS_
// ENABLED). Purely cosmetic: index.html computes the position by
// interpolating along the road-graph path the engine already embedded in
// the shipment record, and passes it in here. These are stateless draw
// calls only -- no simulation state lives in this file. `cargoColor` comes
// from the same resource -> colour registry drawResourceDots already uses.
// =====================================================================
function drawCart(ctx, x, y, cargoColor) {
  ctx.fillStyle = "#5A321B";
  ctx.fillRect(x - 6, y - 4, 12, 7);
  ctx.fillStyle = "#3E3226";
  ctx.beginPath();
  ctx.arc(x - 4, y + 4, 2.5, 0, Math.PI * 2);
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x + 4, y + 4, 2.5, 0, Math.PI * 2);
  ctx.fill();
  if (cargoColor) {
    ctx.fillStyle = cargoColor;
    ctx.fillRect(x - 3, y - 7, 6, 3);
  }
}

function drawShipmentBoat(ctx, x, y, cargoColor) {
  // Small moving vessel -- a scaled-down echo of the moored physicalProps
  // boat art (index.html), not a copy of the DOM/state wiring around it.
  ctx.fillStyle = "#4A2714";
  ctx.beginPath();
  ctx.moveTo(x - 9, y - 2);
  ctx.lineTo(x + 9, y - 2);
  ctx.lineTo(x + 6, y + 5);
  ctx.lineTo(x - 6, y + 5);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#5A321B";
  ctx.fillRect(x - 1, y - 12, 2, 11);
  ctx.fillStyle = "#F4E2B5";
  ctx.beginPath();
  ctx.moveTo(x + 1, y - 11);
  ctx.lineTo(x + 8, y - 1);
  ctx.lineTo(x + 1, y - 1);
  ctx.closePath();
  ctx.fill();
  if (cargoColor) {
    ctx.fillStyle = cargoColor;
    ctx.fillRect(x - 3, y, 4, 3);
  }
}

function drawShipment(ctx, mode, x, y, cargoColor) {
  if (mode === "boat") drawShipmentBoat(ctx, x, y, cargoColor);
  else drawCart(ctx, x, y, cargoColor);
}
