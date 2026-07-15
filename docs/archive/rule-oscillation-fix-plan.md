> **Historical — implemented 2026-07-12, archived 2026-07-15.** Current behavior will be specified in specs/09-systems-society.md (spec rewrite in progress; see docs/spec-docs-rebuild.md).

# Fix: priority-rule propose/repeal oscillation

**Status: implemented 2026-07-12.** `enactedFrame` stamping, `RULE_REPEAL_MIN_AGE_FRAMES`, and the age-gated repeal branch are live in `simulation/sim_engine.py`; `scripts/sid_parity_smoke.py` covers it via `test_repeal_backstop_age_gate`. Live server restart still required to pick it up.

## Context

Found while verifying an unrelated fix (blueprint/invention pipeline) on a live 24/7 server session on 2026-07-12. The activity log showed a recurring pattern roughly every 90-110 seconds:

```
Sage proposed rule "Wood Priority"
Sage proposed repealing "Wood Priority"
Sage proposed rule "Rope Priority"
Sage proposed repealing "Rope Priority"
Sage proposed rule "Stone Priority"
Sage proposed repealing "Stone Priority"
...
```

This is **not** LLM flip-flopping and not related to the blueprint/sage-review feature. It's a fully deterministic backstop in the engine that proposes a priority rule and then immediately (next cooldown window) repeals it, forever, in steady state. It doesn't crash anything, but it's governance-system noise: the elder spends every rule-related decision window on this loop instead of anything else, and `c["rules"]`/`pendingRules` churn constantly for no benefit.

## Root cause

`_maybe_advance_rules()` in [simulation/sim_engine.py:7547](../simulation/sim_engine.py) runs whenever `RULES_ENABLED`, no rule vote is currently pending, and `RULE_PROPOSE_COOLDOWN` (`= 1500` frames, [sim_engine.py:384](../simulation/sim_engine.py)) has elapsed since the last rule activity. It has two branches that directly undo each other:

1. **Propose branch** ([sim_engine.py:7597-7611](../simulation/sim_engine.py)): if a resource tax is active (`_active_resource_tax() > 0`) and no priority rule is currently active (`not _active_priority_resource()`), deterministically `propose_rule` a new priority rule for whatever resource is currently scarcest (`_first_unmet_resource_anywhere()` or `"wood"`).
2. **Repeal branch** ([sim_engine.py:7612-7622](../simulation/sim_engine.py)): if `len(c["rules"]) >= 2` and there's at least one non-tax rule, deterministically `repeal_rule` the **oldest** non-tax rule — "to keep village law lean," per the comment, meant to occasionally exercise the repeal/amendment mechanic for the Sid-parity benchmark. No age check, no check of whether the condition that motivated the rule is still present.

Pending-rule votes are *also* resolved deterministically by the same function ([sim_engine.py:7571-7589](../simulation/sim_engine.py)) — an idle non-elder agent auto-votes yes/no — so both propose and repeal ballots enact/reject within one or two cooldown windows, fast enough to look like rapid oscillation in the activity log.

