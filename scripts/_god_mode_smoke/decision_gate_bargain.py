# Divine Matrix Phase 5/6 -- decision gate/possession pipeline, Burning Bush, anoint/Identity Forge, Merovingian Bargain.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_decision_compulsion_forces_pinned_action():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_decision_compulsion_envelope(
            agent["id"], action="rest", remainingTurns=2))
        assert_true(preview.get("ok"), preview)
        engine.god_apply(preview["previewId"], "req-compulsion-1")
        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "collect_resource", "reasoning": "would gather",
            })
        assert_true(agent.get("lastAction") == "rest", agent)
        conv = [e for e in engine.conversationLog if e.get("source") == "divine"]
        assert_true(any("compulsion" in (e.get("message") or "").lower() for e in conv), conv)
        print("  OK decision_compulsion replaces LLM-path decision with pinned rest")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_possession_skips_llm():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        calls = {"n": 0}
        engine.d["llm_decide"] = lambda payload: calls.__setitem__("n", calls["n"] + 1) or {
            "action": "collect_resource", "reasoning": "smoke llm",
        }
        preview = engine.god_preview(_agent_possession_envelope(agent["id"], duration=5000))
        assert_true(preview.get("ok"), preview)
        engine.god_apply(preview["previewId"], "req-possession-1")
        engine._think_job(agent["name"])
        assert_true(calls["n"] == 0, calls)
        assert_true(agent.get("lastAction") == "rest", agent)
        bench = engine.lastBenchmarks or {}
        assert_true(engine.lastBenchmarks.get("divine_possession_skip") == 1.0
                    or any(
                        (engine.civilization.get("godState") or {}).get("decisionGates"),
                    ), agent)
        print("  OK agent_possession skips llm_decide and applies pinned action")
    finally:
        se.GOD_MODE_ENABLED = old


