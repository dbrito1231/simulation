# Plan — Fix the elder's invalid-rule proposal loop

**Status:** Planned — not executing. Awaiting go-ahead.
**Severity:** Active defect. Permanently disables a governance mechanic and
spams the activity log ~10× faster than the intended cooldown.
**Touches:** `simulation/sim_engine.py`, `specs/09-systems-society.md`.

## Symptom

`"<elder> drafted an invalid rule"` dominates the activity log:
**102 occurrences in one recent session**, 6,399+ across retained sessions.

## Root cause (two independent defects, both confirmed in code + logs)

### Defect A — the auto-proposer generates a permanently-blocked rule id

`_maybe_advance_rules` (sim_engine.py:10007-10019) auto-proposes a priority rule
with the **deterministic id** `priority_{resource}`:

```python
unmet = self._first_unmet_resource_anywhere() or "wood"
"id": f"priority_{unmet}",
```

`_validate_rule` (sim_engine.py:5844) rejects any id already present in the
constitution ledger — **regardless of status**:

```python
if any(p.get("id") == rid for p in (c.get("constitution") or []) if isinstance(p, dict)):
    return False
```

This village already enacted and repealed `priority_iron_ore`, `priority_wood`,
and `priority_stone` (all `status: "repealed"`, still in the ledger). Those three
ids can therefore **never validate again**. Because
`_first_unmet_resource_anywhere()` falls back to `"wood"` when no project is
stalled, the elder deterministically re-proposes the one permanently-blocked id.

The branch stays eligible forever because its guard is
`self._active_resource_tax() > 0 and not self._active_priority_resource()` —
all priority rules were repealed, so `_active_priority_resource()` is `None`.

> **Critical constraint — do NOT "fix" this by relaxing the id check.**
> Globally non-reusable rule ids are a **deliberate, documented invariant**.
> `_ensure_constitution` (sim_engine.py:5781-5783) states *"Rule ids are globally
> non-reusable"* and dedupes the ledger using id as a unique key
> (`by_id = {p["id"]: p for p in cleaned}`). Allowing duplicate ids would collapse
> history rows and could resurrect a repealed provision as active. **The fix
> belongs on the id-generator side, not the validator side.**

### Defect B — failed proposals never advance the cooldown

`_propose_rule` (sim_engine.py:6043-6049) returns **before** recording activity;
`c["lastRuleActivityFrame"] = self.frameTick` is only reached on success
(sim_engine.py:6067). The cooldown guard in `_maybe_advance_rules`
(sim_engine.py:10000) therefore never advances after a failure, so the branch
re-fires on **every** `RULES_TICK_FRAMES` window.

`RULE_PROPOSE_COOLDOWN = 1500` frames (~50s intended) vs
`RULES_TICK_FRAMES = 150` (~5s) — a **10× amplification**.

**Empirical confirmation:** consecutive failures in the live session are spaced
at *exactly* 150 frames — `[150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150, 150]`
(≈7.6s wall-clock each at the observed tick rate). The cooldown is fully bypassed.

## Impact

- **Governance permanently broken for 3 resources.** The elder can never enact a
  priority rule for wood, stone, or iron ore again — exactly the bottleneck
  resources the villagers' councils keep complaining about.
- **A wasted deterministic decision every ~150 frames**, forever.
- **Log/JSONL noise** that masks real failures during debugging.

## Fix

### Fix 1 (required) — unique instance ids for auto-proposed priority rules
Generate a per-enactment unique id (e.g. `priority_{resource}_{frameTick}`, or a
monotonic counter) in the `_maybe_advance_rules` priority branch.

- Preserves the non-reusable-id invariant — each enactment is its own law instance
  (arguably a *more* accurate historical ledger).
- **Safe against existing consumers:** `_active_priority_resource`
  (sim_engine.py) matches on `kind == "priority"` and `value`, **not** on id — so
  unique ids do not break priority-rule lookup. Verify no other consumer keys off
  the literal `priority_{resource}` string.
- **No migration needed:** old blocked ids simply never recur.
- **Watch ledger growth:** the constitution list has no cap (unlike
  `MAX_ACTIVE_RULES = 8` / `MAX_PENDING_RULES = 4`). Repeated enact/repeal cycles
  with unique ids would grow it unboundedly over a long soak — add a cap/trim of
  historical provisions as part of this fix.

### Fix 2 (required) — stop the spam regardless of cause
Advance a cooldown timestamp on a **failed** attempt, so a rejected proposal
waits a full `RULE_PROPOSE_COOLDOWN` instead of retrying every tick window.

> **Design note:** do **not** simply set `lastRuleActivityFrame` on failure.
> That field is also read at sim_engine.py:11412 as a blueprint-stall backstop
> (`> BLUEPRINT_STALL_THRESHOLD`); overloading it with failed attempts would
> silently change blueprint-stall behavior. Prefer a **separate**
> `lastRuleAttemptFrame` (checked by the auto-proposer's cooldown guard) so
> "attempted" and "actual governance activity" stay distinct.

This also protects the LLM-driven `propose_rule` path from any future
colliding-id spam.

### Fix 3 (defensive, recommended) — pre-validate before auto-proposing
In the priority branch, skip to the next candidate resource (or skip the branch)
when the prospective rule would fail validation. Makes intent explicit and
prevents a deterministic backstop from ever knowingly emitting an invalid action.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | Unique id generation in the `_maybe_advance_rules` priority branch; new `lastRuleAttemptFrame` (init in the civilization dict ~line 1854 alongside `lastRuleActivityFrame`, honored by the cooldown guard at ~10000); optional pre-validate guard; constitution trim/cap. |
| `specs/09-systems-society.md` | Document unique priority-rule instance ids, the failed-attempt cooldown, and any constitution cap. Owning spec for governance/rules. |

No new actions, flags, or `/state` shape changes → **action-sync invariant N/A**.

## Verification

1. **Deterministic smokes (no Ollama):** `uv run python scripts/sid_parity_smoke.py`
   (rules are Sid-parity Phase 2) and `uv run python scripts/path1_smoke.py`.
2. **Cadence regression check** — the decisive test. After the fix, confirm
   consecutive `"drafted an invalid rule"` entries are **no longer 150 frames
   apart**; ideally the message disappears entirely:
   ```bash
   grep -c "drafted an invalid rule" simulation/logs/<newest>/activity.jsonl
   ```
3. **Mechanic actually unblocked:** confirm a priority rule for wood/stone/iron_ore
   reaches `enacted: true` in `/state` `civilization.rules` — the thing that has
   been impossible.
4. **No ledger regression:** `civilization.constitution` keeps one row per
   enactment, no duplicate ids, no repealed row flipped back to active.
5. **Single-instance server check** as the final step — and note that
   `uv run` legitimately shows **two** `python.exe` (wrapper → interpreter
   parent/child); verify `ParentProcessId`/port 5001 before killing anything.

## Notes
- Per [CLAUDE.md](../CLAUDE.md) model policy, implementation should be dispatched
  to an `implementer` subagent (Sonnet), not written by the orchestrator.
- Fixes 1 and 2 are independent: Fix 2 alone stops the log spam but leaves the
  mechanic broken; Fix 1 alone unblocks the mechanic but leaves the 10×
  retry amplification for any *other* validation failure. **Ship both.**
