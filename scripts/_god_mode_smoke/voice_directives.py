# Voice/providence directive plumbing, prompt-window rendering, and Voice synthesis/skip-cap behavior.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_directive_and_providence_stay_separate():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        elder = next((a for a in engine.agents if a["role"] == "elder"), engine.agents[0])
        engine.civilization["directive"] = "Elder Test directs: build the granary; gather wood"
        engine.civilization["directiveFrame"] = engine.frameTick

        preview = engine.god_preview(_providence_envelope("A great harvest is coming.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-directive-1")
        assert_true(applied["ok"], applied)

        payload = engine._build_think_payload(elder)
        assert_true(payload["directive"] == "Elder Test directs: build the granary; gather wood",
                    payload["directive"])
        assert_true(payload["divine_public_line"] == "A great harvest is coming.",
                    payload["divine_public_line"])
        assert_true(payload["divine_private_line"] is None, payload["divine_private_line"])
        print("  OK elder civilization['directive'] and divine providence stay independent, "
              "separately-labeled think-payload fields")
    finally:
        se.GOD_MODE_ENABLED = old


def test_prompt_lines_frame_window():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        preview = engine.god_preview(_providence_envelope("Frame window test.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-window-1")
        assert_true(applied["ok"], applied)
        prov = engine.civilization["godState"]["providence"]
        created_frame, expires_frame = prov["createdFrame"], prov["expiresFrame"]

        engine.frameTick = created_frame
        pub, priv = engine._divine_prompt_lines(agent)
        assert_true(pub == "Frame window test." and priv is None, (pub, priv))

        engine.frameTick = expires_frame - 1
        pub, _ = engine._divine_prompt_lines(agent)
        assert_true(pub == "Frame window test.", "must still be active one frame before expiresFrame")

        engine.frameTick = expires_frame
        pub, _ = engine._divine_prompt_lines(agent)
        assert_true(pub is None, "must vanish exactly at expiresFrame")

        # Flag-off: inert regardless of any stored guidance.
        se.GOD_MODE_ENABLED = False
        engine.frameTick = created_frame
        pub_off, priv_off = engine._divine_prompt_lines(agent)
        assert_true(pub_off is None and priv_off is None, (pub_off, priv_off))
        print("  OK divine prompt line active for [createdFrame, expiresFrame) only, "
              "gone exactly at expiresFrame, inert with the flag off")
    finally:
        se.GOD_MODE_ENABLED = old


def test_prompt_size_cap_and_divine_lines_render():
    """Prompt-size assertion (task requirement): the two divine lines add a
    small, precisely bounded amount of text even at maximum omen length, and
    render nothing at all when unset -- proving flag-off/no-guidance prompts
    stay byte-identical to before this phase."""
    import server as _server  # noqa: E402 (module-cached; safe to import here)
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        base_payload = engine._build_think_payload(agent)

        max_text = "A" * se.GOD_TEXT_MAX_CHARS
        payload_with = dict(base_payload)
        payload_with["divine_public_line"] = max_text
        payload_with["divine_private_line"] = max_text
        payload_without = dict(base_payload)
        payload_without["divine_public_line"] = None
        payload_without["divine_private_line"] = None

        prompt_with = _server.build_user_prompt(payload_with)
        prompt_without = _server.build_user_prompt(payload_without)
        public_line = _server._format_voice_guidance_line("public", max_text)
        private_line = _server._format_voice_guidance_line("private", max_text)
        assert_true(public_line in prompt_with, "public binding guidance line missing or mangled")
        assert_true(private_line in prompt_with, "private binding guidance line missing or mangled")
        assert_true("may interpret or ignore" not in prompt_with,
                    "Voice binding lines must not use soft omen wording")
        assert_true("Divine guidance (binding):" not in prompt_without
                    and "Private guidance (binding):" not in prompt_without,
                    "a divine line rendered when unset -- flag-off/no-guidance prompts must be byte-identical")

        added = len(prompt_with) - len(prompt_without)
        # _format_voice_guidance_line prefixes/suffixes + two max-length texts.
        expected_max = len(public_line) + len(private_line)
        assert_true(added <= expected_max, (added, expected_max))
        print(f"  OK divine prompt lines add <= {expected_max} chars at max ({se.GOD_TEXT_MAX_CHARS}-char) "
              f"omen length; absent entirely when unset")
    finally:
        se.GOD_MODE_ENABLED = old


def test_voice_binding_wording_and_guidance_active():
    """Private omen arms binding Voice guidance in think payload and prompt."""
    import server as srv  # noqa: E402
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        secret = "Seek the hidden spring."
        preview = engine.god_preview(
            _omen_envelope(agent["id"], secret, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-voice-binding-1")
        assert_true(applied["ok"], applied)
        omen_id = applied["outcome"]["interventionId"]

        with engine.lock:
            payload = engine._build_think_payload(agent)
        assert_true(payload.get("voice_guidance_active") is True, payload)
        assert_true(payload.get("divine_private_line") == secret, payload)
        assert_true(payload.get("voice_guidance_id") == omen_id, payload)

        prompt = srv.build_user_prompt(payload)
        assert_true("Private guidance (binding):" in prompt, prompt)
        assert_true(secret in prompt, prompt)
        assert_true("may interpret or ignore" not in prompt, prompt)
        print("  OK private omen sets voice_guidance_active + binding prompt wording")
    finally:
        se.GOD_MODE_ENABLED = old


def test_synthesize_divine_response_missing_and_valid():
    """synthesize_divine_response fills missing divine_response on Voice turns."""
    from server import synthesize_divine_response  # noqa: E402

    agent_data = {"voice_guidance_active": True}
    missing = synthesize_divine_response({"action": "rest", "reasoning": "smoke"}, agent_data)
    assert_true(missing.get("divine_response") == {
        "stance": "continue",
        "reason": "missing_divine_response",
    }, missing)
    assert_true(missing.get("divine_response_synthetic") is True, missing)

    valid = synthesize_divine_response({
        "action": "rest",
        "reasoning": "smoke",
        "divine_response": {"stance": "follow", "reason": "The sign is clear."},
    }, agent_data)
    assert_true(valid.get("divine_response") == {
        "stance": "follow",
        "reason": "The sign is clear.",
    }, valid)
    assert_true("divine_response_synthetic" not in valid, valid)

    inactive = synthesize_divine_response({"action": "rest"}, {"voice_guidance_active": False})
    assert_true("divine_response" not in inactive, inactive)
    print("  OK synthesize_divine_response: missing -> continue; valid preserved")


def test_voice_follow_clears_goal_continue_keeps():
    """follow stance clears goal/assignedTask; continue leaves them intact."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["goal"] = "Build a granary"
        agent["assignedTask"] = "gather wood"
        preview = engine.god_preview(
            _omen_envelope(agent["id"], "Abandon your task.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine.god_apply(preview["previewId"], "req-voice-follow-1")

        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "rest",
                "reasoning": "I heed the sign.",
                "divine_response": {"stance": "follow", "reason": "The omen commands it."},
            })
        assert_true(agent.get("goal") is None, agent)
        assert_true(agent.get("assignedTask") is None, agent)
        responses = engine.civilization["godState"].get("recentDivineResponses") or []
        assert_true(any(r.get("stance") == "follow" for r in responses), responses)

        engine2 = make_engine()
        agent2 = engine2.agents[0]
        agent2["goal"] = "Keep building"
        agent2["assignedTask"] = "carry stone"
        preview2 = engine2.god_preview(
            _omen_envelope(agent2["id"], "Stay your course.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine2.god_apply(preview2["previewId"], "req-voice-continue-1")
        with engine2.lock:
            engine2._apply_gated_decision(agent2, {
                "action": "rest",
                "reasoning": "I carry on.",
                "divine_response": {"stance": "continue", "reason": "My path remains."},
            })
        assert_true(agent2.get("goal") == "Keep building", agent2)
        assert_true(agent2.get("assignedTask") == "carry stone", agent2)
        print("  OK follow clears goal/assignedTask; continue preserves them")
    finally:
        se.GOD_MODE_ENABLED = old


def test_voice_omen_cancels_special_turns_not_defer():
    """Private omen cancels invention/sprite special turns immediately."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["inventionTurn"] = True
        agent["spriteDesignTurn"] = {"role": agent["role"], "frame": engine.frameTick}

        preview = engine.god_preview(
            _omen_envelope(agent["id"], "Drop your craft.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-voice-cancel-special")
        assert_true(applied["ok"], applied)
        assert_true(agent.get("inventionTurn") is False, agent)
        assert_true(agent.get("spriteDesignTurn") is None, agent)

        with engine.lock:
            payload = engine._build_think_payload(agent)
        assert_true(payload.get("voice_guidance_active") is True, payload)
        assert_true(payload.get("divine_private_line") == "Drop your craft.", payload)
        assert_true(payload.get("invention_only") is False, payload)
        assert_true(payload.get("sprite_design_only") is False, payload)
        print("  OK private omen cancels special turns; think payload is Voice-only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_voice_skip_cap_forces_close_after_repeated_synthetic():
    """A synthetic (missing divine_response) turn no longer acks immediately
    -- it increments a per-guidance skip counter and only force-acks once
    that counter reaches GOD_VOICE_ACK_SKIP_CAP consecutive synthetic turns."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        preview = engine.god_preview(
            _omen_envelope(agent["id"], "Answer the sign.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine.god_apply(preview["previewId"], "req-voice-skip-cap-1")

        with engine.lock:
            for i in range(se.GOD_VOICE_ACK_SKIP_CAP - 1):
                engine._apply_gated_decision(agent, {"action": "rest", "reasoning": f"turn {i}"})
                voice = engine._active_voice_guidance(agent)
                assert_true(voice["voice_guidance_active"] is True, (i, voice))
                omen = engine.civilization["godState"]["privateOmens"].get(str(agent["id"]))
                assert_true(omen.get("acked") is not True, (i, omen))
                assert_true(omen.get("skipCount") == i + 1, (i, omen))

            # Final turn reaches the cap and force-acks (closes) the guidance.
            engine._apply_gated_decision(agent, {"action": "rest", "reasoning": "final synthetic turn"})
            voice = engine._active_voice_guidance(agent)
            assert_true(voice["voice_guidance_active"] is False, voice)
            omen = engine.civilization["godState"]["privateOmens"].get(str(agent["id"]))
            assert_true(omen.get("acked") is True, omen)
            assert_true(omen.get("skipCount") == se.GOD_VOICE_ACK_SKIP_CAP, omen)

        responses = engine.civilization["godState"].get("recentDivineResponses") or []
        last = responses[0]
        assert_true(last.get("synthetic") is True, last)
        assert_true(last.get("capped") is True, last)
        assert_true(last.get("skipCount") == se.GOD_VOICE_ACK_SKIP_CAP, last)
        print("  OK synthetic divine_response skip counter caps close at GOD_VOICE_ACK_SKIP_CAP")
    finally:
        se.GOD_MODE_ENABLED = old


def test_proclamation_sets_providence_and_cancels_special_turns():
    """Proclamation broadcasts publicly and arms providence for binding Voice."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        for agent in engine.agents:
            if agent.get("deathFrame") is None:
                agent["inventionTurn"] = True
                agent["spriteDesignTurn"] = {"role": agent["role"]}

        text = "A season of trial begins."
        preview = engine.god_preview(_proclamation_envelope(text))
        applied = engine.god_apply(preview["previewId"], "req-proc-prov-1")
        assert_true(applied["ok"], applied)

        god = engine.civilization["godState"]
        prov = god.get("providence")
        assert_true(isinstance(prov, dict) and prov.get("text") == text, prov)
        assert_true(isinstance(prov.get("expiresFrame"), int), prov)

        for agent in engine.agents:
            if agent.get("deathFrame") is None:
                assert_true(agent.get("inventionTurn") is False, agent)
                assert_true(agent.get("spriteDesignTurn") is None, agent)

        assert_true(any(text in line for line in engine.activityLog), engine.activityLog[:3])
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(any(entry.get("text") == text for entry in chronicle), chronicle[-3:])
        print("  OK proclamation -> providence + public chronicle; special turns cancelled")
    finally:
        se.GOD_MODE_ENABLED = old


def test_presentation_soft_thunder_validates_and_applies():
    """Optional presentation is cosmetic-only: stored on records, not cognition."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        bad = engine.god_preview({
            "kind": "proclamation",
            "payload": {"text": "Hear me.", "presentation": "lightning"},
        })
        assert_true(not bad.get("ok"), bad)
        assert_true("presentation" in (bad.get("reason") or ""), bad)

        text = "The sky shall answer."
        preview = engine.god_preview({
            "kind": "proclamation",
            "payload": {"text": text, "presentation": "thunder"},
        })
        assert_true(preview.get("ok"), preview)
        norm = preview.get("normalizedCommand") or {}
        assert_true(norm.get("payload", {}).get("presentation") == "thunder", norm)

        applied = engine.god_apply(preview["previewId"], "req-pres-thunder-1")
        assert_true(applied.get("ok"), applied)

        god = engine.civilization["godState"]
        prov = god.get("providence") or {}
        assert_true(prov.get("presentation") == "thunder", prov)

        recent = god.get("recentInterventions") or []
        proc = next((r for r in reversed(recent) if r.get("kind") == "proclamation"), None)
        assert_true(proc and proc.get("presentation") == "thunder", proc)

        chronicle = engine.civilization.get("chronicle") or []
        assert_true(any(
            e.get("text") == text and e.get("presentation") == "thunder"
            for e in chronicle
        ), chronicle[-3:])

        soft_preview = engine.god_preview(_providence_envelope("A gentle word."))
        assert_true(soft_preview.get("ok"), soft_preview)
        soft_norm = soft_preview.get("normalizedCommand") or {}
        assert_true("presentation" not in (soft_norm.get("payload") or {}), soft_norm)

        soft_applied = engine.god_apply(soft_preview["previewId"], "req-pres-soft-1")
        assert_true(soft_applied.get("ok"), soft_applied)
        assert_true(god.get("providence", {}).get("presentation") is None, god.get("providence"))

        print("  OK presentation thunder validates, stores on banner/chronicle records; soft omits field")
    finally:
        se.GOD_MODE_ENABLED = old


def test_sight_recent_divine_responses_and_snapshot_privacy():
    """Sight exposes adherence log; /state god allowlist stays private."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        secret = "Only you hear this."
        preview = engine.god_preview(
            _omen_envelope(agent["id"], secret, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine.god_apply(preview["previewId"], "req-sight-voice-1")

        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "rest",
                "reasoning": "I obey.",
                "divine_response": {"stance": "follow", "reason": "The omen is binding."},
            })

        sight = engine.god_sight()
        assert_true(sight.get("ok"), sight)
        responses = sight.get("recentDivineResponses") or []
        assert_true(any(
            r.get("stance") == "follow"
            and r.get("reason") == "The omen is binding."
            and r.get("action") == "rest"
            for r in responses
        ), responses)

        snap = engine.snapshot()
        snap_dump = json.dumps(snap)
        assert_true(secret not in snap_dump, "private omen text leaked into /state")
        assert_true("recentDivineResponses" not in snap_dump,
                    "recentDivineResponses leaked into /state god allowlist")
        god_block = snap.get("god") or {}
        assert_true("recentDivineResponses" not in god_block, god_block)

        assert_true(not any(secret in line for line in engine.activityLog),
                    "private omen leaked into public activity log")
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(not any(secret in (entry.get("text") or "") for entry in chronicle),
                    "private omen leaked into Chronicle")
        print("  OK Sight shows recentDivineResponses; /state omits private Voice data")
    finally:
        se.GOD_MODE_ENABLED = old


def test_restore_does_not_refire_omen_memory():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_omen_restore.db")
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]
        preview = engine.god_preview(
            _omen_envelope(target_id, "A quiet warning.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-restore-omen")
        assert_true(applied["ok"], applied)
        expires_frame = engine.civilization["godState"]["privateOmens"][str(target_id)]["expiresFrame"]
        # Advance frameTick PAST expiry WITHOUT running the tick-based sweep
        # -- simulates a save captured right as the omen expired but before
        # _expire_divine_effects next ran.
        engine.frameTick = expires_frame

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        target = restored._find_agent_by_id(target_id)

        def memory_hits(text):
            return sum(text in line for line in target["memory"]["working"])

        assert_true(memory_hits("A quiet warning.") == 1,
                    "restore-time expiry must write the not-yet-written omen memory exactly once")
        assert_true(str(target_id) not in restored.civilization["godState"]["privateOmens"],
                    "restore-time expiry must close the expired omen")

        # A second restore-time expiry sweep against the already-closed
        # state must NOT re-fire the memory write.
        restored._expire_divine_effects(restore=True)
        assert_true(memory_hits("A quiet warning.") == 1,
                    "a second restore-time expiry sweep re-fired an already-written omen memory")
        print("  OK restore-time expiry writes an unwritten omen memory exactly once, never twice")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


# --- Phase 4 tests: bounded immediate miracles (docs/plan-sovereign-god-
# mode-v2.md "Immediate miracles" + "Honest reversibility" + Phase 4) ---
