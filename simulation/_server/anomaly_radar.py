"""Anomaly radar (idea-07, docs/plans/idea-07-anomaly-radar/plan.md; expanded
coverage and severity per idea-07b,
docs/plans/idea-07b-anomaly-console/plan.md): a read-only server-side reader
over the current run's benchmarks.jsonl.

No engine state, no /state key, no live-SimEngine access, no self.lock --
this module only parses the JSONL file already written by SessionLogger
(simulation/_server/logging_session.py). See specs/04-http-api.md's
"Anomaly radar (idea-07)" section for the exact detection semantics,
RANGE_BREAK_METRICS allowlist, and severity algorithm this implements.
"""

import json

# Exactly the 12 bounded/ratio metrics idea-07b §2 Answer 4 allowlists for
# range_break detection -- no others, no monotonic-counter auto-detection.
# Named module-level constant so the spec (specs/04-http-api.md) and this
# code cannot drift.
RANGE_BREAK_METRICS = frozenset({
    "specialization_entropy",
    "rule_adherence",
    "meme_adoption",
    "ecology_scarcity_index",
    "wealth_gini",
    "skill_spread",
    "cultural_carryover",
    "peer_prediction_accuracy",
    "contract_default_rate",
    "storage_utilization",
    "structure_condition",
    "population_median_age",
})


def _range_break_severity(prior_max, prior_min, value, direction):
    """Magnitude-scaled severity for a range_break, per specs/04-http-api.md
    "Anomaly radar (idea-07)" -> severity -> "range_break -- magnitude-scaled".

    `prior_max`/`prior_min` reflect every value seen for this metric before
    the current record (captured prior to folding the current value into the
    running max/min). Never divides by prior_range without checking it is
    > 0 first (the degenerate case, guaranteed on a metric's first-ever
    range_break this run).
    """
    if direction == "max":
        break_amount = value - prior_max
    else:
        break_amount = prior_min - value
    prior_range = prior_max - prior_min
    if prior_range <= 0:
        return "medium"
    ratio = break_amount / prior_range
    if ratio >= 1.0:
        return "high"
    if ratio >= 0.25:
        return "medium"
    return "low"


def compute_anomalies(benchmark_path):
    """Return the full list of detected anomalies for one benchmarks.jsonl
    file, recomputed from scratch on every call (stateless reader, no
    persisted detection state -- plan §2 Answer 1).

    Detects three kinds, in the order their triggering records appear in the
    file:

    - range_break: for each metric in RANGE_BREAK_METRICS, a value that
      exceeds every prior value seen so far this run for that same metric (a
      new session-lifetime max) or falls below every prior value seen so far
      this run for that same metric (a new session-lifetime min).
      Session-lifetime max/min tracking is per-metric (idea-07b §2 Answer 4).
    - new_rule_kind: the first time a rule kind appears in a
      `rule_kind_diversity` record's `detail.kinds` list that has not
      appeared in any earlier such record this run.
    - faction_split: every `metric: "faction_split"` record, reported as-is.

    Each entry carries a `severity` field ("high"/"medium"/"low") -- see
    specs/04-http-api.md for the exact per-kind rules.
    """
    anomalies = []
    sess_max = {}
    sess_min = {}
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
                if metric in RANGE_BREAK_METRICS:
                    if isinstance(value, (int, float)):
                        prior_max = sess_max.get(metric)
                        prior_min = sess_min.get(metric)
                        if prior_max is not None and value > prior_max:
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "range_break", "value": value,
                                "detail": {"direction": "max"},
                                "severity": _range_break_severity(
                                    prior_max, prior_min, value, "max"),
                            })
                        elif prior_min is not None and value < prior_min:
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "range_break", "value": value,
                                "detail": {"direction": "min"},
                                "severity": _range_break_severity(
                                    prior_max, prior_min, value, "min"),
                            })
                        sess_max[metric] = value if prior_max is None else max(prior_max, value)
                        sess_min[metric] = value if prior_min is None else min(prior_min, value)
                elif metric == "rule_kind_diversity":
                    detail = record.get("detail") or {}
                    kinds = detail.get("kinds") or []
                    for kind in kinds:
                        if kind not in seen_rule_kinds:
                            seen_rule_kinds.add(kind)
                            anomalies.append({
                                "timestamp": timestamp, "metric": metric,
                                "kind": "new_rule_kind", "value": kind,
                                "severity": "low",
                            })
                elif metric == "faction_split":
                    entry = {
                        "timestamp": timestamp, "metric": metric,
                        "kind": "faction_split", "value": value,
                        "severity": "high",
                    }
                    detail = record.get("detail")
                    if detail is not None:
                        entry["detail"] = detail
                    anomalies.append(entry)
    except OSError:
        return []
    return anomalies
