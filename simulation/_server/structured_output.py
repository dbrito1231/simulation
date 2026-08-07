"""Ollama /api/chat error-body parsing helpers, split out of server.py
(Phase 5 modularization, pure move, no behavior change).

build_response_format itself stays in server.py: it directly builds on
DECISION_SCHEMA, which the action-sync invariant (specs/01-architecture.md)
requires stay in server.py, and it reads the `_structured_output_enabled`
module-level flag that run_agent_decision (also staying in server.py)
mutates via `global` on the auto-degrade path -- keeping both in the same
module avoids splitting that mutable flag's read/write across two module
namespaces. These three functions have no such coupling: they only parse an
already-received Ollama error body, so they move cleanly.
"""


def _ollama_error_parts(lm_body):
    """Extract (message, type) from an Ollama /api/chat error body. Modern
    Ollama (0.32.3, per Phase 0 finding #5) returns a structured
    {"error": {"code":, "message":, "type":, ...}} object on HTTP 400s (e.g.
    type "exceed_context_size_error"); tolerate a bare string too in case a
    future/older build differs."""
    err = lm_body.get("error") if isinstance(lm_body, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err), err.get("type")
    return str(err or ""), None


def looks_like_model_not_found_error(http_status, lm_body):
    """True when Ollama rejected the request because the requested model id
    isn't created/pulled (as opposed to any other error) -- a setup failure,
    not a transient condition (see run_agent_decision's handling)."""
    text, _ = _ollama_error_parts(lm_body) if isinstance(lm_body, dict) else ("", None)
    low = text.lower()
    return bool(low) and "model" in low and any(
        k in low for k in ("not found", "no model", "failed to load", "unknown model"))


def looks_like_response_format_error(http_status, lm_body):
    """True when Ollama rejected the request specifically over the `format`
    (JSON-schema) field. Rare in practice but the auto-degrade safety net
    stays in place regardless."""
    text, _ = _ollama_error_parts(lm_body) if isinstance(lm_body, dict) else ("", None)
    if http_status == 400 or text:
        low = text.lower()
        return any(k in low for k in ("response_format", "format", "json_schema", "grammar", "schema"))
    return False
