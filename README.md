# Simulation

A server-authoritative AI village simulation where a local LLM acts as the brain for each inhabitant. 8 autonomous agents move, talk, trade, gather resources, and propose build projects in a top-down pixel-art world by default — up to 12, via a roster override (`{"agents": N}` JSON body on `POST /control/reset`, or the `SIM_AGENTS` env var).

Inspired by the multi-agent civilization research in Project Sid, kept intentionally minimal: a proof-of-concept for the LLM-as-brain loop.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- [LM Studio](https://lmstudio.ai/) running locally with a model loaded

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

1. Start LM Studio and load a model. The server expects the OpenAI-compatible API at `http://localhost:1234`.

   > **Context length vs. parallel slots:** the engine queues up to `MAX_CONCURRENT_LLM`
   > (3, `simulation/sim_engine.py`) think requests at once, and each request's prompt is
   > ~3,100 tokens. LM Studio divides its configured context length across its
   > parallel slots, so if `context length ÷ parallel slots` is smaller than that,
   > you'll see `"Context size has been exceeded"` errors under load (the app
   > recovers gracefully with a slimmed-prompt retry, but agents can still lose a
   > turn). Set LM Studio's context length to at least `3400 × parallel slots`, and
   > make sure LM Studio's parallel-slot/concurrency setting is at least 3 — or run
   > `uv run python scripts/lms_load.py` to apply the canonical target config
   > directly. If you can't raise the context length, lower `MAX_CONCURRENT_LLM` in
   > `simulation/sim_engine.py` instead. Full detail: [specs/03-cognition.md](specs/03-cognition.md).

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
| `simulation/sim_engine.py` | The engine — all world state, 30/s tick loop, `apply_decision`, persistence |
| `simulation/server.py` | Flask API, prompt building, LM Studio integration, decision validation |
| `simulation/index.html` | Browser client and render loop |
| `simulation/sprites.js` | Pixel-art drawing helpers |
| `simulation/roles.json` | Single source of truth for role definitions |
| `specs/` | Architecture and feature specifications |

## Specs

See [`specs/00-overview.md`](specs/00-overview.md) for goals, scope, and design context.