def test_veto_hold_and_resolve():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_decision_veto_arm_envelope(agent["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-veto-arm-1")
        with engine.lock:
            held = engine._apply_gated_decision(agent, {
                "action": "rest", "reasoning": "candidate",
            })
        assert_true(held is False, held)
        assert_true(agent.get("divineHold"), agent)
        gate = engine._god_active_decision_gate_record(agent["id"])
        assert_true(gate.get("status") == "holding", gate)
        assert_true(gate.get("pendingDecision", {}).get("action") == "rest", gate)
        resolve_preview = engine.god_preview(_decision_veto_resolve_envelope(
            agent["id"], "reject"))
        applied = engine.god_apply(resolve_preview["previewId"], "req-veto-resolve-1")
        assert_true(applied.get("ok"), applied)
        assert_true(not agent.get("divineHold"), agent)
        assert_true(engine._god_active_decision_gate_record(agent["id"]) is None, agent)
        digests = engine.civilization["godState"].get("decisionDigests") or []
        assert_true(len(digests) == 0, "veto_resolve must not append decisionDigests")
        print("  OK veto arms, holds candidate, resolve clears hold and applies")
    finally:
        se.GOD_MODE_ENABLED = old


def test_sage_emergency_bypasses_decision_gate():
    old = se.GOD_MODE_ENABLED
    old_survival = se.SURVIVAL_ENABLED
    se.GOD_MODE_ENABLED = True
    se.SURVIVAL_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        elder = next((a for a in engine.agents if a["role"] == "elder"), None)
        responder = next((a for a in engine.agents if a["role"] != "elder"), engine.agents[0])
        if elder is None:
            elder = engine.agents[0]
            elder["role"] = "elder"
        preview = engine.god_preview(_agent_possession_envelope(responder["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-sage-bypass-possession")
        elder["incapacitated"] = True
        elder["health"] = 5.0
        with engine.lock:
            engine._rush_to_heal(responder, elder)
        assert_true(responder.get("lastAction") == "heal_agent", responder)
        print("  OK Sage _rush_to_heal bypasses possession gate")
    finally:
        se.GOD_MODE_ENABLED = old
        se.SURVIVAL_ENABLED = old_survival


def test_veto_hold_cap():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition(roster_size=5)
        agents = engine.agents[:4]
        for i, agent in enumerate(agents[:3]):
            preview = engine.god_preview(_decision_veto_arm_envelope(agent["id"], duration=5000))
            engine.god_apply(preview["previewId"], f"req-veto-cap-arm-{i}")
            with engine.lock:
                engine._apply_gated_decision(agent, {"action": "rest"})
        assert_true(engine._god_veto_hold_count() == se.GOD_VETO_HOLD_CAP, engine._god_veto_hold_count())
        fourth = agents[3]
        preview = engine.god_preview(_decision_veto_arm_envelope(fourth["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-veto-cap-arm-4")
        with engine.lock:
            engine._apply_gated_decision(fourth, {"action": "collect_resource"})
        assert_true(fourth.get("lastAction") == "rest", fourth)
        print("  OK concurrent veto holds capped at GOD_VETO_HOLD_CAP")
    finally:
        se.GOD_MODE_ENABLED = old


def test_decision_gate_privacy_and_sight():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_agent_possession_envelope(agent["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-gate-privacy")
        snap = engine.snapshot()
        assert_true("decisionGates" not in json.dumps(snap.get("god") or {}), snap)
        assert_true(all(r.get("kind") != "agent_possession"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "agent_possession leaked into recentPublicInterventions")
        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == agent["id"])
        assert_true(sight_agent.get("decisionGate", {}).get("mode") == "possession", sight_agent)
        assert_true(sight_agent.get("decisionGate", {}).get("pinnedAction") == "rest", sight_agent)
        print("  OK decisionGates absent from /state; Sight shows gate status summary")
    finally:
        se.GOD_MODE_ENABLED = old


def test_burning_bush_message_target_only_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need two agents")
        target, other = agents[0], agents[1]
        secret = "The bush speaks only to you."
        preview = engine.god_preview(_burning_bush_message_envelope(target["id"], secret))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bush-1")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(secret not in dumped, "burning bush text leaked into /state")
        assert_true("burningBush" not in dumped, "burningBush map leaked into /state")
        assert_true(all(r.get("kind") != "burning_bush_message"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "burning_bush_message leaked into recentPublicInterventions")

        target_line = engine._burning_bush_prompt_line(target)
        other_line = engine._burning_bush_prompt_line(other)
        assert_true(target_line and secret in target_line, target_line)
        assert_true(other_line is None, other_line)

        payload = engine._build_think_payload(target)
        assert_true(secret in (payload.get("divine_burning_bush_line") or ""), payload)
        other_payload = engine._build_think_payload(other)
        assert_true(other_payload.get("divine_burning_bush_line") is None, other_payload)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        assert_true(sight_target.get("burningBush", {}).get("active") is True, sight_target)
        assert_true(sight_target.get("burningBush", {}).get("messageCount") == 1, sight_target)
        assert_true(secret not in json.dumps(sight_target), "thread text leaked into Sight")
        print("  OK burning_bush_message appears only in target prompt; no /state leak")
    finally:
        se.GOD_MODE_ENABLED = old


def test_anoint_destiny_stigmata_oracle_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need two agents")
        target, neighbor = agents[0], agents[1]
        target["x"], target["y"] = 100.0, 100.0
        neighbor["x"], neighbor["y"] = 105.0, 100.0

        destiny = "You will unite the village."
        stigma = "golden aura"
        oracle_now = "The river runs backward."
        oracle_later = "A crown awaits."
        ft = engine.frameTick
        preview = engine.god_preview(_anoint_envelope(
            target["id"], destiny,
            stigmataTags=[stigma],
            oracleHints=[
                {"text": oracle_now, "revealFrame": ft},
                {"text": oracle_later, "revealFrame": ft + 5000},
            ],
            durationFrames=9000,
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-anoint-1")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(destiny not in dumped, "destiny leaked into /state")
        assert_true(oracle_now not in dumped, "oracle leaked into /state")
        assert_true("anointments" not in dumped, "anointments map leaked into /state")
        assert_true(all(r.get("kind") != "anoint"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "anoint leaked into recentPublicInterventions")

        target_line = engine._anointment_prompt_line(target)
        assert_true(target_line and destiny in target_line, target_line)
        assert_true(oracle_now in target_line, target_line)
        assert_true(oracle_later not in target_line, target_line)

        neighbor_payload = engine._build_think_payload(neighbor)
        nearby = neighbor_payload.get("nearby_agents") or []
        target_near = next((n for n in nearby if n.get("name") == target["name"]), None)
        assert_true(target_near is not None, nearby)
        assert_true(stigma in (target_near.get("stigmata") or []), target_near)

        from server import format_nearby_agents, build_user_prompt  # noqa: E402
        formatted = format_nearby_agents(nearby)
        assert_true(stigma in formatted, formatted)
        assert_true(destiny not in formatted, formatted)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        anoint_status = sight_target.get("anointment") or {}
        assert_true(anoint_status.get("active") is True, sight_target)
        assert_true(anoint_status.get("tagCount") == 1, sight_target)
        assert_true(anoint_status.get("nextOracleFrame") == ft + 5000, sight_target)
        assert_true(destiny not in json.dumps(sight_target), "destiny leaked into Sight")

        revoke_preview = engine.god_preview(_revoke_anoint_envelope(target["id"]))
        assert_true(revoke_preview.get("ok"), revoke_preview)
        revoked = engine.god_apply(revoke_preview["previewId"], "req-anoint-revoke")
        assert_true(revoked.get("ok"), revoked)
        assert_true(engine._anointment_prompt_line(target) is None, "anoint not cleared")
        neighbor_after = engine._build_think_payload(neighbor)
        nearby_after = neighbor_after.get("nearby_agents") or []
        target_near_after = next((n for n in nearby_after if n.get("name") == target["name"]), None)
        assert_true(target_near_after is not None, nearby_after)
        assert_true(not target_near_after.get("stigmata"), target_near_after)

        print("  OK anoint destiny/oracle private; stigmata in neighbor prompt; revoke clears")
    finally:
        se.GOD_MODE_ENABLED = old
def test_identity_forge_edit_copy_cancel_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine(roster_size=4)
        roles = _load_roles()
        target = engine.agents[0]
        source = engine.agents[1]
        original_role = target["role"]
        original_personality = target["personality"]
        new_role = next(r for r in roles if r != original_role)

        preview = engine.god_preview(_identity_edit_envelope(
            target["id"], role=new_role, personality="divine-forged personality"))
        assert_true(preview.get("ok"), preview)
        outcome = preview.get("previewOutcome") or {}
        assert_true(outcome.get("newRoleSkill") == roles[new_role].get("skill", "helps"),
                    outcome)
        if original_role == "elder" or new_role == "elder":
            assert_true(outcome.get("warning"), outcome)

        applied = engine.god_apply(preview["previewId"], "req-identity-edit")
        assert_true(applied.get("ok"), applied)
        assert_true(target["role"] == new_role, target)
        assert_true(target["personality"] == "divine-forged personality", target)

        payload = engine._build_think_payload(target)
        assert_true(payload.get("role") == new_role, payload)
        assert_true(payload.get("role_skill") == engine.d["ROLE_SKILLS"].get(new_role),
                    payload)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("identityForges" not in dumped, "identityForges leaked into /state")
        assert_true(all(r.get("kind") != "identity_edit"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "identity_edit leaked into recentPublicInterventions")

        source["persona"] = "SOURCE PERSONA LINE"
        source["personality"] = "SOURCE PERSONALITY TRAIT"
        target["persona"] = "TARGET START"
        target["personality"] = "TARGET START PERSONALITY"

        copy_preview = engine.god_preview(_identity_copy_envelope(
            target["id"], source["id"], rate=0.25))
        assert_true(copy_preview.get("ok"), copy_preview)
        copy_applied = engine.god_apply(copy_preview["previewId"], "req-identity-copy")
        assert_true(copy_applied.get("ok"), copy_applied)

        forge = engine.civilization["godState"]["identityForges"][str(target["id"])]
        assert_true(forge.get("progress", 0) > 0, forge)
        progress_after_apply = forge.get("progress")

        with engine.lock:
            engine._finish_think_identity_forge(target)
        progress_after_think = engine.civilization["godState"]["identityForges"][
            str(target["id"])].get("progress")
        assert_true(progress_after_think > progress_after_apply, forge)
        assert_true(target["personality"] != "TARGET START PERSONALITY", target)

        for _ in range(3):
            with engine.lock:
                engine._finish_think_identity_forge(target)
        assert_true(target["personality"] == source["personality"], target)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        forge_status = sight_target.get("identityForge") or {}
        assert_true(forge_status.get("active") is True, sight_target)
        assert_true(forge_status.get("copyFromId") == source["id"], sight_target)
        assert_true(forge_status.get("progress") == 1.0, sight_target)
        assert_true("SOURCE PERSONALITY" not in json.dumps(sight_target),
                    "personality leaked into Sight")

        cancel_preview = engine.god_preview(_identity_forge_cancel_envelope(target["id"]))
        assert_true(cancel_preview.get("ok"), cancel_preview)
        cancelled = engine.god_apply(cancel_preview["previewId"], "req-identity-cancel")
        assert_true(cancelled.get("ok"), cancelled)
        assert_true(target["role"] == original_role, target)
        assert_true(target["personality"] == original_personality, target)

        print("  OK identity edit role_skill; copy progresses; cancel restores; privacy")
    finally:
        se.GOD_MODE_ENABLED = old


def test_bargain_success_grants_resource():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        resource_id = "wood"
        held_before = agent["resources"].get(resource_id, 0)
        agent["resources"][resource_id] = held_before + 3
        preview = engine.god_preview(_merovingian_bargain_envelope(
            agent["id"],
            "Bring wood and be rewarded.",
            {"kind": "agent_has_resource", "resourceId": resource_id, "amount": 3},
            durationFrames=5000,
            rewardPrimitive={
                "kind": "grant_resource",
                "payload": {
                    "resourceId": resource_id,
                    "amount": 7,
                    "target": {"agentId": agent["id"]},
                },
            },
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bargain-success")
        assert_true(applied.get("ok"), applied)

        engine._tick_divine_bargains()
        bargain = engine.civilization["godState"]["burningBush"][str(agent["id"])]["bargain"]
        assert_true(bargain.get("status") == "success", bargain)
        assert_true(agent["resources"].get(resource_id, 0) >= held_before + 3 + 7,
                    agent["resources"])
        print("  OK bargain success predicate grants resource via tick settler")
    finally:
        se.GOD_MODE_ENABLED = old


def test_bargain_expiry_settles_failure():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        health_before = agent["health"]
        preview = engine.god_preview(_merovingian_bargain_envelope(
            agent["id"],
            "Fail if you do not comply in time.",
            {"kind": "frame_reached", "frame": engine.frameTick + 99999},
            durationFrames=10,
            punishPrimitive={
                "kind": "agent_vitals",
                "payload": {"targetId": agent["id"], "healthDelta": -5, "hungerDelta": 0},
            },
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bargain-expiry")
        assert_true(applied.get("ok"), applied)

        engine.frameTick += 301
        engine._expire_divine_effects()
        bargain = engine.civilization["godState"]["burningBush"][str(agent["id"])]["bargain"]
        assert_true(bargain.get("status") == "failure", bargain)
        assert_true(bargain.get("settleTrigger") == "expiry", bargain)
        assert_true(agent["health"] < health_before, (health_before, agent["health"]))
        print("  OK bargain expiry settles failure path with punish primitive")
    finally:
        se.GOD_MODE_ENABLED = old
