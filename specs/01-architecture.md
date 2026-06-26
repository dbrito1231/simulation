# SPEC 01 — Architecture

## Tech Stack (fixed — do not substitute)

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | Vanilla HTML + JS + HTML5 Canvas | No frameworks, no external JS libraries |
| Proxy | Python 3.10+ with Flask | Handles CORS, talks to LM Studio |
| AI brain | LM Studio (OpenAI-compatible API) | Runs at `http://localhost:1234` |

## File Structure

```
/simulation/
  server.py      ← Python Flask app: serves the frontend + proxies LM Studio
  index.html     ← Canvas frontend (world + agents + loop + sidebar UI)
  sprites.js     ← Pixel-art drawing helpers (terrain, agents, structures)
  logs/          ← Per-session JSONL logs, written at runtime (gitignored)
```

`sprites.js` was split out of `index.html` to keep the rendering helpers
reusable and readable. Flask serves both `index.html` (`/`) and `sprites.js`
(`/sprites.js`), so the browser loads the app from the server, not from a
`file://` URL.

## Data Flow

```
Browser (index.html + sprites.js, loaded from the Flask server)
   │  POST /agent/think  (agent + civilization state as JSON)
   ▼
Flask app (server.py, port 5001)
   │  OpenAI-format chat completion request
   ▼
LM Studio (localhost:1234)
   │  JSON decision
   ▼
Flask app parses + cleans + normalizes (role fallbacks, validation)
   │  clean JSON decision
   ▼
Browser applies decision to agent
```

## Why a Python proxy (not direct browser → LM Studio)

Calling LM Studio directly from the browser triggers CORS errors. The Flask app sits in the middle, enabling CORS for the browser and forwarding cleanly to LM Studio. It also serves the frontend so the page runs from an `http://` origin (a `file://` page cannot reach the relative `/agent/think` route). This matches a local-first, self-hosted setup.

## Ports

| Service | Port |
|---------|------|
| Flask app | 5001 |
| LM Studio | 1234 |

Port **5001** (not 5000) on purpose: macOS AirPlay occupies 5000 and returns 403.

## Run Sequence

1. Start LM Studio with a model loaded (e.g. Qwen3 14B), server enabled on port 1234.
2. Start the Flask app: `uv run python simulation/server.py`.
3. Open `http://127.0.0.1:5001` in Chrome or Firefox. No build step, no bundler.

## Design Constraints

- **KISS:** simplest working version of every feature.
- **No assumptions:** anything not specified is a clarification question.
- **Server holds no simulation state:** all agent and civilization state lives in the browser and is sent with each request. The server is otherwise stateless except for an append-only `SessionLogger` that writes per-session JSONL logs under `simulation/logs/`.
- **Non-blocking:** agents call the LLM asynchronously through a bounded-concurrency queue (up to 3 in flight), so one slow response never freezes the others.
