# Plan — Agents Panel 1: Readable vitals & crisis surfacing

**Status:** Planned
**Scope:** Client-only (`simulation/index.html`). No server, engine, or spec changes.
**Enhancements covered:** #1 (visual vitals bars) + #2 (surface agents in crisis).

## Goal

Make the health of the whole village legible at a glance, and make villagers in
trouble impossible to miss. Today each living row shows `❤100 🍗67` as bare
numbers ([index.html `agentVitalsHtml`](../simulation/index.html)), and all
living agents render in roster order regardless of who is dying.

## Non-technical summary

Replace the plain health/hunger numbers with little colored bars (green → yellow
→ red), and automatically float any villager who is collapsed or starving to the
top of the list with a warning glow. You spot a crisis in one glance instead of
reading every row.

## Data available

All fields already ship in `/state`: `health`, `hunger`, `incapacitated`,
`deceased`, `buried`. Nothing new is needed from the backend.

## Steps

1. **Vitals bars.** Rewrite `agentVitalsHtml` (index.html ~line 1620) to emit two
   thin meter bars instead of text: a track `<div>` with an inner fill whose
   `width: {value}%` and color come from thresholds — health ≥60 green, 30–59
   amber, <30 red; hunger inverted (high hunger value = hungrier = worse, so
   confirm polarity against the engine before wiring colors). Keep the existing
   `collapsed` / `† buried` / `† unburied` text states unchanged.
2. **CSS.** Add `.vital-bar` / `.vital-fill` rules next to the existing
   `#agentList .agent-vitals` block (index.html ~line 259). ~15 lines.
3. **Crisis sort.** In `renderAgentPanel` (index.html ~line 1652), sort the
   `living` array by a severity key before mapping to rows: `incapacitated`
   first, then ascending health / worsening hunger, then everyone else in
   current roster order (stable).
4. **Crisis highlight.** Add an `agent-critical` CSS class (warning border /
   subtle pulse) to rows below a danger threshold. Include the computed severity
   in `lastAgentPanelKey` (index.html ~line 1655) so the panel re-renders when an
   agent crosses a threshold.
5. **Respect the flag.** Gate everything behind the existing `SURVIVAL_ENABLED`
   check already used in `agentVitalsHtml`, so nothing shows when survival is off.

## Verify

Run the server (`uv run python simulation/server.py`, open
`http://127.0.0.1:5001`). Confirm: bars track the numbers; low-health/collapsed
agents jump to the top and glow; dead/buried rows still read correctly; the whole
treatment disappears when survival is disabled.

## Risk

Very low — cosmetic, single file, easily reverted.
