# Plan — Minimize the feature-flag surface (70 → ~45)

**Status:** PLAN ONLY. Nothing implemented. **Decisions in §7 are settled — this plan is ready to execute on approval.**

> **Correction (supersedes the first draft).** The first draft's headline said "67 → ~34". Two errors: the flag total is **70**, not 67, and 70 − 9 − 22 is **39**, not 34 — the arithmetic was simply wrong. With the settled decisions below (observability stays unmerged, `DECISION_AUDIT_ENABLED` moves out of the drop list), the honest target is **70 → ~45**.
**Supersedes:** [flag-menu-gui.md](flag-menu-gui.md) as the current priority — a smaller flag set makes that menu cheaper if it is ever revisited.

Every flag below was enumerated from `simulation/sim_engine/constants.py` and its **reference count measured** across `simulation/**/*.py` and `simulation/**/*.js`. Reference count is the proxy for removal cost: a 4-reference flag is a trivial inline; a 93-reference flag is a large, risky diff for no behavioral gain.

---

## 1. Headline numbers

| | Count |
|---|---|
| Flags today (index says 66; actual constants incl. 3 undocumented tuning booleans + `WILDLIFE_BEHAVIOR_ENABLED`) | **70** |
| Drop — first pass (hardcode to current default, delete the flag) | **−8** |
| Merge into bundles | **−17** |
| Drop — KEEP-list re-triage (§3c-bis) | **−8** |
| Keep as real flags | **~37** (~33 if the borderline four go too) |
| God-mode flags after change | 4, unchanged (`GOD_MODE_ENABLED` on, `GOD_AUTH_REQUIRED` off, 2 dark) |

Reduction is ~36% of the flag surface, achieved entirely from low-reference, low-risk changes. No flag changes its effective runtime value.

**Count discrepancy found:** `specs/01-architecture.md` says 66; the code has 70 flag-like booleans. Three are real flags never added to the index — `MODULE_REFRESH_IDLE_SKIP`, `BIRTH_STARTING_SKILL_PENALTY`, `HARVEST_SPIRIT_CONTRIB_BOOST`. The index is incomplete, which is itself an argument for this cleanup.

---

## 2. God mode — mostly already satisfied

Your requirement: god flags enabled by default, no auth, except dark/experimental. Current state:

| Flag | Default today | Meets requirement? |
|---|---|---|
| `GOD_MODE_ENABLED` | **True** (`SIM_GOD_MODE` defaults `"1"`) | ✅ already on by default |
| `GOD_AUTH_REQUIRED` | **False** | ✅ already no auth required |
| `GOD_COMPILER_ENABLED` | False (dark) | ✅ stays dark |
| `GOD_DEJA_VU_REPLAY` | False (dark) | ✅ stays dark |

So **no default changes are needed** — this is already the shipped behavior. The only actionable item is whether to *delete* the auth mechanism:

**DECIDED: keep `GOD_AUTH_REQUIRED`, default False.** The gate stays available as an opt-in and is not deleted. Rationale: the server binds `0.0.0.0` and prints at startup *"God API is unauthenticated … any LAN client can mutate the world via /control/god/*."* Deleting the gate would make that permanent and unfixable without new code; a default-off flag costs nothing and preserves the escape hatch.

**Work required for the God-mode requirement: none.** All four flags already sit at the requested defaults. No phase below touches them.

---

## 3. Full disposition of every flag

### 3a. DROP — hardcode to current value, delete the flag (−8)

Permanently on, shipped long ago, cheap to inline. None has a plausible "turn this off" scenario.

| Flag | Refs | Why drop |
|---|---|---|
| `BIRTH_STARTING_SKILL_PENALTY` | 3 | Tuning boolean, not in the flag index, never toggled |
| `HARVEST_SPIRIT_CONTRIB_BOOST` | 4 | Same — a tuning constant masquerading as a flag |
| `MODULE_REFRESH_IDLE_SKIP` | 4 | Only meaningful under dark `ALWAYS_ON_MODULES`; fold into it |
| `AGENT_MESSAGING` | 6 | Core mechanic — a village where agents cannot message is not a supported mode |
| `LIBRARY_SCALING_ENABLED` | 7 | Shipped, stable, never flipped |
| `ECONOMY_SINKS_ENABLED` | 8 | Sub-behavior of `ECONOMY_ENABLED`; no independent value |
| `ROADS_ENABLED` | 9 | Roads are load-bearing for movement/pathfinding |
| `SAGE_REVIEW_ENABLED` | 10 | The two-stage blueprint gate is the documented core loop |

