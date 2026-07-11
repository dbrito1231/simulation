"""Deterministic smoke harness for Path 1 (Minecraft-like world depth).

No LM Studio required. Run:

    uv run python scripts/path1_smoke.py
"""
from __future__ import annotations

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
        "RESOURCE_GATHER_ROLES": _build_resource_gather_roles(roles),
        "AVAILABLE_ACTIONS": list(se.DECISION_ACTIONS) if hasattr(se, "DECISION_ACTIONS") else [
            "collect_resource", "craft_item", "place_block", "remove_block",
            "dig_terrain", "plant_terrain", "propose_treaty", "vote_treaty", "rest",
        ],
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
    # Import server actions list if not on sim_engine
    try:
        import server  # noqa: E402
        deps["AVAILABLE_ACTIONS"] = list(server.DECISION_ACTIONS)
    except Exception:
        pass
    engine = se.SimEngine(deps, roster_size=roster_size)
    return engine


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_flags(engine):
    snap = engine.snapshot()
    flags = snap["config"]["flags"]
    assert_true(flags.get("PATH1_ENABLED"), "PATH1_ENABLED not in snapshot flags")
    assert_true(flags.get("INDUSTRY_ENABLED"), "INDUSTRY_ENABLED not bundled")
    print(f"  OK flags: PATH1_ENABLED={flags.get('PATH1_ENABLED')}")


def test_ingot_craft(engine):
    agent = engine.agents[0]
    agent["currentZone"] = "workshop"
    agent["currentDistrict"] = "workshop_row"
    agent["resources"]["copper_ore"] = 2
    agent["resources"]["charcoal"] = 2
    for stype in ("workshop", "kiln"):
        engine.civilization["structures"].append({
            "id": 9000 if stype == "workshop" else 9001, "type": stype,
            "x": 100, "y": 100, "condition": 100, "districtId": "workshop_row",
        })
    summary = engine._craft_item(agent, "copper_ingot")
    assert_true("crafted copper_ingot" in summary, f"ingot craft failed: {summary}")
    assert_true(agent["resources"].get("copper_ingot", 0) >= 1, "no copper_ingot in inventory")
    print(f"  OK ingot craft: {summary}")


def test_tool_gate(engine):
    miner = next((a for a in engine.agents if a["role"] == "miner"), engine.agents[0])
    miner["resources"] = {}
    miner["currentZone"] = "cave"
    miner["currentDistrict"] = "cave_east"
    summary = engine._perform_gather(miner, "iron_ore")
    assert_true("found nothing" in summary, f"iron_ore should be blocked without pick: {summary}")
    miner["resources"]["iron_pick"] = 1
    summary2 = engine._perform_gather(miner, "iron_ore")
    assert_true("collected" in summary2, f"iron_ore should succeed with iron_pick: {summary2}")
    print("  OK tool gate: blocked without pick, success with iron_pick")


def test_place_block(engine):
    agent = engine.agents[0]
    agent["currentDistrict"] = "village_core"
    agent["resources"]["wood"] = 5
    did, d, gx, gy = engine._pos_to_grid(agent)
    summary = engine._place_block(agent, "wall", gx, gy)
    assert_true("placed wall" in summary, f"place_block failed: {summary}")
    tiles = engine.civilization["districts"]["village_core"].get("tiles", {})
    assert_true(len(tiles) >= 1, "district.tiles empty after place_block")
    print(f"  OK place_block: {len(tiles)} tile(s)")


def test_dig_terrain(engine):
    agent = engine.agents[0]
    agent["currentDistrict"] = "farm_north"
    agent["resources"]["wooden_pick"] = 1
    agent["resources"]["stone"] = 0
    d = engine.civilization["districts"]["farm_north"]
    engine._ensure_district_terrain(d)
    did, _, gx, gy = engine._pos_to_grid(agent)
    d["terrain"][engine._tile_key(gx, gy)] = "grove"
    summary = engine._dig_terrain(agent)
    assert_true("dug" in summary.lower(), f"dig_terrain failed: {summary}")
    print(f"  OK dig_terrain: {summary}")


def test_two_settlements(engine):
    c = engine.civilization
    for i in range(se.SETTLEMENT_STRUCT_THRESHOLD):
        c["structures"].append({
            "id": 8000 + i, "type": "house", "x": 100, "y": 100,
            "condition": 100, "districtId": "village_core",
        })
    # Force population threshold for smoke (roster may be < SETTLEMENT_POP_THRESHOLD).
    orig = se.SETTLEMENT_POP_THRESHOLD
    se.SETTLEMENT_POP_THRESHOLD = 2
    try:
        engine._maybe_found_settlement()
    finally:
        se.SETTLEMENT_POP_THRESHOLD = orig
    settlements = c.get("settlements") or []
    assert_true(len(settlements) >= 2, f"expected 2 settlements, got {len(settlements)}")
    print(f"  OK settlements: {len(settlements)} ({', '.join(s['id'] for s in settlements)})")


def main():
    assert_true(se.PATH1_ENABLED, "PATH1_ENABLED must be True for smoke")
    engine = make_engine()
    print("Path 1 smoke (headless)")
    test_flags(engine)
    test_ingot_craft(engine)
    test_tool_gate(engine)
    test_place_block(engine)
    test_dig_terrain(engine)
    test_two_settlements(engine)
    import py_compile
    py_compile.compile(str(ROOT / "simulation" / "sim_engine.py"), doraise=True)
    py_compile.compile(str(ROOT / "simulation" / "server.py"), doraise=True)
    print("  OK py_compile")
    print("PASS — all Path 1 smoke checks")


if __name__ == "__main__":
    main()
