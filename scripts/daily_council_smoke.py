"""Deterministic no-Ollama smoke for Daily Council Assembly Phase 1.

Run:
    uv run python scripts/daily_council_smoke.py

The result is always written to scripts/out/daily_council_smoke.json.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import traceback
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
import prompts  # noqa: E402

OUT_PATH = ROOT / "scripts" / "out" / "daily_council_smoke.json"


def _load_roles():
    with open(ROOT / "simulation" / "roles.json", encoding="utf-8") as fh:
        return json.load(fh)


def _resource_roles(roles):
    out = {}
    for role, definition in roles.items():
        for resource in definition.get("specialty") or []:
            out.setdefault(resource, []).append(role)
    return {resource: tuple(names) for resource, names in out.items()}


def make_engine(roster_size=8):
    roles = _load_roles()
    deps = {
        "ROLES": roles,
        "ROLE_PROJECT": {
            role: (definition.get("preferredProject")[0]
                   if isinstance(definition.get("preferredProject"), list)
                   else definition.get("preferredProject"))
            for role, definition in roles.items()
        },
        "ROLE_SKILLS": {
            role: definition.get("skill", "helps")
            for role, definition in roles.items()
        },
        "ROLE_PRIMARY_RESOURCE": {
            role: definition["specialty"][0]
            for role, definition in roles.items() if definition.get("specialty")
        },
        "RESOURCE_GATHER_ROLES": _resource_roles(roles),
        "AVAILABLE_ACTIONS": [
            "rest", "council_speak", "council_propose", "council_vote",
        ],
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "smoke"},
        "lm_complete": lambda *args, **kwargs: None,
        "is_scaffold_text": lambda text: False,
        "memory_store": None,
        "log_activity": lambda *args, **kwargs: None,
        "log_conversation": lambda *args, **kwargs: None,
        "log_benchmark": lambda *args, **kwargs: None,
        "validate_blueprint": lambda bp, *args, **kwargs: (
            (True, None) if isinstance(bp, dict) and bp.get("id")
            and bp.get("name") and bp.get("needs") and bp.get("function")
            else (False, "invalid smoke blueprint")
        ),
        "canonical_effect_vector": lambda *args, **kwargs: (),
    }
    return se.SimEngine(deps, roster_size=roster_size)


class Checks:
    def __init__(self):
        self.details = []

    def check(self, condition, name, detail=None):
        if not condition:
            raise AssertionError(f"{name}: {detail or 'condition was false'}")
        self.details.append({"name": name, "pass": True, "detail": detail})


def seat_everyone(engine):
    council = engine.civilization["dailyCouncil"]
    for seat in council["seats"]:
        agent = engine._find_agent(seat["name"])
        agent["x"], agent["y"] = seat["x"], seat["y"]
        agent["targetX"], agent["targetY"] = seat["x"], seat["y"]


def exercise_scarcity_filtering(checks):
    """Part A: obtainable-only scarce list and village-wide holdings."""
    engine = make_engine()
    c = engine.civilization
    c["resourceRegistry"]["ghost_fish"] = {
        "name": "Ghost Fish", "gatherZone": None, "color": "#607D8B", "crafted": True,
    }
    c["stockpile"]["ghost_fish"] = 0
    c["stockpile"]["gold"] = 0
    engine._invention_required = lambda: False
    engine._maybe_welcome_newcomer = lambda: None
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    council = engine.civilization.get("dailyCouncil")
    checks.check(council is not None, "scarcity_filter_convenes")
    limitations = next(
        item["detail"] for item in council["agenda"] if item["topic"] == "limitations")
    checks.check("gold" in limitations, "obtainable_zero_stockpile_reports", limitations)
    checks.check("ghost_fish" not in limitations, "orphan_resource_excluded", limitations)

    engine2 = make_engine()
    c2 = engine2.civilization
    c2["stockpile"]["gold"] = 0
    engine2.agents[0]["resources"]["gold"] = 50
    engine2._invention_required = lambda: False
    engine2._maybe_welcome_newcomer = lambda: None
    engine2.frameTick = se.DAY_FRAMES - 1
    engine2._tick_once()
    council2 = engine2.civilization.get("dailyCouncil")
    checks.check(council2 is not None, "village_holdings_convenes")
    limitations2 = next(
        item["detail"] for item in council2["agenda"] if item["topic"] == "limitations")
    checks.check("gold" not in limitations2,
                 "agent_inventory_counts_toward_holdings", limitations2)


def exercise_main_session(checks, db_path):
    engine = make_engine()
    dead = engine.agents[0]
    collapsed = engine.agents[2]
    dead["deathFrame"] = se.DAY_FRAMES - 100
    dead["buried"] = True
    dead["incapacitated"] = True
    collapsed["incapacitated"] = True
    collapsed["health"] = -100
    for agent in engine.agents:
        agent["goal"] = {"kind": "smoke"}
        agent["assignedTask"] = "smoke task"
        agent["thinkTimer"] = se.DAY_FRAMES * 2
    expected_at_convene = {
        agent["name"] for agent in engine.agents
        if agent.get("deathFrame") is None and not agent["incapacitated"]
    }

    # Make the invention agenda deterministic without exhausting the full
    # production registry in this focused smoke.
    engine._invention_required = lambda: True
    engine._maybe_welcome_newcomer = lambda: None
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    council = engine.civilization.get("dailyCouncil")
    checks.check(council is not None, "day_boundary_convenes")
    checks.check(council["day"] == 1, "meeting_id_is_day", council["day"])

    checks.check(set(council["attendees"]) == expected_at_convene, "all_available_living_attend",
                 sorted(council["attendees"]))
    checks.check(collapsed["name"] in council["excused"], "incapacitated_excused")
    checks.check(dead in engine.agents and dead.get("deathFrame") is not None,
                 "dead_retained_in_roster_with_death_frame")
    checks.check(dead["name"] not in council["attendees"], "dead_not_attendee")
    checks.check(dead["name"] not in {s["name"] for s in council["seats"]},
                 "dead_not_seated")
    checks.check(all(engine._find_agent(name)["goal"] is None
                     and engine._find_agent(name)["assignedTask"] is None
                     and engine._find_agent(name)["councilTurn"]
                     for name in council["attendees"]), "attendance_interrupts_work")
    head = next((seat for seat in council["seats"] if seat["isHead"]), None)
    checks.check(head is not None and head["role"] == "elder", "elder_at_head", head)
    checks.check(any(item["topic"] == "invention_required"
                     for item in council["agenda"]), "invention_on_agenda")
    projection = engine.snapshot()["civilization"]["dailyCouncil"]
    checks.check(projection["seats"] == council["seats"]
                 and projection["agenda"] == council["agenda"],
                 "state_projection_has_viewer_geometry_and_agenda")

    phases = [council["phase"]]
    seat_everyone(engine)
    engine._maybe_advance_daily_council()
    phases.append(council["phase"])

    # A death during discussion must vacate the ring and denominator on the
    # very next phase-machine tick.
    mid_session_dead = engine._find_agent(council["attendees"][-1])
    mid_session_dead["deathFrame"] = engine.frameTick
    mid_session_dead["buried"] = False
    engine._maybe_advance_daily_council()
    checks.check(mid_session_dead["name"] not in council["attendees"],
                 "mid_session_death_removed")
    checks.check(mid_session_dead["name"] not in {s["name"] for s in council["seats"]},
                 "mid_session_death_vacates_seat")
    seat_everyone(engine)

    total_turns = len(council["speakingOrder"]) * council["maxRounds"]
    for index in range(total_turns):
        who = council["speakingOrder"][index % len(council["speakingOrder"])]
        speaker = engine._find_agent(who)
        summary = engine.apply_decision(speaker, {
            "action": "council_speak",
            "message": f"Scripted council opinion {index + 1}",
            "feeling": "hopeful" if index % 2 == 0 else "concerned",
            "topic": "world_status", "reasoning": "smoke",
        })
        checks.check("spoke to the Daily Council" in summary,
                     f"council_speak_{index + 1}", summary)
    engine._maybe_advance_daily_council()
    phases.append(council["phase"])

    proposer = engine._find_agent(council["attendees"][1])
    summary = engine.apply_decision(proposer, {
        "action": "council_propose", "kind": "idea",
        "title": "Shared Granary",
        "detail": "Build and maintain a shared granary",
        "reasoning": "smoke",
    })
    phases.append(council["phase"])
    checks.check("proposed council idea" in summary, "council_propose_idea", summary)
    quorum = len(council["attendees"]) // 2 + 1
    checks.check(council["ballot"]["quorum"] == quorum,
                 "quorum_is_majority_of_seated", quorum)
    checks.check(dead["name"] not in council["ballot"]["votes"]
                 and mid_session_dead["name"] not in council["ballot"]["votes"],
                 "dead_excluded_from_vote")

    for name in [n for n in council["attendees"] if n != proposer["name"]][:quorum - 1]:
        summary = engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "vote": "yes", "reasoning": "smoke",
        })
        checks.check("voted yes" in summary, f"council_vote_{name}", summary)
    engine._maybe_advance_daily_council()
    phases.append(council["phase"])
    elder = next(engine._find_agent(s["name"]) for s in council["seats"] if s["isHead"])
    checks.check(elder["councilTurn"], "elder_verdict_turn_issued")
    engine.apply_decision(elder, {
        "action": "council_speak",
        "message": "The majority approves the shared granary idea.",
        "feeling": "resolute", "topic": "verdict", "reasoning": "smoke",
    })
    engine._maybe_advance_daily_council()
    phases.append(council["phase"])
    engine._maybe_advance_daily_council()
    checks.check(engine.civilization["dailyCouncil"] is None, "session_finalized")
    checks.check(phases == [
        "convening", "discussion", "proposal", "voting", "verdict", "adjourned"
    ], "phase_order", phases)

    record = engine.civilization["councilLog"][0]
    checks.check(record["trigger"] == "daily_council"
                 and record["verdict"]["winner"] == "yes", "council_log_verdict")
    checks.check(bool(engine.civilization["councilDigests"])
                 and engine.civilization["councilDigests"][0]["mood"] != "not recorded",
                 "digest_created_with_mood")
    meeting_rows = [
        row for row in engine.council_transcript_rows
        if row["meeting_id"] == record.get("meetingId", record["day"])
    ]
    checks.check(len(meeting_rows) == len(record["transcript"]),
                 "ram_rows_match_verbatim_log",
                 {"rows": len(meeting_rows), "events": len(record["transcript"])})

    payload = engine._serialize_state()
    se._write_state_db(str(db_path), payload)
    raw = se._read_state_db(str(db_path))
    checks.check(raw is not None and raw["council_transcript"] == meeting_rows,
                 "sqlite_full_transcript_roundtrip", len(meeting_rows))
    checks.check(raw["civilization"]["councilDigests"]
                 == engine.civilization["councilDigests"],
                 "sqlite_digest_roundtrip")

    original_db_path = se.DB_PATH
    try:
        se.DB_PATH = str(db_path)
        restored = make_engine()
        checks.check(restored.restore_state(), "engine_restore_succeeds")
        checks.check(restored.council_transcript_rows == meeting_rows,
                     "engine_restores_authoritative_rows")
        checks.check(restored.civilization["councilDigests"]
                     == engine.civilization["councilDigests"],
                     "engine_restores_digests")
    finally:
        se.DB_PATH = original_db_path


def exercise_ttls(checks):
    engine = make_engine(4)
    engine.frameTick = se.DAY_FRAMES
    checks.check(engine._maybe_convene_daily_council(), "ttl_fixture_convenes")
    council = engine.civilization["dailyCouncil"]
    phase_order = [council["phase"]]
    council["phaseFrame"] -= se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    phase_order.append(council["phase"])
    council["phaseFrame"] -= se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    phase_order.append(council["phase"])
    council["phaseFrame"] -= se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    phase_order.append(council["phase"])
    council["phaseFrame"] -= se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    phase_order.append(council["phase"])
    council["phaseFrame"] -= se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    phase_order.append(council["phase"])
    engine._maybe_advance_daily_council()
    checks.check(phase_order == [
        "convening", "discussion", "proposal", "verdict", "adjourned", "adjourned"
    ], "phase_ttl_escape_order", phase_order)
    checks.check(engine.civilization["dailyCouncil"] is None
                 and engine.civilization["councilLog"][0]["verdict"] is None
                 and engine.civilization["councilDigests"][0]["verdict"] is None,
                 "ttl_adjourn_records_log_and_digest")

    session_engine = make_engine(4)
    session_engine.frameTick = se.DAY_FRAMES
    session_engine._maybe_convene_daily_council()
    session = session_engine.civilization["dailyCouncil"]
    session_engine.frameTick = session["frame"] + se.DAILY_COUNCIL_SESSION_TTL_FRAMES
    session_engine._maybe_advance_daily_council()
    checks.check(session_engine.civilization["dailyCouncil"] is None
                 and session_engine.civilization["councilLog"],
                 "session_ttl_forces_recorded_adjourn")


def exercise_invention_suppression(checks):
    engine = make_engine()
    engine._invention_required = lambda: True
    engine.civilization["inventionRequiredStreak"] = se.INVENTION_BACKSTOP_STREAK
    engine._maybe_invention_backstop()
    non_elder = [a for a in engine.agents if a["role"] != "elder"]
    checks.check(engine.civilization["councilActive"] is None
                 and not any(a.get("inventionTurn") for a in non_elder),
                 "daily_council_suppresses_legacy_fanout")
    elder = next(a for a in engine.agents if a["role"] == "elder")
    engine.civilization["inventionRequiredStreak"] = se.INVENTION_BACKSTOP_STREAK
    engine.civilization["inventionBackstopFires"] = se.INVENTION_ELDER_TAKEOVER
    engine._maybe_invention_backstop()
    checks.check(elder["inventionTurn"], "elder_self_draft_escape_preserved")


def exercise_elder_eligibility(checks):
    no_elder = make_engine(4)
    for agent in no_elder.agents:
        if agent["role"] == "elder":
            agent["role"] = "trader"
    no_elder.frameTick = se.DAY_FRAMES
    checks.check(not no_elder._maybe_convene_daily_council()
                 and no_elder.civilization["dailyCouncil"] is None,
                 "no_living_elder_prevents_convene")
    checks.check(no_elder._assign_council_seats(
        [a for a in no_elder.agents if not a["incapacitated"]]) == [],
        "seat_assignment_rejects_headless_roster")

    unavailable = make_engine(4)
    elder = next(a for a in unavailable.agents if a["role"] == "elder")
    elder["incapacitated"] = True
    unavailable.frameTick = se.DAY_FRAMES
    checks.check(elder in unavailable._daily_council_living(),
                 "incapacitated_elder_remains_living_and_excusable")
    checks.check(not unavailable._maybe_convene_daily_council()
                 and unavailable.civilization["dailyCouncil"] is None,
                 "incapacitated_elder_defers_meeting")
    elder["incapacitated"] = False
    checks.check(unavailable._maybe_convene_daily_council(),
                 "meeting_convenes_when_elder_available")
    seats = unavailable.civilization["dailyCouncil"]["seats"]
    checks.check(sum(1 for seat in seats if seat["isHead"]) == 1
                 and next(seat for seat in seats if seat["isHead"])["name"] == elder["name"],
                 "recovered_elder_occupies_only_head")


def exercise_retention(checks):
    engine = make_engine()
    cap = se.DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS
    engine.council_transcript_rows = [
        {
            "meeting_id": meeting_id, "who": "Sage", "type": "adjourn",
            "text": f"meeting {meeting_id}", "feeling": None,
            "frame_tick": meeting_id * se.DAY_FRAMES, "ts": "smoke",
        }
        for meeting_id in range(cap + 3)
    ]
    engine._prune_daily_council_transcripts()
    kept = sorted({row["meeting_id"] for row in engine.council_transcript_rows})
    checks.check(len(kept) == cap and kept == list(range(3, cap + 3)),
                 "retention_keeps_newest_30_meeting_ids",
                 {"first": kept[0], "last": kept[-1], "count": len(kept)})


def exercise_rejections_and_fallback(checks):
    engine = make_engine(4)
    actor = engine.agents[0]
    outside = engine.apply_decision(actor, {
        "action": "council_speak", "message": "outside", "feeling": "calm",
    })
    checks.check("no Daily Council is active" in outside, "reject_outside_session", outside)
    engine.frameTick = se.DAY_FRAMES
    engine._maybe_convene_daily_council()
    council = engine.civilization["dailyCouncil"]
    unseated = engine._find_agent(council["speakingOrder"][0])
    rejected = engine.apply_decision(unseated, {
        "action": "council_speak", "message": "too soon", "feeling": "eager",
    })
    checks.check("not seated" in rejected, "reject_unseated", rejected)
    seat_everyone(engine)
    engine._maybe_advance_daily_council()
    wrong = engine._find_agent(council["speakingOrder"][1])
    rejected = engine.apply_decision(wrong, {
        "action": "council_speak", "message": "out of order", "feeling": "eager",
    })
    checks.check("waiting for" in rejected, "reject_wrong_speaker", rejected)
    current = engine._find_agent(council["speakingOrder"][0])
    engine._apply_rule_based_fallback(current)
    checks.check(council["nextSpeakerIdx"] == 1
                 and council["transcript"][-1]["type"] == "speak",
                 "offline_council_fallback_speaks")
    phase_reject = engine.apply_decision(current, {
        "action": "council_vote", "vote": "yes", "reasoning": "wrong phase",
    })
    checks.check("invalid during discussion phase" in phase_reject,
                 "reject_wrong_phase", phase_reject)


def _open_proposal_engine(roster_size=4):
    engine = make_engine(roster_size)
    engine.frameTick = se.DAY_FRAMES
    engine._maybe_convene_daily_council()
    seat_everyone(engine)
    council = engine.civilization["dailyCouncil"]
    engine._set_daily_council_phase(council, "proposal")
    return engine, council


def exercise_tie_break(checks):
    engine, council = _open_proposal_engine()
    elder_name = next(s["name"] for s in council["seats"] if s["isHead"])
    proposer_name = next(n for n in council["attendees"] if n != elder_name)
    engine.apply_decision(engine._find_agent(proposer_name), {
        "action": "council_propose", "kind": "idea",
        "title": "Tie-break test", "detail": "The seated elder resolves an exact tie.",
        "reasoning": "smoke",
    })
    remaining = [n for n in council["attendees"] if n != proposer_name]
    yes_name = next(n for n in remaining if n != elder_name)
    no_name = next(n for n in remaining if n not in (elder_name, yes_name))
    for name, vote in ((yes_name, "yes"), (no_name, "no"), (elder_name, "no")):
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "vote": vote, "reasoning": "smoke",
        })
    engine._maybe_advance_daily_council()
    checks.check(council["ballot"]["quorum"] == 3
                 and council["verdict"]["tally"] == {
                     "yes": 2, "no": 2, "abstain": 0,
                 }, "elder_tie_break_fixture", council["verdict"])
    checks.check(council["verdict"]["winner"] == "no"
                 and "elder tie-break" in council["verdict"]["outcome"],
                 "elder_breaks_tie", council["verdict"])


def exercise_rule_enactment(checks):
    engine, council = _open_proposal_engine()
    proposer = engine._find_agent(council["attendees"][1])
    summary = engine.apply_decision(proposer, {
        "action": "council_propose", "kind": "rule",
        "rule": {
            "id": "council_tax", "name": "Council Tax", "kind": "resource_tax",
            "value": 1, "description": "Fund the shared stockpile.",
        },
        "reasoning": "smoke",
    })
    checks.check("proposed council rule" in summary, "council_propose_rule", summary)
    voters = [n for n in council["attendees"] if n != proposer["name"]][:2]
    for name in voters:
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "vote": "yes", "reasoning": "smoke",
        })
    engine._maybe_advance_daily_council()
    checks.check(any(r.get("id") == "council_tax" and r.get("enacted")
                     for r in engine.civilization["rules"]),
                 "council_rule_uses_real_enact_path")
    checks.check("through tally" in council["verdict"]["ratification"],
                 "rule_ratification_reports_tally_path", council["verdict"])


def exercise_rule_tie_approval(checks):
    engine, council = _open_proposal_engine()
    elder_name = next(s["name"] for s in council["seats"] if s["isHead"])
    proposer_name = next(n for n in council["attendees"] if n != elder_name)
    engine.apply_decision(engine._find_agent(proposer_name), {
        "action": "council_propose", "kind": "rule",
        "rule": {
            "id": "elder_tie_rule", "name": "Elder Tie Rule",
            "kind": "resource_tax", "value": 1,
            "description": "Exercise the explicit elder tie-break exception.",
        },
        "reasoning": "smoke",
    })
    for name in [n for n in council["attendees"] if n != proposer_name]:
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote",
            "vote": "yes" if name == elder_name else "no",
            "reasoning": "smoke",
        })
    engine._maybe_advance_daily_council()
    checks.check(council["verdict"]["tally"] == {
                     "yes": 2, "no": 2, "abstain": 0,
                 }
                 and council["verdict"]["winner"] == "yes"
                 and council["verdict"]["outcome"] == "yes by elder tie-break",
                 "elder_yes_breaks_rule_tie", council["verdict"])
    checks.check(any(r.get("id") == "elder_tie_rule" and r.get("enacted")
                     for r in engine.civilization["rules"])
                 and council["verdict"]["ratification"]
                 == "rule enacted through existing tally path",
                 "rule_tie_approval_uses_existing_enact_path",
                 council["verdict"])


def exercise_blueprint_enactment(checks):
    engine, council = _open_proposal_engine()
    proposer = engine._find_agent(council["attendees"][1])
    blueprint = {
        "id": "council_shelter", "name": "Council Shelter",
        "needs": {"wood": 2}, "new_resources": [], "visual_style": "house",
        "function": {"shelter": {"capacity": 2}},
    }
    summary = engine.apply_decision(proposer, {
        "action": "council_propose", "kind": "blueprint",
        "blueprint": blueprint, "reasoning": "smoke",
    })
    checks.check("proposed council blueprint" in summary,
                 "council_propose_blueprint", summary)
    voters = [n for n in council["attendees"] if n != proposer["name"]][:2]
    for name in voters:
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "vote": "yes", "reasoning": "smoke",
        })
    engine._maybe_advance_daily_council()
    checks.check("council_shelter" in engine.civilization["projectRegistry"],
                 "council_blueprint_uses_sage_elder_paths",
                 council.get("verdict"))


def exercise_subquorum_plurality(checks):
    engine, council = _open_proposal_engine(5)
    proposer = engine._find_agent(council["attendees"][1])
    engine.apply_decision(proposer, {
        "action": "council_propose", "kind": "rule",
        "rule": {
            "id": "plurality_rule", "name": "Plurality Rule",
            "kind": "resource_tax", "value": 1,
            "description": "This must not pass without whole-village majority.",
        },
        "reasoning": "smoke",
    })
    remaining = [n for n in council["attendees"] if n != proposer["name"]]
    votes = ("yes", "no", "abstain", "abstain")
    for name, vote in zip(remaining, votes):
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "vote": vote, "reasoning": "smoke",
        })
    engine._maybe_advance_daily_council()
    checks.check(council["ballot"]["quorum"] == 3
                 and council["verdict"]["tally"] == {
                     "yes": 2, "no": 1, "abstain": 2,
                 }, "subquorum_plurality_fixture", council["verdict"])
    checks.check(council["verdict"]["winner"] == "no"
                 and council["verdict"]["outcome"]
                 == "rejected: no whole-village majority",
                 "subquorum_plurality_does_not_approve", council["verdict"])
    checks.check(not any(r.get("id") == "plurality_rule"
                         for r in engine.civilization["rules"]),
                 "subquorum_plurality_does_not_enact_rule")
    checks.check(not any(r.get("id") == "plurality_rule"
                         for r in engine.civilization["pendingRules"]),
                 "subquorum_plurality_closes_pending_rule")


def exercise_digest_prompt_and_sync(checks):
    engine = make_engine(4)
    engine.civilization["councilDigests"] = [
        {
            "day": day, "topics": ["projects", "rules"],
            "verdict": {"winner": "yes", "outcome": f"approved item {day}"},
            "mood": "hopeful",
        }
        for day in range(5, 0, -1)
    ]
    for actor in engine.agents:
        payload = engine._build_think_payload(actor)
        line = payload.get("council_digest_line") or ""
        checks.check("day 5:" in line and "day 4:" in line and "day 3:" not in line,
                     f"digest_context_bounded_{actor['name']}", line)

    server_source = (ROOT / "simulation" / "server.py").read_text(encoding="utf-8")
    prompts_source = (ROOT / "simulation" / "prompts.py").read_text(encoding="utf-8")
    engine_source = (ROOT / "simulation" / "sim_engine.py").read_text(encoding="utf-8")
    viewer_source = (ROOT / "simulation" / "index.html").read_text(encoding="utf-8")
    tree = ast.parse(server_source)
    action_names = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DECISION_ACTIONS" for t in node.targets):
            action_names = ast.literal_eval(node.value)
            break
    actions = {"council_speak", "council_propose", "council_vote"}
    checks.check(action_names is not None and len(action_names) == 43
                 and actions.issubset(action_names),
                 "action_sync_decision_actions_and_count", len(action_names or []))
    checks.check(all(action in prompts.SYSTEM_PROMPT and action in prompts.COUNCIL_SYSTEM_PROMPT
                     for action in actions), "action_sync_system_prompts")
    checks.check(all(f'elif action == "{action}"' in engine_source for action in actions),
                 "action_sync_apply_decision")
    checks.check(all(action in engine_source for action in actions)
                 and "available_actions = [" in engine_source,
                 "action_sync_available_actions")
    checks.check(all(f"{action}:" in viewer_source for action in actions),
                 "action_sync_action_labels")
    checks.check(
        "#councilAssemblyCanvas { width:min(100%, 760px, calc(100vh - 116px));"
        in viewer_source
        and "aspect-ratio:1" in viewer_source
        and "@media (max-width: 900px)" in viewer_source,
        "viewer_canvas_responsive_contract",
    )
    checks.check('"action": {"type": "string", "enum": DECISION_ACTIONS}' in server_source,
                 "action_sync_decision_schema")
    checks.check("Recent council:" in server_source
                 and "COUNCIL_DIGEST_PROMPT_ENTRIES" in engine_source,
                 "digest_prompt_slot_present")
    council_prompt = prompts.build_council_user_prompt({
        "agent_name": "Sage", "role": "elder", "council_digest_line": "day 5: projects",
        "available_actions": ["council_speak"],
        "daily_council": {
            "phase": "verdict", "round": 2, "maxRounds": 2,
            "agenda": [{"topic": "projects", "detail": "finish work"}],
            "ballot": {"title": "Shared Granary"},
            "verdict": {"winner": "yes", "outcome": "approved by majority"},
        },
    })
    checks.check("ELDER VERDICT" in council_prompt and "Recent council: day 5" in council_prompt,
                 "slim_council_prompt_and_elder_verdict")
    checks.check('data.get("council_turn")' in server_source
                 and 'get("phase") == "verdict"' in server_source
                 and '"council_verdict"' in engine_source,
                 "elder_verdict_high_stakes_routing")


def exercise_flag_off(checks):
    """Prove the rollback flag preserves the legacy invention-council path."""
    checks.check(not se.DAILY_COUNCIL_ENABLED, "flag_off_fixture")
    engine = make_engine(4)
    engine.frameTick = se.DAY_FRAMES - 1
    engine._tick_once()
    checks.check(engine.civilization.get("dailyCouncil") is None,
                 "flag_off_day_boundary_does_not_convene")

    council_actions = {"council_speak", "council_propose", "council_vote"}
    for actor in engine.agents:
        payload = engine._build_think_payload(actor)
        offered = set(payload.get("available_actions") or [])
        checks.check(not (offered & council_actions),
                     f"flag_off_no_council_actions_offered_{actor['name']}",
                     sorted(offered & council_actions))

    # A rollback can happen against a save captured mid-meeting. Its persisted
    # session/turn markers must be inert while the flag is off.
    se.DAILY_COUNCIL_ENABLED = True
    persisted = make_engine(4)
    persisted.frameTick = se.DAY_FRAMES
    persisted._maybe_convene_daily_council()
    seat_everyone(persisted)
    persisted_actor = persisted._find_agent(
        persisted.civilization["dailyCouncil"]["attendees"][0])
    se.DAILY_COUNCIL_ENABLED = False
    persisted_payload = persisted._build_think_payload(persisted_actor)
    checks.check(not persisted_payload.get("council_turn")
                 and not (set(persisted_payload.get("available_actions") or [])
                          & council_actions),
                 "flag_off_saved_live_session_is_inert",
                 persisted_payload.get("available_actions"))

    actor = engine.agents[0]
    for action, extra in (
        ("council_speak", {"message": "disabled"}),
        ("council_propose", {
            "kind": "idea", "title": "disabled", "detail": "disabled",
        }),
        ("council_vote", {"vote": "yes"}),
    ):
        summary = engine.apply_decision(actor, {
            "action": action, "reasoning": "flag-off regression", **extra,
        })
        checks.check("no Daily Council is active" in summary,
                     f"flag_off_rejects_{action}", summary)

    legacy = make_engine(4)
    legacy._invention_required = lambda: True
    legacy.civilization["inventionRequiredStreak"] = se.INVENTION_BACKSTOP_STREAK
    legacy._maybe_invention_backstop()
    checks.check(legacy.civilization.get("councilActive") is not None
                 and legacy.civilization.get("dailyCouncil") is None,
                 "flag_off_preserves_legacy_invention_council")


def main():
    checks = Checks()
    temp_dir = Path(tempfile.mkdtemp(prefix="daily-council-smoke-"))
    requested_flag = os.environ.get("DAILY_COUNCIL_TEST_FLAG")
    if requested_flag is not None:
        se.DAILY_COUNCIL_ENABLED = requested_flag.strip().lower() in {
            "1", "true", "yes", "on",
        }
    try:
        if se.DAILY_COUNCIL_ENABLED:
            exercise_scarcity_filtering(checks)
            exercise_main_session(checks, temp_dir / "state.db")
            exercise_ttls(checks)
            exercise_invention_suppression(checks)
            exercise_elder_eligibility(checks)
            exercise_retention(checks)
            exercise_rejections_and_fallback(checks)
            exercise_tie_break(checks)
            exercise_rule_enactment(checks)
            exercise_rule_tie_approval(checks)
            exercise_blueprint_enactment(checks)
            exercise_subquorum_plurality(checks)
            exercise_digest_prompt_and_sync(checks)
        else:
            exercise_flag_off(checks)
        result = {"pass": True, "assertions": checks.details}
    except Exception as exc:
        result = {
            "pass": False,
            "assertions": checks.details,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
