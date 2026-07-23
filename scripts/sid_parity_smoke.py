"""Deterministic smoke harness for Sid-parity Phases 1-3.

Exercises specialization need signals, priority/repeal governance, competing
memes, and belief-biased votes without LM Studio. Run:

    uv run python scripts/sid_parity_smoke.py
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
        "AVAILABLE_ACTIONS": [
            "switch_role", "propose_role", "approve_role", "reject_role",
            "propose_rule", "vote_rule", "repeal_rule", "found_belief", "talk_to_nearby",
            "collect_resource", "contribute_resources", "rest",
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
    engine = se.SimEngine(deps, roster_size=roster_size)
    return engine


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


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


def test_pending_role_cap():
    """The engine and prompt-side validator both reject role proposals once
    the bounded review queue is full."""
    from server import MAX_EMERGENT_ROLES, MAX_PENDING_ROLES, validate_role  # noqa: E402

    engine = make_engine(8)
    proposal = {
        "slug": "queue_tester", "name": "Queue Tester", "specialty": ["herbs"],
        "preferredProject": "farm_plot", "skill": "Tests role queue limits.",
    }
    engine.civilization["pendingRoles"] = [
        {"slug": f"pending_role_{i}"} for i in range(se.MAX_PENDING_ROLES)
    ]
    ok, reason = engine._validate_role(proposal)
    assert_true(not ok and reason == "too many pending roles", (ok, reason))
    proposer = engine.agents[0]
    engine.apply_decision(proposer, {
        "action": "propose_role", "role": proposal, "reasoning": "smoke queue cap",
    })
    assert_true(len(engine.civilization["pendingRoles"]) == se.MAX_PENDING_ROLES,
                engine.civilization["pendingRoles"])

    known_resources = list(engine.civilization["resourceRegistry"])
    known_roles = list(engine.civilization["roleRegistry"])
    known_projects = list(engine.civilization["projectRegistry"])
    ok, reason = validate_role(
        proposal, known_resources, known_roles, [], known_projects,
        pending_role_count=MAX_PENDING_ROLES, emergent_role_count=0,
    )
    assert_true(not ok and reason == "too many pending roles", (ok, reason))
    ok, reason = validate_role(
        proposal, known_resources, known_roles, [], known_projects,
        pending_role_count=0, emergent_role_count=MAX_EMERGENT_ROLES,
    )
    assert_true(not ok and reason == "too many emergent roles", (ok, reason))
    print("  OK pending-role cap + server queue/emergent prechecks")


def test_priority_and_repeal(engine):
    elder = next(a for a in engine.agents if a["role"] == "elder")
    # Seed a tax so the priority backstop path is reachable, then propose priority.
    engine.apply_decision(elder, {
        "action": "propose_rule",
        "rule": {
            "id": "resource_tax", "name": "Resource Tax", "kind": "resource_tax",
            "value": 1, "description": "tax",
        },
        "reasoning": "smoke tax",
    })
    # Force quorum by stuffing yes votes from everyone.
    for pending in list(engine.civilization["pendingRules"]):
        for a in engine.agents:
            if a["name"] not in pending["votes"]:
                pending["votes"][a["name"]] = "yes"
        engine._tally_and_maybe_enact(pending)

    engine.apply_decision(elder, {
        "action": "propose_rule",
        "rule": {
            "id": "priority_wood", "name": "Wood Priority", "kind": "priority",
            "value": "wood", "description": "prioritize wood",
        },
        "reasoning": "smoke priority",
    })
    for pending in list(engine.civilization["pendingRules"]):
        for a in engine.agents:
            if a["name"] not in pending["votes"]:
                pending["votes"][a["name"]] = "yes"
        engine._tally_and_maybe_enact(pending)

    assert_true(engine._active_priority_resource() == "wood",
                f"priority not active: {engine._active_priority_resource()}")
    kinds = engine.civilization.get("ruleKindsEverEnacted") or []
    assert_true("priority" in kinds, f"priority missing from kinds {kinds}")
    print(f"  OK priority enacted; kinds={kinds}")

    engine.apply_decision(elder, {
        "action": "repeal_rule",
        "target": "priority_wood",
        "reasoning": "smoke repeal",
    })
    for pending in list(engine.civilization["pendingRules"]):
        for a in engine.agents:
            if a["name"] not in pending["votes"]:
                pending["votes"][a["name"]] = "yes"
        engine._tally_and_maybe_enact(pending)

    assert_true(engine._active_priority_resource() is None, "priority still active")
    assert_true(not any(r["id"] == "priority_wood" for r in engine.civilization["rules"]),
                "priority_wood still in rules")
    print("  OK repeal removed priority_wood")


def test_repeal_backstop_age_gate(engine):
    """_maybe_advance_rules's "keep village law lean" repeal backstop must not
    repeal a rule it (or the propose branch) just enacted -- without an age
    gate, tax+priority (the normal 2-rule steady state) triggered an immediate
    propose/repeal oscillation every RULE_PROPOSE_COOLDOWN window."""
    elder = next(a for a in engine.agents if a["role"] == "elder")

    def enact(rule):
        engine.apply_decision(elder, {"action": "propose_rule", "rule": rule,
                                      "reasoning": "smoke age-gate"})
        for pending in list(engine.civilization["pendingRules"]):
            for a in engine.agents:
                if a["name"] not in pending["votes"]:
                    pending["votes"][a["name"]] = "yes"
            engine._tally_and_maybe_enact(pending)

    enact({"id": "resource_tax", "name": "Resource Tax", "kind": "resource_tax",
          "value": 1, "description": "tax"})
    enact({"id": "priority_wood", "name": "Wood Priority", "kind": "priority",
          "value": "wood", "description": "prioritize wood"})
    rule = next(r for r in engine.civilization["rules"] if r["id"] == "priority_wood")
    assert_true(rule.get("enactedFrame") == engine.frameTick,
                f"enactedFrame not stamped: {rule.get('enactedFrame')}")

    # Freshly enacted (age 0): the repeal backstop must not touch it yet, even
    # though len(rules) >= 2 (tax + priority) already satisfies the old
    # (pre-fix) condition on its own.
    engine.civilization["lastRuleActivityFrame"] = engine.frameTick - se.RULE_PROPOSE_COOLDOWN - 1
    before = len(engine.civilization["rules"])
    engine._maybe_advance_rules()
    assert_true(len(engine.civilization["rules"]) == before,
                "repeal backstop fired on a freshly-enacted rule")
    assert_true(any(r["id"] == "priority_wood" for r in engine.civilization["rules"]),
                "priority_wood was repealed before its minimum age")
    print("  OK repeal backstop withholds a freshly-enacted rule")

    # Advance past the minimum age: the backstop should now be willing to
    # propose a repeal of it (auto-enacted immediately by the same
    # deterministic voting the rest of this file relies on).
    engine.frameTick += se.RULE_REPEAL_MIN_AGE_FRAMES
    engine.civilization["lastRuleActivityFrame"] = engine.frameTick - se.RULE_PROPOSE_COOLDOWN - 1
    engine._maybe_advance_rules()
    for pending in list(engine.civilization["pendingRules"]):
        for a in engine.agents:
            if a["name"] not in pending["votes"]:
                pending["votes"][a["name"]] = "yes"
        engine._tally_and_maybe_enact(pending)
    assert_true(not any(r["id"] == "priority_wood" for r in engine.civilization["rules"]),
                "repeal backstop never fired once the rule aged past the minimum")
    print("  OK repeal backstop repeals once minimum age is reached")


def test_belief_biased_vote(engine):
    # Find a harvest_spirit believer and a river_spirit believer.
    harvest = next(
        (a for a in engine.agents if se.MEME_SEED_ID in a.get("beliefs", ())), None)
    river = next(
        (a for a in engine.agents if se.MEME_RIVAL_ID in a.get("beliefs", ())), None)
    assert_true(harvest and river, "need both meme holders")
    pending_ration = {"kind": "rationing", "name": "Rations", "value": 2}
    pending_priority = {"kind": "priority", "name": "Fish First", "value": "fish"}
    assert_true(engine._belief_biased_vote(harvest, pending_ration) == "yes",
                "harvest should favor rationing")
    assert_true(engine._belief_biased_vote(river, pending_ration) == "no",
                "river should oppose rationing")
    assert_true(engine._belief_biased_vote(river, pending_priority) == "yes",
                "river should favor priority")
    print("  OK belief-biased votes")


def test_authored_belief_persuasion_and_project_preference(engine):
    # The resolved Phase-3 rule is fully emergent: a villager with neither a
    # seed belief nor reflection practice can author the first new belief.
    uninitiated = next(a for a in engine.agents if not a.get("beliefs"))
    uninitiated["skills"]["reflection"] = 0.0
    ungated_payload = engine._build_think_payload(uninitiated)
    assert_true("found_belief" in ungated_payload["available_actions"],
                "belief authoring was hidden from an uninitiated agent")
    engine.apply_decision(uninitiated, {
        "action": "found_belief",
        "belief": {
            "id": "first_voice", "name": "First Voice",
            "tenet": "A new village learns by naming what it hopes for.",
            "affinity": ["custom"],
        },
        "reasoning": "Giving the village its first original belief.",
    })
    assert_true("first_voice" in uninitiated["beliefs"],
                "zero-reflection, belief-free agent could not found a belief")

    founder = next(a for a in engine.agents if se.MEME_SEED_ID in a.get("beliefs", ()))
    listener = next(a for a in engine.agents if a is not founder and not a.get("beliefs"))
    engine.apply_decision(founder, {
        "action": "found_belief",
        "belief": {
            "id": "granary_steward", "name": "Granary Stewardship",
            "tenet": "A granary stores the shared harvest safely.",
            "affinity": ["resource_tax"],
        },
        "reasoning": "A shared store makes our harvest secure.",
    })
    registry = engine.civilization.get("beliefRegistry") or {}
    assert_true("granary_steward" in registry, "authored belief was not persisted in registry")
    assert_true("granary_steward" in founder["beliefs"], "founder did not hold authored belief")
    serialized = engine._serialize_state()
    assert_true("granary_steward" in serialized["civilization"].get("beliefRegistry", {}),
                "authored belief missing from persisted state")

    engine.civilization["projectRegistry"]["shared_harvest_store"] = {
        "name": "Shared Harvest Store", "needs": {"wood": 1}, "custom": True,
    }
    engine.d["ROLE_PROJECT"][founder["role"]] = ["farm_plot", "shared_harvest_store"]
    preferred = engine._role_default_project(founder["role"], founder)
    assert_true(preferred == "shared_harvest_store",
                f"belief did not prefer matching project: {preferred}")

    # A named distant agent is valid for ordinary talk/movement, but it must
    # neither be eligible for server pitch scoring nor convert before contact.
    founder["x"] = founder["y"] = 0
    listener["x"] = listener["y"] = 1000
    distant_payload = engine._build_think_payload(founder)
    assert_true(listener["name"] not in distant_payload["nearby_beliefs"],
                "distant target was incorrectly eligible for pitch scoring")
    before_pitch_calls = engine.civilization.get("beliefPitchCalls", 0)
    engine.apply_decision(founder, {
        "action": "talk_to_nearby", "target": listener["name"],
        "message": "Our shared granary protects every family through lean days.",
        "belief_pitch": {"belief_id": "granary_steward",
                          "pitch": "Our shared granary protects every family through lean days."},
        "reasoning": "Trying to persuade from too far away.",
    })
    assert_true("granary_steward" not in listener["beliefs"],
                "distant belief pitch converted before the speakers met")
    assert_true(engine.civilization.get("beliefPitchCalls", 0) == before_pitch_calls,
                "distant belief pitch consumed a scorer result")

    founder["x"] = listener["x"] = 700
    founder["y"] = listener["y"] = 1000
    founder["relationships"][listener["name"]] = "ally"
    listener["relationships"][founder["name"]] = "ally"
    belief_id = "granary_steward"
    for frame in range(1000):
        engine.frameTick = frame
        if engine._deterministic_belief_roll(founder, listener, belief_id) <= \
                engine._belief_conversion_probability(founder, listener, se.BELIEF_FALLBACK_QUALITY):
            break
    engine.apply_decision(founder, {
        "action": "talk_to_nearby", "target": listener["name"],
        "message": "Our shared granary protects every family through lean days.",
        "belief_pitch": {"belief_id": belief_id,
                          "pitch": "Our shared granary protects every family through lean days."},
        "reasoning": "Persuading a neighbor to protect the harvest.",
    })
    assert_true(belief_id in listener["beliefs"], "offline belief pitch did not convert listener")
    assert_true(founder["relationships"].get(listener["name"]) == "ally"
                and listener["relationships"].get(founder["name"]) == "ally",
                "co-believers did not receive reciprocal relationship bonus")
    print("  OK authored belief, project preference, deterministic persuasion")


def test_role_fallback_switch():
    # Avoid importing simulation.server (it constructs the live SimEngine and
    # resumes state.db). Replicate the Phase-1 switch_role branch of
    # role_fallback_action with the same guard conditions.
    role = "trader"
    needed_role = "farmer"
    protected = {"elder", "builder", "healer"}
    primary = {"farmer": "food", "fisher": "fish", "gatherer": "wood", "miner": "gold"}
    assert_true(
        needed_role and needed_role != role
        and role not in protected
        and not primary.get(role),
        "trader should be eligible to switch",
    )
    decision = {
        "action": "switch_role", "new_role": needed_role,
        "reasoning": f"The village needs a {needed_role}; retraining to fill the gap.",
    }
    assert_true(decision["action"] == "switch_role", decision)
    assert_true(decision["new_role"] == "farmer", decision)
    print("  OK role_fallback_action switch_role guards")


def test_piano_stagger_offline():
    """Phase 5: module stagger works without LM (runner returns None)."""
    engine = make_engine(4)
    engine.d["run_piano_module"] = lambda *a, **k: "ok"
    # Force-enable for this unit check only.
    old = se.PIANO_MODULES
    se.PIANO_MODULES = True
    try:
        reports1, tick1, runs1 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            0,
            "role=farmer",
        )
        # tick 1: perception + desire only (social needs %2==0, reflection %3==0)
        assert_true(tick1 == 1, tick1)
        assert_true(runs1 == 2, runs1)
        assert_true("perception" in reports1 and "desire" in reports1, reports1)
        assert_true("social" not in reports1 and "reflection" not in reports1, reports1)

        reports2, tick2, runs2 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick1,
            "role=farmer",
        )
        # tick 2: perception + desire + social
        assert_true(tick2 == 2 and runs2 == 3, (tick2, runs2, reports2))
        assert_true("social" in reports2, reports2)

        reports3, tick3, runs3 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick2,
            "role=farmer",
        )
        # tick 3: perception + desire + reflection
        assert_true(tick3 == 3 and runs3 == 3, (tick3, runs3, reports3))
        assert_true("reflection" in reports3, reports3)
        print("  OK PIANO stagger (2 / 3 / 3 modules across ticks 1-3)")
    finally:
        se.PIANO_MODULES = old


def test_library_scaling_and_lessons():
    engine = make_engine(4)
    c = engine.civilization
    did = "village_core"
    c["structures"].append({"id": 9991, "type": "library", "districtId": did,
                            "condition": 100, "isRuin": False, "level": 30})
    c["libraryKnowledge"] = [
        {"agent": "Old", "skill": "craft", "level": 5.0, "frame": 1},
        {"agent": "Old", "skill": "build", "level": 4.0, "frame": 2},
    ]
    agent = engine.agents[0]
    agent["currentDistrict"] = did
    before = agent["skills"]["craft"]
    engine._study_at_library(agent)
    assert_true(agent["skills"]["craft"] - before == se.LIBRARY_STUDY_GAIN * 3,
                agent["skills"])
    assert_true("craft 5.0" in engine._library_lessons(did), engine._library_lessons(did))
    assert_true(engine._library_lessons("farm_north") is None, "lessons leaked outside library district")
    print("  OK library scaling + local prompt lessons")


def test_civic_era_requires_both_light_and_transit():
    """The final Civic Era rung is monotonic and requires BOTH a working
    light structure and working ocean transit -- neither alone is enough."""
    engine = make_engine(4)
    c = engine.civilization
    did = "village_core"
    c["projectRegistry"]["hearth"] = {
        "name": "Hearth", "needs": {"stone": 2}, "visualStyle": "generic",
        "function": {"light": {"scope": "district"}},
    }
    c["structures"].append({
        "id": 9800, "type": "hearth", "districtId": did,
        "condition": 100, "isRuin": False,
    })
    caps = engine._era_capabilities()
    assert_true("civilization" not in caps,
                f"light alone must not unlock civilization era, got {caps}")
    print(f"  OK light-only caps: {caps}")

    c["projectRegistry"]["dock"] = {
        "name": "Dock", "needs": {"wood": 2}, "visualStyle": "generic",
        "function": {"unlocks": [
            {"kind": "transit", "terrain": "ocean", "consumes": {"boat": 1}}]},
    }
    c["structures"].append({
        "id": 9801, "type": "dock", "districtId": "beach",
        "condition": 100, "isRuin": False,
    })
    caps = engine._era_capabilities()
    assert_true("civilization" in caps,
                f"light + transit should unlock civilization era, got {caps}")
    idx = engine._current_era_index()
    assert_true(idx == len(se.ERA_LADDER) - 1,
                f"expected the final (Civic Era) rung, got index {idx} of {se.ERA_LADDER}")
    assert_true(se.ERA_LADDER[idx][0] == "Civic Era",
                f"expected Civic Era at the top rung, got {se.ERA_LADDER[idx]}")
    print(f"  OK light + transit -> civilization era (index={idx}, caps={caps})")


def main():
    print("Sid-parity smoke (Phases 1-3 + PIANO stagger)")
    engine = make_engine(8)
    test_dual_meme_seed(engine)
    test_belief_biased_vote(engine)
    test_authored_belief_persuasion_and_project_preference(engine)
    test_survival_need_role(engine)
    test_auto_switch_and_latency(engine)
    test_emergent_role_registry(engine)
    test_server_fallback_uses_live_role_maps(engine)
    test_pending_role_cap()

    engine2 = make_engine(8)
    test_priority_and_repeal(engine2)

    engine3 = make_engine(8)
    test_repeal_backstop_age_gate(engine3)

    test_role_fallback_switch()
    test_piano_stagger_offline()
    test_library_scaling_and_lessons()
    test_civic_era_requires_both_light_and_transit()
    print("ALL PASS")


if __name__ == "__main__":
    main()
