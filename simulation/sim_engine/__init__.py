"""Server-authoritative simulation engine package.

Phase 6a package conversion: this package replaces the former single-file
simulation/sim_engine.py with the same public surface, split into sibling
modules by concern:

  - constants.py   -- feature flags, tuning constants, districts/roads/
                       resources/recipes registries, era ladder, etc.
                       (ported from index.html consts; now server config)
  - persistence.py -- full-state persistence (Contract 3): DB_PATH,
                       STATE_VERSION, _write_state_db/_read_state_db, and
                       related SQLite helpers.
  - helpers.py      -- agent-roster registries (AGENT_DEFS/ROSTER) and small
                       free-function helpers (geometry, district/road
                       validation, task-text cleanup).
  - core.py         -- the `SimEngine` class itself, relocated unchanged
                       (pure move, no behavior change) from the former
                       single-file module.
  - mixin_world_state.py -- Phase 6b: `_WorldStateMixin`, a method slice cut
                       out of core.py's SimEngine (state-delta dirty
                       tracking, logging/memory, agent lookups, districts/
                       roads/movement, survival, Sage emergency, project
                       helpers, resource ecology). `SimEngine` in core.py
                       inherits from it; see that file's docstring for the
                       exact method range and rationale.
  - mixin_structures_economy.py -- Phase 6c: `_StructuresEconomyMixin`, a
                       method slice cut out of core.py's SimEngine (structure
                       function registry, GOODS/TECH query helpers, tech
                       eras, weather state machine, market pricing/priced
                       trade/property, spoilage/decay/disaster/shelter tick
                       mechanics, repair_structure, structure upgrades).
  - mixin_diplomacy.py -- Phase 6c: `_DiplomacyMixin`, a method slice cut out
                       of core.py's SimEngine (Path 1 tool tiers, composable
                       tiles, terrain mutation, Path 1 diplomacy --
                       settlements/caravans/treaties -- and Living-ecosystem
                       Phase 3 cosmetic shipment records). `SimEngine` in
                       core.py inherits from both; see each file's docstring
                       for the exact method range and rationale.
  - mixin_wildlife.py -- Phase 6d: `_WildlifeMixin` (Path 1 pressure loop --
                       night/upkeep -- and the huntable wildlife system).
  - mixin_project_helpers.py -- Phase 6d: `_ProjectHelpersMixin` (Path 1
                       benchmarks, project/invention helpers,
                       `_start_project_for`).
  - mixin_divine_matrix.py -- Phase 6d: `_DivineMatrixMixin` (Divine Voice
                       guidance/adherence, Burning Bush, Anointment,
                       Identity Forge, Divine Matrix Architect Zones and
                       checkpoints).
  - mixin_crafting_rules.py -- Phase 6d: `_CraftingRulesMixin` (idle/task
                       helpers, crafting, rules/voting/constitution).
  - mixin_lifecycle.py -- Phase 6d: `_LifecycleMixin` (Phase F population
                       lifecycle, cemetery/burial, repair/ruin backstop,
                       succession, birth/newcomer machinery).
  - mixin_governance_culture.py -- Phase 6d: `_GovernanceCultureMixin`
                       (governance gates, blueprint/role validation, memes,
                       Phase G skills/library/chronicle/personality drift).
  - mixin_backstops.py -- Phase 6d: `_BackstopsMixin` (message bus/inbox,
                       emergent roles, deterministic village-unsticking
                       backstops).
  - mixin_council_growth.py -- Phase 6d: `_CouncilGrowthMixin` (Daily
                       Council Assembly, invention council, stuck-project
                       relocation, structure reorganization, district
                       founding, rules backstop).
  - mixin_decisions.py -- Phase 6e: `_DecisionsMixin` (Sid-parity benchmark
                       helpers, wiki-memory merge/periodic memory
                       maintenance, the `apply_decision` world-mutation
                       switch, talk-target resolution, goal tracking).
  - mixin_think_job.py -- Phase 6e: `_ThinkJobMixin` (`_build_think_payload`
                       LLM-context builder, PIANO module orchestration,
                       think-job dispatch/execution, per-frame tick loop).
  - mixin_persistence.py -- Phase 6f: `_PersistenceMixin` (full-state
                       persistence -- save/restore/serialize, Contract 3 --
                       plus Sovereign God mode core state, decision digests
                       and Deja Vu replay, the stored-text contract, and
                       story-event validation basics).
  - mixin_divine_sampling.py -- Phase 6f: `_DivineSamplingMixin` (Divine
                       Matrix Phase 2 per-agent sampling overlay: sampling
                       validation, dream snapshots, context masks, decision
                       gate validation, anointment/identity edit validation,
                       Merovingian bargain predicates).
  - mixin_god_validation.py -- Phase 6g: `_GodValidationMixin` (Sovereign God
                       mode weather-override validation, timed lawgiver
                       modifiers, repair/clear-ruins selection, the full
                       per-kind envelope validator `_validate_god_envelope`
                       moved whole, and the preview-outcome/digest/
                       fingerprint cluster).
  - mixin_god_lifecycle.py -- Phase 6g: `_GodLifecycleMixin` (Sovereign God
                       mode preview cache, idempotency store, guidance
                       closure, divine-effect/bargain expiry, and the
                       Optional Phase 8 free-prose compiler).
  - mixin_god_broadcast.py -- Phase 6h: `_GodBroadcastMixin` (Sovereign God
                       mode preview entry point, intervention-id/recording
                       bookkeeping, the per-kind apply dispatcher, and the
                       proclamation/providence/private-omen/whisper-campaign/
                       crowd-compulsion/dream-broadcast apply handlers).
  - mixin_god_bush_bargain.py -- Phase 6h: `_GodBushBargainMixin` (Burning
                       Bush session lifecycle, Merovingian bargain
                       primitives/settlement, Anointment apply/revoke, and
                       Identity Forge apply handlers).
  - mixin_god_gate.py -- Phase 6h: `_GodGateMixin` (agent-sampling/memory/
                       belief-plant/context-mask apply handlers, and the
                       full decision-gate/possession/veto/sweep machinery).
  - mixin_god_miracles.py -- Phase 6i: `_GodMiraclesMixin` (Sovereign God
                       mode Phase 4 bounded immediate miracles, huntable
                       wildlife god kinds, the Phase 6 weather override, the
                       Phase 5 storyteller events, and the top-level God
                       command dispatch -- `god_apply`/`god_cancel`/
                       `god_sight`).
  - mixin_snapshot.py -- Phase 6i (final mixin-extraction sub-phase):
                       `_SnapshotMixin` (control -- `pause`/`resume`/
                       `reset` -- plus the full Contract 2 /state snapshot-
                       building cluster: `snapshot`/`snapshot_delta` and
                       their helpers). After Phase 6i, core.py's SimEngine
                       class body holds only `__init__`/
                       `_select_active_defs`/`_make_agents`/`_reset_world`
                       (the construction path); the mixin-extraction portion
                       of Phase 6 is structurally complete.
                       `SimEngine` in core.py inherits from all of the
                       above; see each file's docstring for the exact
                       method range and rationale.

This module re-exports every name that was importable off the old
`simulation/sim_engine.py` module, so `import sim_engine as se` (or
`as _sim_engine`) continues to work identically for every existing caller
(simulation/server.py, simulation/prompts.py, scripts/*_smoke.py, etc.) with
zero changes required on their side. See specs/01-architecture.md for the
module layout invariant (SDD sync for this split is deferred to the end of
the full Phase 6 mixin-extraction effort, not skipped).

`class SimEngine` (core.py) is loaded by exec()'ing its source into THIS
module's own namespace below, rather than via a plain `from .core import
SimEngine`. This is deliberate, not an oversight: dozens of
scripts/*_smoke.py (and their scripts/_*_smoke/ helpers) monkeypatch
module-level names -- DB_PATH, SURVIVAL_ENABLED, GOD_MODE_ENABLED,
WEATHER_ENABLED, PIANO_MODULES, and more -- directly on the imported
`sim_engine` module object (e.g. `se.DB_PATH = tmp_path`) for test isolation.
In the original single-file sim_engine.py this worked because every
SimEngine method read those names as bare globals out of the one module's
own `__dict__`. A plain submodule import of core.py would give SimEngine's
methods their OWN separate copies of those names (captured at package-import
time via `from .constants import *` inside core.py), silently breaking every
one of those monkeypatches -- a real behavior change, not a cosmetic one.
exec()'ing core.py's source into this module's `globals()` keeps the class
body's bare-name globals pointing at the exact same dict this module (and
`se.<NAME> = ...` callers) mutate, preserving single-file-module semantics
byte-for-byte while still keeping the class physically split into its own
file for readability and the Phase 6b+ mixin-extraction work.
"""

