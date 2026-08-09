# D10 — Rules & Beliefs Scoping Audit (Feature 4 Schism prep)

**Branch:** `emergence-breakthroughs`  
**Date:** 2026-08-09  
**Scope:** Read-only inventory of every read/write of `civilization["rules"]`, related governance state, and belief state across `simulation/sim_engine/`, `simulation/_server/`, and `simulation/server.py`. No behavior changes.

---

## Executive summary

Today **all governance law is civilization-global**: one `rules` list, one `pendingRules` queue, one `constitution`, one compiled `customRuleModifiers` map, and global side-effect maps (`harvestQuotas`, `rationingActive`, `taxDue`/`taxPaid`). **Beliefs are split**: per-agent `beliefs` sets already exist, but the **canonical definitions** (`beliefRegistry`, `memeTexts`) and session counters (`beliefPitchCalls`, `memeMutations`) are civilization-global.

Settlements (`settlements`, `settlementStores`, `district.settlementId`, `_settlement_for_agent`) are already per-settlement for Path 1 economy, but **rules and belief registry lookups never consult settlement id**.

**Top migration risks for F4:**

1. **`restore_state()`** — constitution backfill, `ruleKindsEverEnacted` backfill, `_rebuild_custom_rule_modifiers()`, and `beliefRegistry` setdefault all assume a single global ruleset; a naive schema change will break old `state.db` loads.
2. **Voting quorum** — `_vote_quorum()` counts all living agents world-wide; a secession cluster would still vote on (and be counted toward) the parent settlement's ballots unless scoped.
3. **Mechanical enforcement** — `_active_resource_tax`, `_active_priority_resource`, `_custom_rule_modifier`, harvest/rationing gates read global compiled state; agents in settlement B would still obey settlement A's enacted laws.
4. **Single elder** — succession (`pendingSuccession`, `role == "elder"`) is one global office; schism's "elect their own elder" requires per-settlement (or per-faction) leadership, not just a forked rules list.
5. **Treaty layer** — `_enacted_treaty_tariff()` merges `treaties` **and** enacted `kind: treaty` rules from global `rules`; inter-settlement tariffs may need to stay cross-settlement even when domestic rules fork.

---

## 1. Inventory — rules & related governance state

Legend: **R** = read, **W** = write, **RW** = both.

### 1.1 Core rules list (`civilization["rules"]`)

| File | Function | R/W | Global assumption notes |
|------|----------|-----|-------------------------|
| `sim_engine/core.py` | `SimEngine.__init__` (civ init) | W | Initializes `"rules": []` on fresh world. |
| `sim_engine/mixin_crafting_rules.py` | `_validate_rule` | R | Checks id uniqueness against `rules`, `pendingRules`, and `constitution`; `len(rules) >= MAX_ACTIVE_RULES`. |
| `sim_engine/mixin_crafting_rules.py` | `_tally_and_maybe_enact` | RW | On enact: append to `rules`, handle supersede removal, call `_apply_governance_rule`. |
| `sim_engine/mixin_crafting_rules.py` | `_enact_repeal` | RW | Removes target from `rules`; clears side-effect maps. |
| `sim_engine/mixin_crafting_rules.py` | `_rebuild_custom_rule_modifiers` | R | Iterates all enacted `kind: custom` rules to rebuild global `customRuleModifiers`. |
| `sim_engine/mixin_crafting_rules.py` | `_ensure_constitution` | R | Backfills constitution provisions from every active `rules` entry. |
| `sim_engine/mixin_crafting_rules.py` | `_active_priority_resource` | R | Newest enacted `priority` rule in global `rules`. |
| `sim_engine/mixin_crafting_rules.py` | `_active_resource_tax` | R | First enacted `resource_tax` in global `rules`. |
| `sim_engine/mixin_crafting_rules.py` | `_active_or_pending_rationing` | R | Scans global `rules` and `pendingRules`. |
| `sim_engine/mixin_crafting_rules.py` | `_propose_repeal` | R | Finds repeal target in `rules`. |
| `sim_engine/mixin_diplomacy.py` | `_enacted_treaty_tariff` | R | Max tariff from `treaties` **and** enacted `kind: treaty` rows in `rules`. |
| `sim_engine/mixin_council_growth.py` | `_daily_council_agenda` | R | Agenda topic `"rules"` lists first 8 global rule names. |
| `sim_engine/mixin_council_growth.py` | `_maybe_advance_rules` | R | Checks `len(rules)`, non-tax repeal eligibility, priority/tax branches. |
| `sim_engine/mixin_decisions.py` | `_sample_benchmarks` | R | `len(rules)` in `lastBenchmarks`. |
| `sim_engine/mixin_think_job.py` | `_build_think_payload` | R | Builds `active_rules` prompt slice from `c["rules"]`; nudge when no rules and no pending. |
| `sim_engine/mixin_snapshot.py` | `_build_civ_snapshot` | R | Exposes `rules` to `/state` (viewer sidebar). |
| `sim_engine/mixin_persistence.py` | `restore_state` | R | Backfills `ruleKindsEverEnacted` and `constitution` from `rules`. |
| `_server/prompt_format.py` | `format_active_rules` | R | Formats think-payload `active_rules` for `SYSTEM_PROMPT`. |
| `server.py` | `build_agent_data` → prompt format | R | Injects `Enacted rules: {active_rules}`. |

