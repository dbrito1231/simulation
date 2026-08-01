"""Deterministic smoke harness for Sovereign God mode Phase 2 (secure kernel,
persistence, preview, audit -- docs/plan-sovereign-god-mode-v2.md).

Exercises the flag/token gate, preview/idempotency, expiry, the stored-text
contract (including hostile-string round-tripping), godState persistence,
and the five /control/god/* HTTP routes. No Ollama, no network beyond an
in-process Flask test client. Run:

    uv run python scripts/god_mode_smoke.py

IMPORTANT: this process may run alongside a live simulation/server.py
instance sharing simulation/state.db. Every test below either (a) builds its
own lightweight in-process SimEngine (never touching the real DB_PATH) or
(b) reads/mutates ONLY the real server module's in-memory `engine` object
without ever calling save_state()/reset()/clear_state() against the live
state.db -- so nothing here can corrupt or race a concurrently running
server process.
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))

# SIM_GOD_MODE/SIM_GOD_TOKEN/SIM_GOD_AUTH must be set BEFORE the first import
# of sim_engine/server in this process, since all three read the env once at
# import (see sim_engine.GOD_MODE_ENABLED/GOD_AUTH_REQUIRED and server.py's
# GOD_TOKEN). Every other on/off scenario below is exercised by monkeypatching
# the already-imported module's plain attributes for the duration of one test,
# then restoring -- the same idiom sid_parity_smoke.py already uses for
# PIANO_MODULES/ALWAYS_ON_MODULES.
TEST_TOKEN = "smoke-god-token-do-not-reuse-anywhere-real"
os.environ["SIM_GOD_MODE"] = "1"
os.environ["SIM_GOD_TOKEN"] = TEST_TOKEN
os.environ["SIM_GOD_AUTH"] = "1"

import sim_engine as se  # noqa: E402


def _load_roles():
    with open(ROOT / "simulation" / "roles.json", encoding="utf-8") as fh:
        return json.load(fh)


def make_engine(roster_size=4):
    """Lightweight engine construction (mirrors sid_parity_smoke.py's
    make_engine) -- never touches simulation/state.db."""
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
        "lm_complete": lambda *a, **k: None,
        "is_scaffold_text": lambda t: False,
        "memory_store": None,
        "log_activity": lambda *a, **k: None,
        "log_conversation": lambda *a, **k: None,
        "log_benchmark": lambda *a, **k: None,
        "log_divine": lambda **k: None,
        "log_compiler": lambda **k: None,
        "validate_blueprint": lambda *a, **k: (False, "unused"),
        "canonical_effect_vector": lambda *a, **k: (),
    }
    return se.SimEngine(deps, roster_size=roster_size)


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _proclamation_envelope(text):
    return {"kind": "proclamation", "payload": {"text": text}}


def _providence_envelope(text, duration=None):
    payload = {"text": text}
    if duration is not None:
        payload["durationFrames"] = duration
    return {"kind": "providence", "payload": payload}


def _omen_envelope(target_id, text, duration=None):
    payload = {"targetId": target_id, "text": text}
    if duration is not None:
        payload["durationFrames"] = duration
    return {"kind": "private_omen", "payload": payload}


def _whisper_campaign_envelope(theme, whispers, duration=None):
    payload = {"theme": theme, "whispers": whispers}
    if duration is not None:
        payload["durationFrames"] = duration
    return {"kind": "whisper_campaign", "payload": payload}


def _agent_sampling_envelope(target_id, temperature, model="sim-smart", **extra):
    payload = {"targetId": target_id, "temperature": temperature, "model": model}
    payload.update(extra)
    return {"kind": "agent_sampling", "payload": payload}


def _revoke_agent_sampling_envelope(target_id):
    return {"kind": "revoke_agent_sampling", "payload": {"targetId": target_id}}


def _memory_insert_envelope(target_id, text, salience=0.7, kind=None):
    payload = {"targetId": target_id, "text": text, "salience": salience}
    if kind is not None:
        payload["kind"] = kind
    return {"kind": "memory_insert", "payload": payload}


def _memory_delete_envelope(target_id, **filters):
    payload = {"targetId": target_id}
    payload.update(filters)
    return {"kind": "memory_delete", "payload": payload}


def _belief_plant_envelope(target_id, *, belief_id=None, text=None,
                           plant_in_meme_texts=False, salience=0.7):
    payload = {
        "targetId": target_id,
        "plantInMemeTexts": plant_in_meme_texts,
        "salience": salience,
    }
    if belief_id is not None:
        payload["beliefId"] = belief_id
    if text is not None:
        payload["text"] = text
    return {"kind": "belief_plant", "payload": payload}


def _context_mask_envelope(target_id, mode, duration=None, **extra):
    payload = {"targetId": target_id, "mode": mode}
    if duration is not None:
        payload["durationFrames"] = duration
    payload.update(extra)
    return {"kind": "context_mask", "payload": payload}


def _decision_compulsion_envelope(target_id, action="rest", **extra):
    payload = {
        "targetId": target_id,
        "pinnedDecision": {"action": action, "reasoning": "smoke compulsion"},
    }
    payload.update(extra)
    return {"kind": "decision_compulsion", "payload": payload}


def _decision_veto_arm_envelope(target_id, duration=None):
    payload = {"targetId": target_id}
    if duration is not None:
        payload["durationFrames"] = duration
    return {"kind": "decision_veto_arm", "payload": payload}


def _decision_veto_resolve_envelope(target_id, resolution, **extra):
    payload = {"targetId": target_id, "resolution": resolution}
    payload.update(extra)
    return {"kind": "decision_veto_resolve", "payload": payload}


def _agent_possession_envelope(target_id, action="rest", duration=None):
    payload = {
        "targetId": target_id,
        "pinnedDecision": {"action": action, "reasoning": "smoke possession"},
    }
    if duration is not None:
        payload["durationFrames"] = duration
    return {"kind": "agent_possession", "payload": payload}


def _revoke_decision_gate_envelope(target_id):
    return {"kind": "revoke_decision_gate", "payload": {"targetId": target_id}}


def _burning_bush_message_envelope(target_id, text):
    return {"kind": "burning_bush_message",
            "payload": {"targetId": target_id, "text": text}}


def _burning_bush_close_envelope(target_id):
    return {"kind": "burning_bush_close", "payload": {"targetId": target_id}}


def _merovingian_bargain_envelope(target_id, terms_text, success_predicate, **extra):
    payload = {
        "targetId": target_id,
        "termsText": terms_text,
        "successPredicate": success_predicate,
    }
    payload.update(extra)
    return {"kind": "merovingian_bargain", "payload": payload}


def _bargain_settle_envelope(target_id, outcome):
    return {"kind": "bargain_settle",
            "payload": {"targetId": target_id, "outcome": outcome}}


def _anoint_envelope(target_id, destiny_text, **extra):
    payload = {"targetId": target_id, "destinyText": destiny_text}
    payload.update(extra)
    return {"kind": "anoint", "payload": payload}


def _revoke_anoint_envelope(target_id):
    return {"kind": "revoke_anoint", "payload": {"targetId": target_id}}


def make_engine_with_cognition(roster_size=4):
    """make_engine with server cognition deps for pinned-decision validation."""
    import server as srv  # noqa: E402
    engine = make_engine(roster_size=roster_size)
    engine.d["AVAILABLE_ACTIONS"] = list(srv.AVAILABLE_ACTIONS)
    engine.d["normalize_decision"] = srv.normalize_decision
    engine.d["build_agent_data"] = srv.build_agent_data
    return engine


def make_engine_with_memory(roster_size=4):
    """make_engine plus a real in-process MemoryStore (temp path)."""
    from server import MemoryStore  # noqa: E402

    store_path = str(Path(tempfile.mkdtemp()) / "memory_store.json")
    store = MemoryStore(store_path)
    engine = make_engine(roster_size=roster_size)
    engine.d["memory_store"] = store
    return engine, store


def _revoke_envelope(guidance_id):
    return {"kind": "revoke_guidance", "payload": {"id": guidance_id}}


def _vitals_envelope(target_id, health_delta=None, hunger_delta=None):
    payload = {"targetId": target_id}
    if health_delta is not None:
        payload["healthDelta"] = health_delta
    if hunger_delta is not None:
        payload["hungerDelta"] = hunger_delta
    return {"kind": "agent_vitals", "payload": payload}


def _grant_envelope(resource_id, amount, target=None):
    payload = {"resourceId": resource_id, "amount": amount}
    if target is not None:
        payload["target"] = target
    return {"kind": "grant_resource", "payload": payload}


def _structure_envelope(structure_id, delta):
    return {"kind": "structure_condition", "payload": {"structureId": structure_id, "delta": delta}}


def _repair_structures_envelope(scope, *, structure_ids=None, un_ruin=True, condition_target=None):
    payload = {"scope": scope, "unRuin": un_ruin}
    if scope == "ids":
        payload["structureIds"] = list(structure_ids or [])
    if condition_target is not None:
        payload["conditionTarget"] = condition_target
    return {"kind": "repair_structures", "payload": payload}


def _clear_ruins_envelope(*, structure_ids=None, district_id=None, min_age_frames=None):
    payload = {}
    if structure_ids is not None:
        payload["structureIds"] = list(structure_ids)
    if district_id is not None:
        payload["districtId"] = district_id
    if min_age_frames is not None:
        payload["minAgeFrames"] = min_age_frames
    return {"kind": "clear_ruins", "payload": payload}


def _weather_envelope(state, districts=None, duration=None, replace_effect_id=None):
    payload = {"state": state}
    if districts is not None:
        payload["districts"] = districts
    if duration is not None:
        payload["durationFrames"] = duration
    if replace_effect_id is not None:
        payload["replaceEffectId"] = replace_effect_id
    return {"kind": "weather_override", "payload": payload}


def _add_test_structure(engine, condition=100.0, home_of=None, district_id=None):
    """Appends a minimal structure directly to civilization["structures"],
    mirroring the shape _build_active_structure produces, without needing a
    real build pipeline run. Returns the structure dict."""
    c = engine.civilization
    if district_id is None:
        district_id = next(iter(c["districts"]))
    sid = c["nextStructureId"]
    structure = {
        "id": sid, "type": "house", "x": 0, "y": 0,
        "visualStyle": "generic", "sprite": None,
        "name": "Test House", "districtId": district_id,
        "condition": condition, "isRuin": False,
        "homeOf": home_of, "level": 1, "visualTier": 1, "renderScale": 1.0,
    }
    c["structures"].append(structure)
    c["nextStructureId"] += 1
    return structure


# --- Engine-level tests (lightweight engine, never touches real DB_PATH) ---

def test_flag_off_inert():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = False
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("Rain."))
        assert_true(preview == {"ok": False, "reason": "god mode disabled"}, preview)
        applied = engine.god_apply("whatever", "req")
        assert_true(applied == {"ok": False, "reason": "god mode disabled"}, applied)
        sight = engine.god_sight()
        assert_true(sight == {"ok": False, "reason": "god mode disabled"}, sight)
        cancelled = engine.god_cancel("x")
        assert_true(cancelled == {"ok": False, "reason": "god mode disabled"}, cancelled)
        snap = engine.snapshot()
        assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is False, snap["config"]["flags"])
        assert_true("god" not in snap, "flag-off /state leaked a 'god' key")
        print("  OK flag-off: every god entry point inert, no /state 'god' key")
    finally:
        se.GOD_MODE_ENABLED = old


def test_flag_on_state_shape():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]
        expected = engine._default_god_state()
        assert_true(god == expected, god)
        snap = engine.snapshot()
        assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is True, snap["config"]["flags"])
        assert_true(snap["god"] == {
            "intervened": False, "providence": None,
            "activePublicEvents": [], "recentPublicInterventions": [],
        }, snap["god"])
        print("  OK flag-on: default godState shape + /state 'god' projection")
    finally:
        se.GOD_MODE_ENABLED = old


def test_preview_side_effect_free():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        before_god = dict(engine.civilization["godState"])
        before_activity = list(engine.activityLog)
        before_conv = list(engine.conversationLog)
        preview = engine.god_preview(_proclamation_envelope("A great harvest approaches."))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewId"] and preview["commandDigest"], preview)
        assert_true(preview["normalizedCommand"] == {
            "kind": "proclamation", "payload": {"text": "A great harvest approaches."},
        }, preview["normalizedCommand"])
        assert_true(preview["reversibilityClass"] == "irreversible", preview)
        assert_true(engine.civilization["godState"] == before_god, "preview mutated godState")
        assert_true(engine.activityLog == before_activity, "preview mutated activityLog")
        assert_true(engine.conversationLog == before_conv, "preview mutated conversationLog")
        print("  OK preview validates + normalizes with zero mutation")
    finally:
        se.GOD_MODE_ENABLED = old


def test_tampered_expired_missing_preview_rejected():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        missing = engine.god_apply(None, "req-missing")
        assert_true(missing == {"ok": False, "reason": "previewId is required"}, missing)

        unknown = engine.god_apply("does-not-exist", "req-unknown")
        assert_true(unknown == {"ok": False, "reason": "preview missing or expired"}, unknown)

        preview = engine.god_preview(_proclamation_envelope("The tide will turn."))
        pid = preview["previewId"]
        engine._god_preview_cache[pid]["expiresAt"] = time.time() - 1
        expired = engine.god_apply(pid, "req-expired")
        # A TTL-expired preview is evicted by the same bounded sweep that
        # runs at the top of every god_apply call, so it surfaces through
        # the same "missing" rejection an unknown previewId would -- both
        # mean "there is no live preview to apply".
        assert_true(expired == {"ok": False, "reason": "preview missing or expired"}, expired)
        assert_true(pid not in engine._god_preview_cache, "expired preview not evicted")

        preview2 = engine.god_preview(_proclamation_envelope("The tide will turn."))
        pid2 = preview2["previewId"]
        engine._god_preview_cache[pid2]["commandDigest"] = "0" * 64  # simulate a tampered record
        tampered = engine.god_apply(pid2, "req-tampered")
        assert_true(tampered == {"ok": False, "reason": "preview digest mismatch"}, tampered)
        assert_true(engine.civilization["godState"]["intervened"] is False,
                    "a rejected apply must never mutate world state")
        print("  OK missing/unknown/expired/tampered previews rejected, none applied")
    finally:
        se.GOD_MODE_ENABLED = old


def test_idempotent_apply_and_conflict():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        preview1 = engine.god_preview(_proclamation_envelope("Peace be with you."))
        pid1 = preview1["previewId"]
        resp1 = engine.god_apply(pid1, "req-idem")
        assert_true(resp1["ok"] and resp1["interventionId"] == "divine-1", resp1)
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 1,
                    engine.civilization["godState"]["recentInterventions"])

        # Exact replay: same requestId + same previewId returns the ORIGINAL
        # response without re-applying (the preview was already consumed).
        resp1_replay = engine.god_apply(pid1, "req-idem")
        assert_true(resp1_replay == resp1, (resp1_replay, resp1))
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 1,
                    "replay re-applied instead of returning the stored response")
        assert_true(engine.civilization["godState"]["nextInterventionSeq"] == 2,
                    "replay incremented the intervention sequence")

        # Same requestId, a DIFFERENT preview -> conflict, apply nothing.
        preview2 = engine.god_preview(_proclamation_envelope("A second decree."))
        pid2 = preview2["previewId"]
        conflict = engine.god_apply(pid2, "req-idem")
        assert_true(conflict == {
            "ok": False, "reason": "requestId already used with a different preview",
        }, conflict)
        assert_true(len(engine.civilization["godState"]["recentInterventions"]) == 1,
                    "conflicting requestId reuse mutated state")
        print("  OK idempotent replay returns the original response; "
              "different-preview reuse conflicts and applies nothing")
    finally:
        se.GOD_MODE_ENABLED = old


def test_text_normalizer():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        ok_text, err = engine._normalize_divine_text("A calm and steady rain.")
        assert_true(err is None and ok_text == "A calm and steady rain.", (ok_text, err))

        _, err = engine._normalize_divine_text("bad\x00text")
        assert_true(err and "NUL" in err, err)

        _, err = engine._normalize_divine_text("line one\nline two")
        assert_true(err and "newline" in err, err)

        _, err = engine._normalize_divine_text("bad\x01control")
        assert_true(err and "control" in err, err)

        _, err = engine._normalize_divine_text("A" * se.GOD_TEXT_MAX_CHARS)
        assert_true(err is None, f"exactly {se.GOD_TEXT_MAX_CHARS} chars should be accepted: {err}")
        _, err = engine._normalize_divine_text("A" * (se.GOD_TEXT_MAX_CHARS + 1))
        assert_true(err and "characters" in err, err)

        # 200 four-byte codepoints = 800 UTF-8 bytes but only 200 chars --
        # under the char cap (240) yet over the byte cap (600), so the byte
        # check must be load-bearing, not merely redundant with chars.
        wide = "\U0001D54F" * 200
        assert_true(len(wide) <= se.GOD_TEXT_MAX_CHARS, "test fixture must stay under the char cap")
        _, err = engine._normalize_divine_text(wide)
        assert_true(err and "bytes" in err, err)

        _, err = engine._normalize_divine_text(123)
        assert_true(err and "string" in err, err)

        _, err = engine._normalize_divine_text("   ")
        assert_true(err and "empty" in err, err)
        print("  OK text normalizer rejects NUL/control/newline, enforces char+byte caps")
    finally:
        se.GOD_MODE_ENABLED = old


def test_hostile_strings_round_trip():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        hostile_strings = [
            "<script>alert(1)</script>",
            '" onerror=x',
            "&amp;",
            "​Zero‮WidthRTL",
        ]
        for text in hostile_strings:
            engine = make_engine()
            preview = engine.god_preview(_proclamation_envelope(text))
            assert_true(preview["ok"], (text, preview))
            applied = engine.god_apply(preview["previewId"], f"req-{hash(text)}")
            assert_true(applied["ok"], (text, applied))
            stored_text = applied["outcome"]["text"]
            # Only NFC normalization + surrounding-whitespace strip happens
            # to plain text -- no HTML entity decoding/encoding, no escaping,
            # no truncation, no interpretation of any kind.
            import unicodedata
            expected = unicodedata.normalize("NFC", text).strip()
            assert_true(stored_text == expected, (text, stored_text, expected))
            assert_true(engine.conversationLog[0]["message"] == expected, engine.conversationLog[0])
            assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
            assert_true(expected in engine.activityLog[0], engine.activityLog[0])
            chronicle = engine.civilization["chronicle"][-1]
            assert_true(chronicle["text"] == expected and chronicle["source"] == "divine", chronicle)
        print(f"  OK {len(hostile_strings)} hostile strings round-trip as inert, unescaped plain text")
    finally:
        se.GOD_MODE_ENABLED = old


def test_godstate_roundtrip_save_restore():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_roundtrip.db")
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("A season of plenty."))
        applied = engine.god_apply(preview["previewId"], "req-roundtrip")
        assert_true(applied["ok"], applied)

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed against the just-written db")
        god = restored.civilization["godState"]
        assert_true(god["intervened"] is True, god)
        assert_true(god["nextInterventionSeq"] == 2, god)
        assert_true(len(god["recentInterventions"]) == 1
                    and god["recentInterventions"][0]["text"] == "A season of plenty.", god)
        # Restore also invalidates any outstanding preview/idempotency state.
        assert_true(restored._god_preview_cache == {}, restored._god_preview_cache)
        assert_true(restored._god_requests == {}, restored._god_requests)
        print("  OK godState round-trips save_state/restore_state; "
              "preview/idempotency caches invalidated on restore")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_old_save_without_godstate_restores_default():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_old_save.db")
    try:
        engine = make_engine()
        payload = engine._serialize_state()
        del payload["civilization"]["godState"]  # simulate a pre-Phase-2 save
        se._write_state_db(tmp_db, payload)

        restored = make_engine()
        se.DB_PATH = tmp_db
        assert_true(restored.restore_state(), "restore_state failed against a godState-less save")
        assert_true(restored.civilization["godState"] == restored._default_god_state(),
                    restored.civilization["godState"])
        print("  OK a pre-Phase-2 save (no godState key) restores with the fresh default")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_reset_clears_intervention_state():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_reset.db")
    try:
        se.DB_PATH = tmp_db
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("Before the reset."))
        engine.god_apply(preview["previewId"], "req-before-reset")
        assert_true(engine.civilization["godState"]["intervened"] is True, "expected intervened=True before reset")
        assert_true(engine._god_requests, "expected a populated idempotency store before reset")

        engine.reset()
        assert_true(engine.civilization["godState"] == engine._default_god_state(),
                    engine.civilization["godState"])
        assert_true(engine._god_preview_cache == {} and engine._god_requests == {},
                    "reset must clear the in-memory preview/idempotency caches too")
        print("  OK reset() clears godState, intervened marker, and in-memory caches")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_cancel_plumbing():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        result = engine.god_cancel("nothing-here")
        assert_true(result == {
            "ok": True, "cancelled": False, "reason": "nothing to cancel",
            "targetId": "nothing-here",
        }, result)
        print("  OK god_cancel plumbing returns a clean 'nothing to cancel' result")
    finally:
        se.GOD_MODE_ENABLED = old


def test_sight_bounded_projection():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        sight = engine.god_sight()
        assert_true(sight["ok"], sight)
        assert_true("agents" in sight and isinstance(sight["agents"], list), sight)
        dumped = json.dumps(sight)
        assert_true(TEST_TOKEN not in dumped, "sight leaked the process token")
        assert_true("embedding" not in dumped.lower(), "sight leaked memory-store embeddings")
        print("  OK god_sight returns a bounded projection with no embeddings/token")
    finally:
        se.GOD_MODE_ENABLED = old


def test_preview_and_request_cache_bounds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        for i in range(se.GOD_PREVIEW_CACHE_MAX + 10):
            engine.god_preview(_proclamation_envelope(f"Decree number {i}."))
        assert_true(len(engine._god_preview_cache) <= se.GOD_PREVIEW_CACHE_MAX,
                    len(engine._god_preview_cache))

        for i in range(se.GOD_REQUEST_CACHE_MAX + 10):
            preview = engine.god_preview(_proclamation_envelope(f"Bound test {i}."))
            engine.god_apply(preview["previewId"], f"req-bound-{i}")
        assert_true(len(engine._god_requests) <= se.GOD_REQUEST_CACHE_MAX,
                    len(engine._god_requests))
        print("  OK preview cache stays <= 32 and idempotency store stays <= 100 under load")
    finally:
        se.GOD_MODE_ENABLED = old


def test_unknown_and_future_kinds_rejected():
    """story_event was a placeholder "not implemented in this phase" kind
    through Phase 2-4; Phase 5 implements it for real (see the Phase 5
    section below), so this Phase 2 case now only covers a malformed
    story_event payload -- still rejected, but on its own merits (an empty
    title/narration), not the old catalog-stub reason."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        result = engine.god_preview({"kind": "story_event", "payload": {}})
        assert_true(result["ok"] is False and "title" in result["reason"], result)
        unknown = engine.god_preview({"kind": "smite", "payload": {}})
        assert_true(unknown == {"ok": False, "reason": "unknown kind 'smite'"}, unknown)
        malformed = engine.god_preview("not-a-dict")
        assert_true(malformed == {"ok": False, "reason": "envelope must be an object"}, malformed)
        print("  OK unknown/malformed envelopes rejected cleanly; story_event validates its own payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_expire_divine_effects_noop_and_tick():
    """Phase 2 has no timed effects yet, so _expire_divine_effects is a
    provably cheap no-op, and _tick_once must call it without raising."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        before = dict(engine.civilization["godState"])
        engine._expire_divine_effects()
        assert_true(engine.civilization["godState"] == before, "no-op expiry mutated godState")
        engine._tick_once()  # must not raise with GOD_MODE_ENABLED True and no active events
        print("  OK _expire_divine_effects is a safe no-op; wired into _tick_once")
    finally:
        se.GOD_MODE_ENABLED = old


def test_benchmarks_expose_intervened():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        preview = engine.god_preview(_proclamation_envelope("For the benchmark record."))
        engine.god_apply(preview["previewId"], "req-benchmark")
        engine._sample_benchmarks()
        assert_true(engine.lastBenchmarks.get("intervened") is True, engine.lastBenchmarks)
        print("  OK benchmarks expose intervened=True after an applied proclamation")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 3 tests: voice and providence (docs/plan-sovereign-god-mode-v2.md
# "Voice and providence" + "Bounded cognition impact" + Phase 3 deliverables) ---

def test_providence_set_replace_revoke_expire():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]

        preview = engine.god_preview(_providence_envelope("A season of trial approaches.", duration=1000))
        assert_true(preview["ok"], preview)
        assert_true(preview["fingerprint"] == {"outgoingId": None}, preview["fingerprint"])
        assert_true(preview["reversibilityClass"] == "cancellable", preview)
        applied = engine.god_apply(preview["previewId"], "req-prov-1")
        assert_true(applied["ok"], applied)
        prov_id = applied["outcome"]["interventionId"]
        assert_true(god["providence"]["id"] == prov_id, god["providence"])
        assert_true(god["providence"]["expiresFrame"] == engine.frameTick + 1000, god["providence"])
        # Public per docs/plan Visibility: same treatment as proclamation.
        assert_true('"A season of trial approaches."' in engine.activityLog[0], engine.activityLog[0])
        assert_true(engine.conversationLog[0]["message"] == "A season of trial approaches."
                    and engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
        assert_true(engine.civilization["chronicle"][-1]["text"] == "A season of trial approaches."
                    and engine.civilization["chronicle"][-1]["source"] == "divine",
                    engine.civilization["chronicle"][-1])

        # Replace: preview discloses the outgoing id; apply reports it too.
        preview2 = engine.god_preview(_providence_envelope("A season of plenty follows.", duration=2000))
        assert_true(preview2["fingerprint"] == {"outgoingId": prov_id}, preview2["fingerprint"])
        applied2 = engine.god_apply(preview2["previewId"], "req-prov-2")
        assert_true(applied2["ok"], applied2)
        assert_true(applied2["outcome"]["outgoingId"] == prov_id, applied2["outcome"])
        prov_id2 = applied2["outcome"]["interventionId"]
        assert_true(god["providence"]["id"] == prov_id2, god["providence"])

        # A stale preview (recorded outgoingId no longer current) is rejected.
        stale = engine.god_preview(_providence_envelope("Stale replacement.", duration=1000))
        # Simulate the providence having changed again between preview and apply.
        engine.civilization["godState"]["providence"]["id"] = "divine-tampered"
        stale_apply = engine.god_apply(stale["previewId"], "req-prov-stale")
        assert_true(stale_apply == {
            "ok": False, "reason": "providence changed since preview -- re-preview to see the current guidance",
        }, stale_apply)
        engine.civilization["godState"]["providence"]["id"] = prov_id2  # restore for the rest of the test

        # Revoke.
        preview3 = engine.god_preview(_revoke_envelope(prov_id2))
        assert_true(preview3["fingerprint"] == {"targetKind": "providence", "existed": True}, preview3["fingerprint"])
        applied3 = engine.god_apply(preview3["previewId"], "req-prov-revoke")
        assert_true(applied3["ok"], applied3)
        assert_true(god["providence"] is None, god["providence"])
        # Revoking twice (id already gone) is rejected, not re-applied.
        replay_revoke = engine.god_preview(_revoke_envelope(prov_id2))
        assert_true(replay_revoke["fingerprint"] == {"targetKind": None, "existed": False}, replay_revoke)

        # Expire: predicate is startFrame <= frameTick < expiresFrame -- must
        # still be active one frame before expiresFrame and gone exactly at it.
        preview4 = engine.god_preview(
            _providence_envelope("A brief omen.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied4 = engine.god_apply(preview4["previewId"], "req-prov-4")
        assert_true(applied4["ok"], applied4)
        expires_frame = god["providence"]["expiresFrame"]
        engine.frameTick = expires_frame - 1
        engine._expire_divine_effects()
        assert_true(god["providence"] is not None, "providence closed one frame too early")
        engine.frameTick = expires_frame
        engine._expire_divine_effects()
        assert_true(god["providence"] is None, "providence not closed exactly at expiresFrame")
        print("  OK providence set/replace/revoke/expire; disclose-then-replace enforced at apply time")
    finally:
        se.GOD_MODE_ENABLED = old


def test_omen_lifecycle_and_memory_contract():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        god = engine.civilization["godState"]
        target = engine.agents[0]
        target_id = target["id"]
        key = str(target_id)

        def memory_hits(text):
            return sum(text in line for line in target["memory"]["working"])

        # Unknown / dead / bad-type targets rejected.
        unknown = engine.god_preview(_omen_envelope(999999, "Nobody home.", duration=1000))
        assert_true(unknown == {"ok": False, "reason": "unknown target agent"}, unknown)
        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_omen_envelope(target_id, "Too late.", duration=1000))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None
        bad_type = engine.god_preview(_omen_envelope(str(target_id), "Wrong type.", duration=1000))
        assert_true(bad_type == {"ok": False, "reason": "targetId must be an integer agent id"}, bad_type)

        # Set.
        preview = engine.god_preview(_omen_envelope(target_id, "Seek reconciliation with a rival.", duration=1000))
        assert_true(preview["ok"], preview)
        assert_true(preview["fingerprint"] == {"outgoingId": None}, preview["fingerprint"])
        applied = engine.god_apply(preview["previewId"], "req-omen-1")
        assert_true(applied["ok"], applied)
        omen_id = applied["outcome"]["interventionId"]
        assert_true(key in god["privateOmens"], god["privateOmens"])
        assert_true(god["privateOmens"][key]["memoryWritten"] is False, god["privateOmens"][key])
        # While ACTIVE: must NOT be in ordinary memory (else it influences
        # cognition twice -- once via the prompt line, once via memory).
        assert_true(memory_hits("Seek reconciliation") == 0,
                    "omen text leaked into memory while still active")

        # Replace: outgoing disclosed at preview, closed (memory written)
        # exactly once at apply.
        preview2 = engine.god_preview(_omen_envelope(target_id, "A different path opens.", duration=2000))
        assert_true(preview2["fingerprint"] == {"outgoingId": omen_id}, preview2["fingerprint"])
        applied2 = engine.god_apply(preview2["previewId"], "req-omen-2")
        assert_true(applied2["ok"], applied2)
        assert_true(applied2["outcome"]["outgoingId"] == omen_id, applied2["outcome"])
        assert_true(memory_hits("Seek reconciliation") == 1,
                    "replaced omen must enter memory exactly once")
        omen_id2 = applied2["outcome"]["interventionId"]

        # Revoke.
        preview3 = engine.god_preview(_revoke_envelope(omen_id2))
        assert_true(preview3["fingerprint"] == {"targetKind": "private_omen", "existed": True}, preview3["fingerprint"])
        applied3 = engine.god_apply(preview3["previewId"], "req-omen-revoke")
        assert_true(applied3["ok"], applied3)
        assert_true(key not in god["privateOmens"], god["privateOmens"])
        assert_true(memory_hits("A different path opens.") == 1,
                    "revoked omen must enter memory exactly once")

        # Expire.
        preview4 = engine.god_preview(
            _omen_envelope(target_id, "A third sign.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied4 = engine.god_apply(preview4["previewId"], "req-omen-4")
        assert_true(applied4["ok"], applied4)
        expires_frame = god["privateOmens"][key]["expiresFrame"]
        engine.frameTick = expires_frame - 1
        engine._expire_divine_effects()
        assert_true(key in god["privateOmens"], "omen closed one frame too early")
        assert_true(memory_hits("A third sign.") == 0, "expiring omen wrote memory before its expiresFrame")
        engine.frameTick = expires_frame
        engine._expire_divine_effects()
        assert_true(key not in god["privateOmens"], "expired omen not removed")
        assert_true(memory_hits("A third sign.") == 1, "expired omen must enter memory exactly once")

        # A second expiry sweep against an already-closed key must never
        # double-fire (the key is gone, so this is a no-op by construction).
        engine._expire_divine_effects()
        assert_true(memory_hits("A third sign.") == 1, "a second expiry sweep re-fired the omen memory")
        print("  OK private omen set/replace/revoke/expire; memory write exactly once, "
              "never while active, never twice")
    finally:
        se.GOD_MODE_ENABLED = old


def test_omen_public_visibility_boundary():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]
        preview = engine.god_preview(_omen_envelope(target_id, "A secret trial awaits you.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-vis-1")
        assert_true(applied["ok"], applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("secret trial" not in dumped, "private omen text leaked into /state")
        assert_true(all(r.get("kind") != "private_omen" for r in snap["god"]["recentPublicInterventions"]),
                    "a private_omen intervention leaked into /state's recentPublicInterventions")
        assert_true(snap["god"]["providence"] is None, snap["god"])

        assert_true(not any("secret trial" in line for line in engine.activityLog),
                    "private omen leaked into public activity log")
        assert_true(not any("secret trial" in (c.get("message") or "") for c in engine.conversationLog),
                    "private omen leaked into public communication log")
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(not any("secret trial" in (entry.get("text") or "") for entry in chronicle),
                    "private omen leaked into Chronicle")

        sight = engine.god_sight()
        assert_true(sight["ok"], sight)
        sight_agent = next(a for a in sight["agents"] if a["id"] == target_id)
        assert_true(sight_agent["omen"] and sight_agent["omen"]["active"], sight_agent)
        sight_dump = json.dumps(sight)
        assert_true("secret trial" in sight_dump,
                    "sight should expose the intervention outcome text (authenticated-only view)")
        print("  OK private omen absent from /state/activity/communication/Chronicle; "
              "visible only via authenticated sight")
    finally:
        se.GOD_MODE_ENABLED = old


def test_whisper_campaign_batch_apply_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        text0, text1 = "Whisper alpha path.", "Whisper beta path."
        theme = "Secret campaign theme"
        preview = engine.god_preview(_whisper_campaign_envelope(
            theme,
            [{"targetId": a0["id"], "text": text0}, {"targetId": a1["id"], "text": text1}],
            duration=1000,
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-whisper-1")
        assert_true(applied.get("ok"), applied)
        campaign_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(campaign_id in god["whisperCampaigns"], god["whisperCampaigns"])
        assert_true(god["whisperCampaigns"][campaign_id]["theme"] == theme, god)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(theme not in dumped, "whisper campaign theme leaked into /state")
        assert_true(text0 not in dumped and text1 not in dumped,
                    "whisper omen text leaked into /state")
        assert_true("whisperCampaigns" not in dumped,
                    "whisperCampaigns map leaked into /state god snapshot")
        assert_true(all(r.get("kind") != "whisper_campaign"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "whisper_campaign leaked into recentPublicInterventions")

        _, priv0 = engine._divine_prompt_lines(a0)
        _, priv1 = engine._divine_prompt_lines(a1)
        assert_true(priv0 == text0 and priv1 == text1, (priv0, priv1))
        assert_true(priv0 != priv1, "each target must see a distinct private whisper line")

        dup_preview = engine.god_preview(_whisper_campaign_envelope(
            theme,
            [{"targetId": a0["id"], "text": "x"}, {"targetId": a0["id"], "text": "y"}],
            duration=1000,
        ))
        assert_true(not dup_preview.get("ok"), dup_preview)
        print("  OK whisper_campaign batch apply; theme/text/campaigns absent from /state; "
              "per-agent private lines differ")
    finally:
        se.GOD_MODE_ENABLED = old


def test_whisper_campaign_cancel_clears_linked_omens():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview = engine.god_preview(_whisper_campaign_envelope(
            "Cancel test theme",
            [{"targetId": a0["id"], "text": "Cancel whisper A."},
             {"targetId": a1["id"], "text": "Cancel whisper B."}],
            duration=5000,
        ))
        applied = engine.god_apply(preview["previewId"], "req-whisper-cancel")
        assert_true(applied.get("ok"), applied)
        campaign_id = applied["outcome"]["interventionId"]
        god = engine.civilization["godState"]
        assert_true(str(a0["id"]) in god["privateOmens"], god["privateOmens"])
        assert_true(str(a1["id"]) in god["privateOmens"], god["privateOmens"])

        cancelled = engine.god_cancel(campaign_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "whisper_campaign", cancelled)
        assert_true(campaign_id not in god.get("whisperCampaigns", {}), god)
        assert_true(str(a0["id"]) not in god["privateOmens"], god["privateOmens"])
        assert_true(str(a1["id"]) not in god["privateOmens"], god["privateOmens"])

        _, priv0 = engine._divine_prompt_lines(a0)
        _, priv1 = engine._divine_prompt_lines(a1)
        assert_true(priv0 is None and priv1 is None, (priv0, priv1))
        print("  OK god_cancel on whisper_campaign id revokes all linked omens and removes campaign")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_payload_overlay_and_fast_model():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 1, "need a living agent")
        agent = agents[0]
        preview = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 0.95, top_p=0.55, top_k=15, min_p=0.1))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-sampling-overlay")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            think = engine._build_think_payload(agent)
        assert_true(think.get("divine_sampling") == {
            "model": "sim-smart", "temperature": 0.95,
            "top_p": 0.55, "top_k": 15, "min_p": 0.1,
        }, think.get("divine_sampling"))

        payload = srv.build_decision_payload(think, "", srv.build_response_format())
        assert_true(payload.get("model") == srv.MODEL_SMART, payload)
        assert_true(payload.get("temperature") == 0.95, payload)
        assert_true(payload.get("top_p") == 0.55, payload)
        assert_true(payload.get("top_k") == 15, payload)
        assert_true(payload.get("min_p") == 0.1, payload)
        assert_true(srv.model_for_decision(think) == srv.MODEL_SMART, think)

        # Replace with sim-fast on the same agent (allowed — cap excludes self).
        preview_fast = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 1.2, model="sim-fast"))
        assert_true(preview_fast.get("ok"), preview_fast)
        applied_fast = engine.god_apply(preview_fast["previewId"], "req-sampling-fast")
        assert_true(applied_fast.get("ok"), applied_fast)
        with engine.lock:
            think_fast = engine._build_think_payload(agent)
        payload_fast = srv.build_decision_payload(think_fast, "", srv.build_response_format())
        assert_true(payload_fast.get("model") == srv.MODEL_FAST, payload_fast)
        assert_true(srv.model_for_decision(think_fast) == srv.MODEL_FAST, think_fast)
        print("  OK agent_sampling overlays build_decision_payload (smart + fast)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_expiry_revoke_and_cancel_restore_defaults():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        agent = agents[0]
        preview = engine.god_preview(_agent_sampling_envelope(
            agent["id"], 0.88, durationFrames=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-sampling-expire")
        assert_true(applied.get("ok"), applied)
        intervention_id = applied["outcome"]["interventionId"]

        with engine.lock:
            think = engine._build_think_payload(agent)
        assert_true(think.get("divine_sampling") is not None, think)

        with engine.lock:
            engine.frameTick += se.GOD_GUIDANCE_MIN_DURATION_FRAMES
            engine._expire_divine_effects()
            think_after = engine._build_think_payload(agent)
        assert_true(think_after.get("divine_sampling") is None, think_after)
        payload_after = srv.build_decision_payload(think_after, "", srv.build_response_format())
        assert_true(payload_after.get("temperature") == 0.4, payload_after)
        assert_true(payload_after.get("model") == srv.MODEL_SMART, payload_after)

        preview2 = engine.god_preview(_agent_sampling_envelope(agent["id"], 0.77))
        applied2 = engine.god_apply(preview2["previewId"], "req-sampling-revoke")
        assert_true(applied2.get("ok"), applied2)
        with engine.lock:
            think_live = engine._build_think_payload(agent)
        assert_true(think_live.get("divine_sampling") is not None, think_live)

        revoke_preview = engine.god_preview(_revoke_agent_sampling_envelope(agent["id"]))
        assert_true(revoke_preview.get("ok"), revoke_preview)
        revoked = engine.god_apply(revoke_preview["previewId"], "req-sampling-revoked")
        assert_true(revoked.get("ok"), revoked)
        with engine.lock:
            think_revoked = engine._build_think_payload(agent)
        assert_true(think_revoked.get("divine_sampling") is None, think_revoked)

        preview3 = engine.god_preview(_agent_sampling_envelope(agent["id"], 0.66))
        applied3 = engine.god_apply(preview3["previewId"], "req-sampling-cancel")
        assert_true(applied3.get("ok"), applied3)
        cancel_id = applied3["outcome"]["interventionId"]
        cancelled = engine.god_cancel(cancel_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(cancelled.get("targetKind") == "agent_sampling", cancelled)
        with engine.lock:
            think_cancelled = engine._build_think_payload(agent)
        assert_true(think_cancelled.get("divine_sampling") is None, think_cancelled)
        print("  OK agent_sampling expiry/revoke/cancel restore default decision payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_sampling_fast_route_cap_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need at least two living agents")
        a0, a1 = agents[0], agents[1]
        preview0 = engine.god_preview(_agent_sampling_envelope(a0["id"], 0.5, model="sim-fast"))
        assert_true(preview0.get("ok"), preview0)
        applied0 = engine.god_apply(preview0["previewId"], "req-sampling-cap-0")
        assert_true(applied0.get("ok"), applied0)

        preview1 = engine.god_preview(_agent_sampling_envelope(a1["id"], 0.5, model="sim-fast"))
        assert_true(not preview1.get("ok"), preview1)
        assert_true("sim-fast" in (preview1.get("reason") or ""), preview1)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("agentSampling" not in dumped, "agentSampling map leaked into /state")
        assert_true(all(r.get("kind") != "agent_sampling"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "agent_sampling leaked into recentPublicInterventions")
        print("  OK second sim-fast decision override rejected; agentSampling absent from /state")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_insert_query_and_delete_by_keyword():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, store = make_engine_with_memory()
        target = engine.agents[0]
        target_id = target["id"]
        secret = "SMOKE_FALSE_MEMORY_ALPHA_42"

        preview = engine.god_preview(_memory_insert_envelope(target_id, secret, salience=0.85))
        assert_true(preview.get("ok"), preview)
        assert_true(preview.get("previewOutcome", {}).get("kind") == se.GOD_MEMORY_DEFAULT_KIND,
                    preview.get("previewOutcome"))
        applied = engine.god_apply(preview["previewId"], "req-mem-insert-1")
        assert_true(applied.get("ok"), applied)
        assert_true(secret not in json.dumps(applied.get("outcome") or {}),
                    "apply outcome must not echo memory text")

        hits = store.query(agent=target["name"], text=secret, top_k=3)
        assert_true(any(secret in (e.get("text") or "") for e in hits), hits)
        assert_true(any(secret in line for line in target["memory"]["working"]), target["memory"])

        preview_del = engine.god_preview(_memory_delete_envelope(target_id, keyword="ALPHA_42"))
        assert_true(preview_del.get("ok"), preview_del)
        assert_true(preview_del["previewOutcome"]["wouldDelete"] >= 1, preview_del["previewOutcome"])
        applied_del = engine.god_apply(preview_del["previewId"], "req-mem-del-1")
        assert_true(applied_del.get("ok"), applied_del)
        assert_true(applied_del["outcome"]["deletedCount"] >= 1, applied_del["outcome"])
        hits_after = store.query(agent=target["name"], text=secret, top_k=3)
        assert_true(not any(secret in (e.get("text") or "") for e in hits_after), hits_after)
        assert_true(not any(secret in line for line in target["memory"]["working"]),
                    "keyword purge should mirror local working tier")
        print("  OK memory_insert -> query hit; memory_delete by keyword removes store + local tier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_belief_plant_appears_in_think_payload():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target = engine.agents[0]
        target_id = target["id"]
        custom = "The stars whisper that winter will be mild."
        preview = engine.god_preview(_belief_plant_envelope(
            target_id, text=custom, plant_in_meme_texts=True))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-belief-1")
        assert_true(applied.get("ok"), applied)
        belief_id = applied["outcome"]["beliefId"]
        assert_true(belief_id in target["beliefs"], target["beliefs"])

        payload = engine._build_think_payload(target)
        belief_texts = payload.get("beliefs") or []
        assert_true(any(custom in t for t in belief_texts), belief_texts)
        assert_true(belief_id in (payload.get("belief_ids") or []), payload.get("belief_ids"))
        print("  OK belief_plant adds belief id and tenet to think payload beliefs line")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_surgery_privacy_no_public_leak():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target_id = engine.agents[0]["id"]
        secret = "SMOKE_DIVINE_MEMORY_SECRET_ZETA"
        custom_belief = "Secret divine doctrine about hidden treasure."

        preview = engine.god_preview(_memory_insert_envelope(target_id, secret))
        engine.god_apply(preview["previewId"], "req-mem-privacy-1")
        preview2 = engine.god_preview(_belief_plant_envelope(
            target_id, text=custom_belief, plant_in_meme_texts=False))
        engine.god_apply(preview2["previewId"], "req-mem-privacy-2")

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(secret not in dumped, "false memory text leaked into /state")
        assert_true(all(r.get("kind") not in ("memory_insert", "memory_delete", "belief_plant")
                        for r in snap["god"]["recentPublicInterventions"]),
                    "memory surgery kinds leaked into recentPublicInterventions")
        assert_true(not any(secret in line for line in engine.activityLog),
                    "false memory leaked into activity log")
        assert_true(not any(secret in (c.get("message") or "") for c in engine.conversationLog),
                    "false memory leaked into conversation log")
        chronicle = engine.civilization.get("chronicle") or []
        assert_true(not any(secret in (entry.get("text") or "") for entry in chronicle),
                    "false memory leaked into chronicle")

        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == target_id)
        assert_true("memoryCounts" in sight_agent, sight_agent)
        assert_true(sight_agent["beliefCount"] >= 1, sight_agent)
        sight_dump = json.dumps(sight)
        assert_true(secret not in sight_dump, "memory text must not appear in sight")
        print("  OK memory surgery absent from /state activity/chronicle/god public; sight counts only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_memory_delete_requires_filter():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine, _store = make_engine_with_memory()
        target_id = engine.agents[0]["id"]
        rejected = engine.god_preview(_memory_delete_envelope(target_id))
        assert_true(not rejected.get("ok"), rejected)
        assert_true("at least one" in (rejected.get("reason") or ""), rejected)
        print("  OK memory_delete rejected without filters")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_blue_pill_strips_divine_lines():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        other = engine.agents[1] if len(engine.agents) > 1 else agent

        prov_preview = engine.god_preview(_providence_envelope("Public providence smoke.", duration=5000))
        engine.god_apply(prov_preview["previewId"], "req-mask-prov")
        omen_preview = engine.god_preview(_omen_envelope(agent["id"], "Private omen smoke.", duration=5000))
        engine.god_apply(omen_preview["previewId"], "req-mask-omen")
        other_omen = engine.god_preview(_omen_envelope(other["id"], "OTHER_AGENT_SECRET_OMEN", duration=5000))
        engine.god_apply(other_omen["previewId"], "req-mask-other-omen")

        with engine.lock:
            before = engine._build_think_payload(agent)
        assert_true(before.get("divine_public_line"), before)
        assert_true(before.get("divine_private_line"), before)

        mask_preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        assert_true(mask_preview.get("ok"), mask_preview)
        applied = engine.god_apply(mask_preview["previewId"], "req-mask-blue")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            masked = engine._build_think_payload(agent)
        assert_true(masked.get("divine_public_line") is None, masked)
        assert_true(masked.get("divine_private_line") is None, masked)
        assert_true(masked.get("divine_public_event_line") is None, masked)
        print("  OK blue_pill strips divine public/private/event lines from think payload")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_red_pill_truth_without_private_omen_leak():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        import server as srv  # noqa: E402
        engine = make_engine()
        target = engine.agents[0]
        other = engine.agents[1] if len(engine.agents) > 1 else target
        secret = "OTHER_AGENT_RED_PILL_SECRET_OMEN"

        engine.god_apply(
            engine.god_preview(_omen_envelope(other["id"], secret, duration=5000))["previewId"],
            "req-red-other-omen")
        engine.civilization["godState"]["intervened"] = True

        mask_preview = engine.god_preview(_context_mask_envelope(
            target["id"], "red_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(mask_preview["previewId"], "req-mask-red")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            payload = engine._build_think_payload(target)
        truth = payload.get("divine_simulation_truth_line") or ""
        assert_true("SIMULATION TRUTH" in truth, truth)
        assert_true(secret not in truth, "red pill leaked another agent's private omen")
        assert_true(secret not in json.dumps(payload), payload)
        prompt = srv.build_user_prompt(payload)
        assert_true("SIMULATION TRUTH" in prompt, prompt)
        assert_true(secret not in prompt, prompt)
        print("  OK red_pill injects simulation-truth line without leaking other private omens")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_whisper_chain_forges_payload_only():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        true_line = "TRUE_CONVERSATION_LOG_MARKER_XYZ"
        engine.conversationLog.insert(0, {
            "from": "Alice", "to": agent["name"], "message": true_line,
            "kind": "speech", "frame": engine.frameTick,
        })
        forged = [{"from": "Morpheus", "to": agent["name"], "message": "Wake up."}]

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "whisper_chain",
            duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES,
            forgedConversations=forged))
        applied = engine.god_apply(preview["previewId"], "req-mask-whisper")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            payload = engine._build_think_payload(agent)
        assert_true("Morpheus" in (payload.get("recent_conversations") or ""), payload)
        assert_true(true_line not in (payload.get("recent_conversations") or ""), payload)
        assert_true(any(true_line in (c.get("message") or "") for c in engine.conversationLog),
                    "conversationLog must remain unchanged")
        print("  OK whisper_chain forges recent_conversations in payload only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_dream_replaces_and_rejects_unknown_keys():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        with engine.lock:
            base = engine._build_think_payload(agent)
        real_wood = (base.get("resources") or {}).get("wood", 0)

        bad = engine.god_preview(_context_mask_envelope(
            agent["id"], "dream",
            dreamSnapshot={"resources": {"wood": 99}, "evil_key": 1}))
        assert_true(not bad.get("ok"), bad)
        assert_true("unknown keys" in (bad.get("reason") or ""), bad)

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "dream",
            duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES,
            dreamSnapshot={"resources": {"wood": 99}, "hunger": 42.0}))
        applied = engine.god_apply(preview["previewId"], "req-mask-dream")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            dreamed = engine._build_think_payload(agent)
        assert_true(dreamed["resources"].get("wood") == 99, dreamed["resources"])
        assert_true(dreamed.get("hunger") == 42.0, dreamed)
        assert_true(real_wood != 99 or dreamed["resources"].get("wood") == 99, dreamed)
        print("  OK dream replaces allowlisted fields; unknown dreamSnapshot keys rejected")
    finally:
        se.GOD_MODE_ENABLED = old


def test_context_mask_cancel_expiry_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        prov_preview = engine.god_preview(_providence_envelope("Mask cancel providence.", duration=5000))
        engine.god_apply(prov_preview["previewId"], "req-mask-cancel-prov")

        preview = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-mask-expire")
        assert_true(applied.get("ok"), applied)
        mask_id = applied["outcome"]["interventionId"]

        with engine.lock:
            live = engine._build_think_payload(agent)
        assert_true(live.get("divine_public_line") is None, live)
        god = engine.civilization["godState"]
        assert_true(str(agent["id"]) in god.get("contextMasks", {}), god)

        cancelled = engine.god_cancel(mask_id)
        assert_true(cancelled.get("cancelled") is True, cancelled)
        assert_true(str(agent["id"]) not in god.get("contextMasks", {}), god)
        with engine.lock:
            after_cancel = engine._build_think_payload(agent)
        assert_true(after_cancel.get("divine_public_line") == "Mask cancel providence.", after_cancel)

        preview2 = engine.god_preview(_context_mask_envelope(
            agent["id"], "blue_pill", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        engine.god_apply(preview2["previewId"], "req-mask-expire2")
        with engine.lock:
            engine.frameTick += se.GOD_GUIDANCE_MIN_DURATION_FRAMES
            engine._expire_divine_effects()
        assert_true(str(agent["id"]) not in god.get("contextMasks", {}), god)

        snap = engine.snapshot()
        assert_true("contextMasks" not in json.dumps(snap.get("god") or {}), snap)
        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == agent["id"])
        assert_true(sight_agent.get("contextMask") is None, sight_agent)
        assert_true(all(r.get("kind") != "context_mask"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "context_mask leaked into recentPublicInterventions")
        print("  OK context_mask cancel/expiry clears; absent from /state public god")
    finally:
        se.GOD_MODE_ENABLED = old


def test_decision_compulsion_forces_pinned_action():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_decision_compulsion_envelope(
            agent["id"], action="rest", remainingTurns=2))
        assert_true(preview.get("ok"), preview)
        engine.god_apply(preview["previewId"], "req-compulsion-1")
        with engine.lock:
            engine._apply_gated_decision(agent, {
                "action": "collect_resource", "reasoning": "would gather",
            })
        assert_true(agent.get("lastAction") == "rest", agent)
        conv = [e for e in engine.conversationLog if e.get("source") == "divine"]
        assert_true(any("compulsion" in (e.get("message") or "").lower() for e in conv), conv)
        print("  OK decision_compulsion replaces LLM-path decision with pinned rest")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_possession_skips_llm():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        calls = {"n": 0}
        engine.d["llm_decide"] = lambda payload: calls.__setitem__("n", calls["n"] + 1) or {
            "action": "collect_resource", "reasoning": "smoke llm",
        }
        preview = engine.god_preview(_agent_possession_envelope(agent["id"], duration=5000))
        assert_true(preview.get("ok"), preview)
        engine.god_apply(preview["previewId"], "req-possession-1")
        engine._think_job(agent["name"])
        assert_true(calls["n"] == 0, calls)
        assert_true(agent.get("lastAction") == "rest", agent)
        bench = engine.lastBenchmarks or {}
        assert_true(engine.lastBenchmarks.get("divine_possession_skip") == 1.0
                    or any(
                        (engine.civilization.get("godState") or {}).get("decisionGates"),
                    ), agent)
        print("  OK agent_possession skips llm_decide and applies pinned action")
    finally:
        se.GOD_MODE_ENABLED = old


def test_veto_hold_and_resolve():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_decision_veto_arm_envelope(agent["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-veto-arm-1")
        with engine.lock:
            held = engine._apply_gated_decision(agent, {
                "action": "rest", "reasoning": "candidate",
            })
        assert_true(held is False, held)
        assert_true(agent.get("divineHold"), agent)
        gate = engine._god_active_decision_gate_record(agent["id"])
        assert_true(gate.get("status") == "holding", gate)
        assert_true(gate.get("pendingDecision", {}).get("action") == "rest", gate)
        resolve_preview = engine.god_preview(_decision_veto_resolve_envelope(
            agent["id"], "reject"))
        applied = engine.god_apply(resolve_preview["previewId"], "req-veto-resolve-1")
        assert_true(applied.get("ok"), applied)
        assert_true(not agent.get("divineHold"), agent)
        assert_true(engine._god_active_decision_gate_record(agent["id"]) is None, agent)
        print("  OK veto arms, holds candidate, resolve clears hold and applies")
    finally:
        se.GOD_MODE_ENABLED = old


def test_sage_emergency_bypasses_decision_gate():
    old = se.GOD_MODE_ENABLED
    old_survival = se.SURVIVAL_ENABLED
    se.GOD_MODE_ENABLED = True
    se.SURVIVAL_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        elder = next((a for a in engine.agents if a["role"] == "elder"), None)
        responder = next((a for a in engine.agents if a["role"] != "elder"), engine.agents[0])
        if elder is None:
            elder = engine.agents[0]
            elder["role"] = "elder"
        preview = engine.god_preview(_agent_possession_envelope(responder["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-sage-bypass-possession")
        elder["incapacitated"] = True
        elder["health"] = 5.0
        with engine.lock:
            engine._rush_to_heal(responder, elder)
        assert_true(responder.get("lastAction") == "heal_agent", responder)
        print("  OK Sage _rush_to_heal bypasses possession gate")
    finally:
        se.GOD_MODE_ENABLED = old
        se.SURVIVAL_ENABLED = old_survival


def test_veto_hold_cap():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition(roster_size=5)
        agents = engine.agents[:4]
        for i, agent in enumerate(agents[:3]):
            preview = engine.god_preview(_decision_veto_arm_envelope(agent["id"], duration=5000))
            engine.god_apply(preview["previewId"], f"req-veto-cap-arm-{i}")
            with engine.lock:
                engine._apply_gated_decision(agent, {"action": "rest"})
        assert_true(engine._god_veto_hold_count() == se.GOD_VETO_HOLD_CAP, engine._god_veto_hold_count())
        fourth = agents[3]
        preview = engine.god_preview(_decision_veto_arm_envelope(fourth["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-veto-cap-arm-4")
        with engine.lock:
            engine._apply_gated_decision(fourth, {"action": "collect_resource"})
        assert_true(fourth.get("lastAction") == "rest", fourth)
        print("  OK concurrent veto holds capped at GOD_VETO_HOLD_CAP")
    finally:
        se.GOD_MODE_ENABLED = old


def test_decision_gate_privacy_and_sight():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine_with_cognition()
        agent = engine.agents[0]
        preview = engine.god_preview(_agent_possession_envelope(agent["id"], duration=5000))
        engine.god_apply(preview["previewId"], "req-gate-privacy")
        snap = engine.snapshot()
        assert_true("decisionGates" not in json.dumps(snap.get("god") or {}), snap)
        assert_true(all(r.get("kind") != "agent_possession"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "agent_possession leaked into recentPublicInterventions")
        sight = engine.god_sight()
        sight_agent = next(a for a in sight["agents"] if a["id"] == agent["id"])
        assert_true(sight_agent.get("decisionGate", {}).get("mode") == "possession", sight_agent)
        assert_true(sight_agent.get("decisionGate", {}).get("pinnedAction") == "rest", sight_agent)
        print("  OK decisionGates absent from /state; Sight shows gate status summary")
    finally:
        se.GOD_MODE_ENABLED = old


def test_burning_bush_message_target_only_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need two agents")
        target, other = agents[0], agents[1]
        secret = "The bush speaks only to you."
        preview = engine.god_preview(_burning_bush_message_envelope(target["id"], secret))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bush-1")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(secret not in dumped, "burning bush text leaked into /state")
        assert_true("burningBush" not in dumped, "burningBush map leaked into /state")
        assert_true(all(r.get("kind") != "burning_bush_message"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "burning_bush_message leaked into recentPublicInterventions")

        target_line = engine._burning_bush_prompt_line(target)
        other_line = engine._burning_bush_prompt_line(other)
        assert_true(target_line and secret in target_line, target_line)
        assert_true(other_line is None, other_line)

        payload = engine._build_think_payload(target)
        assert_true(secret in (payload.get("divine_burning_bush_line") or ""), payload)
        other_payload = engine._build_think_payload(other)
        assert_true(other_payload.get("divine_burning_bush_line") is None, other_payload)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        assert_true(sight_target.get("burningBush", {}).get("active") is True, sight_target)
        assert_true(sight_target.get("burningBush", {}).get("messageCount") == 1, sight_target)
        assert_true(secret not in json.dumps(sight_target), "thread text leaked into Sight")
        print("  OK burning_bush_message appears only in target prompt; no /state leak")
    finally:
        se.GOD_MODE_ENABLED = old


def test_anoint_destiny_stigmata_oracle_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agents = [a for a in engine.agents if a.get("deathFrame") is None]
        assert_true(len(agents) >= 2, "need two agents")
        target, neighbor = agents[0], agents[1]
        target["x"], target["y"] = 100.0, 100.0
        neighbor["x"], neighbor["y"] = 105.0, 100.0

        destiny = "You will unite the village."
        stigma = "golden aura"
        oracle_now = "The river runs backward."
        oracle_later = "A crown awaits."
        ft = engine.frameTick
        preview = engine.god_preview(_anoint_envelope(
            target["id"], destiny,
            stigmataTags=[stigma],
            oracleHints=[
                {"text": oracle_now, "revealFrame": ft},
                {"text": oracle_later, "revealFrame": ft + 5000},
            ],
            durationFrames=9000,
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-anoint-1")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true(destiny not in dumped, "destiny leaked into /state")
        assert_true(oracle_now not in dumped, "oracle leaked into /state")
        assert_true("anointments" not in dumped, "anointments map leaked into /state")
        assert_true(all(r.get("kind") != "anoint"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "anoint leaked into recentPublicInterventions")

        target_line = engine._anointment_prompt_line(target)
        assert_true(target_line and destiny in target_line, target_line)
        assert_true(oracle_now in target_line, target_line)
        assert_true(oracle_later not in target_line, target_line)

        neighbor_payload = engine._build_think_payload(neighbor)
        nearby = neighbor_payload.get("nearby_agents") or []
        target_near = next((n for n in nearby if n.get("name") == target["name"]), None)
        assert_true(target_near is not None, nearby)
        assert_true(stigma in (target_near.get("stigmata") or []), target_near)

        from server import format_nearby_agents, build_user_prompt  # noqa: E402
        formatted = format_nearby_agents(nearby)
        assert_true(stigma in formatted, formatted)
        assert_true(destiny not in formatted, formatted)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        anoint_status = sight_target.get("anointment") or {}
        assert_true(anoint_status.get("active") is True, sight_target)
        assert_true(anoint_status.get("tagCount") == 1, sight_target)
        assert_true(anoint_status.get("nextOracleFrame") == ft + 5000, sight_target)
        assert_true(destiny not in json.dumps(sight_target), "destiny leaked into Sight")

        revoke_preview = engine.god_preview(_revoke_anoint_envelope(target["id"]))
        assert_true(revoke_preview.get("ok"), revoke_preview)
        revoked = engine.god_apply(revoke_preview["previewId"], "req-anoint-revoke")
        assert_true(revoked.get("ok"), revoked)
        assert_true(engine._anointment_prompt_line(target) is None, "anoint not cleared")
        neighbor_after = engine._build_think_payload(neighbor)
        nearby_after = neighbor_after.get("nearby_agents") or []
        target_near_after = next((n for n in nearby_after if n.get("name") == target["name"]), None)
        assert_true(target_near_after is not None, nearby_after)
        assert_true(not target_near_after.get("stigmata"), target_near_after)

        print("  OK anoint destiny/oracle private; stigmata in neighbor prompt; revoke clears")
    finally:
        se.GOD_MODE_ENABLED = old


def _revoke_anoint_envelope(target_id):
    return {"kind": "revoke_anoint", "payload": {"targetId": target_id}}


def _identity_edit_envelope(target_id, **fields):
    payload = {"targetId": target_id}
    payload.update(fields)
    return {"kind": "identity_edit", "payload": payload}


def _identity_copy_envelope(target_id, source_id, rate=0.25, **extra):
    payload = {"targetId": target_id, "sourceId": source_id, "ratePerThink": rate}
    payload.update(extra)
    return {"kind": "identity_copy_overwrite", "payload": payload}


def _identity_forge_cancel_envelope(target_id):
    return {"kind": "identity_forge_cancel", "payload": {"targetId": target_id}}


def test_identity_forge_edit_copy_cancel_and_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine(roster_size=4)
        roles = _load_roles()
        target = engine.agents[0]
        source = engine.agents[1]
        original_role = target["role"]
        original_personality = target["personality"]
        new_role = next(r for r in roles if r != original_role)

        preview = engine.god_preview(_identity_edit_envelope(
            target["id"], role=new_role, personality="divine-forged personality"))
        assert_true(preview.get("ok"), preview)
        outcome = preview.get("previewOutcome") or {}
        assert_true(outcome.get("newRoleSkill") == roles[new_role].get("skill", "helps"),
                    outcome)
        if original_role == "elder" or new_role == "elder":
            assert_true(outcome.get("warning"), outcome)

        applied = engine.god_apply(preview["previewId"], "req-identity-edit")
        assert_true(applied.get("ok"), applied)
        assert_true(target["role"] == new_role, target)
        assert_true(target["personality"] == "divine-forged personality", target)

        payload = engine._build_think_payload(target)
        assert_true(payload.get("role") == new_role, payload)
        assert_true(payload.get("role_skill") == engine.d["ROLE_SKILLS"].get(new_role),
                    payload)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("identityForges" not in dumped, "identityForges leaked into /state")
        assert_true(all(r.get("kind") != "identity_edit"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "identity_edit leaked into recentPublicInterventions")

        source["persona"] = "SOURCE PERSONA LINE"
        source["personality"] = "SOURCE PERSONALITY TRAIT"
        target["persona"] = "TARGET START"
        target["personality"] = "TARGET START PERSONALITY"

        copy_preview = engine.god_preview(_identity_copy_envelope(
            target["id"], source["id"], rate=0.25))
        assert_true(copy_preview.get("ok"), copy_preview)
        copy_applied = engine.god_apply(copy_preview["previewId"], "req-identity-copy")
        assert_true(copy_applied.get("ok"), copy_applied)

        forge = engine.civilization["godState"]["identityForges"][str(target["id"])]
        assert_true(forge.get("progress", 0) > 0, forge)
        progress_after_apply = forge.get("progress")

        with engine.lock:
            engine._finish_think_identity_forge(target)
        progress_after_think = engine.civilization["godState"]["identityForges"][
            str(target["id"])].get("progress")
        assert_true(progress_after_think > progress_after_apply, forge)
        assert_true(target["personality"] != "TARGET START PERSONALITY", target)

        for _ in range(3):
            with engine.lock:
                engine._finish_think_identity_forge(target)
        assert_true(target["personality"] == source["personality"], target)

        sight = engine.god_sight()
        sight_target = next(a for a in sight["agents"] if a["id"] == target["id"])
        forge_status = sight_target.get("identityForge") or {}
        assert_true(forge_status.get("active") is True, sight_target)
        assert_true(forge_status.get("copyFromId") == source["id"], sight_target)
        assert_true(forge_status.get("progress") == 1.0, sight_target)
        assert_true("SOURCE PERSONALITY" not in json.dumps(sight_target),
                    "personality leaked into Sight")

        cancel_preview = engine.god_preview(_identity_forge_cancel_envelope(target["id"]))
        assert_true(cancel_preview.get("ok"), cancel_preview)
        cancelled = engine.god_apply(cancel_preview["previewId"], "req-identity-cancel")
        assert_true(cancelled.get("ok"), cancelled)
        assert_true(target["role"] == original_role, target)
        assert_true(target["personality"] == original_personality, target)

        print("  OK identity edit role_skill; copy progresses; cancel restores; privacy")
    finally:
        se.GOD_MODE_ENABLED = old


def test_bargain_success_grants_resource():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        resource_id = "wood"
        held_before = agent["resources"].get(resource_id, 0)
        agent["resources"][resource_id] = held_before + 3
        preview = engine.god_preview(_merovingian_bargain_envelope(
            agent["id"],
            "Bring wood and be rewarded.",
            {"kind": "agent_has_resource", "resourceId": resource_id, "amount": 3},
            durationFrames=5000,
            rewardPrimitive={
                "kind": "grant_resource",
                "payload": {
                    "resourceId": resource_id,
                    "amount": 7,
                    "target": {"agentId": agent["id"]},
                },
            },
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bargain-success")
        assert_true(applied.get("ok"), applied)

        engine._tick_divine_bargains()
        bargain = engine.civilization["godState"]["burningBush"][str(agent["id"])]["bargain"]
        assert_true(bargain.get("status") == "success", bargain)
        assert_true(agent["resources"].get(resource_id, 0) >= held_before + 3 + 7,
                    agent["resources"])
        print("  OK bargain success predicate grants resource via tick settler")
    finally:
        se.GOD_MODE_ENABLED = old


def test_bargain_expiry_settles_failure():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        health_before = agent["health"]
        preview = engine.god_preview(_merovingian_bargain_envelope(
            agent["id"],
            "Fail if you do not comply in time.",
            {"kind": "frame_reached", "frame": engine.frameTick + 99999},
            durationFrames=10,
            punishPrimitive={
                "kind": "agent_vitals",
                "payload": {"targetId": agent["id"], "healthDelta": -5, "hungerDelta": 0},
            },
        ))
        assert_true(preview.get("ok"), preview)
        applied = engine.god_apply(preview["previewId"], "req-bargain-expiry")
        assert_true(applied.get("ok"), applied)

        engine.frameTick += 301
        engine._expire_divine_effects()
        bargain = engine.civilization["godState"]["burningBush"][str(agent["id"])]["bargain"]
        assert_true(bargain.get("status") == "failure", bargain)
        assert_true(bargain.get("settleTrigger") == "expiry", bargain)
        assert_true(agent["health"] < health_before, (health_before, agent["health"]))
        print("  OK bargain expiry settles failure path with punish primitive")
    finally:
        se.GOD_MODE_ENABLED = old


def test_directive_and_providence_stay_separate():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        elder = next((a for a in engine.agents if a["role"] == "elder"), engine.agents[0])
        engine.civilization["directive"] = "Elder Test directs: build the granary; gather wood"
        engine.civilization["directiveFrame"] = engine.frameTick

        preview = engine.god_preview(_providence_envelope("A great harvest is coming.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-directive-1")
        assert_true(applied["ok"], applied)

        payload = engine._build_think_payload(elder)
        assert_true(payload["directive"] == "Elder Test directs: build the granary; gather wood",
                    payload["directive"])
        assert_true(payload["divine_public_line"] == "A great harvest is coming.",
                    payload["divine_public_line"])
        assert_true(payload["divine_private_line"] is None, payload["divine_private_line"])
        print("  OK elder civilization['directive'] and divine providence stay independent, "
              "separately-labeled think-payload fields")
    finally:
        se.GOD_MODE_ENABLED = old


def test_prompt_lines_frame_window():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        preview = engine.god_preview(_providence_envelope("Frame window test.", duration=1000))
        applied = engine.god_apply(preview["previewId"], "req-window-1")
        assert_true(applied["ok"], applied)
        prov = engine.civilization["godState"]["providence"]
        created_frame, expires_frame = prov["createdFrame"], prov["expiresFrame"]

        engine.frameTick = created_frame
        pub, priv = engine._divine_prompt_lines(agent)
        assert_true(pub == "Frame window test." and priv is None, (pub, priv))

        engine.frameTick = expires_frame - 1
        pub, _ = engine._divine_prompt_lines(agent)
        assert_true(pub == "Frame window test.", "must still be active one frame before expiresFrame")

        engine.frameTick = expires_frame
        pub, _ = engine._divine_prompt_lines(agent)
        assert_true(pub is None, "must vanish exactly at expiresFrame")

        # Flag-off: inert regardless of any stored guidance.
        se.GOD_MODE_ENABLED = False
        engine.frameTick = created_frame
        pub_off, priv_off = engine._divine_prompt_lines(agent)
        assert_true(pub_off is None and priv_off is None, (pub_off, priv_off))
        print("  OK divine prompt line active for [createdFrame, expiresFrame) only, "
              "gone exactly at expiresFrame, inert with the flag off")
    finally:
        se.GOD_MODE_ENABLED = old


def test_prompt_size_cap_and_divine_lines_render():
    """Prompt-size assertion (task requirement): the two divine lines add a
    small, precisely bounded amount of text even at maximum omen length, and
    render nothing at all when unset -- proving flag-off/no-guidance prompts
    stay byte-identical to before this phase."""
    import server as _server  # noqa: E402 (module-cached; safe to import here)
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        base_payload = engine._build_think_payload(agent)

        max_text = "A" * se.GOD_TEXT_MAX_CHARS
        payload_with = dict(base_payload)
        payload_with["divine_public_line"] = max_text
        payload_with["divine_private_line"] = max_text
        payload_without = dict(base_payload)
        payload_without["divine_public_line"] = None
        payload_without["divine_private_line"] = None

        prompt_with = _server.build_user_prompt(payload_with)
        prompt_without = _server.build_user_prompt(payload_without)
        assert_true(f"Divine omen: {max_text} You may interpret or ignore it." in prompt_with,
                    "public omen line missing or mangled")
        assert_true(f"Private omen: {max_text} You may interpret or ignore it." in prompt_with,
                    "private omen line missing or mangled")
        assert_true("Divine omen:" not in prompt_without and "Private omen:" not in prompt_without,
                    "a divine line rendered when unset -- flag-off/no-guidance prompts must be byte-identical")

        added = len(prompt_with) - len(prompt_without)
        # "Divine omen: " (14) + text + " You may interpret or ignore it.\n" (34)
        # + "Private omen: " (15) + text + " You may interpret or ignore it.\n" (34)
        expected_max = 2 * (20 + se.GOD_TEXT_MAX_CHARS + 40)
        assert_true(added <= expected_max, (added, expected_max))
        print(f"  OK divine prompt lines add <= {expected_max} chars at max ({se.GOD_TEXT_MAX_CHARS}-char) "
              f"omen length; absent entirely when unset")
    finally:
        se.GOD_MODE_ENABLED = old


def test_restore_does_not_refire_omen_memory():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_omen_restore.db")
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]
        preview = engine.god_preview(
            _omen_envelope(target_id, "A quiet warning.", duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES))
        applied = engine.god_apply(preview["previewId"], "req-restore-omen")
        assert_true(applied["ok"], applied)
        expires_frame = engine.civilization["godState"]["privateOmens"][str(target_id)]["expiresFrame"]
        # Advance frameTick PAST expiry WITHOUT running the tick-based sweep
        # -- simulates a save captured right as the omen expired but before
        # _expire_divine_effects next ran.
        engine.frameTick = expires_frame

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        target = restored._find_agent_by_id(target_id)

        def memory_hits(text):
            return sum(text in line for line in target["memory"]["working"])

        assert_true(memory_hits("A quiet warning.") == 1,
                    "restore-time expiry must write the not-yet-written omen memory exactly once")
        assert_true(str(target_id) not in restored.civilization["godState"]["privateOmens"],
                    "restore-time expiry must close the expired omen")

        # A second restore-time expiry sweep against the already-closed
        # state must NOT re-fire the memory write.
        restored._expire_divine_effects(restore=True)
        assert_true(memory_hits("A quiet warning.") == 1,
                    "a second restore-time expiry sweep re-fired an already-written omen memory")
        print("  OK restore-time expiry writes an unwritten omen memory exactly once, never twice")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


# --- Phase 4 tests: bounded immediate miracles (docs/plan-sovereign-god-
# mode-v2.md "Immediate miracles" + "Honest reversibility" + Phase 4) ---

def test_agent_vitals_happy_path_and_clamping():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"], agent["hunger"] = 50.0, 50.0

        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=10, hunger_delta=-5))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "irreversible", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome == {
            "targetId": agent["id"], "targetName": agent["name"],
            "oldHealth": 50.0, "newHealth": 60.0, "oldHunger": 50.0, "newHunger": 45.0,
        }, outcome)
        applied = engine.god_apply(preview["previewId"], "req-vitals-1")
        assert_true(applied["ok"], applied)
        assert_true(applied["outcome"]["newHealth"] == outcome["newHealth"]
                    and applied["outcome"]["newHunger"] == outcome["newHunger"],
                    "applied value must equal the previewed value")
        assert_true(agent["health"] == 60.0 and agent["hunger"] == 45.0, (agent["health"], agent["hunger"]))
        assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])
        assert_true(any("divine" in line.lower() for line in engine.activityLog[:1]), engine.activityLog[0])
        assert_true(engine.civilization["chronicle"][-1]["source"] == "divine", engine.civilization["chronicle"][-1])

        # Clamp at 100.
        agent["health"] = 95.0
        p = engine.god_preview(_vitals_envelope(agent["id"], health_delta=50))
        assert_true(p["previewOutcome"]["newHealth"] == 100.0, p["previewOutcome"])
        a = engine.god_apply(p["previewId"], "req-vitals-clamp-100")
        assert_true(a["ok"] and agent["health"] == 100.0, (a, agent["health"]))

        # Clamp at 0 for hunger (no death floor -- hunger alone cannot kill).
        agent["hunger"] = 2.0
        p2 = engine.god_preview(_vitals_envelope(agent["id"], hunger_delta=-50))
        assert_true(p2["previewOutcome"]["newHunger"] == 0.0, p2["previewOutcome"])
        a2 = engine.god_apply(p2["previewId"], "req-vitals-clamp-hunger-0")
        assert_true(a2["ok"] and agent["hunger"] == 0.0, (a2, agent["hunger"]))

        print("  OK agent_vitals happy path + clamping at 0/100; applied == previewed; divine attribution")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_vitals_cannot_kill():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 2.0
        agent["incapacitated"] = False

        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=-se.GOD_VITALS_DELTA_MAX))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewOutcome"]["newHealth"] == se.GOD_VITALS_HEALTH_FLOOR,
                    preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-vitals-nokill-1")
        assert_true(applied["ok"], applied)
        assert_true(agent["health"] == se.GOD_VITALS_HEALTH_FLOOR, agent["health"])
        assert_true(agent["health"] > 0, "a negative vitals delta must never reach 0")
        assert_true(agent["deathFrame"] is None, "a negative vitals delta must never set deathFrame")

        # Repeated large negative deltas still never cross the floor.
        for i in range(3):
            p = engine.god_preview(_vitals_envelope(agent["id"], health_delta=-se.GOD_VITALS_DELTA_MAX))
            a = engine.god_apply(p["previewId"], f"req-vitals-nokill-repeat-{i}")
            assert_true(a["ok"], a)
            assert_true(agent["health"] == se.GOD_VITALS_HEALTH_FLOOR, agent["health"])
            assert_true(agent["deathFrame"] is None, "deathFrame must stay None across repeated damage")

        print(f"  OK agent_vitals cannot kill: health floors at {se.GOD_VITALS_HEALTH_FLOOR}, "
              f"deathFrame never set")
    finally:
        se.GOD_MODE_ENABLED = old


def test_agent_vitals_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target = engine.agents[0]

        unknown = engine.god_preview(_vitals_envelope(999999, health_delta=5))
        assert_true(unknown == {"ok": False, "reason": "unknown target agent"}, unknown)

        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_vitals_envelope(target["id"], health_delta=5))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None

        neither = engine.god_preview(_vitals_envelope(target["id"]))
        assert_true(neither == {
            "ok": False, "reason": "at least one of healthDelta/hungerDelta must be non-zero",
        }, neither)

        too_big = engine.god_preview(_vitals_envelope(target["id"], health_delta=se.GOD_VITALS_DELTA_MAX + 1))
        assert_true(too_big["ok"] is False and "magnitude exceeds" in too_big["reason"], too_big)
        print("  OK agent_vitals rejects unknown/dead targets, no-op deltas, and out-of-range magnitude")
    finally:
        se.GOD_MODE_ENABLED = old


def test_grant_resource_happy_path_and_carry_semantics():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        c = engine.civilization
        resource_id = "wood"
        assert_true(resource_id in c["resourceRegistry"], "test fixture assumes 'wood' is a known resource")

        # Stockpile grant.
        before = c["stockpile"].get(resource_id, 0)
        preview = engine.god_preview(_grant_envelope(resource_id, 15))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"] == {
            "resourceId": resource_id, "amount": 15, "targetKind": "stockpile",
            "agentAdded": 0, "stockpileAdded": 15,
        }, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-grant-stockpile")
        assert_true(applied["ok"], applied)
        assert_true(c["stockpile"].get(resource_id, 0) == before + 15, c["stockpile"])
        assert_true(engine.conversationLog[0]["source"] == "divine", engine.conversationLog[0])

        # Agent grant with carry-cap overflow: fill the agent close to cap,
        # then grant more than the remaining room -- remainder must route to
        # the stockpile, never be silently dropped or exceed capacity.
        agent = engine.agents[0]
        cap = engine._carry_cap(agent)
        agent["resources"][resource_id] = cap - 3
        stock_before = c["stockpile"].get(resource_id, 0)
        preview2 = engine.god_preview(_grant_envelope(resource_id, 10, target={"agentId": agent["id"]}))
        assert_true(preview2["ok"], preview2)
        outcome2 = preview2["previewOutcome"]
        assert_true(outcome2["agentAdded"] == 3 and outcome2["stockpileAdded"] == 7, outcome2)
        applied2 = engine.god_apply(preview2["previewId"], "req-grant-agent-overflow")
        assert_true(applied2["ok"], applied2)
        assert_true(agent["resources"][resource_id] == cap, "agent resources must not exceed carry cap")
        assert_true(c["stockpile"].get(resource_id, 0) == stock_before + 7, c["stockpile"])
        assert_true(applied2["outcome"]["agentAdded"] == outcome2["agentAdded"]
                    and applied2["outcome"]["stockpileAdded"] == outcome2["stockpileAdded"],
                    "applied split must equal the previewed split")
        print("  OK grant_resource happy path (stockpile + agent), carry-cap overflow routed to stockpile")
    finally:
        se.GOD_MODE_ENABLED = old


def test_grant_resource_rejections_and_caps():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target = engine.agents[0]

        unknown_resource = engine.god_preview(_grant_envelope("definitely-not-a-real-resource", 5))
        assert_true(unknown_resource == {"ok": False, "reason": "unknown resource id"}, unknown_resource)

        target["deathFrame"] = engine.frameTick
        dead = engine.god_preview(_grant_envelope("wood", 5, target={"agentId": target["id"]}))
        assert_true(dead == {"ok": False, "reason": "target agent is deceased"}, dead)
        target["deathFrame"] = None

        over_command_cap = engine.god_preview(_grant_envelope("wood", se.GOD_GRANT_PER_COMMAND_CAP + 1))
        assert_true(over_command_cap["ok"] is False
                    and "per-command cap" in over_command_cap["reason"], over_command_cap)

        # Session cap: drive the running total right up to the cap, then one
        # more unit must be refused.
        engine._god_grant_session_total = se.GOD_GRANT_SESSION_CAP - 5
        under_cap = engine.god_preview(_grant_envelope("wood", 5))
        assert_true(under_cap["ok"], under_cap)
        over_session_cap = engine.god_preview(_grant_envelope("wood", 6))
        assert_true(over_session_cap["ok"] is False
                    and "per-session cap" in over_session_cap["reason"], over_session_cap)
        applied = engine.god_apply(under_cap["previewId"], "req-grant-session-cap")
        assert_true(applied["ok"], applied)
        assert_true(engine._god_grant_session_total == se.GOD_GRANT_SESSION_CAP,
                    engine._god_grant_session_total)
        print("  OK grant_resource rejects unknown resource/dead target, "
              "enforces per-command and per-session caps")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_repair_and_damage():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()

        # Repair, clamped at 100.
        s = _add_test_structure(engine, condition=90.0)
        preview = engine.god_preview(_structure_envelope(s["id"], 50))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"] == {
            "structureId": s["id"], "structureName": s["name"],
            "oldCondition": 90.0, "newCondition": 100.0, "wouldBecomeRuin": False,
        }, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-struct-repair")
        assert_true(applied["ok"], applied)
        assert_true(s["condition"] == 100.0 and not s["isRuin"], s)
        assert_true(applied["outcome"]["newCondition"] == preview["previewOutcome"]["newCondition"],
                    "applied value must equal the previewed value")

        # Damage that does NOT cross the ruin threshold.
        s2 = _add_test_structure(engine, condition=80.0)
        preview2 = engine.god_preview(_structure_envelope(s2["id"], -20))
        applied2 = engine.god_apply(preview2["previewId"], "req-struct-damage-partial")
        assert_true(applied2["ok"] and s2["condition"] == 60.0 and not s2["isRuin"], (applied2, s2))

        print("  OK structure_condition repair (clamped at 100) and partial damage; applied == previewed")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_damage_crosses_ruin_with_homeless_handling():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        owner = engine.agents[0]
        s = _add_test_structure(engine, condition=5.0, home_of=owner["name"])
        owner["homeStructureId"] = s["id"]
        before_activity_len = len(engine.activityLog)

        preview = engine.god_preview(_structure_envelope(s["id"], -20))
        assert_true(preview["ok"], preview)
        assert_true(preview["previewOutcome"]["wouldBecomeRuin"] is True, preview["previewOutcome"])
        assert_true(preview["previewOutcome"]["newCondition"] == 0.0, preview["previewOutcome"])
        applied = engine.god_apply(preview["previewId"], "req-struct-ruin")
        assert_true(applied["ok"], applied)
        assert_true(applied["outcome"]["becameRuin"] is True, applied["outcome"])

        # Same ruin path as natural decay: condition floors at 0, isRuin set,
        # homeOf cleared, owner's homeStructureId cleared, homeless narration
        # emitted -- not a shortcut around _apply_structure_condition_delta.
        assert_true(s["condition"] == 0.0 and s["isRuin"] is True, s)
        assert_true(s["homeOf"] is None, s)
        assert_true(owner["homeStructureId"] is None, owner)
        new_activity = engine.activityLog[:len(engine.activityLog) - before_activity_len + 1]
        assert_true(any("homeless" in line for line in new_activity), engine.activityLog[:5])
        assert_true(any("collapsed into a ruin" in line for line in new_activity), engine.activityLog[:5])

        # Single-target structure_condition still rejects already-ruined
        # structures; batch repair_structures is the operator un-ruin path.
        already_ruin = engine.god_preview(_structure_envelope(s["id"], 10))
        assert_true(already_ruin == {"ok": False, "reason": "structure is already ruined"}, already_ruin)
        print("  OK structure_condition damage crossing the ruin threshold uses the normal ruin "
              "path (homeOf/homeStructureId cleared, homeless narration, decay-identical transition); "
              "single-target repair still rejects ruins")
    finally:
        se.GOD_MODE_ENABLED = old


def test_structure_condition_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        unknown = engine.god_preview(_structure_envelope(999999, 10))
        assert_true(unknown == {"ok": False, "reason": "unknown structure"}, unknown)

        ruin = _add_test_structure(engine, condition=0.0)
        ruin["isRuin"] = True
        already_ruined = engine.god_preview(_structure_envelope(ruin["id"], 10))
        assert_true(already_ruined == {"ok": False, "reason": "structure is already ruined"}, already_ruined)

        s = _add_test_structure(engine, condition=50.0)
        too_big = engine.god_preview(_structure_envelope(s["id"], se.GOD_STRUCTURE_DELTA_MAX + 1))
        assert_true(too_big["ok"] is False and "magnitude exceeds" in too_big["reason"], too_big)
        zero_delta = engine.god_preview(_structure_envelope(s["id"], 0))
        assert_true(zero_delta == {"ok": False, "reason": "delta must be non-zero"}, zero_delta)
        print("  OK structure_condition rejects unknown/already-ruined structures and out-of-range delta")
    finally:
        se.GOD_MODE_ENABLED = old


def test_repair_structures_batch_un_ruins_preview_and_apply():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        ruined = _add_test_structure(engine, condition=0.0)
        ruined["isRuin"] = True
        ruined["ruinedSinceFrame"] = engine.frameTick
        worn = _add_test_structure(engine, condition=40.0)

        preview = engine.god_preview(_repair_structures_envelope(
            "ids", structure_ids=[ruined["id"], worn["id"]]))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["count"] == 2, outcome)
        by_id = {row["structureId"]: row for row in outcome["structures"]}
        assert_true(by_id[ruined["id"]]["unRuined"] is True, by_id[ruined["id"]])
        assert_true(by_id[ruined["id"]]["newCondition"] >= se.REPAIR_CONDITION_RESTORE,
                    by_id[ruined["id"]])
        assert_true(by_id[worn["id"]]["newCondition"] > worn["condition"], by_id[worn["id"]])

        applied = engine.god_apply(preview["previewId"], "req-repair-structures-batch")
        assert_true(applied["ok"], applied)
        assert_true(not ruined["isRuin"] and ruined["condition"] >= se.REPAIR_CONDITION_RESTORE, ruined)
        assert_true(worn["condition"] == by_id[worn["id"]]["newCondition"], (worn, applied))
        assert_true(applied["outcome"]["count"] == preview["previewOutcome"]["count"],
                    "applied count must equal previewed count")
        print("  OK repair_structures batch un-ruins ruined + restores worn; applied == previewed")
    finally:
        se.GOD_MODE_ENABLED = old


def test_repair_structures_rejects_ruins_when_un_ruin_false():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        ruined = _add_test_structure(engine, condition=0.0)
        ruined["isRuin"] = True
        rejected = engine.god_preview(_repair_structures_envelope(
            "ids", structure_ids=[ruined["id"]], un_ruin=False))
        assert_true(rejected == {"ok": False, "reason": "unRuin must be true to include ruined structures"},
                    rejected)
        print("  OK repair_structures with unRuin=false rejects ruined targets")
    finally:
        se.GOD_MODE_ENABLED = old


def test_clear_ruins_deletes_registry_preview_and_apply():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        owner = engine.agents[0]
        ruin_a = _add_test_structure(engine, condition=0.0, home_of=owner["name"])
        ruin_a["isRuin"] = True
        owner["homeStructureId"] = ruin_a["id"]
        ruin_b = _add_test_structure(engine, condition=0.0)
        ruin_b["isRuin"] = True
        kept = _add_test_structure(engine, condition=80.0)

        preview = engine.god_preview(_clear_ruins_envelope(structure_ids=[ruin_a["id"], ruin_b["id"]]))
        assert_true(preview["ok"] and preview["reversibilityClass"] == "irreversible", preview)
        assert_true(preview["previewOutcome"]["count"] == 2, preview["previewOutcome"])
        assert_true(set(preview["previewOutcome"]["structureIds"]) == {ruin_a["id"], ruin_b["id"]},
                    preview["previewOutcome"])

        applied = engine.god_apply(preview["previewId"], "req-clear-ruins")
        assert_true(applied["ok"], applied)
        remaining_ids = {s["id"] for s in engine.civilization["structures"]}
        assert_true(ruin_a["id"] not in remaining_ids and ruin_b["id"] not in remaining_ids,
                    remaining_ids)
        assert_true(kept["id"] in remaining_ids, remaining_ids)
        assert_true(owner["homeStructureId"] is None, owner)
        assert_true(applied["outcome"]["removed"] == preview["previewOutcome"]["count"],
                    (applied, preview))
        print("  OK clear_ruins preview/apply deletes selected ruins and clears homeStructureId")
    finally:
        se.GOD_MODE_ENABLED = old


def test_phase4_miracles_irreversible_and_refuse_cancellation():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        struct = _add_test_structure(engine, condition=50.0)

        envelopes_and_ids = []
        for label, env in (
            ("vitals", _vitals_envelope(agent["id"], health_delta=1)),
            ("grant", _grant_envelope("wood", 1)),
            ("structure", _structure_envelope(struct["id"], 1)),
        ):
            preview = engine.god_preview(env)
            assert_true(preview["ok"], (label, preview))
            assert_true(preview["reversibilityClass"] == "irreversible", (label, preview))
            applied = engine.god_apply(preview["previewId"], f"req-irrev-{label}")
            assert_true(applied["ok"], (label, applied))
            envelopes_and_ids.append((label, applied["interventionId"]))

        for label, intervention_id in envelopes_and_ids:
            cancelled = engine.god_cancel(intervention_id)
            assert_true(cancelled == {
                "ok": True, "cancelled": False, "reason": "nothing to cancel",
                "targetId": intervention_id,
            }, (label, cancelled))
            revoke_preview = engine.god_preview(_revoke_envelope(intervention_id))
            assert_true(revoke_preview["ok"], (label, revoke_preview))
            revoke_applied = engine.god_apply(revoke_preview["previewId"], f"req-revoke-{label}")
            assert_true(revoke_applied == {
                "ok": False, "reason": "guidance id not found or already inactive",
            }, (label, revoke_applied))
        print("  OK agent_vitals/grant_resource/structure_condition all report reversibilityClass "
              "'irreversible'; god_cancel and revoke_guidance both refuse to touch them")
    finally:
        se.GOD_MODE_ENABLED = old


def test_phase4_duplicate_request_and_expired_preview():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 50.0

        # Duplicate requestId applies once.
        preview = engine.god_preview(_vitals_envelope(agent["id"], health_delta=5))
        resp1 = engine.god_apply(preview["previewId"], "req-dup-vitals")
        assert_true(resp1["ok"], resp1)
        assert_true(agent["health"] == 55.0, agent["health"])
        resp2 = engine.god_apply(preview["previewId"], "req-dup-vitals")
        assert_true(resp2 == resp1, (resp1, resp2))
        assert_true(agent["health"] == 55.0, "duplicate requestId must not re-apply the miracle")

        # Expired preview rejected.
        preview2 = engine.god_preview(_vitals_envelope(agent["id"], health_delta=5))
        engine._god_preview_cache[preview2["previewId"]]["expiresAt"] = time.time() - 1
        expired = engine.god_apply(preview2["previewId"], "req-expired-vitals")
        assert_true(expired == {"ok": False, "reason": "preview missing or expired"}, expired)
        assert_true(agent["health"] == 55.0, "an expired preview must never apply")
        print("  OK Phase 4 miracles: duplicate requestId applies once; expired preview rejected")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 5 tests: storyteller events and timed lawgiver modifiers
# (docs/plan-sovereign-god-mode-v2.md "Timed lawgiver modifiers" +
# "Exact consumer sites and arithmetic" + "Storyteller events") ---

def _story_event_envelope(title="A Divine Tale", narration="Something shifts in the world.", **kwargs):
    payload = {"title": title, "narration": narration}
    payload.update(kwargs)
    return {"kind": "story_event", "payload": payload}


def _apply_story_event(engine, request_id, **kwargs):
    """Preview + apply a story_event in one call. Asserts the PREVIEW
    succeeded (a caller proving a REJECTION should call god_preview
    directly); returns the god_apply response either way."""
    preview = engine.god_preview(_story_event_envelope(**kwargs))
    assert_true(preview["ok"], preview)
    return engine.god_apply(preview["previewId"], request_id)


def test_divine_modifier_default_and_flag_gate():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 1.0,
                    "no active effect must return exactly the default")
        applied = _apply_story_event(engine, "req-modifier-default-1",
                                      modifiers={"gather_yield_multiplier": 0.25})
        assert_true(applied["ok"], applied)
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.25, "active effect not read back")
        se.GOD_MODE_ENABLED = False
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 1.0,
                    "the flag gate must win over an active effect still sitting in godState")
        se.GOD_MODE_ENABLED = True
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.25,
                    "re-enabling the flag must see the still-active effect again")
        print("  OK _divine_modifier: 1.0 default with nothing active; active value read back; "
              "flag-off always wins regardless of stored state")
    finally:
        se.GOD_MODE_ENABLED = old


def test_modifier_range_validation_every_key():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        for key, (lo, hi) in se.GOD_MODIFIER_RANGES.items():
            for value in (lo, (lo + hi) / 2.0, hi, 1.0):
                p = engine.god_preview(_story_event_envelope(modifiers={key: value}))
                assert_true(p["ok"], (key, value, p))
            below = engine.god_preview(_story_event_envelope(modifiers={key: lo - 0.01}))
            assert_true(below["ok"] is False and key in below["reason"], (key, below))
            above = engine.god_preview(_story_event_envelope(modifiers={key: hi + 0.01}))
            assert_true(above["ok"] is False and key in above["reason"], (key, above))
        unknown_key = engine.god_preview(_story_event_envelope(modifiers={"not_a_real_key": 1.0}))
        assert_true(unknown_key["ok"] is False and "unknown modifier key" in unknown_key["reason"], unknown_key)
        print(f"  OK all {len(se.GOD_MODIFIER_RANGES)} modifier keys: 0.0/fractional/1.0/max accepted, "
              f"out-of-range and unknown keys rejected")
    finally:
        se.GOD_MODE_ENABLED = old


def test_gather_zero_path_before_carry_cap_clamp():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["currentDistrict"] = "forest"
        agent["resources"]["wood"] = 0
        # gather_yield_multiplier's allowed range is 0.25..3.0 (never 0.0 --
        # only fish_yield_multiplier goes that low), but floor(1 * 0.25) is
        # still 0 for the base amount==1 a fresh agent gathers, so the
        # in-range minimum already exercises the zero-path.
        applied = _apply_story_event(engine, "req-gather-zero-1",
                                      modifiers={"gather_yield_multiplier": 0.25})
        assert_true(applied["ok"], applied)

        c = engine.civilization
        before_success = c["collectSuccesses"]
        before_ok = c.get("tool_gather_ok", 0)
        before_fail = c.get("tool_gather_fail", 0)
        summary = engine._perform_gather(agent, "wood")

        assert_true("found nothing" in summary, summary)
        assert_true(agent["resources"]["wood"] == 0,
                    "the zero-path resurrected an amount past the carry-cap clamp (max(1, min(...)))")
        assert_true(c["collectSuccesses"] == before_success, "zero-path incremented collectSuccesses")
        assert_true(c.get("tool_gather_ok", 0) == before_ok and c.get("tool_gather_fail", 0) == before_fail,
                    "zero-path touched the tool benchmark")
        assert_true(agent["lastGatherRejection"] is not None, "zero-path did not record lastGatherRejection")
        print("  OK a 0.0 gather_yield_multiplier returns BEFORE the carry-cap clamp: "
              "no resource added, collectSuccesses/tool benchmark untouched")
    finally:
        se.GOD_MODE_ENABLED = old


def test_fish_modifier_replaces_general_modifier():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        applied = _apply_story_event(engine, "req-fish-replace-1",
                                      modifiers={"gather_yield_multiplier": 0.25,
                                                "fish_yield_multiplier": 2.0})
        assert_true(applied["ok"], applied)

        agent["currentDistrict"] = "forest"
        agent["resources"]["wood"] = 0
        wood_summary = engine._perform_gather(agent, "wood")
        assert_true("found nothing" in wood_summary,
                    "gather_yield_multiplier=0.25 must still null a non-fish resource at base amount 1")

        agent["currentDistrict"] = "beach"
        agent["resources"]["fish"] = 0
        fish_summary = engine._perform_gather(agent, "fish")
        assert_true("found nothing" not in fish_summary,
                    "fish_yield_multiplier must REPLACE (not be nulled by) gather_yield_multiplier")
        assert_true(agent["resources"]["fish"] == 2,
                    f"expected fish_yield_multiplier=2.0 to double the base yield, got {agent['resources']['fish']}")
        print("  OK fish_yield_multiplier replaces (never multiplies with) gather_yield_multiplier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_collapsed_agent_recovers_under_zero_health_regen():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        applied = _apply_story_event(engine, "req-collapse-regen-1",
                                      modifiers={"health_regen_multiplier": 0.0},
                                      durationFrames=se.GOD_GUIDANCE_MAX_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        assert_true(engine._divine_modifier("health_regen_multiplier") == 0.0, "modifier not active")

        agent["incapacitated"] = True
        agent["health"] = 5.0
        agent["hunger"] = 60.0
        agent["deathFrame"] = None
        for _ in range(40):
            engine._update_survival(agent)
            if not agent["incapacitated"]:
                break
        assert_true(not agent["incapacitated"],
                    "a collapsed agent never recovered under a 0.0 health_regen_multiplier -- "
                    "COLLAPSE_REGEN must be excluded from divine scaling")
        assert_true(agent["health"] >= se.COLLAPSE_REVIVE_HEALTH, agent["health"])
        print("  OK a collapsed agent still recovers under a 0.0 health_regen_multiplier "
              "(COLLAPSE_REGEN is never scaled)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_survival_arithmetic_ordering_hunger_and_starvation():
    """Hunger drain and starvation damage multiply their base rate BEFORE
    the existing clamp, and 0.0 suppresses each independently without
    touching the other."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-survival-1",
                                      modifiers={"hunger_drain_multiplier": 0.0,
                                                "starvation_damage_multiplier": 0.0})
        assert_true(applied["ok"], applied)
        # Above EAT_THRESHOLD so the eat/share branches (which mutate
        # hunger for reasons unrelated to hunger_drain_multiplier) never
        # trigger -- isolates the drain-clamp arithmetic itself.
        agent = engine.agents[0]
        agent["incapacitated"] = False
        agent["hunger"] = 90.0
        agent["health"] = 50.0
        before_hunger = agent["hunger"]
        engine._update_survival(agent)
        assert_true(agent["hunger"] == before_hunger, "0.0 hunger_drain_multiplier still drained hunger")

        # Starvation damage: hunger already at 0 with no edibles anywhere to
        # eat/share, so the ONLY thing that can move health this tick is the
        # starvation branch -- isolates the starvation-damage arithmetic.
        for a in engine.agents:
            for rid in se.EDIBLE_RESOURCES:
                a["resources"][rid] = 0
        agent["incapacitated"] = False
        agent["hunger"] = 0.0
        agent["health"] = 50.0
        before_health = agent["health"]
        engine._update_survival(agent)
        assert_true(agent["health"] == before_health, "0.0 starvation_damage_multiplier still damaged health")
        print("  OK hunger_drain_multiplier=0.0 suppresses drain; starvation_damage_multiplier=0.0 suppresses damage")
    finally:
        se.GOD_MODE_ENABLED = old


