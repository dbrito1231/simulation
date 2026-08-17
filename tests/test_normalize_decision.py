"""normalize_decision() / role_fallback_action() fallback ladder
(simulation/server.py's imported names, defined in
simulation/_server/decision_validation.py).

Covers the five cases the orchestrator asked for: unknown action name,
malformed/missing required payload field, an action disallowed by current
flag/context state, empty dict, and None. Every case must return a decision
whose action is a member of DECISION_ACTIONS and must never raise.
"""

from __future__ import annotations

import pytest

from _server.decision_validation import normalize_decision, role_fallback_action
from _server.roles_data import ROLES

_BASE_AGENT_DATA = {
    "role": "gatherer",
    "nearby_agents": [],
    "council_turn": False,
    "council_seated": False,
    "daily_council": None,
    "active_project": None,
    "role_project_map": {},
    "role_primary_resource_map": {},
    "resource_gather_roles_map": {},
    "needed_role": None,
    "pending_blueprint_ids": [],
    "pending_roles": [],
    "idle_agents": [],
    "invention_status": "not needed",
    "upgradeable_structures": [],
    "world_zone": "village",
}


def _agent_data(**overrides):
    data = dict(_BASE_AGENT_DATA)
    data.update(overrides)
    return data


def test_none_decision_returns_valid_fallback(server_module):
    result = normalize_decision(None, _agent_data())
    assert isinstance(result, dict)
    assert result.get("action") in server_module.DECISION_ACTIONS


def test_empty_dict_decision_never_raises_and_defaults_to_rest(server_module):
    """normalize_decision({}, ...) returns {} unchanged (no "action" key is
    added) -- the same shape apply_decision() itself defaults via
    `decision.get("action") or "rest"` (mixin_decisions.py:532). "rest" IS a
    DECISION_ACTIONS member, so this mirrors real production handling rather
    than asserting normalize_decision adds a key it never has."""
    result = normalize_decision({}, _agent_data())
    assert isinstance(result, dict)
    effective_action = result.get("action") or "rest"
    assert effective_action in server_module.DECISION_ACTIONS


def test_malformed_missing_required_field_falls_back(server_module):
    """council_speak requires a non-empty "message"; a decision missing it
    during a live council turn must be redirected to a role fallback, not
    passed through malformed."""
    agent_data = _agent_data(
        role="elder",
        council_turn=True,
        council_seated=True,
        daily_council={"phase": "discussion", "ballot": {}, "agenda": []},
    )
    result = normalize_decision({"action": "council_speak"}, agent_data)
    assert isinstance(result, dict)
    assert result.get("action") in server_module.DECISION_ACTIONS
    assert result.get("_fallback") is True


def test_action_disallowed_by_current_context_falls_back(server_module):
    """submit_structure_sprite is only valid when the agent is actually
    mid sprite-design-turn (sprite_design_only=True in the payload, mirroring
    the submit_structure_sprite entry in available_actions being gated by
    sprite_design_turn -- mixin_think_job.py). Picking it outside that
    context must fall back, not execute."""
    agent_data = _agent_data(sprite_design_only=False)
    result = normalize_decision(
        {"action": "submit_structure_sprite", "sprite": {"grid": ["."]}},
        agent_data,
    )
    assert isinstance(result, dict)
    assert result.get("action") in server_module.DECISION_ACTIONS
    assert result.get("_fallback") is True


def test_unknown_action_name_falls_back(server_module):
    """normalize_decision() rejects any action name not in DECISION_ACTIONS
    up front, before the per-action shape checks, and substitutes a role
    fallback -- guarding the path that opens up when server.py auto-disables
    structured output session-wide after an Ollama format rejection (see
    specs/03-cognition.md), since Ollama's json_schema mode is otherwise the
    only thing constraining `action` to a legal enum member."""
    result = normalize_decision(
        {"action": "totally_bogus_action", "reasoning": "x"}, _agent_data()
    )
    assert isinstance(result, dict)
    assert result.get("action") in server_module.DECISION_ACTIONS
    assert result.get("_fallback") is True
    assert result.get("action_rejection_note") == "unrecognized action"
    assert "rejection_note" not in result


def test_role_fallback_action_never_raises_and_is_always_valid(server_module):
    """Every seed role, called with the barest possible agent_data, must
    still produce a DECISION_ACTIONS member -- role_fallback_action's
    ladder always ends in a default_branch() that never returns None."""
    for role in list(ROLES) + ["totally_unknown_role"]:
        result = role_fallback_action(role, _agent_data(role=role))
        assert isinstance(result, dict), role
        assert result.get("action") in server_module.DECISION_ACTIONS, role
        assert result.get("_fallback") is True, role
