"""Deterministic smoke for the spectator prediction market (idea-04).

Covers validation, append/resolve persistence, resolution-frame round trips,
hit-rate calculation, and idempotent resolution without starting Flask or an
LLM service.

Run: uv run python scripts/idea04_prediction_market_smoke.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

from _server.predictions_store import PredictionsStore  # noqa: E402


def assert_true(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        path = Path(raw_dir) / "predictions.json"
        store = PredictionsStore(str(path))
        assert_true(store.history() == {"predictions": [], "hitRate": None},
                    "absent store should start empty")
        assert_true(store.submit("invalid", "question", "yes", 1) is None,
                    "invalid kind must be rejected")

        first = store.submit("rule", "Should the rule pass?", "yes", 120)
        second = store.submit("blueprint", "Should the plan pass?", "no", 121)
        assert_true(first == "1" and second == "2", "stable prediction ids")
        assert_true(not store.resolve(first, True, "yes", "bad-frame"),
                    "non-integer resolution frame must be rejected")
        assert_true(store.resolve(first, True, "yes", 135),
                    "first prediction should resolve")
        assert_true(store.resolve(second, False, "no", 136),
                    "second prediction should resolve")
        assert_true(not store.resolve(first, False, "no", 137),
                    "resolved prediction must be immutable")

        payload = store.history()
        assert_true(payload["hitRate"] == 0.5, f"unexpected hit rate: {payload}")
        rows = payload["predictions"]
        assert_true(rows[0]["resolved_frame_tick"] == 135,
                    "resolution frame should be retained")
        assert_true(rows[1]["resolved_frame_tick"] == 136,
                    "second resolution frame should be retained")

        # Verify atomic JSON persistence and startup reconstruction.
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert_true(on_disk["predictions"][0]["resolved_frame_tick"] == 135,
                    "resolution frame missing from JSON")
        restored = PredictionsStore(str(path))
        assert_true(restored.history() == payload,
                    "restored history must match persisted history")

    print("idea04 prediction market smoke: OK")


if __name__ == "__main__":
    main()
