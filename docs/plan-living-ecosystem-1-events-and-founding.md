# Phase 1 — Events Made Visible (frontier founding + disasters)

**Parent:** [plan-living-ecosystem-0-master.md](plan-living-ecosystem-0-master.md)
**Status:** Planned — not executing.
**Solutions covered:** #5 (district founding as an event) + the disaster→chronicle
one-liner.
**Cost:** Low — mostly extends the chronicle/milestone system shipped in `ab23244`.
**Touches:** `simulation/sim_engine.py`, `simulation/index.html`, `specs/05`,
`specs/08`, `specs/11`.

## Problem

**Disasters are invisible in the UI.** `_maybe_disaster` (sim_engine.py:3851)
already writes a dramatic line — *"DISASTER! A storm tears through the {name} in
{district} -- {dmg} damage"* — but its kind is not in
`CHRONICLE_MILESTONE_KINDS` (sim_engine.py:990-993), so it never reaches the
Chronicle panel that just shipped. It scrolls past in the raw Activity feed and
is lost. Structure wear (Phase 1 of the previous batch) now shows the *result*;
nothing announces the *cause*.

**District founding is silent.** `_maybe_found_district()` creates a whole new
district as the frontier is settled. The only viewer signal is the terrain cache
silently rebuilding (index.html:1362 invalidates on a districts-list change).
Territorial growth — arguably the most significant thing that can happen to this
village — happens without any announcement.

## Deliverables

### 1A — Disasters in the chronicle (the cheapest win in the batch)
- `_maybe_disaster` already calls `_push_activity`. Add a parallel
  `_push_chronicle(..., kind="disaster")` and add `"disaster"` to
  `CHRONICLE_MILESTONE_KINDS`.
- Verify against the existing viewer filter: the client renders
  `entry.kind` with `_`→space substitution, so `disaster` needs no special-casing.
- **No flag needed** — this corrects an omission in a shipped feature rather than
  adding a new one. (Confirm with the user; if they'd rather gate it, fold it
  under `CHRONICLE_ENABLED`, which already gates the panel.)

### 1B — District founding as an announced event (`FOUNDING_EVENTS_ENABLED`)
- `_push_chronicle(f"{name} was founded on the frontier", kind="district_founded")`
  from `_maybe_found_district()`, plus the kind added to the milestone set.
- **Viewer:** a brief, non-modal banner/toast naming the new district. Reuse the
  existing `#councilBanner` pattern (index.html — fixed, centered, auto-dismissing)
  rather than inventing a new overlay.
- **Optional (assess during implementation):** a short camera pan/highlight to the
  new district. Only do this if it can be made non-disruptive — it must never
  fight the existing C5 follow-cam or steal the viewport while the user is
  inspecting something. If in doubt, ship the banner only.

### 1C — Stretch: "under construction" state for a new district
A newly founded district currently appears fully formed. If cheap, mark it
`foundedFrame` and have the viewer tint/hatch it for a short window so the
frontier visibly *becomes* settled. **Defer if it requires touching the static
terrain cache** — see the Phase 2 plan for why that cache is delicate.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | Add `"disaster"` + `"district_founded"` to `CHRONICLE_MILESTONE_KINDS` (line ~990); `_push_chronicle` calls in `_maybe_disaster` (~3851) and `_maybe_found_district`; new `FOUNDING_EVENTS_ENABLED` flag echoed in `config.flags`; optional `foundedFrame` on the district record. |
| `index.html` | Founding banner (reuse `#councilBanner` styling/lifecycle); gate on the new flag. |
| `specs/08-systems-economy.md` | Note disasters now emit a chronicle milestone. |
| `specs/05-world.md` | Document the founding milestone + any `foundedFrame` field. |
| `specs/11-viewer.md` | Document the founding banner and its flag. |
| `specs/01-architecture.md` | Flag index +1 (update the "43 flags" count). |

No new agent actions → **action-sync invariant N/A**.

## Risks / notes
- **Chronicle capacity:** `CHRONICLE_CAP = 20` and the viewer projection is
  already capped. Disasters fire ~once/100 min, so they will not flood the ring —
  but confirm they don't crowd out deaths/elections during a storm-heavy stretch.
  If they do, consider a larger cap rather than dropping the kind.
- **Banner fatigue:** founding is rare, so a banner is proportionate. Do **not**
  add banners for disasters — those are frequent enough that the chronicle entry
  plus the (already shipped) visible structure damage is the right level.

## Verification
- Force a disaster (temporarily raise `DISASTER_PROB`, or invoke `_maybe_disaster`
  directly in a scratch harness) → confirm it appears in `/state.chronicle` **and**
  renders in the Chronicle panel, while routine activity still does not.
- Force a district founding → confirm chronicle entry + banner, and that the
  terrain cache still rebuilds correctly (no blank/stale world).
- Toggle `FOUNDING_EVENTS_ENABLED` off → clean no-op.
- Deterministic smokes: `uv run python scripts/sid_parity_smoke.py`,
  `uv run python scripts/path1_smoke.py`.
- Single-instance server check last (mind the two-process `uv run` pattern).
