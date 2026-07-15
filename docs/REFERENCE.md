# REFERENCE.md — deep mechanics (read on demand)

Detailed mechanics that used to live in CLAUDE.md. Read the section relevant to your task; don't load this whole file into context by default. Read the code for exact current behavior — historical design rationale is archived in `docs/archive/` (notably `docs/archive/cursor-plans-consolidated.md`, the merged record of the former `.cursor/plans/` directory, and `docs/archive/docs-archive-consolidated.md`).

## Feature flags (`simulation/sim_engine.py`)

Additive, feature-flagged systems, echoed to the viewer via `/state` `config.flags`. New actions must stay in sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py), the engine's `apply_decision()` + payload `available_actions` (sim_engine.py), and `ACTION_LABELS` (index.html, display only).

| Flag | Adds |
|---|---|
| `USE_GOALS` | LLM picks an action once; `_step_goal()` runs gather/deliver/craft/build deterministically to completion without further LLM calls. |
| `SURVIVAL_ENABLED` | `hunger`/`health`, auto-eating, collapse/revive, `heal_agent`, starvation backstops. |
| `CRAFTING_ENABLED` | `RECIPES` registry, `craft_item`, `propose_recipe`/`approve_recipe`. |
| `STRUCTURE_EFFECTS_ENABLED` | Structure `function` blocks (`produces`/`boosts`/`unlocks`/`houses`), saturation caps. |
| `ECOLOGY_ENABLED` | Per-district resource stocks that deplete/regrow, `start_terraform`, scarcity/craft-input reflexes, stalled-project abandonment. |
| `GOODS_ENABLED` | Storage capacity & spoilage, the `cart`, nightly shelter, structure `condition`/decay/`repair_structure`/ruins/disasters, seasons. |
| `TECH_TREE_ENABLED` | Structure/recipe/blueprint tiers, eras, the `wagon`, the invention council (parallel `propose_blueprint` + elder comparative judgment). |
| `ECONOMY_ENABLED` | The `market`, scarcity-based prices, priced `trade_resource`, home/property claims, wealth gini. |
| `CULTURE_ENABLED` | Skills-by-practice, teaching via `talk_to_nearby`, the `library`/knowledge-on-death, chronicle + meme mutation, personality drift. |
| `LIFECYCLE_ENABLED` | Aging, death (including the elder), succession elections via `propose_rule`/`vote_rule`. |
| `PATH1_ENABLED` | Master flag for 2D world depth: `INDUSTRY_ENABLED` (ore/kiln/tier-3 seeds), `TOOL_TIERS_ENABLED` (pick-gated gathers), `COMPOSABLE_BUILD_ENABLED` (`place_block`/`remove_block`), `TERRAIN_TILES_ENABLED` (`dig_terrain`/`plant_terrain`), `PATH1_DIPLOMACY_ENABLED` (second settlement, caravans, treaties), `TIER3_CONTENT_ENABLED`, `PRESSURE_LOOP_ENABLED` (night exposure, wildlife events). See `docs/archive/path-1-minecraft-like-world-plan.md` (historical) and `.cursor/path-1-integration-contract.json`. Smoke: `uv run python scripts/path1_smoke.py`. |

Full mechanic-by-mechanic writeups (what each phase does, why, and the constants involved) are archived in `docs/archive/civilization-emergence-plan.md` and `docs/archive/docs-archive-consolidated.md` — historical design rationale only.

## The build/civilization pipeline (the heart of the sim, not in the original specs)

`start_project` → agents `collect_resource` → `contribute_resources` → builder `build_structure` once funded → structure placed, `completedProjects++`, civilization level checked. On top of this sits a **blueprint flow**: agents `propose_blueprint` for new structure/resource types, the **elder approves/rejects**, approved blueprints merge into the live registries. The elder (Sage) is the singular leader — it assigns tasks to idle agents and approves blueprints. Background: the build-progression fix plan inside `docs/archive/cursor-plans-consolidated.md`.

## Sage-priority emergency (absolute, deterministic — sim_engine.py)

The elder Sage's survival overrides everything. `_sage_emergency()` returns the agent to revive when Sage is collapsed or below `SAGE_CRITICAL_HEALTH`; the **healer plus the single nearest able agent** rush to heal (healer first if she's also down), everyone else keeps working. Enforcement is deterministic (`_rush_to_heal()` + an in-flight guard that discards a responder's stale LLM decision), not just prompt-level. **Sage is mortal** (`LIFECYCLE_ENABLED`): old age can kill any agent including the elder; death triggers a succession election (`propose_rule`/`vote_rule` scaffold) rather than a permanent leaderless state.

## Invention pipeline safeguards (`sim_engine.py`)

`MAX_APPROVED_CUSTOM` caps concurrent custom blueprints; `_maybe_retire_blueprint` archives the oldest **built** custom to free a slot, `_invention_required()` returns False at the cap as a safety net, and blueprint rejections surface a `rejection_note`/`lastBlueprintRejection` nudge instead of failing silently. `_maybe_invention_backstop` gives an idle villager a one-shot invention-only turn before the elder takes over after repeated fruitless delegations.

## LLM pipeline details

- **Model routing** (`MODEL_SMART`/`MODEL_FAST` + `model_for_decision()` in `simulation/server.py`): elder turns and invention-required turns go to `MODEL_SMART`; everything else goes to `MODEL_FAST`. Both currently point at `qwen/qwen3.5-9b`. Ids must match LM Studio's loaded-model ids (`GET /v1/models`); if a routed id isn't available, the server auto-falls-back to `"local-model"` for the session. Model choice rationale/benchmark history: `docs/archive/`.
- **Context sizing:** each think call is ~3,100 prompt tokens; the worker pool (`MAX_CONCURRENT_LLM` in `simulation/sim_engine.py`, default 3) runs that many concurrently. Set LM Studio's context length to at least `3400 × parallel slots` (target: `uv run python scripts/lms_load.py`, which applies context 20000 / parallel 3 and flash attention), or lower `MAX_CONCURRENT_LLM`. `run_agent_decision()` auto-retries once with a slimmed prompt on a context-overflow error before falling back.
- **PIANO / META experiments** (`PIANO_MODULES` / `META_SYSTEM` in `simulation/sim_engine.py`, both off by default): fan out staggered cognitive modules per think turn (~3–5× LLM calls). Only enable with a reduced roster (`?agents=4-5`) and a correspondingly raised LM Studio context; calls remain bounded by the three-worker pool.
- Roster size override for experiments: `http://127.0.0.1:5001/?agents=12` (default 8; builder and elder always included).

## Specs vs. reality

The `specs/` directory is the original 6-gate build plan and is partly **superseded**. Where it conflicts with the running code, prefer the code and the archived plans (`docs/archive/cursor-plans-consolidated.md`):

- "Exactly two files" → four (sprites.js added, then the engine port split sim_engine.py out of the browser).
- "Exactly 12 agents" → default 8, URL-overridable.
- Port 5000 → 5001; browser opens the server URL, not a local file.
- 4 fixed projects / 3 fixed resources → dynamic registries extended at runtime by the blueprint flow.

`docs/archive/ISSUES.md` is a diagnostic of the *previously broken* state — useful context, but most of what it flags is addressed by the fix plans.