def test_identity_path_all_modifiers_1_0_byte_identical():
    """An effective 1.0 for every key must execute the identical arithmetic
    (and therefore produce identical results) as the feature-off baseline --
    proven by running the SAME operations against two independently built
    engines and comparing the concrete outputs, the same pattern
    sid_parity_smoke.py uses for its engine-vs-control comparisons."""
    old = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = False
        baseline = make_engine()
        b_agent = baseline.agents[0]
        b_agent["currentDistrict"] = "forest"
        b_agent["resources"]["wood"] = 0
        b_gather_summary = baseline._perform_gather(b_agent, "wood")
        b_agent["incapacitated"] = False
        b_agent["hunger"], b_agent["health"] = 40.0, 70.0
        baseline._update_survival(b_agent)
        b_struct = _add_test_structure(baseline, condition=50.0)
        baseline._tick_structure_decay()
        cap = baseline._storage_capacity("food")
        baseline.civilization["stockpile"]["food"] = cap + 8
        baseline._tick_spoilage()
        b_spoilage = dict(baseline.civilization.get("lastSpoilage") or {})
        b_food = baseline.civilization["stockpile"]["food"]

        se.GOD_MODE_ENABLED = True
        divine = make_engine()
        # Insert the active event directly (bypassing preview/apply) so the
        # divine command's OWN narration/Chronicle side effects -- which are
        # expected and unrelated to the arithmetic contract under test --
        # never enter this comparison.
        divine._god_events_insert({
            "id": "divine-identity-test", "kind": "story_event",
            "title": "t", "narration": "n", "visibility": "public", "targetId": None,
            "createdFrame": divine.frameTick, "startFrame": divine.frameTick,
            "expiresFrame": divine.frameTick + 10_000_000, "status": "active",
            "modifiers": {k: 1.0 for k in se.GOD_MODIFIER_RANGES},
            "primitiveInterventionIds": [], "providenceId": None, "replaces": None,
        })
        d_agent = divine.agents[0]
        d_agent["currentDistrict"] = "forest"
        d_agent["resources"]["wood"] = 0
        d_gather_summary = divine._perform_gather(d_agent, "wood")
        assert_true(d_gather_summary == b_gather_summary, (d_gather_summary, b_gather_summary))
        assert_true(d_agent["resources"]["wood"] == b_agent["resources"]["wood"], "gather diverged at 1.0")

        d_agent["incapacitated"] = False
        d_agent["hunger"], d_agent["health"] = 40.0, 70.0
        divine._update_survival(d_agent)
        assert_true(d_agent["hunger"] == b_agent["hunger"] and d_agent["health"] == b_agent["health"],
                    "survival tick diverged at 1.0")

        d_struct = _add_test_structure(divine, condition=50.0)
        divine._tick_structure_decay()
        assert_true(d_struct["condition"] == b_struct["condition"], "structure decay diverged at 1.0")

        divine.civilization["stockpile"]["food"] = cap + 8
        divine._tick_spoilage()
        d_spoilage = dict(divine.civilization.get("lastSpoilage") or {})
        assert_true(divine.civilization["stockpile"]["food"] == b_food, "spoilage diverged at 1.0")
        assert_true(d_spoilage == b_spoilage, "spoilage record diverged at 1.0")
        print("  OK an effective 1.0 modifier for every key is byte-identical to the feature-off baseline "
              "(gather, survival, structure decay, spoilage)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_carry_cap_and_low_stock_boundaries():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["currentDistrict"] = "forest"
        applied = _apply_story_event(engine, "req-carry-cap-1",
                                      modifiers={"gather_yield_multiplier": 3.0})
        assert_true(applied["ok"], applied)
        agent["resources"]["wood"] = se.COLLECT_CAP - 1
        engine._perform_gather(agent, "wood")
        assert_true(agent["resources"]["wood"] == se.COLLECT_CAP,
                    "a maxed divine multiplier must still respect the carry cap")

        engine2 = make_engine()
        agent2 = engine2.agents[0]
        agent2["currentDistrict"] = "forest"
        agent2["resources"]["wood"] = 0
        engine2._ensure_district_stocks()
        engine2._set_district_stock("forest", "wood", 1)  # near-zero but > 0 -> ecology floors amount at 1
        applied2 = _apply_story_event(engine2, "req-carry-cap-2",
                                       modifiers={"gather_yield_multiplier": 0.4})
        assert_true(applied2["ok"], applied2)
        before = engine2.civilization["collectSuccesses"]
        summary2 = engine2._perform_gather(agent2, "wood")
        assert_true("found nothing" in summary2, summary2)
        assert_true(engine2.civilization["collectSuccesses"] == before,
                    "low-stock + fractional divine multiplier zero-path still incremented collectSuccesses")
        print("  OK carry-cap clamp still applies at max divine multiplier; "
              "low ecology stock + a fractional divine multiplier can still zero out")
    finally:
        se.GOD_MODE_ENABLED = old


def test_spoilage_divine_multiplier_bounds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        c = engine.civilization
        cap = engine._storage_capacity("food")
        c["stockpile"]["food"] = cap + 50
        applied = _apply_story_event(engine, "req-spoil-0", modifiers={"spoilage_multiplier": 0.0})
        assert_true(applied["ok"], applied)
        before = c["stockpile"]["food"]
        engine._tick_spoilage()
        assert_true(c["stockpile"]["food"] == before, "a 0.0 spoilage multiplier must spoil nothing")

        engine2 = make_engine()
        c2 = engine2.civilization
        cap2 = engine2._storage_capacity("food")
        # Zero out every agent's edible holdings first so "overflow" is
        # driven ENTIRELY by the stockpile figure below -- _tick_spoilage's
        # overflow = stockpile + agent-held - capacity, and a fresh roster
        # may already be holding some food.
        for a in engine2.agents:
            for rid in se.EDIBLE_RESOURCES:
                a["resources"][rid] = 0
        overflow = 5
        c2["stockpile"]["food"] = cap2 + overflow
        applied2 = _apply_story_event(engine2, "req-spoil-max", modifiers={"spoilage_multiplier": 3.0})
        assert_true(applied2["ok"], applied2)
        engine2._tick_spoilage()
        spoiled = (cap2 + overflow) - c2["stockpile"]["food"]
        assert_true(0 <= spoiled <= overflow, f"spoiled {spoiled} exceeded eligible overflow {overflow}")
        print("  OK spoilage_multiplier: 0.0 spoils nothing; a 3.0 maximum never exceeds eligible overflow")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_one_value_per_key_and_replace_effect_id():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied1 = _apply_story_event(engine, "req-occupy-1",
                                      title="Bountiful Harvest", narration="The fields swell.",
                                      modifiers={"gather_yield_multiplier": 2.0})
        assert_true(applied1["ok"], applied1)
        event_id = applied1["outcome"]["interventionId"]
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 2.0, "occupying event did not register")

        conflict_preview = engine.god_preview(_story_event_envelope(
            title="Black River", narration="A blight follows.",
            modifiers={"gather_yield_multiplier": 0.5}))
        assert_true(conflict_preview["ok"] is False
                    and "gather_yield_multiplier" in conflict_preview["reason"]
                    and "replaceEffectId" in conflict_preview["reason"], conflict_preview)

        replace_preview = engine.god_preview(_story_event_envelope(
            title="Black River", narration="A blight follows.",
            modifiers={"gather_yield_multiplier": 0.5}, replaceEffectId=event_id))
        assert_true(replace_preview["ok"], replace_preview)
        replace_applied = engine.god_apply(replace_preview["previewId"], "req-replace-1")
        assert_true(replace_applied["ok"], replace_applied)
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.5, "replacement did not take effect")

        active_holders = [e for e in engine.civilization["godState"]["activeEvents"]
                          if e.get("status") == "active"
                          and "gather_yield_multiplier" in (e.get("modifiers") or {})]
        assert_true(len(active_holders) == 1, active_holders)
        original = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(original["status"] == "replaced", original)

        bad_replace = engine.god_preview(_story_event_envelope(
            title="X", narration="Y", modifiers={"gather_yield_multiplier": 1.0},
            replaceEffectId="not-a-real-event-id"))
        assert_true(bad_replace == {"ok": False, "reason": "replaceEffectId does not name an active event"},
                    bad_replace)
        print("  OK one active value per key enforced; replaceEffectId accepted only against the active occupier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_atomicity_one_invalid_component_changes_nothing():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        before_health = agent["health"]
        before_events = [dict(e) for e in engine.civilization["godState"]["activeEvents"]]
        before_providence = engine.civilization["godState"]["providence"]
        before_intervened = engine.civilization["godState"]["intervened"]

        preview = engine.god_preview(_story_event_envelope(
            title="Mixed Fortune", narration="Fate is uneven.",
            primitives=[
                {"kind": "agent_vitals", "payload": {"targetId": agent["id"], "healthDelta": 5}},
                {"kind": "agent_vitals", "payload": {"targetId": 999999, "healthDelta": 5}},
            ]))
        assert_true(preview["ok"] is False, preview)
        assert_true(agent["health"] == before_health, "an invalid sibling primitive still mutated the world")
        assert_true(engine.civilization["godState"]["activeEvents"] == before_events,
                    "atomicity violated: activeEvents changed")
        assert_true(engine.civilization["godState"]["providence"] == before_providence,
                    "atomicity violated: providence changed")
        assert_true(engine.civilization["godState"]["intervened"] == before_intervened,
                    "atomicity violated: intervened flipped")
        print("  OK story_event with one invalid component rejects atomically -- nothing changes")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_full_composition_and_reversibility_class():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 50.0
        c = engine.civilization
        resource_id = "wood"
        struct = _add_test_structure(engine, condition=80.0)
        stock_before = c["stockpile"].get(resource_id, 0)

        preview = engine.god_preview(_story_event_envelope(
            title="Bountiful Harvest", narration="The gods smile on the fields.",
            modifiers={"gather_yield_multiplier": 2.0},
            primitives=[
                {"kind": "agent_vitals", "payload": {"targetId": agent["id"], "healthDelta": 5}},
                {"kind": "grant_resource", "payload": {"resourceId": resource_id, "amount": 10}},
                {"kind": "structure_condition", "payload": {"structureId": struct["id"], "delta": 5}},
            ],
            providence={"text": "A season of plenty is coming."}))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "consequential", preview)
        assert_true(preview["previewOutcome"]["modifiers"] == {"gather_yield_multiplier": 2.0},
                    preview["previewOutcome"])
        assert_true(len(preview["previewOutcome"]["primitives"]) == 3, preview["previewOutcome"])

        applied = engine.god_apply(preview["previewId"], "req-composition-1")
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        assert_true(agent["health"] == 55.0, agent["health"])
        assert_true(c["stockpile"].get(resource_id, 0) == stock_before + 10, c["stockpile"])
        assert_true(struct["condition"] == 85.0, struct)
        assert_true(c["godState"]["providence"]["text"] == "A season of plenty is coming.",
                    c["godState"]["providence"])
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 2.0, "modifier not active")
        event = next(e for e in c["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["providenceId"] == c["godState"]["providence"]["id"], event)
        assert_true(len(event["primitiveInterventionIds"]) == 3, event)
        print("  OK story_event applies modifiers+primitives+providence atomically; "
              "reversibilityClass 'consequential' once primitives are present")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_expiry_closes_exactly_once_with_linked_providence():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-expiry-1",
                                      title="A Brief Omen", narration="It will not last.",
                                      modifiers={"structure_decay_multiplier": 0.0},
                                      providence={"text": "Endure -- this too shall pass."},
                                      durationFrames=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        god = engine.civilization["godState"]
        event = god["activeEvents"][-1]
        expires_frame = event["expiresFrame"]
        providence_id = event["providenceId"]
        assert_true(god["providence"]["id"] == providence_id, god["providence"])

        engine.frameTick = expires_frame - 1
        assert_true(engine._divine_modifier("structure_decay_multiplier") == 0.0,
                    "modifier stopped influencing one frame before expiresFrame")
        engine._expire_divine_effects()
        assert_true(event["status"] == "active", "event closed one frame too early")

        engine.frameTick = expires_frame
        assert_true(engine._divine_modifier("structure_decay_multiplier") == 1.0,
                    "modifier still influenced at exactly expiresFrame, BEFORE cleanup ran -- "
                    "the predicate itself, not the cleanup sweep, must stop it")
        engine._expire_divine_effects()
        assert_true(event["status"] == "expired", event)
        assert_true(god["providence"] is None, "linked providence was not closed when its event expired")

        engine._expire_divine_effects()
        assert_true(event["status"] == "expired", "a second expiry sweep re-fired an already-closed event")
        print("  OK story_event expiry: modifier stops exactly at expiresFrame (before cleanup runs); "
              "event + its linked providence both close exactly once")
    finally:
        se.GOD_MODE_ENABLED = old


def test_god_cancel_events_and_refuses_miracles():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-cancel-1",
                                      title="A Long Winter", narration="Hunger presses in.",
                                      modifiers={"hunger_drain_multiplier": 2.0},
                                      providence={"text": "Ration carefully."})
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        assert_true(engine._divine_modifier("hunger_drain_multiplier") == 2.0, "modifier not active")

        cancelled = engine.god_cancel(event_id)
        assert_true(cancelled == {"ok": True, "cancelled": True,
                                  "targetId": event_id, "targetKind": "story_event"}, cancelled)
        assert_true(engine._divine_modifier("hunger_drain_multiplier") == 1.0,
                    "cancelled event still influenced the modifier")
        assert_true(engine.civilization["godState"]["providence"] is None,
                    "cancelling the event did not also close its linked providence")

        vitals_preview = engine.god_preview(_vitals_envelope(engine.agents[0]["id"], health_delta=1))
        vitals_applied = engine.god_apply(vitals_preview["previewId"], "req-cancel-miracle")
        assert_true(vitals_applied["ok"], vitals_applied)
        miracle_cancel = engine.god_cancel(vitals_applied["interventionId"])
        assert_true(miracle_cancel == {
            "ok": True, "cancelled": False, "reason": "nothing to cancel",
            "targetId": vitals_applied["interventionId"],
        }, miracle_cancel)
        print("  OK god_cancel closes an active story_event and its linked providence; "
              "refuses an irreversible miracle's interventionId")
    finally:
        se.GOD_MODE_ENABLED = old


def test_active_events_survive_save_restore_with_absolute_expiry():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_events_roundtrip.db")
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-restore-event-1",
                                      title="Merciful Rain", narration="The drought breaks.",
                                      modifiers={"starvation_damage_multiplier": 0.0},
                                      durationFrames=20000)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        events = restored.civilization["godState"]["activeEvents"]
        event = next(e for e in events if e["id"] == event_id)
        assert_true(event["status"] == "active", event)
        assert_true(event["expiresFrame"] == expires_frame,
                    "expiresFrame must round-trip as an ABSOLUTE frame, not be re-based on restore")
        assert_true(restored._divine_modifier("starvation_damage_multiplier") == 0.0,
                    "a restored active event does not influence _divine_modifier")

        restored.frameTick = expires_frame
        restored._expire_divine_effects(restore=True)
        assert_true(event["status"] == "restore-closed", event)
        print("  OK an active story_event survives save_state/restore_state with an absolute "
              "expiresFrame; restore-time expiry closes it once")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_preview_shows_divine_and_custom_rule_contributions_separately():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        effect = {
            "subject": {"resource": "wood"},
            "condition": {"action": "collect_resource"},
            "modifier": {"kind": "add", "value": 2},
        }
        engine.civilization["customRuleModifiers"]["wood_charter"] = effect

        preview = engine.god_preview(_story_event_envelope(
            title="Bountiful Harvest", narration="The gods amplify the harvest.",
            modifiers={"gather_yield_multiplier": 2.0}))
        assert_true(preview["ok"], preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["modifiers"] == {"gather_yield_multiplier": 2.0}, outcome)
        assert_true(any(e["ruleId"] == "wood_charter" and e["value"] == 2
                        for e in outcome["customRuleContext"]), outcome)

        # A modifier unrelated to gather/fish carries no custom-rule context key at all.
        preview2 = engine.god_preview(_story_event_envelope(
            title="A Long Winter", narration="Hunger presses in.",
            modifiers={"hunger_drain_multiplier": 2.0}))
        assert_true("customRuleContext" not in preview2["previewOutcome"], preview2["previewOutcome"])
        print("  OK preview discloses the divine multiplier and any active custom-rule contribution "
              "separately for gather/fish modifiers only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_private_visibility_and_target_validation():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]

        missing_target = engine.god_preview(_story_event_envelope(visibility="private"))
        assert_true(missing_target["ok"] is False and "targetId" in missing_target["reason"], missing_target)

        applied = _apply_story_event(engine, "req-private-story-1",
                                      title="A Secret Trial", narration="Only you will know.",
                                      visibility="private", targetId=target_id)
        assert_true(applied["ok"], applied)
        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("Only you will know" not in dumped, "a private story_event leaked into /state")
        assert_true(not any("Only you will know" in line for line in engine.activityLog),
                    "a private story_event leaked into public activity")
        print("  OK a private story_event requires a living targetId and never reaches public /state/activity")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 6 tests: divine weather override (docs/plan-sovereign-god-mode-
# v2.md Phase 6 -- event-authoritative clock, RNG-free forced entry, handoff
# to the natural cycle's successor of the OVERRIDDEN state, consequential
# reversibility class) ---

def _apply_weather_override(engine, request_id, state, districts=None, duration=None,
                            replace_effect_id=None):
    """Preview + apply a weather_override in one call. Asserts the PREVIEW
    succeeded (a caller proving a REJECTION should call god_preview
    directly); returns the god_apply response either way."""
    preview = engine.god_preview(_weather_envelope(state, districts=districts, duration=duration,
                                                    replace_effect_id=replace_effect_id))
    assert_true(preview["ok"], preview)
    return engine.god_apply(preview["previewId"], request_id)


def test_weather_override_enters_forced_state_without_rng():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        rng_state_before = random.getstate()
        applied = _apply_weather_override(engine, "req-weather-rng-1", "storm",
                                          districts=districts, duration=5000)
        assert_true(applied["ok"], applied)
        rng_state_after = random.getstate()
        assert_true(rng_state_before == rng_state_after,
                    "_weather_enter_forced must never draw RNG")

        w = engine.civilization["weather"]
        assert_true(w["state"] == "storm", w)
        assert_true(w["districts"] == districts, w)
        assert_true(w["exitFrame"] == applied["outcome"]["expiresFrame"], w)
        print("  OK weather_override enters the requested state with zero RNG draws; "
              "weather state/districts/exitFrame set from the operator input")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_exit_frame_matches_event_expiry():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-exitframe-1", "clearing",
                                          districts=districts, duration=7000)
        assert_true(applied["ok"], applied)
        event = engine.civilization["godState"]["activeEvents"][-1]
        assert_true(event["kind"] == "weather_override" and event["expiresFrame"] == engine.frameTick + 7000,
                    event)
        w = engine.civilization["weather"]
        assert_true(w["exitFrame"] == event["expiresFrame"], (w, event))
        print("  OK weather['exitFrame'] equals the applied event's expiresFrame exactly")
    finally:
        se.GOD_MODE_ENABLED = old


def test_natural_tick_weather_does_not_transition_while_override_holds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-hold-1", "storm",
                                          districts=districts, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        exit_frame = engine.civilization["weather"]["exitFrame"]
        engine.frameTick = exit_frame - 1
        for _ in range(5):
            engine._tick_weather()
            w = engine.civilization["weather"]
            assert_true(w["state"] == "storm" and w["exitFrame"] == exit_frame,
                        "natural _tick_weather transitioned while an override was still active")
        print("  OK the natural _tick_weather cycle never transitions while an override holds "
              "(right up to, but not past, its exitFrame)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_expiry_handoff_all_four_states():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_random_random = random.random
    try:
        districts = ["forest"]  # overwritten per engine below to a real district

        def _handoff_for(state, forced_random=None):
            engine = make_engine()
            real_districts = sorted(engine.civilization["districts"].keys())[:1]
            applied = _apply_weather_override(
                engine, f"req-handoff-{state}", state,
                districts=(None if state == "clear" else real_districts),
                duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
            assert_true(applied["ok"], applied)
            engine.frameTick = engine.civilization["weather"]["exitFrame"]
            random.seed(12345)
            if forced_random is not None:
                random.random = lambda: forced_random
            try:
                engine._expire_divine_effects()
            finally:
                random.random = old_random_random
            event = engine.civilization["godState"]["activeEvents"][-1]
            assert_true(event["status"] == "expired", event)
            return engine.civilization["weather"]["state"]

        assert_true(_handoff_for("clear") == "gathering",
                    "clear override must hand off to 'gathering' on expiry")
        assert_true(_handoff_for("gathering", forced_random=0.0) == "storm",
                    "gathering override (forced storm branch) must hand off to 'storm'")
        assert_true(_handoff_for("gathering", forced_random=0.99) == "clear",
                    "gathering override (forced clear branch) must hand off to 'clear'")
        assert_true(_handoff_for("storm") == "clearing",
                    "storm override must hand off to 'clearing' on expiry")
        assert_true(_handoff_for("clearing") == "clear",
                    "clearing override must hand off to 'clear' on expiry")
        print("  OK expiry hands off to the natural cycle's successor for all four overridden "
              "states, including both probability branches out of 'gathering'")
    finally:
        random.random = old_random_random
        se.GOD_MODE_ENABLED = old


def test_weather_override_cancel_runs_same_handoff():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        districts = sorted(engine.civilization["districts"].keys())[:1]
        applied = _apply_weather_override(engine, "req-weather-cancel-1", "storm",
                                          districts=districts, duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]

        random.seed(999)
        cancelled = engine.god_cancel(event_id)
        assert_true(cancelled == {"ok": True, "cancelled": True, "targetId": event_id,
                                  "targetKind": "weather_override"}, cancelled)
        event = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "cancelled", event)
        assert_true(engine.civilization["weather"]["state"] == "clearing",
                    "cancel must run the same storm -> clearing handoff as expiry")
        print("  OK god_cancel on an active weather override runs the same handoff and "
              "returns cleanly")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_rejections():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        real_district = sorted(engine.civilization["districts"].keys())[0]

        unknown_state = engine.god_preview(_weather_envelope("hurricane", districts=[real_district]))
        assert_true(unknown_state["ok"] is False and "state must be one of" in unknown_state["reason"],
                    unknown_state)

        unknown_district = engine.god_preview(_weather_envelope("storm", districts=["not-a-real-district"]))
        assert_true(unknown_district == {"ok": False, "reason": "unknown district id 'not-a-real-district'"},
                    unknown_district)

        old_weather_enabled = se.WEATHER_ENABLED
        se.WEATHER_ENABLED = False
        try:
            disabled = engine.god_preview(_weather_envelope("storm", districts=[real_district]))
            assert_true(disabled == {"ok": False, "reason": "weather_override requires WEATHER_ENABLED"},
                        disabled)
        finally:
            se.WEATHER_ENABLED = old_weather_enabled

        print("  OK weather_override rejects an unknown state name, an unknown district id, "
              "and WEATHER_ENABLED=False")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_reversibility_class_and_preview_disclosure():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        s1 = _add_test_structure(engine, condition=50.0, district_id=district)
        s2 = _add_test_structure(engine, condition=0.0, district_id=district)
        s2["isRuin"] = True  # must not be counted as at-risk

        preview = engine.god_preview(_weather_envelope("storm", districts=[district]))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "consequential", preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["districts"] == [district], outcome)
        assert_true(outcome["atRiskStructureCount"] == 1, outcome)
        assert_true(outcome["warning"] and "PERMANENTLY damage" in outcome["warning"], outcome)
        print("  OK weather_override reversibilityClass is 'consequential'; preview discloses the "
              "affected districts and the count of non-ruined structures at risk in them")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_replace_requires_replace_effect_id():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied1 = _apply_weather_override(engine, "req-weather-replace-1", "storm",
                                           districts=[district], duration=5000)
        assert_true(applied1["ok"], applied1)
        event_id = applied1["outcome"]["interventionId"]

        no_replace = engine.god_preview(_weather_envelope("clear"))
        assert_true(no_replace["ok"] is False and "already active" in no_replace["reason"], no_replace)

        replace_preview = engine.god_preview(_weather_envelope("clear", replace_effect_id=event_id))
        assert_true(replace_preview["ok"], replace_preview)
        replace_applied = engine.god_apply(replace_preview["previewId"], "req-weather-replace-2")
        assert_true(replace_applied["ok"], replace_applied)

        original = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(original["status"] == "replaced", original)
        assert_true(engine.civilization["weather"]["state"] == "clear", engine.civilization["weather"])
        print("  OK a second override without replaceEffectId is rejected while one is active; "
              "with a matching replaceEffectId it replaces cleanly, closing the previous as 'replaced'")
    finally:
        se.GOD_MODE_ENABLED = old


def test_weather_override_survives_save_restore():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_weather_roundtrip.db")
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied = _apply_weather_override(engine, "req-weather-restore-1", "storm",
                                          districts=[district], duration=20000)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        event = next(e for e in restored.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "active", event)
        assert_true(event["expiresFrame"] == expires_frame,
                    "expiresFrame must round-trip as an ABSOLUTE frame")
        assert_true(restored.civilization["weather"]["state"] == "storm", restored.civilization["weather"])
        print("  OK an active weather override survives save_state/restore_state with its "
              "absolute expiresFrame intact; still in activeEvents and still active")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_weather_override_restore_time_expiry_closes_and_hands_off_once():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_weather_restore_expiry.db")
    try:
        engine = make_engine()
        district = sorted(engine.civilization["districts"].keys())[0]
        applied = _apply_weather_override(engine, "req-weather-restore-expiry-1", "storm",
                                          districts=[district],
                                          duration=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]
        # Advance PAST expiry without running the tick-based sweep -- simulates
        # a save captured after the override's clock ran out but before
        # _expire_divine_effects next ran.
        engine.frameTick = expires_frame + 500

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        divine_lines_before = len(restored.civilization.get("chronicle") or [])

        random.seed(42)
        restored._expire_divine_effects(restore=True)
        event = next(e for e in restored.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["status"] == "restore-closed", event)
        assert_true(restored.civilization["weather"]["state"] == "clearing",
                    "restore-time expiry must still hand off (storm -> clearing)")

        # A second restore-time sweep against the already-closed event must
        # be a no-op -- the handoff (and any narration it would produce)
        # fires exactly once.
        state_after_first = restored.civilization["weather"]["state"]
        restored._expire_divine_effects(restore=True)
        assert_true(restored.civilization["weather"]["state"] == state_after_first,
                    "a second restore-time expiry sweep re-fired the handoff")
        assert_true(event["status"] == "restore-closed", event)
        print("  OK an override past-expiry at restore time closes exactly once and hands off "
              "exactly once (a second sweep is a verified no-op)")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_weather_flag_off_natural_cycle_unaffected():
    """The god-mode machinery participates in weather ONLY through an
    explicitly-applied weather_override; with GOD_MODE_ENABLED False (or
    simply never invoked), the natural _tick_weather/_weather_enter cycle
    must behave identically -- proven here by seeding the RNG and comparing
    a multi-tick trajectory between a GOD_MODE_ENABLED=False engine and a
    GOD_MODE_ENABLED=True engine on which no override is ever applied."""
    old = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = False
        baseline = make_engine()
        random.seed(2026)
        baseline_trajectory = []
        for _ in range(12):
            baseline.frameTick += se.GOODS_TICK_FRAMES
            baseline._tick_weather()
            w = baseline.civilization["weather"]
            baseline_trajectory.append((w["state"], tuple(w["districts"]), w["exitFrame"]))

        se.GOD_MODE_ENABLED = True
        divine = make_engine()
        random.seed(2026)
        divine_trajectory = []
        for _ in range(12):
            divine.frameTick += se.GOODS_TICK_FRAMES
            divine._tick_weather()
            w = divine.civilization["weather"]
            divine_trajectory.append((w["state"], tuple(w["districts"]), w["exitFrame"]))

        assert_true(baseline_trajectory == divine_trajectory,
                    "the natural weather cycle diverged with GOD_MODE_ENABLED on but unused")
        print(f"  OK a {len(baseline_trajectory)}-tick natural weather trajectory (seeded RNG) is "
              f"byte-identical whether GOD_MODE_ENABLED is off or on-but-unused")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Optional Phase 8 smoke -- free-prose story compiler ------------------
# No Ollama call ever happens here: every test monkeypatches
# engine.d["lm_complete"] directly (the same dict object SimEngine.__init__
# stores as self.d -- see make_engine's deps above), exactly as the task
# instructs. GOD_COMPILER_ENABLED is patched per-test on the `se` module the
# same way GOD_MODE_ENABLED already is elsewhere in this file.

def _valid_compiler_json():
    return json.dumps({
        "kind": "story_event",
        "payload": {
            "title": "The Black River",
            "narration": "The river runs dark and the fish flee the shallows.",
            "visibility": "public",
            "durationFrames": 8100,
            "modifiers": {"fish_yield_multiplier": 0.1},
            "primitives": [],
        },
    })


def _compiler_engine():
    """A GOD_MODE_ENABLED + GOD_COMPILER_ENABLED engine for compiler tests.
    Caller must restore se.GOD_MODE_ENABLED/se.GOD_COMPILER_ENABLED (both are
    already True at module import via TEST_TOKEN/os.environ setup for
    GOD_MODE_ENABLED, but GOD_COMPILER_ENABLED defaults False at import since
    SIM_GOD_COMPILER was never set -- each test flips it on explicitly)."""
    return make_engine()


def test_compiler_dual_gate_rejects_when_disabled():
    old_compiler = se.GOD_COMPILER_ENABLED
    old_mode = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = False
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("disabled" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "a disabled compiler must never populate the preview cache")

        # GOD_MODE_ENABLED off but GOD_COMPILER_ENABLED on: still rejected --
        # this is a genuine DUAL gate, neither flag alone is sufficient.
        se.GOD_MODE_ENABLED = False
        se.GOD_COMPILER_ENABLED = True
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        print("  OK compiler dual-gate: GOD_COMPILER_ENABLED=False rejects cleanly; "
              "GOD_MODE_ENABLED=False rejects cleanly even with the compiler flag on")
    finally:
        se.GOD_COMPILER_ENABLED = old_compiler
        se.GOD_MODE_ENABLED = old_mode


def test_compiler_rate_limit():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        first = engine.god_compile_prose("The river runs dark.")
        assert_true(first["compileOk"] is True, first)
        second = engine.god_compile_prose("A second decree, immediately after.")
        assert_true(second["compileOk"] is False and "rate limited" in second["reason"], second)
        assert_true(engine._god_compiler_state["compileCount"] == 1,
                    "a rate-limited call must not bump compileCount")
        print("  OK a second compile within GOD_COMPILER_MIN_INTERVAL_SEC is rejected as rate-limited")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_session_cap():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    old_interval = se.GOD_COMPILER_MIN_INTERVAL_SEC
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        se.GOD_COMPILER_MIN_INTERVAL_SEC = 0.0  # isolate the session cap from the interval check
        engine = _compiler_engine()
        # Every call fails to parse -- proves the cap counts FAILURES too.
        engine.d["lm_complete"] = lambda *a, **k: "not json at all"
        for i in range(se.GOD_COMPILER_SESSION_CAP):
            resp = engine.god_compile_prose(f"decree {i}")
            assert_true(resp["compileOk"] is False, resp)
        assert_true(engine._god_compiler_state["compileCount"] == se.GOD_COMPILER_SESSION_CAP,
                    engine._god_compiler_state)
        over_budget = engine.god_compile_prose("one more, over budget")
        assert_true(over_budget["compileOk"] is False and "session cap" in over_budget["reason"], over_budget)
        assert_true(engine._god_compiler_state["compileCount"] == se.GOD_COMPILER_SESSION_CAP,
                    "an over-budget rejection must NOT bump compileCount further")
        print(f"  OK compileCount increments on every failed compile too; "
              f"at {se.GOD_COMPILER_SESSION_CAP} further compiles reject cleanly")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode
        se.GOD_COMPILER_MIN_INTERVAL_SEC = old_interval


def test_compiler_successful_compile_produces_applyable_preview():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark for three days and the fish flee.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(resp["previewId"] in engine._god_preview_cache, "compile must populate _god_preview_cache")
        assert_true(resp["normalizedCommand"]["kind"] == "story_event", resp)
        assert_true(resp["normalizedCommand"]["payload"]["modifiers"]["fish_yield_multiplier"] == 0.1, resp)
        # The compiler NEVER applies -- intervened must still be False.
        assert_true(engine.civilization["godState"]["intervened"] is False,
                    "god_compile_prose must never mutate world state")
        applied = engine.god_apply(resp["previewId"], "compiler-smoke-apply-1")
        assert_true(applied["ok"], applied)
        assert_true(engine.civilization["godState"]["intervened"] is True,
                    "the OPERATOR's own god_apply call is what mutates state, never the compiler")
        print("  OK a successful compile produces a real _god_preview_cache entry, applyable via normal god_apply, "
              "and never mutates state itself")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_model_shape_mismatch_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: json.dumps({"kind": "not_a_story"})
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("story_event" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "a shape-mismatched draft must never reach the preview cache")
        print("  OK a model response with the wrong 'kind' is rejected BEFORE reaching the preview cache")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_unknown_modifier_key_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        bogus = json.dumps({
            "kind": "story_event",
            "payload": {
                "title": "Bogus Decree", "narration": "Something happens.",
                "visibility": "public", "modifiers": {"bogus_multiplier": 1.0},
            },
        })
        engine.d["lm_complete"] = lambda *a, **k: bogus
        resp = engine.god_compile_prose("An impossible decree.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("bogus_multiplier" in resp["reason"], resp)
        assert_true(engine._god_preview_cache == {}, "an unknown-key draft must never reach the preview cache")
        print("  OK an unknown modifier key from the model is rejected, naming the offending field, "
              "never entering the preview cache")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_non_json_response_rejected():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: "Certainly! Here is a story about a river: " + "x" * 300
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true("JSON" in resp["reason"], resp)
        assert_true(len(resp["reason"]) < 300, "the raw output must be TRUNCATED in the rejection reason")
        print("  OK a non-JSON model response is rejected with a truncated raw-output reason")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_timeout_handled_cleanly():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()

        def _raise_timeout(*a, **k):
            raise TimeoutError("simulated Ollama timeout")
        engine.d["lm_complete"] = _raise_timeout
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is False, resp)
        assert_true(engine._god_compiler_state["compileCount"] == 1,
                    "a timeout must still bump compileCount (docs/plan: regardless of success)")
        print("  OK a simulated lm_complete timeout returns a clean rejection, not an unhandled exception, "
              "and still counts against the session cap")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


def test_compiler_state_not_persisted_across_restore():
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_compiler_roundtrip.db")
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(engine._god_compiler_state["compileCount"] == 1, engine._god_compiler_state)
        assert_true(engine._god_compiler_state["lastCompileWallTime"] > 0, engine._god_compiler_state)

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed against the just-written db")
        assert_true(restored._god_compiler_state == {"lastCompileWallTime": 0.0, "compileCount": 0},
                    restored._god_compiler_state)
        print("  OK compileCount/lastCompileWallTime do NOT survive save/restore -- in-memory only")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode
        se.DB_PATH = old_db_path


def test_compiler_token_never_in_prompt_or_state():
    """The compiler method has no parameter for the token and never reads
    os.environ -- proven here by confirming TEST_TOKEN never appears in the
    prompt text lm_complete receives, nor in any compiler response field."""
    old_compiler, old_mode = se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = True
        se.GOD_COMPILER_ENABLED = True
        engine = _compiler_engine()
        seen_prompts = []

        def _capture(system_prompt, user_prompt, *a, **k):
            seen_prompts.append(system_prompt)
            seen_prompts.append(user_prompt)
            return _valid_compiler_json()
        engine.d["lm_complete"] = _capture
        resp = engine.god_compile_prose("The river runs dark.")
        assert_true(resp["compileOk"] is True, resp)
        assert_true(all(TEST_TOKEN not in p for p in seen_prompts),
                    "the God token must never reach the compiler prompt")
        assert_true(TEST_TOKEN not in json.dumps(resp), "the God token must never appear in the compile response")
        print("  OK the compiler prompt and response never contain the God token "
              "(god_compile_prose has no parameter for it)")
    finally:
        se.GOD_COMPILER_ENABLED, se.GOD_MODE_ENABLED = old_compiler, old_mode


# --- HTTP-layer tests (real server.py app + engine, imported once with the
# module-level env vars above; never persisted to the real state.db) ---

def run_http_tests():
    import server  # noqa: E402  (heavy import: real engine, real state.db READ only)

    assert_true(server.GOD_TOKEN == TEST_TOKEN, "server did not pick up SIM_GOD_TOKEN at import")
    assert_true(server.GOD_ROUTES_ACTIVE is True,
                "server did not activate god routes with flag+token+auth all set")

    client = server.app.test_client()
    headers_ok = {"X-God-Token": TEST_TOKEN}
    headers_wrong = {"X-God-Token": "not-the-token"}

    resp = client.get("/control/god/capabilities", headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    body = resp.get_json()
    assert_true(body["ok"] and body["godModeEnabled"] and body["tokenConfigured"], body)
    assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into a response body")
    print("  OK GET /control/god/capabilities with a valid token")

    for label, headers in (("no header", {}), ("wrong token", headers_wrong)):
        resp = client.get("/control/god/capabilities", headers=headers)
        assert_true(resp.status_code == 401, (label, resp.status_code))
        assert_true(resp.get_json() == {"error": "unauthorized"}, (label, resp.get_json()))
    print("  OK missing/wrong token -> uniform 401 {'error': 'unauthorized'} (auth required)")

    # When GOD_AUTH_REQUIRED is False, tokenless requests succeed.
    old_auth = se.GOD_AUTH_REQUIRED
    se.GOD_AUTH_REQUIRED = False
    try:
        resp = client.get("/control/god/capabilities")
        assert_true(resp.status_code == 200 and resp.get_json()["ok"], resp.get_json())
        print("  OK GOD_AUTH_REQUIRED off -> tokenless GET /control/god/capabilities returns 200")
    finally:
        se.GOD_AUTH_REQUIRED = old_auth

    # Simulate "flag on, auth required, token unset" the same way a real
    # misconfigured startup would compute it -- monkeypatch the already-imported
    # module's plain globals (server.py reads GOD_TOKEN/GOD_ROUTES_ACTIVE once at
    # import, exactly like a real restart would) rather than re-importing.
    old_token, old_active = server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE
    server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE = "", False
    try:
        resp = client.get("/control/god/capabilities", headers=headers_ok)
        assert_true(resp.status_code == 401 and resp.get_json() == {"error": "unauthorized"},
                    (resp.status_code, resp.get_json()))
        print("  OK flag-on-with-auth-required-and-no-token leaves routes disabled "
              "even with a well-formed header")
    finally:
        server.GOD_TOKEN, server.GOD_ROUTES_ACTIVE = old_token, old_active

    # Request-body size limit.
    oversized = b"{\"kind\": \"proclamation\", \"payload\": {\"text\": \"" + b"a" * 9000 + b"\"}}"
    resp = client.post("/control/god/preview", data=oversized,
                       content_type="application/json", headers=headers_ok)
    assert_true(resp.status_code == 413, resp.status_code)
    print("  OK oversized request body -> 413, never reaches JSON parsing")

    # Full preview -> apply -> sight -> cancel flow through the real routes,
    # against the real (but never-persisted-by-this-script) server.engine.
    before_intervened = server.engine.civilization["godState"]["intervened"]
    resp = client.post("/control/god/preview", json=_proclamation_envelope(
        "The Divine Console smoke test speaks."), headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)

    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-1"},
                       headers=headers_ok)
    assert_true(resp.status_code == 200, resp.status_code)
    apply_body = resp.get_json()
    assert_true(apply_body["ok"], apply_body)
    assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into an apply response")
    assert_true(server.engine.civilization["godState"]["intervened"] is True,
                "HTTP apply did not mutate the real in-process engine")

    resp = client.get("/control/god/sight", headers=headers_ok)
    assert_true(resp.status_code == 200 and resp.get_json()["ok"], resp.get_json())

    resp = client.post("/control/god/cancel", json={"targetId": "none"}, headers=headers_ok)
    assert_true(resp.status_code == 200 and resp.get_json()["cancelled"] is False, resp.get_json())
    print(f"  OK full HTTP preview->apply->sight->cancel flow "
          f"(intervened {before_intervened} -> True)")

    # /state must carry config.flags.GOD_MODE_ENABLED and a 'god' key, and
    # the token must never appear in the raw snapshot dump.
    snapshot_dump = json.dumps(server.engine.snapshot())
    assert_true(TEST_TOKEN not in snapshot_dump, "token leaked into /state")
    snap = server.engine.snapshot()
    assert_true(snap["config"]["flags"]["GOD_MODE_ENABLED"] is True, snap["config"]["flags"])
    assert_true("god" in snap and "intervened" in snap["god"], snap.get("god"))
    print("  OK /state exposes GOD_MODE_ENABLED + a bounded 'god' key, never the token")

    # divine.jsonl got a record for the applied proclamation, and never
    # contains the token.
    with open(server.session_logger.divine_path, encoding="utf-8") as fh:
        lines = [json.loads(ln) for ln in fh if ln.strip()]
    applied_records = [r for r in lines if r.get("status") == "applied"]
    assert_true(applied_records, "no 'applied' record written to divine.jsonl")
    divine_text = open(server.session_logger.divine_path, encoding="utf-8").read()
    assert_true(TEST_TOKEN not in divine_text, "token leaked into divine.jsonl")
    assert_true("http-smoke-1" not in divine_text, "raw requestId was logged instead of a hash")
    print(f"  OK divine.jsonl recorded {len(applied_records)} applied record(s), "
          f"no token/raw-requestId leakage")

    # Phase 3 over HTTP: providence appears in /state's god key; a private
    # omen never does, through the real routes (not just the engine directly).
    resp = client.post("/control/god/preview", json=_providence_envelope(
        "The HTTP smoke providence speaks.", duration=1000), headers=headers_ok)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)
    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-providence"},
                       headers=headers_ok)
    assert_true(resp.get_json()["ok"], resp.get_json())
    snap = server.engine.snapshot()
    assert_true(snap["god"]["providence"]["text"] == "The HTTP smoke providence speaks.", snap["god"])

    real_target_id = server.engine.agents[0]["id"]
    resp = client.post("/control/god/preview", json=_omen_envelope(
        real_target_id, "The HTTP smoke omen -- must stay private.", duration=1000), headers=headers_ok)
    preview_body = resp.get_json()
    assert_true(preview_body["ok"], preview_body)
    resp = client.post("/control/god/apply",
                       json={"previewId": preview_body["previewId"], "requestId": "http-smoke-omen"},
                       headers=headers_ok)
    assert_true(resp.get_json()["ok"], resp.get_json())
    state_dump = json.dumps(server.engine.snapshot())
    assert_true("must stay private" not in state_dump, "private omen leaked into /state over HTTP")
    resp = client.get("/control/god/sight", headers=headers_ok)
    assert_true("must stay private" in resp.get_data(as_text=True),
                "sight should expose the omen (authenticated route)")
    # Revoke it so this HTTP smoke run leaves no lingering guidance behind in
    # the real in-process engine for whatever runs next.
    revoke_id = None
    for a in server.engine.god_sight()["agents"]:
        if a["id"] == real_target_id and a.get("omen"):
            revoke_id = server.engine.civilization["godState"]["privateOmens"][str(real_target_id)]["id"]
    if revoke_id:
        resp = client.post("/control/god/preview", json=_revoke_envelope(revoke_id), headers=headers_ok)
        preview_body = resp.get_json()
        client.post("/control/god/apply",
                   json={"previewId": preview_body["previewId"], "requestId": "http-smoke-revoke-cleanup"},
                   headers=headers_ok)
    print("  OK providence appears in /state's 'god' key over HTTP; "
          "a private omen never does, only through /control/god/sight")

    # Optional Phase 8 over HTTP: capabilities.compiler.enabled reflects the
    # dual gate; the /control/god/compile route works end to end when both
    # flags are on, and compiler.jsonl never contains the token.
    resp = client.get("/control/god/capabilities", headers=headers_ok)
    caps = resp.get_json()
    assert_true("compiler" in caps and caps["compiler"]["enabled"] is False,
                "capabilities.compiler.enabled must be False while GOD_COMPILER_ENABLED is off")

    old_compiler_flag = se.GOD_COMPILER_ENABLED
    se.GOD_COMPILER_ENABLED = True
    old_lm_complete = server.engine.d.get("lm_complete")
    try:
        resp = client.get("/control/god/capabilities", headers=headers_ok)
        caps = resp.get_json()
        assert_true(caps["compiler"]["enabled"] is True,
                    "capabilities.compiler.enabled must be True once GOD_COMPILER_ENABLED is on")

        server.engine.d["lm_complete"] = lambda *a, **k: _valid_compiler_json()
        resp = client.post("/control/god/compile", json={"prose": "The river runs dark for three days."},
                           headers=headers_ok)
        assert_true(resp.status_code == 200, resp.status_code)
        compile_body = resp.get_json()
        assert_true(compile_body.get("compileOk") is True, compile_body)
        assert_true(TEST_TOKEN not in resp.get_data(as_text=True), "token leaked into a compile response")
        print("  OK GET /control/god/capabilities reflects the compiler dual gate; "
              "POST /control/god/compile produces a real previewable draft")

        # Unauthorized compile attempts get the same uniform 401.
        resp = client.post("/control/god/compile", json={"prose": "x"}, headers={})
        assert_true(resp.status_code == 401 and resp.get_json() == {"error": "unauthorized"}, resp.get_json())
        print("  OK POST /control/god/compile without a valid token -> uniform 401")

        with open(server.session_logger.compiler_path, encoding="utf-8") as fh:
            compiler_lines = [json.loads(ln) for ln in fh if ln.strip()]
        assert_true(compiler_lines, "no record written to compiler.jsonl")
        compiler_text = open(server.session_logger.compiler_path, encoding="utf-8").read()
        assert_true(TEST_TOKEN not in compiler_text, "token leaked into compiler.jsonl")
        draft_records = [r for r in compiler_lines if r.get("status") == "draft"]
        assert_true(draft_records, "no 'draft' record written to compiler.jsonl")
        print(f"  OK compiler.jsonl recorded {len(compiler_lines)} record(s) "
              f"({len(draft_records)} draft), no token leakage")
    finally:
        se.GOD_COMPILER_ENABLED = old_compiler_flag
        if old_lm_complete is not None:
            server.engine.d["lm_complete"] = old_lm_complete

    # /control/reset password gate (never calls the real engine.reset()).
    assert_true(server.RESET_PASSWORD == "reset",
                "expected default RESET_PASSWORD when SIM_RESET_PASSWORD unset")
    tick_before = server.engine.frameTick
    reset_calls = []
    orig_reset = server.engine.reset

    def _track_reset(roster_size=None):
        reset_calls.append(roster_size)
        return None

    server.engine.reset = _track_reset
    try:
        resp = client.post("/control/reset", json={"password": "wrong-password"})
        assert_true(resp.status_code == 401, resp.status_code)
        assert_true(resp.get_json() == {"ok": False, "error": "unauthorized"}, resp.get_json())
        assert_true(not reset_calls, "wrong password must not call engine.reset()")
        assert_true(server.engine.frameTick == tick_before, "world unchanged on wrong password")

        resp = client.post("/control/reset", json={})
        assert_true(resp.status_code == 401 and not reset_calls,
                    (resp.status_code, reset_calls))

        resp = client.post("/control/reset",
                           json={"password": server.RESET_PASSWORD, "agents": 10})
        assert_true(resp.status_code == 200, resp.status_code)
        body = resp.get_json()
        assert_true(body.get("ok") is True, body)
        assert_true(reset_calls == [10], reset_calls)
    finally:
        server.engine.reset = orig_reset
    print("  OK POST /control/reset password gate (wrong/missing -> 401, correct -> reset)")


