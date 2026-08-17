# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ops and architecture companion. **Workflow source of truth is [AGENTS.md](AGENTS.md)** — read it before making any change. Deep mechanics live in [docs/REFERENCE.md](docs/REFERENCE.md); read that only when your task touches those systems.

## Response style — non-negotiable

- Keep replies to the user simple and easy to understand.
- Keep replies short — condense wherever possible, but never drop factual data.
- No open-ended questions, except the final "Other" option in a multiple-choice question.

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

Manual entry/resume: type **`/loop-in-devs`** for the full loop, or **`/orchestrator`** / **`/implementer`** / **`/reviewer`** to assume a single role in this session (`.claude/skills/<name>/`; Cursor twins under `.cursor/skills/` — keep both sides in sync). Cursor rules in `.cursor/rules/` (`agents-loop`, `agents-sdd`, `agents-model-policy`, plus per-surface action-sync/engine/viewer rules) reinforce the same contract.

## Commands

**Supported primary path — Docker** (foreground container in a titled `cmd` window; Ollama stays host-native):

```bash
docker build -t gitserv-sim .   # from repo root
```

Pre-create bind-mount targets before first run (empty `simulation/state.db` file, `simulation/memory_store.json` as `{}`, `simulation/logs/` directory). On Docker Desktop Windows, a missing mount path can become a **directory** and break SQLite/JSON. Omit `state.db-wal` / `state.db-shm` mounts unless those paths already exist as **files** on the host.

```powershell
Start-Process cmd.exe -ArgumentList '/k', 'title simserver && docker run --name gitserv-sim -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-sim' -WorkingDirectory $PWD
```

No `-d`, no `--restart` (provisional — see `.claude/plans/docker-phase3-soak-notes.md`). Then open `http://127.0.0.1:5001`.

**Native fallback** (when not using Docker):

```bash
uv sync
uv run python simulation/server.py   # then open http://127.0.0.1:5001
```

Titled-window native start (optional):

```powershell
Start-Process cmd.exe -ArgumentList '/k', 'title simserver && uv run python simulation\server.py' -WorkingDirectory $PWD
```

- **Single-instance rule:** multiple server instances (Docker container **or** native `simulation/server.py`) have repeatedly ended up running at once, all fighting over port 5001 and `state.db`. As the **last step of every implementation task** that starts, restarts, or touches the server, verify **at most one** is running before reporting done:
  ```powershell
  docker ps -a --filter name=gitserv-sim
  Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation.server' }
  ```
  Stop an existing container: `docker stop gitserv-sim && docker rm gitserv-sim`. Kill extra native processes (Bash: `pgrep -fa "simulation/server.py"`; close the `simserver` window). Note `uv run` shows a wrapper + interpreter pair; that parent/child pair is **one** native instance.
