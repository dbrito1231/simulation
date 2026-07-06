Read docs/civilization-emergence-plan.md (Phase F scope in Part 4, Part 8 hard rules) and CLAUDE.md. Implement Phase F — population lifecycle & governance depth — behind LIFECYCLE_ENABLED in simulation/sim_engine.py.

GIT RULES: feat/server-authoritative-engine only; no worktrees, no branches. Commit when verified.

SCOPE:
1. AGING: agents gain `age` (start staggered), advancing on a very slow tick. Life stages (young/adult/elder-age) shown in prompts as part of identity, one word.
2. BIRTH: when housing headroom + food surplus + two adult agents with ally relationship share a district, a newborn joins (reuses the existing newcomer machinery + AGENT_DEFS-style slot OR a generated villager if the roster is full of retirees). Persona/name authored by ONE lm_complete call at birth (the only LLM involvement — an event, not a tick). Child inherits: home claim (Phase E), a share of goods, parents' beliefs (memes), and starts low-skill.
3. NATURAL DEATH: at high age, an agent passes away (deterministic curve, never mid-emergency): logged as a village event, memorial memory pushed to all agents, goods/home flow to heirs (Phase E inheritance records). THE ELDER CAN DIE.
4. SUCCESSION: on the elder's death, an election runs on the existing rules/vote machinery (candidates = eligible adults; villagers vote via the existing vote flow; deterministic tally; new elder gets the leader role). Log the whole arc — this is the headline emergence moment.
5. NEW RULE KINDS with teeth (audit/plan I4): `harvest_quota` (caps per-agent gathers per period in a district — enforced in _perform_gather with surfaced reasons), `rationing` (stockpile withdrawals limited when storage low). Both proposable/votable through the existing scaffold.

SAFETY RAILS: population floor (never below 4 adults — births pause aging deaths if at floor... i.e., death defers while below floor, logged); Sage-priority emergency logic must handle elder death gracefully (emergency clears, succession starts); no permanent softlocks — every role must remain coverable (EMERGENT_ROLES reassignment already exists).

CHANGE MAP HINTS: agent dict init (sim_engine _make_agents ~560) + restore setdefaults (~3900); slow ticks join _tick_once gates; newcomer machinery `_maybe_welcome_newcomer` (~2530) is the birth template; election reuses _maybe_advance_rules voting; quota checks join _perform_gather (~1730).

HARD RULES: flags; ONE LLM call per birth/succession event, none per tick; ≤200 prompt tokens; no silent rejections (quota/ration refusals surfaced); deterministic escapes (quotas expire, rationing lifts when storage recovers, succession cannot stall — tie breaks deterministically); state.json back-compat (old saves: everyone gets an age); observability (birth/death/election events + population/median-age benchmark); LIFECYCLE_ENABLED=False = current behavior.

SMOKE TEST BEFORE COMMIT: force it — age an agent to death and verify inheritance + memorial; kill the elder (set age) and verify the election completes and a new elder leads (assign_task works for them); enact a quota and verify gather refusal with reason; verify population floor holds.

RECORD: Phase F implementation log in Part 4; CLAUDE.md bullet (update the Sage-priority section — the elder is now mortal and succession is the design). py_compile. Commit.
