"""Feature flags, tuning constants, and world/registry data for the
simulation engine (districts, roads, resources, recipes, structure
functions, era ladder, etc.). Split out of the former single-file
sim_engine.py during the Phase 6a package conversion -- pure move, no
behavior change (see simulation/sim_engine/__init__.py for the package
overview). Ported from index.html consts; now server config.
"""

import os


__all__ = [
    "SURVIVAL_ENABLED",
    "CRAFTING_ENABLED",
    "USE_GOALS",
    "STRUCTURE_EFFECTS_ENABLED",
    "STRUCTURE_WEAR_ENABLED",
    "ACTIVITY_CUES_ENABLED",
    "SOCIAL_LAYER_ENABLED",
    "CHRONICLE_ENABLED",
    "FOUNDING_EVENTS_ENABLED",
    "WORLD_CLOCK_HUD_ENABLED",
    "SEASONAL_AGENTS_ENABLED",
    "MEMORY_ENABLED",
    "WIKI_MEMORY",
    "THEORY_OF_MIND_ENABLED",
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
    "DETERMINISM_PINNING",
    "DETERMINISM_SEED",
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
    "CHRONICLE_PROMPT_ENTRIES",
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
]

# --- Feature flags (ported from index.html consts; now server config) ---
SURVIVAL_ENABLED = True
CRAFTING_ENABLED = True
USE_GOALS = True
STRUCTURE_EFFECTS_ENABLED = True
# Viewer-only projections of existing simulation state. These flags never
# change decay, ruin, or action mechanics; they only control /state consumers.
STRUCTURE_WEAR_ENABLED = True
ACTIVITY_CUES_ENABLED = True
# Read-only viewer projections of relationships and the existing culture
# chronicle. These never alter social/culture simulation state or prompts.
SOCIAL_LAYER_ENABLED = True
CHRONICLE_ENABLED = True
# Read-only viewer projection: announces a newly founded district as a
# chronicle milestone + a brief banner (index.html). Gates only the
# _found_district chronicle call and the banner trigger -- district founding
# itself (_maybe_found_district) is unconditional and unaffected.
FOUNDING_EVENTS_ENABLED = True
# Viewer-only projections of the existing calendar and sprite season mirror.
# They do not alter simulation time, terrain, or agent state.
WORLD_CLOCK_HUD_ENABLED = True
SEASONAL_AGENTS_ENABLED = True
MEMORY_ENABLED = True
# Wiki-style compounding memory (TASKS_PENDING item 3 / plan Phase 4): when
# True, _run_memory_maintenance's existing round-robin summarizer call is
# upgraded to a merge-and-reconcile call that writes agent["memoryWiki"]
# instead of a plain summarize-and-append. No new LLM call cadence -- same
# call site, same MEMORY_TICK_FRAMES cadence. Default off; one-flag revert.
WIKI_MEMORY = True
# Emergence Breakthroughs F2: bounded peer mental models maintained by a PIANO
# module inside the existing fan-out (not an extra call per turn). Advisory
# prompt context only — no deterministic behavior acts on peerModel. Default
# off; default-on requires a soak comparison (see specs/03-cognition.md).
THEORY_OF_MIND_ENABLED = False
# Hard caps on agent["peerModel"][peerIdStr] entries (LRU by frame).
PEER_MODEL_MAX_PEERS = 8
PEER_MODEL_FIELD_CHAR_CAP = 48
AGENT_MESSAGING = True
PIANO_MODULES = True
# Gated scheduler for the PIANO whiteboard.  Kept dark until Phase B's
# contention soak proves it is safe; false preserves the existing per-think
# fan-out path exactly.
ALWAYS_ON_MODULES = False
META_SYSTEM = True
EMERGENT_ROLES = True
RULES_ENABLED = True
MEMES_ENABLED = True
BENCHMARKS_ENABLED = True
# Emergence Breakthroughs F5 / Phase A1: headless harness pinning only.
# When True, SimEngine re-seeds the process RNG at init, defers think jobs to
# the end of each tick (synchronous, sorted agent-name order), and uses
# frameTick-based scheduling instead of wall clock for LLM gap/cooldown checks.
# Default off — live 24/7 path unchanged. Opt in via SIM_DETERMINISM_PINNING=1
# or scripts/determinism_proof.py --pin.
DETERMINISM_PINNING = str(os.environ.get("SIM_DETERMINISM_PINNING", "")).strip().lower() in (
    "1", "true", "yes", "on",
)
DETERMINISM_SEED = int(os.environ.get("SIM_DETERMINISM_SEED", "42"))
ECOLOGY_ENABLED = True
# World-expansion plan: waypoint-based road routing for general travel
# (move_to_district / idle wander / craft-station redirects). Sage-emergency
# rescue and short local hops (move_to_agent, trade, talk) always stay direct
# regardless of this flag -- see _set_agent_target_to_agent. Off reverts
# _set_agent_target to the old straight-to-random-interior-point behavior so
# routing can be A/B compared.
ROADS_ENABLED = True
# Living-ecosystem Phase 2: districtEcology projection (CROP_GROWTH_ENABLED)
# plus server-authoritative huntable fauna (WILDLIFE_ENABLED). Crop growth is
# still viewer-facing only; WILDLIFE_ENABLED now also gates engine fauna
# state, motion, spawn/respawn/migration, hunt helpers, and /state wildlife[].
# Off → no fauna mutation and an empty wildlife projection.
CROP_GROWTH_ENABLED = True
WILDLIFE_ENABLED = True
# Huntable wildlife (distinct from Path-1 _tick_wildlife forest-attack pressure).
WILDLIFE_KIND_POOLS = {
    "forest": ["bird", "squirrel", "deer", "fox", "boar", "owl"],
    "farm": ["cow", "rabbit", "chicken", "mouse", "bee"],
    "beach": ["fish", "crab", "gull", "turtle", "seal"],
}
WILDLIFE_DECORATIVE_KINDS = {"bee"}
WILDLIFE_YIELD = {
    "bird": "meat", "squirrel": "meat", "deer": "meat", "fox": "meat",
    "boar": "meat", "owl": "meat",
    "cow": "meat", "rabbit": "meat", "chicken": "meat", "mouse": "meat",
    "fish": "fish", "crab": "fish", "gull": "fish", "turtle": "fish", "seal": "fish",
}
# HP tiers: low ≈1–2, mid ≈3–4, high boar/seal ≈5–6; bee decorative.
WILDLIFE_MAX_HP = {
    "bird": 1, "squirrel": 1, "rabbit": 1, "chicken": 2, "mouse": 1,
    "fish": 1, "crab": 1, "gull": 1, "bee": 1,
    "deer": 4, "fox": 3, "owl": 3, "cow": 4, "turtle": 4,
    "boar": 6, "seal": 5,
}
WILDLIFE_SPEED = {
    "bird": 3.2, "squirrel": 2.8, "deer": 3.0, "fox": 3.4, "boar": 2.0, "owl": 2.6,
    "cow": 1.8, "rabbit": 3.5, "chicken": 2.2, "mouse": 2.4, "bee": 2.0,
    "fish": 2.5, "crab": 1.4, "gull": 3.0, "turtle": 1.2, "seal": 2.2,
}
HUNT_RADIUS = 90
WILDLIFE_FLEE_RADIUS = 120
# Retuned Phase 2b: hunters clear 1–4 HP prey in one hit; boar/seal need two.
HUNT_DAMAGE = 2
HUNT_DAMAGE_HUNTER = 4
WILDLIFE_RESPAWN_FRAMES = 600
WILDLIFE_POP_TICK_FRAMES = 600
WILDLIFE_MIGRATE_CHECK_FRAMES = 600
WILDLIFE_MIGRATE_CHANCE = 0.05
WILDLIFE_STAGE_COUNT = {"barren": 0, "sparse": 1, "healthy": 2, "lush": 4}
WILDLIFE_CAP_PER_DISTRICT = 4
WILDLIFE_SHORE_STRIP = 70
WILDLIFE_HABITAT_INSET = 16
WILDLIFE_BEACH_WATER_KINDS = {"fish", "crab", "turtle", "seal"}
WILDLIFE_HABITAT_KINDS = set(WILDLIFE_KIND_POOLS.keys())
# Living-ecosystem Phase 3: purely cosmetic in-flight "shipment" records
# emitted AFTER an existing transfer (trade_resource, the priced-trade
# market path, district-project contributions) has already mutated the
# authoritative stockpile/inventory. The transfer itself is never delayed
# or gated by this -- see _emit_shipment. Shipments live only in
# self.shipments (NOT civilization state), so they are never persisted to
# state.db and simply vanish on restore, which is harmless. Off means
# /state omits "shipments" and the viewer draws nothing extra; the static
# physicalProps moored boats are unaffected either way.
CARAVAN_VISUALS_ENABLED = True
SHIPMENT_TRAVEL_FRAMES = 240   # ~8s at 30 ticks/s: how long a shipment animates across the road graph
SHIPMENT_RING_CAP = 8          # bounded ring -- oldest live shipments drop first

# Living-ecosystem Phase 4: deterministic clear -> gathering -> storm ->
# clearing -> clear weather state machine on the EXISTING GOODS_TICK_FRAMES
# cadence (_tick_weather, called from _tick_goods) -- no new timer. The
# dwell duration for whichever state is currently active is drawn (in
# goods-ticks) when that state is entered and stored as an exit frame in
# civilization["weather"], so the machine is restore-safe without a
# persisted counter (see restore_state's setdefault) and NEVER re-rolls
# already-persisted weather on load. _maybe_disaster (below) is rewired,
# WEATHER_ENABLED only, to require the "storm" state -- see its docstring
# for the rate-calibration arithmetic that keeps the long-run damage rate
# matching the pre-Phase-4 DISASTER_PROB baseline (also see
# specs/08-systems-economy.md).
WEATHER_ENABLED = True
WEATHER_STATES = ("clear", "gathering", "storm", "clearing")
# (min, max) dwell duration in GOODS_TICK_FRAMES units, drawn on state entry.
# clear's range is season-scaled (divided by storminess) in _weather_enter;
# the others are season-independent so storm/clearing "weather" itself
# always reads the same length once it starts.
WEATHER_DWELL_TICKS = {
    "clear": (40, 160),      # ~20-80 goods ticks (~10-80 min) between storm attempts
    "gathering": (2, 5),     # clouds building, ~1-2.5 min
    "storm": (2, 6),         # active storm, ~1-3 min
    "clearing": (2, 4),      # tapering off, ~1-2 min
}
# Season-weighted "storminess" multiplier: >1 shortens the clear dwell and
# raises the gathering->storm chance for that season; <1 does the opposite.
# Deliberately averages to 1.0 across the four (equal-length) seasons so the
# long-run damage rate (see _maybe_disaster) matches the legacy baseline
# without needing per-season recalibration.
WEATHER_SEASON_STORMINESS = {"spring": 1.1, "summer": 0.6, "autumn": 1.3, "winter": 1.0}
WEATHER_BASE_STORM_CHANCE = 0.5   # gathering -> storm probability at storminess 1.0
# In-storm probability (per goods tick, while WEATHER_ENABLED) that
# _maybe_disaster fires damage -- calibrated (see its docstring) so the
# long-run rate stays close to the legacy DISASTER_PROB.
STORM_DISASTER_PROB = 0.32

# Living-ecosystem Phase 5: weather -> ecology -> governance feedback.
# WEATHER_GOVERNANCE_ENABLED off is byte-identical to Phase 4 alone --
# regrowth, prompts, and the rule backstop all behave exactly as if this
# phase didn't exist. On, it does three things, all deterministic and all
# riding EXISTING cadences (no new timer, no new LLM call):
#   1. _tick_ecology_regrow multiplies its per-district regrow amount by
#      WEATHER_STORM_REGROW_MULT/WEATHER_CLEARING_REGROW_MULT for whichever
#      district(s) civilization["weather"]["districts"] names, while that
#      storm/clearing state lasts -- suppression during "storm", a partial
#      "rain" boost during "clearing" so weather isn't purely punitive.
#      Deliberately a fractional multiplier, never floored to 0: since
#      WEATHER_DWELL_TICKS bounds storm/clearing to a few minutes and the
#      multiplier is never exactly 0, a district always keeps inching toward
#      recovery even mid-storm -- no unbounded starvation spiral is possible
#      from this term alone (see _tick_ecology_regrow's docstring).
#   2. _weather_prompt_line() adds ONE short line to the think payload, and
#      only while state is "storm"/"clearing" (silent the rest of the time)
#      -- see _build_think_payload.
#   3. _maybe_advance_rules gets a third auto-proposal branch: if a
#      storm-affected district's ecology ratio drops below STOCK_LOW_RATIO,
#      propose an emergency "rationing" rule (existing RULE_KINDS entry,
#      LIFECYCLE_ENABLED already default True) with a unique per-enactment
#      id (see emergencyRuleSeq) -- never a second parallel governance
#      mechanism, and gated by the SAME RULE_PROPOSE_COOLDOWN/
#      lastRuleAttemptFrame discipline as the priority/tax branches (see
#      docs/archive/plan-rule-loop-fix.md).
WEATHER_GOVERNANCE_ENABLED = True
WEATHER_STORM_REGROW_MULT = 0.3     # suppression multiplier for storm-affected districts
WEATHER_CLEARING_REGROW_MULT = 1.5  # post-storm "rain" boost for the same districts while clearing

# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 2) ---
# GOD_MODE_ENABLED is an environment-backed, READ-ONCE-AT-IMPORT module flag,
# not a runtime toggle: no HTTP route may change it, and there is no live
# on/off switch by design -- enabling or disabling it requires the normal
# single-instance server restart with SIM_GOD_MODE set (or unset) in the
# process environment beforehand. Absent/blank/anything not explicitly
# false-like resolves to enabled, which is the default -- the Divine Console
# bar is now a normal, permanent part of the viewer. Set SIM_GOD_MODE to a
# false-like value ("0"/"false"/"no"/"off") before starting the server to
# opt back out and hide the console bar entirely. This is also the FIRST
# env-var-backed flag in sim_engine.py (every prior env-var precedent, e.g.
# SIM_HOST/SIM_PORT/SIM_AGENTS/SIM_LOG_RETENTION, lives only in server.py);
# see specs/01-architecture.md for that precedent note. God routes use a
# two-gate model: GOD_MODE_ENABLED must be True (this flag), and token auth
# is optional via GOD_AUTH_REQUIRED / SIM_GOD_AUTH (default off). See
# server.py's God-mode section and specs/12-ops.md -- security-relevant
# because HOST binds 0.0.0.0 (LAN-wide).
GOD_MODE_ENABLED = str(os.environ.get("SIM_GOD_MODE", "1")).strip().lower() not in (
    "0", "false", "no", "off",
)
# GOD_AUTH_REQUIRED is env-backed, read-once-at-import; default False (no
# token gate). Set SIM_GOD_AUTH=1 to restore the SIM_GOD_TOKEN check.
# Security-relevant: server.py HOST binds 0.0.0.0 (see specs/12-ops.md).
GOD_AUTH_REQUIRED = str(os.environ.get("SIM_GOD_AUTH", "0")).strip().lower() in (
    "1", "true", "yes", "on",
)
GOD_STATE_VERSION = 3               # schema version for civilization["godState"]
GOD_WHISPER_CAMPAIGN_MAX_TARGETS = 12  # max agents per whisper_campaign batch
GOD_CROWD_COMPULSION_MAX_TARGETS = 12   # max agents per crowd_compulsion batch
GOD_DREAM_BROADCAST_MAX_TARGETS = 12    # max agents per dream_broadcast batch
# Divine Matrix Phase 3: memory surgery kinds (private, never in public logs).
GOD_MEMORY_DEFAULT_KIND = "divine_false_memory"
GOD_BELIEF_MEMORY_KIND = "divine_belief"
GOD_MEMORY_KIND_MAX_LEN = 48
# Divine Matrix Phase 2: per-agent decision sampling overlay (Temperature Dial).
GOD_AGENT_SAMPLING_MODELS = frozenset({"sim-smart", "sim-fast"})
GOD_AGENT_SAMPLING_TEMP_MIN = 0.0
GOD_AGENT_SAMPLING_TEMP_MAX = 1.5
GOD_AGENT_SAMPLING_TOP_P_MIN = 0.0
GOD_AGENT_SAMPLING_TOP_P_MAX = 1.0
GOD_AGENT_SAMPLING_TOP_K_MIN = 0
GOD_AGENT_SAMPLING_TOP_K_MAX = 200
GOD_AGENT_SAMPLING_MIN_P_MIN = 0.0
GOD_AGENT_SAMPLING_MIN_P_MAX = 1.0
GOD_AGENT_SAMPLING_FAST_DECISION_CAP = 1  # max living agents on sim-fast decision override
# Divine Matrix Phase 4: reality distortion / context masks (private cognition layer).
GOD_CONTEXT_MASK_MODES = frozenset({"dream", "blue_pill", "red_pill", "whisper_chain"})
GOD_CONTEXT_MASK_DREAM_KEYS = frozenset({
    "nearby_agents", "resources", "weather_line", "recent_conversations",
    "district_stocks", "nearby_wildlife", "nearby_wildlife_line",
    "hunger", "health",
})
GOD_CONTEXT_MASK_FORGED_MAX = 8
# Divine Matrix Phase 5: decision gate (compulsion / thought veto / possession).
GOD_DECISION_GATE_MODES = frozenset({"compulsion", "veto", "possession"})
GOD_VETO_HOLD_CAP = 3
GOD_VOICE_ACK_SKIP_CAP = 3          # consecutive synthetic (non-genuine) divine_response turns for the
                                     # same guidance id before it is force-acked so a model that never
                                     # cooperates can't stall the guidance forever (see _bump_voice_guidance_skip)
GOD_PREVIEW_CACHE_MAX = 32          # bounded, in-memory, never persisted
GOD_PREVIEW_TTL_SECONDS = 60        # wall-clock, not frame-based (previews are a request-scoped concept)
GOD_REQUEST_CACHE_MAX = 100         # bounded, in-memory idempotency store (never persisted -- see docs/archive/plan-sovereign-god-mode-v2.md)
GOD_RECENT_INTERVENTIONS_CAP = 100  # persisted viewer-history ring inside godState
GOD_DIVINE_RESPONSE_LOG_MAX = 50    # Voice adherence ring (Sight only, never /state)
GOD_ACTIVE_EVENTS_CAP = 8           # bounded timed-effect ring (Phase 5 payload; plumbing only in Phase 2)
GOD_TEXT_MAX_CHARS = 240            # title/narration/proclamation cap (post-NFC-normalization character count)
GOD_TEXT_MAX_BYTES = 600            # a tighter-than-4x-chars UTF-8 byte cap so the byte check is load-bearing,
                                     # not merely redundant with the character cap (see _normalize_divine_text)
# Divine Matrix Phase 6: Burning Bush dialogue + Merovingian Bargain (private).
GOD_BURNING_BUSH_THREAD_MAX = 20
GOD_BURNING_BUSH_PROMPT_MAX_CHARS = GOD_TEXT_MAX_CHARS * 2
GOD_BARGAIN_PREDICATES = frozenset({
    "agent_has_resource", "structure_built", "frame_reached", "agent_health_below",
})
GOD_BARGAIN_PRIMITIVE_KINDS = frozenset({"agent_vitals", "grant_resource"})
# Divine Matrix Phase 7: Anointed (destiny + stigmata + oracle hints — private).
GOD_ANOINT_STIGMATA_MAX = 6
GOD_ANOINT_STIGMATA_TAG_MAX_CHARS = 40
GOD_ANOINT_ORACLE_HINTS_MAX = 8
GOD_ANOINT_PROMPT_MAX_CHARS = GOD_TEXT_MAX_CHARS * 2
# Divine Matrix Phase 8: Identity Forge (persona/personality/role mutation).
GOD_IDENTITY_PERSONA_MAX_CHARS = 200
GOD_IDENTITY_PERSONALITY_MAX_CHARS = GOD_TEXT_MAX_CHARS
GOD_IDENTITY_COPY_MEMORIES_MAX = 3
# Divine Matrix Phase 9: Architect Zones (paint / keyed door / limbo hold).
GOD_LIMBO_STATION = (140, 500)  # ocean district — Trainman limbo platform
GOD_ARCHITECT_ZONE_MAX_CELLS = 64
GOD_ARCHITECT_ZONES_MAX = 16
GOD_ARCHITECT_KEY_MAX_LEN = 32
# Divine Matrix Phase 10: Reload / Déjà Vu checkpoints (disk snapshots).
GOD_CHECKPOINT_MAX = 5
# NOTE: this module now lives one directory deeper than the original
# single-file sim_engine.py (simulation/sim_engine.py -> simulation/sim_engine/
# constants.py), so this needs an extra os.path.dirname() hop to keep
# resolving to simulation/backup/god-checkpoints (same value as before the
# package split).
GOD_CHECKPOINT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backup", "god-checkpoints")
# Stub-only tick replay — default off; enable via SIM_GOD_DEJA_VU_REPLAY=1.
GOD_DEJA_VU_REPLAY = str(os.environ.get("SIM_GOD_DEJA_VU_REPLAY", "")).strip().lower() in (
    "1", "true", "yes", "on",
)
GOD_DECISION_DIGEST_CAP = 200          # bounded ring in godState["decisionDigests"]
GOD_DEJA_VU_MAX_STEPS = 8              # max replay steps (K) per deja_vu_replay apply
GOD_DEJA_VU_SESSION_CAP = 12           # max applied replays per process lifetime (in-memory)

# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Optional Phase 8) ---
# GOD_COMPILER_ENABLED is a SECOND, independent, env-backed, read-once-at-
# import dark flag, gated on GOD_MODE_ENABLED as well (both must be True --
# see god_compile_prose and server.py's route). The plan is explicit that
# this phase's contention gate is NOT cleared by shipping the code: the
# compiler "needs its own A/B contention check" that "is not required for a
# complete structured Storyteller God" (docs/archive/plan-sovereign-god-mode-v2.md "Optional Phase 8"). No
# A/B measurement has been run in this change, so the flag ships OFF by
# default and stays off until that measurement happens -- see specs/12-ops.md
# "not-yet-A/B-measured" section for the recommended protocol.
GOD_COMPILER_ENABLED = str(os.environ.get("SIM_GOD_COMPILER", "")).strip().lower() in (
    "1", "true", "yes", "on",
)
GOD_COMPILER_MIN_INTERVAL_SEC = 5.0    # per-process rate limit, distinct from agent cognition
GOD_COMPILER_SESSION_CAP = 60          # hard ceiling on compiles for this process's lifetime
GOD_COMPILER_TIMEOUT_SEC = 10.0        # aggressive: a hung Ollama request must not lock the console
GOD_COMPILER_PROSE_MAX_CHARS = 800     # operator free-prose input cap (post-NFC-normalization character count)
GOD_COMPILER_PROSE_MAX_BYTES = 2400    # byte cap scaled to the larger prose cap (see GOD_TEXT_MAX_BYTES's ratio)

# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 3) ---
# Voice/providence duration bounds, frame-based like every other timed system
# (DIRECTIVE_TTL_FRAMES above is the closest precedent -- 5400 frames is the
# same ~3-minute default this reuses). A caller may omit durationFrames (the
# default applies) or supply one, silently clamped into range rather than
# rejected -- consistent with the plan's "clamped values" preview contract.
GOD_GUIDANCE_MIN_DURATION_FRAMES = 300      # ~10s at 30 ticks/s
GOD_GUIDANCE_MAX_DURATION_FRAMES = 54000    # ~30 min at 30 ticks/s
GOD_GUIDANCE_DEFAULT_DURATION_FRAMES = 5400  # ~3 min, mirrors DIRECTIVE_TTL_FRAMES
GOD_VETO_HOLD_TIMEOUT_FRAMES = GOD_GUIDANCE_DEFAULT_DURATION_FRAMES

# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 4) ---
# Immediate miracles: agent_vitals, grant_resource, structure_condition. All
# three are irreversible (docs/archive/plan-sovereign-god-mode-v2.md "Honest reversibility" -- the default
# branch of _god_reversibility_class already covers any kind that is not
# providence/private_omen, so no change was needed there).
#
# agent_vitals "cannot kill" (Decision #6): health <= 0 is the
# _update_survival incapacitation threshold (see HEALTH_RATE/COLLAPSE_REGEN
# above), not death -- death only ever happens through _agent_dies (old age,
# or a future cause), never as a direct consequence of health hitting 0. A
# divine negative health delta is still clamped to stop ONE full point above
# that threshold rather than at it, so a miracle can never itself be the
# thing that flips incapacitated=True -- the "floor above death" the plan's
# Phase 4 section requires documented. Hunger has no such floor: hunger
# reaching 0 does not incapacitate or kill by itself (it only makes the next
# _update_survival tick apply HEALTH_RATE loss instead of HEALTH_REGEN gain),
# so hunger clamps to the ordinary 0..100 survival range like health's
# positive/upper bound does.
GOD_VITALS_HEALTH_FLOOR = 1
GOD_VITALS_DELTA_MAX = 100   # per-command |delta| cap, health and hunger independently
# grant_resource: per-command cap bounds a single miracle's blast radius;
# per-session cap (Decision-adjacent, new in Phase 4) bounds the cumulative
# total across every grant_resource command applied since process start --
# in-memory only (self._god_grant_session_total), like every other God-mode
# counter that is not part of the persisted godState ring buffers.
GOD_GRANT_PER_COMMAND_CAP = 200
GOD_GRANT_SESSION_CAP = 2000
# structure_condition: per-command |delta| cap. Repair/damage both reuse the
# SAME shared _apply_structure_condition_delta helper _tick_structure_decay
# uses (see sim_engine.py's structure-decay section), so disrepair/ruin
# transitions -- including the homeOf homeless handling -- fire through their
# normal narration rather than a parallel code path.
GOD_STRUCTURE_DELTA_MAX = 100
# Town-integrity mass structure commands (repair_structures / clear_ruins).
GOD_REPAIR_STRUCTURES_CONDITION_MAX = 100
GOD_REPAIR_STRUCTURES_BATCH_MAX = 10
GOD_CLEAR_RUINS_BATCH_MAX = 10

