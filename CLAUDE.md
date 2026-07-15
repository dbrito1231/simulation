# CLAUDE.md

Guidance for AI agents working in this repository. This file is the canonical agent guide (AGENTS.md points here). Keep it lean — deep mechanics live in [docs/REFERENCE.md](docs/REFERENCE.md); read that only when your task touches those systems.

## What this is

A real-time, browser-based AI village simulation: a local LLM (LM Studio) is the "brain" for 8–12 autonomous pixel-art agents that move, talk, trade, gather, and build. Proof-of-concept of the LLM-as-brain loop, inspired by Project Sid — keep it minimal and observable (non-goals: [specs/00-overview.md](specs/00-overview.md)).

## Model policy

**One orchestrator, many Sonnet 5 subagents.** The model that initiates/plans the work (whatever tier it is — Fable, Mythos, Opus, Sonnet) acts as the **orchestrator only**: it plans, splits the plan into phases/steps, dispatches, and reviews results. All implementation is delegated to **subagents running Sonnet 5 or lower** (`model: "sonnet"` on the Agent tool; use lower tiers when sufficient) — Sonnet 5 is the highest model any implementation agent may use. Use the `implementer` subagent type (`.claude/agents/implementer.md`, pinned to `model: sonnet`) for phase/step-level code changes. The orchestrator should not write implementation code itself except for trivial one-line fixes, and must never spawn implementation subagents on a tier above Sonnet 5.

## Commands

```bash
uv sync                              # install deps (flask, flask-cors, requests)
uv run python simulation/server.py   # start server, then open http://127.0.0.1:5001
```

- **Restarting/starting the server:** always run it in its own visible, titled `cmd` window (never backgrounded/detached) — from PowerShell:
  ```powershell
  Start-Process cmd.exe -ArgumentList '/k', 'title simserver && cd /d C:\Users\dbadmin\Desktop\GitServ\simulation && uv run python simulation\server.py' -WorkingDirectory 'C:\Users\dbadmin\Desktop\GitServ\simulation'
  ```
  Kill any prior instance first (`pkill -f "simulation/server.py"` from Bash, or close the `simserver` window).
- LM Studio must be running at `http://localhost:1234` with a model loaded (OpenAI-compatible API). Without it, agent decisions fall back to `rest` but the server stays up.
- Port is **5001** on purpose. The server serves the viewer — open `http://127.0.0.1:5001`, never `index.html` as a file.
- **No test suite, linter, or build step.** Verify by running the server and watching the browser + JSONL logs. Deterministic smokes (no LM Studio needed): `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py`.

## Architecture

**Server-authoritative**: the world runs headless in Python; the browser is a thin viewer holding no simulation state.

- **[simulation/sim_engine.py](simulation/sim_engine.py)** — the engine (`SimEngine`). Owns ALL world state, runs the 30/s tick loop on a daemon thread, applies decisions via `apply_decision()`, runs every deterministic system, dispatches LLM think jobs to a bounded worker pool, persists to `simulation/state.json`.
- **[simulation/server.py](simulation/server.py)** — Flask app + cognition. Serves viewer/state/controls; `run_agent_decision()` prompts LM Studio, extracts JSON, then `normalize_decision()` + `role_fallback_action()` reject invalid actions. `SessionLogger` writes per-session JSONL.
- **[simulation/index.html](simulation/index.html)** — thin viewer only. Polls `GET /state` (~10 Hz) and renders; closing it does not stop the sim.
- **[simulation/sprites.js](simulation/sprites.js)** — pure, stateless Canvas drawing.
- **[simulation/roles.json](simulation/roles.json)** — **single source of truth for role definitions**. Edit role data here, never in code maps.

Data flow: tick thread advances world → think timer fires → `_build_think_payload()` snapshots context under the lock → `run_agent_decision()` (server.py) → validated decision → `apply_decision()` mutates world under the lock. Browser only polls and renders.

## Critical invariants

- New actions must stay in sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py), `apply_decision()` + payload `available_actions` (sim_engine.py), and `ACTION_LABELS` (index.html, display only).
- The engine mutates world state only under its lock; full world persists to `simulation/state.json` (autosave + graceful-exit flush; `restore_state()` resumes old saves).
- `MAX_CONCURRENT_LLM = 3` (sim_engine.py); LM Studio context must cover ~3,400 tokens × parallel slots (`uv run python scripts/lms_load.py` applies the target config). Details: [docs/REFERENCE.md](docs/REFERENCE.md).
- The core loop is the build pipeline: `start_project` → gather → contribute → `build_structure`, plus a blueprint flow where the elder (Sage) approves new types. Sage's survival is protected by a deterministic emergency system. Details: [docs/REFERENCE.md](docs/REFERENCE.md).

## Feature flags

All in `simulation/sim_engine.py`, echoed to the viewer via `/state` `config.flags`: `USE_GOALS`, `SURVIVAL_ENABLED`, `CRAFTING_ENABLED`, `STRUCTURE_EFFECTS_ENABLED`, `ECOLOGY_ENABLED`, `GOODS_ENABLED`, `TECH_TREE_ENABLED`, `ECONOMY_ENABLED`, `CULTURE_ENABLED`, `LIFECYCLE_ENABLED`, `PATH1_ENABLED` (+ its sub-flags), plus experimental `PIANO_MODULES`/`META_SYSTEM` (off). What each adds: [docs/REFERENCE.md](docs/REFERENCE.md).

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `lm_studio.jsonl` (full LLM request/response/decision per call). Primary debugging surface — read `lm_studio.jsonl` to see what the model actually returned and which fallback fired. `simulation/logs/lm_studio_server.log`, if present, is LM Studio's *own* log (token usage, per-slot context checkpoints).

## Docs map

- [docs/HANDOFF.md](docs/HANDOFF.md) — **start here** when resuming: snapshot + narrative catch-up.
- [docs/REFERENCE.md](docs/REFERENCE.md) — deep mechanics: flag details, build/blueprint pipeline, Sage emergency, invention safeguards, LLM routing/context sizing, specs-vs-reality. Read when touching those systems.
- `docs/archive/` — **historical record only. Do not read or act on files there unless the user explicitly asks.**
