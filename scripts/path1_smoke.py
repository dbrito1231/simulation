"""Deterministic smoke harness for Path 1 (Minecraft-like world depth).

No LM Studio required. Run:

    uv run python scripts/path1_smoke.py
"""
from __future__ import annotations

import os
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


def test_craft_routes_only_when_feasible(engine):
    # No Workshop built yet: crafting planks away from the station must be
    # rejected immediately, not send the agent commuting to workshop_row
    # only to fail on arrival. (Must run before test_ingot_craft, which
    # seeds a workshop structure into the shared engine.)
    agent = engine.agents[0]
    agent["currentZone"] = "farm"
    agent["currentDistrict"] = "farm_north"
    agent["resources"]["wood"] = 3
    summary = engine._craft_item(agent, "planks")
    assert_true("no Workshop built yet" in summary,
                f"expected feasibility rejection before travel, got: {summary}")
    print(f"  OK craft feasibility-before-travel: {summary}")


def test_pickless_stone_routing(engine):
    # A pickless stone-seeker must never be routed to the cave (no soil
    # there): dig in place when the ground allows, else head straight to
    # the nearest diggable district with a persistent goal.
    agent = engine.agents[0]
    for pick in ("wooden_pick", "stone_pick", "iron_pick"):
        agent["resources"].pop(pick, None)
    # Standing on diggable ground -> dig here.
    b = engine.civilization["districts"]["village_core"]["bounds"]
    agent["currentDistrict"] = "village_core"
    agent["currentZone"] = "village"
    agent["x"], agent["y"] = b["x1"] + 25, b["y1"] + 25
    d = engine.civilization["districts"]["village_core"]
    engine._ensure_district_terrain(d)
    _, _, gx, gy = engine._pos_to_grid(agent)
    d["terrain"][engine._tile_key(gx, gy)] = "soil"
    summary = engine._pickless_stone_route(agent, "stone")
    assert_true(summary is not None and "dug" in summary.lower(),
                f"expected an in-place dig, got: {summary}")
    # Standing in a cave -> route to a diggable district with a goal.
    agent["goal"] = None
    cb = engine.civilization["districts"]["cave_east"]["bounds"]
    agent["currentDistrict"] = "cave_east"
    agent["currentZone"] = "cave"
    agent["x"], agent["y"] = cb["x1"] + 25, cb["y1"] + 25
    summary2 = engine._pickless_stone_route(agent, "stone")
    assert_true(summary2 is not None and "diggable ground" in summary2,
                f"expected a diggable-district route, got: {summary2}")
    assert_true((agent.get("goal") or {}).get("kind") == "dig_relocate",
                "cave routing should set a persistent dig_relocate goal")
    agent["goal"] = None
    # With a pick -> normal cave routing applies (helper stands down).
    agent["resources"]["wooden_pick"] = 1
    assert_true(engine._pickless_stone_route(agent, "stone") is None,
                "helper must return None when the agent has the pick")
    agent["resources"].pop("wooden_pick", None)
    print("  OK pickless stone routing: dig-in-place, cave reroute, pick bypass")


def test_no_retarget_mid_walk(engine):
    # _set_agent_target_once must not re-roll the destination while the
    # agent is already traveling to that district (the re-roll every goal
    # step is what made agents jitter/circle around road hubs).
    agent = engine.agents[0]
    agent["x"], agent["y"] = 300, 300
    agent["waypoints"] = []
    engine._set_agent_target_once(agent, "farm_south")
    wps = agent.get("waypoints") or []
    dest1 = (wps[-1]["x"], wps[-1]["y"]) if wps else (agent["targetX"], agent["targetY"])
    engine._set_agent_target_once(agent, "farm_south")
    wps2 = agent.get("waypoints") or []
    dest2 = (wps2[-1]["x"], wps2[-1]["y"]) if wps2 else (agent["targetX"], agent["targetY"])
    assert_true(dest1 == dest2, f"destination re-rolled mid-walk: {dest1} -> {dest2}")
    print("  OK no re-target mid-walk")


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
    # Digging must work tool-free: it is the bootstrap stone source.
    agent = engine.agents[0]
    agent["currentDistrict"] = "farm_north"
    agent["resources"].pop("wooden_pick", None)
    agent["resources"]["stone"] = 0
    d = engine.civilization["districts"]["farm_north"]
    engine._ensure_district_terrain(d)
    did, _, gx, gy = engine._pos_to_grid(agent)
    key = engine._tile_key(gx, gy)
    d["terrain"][key] = "soil"
    summary = engine._dig_terrain(agent)
    assert_true("dug" in summary.lower(), f"dig_terrain failed: {summary}")
    assert_true(agent["resources"].get("stone", 0) >= 1, f"dig on soil yielded no stone: {summary}")
    assert_true(d["terrain"].get(key) == "rock", f"dug soil tile should become rock, got {d['terrain'].get(key)}")
    print(f"  OK dig_terrain (tool-free): {summary}")


