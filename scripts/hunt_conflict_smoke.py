"""Deterministic smoke harness for Hunt + Conflict (Phase C): hunt damage retune,
survival role precedence, forced hunt goals, bounded PvP, action-sync. No Ollama.

Run:

    uv run python scripts/hunt_conflict_smoke.py
"""
from __future__ import annotations

import ast
import re
import sys
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
        deps["AVAILABLE_ACTIONS"] = [
            "rest", "hunt_wildlife", "confront_agent", "switch_role",
        ]
    return se.SimEngine(deps, roster_size=roster_size)


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _clear_village_edibles(engine):
    c = engine.civilization
    for rid in se.EDIBLE_RESOURCES:
        c.setdefault("stockpile", {})[rid] = 0
    for agent in engine.agents:
        for rid in se.EDIBLE_RESOURCES:
            agent.setdefault("resources", {})[rid] = 0


def _deplete_gather_zones(engine):
    engine._ensure_district_stocks()
    for did, stocks in engine.civilization["districtStocks"].items():
        kind = engine.civilization["districts"][did].get("kind")
        if kind not in ("farm", "beach"):
            continue
        for rid in list(stocks.keys()):
            engine._set_district_stock(did, rid, 0)


def _strip_roles(engine, roles=("farmer", "fisher", "hunter")):
    role_set = set(roles)
    for agent in engine.agents:
        if agent["role"] in role_set:
            agent["role"] = "trader"


def _spawn_prey(engine, district_id, kind, x, y):
    cre = engine._make_wildlife_creature(district_id, kind, x=x, y=y)
    engine.civilization.setdefault("wildlife", []).append(cre)
    return cre


