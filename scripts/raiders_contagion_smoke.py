"""Deterministic smoke harness for Raiders + Contagion (idea-05).

No LLM runtime (Ollama) required. Run:

    uv run python scripts/raiders_contagion_smoke.py
"""
from __future__ import annotations

import random
import re
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402


def _load_roles():
    import json
    with open(ROOT / "simulation" / "roles.json", encoding="utf-8") as fh:
        return json.load(fh)


def _build_resource_gather_roles(roles):
    out = {}
    for role, d in roles.items():
        for res in d.get("specialty") or []:
            out.setdefault(res, []).append(role)
    return {res: tuple(rs) for res, rs in out.items()}


def make_engine(roster_size=8):
    roles = _load_roles()
    role_primary = {
        role: d["specialty"][0] for role, d in roles.items() if d.get("specialty")
    }
    deps = {
        "ROLES": roles,
        "ROLE_PROJECT": {
            role: (d.get("preferredProject")[0]
                   if isinstance(d.get("preferredProject"), list)
                   else d.get("preferredProject"))
            for role, d in roles.items()
        },
        "ROLE_SKILLS": {role: d.get("skill", "helps") for role, d in roles.items()},
        "ROLE_PRIMARY_RESOURCE": role_primary,
        "RESOURCE_GATHER_ROLES": _build_resource_gather_roles(roles),
        "AVAILABLE_ACTIONS": [],
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "smoke"},
        "lm_complete": lambda *a, **k: None,
        "is_scaffold_text": lambda t: False,
        "memory_store": None,
        "log_activity": lambda *a, **k: None,
        "log_conversation": lambda *a, **k: None,
        "log_benchmark": lambda *a, **k: None,
        "validate_blueprint": lambda *a, **k: (False, "unused"),
        "canonical_effect_vector": lambda *a, **k: (),
    }
    try:
        import server  # noqa: E402
        deps["AVAILABLE_ACTIONS"] = list(server.DECISION_ACTIONS)
    except Exception:
        deps["AVAILABLE_ACTIONS"] = ["rest", "move_to_district", "trade_resource"]
    return se.SimEngine(deps, roster_size=roster_size)


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _add_structure(engine, *, condition=100.0, district_id="village_core",
                   type_="house", x=700.0, y=1500.0):
    c = engine.civilization
    sid = c["nextStructureId"]
    structure = {
        "id": sid, "type": type_, "x": x, "y": y,
        "visualStyle": "generic", "sprite": None,
        "name": f"Test {type_} {sid}", "districtId": district_id,
        "condition": condition, "isRuin": False,
        "homeOf": None, "level": 1, "visualTier": 1, "renderScale": 1.0,
    }
    c["structures"].append(structure)
    c["nextStructureId"] += 1
    return structure


def _find_role(engine, role):
    return next(a for a in engine.agents if a.get("role") == role)


def _find_non_elder(engine):
    return [a for a in engine.agents if a.get("role") != "elder"]


@contextmanager
def _patched_randint(value):
    orig = random.randint
    random.randint = lambda a, b: value
    try:
        yield
    finally:
        random.randint = orig


@contextmanager
def _patched_random(value):
    orig = random.random
    random.random = lambda: value
    try:
        yield
    finally:
        random.random = orig


@contextmanager
def _agent_dies_trap(engine):
    calls = []
    orig = engine._agent_dies

    def _trap(agent, cause="old age"):
        calls.append((agent.get("name"), cause))
        return orig(agent, cause=cause)

    engine._agent_dies = _trap
    try:
        yield calls
    finally:
        engine._agent_dies = orig


def _seed_raid_world(engine, *, wall=False, guards=0, victim_health=100):
    """Prepare village_core raid target with stockpile + district stocks + structure."""
    c = engine.civilization
    district_id = "village_core"
    engine._ensure_district_stocks()
    c.setdefault("stockpile", {})["wood"] = 50
    c["districtStocks"].setdefault(district_id, {})["wood"] = 50
    target = _add_structure(engine, district_id=district_id, x=700.0, y=1500.0)
    if wall:
        _add_structure(engine, district_id=district_id, type_="wall", x=710.0, y=1510.0)
    elder = _find_role(engine, "elder")
    victim = _find_non_elder(engine)[0]
    elder["health"] = 100
    elder["x"], elder["y"] = target["x"], target["y"]
    elder["currentDistrict"] = district_id
    victim["health"] = victim_health
    victim["x"], victim["y"] = target["x"], target["y"]
    victim["currentDistrict"] = district_id
    guard_agents = []
    for i, agent in enumerate(_find_non_elder(engine)[1:1 + guards]):
        agent["role"] = "guard"
        agent["incapacitated"] = False
        agent["x"] = target["x"] + i * 5
        agent["y"] = target["y"]
        agent["currentDistrict"] = district_id
        guard_agents.append(agent)
    return target, elder, victim