def test_stone_bootstrap(engine):
    # Cold-start: a tier-0 stone gather must redirect to a dig, not fail —
    # otherwise stone -> pick -> Workshop -> stone deadlocks a fresh world.
    agent = engine.agents[0]
    agent["currentDistrict"] = "farm_north"
    agent["currentZone"] = "farm"
    for pick in ("wooden_pick", "stone_pick", "iron_pick"):
        agent["resources"].pop(pick, None)
    agent["resources"]["stone"] = 0
    d = engine.civilization["districts"]["farm_north"]
    engine._ensure_district_terrain(d)
    did, _, gx, gy = engine._pos_to_grid(agent)
    d["terrain"][engine._tile_key(gx, gy)] = "soil"
    summary = engine._perform_gather(agent, "stone")
    assert_true("dug" in summary.lower(), f"pickless stone gather should dig: {summary}")
    assert_true(agent["resources"].get("stone", 0) >= 1, f"bootstrap dig yielded no stone: {summary}")
    summary2 = engine._perform_gather(agent, "copper_ore")
    assert_true("found nothing" in summary2, f"copper_ore must stay pick-gated: {summary2}")
    print(f"  OK stone bootstrap: {summary}")


def test_dig_relocate(engine):
    # Standing on an exhausted (rock) tile with soil elsewhere in the
    # district must relocate the agent toward that soil, not fail forever.
    agent = engine.agents[0]
    agent["currentDistrict"] = "farm_north"
    d = engine.civilization["districts"]["farm_north"]
    engine._ensure_district_terrain(d)
    did, _, gx, gy = engine._pos_to_grid(agent)
    # Blank the whole grid to rock except one soil tile far from the agent.
    for key in d["terrain"]:
        d["terrain"][key] = "rock"
    soil_gx, soil_gy = (gx + 3) % se.PATH1_GRID_COLS, (gy + 3) % se.PATH1_GRID_ROWS
    d["terrain"][engine._tile_key(soil_gx, soil_gy)] = "soil"
    before_x, before_y = agent["x"], agent["y"]
    summary = engine._dig_terrain(agent)
    assert_true("fresh ground" in summary, f"expected relocation, got: {summary}")
    assert_true(agent.get("lastTerrainRejection") is None, "relocation should not leave a rejection")
    b = d["bounds"]
    expected_x = b["x1"] + (soil_gx + 0.5) * se.TILE_CELL
    expected_y = b["y1"] + (soil_gy + 0.5) * se.TILE_CELL
    assert_true(agent["targetX"] == expected_x and agent["targetY"] == expected_y,
                f"target should be the soil tile center, got ({agent['targetX']},{agent['targetY']})")
    print(f"  OK dig_relocate: {summary}")


