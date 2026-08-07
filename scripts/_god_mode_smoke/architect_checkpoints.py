# Divine Matrix Phase 9 (Architect Zones) plus Phase 10 (checkpoints, deja vu, crowd compulsion, dream broadcast, village pulse) smoke.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_architect_zone_door_blocks_without_key_allows_with_key():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        district_id = "farm_north"
        bounds = engine.civilization["districts"][district_id]["bounds"]
        start_x = bounds["x1"] + 19
        start_y = bounds["y1"] + 20
        target_x = bounds["x1"] + 60
        agent["x"] = start_x
        agent["y"] = start_y
        agent["targetX"] = target_x
        agent["targetY"] = start_y
        agent["currentDistrict"] = district_id
        agent["godKeys"] = set()

        preview = engine.god_preview(_architect_zone_envelope(
            "door", district_id, ["1,0"], keyId="matrix-red-key"))
        applied = engine.god_apply(preview["previewId"], "req-arch-door")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            engine._move_agent(agent, scale=5.0)
        blocked_x = agent["x"]
        assert_true(blocked_x < bounds["x1"] + 40, agent)

        grant_preview = engine.god_preview(_architect_zone_envelope(
            "door", district_id, ["2,0"], keyId="matrix-red-key",
            grantKeyAgentIds=[agent["id"]]))
        engine.god_apply(grant_preview["previewId"], "req-arch-door-grant")
        assert_true(engine._agent_has_god_key(agent, "matrix-red-key"), agent)

        agent["targetX"] = target_x
        with engine.lock:
            engine._move_agent(agent, scale=5.0)
        assert_true(agent["x"] > blocked_x, agent)
        print("  OK architect door blocks move without key and allows with godKeys tag")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_limbo_freezes_think_and_release_restores():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        prior_x, prior_y = agent["x"], agent["y"]
        prior_tx, prior_ty = agent["targetX"], agent["targetY"]

        preview = engine.god_preview(_architect_zone_envelope(
            "limbo", "ocean", ["0,0"], holdAgentIds=[agent["id"]]))
        applied = engine.god_apply(preview["previewId"], "req-arch-limbo")
        assert_true(applied.get("ok"), applied)
        zone_id = applied["interventionId"]

        assert_true(agent.get("divineHold"), agent)
        assert_true(agent["x"] == se.GOD_LIMBO_STATION[0], agent)
        assert_true(agent["y"] == se.GOD_LIMBO_STATION[1], agent)
        assert_true(not engine._schedule_think(agent), agent)

        release_preview = engine.god_preview({
            "kind": "architect_release_hold",
            "payload": {"zoneId": zone_id, "agentIds": [agent["id"]]},
        })
        released = engine.god_apply(release_preview["previewId"], "req-arch-release")
        assert_true(released.get("ok"), released)
        assert_true(not agent.get("divineHold"), agent)
        assert_true(abs(agent["x"] - prior_x) < 0.01 and abs(agent["y"] - prior_y) < 0.01, agent)
        assert_true(agent["targetX"] == prior_tx and agent["targetY"] == prior_ty, agent)
        print("  OK architect limbo sets divineHold, blocks think, release restores pose")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_paint_cancel_reverts():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district_id = "farm_north"
        district = engine.civilization["districts"][district_id]
        engine._ensure_district_terrain(district)
        key = engine._tile_key(0, 0)
        district["terrain"][key] = "soil"

        preview = engine.god_preview(_architect_zone_envelope(
            "paint", district_id, ["0,0"], paintTerrain="rock", reversible=True))
        applied = engine.god_apply(preview["previewId"], "req-arch-paint")
        assert_true(applied.get("ok"), applied)
        zone_id = applied["interventionId"]
        assert_true(district["terrain"][key] == "rock", district["terrain"])

        cancel_preview = engine.god_preview({
            "kind": "architect_zone_cancel",
            "payload": {"zoneId": zone_id},
        })
        engine.god_apply(cancel_preview["previewId"], "req-arch-paint-cancel")
        assert_true(district["terrain"][key] == "soil", district["terrain"])
        print("  OK architect paint cancel reverts reversible terrain")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_privacy_and_sight_summary():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        preview = engine.god_preview(_architect_zone_envelope(
            "door", "farm_north", ["3,0"], keyId="secret-door-key",
            grantKeyAgentIds=[agent["id"]]))
        applied = engine.god_apply(preview["previewId"], "req-arch-privacy")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("architectZones" not in dumped, "architectZones leaked into /state")
        assert_true(all(r.get("kind") != "architect_zone"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "door architect_zone leaked into recentPublicInterventions")
        assert_true("secret-door-key" not in dumped, "key id leaked into /state")

        sight = engine.god_sight()
        assert_true(isinstance(sight.get("architectZones"), list), sight)
        assert_true(any(z.get("kind") == "door" for z in sight["architectZones"]), sight)
        assert_true(all("keyId" not in z for z in sight["architectZones"]), sight)
        print("  OK architect zones stay off /state; Sight has summaries without secrets")
    finally:
        se.GOD_MODE_ENABLED = old
def test_checkpoint_create_restore_roundtrip():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state.db")
    tmp_ckpt = str(Path(tmpdir) / "god-checkpoints")
    try:
        se.DB_PATH = tmp_db
        se.GOD_CHECKPOINT_ROOT = tmp_ckpt
        engine, store = make_engine_with_memory()
        engine.god_checkpoint_root = tmp_ckpt
        store.store("Alice", "smoke memory before checkpoint", salience=0.5, kind="event")

        original_tick = engine.frameTick
        original_wood = engine.civilization["stockpile"].get("wood", 0)

        create_preview = engine.god_preview(_checkpoint_create_envelope("before grant"))
        assert_true(create_preview.get("ok"), create_preview)
        assert_true(create_preview.get("reversibilityClass") == "irreversible", create_preview)
        created = engine.god_apply(create_preview["previewId"], "req-ckpt-create")
        assert_true(created.get("ok"), created)
        checkpoint_id = created["outcome"]["checkpointId"]
        ckpt_dir = Path(tmp_ckpt) / checkpoint_id
        assert_true((ckpt_dir / "state.db").is_file(), ckpt_dir)
        assert_true((ckpt_dir / "memory_store.json").is_file(), ckpt_dir)

        grant_preview = engine.god_preview(_grant_envelope("wood", 7, "stockpile"))
        engine.god_apply(grant_preview["previewId"], "req-ckpt-grant")
        mutated_wood = engine.civilization["stockpile"].get("wood", 0)
        assert_true(mutated_wood == original_wood + 7, (original_wood, mutated_wood))
        engine.frameTick += 50

        restore_preview = engine.god_preview(_checkpoint_restore_envelope(checkpoint_id))
        assert_true(restore_preview.get("ok"), restore_preview)
        warning = (restore_preview.get("previewOutcome") or {}).get("irreversibleWarning") or ""
        assert_true("Irreversible world replace" in warning, restore_preview)
        restored = engine.god_apply(restore_preview["previewId"], "req-ckpt-restore")
        assert_true(restored.get("ok"), restored)
        assert_true(engine.frameTick == original_tick, (original_tick, engine.frameTick))
        assert_true(engine.civilization["stockpile"].get("wood", 0) == original_wood,
                    engine.civilization["stockpile"])
        assert_true(engine._god_preview_cache == {}, engine._god_preview_cache)
        assert_true("req-ckpt-restore" in engine._god_requests, engine._god_requests)

        snap = engine.snapshot()
        god_pub = snap.get("god") or {}
        assert_true("checkpoints" not in god_pub, god_pub)
        assert_true(any(r.get("kind") == "checkpoint_restore"
                        for r in god_pub.get("recentPublicInterventions") or []),
                    god_pub)
        print("  OK checkpoint create -> mutate -> restore roundtrip on temp paths")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old


def test_checkpoint_cap_reject_and_replace_oldest():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    try:
        se.DB_PATH = str(Path(tmpdir) / "state.db")
        se.GOD_CHECKPOINT_ROOT = str(Path(tmpdir) / "god-checkpoints")
        engine = make_engine()
        engine.god_checkpoint_root = se.GOD_CHECKPOINT_ROOT
        for i in range(se.GOD_CHECKPOINT_MAX):
            preview = engine.god_preview(_checkpoint_create_envelope(f"ck{i}"))
            engine.god_apply(preview["previewId"], f"req-cap-{i}")

        blocked = engine.god_preview(_checkpoint_create_envelope("one too many"))
        assert_true(not blocked.get("ok"), blocked)
        assert_true("cap" in (blocked.get("reason") or "").lower(), blocked)

        replace_preview = engine.god_preview(_checkpoint_create_envelope("replacement", True))
        assert_true(replace_preview.get("ok"), replace_preview)
        replaced = engine.god_apply(replace_preview["previewId"], "req-cap-replace")
        assert_true(replaced.get("ok"), replaced)
        checkpoints = engine.civilization["godState"]["checkpoints"]
        assert_true(len(checkpoints) == se.GOD_CHECKPOINT_MAX, len(checkpoints))
        assert_true(checkpoints[0]["label"] == "ck1", checkpoints)
        assert_true(checkpoints[-1]["label"] == "replacement", checkpoints)
        print("  OK checkpoint cap rejects preview unless replaceOldest")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old


def test_checkpoint_sight_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    try:
        se.DB_PATH = str(Path(tmpdir) / "state.db")
        se.GOD_CHECKPOINT_ROOT = str(Path(tmpdir) / "god-checkpoints")
        engine = make_engine()
        engine.god_checkpoint_root = se.GOD_CHECKPOINT_ROOT
        preview = engine.god_preview(_checkpoint_create_envelope("privacy ck"))
        engine.god_apply(preview["previewId"], "req-ckpt-privacy")
        sight = engine.god_sight()
        dumped = json.dumps(sight)
        assert_true("backup/god-checkpoints" not in dumped, "checkpoint path leaked in sight")
        assert_true(isinstance(sight.get("checkpoints"), list), sight)
        assert_true(sight["checkpoints"][0].get("label") == "privacy ck", sight)
        assert_true("path" not in sight["checkpoints"][0], sight["checkpoints"][0])
        print("  OK checkpoint Sight summaries omit disk paths")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old
def test_god_state_v3_default_shape():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]
        assert_true(god.get("version") == 3, god.get("version"))
        assert_true(isinstance(god.get("decisionDigests"), list), god)
        assert_true(isinstance(god.get("dejaVuReplays"), dict), god)
        assert_true(isinstance(god.get("crowdCompulsions"), dict), god)
        assert_true(isinstance(god.get("dreamBroadcasts"), dict), god)
        normalized = engine._normalize_god_state({"version": 2, "decisionDigests": [
            {"frameTick": 10, "agentId": 1, "action": "rest", "reasoningHash": "abc"},
            {"bad": True},
        ]})
        assert_true(normalized["version"] == 2, normalized)
        assert_true(len(normalized["decisionDigests"]) == 1, normalized["decisionDigests"])
        assert_true(normalized["decisionDigests"][0]["action"] == "rest", normalized)
        print("  OK godState v3 default + normalize backfills digests")
    finally:
        se.GOD_MODE_ENABLED = old


