"""Decision audit join/scoring for idea-10 (Why did you do that?).

Read-only over llm.jsonl + activity.jsonl; scoring semantics in specs/12-ops.md.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any

# Bounded recent drill-down list (spec: newest first; count not fixed in spec).
RECENT_MAX = 50

_PARSE_CACHE: dict[
    tuple[tuple[str, int | None, int | None], tuple[str, int | None, int | None]],
    tuple[list[dict[str, Any]], list[dict[str, Any]]],
] = {}

# Full-view entry cap (spec: newest first).
FULL_ENTRIES_MAX = 200

# Outcome fail keywords — specs/12-ops.md (case-insensitive substring).
_OUTCOME_FAIL_KEYWORDS: tuple[str, ...] = (
    "cannot ",
    "found nothing",
    "has nothing",
    "failed",
    "blocked",
)

# Action-category mapping — specs/12-ops.md (first matching category wins).
_REASONING_CATEGORIES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
    (
        "gather",
        (
            "gather", "collect", "forage", "harvest", "hunt", "fish", "wood",
            "stone", "food", "resource",
        ),
        frozenset({"collect_resource", "hunt_wildlife"}),
    ),
    (
        "build",
        (
            "build", "construct", "project", "contribute", "repair", "upgrade",
            "terraform", "structure", "place block", "dig", "plant",
        ),
        frozenset({
            "start_project", "contribute_resources", "build_structure",
            "repair_structure", "upgrade_structure", "start_terraform",
            "submit_structure_sprite", "place_block", "remove_block",
            "dig_terrain", "plant_terrain",
        }),
    ),
    (
        "social",
        (
            "talk", "speak", "chat", "help", "trade", "confront", "deliver",
            "caravan", "nearby", "message",
        ),
        frozenset({
            "talk_to_nearby", "confront_agent", "trade_resource",
            "deliver_caravan", "assign_task",
        }),
    ),
    (
        "governance",
        ("rule", "vote", "treaty", "council", "repeal", "law", "assembly"),
        frozenset({
            "propose_rule", "vote_rule", "repeal_rule", "propose_treaty",
            "vote_treaty", "council_speak", "council_propose", "council_vote",
        }),
    ),
    (
        "roles",
        ("role", "switch role", "become", "elder", "builder", "healer", "blacksmith"),
        frozenset({
            "change_role", "switch_role", "propose_role", "approve_role",
            "reject_role",
        }),
    ),
    (
        "invention",
        ("blueprint", "recipe", "design", "sprite", "craft", "invent", "propose"),
        frozenset({
            "propose_blueprint", "approve_blueprint", "reject_blueprint",
            "sage_review_blueprint", "propose_recipe", "approve_recipe",
            "reject_recipe", "craft_item",
        }),
    ),
    (
        "belief",
        ("belief", "faith", "worship", "found belief", "religion"),
        frozenset({"found_belief"}),
    ),
    (
        "care",
        ("heal", "bury", "funeral", "sick", "wounded", "cemetery"),
        frozenset({"heal_agent", "bury_agent"}),
    ),
    (
        "movement",
        ("move", "travel", "district", "rest", "pause", "go to"),
        frozenset({"move_to_district", "move_to_agent", "rest"}),
    ),
    (
        "contracts",
        ("contract", "escrow", "offer work", "accept contract"),
        frozenset({"offer_contract", "accept_contract"}),
    ),
)


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError:
        pass
    return records


def _file_identity(path: str) -> tuple[str, int | None, int | None]:
    try:
        stat = os.stat(path)
    except OSError:
        return (path, None, None)
    return (path, stat.st_size, stat.st_mtime_ns)


def _read_audit_sources(
    llm_path: str, activity_path: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse current-session sources once per path/size/mtime identity."""
    key = (_file_identity(llm_path), _file_identity(activity_path))
    cached = _PARSE_CACHE.get(key)
    if cached is not None:
        return cached
    parsed = (_read_jsonl(llm_path), _read_jsonl(activity_path))
    _PARSE_CACHE.clear()
    _PARSE_CACHE[key] = parsed
    return parsed


