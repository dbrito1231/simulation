"""Anomaly radar (idea-07, docs/plans/idea-07-anomaly-radar/plan.md): a
read-only server-side reader over the current run's benchmarks.jsonl.

No engine state, no /state key, no live-SimEngine access, no self.lock --
this module only parses the JSONL file already written by SessionLogger
(simulation/_server/logging_session.py). See specs/04-http-api.md's
"Anomaly radar (idea-07)" section for the exact detection semantics and
response shape this implements.
"""

import json


def compute_anomalies(benchmark_path):
    """Return the full list of detected anomalies for one benchmarks.jsonl
    file, recomputed from scratch on every call (stateless reader, no
    persisted detection state -- plan §2 Answer 1).

    Detects three kinds, in the order their triggering records appear in the
    file:

    - range_break: a `specialization_entropy` value that exceeds every prior
      value seen so far this run (a new session-lifetime max) or falls below
      every prior value seen so far this run (a new session-lifetime min).
    - new_rule_kind: the first time a rule kind appears in a
      `rule_kind_diversity` record's `detail.kinds` list that has not
      appeared in any earlier such record this run.
    - schism: every `metric: "schism"` record, reported as-is.
    """
    anomalies = []
    sess_max = None
    sess_min = None
    seen_rule_kinds = set()
    try:
        with open(benchmark_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(record, dict) or record.get("type") != "benchmark":
                    continue
                metric = record.get("metric")
                timestamp = record.get("frame_tick")
                value = record.get("value")
                if metric == "specialization_entropy":
                    if isinstance(value, (int, float)):
                        if sess_max is not None and value > sess_max:
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "range_break", "value": value,
                                "detail": {"direction": "max"},
                            })
                        elif sess_min is not None and value < sess_min:
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "range_break", "value": value,
                                "detail": {"direction": "min"},
                            })
                        sess_max = value if sess_max is None else max(sess_max, value)
                        sess_min = value if sess_min is None else min(sess_min, value)
                elif metric == "rule_kind_diversity":
                    detail = record.get("detail") or {}
                    kinds = detail.get("kinds") or []
                    for kind in kinds:
                        if kind not in seen_rule_kinds:
                            seen_rule_kinds.add(kind)
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "new_rule_kind", "value": kind,
                            })
                elif metric == "schism":
                    entry = {
                        "timestamp": timestamp, "metric": metric,
                        "kind": "schism", "value": value,
                    }
                    detail = record.get("detail")
                    if detail is not None:
                        entry["detail"] = detail
                    anomalies.append(entry)
    except OSError:
        return []
    return anomalies
