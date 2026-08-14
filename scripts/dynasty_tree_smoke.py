"""Deterministic smoke for idea-02 Dynasty tree Phase 1.

Covers children[] at birth, restore back-compat, _heirs_of regression
(children-array vs legacy parents-scan), inheritedTestament snapshot,
inheritedBeliefs snapshot, and /state snapshot lineage fields +
DYNASTY_TREE_ENABLED flag echo. Ollama-free.

Run: uv run python scripts/dynasty_tree_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def _legacy_heirs_scan(engine, agent):
    """Pre-change _heirs_of parents-scan oracle."""
    children = [
        a for a in engine.agents
        if a.get("deathFrame") is None and a.get("parents")
        and agent["name"] in a["parents"]
    ]
    if children:
        return children
    return engine._eligible_adults(exclude=agent) or [
        a for a in engine.agents if a is not agent
    ]


def _heir_names(heirs):
    return {a["name"] for a in heirs}


def test_cold_start_children_empty():
    old_l = se.LIFECYCLE_ENABLED
    se.LIFECYCLE_ENABLED = True
    try:
        engine = make_engine(4)
        for agent in engine.agents:
            assert_true(agent.get("children") == [], f"{agent['name']} children not []")
            assert_true(agent.get("inheritedTestament") == [],
                        f"{agent['name']} inheritedTestament not []")
            assert_true(agent.get("inheritedBeliefs") == [],
                        f"{agent['name']} inheritedBeliefs not []")
    finally:
        se.LIFECYCLE_ENABLED = old_l
    print("  OK cold-start children/inheritance fields")


def test_birth_appends_children():
    old_l = se.LIFECYCLE_ENABLED
    se.LIFECYCLE_ENABLED = True
    try:
        engine = make_engine(4)
        parent_a, parent_b = engine.agents[0], engine.agents[1]
        before_a = list(parent_a.get("children") or [])
        before_b = list(parent_b.get("children") or [])
        engine._spawn_newborn(parent_a, parent_b)
        newborn = engine.agents[-1]
        assert_true(newborn["name"] in parent_a["children"],
                    "newborn missing from parent_a children")
        assert_true(newborn["name"] in parent_b["children"],
                    "newborn missing from parent_b children")
        assert_true(parent_a["children"] == before_a + [newborn["name"]],
                    "parent_a children append wrong")
        assert_true(parent_b["children"] == before_b + [newborn["name"]],
                    "parent_b children append wrong")
    finally:
        se.LIFECYCLE_ENABLED = old_l
    print("  OK birth appends to both parents")


def test_restore_back_compat():
    old_l = se.LIFECYCLE_ENABLED
    se.LIFECYCLE_ENABLED = True
    old_db_path = se.DB_PATH
    try:
        engine = make_engine(2)
        agent = engine.agents[0]
        agent["parents"] = ["AncestorA", "AncestorB"]
        for key in ("children", "inheritedTestament", "inheritedBeliefs"):
            agent.pop(key, None)
        tmpdir = tempfile.mkdtemp()
        se.DB_PATH = str(Path(tmpdir) / "state_dynasty.db")
        engine.save_state()
        restored = engine.restore_state()
        assert_true(restored, "restore_state should succeed")
        restored_agent = next(a for a in engine.agents if a["name"] == agent["name"])
        assert_true(restored_agent.get("children") == [], "children not back-filled")
        assert_true(restored_agent.get("inheritedTestament") == [],
                    "inheritedTestament not back-filled")
        assert_true(restored_agent.get("inheritedBeliefs") == [],
                    "inheritedBeliefs not back-filled")
    finally:
        se.DB_PATH = old_db_path
        se.LIFECYCLE_ENABLED = old_l
    print("  OK restore back-compat setdefaults")


def _build_heirs_fixture(engine):
    """Parent with two living children and one dead child."""
    parent = engine.agents[0]
    parent["children"] = []
    parent["deathFrame"] = None
    living_a = engine.agents[1]
    living_b = engine.agents[2]
    dead_child = engine.agents[3]
    for child, name in ((living_a, "ChildLiveA"), (living_b, "ChildLiveB"),
                        (dead_child, "ChildDead")):
        child["name"] = name
        child["parents"] = [parent["name"], "CoParent"]
        child["deathFrame"] = None
        parent["children"].append(name)
    dead_child["deathFrame"] = 100
    engine.agent_names = {a["name"] for a in engine.agents}
    return parent, {living_a["name"], living_b["name"]}


def test_heirs_of_regression():
    old_d, old_l = se.DYNASTY_TREE_ENABLED, se.LIFECYCLE_ENABLED
    se.LIFECYCLE_ENABLED = True
    try:
        engine = make_engine(4)
        parent, expected_living = _build_heirs_fixture(engine)
        oracle = _heir_names(_legacy_heirs_scan(engine, parent))

        se.DYNASTY_TREE_ENABLED = True
        rewired = _heir_names(engine._heirs_of(parent))
        assert_true(rewired == oracle,
                    f"flag-on heirs mismatch: rewired={rewired} oracle={oracle}")
        assert_true(expected_living.issubset(rewired),
                    f"living children missing from heirs: {rewired}")

        se.DYNASTY_TREE_ENABLED = False
        flag_off = _heir_names(engine._heirs_of(parent))
        assert_true(flag_off == oracle,
                    f"flag-off heirs mismatch: got={flag_off} oracle={oracle}")
    finally:
        se.DYNASTY_TREE_ENABLED = old_d
        se.LIFECYCLE_ENABLED = old_l
    print("  OK _heirs_of regression (flag on/off vs parents-scan)")


def test_inherited_testament_and_beliefs():
    old_t, old_w, old_l, old_m = (
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY, se.LIFECYCLE_ENABLED, se.MEMES_ENABLED,
    )
    se.TESTAMENT_ENABLED, se.WIKI_MEMORY, se.LIFECYCLE_ENABLED = True, True, True
    se.MEMES_ENABLED = True
    try:
        engine = make_engine(4)
        parent_a, parent_b = engine.agents[0], engine.agents[1]
        parent_a["beliefs"] = {"share_food", "honor_elders"}
        parent_b["beliefs"] = {"share_food", "build_together"}
        engine.civilization["testament"] = [
            {"text": "old lesson", "author": "Elder", "frame": 1, "generation": 0},
            {"text": "recent wisdom", "author": "Sage", "frame": 99, "generation": 1},
        ]
        engine._spawn_newborn(parent_a, parent_b)
        newborn = engine.agents[-1]
        expected_beliefs = sorted({"share_food", "honor_elders", "build_together"})
        assert_true(newborn.get("inheritedBeliefs") == expected_beliefs,
                    f"inheritedBeliefs wrong: {newborn.get('inheritedBeliefs')}")
        testament = newborn.get("inheritedTestament") or []
        assert_true(len(testament) == 2, f"expected 2 testament entries, got {testament}")
        texts = [e["text"] for e in testament]
        assert_true("recent wisdom" in texts and "old lesson" in texts,
                    f"testament snapshot missing entries: {texts}")
        # Snapshot copies must not alias civilization ring entries.
        engine.civilization["testament"][0]["text"] = "mutated"
        assert_true(testament[0]["text"] != "mutated",
                    "inheritedTestament must be a deep copy, not aliased")
    finally:
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY = old_t, old_w
        se.LIFECYCLE_ENABLED = old_l
        se.MEMES_ENABLED = old_m
    print("  OK inheritedTestament + inheritedBeliefs at birth")


def _assert_snapshot_lineage_row(row, *, parents=None, children=None,
                                 inherited_testament=None, inherited_beliefs=None):
    for key in ("parents", "children", "inheritedTestament", "inheritedBeliefs"):
        assert_true(key in row, f"snapshot row missing {key}")
    if parents is not None:
        assert_true(row["parents"] == parents, f"parents wrong: {row['parents']}")
    if children is not None:
        assert_true(row["children"] == children, f"children wrong: {row['children']}")
    if inherited_testament is not None:
        assert_true(row["inheritedTestament"] == inherited_testament,
                    f"inheritedTestament wrong: {row['inheritedTestament']}")
    if inherited_beliefs is not None:
        assert_true(row["inheritedBeliefs"] == inherited_beliefs,
                    f"inheritedBeliefs wrong: {row['inheritedBeliefs']}")


def test_snapshot_lineage_fields():
    old_l = se.LIFECYCLE_ENABLED
    se.LIFECYCLE_ENABLED = True
    try:
        engine = make_engine(4)
        snap = engine.snapshot()
        assert_true(snap["config"]["flags"].get("DYNASTY_TREE_ENABLED") is True,
                    "DYNASTY_TREE_ENABLED flag not True in snapshot config")
        for row in snap["agents"]:
            _assert_snapshot_lineage_row(
                row,
                parents=None,
                children=[],
                inherited_testament=[],
                inherited_beliefs=[],
            )

        parent_a, parent_b = engine.agents[0], engine.agents[1]
        engine._spawn_newborn(parent_a, parent_b)
        newborn = engine.agents[-1]
        snap = engine.snapshot()
        by_name = {row["name"]: row for row in snap["agents"]}
        assert_true(newborn["name"] in by_name[parent_a["name"]]["children"],
                    "snapshot parent_a children missing newborn")
        assert_true(newborn["name"] in by_name[parent_b["name"]]["children"],
                    "snapshot parent_b children missing newborn")
        newborn_row = by_name[newborn["name"]]
        assert_true(newborn_row["parents"] == [parent_a["name"], parent_b["name"]],
                    f"snapshot newborn parents wrong: {newborn_row['parents']}")
        for agent in (parent_a, parent_b, newborn):
            row = by_name[agent["name"]]
            _assert_snapshot_lineage_row(
                row,
                parents=agent.get("parents"),
                children=list(agent.get("children") or []),
                inherited_testament=list(agent.get("inheritedTestament") or []),
                inherited_beliefs=list(agent.get("inheritedBeliefs") or []),
            )
    finally:
        se.LIFECYCLE_ENABLED = old_l
    print("  OK /state snapshot lineage fields + DYNASTY_TREE_ENABLED flag")


def test_memes_off_inherited_beliefs_empty():
    old_l, old_m = se.LIFECYCLE_ENABLED, se.MEMES_ENABLED
    se.LIFECYCLE_ENABLED = True
    se.MEMES_ENABLED = False
    try:
        engine = make_engine(4)
        parent_a, parent_b = engine.agents[0], engine.agents[1]
        parent_a["beliefs"] = {"should_not_snapshot"}
        engine._spawn_newborn(parent_a, parent_b)
        newborn = engine.agents[-1]
        assert_true(newborn.get("inheritedBeliefs") == [],
                    "memes off should yield empty inheritedBeliefs")
    finally:
        se.LIFECYCLE_ENABLED = old_l
        se.MEMES_ENABLED = old_m
    print("  OK memes-off inheritedBeliefs empty")


def main():
    print("dynasty_tree_smoke")
    test_cold_start_children_empty()
    test_birth_appends_children()
    test_restore_back_compat()
    test_heirs_of_regression()
    test_inherited_testament_and_beliefs()
    test_snapshot_lineage_fields()
    test_memes_off_inherited_beliefs_empty()
    print("PASS")


if __name__ == "__main__":
    main()
