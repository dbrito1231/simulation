# Plan — idea-02: Dynasty tree

**Status:** READY — all open questions answered.

**Order:** 5 of 11 in the meta-plan §8 production order (first engine state
addition in the set — small, contained; depends on nothing).

---

## 1. Idea text (verbatim)

> **2. Dynasty tree.**
> `mixin_lifecycle.py:147` says outright there's no formal family tree yet. Record
> `parents`/`children` at birth and render lineage: who descends from whom, which
> testament each heir inherited, whether beliefs travel down bloodlines or
> sideways through conversation. Makes long runs legible, and it's the substrate
> for any future heritable-trait work.

---

## 2. Answered questions

All five questions below were raised during planning, verified against the
repo, and put to the user. Their answers are decisions, not suggestions, and
the rest of this plan (§4–§8) is reconciled to them. The verified technical
findings from the original research are preserved under each answer because
they remain load-bearing for the implementer phases.

**Correction to the idea's premise, verified by reading the code (not an
assumption):** `parents` is **already recorded at birth**. `_spawn_newborn`
(`simulation/sim_engine/mixin_lifecycle.py:1186-1253`) sets
`newborn["parents"] = [parent_a["name"], parent_b["name"]]` at line 1191, and
`_make_agents` seeds `a["parents"] = None` for every cold-start agent
(`simulation/sim_engine/core.py:365`). `specs/06-agents.md:128` already documents
`parents` as an existing Lifecycle-gated agent field. What is genuinely missing —
matching the `mixin_lifecycle.py:145-153` comment cited by the idea ("no formal
family tree yet") — is a **reverse `children` lookup**: today `_heirs_of()`
(`mixin_lifecycle.py:144-153`) derives children by scanning every living agent's
`parents` list on demand, every time it needs heirs; there is no persisted
`children` array on the parent agent itself. This plan proposes adding that
reverse array plus a viewer lineage panel. The answers below are about the
remaining, previously-ambiguous design points, not about whether `parents`
exists.

1. **`children` field shape and write point.** **Answer: names, mirroring
   `parents`.** `agent["children"]` is a list of names (not ids), consistent
   with every other identity-linkage field this plan found in the codebase
   (`parents`, `relationships` keys, `homeOf`). The newborn's name is appended
   to both `parent_a["children"]` and `parent_b["children"]` inside
   `_spawn_newborn` at the same point `parents` is set on the newborn
   (`mixin_lifecycle.py:1191`).
2. **Should `_heirs_of()` be rewired to read the new `children` array instead of
   scanning `parents`?** **Answer: yes.** `_heirs_of()`
   (`mixin_lifecycle.py:144-153`) will read `agent.get("children") or []`,
   filtered to living agents, directly, instead of re-scanning `self.agents`
   for matching `parents` entries — same behavior, cheaper, and it makes
   `children` load-bearing rather than a second, potentially drifting copy of
   the same information. **This touches an existing, already-shipped code
   path** (`_heirs_of()` is called from `_inherit_from()`, which drives goods/
   home/beliefs inheritance and the deceased-elder succession flow), so Phase 1
   and Gate 1 must include an explicit **regression check**: the rewired
   `_heirs_of()` must return the identical heir set, on the same fixture, as
   the pre-change scan-based implementation (same living-agent filtering, same
   membership) — not merely "the new field reads correctly." See §7's
   migration note and §8 Phase 1/§14 acceptance for how this is exercised.
3. **"Which testament each heir inherited."** **Answer, resolved by this
   reconciliation pass (not deferred to Phase 0):** no traceable per-heir
   testament link exists today — a new field is required. Read in full:
   `_merge_testament_on_death` (`simulation/sim_engine/mixin_governance_culture.py:1298-1312`)
   and `_seed_newborn_wiki_from_testament`
   (`simulation/sim_engine/mixin_governance_culture.py:1331-1366`), both called
   from `mixin_lifecycle.py` (lines 131 and 1214 respectively). Findings:
   - `_merge_testament_on_death` folds a **dying agent's own** `memoryWiki`
     ("lessons"/"relationships" text) into `civilization["testament"]`
     (a village-wide, capped, deduplicated ring — `TESTAMENT_CAP`,
     `_push_testament_entry`, `mixin_governance_culture.py:1274-1296`) —
     attributed to that agent as `author`, but with no link forward to any
     specific heir.
   - `_seed_newborn_wiki_from_testament` seeds a **newborn's** `memoryWiki` by
     joining (a) both parents' existing wiki `lessons`/`relationships`/`goals`
     text and (b) the **newest `TESTAMENT_PROMPT_ENTRIES` entries from the
     village-wide `civilization["testament"]` ring** — not entries scoped to
     this newborn's own ancestors specifically. The join
     (`_join_unique`, line 1343) concatenates raw text into a single capped
     string (`wiki["lessons"]`); it does not retain which individual
     testament entries (with their `author`/`frame`/`generation`) fed into
     that string, so there is nothing on the newborn to trace back to "entry
     X, written by ancestor Y" after the fact.
   - Conclusion: **genuinely missing, confirmed by reading, not assumed.**
     This plan adds a new field, `newborn["inheritedTestament"]` — a snapshot
     **copy** of the testament entry dicts (`{text, author, frame,
     generation}`) that fed into that newborn's wiki at the moment of birth,
     captured inside `_seed_newborn_wiki_from_testament` from the same
     `testament[-TESTAMENT_PROMPT_ENTRIES:]` slice already computed there
     (line 1359) — a deterministic, birth-time snapshot with no new
     mechanic, no new civilization-wide state, and no live link that could
     drift as the shared `civilization["testament"]` ring later caps/drops
     older entries. This keeps the field additive to the existing testament
     system rather than reworking it.
4. **"Whether beliefs travel down bloodlines or sideways through conversation."**
   **Answer: simple static snapshot.** "Beliefs inherited from parents at
   birth" is recorded as a static list, computed from the existing belief
   union already performed in `_spawn_newborn` at line 1198 (verified: the
   newborn's belief set is unioned from both parents at birth). The full live
   bloodline-vs-conversation diff (which would require snapshotting every
   agent's belief set at birth separately from their live, mutating current
   belief set, and comparing against beliefs later picked up via
   `_spread_beliefs_by_proximity`/`MEMES_ENABLED`) is **out of scope**, per the
   user's explicit answer — not attempted in any phase below.
5. **Viewer placement.** **Answer (user's exact words): "Make it a collapsible
   and expandable panel and also add as a button on the divine bar."**
   Verified against `specs/11-viewer.md` and the actual markup: "the Divine
   bar" is `#divineBar` (`simulation/index.html:141`), the fixed bottom action
   bar documented at `specs/11-viewer.md:845-851` ("The Divine Console is a
   fixed bottom action bar plus a large modal dialog... `#divineBar`
   (`position: fixed; bottom: 0; left: 0; right: 0`)"). It already holds a row
   of `.gbtn` buttons (`unlock`, `sight`, `voice`, `matrix`, `miracles`,
   `story`, `laws`, `history`, `audit`, `compile` — `index.html:150-208`),
   each of which opens a named tab inside `#divineModal` via
   `openDivineModal(<tab>)`/`showGodTab()` (`viewer/divine-modal.js`). The
   most recent addition to this exact pattern is the idea-10 Divine Audit tab
   (`specs/11-viewer.md:353-388`): a new `.gbtn audit` button
   (`#godAuditTabBtn`) placed in `#divineBar` "after History / before
   Compile," opening a read-only `#divineTab-audit` panel reparented into
   `#divineModal` on open and reparented back to `#divineTabHold` on close.
   `simulation/css/panels.css` and `simulation/viewer/sidebar.js` were checked
   and contain **no** separate accordion/collapsible-section pattern anywhere
   in the codebase (`grep` for "collaps"/"expand"/"toggle"/"details"/"summary"
   in `panels.css` returned no matches; `sidebar.js` only has unrelated
   `.classList.toggle` calls for CSS state classes and an `"collapsed"` vitals
   string, not a UI collapse mechanism). **The Divine Console modal-tab
   pattern itself already satisfies both halves of the user's answer
   simultaneously**: the tab is closed/hidden by default (collapsed) and
   opened into the modal by its bar button (expanded) — there is no separate,
   competing collapsible-panel precedent to choose between, so this is not a
   genuine ambiguity requiring a new open question. This plan therefore reuses
   the idea-10 Audit-tab pattern exactly: a new `.gbtn lineage` button in
   `#divineBar`, opening a new `#divineTab-lineage` panel inside
   `#divineModal`, following the same registry wiring
   (`DIVINE_FEATURES.lineage` in `viewer/divine-bootstrap.js`, `"lineage"` in
   `GOD_TABS`) and reparent pattern (`viewer/divine-modal.js`) as `audit`. No
   new CSS collapsible-panel component is invented. §8 Phase 3 and its
   implementer prompt are updated below to this concrete design.

---

## 3. Ask, never assume — mandatory clause

> **Ask when in doubt.** Do not assume anything. If any detail is unclear, ambiguous,
> missing, or contradicts a spec — scope, flag defaults, data shapes, route names, UI
> placement, model choice, or acceptance criteria — stop and escalate to the
> orchestrator, who asks the user. Per AGENTS.md, implementers and reviewers never ask
> the user directly and never invent an answer. Work does not proceed on an
> unconfirmed assumption; a guess recorded as fact is FAIL material at review.

---

## 4. Owning specs

- **`specs/06-agents.md`** — the "Agent state fields" table
  (`specs/06-agents.md:116-128`) already lists `parents` under the
  `LIFECYCLE_ENABLED` row; add `children` (list of names) and
  `inheritedTestament` (list of `{text, author, frame, generation}` snapshot
  copies, per §2 Answer 3) to that same row/list. Update the birth section
  (`specs/06-agents.md:241`, "mixin_lifecycle.py seeds the newborn's
  memoryWiki from both parents'...") to also describe the new
  `inheritedTestament` snapshot and the static birth-time belief-inheritance
  list (§2 Answer 4).
- **`specs/02-engine-core.md`** — the Persistence section
  (`specs/02-engine-core.md:250-324`) must document the new `restore_state()`
  `setdefault`-only back-compat for `children` (and, if it needs one,
  `inheritedTestament`) (see §7 migration below), mirroring the documented
  discipline for `parents`/`deathFrame`/etc. (`mixin_persistence.py:540-544`,
  cited there).
- **`specs/09-systems-society.md`** — the new `inheritedTestament` field (§2
  Answer 3) is a birth-time snapshot of the existing Testament mechanism; its
  data shape belongs in the existing "Testament" subsection
  (`specs/09-systems-society.md:715` on), cross-referenced from
  `_seed_newborn_wiki_from_testament`.
- **`specs/11-viewer.md`** — new "Divine Lineage tab" section, following the
  same documentation pattern as the existing "Divine Audit tab" section
  (`specs/11-viewer.md:353-388`): new `.gbtn lineage` button in `#divineBar`,
  new `#divineTab-lineage` panel, registry wiring in
  `viewer/divine-bootstrap.js`/`viewer/divine-modal.js` (per §2 Answer 5).
- **`specs/01-architecture.md`** — new row in the flag index for the new flag.

---

## 5. In-scope files / Out-of-scope files

**In scope:**
- `simulation/sim_engine/constants.py` — new flag `DYNASTY_TREE_ENABLED`.
- `simulation/sim_engine/mixin_lifecycle.py` — `_spawn_newborn` (append to
  `children`; snapshot birth-time belief-inheritance list per §2 Answer 4),
  `_heirs_of` (rewired to read `children` per §2 Answer 2), `core.py` seed
  default for `children` on cold-start agents (mirror `a["parents"] = None` at
  `core.py:365`, using `[]` for `children` since it is a list, not a nullable
  link).
- `simulation/sim_engine/mixin_governance_culture.py` —
  `_seed_newborn_wiki_from_testament` (set `newborn["inheritedTestament"]`
  from the existing `testament[-TESTAMENT_PROMPT_ENTRIES:]` slice, per §2
  Answer 3).
- `simulation/sim_engine/mixin_persistence.py` — `restore_state()`
  `a.setdefault("children", [])` (and `a.setdefault("inheritedTestament", [])`
  if that field also needs restore-time back-compat — confirm during Phase 1)
  back-compat (see §7 migration).
- `simulation/sim_engine/mixin_snapshot.py` — `/state` per-agent field exposure
  (agents are already serialized wholesale via `_serialize_state`/snapshot; a new
  agent dict key needs no special snapshot-layer work beyond confirming it isn't
  filtered out — verify by reading `mixin_snapshot.py`'s agent projection before
  assuming it "just works").
- `simulation/index.html` — new `.gbtn lineage` button in `#divineBar`, new
  `#divineTab-lineage` panel markup, per §2 Answer 5.
- `simulation/viewer/divine-bootstrap.js`, `simulation/viewer/divine-modal.js`
  — `DIVINE_FEATURES.lineage` registry entry, `"lineage"` in `GOD_TABS`, tab
  open/close reparent wiring, following the `audit` tab pattern exactly.
- `simulation/css/divine.css` — lineage panel styling, in the same family as
  the existing `.divine-*`/tab rules.

**Out of scope:**
- Any change to `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/
  `apply_decision`/`available_actions`/`ACTION_LABELS` — see §6.
- Any change to `_inherit_from`'s resource/home-split logic
  (`mixin_lifecycle.py:155-196`) beyond what §2 Answer 2's `_heirs_of` rewire
  requires — the goods/home inheritance mechanics themselves are unchanged.
- Any change to `_push_testament_entry`/`_merge_testament_on_death`'s
  village-wide testament ring mechanics — `inheritedTestament` (§2 Answer 3) is
  an additive, read-only snapshot copy, not a rework of the shared ring.
- The full live bloodline-vs-conversation belief diff (§2 Answer 4) — out of
  scope per the user's explicit answer.
- Any new accordion/collapsible-panel CSS component — §2 Answer 5 reuses the
  existing Divine Console modal-tab pattern instead of inventing one.
- `simulation/viewer/sidebar.js` — the lineage panel is Divine Console
  surface (§2 Answer 5), not a sidebar section.
- `simulation/roles.json`.

---

## 6. Action-sync checklist

**N/A — no new action.** This plan is pure engine-state (birth-time linkage
recording) plus a read-only viewer panel. No agent ever chooses a "record
lineage" action; it is derived automatically inside the existing, unconditional
`_spawn_newborn` birth path. No `DECISION_ACTIONS`/`DECISION_SCHEMA`/
`SYSTEM_PROMPT`/`apply_decision`/`available_actions`/`ACTION_LABELS` change.

---

## 7. Feature flag — and the save/restore migration concern (mandatory per plan brief)

**`DYNASTY_TREE_ENABLED`** (`simulation/sim_engine/constants.py`), plain
module-level boolean — **default `True`** per user decision.

**Migration is the central risk in this plan and must be handled explicitly, not
implicitly.** New agent state (`children`) is written only at the moment of a new
birth (`_spawn_newborn`). Every agent that already exists in **any save made
before this change lands** — including every agent alive today in the live
world's `state.db` — has no `children` key at all. Three places must agree or a
restored old save breaks:

1. **`restore_state()` back-compat** (`simulation/sim_engine/mixin_persistence.py`,
   inside the existing `if LIFECYCLE_ENABLED:` block that already runs
   `a.setdefault("parents", None)` at line ~540): add
   `a.setdefault("children", [])` in the same block, same discipline. This is
   the load-bearing fix — every other consumer below depends on `children` never
   being absent/`None` on a restored agent.
2. **Cold-start default** (`simulation/sim_engine/core.py:365`, next to
   `a["parents"] = None`): new agents built via `_make_agents` (both cold-start
   roster and `_next_agent_slot`-generated agents) must also get
   `a["children"] = []` so a fresh world and a restored world converge on the
   same shape before any birth ever happens.
3. **Every reader must tolerate absence defensively even after (1)/(2) land**,
   because a save written by an OLDER worktree/branch during a mixed-deployment
   window (e.g. gate 2 Docker rebuild sequencing across multiple plans per the
   meta-plan's per-plan-worktree model) could still be read by code with this
   change applied before a full restart cycle has passed through every save
   path. `_heirs_of()` and the viewer projection must use `agent.get("children")
   or []`, never bare `agent["children"]`, matching this codebase's existing
   `a.get("beliefs") or set()` / `.get("resources", {})` defensive-read style
   seen throughout `mixin_lifecycle.py`.

Gate 1 for this plan MUST include an explicit test: restore a save (or a
minimally-constructed in-memory agent dict) that has `parents` but no `children`
key, and confirm `restore_state()` (or the equivalent construction path) does not
raise and produces `children == []`. It must also include the §2 Answer 2
regression check: on a fixture with several living/dead agents and multi-child
parents, the rewired `_heirs_of()` returns the identical heir set the
pre-change `parents`-scanning implementation would have returned.

- **Gate 1 (on-state testing).** Run with the flag at its default (`True`);
  trigger a real birth (`_maybe_birth`/`_spawn_newborn`, needs housing headroom +
  food surplus + an ally pair per `mixin_lifecycle.py:1123-1141` — likely driven
  directly in a smoke rather than waited for live) and confirm both parents'
  `children` arrays gained the newborn's name.
- **Kill switch.** Flip `DYNASTY_TREE_ENABLED = False` in
  `simulation/sim_engine/constants.py` and restart. No env override by default
  (consistent with most flags in the `specs/01-architecture.md` index).
  **Note:** because `parents`/`children` recording is folded into the existing
  unconditional `LIFECYCLE_ENABLED`-gated birth path, the new flag likely gates
  only the **viewer panel and the `_heirs_of` children-array read** (§2 Answer
  2), not whether `children` gets written at all — the write should probably
  stay unconditional (same as `parents` today, which is not separately flagged
  from `LIFECYCLE_ENABLED`). This scoping detail (whether the flag gates the
  write too, or only the read/viewer) is an implementation detail left to
  Phase 1 to resolve and document in `specs/01-architecture.md`'s flag index —
  it does not change the user's Q2 answer (rewire is in scope) and does not
  need separate user confirmation.
- **Viewer echo.** `/state` `config.flags.DYNASTY_TREE_ENABLED`.

---

## 8. Phases

### Phase 0 — Spec update (SDD: specs first)
**Goal:** land owning-spec changes from §4. §2 Answer 3's testament-linkage
research question was already resolved during this plan's reconciliation pass
(see §2 Answer 3 for the finding and citations) — Phase 0 documents that
resolved data shape (`inheritedTestament`), it does not re-investigate it.
**In scope:** `specs/06-agents.md`, `specs/02-engine-core.md`,
`specs/01-architecture.md`, `specs/09-systems-society.md`.
**Out of scope:** `simulation/` code, `specs/11-viewer.md` (Phase 3).
**Acceptance:** every cited field/function in §2/§4 verified by reading the
file (spot-check, since the reconciliation pass already did the primary
read); spec sections match §2's answers exactly, no invented data shapes.
**Implementer prompt (copy-paste):**
> Implement Phase 0 of `docs/plans/idea-02-dynasty-tree/plan.md`: update
> `specs/06-agents.md`, `specs/02-engine-core.md`, `specs/09-systems-society.md`,
> and `specs/01-architecture.md` per this plan's §4/§7, grounded exactly in §2's
> answers — in particular §2 Answer 3's already-resolved finding that no
> traceable per-heir testament link exists today and the new
> `inheritedTestament` field design (snapshot copy of
> `testament[-TESTAMENT_PROMPT_ENTRIES:]` entries, captured inside
> `_seed_newborn_wiki_from_testament`,
> `simulation/sim_engine/mixin_governance_culture.py:1331-1366`). Do not
> re-investigate or redesign §2 Answer 3 — cite it. Do not write `simulation/`
> code in this phase.

### Phase 1 — Engine: `children` field, `_heirs_of()` rewire, testament + belief snapshots, migration
**Goal:** add `children` at birth and cold-start, rewire `_heirs_of()` to read
it (§2 Answer 2), add the `inheritedTestament` snapshot (§2 Answer 3) and the
static birth-time belief-inheritance list (§2 Answer 4), all with full
restore-time back-compat (§7).
**In scope:** `simulation/sim_engine/constants.py`, `core.py`,
`mixin_lifecycle.py`, `mixin_governance_culture.py` (only
`_seed_newborn_wiki_from_testament`, for `inheritedTestament`),
`mixin_persistence.py`.
**Out of scope:** viewer (Phase 3); no other change to
`_push_testament_entry`/`_merge_testament_on_death`'s shared ring mechanics.
**Acceptance:** new birth appends to both parents' `children`; a restored save
missing `children` does not raise and back-fills `[]`; **`_heirs_of()`
regression check**: on a fixture with several living/dead agents and
multi-child parents, the rewired implementation (reading `children`) returns
the identical heir set the pre-change `parents`-scanning implementation
returns — same living-agent filtering, same membership; newborn's
`inheritedTestament` and static belief-inheritance list are populated at
birth from the exact sources cited in §2 Answers 3–4.
**Implementer prompt (copy-paste):**
> Implement Phase 1 of `docs/plans/idea-02-dynasty-tree/plan.md`: add
> `DYNASTY_TREE_ENABLED` to `simulation/sim_engine/constants.py` (default
> `True`); add `agent["children"] = []` at cold-start (`core.py:365`, next to
> `a["parents"] = None`) and append newborn names to both parents' `children`
> lists in `_spawn_newborn` (`mixin_lifecycle.py:1191`); add
> `a.setdefault("children", [])` in `restore_state()`'s existing
> `LIFECYCLE_ENABLED` back-compat block (`mixin_persistence.py`, ~line 540).
> Rewire `_heirs_of()` (`mixin_lifecycle.py:144-153`) to read
> `agent.get("children") or []` (filtered to living agents) instead of
> scanning `parents`, per §2 Answer 2 — and write a regression test/fixture
> proving the rewired result is identical to the old scan on the same data,
> per this phase's Acceptance. Add `newborn["inheritedTestament"]` inside
> `_seed_newborn_wiki_from_testament`
> (`simulation/sim_engine/mixin_governance_culture.py:1331-1366`) as a
> snapshot copy of the same `testament[-TESTAMENT_PROMPT_ENTRIES:]` entries
> already sliced there (line 1359), per §2 Answer 3. Add the static
> birth-time belief-inheritance list in `_spawn_newborn`, computed from the
> existing belief union at line 1198, per §2 Answer 4 — no live diff logic.
> Verify old-save restore explicitly per §7's mandated test.

### Phase 2 — Engine: lineage projection (`/state`)
**Goal:** expose lineage data the viewer needs without inventing a new endpoint
if `/state`'s existing agent serialization already carries `children`/
`parents`/`inheritedTestament`/the belief-inheritance list (verify first).
**In scope:** `simulation/sim_engine/mixin_snapshot.py` only if the existing
agent projection filters fields.
**Out of scope:** viewer rendering (Phase 3).
**Acceptance:** `/state` response includes `parents`/`children`/
`inheritedTestament`/the belief-inheritance list per agent, or a documented
reason why a dedicated projection is needed instead.
**Implementer prompt (copy-paste):**
> Implement Phase 2 of `docs/plans/idea-02-dynasty-tree/plan.md`: read
> `simulation/sim_engine/mixin_snapshot.py`'s agent serialization path and
> confirm whether `parents`/`children`/`inheritedTestament`/the
> belief-inheritance list (all from Phase 1) already pass through to `/state`
> unfiltered. If they do, no code change is needed here — document that in
> `specs/06-agents.md`/`specs/04-http-api.md` and report it. If agent fields are
> filtered/allowlisted somewhere, add the new fields (and confirm `parents` is
> already there) to that allowlist.

### Phase 3 — Viewer: Divine Lineage tab
**Goal:** render family lineage as a new `.gbtn lineage` button in `#divineBar`
opening a new `#divineTab-lineage` panel inside `#divineModal`, following the
existing idea-10 Audit-tab pattern exactly, per §2 Answer 5.
**In scope:** `simulation/index.html` (button + panel markup),
`simulation/viewer/divine-bootstrap.js` (`DIVINE_FEATURES.lineage`, `GOD_TABS`
entry), `simulation/viewer/divine-modal.js` (open/close reparent wiring),
`simulation/css/divine.css` (panel styling).
**Out of scope:** engine/server code (Phases 1-2); `simulation/viewer/sidebar.js`
(the lineage panel is Divine Console surface, not sidebar); the full
belief-bloodline diff (§2 Answer 4, out of scope); any new accordion/
collapsible-panel CSS component (§2 Answer 5 reuses the modal-tab pattern).
**Acceptance:** the new `#godLineageTabBtn`-style button appears in `#divineBar`
after the existing buttons (placement mirrors `audit`'s "after History / before
Compile" precedent unless a more natural slot is confirmed by reading the
current bar order at implementation time); selecting an agent's lineage shows
parents and children by name (dead or alive, since corpses stay in
`self.agents`), the `inheritedTestament` snapshot, and the static birth-time
belief-inheritance list; same unlock gate as other `locked-dependent` tabs
(`godEffectivelyAuthorized()`); pure renderer, no client-side simulation
logic, per `specs/11-viewer.md`.
**Implementer prompt (copy-paste):**
> Implement Phase 3 of `docs/plans/idea-02-dynasty-tree/plan.md`: add a new
> Divine Lineage tab following the existing idea-10 Audit-tab pattern exactly
> (`specs/11-viewer.md:353-388`, `#godAuditTabBtn`/`#divineTab-audit` in
> `simulation/index.html`, `viewer/divine-bootstrap.js`,
> `viewer/divine-modal.js`) — a new `.gbtn lineage` button in `#divineBar`
> opening a new `#divineTab-lineage` panel inside `#divineModal`, with the
> same `locked-dependent`/`godEffectivelyAuthorized()` unlock gate and the
> same reparent-on-open/reparent-back-on-close mechanic as `audit`. Render
> `parents`/`children`/`inheritedTestament`/the static birth-time
> belief-inheritance list from the `/state` fields exposed in Phase 2,
> following the pure-renderer contract in `specs/11-viewer.md`. Do not invent
> a new collapsible-panel CSS component — reuse the modal-tab pattern. Update
> `specs/11-viewer.md`'s Divine Console tab inventory in the same change.

---

## 9. AI assistant / model table

| Phase | Role | Claude Code model | Cursor model |
|---|---|---|---|
| 0 — Plan authoring & orchestration | Orchestrator | Opus 5 (the session holding the plan) | Same model as the session holding the plan |
| 1 — Spec update (SDD: specs first) | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 2 — Engine / server implementation | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 3 — Viewer implementation | Implementer | Sonnet 5 | Composer 2.5 **only** |
| 4 — Review (accuracy, plan fit, SDD sync, security) | Reviewer | Sonnet 5 | Composer 2.5 Fast |
| 5 — Gate 1 verification, host (smokes, native server run, log inspection) | Deterministic | No model — `scripts/` smokes + JSONL inspection | No model — same |
| 6 — Gate 2 verification, container (image build, Docker run, delivery) | Deterministic | No model — Docker build/run + JSONL inspection | No model — same |

Notes:
- Phases 2 and 3 are separate implementer dispatches even when small; the reviewer runs
  after each, per the AGENTS.md loop.
- A plan whose idea touches no viewer surface omits phase 3 and says so explicitly.
  (This plan does not omit it — Phase 3 above is required.)
- The orchestrator never edits repo files. Only implementers write.

---

## 10. Worktree lifecycle + gate 1 (host)

**Host-first rule.** All editing happens in the worktree **on the host**, and the
**first gate of tests runs on the host** — never in a container. Docker is a second
gate only (§12). An implementer that cannot pass the host gate does not proceed to a
container.

```bash
git worktree add ../gitserv-idea-02-dynasty-tree -b idea-02-dynasty-tree main
```

Rules:
- Branch and worktree directory names both use the plan's folder slug.
- Worktrees live **outside** the repo directory (sibling paths), so they are never
  picked up by globs, Docker build context, or `.gitignore` handling.
- All `git` commands (worktree create, commit, branch, remove) run on the **host**, not
  inside the container — a worktree's `.git` is a file pointing back at the main repo's
  object store, which is not mounted into the container.
- The worktree is removed only after the user approves the implementation:
  ```bash
  git worktree remove ../gitserv-idea-02-dynasty-tree
  ```
- Never delete a worktree with uncommitted work without asking the user first.

### Gate 1 — host (mandatory, runs first)

```bash
uv sync
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
```

Plus a targeted smoke covering birth/lineage (no existing `scripts/` smoke covers
this surface directly — the implementer should check `scripts/` for a lifecycle or
succession smoke to extend, e.g. one covering `_agent_dies`/`_heirs_of`, before
writing a new one from scratch), the mandated old-save-migration test from §7, and
a native server run:

```bash
uv run python simulation/server.py   # http://127.0.0.1:5001
```

Verify via the browser and `simulation/logs/<timestamp>/`, then stop the native
server and confirm §13 before moving to gate 2. A failure at gate 1 goes back to
the implementer; it never gets "retried in Docker."

Before trusting any of the above, confirm storage isolation (§11) — this
worktree's own `state.db`/`logs/`/`memory_store.json`, never main's.

---

## 11. Storage isolation

This worktree owns its own `simulation/state.db`, `simulation/memory_store.json`,
and `simulation/logs/` — never the main repo's copies (meta-plan §6).

- **Isolation is automatic for native host runs.** `DB_PATH`
  (`sim_engine/persistence.py`) and the `SessionLogger`/`MemoryStore` paths
  built in `server.py` all resolve via `os.path.abspath(__file__)` against
  files inside *this worktree's* `simulation/` directory — not the shell's
  current working directory. Running `uv run python simulation/server.py`
  from inside this worktree cannot reach main's `state.db`, `logs/`, or
  `memory_store.json`, regardless of where the shell was `cd`'d from.
- This worktree starts with **none** of `state.db`, `memory_store.json`, or
  `logs/` — all three are gitignored, so a fresh worktree checkout is a
  clean world. If this plan needs a populated world to test against, build
  it inside this worktree; never copy main's files in — except the
  deliberate, one-time migration fixture below, which is a copy, not a
  live link to main.
- SQLite WAL sidecars (`state.db-wal`/`state.db-shm`), if they appear,
  follow the same rule and stay inside this worktree.
- Docker bind mounts (§12) point at THIS worktree's files only — the
  `%CD%`-relative volume paths in the `docker run` command resolve against
  `-WorkingDirectory <worktree path>`, never the main repo. The gate-2
  migration test below mounts a **copy** of a pre-change `state.db` placed
  inside this worktree — it is never a bind mount of main's actual
  `simulation/state.db` file.
- **Verify before trusting any gate-1 result:** start the native server and
  read its own startup lines — `[server] Logging session to: <dir>` and
  `[server] MemoryStore ... from <path>` — and confirm both paths are under
  this worktree, not the main repo. Or run, from inside the worktree:
  ```bash
  uv run python -c "from simulation.sim_engine.persistence import DB_PATH; print(DB_PATH)"
  ```
  and confirm the printed path is this worktree's `simulation/state.db`.
- Teardown (§14): removing this worktree removes its `state.db`, `logs/`,
  and `memory_store.json` with it — that is intended. Copy out anything
  worth keeping first.

---

## 12. Docker container lifecycle + gate 2

**Role of Docker, narrowed.** Containers are **not** the editing or first-test
surface. They exist for exactly two purposes:
1. **Gate 2** — re-run the plan's changes in the packaged container to confirm they
   hold under the supported Docker path (bind mounts, `host.docker.internal`
   Ollama, image build).
2. **Delivering the code changes** for the plan once gate 2 passes.

### Create

```bash
docker build -t gitserv-idea-02 ../gitserv-idea-02-dynasty-tree
```

Pre-create bind-mount targets before the first run — an empty `simulation/state.db`
**file**, `simulation/memory_store.json` containing `{}`, and a `simulation/logs/`
**directory** — on Docker Desktop for Windows a missing mount path is created as a
*directory*, which corrupts SQLite/JSON mounts. Do not mount `state.db-wal` /
`state.db-shm` unless those already exist as files.

```powershell
Start-Process cmd.exe -ArgumentList '/k', 'title simserver-idea-02 && docker run --name gitserv-idea-02 -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-idea-02' -WorkingDirectory <worktree path>
```

Foreground container, no `-d`, no `--restart`.

**Gate 2 must specifically test the migration path** (§7): mount a copy of the
live/current `state.db` (predating this plan's changes, so it has `parents` but
no `children`) into the container and confirm `restore_state()` succeeds and
back-fills `children` correctly — this is the surface a host-only gate 1 using a
fresh cold-start world could miss if the fixture wasn't deliberately old-shaped.

### Constraints while running
- **Port is always 5001.** At most one container running a server at any time.
- Ollama stays **host-native** at `localhost:11434`; the container reaches it via
  `SIM_OLLAMA_HOST=host.docker.internal:11434`.
- Git operations are host-side only.
- Nothing is edited in the container. Any fix discovered at gate 2 is made in the
  worktree on the host, re-run through gate 1, and only then rebuilt here.

### Destroy — on user approval
```bash
docker stop gitserv-idea-02
docker rm gitserv-idea-02
docker image rm gitserv-idea-02
```
Then remove the worktree. Teardown is explicitly gated on user approval — never on
"the tests passed," and never before the user has seen the result. If the user
rejects or defers, the container and worktree stay up.

---

## 13. Single-server-instance verification — mandatory block

Copied into every plan and run as the **last step of any task that starts, restarts, or
touches the server**, and again **before** starting a plan's container:

```powershell
docker ps -a --filter name=gitserv
Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation.server' }
```

Expected: at most one running container **and** zero conflicting native servers, or zero
containers and exactly one native server. Anything else must be resolved before
reporting done.

- Stop a stray container: `docker stop <name>` then `docker rm <name>`.
- Kill extra native servers (Bash: `pgrep -fa "simulation/server.py"`); close the
  `simserver` window.
- **`uv run` shows a wrapper + interpreter pair — that parent/child pair is ONE native
  instance, not a duplicate.** Verify `ParentProcessId` and port binding before killing
  anything.

A plan may not be reported complete while more than one server instance is live.

---

## 14. Acceptance checks

- [x] All five §2 questions, including Answer 3's testament-linkage research
      finding (no traceable per-heir link existed; new `inheritedTestament`
      field designed) and Answer 5's Divine-bar identity verification, are
      resolved — this reconciliation pass. No implementer beyond Phase 0
      needed further user input before dispatch.
- [ ] Storage isolation (§11) verified before trusting any gate-1 result:
      the native run's `state.db`/`logs/`/`memory_store.json` resolve inside
      this worktree, not the main repo.
- [ ] `specs/06-agents.md`, `specs/02-engine-core.md`, `specs/01-architecture.md`,
      and `specs/09-systems-society.md` (for `inheritedTestament`, §2 Answer 3)
      updated in the same change as behavior code.
- [ ] `DYNASTY_TREE_ENABLED` defaults `True`, echoed in `/state` `config.flags`,
      documented kill switch (§7).
- [ ] **Old-save migration explicitly verified**: a save/agent dict with `parents`
      but no `children` restores without raising and back-fills `children = []`
      (§7's mandated test, exercised at both gate 1 and gate 2).
- [ ] `_heirs_of()` regression check (§2 Answer 2): the rewired implementation
      (reading `children`) produces identical heir sets to the pre-change
      `parents`-scanning implementation on the same fixture — same living-agent
      filtering, same membership, not merely "the new field reads correctly."
- [ ] Gate 1 passes: `scripts/sid_parity_smoke.py`, `scripts/path1_smoke.py`,
      the targeted birth/lineage smoke, native server run.
- [ ] No `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/`apply_decision`/
      `available_actions`/`ACTION_LABELS` changes.
- [ ] Divine Lineage tab (`.gbtn lineage` in `#divineBar`, `#divineTab-lineage`
      in `#divineModal`, per §2 Answer 5) renders read-only, pure-renderer per
      `specs/11-viewer.md`.
- [ ] Gate 2 (Docker) passes, including the migration test against a real
      pre-change `state.db`, before delivery.
- [ ] §13 single-instance check passes as the last step.

---

## 15. Teardown

Per §12 "Destroy — on user approval": the container is stopped/removed and the
worktree removed only after the user approves this plan's implementation — never
automatically on tests passing, and never before the user has seen the result. If
the user rejects or defers, the container and worktree stay up until a decision is
made.
