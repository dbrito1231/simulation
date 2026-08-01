# Scoping: Huntable Wildlife + More Creature Kinds

Context for implementing huntable wildlife and additional ambient creature
kinds. Written after inspecting the current ambient-wildlife implementation;
not yet planned or built.

## Current state

The fish/bird/grazer sprites visible in the viewer (`WILDLIFE_ENABLED`,
living-ecosystem Phase 2) are **decoration only, with zero server-side
representation**:

- Draw functions live in `simulation/sprites.js` (`drawFishRipple`,
  `drawBird`, `drawGrazer`, dispatched via `drawWildlifeCreature`).
- Positions are **not** simulation state. They're computed client-side in
  `index.html`'s `tickBody()` via `drawWildlife(ctx, renderFrame)`, seeded
  deterministically per district by `wildlifeHashSeed(districtId)` (an
  FNV-1a-style hash feeding a simple integer PRNG) — see
  [specs/11-viewer.md](../specs/11-viewer.md#ambient-wildlife-wildlife_enabled-living-ecosystem-phase-2),
  lines ~406-439.
- Density scales with district ecology stage:
  `WILDLIFE_STAGE_COUNT = {barren: 0, sparse: 1, healthy: 2, lush: 4}`,
  capped at `WILDLIFE_CAP_PER_DISTRICT = 4`.
- Creature kind is fixed per district kind:
  `WILDLIFE_KIND_BY_DISTRICT_KIND = {forest: "bird", farm: "grazer", beach: "fish"}`.
  Fish are placed only within ~70px of the beach/ocean shore edge.
- The spec is explicit about the boundary (lines ~435-439):

  > **Limitation (intentional):** wildlife is decoration only. They cannot be
  > hunted, gathered, or interacted with in any way, and do not feed the food
  > supply or any other resource. Making them huntable would require a new
  > agent action and a new resource path — explicitly out of scope for this
  > phase.

- Note: `sim_engine.py` has an unrelated function also named `_tick_wildlife()`
  (around line 5956). That's a *predator-attacks-a-lone-forest-agent* pressure
  event gated by `PRESSURE_LOOP_ENABLED` and `WILDLIFE_EVENT_PROB = 0.02` — it
  has nothing to do with the fish/bird/grazer sprites and should not be
  confused with them or reused for this feature.

## What huntability actually requires

This is not "add a hunt action to an existing entity." It's inventing a
server-side wildlife entity system from nothing, since none exists today.

1. **New server-side state.** The engine needs to track individual creatures
   (position, alive/dead, district, respawn timer) instead of deriving
   positions from a client-side hash. Position logic needs to move from
   `index.html`/`sprites.js` into `simulation/sim_engine.py` so client and
   server agree on what's actually being hunted (server-authoritative
   invariant — see [specs/02-engine-core.md](../specs/02-engine-core.md)).

2. **New agent action**, e.g. `hunt_wildlife`, added in lockstep across the
   action-sync invariant (CLAUDE.md, [specs/01-architecture.md](../specs/01-architecture.md#action-sync-invariant)):
   - `DECISION_ACTIONS` / `DECISION_SCHEMA` / `SYSTEM_PROMPT` in `server.py`
   - `apply_decision()` + payload `available_actions` in `sim_engine.py`
   - `ACTION_LABELS` in `viewer.js` (display only)

3. **New resource path.** A "meat"/"game" good that feeds the existing
   food/goods economy (survival, `contribute`, trade), threaded through
   wherever `districtStocks`/goods already flow — see
   [specs/08-systems-economy.md](../specs/08-systems-economy.md).

4. **Depletion/respawn balancing** so hunting doesn't strip a district bare.
   Likely modeled on the existing `ECOLOGY_REGROW_FRAMES` regrowth pattern
   already used for crops.

5. **More creature kinds** (visual variety) — comparatively cheap:
   - Extend `WILDLIFE_KIND_BY_DISTRICT_KIND` with new kind→district mappings.
   - Add a `draw<Kind>()` function in `sprites.js` per new kind, dispatched
     from `drawWildlifeCreature`.
   - However, each *huntable* new kind still needs its own yield/resource
     mapping once items 1-3 above exist — visual-only kinds are cheap, but
     huntable kinds inherit the full cost of the subsystem.

6. **Spec updates required in the same change** (SDD: specs first, code
   second, per CLAUDE.md):
   - [specs/11-viewer.md](../specs/11-viewer.md) — wildlife is no longer
     client-derived decoration; rewrite the "Limitation (intentional)"
     section and the position/seeding description.
   - [specs/07-actions.md](../specs/07-actions.md) — new `hunt_wildlife`
     action.
   - [specs/08-systems-economy.md](../specs/08-systems-economy.md) — new
     resource/good and its flow into existing goods systems.
   - [specs/02-engine-core.md](../specs/02-engine-core.md) — new tick
     function(s) for spawn/respawn/state ownership under the engine lock.

## Sizing

Item 5 alone (more creature kinds, decorative only) is a small, self-contained
addition. Items 1-4 (huntability) are a genuinely new subsystem, comparable in
size to the existing living-ecosystem batch (see
`docs/archive/` for that batch's plan docs if useful as a template), not a
quick patch.