- Ollama must be running on the **host** at `http://localhost:11434` with the `sim-smart`/`sim-fast` models loaded (native `/api/chat`); canonical loader is `uv run python scripts/ollama_setup.py` (sets env vars, creates/warms both models, verifies dual residency — see [ollama_config.md](ollama_config.md)). In Docker, the container uses `SIM_OLLAMA_HOST=host.docker.internal:11434`. Without Ollama, decisions fall back to `rest` but the server stays up.
- Port is **5001** on purpose — open `http://127.0.0.1:5001`, never `index.html` as a file.
- **No linter or build step.** A thin `tests/` unit layer covers the action-sync invariant, the `normalize_decision`/`role_fallback_action` fallback ladder, and the save/restore round-trip — run it with `uv run pytest` (no Ollama, no running server, under a few seconds; see [specs/12-ops.md](specs/12-ops.md#unit-tests-tests-repo-root)). Beyond that, verify by running the server, watching the browser + JSONL logs. Deterministic smokes (no Ollama needed; run on the **host** with `uv run`): `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py`. `scripts/` also holds targeted smokes/soaks per subsystem (god mode, blueprints, daily council, hunting, log retention, succession, town integrity) — run the one that covers the surface you touched.
- Never commit credentials, `simulation/logs/`, or `simulation/state.db`.

## Architecture

**Server-authoritative**: the world runs headless in Python; the browser is a thin viewer holding no simulation state.

- **[simulation/sim_engine/](simulation/sim_engine/)** — the engine package (`SimEngine`, defined in `core.py`). Owns ALL world state, runs the 30/s tick loop, applies decisions via `apply_decision()`, runs every deterministic system, dispatches LLM think jobs to a bounded worker pool, persists to `simulation/state.db`. Module-level `constants.py`/`persistence.py`/`helpers.py` plus 22 `mixin_*.py` topic files are `exec()`'d into a shared namespace by `__init__.py` — a former 24,324-line single file, pure move split, no behavior change.
- **[simulation/server.py](simulation/server.py)** — Flask app + cognition entry point. Serves viewer/state/controls; `run_agent_decision()` prompts Ollama, extracts JSON. Directly-runnable, and the single source for every route, `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`. Imports non-route helper logic (`normalize_decision()`/`role_fallback_action()`, `SessionLogger`, `MemoryStore`, model routing, blueprint/role/sprite validation) from **[simulation/_server/](simulation/_server/)** — a sibling package split out of this former 6,192-line file (pure move, no behavior change); `import server` still re-exports every name external callers rely on.
- **[simulation/index.html](simulation/index.html)** — thin viewer shell (markup only).
- **[simulation/viewer/](simulation/viewer/)** — split viewer client script (16 files: `setup.js`, `state.js`, `render.js`, `sidebar.js`, `council.js`, `minimap.js`, `polling.js`, `controls.js`, `renderloop.js`, `divine-bootstrap.js`, `divine-auth-sight.js`, `divine-modal.js`, `divine-sight-voice.js`, `divine-voice.js`, `divine-miracles-story.js`, `divine-history.js`) — polling, render loop, sidebar, Divine Console.
- **[simulation/css/](simulation/css/)** — split viewer stylesheet (6 files: `base.css`, `panels.css`, `agents.css`, `council.css`, `divine.css`, `responsive.css`) — layout and panel chrome.
- **[simulation/sprites/](simulation/sprites/)** — split pure, stateless Canvas drawing (8 files: `core.js`, `tiles.js`, `props.js`, `structures.js`, `agents.js`, `world.js`, `wildlife.js`, `shipments.js`).
- **[simulation/roles.json](simulation/roles.json)** — **single source of truth for role definitions**; edit role data here, never in code maps.

Data flow: tick thread advances world → think timer fires → `_build_think_payload()` snapshots context under the lock → `run_agent_decision()` (server.py) → validated decision → `apply_decision()` mutates world under the lock. Browser only polls and renders.

## Critical invariants

- New actions must stay in sync across `DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT` (server.py), `apply_decision()` + payload `available_actions` (sim_engine/mixin_decisions.py, sim_engine/mixin_think_job.py), and `ACTION_LABELS` (viewer/sidebar.js, display only) — [specs/01-architecture.md](specs/01-architecture.md#action-sync-invariant), [specs/07-actions.md](specs/07-actions.md).
- The engine mutates world state only under its lock, and LLM calls stay outside it; full world persists to `simulation/state.db` (autosave + graceful-exit flush; `restore_state()` resumes old saves) — [specs/02-engine-core.md](specs/02-engine-core.md).
- `MAX_CONCURRENT_LLM = 3` (sim_engine/constants.py); Ollama's `num_ctx` must cover ~3,400 tokens × parallel slots (`uv run python scripts/ollama_setup.py` applies target config; see [ollama_config.md](ollama_config.md)) — [specs/03-cognition.md](specs/03-cognition.md).
- Viewer is a pure renderer — no decisions, movement, or mutation in the browser; keep `WORLD_W`/`WORLD_H` matched between `sim_engine/constants.py` and `viewer/setup.js` — [specs/11-viewer.md](specs/11-viewer.md).
- God routes (`/control/god/*`) are control endpoints, not decision actions; audited via `divine.jsonl` — [specs/12-ops.md](specs/12-ops.md).
- Core loop is the build pipeline: `start_project` → gather → contribute → `build_structure`, plus a blueprint flow where elder Sage approves new types; Sage's survival is protected by a deterministic emergency system — [specs/07-actions.md](specs/07-actions.md), [specs/02-engine-core.md](specs/02-engine-core.md#sage-emergency).
- **specs/ must always match the repo.** Any code change that alters behavior, actions, flags, routes, constants, or data shapes MUST update the owning spec in the same change (SDD: specs first, code second).

## Feature flags

~30 module-level flags in `simulation/sim_engine/constants.py`; most echoed to the viewer via `/state` `config.flags`. Complete index: [specs/01-architecture.md](specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy). Semantics per flag: [specs/02](specs/02-engine-core.md), [03](specs/03-cognition.md), [08](specs/08-systems-economy.md), [09](specs/09-systems-society.md), [10](specs/10-path1.md).

## Logs

Each server run writes to `simulation/logs/<timestamp>/` (gitignored): `activity.jsonl` (world events), `conversation.jsonl` (agent dialogue), `llm.jsonl` (full LLM request/response/decision per call; sessions predating the Ollama migration used `lm_studio.jsonl`), `benchmarks.jsonl` (Sid-parity metrics: specialization index, rule adherence, meme adoption, memory-store size, module-activation timeline), `divine.jsonl` (Sovereign God mode intervention audit trail — dark by default, see [specs/12-ops.md](specs/12-ops.md)), and `compiler.jsonl` (Sovereign God mode Optional Phase 8 free-prose compiler attempts — draft/rejection, dark by default behind its own `GOD_COMPILER_ENABLED` flag). Primary debugging surface — read `llm.jsonl` to see what the model actually returned and which fallback fired. Ollama's own log (token usage, per-request checkpoints) lives outside the repo at `%LOCALAPPDATA%\Ollama\server.log`.

## Docs map

- [AGENTS.md](AGENTS.md) — **AI workflow SoT** (loop, roles, models, SDD, constraints).
- [docs/HANDOFF.md](docs/HANDOFF.md) — **start here** when resuming: snapshot + narrative catch-up.
- [specs/00-overview.md](specs/00-overview.md) — index of the canonical, rebuildable 13-file spec set (`00`–`12`).
- [docs/REFERENCE.md](docs/REFERENCE.md) — historical-rationale pointers plus Ollama operational tips not already canonical in specs/03.
- [README.md](README.md) — human-facing setup/run instructions and the agent-roster override.
- [ollama_config.md](ollama_config.md) — target Ollama model/context configuration.
- `docs/archive/plan-*.md` — shipped implementation plans (living-ecosystem, living-world, agents-panel, sovereign-god-mode, visual polish, etc.); triage index at `docs/archive/plan-archive-triage-phase-b.md`.
- `docs/archive/` — **historical record only. Do not read or act on files there unless the user explicitly asks.**
