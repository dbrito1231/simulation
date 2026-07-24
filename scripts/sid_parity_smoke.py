"""Deterministic smoke harness for Sid-parity Phases 1-4.

Exercises specialization need signals, priority/repeal governance, competing
memes, belief-biased votes, and effectful constitutional rules without LM
Studio. Run:

    uv run python scripts/sid_parity_smoke.py
"""
from __future__ import annotations

import re
import sys
from copy import deepcopy
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


def test_custom_rule_effect_and_constitution(engine):
    """A safe custom effect changes a real gather, amendment replaces it, and
    repeal clears the compiled lookup without reviving the old provision."""
    elder = next(a for a in engine.agents if a["role"] == "elder")
    control = make_engine(8)
    control_elder = next(a for a in control.agents if a["role"] == "elder")
    for actor in (elder, control_elder):
        actor["currentZone"] = "forest"
        actor["currentDistrict"] = "forest"

    def enact(rule):
        engine.apply_decision(elder, {"action": "propose_rule", "rule": rule,
                                      "reasoning": "smoke constitution"})
        for pending in list(engine.civilization["pendingRules"]):
            for a in engine.agents:
                pending["votes"].setdefault(a["name"], "yes")
            engine._tally_and_maybe_enact(pending)

    effect = {
        "subject": {"resource": "wood"},
        "condition": {"action": "collect_resource"},
        "modifier": {"kind": "add", "value": 2},
    }
    enact({"id": "wood_charter", "name": "Wood Charter", "kind": "custom",
           "description": "Gatherers bring in extra wood.", "effect": effect})
    assert_true(sum(p.get("id") == "wood_charter" for p in engine.civilization["constitution"]) == 1,
                f"ordinary enactment duplicated a constitution provision: {engine.civilization['constitution']}")
    assert_true("wood_charter" in engine.civilization["customRuleModifiers"],
                "custom effect was not compiled")
    elder["resources"]["wood"] = 0
    control_elder["resources"]["wood"] = 0
    engine._perform_gather(elder, "wood")
    control._perform_gather(control_elder, "wood")
    assert_true(elder["resources"]["wood"] > control_elder["resources"]["wood"],
                "custom charter did not add its modifier to the real gather output")

    amended_effect = {
        "subject": {"resource": "wood"},
        "condition": {"action": "collect_resource"},
        "modifier": {"kind": "add", "value": 1},
    }
    enact({"id": "wood_charter_amendment", "name": "Amended Wood Charter",
           "kind": "custom", "description": "Moderate the wood bonus.",
           "supersedes": "wood_charter", "effect": amended_effect})
    assert_true(not any(r["id"] == "wood_charter" for r in engine.civilization["rules"]),
                "superseded rule remained active")
    constitution = {p["id"]: p for p in engine.civilization["constitution"]}
    assert_true(constitution["wood_charter"]["status"] == "superseded"
                and constitution["wood_charter"]["supersededBy"] == "wood_charter_amendment",
                f"constitution did not record amendment: {constitution}")
    assert_true(sum(p.get("id") == "wood_charter" for p in engine.civilization["constitution"]) == 1
                and sum(p.get("id") == "wood_charter_amendment" for p in engine.civilization["constitution"]) == 1
                and not any(p.get("id") == "wood_charter" and p.get("status") == "active"
                            for p in engine.civilization["constitution"]),
                f"amendment left duplicate/stale active provisions: {engine.civilization['constitution']}")
    assert_true(engine._custom_rule_modifier("collect_resource", elder, "wood") == 1,
                "amendment did not replace the compiled custom modifier")

    engine.apply_decision(elder, {"action": "repeal_rule", "target": "wood_charter_amendment",
                                  "reasoning": "repeal charter"})
    for pending in list(engine.civilization["pendingRules"]):
        for a in engine.agents:
            pending["votes"].setdefault(a["name"], "yes")
        engine._tally_and_maybe_enact(pending)
    assert_true(not engine.civilization["customRuleModifiers"],
                "repeal left a compiled custom effect")
    assert_true(constitution["wood_charter_amendment"]["status"] == "repealed",
                f"constitution did not record repeal: {constitution}")
    assert_true(not any(p.get("id") == "wood_charter_amendment" and p.get("status") == "active"
                        for p in engine.civilization["constitution"]),
                "repeal left a stale active constitutional provision")
    repeal_control = make_engine(8)
    repeal_elder = next(a for a in repeal_control.agents if a["role"] == "elder")
    repeal_elder["currentZone"] = elder["currentZone"]
    repeal_elder["currentDistrict"] = elder["currentDistrict"]
    repeal_elder["skills"] = dict(elder["skills"])
    repeal_control.civilization["districtStocks"] = deepcopy(engine.civilization["districtStocks"])
    elder["resources"]["wood"] = 0
    repeal_elder["resources"]["wood"] = 0
    engine._perform_gather(elder, "wood")
    repeal_control._perform_gather(repeal_elder, "wood")
    assert_true(elder["resources"]["wood"] == repeal_elder["resources"]["wood"],
                "repeal did not restore the base gather computation")
    print("  OK custom effect -> amendment -> repeal restores base gather")


