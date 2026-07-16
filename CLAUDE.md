# CLAUDE.md

Guidance for AI agents working in this repository. This is the canonical agent guide (AGENTS.md points here). Keep it lean — deep mechanics live in [docs/REFERENCE.md](docs/REFERENCE.md); read that only when your task touches those systems.

## What this is

A server-authoritative AI village simulation: a Python engine runs the world headless; a local LLM (LM Studio) is the "brain" for 8–12 pixel-art agents that move, talk, trade, gather, and build; the browser is a thin viewer. Proof-of-concept of the LLM-as-brain loop, inspired by Project Sid — minimal and observable (non-goals: [specs/00-overview.md](specs/00-overview.md)).

## Model policy

**One orchestrator, many Sonnet 5 subagents.** The initiating model (any tier — Fable, Mythos, Opus, Sonnet) acts as the **orchestrator only**: plans, splits into phases/steps, dispatches, reviews. All implementation is delegated to subagents on **Sonnet 5 or lower** (`model: "sonnet"` on the Agent tool; lower tiers when sufficient) — use the `implementer` subagent type (`.claude/agents/implementer.md`, pinned to `model: sonnet`) for phase/step-level code changes. The orchestrator writes no implementation code itself except trivial one-line fixes.

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
- LM Studio must be running at `http://localhost:1234` with a model loaded (OpenAI-compatible API); without it, decisions fall back to `rest` but the server stays up.
- Port is **5001** on purpose — open `http://127.0.0.1:5001`, never `index.html` as a file.
- **No test suite, linter, or build step.** Verify by running the server, watching the browser + JSONL logs. Deterministic smokes (no LM Studio needed): `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py`.

## Architecture

**Server-authoritative**: the world runs headless in Python; the browser is a thin viewer holding no simulation state.

- **[simulation/sim_engine.py](simulation/sim_engine.py)** — the engine (`SimEngine`). Owns ALL world state, runs the 30/s tick loop, applies decisions via `apply_decision()`, runs every deterministic system, dispatches LLM think jobs to a bounded worker pool, persists to `simulation/state.json`.
- **[simulation/server.py](simulation/server.py)** — Flask app + cognition. Serves viewer/state/controls; `run_agent_decision()` prompts LM Studio, extracts JSON; `normalize_decision()` + `role_fallback_action()` reject invalid actions. `SessionLogger` writes per-session JSONL.
- **[simulation/index.html](simulation/index.html)** — thin viewer only. Polls `GET /state` (~10 Hz) and renders; closing it does not stop the sim.
- **[simulation/sprites.js](simulation/sprites.js)** — pure, stateless Canvas drawing.
- **[simulation/roles.json](simulation/roles.json)** — **single source of truth for role definitions**; edit role data here, never in code maps.

Data flow: tick thread advances world → think timer fires → `_build_think_payload()` snapshots context under the lock → `run_agent_decision()` (server.py) → validated decision → `apply_decision()` mutates world under the lock. Browser only polls and renders.

## Critical invariants

- New actions must stay in sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py), `apply_decision()` + payload `available_actions` (sim_engine.py), and `ACTION_LABELS` (index.html, display only) — [specs/01-architecture.md](specs/01-architecture.md#action-sync-invariant).
- The engine mutates world state only under its lock; full world persists to `simulation/state.json` (autosave + graceful-exit flush; `restore_state()` resumes old saves) — [specs/02-engine-core.md](specs/02-engine-core.md).
- `MAX_CONCURRENT_LLM = 3` (sim_engine.py); LM Studio context must cover ~3,400 tokens × parallel slots (`uv run python scripts/lms_load.py` applies target config) — [specs/03-cognition.md](specs/03-cognition.md).
- Core loop is the build pipeline: `start_project` → gather → contribute → `build_structure`, plus a blueprint flow where elder Sage approves new types; Sage's survival is protected by a deterministic emergency system — [specs/07-actions.md](specs/07-actions.md), [specs/02-engine-core.md](specs/02-engine-core.md#sage-emergency).
- **specs/ must always match the repo.** Any code change that alters behavior, actions, flags, routes, constants, or data shapes MUST update the owning spec in the same change (SDD: specs first, code second). Ownership map: [specs/00-overview.md](specs/00-overview.md).

## Feature flags

~30 module-level flags in `simulation/sim_engine.py`; most echoed to the viewer via `/state` `config.flags`. Complete index: [specs/01-architecture.md](specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy). Semantics per flag: [specs/02](specs/02-engine-core.md), [03](specs/03-cognition.md), [08](specs/08-systems-economy.md), [09](specs/09-systems-society.md), [10](specs/10-path1.md).

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `lm_studio.jsonl` (full LLM request/response/decision per call). Primary debugging surface — read `lm_studio.jsonl` to see what the model actually returned and which fallback fired. `simulation/logs/lm_studio_server.log`, if present, is LM Studio's *own* log (token usage, per-slot context checkpoints).

## Docs map

- [docs/HANDOFF.md](docs/HANDOFF.md) — **start here** when resuming: snapshot + narrative catch-up.
- [specs/00-overview.md](specs/00-overview.md) — index of the canonical, rebuildable 13-file spec set.
- [docs/REFERENCE.md](docs/REFERENCE.md) — historical-rationale pointers plus LM Studio operational tips not already canonical in specs/03.
- `docs/plan-visual-{1,2,3}-*.md` — visual-polish plans (1, 2 planned; 3 done).
- `docs/archive/` — **historical record only. Do not read or act on files there unless the user explicitly asks.**
