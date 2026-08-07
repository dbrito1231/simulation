# Sovereign God mode Phase 2 -- flag/token gate, preview/idempotency, persistence, expiry, benchmarks.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_flag_off_inert():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = False
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("Rain."))
        assert_true(preview == {"ok": False, "reason": "god mode disabled"}, preview)
        applied = engine.god_apply("whatever", "req")
        assert_true(applied == {"ok": False, "reason": "god mode disabled"}, applied)
        sight = engine.god_sight()
        assert_true(sight == {"ok": False, "reason": "god mode disabled"}, sight)
        cancelled = engine.god_cancel("x")
        assert_true(cancelled == {"ok": False, "reason": "god mode disabled"}, cancelled)
        snap = engine.snapshot()
        assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is False, snap["config"]["flags"])
        assert_true("god" not in snap, "flag-off /state leaked a 'god' key")
        print("  OK flag-off: every god entry point inert, no /state 'god' key")
    finally:
        se.GOD_MODE_ENABLED = old


def test_flag_on_state_shape():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]
        expected = engine._default_god_state()
        assert_true(god == expected, god)
        snap = engine.snapshot()
        assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is True, snap["config"]["flags"])
        assert_true(snap["god"] == {
            "intervened": False, "providence": None,
            "activePublicEvents": [], "recentPublicInterventions": [],
        }, snap["god"])
        print("  OK flag-on: default godState shape + /state 'god' projection")
    finally:
        se.GOD_MODE_ENABLED = old


def test_preview_side_effect_free():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        before_god = dict(engine.civilization["godState"])
        before_activity = list(engine.activityLog)
        before_conv = list(engine.conversationLog)
        preview = engine.god_preview(_proclamation_envelope("A great harvest approaches."))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewId"] and preview["commandDigest"], preview)
        assert_true(preview["normalizedCommand"] == {
            "kind": "proclamation", "payload": {"text": "A great harvest approaches."},
        }, preview["normalizedCommand"])
        assert_true(preview["reversibilityClass"] == "irreversible", preview)
        assert_true(engine.civilization["godState"] == before_god, "preview mutated godState")
        assert_true(engine.activityLog == before_activity, "preview mutated activityLog")
        assert_true(engine.conversationLog == before_conv, "preview mutated conversationLog")
        print("  OK preview validates + normalizes with zero mutation")
    finally:
        se.GOD_MODE_ENABLED = old


def test_tampered_expired_missing_preview_rejected():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        missing = engine.god_apply(None, "req-missing")
        assert_true(missing == {"ok": False, "reason": "previewId is required"}, missing)

        unknown = engine.god_apply("does-not-exist", "req-unknown")
        assert_true(unknown == {"ok": False, "reason": "preview missing or expired"}, unknown)

        preview = engine.god_preview(_proclamation_envelope("The tide will turn."))
        pid = preview["previewId"]
        engine._god_preview_cache[pid]["expiresAt"] = time.time() - 1
        expired = engine.god_apply(pid, "req-expired")
        # A TTL-expired preview is evicted by the same bounded sweep that
        # runs at the top of every god_apply call, so it surfaces through
        # the same "missing" rejection an unknown previewId would -- both
        # mean "there is no live preview to apply".
        assert_true(expired == {"ok": False, "reason": "preview missing or expired"}, expired)
        assert_true(pid not in engine._god_preview_cache, "expired preview not evicted")

        preview2 = engine.god_preview(_proclamation_envelope("The tide will turn."))
        pid2 = preview2["previewId"]
        engine._god_preview_cache[pid2]["commandDigest"] = "0" * 64  # simulate a tampered record
        tampered = engine.god_apply(pid2, "req-tampered")
        assert_true(tampered == {"ok": False, "reason": "preview digest mismatch"}, tampered)
        assert_true(engine.civilization["godState"]["intervened"] is False,
                    "a rejected apply must never mutate world state")
        print("  OK missing/unknown/expired/tampered previews rejected, none applied")
    finally:
        se.GOD_MODE_ENABLED = old


def test_idempotent_apply_and_conflict():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        preview1 = engine.god_preview(_proclamation_envelope("Peace be with you."))
        pid1 = preview1["previewId"]
        resp1 = engine.god_apply(pid1, "req-idem")
        assert_true(resp1["ok"] and resp1["interventionId"] == "divine-1", resp1)
        # Proclamation also arms providence (Voice binding) -- two audit records.
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 2,
                    engine.civilization["godState"]["recentInterventions"])

        # Exact replay: same requestId + same previewId returns the ORIGINAL
        # response without re-applying (the preview was already consumed).
        resp1_replay = engine.god_apply(pid1, "req-idem")
        assert_true(resp1_replay == resp1, (resp1_replay, resp1))
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 2,
                    "replay re-applied instead of returning the stored response")
        assert_true(engine.civilization["godState"]["nextInterventionSeq"] == 3,
                    "replay incremented the intervention sequence")

        # Same requestId, a DIFFERENT preview -> conflict, apply nothing.
        preview2 = engine.god_preview(_proclamation_envelope("A second decree."))
        pid2 = preview2["previewId"]
        conflict = engine.god_apply(pid2, "req-idem")
        assert_true(conflict == {
            "ok": False, "reason": "requestId already used with a different preview",
        }, conflict)
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 2,
                    "conflicting requestId reuse mutated state")
        print("  OK idempotent replay returns the original response; "
              "different-preview reuse conflicts and applies nothing")
    finally:
        se.GOD_MODE_ENABLED = old