from .constants import *  # noqa: F401,F403
from .persistence import *  # noqa: F401,F403
from .helpers import *  # noqa: F401,F403

import os as _os

# Phase 6b+: mixin files must be exec()'d into this same shared namespace
# (not plain-imported) BEFORE core.py, for two reasons: (1) core.py's
# `class SimEngine(_WorldStateMixin)` needs the mixin class name bound in
# this namespace at class-definition time, and (2) the mixin methods'
# bare-name globals (SURVIVAL_ENABLED, ECOLOGY_ENABLED, ...) must resolve
# against this module's own __dict__ -- the same reasoning as core.py below.
_mixin_files = (
    "mixin_world_state.py",
    "mixin_structures_economy.py",
    "mixin_diplomacy.py",
    "mixin_wildlife.py",
    "mixin_project_helpers.py",
    "mixin_divine_matrix.py",
    "mixin_crafting_rules.py",
    "mixin_lifecycle.py",
    "mixin_governance_culture.py",
    "mixin_backstops.py",
    "mixin_council_growth.py",
    "mixin_decisions.py",
    "mixin_think_job.py",
    "mixin_persistence.py",
    "mixin_divine_sampling.py",
    "mixin_god_validation.py",
    "mixin_god_lifecycle.py",
    "mixin_god_broadcast.py",
    "mixin_god_bush_bargain.py",
    "mixin_god_gate.py",
    "mixin_god_miracles.py",
    "mixin_snapshot.py",
)
for _mixin_name in _mixin_files:
    _mixin_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), _mixin_name)
    with open(_mixin_path, "r", encoding="utf-8") as _mixin_file:
        exec(compile(_mixin_file.read(), _mixin_path, "exec"), globals())  # noqa: S102

