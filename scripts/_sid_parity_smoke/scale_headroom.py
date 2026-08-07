# Sid-parity Phase 6 -- roster scale headroom, district bucketing, and think-dispatch staleness priority.
# Split out of the original monolithic scripts/sid_parity_smoke.py (pure move,
# no behavior change).
from _sid_parity_smoke.helpers import *  # noqa: F401,F403


def test_roster_default_unchanged():
    """roster_size in [1, len(AGENT_DEFS)] (today's default of 8 and the
    whole hand-written range) must resolve identically to the pre-Phase-6
    algorithm -- headroom is pure addition, not a default change."""
    engine = make_engine(8)
    for size in (1, 5, 8, 12):
        got = [d["name"] for d in engine._select_active_defs(size)]
        expected = [d["name"] for d in _legacy_select_active_defs(size)]
        assert_true(got == expected, f"roster_size={size} selection changed: {got} != {expected}")
    assert_true([a["name"] for a in engine.agents] == se.ROSTER,
                f"default roster_size=8 no longer matches ROSTER: {[a['name'] for a in engine.agents]}")
    print("  OK roster_size 1/5/8/12 selection unchanged (matches pre-Phase-6 algorithm)")


def test_roster_headroom_generates_20():
    """roster_size=20 must actually produce 20 distinct, fully-formed agents
    -- not a silent clamp to 12 -- with generated agents spread across
    multiple roles.json roles and indistinguishable in shape from a
    hand-written def."""
    assert_true(se.MAX_ROSTER_SIZE == 20, f"expected MAX_ROSTER_SIZE=20, got {se.MAX_ROSTER_SIZE}")
    engine = make_engine(se.MAX_ROSTER_SIZE)
    assert_true(len(engine.agents) == 20, f"roster_size=20 produced {len(engine.agents)} agents")
    names = [a["name"] for a in engine.agents]
    assert_true(len(set(names)) == 20, f"duplicate names in generated roster: {names}")
    hand_written = {d["name"] for d in se.AGENT_DEFS}
    generated_names = set(names) - hand_written
    assert_true(len(generated_names) == 20 - len(se.AGENT_DEFS),
                f"expected {20 - len(se.AGENT_DEFS)} generated agents, got {generated_names}")
    assert_true("elder" not in {a["role"] for a in engine.agents if a["name"] in generated_names},
                "a generated agent was seeded into the singular elder role")
    roles_used = {a["role"] for a in engine.agents}
    assert_true(len(roles_used) >= 6,
                f"generated roster clustered into too few roles: {roles_used}")
    # A generated agent must be indistinguishable in shape from a hand-written
    # one to every other system, and able to take a normal decision.
    gen_agent = next(a for a in engine.agents if a["name"] in generated_names)
    hand_agent = next(a for a in engine.agents if a["name"] not in generated_names)
    assert_true(set(gen_agent.keys()) == set(hand_agent.keys()),
                f"generated agent shape differs: "
                f"{set(gen_agent.keys()) ^ set(hand_agent.keys())}")
    engine.apply_decision(gen_agent, {"action": "rest", "reasoning": "smoke"})
    assert_true(engine.civilization["basePopulation"] == 20,
                f"basePopulation did not reflect roster_size=20: {engine.civilization['basePopulation']}")
    print(f"  OK roster_size=20 -> 20 agents ({len(generated_names)} generated, "
          f"roles={sorted(roles_used)})")


def test_newcomer_backstop_reaches_generated_slots():
    """_maybe_welcome_newcomer must not silently stall at 12 agents once
    every hand-written AGENT_DEFS name is taken. Regression for the gap left
    by Phase 6 (docs/archive/plan-sid-parity-gaps.md): MAX_ROSTER_SIZE/
    _generated_agent_defs raised the cold-start ceiling to 20, but the
    house-driven newcomer backstop still only ever drew from the 12
    hand-written defs, so a village that never cold-started above 12 could
    never grow into slots 12-19 via houses alone."""
    engine = make_engine(len(se.AGENT_DEFS))
    assert_true(len(engine.agents) == len(se.AGENT_DEFS),
                f"expected a full hand-written roster, got {len(engine.agents)}")
    hand_written = {d["name"] for d in se.AGENT_DEFS}
    assert_true({a["name"] for a in engine.agents} == hand_written,
                "cold start did not fill every hand-written AGENT_DEFS slot")

    # Simulate house-driven cap growth past 12 without a cold-start reset --
    # the exact scenario _maybe_welcome_newcomer must serve.
    target = se.MAX_ROSTER_SIZE
    engine.civilization["basePopulation"] = target
    generated_pool_names = {d["name"] for d in se._generated_agent_defs(target - len(se.AGENT_DEFS))}

    added = []
    for _ in range(target - len(se.AGENT_DEFS)):
        before = set(engine.agent_names)
        engine._maybe_welcome_newcomer()
        after = set(engine.agent_names)
        new = after - before
        assert_true(len(new) == 1, f"expected exactly one newcomer per call, got {new}")
        added.append(next(iter(new)))

    assert_true(len(engine.agents) == target,
                f"newcomer backstop stalled at {len(engine.agents)} agents, expected {target}")
    assert_true(set(added) == generated_pool_names,
                f"newcomers {added} did not match the deterministic generated pool {generated_pool_names}")
    assert_true(len(set(added)) == len(added), f"newcomer backstop produced duplicate names: {added}")

    # Cap reached: one more call must no-op, not overflow past basePopulation.
    before = set(engine.agent_names)
    engine._maybe_welcome_newcomer()
    assert_true(engine.agent_names == before,
                "newcomer backstop added an agent past the population cap")
    print(f"  OK newcomer backstop fills generated slots 12-{target - 1} once hand-written defs are exhausted")
