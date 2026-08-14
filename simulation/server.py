# HOW TO RUN:
# 1. pip install flask flask-cors requests
# 2. Make sure Ollama is running at localhost:11434 with sim-smart and
#    sim-fast created/warm (uv run python scripts/ollama_setup.py --check to
#    verify; uv run python scripts/ollama_setup.py to (re)apply)
# 3. python server.py
# 4. Open http://127.0.0.1:5001 in Chrome or Firefox
#    (macOS AirPlay uses port 5000 and returns 403 — do not use 5000)

import atexit
import hashlib
import hmac
import json
import os
import re
import signal
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# SYSTEM_PROMPT / SYSTEM_PROMPT_SLIM live in prompts.py (2026-07-24,
# docs/archive/plan-ollama-migration.md Phase 6) -- the single source of truth both
# server.py and scripts/ollama_setup.py (--with-system) import from, so the
# rulebook text is never duplicated. See prompts.py's module docstring for
# why server.py itself can't be imported by the setup script directly
# (module-level side effects: SessionLogger() below opens a new session
# directory, and the live SimEngine is constructed further down).
import prompts as _prompts
SYSTEM_PROMPT = _prompts.SYSTEM_PROMPT
SYSTEM_PROMPT_SLIM = _prompts.SYSTEM_PROMPT_SLIM
COUNCIL_SYSTEM_PROMPT = _prompts.COUNCIL_SYSTEM_PROMPT

# to_ollama_body (the OpenAI-shaped-payload -> Ollama /api/chat wire-format
# conversion) lives in llm_wire.py (2026-07-24, docs/archive/plan-ollama-migration.md
# Phase 4) so scripts/llm_replay_bench.py can import the exact conversion
# this module uses without importing server.py itself (see prompts.py's
# module docstring above for why -- same import-time side-effect reasoning
# applies here). Do not duplicate this mapping back into this module.
import llm_wire as _llm_wire
to_ollama_body = _llm_wire.to_ollama_body

# Non-route, mostly-pure helper logic lives in the sibling _server/ package
# (Phase 5 modularization, pure move split of this former 6,192-line file, no
# behavior change -- see simulation/_server/__init__.py's module docstring).
# Every name imported below is re-exported at this module's top level (plain
# `from _server.x import y`), so existing external callers
# (`import server; server.<name>`) are unaffected.
from _server.roles_data import ROLES, ROLE_PROJECT
from _server.validation_constants import (
    GATHER_ZONES, BASE_RESOURCE_IDS, SEED_PROJECT_IDS, TERRAFORM_PROJECT_IDS,
    KIND_TO_TERRAFORM, TERRAFORM_KIND, RESOURCE_TO_TERRAFORM,
    VISUAL_STYLES, SLUG_RE, FUNCTION_EFFECT_KEYS, VALID_PRODUCE_SCOPES,
    VALID_BOOST_KINDS, VALID_BOOST_SCOPES, VALID_UNLOCK_KINDS,
    MAX_PENDING_BLUEPRINTS, MAX_PENDING_ROLES, MAX_EMERGENT_ROLES,
    MAX_APPROVED_CUSTOM, MAX_CUSTOM_RESOURCES, MAX_TECH_TIER,
    SPRITE_GRID_MIN, SPRITE_GRID_MAX,
)
from _server.model_routing import (
    MODEL_SMART, MODEL_FAST, HIGH_STAKES_ENABLED_REASONS,
    EXTRA_THINKING_PER_WINDOW, EXTRA_THINKING_WINDOW_S,
    is_high_stakes_turn, resolve_high_stakes, model_for_decision,
)
from _server.logging_session import SessionLogger
from _server.memory_store import (
    MemoryStore, embed_text, _cosine, _stable_hash, is_scaffold_text,
    extract_plain_answer,
)
from _server.structured_output import (
    _ollama_error_parts, looks_like_model_not_found_error,
    looks_like_response_format_error,
)
from _server import prompt_format as _prompt_format
from _server.prompt_format import (
    compose_memory, format_nearby_agents, parse_nearby_names,
    format_known_districts, format_known_resources, format_pending_blueprints,
    format_known_recipes, format_pending_recipes, format_approved_custom,
    format_reserved_structure_ids, format_rejected_blueprints,
    format_pending_rules, format_active_rules, format_constitution,
    format_commitment, format_idle_agents, role_default_project,
    RESOURCE_GATHER_ROLES, ROLE_PRIMARY_RESOURCE, parse_project_shortfalls,
    pick_idle_agent_for_project, task_for_role, first_shortfall_resource,
    held_shortfall_resource, build_agent_data,
)
from _server.decision_validation import (
    canonical_effect_vector, sprite_spec_is_degenerate, validate_sprite_block,
    validate_function_block, validate_blueprint, validate_role,
    role_fallback_action, role_fallback_candidates, synthesize_divine_response,
    normalize_decision, _infer_terraform_decision,
)
from _server.anomaly_radar import compute_anomalies

app = Flask(__name__)
CORS(app)

# Ollama migration (2026-07-24, docs/archive/plan-ollama-migration.md Phase 2):
# LM Studio is permanently unavailable. Native /api/chat is the only endpoint
# that actually honors think:false (Phase 0 finding #4 -- the OpenAI-compat
# /v1/chat/completions endpoint silently ignores it and would reintroduce the
# thinking-leak epidemic), so this repo targets it exclusively. See
# ollama_config.md for the full settings contract.


def _resolve_ollama_host():
    """Return SIM_OLLAMA_HOST as host:port only; fail fast on misconfiguration."""
    raw = os.environ.get("SIM_OLLAMA_HOST")
    if raw is None:
        return "localhost:11434"
    host = raw.strip()
    if not host:
        raise ValueError(
            "SIM_OLLAMA_HOST must be host:port (e.g. localhost:11434), "
            "got empty/whitespace"
        )
    if "://" in host or "/" in host or "@" in host:
        raise ValueError(
            f"SIM_OLLAMA_HOST must be host:port only (no scheme/path), got: {raw!r}"
        )
    if ":" not in host:
        raise ValueError(
            f"SIM_OLLAMA_HOST must include a port (host:port), got: {raw!r}"
        )
    hostname, _, port = host.rpartition(":")
    if not hostname or not port:
        raise ValueError(
            f"SIM_OLLAMA_HOST must be host:port, got: {raw!r}"
        )
    return host


OLLAMA_CHAT_URL = f"http://{_resolve_ollama_host()}/api/chat"

# Model routing (which turns go to MODEL_SMART vs MODEL_FAST, and why) is
# documented in _server/model_routing.py's module docstring -- MODEL_SMART/
# MODEL_FAST themselves are defined and imported from there (see the
# `from _server.model_routing import ...` block above).

# Load-time rulebook (Phase 6, docs/archive/plan-ollama-migration.md, shipped DARK --
# TASKS_PENDING item 2b revived). MODEL_SMART_SYS is a SEPARATE Ollama model,
# `sim-smart-sys`, generated by `scripts/ollama_setup.py --with-system` from a
# Modelfile that bakes prompts.py's SYSTEM_PROMPT text in as a Modelfile
# `SYSTEM` directive (never hand-copied -- prompts.py stays the one editable
# source). Verified live (ollama_config.md "Modelfile SYSTEM semantics"): a
# request WITHOUT a system message gets the baked SYSTEM applied; a request
# WITH an explicit system message overrides it entirely (replace, not
# concatenate). SYSTEM_PROMPT_AT_LOAD_TIME (below) is the flag that switches
# routine decision turns from "send MODEL_SMART + explicit SYSTEM_PROMPT" to
# "send MODEL_SMART_SYS + no system message" -- see build_decision_payload.
MODEL_SMART_SYS = "sim-smart-sys"

# DARK FLAG (2026-07-24, Phase 6): when True, routine decision turns (every
# build_decision_payload call except sprite/invention-only and the
# context-overflow slim retry) omit the system message and route to
# MODEL_SMART_SYS instead of MODEL_SMART, relying on that model's baked
# Modelfile SYSTEM directive for the rulebook. Slim-retry/invention/sprite
# turns are UNCHANGED: they always send their own explicit system prompt
# (SYSTEM_PROMPT_SLIM / INVENTION_SYSTEM_PROMPT / SPRITE_UPGRADE_SYSTEM_PROMPT
# respectively -- each differs from the baked SYSTEM_PROMPT, and an explicit
# system message overrides the baked one on either model) and stay on
# MODEL_SMART for cache-locality (no reason to pay for a model switch when
# the prompt is being sent explicitly anyway).
#
# Ships False. Do not flip without first: (1) running
# `uv run python scripts/ollama_setup.py --with-system` to (re)generate and
# `ollama create sim-smart-sys`, (2) an A/B soak comparing fallback rate
# (bad_response + role_fallback) and action distribution against a
# flag-off session of similar length -- the "model forgets a distant system
# prompt" risk is the tripwire (see specs/03-cognition.md and
# ollama_config.md "Load-time rulebook (dark)").
SYSTEM_PROMPT_AT_LOAD_TIME = False

# The MODEL_FAST == MODEL_SMART startup assert now lives in
# _server/model_routing.py (runs at that module's import time, same as
# before -- see the `from _server.model_routing import ...` block above).

# Thinking control (2026-07-11): routine villager turns run with reasoning
# DISABLED -- the old '"thinking": {"type": "disabled"}' payload key was
# Anthropic-API format that LM Studio ignores (every routine decision was
# emitted through reasoning_content). Probed live against this LM Studio
# build: top-level '"reasoning_effort": "none"' is the knob it honors;
# chat_template_kwargs={"enable_thinking": false} and Qwen's /no_think soft
# switch are both ignored (known bug, lmstudio-bug-tracker #1990). High-stakes
# turns (elder / invention / sprite / invention-REQUIRED -- the MODEL_SMART
# set in model_for_decision) keep thinking ON, which makes the smart/fast
# routing meaningful even while both tiers point at one model.
# Replay bench (scripts/llm_replay_bench.py, 40 calls): thinking-leak
# 100% -> 0%, JSON validity 100% -> 100%, action diversity unchanged.
#
# Ollama migration (2026-07-24): the LM Studio-specific "reasoning_effort"
# knob above is history -- Ollama's native /api/chat has its own top-level
# "think" boolean, verified live (Phase 0 finding #4) to actually suppress
# reasoning output entirely (no separate `thinking` field, no <think> tags in
# content). DISABLE_THINKING_ROUTINE now means "send think:false on routine
# turns" (see to_ollama_body / build_decision_payload); the historical intent
# (routine turns never spend budget on reasoning) is unchanged.
DISABLE_THINKING_ROUTINE = True

# Thinking on high-stakes turns is DISABLED (reverted 2026-07-14, Phase 3 --
# see .claude/plans/only-create-the-plan-linear-iverson.md Phase 2/3). Phase 1
# history: a full session (6,320 calls) measured 57% of high-stakes/thinking
# turns -- 65% of the elder's -- returning bad_response (finish_reason
# "length", empty content), then falling back to a canned action. Cause: with
# thinking ON the model spends its whole max_tokens budget (512-1024) on
# reasoning_content before emitting the decision JSON. Phase 1 fixed the
# epidemic by disabling thinking on high-stakes turns entirely. Phase 2 tried
# fixing the root cause instead: scripts/lms_load.py dropped to parallel 2
# (10,000 tokens/slot, same total VRAM) and HIGH_STAKES_MAX_TOKENS=1600 gave
# the completion room to finish, so thinking was re-enabled and measured
# against live traffic. Phase 3 verdict (2026-07-14): a live analysis of 48
# diverse high-stakes samples (assign_task, propose_blueprint,
# sage_review_blueprint, approve_blueprint, upgrade_structure,
# contribute_resources, collect_resource, move_to_district) found ZERO
# measurable reasoning benefit -- with thinking on, the model emits the same
# direct JSON answer, just routed through reasoning_content instead of
# content (THINKING_SAMPLING doesn't set reasoning_effort, so nothing bounds
# or shapes the "reasoning"). The only sample showing genuine descriptive
# text was submit_structure_sprite, an unrelated creative-task pattern
# (always high-stakes regardless of this flag). Since thinking has no
# measured benefit but costs 33% concurrency (parallel 3->2), reverted to
# THINKING_ENABLED_HIGH_STAKES=False and parallel=3.
THINKING_ENABLED_HIGH_STAKES = False

# Qwen-recommended sampling pins (model card). Only temperature was sent
# before; top_p/top_k/min_p silently followed whatever LM Studio preset was
# active, which drifts across app updates and reloads. Temperatures stay the
# behavior-tuned values below. Under Ollama these route into the request's
# "options" object (see to_ollama_body) rather than top-level payload keys;
# the dict shape here is unchanged since it's just merged into the internal
# payload before conversion.
NON_THINKING_SAMPLING = {"top_p": 0.8, "top_k": 20, "min_p": 0}
THINKING_SAMPLING = {"top_p": 0.95, "top_k": 20}

# Experiment lever (off by default): a small presence penalty on routine
# turns may further cut move_to_district fixation (32% share in the
# 2026-07-05 replay benchmark above). Flip to e.g. 0.5 and compare with
# scripts/llm_replay_bench.py before adopting.
ROUTINE_PRESENCE_PENALTY = 0.0

# Phase D model-experiment hook (plan Part 6 / copilot-audit C4): invention-only
# calls override the decision defaults (temperature 0.4 / max_tokens 512).
# The 2026-07-09 council investigation found 32/171 invention completions
# hitting the 512-token ceiling (finish_reason "length") mid-JSON -- the
# dominant cause of "blueprint must be an object" and missing-function
# rejections, since the model runs out of budget before closing the object.
# 1024 gives room for needs + function + an optional sprite without changing
# routine (non-invention) turns. Temperature 0.6 (up from the routine 0.4)
# gives the council fan-out proposal diversity instead of 3 members
# converging on the same idea.
INVENTION_TEMPERATURE = 0.6
INVENTION_MAX_TOKENS = 1024

# Phase 2 (2026-07-14, see .claude/plans/only-create-the-plan-linear-iverson.md):
# max_tokens for high-stakes turns with thinking re-enabled. A live probe
# showed a thinking turn needs ~950-1,300 completion tokens to finish
# reasoning_content and still emit the decision JSON; 1600 leaves headroom.
# 6,163 tokens (worst-case measured prompt) + 1600 = 7,763 < 10,000 (the new
# per-slot budget at parallel 2), so it fits without truncation.
# Phase 3 (2026-07-14): THINKING_ENABLED_HIGH_STAKES reverted to False, so the
# override below is currently dead code (only applies when thinking_active is
# true). Left in place in case thinking on high-stakes turns is revisited.
HIGH_STAKES_MAX_TOKENS = 1600

# Request timeout (seconds). Routine decisions measured median ~18s / p90 ~22s
# in the 2026-07-07 session, well under the old flat 30s -- but invention-only
# turns (bigger prompt: function-block schema, tier rules, sprite instructions
# + few-shot example) measured median ~32s / max ~33.6s, so ~71% of them were
# timing out, logged as "llm offline", and silently falling back to a
# non-propose action -- the actual reason invention councils kept dissolving
# with zero proposals (12 dissolutions, only 2 successful proposals logged).
# Invention turns are rare (a few per hour) so a generous timeout costs
# nothing; DEFAULT_TIMEOUT_S stays tight so routine throughput is unaffected.
#
# THINKING_TIMEOUT_S covers ALL high-stakes turns (see is_high_stakes_turn),
# not just invention/sprite ones -- elder-role and invention-status-REQUIRED
# turns also keep THINKING_SAMPLING on and route to MODEL_SMART, so they're
# just as slow. Measured ~12-20s median under 3-way concurrency; 75s covers
# p99 plus queueing behind other in-flight thinking turns.
DEFAULT_TIMEOUT_S = 30
THINKING_TIMEOUT_S = 75

COUNCIL_LLM_ACTIONS = frozenset({
    "propose_blueprint", "approve_blueprint", "reject_blueprint", "sage_review_blueprint",
})


# HIGH_STAKES_ENABLED_REASONS/EXTRA_THINKING_PER_WINDOW/EXTRA_THINKING_WINDOW_S
# and the is_high_stakes_turn/resolve_high_stakes/model_for_decision predicates
# now live in _server/model_routing.py (imported above) -- see that module's
# docstrings for the A3/rolling-window rationale previously here.


# SessionLogger (the append-only JSON Lines per-session logger class) and its
# retention/preview constants now live in _server/logging_session.py (imported
# above). The singleton below stays here since it's bootstrap/initialization
# (opens the session's log directory, prints the session path) rather than
# pure helper logic -- unchanged from before the split.
session_logger = SessionLogger(os.path.dirname(os.path.abspath(__file__)))
atexit.register(session_logger.flush_benchmarks)
print(f"[server] Logging session to: {session_logger.dir}")


# The in-process vector memory store (MemoryStore class, embed_text/_cosine/
# _stable_hash, is_scaffold_text/extract_plain_answer, and their constants)
# now lives in _server/memory_store.py (imported above). The singleton below
# stays here since its mirror_path depends on session_logger.dir (a
# server.py bootstrap value) -- initialization, not pure helper logic,
# unchanged from before the split.

# MemoryStore is constructed against a restart-stable path (simulation/
# memory_store.json, next to state.db) so semantic recall survives a server
# restart instead of silently starting empty every time; the per-session
# memory.json in the log dir is kept as a mirror for human inspection only
# (see MemoryStore._persist).
MEMORY_STORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "memory_store.json")
memory_store = MemoryStore(
    MEMORY_STORE_PATH, mirror_path=os.path.join(session_logger.dir, "memory.json"))
_load_status, _load_count = getattr(memory_store, "_load_status", ("absent", 0))
print(f"[server] MemoryStore {_load_status} ({_load_count} entries) from {MEMORY_STORE_PATH}")
session_logger.log_benchmark("memory_store_loaded", _load_count)
# _server.prompt_format.compose_memory needs the live memory_store singleton
# but can't import it directly (server.py imports compose_memory FROM
# prompt_format.py, so the reverse import would be circular) -- inject it
# into that module's namespace now that it exists. See prompt_format.py's
# module docstring for the full rationale.
_prompt_format.memory_store = memory_store

# --- Blueprint validation constants (GATHER_ZONES etc.) now live in
# _server/validation_constants.py (imported above).
# _district_kind_map/_fuzzy_terraform_id/_infer_terraform_decision, the
# blueprint/sprite/role validation constants (VISUAL_STYLES, SLUG_RE,
# FUNCTION_EFFECT_KEYS, VALID_*_SCOPES/KINDS, MAX_PENDING_*, MAX_TECH_TIER,
# TERRAFORM_KIND, RESOURCE_TO_TERRAFORM) and the ROLES/ROLE_PROJECT loader
# now live in _server/decision_validation.py, _server/validation_constants.py,
# and _server/roles_data.py respectively (all imported above).
# --- Structured output (Ollama `format`, via the internal response_format shape) ---
# Constrain the model to emit a conforming JSON decision at decode time, which
# largely eliminates the malformed-JSON fallback path. "json_schema" shapes every
# field; "json_object" only guarantees syntactic validity; "off" disables it.
# extract_json_decision/normalize_decision remain as defense in depth regardless.
STRUCTURED_OUTPUT_MODE = "json_schema"

