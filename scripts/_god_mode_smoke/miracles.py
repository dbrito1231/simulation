# Sovereign God mode Phase 4 -- bounded immediate miracles (vitals, grant, structure condition, repair, clear ruins).
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_agent_vitals_happy_path_and_clamping():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"], agent["hunger"] = 50.0, 50.0

        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=10, hunger_delta=-5))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "irreversible", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome == {
            "targetId": agent["id"], "targetName": agent["name"],
            "oldHealth": 50.0, "newHealth": 60.0, "oldHunger": 50.0, "newHunger": 45.0,
        }, outcome)
        applied = engine.god_apply(preview["previewId"], "req-vitals-1")
        assert_true(applied["ok"], applied)
        assert_true(applied["outcome"]["newHealth"] == outcome["newHealth"]
                    and applied["outcome"]["newHunger"] == outcome["newHunger"],
                    "applied value must equal the previewed value")
        assert_true(agent["health"] == 60.0 and agent["hunger"] == 45.0, (agent["health"], agent["hunger"]))
        assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
        assert_true(any("divine" in line.lower() for line in engine.activityLog[:1]), engine.activityLog[0])
        assert_true(engine.civilization["chronicle"][-1]["source"] == "divine", engine.civilization["chronicle"][-1])

        # Clamp at 100.
        agent["health"] = 95.0
        p = engine.god_preview(_vitals_envelope(agent["id"], health_delta=50))
        assert_true(p["previewOutcome"]["newHealth"] == 100.0, p["previewOutcome"])
        a = engine.god_apply(p["previewId"], "req-vitals-clamp-100")
        assert_true(a["ok"] and agent["health"] == 100.0, (a, agent["health"]))

        # Clamp at 0 for hunger (no death floor -- hunger alone cannot kill).
        agent["hunger"] = 2.0
        p2 = engine.god_preview(_vitals_envelope(agent["id"], hunger_delta=-50))
        assert_true(p2["previewOutcome"]["newHunger"] == 0.0, p2["previewOutcome"])
        a2 = engine.god_apply(p2["previewId"], "req-vitals-clamp-hunger-0")
        assert_true(a2["ok"] and agent["hunger"] == 0.0, (a2, agent["hunger"]))

        print("  OK agent_vitals happy path + clamping at 0/100; applied == previewed; divine attribution")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_vitals_cannot_kill():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 2.0
        agent["incapacitated"] = False

        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=-se.GOD_VITALS_DELTA_MAX))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewOutcome"]["newHealth"] == se.GOD_VITALS_HEALTH_FLOOR,
                    preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-vitals-nokill-1")
        assert_true(applied["ok"], applied)
        assert_true(agent["health"] == se.GOD_VITALS_HEALTH_FLOOR, agent["health"])
        assert_true(agent["health"] > 0, "a negative vitals delta must never reach 0")
        assert_true(agent["deathFrame"] is None, "a negative vitals delta must never set deathFrame")

        # Repeated large negative deltas still never cross the floor.
        for i in range(3):
            p = engine.god_preview(_vitals_envelope(agent["id"], health_delta=-se.GOD_VITALS_DELTA_MAX))
            a = engine.god_apply(p["previewId"], f"req-vitals-nokill-repeat-{i}")
            assert_true(a["ok"], a)
            assert_true(agent["health"] == se.GOD_VITALS_HEALTH_FLOOR, agent["health"])
            assert_true(agent["deathFrame"] is None, "deathFrame must stay None across repeated damage")

        print(f"  OK agent_vitals cannot kill: health floors at {se.GOD_VITALS_HEALTH_FLOOR}, "
              f"deathFrame never set")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_vitals_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target = engine.agents[0]

        unknown = engine.god_preview(_vitals_envelope(999999, health_delta=5))
        assert_true(unknown == {"ok": False, "reason": "unknown target agent"}, unknown)

        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_vitals_envelope(target["id"], health_delta=5))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None

        neither = engine.god_preview(_vitals_envelope(target["id"]))
        assert_true(neither == {
            "ok": False, "reason": "at least one of healthDelta/hungerDelta must be non-zero",
        }, neither)

        too_big = engine.god_preview(_vitals_envelope(target["id"], health_delta=se.GOD_VITALS_DELTA_MAX + 1))
        assert_true(too_big["ok"] is False and "magnitude exceeds" in too_big["reason"], too_big)
        print("  OK agent_vitals rejects unknown/dead targets, no-op deltas, and out-of-range magnitude")
    finally:
        se.GOD_MODE_ENABLED = old


