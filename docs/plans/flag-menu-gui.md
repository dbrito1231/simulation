# Plan — GUI menu for the 66 feature flags

**Status:** PLAN ONLY. Nothing implemented. Approval required before any implementer is dispatched.
**Goal:** a viewer menu to see and toggle the 66 module-level feature flags.

---

## 1. The blocking finding (read this first)

Flags are **module-level constants** in `simulation/sim_engine/constants.py`, exec'd into the engine package's shared namespace. Three facts decide this entire design:

1. **Most flags are read at call time from that shared namespace** (`if WILDLIFE_ENABLED:` inside a mixin method), so reassigning the module attribute *would* affect later reads. Live toggling is technically feasible for many flags.
2. **Some flags are consumed once at import time and baked into other artifacts.** `TECH_TREE_ENABLED` is the clearest case: `server.py:2761` reads it at import to rewrite `SYSTEM_PROMPT` and `DECISION_SCHEMA`. Flipping it at runtime desyncs the prompt/schema from engine behavior — the model would be told about a tier field the engine ignores, or vice versa. `GOD_MODE_ENABLED`/`GOD_AUTH_REQUIRED`/`GOD_COMPILER_ENABLED` are env-backed and documented as startup configuration. `DETERMINISM_PINNING` seeds the RNG at engine init.
3. **There is no precedent for runtime flag mutation anywhere in the repo.** The only dynamic flag read is `path1_on()` (`constants.py:1878`), which does `globals().get(subflag)`. This feature would introduce a new class of mutation.

**Therefore a single uniform "toggle any flag live" menu is not safely buildable.** Any plan claiming otherwise is wrong. The design below classifies flags into tiers and treats them differently — that classification is the real work, not the UI.

**Second finding:** `_build_snapshot_config` (`mixin_snapshot.py:263+`) hardcodes its flag echo and carries only ~45 of the 66. The remaining ~21 are invisible to the viewer today, so the menu cannot even *display* them without extending that dict.

---

## 2. Recommended approach

A **three-tier menu**, honest about what each flag can do:

- **Tier A — Live.** Toggles apply immediately under the engine lock. Candidates: flags read only at call time inside tick systems and gated per-think (`WILDLIFE_ENABLED`, `WILDLIFE_BEHAVIOR_ENABLED`, `WEATHER_ENABLED`, `RAIDERS_CONTAGION_ENABLED`, `CHRONICLE_SAGA_ENABLED`, most cosmetic/viewer flags, most Path 1 sub-flags via the existing `path1_on()` indirection).
- **Tier B — Restart required.** The menu records a pending override, shows a "takes effect on restart" badge, and persists it. Candidates: `TECH_TREE_ENABLED`, `GOD_MODE_ENABLED`, `GOD_AUTH_REQUIRED`, `GOD_COMPILER_ENABLED`, `DETERMINISM_PINNING`, `PIANO_MODULES`, `ALWAYS_ON_MODULES`, `SYSTEM_PROMPT_AT_LOAD_TIME`.
- **Tier C — Read-only.** Displayed with a lock icon and a reason. For flags where mid-run toggling would corrupt state rather than merely change behavior (e.g. anything governing a persisted data shape). Expected to be a small set; Phase 1 decides membership.

**Every flag's tier must be decided by reading its actual call sites — not guessed.** That is Phase 1 and it is the bulk of the effort.

**Rejected alternative:** "write overrides to a config file, require restart for everything." Simpler and safer, but the user asked for a menu to turn flags on/off, and a restart-only menu delivers little over editing `constants.py`. Noted as the fallback if Phase 1 finds Tier A is nearly empty.

---

## 3. Safety position (needs a user decision — see §6)

Toggling flags mid-run is a **control-plane intervention**, equal to or stronger than God mode. Disabling `SURVIVAL_ENABLED` mid-run changes the world more than most god commands. Consequences:

- `specs/00-overview.md` states intervened runs "must never be cited as evidence of emergent behavior." A flag-toggled run has the same problem and should be marked the same way.
- God routes have an auth gate (`GOD_AUTH_REQUIRED`) and a `divine.jsonl` audit trail. A flag menu with neither would be a weaker-guarded, more powerful surface.
- The server already warns at startup that the God API is unauthenticated on `0.0.0.0`. Adding another unauthenticated mutation surface widens that exposure.

**Plan's position:** flag changes get an audit trail and a run marking, and reuse the God auth gate when it is on. Confirm in §6.

---

## 4. Phases

### Phase 1 — Classify all 66 flags (the real work; no UI)

**Goal.** A machine-readable registry: for each flag — default, tier, owning spec, echo status, and *why* it got that tier, citing call sites.

- Enumerate every flag from `specs/01-architecture.md`'s flag index (the canonical list of 66, now 67 with `WILDLIFE_BEHAVIOR_ENABLED` — reconcile the count first).
- For each: grep every read site. Classify Tier A/B/C on evidence. Record the deciding call site.
- Flag any whose reads are inconsistent (some at import, some at call time) — those are Tier B regardless.
- Deliverable: a `FLAG_REGISTRY` structure in `constants.py` (or a sibling module) + a table in the owning spec. **No behavior change in this phase.**