def classify_reasoning(reasoning: str) -> str | None:
    """Return implied action category from reasoning text, or None."""
    text = (reasoning or "").lower()
    if not text:
        return None
    for category, keywords, _actions in _REASONING_CATEGORIES:
        for keyword in keywords:
            if keyword in text:
                return category
    return None


def classify_outcome(activity_message: str | None) -> str:
    """Read-side outcome heuristic over activity message — specs/12-ops.md."""
    if activity_message is None:
        return "unknown"
    msg = activity_message.lower()
    for keyword in _OUTCOME_FAIL_KEYWORDS:
        if keyword in msg:
            return "fail"
    return "ok"


def _actions_for_category(category: str) -> frozenset[str]:
    for name, _keywords, actions in _REASONING_CATEGORIES:
        if name == category:
            return actions
    return frozenset()


def _empty_agent_stats() -> dict[str, int]:
    return {
        "scored": 0,
        "matches": 0,
        "mismatches": 0,
        "excluded_fallback": 0,
        "uncorrelated": 0,
        "unclassified": 0,
    }


def _empty_outcome_stats() -> dict[str, int]:
    return {"outcome_ok": 0, "outcome_fail": 0, "outcome_unknown": 0}


def _build_full_entry(
    llm_rec: dict[str, Any],
    *,
    decision: dict[str, Any],
    agent_name: str,
    intent: str,
    category: str | None,
    activity_message: str | None,
    outcome: str,
    decision_id: Any | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "agent_name": agent_name,
        "frame_tick": llm_rec.get("frame_tick"),
        "action": decision.get("action"),
        "reasoning": str(decision.get("reasoning") or ""),
        "reasoning_category": category,
        "intent": intent,
        "activity_message": activity_message,
        "outcome": outcome,
    }
    if decision_id is not None:
        entry["decision_id"] = str(decision_id)
    if "latency_ms" in llm_rec:
        entry["latency_ms"] = llm_rec["latency_ms"]
    if "error" in llm_rec:
        entry["error"] = llm_rec["error"]
    return entry