def test_text_normalizer():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        ok_text, err = engine._normalize_divine_text("A calm and steady rain.")
        assert_true(err is None and ok_text == "A calm and steady rain.", (ok_text, err))

        _, err = engine._normalize_divine_text("bad\x00text")
        assert_true(err and "NUL" in err, err)

        _, err = engine._normalize_divine_text("line one\nline two")
        assert_true(err and "newline" in err, err)

        _, err = engine._normalize_divine_text("bad\x01control")
        assert_true(err and "control" in err, err)

        _, err = engine._normalize_divine_text("A" * se.GOD_TEXT_MAX_CHARS)
        assert_true(err is None, f"exactly {se.GOD_TEXT_MAX_CHARS} chars should be accepted: {err}")
        _, err = engine._normalize_divine_text("A" * (se.GOD_TEXT_MAX_CHARS + 1))
        assert_true(err and "characters" in err, err)

        # 200 four-byte codepoints = 800 UTF-8 bytes but only 200 chars --
        # under the char cap (240) yet over the byte cap (600), so the byte
        # check must be load-bearing, not merely redundant with chars.
        wide = "\U0001D54F" * 200
        assert_true(len(wide) <= se.GOD_TEXT_MAX_CHARS, "test fixture must stay under the char cap")
        _, err = engine._normalize_divine_text(wide)
        assert_true(err and "bytes" in err, err)

        _, err = engine._normalize_divine_text(123)
        assert_true(err and "string" in err, err)

        _, err = engine._normalize_divine_text("   ")
        assert_true(err and "empty" in err, err)
        print("  OK text normalizer rejects NUL/control/newline, enforces char+byte caps")
    finally:
        se.GOD_MODE_ENABLED = old


def test_hostile_strings_round_trip():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        hostile_strings = [
            "<script>alert(1)</script>",
            '" onerror=x',
            "&amp;",
            "​Zero‮WidthRTL",
        ]
        for text in hostile_strings:
            engine = make_engine()
            preview = engine.god_preview(_proclamation_envelope(text))
            assert_true(preview["ok"], (text, preview))
            applied = engine.god_apply(preview["previewId"], f"req-{hash(text)}")
            assert_true(applied["ok"], (text, applied))
            stored_text = applied["outcome"]["text"]
            # Only NFC normalization + surrounding-whitespace strip happens
            # to plain text -- no HTML entity decoding/encoding, no escaping,
            # no truncation, no interpretation of any kind.
            import unicodedata
            expected = unicodedata.normalize("NFC", text).strip()
            assert_true(stored_text == expected, (text, stored_text, expected))
            assert_true(engine.conversationLog[0]["message"] == expected, engine.conversationLog[0])
            assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
            assert_true(expected in engine.activityLog[0], engine.activityLog[0])
            chronicle = engine.civilization["chronicle"][-1]
            assert_true(chronicle["text"] == expected and chronicle["source"] == "divine", chronicle)
        print(f"  OK {len(hostile_strings)} hostile strings round-trip as inert, unescaped plain text")
    finally:
        se.GOD_MODE_ENABLED = old