def _resolve_scheduled_raid(engine, target, randint_val=8):
    tele = engine._begin_raid_telegraph(
        target_district_id=target["districtId"],
        target_structure_id=target["id"],
    )
    assert_true(tele is not None, "raid telegraph missing")
    engine.frameTick = tele["impactFrame"]
    with _patched_randint(randint_val):
        engine._maybe_resolve_pressure_telegraph()
    return tele


def _raid_outcome(engine, district_id, target, elder, victim):
    c = engine.civilization
    stock = int(c.get("stockpile", {}).get("wood", 0))
    dstock = int(c["districtStocks"].get(district_id, {}).get("wood", 0))
    struct_cond = float(target.get("condition", 100))
    return {
        "stockpile_wood": stock,
        "district_wood": dstock,
        "structure_condition": struct_cond,
        "elder_health": elder.get("health"),
        "victim_health": victim.get("health"),
    }


def test_raid_unmitigated_stockpile_structure_contact():
    engine = make_engine(8)
    target, elder, victim = _seed_raid_world(engine, wall=False, guards=0)
    district_id = target["districtId"]
    stock_before = 50
    dstock_before = 50
    struct_before = 100.0
    with _agent_dies_trap(engine) as dies:
        with _patched_randint(se.RAID_RESOURCE_LOSS_MAX):
            _resolve_scheduled_raid(engine, target, randint_val=se.RAID_RESOURCE_LOSS_MAX)
    out = _raid_outcome(engine, district_id, target, elder, victim)
    stock_loss = stock_before - out["stockpile_wood"]
    dstock_loss = dstock_before - out["district_wood"]
    struct_loss = struct_before - out["structure_condition"]
    assert_true(se.RAID_RESOURCE_LOSS_MIN <= stock_loss <= se.RAID_RESOURCE_LOSS_MAX,
                f"stockpile loss {stock_loss}")
    assert_true(se.RAID_RESOURCE_LOSS_MIN <= dstock_loss <= se.RAID_RESOURCE_LOSS_MAX,
                f"district stock loss {dstock_loss}")
    assert_true(abs(struct_loss - se.RAID_STRUCTURE_DAMAGE) < 0.01,
                f"structure damage {struct_loss}")
    assert_true(out["victim_health"] == 100 - se.RAID_CONTACT_DAMAGE,
                f"victim health {out['victim_health']}")
    assert_true(out["elder_health"] == 100, f"elder damaged: {out['elder_health']}")
    assert_true(not dies, f"_agent_dies called: {dies}")
    print("  OK raid unmitigated: stockpile + districtStocks + structure + contact; elder safe")


