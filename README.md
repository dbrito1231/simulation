# Simulation

A server-authoritative AI village simulation where a local LLM acts as the brain for each inhabitant. 8 autonomous agents move, talk, trade, gather resources, and propose build projects in a top-down pixel-art world by default — up to 12, via a roster override (`{"agents": N}` JSON body on `POST /control/reset`, or the `SIM_AGENTS` env var).

Inspired by the multi-agent civilization research in Project Sid, kept intentionally minimal: a proof-of-concept for the LLM-as-brain loop.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [Ollama](https://ollama.com/) running locally with the `sim-smart`/`sim-fast` models created (`uv run python scripts/ollama_setup.py`)

## Setup

```bash
uv sync
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install flask flask-cors requests
```

## Run

1. Start Ollama and run the setup script, which creates/warms the `sim-smart`/`sim-fast` models and sets the required env vars. The server targets native `http://localhost:11434/api/chat`.

   ```bash
   uv run python scripts/ollama_setup.py
   ```

   > **Context length vs. parallel slots:** the engine queues up to `MAX_CONCURRENT_LLM`
   > (3, `simulation/sim_engine/constants.py`) think requests at once, and each request's prompt is
   > ~3,100 tokens. Ollama divides `num_ctx` across `OLLAMA_NUM_PARALLEL` slots, so if
   > `num_ctx ÷ parallel` is smaller than that, requests risk the `exceed_context_size_error`
   > overflow response under load (the app recovers gracefully with a slimmed-prompt retry,
   > but agents can still lose a turn). `ollama/Modelfile.smart` ships `num_ctx 20480` at
   > `OLLAMA_NUM_PARALLEL=3` (~6,827 tokens/slot) — `scripts/ollama_setup.py` applies this
   > canonical target config directly. If you can't raise `num_ctx`, lower
   > `MAX_CONCURRENT_LLM` in `simulation/sim_engine/constants.py` instead. Full detail:
   > [specs/03-cognition.md](specs/03-cognition.md).

2. Start the simulation server:

```bash
uv run python simulation/server.py
```

3. Open http://127.0.0.1:5001 in Chrome or Firefox.

> macOS AirPlay uses port 5000 and can return 403 — this project uses port **5001** on purpose.

Each server run writes session logs under `simulation/logs/` (gitignored).

## Project layout

| Path | Purpose |
|------|---------|
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
