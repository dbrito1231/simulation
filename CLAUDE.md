# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ops and architecture companion. **Workflow source of truth is [AGENTS.md](AGENTS.md)** — read it before making any change. Deep mechanics live in [docs/REFERENCE.md](docs/REFERENCE.md); read that only when your task touches those systems.

## What this is

A server-authoritative AI village simulation: a Python engine runs the world headless; a local LLM (Ollama) is the "brain" for 8–12 pixel-art agents that move, talk, trade, gather, and build; the browser is a thin viewer. Proof-of-concept of the LLM-as-brain loop, inspired by Project Sid — minimal and observable (non-goals: [specs/00-overview.md](specs/00-overview.md)).

## Workflow — non-negotiable

Every user-requested change follows the [AGENTS.md](AGENTS.md) loop. Summary only; AGENTS.md wins on any conflict:

1. Plan → user approves → the planning session **becomes orchestrator** (read-only for repo edits).
2. Orchestrator splits the plan into phases with copy-pasteable prompts (goal, owning specs, in-scope/out-of-scope files, acceptance checks).
3. Each phase → **implementer** subagent (`.claude/agents/implementer.md`) writes specs + code → **reviewer** subagent (`.claude/agents/reviewer.md`) checks accuracy, plan fit, SDD sync, and security → findings to orchestrator → SUCCESS to user, or FAIL → new implementer prompt.

Hard rules:

- **Only implementers write.** The orchestrator dispatches; it does not edit.
- **SDD:** update the owning `specs/` file in the same change as behavior code — specs must never drift. Ownership map: [specs/00-overview.md](specs/00-overview.md).
- **KISS:** smallest change that satisfies the approved plan and owning specs. No speculative refactors or out-of-scope edits without user approval.
- **No hallucinations, unproven theories, or AI slop.** Ground every claim in a spec, a code path, or verified runtime evidence (smokes, logs). Reviewers treat invented claims as FAIL material.
- **Escalation:** implementer/reviewer doubts go to the orchestrator, who asks the user. Never invent an answer; never ask the user directly from a subagent.

Manual entry/resume: the `loop-in-devs` skill (`.cursor/skills/loop-in-devs/`). Cursor rules in `.cursor/rules/` (`agents-loop`, `agents-sdd`, `agents-model-policy`, plus per-surface action-sync/engine/viewer rules) reinforce the same contract.

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
- **Single-instance rule:** multiple `simulation/server.py` processes have repeatedly ended up running at once (stale terminal, forgotten restart, etc.), all fighting over port 5001 and `state.db`. As the **last step of every implementation task** that starts, restarts, or touches the server, verify only one instance is running before reporting done — e.g. `pgrep -fa "simulation/server.py"` (Bash) or `Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation.server' }` (PowerShell) — and kill any extras, keeping the most recently started one. Note `uv run` shows a wrapper + interpreter pair; that parent/child pair is **one** instance.
- Ollama must be running at `http://localhost:11434` with the `sim-smart`/`sim-fast` models loaded (native `/api/chat`); canonical loader is `uv run python scripts/ollama_setup.py` (sets env vars, creates/warms both models, verifies dual residency — see [ollama_config.md](ollama_config.md)). Without Ollama, decisions fall back to `rest` but the server stays up.
- Port is **5001** on purpose — open `http://127.0.0.1:5001`, never `index.html` as a file.
- **No test suite, linter, or build step.** Verify by running the server, watching the browser + JSONL logs. Deterministic smokes (no Ollama needed): `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py`. `scripts/` also holds targeted smokes/soaks per subsystem (god mode, blueprints, daily council, hunting, log retention, succession, town integrity) — run the one that covers the surface you touched.
- Never commit credentials, `simulation/logs/`, or `simulation/state.db`.

## Architecture

**Server-authoritative**: the world runs headless in Python; the browser is a thin viewer holding no simulation state.

- **[simulation/sim_engine.py](simulation/sim_engine.py)** — the engine (`SimEngine`). Owns ALL world state, runs the 30/s tick loop, applies decisions via `apply_decision()`, runs every deterministic system, dispatches LLM think jobs to a bounded worker pool, persists to `simulation/state.db`.
- **[simulation/server.py](simulation/server.py)** — Flask app + cognition. Serves viewer/state/controls; `run_agent_decision()` prompts Ollama, extracts JSON; `normalize_decision()` + `role_fallback_action()` reject invalid actions. `SessionLogger` writes per-session JSONL.
- **[simulation/index.html](simulation/index.html)** — thin viewer shell (markup only).
- **[simulation/viewer.js](simulation/viewer.js)** — polling, render loop, sidebar, Divine Console.
- **[simulation/viewer.css](simulation/viewer.css)** — viewer layout and panel chrome.
- **[simulation/sprites.js](simulation/sprites.js)** — pure, stateless Canvas drawing.
- **[simulation/roles.json](simulation/roles.json)** — **single source of truth for role definitions**; edit role data here, never in code maps.

