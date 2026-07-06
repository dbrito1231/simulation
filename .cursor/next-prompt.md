# Day 2 batch (cycle 2.morning, 2026-07-06): C3 amnesty + Phase C (GOODS_ENABLED) + C5 legacy strip

Phase B passed its exam this morning (session 2026-07-05T22-24-13: live invention loop end-to-end, 283 builds, ecology healthy). Work these three items IN ORDER, one commit each, on feat/server-authoritative-engine (no worktrees, no branches).

## Item 1 — C3: blueprint amnesty + custom-resource-cap expiry (small)

`rejectedBlueprintIds` in sim_engine.py only ever grows — a permanent blacklist. Give rejected ids an expiry (amnesty after a long cooldown, mirroring `_maybe_retire_blueprint`'s pattern), so a once-rejected idea can be legitimately re-proposed later.

ALSO (found by the 2026-07-06 morning audit, same shape): the busiest rejection of the whole 9h soak was `too many custom resources` ×137 — the custom-resource cap has no retirement/expiry analogue. Add one: when the cap is hit, retire the oldest custom resource that has no remaining producer/consumer references (a structure produce, recipe input/output, or stockpile balance > 0 counts as a reference; skip if all are referenced — and in that case make the rejection note say the cap is hard until something retires). No silent behavior changes: log retirements to activity.jsonl.

Back-compat: state.json setdefault everything. Commit as its own commit.

## Item 2 — Phase C: physical goods, plural needs & consequence (GOODS_ENABLED)

Read docs/civilization-emergence-plan.md (Phase C scope in Part 4, Part 8 hard rules) and CLAUDE.md. Implement Phase C — physical goods, plural needs & consequence — behind a new GOODS_ENABLED flag in simulation/sim_engine.py.

SCOPE (all deterministic tick mechanics; the LLM only chooses):
1. STORAGE CAPACITY: implement the `stores` effect from the Phase A function registry (validate_function_block already accepts it; the engine ignores it today). Built structures with `stores` grant village storage capacity per resource; `civilization["stockpile"]` becomes capacity-limited.
2. SPOILAGE: edibles (EDIBLE_RESOURCES) held by agents or in stockpile beyond storage capacity decay on a slow tick (new constant, sizing math in a comment). Spoilage events logged; a "food spoiled" reason surfaces via the existing nudge pattern.
3. CARRY & THE CART: agent carry cap stays COLLECT_CAP; add a `cart` recipe (RECIPES/SEED tier) whose holder gets a higher cap (query-time effect, like _gather_yield_bonus). This is the first vehicle.
4. SHELTER NEED: nightly tick (reuse a slow gate) — agents outside when it fires lose a little hunger/comfort unless housed (houses count vs population); surfaced as a nudge, never a hard punishment. Ties houses into daily consumption.
5. DECAY & REPAIR: structures gain `condition` (100 at build), decaying slowly; a new `repair_structure` action (sync AVAILABLE_ACTIONS / DECISION_ACTIONS / DECISION_SCHEMA / SYSTEM_PROMPT) restores it using a small resource cost; below a threshold the structure stops producing (visible in activity log); at 0 it collapses to a ruin that can be rebuilt cheaper than new (the deterministic escape). A rare disaster event may damage one structure (very low probability per slow tick, logged dramatically).
6. SEASONS: a four-season clock (very slow); season multiplies district stock regrowth (winter low, spring high) and is shown in prompts as one short line. Closes the loop with storage/spoilage.

CHANGE MAP HINTS: constants near the other Phase A/B constants (sim_engine.py ~260-350); tick gates join the RULES_TICK/EFFECT_TICK block in _tick_once (~3650); query-time effects follow _gather_yield_bonus/_craft_output_bonus (~1290); prompt lines join _build_think_payload (~3400); new action follows the start_terraform pattern end-to-end (engine apply_decision + server DECISION_ACTIONS/SYSTEM_PROMPT/normalize with surfaced reasons).

HARD RULES: no per-tick LLM calls; ≤200 prompt tokens added; no silent rejections (lastRepairRejection etc.); every gate has a deterministic escape (decay→repair/rebuild, spoilage→storage, winter→stores/season turns); state.json back-compat (setdefault everything); observability in the same commit (activity events + a benchmarks.jsonl metric, e.g. storage_utilization or spoilage_rate); GOODS_ENABLED=False must exactly match current behavior.

AUDIT CONTEXT worth designing for (2026-07-06 morning): the village currently builds ~30 structures/hour with nothing consuming them (283 builds, 9 new districts overnight — sprawl). Decay/upkeep is the designed consumer: sized right, upkeep should bend the build rate down and make repair a real competing use of resources. Note your sizing math in comments.

SMOKE TEST BEFORE COMMIT (force, don't wait): run the server briefly; force winter + spoilage by setting the season clock and an agent's inventory directly (or temporary tiny constants); force a structure to low condition and verify repair + collapse + rebuild; verify logs show each event with reasons. Revert any temporary constants before committing.

RECORD: append the Phase C implementation log to Part 4 of the plan doc; update CLAUDE.md's mechanics section with a GOODS_ENABLED bullet. py_compile both python files. Commit.

## Item 3 — C5: strip legacy client-sim code from index.html (cleanup)

The server-authoritative engine made index.html a viewer, but dead client-side simulation/decision code still lives there and misleads audits (docs/copilot-audit-response.md item C5). Strip the dead client-side simulation/decision paths from index.html, leaving the viewer (rendering, state polling, UI). Fix CLAUDE.md's stale "Data flow" paragraph to describe the server-authoritative flow. Verify by running the server and confirming the browser view still renders agents/structures/conversations correctly (no console errors). Own commit.

## After all three

py_compile simulation/server.py and simulation/sim_engine.py one final time. Leave the server STOPPED (port 5001 free) — the parent session restarts it. Report: what landed per item, commit shas, smoke-test evidence, any scope you had to cut.
