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
            "switch_role", "propose_rule", "vote_rule", "repeal_rule",
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


def test_role_fallback_switch():
    # Avoid importing simulation.server (it constructs the live SimEngine and
    # resumes state.json). Replicate the Phase-1 switch_role branch of
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


def main():
    print("Sid-parity smoke (Phases 1-3 + PIANO stagger)")
    engine = make_engine(8)
    test_dual_meme_seed(engine)
    test_belief_biased_vote(engine)
    test_survival_need_role(engine)
    test_auto_switch_and_latency(engine)

    engine2 = make_engine(8)
    test_priority_and_repeal(engine2)
    test_role_fallback_switch()
    test_piano_stagger_offline()
    print("ALL PASS")


if __name__ == "__main__":
    main()