def test_grant_resource_happy_path_and_carry_semantics():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        c = engine.civilization
        resource_id = "wood"
        assert_true(resource_id in c["resourceRegistry"], "test fixture assumes 'wood' is a known resource")

        # Stockpile grant.
        before = c["stockpile"].get(resource_id, 0)
        preview = engine.god_preview(_grant_envelope(resource_id, 15))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"] == {
            "resourceId": resource_id, "amount": 15, "targetKind": "stockpile",
            "agentAdded": 0, "stockpileAdded": 15,
        }, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-grant-stockpile")
        assert_true(applied["ok"], applied)
        assert_true(c["stockpile"].get(resource_id, 0) == before + 15, c["stockpile"])
        assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])

        # Agent grant with carry-cap overflow: fill the agent close to cap,
        # then grant more than the remaining room -- remainder must route to
        # the stockpile, never be silently dropped or exceed capacity.
        agent = engine.agents[0]
        cap = engine._carry_cap(agent)
        agent["resources"][resource_id] = cap - 3
        stock_before = c["stockpile"].get(resource_id, 0)
        preview2 = engine.god_preview(_grant_envelope(resource_id, 10, target={"agentId": agent["id"]}))
        assert_true(preview2["ok"], preview2)
        outcome2 = preview2["previewOutcome"]
        assert_true(outcome2["agentAdded"] == 3 and outcome2["stockpileAdded"] == 7, outcome2)
        applied2 = engine.god_apply(preview2["previewId"], "req-grant-agent-overflow")
        assert_true(applied2["ok"], applied2)
        assert_true(agent["resources"][resource_id] == cap, "agent resources must not exceed carry cap")
        assert_true(c["stockpile"].get(resource_id, 0) == stock_before + 7, c["stockpile"])
        assert_true(applied2["outcome"]["agentAdded"] == outcome2["agentAdded"]
                    and applied2["outcome"]["stockpileAdded"] == outcome2["stockpileAdded"],
                    "applied split must equal the previewed split")
        print("  OK grant_resource happy path (stockpile + agent), carry-cap overflow routed to stockpile")
    finally:
        se.GOD_MODE_ENABLED = old


