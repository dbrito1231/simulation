# Plan: Civ-1 — Advanced Civilization Mechanics

**Status:** COMPLETE (Phases 1–4)
**Created:** 2026-07-15
**Owner branch:** `feat/civ-advancement` (branch from `main` when work starts)

## Why

Review of the running instance (2026-07-15) showed the village "advances" only by
sprawl and stockpile inflation: 1,049 boats exist as counters with no embodiment or
purpose, fire-adjacent structures (kiln, hearth, lighthouse) cannot affect the
mechanical night-exposure system, the level-100 Mega Library's knowledge effect is a
boolean that ignores upgrades, and production has no sinks (12k dried fish, 10k
planks). Root cause: the blueprint invention vocabulary is closed and tiny —
`FUNCTION_EFFECT_KEYS = ("produces", "boosts", "unlocks", "stores", "houses")`,
`VALID_BOOST_KINDS = {"gather", "craft"}` (server.py ~716). Agents invent new *names*
but every invention compiles to a resource dispenser.

This plan opens that vocabulary with new deterministic effect kinds and wires them
into systems that already exist (night pressure, settlements/caravans, library
knowledge, upgrade weights), so advancement becomes visible and mechanical.

## Ground rules

- **SDD contract:** every phase edits the owning spec(s) FIRST, then code, in the same
  change. Spec ownership is listed per phase.
- **Model policy (CLAUDE.md):** the initiating session is orchestrator-only. All
  implementation is dispatched to subagents via the Agent tool. Model per phase is
  listed below; default is the `implementer` subagent type (pinned to Sonnet 5).
- **Action-sync invariant:** any new/changed action or payload field must stay in sync
  across DECISION_ACTIONS/DECISION_SCHEMA/SYSTEM_PROMPT (server.py), apply_decision +
  available_actions (sim_engine.py), ACTION_LABELS (index.html).
- **Feature flags:** each phase ships behind a new module-level flag in sim_engine.py,
  echoed via `/state config.flags`, and added to the flag index in specs/01.
- **Verification:** no test suite — extend `scripts/sid_parity_smoke.py` /
  `scripts/path1_smoke.py` with deterministic checks per phase, then a live server run
  watching activity.jsonl + the viewer.

---

## Phase 1 — Environmental effect kinds: `shelter` and `light` (fire matters)

**Goal:** invented structures can shelter agents and push back the night.
Night pressure is already mechanical (`_tick_night_pressure`, `NIGHT_EXPOSURE_DAMAGE=2`,
sim_engine.py ~4327) but only `type == "house"` counts as shelter and nothing emits light.

**Flag:** `ENV_EFFECTS_ENABLED` (default on).

**Completion:** Landed in the current worktree and documented in specs/07,
specs/08, specs/10, specs/11, and the architecture flag index. The validator
accepts `shelter`, `light`, and `upkeep` effects; working shelter effects add
night capacity; fueled district-scoped lights exempt unsheltered agents from
night exposure; upkeep is charged once per in-world day; existing `hearth` and
`lighthouse` registry entries are migrated with light/upkeep effects; `/state`
exposes `litDistricts` and per-structure light state; and the viewer renders a
warm night glow. `scripts/path1_smoke.py` covers fueled/unfueled light behavior,
upkeep deduction, and flag-off behavior.

Steps:
1. Specs first: 07-actions (function-block schema gains `shelter` + `light` effect
   kinds and an optional `upkeep` field), 08-systems-economy (night shelter counts any
   working structure with a `shelter` effect; `light` semantics: district-scoped,
   negates/halves night exposure damage while fueled), 01-architecture (flag index).
2. server.py: extend `validate_function_block` — `shelter: {capacity: 1-4}`,
   `light: {scope: "district"}`, `upkeep: {resource, amount, every_ticks}` (resource
   must exist; caps mirror produce caps). Update SYSTEM_PROMPT blueprint examples.