def _architect_zone_envelope(zone_kind, district_id, cells, **extra):
    payload = {"zoneKind": zone_kind, "districtId": district_id, "cells": cells,
               "durationFrames": 5000}
    payload.update(extra)
    return {"kind": "architect_zone", "payload": payload}


def test_architect_zone_door_blocks_without_key_allows_with_key():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        district_id = "farm_north"
        bounds = engine.civilization["districts"][district_id]["bounds"]
        start_x = bounds["x1"] + 19
        start_y = bounds["y1"] + 20
        target_x = bounds["x1"] + 60
        agent["x"] = start_x
        agent["y"] = start_y
        agent["targetX"] = target_x
        agent["targetY"] = start_y
        agent["currentDistrict"] = district_id
        agent["godKeys"] = set()

        preview = engine.god_preview(_architect_zone_envelope(
            "door", district_id, ["1,0"], keyId="matrix-red-key"))
        applied = engine.god_apply(preview["previewId"], "req-arch-door")
        assert_true(applied.get("ok"), applied)

        with engine.lock:
            engine._move_agent(agent, scale=5.0)
        blocked_x = agent["x"]
        assert_true(blocked_x < bounds["x1"] + 40, agent)

        grant_preview = engine.god_preview(_architect_zone_envelope(
            "door", district_id, ["2,0"], keyId="matrix-red-key",
            grantKeyAgentIds=[agent["id"]]))
        engine.god_apply(grant_preview["previewId"], "req-arch-door-grant")
        assert_true(engine._agent_has_god_key(agent, "matrix-red-key"), agent)

        agent["targetX"] = target_x
        with engine.lock:
            engine._move_agent(agent, scale=5.0)
        assert_true(agent["x"] > blocked_x, agent)
        print("  OK architect door blocks move without key and allows with godKeys tag")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_limbo_freezes_think_and_release_restores():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        prior_x, prior_y = agent["x"], agent["y"]
        prior_tx, prior_ty = agent["targetX"], agent["targetY"]

        preview = engine.god_preview(_architect_zone_envelope(
            "limbo", "ocean", ["0,0"], holdAgentIds=[agent["id"]]))
        applied = engine.god_apply(preview["previewId"], "req-arch-limbo")
        assert_true(applied.get("ok"), applied)
        zone_id = applied["interventionId"]

        assert_true(agent.get("divineHold"), agent)
        assert_true(agent["x"] == se.GOD_LIMBO_STATION[0], agent)
        assert_true(agent["y"] == se.GOD_LIMBO_STATION[1], agent)
        assert_true(not engine._schedule_think(agent), agent)

        release_preview = engine.god_preview({
            "kind": "architect_release_hold",
            "payload": {"zoneId": zone_id, "agentIds": [agent["id"]]},
        })
        released = engine.god_apply(release_preview["previewId"], "req-arch-release")
        assert_true(released.get("ok"), released)
        assert_true(not agent.get("divineHold"), agent)
        assert_true(abs(agent["x"] - prior_x) < 0.01 and abs(agent["y"] - prior_y) < 0.01, agent)
        assert_true(agent["targetX"] == prior_tx and agent["targetY"] == prior_ty, agent)
        print("  OK architect limbo sets divineHold, blocks think, release restores pose")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_paint_cancel_reverts():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        district_id = "farm_north"
        district = engine.civilization["districts"][district_id]
        engine._ensure_district_terrain(district)
        key = engine._tile_key(0, 0)
        district["terrain"][key] = "soil"

        preview = engine.god_preview(_architect_zone_envelope(
            "paint", district_id, ["0,0"], paintTerrain="rock", reversible=True))
        applied = engine.god_apply(preview["previewId"], "req-arch-paint")
        assert_true(applied.get("ok"), applied)
        zone_id = applied["interventionId"]
        assert_true(district["terrain"][key] == "rock", district["terrain"])

        cancel_preview = engine.god_preview({
            "kind": "architect_zone_cancel",
            "payload": {"zoneId": zone_id},
        })
        engine.god_apply(cancel_preview["previewId"], "req-arch-paint-cancel")
        assert_true(district["terrain"][key] == "soil", district["terrain"])
        print("  OK architect paint cancel reverts reversible terrain")
    finally:
        se.GOD_MODE_ENABLED = old