def test_amendment_at_active_rule_cap(engine):
    """A valid amendment replaces at MAX_ACTIVE_RULES; malformed citations do not."""
    elder = next(a for a in engine.agents if a["role"] == "elder")

    def enact(rule):
        engine.apply_decision(elder, {"action": "propose_rule", "rule": rule,
                                      "reasoning": "smoke active-rule cap"})
        for pending in list(engine.civilization["pendingRules"]):
            for a in engine.agents:
                pending["votes"].setdefault(a["name"], "yes")
            engine._tally_and_maybe_enact(pending)

    for index in range(se.MAX_ACTIVE_RULES):
        enact({"id": f"charter_{index}", "name": f"Charter {index}", "kind": "custom",
               "description": "A prose constitutional provision."})
    assert_true(len(engine.civilization["rules"]) == se.MAX_ACTIVE_RULES,
                "failed to fill the existing active-rule budget")
    amendment = {"id": "charter_amended", "name": "Amended Charter", "kind": "custom",
                 "description": "Replace the first provision.", "supersedes": "charter_0"}
    assert_true(engine._validate_rule(amendment),
                "valid amendment was rejected at MAX_ACTIVE_RULES")
    assert_true(not engine._validate_rule({**amendment, "id": "charter_self", "supersedes": "charter_self"}),
                "self-supersession was accepted")
    assert_true(not engine._validate_rule({**amendment, "id": "charter_missing", "supersedes": "missing"}),
                "non-active supersession target was accepted")
    enact(amendment)
    assert_true(len(engine.civilization["rules"]) == se.MAX_ACTIVE_RULES
                and not any(r["id"] == "charter_0" for r in engine.civilization["rules"]),
                "amendment did not replace rather than exceed the active-rule cap")
    print("  OK amendment replaces a provision at MAX_ACTIVE_RULES")


def test_rule_enactment_races(engine):
    """Passed ballots re-check their live target/budget before mutating law."""
    elder = next(a for a in engine.agents if a["role"] == "elder")

    def propose(rule):
        engine.apply_decision(elder, {"action": "propose_rule", "rule": rule,
                                      "reasoning": "smoke governance race"})
        return next(r for r in engine.civilization["pendingRules"] if r["id"] == rule["id"])

    def pass_ballot(ballot):
        for a in engine.agents:
            ballot["votes"].setdefault(a["name"], "yes")
        return engine._tally_and_maybe_enact(ballot)

    original = propose({"id": "race_original", "name": "Race Original", "kind": "custom",
                        "description": "Original provision."})
    assert_true(pass_ballot(original) == "enacted", "could not enact race target")
    first = propose({"id": "race_first_amendment", "name": "First Amendment", "kind": "custom",
                     "description": "First pending amendment.", "supersedes": "race_original"})
    second = propose({"id": "race_second_amendment", "name": "Second Amendment", "kind": "custom",
                      "description": "Second pending amendment.", "supersedes": "race_original"})
    assert_true(pass_ballot(second) == "enacted", "second amendment did not enact")
    before_stale_tally = deepcopy(engine.civilization["constitution"])
    assert_true(pass_ballot(first) == "rejected", "stale amendment enacted without its target")
    assert_true(not any(r["id"] == "race_first_amendment" for r in engine.civilization["rules"])
                and not any(p.get("id") == "race_first_amendment" for p in engine.civilization["constitution"])
                and engine.civilization["constitution"] == before_stale_tally,
                "stale amendment mutated active rules or constitution")

    budget = make_engine(8)
    budget_elder = next(a for a in budget.agents if a["role"] == "elder")

    def budget_propose(rule):
        budget.apply_decision(budget_elder, {"action": "propose_rule", "rule": rule,
                                             "reasoning": "smoke budget race"})
        return next(r for r in budget.civilization["pendingRules"] if r["id"] == rule["id"])

    def budget_pass(ballot):
        for a in budget.agents:
            ballot["votes"].setdefault(a["name"], "yes")
        return budget._tally_and_maybe_enact(ballot)

    for index in range(se.MAX_ACTIVE_RULES - 1):
        assert_true(budget_pass(budget_propose({
            "id": f"budget_{index}", "name": f"Budget {index}", "kind": "custom",
            "description": "Fill the budget."})) == "enacted", "could not seed budget")
    late = budget_propose({"id": "budget_late", "name": "Budget Late", "kind": "custom",
                           "description": "Should lose the final slot."})
    filler = budget_propose({"id": "budget_filler", "name": "Budget Filler", "kind": "custom",
                             "description": "Wins the final slot."})
    assert_true(budget_pass(filler) == "enacted", "filler did not occupy final active-rule slot")
    assert_true(budget_pass(late) == "rejected"
                and len(budget.civilization["rules"]) == se.MAX_ACTIVE_RULES
                and not any(p.get("id") == "budget_late" for p in budget.civilization["constitution"]),
                "late ordinary ballot exceeded the active-rule budget")
    print("  OK enactment rechecks stale amendment targets and rule budget")