# Full action superset (mirrors AVAILABLE_ACTIONS in index.html). Per-agent
# availability is still enforced by normalize_decision/role_fallback_action.
# World-expansion plan: the fixed move_to_farm/move_to_market/etc. members were
# replaced by a single generic move_to_district (target names a district id,
# or a legacy kind name -- sim_engine.py's _resolve_target_district resolves
# either). Hardcoding a move_to_X per district doesn't scale once districts
# can be founded at runtime rather than fixed at code-authoring time.
DECISION_ACTIONS = [
    "move_to_district", "move_to_agent",
    "collect_resource", "talk_to_nearby", "found_belief", "trade_resource",
    "start_project", "contribute_resources", "build_structure",
    "start_terraform",
    # Phase C (GOODS_ENABLED): structure upkeep. The engine filters it from
    # available_actions when the flag is off; normalize passes it through and
    # the engine surfaces reasons (lastRepairRejection).
    "repair_structure",
    "upgrade_structure",
    "submit_structure_sprite",
    "propose_blueprint", "approve_blueprint", "reject_blueprint", "sage_review_blueprint",
    "assign_task", "change_role", "rest",
    # Survival (#2) and crafting (#4) actions. The client gates these by flag,
    # but the schema enum is a fixed superset (normalize_decision filters).
    "heal_agent",
    "craft_item", "propose_recipe", "approve_recipe", "reject_recipe",
    # CMA + Sid enhancement actions (emergent roles + collective rules/voting).
    "switch_role", "propose_role", "approve_role", "reject_role",
    "propose_rule", "vote_rule", "repeal_rule",
    # Cemetery/burial (permanent-death handling): the engine filters it from
    # available_actions when CEMETERY_ENABLED is off, same pattern as repair_structure.
    "bury_agent",
    # Path 1: composable tiles, terrain mutation, diplomacy treaties.
    "place_block", "remove_block", "dig_terrain", "plant_terrain",
    "propose_treaty", "vote_treaty",
    "deliver_caravan",
    # Huntable wildlife (WILDLIFE_ENABLED): engine offers only when prey is in range.
    "hunt_wildlife",
    "confront_agent",
    # Daily Council Assembly. Offered only to a seated attendee in-session.
    "council_speak", "council_propose", "council_vote",
    # Contracts & escrow (CONTRACTS_ENABLED): engine filters from
    # available_actions when the flag is off; normalize validates shape;
    # apply_decision settlement ships in F3.2.
    "offer_contract", "accept_contract",
]

# Loose shape only; validate_blueprint() stays the authority on blueprint detail.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    # normalize_decision tolerates their absence (action defaults to "rest"),
    # but requiring them at decode time means the grammar itself guarantees
    # the two fields every log/consumer relies on.
    "required": ["action", "reasoning"],
    "properties": {
        "action": {"type": "string", "enum": DECISION_ACTIONS},
        "target": {"type": ["string", "null"]},
        "target_district": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "feeling": {"type": ["string", "null"]},
        "topic": {"type": ["string", "null"]},
        "kind": {"type": ["string", "null"], "enum": ["rule", "blueprint", "idea", None]},
        "title": {"type": ["string", "null"]},
        "detail": {"type": ["string", "null"]},
        "new_role": {"type": ["string", "null"]},
        "relationship_update": {
            "type": ["object", "null"],
            "additionalProperties": {"enum": ["ally", "neutral", "rival"]},
        },
        "reasoning": {"type": "string"},
        "blueprint": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "needs": {"type": "object"},
                "new_resources": {"type": "array"},
                "visual_style": {"type": "string"},
                "sprite": {"type": ["object", "null"]},
                "function": {"type": "object"},
            },
        },
        "recipe": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "inputs": {"type": "object"},
                "station": {"type": ["string", "null"]},
            },
        },
        "contract": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["want", "qty", "pay_coin", "deadline_frames"],
            "properties": {
                "want": {"type": "string"},
                "qty": {"type": "integer", "minimum": 1},
                "pay_coin": {"type": "integer", "minimum": 1},
                "deadline_frames": {"type": "integer", "minimum": 1},
            },
        },
        "role": {
            "type": ["object", "null"],
            "properties": {
                "slug": {"type": "string"},
                "name": {"type": "string"},
                "specialty": {"type": "array", "items": {"type": "string"}},
                "preferredProject": {"type": ["string", "array", "null"]},
                "skill": {"type": "string"},
            },
        },
        "rule": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "value": {"type": ["number", "string", "null"]},
                "description": {"type": ["string", "null"]},
                "tariff": {"type": ["number", "null"], "minimum": 0, "maximum": 0.25},
                "supersedes": {"type": ["string", "null"]},
                "effect": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["subject", "condition", "modifier"],
                    "properties": {
                        "subject": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "resource": {"type": "string"},
                                "role": {"type": "string"},
                                "district": {"type": "string"},
                                "action": {"type": "string", "enum": [
                                    "collect_resource", "contribute_resources", "craft_item"]},
                            },
                            "oneOf": [
                                {"required": ["resource"], "properties": {"resource": {"type": "string"}}},
                                {"required": ["role"], "properties": {"role": {"type": "string"}}},
                                {"required": ["district"], "properties": {"district": {"type": "string"}}},
                                {"required": ["action"], "properties": {
                                    "action": {"type": "string", "enum": [
                                        "collect_resource", "contribute_resources", "craft_item"]}}},
                            ],
                        },
                        "condition": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "action": {"type": "string", "enum": [
                                    "collect_resource", "contribute_resources", "craft_item"]},
                                "resource": {"type": "string"},
                                "role": {"type": "string"},
                                "district": {"type": "string"},
                            },
                        },
                        "modifier": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["kind", "value"],
                            "properties": {
                                "kind": {"type": "string", "enum": ["add"]},
                                "value": {"type": "integer", "minimum": 1, "maximum": 3},
                            },
                        },
                    },
                },
            },
        },
        "belief": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "tenet": {"type": "string"},
                "affinity": {"type": "array", "items": {"type": "string"}},
            },
        },
        "belief_pitch": {
            "type": ["object", "null"],
            "properties": {
                "belief_id": {"type": "string"},
                "pitch": {"type": "string"},
            },
        },
        "vote": {"type": ["string", "null"]},
        "candidate": {"type": ["string", "null"]},
        "sage_decision": {"type": ["string", "null"], "enum": ["approve", "deny", None]},
        # Bounded at the grammar level (not just post-hoc validation) because an
        # unbounded schema lets generation run away to 30-100+ row grids and get
        # cut off by max_tokens mid-JSON (observed: 5/5 length-truncation failures
        # in a Phase 0 probe). Mirrors the limits validate_sprite_block() enforces.
        "sprite": {
            "type": ["object", "null"],
            "properties": {
                "palette": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 5,
                    "items": {"type": "string"},
                },
                "grid": {
                    "type": "array",
                    "minItems": SPRITE_GRID_MIN,
                    "maxItems": SPRITE_GRID_MAX,
                    "items": {
                        "type": "string",
                        "minLength": SPRITE_GRID_MIN,
                        "maxLength": SPRITE_GRID_MAX,
                    },
                },
            },
        },
        # Required on every decision turn while voice_guidance_active is true
        # (binding Voice guidance). Missing/invalid values are synthesized in
        # synthesize_divine_response() — not rejected.
        "divine_response": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "properties": {
                "stance": {"type": "string", "enum": ["follow", "continue"]},
                "reason": {"type": "string"},
            },
            "required": ["stance", "reason"],
        },
    },
}

# Flipped off for the rest of the session if Ollama rejects the format/
# json-schema field. Ollama's `format` param is stable (Phase 0 finding #2 --
# a full JSON schema was honored in every trial), so this auto-degrade is a
# safety net now rather than an expected path, but it stays in place.
_structured_output_enabled = STRUCTURED_OUTPUT_MODE != "off"

# Flipped off (and the setup-failure warning logged once) if Ollama doesn't
# have a routed model id (MODEL_SMART/MODEL_FAST) created. Unlike LM Studio,
# Ollama has no generic "local-model" alias to retry with -- a missing model
# is a setup failure (see looks_like_model_not_found_error and its call site
# in run_agent_decision), so this flag no longer gates a retry; it only
# avoids repeat-logging the same warning every subsequent call.
_model_routing_enabled = True

# Bounds the WORST CASE across every requests.post(OLLAMA_CHAT_URL, ...) call
# site inside a single run_agent_decision() invocation (currently 5: the
# initial call, the format-degrade retry, the context-overflow slim retry,
# Fix 4's answer-quality retry, and Fix 5's terminal fallback AI-choice
# call). The ceiling is 4 rather than 5 because the format-degrade and
# context-overflow retries are mutually exclusive on any one turn, so no
# real path can reach all five. MAX_CONCURRENT_LLM = 3 (sim_engine.py)
# means one agent stuck making sequential calls can otherwise hold a scarce
# worker slot for far longer than one call's timeout; this constant is the
# hard ceiling _post_ollama() (see run_agent_decision) enforces per turn.
LLM_CALLS_PER_TURN_MAX = 4

# Phase 5: retry once (budget permitting) on an answer-quality failure --
# unparseable JSON or normalize_decision stamping _fallback -- with a
# concrete-reason feedback line appended to the retry's user prompt. Default
# on. With this flag off, run_agent_decision behaves exactly as it did before
# this phase (no retry, decision_retries always 0 in llm.jsonl). Deliberately
# does NOT cover network-level failures (llm offline/timeout, compute_error,
# server_error, model_not_found, llm budget exhausted) -- see
# run_agent_decision's _request_error_tag docstring on why Ollama-side
# orphaned generations make a network retry counterproductive there.
DECISION_RETRY_ENABLED = True

# Phase 6 (Fix 5): when DECISION_RETRY_ENABLED's retry is exhausted with no
# usable decision AND the failure was answer-quality (not network -- see
# _request_error_tag's docstring; llm offline/timeout, compute_error,
# server_error, model_not_found, and llm budget exhausted all return before
# ever reaching a fallback call site, so they can never trigger this), offer
# the model a tiny "pick one of these safe options" choice among the role
# ladder's candidates (role_fallback_candidates) instead of always taking the
# highest-priority one silently. Default on. With this flag off,
# run_agent_decision behaves exactly as it did before this phase, including
# no fallback_* fields in llm.jsonl.
FALLBACK_AI_CHOICE_ENABLED = True

# Small, fixed timeout for the Fix 5 choice call -- it is a single-letter
# tiebreak prompt (see _fallback_ai_choice), never the full decision schema,
# so it does not need DEFAULT_TIMEOUT_S/THINKING_TIMEOUT_S headroom. Never
# retried on timeout; the first (highest-priority) candidate is used instead.
FALLBACK_AI_CHOICE_TIMEOUT_S = 10


class LLMBudgetExhausted(Exception):
    """Raised by _post_ollama (inside run_agent_decision) when a single turn
    has already spent its LLM_CALLS_PER_TURN_MAX budget. Deliberately NOT a
    requests.exceptions.RequestException subclass so it can never be mistaken
    for a network failure (llm offline/llm timeout) at any call site."""


def build_response_format(require_divine_response=False):
    """The internal response_format payload field for the current mode, or
    None. Kept in this OpenAI-style shape (rather than Ollama's flatter
    `format` field) so the rest of this module's payload-building code is
    unchanged; to_ollama_body() extracts the actual JSON schema object out of
    the "json_schema" nesting when converting to the wire request.

    require_divine_response: when True (agent has binding Voice guidance
    active this turn -- see run_agent_decision's call site), the schema is
    amended with a shallow copy so "divine_response" is a required, non-null
    object rather than optional -- the model can no longer silently omit it.
    Built fresh per call (never mutates the module-level DECISION_SCHEMA)
    because MAX_CONCURRENT_LLM=3 means multiple think-workers can call this
    concurrently with different require_divine_response values."""
    if not _structured_output_enabled:
        return None
    schema = DECISION_SCHEMA
    if require_divine_response and STRUCTURED_OUTPUT_MODE == "json_schema":
        schema = dict(DECISION_SCHEMA)
        schema["required"] = list(DECISION_SCHEMA["required"]) + ["divine_response"]
        properties = dict(DECISION_SCHEMA["properties"])
        divine_response_field = dict(DECISION_SCHEMA["properties"]["divine_response"])
        divine_response_field["type"] = "object"
        properties["divine_response"] = divine_response_field
        schema["properties"] = properties
    if STRUCTURED_OUTPUT_MODE == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "agent_decision", "schema": schema},
        }
    if STRUCTURED_OUTPUT_MODE == "json_object":
        return {"type": "json_object"}
    return None


# Dedicated, minimal system prompt for invention-only turns (see
# build_invention_prompt / _maybe_invention_backstop). SYSTEM_PROMPT/SLIM carry
# ~20 rules (talk, ecology, survival, crafting, sage priority, roles, voting,
# market, population, knowledge) that are irrelevant to authoring a blueprint
# and cost ~3k prompt tokens on every council member's turn for nothing --
# the 2026-07-09 investigation found invention turns still shipped the full
# rulebook while only ever emitting propose_blueprint. This keeps just the
# output-format contract and the blueprint schema/example, cutting prompt
# size by roughly 85% so the context-overflow retry actually has headroom to
# matter and so INVENTION_MAX_TOKENS goes toward the blueprint, not rules the
# model never needed to see.
INVENTION_SYSTEM_PROMPT = """You are an autonomous agent in a pixel-art village simulation.
This turn your ONLY job is to invent a new structure by responding with propose_blueprint. Do not use any other action.

Respond with ONLY valid JSON. No markdown, no explanation, no extra text.
Do not use chain-of-thought or reasoning — output the JSON object immediately.
The JSON must match this structure exactly:
{
  "action": "propose_blueprint",
  "target": null,
  "target_district": null,
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "<one short sentence>",
  "blueprint": <blueprint object, see schema below>
}

BLUEPRINT object schema:
{
  "id": "library",                       // ^[a-z][a-z0-9_]{1,24}$ -- must NOT match any id already taken (see below)
  "name": "Library",                     // 1-32 chars
  "needs": {"wood": 4, "paper": 2},      // 1-8 entries, each amount 1-5
  "new_resources": [                      // 0-3 items; omit entirely if you aren't adding a resource
    {"id": "paper", "name": "Paper", "gather_zone": "forest", "color": "#E8D5B7"}
  ],
  "visual_style": "house",               // house | farm_plot | workshop | wall | generic
  "function": {                          // REQUIRED: at least one effect -- author this BEFORE sprite
    "produces": [{"resource":"herbs","amount":2,"every_ticks":600,"scope":"district"}],
    "boosts": [{"kind":"gather","resources":["food"],"every_n":4,"bonus":1,"max_bonus":2,"scope":"district"}],
    "unlocks": [{"kind":"craft","station":"workshop"}],
    "houses": {"every_n": 3}
    // optional: "shelter":{"capacity":1-4}, "light":{"scope":"district"}, "upkeep":{"resource":..,"amount":1-5}
  },
  "sprite": {                            // OPTIONAL pixel art -- only include once id/needs/function are done
    "palette": ["#8B5A2B", "#D9C08C", "#4A6B3A"],   // 2-5 hex colors; a=1st, b=2nd, c=3rd...
    "grid": ["...aaa...", "..aaaaa..", ".bbbbbbb.", ".bcbbbcb.", ".bbbbbbb."]
  }                                       // 4-14 rows, each 4-14 chars of . (empty) or a-e
}

EXAMPLE (gatherer proposing a library + paper):
{"action":"propose_blueprint","target":null,"target_district":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"The village needs knowledge storage.","blueprint":{"id":"library","name":"Library","needs":{"wood":4,"paper":2},"new_resources":[{"id":"paper","name":"Paper","gather_zone":"forest","color":"#E8D5B7"}],"visual_style":"house","function":{"produces":[{"resource":"paper","amount":1,"every_ticks":900,"scope":"village"}]}}}"""

USER_PROMPT_TEMPLATE = """Your name: {agent_name}
Your role: {role}
Your skill: {role_skill}
Known roles (switch_role targets): {known_roles}
Your personality: {personality}
Recent memory: {memory}
Resources: {resources}
Hunger: {hunger}/100  Health: {health}/100
Relationships: {relationships}
Your beliefs: {beliefs}
Known beliefs (id/name/tenet): {belief_registry}
Belief authoring exemplars: {belief_examples}
Nearby agents' belief ids: {nearby_beliefs}
Agents near you: {nearby_agents}
{nearby_wildlife_line}Current zone: {world_zone}
Current district: {current_district}
Known districts (use as target_district): {known_districts}
Local resource stocks (your current district): {district_stocks}
Terraform projects (start_terraform targets): {known_terraform}
{season_line}{prices_line}{weather_line}{chronicle_line}{testament_line}{council_digest_line}{library_lessons_line}{path1_lines}{contracts_line}{level_line}Structures built: {structures_built}
Active builds (by district): {active_project}
Build progress (by district): {project_progress}
Civilization directive: {directive}
{divine_lines}Invention status: {invention_status}
Commitment: {commitment_text}
Idle agents needing a task: {idle_agents}
Known resources: {known_resources}
Known recipes (craft_item targets): {known_recipes}
Pending blueprints: {pending_blueprints}
Pending recipes: {pending_recipes}
Pending roles (elder: approve_role/reject_role by slug): {pending_roles}
Approved custom builds: {approved_custom_projects}
Reserved structure ids (propose_blueprint id must avoid ALL of these -- includes unbuilt seed types like forge/granary/market/library): {reserved_structure_ids}
Rejected blueprints (do NOT re-propose these ids): {rejected_blueprints}
Pending rules (vote with vote_rule): {pending_rules}
Enacted rules: {active_rules}
Constitution (ordered provisions): {constitution}
Recent village conversations: {recent_conversations}
Incoming messages (reply or act on these): {inbox}
Module reports (Cognitive Controller — weigh these): {module_reports}
{behavior_nudge}
Available actions: {available_actions}

What do you do next? Respond with only the JSON."""


# The prompt-context formatting helpers (compose_memory/_cap_memory_text/
# format_*), role-default-project helpers (role_default_project,
# _build_resource_gather_roles/RESOURCE_GATHER_ROLES/ROLE_PRIMARY_RESOURCE,
# parse_project_shortfalls, pick_idle_agent_for_project, task_for_role,
# first_shortfall_resource, held_shortfall_resource), and the decision/
# blueprint/role/sprite validators + role-fallback ladder + normalize_decision
# now live in _server/prompt_format.py and _server/decision_validation.py
# (both imported above).


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


# sprites.js was split into ordered files under sprites/ (see index.html's
# <script> tag order and specs/11-viewer.md) -- each gets its own fixed route
# below rather than a filename-taking catch-all, so this can't be used for
# directory traversal.
_SPRITES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprites")
_SPRITE_FILES = (
    "core.js", "tiles.js", "props.js", "structures.js",
    "agents.js", "world.js", "wildlife.js", "shipments.js",
)


def _register_sprite_route(filename):
    def _serve_sprite_file():
        return send_from_directory(_SPRITES_DIR, filename)

    app.add_url_rule(
        f"/sprites/{filename}",
        endpoint=f"sprite_{filename.replace('.', '_')}",
        view_func=_serve_sprite_file,
    )


for _sprite_filename in _SPRITE_FILES:
    _register_sprite_route(_sprite_filename)


# viewer.css was split into ordered files under css/ (see index.html's
# <link> tag order and specs/11-viewer.md) -- each gets its own fixed route
# below rather than a filename-taking catch-all, so this can't be used for
# directory traversal.
_CSS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "css")
_CSS_FILES = (
    "base.css", "panels.css", "agents.css", "council.css", "divine.css", "responsive.css",
)