### 1.2 Pending rules (`civilization["pendingRules"]`)

| File | Function | R/W | Global assumption notes |
|------|----------|-----|-------------------------|
| `sim_engine/core.py` | civ init | W | `"pendingRules": []`. |
| `sim_engine/mixin_crafting_rules.py` | `_validate_rule` | R | Capacity / id collision checks. |
| `sim_engine/mixin_crafting_rules.py` | `_propose_rule` | W | Appends ballot; proposer auto-votes yes. |
| `sim_engine/mixin_crafting_rules.py` | `_propose_repeal` | W | Appends repeal ballot. |
| `sim_engine/mixin_crafting_rules.py` | `_vote_on_rule` | RW | Records vote; succession mutual-exclusion across siblings. |
| `sim_engine/mixin_crafting_rules.py` | `_tally_and_maybe_enact` | RW | Removes from pending on resolve. |
| `sim_engine/mixin_lifecycle.py` | `_start_succession_election`, `_enact_succession_winner`, `_maybe_resolve_stalled_succession` | RW | Succession ballots live in global `pendingRules`. |
| `sim_engine/mixin_diplomacy.py` | `_propose_treaty`, `_vote_treaty` | RW | Treaty ballots use same global queue (kind `treaty`). |
| `sim_engine/mixin_council_growth.py` | `_council_vote` | RW | Daily council rule ballots mirror votes into matching `pendingRules` entry. |
| `sim_engine/mixin_council_growth.py` | `_maybe_advance_rules` | R | Backstop votes on `pendingRules[0]` or succession subset. |
| `sim_engine/mixin_think_job.py` | `_build_think_payload`, think nudges | R | `pending_rules` for prompt; unvoted pending nudge; succession candidate list. |
| `sim_engine/mixin_snapshot.py` | `_build_civ_snapshot` | R | `/state` projection. |
| `sim_engine/mixin_decisions.py` | `apply_decision` dirty keys | W | Marks `pendingRules` dirty on governance actions. |
| `_server/prompt_format.py` | `format_pending_rules` | R | Prompt formatting. |
| `server.py` | prompt build | R | `Pending rules: {pending_rules}`. |

### 1.3 Constitution & compiled effects

| File | Function | R/W | Global assumption notes |
|------|----------|-----|-------------------------|
| `sim_engine/core.py` | civ init | W | `constitution`, `customRuleModifiers`. |
| `sim_engine/mixin_crafting_rules.py` | `_constitution_provision`, `_ensure_constitution`, `_set_constitution_status` | RW | Single global ledger; rule ids globally non-reusable. |
| `sim_engine/mixin_crafting_rules.py` | `_apply_governance_rule`, `_clear_governance_rule` | RW | Updates global `harvestQuotas`, `rationingActive`, `customRuleModifiers`. |
| `sim_engine/mixin_crafting_rules.py` | `_custom_rule_modifier` | R | Sums all compiled custom effects (no settlement filter). |
| `sim_engine/mixin_structures_economy.py` | `_collect_resource` path | R | Calls `_custom_rule_modifier("collect_resource", ...)`. |
| `sim_engine/mixin_world_state.py` | contribute path | R | `_custom_rule_modifier("contribute_resources", ...)`. |
| `sim_engine/mixin_crafting_rules.py` | `_craft_item` | R | `_custom_rule_modifier("craft_item", ...)`. |
| `sim_engine/mixin_god_validation.py` | `_god_custom_rule_gather_context` | R | Reads global `customRuleModifiers` for divine preview. |
| `sim_engine/mixin_persistence.py` | `restore_state` | RW | Constitution backfill from `rules`; then `_ensure_constitution` + `_rebuild_custom_rule_modifiers` on load. |
| `sim_engine/mixin_think_job.py` | `_build_think_payload` | R | `constitution` in think payload. |
| `sim_engine/mixin_snapshot.py` | `_build_civ_snapshot` | R | `/state` constitution panel. |
| `_server/prompt_format.py` | `format_constitution` | R | Prompt rendering. |

