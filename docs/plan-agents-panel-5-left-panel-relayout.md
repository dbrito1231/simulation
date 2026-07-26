# Plan — Agents Panel 5: Move Agents to the left panel; drop Conversations & Settlements

**Status:** Planned — decisions locked (see Decisions). Ships on the existing
`feature/agents-panel-overhaul` branch / PR #2.
**Scope:** Client-only (`simulation/index.html`) — markup move + flag-gated
section hiding. No server/engine changes. Spec check required (see below), but no
`/state` contract change.
**Depends on:** Plans 1–3 shipped (the Agents section already contains the rollup
header, vitals, detail panel, etc. — this plan relocates that whole section as-is).

## Decisions (from user)
1. **Agents placement:** TOP of the left panel (above Activity).
2. **Left-panel title:** rename `<h1>` from "Activity & Chat" to **"Agents & Activity"**.
3. **Removal style:** **hide behind a viewer flag** (keep the markup + render code;
   gate visibility so it can be toggled back), NOT full deletion.

## Goal

Reorganize the viewer's two side panels:
1. **Move the Agents section** from the right sidebar (`#sidebar`) into the left
   panel (`#convPanel`).
2. **Remove the Conversations section** (`#conversationLog`) from the left panel.
3. **Remove the Settlements section** (`#settlementsSection`) from the left panel.

## Non-technical summary

The villager list (with all its new vitals/inventory/social detail) becomes the
focus of the left-hand panel, sitting alongside the Activity log. The chat
"Conversations" feed and the "Settlements" list are removed entirely. The right
sidebar keeps Time and Civilization.

## Current layout (grounded)

Left panel — `<aside id="convPanel">` "Activity & Chat" (index.html ~709–734):
- `#conversationLog` — **"Conversations"** (`#convList`) — **REMOVE**
- `#activityLog` — "Activity" (`#actList`) — keep
- `#settlementsSection` — **"Settlements"** — **REMOVE** (already `display:none` by default; only shows when `PATH1_ENABLED` + settlements exist)
- `#councilSection` — "Council" — keep (not in scope)

Right panel — `<aside id="sidebar">` (index.html ~745–806):
- `#timePanel` "Time" — keep
- `#civPanel` "Civilization" — keep
- **Agents** `panel-section` (~796–804: `agents-panel-head`, `#agentRollup`, `#agentList`, `#agentDetail`, `#deadAgentsBtn`, `#livingAgentCount`) — **MOVE to left panel**

## Steps

### A. Move the Agents section
- Cut the entire Agents `<section class="panel-section">…</section>` block (index.html ~796–804) out of `#sidebar` and paste it into `#convPanel`.
- **Placement decision (see Open questions):** default to placing Agents FIRST inside `#convPanel` (above Activity), since it's now the primary panel.
- No JS changes needed for the move itself — `agentListEl`, `agentRollup`, `agentDetail`, `deadAgentsBtn`, `livingAgentCount` are all looked up by `id`, which is unchanged. `renderAgentPanel()` continues to run from `renderSidebar()`.

### Flag setup (for B & C)
- Add two client-side viewer flags near the top of the `<script>` with the other
  viewer consts, defaulting OFF: `const SHOW_CONVERSATIONS = false;` and
  `const SHOW_SETTLEMENTS = false;`. Flipping either to `true` restores the
  section with no other edits. (Keep them separate so each can be toggled alone.)

### B. Hide Conversations (flag-gated)
- Keep the `#conversationLog` `<section>` markup, but hide it when the flag is off:
  either set `style="display:none"` initially and toggle in JS, or set its display
  from `SHOW_CONVERSATIONS` on init.
- In the "Activity & Chat panel" render block (index.html ~2228–2249): guard the
  `convListEl.innerHTML = …` / `convScroll` work behind `if (SHOW_CONVERSATIONS)`;
  drop `conversation` from `logKey` when the flag is off (or keep the ref but skip
  the render). Keep the `activity` / `actListEl` rendering unchanged. `convListEl`
  ref stays (no null-deref risk since markup remains).
- No CSS removal (markup is retained, just hidden).
- Note: `world.conversation` still arrives in `/state`; we simply don't render it
  while the flag is off. No server/spec change.

### C. Hide Settlements (flag-gated)
- Keep the `#settlementsSection` markup and `renderSettlements()`.
- Gate the call site `renderSettlements(civ)` (index.html ~2213) behind
  `if (SHOW_SETTLEMENTS)`, and ensure the section stays hidden when the flag is
  off (it already defaults to `display:none` and only shows via `renderSettlements`;
  skipping the call keeps it hidden). Element refs `settlementsSectionEl` etc.
  stay (no null-deref).
- Note: `civ.settlements` still arrives in `/state`; just not shown while off.

### D. Rename the left panel header
- Rename `<h1>Activity &amp; Chat</h1>` (line 710) to `<h1>Agents &amp; Activity</h1>`.

### E. CSS / layout
- The Agents section can be tall (rollup + up to N rows + detail panel). Confirm `#convPanel` scrolls (it already hosts the Activity list; check the ~380px width sync comment at index.html ~74 and any `overflow`/height rules). Ensure the moved section doesn't overflow the panel — add `overflow-y:auto` on the panel body if needed.
- Verify the right `#sidebar` still lays out cleanly with only Time + Civilization.

## Spec check (required, likely no-op)
Grep `specs/` for references to the viewer's Conversations/Settlements/Agents
panels. The specs describe the `/state` **contract** (which still ships
`conversation` and `settlements`), not which panels the thin viewer renders, so a
UI-only relayout should need **no** spec change. Confirm this — if any spec
asserts these panels exist in the viewer, update it in the same change.

## Verify
- Server serves 200; reload the viewer.
- Left panel shows Agents FIRST (with rollup/vitals/detail intact), then Activity
  (+ Council when active); Conversations and Settlements hidden.
- Left-panel title reads "Agents & Activity".
- Right sidebar shows Time + Civilization only.
- No console errors.
- Flipping `SHOW_CONVERSATIONS = true` restores the Conversations feed;
  `SHOW_SETTLEMENTS = true` restores Settlements — confirm the toggle works both ways.
- Selecting an agent still opens the detail panel; deceased modal still works.

## Risk
Low. Because the flag approach RETAINS all markup and element refs, there is no
null-deref risk (the main footgun of full deletion is avoided). Main things to
check: the moved Agents section fits/scrolls in the left panel, and the render
guards skip cleanly when flags are off.
