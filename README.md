# Simulation

A server-authoritative AI village simulation where a local LLM acts as the brain for each inhabitant. 8 autonomous agents move, talk, trade, gather resources, and propose build projects in a top-down pixel-art world by default — up to 12, via a roster override (`{"agents": N}` JSON body on `POST /control/reset`, or the `SIM_AGENTS` env var).

Inspired by the multi-agent civilization research in Project Sid; started as a proof-of-concept of the LLM-as-brain loop and has grown well past that: ~37,700 lines of Python (engine + server), ~13,800 lines of viewer JS/CSS/sprites, 66 feature flags, 47 agent actions, 67 HTTP routes, 43 God-mode command kinds, 35 smoke/soak scripts, 13 spec files. It still holds to a minimal-and-observable *design bar* — every mechanic must be debuggable from JSONL logs and `/state`, not just from behavior — but the implementation itself is no longer small.

## Start here

1. Run it (below) and open `http://127.0.0.1:5001`.
2. Read [specs/00-overview.md](specs/00-overview.md) for goals, scope, and the spec index.
3. Everything else is reference — [docs/README.md](docs/README.md) indexes the rest.

## Prerequisites

- **Windows only.** The commands below are PowerShell/`cmd` — a deliberate scope decision, not an oversight. There is no supported Linux/macOS path.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (supported primary run path)
- [Ollama](https://ollama.com/) running **on the host** (not containerized) with `sim-smart`/`sim-fast` models — run on the host: `uv run python scripts/ollama_setup.py` (see [ollama_config.md](ollama_config.md)). `sim-smart` build sources a local GGUF where available, or pulls `qwen3.5:9b` from the registry when no local GGUF is present (~5 GB download, first run only)
- Port **5001** free (macOS AirPlay uses port 5000 and can return 403 — this project uses **5001** on purpose)
- For **native fallback** only: Python 3.12+ and [uv](https://docs.astral.sh/uv/) (also needed for host-side `scripts/` tools either way)

## Run (Docker — supported)

From the repo root:

1. Build the image:

   ```bash
   docker build -t gitserv-sim .
   ```

2. **Pre-create bind-mount targets** before the first run. On Docker Desktop Windows, mounting a missing path can create a **directory** instead of a file and break SQLite/JSON:

   ```powershell
   New-Item -ItemType Directory -Force simulation\logs
   New-Item -ItemType File -Force simulation\state.db
   Set-Content simulation\memory_store.json '{}'
   ```

   Omit `-v` mounts for `state.db-wal` / `state.db-shm` unless those paths already exist as **files** on the host.

3. Confirm at most one server instance (container **or** native Python) — see [CLAUDE.md](CLAUDE.md#commands) for the full check/stop recipe.

4. Start in a titled `cmd` window (foreground; no `-d`, no `--restart`):

   ```powershell
   Start-Process cmd.exe -ArgumentList '/k', 'title simserver && docker run --name gitserv-sim -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-sim' -WorkingDirectory $PWD
   ```

5. Open http://127.0.0.1:5001 in Chrome or Firefox.

Ollama stays host-native; the container reaches it via `SIM_OLLAMA_HOST=host.docker.internal:11434` (host:port only — see [specs/03-cognition.md](specs/03-cognition.md)).

## Run natively (fallback)

```bash
uv sync
uv run python scripts/ollama_setup.py
uv run python simulation/server.py
```

Then open http://127.0.0.1:5001. For the titled-window restart convention and combined single-instance checks, see [CLAUDE.md](CLAUDE.md#commands).

> **Context length vs. parallel slots:** the engine queues up to `MAX_CONCURRENT_LLM` (3) think requests at once (~3,100 tokens each). `scripts/ollama_setup.py` applies the canonical Ollama config (`num_ctx` ÷ parallel must cover each slot). Full detail: [specs/03-cognition.md](specs/03-cognition.md), [ollama_config.md](ollama_config.md).

## Logs and state

`state.db`, `memory_store.json`, and `simulation/logs/` are bind-mounted to the same host paths in Docker — no `docker cp` needed for debugging. Each new server process (container start or native launch) creates a new session folder under `simulation/logs/<timestamp>/` (gitignored). See [specs/12-ops.md](specs/12-ops.md).

## Project layout

| Path | Purpose |
|------|---------|
| `Dockerfile` | Single-container image for the Flask server (host-native Ollama via `SIM_OLLAMA_HOST`) |
| `.dockerignore` | Build context exclusions (logs, `state.db`, etc.) |
| `simulation/sim_engine/*.py` | The engine package — all world state, 30/s tick loop, `apply_decision`, persistence (`core.py` + `constants.py`/`persistence.py`/`helpers.py` + 22 `mixin_*.py` topic files) |
| `simulation/server.py` | Flask API entry point — every route, `DECISION_ACTIONS`/`DECISION_SCHEMA`, prompt building, Ollama integration |
| `simulation/_server/*.py` | Non-route helper modules server.py imports from — decision/blueprint/role/sprite validation, prompt formatting, memory store, session logging, model routing |
| `simulation/index.html` | Browser client shell (markup only) |
| `simulation/viewer/*.js` | Split viewer client script — polling, render loop, sidebar, Divine Console (16 files) |
| `simulation/sprites/*.js` | Pixel-art drawing helpers (8 files) |
| `simulation/css/*.css` | Split viewer stylesheet (6 files) |
| `simulation/roles.json` | Single source of truth for role definitions |
| `specs/` | Architecture and feature specifications |

## Specs

See [`specs/00-overview.md`](specs/00-overview.md) for goals, scope, and design context.
