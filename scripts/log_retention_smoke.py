"""Deterministic no-server/no-Ollama smoke for the session-log retention
policy (docs/plan-log-retention.md).

This smoke deliberately does NOT `import server` -- doing so would construct
the module-level `session_logger = SessionLogger(...)` and create/prune a
directory under the REAL `simulation/logs/` tree as a side effect of import
(the same thing sid_parity_smoke.py / path1_smoke.py already accept when
they import server.py for other reasons). To keep this smoke's side effects
confined to a throwaway temp directory, the pruning algorithm is mirrored
here verbatim from `simulation/server.py`'s `SessionLogger._prune_old_sessions`
(SESSION_DIR_RE, LOG_RETENTION_SESSIONS parsing, keep-N-newest slice,
best-effort per-directory rmtree). If that method's logic ever changes, this
mirror must change with it -- there is no import-time coupling to catch
drift automatically, so keep the two in sync by eye.

Run:
    uv run python scripts/log_retention_smoke.py

The result is always written to scripts/out/log_retention_smoke.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_PATH = ROOT / "scripts" / "out" / "log_retention_smoke.json"

# --- Mirrors simulation/server.py verbatim (see module docstring) ----------

SESSION_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}$")


def _parse_retention(raw):
    try:
        return int(raw or 20)
    except (TypeError, ValueError):
        return 20


def prune_old_sessions(logs_root, session_id, keep):
    """Standalone mirror of SessionLogger._prune_old_sessions -- takes the
    keep-count and current session_id as explicit arguments instead of
    reading module/instance state, so it is directly testable against a
    throwaway logs_root with no server import required."""
    if keep <= 0:
        return  # retention disabled -- keep everything
    try:
        names = sorted(
            name for name in os.listdir(logs_root)
            if SESSION_DIR_RE.match(name)
            and os.path.isdir(os.path.join(logs_root, name))
        )
    except OSError:
        return
    stale = [name for name in names[:-keep] if name != session_id]
    for name in stale:
        try:
            shutil.rmtree(os.path.join(logs_root, name))
        except OSError:
            pass  # best-effort; one un-deletable dir must not block others


# --- Test harness ------------------------------------------------------

class Checks:
    def __init__(self):
        self.details = []

    def check(self, condition, name, detail=None):
        if not condition:
            raise AssertionError(f"{name}: {detail or 'condition was false'}")
        self.details.append({"name": name, "pass": True, "detail": detail})


def _make_session_dir(logs_root, session_id):
    path = os.path.join(logs_root, session_id)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "activity.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    return path


def _ids(count, day=20, start=0):
    """count synthetic ISO session ids on the same day, strictly increasing
    both numerically and lexicographically (one-second increments, so no
    HH/MM/SS field wraps within a single call for any count used here)."""
    ids = []
    for i in range(count):
        total_seconds = start + i
        hh = (total_seconds // 3600) % 24
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        ids.append(f"2026-07-{day:02d}T{hh:02d}-{mm:02d}-{ss:02d}")
    return ids


def exercise_keep_n_newest(checks):
    """Assertion 1: keep-N-newest keeps exactly N newest, deletes the rest."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-1-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        ids = _ids(25, day=10)
        for sid in ids:
            _make_session_dir(logs_root, sid)
        current = "OTHER-CURRENT-SESSION-not-matching-regex-irrelevant"
        prune_old_sessions(str(logs_root), current, keep=20)
        remaining = sorted(
            n for n in os.listdir(logs_root)
            if SESSION_DIR_RE.match(n)
        )
        checks.check(remaining == ids[-20:],
                     "keep_n_newest_keeps_exactly_20_newest",
                     {"kept": remaining, "expected": ids[-20:]})
        checks.check(len(remaining) == 20, "keep_n_newest_count_is_20", len(remaining))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_loose_files_survive(checks):
    """Assertion 2: loose non-session files in logs/ root are never touched."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-2-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        ids = _ids(25, day=11)
        for sid in ids:
            _make_session_dir(logs_root, sid)
        loose = ["soak-attempt2.json", "path1_soak_20260101.json",
                 "path1_soak_20260101.timeline.json", "foo.db"]
        for name in loose:
            (logs_root / name).write_text("{}", encoding="utf-8")
        prune_old_sessions(str(logs_root), "irrelevant-current", keep=5)
        for name in loose:
            checks.check((logs_root / name).exists(),
                         f"loose_file_survives_{name}", name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_non_session_dirs_survive(checks):
    """Assertion 3: non-session subdirectories are never touched."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-3-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        ids = _ids(25, day=12)
        for sid in ids:
            _make_session_dir(logs_root, sid)
        non_session_dirs = ["replay_bench", "not-a-session"]
        for name in non_session_dirs:
            nested = logs_root / name
            os.makedirs(nested, exist_ok=True)
            (nested / "keep.txt").write_text("keep me", encoding="utf-8")
        prune_old_sessions(str(logs_root), "irrelevant-current", keep=5)
        for name in non_session_dirs:
            checks.check((logs_root / name).is_dir()
                         and (logs_root / name / "keep.txt").exists(),
                         f"non_session_dir_survives_{name}", name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_current_session_never_deleted(checks):
    """Assertion 4: the current session dir survives even outside the N
    newest window (e.g. its dir predates a burst of manually-seeded ones
    that lexicographically sort after it, an edge case that can't happen in
    production but must not crash/misbehave the guard)."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-4-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        current = "2026-07-13T00-00-00"
        _make_session_dir(logs_root, current)
        # Seed 25 sessions that all sort AFTER the current one.
        later_ids = _ids(25, day=14)
        for sid in later_ids:
            _make_session_dir(logs_root, sid)
        prune_old_sessions(str(logs_root), current, keep=5)
        checks.check((logs_root / current).is_dir(),
                     "current_session_never_deleted", current)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_zero_disables_pruning(checks):
    """Assertion 5: LOG_RETENTION_SESSIONS <= 0 prunes nothing."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-5-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        ids = _ids(30, day=15)
        for sid in ids:
            _make_session_dir(logs_root, sid)
        prune_old_sessions(str(logs_root), "irrelevant-current", keep=0)
        remaining = sorted(
            n for n in os.listdir(logs_root) if SESSION_DIR_RE.match(n)
        )
        checks.check(remaining == ids, "zero_retention_prunes_nothing",
                     {"kept": len(remaining), "expected": len(ids)})

        prune_old_sessions(str(logs_root), "irrelevant-current", keep=-3)
        remaining_neg = sorted(
            n for n in os.listdir(logs_root) if SESSION_DIR_RE.match(n)
        )
        checks.check(remaining_neg == ids, "negative_retention_prunes_nothing",
                     {"kept": len(remaining_neg), "expected": len(ids)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_best_effort_undeletable(checks):
    """Assertion 6: an un-deletable stale dir doesn't abort pruning or skip
    the rest of the stale set. Simulated by monkeypatching shutil.rmtree to
    raise OSError for one specific target while leaving the rest real."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-6-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        ids = _ids(25, day=16)
        for sid in ids:
            _make_session_dir(logs_root, sid)
        stale_expected = ids[:-20]
        checks.check(len(stale_expected) > 1,
                     "best_effort_fixture_has_multiple_stale_dirs",
                     len(stale_expected))
        undeletable = stale_expected[0]

        real_rmtree = shutil.rmtree

        def flaky_rmtree(path, *args, **kwargs):
            if os.path.basename(os.path.normpath(path)) == undeletable:
                raise OSError("simulated permission failure")
            return real_rmtree(path, *args, **kwargs)

        shutil.rmtree = flaky_rmtree
        try:
            prune_old_sessions(str(logs_root), "irrelevant-current", keep=20)
        finally:
            shutil.rmtree = real_rmtree

        remaining = sorted(
            n for n in os.listdir(logs_root) if SESSION_DIR_RE.match(n)
        )
        checks.check(undeletable in remaining,
                     "best_effort_undeletable_dir_survives", undeletable)
        other_stale = [n for n in stale_expected if n != undeletable]
        checks.check(all(n not in remaining for n in other_stale),
                     "best_effort_other_stale_dirs_still_pruned",
                     {"other_stale": other_stale, "remaining": remaining})
        checks.check(set(remaining) == set(ids[-20:]) | {undeletable},
                     "best_effort_pruning_continues_past_failure",
                     remaining)
    finally:
        real_rmtree(tmp, ignore_errors=True)


def exercise_day_boundary_lexicographic_order(checks):
    """Assertion 7: lexicographic sort correctly orders a day boundary --
    ...T23-59-59 on one day must sort BEFORE ...T00-00-01 the next day, and
    pruning must treat the earlier one as staler."""
    tmp = Path(tempfile.mkdtemp(prefix="log-retention-smoke-7-"))
    try:
        logs_root = tmp / "logs"
        os.makedirs(logs_root, exist_ok=True)
        older = "2026-07-17T23-59-59"
        newer = "2026-07-18T00-00-01"
        _make_session_dir(logs_root, older)
        _make_session_dir(logs_root, newer)
        checks.check(sorted([newer, older]) == [older, newer],
                     "day_boundary_names_sort_chronologically",
                     sorted([newer, older]))
        prune_old_sessions(str(logs_root), "irrelevant-current", keep=1)
        remaining = sorted(
            n for n in os.listdir(logs_root) if SESSION_DIR_RE.match(n)
        )
        checks.check(remaining == [newer],
                     "day_boundary_keeps_the_chronologically_newer_dir",
                     remaining)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def exercise_retention_default_and_env_parse(checks):
    """Sanity on the SIM_LOG_RETENTION defensive-parse contract described in
    the plan/spec (mirrored here, not imported, per module docstring)."""
    checks.check(_parse_retention("20") == 20, "env_parse_valid_string")
    checks.check(_parse_retention(None) == 20, "env_parse_missing_falls_back")
    checks.check(_parse_retention("") == 20, "env_parse_blank_falls_back")
    checks.check(_parse_retention("not-a-number") == 20, "env_parse_garbage_falls_back")
    checks.check(_parse_retention("0") == 0, "env_parse_zero_is_zero")
    checks.check(_parse_retention("-5") == -5, "env_parse_negative_passthrough")


def main():
    checks = Checks()
    try:
        exercise_keep_n_newest(checks)
        exercise_loose_files_survive(checks)
        exercise_non_session_dirs_survive(checks)
        exercise_current_session_never_deleted(checks)
        exercise_zero_disables_pruning(checks)
        exercise_best_effort_undeletable(checks)
        exercise_day_boundary_lexicographic_order(checks)
        exercise_retention_default_and_env_parse(checks)
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
