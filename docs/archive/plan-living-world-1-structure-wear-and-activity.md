# Phase 1 — Structural Life & Activity Cues

**Parent:** [plan-living-world-0-master.md](plan-living-world-0-master.md)
**Status:** Implemented — completed 2026-07-27.
**Payoff:** Highest — directly closes the user's original complaint that
storm/decay damage is invisible. Confirmed 100% absent today.
**Touches:** `simulation/sprites.js`, `simulation/index.html`,
`simulation/sim_engine.py` (read-only snapshot field), `specs/08`, `specs/11`.

## Problem (confirmed by audit)

- `drawStructure` (sprites.js:784) and `getStructureGrid` (sprites.js:719) never
  read `condition` or `isRuin`. A structure at `condition: 0, isRuin: true`
  renders **identically** to a pristine one. Decay (`STRUCTURE_DECAY_PER_GOODS_TICK`)
  and disasters (`DISASTER_PROB`, `DISASTER_DAMAGE = (40,70)`,
  [specs/08](../specs/08-systems-economy.md#L192)) are therefore invisible.
- No activity cues exist: nothing marks a forge/kiln as actively working or an
  agent as actively building (grep: zero `smoke`/`dust`/`particle`/`ember`).

## Deliverables

### 1A — Structure wear rendering (`STRUCTURE_WEAR_ENABLED`)
Four visual tiers driven by the existing `condition` (0–100) plus `isRuin`:

| Tier | Condition | Treatment |
|---|---|---|
| pristine | ≥ `STRUCTURE_DISREPAIR_THRESHOLD` (30) and high | full sprite, no overlay |
| worn | ~30–60 | subtle desaturation + a few "missing pixel" gaps along edges |
| crumbling | < disrepair threshold (stops "working" per spec 08) | cracks overlay, darker, cracked-corner pixels |
| ruin | `isRuin` / condition 0 | dedicated collapsed/rubble silhouette + optional overgrowth pixels |

- **Snapshot field (read-only):** add `conditionTier` (enum:
  `pristine`/`worn`/`crumbling`/`ruin`) to each structure in the `/state`
  builder in `sim_engine.py`, derived from the values the engine already holds.
  Rationale: keeps the tier thresholds authoritative on the server (single
  source of truth) and the viewer thin — it just maps tier → overlay. The raw
  `condition` float is already in the snapshot; the tier is a convenience derive.
- **sprites.js:** `drawStructure` gains a wear pass. Ruin gets a real rubble
  sprite (polished pixel-art per locked decision), not just a tint. Overlays are
  drawn deterministically (seeded by `structure.id`) so a given structure's
  cracks don't shimmer frame to frame.
- **Gate:** all of the above no-ops when `STRUCTURE_WEAR_ENABLED` is false
  (flag echoed via `config.flags`); older snapshots lacking `conditionTier`
  fall back to `condition` thresholds client-side, then to pristine.

### 1B — Activity cues (`ACTIVITY_CUES_ENABLED`)
Cosmetic, deterministic, cheap. Keyed off data already present:

- **Smoke** rising from structures whose `type` is a heat/craft type
  (forge, kiln, furnace, blast_furnace, charcoal_pit/kiln, brick_kiln, smelter…)
  **and** that are working (`condition ≥ disrepair threshold`). A small animated
  puff cycle driven by `frameTick` (same pattern as ocean foam / walk frames).
- **Dust** puffs under an agent whose `lastAction` is `build_structure` /
  `contribute_resources` / `start_project`, shown briefly.
- Optional stretch: faint **splash** near docks/piers when a `fisher` agent with
  `lastAction == collect_resource` stands on a beach tile.
- **Gate:** `ACTIVITY_CUES_ENABLED`. All effects are pure canvas draws in the
  per-frame `drawWorld` path; no new state retained between frames beyond the
  existing `frameTick`.

## Files & functions

| File | Change |
|---|---|
| `sim_engine.py` | `/state` structure serialization: add `conditionTier` derive (read-only). Add module flags `STRUCTURE_WEAR_ENABLED`, `ACTIVITY_CUES_ENABLED` (default True) to the flag block; ensure both are echoed in `config.flags`. |
| `sprites.js` | `drawStructure`: wear overlays + ruin rubble sprite; new `RUIN_GRIDS`/overlay helpers. New smoke/dust particle helpers. |
| `index.html` | Call activity-cue draws in `drawWorld`; read `config.flags` to gate both features; client-side fallback when `conditionTier` absent. |
| `specs/08-systems-economy.md` | Document that `conditionTier` is now surfaced in `/state`; note the viewer renders wear tiers. |
| `specs/11-viewer.md` | Document the wear render pass, ruin sprite, activity-cue passes, and the two flags. |

## Risks / notes
- Ruin rubble sprites are the largest single art task (polished tier). Budget a
  focused `implementer` pass on `sprites.js` alone for the sprite grids.
- Smoke must be scoped to *working* heat structures, or every ruined furnace
  would smoke — tie it to the same disrepair threshold as 1A.

## Verification
- Run server + preview. Use an admin control (or a soak) to drive a structure to
  ruin, screenshot pristine → worn → crumbling → ruin transitions.
- Confirm smoke appears only on working heat structures and stops when one
  decays below threshold.
- Toggle each flag off → confirm clean no-op (looks like today).
- Single-instance server check as the final step.

**Implementation verification (2026-07-27):** Deterministic syntax, Python
compile, `/state` flag-echo, log-write, and diff checks completed. Browser visual
smoke was attempted but timed out; no screenshots are claimed.