### 1.4 Rule side-effect & metric fields (derived from global rules)

| Field | Primary readers/writers | Global assumption |
|-------|-------------------------|-------------------|
| `harvestQuotas` | `_apply_governance_rule` (W), `_active_harvest_quota` / `_harvest_quota_gate` (R) in `mixin_governance_culture.py` | Village-wide gather caps. |
| `rationingActive` | `_apply_governance_rule` (W), `_rationing_active_cap` / `_rationing_gate` (R) in `mixin_governance_culture.py`; `_withdraw_from_stockpile` in `mixin_world_state.py` | Village-wide stockpile withdrawal cap when storage low. |
| `taxDue`, `taxPaid` | `_enforce_resource_tax` (W), `_rule_adherence` (R) in `mixin_decisions.py` | Single global adherence metric for resource_tax. |
| `ruleKindsEverEnacted` | `_record_rule_kind_enacted` (W), benchmarks (R), `restore_state` backfill (RW) | Session diversity metric; not settlement-scoped. |
| `lastRuleActivityFrame`, `lastRuleAttemptFrame`, `priorityRuleSeq`, `taxRuleSeq`, `emergencyRuleSeq` | `_propose_rule`, `_tally_and_maybe_enact`, `_maybe_advance_rules` | Global governance churn / auto-proposal cadence. |
| `pendingSuccession` | `mixin_lifecycle.py`, `mixin_council_growth.py`, think nudges | One election at a time for the whole civ. |
| `treaties` | `mixin_diplomacy.py` (`_vote_treaty` enact, caravan) | Inter-settlement; separate list but tariff also reads global `rules`. |

### 1.5 Decision / council / backstop entry points

| File | Function | R/W | Notes |
|------|----------|-----|-------|
| `sim_engine/mixin_decisions.py` | `apply_decision` | — | Dispatches `propose_rule`, `vote_rule`, `repeal_rule`, `found_belief`, `talk_to_nearby` (belief spread). |
| `sim_engine/mixin_crafting_rules.py` | `_propose_rule`, `_vote_on_rule`, `_propose_repeal` | RW | Agent-initiated governance. |
| `sim_engine/mixin_council_growth.py` | `_council_propose` (rule kind), `_council_vote`, `_resolve_daily_council_ballot`, `_maybe_advance_rules` | RW | Council can propose/vote rules into global queue; belief-biased backstop votes. |
| `sim_engine/mixin_governance_culture.py` | `_belief_biased_vote` | R | Called from `_maybe_advance_rules` backstop only. |
| `sim_engine/mixin_diplomacy.py` | `_propose_treaty`, `_vote_treaty` | RW | Reuses global `pendingRules` / enact path. |
| `server.py` | `DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT`, `prompts.py` | R | Prompt describes village-wide rules; belief_pitch / found_belief schemas. |
| `server.py` | `score_belief_pitch_decision`, `run_belief_pitch` | R | Scores pitch; does not mutate belief state (engine applies adoption). |

---

## 2. Inventory — belief state

### 2.1 Civilization-level belief fields

| Field | File(s) | R/W | Global assumption notes |
|-------|---------|-----|-------------------------|
| `beliefRegistry` | `mixin_governance_culture.py` (`_belief_registry`, `_found_belief`, `_belief_entry`, …) | RW | Single registry for all belief definitions; `MAX_BELIEFS` is civ-wide. |
| `memeTexts` | `mixin_governance_culture.py` (`_belief_text`, `_maybe_mutate_meme`), `mixin_world_state.py` (`_god_belief_plant`) | RW | Global text overrides per belief id. |
| `memeMutations` | `mixin_governance_culture.py` (`_maybe_mutate_meme`) | RW | Session cap counter (civ-wide). |
| `beliefPitchCalls` | `mixin_governance_culture.py` (`_maybe_spread_beliefs`), `mixin_think_job.py`, `mixin_decisions.py` (benchmarks), `server.py` (`score_belief_pitch_decision`) | RW | Session cap for LLM pitch scoring (civ-wide). |