3. sim_engine.py: `_night_shelter_capacity()` replaces the hardcoded house check
   (houses keep working via an implicit shelter effect in SEED_STRUCTURE_FUNCTIONS);
   `_tick_night_pressure` consults per-district light (fueled structures with a
   `light` effect consume upkeep from district stock/stockpile at nightfall; unfueled
   = dark). Seed the existing `hearth`/`lighthouse` types with a light function via
   the projectRegistry migration in `restore_state()`.
4. Viewer: index.html/sprites.js draw a warm glow radius around lit structures during
   night rendering (spec 11).
5. Smoke: extend path1_smoke — build a light structure + fuel, advance to night,
   assert reduced exposure damage and upkeep deduction; assert dark when fuel absent.

**Model:** `implementer` (Sonnet 5) — 2 dispatches (engine+server; viewer). Orchestrator reviews diffs against specs.

**Acceptance:** at night the viewer shows glow around fueled light structures; agents
inside a lit district take no exposure damage; charcoal stock visibly drains nightly;
smoke passes with flag on and off.

---

## Phase 2 — Library scaling + knowledge-to-cognition feedback (smarter agents)

**Goal:** the Mega Library actually does more than a level-1 library, and preserved
knowledge reaches the LLM prompt — the only "smarter" this architecture can deliver.

**Flag:** `LIBRARY_SCALING_ENABLED` (default on).

Steps:
1. Specs first: 09-systems-society (library scaling formula + prompt injection),
   03-cognition (payload addition + token budget note), 01 (flag index).
2. sim_engine.py: scale `LIBRARY_KNOWLEDGE_CAP` and `LIBRARY_STUDY_GAIN` by
   `_structure_upgrade_weight()` of the best working library; cap effective upgrade
   levels where a function stops scaling and stop advertising maxed structures in
   `_upgradeable_structures_brief()` (ends the level-100 resource dump).
3. `_build_think_payload()`: for agents in a district with a working library, inject a
   compact `library_lessons` block — top 3 `libraryKnowledge` entries + last 2
   chronicle lines. Hard cap ~120 tokens; verify against the ~3,400-token/slot budget
   (specs/03) with `scripts/lms_load.py` context math.
4. Smoke: sid_parity_smoke — upgraded library preserves more entries and teaches
   faster than level 1; payload contains `library_lessons` only when a working library
   is present.

**Model:** `implementer` (Sonnet 5) — 1 dispatch. Prompt-shape review (step 3 wording)
stays with the orchestrator since prompt regressions are the main risk.

**Acceptance:** lm_studio.jsonl shows `library_lessons` in prompts near the library;
study events log larger gains at high level; upgrade spam on maxed structures stops.

---

## Phase 3 — Transit unlock + boat embodiment (boats exist and matter)

**Goal:** boats gain a sink and a purpose (ocean caravans between settlements), and
physical resources render as visible props.

**Flag:** `TRANSIT_ENABLED` (default on; requires `PATH1` diplomacy systems).

Steps:
1. Specs first: 10-path1 (transit unlock kind, ocean caravan routing, boat
   consumption), 07-actions (function schema: `{"kind":"transit","terrain":"ocean",
   "consumes":{...}}` unlock), 05-world + 11-viewer (prop rendering), 01 (flag index).
2. server.py: `validate_function_block` accepts the `transit` unlock kind (terrain
   whitelist: `ocean`; consumes must reference existing resources).
3. sim_engine.py: caravan goal generation (`_maybe_caravan_goal`, ~4256) may route via
   ocean when a working transit structure exists, consuming its `consumes` cost per
   trip from stockpile; ocean-adjacent gather zones for registry resources with
   `gatherZone: "ocean"` require a working transit structure (gives the dock/shipyard
   cluster a function). Seed `dock`/`shipyard` types with a transit function via
   registry migration.
4. Viewer prop rendering: when a district's stock of a flagged "physical" resource
   (start: `boat`) exceeds a threshold, sprites.js draws up to 3 prop sprites along
   the adjacent shoreline. `/state` already ships `districtStocks`; add a small
   `physicalProps` hint in the state payload (spec 04 + 11).
