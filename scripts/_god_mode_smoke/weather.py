# Sovereign God mode Phase 6 -- divine weather override.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_weather_override_enters_forced_state_without_rng():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        rng_state_before = random.getstate()
        applied = _apply_weather_override(engine, "req-weather-rng-1", "storm",
                                          districts=districts, duration=5000)
        assert_true(applied["ok"], applied)
        rng_state_after = random.getstate()
        assert_true(rng_state_before == rng_state_after,
                    "_weather_enter_forced must never draw RNG")

        w = engine.civilization["weather"]
        assert_true(w["state"] == "storm", w)
        assert_true(w["districts"] == districts, w)
        assert_true(w["exitFrame"] == applied["outcome"]["expiresFrame"], w)
        print("  OK weather_override enters the requested state with zero RNG draws; "
              "weather state/districts/exitFrame set from the operator input")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_exit_frame_matches_event_expiry():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-exitframe-1", "clearing",
                                          districts=districts, duration=7000)
        assert_true(applied["ok"], applied)
        event = engine.civilization["godState"]["activeEvents"][-1]
        assert_true(event["kind"] == "weather_override" and event["expiresFrame"] == engine.frameTick + 7000,
                    event)
        w = engine.civilization["weather"]
        assert_true(w["exitFrame"] == event["expiresFrame"], (w, event))
        print("  OK weather['exitFrame'] equals the applied event's expiresFrame exactly")
    finally:
        se.GOD_MODE_ENABLED = old


