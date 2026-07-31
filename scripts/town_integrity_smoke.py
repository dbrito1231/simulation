"""Deterministic smoke harness for Town Integrity (Phase A): decay retune,
repair campaigns, ruin cull, disaster constants. No Ollama, no live state.db.

Run:

    uv run python scripts/town_integrity_smoke.py
"""
from __future__ import annotations

import random
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


def make_engine(roster_size=4):
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
        "RESOURCE_GATHER_ROLES": {},
        "AVAILABLE_ACTIONS": ["rest", "repair_structure"],
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
    return se.SimEngine(deps, roster_size=roster_size)


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _add_structure(engine, *, condition=100.0, is_ruin=False, ruined_since=None,
                   district_id=None, type_="house"):
    c = engine.civilization
    if district_id is None:
        district_id = next(iter(c["districts"]))
    sid = c["nextStructureId"]
    structure = {
        "id": sid, "type": type_, "x": 0, "y": 0,
        "visualStyle": "generic", "sprite": None,
        "name": f"Test {type_} {sid}", "districtId": district_id,
        "condition": condition, "isRuin": is_ruin,
        "homeOf": None, "level": 1, "visualTier": 1, "renderScale": 1.0,
    }
    if is_ruin:
        structure["ruinedSinceFrame"] = ruined_since if ruined_since is not None else 0
    c["structures"].append(structure)
    c["nextStructureId"] += 1
    return structure


def test_decay_constants_and_per_tick_math():
    assert_true(se.STRUCTURE_DECAY_PER_GOODS_TICK == 0.025,
                f"expected decay 0.025, got {se.STRUCTURE_DECAY_PER_GOODS_TICK}")
    assert_true(se.STRUCTURE_DISREPAIR_THRESHOLD == 30, se.STRUCTURE_DISREPAIR_THRESHOLD)
    assert_true(se.REPAIR_CONDITION_RESTORE == 50, se.REPAIR_CONDITION_RESTORE)

    ticks_to_disrepair = (100 - se.STRUCTURE_DISREPAIR_THRESHOLD) / se.STRUCTURE_DECAY_PER_GOODS_TICK
    ticks_to_ruin = 100 / se.STRUCTURE_DECAY_PER_GOODS_TICK
    assert_true(abs(ticks_to_disrepair - 2800) < 0.01, ticks_to_disrepair)
    assert_true(abs(ticks_to_ruin - 4000) < 0.01, ticks_to_ruin)

    # ~23.3h / ~33.3h real time at 30 ticks/s and GOODS_TICK_FRAMES cadence.
    sec_per_goods_tick = se.GOODS_TICK_FRAMES / 30
    hours_to_disrepair = ticks_to_disrepair * sec_per_goods_tick / 3600
    hours_to_ruin = ticks_to_ruin * sec_per_goods_tick / 3600
    assert_true(23.0 <= hours_to_disrepair <= 24.0, hours_to_disrepair)
    assert_true(33.0 <= hours_to_ruin <= 34.0, hours_to_ruin)

    engine = make_engine(2)
    s = _add_structure(engine, condition=100.0)
    engine._tick_structure_decay()
    assert_true(abs(s["condition"] - 99.975) < 1e-9, s["condition"])
    print(f"  OK decay 0.025/tick; ~{hours_to_disrepair:.1f}h to disrepair, "
          f"~{hours_to_ruin:.1f}h to ruin")


def test_repair_campaign_assigns_under_pressure_and_funded_repair_recovers():
    engine = make_engine(4)
    c = engine.civilization
    # 15 disrepaired + 5 working => working fraction 0.25 < 0.5 triggers pressure.
    for _ in range(15):
        _add_structure(engine, condition=20.0)
    for _ in range(5):
        _add_structure(engine, condition=80.0)
    assert_true(engine._village_repair_pressure(), "expected repair pressure")

    c["stockpile"]["wood"] = 500
    for agent in engine.agents:
        agent["resources"]["wood"] = 0

    engine._maybe_repair_campaign()
    repair_goals = [
        a for a in engine.agents
        if (a.get("goal") or {}).get("kind") == "repair"
    ]
    assert_true(1 <= len(repair_goals) <= se.REPAIR_CAMPAIGN_MAX_ASSIGN, repair_goals)

    target_id = repair_goals[0]["goal"]["target"]
    target = next(s for s in c["structures"] if s["id"] == target_id)
    before_cond = target["condition"]
    agent = repair_goals[0]
    summary = engine._repair_structure(agent, target_id)
    assert_true("lacks" not in summary and "nothing" not in summary, summary)
    assert_true(target["condition"] > before_cond, (before_cond, target["condition"]))
    assert_true(not target.get("isRuin"), target)
    print(f"  OK repair campaign assigned {len(repair_goals)} goal(s); "
          f"funded repair restored condition {before_cond}->{target['condition']}")


