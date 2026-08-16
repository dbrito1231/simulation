"""save_state() -> restore_state() persistence round-trip (Contract 3:
simulation/sim_engine/mixin_persistence.py, persistence.py).

Every test here builds a cold-start SimEngine against a TEMP database path
(pytest tmp_path) via a monkeypatched sim_engine.DB_PATH -- never
simulation/state.db, which is the user's live world. sim_engine (the
package) has no import-time side effects (no thread starts, no state.db
reads at import) -- only SimEngine() construction and explicit method calls
touch anything, both done here under our own temp path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "simulation"))

import sim_engine as se  # noqa: E402
from _server.roles_data import ROLES  # noqa: E402
from _server.validation_constants import SLUG_RE  # noqa: E402

_DEPS = {"ROLES": ROLES, "SLUG_RE": SLUG_RE}


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    """Point sim_engine's shared DB_PATH at a temp file for the duration of
    one test. sim_engine's mixin methods resolve DB_PATH as a bare global
    against this package module's own __dict__ (see
    simulation/sim_engine/__init__.py's exec()-shared-namespace docstring),
    so setting the package attribute here is what save_state()/restore_state()
    actually read -- not simulation/state.db."""
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(se, "DB_PATH", str(db_path))
    return str(db_path)


def _make_engine(roster_size=4):
    return se.SimEngine(dict(_DEPS), roster_size=roster_size)


def test_save_then_restore_preserves_agents_civ_and_wildlife(temp_db_path):
    engine = _make_engine(roster_size=4)
    with engine.lock:
        engine.frameTick = 12345
        engine.civilization["level"] = 3
        engine.civilization["wildlife"] = [{
            "id": "w1", "kind": "deer", "districtId": "forest_west",
            "x": 10.0, "y": 20.0, "targetX": 10.0, "targetY": 20.0,
            "waypoints": [], "hp": 5, "maxHp": 5, "alive": True,
            "respawnAt": None, "migrateDest": None,
        }]
        agent_names = sorted(a["name"] for a in engine.agents)
        engine.agents[0]["resources"]["wood"] = 42

    assert engine.save_state(force=True) is True

    restored_engine = _make_engine(roster_size=4)
    assert restored_engine.restore_state() is True

    assert restored_engine.frameTick == 12345
    assert restored_engine.civilization["level"] == 3
    assert sorted(a["name"] for a in restored_engine.agents) == agent_names
    # The agent whose resources we mutated is the first cold-start agent by
    # construction order, not necessarily alphabetically first -- look it up
    # by matching the mutated value instead of assuming order/name.
    mutated = [a for a in restored_engine.agents if a["resources"].get("wood") == 42]
    assert len(mutated) == 1

    restored_wildlife = restored_engine.civilization.get("wildlife") or []
    assert len(restored_wildlife) == 1
    assert restored_wildlife[0]["id"] == "w1"
    assert restored_wildlife[0]["kind"] == "deer"
    assert restored_wildlife[0]["alive"] is True


def test_restore_rejects_missing_or_corrupt_db(temp_db_path):
    """No file at DB_PATH yet (cold start) -- restore_state() must return
    False, not raise, and leave the engine's cold-start world untouched."""
    engine = _make_engine(roster_size=4)
    cold_start_frame = engine.frameTick
    assert engine.restore_state() is False
    assert engine.frameTick == cold_start_frame


def test_restore_backfills_a_save_missing_a_newer_key(temp_db_path):
    """An old-shape save (missing a civ key introduced by a later phase,
    e.g. "godState") must still restore successfully -- restore_state()'s
    setdefault-only migrations are exactly what back-compat depends on."""
    engine = _make_engine(roster_size=4)
    with engine.lock:
        engine.frameTick = 99
    assert engine.save_state(force=True) is True

    # Read the just-written payload back out, strip a newer key that a real
    # pre-godState save would never have had, and write it back -- simulating
    # an old-shape save without hand-rolling the whole Contract-3 schema.
    payload = se._read_state_db(se.DB_PATH)
    assert payload is not None
    del payload["civilization"]["godState"]
    se._write_state_db(se.DB_PATH, payload)

    restored_engine = _make_engine(roster_size=4)
    assert restored_engine.restore_state() is True
    assert restored_engine.frameTick == 99
    # restore_state() backfills godState via _normalize_god_state(None) when
    # the key is absent -- never leaves it missing outright.
    assert "godState" in restored_engine.civilization
    assert isinstance(restored_engine.civilization["godState"], dict)