def _register_css_route(filename):
    def _serve_css_file():
        return send_from_directory(_CSS_DIR, filename)

    app.add_url_rule(
        f"/css/{filename}",
        endpoint=f"css_{filename.replace('.', '_')}",
        view_func=_serve_css_file,
    )


for _css_filename in _CSS_FILES:
    _register_css_route(_css_filename)


# viewer.js was split into ordered files under viewer/ (see index.html's
# <script> tag order and specs/11-viewer.md) -- each gets its own fixed route
# below rather than a filename-taking catch-all, so this can't be used for
# directory traversal.
_VIEWER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")
_VIEWER_FILES = (
    "setup.js", "state.js", "render.js", "sidebar.js", "council.js", "minimap.js",
    "polling.js", "controls.js", "renderloop.js", "divine-bootstrap.js",
    "divine-auth-sight.js", "divine-modal.js", "divine-sight-voice.js",
    "divine-voice.js", "divine-miracles-story.js", "divine-history.js",
    "anomaly.js",
)


def _register_viewer_route(filename):
    def _serve_viewer_file():
        return send_from_directory(_VIEWER_DIR, filename)

    app.add_url_rule(
        f"/viewer/{filename}",
        endpoint=f"viewer_{filename.replace('.', '_').replace('-', '_')}",
        view_func=_serve_viewer_file,
    )


for _viewer_filename in _VIEWER_FILES:
    _register_viewer_route(_viewer_filename)


@app.route("/wildlife_refsheet.html")
def wildlife_refsheet():
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)), "wildlife_refsheet.html"
    )


@app.route("/wildlife.png")
def wildlife_png():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "wildlife.png")


@app.route("/roles.js")
def roles_js():
    # Serve the single role source as a JS global so the browser uses the exact
    # same data the server derives its maps from.
    body = f"const ROLES = {json.dumps(ROLES)};"
    return app.response_class(body, mimetype="application/javascript")


@app.route("/log/event", methods=["POST"])
def log_event():
    """Persist a browser-origin activity or conversation event."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        event_type = body.get("type")
        frame_tick = body.get("frame_tick")
        if event_type == "activity":
            session_logger.log_activity(body.get("message", ""), frame_tick)
        elif event_type == "conversation":
            session_logger.log_conversation(
                body.get("from", ""),
                body.get("to", ""),
                body.get("message"),
                frame_tick,
                kind=body.get("kind", "speech"),
                outcome=body.get("outcome"),
            )
        # Unknown types are ignored; logging must never break the simulation.
    except Exception:
        pass
    return ("", 204)


@app.route("/log/benchmark", methods=["POST"])
def log_benchmark():
    """Persist a browser-origin benchmark metric (Phase 0/8 metrics stream)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        metric = body.get("metric")
        if metric:
            session_logger.log_benchmark(
                metric,
                body.get("value"),
                body.get("frame_tick"),
                body.get("detail"),
            )
    except Exception:
        pass
    return ("", 204)


@app.route("/memory/store", methods=["POST"])
def memory_store_endpoint():
    """Embed + persist one or more memories (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        items = body.get("entries")
        if not isinstance(items, list):
            items = [body]
        stored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = memory_store.store(
                item.get("agent"),
                item.get("text"),
                salience=item.get("salience", 0.5),
                kind=item.get("kind", "event"),
                frame_tick=item.get("frame_tick"),
                tier=item.get("tier"),
            )
            if entry:
                stored += 1
        return jsonify({"ok": True, "stored": stored, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/query", methods=["POST"])
def memory_query_endpoint():
    """Top-k retrieval over the memory store (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        results = memory_store.query(
            agent=body.get("agent"),
            text=body.get("text", ""),
            top_k=body.get("top_k", 5),
            tier=body.get("tier"),
            kinds=body.get("kinds"),
        )
        return jsonify({"results": [
            {
                "text": e["text"],
                "tier": e["tier"],
                "kind": e["kind"],
                "salience": e["salience"],
                "frame_tick": e["frame_tick"],
            }
            for e in results
        ]})
    except Exception:
        return jsonify({"results": []}), 200


@app.route("/memory/summarize", methods=["POST"])
def memory_summarize_endpoint():
    """Summarizer loop (Phase 1, CMA E): compress an agent's recent memories
    into one durable first-person sentence stored back into long-term memory."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        agent = body.get("agent")
        frame_tick = body.get("frame_tick")
        recents = memory_store.recent(agent=agent, limit=12)
        recents = [e for e in recents if e["kind"] != "summary"]
        if len(recents) < 4:
            return jsonify({"ok": False, "reason": "not enough memories"})
        joined = "; ".join(e["text"] for e in recents)
        summary = lm_complete(
            "You compress an agent's recent memories into ONE concise "
            "first-person sentence capturing what matters for their future "
            "decisions. Output only the sentence, no preamble.",
            f"Agent {agent}'s recent memories: {joined}\nSummary:",
            max_tokens=80, temperature=0.4,
        )
        if not summary:
            return jsonify({"ok": False, "reason": "no summary"})
        summary = summary.strip().strip('"').strip()[:200]
        if not summary:
            return jsonify({"ok": False, "reason": "empty summary"})
        memory_store.store(agent, summary, salience=0.9, kind="summary",
                           frame_tick=frame_tick, tier="longTerm")
        session_logger.log_benchmark(
            "memory_summary", memory_store.size(), frame_tick,
            {"agent": agent, "summary": summary},
        )
        return jsonify({"ok": True, "summary": summary, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


# PIANO modules (Phase 3): each is a small, single-sentence cognitive sub-call.
# The Cognitive Controller (the /agent/think decision call) consumes their
# combined output as a bottleneck and emits the one validated decision.
MODULE_PROMPTS = {
    "perception": "You are the Perception module of a village agent. In ONE "
                  "sentence, state the key facts of the current situation and "
                  "any immediate threat or opportunity. If last_reports are "
                  "present, build on or correct them rather than repeating "
                  "them. Reference only agents, resources, and numbers that "
                  "appear in the context; never invent a name, quantity, or "
                  "statistic. Output only the sentence.",
    "social": "You are the Social module of a village agent. In ONE sentence, "
              "suggest who to coordinate with and what to say or request, based "
              "on nearby agents, relationships, and incoming messages. If "
              "last_reports are present, build on or correct them rather than "
              "repeating them. Never suggest coordinating with, messaging, or "
              "requesting from yourself. Reference only agents, resources, and "
              "numbers that appear in the context; never invent a name, quantity, "
              "or statistic. Output only the sentence.",
    "desire": "You are the Desire/Goal module of a village agent. In ONE "
              "sentence, name the single most useful goal right now given the "
              "village's needs and this agent's role and resources. If "
              "last_reports are present, build on or correct them rather than "
              "repeating them. Reference only agents, resources, and numbers "
              "that appear in the context; never invent a name, quantity, or "
              "statistic. Output only the sentence.",
    "reflection": "You are the Reflection module of a village agent. In ONE "
                  "sentence, note one lesson or pattern from the agent's "
                  "memories worth applying now. If last_reports are present, "
                  "build on or correct them rather than repeating them. "
                  "Reference only agents, resources, and numbers that appear in "
                  "the context; never invent a name, quantity, or statistic. "
                  "Output only the sentence.",
    "theory_of_mind": "You are the Theory-of-Mind module of a village agent. "
                      "Given nearby peers in context, model ONE peer you can "
                      "see. Output EXACTLY one line in this format (no extra "
                      "text): PEER=<name> | wants=<short> | good_at=<short> | "
                      "owes=<short> | trust=<0.00-1.00> | expect=<action>. "
                      "Use only peer names and actions from context; never "
                      "invent. expect must be one action the peer might take next.",
}


# Sid-parity Phase 1 rollout: PIANO modules run on their own worker pool
# (SimEngine.piano_workers, PIANO_CONCURRENT_LLM slots), routed to MODEL_FAST
# with a hard, non-blocking timeout -- a slow module is dropped, never
# retried, so it can't stall the decision turn that consumes its report.
PIANO_MODULE_TIMEOUT_S = 15
PIANO_MODULE_MAX_TOKENS = 90


def run_piano_module(module, agent_name, context, frame_tick=None, timeout_s=None):
    """In-process PIANO module runner (Sid-parity Phase 5/1).

    Dispatched onto SimEngine.piano_workers -- a small pool bounded
    independently of MAX_CONCURRENT_LLM (the decision pool), so a module
    backlog can never starve agent decisions. Always MODEL_FAST, always a
    hard PIANO_MODULE_TIMEOUT_S timeout by default. Always-on refreshes may
    supply their separately-gated timeout. Returns a one-sentence report
    string, or None on failure/timeout (dropped, not retried).
    """
    timeout_s = PIANO_MODULE_TIMEOUT_S if timeout_s is None else timeout_s
    sysp = MODULE_PROMPTS.get(module)
    if not sysp:
        return None
    try:
        text = lm_complete(
            sysp,
            f"You ARE {agent_name}. Context: {context}",
            max_tokens=PIANO_MODULE_MAX_TOKENS, temperature=0.5,
            timeout=timeout_s, raise_timeout=True,
        )
        if text:
            text = text.strip().strip('"').strip()[:200]
        session_logger.log_benchmark(
            "module_run", 1, frame_tick,
            {"agent": agent_name, "module": module},
        )
        return text or None
    except requests.exceptions.Timeout:
        session_logger.log_lm_exchange({
            "agent_name": agent_name,
            "frame_tick": frame_tick,
            "module": module,
            "error": "piano_module_timeout",
            "timeout_s": timeout_s,
        })
        eng = globals().get("engine")
        if eng is not None:
            eng._record_llm_orphan_timeout()
        return None
    except Exception:
        return None


def run_belief_pitch(speaker_name, listener_name, belief, pitch, relationship,
                     listener_beliefs, frame_tick=None):
    """Score one explicit persuasion pitch outside SimEngine's lock.

    Returns a bounded quality float or None, allowing the engine to use its
    deterministic offline quality/roll when the LLM is unavailable. The
    engine owns the session cap and only calls this from an already-bounded
    cognition request.
    """
    if not isinstance(belief, dict) or not isinstance(pitch, str):
        return None
    tenet = str(belief.get("tenet") or "").strip()
    if not tenet or not pitch.strip():
        return None
    try:
        text = lm_complete(
            "Judge how persuasive a village belief pitch is for the named listener. "
            "Reply only with one decimal from 0.00 (unpersuasive) to 1.00 (very persuasive).",
            f"Speaker: {speaker_name}. Listener: {listener_name}. Relationship: {relationship}. "
            f"Belief: {belief.get('name')} — {tenet}. Listener already believes: {listener_beliefs or 'none'}. "
            f"Pitch: {pitch.strip()}",
            max_tokens=8, temperature=0.0,
        )
        match = re.search(r"(?:0(?:\.\d+)?|1(?:\.0+)?)", text or "")
        if not match:
            return None
        quality = max(0.0, min(1.0, float(match.group(0))))
        session_logger.log_benchmark(
            "belief_pitch_quality", quality, frame_tick,
            {"speaker": speaker_name, "listener": listener_name,
             "belief": belief.get("id")},
        )
        return quality
    except Exception:
        return None


def run_meta_update(agent_name, report, frame_tick=None):
    """In-process meta-system runner (Sid-parity Phase 5).

    Returns {"autobiography": str|None, "persona": str|None} or None on failure.
    """
    try:
        mems = memory_store.recent(agent=agent_name, limit=14)
        joined = "; ".join(e["text"] for e in mems)

        autobiography = None
        if joined:
            autobiography = lm_complete(
                "Write a 1-2 sentence first-person life story for this village "
                "agent from their memories, capturing their identity and what "
                "they care about. Output only the story.",
                f"Agent {agent_name} ({report.get('role')}). Memories: {joined}. "
                f"Top actions: {report.get('top_actions')}. "
                f"Beliefs: {report.get('beliefs')}.",
                max_tokens=100, temperature=0.6,
            )
            if autobiography:
                autobiography = autobiography.strip().strip('"').strip()[:300]
                if autobiography:
                    memory_store.store(
                        agent_name, autobiography, salience=0.95,
                        kind="autobiography", frame_tick=frame_tick,
                        tier="longTerm",
                    )

        persona = lm_complete(
            "From this agent's self-report, write ONE short imperative directive "
            "(max 18 words) to guide their future behavior, reflecting who they "
            "have become. Output only the directive.",
            f"Agent {agent_name}. Role: {report.get('role')}. "
            f"Top actions: {report.get('top_actions')}. "
            f"Resources: {report.get('resources')}. "
            f"Beliefs: {report.get('beliefs')}. "
            f"Life story: {autobiography or 'n/a'}.",
            max_tokens=40, temperature=0.6,
        )
        if persona:
            persona = persona.strip().strip('"').strip()[:160]

        session_logger.log_benchmark(
            "meta_update", 1, frame_tick,
            {"agent": agent_name, "persona": persona, "autobiography": autobiography},
        )
        return {"autobiography": autobiography, "persona": persona}
    except Exception:
        return None


@app.route("/agent/module", methods=["POST"])
def agent_module_endpoint():
    """Run one PIANO cognitive module (Phase 3). Returns a one-sentence report."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        text = run_piano_module(
            body.get("module"), body.get("agent"), body.get("context"),
            frame_tick=body.get("frame_tick"),
        )
        return jsonify({"text": text})
    except Exception:
        return jsonify({"text": None}), 200