### 3b. MERGE — collapse families into bundles (−17)

**Path 1 sub-flags → `PATH1_ENABLED` (−7).** `path1_on()` already returns True for every sub-flag whenever `PATH1_ENABLED` is on, so the sub-flags only matter in a staged-rollout state that is long finished. Removing them also removes a confusing double-gate.
`INDUSTRY_ENABLED` (10), `TOOL_TIERS_ENABLED` (11), `COMPOSABLE_BUILD_ENABLED` (10), `TERRAIN_TILES_ENABLED` (14), `PATH1_DIPLOMACY_ENABLED` (24), `TIER3_CONTENT_ENABLED` (9), `PRESSURE_LOOP_ENABLED` (12).

**Cosmetic/viewer → `VISUALS_ENABLED` (−5).** All are pure presentation with no engine behavior; they always ship on together.
`ACTIVITY_CUES_ENABLED` (10), `SEASONAL_AGENTS_ENABLED` (10), `WORLD_CLOCK_HUD_ENABLED` (10), `FOUNDING_EVENTS_ENABLED` (11), `STRUCTURE_WEAR_ENABLED` (14), `CARAVAN_VISUALS_ENABLED` (18).

**Observability — DECIDED: NOT merged.** All six stay independent flags. The first draft proposed bundling them as "pure read-side," which closer inspection showed to be wrong for half of them:

| Flag | Refs | Why it is not merely a panel toggle |
|---|---|---|
| `BENCHMARKS_ENABLED` | 13 | Writes the `benchmarks.jsonl` stream that `ANOMALY_RADAR` reads |
| `ANOMALY_RADAR_ENABLED` | 7 | Read-only, but **depends on** `BENCHMARKS_ENABLED`'s output |
| `DECISION_AUDIT_ENABLED` | 4 | Gates engine-side correlation-id **minting** — off means `llm.jsonl`/`activity.jsonl` stop carrying `decision_id` at all. A logging-behavior flag, not a panel flag |
| `WORLD_WIKI_ENABLED` | 10 | Read-only route + modal; no LLM |
| `PREDICTION_MARKET_ENABLED` | 13 | Player-facing, and gates `predictions.json` file I/O |
| `AGENT_INTERVIEW_ENABLED` | 8 | **A live LLM call site** on `sim-smart` with its own concurrency pool — bundling it would let a "debug" switch silently consume GPU |

Keeping them separate preserves the ability to disable the GPU-consuming interview surface without also losing benchmarks, and keeps a logging-behavior flag from hiding inside a UI bundle.

**Memory → `MEMORY_ENABLED` (−2).** `WIKI_MEMORY` (21) and `TESTAMENT_ENABLED` (16) are both meaningless with memory off.

**Chronicle → `CHRONICLE_ENABLED` (−1).** `CHRONICLE_SAGA_ENABLED` (16) is a sub-behavior.

**Weather → `WEATHER_ENABLED` (−1).** `WEATHER_GOVERNANCE_ENABLED` (14) is a sub-behavior.

**Lifecycle → `LIFECYCLE_ENABLED` (−1).** `DYNASTY_TREE_ENABLED` (11) only gates a viewer panel over lifecycle data.

**Deferred:** `WILDLIFE_BEHAVIOR_ENABLED` → `WILDLIFE_ENABLED` (−1). **Not in this plan.** It shipped days ago and its one-flag revert still has value. Merge after it has soaked (see §5 maturity rule).

### 3c-bis. RE-TRIAGE of the KEEP list (added after review)

The first pass justified KEEP as "genuine kill switch" by intuition. Re-triaged against two measured signals:

