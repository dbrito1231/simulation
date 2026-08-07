"""Deterministic smoke harness for Sid-parity Phases 1-4.

Exercises specialization need signals, priority/repeal governance, competing
memes, belief-biased votes, and effectful constitutional rules without LM
Studio. Run:

    uv run python scripts/sid_parity_smoke.py
"""

# Shared fixture helpers, split out of the original monolithic
# scripts/sid_parity_smoke.py (pure move, no behavior change).
from __future__ import annotations

import os
import re
import sys
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402

__all__ = [
    'os',
    're',
    'sys',
    'time',
    'deepcopy',
    'Path',
    'ROOT',
    'se',
    '_load_roles',
    '_build_resource_gather_roles',
    'make_engine',
    'assert_true',
    '_legacy_select_active_defs',
    '_district_center',
    '_flat_nearby',
    '_StubMemoryStore',
]


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
def _district_center(bounds):
    return {"x": (bounds["x1"] + bounds["x2"]) / 2, "y": (bounds["y1"] + bounds["y2"]) / 2}


def _flat_nearby(agents, agent, radius):
    return sorted(o["name"] for o in agents
                  if o is not agent and se._dist(agent["x"], agent["y"], o["x"], o["y"]) <= radius)
class _StubMemoryStore:
    """Minimal recent()/store()/size() stand-in -- just enough surface for
    _run_memory_maintenance / _run_wiki_memory_merge, no embedding math."""

    def __init__(self):
        self.entries = []

    def recent(self, agent=None, limit=12):
        matches = [e for e in self.entries if e.get("agent") == agent]
        return matches[-limit:]

    def store(self, agent, text, salience=0.5, kind="event", frame_tick=None, tier=None):
        self.entries.append({"agent": agent, "text": text, "kind": kind, "salience": salience})

    def size(self):
        return len(self.entries)

    def clean(self):
        pass