# --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 5) ---
# Timed lawgiver modifiers + storyteller events. "One active value per key":
# a new event whose modifiers include a key already held by another ACTIVE
# event is rejected unless it declares replaceEffectId naming that event.
# Base module constants (HUNGER_RATE etc.) are unchanged -- every consumer
# site multiplies its own local delta/amount by _divine_modifier(key), which
# returns exactly 1.0 with no active effect for that key, so a feature-off
# (or effect-free) run is byte-identical to before this phase (see
# specs/08-systems-economy.md "Exact consumer sites and arithmetic").
GOD_MODIFIER_RANGES = {
    "gather_yield_multiplier": (0.25, 3.0),
    "fish_yield_multiplier": (0.0, 3.0),
    "hunger_drain_multiplier": (0.0, 3.0),
    "health_regen_multiplier": (0.0, 3.0),   # fed regen ONLY -- never COLLAPSE_REGEN, see _update_survival
    "starvation_damage_multiplier": (0.0, 3.0),
    "structure_decay_multiplier": (0.0, 3.0),
    "spoilage_multiplier": (0.0, 3.0),
}
# Non-fatal preview warnings: both keys present and stressed above neutral (1.0).
GOD_MODIFIER_CONFLICT_RULES = (
    ("gather_yield_multiplier", "hunger_drain_multiplier",
     "High gather yield with faster hunger drain sends mixed survival signals."),
    ("gather_yield_multiplier", "spoilage_multiplier",
     "High gather yield with faster spoilage wastes abundance before it can be used."),
    ("fish_yield_multiplier", "hunger_drain_multiplier",
     "High fish yield with faster hunger drain sends mixed survival signals."),
    ("health_regen_multiplier", "starvation_damage_multiplier",
     "Stronger fed regen combined with harsher starvation damage sends mixed survival signals."),
    ("gather_yield_multiplier", "structure_decay_multiplier",
     "High gather yield while structures decay faster strains long-term village stability."),
)


def _god_modifier_conflict_warnings(modifiers):
    """Return non-fatal warning strings when modifier keys fight in one envelope."""
    if not isinstance(modifiers, dict) or not modifiers:
        return []
    warnings = []
    for key_a, key_b, message in GOD_MODIFIER_CONFLICT_RULES:
        va = modifiers.get(key_a)
        vb = modifiers.get(key_b)
        if va is None or vb is None:
            continue
        if va > 1.0 and vb > 1.0:
            warnings.append(message)
    return warnings
GOD_EVENT_TITLE_MAX_CHARS = 80        # shorter than GOD_TEXT_MAX_CHARS -- a title is a label, not narration
GOD_STORY_EVENT_MAX_PRIMITIVES = 5    # bounded blast radius for one atomic story event
GOD_STORY_EVENT_MAX_MODIFIERS = len(GOD_MODIFIER_RANGES)  # every key, at most once each

# --- World geometry ---
# WORLD_H was 1000, then 2700 (to stop the village/farm build-out grids from
# overflowing off-canvas). The world-expansion plan raises this again, this
# time to add real additional terrain (districts model, below) rather than
# just more headroom for the same 7 zones: the starter core keeps occupying
# roughly its old ~2600x2700 footprint, and WORLD_W/WORLD_H are set generously
# larger so the remainder is open FRONTIER territory districts can be founded
# into later (see STARTER_DISTRICTS / _maybe_found_district). index.html's
# WORLD_W/WORLD_H MUST be kept in sync.
WORLD_W = 5200
WORLD_H = 5400

# --- Districts: hand-authored starter core + growable frontier ---
# STARTER_DISTRICTS is the immutable, hand-authored blueprint used ONLY to
# seed civilization["districts"] at cold-start (_reset_world). Every runtime
# function reads the LIVE civilization["districts"] dict, never this module
# constant -- that's what lets _maybe_found_district() append new district
# instances later (the open-world mechanism) without a parallel data model.
#
# Entry shape (frozen):
#   {kind, tile, label, bounds:{x1,y1,x2,y2},
#    build_grid: {x0,y0,cols,dx,dy,cap} | None, entryNode}
# `kind` groups districts for resource/tile purposes -- two districts can
# share a kind (two "farm" districts = two farm clusters, and later a third
# founded one). `entryNode` names this district's "front door" in the road
# graph (STARTER_ROAD_NODES, below). Bounds are pairwise non-overlapping
# (enforced by _validate_districts, both at import time and after any
# founding) and, for the 7 starter-core districts, are exactly the original
# ZONE_BOUNDS rectangles from before this refactor, so get_zone/get_district
# resolve identically to the pre-districts get_zone() for existing ground.
STARTER_DISTRICTS = {
    "farm_north": {
        "kind": "farm", "tile": "farm", "label": "FARM",
        "bounds": {"x1": 500, "y1": 110, "x2": 920, "y2": 810},
        "build_grid": {"x0": 520, "y0": 250, "cols": 4, "dx": 105, "dy": 85, "cap": 30},
        "entryNode": "farm_north_gate",
    },
    "forest": {
        "kind": "forest", "tile": "forest", "label": "FOREST",
        "bounds": {"x1": 1030, "y1": 110, "x2": 1550, "y2": 450},
        "build_grid": None, "entryNode": "forest_gate",
    },
    "village_core": {
        "kind": "village", "tile": "village", "label": "VILLAGE",
        "bounds": {"x1": 540, "y1": 960, "x2": 900, "y2": 2540},
        "build_grid": {"x0": 560, "y0": 980, "cols": 4, "dx": 100, "dy": 95, "cap": 30},
        "entryNode": "village_hub",
    },
    "market": {
        "kind": "market", "tile": "market", "label": "MARKET",
        "bounds": {"x1": 970, "y1": 1020, "x2": 1110, "y2": 1120},
        "build_grid": None, "entryNode": "market_gate",
    },
    "beach": {
        "kind": "beach", "tile": "beach", "label": "BEACH",
        "bounds": {"x1": 290, "y1": 100, "x2": 490, "y2": 900},
        "build_grid": None, "entryNode": "beach_gate",
    },
    "cave_east": {
        "kind": "cave", "tile": "cave", "label": "CAVE",
        "bounds": {"x1": 1210, "y1": 1150, "x2": 1540, "y2": 1360},
        "build_grid": None, "entryNode": "cave_east_gate",
    },
    "ocean": {
        "kind": "ocean", "tile": "ocean", "label": None,
        "bounds": {"x1": 0, "y1": 100, "x2": 280, "y2": 900},
        "build_grid": None, "entryNode": "beach_gate",
    },
    # --- World expansion: second instances of buildable kinds, plus a new
    # "workshop" (industrial) kind, occupying a ~1000px-wider eastern strip of
    # the starter core (still well under half of WORLD_W/WORLD_H above) so the
    # fixed roster has real additional ground to build a fuller civilization on.
    "farm_south": {
        "kind": "farm", "tile": "farm", "label": "FARM (SOUTH FIELDS)",
        "bounds": {"x1": 1650, "y1": 110, "x2": 2050, "y2": 710},
        "build_grid": {"x0": 1670, "y0": 250, "cols": 4, "dx": 105, "dy": 85, "cap": 30},
        "entryNode": "farm_south_gate",
    },
    "village_east": {
        "kind": "village", "tile": "village", "label": "EAST VILLAGE",
        "bounds": {"x1": 1650, "y1": 960, "x2": 2050, "y2": 2540},
        "build_grid": {"x0": 1670, "y0": 980, "cols": 4, "dx": 100, "dy": 95, "cap": 30},
        "entryNode": "village_east_gate",
    },
    "workshop_row": {
        "kind": "workshop", "tile": "workshop", "label": "WORKSHOP ROW",
        "bounds": {"x1": 2100, "y1": 110, "x2": 2500, "y2": 710},
        "build_grid": {"x0": 2120, "y0": 250, "cols": 4, "dx": 100, "dy": 90, "cap": 24},
        "entryNode": "workshop_row_gate",
    },
    "cave_deep": {
        "kind": "cave", "tile": "cave", "label": "DEEP CAVE",
        "bounds": {"x1": 2100, "y1": 960, "x2": 2500, "y2": 1560},
        "build_grid": None, "entryNode": "cave_deep_gate",
    },
    # Dedicated burial grounds west of the village (below the beach). The
    # cemetery structure sits on build_grid slot 0; graves use grave_grid with
    # the same spacing as village structures so tombstones never overlap.
    "cemetery_grounds": {
        "kind": "cemetery", "tile": "cemetery", "label": "CEMETERY",
        "bounds": {"x1": 230, "y1": 900, "x2": 530, "y2": 2200},
        "build_grid": {"x0": 340, "y0": 980, "cols": 1, "dx": 100, "dy": 95, "cap": 1},
        "grave_grid": {"x0": 245, "y0": 1100, "cols": 3, "dx": 100, "dy": 95, "cap": 48},
        "entryNode": "cemetery_gate",
    },
}

# kind -> template used by _maybe_found_district() to instantiate a brand new
# district of that kind into a claimed frontier plot. Beach expansion uses
# COASTAL_PAIR_BEACH_TEMPLATE + OCEAN_DISTRICT_TEMPLATE instead (see
# _found_coastal_pair). Forest/cave/market are not frontier-founded; a founded
# "cave" would need real per-district mining logic it doesn't have, so cave
# growth is covered by cave_deep already existing as a second starter site.
DISTRICT_KIND_TEMPLATES = {
    "farm": {"tile": "farm", "grid": {"cols": 4, "dx": 105, "dy": 85, "cap": 30}},
    "village": {"tile": "village", "grid": {"cols": 4, "dx": 100, "dy": 95, "cap": 30}},
    "workshop": {"tile": "workshop", "grid": {"cols": 4, "dx": 100, "dy": 90, "cap": 24}},
}
# Beach/ocean frontier founding always claims an adjacent plot pair (west water,
# east sand). Standalone beach is not in DISTRICT_KIND_TEMPLATES.
COASTAL_PAIR_BEACH_TEMPLATE = {
    "tile": "beach", "grid": {"cols": 3, "dx": 100, "dy": 80, "cap": 18},
}
OCEAN_DISTRICT_TEMPLATE = {"tile": "ocean", "grid": None, "label": None}

# project type -> the district kind it must be built in (farmers build farm
# plots in a farm district, general village builds go up in a village
# district, and the "workshop" structure itself belongs in a workshop/
# industrial district). Falls back to "village" for any type not listed here
# (covers future custom blueprint project types).
PROJECT_KIND = {"house": "village", "wall": "village", "granary": "village",
                "farm_plot": "farm", "workshop": "village"}

# --- Road network: hand-authored starter graph + growable, same runtime-
# mutable rationale as districts (a founded district needs to extend the
# graph, not just read a frozen one). Edges are undirected [a, b] pairs; the
# small size (a dozen-ish nodes even after several foundings) makes recomputing
# all-pairs shortest paths via BFS on every graph change cheap (see
# _recompute_road_paths / ROAD_PATH_CACHE).
STARTER_ROAD_NODES = {
    "village_hub": {"x": 740, "y": 900},
    "farm_north_gate": {"x": 740, "y": 820},
    "forest_gate": {"x": 1090, "y": 460},
    "cave_east_gate": {"x": 1270, "y": 824},
    "beach_gate": {"x": 400, "y": 800},
    "market_gate": {"x": 1040, "y": 1000},
    "east_hub": {"x": 1850, "y": 900},
    "farm_south_gate": {"x": 1850, "y": 680},
    "village_east_gate": {"x": 1850, "y": 960},
    "workshop_row_gate": {"x": 2300, "y": 680},
    "cave_deep_gate": {"x": 2300, "y": 960},
    "cemetery_gate": {"x": 380, "y": 920},
}
STARTER_ROAD_EDGES = [
    ["farm_north_gate", "village_hub"],
    ["village_hub", "forest_gate"],
    ["village_hub", "cave_east_gate"],
    ["village_hub", "beach_gate"],
    ["beach_gate", "cemetery_gate"],
    ["village_hub", "market_gate"],
    ["village_hub", "east_hub"],
    ["east_hub", "farm_south_gate"],
    ["east_hub", "village_east_gate"],
    ["east_hub", "workshop_row_gate"],
    ["east_hub", "cave_deep_gate"],
]

# Frontier: a fixed-size plot grid tiling everything OUTSIDE the starter
# core's reserved footprint. _maybe_found_district() claims one plot at a time
# as a buildable kind fills up and keeps stalling. This is deliberately NOT a
# fully dynamic/streaming world (the outer WORLD_W/WORLD_H bound is fixed and
# known upfront) -- just a generous, genuinely-unclaimed interior that the
# simulation can grow into.
FRONTIER_PLOT_W = 500
FRONTIER_PLOT_H = 600
CORE_RESERVED_BOUNDS = {"x1": 0, "y1": 0, "x2": 2600, "y2": 2700}
MAX_TOTAL_DISTRICTS = 26          # generous safety valve; see _maybe_found_district
DISTRICT_FOUND_STALL_THRESHOLD = 900  # frames of no kind activity before founding

ZONE_NAMES = ["farm", "forest", "village", "market", "beach", "cave", "ocean", "workshop", "cemetery"]

# --- Cadences / tuning (frame-gated, ported) ---
FRAME_MS = 1000.0 / 60.0
TICKS_PER_SEC = 30
TICK_DT = 1.0 / TICKS_PER_SEC
# Movement scale: the browser ran at 60fps (moveScale=1). The engine ticks at
# 30/s, so scale movement by 2 to keep real-time travel speed equivalent.
MOVE_SCALE = 60.0 / TICKS_PER_SEC

