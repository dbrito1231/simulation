# SPEC 01 — Architecture

## Tech Stack (fixed — do not substitute)

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | Vanilla HTML + JS + HTML5 Canvas | No frameworks, no external JS libraries |
| Proxy | Python 3.10+ with Flask | Handles CORS, talks to LM Studio |
| AI brain | LM Studio (OpenAI-compatible API) | Runs at `http://localhost:1234` |

## File Structure (exactly two files)

```
/simulation/
  server.py      ← Python Flask proxy
  index.html     ← Canvas frontend (world + agents + loop + UI)
```

No other files. No folders. No `requirements.txt`. If an extra file seems necessary, STOP and ask.

## Data Flow

```
Browser (index.html)
   │  POST /agent/think  (agent state as JSON)
   ▼
Flask proxy (server.py, port 5000)
   │  OpenAI-format chat completion request
   ▼
LM Studio (localhost:1234)
   │  JSON decision
   ▼
Flask proxy parses + cleans
   │  clean JSON decision
   ▼
Browser applies decision to agent
```

## Why a Python proxy (not direct browser → LM Studio)

Calling LM Studio directly from the browser triggers CORS errors. The Flask proxy sits in the middle, enabling CORS for the browser and forwarding cleanly to LM Studio. This matches a local-first, self-hosted setup.

## Ports

| Service | Port |
|---------|------|
| Flask proxy | 5000 |
| LM Studio | 1234 |

## Run Sequence

1. Start LM Studio with a model loaded (e.g. Qwen3 14B), server enabled on port 1234.
2. Start the Flask proxy: `python server.py`.
3. Open `index.html` in Chrome or Firefox. No build step, no bundler.

## Design Constraints

- **KISS:** simplest working version of every feature.
- **No assumptions:** anything not specified is a clarification question.
- **Stateless server:** the proxy holds no state; all agent state lives in the browser and is sent with each request.
- **Non-blocking:** agents call the LLM asynchronously and staggered, so one slow response never freezes the others.