def build_decision_audit(
    llm_path: str,
    activity_path: str,
    session_id: str,
    *,
    enabled: bool = True,
    view: str | None = None,
) -> dict[str, Any]:
    """Join session logs and score action-category match/mismatch per agent."""
    is_full = view == "full"

    if not enabled:
        result: dict[str, Any] = {"enabled": False, "agents": [], "recent": []}
        if is_full:
            result["entries"] = []
        return result

    llm_records, activity_records = _read_audit_sources(llm_path, activity_path)
    activity_by_id: dict[str, dict[str, Any]] = {}
    for rec in activity_records:
        decision_id = rec.get("decision_id")
        if decision_id:
            activity_by_id[str(decision_id)] = rec

    per_agent: dict[str, dict[str, int]] = defaultdict(_empty_agent_stats)
    per_agent_outcomes: dict[str, dict[str, int]] = defaultdict(_empty_outcome_stats)
    recent_scored: list[dict[str, Any]] = []
    full_entries: list[dict[str, Any]] = []

    for llm_rec in llm_records:
        if llm_rec.get("type") != "llm":
            continue
        decision = llm_rec.get("decision")
        if not isinstance(decision, dict):
            continue
        agent_name = str(llm_rec.get("agent_name") or "")
        if not agent_name:
            continue
        stats = per_agent[agent_name]
        reasoning = str(decision.get("reasoning") or "")
        action = decision.get("action")
        decision_id = decision.get("_decision_id")

        if decision.get("_fallback") is True:
            stats["excluded_fallback"] += 1
            if is_full:
                entry = _build_full_entry(
                    llm_rec,
                    decision=decision,
                    agent_name=agent_name,
                    intent="fallback",
                    category=classify_reasoning(reasoning),
                    activity_message=None,
                    outcome="unknown",
                    decision_id=decision_id,
                )
                full_entries.append(entry)
                per_agent_outcomes[agent_name]["outcome_unknown"] += 1
            continue

        if not decision_id:
            stats["uncorrelated"] += 1
            if is_full:
                entry = _build_full_entry(
                    llm_rec,
                    decision=decision,
                    agent_name=agent_name,
                    intent="uncorrelated",
                    category=classify_reasoning(reasoning),
                    activity_message=None,
                    outcome="unknown",
                    decision_id=None,
                )
                full_entries.append(entry)
                per_agent_outcomes[agent_name]["outcome_unknown"] += 1
            continue

        activity = activity_by_id.get(str(decision_id))
        if activity is None:
            stats["uncorrelated"] += 1
            if is_full:
                entry = _build_full_entry(
                    llm_rec,
                    decision=decision,
                    agent_name=agent_name,
                    intent="uncorrelated",
                    category=classify_reasoning(reasoning),
                    activity_message=None,
                    outcome="unknown",
                    decision_id=decision_id,
                )
                full_entries.append(entry)
                per_agent_outcomes[agent_name]["outcome_unknown"] += 1
            continue

        activity_message = activity.get("message")
        if isinstance(activity_message, str):
            activity_msg_str: str | None = activity_message
        elif activity_message is None:
            activity_msg_str = None
        else:
            activity_msg_str = str(activity_message)
        outcome = classify_outcome(activity_msg_str)
        category = classify_reasoning(reasoning)

        if not category:
            stats["unclassified"] += 1
            intent = "unclassified"
        else:
            allowed = _actions_for_category(category)
            if action in allowed:
                score = "match"
                intent = "match"
                stats["matches"] += 1
            else:
                score = "mismatch"
                intent = "mismatch"
                stats["mismatches"] += 1
            stats["scored"] += 1
            recent_scored.append({
                "decision_id": str(decision_id),
                "agent_name": agent_name,
                "frame_tick": llm_rec.get("frame_tick"),
                "action": action,
                "reasoning_category": category,
                "score": score,
                "activity_message": activity_msg_str,
            })

        if is_full:
            full_entries.append(_build_full_entry(
                llm_rec,
                decision=decision,
                agent_name=agent_name,
                intent=intent,
                category=category,
                activity_message=activity_msg_str,
                outcome=outcome,
                decision_id=decision_id,
            ))
            outcome_key = f"outcome_{outcome}"
            per_agent_outcomes[agent_name][outcome_key] += 1

    agents: list[dict[str, Any]] = []
    for agent_name, stats in per_agent.items():
        scored = stats["scored"]
        if scored < 1:
            continue
        mismatches = stats["mismatches"]
        row: dict[str, Any] = {
            "agent_name": agent_name,
            "scored": scored,
            "matches": stats["matches"],
            "mismatches": mismatches,
            "mismatch_rate": mismatches / scored,
            "excluded_fallback": stats["excluded_fallback"],
            "uncorrelated": stats["uncorrelated"],
            "unclassified": stats["unclassified"],
        }
        if is_full:
            outcomes = per_agent_outcomes[agent_name]
            row["outcome_ok"] = outcomes["outcome_ok"]
            row["outcome_fail"] = outcomes["outcome_fail"]
            row["outcome_unknown"] = outcomes["outcome_unknown"]
        agents.append(row)

    agents.sort(
        key=lambda row: (row["mismatch_rate"], row["mismatches"]),
        reverse=True,
    )

    result = {
        "enabled": True,
        "session_id": session_id,
        "agents": agents,
    }

    if is_full:
        full_entries.sort(
            key=lambda row: row.get("frame_tick") or 0,
            reverse=True,
        )
        result["entries"] = full_entries[:FULL_ENTRIES_MAX]
    else:
        recent_scored.sort(
            key=lambda row: row.get("frame_tick") or 0,
            reverse=True,
        )
        result["recent"] = recent_scored[:RECENT_MAX]

    return result
