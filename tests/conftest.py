"""Shared pytest fixtures for the unit-test layer (tests/).

Adds simulation/ to sys.path (same idiom scripts/_sid_parity_smoke/helpers.py
already uses) so `import sim_engine`, `import server`, and `from _server....`
resolve without installing the simulation package. See specs/12-ops.md's
"Unit tests" section for what this layer covers and why it stays this thin.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "simulation"
sys.path.insert(0, str(SIM_DIR))


@pytest.fixture(scope="session")
def server_module(tmp_path_factory):
    """Import simulation/server.py exactly once for the whole test session.

    server.py has a documented module-level side effect (see its own
    docstring and prompts.py's): `session_logger = SessionLogger(...)` at
    import time unconditionally creates a new `simulation/logs/<timestamp>/`
    directory. That's real production behavior we're not allowed to change
    (server.py is out of scope for this test layer), so instead this fixture
    monkeypatches SessionLogger's base_dir just for the duration of the
    import, redirecting the new session directory into a session-scoped
    pytest tmp dir. The patch is reverted immediately after import — nothing
    else in the codebase is touched.

    server.py's `state.db`/`memory_store.json`/`predictions.json` reads stay
    pointed at their real (hardcoded) paths, but all three are read-only at
    import time (restore_state() only reads; MemoryStore/PredictionsStore
    only read-if-present at construction) and tolerate a missing file, so
    this is safe even when those files don't exist in a given checkout.
    """
    import _server.logging_session as logging_session

    fake_logs_base = tmp_path_factory.mktemp("server_import_logs")
    real_init = logging_session.SessionLogger.__init__

    def _patched_init(self, base_dir, *args, **kwargs):
        real_init(self, str(fake_logs_base), *args, **kwargs)

    logging_session.SessionLogger.__init__ = _patched_init
    try:
        import server
    finally:
        logging_session.SessionLogger.__init__ = real_init
    return server


def make_minimal_engine(available_actions, roster_size=4):
    """Build a cold-start SimEngine with the same minimal-deps idiom
    scripts/_sid_parity_smoke/helpers.py's make_engine() already uses (no
    LLM, no logging side effects, no state.db). Not a fixture itself so
    callers can pass a specific AVAILABLE_ACTIONS list (e.g. the real
    DECISION_ACTIONS from the server_module fixture)."""
    import sim_engine as se
    from _server.roles_data import ROLES

    role_primary = {
        role: d["specialty"][0] for role, d in ROLES.items() if d.get("specialty")
    }
    resource_gather_roles = {}
    for role, d in ROLES.items():
        for res in d.get("specialty") or []:
            resource_gather_roles.setdefault(res, []).append(role)

    deps = {
        "ROLES": ROLES,
        "ROLE_PROJECT": {
            role: (d.get("preferredProject")[0]
                   if isinstance(d.get("preferredProject"), list)
                   else d.get("preferredProject"))
            for role, d in ROLES.items()
        },
        "ROLE_SKILLS": {role: d.get("skill", "helps") for role, d in ROLES.items()},
        "ROLE_PRIMARY_RESOURCE": role_primary,
        "RESOURCE_GATHER_ROLES": {r: tuple(v) for r, v in resource_gather_roles.items()},
        "AVAILABLE_ACTIONS": list(available_actions),
        "SLUG_RE": re.compile(r"^[a-z][a-z0-9_]{1,24}$"),
        "llm_decide": lambda payload: {"action": "rest", "reasoning": "test"},
        "lm_complete": lambda *a, **k: None,
        "is_scaffold_text": lambda t: False,
        "memory_store": None,
        "log_activity": lambda *a, **k: None,
        "log_conversation": lambda *a, **k: None,
        "read_conversation_window": lambda *a, **k: [],
        "log_benchmark": lambda *a, **k: None,
        "flush_benchmarks": lambda: None,
        "log_divine": lambda *a, **k: None,
        "log_compiler": lambda *a, **k: None,
        "validate_blueprint": lambda *a, **k: (False, "unused"),
        "validate_sprite_block": lambda *a, **k: (False, "unused"),
        "sprite_spec_is_degenerate": lambda *a, **k: False,
        "SPRITE_GRID_MIN": 3,
        "SPRITE_GRID_MAX": 16,
        "canonical_effect_vector": lambda *a, **k: (),
        "run_piano_module": lambda *a, **k: None,
        "run_chronicle_saga": lambda *a, **k: None,
        "run_meta_update": lambda *a, **k: None,
        "normalize_decision": lambda d, a: d,
        "synthesize_divine_response": lambda d, a: d,
        "build_agent_data": lambda *a, **k: {},
    }
    return se.SimEngine(deps, roster_size=roster_size)
