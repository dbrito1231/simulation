"""Deterministic smoke harness for Sovereign God mode Phase 2 (secure kernel,
persistence, preview, audit -- docs/archive/plan-sovereign-god-mode-v2.md).

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

# Shared envelope/fixture helpers, split out of the original monolithic
# scripts/god_mode_smoke.py (pure move, no behavior change).
from __future__ import annotations

import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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

__all__ = [
    'json',
    'os',
    'random',
    're',
    'sys',
    'tempfile',
    'time',
    'Path',
    'ROOT',
    'TEST_TOKEN',
    'se',
    '_load_roles',
    'make_engine',
    'assert_true',
    '_proclamation_envelope',
    '_providence_envelope',
    '_omen_envelope',
    '_whisper_campaign_envelope',
    '_crowd_compulsion_envelope',
    '_dream_broadcast_envelope',
    '_agent_sampling_envelope',
    '_revoke_agent_sampling_envelope',
    '_memory_insert_envelope',
    '_memory_delete_envelope',
    '_belief_plant_envelope',
    '_context_mask_envelope',
    '_decision_compulsion_envelope',
    '_decision_veto_arm_envelope',
    '_decision_veto_resolve_envelope',
    '_agent_possession_envelope',
    '_revoke_decision_gate_envelope',
    '_burning_bush_message_envelope',
    '_burning_bush_close_envelope',
    '_merovingian_bargain_envelope',
    '_bargain_settle_envelope',
    '_anoint_envelope',
    '_revoke_anoint_envelope',
    'make_engine_with_cognition',
    'make_engine_with_memory',
    '_revoke_envelope',
    '_vitals_envelope',
    '_grant_envelope',
    '_structure_envelope',
    '_repair_structures_envelope',
    '_clear_ruins_envelope',
    '_weather_envelope',
    '_add_test_structure',
    '_identity_edit_envelope',
    '_identity_copy_envelope',
    '_identity_forge_cancel_envelope',
    '_story_event_envelope',
    '_apply_story_event',
    '_apply_weather_override',
    '_valid_compiler_json',
    '_compiler_engine',
    '_architect_zone_envelope',
    '_checkpoint_create_envelope',
    '_checkpoint_restore_envelope',
    '_deja_vu_replay_envelope',
]


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
        "log_divine": lambda *a, **k: None,
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


def _crowd_compulsion_envelope(targets, theme=None, duration=None, remaining_turns=None):
    payload = {"targets": targets}
    if theme is not None:
        payload["theme"] = theme
    if duration is not None:
        payload["durationFrames"] = duration
    if remaining_turns is not None:
        payload["remainingTurns"] = remaining_turns
    return {"kind": "crowd_compulsion", "payload": payload}


def _dream_broadcast_envelope(target_ids, dream_snapshot, duration=None):
    payload = {
        "targetIds": target_ids,
        "dreamSnapshot": dream_snapshot,
        "durationFrames": duration if duration is not None else se.GOD_GUIDANCE_MIN_DURATION_FRAMES,
    }
    return {"kind": "dream_broadcast", "payload": payload}


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
def _apply_weather_override(engine, request_id, state, districts=None, duration=None,
                            replace_effect_id=None):
    """Preview + apply a weather_override in one call. Asserts the PREVIEW
    succeeded (a caller proving a REJECTION should call god_preview
    directly); returns the god_apply response either way."""
    preview = engine.god_preview(_weather_envelope(state, districts=districts, duration=duration,
                                                    replace_effect_id=replace_effect_id))
    assert_true(preview["ok"], preview)
    return engine.god_apply(preview["previewId"], request_id)
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
def _architect_zone_envelope(zone_kind, district_id, cells, **extra):
    payload = {"zoneKind": zone_kind, "districtId": district_id, "cells": cells,
               "durationFrames": 5000}
    payload.update(extra)
    return {"kind": "architect_zone", "payload": payload}
def _checkpoint_create_envelope(label, replace_oldest=False):
    payload = {"label": label}
    if replace_oldest:
        payload["replaceOldest"] = True
    return {"kind": "checkpoint_create", "payload": payload}


def _checkpoint_restore_envelope(checkpoint_id):
    return {"kind": "checkpoint_restore", "payload": {"checkpointId": checkpoint_id}}
def _deja_vu_replay_envelope(target_id, **extra):
    payload = {"targetId": target_id}
    payload.update(extra)
    return {"kind": "deja_vu_replay", "payload": payload}