def test_cull_removes_aged_unaffordable_ruins_and_clears_references():
    engine = make_engine(4)
    c = engine.civilization
    engine.frameTick = se.RUIN_CULL_AGE_FRAMES + 500

    owner = engine.agents[0]
    ruins = []
    for i in range(4):
        r = _add_structure(engine, condition=0.0, is_ruin=True, ruined_since=0,
                           type_="decoration" if i else "house")
        ruins.append(r)
    owner["homeStructureId"] = ruins[0]["id"]
    ruins[0]["homeOf"] = owner["name"]

    for _ in range(16):
        _add_structure(engine, condition=90.0)

    c["reorgTasks"] = [
        {"structureId": ruins[1]["id"], "kind": "relocate"},
        {"structureId": 99999, "kind": "noop"},
    ]
    c["stockpile"] = {res: 0 for res in c["stockpile"]}
    for agent in engine.agents:
        agent["resources"] = {res: 0 for res in agent.get("resources", {})}

    before_total = len(c["structures"])
    assert_true(engine._village_repair_pressure(), "ruin ratio should trigger pressure")
    assert_true(not engine._village_can_afford_any_rebuild(), "cull requires unaffordable rebuild")

    engine._maybe_cull_ruins()
    after_total = len(c["structures"])
    removed = before_total - after_total
    assert_true(se.RUIN_CULL_MIN_PER_CALL <= removed <= se.RUIN_CULL_MAX_PER_CALL, removed)

    remaining_ruin_ids = {s["id"] for s in c["structures"] if s.get("isRuin")}
    culled_ids = {r["id"] for r in ruins} - remaining_ruin_ids
    assert_true(culled_ids, "expected at least one ruin culled")

    if ruins[0]["id"] in culled_ids:
        assert_true(owner["homeStructureId"] is None, owner)
    assert_true(all(t.get("structureId") not in culled_ids for t in c["reorgTasks"]),
                c["reorgTasks"])
    print(f"  OK cull removed {removed} aged unaffordable ruin(s); "
          f"homeStructureId/reorgTasks references cleared")


def test_disaster_constants_and_off_weather_rate():
    assert_true(se.DISASTER_PROB == 0.002, se.DISASTER_PROB)
    assert_true(se.DISASTER_DAMAGE == (30, 55), se.DISASTER_DAMAGE)

    old_weather = se.WEATHER_ENABLED
    se.WEATHER_ENABLED = False
    try:
        engine = make_engine(2)
        s = _add_structure(engine, condition=100.0)
        trials = 100_000
        random.seed(20260731)
        hits = 0
        for _ in range(trials):
            s["condition"] = 100.0
            s["isRuin"] = False
            before_cond = s["condition"]
            engine._maybe_disaster()
            if s["condition"] < before_cond:
                hits += 1
                dmg = before_cond - s["condition"]
                assert_true(se.DISASTER_DAMAGE[0] <= dmg <= se.DISASTER_DAMAGE[1], dmg)
        rate = hits / trials
        assert_true(0.0014 <= rate <= 0.0026,
                    f"disaster rate {rate:.4f} outside tolerance for DISASTER_PROB=0.002")
        print(f"  OK DISASTER_PROB=0.002, DISASTER_DAMAGE=(30,55); "
              f"empirical off-weather rate {rate:.4f} over {trials} trials")
    finally:
        se.WEATHER_ENABLED = old_weather


def main():
    print("Town integrity smoke (Phase A)")
    test_decay_constants_and_per_tick_math()
    test_repair_campaign_assigns_under_pressure_and_funded_repair_recovers()
    test_cull_removes_aged_unaffordable_ruins_and_clears_references()
    test_disaster_constants_and_off_weather_rate()
    print("ALL PASS")


if __name__ == "__main__":
    main()