SURVIVAL_TICK_FRAMES = 30
MEMORY_TICK_FRAMES = 1800
META_TICK_FRAMES = 2400
ROLE_REVIEW_FRAMES = 1200
BENCHMARK_TICK_FRAMES = 600
FIRST_BENCHMARK_FRAME = 60

HUNGER_RATE = 0.3
HEALTH_RATE = 2
HEALTH_REGEN = 1.5
EAT_THRESHOLD = 65
FOOD_RESTORE = 45
EDIBLE_RESOURCES = ["food", "fish", "meat"]
HEAL_AMOUNT = 25
COLLAPSE_REGEN = 0.5
COLLAPSE_REVIVE_HEALTH = 15
REVIVE_HUNGER = 35          # hunger floor on revival, else 0-hunger re-collapse in ~8s
EDIBLE_RESERVE = 3          # food/fish/meat an agent keeps back from builds/sharing
EDIBLE_SCARCITY_THRESHOLD = 3   # village-wide edible scarcity (matches council threshold)
MEAT_SCARCITY_CAP = 12          # ecology branch meat ratio denominator (~one boar + reserve)
SHARE_RADIUS = 120          # auto-share edibles with a starving neighbour within this range
STARVING_HUNGER = 10        # below this, a foodless agent deterministically seeks the nearest food zone

# Structure effects (STRUCTURE_EFFECTS_ENABLED): buildings do something, and
# soft caps make the Nth duplicate worthless so agents move on to new types.
FARM_PLOTS_PER_EXTRA = 4    # farm plots in the agent's district per +1 edible gathered
FARM_YIELD_BONUS_CAP = 2    # max bonus units per gather, so plots beyond 8/district are waste
HOUSES_PER_NEW_VILLAGER = 3  # each 3 houses raise the population cap by 1 (hard cap: MAX_ROSTER_SIZE)
WORKSHOPS_PER_CRAFT_BONUS = 3  # workshops village-wide per +1 crafted output (max +1)
WALL_SOFT_CAP = 10
WORKSHOP_DISTRICT_CAP = 3   # per buildable village/workshop-kind district
CUSTOM_SOFT_CAP = 5         # per custom/blueprint type (and the granary)
# Structure upgrades: level 1-100 per instance; duplicate builds blocked until
# every existing instance of that type is maxed (forward-only for legacy saves).
STRUCTURE_UPGRADES_ENABLED = True
MAX_STRUCTURE_LEVEL = 100
LEVEL_STEP = 1              # levels gained per upgrade_structure action (1 → 2 → 3 …)
UPGRADE_STAT_STEP = 10        # cost + produce/boost weight tier every N levels
UPGRADE_TIERS = (1, 25, 50, 75, 100)
UPGRADE_COST_BASE = 1       # primary material units; scales with level tier
SPRITE_DESIGN_MAX_ATTEMPTS = 3  # give up on a rejected sprite design turn after this many tries
# Structure footprint model (size-aware placement/overlap): mirrors the
# client's drawn size so the engine can prevent/detect visual overlap after
# upgrades grow a structure's renderScale.
STRUCTURE_PX_SCALE = 5          # mirrors sprites.js STRUCTURE_SCALE
SEED_SPRITE_DIMS = {             # (rows, cols) of sprites.js STRUCTURE_GRIDS
    "house": (8, 8), "workshop": (8, 8), "farm_plot": (6, 8),
    "wall": (6, 6), "cemetery": (6, 6),
}
PROC_SPRITE_DIMS = (9, 10)      # sprites.js proceduralGridForStructure fallback
STRUCTURE_GAP_X = 12             # min clear px between structure footprints
STRUCTURE_GAP_Y = 18             # taller: covers the label drawn at y+height+2
# Agent-driven reorganization: periodic backstop cadence (~10s at 30/s) for
# _maybe_reorganize_structures, and the throttle window for the "no room to
# relocate" activity nudge so a stuck relocation doesn't spam the feed.
REORG_CHECK_FRAMES = 300
REORG_NO_ROOM_NUDGE_FRAMES = 1000
# Type-aware palettes for procedural upgrade sprites (seed types have no stored sprite).
SEED_UPGRADE_PALETTES = {
    "farm_plot": ["#6D4C41", "#8BC34A", "#C5E1A5", "#33691E", "#FFF9C4"],
    "house": ["#8B5A2B", "#C62828", "#F5E6C8", "#5D4037", "#FFEB3B"],
    "workshop": ["#78909C", "#37474F", "#FFD54F", "#5D4037", "#B0BEC5"],
    "wall": ["#9E9E9E", "#616161", "#BDBDBD", "#424242", "#EEEEEE"],
    "cemetery": ["#455A64", "#263238", "#B0BEC5", "#37474F", "#ECEFF1"],
}
EFFECT_TICK_FRAMES = 150     # deterministic structure-effect tick (produces, etc.)
# Ecology regrowth: +1 per ECOLOGY_REGROW_FRAMES (~20s at 30 ticks/s). At ~3
# gathers/min/agent depleting 2× yield, one district needs regrowth slower than
# harvest to reach "depleted" under sustained gathering (old +2/150 was ~8× too fast).
ECOLOGY_REGROW_FRAMES = 600
LEGACY_CUSTOM_PRODUCE = {"resource": "herbs", "amount": 1, "every_ticks": 600, "scope": "village"}
APPROVED_CUSTOM_STALL_FRAMES = 1800  # ~1 min: nudge + elder backstop for unbuilt approvals
APPROVED_CUSTOM_BACKOFF_FRAMES = 5400  # ~3 min cooldown after escalation gives up
STOCK_DEFAULT_MAX = 100
STOCK_REGROW_PER_TICK = 1
STOCK_DEPLETE_MULTIPLIER = 2   # each gather removes 2× the units collected
STOCK_LOW_RATIO = 0.25
STOCK_MIN_YIELD_RATIO = 0.25  # lowest gather yield multiplier when stock is low but > 0
# Phase 2 (living ecosystem): quantize a district's average stock ratio into a
# small number of stages for crop/tree growth (CROP_GROWTH_ENABLED) and
# wildlife density (WILDLIFE_ENABLED). Boundaries mirror the depleted/low/
# fair/ok categories _format_district_stocks_for_prompt already narrates
# (STOCK_LOW_RATIO=0.25, then 0.5) so the visual matches the scarcity the
# engine already tells agents about -- no new thresholds invented.
DISTRICT_ECOLOGY_STAGES = ["barren", "sparse", "healthy", "lush"]
DISTRICT_ECOLOGY_THRESHOLDS = [0.0, STOCK_LOW_RATIO, 0.5]  # boundary ratio between consecutive stages
# Hysteresis margin: a ratio must clear a boundary by this much before the
# stage flips, so a stock hovering on a boundary can't thrash the stage (and
# the terrain-cache rebuild it triggers in the viewer) every /state poll.
DISTRICT_ECOLOGY_HYSTERESIS = 0.05

COLLECT_CAP = 20
STALL_THRESHOLD = 600
# Abandon only after long stalls — scarcity slows funding; crafted-needs projects
# (granary, etc.) get 2× the base window.
PROJECT_ABANDON_THRESHOLD = STALL_THRESHOLD * 10
PROJECT_ABANDON_THRESHOLD_CRAFTED = STALL_THRESHOLD * 20
PROJECT_DEFER_ABANDON_STREAK = 3
PROJECT_DEFER_COOLDOWN = STALL_THRESHOLD * 20  # defer serially-abandoned types ~6.5 min
BLUEPRINT_STALL_THRESHOLD = 1800
# A leader directive is broadcast to every agent's prompt with "Prioritize
# it"; without an expiry it dominates decisions forever (and persists across
# sessions via state.db). ~3 minutes at 30 ticks/s = several think cycles.
DIRECTIVE_TTL_FRAMES = 5400
# Cap on how many behavior_nudge strings get concatenated into one prompt.
# P0 (emergency/survival) nudges always pass through uncapped since they are
# rare; P1-P3 nudges fill the remaining slots in priority order.
MAX_BEHAVIOR_NUDGES = 3
# C3: caps on unbounded/monotonically-growing think-payload lists. Each trims
# only what reaches the PROMPT -- validation (server.py's validate_blueprint)
# either keeps reading a separate, always-full list, or is unaffected because
# the underlying value is already bounded elsewhere (noted per constant).
MAX_REJECTED_BLUEPRINTS_PROMPT = 15  # rest: engine keeps full rejected_blueprints for validation
MAX_APPROVED_PROJECTS_PROMPT = 15  # already <= MAX_APPROVED_CUSTOM in practice; safeguard only
MAX_KNOWN_RESOURCES_PROMPT = 40  # validation gets a separate, always-full known_resource_ids list
MAX_KNOWN_RECIPES_PROMPT = 30  # not read by validate_blueprint; prompt-only
MAX_ACTIVE_RULES_PROMPT = 12  # already <= MAX_ACTIVE_RULES (8) in practice; safeguard only
MAX_NEARBY_AGENTS_PROMPT = 10  # village is 8-12 agents; safeguard only
MAX_IDLE_AGENTS_PROMPT = 8  # elder-only list; safeguard only
MAX_BLUEPRINT_BRIEFS = 4  # per-bucket cap on elder blueprint-council nudge briefs
GOAL_STEP_FRAMES = 45
SAGE_CRITICAL_HEALTH = 30
CRAFT_STALL_THRESHOLD = 1500

# Invention-gated progression (#5.1/#5.2): consecutive elder turns (see
# _schedule_think) that _invention_required() must hold true before
# _maybe_invention_backstop() steps in and assigns the invention task itself.
INVENTION_BACKSTOP_STREAK = 3
# After this many backstop delegations without a valid proposal landing (or
# when no villager is available to task), the elder stops delegating and takes
# the invention-only turn himself.
INVENTION_ELDER_TAKEOVER = 3
# The elder may not re-task the same villager within this window; keeps the
# MAIN RULE from turning every elder turn into an assign_task megaphone.
ELDER_RETASK_COOLDOWN_FRAMES = 1800
# An agent with company nearby that hasn't spoken for this long gets a gentle
# talk_to_nearby nudge (the consecutiveTalks>=2 brake still applies).
SOCIAL_SILENCE_FRAMES = 4500

# Consequential conversations (#5.4): a commitment auto-expires if unhonored
# for this many frames -- roughly 15 think-turns at a typical ~400-frame
# per-agent think interval, mirroring the STALL_THRESHOLD-style frame-gated
# expiries used elsewhere in this file.
COMMITMENT_EXPIRE_FRAMES = 6000

MAX_PENDING_BLUEPRINTS = 5
MAX_PENDING_ROLES = 5
MAX_APPROVED_CUSTOM = 15
MAX_CUSTOM_RESOURCES = 10
MAX_CUSTOM_RECIPES = 12
# Blueprint amnesty (C3, 2026-07-06): rejectedBlueprintIds used to be a
# permanent blacklist -- once rejected, an id could never legitimately be
# re-proposed, mirroring the MAX_APPROVED_CUSTOM deadlock shape. A rejected id
# now expires after this cooldown (~20 min at 30 ticks/s: long enough that the
# elder's verdict means something across many think cycles, short enough that
# a 9h soak sees several amnesty waves).
BLUEPRINT_AMNESTY_FRAMES = STALL_THRESHOLD * 60
# Orphan custom resources (registry entry with no references) retire after this
# window (~40 min at 30 ticks/s: ~2× the blueprint amnesty, so a resource can
# sit unused between approval and first build without being pruned prematurely).
CUSTOM_RESOURCE_RETIRE_FRAMES = STALL_THRESHOLD * 120
# A pending blueprint whose sage review never lands (elder offline/incapacitated
# the whole window) auto-skips the review after this many frames rather than
# blocking approval forever -- same deadlock-avoidance shape as the amnesty
# clock above, just for the review stage instead of the rejection stage.
SAGE_REVIEW_TIMEOUT_FRAMES = STALL_THRESHOLD * 20
MAX_PENDING_RULES = 4
MAX_ACTIVE_RULES = 8
MAX_EMERGENT_ROLES = 8
# Unlike MAX_ACTIVE_RULES (live provisions), the constitution is an
# append-only historical ledger with no cap of its own. Unique per-enactment
# ids for auto-proposed priority rules (see _maybe_advance_rules) mean a long
# soak's enact/repeal cycles would otherwise grow it unboundedly. Trim the
# oldest INACTIVE (non-"active") rows once the ledger exceeds this size;
# every currently-active provision is always preserved regardless of count.
MAX_CONSTITUTION_HISTORY = 200

ROLE_SWITCH_TICK_FRAMES = 120
ROLE_SWITCH_COOLDOWN = 600
AUTOSWITCH_PROTECTED_ROLES = {"elder", "builder", "healer"}
# Phase 1 Sid-parity: survival need fires when this many living agents are at
# or below STARVING_HUNGER and no living food/fish gatherer is present.
ROLE_STARVE_NEED_THRESHOLD = 2
RULES_TICK_FRAMES = 150
RULE_PROPOSE_COOLDOWN = 1500
# _maybe_advance_rules's "keep village law lean" repeal backstop must not be
# able to repeal a rule it (or the propose branch) only just enacted -- without
# a minimum age, tax+priority (the normal 2-rule steady state) triggers an
# immediate propose/repeal oscillation every cooldown window. A few cooldown
# cycles' worth of frames lets a freshly-enacted rule actually do something
# before it's eligible for the amendment-exercise repeal.
RULE_REPEAL_MIN_AGE_FRAMES = RULE_PROPOSE_COOLDOWN * 4