def test_dig_cave_relocates(engine):
    # A cave district has no soil by design (whole grid defaults to "rock").
    # A pickless miner digging there must be routed to a soil-bearing
    # district (e.g. a farm/village) via a persistent goal — otherwise the
    # next think cycle's role reflex bounces them straight back to the cave.
    agent = engine.agents[0]
    agent["currentDistrict"] = "cave_east"
    d = engine.civilization["districts"]["cave_east"]
    engine._ensure_district_terrain(d)
    summary = engine._dig_terrain(agent)
    assert_true("heads to" in summary and "diggable ground" in summary,
                f"expected cross-district relocation, got: {summary}")
    assert_true(agent.get("lastTerrainRejection") is None, "relocation should not leave a rejection")
    goal = agent.get("goal") or {}
    assert_true(goal.get("kind") == "dig_relocate",
                f"expected a persistent dig_relocate goal, got: {goal}")
    dest = goal["target_district"]
    # Mid-transit: stepping the goal must keep it alive (walk continues).
    assert_true(engine._step_goal(agent) is True, "goal should persist while in transit")
    assert_true((agent.get("goal") or {}).get("kind") == "dig_relocate",
                "goal should survive a transit step")
    # Simulate arrival: place the agent in the destination on a soil tile,
    # step the goal, and expect an actual dig yield with the goal still live.
    dd = engine.civilization["districts"][dest]
    engine._ensure_district_terrain(dd)
    b = dd["bounds"]
    agent["currentDistrict"] = dest
    agent["x"], agent["y"] = b["x1"] + 20, b["y1"] + 20
    _, _, gx, gy = engine._pos_to_grid(agent)
    dd["terrain"][engine._tile_key(gx, gy)] = "soil"
    before = agent["resources"].get("stone", 0)
    assert_true(engine._step_goal(agent) is True, "goal should keep digging after arrival")
    assert_true(agent["resources"].get("stone", 0) > before, "arrival dig should yield stone")
    agent["goal"] = None
    print(f"  OK dig_cave_relocates: {summary} -> dug at {dest}")


def test_dig_district_exhausted(engine):
    # No soil ANYWHERE across every diggable-kind district: dig must fail
    # with a clear hint, not loop silently.
    agent = engine.agents[0]
    agent["currentDistrict"] = "farm_south"
    diggable_dids = [did for did, dd in engine.civilization["districts"].items()
                      if dd.get("kind") not in se.NON_DIGGABLE_DISTRICT_KINDS]
    for other_did in diggable_dids:
        other = engine.civilization["districts"][other_did]
        engine._ensure_district_terrain(other)
        if other_did != "farm_south":
            for key in other["terrain"]:
                other["terrain"][key] = "rock"
    d = engine.civilization["districts"]["farm_south"]
    for key in d["terrain"]:
        d["terrain"][key] = "rock"
    summary = engine._dig_terrain(agent)
    assert_true("cannot dig" in summary, f"expected a dig failure, got: {summary}")
    rejection = agent.get("lastTerrainRejection") or {}
    assert_true("another district" in (rejection.get("reason") or ""),
                f"expected a district-move hint, got: {rejection}")
    print(f"  OK dig_district_exhausted: {summary}")


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


def test_env_effects():
    # Dedicated engine: environmental function-block effects (shelter/light/
    # upkeep) are independent of the shared engine's accumulated structures.
    engine = make_engine()
    c = engine.civilization
    did = "village_core"
    c["projectRegistry"]["hearth"] = {
        "name": "Hearth", "needs": {"stone": 2}, "visualStyle": "generic",
        "function": {
            "light": {"scope": "district"},
            "upkeep": {"resource": "charcoal", "amount": 1},
        },
    }
    c["structures"].append({
        "id": 9500, "type": "hearth", "x": 100, "y": 100,
        "condition": 100, "districtId": did, "isRuin": False,
    })
    c["stockpile"]["charcoal"] = 5
    agent = engine.agents[0]
    agent["currentDistrict"] = did
    agent["homeStructureId"] = None
    agent["incapacitated"] = False
    agent["health"] = 50
    engine.frameTick = 12000  # inside the night fraction of day 0

    engine._tick_night_pressure()
    assert_true(c["stockpile"]["charcoal"] == 4,
                f"expected 1 charcoal burned, got {c['stockpile']['charcoal']}")
    assert_true(did in (c.get("litDistricts") or []),
                f"expected {did} lit, got {c.get('litDistricts')}")
    assert_true(agent["health"] == 50,
                f"lit agent should take no exposure damage, got {agent['health']}")
    print(f"  OK env upkeep + light: charcoal={c['stockpile']['charcoal']}, "
          f"lit={c.get('litDistricts')}")

    # Drain the fuel and force a new day so upkeep is re-charged: the hearth
    # can't pay, so it goes unfueled and the agent takes exposure damage.
    c["stockpile"]["charcoal"] = 0
    engine.frameTick = se.DAY_FRAMES + 12000  # day 1, still the night fraction
    engine._tick_night_pressure()
    assert_true(did not in (c.get("litDistricts") or []),
                f"expected {did} unlit once fuel is drained, got {c.get('litDistricts')}")
    assert_true(agent["health"] < 50,
                f"unlit exposed agent should take exposure damage, got {agent['health']}")
    print(f"  OK unfueled hearth -> unlit + exposure damage (health={agent['health']})")

    # ENV_EFFECTS_ENABLED=False: legacy behavior -- no upkeep charge, no lit
    # districts, unsheltered agents take ordinary exposure damage.
    se.ENV_EFFECTS_ENABLED = False
    try:
        agent["health"] = 50
        c["stockpile"]["charcoal"] = 5
        engine.frameTick = se.DAY_FRAMES * 2 + 12000  # day 2, night fraction
        engine._tick_night_pressure()
        assert_true(c.get("litDistricts") == [],
                    f"litDistricts should be empty with the flag off, got {c.get('litDistricts')}")
        assert_true(c["stockpile"]["charcoal"] == 5,
                    "flag off: upkeep must not be charged")
        assert_true(agent["health"] < 50,
                    f"flag off: unsheltered agent should still take exposure damage, got {agent['health']}")
    finally:
        se.ENV_EFFECTS_ENABLED = True
    print("  OK ENV_EFFECTS_ENABLED=False legacy behavior")