def test_architect_zone_privacy_and_sight_summary():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        preview = engine.god_preview(_architect_zone_envelope(
            "door", "farm_north", ["3,0"], keyId="secret-door-key",
            grantKeyAgentIds=[agent["id"]]))
        applied = engine.god_apply(preview["previewId"], "req-arch-privacy")
        assert_true(applied.get("ok"), applied)

        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("architectZones" not in dumped, "architectZones leaked into /state")
        assert_true(all(r.get("kind") != "architect_zone"
                        for r in snap["god"]["recentPublicInterventions"]),
                    "door architect_zone leaked into recentPublicInterventions")
        assert_true("secret-door-key" not in dumped, "key id leaked into /state")

        sight = engine.god_sight()
        assert_true(isinstance(sight.get("architectZones"), list), sight)
        assert_true(any(z.get("kind") == "door" for z in sight["architectZones"]), sight)
        assert_true(all("keyId" not in z for z in sight["architectZones"]), sight)
        print("  OK architect zones stay off /state; Sight has summaries without secrets")
    finally:
        se.GOD_MODE_ENABLED = old


def _checkpoint_create_envelope(label, replace_oldest=False):
    payload = {"label": label}
    if replace_oldest:
        payload["replaceOldest"] = True
    return {"kind": "checkpoint_create", "payload": payload}


