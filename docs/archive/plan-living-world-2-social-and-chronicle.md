# Phase 2 — Social & Historical Layer

**Parent:** [plan-living-world-0-master.md](plan-living-world-0-master.md)
**Status:** Implemented — completed 2026-07-27.
**Payoff:** High — turns invisible social/historical state into something you can
watch unfold on the map and in a narrative feed.
**Touches:** `simulation/index.html`, `simulation/sim_engine.py` (read-only
snapshot), `specs/09`, `specs/11`.

## Problem (confirmed by audit)

- `relationships` exist per agent but are rendered **only as text in the agent
  side panel** (index.html:1918). Nothing on the map shows who is bonded or in
  conflict.
- An Activity feed panel exists (index.html:831) but it is a **raw event log**,
  not a curated chronicle of the village's meaningful history (deaths,
  elections, founded beliefs, first-of-a-kind builds, disasters).

## Deliverables

### 2A — On-canvas social layer (`SOCIAL_LAYER_ENABLED`)
- When two living agents are within a proximity radius **and** share a
  relationship entry, draw a thin connective line/aura between them, colored by
  tie valence (warm = friendly/bond, cool = rivalry/conflict) using the existing
  `relationships[name]` tie data.
- Optional small glyph over an agent reflecting dominant relationship state or
  mood, distinct from the existing speech bubble (which is transient/message-based).
- **Snapshot field (read-only):** if per-pair valence isn't already cheaply
  derivable client-side, add a compact `socialTies` array (pairs + valence) to
  `/state`, derived from data the engine already holds. Keeps viewer thin.
- **Gate:** `SOCIAL_LAYER_ENABLED`. Proximity math runs only over living agents
  in the current viewport; capped per frame.

### 2B — Village chronicle (`CHRONICLE_ENABLED`)
- A dedicated, curated narrative feed (separate from the raw Activity log):
  named milestones only — deaths & burials, succession/elections, founded
  beliefs & meme adoption, disasters (ties into Phase 1's now-visible damage),
  first build of a new structure type, new district founded.
- Sourced from data the server already emits to `activity.jsonl` /
  `conversation.jsonl` and the civilization state; the chronicle is a **filtered,
  formatted** view, not a new event system.
- **Snapshot / endpoint:** prefer a small read-only endpoint or a bounded
  `chronicle` array in `/state` (last N milestone events) built server-side so
  the classification of "milestone vs routine" stays authoritative on the server.
- **Gate:** `CHRONICLE_ENABLED`. Renders as a scrollable panel reusing existing
  side-panel styling; no layout regression to the Activity/Agents panels.

## Files & functions

| File | Change |
|---|---|
| `sim_engine.py` | Optional read-only `socialTies` + bounded `chronicle` in `/state` (or a `/chronicle` route). Milestone classification helper. Flags `SOCIAL_LAYER_ENABLED`, `CHRONICLE_ENABLED` echoed in `config.flags`. |
| `index.html` | Canvas social-layer draw in `drawWorld`; new chronicle panel + render/change-detect (mirror the Activity panel's change-detection pattern at index.html:2266). Gate both on `config.flags`. |
| `specs/09-systems-society.md` | Document `socialTies`/`chronicle` surfacing and the milestone classification rules. |
| `specs/11-viewer.md` | Document the social render pass and chronicle panel + flags. |

## Risks / notes
- Chronicle must not duplicate the Activity feed — define the milestone filter
  crisply (a short allowlist of event kinds) so it reads as *history*, not noise.
- Social lines can clutter at high agent density; cap count and fade by distance.
- Depends conceptually on Phase 1 for disasters to be worth chronicling visibly,
  but has no code dependency — can be built independently.

## Verification
- Run server + preview. Confirm social lines appear only between related, nearby
  living agents and match panel tie data. Screenshot a friendly + a rival pair.
- Trigger/observe a milestone (a death or election) → confirm it lands in the
  chronicle and routine gather/talk events do not.
- Toggle each flag off → clean no-op. Single-instance server check last.

**Implementation verification (2026-07-27):** Deterministic syntax, Python
compile, `/state` flag-echo, log-write, and diff checks completed. Browser visual
smoke was attempted but timed out; no screenshots are claimed.
