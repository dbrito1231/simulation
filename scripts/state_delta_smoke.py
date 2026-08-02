"""Smoke test for GET /state delta protocol (full → delta → unchanged).

Run: uv run python scripts/state_delta_smoke.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
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


def _sample_sprite():
    return {"palette": ["#112233", "#445566"], "grid": ["ab", "ba"]}


def test_sprite_delta_and_persistence():
    eng = make_engine()
    eng.pause()
    sprite = _sample_sprite()

    with eng.lock:
        struct = {
            "id": "smoke_house_1",
            "type": "house",
            "x": 120.0,
            "y": 200.0,
            "visualStyle": "generic",
            "sprite": sprite,
            "name": "Smoke House",
            "districtId": "village",
            "condition": 100.0,
            "isRuin": False,
            "homeOf": None,
            "level": 1,
            "visualTier": 1,
            "renderScale": 1.0,
        }
        eng.civilization["structures"].append(struct)
        eng._mark_structure_dirty(struct, sprite_changed=True)
        eng.frameTick = max(eng.frameTick, 5)

    full = eng.snapshot()
    structs = (full.get("civilization") or {}).get("structures") or []
    row = next((s for s in structs if s.get("id") == "smoke_house_1"), None)
    assert row and row.get("sprite") == sprite, "full snapshot must include sprite"

    ft = eng.frameTick
    with eng.lock:
        struct["condition"] = 88.0
        eng._mark_structure_dirty(struct, sprite_changed=False)

    delta = eng.snapshot_delta(ft)
    upsert = next(
        (s for s in (delta.get("civilization") or {}).get("structures") or []
         if s.get("id") == "smoke_house_1"),
        None,
    )
    assert upsert is not None, delta
    assert "sprite" not in upsert, "non-sprite structure delta must omit sprite"
    assert upsert.get("condition") == 88.0

    old_db = se.DB_PATH
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = str(Path(tmpdir) / "state.db")
        se.DB_PATH = tmp_db
        try:
            assert eng.save_state(force=True)
            conn = sqlite3.connect(tmp_db)
            try:
                civ_structs = conn.execute(
                    "SELECT value FROM civ WHERE key = 'structures'"
                ).fetchone()
                assert civ_structs, "structures row missing"
                import json
                stored = json.loads(civ_structs[0])
                stored_row = next(s for s in stored if s.get("id") == "smoke_house_1")
                assert "sprite" not in stored_row, "civ JSON must omit sprite grids"
                sprite_rows = conn.execute(
                    "SELECT sprite_json FROM structure_sprites WHERE structure_id = ?",
                    ("smoke_house_1",),
                ).fetchall()
                assert len(sprite_rows) == 1, "dirty sprite row must be persisted"
                assert json.loads(sprite_rows[0][0]) == sprite
            finally:
                conn.close()

            eng2 = make_engine()
            eng2.pause()
            assert eng2.restore_state(), "restore from v3 split DB"
            restored = next(
                s for s in eng2.civilization["structures"] if s.get("id") == "smoke_house_1"
            )
            assert restored.get("sprite") == sprite, "restore must merge sprites"

            # v2 migration: embedded sprites accepted once, then split on save.
            with eng2.lock:
                v2_payload = eng2._serialize_state()
            v2_payload["version"] = 2
            v2_payload.pop("_sprite_upserts", None)
            v2_payload.pop("_sprite_removals", None)
            for s in v2_payload["civilization"]["structures"]:
                if s.get("id") == "smoke_house_1":
                    s["sprite"] = sprite
            conn = sqlite3.connect(tmp_db)
            try:
                conn.execute("DELETE FROM structure_sprites")
                conn.commit()
            finally:
                conn.close()
            se._write_state_db(tmp_db, v2_payload)

            eng3 = make_engine()
            eng3.pause()
            assert eng3.restore_state(), "restore from v2 embedded sprites"
            migrated = next(
                s for s in eng3.civilization["structures"] if s.get("id") == "smoke_house_1"
            )
            assert migrated.get("sprite") == sprite
            assert "smoke_house_1" in eng3._persist_dirty_structure_sprites
            assert eng3.save_state(force=True)
            conn = sqlite3.connect(tmp_db)
            try:
                version = conn.execute(
                    "SELECT value FROM meta WHERE key = 'version'"
                ).fetchone()[0]
                assert int(version) == se.STATE_VERSION
            finally:
                conn.close()
        finally:
            se.DB_PATH = old_db


def test_multi_client_delta_since():
    """Two clients with different since cursors both receive the same upsert."""
    eng = make_engine()
    eng.pause()
    sprite = _sample_sprite()

    with eng.lock:
        struct = {
            "id": "multi_client_house",
            "type": "house",
            "x": 50.0,
            "y": 60.0,
            "visualStyle": "generic",
            "sprite": sprite,
            "name": "Multi Client House",
            "districtId": "village",
            "condition": 100.0,
            "isRuin": False,
            "homeOf": None,
            "level": 1,
            "visualTier": 1,
            "renderScale": 1.0,
        }
        eng.civilization["structures"].append(struct)
        eng.frameTick = max(eng.frameTick, 20)
        client_a_since = eng.frameTick
        eng._mark_structure_dirty(struct, sprite_changed=True)
        change_frame = eng.frameTick

    delta_a = eng.snapshot_delta(client_a_since)
    upsert_a = next(
        (s for s in (delta_a.get("civilization") or {}).get("structures") or []
         if s.get("id") == "multi_client_house"),
        None,
    )
    assert upsert_a is not None, delta_a
    assert upsert_a.get("sprite") == sprite

    client_b_since = max(0, change_frame - 5)
    assert change_frame - client_b_since <= se.STATE_DELTA_MAX_GAP
    delta_b = eng.snapshot_delta(client_b_since)
    upsert_b = next(
        (s for s in (delta_b.get("civilization") or {}).get("structures") or []
         if s.get("id") == "multi_client_house"),
        None,
    )
    assert upsert_b is not None, (
        "older since must still receive upsert after another client polled",
        delta_b,
    )


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

    test_sprite_delta_and_persistence()
    test_multi_client_delta_since()

    print("state_delta_smoke: OK")


if __name__ == "__main__":
    main()
