Read docs/civilization-emergence-plan.md (Phase E scope in Part 4, Part 8 hard rules) and CLAUDE.md. Implement Phase E — market, property & mechanical relationships — behind ECONOMY_ENABLED in simulation/sim_engine.py.

GIT RULES: feat/server-authoritative-engine only; no worktrees, no branches. Commit when verified.

SCOPE:
1. PRICES: a `market` seed structure (function block: unlocks trade pricing). While one exists, per-resource prices derive deterministically from district stocks + stockpile levels (scarce = expensive; sizing math in comments). Prices appear in prompts as one compact line ("Prices: wood 3g, food 1g, ...") only when a market exists.
2. GOLD AS MEDIUM: trade_resource becomes a priced exchange — the target receives gold at the current price (or refuses, below). Gold stops being just a gatherable line item. Keep barter fallback when no market exists (current behavior).
3. RELATIONSHIPS GET TEETH (audit item C2): trade terms condition on relationship — allies trade at a discount, rivals refuse or surcharge (deterministic modifiers in the trade path, logged: "Rex refused to trade with his rival Colt"). This is the first mechanical consumer of the relationships field.
4. PROPERTY: agents can claim a built house as home (first-come on build/repair; stored on the structure). Homeowners get the shelter-need benefit (Phase C) automatically; claims logged; a `homeless` nudge points agents to build/claim. Inheritance recording only (consumed by Phase F).
5. WEALTH BENCHMARK: per-agent gold+goods valuation at prices → wealth Gini in benchmarks.jsonl.

CHANGE MAP HINTS: trade path is apply_decision "trade_resource" (sim_engine ~2540); prices as a pure function consulted at query time (no new tick); market seed joins PROJECT_TEMPLATES + SEED_STRUCTURE_FUNCTIONS; prompt line joins _build_think_payload; refusals surface via the standard rejection-nudge pattern (lastTradeRejection).

HARD RULES: flags; no per-tick LLM calls; ≤200 prompt tokens; no silent rejections (refusals are IN-WORLD events with reasons, not swallowed decisions); deterministic escapes (a rival refusal must leave another path: market price purchase or another partner); state.json back-compat; observability in-commit (trade/refusal/claim events + wealth_gini metric); ECONOMY_ENABLED=False = current behavior.

SMOKE TEST BEFORE COMMIT: force it — set two agents rival, verify refusal + surcharge paths; deplete a stock and verify its price rises in the prompt line; claim a home and verify the shelter benefit; check wealth_gini appears and moves.

RECORD: Phase E implementation log in Part 4; CLAUDE.md bullet. py_compile. Commit.
