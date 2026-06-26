# Simulation

A real-time, browser-based AI village simulation where a local LLM acts as the brain for each inhabitant. Twelve autonomous agents move, talk, trade, gather resources, and propose build projects in a top-down pixel-art world.

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

   > **Context length vs. parallel slots:** the app queues up to `MAX_CONCURRENT_LLM`
   > (3, see `simulation/index.html`) requests at once, and each request's prompt is
   > ~1500 tokens. LM Studio divides its configured context length across its
   > parallel slots, so if `context length ÷ parallel slots` is smaller than that,
   > you'll see `"Context size has been exceeded"` errors under load (the app
   > recovers gracefully, but agents lose a turn). Set LM Studio's context length to
   > at least `1600 × parallel slots` (e.g. 8K context for 4 slots), and make sure
   > LM Studio's parallel-slot/concurrency setting is at least 3. If you can't raise
   > the context length, lower `MAX_CONCURRENT_LLM` in `simulation/index.html` instead.

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
| `simulation/server.py` | Flask API, agent logic, LM Studio integration |
| `simulation/index.html` | Browser client and render loop |
| `simulation/sprites.js` | Pixel-art drawing helpers |
| `specs/` | Architecture and feature specifications |

## Specs

See [`specs/00-overview.md`](specs/00-overview.md) for goals, scope, and design context.
