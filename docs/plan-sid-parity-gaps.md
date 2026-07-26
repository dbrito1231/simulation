# Project Sid parity gaps — status (updated 2026-07-25)

Original plan proposed 2026-07-22 (six phases). This file is now a status
record, not a forward plan — five of the six original phases shipped; the
sixth is an explicit non-goal. Superseded by
[docs/plan-always-on-modules.md](plan-always-on-modules.md) for the one gap
that survived: continuous (non-turn-gated) module cognition.

## Original gaps — resolution status

| # | Gap | 2026-07-22 state | 2026-07-25 state |
| --- | --- | --- | --- |
| 1 | PIANO concurrent modules | `PIANO_MODULES = False`, experimental | **Done.** `PIANO_MODULES = True` (sim_engine.py:182), default-on with an independent `piano_workers` pool. Deeper sub-gap (modules run continuously, not per-turn) tried and rolled back — see below. |
| 2 | Meta/autobiography system | `META_SYSTEM = False` | **Done.** `META_SYSTEM = True` (sim_engine.py:187). |
| 3 | Emergent role specialization | 12 fixed roles only | **Done.** `EMERGENT_ROLES = True`; `propose_role`/`approve_role`/`reject_role` live, `MAX_EMERGENT_ROLES = 8` cap on top of the seed 12. |
| 4 | LLM-driven culture/religion | Deterministic proximity diffusion, 2 hardcoded memes | **Done.** `MEMES_ENABLED = True`; `found_belief` + persuasion-based `talk_to_nearby` pitches, `run_belief_pitch` model-judged quality — agents author and persuade, not roll dice. |
| 5 | Open-ended governance | `RULE_KINDS` closed set, `custom` had no mechanical effect | **Done.** Effect grammar (`{subject, condition, modifier}`) gives `custom` rules real teeth; `civilization["constitution"]` aggregates enacted rules. |
| 6 | Currency / minted money | Barter or scarcity-priced trade only | **Done.** `ECONOMY_ENABLED = True`; `coin` mintable via a treasury/mint structure unlock, priced trades settle in coin, `resource_tax` collects it, wealth benchmark includes coin balances. |
| 7 | Scale (Sid ran ~500 agents) | 8–12, clamped to `AGENT_DEFS` | **Partially done, remains a non-goal at Sid's scale.** `MAX_ROSTER_SIZE = 20` (sim_engine.py:1375) via procedural generation past the 12 hand-written defs (`_generated_agent_defs`, the "newcomer backstop"). [specs/00-overview.md](../specs/00-overview.md) still declares 500-agent parity out of scope; 20 is the current headroom ceiling, not a step toward it. |

## The gap that resolution #1 didn't fully close

Sid's modules run continuously against shared agent state; ours ran (and
still run, by default) as a **per-decision-turn fan-out** the decision
briefly waits on (`PIANO_MODULE_TIMEOUT_WAIT_S = 18`), with a slow module's
work dropped rather than reused. This week's [cross-module-visibility
work](plan-tasks-pending-rollout.md) closed the *information-sharing* half
of the gap (modules now see each other's recent notes with age labels,
persisted across restarts); [plan-always-on-modules.md](plan-always-on-modules.md)
attempted the *scheduling* half — refresh notes on the world's clock instead
of the decision's, so nothing is ever dropped, only stale.

**Result: implemented, gated dark, Phase B gate failed twice, rolled back.**
Full record in [ollama_config.md](../ollama_config.md) ("Always-on PIANO
Phase B gate") and [TASKS_PENDING.md](../TASKS_PENDING.md) item 4. Root
cause: on a single RTX 3060, no refresh-batch size satisfies both the
decision-latency gate and the note-freshness gate simultaneously — batch 2
kept notes fresh (median well under budget) but taxed decision p50 by
+17.7%; batch 1 fixed latency (+13.5%, passing) but starved refresh
throughput so badly that notes sat 10–17 minutes stale. The scheduler code
is intact and correct (verified by code review, both smokes pass) — it
stays behind `ALWAYS_ON_MODULES = False` pending either a second GPU or a
materially smaller/faster fast model than `llama3.2:3b`.

## What this leaves as genuinely open

With items 1–6 done and item 7 a deliberate non-goal, the only architectural
gap with Project Sid still open is **continuous vs. turn-gated module
cognition** — and it is now a *hardware* gap, not a *design* gap. The design
was validated: build/no-op the machinery correctly, gate it behind a flag,
measure honestly, roll back on failure without degrading the sim. Revisit
when the fast-model side of the two-model split has meaningfully more
headroom (second GPU, or a sub-1B model with acceptable module quality).
