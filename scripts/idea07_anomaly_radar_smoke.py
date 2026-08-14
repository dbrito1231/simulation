"""Deterministic smoke for the anomaly radar (idea-07,
docs/plans/idea-07-anomaly-radar/plan.md).

Covers the pure `compute_anomalies()` reader (range_break, new_rule_kind,
schism, and no-anomaly-on-first-sample) against synthetic benchmarks.jsonl
content, plus the real `GET /anomalies` route (server.app.test_client())
flag-on and flag-off shapes, same pattern as
scripts/_god_mode_smoke/compiler_http.py. No Ollama.

Run: uv run python scripts/idea07_anomaly_radar_smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _server.anomaly_radar import compute_anomalies, RANGE_BREAK_METRICS  # noqa: E402


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError(msg)


def _write_benchmark_lines(path, records):
    with open(path, "w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


def _benchmark_record(metric, value, frame_tick, detail=None):
    record = {"type": "benchmark", "metric": metric, "value": value,
              "frame_tick": frame_tick}
    if detail is not None:
        record["detail"] = detail
    return record


def test_range_break_max_and_min():
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("specialization_entropy", 1.0, 100, {"counts": {}}),
        _benchmark_record("specialization_entropy", 1.5, 200, {"counts": {}}),  # new max
        _benchmark_record("specialization_entropy", 1.2, 300, {"counts": {}}),  # within range
        _benchmark_record("specialization_entropy", 0.5, 400, {"counts": {}}),  # new min
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    range_breaks = [a for a in anomalies if a["kind"] == "range_break"]
    assert_true(len(range_breaks) == 2, f"expected 2 range breaks, got {range_breaks}")
    assert_true(range_breaks[0]["timestamp"] == 200 and range_breaks[0]["value"] == 1.5
                and range_breaks[0]["detail"]["direction"] == "max", range_breaks[0])
    assert_true(range_breaks[1]["timestamp"] == 400 and range_breaks[1]["value"] == 0.5
                and range_breaks[1]["detail"]["direction"] == "min", range_breaks[1])
    assert_true(range_breaks[0]["metric"] == "specialization_entropy", range_breaks[0])
    # The first-ever break on a metric this run is the guaranteed degenerate
    # case (prior_max == prior_min, prior_range == 0): fixed "medium", no
    # division. The second break has a real prior_range (1.0 to 1.5 = 0.5)
    # and break_amount 0.5 -> ratio 1.0 -> "high".
    assert_true(range_breaks[0]["severity"] == "medium",
                f"first-ever break on a metric must use the degenerate-case fixed severity, "
                f"got {range_breaks[0]}")
    assert_true(range_breaks[1]["severity"] == "high", range_breaks[1])
    # The very first sample must never fire (nothing to break against yet).
    first_ts = {a["timestamp"] for a in range_breaks}
    assert_true(100 not in first_ts, "first sample must not be reported as an anomaly")
    print("  OK range_break fires on a new session-lifetime max and a new session-lifetime min, "
          "not on the first sample or an in-range value, with magnitude-scaled severity")


def test_new_rule_kind_first_occurrence_only():
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("rule_kind_diversity", 1, 100, {"kinds": ["priority"]}),
        _benchmark_record("rule_kind_diversity", 2, 200, {"kinds": ["priority", "resource_tax"]}),
        _benchmark_record("rule_kind_diversity", 2, 300, {"kinds": ["priority", "resource_tax"]}),
        _benchmark_record("rule_kind_diversity", 3, 400,
                          {"kinds": ["priority", "resource_tax", "custom"]}),
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    new_kinds = [a for a in anomalies if a["kind"] == "new_rule_kind"]
    assert_true([a["value"] for a in new_kinds] == ["priority", "resource_tax", "custom"],
                f"expected 3 first-occurrences in order, got {new_kinds}")
    assert_true([a["timestamp"] for a in new_kinds] == [100, 200, 400], new_kinds)
    assert_true(all(a["metric"] == "rule_kind_diversity" for a in new_kinds), new_kinds)
    assert_true(all(a["severity"] == "low" for a in new_kinds),
                f"new_rule_kind must use the fixed \"low\" severity, got {new_kinds}")
    print("  OK new_rule_kind fires once per rule kind, on its first appearance only, "
          "always \"low\" severity")


def test_schism_reported_every_time():
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("schism", 3, 150, {"parent": "village", "child": "village_2",
                                              "belief": "b1", "rule": "r1"}),
        _benchmark_record("schism", 2, 500, {"parent": "village_2", "child": "village_3",
                                              "belief": "b2", "rule": "r2"}),
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    schisms = [a for a in anomalies if a["kind"] == "schism"]
    assert_true(len(schisms) == 2, f"expected every schism record reported, got {schisms}")
    assert_true(schisms[0]["value"] == 3 and schisms[0]["timestamp"] == 150, schisms[0])
    assert_true(schisms[0]["detail"] == {"parent": "village", "child": "village_2",
                                          "belief": "b1", "rule": "r1"}, schisms[0])
    assert_true(schisms[1]["value"] == 2 and schisms[1]["timestamp"] == 500, schisms[1])
    assert_true(all(a["metric"] == "schism" for a in schisms), schisms)
    assert_true(all(a["severity"] == "high" for a in schisms),
                f"schism must use the fixed \"high\" severity, got {schisms}")
    print("  OK schism reports every metric:\"schism\" record, unconditionally, "
          "always \"high\" severity")


def test_range_break_allowlist_per_metric_independent_tracking():
    """Several allowlisted metrics interleaved -- each must get its own
    session-lifetime max/min, independent of the others (idea-07b §2
    Answer 4: per-metric, not one shared max/min)."""
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("rule_adherence", 0.5, 100),
        _benchmark_record("skill_spread", 0.5, 110),
        _benchmark_record("rule_adherence", 0.9, 200),   # new max for rule_adherence only
        _benchmark_record("skill_spread", 0.5, 210),      # not a break (equal to baseline)
        _benchmark_record("skill_spread", 0.9, 300),      # new max for skill_spread, now
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    range_breaks = [a for a in anomalies if a["kind"] == "range_break"]
    assert_true(len(range_breaks) == 2, f"expected exactly 2 breaks, got {range_breaks}")
    assert_true(range_breaks[0]["metric"] == "rule_adherence" and range_breaks[0]["timestamp"] == 200,
                range_breaks[0])
    assert_true(range_breaks[1]["metric"] == "skill_spread" and range_breaks[1]["timestamp"] == 300,
                range_breaks[1])
    print("  OK range_break session-lifetime max/min tracking is independent per metric")


def test_range_break_non_allowlisted_metric_never_fires():
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("memory_store_size", 10, 100),
        _benchmark_record("memory_store_size", 20, 200),
        _benchmark_record("memory_store_size", 30, 300),
        _benchmark_record("memory_store_size", 5, 400),
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    assert_true(anomalies == [],
                f"a non-allowlisted monotonic counter must never produce a range_break, "
                f"got {anomalies}")
    assert_true("memory_store_size" not in RANGE_BREAK_METRICS,
                "sanity: memory_store_size must not be in the allowlist")
    print("  OK a non-allowlisted metric (memory_store_size) never produces a range_break, "
          "even when every sample sets a new monotonic max")


def test_range_break_severity_all_tiers_reachable():
    """Walks wealth_gini through a sequence engineered to hit the degenerate
    case, then all three magnitude tiers, per specs/04-http-api.md's exact
    thresholds:
      ratio <  0.25            -> "low"
      0.25 <= ratio < 1.0      -> "medium"
      ratio >= 1.0             -> "high"
    """
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    records = [
        _benchmark_record("wealth_gini", 0.2, 100),   # baseline, no anomaly
        _benchmark_record("wealth_gini", 0.6, 200),   # new max; prior_range == 0 -> degenerate
        _benchmark_record("wealth_gini", 0.1, 300),   # new min; break=0.1, range=0.4 -> ratio 0.25
        _benchmark_record("wealth_gini", 0.65, 400),  # new max; break=0.05, range=0.55 -> ratio ~0.09
        _benchmark_record("wealth_gini", 1.5, 500),   # new max; break=0.85, range=0.55 -> ratio ~1.55
    ]
    _write_benchmark_lines(path, records)
    anomalies = compute_anomalies(path)
    range_breaks = [a for a in anomalies if a["kind"] == "range_break"]
    assert_true(len(range_breaks) == 4, f"expected 4 breaks, got {range_breaks}")
    assert_true(range_breaks[0]["severity"] == "medium",
                f"first-ever break (prior_range == 0) must be the fixed degenerate \"medium\", "
                f"got {range_breaks[0]}")
    assert_true(range_breaks[1]["severity"] == "medium",
                f"ratio exactly 0.25 must land in the inclusive-low \"medium\" tier, "
                f"got {range_breaks[1]}")
    assert_true(range_breaks[2]["severity"] == "low",
                f"ratio well under 0.25 must be \"low\", got {range_breaks[2]}")
    assert_true(range_breaks[3]["severity"] == "high",
                f"ratio >= 1.0 must be \"high\", got {range_breaks[3]}")
    print("  OK range_break severity reaches all three magnitude tiers (low/medium/high) "
          "and the degenerate prior_range == 0 case never raises and yields \"medium\"")


def test_non_benchmark_and_malformed_lines_ignored():
    tmpdir = tempfile.mkdtemp()
    path = str(Path(tmpdir) / "benchmarks.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write(json.dumps({"type": "activity", "message": "irrelevant"}) + "\n")
        fh.write("\n")
        fh.write(json.dumps(_benchmark_record("schism", 1, 10, {"parent": "a", "child": "b",
                                                                  "belief": "x", "rule": "y"})) + "\n")
    anomalies = compute_anomalies(path)
    assert_true(len(anomalies) == 1 and anomalies[0]["kind"] == "schism", anomalies)
    print("  OK malformed lines and non-benchmark records are skipped without raising")


def test_missing_file_returns_empty():
    anomalies = compute_anomalies(str(Path(tempfile.mkdtemp()) / "does_not_exist.jsonl"))
    assert_true(anomalies == [], anomalies)
    print("  OK a missing benchmarks.jsonl path returns an empty list, not an error")


def run_http_tests():
    import server  # noqa: E402  (heavy import: real engine, real state.db READ only)

    client = server.app.test_client()

    old_flag = se.ANOMALY_RADAR_ENABLED
    try:
        # Flag-off: clean no-op shape, not a 404/disabled error.
        se.ANOMALY_RADAR_ENABLED = False
        resp = client.get("/anomalies")
        assert_true(resp.status_code == 200, resp.status_code)
        assert_true(resp.get_json() == {"ok": True, "enabled": False, "anomalies": []},
                    resp.get_json())
        print("  OK GET /anomalies with ANOMALY_RADAR_ENABLED=False returns the flag-off no-op shape")

        # Flag-on: real route reads the live session_logger's benchmarks.jsonl.
        se.ANOMALY_RADAR_ENABLED = True
        server.session_logger.log_benchmark("schism", 4, server.engine.frameTick,
                                            {"parent": "village", "child": "village_http_smoke",
                                             "belief": "b_http", "rule": "r_http"})
        server.session_logger.flush_benchmarks()
        resp = client.get("/anomalies")
        assert_true(resp.status_code == 200, resp.status_code)
        body = resp.get_json()
        assert_true(body["ok"] is True and body["enabled"] is True, body)
        schism_entries = [a for a in body["anomalies"] if a["kind"] == "schism"
                          and a.get("detail", {}).get("child") == "village_http_smoke"]
        assert_true(schism_entries, f"expected the just-flushed schism record via HTTP, got {body}")
        print("  OK GET /anomalies with the flag on reads the live session_logger's "
              "benchmarks.jsonl and reports a freshly flushed schism record")
    finally:
        se.ANOMALY_RADAR_ENABLED = old_flag


def main():
    print("idea07_anomaly_radar_smoke")
    test_range_break_max_and_min()
    test_new_rule_kind_first_occurrence_only()
    test_schism_reported_every_time()
    test_range_break_allowlist_per_metric_independent_tracking()
    test_range_break_non_allowlisted_metric_never_fires()
    test_range_break_severity_all_tiers_reachable()
    test_non_benchmark_and_malformed_lines_ignored()
    test_missing_file_returns_empty()
    run_http_tests()
    print("PASS")


if __name__ == "__main__":
    main()