def _checkpoint_restore_envelope(checkpoint_id):
    return {"kind": "checkpoint_restore", "payload": {"checkpointId": checkpoint_id}}


def test_checkpoint_create_restore_roundtrip():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state.db")
    tmp_ckpt = str(Path(tmpdir) / "god-checkpoints")
    try:
        se.DB_PATH = tmp_db
        se.GOD_CHECKPOINT_ROOT = tmp_ckpt
        engine, store = make_engine_with_memory()
        engine.god_checkpoint_root = tmp_ckpt
        store.store("Alice", "smoke memory before checkpoint", salience=0.5, kind="event")

        original_tick = engine.frameTick
        original_wood = engine.civilization["stockpile"].get("wood", 0)

        create_preview = engine.god_preview(_checkpoint_create_envelope("before grant"))
        assert_true(create_preview.get("ok"), create_preview)
        assert_true(create_preview.get("reversibilityClass") == "irreversible", create_preview)
        created = engine.god_apply(create_preview["previewId"], "req-ckpt-create")
        assert_true(created.get("ok"), created)
        checkpoint_id = created["outcome"]["checkpointId"]
        ckpt_dir = Path(tmp_ckpt) / checkpoint_id
        assert_true((ckpt_dir / "state.db").is_file(), ckpt_dir)
        assert_true((ckpt_dir / "memory_store.json").is_file(), ckpt_dir)

        grant_preview = engine.god_preview(_grant_envelope("wood", 7, "stockpile"))
        engine.god_apply(grant_preview["previewId"], "req-ckpt-grant")
        mutated_wood = engine.civilization["stockpile"].get("wood", 0)
        assert_true(mutated_wood == original_wood + 7, (original_wood, mutated_wood))
        engine.frameTick += 50

        restore_preview = engine.god_preview(_checkpoint_restore_envelope(checkpoint_id))
        assert_true(restore_preview.get("ok"), restore_preview)
        warning = (restore_preview.get("previewOutcome") or {}).get("irreversibleWarning") or ""
        assert_true("Irreversible world replace" in warning, restore_preview)
        restored = engine.god_apply(restore_preview["previewId"], "req-ckpt-restore")
        assert_true(restored.get("ok"), restored)
        assert_true(engine.frameTick == original_tick, (original_tick, engine.frameTick))
        assert_true(engine.civilization["stockpile"].get("wood", 0) == original_wood,
                    engine.civilization["stockpile"])
        assert_true(engine._god_preview_cache == {}, engine._god_preview_cache)
        assert_true("req-ckpt-restore" in engine._god_requests, engine._god_requests)

        snap = engine.snapshot()
        god_pub = snap.get("god") or {}
        assert_true("checkpoints" not in god_pub, god_pub)
        assert_true(any(r.get("kind") == "checkpoint_restore"
                        for r in god_pub.get("recentPublicInterventions") or []),
                    god_pub)
        print("  OK checkpoint create -> mutate -> restore roundtrip on temp paths")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old


