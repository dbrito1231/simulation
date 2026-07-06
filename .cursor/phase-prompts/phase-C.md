Read docs/civilization-emergence-plan.md (Phase C scope in Part 4, Part 8 hard rules) and CLAUDE.md. Implement Phase C — physical goods, plural needs & consequence — behind a new GOODS_ENABLED flag in simulation/sim_engine.py.

GIT RULES: feat/server-authoritative-engine only; no worktrees, no branches. Commit when verified.

SCOPE (all deterministic tick mechanics; the LLM only chooses):
1. STORAGE CAPACITY: implement the `stores` effect from the Phase A function registry (validate_function_block already accepts it; the engine ignores it today). Built structures with `stores` grant village storage capacity per resource; `civilization["stockpile"]` becomes capacity-limited.
2. SPOILAGE: edibles (EDIBLE_RESOURCES) held by agents or in stockpile beyond storage capacity decay on a slow tick (new constant, sizing math in a comment). Spoilage events logged; a "food spoiled" reason surfaces via the existing nudge pattern.
3. CARRY & THE CART: agent carry cap stays COLLECT_CAP; add a `cart` recipe (RECIPES/SEED tier) whose holder gets a higher cap (query-time effect, like _gather_yield_bonus). This is the first vehicle.
4. SHELTER NEED: nightly tick (reuse a slow gate) — agents outside when it fires lose a little hunger/comfort unless housed (houses count vs population); surfaced as a nudge, never a hard punishment. Ties houses into daily consumption.
5. DECAY & REPAIR: structures gain `condition` (100 at build), decaying slowly; a new `repair_structure` action (sync AVAILABLE_ACTIONS / DECISION_ACTIONS / DECISION_SCHEMA / SYSTEM_PROMPT) restores it using a small resource cost; below a threshold the structure stops producing (visible in activity log); at 0 it collapses to a ruin that can be rebuilt cheaper than new (the deterministic escape). A rare disaster event may damage one structure (very low probability per slow tick, logged dramatically).
6. SEASONS: a four-season clock (very slow); season multiplies district stock regrowth (winter low, spring high) and is shown in prompts as one short line. Closes the loop with storage/spoilage.

CHANGE MAP HINTS: constants near the other Phase A/B constants (sim_engine.py ~260-350); tick gates join the RULES_TICK/EFFECT_TICK block in _tick_once (~3650); query-time effects follow _gather_yield_bonus/_craft_output_bonus (~1290); prompt lines join _build_think_payload (~3400); new action follows the start_terraform pattern end-to-end (engine apply_decision + server DECISION_ACTIONS/SYSTEM_PROMPT/normalize with surfaced reasons).

HARD RULES: no per-tick LLM calls; ≤200 prompt tokens added; no silent rejections (lastRepairRejection etc.); every gate has a deterministic escape (decay→repair/rebuild, spoilage→storage, winter→stores/season turns); state.json back-compat (setdefault everything); observability in the same commit (activity events + a benchmarks.jsonl metric, e.g. storage_utilization or spoilage_rate); GOODS_ENABLED=False must exactly match current behavior.

SMOKE TEST BEFORE COMMIT (force, don't wait): run the server briefly; force winter + spoilage by setting the season clock and an agent's inventory directly (or temporary tiny constants); force a structure to low condition and verify repair + collapse + rebuild; verify logs show each event with reasons. Revert any temporary constants before committing.

RECORD: append the Phase C implementation log to Part 4 of the plan doc; update CLAUDE.md's mechanics section with a GOODS_ENABLED bullet. py_compile both python files. Commit.