### 2.2 Per-agent belief fields

| Field | File(s) | R/W | Notes |
|-------|---------|-----|-------|
| `agent["beliefs"]` | `set` on agent | RW | **Already per-agent.** Persisted in `state.db` per agent. |
| Adoption / spread | `mixin_governance_culture.py`: `_seed_beliefs`, `_found_belief`, `_adopt_belief`, `_maybe_spread_beliefs` | RW | Writes agent sets; reads global registry for validation. |
| Inheritance | `mixin_lifecycle.py`: `_inherit_from`, birth | W | Heirs union deceased beliefs; newborns union parents. |
| God mode | `mixin_world_state.py`: `_god_belief_plant`; `mixin_god_gate.py`: `_god_apply_belief_plant` | RW | Can create divine registry entries + adopt on one agent. |
| Mechanical bias | `mixin_governance_culture.py`: `_belief_favored_kinds`, `_belief_biased_vote`, contrib boost; `mixin_project_helpers.py`: `_belief_project_score` | R | Uses agent beliefs + **global** `_active_priority_resource()` / global registry. |
| Benchmarks | `mixin_governance_culture.py`: `_meme_adoption_counts`, `_meme_adoption_count`; `mixin_decisions.py`: `_sample_benchmarks` | R | Counts living agents vs global registry keys. |
| Prompts | `mixin_think_job.py`: `_build_think_payload` | R | `beliefs`, `belief_registry`, `nearby_beliefs`, `belief_pitch_budget_remaining`. |
| `server.py` | `build_agent_data`, `run_belief_pitch` | R | Formats beliefs for `SYSTEM_PROMPT`. |

### 2.3 Belief ↔ rules coupling (schism trigger surface)

| Mechanism | Location | Implication for F4 |
|-----------|----------|-------------------|
| `affinity` on registry entries | `_belief_favored_kinds`, `_belief_biased_vote` | Beliefs bias votes on **global** pending rules by `kind`. Schism trigger ("belief contradicts enacted rule") must compare agent beliefs against **their settlement's** enacted rules, not global. |
| `MEME_RULE_AFFINITY` seeds | `constants.py`, `_belief_registry` setdefault | Seed beliefs ship with rule-kind affinities globally defined. |
| Priority / tax bias in project choice | `_belief_project_score` + `_active_priority_resource` | Project scoring uses global priority rule. |

---

## 3. Persistence & restore paths

### 3.1 Write path (`state.db`)

```
save_state → _serialize_state (mixin_persistence.py)
  ├─ civilization: full dict copy (includes rules, pendingRules, constitution,
  │    customRuleModifiers, harvestQuotas, rationingActive, beliefRegistry,
  │    memeTexts, beliefPitchCalls, treaties, pendingSuccession, …)
  └─ agents[]: beliefs serialized as sorted list per agent
```

All governance and belief fields live in the **`civilization` blob** except per-agent `beliefs` (and god keys). No settlement id is stored on rules or beliefs today.

### 3.2 Read path (`restore_state`)

Critical steps touching rules/beliefs (in order):

1. **`civ.setdefault("ruleKindsEverEnacted")`** + backfill kinds from `civ["rules"]` (`mixin_persistence.py` ~241–247).
2. **Constitution migration** — append provisions for any `rules` id missing from `constitution` (~248–275).
3. **`civ.setdefault("customRuleModifiers", {})`** (~276).
4. **`CULTURE_ENABLED` block** — `memeTexts`, `beliefRegistry` seed merge, `beliefPitchCalls` (~365–382).
5. **Agent restore** — `a["beliefs"] = set(...)` (~448).
6. **Post-assign hooks** — `_ensure_constitution()`, `_rebuild_custom_rule_modifiers()` (~567–568).

**F4 requirement (from plan):** pre-scoping saves must restore into single-settlement-scoped rules with **no behavior change**. That implies a migration that wraps today's flat fields under a default settlement id (e.g. `"home"`) without altering effective lookups until schism occurs.

### 3.3 Runtime projections (non-persistent but consumer-facing)

| Path | Rules/beliefs exposed |
|------|----------------------|
| `_build_civ_snapshot` | `rules`, `pendingRules`, `constitution`, `beliefRegistry`, `beliefPitchCalls` |
| `_build_think_payload` | `active_rules`, `pending_rules`, `constitution`, full belief prompt bundle |
| `benchmarks.jsonl` via `_sample_benchmarks` | `rules` count, `rule_adherence`, `meme_adoption`, `belief_pitch_calls` |

