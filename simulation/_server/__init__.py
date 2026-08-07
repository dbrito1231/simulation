"""Sibling helper package for simulation/server.py (Phase 5 modularization,
pure move split, no behavior change -- see docs/plan-*.md for the phase plan
and specs/01-architecture.md for the module-layout summary).

server.py stays the real, directly-runnable entry point: the Flask `app`
object, every `@app.route`/`add_url_rule` handler, `DECISION_ACTIONS`,
`DECISION_SCHEMA`, `SYSTEM_PROMPT`/`SYSTEM_PROMPT_SLIM`, and the
`if __name__ == "__main__"` block all remain there. The non-route, mostly
pure helper logic server.py depends on (validation, prompt formatting,
memory store, session logging, model routing, structured-output error
parsing, role data) lives in this package's modules instead. server.py
imports every name it needs from here and re-exports them at module level
(via plain `from _server.x import y` statements) so existing external
callers (`import server; server.<name>`) are unaffected.
"""
