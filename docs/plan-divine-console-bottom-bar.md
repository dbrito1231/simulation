# Implementation Plan — Divine Console as a Bottom Action Bar

**Status:** Planned — not executing. Awaiting the user's signal to implement.
**Branch:** `feature/god-mode` (same branch as the God-mode backend/viewer this depends on).
**Delivery gate:** documentation only. No `simulation/index.html` code is changed
by this file.
**Prototype:** a clickable HTML mockup was built and shown to the user in this
conversation (`divine-bar-prototype.html`, published as a Claude artifact) before
this plan was written, so the shape below is already visually approved pending
sign-off on the plan itself.

## Why this change

The Divine Console currently lives as a collapsed `<details>` panel inside the
right sidebar (`#divineConsoleSection` in `simulation/index.html`), with an
in-panel tab strip (`.divine-tabbar`) switching between 8 cramped
`.divine-tab-panel` divs squeezed into sidebar width. The user finds this
placement unsatisfying: it competes for space with Agents/Conversations/
Settlements/Council, and each panel is too narrow to comfortably hold forms like
Story's modifier/primitive editor or Sight's multi-field agent readout.

The requested shape:

- a bar fixed to the bottom of the page;
- each of the 8 features (Unlock, Sight, Voice, Miracles, Story, Laws, History,
  Compile) is its own button in that bar;
- clicking a button opens a large popup window, not an inline panel;
- every control inside that window has a hover tooltip explaining what it does.

## Scope discipline: presentation only

This is a DOM/CSS/JS relocation, not a rebuild. Every piece of god-mode logic
already implemented across Phases 2–8 is untouched:

- `wireDivineForm()` (generic Preview → Apply wiring, invalidate-on-edit,
  disabled-until-preview) — reused as-is.
- `godApiFetch` / token lifecycle / 401 → re-lock handling — reused as-is.
- All rendering discipline (`escapeHtml()`, `textContent`, never
  `normalizedCommand` into `innerHTML`) — reused as-is, extended to tooltip
  content.
- The 8 tab bodies (`#divineTab-unlock` … `#divineTab-compile`) — their inner
  markup and event wiring move house; their content does not change.
- `GOD_MODE_ENABLED_FLAG` gating — the bar renders `display:none` under exactly
  the same condition `#divineConsoleSection` does today. A flag-off `/state`
  snapshot must still render byte-identical to a build that never had this
  feature, per the existing regression contract.

No engine code, no HTTP routes, no specs beyond `specs/11-viewer.md` change.

## Target structure

```
<div id="divineBar">              <!-- fixed; bottom:0; left:0; right:0 -->
  <button data-feature="unlock">   ... 8 buttons, one per existing tab ...
  <button data-feature="sight">
  ...
  <button data-feature="compile">  <!-- stays hidden unless capabilities.compiler.enabled -->
</div>

<div id="divineModalScrim">        <!-- backdrop; click-outside and Esc close it -->
  <div id="divineModal" role="dialog" aria-modal="true">
    <header>icon + title + subtitle + close button</header>
    <div id="divineModalBody">     <!-- the existing #divineTab-<name> node is
                                         REPARENTED here on open, not cloned -->
    </div>
  </div>
</div>
```

Reparenting (not cloning) the existing tab-body nodes into the modal on open,
and returning them to their original hidden container on close, means every
existing `getElementById("divineTab-story")`-style reference in the current JS
keeps working unmodified — only the container they render into changes.

## Tooltip contract

Every interactive control and every fieldset legend gets a `data-tip` attribute
carrying a short title + one-sentence description (mirroring the plan's existing
"reversibility class" and "duration" disclosures — this makes explicit what
preview already discloses implicitly). A single shared tooltip engine:

- shows on `mouseenter`/`focusin`, hides on `mouseleave`/`focusout` (keyboard
  accessible, not hover-only);
- positions above the element, flipping below if it would clip the viewport top;
- content goes through the existing text-escaping discipline (`textContent`,
  never raw HTML from a variable);
- respects `prefers-reduced-motion` for its show/hide transition.

Each of the 8 buttons in the bar itself also gets a tooltip (what this whole
window does), not just the controls inside the opened window.

## Implementation steps

1. **CSS** — add `.divine-bar`, `.gbtn` (bar button), `.modal-scrim`, `.modal`,
   `.modal-head`, `.modal-body`, `#tooltip` rules near the existing
   `.divine-*` block in the `<style>` section. Reuse existing tokens
   (`--gold`/`#ffd27a`, the panel/edge greys) rather than inventing a new
   palette — this is the same feature, relocated.
2. **HTML** — replace `<section class="panel-section" id="divineConsoleSection">`
   with the bar markup (buttons only) placed as a sibling of `#wrap`, plus the
   modal scrim/shell placed once, also as a sibling of `#wrap`. Move the 8
   `#divineTab-*` bodies to sit inside a hidden holding container (so they still
   exist in the DOM for `wireDivineForm` to bind to at load time) rather than
   deleting the sidebar section outright.
3. **JS** —
   - Replace `showGodTab(name)`'s in-sidebar toggle with:
     `openDivineModal(name)` (reparents `#divineTab-<name>` into
     `#divineModalBody`, sets header text/icon, opens the scrim) and
     `closeDivineModal()` (reparents it back, closes the scrim).
   - Keep the existing per-tab side effects that already fire on tab switch —
     `refreshGodSight()` on Sight, `renderGodLawsActive()` on Laws,
     `renderGodHistory()` on History — calling them from `openDivineModal`
     instead of `showGodTab`.
   - Add the shared tooltip engine (mouseenter/focusin listeners delegated from
     a single root, so late-rendered content — e.g. Sight's agent list —
     doesn't need re-wiring).
   - Gate bar visibility exactly where `divineConsoleSectionEl.style.display`
     is currently set from `GOD_MODE_ENABLED_FLAG` (`updateGodModeGate()`).
   - Compile's button keeps its existing `capabilities.compiler.enabled` gate.
4. **`specs/11-viewer.md`** — update the Divine Console section to describe the
   bar + modal + tooltip layout in place of the sidebar-panel description.
   Same-change requirement per CLAUDE.md's SDD discipline.

## Verification (same bar as prior phases)

- `scripts/god_mode_smoke.py`, `sid_parity_smoke.py`, `path1_smoke.py` all stay
  green — this change touches no engine/server code, so this is a pure
  regression check.
- Live browser check against the running instance: with the flag off, confirm
  the bar element is absent/`display:none` and the page is otherwise pixel-
  identical to before. With the flag on (current dev server already has
  `SIM_GOD_MODE=1`), exercise: open each of the 8 modals, confirm Preview→Apply
  still functions unchanged, confirm Esc/backdrop-click/✕ all close, confirm a
  tooltip appears on hover and on keyboard focus, confirm the public banner
  (`#godPublicBanner`) still renders independently of the bar/modal.
- Responsive check: bar collapses gracefully under 900px width (existing
  breakpoint), consistent with the plan's "narrow-screen and scroll behavior"
  verification item from Phase 7.

## Out of scope for this change

- No new god-mode features, routes, or effect types.
- No change to the free-prose compiler's dark-flag gating.
- No change to token handling, preview/apply semantics, or any backend
  validation — this plan only relocates where the existing UI renders and adds
  hover explanations to controls that already exist.
