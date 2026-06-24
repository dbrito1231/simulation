# SPEC 02 — server.py (Flask Proxy)

**Build target.** Produces the complete `server.py`. Gate: GATE B.

## Dependencies

```
pip install flask flask-cors requests
```

No other libraries.

## Top-of-file comment (include verbatim)

```python
# HOW TO RUN:
# 1. pip install flask flask-cors requests
# 2. Make sure LM Studio is running at localhost:1234 with a model loaded
# 3. python server.py
# Server starts at http://localhost:5000
```

## The only route: POST /agent/think

### Request body (JSON, sent by the browser)

```json
{
  "agent_name": "Aria",
  "role": "farmer",
  "personality": "hardworking and cautious",
  "memory": ["collected food at farm", "talked to Marco"],
  "resources": { "food": 3, "wood": 1, "gold": 0 },
  "relationships": { "Marco": "ally", "Rex": "rival" },
  "nearby_agents": ["Marco", "Zara"],
  "world_zone": "farm",
  "available_actions": ["move_to_farm","move_to_market","move_to_forest","move_to_beach","move_to_village","move_to_cave","collect_resource","talk_to_nearby","trade_resource","change_role","rest"]
}
```

### Response body (JSON, returned to the browser)

```json
{
  "action": "talk_to_nearby",
  "target": "Marco",
  "message": "Want to trade food for wood?",
  "new_role": null,
  "relationship_update": null,
  "reasoning": "I have surplus food and need wood"
}
```

## System prompt sent to LM Studio (use verbatim)

```
You are an autonomous agent living in a small pixel-art simulation world.
Behave like a real human would, based on your role, relationships, resources,
and what is happening around you.

Respond with ONLY valid JSON. No markdown, no explanation, no extra text.
The JSON must match this structure exactly:
{
  "action": "<one of the available_actions>",
  "target": "<agent name, zone name, or null>",
  "message": "<what you say if talking, or null>",
  "new_role": "<a new role name if changing role, or null>",
  "relationship_update": {"<agent_name>": "ally|neutral|rival"} or null,
  "reasoning": "<one short sentence>"
}
```

## User prompt template (fill from request)

```
Your name: {agent_name}
Your role: {role}
Your personality: {personality}
Recent memory: {memory}
Resources: {resources}
Relationships: {relationships}
Agents near you: {nearby_agents}
Current zone: {world_zone}
Available actions: {available_actions}

What do you do next? Respond with only the JSON.
```

## LM Studio call settings

| Setting | Value |
|---------|-------|
| URL | `http://localhost:1234/v1/chat/completions` |
| model | `"local-model"` |
| max_tokens | 300 |
| temperature | 0.7 |
| stream | false |
| timeout | 30 seconds |

## Response parsing

1. Read the assistant message text from the LM Studio response.
2. Strip any markdown code fences (```json ... ```) if present.
3. `json.loads()` the cleaned text.
4. Return the parsed object to the browser as JSON.

## Error handling (always return HTTP 200)

| Failure | Returned JSON |
|---------|---------------|
| LM Studio unreachable | `{"error": "LM Studio offline", "action": "rest"}` |
| JSON parse fails | `{"error": "bad_response", "action": "rest"}` |
| Any other exception | `{"error": "server_error", "action": "rest"}` |

Never let the server crash the browser loop — every path returns a valid object with at least an `action`.

## CORS + run

```python
from flask_cors import CORS
CORS(app)
...
app.run(port=5000, debug=False)
```

## Anti-hallucination check for this spec

- Only one route exists: `POST /agent/think`.
- Only Flask, flask-cors, requests imported.
- No database, no file writes, no extra endpoints.
