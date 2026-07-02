# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A real-time, browser-based AI village simulation. A local LLM (served by LM Studio) acts as the "brain" for each of 8–12 autonomous pixel-art agents that move, talk, trade, gather resources, and collaboratively build structures. It is a proof-of-concept of the LLM-as-brain loop, inspired by Project Sid. Keep it minimal and observable — see [specs/00-overview.md](specs/00-overview.md) for the non-goals.

## Commands

```bash
uv sync                              # install deps (flask, flask-cors, requests)
uv run python simulation/server.py   # start server, then open http://127.0.0.1:5001
```

- LM Studio must be running at `http://localhost:1234` with a model loaded (OpenAI-compatible API). Without it, every agent decision falls back to `rest` and the status dot goes red, but the server stays up.
- **LM Studio context length vs. parallel slots:** the server-authoritative engine's think-worker pool (`MAX_CONCURRENT_LLM` in `simulation/sim_engine.py`, currently **2** — a server-side `ThreadPoolExecutor`, not a client-side queue in `index.html`) runs up to that many decision calls at once, and each one measures **~3,100 prompt tokens** in practice (well above the ~1,500 originally assumed here). LM Studio splits its configured context length across its parallel slots — if `context length ÷ parallel slots` is under ~3,400, expect `"Context size has been exceeded"` errors in bursts under concurrent load. `run_agent_decision()` in `simulation/server.py` detects that specific error and retries once with a slimmed-down prompt (memory/recent-conversations/examples dropped) before giving up the turn to `bad_response_fallback`, and logs the retry distinctly as `context_overflow` in `lm_studio.jsonl`. Set LM Studio's context length to at least `3400 × parallel slots`, or lower `MAX_CONCURRENT_LLM` instead.
- The server (not the browser) serves `index.html` and `sprites.js`, so open `http://127.0.0.1:5001` — do **not** open `index.html` as a file (the spec text predates this; trust the running setup).
- Port is **5001** on purpose (macOS AirPlay squats on 5000).
- Roster size override for experiments: `http://127.0.0.1:5001/?agents=12` (default 8; the builder and elder are always included).
- There is **no test suite, linter, or build step.** `main.py` is an unused stub. Verify changes by running the server and watching the browser + the JSONL logs.

## Architecture

Three code files do all the work, despite [specs/01-architecture.md](specs/01-architecture.md) saying "exactly two", plus one shared data file. The split (and several other deviations) are deliberate and documented in `.cursor/plans/`.

- **[simulation/server.py](simulation/server.py)** — Flask app. Serves the frontend, exposes `POST /agent/think` (the core decision endpoint) and `POST /log/event`. For each think request it builds a system+user prompt from the agent/civilization state, calls LM Studio, extracts JSON from the model output (handles reasoning models that emit empty `content` or code fences), then runs `normalize_decision()` + `role_fallback_action()` to reject invalid/impossible actions and substitute a sensible deterministic one. `SessionLogger` writes per-session JSONL.
- **[simulation/index.html](simulation/index.html)** — all client state and the `requestAnimationFrame` loop. Holds the `agents` array and the central `civilization` object (active project, structures, dynamic resource/project registries, pending blueprints). `applyDecision()` is the large switch that turns an LLM decision into a world mutation. LLM calls go through a bounded-concurrency queue (`MAX_CONCURRENT_LLM = 2`, `LLM_MIN_GAP_MS = 250`) via `drainThinkQueue()` — not one-at-a-time.
- **[simulation/sprites.js](simulation/sprites.js)** — pure Canvas drawing (terrain, zones, agents, structures). Stateless. Custom/blueprint structure types fall back to `drawGenericStructure()`.
- **[simulation/roles.json](simulation/roles.json)** — the **single source of truth for role definitions** (`skill`, `specialty[]`, `preferredProject`, `leader`). The server loads it at startup and derives its role maps (`ROLE_PROJECT`, `RESOURCE_GATHER_ROLES`, `ROLE_PRIMARY_RESOURCE`) from it; it also serves it to the browser as `/roles.js` (a `const ROLES = {…}` global), from which the client derives `ROLE_SKILLS`/`ROLE_PROJECT`/`ROLE_SPECIALTY_RESOURCE`. Edit role data here, never in the two code files.

### Data flow

Browser collects agent + civilization state → `POST /agent/think` → server prompts LM Studio → JSON decision validated/normalized server-side → browser `applyDecision()` mutates world and renders. The server holds no simulation state between requests; all state lives in the browser.

### The build/civilization pipeline (the heart of the sim, not in the original specs)

`start_project` → agents `collect_resource` → `contribute_resources` → builder `build_structure` once funded → structure placed, `completedProjects++`, civilization level checked. On top of this sits a **blueprint flow**: agents `propose_blueprint` for new structure/resource types, the **elder approves/rejects**, approved blueprints merge into the live registries. The elder (Sage) is the singular leader — it assigns tasks to idle agents and approves blueprints. Earlier the pipeline stalled because the LLM never spontaneously chose `start_project`; the fixes (any role can start projects, elder-driven task assignment, prompt nudges, deterministic fallbacks) live in `.cursor/plans/fix_build_progression.plan.md`.

