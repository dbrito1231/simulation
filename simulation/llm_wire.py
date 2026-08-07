"""Shared wire-format conversion between this repo's internal
OpenAI-chat-completions-shaped LLM payloads and Ollama's native /api/chat
request body.

Split out of server.py (2026-07-24, docs/archive/plan-ollama-migration.md Phase 4)
so scripts/llm_replay_bench.py can build real Ollama request bodies from
logged (LM-Studio-shaped) request payloads without duplicating this mapping
or importing server.py itself -- server.py has module-level side effects on
import (SessionLogger() opens a new simulation/logs/<timestamp>/ session
directory; the live SimEngine is constructed against state.db further down
the module), so importing it from a benchmark script would create stray
session directories / touch persisted state just by importing it. This
module has no such side effects -- it is pure functions only.

server.py imports to_ollama_body from this module; this module is now the
single source of truth for the conversion (specs/03-cognition.md,
specs/12-ops.md). Do not duplicate this mapping back into server.py or any
script.
"""


def to_ollama_body(payload):
    """Convert the internal OpenAI-chat-completions-shaped payload this
    repo builds (model, messages, max_tokens, temperature, sampling keys,
    an optional response_format, an optional boolean `think`) into an Ollama
    native /api/chat request body:
      - messages: pass through unchanged.
      - max_tokens -> options.num_predict.
      - temperature/top_p/top_k/min_p/presence_penalty -> options.*.
      - response_format (json_schema nesting, see server.py's
        build_response_format) -> format: the extracted schema object;
        json_object -> format: "json".
      - think: passed through as-is under its own Ollama-native name (False
        suppresses reasoning entirely -- Phase 0 finding #4 -- callers set it
        by putting payload["think"] = False; omitting the key lets the model
        think, matching Ollama's own default semantics, so no translation is
        needed for this one field)."""
    options = {}
    for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty"):
        if key in payload:
            options[key] = payload[key]
    if "max_tokens" in payload:
        options["num_predict"] = payload["max_tokens"]
    body = {
        "model": payload["model"],
        "messages": payload["messages"],
        "stream": False,
        "options": options,
    }
    if "think" in payload:
        body["think"] = payload["think"]
    response_format = payload.get("response_format")
    if response_format:
        schema = None
        if isinstance(response_format, dict):
            if response_format.get("type") == "json_schema":
                schema = (response_format.get("json_schema") or {}).get("schema")
            elif response_format.get("type") == "json_object":
                schema = "json"
        if schema:
            body["format"] = schema
    return body
