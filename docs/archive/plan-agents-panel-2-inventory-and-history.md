# Plan — Agents Panel 2: Per-agent inventory & activity history

**Status:** Planned
**Scope:** Client detail panel (inventory) + optional small server endpoint (history).
**Enhancements covered:** #3 (per-agent inventory) + #5 (per-agent activity history).

## Goal

When you click a villager, understand *what they are carrying* and *what they
have been doing* — not just their single current action. Today selecting a row
only highlights it and floats the inventory on the canvas
([index.html `isAgentInventoryVisible`](../simulation/index.html) ~line 1361);
there is no sidebar detail area.

## Non-technical summary

Clicking a villager opens a small detail area showing the resources they hold
(wood, food, stone…) and a short timeline of their recent actions
("gathered wood → walked to workshop → contributed wood"). This reveals whether
someone is making progress or stuck in a loop.

## Data available

- `agent.resources` already ships in `/state` (already summed for village totals
  at index.html ~line 1427).
- Action history: the engine writes `activity.jsonl` per session
  ([sim_engine.py `activity_path`](../simulation/sim_engine.py) ~line 369). Not
  currently exposed over HTTP.

## Steps

1. **Detail container.** Add a `#agentDetail` panel under the agent list in the
   sidebar. Populate it in `renderAgentPanel` for `selectedAgentId`; clear it
   when nothing is selected.
2. **Inventory chips.** Render `agent.resources` as the same `res-chip` swatches
   the Civilization panel already uses (index.html ~line 1445). This also gives a
   readable replacement for the canvas-only inventory float.
3. **History — pick a source:**
   - **B1 (lightweight, client-only, recommended first):** keep a rolling
     per-agent buffer client-side. Each poll, if an agent's `lastAction` changed,
     push it onto a capped list (last ~8). No backend; resets on page reload.
   - **B2 (durable, server-backed):** add `GET /agent-history?id=&limit=` in
     server.py that tails the session's `activity.jsonl` filtered to that agent.
     Survives reloads; costs one endpoint + a spec update (routes are tracked per
     CLAUDE.md SDD rule).
4. **Render timeline.** Show the recent-actions list in `#agentDetail`, newest
   first, reusing `humanizeAction`-style labels (index.html ~line 1496).
5. **Spec.** B2 only: add the new route to its owning spec in the same change.
   B1 needs no spec change.

## Recommendation

Ship **B1** first (zero backend, instant value). Upgrade to **B2** only if
durable, reload-surviving history proves worth the endpoint + spec cost.

## Verify

Select an agent: inventory chips match what they carry; the timeline grows as
their actions change; deselecting clears the panel.

## Risk

Low for B1 (single file). B2 adds a small read-only endpoint + a spec update.
