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
- The server (not the browser) serves `index.html` and `sprites.js`, so open `http://127.0.0.1:5001` — do **not** open `index.html` as a file (the spec text predates this; trust the running setup).
- Port is **5001** on purpose (macOS AirPlay squats on 5000).
- Roster size override for experiments: `http://127.0.0.1:5001/?agents=12` (default 8; the builder and elder are always included).
- There is **no test suite, linter, or build step.** `main.py` is an unused stub. Verify changes by running the server and watching the browser + the JSONL logs.

## Architecture

Three files do all the work, despite [specs/01-architecture.md](specs/01-architecture.md) saying "exactly two." The split (and several other deviations) are deliberate and documented in `.cursor/plans/`.

- **[simulation/server.py](simulation/server.py)** — Flask app. Serves the frontend, exposes `POST /agent/think` (the core decision endpoint) and `POST /log/event`. For each think request it builds a system+user prompt from the agent/civilization state, calls LM Studio, extracts JSON from the model output (handles reasoning models that emit empty `content` or code fences), then runs `normalize_decision()` + `role_fallback_action()` to reject invalid/impossible actions and substitute a sensible deterministic one. `SessionLogger` writes per-session JSONL.
- **[simulation/index.html](simulation/index.html)** — all client state and the `requestAnimationFrame` loop. Holds the `agents` array and the central `civilization` object (active project, structures, dynamic resource/project registries, pending blueprints). `applyDecision()` is the large switch that turns an LLM decision into a world mutation. LLM calls go through a bounded-concurrency queue (`MAX_CONCURRENT_LLM = 3`, `LLM_MIN_GAP_MS = 250`) via `drainThinkQueue()` — not one-at-a-time.
- **[simulation/sprites.js](simulation/sprites.js)** — pure Canvas drawing (terrain, zones, agents, structures). Stateless. Custom/blueprint structure types fall back to `drawGenericStructure()`.

### Data flow

Browser collects agent + civilization state → `POST /agent/think` → server prompts LM Studio → JSON decision validated/normalized server-side → browser `applyDecision()` mutates world and renders. The server holds no simulation state between requests; all state lives in the browser.

### The build/civilization pipeline (the heart of the sim, not in the original specs)

`start_project` → agents `collect_resource` → `contribute_resources` → builder `build_structure` once funded → structure placed, `completedProjects++`, civilization level checked. On top of this sits a **blueprint flow**: agents `propose_blueprint` for new structure/resource types, the **elder approves/rejects**, approved blueprints merge into the live registries. The elder (Sage) is the singular leader — it assigns tasks to idle agents and approves blueprints. Earlier the pipeline stalled because the LLM never spontaneously chose `start_project`; the fixes (any role can start projects, elder-driven task assignment, prompt nudges, deterministic fallbacks) live in `.cursor/plans/fix_build_progression.plan.md`.

## Specs vs. reality

The `specs/` directory is the original 6-gate build plan and is partly **superseded**. Where it conflicts with the running code, prefer the code and the plans in `.cursor/plans/`:

- "Exactly two files" → three (sprites.js added).
- "Exactly 12 agents" → default 8, URL-overridable.
- Port 5000 → 5001; browser opens the server URL, not a local file.
- 4 fixed projects / 3 fixed resources → dynamic registries extended at runtime by the blueprint flow.

[simulation/ISSUES.md](simulation/ISSUES.md) is a diagnostic of the *previously broken* state — useful context, but most of what it flags is addressed by the fix plans.

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `lm_studio.jsonl` (full LLM request/response/decision per call). These are the primary debugging surface — read `lm_studio.jsonl` to see what the model actually returned and which fallback fired.