def test_grant_resource_rejections_and_caps():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target = engine.agents[0]

        unknown_resource = engine.god_preview(_grant_envelope("definitely-not-a-real-resource", 5))
        assert_true(unknown_resource == {"ok": False, "reason": "unknown resource id"}, unknown_resource)

        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_grant_envelope("wood", 5, target={"agentId": target["id"]}))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None

        over_command_cap = engine.god_preview(_grant_envelope("wood", se.GOD_GRANT_PER_COMMAND_CAP + 1))
        assert_true(over_command_cap["ok"] is False
                    and "per-command cap" in over_command_cap["reason"], over_command_cap)

        # Session cap: drive the running total right up to the cap, then one
        # more unit must be refused.
        engine._god_grant_session_total = se.GOD_GRANT_SESSION_CAP - 5
        under_cap = engine.god_preview(_grant_envelope("wood", 5))
        assert_true(under_cap["ok"], under_cap)
        over_session_cap = engine.god_preview(_grant_envelope("wood", 6))
        assert_true(over_session_cap["ok"] is False
                    and "per-session cap" in over_session_cap["reason"], over_session_cap)
        applied = engine.god_apply(under_cap["previewId"], "req-grant-session-cap")
        assert_true(applied["ok"], applied)
        assert_true(engine._god_grant_session_total == se.GOD_GRANT_SESSION_CAP,
                    engine._god_grant_session_total)
        print("  OK grant_resource rejects unknown resource/dead target, "
              "enforces per-command and per-session caps")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_repair_and_damage():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        # Repair, clamped at 100.
        s = _add_test_structure(engine, condition=90.0)
        preview = engine.god_preview(_structure_envelope(s["id"], 50))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"] == {
            "structureId": s["id"], "structureName": s["name"],
            "oldCondition": 90.0, "newCondition": 100.0, "wouldBecomeRuin": False,
        }, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-struct-repair")
        assert_true(applied["ok"], applied)
        assert_true(s["condition"] == 100.0 and not s["isRuin"], s)
        assert_true(applied["outcome"]["newCondition"] == preview["previewOutcome"]["newCondition"],
                    "applied value must equal the previewed value")

        # Damage that does NOT cross the ruin threshold.
        s2 = _add_test_structure(engine, condition=80.0)
        preview2 = engine.god_preview(_structure_envelope(s2["id"], -20))
        applied2 = engine.god_apply(preview2["previewId"], "req-struct-damage-partial")
        assert_true(applied2["ok"] and s2["condition"] == 60.0 and not s2["isRuin"], (applied2, s2))

        print("  OK structure_condition repair (clamped at 100) and partial damage; applied == previewed")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_damage_crosses_ruin_with_homeless_handling():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        owner = engine.agents[0]
        s = _add_test_structure(engine, condition=5.0, home_of=owner["name"])
        owner["homeStructureId"] = s["id"]
        before_activity_len = len(engine.activityLog)

        preview = engine.god_preview(_structure_envelope(s["id"], -20))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewOutcome"]["wouldBecomeRuin"] is True, preview["previewOutcome"])
        assert_true(preview["previewOutcome"]["newCondition"] == 0.0, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-struct-ruin")
        assert_true(applied["ok"], applied)
        assert_true(applied["outcome"]["becameRuin"] is True, applied["outcome"])

        # Same ruin path as natural decay: condition floors at 0, isRuin set,
        # homeOf cleared, owner's homeStructureId cleared, homeless narration
        # emitted -- not a shortcut around _apply_structure_condition_delta.
        assert_true(s["condition"] == 0.0 and s["isRuin"] is True, s)
        assert_true(s["homeOf"] is None, s)
        assert_true(owner["homeStructureId"] is None, owner)
        new_activity = engine.activityLog[:len(engine.activityLog) - before_activity_len + 1]
        assert_true(any("homeless" in line for line in new_activity), engine.activityLog[:5])
        assert_true(any("collapsed into a ruin" in line for line in new_activity), engine.activityLog[:5])

        # Single-target structure_condition still rejects already-ruined
        # structures; batch repair_structures is the operator un-ruin path.
        already_ruin = engine.god_preview(_structure_envelope(s["id"], 10))
        assert_true(already_ruin == {"ok": False, "reason": "structure is already ruined"}, already_ruin)
        print("  OK structure_condition damage crossing the ruin threshold uses the normal ruin "
              "path (homeOf/homeStructureId cleared, homeless narration, decay-identical transition); "
              "single-target repair still rejects ruins")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        unknown = engine.god_preview(_structure_envelope(999999, 10))
        assert_true(unknown == {"ok": False, "reason": "unknown structure"}, unknown)

        ruin = _add_test_structure(engine, condition=0.0)
        ruin["isRuin"] = True
        already_ruined = engine.god_preview(_structure_envelope(ruin["id"], 10))
        assert_true(already_ruined == {"ok": False, "reason": "structure is already ruined"}, already_ruined)

        s = _add_test_structure(engine, condition=50.0)
        too_big = engine.god_preview(_structure_envelope(s["id"], se.GOD_STRUCTURE_DELTA_MAX + 1))
        assert_true(too_big["ok"] is False and "magnitude exceeds" in too_big["reason"], too_big)
        zero_delta = engine.god_preview(_structure_envelope(s["id"], 0))
        assert_true(zero_delta == {"ok": False, "reason": "delta must be non-zero"}, zero_delta)
        print("  OK structure_condition rejects unknown/already-ruined structures and out-of-range delta")
    finally:
        se.GOD_MODE_ENABLED = old


def test_repair_structures_batch_un_ruins_preview_and_apply():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        ruined = _add_test_structure(engine, condition=0.0)
        ruined["isRuin"] = True
        ruined["ruinedSinceFrame"] = engine.frameTick
        worn = _add_test_structure(engine, condition=40.0)

        preview = engine.god_preview(_repair_structures_envelope(
            "ids", structure_ids=[ruined["id"], worn["id"]]))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["count"] == 2, outcome)
        by_id = {row["structureId"]: row for row in outcome["structures"]}
        assert_true(by_id[ruined["id"]]["unRuined"] is True, by_id[ruined["id"]])
        assert_true(by_id[ruined["id"]]["newCondition"] >= se.REPAIR_CONDITION_RESTORE,
                    by_id[ruined["id"]])
        assert_true(by_id[worn["id"]]["newCondition"] > worn["condition"], by_id[worn["id"]])

        applied = engine.god_apply(preview["previewId"], "req-repair-structures-batch")
        assert_true(applied["ok"], applied)
        assert_true(not ruined["isRuin"] and ruined["condition"] >= se.REPAIR_CONDITION_RESTORE, ruined)
        assert_true(worn["condition"] == by_id[worn["id"]]["newCondition"], (worn, applied))
        assert_true(applied["outcome"]["count"] == preview["previewOutcome"]["count"],
                    "applied count must equal previewed count")
        print("  OK repair_structures batch un-ruins ruined + restores worn; applied == previewed")
    finally:
        se.GOD_MODE_ENABLED = old


def test_repair_structures_rejects_ruins_when_un_ruin_false():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        ruined = _add_test_structure(engine, condition=0.0)
        ruined["isRuin"] = True
        rejected = engine.god_preview(_repair_structures_envelope(
            "ids", structure_ids=[ruined["id"]], un_ruin=False))
        assert_true(rejected == {"ok": False, "reason": "unRuin must be true to include ruined structures"},
                    rejected)
        print("  OK repair_structures with unRuin=false rejects ruined targets")
    finally:
        se.GOD_MODE_ENABLED = old