@app.route("/meta/update", methods=["POST"])
def meta_update_endpoint():
    """Meta system (Phase 4, CMA F): build an autobiographical memory and a
    persona directive for an agent from its self-report + memories."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        result = run_meta_update(
            body.get("agent"), body.get("report") or {},
            frame_tick=body.get("frame_tick"),
        )
        if not result:
            return jsonify({"ok": False}), 200
        return jsonify({
            "ok": True,
            "autobiography": result.get("autobiography"),
            "persona": result.get("persona"),
        })
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/clean", methods=["POST"])
def memory_clean_endpoint():
    """Memory Cleaner loop (Phase 1, CMA E): dedupe + trim the store."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        removed = memory_store.clean()
        session_logger.log_benchmark(
            "memory_clean", memory_store.size(), body.get("frame_tick"),
            {"removed": removed},
        )
        return jsonify({"ok": True, "removed": removed, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


# build_agent_data now lives in _server/prompt_format.py (imported above).


def strip_code_fences(text):
    """Remove markdown ```json ... ``` fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def lm_message_text(message):
    """Return the model's output text from an Ollama /api/chat message.

    Ollama's native endpoint with think:false fully suppresses reasoning --
    no separate `thinking` field, no <think> tags in content (Phase 0 finding
    #4) -- so unlike LM Studio there is no reasoning_content fallback channel
    to check anymore (that was purely an LM Studio quirk). As defense in
    depth, if a <think>...</think> block ever leaks into content anyway (a
    contract violation -- would mean `think` wasn't actually sent False, or a
    future Ollama version changed behavior), strip it rather than feed
    chain-of-thought into the decision JSON parser."""
    if not isinstance(message, dict):
        return ""
    content = (message.get("content") or "").strip()
    if content and _THINK_TAG_RE.search(content):
        stripped = _THINK_TAG_RE.sub("", content).strip()
        if stripped:
            content = stripped
    return content


def lm_complete(system_prompt, user_prompt, max_tokens=200, temperature=0.5,
                timeout=30, raise_timeout=False, model=None):
    """Plain-text Ollama completion for the background cognition loops
    (Summarizer, meta system, PIANO modules / Cognitive Controller). Returns the
    text or None on any failure so every caller can degrade gracefully.

    `timeout` defaults to 30s (specs/03:24 background-call budget); PIANO
    module calls pass 15s so a slow module never blocks a decision turn --
    see run_piano_module(). `raise_timeout=True` re-raises
    requests.exceptions.Timeout instead of swallowing it, so a caller that
    wants to log/count timeouts distinctly (run_piano_module) can -- every
    other caller keeps the original swallow-and-return-None behavior.

    `model` defaults to None, which resolves to MODEL_FAST -- every existing
    caller is background cognition and keeps that default unchanged. The ONE
    exception is the Sovereign God mode Optional Phase 8 free-prose compiler
    (sim_engine.god_compile_prose), which explicitly passes model="sim-smart"
    -- see that call site's comment for why it deliberately does NOT use
    MODEL_FAST (sim-fast contention has previously increased PIANO module
    drops; this is a distinct LLM path this module's design intentionally
    keeps off that tier)."""
    payload = {
        # Background cognition is routine work -- always the fast model,
        # unless a caller explicitly names a different model id (see the
        # `model` docstring paragraph above). (No LM-Studio-style
        # "local-model" alias to fall back to in Ollama -- a missing model
        # id is a setup failure, handled where the call sites actually see
        # the error, not here.)
        "model": model or MODEL_FAST,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        **NON_THINKING_SAMPLING,
    }
    if DISABLE_THINKING_ROUTINE:
        # See DISABLE_THINKING_ROUTINE's comment: Ollama's native "think"
        # boolean is the knob (LM Studio's "reasoning_effort" is history).
        # All lm_complete callers are low-stakes/routine.
        payload["think"] = False
    try:
        resp = requests.post(OLLAMA_CHAT_URL, json=to_ollama_body(payload), timeout=timeout)
        body = resp.json()
        message = body["message"]
        done_reason = body.get("done_reason")
    except requests.exceptions.Timeout:
        if raise_timeout:
            raise
        return None
    except (requests.exceptions.RequestException, ValueError, KeyError,
            IndexError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    text = lm_message_text(message)
    if text and is_scaffold_text(text):
        # A reasoning-class model sometimes echoes the instruction as a
        # preamble ("Input: Parents' names...") even in `content` with
        # thinking disabled -- if a real answer follows on a later line,
        # extract_plain_answer's last-line rule recovers it; otherwise
        # this still rejects and the caller falls back deterministically.
        text = extract_plain_answer(text)
    if not text or is_scaffold_text(text):
        return None
    if done_reason == "length" and text.rstrip("'\" ")[-1:] not in ".!?":
        # max_tokens (options.num_predict) cut generation off before a full
        # sentence -- what's left is a mid-thought fragment (e.g. "Output" or
        # "Invent one brief personality trait"), not a real answer.
        return None
    return text


def extract_json_decision(text):
    """Parse a decision object from model output, including partial/truncated JSON."""
    if not text or not isinstance(text, str):
        return None

    cleaned = strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start:idx + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except (ValueError, TypeError):
                    break

    action_match = re.search(r'"action"\s*:\s*"([^"]+)"', cleaned)
    if not action_match:
        return None

    decision = {
        "action": action_match.group(1),
        "target": None,
        "message": None,
        "new_role": None,
        "relationship_update": None,
        "reasoning": "Parsed from partial model response.",
    }
    target_match = re.search(r'"target"\s*:\s*(?:"([^"]*)"|null)', cleaned)
    if target_match:
        decision["target"] = target_match.group(1) or None
    message_match = re.search(r'"message"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|null)', cleaned)
    if message_match:
        decision["message"] = message_match.group(1) or None
    return decision


INVENTION_USER_PROMPT = """You are {agent_name}, the village {role}.

THIS TURN YOU HAVE EXACTLY ONE JOB: invent a new structure for the village by responding with a propose_blueprint action. Ignore every other duty this turn (including task assignment if you are the elder). Do NOT pick any other action.

What problem does this structure solve? Author these REQUIRED fields FIRST, in order: id, name, needs, and a "function" block (produces/boosts/unlocks/houses) describing its mechanical effect — not just a name. A blueprint without a function block is always rejected.

Structure ids already taken (your blueprint id must NOT be any of these): {taken_ids}
Blueprint ids previously rejected (do NOT reuse): {rejected_ids}
Resources you may reference in "needs" and "function": {resource_ids}
{new_resources_line}
{feedback}
{tech_line}Only AFTER id/name/needs/function are complete, and only if you still have room, add an OPTIONAL "sprite" for how it looks: 2-5 hex colors in "palette" plus a "grid" of 4-14 rows (4-14 chars each) using . for empty and a-e for palette colors. If you are unsure you have room left, skip the sprite — a missing sprite is never rejected.
{sprite_example}
Respond with ONLY the JSON decision object: action "propose_blueprint" plus a "blueprint" with id, name, needs, and function REQUIRED; new_resources and sprite are OPTIONAL. Invent something with a NEW effect, not a renamed duplicate."""

SPRITE_UPGRADE_SYSTEM_PROMPT = """You are an autonomous agent in a pixel-art village simulation.

THIS TURN YOU HAVE EXACTLY ONE JOB: design a LARGER pixel-art sprite for a structure that was just upgraded.

Respond with ONLY a JSON decision:
{"action":"submit_structure_sprite","target":null,"sprite":{"palette":["#RRGGBB",...],"grid":[".aab...",...]},"reasoning":"..."}

RULES:
1. action MUST be submit_structure_sprite.
2. sprite.palette: 2-5 hex colors (#RRGGBB).
3. sprite.grid: 4-14 rows, each row 4-14 characters, only . (empty) and letters a-e for palette indices.
4. Grow the grid on whichever dimension(s) have a minimum given (strictly more rows if a row minimum is given, strictly more columns if a column minimum is given). Regardless, the grid must always stay within 4-14 rows and 4-14 columns.
5. Keep the same building identity (roof, walls, door) but expand detail — it is a grown-up version of the same structure.
6. Do NOT invent random unrelated shapes; evolve the existing building bigger."""

SPRITE_UPGRADE_USER_PROMPT = """You are {agent_name}, the village {role}.

Structure to redraw: {structure_name} (type {structure_type}, visual tier {tier})
Minimum size to beat: {size_requirement}

{feedback}
{sprite_example}

Submit submit_structure_sprite with a bigger sprite grid that clearly shows a larger version of this facility."""

# Few-shot sprite references derived from Kenney's CC0 "Tiny Town" pack (see
# simulation/sprite_examples/LICENSE.md). One example is shown per invention
# turn — enough to teach the grid format and pixel-art idioms (outline, roof
# band, symmetric openings) without bloating the prompt.
SPRITE_EXAMPLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "sprite_examples", "examples.json")
try:
    with open(SPRITE_EXAMPLES_PATH, encoding="utf-8") as _f:
        SPRITE_EXAMPLES = json.load(_f)
except Exception:
    SPRITE_EXAMPLES = []


def format_sprite_example(frame_tick):
    """One rotating few-shot sprite example (deterministic per frame window so
    retries see the same example but successive inventions see variety)."""
    if not SPRITE_EXAMPLES:
        return ""
    ex = SPRITE_EXAMPLES[(int(frame_tick or 0) // 600) % len(SPRITE_EXAMPLES)]
    body = json.dumps({"palette": ex["palette"], "grid": ex["grid"]},
                      separators=(",", ":"))
    return (f'Example of a good sprite (a {ex["name"].replace("_", " ")} — '
            f'{ex["note"]}): {body}')


def _sprite_upgrade_size_requirement(min_rows, min_cols):
    """Render the per-dimension growth requirement for a sprite upgrade turn.
    min_rows/min_cols of 0 means "no growth requirement" for that dimension
    (see validate_sprite_block's `if min_rows and ...` semantics) — never
    collapse 0 into a fake floor. The 4-14 grid bound is always stated."""
    bound = (f"grid must stay within {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} rows "
             f"and {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} columns")
    if min_rows and min_cols:
        growth = f"strictly more than {min_rows} rows AND strictly more than {min_cols} columns"
    elif min_rows:
        growth = f"strictly more than {min_rows} rows (no minimum on columns)"
    elif min_cols:
        growth = f"strictly more than {min_cols} columns (no minimum on rows)"
    else:
        growth = "no minimum size requirement this turn"
    return f"{growth} ({bound})."


def build_sprite_upgrade_prompt(data, retry_feedback=None):
    ctx = data.get("sprite_design_context") or {}
    feedback = data.get("behavior_nudge") or ""
    # Phase 5: the decision retry's concrete-reason line takes priority over
    # the ordinary behavior_nudge -- it's a same-turn "your last reply on
    # THIS call failed, here's exactly why" signal, not a rolling nudge.
    if retry_feedback:
        feedback = f"{retry_feedback} {feedback}".strip()
    min_rows = int(ctx.get("minRows") or 0)
    min_cols = int(ctx.get("minCols") or 0)
    return SPRITE_UPGRADE_USER_PROMPT.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        structure_name=ctx.get("structureName") or ctx.get("structureType") or "structure",
        structure_type=ctx.get("structureType") or "unknown",
        tier=(ctx.get("tier") or 0) + 1,
        size_requirement=_sprite_upgrade_size_requirement(min_rows, min_cols),
        feedback=feedback,
        sprite_example=format_sprite_example(data.get("frame_tick")),
    )


def build_invention_prompt(data, retry_feedback=None):
    """Slim, single-purpose user prompt for a dedicated invention turn (set by
    the engine's _maybe_invention_backstop). Strips every competing nudge and
    state section so the model's whole budget goes into authoring a valid,
    novel blueprint."""
    # Unbuilt seed templates are not necessarily present in structure_counts,
    # but their ids must still be reserved from invention.
    taken = sorted(set(list(SEED_PROJECT_IDS)
                       + list(data.get("structure_counts") or {})
                       + [str(a) for a in data.get("approved_custom_projects") or []]
                       + [b.get("id") for b in data.get("pending_blueprints") or []
                          if isinstance(b, dict) and b.get("id")]))
    # C3: prompt-only capped view; the "taken" ids above intentionally stay on
    # the full approved_custom_projects/pending_blueprints fields since those
    # feed id-collision avoidance, not just display.
    rejected = [str(r) for r in (data.get("rejected_blueprints_prompt") or data.get("rejected_blueprints") or [])]
    known_resources = data.get("known_resources") or []
    resources = [r.get("id") for r in known_resources
                 if isinstance(r, dict) and r.get("id")]
    feedback = data.get("behavior_nudge") or ""
    build_ctx = data.get("invention_build_context") or {}
    if build_ctx.get("typeName"):
        build_line = (f"You were trying to build: {build_ctx['typeName']}. Your invention should "
                      f"plausibly satisfy that need or unlock a path to it.")
        feedback = f"{build_line} {feedback}".strip()
    # Phase 5: prepend the decision retry's concrete-reason line (same-turn
    # "your last reply failed, here's exactly why") ahead of the rolling nudge.
    if retry_feedback:
        feedback = f"{retry_feedback} {feedback}".strip()
    new_resources_line = (
        'You may introduce up to 3 brand-new resources via "new_resources", each '
        'with a gather_zone of farm, forest, village, market, beach, cave, or ocean '
        '(or null for crafted-only goods). There is no village-wide cap on invented resources.')
    # Phase D (TECH_TREE_ENABLED): one short tier line -- what the current
    # tech tier allows and how the next tier is reached. Empty (and therefore
    # byte-identical to the Phase C prompt) when the engine sends no tier.
    tech_tier = data.get("village_tech_tier")
    tech_line = ""
    if tech_tier:
        tech_line = (f'Village tech tier: {tech_tier}. Your blueprint may set "tier" '
                     f'1-{tech_tier} (default 1); tier {tech_tier + 1} tech needs a station '
                     f'whose function unlocks tier {tech_tier + 1} built first.\n'
                     if tech_tier < MAX_TECH_TIER else
                     f'Village tech tier: {tech_tier} (the highest). Your blueprint may set '
                     f'"tier" 1-{tech_tier} (default 1).\n')
    return INVENTION_USER_PROMPT.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        taken_ids=", ".join(taken) or "none",
        rejected_ids=", ".join(rejected) or "none",
        resource_ids=", ".join(resources) or "none",
        new_resources_line=new_resources_line,
        feedback=feedback,
        tech_line=tech_line,
        sprite_example=format_sprite_example(data.get("frame_tick")),
    )


def _format_voice_guidance_line(kind, text):
    """Binding Voice prompt line for public providence or private omen."""
    label = "Divine guidance (binding)" if kind == "public" else "Private guidance (binding)"
    return f"{label}: {text} State whether you follow or continue in divine_response.\n"


def build_user_prompt(data, slim=False, retry_feedback=None):
    """Fill in USER_PROMPT_TEMPLATE from the agent/civilization state. When
    slim=True (the context-overflow retry, see run_agent_decision), drop the
    memory line and recent conversations -- the two most compressible,
    highest-variance-size fields -- to shrink the prompt. invention_only
    turns get the dedicated proposal-only prompt instead.

    retry_feedback (Phase 5, see run_agent_decision's answer-quality retry):
    an optional concrete-reason string folded into behavior_nudge (routine
    turns) or the sprite/invention prompt's own feedback field, ahead of the
    ordinary rolling nudge -- a same-turn "your last reply on THIS call
    failed, here's exactly why" signal."""
    if data.get("sprite_design_only"):
        return build_sprite_upgrade_prompt(data, retry_feedback=retry_feedback)
    if data.get("invention_only"):
        return build_invention_prompt(data, retry_feedback=retry_feedback)
    nearby_formatted = format_nearby_agents(data.get("nearby_agents"))
    known_resources = data.get("known_resources") or []
    pending_blueprints = data.get("pending_blueprints") or []
    # C3: prompt rendering uses the capped *_prompt views (falling back to the
    # uncapped field for older callers); validation elsewhere in this file
    # keeps reading the full "approved_custom_projects"/"rejected_blueprints"
    # fields untouched.
    approved_custom_projects = data.get("approved_custom_projects_prompt") or data.get("approved_custom_projects") or []
    rejected_blueprints = data.get("rejected_blueprints_prompt") or data.get("rejected_blueprints") or []
    idle_agents = data.get("idle_agents") or []
    pending_roles = data.get("pending_roles") or []
    behavior_nudge = data.get("behavior_nudge") or ""
    if retry_feedback:
        behavior_nudge = f"{retry_feedback} {behavior_nudge}".strip()
    # Phase C: one short season line, rendered ONLY when the engine sends a
    # season (GOODS_ENABLED) so flag-off prompts stay byte-identical.
    season = data.get("season")
    season_line = ""
    if season:
        winter_hint = " — stocks do not regrow; rely on stored food" if season == "winter" else ""
        season_line = f"Season: {season}{winter_hint}\n"
    # Phase D: the era (one line) replaces the vanity level when the engine
    # sends one (TECH_TREE_ENABLED); with the flag off the engine sends None
    # and this renders the exact Phase C level line.
    era = data.get("era")
    if era:
        tech_tier = data.get("village_tech_tier")
        tier_part = f" (tech tier {tech_tier})" if tech_tier else ""
        level_line = f"Era: {era}{tier_part}\n"
    else:
        level_line = f"Civilization level: {data.get('civilization_level', 1)}\n"
    # Phase E: one short prices line, rendered ONLY when the engine sends one
    # (ECONOMY_ENABLED and a market exists) so flag-off / no-market prompts
    # stay byte-identical to Phase D.
    prices_raw = data.get("prices_line")
    prices_line = f"Prices: {prices_raw}\n" if prices_raw else ""
    # Living-ecosystem Phase 5: one short line, rendered ONLY while the
    # engine's weather state is "storm"/"clearing" (WEATHER_GOVERNANCE_ENABLED
    # and WEATHER_ENABLED both on) -- same fold-in-only-when-set pattern as
    # prices_line/chronicle_line, so flag-off / clear-weather prompts stay
    # byte-identical to Phase 4 alone.
    weather_raw = data.get("weather_line")
    weather_line = f"Weather: {weather_raw}\n" if weather_raw else ""
    # Huntable wildlife hint: rendered ONLY when the engine reports prey in
    # HUNT_RADIUS (WILDLIFE_ENABLED), same fold-in-only-when-set pattern.
    wildlife_raw = data.get("nearby_wildlife_line")
    nearby_wildlife_line = f"{wildlife_raw}\n" if wildlife_raw else ""
    # Phase F: one-word life stage folded into the existing personality line
    # (no new template line -- near-zero token cost, and with the flag off
    # the engine sends life_stage=None so this renders byte-identical to
    # Phase E).
    life_stage = data.get("life_stage")
    personality_text = data.get("personality") or ""
    if life_stage:
        personality_text = f"{life_stage}, {personality_text}" if personality_text else life_stage
    # Phase G: practiced skill levels folded into the existing "Your skill:"
    # line (no new template line) -- only nonzero levels are shown so an
    # unpracticed agent's line stays exactly the Phase F role_skill text.
    role_skill_text = data.get("role_skill", "")
    skills = data.get("skills")
    if skills:
        practiced = ", ".join(f"{k} {v}" for k, v in skills.items() if v > 0)
        if practiced:
            role_skill_text = f"{role_skill_text} (practiced: {practiced})"
    # Phase G: one short rotating "Village history: ..." line, rendered ONLY
    # when the engine sends one (CULTURE_ENABLED and the chronicle has an
    # entry) so flag-off / empty-chronicle prompts stay byte-identical.
    chronicle_line_raw = data.get("chronicle_line")
    chronicle_line = f"Village history: {chronicle_line_raw}\n" if chronicle_line_raw else ""
    testament_line_raw = data.get("testament_line")
    testament_line = f"Village testament: {testament_line_raw}\n" if testament_line_raw else ""
    # Sovereign God mode (Phase 3 — Voice binding): public providence and
    # private omens use binding prompt lines requiring divine_response; Matrix
    # anoint/bush/story lines keep soft "interpret or ignore" wording.
    divine_lines_parts = []
    divine_public_raw = data.get("divine_public_line")
    if divine_public_raw:
        divine_lines_parts.append(_format_voice_guidance_line("public", divine_public_raw))
    divine_private_raw = data.get("divine_private_line")
    if divine_private_raw:
        divine_lines_parts.append(_format_voice_guidance_line("private", divine_private_raw))
    divine_bush_raw = data.get("divine_burning_bush_line")
    if divine_bush_raw:
        divine_lines_parts.append(
            f"Divine audience: {divine_bush_raw} You may respond in talk or reason.\n")
    divine_anoint_raw = data.get("divine_anointment_line")
    if divine_anoint_raw:
        divine_lines_parts.append(
            f"Anointed destiny: {divine_anoint_raw} You may interpret or ignore it.\n")
    divine_event_raw = data.get("divine_public_event_line")
    if divine_event_raw:
        divine_lines_parts.append(f"Divine story: {divine_event_raw} You may interpret or ignore it.\n")
    truth_raw = data.get("divine_simulation_truth_line")
    if truth_raw:
        divine_lines_parts.append(f"{truth_raw}\n")
    divine_lines = "".join(divine_lines_parts)
    council_digest_raw = data.get("council_digest_line")
    council_digest_line = (
        f"Recent council: {council_digest_raw}\n" if council_digest_raw else ""
    )
    lessons_raw = data.get("library_lessons")
    library_lessons_line = f"Library lessons: {lessons_raw}\n" if lessons_raw else ""
    path1_parts = []
    if data.get("path1_tool_line"):
        path1_parts.append(data["path1_tool_line"])
    if data.get("path1_industry_line"):
        path1_parts.append(data["path1_industry_line"])
    if data.get("path1_neighbor_line"):
        path1_parts.append(data["path1_neighbor_line"])
    if data.get("settlement_stores_line"):
        path1_parts.append(f"Settlement stores: {data['settlement_stores_line']}")
    path1_lines = ("\n".join(path1_parts) + "\n") if path1_parts else ""
    contracts_raw = data.get("contracts_line")
    contracts_line = f"Open contracts: {contracts_raw}\n" if contracts_raw else ""

    return USER_PROMPT_TEMPLATE.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        role_skill=role_skill_text,
        known_roles=", ".join(data.get("known_role_ids") or []) or "none",
        personality=personality_text,
        memory="none" if slim else compose_memory(data),
        hunger=data.get("hunger", 100),
        health=data.get("health", 100),
        resources=data.get("resources"),
        relationships=data.get("relationships"),
        beliefs=data.get("beliefs") or "none",
        belief_registry="; ".join(
            f"{b.get('id')} / {b.get('name')}: {b.get('tenet')}"
            for b in (data.get("belief_registry") or []) if isinstance(b, dict)
        ) or "none",
        belief_examples="; ".join(
            f"{b.get('id')} ({b.get('kind')}): {b.get('tenet')}"
            for b in (data.get("belief_examples") or []) if isinstance(b, dict)
        ) or "none",
        nearby_beliefs=data.get("nearby_beliefs") or "none",
        nearby_agents=nearby_formatted,
        nearby_wildlife_line=nearby_wildlife_line,
        world_zone=data.get("world_zone"),
        current_district=data.get("current_district", "none"),
        known_districts=format_known_districts(data.get("known_districts") or []),
        district_stocks=data.get("district_stocks") or "none",
        known_terraform=", ".join(data.get("known_terraform") or []) or "none",
        level_line=level_line,
        structures_built=data.get("structures_built", 0),
        active_project=data.get("active_project", "none"),
        project_progress=data.get("project_progress", "none"),
        directive=data.get("directive", "none"),
        divine_lines=divine_lines,
        invention_status=data.get("invention_status", "not needed"),
        commitment_text=format_commitment(data.get("commitment")),
        idle_agents=format_idle_agents(idle_agents),
        known_resources=format_known_resources(known_resources),
        known_recipes=format_known_recipes(data.get("known_recipes") or []),
        pending_blueprints=format_pending_blueprints(pending_blueprints),
        pending_recipes=format_pending_recipes(data.get("pending_recipes") or []),
        pending_roles="; ".join(
            f"{role.get('slug')} ({role.get('name')}; specialty: {', '.join(role.get('specialty') or []) or 'none'})"
            for role in pending_roles if isinstance(role, dict)
        ) or "none",
        approved_custom_projects=format_approved_custom(approved_custom_projects),
        reserved_structure_ids=format_reserved_structure_ids(approved_custom_projects, pending_blueprints),
        rejected_blueprints=format_rejected_blueprints(rejected_blueprints),
        pending_rules=format_pending_rules(data.get("pending_rules") or []),
        active_rules=format_active_rules(data.get("active_rules") or []),
        constitution=format_constitution(data.get("constitution") or []),
        season_line=season_line,
        prices_line=prices_line,
        weather_line=weather_line,
        chronicle_line=chronicle_line,
        testament_line=testament_line,
        council_digest_line=council_digest_line,
        library_lessons_line=library_lessons_line,
        path1_lines=path1_lines,
        contracts_line=contracts_line,
        recent_conversations="none" if slim else data.get("recent_conversations", "none"),
        inbox=data.get("inbox", "none"),
        module_reports=data.get("module_reports", "none"),
        behavior_nudge=behavior_nudge,
        available_actions=data.get("available_actions"),
    )


_last_routine_system_prompt = None
_system_prompt_mismatch_warned = False


def _check_system_prompt_stability(system_content):
    """Log-once guard (Phase 2, TASKS_PENDING #2a): warn if the routine-turn
    system message differs from the one seen on a prior routine turn this
    session. Ollama (llama.cpp-based, same as LM Studio) reuses KV cache by longest common prefix per slot, so
    a system-prompt mutation mid-session would silently forfeit that reuse
    for every subsequent call -- this makes it observable instead.

    Fast path is an identity check (`is`): SYSTEM_PROMPT is a module global
    that should only ever be assigned once (module load) or, under
    TECH_TREE_ENABLED, rewritten once immediately after via .replace() before
    any request is served -- never reassigned once serving starts. Identity
    equality costs nothing per call. Only on an identity mismatch do we fall
    back to a value comparison (and then a hash for the warning message),
    so this never crashes and adds no per-call overhead in the expected
    (stable) case.

    Not called at all when SYSTEM_PROMPT_AT_LOAD_TIME is True and the turn is
    the omitted-system-message case (see build_decision_payload) -- omitting
    the message on purpose is not a "changed prefix", it is a different,
    intentional call shape, so routing it through this guard would fire a
    spurious mismatch warning against the last turn that DID send a system
    message (e.g. a slim retry or an invention turn in between)."""
    global _last_routine_system_prompt, _system_prompt_mismatch_warned
    if _last_routine_system_prompt is None:
        _last_routine_system_prompt = system_content
        return
    if system_content is _last_routine_system_prompt:
        return
    if system_content == _last_routine_system_prompt:
        _last_routine_system_prompt = system_content
        return
    if not _system_prompt_mismatch_warned:
        _system_prompt_mismatch_warned = True
        old_sha = hashlib.sha256(_last_routine_system_prompt.encode()).hexdigest()[:12]
        new_sha = hashlib.sha256(system_content.encode()).hexdigest()[:12]
        print(f"[server] WARNING: system prompt changed mid-session (cache "
              f"invalidated) old_sha256={old_sha} new_sha256={new_sha}")
    _last_routine_system_prompt = system_content


def build_decision_payload(data, self_prompt, response_format, slim=False, retry_feedback=None):
    """Assemble the internal chat-completion-shaped payload for a decision
    call (converted to Ollama's /api/chat wire body by to_ollama_body() at
    the actual POST site). slim=True builds the reduced-context retry payload (see
    run_agent_decision): SYSTEM_PROMPT_SLIM instead of SYSTEM_PROMPT (drops
    the worked EXAMPLE blocks) plus the slim user prompt. The rules and JSON
    schema are kept either way so response_format still shapes the output.
    invention_only turns always use INVENTION_SYSTEM_PROMPT instead (the
    ~20-rule village rulebook is irrelevant to authoring a blueprint and
    slim/full made almost no size difference for these calls -- see its
    docstring), regardless of the slim flag.

    retry_feedback (Phase 5, DECISION_RETRY_ENABLED): an optional
    concrete-reason string for the single answer-quality retry in
    run_agent_decision (unparseable JSON or a _fallback-stamped
    normalize_decision result). Threaded into build_user_prompt() for
    routine/high-stakes turns and into the sprite/invention prompt builders;
    build_council_user_prompt() (prompts.py) takes no such parameter, so for
    council turns the feedback is prefixed onto its returned user_content
    below instead -- same effect, no change to prompts.py's signature.

    SYSTEM_PROMPT_AT_LOAD_TIME (Phase 6, dark by default): when True, the
    primary (non-slim) routine/high-stakes dispatch -- i.e. everything that
    isn't sprite_design_only, invention_only, or the slim retry -- omits the
    system message entirely and routes to MODEL_SMART_SYS, relying on that
    model's baked Modelfile SYSTEM directive (generated from this exact
    SYSTEM_PROMPT text by `scripts/ollama_setup.py --with-system`) instead of
    sending it every call. Sprite/invention turns and the slim retry are
    unaffected -- they always send their own explicit system message and stay
    on MODEL_SMART (see the flag's definition for the full rationale)."""
    omit_system_prompt = False
    if data.get("sprite_design_only"):
        system_content = SPRITE_UPGRADE_SYSTEM_PROMPT
    elif data.get("invention_only"):
        system_content = INVENTION_SYSTEM_PROMPT
    elif data.get("council_turn"):
        system_content = COUNCIL_SYSTEM_PROMPT
    else:
        system_content = SYSTEM_PROMPT_SLIM if slim else SYSTEM_PROMPT
        system_content = _prompts.append_contracts_addendum(system_content)
        if not slim:
            if SYSTEM_PROMPT_AT_LOAD_TIME:
                # The baked Modelfile SYSTEM directive applies instead (see
                # MODEL_SMART_SYS's definition) -- omitting the message here
                # is the intended, non-spurious case, so the stability guard
                # below (designed to catch an *unintended* mid-session prompt
                # mutation) must not run against it.
                omit_system_prompt = True
            else:
                # Only the primary (non-retry) routine dispatch is checked: the
                # slim retry deliberately swaps to SYSTEM_PROMPT_SLIM on overflow
                # and is expected to forfeit cache reuse for that one call (see
                # specs/03-cognition.md KV-cache prefix stability note).
                _check_system_prompt_stability(system_content)
    # Persona goes at the TOP OF THE USER MESSAGE, not appended to the system
    # prompt: Ollama (llama.cpp-based, same as LM Studio) reuses KV cache by
    # longest common prefix per slot, so
    # per-agent text inside the system message forced full prompt
    # reprocessing (~5k tokens) on every agent rotation. With the system
    # prompt byte-identical across agents it becomes a shared cached prefix.
    if data.get("council_turn"):
        user_content = _prompts.build_council_user_prompt(data)
        if retry_feedback:
            user_content = f"RETRY (previous reply rejected): {retry_feedback}\n\n" + user_content
    else:
        user_content = build_user_prompt(data, slim=slim, retry_feedback=retry_feedback)
    if self_prompt:
        user_content = (f"YOUR PERSONA (act in character): {self_prompt}\n\n"
                        + user_content)
    # Computed once and reused for both the max_tokens override below and the
    # sampling branch further down, so the two conditions can't drift apart.
    thinking_active = is_high_stakes_turn(data) and THINKING_ENABLED_HIGH_STAKES
    max_tokens, temperature = 512, 0.4
    if data.get("sprite_design_only"):
        max_tokens = 768
        temperature = 0.3
    elif data.get("invention_only"):
        # Phase D experiment hook: per-call overrides for invention-only turns.
        if INVENTION_MAX_TOKENS is not None:
            max_tokens = INVENTION_MAX_TOKENS
        if INVENTION_TEMPERATURE is not None:
            temperature = INVENTION_TEMPERATURE
    elif thinking_active:
        # Phase 2: high-stakes turns with thinking re-enabled need extra
        # budget for reasoning output on top of the decision JSON.
        max_tokens = HIGH_STAKES_MAX_TOKENS
    messages = [] if omit_system_prompt else [{"role": "system", "content": system_content}]
    messages.append({"role": "user", "content": user_content})
    payload = {
        "model": MODEL_SMART_SYS if omit_system_prompt else model_for_decision(data),
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if thinking_active:
        payload.update(THINKING_SAMPLING)
        # thinking_active turns intentionally omit "think" -- see
        # to_ollama_body's docstring: omitted means the model may think.
    else:
        payload.update(NON_THINKING_SAMPLING)
        if DISABLE_THINKING_ROUTINE:
            payload["think"] = False
        if ROUTINE_PRESENCE_PENALTY:
            payload["presence_penalty"] = ROUTINE_PRESENCE_PENALTY
    if response_format is not None:
        payload["response_format"] = response_format
    divine_sampling = data.get("divine_sampling")
    if isinstance(divine_sampling, dict):
        model_key = divine_sampling.get("model")
        if model_key == "sim-fast":
            payload["model"] = MODEL_FAST
        elif model_key == "sim-smart":
            payload["model"] = MODEL_SMART_SYS if omit_system_prompt else MODEL_SMART
        if "temperature" in divine_sampling:
            payload["temperature"] = divine_sampling["temperature"]
        for key in ("top_p", "top_k", "min_p"):
            if key in divine_sampling:
                payload[key] = divine_sampling[key]
    return payload


def is_context_overflow_error(err_text, err_type=None):
    """True for Ollama's context-overflow signal (docs/ollama_config.md
    'Overflow / truncation contract', Phase 0 finding #5): HTTP 400 with
    error.type == "exceed_context_size_error" is the structured, preferred
    signal; a message mentioning both "context" and "exceed" is the fallback
    for any future Ollama build that changes the type string. This replaces
    LM Studio's "context size has been exceeded" string-sniff -- Ollama does
    NOT silently truncate (confirmed live), it errors instead."""
    if err_type == "exceed_context_size_error":
        return True
    low = (err_text or "").lower()
    return "context" in low and "exceed" in low


def score_belief_pitch_decision(decision, data):
    """Attach a model score to a validated belief pitch when the engine's
    bounded budget says one remains. This runs in the decision worker, after
    the normal request and never while SimEngine's state lock is held."""
    if not isinstance(decision, dict) or decision.get("action") != "talk_to_nearby":
        return decision
    pitch = decision.get("belief_pitch")
    if not isinstance(pitch, dict) or not data.get("belief_pitch_budget_remaining"):
        return decision
    belief_id = pitch.get("belief_id")
    belief = next((b for b in data.get("belief_registry") or []
                   if isinstance(b, dict) and b.get("id") == belief_id), None)
    if not belief or belief_id not in (data.get("belief_ids") or []):
        return decision
    listener = decision.get("target")
    nearby_beliefs = data.get("nearby_beliefs") or {}
    # The engine's payload includes only agents within the existing 80px talk
    # radius. Ordinary talk may target a distant agent and walk toward them,
    # but do not spend a pitch-scoring call until this is a real conversation.
    if listener not in nearby_beliefs:
        return decision
    # The per-world cap is authoritatively checked/incremented under the
    # engine lock when the score is applied. Concurrent decision workers can
    # observe the same remaining budget, so at most MAX_CONCURRENT_LLM stale
    # score calls can overshoot external LM spend; late scores are ignored by
    # the engine rather than mutating belief state past the cap.
    quality = run_belief_pitch(
        data.get("agent_name"), listener, belief, pitch.get("pitch"),
        (data.get("relationships") or {}).get(listener, "neutral"),
        nearby_beliefs.get(listener) or [], data.get("frame_tick"),
    )
    if quality is not None:
        decision["belief_pitch_quality"] = quality
        decision["belief_pitch_scored"] = True
    return decision


# Every *_rejection_note key normalize_decision can stamp onto a fallback
# decision (see its rejection branches) -- checked in this order by
# _rejection_feedback_text (Phase 5) to build the decision retry's concrete
# feedback line. Kept as one tuple so a future rejection-note key only needs
# adding here, not at every call site.
_REJECTION_NOTE_KEYS = (
    "sprite_rejection_note",
    "council_rejection_note",
    "terraform_rejection_note",
    "upgrade_rejection_note",
    "contract_rejection_note",
    "rejection_note",
)


def _rejection_feedback_text(decision):
    """Pull the concrete reason off a normalize_decision fallback (Phase 5
    decision retry), preferring whichever *_rejection_note key is present --
    see _REJECTION_NOTE_KEYS. Returns None if the fallback carries no note
    (e.g. an invalid talk_to_nearby, which is redirected without a note)."""
    if not isinstance(decision, dict):
        return None
    for key in _REJECTION_NOTE_KEYS:
        note = decision.get(key)
        if note:
            return str(note)
    return None


def _truncation_retry_feedback(sprite_turn):
    """Shared wording for the Phase 5 decision retry whenever the prior
    response's done_reason was "length" (Ollama's max_tokens cut generation
    off mid-object -- the Phase 0 probe's dominant real-world failure mode).
    Used at BOTH retry trigger points in run_agent_decision, factored here so
    they cannot drift into two different strings: extract_json_decision's
    truncated-JSON salvager (deliberately NOT changed by this fix -- it is
    load-bearing elsewhere) can turn a cut-off reply into a
    syntactically-parseable-but-incomplete decision that skips the
    unparseable-JSON trigger point and lands on the _fallback trigger point
    instead (e.g. a sprite turn missing its grid, rejected by
    normalize_decision), so both points need this exact message rather than
    the bare *_rejection_note, which would otherwise read as a
    malformed-answer complaint and prompt the model to re-send another
    oversized reply.

    A DECISION_SCHEMA grammar bound on sprite.grid (Phase 4) already makes
    runaway sprite grids far rarer at generation time; this retry wording is
    the backstop for when that bound doesn't apply (e.g. structured output
    disabled session-wide after a format-degrade -- see
    _structured_output_enabled)."""
    grid_hint = " (for example, a smaller sprite grid)" if sprite_turn else ""
    return (
        "your previous reply was cut off before it finished (it ran out of "
        f"output space); reply with a smaller, more compact JSON object{grid_hint}."
    )


def run_agent_decision(data):
    """Build the prompt, call Ollama, and return a validated decision dict.

    Shared by the HTTP /agent/think endpoint and the server-authoritative
    SimEngine's think worker. Returns a plain dict (already normalized) — on any
    failure it returns either an {"error": ...} dict (engine maps these to its
    offline/compute/rest paths) or a role fallback decision."""
    try:
        # Resolve is_high_stakes_turn (and consume the extra-thinking budget,
        # if applicable) exactly once for this request. build_decision_payload
        # (model_for_decision, THINKING_SAMPLING) and the timeout choice below
        # both call is_high_stakes_turn(), which will echo this stamped value
        # instead of re-evaluating -- see resolve_high_stakes()'s docstring.
        high_stakes_active, high_stakes_capped = resolve_high_stakes(data)
        self_prompt = (data.get("self_prompt") or "").strip()
        response_format = build_response_format(require_divine_response=bool(data.get("voice_guidance_active")))
        payload = build_decision_payload(data, self_prompt, response_format)
        request_timeout = THINKING_TIMEOUT_S if is_high_stakes_turn(data) else DEFAULT_TIMEOUT_S

        known_resources = data.get("known_resources") or []
        pending_blueprints = data.get("pending_blueprints") or []
        approved_custom_projects = data.get("approved_custom_projects") or []
        rejected_blueprints = data.get("rejected_blueprints") or []
        nearby_formatted = format_nearby_agents(data.get("nearby_agents"))

        agent_name = data.get("agent_name")
        frame_tick = data.get("frame_tick")
        agent_data = build_agent_data(
            data, nearby_formatted, known_resources, pending_blueprints,
            approved_custom_projects, rejected_blueprints,
        )

        def log_lm(latency_ms, response=None, http_status=None, decision=None, error=None,
                   fallback_extra=None):
            # Measure the payload actually sent -- `payload` is reassigned in
            # place if the context-overflow retry (or, Phase 5, the
            # answer-quality decision retry) swaps in a different payload, so
            # reading it here (not capturing sizes earlier) reflects that.
            # decision_retries is read the same way (by name, at call time)
            # so it's always the value as-of whenever log_lm actually runs --
            # 0 on the common path, 1 once the Phase 5 retry has fired.
            messages = payload.get("messages") or []
            system_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "system")
            prompt_chars = sum(len(m.get("content") or "") for m in messages if m.get("role") == "user")
            record = {
                "agent_name": agent_name,
                "frame_tick": frame_tick,
                "latency_ms": latency_ms,
                "invention_only": bool(data.get("invention_only")),
                "sprite_design_only": bool(data.get("sprite_design_only")),
                "high_stakes_reason": data.get("high_stakes_reason"),
                "high_stakes_active": high_stakes_active,
                "high_stakes_capped": high_stakes_capped,
                "prompt_chars": prompt_chars,
                "system_chars": system_chars,
                "nudges_total": data.get("nudges_total"),
                "nudges_dropped": data.get("nudges_dropped"),
                "decision_retries": decision_retries,
                "request": payload,
                "response": response,
                "http_status": http_status,
                "decision": decision,
                "error": error,
            }
            # Phase 6 (Fix 5): fallback_extra is populated when
            # bad_response_fallback runs (with FALLBACK_AI_CHOICE_ENABLED on)
            # or when normalize_decision() returns a terminal _fallback
            # decision -- every ordinary turn's log_lm call leaves this None,
            # so these keys are simply absent from the record rather than
            # present with null/false values.
            if fallback_extra:
                record.update(fallback_extra)
            session_logger.log_lm_exchange(record)

        def bad_response_fallback(latency_ms, response=None, http_status=None, error="bad_response"):
            fallback, fallback_extra = _terminal_fallback(agent_data.get("role"), agent_data)
            log_lm(latency_ms, response=response, http_status=http_status,
                   decision=fallback, error=error, fallback_extra=fallback_extra)
            return fallback

        global _structured_output_enabled, _model_routing_enabled

        # Ollama does not cancel server-side generation when a client
        # aborts/times out a stream:false request (Phase 0 operational
        # finding, ollama_config.md) -- an orphaned timed-out request keeps
        # consuming a queue slot. Do not add a retry-on-timeout loop here;
        # every requests.exceptions.RequestException path below (including
        # Timeout) returns immediately instead of firing a second request.
        def _request_error_tag(exc):
            if isinstance(exc, requests.exceptions.Timeout):
                return "llm timeout"
            return "llm offline"

        # Every requests.post(OLLAMA_CHAT_URL, ...) in this turn (initial call,
        # format-degrade retry, context-overflow slim retry, answer-quality
        # retry, terminal fallback AI-choice call, and any future
        # call site) routes through here so a single run_agent_decision
        # invocation can never fire more than LLM_CALLS_PER_TURN_MAX requests
        # -- llm_calls_made is local to this invocation, not shared/global.
        # Raises LLMBudgetExhausted (never a RequestException) once the
        # budget is spent so callers can refuse distinguishably from a real
        # network failure and take their existing fallback path.
        llm_calls_made = 0

        def _post_ollama(body, timeout):
            nonlocal llm_calls_made
            if llm_calls_made >= LLM_CALLS_PER_TURN_MAX:
                raise LLMBudgetExhausted()
            llm_calls_made += 1
            return requests.post(OLLAMA_CHAT_URL, json=body, timeout=timeout)

        # Phase 6 (Fix 5, FALLBACK_AI_CHOICE_ENABLED): reached only once Fix
        # 4's retry is exhausted on an ANSWER-QUALITY failure, from either of
        # two call sites -- bad_response_fallback (no usable decision at all:
        # unparseable JSON, a missing `message` key, or a non-recoverable
        # error body) or the post-normalize path where normalize_decision()
        # returned a terminal _fallback-stamped decision. Every network-level
        # failure (llm offline/timeout, compute_error, model_not_found, llm
        # budget exhausted) returns directly from its own call site above
        # without ever reaching either site, so this can never fire
        # for those. Returns (decision, extra_log_fields); extra_log_fields
        # is {} when the flag is off, so log_lm emits nothing extra and
        # behavior is byte-identical to pre-Phase-6.
        def _terminal_fallback(role, agent_data):
            if not FALLBACK_AI_CHOICE_ENABLED:
                return role_fallback_action(role, agent_data), {}

            candidates = role_fallback_candidates(role, agent_data, limit=3)
            if not candidates:
                # Unreachable in practice (the ladder's final branch always
                # yields a candidate) -- fall back to the plain ladder call
                # rather than ever returning no decision.
                decision = role_fallback_action(role, agent_data)
                return decision, {
                    "fallback_triggered": True,
                    "fallback_candidate_count": 0,
                    "fallback_selection_method": "priority_default",
                    "fallback_candidates": [],
                }

            extra = {
                "fallback_triggered": True,
                "fallback_candidate_count": len(candidates),
                "fallback_candidates": [
                    {"action": c.get("action"), "target": c.get("target")} for c in candidates
                ],
            }

            if len(candidates) < 2 or llm_calls_made >= LLM_CALLS_PER_TURN_MAX:
                # Only one safe option, or no budget left to ask -- the
                # highest-priority candidate is the answer either way; no
                # call is made, so no fallback_ai_latency_ms is logged.
                chosen = candidates[0]
                extra["fallback_selection_method"] = (
                    "single_candidate" if len(candidates) < 2 else "priority_default"
                )
                chosen["_fallback"] = True
                return chosen, extra

            # >=2 candidates and budget remains: ask the fast model to pick
            # one letter. Minimal prompt, minimal tokens -- this is a
            # tiebreak, not a decision turn -- and never retried; any
            # failure/timeout/unparseable reply below falls back to the
            # first (highest-priority) candidate.
            labels = ["A", "B", "C", "D", "E"][:len(candidates)]
            option_lines = []
            for label, cand in zip(labels, candidates):
                target = cand.get("target")
                option_lines.append(
                    f"{label}: {cand.get('action')}" + (f" -> {target}" if target else "")
                )
            choice_prompt = (
                "Pick the best next action for your villager from these safe options:\n"
                + "\n".join(option_lines)
                + f"\nReply with a single letter ({', '.join(labels)}) and nothing else."
            )
            choice_payload = {
                "model": MODEL_FAST,
                "messages": [
                    {"role": "system",
                     "content": "You choose one lettered option. Reply with only the letter."},
                    {"role": "user", "content": choice_prompt},
                ],
                "max_tokens": 5,
                "temperature": 0.2,
                **NON_THINKING_SAMPLING,
            }
            if DISABLE_THINKING_ROUTINE:
                choice_payload["think"] = False

            choice_start = datetime.now()
            try:
                choice_resp = _post_ollama(to_ollama_body(choice_payload), FALLBACK_AI_CHOICE_TIMEOUT_S)
            except (LLMBudgetExhausted, requests.exceptions.RequestException):
                extra["fallback_ai_latency_ms"] = int(
                    (datetime.now() - choice_start).total_seconds() * 1000)
                extra["fallback_selection_method"] = "priority_default"
                chosen = candidates[0]
                chosen["_fallback"] = True
                return chosen, extra
            extra["fallback_ai_latency_ms"] = int((datetime.now() - choice_start).total_seconds() * 1000)

            picked = None
            try:
                choice_body = choice_resp.json()
            except ValueError:
                choice_body = None
            choice_text = lm_message_text(choice_body.get("message")) if isinstance(choice_body, dict) else ""
            first_char = (choice_text or "").strip()[:1].upper()
            if first_char in labels:
                picked = candidates[labels.index(first_char)]

            if picked is None:
                extra["fallback_selection_method"] = "priority_default"
                chosen = candidates[0]
            else:
                extra["fallback_selection_method"] = "ai_choice"
                chosen = picked
            chosen["_fallback"] = True
            return chosen, extra

        # Phase 5 (DECISION_RETRY_ENABLED): 0 on the common path, set to 1 the
        # moment (and only once) an answer-quality retry fires -- guards both
        # retry trigger points below so a turn can retry for unparseable JSON
        # OR for a _fallback-stamped decision, never both. log_lm reads this
        # by name (see its docstring), so it doesn't need threading through
        # every log_lm() call site.
        decision_retries = 0

        def _decision_retry(feedback_text, slim_used):
            """POST the single answer-quality retry through _post_ollama (so
            it spends from the same LLM_CALLS_PER_TURN_MAX budget as every
            other call site) with feedback_text folded into the retry's user
            prompt via build_decision_payload's retry_feedback kwarg. Returns
            (lm_body, http_status, decision) where decision is None if the
            retry itself came back unparseable/erroring -- callers then take
            the existing fallback path per spec (no second retry). Deliberately
            does NOT catch LLMBudgetExhausted / requests.exceptions.RequestException
            -- those propagate to the caller, which handles them exactly like
            every other call site in this function (immediate {"error": ...}
            return, no fallback, no further retry)."""
            nonlocal payload
            retry_payload = build_decision_payload(
                data, self_prompt, response_format, slim=slim_used, retry_feedback=feedback_text)
            payload = retry_payload
            resp = _post_ollama(to_ollama_body(retry_payload), request_timeout)
            http_status2 = resp.status_code
            try:
                lm_body2 = resp.json()
            except ValueError:
                lm_body2 = None
            decision2 = None
            if not (lm_body2 is None or (isinstance(lm_body2, dict) and lm_body2.get("error"))):
                try:
                    message2 = lm_body2["message"]
                except (TypeError, KeyError):
                    message2 = None
                if message2 is not None:
                    if isinstance(message2, dict):
                        message2.setdefault("reasoning_content", "")
                    raw_text2 = lm_message_text(message2)
                    decision2 = extract_json_decision(raw_text2)
                    if not decision2 and isinstance(message2, dict):
                        decision2 = extract_json_decision(message2.get("content") or "")
            return lm_body2, http_status2, decision2

        start = datetime.now()
        try:
            resp = _post_ollama(to_ollama_body(payload), request_timeout)
        except LLMBudgetExhausted:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            log_lm(latency_ms, error="llm budget exhausted")
            return {"error": "llm budget exhausted", "action": "rest"}
        except requests.exceptions.RequestException as exc:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            err = _request_error_tag(exc)
            log_lm(latency_ms, error=err)
            return {"error": err, "action": "rest"}

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)
        http_status = resp.status_code

        try:
            lm_body = resp.json()
        except ValueError:
            lm_body = None

        # Auto-degrade: if Ollama rejected the `format` (JSON-schema) field,
        # disable structured output for the session and retry once so this
        # turn still succeeds. Ollama's format param is stable (Phase 0
        # finding #2) so this is expected to fire rarely, but the safety net
        # stays in place.
        if ("response_format" in payload and _structured_output_enabled
                and looks_like_response_format_error(http_status, lm_body)):
            print("[server] Ollama rejected the format/json-schema field; disabling "
                  "structured output for this session and retrying without it.")
            _structured_output_enabled = False
            payload.pop("response_format", None)
            response_format = None
            start = datetime.now()
            try:
                resp = _post_ollama(to_ollama_body(payload), request_timeout)
            except LLMBudgetExhausted:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

        # A missing routed model id is a setup failure, not a transient
        # condition -- Ollama has no LM-Studio-style "local-model" alias to
        # fall back to. Log once per session and return the offline fallback
        # rather than retrying (see scripts/ollama_setup.py).
        if looks_like_model_not_found_error(http_status, lm_body):
            if _model_routing_enabled:
                print(f"[server] Ollama doesn't have model {payload.get('model')!r} "
                      f"created/loaded -- this is a setup failure. Run "
                      f"`uv run python scripts/ollama_setup.py` then restart the server.")
                _model_routing_enabled = False
            log_lm(latency_ms, response=lm_body, http_status=http_status, error="model_not_found")
            return {"error": "llm offline", "action": "rest"}

        if lm_body is None:
            return bad_response_fallback(latency_ms, http_status=http_status)

        # error_kind tags the whole call for logging once at the end (below),
        # even if the context-overflow retry ultimately recovers a decision --
        # this is what makes context_overflow measurable/distinguishable in
        # llm.jsonl per the plan, without double-logging each attempt.
        error_kind = None
        if isinstance(lm_body, dict) and lm_body.get("error"):
            err_text, err_type = _ollama_error_parts(lm_body)
            if "compute error" in err_text.lower():
                log_lm(latency_ms, response=lm_body, http_status=http_status, error="compute_error")
                return {"error": "compute_error", "action": "rest"}
            if not is_context_overflow_error(err_text, err_type):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

            # Retry ONCE with a slimmed-down payload: no memory line, no
            # recent conversations, no worked examples. Rules + JSON schema
            # are kept so response_format still shapes the output. On any
            # further failure this falls through to the normal handling below
            # (which will hit bad_response_fallback), so there is no loop.
            error_kind = "context_overflow"
            slim_payload = build_decision_payload(data, self_prompt, response_format, slim=True)
            payload = slim_payload
            retry_start = datetime.now()
            try:
                resp = _post_ollama(to_ollama_body(slim_payload), request_timeout)
            except LLMBudgetExhausted:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

            if lm_body is None:
                return bad_response_fallback(latency_ms, http_status=http_status, error=error_kind)
            if isinstance(lm_body, dict) and lm_body.get("error"):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status, error=error_kind)

        try:
            message = lm_body["message"]
        except (TypeError, KeyError):
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                          error=error_kind or "bad_response")
        if isinstance(message, dict):
            # No reasoning_content channel exists in Ollama's response shape
            # (that was purely an LM Studio quirk) -- keep the key present as
            # an empty string in the logged response for log-shape
            # compatibility with any downstream tooling that still reads it
            # (e.g. scripts/llm_replay_bench.py's thinking-leak analysis,
            # ported to the new shape in Phase 4).
            message.setdefault("reasoning_content", "")

        raw_text = lm_message_text(message)
        decision = extract_json_decision(raw_text)
        if not decision and isinstance(message, dict):
            decision = extract_json_decision(message.get("content") or "")
        if not decision:
            # Phase 5, failure point 1/2: unparseable JSON. Retry once
            # (budget permitting) with a concrete-reason feedback line before
            # falling back -- see DECISION_RETRY_ENABLED's docstring for why
            # this never covers network-level failures.
            if DECISION_RETRY_ENABLED and decision_retries == 0:
                decision_retries = 1
                # Phase 0 probe finding (see the plan): the dominant real
                # cause of unparseable replies is generation truncation --
                # Ollama's done_reason == "length" means max_tokens cut the
                # JSON off mid-object, not a malformed answer. A generic
                # "could not be parsed" message would mislead the model into
                # re-emitting the same oversized answer, so call that out by
                # name and ask for something smaller instead.
                done_reason = lm_body.get("done_reason") if isinstance(lm_body, dict) else None
                if done_reason == "length":
                    feedback_text = _truncation_retry_feedback(data.get("sprite_design_only"))
                else:
                    feedback_text = (
                        "your previous reply could not be parsed as JSON; reply with only "
                        "the JSON decision object."
                    )
                retry_start = datetime.now()
                try:
                    lm_body, http_status, decision = _decision_retry(
                        feedback_text, slim_used=(error_kind == "context_overflow"))
                except LLMBudgetExhausted:
                    latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                    log_lm(latency_ms, error="llm budget exhausted")
                    return {"error": "llm budget exhausted", "action": "rest"}
                except requests.exceptions.RequestException as exc:
                    latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                    err = _request_error_tag(exc)
                    log_lm(latency_ms, error=err)
                    return {"error": err, "action": "rest"}
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                if not decision:
                    # Second attempt also failed -- existing fallback path,
                    # no further retry.
                    return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                                  error=error_kind or "bad_response")
            else:
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                              error=error_kind or "bad_response")

        # Held in its own local (rather than nested inline) so it can be
        # inspected for the _fallback stamp (role_fallback_action) before the
        # belief-pitch scoring / divine-response synthesis passes run over it,
        # and so failure point 2/2 (Phase 5) below can trigger the same
        # single answer-quality retry when normalize_decision rejects it.
        normalized_decision = normalize_decision(decision, agent_data)
        if (DECISION_RETRY_ENABLED and decision_retries == 0
                and isinstance(normalized_decision, dict) and normalized_decision.get("_fallback")):
            decision_retries = 1
            # Bug fix (found in Phase 5 verification): extract_json_decision's
            # truncated-JSON salvager (see _truncation_retry_feedback's
            # docstring) can turn a done_reason == "length" reply into a
            # decision that parses fine but is missing a required field --
            # e.g. a sprite turn whose grid got cut off -- which skips the
            # unparseable-JSON trigger point above and lands here instead,
            # rejected by normalize_decision. lm_body has NOT been reassigned
            # yet at this point (this whole block only runs when
            # decision_retries was still 0, i.e. the unparseable-JSON retry
            # never fired), so it is still the ORIGINAL response -- reading
            # its done_reason here is safe. When that response was truncated,
            # lead with the same truncation wording as the other trigger
            # point instead of the bare *_rejection_note (which reads as a
            # malformed-answer complaint and would prompt the model to
            # re-send another oversized reply); keep the note as supporting
            # detail since it still names which field was lost.
            done_reason = lm_body.get("done_reason") if isinstance(lm_body, dict) else None
            rejection_note = _rejection_feedback_text(normalized_decision)
            if done_reason == "length":
                feedback_text = _truncation_retry_feedback(data.get("sprite_design_only"))
                if rejection_note:
                    feedback_text = f"{feedback_text} ({rejection_note})"
            else:
                feedback_text = rejection_note or (
                    "your previous reply was valid JSON but was rejected during "
                    "validation; reply with a decision matching the required schema."
                )
            retry_start = datetime.now()
            try:
                lm_body, http_status, retry_decision = _decision_retry(
                    feedback_text, slim_used=(error_kind == "context_overflow"))
            except LLMBudgetExhausted:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                log_lm(latency_ms, error="llm budget exhausted")
                return {"error": "llm budget exhausted", "action": "rest"}
            except requests.exceptions.RequestException as exc:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                err = _request_error_tag(exc)
                log_lm(latency_ms, error=err)
                return {"error": err, "action": "rest"}
            latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
            if retry_decision:
                normalized_decision = normalize_decision(retry_decision, agent_data)
            # else: retry itself came back unparseable/erroring -- the
            # existing fallback path IS normalized_decision as already
            # computed above (already a role_fallback_action dict), so there
            # is nothing further to do; fall through and log/return it.

        # Phase 6 (Fix 5): this is the motivating case for the whole plan --
        # normalize_decision rejected a syntactically valid decision and
        # Fix 4's retry is exhausted, whether it fired just above and still
        # landed on a fallback, or never fired at all (DECISION_RETRY_ENABLED
        # off, or decision_retries already spent by the unparseable-JSON
        # failure point earlier in this function). Never reached for a
        # network failure -- this whole function body only gets here once
        # Ollama has actually responded with something normalize_decision
        # could evaluate. With FALLBACK_AI_CHOICE_ENABLED off this block is
        # skipped entirely, so normalized_decision (and its baked
        # *_rejection_note/reasoning) is returned exactly as
        # normalize_decision produced it -- byte-identical to pre-Phase-6
        # behavior, no fallback_* log fields, no extra call.
        fallback_extra = None
        if (FALLBACK_AI_CHOICE_ENABLED and isinstance(normalized_decision, dict)
                and normalized_decision.get("_fallback")):
            # Carry the specific *_rejection_note/reasoning normalize_decision
            # already baked onto the rejected fallback (this is what makes
            # llm.jsonl greppable and feeds the cross-turn behavior_nudge --
            # see _rejection_feedback_text/_REJECTION_NOTE_KEYS) onto
            # whatever _terminal_fallback returns, so swapping in the
            # AI-choice tiebreak doesn't silently drop that diagnostic.
            carried_notes = {
                key: normalized_decision[key]
                for key in _REJECTION_NOTE_KEYS if normalized_decision.get(key)
            }
            carried_reasoning = normalized_decision.get("reasoning")
            normalized_decision, fallback_extra = _terminal_fallback(agent_data.get("role"), agent_data)
            if carried_notes:
                normalized_decision.update(carried_notes)
            if carried_reasoning:
                normalized_decision["reasoning"] = carried_reasoning

        decision = synthesize_divine_response(
            score_belief_pitch_decision(normalized_decision, data),
            agent_data,
        )

        log_lm(latency_ms, response=lm_body, http_status=http_status, decision=decision, error=error_kind,
               fallback_extra=fallback_extra)
        return decision

    except Exception:
        return {"error": "server_error", "action": "rest"}


@app.route("/agent/think", methods=["POST"])
def agent_think():
    """Legacy HTTP think endpoint. Now unused by the server-authoritative
    engine (which calls run_agent_decision directly), but kept functional."""
    data = request.get_json(force=True) or {}
    return jsonify(run_agent_decision(data))


# --- Server-authoritative SimEngine wiring (Phases 2-6) ---
# AVAILABLE_ACTIONS: the full action superset the engine advertises to the model
# (mirrors AVAILABLE_ACTIONS in index.html). normalize_decision still filters.
# Reuses the module-level lm_complete() and the MemoryStore `memory_store`
# instance already defined above.
AVAILABLE_ACTIONS = list(DECISION_ACTIONS)

# Import the engine module whether server.py is run as a script (cwd-relative)
# or imported as simulation.server (package-relative).
import sys as _sys  # noqa: E402
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_engine as _sim_engine  # noqa: E402

# Phase D (TECH_TREE_ENABLED) prompt/schema amendments, applied only when the
# engine flag is on so that flag-off prompts and request payloads stay
# byte-identical to Phase C. The blueprint schema line teaches the optional
# "tier" field; the "verdict" decision field carries the elder's comparative
# council judgment (approve-the-best + reject-the-rest-with-reasons in one
# decision -- the engine's COUNCIL VERDICT nudge explains when to use it).
# Bug fix (found by the Phase D live smoke): SEED_PROJECT_IDS was a hardcoded
# subset that never included the granary (or now the forge), so a blueprint
# with id "granary" validated and its approval OVERWROTE the seed registry
# entry (cheaper needs, wrong tier). Protect every seed template id.
SEED_PROJECT_IDS.update(_sim_engine.PROJECT_TEMPLATES.keys())

if _sim_engine.TECH_TREE_ENABLED:
    DECISION_SCHEMA["properties"]["verdict"] = {
        "type": ["object", "null"],
        "properties": {"rejections": {"type": "object"}},
    }
    DECISION_SCHEMA["properties"]["blueprint"]["properties"]["tier"] = {
        "type": ["integer", "null"],
    }
    # The corresponding SYSTEM_PROMPT/SYSTEM_PROMPT_SLIM rewrite (documenting
    # the optional "tier" field) now lives in prompts.py, applied at that
    # module's import time (which happens before this point, near the top of
    # this file) -- see prompts.py for the rewrite + its own startup sha256
    # print. Nothing to do here except the DECISION_SCHEMA amendments above.


def _llm_decide(payload):
    """Engine -> LM bridge: run the existing decision pipeline + log it."""
    return run_agent_decision(payload)


_ENGINE_DEPS = {
    "ROLES": ROLES,
    "ROLE_PROJECT": ROLE_PROJECT,
    "ROLE_SKILLS": {role: d.get("skill", "helps the village") for role, d in ROLES.items()},
    "ROLE_PRIMARY_RESOURCE": ROLE_PRIMARY_RESOURCE,
    "RESOURCE_GATHER_ROLES": RESOURCE_GATHER_ROLES,
    "AVAILABLE_ACTIONS": AVAILABLE_ACTIONS,
    "SLUG_RE": SLUG_RE,
    "llm_decide": _llm_decide,
    "lm_complete": lm_complete,
    "is_scaffold_text": is_scaffold_text,
    "memory_store": memory_store,
    "log_activity": session_logger.log_activity,
    "log_conversation": session_logger.log_conversation,
    "log_benchmark": session_logger.log_benchmark,
    "flush_benchmarks": session_logger.flush_benchmarks,
    "log_divine": session_logger.log_divine,
    "log_compiler": session_logger.log_compiler,
    "validate_blueprint": validate_blueprint,
    "validate_sprite_block": validate_sprite_block,
    "sprite_spec_is_degenerate": sprite_spec_is_degenerate,
    "SPRITE_GRID_MIN": SPRITE_GRID_MIN,
    "SPRITE_GRID_MAX": SPRITE_GRID_MAX,
    "canonical_effect_vector": canonical_effect_vector,
    "run_piano_module": run_piano_module,
    "run_meta_update": run_meta_update,
    "normalize_decision": normalize_decision,
    "synthesize_divine_response": synthesize_divine_response,
    "build_agent_data": build_agent_data,
}

_roster_env = os.environ.get("SIM_AGENTS")
try:
    _roster_size = int(_roster_env) if _roster_env else 8
except ValueError:
    _roster_size = 8

engine = _sim_engine.SimEngine(_ENGINE_DEPS, roster_size=_roster_size)

# Full-state resume (Contract 3): if a valid state.db exists, rehydrate the
# world (frameTick, civilization, agents, re-embedded memory) instead of using
# the cold-start roster the constructor just built. Otherwise keep cold start.
if engine.restore_state():
    print(f"[server] resumed from state.db @ frameTick={engine.frameTick} "
          f"(level {engine.civilization['level']}, "
          f"{len(engine.civilization['structures'])} structures, "
          f"memory {memory_store.size()})")
else:
    print("[server] cold start (no valid state.db)")


# Per-file (min_frame, max_frame) for llm.jsonl — keyed by absolute path,
# invalidated by mtime+size so /council-llm-log can skip out-of-range files
# without a full council-filter parse.
_COUNCIL_LLM_FRAME_BOUNDS_CACHE = {}


def _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame):
    try:
        st = os.stat(path)
    except OSError:
        return
    _COUNCIL_LLM_FRAME_BOUNDS_CACHE[path] = {
        "mtime": st.st_mtime,
        "size": st.st_size,
        "min_frame": min_frame,
        "max_frame": max_frame,
    }