def test_raid_guard_wall_mitigation():
    control = make_engine(8)
    target_c, _, victim_c = _seed_raid_world(control, wall=False, guards=0)
    with _patched_randint(se.RAID_RESOURCE_LOSS_MAX):
        _resolve_scheduled_raid(control, target_c, randint_val=se.RAID_RESOURCE_LOSS_MAX)
    control_out = _raid_outcome(control, "village_core", target_c, _find_role(control, "elder"), victim_c)

    guards_only = make_engine(8)
    target_g, _, _ = _seed_raid_world(guards_only, wall=False, guards=3)
    guard_mitigation = guards_only._resolve_raid_mitigation(
        target_g["x"], target_g["y"], "village_core")
    assert_true(abs(guard_mitigation - 0.45) < 0.001, guard_mitigation)

    wall_only = make_engine(8)
    target_w, _, _ = _seed_raid_world(wall_only, wall=True, guards=0)
    wall_mitigation = wall_only._resolve_raid_mitigation(
        target_w["x"], target_w["y"], "village_core")
    assert_true(abs(wall_mitigation - se.RAID_WALL_MITIGATION) < 0.001,
                wall_mitigation)

    mitigated = make_engine(8)
    target_m, _, victim_m = _seed_raid_world(mitigated, wall=True, guards=3)
    expected_mitigation = min(
        se.RAID_GUARD_MITIGATION_CAP,
        3 * se.RAID_GUARD_MITIGATION_PER_GUARD + se.RAID_WALL_MITIGATION,
    )
    assert_true(abs(expected_mitigation - 0.65) < 0.001, expected_mitigation)
    with _patched_randint(se.RAID_RESOURCE_LOSS_MAX):
        _resolve_scheduled_raid(mitigated, target_m, randint_val=se.RAID_RESOURCE_LOSS_MAX)
    mit_out = _raid_outcome(mitigated, "village_core", target_m, _find_role(mitigated, "elder"), victim_m)

    assert_true(mit_out["stockpile_wood"] > control_out["stockpile_wood"],
                f"stockpile: control={control_out['stockpile_wood']} mit={mit_out['stockpile_wood']}")
    assert_true(mit_out["district_wood"] > control_out["district_wood"],
                f"district: control={control_out['district_wood']} mit={mit_out['district_wood']}")
    assert_true(mit_out["structure_condition"] > control_out["structure_condition"],
                f"structure: control={control_out['structure_condition']} mit={mit_out['structure_condition']}")
    assert_true(mit_out["victim_health"] > control_out["victim_health"],
                f"contact: control={control_out['victim_health']} mit={mit_out['victim_health']}")
    print(f"  OK guard-only {guard_mitigation:.0%}, wall-only {wall_mitigation:.0%}, "
          f"combined ~{int(expected_mitigation * 100)}% reduces loss vs control")


def test_telegraph_and_state_echo():
    engine = make_engine(8)
    target = _add_structure(engine)
    engine.frameTick = 1200
    tele = engine._begin_raid_telegraph(
        target_district_id=target["districtId"],
        target_structure_id=target["id"],
    )
    assert_true(tele is not None, "telegraph")
    assert_true(tele["impactFrame"] == engine.frameTick + se.RAID_TELEGRAPH_LEAD_FRAMES,
                tele)
    snap = engine.snapshot()
    pt = snap.get("pressureTelegraph")
    assert_true(pt is not None, "snapshot pressureTelegraph")
    assert_true(pt.get("impactFrame") == tele["impactFrame"], pt)
    assert_true(isinstance(pt.get("targetStructureId"), int), pt)
    assert_true("targetAgentId" in pt and pt["targetAgentId"] is None, pt)
    payload = engine._build_think_payload(engine.agents[0])
    line = payload.get("pressure_warning_line")
    assert_true(line and "RAID WARNING" in line, line)
    assert_true(engine._pressure_telegraph() is not None, "telegraph cleared early")
    print("  OK telegraph: think line + /state pressureTelegraph before resolve")


def test_contagion_spread_elder_excluded():
    engine = make_engine(8)
    carrier = _find_non_elder(engine)[0]
    neighbor = _find_non_elder(engine)[1]
    elder = _find_role(engine, "elder")
    carrier["x"], carrier["y"] = 1000.0, 1200.0
    neighbor["x"], neighbor["y"] = 1030.0, 1200.0
    elder["x"], elder["y"] = 1060.0, 1200.0
    assert_true(se._dist(carrier["x"], carrier["y"], neighbor["x"], neighbor["y"])
                <= se.CONTAGION_SPREAD_RADIUS, "neighbor spacing")
    assert_true(se._dist(carrier["x"], carrier["y"], elder["x"], elder["y"])
                <= se.CONTAGION_SPREAD_RADIUS, "elder spacing")
    assert_true(engine._pick_contagion_patient_zero(preferred_agent_id=elder["id"]) is None,
                "elder as patient zero")
    assert_true(not engine._infect_agent(elder), "infect elder")
    engine._infect_agent(carrier, announce=False)
    old_prob = se.CONTAGION_TRANSMISSION_PROB
    se.CONTAGION_TRANSMISSION_PROB = 1.0
    engine.frameTick = se.GOODS_TICK_FRAMES
    try:
        with _agent_dies_trap(engine) as dies:
            engine._tick_contagion_infection()
        assert_true(neighbor.get("infected"), "neighbor not infected")
        assert_true(not elder.get("infected"), "elder infected")
        assert_true(not dies, dies)
    finally:
        se.CONTAGION_TRANSMISSION_PROB = old_prob
    print("  OK contagion spread infects neighbor; elder never target")