def test_clear_ruins_deletes_registry_preview_and_apply():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        owner = engine.agents[0]
        ruin_a = _add_test_structure(engine, condition=0.0, home_of=owner["name"])
        ruin_a["isRuin"] = True
        owner["homeStructureId"] = ruin_a["id"]
        ruin_b = _add_test_structure(engine, condition=0.0)
        ruin_b["isRuin"] = True
        kept = _add_test_structure(engine, condition=80.0)

        preview = engine.god_preview(_clear_ruins_envelope(structure_ids=[ruin_a["id"], ruin_b["id"]]))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"]["count"] == 2, preview["previewOutcome"])
        assert_true(set(preview["previewOutcome"]["structureIds"]) == {ruin_a["id"], ruin_b["id"]},
                    preview["previewOutcome"])

        applied = engine.god_apply(preview["previewId"], "req-clear-ruins")
        assert_true(applied["ok"], applied)
        remaining_ids = {s["id"] for s in engine.civilization["structures"]}
        assert_true(ruin_a["id"] not in remaining_ids and ruin_b["id"] not in remaining_ids,
                    remaining_ids)
        assert_true(kept["id"] in remaining_ids, remaining_ids)
        assert_true(owner["homeStructureId"] is None, owner)
        assert_true(applied["outcome"]["removed"] == preview["previewOutcome"]["count"],
                    (applied, preview))
        print("  OK clear_ruins preview/apply deletes selected ruins and clears homeStructureId")
    finally:
        se.GOD_MODE_ENABLED = old


def test_phase4_miracles_irreversible_and_refuse_cancellation():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        struct = _add_test_structure(engine, condition=50.0)

        envelopes_and_ids = []
        for label, env in (
            ("vitals", _vitals_envelope(agent["id"], health_delta=1)),
            ("grant", _grant_envelope("wood", 1)),
            ("structure", _structure_envelope(struct["id"], 1)),
        ):
            preview = engine.god_preview(env)
            assert_true(preview["ok"], (label, preview))
            assert_true(preview["reversibilityClass"] == "irreversible", (label, preview))
            applied = engine.god_apply(preview["previewId"], f"req-irrev-{label}")
            assert_true(applied["ok"], (label, applied))
            envelopes_and_ids.append((label, applied["interventionId"]))

        for label, intervention_id in envelopes_and_ids:
            cancelled = engine.god_cancel(intervention_id)
            assert_true(cancelled == {
                "ok": True, "cancelled": False, "reason": "nothing to cancel",
                "targetId": intervention_id,
            }, (label, cancelled))
            revoke_preview = engine.god_preview(_revoke_envelope(intervention_id))
            assert_true(revoke_preview["ok"], (label, revoke_preview))
            revoke_applied = engine.god_apply(revoke_preview["previewId"], f"req-revoke-{label}")
            assert_true(revoke_applied == {
                "ok": False, "reason": "guidance id not found or already inactive",
            }, (label, revoke_applied))
        print("  OK agent_vitals/grant_resource/structure_condition all report reversibilityClass "
              "'irreversible'; god_cancel and revoke_guidance both refuse to touch them")
    finally:
        se.GOD_MODE_ENABLED = old


def test_phase4_duplicate_request_and_expired_preview():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 50.0

        # Duplicate requestId applies once.
        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=5))
        resp1 = engine.god_apply(preview["previewId"], "req-dup-vitals")
        assert_true(resp1["ok"], resp1)
        assert_true(agent["health"] == 55.0, agent["health"])
        resp2 = engine.god_apply(preview["previewId"], "req-dup-vitals")
        assert_true(resp2 == resp1, (resp1, resp2))
        assert_true(agent["health"] == 55.0, "duplicate requestId must not re-apply the miracle")

        # Expired preview rejected.
        preview2 = engine.god_preview(_vitals_envelope(agent["id"], health_delta=5))
        engine._god_preview_cache[preview2["previewId"]]["expiresAt"] = time.time() - 1
        expired = engine.god_apply(preview2["previewId"], "req-expired-vitals")
        assert_true(expired == {"ok": False, "reason": "preview missing or expired"}, expired)
        assert_true(agent["health"] == 55.0, "an expired preview must never apply")
        print("  OK Phase 4 miracles: duplicate requestId applies once; expired preview rejected")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 5 tests: storyteller events and timed lawgiver modifiers
# (docs/plan-sovereign-god-mode-v2.md "Timed lawgiver modifiers" +
# "Exact consumer sites and arithmetic" + "Storyteller events") ---
