"""Full-state persistence (Contract 3) for the simulation engine, backed by a
SQLite database. Split out of the former single-file sim_engine.py during the
Phase 6a package conversion -- pure move, no behavior change (see
simulation/sim_engine/__init__.py for the package overview).

STATE_VERSION was bumped 1 -> 2 for the world-expansion plan
(civilization.activeProject -> districtProjects, new
districts/roadNodes/roadEdges/frontierPlots); v1 saves are no longer
supported. v2 -> 3 splits structure sprite grids out of the civ JSON blob
into a structure_sprites table (restore still accepts v2 with embedded
sprites once, then re-saves as v3).
"""

import hashlib
import json
import os
import sqlite3


__all__ = [
    "STATE_VERSION",
    "RESTORE_STATE_VERSIONS",
    "STATE_DELTA_MAX_GAP",
    "DB_PATH",
    "AUTOSAVE_SECONDS",
    "_CIV_SET_KEYS",
    "_DB_DDL",
    "_STATE_HASH_SKIP_KEYS",
    "_connect_db",
    "_json_safe_copy",
    "_structure_sprites_fingerprint",
    "_state_content_hash",
    "_write_state_db",
    "_read_state_db",
]

STATE_VERSION = 3
# Restore accepts these versions; v2 DBs may still embed sprites in civ JSON.
RESTORE_STATE_VERSIONS = (2, 3)
# Max frame gap for GET /state?since= before the server returns a full snapshot.
STATE_DELTA_MAX_GAP = 90  # ~3s at 30Hz
# NOTE: this module now lives one directory deeper than the original
# single-file sim_engine.py (simulation/sim_engine.py -> simulation/sim_engine/
# persistence.py), so DB_PATH needs an extra os.path.dirname() hop to keep
# resolving to simulation/state.db (same value as before the package split).
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state.db")
AUTOSAVE_SECONDS = 10
# Sets on the civilization that serialize to JSON arrays and back.
_CIV_SET_KEYS = ("rejectedBlueprintIds", "rejectedRecipeIds", "builtTypes")


