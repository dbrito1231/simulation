# Sid-parity Phase 2 -- priority/repeal governance and effectful constitutional (custom) rules.
# Split out of the original monolithic scripts/sid_parity_smoke.py (pure move,
# no behavior change).
from _sid_parity_smoke.helpers import *  # noqa: F401,F403


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
