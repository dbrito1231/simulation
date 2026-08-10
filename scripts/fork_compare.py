"""Fork compare harness — Emergence Breakthroughs Phase A2.

Cold-start (or optional checkpoint) two headless worlds differing in exactly
one ``sim_engine`` flag or documented harness knob, stub ``llm_decide``
(always ``rest``), and compare **benchmark trajectories plus world fingerprint**.

Bit-identical identical-input forks require ``DETERMINISM_PINNING`` (``--pin`` or
``--identical``, which implies ``--pin``). Never touches ``simulation/state.db``.

Examples::

    uv run python scripts/fork_compare.py --ticks 400 --identical --pin
    uv run python scripts/fork_compare.py --ticks 400 --pin --var WEATHER_ENABLED=false
    uv run python scripts/fork_compare.py --ticks 200 --pin --var DETERMINISM_SEED=99 --out /tmp/fork_out
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402

from determinism_proof import (  # noqa: E402
    BenchmarkCapture,
    DEFAULT_DETERMINISM_SEED,
    DEFAULT_TICK_COUNT,
    DRAIN_TIMEOUT_S,
    _advance_engine,
    _drain_async_work,
    _prepare_checkpoint_bytes,
    _shutdown_engine,
    apply_pin_config,
    canonical_trajectory,
    make_engine,
    summarize_divergence,
    world_fingerprint,
)

# Harness knobs beyond module flags (name -> type for CLI parsing).
HARNESS_KNOBS: dict[str, type] = {
    "DETERMINISM_SEED": int,
    "DETERMINISM_PINNING": bool,
}

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _parse_assignment(raw: str) -> tuple[str, object]:
    name, sep, value_raw = raw.partition("=")
    name = name.strip()
    value_raw = value_raw.strip()
    if not sep or not name or not value_raw:
        raise ValueError(f"expected NAME=VALUE, got {raw!r}")
    if name in HARNESS_KNOBS:
        typ = HARNESS_KNOBS[name]
    elif hasattr(se, name):
        current = getattr(se, name)
        typ = type(current)
        if typ not in (bool, int, float, str):
            raise ValueError(
                f"{name!r} has unsupported type {typ.__name__} for harness override"
            )
    else:
        raise ValueError(f"unknown harness variable {name!r} (not on sim_engine)")

    if typ is bool:
        lowered = value_raw.lower()
        if lowered in _TRUTHY:
            return name, True
        if lowered in _FALSY:
            return name, False
        raise ValueError(f"boolean {name} expects true/false, got {value_raw!r}")
    if typ is int:
        return name, int(value_raw)
    if typ is float:
        return name, float(value_raw)
    return name, value_raw


def _apply_overrides(overrides: dict[str, object]) -> dict[str, object]:
    saved: dict[str, object] = {}
    for key, value in overrides.items():
        saved[key] = getattr(se, key)
        setattr(se, key, value)
    return saved


def _restore_overrides(saved: dict[str, object]) -> None:
    for key, value in saved.items():
        setattr(se, key, value)


def run_fork(
    tick_count: int,
    roster_size: int,
    *,
    seed: int,
    overrides: dict[str, object] | None = None,
    checkpoint_bytes: bytes | None = None,
) -> tuple[list[dict], str]:
    """Run one headless fork; return benchmark records and world fingerprint."""
    overrides = overrides or {}
    saved = _apply_overrides(overrides)
    random.seed(seed)
    capture = BenchmarkCapture()
    with tempfile.TemporaryDirectory(prefix="fork_compare_") as tmpdir:
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
            _advance_engine(engine, tick_count, "a0")
            _drain_async_work(engine)
            return list(capture.records), world_fingerprint(engine)
        finally:
            se.DB_PATH = old_db_path
            if engine is not None:
                _shutdown_engine(engine)
            _restore_overrides(saved)


def _fork_result_label(overrides: dict[str, object]) -> str:
    if not overrides:
        return "baseline"
    return ", ".join(f"{k}={overrides[k]!r}" for k in sorted(overrides))


def compare_forks(
    tick_count: int,
    roster_size: int,
    *,
    seed: int,
    pin: bool,
    fork_b_overrides: dict[str, object],
    checkpoint_prep_ticks: int | None = None,
) -> dict:
    apply_pin_config(pin, seed)
    checkpoint_bytes = None
    run_ticks = tick_count
    if checkpoint_prep_ticks is not None:
        checkpoint_bytes = _prepare_checkpoint_bytes(
            checkpoint_prep_ticks, roster_size, seed
        )
        run_ticks = max(120, tick_count // 3)

    fork_a_overrides: dict[str, object] = {}
    records_a, fp_a = run_fork(
        run_ticks,
        roster_size,
        seed=seed,
        overrides=fork_a_overrides,
        checkpoint_bytes=checkpoint_bytes,
    )
    records_b, fp_b = run_fork(
        run_ticks,
        roster_size,
        seed=seed,
        overrides=fork_b_overrides,
        checkpoint_bytes=checkpoint_bytes,
    )

    bench_a = canonical_trajectory(records_a)
    bench_b = canonical_trajectory(records_b)
    benchmarks_match = bench_a == bench_b
    fingerprints_match = fp_a == fp_b
    identical = benchmarks_match and fingerprints_match

    return {
        "pin": pin,
        "seed": seed,
        "ticks": run_ticks,
        "roster": roster_size,
        "checkpoint_prep_ticks": checkpoint_prep_ticks,
        "fork_a": _fork_result_label(fork_a_overrides),
        "fork_b": _fork_result_label(fork_b_overrides),
        "fork_b_overrides": dict(fork_b_overrides),
        "benchmark_records_a": len(records_a),
        "benchmark_records_b": len(records_b),
        "world_fingerprint_a": fp_a,
        "world_fingerprint_b": fp_b,
        "benchmarks_match": benchmarks_match,
        "fingerprints_match": fingerprints_match,
        "identical": identical,
        "divergence_summary": summarize_divergence(records_a, records_b, fp_a, fp_b),
        "records_a": records_a,
        "records_b": records_b,
    }


def format_report(result: dict, *, identical_mode: bool) -> str:
    lines = [
        "fork_compare report (Phase A2)",
        f"  pin={result['pin']} seed={result['seed']} ticks={result['ticks']} "
        f"roster={result['roster']}",
    ]
    if result["checkpoint_prep_ticks"] is not None:
        lines.append(f"  checkpoint_prep_ticks={result['checkpoint_prep_ticks']}")
    lines.extend([
        f"  fork A: {result['fork_a']}",
        f"  fork B: {result['fork_b']}",
        f"  benchmark records: A={result['benchmark_records_a']} "
        f"B={result['benchmark_records_b']}",
        f"  world fingerprint A: {result['world_fingerprint_a'][:16]}…",
        f"  world fingerprint B: {result['world_fingerprint_b'][:16]}…",
        f"  benchmarks match: {result['benchmarks_match']}",
        f"  fingerprints match: {result['fingerprints_match']}",
    ])
    if result["identical"]:
        lines.append("FORK_COMPARE: BIT-IDENTICAL YES (benchmarks + fingerprint)")
    else:
        lines.append("FORK_COMPARE: BIT-IDENTICAL NO")
        lines.append("Divergence summary:")
        lines.append(result["divergence_summary"])
    if identical_mode:
        lines.append(
            "identical-input check: PASS"
            if result["identical"]
            else "identical-input check: FAIL"
        )
    return "\n".join(lines)


def write_report_artifacts(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "fork_compare_summary.json"
    bench_a_path = out_dir / "benchmarks_fork_a.jsonl"
    bench_b_path = out_dir / "benchmarks_fork_b.jsonl"

    payload = {k: v for k, v in result.items() if k not in ("records_a", "records_b")}
    summary_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    bench_a_path.write_text(canonical_trajectory(result["records_a"]), encoding="utf-8")
    bench_b_path.write_text(canonical_trajectory(result["records_b"]), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare two headless sim forks (benchmarks + world fingerprint)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=DEFAULT_TICK_COUNT,
        help=f"tick advances per fork (default {DEFAULT_TICK_COUNT})",
    )
    parser.add_argument("--roster", type=int, default=8, help="roster size (default 8)")
    parser.add_argument(
        "--pin",
        action="store_true",
        help="enable DETERMINISM_PINNING (required for identical-input guarantee)",
    )
    parser.add_argument(
        "--identical",
        action="store_true",
        help="sanity mode: two identical forks must match (implies --pin)",
    )
    parser.add_argument(
        "--var",
        metavar="NAME=VALUE",
        help="single variable for fork B (fork A stays baseline); e.g. WEATHER_ENABLED=false",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_DETERMINISM_SEED,
        help=f"random / DETERMINISM_SEED baseline (default {DEFAULT_DETERMINISM_SEED})",
    )
    parser.add_argument(
        "--checkpoint-prep-ticks",
        type=int,
        metavar="N",
        help="prepare in-memory checkpoint after N ticks, then fork from restore",
    )
    parser.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="write summary JSON and benchmark JSONL artifacts here",
    )
    args = parser.parse_args(argv)

    if args.ticks < 1:
        print("ERROR: --ticks must be >= 1", file=sys.stderr)
        return 1
    if args.roster < 1:
        print("ERROR: --roster must be >= 1", file=sys.stderr)
        return 1
    if args.identical and args.var:
        print("ERROR: --identical and --var are mutually exclusive", file=sys.stderr)
        return 1
    if not args.identical and not args.var:
        print("ERROR: specify --identical or --var NAME=VALUE", file=sys.stderr)
        return 1

    pin = args.pin or args.identical
    if args.identical and not pin:
        print("ERROR: --identical requires DETERMINISM_PINNING (--pin)", file=sys.stderr)
        return 1

    fork_b_overrides: dict[str, object] = {}
    if args.var:
        try:
            key, value = _parse_assignment(args.var)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        fork_b_overrides[key] = value

    if args.checkpoint_prep_ticks is not None and args.checkpoint_prep_ticks < 1:
        print("ERROR: --checkpoint-prep-ticks must be >= 1", file=sys.stderr)
        return 1

    print(
        f"fork_compare: pin={pin} seed={args.seed} ticks={args.ticks} "
        f"roster={args.roster} mode={'identical' if args.identical else 'one-var'}"
    )
    if fork_b_overrides:
        print(f"  fork B override: {fork_b_overrides}")
    print(f"  DETERMINISM_PINNING will be {pin} for both forks")
    print(f"  async drain timeout: {DRAIN_TIMEOUT_S}s")

    result = compare_forks(
        args.ticks,
        args.roster,
        seed=args.seed,
        pin=pin,
        fork_b_overrides=fork_b_overrides,
        checkpoint_prep_ticks=args.checkpoint_prep_ticks,
    )
    report = format_report(result, identical_mode=args.identical)
    print(report)

    if args.out is not None:
        write_report_artifacts(result, args.out)
        print(f"  artifacts written to {args.out}")

    if args.identical:
        return 0 if result["identical"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