def test_custom_rule_nonbuild_district(engine):
    """All live district ids, including forest, are valid custom selectors."""
    assert_true(not engine.civilization["districts"]["forest"].get("build_grid"),
                "smoke requires forest to be a non-buildable live district")
    effect = {
        "subject": {"district": "forest"},
        "condition": {"action": "collect_resource"},
        "modifier": {"kind": "add", "value": 1},
    }
    assert_true(engine._normalize_custom_rule_effect(effect) is not None,
                "non-buildable forest district was rejected by effect grammar")
    elder = next(a for a in engine.agents if a["role"] == "elder")
    engine.civilization["customRuleModifiers"] = {"forest_charter": effect}
    elder["currentDistrict"] = "forest"
    assert_true(engine._custom_rule_modifier("collect_resource", elder, "wood") == 1,
                "forest district subject did not match its live action context")
    elder["currentDistrict"] = "village_core"
    assert_true(engine._custom_rule_modifier("collect_resource", elder, "wood") == 0,
                "forest district subject matched outside its district")
    print("  OK custom grammar accepts and matches a non-buildable district")


def test_constitution_restore_migration(engine):
    """The restore helpers backfill old active laws once and recompile effects."""
    effect = {
        "subject": {"resource": "wood"},
        "condition": {"action": "collect_resource"},
        "modifier": {"kind": "add", "value": 1},
    }
    engine.civilization["rules"] = [{
        "id": "legacy_wood_charter", "name": "Legacy Wood Charter", "kind": "custom",
        "description": "An old saved charter.", "effect": effect, "enacted": True,
        "enactedFrame": 12,
    }]
    engine.civilization.pop("constitution", None)
    engine.civilization.pop("customRuleModifiers", None)
    engine._ensure_constitution()
    engine._rebuild_custom_rule_modifiers()
    engine._ensure_constitution()  # repeated restore must not duplicate the provision
    assert_true(len(engine.civilization["constitution"]) == 1,
                f"constitution migration duplicated legacy rules: {engine.civilization['constitution']}")
    assert_true(engine.civilization["constitution"][0]["status"] == "active"
                and "legacy_wood_charter" in engine.civilization["customRuleModifiers"],
                "legacy rule was not restored as an active compiled provision")
    # Existing duplicate rows from the short-lived first Phase-4 implementation
    # collapse deterministically to the latest provision/status.
    engine.civilization["rules"] = []
    engine.civilization["constitution"] = [
        {"id": "retired_legacy", "name": "Retired", "status": "active"},
        {"id": "retired_legacy", "name": "Retired", "status": "repealed"},
    ]
    engine._ensure_constitution()
    assert_true(engine.civilization["constitution"] == [
        {"id": "retired_legacy", "name": "Retired", "status": "repealed"}],
                "constitution dedupe did not retain the latest historical status")
    print("  OK constitution restore migration backfills once + recompiles effects")


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
    calls = []

    def stub(module, agent_name, context, frame_tick=None):
        calls.append((module, context))
        return "ok"

    engine.d["run_piano_module"] = stub
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
        # Cross-module visibility: tick 1 has nothing cached yet, so no
        # last_reports suffix should be attached to any dispatched context.
        tick1_calls = calls[:2]
        assert_true(len(tick1_calls) == 2, tick1_calls)
        assert_true(all("last_reports" not in ctx for _, ctx in tick1_calls),
                    tick1_calls)

        reports2, tick2, runs2 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick1,
            "role=farmer",
        )
        # tick 2: perception + desire + social
        assert_true(tick2 == 2 and runs2 == 3, (tick2, runs2, reports2))
        assert_true("social" in reports2, reports2)
        # Every module dispatched on tick 2 should see both tick-1 reports,
        # each labeled "1 ago" (tick2 - tick1 == 1).
        tick2_calls = calls[2:5]
        assert_true(len(tick2_calls) == 3, tick2_calls)
        assert_true(all("perception(1 ago)" in ctx and "desire(1 ago)" in ctx
                        for _, ctx in tick2_calls), tick2_calls)

        # Force social's cache entry to look 2 ticks stale (as if it were
        # last reported on tick 1 instead of tick 2), then dispatch an
        # off-tick turn (social doesn't run on odd ticks) to confirm the
        # decision payload age-labels the stale fill distinctly from the
        # bare "module:" form fresh reports use.
        cache = engine._piano_module_cache["Aria"]
        cache["social"]["tick"] = tick2 - 1

        reports3, tick3, runs3 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick2,
            "role=farmer",
        )
        # tick 3: perception + desire + reflection run fresh; social is
        # off-tick, served from cache and age-labeled "2 turns ago".
        assert_true(tick3 == 3 and runs3 == 3, (tick3, runs3, reports3))
        assert_true("reflection" in reports3, reports3)
        assert_true("social (2 turns ago):" in reports3, reports3)

        # TTL boundary: a report older than PIANO_CROSS_CONTEXT_TTL must be
        # excluded from the last_reports suffix. Force "desire"'s cache entry
        # to look stale, then confirm the next dispatch's context omits it.
        cache["desire"]["tick"] = tick3 - se.PIANO_CROSS_CONTEXT_TTL - 1
        before = len(calls)
        reports4, tick4, runs4 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick3,
            "role=farmer",
        )
        tick4_calls = calls[before:]
        assert_true(len(tick4_calls) == 3, tick4_calls)
        assert_true(all("desire(" not in ctx for _, ctx in tick4_calls),
                    tick4_calls)
        print("  OK PIANO stagger (2 / 3 / 3 modules across ticks 1-3) "
              "+ cross-module last_reports visibility, age labels, TTL cutoff")
    finally:
        se.PIANO_MODULES = old