def test_decision_digest_capture_on_gated_apply():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "rest", "reasoning": "smoke digest path",
            })
        digests = engine.civilization["godState"].get("decisionDigests") or []
        assert_true(len(digests) >= 1, digests)
        last = digests[-1]
        assert_true(last.get("agentId") == agent["id"], last)
        assert_true(last.get("action") == "rest", last)
        assert_true(isinstance(last.get("reasoningHash"), str), last)
        snap = engine.snapshot()
        assert_true("decisionDigests" not in (snap.get("god") or {}), snap.get("god"))
        print("  OK decision digest captured on gated apply; not in /state")
    finally:
        se.GOD_MODE_ENABLED = old


def test_deja_vu_replay_apply_and_cancel():
    old = se.GOD_MODE_ENABLED
    old_replay = se.GOD_DEJA_VU_REPLAY
    se.GOD_MODE_ENABLED = True
    se.GOD_DEJA_VU_REPLAY = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        with engine.lock:
            for action in ("rest", "talk_to_nearby"):
                engine._apply_gated_decision(agent, {
                    "action": action, "reasoning": f"seed {action}",
                })
        preview = engine.god_preview(_deja_vu_replay_envelope(agent["id"], maxSteps=2))
        assert_true(preview.get("ok"), preview)
        assert_true(preview.get("reversibilityClass") == "cancellable", preview)
        norm = preview.get("normalizedCommand") or {}
        steps = (norm.get("payload") or {}).get("replaySteps") or []
        assert_true(len(steps) == 2, steps)
        applied = engine.god_apply(preview["previewId"], "req-deja-vu-1")
        assert_true(applied.get("ok"), applied)
        replays = engine.civilization["godState"].get("dejaVuReplays") or {}
        assert_true(len(replays) == 1, replays)
        replay_id = next(iter(replays.keys()))
        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "collect_resource", "reasoning": "ignored under replay",
            })
        assert_true(agent.get("lastAction") == steps[0]["action"], agent)
        parent = (engine.civilization["godState"].get("dejaVuReplays") or {}).get(replay_id)
        assert_true(isinstance(parent, dict) and parent.get("currentIndex") == 1, parent)
        cancelled = engine.god_cancel(replay_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "deja_vu_replay", cancelled)
        assert_true(replay_id not in (engine.civilization["godState"].get("dejaVuReplays") or {}),
                    "parent removed after cancel")
        print("  OK deja_vu_replay preview/apply sequences compulsion; cancel clears parent")
    finally:
        se.GOD_DEJA_VU_REPLAY = old_replay
        se.GOD_MODE_ENABLED = old