MEME_SEED_ID = "harvest_spirit"
MEME_RIVAL_ID = "river_spirit"
MEME_SEED_IDS = (MEME_SEED_ID, MEME_RIVAL_ID)
MEMES = {
    "harvest_spirit": "The Harvest Spirit rewards those who share food",
    "river_spirit": "The River Spirit blesses fishers who keep the waters free",
}
# Belief -> rule kinds this believer tends to support (Sid-parity Phase 3).
MEME_RULE_AFFINITY = {
    "harvest_spirit": {"rationing", "harvest_quota", "resource_tax"},
    "river_spirit": {"priority"},  # prefers free waters / fish priority over food rationing
}
# Resolved Phase-3 belief mix. These are authoring exemplars, not preloaded
# live beliefs: keeping them out of beliefRegistry preserves the competing
# dual-seed opening and leaves four of MAX_BELIEFS slots for actual authors.
BELIEF_ARCHETYPES = {
    "forest_steward": {
        "id": "forest_steward", "name": "Forest Stewardship",
        "tenet": "The forest stays generous when we harvest with care.",
        "affinity": ["priority"], "kind": "practical",
    },
    "egalitarian": {
        "id": "egalitarian", "name": "Equal Share",
        "tenet": "Every household deserves an equal share of the village stores.",
        "affinity": ["resource_tax"], "kind": "political",
    },
    "dreamwalker": {
        "id": "dreamwalker", "name": "Dreamwalkers",
        "tenet": "Dreams reveal the village's next useful path.",
        "affinity": ["custom"], "kind": "outlier",
    },
}
MEME_SPREAD_PROB = 0.5
MEME_PROXIMITY_PROB = 0.2
MEME_TICK_FRAMES = 90
# Phase 3: beliefs are live authored records, bounded so a long-running
# civilization cannot turn every conversation into unbounded prompt/state work.
MAX_BELIEFS = 6
BELIEF_PITCH_SESSION_CAP = 30
BELIEF_FALLBACK_QUALITY = 0.55
BELIEF_EXISTING_PENALTY = 0.55
BELIEF_RELATIONSHIP_WEIGHT = {"ally": 1.0, "neutral": 0.68, "rival": 0.32}

INBOX_CAP = 6
WORKING_MEM_CAP = 6
SHORT_MEM_CAP = 12
LONG_MEM_CAP = 8
# Wiki-style memory (WIKI_MEMORY flag): hard char cap per named section in
# agent["memoryWiki"] ({"relationships", "goals", "lessons"}).
WIKI_SECTION_CHAR_CAP = 300

VALID_GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
VALID_VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
RULE_KINDS = {"resource_tax", "custom", "priority"}
# A custom rule can only select one of these existing computations. Its
# structured effect is interpreted below; it is never eval'd or executed.
CUSTOM_RULE_ACTIONS = {"collect_resource", "contribute_resources", "craft_item"}
CUSTOM_RULE_MODIFIER_MAX = 3

# Must match Ollama's OLLAMA_NUM_PARALLEL (3, set by scripts/ollama_setup.py;
# see ollama_config.md -- per-model num_ctx, not a shared per-slot budget,
# under Ollama). History below predates the Ollama migration (2026-07-24) and
# refers to the former LM Studio runtime's old context 20000 / parallel 3
# setup (scripts/lms_load.py, removed in the migration's Phase 5). Raised 2->3 on
# 2026-07-11 for +50% think throughput, then dropped 3->2 on 2026-07-14
# (Phase 2, see .claude/plans/only-create-the-plan-linear-iverson.md) to give
# high-stakes thinking turns (needing ~950-1,300 completion tokens on top of a
# ~5,725-6,163 token prompt) more per-slot headroom. Phase 3 (2026-07-14):
# a live analysis of 48 high-stakes samples found thinking gave zero
# measurable reasoning benefit, so it was disabled again
# (THINKING_ENABLED_HIGH_STAKES=False in server.py) and parallel reverted
# back to 3 for max routine-turn throughput.
MAX_CONCURRENT_LLM = 3
# Sid-parity Phase 1: PIANO module calls (perception/social/desire/reflection)
# get their own small pool, bounded independently of MAX_CONCURRENT_LLM, so a
# module backlog can never starve the decision path -- see
# SimEngine.piano_workers / _run_piano_modules. Under Ollama, sim-smart and
# sim-fast each have their own num_ctx (20480 / 4096) rather than a shared
# per-slot token budget; OLLAMA_NUM_PARALLEL=3 must still cover
# MAX_CONCURRENT_LLM (decision calls) and PIANO_CONCURRENT_LLM (module calls)
# running concurrently against their respective model (specs/03-cognition.md,
# ollama_config.md).
PIANO_CONCURRENT_LLM = 2
# Off-tick module reports (e.g. social/reflection on a tick they don't fire)
# are served from the last real report instead of an empty slot, as long as
# it is no more than this many module-ticks stale -- see _run_piano_modules.
PIANO_MODULE_CACHE_TTL = 2
# Cross-module context injection (working-memory half-step): modules see a
# shared "last_reports=" suffix built from every cached report within this
# many module-ticks -- see _run_piano_modules. Deliberately more tolerant of
# staleness than PIANO_MODULE_CACHE_TTL above (which gates the *decision*
# payload's off-tick fills); a 6-tick-old report is still useful orientation
# for a peer module even though it's too stale to stand in as that module's
# own fresh output. Keep these two TTLs as separate constants.
PIANO_CROSS_CONTEXT_TTL = 6
# Always-on PIANO is a pulse, not a continuously occupied GPU queue.  These
# wall-clock knobs intentionally live beside the existing PIANO concurrency
# settings because they govern the same sim-fast pool.
# Dark-gated default. Phase B's contention gate did not pass, so retain the
# original rest window; the optional Phase C night backstop is not attempted.
MODULE_PULSE_INTERVAL_S = 45
MODULE_PULSE_MAX_BATCH = 2
MODULE_NOTE_MAX_AGE_S = 600
MODULE_REFRESH_IDLE_SKIP = True
MODULE_REFRESH_TIMEOUT_S = 60
# Wait budget for a dispatched module future -- strictly above server.py's
# PIANO_MODULE_TIMEOUT_S (15s) HTTP timeout so that timeout, not this one,
# is what fires and gets logged/counted as a drop in the normal case.
PIANO_MODULE_TIMEOUT_WAIT_S = 18
# Orphaned Ollama timeouts (client gave up but server-side generation continues)
# share the sim-smart/sim-fast parallel budget. After this many decision or
# PIANO module timeouts in a row, pause new decision dispatches briefly so
# in-flight orphans can drain -- see _record_llm_orphan_timeout / _schedule_think.
LLM_ORPHAN_TIMEOUT_THRESHOLD = 3
LLM_ORPHAN_COOLDOWN_S = 30.0
LLM_MIN_GAP_MS = 250
# When _schedule_think can't dispatch (worker pool full, cooldown, min-gap),
# the agent retries this soon instead of waiting a full thinkInterval (up to
# 600 frames = 20s) -- a full pool used to silently cost an agent an entire
# cycle, which is how a flagged council member (one invention-only turn per
# debate, no retry) could miss its slot inside COUNCIL_TTL_FRAMES entirely
# (found live 2026-07-08: Sage never got dispatched in a 3-member debate).
# 15 frames = 0.5s, comfortably above LLM_MIN_GAP_MS so it won't self-block.
THINK_RETRY_FRAMES = 15

# Concurrent district builds: how many districts may have an active build
# project at once, village-wide. Start conservative -- with a fixed 8-12 agent
# roster, spreading across too many simultaneous builds means none ever
# finishes. Tune empirically.
MAX_CONCURRENT_PROJECTS = 3

# --- Phase C: physical goods, plural needs & consequence (GOODS_ENABLED) ---
# All deterministic tick mechanics; the LLM only chooses (repair_structure,
# craft the cart, build storage). With the flag off, behavior is exactly
# Phase B: no spoilage/decay/seasons/shelter, carry cap == COLLECT_CAP, no
# cart recipe, no repair action offered, no Season prompt line.
GOODS_ENABLED = True
GOODS_TICK_FRAMES = 900   # slow goods tick (~30s at 30 ticks/s): spoilage + decay + disaster
# Storage: village-wide capacity per resource id. The base is what a small camp
# can pile up without buildings; built structures add capacity via their
# "stores" function effect (Phase A registry; validate_function_block caps each
# entry at 5-100). Sizing: 8 agents each keep EDIBLE_RESERVE (3) = 24 edibles
# village-wide, so base 25 means living hand-to-mouth is safe but any real
# hoard needs storage built for it.
BASE_STORAGE_CAPACITY = 25
# Spoilage: each goods tick, 25% (min 1) of the edible overflow beyond storage
# capacity rots -- stockpile first, then the largest holders, never taking an
# agent below EDIBLE_RESERVE (spoilage must never starve anyone; the escape is
# building storage, eating, or contributing the surplus).
SPOILAGE_RATIO = 0.25
# Cart (the first vehicle): holding one raises the carry cap query-time, the
# same pattern as _gather_yield_bonus. COLLECT_CAP itself stays unchanged.
CART_CARRY_BONUS = 20
# Shelter: one night per DAY_FRAMES (~10.0 min real time). Each *working* house
# shelters HOUSE_SHELTER_OCCUPANTS; unsheltered agents lose a little hunger --
# a nudge, never a punishment: ~1/7 of one meal (FOOD_RESTORE 45), floored at
# SHELTER_HUNGER_FLOOR (20), i.e. a night outside can never push anyone into
# the starvation-reflex band (STARVING_HUNGER 10).
DAY_FRAMES = 18000
HOUSE_SHELTER_OCCUPANTS = 2
SHELTER_HUNGER_PENALTY = 6
SHELTER_HUNGER_FLOOR = 20
# Decay & repair: the designed consumer for the build-rate sprawl (2026-07-06
# audit: ~30 builds/hour with nothing consuming structures). condition is 100
# at build and decays STRUCTURE_DECAY_PER_GOODS_TICK per goods tick (30s).
# 2026-07-07 audit retune: 0.5/tick ruined a 416-structure town in one night
# (~100 min per structure, needing ~4 successful repairs per 30s village-wide
# to hold -- unpayable by 12 agents; 409/416 became ruins, throughput 0).
# Mid retune 0.1/tick (~5.8h to disrepair, ~8.3h to ruin) still failed the
# 2026-07-10 morning soak on a reset world: ruins 11→154 over ~13.5h real /
# ~8.6h sim while agents were heal-spamming, all 15 houses non-working, births
# stalled (pop 16→5). 0.05/tick (~11.7h to disrepair, ~16.7h to ruin) still
# outran repair under soak. Town-integrity retune 0.025/tick: ~23.3h to
# disrepair, ~33.3h to ruin -- paired with repair campaigns, critical backstop
# widening, and ruin cull so sprawl decays across days without offline prune.
# Paired with _maybe_repair_critical (house category) so zero working houses
# can't permanently lock the population cap. A ruin is rebuilt via
# repair_structure for half the original needs (min 1 each) -- cheaper than
# new, the deterministic escape.
STRUCTURE_DECAY_PER_GOODS_TICK = 0.025
STRUCTURE_DISREPAIR_THRESHOLD = 30
REPAIR_CONDITION_RESTORE = 50
# Repair campaigns: autonomous repair goals when village-wide pressure rises.
REPAIR_CAMPAIGN_RUIN_RATIO = 0.15
REPAIR_CAMPAIGN_WORKING_FRAC = 0.5
REPAIR_CAMPAIGN_MAX_ASSIGN = 2
REPAIR_CAMPAIGN_GOAL_TTL = STALL_THRESHOLD * 2
REPAIR_CAMPAIGN_CRITICAL_TYPES = (
    "house", "market", "workshop", "foundry", "granary", "farm_plot",
)
# Ruin cull: in-sim registry deletion for aged, unaffordable ruins.
RUIN_CULL_AGE_FRAMES = DAY_FRAMES
RUIN_CULL_MIN_PER_CALL = 1
RUIN_CULL_MAX_PER_CALL = 3


def structure_condition_tier(structure):
    """Return the authoritative viewer tier for a persisted structure."""
    condition = float(structure.get("condition", 100) or 0)
    if structure.get("isRuin") or condition <= 0:
        return "ruin"
    if condition < STRUCTURE_DISREPAIR_THRESHOLD:
        return "crumbling"
    if condition < 60:
        return "worn"
    return "pristine"
