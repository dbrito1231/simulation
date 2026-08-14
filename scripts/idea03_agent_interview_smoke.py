"""Deterministic smoke for idea-03 agent interview (operator Q&A, out-of-
world debug surface).

Covers the /agent/interview route's core contract via
_server.agent_interview.run_agent_interview() -- the exact function the
Flask route (simulation/server.py) calls, with lm_complete injected so no
Ollama/network call ever happens. Exercises: response shape, the
zero-mutation contract (full engine world-state byte-identical before/after
a call), AGENT_INTERVIEW_ENABLED flag-off no-op, the
INTERVIEW_QUESTION_MAX_CHARS reject path, an unknown/deceased agent
rejection, and -- required per plan Sec 2 Answer 8 -- the MEMORY_ENABLED=False
and WIKI_MEMORY=False clean-error cases (must NOT degrade to answering from
relationships/beliefs alone, or with an empty memoryWiki section).

Run: uv run python scripts/idea03_agent_interview_smoke.py
"""

from __future__ import annotations

import copy
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

from _server.agent_interview import run_agent_interview  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402

MAX_CHARS = 500


def _semaphore():
    return threading.BoundedSemaphore(1)


def _poison_lm_complete(*args, **kwargs):
    raise AssertionError("lm_complete must not be called on this path")


def _seed_agent(engine):
    agent = engine.agents[0]
    agent["memory"] = {
        "working": ["saw Ash gathering wood"],
        "shortTerm": ["helped build the granary"],
        "longTerm": ["founded the village with Ash"],
    }
    agent["memoryWiki"] = {
        "relationships": "trusts Ash",
        "goals": "finish the granary",
        "lessons": "store food before winter",
    }
    return agent


