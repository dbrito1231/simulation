"""Read-only observer for an Always-on-PIANO live soak.

Selects the newest session directory when started, tails that session's JSONL
logs, prints a compact progress line each minute, and writes a summary beside
the session logs. It never calls or controls the running server.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = ROOT / "simulation" / "logs"


def newest_session() -> Path:
    candidates = [p for p in LOG_ROOT.iterdir()
                  if p.is_dir() and (p / "llm.jsonl").exists()
                  and (p / "benchmarks.jsonl").exists()]
    if not candidates:
        raise SystemExit(f"no session logs found under {LOG_ROOT}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def tail_jsonl(path: Path, offset: int):
    if not path.exists():
        return [], offset
    with path.open("r", encoding="utf-8") as fh:
        fh.seek(offset)
        lines = fh.readlines()
        offset = fh.tell()
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return records, offset


def prompt_module_reports(record: dict) -> str | None:
    request = record.get("request") or {}
    for message in request.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else ""
        if not isinstance(content, str):
            continue
        marker = "Module reports (Cognitive Controller — weigh these):"
        if marker in content:
            return content.split(marker, 1)[1].split("Available actions:", 1)[0].strip()
    return None


def median(values):
    return round(statistics.median(values), 1) if values else None


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.5)))
    return round(ordered[index], 1)


def is_decision_record(record: dict) -> bool:
    """Module runner records share llm.jsonl but do not carry a decision."""
    return not record.get("module") and isinstance(record.get("decision"), dict)


def is_fallback(record: dict) -> bool:
    if record.get("error"):
        return True
    reasoning = str((record.get("decision") or {}).get("reasoning") or "").lower()
    return "fallback" in reasoning


def piano_latency_attempts(record: dict) -> int:
    """Read per-module attempt counts when a session's latency metric emits them."""
    value, detail = record.get("value"), record.get("detail") or {}
    counts = 0
    if isinstance(value, dict):
        for module_value in value.values():
            if isinstance(module_value, dict):
                count = module_value.get("count", module_value.get("attempts"))
                if isinstance(count, (int, float)):
                    counts += int(count)
    for key in ("counts", "per_module_counts", "attempt_counts"):
        mapping = detail.get(key) if isinstance(detail, dict) else None
        if isinstance(mapping, dict):
            counts = max(counts, sum(int(v) for v in mapping.values()
                                     if isinstance(v, (int, float))))
    return counts


