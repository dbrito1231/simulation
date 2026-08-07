"""Deterministic smoke harness for Sid-parity Phases 1-4.

Exercises specialization need signals, priority/repeal governance, competing
memes, belief-biased votes, and effectful constitutional rules without LM
Studio. Run:

    uv run python scripts/sid_parity_smoke.py
"""


from __future__ import annotations

import sys
from pathlib import Path

# Thin runner: discovers and calls every test_* function moved into
# scripts/_sid_parity_smoke/ (pure move split of this former 1,483-line file,
# no behavior change). The explicit sys.path insert makes the package
# importable regardless of invocation style (direct `python script.py`,
# where the interpreter already puts the script's directory on sys.path[0],
# or runpy.run_path(), which does not -- e.g. daily_council_regression.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sid_parity_smoke import helpers as _helpers  # noqa: F401,E402
from _sid_parity_smoke.roles_memes import *  # noqa: F401,F403,E402
from _sid_parity_smoke.governance import *  # noqa: F401,F403,E402
from _sid_parity_smoke.beliefs_piano import *  # noqa: F401,F403,E402
from _sid_parity_smoke.scale_headroom import *  # noqa: F401,F403,E402
from _sid_parity_smoke.memory import *  # noqa: F401,F403,E402


def main():
    print("Sid-parity smoke (Phases 1-4 + PIANO stagger)")
    engine = make_engine(8)
    test_dual_meme_seed(engine)
    test_belief_biased_vote(engine)
    test_authored_belief_persuasion_and_project_preference(engine)
    test_survival_need_role(engine)
    test_auto_switch_and_latency(engine)
    test_emergent_role_registry(engine)
    test_server_fallback_uses_live_role_maps(engine)
    test_pending_role_cap()

    engine2 = make_engine(8)
    test_priority_and_repeal(engine2)

    engine3 = make_engine(8)
    test_repeal_backstop_age_gate(engine3)

    engine4 = make_engine(8)
    test_custom_rule_effect_and_constitution(engine4)

    engine5 = make_engine(8)
    test_amendment_at_active_rule_cap(engine5)

    engine6 = make_engine(8)
    test_rule_enactment_races(engine6)

    engine7 = make_engine(8)
    test_custom_rule_nonbuild_district(engine7)

    engine8 = make_engine(8)
    test_constitution_restore_migration(engine8)

    test_role_fallback_switch()
    test_piano_stagger_offline()
    test_piano_cache_restore_roundtrip()
    test_always_on_piano_pulse()
    test_library_scaling_and_lessons()
    test_civic_era_requires_both_light_and_transit()

    print("Sid-parity smoke (Phase 6 -- scale headroom)")
    test_roster_default_unchanged()
    test_roster_headroom_generates_20()
    test_newcomer_backstop_reaches_generated_slots()
    test_district_bucket_matches_flat_scan()
    test_think_dispatch_staleness_priority()

    print("Sid-parity smoke (Phase 1 -- MemoryStore restart persistence)")
    test_memory_store_restart_persistence()

    print("Sid-parity smoke (Phase 4 -- wiki-style compounding memory)")
    test_wiki_memory_merge()
    test_wiki_memory_roundtrip()
    print("ALL PASS")


if __name__ == "__main__":
    main()
