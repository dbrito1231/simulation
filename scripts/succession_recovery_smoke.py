"""Deterministic regression smoke for leaderless-village succession recovery.

Run from the repository root:

    uv run python scripts/succession_recovery_smoke.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from sid_parity_smoke import assert_true, make_engine

import sim_engine as se
import prompts


def _make_leaderless():
    engine = make_engine(8)
    elder = next(a for a in engine.agents if a["role"] == "elder")
    elder["role"] = "retired_elder"
    elder["incapacitated"] = True
    engine.civilization["pendingSuccession"] = None
    engine.civilization["pendingRules"] = [
        r for r in engine.civilization["pendingRules"]
        if r.get("kind") != "succession"
    ]
    return engine, elder


def _succession_rules(engine):
    return [
        r for r in engine.civilization["pendingRules"]
        if r.get("kind") == "succession"
    ]


def _seat_everyone(engine):
    council = engine.civilization["dailyCouncil"]
    for seat in council["seats"]:
        agent = engine._find_agent(seat["name"])
        agent["x"], agent["y"] = seat["x"], seat["y"]
        agent["targetX"], agent["targetY"] = seat["x"], seat["y"]


def _advance_to_succession_voting(engine):
    council = engine.civilization["dailyCouncil"]
    _seat_everyone(engine)
    engine._maybe_advance_daily_council()
    assert_true(council["phase"] == "discussion", council["phase"])
    speakers = council["speakingOrder"][:2]
    for index, name in enumerate(speakers):
        council["nextSpeakerIdx"] = index
        summary = engine.apply_decision(engine._find_agent(name), {
            "action": "council_speak",
            "message": f"I compare the candidates on service and judgment ({index}).",
            "feeling": "concerned", "topic": "leadership_vacancy",
            "reasoning": "succession smoke discussion",
        })
        assert_true("spoke to the Daily Council" in summary, summary)
    council["nextSpeakerIdx"] = (
        len(council["speakingOrder"]) * council["maxRounds"]
    )
    engine._maybe_advance_daily_council()
    assert_true(council["phase"] == "proposal", council["phase"])
    engine._maybe_advance_daily_council()
    assert_true(council["phase"] == "voting", council["phase"])
    return council


def test_restored_leaderless_village_recovers_once():
    engine, retired = _make_leaderless()
    engine._ensure_succession_election()
    pending = engine.civilization["pendingSuccession"]
    rules = _succession_rules(engine)
    assert_true(pending is not None, "leaderless restored village got no election")
    assert_true(retired["name"] not in pending["candidates"],
                "incapacitated retired elder was nominated")
    assert_true(len(rules) == len(pending["candidates"]) > 0,
                "candidate ballots were incomplete")
    council = engine.civilization.get("dailyCouncil")
    assert_true(council and council["trigger"] == "succession",
                "leaderless recovery did not promptly convene emergency council")
    topics = {item["topic"] for item in council["agenda"]}
    assert_true({
        "world_status", "projects", "limitations", "rules",
        "ideas_and_proposals", "feelings_about_evolution",
        "leadership_vacancy",
    }.issubset(topics), f"succession council lost normal topics: {topics}")
    assert_true(council["ballot"]["candidates"] == pending["candidates"],
                "council ballot did not expose succession candidates")
    assert_true(not any(seat["isHead"] for seat in council["seats"]),
                "leaderless council invented a head seat")
    election_id = pending["electionId"]
    activity_count = len(engine.activityLog)

    engine._ensure_succession_election()
    assert_true(engine.civilization["pendingSuccession"]["electionId"] == election_id,
                "valid election was restarted")
    assert_true(len(engine.activityLog) == activity_count,
                "valid election emitted repeat activity")
    print("  OK restored leaderless village opens exactly one election")


def test_orphan_and_ineligible_ballots_restart():
    engine, _ = _make_leaderless()
    engine.civilization["pendingRules"].append({
        "id": "orphan", "kind": "succession", "candidateName": "Nobody",
        "electionId": "lost", "value": "Nobody", "votes": {},
    })
    engine._ensure_succession_election()
    pending = engine.civilization["pendingSuccession"]
    assert_true(pending and pending["electionId"] != "lost",
                "orphan ballot was not replaced")
    assert_true(not any(r["id"] == "orphan" for r in _succession_rules(engine)),
                "orphan ballot survived repair")

    stale_name = pending["candidates"][0]
    stale_agent = engine._find_agent(stale_name)
    stale_agent["incapacitated"] = True
    old_election_id = pending["electionId"]
    engine.frameTick += 1
    engine._ensure_succession_election()
    repaired = engine.civilization["pendingSuccession"]
    assert_true(repaired and repaired["electionId"] != old_election_id,
                "election with incapacitated candidate was not restarted")
    assert_true(stale_name not in repaired["candidates"],
                "incapacitated candidate survived restart")
    print("  OK orphaned and ineligible ballots restart deterministically")


def test_expired_election_crowns_safe_single_elder():
    old_daily = se.DAILY_COUNCIL_ENABLED
    se.DAILY_COUNCIL_ENABLED = False
    try:
        engine, retired = _make_leaderless()
        engine._ensure_succession_election()
        pending = engine.civilization["pendingSuccession"]
        expected = min(
            (engine._find_agent(name) for name in pending["candidates"]),
            key=lambda a: a["id"],
        )["name"]
        engine.frameTick = pending["deadline"]
        engine._maybe_resolve_stalled_succession()

        elders = [
            a for a in engine.agents
            if a.get("deathFrame") is None and a["role"] == "elder"
        ]
        assert_true(len(elders) == 1 and elders[0]["name"] == expected,
                    f"expired election did not produce one deterministic elder: {elders}")
        assert_true(not elders[0]["incapacitated"],
                    "expired election crowned an incapacitated winner")
        assert_true(retired["role"] == "retired_elder",
                    "retired elder was restored to office")
        assert_true(engine.civilization["pendingSuccession"] is None
                    and not _succession_rules(engine),
                    "resolved election left succession state behind")
        print("  OK council-off expired election retains bounded safe fallback")
    finally:
        se.DAILY_COUNCIL_ENABLED = old_daily


def test_visible_candidate_voting_and_nonfirst_winner():
    engine, _ = _make_leaderless()
    engine._ensure_succession_election()
    council = _advance_to_succession_voting(engine)
    candidates = council["ballot"]["candidates"]
    assert_true(len(candidates) >= 2, candidates)
    first, chosen = candidates[:2]
    attendees = list(council["attendees"])
    choices = [first, chosen, chosen, chosen] + ["abstain"] * max(0, len(attendees) - 4)
    before_rules = [dict(rule.get("votes") or {}) for rule in _succession_rules(engine)]
    engine._maybe_advance_rules()
    assert_true([dict(rule.get("votes") or {}) for rule in _succession_rules(engine)]
                == before_rules, "rule backstop silently manufactured succession votes")
    for name, choice in zip(attendees, choices):
        decision = {
            "action": "council_vote", "reasoning": "candidate smoke",
            **({"vote": "abstain"} if choice == "abstain" else {"candidate": choice}),
        }
        summary = engine.apply_decision(engine._find_agent(name), decision)
        assert_true("voted" in summary, summary)
    engine._maybe_advance_daily_council()
    assert_true(council["verdict"]["winner"] == chosen, council["verdict"])
    assert_true(engine._find_agent(chosen)["role"] == "elder",
                "non-first recorded plurality did not win office")
    vote_rows = [row for row in council["transcript"] if row.get("type") == "vote"]
    assert_true({row.get("text") for row in vote_rows} >= {first, chosen, "abstain"},
                f"candidate choices missing from transcript: {vote_rows}")
    assert_true(any(seat["isHead"] and seat["name"] == chosen for seat in council["seats"]),
                "winner was not refreshed into council head seat")
    projection = engine.snapshot()["civilization"]["dailyCouncil"]
    assert_true(projection["ballot"]["kind"] == "succession"
                and projection["verdict"]["winner"] == chosen
                and projection["transcript"] == council["transcript"],
                "viewer projection lacks succession tally/verdict/transcript")
    print("  OK visible distinct candidate votes elect a non-first winner")


def test_succession_tie_uses_stable_seniority():
    engine, _ = _make_leaderless()
    engine._ensure_succession_election()
    council = _advance_to_succession_voting(engine)
    first, second = council["ballot"]["candidates"][:2]
    attendees = list(council["attendees"])
    choices = [first, second] + ["abstain"] * (len(attendees) - 2)
    for name, choice in zip(attendees, choices):
        engine.apply_decision(engine._find_agent(name), {
            "action": "council_vote", "reasoning": "tie smoke",
            **({"vote": "abstain"} if choice == "abstain" else {"candidate": choice}),
        })
    expected = min(
        (engine._find_agent(first), engine._find_agent(second)),
        key=lambda candidate: candidate["id"],
    )["name"]
    engine._maybe_advance_daily_council()
    verdict = council["verdict"]
    assert_true(verdict["winner"] == expected
                and verdict["tieBreak"] == "lowest stable agent id",
                verdict)
    print("  OK exact candidate tie uses stable seniority")


def test_no_vote_timeout_uses_stable_seniority():
    engine, _ = _make_leaderless()
    engine._ensure_succession_election()
    council = _advance_to_succession_voting(engine)
    expected = min(
        (engine._find_agent(name) for name in council["ballot"]["candidates"]),
        key=lambda candidate: candidate["id"],
    )["name"]
    council["phaseFrame"] = engine.frameTick - se.DAILY_COUNCIL_PHASE_TTL_FRAMES
    engine._maybe_advance_daily_council()
    verdict = council["verdict"]
    assert_true(verdict["winner"] == expected
                and verdict["tieBreak"] == "lowest stable agent id"
                and all(value == 0 for key, value in verdict["tally"].items()
                        if key != "abstain"),
                verdict)
    assert_true(not any(rule.get("votes") for rule in _succession_rules(engine)),
                "no-vote council timeout wrote synthetic rule yes votes")
    print("  OK no-vote timeout uses stable seniority without synthetic support")


def test_prompt_and_restore_roundtrip():
    engine, _ = _make_leaderless()
    engine._ensure_succession_election()
    council = engine.civilization["dailyCouncil"]
    prompt = prompts.build_council_user_prompt({
        "agent_name": council["attendees"][0], "role": "villager",
        "available_actions": ["council_speak"], "daily_council": {
            "phase": "discussion", "round": 0, "maxRounds": 2,
            "agenda": council["agenda"], "ballot": council["ballot"],
            "currentSpeaker": council["attendees"][0],
        },
    })
    assert_true("SUCCESSION: compare these candidates" in prompt
                and all(name in prompt for name in council["ballot"]["candidates"]),
                "succession discussion prompt did not name/compare candidates")

    source = make_engine(8)
    elder = next(a for a in source.agents if a["role"] == "elder")
    elder["role"] = "retired_elder"
    elder["incapacitated"] = True
    old_db = se.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            se.DB_PATH = str(Path(tmp) / "state.db")
            source.save_state()
            restored = make_engine(8)
            assert_true(restored.restore_state(), "leaderless save did not restore")
            restored._ensure_succession_election()
            recovered = restored.civilization.get("dailyCouncil")
            assert_true(recovered and recovered["trigger"] == "succession"
                        and recovered["ballot"]["candidates"],
                        "restored leaderless save did not convene visible election")
    finally:
        se.DB_PATH = old_db

    viewer = (Path(__file__).resolve().parents[1] / "simulation" / "index.html").read_text(
        encoding="utf-8",
    )
    assert_true('ballot.kind === "succession"' in viewer
                and "Village verdict" in viewer,
                "viewer lacks succession-specific tally/verdict rendering")
    print("  OK candidate prompt, restore recovery, and viewer contract")


def test_server_candidate_normalization():
    import server

    council = {
        "phase": "voting",
        "ballot": {"kind": "succession", "candidates": ["Aria", "Luna"]},
    }
    data = {
        "role": "trader", "council_turn": True, "council_seated": True,
        "daily_council": council,
    }
    normalized = server.normalize_decision({
        "action": "council_vote", "candidate": "Luna",
        "reasoning": "Luna best reflects the recorded discussion.",
    }, data)
    assert_true(normalized.get("candidate") == "Luna",
                f"server normalization dropped candidate choice: {normalized}")
    fallback = server.normalize_decision({
        "action": "council_vote", "candidate": "Nobody", "reasoning": "invalid",
    }, data)
    assert_true(fallback.get("action") == "council_vote"
                and fallback.get("vote") == "abstain",
                f"invalid candidate fallback manufactured support: {fallback}")
    print("  OK server preserves valid candidates and invalid fallback abstains")


def test_incapacitated_formal_elder_blocks_succession():
    engine = make_engine(8)
    elder = next(a for a in engine.agents if a["role"] == "elder")
    elder["incapacitated"] = True
    engine.civilization["pendingSuccession"] = {
        "electionId": "stray", "candidates": [engine.agents[1]["name"]],
        "startFrame": 0, "deadline": se.SUCCESSION_ELECTION_TTL_FRAMES,
    }
    engine.civilization["pendingRules"].append({
        "id": "stray_ballot", "name": "Stray ballot", "kind": "succession",
        "value": engine.agents[1]["name"], "candidateName": engine.agents[1]["name"],
        "electionId": "stray", "votes": {},
    })
    engine._ensure_succession_election()

    elders = [a for a in engine._living_agents() if a["role"] == "elder"]
    assert_true(elders == [elder], "temporarily incapacitated elder was deposed")
    assert_true(engine.civilization["pendingSuccession"] is None
                and not _succession_rules(engine),
                "stray election remained active beside a formal elder")
    print("  OK temporarily incapacitated formal elder retains sole office")


def main():
    print("Succession recovery smoke")
    test_restored_leaderless_village_recovers_once()
    test_orphan_and_ineligible_ballots_restart()
    test_visible_candidate_voting_and_nonfirst_winner()
    test_succession_tie_uses_stable_seniority()
    test_no_vote_timeout_uses_stable_seniority()
    test_prompt_and_restore_roundtrip()
    test_server_candidate_normalization()
    test_expired_election_crowns_safe_single_elder()
    test_incapacitated_formal_elder_blocks_succession()
    print("ALL PASS")


if __name__ == "__main__":
    main()
