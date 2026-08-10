"""Feature 5 (F5) determinism proof — Emergence Breakthroughs Phase A0/A1.

Runs two identical headless worlds with stubbed ``llm_decide`` (always
``rest``), advances the same tick count, and reports whether benchmark
trajectories (and, for hard-case modes, a world-state fingerprint) are
bit-identical.

Phase A1 adds hard-case modes (global-RNG carryover, optional no-drain /
tick-thread / checkpoint-restore forks) and optional engine pinning via
``DETERMINISM_PINNING`` (``--pin``).

No Ollama. Never touches ``simulation/state.db``. Run:

    uv run python scripts/determinism_proof.py
    uv run python scripts/determinism_proof.py --mode unseeded-twin --pin
    uv run python scripts/determinism_proof.py --mode checkpoint --pin --ticks 400
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
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
DEFAULT_DETERMINISM_SEED = 42
HARD_MODES = frozenset({"unseeded-twin", "no-drain", "tick-thread", "checkpoint"})


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


def apply_pin_config(pin: bool, seed: int = DEFAULT_DETERMINISM_SEED) -> None:
    """Toggle harness pinning on the live sim_engine module (monkeypatch surface)."""
    se.DETERMINISM_PINNING = pin
    se.DETERMINISM_SEED = seed


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
    if se.DETERMINISM_PINNING:
        engine._run_pin_think_queue()
        with engine.lock:
            if engine._inflight:
                raise RuntimeError(
                    f"pin think queue left inflight={engine._inflight}"
                )
        return
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


def _sanitize_snapshot(snapshot: dict) -> dict:
    """Drop wall-clock fields that are intentionally nondeterministic."""
    snap = dict(snapshot)
    snap.pop("uptimeSeconds", None)
    for agent in snap.get("agents") or []:
        if isinstance(agent, dict):
            agent.pop("contextDirtySince", None)
    return snap


def world_fingerprint(engine: se.SimEngine) -> str:
    """Stable hash of the post-run world snapshot (harness comparison only)."""
    snap = _sanitize_snapshot(engine.snapshot())
    payload = json.dumps(snap, sort_keys=True, ensure_ascii=False, default=str,
                          separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def combined_trajectory(records: list[dict], engine: se.SimEngine | None) -> str:
    parts = [canonical_trajectory(records)]
    if engine is not None:
        parts.append(world_fingerprint(engine))
    return "\n".join(parts)


def _format_value(value) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return repr(value)


def summarize_divergence(run_a: list[dict], run_b: list[dict],
                         fp_a: str | None = None, fp_b: str | None = None) -> str:
    canon_a = canonical_trajectory(run_a)
    canon_b = canonical_trajectory(run_b)
    parts: list[str] = []
    if canon_a != canon_b:
        if len(run_a) != len(run_b):
            first_idx = min(len(run_a), len(run_b))
            parts.append(
                f"benchmark record count differs (run_a={len(run_a)}, run_b={len(run_b)})"
            )
        else:
            first_idx = next(
                (i for i, (a, b) in enumerate(zip(run_a, run_b)) if a != b),
                min(len(run_a), len(run_b)),
            )
            parts.append("")
        if first_idx >= len(run_a) or first_idx >= len(run_b):
            parts.append(f"first benchmark divergence at index {first_idx}")
        else:
            a = run_a[first_idx]
            b = run_b[first_idx]
            parts.append(
                f"first benchmark divergence at index {first_idx}: "
                f"metric={a.get('metric')!r} frame_tick={a.get('frame_tick')}"
            )
            if a.get("value") != b.get("value"):
                parts.append(f"  value: run_a={_format_value(a.get('value'))}")
                parts.append(f"         run_b={_format_value(b.get('value'))}")
            if a.get("detail") != b.get("detail"):
                parts.append(f"  detail: run_a={_format_value(a.get('detail'))}")
                parts.append(f"          run_b={_format_value(b.get('detail'))}")
    if fp_a is not None and fp_b is not None and fp_a != fp_b:
        parts.append(f"world fingerprint differs (run_a={fp_a[:16]}…, run_b={fp_b[:16]}…)")
    if not parts:
        return "trajectories match"
    return "\n".join(parts)


def _advance_engine(engine: se.SimEngine, tick_count: int, mode: str) -> None:
    if mode == "tick-thread":
        engine.start()
        deadline = time.monotonic() + max(120.0, tick_count / se.TICKS_PER_SEC + 30.0)
        while engine.frameTick < tick_count and time.monotonic() < deadline:
            time.sleep(0.01)
        if engine.frameTick < tick_count:
            raise RuntimeError(
                f"tick-thread mode stalled at frame {engine.frameTick}/{tick_count}"
            )
        engine.stop()
        return
    for _ in range(tick_count):
        engine._tick_once()


def run_world(
    tick_count: int,
    roster_size: int = 8,
    *,
    mode: str = "a0",
    reseed: bool = True,
    seed: int = DEFAULT_DETERMINISM_SEED,
    drain: bool | None = None,
    checkpoint_bytes: bytes | None = None,
) -> tuple[list[dict], str | None]:
    if reseed:
        random.seed(seed)
    capture = BenchmarkCapture()
    should_drain = drain if drain is not None else mode != "no-drain"
    with tempfile.TemporaryDirectory(prefix="determinism_proof_") as tmpdir:
        tmp_path = Path(tmpdir)
        old_db_path = se.DB_PATH
        db_path = tmp_path / "state.db"
        se.DB_PATH = str(db_path)
        engine = None
        try:
            if checkpoint_bytes is not None:
                db_path.write_bytes(checkpoint_bytes)
            engine = make_engine(capture, roster_size=roster_size)
            if checkpoint_bytes is not None and not engine.restore_state():
                raise RuntimeError("checkpoint restore failed")
            _advance_engine(engine, tick_count, mode)
            if should_drain:
                _drain_async_work(engine)
            fp = world_fingerprint(engine) if mode in HARD_MODES else None
            return list(capture.records), fp
        finally:
            se.DB_PATH = old_db_path
            if engine is not None:
                _shutdown_engine(engine)


def _prepare_checkpoint_bytes(
    tick_count: int,
    roster_size: int,
    seed: int,
) -> bytes:
    random.seed(seed)
    with tempfile.TemporaryDirectory(prefix="determinism_ckpt_") as tmpdir:
        tmp_path = Path(tmpdir)
        db_path = tmp_path / "state.db"
        old_db_path = se.DB_PATH
        se.DB_PATH = str(db_path)
        engine = None
        try:
            engine = make_engine(BenchmarkCapture(), roster_size=roster_size)
            for _ in range(tick_count):
                engine._tick_once()
            _drain_async_work(engine)
            engine.save_state(force=True)
            if not db_path.is_file():
                raise RuntimeError("checkpoint prepare did not write state.db")
            return db_path.read_bytes()
        finally:
            se.DB_PATH = old_db_path
            if engine is not None:
                _shutdown_engine(engine)


def compare_runs(
    tick_count: int,
    roster_size: int,
    *,
    mode: str,
    pin: bool,
    seed: int,
) -> tuple[bool, list[dict], list[dict], str | None, str | None]:
    apply_pin_config(pin, seed)
    reseed_each = mode != "unseeded-twin"
    run_kwargs = {
        "roster_size": roster_size,
        "seed": seed,
    }
    if mode == "checkpoint":
        ckpt_bytes = _prepare_checkpoint_bytes(tick_count, roster_size, seed)
        fork_ticks = max(120, tick_count // 3)
        fork_kwargs = {
            **run_kwargs,
            "mode": "checkpoint",
            "reseed": reseed_each,
            "checkpoint_bytes": ckpt_bytes,
        }
        run_a, fp_a = run_world(fork_ticks, **fork_kwargs)
        run_b, fp_b = run_world(fork_ticks, **fork_kwargs)
    else:
        run_a, fp_a = run_world(
            tick_count,
            mode=mode,
            reseed=reseed_each,
            **run_kwargs,
        )
        run_b, fp_b = run_world(
            tick_count,
            mode=mode,
            reseed=reseed_each,
            **run_kwargs,
        )

    if mode in HARD_MODES:
        traj_a = combined_trajectory(run_a, None) + (fp_a or "")
        traj_b = combined_trajectory(run_b, None) + (fp_b or "")
    else:
        traj_a = canonical_trajectory(run_a)
        traj_b = canonical_trajectory(run_b)
    identical = traj_a == traj_b
    return identical, run_a, run_b, fp_a, fp_b


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="F5 determinism proof (Phase A0/A1)")
    parser.add_argument(
        "--ticks",
        type=int,
        default=DEFAULT_TICK_COUNT,
        help=f"number of tick advances per run (default {DEFAULT_TICK_COUNT})",
    )
    parser.add_argument(
        "--roster",
        type=int,
        default=8,
        help="roster size for each cold-start world (default 8)",
    )
    parser.add_argument(
        "--mode",
        choices=["a0", *sorted(HARD_MODES)],
        default="a0",
        help="a0=benchmark-only baseline; hard cases exercise RNG/async/thread/checkpoint paths",
    )
    parser.add_argument(
        "--pin",
        action="store_true",
        help="enable DETERMINISM_PINNING for this proof run",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_DETERMINISM_SEED,
        help=f"DETERMINISM_SEED / explicit random.seed for reseeded modes (default {DEFAULT_DETERMINISM_SEED})",
    )
    args = parser.parse_args(argv)
    if args.ticks < 1:
        print("ERROR: --ticks must be >= 1", file=sys.stderr)
        return 1
    if args.roster < 1:
        print("ERROR: --roster must be >= 1", file=sys.stderr)
        return 1

    phase = "A1" if args.mode != "a0" or args.pin else "A0"
    print(f"F5 determinism proof (Phase {phase})")
    print(f"  mode={args.mode} pin={args.pin} seed={args.seed} ticks={args.ticks} "
          f"roster={args.roster}")
    print(f"  BENCHMARK_TICK_FRAMES={se.BENCHMARK_TICK_FRAMES} "
          f"FIRST_BENCHMARK_FRAME={se.FIRST_BENCHMARK_FRAME}")
    print(f"  DETERMINISM_PINNING will be {args.pin} for this run")

    identical, run_a, run_b, fp_a, fp_b = compare_runs(
        args.ticks,
        roster_size=args.roster,
        mode=args.mode,
        pin=args.pin,
        seed=args.seed,
    )

    print(f"  run_a benchmark records: {len(run_a)}")
    print(f"  run_b benchmark records: {len(run_b)}")
    if fp_a is not None:
        print(f"  run_a world fingerprint: {fp_a[:16]}…")
        print(f"  run_b world fingerprint: {fp_b[:16]}…")

    if identical:
        print("DETERMINISM: BIT-IDENTICAL YES")
    else:
        print("DETERMINISM: BIT-IDENTICAL NO")
        print("Divergence summary:")
        print(summarize_divergence(run_a, run_b, fp_a, fp_b))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
