# Phase 3 — Goods in Motion (caravans & boats)

**Parent:** [plan-living-ecosystem-0-master.md](plan-living-ecosystem-0-master.md)
**Status:** Planned — not executing.
**Solutions covered:** #4 (trade caravans / boats as visible movement).
**Cost:** Medium.
**Touches:** `simulation/sim_engine.py`, `simulation/index.html`,
`simulation/sprites.js`, `specs/05` or `specs/08`, `specs/11`.

## Problem

Trade and transfers are **instantaneous and invisible**. When `trade_resource`
settles or the market moves goods, counters change on both sides with nothing
crossing the map. The village has a full road graph (`roadNodes`/`roadEdges`,
served via `/districts.js`, already used for agent waypoint pathing) and it is
used only by agents walking — never by goods.

Two existing footholds:
- **`physicalProps`** (sim_engine.py:13259 → index.html:3108) already renders
  boats as decoration derived from `stockpile.boat`, at three fixed moorings, with
  the explicit comment *"boats remain resources, not entities."* Static, never moves.
- **An ocean caravan concept already exists**: `_has_ocean_transit`
  (sim_engine.py:5006), `_consume_ocean_transit` (5009), and the activity line
  *"Ocean caravan waits for transit supplies"* (5016). The *idea* of a caravan is
  in the engine; it just has no position or movement.

Also relevant: `CART_CARRY_BONUS`/`WAGON_CARRY_BONUS`+`WAGON_SPEED_MULT`
([specs/08](../specs/08-systems-economy.md)) are pure query-time carry/speed
multipliers on the holder — there is no cart or wagon object in the world.

## Deliverables

### 3A — Shipment records (`CARAVAN_VISUALS_ENABLED`)
Give a completed transfer a short-lived, **purely cosmetic** in-flight record so
the viewer has something to draw:
`{id, fromDistrict, toDistrict, resource, startFrame, endFrame, mode}` where
`mode` is `cart` (land) or `boat` (ocean), exposed read-only in `/state`.

**Hard design constraint — the shipment must NOT gate the economy.** The resource
transfer stays instantaneous and authoritative exactly as today; the shipment is
an *animation receipt* emitted after the fact. Making delivery depend on travel
time would change trade, starvation timing, and build completion — a far larger
behavioral change than this phase is scoped for, and a real risk of destabilizing
the survival loop.

- Keep a small bounded ring (a handful of live shipments, aged out by `endFrame`).
- Emit on the existing transfer paths (`trade_resource`, market settlement,
  stockpile transfers) — no new tick.
- Choose `mode` from the route: `boat` when the route crosses ocean/uses ocean
  transit (reuse `_has_ocean_transit`), else `cart`.

### 3B — Viewer: goods travelling the road graph
- Interpolate position along the **existing road path** between the two districts'
  `entryNode`s, using `(frameTick - startFrame) / (endFrame - startFrame)`.
  Reuse the same road-resolution helper agent movement already uses rather than
  duplicating pathing.
- Draw a small cart sprite (land) or reuse/adapt the existing boat art (ocean),
  optionally with a tiny resource-colored dot indicating cargo — the
  `drawResourceDots` colour registry (index.html:1332) already maps resource →
  colour, so reuse it.
- Viewport-cull and cap concurrent draws, per the shipped social-tie pattern.
- **Gate:** flag off → nothing drawn; `physicalProps` moored boats keep working
  exactly as today (do not regress that path).

### 3C — Stretch: make carts/wagons visible when held
`CART_CARRY_BONUS`/`WAGON_*` holders currently look identical to everyone else.
A small cart pixel beside an agent holding one would make the tool tier legible.
Cheap and additive — do it only if 3A/3B land cleanly.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | Bounded `shipments` list + emit on existing transfer paths; read-only `/state` projection; `CARAVAN_VISUALS_ENABLED` flag echoed in `config.flags`; age-out on an existing tick (no new timer). |
| `sprites.js` | Cart sprite; reuse/adapt boat art for moving vessels. |
| `index.html` | Per-frame shipment interpolation along road paths, viewport-culled; reuse the resource colour registry for cargo dots; gate on the flag. |
| `specs/08-systems-economy.md` | Document shipments as cosmetic receipts that do **not** gate transfer, plus the flag. |
| `specs/11-viewer.md` | Document the shipment render pass. |
| `specs/01-architecture.md` | Flag index +1. |

No new agent actions → **action-sync invariant N/A**.

## Risks / notes
- **Biggest risk is scope creep into "real logistics."** Resist it. Once shipments
  gate delivery, every downstream system (hunger, build completion, market pricing)
  inherits latency. Keep this cosmetic; revisit only as its own deliberate project.
- **Persistence:** shipments are ephemeral. Prefer *not* persisting them to
  `state.db` — on restore, in-flight shipments simply vanish, which is harmless.
  If they are persisted, they must be restore-safe and aged out on load.
- **Route resolution can fail** (no road path, or a district with no `entryNode`).
  Fall back to skipping the visual, never to a straight line through terrain, and
  never raise into the tick loop.

## Verification
- Trigger a land trade between two districts → confirm a cart appears, travels the
  road, and disappears at arrival; confirm the resource counters moved
  **immediately** (proving the economy was not gated).
- Trigger an ocean-transit trade → confirm boat mode.
- Confirm moored `physicalProps` boats still render (no regression).
- Toggle the flag off → clean no-op.
- Restore from `state.db` mid-shipment → no crash, no orphaned visual.
- Deterministic smokes + single-instance server check last.