# Disaster: rare random damage so decay isn't perfectly predictable.
# Town-integrity retune: 0.002 per ~30s goods tick => ~once per 250 real min;
# damage softened to (30, 55). STORM_DISASTER_PROB unchanged.
DISASTER_PROB = 0.002
DISASTER_DAMAGE = (30, 55)
# Seasons: a four-season clock derived purely from frameTick (no extra state to
# persist). YEAR_FRAMES is the single canonical in-world year -- 4 real hours
# = exactly 24 day/night cycles (DAY_FRAMES) -- and seasons and aging both
# derive from it, so the GUI calendar, the season clock, and agent ages stay
# in sync. One season = YEAR_FRAMES/4 (~60 min = exactly 6 day/night cycles;
# an overnight soak sees several winters). The season multiplies district
# stock regrowth: spring booms, winter stops regrowth entirely. Escapes:
# stores/granary capacity built before winter (spoilage permitting), and the
# season simply turning. Note: calendar stretch 2026-07-31 (+33% real-time
# cadence: 7.5->10 min days, 45->60 min seasons) -- watch food runway across
# winter on the next soak.
YEAR_FRAMES = 432_000
SEASON_FRAMES = YEAR_FRAMES // 4  # 108_000: one season = 60 min = exactly 6 day/night cycles
SEASONS = ["spring", "summer", "autumn", "winter"]
SEASON_REGROW_MULT = {"spring": 2, "summer": 1, "autumn": 1, "winter": 0}

# --- Phase D: technology tiers & eras (TECH_TREE_ENABLED) ---
# Every structure type and recipe carries a `tier` (default 1; the granary and
# cart are tier 2). A station structure's `unlocks` effect gains an optional
# `tier`: the village tech tier is the highest unlock tier among built WORKING
# stations (workshop=1, the seed Forge=2). Proposing/starting/crafting tier-T
# tech requires village tier >= T, with every refusal surfaced; the
# deterministic escape is that the tier-T station is itself tier T-1 or lower
# (the Forge is a plain tier-1 build needing workshop-crafted planks). With the
# flag off: no tier fields, no gates, no era/council prompt lines -- prompts
# are byte-identical to Phase C.
TECH_TREE_ENABLED = True
# Scheduled, whole-village deliberation. The legacy invention council remains
# available behind this one-flag rollback.
DAILY_COUNCIL_ENABLED = True
MAX_TECH_TIER = 3
# Two-stage blueprint approval: the elder must sage_review_blueprint (a
# geography/resource sanity pass) before approve_blueprint/reject_blueprint is
# accepted on that id. Flag-gated so it can be killed instantly if it ever
# deadlocks approval; with it off, approve_blueprint behaves exactly as before.
SAGE_REVIEW_ENABLED = True
# The wagon (tier-2 vehicle, crafted at the Forge, consumes the Phase C cart):
# query-time effects on its holder, same pattern as the cart.
WAGON_CARRY_BONUS = 40
WAGON_SPEED_MULT = 1.4
# Invention council (diegetic LLM-council, plan Part 6): when the invention
# backstop fires, up to this many idle villagers get parallel invention-only
# turns (their council turn REPLACES their normal think turn -- no added call
# volume). Never fans out when fewer than 2 villagers are idle.
INVENTION_COUNCIL_SIZE = 3
COUNCIL_LOG_CAP = 12                      # persisted debate records (viewer panel)
DAILY_COUNCIL_MIN_LIVING = 2
DAILY_COUNCIL_DISCUSSION_ROUNDS = 2
DAILY_COUNCIL_PHASE_TTL_FRAMES = STALL_THRESHOLD * 8
DAILY_COUNCIL_SESSION_TTL_FRAMES = STALL_THRESHOLD * 30
DAILY_COUNCIL_LOG_CAP = 12
DAILY_COUNCIL_DIGEST_CAP = 5
DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS = 30
# Inherited from EDIBLE_RESERVE misuse at the agenda scan; now independently tunable.
DAILY_COUNCIL_SCARCITY_THRESHOLD = 3   # village-wide holdings at/below this read as "low stores"
DAILY_COUNCIL_SCARCITY_TOPICS = 8      # caps scarce ids and active_projects slices in _daily_council_agenda
# A council with no verdict dissolves after this many frames (STALL_THRESHOLD=600
# frames = 20s at 30fps, so x20 = ~6.7 min). Sized for THINKING_TIMEOUT_S=75s
# per member (server.py) queued behind MAX_CONCURRENT_LLM=2 workers, plus the
# elder's own verdict turn -- was x10 (~3.3 min), too tight once the 2026-07-07
# timeout fix let invention calls actually run to completion instead of
# failing fast, which had been masking how little runway a debate really had.
COUNCIL_TTL_FRAMES = STALL_THRESHOLD * 20
# Era ladder: the highest capability rung held names the era (monotonic -- a
# lost capability never regresses the era). Replaces the vanity level in
# prompts/UI; `level` stays in state for back-compat.
ERA_LADDER = [
    ("Founding Era", None),
    ("Craftsman Era", "crafting"),     # a working craft station (workshop)
    ("Forge Era", "metallurgy"),       # a working tier-2 station (the Forge)
    ("Wagon Era", "vehicles"),         # a vehicle (cart/wagon) in village hands
]

# --- Phase E: market, property & mechanical relationships (ECONOMY_ENABLED) ---
# While a market structure exists (kind="village" so it fits the same
# build_grid districts as house/wall), trade_resource stops bartering 1-for-1
# and becomes a priced exchange in gold, and relationships condition the deal
# (ally discount, rival surcharge/refusal). Prices are a pure query-time
# function of district stock ratio + stockpile depth -- no new tick. With the
# flag off, trade_resource stays exactly the Phase B/C/D barter swap and no
# market/property/wealth code runs, so flag-off prompts/behavior are
# byte-identical.
ECONOMY_ENABLED = True
# Price curve: BASE_PRICE at "comfortable" stock, scaling up as stock (district
# ratio and/or village stockpile depth) drops toward zero -- scarce = expensive.
# Sizing: at full stock (ratio 1.0) price == BASE_PRICE; at zero stock price
# caps at BASE_PRICE * PRICE_SCARCITY_MULT, so no resource can ever demand more
# gold than a villager could plausibly gather toward across a few turns
# (COLLECT_CAP=20, so a 4x spike on a BASE_PRICE=1-3 good tops out under 12g).
BASE_PRICE = {"food": 1, "fish": 1, "meat": 1, "water": 1, "wood": 1, "herbs": 1,
              "stone": 2, "planks": 2, "bricks": 2, "tools": 3, "cart": 4, "wagon": 6}
PRICE_SCARCITY_MULT = 4.0
PRICE_MIN = 1
# Relationship modifiers on the priced-trade path (audit C2 -- the first
# mechanical consumer of agent["relationships"]). Allies get a break, rivals
# pay/charge more; REFUSAL is a hard stop only for rival-priced trades the
# buyer can't afford even at the surcharge (never for barter -- that stays the
# deterministic escape when gold is short or no market exists).
ALLY_PRICE_DISCOUNT = 0.75
RIVAL_PRICE_SURCHARGE = 1.5
# Mint/coin: a genuinely separate currency from gold (gold stays a minable
# commodity and structure-cost input forever). MINT_RATE is how much village
# stockpile gold -> coin each _maybe_mint_coin() call, which fires on the same
# RULES_TICK_FRAMES (150-frame / ~5s) cadence as every other unconditional
# backstop in that batch -- deliberately slow and small so a fresh mint can't
# instantly convert an entire treasury in one tick.
MINT_RATE = 1
# Property: the first agent to build OR repair-from-ruin a house claims it as
# home (stored on the structure as "homeOf"; an agent can hold only one home
# at a time -- claiming a new one releases the old). Homeowners get the Phase
# C nightly shelter benefit automatically (their own house, regardless of
# proximity), so a homeless villager is the one actually competing for the
# nearest-N shelter slots.
HOMELESS_NUDGE_FRAMES = STALL_THRESHOLD * 3  # ~10 min before the nudge repeats

# --- Phase F: population lifecycle & governance depth (LIFECYCLE_ENABLED) ---
# Aging, birth, natural death (elder included -- succession is the design, not
# an edge case), and two rule kinds with teeth (harvest_quota, rationing) that
# bind on the ecology/goods systems Phases B/C built. All deterministic tick
# mechanics gated on one slow tick; the ONLY LLM involvement is exactly one
# lm_complete call per birth event (persona authoring) and one per succession
# candidacy is NOT needed (candidates are deterministic; villagers vote via the
# existing propose_rule/vote_rule scaffold, reused verbatim). With the flag
# off, no agent carries an age, no birth/death/election code runs, and
# RULE_KINDS stays {resource_tax, custom, priority} -- prompts/behavior for
# lifecycle-only kinds are suppressed.
LIFECYCLE_ENABLED = True
# Aging: 1 "year" per LIFECYCLE_TICK_FRAMES (~10s at 30 ticks/s) is far too
# fast for a multi-day soak to show generational turnover in real time, so
# ages advance in small fractional steps. 2026-07-10: 0.02 (~1y/8.3min) wiped
# cohorts overnight; 0.005 (~1y/33min) still felt too fast -- retuned to
# 0.001 (~1y/2.8h, 0→90 in ~10.4 days) so multi-day 24/7 soaks see gradual
# turnover, not near-extinction every night. 2026-07-14: derived from
# YEAR_FRAMES instead of a hand-tuned constant (~1y/4.0h, 0→90 in ~15.0
# days) so aging stays locked to the same canonical year as the season clock
# and GUI calendar. Smoke-testing forces this by temporarily shrinking the
# gate/increment, never by waiting.
LIFECYCLE_TICK_FRAMES = 300
AGE_YEARS_PER_TICK = LIFECYCLE_TICK_FRAMES / YEAR_FRAMES  # = 1/1440: exactly 1 year per YEAR_FRAMES (4.0 h)
ADULT_AGE = 18                      # below this, an agent cannot be a birth parent or election candidate
ELDER_AGE = 55                      # life-stage label switches to "elder" (age word only, not the elder ROLE)
MAX_LIFE_EXPECTANCY = 90            # death chance saturates approaching this age
DEATH_CHANCE_START_AGE = 65         # natural death rolls begin at this age
DEATH_CHANCE_PER_TICK = 0.0006      # base per-gate roll once past DEATH_CHANCE_START_AGE, scaled by age
POPULATION_FLOOR = 4                # never below this many non-incapacitated adults; death defers, logged
# Birth: needs housing headroom (population cap > current population, the
# same signal _maybe_welcome_newcomer uses), a food surplus (stockpile+held
# edibles above a small multiple of the roster), and two ally adults sharing a
# district. Gated to at most one birth per interval so a housing boom can't
# spawn a crowd in one tick.
BIRTH_CHECK_FRAMES = LIFECYCLE_TICK_FRAMES
BIRTH_FOOD_SURPLUS_PER_AGENT = 4    # stockpile+carried edibles must exceed this * population
BIRTH_MIN_INTERVAL_FRAMES = STALL_THRESHOLD * 6  # ~2 min cooldown between births village-wide
BIRTH_STARTING_SKILL_PENALTY = True  # newborns start at the "young" life stage (see _life_stage)
NEWBORN_GOODS_SHARE = 0.15          # newborn inherits this fraction of a parent's held goods
# Succession: on the elder's death, an election runs on the existing
# propose_rule/vote_rule machinery -- one pending rule per eligible candidate
# (kind "succession"), same quorum tally as resource_tax. Deterministic tie
# break (see _resolve_succession_tie) guarantees the arc always completes.
SUCCESSION_ELECTION_TTL_FRAMES = STALL_THRESHOLD * 8  # ~13 min: any candidate short of quorum by then, deterministic tiebreak decides
# Governance (I4): harvest_quota caps an agent's gathers of one resource in one
# district per rationing period; rationing caps stockpile withdrawals when
# storage is low. Both proposable/votable exactly like resource_tax.
HARVEST_QUOTA_PERIOD_FRAMES = STALL_THRESHOLD * 3   # ~5 min per quota period
RATIONING_STORAGE_LOW_RATIO = 0.5   # rationing only actually restricts below this storage-utilization ratio
RATIONING_WITHDRAW_CAP = 3          # max units withdrawn from stockpile per agent per rationing check while low
# --- Phase G: knowledge, culture, factions (CULTURE_ENABLED) ---
# Skills-by-practice + teaching, a library that persists a dead agent's
# knowledge, a village chronicle folded into prompts, meme mutation (ONE
# event-driven lm_complete call, capped per session, mirroring Phase F's
# one-call-per-birth discipline), and deterministic personality drift from
# life events. All deterministic mechanics ride the existing slow tick gates
# (no new per-tick LLM calls); the only LLM involvement is the capped meme
# mutation. With the flag off, no agent carries a "skills" dict, no chronicle/
# library state exists, and prompts are byte-identical to Phase F.
CULTURE_ENABLED = True
# Skills: one float level (0..SKILL_MAX_LEVEL) per practiced verb, rising a
# small fixed amount on each successful use (deterministic -- no roll needed,
# matching the "practice raises it" framing) and feeding a small yield/output
# bonus so skill is legible in the numbers, not just flavor text.
SKILL_KINDS = ("gather", "craft", "build", "heal", "reflection")
SKILL_MAX_LEVEL = 10.0
SKILL_PRACTICE_GAIN = 0.15           # per successful practice of that verb
SKILL_BONUS_DIVISOR = 4.0            # +1 yield/output per this many skill levels (see _skill_bonus)
SKILL_HEAL_BONUS_PER_LEVEL = 0.6     # extra health restored per heal skill level
# Teaching: a talk_to_nearby message containing a teach-intent keyword and a
# recognized skill kind transfers a fraction of the SPEAKER's level in that
# skill to the recipient (apprenticeship) -- deterministic keyword check, no
# extra LLM call, no new action verb (mirrors the plan's change-map hint).
TEACH_KEYWORDS = ("teach", "train", "show you how", "apprentice", "mentor")
TEACH_TRANSFER_FRACTION = 0.3
# Library: a seed structure (see SEED_STRUCTURE_FUNCTIONS) that, while
# working, persists a dying agent's best skill so children/newcomers can
# still study it (a goal, not a new action) -- death stops mattering as total
# knowledge loss. Capped so the registry can't grow unbounded over a long
# soak (oldest entry retires first, the same discipline as blueprint/resource
# retirement elsewhere in the file).
LIBRARY_KNOWLEDGE_CAP = 12
LIBRARY_STUDY_GAIN = 0.4             # skill gained per study session at the library
LIBRARY_STUDY_WEIGHT_CAP = 5         # study-gain upgrade-weight cap (knowledge-capacity cap stays 10)
# Chronicle: a capped ring of major village-level events, summarized into one
# prompt line ("Village history: ...") so a long-running village develops a
# legible past without growing the prompt unboundedly.
CHRONICLE_CAP = 100
CHRONICLE_PROMPT_ENTRIES = 3         # how many recent entries to fold into the prompt line
COUNCIL_DIGEST_PROMPT_ENTRIES = 2    # newest compact Daily Council records per think payload
# The viewer chronicle is deliberately narrower than the prompt-facing ring:
# only named historical milestones belong beside the raw Activity feed.
CHRONICLE_MILESTONE_KINDS = frozenset({
    "death", "burial", "election", "belief_founded", "belief_adoption",
    "meme_mutation", "knowledge_preserved", "disaster", "district_founded",
    "emergency_measure",
    # Sovereign God mode (Phase 2): a proclamation's chronicle entry. Viewer
    # rendering of this kind is out of scope until the later Divine Console
    # phase, but the milestone-set membership itself is data-shape only.
    "divine",
})
# Memes: mutation is capped and event-driven -- at most one lm_complete call
# per mutation ATTEMPT (itself gated to a low probability on ordinary
# proximity spread), and a hard per-session ceiling so a long soak can never
# turn this into a background LLM-spam loop.
MEME_MUTATION_PROB = 0.08            # chance an ordinary belief transmission also mutates the text
MEME_MUTATION_SESSION_CAP = 30        # hard ceiling on lm_complete calls for meme mutation, this process's lifetime
# Belief-driven bias: believers in the seed harvest_spirit meme contribute
# food more readily (a deterministic behavioral tilt, not a new action) --
# folded into _pick_contribution_resource so it costs no new template line.
HARVEST_SPIRIT_CONTRIB_BOOST = True
# Personality drift: major life events append one short trait clause to the
# agent's existing persona/personality text (deterministic templates only).
# Capped so a long-lived elder doesn't accumulate an unbounded run-on string.
PERSONALITY_DRIFT_CAP = 3
# --- Cemetery & burial (permanent-death handling, CEMETERY_ENABLED) ---
# A permanent death (LIFECYCLE_ENABLED) used to leave the corpse lying
# wherever it fell -- incapacitated forever, at a random world position, with
# no in-fiction acknowledgement. This closes that gap: a seed Cemetery
# structure (station pattern, like Market/Library) + a deterministic backstop
# that (a) has the village build one the first time it's needed and (b)
# either an agent organically bury_agent's the dead or, after a grace window,
# the backstop does it itself -- so no corpse waits forever. A collapsed-but-
# not-dead agent (deathFrame is None) is never eligible; burial is strictly
# for LIFECYCLE_ENABLED's permanent death.
CEMETERY_ENABLED = True
BURY_CONTACT_DIST = 80                # matches heal_agent's contact radius
CONFRONT_CONTACT_DIST = 80            # bounded PvP adjacency (heal/bury/trade parity)
CONFRONT_DAMAGE = 10
CONFRONT_INCAP_HEALTH = 1             # non-lethal floor unless target already critical
CONFRONT_LETHAL_THRESHOLD = 15
CONFRONT_FLEE_DIST = 60
CONFRONT_COOLDOWN_FRAMES = STALL_THRESHOLD * 4
CONFRONT_PRESSURE_WINDOW_FRAMES = STALL_THRESHOLD * 2
FORCED_HUNT_GOAL_TTL = STALL_THRESHOLD
BURIAL_BACKSTOP_FRAMES = STALL_THRESHOLD * 3  # ~1 min grace for organic bury_agent before the backstop buries directly