def test_checkpoint_cap_reject_and_replace_oldest():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    try:
        se.DB_PATH = str(Path(tmpdir) / "state.db")
        se.GOD_CHECKPOINT_ROOT = str(Path(tmpdir) / "god-checkpoints")
        engine = make_engine()
        engine.god_checkpoint_root = se.GOD_CHECKPOINT_ROOT
        for i in range(se.GOD_CHECKPOINT_MAX):
            preview = engine.god_preview(_checkpoint_create_envelope(f"ck{i}"))
            engine.god_apply(preview["previewId"], f"req-cap-{i}")

        blocked = engine.god_preview(_checkpoint_create_envelope("one too many"))
        assert_true(not blocked.get("ok"), blocked)
        assert_true("cap" in (blocked.get("reason") or "").lower(), blocked)

        replace_preview = engine.god_preview(_checkpoint_create_envelope("replacement", True))
        assert_true(replace_preview.get("ok"), replace_preview)
        replaced = engine.god_apply(replace_preview["previewId"], "req-cap-replace")
        assert_true(replaced.get("ok"), replaced)
        checkpoints = engine.civilization["godState"]["checkpoints"]
        assert_true(len(checkpoints) == se.GOD_CHECKPOINT_MAX, len(checkpoints))
        assert_true(checkpoints[0]["label"] == "ck1", checkpoints)
        assert_true(checkpoints[-1]["label"] == "replacement", checkpoints)
        print("  OK checkpoint cap rejects preview unless replaceOldest")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old