_DB_DDL = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS civ    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (name TEXT PRIMARY KEY, ord INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory (
    rowid_pk INTEGER PRIMARY KEY, id INTEGER, agent TEXT NOT NULL, text TEXT NOT NULL,
    salience REAL, kind TEXT, tier TEXT, frame_tick INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS council_transcript (
    rowid_pk INTEGER PRIMARY KEY, meeting_id INTEGER, who TEXT, type TEXT, text TEXT,
    feeling TEXT, frame_tick INTEGER, ts TEXT);
CREATE TABLE IF NOT EXISTS structure_sprites (
    structure_id TEXT PRIMARY KEY, sprite_json TEXT NOT NULL, updated_frame INTEGER NOT NULL);
"""

# Payload keys used only for save I/O and hashing — never written to meta/civ.
_STATE_HASH_SKIP_KEYS = frozenset({"savedAt", "_sprite_upserts", "_sprite_removals"})


def _connect_db(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_DB_DDL)
    return conn


def _json_safe_copy(value):
    """Deep-copy a value into JSON-serializable form (sets -> sorted lists)."""
    if isinstance(value, set):
        return sorted(_json_safe_copy(v) for v in value)
    if isinstance(value, dict):
        return {k: _json_safe_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe_copy(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe_copy(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _structure_sprites_fingerprint(civilization):
    """Stable digest of all in-memory structure sprites (for autosave hash)."""
    sprites = {}
    for s in (civilization or {}).get("structures") or []:
        sid = s.get("id")
        sp = s.get("sprite")
        if sid and sp:
            sprites[sid] = sp
    if not sprites:
        return None
    blob = json.dumps(
        _json_safe_copy(sprites), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _state_content_hash(payload):
    """Stable SHA-256 of a save payload, excluding ephemeral save keys."""
    canonical = {k: v for k, v in payload.items() if k not in _STATE_HASH_SKIP_KEYS}
    blob = json.dumps(
        canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _write_state_db(path, payload):
    """Atomically persist a Contract-3 payload dict into the SQLite state
    database at `path`. All table rewrites happen in a single transaction so
    a crash mid-write can never leave the DB half-updated. May raise; callers
    (SimEngine.save_state) swallow exceptions."""
    sprite_upserts = payload.get("_sprite_upserts") or {}
    sprite_removals = payload.get("_sprite_removals") or []
    conn = _connect_db(path)
    try:
        civ = payload.get("civilization") or {}
        agents = payload.get("agents") or []
        memory = payload.get("memory") or []
        council_transcript = payload.get("council_transcript") or []
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("version", str(payload.get("version"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("frameTick", str(payload.get("frameTick"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("savedAt", str(payload.get("savedAt"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("roster_size", str(payload.get("roster_size"))),
            )
            conn.execute("DELETE FROM civ")
            conn.executemany(
                "INSERT INTO civ (key, value) VALUES (?, ?)",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in civ.items()],
            )
            conn.execute("DELETE FROM agents")
            conn.executemany(
                "INSERT INTO agents (name, ord, data) VALUES (?, ?, ?)",
                [(a.get("name"), i, json.dumps(a, ensure_ascii=False))
                 for i, a in enumerate(agents)],
            )
            conn.execute("DELETE FROM memory")
            conn.executemany(
                "INSERT INTO memory (id, agent, text, salience, kind, tier, frame_tick, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.get("id"), m.get("agent"), m.get("text"), m.get("salience"),
                  m.get("kind"), m.get("tier"), m.get("frame_tick"), m.get("ts"))
                 for m in memory],
            )
            conn.execute("DELETE FROM council_transcript")
            conn.executemany(
                "INSERT INTO council_transcript "
                "(meeting_id, who, type, text, feeling, frame_tick, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r.get("meeting_id"), r.get("who"), r.get("type"), r.get("text"),
                  r.get("feeling"), r.get("frame_tick"), r.get("ts"))
                 for r in council_transcript],
            )
            for sid in sprite_removals:
                conn.execute(
                    "DELETE FROM structure_sprites WHERE structure_id = ?",
                    (sid,),
                )
            for sid, row in sprite_upserts.items():
                sprite = row.get("sprite") if isinstance(row, dict) else row
                frame = row.get("updated_frame", 0) if isinstance(row, dict) else 0
                if not sprite:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO structure_sprites "
                    "(structure_id, sprite_json, updated_frame) VALUES (?, ?, ?)",
                    (sid, json.dumps(sprite, ensure_ascii=False), int(frame)),
                )
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    finally:
        conn.close()


def _read_state_db(path):
    """Read a Contract-3 payload dict back out of the SQLite state database at
    `path`, or return None if it doesn't exist, is empty, or is corrupt."""
    if not os.path.exists(path):
        return None
    conn = None
    try:
        conn = _connect_db(path)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if "version" not in meta:
            return None
        try:
            version = int(meta.get("version"))
        except (TypeError, ValueError):
            return None
        try:
            frame_tick = int(meta.get("frameTick"))
        except (TypeError, ValueError):
            frame_tick = 0
        try:
            roster_size = int(meta.get("roster_size"))
        except (TypeError, ValueError):
            roster_size = None
        civilization = {
            k: json.loads(v)
            for k, v in conn.execute("SELECT key, value FROM civ").fetchall()
        }
        agents = [
            json.loads(data)
            for (data,) in conn.execute("SELECT data FROM agents ORDER BY ord").fetchall()
        ]
        memory = [
            {
                "id": row[0], "agent": row[1], "text": row[2], "salience": row[3],
                "kind": row[4], "tier": row[5], "frame_tick": row[6], "ts": row[7],
            }
            for row in conn.execute(
                "SELECT id, agent, text, salience, kind, tier, frame_tick, ts FROM memory"
            ).fetchall()
        ]
        council_transcript = [
            {
                "meeting_id": row[0], "who": row[1], "type": row[2],
                "text": row[3], "feeling": row[4], "frame_tick": row[5],
                "ts": row[6],
            }
            for row in conn.execute(
                "SELECT meeting_id, who, type, text, feeling, frame_tick, ts "
                "FROM council_transcript ORDER BY rowid_pk"
            ).fetchall()
        ]
        structure_sprites = {}
        try:
            for sid, sprite_json in conn.execute(
                "SELECT structure_id, sprite_json FROM structure_sprites"
            ).fetchall():
                structure_sprites[sid] = json.loads(sprite_json)
        except sqlite3.OperationalError:
            pass
        return {
            "version": version,
            "frameTick": frame_tick,
            "savedAt": meta.get("savedAt"),
            "roster_size": roster_size,
            "civilization": civilization,
            "agents": agents,
            "memory": memory,
            "council_transcript": council_transcript,
            "structureSprites": structure_sprites,
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()
