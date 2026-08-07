# Phase 5 — Weather → Ecology → Governance Feedback

**Parent:** [plan-living-ecosystem-0-master.md](plan-living-ecosystem-0-master.md)
**Status:** Planned — not executing.
**Solutions covered:** #6 (weather consequences feeding into governance).
**Cost:** Low in isolation — but **hard-depends on [Phase 4](plan-living-ecosystem-4-weather.md)**.
Without weather state this phase has nothing to react to.
**Touches:** `simulation/sim_engine.py`, `specs/05`, `specs/09`.

**Why this phase matters:** it is the one item in the batch that makes the
"living" layer *matter* rather than decorate. Reviewing this world's council
transcripts showed villagers debating the same resource shortages for hours; this
gives those debates a real, externally-caused shock to respond to — and closes the
loop between ambience and the LLM-cognition layer that is the point of the project.

## Problem

Weather (Phase 4) as scoped is **pure decoration plus a relocated damage roll**. It
does not touch the resource economy, so the councils — which already deliberate
almost exclusively about resource scarcity — have no reason to acknowledge it.

Meanwhile two systems sit adjacent and unconnected:
- **Ecology regrowth already responds to environment.** `SEASON_REGROW_MULT`
  (winter = 0) multiplies `STOCK_REGROW_PER_TICK = 1`, and the tick already
  narrates scarcity/recovery to the activity log (sim_engine.py ~2750).
- **The governance backstop already auto-proposes resource rules.**
  `_maybe_advance_rules` proposes a priority rule for the scarcest unmet resource —
  and, as of commit `6a78162`, it actually *works* (it was permanently broken by
  the blocked-id bug; it now cycles propose→enact→repeal reliably).

## Deliverables

### 5A — Weather modulates ecology regrowth (`WEATHER_GOVERNANCE_ENABLED`)
Extend the regrowth multiplier chain that `SEASON_REGROW_MULT` already
establishes — **do not add a second, parallel mechanism**:

- **Storm / drought suppresses** regrowth in affected districts (multiplier < 1).
- **Optionally, rain boosts** it (multiplier > 1) so weather is not purely punitive
  and the village experiences good years as well as bad.
- Reuse the existing scarcity narration (*"…stock is growing again"* /
  *"has regrown to fair levels"*) so the shock is legible in the activity log
  without new logging code.
- If Phase 4 shipped **world-wide** rather than per-district storms, apply the
  multiplier globally and say so in the spec.

### 5B — Weather in the think payload
Add a short weather/scarcity line to the prompt context so agents can actually
reference conditions in council. `_build_think_payload` already assembles
`chronicle_line`, council digests, and similar one-line summaries — follow that
exact pattern.

> **Cost discipline:** this must be **one short line**, not a new context section.
> Prompts already run ~3,400 tokens against `MAX_CONCURRENT_LLM = 3` and
> `num_ctx` budgeting ([specs/03](../specs/03-cognition.md)); a verbose weather
> block would squeeze real cognition. **No new LLM calls** — this rides the
> existing think cycle.

### 5C — Emergency governance response (deterministic)
When a storm drives a district's ecology below the existing `STOCK_LOW_RATIO`
threshold, let the already-working rule backstop propose an appropriate emergency
rule — rationing, or a repair/gather priority.

- **Reuse the existing kinds.** `RULE_KINDS` already includes `rationing` and
  `harvest_quota` under `LIFECYCLE_ENABLED`, plus `priority`. Do **not** invent a
  new rule kind.
- **Mint unique per-enactment ids** via the existing `_next_rule_seq_token(counter_key)`
  helper. This is mandatory: reusing a deterministic id would recreate exactly the
  permanently-blocked-id loop fixed in `6a78162` (rule ids are globally
  non-reusable — see `_ensure_constitution`). A new counter field must be
  restore-safe (`setdefault`).
- **Respect the cooldowns.** Honor `RULE_PROPOSE_COOLDOWN` and the
  `lastRuleAttemptFrame` guard, and pre-validate before proposing — same discipline
  as the priority/tax branches. An emergency must not become a proposal flood.
- Emit a chronicle milestone for an enacted emergency measure so the village's
  response to a disaster becomes part of its recorded history.

## Files & changes

| File | Change |
|---|---|
| `sim_engine.py` | Weather term in the ecology regrowth multiplier; one-line weather/scarcity entry in `_build_think_payload`; emergency-rule branch in `_maybe_advance_rules` reusing `_next_rule_seq_token` + existing cooldowns; `WEATHER_GOVERNANCE_ENABLED` echoed in `config.flags`. |
| `specs/05-world.md` | Document the weather term in the regrowth chain alongside `SEASON_REGROW_MULT`. |
| `specs/09-systems-society.md` | Document the emergency-rule branch, which existing kinds it uses, its unique-id requirement, and its cooldown behavior — extend the existing unique-instance-id section rather than duplicating it. |
| `specs/03-cognition.md` | Note the added prompt line (it changes payload content). |
| `specs/01-architecture.md` | Flag index +1. |

Uses only existing rule kinds and actions → **action-sync invariant N/A** (verify:
if any new action is added, the full sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/
`SYSTEM_PROMPT`/`apply_decision`/`available_actions`/`ACTION_LABELS` becomes mandatory).

## Risks / notes
- **Rule-loop regression is the top risk.** This phase adds a *third* auto-proposal
  branch to `_maybe_advance_rules`, the function that just caused a 6,399-occurrence
  infinite loop. Every lesson from that fix applies: unique ids, cooldown honored on
  failure, pre-validate, never emit a known-invalid action. Re-read
  [plan-rule-loop-fix.md](plan-rule-loop-fix.md) before touching it.
- **Difficulty balance.** Suppressed regrowth plus storm structure damage could
  compound into an unrecoverable spiral (starvation → collapse). Bound the
  suppression and its duration; consider a floor so a district can always recover.
  Watch hunger/health in a long soak.
- **Governance churn.** The backstop already cycles priority rules every ~5-6
  minutes. Adding emergency proposals could crowd the pending queue
  (`MAX_PENDING_RULES = 4`). Verify emergencies do not starve ordinary governance.
- **Prompt bloat** — see 5B. One line.

## Verification
- Force a storm → confirm the affected district's regrowth slows, scarcity
  narration fires, and (below threshold) an emergency rule is proposed **with a
  unique id** and actually enacts.
- Confirm `"drafted an invalid rule"` stays at **zero** — the regression signal
  from the rule-loop fix.
- Long soak with the flag on: confirm no starvation spiral and no pending-queue
  starvation; compare hunger/health trends against a flag-off run.
- Confirm the prompt gains exactly one line (diff a captured `llm.jsonl` request).
- Toggle `WEATHER_GOVERNANCE_ENABLED` off → regrowth, prompts, and governance
  behave exactly as Phase 4 alone.
- Deterministic smokes + single-instance server check last.