def test_checkpoint_sight_privacy():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    old_ckpt_root = se.GOD_CHECKPOINT_ROOT
    tmpdir = tempfile.mkdtemp()
    try:
        se.DB_PATH = str(Path(tmpdir) / "state.db")
        se.GOD_CHECKPOINT_ROOT = str(Path(tmpdir) / "god-checkpoints")
        engine = make_engine()
        engine.god_checkpoint_root = se.GOD_CHECKPOINT_ROOT
        preview = engine.god_preview(_checkpoint_create_envelope("privacy ck"))
        engine.god_apply(preview["previewId"], "req-ckpt-privacy")
        sight = engine.god_sight()
        dumped = json.dumps(sight)
        assert_true("backup/god-checkpoints" not in dumped, "checkpoint path leaked in sight")
        assert_true(isinstance(sight.get("checkpoints"), list), sight)
        assert_true(sight["checkpoints"][0].get("label") == "privacy ck", sight)
        assert_true("path" not in sight["checkpoints"][0], sight["checkpoints"][0])
        print("  OK checkpoint Sight summaries omit disk paths")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_CHECKPOINT_ROOT = old_ckpt_root
        se.GOD_MODE_ENABLED = old


def test_deja_vu_replay_rejects_when_flag_off():
    old = se.GOD_MODE_ENABLED
    old_replay = se.GOD_DEJA_VU_REPLAY
    se.GOD_MODE_ENABLED = True
    se.GOD_DEJA_VU_REPLAY = False
    try:
        engine = make_engine()
        preview = engine.god_preview({"kind": "deja_vu_replay", "payload": {}})
        assert_true(not preview.get("ok"), preview)
        assert_true("GOD_DEJA_VU_REPLAY" in (preview.get("reason") or ""), preview)
        print("  OK deja_vu_replay rejects cleanly when GOD_DEJA_VU_REPLAY is off")
    finally:
        se.GOD_DEJA_VU_REPLAY = old_replay
        se.GOD_MODE_ENABLED = old


