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

import sys
from pathlib import Path

# Thin runner: discovers and calls every test_*/run_* function moved into
# scripts/_god_mode_smoke/ (pure move split of this former 5,005-line file,
# no behavior change). The explicit sys.path insert makes the package
# importable regardless of invocation style (direct `python script.py`,
# where the interpreter already puts the script's directory on sys.path[0],
# or runpy.run_path(), which does not). helpers must be imported first --
# it sets SIM_GOD_MODE/SIM_GOD_TOKEN/SIM_GOD_AUTH before the first import of
# sim_engine/server, exactly as the original single file did.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _god_mode_smoke import helpers as _helpers  # noqa: F401,E402
from _god_mode_smoke.phase2_core import *  # noqa: F401,F403,E402
from _god_mode_smoke.phase3_voice_matrix import *  # noqa: F401,F403,E402
from _god_mode_smoke.decision_gate_bargain import *  # noqa: F401,F403,E402
from _god_mode_smoke.voice_directives import *  # noqa: F401,F403,E402
from _god_mode_smoke.miracles import *  # noqa: F401,F403,E402
from _god_mode_smoke.story_events import *  # noqa: F401,F403,E402
from _god_mode_smoke.weather import *  # noqa: F401,F403,E402
from _god_mode_smoke.compiler_http import *  # noqa: F401,F403,E402
from _god_mode_smoke.architect_checkpoints import *  # noqa: F401,F403,E402


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
    run_divine_console_phase8_smoke()
    run_divine_console_phase9_smoke()
    run_divine_console_phase10_smoke()
    test_bargain_success_grants_resource()
    test_bargain_expiry_settles_failure()
    test_directive_and_providence_stay_separate()
    test_prompt_lines_frame_window()
    test_prompt_size_cap_and_divine_lines_render()
    test_voice_binding_wording_and_guidance_active()
    test_synthesize_divine_response_missing_and_valid()
    test_voice_follow_clears_goal_continue_keeps()
    test_voice_omen_cancels_special_turns_not_defer()
    test_voice_skip_cap_forces_close_after_repeated_synthetic()
    test_proclamation_sets_providence_and_cancels_special_turns()
    test_presentation_soft_thunder_validates_and_applies()
    test_sight_recent_divine_responses_and_snapshot_privacy()
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
    test_story_event_modifier_conflict_warnings_non_fatal()
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