Data flow: tick thread advances world → think timer fires → `_build_think_payload()` snapshots context under the lock → `run_agent_decision()` (server.py) → validated decision → `apply_decision()` mutates world under the lock. Browser only polls and renders.

## Critical invariants

- New actions must stay in sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py), `apply_decision()` + payload `available_actions` (sim_engine.py), and `ACTION_LABELS` (viewer.js, display only) — [specs/01-architecture.md](specs/01-architecture.md#action-sync-invariant), [specs/07-actions.md](specs/07-actions.md).
- The engine mutates world state only under its lock, and LLM calls stay outside it; full world persists to `simulation/state.db` (autosave + graceful-exit flush; `restore_state()` resumes old saves) — [specs/02-engine-core.md](specs/02-engine-core.md).
- `MAX_CONCURRENT_LLM = 3` (sim_engine.py); Ollama's `num_ctx` must cover ~3,400 tokens × parallel slots (`uv run python scripts/ollama_setup.py` applies target config; see [ollama_config.md](ollama_config.md)) — [specs/03-cognition.md](specs/03-cognition.md).
- Viewer is a pure renderer — no decisions, movement, or mutation in the browser; keep `WORLD_W`/`WORLD_H` matched between `sim_engine.py` and `viewer.js` — [specs/11-viewer.md](specs/11-viewer.md).
- God routes (`/control/god/*`) are control endpoints, not decision actions; audited via `divine.jsonl` — [specs/12-ops.md](specs/12-ops.md).
- Core loop is the build pipeline: `start_project` → gather → contribute → `build_structure`, plus a blueprint flow where elder Sage approves new types; Sage's survival is protected by a deterministic emergency system — [specs/07-actions.md](specs/07-actions.md), [specs/02-engine-core.md](specs/02-engine-core.md#sage-emergency).
- **specs/ must always match the repo.** Any code change that alters behavior, actions, flags, routes, constants, or data shapes MUST update the owning spec in the same change (SDD: specs first, code second).

## Feature flags

~30 module-level flags in `simulation/sim_engine.py`; most echoed to the viewer via `/state` `config.flags`. Complete index: [specs/01-architecture.md](specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy). Semantics per flag: [specs/02](specs/02-engine-core.md), [03](specs/03-cognition.md), [08](specs/08-systems-economy.md), [09](specs/09-systems-society.md), [10](specs/10-path1.md).

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `llm.jsonl` (full LLM request/response/decision per call; sessions predating the Ollama migration used `lm_studio.jsonl`), `benchmarks.jsonl` (Sid-parity metrics: specialization index, rule adherence, meme adoption, memory-store size, module-activation timeline), `divine.jsonl` (Sovereign God mode intervention audit trail — dark by default, see [specs/12-ops.md](specs/12-ops.md)), and `compiler.jsonl` (Sovereign God mode Optional Phase 8 free-prose compiler attempts — draft/rejection, dark by default behind its own `GOD_COMPILER_ENABLED` flag). Primary debugging surface — read `llm.jsonl` to see what the model actually returned and which fallback fired. Ollama's own log (token usage, per-request checkpoints) lives outside the repo at `%LOCALAPPDATA%\Ollama\server.log`.

## Docs map

- [AGENTS.md](AGENTS.md) — **AI workflow SoT** (loop, roles, models, SDD, constraints).
- [docs/HANDOFF.md](docs/HANDOFF.md) — **start here** when resuming: snapshot + narrative catch-up.
- [specs/00-overview.md](specs/00-overview.md) — index of the canonical, rebuildable 13-file spec set (`00`–`12`).
- [docs/REFERENCE.md](docs/REFERENCE.md) — historical-rationale pointers plus Ollama operational tips not already canonical in specs/03.
- [README.md](README.md) — human-facing setup/run instructions and the agent-roster override.
- [ollama_config.md](ollama_config.md) — target Ollama model/context configuration.
- `docs/plan-*.md` — per-batch implementation plans (living-ecosystem, living-world, agents-panel, sovereign-god-mode, visual polish, etc.).
- `docs/archive/` — **historical record only. Do not read or act on files there unless the user explicitly asks.**
