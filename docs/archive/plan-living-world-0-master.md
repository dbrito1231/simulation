# Master Plan — "Living World" Viewer Enrichment (three phases)

**Status:** Implemented — all three phases completed (2026-07-27).
**Branch:** TBD (propose `feature/living-world` off `main`).
**Delivery gate:** Nothing merges to `main` without the user's explicit approval.

## Why this plan exists

User feedback: the world "feels dead" — no visible storm/decay damage, and it's
unclear how anything changes over time. Investigation confirmed this is a
**visibility gap, not a missing-systems gap.** The engine already tracks
structure `condition` (decay/disasters), `deceased`/`buried` (mortality),
`relationships` (society), and agent `message`/`lastReasoning`/`lastAction`.

**Design principle:** *surface existing state, don't simulate new state.* No new
tick-loop cost, no new entity types, no new sim systems. This deliberately avoids
the "full living ecosystem" (weather/animals/plant-lifecycle) engineering jump,
which was scoped and rejected as a mission shift against the "not a game"
non-goal ([specs/00-overview.md](../specs/00-overview.md)).

## Audit — what ALREADY EXISTS (do not rebuild)

A code audit of `index.html`/`sprites.js` found many "living" cues are already
implemented. These are **out of scope** except where a child plan explicitly
extends them:

| Feature | Where | Status |
|---|---|---|
| Speech bubbles over agents | `drawSpeechBubble` (index.html:1349), reads `agent.message` | **Done** |
| Day/night lighting overlay + warm light glow | `nightAlpha`, `drawLightGlows` (index.html:1246+), `ENV_EFFECTS_ENABLED` | **Done** |
| Seasonal terrain tint + winter snow caps | `applySeasonTint`, `drawSnowCap` (sprites.js) | **Done** |
| Buried agents render as named tombstones | `tombstoneSprite`, `drawAgentSprite` (sprites.js:1370) | **Done** |
| Belief/meme tint on living agents | `BELIEF_TINTS` (sprites.js:1411) | **Done** |
| Relationships (text) + per-agent history + culture/meme chips | agent side panel (index.html:1690, 1918) | **Done (side panel)** |
| Activity feed panel | `#activityLog`, Activity panel (index.html:831) | **Done (side panel)** |

## Audit — what is GENUINELY MISSING (the real work)

| Gap | Evidence | Phase |
|---|---|---|
| **Structure wear is invisible** — `drawStructure`/`getStructureGrid` never read `condition` or `isRuin`; a fully ruined structure renders identically to a pristine one | sprites.js:719, 784 (no `condition`/`isRuin` branch) | **1** |
| **No activity cues** — no smoke from active forge/kiln, no dust when building | grep: zero `smoke`/`dust`/`particle`/`ember` in viewer | **1** |
| **No on-canvas social layer** — relationships live only as side-panel text, invisible on the map | index.html:1918 (panel only) | **2** |
| **No curated "chronicle"** — raw activity feed exists, but no narrative history of named events/beliefs/deaths | Activity panel is a raw event log, not a chronicle | **2** |
| **Atmosphere is minimal** — day/night + season exist but are subtle; no weather *feel*, no time-of-day/season indicator in the world view | `nightAlpha` is a flat overlay; no UI clock/season badge | **3** |

## The three phases (ordered by payoff-per-effort)

| # | Phase | Child plan | Touches |
|---|-------|-----------|---------|
| 1 | Structural life & activity cues | [plan-living-world-1-structure-wear-and-activity.md](plan-living-world-1-structure-wear-and-activity.md) | `sprites.js`, `index.html`, `sim_engine.py` (read-only snapshot field), spec 08 + 11 |
| 2 | Social & historical layer | [plan-living-world-2-social-and-chronicle.md](plan-living-world-2-social-and-chronicle.md) | `index.html`, `sim_engine.py` (read-only snapshot), spec 09 + 11 |
| 3 | Atmosphere & time-of-day polish | [plan-living-world-3-atmosphere.md](plan-living-world-3-atmosphere.md) | `index.html`, `sprites.js`, spec 11 |

**Phase 1 is the headline** — it directly closes the user's original complaint
(invisible storm/decay damage) and is confirmed 100% absent today.

## Decisions locked (from user, 2026-07-26)

1. **Flag granularity: one flag per item.** e.g. `STRUCTURE_WEAR_ENABLED`,
   `ACTIVITY_CUES_ENABLED`, `SOCIAL_LAYER_ENABLED`, `CHRONICLE_ENABLED`, etc. —
   each echoed to the viewer via `/state` `config.flags` per
   [specs/01](../specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy).
2. **`/state` read-only additions allowed.** The engine may expose
   already-tracked data (e.g. a discretized `conditionTier`, relationship pairs)
   into the snapshot. Keeps the viewer thin; each addition carries a spec edit.
3. **Fidelity: polished pixel-art.** Hand-tuned wear sprite states, real particle
   cues, tuned social glyphs — not flat placeholders. This raises `sprites.js`
   effort and is reflected in each child plan's estimate.
4. **Sequencing: fully plan all three before building.** This master + all three
   child plans are written first; no code until the user approves the set.
5. **Phase 3 (3B) golden-hour tint folds into `ENV_EFFECTS_ENABLED`** — no
   dedicated flag (user, 2026-07-26).
6. **Some already-existing cues may be revisited/polished** (user chose
   "revisit some", 2026-07-26) — specific cues TBD by the user; not yet scoped
   into a phase. Add as a Phase 1.5/side task when the user names which.

## Ownership

**The user is implementing these phases themselves** (decided 2026-07-26). This
session's role is planning only; the orchestrator/`implementer` build pipeline in
the child plans applies only if the user later hands implementation back. The
user will request a review when the work is done.

## Cross-cutting rules (every phase)

- **Thin-viewer contract.** [specs/11-viewer.md](../specs/11-viewer.md) declares the
  viewer thin/stateless. Every phase carries a spec-11 edit documenting the new
  render pass + its flag. Engine changes limited to read-only snapshot fields.
- **Action-sync invariant is N/A** — no new agent actions are added; this is
  rendering + snapshot only.
- **Model policy.** Orchestrator (this session) plans/sequences/reviews; all
  implementation dispatched to `implementer` subagents on Sonnet
  ([CLAUDE.md](../CLAUDE.md)).
- **Evidence bar.** Each phase verified by running the server + browser preview,
  capturing before/after screenshots (esp. a ruined structure for Phase 1). No
  merge without user approval. Single-instance server rule enforced as last step.

## Status log

- 2026-07-26 — Plan drafted. Code audit folded in (many cues already exist; scope
  refocused on genuine gaps). User decisions locked. Awaiting go-ahead to build.
- 2026-07-27 — Phases 1–3 implemented. Deterministic syntax, Python compile,
  `/state` flag-echo, log-write, and diff checks completed. A browser visual
  smoke was attempted but timed out; no screenshots are claimed.
