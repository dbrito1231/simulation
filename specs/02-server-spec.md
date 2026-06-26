# SPEC 02 — server.py (Flask App)

**Build target.** Produces the complete `server.py`. Gate: GATE B.

`server.py` does two jobs: it **serves the frontend** (`index.html`, `sprites.js`)
and it **proxies LM Studio** for agent decisions. It also persists per-session
logs and normalizes/validates model output before returning it to the browser.

## Dependencies

```
flask, flask-cors, requests   (installed via `uv sync`)
```

Only those third-party libraries. The standard-library modules `json`, `os`,
`re`, and `datetime` are also used (logging, JSON parsing, blueprint validation).

## Top-of-file comment (include verbatim)

```python
# HOW TO RUN:
# 1. uv sync   (installs flask, flask-cors, requests)
# 2. Make sure LM Studio is running at localhost:1234 with a model loaded
# 3. uv run python simulation/server.py
# 4. Open http://127.0.0.1:5001 in Chrome or Firefox
```

## Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Serve `index.html` |
| `/sprites.js` | GET | Serve the rendering helpers |
| `/agent/think` | POST | Core decision endpoint (LM Studio proxy) |
| `/log/event` | POST | Persist a browser-origin activity/conversation event |

The frontend is served from Flask so the page runs from an `http://` origin and
its relative `fetch("/agent/think")` resolves. There is no separate static host.

## POST /agent/think

### Request body (JSON, sent by the browser)

The browser sends agent state **plus** civilization context. Beyond name/role/
personality/memory/resources/relationships/nearby_agents/world_zone/
available_actions, the payload includes: `role_skill`, `civilization_level`,
`structures_built`, `active_project`, `project_progress`, `directive`,
`idle_agents`, `known_resources`, `pending_blueprints`,
`approved_custom_projects`, `rejected_blueprints`, `recent_conversations`,
`behavior_nudge`, and `frame_tick`. Missing fields default safely server-side.

### Response body (JSON, returned to the browser)

```json
{
  "action": "contribute_resources",
  "target": "wood",
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Donating wood to the active build.",
  "blueprint": null
}
```

`blueprint` is only present (and only retained) for a valid `propose_blueprint`.

## System prompt sent to LM Studio

The system prompt is the source of truth in `server.py` (`SYSTEM_PROMPT`). It is
**not** a short stub — it encodes the simulation's coordination rules. Keep these
elements when editing it:

- The shared goal: grow the village into a civilization by gathering, contributing
  to build projects, and coordinating.
- An **elder-only MAIN RULE**: every turn, if any agent is idle, `assign_task` to
  give that agent a job.
- Talk rules: never `talk_to_nearby` when nobody is near; talk requires both a
  nearby `target` and a `message`; prefer productive actions over idle talk.
- **Any agent may `start_project`** when none is active; everyone contributes/builds.
- The **blueprint** rules: any agent may `propose_blueprint` (optionally bundling
  up to 3 new gatherable resources); only the elder may `approve_blueprint` /
  `reject_blueprint`.
- A strict "respond with ONLY valid JSON, no chain-of-thought" instruction, the
  exact JSON shape (including the optional `blueprint` field), the blueprint
  object schema, and worked examples.

## User prompt template (filled from the request)

`USER_PROMPT_TEMPLATE` interpolates the full payload above (name, role, role_skill,
personality, memory, resources, relationships, nearby agents, current zone,
civilization level, structures built, active project, project progress, directive,
idle agents, known resources, pending blueprints, approved custom builds, rejected
blueprint ids, recent conversations, an optional behavior nudge, and available
actions), ending with "What do you do next? Respond with only the JSON."

## LM Studio call settings

| Setting | Value |
|---------|-------|
| URL | `http://localhost:1234/v1/chat/completions` |
| model | `"local-model"` |
| max_tokens | 512 |
| temperature | 0.4 |
| stream | false |
| thinking | `{"type": "disabled", "budget_tokens": 0}` (suppress reasoning models) |
| timeout | 30 seconds |

## Response parsing (`extract_json_decision`)

1. Read the assistant message text. Reasoning models may leave `content` empty —
   fall back to `reasoning_content`.
2. Strip markdown ```` ```json ```` fences if present.
3. `json.loads()` the cleaned text. If that fails, scan for the first balanced
   `{...}` object and parse it.
4. As a last resort, regex out `action`/`target`/`message` to build a minimal
   decision from a truncated response.
5. If nothing parses, return a role-appropriate fallback (see below).

## Decision normalization (`normalize_decision`)

After parsing, the decision is validated against the agent's real context and a
**deterministic fallback is substituted** for impossible actions:

- `talk_to_nearby` with no one nearby, or a missing/invalid `target`/`message` →
  role fallback (reasoning tagged "redirected from talk").
- `propose_blueprint` that fails `validate_blueprint` → role fallback.
- `approve_blueprint` / `reject_blueprint` by a non-elder, or targeting an id not in
  pending blueprints → role fallback.
- `assign_task` by a non-elder, or targeting a non-idle agent, or with no message →
  role fallback.
- Any other valid action passes through (the stray `blueprint` field is dropped).

`role_fallback_action` returns sensible work per role (elder approves a pending
blueprint or assigns idle agents; if no project is active, start one; gatherers
move to their zone and collect; builder contributes; etc.). Client and server
share this logic so they agree on recovery behavior.

## Logging (`SessionLogger`)

Each server run creates `simulation/logs/<ISO-timestamp>/` and appends JSON Lines:

| File | Contents |
|------|----------|
| `activity.jsonl` | World events (from `POST /log/event`) |
| `conversation.jsonl` | Agent dialogue (from `POST /log/event`) |
| `lm_studio.jsonl` | Full request/response/decision/latency/error per LLM call |

Logging must never break the simulation: `/log/event` swallows errors and returns
204; unknown event types are ignored.

## Error handling (always return HTTP 200)

| Failure | Returned JSON |
|---------|---------------|
| LM Studio unreachable | `{"error": "LM Studio offline", "action": "rest"}` |
| LM Studio "compute error" | `{"error": "compute_error", "action": "rest"}` |
| Bad/empty/unparseable model output | role fallback decision (logged as `bad_response`) |
| Any other exception | `{"error": "server_error", "action": "rest"}` |

Never let the server crash the browser loop — every path returns a valid object
with at least an `action`.

## CORS + run

```python
from flask_cors import CORS
CORS(app)
...
app.run(host="127.0.0.1", port=5001, debug=False)
```

## Anti-hallucination check for this spec

- Four routes exist: `GET /`, `GET /sprites.js`, `POST /agent/think`, `POST /log/event`.
- Only Flask, flask-cors, requests are third-party imports.
- The server writes JSONL logs under `simulation/logs/` but holds no simulation
  state — all agent/civilization state lives in the browser.