Viewer (`viewer/sidebar.js`) reads global `civ.rules` / `pendingRules` — **out of scope for F4** per plan, but will show wrong data after schism until a later viewer phase.

---

## 4. Call sites assuming a single global ruleset / belief pool

### 4.1 Prompt & cognition (`server.py`, `_server/`, `mixin_think_job.py`)

- **"The village has no shared rules yet"** nudge (`mixin_think_job.py`) — global `rules` + `pendingRules`.
- **`Enacted rules` / `Pending rules` / `Constitution`** prompt lines — no settlement qualifier.
- **`belief_registry`** in think payload — entire civ registry shown to every agent.
- **`nearby_beliefs`** — adjacent agents only, but ids resolve through global registry.
- **`belief_pitch_budget_remaining`** — global session cap.

### 4.2 Voting & quorum

- **`_vote_quorum`** = `(active_agents // 2) + 1` over **all** agents (`mixin_crafting_rules.py`).
- **Daily council** (`mixin_council_growth.py`) — single elder head; rule ballots sync to global `pendingRules`; agenda lists global rules.
- **Succession** — global `pendingSuccession` + global elder role; `_enact_succession_winner` ensures at most one `role == "elder"`.

### 4.3 Mechanical enforcement (agents feel global law regardless of district/settlement)

- Resource tax on gather/contribute (`_enforce_resource_tax`, `_active_resource_tax`).
- Priority contribution bias (`_active_priority_resource`, `_preferred_contribution_resource`).
- Custom rule modifiers on collect/craft/contribute.
- Harvest quota & rationing gates (`mixin_governance_culture.py`).

### 4.4 Path 1 / diplomacy

- **`_enacted_treaty_tariff`** — intentionally cross-settlement; reads global `treaties` + treaty rows in global `rules`.
- **Treaty ballots** in global `pendingRules` — village-wide votes on inter-settlement pacts.
- **Caravan** uses tariff + settlement stores (already per-settlement for goods).

### 4.5 Memes & culture

- **`MAX_BELIEFS`** — civ-wide registry cap.
- **`_seed_beliefs`** — seeds competing memes on random living agents at culture start (not settlement-aware).
- **Mutation** (`_maybe_mutate_meme`) — mutates global registry `tenet` / `memeTexts` for all holders.
- **Benchmarks** — adoption rate vs entire living population.

### 4.6 Auto-governance backstop (`_maybe_advance_rules`)

- Proposes emergency rationing, priority rules, resource tax, repeals — all into **global** queues.
- Uses **`_belief_biased_vote`** for deterministic villager votes.
- Cooldown uses global `lastRuleActivityFrame`.

### 4.7 Smokes / scripts (verification debt for F4)

- `scripts/_sid_parity_smoke/governance.py` — mutates `engine.civilization["rules"]` directly.
- `scripts/daily_council_smoke.py` — asserts on global `rules`.
- Plan calls for new `scripts/schism_smoke.py`; existing `path1_smoke.py` and `sid_parity_smoke.py` must stay green after migration.

---

## 5. Recommended scoping strategy options (brief)

**Option A — Settlement-keyed maps (recommended baseline)**  
Store `rulesBySettlement`, `pendingRulesBySettlement`, `constitutionBySettlement`, `customRuleModifiersBySettlement`, and derived maps (`harvestQuotas`, etc.) keyed by settlement id. Add `_rules_for_agent(agent)` / `_beliefs_registry_for_agent(agent)` helpers that delegate via `_settlement_for_agent`. Keep **`treaties` + treaty tariffs** at civ level (or a dedicated `interSettlement` slice). On schism: clone parent's enacted rules + fork `beliefRegistry` subset for seceding cluster; migrate agents' settlement assignment.

**Option B — Lazy fork on schism only**  
Keep flat globals until schism fires; on secession, materialize per-settlement structures and re-point districts/agents. Smaller diff before schism, but higher burst complexity at fork time and harder to test multi-settlement divergence in soak.

**Option C — Faction overlay**  
Attach `factionId` to agents and rules instead of settlement id. Works if schism clusters are not 1:1 with settlements; adds another dimension and may fight Path 1's existing `settlementId` model.