**Acceptance:** every one of the 66/67 flags has a tier and a cited justification; no flag is unclassified; `uv run pytest` still green.

### Phase 2 — Extend the `/state` echo

**Goal.** The viewer can see all flags and their tiers.

- Extend `_build_snapshot_config` to echo every flag plus its tier, driven by `FLAG_REGISTRY` rather than a hand-maintained dict (the current hardcoded dict is why ~21 are missing).
- Watch payload size: `/state` is polled at 10 Hz. Flags are static between toggles, so send them in the **full** snapshot only, not every delta.

**Acceptance:** all flags visible in `/state`; delta payload size unchanged in steady state; existing viewer flag mirrors (`GOD_MODE_ENABLED_FLAG`, etc.) keep working unchanged.

### Phase 3 — Backend toggle route

**Goal.** A guarded, audited endpoint to change a flag.

- `POST /control/flags` — `{flag, value}`. Validates against `FLAG_REGISTRY`; rejects unknown names; rejects Tier C; applies Tier A under the engine lock; records Tier B as a pending override.
- **Audit:** every change appends to a JSONL trail (new `flags.jsonl`, or reuse `divine.jsonl` — decide in §6) with flag, old value, new value, frame tick, timestamp.
- **Run marking:** set a `flagsModified` marker on the run, mirroring `intervened`, so a toggled run is never mistaken for a clean autonomous one.
- **Auth:** when `GOD_AUTH_REQUIRED` is on, require the same token as `/control/god/*`.
- **Persistence:** Tier B overrides must survive restart or they are meaningless — needs a small override store read at import. Do **not** put this in `state.db` (that is world state, and `/control/reset` wipes it).

**Acceptance:** Tier A toggle observable in the next tick; Tier B recorded and applied on restart; Tier C rejected cleanly; unknown flag rejected; audit line written; unit tests for each path.

### Phase 4 — The GUI menu

**Goal.** A viewer panel to browse and toggle flags.

- New collapsible sidebar panel (follow the existing `.panel-section` + collapsible-panel contract in `specs/11-viewer.md`), **not** a Divine Console tab — flags are not god commands.
- Group by owning spec (survival/economy, society, Path 1, viewer/cosmetic, cognition, ops) with a filter box; 66 raw checkboxes is unusable.
- Per row: name, current value, tier badge, one-line description, and a link to its owning spec.
- Tier A = live checkbox. Tier B = checkbox with "restart required" badge and pending-state styling. Tier C = disabled with tooltip reason.
- Confirmation step for flags that visibly alter the world (`SURVIVAL_ENABLED`, `LIFECYCLE_ENABLED`, etc.) — an accidental click should not silently halt survival.
- Viewer stays a pure renderer: the panel POSTs and re-reads `/state`; it never holds authoritative flag state.

**Acceptance:** all flags listed and grouped; live toggle visibly takes effect; restart-required clearly distinguished; panel hidden/no-ops cleanly if the route is disabled; flag-off render unchanged from today.

### Phase 5 — Specs and docs

- `specs/01-architecture.md` — flag index gains the tier column; document `FLAG_REGISTRY` as the single source.
- `specs/04-http-api.md` — the new route.
- `specs/11-viewer.md` — the panel.
- `specs/12-ops.md` — the audit trail and the `flagsModified` run marker.
- `CLAUDE.md` — brief pointer.

---

## 5. Risks

| Risk | Mitigation |
|---|---|
| A flag classified Tier A that is actually import-baked → silent prompt/engine desync | Phase 1 requires a cited call site per flag; reviewer independently re-derives a sample |
| Mid-run toggle corrupts persisted state (e.g. disabling a flag that owns a data shape) | Tier C exists for exactly this; when in doubt, classify stricter |
| KV-cache invalidation | Toggling `CONTRACTS_ENABLED` changes the system-prompt addendum mid-session, forfeiting prefix reuse until stable. Document; do not block |
| New unauthenticated mutation surface | Reuse God auth gate; audit trail; run marking |
| `/state` payload growth at 10 Hz | Full-snapshot only, never in deltas |
| Toggled runs polluting benchmark comparisons | `flagsModified` marker, mirroring `intervened` |

## 6. Decisions needed before implementation

1. **Auth:** reuse the God token gate, or leave the route open like `/control/pause`?
2. **Audit destination:** new `flags.jsonl`, or fold into `divine.jsonl`?
3. **Tier B persistence:** where do overrides live — a new `flag_overrides.json`, or env vars written at toggle time?
4. **Scope check:** if Phase 1 finds Tier A is nearly empty, do we still build the menu (restart-only), or stop?

## 7. Standing constraints

- SDD: owning spec updated in the same change as code.
- KISS: no speculative refactor of the flag system beyond what the menu needs.
- **`GOD_MODE_ENABLED` stays default True** — the menu may expose it as Tier B, but its default is not changed by this work.
- Phase order is 1 → 2 → 3 → 4 → 5. Phase 1 gates everything; if its classification says otherwise, the plan changes.
