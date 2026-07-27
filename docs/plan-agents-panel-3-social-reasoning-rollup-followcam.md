# Plan — Agents Panel 3: Social graph, reasoning peek, roll-up header & follow-cam

**Status:** Planned
**Depends on:** Plan 1 (vitals & crisis) and Plan 2 (inventory & history) shipped —
this plan reuses Plan 2's `#agentDetail` panel and Plan 1's severity data.
**Scope:** Client (`index.html`) + one engine snapshot change (`sim_engine.py`) +
spec update (new snapshot fields are a data-shape change).
**Enhancements covered:** C1 (relationships/social graph), C2 (reasoning peek),
C4 (role/status roll-up header), C5 (follow-cam & jump-to). Also absorbs the
inline "last thought" swap (replacing the per-agent beliefs line) that was
briefly split out as a standalone "Plan 4" and then dropped — see step 4b for why
the paired "feelings" idea was cut.

## Goal

Turn the Agents panel from a per-agent status list into a lens on the *society*:
who is allied with whom, why an agent decided what it did, a one-line village
health summary, and a way to physically find a villager on the map.

## Non-technical summary

- **Social graph (C1):** clicking a villager shows who they treat as allies vs.
  rivals.
- **Reasoning peek (C2):** see the brain's own justification for a villager's
  last decision — the black box becomes inspectable.
- **Roll-up header (C4):** a compact strip above the list — counts by job, how
  many are thinking / gathering / in crisis, average village hunger.
- **Follow-cam (C5):** clicking a villager centers the map on them and can lock
  the camera to follow them around.

## Data availability — IMPORTANT

The engine already tracks the underlying data, but **two fields are not yet in
the `/state` snapshot** and must be added:

- `agent["relationships"]` — dict of `name → "ally" | "neutral" | "rival"`
  (initialized [sim_engine.py](../simulation/sim_engine.py) ~line 1699; consumed
  by the priced-trade path ~line 3458). **Not in the snapshot** built at
  [sim_engine.py](../simulation/sim_engine.py) ~lines 12977–12998.
- `agent["lastReasoning"]` — the model's justification for its last decision
  (initialized ~line 1708). **Not in the snapshot.**

Everything C4 needs (`role`, `isThinking`, `lastAction`, `hunger`, plus Plan 1's
severity) and everything C5 needs (`x`, `y`) is already exposed.

## Steps

### Phase A — Engine: expose the two fields (server-authoritative)

1. In the agent snapshot dict ([sim_engine.py](../simulation/sim_engine.py)
   ~line 12977), add:
   - `"relationships": dict(a["relationships"])` (or a filtered non-neutral view
     to keep payloads small).
   - `"lastReasoning": a.get("lastReasoning")`.
   Gate behind the relevant feature flags if these systems are flag-controlled
   (verify against the flag index before wiring).
2. **Spec update (same change):** record the two new snapshot fields in the
   owning spec — snapshot shape is tracked per CLAUDE.md's SDD rule. Confirm the
   owning file via [specs/00-overview.md](../specs/00-overview.md) (cognition /
   society specs are the likely owners).

### Phase B — C4 roll-up header (no new data, do first for quick win)

3. Add a `#agentRollup` strip above the list. In `renderAgentPanel`
   ([index.html](../simulation/index.html) ~line 1648), compute from `living`:
   counts by role, count thinking, count in crisis (reuse Plan 1's severity),
   and average hunger. Render as compact chips. Fold the derived counts into
   `lastAgentPanelKey` so it only re-renders on change.

### Phase C — C1 social graph + C2 reasoning peek (extend Plan 2's detail panel)

4. In `#agentDetail`, when an agent is selected, add:
   - **Relationships block:** allies and rivals from `agent.relationships`,
     rendered as name chips colored by tie (skip `neutral` to cut noise, matching
     the belief-panel "hide universal" pattern at index.html ~line 1676).
   - **Reasoning block:** show `agent.lastReasoning` (fallback: "no reasoning
     recorded"). Only meaningful when populated — show a `thinking…` placeholder
     while `isThinking`.

4b. **Inline "last thought" on every row (absorbs former Plan 4).** Since
   Phase A already exposes `lastReasoning`, also render a truncated last-thought
   line under each villager in the main list, *replacing* the current per-agent
   beliefs line ([index.html](../simulation/index.html) ~lines 1672–1686 — delete
   the `universalBeliefs` filtering + `beliefHtml` block, and re-key
   `lastAgentPanelKey` at ~line 1655 on `lastReasoning` instead of `beliefs`).
   This gives always-visible reasoning per row, not just on click. Remove the
   now-dead `universalBeliefs` helper (~line 1631) and `.agent-beliefs` CSS
   (~line 261) if nothing else uses them.
   - **Do NOT add a "feeling" line.** The decision `feeling` field
     ([server.py](../simulation/server.py) ~line 961) is optional and, on the live
     sim, is currently emitted **~0% of the time** (0/30 recent conversation
     events carried a feeling) — routine turns run with reasoning effort off, so
     the model never populates it. Displaying it today would render an empty
     column. Generating feelings reliably is a separate prompt/schema change and
     is out of scope here; revisit only after that lands. (This is why the former
     standalone "feelings & last thought" plan was dropped.)

### Phase D — C5 follow-cam & jump-to

5. On row/detail select, pan the canvas to center the agent's `x`/`y`. Add a
   "Follow" toggle in `#agentDetail`; while active, re-center each render tick on
   the selected agent. Verify against the existing camera/viewport transform
   (`clientToWorld` / `WORLD_W`/`WORLD_H` at index.html ~line 1338) — reuse it
   rather than introducing a second coordinate mapping. Clear follow on deselect
   or when the agent dies.

## Suggested order

Phase B (C4) → Phase A (unlocks C1/C2) → Phase C (C1, C2) → Phase D (C5). B is
pure client work with immediate payoff; A is the only backend touch and gates C.

## Verify

- Roll-up counts match the list (jobs, thinking, crisis, avg hunger).
- Snapshot exposes `relationships` + `lastReasoning` (check `GET /state`).
- Detail panel shows correct allies/rivals and the last reasoning string.
- The per-agent beliefs line is gone; each living row shows a truncated last
  thought that updates as the villager thinks (step 4b). No empty "feeling" line.
- Selecting a villager centers the map on them; Follow keeps them centered as
  they walk; deselect / death releases the camera.

## Risk

Medium. C4/C5 are client-only and low risk. C1/C2 touch the server-authoritative
snapshot — keep payload growth bounded (filter neutral ties, cap reasoning
length) and update the owning spec in the same change to preserve the
specs-match-repo invariant.