# --- Path 1: Minecraft-like world depth (PATH1_ENABLED) ---
PATH1_ENABLED = True
INDUSTRY_ENABLED = True
TOOL_TIERS_ENABLED = True
COMPOSABLE_BUILD_ENABLED = True
TERRAIN_TILES_ENABLED = True
PATH1_DIPLOMACY_ENABLED = True
TIER3_CONTENT_ENABLED = True
PRESSURE_LOOP_ENABLED = True
ENV_EFFECTS_ENABLED = True
LIBRARY_SCALING_ENABLED = True
TRANSIT_ENABLED = True
ECONOMY_SINKS_ENABLED = True
COMFORT_EVERY_N_GOODS_TICKS = 4  # comfort consumption fires every Nth goods tick, not every one


def path1_on(subflag=None):
    """True when a Path 1 sub-flag is active. PATH1_ENABLED bundles all on."""
    if PATH1_ENABLED:
        return True
    if subflag:
        return globals().get(subflag, False)
    return False


if LIFECYCLE_ENABLED:
    # New governable rule kinds (I4) + the deterministic succession-ballot
    # kind, layered onto the existing set so a flag-off village keeps
    # {resource_tax, custom, priority} and byte-identical propose_rule
    # validation for those kinds.
    RULE_KINDS = RULE_KINDS | {"harvest_quota", "rationing", "succession"}
if path1_on("PATH1_DIPLOMACY_ENABLED"):
    RULE_KINDS = RULE_KINDS | {"treaty"}

# --- Registries (ported from index.html) ---
PROJECT_TEMPLATES = {
    "house": {"name": "House", "needs": {"wood": 3, "stone": 1, "food": 1, "fish": 1}, "visualStyle": "house"},
    "farm_plot": {"name": "Farm Plot", "needs": {"wood": 2, "food": 1, "herbs": 1, "water": 1}, "visualStyle": "farm_plot"},
    "workshop": {"name": "Workshop", "needs": {"wood": 3, "stone": 2, "gold": 1}, "visualStyle": "workshop"},
    "wall": {"name": "Wall", "needs": {"stone": 3, "gold": 1}, "visualStyle": "wall"},
}
PROJECT_ORDER = ["house", "farm_plot", "workshop", "wall"]
if CRAFTING_ENABLED:
    PROJECT_TEMPLATES["granary"] = {
        "name": "Granary", "needs": {"planks": 2, "bricks": 2, "food": 1}, "visualStyle": "house"
    }
    PROJECT_ORDER.append("granary")

# Seed structure functions (Phase A): every built type declares mechanical effects.
# Custom blueprints must supply their own function block; these cover seed templates.
SEED_STRUCTURE_FUNCTIONS = {
    "house": {"houses": {"every_n": HOUSES_PER_NEW_VILLAGER}},
    "farm_plot": {
        "boosts": [{
            "kind": "gather",
            "resources": list(EDIBLE_RESOURCES),
            "every_n": FARM_PLOTS_PER_EXTRA,
            "bonus": 1,
            "max_bonus": FARM_YIELD_BONUS_CAP,
            "scope": "district",
        }],
    },
    "workshop": {
        "unlocks": [{"kind": "craft", "station": "workshop"}],
        "boosts": [{
            "kind": "craft",
            "station": "workshop",
            "every_n": WORKSHOPS_PER_CRAFT_BONUS,
            "bonus": 1,
            "max_bonus": 1,
            "scope": "village",
        }],
    },
    "wall": {
        "produces": [{
            "resource": "stone",
            "amount": 1,
            "every_ticks": 1800,
            "scope": "village",
        }],
    },
}
if CRAFTING_ENABLED:
    SEED_STRUCTURE_FUNCTIONS["granary"] = {
        "produces": [{
            "resource": "food",
            "amount": 1,
            "every_ticks": 1200,
            "scope": "village",
        }],
    }
    if GOODS_ENABLED:
        # Phase C: the granary finally does what its name says -- the seed
        # `stores` effect (real storage capacity, spoilage headroom). Gated on
        # the flag so the flag-off effect vector matches Phase B exactly.
        SEED_STRUCTURE_FUNCTIONS["granary"]["stores"] = [
            {"resource": "food", "capacity": 40},
            {"resource": "fish", "capacity": 20},
        ]

# Terraform projects (Phase B): funded like builds but mutate terrain/stocks.
TERRAFORM_TEMPLATES = {
    "plant_grove": {
        # Needs must stay fundable in a FRESH world: base/gatherable resources
        # only (herbs only exists once a blueprint invents it — a depleted
        # forest must never depend on an uninvented resource to recover).
        "name": "Plant Grove",
        "needs": {"wood": 2, "food": 1},
        "kind": "forest",
        "function": {
            "modifies": [{
                "target": "stock", "resources": ["wood", "herbs"],
                "set_ratio": 0.85, "scope": "district",
            }],
        },
    },
    "clear_field": {
        "name": "Clear Field",
        "needs": {"wood": 1, "stone": 1},
        "kind": "farm",
        "function": {
            "modifies": [{
                "target": "stock", "resources": ["food"],
                "set_ratio": 1.0, "scope": "district",
            }],
        },
    },
    "extend_beach": {
        "name": "Extend Beach",
        "needs": {"stone": 2, "wood": 1},
        "kind": "beach",
        "function": {
            "modifies": [{
                "target": "stock", "resources": ["fish"],
                "set_ratio": 0.9, "scope": "district",
            }],
            "found_coastal_pair": True,
        },
    },
}
TERRAFORM_FUNCTIONS = {tid: tmpl["function"] for tid, tmpl in TERRAFORM_TEMPLATES.items()}
KIND_TERRAFORM = {"farm": "clear_field", "forest": "plant_grove", "beach": "extend_beach"}

BASE_RESOURCES = {
    "food": {"name": "Food", "gatherZone": "farm", "color": "#4CAF50"},
    "wood": {"name": "Wood", "gatherZone": "forest", "color": "#795548"},
    "gold": {"name": "Gold", "gatherZone": "cave", "color": "#FFC107"},
    "stone": {"name": "Stone", "gatherZone": "cave", "color": "#9E9E9E"},
    "fish": {"name": "Fish", "gatherZone": "beach", "color": "#4FC3F7"},
    "meat": {"name": "Meat", "gatherZone": None, "color": "#C62828"},
    "herbs": {"name": "Herbs", "gatherZone": "forest", "color": "#8BC34A"},
    "water": {"name": "Water", "gatherZone": "village", "color": "#03A9F4"},
}
if ECONOMY_ENABLED:
    # Coin: a currency, not a commodity -- no gatherZone (never foraged/mined
    # directly, unlike gold). Registered here purely/always once ECONOMY_ENABLED
    # is on, mirroring how gold itself is always seeded; whether it can ever
    # be MINTED is gated separately by _mint_active() (needs a working Mint).
    BASE_RESOURCES["coin"] = {"name": "Coin", "gatherZone": None, "color": "#F5D76E"}
CRAFTED_RESOURCES = {
    "planks": {"name": "Planks", "gatherZone": None, "color": "#C19A6B", "crafted": True},
    "bricks": {"name": "Bricks", "gatherZone": None, "color": "#B7410E", "crafted": True},
    "tools": {"name": "Tools", "gatherZone": None, "color": "#90A4AE", "crafted": True},
} if CRAFTING_ENABLED else {}
SEED_RECIPES = {
    "planks": {"name": "Planks", "inputs": {"wood": 1}, "station": "workshop"},
    "bricks": {"name": "Bricks", "inputs": {"stone": 2}, "station": "workshop"},
    "tools": {"name": "Tools", "inputs": {"wood": 2, "stone": 1}, "station": "workshop"},
} if CRAFTING_ENABLED else {}
if CRAFTING_ENABLED and GOODS_ENABLED:
    # Phase C: the cart, the first vehicle -- a crafted good whose holder gets
    # a higher carry cap (see _carry_cap). Costs a craft chain (wood -> planks
    # -> cart at the workshop), so it is earned, not named into existence.
    CRAFTED_RESOURCES["cart"] = {"name": "Cart", "gatherZone": None,
                                 "color": "#A1887F", "crafted": True}
    SEED_RECIPES["cart"] = {"name": "Cart", "inputs": {"planks": 2, "wood": 2},
                            "station": "workshop"}

