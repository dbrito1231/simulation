"""Smoke test for GET /state delta protocol (full → delta → unchanged).

Run: uv run python scripts/state_delta_smoke.py
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


def make_engine(roster_size=4):
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
        "RESOURCE_GATHER_ROLES": {},
        "AVAILABLE_ACTIONS": ["rest"],
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "smoke"},
        "lm_complete": lambda *a, **k: "",
        "memory_store": None,
        "log_activity": lambda *a, **k: None,
        "log_conversation": lambda *a, **k: None,
        "log_benchmark": lambda *a, **k: None,
    }
    return se.SimEngine(deps, roster_size=roster_size)


def main():
    eng = make_engine()
    eng.pause()

    full = eng.snapshot()
    assert full.get("full") is True, "first snapshot should be full"
    assert "stateGeneration" in full
    gen = full["stateGeneration"]

    with eng.lock:
        eng.frameTick = max(eng.frameTick, 1)
    ft = eng.frameTick

    unchanged = eng.snapshot_delta(ft)
    assert unchanged.get("unchanged") is True, unchanged
    assert unchanged.get("stateGeneration") == gen

    with eng.lock:
        eng.agents[0]["hunger"] = 42
        eng._mark_agent_dirty(eng.agents[0])

    delta = eng.snapshot_delta(ft)
    assert delta.get("baseFrame") == ft
    assert delta.get("stateGeneration") == gen
    assert not delta.get("full")
    assert len(delta.get("agents", [])) == 1
    assert delta["agents"][0]["hunger"] == 42
    assert "civilization" not in delta, "civ registries omitted when unchanged"

    # Gap too large → full resync
    huge = eng.snapshot_delta(max(0, ft - se.STATE_DELTA_MAX_GAP - 1))
    assert huge.get("full") is True

    print("state_delta_smoke: OK")


if __name__ == "__main__":
    main()
