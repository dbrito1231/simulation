"""Action-sync invariant (specs/01-architecture.md#action-sync-invariant,
specs/07-actions.md).

Every decision action name must agree across six surfaces:
  1. DECISION_ACTIONS (simulation/server.py) — the canonical list.
  2. DECISION_SCHEMA's "action" enum (simulation/server.py).
  3. SYSTEM_PROMPT (simulation/prompts.py) — prose mentions each action.
  4. apply_decision() dispatch (simulation/sim_engine/mixin_decisions.py).
  5. available_actions filter (simulation/sim_engine/mixin_think_job.py) —
     a per-agent FLAG-FILTERED SUBSET of DECISION_ACTIONS, not an equal set
     (specs/07-actions.md: "available_actions ... further filters this
     list per-agent by live flag state").
  6. ACTION_LABELS (simulation/viewer/sidebar.js) — display-only; missing
     keys fall back to actionLabel()'s "move_to_" prefix rule or a generic
     `.replace(/_/g, " ")`, so this is asserted as a SUBSET too (no stray/
     mistyped label keys), not equality (specs/01-architecture.md's table
     marks it "display only, no logic").

Deliberately does not hardcode the action count (specs warn against a second
source of truth for the total).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIDEBAR_JS = ROOT / "simulation" / "viewer" / "sidebar.js"
MIXIN_DECISIONS_PY = ROOT / "simulation" / "sim_engine" / "mixin_decisions.py"


def _action_labels_keys():
    """Parse ACTION_LABELS's object-literal keys out of sidebar.js as text
    (no JS runtime available in this Python test layer)."""
    text = SIDEBAR_JS.read_text(encoding="utf-8")
    start = text.index("const ACTION_LABELS = {")
    end = text.index("\n};", start)
    body = text[start:end]
    return set(re.findall(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*):", body, re.MULTILINE))


def _apply_decision_dispatched_actions():
    """Parse every action name apply_decision() dispatches on, out of
    mixin_decisions.py as text: `action == "x"`, `action.startswith("move_to_")`
    handled separately, and `action in ("a", "b")` combined branches."""
    text = MIXIN_DECISIONS_PY.read_text(encoding="utf-8")
    # Slice to apply_decision's body so this doesn't pick up unrelated code.
    start = text.index("def apply_decision(self, agent, decision):")
    # Next top-level "    def " after apply_decision marks the next method.
    next_def = text.index("\n    def ", start + 1)
    body = text[start:next_def]

    actions = set(re.findall(r'action == "([a-z_]+)"', body))
    for group in re.findall(r'action in \(([^)]+)\)', body):
        actions.update(re.findall(r'"([a-z_]+)"', group))
    # move_to_district/move_to_agent are dispatched by exact-match branches
    # already captured above; the trailing `elif action.startswith("move_to_")`
    # is the back-compat hedge for legacy move_to_<kind> actions, not a new
    # DECISION_ACTIONS member — no action-name literal to extract there.
    return actions


def test_decision_actions_no_duplicates(server_module):
    actions = server_module.DECISION_ACTIONS
    assert len(actions) == len(set(actions)), "DECISION_ACTIONS has duplicate entries"


def test_decision_schema_enum_matches_decision_actions(server_module):
    schema_enum = server_module.DECISION_SCHEMA["properties"]["action"]["enum"]
    assert set(schema_enum) == set(server_module.DECISION_ACTIONS)


# Actions deliberately absent from the literal SYSTEM_PROMPT text: each has
# its own dedicated prompt path instead of a routine-turn rule line, so their
# absence here is not drift.
#   - submit_structure_sprite: swapped in via its own sprite-design system
#     prompt (server.py ~line 1553), only during a sprite_design_only turn.
#   - offer_contract / accept_contract: appended by
#     prompts.append_contracts_addendum() at request time, only when
#     CONTRACTS_ENABLED (server.py ~line 1984) -- not baked into the base
#     SYSTEM_PROMPT module constant.
_SYSTEM_PROMPT_SPECIAL_CASED_ACTIONS = {
    "submit_structure_sprite", "offer_contract", "accept_contract",
}


def test_system_prompt_mentions_every_routine_action(server_module):
    """SYSTEM_PROMPT is prose, not a machine-checkable list, but every
    canonical action name the model could pick on a routine turn must
    appear somewhere in it, or the model can never be told about it."""
    prompt = server_module.SYSTEM_PROMPT
    checked = set(server_module.DECISION_ACTIONS) - _SYSTEM_PROMPT_SPECIAL_CASED_ACTIONS
    missing = [a for a in checked if a not in prompt]
    assert not missing, f"SYSTEM_PROMPT never mentions: {sorted(missing)}"


def test_system_prompt_documents_move_to_agent(server_module):
    """move_to_agent is a real DECISION_ACTIONS/DECISION_SCHEMA member and now
    has its own rule line (5c2) in SYSTEM_PROMPT, placed before the first
    EXAMPLE marker so SYSTEM_PROMPT_SLIM's retry variant carries it too."""
    assert "move_to_agent" in server_module.SYSTEM_PROMPT
    assert "move_to_agent" in server_module.SYSTEM_PROMPT_SLIM


def test_apply_decision_dispatches_every_action():
    dispatched = _apply_decision_dispatched_actions()
    # DECISION_ACTIONS itself is server.py-owned; re-derive it here without
    # importing server.py so this test can run standalone from the others.
    decision_actions_text = (ROOT / "simulation" / "server.py").read_text(encoding="utf-8")
    start = decision_actions_text.index("DECISION_ACTIONS = [")
    end = decision_actions_text.index("]", start)
    literal = decision_actions_text[start:end]
    canonical = set(re.findall(r'"([a-z_]+)"', literal))

    # move_to_district/move_to_agent are explicit branches; every other
    # DECISION_ACTIONS member must have its own apply_decision() branch.
    missing = canonical - dispatched
    assert not missing, f"apply_decision() has no branch for: {sorted(missing)}"
    # No stray branch names outside the canonical set either (would mean
    # apply_decision and DECISION_ACTIONS have already drifted).
    extra = dispatched - canonical
    assert not extra, f"apply_decision() dispatches unknown actions: {sorted(extra)}"


def test_available_actions_is_subset_of_decision_actions(server_module):
    """available_actions (mixin_think_job.py, the payload key
    _build_think_payload() actually emits) filters DECISION_ACTIONS per live
    flag state / per-agent context — it must never introduce a name
    DECISION_ACTIONS doesn't already have (specs/07-actions.md). Built from a
    real engine + a real think payload rather than parsed as text, since the
    filter body only names its EXCLUSIONS as literals -- the base list comes
    from self.d["AVAILABLE_ACTIONS"] at runtime, not a source literal here."""
    from conftest import make_minimal_engine

    engine = make_minimal_engine(server_module.DECISION_ACTIONS)
    agent = engine.agents[0]
    with engine.lock:
        payload = engine._build_think_payload(agent)

    available = payload["available_actions"]
    assert set(available) <= set(server_module.DECISION_ACTIONS)
    # Sanity-check this isn't a trivial empty/degenerate result.
    assert "collect_resource" in available


def test_action_labels_is_subset_of_decision_actions(server_module):
    labels = _action_labels_keys()
    assert labels <= set(server_module.DECISION_ACTIONS), (
        f"ACTION_LABELS has stray keys not in DECISION_ACTIONS: "
        f"{sorted(labels - set(server_module.DECISION_ACTIONS))}"
    )
    # Sanity-check the parse itself found real entries.
    assert "collect_resource" in labels