def test_godstate_roundtrip_save_restore():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_roundtrip.db")
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("A season of plenty."))
        applied = engine.god_apply(preview["previewId"], "req-roundtrip")
        assert_true(applied["ok"], applied)

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed against the just-written db")
        god = restored.civilization["godState"]
        assert_true(god["intervened"] is True, god)
        assert_true(god["nextInterventionSeq"] == 3, god)
        assert_true(len(god["recentInterventions"]) == 2
                    and any(r.get("text") == "A season of plenty."
                            for r in god["recentInterventions"]), god)
        # Restore also invalidates any outstanding preview/idempotency state.
        assert_true(restored._god_preview_cache == {}, restored._god_preview_cache)
        assert_true(restored._god_requests == {}, restored._god_requests)
        print("  OK godState round-trips save_state/restore_state; "
              "preview/idempotency caches invalidated on restore")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_old_save_without_godstate_restores_default():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_old_save.db")
    try:
        engine = make_engine()
        payload = engine._serialize_state()
        del payload["civilization"]["godState"]  # simulate a pre-Phase-2 save
        se._write_state_db(tmp_db, payload)

        restored = make_engine()
        se.DB_PATH = tmp_db
        assert_true(restored.restore_state(), "restore_state failed against a godState-less save")
        assert_true(restored.civilization["godState"] == restored._default_god_state(),
                    restored.civilization["godState"])
        print("  OK a pre-Phase-2 save (no godState key) restores with the fresh default")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_reset_clears_intervention_state():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_reset.db")
    try:
        se.DB_PATH = tmp_db
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("Before the reset."))
        engine.god_apply(preview["previewId"], "req-before-reset")
        assert_true(engine.civilization["godState"]["intervened"] is True, "expected intervened=True before reset")
        assert_true(engine._god_requests, "expected a populated idempotency store before reset")

        engine.reset()
        assert_true(engine.civilization["godState"] == engine._default_god_state(),
                    engine.civilization["godState"])
        assert_true(engine._god_preview_cache == {} and engine._god_requests == {},
                    "reset must clear the in-memory preview/idempotency caches too")
        print("  OK reset() clears godState, intervened marker, and in-memory caches")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_cancel_plumbing():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        result = engine.god_cancel("nothing-here")
        assert_true(result == {
            "ok": True, "cancelled": False, "reason": "nothing to cancel",
            "targetId": "nothing-here",
        }, result)
        print("  OK god_cancel plumbing returns a clean 'nothing to cancel' result")
    finally:
        se.GOD_MODE_ENABLED = old


def test_sight_bounded_projection():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        sight = engine.god_sight()
        assert_true(sight["ok"], sight)
        assert_true("agents" in sight and isinstance(sight["agents"], list), sight)
        dumped = json.dumps(sight)
        assert_true(TEST_TOKEN not in dumped, "sight leaked the process token")
        assert_true("embedding" not in dumped.lower(), "sight leaked memory-store embeddings")
        print("  OK god_sight returns a bounded projection with no embeddings/token")
    finally:
        se.GOD_MODE_ENABLED = old


def test_preview_and_request_cache_bounds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        for i in range(se.GOD_PREVIEW_CACHE_MAX + 10):
            engine.god_preview(_proclamation_envelope(f"Decree number {i}."))
        assert_true(len(engine._god_preview_cache) <= se.GOD_PREVIEW_CACHE_MAX,
                    len(engine._god_preview_cache))

        for i in range(se.GOD_REQUEST_CACHE_MAX + 10):
            preview = engine.god_preview(_proclamation_envelope(f"Bound test {i}."))
            engine.god_apply(preview["previewId"], f"req-bound-{i}")
        assert_true(len(engine._god_requests) <= se.GOD_REQUEST_CACHE_MAX,
                    len(engine._god_requests))
        print("  OK preview cache stays <= 32 and idempotency store stays <= 100 under load")
    finally:
        se.GOD_MODE_ENABLED = old


def test_unknown_and_future_kinds_rejected():
    """story_event was a placeholder "not implemented in this phase" kind
    through Phase 2-4; Phase 5 implements it for real (see the Phase 5
    section below), so this Phase 2 case now only covers a malformed
    story_event payload -- still rejected, but on its own merits (an empty
    title/narration), not the old catalog-stub reason."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        result = engine.god_preview({"kind": "story_event", "payload": {}})
        assert_true(result["ok"] is False and "title" in result["reason"], result)
        unknown = engine.god_preview({"kind": "smite", "payload": {}})
        assert_true(unknown == {"ok": False, "reason": "unknown kind 'smite'"}, unknown)
        malformed = engine.god_preview("not-a-dict")
        assert_true(malformed == {"ok": False, "reason": "envelope must be an object"}, malformed)
        print("  OK unknown/malformed envelopes rejected cleanly; story_event validates its own payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_expire_divine_effects_noop_and_tick():
    """Phase 2 has no timed effects yet, so _expire_divine_effects is a
    provably cheap no-op, and _tick_once must call it without raising."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        before = dict(engine.civilization["godState"])
        engine._expire_divine_effects()
        assert_true(engine.civilization["godState"] == before, "no-op expiry mutated godState")
        engine._tick_once()  # must not raise with GOD_MODE_ENABLED True and no active events
        print("  OK _expire_divine_effects is a safe no-op; wired into _tick_once")
    finally:
        se.GOD_MODE_ENABLED = old


def test_benchmarks_expose_intervened():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("For the benchmark record."))
        engine.god_apply(preview["previewId"], "req-benchmark")
        engine._sample_benchmarks()
        assert_true(engine.lastBenchmarks.get("intervened") is True, engine.lastBenchmarks)
        print("  OK benchmarks expose intervened=True after an applied proclamation")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 3 tests: voice and providence (docs/archive/plan-sovereign-god-mode-v2.md
# "Voice and providence" + "Bounded cognition impact" + Phase 3 deliverables) ---
