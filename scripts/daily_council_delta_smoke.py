"""Deterministic no-Ollama smoke for the Daily Council delta-protocol fix.

Proves that convening, per-tick advancing, and adjourning a Daily Council
each mark the right civilization keys dirty via `_mark_civ_dirty`, so
`GET /state?since=<frameTick>` (SimEngine.snapshot_delta) carries the live
`dailyCouncil` dict (and the `councilLog`/`councilDigests` history it feeds)
without requiring a full snapshot (page reload / stateGeneration bump).

No server, no Ollama -- pure engine calls, matching the pattern in
scripts/daily_council_state_probe.py and scripts/daily_council_smoke.py.

Run:
    uv run python scripts/daily_council_delta_smoke.py

The result is always written to scripts/out/daily_council_delta_smoke.json.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from daily_council_smoke import make_engine, seat_everyone  # noqa: E402

OUT_PATH = ROOT / "scripts" / "out" / "daily_council_delta_smoke.json"


class Checks:
    def __init__(self):
        self.details = []

    def check(self, condition, name, detail=None):
        if not condition:
            raise AssertionError(f"{name}: {detail or 'condition was false'}")
        self.details.append({"name": name, "pass": True, "detail": detail})


def exercise_delta_protocol(checks):
    if not se.DAILY_COUNCIL_ENABLED:
        checks.check(True, "daily_council_disabled_skip",
                     "DAILY_COUNCIL_ENABLED is off; nothing to exercise")
        return

    engine = make_engine(roster_size=5)

    # --- Step 1: convene must mark "dailyCouncil" dirty on the convening
    # frame itself (item 2), so a client polling with since==frameTick
    # (the "still on this frame" same-frame frame_tag path) sees it land
    # in the very next delta rather than a tick later.
    engine.frameTick = se.DAY_FRAMES
    since0 = engine.frameTick
    convened = engine._maybe_convene_daily_council()
    checks.check(convened, "council_convenes")

    delta1 = engine.snapshot_delta(since0)
    civ1 = delta1.get("civilization") or {}
    checks.check("dailyCouncil" in civ1, "convene_delta_includes_dailyCouncil",
                 sorted(civ1.keys()))
    checks.check(isinstance(civ1.get("dailyCouncil"), dict)
                 and civ1["dailyCouncil"].get("phase") == "convening",
                 "convene_delta_daily_council_has_convening_phase",
                 (civ1.get("dailyCouncil") or {}).get("phase"))

    # --- Step 2: advance the council to a new phase after the previous
    # delta consumed/discarded the convene-time dirty tag, and after the
    # frame clock has moved on (the normal steady-polling case). This
    # exercises the per-tick "dailyCouncil" mark inside
    # _maybe_advance_daily_council (item 1) -- the safety net for every
    # in-place mutation of the live council dict.
    since1 = delta1["frameTick"]
    checks.check(since1 == since0, "step1_frame_unchanged_by_convene", since1)
    engine.frameTick += 5
    seat_everyone(engine)
    council = engine.civilization["dailyCouncil"]
    phase_before = council["phase"]
    engine._maybe_advance_daily_council()
    phase_after = council["phase"]
    checks.check(phase_after != phase_before, "phase_advanced",
                 {"before": phase_before, "after": phase_after})

    delta2 = engine.snapshot_delta(since1)
    civ2 = delta2.get("civilization") or {}
    checks.check("dailyCouncil" in civ2, "advance_delta_includes_dailyCouncil",
                 sorted(civ2.keys()))
    checks.check(isinstance(civ2.get("dailyCouncil"), dict)
                 and civ2["dailyCouncil"].get("phase") == phase_after,
                 "advance_delta_daily_council_has_new_phase",
                 (civ2.get("dailyCouncil") or {}).get("phase"))

    # --- Step 3: force an adjourn directly (deterministic) and confirm the
    # very next delta carries both the cleared dailyCouncil and the new
    # councilLog record (item 3): the mark must fire AFTER
    # civilization["dailyCouncil"] = None, so the delta carries the
    # cleared value, not the pre-adjourn dict.
    since2 = delta2["frameTick"]
    engine.frameTick += 5
    adjourned = engine._adjourn_daily_council("smoke forced adjourn")
    checks.check(adjourned, "council_adjourns")
    checks.check(engine.civilization["dailyCouncil"] is None,
                 "engine_state_daily_council_cleared")

    delta3 = engine.snapshot_delta(since2)
    civ3 = delta3.get("civilization") or {}
    checks.check("dailyCouncil" in civ3, "adjourn_delta_includes_dailyCouncil_key",
                 sorted(civ3.keys()))
    checks.check(civ3.get("dailyCouncil") is None,
                 "adjourn_delta_daily_council_is_cleared", civ3.get("dailyCouncil"))
    checks.check("councilLog" in civ3 and isinstance(civ3["councilLog"], list)
                 and bool(civ3["councilLog"]),
                 "adjourn_delta_includes_council_log", civ3.get("councilLog"))
    new_record = civ3["councilLog"][0]
    transcript = new_record.get("transcript") or []
    checks.check(
        any(entry.get("type") == "adjourn" and entry.get("text") == "smoke forced adjourn"
            for entry in transcript),
        "adjourn_delta_council_log_has_new_record",
        {"outcome": new_record.get("outcome"), "transcript_tail": transcript[-1:]})


def main():
    checks = Checks()
    try:
        exercise_delta_protocol(checks)
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