def test_deja_vu_replay_rejects_when_flag_off():
    old = se.GOD_MODE_ENABLED
    old_replay = se.GOD_DEJA_VU_REPLAY
    se.GOD_MODE_ENABLED = True
    se.GOD_DEJA_VU_REPLAY = False
    try:
        engine = make_engine()
        preview = engine.god_preview({"kind": "deja_vu_replay", "payload": {}})
        assert_true(not preview.get("ok"), preview)
        assert_true("GOD_DEJA_VU_REPLAY" in (preview.get("reason") or ""), preview)
        print("  OK deja_vu_replay rejects cleanly when GOD_DEJA_VU_REPLAY is off")
    finally:
        se.GOD_DEJA_VU_REPLAY = old_replay
        se.GOD_MODE_ENABLED = old


def test_crowd_compulsion_batch_apply_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        theme = "Secret crowd theme"
        preview = engine.god_preview(_crowd_compulsion_envelope(
            [
                {"targetId": a0["id"], "pinnedDecision": {"action": "rest", "reasoning": "crowd A"}},
                {"targetId": a1["id"], "pinnedDecision": {"action": "rest", "reasoning": "crowd B"}},
            ],
            theme=theme,
            remaining_turns=2,
        ))
        assert_true(preview.get("ok"), preview)
        assert_true(preview.get("reversibilityClass") == "cancellable", preview)
        applied = engine.god_apply(preview["previewId"], "req-crowd-1")
        assert_true(applied.get("ok"), applied)
        parent_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(parent_id in god["crowdCompulsions"], god)
        assert_true(god["crowdCompulsions"][parent_id]["theme"] == theme, god)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(theme not in dumped, "crowd theme leaked into /state")
        assert_true("crowdCompulsions" not in dumped, "crowdCompulsions map leaked into /state")
        assert_true(all(r.get("kind") != "crowd_compulsion"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "crowd_compulsion leaked into recentPublicInterventions")

        with engine.lock:
            engine._apply_gated_decision(a0, {
                "action": "collect_resource", "reasoning": "ignored under crowd",
            })
            engine._apply_gated_decision(a1, {
                "action": "collect_resource", "reasoning": "ignored under crowd",
            })
        assert_true(a0.get("lastAction") == "rest", a0)
        assert_true(a1.get("lastAction") == "rest", a1)

        sight = engine.god_sight()
        assert_true(any(c.get("id") == parent_id for c in (sight.get("crowdCompulsions") or [])),
                    sight.get("crowdCompulsions"))
        print("  OK crowd_compulsion batch apply; theme/map absent from /state; gates compel per target")
    finally:
        se.GOD_MODE_ENABLED = old


def test_crowd_compulsion_cancel_clears_linked_gates():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview = engine.god_preview(_crowd_compulsion_envelope(
            [
                {"targetId": a0["id"], "pinnedDecision": {"action": "rest", "reasoning": "cancel A"}},
                {"targetId": a1["id"], "pinnedDecision": {"action": "rest", "reasoning": "cancel B"}},
            ],
            duration=5000,
        ))
        applied = engine.god_apply(preview["previewId"], "req-crowd-cancel")
        parent_id = applied["outcome"]["interventionId"]
        gates = engine.civilization["godState"].get("decisionGates") or {}
        assert_true(str(a0["id"]) in gates and str(a1["id"]) in gates, gates)

        cancelled = engine.god_cancel(parent_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "crowd_compulsion", cancelled)
        assert_true(parent_id not in (engine.civilization["godState"].get("crowdCompulsions") or {}),
                    "parent removed after cancel")
        gates_after = engine.civilization["godState"].get("decisionGates") or {}
        assert_true(str(a0["id"]) not in gates_after and str(a1["id"]) not in gates_after, gates_after)
        print("  OK god_cancel on crowd_compulsion id revokes all linked gates and removes parent")
    finally:
        se.GOD_MODE_ENABLED = old


def test_dream_broadcast_batch_apply_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        secret = "SMOKE_DREAM_BROADCAST_SECRET_XYZZY"
        snapshot = {"resources": {"wood": 777}, "recent_conversations": secret}
        preview = engine.god_preview(_dream_broadcast_envelope(
            [a0["id"], a1["id"]], snapshot, duration=5000))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-dream-bcast-1")
        assert_true(applied.get("ok"), applied)
        parent_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(parent_id in god["dreamBroadcasts"], god)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(secret not in dumped, "dream text leaked into /state")
        assert_true("dreamBroadcasts" not in dumped, "dreamBroadcasts map leaked into /state")
        assert_true(all(r.get("kind") != "dream_broadcast"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "dream_broadcast leaked into recentPublicInterventions")

        with engine.lock:
            dreamed0 = engine._build_think_payload(a0)
            dreamed1 = engine._build_think_payload(a1)
        assert_true(dreamed0["resources"].get("wood") == 777, dreamed0)
        assert_true(dreamed1["resources"].get("wood") == 777, dreamed1)

        sight = engine.god_sight()
        sight_dump = json.dumps(sight)
        assert_true(secret not in sight_dump, "dream text must not appear in sight")
        assert_true(any(b.get("id") == parent_id for b in (sight.get("dreamBroadcasts") or [])),
                    sight.get("dreamBroadcasts"))
        print("  OK dream_broadcast batch apply; dream text/maps absent from /state and sight")
    finally:
        se.GOD_MODE_ENABLED = old


def test_dream_broadcast_cancel_clears_linked_masks():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview = engine.god_preview(_dream_broadcast_envelope(
            [a0["id"], a1["id"]],
            {"resources": {"wood": 1}},
            duration=5000,
        ))
        applied = engine.god_apply(preview["previewId"], "req-dream-bcast-cancel")
        parent_id = applied["outcome"]["interventionId"]
        masks = engine.civilization["godState"].get("contextMasks") or {}
        assert_true(str(a0["id"]) in masks and str(a1["id"]) in masks, masks)

        cancelled = engine.god_cancel(parent_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "dream_broadcast", cancelled)
        assert_true(parent_id not in (engine.civilization["godState"].get("dreamBroadcasts") or {}),
                    "parent removed after cancel")
        masks_after = engine.civilization["godState"].get("contextMasks") or {}
        assert_true(str(a0["id"]) not in masks_after and str(a1["id"]) not in masks_after, masks_after)
        print("  OK god_cancel on dream_broadcast id revokes all linked masks and removes parent")
    finally:
        se.GOD_MODE_ENABLED = old


def run_matrix_phase10_smoke():
    print("Divine Matrix Phase 10 smoke -- Reload / Déjà Vu checkpoints")
    test_checkpoint_create_restore_roundtrip()
    test_checkpoint_cap_reject_and_replace_oldest()
    test_checkpoint_sight_privacy()
    test_deja_vu_replay_rejects_when_flag_off()


def run_divine_console_phase8_smoke():
    print("Divine Console Phase 8 smoke -- GOD_STATE_VERSION 3 + Déjà Vu")
    test_god_state_v3_default_shape()
    test_decision_digest_capture_on_gated_apply()
    test_deja_vu_replay_apply_and_cancel()
    test_deja_vu_replay_rejects_when_flag_off()


def run_divine_console_phase9_smoke():
    print("Divine Console Phase 9 smoke -- crowd compulsion + dream broadcast")
    test_crowd_compulsion_batch_apply_and_privacy()
    test_crowd_compulsion_cancel_clears_linked_gates()
    test_dream_broadcast_batch_apply_and_privacy()
    test_dream_broadcast_cancel_clears_linked_masks()


def test_sight_village_pulse_ephemeral():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_pulse.db")
    try:
        engine = make_engine()
        sight = engine.god_sight()
        assert_true(sight.get("ok"), sight)
        pulse = sight.get("pulse")
        assert_true(isinstance(pulse, dict), pulse)
        for key in (
            "crisisAgents", "stockpileTotals", "openProjectsCount",
            "sageStatus", "weather", "activeEventTitles", "providence",
        ):
            assert_true(key in pulse, f"missing pulse.{key}: {pulse}")
        assert_true(isinstance(pulse["crisisAgents"], list), pulse)
        assert_true(isinstance(pulse["stockpileTotals"], dict), pulse)
        assert_true(isinstance(pulse["openProjectsCount"], int), pulse)
        assert_true(isinstance(pulse["sageStatus"], dict), pulse)
        assert_true(isinstance(pulse["weather"], dict), pulse)
        assert_true(isinstance(pulse["activeEventTitles"], list), pulse)
        assert_true(isinstance(pulse["providence"], dict), pulse)
        assert_true("pulse" not in json.dumps(engine.civilization["godState"]),
                    "pulse must not persist in godState")

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")
        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        assert_true("pulse" not in restored.civilization["godState"],
                    "pulse in godState after restore")
        sight2 = restored.god_sight()
        assert_true(isinstance(sight2.get("pulse"), dict), sight2)
        print("  OK god_sight pulse ephemeral with expected keys; not in godState")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def run_divine_console_phase10_smoke():
    print("Divine Console Phase 10 smoke -- village pulse (Sight aggregate)")
    test_sight_village_pulse_ephemeral()
