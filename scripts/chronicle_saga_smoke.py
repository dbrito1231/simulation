"""Deterministic no-Ollama smoke for Chronicle Saga (Phase 1 plumbing + Phase 2 dispatch).

Run:
    uv run python scripts/chronicle_saga_smoke.py

The result is always written to scripts/out/chronicle_saga_smoke.json.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _server.logging_session import read_conversation_window  # noqa: E402

OUT_PATH = ROOT / "scripts" / "out" / "chronicle_saga_smoke.json"
SAGA_WAIT_TIMEOUT_S = 2.0


def _load_roles():
    with open(ROOT / "simulation" / "roles.json", encoding="utf-8") as fh:
        return json.load(fh)


def _resource_roles(roles):
    out = {}
    for role, definition in roles.items():
        for resource in definition.get("specialty") or []:
            out.setdefault(resource, []).append(role)
    return {resource: tuple(names) for resource, names in out.items()}


def make_engine(extra_deps=None, roster_size=8):
    roles = _load_roles()
    deps = {
        "ROLES": roles,
        "ROLE_PROJECT": {
            role: (definition.get("preferredProject")[0]
                   if isinstance(definition.get("preferredProject"), list)
                   else definition.get("preferredProject"))
            for role, definition in roles.items()
        },
        "ROLE_SKILLS": {
            role: definition.get("skill", "helps")
            for role, definition in roles.items()
        },
        "ROLE_PRIMARY_RESOURCE": {
            role: definition["specialty"][0]
            for role, definition in roles.items() if definition.get("specialty")
        },
        "RESOURCE_GATHER_ROLES": _resource_roles(roles),
        "AVAILABLE_ACTIONS": ["rest"],
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "smoke"},
        "lm_complete": lambda *args, **kwargs: None,
        "is_scaffold_text": lambda text: False,
        "memory_store": None,
        "log_activity": lambda *args, **kwargs: None,
        "log_conversation": lambda *args, **kwargs: None,
        "log_benchmark": lambda *args, **kwargs: None,
        "validate_blueprint": lambda bp, *args, **kwargs: (
            (True, None) if isinstance(bp, dict) and bp.get("id")
            and bp.get("name") and bp.get("needs") and bp.get("function")
            else (False, "invalid smoke blueprint")
        ),
        "canonical_effect_vector": lambda *args, **kwargs: (),
    }
    if extra_deps:
        deps.update(extra_deps)
    return se.SimEngine(deps, roster_size=roster_size)


def wait_for_saga(engine):
    """Wait for the async day-boundary saga worker without blocking _tick_once."""
    future = engine._saga_inflight
    if future is not None:
        future.result(timeout=SAGA_WAIT_TIMEOUT_S)


class Checks:
    def __init__(self):
        self.details = []

    def check(self, condition, name, detail=None):
        if not condition:
            raise AssertionError(f"{name}: {detail or 'condition was false'}")
        self.details.append({"name": name, "pass": True, "detail": detail})


def _assert_saga_entry(entry, expected_frame, expected_day, expected_text):
    return (
        isinstance(entry, dict)
        and entry.get("text") == expected_text
        and entry.get("frame") == expected_frame
        and entry.get("dayIndex") == expected_day
    )


def exercise_day_boundary_with_chronicle(checks):
    engine = make_engine()
    engine.frameTick = se.DAY_FRAMES - 1
    engine._push_chronicle("Smoke death recorded.", kind="death")
    engine._tick_once()
    wait_for_saga(engine)
    saga = engine.civilization.get("saga") or []
    checks.check(len(saga) == 1, "chronicle_day_appends_saga", saga)
    checks.check(_assert_saga_entry(saga[0], se.DAY_FRAMES, 1, se.SAGA_FALLBACK_TEXT),
                 "chronicle_day_saga_fallback_when_lm_none", saga[0])


def exercise_empty_chronicle_day(checks):
    engine = make_engine()
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    wait_for_saga(engine)
    checks.check(len(engine.civilization.get("saga") or []) == 1,
                 "first_empty_day_appends_saga")
    engine.frameTick = 2 * se.DAY_FRAMES - 1
    engine._tick_once()
    wait_for_saga(engine)
    saga = engine.civilization.get("saga") or []
    checks.check(len(saga) == 2, "second_empty_day_appends_saga", len(saga))
    checks.check(_assert_saga_entry(saga[1], 2 * se.DAY_FRAMES, 2, se.SAGA_FALLBACK_TEXT),
                 "empty_day_saga_fallback_shape", saga[1])


def exercise_lm_complete_success_path(checks):
    fixed = "The village marked a calm day with one recorded death."
    logged = []

    def run_chronicle_saga(saga_context):
        logged.append(saga_context)
        return fixed

    engine = make_engine({"run_chronicle_saga": run_chronicle_saga})
    engine.frameTick = se.DAY_FRAMES - 1
    engine._push_chronicle("A villager fell.", kind="death")
    engine._tick_once()
    wait_for_saga(engine)
    saga = engine.civilization.get("saga") or []
    checks.check(len(saga) == 1 and saga[0].get("text") == fixed,
                 "lm_complete_success_writes_model_text", saga)
    checks.check(bool(logged), "run_chronicle_saga_receives_context",
                 list(logged[0].keys()) if logged else None)


def exercise_dialogue_cap(checks):
    many_lines = [
        {"type": "conversation", "kind": "speech", "from": "A", "to": "B",
         "message": f"line-{i}", "frame_tick": 100 + i}
        for i in range(20)
    ]

    def reader(start, end):
        return [row for row in many_lines if start <= row["frame_tick"] < end]

    engine = make_engine({"read_conversation_window": reader})
    ctx = engine._snapshot_saga_context(0, se.DAY_FRAMES, se.DAY_FRAMES, 1)
    checks.check(len(ctx.get("dialogue") or []) == se.SAGA_DIALOGUE_EXCERPT_CAP,
                 "dialogue_excerpt_capped",
                 len(ctx.get("dialogue") or []))


def exercise_read_conversation_window(checks):
    lines = [
        {"type": "conversation", "kind": "speech", "from": "A", "to": "B",
         "message": "before window", "frame_tick": 5},
        {"type": "conversation", "kind": "speech", "from": "A", "to": "B",
         "message": "in window", "frame_tick": 50},
        {"type": "activity", "message": "ignored", "frame_tick": 60},
        {"type": "conversation", "kind": "speech", "from": "B", "to": "A",
         "message": "near end inclusive", "frame_tick": 99},
        {"type": "conversation", "kind": "speech", "from": "B", "to": "A",
         "message": "at end exclusive", "frame_tick": 100},
        {"type": "conversation", "kind": "speech", "from": "C", "to": "D",
         "message": "after window", "frame_tick": 101},
        "not valid json",
    ]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".jsonl") as fh:
        path = fh.name
        for item in lines:
            if isinstance(item, str):
                fh.write(item + "\n")
            else:
                fh.write(json.dumps(item) + "\n")
    try:
        window = read_conversation_window(path, 10, 100)
        checks.check(len(window) == 2, "conversation_window_filters_frame_range",
                     [r.get("message") for r in window])
        checks.check(all(r.get("type") == "conversation" for r in window),
                     "conversation_window_type_only")
        checks.check(read_conversation_window(path, 100, 100) == [],
                     "conversation_window_empty_when_start_eq_end")
        checks.check(read_conversation_window("/no/such/file.jsonl", 0, 10) == [],
                     "conversation_window_missing_file_returns_empty")
    finally:
        Path(path).unlink(missing_ok=True)


def exercise_restore_saga_setdefault(checks):
    engine = make_engine()
    payload = engine._serialize_state()
    payload["civilization"].pop("saga", None)
    db_path = Path(tempfile.mkdtemp()) / "state.db"
    se._write_state_db(str(db_path), payload)
    original_db_path = se.DB_PATH
    try:
        se.DB_PATH = str(db_path)
        restored = make_engine()
        checks.check(restored.restore_state(), "restore_state_succeeds_without_saga")
        checks.check(isinstance(restored.civilization.get("saga"), list),
                     "restore_setdefault_saga", restored.civilization.get("saga"))
    finally:
        se.DB_PATH = original_db_path


def exercise_snapshot_flag_and_projection(checks):
    engine = make_engine()
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    wait_for_saga(engine)
    snap = engine.snapshot()
    flags = (snap.get("config") or {}).get("flags") or {}
    checks.check(flags.get("CHRONICLE_SAGA_ENABLED") is True,
                 "snapshot_flag_chronicle_saga_enabled", flags.get("CHRONICLE_SAGA_ENABLED"))
    saga = snap.get("saga") or []
    checks.check(len(saga) == 1 and saga[0].get("text") == se.SAGA_FALLBACK_TEXT,
                 "snapshot_saga_projection", saga)


def exercise_engine_reader_dep(checks):
    captured = {}

    def reader(start, end):
        captured["window"] = (start, end)
        return [{"type": "conversation", "frame_tick": start + 1}]

    engine = make_engine({"read_conversation_window": reader})
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    wait_for_saga(engine)
    checks.check(captured.get("window") == (0, se.DAY_FRAMES),
                 "engine_calls_reader_with_completed_day_window",
                 captured.get("window"))


def exercise_llm_log_tag(checks):
    logged = []

    def run_chronicle_saga(saga_context):
        logged.append({"module": "chronicle_saga", "frame_tick": saga_context.get("frame")})
        return "A brief dispatch."

    engine = make_engine({"run_chronicle_saga": run_chronicle_saga})
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    wait_for_saga(engine)
    checks.check(logged and logged[0].get("module") == "chronicle_saga",
                 "optional_run_chronicle_saga_hook", logged)


def main():
    checks = Checks()
    try:
        checks.check(se.CHRONICLE_SAGA_ENABLED is True, "flag_default_true")
        checks.check(se.SAGA_DIALOGUE_EXCERPT_CAP == 10, "dialogue_cap_constant")
        exercise_day_boundary_with_chronicle(checks)
        exercise_empty_chronicle_day(checks)
        exercise_lm_complete_success_path(checks)
        exercise_dialogue_cap(checks)
        exercise_read_conversation_window(checks)
        exercise_restore_saga_setdefault(checks)
        exercise_snapshot_flag_and_projection(checks)
        exercise_engine_reader_dep(checks)
        exercise_llm_log_tag(checks)
        result = {"pass": True, "assertions": checks.details}
    except Exception as exc:
        result = {
            "pass": False,
            "assertions": checks.details,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