def test_contagion_health_loss_and_duration():
    engine = make_engine(8)
    agent = _find_non_elder(engine)[0]
    agent["health"] = 100
    engine._infect_agent(agent, announce=False)
    engine.frameTick = se.GOODS_TICK_FRAMES
    with _agent_dies_trap(engine) as dies:
        engine._tick_contagion_infection()
    assert_true(agent["health"] == 100 - se.CONTAGION_HEALTH_LOSS_PER_TICK_GATE,
                agent["health"])
    assert_true(not dies, dies)

    engine2 = make_engine(8)
    agent2 = _find_non_elder(engine2)[0]
    engine2._infect_agent(agent2, announce=False)
    agent2["infectionFrame"] = engine2.frameTick - se.CONTAGION_DURATION_FRAMES
    engine2.frameTick = se.GOODS_TICK_FRAMES * 2
    engine2._tick_contagion_infection()
    assert_true(not agent2.get("infected"), "infection should clear at duration cap")
    print("  OK contagion health loss per gate + duration cap clears infection")


def test_contagion_recovery_probabilities():
    engine = make_engine(8)
    patient = _find_non_elder(engine)[0]
    patient["currentDistrict"] = "village_core"
    patient["x"], patient["y"] = 700.0, 1500.0
    engine._infect_agent(patient, announce=False)

    control_prob = engine._agent_contagion_recovery_prob(patient)
    assert_true(abs(control_prob - 0.0) < 1e-9, control_prob)

    healer = _find_role(engine, "healer")
    healer["incapacitated"] = False
    healer["x"], healer["y"] = patient["x"] + 10, patient["y"]
    healer_prob = engine._agent_contagion_recovery_prob(patient)
    assert_true(abs(healer_prob - se.HEALER_RECOVERY_BONUS) < 1e-9, healer_prob)

    healer["x"], healer["y"] = 0.0, 0.0
    _add_structure(engine, district_id="village_core", type_="clinic", x=705.0, y=1505.0)
    clinic_prob = engine._agent_contagion_recovery_prob(patient)
    assert_true(abs(clinic_prob - se.CLINIC_RECOVERY_BONUS) < 1e-9, clinic_prob)

    healer["x"], healer["y"] = patient["x"] + 10, patient["y"]
    both_prob = engine._agent_contagion_recovery_prob(patient)
    expected_both = se.HEALER_RECOVERY_BONUS + se.CLINIC_RECOVERY_BONUS
    assert_true(abs(both_prob - expected_both) < 1e-9, both_prob)

    engine2 = make_engine(8)
    patient2 = _find_non_elder(engine2)[0]
    engine2._infect_agent(patient2, announce=False)
    engine2.frameTick = se.GOODS_TICK_FRAMES
    with _patched_random(0.0):
        engine2._tick_contagion_infection()
    assert_true(not patient2.get("infected"), "forced early recovery roll")
    print("  OK recovery prob 0 / 0.05 / 0.05 / 0.10 + forced recover roll")