- **Spec-cue hits** — mentions of the flag within ±6 lines of `flag-off` / `one-flag revert` / `soak` / `A/B` / `byte-identical` / `kill switch` language in `specs/*.md`. Non-zero means a spec **documents a dependence on the flag-off path**; deleting the flag invalidates a written guarantee or a measurement procedure.
- **Reference count** — removal cost. Every reference is a conditional whose else-branch must be deleted correctly.

**Rule applied:** drop when cues = 0 AND refs ≤ ~20 AND the feature is mature. Keep when a spec documents flag-off dependence, the flag is dark/recent, or refs are high enough that the removal itself is the bigger risk.

**NOW DROPPABLE — 0 spec cues, low reference count (−8):**

| Flag | Cues | Refs | Note |
|---|---|---|---|
| `USE_GOALS` | 0 | 11 | Mature, no documented off-path |
| `TRANSIT_ENABLED` | 1* | 11 | *cue is incidental prose, not a revert guarantee — verify in phase |
| `EMERGENT_ROLES` | 0 | 14 | Core mechanic, never flipped |
| `DAILY_COUNCIL_ENABLED` | 1* | 15 | *same — verify |
| `META_SYSTEM` | 0 | 16 | Default-on since Sid-parity Phase 3 |
| `SOCIAL_LAYER_ENABLED` | 0 | 16 | Cosmetic-adjacent, mature |
| `CHRONICLE_ENABLED` | 0 | 16 | Drops the whole chronicle family (supersedes the saga merge) |
| `CEMETERY_ENABLED` | 0 | 21 | Borderline on refs; mature |

**BORDERLINE — 0 cues but mid-range refs (−4 if you want the deeper cut):**
`STRUCTURE_EFFECTS_ENABLED` (26), `STRUCTURE_UPGRADES_ENABLED` (28), `CRAFTING_ENABLED` (29), `RULES_ENABLED` (33). No spec documents their off-path, but each is a 25–35 conditional deletion. Worth doing only as a dedicated phase with its own review.

**CONFIRMED KEEP — spec documents flag-off dependence, or removal risk dominates:**

| Flag | Cues | Refs | Why it stays |
|---|---|---|---|
| `CONTRACTS_ENABLED` | 7 | 23 | D9 prompt-cost measurement is stated flag-off vs flag-on; the addendum's "flag-off ≈0 tokens" guarantee needs the off path |
| `RAIDERS_CONTAGION_ENABLED` | 6 | 42 | Most flag-off documentation of any flag |
| `WEATHER_ENABLED` | 4 | 40 | Documented off-path behavior |
| `FACTION_SPLIT_ENABLED` | 4 | 93 | Highest removal cost in the repo |
| `LIFECYCLE_ENABLED` | 3 | 55 | Governs persisted agent shape |
| `WILDLIFE_ENABLED` | 3 | 52 | — |
| `WILDLIFE_BEHAVIOR_ENABLED` | 3 | 8 | Shipped days ago; maturity rule |
| `TECH_TREE_ENABLED` | 2 | 71 | Also import-time prompt/schema rewrite — not safely removable without touching prompts |
| `GOODS_ENABLED` | 2 | 45 | — |
| `SURVIVAL_ENABLED` | 2 | 33 | Emergency lever on a 24/7 run |
| `CROP_GROWTH_ENABLED` | 2 | 23 | — |
| `CULTURE_ENABLED` | 0 | 63 | Zero cues, but 63 refs — removal risk dominates |
| `ECOLOGY_ENABLED` | 0 | 39 | Same |
| `MEMES_ENABLED` | 1 | 36 | Same |
| `ECONOMY_ENABLED` | 1 | 35 | Same |
| `ENV_EFFECTS_ENABLED` | 1 | 28 | Same |
| `MEMORY_ENABLED` | 1 | 16 | Bundle target for the memory family |
| `PIANO_MODULES` | 1 | 19 | Gates a worker pool + GPU load |
| `PATH1_ENABLED` | 1 | 13 | Bundle target for 7 sub-flags |
| all 5 dark/experimental | — | — | `ALWAYS_ON_MODULES`, `THEORY_OF_MIND_ENABLED`, `GOD_COMPILER_ENABLED`, `GOD_DEJA_VU_REPLAY`, `DETERMINISM_PINNING` |
| all 6 observability | — | — | Per §3b decision |
| `GOD_MODE_ENABLED`, `GOD_AUTH_REQUIRED` | — | — | Per §2 |

