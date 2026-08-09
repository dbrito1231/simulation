"""Deterministic smoke for Emergence Breakthroughs F2 (Theory of Mind).

Covers peerModel caps, no-wipe on module drop/timeout, restore round-trip,
flag-off byte shape, and peer_prediction_accuracy scoring. Ollama-free.

Run: uv run python scripts/peer_model_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _server.prompt_format import format_nearby_agents  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def test_flag_off_no_peer_model():
    old = se.THEORY_OF_MIND_ENABLED
    se.THEORY_OF_MIND_ENABLED = False
    try:
        engine = make_engine(4)
        for agent in engine.agents:
            assert_true("peerModel" not in agent, "flag off should omit peerModel")
            assert_true("theory_of_mind" not in (agent.get("modules") or {}),
                        "flag off should omit theory_of_mind module")
    finally:
        se.THEORY_OF_MIND_ENABLED = old
    print("  OK flag-off shape")


def test_peer_model_caps():
    old = se.THEORY_OF_MIND_ENABLED
    se.THEORY_OF_MIND_ENABLED = True
    try:
        engine = make_engine(4)
        agent = engine.agents[0]
        agent["peerModel"] = {}
        for i in range(se.PEER_MODEL_MAX_PEERS + 3):
            engine._upsert_peer_model(agent, f"peer-{i}", {
                "wants": "x" * 200,
                "good_at": "wood",
                "owes": "food",
                "trust": 0.5,
                "frame": i,
            })
        assert_true(len(agent["peerModel"]) == se.PEER_MODEL_MAX_PEERS,
                    f"expected LRU cap {se.PEER_MODEL_MAX_PEERS}")
        sample = next(iter(agent["peerModel"].values()))
        assert_true(len(sample["wants"]) <= se.PEER_MODEL_FIELD_CHAR_CAP,
                    "per-field char cap")
        assert_true("peer-0" not in agent["peerModel"],
                    "oldest peer should have been evicted")
    finally:
        se.THEORY_OF_MIND_ENABLED = old
    print("  OK peerModel caps")


def test_module_drop_preserves_peer_model():
    old_piano, old_tom = se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED
    se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED = True, True
    try:
        engine = make_engine(4)
        observer = engine.agents[0]
        peer = engine.agents[1]
        observer["peerModel"] = {
            str(peer["id"]): {
                "wants": "wood", "good_at": "gather", "owes_me": "",
                "trust": 0.6, "frame": 1,
            },
        }
        before = dict(observer["peerModel"])

        def stub_timeout(module, agent_name, context, frame_tick=None, timeout_s=None):
            return None

        engine.d["run_piano_module"] = stub_timeout
        se.ALWAYS_ON_MODULES = False
        reports, tick, runs = engine._run_piano_modules(
            observer["name"],
            observer["modules"],
            3,
            "role=farmer",
        )
        assert_true(runs == 0, f"stub should drop modules, got runs={runs}")
        assert_true(observer["peerModel"] == before,
                    "module drop must not wipe peerModel")
    finally:
        se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED = old_piano, old_tom
    print("  OK no-wipe on module drop")


def test_theory_of_mind_parse_and_benchmark():
    old = se.THEORY_OF_MIND_ENABLED
    se.THEORY_OF_MIND_ENABLED = True
    try:
        engine = make_engine(4)
        observer = engine.agents[0]
        peer = engine.agents[1]
        observer["peerModel"] = {}
        line = (
            f"PEER={peer['name']} | wants=wood | good_at=gather | owes= | "
            f"trust=0.70 | expect=collect_resource"
        )
        engine._apply_theory_of_mind_report(observer, line)
        entry = observer["peerModel"].get(str(peer["id"]))
        assert_true(entry and entry["wants"] == "wood", "peerModel not updated")
        assert_true(str(peer["id"]) in engine._peer_prediction_pending,
                    "prediction not recorded")
        engine.apply_decision(peer, {"action": "collect_resource", "reasoning": "test"})
        assert_true(engine._peer_prediction_total == 1, "prediction not scored")
        assert_true(engine._peer_prediction_hits == 1, "exact action should score hit")
        accuracy = engine._peer_prediction_accuracy()
        assert_true(accuracy == 1.0, f"expected accuracy 1.0, got {accuracy}")
    finally:
        se.THEORY_OF_MIND_ENABLED = old
    print("  OK parse + peer_prediction_accuracy")


def test_restore_round_trip():
    import tempfile

    old = se.THEORY_OF_MIND_ENABLED
    se.THEORY_OF_MIND_ENABLED = True
    old_db_path = se.DB_PATH
    try:
        engine = make_engine(4)
        agent = engine.agents[0]
        peer = engine.agents[1]
        agent["peerModel"] = {
            str(peer["id"]): {
                "wants": "fish", "good_at": "fishing", "owes_me": "coin",
                "trust": 0.4, "frame": 99,
            },
        }
        tmpdir = tempfile.mkdtemp()
        se.DB_PATH = str(Path(tmpdir) / "state_peer_model.db")
        engine.save_state()
        restored = engine.restore_state()
        assert_true(restored, "restore_state should succeed against the just-written db")
        restored_agent = engine._find_agent(agent["name"])
        assert_true(restored_agent.get("peerModel") == agent["peerModel"],
                    "peerModel did not round-trip")
    finally:
        se.DB_PATH = old_db_path
        se.THEORY_OF_MIND_ENABLED = old
    print("  OK restore round-trip")


def test_nearby_prompt_fold_in():
    old = se.THEORY_OF_MIND_ENABLED
    se.THEORY_OF_MIND_ENABLED = True
    try:
        nearby = [{
            "name": "Aria", "role": "farmer", "food": 1, "wood": 0, "gold": 0,
            "peer_model": "wants wood — trust 0.6",
        }]
        text = format_nearby_agents(nearby)
        assert_true("[think: wants wood — trust 0.6]" in text,
                    f"peer hint missing from nearby line: {text}")
    finally:
        se.THEORY_OF_MIND_ENABLED = old
    print("  OK nearby prompt fold-in")


def test_piano_stagger_swap():
    old_piano, old_tom, old_always = se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED, se.ALWAYS_ON_MODULES
    se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED, se.ALWAYS_ON_MODULES = True, True, False
    try:
        engine = make_engine(4)
        to_run, _ = engine._piano_to_run(engine.agents[0]["modules"], 4)
        assert_true("theory_of_mind" in to_run, to_run)
        assert_true("social" not in to_run, "theory_of_mind should swap social slot on tick 4")
        to_run2, _ = engine._piano_to_run(engine.agents[0]["modules"], 2)
        assert_true("social" in to_run2 and "theory_of_mind" not in to_run2, to_run2)
    finally:
        se.PIANO_MODULES, se.THEORY_OF_MIND_ENABLED, se.ALWAYS_ON_MODULES = old_piano, old_tom, old_always
    print("  OK piano stagger swap")


def main():
    print("peer_model_smoke")
    test_flag_off_no_peer_model()
    test_peer_model_caps()
    test_module_drop_preserves_peer_model()
    test_theory_of_mind_parse_and_benchmark()
    test_restore_round_trip()
    test_nearby_prompt_fold_in()
    test_piano_stagger_swap()
    print("ALL OK")


if __name__ == "__main__":
    main()