def test_quarantine_governance():
    engine = make_engine(8)
    assert_true("quarantine" in se.RULE_KINDS, sorted(se.RULE_KINDS))
    district = "farm_north"
    rule = {
        "id": "smoke_quarantine",
        "kind": "quarantine",
        "name": "Smoke quarantine",
        "value": district,
        "description": "Restrict movement and trade across the farm boundary.",
    }
    elder = _find_role(engine, "elder")
    engine.apply_decision(elder, {
        "action": "propose_rule", "rule": rule, "reasoning": "contain illness",
    })
    pending = next(
        (r for r in engine.civilization["pendingRules"] if r.get("id") == rule["id"]),
        None,
    )
    assert_true(pending is not None, "quarantine proposal was not queued")
    for agent in engine.agents:
        pending["votes"].setdefault(agent["name"], "yes")
    engine._tally_and_maybe_enact(pending)
    assert_true(any(r.get("id") == rule["id"] for r in engine.civilization["rules"]),
                "quarantine proposal was not enacted")
    inside = _find_non_elder(engine)[0]
    outside = _find_non_elder(engine)[1]
    inside["currentDistrict"] = district
    outside["currentDistrict"] = "village_core"
    inside["x"], inside["y"] = 600.0, 400.0
    outside["x"], outside["y"] = 700.0, 1500.0
    inside["resources"]["wood"] = 5
    outside["resources"]["wood"] = 0

    assert_true(engine._quarantine_blocks_travel(inside, district, "village_core"),
                "outbound travel")
    assert_true(engine._quarantine_blocks_travel(outside, "village_core", district),
                "inbound travel")

    prior_tx = inside.get("targetX")
    engine.apply_decision(inside, {"action": "move_to_district", "target": "village_core"})
    assert_true(inside.get("currentDistrict") == district, "move out blocked")
    assert_true(inside.get("targetX") == prior_tx, "move out should not reroute")

    prior_tx2 = outside.get("targetX")
    engine.apply_decision(outside, {"action": "move_to_district", "target": district})
    assert_true(outside.get("currentDistrict") == "village_core", "move in blocked")
    assert_true(outside.get("targetX") == prior_tx2, "move in should not reroute")

    # Cross-district trade blocked
    inside["x"], inside["y"] = outside["x"], outside["y"]
    summary_block = engine.apply_decision(
        outside,
        {"action": "trade_resource", "target": inside["name"]},
    )
    assert_true("quarantine" in summary_block.lower(), summary_block)

    # Different quarantined districts are still different endpoints: either
    # active endpoint blocks cross-district trade, even when both are active.
    second_rule = {
        "id": "smoke_quarantine_core",
        "kind": "quarantine",
        "name": "Smoke core quarantine",
        "value": "village_core",
    }
    engine._apply_governance_rule(second_rule)
    summary_both = engine.apply_decision(
        outside,
        {"action": "trade_resource", "target": inside["name"]},
    )
    assert_true("quarantine" in summary_both.lower(), summary_both)
    engine._clear_governance_rule(second_rule)

    # Same-district trade allowed
    partner = _find_non_elder(engine)[2]
    partner["currentDistrict"] = district
    partner["x"], partner["y"] = inside["x"] + 1, inside["y"]
    partner["resources"]["wood"] = 0
    inside["resources"]["wood"] = 3
    summary_ok = engine.apply_decision(
        inside,
        {"action": "trade_resource", "target": partner["name"]},
    )
    assert_true("traded" in summary_ok.lower(), summary_ok)

    engine.apply_decision(elder, {
        "action": "repeal_rule", "target": rule["id"], "reasoning": "outbreak contained",
    })
    repeal = next(
        (r for r in engine.civilization["pendingRules"]
         if r.get("targetRuleId") == rule["id"]),
        None,
    )
    assert_true(repeal is not None, "quarantine repeal was not queued")
    for agent in engine.agents:
        repeal["votes"].setdefault(agent["name"], "yes")
    engine._tally_and_maybe_enact(repeal)
    assert_true(not engine._quarantine_blocks_travel(outside, "village_core", district),
                "repeal should lift travel block")
    assert_true(not engine._quarantine_blocks_trade(outside, inside),
                "repeal should lift cross-district trade block")
    print("  OK quarantine blocks either cross-district trade endpoint; repeal lifts it")


def test_quarantine_stops_preassigned_crossing():
    engine = make_engine(8)
    mover = _find_non_elder(engine)[0]
    mover["currentDistrict"] = "farm_north"
    mover["x"], mover["y"] = 700.0, 809.0
    mover["targetX"], mover["targetY"] = 700.0, 900.0
    mover["waypoints"] = []
    mover["speed"] = 20.0
    rule = {
        "id": "smoke_q_inflight",
        "kind": "quarantine",
        "name": "In-flight quarantine",
        "value": "farm_north",
    }

    # The route exists before enactment; the physical crossing guard must
    # cancel it at the boundary without moving or teleporting the agent.
    engine._apply_governance_rule(rule)
    engine._move_agent(mover)
    assert_true((mover["x"], mover["y"]) == (700.0, 809.0), mover)
    assert_true((mover["targetX"], mover["targetY"]) == (700.0, 809.0), mover)
    assert_true(mover.get("currentDistrict") == "farm_north", mover)

    engine._clear_governance_rule(rule)
    mover["targetX"], mover["targetY"] = 700.0, 900.0
    engine._move_agent(mover)
    assert_true(mover["y"] > 809.0, "repeal should restore physical crossing")
    print("  OK enacted-after-assignment quarantine stops crossing; repeal restores it")