def _llm_jsonl_frame_bounds(path):
    """Return (min_frame, max_frame) for type=llm records, or (None, None)."""
    if not os.path.isfile(path):
        return None, None
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    cached = _COUNCIL_LLM_FRAME_BOUNDS_CACHE.get(path)
    if (cached
            and cached["mtime"] == st.st_mtime
            and cached["size"] == st.st_size):
        return cached["min_frame"], cached["max_frame"]
    min_frame, max_frame = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm":
                    continue
                ft = rec.get("frame_tick")
                if ft is None:
                    continue
                if min_frame is None or ft < min_frame:
                    min_frame = ft
                if max_frame is None or ft > max_frame:
                    max_frame = ft
    except OSError:
        return None, None
    _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame)
    return min_frame, max_frame


def _llm_frame_window_fully_covered(min_frame, max_frame, start_frame, end_frame):
    if min_frame is None or max_frame is None:
        return False
    return min_frame <= start_frame and end_frame <= max_frame


def _council_llm_entries_from_file(path, start_frame, end_frame, agent_set):
    """Return slim decision records from one llm.jsonl matching a council
    frame window. Shared by /council-llm-log -- see that route's docstring
    for the filtering rules."""
    entries, _, _ = _scan_council_llm_file(path, start_frame, end_frame, agent_set)
    return entries