**Revised totals:** 70 − 8 (drop) − 17 (merge) − 8 (re-triage drops) = **~37**, or **~33** if the borderline four are included.

**Methodology caveat:** a 0-cue result means no spec *documents* a flag-off dependence — it does not prove none exists in code. Each drop phase must confirm the else-branch is genuinely dead before deleting, and the reviewer must re-derive a sample independently. Two flags marked `*` above have incidental cue hits that need reading in context before they are dropped.

### 3c. KEEP — original first-pass list (superseded by 3c-bis above)

**Dark / experimental — keep exactly as-is:** `ALWAYS_ON_MODULES` (False, gate failed), `THEORY_OF_MIND_ENABLED` (False, env), `GOD_COMPILER_ENABLED` (False, dark), `GOD_DEJA_VU_REPLAY` (False, dark), `DETERMINISM_PINNING` (False, env — needed for repro runs).

**Observability — keep all six independent** (per §3b): `BENCHMARKS_ENABLED`, `ANOMALY_RADAR_ENABLED`, `DECISION_AUDIT_ENABLED`, `WORLD_WIKI_ENABLED`, `PREDICTION_MARKET_ENABLED`, `AGENT_INTERVIEW_ENABLED`.

**Major subsystem kill switches — keep:** `SURVIVAL_ENABLED` (33), `ECONOMY_ENABLED` (35), `RULES_ENABLED` (33), `MEMES_ENABLED` (36), `CULTURE_ENABLED` (63), `LIFECYCLE_ENABLED` (55), `TECH_TREE_ENABLED` (71), `DAILY_COUNCIL_ENABLED` (15), `FACTION_SPLIT_ENABLED` (93), `RAIDERS_CONTAGION_ENABLED` (42), `GOD_MODE_ENABLED` (46), `PIANO_MODULES` (19), `META_SYSTEM` (16), `CONTRACTS_ENABLED` (23), `ECOLOGY_ENABLED` (39), `WILDLIFE_ENABLED` (52), `GOODS_ENABLED` (45), `CRAFTING_ENABLED` (29), `EMERGENT_ROLES` (14), `CEMETERY_ENABLED` (21), `STRUCTURE_EFFECTS_ENABLED` (26), `STRUCTURE_UPGRADES_ENABLED` (28), `ENV_EFFECTS_ENABLED` (28), `CROP_GROWTH_ENABLED` (23), `TRANSIT_ENABLED` (11), `SOCIAL_LAYER_ENABLED` (16), `USE_GOALS` (11), `PATH1_ENABLED` (13), `WEATHER_ENABLED` (40), `MEMORY_ENABLED` (16), `CHRONICLE_ENABLED` (16), `WILDLIFE_BEHAVIOR_ENABLED` (8, until soaked), `GOD_AUTH_REQUIRED` (19, pending §2 decision).

**Rationale for keeping the high-count ones:** `FACTION_SPLIT_ENABLED` (93 refs), `TECH_TREE_ENABLED` (71), `CULTURE_ENABLED` (63) are the *most* expensive to remove and among the most valuable to keep — they gate large, recently-active subsystems where a fast revert is real insurance. Removal cost is highest exactly where removal benefit is lowest.

---

## 4. Phases