_core_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "core.py")
with open(_core_path, "r", encoding="utf-8") as _core_file:
    exec(compile(_core_file.read(), _core_path, "exec"), globals())  # noqa: S102
del _os, _mixin_files, _mixin_name, _mixin_path, _mixin_file, _core_path, _core_file


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
    "SURVIVAL_ENABLED",
    "CRAFTING_ENABLED",
    "USE_GOALS",
    "STRUCTURE_EFFECTS_ENABLED",
    "STRUCTURE_WEAR_ENABLED",
    "ACTIVITY_CUES_ENABLED",
    "SOCIAL_LAYER_ENABLED",
    "CHRONICLE_ENABLED",
    "CHRONICLE_SAGA_ENABLED",
    "FOUNDING_EVENTS_ENABLED",
    "WORLD_CLOCK_HUD_ENABLED",
    "SEASONAL_AGENTS_ENABLED",
    "MEMORY_ENABLED",
    "WIKI_MEMORY",
    "TESTAMENT_ENABLED",
    "THEORY_OF_MIND_ENABLED",
    "SCHISM_ENABLED",
    "SCHISM_MIN_CLUSTER",
    "SCHISM_COOLDOWN_FRAMES",
    "PEER_MODEL_MAX_PEERS",
    "PEER_MODEL_FIELD_CHAR_CAP",
    "AGENT_MESSAGING",
    "PIANO_MODULES",
    "ALWAYS_ON_MODULES",
    "META_SYSTEM",
    "EMERGENT_ROLES",
    "RULES_ENABLED",
    "MEMES_ENABLED",
    "BENCHMARKS_ENABLED",
    "ECOLOGY_ENABLED",
    "ROADS_ENABLED",
    "CROP_GROWTH_ENABLED",
    "WILDLIFE_ENABLED",
    "WILDLIFE_KIND_POOLS",
    "WILDLIFE_DECORATIVE_KINDS",
    "WILDLIFE_YIELD",
    "WILDLIFE_MAX_HP",
    "WILDLIFE_SPEED",
    "HUNT_RADIUS",
    "WILDLIFE_FLEE_RADIUS",
    "HUNT_DAMAGE",
    "HUNT_DAMAGE_HUNTER",
    "WILDLIFE_RESPAWN_FRAMES",
    "WILDLIFE_POP_TICK_FRAMES",
    "WILDLIFE_MIGRATE_CHECK_FRAMES",
    "WILDLIFE_MIGRATE_CHANCE",
    "WILDLIFE_STAGE_COUNT",
    "WILDLIFE_CAP_PER_DISTRICT",
    "WILDLIFE_SHORE_STRIP",
    "WILDLIFE_HABITAT_INSET",
    "WILDLIFE_BEACH_WATER_KINDS",
    "WILDLIFE_HABITAT_KINDS",
    "CARAVAN_VISUALS_ENABLED",
    "SHIPMENT_TRAVEL_FRAMES",
    "SHIPMENT_RING_CAP",
    "WEATHER_ENABLED",
    "WEATHER_STATES",
    "WEATHER_DWELL_TICKS",
    "WEATHER_SEASON_STORMINESS",
    "WEATHER_BASE_STORM_CHANCE",
    "STORM_DISASTER_PROB",
    "WEATHER_GOVERNANCE_ENABLED",
    "WEATHER_STORM_REGROW_MULT",
    "WEATHER_CLEARING_REGROW_MULT",
    "GOD_MODE_ENABLED",
    "GOD_AUTH_REQUIRED",
    "GOD_STATE_VERSION",
    "GOD_WHISPER_CAMPAIGN_MAX_TARGETS",
    "GOD_CROWD_COMPULSION_MAX_TARGETS",
    "GOD_DREAM_BROADCAST_MAX_TARGETS",
    "GOD_MEMORY_DEFAULT_KIND",
    "GOD_BELIEF_MEMORY_KIND",
    "GOD_MEMORY_KIND_MAX_LEN",
    "GOD_AGENT_SAMPLING_MODELS",
    "GOD_AGENT_SAMPLING_TEMP_MIN",
    "GOD_AGENT_SAMPLING_TEMP_MAX",
    "GOD_AGENT_SAMPLING_TOP_P_MIN",
    "GOD_AGENT_SAMPLING_TOP_P_MAX",
    "GOD_AGENT_SAMPLING_TOP_K_MIN",
    "GOD_AGENT_SAMPLING_TOP_K_MAX",
    "GOD_AGENT_SAMPLING_MIN_P_MIN",
    "GOD_AGENT_SAMPLING_MIN_P_MAX",
    "GOD_AGENT_SAMPLING_FAST_DECISION_CAP",
    "GOD_CONTEXT_MASK_MODES",
    "GOD_CONTEXT_MASK_DREAM_KEYS",
    "GOD_CONTEXT_MASK_FORGED_MAX",
    "GOD_DECISION_GATE_MODES",
    "GOD_VETO_HOLD_CAP",
    "GOD_VOICE_ACK_SKIP_CAP",
    "GOD_PREVIEW_CACHE_MAX",
    "GOD_PREVIEW_TTL_SECONDS",
    "GOD_REQUEST_CACHE_MAX",
    "GOD_RECENT_INTERVENTIONS_CAP",
    "GOD_DIVINE_RESPONSE_LOG_MAX",
    "GOD_ACTIVE_EVENTS_CAP",
    "GOD_TEXT_MAX_CHARS",
    "GOD_TEXT_MAX_BYTES",
    "GOD_BURNING_BUSH_THREAD_MAX",
    "GOD_BURNING_BUSH_PROMPT_MAX_CHARS",
    "GOD_BARGAIN_PREDICATES",
    "GOD_BARGAIN_PRIMITIVE_KINDS",
    "GOD_ANOINT_STIGMATA_MAX",
    "GOD_ANOINT_STIGMATA_TAG_MAX_CHARS",
    "GOD_ANOINT_ORACLE_HINTS_MAX",
    "GOD_ANOINT_PROMPT_MAX_CHARS",
    "GOD_IDENTITY_PERSONA_MAX_CHARS",
    "GOD_IDENTITY_PERSONALITY_MAX_CHARS",
    "GOD_IDENTITY_COPY_MEMORIES_MAX",
    "GOD_LIMBO_STATION",
    "GOD_ARCHITECT_ZONE_MAX_CELLS",
    "GOD_ARCHITECT_ZONES_MAX",
    "GOD_ARCHITECT_KEY_MAX_LEN",
    "GOD_CHECKPOINT_MAX",
    "GOD_CHECKPOINT_ROOT",
    "GOD_DEJA_VU_REPLAY",
    "GOD_DECISION_DIGEST_CAP",
    "GOD_DEJA_VU_MAX_STEPS",
    "GOD_DEJA_VU_SESSION_CAP",
    "GOD_COMPILER_ENABLED",
    "GOD_COMPILER_MIN_INTERVAL_SEC",
    "GOD_COMPILER_SESSION_CAP",
    "GOD_COMPILER_TIMEOUT_SEC",
    "GOD_COMPILER_PROSE_MAX_CHARS",
    "GOD_COMPILER_PROSE_MAX_BYTES",
    "GOD_GUIDANCE_MIN_DURATION_FRAMES",
    "GOD_GUIDANCE_MAX_DURATION_FRAMES",
    "GOD_GUIDANCE_DEFAULT_DURATION_FRAMES",
    "GOD_VETO_HOLD_TIMEOUT_FRAMES",
    "GOD_VITALS_HEALTH_FLOOR",
    "GOD_VITALS_DELTA_MAX",
    "GOD_GRANT_PER_COMMAND_CAP",
    "GOD_GRANT_SESSION_CAP",
    "GOD_STRUCTURE_DELTA_MAX",
    "GOD_REPAIR_STRUCTURES_CONDITION_MAX",
    "GOD_REPAIR_STRUCTURES_BATCH_MAX",
    "GOD_CLEAR_RUINS_BATCH_MAX",
    "GOD_MODIFIER_RANGES",
    "GOD_MODIFIER_CONFLICT_RULES",
    "_god_modifier_conflict_warnings",
    "GOD_EVENT_TITLE_MAX_CHARS",
    "GOD_STORY_EVENT_MAX_PRIMITIVES",
    "GOD_STORY_EVENT_MAX_MODIFIERS",
    "WORLD_W",
    "WORLD_H",
    "STARTER_DISTRICTS",
    "DISTRICT_KIND_TEMPLATES",
    "COASTAL_PAIR_BEACH_TEMPLATE",
    "OCEAN_DISTRICT_TEMPLATE",
    "PROJECT_KIND",
    "STARTER_ROAD_NODES",
    "STARTER_ROAD_EDGES",
    "FRONTIER_PLOT_W",
    "FRONTIER_PLOT_H",
    "CORE_RESERVED_BOUNDS",
    "MAX_TOTAL_DISTRICTS",
    "DISTRICT_FOUND_STALL_THRESHOLD",
    "ZONE_NAMES",
    "FRAME_MS",
    "TICKS_PER_SEC",
    "TICK_DT",
    "MOVE_SCALE",
    "SURVIVAL_TICK_FRAMES",
    "MEMORY_TICK_FRAMES",
    "META_TICK_FRAMES",
    "ROLE_REVIEW_FRAMES",
    "BENCHMARK_TICK_FRAMES",
    "FIRST_BENCHMARK_FRAME",
    "HUNGER_RATE",
    "HEALTH_RATE",
    "HEALTH_REGEN",
    "EAT_THRESHOLD",
    "FOOD_RESTORE",
    "EDIBLE_RESOURCES",
    "HEAL_AMOUNT",
    "COLLAPSE_REGEN",
    "COLLAPSE_REVIVE_HEALTH",
    "REVIVE_HUNGER",
    "EDIBLE_RESERVE",
    "EDIBLE_SCARCITY_THRESHOLD",
    "MEAT_SCARCITY_CAP",
    "SHARE_RADIUS",
    "STARVING_HUNGER",
    "FARM_PLOTS_PER_EXTRA",
    "FARM_YIELD_BONUS_CAP",
    "HOUSES_PER_NEW_VILLAGER",
    "WORKSHOPS_PER_CRAFT_BONUS",
    "WALL_SOFT_CAP",
    "WORKSHOP_DISTRICT_CAP",
    "CUSTOM_SOFT_CAP",
    "STRUCTURE_UPGRADES_ENABLED",
    "MAX_STRUCTURE_LEVEL",
    "LEVEL_STEP",
    "UPGRADE_STAT_STEP",
    "UPGRADE_TIERS",
    "UPGRADE_COST_BASE",
    "SPRITE_DESIGN_MAX_ATTEMPTS",
    "STRUCTURE_PX_SCALE",
    "SEED_SPRITE_DIMS",
    "PROC_SPRITE_DIMS",
    "STRUCTURE_GAP_X",
    "STRUCTURE_GAP_Y",
    "REORG_CHECK_FRAMES",
    "REORG_NO_ROOM_NUDGE_FRAMES",
    "SEED_UPGRADE_PALETTES",
    "EFFECT_TICK_FRAMES",
    "ECOLOGY_REGROW_FRAMES",
    "LEGACY_CUSTOM_PRODUCE",
    "APPROVED_CUSTOM_STALL_FRAMES",
    "APPROVED_CUSTOM_BACKOFF_FRAMES",
    "STOCK_DEFAULT_MAX",
    "STOCK_REGROW_PER_TICK",
    "STOCK_DEPLETE_MULTIPLIER",
    "STOCK_LOW_RATIO",
    "STOCK_MIN_YIELD_RATIO",
    "DISTRICT_ECOLOGY_STAGES",
    "DISTRICT_ECOLOGY_THRESHOLDS",
    "DISTRICT_ECOLOGY_HYSTERESIS",
    "COLLECT_CAP",
    "STALL_THRESHOLD",
    "PROJECT_ABANDON_THRESHOLD",
    "PROJECT_ABANDON_THRESHOLD_CRAFTED",
    "PROJECT_DEFER_ABANDON_STREAK",
    "PROJECT_DEFER_COOLDOWN",
    "BLUEPRINT_STALL_THRESHOLD",
    "DIRECTIVE_TTL_FRAMES",
    "MAX_BEHAVIOR_NUDGES",
    "MAX_REJECTED_BLUEPRINTS_PROMPT",
    "MAX_APPROVED_PROJECTS_PROMPT",
    "MAX_KNOWN_RESOURCES_PROMPT",
    "MAX_KNOWN_RECIPES_PROMPT",
    "MAX_ACTIVE_RULES_PROMPT",
    "MAX_NEARBY_AGENTS_PROMPT",
    "MAX_IDLE_AGENTS_PROMPT",
    "MAX_BLUEPRINT_BRIEFS",
    "GOAL_STEP_FRAMES",
    "SAGE_CRITICAL_HEALTH",
    "CRAFT_STALL_THRESHOLD",
    "INVENTION_BACKSTOP_STREAK",
    "INVENTION_ELDER_TAKEOVER",
    "ELDER_RETASK_COOLDOWN_FRAMES",
    "SOCIAL_SILENCE_FRAMES",
    "COMMITMENT_EXPIRE_FRAMES",
    "MAX_PENDING_BLUEPRINTS",
    "MAX_PENDING_ROLES",
    "MAX_APPROVED_CUSTOM",
    "MAX_CUSTOM_RESOURCES",
    "MAX_CUSTOM_RECIPES",
    "BLUEPRINT_AMNESTY_FRAMES",
    "CUSTOM_RESOURCE_RETIRE_FRAMES",
    "SAGE_REVIEW_TIMEOUT_FRAMES",
    "MAX_PENDING_RULES",
    "MAX_ACTIVE_RULES",
    "MAX_EMERGENT_ROLES",
    "MAX_CONSTITUTION_HISTORY",
    "ROLE_SWITCH_TICK_FRAMES",
    "ROLE_SWITCH_COOLDOWN",
    "AUTOSWITCH_PROTECTED_ROLES",
    "ROLE_STARVE_NEED_THRESHOLD",
    "RULES_TICK_FRAMES",
    "RULE_PROPOSE_COOLDOWN",
    "RULE_REPEAL_MIN_AGE_FRAMES",
    "MEME_SEED_ID",
    "MEME_RIVAL_ID",
    "MEME_SEED_IDS",
    "MEMES",
    "MEME_RULE_AFFINITY",
    "BELIEF_ARCHETYPES",
    "MEME_SPREAD_PROB",
    "MEME_PROXIMITY_PROB",
    "MEME_TICK_FRAMES",
    "MAX_BELIEFS",
    "BELIEF_PITCH_SESSION_CAP",
    "BELIEF_FALLBACK_QUALITY",
    "BELIEF_EXISTING_PENALTY",
    "BELIEF_RELATIONSHIP_WEIGHT",
    "INBOX_CAP",
    "WORKING_MEM_CAP",
    "SHORT_MEM_CAP",
    "LONG_MEM_CAP",
    "WIKI_SECTION_CHAR_CAP",
    "VALID_GATHER_ZONES",
    "VALID_VISUAL_STYLES",
    "RULE_KINDS",
    "CUSTOM_RULE_ACTIONS",
    "CUSTOM_RULE_MODIFIER_MAX",
    "MAX_CONCURRENT_LLM",
    "PIANO_CONCURRENT_LLM",
    "PIANO_MODULE_CACHE_TTL",
    "PIANO_CROSS_CONTEXT_TTL",
    "MODULE_PULSE_INTERVAL_S",
    "MODULE_PULSE_MAX_BATCH",
    "MODULE_NOTE_MAX_AGE_S",
    "MODULE_REFRESH_IDLE_SKIP",
    "MODULE_REFRESH_TIMEOUT_S",
    "PIANO_MODULE_TIMEOUT_WAIT_S",
    "LLM_ORPHAN_TIMEOUT_THRESHOLD",
    "LLM_ORPHAN_COOLDOWN_S",
    "LLM_MIN_GAP_MS",
    "THINK_RETRY_FRAMES",
    "MAX_CONCURRENT_PROJECTS",
    "GOODS_ENABLED",
    "GOODS_TICK_FRAMES",
    "BASE_STORAGE_CAPACITY",
    "SPOILAGE_RATIO",
    "CART_CARRY_BONUS",
    "DAY_FRAMES",
    "HOUSE_SHELTER_OCCUPANTS",
    "SHELTER_HUNGER_PENALTY",
    "SHELTER_HUNGER_FLOOR",
    "STRUCTURE_DECAY_PER_GOODS_TICK",
    "STRUCTURE_DISREPAIR_THRESHOLD",
    "REPAIR_CONDITION_RESTORE",
    "REPAIR_CAMPAIGN_RUIN_RATIO",
    "REPAIR_CAMPAIGN_WORKING_FRAC",
    "REPAIR_CAMPAIGN_MAX_ASSIGN",
    "REPAIR_CAMPAIGN_GOAL_TTL",
    "REPAIR_CAMPAIGN_CRITICAL_TYPES",
    "RUIN_CULL_AGE_FRAMES",
    "RUIN_CULL_MIN_PER_CALL",
    "RUIN_CULL_MAX_PER_CALL",
    "structure_condition_tier",
    "DISASTER_PROB",
    "DISASTER_DAMAGE",
    "YEAR_FRAMES",
    "SEASON_FRAMES",
    "SEASONS",
    "SEASON_REGROW_MULT",
    "TECH_TREE_ENABLED",
    "DAILY_COUNCIL_ENABLED",
    "MAX_TECH_TIER",
    "SAGE_REVIEW_ENABLED",
    "WAGON_CARRY_BONUS",
    "WAGON_SPEED_MULT",
    "INVENTION_COUNCIL_SIZE",
    "COUNCIL_LOG_CAP",
    "DAILY_COUNCIL_MIN_LIVING",
    "DAILY_COUNCIL_DISCUSSION_ROUNDS",
    "DAILY_COUNCIL_PHASE_TTL_FRAMES",
    "DAILY_COUNCIL_SESSION_TTL_FRAMES",
    "DAILY_COUNCIL_LOG_CAP",
    "DAILY_COUNCIL_DIGEST_CAP",
    "DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS",
    "DAILY_COUNCIL_SCARCITY_THRESHOLD",
    "DAILY_COUNCIL_SCARCITY_TOPICS",
    "COUNCIL_TTL_FRAMES",
    "ERA_LADDER",
    "ECONOMY_ENABLED",
    "CONTRACTS_ENABLED",
    "MAX_OPEN_CONTRACTS",
    "MAX_CONTRACTS_PROMPT",
    "BASE_PRICE",
    "PRICE_SCARCITY_MULT",
    "PRICE_MIN",
    "ALLY_PRICE_DISCOUNT",
    "RIVAL_PRICE_SURCHARGE",
    "MINT_RATE",
    "HOMELESS_NUDGE_FRAMES",
    "LIFECYCLE_ENABLED",
    "LIFECYCLE_TICK_FRAMES",
    "AGE_YEARS_PER_TICK",
    "ADULT_AGE",
    "ELDER_AGE",
    "MAX_LIFE_EXPECTANCY",
    "DEATH_CHANCE_START_AGE",
    "DEATH_CHANCE_PER_TICK",
    "POPULATION_FLOOR",
    "BIRTH_CHECK_FRAMES",
    "BIRTH_FOOD_SURPLUS_PER_AGENT",
    "BIRTH_MIN_INTERVAL_FRAMES",
    "BIRTH_STARTING_SKILL_PENALTY",
    "NEWBORN_GOODS_SHARE",
    "SUCCESSION_ELECTION_TTL_FRAMES",
    "HARVEST_QUOTA_PERIOD_FRAMES",
    "RATIONING_STORAGE_LOW_RATIO",
    "RATIONING_WITHDRAW_CAP",
    "CULTURE_ENABLED",
    "SKILL_KINDS",
    "SKILL_MAX_LEVEL",
    "SKILL_PRACTICE_GAIN",
    "SKILL_BONUS_DIVISOR",
    "SKILL_HEAL_BONUS_PER_LEVEL",
    "TEACH_KEYWORDS",
    "TEACH_TRANSFER_FRACTION",
    "LIBRARY_KNOWLEDGE_CAP",
    "LIBRARY_STUDY_GAIN",
    "LIBRARY_STUDY_WEIGHT_CAP",
    "CHRONICLE_CAP",
    "SAGA_CAP",
    "SAGA_DIALOGUE_EXCERPT_CAP",
    "SAGA_FALLBACK_TEXT",
    "CHRONICLE_PROMPT_ENTRIES",
    "TESTAMENT_CAP",
    "TESTAMENT_PROMPT_ENTRIES",
    "COUNCIL_DIGEST_PROMPT_ENTRIES",
    "CHRONICLE_MILESTONE_KINDS",
    "MEME_MUTATION_PROB",
    "MEME_MUTATION_SESSION_CAP",
    "HARVEST_SPIRIT_CONTRIB_BOOST",
    "PERSONALITY_DRIFT_CAP",
    "CEMETERY_ENABLED",
    "BURY_CONTACT_DIST",
    "CONFRONT_CONTACT_DIST",
    "CONFRONT_DAMAGE",
    "CONFRONT_INCAP_HEALTH",
    "CONFRONT_LETHAL_THRESHOLD",
    "CONFRONT_FLEE_DIST",
    "CONFRONT_COOLDOWN_FRAMES",
    "CONFRONT_PRESSURE_WINDOW_FRAMES",
    "FORCED_HUNT_GOAL_TTL",
    "BURIAL_BACKSTOP_FRAMES",
    "PATH1_ENABLED",
    "INDUSTRY_ENABLED",
    "TOOL_TIERS_ENABLED",
    "COMPOSABLE_BUILD_ENABLED",
    "TERRAIN_TILES_ENABLED",
    "PATH1_DIPLOMACY_ENABLED",
    "TIER3_CONTENT_ENABLED",
    "PRESSURE_LOOP_ENABLED",
    "ENV_EFFECTS_ENABLED",
    "LIBRARY_SCALING_ENABLED",
    "TRANSIT_ENABLED",
    "ECONOMY_SINKS_ENABLED",
    "COMFORT_EVERY_N_GOODS_TICKS",
    "path1_on",
    "PROJECT_TEMPLATES",
    "PROJECT_ORDER",
    "SEED_STRUCTURE_FUNCTIONS",
    "TERRAFORM_TEMPLATES",
    "TERRAFORM_FUNCTIONS",
    "KIND_TERRAFORM",
    "BASE_RESOURCES",
    "CRAFTED_RESOURCES",
    "SEED_RECIPES",
    "TILE_CELL",
    "TILE_CAP_PER_DISTRICT",
    "BLOCK_REFUND_RATIO",
    "NON_DIGGABLE_DISTRICT_KINDS",
    "TOOL_TIER_ORDER",
    "TOOL_TIER_LEVEL",
    "RESOURCE_MIN_TOOL",
    "TOOL_YIELD_BONUS",
    "TERRAIN_TYPES",
    "GOD_ARCHITECT_PAINT_TERRAINS",
    "BLOCK_TYPES",
    "NIGHT_FRACTION",
    "NIGHT_EXPOSURE_DAMAGE",
    "WILDLIFE_EVENT_PROB",
    "WILDLIFE_GUARD_RADIUS",
    "SETTLEMENT_STRUCT_THRESHOLD",
    "SETTLEMENT_POP_THRESHOLD",
    "CARAVAN_CARRY_MIN",
    "CARAVAN_LOG_CAP",
    "CARAVAN_VEHICLE_RESOURCES",
    "TREATY_TARIFF_MAX",
    "PATH1_GRID_COLS",
    "PATH1_GRID_ROWS",
    "AGENT_DEFS",
    "ROSTER",
    "MAX_ROSTER_SIZE",
    "_GENERATED_AGENT_NAMES",
    "_GENERATED_AGENT_PERSONALITIES",
    "_GENERATED_AGENT_COLORS",
    "_generated_agent_defs",
    "NEARBY_RADIUS",
    "_dist",
    "_rects_overlap",
    "_TASK_PREAMBLE",
    "_TASK_MAX_LEN",
    "_clean_task_text",
    "_validate_districts",
    "_validate_road_graph",
    "_build_frontier_plots",
    "get_zone",
    "get_district",
    "SimEngine",
]