def test_natural_tick_weather_does_not_transition_while_override_holds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-hold-1", "storm",
                                          districts=districts, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        exit_frame = engine.civilization["weather"]["exitFrame"]
        engine.frameTick = exit_frame - 1
        for _ in range(5):
            engine._tick_weather()
            w = engine.civilization["weather"]
            assert_true(w["state"] == "storm" and w["exitFrame"] == exit_frame,
                        "natural _tick_weather transitioned while an override was still active")
        print("  OK the natural _tick_weather cycle never transitions while an override holds "
              "(right up to, but not past, its exitFrame)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_expiry_handoff_all_four_states():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_random_random = random.random
    try:
        districts = ["forest"]  # overwritten per engine below to a real district

        def _handoff_for(state, forced_random=None):
            engine = make_engine()
            real_districts = sorted(engine.civilization["districts"].keys())[:1]
            applied = _apply_weather_override(
                engine, f"req-handoff-{state}", state,
                districts=(None if state == "clear" else real_districts),
                duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
            assert_true(applied["ok"], applied)
            engine.frameTick = engine.civilization["weather"]["exitFrame"]
            random.seed(12345)
            if forced_random is not None:
                random.random = lambda: forced_random
            try:
                engine._expire_divine_effects()
            finally:
                random.random = old_random_random
            event = engine.civilization["godState"]["activeEvents"][-1]
            assert_true(event["status"] == "expired", event)
            return engine.civilization["weather"]["state"]

        assert_true(_handoff_for("clear") == "gathering",
                    "clear override must hand off to 'gathering' on expiry")
        assert_true(_handoff_for("gathering", forced_random=0.0) == "storm",
                    "gathering override (forced storm branch) must hand off to 'storm'")
        assert_true(_handoff_for("gathering", forced_random=0.99) == "clear",
                    "gathering override (forced clear branch) must hand off to 'clear'")
        assert_true(_handoff_for("storm") == "clearing",
                    "storm override must hand off to 'clearing' on expiry")
        assert_true(_handoff_for("clearing") == "clear",
                    "clearing override must hand off to 'clear' on expiry")
        print("  OK expiry hands off to the natural cycle's successor for all four overridden "
              "states, including both probability branches out of 'gathering'")
    finally:
        random.random = old_random_random
        se.GOD_MODE_ENABLED = old


def test_weather_override_cancel_runs_same_handoff():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-cancel-1", "storm",
                                          districts=districts, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]

        random.seed(999)
        cancelled = engine.god_cancel(event_id)
        assert_true(cancelled == {"ok": True, "cancelled": True, "targetId": event_id,
                                  "targetKind": "weather_override"}, cancelled)
        event = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "cancelled", event)
        assert_true(engine.civilization["weather"]["state"] == "clearing",
                    "cancel must run the same storm -> clearing handoff as expiry")
        print("  OK god_cancel on an active weather override runs the same handoff and "
              "returns cleanly")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        real_district = sorted(engine.civilization["districts"].keys())[0]

        unknown_state = engine.god_preview(_weather_envelope("hurricane", districts=[real_district]))
        assert_true(unknown_state["ok"] is False and "state must be one of" in unknown_state["reason"],
                    unknown_state)

        unknown_district = engine.god_preview(_weather_envelope("storm", districts=["not-a-real-district"]))
        assert_true(unknown_district == {"ok": False, "reason": "unknown district id 'not-a-real-district'"},
                    unknown_district)

        old_weather_enabled = se.WEATHER_ENABLED
        se.WEATHER_ENABLED = False
        try:
            disabled = engine.god_preview(_weather_envelope("storm", districts=[real_district]))
            assert_true(disabled == {"ok": False, "reason": "weather_override requires WEATHER_ENABLED"},
                        disabled)
        finally:
            se.WEATHER_ENABLED = old_weather_enabled

        print("  OK weather_override rejects an unknown state name, an unknown district id, "
              "and WEATHER_ENABLED=False")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_reversibility_class_and_preview_disclosure():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        s1 = _add_test_structure(engine, condition=50.0, district_id=district)
        s2 = _add_test_structure(engine, condition=0.0, district_id=district)
        s2["isRuin"] = True  # must not be counted as at-risk

        preview = engine.god_preview(_weather_envelope("storm", districts=[district]))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "consequential", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["districts"] == [district], outcome)
        assert_true(outcome["atRiskStructureCount"] == 1, outcome)
        assert_true(outcome["warning"] and "PERMANENTLY damage" in outcome["warning"], outcome)
        print("  OK weather_override reversibilityClass is 'consequential'; preview discloses the "
              "affected districts and the count of non-ruined structures at risk in them")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_replace_requires_replace_effect_id():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied1 = _apply_weather_override(engine, "req-weather-replace-1", "storm",
                                           districts=[district], duration=5000)
        assert_true(applied1["ok"], applied1)
        event_id = applied1["outcome"]["interventionId"]

        no_replace = engine.god_preview(_weather_envelope("clear"))
        assert_true(no_replace["ok"] is False and "already active" in no_replace["reason"], no_replace)

        replace_preview = engine.god_preview(_weather_envelope("clear", replace_effect_id=event_id))
        assert_true(replace_preview["ok"], replace_preview)
        replace_applied = engine.god_apply(replace_preview["previewId"], "req-weather-replace-2")
        assert_true(replace_applied["ok"], replace_applied)

        original = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(original["status"] == "replaced", original)
        assert_true(engine.civilization["weather"]["state"] == "clear", engine.civilization["weather"])
        print("  OK a second override without replaceEffectId is rejected while one is active; "
              "with a matching replaceEffectId it replaces cleanly, closing the previous as 'replaced'")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_survives_save_restore():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_weather_roundtrip.db")
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied = _apply_weather_override(engine, "req-weather-restore-1", "storm",
                                          districts=[district], duration=20000)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        event = next(e for e in restored.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "active", event)
        assert_true(event["expiresFrame"] == expires_frame,
                    "expiresFrame must round-trip as an ABSOLUTE frame")
        assert_true(restored.civilization["weather"]["state"] == "storm", restored.civilization["weather"])
        print("  OK an active weather override survives save_state/restore_state with its "
              "absolute expiresFrame intact; still in activeEvents and still active")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_weather_override_restore_time_expiry_closes_and_hands_off_once():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_weather_restore_expiry.db")
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied = _apply_weather_override(engine, "req-weather-restore-expiry-1", "storm",
                                          districts=[district],
                                          duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]
        # Advance PAST expiry without running the tick-based sweep -- simulates
        # a save captured after the override's clock ran out but before
        # _expire_divine_effects next ran.
        engine.frameTick = expires_frame + 500

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        divine_lines_before = len(restored.civilization.get("chronicle") or [])

        random.seed(42)
        restored._expire_divine_effects(restore=True)
        event = next(e for e in restored.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "restore-closed", event)
        assert_true(restored.civilization["weather"]["state"] == "clearing",
                    "restore-time expiry must still hand off (storm -> clearing)")

        # A second restore-time sweep against the already-closed event must
        # be a no-op -- the handoff (and any narration it would produce)
        # fires exactly once.
        state_after_first = restored.civilization["weather"]["state"]
        restored._expire_divine_effects(restore=True)
        assert_true(restored.civilization["weather"]["state"] == state_after_first,
                    "a second restore-time expiry sweep re-fired the handoff")
        assert_true(event["status"] == "restore-closed", event)
        print("  OK an override past-expiry at restore time closes exactly once and hands off "
              "exactly once (a second sweep is a verified no-op)")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_weather_flag_off_natural_cycle_unaffected():
    """The god-mode machinery participates in weather ONLY through an
    explicitly-applied weather_override; with GOD_MODE_ENABLED False (or
    simply never invoked), the natural _tick_weather/_weather_enter cycle
    must behave identically -- proven here by seeding the RNG and comparing
    a multi-tick trajectory between a GOD_MODE_ENABLED=False engine and a
    GOD_MODE_ENABLED=True engine on which no override is ever applied."""
    old = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = False
        baseline = make_engine()
        random.seed(2026)
        baseline_trajectory = []
        for _ in range(12):
            baseline.frameTick += se.GOODS_TICK_FRAMES
            baseline._tick_weather()
            w = baseline.civilization["weather"]
            baseline_trajectory.append((w["state"], tuple(w["districts"]), w["exitFrame"]))

        se.GOD_MODE_ENABLED = True
        divine = make_engine()
        random.seed(2026)
        divine_trajectory = []
        for _ in range(12):
            divine.frameTick += se.GOODS_TICK_FRAMES
            divine._tick_weather()
            w = divine.civilization["weather"]
            divine_trajectory.append((w["state"], tuple(w["districts"]), w["exitFrame"]))

        assert_true(baseline_trajectory == divine_trajectory,
                    "the natural weather cycle diverged with GOD_MODE_ENABLED on but unused")
        print(f"  OK a {len(baseline_trajectory)}-tick natural weather trajectory (seeded RNG) is "
              f"byte-identical whether GOD_MODE_ENABLED is off or on-but-unused")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Optional Phase 8 smoke -- free-prose story compiler ------------------
# No Ollama call ever happens here: every test monkeypatches
# engine.d["lm_complete"] directly (the same dict object SimEngine.__init__
# stores as self.d -- see make_engine's deps above), exactly as the task
# instructs. GOD_COMPILER_ENABLED is patched per-test on the `se` module the
# same way GOD_MODE_ENABLED already is elsewhere in this file.