def run_matrix_phase10_smoke():
    print("Divine Matrix Phase 10 smoke -- Reload / Déjà Vu checkpoints")
    test_checkpoint_create_restore_roundtrip()
    test_checkpoint_cap_reject_and_replace_oldest()
    test_checkpoint_sight_privacy()
    test_deja_vu_replay_rejects_when_flag_off()


def main():
    print("Sovereign God mode Phase 2 smoke")
    test_flag_off_inert()
    test_flag_on_state_shape()
    test_preview_side_effect_free()
    test_tampered_expired_missing_preview_rejected()
    test_idempotent_apply_and_conflict()
    test_text_normalizer()
    test_hostile_strings_round_trip()
    test_godstate_roundtrip_save_restore()
    test_old_save_without_godstate_restores_default()
    test_reset_clears_intervention_state()
    test_cancel_plumbing()
    test_sight_bounded_projection()
    test_preview_and_request_cache_bounds()
    test_unknown_and_future_kinds_rejected()
    test_expire_divine_effects_noop_and_tick()
    test_benchmarks_expose_intervened()

    print("Sovereign God mode Phase 3 smoke -- voice and providence")
    test_providence_set_replace_revoke_expire()
    test_omen_lifecycle_and_memory_contract()
    test_omen_public_visibility_boundary()
    test_whisper_campaign_batch_apply_and_privacy()
    test_whisper_campaign_cancel_clears_linked_omens()
    test_agent_sampling_payload_overlay_and_fast_model()
    test_agent_sampling_expiry_revoke_and_cancel_restore_defaults()
    test_agent_sampling_fast_route_cap_and_privacy()
    print("Divine Matrix Phase 3 smoke -- memory surgery")
    test_memory_insert_query_and_delete_by_keyword()
    test_belief_plant_appears_in_think_payload()
    test_memory_surgery_privacy_no_public_leak()
    test_memory_delete_requires_filter()
    print("Divine Matrix Phase 4 smoke -- reality distortion / context masks")
    test_context_mask_blue_pill_strips_divine_lines()
    test_context_mask_red_pill_truth_without_private_omen_leak()
    test_context_mask_whisper_chain_forges_payload_only()
    test_context_mask_dream_replaces_and_rejects_unknown_keys()
    test_context_mask_cancel_expiry_and_privacy()
    print("Divine Matrix Phase 5 smoke -- decision gate / possession pipeline")
    test_decision_compulsion_forces_pinned_action()
    test_agent_possession_skips_llm()
    test_veto_hold_and_resolve()
    test_sage_emergency_bypasses_decision_gate()
    test_veto_hold_cap()
    test_decision_gate_privacy_and_sight()
    print("Divine Matrix Phase 6 smoke -- Burning Bush + Merovingian Bargain")
    test_burning_bush_message_target_only_and_privacy()
    test_anoint_destiny_stigmata_oracle_and_privacy()
    test_identity_forge_edit_copy_cancel_and_privacy()
    print("Divine Matrix Phase 9 smoke -- Architect Zones")
    test_architect_zone_door_blocks_without_key_allows_with_key()
    test_architect_zone_limbo_freezes_think_and_release_restores()
    test_architect_zone_paint_cancel_reverts()
    test_architect_zone_privacy_and_sight_summary()
    run_matrix_phase10_smoke()
    test_bargain_success_grants_resource()
    test_bargain_expiry_settles_failure()
    test_directive_and_providence_stay_separate()
    test_prompt_lines_frame_window()
    test_prompt_size_cap_and_divine_lines_render()
    test_restore_does_not_refire_omen_memory()

    print("Sovereign God mode Phase 4 smoke -- bounded immediate miracles")
    test_agent_vitals_happy_path_and_clamping()
    test_agent_vitals_cannot_kill()
    test_agent_vitals_rejections()
    test_grant_resource_happy_path_and_carry_semantics()
    test_grant_resource_rejections_and_caps()
    test_structure_condition_repair_and_damage()
    test_structure_condition_damage_crosses_ruin_with_homeless_handling()
    test_structure_condition_rejections()
    test_repair_structures_batch_un_ruins_preview_and_apply()
    test_repair_structures_rejects_ruins_when_un_ruin_false()
    test_clear_ruins_deletes_registry_preview_and_apply()
    test_phase4_miracles_irreversible_and_refuse_cancellation()
    test_phase4_duplicate_request_and_expired_preview()

    print("Sovereign God mode Phase 5 smoke -- storyteller events and timed lawgiver modifiers")
    test_divine_modifier_default_and_flag_gate()
    test_modifier_range_validation_every_key()
    test_gather_zero_path_before_carry_cap_clamp()
    test_fish_modifier_replaces_general_modifier()
    test_collapsed_agent_recovers_under_zero_health_regen()
    test_survival_arithmetic_ordering_hunger_and_starvation()
    test_identity_path_all_modifiers_1_0_byte_identical()
    test_carry_cap_and_low_stock_boundaries()
    test_spoilage_divine_multiplier_bounds()
    test_story_event_one_value_per_key_and_replace_effect_id()
    test_story_event_atomicity_one_invalid_component_changes_nothing()
    test_story_event_full_composition_and_reversibility_class()
    test_story_event_expiry_closes_exactly_once_with_linked_providence()
    test_god_cancel_events_and_refuses_miracles()
    test_active_events_survive_save_restore_with_absolute_expiry()
    test_preview_shows_divine_and_custom_rule_contributions_separately()
    test_story_event_private_visibility_and_target_validation()

    print("Sovereign God mode Phase 6 smoke -- divine weather override")
    test_weather_override_enters_forced_state_without_rng()
    test_weather_override_exit_frame_matches_event_expiry()
    test_natural_tick_weather_does_not_transition_while_override_holds()
    test_weather_override_expiry_handoff_all_four_states()
    test_weather_override_cancel_runs_same_handoff()
    test_weather_override_rejections()
    test_weather_override_reversibility_class_and_preview_disclosure()
    test_weather_override_replace_requires_replace_effect_id()
    test_weather_override_survives_save_restore()
    test_weather_override_restore_time_expiry_closes_and_hands_off_once()
    test_weather_flag_off_natural_cycle_unaffected()

    print("Sovereign God mode Optional Phase 8 smoke -- free-prose story compiler")
    test_compiler_dual_gate_rejects_when_disabled()
    test_compiler_rate_limit()
    test_compiler_session_cap()
    test_compiler_successful_compile_produces_applyable_preview()
    test_compiler_model_shape_mismatch_rejected()
    test_compiler_unknown_modifier_key_rejected()
    test_compiler_non_json_response_rejected()
    test_compiler_timeout_handled_cleanly()
    test_compiler_state_not_persisted_across_restore()
    test_compiler_token_never_in_prompt_or_state()

    print("Sovereign God mode Phase 2+3+4+5+6+8 smoke -- HTTP layer (real server.py app)")
    run_http_tests()
    print("ALL PASS")


if __name__ == "__main__":
    main()