def test_district_bucket_matches_flat_scan():
    """The district-bucketed proximity scan (_get_nearby_agents/_detailed)
    must report exactly what an equivalent flat O(n) scan would for the same
    positions -- including across a district border narrower than
    NEARBY_RADIUS (village_core/market are only ~70px apart), and must NOT
    leak agents across two districts far enough apart that a flat scan
    would never have found them either."""
    engine = make_engine(se.MAX_ROSTER_SIZE)
    districts = engine.civilization["districts"]
    forest_c = _district_center(districts["forest"]["bounds"])
    cave_c = _district_center(districts["cave_east"]["bounds"])
    for i, a in enumerate(engine.agents):
        if i < 6:
            a["currentDistrict"], a["x"], a["y"] = "village_core", 895, 1050
        elif i < 10:
            a["currentDistrict"], a["x"], a["y"] = "market", 975, 1050
        elif i < 15:
            a["currentDistrict"], a["x"], a["y"] = "forest", forest_c["x"], forest_c["y"]
        else:
            a["currentDistrict"], a["x"], a["y"] = "cave_east", cave_c["x"], cave_c["y"]
    # Force a bucket/adjacency rebuild against these hand-placed positions.
    engine._district_agent_buckets_frame = -1
    engine._district_adjacency = None

    for a in engine.agents:
        got = sorted(engine._get_nearby_agents(a))
        want = _flat_nearby(engine.agents, a, se.NEARBY_RADIUS)
        assert_true(got == want,
                    f"{a['name']} ({a['currentDistrict']}): bucketed={got} flat={want}")
        detailed_names = sorted(d["name"] for d in engine._get_nearby_detailed(a))
        assert_true(set(detailed_names) <= set(want),
                    f"{a['name']}: detailed scan found names outside the flat reference: "
                    f"{detailed_names} vs {want}")

    # village_core/market sit only ~70px apart (< NEARBY_RADIUS=80) -- an
    # agent standing at the shared edge in either district must see the
    # other side, proving cross-district adjacency actually engaged.
    village_agent = next(a for a in engine.agents if a["currentDistrict"] == "village_core")
    market_names = {a["name"] for a in engine.agents if a["currentDistrict"] == "market"}
    assert_true(set(engine._get_nearby_agents(village_agent)) & market_names,
                "village_core/market border neighbors were not visible across districts")

    # forest/cave_east are far apart -- must not leak into each other's pool.
    forest_agent = next(a for a in engine.agents if a["currentDistrict"] == "forest")
    cave_names = {a["name"] for a in engine.agents if a["currentDistrict"] == "cave_east"}
    assert_true(not (set(engine._get_nearby_agents(forest_agent)) & cave_names),
                "forest/cave_east districts leaked into each other's candidate pool")
    print("  OK district-bucketed proximity matches flat scan; cross-border pairs "
          "visible, far districts do not leak")


def test_think_dispatch_staleness_priority():
    """When the LLM worker pool is contested, the agent most overdue since
    its last successful think must be tried first each tick -- not whichever
    agent happens to be earliest in roster order. _schedule_think is stubbed
    with a deterministic fake (no real executor/LLM call) so this checks
    _tick_once's dispatch *ordering* in isolation, without racing a real
    background think job to completion."""
    engine = make_engine(se.MAX_ROSTER_SIZE)
    for a in engine.agents:
        a["thinkTimer"] = 0
        a["isThinking"] = False

    dispatch_order = []
    slots = {"remaining": 1}

    def fake_schedule_think(agent):
        dispatch_order.append(agent["name"])
        if slots["remaining"] <= 0:
            return False
        slots["remaining"] -= 1
        return True

    original = engine._schedule_think
    engine._schedule_think = fake_schedule_think
    try:
        stale_agent = engine.agents[-1]
        for a in engine.agents:
            a["lastThinkFrame"] = -10_000 if a is stale_agent else engine.frameTick
        engine._tick_once()
    finally:
        engine._schedule_think = original

    assert_true(dispatch_order and dispatch_order[0] == stale_agent["name"],
                f"most-overdue agent was not tried first: {dispatch_order[:3]}")
    assert_true(stale_agent["thinkTimer"] == stale_agent["thinkInterval"],
                "most-overdue agent's think was not actually dispatched this tick")
    print("  OK think dispatch prioritizes the most stale agent under pool contention "
          f"(order[:3]={dispatch_order[:3]})")