def _scan_council_llm_file(path, start_frame, end_frame, agent_set):
    """One pass over llm.jsonl: council-filtered entries plus file bounds."""
    if not os.path.isfile(path):
        return [], None, None
    entries = []
    min_frame, max_frame = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "llm":
                    continue
                ft = rec.get("frame_tick")
                if ft is not None:
                    if min_frame is None or ft < min_frame:
                        min_frame = ft
                    if max_frame is None or ft > max_frame:
                        max_frame = ft
                if ft is None or ft < start_frame or ft > end_frame:
                    continue
                name = rec.get("agent_name")
                if agent_set and name not in agent_set:
                    continue
                decision = rec.get("decision") or {}
                action = decision.get("action")
                invention_only = bool(rec.get("invention_only"))
                if not invention_only:
                    req = rec.get("request") or {}
                    for msg in req.get("messages") or []:
                        content = (msg.get("content") or "").lower()
                        if "invention-only" in content or "propose a new structure blueprint" in content:
                            invention_only = True
                            break
                is_verdict = isinstance(decision.get("verdict"), dict)
                if (action not in COUNCIL_LLM_ACTIONS
                        and not invention_only
                        and not is_verdict):
                    continue
                slim_decision = {
                    "action": action,
                    "reasoning": decision.get("reasoning"),
                    "message": decision.get("message"),
                    "verdict": decision.get("verdict"),
                    "blueprint_name": (decision.get("blueprint") or {}).get("name"),
                }
                entries.append({
                    "agent_name": name,
                    "frame_tick": ft,
                    "ts": rec.get("ts"),
                    "latency_ms": rec.get("latency_ms"),
                    "invention_only": invention_only,
                    "decision": slim_decision,
                    "error": rec.get("error"),
                })
    except OSError:
        return [], None, None
    _cache_llm_jsonl_frame_bounds(path, min_frame, max_frame)
    return entries, min_frame, max_frame