def _district_center(engine, district_id):
    b = engine.civilization["districts"][district_id]["bounds"]
    return (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2


def test_hunt_damage_constants_and_role_bonus():
    assert_true(se.HUNT_DAMAGE == 2, se.HUNT_DAMAGE)
    assert_true(se.HUNT_DAMAGE_HUNTER == 4, se.HUNT_DAMAGE_HUNTER)

    engine = make_engine(4)
    prey = _spawn_prey(engine, "forest", "deer", 1300, 280)
    assert_true(prey["hp"] == se.WILDLIFE_MAX_HP["deer"], prey["hp"])

    trader = next(a for a in engine.agents if a["role"] != "hunter")
    trader["role"] = "trader"
    trader["x"], trader["y"] = 1295, 275
    result = engine._apply_hunt_damage(trader, prey)
    assert_true(result.get("ok") and result.get("damage") == 2, result)
    assert_true(prey["hp"] == 2, prey["hp"])

    hunter = engine.agents[0]
    hunter["role"] = "hunter"
    hunter["x"], hunter["y"] = 1295, 275
    result = engine._apply_hunt_damage(hunter, prey)
    assert_true(result.get("ok") and result.get("damage") == 4, result)
    assert_true(result.get("killed") and result.get("resource") == "meat", result)
    assert_true(hunter["resources"].get("meat", 0) >= 1 or
                engine.civilization["stockpile"].get("meat", 0) >= 1, result)
    print("  OK HUNT_DAMAGE=2, HUNT_DAMAGE_HUNTER=4; hunter one-shots 4hp deer")


def test_survival_precedence_hunter_over_farmer():
    """Farmer/fisher unfilled, village edibles scarce, gather zones barren,
    wildlife present -> hunter (not farmer), matching specs/08 precedence."""
    engine = make_engine(8)
    _strip_roles(engine)
    _clear_village_edibles(engine)
    _deplete_gather_zones(engine)

    for agent in engine.agents[:2]:
        agent["hunger"] = se.STARVING_HUNGER

    _spawn_prey(engine, "forest", "deer", 1300, 280)

    assert_true(not engine._role_is_filled("farmer"), "farmer should be unfilled")
    assert_true(not engine._role_is_filled("fisher"), "fisher should be unfilled")
    assert_true(not engine._role_is_filled("hunter"), "hunter should be unfilled")
    assert_true(engine._edible_scarce("food"), "food should be scarce")
    assert_true(engine._gather_failing("food"), "farm gather should be failing")
    assert_true(engine._gather_failing("fish"), "fish gather should be failing")
    assert_true(engine._wildlife_present(), "wildlife should be present")
    assert_true(engine._meat_scarce(), "meat should be scarce")

    needed = engine._village_needed_role()
    assert_true(needed == "hunter", f"expected hunter precedence, got {needed!r}")
    print(f"  OK survival precedence -> {needed} (not farmer)")


def test_forced_hunt_when_prey_nearer_than_gather():
    engine = make_engine(4)
    agent = engine.agents[0]
    agent["hunger"] = se.STARVING_HUNGER
    agent["goal"] = None
    for rid in se.EDIBLE_RESOURCES:
        agent["resources"][rid] = 0

    fx, fy = _district_center(engine, "forest")
    agent["x"], agent["y"] = fx, fy
    prey = _spawn_prey(engine, "forest", "rabbit", fx + 12, fy + 5)

    gather_dist = engine._nearest_gather_edible_distance(agent)
    prey_dist = se._dist(agent["x"], agent["y"], prey["x"], prey["y"])
    assert_true(gather_dist is not None and prey_dist < gather_dist,
                (gather_dist, prey_dist))

    engine._maybe_forced_hunt()
    goal = agent.get("goal") or {}
    assert_true(goal.get("kind") == "hunt" and goal.get("target") == prey["id"], goal)
    print("  OK forced hunt assigns goal when prey nearer than gather")


def test_forced_hunt_skipped_when_gather_nearer():
    engine = make_engine(4)
    agent = engine.agents[0]
    agent["hunger"] = se.STARVING_HUNGER
    agent["goal"] = None
    for rid in se.EDIBLE_RESOURCES:
        agent["resources"][rid] = 0

    fx, fy = _district_center(engine, "farm_north")
    agent["x"], agent["y"] = fx, fy
    prey = _spawn_prey(engine, "farm_north", "rabbit", fx + 40, fy + 10)

    gather_dist = engine._nearest_gather_edible_distance(agent)
    prey_dist_val = se._dist(agent["x"], agent["y"], prey["x"], prey["y"])
    assert_true(gather_dist is not None and prey_dist_val <= se.HUNT_RADIUS,
                (gather_dist, prey_dist_val, se.HUNT_RADIUS))
    assert_true(gather_dist < prey_dist_val, (gather_dist, prey_dist_val))

    engine._maybe_forced_hunt()
    goal = agent.get("goal")
    assert_true(not goal or goal.get("kind") != "hunt", goal)
    print("  OK forced hunt skipped when gather edible is closer")


def test_confront_agent_allowed_and_rejected():
    engine = make_engine(8)
    non_elder = [a for a in engine.agents if a["role"] != "elder"]
    assert_true(len(non_elder) >= 4, len(non_elder))
    actor, target, friendly, victim = non_elder[0], non_elder[1], non_elder[2], non_elder[3]
    elder = next(a for a in engine.agents if a["role"] == "elder")

    actor["x"], actor["y"] = 700.0, 1500.0
    target["x"], target["y"] = 740.0, 1500.0
    target["health"] = 80
    target["resources"]["food"] = 5
    actor["relationships"][target["name"]] = "rival"

    summary = engine.apply_decision(actor, {
        "action": "confront_agent",
        "target": target["name"],
        "reasoning": "smoke rival confront",
    })
    assert_true("confronted" in summary.lower(), summary)
    assert_true(target["health"] < 80, target["health"])
    assert_true(actor["resources"].get("food", 0) >= 1, actor["resources"])

    friendly["x"], friendly["y"] = 800.0, 1500.0
    victim["x"], victim["y"] = 840.0, 1500.0
    friendly["relationships"][victim["name"]] = "neutral"
    friendly.pop("lastNightNote", None)
    reject = engine.apply_decision(friendly, {
        "action": "confront_agent",
        "target": victim["name"],
        "reasoning": "smoke friendly confront",
    })
    assert_true("no cause" in reject.lower(), reject)

    actor2 = non_elder[0]
    actor2["x"], actor2["y"] = elder["x"] + 10, elder["y"]
    sage_reject = engine.apply_decision(actor2, {
        "action": "confront_agent",
        "target": elder["name"],
        "reasoning": "smoke sage confront",
    })
    assert_true("elder" in sage_reject.lower(), sage_reject)

    actor3 = non_elder[1]
    target3 = non_elder[2]
    actor3["x"], actor3["y"] = 900.0, 1500.0
    target3["x"], target3["y"] = 940.0, 1500.0
    actor3["relationships"][target3["name"]] = "rival"
    pair_key = engine._confront_pair_key(actor3["id"], target3["id"])
    engine.civilization.setdefault("confrontCooldowns", {})[pair_key] = (
        engine.frameTick + se.CONFRONT_COOLDOWN_FRAMES)
    cooldown_reject = engine.apply_decision(actor3, {
        "action": "confront_agent",
        "target": target3["name"],
        "reasoning": "smoke cooldown confront",
    })
    assert_true("wait" in cooldown_reject.lower(), cooldown_reject)
    print("  OK confront allowed for rival; rejected friendly/Sage/cooldown")


def test_confront_available_actions_gating():
    engine = make_engine(8)
    non_elder = [a for a in engine.agents if a["role"] != "elder"]
    actor, target = non_elder[0], non_elder[1]
    actor["x"], actor["y"] = 700.0, 1500.0
    target["x"], target["y"] = 740.0, 1500.0
    actor["relationships"][target["name"]] = "rival"

    payload = engine._build_think_payload(actor)
    offered = set(payload.get("available_actions") or [])
    assert_true("confront_agent" in offered, sorted(offered))

    actor["relationships"][target["name"]] = "neutral"
    actor.pop("lastNightNote", None)
    payload2 = engine._build_think_payload(actor)
    offered2 = set(payload2.get("available_actions") or [])
    assert_true("confront_agent" not in offered2, sorted(offered2))
    print("  OK confront_agent gated in available_actions by rivalry/pressure")


def test_confront_agent_action_sync():
    action = "confront_agent"
    server_source = (ROOT / "simulation" / "server.py").read_text(encoding="utf-8")
    # sim_engine.py was split into a package under simulation/sim_engine/
    # (Phase 6a) and viewer.js into ordered files under simulation/viewer/
    # (see index.html's <script> tag order and specs/11-viewer.md);
    # concatenate each for this source-scan check (order doesn't matter here).
    engine_source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "simulation" / "sim_engine").glob("*.py"))
    )
    viewer_source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((ROOT / "simulation" / "viewer").glob("*.js"))
    )
    prompts_source = (ROOT / "simulation" / "prompts.py").read_text(encoding="utf-8")

    tree = ast.parse(server_source)
    action_names = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DECISION_ACTIONS" for t in node.targets):
            action_names = ast.literal_eval(node.value)
            break
    assert_true(action_names is not None and action in action_names, action_names)
    assert_true('"action": {"type": "string", "enum": DECISION_ACTIONS}' in server_source,
                "DECISION_SCHEMA enum")
    assert_true(action in prompts_source, "prompts mention confront_agent")
    assert_true(f'elif action == "{action}"' in engine_source, "apply_decision branch")
    assert_true(f'action_name != "{action}"' in engine_source, "available_actions gate")
    assert_true(f"{action}:" in viewer_source, "ACTION_LABELS entry")
    print(f"  OK action-sync checklist for {action}")


def main():
    print("Hunt + conflict smoke (Phase C)")
    test_hunt_damage_constants_and_role_bonus()
    test_survival_precedence_hunter_over_farmer()
    test_forced_hunt_when_prey_nearer_than_gather()
    test_forced_hunt_skipped_when_gather_nearer()
    test_confront_agent_allowed_and_rejected()
    test_confront_available_actions_gating()
    test_confront_agent_action_sync()
    print("ALL PASS")


if __name__ == "__main__":
    main()