def test_kill_switch_and_flag_echo():
    engine = make_engine(8)
    flags = engine.snapshot()["config"]["flags"]
    assert_true(flags.get("RAIDERS_CONTAGION_ENABLED") is True, flags)

    old = se.RAIDERS_CONTAGION_ENABLED
    try:
        se.RAIDERS_CONTAGION_ENABLED = False
        off_engine = make_engine(8)
        assert_true("quarantineActive" not in off_engine.civilization,
                    "flag-off fresh world seeded quarantineActive")
        assert_true("quarantineActiveBySettlement" not in off_engine.civilization,
                    "flag-off fresh world seeded quarantineActiveBySettlement")
        assert_true("mitigates" not in off_engine._get_structure_function("wall"),
                    "flag-off wall registered raid mitigation")
        assert_true(off_engine._begin_raid_telegraph() is None, "raid telegraph when off")
        assert_true(off_engine._begin_contagion_telegraph() is None, "contagion telegraph when off")
        assert_true(off_engine._pressure_telegraph() is None, "pressure telegraph when off")
    finally:
        se.RAIDERS_CONTAGION_ENABLED = old
    print("  OK kill switch + flag echo when enabled")


def test_sage_emergency_bypasses_quarantine():
    engine = make_engine(8)
    elder = _find_role(engine, "elder")
    healer = _find_role(engine, "healer")
    district = "farm_north"
    rule = {
        "id": "smoke_q_sage",
        "kind": "quarantine",
        "name": "Sage quarantine",
        "value": district,
    }
    engine._apply_governance_rule(rule)
    elder["currentDistrict"] = district
    elder["x"], elder["y"] = 600.0, 400.0
    # Start immediately outside the quarantined farm so the first movement
    # step exercises the physical boundary guard, not only route assignment.
    healer["currentDistrict"] = None
    healer["x"], healer["y"] = 600.0, 811.0
    assert_true(engine._quarantine_blocks_travel(healer, healer["currentDistrict"], district),
                "quarantine would block normal travel")
    prior_goal = healer.get("goal")
    engine._rush_to_heal(healer, elder)
    assert_true(healer.get("goal") is None, "goal cleared for rush")
    assert_true(
        abs(healer.get("targetX", 0) - elder["x"]) + abs(healer.get("targetY", 0) - elder["y"])
        < 200,
        "healer routed toward elder despite quarantine",
    )
    healer["speed"] = 30.0
    before = (healer["x"], healer["y"])
    engine._move_agent(healer)
    assert_true((healer["x"], healer["y"]) != before,
                "Sage-emergency physical crossing was blocked")
    assert_true(healer.get("_quarantineTravelBypass") is True,
                "Sage-emergency crossing marker missing in flight")
    healer["goal"] = prior_goal
    engine._clear_governance_rule(rule)
    print("  OK Sage-emergency _rush_to_heal bypasses quarantine routing")


def test_contagion_telegraph_think_line():
    engine = make_engine(8)
    patient = _find_non_elder(engine)[0]
    engine.frameTick = 500
    tele = engine._begin_contagion_telegraph(patient_zero_id=patient["id"])
    assert_true(tele is not None, tele)
    projected = engine._pressure_telegraph_snapshot()
    assert_true(isinstance(projected.get("targetAgentId"), int), projected)
    assert_true("targetStructureId" in projected and projected["targetStructureId"] is None,
                projected)
    payload = engine._build_think_payload(patient)
    line = payload.get("pressure_warning_line")
    assert_true(line and "CONTAGION WARNING" in line, line)
    print("  OK contagion telegraph pressure_warning_line")


def main():
    print("Raiders + contagion smoke (idea-05)")
    tests = [
        test_raid_unmitigated_stockpile_structure_contact,
        test_raid_guard_wall_mitigation,
        test_telegraph_and_state_echo,
        test_contagion_spread_elder_excluded,
        test_contagion_health_loss_and_duration,
        test_contagion_recovery_probabilities,
        test_quarantine_governance,
        test_quarantine_stops_preassigned_crossing,
        test_kill_switch_and_flag_echo,
        test_sage_emergency_bypasses_quarantine,
        test_contagion_telegraph_think_line,
    ]
    passed = 0
    for fn in tests:
        fn()
        passed += 1
    print(f"ALL PASS ({passed}/{len(tests)})")


if __name__ == "__main__":
    main()
