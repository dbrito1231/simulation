"""Deterministic smoke for Emergence Breakthroughs F4.1 (Schism storage).

Covers flag-off shape, flag-on cold-start keyed maps, and legacy restore wrap.
Ollama-free.

Run: uv run python scripts/schism_smoke.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

import sim_engine as se  # noqa: E402
from _sid_parity_smoke.helpers import assert_true, make_engine  # noqa: E402


def test_flag_off_no_keyed_maps():
    old = se.SCHISM_ENABLED
    se.SCHISM_ENABLED = False
    try:
        engine = make_engine(4)
        c = engine.civilization
        assert_true("rulesBySettlement" not in c,
                    "flag off must not install rulesBySettlement")
        assert_true("beliefRegistryBySettlement" not in c,
                    "flag off must not install beliefRegistryBySettlement")
    finally:
        se.SCHISM_ENABLED = old
    print("  OK flag-off shape")


def test_flag_on_cold_start_aliases_home():
    old = se.SCHISM_ENABLED
    se.SCHISM_ENABLED = True
    try:
        engine = make_engine(4)
        c = engine.civilization
        home = engine._primary_settlement_id()
        assert_true(home == "home", f"expected home settlement id, got {home}")
        assert_true(c["rulesBySettlement"][home] is c["rules"],
                    "home rules bucket must alias flat rules")
        assert_true(c["pendingRulesBySettlement"][home] is c["pendingRules"],
                    "home pending bucket must alias flat pendingRules")
        assert_true(c["beliefRegistryBySettlement"][home] is c["beliefRegistry"],
                    "home registry must alias flat beliefRegistry")
        assert_true(engine._rules_for_settlement(home) is c["rules"],
                    "_rules_for_settlement must return home bucket")
    finally:
        se.SCHISM_ENABLED = old
    print("  OK flag-on cold-start aliases")


def test_flag_on_restore_wraps_legacy_flat():
    old = se.SCHISM_ENABLED
    se.SCHISM_ENABLED = False
    old_db_path = se.DB_PATH
    try:
        engine = make_engine(4)
        flat_rules = [
            {"id": "priority_wood", "name": "Wood", "kind": "priority",
             "value": "wood", "enactedFrame": 10},
        ]
        engine.civilization["rules"] = copy.deepcopy(flat_rules)
        engine.civilization["pendingRules"] = [
            {"id": "tax_fish", "name": "Fish tax", "kind": "resource_tax",
             "value": 0.1, "votes": {}},
        ]
        tmpdir = tempfile.mkdtemp()
        se.DB_PATH = str(Path(tmpdir) / "state_schism.db")
        engine.save_state()

        se.SCHISM_ENABLED = True
        restored_engine = make_engine(4)
        assert_true(restored_engine.restore_state(), "restore should succeed")
        c = restored_engine.civilization
        home = restored_engine._primary_settlement_id()
        assert_true(len(c["rulesBySettlement"][home]) == len(flat_rules),
                    "wrapped rules length must match pre-wrap flat list")
        assert_true(c["rulesBySettlement"][home][0]["id"] == flat_rules[0]["id"],
                    "wrapped rule id must match")
        assert_true(c["rulesBySettlement"][home] is c["rules"],
                    "post-restore home rules must alias flat rules")
        restored_engine._rebuild_settlement_governance(home)
    finally:
        se.DB_PATH = old_db_path
        se.SCHISM_ENABLED = old
    print("  OK flag-on legacy restore wrap")


def main():
    print("schism_smoke.py")
    test_flag_off_no_keyed_maps()
    test_flag_on_cold_start_aliases_home()
    test_flag_on_restore_wraps_legacy_flat()
    print("ALL OK")


if __name__ == "__main__":
    main()