def test_piano_cache_restore_roundtrip():
    """Phase B: _piano_module_cache survives a save/restore round-trip via
    each agent's persistence-only moduleReports mirror."""
    import tempfile

    engine = make_engine(4)

    def stub(module, agent_name, context, frame_tick=None):
        return f"{module} report for {agent_name}"

    engine.d["run_piano_module"] = stub
    old_piano = se.PIANO_MODULES
    se.PIANO_MODULES = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_roundtrip.db")
    try:
        agent_name = engine.agents[0]["name"]
        modules = {"perception": True, "social": True, "desire": True, "reflection": True}
        # Tick 1: perception + desire only. Tick 2: + social (tick % 2 == 0).
        # This leaves "social" freshly cached right before the restart.
        _, tick1, _ = engine._run_piano_modules(agent_name, modules, 0, "role=farmer")
        reports2, tick2, runs2 = engine._run_piano_modules(
            agent_name, modules, tick1, "role=farmer")
        assert_true(tick2 == 2 and "social" in reports2, (tick2, reports2))
        # Mirror the cache into the agent dict the same way the post-think
        # callback does (sim_engine.py, right after agent["moduleTick"] = new_tick).
        agent = engine._find_agent(agent_name)
        agent["moduleTick"] = tick2
        agent["moduleReports"] = {
            m: dict(v) for m, v in engine._piano_module_cache.get(agent_name, {}).items()
        }
        cache_before = deepcopy(engine._piano_module_cache.get(agent_name))
        assert_true(cache_before and "social" in cache_before,
                    "cache should hold a fresh social report before the restart")

        # Serialize + persist to a throwaway state.db, then simulate a fresh
        # process by wiping the in-memory cache (as a restart would) before
        # restoring from disk.
        se.DB_PATH = tmp_db
        engine.save_state()
        engine._piano_module_cache = {}

        restored = engine.restore_state()
        assert_true(restored, "restore_state should succeed against the just-written db")
        assert_true(agent_name in engine._piano_module_cache,
                    f"restore did not rehydrate cache for {agent_name}: "
                    f"{engine._piano_module_cache}")
        restored_entry = engine._piano_module_cache[agent_name]
        for module, report in cache_before.items():
            assert_true(restored_entry.get(module) == report,
                        f"restored cache entry for {module} mismatched: "
                        f"{restored_entry.get(module)} != {report}")

        # First post-restore turn: dispatch tick 3, where social is off-tick
        # (tick % 2 != 0). It must be served as an age-labeled fill from the
        # rehydrated cache instead of an empty slot.
        agent2 = engine._find_agent(agent_name)
        restored_tick = int(agent2.get("moduleTick") or 0)
        assert_true(restored_tick == tick2, (restored_tick, tick2))
        reports3, tick3, runs3 = engine._run_piano_modules(
            agent_name, modules, restored_tick, "role=farmer")
        assert_true(tick3 == restored_tick + 1, (tick3, restored_tick))
        assert_true("social (" in reports3 and "turns ago):" in reports3,
                    f"restored cache did not serve an off-tick social fill: {reports3}")
        print("  OK PIANO module cache rehydrates from state.db after restore "
              "and serves an off-tick fill on the first post-restore turn")
    finally:
        se.PIANO_MODULES = old_piano
        se.DB_PATH = old_db_path


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