def test_env_upkeep_shared_district():
    # Two working structures of the same upkeep type sharing one district:
    # the district stock must be consulted ONCE (aggregated), not re-read
    # per structure, so it can't be over-drawn below zero while silently
    # reporting "fueled" with an under-payment.
    engine = make_engine()
    c = engine.civilization
    did = "village_core"
    c["projectRegistry"]["hearth"] = {
        "name": "Hearth", "needs": {"stone": 2}, "visualStyle": "generic",
        "function": {
            "upkeep": {"resource": "charcoal", "amount": 1},
        },
    }
    c["structures"].append({
        "id": 9600, "type": "hearth", "x": 100, "y": 100,
        "condition": 100, "districtId": did, "isRuin": False,
    })
    c["structures"].append({
        "id": 9601, "type": "hearth", "x": 120, "y": 100,
        "condition": 100, "districtId": did, "isRuin": False,
    })
    # total need = 1 * 2 structures = 2 charcoal; district holds only 1,
    # stockpile covers the remaining 1.
    engine._set_district_stock(did, "charcoal", 1)
    c["stockpile"]["charcoal"] = 5
    working = [s for s in c["structures"] if s["type"] == "hearth"]
    fueled = engine._pay_upkeep(working, "charcoal", 2)
    assert_true(fueled, "expected shared-district upkeep to be fueled")
    assert_true(engine._district_stock(did, "charcoal") == 0,
                f"expected district stock drained to 0, got {engine._district_stock(did, 'charcoal')}")
    assert_true(c["stockpile"]["charcoal"] == 4,
                f"expected stockpile to pay exactly the 1-unit shortfall, got {c['stockpile']['charcoal']}")
    print(f"  OK env upkeep shared district: district=0, "
          f"stockpile={c['stockpile']['charcoal']}, fueled={fueled}")


def test_transit_and_economy_sinks():
    engine = make_engine()
    c = engine.civilization
    c["projectRegistry"]["dock"] = {"name": "Dock", "function": {"unlocks": [
        {"kind": "transit", "terrain": "ocean", "consumes": {"boat": 1}}]}}
    c["structures"].append({"id": 9700, "type": "dock", "districtId": "beach",
                            "condition": 100, "isRuin": False})
    c["stockpile"]["boat"] = 2
    assert_true(engine._has_ocean_transit(), "working dock should unlock transit")
    assert_true(engine._consume_ocean_transit(), "boat-funded transit should launch")
    assert_true(c["stockpile"]["boat"] == 1, c["stockpile"])
    c["stockpile"]["planks"] = 1
    structure = {"type": "house", "condition": 80, "isRuin": False}
    assert_true(engine._repair_cost(structure) == {"planks": 1}, engine._repair_cost(structure))
    c["stockpile"]["dried_fish"] = len(engine._living_agents())
    hunger = engine.agents[0]["hunger"]
    engine._tick_comfort_consumption()
    assert_true(engine.agents[0]["hunger"] >= hunger + 2, engine.agents[0]["hunger"])
    print("  OK transit boat sink + economy comfort/repair sinks")


