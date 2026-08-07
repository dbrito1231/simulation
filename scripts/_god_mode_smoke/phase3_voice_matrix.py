# Sovereign God mode Phase 3 (providence/omen/whisper/sampling) plus Divine Matrix Phase 3/4 (memory surgery, context masks).
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_providence_set_replace_revoke_expire():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]

        preview = engine.god_preview(_providence_envelope("A season of trial approaches.", duration=1000))
        assert_true(preview["ok"], preview)
        assert_true(preview["fingerprint"] == {"outgoingId": None}, preview["fingerprint"])
        assert_true(preview["reversibilityClass"] == "cancellable", preview)
        applied = engine.god_apply(preview["previewId"], "req-prov-1")
        assert_true(applied["ok"], applied)
        prov_id = applied["outcome"]["interventionId"]
        assert_true(god["providence"]["id"] == prov_id, god["providence"])
        assert_true(god["providence"]["expiresFrame"] == engine.frameTick + 1000, god["providence"])
        # Public per docs/plan Visibility: same treatment as proclamation.
        assert_true('"A season of trial approaches."' in engine.activityLog[0], engine.activityLog[0])
        assert_true(engine.conversationLog[0]["message"] == "A season of trial approaches."
                    and engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
        assert_true(engine.civilization["chronicle"][-1]["text"] == "A season of trial approaches."
                    and engine.civilization["chronicle"][-1]["source"] == "divine",
                    engine.civilization["chronicle"][-1])

        # Replace: preview discloses the outgoing id; apply reports it too.
        preview2 = engine.god_preview(_providence_envelope("A season of plenty follows.", duration=2000))
        assert_true(preview2["fingerprint"] == {"outgoingId": prov_id}, preview2["fingerprint"])
        applied2 = engine.god_apply(preview2["previewId"], "req-prov-2")
        assert_true(applied2["ok"], applied2)
        assert_true(applied2["outcome"]["outgoingId"] == prov_id, applied2["outcome"])
        prov_id2 = applied2["outcome"]["interventionId"]
        assert_true(god["providence"]["id"] == prov_id2, god["providence"])

        # A stale preview (recorded outgoingId no longer current) is rejected.
        stale = engine.god_preview(_providence_envelope("Stale replacement.", duration=1000))
        # Simulate the providence having changed again between preview and apply.
        engine.civilization["godState"]["providence"]["id"] = "divine-tampered"
        stale_apply = engine.god_apply(stale["previewId"], "req-prov-stale")
        assert_true(stale_apply == {
            "ok": False, "reason": "providence changed since preview -- re-preview to see the current guidance",
        }, stale_apply)
        engine.civilization["godState"]["providence"]["id"] = prov_id2  # restore for the rest of the test

        # Revoke.
        preview3 = engine.god_preview(_revoke_envelope(prov_id2))
        assert_true(preview3["fingerprint"] == {"targetKind": "providence", "existed": True}, preview3["fingerprint"])
        applied3 = engine.god_apply(preview3["previewId"], "req-prov-revoke")
        assert_true(applied3["ok"], applied3)
        assert_true(god["providence"] is None, god["providence"])
        # Revoking twice (id already gone) is rejected, not re-applied.
        replay_revoke = engine.god_preview(_revoke_envelope(prov_id2))
        assert_true(replay_revoke["fingerprint"] == {"targetKind": None, "existed": False}, replay_revoke)

        # Expire: predicate is startFrame <= frameTick < expiresFrame -- must
        # still be active one frame before expiresFrame and gone exactly at it.
        preview4 = engine.god_preview(
            _providence_envelope("A brief omen.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied4 = engine.god_apply(preview4["previewId"], "req-prov-4")
        assert_true(applied4["ok"], applied4)
        expires_frame = god["providence"]["expiresFrame"]
        engine.frameTick = expires_frame - 1
        engine._expire_divine_effects()
        assert_true(god["providence"] is not None, "providence closed one frame too early")
        engine.frameTick = expires_frame
        engine._expire_divine_effects()
        assert_true(god["providence"] is None, "providence not closed exactly at expiresFrame")
        print("  OK providence set/replace/revoke/expire; disclose-then-replace enforced at apply time")
    finally:
        se.GOD_MODE_ENABLED = old


def test_omen_lifecycle_and_memory_contract():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]
        target = engine.agents[0]
        target_id = target["id"]
        key = str(target_id)

        def memory_hits(text):
            return sum(text in line for line in target["memory"]["working"])

        # Unknown / dead / bad-type targets rejected.
        unknown = engine.god_preview(_omen_envelope(999999, "Nobody home.", duration=1000))
        assert_true(unknown == {"ok": False, "reason": "unknown target agent"}, unknown)
        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_omen_envelope(target_id, "Too late.", duration=1000))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None
        bad_type = engine.god_preview(_omen_envelope(str(target_id), "Wrong type.", duration=1000))
        assert_true(bad_type == {"ok": False, "reason": "targetId must be an integer agent id"}, bad_type)

        # Set.
        preview = engine.god_preview(_omen_envelope(target_id, "Seek reconciliation with a rival.", duration=1000))
        assert_true(preview["ok"], preview)
        assert_true(preview["fingerprint"] == {"outgoingId": None}, preview["fingerprint"])
        applied = engine.god_apply(preview["previewId"], "req-omen-1")
        assert_true(applied["ok"], applied)
        omen_id = applied["outcome"]["interventionId"]
        assert_true(key in god["privateOmens"], god["privateOmens"])
        assert_true(god["privateOmens"][key]["memoryWritten"] is False, god["privateOmens"][key])
        # While ACTIVE: must NOT be in ordinary memory (else it influences
        # cognition twice -- once via the prompt line, once via memory).
        assert_true(memory_hits("Seek reconciliation") == 0,
                    "omen text leaked into memory while still active")

        # Replace: outgoing disclosed at preview, closed (memory written)
        # exactly once at apply.
        preview2 = engine.god_preview(_omen_envelope(target_id, "A different path opens.", duration=2000))
        assert_true(preview2["fingerprint"] == {"outgoingId": omen_id}, preview2["fingerprint"])
        applied2 = engine.god_apply(preview2["previewId"], "req-omen-2")
        assert_true(applied2["ok"], applied2)
        assert_true(applied2["outcome"]["outgoingId"] == omen_id, applied2["outcome"])
        assert_true(memory_hits("Seek reconciliation") == 1,
                    "replaced omen must enter memory exactly once")
        omen_id2 = applied2["outcome"]["interventionId"]

        # Revoke.
        preview3 = engine.god_preview(_revoke_envelope(omen_id2))
        assert_true(preview3["fingerprint"] == {"targetKind": "private_omen", "existed": True}, preview3["fingerprint"])
        applied3 = engine.god_apply(preview3["previewId"], "req-omen-revoke")
        assert_true(applied3["ok"], applied3)
        assert_true(key not in god["privateOmens"], god["privateOmens"])
        assert_true(memory_hits("A different path opens.") == 1,
                    "revoked omen must enter memory exactly once")

        # Expire.
        preview4 = engine.god_preview(
            _omen_envelope(target_id, "A third sign.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied4 = engine.god_apply(preview4["previewId"], "req-omen-4")
        assert_true(applied4["ok"], applied4)
        expires_frame = god["privateOmens"][key]["expiresFrame"]
        engine.frameTick = expires_frame - 1
        engine._expire_divine_effects()
        assert_true(key in god["privateOmens"], "omen closed one frame too early")
        assert_true(memory_hits("A third sign.") == 0, "expiring omen wrote memory before its expiresFrame")
        engine.frameTick = expires_frame
        engine._expire_divine_effects()
        assert_true(key not in god["privateOmens"], "expired omen not removed")
        assert_true(memory_hits("A third sign.") == 1, "expired omen must enter memory exactly once")

        # A second expiry sweep against an already-closed key must never
        # double-fire (the key is gone, so this is a no-op by construction).
        engine._expire_divine_effects()
        assert_true(memory_hits("A third sign.") == 1, "a second expiry sweep re-fired the omen memory")
        print("  OK private omen set/replace/revoke/expire; memory write exactly once, "
              "never while active, never twice")
    finally:
        se.GOD_MODE_ENABLED = old


def test_omen_public_visibility_boundary():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]
        preview = engine.god_preview(_omen_envelope(target_id, "A secret trial awaits you.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-vis-1")
        assert_true(applied["ok"], applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("secret trial" not in dumped, "private omen text leaked into /state")
        assert_true(all(r.get("kind") != "private_omen" for r in snap["god"]["recentPublicInterventions"]),
                    "a private_omen intervention leaked into /state's recentPublicInterventions")
        assert_true(snap["god"]["providence"] is None, snap["god"])

        assert_true(not any("secret trial" in line for line in engine.activityLog),
                    "private omen leaked into public activity log")
        assert_true(not any("secret trial" in (c.get("message") or "") for c in engine.conversationLog),
                    "private omen leaked into public communication log")
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(not any("secret trial" in (entry.get("text") or "") for entry in chronicle),
                    "private omen leaked into Chronicle")

        sight = engine.god_sight()
        assert_true(sight["ok"], sight)
        sight_agent = next(a for a in sight["agents"] if a["id"] == target_id)
        assert_true(sight_agent["omen"] and sight_agent["omen"]["active"], sight_agent)
        sight_dump = json.dumps(sight)
        assert_true("secret trial" in sight_dump,
                    "sight should expose the intervention outcome text (authenticated-only view)")
        print("  OK private omen absent from /state/activity/communication/Chronicle; "
              "visible only via authenticated sight")
    finally:
        se.GOD_MODE_ENABLED = old


def test_whisper_campaign_batch_apply_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        text0, text1 = "Whisper alpha path.", "Whisper beta path."
        theme = "Secret campaign theme"
        preview = engine.god_preview(_whisper_campaign_envelope(
            theme,
            [{"targetId": a0["id"], "text": text0}, {"targetId": a1["id"], "text": text1}],
            duration=1000,
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-whisper-1")
        assert_true(applied.get("ok"), applied)
        campaign_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(campaign_id in god["whisperCampaigns"], god["whisperCampaigns"])
        assert_true(god["whisperCampaigns"][campaign_id]["theme"] == theme, god)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(theme not in dumped, "whisper campaign theme leaked into /state")
        assert_true(text0 not in dumped and text1 not in dumped,
                    "whisper omen text leaked into /state")
        assert_true("whisperCampaigns" not in dumped,
                    "whisperCampaigns map leaked into /state god snapshot")
        assert_true(all(r.get("kind") != "whisper_campaign"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "whisper_campaign leaked into recentPublicInterventions")

        _, priv0 = engine._divine_prompt_lines(a0)
        _, priv1 = engine._divine_prompt_lines(a1)
        assert_true(priv0 == text0 and priv1 == text1, (priv0, priv1))
        assert_true(priv0 != priv1, "each target must see a distinct private whisper line")

        dup_preview = engine.god_preview(_whisper_campaign_envelope(
            theme,
            [{"targetId": a0["id"], "text": "x"}, {"targetId": a0["id"], "text": "y"}],
            duration=1000,
        ))
        assert_true(not dup_preview.get("ok"), dup_preview)
        print("  OK whisper_campaign batch apply; theme/text/campaigns absent from /state; "
              "per-agent private lines differ")
    finally:
        se.GOD_MODE_ENABLED = old


def test_whisper_campaign_cancel_clears_linked_omens():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview = engine.god_preview(_whisper_campaign_envelope(
            "Cancel test theme",
            [{"targetId": a0["id"], "text": "Cancel whisper A."},
             {"targetId": a1["id"], "text": "Cancel whisper B."}],
            duration=5000,
        ))
        applied = engine.god_apply(preview["previewId"], "req-whisper-cancel")
        assert_true(applied.get("ok"), applied)
        campaign_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(str(a0["id"]) in god["privateOmens"], god["privateOmens"])
        assert_true(str(a1["id"]) in god["privateOmens"], god["privateOmens"])

        cancelled = engine.god_cancel(campaign_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "whisper_campaign", cancelled)
        assert_true(campaign_id not in god.get("whisperCampaigns", {}), god)
        assert_true(str(a0["id"]) not in god["privateOmens"], god["privateOmens"])
        assert_true(str(a1["id"]) not in god["privateOmens"], god["privateOmens"])

        _, priv0 = engine._divine_prompt_lines(a0)
        _, priv1 = engine._divine_prompt_lines(a1)
        assert_true(priv0 is None and priv1 is None, (priv0, priv1))
        print("  OK god_cancel on whisper_campaign id revokes all linked omens and removes campaign")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_payload_overlay_and_fast_model():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 1, "need a living agent")
        agent = agents[0]
        preview = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 0.95, top_p=0.55, top_k=15, min_p=0.1))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-sampling-overlay")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            think = engine._build_think_payload(agent)
        assert_true(think.get("divine_sampling") == {
            "model": "sim-smart", "temperature": 0.95,
            "top_p": 0.55, "top_k": 15, "min_p": 0.1,
        }, think.get("divine_sampling"))

        payload = srv.build_decision_payload(think, "", srv.build_response_format())
        assert_true(payload.get("model") == srv.MODEL_SMART, payload)
        assert_true(payload.get("temperature") == 0.95, payload)
        assert_true(payload.get("top_p") == 0.55, payload)
        assert_true(payload.get("top_k") == 15, payload)
        assert_true(payload.get("min_p") == 0.1, payload)
        assert_true(srv.model_for_decision(think) == srv.MODEL_SMART, think)

        # Replace with sim-fast on the same agent (allowed — cap excludes self).
        preview_fast = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 1.2, model="sim-fast"))
        assert_true(preview_fast.get("ok"), preview_fast)
        applied_fast = engine.god_apply(preview_fast["previewId"], "req-sampling-fast")
        assert_true(applied_fast.get("ok"), applied_fast)
        with engine.lock:
            think_fast = engine._build_think_payload(agent)
        payload_fast = srv.build_decision_payload(think_fast, "", srv.build_response_format())
        assert_true(payload_fast.get("model") == srv.MODEL_FAST, payload_fast)
        assert_true(srv.model_for_decision(think_fast) == srv.MODEL_FAST, think_fast)
        print("  OK agent_sampling overlays build_decision_payload (smart + fast)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_expiry_revoke_and_cancel_restore_defaults():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        agent = agents[0]
        preview = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 0.88, durationFrames=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-sampling-expire")
        assert_true(applied.get("ok"), applied)
        intervention_id = applied["outcome"]["interventionId"]

        with engine.lock:
            think = engine._build_think_payload(agent)
        assert_true(think.get("divine_sampling") is not None, think)

        with engine.lock:
            engine.frameTick += se.GOD_GUIDANCE_MIN_DURATION_FRAMES
            engine._expire_divine_effects()
            think_after = engine._build_think_payload(agent)
        assert_true(think_after.get("divine_sampling") is None, think_after)
        payload_after = srv.build_decision_payload(think_after, "", srv.build_response_format())
        assert_true(payload_after.get("temperature") == 0.4, payload_after)
        assert_true(payload_after.get("model") == srv.MODEL_SMART, payload_after)

        preview2 = engine.god_preview(_agent_sampling_envelope(agent["id"], 0.77))
        applied2 = engine.god_apply(preview2["previewId"], "req-sampling-revoke")
        assert_true(applied2.get("ok"), applied2)
        with engine.lock:
            think_live = engine._build_think_payload(agent)
        assert_true(think_live.get("divine_sampling") is not None, think_live)

        revoke_preview = engine.god_preview(_revoke_agent_sampling_envelope(agent["id"]))
        assert_true(revoke_preview.get("ok"), revoke_preview)
        revoked = engine.god_apply(revoke_preview["previewId"], "req-sampling-revoked")
        assert_true(revoked.get("ok"), revoked)
        with engine.lock:
            think_revoked = engine._build_think_payload(agent)
        assert_true(think_revoked.get("divine_sampling") is None, think_revoked)

        preview3 = engine.god_preview(_agent_sampling_envelope(agent["id"], 0.66))
        applied3 = engine.god_apply(preview3["previewId"], "req-sampling-cancel")
        assert_true(applied3.get("ok"), applied3)
        cancel_id = applied3["outcome"]["interventionId"]
        cancelled = engine.god_cancel(cancel_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "agent_sampling", cancelled)
        with engine.lock:
            think_cancelled = engine._build_think_payload(agent)
        assert_true(think_cancelled.get("divine_sampling") is None, think_cancelled)
        print("  OK agent_sampling expiry/revoke/cancel restore default decision payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_fast_route_cap_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview0 = engine.god_preview(_agent_sampling_envelope(a0["id"], 0.5, model="sim-fast"))
        assert_true(preview0.get("ok"), preview0)
        applied0 = engine.god_apply(preview0["previewId"], "req-sampling-cap-0")
        assert_true(applied0.get("ok"), applied0)

        preview1 = engine.god_preview(_agent_sampling_envelope(a1["id"], 0.5, model="sim-fast"))
        assert_true(not preview1.get("ok"), preview1)
        assert_true("sim-fast" in (preview1.get("reason") or ""), preview1)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("agentSampling" not in dumped, "agentSampling map leaked into /state")
        assert_true(all(r.get("kind") != "agent_sampling"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "agent_sampling leaked into recentPublicInterventions")
        print("  OK second sim-fast decision override rejected; agentSampling absent from /state")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_insert_query_and_delete_by_keyword():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, store = make_engine_with_memory()
        target = engine.agents[0]
        target_id = target["id"]
        secret = "SMOKE_FALSE_MEMORY_ALPHA_42"

        preview = engine.god_preview(_memory_insert_envelope(target_id, secret, salience=0.85))
        assert_true(preview.get("ok"), preview)
        assert_true(preview.get("previewOutcome", {}).get("kind") == se.GOD_MEMORY_DEFAULT_KIND,
                    preview.get("previewOutcome"))
        applied = engine.god_apply(preview["previewId"], "req-mem-insert-1")
        assert_true(applied.get("ok"), applied)
        assert_true(secret not in json.dumps(applied.get("outcome") or {}),
                    "apply outcome must not echo memory text")

        hits = store.query(agent=target["name"], text=secret, top_k=3)
        assert_true(any(secret in (e.get("text") or "") for e in hits), hits)
        assert_true(any(secret in line for line in target["memory"]["working"]), target["memory"])

        preview_del = engine.god_preview(_memory_delete_envelope(target_id, keyword="ALPHA_42"))
        assert_true(preview_del.get("ok"), preview_del)
        assert_true(preview_del["previewOutcome"]["wouldDelete"] >= 1, preview_del["previewOutcome"])
        applied_del = engine.god_apply(preview_del["previewId"], "req-mem-del-1")
        assert_true(applied_del.get("ok"), applied_del)
        assert_true(applied_del["outcome"]["deletedCount"] >= 1, applied_del["outcome"])
        hits_after = store.query(agent=target["name"], text=secret, top_k=3)
        assert_true(not any(secret in (e.get("text") or "") for e in hits_after), hits_after)
        assert_true(not any(secret in line for line in target["memory"]["working"]),
                    "keyword purge should mirror local working tier")
        print("  OK memory_insert -> query hit; memory_delete by keyword removes store + local tier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_belief_plant_appears_in_think_payload():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target = engine.agents[0]
        target_id = target["id"]
        custom = "The stars whisper that winter will be mild."
        preview = engine.god_preview(_belief_plant_envelope(
            target_id, text=custom, plant_in_meme_texts=True))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-belief-1")
        assert_true(applied.get("ok"), applied)
        belief_id = applied["outcome"]["beliefId"]
        assert_true(belief_id in target["beliefs"], target["beliefs"])

        payload = engine._build_think_payload(target)
        belief_texts = payload.get("beliefs") or []
        assert_true(any(custom in t for t in belief_texts), belief_texts)
        assert_true(belief_id in (payload.get("belief_ids") or []), payload.get("belief_ids"))
        print("  OK belief_plant adds belief id and tenet to think payload beliefs line")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_surgery_privacy_no_public_leak():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target_id = engine.agents[0]["id"]
        secret = "SMOKE_DIVINE_MEMORY_SECRET_ZETA"
        custom_belief = "Secret divine doctrine about hidden treasure."

        preview = engine.god_preview(_memory_insert_envelope(target_id, secret))
        engine.god_apply(preview["previewId"], "req-mem-privacy-1")
        preview2 = engine.god_preview(_belief_plant_envelope(
            target_id, text=custom_belief, plant_in_meme_texts=False))
        engine.god_apply(preview2["previewId"], "req-mem-privacy-2")

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(secret not in dumped, "false memory text leaked into /state")
        assert_true(all(r.get("kind") not in ("memory_insert", "memory_delete", "belief_plant")
                        for r in snap["god"]["recentPublicInterventions"]),
                    "memory surgery kinds leaked into recentPublicInterventions")
        assert_true(not any(secret in line for line in engine.activityLog),
                    "false memory leaked into activity log")
        assert_true(not any(secret in (c.get("message") or "") for c in engine.conversationLog),
                    "false memory leaked into conversation log")
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(not any(secret in (entry.get("text") or "") for entry in chronicle),
                    "false memory leaked into chronicle")

        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == target_id)
        assert_true("memoryCounts" in sight_agent, sight_agent)
        assert_true(sight_agent["beliefCount"] >= 1, sight_agent)
        sight_dump = json.dumps(sight)
        assert_true(secret not in sight_dump, "memory text must not appear in sight")
        print("  OK memory surgery absent from /state activity/chronicle/god public; sight counts only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_delete_requires_filter():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target_id = engine.agents[0]["id"]
        rejected = engine.god_preview(_memory_delete_envelope(target_id))
        assert_true(not rejected.get("ok"), rejected)
        assert_true("at least one" in (rejected.get("reason") or ""), rejected)
        print("  OK memory_delete rejected without filters")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_blue_pill_strips_divine_lines():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        other = engine.agents[1] if len(engine.agents) > 1 else agent

        prov_preview = engine.god_preview(_providence_envelope("Public providence smoke.", duration=5000))
        engine.god_apply(prov_preview["previewId"], "req-mask-prov")
        omen_preview = engine.god_preview(_omen_envelope(agent["id"], "Private omen smoke.", duration=5000))
        engine.god_apply(omen_preview["previewId"], "req-mask-omen")
        other_omen = engine.god_preview(_omen_envelope(other["id"], "OTHER_AGENT_SECRET_OMEN", duration=5000))
        engine.god_apply(other_omen["previewId"], "req-mask-other-omen")

        with engine.lock:
            before = engine._build_think_payload(agent)
        assert_true(before.get("divine_public_line"), before)
        assert_true(before.get("divine_private_line"), before)

        mask_preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        assert_true(mask_preview.get("ok"), mask_preview)
        applied = engine.god_apply(mask_preview["previewId"], "req-mask-blue")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            masked = engine._build_think_payload(agent)
        assert_true(masked.get("divine_public_line") is None, masked)
        assert_true(masked.get("divine_private_line") is None, masked)
        assert_true(masked.get("divine_public_event_line") is None, masked)
        print("  OK blue_pill strips divine public/private/event lines from think payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_red_pill_truth_without_private_omen_leak():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        target = engine.agents[0]
        other = engine.agents[1] if len(engine.agents) > 1 else target
        secret = "OTHER_AGENT_RED_PILL_SECRET_OMEN"

        engine.god_apply(
            engine.god_preview(_omen_envelope(other["id"], secret, duration=5000))["previewId"],
            "req-red-other-omen")
        engine.civilization["godState"]["intervened"] = True

        mask_preview = engine.god_preview(_context_mask_envelope(
            target["id"], "red_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(mask_preview["previewId"], "req-mask-red")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            payload = engine._build_think_payload(target)
        truth = payload.get("divine_simulation_truth_line") or ""
        assert_true("SIMULATION TRUTH" in truth, truth)
        assert_true(secret not in truth, "red pill leaked another agent's private omen")
        assert_true(secret not in json.dumps(payload), payload)
        prompt = srv.build_user_prompt(payload)
        assert_true("SIMULATION TRUTH" in prompt, prompt)
        assert_true(secret not in prompt, prompt)
        print("  OK red_pill injects simulation-truth line without leaking other private omens")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_whisper_chain_forges_payload_only():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        true_line = "TRUE_CONVERSATION_LOG_MARKER_XYZ"
        engine.conversationLog.insert(0, {
            "from": "Alice", "to": agent["name"], "message": true_line,
            "kind": "speech", "frame": engine.frameTick,
        })
        forged = [{"from": "Morpheus", "to": agent["name"], "message": "Wake up."}]

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "whisper_chain",
            duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES,
            forgedConversations=forged))
        applied = engine.god_apply(preview["previewId"], "req-mask-whisper")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            payload = engine._build_think_payload(agent)
        assert_true("Morpheus" in (payload.get("recent_conversations") or ""), payload)
        assert_true(true_line not in (payload.get("recent_conversations") or ""), payload)
        assert_true(any(true_line in (c.get("message") or "") for c in engine.conversationLog),
                    "conversationLog must remain unchanged")
        print("  OK whisper_chain forges recent_conversations in payload only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_dream_replaces_and_rejects_unknown_keys():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        with engine.lock:
            base = engine._build_think_payload(agent)
        real_wood = (base.get("resources") or {}).get("wood", 0)

        bad = engine.god_preview(_context_mask_envelope(
            agent["id"], "dream",
            dreamSnapshot={"resources": {"wood": 99}, "evil_key": 1}))
        assert_true(not bad.get("ok"), bad)
        assert_true("unknown keys" in (bad.get("reason") or ""), bad)

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "dream",
            duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES,
            dreamSnapshot={"resources": {"wood": 99}, "hunger": 42.0}))
        applied = engine.god_apply(preview["previewId"], "req-mask-dream")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            dreamed = engine._build_think_payload(agent)
        assert_true(dreamed["resources"].get("wood") == 99, dreamed["resources"])
        assert_true(dreamed.get("hunger") == 42.0, dreamed)
        assert_true(real_wood != 99 or dreamed["resources"].get("wood") == 99, dreamed)
        print("  OK dream replaces allowlisted fields; unknown dreamSnapshot keys rejected")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_cancel_expiry_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        prov_preview = engine.god_preview(_providence_envelope("Mask cancel providence.", duration=5000))
        engine.god_apply(prov_preview["previewId"], "req-mask-cancel-prov")

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-mask-expire")
        assert_true(applied.get("ok"), applied)
        mask_id = applied["outcome"]["interventionId"]

        with engine.lock:
            live = engine._build_think_payload(agent)
        assert_true(live.get("divine_public_line") is None, live)
        god = engine.civilization["godState"]
        assert_true(str(agent["id"]) in god.get("contextMasks", {}), god)

        cancelled = engine.god_cancel(mask_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(str(agent["id"]) not in god.get("contextMasks", {}), god)
        with engine.lock:
            after_cancel = engine._build_think_payload(agent)
        assert_true(after_cancel.get("divine_public_line") == "Mask cancel providence.", after_cancel)

        preview2 = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine.god_apply(preview2["previewId"], "req-mask-expire2")
        with engine.lock:
            engine.frameTick += se.GOD_GUIDANCE_MIN_DURATION_FRAMES
            engine._expire_divine_effects()
        assert_true(str(agent["id"]) not in god.get("contextMasks", {}), god)

        snap = engine.snapshot()
        assert_true("contextMasks" not in json.dumps(snap.get("god") or {}), snap)
        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == agent["id"])
        assert_true(sight_agent.get("contextMask") is None, sight_agent)
        assert_true(all(r.get("kind") != "context_mask"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "context_mask leaked into recentPublicInterventions")
        print("  OK context_mask cancel/expiry clears; absent from /state public god")
    finally:
        se.GOD_MODE_ENABLED = old