**Beliefs recommendation:** Keep **per-agent `beliefs` sets** as-is; scope **registry + memeTexts + pitch/mutation counters** per settlement (or per faction). Cross-settlement pitch should still resolve ids against the **speaker's** registry to avoid ghost beliefs.

### 5.1 `state.db` migration risks

| Risk | Mitigation sketch |
|------|-------------------|
| Old saves lack settlement-keyed maps | On restore: if `rulesBySettlement` absent, set `rulesBySettlement["home"] = civ["rules"]` (and same for pending, constitution, compiled maps); keep flat keys as aliases during transition **or** remove after helper migration. |
| Rule ids globally unique today | Constitution comments require global id non-reuse; per-settlement scoping may allow same id in two settlements **or** require prefixed ids (`home/priority_wood`) — spec decision needed before implementation. |
| `beliefRegistry` shared across settlements | Migration: entire registry → `beliefRegistryBySettlement["home"]`; agents in other settlements at load time should all be `"home"` today. |
| Compiled `customRuleModifiers` stale after migration | Always call `_rebuild_custom_rule_modifiers()` per settlement bucket after restore (extend current single call). |
| Session counters (`beliefPitchCalls`, `memeMutations`) | Either stay global (KISS) or split per settlement (fairer); document choice in spec. |
| Single elder after load | Pre-schism saves: one elder remains valid. Post-schism: need `elderSettlementId` or multiple elders with scoped authority — **architectural decision beyond rules list fork**. |

---

## 6. Out of scope for Feature 4 (plan reminders)

Per `emergence-breakthroughs.md` §6 and owning specs draft:

- **War, raids, or inter-settlement violence**
- **Per-settlement currencies**
- **Forced reunification**
- **Viewer settlement-comparison panel** (sidebar will remain globally wrong until a later viewer phase)

In scope per plan: schism trigger, secession migration, forked ruleset, carried beliefs, per-settlement elder via succession reuse, `chronicle` kind `schism`, treaty/caravan/tariff continuity, `mixin_persistence.py` migration, smokes.

---

## 7. Suggested F4 implementation phases (orchestrator use only)

Not implementing here — ordering hint from audit:

1. **Spec + schema** — define settlement-scoped storage shape and migration contract in `specs/09-systems-society.md` (+ `05-world`, `10-path1` consequences).
2. **Helper layer** — `_rules_for_settlement`, `_registry_for_settlement`, thread through read paths in `mixin_crafting_rules.py` / `mixin_governance_culture.py` before any schism trigger.
3. **Persistence migration** — `restore_state` wrap + rebuild; acceptance: old saves behave identically with one settlement.
4. **Voting scope** — quorum and `pendingRules` partition by settlement (treaties excepted).
5. **Schism trigger + migration** — cluster detection, district/founding path, fork rules/beliefs, succession for new elder.
6. **Smokes** — `scripts/schism_smoke.py`; regression `path1_smoke.py`, `sid_parity_smoke.py`.

---

## 8. Files touched by F4 (from plan, confirmed by audit)

| File | Why |
|------|-----|
| `mixin_persistence.py` | **Critical** — migration + rebuild on restore |
| `mixin_crafting_rules.py` | All enact/repeal/vote/enforcement |
| `mixin_governance_culture.py` | Beliefs, biased votes, quota/rationing gates |
| `mixin_council_growth.py` | Council agenda, `_maybe_advance_rules`, council ballots |
| `mixin_lifecycle.py` | Succession ballots, belief inheritance |
| `mixin_diplomacy.py` | Treaties vs domestic rules boundary |
| `mixin_think_job.py` | Prompt payload scoping |
| `mixin_decisions.py` | Benchmarks, apply_decision dirty keys |
| `mixin_snapshot.py` | `/state` shape (viewer lag acceptable) |
| `mixin_world_state.py` | God belief plant, stockpile/rationing |
| `mixin_structures_economy.py` | Custom rule modifiers on collect |
| `mixin_project_helpers.py` | Belief-weighted project scoring |
| `core.py` / `constants.py` | Init defaults, flags |
| `server.py` / `_server/prompt_format.py` | Prompt wording (settlement-qualified rules text) |
| `specs/09-systems-society.md`, `05-world.md`, `10-path1.md` | SDD |

**Not required for minimal scoping:** viewer, divine UI (god belief plant should target settlement-aware registry), smokes in `scripts/_sid_parity_smoke/` (update when behavior splits).

---

*End of D10 audit. No runtime code was modified.*