def test_mint_coin_currency():
    """Market + mint together: minting converts stockpiled gold to coin, and
    a priced trade settles in coin (not gold) -- the seller's wealth
    benchmark rises accordingly."""
    engine = make_engine()
    c = engine.civilization
    c["structures"].append({"id": 9820, "type": "market", "districtId": "village_core",
                            "condition": 100, "isRuin": False})
    c["structures"].append({"id": 9821, "type": "mint", "districtId": "village_core",
                            "condition": 100, "isRuin": False})
    assert_true(engine._market_active(), "working market should unlock pricing")
    assert_true(engine._mint_active(), "working mint should unlock currency")
    assert_true(engine._active_currency() == "coin", "active currency should be coin once minted")

    c["stockpile"]["gold"] = 5
    c["stockpile"]["coin"] = 0
    engine._maybe_mint_coin()
    assert_true(c["stockpile"]["coin"] == se.MINT_RATE,
                f"expected {se.MINT_RATE} coin minted, got {c['stockpile']}")
    assert_true(c["stockpile"]["gold"] == 5 - se.MINT_RATE, c["stockpile"])

    seller, buyer = engine.agents[0], engine.agents[1]
    seller["resources"]["wood"] = 3
    seller["resources"]["gold"] = 0
    seller["resources"]["coin"] = 0
    buyer["resources"]["coin"] = 10
    buyer["resources"]["gold"] = 10
    # A fair-priced (neutral) trade is value-neutral by design: the coin
    # received equals the priced value of the good given up, so wealth is
    # unchanged. A rival surcharge is the case that actually moves the
    # seller's wealth benchmark (they collect more coin than the good's
    # base-priced value) -- use that to assert a genuine rise.
    seller["relationships"][buyer["name"]] = "rival"
    wealth_before = engine._agent_wealth(seller)
    summary = engine._priced_trade(seller, buyer, "wood")
    assert_true("sold" in summary, summary)
    assert_true(seller["resources"].get("coin", 0) > 0,
                f"seller should have received coin, got {seller['resources']}")
    assert_true(seller["resources"].get("gold", 0) == 0,
                "a coin-settled trade must never touch gold")
    assert_true(buyer["resources"]["coin"] < 10, "buyer's coin balance should drop")
    assert_true(buyer["resources"]["gold"] == 10, "buyer's gold balance must stay untouched")
    wealth_after = engine._agent_wealth(seller)
    assert_true(wealth_after > wealth_before,
                f"seller wealth should rise from the coin-settled sale: {wealth_before} -> {wealth_after}")
    print("  OK mint mints coin from stockpile gold; priced trade settles in coin; wealth rises")


def test_market_only_settles_in_gold():
    """Control case: a market with no mint must keep settling priced trades
    in gold exactly as before -- pre-mint behavior stays byte-identical."""
    engine = make_engine()
    c = engine.civilization
    c["structures"].append({"id": 9822, "type": "market", "districtId": "village_core",
                            "condition": 100, "isRuin": False})
    assert_true(engine._market_active(), "working market should unlock pricing")
    assert_true(not engine._mint_active(), "no mint built -- currency stays gold")
    assert_true(engine._active_currency() == "gold", "no mint -> active currency must stay gold")

    seller, buyer = engine.agents[2], engine.agents[3]
    seller["resources"]["wood"] = 3
    seller["resources"]["gold"] = 0
    buyer["resources"]["gold"] = 10
    summary = engine._priced_trade(seller, buyer, "wood")
    assert_true("sold" in summary, summary)
    assert_true(seller["resources"].get("gold", 0) > 0,
                f"seller should have received gold (pre-mint fallback), got {seller['resources']}")
    assert_true(seller["resources"].get("coin", 0) == 0,
                "no coin should ever appear without a mint")
    print("  OK market-only (no mint) still settles priced trades in gold, unchanged")


