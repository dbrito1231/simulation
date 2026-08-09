"""Feature 5 (F5) determinism proof — Emergence Breakthroughs Phase A0.

Runs two identical cold-start headless worlds with stubbed llm_decide (always
``rest``), advances the same tick count via ``_tick_once()``, captures
benchmark trajectories, and reports whether they are bit-identical.

This is a cheap proof/probe only — not the full ``fork_compare.py`` harness
(A2) and does not pin RNG, tick-thread scheduling, or executor ordering (A1).
Live LLM runs are out of scope.

No Ollama. Never touches ``simulation/state.db``. Run:

    uv run python scripts/determinism_proof.py
    uv run python scripts/determinism_proof.py --ticks 1800
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402

DEFAULT_TICK_COUNT = 1800
DRAIN_TIMEOUT_S = 60.0


def _load_roles():
    with open(ROOT / "simulation" / "roles.json", encoding="utf-8") as fh:
        return json.load(fh)


def _build_resource_gather_roles(roles):
    out = {}
    for role, d in roles.items():
        for res in d.get("specialty") or []:
            out.setdefault(res, []).append(role)
    return {res: tuple(rs) for res, rs in out.items()}


class BenchmarkCapture:
  """In-memory stand-in for SessionLogger.log_benchmark."""

  def __init__(self):
    self.records: list[dict] = []

  def log(self, metric, value, frame_tick=None, detail=None):
    record = {
      "metric": metric,
      "value": value,
      "frame_tick": frame_tick,
    }
    if detail is not None:
      record["detail"] = detail
    self.records.append(record)


def canonical_trajectory(records: list[dict]) -> str:
  """Stable JSONL serialization for bit-identical comparison."""
  if not records:
    return ""
  return "\n".join(
    json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    for record in records
  ) + "\n"


def _load_decision_actions():
    """Parse DECISION_ACTIONS from server.py without importing server (no side effects)."""
    server_source = (ROOT / "simulation" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(server_source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "DECISION_ACTIONS" for t in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise RuntimeError("DECISION_ACTIONS not found in simulation/server.py")


def make_engine(capture: BenchmarkCapture, roster_size: int = 8) -> se.SimEngine:
  roles = _load_roles()
  role_primary = {
    role: d["specialty"][0] for role, d in roles.items() if d.get("specialty")
  }
  deps = {
    "ROLES": roles,
    "ROLE_PROJECT": {
      role: (d.get("preferredProject")[0]
             if isinstance(d.get("preferredProject"), list)
             else d.get("preferredProject"))
      for role, d in roles.items()
    },
    "ROLE_SKILLS": {role: d.get("skill", "helps") for role, d in roles.items()},
    "ROLE_PRIMARY_RESOURCE": role_primary,
    "RESOURCE_GATHER_ROLES": _build_resource_gather_roles(roles),
    "AVAILABLE_ACTIONS": _load_decision_actions(),
    "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
    "llm_decide": lambda payload: {"action": "rest", "reasoning": "determinism_proof"},
    "lm_complete": lambda *a, **k: None,
    "is_scaffold_text": lambda t: False,
    "memory_store": None,
    "log_activity": lambda *a, **k: None,
    "log_conversation": lambda *a, **k: None,
    "log_benchmark": capture.log,
    "flush_benchmarks": lambda: None,
    "validate_blueprint": lambda *a, **k: (False, "unused"),
    "canonical_effect_vector": lambda *a, **k: (),
  }
  return se.SimEngine(deps, roster_size=roster_size)


def _drain_async_work(engine: se.SimEngine, timeout_s: float = DRAIN_TIMEOUT_S) -> None:
  deadline = time.monotonic() + timeout_s
  while time.monotonic() < deadline:
    with engine.lock:
      inflight = bool(engine._inflight) or bool(engine._piano_refresh_inflight)
    if not inflight:
      time.sleep(0.05)
      with engine.lock:
        if not engine._inflight and not engine._piano_refresh_inflight:
          return
    time.sleep(0.01)
  raise RuntimeError(
    f"async work did not drain within {timeout_s}s "
    f"(inflight={engine._inflight}, piano_refresh={engine._piano_refresh_inflight})"
  )


def _shutdown_engine(engine: se.SimEngine) -> None:
  engine._stop.set()
  engine._executor.shutdown(wait=True, cancel_futures=True)
  engine.piano_workers.shutdown(wait=True, cancel_futures=True)


def run_world(tick_count: int, roster_size: int = 8) -> list[dict]:
  capture = BenchmarkCapture()
  with tempfile.TemporaryDirectory(prefix="determinism_proof_") as tmpdir:
    old_db_path = se.DB_PATH
    se.DB_PATH = str(Path(tmpdir) / "state.db")
    engine = None
    try:
      engine = make_engine(capture, roster_size=roster_size)
      for _ in range(tick_count):
        engine._tick_once()
      _drain_async_work(engine)
      return list(capture.records)
    finally:
      se.DB_PATH = old_db_path
      if engine is not None:
        _shutdown_engine(engine)


def _format_value(value) -> str:
  try:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
  except TypeError:
    return repr(value)


def summarize_divergence(run_a: list[dict], run_b: list[dict]) -> str:
  canon_a = canonical_trajectory(run_a)
  canon_b = canonical_trajectory(run_b)
  if canon_a == canon_b:
    return "trajectories match"

  if len(run_a) != len(run_b):
    first_idx = min(len(run_a), len(run_b))
    parts = [f"record count differs (run_a={len(run_a)}, run_b={len(run_b)})"]
  else:
    first_idx = next(
      (i for i, (a, b) in enumerate(zip(run_a, run_b)) if a != b),
      min(len(run_a), len(run_b)),
    )
    parts = []

  if first_idx >= len(run_a) or first_idx >= len(run_b):
    parts.append(f"first divergence at index {first_idx} (one run ended early)")
    if first_idx < len(run_a):
      parts.append(f"  run_a only: metric={run_a[first_idx].get('metric')}, "
                     f"frame_tick={run_a[first_idx].get('frame_tick')}")
    if first_idx < len(run_b):
      parts.append(f"  run_b only: metric={run_b[first_idx].get('metric')}, "
                     f"frame_tick={run_b[first_idx].get('frame_tick')}")
    return "; ".join(parts)

  a = run_a[first_idx]
  b = run_b[first_idx]
  parts.append(
    f"first divergence at index {first_idx}: "
    f"metric={a.get('metric')!r} frame_tick={a.get('frame_tick')}"
  )
  if a.get("metric") != b.get("metric") or a.get("frame_tick") != b.get("frame_tick"):
    parts.append(
      f"  run_b: metric={b.get('metric')!r} frame_tick={b.get('frame_tick')}"
    )
  if a.get("value") != b.get("value"):
    parts.append(f"  value: run_a={_format_value(a.get('value'))}")
    parts.append(f"         run_b={_format_value(b.get('value'))}")
  if a.get("detail") != b.get("detail"):
    parts.append(f"  detail: run_a={_format_value(a.get('detail'))}")
    parts.append(f"          run_b={_format_value(b.get('detail'))}")
  return "\n".join(parts)


def compare_runs(tick_count: int, roster_size: int = 8) -> tuple[bool, list[dict], list[dict]]:
  run_a = run_world(tick_count, roster_size=roster_size)
  run_b = run_world(tick_count, roster_size=roster_size)
  identical = canonical_trajectory(run_a) == canonical_trajectory(run_b)
  return identical, run_a, run_b


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(description="F5 determinism proof (Phase A0)")
  parser.add_argument(
    "--ticks",
    type=int,
    default=DEFAULT_TICK_COUNT,
    help=f"number of _tick_once() calls per run (default {DEFAULT_TICK_COUNT})",
  )
  parser.add_argument(
    "--roster",
    type=int,
    default=8,
    help="roster size for each cold-start world (default 8)",
  )
  args = parser.parse_args(argv)
  if args.ticks < 1:
    print("ERROR: --ticks must be >= 1", file=sys.stderr)
    return 1
  if args.roster < 1:
    print("ERROR: --roster must be >= 1", file=sys.stderr)
    return 1

  print("F5 determinism proof (Phase A0)")
  print(f"  ticks={args.ticks} roster={args.roster} "
        f"(BENCHMARK_TICK_FRAMES={se.BENCHMARK_TICK_FRAMES}, "
        f"FIRST_BENCHMARK_FRAME={se.FIRST_BENCHMARK_FRAME})")

  identical, run_a, run_b = compare_runs(args.ticks, roster_size=args.roster)

  print(f"  run_a benchmark records: {len(run_a)}")
  print(f"  run_b benchmark records: {len(run_b)}")

  if identical:
    print("DETERMINISM: BIT-IDENTICAL YES")
  else:
    print("DETERMINISM: BIT-IDENTICAL NO")
    print("Divergence summary:")
    print(summarize_divergence(run_a, run_b))

  return 0


if __name__ == "__main__":
  raise SystemExit(main())
