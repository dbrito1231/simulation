Read docs/civilization-emergence-plan.md (Phase D scope in Part 4, Part 6 model notes, Part 8 hard rules) and CLAUDE.md. Implement Phase D — technology tiers & eras — behind TECH_TREE_ENABLED in simulation/sim_engine.py.

GIT RULES: feat/server-authoritative-engine only; no worktrees, no branches. Commit when verified.

SCOPE:
1. TIERS: every structure type and recipe gets `tier` (seeds = 1; granary/cart = 2). Blueprints/recipes may declare tier N+1 only if the village has a tier-N station built (workshop=1; a new tier-2 station type, e.g. forge, is added as a seed template with a function block). validate_blueprint/validateRecipe enforce prerequisites WITH surfaced reasons ("requires a forge (tier 2) first").
2. UNLOCKS: the Phase A `unlocks` effect gains tier semantics — a station structure unlocks crafting/inventing at its tier. _craft_station_unlocked and the invention prompt become tier-aware (the invention prompt lists what the current tier allows — keep it short).
3. ERAS: replace the vanity level with era computed from capabilities held (has tier-2 station, has vehicle, has writing...). Era shown in prompts (one line) and UI chip; era transitions logged dramatically ("The village enters the Craftsman Era"). Keep `level` field for back-compat but stop surfacing it.
4. VEHICLES: `cart` (Phase C) upgrades to a `wagon` blueprint path — a MOBILE structure: crafted at tier 2, assigned to an agent, raises carry cap further and speeds movement (query-time effects). This is the audit's "cars" answer: reachable only through the chain, never named into existence.
5. MODEL EXPERIMENT HOOK: invention-only calls read optional per-call overrides (temperature/max_tokens) from constants near MODEL_SMART in server.py, defaulting to current values — the Part 6 replay experiment flips them, not you.

CHANGE MAP HINTS: tier data lives in PROJECT_TEMPLATES/RECIPES entries + blueprint schema (server.py validate_blueprint ~1100, SYSTEM_PROMPT blueprint section); era computation is a pure function in sim_engine + benchmark metric `era`; prerequisites check joins _invention_required/_start_project_for gating (surfaced, with escape: tier-N station buildable at tier N-1).

HARD RULES: same as always — flags, no per-tick LLM calls, ≤200 prompt tokens, no silent rejections, deterministic escapes (a missing station must itself be buildable now), state.json back-compat, observability in-commit (era benchmark + tier-gate rejection events), TECH_TREE_ENABLED=False = current behavior.

SMOKE TEST BEFORE COMMIT: force the chain live — build workshop, craft toward the tier-2 station, verify a tier-2 blueprint is rejected before and accepted after; verify era transition fires and logs; verify wagon effects apply to its holder.

RECORD: Phase D implementation log in Part 4; CLAUDE.md bullet. py_compile. Commit.
