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
from _server.anomaly_radar import compute_anomalies  # noqa: E402


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
    # The very first sample must never fire (nothing to break against yet).
    first_ts = {a["timestamp"] for a in range_breaks}
    assert_true(100 not in first_ts, "first sample must not be reported as an anomaly")
    print("  OK range_break fires on a new session-lifetime max and a new session-lifetime min, "
          "not on the first sample or an in-range value")


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
    print("  OK new_rule_kind fires once per rule kind, on its first appearance only")


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
    print("  OK schism reports every metric:\"schism\" record, unconditionally")


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
    test_non_benchmark_and_malformed_lines_ignored()
    test_missing_file_returns_empty()
    run_http_tests()
    print("PASS")


if __name__ == "__main__":
    main()
