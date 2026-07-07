"""Server-authoritative simulation engine (Phases 2-5 of the engine port).

This module ports the browser engine (simulation/index.html) into Python so the
simulation runs headless server-side. It owns ALL world state (the `civilization`
dict + `agents` list + frameTick/paused), runs a fixed-timestep daemon thread,
and dispatches LLM "think" jobs to a bounded worker pool. A single RLock guards
all state mutation (tick thread, LLM callbacks, and /state snapshots).

Field names and behavior mirror index.html per the frozen Contract 1/2 in
.cursor/plans/engine-port-contracts.md. The cognition side (prompt builder,
normalize_decision, role_fallback_action, MemoryStore, lm_complete) is reused
from server.py and injected at construction time to avoid a circular import.
"""

import json
import math
import os
import random
import re
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Full-state persistence (Contract 3). Resolved relative to this module so it
# lands next to server.py/sim_engine.py regardless of the launch cwd.
# Bumped 1 -> 2 for the world-expansion plan: civilization.activeProject ->
# districtProjects, new districts/roadNodes/roadEdges/frontierPlots. See
# restore_state()'s migration shim for pre-v2 saves.
STATE_VERSION = 2
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
AUTOSAVE_SECONDS = 10
# Sets on the civilization that serialize to JSON arrays and back.
_CIV_SET_KEYS = ("rejectedBlueprintIds", "rejectedRecipeIds", "builtTypes")


# --- Feature flags (ported from index.html consts; now server config) ---
SURVIVAL_ENABLED = True
CRAFTING_ENABLED = True
USE_GOALS = True
STRUCTURE_EFFECTS_ENABLED = True
MEMORY_ENABLED = True
AGENT_MESSAGING = True
PIANO_MODULES = False
META_SYSTEM = False
EMERGENT_ROLES = True
RULES_ENABLED = True
MEMES_ENABLED = True
BENCHMARKS_ENABLED = True
ECOLOGY_ENABLED = True
# World-expansion plan: waypoint-based road routing for general travel
# (move_to_district / idle wander / craft-station redirects). Sage-emergency
# rescue and short local hops (move_to_agent, trade, talk) always stay direct
# regardless of this flag -- see _set_agent_target_to_agent. Off reverts
# _set_agent_target to the old straight-to-random-interior-point behavior so
# routing can be A/B compared.
ROADS_ENABLED = True

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
        "bounds": {"x1": 230, "y1": 120, "x2": 400, "y2": 880},
        "build_grid": None, "entryNode": "beach_gate",
    },
    "cave_east": {
        "kind": "cave", "tile": "cave", "label": "CAVE",
        "bounds": {"x1": 1210, "y1": 1150, "x2": 1540, "y2": 1360},
        "build_grid": None, "entryNode": "cave_east_gate",
    },
    "ocean": {
        "kind": "ocean", "tile": "ocean", "label": None,
        "bounds": {"x1": 30, "y1": 120, "x2": 180, "y2": 880},
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
}

# kind -> template used by _maybe_found_district() to instantiate a brand new
# district of that kind into a claimed frontier plot. Only kinds that are
# actually buildable get a template -- there's no reason to found more empty
# forest/beach/ocean/market (single-instance by design), and a founded "cave"
# would need real per-district mining logic it doesn't have, so cave growth is
# covered by cave_deep already existing as a second starter site instead.
DISTRICT_KIND_TEMPLATES = {
    "farm": {"tile": "farm", "grid": {"cols": 4, "dx": 105, "dy": 85, "cap": 30}},
    "village": {"tile": "village", "grid": {"cols": 4, "dx": 100, "dy": 95, "cap": 30}},
    "workshop": {"tile": "workshop", "grid": {"cols": 4, "dx": 100, "dy": 90, "cap": 24}},
    "beach": {"tile": "beach", "grid": {"cols": 3, "dx": 100, "dy": 80, "cap": 18}},
}

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
}
STARTER_ROAD_EDGES = [
    ["farm_north_gate", "village_hub"],
    ["village_hub", "forest_gate"],
    ["village_hub", "cave_east_gate"],
    ["village_hub", "beach_gate"],
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

ZONE_NAMES = ["farm", "forest", "village", "market", "beach", "cave", "ocean", "workshop"]

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
EDIBLE_RESOURCES = ["food", "fish"]
HEAL_AMOUNT = 25
COLLAPSE_REGEN = 0.5
COLLAPSE_REVIVE_HEALTH = 15
REVIVE_HUNGER = 35          # hunger floor on revival, else 0-hunger re-collapse in ~8s
EDIBLE_RESERVE = 3          # food/fish an agent keeps back from builds/sharing
SHARE_RADIUS = 120          # auto-share edibles with a starving neighbour within this range
STARVING_HUNGER = 10        # below this, a foodless agent deterministically seeks the nearest food zone

# Structure effects (STRUCTURE_EFFECTS_ENABLED): buildings do something, and
# soft caps make the Nth duplicate worthless so agents move on to new types.
FARM_PLOTS_PER_EXTRA = 4    # farm plots in the agent's district per +1 edible gathered
FARM_YIELD_BONUS_CAP = 2    # max bonus units per gather, so plots beyond 8/district are waste
HOUSES_PER_NEW_VILLAGER = 3  # each 3 houses raise the population cap by 1 (hard cap: len(AGENT_DEFS))
WORKSHOPS_PER_CRAFT_BONUS = 3  # workshops village-wide per +1 crafted output (max +1)
WALL_SOFT_CAP = 10
WORKSHOP_DISTRICT_CAP = 3   # per buildable village/workshop-kind district
CUSTOM_SOFT_CAP = 5         # per custom/blueprint type (and the granary)
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
# sessions via state.json). ~3 minutes at 30 ticks/s = several think cycles.
DIRECTIVE_TTL_FRAMES = 5400
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
MAX_PENDING_RULES = 4
MAX_ACTIVE_RULES = 8

ROLE_SWITCH_TICK_FRAMES = 120
ROLE_SWITCH_COOLDOWN = 600
AUTOSWITCH_PROTECTED_ROLES = {"elder", "builder", "healer"}
RULES_TICK_FRAMES = 150
RULE_PROPOSE_COOLDOWN = 1500

MEME_SEED_ID = "harvest_spirit"
MEMES = {"harvest_spirit": "The Harvest Spirit rewards those who share food"}
MEME_SPREAD_PROB = 0.5
MEME_PROXIMITY_PROB = 0.2
MEME_TICK_FRAMES = 90

INBOX_CAP = 6
WORKING_MEM_CAP = 6
SHORT_MEM_CAP = 12
LONG_MEM_CAP = 8

VALID_GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
VALID_VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
RULE_KINDS = {"resource_tax", "custom"}

MAX_CONCURRENT_LLM = 2
LLM_MIN_GAP_MS = 250

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
# Shelter: one night per DAY_FRAMES (~7.5 min real time). Each *working* house
# shelters HOUSE_SHELTER_OCCUPANTS; unsheltered agents lose a little hunger --
# a nudge, never a punishment: ~1/7 of one meal (FOOD_RESTORE 45), floored at
# SHELTER_HUNGER_FLOOR (20), i.e. a night outside can never push anyone into
# the starvation-reflex band (STARVING_HUNGER 10).
DAY_FRAMES = 13500
HOUSE_SHELTER_OCCUPANTS = 2
SHELTER_HUNGER_PENALTY = 6
SHELTER_HUNGER_FLOOR = 20
# Decay & repair: the designed consumer for the build-rate sprawl (2026-07-06
# audit: ~30 builds/hour with nothing consuming structures). condition is 100
# at build and decays STRUCTURE_DECAY_PER_GOODS_TICK per goods tick (30s).
# 2026-07-07 audit retune: 0.5/tick ruined a 416-structure town in one night
# (~100 min per structure, needing ~4 successful repairs per 30s village-wide
# to hold -- unpayable by 12 agents; 409/416 became ruins, throughput 0). Now
# 0.1/tick: ~5.8h of neglect to disrepair (<30: stops producing/boosting/
# housing), ~8.3h to ruin -- a structure survives an unattended overnight soak,
# sprawl still decays across days, upkeep stays a real competing use of
# gathering time. A ruin is rebuilt via repair_structure for half the original
# needs (min 1 each) -- cheaper than new, the deterministic escape for total
# collapse.
STRUCTURE_DECAY_PER_GOODS_TICK = 0.1
STRUCTURE_DISREPAIR_THRESHOLD = 30
REPAIR_CONDITION_RESTORE = 50
# Disaster: rare random damage so decay isn't perfectly predictable. 0.005 per
# ~30s goods tick => expected roughly once per 100 minutes of runtime.
DISASTER_PROB = 0.005
DISASTER_DAMAGE = (40, 70)
# Seasons: a four-season clock derived purely from frameTick (no extra state to
# persist). One season = SEASON_FRAMES (~30 min => a full year every 2h; an
# overnight soak sees several winters). The season multiplies district stock
# regrowth: spring booms, winter stops regrowth entirely. Escapes: stores/
# granary capacity built before winter (spoilage permitting), and the season
# simply turning.
SEASON_FRAMES = 54000
SEASONS = ["spring", "summer", "autumn", "winter"]
SEASON_REGROW_MULT = {"spring": 2, "summer": 1, "autumn": 1, "winter": 0}

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
            "found_district": "beach",
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
    "herbs": {"name": "Herbs", "gatherZone": "forest", "color": "#8BC34A"},
    "water": {"name": "Water", "gatherZone": "village", "color": "#03A9F4"},
}
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

AGENT_DEFS = [
    {"id": 1, "name": "Aria", "role": "farmer", "personality": "hardworking and cautious", "color": "#4CAF50", "zone": "farm_north"},
    {"id": 2, "name": "Marco", "role": "trader", "personality": "sociable and opportunistic", "color": "#FF9800", "zone": "market"},
    {"id": 3, "name": "Zara", "role": "builder", "personality": "creative and methodical", "color": "#9C27B0", "zone": "village_core"},
    {"id": 4, "name": "Rex", "role": "guard", "personality": "loyal and aggressive", "color": "#F44336", "zone": "village_core"},
    {"id": 5, "name": "Luna", "role": "gatherer", "personality": "curious and adventurous", "color": "#2196F3", "zone": "forest"},
    {"id": 6, "name": "Finn", "role": "fisher", "personality": "patient and quiet", "color": "#00BCD4", "zone": "beach"},
    {"id": 7, "name": "Mia", "role": "healer", "personality": "empathetic and generous", "color": "#E91E63", "zone": "village_core"},
    {"id": 8, "name": "Colt", "role": "miner", "personality": "stubborn and hardworking", "color": "#795548", "zone": "cave_east"},
    {"id": 9, "name": "Ivy", "role": "scout", "personality": "fast and observant", "color": "#8BC34A", "zone": "forest"},
    {"id": 10, "name": "Dex", "role": "blacksmith", "personality": "focused and proud", "color": "#607D8B", "zone": "market"},
    {"id": 11, "name": "Nova", "role": "explorer", "personality": "bold and impulsive", "color": "#FF5722", "zone": "beach"},
    {"id": 12, "name": "Sage", "role": "elder", "personality": "wise and slow-moving", "color": "#FFC107", "zone": "village_core"},
]
ROSTER = ["Zara", "Sage", "Aria", "Luna", "Marco", "Colt", "Finn", "Mia"]


