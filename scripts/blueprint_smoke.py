"""Deterministic blueprint validation/recovery checks (no LM Studio)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import server  # noqa: E402
import sim_engine as se  # noqa: E402


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def blueprint(identifier="archive_hall", new_resources=None, function=None):
    # Default function is intentionally distinct from any SEED_STRUCTURE_FUNCTIONS
    # entry (e.g. house's {"houses": {"every_n": 3}}) so blueprint() calls don't
    # accidentally collide with a seed structure once duplicate-effect detection
    # is exercised for real (see _canonical_effect_vector) -- callers that want
    # to test duplicate detection pass a matching `function` explicitly.
    bp = {
        "id": identifier,
        "name": "Archive Hall",
        "needs": {"wood": 2},
        "visual_style": "house",
        "function": function if function is not None else
                    {"produces": [{"resource": "wood", "amount": 1,
                                   "every_ticks": 900, "scope": "village"}]},
    }
    if new_resources is not None:
        bp["new_resources"] = new_resources
    return bp


def _canonical_effect_vector(fn):
    """Real (not no-op) vector fn so duplicate-effect detection is exercised:
    produces-resource pairs, else the houses block, else empty."""
    fn = fn or {}
    if fn.get("produces"):
        return tuple(sorted((p.get("resource"), p.get("amount")) for p in fn["produces"]))
    if fn.get("houses"):
        return tuple(sorted(fn["houses"].items()))
    return ()


def make_engine(roster_size=1):
    roles = {"elder": {"specialty": ["wood"], "preferredProject": "house"},
             "builder": {"specialty": ["wood"], "preferredProject": "house"}}
    deps = {
        "ROLES": roles,
        "ROLE_PROJECT": {"elder": "house", "builder": "house"},
        "ROLE_SKILLS": {"elder": "helps", "builder": "builds"},
        "ROLE_PRIMARY_RESOURCE": {"elder": "wood", "builder": "wood"},
        "RESOURCE_GATHER_ROLES": {"wood": ("elder", "builder")},
        "AVAILABLE_ACTIONS": list(server.DECISION_ACTIONS),
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "smoke"},
        "lm_complete": lambda *args, **kwargs: None,
        "is_scaffold_text": lambda text: False,
        "memory_store": None,
        "log_activity": lambda *args, **kwargs: None,
        "log_conversation": lambda *args, **kwargs: None,
        "log_benchmark": lambda *args, **kwargs: None,
        "validate_blueprint": server.validate_blueprint,
        "canonical_effect_vector": _canonical_effect_vector,
    }
    return se.SimEngine(deps, roster_size=roster_size)


def main():
    known = ["food", "wood"]
    at_cap = server.MAX_CUSTOM_RESOURCES + 1
    ok, reason = server.validate_blueprint(
        blueprint(), known, [], [], at_cap, [], [], None)
    check(ok, f"structure-only blueprint rejected above cap: {reason}")

    extra = [{"id": "new_fiber", "name": "New Fiber", "gather_zone": "forest"}]
    ok, reason = server.validate_blueprint(
        blueprint("fiber_hut", extra), known, [], [], at_cap, [], [], None)
    check(ok, f"new resource was incorrectly capped: {reason}")

    engine = make_engine()
    registry = engine.civilization["resourceRegistry"]
    registry["planks"] = {"name": "Planks", "crafted": True}
    registry["bricks"] = {"name": "Bricks", "crafted": True}
    check(engine._custom_resource_count() == 0, "crafted resources consumed invention slots")

    for seed_id in ("mill", "kiln", "granary"):
        ok, _ = server.validate_blueprint(
            blueprint(seed_id), known, [], [], 0, [], [], None)
        check(not ok, f"seed id {seed_id} was accepted")

    council = {
        "frame": engine.frameTick,
        "proposers": [engine.agents[0]["name"]],
        "proposals": [],
        "transcript": [],
    }
    engine.civilization["councilActive"] = council
    agent = engine.agents[0]
    engine.apply_decision(agent, {"action": "propose_blueprint", "blueprint": blueprint()})
    check(engine.civilization["pendingBlueprints"], "valid blueprint did not enter pendingBlueprints")
    check(council["proposals"], "valid blueprint did not enter active council proposals")

    engine.frameTick = council["frame"] + se.COUNCIL_TTL_FRAMES
    engine._maybe_dissolve_council()
    check(engine.civilization["councilActive"] is None, "empty council did not dissolve at TTL")

    # --- Sage review / two-stage approval / duplicate-as-upgrade / project lead ---
    e = make_engine(roster_size=2)
    sage = next(a for a in e.agents if a["role"] == "elder")
    zara = next(a for a in e.agents if a["role"] != "elder")

    # 1. Gate-block preserves proposer, sets inventionTurn + context, not spam.
    e._invention_required = lambda: True
    gate_summary = e._start_project_for(zara, "house")
    check(zara.get("inventionTurn") is True, "gate-block did not set inventionTurn")
    check(zara.get("inventionBuildContext", {}).get("type") == "house",
          "gate-block did not preserve build context")
    check(zara.get("lastProjectRejection") is not None,
          "gate-block did not set lastProjectRejection (spam not fixed)")
    check("invention" in gate_summary.lower(), f"unexpected gate summary: {gate_summary}")
    del e._invention_required  # restore the bound method for the rest of this engine's use

    def _produces(amount):
        # A distinct "amount" (1-5, validate_function_block's allowed range)
        # gives each test blueprint its own canonical_effect_vector without
        # needing a resource id that doesn't yet exist in known_resource_ids.
        return {"produces": [{"resource": "wood", "amount": amount, "every_ticks": 900, "scope": "village"}]}

    # 2. propose_blueprint -> sageReview starts pending.
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("archive_hall", function=_produces(1))})
    pending = e.civilization["pendingBlueprints"]
    bp1 = next((b for b in pending if b["id"] == "archive_hall"), None)
    check(bp1 is not None, "propose_blueprint did not land in pendingBlueprints")
    check(bp1["sageReview"] == "pending", "new blueprint did not start sageReview=pending")

    # 3. Non-elder cannot sage_review_blueprint.
    e.apply_decision(zara, {"action": "sage_review_blueprint", "target": "archive_hall",
                            "sage_decision": "approve"})
    check(bp1["sageReview"] == "pending", "non-elder sage review was not rejected")

    # 4. Elder cannot approve_blueprint before review.
    e.apply_decision(sage, {"action": "approve_blueprint", "target": "archive_hall"})
    check(any(b["id"] == "archive_hall" for b in pending),
          "approve_blueprint succeeded before sage review")
    check("archive_hall" not in e.civilization["projectRegistry"],
          "approve_blueprint created a project before sage review")

    # 5. Sage denial blocks approval and records the reason; stays queued (amnesty later).
    e.apply_decision(sage, {"action": "sage_review_blueprint", "target": "archive_hall",
                            "sage_decision": "deny", "message": "no gather capacity nearby"})
    check(bp1["sageReview"] == "denied", "sage denial did not record")
    check(bp1["sageReviewReason"] == "no gather capacity nearby", "sage denial reason not recorded")
    e.apply_decision(sage, {"action": "approve_blueprint", "target": "archive_hall"})
    check(any(b["id"] == "archive_hall" for b in pending),
          "approve_blueprint succeeded on a sage-denied blueprint")

    # 6. Sage approval -> elder approve_blueprint(target_district) -> project + lead.
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("archive_hall2", function=_produces(2))})
    e.apply_decision(sage, {"action": "sage_review_blueprint", "target": "archive_hall2",
                            "sage_decision": "approve"})
    e.apply_decision(sage, {"action": "approve_blueprint", "target": "archive_hall2",
                            "target_district": "village_core"})
    check("archive_hall2" in e.civilization["projectRegistry"], "approved blueprint did not register a project")
    check(not any(b["id"] == "archive_hall2" for b in pending), "approved blueprint stayed pending")
    dp = e.civilization["districtProjects"].get("village_core")
    check(dp is not None and dp.get("lead") == "Zara", "project lead was not set to the proposer")

    # 7. Proposer unavailable at approval time -> lead reassigned, recorded.
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("archive_hall3", function=_produces(3))})
    e.apply_decision(sage, {"action": "sage_review_blueprint", "target": "archive_hall3",
                            "sage_decision": "approve"})
    zara["incapacitated"] = True
    e.apply_decision(sage, {"action": "approve_blueprint", "target": "archive_hall3"})
    zara["incapacitated"] = False
    dp3 = next((p for p in e.civilization["districtProjects"].values()
                if p and p.get("type") == "archive_hall3"), None)
    check(dp3 is not None, "reassignment case did not create a project")
    check(dp3["lead"] != "Zara", "project lead was not reassigned off the unavailable proposer")
    check(dp3.get("leadReassigned", {}).get("from") == "Zara", "lead reassignment was not recorded")

    # 8/9. Duplicate-effect proposal is accepted (never rejected) and tagged;
    # approving it upgrades the existing structure instead of building a new one.
    # archive_hall2 was only approved (a districtProject, not yet built) in
    # step 6, so give it a standing structure instance first -- otherwise
    # there is nothing to upgrade (see step 9b) and the proposal must stay
    # pending, which is exactly the case 9b tests separately.
    e.civilization["structures"].append(
        {"id": "smoke-archive_hall2-1", "type": "archive_hall2", "level": 1,
         "districtId": "village_core"})
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("archive_hall_dup", function=_produces(2))})
    bp_dup = next(b for b in pending if b["id"] == "archive_hall_dup")
    check(bp_dup["duplicateOf"] == "archive_hall2", "duplicate-effect proposal was not tagged duplicateOf")
    e.apply_decision(sage, {"action": "sage_review_blueprint", "target": "archive_hall_dup",
                            "sage_decision": "approve"})
    e.apply_decision(sage, {"action": "approve_blueprint", "target": "archive_hall_dup"})
    check("archive_hall_dup" not in e.civilization["projectRegistry"],
          "duplicate approval created a second structure type instead of upgrading")
    check(not any(b["id"] == "archive_hall_dup" for b in pending), "duplicate blueprint stayed pending")

    # 9b. Hardening: duplicateOf pointing at a type with NO built instance yet
    # (still under construction, or -- since _effect_vector_owner_map also
    # scans pendingBlueprints -- another proposal that was never approved)
    # must NOT be popped into a doomed upgrade attempt. It stays pending so
    # the elder can retry once the original is built, or reject it outright.
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("hall_unbuilt_a", function=_produces(4))})
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("hall_unbuilt_b", function=_produces(4))})
    bp_unbuilt_b = next(b for b in pending if b["id"] == "hall_unbuilt_b")
    check(bp_unbuilt_b["duplicateOf"] == "hall_unbuilt_a",
          "duplicate-of-pending proposal was not tagged duplicateOf")
    e.apply_decision(sage, {"action": "sage_review_blueprint", "target": "hall_unbuilt_b",
                            "sage_decision": "approve"})
    unbuilt_result = e.apply_decision(sage, {"action": "approve_blueprint", "target": "hall_unbuilt_b"})
    check("not built yet" in unbuilt_result, f"unbuilt-duplicate approval was not deferred: {unbuilt_result}")
    check(any(b["id"] == "hall_unbuilt_b" for b in pending),
          "duplicate-of-unbuilt blueprint was incorrectly popped/lost")
    check("hall_unbuilt_b" not in e.civilization["projectRegistry"],
          "duplicate-of-unbuilt approval incorrectly created a project")

    # 10. Sage unavailable + timeout elapsed -> review auto-skips (no deadlock).
    e2 = make_engine(roster_size=2)
    sage2 = next(a for a in e2.agents if a["role"] == "elder")
    zara2 = next(a for a in e2.agents if a["role"] != "elder")
    e2.apply_decision(zara2, {"action": "propose_blueprint",
                              "blueprint": blueprint("timeout_hall", function={"produces": [
                                  {"resource": "wood", "amount": 4, "every_ticks": 900,
                                   "scope": "village"}]})})
    sage2["incapacitated"] = True
    e2.frameTick += se.SAGE_REVIEW_TIMEOUT_FRAMES + 10
    e2._maybe_skip_sage_review()
    bp_timeout = next(b for b in e2.civilization["pendingBlueprints"] if b["id"] == "timeout_hall")
    check(bp_timeout["sageReview"] == "skipped", "unavailable-sage timeout did not auto-skip the review")

    # 11. Sage-review nudge carries district geography/resource context.
    e.apply_decision(zara, {"action": "propose_blueprint",
                            "blueprint": blueprint("archive_hall_geo", function=_produces(5))})
    payload = e._build_think_payload(sage)
    check("SAGE REVIEW" in (payload.get("behavior_nudge") or ""),
          "elder prompt did not surface a sage-review nudge for the pending blueprint")
    context = e._sage_review_geo_context()
    check(bool(context) and context != "no district data",
          "sage review geography context helper returned nothing")

    # 12. Regression: existing council/TTL assertions above still passed to reach this point.
    print("blueprint smoke: OK")


if __name__ == "__main__":
    main()