The cycle in steady state (tax rule already enacted, which is typical — it's usually the very first rule a village enacts):

1. No priority rule active → branch 1 fires → propose `"<resource> Priority"` → auto-voted → enacted. `c["rules"]` now has 2 entries (tax + priority).
2. Next cooldown window: `len(c["rules"]) >= 2 and non_tax` is now true → branch 2 fires → **repeals the priority rule that branch 1 just created**, with zero regard for how recently it was enacted.
3. Priority rule is gone → `_active_priority_resource()` is None again → next cooldown window, branch 1 fires again (possibly for a different resource, since scarcity shifts) → enact → repeal → ...

This repeats forever because tax + one priority rule is the *normal* steady state (not "several rules stacked" as the comment assumes), so branch 2's `len(c["rules"]) >= 2` condition is satisfied almost immediately after branch 1 ever succeeds once.

## Recommended fix

Give the repeal branch a **minimum-age gate** so it can't repeal something that was enacted too recently — this preserves the intended "occasionally exercise repeal" behavior (Sid-parity amendable-rules benchmark) while breaking the tight oscillation.

1. **Record enactment time.** Rule dicts currently have no timestamp field (`id, name, kind, value, description, proposedBy, enacted, votes` — see `_propose_rule`/`_propose_repeal` at [sim_engine.py:4926-4986](../simulation/sim_engine.py)). Add `"enactedFrame": self.frameTick` when a rule transitions to enacted, in `_tally_and_maybe_enact()` at the `c["rules"].append(rule)` site ([sim_engine.py:4857](../simulation/sim_engine.py)).

2. **Gate the repeal branch on age.** In `_maybe_advance_rules()`, change:
   ```python
   non_tax = [r for r in c["rules"] if r.get("kind") != "resource_tax"]
   if len(c["rules"]) >= 2 and non_tax:
   ```
   to also require the *oldest* non-tax rule to have been enacted at least some minimum number of frames ago — e.g. a new constant `RULE_REPEAL_MIN_AGE_FRAMES` (suggest a value a healthy multiple of `RULE_PROPOSE_COOLDOWN`, e.g. `RULE_PROPOSE_COOLDOWN * 4` = 6000 frames = ~3.3 min at 30fps, or tune to whatever cadence feels right — the key requirement is just "longer than one propose/repeal cooldown cycle" so a freshly-enacted rule survives at least a few cooldown windows before being eligible for the "exercise repeal" backstop):
   ```python
   non_tax = [r for r in c["rules"] if r.get("kind") != "resource_tax"]
   repeal_eligible = [r for r in non_tax
                      if self.frameTick - r.get("enactedFrame", 0) >= RULE_REPEAL_MIN_AGE_FRAMES]
   if len(c["rules"]) >= 2 and repeal_eligible:
       target = repeal_eligible[0]
       ...
   ```
   Old saves restored via `restore_state()` won't have `enactedFrame` on existing rules — `r.get("enactedFrame", 0)` naturally treats those as "very old" (age = current frameTick - 0, always past the threshold), which is the safe default (doesn't newly block repeal for pre-existing rules, just doesn't protect them either — acceptable since this is a minor one-time transitional gap, not a correctness issue).

3. **Add the new constant** near `RULE_PROPOSE_COOLDOWN` ([sim_engine.py:384](../simulation/sim_engine.py)).

This is a small, self-contained change: one new field written at one call site, one new constant, and a small tweak to one existing condition. No schema/API changes, no new decision actions, no prompt changes needed (the propose/repeal decisions are backstop-issued, not LLM-issued, so `SYSTEM_PROMPT` is unaffected).

### Alternative considered (not recommended)

Just raising the `len(c["rules"]) >= 2` threshold to `>= 3` would delay the oscillation but not fix it — once a third rule exists (e.g., a second priority rule for a different resource, or a harvest_quota/rationing rule under `LIFECYCLE_ENABLED`), the same tight propose→repeal loop would resume. It also doesn't address the actual bug (repealing something the instant it was created), just kicks it further out. The age-gate approach directly fixes the underlying issue.

## Files to change

- `simulation/sim_engine.py`:
  - `_tally_and_maybe_enact()` (~line 4857): stamp `enactedFrame` on enactment.
  - `_maybe_advance_rules()` (~lines 7612-7622): age-gate the repeal branch.
  - New constant `RULE_REPEAL_MIN_AGE_FRAMES` near `RULE_PROPOSE_COOLDOWN` (~line 384).
- `scripts/sid_parity_smoke.py`: extend `test_priority_and_repeal()` (~line 109) with a new assertion: enact a priority rule, advance `engine.frameTick` by less than `RULE_REPEAL_MIN_AGE_FRAMES`, call `engine._maybe_advance_rules()` directly (with a tax rule and 2+ total rules already present to satisfy the old `len(c["rules"]) >= 2` condition), and assert the priority rule is **not** repealed. Then advance past the threshold and assert it becomes eligible (either call `_maybe_advance_rules()` again and check a repeal was proposed, or just assert the rule is now in `repeal_eligible` via whatever helper/condition the implementation uses).

## Verification

```
uv run python scripts/sid_parity_smoke.py
uv run python scripts/blueprint_smoke.py
uv run python scripts/path1_smoke.py
uv run python -m py_compile simulation/server.py simulation/sim_engine.py
```

Live acceptance (server running): watch `simulation/logs/<session>/activity.jsonl` for rule-related events over several `RULE_PROPOSE_COOLDOWN` windows (~1500 frames = 50s each at 30fps) — a priority rule enacted by the backstop should persist for at least `RULE_REPEAL_MIN_AGE_FRAMES` before becoming eligible for the "keep law lean" repeal, instead of being undone on the very next cooldown window.
