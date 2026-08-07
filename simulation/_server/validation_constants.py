"""Blueprint/role/sprite validation constants, split out of server.py (Phase 5
modularization, pure move, no behavior change).

Shared by decision_validation.py (the actual validators), prompt_format.py
(format_reserved_structure_ids needs SEED_PROJECT_IDS), and server.py itself
(DECISION_SCHEMA's sprite bounds, build_invention_prompt's taken-id set, and
the Phase D TECH_TREE_ENABLED amendment all reference these directly). Kept
as its own module (rather than folded into decision_validation.py) so
prompt_format.py can import SEED_PROJECT_IDS without an import cycle back
through decision_validation.py (which itself imports from prompt_format.py).
"""

import re

# --- Blueprint validation constants ---
GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
BASE_RESOURCE_IDS = {"food", "wood", "gold"}
SEED_PROJECT_IDS = {"house", "farm_plot", "workshop", "wall"}
TERRAFORM_PROJECT_IDS = frozenset({"plant_grove", "clear_field", "extend_beach"})
KIND_TO_TERRAFORM = {"farm": "clear_field", "forest": "plant_grove", "beach": "extend_beach"}
TERRAFORM_KIND = {v: k for k, v in KIND_TO_TERRAFORM.items()}
RESOURCE_TO_TERRAFORM = {
    "wood": "plant_grove", "herbs": "plant_grove",
    "food": "clear_field", "fish": "extend_beach",
}

VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,24}$")
FUNCTION_EFFECT_KEYS = ("produces", "boosts", "unlocks", "stores", "houses",
                        "shelter", "light", "upkeep")
VALID_PRODUCE_SCOPES = {"village", "district"}
VALID_BOOST_KINDS = {"gather", "craft"}
VALID_BOOST_SCOPES = {"village", "district"}
VALID_UNLOCK_KINDS = {"craft", "transit"}
MAX_PENDING_BLUEPRINTS = 5
MAX_PENDING_ROLES = 5
MAX_EMERGENT_ROLES = 8
MAX_APPROVED_CUSTOM = 15
MAX_CUSTOM_RESOURCES = 10
# Phase D (TECH_TREE_ENABLED): blueprint tech-tier bounds. Tier gating only
# runs when the caller passes a village_tier (the engine passes None with the
# flag off, so flag-off validation is unchanged).
MAX_TECH_TIER = 3

# Sprite grid bounds (also enforced post-hoc by validate_sprite_block()); used
# by server.py's DECISION_SCHEMA (bounded at the grammar level, not just
# post-hoc) and by decision_validation.validate_sprite_block.
SPRITE_GRID_MIN = 4
SPRITE_GRID_MAX = 14
