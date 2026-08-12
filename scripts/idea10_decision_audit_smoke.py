"""Deterministic smoke for idea-10 decision audit (Why did you do that?).

Covers matched pair, mismatched pair, _fallback exclusion before scoring,
and flag-off response shape. Ollama-free — reads a sample log directory via
the same read-jsonl idiom as soak_monitor.py.

Run: uv run python scripts/idea10_decision_audit_smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

from _server.decision_audit import build_decision_audit  # noqa: E402


def assert_true(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _llm_line(
    agent_name: str,
    frame_tick: int,
    action: str,
    reasoning: str,
    decision_id: str,
    *,
    fallback: bool = False,
) -> dict:
    decision = {
        "action": action,
        "reasoning": reasoning,
        "_decision_id": decision_id,
    }
    if fallback:
        decision["_fallback"] = True
    return {
        "type": "llm",
        "agent_name": agent_name,
        "frame_tick": frame_tick,
        "decision": decision,
    }


def _activity_line(message: str, frame_tick: int, decision_id: str) -> dict:
    return {
        "type": "activity",
        "message": message,
        "frame_tick": frame_tick,
        "decision_id": decision_id,
    }


def _sample_session(tmpdir: Path) -> tuple[Path, Path, str]:
    llm_path = tmpdir / "llm.jsonl"
    activity_path = tmpdir / "activity.jsonl"
    session_id = "2026-08-11T22-00-00"
    return llm_path, activity_path, session_id


def test_matched_pair(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "match001"
    write_jsonl(llm_path, [
        _llm_line("Sage", 100, "collect_resource", "I need to gather wood.", did),
    ])
    write_jsonl(activity_path, [
        _activity_line("Sage gathered wood.", 100, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    assert_true(result["enabled"] is True, "expected enabled")
    assert_true(len(result["agents"]) == 1, f"expected one agent, got {result['agents']}")
    agent = result["agents"][0]
    assert_true(agent["agent_name"] == "Sage", "agent name")
    assert_true(agent["scored"] == 1, f"scored count {agent}")
    assert_true(agent["matches"] == 1, f"matches {agent}")
    assert_true(agent["mismatches"] == 0, f"mismatches {agent}")
    assert_true(agent["mismatch_rate"] == 0.0, "mismatch_rate")
    assert_true(len(result["recent"]) == 1, "recent entry")
    assert_true(result["recent"][0]["score"] == "match", "recent score")
    assert_true(result["recent"][0]["reasoning_category"] == "gather", "category")
    print("  OK matched pair")


def test_mismatched_pair(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "mismatch001"
    write_jsonl(llm_path, [
        _llm_line("Mira", 200, "talk_to_nearby", "Time to gather more food.", did),
    ])
    write_jsonl(activity_path, [
        _activity_line("Mira talked to Ash.", 200, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    agent = result["agents"][0]
    assert_true(agent["scored"] == 1, f"scored {agent}")
    assert_true(agent["matches"] == 0, f"matches {agent}")
    assert_true(agent["mismatches"] == 1, f"mismatches {agent}")
    assert_true(agent["mismatch_rate"] == 1.0, "mismatch_rate")
    assert_true(result["recent"][0]["score"] == "mismatch", "recent score")
    print("  OK mismatched pair")


def test_fallback_excluded(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "fallback001"
    write_jsonl(llm_path, [
        _llm_line(
            "Tom", 300, "rest", "Deterministic fallback rest.",
            did, fallback=True,
        ),
        _llm_line("Tom", 301, "collect_resource", "Collect stone for the wall.", "real001"),
    ])
    write_jsonl(activity_path, [
        _activity_line("Tom rested.", 300, did),
        _activity_line("Tom collected stone.", 301, "real001"),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    agent = result["agents"][0]
    assert_true(agent["scored"] == 1, f"fallback must not score: {agent}")
    assert_true(agent["excluded_fallback"] == 1, f"excluded_fallback {agent}")
    assert_true(agent["matches"] == 1, "only non-fallback scored")
    assert_true(len(result["recent"]) == 1, "recent excludes fallback")
    assert_true(result["recent"][0]["decision_id"] == "real001", "real decision only")
    print("  OK fallback excluded before scoring")


def test_flag_off_shape(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    write_jsonl(llm_path, [
        _llm_line("Sage", 1, "rest", "rest", "ignored"),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=False)
    assert_true(result == {"enabled": False, "agents": [], "recent": []},
                f"flag-off shape {result}")
    print("  OK flag-off response shape")


def test_flag_off_full_view(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=False, view="full")
    assert_true(result == {
        "enabled": False, "agents": [], "recent": [], "entries": [],
    }, f"flag-off full shape {result}")
    print("  OK flag-off full view shape")


def test_default_no_full_keys(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "default001"
    write_jsonl(llm_path, [
        _llm_line("Sage", 100, "collect_resource", "I need to gather wood.", did),
    ])
    write_jsonl(activity_path, [
        _activity_line("Sage gathered wood.", 100, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    assert_true("entries" not in result, "default must not include entries")
    assert_true(len(result["agents"]) == 1, "expected one agent")
    agent = result["agents"][0]
    assert_true("outcome_ok" not in agent, "default agents must not have outcome_ok")
    assert_true("outcome_fail" not in agent, "default agents must not have outcome_fail")
    assert_true("outcome_unknown" not in agent, "default agents must not have outcome_unknown")
    print("  OK default payload has no full-view keys")


def test_full_view_fallback_entry(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "fallback-full"
    write_jsonl(llm_path, [
        _llm_line("Tom", 300, "rest", "Deterministic fallback rest.", did, fallback=True),
    ])
    write_jsonl(activity_path, [
        _activity_line("Tom rested.", 300, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True, view="full")
    assert_true("recent" not in result, "full view must omit recent")
    assert_true("entries" in result, "full view must include entries")
    assert_true(len(result["entries"]) == 1, f"expected one entry {result['entries']}")
    entry = result["entries"][0]
    assert_true(entry["intent"] == "fallback", f"intent {entry}")
    assert_true(entry["outcome"] == "unknown", f"outcome {entry}")
    assert_true(entry["activity_message"] is None,
                "fallback must not expose activity_message")
    print("  OK full view fallback entry")


def test_outcome_fail_cannot(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "fail001"
    write_jsonl(llm_path, [
        _llm_line("Mira", 400, "collect_resource", "Need to gather stone.", did),
    ])
    write_jsonl(activity_path, [
        _activity_line("Mira cannot gather stone here.", 400, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True, view="full")
    entry = result["entries"][0]
    assert_true(entry["outcome"] == "fail", f"expected fail outcome {entry}")
    print("  OK outcome fail on cannot ")


def test_outcome_ok_summary(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    did = "ok001"
    write_jsonl(llm_path, [
        _llm_line("Sage", 500, "collect_resource", "Time to gather wood.", did),
    ])
    write_jsonl(activity_path, [
        _activity_line("Sage heads to gather wood.", 500, did),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True, view="full")
    entry = result["entries"][0]
    assert_true(entry["outcome"] == "ok", f"expected ok outcome {entry}")
    print("  OK outcome ok on successful summary")


def test_outcome_unknown_missing_activity(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    write_jsonl(llm_path, [
        _llm_line("Ash", 600, "rest", "Need to gather berries.", "orphan-full"),
    ])
    write_jsonl(activity_path, [])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True, view="full")
    entry = result["entries"][0]
    assert_true(entry["intent"] == "uncorrelated", f"intent {entry}")
    assert_true(entry["outcome"] == "unknown", f"outcome {entry}")
    assert_true(entry["activity_message"] is None, "missing activity → null message")
    print("  OK outcome unknown and uncorrelated without activity")


def test_uncorrelated_and_unclassified(tmpdir: Path) -> None:
    llm_path, activity_path, session_id = _sample_session(tmpdir)
    write_jsonl(llm_path, [
        {
            "type": "llm",
            "agent_name": "Ash",
            "frame_tick": 10,
            "decision": {
                "action": "rest",
                "reasoning": "zzz xyzzy qwerty only",
                "_decision_id": "unclass-id",
            },
        },
        _llm_line("Ash", 11, "rest", "Need to gather berries.", "orphan-id"),
    ])
    write_jsonl(activity_path, [])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    assert_true(result["agents"] == [], "no scored agents")
    # Stats live on agents with scored>=1 only; verify via direct counts in logs
    # by adding a scored row and checking side buckets on that agent.
    write_jsonl(llm_path, [
        {
            "type": "llm",
            "agent_name": "Ash",
            "frame_tick": 10,
            "decision": {
                "action": "rest",
                "reasoning": "zzz xyzzy qwerty only",
                "_decision_id": "unclass-id",
            },
        },
        _llm_line("Ash", 11, "rest", "Need to gather berries.", "orphan-id"),
        _llm_line("Ash", 12, "collect_resource", "gather food", "good-id"),
    ])
    write_jsonl(activity_path, [
        _activity_line("Ash paused.", 10, "unclass-id"),
        _activity_line("Ash gathered food.", 12, "good-id"),
    ])
    result = build_decision_audit(
        str(llm_path), str(activity_path), session_id, enabled=True)
    agent = result["agents"][0]
    assert_true(agent["uncorrelated"] == 1, f"orphan-id uncorrelated {agent}")
    assert_true(agent["unclassified"] == 1, f"unclassified reasoning {agent}")
    assert_true(agent["scored"] == 1, f"one scored {agent}")
    print("  OK uncorrelated and unclassified buckets")


def main() -> None:
    print("idea10_decision_audit_smoke")
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        test_matched_pair(tmpdir)
        test_mismatched_pair(tmpdir)
        test_fallback_excluded(tmpdir)
        test_flag_off_shape(tmpdir)
        test_flag_off_full_view(tmpdir)
        test_default_no_full_keys(tmpdir)
        test_full_view_fallback_entry(tmpdir)
        test_outcome_fail_cannot(tmpdir)
        test_outcome_ok_summary(tmpdir)
        test_outcome_unknown_missing_activity(tmpdir)
        test_uncorrelated_and_unclassified(tmpdir)
    print("PASS")


if __name__ == "__main__":
    main()
