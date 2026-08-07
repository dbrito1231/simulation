# Sid-parity Phases 1-3 -- specialization need signals, competing memes, emergent role registry, and server fallback role-map parity.
# Split out of the original monolithic scripts/sid_parity_smoke.py (pure move,
# no behavior change).
from _sid_parity_smoke.helpers import *  # noqa: F401,F403


def test_dual_meme_seed(engine):
    counts = engine._meme_adoption_counts()
    assert_true(counts.get("harvest_spirit", 0) >= 1, "harvest_spirit not seeded")
    assert_true(counts.get("river_spirit", 0) >= 1, "river_spirit not seeded")
    assert_true(set(se.BELIEF_ARCHETYPES) == {"forest_steward", "egalitarian", "dreamwalker"},
                "resolved practical/political/outlier belief exemplars missing")
    assert_true(not set(se.BELIEF_ARCHETYPES) & set(engine.civilization["beliefRegistry"]),
                "belief archetypes must not consume live slots before an agent authors one")
    print(f"  OK dual meme seed: {counts}")


def test_survival_need_role(engine):
    # Collapse food gatherers and starve two agents -> need farmer/fisher.
    for a in engine.agents:
        if a["role"] in ("farmer", "fisher"):
            a["role"] = "trader"
    living = engine._living_agents()
    for a in living[:2]:
        a["hunger"] = se.STARVING_HUNGER
    needed = engine._village_needed_role()
    assert_true(needed in ("farmer", "fisher"), f"expected food role, got {needed}")
    print(f"  OK survival need -> {needed}")


def test_auto_switch_and_latency(engine):
    engine.civilization["lastRoleSwitchFrame"] = -se.ROLE_SWITCH_COOLDOWN
    engine.civilization["roleNeedSinceFrame"] = engine.frameTick - 50
    before_roles = {a["name"]: a["role"] for a in engine.agents}
    engine._maybe_auto_switch_role()
    switched = [
        a for a in engine.agents if before_roles[a["name"]] != a["role"]
    ]
    assert_true(switched, "auto switch did not fire")
    latency = engine.civilization.get("lastRoleRebalanceLatency")
    assert_true(latency is not None and latency >= 50, f"bad latency {latency}")
    print(f"  OK auto switch {switched[0]['name']} -> {switched[0]['role']} "
          f"(latency={latency})")


def test_emergent_role_registry(engine):
    """A proposed role must become persistent, switchable, and visible to
    the gathered-resource map only after elder approval."""
    elder = next(a for a in engine.agents if a["role"] == "elder")
    proposer = next(a for a in engine.agents if a is not elder)
    proposal = {
        "slug": "herbalist", "name": "Herbalist", "specialty": ["herbs"],
        "preferredProject": "farm_plot", "skill": "Gathers herbs for remedies.",
    }
    engine.apply_decision(proposer, {
        "action": "propose_role", "role": proposal, "reasoning": "smoke role",
    })
    assert_true(engine.civilization["pendingRoles"][0]["slug"] == "herbalist",
                engine.civilization["pendingRoles"])
    assert_true("herbalist" not in engine.civilization["roleRegistry"],
                "pending role leaked into registry")

    engine.apply_decision(elder, {
        "action": "approve_role", "target": "herbalist", "reasoning": "smoke approval",
    })
    assert_true("herbalist" in engine.civilization["roleRegistry"],
                engine.civilization["roleRegistry"])
    assert_true("herbalist" in engine.d["RESOURCE_GATHER_ROLES"].get("herbs", ()),
                engine.d["RESOURCE_GATHER_ROLES"])
    assert_true(engine.d["ROLE_PRIMARY_RESOURCE"].get("herbalist") == "herbs",
                engine.d["ROLE_PRIMARY_RESOURCE"])
    assert_true(engine.d["ROLE_SKILLS"].get("herbalist") == proposal["skill"],
                engine.d["ROLE_SKILLS"])
    persisted = engine._serialize_state()["civilization"].get("roleRegistry") or {}
    assert_true("herbalist" in persisted, "approved role missing from persistence payload")
    think_payload = engine._build_think_payload(proposer)
    assert_true(think_payload["role_project_map"].get("herbalist") == "farm_plot",
                think_payload["role_project_map"])
    assert_true("herbalist" in think_payload["resource_gather_roles_map"].get("herbs", []),
                think_payload["resource_gather_roles_map"])
    engine.apply_decision(proposer, {
        "action": "switch_role", "new_role": "herbalist", "reasoning": "smoke switch",
    })
    assert_true(proposer["role"] == "herbalist", proposer)
    print("  OK role proposal -> approval -> switch; herbs gather map refreshed")


def test_server_fallback_uses_live_role_maps(engine):
    """The server's pure fallback helpers must honor this engine's approved
    role, not only their module-global roles.json seed maps."""
    from server import ROLE_PROJECT, role_fallback_action  # noqa: E402

    assert_true("herbalist" not in ROLE_PROJECT,
                "test requires herbalist to be absent from server seed map")
    dynamic = {
        "role_project_map": engine.d["ROLE_PROJECT"],
        "role_primary_resource_map": engine.d["ROLE_PRIMARY_RESOURCE"],
        "resource_gather_roles_map": engine.d["RESOURCE_GATHER_ROLES"],
        "active_project": "none", "pending_blueprint_ids": [],
        "pending_roles": [], "idle_agents": [], "invention_status": "not needed",
    }
    fallback = role_fallback_action("herbalist", dynamic)
    assert_true(fallback["action"] == "start_project" and fallback["target"] == "farm_plot",
                f"dynamic preferred project ignored: {fallback}")

    dynamic.update({
        "active_project": "Herb Store", "project_progress": "herbs 0/2",
        "idle_agents": [
            {"name": "Generic", "role": "trader"},
            {"name": "Herbalist", "role": "herbalist"},
        ],
    })
    elder = role_fallback_action("elder", dynamic)
    assert_true(elder["action"] == "assign_task" and elder["target"] == "Herbalist",
                f"dynamic gather specialty ignored: {elder}")
    assert_true(elder["message"] == "gather herbs for the active project", elder)

    # The server must not spend a pitch-scoring call merely because an LLM
    # named an agent who is not in the engine's current nearby payload.
    import server  # noqa: E402
    scorer_calls = []
    original_scorer = server.run_belief_pitch
    try:
        server.run_belief_pitch = lambda *args, **kwargs: scorer_calls.append(args) or 0.9
        distant = server.score_belief_pitch_decision(
            {"action": "talk_to_nearby", "target": "FarAway",
             "belief_pitch": {"belief_id": "forest_steward", "pitch": "Protect the forest."}},
            {"belief_pitch_budget_remaining": 1,
             "belief_registry": [se.BELIEF_ARCHETYPES["forest_steward"]],
             "belief_ids": ["forest_steward"], "nearby_beliefs": {"Near": []},
             "agent_name": "Speaker", "relationships": {}, "frame_tick": 0},
        )
    finally:
        server.run_belief_pitch = original_scorer
    assert_true(not scorer_calls and "belief_pitch_scored" not in distant,
                f"distant pitch unexpectedly invoked scorer: {distant}")
    print("  OK server fallback uses live role project + specialty maps")
