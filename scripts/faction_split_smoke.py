"""Deterministic smoke for Emergence Breakthroughs F4.1–F4.2 (Faction Split storage + scope).

Covers flag-off shape, flag-on cold-start keyed maps, legacy restore wrap,
home-settlement propose/enact, and manual two-settlement rule independence.
Ollama-free.

Run: uv run python scripts/faction_split_smoke.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def test_flag_off_no_keyed_maps():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = False
    try:
        engine = make_engine(4)
        c = engine.civilization
        assert_true("rulesBySettlement" not in c,
                    "flag off must not install rulesBySettlement")
        assert_true("beliefRegistryBySettlement" not in c,
                    "flag off must not install beliefRegistryBySettlement")
    finally:
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-off shape")


def test_flag_on_cold_start_aliases_home():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = True
    try:
        engine = make_engine(4)
        c = engine.civilization
        home = engine._primary_settlement_id()
        assert_true(home == "home", f"expected home settlement id, got {home}")
        assert_true(c["rulesBySettlement"][home] is c["rules"],
                    "home rules bucket must alias flat rules")
        assert_true(c["pendingRulesBySettlement"][home] is c["pendingRules"],
                    "home pending bucket must alias flat pendingRules")
        assert_true(c["beliefRegistryBySettlement"][home] is c["beliefRegistry"],
                    "home registry must alias flat beliefRegistry")
        assert_true(engine._rules_for_settlement(home) is c["rules"],
                    "_rules_for_settlement must return home bucket")
    finally:
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-on cold-start aliases")


def test_flag_on_restore_wraps_legacy_flat():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = False
    old_db_path = se.DB_PATH
    try:
        engine = make_engine(4)
        flat_rules = [
            {"id": "priority_wood", "name": "Wood", "kind": "priority",
             "value": "wood", "enactedFrame": 10},
        ]
        engine.civilization["rules"] = copy.deepcopy(flat_rules)
        engine.civilization["pendingRules"] = [
            {"id": "tax_fish", "name": "Fish tax", "kind": "resource_tax",
             "value": 0.1, "votes": {}},
        ]
        tmpdir = tempfile.mkdtemp()
        se.DB_PATH = str(Path(tmpdir) / "state_faction_split.db")
        engine.save_state()

        se.FACTION_SPLIT_ENABLED = True
        restored_engine = make_engine(4)
        assert_true(restored_engine.restore_state(), "restore should succeed")
        c = restored_engine.civilization
        home = restored_engine._primary_settlement_id()
        assert_true(len(c["rulesBySettlement"][home]) == len(flat_rules),
                    "wrapped rules length must match pre-wrap flat list")
        assert_true(c["rulesBySettlement"][home][0]["id"] == flat_rules[0]["id"],
                    "wrapped rule id must match")
        assert_true(c["rulesBySettlement"][home] is c["rules"],
                    "post-restore home rules must alias flat rules")
        restored_engine._rebuild_settlement_governance(home)
    finally:
        se.DB_PATH = old_db_path
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-on legacy restore wrap")


def test_flag_on_propose_enact_home():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = True
    try:
        engine = make_engine(4)
        home = engine._primary_settlement_id()
        agent = engine.agents[0]
        rule = {
            "id": "tax_smoke_home",
            "name": "Smoke Tax",
            "kind": "resource_tax",
            "value": 1,
            "description": "smoke test tax",
        }
        engine._propose_rule(agent, {"rule": rule})
        pending = engine._pending_for_settlement(home)
        ballot = next((r for r in pending if r["id"] == "tax_smoke_home"), None)
        assert_true(ballot is not None, "pending ballot must exist after propose")
        assert_true(ballot.get("settlementId") == home,
                    "domestic ballot must carry proposer settlementId")
        for voter in engine.agents:
            if voter["name"] not in ballot["votes"]:
                engine._vote_on_rule(voter, {"target": ballot["id"], "vote": "yes"})
        rules = engine._rules_for_settlement(home)
        assert_true(any(r.get("id") == "tax_smoke_home" for r in rules),
                    "rule must enact in home settlement bucket")
    finally:
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-on propose/enact home")


def test_flag_on_two_settlement_rules_independent():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = True
    try:
        engine = make_engine(4)
        c = engine.civilization
        home = engine._primary_settlement_id()
        outpost = "outpost_smoke"
        c.setdefault("rulesBySettlement", {})[outpost] = []
        c.setdefault("pendingRulesBySettlement", {})[outpost] = []
        c.setdefault("constitutionBySettlement", {})[outpost] = []
        c.setdefault("customRuleModifiersBySettlement", {})[outpost] = {}
        c.setdefault("harvestQuotasBySettlement", {})[outpost] = {}
        c.setdefault("rationingActiveBySettlement", {})[outpost] = {}
        outpost_rule = {
            "id": "priority_out_only",
            "name": "Outpost Only",
            "kind": "priority",
            "value": "wood",
            "enacted": True,
            "enactedFrame": 1,
        }
        c["rulesBySettlement"][outpost].append(outpost_rule)
        engine._rebuild_settlement_governance(outpost)
        home_ids = [r["id"] for r in engine._rules_for_settlement(home)]
        out_ids = [r["id"] for r in engine._rules_for_settlement(outpost)]
        assert_true("priority_out_only" not in home_ids,
                    "home settlement must not see outpost-only enacted rule")
        assert_true("priority_out_only" in out_ids,
                    "outpost settlement must retain its enacted rule")
        payload = engine._build_think_payload(engine.agents[0])
        active_ids = [
            r["id"] for r in payload.get("active_rules") or []
            if isinstance(r, dict) and r.get("id")
        ]
        assert_true("priority_out_only" not in active_ids,
                    "home agent think payload must not list outpost-only rule")
    finally:
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-on two-settlement independence")


def test_flag_on_scripted_faction_split():
    old = se.FACTION_SPLIT_ENABLED
    se.FACTION_SPLIT_ENABLED = True
    try:
        engine = make_engine(8)
        c = engine.civilization
        home = engine._primary_settlement_id()
        elder = engine._elder_for_settlement(home)
        assert_true(elder is not None, "home settlement must have an elder")

        fish_rule = {
            "id": "priority_fish_faction_split",
            "name": "Fish Priority",
            "kind": "priority",
            "value": "fish",
            "enacted": True,
            "enactedFrame": 1,
        }
        c["rules"].append(fish_rule)
        engine._rebuild_settlement_governance(home)

        rebels = [a for a in engine.agents if a.get("role") != "elder"][:3]
        assert_true(len(rebels) >= se.FACTION_SPLIT_MIN_CLUSTER, "need faction split cluster size")
        for agent in rebels:
            agent["beliefs"] = {se.MEME_SEED_ID}
            for other in rebels:
                if other is not agent:
                    agent.setdefault("relationships", {})[other["name"]] = "ally"
                    other.setdefault("relationships", {})[agent["name"]] = "ally"
            agent.setdefault("relationships", {})[elder["name"]] = "rival"

        cluster = engine._find_faction_split_cluster(home)
        assert_true(cluster is not None, "faction split cluster must be detected")

        outpost = "outpost_faction_split_smoke"
        rebel_dids = {a.get("currentDistrict") for a in rebels}
        outpost_did = next(
            (did for did in c["districts"] if did not in rebel_dids),
            list(c["districts"].keys())[-1],
        )
        c["districts"][outpost_did]["settlementId"] = outpost
        c["settlements"].append({
            "id": outpost,
            "name": "Faction Split Outpost",
            "districts": [outpost_did],
        })
        engine._init_faction_split_settlement_buckets(outpost)
        engine._ensure_settlement_stores()

        assert_true(engine._execute_faction_split(cluster), "faction split secession must succeed")

        raw_chronicle = c.get("chronicle") or []
        assert_true(any(e.get("kind") == "faction_split" for e in raw_chronicle),
                    "faction split must be recorded in civilization chronicle ring")
        projected = engine._chronicle_snapshot()
        assert_true(any(e.get("kind") == "faction_split" for e in projected),
                    "faction split must appear in /state chronicle projection")

        child_sid = next(s["id"] for s in c["settlements"] if s["id"] != home)
        for agent in rebels:
            assert_true(engine._settlement_id_for_agent(agent) == child_sid,
                        f"{agent['name']} must migrate to child settlement")

        child_only = {
            "id": "priority_child_faction_split",
            "name": "Child Only",
            "kind": "priority",
            "value": "wood",
            "enacted": True,
            "enactedFrame": 2,
        }
        engine._rules_for_settlement(child_sid).append(child_only)
        engine._rebuild_settlement_governance(child_sid)
        home_ids = [r["id"] for r in engine._rules_for_settlement(home)]
        child_ids = [r["id"] for r in engine._rules_for_settlement(child_sid)]
        assert_true("priority_child_faction_split" not in home_ids,
                    "home must not see child-only rule")
        assert_true("priority_child_faction_split" in child_ids,
                    "child must retain its own enacted rule")

        pending = c.get("pendingSuccession")
        assert_true(isinstance(pending, dict) and pending.get("settlementId") == child_sid,
                    "child settlement succession must be pending")
        ballot = next(r for r in c["pendingRules"] if r.get("kind") == "succession")
        for voter in engine.agents:
            if voter.get("deathFrame") or voter.get("role") == "elder":
                continue
            if voter["name"] not in ballot["votes"]:
                engine._vote_on_rule(voter, {"target": ballot["id"], "vote": "yes"})
        child_elder = engine._elder_for_settlement(child_sid)
        assert_true(child_elder is not None, "child settlement must elect an elder")
        assert_true(engine._elder_for_settlement(home) is not None,
                    "home elder must remain after faction split")

        proposer = child_elder or rebels[0]
        engine._propose_treaty(proposer, {
            "rule": {
                "id": "treaty_faction_split_smoke",
                "name": "Faction Split Trade Pact",
                "tariff": 0.1,
            },
        })
        treaty_ballot = next(
            (r for r in c["pendingRules"] if r.get("kind") == "treaty"), None)
        assert_true(treaty_ballot is not None, "treaty ballot must be proposed")
        for voter in engine.agents:
            if voter.get("deathFrame") or voter["name"] in treaty_ballot["votes"]:
                continue
            engine._vote_treaty(voter, {"target": treaty_ballot["id"], "vote": "yes"})
        assert_true(any(t.get("id") == "treaty_faction_split_smoke" for t in c.get("treaties") or []),
                    "treaty must enact across settlements after faction split")
        assert_true(engine._enacted_treaty_tariff() >= 0.1,
                    "treaty tariff must apply after faction split")
    finally:
        se.FACTION_SPLIT_ENABLED = old
    print("  OK flag-on scripted faction split")


def test_child_succession_replaces_normal_council_visibly():
    old_faction_split = se.FACTION_SPLIT_ENABLED
    old_daily = se.DAILY_COUNCIL_ENABLED
    se.FACTION_SPLIT_ENABLED = True
    se.DAILY_COUNCIL_ENABLED = True
    try:
        engine = make_engine(8)
        c = engine.civilization
        home = engine._primary_settlement_id()
        home_elder = next(a for a in engine._living_agents() if a.get("role") == "elder")

        engine.frameTick = se.DAY_FRAMES
        assert_true(engine._maybe_convene_daily_council(),
                    "normal Daily Council fixture did not convene")
        normal = c["dailyCouncil"]
        assert_true(normal.get("trigger") == "daily", normal)

        child_sid = "child_succession_smoke"
        child_did = next(did for did in c["districts"] if did != "village_core")
        c["districts"][child_did]["settlementId"] = child_sid
        c["settlements"].append({
            "id": child_sid, "name": "Child Succession Smoke",
            "districts": [child_did],
        })
        engine._init_faction_split_settlement_buckets(child_sid)
        child_agents = [a for a in engine.agents if a is not home_elder][:3]
        engine._migrate_agents_to_settlement(child_agents, child_sid, child_did)

        engine._start_succession_election(settlement_id=child_sid)
        pending_rules = [
            rule for rule in c["pendingRules"]
            if rule.get("kind") == "succession"
            and rule.get("settlementId") == child_sid
        ]
        assert_true(pending_rules and not any(rule.get("votes") for rule in pending_rules),
                    "child election fixture started with manufactured votes")

        engine._ensure_succession_election()
        council = c.get("dailyCouncil")
        child_names = {agent["name"] for agent in child_agents}
        assert_true(council and council.get("trigger") == "succession"
                    and council.get("settlementId") == child_sid,
                    "normal assembly did not become child succession council")
        assert_true(set(council.get("attendees") or []) == child_names,
                    f"succession attendees leaked across settlements: {council.get('attendees')}")
        assert_true(not any(seat.get("isHead") for seat in council.get("seats") or []),
                    "home elder incorrectly headed the child succession council")
        assert_true(council.get("ballot", {}).get("candidates")
                    == c["pendingSuccession"]["candidates"],
                    "child succession candidates are not visibly named")
        assert_true(not council["ballot"].get("votes")
                    and not any(rule.get("votes") for rule in pending_rules),
                    "succession projection manufactured candidate support")
        assert_true(engine._elder_for_settlement(home) is home_elder,
                    "child succession displaced the home elder")

        child_formal_elder = child_agents[0]
        child_formal_elder["role"] = "elder"
        child_formal_elder["incapacitated"] = True
        engine._ensure_succession_election()
        assert_true(c.get("pendingSuccession") is None
                    and not any(rule.get("kind") == "succession"
                                and rule.get("settlementId") == child_sid
                                for rule in c["pendingRules"]),
                    "incapacitated child elder did not cancel stray election")
        assert_true(c.get("dailyCouncil") is None,
                    "cancelled child election left an unresolved council active")
    finally:
        se.FACTION_SPLIT_ENABLED = old_faction_split
        se.DAILY_COUNCIL_ENABLED = old_daily
    print("  OK child succession is visible, scoped, and vote-neutral")


def main():
    print("faction_split_smoke.py")
    test_flag_off_no_keyed_maps()
    test_flag_on_cold_start_aliases_home()
    test_flag_on_restore_wraps_legacy_flat()
    test_flag_on_propose_enact_home()
    test_flag_on_two_settlement_rules_independent()
    test_flag_on_scripted_faction_split()
    test_child_succession_replaces_normal_council_visibly()
    print("ALL OK")


if __name__ == "__main__":
    main()