@app.route("/council-llm-log")
def council_llm_log():
    """Return slim decision records (llm.jsonl) for a council frame window.

    Only blueprint-pitch and verdict turns are included — routine gather/talk
    decisions from the same agents during the council window are omitted.

    Scans the live session's llm.jsonl first; only reads older retained
    session directories when the requested frame window is not fully covered
    by the live file's frame range (frame_tick is monotonic across restarts,
    but each session only spans the frames recorded while that server run was
    alive). Out-of-range files are skipped using cached per-file bounds when
    possible. Session dirs are pruned by SessionLogger._prune_old_sessions to
    LOG_RETENTION_SESSIONS newest."""
    try:
        start_frame = int(request.args.get("start_frame", 0))
        end_frame = int(request.args.get("end_frame", 0))
    except (TypeError, ValueError):
        return jsonify({"entries": [], "error": "invalid frame range"}), 400
    agents_raw = request.args.get("agents") or ""
    agent_set = {a.strip() for a in agents_raw.split(",") if a.strip()}
    logs_root = os.path.dirname(session_logger.dir)
    live_path = session_logger.llm_path
    entries, live_min, live_max = _scan_council_llm_file(
        live_path, start_frame, end_frame, agent_set)
    if not _llm_frame_window_fully_covered(
            live_min, live_max, start_frame, end_frame):
        try:
            session_dirs = sorted(
                name for name in os.listdir(logs_root)
                if SESSION_DIR_RE.match(name)
                and os.path.isdir(os.path.join(logs_root, name))
            )  # ISO session-id names sort lexicographically == chronologically
        except OSError:
            session_dirs = []
        for name in session_dirs:
            if name == session_logger.session_id:
                continue
            path = os.path.join(logs_root, name, "llm.jsonl")
            fmin, fmax = _llm_jsonl_frame_bounds(path)
            if fmin is not None and fmax is not None:
                if fmax < start_frame or fmin > end_frame:
                    continue
            entries.extend(_council_llm_entries_from_file(
                path, start_frame, end_frame, agent_set))
    entries.sort(key=lambda e: e.get("frame_tick") or 0)
    return jsonify({"entries": entries})


@app.route("/anomalies")
def anomalies():
    """Anomaly radar (idea-07, docs/plans/idea-07-anomaly-radar/plan.md):
    read-only, server-side reader over the current run's benchmarks.jsonl,
    located via the existing session_logger reference. No live-engine or
    civilization["chronicle"] access, no self.lock -- see
    specs/04-http-api.md's "Anomaly radar (idea-07)" section."""
    if not _sim_engine.ANOMALY_RADAR_ENABLED:
        return jsonify({"ok": True, "enabled": False, "anomalies": []})
    found = compute_anomalies(session_logger.benchmark_path)
    return jsonify({"ok": True, "enabled": True, "anomalies": found})


@app.route("/state")
def state():
    """Consistent world snapshot for the thin viewer (Contract 2)."""
    since_raw = request.args.get("since")
    since = None
    if since_raw is not None:
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = None
    if since is None:
        return jsonify(engine.snapshot())
    return jsonify(engine.snapshot_delta(since))


@app.route("/districts.js")
def districts_js():
    """Live districts/roads for the viewer (world-expansion plan). Despite the
    ".js" name (matching the plan's route naming), the body is plain JSON;
    the viewer fetch()-polls it rather than re-injecting a <script> tag.

    Under the engine lock, read districtsEpoch and either return a tiny
    unchanged body when ?since= matches, or shallow-copy district/road data
    into plain dicts/lists. JSON assembly happens after the lock is released."""
    since_raw = request.args.get("since")
    since = None
    if since_raw is not None:
        try:
            since = int(since_raw)
        except (TypeError, ValueError):
            since = None

    with engine.lock:
        epoch = engine.districtsEpoch
        if since is not None and since == epoch:
            payload = {"unchanged": True, "epoch": epoch}
        else:
            c = engine.civilization
            districts = [
                {"id": did, "kind": d["kind"], "tile": d["tile"], "label": d.get("label"),
                 "bounds": dict(d["bounds"]),
                 "buildGrid": dict(d["build_grid"]) if d.get("build_grid") else None,
                 "tiles": dict(d.get("tiles") or {}),
                 "terrain": dict(d.get("terrain") or {}),
                 "settlementId": d.get("settlementId")}
                for did, d in c["districts"].items()
            ]
            road_nodes = {nid: dict(n) for nid, n in c["roadNodes"].items()}
            road_edges = [list(e) for e in c["roadEdges"]]
            payload = {
                "districts": districts,
                "roadNodes": road_nodes,
                "roadEdges": road_edges,
                "epoch": epoch,
            }
    return jsonify(payload)


@app.route("/control/pause", methods=["POST"])
def control_pause():
    engine.pause()
    return jsonify({"ok": True, "paused": True})


@app.route("/control/resume", methods=["POST"])
def control_resume():
    engine.resume()
    return jsonify({"ok": True, "paused": False})


_RESET_PASSWORD_RAW = os.environ.get("SIM_RESET_PASSWORD", "").strip()
RESET_PASSWORD = _RESET_PASSWORD_RAW if _RESET_PASSWORD_RAW else "reset"


@app.route("/control/reset", methods=["POST"])
def control_reset():
    body = request.get_json(force=True, silent=True) or {}
    supplied = body.get("password", "")
    if not isinstance(supplied, str):
        supplied = ""
    if not hmac.compare_digest(supplied, RESET_PASSWORD):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    agents = body.get("agents")
    try:
        agents = int(agents) if agents else None
    except (TypeError, ValueError):
        agents = None
    engine.reset(roster_size=agents)
    return jsonify({"ok": True, "agents": engine.roster_size})


# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 2) ---
# Two-gate model: GOD_MODE_ENABLED (sim_engine.py) is always required;
# GOD_AUTH_REQUIRED (sim_engine.py, default False) optionally requires a
# token. GOD_TOKEN is read ONCE at import here -- no route may change any of
# these, and there is no live on/off switch by design. Routes go live when the
# flag is on AND either auth is off OR a non-empty token is configured. A
# flag-on/auth-required/token-missing combination is a misconfiguration,
# reported once at startup WITHOUT the secret itself (there is none to reveal
# -- the token is simply absent), and every /control/god/* route stays
# disabled until a restart supplies one.
GOD_TOKEN = os.environ.get("SIM_GOD_TOKEN", "").strip()
GOD_ROUTES_ACTIVE = (
    _sim_engine.GOD_MODE_ENABLED
    and (bool(GOD_TOKEN) or not _sim_engine.GOD_AUTH_REQUIRED)
)
if _sim_engine.GOD_MODE_ENABLED and not _sim_engine.GOD_AUTH_REQUIRED:
    _god_bind_host = os.environ.get("SIM_HOST", "0.0.0.0")
    print("[server] SECURITY: God API is unauthenticated (SIM_GOD_AUTH off); "
          f"listening bind will be {_god_bind_host} — any LAN client can "
          "mutate the world via /control/god/*.")
elif _sim_engine.GOD_MODE_ENABLED and _sim_engine.GOD_AUTH_REQUIRED and not GOD_TOKEN:
    print("[server] WARNING: SIM_GOD_MODE is enabled but SIM_GOD_TOKEN is unset/blank -- "
          "every /control/god/* route stays disabled until a token is configured "
          "and the server is restarted.")

# A proclamation payload is small; this bounds request bodies well above any
# legitimate use while still capping abuse. Checked before request.get_json
# so an oversized body never reaches JSON parsing.
GOD_MAX_BODY_BYTES = 8192


def _god_authorized():
    """True when God routes are active and, if GOD_AUTH_REQUIRED, the
    request's X-God-Token header matches via constant-time comparison."""
    if not GOD_ROUTES_ACTIVE:
        return False
    if not _sim_engine.GOD_AUTH_REQUIRED:
        return True
    supplied = request.headers.get("X-God-Token", "")
    return hmac.compare_digest(supplied, GOD_TOKEN)


def _god_unauthorized_response():
    """ONE uniform shape for every authorization failure -- disabled flag,
    missing token config, missing header, and wrong token are all
    indistinguishable from the outside, and no God route ever reveals
    whether a target/event exists to an unauthorized caller."""
    return jsonify({"error": "unauthorized"}), 401


def _god_body_too_large():
    length = request.content_length
    return isinstance(length, int) and length > GOD_MAX_BODY_BYTES