def test_transit_migration_from_instance():
    """Light and transit restore migrations must recreate retired registry
    entries from standing structure instances through the shared fallback.
    Uses a temp DB_PATH so this never touches the real simulation/state.db."""
    import tempfile

    source = make_engine()
    c = source.civilization
    c["projectRegistry"].pop("hearth", None)
    c["projectRegistry"].pop("dock", None)
    c["structures"].append({
        "id": 9899, "type": "hearth", "districtId": "village_core",
        "condition": 100, "isRuin": False, "name": "Old Hearth",
        "visualStyle": "warm_stone",
    })
    c["structures"].append({
        "id": 9900, "type": "dock", "districtId": "beach",
        "condition": 100, "isRuin": False, "name": "Old Dock",
        "visualStyle": "harbor",
    })
    with source.lock:
        payload = source._serialize_state()

    tmp_dir = tempfile.gettempdir()
    tmp_path = str(Path(tmp_dir) / "path1_smoke_state_migration.db")
    se._write_state_db(tmp_path, payload)

    old_state_path = se.DB_PATH
    se.DB_PATH = tmp_path
    try:
        target = make_engine()
        restored = target.restore_state()
        assert_true(restored, "restore_state should succeed from the temp save")
        registry = target.civilization.get("projectRegistry") or {}
        light_entry = registry.get("hearth")
        assert_true(isinstance(light_entry, dict),
                    f"hearth registry entry should be recreated from the instance, got {light_entry}")
        assert_true(light_entry.get("name") == "Old Hearth", light_entry)
        assert_true(light_entry.get("needs") == {"wood": 2, "stone": 2}, light_entry)
        assert_true(light_entry.get("visualStyle") == "warm_stone", light_entry)
        assert_true(light_entry.get("custom") is True, light_entry)
        light_fn = light_entry.get("function") or {}
        assert_true(light_fn.get("light") == {"scope": "district"}, light_fn)
        assert_true(light_fn.get("upkeep") == {"resource": "charcoal", "amount": 1},
                    light_fn)

        entry = registry.get("dock")
        assert_true(isinstance(entry, dict),
                    f"dock registry entry should be recreated from the instance, got {entry}")
        assert_true(entry.get("name") == "Old Dock", entry)
        assert_true(entry.get("needs") == {"wood": 2, "stone": 2}, entry)
        assert_true(entry.get("visualStyle") == "harbor", entry)
        assert_true(entry.get("custom") is True, entry)
        fn = entry.get("function") or {}
        unlocks = fn.get("unlocks") or []
        assert_true(any(u.get("kind") == "transit" and u.get("terrain") == "ocean"
                        for u in unlocks if isinstance(u, dict)),
                    f"expected ocean transit unlock on recreated dock entry, got {unlocks}")
        assert_true(sum(1 for u in unlocks if isinstance(u, dict)
                        and u.get("kind") == "transit") == 1, unlocks)
        assert_true(target._ensure_registry_entry_from_instance(
            target.civilization, "hearth") is light_entry,
            "shared fallback must preserve an existing light registry entry")
        assert_true(target._ensure_registry_entry_from_instance(
            target.civilization, "dock") is entry,
            "shared fallback must preserve an existing transit registry entry")
        print("  OK light + transit registry entries recreated idempotently from instances")
    finally:
        se.DB_PATH = old_state_path
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        try:
            os.remove(tmp_path + "-wal")
        except OSError:
            pass
        try:
            os.remove(tmp_path + "-shm")
        except OSError:
            pass


def main():
    assert_true(se.PATH1_ENABLED, "PATH1_ENABLED must be True for smoke")
    engine = make_engine()
    print("Path 1 smoke (headless)")
    test_flags(engine)
    test_craft_routes_only_when_feasible(engine)
    test_pickless_stone_routing(engine)
    test_no_retarget_mid_walk(engine)
    test_ingot_craft(engine)
    test_tool_gate(engine)
    test_place_block(engine)
    test_dig_terrain(engine)
    test_stone_bootstrap(engine)
    test_dig_relocate(engine)
    test_dig_cave_relocates(engine)
    test_dig_district_exhausted(engine)
    test_two_settlements(engine)
    test_env_effects()
    test_env_upkeep_shared_district()
    test_transit_and_economy_sinks()
    test_mint_coin_currency()
    test_market_only_settles_in_gold()
    test_transit_migration_from_instance()
    import py_compile
    py_compile.compile(str(ROOT / "simulation" / "sim_engine.py"), doraise=True)
    py_compile.compile(str(ROOT / "simulation" / "server.py"), doraise=True)
    print("  OK py_compile")
    print("PASS — all Path 1 smoke checks")


if __name__ == "__main__":
    main()
