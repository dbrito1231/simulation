"""Agent interview (idea-03): read-only, out-of-world operator Q&A over one
agent's own memory/relationships/beliefs. See specs/03-cognition.md ("Agent
interview") and specs/04-http-api.md ("Agent interview route") for the full
contract.

Zero mutation, no HTTP round-trip: `run_agent_interview()` reads
`agent["memory"]`/`agent["memoryWiki"]` directly plus `_agent_snapshot_row()`
(both in-process, under `engine.lock`), then makes exactly one LLM call
outside the lock. It never calls a mutating engine helper. Flags
(`AGENT_INTERVIEW_ENABLED`, `MEMORY_ENABLED`) and the model/pool are passed
in by the caller (server.py) rather than read from `sim_engine` here --
same "caller passes `enabled`" shape `_server/decision_audit.py` uses for
`DECISION_AUDIT_ENABLED`.
"""

from __future__ import annotations

from typing import Any


def _validate_question(question: Any, max_chars: int) -> tuple[str | None, str | None]:
    """Reject (never truncate) a missing/non-string/empty/oversized question.
    Returns (cleaned_question, None) on success or (None, reason) on failure."""
    if not isinstance(question, str) or not question.strip():
        return None, "question must be a non-empty string"
    if len(question) > max_chars:
        return None, f"question must be at most {max_chars} characters"
    return question.strip(), None


def _read_agent_context(engine, agent_id: int) -> tuple[dict | None, str | None]:
    """One in-process, lock-held read of the requested agent's snapshot row
    plus its private memory/memoryWiki fields. Never mutates anything.
    Returns (context, None) or (None, reason)."""
    with engine.lock:
        agent = engine._find_agent_by_id(agent_id)
        if not agent or agent.get("deathFrame") is not None:
            return None, "unknown or deceased agent"
        snapshot = engine._agent_snapshot_row(agent)
        memory = agent.get("memory") or {}
        memory_copy = {
            "working": list(memory.get("working") or []),
            "shortTerm": list(memory.get("shortTerm") or []),
            "longTerm": list(memory.get("longTerm") or []),
        }
        memory_wiki_copy = dict(agent.get("memoryWiki") or {})
        return {
            "agentId": agent["id"],
            "agentName": agent["name"],
            "snapshot": snapshot,
            "memory": memory_copy,
            "memoryWiki": memory_wiki_copy,
        }, None


def _build_prompt(context: dict, question: str) -> tuple[str, str]:
    """Pure string assembly -- no engine or network access. The prompt is
    scoped strictly to this agent's own beliefs/relationships/memory/
    memoryWiki (idea text: "generated strictly from that agent's memory
    store, relationships, and beliefs")."""
    snapshot = context["snapshot"]
    name = context["agentName"]
    role = snapshot.get("role")
    beliefs = "; ".join(snapshot.get("beliefs") or []) or "none recorded"
    relationships = snapshot.get("relationships") or {}
    rel_text = "; ".join(f"{k}: {v}" for k, v in relationships.items()) or "none recorded"

    mem = context["memory"]
    mem_lines = []
    for tier in ("longTerm", "shortTerm", "working"):
        entries = mem.get(tier) or []
        if entries:
            mem_lines.append(f"{tier}: " + "; ".join(str(e) for e in entries[-8:]))
    memory_text = "\n".join(mem_lines) or "no memories recorded yet"

    wiki = context["memoryWiki"] or {}
    wiki_lines = [f"{k}: {v}" for k, v in wiki.items() if v]
    wiki_text = "\n".join(wiki_lines) or "no compounded memory yet"

    system_prompt = (
        f"You ARE {name}, a {role} in this village. An out-of-world operator "
        "is interviewing you. Answer their question in first person, using "
        "ONLY what you would actually know from your own memory, "
        "relationships, and beliefs given below. Do not invent facts outside "
        "this context. Keep the answer concise (a few sentences)."
    )
    user_prompt = (
        f"Your beliefs: {beliefs}\n"
        f"Your relationships: {rel_text}\n"
        f"Your memory:\n{memory_text}\n"
        f"Your compounded memory notes:\n{wiki_text}\n\n"
        f"Operator's question: {question}\n"
        f"{name}'s answer:"
    )
    return system_prompt, user_prompt


def run_agent_interview(engine, agent_id, question, *, enabled: bool,
                         memory_enabled: bool, wiki_memory_enabled: bool,
                         max_chars: int, model: str,
                         lm_complete_fn, semaphore, timeout: float = 30,
                         acquire_timeout: float = 1.0) -> dict:
    """Full /agent/interview contract, Ollama-free-testable (lm_complete_fn is
    injected). Returns the exact HTTP response body shape.

    Order: flag gate -> agentId type -> question validation -> MEMORY_ENABLED
    gate -> WIKI_MEMORY gate -> agent lookup (lock-held) -> prompt build ->
    ONE LLM call bounded by `semaphore` (a pool independent of
    MAX_CONCURRENT_LLM/PIANO_CONCURRENT_LLM -- see specs/03-cognition.md).
    Never mutates world state; never calls a mutating engine helper."""
    if not enabled:
        return {"ok": False, "reason": "agent interview disabled"}
    if not isinstance(agent_id, int) or isinstance(agent_id, bool):
        return {"ok": False, "reason": "agentId must be an integer"}
    clean_question, reason = _validate_question(question, max_chars)
    if reason:
        return {"ok": False, "reason": reason}
    if not memory_enabled:
        # specs/03-cognition.md "Clean-error degrade when memory is
        # unavailable": refuse rather than answer from relationships/beliefs
        # alone.
        return {"ok": False, "reason": "agent memory unavailable (MEMORY_ENABLED is off)"}
    if not wiki_memory_enabled:
        # Same clean-error contract, other half of the flag pair: refuse
        # rather than answer with an empty memoryWiki section.
        return {"ok": False, "reason": "agent memory wiki unavailable (WIKI_MEMORY is off)"}

    context, reason = _read_agent_context(engine, agent_id)
    if reason:
        return {"ok": False, "reason": reason}

    system_prompt, user_prompt = _build_prompt(context, clean_question)

    if not semaphore.acquire(timeout=acquire_timeout):
        return {
            "ok": False,
            "reason": "agent interview capacity unavailable; try again shortly",
        }
    try:
        raw_text = lm_complete_fn(system_prompt, user_prompt, model=model,
                                   max_tokens=300, temperature=0.6, timeout=timeout)
    finally:
        semaphore.release()

    if not raw_text or not raw_text.strip():
        return {"ok": False,
                "reason": "the interview model produced no output (timeout or empty response)"}

    return {
        "ok": True,
        "agentId": context["agentId"],
        "agentName": context["agentName"],
        "answer": raw_text.strip(),
    }