### Mineflayer-inspired mechanics (feature-flagged constants near the top of index.html)

Three additive systems, each toggled by a `const` flag so behavior can be A/B compared. New actions are kept in sync between `AVAILABLE_ACTIONS` (index.html) and `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py).

- **`SURVIVAL_ENABLED` (#2)** — agents have `hunger`/`health`; `updateSurvival()` (frame-gated by `SURVIVAL_TICK_FRAMES`) drains hunger at `HUNGER_RATE` (0.3/tick — sized so ~3 part-time food producers can feed a roster of 8), then health when starving, and **auto-eats** the first held edible (`firstEdible()` over `EDIBLE_RESOURCES = ["food", "fish"]`, so the fisher self-feeds on his catch — the food consumption sink). At 0 health an agent is `incapacitated` (skips move/think, greyed sprite) until revived. New action **`heal_agent`** restores health (healers heal 2×); any agent can also feed a collapsed neighbour any edible it holds. No permanent death — collapse is recoverable, and both revival paths floor hunger at `REVIVE_HUNGER` (35) so a revived agent doesn't re-collapse seconds later. The food economy is protected deterministically: edibles are **exempt from the resource tax** (the stockpile is never consumed, so taxing them deletes food), `_maybe_force_contribution` leaves each agent an `EDIBLE_RESERVE` (3), a starving agent **auto-receives** an edible from a neighbour within `SHARE_RADIUS` holding above the reserve (`_share_edible_with`), and a foodless agent at hunger ≤ `STARVING_HUNGER` (10) **deterministically seeks the nearest food zone** and gathers (`_maybe_feed_starving`, tick-gated like the other `_maybe_*` backstops — the hunger prompt-nudge is coherence only).
- **`CRAFTING_ENABLED` (#4)** — a `RECIPES` registry produces non-gatherable crafted goods (`planks`/`bricks`/`tools`, `gatherZone:null`) via **`craft_item`** at a station zone. Advanced builds (e.g. the `granary`) require crafted goods → gather→craft→build chain. Agents extend the tree via **`propose_recipe`** / elder **`approve_recipe`**/`reject_recipe`, mirroring the blueprint flow (`pendingRecipes`, `validateRecipe`).
- **`USE_GOALS` (#1)** — the LLM picks an action; `goalForDecision()` turns gather/deliver/craft/build into a persistent `agent.goal`, and `stepGoal()` runs it deterministically every `GOAL_STEP_FRAMES` (with a `ttl` cap) **without an LLM call**, consulting the model again only when the goal completes or blocks. Cuts LLM load and improves coherence.

### Sage-priority emergency (absolute, deterministic — index.html)

The elder Sage's survival overrides everything. `sageEmergency()` returns the agent to revive when Sage is collapsed or below `SAGE_CRITICAL_HEALTH` (**30**) — the **healer first if she is also down** (she's the key to saving Sage), otherwise Sage. Only a scoped responder set rushes in: `sageResponders(target)` is the **healer plus the single nearest able agent** (≤2 villagers), so everyone else keeps working (the original "all villagers rush" rule was overkill — it caused ~270 rushes/session). The tick loop runs `rushToHeal()` for just those responders (clearing their `goal`, skipping LLM), and `thinkAgent` has an **in-flight guard** that discards a responder's LLM decision that lands mid-emergency — so no task, goal, or model output can take precedence for them (enforcement is deterministic; the `SYSTEM_PROMPT` rule #15 + a responder-only nudge are only for coherence). Collapse stays recoverable: assisted `heal_agent` revives fast, and `COLLAPSE_REGEN` self-revives an unattended agent slowly (`COLLAPSE_REVIVE_HEALTH`) so no one is permanently stuck. A `CRAFT_STALL_THRESHOLD` nudge pushes the village to build a workshop and craft when the chain has stalled.

## Specs vs. reality

The `specs/` directory is the original 6-gate build plan and is partly **superseded**. Where it conflicts with the running code, prefer the code and the plans in `.cursor/plans/`:

- "Exactly two files" → three (sprites.js added).
- "Exactly 12 agents" → default 8, URL-overridable.
- Port 5000 → 5001; browser opens the server URL, not a local file.
- 4 fixed projects / 3 fixed resources → dynamic registries extended at runtime by the blueprint flow.

[simulation/ISSUES.md](simulation/ISSUES.md) is a diagnostic of the *previously broken* state — useful context, but most of what it flags is addressed by the fix plans.

[docs/project-sid-parity-roadmap.md](docs/project-sid-parity-roadmap.md) maps the gap between this codebase and the Project Sid paper (`docs/2024-10-31.pdf`) and lays out a tiered roadmap (memory, emergent roles, voting, memes, PIANO) for closing it in 2D.

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `lm_studio.jsonl` (full LLM request/response/decision per call). These are the primary debugging surface — read `lm_studio.jsonl` to see what the model actually returned and which fallback fired.

`simulation/logs/lm_studio_server.log`, if present, is LM Studio's *own* server log (not written by this app) — useful for diagnosing model-side issues like context-window/parallel-slot sizing, since it shows token usage and per-slot context checkpoints that `lm_studio.jsonl` doesn't.
