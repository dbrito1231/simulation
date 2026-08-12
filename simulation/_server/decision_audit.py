"""Decision audit join/scoring for idea-10 (Why did you do that?).

Read-only over llm.jsonl + activity.jsonl; scoring semantics in specs/12-ops.md.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

# Bounded recent drill-down list (spec: newest first; count not fixed in spec).
RECENT_MAX = 50

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


def build_decision_audit(
    llm_path: str,
    activity_path: str,
    session_id: str,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    """Join session logs and score action-category match/mismatch per agent."""
    if not enabled:
        return {"enabled": False, "agents": [], "recent": []}

    activity_by_id: dict[str, dict[str, Any]] = {}
    for rec in _read_jsonl(activity_path):
        decision_id = rec.get("decision_id")
        if decision_id:
            activity_by_id[str(decision_id)] = rec

    per_agent: dict[str, dict[str, int]] = defaultdict(_empty_agent_stats)
    recent_scored: list[dict[str, Any]] = []

    for llm_rec in _read_jsonl(llm_path):
        if llm_rec.get("type") != "llm":
            continue
        decision = llm_rec.get("decision")
        if not isinstance(decision, dict):
            continue
        agent_name = str(llm_rec.get("agent_name") or "")
        if not agent_name:
            continue
        stats = per_agent[agent_name]

        if decision.get("_fallback") is True:
            stats["excluded_fallback"] += 1
            continue

        decision_id = decision.get("_decision_id")
        if not decision_id:
            stats["uncorrelated"] += 1
            continue

        activity = activity_by_id.get(str(decision_id))
        if activity is None:
            stats["uncorrelated"] += 1
            continue

        reasoning = str(decision.get("reasoning") or "")
        category = classify_reasoning(reasoning)
        action = decision.get("action")
        if not category:
            stats["unclassified"] += 1
            continue

        allowed = _actions_for_category(category)
        if action in allowed:
            score = "match"
            stats["matches"] += 1
        else:
            score = "mismatch"
            stats["mismatches"] += 1
        stats["scored"] += 1

        recent_scored.append({
            "decision_id": str(decision_id),
            "agent_name": agent_name,
            "frame_tick": llm_rec.get("frame_tick"),
            "action": action,
            "reasoning_category": category,
            "score": score,
            "activity_message": activity.get("message"),
        })

    agents: list[dict[str, Any]] = []
    for agent_name, stats in per_agent.items():
        scored = stats["scored"]
        if scored < 1:
            continue
        mismatches = stats["mismatches"]
        agents.append({
            "agent_name": agent_name,
            "scored": scored,
            "matches": stats["matches"],
            "mismatches": mismatches,
            "mismatch_rate": mismatches / scored,
            "excluded_fallback": stats["excluded_fallback"],
            "uncorrelated": stats["uncorrelated"],
            "unclassified": stats["unclassified"],
        })

    agents.sort(
        key=lambda row: (row["mismatch_rate"], row["mismatches"]),
        reverse=True,
    )

    recent_scored.sort(
        key=lambda row: row.get("frame_tick") or 0,
        reverse=True,
    )

    return {
        "enabled": True,
        "session_id": session_id,
        "agents": agents,
        "recent": recent_scored[:RECENT_MAX],
    }