def build_summary(label: str, session: Path, started: float, ended: float,
                  llm_records: list[dict], benchmarks: list[dict]) -> dict:
    decisions = [record for record in llm_records if is_decision_record(record)]
    module_records = [record for record in llm_records if record.get("module")]
    latencies = [r["latency_ms"] for r in decisions if isinstance(r.get("latency_ms"), (int, float))]
    errors = [r for r in decisions if r.get("error")]
    fallbacks = [r for r in decisions if is_fallback(r)]
    module_failure_records = [r for r in module_records
                              if r.get("error") in {"piano_module_timeout", "module_refresh_timeout"}]
    refresh_failures = len(module_failure_records)
    refresh_metric_seen = False
    drop_metric_seen = False
    legacy_successes = 0
    latency_attempts = 0
    note_ages, pulse_values = [], []
    for record in benchmarks:
        if record.get("metric") == "module_note_age" and isinstance(record.get("value"), dict):
            value = record["value"]
            if isinstance(value.get("avg_s"), (int, float)):
                note_ages.append(value["avg_s"])
        elif record.get("metric") == "module_pulse_work":
            value = record.get("value")
            pulse_values.extend(v for v in (value if isinstance(value, list) else [value])
                                if isinstance(v, (int, float)))
        elif record.get("metric") == "module_refresh_failures":
            refresh_metric_seen = True
            value = record.get("value")
            if isinstance(value, (int, float)):
                refresh_failures = max(refresh_failures, int(value))
        elif record.get("metric") == "piano_module_drops":
            drop_metric_seen = True
            value = record.get("value")
            if isinstance(value, (int, float)):
                refresh_failures = max(refresh_failures, int(value))
        elif record.get("metric") == "module_total" and isinstance(record.get("value"), (int, float)):
            legacy_successes += int(record["value"])
        elif record.get("metric") == "piano_module_latency":
            latency_attempts += piano_latency_attempts(record)
    failure_metric = "module_refresh_failures" if refresh_metric_seen else (
        "piano_module_drops" if drop_metric_seen else "module_refresh_failures")
    if failure_metric == "module_refresh_failures":
        failure_attempts = sum(pulse_values)
    elif latency_attempts:
        failure_attempts = latency_attempts
    elif legacy_successes or refresh_failures:
        # Current legacy sessions emit successful module_total per benchmark
        # period and cumulative drops; together they cover all attempts.
        failure_attempts = legacy_successes + refresh_failures
    else:
        failure_attempts = None
    samples = []
    for record in decisions:
        report = prompt_module_reports(record)
        if report is not None and len(samples) < 3:
            samples.append(report)
    return {
        "label": label,
        "session": session.name,
        "started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(),
        "ended_at": datetime.fromtimestamp(ended, timezone.utc).isoformat(),
        "duration_s": round(ended - started, 1),
        "decision_count": len(decisions),
        "decision_error_count": len(errors),
        "decision_error_rate": round(len(errors) / len(decisions), 4) if decisions else None,
        "decision_fallback_count": len(fallbacks),
        "decision_fallback_rate": round(len(fallbacks) / len(decisions), 4) if decisions else None,
        "decision_latency_ms": {"p50": percentile(latencies, .50), "p90": percentile(latencies, .90)},
        "module_failure_metric": failure_metric,
        "module_failure_count": refresh_failures,
        "module_failure_attempts": failure_attempts,
        "module_failure_rate": round(refresh_failures / failure_attempts, 4)
                               if failure_attempts else None,
        "module_note_age_s": {"median": median(note_ages), "max": max(note_ages, default=None)},
        "module_pulse_work": pulse_values,
        "module_pulse_values": pulse_values,
        "module_pulse_zero_count": sum(v == 0 for v in pulse_values),
        "decision_prompt_module_report_samples": samples,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="summary label, used in soak-<label>.json")
    parser.add_argument("--minutes", type=float, default=45, help="observation duration (default: 45)")
    args = parser.parse_args()
    if args.minutes <= 0:
        raise SystemExit("--minutes must be positive")
    session = newest_session()
    paths = {"llm": session / "llm.jsonl", "benchmarks": session / "benchmarks.jsonl"}
    offsets = {name: path.stat().st_size if path.exists() else 0 for name, path in paths.items()}
    llm_records, benchmarks = [], []
    started, deadline, next_progress = time.time(), time.time() + args.minutes * 60, time.time() + 60
    print(f"soak monitor: session={session.name} label={args.label} minutes={args.minutes:g}")
    while time.time() < deadline:
        for name, path in paths.items():
            records, offsets[name] = tail_jsonl(path, offsets[name])
            if name == "llm":
                llm_records.extend(records)
            else:
                benchmarks.extend(records)
        if time.time() >= next_progress:
            summary = build_summary(args.label, session, started, time.time(), llm_records, benchmarks)
            print("progress " + json.dumps({
                "elapsed_s": int(time.time() - started),
                "decisions": summary["decision_count"],
                "decision_p50_ms": summary["decision_latency_ms"]["p50"],
                "module_failures": summary["module_failure_count"],
            }, sort_keys=True))
            next_progress += 60
        time.sleep(1)
    for name, path in paths.items():
        records, offsets[name] = tail_jsonl(path, offsets[name])
        (llm_records if name == "llm" else benchmarks).extend(records)
    summary = build_summary(args.label, session, started, time.time(), llm_records, benchmarks)
    output = LOG_ROOT / f"soak-{args.label}.json"
    output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"DONE {output}")


if __name__ == "__main__":
    main()
