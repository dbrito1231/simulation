"""Deterministic smoke for Emergence Breakthroughs F1 (Testament).

Covers deathbed merge, newborn wiki inheritance, ring cap, restore round-trip,
and flag-off byte shape. Ollama-free.

Run: uv run python scripts/testament_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _server.prompt_format import format_testament_prompt_line  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def test_flag_off_no_testament():
    old_t, old_w = se.TESTAMENT_ENABLED, se.WIKI_MEMORY
    se.TESTAMENT_ENABLED, se.WIKI_MEMORY = False, True
    try:
        engine = make_engine(4)
        assert_true("testament" not in engine.civilization,
                    "flag off should not pre-seed testament ring")
        agent = engine.agents[0]
        agent["memoryWiki"] = {
            "relationships": "ally with Ash",
            "goals": "gather wood",
            "lessons": "store food before winter",
        }
        engine._agent_dies(agent, cause="smoke")
        assert_true(not engine.civilization.get("testament"),
                    "flag off must not merge on death")
        payload = engine._build_think_payload(engine.agents[1])
        assert_true("testament_line" not in payload or not payload.get("testament_line"),
                    "flag off must not send testament_line")
    finally:
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY = old_t, old_w
    print("  OK flag-off shape")


def test_deathbed_merge_and_dedupe():
    old_t, old_w = se.TESTAMENT_ENABLED, se.WIKI_MEMORY
    se.TESTAMENT_ENABLED, se.WIKI_MEMORY = True, True
    try:
        engine = make_engine(4)
        agent = engine.agents[0]
        agent["memoryWiki"] = {
            "relationships": "trusts the elder",
            "goals": "",
            "lessons": "fish near the beach at dawn",
        }
        engine.civilization["births"] = 3
        engine._merge_testament_on_death(agent)
        testament = engine.civilization.get("testament") or []
        assert_true(len(testament) == 2, f"expected lessons+relationships, got {testament}")
        texts = {e["text"] for e in testament}
        assert_true("fish near the beach at dawn" in texts, "lessons not merged")
        assert_true("trusts the elder" in texts, "relationships not merged")
        for entry in testament:
            assert_true(entry.get("author") == agent["name"], "author attribution")
            assert_true(entry.get("generation") == 3, "generation stamp")
            assert_true(len(entry["text"]) <= se.WIKI_SECTION_CHAR_CAP, "text cap")
        engine._merge_testament_on_death(agent)
        assert_true(len(engine.civilization["testament"]) == 2, "dedupe should skip repeats")
    finally:
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY = old_t, old_w
    print("  OK deathbed merge + dedupe")


def test_ring_cap():
    old_t = se.TESTAMENT_ENABLED
    se.TESTAMENT_ENABLED = True
    try:
        engine = make_engine(2)
        cap = se.TESTAMENT_CAP
        for i in range(cap + 5):
            engine._push_testament_entry(f"lesson {i}", f"author-{i}", i)
        testament = engine.civilization["testament"]
        assert_true(len(testament) == cap, f"ring should cap at {cap}, got {len(testament)}")
        assert_true(testament[0]["text"] == f"lesson {5}", "oldest should have dropped")
        assert_true(testament[-1]["text"] == f"lesson {cap + 4}", "newest should remain")
    finally:
        se.TESTAMENT_ENABLED = old_t
    print("  OK ring cap")


def test_newborn_inheritance():
    old_t, old_w, old_l = se.TESTAMENT_ENABLED, se.WIKI_MEMORY, se.LIFECYCLE_ENABLED
    se.TESTAMENT_ENABLED, se.WIKI_MEMORY, se.LIFECYCLE_ENABLED = True, True, True
    try:
        engine = make_engine(4)
        parent_a, parent_b = engine.agents[0], engine.agents[1]
        parent_a["memoryWiki"] = {
            "relationships": "", "goals": "build a granary",
            "lessons": "parent lesson",
        }
        engine.civilization["testament"] = [
            {"text": "elder wisdom", "author": "Sage", "frame": 1, "generation": 0},
        ]
        slot = {"id": 999, "name": "TestChild", "role": "gatherer",
                "personality": "curious", "color": "#abcdef", "zone": "village_core"}
        newborn = engine._make_agents([slot])[0]
        engine._seed_newborn_wiki_from_testament(newborn, parent_a, parent_b)
        wiki = newborn.get("memoryWiki") or {}
        assert_true(wiki.get("lessons"), f"lessons should inherit, got {wiki}")
        assert_true("parent lesson" in wiki["lessons"], "parent lessons missing")
        assert_true("elder wisdom" in wiki["lessons"], "testament lessons missing")
        assert_true(wiki.get("goals") == "build a granary", "goals should inherit")
        assert_true(len(wiki["lessons"]) <= se.WIKI_SECTION_CHAR_CAP, "section cap")
    finally:
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY, se.LIFECYCLE_ENABLED = old_t, old_w, old_l
    print("  OK newborn inheritance")


def test_prompt_line_bounded():
    old_t = se.TESTAMENT_ENABLED
    se.TESTAMENT_ENABLED = True
    try:
        entries = [
            {"text": f"lesson {i}", "author": f"a{i}", "frame": i, "generation": 0}
            for i in range(10)
        ]
        line = format_testament_prompt_line(entries, se.TESTAMENT_PROMPT_ENTRIES)
        assert_true(line is not None, "prompt line expected")
        assert_true(line.count("lesson") == se.TESTAMENT_PROMPT_ENTRIES,
                    "prompt must slice by TESTAMENT_PROMPT_ENTRIES not ring size")
    finally:
        se.TESTAMENT_ENABLED = old_t
    print("  OK prompt slice")


def test_restore_round_trip():
    old_t, old_w = se.TESTAMENT_ENABLED, se.WIKI_MEMORY
    se.TESTAMENT_ENABLED, se.WIKI_MEMORY = True, True
    old_db_path = se.DB_PATH
    try:
        engine = make_engine(4)
        engine.civilization["testament"] = [
            {"text": "carry me forward", "author": "Mira", "frame": 42, "generation": 2},
        ]
        engine.civilization["testamentAuthored"] = 1
        parent = engine.agents[0]
        parent["memoryWiki"] = {
            "relationships": "", "goals": "", "lessons": "saved lesson",
        }
        tmpdir = tempfile.mkdtemp()
        se.DB_PATH = str(Path(tmpdir) / "state_testament.db")
        engine.save_state()
        restored = engine.restore_state()
        assert_true(restored, "restore_state should succeed")
        assert_true(engine.civilization.get("testament") == [
            {"text": "carry me forward", "author": "Mira", "frame": 42, "generation": 2},
        ], "testament ring did not round-trip")
        assert_true(engine.civilization.get("testamentAuthored") == 1,
                    "testamentAuthored did not round-trip")
    finally:
        se.DB_PATH = old_db_path
        se.TESTAMENT_ENABLED, se.WIKI_MEMORY = old_t, old_w
    print("  OK restore round-trip")


def main():
    print("testament_smoke")
    test_flag_off_no_testament()
    test_deathbed_merge_and_dedupe()
    test_ring_cap()
    test_newborn_inheritance()
    test_prompt_line_bounded()
    test_restore_round_trip()
    print("PASS")


if __name__ == "__main__":
    main()