def test_success_shape_and_pool_bound():
    engine = make_engine(4)
    agent = _seed_agent(engine)
    calls = []

    def fake_lm_complete(system_prompt, user_prompt, model=None, **kwargs):
        calls.append((model, system_prompt, user_prompt))
        return "I trust Ash and I'm helping finish the granary."

    result = run_agent_interview(
        engine, agent["id"], "Who do you trust in the village?",
        enabled=True, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=fake_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(result["ok"] is True, f"expected ok, got {result}")
    assert_true(set(result.keys()) == {"ok", "agentId", "agentName", "answer"},
                f"unexpected response shape: {result}")
    assert_true(result["agentId"] == agent["id"], "agentId mismatch")
    assert_true(result["agentName"] == agent["name"], "agentName mismatch")
    assert_true(isinstance(result["answer"], str) and result["answer"],
                "answer must be a non-empty string")
    assert_true(len(calls) == 1, f"expected exactly one LLM call, got {len(calls)}")
    assert_true(calls[0][0] == "sim-smart", "must route to sim-smart, not sim-fast")
    print("  OK success response shape + sim-smart routing")


def test_capacity_timeout_clean_error():
    engine = make_engine(4)
    agent = _seed_agent(engine)
    semaphore = _semaphore()
    semaphore.acquire()
    started = time.monotonic()
    try:
        result = run_agent_interview(
            engine, agent["id"], "Who do you trust?",
            enabled=True, memory_enabled=True, wiki_memory_enabled=True,
            max_chars=MAX_CHARS, model="sim-smart",
            lm_complete_fn=_poison_lm_complete, semaphore=semaphore,
            acquire_timeout=0.01,
        )
    finally:
        semaphore.release()
    elapsed = time.monotonic() - started
    assert_true(result["ok"] is False and "capacity unavailable" in result["reason"],
                f"capacity error shape: {result}")
    assert_true(elapsed < 1.0, f"capacity wait was not bounded: {elapsed:.3f}s")
    print("  OK occupied interview slot returns a bounded clean error")


def test_zero_mutation():
    engine = make_engine(4)
    agent = _seed_agent(engine)
    before = copy.deepcopy((engine.agents, engine.civilization))
    result = run_agent_interview(
        engine, agent["id"], "What do you remember about the founding?",
        enabled=True, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=lambda *a, **k: "We founded it together.",
        semaphore=_semaphore(),
    )
    after = copy.deepcopy((engine.agents, engine.civilization))
    assert_true(result["ok"] is True, f"expected ok for mutation baseline: {result}")
    assert_true(before == after, "engine world state must be byte-identical before/after an interview call")
    print("  OK zero-mutation contract (agents + civilization unchanged)")


def test_flag_off_no_op():
    engine = make_engine(4)
    agent = _seed_agent(engine)
    result = run_agent_interview(
        engine, agent["id"], "Any question at all",
        enabled=False, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(result == {"ok": False, "reason": "agent interview disabled"},
                f"flag-off shape: {result}")
    print("  OK AGENT_INTERVIEW_ENABLED=False no-op (no LLM call)")


def test_question_cap_reject():
    engine = make_engine(4)
    agent = _seed_agent(engine)
    too_long = "x" * (MAX_CHARS + 1)
    result = run_agent_interview(
        engine, agent["id"], too_long,
        enabled=True, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(result["ok"] is False, f"expected reject: {result}")
    assert_true(str(MAX_CHARS) in result["reason"], f"reason should name the cap: {result}")
    print("  OK INTERVIEW_QUESTION_MAX_CHARS reject path (no LLM call)")


def test_unknown_and_deceased_agent_rejected():
    engine = make_engine(4)
    _seed_agent(engine)
    unknown = run_agent_interview(
        engine, 999999, "hello",
        enabled=True, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(unknown == {"ok": False, "reason": "unknown or deceased agent"},
                f"unknown agent shape: {unknown}")

    deceased = engine.agents[1]
    deceased["deathFrame"] = 42
    dead_result = run_agent_interview(
        engine, deceased["id"], "hello",
        enabled=True, memory_enabled=True, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(dead_result == {"ok": False, "reason": "unknown or deceased agent"},
                f"deceased agent shape: {dead_result}")
    print("  OK unknown/deceased agent rejected (no LLM call)")


def test_memory_disabled_clean_error():
    """Required per plan Sec 2 Answer 8: MEMORY_ENABLED=False must return a
    clean error and make NO LLM call -- it must NOT degrade to answering
    from relationships/beliefs alone."""
    engine = make_engine(4)
    agent = _seed_agent(engine)
    result = run_agent_interview(
        engine, agent["id"], "Who do you trust?",
        enabled=True, memory_enabled=False, wiki_memory_enabled=True, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(result["ok"] is False, f"expected clean error: {result}")
    assert_true("MEMORY_ENABLED" in result["reason"],
                f"reason should name MEMORY_ENABLED: {result}")
    print("  OK MEMORY_ENABLED=False clean error, no partial-context fallback, no LLM call")


def test_wiki_memory_disabled_clean_error():
    """Required per plan Sec 2 Answer 8 (covers both MEMORY_ENABLED and
    WIKI_MEMORY): WIKI_MEMORY=False must return a clean error and make NO
    LLM call -- it must NOT silently degrade to answering with an empty
    memoryWiki section."""
    engine = make_engine(4)
    agent = _seed_agent(engine)
    result = run_agent_interview(
        engine, agent["id"], "Who do you trust?",
        enabled=True, memory_enabled=True, wiki_memory_enabled=False, max_chars=MAX_CHARS,
        model="sim-smart", lm_complete_fn=_poison_lm_complete,
        semaphore=_semaphore(),
    )
    assert_true(result["ok"] is False, f"expected clean error: {result}")
    assert_true("WIKI_MEMORY" in result["reason"],
                f"reason should name WIKI_MEMORY: {result}")
    print("  OK WIKI_MEMORY=False clean error, no partial-context fallback, no LLM call")


def main():
    print("idea03_agent_interview_smoke")
    test_success_shape_and_pool_bound()
    test_capacity_timeout_clean_error()
    test_zero_mutation()
    test_flag_off_no_op()
    test_question_cap_reject()
    test_unknown_and_deceased_agent_rejected()
    test_memory_disabled_clean_error()
    test_wiki_memory_disabled_clean_error()
    print("PASS")


if __name__ == "__main__":
    main()