1. **Reconcile the index + produce the authoritative disposition table.** Add the 3 undocumented flags to `specs/01-architecture.md`, fix the stated count (66 → 70), and classify **every** flag DROP / MERGE / KEEP by *reading the spec context* around each cue hit — not by grep count alone. No code change. *Do this first: it is the honest baseline, and it is what every later phase executes against.*

   **Corrections already found this way (apply them, and expect more):**
   - `WIKI_MEMORY` — specs/03 states "Default flipped to `True` … after D2 soak; flag-off remains a one-flag revert." **KEEP**, do not merge into `MEMORY_ENABLED`.
   - `DYNASTY_TREE_ENABLED` — specs/01 documents it explicitly as a **kill switch** with a restart procedure. **KEEP**, do not merge into `LIFECYCLE_ENABLED`.
   - `ECONOMY_SINKS_ENABLED`, Path 1 sub-flags — cue hits verified **incidental** (section prose, not revert guarantees). Drop/merge stands.
   - Path 1 note: specs/10 says `path1_on()` "falls back to the named sub-flag's own value" when `PATH1_ENABLED` is off, so merging the seven makes Path 1 **all-or-nothing**. That is an accepted, documented consequence — record it in specs/10.
   - Still unverified, must be context-read in this phase: `TESTAMENT_ENABLED` (4 cues), `CHRONICLE_SAGA_ENABLED` (2), `WEATHER_GOVERNANCE_ENABLED` (2), `TRANSIT_ENABLED` (1), `DAILY_COUNCIL_ENABLED` (1).
2. **Drop the 8.** Inline each to its current value, delete the constant, update owning specs. Lowest risk; one pass, verified with `uv run pytest` + smokes.
3. **Merge Path 1 (−7).** Largest single win; `path1_on()` already makes it near-mechanical. Remove `path1_on()`'s subflag parameter afterward.
4. **Merge cosmetic (−5).** Touches viewer + `/state` `config.flags`; update `_build_snapshot_config` and the viewer mirrors in the same change.
5. **Merge the small families (−5):** memory, chronicle, weather, lifecycle.

**No God-mode phase** — §2 requires no work. **No observability phase** — §3b keeps all six.

Each phase = one implementer + one reviewer, per the AGENTS.md loop.

---

## 5. Rules that must hold

- **Maturity rule:** never drop the revert flag of a feature that shipped in the last ~2 weeks or has not completed a soak. That protects `WILDLIFE_BEHAVIOR_ENABLED` today and any future one-flag revert.
- **SDD:** each dropped/merged flag's owning spec updated in the same change. `specs/01-architecture.md`'s flag index is the master list and must match exactly after every phase.
- **`/state` contract:** removing a flag from `config.flags` is a viewer-visible change — update `_build_snapshot_config` and every viewer mirror (`GOD_MODE_ENABLED_FLAG` and friends) in the same phase.
- **No behavior change.** Every flag is being hardcoded to *its current default*. If any phase changes runtime behavior, it is a bug, and the smokes/determinism proof must catch it.
- **Verification per phase:** `uv run pytest`, `sid_parity_smoke.py`, `path1_smoke.py`, `hunt_conflict_smoke.py`, `determinism_proof.py` (bit-identical), plus the subsystem smoke covering the touched area.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Dropping a flag that was someone's rollback plan | Maturity rule; keep every dark/experimental toggle |
| A "cosmetic" flag turns out to gate engine behavior | Phase 4 must verify each is viewer-only before merging; reviewer re-derives independently |
| Merged bundle hides a sub-behavior someone needed | Bundles are documented in specs with the exact sub-behaviors they now cover |
| Viewer/`config.flags` desync after removal | Same-phase update; the action-sync-style test pattern could be extended to flags |

## 7. Decisions — settled

| # | Decision | Outcome |
|---|---|---|
| 1 | `GOD_AUTH_REQUIRED` | **Keep, default off.** Not deleted. Zero work — already the shipped behavior. The opt-in gate stays available because the server binds `0.0.0.0` |
| 2 | Observability bundle | **Do not merge.** All six stay independent — half are not read-only (see §3b) |
| 3 | Reduction target | **Stop at the identified scope.** Drop 8 + merge 17; keep every dark toggle and every major subsystem kill switch. Do NOT touch the high-reference switches (`FACTION_SPLIT` 93, `TECH_TREE` 71, `CULTURE` 63) |

Net effect: **70 → ~45 flags**, all from low-reference, low-risk changes, with no runtime behavior change.

**Ready to execute on approval.** Five phases, each one implementer + one reviewer per the AGENTS.md loop.