if TECH_TREE_ENABLED:
    # Phase D seed tiers: seeds default to tier 1; the granary and cart are the
    # first tier-2 tech (reachable only once the Forge raises the village tier).
    for _tid, _tmpl in PROJECT_TEMPLATES.items():
        _tmpl["tier"] = 2 if _tid == "granary" else 1
    for _rid, _recipe in SEED_RECIPES.items():
        _recipe["tier"] = 2 if _rid == "cart" else 1
    # The Forge: the seed tier-2 STATION. Itself plain tier-1 tech (the
    # deterministic escape: the station for tier N is always buildable at tier
    # N-1) -- its planks come from the workshop, closing the chain
    # workshop -> planks -> Forge -> tier-2 tech (cart, wagon, tier-2 blueprints).
    PROJECT_TEMPLATES["forge"] = {
        "name": "Forge",
        "needs": ({"stone": 3, "planks": 2, "gold": 1} if CRAFTING_ENABLED
                  else {"stone": 3, "wood": 2, "gold": 1}),
        "visualStyle": "workshop", "tier": 1,
    }
    PROJECT_ORDER.append("forge")
    PROJECT_KIND["forge"] = "village"
    SEED_STRUCTURE_FUNCTIONS["forge"] = {
        "unlocks": [{"kind": "craft", "station": "forge", "tier": 2}],
        "produces": [{"resource": "tools", "amount": 1, "every_ticks": 2400,
                      "scope": "village"}] if CRAFTING_ENABLED else [],
    }
    if CRAFTING_ENABLED and GOODS_ENABLED:
        # The wagon: the cart's tier-2 upgrade path (consumes the cart).
        # Crafted at the Forge; query-time effects on the holder: a bigger
        # carry cap than the cart AND faster movement (_carry_cap /
        # _vehicle_speed_mult). The audit's "cars" answer -- reachable only
        # through workshop -> planks -> forge -> cart -> wagon.
        CRAFTED_RESOURCES["wagon"] = {"name": "Wagon", "gatherZone": None,
                                      "color": "#8D6E63", "crafted": True}
        # Station stays the workshop ZONE (stations are zone kinds); the Forge
        # requirement is expressed through the tier-2 gate, not the zone.
        SEED_RECIPES["wagon"] = {"name": "Wagon",
                                 "inputs": {"cart": 1, "planks": 2, "tools": 1},
                                 "station": "workshop", "tier": 2}

if ECONOMY_ENABLED:
    # The market: the seed price-unlock STATION. Plain tier-1 (buildable in
    # any village-kind district, same as house/wall) -- the deterministic
    # escape means a village never needs an uninvented resource to reach
    # pricing. Its "unlocks" effect is a new kind ("pricing") consulted by
    # _market_active(); it produces nothing on its own.
    PROJECT_TEMPLATES["market"] = {
        "name": "Market",
        "needs": {"wood": 2, "stone": 2, "gold": 2},
        "visualStyle": "workshop",
        **({"tier": 1} if TECH_TREE_ENABLED else {}),
    }
    PROJECT_ORDER.append("market")
    PROJECT_KIND["market"] = "village"
    SEED_STRUCTURE_FUNCTIONS["market"] = {
        "unlocks": [{"kind": "pricing", "station": "market"}],
    }

    # The mint: the seed currency-unlock STATION. Plain tier-1, buildable in
    # any village-kind district exactly like market/library/cemetery -- the
    # deterministic escape means a village never needs an uninvented resource
    # (or a market first) to reach a mint. Its "unlocks" effect is a new kind
    # ("currency") consulted by _mint_active(); once WORKING it also feeds
    # _maybe_mint_coin() (the periodic backstop in the RULES_TICK_FRAMES
    # batch) that converts the village's gold stockpile into coin. Coin is
    # distinct from gold: gold stays a minable/spendable commodity forever,
    # coin exists only once minted -- see specs/08-systems-economy.md.
    PROJECT_TEMPLATES["mint"] = {
        "name": "Mint",
        "needs": {"stone": 3, "gold": 3},
        "visualStyle": "workshop",
        **({"tier": 1} if TECH_TREE_ENABLED else {}),
    }
    PROJECT_ORDER.append("mint")
    PROJECT_KIND["mint"] = "village"
    SEED_STRUCTURE_FUNCTIONS["mint"] = {
        "unlocks": [{"kind": "currency", "station": "mint"}],
    }

if CULTURE_ENABLED:
    # The Library: the seed knowledge-persistence STATION. Plain tier-1,
    # buildable like house/wall/market (the deterministic escape -- a village
    # never needs an uninvented resource to preserve knowledge). Its "unlocks"
    # effect is a new kind ("knowledge") consulted by _library_active(); the
    # actual persistence mechanic (surviving a dead agent's best skill) lives
    # in civilization["libraryKnowledge"], not in the function block, so it
    # needs no new produces/boosts vector.
    PROJECT_TEMPLATES["library"] = {
        "name": "Library",
        "needs": {"wood": 3, "stone": 1, "gold": 1},
        "visualStyle": "workshop",
        **({"tier": 1} if TECH_TREE_ENABLED else {}),
    }
    PROJECT_ORDER.append("library")
    PROJECT_KIND["library"] = "village"
    SEED_STRUCTURE_FUNCTIONS["library"] = {
        "unlocks": [{"kind": "knowledge", "station": "library"}],
    }

if CEMETERY_ENABLED:
    # The Cemetery: the seed burial STATION. Plain tier-1, buildable like
    # house/wall/market/library (the deterministic escape -- a village never
    # needs an uninvented resource to bury its dead). Its "unlocks" effect is
    # a new kind ("burial") consulted by _working_cemeteries(); the actual
    # burial mechanic (moving a corpse to a grave slot) lives in
    # _bury_agent_at, not in the function block.
    PROJECT_TEMPLATES["cemetery"] = {
        "name": "Cemetery",
        "needs": {"stone": 3, "wood": 1},
        "visualStyle": "cemetery",
        **({"tier": 1} if TECH_TREE_ENABLED else {}),
    }
    PROJECT_ORDER.append("cemetery")
    PROJECT_KIND["cemetery"] = "cemetery"
    SEED_STRUCTURE_FUNCTIONS["cemetery"] = {
        "unlocks": [{"kind": "burial", "station": "cemetery"}],
    }

# Path 1 constants + registry extensions (flags defined above).
TILE_CELL = 40
TILE_CAP_PER_DISTRICT = 200
BLOCK_REFUND_RATIO = 0.5
# District kinds whose terrain grid defaults to something other than "soil"
# (see _ensure_district_terrain) -- these can never be dug for stone.
NON_DIGGABLE_DISTRICT_KINDS = {"forest", "beach", "cave", "ocean"}
TOOL_TIER_ORDER = ("wooden_pick", "stone_pick", "iron_pick")
TOOL_TIER_LEVEL = {"wooden_pick": 1, "stone_pick": 2, "iron_pick": 3}
RESOURCE_MIN_TOOL = {
    "stone": "wooden_pick",
    "copper_ore": "stone_pick",
    "iron_ore": "iron_pick",
}
TOOL_YIELD_BONUS = 1
TERRAIN_TYPES = ("soil", "rock", "grove", "water")
GOD_ARCHITECT_PAINT_TERRAINS = frozenset((*TERRAIN_TYPES, "sand"))
BLOCK_TYPES = {
    "wall": {"cost": {"wood": 1}, "shelter": True},
    "floor": {"cost": {"wood": 1}, "shelter": False},
    "door": {"cost": {"wood": 2}, "shelter": False},
    "fence": {"cost": {"wood": 1}, "shelter": True},
}
NIGHT_FRACTION = 0.25
NIGHT_EXPOSURE_DAMAGE = 2
WILDLIFE_EVENT_PROB = 0.02
WILDLIFE_GUARD_RADIUS = 120
SETTLEMENT_STRUCT_THRESHOLD = 5
SETTLEMENT_POP_THRESHOLD = 6
CARAVAN_CARRY_MIN = 3
CARAVAN_LOG_CAP = 20              # bounded ring -- oldest caravan deliveries drop first
CARAVAN_VEHICLE_RESOURCES = frozenset({"cart", "wagon"})
TREATY_TARIFF_MAX = 0.25
PATH1_GRID_COLS = 8
PATH1_GRID_ROWS = 8

if path1_on("INDUSTRY_ENABLED"):
    _P1_BASE = {
        "clay": {"name": "Clay", "gatherZone": "beach", "color": "#BCAAA4"},
        "sand": {"name": "Sand", "gatherZone": "beach", "color": "#FFE082"},
        "copper_ore": {"name": "Copper Ore", "gatherZone": "cave", "color": "#D84315"},
        "iron_ore": {"name": "Iron Ore", "gatherZone": "cave", "color": "#5D4037"},
    }
    BASE_RESOURCES.update(_P1_BASE)
    _P1_CRAFTED = {
        "charcoal": {"name": "Charcoal", "gatherZone": None, "color": "#424242", "crafted": True},
        "copper_ingot": {"name": "Copper Ingot", "gatherZone": None, "color": "#FF7043", "crafted": True},
        "iron_ingot": {"name": "Iron Ingot", "gatherZone": None, "color": "#78909C", "crafted": True},
        "rope": {"name": "Rope", "gatherZone": None, "color": "#A1887F", "crafted": True},
        "cloth": {"name": "Cloth", "gatherZone": None, "color": "#F48FB1", "crafted": True},
        "wooden_pick": {"name": "Wooden Pick", "gatherZone": None, "color": "#8D6E63", "crafted": True},
        "stone_pick": {"name": "Stone Pick", "gatherZone": None, "color": "#9E9E9E", "crafted": True},
        "iron_pick": {"name": "Iron Pick", "gatherZone": None, "color": "#607D8B", "crafted": True},
    }
    CRAFTED_RESOURCES.update(_P1_CRAFTED)
    SEED_RECIPES.update({
        "charcoal": {"name": "Charcoal", "inputs": {"wood": 2}, "station": "workshop"},
        "copper_ingot": {"name": "Copper Ingot", "inputs": {"copper_ore": 1, "charcoal": 1},
                         "station": "workshop"},
        "iron_ingot": {"name": "Iron Ingot", "inputs": {"iron_ore": 1, "charcoal": 1},
                       "station": "workshop"},
        "rope": {"name": "Rope", "inputs": {"wood": 1}, "station": "workshop"},
        "cloth": {"name": "Cloth", "inputs": {"herbs": 2}, "station": "workshop"},
        "wooden_pick": {"name": "Wooden Pick", "inputs": {"wood": 3}, "station": "workshop"},
        "stone_pick": {"name": "Stone Pick", "inputs": {"stone": 2, "wood": 1}, "station": "workshop"},
        "iron_pick": {"name": "Iron Pick", "inputs": {"iron_ingot": 1, "wood": 1}, "station": "workshop"},
    })
    PROJECT_TEMPLATES["kiln"] = {
        "name": "Kiln", "needs": {"stone": 3, "wood": 2},
        "visualStyle": "workshop", **({"tier": 1} if TECH_TREE_ENABLED else {}),
    }
    PROJECT_ORDER.append("kiln")
    PROJECT_KIND["kiln"] = "workshop"
    SEED_STRUCTURE_FUNCTIONS["kiln"] = {
        "unlocks": [{"kind": "craft", "station": "kiln"}],
        "produces": [{"resource": "charcoal", "amount": 1, "every_ticks": 1800, "scope": "district"}],
    }
    if path1_on("TIER3_CONTENT_ENABLED"):
        PROJECT_TEMPLATES["harbor"] = {
            "name": "Harbor", "needs": {"planks": 3, "stone": 2, "rope": 1},
            "visualStyle": "dock", **({"tier": 2} if TECH_TREE_ENABLED else {}),
        }
        PROJECT_TEMPLATES["mill"] = {
            "name": "Mill", "needs": {"planks": 2, "stone": 2, "wood": 2},
            "visualStyle": "farm_plot", **({"tier": 2} if TECH_TREE_ENABLED else {}),
        }
        PROJECT_TEMPLATES["foundry"] = {
            "name": "Foundry", "needs": {"iron_ingot": 2, "stone": 3, "bricks": 2},
            "visualStyle": "workshop", **({"tier": 3} if TECH_TREE_ENABLED else {}),
        }
        for tid in ("harbor", "mill", "foundry"):
            PROJECT_ORDER.append(tid)
            PROJECT_KIND[tid] = "village" if tid != "harbor" else "beach"
        SEED_STRUCTURE_FUNCTIONS["harbor"] = {
            "produces": [{"resource": "fish", "amount": 1, "every_ticks": 1500, "scope": "district"}],
            "boosts": [{"kind": "gather", "resources": ["fish"], "every_n": 1, "bonus": 1,
                        "max_bonus": 2, "scope": "district"}],
        }
        SEED_STRUCTURE_FUNCTIONS["mill"] = {
            "boosts": [{"kind": "gather", "resources": list(EDIBLE_RESOURCES), "every_n": 1,
                        "bonus": 1, "max_bonus": 2, "scope": "district"}],
        }
        SEED_STRUCTURE_FUNCTIONS["foundry"] = {
            "unlocks": [{"kind": "craft", "station": "foundry", "tier": 3}],
            "produces": [{"resource": "iron_ingot", "amount": 1, "every_ticks": 2400, "scope": "village"}],
        }
        if TECH_TREE_ENABLED:
            ERA_LADDER.extend([
                ("Harbor Era", "harbor"),
                ("Mill Era", "mill"),
            ])

if TECH_TREE_ENABLED:
    ERA_LADDER.append(("Civic Era", "civilization"))