def _dist(ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    return math.sqrt(dx * dx + dy * dy)


def _rects_overlap(a, b):
    return a["x1"] < b["x2"] and b["x1"] < a["x2"] and a["y1"] < b["y2"] and b["y1"] < a["y2"]


# assign_task speech gets embedded in templated prompt text ("Your leader
# assigned you: <task>. Do it now."), so oratory framing ("My dear agents,
# Luna, please ...") reads as garbage there. Strip the greeting/addressee
# preamble and cap the length; the task itself is what must survive.
_TASK_PREAMBLE = re.compile(
    r"^(?:(?:my\s+)?dear(?:est)?s?(?:\s+\w+)?|villagers?|agents?|everyone|friends?|"
    r"hello|greetings|attention|listen(?:\s+up)?|please)[\s,!.:;-]+",
    re.IGNORECASE)
_TASK_MAX_LEN = 200


def _clean_task_text(text, target_name=None):
    task = " ".join((text or "").split())
    for _ in range(4):
        before = task
        task = _TASK_PREAMBLE.sub("", task)
        if target_name:
            task = re.sub(r"^" + re.escape(target_name) + r"[\s,!.:;-]+", "", task, flags=re.IGNORECASE)
        if task == before:
            break
    task = task.strip(" ,;:-")
    if not task:
        task = " ".join((text or "").split())
    if len(task) > _TASK_MAX_LEN:
        # Prefer a sentence boundary; otherwise cut at a word.
        cut = task.rfind(". ", 0, _TASK_MAX_LEN)
        if cut < 40:
            cut = task.rfind(" ", 0, _TASK_MAX_LEN)
        task = task[:cut if cut > 0 else _TASK_MAX_LEN]
    # The prompt templates supply their own trailing punctuation ("...: <task>.
    # Do it now."), so a terminal ./!/? here would double up.
    task = re.sub(r"[\s.!?;:,]+$", "", task)
    if task and task[0].islower():
        task = task[0].upper() + task[1:]
    return task


def _validate_districts(districts):
    """Assert no two district rectangles overlap. Callable both at module load
    (against STARTER_DISTRICTS) and at runtime (against the live
    civilization["districts"], re-checked after any founding) so a bad
    hand-authored edit or a founding-logic bug fails loudly instead of
    silently corrupting get_zone/get_district results."""
    ids = list(districts.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = districts[ids[i]]["bounds"], districts[ids[j]]["bounds"]
            if _rects_overlap(a, b):
                raise AssertionError(f"district bounds overlap: {ids[i]!r} and {ids[j]!r}")


def _validate_road_graph(nodes, edges):
    """Assert every road node is reachable from every other (BFS from an
    arbitrary root). Raises at module load and again after any founding, so a
    missing/typo'd edge -- or a founding-logic bug -- fails loudly rather than
    silently stranding a district."""
    if not nodes:
        return
    adj = {n: [] for n in nodes}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    root = next(iter(nodes))
    seen = {root}
    queue = deque([root])
    while queue:
        cur = queue.popleft()
        for nxt in adj.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    missing = set(nodes) - seen
    if missing:
        raise AssertionError(f"road graph has unreachable node(s): {sorted(missing)}")


_validate_districts(STARTER_DISTRICTS)
_validate_road_graph(STARTER_ROAD_NODES, STARTER_ROAD_EDGES)


def _build_frontier_plots():
    """Tile everything OUTSIDE the starter core's reserved footprint into a
    fixed-size plot grid. _maybe_found_district() claims one plot at a time."""
    plots = []
    cols = WORLD_W // FRONTIER_PLOT_W
    rows = WORLD_H // FRONTIER_PLOT_H
    idx = 0
    for r in range(rows):
        for col in range(cols):
            rect = {"x1": col * FRONTIER_PLOT_W, "y1": r * FRONTIER_PLOT_H,
                    "x2": (col + 1) * FRONTIER_PLOT_W, "y2": (r + 1) * FRONTIER_PLOT_H}
            if _rects_overlap(rect, CORE_RESERVED_BOUNDS):
                continue
            plots.append({"id": f"plot_{idx}", **rect, "claimed": False, "claimedBy": None})
            idx += 1
    return plots


def get_zone(districts, x, y):
    """kind at (x, y), or "path" if it's unclaimed ground (frontier or the
    starter core's connecting paths). Back-compat: agent["currentZone"] keeps
    meaning "kind", exactly as before districts existed."""
    for d in districts.values():
        b = d["bounds"]
        if b["x1"] <= x <= b["x2"] and b["y1"] <= y <= b["y2"]:
            return d["kind"]
    return "path"


def get_district(districts, x, y):
    """The specific district id at (x, y), or None. New alongside get_zone:
    callers that need the specific instance (build-grid/road targeting) use
    this instead of the kind-only get_zone."""
    for did, d in districts.items():
        b = d["bounds"]
        if b["x1"] <= x <= b["x2"] and b["y1"] <= y <= b["y2"]:
            return did
    return None


class SimEngine:
    """Owns world state and the fixed-timestep loop. Thread-safe via self.lock."""

    def __init__(self, deps, roster_size=8):
        # deps: the small surface from server.py we reuse (functions + objects).
        # Required keys: ROLES, ROLE_PROJECT, ROLE_SKILLS, ROLE_PRIMARY_RESOURCE,
        #   RESOURCE_GATHER_ROLES, AVAILABLE_ACTIONS, SLUG_RE, llm_decide,
        #   lm_complete, memory_store, log_activity, log_conversation,
        #   log_benchmark.
        self.d = deps
        self.SLUG_RE = deps["SLUG_RE"]
        self.lock = threading.RLock()
        self.frameTick = 0
        self.paused = False
        self.lmStatus = "offline"
        self.llm_cooldown_until = 0.0
        self.last_llm_dispatch_ms = 0.0
        self.activityLog = []      # most-recent-first, capped 30
        self.conversationLog = []  # most-recent-first, capped 100
        self.lastBenchmarks = {}
        self.lastMemorySize = 0
        self._memory_maint_index = 0
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM)
        self._inflight = set()      # agent names with a think job in flight
        self.RECIPES = {}
        self.roster_size = roster_size
        self.ROAD_PATH_CACHE = {}   # (nodeA, nodeB) -> [node ids]; see _recompute_road_paths
        self._reset_world(roster_size)

    # --- roster + cold start ---
    def _select_active_defs(self, roster_size):
        roster_size = max(1, min(len(AGENT_DEFS), roster_size))
        if roster_size >= len(AGENT_DEFS):
            return list(AGENT_DEFS)
        names = []
        for name in ROSTER:
            if len(names) >= roster_size:
                break
            names.append(name)
        for d in AGENT_DEFS:
            if len(names) >= roster_size:
                break
            if d["name"] not in names:
                names.append(d["name"])
        if "Sage" not in names:
            names[max(0, len(names) - 1)] = "Sage"
        by_name = {d["name"]: d for d in AGENT_DEFS}
        return [by_name[n] for n in names if n in by_name]

    def _make_agents(self, active_defs):
        agents = []
        for i, d in enumerate(active_defs):
            district = self.civilization["districts"][d["zone"]]
            b = district["bounds"]
            center = {"x": (b["x1"] + b["x2"]) / 2, "y": (b["y1"] + b["y2"]) / 2}
            ox = (i % 3 - 1) * 22
            oy = ((i // 3) % 3 - 1) * 22
            speed = 2.8
            if d["name"] == "Sage":
                speed = 1.4
            if d["name"] in ("Ivy", "Nova"):
                speed = 3.6
            a = {
                "id": d["id"], "name": d["name"], "role": d["role"],
                "personality": d["personality"], "color": d["color"],
                "x": center["x"] + ox, "y": center["y"] + oy,
                "targetX": center["x"] + ox, "targetY": center["y"] + oy,
                "speed": speed,
                "memory": {"working": [], "shortTerm": [], "longTerm": []},
                "resources": {"food": 2, "wood": 0, "gold": 0},
                "relationships": {}, "inbox": [], "beliefs": set(), "votes": {},
                "currentZone": district["kind"], "currentDistrict": d["zone"],
                "waypoints": [], "message": None, "messageTimer": 0,
                "thinkTimer": 0, "thinkInterval": 300, "isThinking": False,
                "lastAction": None, "lastReasoning": None, "consecutiveTalks": 0,
                "pendingThink": False, "assignedTask": None, "idleCycles": 0,
                "lastTaskedFrame": None, "lastContributedFrame": None,
                "consecutiveIdleMoves": 0, "hunger": 80, "health": 100,
                "incapacitated": False, "goal": None, "actionCounts": {},
                "commitment": None, "inventionTurn": False,
                "lastBlueprintRejection": None, "lastGatherRejection": None,
                "lastProjectRejection": None, "lastTerraformRejection": None,
                "lastCraftRejection": None, "lastRepairRejection": None,
                "lastShelterNote": None, "lastSpokeFrame": 0,
                "persona": "", "idleFrames": 0,
                "modules": {"perception": True, "social": True, "desire": True, "reflection": True},
            }
            agents.append(a)
        # post-build setup (index.html lines ~1037)
        for i, a in enumerate(agents):
            a["thinkInterval"] = 360 + i * 60
            if a["role"] == "elder":
                a["thinkInterval"] = 240
            a["thinkTimer"] = i * 30
            a["idleFrames"] = 0
            self._set_agent_target(a, a["currentDistrict"])
        return agents

    def _reset_world(self, roster_size):
        self.RECIPES = {k: {"name": v["name"], "inputs": dict(v["inputs"]), "station": v["station"]}
                        for k, v in SEED_RECIPES.items()}
        districts = json.loads(json.dumps(STARTER_DISTRICTS))
        road_nodes = json.loads(json.dumps(STARTER_ROAD_NODES))
        road_edges = [list(e) for e in STARTER_ROAD_EDGES]
        district_projects = {did: None for did, d in districts.items() if d.get("build_grid")}
        district_last_contribution = {did: 0 for did in district_projects}
        self.civilization = {
            "level": 1,
            "structures": [],
            "districts": districts,
            "roadNodes": road_nodes,
            "roadEdges": road_edges,
            "frontierPlots": _build_frontier_plots(),
            "districtProjects": district_projects,
            "districtLastContribution": district_last_contribution,
            "kindLastActivityFrame": {},
            "lastDistrictFoundFrame": 0,
            "frontierExhaustedLogged": False,
            "completedProjects": 0,
            "nextStructureId": 1,
            "basePopulation": max(1, min(len(AGENT_DEFS), roster_size)),
            "resourceRegistry": {**{k: dict(v) for k, v in BASE_RESOURCES.items()},
                                 **{k: dict(v) for k, v in CRAFTED_RESOURCES.items()}},
            "projectRegistry": {k: dict(v) for k, v in PROJECT_TEMPLATES.items()},
            "builtTypes": set(),
            "inventionRequiredStreak": 0,
            "inventionBackstopFires": 0,
            "pendingBlueprints": [],
            "rejectedBlueprintIds": set(),
            "rejectedBlueprintFrames": {},
            "customResourceAddedFrame": {},
            "pendingRecipes": [],
            "rejectedRecipeIds": set(),
            "directive": None,
            "directiveFrame": 0,
            "lastBlueprintActivityFrame": 0,
            "lastCraftActivityFrame": 0,
            "lastRuleActivityFrame": 0,
            "lastRoleSwitchFrame": 0,
            "collectAttempts": 0,
            "collectSuccesses": 0,
            "rules": [],
            "pendingRules": [],
            "stockpile": {},
            "taxDue": 0,
            "taxPaid": 0,
            "effectLastFire": {},
            "districtStocks": {},
            "approvedCustomApprovedFrame": {},
            "lastProjectAbandonment": None,
            "lastSpoilage": None,
            "approvedCustomBackoffUntil": 0,
            "approvedCustomBackstopFailures": 0,
            "approvedCustomEscalationLogged": False,
            "projectAbandonStreak": {},
            "deferredProjectTypes": {},
        }
        self._effect_period_fired = 0
        self._last_effect_benchmark_fired = 0
        self._spoiled_period = 0     # Phase C: spoilage counter per benchmark period
        self._last_season = None     # Phase C: season-turn activity logging
        if ECOLOGY_ENABLED:
            self.civilization["districtStocks"] = self._init_district_stocks(self.civilization["districts"])
        self._recompute_road_paths()
        active_defs = self._select_active_defs(roster_size)
        self.agent_names = set(d["name"] for d in active_defs)
        self.agents = self._make_agents(active_defs)
        self.frameTick = 0
        self._seed_beliefs()

    # --- logging helpers (mirror pushActivity / pushCommunication) ---
    def _push_activity(self, line):
        self.activityLog.insert(0, line)
        del self.activityLog[30:]
        try:
            self.d["log_activity"](line, self.frameTick)
        except Exception:
            pass

    def _push_communication(self, kind, frm, to, message, outcome=None):
        entry = {"kind": kind, "from": frm, "to": to, "message": message}
        if outcome:
            entry["outcome"] = outcome
        self.conversationLog.insert(0, entry)
        del self.conversationLog[100:]
        try:
            self.d["log_conversation"](frm, to, message, self.frameTick,
                                       kind=kind, outcome=outcome)
        except Exception:
            pass

    def _push_conversation(self, frm, to, message):
        self._push_communication("speech", frm, to, message)

    def _log_benchmark(self, metric, value, detail=None):
        if not BENCHMARKS_ENABLED:
            return
        try:
            self.d["log_benchmark"](metric, value, self.frameTick, detail)
        except Exception:
            pass

    # --- memory (tiered; vector store lives in server) ---
    _HIGH_SAL_WORDS = ("built", "collapsed", "revived", "approved", "rejected",
                       "started", "proposed", "tasked", "reached level",
                       "became a", "enacted", "voted", "switched")
    _LOW_SAL_WORDS = ("rested", "wandered", "found nothing", "has nothing",
                      "heads to", "moves toward", "looked for")

    def _salience_for(self, line):
        low = (line or "").lower()
        if any(w in low for w in self._HIGH_SAL_WORDS):
            return 0.85
        if any(w in low for w in self._LOW_SAL_WORDS):
            return 0.2
        return 0.5

    def _push_memory(self, agent, line, kind=None):
        m = agent["memory"]
        sal = self._salience_for(line)
        m["working"].append(line)
        while len(m["working"]) > WORKING_MEM_CAP:
            evicted = m["working"].pop(0)
            if self._salience_for(evicted) >= 0.7:
                m["shortTerm"].append(evicted)
                if len(m["shortTerm"]) > SHORT_MEM_CAP:
                    m["shortTerm"].pop(0)
        try:
            self.d["memory_store"].store(agent["name"], line, salience=sal,
                                         kind=kind or "event", frame_tick=self.frameTick)
        except Exception:
            pass

    def _memory_for_prompt(self, agent):
        m = agent["memory"]
        return m["longTerm"][-3:] + m["shortTerm"][-4:] + m["working"][-4:]

    # --- agent lookups + movement ---
    def _find_agent(self, name):
        for a in self.agents:
            if a["name"] == name:
                return a
        return None

    def _get_nearby_agents(self, agent):
        near = []
        for o in self.agents:
            if o is agent:
                continue
            if _dist(agent["x"], agent["y"], o["x"], o["y"]) <= 80:
                near.append(o["name"])
        return near

    def _get_nearby_detailed(self, agent):
        near = []
        for o in self.agents:
            if o is agent:
                continue
            if _dist(agent["x"], agent["y"], o["x"], o["y"]) <= 80:
                near.append({"name": o["name"], "role": o["role"],
                             "food": o["resources"].get("food", 0),
                             "wood": o["resources"].get("wood", 0),
                             "gold": o["resources"].get("gold", 0)})
        return near

    def _find_nearest_agent(self, agent):
        best, best_d = None, float("inf")
        for o in self.agents:
            if o is agent:
                continue
            dd = _dist(agent["x"], agent["y"], o["x"], o["y"])
            if dd < best_d:
                best_d, best = dd, o
        return best

    def _distance_to(self, a, b):
        return _dist(a["x"], a["y"], b["x"], b["y"])

    # --- districts + roads ---
    def _districts_of_kind(self, kind):
        return [did for did, d in self.civilization["districts"].items() if d["kind"] == kind]

    def _resolve_target_district(self, target, agent=None):
        """Resolve a decision/movement 'target' to a concrete district id.
        Accepts either a specific district id, or (hedge for the prompt-tuning
        transition / any remaining kind-based call site) a kind name like
        "farm", in which case the nearest district of that kind to `agent`
        (or simply the first one, if no agent given) is used instead of
        failing outright."""
        c = self.civilization
        if not target:
            return None
        if target in c["districts"]:
            return target
        ids = self._districts_of_kind(target)
        if not ids:
            return None
        if agent is None or len(ids) == 1:
            return ids[0]
        best, best_d = ids[0], float("inf")
        for did in ids:
            b = c["districts"][did]["bounds"]
            cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
            dd = _dist(agent["x"], agent["y"], cx, cy)
            if dd < best_d:
                best_d, best = dd, did
        return best

    def _nearest_road_node(self, x, y):
        nodes = self.civilization["roadNodes"]
        best, best_d = None, float("inf")
        for nid, n in nodes.items():
            dd = _dist(x, y, n["x"], n["y"])
            if dd < best_d:
                best_d, best = dd, nid
        return best

    def _recompute_road_paths(self):
        """All-pairs shortest paths via BFS. Cheap at this graph's size (a
        dozen-ish nodes even after several foundings) -- recomputed on cold
        start and again after any district-founding graph change, not cached
        as a one-time module-load constant, since the graph itself isn't one."""
        nodes = self.civilization["roadNodes"]
        edges = self.civilization["roadEdges"]
        adj = {n: [] for n in nodes}
        for a, b in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        cache = {}
        for start in nodes:
            prev = {start: None}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for nxt in adj.get(cur, []):
                    if nxt not in prev:
                        prev[nxt] = cur
                        queue.append(nxt)
            for end in prev:
                path = []
                node = end
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                cache[(start, end)] = path
        self.ROAD_PATH_CACHE = cache

    def _road_path_between(self, agent, dest_district_id):
        c = self.civilization
        dest_node = c["districts"][dest_district_id].get("entryNode")
        origin_district = agent.get("currentDistrict")
        origin_node = None
        if origin_district and origin_district in c["districts"]:
            origin_node = c["districts"][origin_district].get("entryNode")
        if not origin_node:
            origin_node = self._nearest_road_node(agent["x"], agent["y"])
        if not dest_node:
            dest_node = self._nearest_road_node(agent["x"], agent["y"])
        if not origin_node or not dest_node or origin_node == dest_node:
            return []
        return self.ROAD_PATH_CACHE.get((origin_node, dest_node)) or []

    def _set_agent_target(self, agent, target):
        """Route the agent to a random interior point of the destination
        district. When ROADS_ENABLED, travel goes via cached road-node paths
        (agent["waypoints"]) instead of a straight line -- this is general
        travel (idle wander, craft-station redirects, move_to_district);
        move_to_agent/trade/talk and Sage-emergency rescue use
        _set_agent_target_to_agent instead and always stay direct."""
        district_id = self._resolve_target_district(target, agent)
        if not district_id:
            return
        bounds = self.civilization["districts"][district_id]["bounds"]
        dest_x = bounds["x1"] + random.random() * (bounds["x2"] - bounds["x1"])
        dest_y = bounds["y1"] + random.random() * (bounds["y2"] - bounds["y1"])
        if not ROADS_ENABLED:
            agent["targetX"] = dest_x
            agent["targetY"] = dest_y
            agent["waypoints"] = []
            return
        path_nodes = self._road_path_between(agent, district_id)
        waypoints = [dict(self.civilization["roadNodes"][n]) for n in path_nodes]
        waypoints.append({"x": dest_x, "y": dest_y})
        agent["waypoints"] = waypoints[1:]
        first = waypoints[0]
        agent["targetX"] = first["x"]
        agent["targetY"] = first["y"]

    def _set_agent_target_to_agent(self, agent, target_name):
        target = self._find_agent(target_name)
        if not target:
            return
        agent["targetX"] = target["x"] + (random.random() - 0.5) * 60
        agent["targetY"] = target["y"] + (random.random() - 0.5) * 60
        agent["waypoints"] = []

    def _auto_move_toward_target(self, agent, target_name):
        if not target_name or target_name not in self.agent_names:
            return
        other = self._find_agent(target_name)
        if not other:
            return
        if self._distance_to(agent, other) > 80:
            self._set_agent_target_to_agent(agent, target_name)

    def _move_agent(self, agent, scale=1.0):
        dx = agent["targetX"] - agent["x"]
        dy = agent["targetY"] - agent["y"]
        dist = math.sqrt(dx * dx + dy * dy)
        step = agent["speed"] * scale
        if dist <= step:
            agent["x"] = agent["targetX"]
            agent["y"] = agent["targetY"]
            waypoints = agent.get("waypoints") or []
            if waypoints:
                nxt = waypoints.pop(0)
                agent["targetX"] = nxt["x"]
                agent["targetY"] = nxt["y"]
                agent["idleFrames"] = 0
            else:
                agent["idleFrames"] = agent.get("idleFrames", 0) + 1
                if agent["idleFrames"] >= 60:
                    cur = agent.get("currentDistrict")
                    if cur:
                        wander = cur
                    else:
                        wander = random.choice(list(self.civilization["districts"].keys()))
                    self._set_agent_target(agent, wander)
                    agent["idleCycles"] = agent.get("idleCycles", 0) + 1
                    agent["idleFrames"] = 0
        else:
            agent["x"] += (dx / dist) * step
            agent["y"] += (dy / dist) * step
            agent["idleFrames"] = 0
        agent["currentZone"] = get_zone(self.civilization["districts"], agent["x"], agent["y"])
        agent["currentDistrict"] = get_district(self.civilization["districts"], agent["x"], agent["y"])

    # --- survival ---
    def _first_edible(self, agent):
        for rid in EDIBLE_RESOURCES:
            if agent["resources"].get(rid, 0) > 0:
                return rid
        return None

    def _share_edible_with(self, agent):
        """Deterministic anti-hoarding backstop: a starving agent with nothing
        to eat receives one edible from a nearby villager holding surplus
        (above EDIBLE_RESERVE). Without this the only food-transfer paths are
        heal-donation and voluntary LLM trades, so one well-fed fisher can sit
        on a full stack while neighbours starve."""
        for donor in self.agents:
            if donor is agent or donor["incapacitated"]:
                continue
            if self._distance_to(agent, donor) > SHARE_RADIUS:
                continue
            for rid in EDIBLE_RESOURCES:
                if donor["resources"].get(rid, 0) > EDIBLE_RESERVE:
                    donor["resources"][rid] -= 1
                    agent["hunger"] = min(100, agent["hunger"] + FOOD_RESTORE)
                    self._push_activity(f"{donor['name']} shared {rid} with {agent['name']}")
                    return True
        return False

    def _update_survival(self, agent):
        if not SURVIVAL_ENABLED:
            return
        edible = self._first_edible(agent) if agent["hunger"] < EAT_THRESHOLD else None
        if edible:
            agent["resources"][edible] -= 1
            agent["hunger"] = min(100, agent["hunger"] + FOOD_RESTORE)
            self._push_activity(f"{agent['name']} ate {edible}")
        if not edible and agent["hunger"] <= 0:
            self._share_edible_with(agent)
        agent["hunger"] = max(0, agent["hunger"] - HUNGER_RATE)
        if agent["incapacitated"]:
            agent["health"] = min(100, agent["health"] + COLLAPSE_REGEN)
            if agent["health"] >= COLLAPSE_REVIVE_HEALTH:
                agent["incapacitated"] = False
                agent["hunger"] = max(agent["hunger"], REVIVE_HUNGER)
                self._push_activity(f"{agent['name']} recovered")
        else:
            if agent["hunger"] <= 0:
                agent["health"] = max(0, agent["health"] - HEALTH_RATE)
            else:
                agent["health"] = min(100, agent["health"] + HEALTH_REGEN)
            if agent["health"] <= 0:
                agent["incapacitated"] = True
                agent["goal"] = None
                self._push_activity(f"{agent['name']} collapsed from starvation")

    def _neediest_nearby(self, agent):
        nearby = [self._find_agent(n) for n in self._get_nearby_agents(agent)]
        nearby = [a for a in nearby if a and (a["incapacitated"] or a["health"] < 60)]
        if not nearby:
            return None
        nearby.sort(key=lambda a: (0 if a["incapacitated"] else 1, a["health"]))
        return nearby[0]

    # --- Sage emergency ---
    def _sage_emergency(self):
        if not SURVIVAL_ENABLED:
            return None
        sage = next((a for a in self.agents if a["role"] == "elder"), None)
        if not sage:
            return None
        if not sage["incapacitated"] and sage["health"] >= SAGE_CRITICAL_HEALTH:
            return None
        healer = next((a for a in self.agents if a["role"] == "healer"), None)
        return healer if (healer and healer["incapacitated"]) else sage

    def _sage_responders(self, target):
        responders = set()
        healer = next((a for a in self.agents if a["role"] == "healer"), None)
        if healer and not healer["incapacitated"] and healer is not target:
            responders.add(healer["name"])
        nearest, nearest_d = None, float("inf")
        for a in self.agents:
            if a is target or a["incapacitated"] or a["name"] in responders:
                continue
            dd = self._distance_to(a, target)
            if dd < nearest_d:
                nearest_d, nearest = dd, a
        if nearest:
            responders.add(nearest["name"])
        return responders

    def _rush_to_heal(self, agent, target):
        agent["goal"] = None
        if self._distance_to(agent, target) > 80:
            self._auto_move_toward_target(agent, target["name"])
            self._push_activity(f"{agent['name']} rushes to save {target['name']}")
            return
        self.apply_decision(agent, {"action": "heal_agent", "target": target["name"],
                                    "message": None, "reasoning": "Sage-priority emergency."})

    # --- project helpers (concurrent per-district builds) ---
    def _touch_kind_activity(self, kind):
        self.civilization["kindLastActivityFrame"][kind] = self.frameTick

    def _active_project_districts(self):
        return [did for did, p in self.civilization["districtProjects"].items() if p]

    def _buildable_district_ids(self):
        return [did for did, d in self.civilization["districts"].items() if d.get("build_grid")]

    def _resolve_contribution_district(self, agent, target_district=None):
        """Which district a contribute/collect/build decision should act on:
        an explicit target_district with an active project, else the agent's
        own district if it has one, else the most-stalled active district
        village-wide (mirrors the old single-project "the project" default,
        generalized to pick fairly across concurrent builds)."""
        c = self.civilization
        if target_district and c["districtProjects"].get(target_district):
            return target_district
        cur = agent.get("currentDistrict")
        if cur and c["districtProjects"].get(cur):
            return cur
        actives = self._active_project_districts()
        if not actives:
            return None
        actives.sort(key=lambda did: c["districtLastContribution"].get(did, 0))
        return actives[0]

    def _resolve_build_district(self, agent, type_, target_district=None):
        """Which district a new project of `type_` should start in: an
        explicit target_district (if it's buildable and idle), else the
        agent's current district (if its kind matches and it's idle and
        under cap), else the nearest matching-kind buildable district with
        room, else the nearest matching-kind buildable district at all."""
        c = self.civilization
        kind = PROJECT_KIND.get(type_, "village")

        def usable(did):
            d = c["districts"].get(did)
            return bool(d and d.get("build_grid") and d["kind"] == kind
                        and not c["districtProjects"].get(did)
                        and self._district_structure_count(did) < d["build_grid"]["cap"])

        if target_district and usable(target_district):
            return target_district
        cur = agent.get("currentDistrict")
        if cur and usable(cur):
            return cur
        # Only districts with room: a project started in a full district can
        # never build and squats on a concurrent-project slot forever. When
        # every district of this kind is full, returning None is correct --
        # _maybe_found_district exists to open up new land in that case.
        with_room = [did for did in self._buildable_district_ids() if usable(did)]
        if not with_room:
            return None
        return min(with_room, key=lambda did: self._distance_to_district(agent, did))

    def _distance_to_district(self, agent, district_id):
        b = self.civilization["districts"][district_id]["bounds"]
        cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        return _dist(agent["x"], agent["y"], cx, cy)

    def _project_progress_text(self, district_id):
        p = self.civilization["districtProjects"].get(district_id)
        if not p:
            return "none"
        parts = [f"{res} {p['contributed'].get(res, 0)}/{need}" for res, need in p["needs"].items()]
        return ", ".join(parts)

    def _active_projects_brief(self):
        actives = self._active_project_districts()
        if not actives:
            return "none"
        c = self.civilization
        return "; ".join(f"{c['districtProjects'][did]['name']} in {did}" for did in actives)

    def _active_projects_progress_text(self):
        actives = self._active_project_districts()
        if not actives:
            return "none"
        return "; ".join(f"{did}: {self._project_progress_text(did)}" for did in actives)

    def _first_unmet_project_resource(self, district_id):
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return None
        for res in p["needs"]:
            if p["contributed"].get(res, 0) < p["needs"].get(res, 0):
                return res
        return None

    def _first_unmet_resource_anywhere(self):
        """First unmet resource across ANY active district project -- used by
        the emergent-role gap-filling logic, which cares about "is anything
        stalled village-wide" rather than one specific district."""
        for did in self._active_project_districts():
            res = self._first_unmet_project_resource(did)
            if res:
                return res
        return None

    def _gather_zone_for_resource(self, rid):
        d = self.civilization["resourceRegistry"].get(rid)
        return d.get("gatherZone") if d else None

    def _get_zone_resources(self, zone):
        return [rid for rid, d in self.civilization["resourceRegistry"].items()
                if d.get("gatherZone") == zone]

    # --- resource ecology (Phase B; gated by ECOLOGY_ENABLED) ---
    def _stock_max(self, resource_id):
        return STOCK_DEFAULT_MAX

    def _resources_for_district_kind(self, kind, resource_registry=None):
        reg = resource_registry or self.civilization["resourceRegistry"]
        return [rid for rid, d in reg.items()
                if d.get("gatherZone") == kind and not d.get("crafted")]

    def _init_district_stocks(self, districts, resource_registry=None):
        stocks = {}
        for did, d in districts.items():
            kind = d.get("kind")
            if not kind:
                continue
            res_ids = self._resources_for_district_kind(kind, resource_registry)
            if res_ids:
                stocks[did] = {rid: self._stock_max(rid) for rid in res_ids}
        return stocks

    def _ensure_district_stocks(self):
        c = self.civilization
        if c.get("districtStocks"):
            return
        c["districtStocks"] = self._init_district_stocks(c["districts"])

    def _district_stock(self, district_id, resource_id):
        return c_stocks.get(resource_id) if (c_stocks := self.civilization["districtStocks"].get(district_id)) else None

    def _set_district_stock(self, district_id, resource_id, value):
        c = self.civilization
        max_s = self._stock_max(resource_id)
        c.setdefault("districtStocks", {}).setdefault(district_id, {})[resource_id] = \
            min(max_s, max(0, value))

    def _add_district_stock(self, district_id, resource_id, amount):
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return
        self._set_district_stock(district_id, resource_id, current + amount)

    def _deplete_district_stock(self, district_id, resource_id, amount):
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return
        new_val = max(0, current - amount)
        self._set_district_stock(district_id, resource_id, new_val)
        if current > 0 and new_val <= 0:
            kind = self.civilization["districts"][district_id]["kind"]
            self._push_activity(
                f"The {kind} in {district_id} is depleted of {resource_id} — gathering fails here until it regrows")

    def _ecology_gather_gate(self, agent, resource_id):
        """Returns (allowed, reason, yield_scale). Non-tracked resources pass through."""
        if not ECOLOGY_ENABLED:
            return True, None, 1.0
        district_id = agent.get("currentDistrict")
        if not district_id:
            return True, None, 1.0
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return True, None, 1.0
        max_s = self._stock_max(resource_id)
        if current <= 0:
            kind = self.civilization["districts"][district_id]["kind"]
            reason = f"the {kind} here is depleted of {resource_id}"
            return False, reason, 0.0
        ratio = min(1.0, current / max_s)
        scale = max(STOCK_MIN_YIELD_RATIO, ratio)
        return True, None, scale

    def _format_district_stocks_for_prompt(self, agent):
        if not ECOLOGY_ENABLED:
            return "none"
        self._ensure_district_stocks()
        did = agent.get("currentDistrict")
        if not did:
            return "none"
        stocks = self.civilization["districtStocks"].get(did) or {}
        if not stocks:
            return "none"
        parts = []
        for rid, val in sorted(stocks.items()):
            max_s = self._stock_max(rid)
            if val <= 0:
                parts.append(f"{rid}:depleted")
            elif val < max_s * STOCK_LOW_RATIO:
                parts.append(f"{rid}:low")
            elif val < max_s * 0.5:
                parts.append(f"{rid}:fair")
            else:
                parts.append(f"{rid}:ok")
        return ", ".join(parts)

    def _tick_ecology_regrow(self):
        if not ECOLOGY_ENABLED:
            return
        self._ensure_district_stocks()
        c = self.civilization
        regrow = STOCK_REGROW_PER_TICK
        if GOODS_ENABLED:
            # Phase C seasons: spring regrows double, winter not at all --
            # the loop-closer with storage/spoilage (stockpile before winter).
            regrow *= SEASON_REGROW_MULT.get(self._current_season(), 1)
            if regrow <= 0:
                return
        for did, stocks in c["districtStocks"].items():
            kind = c["districts"].get(did, {}).get("kind", "land")
            for rid, val in list(stocks.items()):
                max_s = self._stock_max(rid)
                if val >= max_s:
                    continue
                new_val = min(max_s, val + regrow)
                if new_val == val:
                    continue
                stocks[rid] = new_val
                if val <= 0 < new_val:
                    self._push_activity(
                        f"The {kind} in {did} is recovering — {rid} stock is growing again")
                elif val < max_s * STOCK_LOW_RATIO <= new_val:
                    self._push_activity(f"{rid} stock in {did} has regrown to fair levels")

    def _ecology_scarcity_index(self):
        if not ECOLOGY_ENABLED:
            return None
        self._ensure_district_stocks()
        total, count = 0.0, 0
        for stocks in self.civilization["districtStocks"].values():
            for rid, val in stocks.items():
                max_s = self._stock_max(rid)
                total += min(1.0, val / max_s if max_s else 0)
                count += 1
        return round(total / count, 3) if count else 1.0

    def _is_project_type_deferred(self, type_):
        """Returns (deferred, frames_remaining). Clears expired deferrals."""
        c = self.civilization
        until = c.get("deferredProjectTypes", {}).get(type_)
        if not until:
            return False, 0
        if self.frameTick >= until:
            c["deferredProjectTypes"].pop(type_, None)
            c.get("projectAbandonStreak", {}).pop(type_, None)
            return False, 0
        return True, until - self.frameTick

    def _unbuilt_customs_blocking_invention(self):
        c = self.civilization
        return any(pid not in c["builtTypes"] and not self._is_project_type_deferred(pid)[0]
                   for pid in self._custom_project_ids())

    def _seed_project_from_stockpile(self, district_id, project):
        """Transfer matching stockpile materials into a newly started project."""
        c = self.civilization
        needs = project.get("needs") or {}
        contributed = project.setdefault("contributed", {})
        seeds = []
        for res, need in needs.items():
            short = need - contributed.get(res, 0)
            if short <= 0:
                continue
            available = int(c["stockpile"].get(res, 0))
            if available <= 0:
                continue
            take = min(short, available)
            c["stockpile"][res] = available - take
            contributed[res] = contributed.get(res, 0) + take
            seeds.append((take, res))
        if seeds:
            parts = ", ".join(f"{amt} {res}" for amt, res in seeds)
            self._push_activity(
                f"The village stockpile supplied {parts} toward the {project['name']}")
        return bool(seeds)

    def _largest_missing_input(self, agent, inputs):
        best = None
        best_short = 0
        for res, need in inputs.items():
            short = need - agent["resources"].get(res, 0)
            if short > best_short:
                best_short = short
                best = res
        return best

    def _craft_input_reflex(self, agent, recipe_id, recipe):
        """On missing craft inputs: gather the largest deficit deterministically."""
        missing = self._largest_missing_input(agent, recipe["inputs"])
        if not missing:
            return
        reason = f"lacks {missing} to craft {recipe_id}"
        agent["lastCraftRejection"] = {"reason": reason, "frame": self.frameTick, "resource": missing}
        allowed, _, _ = self._ecology_gather_gate(agent, missing)
        if not allowed and ECOLOGY_ENABLED:
            self._scarcity_reflex_on_depletion(agent, missing)
        elif USE_GOALS:
            agent["goal"] = {
                "kind": "craft_gather", "target": missing, "recipe": recipe_id, "ttl": 10,
            }
        else:
            gz = self._gather_zone_for_resource(missing)
            if gz and agent["currentZone"] != gz:
                self._set_agent_target(agent, gz)
        self._push_activity(
            f"{agent['name']} craft reflex: gathering {missing} for {recipe_id}")

    def _step_craft_gather_goal(self, agent, g):
        resource = g.get("target")
        if not resource:
            agent["goal"] = None
            return False
        if agent["resources"].get(resource, 0) >= self._carry_cap(agent):
            agent["goal"] = None
            return False
        gz = self._gather_zone_for_resource(resource)
        if gz and agent["currentZone"] != gz:
            self._set_agent_target(agent, gz)
            return True
        allowed, _, _ = self._ecology_gather_gate(agent, resource)
        if not allowed:
            self._scarcity_reflex_on_depletion(agent, resource)
            return True
        summary = self._perform_gather(agent, resource)
        if "found nothing" in summary:
            return True
        return True

    def _get_terraform_function(self, terraform_id):
        return TERRAFORM_FUNCTIONS.get(terraform_id) or {}

    def _stalled_approved_customs(self):
        c = self.civilization
        frames = c.get("approvedCustomApprovedFrame") or {}
        out = []
        for pid in self._custom_project_ids():
            if pid in c["builtTypes"]:
                continue
            if self._is_project_type_deferred(pid)[0]:
                continue
            if any(p and p.get("type") == pid for p in c["districtProjects"].values()):
                continue
            approved_at = frames.get(pid, c.get("lastBlueprintActivityFrame", 0))
            if self.frameTick - approved_at < APPROVED_CUSTOM_STALL_FRAMES:
                continue
            name = c["projectRegistry"][pid].get("name", pid)
            out.append((pid, name, approved_at))
        out.sort(key=lambda x: x[2])
        return out

    def _terraform_template_for_kind(self, kind):
        return KIND_TERRAFORM.get(kind)

    def _active_terraform_for_kind(self, kind):
        template = self._terraform_template_for_kind(kind)
        if not template:
            return None
        for did, p in self.civilization["districtProjects"].items():
            if p and p.get("isTerraform") and p.get("type") == template:
                return did
        return None

    def _district_highest_stock(self, resource_id):
        if not ECOLOGY_ENABLED:
            return None
        self._ensure_district_stocks()
        best_did = None
        best_val = -1
        for did, stocks in self.civilization["districtStocks"].items():
            val = stocks.get(resource_id)
            if val is not None and val > best_val:
                best_val = val
                best_did = did
        return best_did if best_val > 0 else None

    def _scarcity_reflex_migrate(self, agent, resource):
        dest = self._district_highest_stock(resource)
        if not dest:
            gz = self._gather_zone_for_resource(resource)
            if gz:
                dest = next((did for did, d in self.civilization["districts"].items()
                             if d.get("kind") == gz), None)
        if dest and dest != agent.get("currentDistrict"):
            self.apply_decision(agent, {
                "action": "move_to_district", "target": dest,
                "reasoning": f"scarcity reflex: seeking {resource}",
            })
            self._push_activity(
                f"{agent['name']} scarcity reflex: routed to {dest} for depleted {resource}")

    def _scarcity_reflex_on_depletion(self, agent, resource):
        """Deterministic response to ecology depletion (no LLM). Terraform
        contribute/start first; migrate to best-stocked district last."""
        if not ECOLOGY_ENABLED:
            return
        c = self.civilization
        did = agent.get("currentDistrict")
        if not did or did not in c.get("districts", {}):
            self._scarcity_reflex_migrate(agent, resource)
            return
        kind = c["districts"][did].get("kind")
        terraform_did = self._active_terraform_for_kind(kind)
        if terraform_did:
            p = c["districtProjects"][terraform_did]
            unmet = self._first_unmet_project_resource(terraform_did)
            if USE_GOALS:
                agent["goal"] = {
                    "kind": "gather" if unmet else "deliver",
                    "target": unmet,
                    "district": terraform_did,
                    "ttl": 10,
                }
            self._push_activity(
                f"{agent['name']} scarcity reflex: contributing to {p['name']} in {terraform_did}")
            return
        template = self._terraform_template_for_kind(kind)
        if template and not c["districtProjects"].get(did):
            summary = self._start_terraform_for(agent, template, did)
            if summary:
                self._push_activity(f"{agent['name']} scarcity reflex: {summary}")
                return
        self._scarcity_reflex_migrate(agent, resource)

    def _start_terraform_for(self, agent, target, target_district=None):
        c = self.civilization
        tmpl = TERRAFORM_TEMPLATES.get(target) if target else None
        if not tmpl:
            return None
        if len(self._active_project_districts()) >= MAX_CONCURRENT_PROJECTS:
            return None
        kind = tmpl["kind"]
        district_id = None
        if target_district and c["districts"].get(target_district, {}).get("kind") == kind \
                and not c["districtProjects"].get(target_district):
            district_id = target_district
        if not district_id:
            cur = agent.get("currentDistrict")
            if cur and c["districts"].get(cur, {}).get("kind") == kind \
                    and not c["districtProjects"].get(cur):
                district_id = cur
        if not district_id:
            candidates = [did for did, d in c["districts"].items()
                          if d.get("kind") == kind and not c["districtProjects"].get(did)]
            if candidates:
                district_id = min(candidates, key=lambda did: self._distance_to_district(agent, did))
        if not district_id:
            return None
        c["districtProjects"][district_id] = {
            "type": target, "name": tmpl["name"], "needs": dict(tmpl["needs"]),
            "contributed": {res: 0 for res in tmpl["needs"]},
            "districtId": district_id, "isTerraform": True,
        }
        self._seed_project_from_stockpile(district_id, c["districtProjects"][district_id])
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        return f"{agent['name']} started {tmpl['name']} terraform in {district_id}"

    def _apply_terraform_modifiers(self, district_id, function):
        c = self.civilization
        self._ensure_district_stocks()
        for mod in function.get("modifies") or []:
            if mod.get("target") != "stock":
                continue
            scope_did = district_id if mod.get("scope", "district") == "district" else district_id
            for rid in mod.get("resources") or []:
                max_s = self._stock_max(rid)
                if mod.get("set_ratio") is not None:
                    self._set_district_stock(scope_did, rid, int(max_s * mod["set_ratio"]))
                elif mod.get("add"):
                    self._add_district_stock(scope_did, rid, mod["add"])
        if function.get("found_district") in DISTRICT_KIND_TEMPLATES:
            plot = self._claim_frontier_plot()
            if plot:
                self._found_district(function["found_district"],
                                     DISTRICT_KIND_TEMPLATES[function["found_district"]], plot)

    def _complete_terraform(self, agent, district_id):
        c = self.civilization
        project = c["districtProjects"].get(district_id)
        if not project or not project.get("isTerraform"):
            return f"{agent['name']} has nothing to terraform"
        tid = project["type"]
        tmpl = TERRAFORM_TEMPLATES.get(tid) or {}
        fn = tmpl.get("function") or self._get_terraform_function(tid)
        self._apply_terraform_modifiers(district_id, fn)
        name = project["name"]
        c["districtProjects"][district_id] = None
        c["completedProjects"] += 1
        agent["lastContributedFrame"] = self.frameTick
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        self._check_civilization_level()
        self._push_activity(f"{agent['name']} completed {name} — the land in {district_id} has changed")
        return f"{agent['name']} completed {name} in {district_id}"

    def _try_contribute_resource(self, agent, res, district_id=None):
        district_id = district_id or self._resolve_contribution_district(agent)
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p or not res:
            return None
        need = p["needs"].get(res, 0)
        have = p["contributed"].get(res, 0)
        if have >= need or agent["resources"].get(res, 0) <= 0:
            return None
        agent["resources"][res] -= 1
        p["contributed"][res] = have + 1
        agent["lastContributedFrame"] = self.frameTick
        self.civilization["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(self.civilization["districts"][district_id]["kind"])
        self._enforce_resource_tax(agent, res)
        return f"{agent['name']} contributed {res} to {p['name']} ({district_id})"

    def _is_project_complete(self, district_id):
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return False
        return all(p["contributed"].get(res, 0) >= need for res, need in p["needs"].items())

    def _district_structure_count(self, district_id):
        return sum(1 for s in self.civilization["structures"] if s.get("districtId") == district_id)

    def _structure_count(self, type_, district_id=None):
        return sum(1 for s in self.civilization["structures"]
                   if s.get("type") == type_
                   and (district_id is None or s.get("districtId") == district_id))

    # --- structure function registry (Phase A consequence engine) ---
    def _get_structure_function(self, type_):
        if not STRUCTURE_EFFECTS_ENABLED:
            return {}
        c = self.civilization
        tmpl = c["projectRegistry"].get(type_) or PROJECT_TEMPLATES.get(type_) or {}
        fn = tmpl.get("function")
        if fn:
            return fn
        if type_ in SEED_STRUCTURE_FUNCTIONS:
            return SEED_STRUCTURE_FUNCTIONS[type_]
        if tmpl.get("custom"):
            return {"produces": [dict(LEGACY_CUSTOM_PRODUCE)]}
        return {}

    def _canonical_effect_vector(self, function):
        return self.d["canonical_effect_vector"](function)

    def _known_effect_vectors(self):
        c = self.civilization
        vectors = set()
        for tid, fn in SEED_STRUCTURE_FUNCTIONS.items():
            vec = self._canonical_effect_vector(fn)
            if vec:
                vectors.add(vec)
        for pid in c["projectRegistry"]:
            fn = self._get_structure_function(pid)
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    vectors.add(vec)
        for bp in c["pendingBlueprints"]:
            fn = bp.get("function")
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    vectors.add(vec)
        return vectors

    def _structure_display_name(self, type_id):
        c = self.civilization
        return (c["projectRegistry"].get(type_id) or PROJECT_TEMPLATES.get(type_id) or {}).get("name", type_id)

    # --- Phase C query-time helpers (GOODS_ENABLED) ---
    def _working_structure_count(self, type_, district_id=None):
        """Structures still functional under decay: condition >= the disrepair
        threshold (ruins are 0 and never count). With GOODS_ENABLED off this
        is exactly _structure_count, so Phase A/B behavior is unchanged."""
        if not GOODS_ENABLED:
            return self._structure_count(type_, district_id)
        return sum(1 for s in self.civilization["structures"]
                   if s.get("type") == type_
                   and (district_id is None or s.get("districtId") == district_id)
                   and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)

    def _carry_cap(self, agent):
        """Per-agent carry cap: COLLECT_CAP, +CART_CARRY_BONUS while holding a
        cart (query-time vehicle effect, like _gather_yield_bonus)."""
        if GOODS_ENABLED and agent["resources"].get("cart", 0) > 0:
            return COLLECT_CAP + CART_CARRY_BONUS
        return COLLECT_CAP

    def _storage_capacity(self, resource_id):
        """Village-wide storage capacity for a resource: the base camp pile
        plus every working structure's `stores` entries (Phase A function
        registry -- accepted by validate_function_block since Phase A, made
        real here)."""
        cap = BASE_STORAGE_CAPACITY
        if not STRUCTURE_EFFECTS_ENABLED:
            return cap
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for store in fn.get("stores") or []:
                if store.get("resource") != resource_id:
                    continue
                cap += store.get("capacity", 0) * self._working_structure_count(type_id)
        return cap

    def _current_season(self):
        """Four-season clock derived from frameTick (no persisted state)."""
        if not GOODS_ENABLED:
            return None
        return SEASONS[(self.frameTick // SEASON_FRAMES) % len(SEASONS)]

    def _gather_yield_bonus(self, agent, resource):
        if not STRUCTURE_EFFECTS_ENABLED:
            return 0
        district = agent.get("currentDistrict")
        bonus = 0
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for boost in fn.get("boosts") or []:
                if boost.get("kind") != "gather":
                    continue
                resources = boost.get("resources") or []
                if resource not in resources:
                    continue
                scope = boost.get("scope", "district")
                count = self._working_structure_count(type_id, district if scope == "district" else None)
                every_n = boost.get("every_n", 1)
                max_bonus = boost.get("max_bonus", 1)
                bonus += min(max_bonus, (count // every_n) * boost.get("bonus", 1))
        return bonus

    def _craft_station_unlocked(self, station):
        if not STRUCTURE_EFFECTS_ENABLED or not station:
            return True
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") == "craft" and unlock.get("station") == station \
                        and self._working_structure_count(type_id) > 0:
                    return True
        return False

    def _craft_output_bonus(self, recipe, district_id=None):
        if not STRUCTURE_EFFECTS_ENABLED:
            return 0
        station = recipe.get("station")
        if not station:
            return 0
        bonus = 0
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for boost in fn.get("boosts") or []:
                if boost.get("kind") != "craft" or boost.get("station") != station:
                    continue
                scope = boost.get("scope", "village")
                count = self._working_structure_count(type_id, district_id if scope == "district" else None)
                every_n = boost.get("every_n", 1)
                max_bonus = boost.get("max_bonus", 1)
                bonus += min(max_bonus, (count // every_n) * boost.get("bonus", 1))
        return bonus

    def _deposit_produced(self, resource, amount, type_id, district_id=None):
        c = self.civilization
        if resource not in c["resourceRegistry"]:
            return
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            if district_id and self._district_stock(district_id, resource) is not None:
                self._add_district_stock(district_id, resource, amount)
            else:
                dids = [did for did, stocks in c["districtStocks"].items()
                        if resource in stocks]
                if dids:
                    share = max(1, amount // len(dids))
                    for did in dids:
                        self._add_district_stock(did, resource, share)
                else:
                    c["stockpile"][resource] = c["stockpile"].get(resource, 0) + amount
        else:
            c["stockpile"][resource] = c["stockpile"].get(resource, 0) + amount
        name = self._structure_display_name(type_id)
        where = f" in {district_id}" if district_id else ""
        self._push_activity(f"{name} produced {amount} {resource}{where}")

    def _tick_structure_effects(self):
        """Apply tick-time produces (and log every firing). Boosts/unlocks/houses
        are query-time via the registry helpers above."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return
        c = self.civilization
        last_fire = c.setdefault("effectLastFire", {})
        built_types = {s["type"] for s in c["structures"]}
        for type_id in built_types:
            fn = self._get_structure_function(type_id)
            for prod in fn.get("produces") or []:
                resource = prod.get("resource")
                every = prod.get("every_ticks", 600)
                fire_key = f"{type_id}:{resource}:{prod.get('scope', 'village')}"
                if self.frameTick - last_fire.get(fire_key, -every) < every:
                    continue
                scope = prod.get("scope", "village")
                amount_each = prod.get("amount", 1)
                # Phase C: only structures in working condition produce
                # (_working_structure_count == _structure_count with GOODS off).
                if scope == "district":
                    for did in {s.get("districtId") for s in c["structures"] if s["type"] == type_id}:
                        count = self._working_structure_count(type_id, did)
                        if count <= 0:
                            continue
                        total = amount_each * count
                        self._deposit_produced(resource, total, type_id, did)
                        self._effect_period_fired += 1
                else:
                    count = self._working_structure_count(type_id)
                    if count <= 0:
                        continue
                    total = amount_each * count
                    self._deposit_produced(resource, total, type_id)
                    self._effect_period_fired += 1
                last_fire[fire_key] = self.frameTick

    # --- Phase C tick mechanics (GOODS_ENABLED): spoilage / decay / disaster / shelter ---
    def _tick_goods(self):
        """The slow goods tick (GOODS_TICK_FRAMES): season bookkeeping,
        edible spoilage beyond storage capacity, structure decay, and the
        rare disaster. All deterministic -- no LLM involvement."""
        if not GOODS_ENABLED:
            return
        season = self._current_season()
        if season != self._last_season:
            self._last_season = season
            note = " -- district stocks will not regrow until spring" if season == "winter" else ""
            self._push_activity(f"The season turns: {season} begins{note}")
            self._log_benchmark("season_turn", SEASONS.index(season), {"season": season})
        self._tick_spoilage()
        self._tick_structure_decay()
        self._maybe_disaster()

    def _tick_spoilage(self):
        """Edibles beyond village storage capacity rot: SPOILAGE_RATIO of the
        overflow per goods tick (min 1), stockpile first, then the largest
        holders -- never below EDIBLE_RESERVE, so spoilage cannot starve
        anyone. The escape is storage: build a structure with a `stores`
        effect (granary), or eat/contribute the surplus."""
        c = self.civilization
        for rid in EDIBLE_RESOURCES:
            cap = self._storage_capacity(rid)
            stock = c["stockpile"].get(rid, 0)
            held = sum(a["resources"].get(rid, 0) for a in self.agents)
            overflow = stock + held - cap
            if overflow <= 0:
                continue
            to_spoil = min(overflow, max(1, int(overflow * SPOILAGE_RATIO)))
            spoiled = min(to_spoil, stock)
            if spoiled > 0:
                c["stockpile"][rid] = stock - spoiled
            while spoiled < to_spoil:
                holders = [a for a in self.agents
                           if a["resources"].get(rid, 0) > EDIBLE_RESERVE]
                if not holders:
                    break
                top = max(holders, key=lambda a: a["resources"].get(rid, 0))
                top["resources"][rid] -= 1
                spoiled += 1
            if spoiled <= 0:
                continue
            self._spoiled_period += spoiled
            reason = (f"{spoiled} {rid} spoiled -- the village holds more than its "
                      f"storage capacity ({cap}). Build storage (a granary or a "
                      f"blueprint with a stores effect) or eat/contribute the surplus")
            c["lastSpoilage"] = {"reason": reason, "frame": self.frameTick}
            self._push_activity(reason[0].upper() + reason[1:])

    def _tick_structure_decay(self):
        """Structures decay STRUCTURE_DECAY_PER_GOODS_TICK per goods tick.
        Below STRUCTURE_DISREPAIR_THRESHOLD they stop working (produces/
        boosts/unlocks/houses all go through _working_structure_count); at 0
        they collapse into a ruin, rebuildable via repair_structure for half
        the original materials (the deterministic escape)."""
        c = self.civilization
        for s in c["structures"]:
            cond = s.get("condition", 100.0)
            if cond <= 0:
                continue
            new_cond = max(0.0, cond - STRUCTURE_DECAY_PER_GOODS_TICK)
            s["condition"] = new_cond
            name = s.get("name") or s.get("type")
            did = s.get("districtId") or "the village"
            if cond >= STRUCTURE_DISREPAIR_THRESHOLD > new_cond:
                self._push_activity(
                    f"The {name} in {did} has fallen into disrepair -- it stops "
                    f"working until someone uses repair_structure")
            if new_cond <= 0:
                s["isRuin"] = True
                self._push_activity(
                    f"The {name} in {did} has collapsed into a ruin! "
                    f"repair_structure can rebuild it for half the original materials")

    def _maybe_disaster(self):
        """Rare random structure damage (DISASTER_PROB per goods tick), so
        decay isn't perfectly predictable and repair stays relevant even in a
        well-kept village. Logged dramatically; the standard escape applies
        (repair, or rebuild from the ruin)."""
        c = self.civilization
        candidates = [s for s in c["structures"] if s.get("condition", 100) > 0]
        if not candidates or random.random() >= DISASTER_PROB:
            return
        s = random.choice(candidates)
        dmg = random.randint(*DISASTER_DAMAGE)
        s["condition"] = max(0.0, s.get("condition", 100.0) - dmg)
        name = s.get("name") or s.get("type")
        did = s.get("districtId") or "the village"
        line = (f"DISASTER! A storm tears through the {name} in {did} -- "
                f"{dmg} damage (condition {int(s['condition'])})")
        if s["condition"] <= 0:
            s["isRuin"] = True
            line += "; it lies in ruins"
        self._push_activity(line)
        self._log_benchmark("disaster_damage", dmg,
                            {"structure": s.get("type"), "district": did})

    def _tick_shelter(self):
        """Nightly (every DAY_FRAMES): each working house shelters
        HOUSE_SHELTER_OCCUPANTS villagers (nearest to a house first);
        everyone else spends the night outside and loses a little hunger --
        a surfaced nudge, never a hard punishment (floored at
        SHELTER_HUNGER_FLOOR, above the starvation band). This is what makes
        houses consumed nightly instead of just population math."""
        if not GOODS_ENABLED or not SURVIVAL_ENABLED:
            return
        c = self.civilization
        house_structs = []
        for type_id in {s["type"] for s in c["structures"]}:
            if (self._get_structure_function(type_id) or {}).get("houses"):
                house_structs.extend(
                    s for s in c["structures"]
                    if s["type"] == type_id
                    and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)
        slots = len(house_structs) * HOUSE_SHELTER_OCCUPANTS
        if slots >= len(self.agents):
            self._push_activity("Night falls -- every villager has a roof tonight")
            return

        def dist_to_house(a):
            if not house_structs:
                return float("inf")
            return min(_dist(a["x"], a["y"], s["x"], s["y"]) for s in house_structs)

        unsheltered = sorted(self.agents, key=dist_to_house)[slots:]
        penalized = 0
        for a in unsheltered:
            if a["incapacitated"] or a["hunger"] <= SHELTER_HUNGER_FLOOR:
                continue
            a["hunger"] = max(SHELTER_HUNGER_FLOOR, a["hunger"] - SHELTER_HUNGER_PENALTY)
            a["lastShelterNote"] = {
                "reason": (f"you spent the night outside -- {len(house_structs)} working "
                           f"house(s) shelter only {slots} of {len(self.agents)} villagers"),
                "frame": self.frameTick,
            }
            penalized += 1
        if penalized:
            self._push_activity(
                f"Night falls -- {penalized} villager(s) had no shelter "
                f"({len(house_structs)} working houses, {slots} beds for {len(self.agents)})")

    # --- Phase C: repair_structure (the decay escape hatch) ---
    def _find_repair_target(self, agent, target):
        """Resolve a repair target: explicit structure id/type/name first
        (worst-condition match wins), else the worst STANDING damaged structure
        (cheap 1-unit upkeep that keeps it working -- the Phase C test is
        'repairs a decaying structure BEFORE it collapses'), falling back to
        ruins (expensive half-rebuild) only when nothing standing is damaged.
        2026-07-07 audit: plain min(condition) always chose a ruin, so every
        repair turn hit the multi-resource rebuild cost and failed. District
        preference applies within each tier."""
        c = self.civilization
        damaged = [s for s in c["structures"]
                   if s.get("isRuin") or s.get("condition", 100) < 100]
        if not damaged:
            return None
        if target:
            t = str(target).strip().lower()
            matches = [s for s in damaged
                       if str(s.get("id")) == t
                       or (s.get("type") or "").lower() == t
                       or (s.get("name") or "").lower() == t]
            if matches:
                return min(matches, key=lambda s: s.get("condition", 100))
        standing = [s for s in damaged
                    if not s.get("isRuin") and s.get("condition", 100) > 0]
        tier = standing or damaged
        local = [s for s in tier if s.get("districtId") == agent.get("currentDistrict")]
        pool = local or tier
        return min(pool, key=lambda s: s.get("condition", 100))

    def _repair_cost(self, structure):
        """Normal repair: 1 unit of the structure's primary material per
        +REPAIR_CONDITION_RESTORE. Ruin rebuild: half the original needs
        (min 1 each) -- deliberately cheaper than starting a new project."""
        c = self.civilization
        tmpl = c["projectRegistry"].get(structure.get("type")) \
            or PROJECT_TEMPLATES.get(structure.get("type")) or {}
        needs = tmpl.get("needs") or {"wood": 2}
        if structure.get("isRuin") or structure.get("condition", 100) <= 0:
            return {res: max(1, amt // 2) for res, amt in needs.items()}
        primary = next(iter(needs), "wood")
        return {primary: 1}

    def _repair_structure(self, agent, target):
        s = self._find_repair_target(agent, target)
        if not s:
            agent["lastRepairRejection"] = {
                "reason": "nothing needs repair right now", "frame": self.frameTick}
            return f"{agent['name']} found nothing that needs repair"
        cost = self._repair_cost(s)
        name = s.get("name") or s.get("type")
        # Fund each resource from the agent's inventory first, then the village
        # stockpile (2026-07-07 audit: repairs drew from personal inventory
        # only, so 320 repair attempts failed while the stockpile held 29k
        # planks -- the gather->contribute loop could never fund the escape
        # hatch). Refuse only when both together fall short.
        c = self.civilization
        plan = {}
        missing = []
        for res, amt in cost.items():
            held = agent["resources"].get(res, 0)
            from_agent = min(held, amt)
            from_stock = amt - from_agent
            if from_stock > int(c["stockpile"].get(res, 0)):
                missing.append(res)
            else:
                plan[res] = (from_agent, from_stock)
        if missing:
            cost_str = ", ".join(f"{amt} {res}" for res, amt in cost.items())
            agent["lastRepairRejection"] = {
                "reason": (f"repairing the {name} needs {cost_str} -- you and the "
                           f"village stockpile together lack {', '.join(missing)}"),
                "frame": self.frameTick}
            return f"{agent['name']} lacks {', '.join(missing)} to repair the {name}"
        stock_parts = []
        for res, (from_agent, from_stock) in plan.items():
            if from_agent:
                agent["resources"][res] -= from_agent
            if from_stock:
                c["stockpile"][res] = int(c["stockpile"].get(res, 0)) - from_stock
                stock_parts.append(f"{from_stock} {res}")
        if stock_parts:
            self._push_activity(
                f"The village stockpile supplied {', '.join(stock_parts)} for "
                f"{agent['name']}'s repair of the {name}")
        was_ruin = bool(s.get("isRuin")) or s.get("condition", 100) <= 0
        if was_ruin:
            s["condition"] = 100.0
            s["isRuin"] = False
            summary = f"{agent['name']} rebuilt the {name} from its ruins in {s.get('districtId')}"
        else:
            s["condition"] = min(100.0, s.get("condition", 100.0) + REPAIR_CONDITION_RESTORE)
            summary = (f"{agent['name']} repaired the {name} in {s.get('districtId')} "
                       f"(condition {int(s['condition'])})")
        agent["lastRepairRejection"] = None
        self._log_benchmark("structure_repaired", int(s["condition"]),
                            {"structure": s.get("type"), "ruin_rebuild": was_ruin})
        return summary

    def _population_cap(self):
        c = self.civilization
        base = c.get("basePopulation") or len(self.agents)
        cap = base
        if STRUCTURE_EFFECTS_ENABLED:
            for type_id in {s["type"] for s in c["structures"]} | set(SEED_STRUCTURE_FUNCTIONS):
                fn = self._get_structure_function(type_id)
                houses = fn.get("houses")
                if houses:
                    every_n = houses.get("every_n", HOUSES_PER_NEW_VILLAGER)
                    cap += self._working_structure_count(type_id) // every_n
        return min(len(AGENT_DEFS), cap)

    def _type_saturated(self, type_):
        """Soft cap per structure type, derived from what the type actually
        does, so building past the cap is provably waste. Saturated types are
        skipped by role defaults, refused by _start_project_for, and count as
        'exhausted' toward the invention gate. Deliberately counts TOTAL
        structures (not Phase C working ones): a district full of decayed
        houses should be repaired, not built over."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return False
        c = self.civilization
        count = self._structure_count(type_)
        fn = self._get_structure_function(type_)
        houses = fn.get("houses")
        if houses:
            base = c.get("basePopulation") or len(self.agents)
            every_n = houses.get("every_n", HOUSES_PER_NEW_VILLAGER)
            return count >= (len(AGENT_DEFS) - base) * every_n + 3
        for boost in fn.get("boosts") or []:
            if boost.get("kind") == "gather":
                every_n = boost.get("every_n", FARM_PLOTS_PER_EXTRA)
                max_bonus = boost.get("max_bonus", FARM_YIELD_BONUS_CAP)
                farm_districts = sum(1 for d in c["districts"].values()
                                     if d["kind"] == "farm" and d.get("build_grid"))
                return count >= every_n * max_bonus * max(1, farm_districts)
            if boost.get("kind") == "craft":
                eligible = sum(1 for d in c["districts"].values()
                               if d["kind"] in ("village", "workshop") and d.get("build_grid"))
                return count >= WORKSHOP_DISTRICT_CAP * max(1, eligible)
        if type_ == "wall":
            return count >= WALL_SOFT_CAP
        return count >= CUSTOM_SOFT_CAP

    def _find_structure_spot(self, district_id):
        d = self.civilization["districts"].get(district_id)
        grid = d.get("build_grid") if d else None
        if not grid:
            b = d["bounds"] if d else {"x1": 0, "y1": 0}
            return {"x": b["x1"], "y": b["y1"]}
        existing = [s for s in self.civilization["structures"] if s.get("districtId") == district_id]
        cap = grid.get("cap", 30)
        for i in range(cap):
            x = grid["x0"] + (i % grid["cols"]) * grid["dx"]
            y = grid["y0"] + (i // grid["cols"]) * grid["dy"]
            taken = any(abs(s["x"] - x) < 70 and abs(s["y"] - y) < 80 for s in existing)
            if not taken:
                return {"x": x, "y": y}
        return None  # district's build grid is at capacity

    def _check_civilization_level(self):
        new_level = (self.civilization["completedProjects"] // 3) + 1
        if new_level > self.civilization["level"]:
            self.civilization["level"] = new_level
            self._push_activity(f"Civilization reached level {self.civilization['level']}!")

    def _build_active_structure(self, agent, district_id=None):
        c = self.civilization
        district_id = district_id or self._resolve_contribution_district(agent)
        project = c["districtProjects"].get(district_id) if district_id else None
        if not project:
            return f"{agent['name']} has nothing to build"
        if project.get("isTerraform"):
            return self._complete_terraform(agent, district_id)
        spot = self._find_structure_spot(district_id)
        if not spot:
            return f"{agent['name']} finds {district_id} has no room left to build"
        struct_type = project["type"]
        c["structures"].append({
            "id": c["nextStructureId"], "type": struct_type,
            "x": spot["x"], "y": spot["y"],
            "visualStyle": project.get("visualStyle") or "generic",
            "sprite": project.get("sprite"),
            "name": project["name"], "districtId": district_id,
            # Phase C decay stat; every read uses .get(default 100) so
            # structures from pre-Phase-C saves need no migration.
            "condition": 100.0, "isRuin": False,
        })
        c["nextStructureId"] += 1
        built_name = project["name"]
        c["districtProjects"][district_id] = None
        c["completedProjects"] += 1
        c["builtTypes"].add(struct_type)
        c.get("projectAbandonStreak", {}).pop(struct_type, None)
        c.get("deferredProjectTypes", {}).pop(struct_type, None)
        agent["lastContributedFrame"] = self.frameTick
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        self._check_civilization_level()
        return f"{agent['name']} built {built_name} in {district_id}"

    def _perform_gather(self, agent, resource):
        """Ecology-aware gather with structure boosts. Returns summary string."""
        c = self.civilization
        allowed, reason, scale = self._ecology_gather_gate(agent, resource)
        if not allowed:
            agent["lastGatherRejection"] = {"reason": reason, "frame": self.frameTick}
            self._scarcity_reflex_on_depletion(agent, resource)
            return f"{agent['name']} found nothing — {reason}"
        amount = 1
        if STRUCTURE_EFFECTS_ENABLED:
            amount += self._gather_yield_bonus(agent, resource)
        if ECOLOGY_ENABLED and scale < 1.0:
            amount = max(1, int(amount * scale))
        amount = max(1, min(amount, self._carry_cap(agent) - agent["resources"].get(resource, 0)))
        agent["resources"][resource] = agent["resources"].get(resource, 0) + amount
        c["collectSuccesses"] += 1
        if ECOLOGY_ENABLED:
            did = agent.get("currentDistrict")
            if did:
                self._deplete_district_stock(
                    did, resource, amount * STOCK_DEPLETE_MULTIPLIER)
        agent["lastGatherRejection"] = None
        bonus_note = ""
        if amount > 1:
            bonus_note = " (structure effects boosted the harvest)"
        return f"{agent['name']} collected {resource}" + (f" x{amount}{bonus_note}" if amount > 1 else "")

    def _project_resource_list(self, project):
        return " and ".join(project["needs"].keys())

    def _role_default_project(self, role):
        pref = self.d["ROLE_PROJECT"].get((role or "").lower(), "house")
        prefs = pref if isinstance(pref, list) else [pref]
        prefs = prefs or ["house"]
        open_prefs = [p for p in prefs if not self._type_saturated(p)
                      and not self._is_project_type_deferred(p)[0]]
        if open_prefs:
            return random.choice(open_prefs)
        # Every preferred type is saturated: fall back to any unsaturated
        # registry type (this is what steers the default loop toward the
        # granary and approved customs once the basics are overbuilt).
        fallback = [tid for tid in self.civilization["projectRegistry"]
                    if not self._type_saturated(tid)
                    and not self._is_project_type_deferred(tid)[0]]
        if fallback:
            return random.choice(fallback)
        return prefs[0]

    def _seed_exhausted(self, tid):
        """A seed template no longer blocks the invention gate once it is
        built, saturated past its soft cap, or -- for a never-built seed that
        depends on crafted goods (the granary) -- once crafting itself has
        stalled. Without that last clause a dead craft chain would freeze all
        progression: everything else saturated, the granary unreachable, and
        invention never armed. A deferred type counts as exhausted for the
        same reason: while it can't be started, it must not hold the
        invention gate shut (2026-07-05 evening soak: healthy crafting kept
        the stall clause False while the granary cycled through deferrals,
        so nothing was buildable AND invention never armed)."""
        c = self.civilization
        if tid in c["builtTypes"] or self._type_saturated(tid):
            return True
        if self._is_project_type_deferred(tid)[0]:
            return True
        if not STRUCTURE_EFFECTS_ENABLED:
            return False
        tmpl = c["projectRegistry"].get(tid) or PROJECT_TEMPLATES.get(tid) or {}
        needs_crafted = any(r in self.RECIPES for r in tmpl.get("needs", {}))
        return needs_crafted and \
            self.frameTick - c["lastCraftActivityFrame"] > CRAFT_STALL_THRESHOLD

    def _invention_required(self):
        """Blueprint-gated progression (#5.1): true once no productive seed
        option remains (every seed PROJECT_TEMPLATES id is exhausted per
        _seed_exhausted) AND there is no approved-but-unbuilt custom project
        sitting in projectRegistry -- i.e. the village can only keep growing
        through propose_blueprint."""
        c = self.civilization
        if len(self._custom_project_ids()) >= MAX_APPROVED_CUSTOM:
            # Safety net: validate_blueprint rejects every proposal past this
            # cap, so demanding invention here is a deadlock (the 2026-07-02
            # session spun for hours on it). _maybe_retire_blueprint normally
            # frees a slot first; if it can't, the village is fully developed.
            return False
        if not all(self._seed_exhausted(tid) for tid in PROJECT_TEMPLATES):
            return False
        # Invention is required when NO approved custom is left to pursue
        # (all built or deferred). The loop-back #3 refactor dropped this
        # negation, inverting the gate: it read "required" only while an
        # unbuilt custom existed, and went permanently False once the
        # village finished building everything (2026-07-05 evening audit).
        return not self._unbuilt_customs_blocking_invention()

    def _start_project_for(self, agent, target, target_district=None):
        c = self.civilization
        explicit = bool(target and target in c["projectRegistry"])
        type_ = target if explicit else self._role_default_project(agent["role"])
        if not explicit:
            # Bias the default (role-based) pick toward an approved-but-
            # unbuilt custom project of the same kind, before any seed
            # repeat -- this is what makes invention pay off even before
            # it's strictly required.
            preferred_kind = PROJECT_KIND.get(type_, "village")
            biased = next((pid for pid in self._custom_project_ids()
                           if pid not in c["builtTypes"]
                           and not self._is_project_type_deferred(pid)[0]
                           and PROJECT_KIND.get(pid, "village") == preferred_kind), None)
            if biased:
                type_ = biased
        tmpl = c["projectRegistry"].get(type_)
        if not tmpl:
            return None
        deferred, _ = self._is_project_type_deferred(type_)
        if deferred:
            name = tmpl.get("name", type_)
            agent["lastProjectRejection"] = {
                "reason": f"{name} is deferred after repeated abandonments — try another project",
                "frame": self.frameTick,
            }
            return (f"{agent['name']} cannot start {name} — deferred after repeated abandonments")
        if self._invention_required() and not tmpl.get("custom"):
            return (f"{agent['name']} wants to build, but the village needs a NEW invention "
                    f"(propose_blueprint)")
        if self._type_saturated(type_):
            # Only suggest an alternative the agent can actually start:
            # deferred types and types with an active duplicate both get
            # deterministically rejected, so naming them here just rams
            # agents into a wall (471 such nudges in the 2026-07-05 soak).
            alt = next((tid for tid in c["projectRegistry"]
                        if not self._type_saturated(tid)
                        and not self._is_project_type_deferred(tid)[0]
                        and not any(p and p.get("type") == tid
                                    for p in c["districtProjects"].values())), None)
            if alt:
                return (f"{agent['name']} wants to build a {tmpl['name']}, but the village has "
                        f"enough of those -- build a {c['projectRegistry'][alt]['name']} instead, "
                        f"or propose_blueprint")
            return (f"{agent['name']} wants to build, but every known structure is at capacity -- "
                    f"the village needs a NEW invention (propose_blueprint)")
        active_count = len(self._active_project_districts())
        if active_count >= MAX_CONCURRENT_PROJECTS:
            return None
        dup_did = next((did for did, p in c["districtProjects"].items()
                        if p and p.get("type") == type_), None)
        if dup_did:
            name = tmpl["name"]
            agent["lastProjectRejection"] = {
                "reason": f"a {name} project is already active in {dup_did}",
                "frame": self.frameTick,
            }
            return (f"{agent['name']} cannot start another {name} — "
                    f"one is already underway in {dup_did}")
        district_id = self._resolve_build_district(agent, type_, target_district)
        if not district_id or c["districtProjects"].get(district_id):
            return None
        contributed = {res: 0 for res in tmpl["needs"]}
        c["districtProjects"][district_id] = {
            "type": type_, "name": tmpl["name"], "needs": dict(tmpl["needs"]),
            "contributed": contributed, "visualStyle": tmpl.get("visualStyle") or "generic",
            "sprite": tmpl.get("sprite"),
            "districtId": district_id,
        }
        self._seed_project_from_stockpile(district_id, c["districtProjects"][district_id])
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        if agent["role"] == "elder":
            # No trailing period: the prompt nudge templates this as
            # "Your leader directs: {directive}. Prioritize it."
            c["directive"] = (f"Elder {agent['name']} directs: build the {tmpl['name']} in {district_id}; "
                              f"gather {self._project_resource_list(tmpl)}")
            c["directiveFrame"] = self.frameTick
            return f"{agent['name']} started {tmpl['name']} project in {district_id}. {c['directive']}"
        return f"{agent['name']} started {tmpl['name']} project in {district_id}"

    def _current_directive(self):
        """The leader directive, or None once it has aged past its TTL
        (covers stale directives restored from state.json too)."""
        c = self.civilization
        if not c["directive"]:
            return None
        if self.frameTick - c.get("directiveFrame", 0) > DIRECTIVE_TTL_FRAMES:
            return None
        return c["directive"]

    def _is_idle(self, agent):
        return agent["role"] != "elder" and (
            agent["lastAction"] is None or agent["lastAction"] == "rest"
            or agent.get("idleCycles", 0) >= 2)

    def _idle_agents_for_elder(self):
        # Re-task cooldown: an agent tasked recently isn't offered to the
        # elder again, so the MAIN RULE can't spend every elder turn
        # re-announcing directives at the same villagers (the 2026-07-02
        # session logged 1,556 elder directives vs 19 villager speeches).
        idle = [a for a in self.agents if self._is_idle(a)
                and (a["lastTaskedFrame"] is None
                     or self.frameTick - a["lastTaskedFrame"] > ELDER_RETASK_COOLDOWN_FRAMES)]
        idle.sort(key=lambda a: (a["lastTaskedFrame"] if a["lastTaskedFrame"] is not None
                                 else float("-inf")))
        return idle

    def _task_for_agent(self, agent):
        c = self.civilization
        district_id = self._resolve_contribution_district(agent)
        ap = c["districtProjects"].get(district_id) if district_id else None
        if ap:
            lacking = next((res for res in ap["needs"]
                            if ap["contributed"].get(res, 0) < ap["needs"][res]), None)
            if lacking:
                return f"gather or contribute {lacking} to the {ap['name']} in {district_id}"
            return f"help finish the {ap['name']} in {district_id}"
        project = c["projectRegistry"].get(self._role_default_project(agent["role"])) \
            or c["projectRegistry"]["house"]
        return f"prepare to start a {project['name']} project"

    # --- crafting ---
    def _has_inputs(self, agent, inputs):
        return all(agent["resources"].get(r, 0) >= n for r, n in inputs.items())

    def _craft_item(self, agent, recipe_id):
        recipe = self.RECIPES.get(recipe_id) if recipe_id else None
        if not recipe:
            station = agent["currentZone"]
            affordable = None
            for rid, r in self.RECIPES.items():
                if (not r.get("station") or r["station"] == station) and self._has_inputs(agent, r["inputs"]):
                    affordable = rid
                    break
            if not affordable:
                return f"{agent['name']} has nothing to craft"
            return self._craft_item(agent, affordable)
        if recipe.get("station") and agent["currentZone"] != recipe["station"]:
            self._set_agent_target(agent, recipe["station"])
            return f"{agent['name']} heads to the {recipe['station']} to craft {recipe_id}"
        # Workshop-station recipes need a physical Workshop somewhere in the
        # village (structures of type "workshop" are placed in village-kind
        # districts, so this is a village-wide check, not a per-district one).
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop" \
                and not self._craft_station_unlocked("workshop"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Workshop built yet"
        if not self._has_inputs(agent, recipe["inputs"]):
            self._craft_input_reflex(agent, recipe_id, recipe)
            missing = self._largest_missing_input(agent, recipe["inputs"])
            return f"{agent['name']} lacks {missing} to craft {recipe_id}"
        for r, n in recipe["inputs"].items():
            agent["resources"][r] -= n
        output = 1
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop":
            output += self._craft_output_bonus(recipe, agent.get("currentDistrict"))
        agent["resources"][recipe_id] = agent["resources"].get(recipe_id, 0) + output
        agent["lastCraftRejection"] = None
        self.civilization["lastCraftActivityFrame"] = self.frameTick
        return f"{agent['name']} crafted {recipe_id}" \
            + (f" x{output} (well-equipped workshops)" if output > 1 else "")

    def _custom_recipe_count(self):
        return len([rid for rid in self.RECIPES if rid not in ("planks", "bricks", "tools")])

    def _validate_recipe(self, rc):
        c = self.civilization
        if not CRAFTING_ENABLED or not isinstance(rc, dict):
            return False
        if len(c["pendingRecipes"]) >= MAX_PENDING_BLUEPRINTS:
            return False
        if self._custom_recipe_count() >= MAX_CUSTOM_RECIPES:
            return False
        rid = rc.get("id")
        if not isinstance(rid, str) or not self.SLUG_RE.match(rid):
            return False
        if rid in self.RECIPES or rid in c["resourceRegistry"]:
            return False
        if any(p["id"] == rid for p in c["pendingRecipes"]):
            return False
        if rid in c["rejectedRecipeIds"]:
            return False
        name = rc.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= 32):
            return False
        inputs = rc.get("inputs")
        if not isinstance(inputs, dict):
            return False
        keys = list(inputs.keys())
        if not (1 <= len(keys) <= 6):
            return False
        for k in keys:
            if k not in c["resourceRegistry"]:
                return False
            v = inputs[k]
            if isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 5):
                return False
        station = rc.get("station")
        if station is not None and station not in VALID_GATHER_ZONES:
            return False
        return True

    def _propose_recipe(self, agent, rc):
        c = self.civilization
        if rc and rc.get("id") in c["rejectedRecipeIds"]:
            return f"{agent['name']}'s recipe {rc.get('id')} was already rejected"
        if not self._validate_recipe(rc):
            return f"{agent['name']} drafted an invalid recipe"
        c["pendingRecipes"].append({
            "id": rc["id"], "name": rc["name"], "inputs": dict(rc["inputs"]),
            "station": rc.get("station"), "color": rc.get("color", "#BCAAA4"),
            "proposedBy": agent["name"],
        })
        c["lastBlueprintActivityFrame"] = self.frameTick
        needs_str = ", ".join(f"{k}x{v}" for k, v in rc["inputs"].items())
        return f"{agent['name']} proposed recipe {rc['name']} (needs {needs_str})"

    def _review_recipe(self, agent, action, target_id, message):
        c = self.civilization
        if agent["role"] != "elder":
            return f"{agent['name']} could not review that recipe"
        idx = next((i for i, p in enumerate(c["pendingRecipes"]) if p["id"] == target_id), -1)
        if idx == -1:
            return f"{agent['name']} could not review that recipe"
        rc = c["pendingRecipes"].pop(idx)
        c["lastBlueprintActivityFrame"] = self.frameTick
        if message:
            agent["message"] = message
            agent["messageTimer"] = 180
        if action == "reject_recipe":
            c["rejectedRecipeIds"].add(rc["id"])
            return f"{agent['name']} rejected the {rc['name']} recipe"
        c["resourceRegistry"][rc["id"]] = {"name": rc["name"], "gatherZone": None,
                                           "color": rc["color"], "crafted": True}
        self.RECIPES[rc["id"]] = {"name": rc["name"], "inputs": dict(rc["inputs"]), "station": rc["station"]}
        c["lastCraftActivityFrame"] = self.frameTick
        return f"{agent['name']} approved the {rc['name']} recipe"

    # --- rules / voting ---
    def _active_agent_count(self):
        return len([a for a in self.agents if not a["incapacitated"]])

    def _vote_quorum(self):
        return (self._active_agent_count() // 2) + 1

    def _validate_rule(self, rule):
        c = self.civilization
        if not RULES_ENABLED or not isinstance(rule, dict):
            return False
        if len(c["pendingRules"]) >= MAX_PENDING_RULES:
            return False
        if len(c["rules"]) >= MAX_ACTIVE_RULES:
            return False
        rid = rule.get("id")
        if not isinstance(rid, str) or not self.SLUG_RE.match(rid):
            return False
        if any(r["id"] == rid for r in c["rules"]):
            return False
        if any(r["id"] == rid for r in c["pendingRules"]):
            return False
        name = rule.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= 32):
            return False
        kind = rule.get("kind") or "custom"
        if kind not in RULE_KINDS:
            return False
        if kind == "resource_tax":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (0 <= v <= 3):
                return False
        return True

    def _tally_and_maybe_enact(self, rule):
        c = self.civilization
        votes = list(rule["votes"].values())
        yes = votes.count("yes")
        no = votes.count("no")
        quorum = self._vote_quorum()
        if yes >= quorum:
            rule["enacted"] = True
            c["pendingRules"] = [r for r in c["pendingRules"] if r["id"] != rule["id"]]
            c["rules"].append(rule)
            c["lastRuleActivityFrame"] = self.frameTick
            self._push_activity(f'Rule "{rule["name"]}" enacted by vote ({yes} yes)')
            self._log_benchmark("rule_enacted", len(c["rules"]), {"id": rule["id"], "yes": yes, "no": no})
            return "enacted"
        if no >= quorum:
            c["pendingRules"] = [r for r in c["pendingRules"] if r["id"] != rule["id"]]
            c["lastRuleActivityFrame"] = self.frameTick
            self._push_activity(f'Rule "{rule["name"]}" rejected by vote ({no} no)')
            return "rejected"
        return "pending"

    def _propose_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot propose rules"
        rule = decision.get("rule")
        if not self._validate_rule(rule):
            return f"{agent['name']} drafted an invalid rule"
        kind = rule.get("kind") or "custom"
        value = float(rule["value"]) if kind == "resource_tax" else rule.get("value")
        entry = {
            "id": rule["id"], "name": rule["name"], "kind": kind, "value": value,
            "description": rule.get("description", ""), "proposedBy": agent["name"],
            "enacted": False, "votes": {agent["name"]: "yes"},
        }
        c["pendingRules"].append(entry)
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("rule_proposal", agent["name"], "everyone",
                                 f"{entry['name']}: {entry['description']}")
        self._tally_and_maybe_enact(entry)
        return f'{agent["name"]} proposed rule "{entry["name"]}"'

    def _vote_on_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot vote"
        rule = next((r for r in c["pendingRules"] if r["id"] == decision.get("target")), None)
        if not rule:
            return f"{agent['name']} found no such pending rule"
        vote = "no" if decision.get("vote") == "no" else "yes"
        rule["votes"][agent["name"]] = vote
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("vote", agent["name"], "everyone", f"{vote} on {rule['name']}")
        outcome = self._tally_and_maybe_enact(rule)
        suffix = f" ({outcome})" if outcome != "pending" else ""
        return f'{agent["name"]} voted {vote} on "{rule["name"]}"{suffix}'

    def _active_resource_tax(self):
        if not RULES_ENABLED:
            return 0
        rule = next((r for r in self.civilization["rules"]
                     if r["kind"] == "resource_tax" and r.get("enacted")), None)
        return (rule.get("value") or 0) if rule else 0

    def _enforce_resource_tax(self, agent, res):
        tax = self._active_resource_tax()
        # Edibles are exempt: nothing ever consumes the stockpile, so taxing
        # food/fish just deletes it from the survival economy.
        if tax <= 0 or res in EDIBLE_RESOURCES:
            return
        c = self.civilization
        c["taxDue"] += tax
        pay = min(tax, agent["resources"].get(res, 0))
        if pay > 0:
            agent["resources"][res] -= pay
            c["stockpile"][res] = c["stockpile"].get(res, 0) + pay
            c["taxPaid"] += pay

    # --- blueprint validation ---
    def _custom_resource_count(self):
        return len([rid for rid in self.civilization["resourceRegistry"]
                    if rid not in BASE_RESOURCES and rid not in CRAFTED_RESOURCES])

    def _custom_project_ids(self):
        return [pid for pid, p in self.civilization["projectRegistry"].items() if p.get("custom")]

    def _validate_blueprint(self, bp):
        c = self.civilization
        if not isinstance(bp, dict):
            return False, "blueprint must be an object"
        return self.d["validate_blueprint"](
            bp,
            list(c["resourceRegistry"].keys()),
            [p["id"] for p in c["pendingBlueprints"]],
            self._custom_project_ids(),
            self._custom_resource_count(),
            list(c["rejectedBlueprintIds"]),
            list(self._known_effect_vectors()),
        )

    # --- relationships / helpers ---
    def _nudge_ally(self, agent, other_name):
        cur = agent["relationships"].get(other_name)
        if cur == "rival":
            agent["relationships"][other_name] = "neutral"
        else:
            agent["relationships"][other_name] = "ally"

    def _most_abundant_resource(self, agent):
        best, best_count = None, 0
        for key in self.civilization["resourceRegistry"]:
            count = agent["resources"].get(key, 0)
            if count > best_count:
                best_count, best = count, key
        return best if best_count > 0 else None

    def _pick_contribution_resource(self, agent, decision, district_id=None):
        district_id = district_id or self._resolve_contribution_district(agent, decision.get("target_district"))
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return None
        target = decision.get("target")
        if target and target in self.civilization["resourceRegistry"]:
            if agent["resources"].get(target, 0) > 0 and p["contributed"].get(target, 0) < p["needs"].get(target, 0):
                return target
        for res in p["needs"]:
            need = p["needs"].get(res, 0)
            have = p["contributed"].get(res, 0)
            if need > have and agent["resources"].get(res, 0) > 0:
                return res
        return None

    # --- memes ---
    def _belief_text(self, bid):
        return MEMES.get(bid, bid)

    def _seed_beliefs(self):
        if not MEMES_ENABLED or not self.agents:
            return
        origin = random.choice(self.agents)
        origin["beliefs"].add(MEME_SEED_ID)
        self._push_activity(f'{origin["name"]} began spreading a rumor: "{self._belief_text(MEME_SEED_ID)}"')
        self._push_communication("rumor", origin["name"], "everyone", self._belief_text(MEME_SEED_ID))
        self._push_memory(origin, f"I believe: {self._belief_text(MEME_SEED_ID)}")

    def _transmit_belief(self, speaker, recipient, prob):
        if not MEMES_ENABLED or not speaker or not speaker["beliefs"]:
            return None
        if not recipient or recipient is speaker or recipient["incapacitated"]:
            return None
        if random.random() > prob:
            return None
        belief = random.choice(list(speaker["beliefs"]))
        if belief in recipient["beliefs"]:
            return None
        recipient["beliefs"].add(belief)
        self._push_activity(f'{recipient["name"]} adopted "{self._belief_text(belief)}" from {speaker["name"]}')
        self._push_communication("belief", speaker["name"], recipient["name"], self._belief_text(belief))
        self._push_memory(recipient, f"Came to believe: {self._belief_text(belief)}")
        return belief

    def _maybe_spread_beliefs(self, agent, recipient_name, message):
        if not MEMES_ENABLED or not recipient_name or recipient_name == "everyone":
            return
        recipient = self._find_agent(recipient_name)
        belief = self._transmit_belief(agent, recipient, MEME_SPREAD_PROB)
        if belief:
            self._deliver_message(agent["name"], recipient_name,
                                  f"(belief shared) {self._belief_text(belief)}", "belief")

    def _maybe_form_commitment(self, agent, recipient_name, message):
        """Consequential conversations (#5.4): talk stops being purely
        advisory -- a request naming a known resource creates a commitment
        on the recipient. One commitment per agent; a new one overwrites
        the old. Honored/cleared in apply_decision's post-action bookkeeping."""
        if not recipient_name or recipient_name == "everyone":
            return
        recipient = self._find_agent(recipient_name)
        if not recipient or recipient is agent:
            return
        text_lower = message.lower()
        matched = next((rid for rid in self.civilization["resourceRegistry"] if rid in text_lower), None)
        if not matched:
            return
        recipient["commitment"] = {"to": agent["name"], "text": message,
                                   "madeAt": self.frameTick, "resource": matched}

    def _spread_beliefs_by_proximity(self):
        if not MEMES_ENABLED:
            return
        for speaker in self.agents:
            if speaker["incapacitated"] or not speaker["beliefs"]:
                continue
            for name in self._get_nearby_agents(speaker):
                recipient = self._find_agent(name)
                self._transmit_belief(speaker, recipient, MEME_PROXIMITY_PROB)

    def _meme_adoption_count(self):
        if not MEMES_ENABLED:
            return 0
        return len([a for a in self.agents if MEME_SEED_ID in a["beliefs"]])

    # --- message bus / inbox ---
    def _deliver_message(self, from_name, to_name, text, kind):
        if not AGENT_MESSAGING or not text:
            return
        broadcast = to_name in ("everyone", "all", None)
        for r in self.agents:
            if r["name"] == from_name:
                continue
            if not broadcast and r["name"] != to_name:
                continue
            r["inbox"].append({"from": from_name, "text": text,
                               "kind": kind or "message", "frame": self.frameTick})
            while len(r["inbox"]) > INBOX_CAP:
                r["inbox"].pop(0)

    def _drain_inbox(self, agent):
        if not AGENT_MESSAGING or not agent["inbox"]:
            return "none"
        msgs = " | ".join(f"{m['from']} ({m['kind']}): {m['text']}" for m in agent["inbox"])
        agent["inbox"] = []
        return msgs

    def _has_unread(self, agent):
        return AGENT_MESSAGING and bool(agent["inbox"])

    # --- emergent roles ---
    def _role_specialty_resource(self, role):
        return self.d["ROLE_PRIMARY_RESOURCE"].get((role or "").lower())

    def _is_flexible_role(self, role):
        return EMERGENT_ROLES and not self._role_specialty_resource(role) and role != "elder"

    def _village_needed_role(self):
        if not EMERGENT_ROLES or not self._active_project_districts():
            return None
        unmet = self._first_unmet_resource_anywhere()
        if not unmet:
            return None
        roles = self.d["RESOURCE_GATHER_ROLES"].get(unmet)
        if not roles:
            return None
        filled = any(a["role"] in roles and not a["incapacitated"] for a in self.agents)
        return None if filled else roles[0]

    def _auto_switch_candidate(self, needed_role):
        cands = [a for a in self.agents
                 if not a["incapacitated"] and a["role"] != needed_role
                 and self._is_flexible_role(a["role"])
                 and a["role"] not in AUTOSWITCH_PROTECTED_ROLES]
        cands.sort(key=lambda a: 0 if self._is_idle(a) else 1)
        return cands[0] if cands else None

    def _maybe_auto_switch_role(self):
        if not EMERGENT_ROLES:
            return
        if self.frameTick - self.civilization["lastRoleSwitchFrame"] < ROLE_SWITCH_COOLDOWN:
            return
        needed_role = self._village_needed_role()
        if not needed_role:
            return
        agent = self._auto_switch_candidate(needed_role)
        if not agent:
            return
        self.civilization["lastRoleSwitchFrame"] = self.frameTick
        agent["goal"] = None
        unmet = self._first_unmet_resource_anywhere()
        self.apply_decision(agent, {
            "action": "switch_role", "new_role": needed_role,
            "reasoning": f"The village has no one gathering {unmet}; "
                         f"retraining to {needed_role} to fill the gap."})

    # --- stalled-contribution backstop ---
    def _maybe_force_contribution(self):
        """Deterministic backstop for the build-progression stall where an
        agent (often off-spec, e.g. a trader holding traded stone) sits on a
        resource an active project needs but the LLM never volunteers
        contribute_resources for them. Mirrors _maybe_auto_switch_role /
        _maybe_advance_rules: fires only after a real stall, so it never
        preempts normal LLM-driven play. Generalized to loop every district
        with an active project (not just one global project) -- same
        stall-gated guarantee, per district."""
        c = self.civilization
        for district_id in self._active_project_districts():
            p = c["districtProjects"][district_id]
            if self.frameTick - c["districtLastContribution"].get(district_id, 0) < STALL_THRESHOLD:
                continue
            # Check every still-needed resource, not just the first: e.g. a
            # build stuck on "stone 0/1, food 0/1" with no stone holders but
            # several food holders must still be able to progress on food.
            unmet_resources = [res for res, need in p["needs"].items()
                                if p["contributed"].get(res, 0) < need]
            holder, unmet = None, None
            for res in unmet_resources:
                # Never strip an agent's food/fish safety margin: builds need
                # edibles too, but force-taking them from the last agents
                # standing turns a build stall into a starvation spiral.
                reserve = EDIBLE_RESERVE if res in EDIBLE_RESOURCES else 0
                cands = [a for a in self.agents
                         if not a["incapacitated"] and a["resources"].get(res, 0) > reserve]
                if cands:
                    unmet = res
                    holder = max(cands, key=lambda a: a["resources"].get(res, 0))
                    break
            if not holder:
                continue
            holder["goal"] = None
            self.apply_decision(holder, {
                "action": "contribute_resources", "target": unmet, "target_district": district_id,
                "reasoning": f"Build has stalled in {district_id}; contributing my {unmet} to it now."})

    # --- idle-district backstop (concurrent builds) ---
    def _maybe_feed_starving(self):
        """Deterministic survival backstop (same tick-gated _maybe_* shape as
        _maybe_force_contribution): a starving agent holding nothing edible
        heads to the nearest edible gather zone and collects, instead of
        waiting passively for the LLM to act on the hunger nudge. Auto-eat in
        _update_survival feeds them on the first collect. Same philosophy as
        rushToHeal: survival is too important to leave to prompt nudges; the
        "you are hungry" NOTE stays for coherence only. Sage-emergency
        responders are exempt (the elder's life outranks their own hunger)."""
        if not SURVIVAL_ENABLED:
            return
        em = self._sage_emergency()
        responders = self._sage_responders(em) if em else set()
        for agent in self.agents:
            if agent["incapacitated"] or agent["hunger"] > STARVING_HUNGER:
                continue
            if agent["name"] in responders or self._first_edible(agent):
                continue
            # Nearest edible source: food@farm vs fish@beach, by district distance.
            best = None  # (distance, resource_id, district_id)
            for rid in EDIBLE_RESOURCES:
                zone = self._gather_zone_for_resource(rid)
                if not zone:
                    continue
                for did in self._districts_of_kind(zone):
                    d = self._distance_to_district(agent, did)
                    if best is None or d < best[0]:
                        best = (d, rid, did)
            if best is None:
                continue
            _, rid, district_id = best
            agent["goal"] = None
            if agent["currentZone"] == self._gather_zone_for_resource(rid):
                if self._resolve_contribution_district(agent):
                    # In the right zone: collect now and install a gather goal
                    # so _step_goal keeps at it without LLM round-trips.
                    decision = {"action": "collect_resource", "target": rid,
                                "target_district": None, "message": None,
                                "reasoning": "Starving - gathering food to survive."}
                    self.apply_decision(agent, decision)
                    agent["goal"] = self._goal_for_decision(decision)
                elif agent["resources"].get(rid, 0) < self._carry_cap(agent):
                    # No active project anywhere: a full apply_decision would
                    # detour into _start_project_for, which a hunger backstop
                    # has no business doing. Collect the edible directly.
                    agent["resources"][rid] = agent["resources"].get(rid, 0) + 1
                    self.civilization["collectAttempts"] += 1
                    self.civilization["collectSuccesses"] += 1
                    self._push_activity(f"{agent['name']} collected {rid}")
            else:
                # Wrong zone: walk there via the road network. The gate
                # re-fires every RULES_TICK_FRAMES until they arrive, then the
                # branch above takes over.
                self._set_agent_target(agent, district_id)
                self._push_activity(
                    f"{agent['name']} is starving and heads to {district_id} for {rid}")

    def _maybe_start_idle_district_project(self):
        """With multiple buildable districts, nothing today encourages the
        LLM to spread work across them -- it's plausible the model fixates on
        one district indefinitely.         Deterministically start a project in a
        buildable, idle district that has an agent standing in it, mirroring
        _maybe_advance_rules's shape (cooldown-gated, calls into normal state
        mutation). Routes through apply_decision -> _start_project_for, so
        the invention gate (#5.1) applies here automatically -- when
        invention is required this becomes a no-op refusal rather than a
        seed-type build, exactly like an LLM-issued start_project would."""
        c = self.civilization
        if len(self._active_project_districts()) >= MAX_CONCURRENT_PROJECTS:
            return
        if self.frameTick - c.get("lastIdleDistrictCheckFrame", 0) < STALL_THRESHOLD:
            return
        c["lastIdleDistrictCheckFrame"] = self.frameTick
        for district_id in self._buildable_district_ids():
            if c["districtProjects"].get(district_id):
                continue
            if self._district_structure_count(district_id) >= c["districts"][district_id]["build_grid"]["cap"]:
                continue
            occupant = next((a for a in self.agents
                             if not a["incapacitated"] and a.get("currentDistrict") == district_id), None)
            if not occupant:
                continue
            occupant["goal"] = None
            self.apply_decision(occupant, {
                "action": "start_project", "target_district": district_id,
                "reasoning": f"No build is underway in {district_id} yet; starting one so work spreads out."})
            return

    def _maybe_build_funded_project(self):
        """Deterministic backstop, same tick-gated _maybe_* shape as
        _maybe_start_idle_district_project: a fully funded project that has
        sat unbuilt past STALL_THRESHOLD gets built by the builder (or any
        able agent). Observed sessions (e.g. 2026-07-02T19-50-21) left
        100%-funded projects idle because nothing ever pushed the LLM toward
        build_structure. Routes through apply_decision so the normal
        build path (spot finding, level check) applies."""
        c = self.civilization
        if self.frameTick - c.get("lastFundedBuildCheckFrame", 0) < STALL_THRESHOLD:
            return
        c["lastFundedBuildCheckFrame"] = self.frameTick
        for district_id in self._active_project_districts():
            if not self._is_project_complete(district_id):
                continue
            # Freshly funded: give the LLM a turn to build it itself first.
            if self.frameTick - c["districtLastContribution"].get(district_id, 0) < STALL_THRESHOLD:
                continue
            builder = next((a for a in self.agents if not a["incapacitated"] and a["role"] == "builder"), None) \
                or next((a for a in self.agents if not a["incapacitated"]), None)
            if not builder:
                return
            builder["goal"] = None
            self.apply_decision(builder, {
                "action": "build_structure", "target_district": district_id,
                "reasoning": f"The {district_id} project is fully funded; raising the structure."})
            return

    def _project_contribution_stall_frames(self, district_id):
        c = self.civilization
        if not c["districtProjects"].get(district_id):
            return 0
        last = c["districtLastContribution"].get(district_id, self.frameTick)
        return self.frameTick - last

    def _abandon_threshold_for(self, district_id):
        project = self.civilization["districtProjects"].get(district_id)
        if not project:
            return PROJECT_ABANDON_THRESHOLD
        registry = self.civilization.get("resourceRegistry") or {}
        needs = project.get("needs") or {}
        if any(registry.get(res, {}).get("crafted") for res in needs):
            return PROJECT_ABANDON_THRESHOLD_CRAFTED
        return PROJECT_ABANDON_THRESHOLD

    def _project_squatting_past_abandon_threshold(self, district_id):
        return self._project_contribution_stall_frames(district_id) >= \
            self._abandon_threshold_for(district_id)

    def _project_type_active(self, type_):
        return any(p and p.get("type") == type_
                   for p in self.civilization["districtProjects"].values())

    def _maybe_abandon_stalled_projects(self):
        """Cancel district projects with no contribution progress past the
        per-project abandon threshold; refund materials and free the slot."""
        c = self.civilization
        for district_id in list(self._active_project_districts()):
            if not self._project_squatting_past_abandon_threshold(district_id):
                continue
            project = c["districtProjects"][district_id]
            name = project.get("name", project.get("type", "project"))
            ptype = project.get("type")
            for res, amt in (project.get("contributed") or {}).items():
                if amt > 0:
                    c["stockpile"][res] = c["stockpile"].get(res, 0) + amt
            c["districtProjects"][district_id] = None
            if ptype:
                streak = c.setdefault("projectAbandonStreak", {})
                streak[ptype] = streak.get(ptype, 0) + 1
                if streak[ptype] >= PROJECT_DEFER_ABANDON_STREAK:
                    c.setdefault("deferredProjectTypes", {})[ptype] = \
                        self.frameTick + PROJECT_DEFER_COOLDOWN
                    self._push_activity(
                        f"The village defers further {name} projects — "
                        f"{streak[ptype]} abandonments in a row")
            reason = (f"the {name} project in {district_id} was abandoned — "
                      f"materials reclaimed")
            c["lastProjectAbandonment"] = {
                "reason": reason, "frame": self.frameTick, "district": district_id,
            }
            self._touch_kind_activity(c["districts"][district_id]["kind"])
            self._push_activity(reason[0].upper() + reason[1:])

    def _maybe_start_approved_custom(self):
        """When an approved custom blueprint sits unbuilt too long, the elder
        deterministically starts a project for it (Phase A audit carry-over).
        On failure, try founding a district of the needed kind; otherwise log
        once and back off instead of retrying every STALL_THRESHOLD."""
        c = self.civilization
        if self.frameTick < c.get("approvedCustomBackoffUntil", 0):
            return
        if len(self._active_project_districts()) >= MAX_CONCURRENT_PROJECTS:
            return
        if self.frameTick - c.get("lastApprovedCustomCheckFrame", 0) < STALL_THRESHOLD:
            return
        stalled = self._stalled_approved_customs()
        if not stalled:
            return
        pid, name, _ = stalled[0]
        if self._is_project_type_deferred(pid)[0]:
            return
        if self._project_type_active(pid):
            return
        c["lastApprovedCustomCheckFrame"] = self.frameTick
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        elder["goal"] = None
        decision = {
            "action": "start_project", "target": pid,
            "reasoning": f"The village approved {name} but never started building it."}

        def _try_start():
            self.apply_decision(elder, decision)
            return self._project_type_active(pid)

        if _try_start():
            c["approvedCustomBackstopFailures"] = 0
            c["approvedCustomEscalationLogged"] = False
            c["approvedCustomBackoffUntil"] = 0
            self._push_activity(f"Elder {elder['name']} directs the village to build the approved {name}")
            return

        kind = PROJECT_KIND.get(pid, "village")
        tmpl = DISTRICT_KIND_TEMPLATES.get(kind)
        if tmpl:
            plot = self._claim_frontier_plot()
            if plot:
                self._found_district(kind, tmpl, plot)
                if _try_start():
                    c["approvedCustomBackstopFailures"] = 0
                    c["approvedCustomEscalationLogged"] = False
                    c["approvedCustomBackoffUntil"] = 0
                    self._push_activity(
                        f"Elder {elder['name']} opens new {kind} land and starts the approved {name}")
                    return

        c["approvedCustomBackstopFailures"] = c.get("approvedCustomBackstopFailures", 0) + 1
        if not c.get("approvedCustomEscalationLogged"):
            self._push_activity(
                f"Cannot start approved {name} — all {kind} districts are blocked; "
                f"backing off until land opens")
            c["approvedCustomEscalationLogged"] = True
        c["approvedCustomBackoffUntil"] = self.frameTick + APPROVED_CUSTOM_BACKOFF_FRAMES

    # --- newcomer backstop (structure effects: houses grow the population) ---
    def _maybe_welcome_newcomer(self):
        """Tick-gated like the other _maybe_* backstops. When built housing
        raises the population cap above the current roster, the next unused
        AGENT_DEFS entry moves in (at most one per gate interval). Newcomers
        persist via state.json like any other agent."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return
        if len(self.agents) >= self._population_cap():
            return
        unused = next((d for d in AGENT_DEFS if d["name"] not in self.agent_names), None)
        if not unused:
            return
        newcomer = self._make_agents([unused])[0]
        self.agents.append(newcomer)
        self.agent_names.add(unused["name"])
        # Deliberately do NOT touch self.roster_size: it means "cold-start
        # roster" (what reset() re-seeds from). Letting spawns inflate it made
        # a later Reset cold-start at 12 agents with basePopulation=12,
        # permanently disabling this very mechanic in the new world.
        self._push_activity(f"{unused['name']} the {unused['role']} moved to the village -- "
                            f"the new houses drew a newcomer!")

    # --- blueprint retirement (frees approval slots so invention never deadlocks) ---
    def _maybe_retire_blueprint(self):
        """Once the approved-custom count reaches MAX_APPROVED_CUSTOM,
        validate_blueprint rejects every new proposal -- while
        _invention_required() keeps demanding one. Retire the oldest *built*
        custom blueprint (drop its registry entry; standing structures keep
        their own name/visualStyle so nothing on the map changes) to keep a
        slot open for the next invention."""
        c = self.civilization
        while len(self._custom_project_ids()) >= MAX_APPROVED_CUSTOM:
            retired = next((pid for pid in self._custom_project_ids()
                            if pid in c["builtTypes"]), None)
            if not retired:
                return  # nothing built to retire; _invention_required stays False
            name = c["projectRegistry"][retired].get("name", retired)
            del c["projectRegistry"][retired]
            self._push_activity(f"The {name} design has been archived -- its plans made room for new inventions.")

    # --- blueprint amnesty (C3: rejected ids expire instead of blacklisting forever) ---
    def _maybe_amnesty_rejected_blueprints(self):
        """A rejected blueprint id used to stay in rejectedBlueprintIds forever
        (permanent blacklist -- copilot audit #16). Grant amnesty after
        BLUEPRINT_AMNESTY_FRAMES so a once-rejected idea can legitimately be
        re-proposed later, mirroring _maybe_retire_blueprint's slot-freeing
        pattern. Ids restored from a pre-amnesty save have no rejection frame;
        their clock starts at the first gate check after restore."""
        c = self.civilization
        if not c["rejectedBlueprintIds"]:
            return
        frames = c.setdefault("rejectedBlueprintFrames", {})
        for bid in list(c["rejectedBlueprintIds"]):
            rejected_at = frames.get(bid)
            if rejected_at is None:
                frames[bid] = self.frameTick
                continue
            if self.frameTick - rejected_at >= BLUEPRINT_AMNESTY_FRAMES:
                c["rejectedBlueprintIds"].discard(bid)
                frames.pop(bid, None)
                self._push_activity(
                    f"The old rejection of the '{bid}' blueprint has been forgotten -- "
                    f"it may be proposed again")

    # --- custom-resource retirement (C3: the resource cap gets an expiry too) ---
    def _custom_resource_referenced(self, rid):
        """True while anything still uses the custom resource: a structure
        function that produces/boosts it, a project (registry or active) that
        needs it, a recipe that inputs or outputs it (pending included), or a
        remaining balance in the stockpile / any agent's inventory."""
        c = self.civilization
        if c["stockpile"].get(rid, 0) > 0:
            return True
        if any(a["resources"].get(rid, 0) > 0 for a in self.agents):
            return True
        if rid in self.RECIPES or any(rid in r["inputs"] for r in self.RECIPES.values()):
            return True
        if any(p["id"] == rid or rid in p.get("inputs", {}) for p in c["pendingRecipes"]):
            return True
        for pid, tmpl in c["projectRegistry"].items():
            if rid in (tmpl.get("needs") or {}):
                return True
            fn = self._get_structure_function(pid)
            if any(prod.get("resource") == rid for prod in fn.get("produces") or []):
                return True
            if any(rid in (boost.get("resources") or []) for boost in fn.get("boosts") or []):
                return True
        for bp in c["pendingBlueprints"]:
            if rid in (bp.get("needs") or {}):
                return True
            fn = bp.get("function") or {}
            if any(prod.get("resource") == rid for prod in fn.get("produces") or []):
                return True
            if any(rid in (boost.get("resources") or []) for boost in fn.get("boosts") or []):
                return True
        for p in c["districtProjects"].values():
            if p and rid in (p.get("needs") or {}):
                return True
        return False

    def _maybe_retire_custom_resource(self):
        """Once MAX_CUSTOM_RESOURCES is hit, validate_blueprint rejects every
        proposal bundling a new resource ("too many custom resources" x137 in
        the 2026-07-05 9h soak) with no expiry analogue -- same shape as the
        MAX_APPROVED_CUSTOM deadlock. Retire the oldest custom resource that
        nothing references anymore (no producer, recipe, project need, or
        remaining stock); if every one is still in use the cap is legitimately
        hard and the rejection reason (server.py validate_blueprint) says so."""
        c = self.civilization
        while self._custom_resource_count() >= MAX_CUSTOM_RESOURCES:
            added = c.setdefault("customResourceAddedFrame", {})
            custom = [rid for rid in c["resourceRegistry"]
                      if rid not in BASE_RESOURCES and rid not in CRAFTED_RESOURCES]
            unreferenced = [rid for rid in custom if not self._custom_resource_referenced(rid)]
            if not unreferenced:
                return  # all referenced: the cap holds until something falls out of use
            retired = min(unreferenced, key=lambda rid: added.get(rid, 0))
            name = c["resourceRegistry"][retired].get("name", retired)
            del c["resourceRegistry"][retired]
            added.pop(retired, None)
            # Scrub dead stock entries so district stocks don't track a
            # resource that can no longer be gathered or referenced.
            for stocks in (c.get("districtStocks") or {}).values():
                stocks.pop(retired, None)
            self._push_activity(
                f"The unused resource {name} ({retired}) has faded from village life -- "
                f"its slot is free for new inventions")

    # --- invention-demand backstop (#5.2) ---
    def _maybe_invention_backstop(self):
        """Deterministic elder backstop, same tick-gated _maybe_* shape as
        _maybe_advance_rules/_maybe_start_idle_district_project: once
        _invention_required() has held true for INVENTION_BACKSTOP_STREAK
        consecutive elder turns (the streak is tracked in
        civilization["inventionRequiredStreak"], incremented in
        _schedule_think whenever the elder is dispatched to think, reset on
        every non-required turn or successful propose_blueprint) and no
        blueprint is currently pending, direct the most-idle villager to
        invent one -- and flag that villager's next think as an
        invention-only turn (slim, proposal-focused prompt; see
        _build_think_payload / server build_invention_prompt). After
        INVENTION_ELDER_TAKEOVER delegations with no valid proposal landing
        (counted in civilization["inventionBackstopFires"], reset on every
        accepted proposal), or when no villager is free to task, the elder
        takes the invention-only turn himself. The blueprint's actual content
        still comes from the LLM either way."""
        c = self.civilization
        if c.get("inventionRequiredStreak", 0) < INVENTION_BACKSTOP_STREAK:
            return
        if c["pendingBlueprints"]:
            return
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        target = next((a for a in self._idle_agents_for_elder() if a["name"] != elder["name"]), None)
        if c.get("inventionBackstopFires", 0) >= INVENTION_ELDER_TAKEOVER or not target:
            c["inventionRequiredStreak"] = 0
            c["inventionBackstopFires"] = 0
            elder["inventionTurn"] = True
            self._push_activity(f"Elder {elder['name']} will draft the new blueprint himself.")
            return
        c["inventionRequiredStreak"] = 0
        c["inventionBackstopFires"] = c.get("inventionBackstopFires", 0) + 1
        target["inventionTurn"] = True
        self.apply_decision(elder, {
            "action": "assign_task", "target": target["name"],
            "message": "propose a new structure blueprint -- the village needs a new invention!",
            "reasoning": "All known structures are built and no invention is pending; "
                         "directing the village to invent something new."})
        self._push_activity(f"Elder {elder['name']} demands invention: every known structure is already built.")

    # --- stuck-project relocation backstop ---
    def _maybe_relocate_stuck_project(self):
        """A project active in a district whose build grid has filled up can
        never complete: build_structure fails with "no room left to build"
        forever, the project squats on one of the MAX_CONCURRENT_PROJECTS
        slots, and everything contributed to it is lost. Move such a project
        (contributions included) to a same-kind district that has a free spot
        and no active build. If none exists, do nothing this gate --
        _kind_at_capacity will be true, _maybe_found_district opens new land,
        and a later gate completes the move."""
        c = self.civilization
        for district_id in self._active_project_districts():
            if self._find_structure_spot(district_id) is not None:
                continue
            project = c["districtProjects"][district_id]
            kind = c["districts"][district_id]["kind"]
            dest = next((did for did in self._buildable_district_ids()
                         if did != district_id
                         and c["districts"][did]["kind"] == kind
                         and not c["districtProjects"].get(did)
                         and self._find_structure_spot(did) is not None), None)
            if not dest:
                continue
            project["districtId"] = dest
            c["districtProjects"][dest] = project
            c["districtProjects"][district_id] = None
            c["districtLastContribution"][dest] = self.frameTick
            self._touch_kind_activity(kind)
            self._push_activity(
                f"The {project['name']} build moves to {dest} — {district_id} has no land left")

    # --- district founding (the open-world mechanism) ---
    def _district_counts_as_full(self, district_id):
        c = self.civilization
        if c["districtProjects"].get(district_id) and \
                self._project_squatting_past_abandon_threshold(district_id):
            return True
        d = c["districts"][district_id]
        if d.get("build_grid"):
            return self._district_structure_count(district_id) >= d["build_grid"]["cap"]
        return False

    def _kind_at_capacity(self, kind):
        ids = [did for did, d in self.civilization["districts"].items()
               if d["kind"] == kind and d.get("build_grid")]
        if not ids:
            return False
        return all(self._district_counts_as_full(did) for did in ids)

    def _claim_frontier_plot(self):
        for plot in self.civilization["frontierPlots"]:
            if not plot["claimed"]:
                return plot
        return None

    def _found_district(self, kind, tmpl, plot):
        c = self.civilization
        n = sum(1 for d in c["districts"].values() if d["kind"] == kind) + 1
        did = f"{kind}_{n}"
        while did in c["districts"]:
            n += 1
            did = f"{kind}_{n}"
        grid_t = tmpl["grid"]
        bounds = {"x1": plot["x1"], "y1": plot["y1"], "x2": plot["x2"], "y2": plot["y2"]}
        entry_node = f"{did}_gate"
        cx, cy = (bounds["x1"] + bounds["x2"]) / 2, (bounds["y1"] + bounds["y2"]) / 2
        nearest = self._nearest_road_node(cx, cy)
        c["districts"][did] = {
            "kind": kind, "tile": tmpl["tile"], "label": f"{kind.upper()} {n}",
            "bounds": bounds,
            "build_grid": {"x0": bounds["x1"] + 20, "y0": bounds["y1"] + 40,
                           "cols": grid_t["cols"], "dx": grid_t["dx"], "dy": grid_t["dy"], "cap": grid_t["cap"]},
            "entryNode": entry_node,
        }
        c["districtProjects"][did] = None
        c["districtLastContribution"][did] = self.frameTick
        plot["claimed"] = True
        plot["claimedBy"] = did
        c["roadNodes"][entry_node] = {"x": cx, "y": cy}
        if nearest:
            c["roadEdges"].append([entry_node, nearest])
        c["lastDistrictFoundFrame"] = self.frameTick
        self._recompute_road_paths()
        _validate_districts(c["districts"])
        _validate_road_graph(c["roadNodes"], c["roadEdges"])
        self._push_activity(f"The village claims new land in the frontier for a {kind} district ({did}).")
        self._log_benchmark("district_founded", len(c["districts"]), {"id": did, "kind": kind})
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            new_stocks = self._init_district_stocks({did: c["districts"][did]}, c["resourceRegistry"])
            c["districtStocks"].update(new_stocks)

    def _maybe_found_district(self):
        """Deterministic, tick-gated backstop (same shape as
        _maybe_advance_rules/_maybe_force_contribution) that founds a new
        district of a buildable kind once every existing district of that
        kind is at/near capacity AND that kind's contribution activity keeps
        stalling -- i.e. the civilization has run out of room to build more
        of something and is actively trying to. This is the mechanism that
        makes the world genuinely open rather than just bigger-but-finite."""
        c = self.civilization
        if len(c["districts"]) >= MAX_TOTAL_DISTRICTS:
            return
        if self.frameTick - c.get("lastDistrictFoundFrame", 0) < DISTRICT_FOUND_STALL_THRESHOLD:
            return
        for kind, tmpl in DISTRICT_KIND_TEMPLATES.items():
            if not self._kind_at_capacity(kind):
                continue
            if self.frameTick - c["kindLastActivityFrame"].get(kind, 0) < DISTRICT_FOUND_STALL_THRESHOLD:
                continue
            plot = self._claim_frontier_plot()
            if not plot:
                # Treat "no unclaimed frontier plot" as a silent no-op (log
                # once) rather than an error -- an extremely distant edge
                # case given the frontier is sized generously relative to
                # MAX_TOTAL_DISTRICTS, but cheap to guard against.
                if not c.get("frontierExhaustedLogged"):
                    self._push_activity("The frontier has no more unclaimed land left to expand into.")
                    c["frontierExhaustedLogged"] = True
                continue
            self._found_district(kind, tmpl, plot)
            return  # one founding per gate check keeps this easy to reason about

    # --- rules backstop ---
    def _maybe_advance_rules(self):
        if not RULES_ENABLED:
            return
        c = self.civilization
        pending = c["pendingRules"][0] if c["pendingRules"] else None
        if pending:
            eligible = [a for a in self.agents
                        if not a["incapacitated"] and a["role"] != "elder"
                        and a["name"] not in pending["votes"]]
            voter = next((a for a in eligible if self._is_idle(a)), None) or (eligible[0] if eligible else None)
            if voter:
                vote = "no" if (pending["kind"] == "resource_tax" and (pending.get("value") or 0) > 2) else "yes"
                self.apply_decision(voter, {"action": "vote_rule", "target": pending["id"],
                                            "vote": vote,
                                            "reasoning": f'Casting my vote on the proposed rule "{pending["name"]}".'})
            return
        if self._active_resource_tax() > 0:
            return
        if self.frameTick - c["lastRuleActivityFrame"] < RULE_PROPOSE_COOLDOWN:
            return
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        self.apply_decision(elder, {
            "action": "propose_rule",
            "rule": {"id": "resource_tax", "name": "Resource Tax", "kind": "resource_tax",
                     "value": 1, "description": "Contributors add 1 of the same resource to a shared stockpile."},
            "reasoning": "Proposing a small resource tax to build a shared village stockpile."})

    # --- benchmarks ---
    def _role_entropy(self):
        counts = {}
        for a in self.agents:
            counts[a["role"]] = counts.get(a["role"], 0) + 1
        n = len(self.agents) or 1
        h = 0.0
        for k in counts:
            p = counts[k] / n
            if p > 0:
                h -= p * math.log2(p)
        return h

    def _rule_adherence(self):
        if self.civilization["taxDue"] <= 0:
            return None
        return self.civilization["taxPaid"] / self.civilization["taxDue"]

    def _sample_benchmarks(self):
        if not BENCHMARKS_ENABLED:
            return
        entropy = self._role_entropy()
        adherence = self._rule_adherence()
        adoption = self._meme_adoption_count()
        adoption_rate = adoption / len(self.agents) if self.agents else 0
        self.lastBenchmarks = {
            "entropy": entropy, "adherence": adherence, "adoption": adoption,
            "adoptionRate": adoption_rate, "moduleTotal": 0,
            "rules": len(self.civilization["rules"]),
            "structures": len(self.civilization["structures"]),
            "level": self.civilization["level"], "memory": self.lastMemorySize,
            "effectThroughput": self._effect_period_fired,
            "ecologyScarcity": self._ecology_scarcity_index(),
        }
        role_counts = {}
        for a in self.agents:
            role_counts[a["role"]] = role_counts.get(a["role"], 0) + 1
        self._log_benchmark("specialization_entropy", round(entropy, 2), {"counts": role_counts})
        if adherence is not None:
            self._log_benchmark("rule_adherence", round(adherence, 2),
                                {"paid": self.civilization["taxPaid"], "due": self.civilization["taxDue"]})
        if MEMES_ENABLED:
            self._log_benchmark("meme_adoption", adoption,
                                {"rate": round(adoption_rate, 2), "seed": MEME_SEED_ID, "of": len(self.agents)})
        self._log_benchmark("memory_store_size", self.lastMemorySize)
        if STRUCTURE_EFFECTS_ENABLED:
            fired = self._effect_period_fired
            self._log_benchmark("structure_effect_throughput", fired,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._last_effect_benchmark_fired = fired
            self._effect_period_fired = 0
        if ECOLOGY_ENABLED:
            scarcity = self._ecology_scarcity_index()
            if scarcity is not None:
                self._log_benchmark("ecology_scarcity_index", scarcity,
                                    {"period_ticks": BENCHMARK_TICK_FRAMES})
        if GOODS_ENABLED:
            c = self.civilization
            caps = {rid: self._storage_capacity(rid) for rid in EDIBLE_RESOURCES}
            stored = {rid: c["stockpile"].get(rid, 0)
                      + sum(a["resources"].get(rid, 0) for a in self.agents)
                      for rid in EDIBLE_RESOURCES}
            total_cap = sum(caps.values()) or 1
            self._log_benchmark("storage_utilization",
                                round(sum(stored.values()) / total_cap, 3),
                                {"stored": stored, "capacity": caps,
                                 "spoiled_period": self._spoiled_period,
                                 "season": self._current_season()})
            self._spoiled_period = 0
            conds = [s.get("condition", 100) for s in c["structures"]]
            if conds:
                self._log_benchmark(
                    "structure_condition", round(sum(conds) / len(conds), 1),
                    {"ruins": sum(1 for s in c["structures"] if s.get("isRuin")),
                     "disrepair": sum(1 for v in conds
                                      if 0 < v < STRUCTURE_DISREPAIR_THRESHOLD),
                     "structures": len(conds)})

    # --- memory maintenance (round-robin summarizer + periodic cleaner) ---
    def _run_memory_maintenance(self):
        if not MEMORY_ENABLED or not self.agents:
            return
        agent = self.agents[self._memory_maint_index % len(self.agents)]
        self._memory_maint_index += 1
        try:
            ms = self.d["memory_store"]
            recents = [e for e in ms.recent(agent=agent["name"], limit=12) if e["kind"] != "summary"]
            if len(recents) >= 4:
                joined = "; ".join(e["text"] for e in recents)
                summary = self.d["lm_complete"](
                    "You compress an agent's recent memories into ONE concise "
                    "first-person sentence capturing what matters for their future "
                    "decisions. Output only the sentence, no preamble.",
                    f"Agent {agent['name']}'s recent memories: {joined}\nSummary:",
                    max_tokens=80, temperature=0.4)
                if summary:
                    summary = summary.strip().strip('"').strip()[:200]
                if summary:
                    ms.store(agent["name"], summary, salience=0.9, kind="summary",
                             frame_tick=self.frameTick, tier="longTerm")
                    agent["memory"]["longTerm"].append(summary)
                    while len(agent["memory"]["longTerm"]) > LONG_MEM_CAP:
                        agent["memory"]["longTerm"].pop(0)
                    self._push_activity(f"{agent['name']} reflected: {summary}")
            self.lastMemorySize = ms.size()
        except Exception:
            pass
        if self._memory_maint_index % 4 == 0:
            try:
                self.d["memory_store"].clean()
                self.lastMemorySize = self.d["memory_store"].size()
            except Exception:
                pass
            # MemoryStore.clean() only scrubs the vector store; each agent's
            # live memory.longTerm list is separate engine state and can hold
            # the same leaked chain-of-thought scaffold (see is_scaffold_text)
            # if it was written before validation existed. Without this, a
            # running session keeps poisoned entries indefinitely -- they only
            # roll off after LONG_MEM_CAP new (now-validated) summaries arrive.
            try:
                is_scaffold = self.d["is_scaffold_text"]
                for a in self.agents:
                    long_term = a.get("memory", {}).get("longTerm")
                    if long_term:
                        a["memory"]["longTerm"] = [
                            t for t in long_term if not is_scaffold(t)
                        ]
            except Exception:
                pass

    # --- the 27-case world-mutation switch (ported applyDecision) ---
    def apply_decision(self, agent, decision):
        action = decision.get("action") or "rest"
        summary = f"{agent['name']} rested"
        resource_acted = None  # set by collect_resource/contribute_resources/trade_resource
        # below; used only to honor a pending commitment (#5.4) after the fact.
        c = self.civilization

        is_talk = action == "talk_to_nearby"
        if is_talk and decision.get("message"):
            agent["consecutiveTalks"] += 1
        elif action != "rest":
            agent["consecutiveTalks"] = 0

        is_move_only = action.startswith("move_to_") or action == "rest"
        agent["consecutiveIdleMoves"] = (agent.get("consecutiveIdleMoves", 0) + 1) if is_move_only else 0

        if action == "move_to_district":
            # Models often put the district id in target_district instead of
            # target; accept either so the move actually happens.
            target = decision.get("target") or decision.get("target_district")
            self._set_agent_target(agent, target)
            district_id = self._resolve_target_district(target, agent) or target or "somewhere"
            summary = f"{agent['name']} heads to {district_id}"

        elif action == "move_to_agent":
            target = decision.get("target")
            if target and target in self.agent_names:
                self._set_agent_target_to_agent(agent, target)
                summary = f"{agent['name']} moves toward {target}"
            else:
                nearest = self._find_nearest_agent(agent)
                if nearest:
                    self._set_agent_target_to_agent(agent, nearest["name"])
                    summary = f"{agent['name']} moves toward {nearest['name']}"

        elif action.startswith("move_to_"):
            # Back-compat hedge: an older move_to_<kind> action (e.g.
            # "move_to_farm", from a stale client/model) still resolves via
            # the kind-name hedge in _resolve_target_district instead of
            # failing outright.
            kind = action[len("move_to_"):]
            self._set_agent_target(agent, kind)
            summary = f"{agent['name']} heads to the {kind}"

        elif action == "collect_resource":
            c["collectAttempts"] += 1
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            else:
                zone = agent["currentZone"]
                unmet = self._first_unmet_project_resource(district_id)
                target = decision.get("target")
                target_def = c["resourceRegistry"].get(target) if target else None
                zone_resources = self._get_zone_resources(zone)
                candidates = []
                if target_def and target_def.get("gatherZone") == zone:
                    candidates.append(target)
                if unmet and self._gather_zone_for_resource(unmet) == zone:
                    candidates.append(unmet)
                candidates.extend(zone_resources)
                if not zone_resources and zone == "beach" and agent["role"] == "fisher":
                    candidates.append("food")
                resource = next((r for r in candidates
                                 if agent["resources"].get(r, 0) < self._carry_cap(agent)), None)
                if resource:
                    summary = self._perform_gather(agent, resource)
                    resource_acted = resource if "collected" in summary else None
                else:
                    contrib_res = self._pick_contribution_resource(
                        agent, {"target": unmet, "target_district": district_id}, district_id)
                    contributed = self._try_contribute_resource(agent, contrib_res, district_id)
                    if contributed:
                        summary = contributed
                        resource_acted = contrib_res
                    elif unmet and self._gather_zone_for_resource(unmet):
                        gz = self._gather_zone_for_resource(unmet)
                        if agent["currentZone"] != gz:
                            self._set_agent_target(agent, gz)
                            summary = f"{agent['name']} heads to gather {unmet}"
                        else:
                            summary = f"{agent['name']} found nothing to collect"
                    else:
                        summary = f"{agent['name']} found nothing to collect"

        elif action == "talk_to_nearby":
            recipient = self._resolve_talk_target(agent, decision)
            self._auto_move_toward_target(agent, recipient if recipient != "everyone" else decision.get("target"))
            if decision.get("message"):
                agent["message"] = decision["message"]
                agent["messageTimer"] = 180
                agent["lastSpokeFrame"] = self.frameTick
                self._push_conversation(agent["name"], recipient, decision["message"])
                self._deliver_message(agent["name"], recipient, decision["message"], "speech")
                self._maybe_spread_beliefs(agent, recipient, decision["message"])
                self._maybe_form_commitment(agent, recipient, decision["message"])
                summary = f"{agent['name']} talked to {recipient}"
            else:
                self._push_communication("talk_attempt", agent["name"], recipient, None, "no_message")
                summary = f"{agent['name']} looked for someone to talk to"

        elif action == "trade_resource":
            target = self._find_agent(decision.get("target"))
            if target:
                self._auto_move_toward_target(agent, target["name"])
            nearby = target and self._distance_to(agent, target) <= 80
            give = self._most_abundant_resource(agent)
            if nearby and give:
                agent["resources"][give] -= 1
                target["resources"][give] = target["resources"].get(give, 0) + 1
                self._nudge_ally(agent, target["name"])
                self._nudge_ally(target, agent["name"])
                self._push_memory(target, f"Received {give} from {agent['name']}")
                summary = f"{agent['name']} traded {give} to {target['name']}"
                resource_acted = give
            elif target:
                summary = f"{agent['name']} moves to trade with {target['name']}"
            else:
                summary = f"{agent['name']} rested"

        elif action == "craft_item":
            summary = self._craft_item(agent, decision.get("target"))

        elif action == "propose_recipe":
            summary = self._propose_recipe(agent, decision.get("recipe"))

        elif action in ("approve_recipe", "reject_recipe"):
            summary = self._review_recipe(agent, action, decision.get("target"), decision.get("message"))

        elif action == "start_project":
            summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                or f"{agent['name']} could not start a project"

        elif action == "start_terraform":
            if ECOLOGY_ENABLED:
                summary = self._start_terraform_for(agent, decision.get("target"),
                                                    decision.get("target_district"))
                if summary:
                    agent["lastTerraformRejection"] = None
                else:
                    agent["lastTerraformRejection"] = {
                        "reason": "no free district of the right kind for that terraform",
                        "frame": self.frameTick,
                    }
                    summary = f"{agent['name']} could not start that terraform project"
            else:
                summary = f"{agent['name']} cannot terraform — ecology is disabled"

        elif action == "repair_structure":
            if GOODS_ENABLED:
                summary = self._repair_structure(agent, decision.get("target"))
            else:
                summary = f"{agent['name']} cannot repair — structure decay is disabled"

        elif action == "contribute_resources":
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            else:
                res = self._pick_contribution_resource(agent, decision, district_id)
                contributed = self._try_contribute_resource(agent, res, district_id)
                if contributed:
                    summary = contributed
                    resource_acted = res
                elif self._is_project_complete(district_id):
                    summary = self._build_active_structure(agent, district_id)
                else:
                    unmet = self._first_unmet_project_resource(district_id)
                    gz = self._gather_zone_for_resource(unmet) if unmet else None
                    if unmet and gz and agent["currentZone"] != gz:
                        self._set_agent_target(agent, gz)
                        summary = f"{agent['name']} heads to gather {unmet}"
                    elif unmet and gz and agent["currentZone"] == gz \
                            and agent["resources"].get(unmet, 0) < self._carry_cap(agent):
                        summary = self._perform_gather(agent, unmet)
                        if "collected" in summary:
                            resource_acted = unmet
                        else:
                            resource_acted = None
                    else:
                        summary = f"{agent['name']} has nothing to contribute"

        elif action == "build_structure":
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            elif self._is_project_complete(district_id):
                summary = self._build_active_structure(agent, district_id)
            else:
                summary = f"{agent['name']} waiting for more resources in {district_id}"

        elif action == "propose_blueprint":
            bp = decision.get("blueprint")
            if bp and bp.get("id") in c["rejectedBlueprintIds"]:
                summary = f"{agent['name']}'s blueprint {bp.get('id')} was already rejected"
                agent["lastBlueprintRejection"] = {
                    "reason": "blueprint was previously rejected", "frame": self.frameTick}
            else:
                ok, reason = self._validate_blueprint(bp)
                if ok:
                    needs_str = ", ".join(f"{k}x{v}" for k, v in bp["needs"].items())
                    c["pendingBlueprints"].append({
                        "id": bp["id"], "name": bp["name"], "needs": dict(bp["needs"]),
                        "function": dict(bp["function"]),
                        "newResources": [{"id": r["id"], "name": r["name"],
                                          "gatherZone": r.get("gather_zone"),
                                          "color": r.get("color", "#BDBDBD")}
                                         for r in (bp.get("new_resources") or [])],
                        "visualStyle": bp.get("visual_style") or "generic",
                        "sprite": bp.get("sprite"),
                        "proposedBy": agent["name"],
                    })
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    c["inventionRequiredStreak"] = 0
                    c["inventionBackstopFires"] = 0
                    agent["lastBlueprintRejection"] = None
                    summary = f"{agent['name']} proposed {bp['name']} (needs {needs_str})"
                else:
                    agent["lastBlueprintRejection"] = {"reason": reason, "frame": self.frameTick}
                    summary = f"{agent['name']} drafted an invalid blueprint ({reason})"

        elif action == "approve_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                bp = c["pendingBlueprints"][idx]
                for r in bp["newResources"]:
                    if r["id"] not in c["resourceRegistry"]:
                        c["resourceRegistry"][r["id"]] = {"name": r["name"],
                                                          "gatherZone": r["gatherZone"], "color": r["color"]}
                        # Age record for the custom-resource retirement gate
                        # (_maybe_retire_custom_resource picks the oldest).
                        c.setdefault("customResourceAddedFrame", {})[r["id"]] = self.frameTick
                c["projectRegistry"][bp["id"]] = {
                    "name": bp["name"], "needs": dict(bp["needs"]),
                    "visualStyle": bp["visualStyle"], "custom": True,
                    "sprite": bp.get("sprite"),
                    "function": dict(bp.get("function") or {}),
                }
                c.setdefault("approvedCustomApprovedFrame", {})[bp["id"]] = self.frameTick
                c["pendingBlueprints"].pop(idx)
                c["lastBlueprintActivityFrame"] = self.frameTick
                if decision.get("message"):
                    agent["message"] = decision["message"]
                    agent["messageTimer"] = 180
                summary = f"{agent['name']} approved {bp['name']} blueprint"
            else:
                summary = f"{agent['name']} could not approve that blueprint"

        elif action == "reject_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                bp = c["pendingBlueprints"].pop(idx)
                c["rejectedBlueprintIds"].add(bp["id"])
                # Amnesty clock (C3): the rejection expires after
                # BLUEPRINT_AMNESTY_FRAMES via _maybe_amnesty_rejected_blueprints.
                c.setdefault("rejectedBlueprintFrames", {})[bp["id"]] = self.frameTick
                summary = f"{agent['name']} rejected {bp['name']} blueprint"
            else:
                summary = f"{agent['name']} could not reject that blueprint"

        elif action == "assign_task":
            target = self._find_agent(decision.get("target"))
            if agent["role"] == "elder" and target and self._is_idle(target) and decision.get("message"):
                task_text = _clean_task_text(decision["message"], target["name"])
                target["assignedTask"] = task_text
                target["lastTaskedFrame"] = self.frameTick
                # Deliberately NOT written to c["directive"]: that field is
                # broadcast to every agent's prompt with "Prioritize it", and a
                # per-agent task there sent the whole roster chasing one
                # villager's errand (measured 83% move_to_district sessions).
                self._push_communication("directive", agent["name"], target["name"], task_text)
                self._deliver_message(agent["name"], target["name"], task_text, "directive")
                self._transmit_belief(agent, target, MEME_SPREAD_PROB)
                summary = f"Elder {agent['name']} tasked {target['name']}: {task_text}"
            else:
                summary = f"{agent['name']} could not assign that task"

        elif action == "change_role":
            if decision.get("new_role"):
                agent["role"] = decision["new_role"]
                summary = f"{agent['name']} became a {decision['new_role']}"

        elif action == "switch_role":
            new_role = decision.get("new_role") or decision.get("target")
            if EMERGENT_ROLES and new_role and new_role in self.d["ROLES"] and new_role != agent["role"]:
                old = agent["role"]
                agent["role"] = new_role
                agent["assignedTask"] = None
                agent["idleCycles"] = 0
                summary = f"{agent['name']} switched role from {old} to {new_role}"
            else:
                summary = f"{agent['name']} kept the {agent['role']} role"

        elif action == "propose_rule":
            summary = self._propose_rule(agent, decision)

        elif action == "vote_rule":
            summary = self._vote_on_rule(agent, decision)

        elif action == "heal_agent":
            patient = self._find_agent(decision.get("target")) if decision.get("target") else None
            if not patient or (patient["health"] >= 100 and not patient["incapacitated"]):
                patient = self._neediest_nearby(agent)
            if not patient:
                summary = f"{agent['name']} found no one to heal"
            elif self._distance_to(agent, patient) > 80:
                self._auto_move_toward_target(agent, patient["name"])
                summary = f"{agent['name']} moves to help {patient['name']}"
            else:
                boost = HEAL_AMOUNT * 2 if agent["role"] == "healer" else HEAL_AMOUNT
                patient["health"] = min(100, patient["health"] + boost)
                donate = self._first_edible(agent) if patient["incapacitated"] else None
                if donate:
                    agent["resources"][donate] -= 1
                    patient["resources"][donate] = patient["resources"].get(donate, 0) + 1
                    patient["hunger"] = min(100, patient["hunger"] + FOOD_RESTORE)
                if patient["incapacitated"] and patient["health"] > 0:
                    patient["incapacitated"] = False
                    patient["hunger"] = max(patient["hunger"], REVIVE_HUNGER)
                    self._push_activity(f"{patient['name']} was revived by {agent['name']}")
                self._nudge_ally(agent, patient["name"])
                self._push_memory(patient, f"Healed by {agent['name']}")
                summary = f"{agent['name']} healed {patient['name']}"

        # rest / default: summary already set

        agent["lastAction"] = action
        agent["lastReasoning"] = decision.get("reasoning")
        agent["actionCounts"][action] = agent["actionCounts"].get(action, 0) + 1
        if action not in ("rest", "talk_to_nearby", "assign_task"):
            agent["assignedTask"] = None
            agent["idleCycles"] = 0
        if agent.get("commitment"):
            commitment = agent["commitment"]
            if resource_acted and resource_acted == commitment.get("resource"):
                agent["commitment"] = None
                self._push_activity(f"{agent['name']} honored a promise to {commitment['to']}")
            elif self.frameTick - commitment.get("madeAt", self.frameTick) > COMMITMENT_EXPIRE_FRAMES:
                agent["commitment"] = None
        self._push_memory(agent, summary)

        ru = decision.get("relationship_update")
        if isinstance(ru, dict):
            agent["relationships"].update(ru)

        self._push_activity(summary)
        return summary

    def _resolve_talk_target(self, agent, decision):
        target = decision.get("target")
        if target and target in self.agent_names:
            return target
        nearby = self._get_nearby_agents(agent)
        return nearby[0] if nearby else "everyone"

    # --- goals (#1) ---
    def _goal_for_decision(self, decision):
        if not USE_GOALS or not decision:
            return None
        a = decision.get("action")
        district = decision.get("target_district")
        if a == "collect_resource":
            return {"kind": "gather", "target": decision.get("target"), "district": district, "ttl": 8}
        if a == "contribute_resources":
            return {"kind": "deliver", "target": decision.get("target"), "district": district, "ttl": 6}
        if a == "craft_item":
            return {"kind": "craft", "target": decision.get("target"), "ttl": 6}
        if a == "build_structure":
            return {"kind": "build", "target": None, "district": district, "ttl": 6}
        return None

    def _step_goal(self, agent):
        g = agent["goal"]
        if not g:
            return False
        if agent["incapacitated"]:
            agent["goal"] = None
            return False
        g["ttl"] -= 1
        if g["ttl"] < 0:
            agent["goal"] = None
            return False
        if g["kind"] == "craft_gather":
            return self._step_craft_gather_goal(agent, g)
        district_id = g.get("district") or self._resolve_contribution_district(agent)
        if g["kind"] in ("gather", "deliver", "build") and not district_id:
            agent["goal"] = None
            return False
        if g["kind"] == "gather" and not self._first_unmet_project_resource(district_id):
            agent["goal"] = None
            return False
        action_by_kind = {"gather": "collect_resource", "deliver": "contribute_resources",
                          "craft": "craft_item", "build": "build_structure"}
        action = action_by_kind.get(g["kind"])
        if not action:
            agent["goal"] = None
            return False
        summary = self.apply_decision(agent, {"action": action, "target": g.get("target"),
                                              "target_district": district_id,
                                              "message": None, "reasoning": f"goal:{g['kind']}"})
        s = summary or ""
        if any(t in s for t in ("has nothing to contribute", "found nothing", "nothing to craft",
                                "lacks ", "built ", "could not")):
            agent["goal"] = None
            return False
        return True

    def _apply_rule_based_fallback(self, agent):
        district_id = random.choice(list(self.civilization["districts"].keys()))
        self._set_agent_target(agent, district_id)
        self._push_memory(agent, f"{agent['name']} wandered toward {district_id}")
        self._push_activity(f"{agent['name']} wandered toward {district_id} (LLM fallback)")

    # --- LLM think job (runs in worker; builds payload, calls LM, applies) ---
    def _build_think_payload(self, agent):
        """Mirror index.html thinkAgent payload, computed under lock."""
        c = self.civilization
        nearby_detailed = self._get_nearby_detailed(agent)
        idle_agents = []
        if agent["role"] == "elder":
            for i, a in enumerate(self._idle_agents_for_elder()):
                idle_agents.append({
                    "name": a["name"], "role": a["role"], "longest_idle": i == 0,
                    "contribution_debt": self.frameTick - (a["lastContributedFrame"] or 0),
                })

        actives = self._active_project_districts()
        invention_required = self._invention_required()
        # One-shot invention-only turn (set by _maybe_invention_backstop):
        # the server swaps in a slim, proposal-only prompt for this call.
        invention_turn = bool(agent.get("inventionTurn"))
        if invention_turn:
            agent["inventionTurn"] = False
        nudges = []
        rejection = agent.get("lastBlueprintRejection")
        if rejection and self.frameTick - rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: Your last blueprint proposal was rejected: {rejection['reason']}. "
                          f"Propose a different blueprint that avoids that problem.")
        gather_rejection = agent.get("lastGatherRejection")
        if gather_rejection and self.frameTick - gather_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: Your last gather failed: {gather_rejection['reason']}. "
                          f"Contribute to an active terraform or start_terraform here before moving elsewhere.")
        terraform_rejection = agent.get("lastTerraformRejection")
        if terraform_rejection and self.frameTick - terraform_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: Your last start_terraform failed: {terraform_rejection['reason']}. "
                          f"Use a template id (plant_grove/clear_field/extend_beach) or name the district.")
        craft_rejection = agent.get("lastCraftRejection")
        if craft_rejection and self.frameTick - craft_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: Your last craft failed: {craft_rejection['reason']}. "
                          f"Gather the missing input first.")
        project_rejection = agent.get("lastProjectRejection")
        if project_rejection and self.frameTick - project_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: Your last start_project failed: {project_rejection['reason']}.")
        if GOODS_ENABLED:
            repair_rejection = agent.get("lastRepairRejection")
            if repair_rejection and self.frameTick - repair_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                nudges.append(f"NOTE: Your last repair failed: {repair_rejection['reason']}.")
            spoilage = c.get("lastSpoilage")
            if spoilage and self.frameTick - spoilage.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                nudges.append(f"NOTE: {spoilage['reason']}.")
            shelter = agent.get("lastShelterNote")
            if shelter and self.frameTick - shelter.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                nudges.append(f"NOTE: {shelter['reason']}. More houses would fix this.")
            worst_local = min((s for s in c["structures"]
                               if s.get("districtId") == agent.get("currentDistrict")
                               and (s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD)),
                              key=lambda s: s.get("condition", 100), default=None)
            if worst_local:
                state_word = "in ruins" if worst_local.get("isRuin") else "in disrepair and not working"
                nudges.append(f"NOTE: The {worst_local.get('name') or worst_local.get('type')} here is "
                              f"{state_word} (condition {int(worst_local.get('condition', 0))}). "
                              f"Use repair_structure to restore it.")
        abandonment = c.get("lastProjectAbandonment")
        if abandonment and self.frameTick - abandonment.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            nudges.append(f"NOTE: {abandonment['reason']}.")
        stalled_customs = self._stalled_approved_customs()
        if stalled_customs:
            pid, name, _ = stalled_customs[0]
            nudges.append(f"NOTE: The village approved {name} but never built it. "
                          f"Use start_project with target {pid}.")
        if ECOLOGY_ENABLED:
            stocks_line = self._format_district_stocks_for_prompt(agent)
            if ":depleted" in stocks_line or ":low" in stocks_line:
                nudges.append(f"NOTE: Local stocks are strained ({stocks_line}). "
                              f"Consider start_terraform (plant_grove/clear_field/extend_beach) or move_to_district.")
        if agent["assignedTask"] and \
                self.frameTick - (agent.get("lastTaskedFrame") or 0) > DIRECTIVE_TTL_FRAMES:
            # Same staleness problem as the directive: an old task (possibly
            # restored from state.json) shouldn't bias decisions forever.
            agent["assignedTask"] = None
        if agent["assignedTask"]:
            nudges.append(f"Your leader assigned you: {agent['assignedTask']}. Do it now.")
        if invention_required:
            nudges.append("NOTE: All known structures are already built. The village needs a NEW "
                          "invention -- use propose_blueprint now.")
        ready = next((did for did in actives if self._is_project_complete(did)), None)
        if ready:
            nudges.append(f"PROJECT READY: the build in {ready} is fully funded. "
                          f"Use build_structure with target_district {ready} now.")
        if agent.get("commitment"):
            commitment = agent["commitment"]
            nudges.append(f'NOTE: You agreed to help {commitment["to"]}: "{commitment["text"]}". '
                          f'Honor it soon with collect_resource, contribute_resources, or '
                          f'trade_resource for {commitment["resource"]}.')
        if not actives:
            # Suppressed while invention is required: start_project would be
            # refused anyway, and the nudge pulls the model away from
            # propose_blueprint (the only action that unblocks progress).
            if not invention_required:
                nudges.append("NOTE: No active project exists anywhere. Use start_project now to begin a build "
                              "(optionally set target_district to one of the known_districts ids).")
        elif agent["consecutiveTalks"] >= 2:
            nudges.append("NOTE: You have chatted twice. Prioritize collect_resource, contribute_resources, or move_to_agent.")
        directive = self._current_directive()
        if agent["role"] != "elder" and directive:
            nudges.append(f"Your leader directs: {directive}. Prioritize it.")
        if agent.get("consecutiveIdleMoves", 0) >= 3:
            nudges.append("NOTE: You have been moving without acting. Prioritize collect_resource or contribute_resources.")
        carry_cap = self._carry_cap(agent)
        capped = next(((k, v) for k, v in agent["resources"].items() if v >= carry_cap), None)
        if capped:
            nudges.append(f"NOTE: You are at capacity for {capped[0]} ({capped[1]}/{carry_cap}). "
                          f"Use contribute_resources or trade_resource instead of collecting more.")
        spec = self._role_specialty_resource(agent["role"])
        if spec and spec == self._first_unmet_resource_anywhere():
            nudges.append(f"NOTE: Your role specializes in {spec}, which an active project still needs. Prioritize collect_resource.")
        if EMERGENT_ROLES:
            need_role = self._village_needed_role()
            if need_role and need_role != agent["role"] and self._is_flexible_role(agent["role"]):
                nudges.append(f"NOTE: No one is gathering {self._first_unmet_resource_anywhere()}, "
                              f"which a build needs. Consider switch_role to {need_role} to fill the gap.")
        if RULES_ENABLED:
            unvoted = next((r for r in c["pendingRules"] if agent["name"] not in r["votes"]), None)
            if unvoted:
                nudges.append(f'NOTE: Pending rule "{unvoted["name"]}" (id {unvoted["id"]}) needs your vote. '
                              f"Use vote_rule with target {unvoted['id']} and vote yes or no.")
            elif (not c["rules"] and not c["pendingRules"]
                  and self.frameTick - c["lastRuleActivityFrame"] > BLUEPRINT_STALL_THRESHOLD):
                nudges.append("NOTE: The village has no shared rules yet. Consider propose_rule (a small resource_tax builds a shared stockpile).")
        if agent["role"] == "elder" and actives:
            stalled_district = next((did for did in actives
                                     if self.frameTick - c["districtLastContribution"].get(did, 0) > STALL_THRESHOLD), None)
            if stalled_district:
                stalled = self._first_unmet_project_resource(stalled_district)
                if stalled:
                    holders = sorted((a for a in self.agents if a["resources"].get(stalled, 0) > 0),
                                     key=lambda a: a["resources"].get(stalled, 0), reverse=True)
                    holder = holders[0]["name"] if holders else "no one"
                    nudges.append(f"NOTE: No progress on {stalled_district} in a while. {stalled} is still short; "
                                  f"{holder} is holding the most of it. Consider assign_task or contribute_resources.")
        if len(actives) < MAX_CONCURRENT_PROJECTS:
            idle_buildable = next((did for did in self._buildable_district_ids()
                                   if not c["districtProjects"].get(did)
                                   and self._district_structure_count(did) < c["districts"][did]["build_grid"]["cap"]),
                                  None)
            if idle_buildable and idle_buildable != agent.get("currentDistrict"):
                nudges.append(f"NOTE: {idle_buildable} has no build underway and there's room for another "
                              f"concurrent project (up to {MAX_CONCURRENT_PROJECTS} at once). Consider start_project "
                              f"with target_district {idle_buildable} if you're nearby.")
        if len(c["pendingBlueprints"]) < MAX_PENDING_BLUEPRINTS \
                and self.frameTick - c["lastBlueprintActivityFrame"] > BLUEPRINT_STALL_THRESHOLD:
            nudges.append("NOTE: No new blueprint activity in a while. Consider propose_blueprint if you have an idea.")
        if STRUCTURE_EFFECTS_ENABLED and not invention_required:
            pref = self.d["ROLE_PROJECT"].get(agent["role"].lower(), "house")
            prefs = pref if isinstance(pref, list) else [pref]
            if prefs and all(self._type_saturated(p) for p in prefs):
                nudges.append(f"NOTE: The village has enough {', '.join(prefs)} structures -- "
                              f"more add nothing. Build a different type or propose_blueprint.")
        if CRAFTING_ENABLED and self.frameTick - c["lastCraftActivityFrame"] > CRAFT_STALL_THRESHOLD:
            has_workshop = any(s["type"] == "workshop" for s in c["structures"])
            if agent["role"] == "elder" and not has_workshop:
                nudges.append("NOTE: No workshop exists yet. Direct an agent to build a Workshop so the village can craft planks, bricks, and tools for advanced builds.")
            elif has_workshop:
                granary = c["projectRegistry"].get("granary")
                if granary and "granary" not in c["builtTypes"]:
                    crafted_needs = ", ".join(f"{n} {r}" for r, n in granary["needs"].items()
                                              if r in self.RECIPES)
                    nudges.append(f"NOTE: No crafting in a while and the Granary is still unbuilt -- "
                                  f"it needs {crafted_needs}. At the workshop, craft_item those now.")
                else:
                    nudges.append("NOTE: No crafting in a while. At the workshop, craft_item (planks/bricks/tools) — advanced builds like the Granary need crafted goods.")
            else:
                nudges.append("NOTE: The village should build a Workshop, then craft goods for advanced builds like the Granary.")
        if SURVIVAL_ENABLED:
            if agent["hunger"] < EAT_THRESHOLD and agent["resources"].get("food", 0) == 0:
                nudges.append("NOTE: You are hungry and have no food. Gather food from the farm (or fish at the beach) before you starve.")
            collapsed = next((a for a in self.agents if a["incapacitated"]), None)
            if collapsed and collapsed["name"] != agent["name"]:
                verb = "Go heal_agent" if agent["role"] == "healer" else "Bring food or heal_agent"
                nudges.append(f"NOTE: {collapsed['name']} has collapsed. {verb} to revive them.")
            em = self._sage_emergency()
            if em and em["name"] != agent["name"] and agent["name"] in self._sage_responders(em):
                nudges.append(f"EMERGENCY: Elder Sage's life is the top priority — abandon your task and "
                              f"heal_agent {em['name']}. Nothing matters more than the elder's survival.")
        if nearby_detailed and agent["consecutiveTalks"] == 0 \
                and self.frameTick - agent.get("lastSpokeFrame", 0) > SOCIAL_SILENCE_FRAMES:
            nudges.append("NOTE: You haven't spoken with anyone in a while and someone is nearby. "
                          "Consider talk_to_nearby to coordinate plans, ask for help, or share what you know.")
        behavior_nudge = " ".join(nudges)

        return {
            "agent_name": agent["name"],
            "frame_tick": self.frameTick,
            "role": agent["role"],
            "role_skill": self.d["ROLE_SKILLS"].get(agent["role"], "helps the village"),
            "personality": agent["personality"],
            "memory": self._memory_for_prompt(agent),
            "resources": dict(agent["resources"]),
            "hunger": agent["hunger"],
            "health": agent["health"],
            "relationships": dict(agent["relationships"]),
            "beliefs": [self._belief_text(b) for b in agent["beliefs"]] if MEMES_ENABLED else [],
            "nearby_agents": nearby_detailed,
            "world_zone": agent["currentZone"],
            "current_district": agent.get("currentDistrict") or "none",
            "civilization_level": c["level"],
            "structures_built": len(c["structures"]),
            "structure_counts": {tid: self._structure_count(tid) for tid in c["projectRegistry"]}
                                if STRUCTURE_EFFECTS_ENABLED else {},
            "active_project": self._active_projects_brief(),
            "project_progress": self._active_projects_progress_text(),
            "known_districts": [{"id": did, "kind": d["kind"]} for did, d in c["districts"].items()
                                if d.get("build_grid")],
            "directive": self._current_directive() or "none",
            "invention_only": invention_turn,
            "invention_status": ("REQUIRED: every known structure is built or at capacity. Use "
                                 "propose_blueprint to invent a new structure.") if invention_required else "not needed",
            "commitment": agent.get("commitment"),
            "idle_agents": idle_agents,
            "known_resources": [{"id": rid, "gather_zone": d.get("gatherZone"),
                                 "custom": rid not in BASE_RESOURCES}
                                for rid, d in c["resourceRegistry"].items()],
            "pending_blueprints": [{"id": b["id"], "needs": b["needs"], "proposed_by": b["proposedBy"]}
                                   for b in c["pendingBlueprints"]],
            "known_recipes": [{"id": rid, "inputs": r["inputs"], "station": r["station"]}
                              for rid, r in self.RECIPES.items()] if CRAFTING_ENABLED else [],
            "pending_recipes": [{"id": r["id"], "inputs": r["inputs"], "proposed_by": r["proposedBy"]}
                                for r in c["pendingRecipes"]],
            "approved_custom_projects": self._custom_project_ids(),
            "rejected_blueprints": list(c["rejectedBlueprintIds"]),
            "known_effect_vectors": list(self._known_effect_vectors()),
            "district_stocks": self._format_district_stocks_for_prompt(agent),
            "known_terraform": list(TERRAFORM_TEMPLATES.keys()) if ECOLOGY_ENABLED else [],
            # Phase C: one short prompt line (server renders it only when set,
            # so flag-off prompts stay byte-identical to Phase B).
            "season": self._current_season(),
            "pending_rules": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"],
                               "yes": list(r["votes"].values()).count("yes"),
                               "no": list(r["votes"].values()).count("no"),
                               "proposed_by": r["proposedBy"]}
                              for r in c["pendingRules"]] if RULES_ENABLED else [],
            "active_rules": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"]}
                             for r in c["rules"]] if RULES_ENABLED else [],
            "recent_conversations": self._recent_conversations_text(),
            "inbox": self._drain_inbox(agent),
            "self_prompt": "",
            "module_reports": "none",
            "behavior_nudge": behavior_nudge,
            "available_actions": [a for a in self.d["AVAILABLE_ACTIONS"]
                                  if (a != "start_terraform" or ECOLOGY_ENABLED)
                                  and (a != "repair_structure" or GOODS_ENABLED)],
        }

    def _recent_conversations_text(self):
        if not self.conversationLog:
            return "none"
        return " | ".join(f"{c['from']} -> {c['to']}: {c['message']}"
                          for c in self.conversationLog[:5])

    def _think_job(self, agent_name):
        """Runs in the worker pool. Build payload under lock, do the network
        call OUTSIDE the lock, then apply the result UNDER the lock."""
        try:
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent or agent["incapacitated"]:
                    return
                payload = self._build_think_payload(agent)
            # Network call outside the lock (never block the tick thread or peers).
            decision = self.d["llm_decide"](payload)
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent:
                    return
                # In-flight guard (#A): if a Sage emergency began and THIS agent is
                # a designated responder, discard the decision and rush instead.
                em = self._sage_emergency()
                if em and agent is not em and not agent["incapacitated"] \
                        and agent["name"] in self._sage_responders(em):
                    self._rush_to_heal(agent, em)
                    return
                if not decision or decision.get("error") == "LM Studio offline":
                    self.lmStatus = "offline"
                    self._apply_rule_based_fallback(agent)
                elif decision.get("error") == "compute_error":
                    self.lmStatus = "compute_error"
                    self.llm_cooldown_until = time.time() + 30.0
                    self._apply_rule_based_fallback(agent)
                elif decision.get("error"):
                    self.lmStatus = "online"
                    self.apply_decision(agent, {"action": "rest"})
                else:
                    self.lmStatus = "online"
                    self.llm_cooldown_until = 0.0
                    if decision.get("terraform_rejection_note"):
                        agent["lastTerraformRejection"] = {
                            "reason": decision["terraform_rejection_note"],
                            "frame": self.frameTick,
                        }
                    elif decision.get("rejection_note"):
                        # normalize_decision swapped an invalid propose_blueprint
                        # for a fallback; remember why so the next prompt can
                        # tell the model instead of failing silently again.
                        agent["lastBlueprintRejection"] = {
                            "reason": decision["rejection_note"], "frame": self.frameTick}
                    self.apply_decision(agent, decision)
                    agent["goal"] = self._goal_for_decision(decision)
        except Exception:
            with self.lock:
                agent = self._find_agent(agent_name)
                if agent:
                    self.lmStatus = "offline"
                    self._apply_rule_based_fallback(agent)
        finally:
            with self.lock:
                a = self._find_agent(agent_name)
                if a:
                    a["isThinking"] = False
                self._inflight.discard(agent_name)

    def _schedule_think(self, agent):
        if agent["name"] in self._inflight:
            return
        if len(self._inflight) >= MAX_CONCURRENT_LLM:
            return
        now_ms = time.time() * 1000.0
        if time.time() < self.llm_cooldown_until:
            return
        if now_ms - self.last_llm_dispatch_ms < LLM_MIN_GAP_MS:
            return
        if agent["role"] == "elder":
            c = self.civilization
            c["inventionRequiredStreak"] = (c.get("inventionRequiredStreak", 0) + 1) \
                if self._invention_required() else 0
        self.last_llm_dispatch_ms = now_ms
        self._inflight.add(agent["name"])
        agent["isThinking"] = True
        self._executor.submit(self._think_job, agent["name"])

    # --- the per-frame tick (ported tick(), minus rendering) ---
    def _tick_once(self):
        with self.lock:
            # When paused, the sim clock freezes entirely (no movement, survival,
            # thinking, or frameTick advance) so the viewer sees a frozen world
            # and persistence captures a stable frame. (The browser advanced its
            # render-frame counter while paused; here frameTick is the sim clock.)
            if self.paused:
                return
            self.frameTick += 1
            ft = self.frameTick

            if SURVIVAL_ENABLED and ft % SURVIVAL_TICK_FRAMES == 0:
                for a in self.agents:
                    self._update_survival(a)
            if MEMORY_ENABLED and ft % MEMORY_TICK_FRAMES == 0:
                self._run_memory_maintenance()
            if EMERGENT_ROLES and ft % ROLE_SWITCH_TICK_FRAMES == 0:
                self._maybe_auto_switch_role()
            if RULES_ENABLED and ft % RULES_TICK_FRAMES == 0:
                self._maybe_advance_rules()
            if ft % RULES_TICK_FRAMES == 0:
                self._maybe_feed_starving()
                self._maybe_abandon_stalled_projects()
                self._maybe_relocate_stuck_project()
                self._maybe_force_contribution()
                self._maybe_start_idle_district_project()
                self._maybe_build_funded_project()
                self._maybe_start_approved_custom()
                self._maybe_retire_blueprint()
                self._maybe_amnesty_rejected_blueprints()
                self._maybe_retire_custom_resource()
                self._maybe_invention_backstop()
                self._maybe_found_district()
                self._maybe_welcome_newcomer()
            if STRUCTURE_EFFECTS_ENABLED and ft % EFFECT_TICK_FRAMES == 0:
                self._tick_structure_effects()
            if ECOLOGY_ENABLED and ft % ECOLOGY_REGROW_FRAMES == 0:
                self._tick_ecology_regrow()
            if GOODS_ENABLED and ft % GOODS_TICK_FRAMES == 0:
                self._tick_goods()
            if GOODS_ENABLED and ft % DAY_FRAMES == 0:
                self._tick_shelter()
            if MEMES_ENABLED and ft % MEME_TICK_FRAMES == 0:
                self._spread_beliefs_by_proximity()
            if BENCHMARKS_ENABLED and (ft % BENCHMARK_TICK_FRAMES == 0 or ft == FIRST_BENCHMARK_FRAME):
                self._sample_benchmarks()

            for a in self.agents:
                if not a["incapacitated"]:
                    self._move_agent(a, MOVE_SCALE)

            em_target = self._sage_emergency()
            responders = self._sage_responders(em_target) if em_target else None

            for a in self.agents:
                if a["messageTimer"] > 0:
                    a["messageTimer"] -= 1
                    if a["messageTimer"] == 0:
                        a["message"] = None
                if a["incapacitated"]:
                    continue
                if responders and a["name"] in responders:
                    a["thinkTimer"] -= 1
                    if a["thinkTimer"] <= 0:
                        self._rush_to_heal(a, em_target)
                        a["thinkTimer"] = GOAL_STEP_FRAMES
                    continue
                a["thinkTimer"] -= 1
                if a["thinkTimer"] <= 0 and not a["isThinking"] and a["name"] not in self._inflight:
                    if USE_GOALS and a["goal"] and not self._has_unread(a):
                        continuing = self._step_goal(a)
                        a["thinkTimer"] = GOAL_STEP_FRAMES if continuing else 1
                    else:
                        self._schedule_think(a)
                        a["thinkTimer"] = a["thinkInterval"]

    def _run_loop(self):
        while not self._stop.is_set():
            start = time.time()
            try:
                self._tick_once()
            except Exception:
                pass
            elapsed = time.time() - start
            sleep = TICK_DT - elapsed
            if sleep > 0:
                self._stop.wait(sleep)

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, name="SimEngine", daemon=True)
        self._thread.start()
        # Periodic full-state autosave (Contract 3). Separate daemon thread so a
        # slow disk write never stalls the fixed-timestep tick loop.
        self._saver = threading.Thread(target=self._save_loop, name="SimSaver", daemon=True)
        self._saver.start()

    def stop(self):
        self._stop.set()

    # --- full-state persistence (Contract 3) ---
    def _save_loop(self):
        while not self._stop.is_set():
            # Wait first so we don't immediately overwrite a freshly restored
            # state.json with a near-identical one before any work happens.
            if self._stop.wait(AUTOSAVE_SECONDS):
                break
            self.save_state()

    def _serialize_state(self):
        """Build the Contract-3 payload. Caller must hold self.lock."""
        c = self.civilization
        civ = {k: v for k, v in c.items() if k not in _CIV_SET_KEYS}
        # Deep-ish copy of nested mutables so the JSON dump can't race a mutation
        # after the lock is released; sets -> sorted arrays.
        civ = json.loads(json.dumps(civ, default=str))
        for key in _CIV_SET_KEYS:
            civ[key] = sorted(c.get(key, set()))
        agents = []
        for a in self.agents:
            ad = {k: v for k, v in a.items() if k not in ("beliefs", "isThinking")}
            ad = json.loads(json.dumps(ad, default=str))
            ad["beliefs"] = sorted(a.get("beliefs", set()))
            agents.append(ad)
        memory = []
        ms = self.d.get("memory_store")
        if ms is not None:
            try:
                memory = ms.export_entries()
            except Exception:
                memory = []
        return {
            "version": STATE_VERSION,
            "frameTick": self.frameTick,
            "savedAt": datetime.now(timezone.utc).isoformat(),
            "roster_size": self.roster_size,
            "civilization": civ,
            "agents": agents,
            "memory": memory,
        }

    def save_state(self):
        """Atomically write the complete world to STATE_PATH. Never raises."""
        try:
            with self.lock:
                payload = self._serialize_state()
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, STATE_PATH)
            return True
        except Exception:
            # Persistence must never crash the sim.
            return False

    def clear_state(self):
        """Remove state.json so the next start cold-starts. Never raises."""
        try:
            if os.path.exists(STATE_PATH):
                os.remove(STATE_PATH)
        except Exception:
            pass

    def _migrate_v1_to_v2(self, civ, agents):
        """One-time migration shim for pre-districts (STATE_VERSION 1) saves:
        old state.json files have the singular activeProject, no
        districts/roadNodes/roadEdges/frontierPlots at all (they were static
        constants before this plan), and no
        districtProjects/currentDistrict/waypoints. Seed all of those from the
        starter blueprint and drop the old in-flight activeProject -- a
        one-time, low-stakes loss of a build-in-progress only. Agent
        identity/memory/resources/relationships all carry over untouched."""
        civ["districts"] = json.loads(json.dumps(STARTER_DISTRICTS))
        civ["roadNodes"] = json.loads(json.dumps(STARTER_ROAD_NODES))
        civ["roadEdges"] = [list(e) for e in STARTER_ROAD_EDGES]
        civ["frontierPlots"] = _build_frontier_plots()
        civ["districtProjects"] = {did: None for did, d in civ["districts"].items() if d.get("build_grid")}
        civ["districtLastContribution"] = {did: 0 for did in civ["districtProjects"]}
        civ["kindLastActivityFrame"] = {}
        civ["lastDistrictFoundFrame"] = 0
        civ["frontierExhaustedLogged"] = False
        civ.pop("activeProject", None)
        civ.pop("lastProjectContributionFrame", None)
        kind_to_starter_district = {}
        for did, d in civ["districts"].items():
            kind_to_starter_district.setdefault(d["kind"], did)
        for a in agents:
            kind = a.get("currentZone") or "village"
            a["currentDistrict"] = kind_to_starter_district.get(kind, "village_core")
            a["waypoints"] = []

    def restore_state(self):
        """If a valid state.json exists, rehydrate the world from it instead of
        the cold-start roster. Returns True on a successful restore. Accepts
        both the current STATE_VERSION and the pre-districts version 1 (run
        through _migrate_v1_to_v2 below) so an old save cold-starts the new
        fields cleanly instead of crashing or being silently discarded."""
        try:
            if not os.path.exists(STATE_PATH):
                return False
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            return False
        if not isinstance(data, dict) or data.get("version") not in (1, STATE_VERSION):
            return False
        try:
            with self.lock:
                civ = dict(data.get("civilization") or {})
                for key in _CIV_SET_KEYS:
                    civ[key] = set(civ.get(key) or [])
                # builtTypes backfill: a save from before #5.1 has no record of
                # which project types were ever completed, even though
                # civ["structures"] already captures it losslessly (append-only,
                # never pruned) -- derive it instead of leaving
                # _invention_required() permanently False for a long-lived,
                # already-built-out village.
                civ["builtTypes"].update(
                    s.get("type") for s in (civ.get("structures") or []) if s.get("type"))
                # basePopulation backfill: saves from before structure effects
                # existed have no record of the starting roster -- treat the
                # saved roster as the base so existing houses grow it from here.
                if not civ.get("basePopulation"):
                    civ["basePopulation"] = max(1, min(len(AGENT_DEFS),
                                                       len(data.get("agents") or []) or 8))
                civ.setdefault("effectLastFire", {})
                civ.setdefault("approvedCustomApprovedFrame", {})
                civ.setdefault("lastProjectAbandonment", None)
                civ.setdefault("approvedCustomBackoffUntil", 0)
                civ.setdefault("approvedCustomBackstopFailures", 0)
                civ.setdefault("approvedCustomEscalationLogged", False)
                civ.setdefault("projectAbandonStreak", {})
                civ.setdefault("deferredProjectTypes", {})
                civ.setdefault("rejectedBlueprintFrames", {})
                civ.setdefault("customResourceAddedFrame", {})
                # Phase C: spoilage nudge state. Structure condition/isRuin
                # deliberately have NO migration -- every read defaults via
                # .get(cond, 100), so pre-Phase-C structures start pristine.
                civ.setdefault("lastSpoilage", None)
                if ECOLOGY_ENABLED and not civ.get("districtStocks"):
                    civ["districtStocks"] = self._init_district_stocks(
                        civ.get("districts") or {}, civ.get("resourceRegistry"))
                agents = []
                is_scaffold = self.d.get("is_scaffold_text")
                for ad in (data.get("agents") or []):
                    a = dict(ad)
                    # isThinking is an in-flight-only runtime flag; a snapshot
                    # taken mid-think would otherwise wedge the agent forever
                    # (the dispatch gate requires False and only the dead
                    # process's _think_job could have reset it).
                    a["isThinking"] = False
                    a["beliefs"] = set(a.get("beliefs") or [])
                    a.setdefault("lastProjectRejection", None)
                    a.setdefault("lastTerraformRejection", None)
                    a.setdefault("lastCraftRejection", None)
                    a.setdefault("lastRepairRejection", None)
                    a.setdefault("lastShelterNote", None)
                    # state.json may have been written before scaffold
                    # validation existed (or before a clean cycle ran), so a
                    # saved agent's memory.longTerm list can carry leaked
                    # chain-of-thought text wholesale -- scrub it on load too.
                    if is_scaffold and isinstance(a.get("memory"), dict):
                        long_term = a["memory"].get("longTerm")
                        if long_term:
                            a["memory"] = dict(a["memory"])
                            a["memory"]["longTerm"] = [
                                t for t in long_term if not is_scaffold(t)
                            ]
                    agents.append(a)
                if not agents or not civ:
                    return False
                if data.get("version") == 1:
                    self._migrate_v1_to_v2(civ, agents)
                self.civilization = civ
                self.agents = agents
                self.agent_names = set(a["name"] for a in agents)
                self.frameTick = int(data.get("frameTick") or 0)
                rs = data.get("roster_size")
                if rs:
                    self.roster_size = int(rs)
                self._recompute_road_paths()
                _validate_districts(self.civilization["districts"])
                _validate_road_graph(self.civilization["roadNodes"], self.civilization["roadEdges"])
                # Rebuild the MemoryStore by re-embedding each entry's text.
                ms = self.d.get("memory_store")
                if ms is not None:
                    try:
                        ms.import_entries(data.get("memory") or [])
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    # --- control + snapshot (Contract 2) ---
    def pause(self):
        with self.lock:
            self.paused = True

    def resume(self):
        with self.lock:
            self.paused = False

    def reset(self, roster_size=None):
        with self.lock:
            self._reset_world(roster_size if roster_size else self.roster_size)
            if roster_size:
                self.roster_size = roster_size
            ms = self.d.get("memory_store")
            if ms is not None:
                try:
                    ms.clear()
                except Exception:
                    pass
        # Replace the on-disk save so a reset truly starts fresh: clear the old
        # snapshot, then immediately persist the fresh cold-started world.
        self.clear_state()
        self.save_state()

    def snapshot(self):
        """Consistent /state snapshot per Contract 2 (copied under lock)."""
        with self.lock:
            c = self.civilization
            district_projects = {}
            for did, ap in c["districtProjects"].items():
                if not ap:
                    district_projects[did] = None
                    continue
                total = sum(ap["needs"].values())
                done = sum(min(ap["contributed"].get(r, 0), n) for r, n in ap["needs"].items())
                pct = round(done / total * 100) if total else 0
                progress_text = ", ".join(f"{r} {ap['contributed'].get(r, 0)}/{n}"
                                          for r, n in ap["needs"].items())
                district_projects[did] = {"name": ap["name"], "type": ap["type"],
                                          "progressText": progress_text, "progressPercent": pct}
            agents = [{
                "id": a["id"], "name": a["name"], "role": a["role"], "color": a["color"],
                "x": a["x"], "y": a["y"], "currentZone": a["currentZone"],
                "currentDistrict": a.get("currentDistrict"),
                "waypoints": len(a.get("waypoints") or []),
                "resources": dict(a["resources"]), "hunger": a["hunger"], "health": a["health"],
                "incapacitated": a["incapacitated"], "message": a["message"],
                "isThinking": a["isThinking"], "beliefs": [self._belief_text(b) for b in a["beliefs"]],
                "lastAction": a["lastAction"], "assignedTask": a["assignedTask"],
            } for a in self.agents]
            civ = {
                "level": c["level"],
                "structures": [{"id": s["id"], "type": s["type"], "x": s["x"], "y": s["y"],
                                "visualStyle": s.get("visualStyle"), "name": s.get("name"),
                                "sprite": s.get("sprite"),
                                "districtId": s.get("districtId"),
                                "condition": s.get("condition", 100),
                                "isRuin": bool(s.get("isRuin"))}
                               for s in c["structures"]],
                "districtProjects": district_projects,
                "completedProjects": c["completedProjects"],
                "resourceRegistry": {rid: dict(d) for rid, d in c["resourceRegistry"].items()},
                "projectRegistry": {pid: dict(p) for pid, p in c["projectRegistry"].items()},
                "pendingBlueprints": [dict(b) for b in c["pendingBlueprints"]],
                "pendingRecipes": [dict(r) for r in c["pendingRecipes"]],
                # The viewer's Recipes sidebar row reads civ.recipes; it was
                # dead (always empty) because the snapshot never included the
                # live RECIPES registry (C5 cleanup, 2026-07-06).
                "recipes": {rid: {"name": r["name"], "inputs": dict(r["inputs"]),
                                  "station": r.get("station")}
                            for rid, r in self.RECIPES.items()} if CRAFTING_ENABLED else {},
                "rules": [dict(r) for r in c["rules"]],
                "pendingRules": [dict(r) for r in c["pendingRules"]],
                "directive": self._current_directive(),
                "season": self._current_season(),
                "stockpile": dict(c["stockpile"]),
                "taxDue": c["taxDue"], "taxPaid": c["taxPaid"],
                "collectAttempts": c["collectAttempts"], "collectSuccesses": c["collectSuccesses"],
            }
            benchmarks = dict(self.lastBenchmarks)
            activity = list(self.activityLog)
            conversation = list(self.conversationLog[:30])
            return {
                "frameTick": self.frameTick,
                "paused": self.paused,
                "lmStatus": self.lmStatus,
                "agents": agents,
                "civilization": civ,
                "benchmarks": benchmarks,
                "activity": activity,
                "conversation": conversation,
                "config": {
                    "WORLD_W": WORLD_W, "WORLD_H": WORLD_H,
                    "flags": {
                        "SURVIVAL_ENABLED": SURVIVAL_ENABLED, "USE_GOALS": USE_GOALS,
                        "EMERGENT_ROLES": EMERGENT_ROLES, "RULES_ENABLED": RULES_ENABLED,
                        "MEMES_ENABLED": MEMES_ENABLED, "CRAFTING_ENABLED": CRAFTING_ENABLED,
                        "META_SYSTEM": META_SYSTEM, "PIANO_MODULES": PIANO_MODULES,
                        "ROADS_ENABLED": ROADS_ENABLED,
                        "ECOLOGY_ENABLED": ECOLOGY_ENABLED,
                        "GOODS_ENABLED": GOODS_ENABLED,
                    },
                },
            }
