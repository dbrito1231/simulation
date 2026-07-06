Read docs/civilization-emergence-plan.md (Phase G scope in Part 4, Part 8 hard rules) and CLAUDE.md. Implement Phase G — knowledge, culture, factions & diplomacy — behind CULTURE_ENABLED and DIPLOMACY_ENABLED in simulation/sim_engine.py. This is the last phase; prefer depth on 1-3 (culture) and treat 4-5 (diplomacy) as its own flag that can ship separately if the slot runs long.

GIT RULES: feat/server-authoritative-engine only; no worktrees, no branches. Commit when verified.

SCOPE (CULTURE_ENABLED):
1. SKILLS BY PRACTICE: per-agent skill levels (gather/craft/build/heal) that rise with successful use (deterministic), give small yield bonuses, and appear in prompts as one compact line. Teaching: a talk_to_nearby message matching a teach intent (deterministic keyword check, no extra LLM call) transfers a skill fraction — apprenticeship. Skills are what Phase F children lack and inherit slowly.
2. LIBRARY: a seed structure whose function stores skill knowledge — while built, dead agents' top skills remain learnable (children/newcomers study there via a goal), making death matter without erasing progress.
3. CHRONICLE & MEMES: an event chronicle (village-level ring of major events: era transitions, deaths, elections, disasters, famines — already logged; now STORED in civilization state and summarized into prompts as "Village history: ..." one line, rotating). Meme mutation: on spread, small chance a belief text mutates via ONE lm_complete call (event-driven, capped per session); beliefs influence a deterministic bias (e.g., harvest-spirit believers contribute food more readily).
4. PERSONALITY DRIFT: major life events (collapse, election won/lost, bereavement) append a trait to the agent's persona (deterministic templates; persona already flows into prompts).

SCOPE (DIPLOMACY_ENABLED — separable):
5. SECOND SETTLEMENT: when population + wealth thresholds hold, a founding party claims a distant frontier plot as a NEW named village (district group with its own stockpile and elder-elect via Phase F machinery). Inter-village: a trade-caravan goal (cart/wagon required — Phase C/D payoff), treaty/rivalry state between villages driven by trade balance, surfaced in prompts of border agents only (token budget!).

CHANGE MAP HINTS: skills on the agent dict + restore setdefaults; teach detection in the talk path (apply_decision ~2540); chronicle as a capped list on civilization + one prompt line in _build_think_payload; settlement founding reuses _found_district + _maybe_welcome_newcomer patterns; caravan is a goal (stepGoal family), not a new tick loop.

HARD RULES: flags (two, separable); LLM calls only on events (meme mutation, settlement naming), never per tick; ≤200 prompt tokens TOTAL across both flags — compact aggressively; no silent rejections; deterministic escapes (a failed settlement dissolves back, treaties decay to neutral); state.json back-compat; observability (skill/teach/chronicle/settlement events + benchmarks: skill_spread, chronicle_size, settlements); flags off = current behavior.

SMOKE TEST BEFORE COMMIT: force it — practice a skill and verify the bonus + prompt line; teach between two agents; kill a skilled agent with a library built and verify knowledge survives; force the settlement threshold and verify founding + a caravan round trip; verify prompt token growth stays in budget (measure a real prompt).

RECORD: Phase G implementation log in Part 4; CLAUDE.md bullets. py_compile. Commit.