def _legacy_select_active_defs(roster_size):
    """Frozen copy of the pre-Phase-6 _select_active_defs algorithm (roster
    <= len(AGENT_DEFS) branch), used as an independent reference so
    test_roster_default_unchanged proves today's default/range genuinely did
    not change, rather than just re-checking the (possibly also-buggy)
    current implementation against itself."""
    roster_size = max(1, min(len(se.AGENT_DEFS), roster_size))
    if roster_size >= len(se.AGENT_DEFS):
        return list(se.AGENT_DEFS)
    names = []
    for name in se.ROSTER:
        if len(names) >= roster_size:
            break
        names.append(name)
    for d in se.AGENT_DEFS:
        if len(names) >= roster_size:
            break
        if d["name"] not in names:
            names.append(d["name"])
    if "Sage" not in names:
        names[max(0, len(names) - 1)] = "Sage"
    by_name = {d["name"]: d for d in se.AGENT_DEFS}
    return [by_name[n] for n in names if n in by_name]


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
    by Phase 6 (docs/plan-sid-parity-gaps.md): MAX_ROSTER_SIZE/
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


def _district_center(bounds):
    return {"x": (bounds["x1"] + bounds["x2"]) / 2, "y": (bounds["y1"] + bounds["y2"]) / 2}


def _flat_nearby(agents, agent, radius):
    return sorted(o["name"] for o in agents
                  if o is not agent and se._dist(agent["x"], agent["y"], o["x"], o["y"]) <= radius)


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


def main():
    print("Sid-parity smoke (Phases 1-4 + PIANO stagger)")
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

    engine4 = make_engine(8)
    test_custom_rule_effect_and_constitution(engine4)

    engine5 = make_engine(8)
    test_amendment_at_active_rule_cap(engine5)

    engine6 = make_engine(8)
    test_rule_enactment_races(engine6)

    engine7 = make_engine(8)
    test_custom_rule_nonbuild_district(engine7)

    engine8 = make_engine(8)
    test_constitution_restore_migration(engine8)

    test_role_fallback_switch()
    test_piano_stagger_offline()
    test_piano_cache_restore_roundtrip()
    test_library_scaling_and_lessons()
    test_civic_era_requires_both_light_and_transit()

    print("Sid-parity smoke (Phase 6 -- scale headroom)")
    test_roster_default_unchanged()
    test_roster_headroom_generates_20()
    test_newcomer_backstop_reaches_generated_slots()
    test_district_bucket_matches_flat_scan()
    test_think_dispatch_staleness_priority()
    print("ALL PASS")


if __name__ == "__main__":
    main()