5. Smoke: path1_smoke — ocean caravan fires only with transit structure + boat stock,
   decrements boats; gather of ocean-zone custom resources gated on transit.

**Model:** `implementer` (Sonnet 5) — 2 dispatches (engine/server; viewer/props).
Sprite/prop drawing may go to a lower tier (Haiku 4.5) if split out — it is
self-contained sprites.js work.

**Acceptance:** boat counter decreases over time; activity.jsonl shows ocean caravan
events; boats visibly drawn at the shore in the viewer.

---

## Phase 4 — Economy sinks + tiered construction (numbers circulate)

**Goal:** production meets consumption so stockpiles stop inflating; higher tech eras
demand crafted goods.

**Flag:** `ECONOMY_SINKS_ENABLED` (default on).

Steps:
1. Specs first: 08-systems-economy (repair/build material tiers, upkeep generalized),
   09-systems-society (era transitions gate on new effect kinds in use), 01 (flags).
2. sim_engine.py: structure repair and tier-2+/era-2+ construction consume crafted
   goods (planks/bricks/tools) before raw wood/stone; generalize Phase 1's `upkeep`
   to any function block; population consumes crafted comfort goods (pottery,
   dried_fish preference) when available, with a small hunger/health bonus.
3. Rebalance pass: pick consumption rates so current saturated stockpiles (10k planks,
   12k dried fish) drain over ~2-3 real-time days rather than instantly; document the
   arithmetic in spec 08.
4. Smoke: sid_parity_smoke — repair draws planks when present; era transition requires
   a lit district + transit structure (proves the new kinds are load-bearing).

**Model:** `implementer` (Sonnet 5) — 1 dispatch. Rebalance-rate review by the
orchestrator against a soak run (`scripts/path1_soak` pattern) before merging.

**Acceptance:** stockpile trend lines flatten/decline for crafted goods over a soak;
era advances only via the new mechanics; no starvation regressions (watch
`nightSheltered`/`populationFloorHeld` benchmarks).

---

## Implementation completion

Phases 2–4 landed on 2026-07-15. Phase 2 adds upgrade-weighted local Library
capacity/study and bounded prompt lessons. Phase 3 adds validated abstract
ocean transit, boat consumption, migration support, ocean-gather gating, and
server-derived boat props. Phase 4 adds plank-preferred repairs, slow comfort
consumption, and the monotonic Civic Era gate requiring working light plus
transit. `sid_parity_smoke.py` and `path1_smoke.py` cover the new behavior.

## Sequencing & dependencies

```
Phase 1 (env effects)  ──┐
Phase 2 (library)        ├──> Phase 4 (sinks — reuses Phase 1 upkeep, Phase 3 transit costs)
Phase 3 (transit/boats) ─┘
```

Phases 1–3 are independent of each other and can be built in any order (1 recommended
first: smallest, most visible). Phase 4 depends on 1 and 3.

## Model assignment summary

| Work | Model |
|---|---|
| Orchestration, spec review, prompt wording, rebalance signoff | Initiating session (any tier — orchestrator only, writes no impl code) |
| All engine/server/spec implementation dispatches | `implementer` subagent — Sonnet 5 |
| Self-contained viewer/sprite prop drawing (Phase 3 step 4) | Haiku 4.5 acceptable |
| In-sim cognition at runtime | LM Studio local model (unchanged; only prompt/payload shape changes, Phase 2) |

## Out of scope (explicitly deferred)

- Boardable vehicle entities / agent ocean pathing — cosmetic gain over Phase 3, high
  risk to tick loop and thin-viewer contract.
- New tech tiers beyond `MAX_TECH_TIER = 3` — revisit after Phase 4 era gating proves out.
- Any new LLM-side action verbs — all phases reuse existing actions; only function-block
  vocabulary and deterministic systems change (no action-sync churn except prompt text).
