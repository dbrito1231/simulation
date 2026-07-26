"""Headless Daily Council retention and lifecycle soak.

The default run is deterministic and does not call Ollama:

    uv run python scripts/daily_council_soak.py

Use ``--ollama`` to let the configured local model attempt council decisions;
invalid or phase-inappropriate model output falls back to the deterministic
driver so the retention soak can always finish.

Every run uses a temporary ``state.db`` and writes a stamped result to
``scripts/out/daily_council_soak_<UTC stamp>.json``. The repository's real
``simulation/state.db`` and source files are fingerprinted before and after.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402
from daily_council_smoke import make_engine, seat_everyone  # noqa: E402


SOURCE_GUARDS = (
    ROOT / "simulation" / "sim_engine.py",
    ROOT / "simulation" / "server.py",
    ROOT / "simulation" / "prompts.py",
    ROOT / "simulation" / "roles.json",
    ROOT / "simulation" / "index.html",
)
REAL_STATE_DB = ROOT / "simulation" / "state.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest.hexdigest(),
    }


def source_fingerprints() -> dict:
    return {str(path.relative_to(ROOT)): fingerprint(path) for path in SOURCE_GUARDS}


def sqlite_metrics(db_path: Path) -> dict:
    # sqlite3.Connection's context manager commits/rolls back but does not
    # close; closing explicitly matters on Windows because the temporary
    # database must be removable before this process exits.
    with closing(sqlite3.connect(db_path)) as conn:
        row_count = conn.execute(
            "SELECT COUNT(*) FROM council_transcript"
        ).fetchone()[0]
        meeting_ids = [
            row[0] for row in conn.execute(
                "SELECT DISTINCT meeting_id FROM council_transcript "
                "ORDER BY meeting_id"
            ).fetchall()
        ]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    return {
        "sqlite_bytes": db_path.stat().st_size,
        "row_count": row_count,
        "distinct_meeting_count": len(meeting_ids),
        "oldest_meeting_id": meeting_ids[0] if meeting_ids else None,
        "newest_meeting_id": meeting_ids[-1] if meeting_ids else None,
        "meeting_ids": meeting_ids,
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist_count,
    }


class OllamaCouncilDecider:
    """Small optional local-Ollama adapter; the default soak never constructs it."""

    def __init__(self, url: str, model: str, timeout: float):
        import requests
        import prompts

        self.requests = requests
        self.system_prompt = prompts.COUNCIL_SYSTEM_PROMPT
        self.build_prompt = prompts.build_council_user_prompt
        self.url = url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout = timeout
        self.calls = 0
        self.fallbacks = 0
        self.errors = []

    def decide(self, engine, agent, expected_action: str, fallback: dict) -> dict:
        self.calls += 1
        try:
            payload = engine._build_think_payload(agent)
            response = self.requests.post(
                self.url,
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": self.build_prompt(payload)},
                    ],
                    "options": {"temperature": 0.2},
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            decision = json.loads(content)
            if isinstance(decision, dict) and decision.get("action") == expected_action:
                return decision
            raise ValueError(
                f"expected {expected_action}, got "
                f"{decision.get('action') if isinstance(decision, dict) else type(decision).__name__}"
            )
        except Exception as exc:
            self.fallbacks += 1
            self.errors.append(str(exc)[:500])
            return dict(fallback)


def deterministic_decision(expected_action: str, fallback: dict) -> dict:
    del expected_action
    return dict(fallback)


def observe_phase(council: dict, phases: list[str]) -> None:
    phase = council.get("phase")
    if phase and (not phases or phases[-1] != phase):
        phases.append(phase)


def voting_plan(attendees: list[str], proposer: str, elder: str, mode: str) -> dict[str, str]:
    """Return votes for everyone except proposer (whose proposal is an implicit yes)."""
    voters = [name for name in attendees if name != proposer]
    if mode == "majority_yes":
        return {name: "yes" for name in voters}
    if mode == "majority_no":
        return {name: "no" for name in voters}

    count = len(attendees)
    if mode == "elder_tie" and count % 2 == 0:
        target_yes = count // 2
        votes = {}
        yes_so_far = 1
        for name in voters:
            if name == elder or yes_so_far < target_yes:
                votes[name] = "yes"
                yes_so_far += 1
            else:
                votes[name] = "no"
        # If elder was encountered after the target was filled, swap one
        # non-elder yes to no so the final tally remains exactly tied.
        if sum(v == "yes" for v in votes.values()) + 1 > target_yes:
            swap = next(name for name, vote in votes.items()
                        if name != elder and vote == "yes")
            votes[swap] = "no"
        return votes

    # Non-tied sub-quorum plurality: the proposer supplies the first yes.
    quorum = count // 2 + 1
    target_yes = max(1, quorum - 1)
    target_no = max(0, target_yes - 1)
    votes = {}
    yes_left = target_yes - 1
    no_left = target_no
    for name in voters:
        if yes_left:
            votes[name] = "yes"
            yes_left -= 1
        elif no_left:
            votes[name] = "no"
            no_left -= 1
        else:
            votes[name] = "abstain"
    return votes


def run_meeting(engine, day: int, decider=None) -> dict:
    engine.frameTick = day * se.DAY_FRAMES
    if not engine._maybe_convene_daily_council():
        raise AssertionError(f"day {day}: council did not convene")
    council = engine.civilization["dailyCouncil"]
    phases: list[str] = []
    observe_phase(council, phases)

    living_available = {
        agent["name"] for agent in engine.agents
        if agent.get("deathFrame") is None and not agent.get("incapacitated")
    }
    heads = [seat for seat in council["seats"] if seat.get("isHead")]
    integrity = {
        "attendees_match_available_living": set(council["attendees"]) == living_available,
        "seat_names_unique": len({seat["name"] for seat in council["seats"]})
        == len(council["seats"]),
        "exactly_one_head": len(heads) == 1,
        "head_is_living_elder": bool(
            len(heads) == 1
            and heads[0]["name"] in living_available
            and (engine._find_agent(heads[0]["name"]) or {}).get("role") == "elder"
        ),
    }
    elder = heads[0]["name"] if heads else None

    seat_everyone(engine)
    engine._maybe_advance_daily_council()
    observe_phase(council, phases)
    if council.get("phase") != "discussion":
        raise AssertionError(f"day {day}: failed to enter discussion")

    max_speaks = len(council["speakingOrder"]) * council["maxRounds"]
    for turn in range(max_speaks):
        speaker = council["speakingOrder"][
            council["nextSpeakerIdx"] % len(council["speakingOrder"])
        ]
        agent = engine._find_agent(speaker)
        fallback = {
            "action": "council_speak",
            "message": f"Day {day} round {turn + 1}: preserve steady village progress.",
            "feeling": ("hopeful" if (day + turn) % 2 else "watchful"),
            "reasoning": "deterministic soak discussion",
        }
        decision = (decider.decide(engine, agent, "council_speak", fallback)
                    if decider else deterministic_decision("council_speak", fallback))
        result = engine.apply_decision(agent, decision)
        if "cannot council speak" in result:
            raise AssertionError(f"day {day}: council_speak rejected: {result}")
    engine._maybe_advance_daily_council()
    observe_phase(council, phases)
    if council.get("phase") != "proposal":
        raise AssertionError(f"day {day}: failed to enter proposal")

    proposer = next(name for name in council["attendees"] if name != elder)
    proposer_agent = engine._find_agent(proposer)
    fallback_proposal = {
        "action": "council_propose",
        "kind": "idea",
        "title": f"Day {day} village improvement",
        "detail": "Coordinate stores, projects, and mutual support before the next assembly.",
        "reasoning": "deterministic soak proposal",
    }
    proposal = (decider.decide(
        engine, proposer_agent, "council_propose", fallback_proposal,
    ) if decider else deterministic_decision("council_propose", fallback_proposal))
    result = engine.apply_decision(proposer_agent, proposal)
    if "cannot council propose" in result:
        # Model-created rule/blueprint proposals can be invalid against a
        # long-running world's registries. Retry through the same real action
        # handler with the deterministic advisory idea.
        if not decider:
            raise AssertionError(f"day {day}: council_propose rejected: {result}")
        decider.fallbacks += 1
        result = engine.apply_decision(proposer_agent, fallback_proposal)
        if "cannot council propose" in result:
            raise AssertionError(f"day {day}: fallback proposal rejected: {result}")
    observe_phase(council, phases)
    if council.get("phase") != "voting":
        raise AssertionError(f"day {day}: failed to enter voting")

    modes = ("majority_yes", "majority_no", "elder_tie", "subquorum_plurality")
    vote_mode = modes[(day - 1) % len(modes)]
    votes = voting_plan(council["attendees"], proposer, elder, vote_mode)
    for voter, fallback_vote in votes.items():
        agent = engine._find_agent(voter)
        fallback = {
            "action": "council_vote", "vote": fallback_vote,
            "reasoning": f"deterministic soak {vote_mode}",
        }
        decision = (decider.decide(engine, agent, "council_vote", fallback)
                    if decider else deterministic_decision("council_vote", fallback))
        result = engine.apply_decision(agent, decision)
        if "cannot council vote" in result:
            if not decider:
                raise AssertionError(f"day {day}: council_vote rejected: {result}")
            decider.fallbacks += 1
            result = engine.apply_decision(agent, fallback)
            if "cannot council vote" in result:
                raise AssertionError(f"day {day}: fallback vote rejected: {result}")

    engine._maybe_advance_daily_council()
    observe_phase(council, phases)
    if council.get("phase") != "verdict" or not council.get("verdict"):
        raise AssertionError(f"day {day}: failed to resolve ballot")

    verdict = json.loads(json.dumps(council["verdict"]))
    ballot = json.loads(json.dumps(council["ballot"]))
    elder_agent = engine._find_agent(elder)
    fallback_verdict = {
        "action": "council_speak",
        "message": f"The ruling is {verdict['outcome']}. The assembly is ratified.",
        "feeling": "resolved",
        "reasoning": "deterministic soak elder ruling",
    }
    decision = (decider.decide(engine, elder_agent, "council_speak", fallback_verdict)
                if decider else deterministic_decision("council_speak", fallback_verdict))
    result = engine.apply_decision(elder_agent, decision)
    if "cannot council speak" in result:
        if not decider:
            raise AssertionError(f"day {day}: elder verdict rejected: {result}")
        decider.fallbacks += 1
        result = engine.apply_decision(elder_agent, fallback_verdict)
        if "cannot council speak" in result:
            raise AssertionError(f"day {day}: fallback elder verdict rejected: {result}")

    engine._maybe_advance_daily_council()
    observe_phase(council, phases)
    engine._maybe_advance_daily_council()
    if engine.civilization.get("dailyCouncil") is not None:
        raise AssertionError(f"day {day}: council remained active after adjourn")

    record = engine.civilization["councilLog"][0]
    transcript = record["transcript"]
    ttl_escapes = [
        event for event in transcript
        if "ttl" in json.dumps(event, sort_keys=True).lower()
    ]
    return {
        "day": day,
        "meeting_id": council["day"],
        "convene": {
            "frame": council["frame"],
            "attendees": list(council["attendees"]),
            "excused": list(council["excused"]),
            "head": elder,
        },
        "adjourn": {
            "frame": record["end_frame"],
            "outcome": record["outcome"],
            "session_cleared": engine.civilization.get("dailyCouncil") is None,
        },
        "phase_sequence": phases,
        "vote_mode": vote_mode,
        "ballot": {
            "id": ballot["id"],
            "title": ballot["title"],
            "kind": ballot["kind"],
            "quorum": ballot["quorum"],
        },
        "vote_tally": verdict["tally"],
        "elder_ruling": {
            "elder": verdict["elderRuling"],
            "winner": verdict["winner"],
            "outcome": verdict["outcome"],
            "ratification": verdict["ratification"],
        },
        "ttl_escapes": ttl_escapes,
        "attendee_head_integrity": integrity,
        "transcript_event_count": len(transcript),
        "digest_count": len(engine.civilization.get("councilDigests") or []),
        "log_count": len(engine.civilization.get("councilLog") or []),
    }


def add_anomaly(anomalies: list[dict], kind: str, detail) -> None:
    anomalies.append({"kind": kind, "detail": detail})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=35,
        help="number of in-world days/meetings to drive (default: 35)",
    )
    parser.add_argument("--roster-size", type=int, default=8)
    parser.add_argument(
        "--ollama", action="store_true",
        help="attempt council decisions through local Ollama; deterministic fallback remains enabled",
    )
    parser.add_argument(
        "--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
    )
    parser.add_argument(
        "--ollama-model", default=os.environ.get("MODEL_SMART", "sim-smart"),
    )
    parser.add_argument("--ollama-timeout", type=float, default=90.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = ROOT / "scripts" / "out" / f"daily_council_soak_{stamp}.json"
    started = time.perf_counter()
    anomalies: list[dict] = []
    summaries: list[dict] = []
    growth_curve: list[dict] = []
    source_before = source_fingerprints()
    real_db_before = fingerprint(REAL_STATE_DB)
    engine = None
    decider = None
    temp_db_label = "isolated temporary state.db (deleted after run)"
    result = {}

    try:
        if args.days < 1:
            raise ValueError("--days must be at least 1")
        if not (2 <= args.roster_size <= se.MAX_ROSTER_SIZE):
            raise ValueError(
                f"--roster-size must be between 2 and {se.MAX_ROSTER_SIZE}"
            )
        se.DAILY_COUNCIL_ENABLED = True
        with tempfile.TemporaryDirectory(prefix="daily-council-soak-") as tmp:
            db_path = Path(tmp) / "state.db"
            se.DB_PATH = str(db_path)
            engine = make_engine(args.roster_size)
            if args.ollama:
                decider = OllamaCouncilDecider(
                    args.ollama_url, args.ollama_model, args.ollama_timeout,
                )

            for day in range(1, args.days + 1):
                summary = run_meeting(engine, day, decider)
                if not engine.save_state():
                    add_anomaly(anomalies, "save_failed", {"day": day})
                    break
                metrics = sqlite_metrics(db_path)
                summary["persistence"] = {
                    key: value for key, value in metrics.items()
                    if key != "meeting_ids"
                }
                summaries.append(summary)
                growth_curve.append({"day": day, **metrics})

            retention_cap = se.DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS
            final_metrics = growth_curve[-1] if growth_curve else {}
            completed_ids = [summary["meeting_id"] for summary in summaries]
            expected_ids = completed_ids[-retention_cap:]
            actual_ids = final_metrics.get("meeting_ids") or []
            if actual_ids != expected_ids:
                add_anomaly(anomalies, "retention_ids", {
                    "expected": expected_ids, "actual": actual_ids,
                })
            if final_metrics.get("distinct_meeting_count", 0) > retention_cap:
                add_anomaly(anomalies, "retention_cap_exceeded", final_metrics)

            retained_summaries = summaries[-retention_cap:]
            expected_rows = sum(s["transcript_event_count"] for s in retained_summaries)
            if final_metrics.get("row_count") != expected_rows:
                add_anomaly(anomalies, "transcript_row_mismatch", {
                    "expected": expected_rows,
                    "actual": final_metrics.get("row_count"),
                })

            expected_phases = [
                "convening", "discussion", "proposal", "voting", "verdict", "adjourned",
            ]
            for summary in summaries:
                day = summary["day"]
                if summary["phase_sequence"] != expected_phases:
                    add_anomaly(anomalies, "phase_sequence", {
                        "day": day, "actual": summary["phase_sequence"],
                    })
                failed_integrity = [
                    name for name, passed
                    in summary["attendee_head_integrity"].items() if not passed
                ]
                if failed_integrity:
                    add_anomaly(anomalies, "attendance_or_head", {
                        "day": day, "failed": failed_integrity,
                    })
                if summary["ttl_escapes"]:
                    add_anomaly(anomalies, "ttl_escape", {
                        "day": day, "events": summary["ttl_escapes"],
                    })
                if not summary["adjourn"]["session_cleared"]:
                    add_anomaly(anomalies, "stuck_session", {"day": day})
                if summary["digest_count"] > se.DAILY_COUNCIL_DIGEST_CAP:
                    add_anomaly(anomalies, "digest_cap_exceeded", {"day": day})
                if summary["log_count"] > se.DAILY_COUNCIL_LOG_CAP:
                    add_anomaly(anomalies, "log_cap_exceeded", {"day": day})

            cap_curve = [
                point for point in growth_curve
                if point["day"] >= retention_cap
            ]
            row_plateau = (
                len({point["row_count"] for point in cap_curve}) <= 1
                if len(cap_curve) > 1 else True
            )
            if args.days > retention_cap and not row_plateau:
                add_anomaly(anomalies, "row_growth_not_bounded", [
                    {"day": p["day"], "rows": p["row_count"]} for p in cap_curve
                ])

            if cap_curve:
                size_min = min(point["sqlite_bytes"] for point in cap_curve)
                size_max = max(point["sqlite_bytes"] for point in cap_curve)
                page_size = cap_curve[-1]["page_size"]
                size_tolerance = max(page_size * 2, int(size_min * 0.05))
                db_size_bounded = size_max - size_min <= size_tolerance
            else:
                size_min = size_max = size_tolerance = 0
                db_size_bounded = True
            if args.days > retention_cap and not db_size_bounded:
                add_anomaly(anomalies, "sqlite_growth_not_bounded", {
                    "post_cap_min": size_min,
                    "post_cap_max": size_max,
                    "tolerance": size_tolerance,
                })

            if engine.civilization.get("dailyCouncil") is not None:
                add_anomaly(anomalies, "final_session_stuck", {
                    "phase": engine.civilization["dailyCouncil"].get("phase"),
                })
            if args.days >= retention_cap + 1 and len(summaries) <= retention_cap:
                add_anomaly(anomalies, "retention_boundary_not_crossed", {
                    "requested_days": args.days, "completed": len(summaries),
                })

            retention_evidence = {
                "cap_meetings": retention_cap,
                "boundary_crossed": len(summaries) > retention_cap,
                "expected_retained_ids": expected_ids,
                "actual_retained_ids": actual_ids,
                "oldest_retained_id": actual_ids[0] if actual_ids else None,
                "newest_retained_id": actual_ids[-1] if actual_ids else None,
                "final_distinct_meeting_count": final_metrics.get(
                    "distinct_meeting_count", 0
                ),
                "final_row_count": final_metrics.get("row_count", 0),
                "expected_final_row_count": expected_rows,
                "row_count_plateau_after_cap": row_plateau,
                "sqlite_size_bounded_after_cap": db_size_bounded,
                "post_cap_sqlite_size_min": size_min,
                "post_cap_sqlite_size_max": size_max,
                "post_cap_size_tolerance": size_tolerance,
            }

        source_after = source_fingerprints()
        real_db_after = fingerprint(REAL_STATE_DB)
        if source_before != source_after:
            add_anomaly(anomalies, "source_files_changed", {
                "before": source_before, "after": source_after,
            })
        if real_db_before != real_db_after:
            add_anomaly(anomalies, "real_state_db_changed", {
                "before": real_db_before, "after": real_db_after,
            })

        result = {
            "generated_at": utc_now(),
            "pass": not anomalies and len(summaries) == args.days,
            "anomalies": anomalies,
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "configuration": {
                "requested_days": args.days,
                "roster_size": args.roster_size,
                "ollama_enabled": args.ollama,
                "ollama_url": args.ollama_url if args.ollama else None,
                "ollama_model": args.ollama_model if args.ollama else None,
                "day_frames": se.DAY_FRAMES,
                "discussion_rounds": se.DAILY_COUNCIL_DISCUSSION_ROUNDS,
                "phase_ttl_frames": se.DAILY_COUNCIL_PHASE_TTL_FRAMES,
                "session_ttl_frames": se.DAILY_COUNCIL_SESSION_TTL_FRAMES,
                "transcript_retention_meetings":
                    se.DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS,
                "digest_cap": se.DAILY_COUNCIL_DIGEST_CAP,
                "log_cap": se.DAILY_COUNCIL_LOG_CAP,
            },
            "meetings_completed": len(summaries),
            "retention_evidence": retention_evidence,
            "per_day_summaries": summaries,
            "growth_curve": growth_curve,
            "ollama": ({
                "calls": decider.calls,
                "deterministic_fallbacks": decider.fallbacks,
                "error_samples": decider.errors[:20],
            } if decider else {
                "calls": 0, "deterministic_fallbacks": 0,
                "mode": "deterministic no-Ollama",
            }),
            "isolation": {
                "state_db": temp_db_label,
                "real_state_db_unchanged": real_db_before == real_db_after,
                "source_files_unchanged": source_before == source_after,
                "guarded_source_files": list(source_before),
            },
            "artifact": str(out_path),
        }
    except Exception as exc:
        add_anomaly(anomalies, "exception", str(exc))
        result = {
            "generated_at": utc_now(),
            "pass": False,
            "anomalies": anomalies,
            "runtime_seconds": round(time.perf_counter() - started, 3),
            "configuration": vars(args),
            "meetings_completed": len(summaries),
            "per_day_summaries": summaries,
            "growth_curve": growth_curve,
            "traceback": traceback.format_exc(),
            "artifact": str(out_path),
        }
    finally:
        if engine is not None:
            engine.stop()
            engine._executor.shutdown(wait=False, cancel_futures=True)
            engine.piano_workers.shutdown(wait=False, cancel_futures=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "pass": result["pass"],
        "runtime_seconds": result["runtime_seconds"],
        "meetings_completed": result.get("meetings_completed"),
        "anomaly_count": len(result.get("anomalies") or []),
        "artifact": str(out_path),
    }, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