@app.route("/control/god/capabilities", methods=["GET"])
def control_god_capabilities():
    if not _god_authorized():
        return _god_unauthorized_response()
    return jsonify({
        "ok": True,
        "godModeEnabled": _sim_engine.GOD_MODE_ENABLED,
        "tokenConfigured": bool(GOD_TOKEN),
        "kinds": {
            "proclamation": {
                "applyable": True,
                "payload": {"text": {"type": "string",
                                     "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                     "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES}},
                "reversibilityClass": "irreversible",
                "notes": (
                    "Chronicle/activity proclamation plus auto-applies as timed "
                    "providence (same text, default duration, replace slot)."
                ),
            },
            # Sovereign God mode Phase 3 (docs/archive/plan-sovereign-god-mode-v2.md
            # "Voice and providence").
            "providence": {
                "applyable": True,
                "payload": {
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            "private_omen": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 1: batch whisper campaign (private omens per target).
            "whisper_campaign": {
                "applyable": True,
                "payload": {
                    "theme": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                              "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "whispers": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_WHISPER_CAMPAIGN_MAX_TARGETS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "targetId": {"type": "integer"},
                                "text": {"type": "string",
                                         "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                         "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                            },
                        },
                    },
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 2: per-agent LLM sampling overlay (private).
            "agent_sampling": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "model": {"type": "string", "enum": list(_sim_engine.GOD_AGENT_SAMPLING_MODELS),
                              "optional": True, "default": "sim-smart"},
                    "temperature": {"type": "number",
                                    "min": _sim_engine.GOD_AGENT_SAMPLING_TEMP_MIN,
                                    "max": _sim_engine.GOD_AGENT_SAMPLING_TEMP_MAX},
                    "top_p": {"type": "number", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_TOP_P_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_TOP_P_MAX},
                    "top_k": {"type": "integer", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_TOP_K_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_TOP_K_MAX},
                    "min_p": {"type": "number", "optional": True,
                              "min": _sim_engine.GOD_AGENT_SAMPLING_MIN_P_MIN,
                              "max": _sim_engine.GOD_AGENT_SAMPLING_MIN_P_MAX},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": (
                    f"at most {_sim_engine.GOD_AGENT_SAMPLING_FAST_DECISION_CAP} living agent "
                    "may use sim-fast for decisions at once (PIANO pool contention)."
                ),
            },
            "revoke_agent_sampling": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 3: memory surgery (private, irreversible).
            "memory_insert": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "salience": {"type": "number", "min": 0.0, "max": 1.0,
                                 "optional": True, "default": 0.7},
                    "kind": {"type": "string", "optional": True,
                             "default": _sim_engine.GOD_MEMORY_DEFAULT_KIND,
                             "maxLen": _sim_engine.GOD_MEMORY_KIND_MAX_LEN},
                },
                "reversibilityClass": "irreversible",
                "notes": "never in public activity/chronicle; outcomes omit memory text.",
            },
            "memory_delete": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "keyword": {"type": "string", "optional": True},
                    "frameFrom": {"type": "integer", "optional": True},
                    "frameTo": {"type": "integer", "optional": True},
                    "kinds": {"type": "array", "optional": True, "items": {"type": "string"}},
                },
                "reversibilityClass": "irreversible",
                "notes": "at least one of keyword/frameFrom/frameTo/kinds required; "
                         "outcome is deletedCount only.",
            },
            "belief_plant": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "beliefId": {"type": "string", "optional": True},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES, "optional": True},
                    "plantInMemeTexts": {"type": "boolean"},
                    "salience": {"type": "number", "min": 0.0, "max": 1.0,
                                 "optional": True, "default": 0.7},
                },
                "reversibilityClass": "irreversible",
                "notes": "at least one of beliefId or text; never in public logs.",
            },
            # Divine Matrix Phase 4: reality distortion / context masks (private).
            "context_mask": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "mode": {"type": "string",
                             "enum": sorted(_sim_engine.GOD_CONTEXT_MASK_MODES)},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "dreamSnapshot": {
                        "type": "object", "optional": True,
                        "allowedKeys": sorted(_sim_engine.GOD_CONTEXT_MASK_DREAM_KEYS),
                        "notes": "required when mode=dream; unknown keys rejected",
                    },
                    "forgedConversations": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_CONTEXT_MASK_FORGED_MAX,
                        "notes": "required when mode=whisper_chain",
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": "mutates think payload only; cancel via god_cancel on mask id.",
            },
            # Divine Matrix Phase 5: decision gate / possession pipeline (private).
            "decision_compulsion": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "pinnedDecision": {"type": "object", "notes": "must include action; validated via normalize_decision"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES},
                    "remainingTurns": {"type": "integer", "optional": True, "min": 1},
                },
                "reversibilityClass": "cancellable",
                "notes": "at least one of durationFrames or remainingTurns required; cancel via god_cancel.",
            },
            "decision_veto_arm": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
            },
            "decision_veto_resolve": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "resolution": {"type": "string", "enum": ["approve", "reject", "rewrite"]},
                    "rewrittenDecision": {"type": "object", "optional": True},
                },
                "reversibilityClass": "irreversible",
            },
            "agent_possession": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "pinnedDecision": {"type": "object", "optional": True},
                    "queue": {"type": "array", "optional": True, "maxItems": 8},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "skips LLM when active; pinnedDecision or queue required.",
            },
            "revoke_decision_gate": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 6: Burning Bush + Merovingian Bargain (private).
            "burning_bush_message": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "text": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                             "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                },
                "reversibilityClass": "cancellable",
                "notes": "private thread; never in /state; Sight shows messageCount only.",
            },
            "burning_bush_close": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            "merovingian_bargain": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "termsText": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                  "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "successPredicate": {
                        "type": "object",
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PREDICATES),
                    },
                    "failurePredicate": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PREDICATES),
                    },
                    "rewardPrimitive": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PRIMITIVE_KINDS),
                    },
                    "punishPrimitive": {
                        "type": "object", "optional": True,
                        "kindEnum": sorted(_sim_engine.GOD_BARGAIN_PRIMITIVE_KINDS),
                    },
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "predicates allowlisted only; auto-settle on tick; grant rewards are public.",
            },
            "bargain_settle": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "outcome": {"type": "string", "enum": ["success", "failure"]},
                },
                "reversibilityClass": "irreversible",
                "notes": "manual settle; tick auto-settle is primary path.",
            },
            # Divine Matrix Phase 7: Anointed (destiny private, stigmata in neighbor prompts).
            "anoint": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "destinyText": {"type": "string",
                                    "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                    "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "stigmataTags": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_ANOINT_STIGMATA_MAX,
                        "items": {"type": "string",
                                  "maxChars": _sim_engine.GOD_ANOINT_STIGMATA_TAG_MAX_CHARS},
                    },
                    "oracleHints": {
                        "type": "array", "optional": True,
                        "maxItems": _sim_engine.GOD_ANOINT_ORACLE_HINTS_MAX,
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string",
                                         "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                         "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                                "revealFrame": {"type": "integer", "min": 0},
                            },
                        },
                    },
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "destiny/oracle private; stigmata in nearby-agent prompt only; "
                         "cancel via god_cancel or revoke_anoint.",
            },
            "revoke_anoint": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 8: Identity Forge (persona/personality/role).
            "identity_edit": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "persona": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_IDENTITY_PERSONA_MAX_CHARS,
                    },
                    "personality": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_IDENTITY_PERSONALITY_MAX_CHARS,
                        "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES,
                    },
                    "role": {"type": "string", "optional": True,
                             "description": "must exist in roles.json"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "timed edits restore snapshot on expiry/cancel; permanent edits are consequential.",
            },
            "identity_copy_overwrite": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "sourceId": {"type": "integer"},
                    "ratePerThink": {"type": "number", "min": 0.0, "max": 1.0},
                    "syncMemories": {"type": "boolean", "optional": True, "default": False},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "blends persona/personality each think; optional syncMemories plants up to 3 source memories.",
            },
            "identity_forge_cancel": {
                "applyable": True,
                "payload": {"targetId": {"type": "integer"}},
                "reversibilityClass": "irreversible",
                "notes": "restores snapshot taken at apply; clears active forge.",
            },
            # Divine Matrix Phase 9: Architect Zones (paint / door / limbo).
            "architect_zone": {
                "applyable": True,
                "payload": {
                    "zoneKind": {"type": "string", "enum": ["paint", "door", "limbo"]},
                    "districtId": {"type": "string", "optional": True},
                    "cells": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_ARCHITECT_ZONE_MAX_CELLS,
                        "description": 'gx,gy strings or {gx1,gy1,gx2,gy2} bounds',
                    },
                    "paintTerrain": {
                        "type": "string", "optional": True,
                        "enum": sorted(_sim_engine.GOD_ARCHITECT_PAINT_TERRAINS),
                    },
                    "keyId": {"type": "string", "optional": True,
                              "maxChars": _sim_engine.GOD_ARCHITECT_KEY_MAX_LEN},
                    "grantKeyAgentIds": {"type": "array", "optional": True,
                                         "items": {"type": "integer"}},
                    "holdAgentIds": {"type": "array", "optional": True,
                                     "items": {"type": "integer"}},
                    "reversible": {"type": "boolean", "optional": True, "default": True},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                },
                "reversibilityClass": "cancellable",
                "notes": "paint is public/world-visible; door/limbo audit private. grantKeyAgentIds grants godKeys tags on apply.",
            },
            "architect_zone_cancel": {
                "applyable": True,
                "payload": {"zoneId": {"type": "string"}},
                "reversibilityClass": "cancellable",
            },
            "architect_release_hold": {
                "applyable": True,
                "payload": {
                    "zoneId": {"type": "string"},
                    "agentIds": {"type": "array", "optional": True,
                                 "items": {"type": "integer"}},
                },
                "reversibilityClass": "cancellable",
            },
            # Divine Matrix Phase 10: Reload / Déjà Vu checkpoints.
            "checkpoint_create": {
                "applyable": True,
                "payload": {
                    "label": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                              "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "replaceOldest": {"type": "boolean", "optional": True, "default": False},
                },
                "reversibilityClass": "irreversible",
                "notes": (
                    f"cap {_sim_engine.GOD_CHECKPOINT_MAX} checkpoints; "
                    "reject at preview when full unless replaceOldest is true."
                ),
            },
            "checkpoint_restore": {
                "applyable": True,
                "payload": {"checkpointId": {"type": "string"}},
                "reversibilityClass": "irreversible",
                "notes": "irreversible world replace — copies checkpoint state.db + memory_store.json, then restore_state().",
            },
            "deja_vu_replay": {
                "applyable": _sim_engine.GOD_DEJA_VU_REPLAY,
                "payload": {
                    "targetId": {"type": "integer"},
                    "maxSteps": {
                        "type": "integer", "optional": True,
                        "min": 1, "max": _sim_engine.GOD_DEJA_VU_MAX_STEPS,
                        "default": _sim_engine.GOD_DEJA_VU_MAX_STEPS,
                    },
                },
                "reversibilityClass": "cancellable",
                "sessionCap": _sim_engine.GOD_DEJA_VU_SESSION_CAP,
                "notes": (
                    f"sequences up to {_sim_engine.GOD_DEJA_VU_MAX_STEPS} compulsion "
                    "steps from decisionDigests for one agent; cancel parent clears "
                    "remaining gates."
                ),
            },
            "crowd_compulsion": {
                "applyable": True,
                "payload": {
                    "theme": {
                        "type": "string", "optional": True,
                        "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                        "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES,
                    },
                    "durationFrames": {
                        "type": "integer", "optional": True,
                        "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                        "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                    },
                    "remainingTurns": {"type": "integer", "optional": True, "min": 1},
                    "targets": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_CROWD_COMPULSION_MAX_TARGETS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "targetId": {"type": "integer"},
                                "pinnedDecision": {
                                    "type": "object",
                                    "notes": "must include action; validated via normalize_decision",
                                },
                            },
                        },
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": (
                    "at least one of durationFrames or remainingTurns required; "
                    "cancel parent id clears all linked gates."
                ),
            },
            "dream_broadcast": {
                "applyable": True,
                "payload": {
                    "durationFrames": {
                        "type": "integer",
                        "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                        "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                        "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES,
                    },
                    "dreamSnapshot": {
                        "type": "object",
                        "notes": "required; keys validated per GOD_CONTEXT_MASK_DREAM_KEYS",
                    },
                    "targetIds": {
                        "type": "array",
                        "maxItems": _sim_engine.GOD_DREAM_BROADCAST_MAX_TARGETS,
                        "items": {"type": "integer"},
                    },
                },
                "reversibilityClass": "cancellable",
                "notes": "shared dream snapshot per target; cancel parent clears all linked masks.",
            },
            "revoke_guidance": {
                "applyable": True,
                "payload": {"id": {"type": "string"}},
                "reversibilityClass": "irreversible",
            },
            # Sovereign God mode Phase 4 (docs/archive/plan-sovereign-god-mode-v2.md
            # "Immediate miracles"). All three are irreversible.
            "agent_vitals": {
                "applyable": True,
                "payload": {
                    "targetId": {"type": "integer"},
                    "healthDelta": {"type": "number", "optional": True,
                                    "min": -_sim_engine.GOD_VITALS_DELTA_MAX,
                                    "max": _sim_engine.GOD_VITALS_DELTA_MAX},
                    "hungerDelta": {"type": "number", "optional": True,
                                    "min": -_sim_engine.GOD_VITALS_DELTA_MAX,
                                    "max": _sim_engine.GOD_VITALS_DELTA_MAX},
                },
                "reversibilityClass": "irreversible",
                "notes": ("cannot kill: a negative healthDelta is clamped to stop at "
                          f"{_sim_engine.GOD_VITALS_HEALTH_FLOOR} (never reaching the "
                          "incapacitation threshold), never touches deathFrame."),
            },
            "grant_resource": {
                "applyable": True,
                "payload": {
                    "resourceId": {"type": "string"},
                    "amount": {"type": "integer", "min": 1,
                              "max": _sim_engine.GOD_GRANT_PER_COMMAND_CAP},
                    "target": {"type": "object", "optional": True,
                              "description": '"stockpile" (default) or {"agentId": <int>}'},
                },
                "reversibilityClass": "irreversible",
                "sessionCap": _sim_engine.GOD_GRANT_SESSION_CAP,
            },
            "structure_condition": {
                "applyable": True,
                "payload": {
                    "structureId": {"type": "integer"},
                    "delta": {"type": "number",
                             "min": -_sim_engine.GOD_STRUCTURE_DELTA_MAX,
                             "max": _sim_engine.GOD_STRUCTURE_DELTA_MAX},
                },
                "reversibilityClass": "irreversible",
                "notes": "positive delta repairs, negative delta damages (may reach ruin).",
            },
            "repair_structures": {
                "applyable": True,
                "payload": {
                    "scope": {"type": "string | object",
                              "enum": ["ids", "all_critical"],
                              "description": '"ids", "all_critical", or {"districtId": "<id>"}'},
                    "structureIds": {"type": "array", "optional": True,
                                    "description": "required when scope is ids"},
                    "conditionTarget": {"type": "number", "optional": True,
                                       "min": 0, "max": 100},
                    "unRuin": {"type": "boolean", "optional": True, "default": True},
                },
                "reversibilityClass": "irreversible",
                "batchMax": _sim_engine.GOD_REPAIR_STRUCTURES_BATCH_MAX,
                "conditionMax": _sim_engine.GOD_REPAIR_STRUCTURES_CONDITION_MAX,
                "notes": "batch restore / un-ruin; only this command and agent repair_structure may un-ruin.",
            },
            "clear_ruins": {
                "applyable": True,
                "payload": {
                    "structureIds": {"type": "array", "optional": True},
                    "minAgeFrames": {"type": "integer", "optional": True,
                                    "default": _sim_engine.RUIN_CULL_AGE_FRAMES},
                    "districtId": {"type": "string", "optional": True},
                },
                "reversibilityClass": "irreversible",
                "batchMax": _sim_engine.GOD_CLEAR_RUINS_BATCH_MAX,
                "notes": "delete selected or aged ruins; mirrors engine cull cleanup.",
            },
            # Sovereign God mode Phase 5 (docs/archive/plan-sovereign-god-mode-v2.md
            # "Storyteller events" + "Timed lawgiver modifiers"). Reversibility
            # is "cancellable" with no primitives, "consequential" once any
            # primitive is included (see _god_reversibility_class).
            "story_event": {
                "applyable": True,
                "payload": {
                    "title": {"type": "string", "maxChars": _sim_engine.GOD_EVENT_TITLE_MAX_CHARS},
                    "narration": {"type": "string", "maxChars": _sim_engine.GOD_TEXT_MAX_CHARS,
                                 "maxBytes": _sim_engine.GOD_TEXT_MAX_BYTES},
                    "visibility": {"type": "string", "optional": True,
                                  "enum": ["public", "private"], "default": "public"},
                    "targetId": {"type": "integer", "optional": True,
                                "description": "required when visibility is 'private'"},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "modifiers": {"type": "object", "optional": True,
                                 "keys": _sim_engine.GOD_MODIFIER_RANGES},
                    "primitives": {"type": "array", "optional": True,
                                  "maxItems": _sim_engine.GOD_STORY_EVENT_MAX_PRIMITIVES,
                                  "itemKinds": ["agent_vitals", "grant_resource", "structure_condition"]},
                    "providence": {"type": "object", "optional": True,
                                  "description": "{text} -- reuses this event's own durationFrames"},
                    "replaceEffectId": {"type": "string", "optional": True,
                                        "description": "required to reuse a modifier key already active"},
                },
                "reversibilityClass": "cancellable | consequential (with primitives)",
                "notes": "atomic: preview validates every component; apply accepts all or changes nothing.",
            },
            # Sovereign God mode Phase 6 (docs/archive/plan-sovereign-god-mode-v2.md
            # "Weather override"). Always "consequential": entering "storm"
            # can permanently damage structures in the named districts, and
            # neither cancelling nor natural expiry undoes that damage.
            "weather_override": {
                "applyable": True,
                "requires": "WEATHER_ENABLED",
                "payload": {
                    "state": {"type": "string", "enum": list(_sim_engine.WEATHER_STATES)},
                    "districts": {"type": "array", "optional": True,
                                 "description": ('district ids; empty ONLY for state="clear"; '
                                                 "at least one required for every other state")},
                    "durationFrames": {"type": "integer", "optional": True,
                                       "min": _sim_engine.GOD_GUIDANCE_MIN_DURATION_FRAMES,
                                       "max": _sim_engine.GOD_GUIDANCE_MAX_DURATION_FRAMES,
                                       "default": _sim_engine.GOD_GUIDANCE_DEFAULT_DURATION_FRAMES},
                    "replaceEffectId": {"type": "string", "optional": True,
                                        "description": "required to replace the already-active weather override"},
                },
                "reversibilityClass": "consequential",
                "notes": ('clock ownership: activeEvents[].expiresFrame == weather["exitFrame"] '
                         "(the divine event owns duration). Cancelling or expiry hands off to the "
                         "natural cycle's next state -- never restores the prior state. Entering "
                         "'storm' can permanently damage structures in the named districts."),
            },
            # Huntable wildlife god kinds (specs/02-engine-core.md
            # "Sovereign God mode: wildlife kinds"). Irreversible one-shots
            # like grant_resource; gated on WILDLIFE_ENABLED.
            "wildlife_spawn": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "districtId": {"type": "string"},
                    "kind": {"type": "string",
                             "description": "must be valid for that district's habitat pool"},
                },
                "reversibilityClass": "irreversible",
                "kindPools": dict(_sim_engine.WILDLIFE_KIND_POOLS),
                "capPerDistrict": _sim_engine.WILDLIFE_CAP_PER_DISTRICT,
                "notes": "respects WILDLIFE_CAP_PER_DISTRICT; rejects unknown district/kind or full cap.",
            },
            "wildlife_despawn": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "id": {"type": "string", "optional": True,
                           "description": "creature id (mutually exclusive with districtId)"},
                    "districtId": {"type": "string", "optional": True,
                                   "description": "clear all alive fauna in district (mutually exclusive with id)"},
                },
                "reversibilityClass": "irreversible",
                "notes": "exactly one of id or districtId required; marks target(s) dead with ordinary respawn bookkeeping.",
            },
            "wildlife_set_hp": {
                "applyable": True,
                "requires": "WILDLIFE_ENABLED",
                "payload": {
                    "id": {"type": "string"},
                    "hp": {"type": "integer", "min": 0,
                           "description": "clamped to [0, maxHp]; hp<=0 kills with ordinary respawn bookkeeping"},
                },
                "reversibilityClass": "irreversible",
            },
        },
        "modifierRanges": _sim_engine.GOD_MODIFIER_RANGES,
        "previewTtlSeconds": _sim_engine.GOD_PREVIEW_TTL_SECONDS,
        "activeEventsCap": _sim_engine.GOD_ACTIVE_EVENTS_CAP,
        "weatherEnabled": _sim_engine.WEATHER_ENABLED,
        "wildlifeEnabled": _sim_engine.WILDLIFE_ENABLED,
        # Sovereign God mode Optional Phase 8 (docs/archive/plan-sovereign-god-mode-
        # v2.md "Free-prose story compiler"): dual-gated on GOD_MODE_ENABLED
        # (already true to reach this route) AND the SEPARATE
        # GOD_COMPILER_ENABLED dark flag -- so the viewer can render or hide
        # the Compile tab correctly without probing /control/god/compile.
        "compiler": {
            "enabled": _sim_engine.GOD_MODE_ENABLED and _sim_engine.GOD_COMPILER_ENABLED,
            "minIntervalSec": _sim_engine.GOD_COMPILER_MIN_INTERVAL_SEC,
            "sessionCap": _sim_engine.GOD_COMPILER_SESSION_CAP,
            "promptMaxChars": _sim_engine.GOD_COMPILER_PROSE_MAX_CHARS,
        },
    })


@app.route("/control/god/sight", methods=["GET"])
def control_god_sight():
    if not _god_authorized():
        return _god_unauthorized_response()
    return jsonify(engine.god_sight())


@app.route("/control/god/preview", methods=["POST"])
def control_god_preview():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    envelope = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_preview(envelope))


@app.route("/control/god/compile", methods=["POST"])
def control_god_compile():
    """Sovereign God mode Optional Phase 8 (docs/archive/plan-sovereign-god-mode-
    v2.md "Free-prose story compiler"). Token-gated exactly like every other
    God route, but the token itself is NEVER forwarded to
    engine.god_compile_prose -- that method has no parameter for it and
    never reads SIM_GOD_TOKEN. This route only ever produces a PREVIEW; it
    never applies anything (there is no god_apply call anywhere on this
    path)."""
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    prose = body.get("prose")
    if not isinstance(prose, str) or len(prose) > _sim_engine.GOD_COMPILER_PROSE_MAX_CHARS:
        return jsonify({"compileOk": False,
                        "reason": f"prose must be a string of at most "
                                  f"{_sim_engine.GOD_COMPILER_PROSE_MAX_CHARS} characters"})
    return jsonify(engine.god_compile_prose(prose))


@app.route("/control/god/apply", methods=["POST"])
def control_god_apply():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_apply(body.get("previewId"), body.get("requestId")))


@app.route("/control/god/cancel", methods=["POST"])
def control_god_cancel():
    if not _god_authorized():
        return _god_unauthorized_response()
    if _god_body_too_large():
        return jsonify({"error": "payload_too_large"}), 413
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(engine.god_cancel(body.get("targetId")))


if __name__ == "__main__":
    # Bind 0.0.0.0 so any device on the LAN can reach the sim (req #3); find this
    # machine's LAN IP with `ipconfig` and open the URL from another device as
    # http://<host-ip>:5001. On Windows, allow inbound TCP 5001 through the
    # firewall (or accept the first-run prompt). threaded=True lets the request
    # handlers run concurrently alongside the (forthcoming) SimEngine thread.
    # NOTE: this exposes the server — including the Ollama proxy and, when
    # GOD_AUTH_REQUIRED is off (the default), the unauthenticated God API —
    # to the whole local network. Intended for a trusted home LAN, not a
    # hostile network. Set SIM_GOD_AUTH=1 to restore token gating.
    HOST = os.environ.get("SIM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("SIM_PORT", "5001"))
    # Start the server-authoritative engine thread BEFORE the HTTP server so the
    # world ticks headless regardless of any connected viewer.
    engine.start()
    print(f"[server] SimEngine started ({engine.roster_size} agents, "
          f"{_sim_engine.TICKS_PER_SEC} ticks/s)")

    # Graceful shutdown: flush the full state to disk on exit so a restart
    # resumes exactly. atexit covers normal exit; the signal handlers cover
    # Ctrl-C / `kill` (which otherwise bypass atexit during app.run()).
    _saved_once = threading.Event()

    def _flush_on_exit():
        if _saved_once.is_set():
            return
        _saved_once.set()
        session_logger.flush_benchmarks()
        engine.stop()
        engine.save_state(force=True)

    atexit.register(_flush_on_exit)

    def _signal_shutdown(signum, frame):
        _flush_on_exit()
        os._exit(0)

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, _signal_shutdown)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
