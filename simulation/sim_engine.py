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
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

# Full-state persistence (Contract 3), backed by a SQLite database. Resolved
# relative to this module so it lands next to server.py/sim_engine.py
# regardless of the launch cwd.
# STATE_VERSION was bumped 1 -> 2 for the world-expansion plan
# (civilization.activeProject -> districtProjects, new
# districts/roadNodes/roadEdges/frontierPlots); v1 saves are no longer
# supported.
STATE_VERSION = 2
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.db")
AUTOSAVE_SECONDS = 10
# Sets on the civilization that serialize to JSON arrays and back.
_CIV_SET_KEYS = ("rejectedBlueprintIds", "rejectedRecipeIds", "builtTypes")


_DB_DDL = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS civ    (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agents (name TEXT PRIMARY KEY, ord INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory (
    rowid_pk INTEGER PRIMARY KEY, id INTEGER, agent TEXT NOT NULL, text TEXT NOT NULL,
    salience REAL, kind TEXT, tier TEXT, frame_tick INTEGER, ts REAL);
"""


def _connect_db(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_DB_DDL)
    return conn


def _write_state_db(path, payload):
    """Atomically persist a Contract-3 payload dict into the SQLite state
    database at `path`. All table rewrites happen in a single transaction so
    a crash mid-write can never leave the DB half-updated. May raise; callers
    (SimEngine.save_state) swallow exceptions."""
    conn = _connect_db(path)
    try:
        civ = payload.get("civilization") or {}
        agents = payload.get("agents") or []
        memory = payload.get("memory") or []
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("version", str(payload.get("version"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("frameTick", str(payload.get("frameTick"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("savedAt", str(payload.get("savedAt"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("roster_size", str(payload.get("roster_size"))),
            )
            conn.execute("DELETE FROM civ")
            conn.executemany(
                "INSERT INTO civ (key, value) VALUES (?, ?)",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in civ.items()],
            )
            conn.execute("DELETE FROM agents")
            conn.executemany(
                "INSERT INTO agents (name, ord, data) VALUES (?, ?, ?)",
                [(a.get("name"), i, json.dumps(a, ensure_ascii=False))
                 for i, a in enumerate(agents)],
            )
            conn.execute("DELETE FROM memory")
            conn.executemany(
                "INSERT INTO memory (id, agent, text, salience, kind, tier, frame_tick, ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(m.get("id"), m.get("agent"), m.get("text"), m.get("salience"),
                  m.get("kind"), m.get("tier"), m.get("frame_tick"), m.get("ts"))
                 for m in memory],
            )
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
    finally:
        conn.close()


def _read_state_db(path):
    """Read a Contract-3 payload dict back out of the SQLite state database at
    `path`, or return None if it doesn't exist, is empty, or is corrupt."""
    if not os.path.exists(path):
        return None
    conn = None
    try:
        conn = _connect_db(path)
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if "version" not in meta:
            return None
        try:
            version = int(meta.get("version"))
        except (TypeError, ValueError):
            return None
        try:
            frame_tick = int(meta.get("frameTick"))
        except (TypeError, ValueError):
            frame_tick = 0
        try:
            roster_size = int(meta.get("roster_size"))
        except (TypeError, ValueError):
            roster_size = None
        civilization = {
            k: json.loads(v)
            for k, v in conn.execute("SELECT key, value FROM civ").fetchall()
        }
        agents = [
            json.loads(data)
            for (data,) in conn.execute("SELECT data FROM agents ORDER BY ord").fetchall()
        ]
        memory = [
            {
                "id": row[0], "agent": row[1], "text": row[2], "salience": row[3],
                "kind": row[4], "tier": row[5], "frame_tick": row[6], "ts": row[7],
            }
            for row in conn.execute(
                "SELECT id, agent, text, salience, kind, tier, frame_tick, ts FROM memory"
            ).fetchall()
        ]
        return {
            "version": version,
            "frameTick": frame_tick,
            "savedAt": meta.get("savedAt"),
            "roster_size": roster_size,
            "civilization": civilization,
            "agents": agents,
            "memory": memory,
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()


# --- Feature flags (ported from index.html consts; now server config) ---
SURVIVAL_ENABLED = True
CRAFTING_ENABLED = True
USE_GOALS = True
STRUCTURE_EFFECTS_ENABLED = True
MEMORY_ENABLED = True
AGENT_MESSAGING = True
PIANO_MODULES = True
META_SYSTEM = True
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
# Structure upgrades: level 1-100 per instance; duplicate builds blocked until
# every existing instance of that type is maxed (forward-only for legacy saves).
STRUCTURE_UPGRADES_ENABLED = True
MAX_STRUCTURE_LEVEL = 100
LEVEL_STEP = 1              # levels gained per upgrade_structure action (1 → 2 → 3 …)
UPGRADE_STAT_STEP = 10        # cost + produce/boost weight tier every N levels
UPGRADE_TIERS = (1, 25, 50, 75, 100)
UPGRADE_COST_BASE = 1       # primary material units; scales with level tier
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
# A pending blueprint whose sage review never lands (elder offline/incapacitated
# the whole window) auto-skips the review after this many frames rather than
# blocking approval forever -- same deadlock-avoidance shape as the amnesty
# clock above, just for the review stage instead of the rejection stage.
SAGE_REVIEW_TIMEOUT_FRAMES = STALL_THRESHOLD * 20
MAX_PENDING_RULES = 4
MAX_ACTIVE_RULES = 8
MAX_EMERGENT_ROLES = 8

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

VALID_GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
VALID_VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
RULE_KINDS = {"resource_tax", "custom", "priority"}

# Must match LM Studio's loaded parallel slots (scripts/lms_load.py loads
# context 20000 / parallel 3 -- per-slot budget ~6666 tokens). Raised 2->3 on
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
# SimEngine.piano_workers / _run_piano_modules. Context budget for LM Studio
# must now cover MAX_CONCURRENT_LLM + PIANO_CONCURRENT_LLM = 5 parallel slots
# (specs/03-cognition.md).
PIANO_CONCURRENT_LLM = 2
# Off-tick module reports (e.g. social/reflection on a tick they don't fire)
# are served from the last real report instead of an empty slot, as long as
# it is no more than this many module-ticks stale -- see _run_piano_modules.
PIANO_MODULE_CACHE_TTL = 2
# Wait budget for a dispatched module future -- strictly above server.py's
# PIANO_MODULE_TIMEOUT_S (15s) HTTP timeout so that timeout, not this one,
# is what fires and gets logged/counted as a drop in the normal case.
PIANO_MODULE_TIMEOUT_WAIT_S = 18
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
# to hold -- unpayable by 12 agents; 409/416 became ruins, throughput 0).
# Mid retune 0.1/tick (~5.8h to disrepair, ~8.3h to ruin) still failed the
# 2026-07-10 morning soak on a reset world: ruins 11→154 over ~13.5h real /
# ~8.6h sim while agents were heal-spamming, all 15 houses non-working, births
# stalled (pop 16→5). Now 0.05/tick: ~11.7h of neglect to disrepair, ~16.7h to
# ruin -- survives one unattended overnight; sprawl still decays across days.
# Paired with _maybe_repair_critical (house category) so zero working houses
# can't permanently lock the population cap. A ruin is rebuilt via
# repair_structure for half the original needs (min 1 each) -- cheaper than
# new, the deterministic escape.
STRUCTURE_DECAY_PER_GOODS_TICK = 0.05
STRUCTURE_DISREPAIR_THRESHOLD = 30
REPAIR_CONDITION_RESTORE = 50
# Disaster: rare random damage so decay isn't perfectly predictable. 0.005 per
# ~30s goods tick => expected roughly once per 100 minutes of runtime.
DISASTER_PROB = 0.005
DISASTER_DAMAGE = (40, 70)
# Seasons: a four-season clock derived purely from frameTick (no extra state to
# persist). YEAR_FRAMES is the single canonical in-world year -- 3 real hours
# = exactly 24 day/night cycles (DAY_FRAMES) -- and seasons and aging both
# derive from it, so the GUI calendar, the season clock, and agent ages stay
# in sync. One season = YEAR_FRAMES/4 (~45 min = exactly 6 day/night cycles;
# an overnight soak sees several winters). The season multiplies district
# stock regrowth: spring booms, winter stops regrowth entirely. Escapes:
# stores/granary capacity built before winter (spoilage permitting), and the
# season simply turning. Note: winter lengthened 30->45 min in the 2026-07-14
# year unification -- watch food runway across winter on the next soak.
YEAR_FRAMES = 324_000
SEASON_FRAMES = YEAR_FRAMES // 4  # 81_000: one season = 45 min = exactly 6 day/night cycles
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
BASE_PRICE = {"food": 1, "fish": 1, "water": 1, "wood": 1, "herbs": 1,
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
# YEAR_FRAMES instead of a hand-tuned constant (~1y/3.0h, 0→90 in ~11.25
# days) so aging stays locked to the same canonical year as the season clock
# and GUI calendar. Smoke-testing forces this by temporarily shrinking the
# gate/increment, never by waiting.
LIFECYCLE_TICK_FRAMES = 300
AGE_YEARS_PER_TICK = LIFECYCLE_TICK_FRAMES / YEAR_FRAMES  # = 1/1080: exactly 1 year per YEAR_FRAMES (3.0 h)
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
CHRONICLE_CAP = 20
CHRONICLE_PROMPT_ENTRIES = 3         # how many recent entries to fold into the prompt line
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
        # Real wall-clock seconds at process start, for the GUI's "uptime"
        # display. Deliberately not persisted/restored -- it reflects time
        # since the server process last started, not since the world began.
        self.processStartTime = time.time()
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
        # Sid-parity Phase 1: separate pool for PIANO module calls so they
        # never compete with decision calls for MAX_CONCURRENT_LLM slots.
        self.piano_workers = ThreadPoolExecutor(max_workers=PIANO_CONCURRENT_LLM)
        self._inflight = set()      # agent names with a think job in flight
        self.RECIPES = {}
        self.roster_size = roster_size
        self._effect_period_fired = 0
        self._module_period_runs = 0
        self._last_effect_benchmark_fired = 0
        # PIANO module report cache: {agent_name: {module_name: {"tick": int,
        # "text": str}}} -- fills off-tick stagger slots (see
        # _run_piano_modules) instead of leaving them empty, TTL-bounded by
        # PIANO_MODULE_CACHE_TTL.
        self._piano_module_cache = {}
        self._piano_module_drops = 0     # timeouts/failures this session
        self._piano_latency_ms = {}      # module -> [sum_ms, count] this period
        self._meta_agent_index = 0
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
                "commitment": None, "inventionTurn": False, "inventionRetryUsed": False,
                "inventionBuildContext": None,
                "spriteDesignTurn": None,
                "lastBlueprintRejection": None, "lastGatherRejection": None,
                "lastUpgradeRejection": None, "lastSpriteRejection": None,
                "lastProjectRejection": None, "lastTerraformRejection": None,
                "lastCraftRejection": None, "lastRepairRejection": None,
                "lastRecipeRejection": None, "lastBurialRejection": None,
                "lastShelterNote": None, "lastSpokeFrame": 0,
                "persona": "", "idleFrames": 0, "moduleTick": 0,
                "modules": {"perception": True, "social": True, "desire": True, "reflection": True},
                # Phase E: home structure id (None = homeless) + refusal nudges.
                "homeStructureId": None, "lastTradeRejection": None,
                "lastHomelessNudgeFrame": None,
                # Agent-driven reorg: structureId this agent is relocating, or None.
                "reorgTask": None,
            }
            if LIFECYCLE_ENABLED:
                # Phase F: staggered starting ages so the roster isn't a single
                # generation -- the elder starts oldest (just past ELDER_AGE,
                # so Sage is mortal from frame 0 but not on the brink), the
                # rest spread across young/adult so aging/succession has
                # texture from the first soak rather than needing weeks to
                # differentiate. Deterministic (seeded by roster index), not
                # random, so a fresh cold-start is reproducible.
                if d["role"] == "elder":
                    a["age"] = float(ELDER_AGE + 5)
                else:
                    a["age"] = float(ADULT_AGE + 2 + (i * 7) % 30)
                a["lastQuotaResetFrame"] = 0
                a["gatherCountThisPeriod"] = {}
                a["lastQuotaRejection"] = None
                a["lastRationingRejection"] = None
                a["parents"] = None
                a["deathFrame"] = None
                # Cemetery/burial: unset until a permanent death is buried
                # (see CEMETERY_ENABLED above); irrelevant while alive.
                a["buried"] = False
                a["restingPlaceId"] = None
                a["restingDistrictId"] = None
            else:
                a["age"] = None
            if CULTURE_ENABLED:
                # Phase G: per-agent skill levels (float, starts at 0 -- a
                # newborn/newcomer has no practice yet, matching "children
                # lack skills and inherit them slowly" from the plan). Life
                # events append to personalityTraits, folded into the
                # personality prompt line at build time (see build_user_prompt).
                a["skills"] = {k: 0.0 for k in SKILL_KINDS}
                a["personalityTraits"] = []
                a["lastTeachFrame"] = 0
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
        self.RECIPES = {k: {"name": v["name"], "inputs": dict(v["inputs"]), "station": v["station"],
                            **({"tier": v.get("tier", 1)} if TECH_TREE_ENABLED else {})}
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
            # roles.json remains the seed authoring source. This copy is the
            # persistent, per-world registry that can receive elder-approved
            # emergent roles without ever mutating the seed file.
            "roleRegistry": {role: dict(defn) for role, defn in self.d["ROLES"].items()},
            "pendingRoles": [],
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
            # Phase 1 Sid-parity: frame when a role need first appeared; used
            # for role_rebalance_latency. Cleared when the need resolves or a
            # switch fires.
            "roleNeedSinceFrame": None,
            "lastRoleRebalanceLatency": None,
            "collectAttempts": 0,
            "collectSuccesses": 0,
            "rules": [],
            "pendingRules": [],
            "ruleKindsEverEnacted": [],
            "stockpile": {},
            "taxDue": 0,
            "taxPaid": 0,
            "effectLastFire": {},
            "districtStocks": {},
            "upkeepLastDay": {},
            "litDistricts": [],
            "approvedCustomApprovedFrame": {},
            "lastProjectAbandonment": None,
            "lastSpoilage": None,
            "approvedCustomBackoffUntil": 0,
            "approvedCustomBackstopFailures": 0,
            "approvedCustomEscalationLogged": False,
            "projectAbandonStreak": {},
            "deferredProjectTypes": {},
            # Phase D (TECH_TREE_ENABLED): era + invention-council state.
            "era": None,
            "eraIndex": 0,
            "councilActive": None,
            "councilLog": [],
            # Phase F (LIFECYCLE_ENABLED): population lifecycle + governance.
            "lastBirthFrame": 0,
            "lastDeathActivityFrame": 0,
            "births": 0,
            "deaths": 0,
            "nextGeneratedAgentId": 1000,  # synthetic ids for generated villagers once AGENT_DEFS is exhausted
            "pendingSuccession": None,     # {electionId, candidates:[names], startFrame, deadline}
            "lastSuccessionActivityFrame": 0,
            "harvestQuotas": {},            # rule id -> {"district": id|None, "resource": id|None, "value": n}
            "rationingActive": {},          # rule id -> {"value": n}
            "populationFloorHeld": False,   # last death-deferred-at-floor state, for the nudge
            # Phase G (CULTURE_ENABLED): knowledge, chronicle, meme mutation.
            "chronicle": [],                # capped ring: {"text": str, "frame": int, "kind": str}
            "libraryKnowledge": [],         # capped ring: {"agent": name, "skill": kind, "level": float, "frame": int}
            "memeTexts": {},                # belief id -> mutated text override (see _belief_text)
            "memeMutations": 0,             # session-lifetime count, enforces MEME_MUTATION_SESSION_CAP
            "beliefRegistry": {
                bid: {"id": bid, "name": bid.replace("_", " ").title(),
                      "tenet": text, "affinity": sorted(MEME_RULE_AFFINITY.get(bid, set())),
                      "authoredBy": None, "createdFrame": 0, "seed": True}
                for bid, text in MEMES.items()
            },
            "beliefPitchCalls": 0,
            "skillPracticeCount": 0,        # benchmark helper for skill_spread
            "teachCount": 0,                # benchmark helper for skill_spread
            # Path 1: settlements, treaties, composable/terrain counters.
            "settlements": [],
            "treaties": [],
            "caravanLog": [],
            "path1Placements": 0,
            "path1TerrainMutations": 0,
            # Agent-driven structure reorganization (footprint-overlap fixup):
            # at most one task in flight; see _maybe_reorganize_structures.
            "reorgTasks": [],
            "lastReorgFrame": 0,
            "lastReorgCheckFrame": 0,
            "lastReorgNoRoomFrame": 0,
        }
        self._effect_period_fired = 0
        self._module_period_runs = 0
        self._last_effect_benchmark_fired = 0
        self._meta_agent_index = 0
        self._spoiled_period = 0     # Phase C: spoilage counter per benchmark period
        self._last_season = None     # Phase C: season-turn activity logging
        if ECOLOGY_ENABLED:
            self.civilization["districtStocks"] = self._init_district_stocks(self.civilization["districts"])
        if path1_on():
            for d in self.civilization["districts"].values():
                d.setdefault("tiles", {})
                self._ensure_district_terrain(d)
            self._init_settlements()
        self._recompute_road_paths()
        self._rebuild_role_maps()
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
            d = _dist(agent["x"], agent["y"], o["x"], o["y"])
            if d <= 80:
                near.append((d, {"name": o["name"], "role": o["role"],
                             "food": o["resources"].get("food", 0),
                             "wood": o["resources"].get("wood", 0),
                             "gold": o["resources"].get("gold", 0)}))
        # C3: sort nearest-first before the MAX_NEARBY_AGENTS_PROMPT cap below
        # so a crowded radius always keeps the closest agents, not an
        # arbitrary iteration-order slice.
        near.sort(key=lambda pair: pair[0])
        return [item for _, item in near[:MAX_NEARBY_AGENTS_PROMPT]]

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

    def _en_route_to(self, agent, district_id):
        """True while the agent's final travel destination already lies in
        the given district and they haven't arrived yet. Guards the callers
        that re-issue routing every goal step: without it each call re-rolls
        a new random destination point and replans the road path, which reads
        as agents jittering/circling around road hubs instead of walking."""
        d = self.civilization["districts"].get(district_id)
        if not d:
            return False
        wps = agent.get("waypoints") or []
        fx = wps[-1]["x"] if wps else agent.get("targetX")
        fy = wps[-1]["y"] if wps else agent.get("targetY")
        if fx is None or fy is None:
            return False
        b = d["bounds"]
        if not (b["x1"] <= fx <= b["x2"] and b["y1"] <= fy <= b["y2"]):
            return False
        return abs(agent["x"] - fx) + abs(agent["y"] - fy) > 1.0

    def _set_agent_target_once(self, agent, target):
        """_set_agent_target, but a no-op while already traveling there."""
        district_id = self._resolve_target_district(target, agent)
        if district_id and self._en_route_to(agent, district_id):
            return
        self._set_agent_target(agent, target)

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
        # Phase D: wagon holders travel faster (query-time vehicle effect).
        step = agent["speed"] * scale * self._vehicle_speed_mult(agent)
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
        if LIFECYCLE_ENABLED and agent.get("deathFrame") is not None:
            # Phase F: death is permanent, unlike a survival collapse. Without
            # this guard the COLLAPSE_REGEN/COLLAPSE_REVIVE_HEALTH path below
            # (designed for a temporarily incapacitated agent) would
            # eventually heal a corpse back past the revive threshold and
            # resurrect it -- "{name} recovered" on someone already dead,
            # who then resumes moving/thinking/voting with a stale role.
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
                if CULTURE_ENABLED:
                    self._drift_personality(agent, "wary of hunger since a collapse")

    def _neediest_nearby(self, agent):
        nearby = [self._find_agent(n) for n in self._get_nearby_agents(agent)]
        nearby = [a for a in nearby if a and a.get("deathFrame") is None
                  and (a["incapacitated"] or a["health"] < 60)]
        if not nearby:
            return None
        nearby.sort(key=lambda a: (0 if a["incapacitated"] else 1, a["health"]))
        return nearby[0]

    # --- Sage emergency ---
    def _sage_emergency(self):
        if not SURVIVAL_ENABLED:
            return None
        # Phase F: the elder is mortal. A dead elder is permanently
        # incapacitated (no revival path applies post-mortem), so without this
        # guard a deceased Sage would look like a standing emergency forever
        # -- responders would rush to a corpse instead of working, and no
        # amount of healing ever clears it. Once dead, there is no emergency
        # to respond to; _agent_dies has already started succession, which is
        # the correct next step, not a rescue.
        sage = next((a for a in self.agents
                    if a["role"] == "elder" and a.get("deathFrame") is None), None)
        if not sage:
            return None
        if not sage["incapacitated"] and sage["health"] >= SAGE_CRITICAL_HEALTH:
            return None
        healer = next((a for a in self.agents if a["role"] == "healer" and a.get("deathFrame") is None), None)
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
            # Note: this count-vs-cap check is an optimistic pre-filter -- it
            # can overestimate room now that footprint-aware placement lets
            # large (upgraded) structures shadow multiple grid slots.
            # _find_structure_spot returning None is the authoritative gate;
            # _maybe_relocate_stuck_project handles the case where a project
            # starts here anyway and then can't actually complete.
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
        def _brief(did):
            p = c["districtProjects"][did]
            lead = p.get("lead")
            return f"{p['name']} in {did}" + (f" (lead: {lead})" if lead else "")
        return "; ".join(_brief(did) for did in actives)

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

    def _structure_distribution_by_district(self):
        """Per-district structure type counts, computed fresh from
        civilization["structures"] -- no caching needed at this scale. Used to
        give the sage review a sense of what's already built where."""
        counts = {}
        for s in self.civilization["structures"]:
            did = s.get("districtId")
            if not did or s.get("isRuin"):
                continue
            counts.setdefault(did, {}).setdefault(s.get("type"), 0)
            counts[did][s.get("type")] += 1
        return counts

    def _sage_review_geo_context(self):
        """Compact village-wide geography/resource summary for the sage
        review nudge: per buildable district, stock levels and what's already
        standing there."""
        c = self.civilization
        distribution = self._structure_distribution_by_district()
        parts = []
        for did, d in c["districts"].items():
            if not d.get("build_grid"):
                continue
            stocks = c.get("districtStocks", {}).get(did) or {} if ECOLOGY_ENABLED else {}
            low = [rid for rid, val in stocks.items() if val <= 0 or val < self._stock_max(rid) * STOCK_LOW_RATIO]
            built = distribution.get(did) or {}
            built_str = ", ".join(f"{t}x{n}" for t, n in sorted(built.items())) or "nothing built"
            shortage_str = f"short on {', '.join(sorted(low))}" if low else "stocks fine"
            parts.append(f"{did} ({d.get('kind')}): {built_str}; {shortage_str}")
        return "; ".join(parts) if parts else "no district data"

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
        return any(pid not in c["builtTypes"]
                   and not self._is_project_type_deferred(pid)[0]
                   and not self._type_tier_locked(pid)[0]
                   for pid in self._custom_project_ids())

    def _seed_project_from_stockpile(self, district_id, project, agent=None):
        """Transfer matching stockpile materials into a newly started project.
        Phase F (I4): a rationing rule caps how much of an EDIBLE resource
        this can pull out per call while village storage is low -- the
        deterministic "stockpile withdrawal" the plan's rationing kind
        governs. Non-edible materials (wood/stone/etc.) are never rationed."""
        c = self.civilization
        needs = project.get("needs") or {}
        contributed = project.setdefault("contributed", {})
        seeds = []
        capped_note = None
        for res, need in needs.items():
            short = need - contributed.get(res, 0)
            if short <= 0:
                continue
            available = int(c["stockpile"].get(res, 0))
            if available <= 0:
                continue
            take = min(short, available)
            if LIFECYCLE_ENABLED and res in EDIBLE_RESOURCES:
                take, reason = self._rationing_gate(agent, res, take)
                if reason and take < min(short, available):
                    capped_note = reason
            if take <= 0:
                continue
            c["stockpile"][res] = available - take
            contributed[res] = contributed.get(res, 0) + take
            seeds.append((take, res))
        if seeds:
            parts = ", ".join(f"{amt} {res}" for amt, res in seeds)
            self._push_activity(
                f"The village stockpile supplied {parts} toward the {project['name']}")
        if capped_note and agent is not None:
            agent["lastRationingRejection"] = {"reason": capped_note, "frame": self.frameTick}
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
            if self._type_tier_locked(pid)[0]:
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
        self._seed_project_from_stockpile(district_id, c["districtProjects"][district_id], agent=agent)
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
        # Phase G: a practiced builder contributes a bit more efficiently per
        # action -- the "build" skill's mechanical payoff, capped by what the
        # project still needs and what the agent actually holds (never over-
        # contributes past the requirement or below zero resources).
        amount = 1
        if CULTURE_ENABLED:
            amount += self._skill_bonus(agent, "build")
        amount = max(1, min(amount, need - have, agent["resources"].get(res, 0)))
        agent["resources"][res] -= amount
        p["contributed"][res] = have + amount
        agent["lastContributedFrame"] = self.frameTick
        self.civilization["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(self.civilization["districts"][district_id]["kind"])
        self._enforce_resource_tax(agent, res)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "build")
        bonus_note = " (skilled builder)" if amount > 1 else ""
        return f"{agent['name']} contributed {res} x{amount}{bonus_note} to {p['name']} ({district_id})" \
            if amount > 1 else f"{agent['name']} contributed {res} to {p['name']} ({district_id})"

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

    def _structure_type_built(self, type_):
        """True once at least one non-ruin structure of this type is actually
        standing. duplicateOf can name a seed/custom type that's registered
        (or even just another pendingBlueprints id) but has no built instance
        yet -- approve_blueprint's upgrade routing checks this first so it
        never pops a proposal into a doomed "no structure to upgrade" call."""
        return any(s.get("type") == type_ and not s.get("isRuin")
                   for s in self.civilization["structures"])

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

    def _effect_vector_owner_map(self):
        """Map canonical effect vector -> owning id (seed/custom structure type
        or pending blueprint id), so a new proposal can be tagged duplicateOf
        the thing it duplicates instead of just being rejected outright."""
        c = self.civilization
        owners = {}
        for tid, fn in SEED_STRUCTURE_FUNCTIONS.items():
            vec = self._canonical_effect_vector(fn)
            if vec:
                owners.setdefault(vec, tid)
        for pid in c["projectRegistry"]:
            fn = self._get_structure_function(pid)
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    owners.setdefault(vec, pid)
        for bp in c["pendingBlueprints"]:
            fn = bp.get("function")
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    owners.setdefault(vec, bp["id"])
        return owners

    def _known_effect_vectors(self):
        return set(self._effect_vector_owner_map())

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
        cart (query-time vehicle effect, like _gather_yield_bonus). Phase D:
        the wagon (the cart's tier-2 upgrade) grants the larger bonus."""
        if TECH_TREE_ENABLED and agent["resources"].get("wagon", 0) > 0:
            return COLLECT_CAP + WAGON_CARRY_BONUS
        if GOODS_ENABLED and agent["resources"].get("cart", 0) > 0:
            return COLLECT_CAP + CART_CARRY_BONUS
        return COLLECT_CAP

    # --- Phase D query-time helpers (TECH_TREE_ENABLED) ---
    def _vehicle_speed_mult(self, agent):
        """Movement speed multiplier for vehicle holders (query-time, applied
        in _move_agent). Only the wagon moves faster; the cart only carries."""
        if TECH_TREE_ENABLED and agent["resources"].get("wagon", 0) > 0:
            return WAGON_SPEED_MULT
        return 1.0

    def _type_tier(self, type_):
        """Tech tier of a structure type: live registry entry first, then the
        seed template (covers registries restored from pre-Phase-D saves,
        whose entries carry no tier field), else 1."""
        c = self.civilization
        tier = (c["projectRegistry"].get(type_) or {}).get("tier")
        if tier is None:
            tier = (PROJECT_TEMPLATES.get(type_) or {}).get("tier")
        return tier if isinstance(tier, int) and tier >= 1 else 1

    def _village_tech_tier(self):
        """Highest tier unlocked by a built, WORKING station structure
        (floor 1). The workshop's craft unlock is tier 1; the Forge's is
        tier 2; blueprints may declare higher unlock tiers (bounded by
        validate_blueprint's escape rule: unlock tier <= blueprint tier + 1)."""
        if not TECH_TREE_ENABLED:
            return MAX_TECH_TIER  # gate disabled: nothing is ever tier-locked
        best = 1
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") != "craft":
                    continue
                t = unlock.get("tier", 1)
                if isinstance(t, int) and t > best and self._working_structure_count(type_id) > 0:
                    best = t
        return best

    def _tier_gate_reason(self, tier):
        """Human-readable refusal for tier-locked tech, always naming the
        deterministic escape."""
        if tier <= 2:
            return (f"tier {tier} tech needs a tier-{tier} station built first "
                    f"(the Forge unlocks tier 2 and is a normal tier-1 build)")
        return (f"tier {tier} tech needs a tier-{tier} station built first "
                f"(invent a structure whose function unlocks tier {tier} crafting)")

    def _type_tier_locked(self, type_):
        """(locked, reason) for starting a project of this type."""
        if not TECH_TREE_ENABLED:
            return False, None
        tier = self._type_tier(type_)
        if tier <= self._village_tech_tier():
            return False, None
        return True, self._tier_gate_reason(tier)

    def _function_summary(self, fn):
        """Compact one-line summary of a function block, for the elder's
        comparative council prompt and the persisted councilLog records."""
        parts = []
        for p in (fn or {}).get("produces") or []:
            parts.append(f"produces {p.get('amount', 1)} {p.get('resource')}")
        for b in (fn or {}).get("boosts") or []:
            res = "/".join(b.get("resources") or []) if b.get("kind") == "gather" \
                else f"@{b.get('station')}"
            parts.append(f"boosts {b.get('kind')} {res}")
        for u in (fn or {}).get("unlocks") or []:
            t = u.get("tier")
            parts.append(f"unlocks {u.get('station')}" + (f" (tier {t})" if t else ""))
        for s in (fn or {}).get("stores") or []:
            parts.append(f"stores {s.get('capacity')} {s.get('resource')}")
        if (fn or {}).get("houses"):
            parts.append("houses villagers")
        return "; ".join(parts) or "no effect"

    # --- Phase D eras ---
    def _era_capabilities(self):
        caps = set()
        c = self.civilization
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id)
            if (fn.get("unlocks") or []) and self._working_structure_count(type_id) > 0:
                caps.add("crafting")
                break
        if self._village_tech_tier() >= 2:
            caps.add("metallurgy")
        if path1_on("TIER3_CONTENT_ENABLED"):
            if any(s.get("type") == "harbor" and self._working_structure_count("harbor") > 0
                   for s in c["structures"]):
                caps.add("harbor")
            if any(s.get("type") == "mill" and self._working_structure_count("mill") > 0
                   for s in c["structures"]):
                caps.add("mill")
        vehicles = ("cart", "wagon")
        if any(a["resources"].get(v, 0) > 0 for a in self.agents for v in vehicles) \
                or any(c["stockpile"].get(v, 0) > 0 for v in vehicles):
            caps.add("vehicles")
        has_light = any(isinstance((self._get_structure_function(s.get("type")) or {}).get("light"), dict)
                        and self._working_structure_count(s.get("type")) > 0
                        for s in c["structures"])
        if has_light and self._has_ocean_transit():
            caps.add("civilization")
        return caps

    def _current_era_index(self):
        caps = self._era_capabilities()
        idx = 0
        for i, (_, cap) in enumerate(ERA_LADDER):
            if cap is None or cap in caps:
                idx = i
        return idx

    def _current_era_name(self):
        if not TECH_TREE_ENABLED:
            return None
        c = self.civilization
        return c.get("era") or ERA_LADDER[max(0, min(len(ERA_LADDER) - 1,
                                                     c.get("eraIndex") or 0))][0]

    def _maybe_era_transition(self):
        """Tick-gated era check. Monotonic: capabilities only ever advance the
        era (a broken forge doesn't un-name the age). Transitions are logged
        dramatically and benchmarked (`era`)."""
        if not TECH_TREE_ENABLED:
            return
        c = self.civilization
        idx = self._current_era_index()
        stored = c.get("eraIndex") or 0
        if idx > stored or not c.get("era"):
            advanced = idx > stored
            c["eraIndex"] = max(idx, stored)
            c["era"] = ERA_LADDER[c["eraIndex"]][0]
            if advanced:
                self._push_activity(
                    f"A new age dawns — the village enters the {c['era']}!")
                self._log_benchmark("era", c["eraIndex"],
                                    {"era": c["era"],
                                     "tech_tier": self._village_tech_tier()})

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

    def _calendar(self):
        """In-world calendar, a pure function of frameTick (nothing persisted)."""
        return {
            "year": self.frameTick // YEAR_FRAMES + 1,
            "season": self._current_season(),
            "dayOfSeason": (self.frameTick % SEASON_FRAMES) // DAY_FRAMES + 1,
            "daysPerSeason": SEASON_FRAMES // DAY_FRAMES,
            "isNight": self._is_night(),
            "dayFraction": (self.frameTick % DAY_FRAMES) / DAY_FRAMES,
        }

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
                if STRUCTURE_UPGRADES_ENABLED:
                    count = int(self._weighted_working_count(
                        type_id, district if scope == "district" else None))
                else:
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
                if STRUCTURE_UPGRADES_ENABLED:
                    count = int(self._weighted_working_count(
                        type_id, district_id if scope == "district" else None))
                else:
                    count = self._working_structure_count(type_id, district_id if scope == "district" else None)
                every_n = boost.get("every_n", 1)
                max_bonus = boost.get("max_bonus", 1)
                bonus += min(max_bonus, (count // every_n) * boost.get("bonus", 1))
        return bonus

    # --- Phase E: market pricing, priced trade, property (ECONOMY_ENABLED) ---
    def _market_active(self):
        """True while at least one WORKING market unlocks pricing (same
        query-time unlock pattern as craft stations)."""
        if not ECONOMY_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") == "pricing" and self._working_structure_count(type_id) > 0:
                    return True
        return False

    def _resource_price(self, resource_id):
        """Deterministic price in gold, no persisted state. base * a scarcity
        multiplier derived from (a) the average district-stock ratio for this
        resource village-wide (ECOLOGY_ENABLED) and (b) the village stockpile
        depth relative to storage capacity (GOODS_ENABLED) -- either signal
        alone is enough to move price; both compound. Gold itself is priced at
        1 (the medium doesn't price itself)."""
        if resource_id == "gold":
            return 1
        base = BASE_PRICE.get(resource_id, 2)
        scarcity = 1.0  # 1.0 = comfortable stock, 0.0 = fully depleted
        signals = 0
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            ratios = []
            for stocks in self.civilization["districtStocks"].values():
                if resource_id in stocks:
                    max_s = self._stock_max(resource_id)
                    ratios.append(min(1.0, stocks[resource_id] / max_s) if max_s else 1.0)
            if ratios:
                scarcity = min(scarcity, sum(ratios) / len(ratios))
                signals += 1
        if GOODS_ENABLED and resource_id in EDIBLE_RESOURCES:
            cap = self._storage_capacity(resource_id)
            if cap:
                c = self.civilization
                held = c["stockpile"].get(resource_id, 0) + \
                    sum(a["resources"].get(resource_id, 0) for a in self.agents)
                scarcity = min(scarcity, min(1.0, held / cap))
                signals += 1
        if signals == 0:
            scarcity = 1.0
        mult = 1.0 + (1.0 - scarcity) * (PRICE_SCARCITY_MULT - 1.0)
        return max(PRICE_MIN, round(base * mult))

    def _format_prices_for_prompt(self):
        """One compact prompt line, rendered only while a market exists (flag-
        off / no-market prompts are unaffected)."""
        if not self._market_active():
            return None
        ids = sorted(self.civilization["resourceRegistry"].keys())
        parts = [f"{rid} {self._resource_price(rid)}g" for rid in ids if rid != "gold"]
        return ", ".join(parts) if parts else None

    def _relationship_between(self, agent, other_name):
        return agent["relationships"].get(other_name, "neutral")

    def _priced_trade_terms(self, seller, buyer_name, resource_id):
        """Returns (unit_price, refused, refusal_reason). Relationship
        modifiers apply from the SELLER's perspective (their opinion of the
        buyer): ally = discount, rival = surcharge, and a rival trade is
        refused outright if the buyer can't afford even the surcharged price
        -- never for any other reason, so barter/other partners/waiting for
        price to move all remain reachable."""
        price = self._resource_price(resource_id)
        rel = self._relationship_between(seller, buyer_name)
        if rel == "ally":
            price = max(PRICE_MIN, round(price * ALLY_PRICE_DISCOUNT))
        elif rel == "rival":
            price = max(PRICE_MIN, round(price * RIVAL_PRICE_SURCHARGE))
        return price, rel

    def _priced_trade(self, agent, target, resource_id):
        """Priced exchange (market active): target buys 1 unit of resource_id
        from agent at the relationship-adjusted price, in gold. Refusals are
        NEVER silent: every one sets lastTradeRejection (read by the next
        prompt) and logs an in-world activity line. Deterministic escapes:
        a rival refusal doesn't touch either agent's inventory (both keep
        everything they came with -- gather more gold, wait for price to
        move, or approach a different, non-rival partner); an ally/neutral
        trade the buyer can't afford falls back to the barter swap (never
        blocked just because gold is short)."""
        price, rel = self._priced_trade_terms(agent, target["name"], resource_id)
        buyer_gold = target["resources"].get("gold", 0)
        if rel == "rival" and buyer_gold < price:
            reason = (f"{target['name']} can't afford {agent['name']}'s rival surcharge "
                      f"for {resource_id} ({price}g, has {buyer_gold}g)")
            agent["lastTradeRejection"] = {"reason": reason, "frame": self.frameTick}
            self._push_activity(f"{agent['name']} refused to trade with his rival {target['name']}")
            return f"{agent['name']} refused to trade with rival {target['name']}"
        if buyer_gold < price:
            # Ally/neutral, gold short: barter fallback (the deterministic
            # escape -- a thin gold supply never blocks trade outright).
            agent["resources"][resource_id] -= 1
            target["resources"][resource_id] = target["resources"].get(resource_id, 0) + 1
            self._nudge_ally(agent, target["name"])
            self._nudge_ally(target, agent["name"])
            self._push_memory(target, f"Received {resource_id} from {agent['name']} (bartered, short on gold)")
            agent["lastTradeRejection"] = None
            return f"{agent['name']} bartered {resource_id} to {target['name']} (short on gold)"
        agent["resources"][resource_id] -= 1
        target["resources"][resource_id] = target["resources"].get(resource_id, 0) + 1
        target["resources"]["gold"] = buyer_gold - price
        agent["resources"]["gold"] = agent["resources"].get("gold", 0) + price
        self._nudge_ally(agent, target["name"])
        self._nudge_ally(target, agent["name"])
        self._push_memory(target, f"Bought {resource_id} from {agent['name']} for {price}g")
        agent["lastTradeRejection"] = None
        term = f" ({rel} price)" if rel != "neutral" else ""
        self._push_activity(
            f"{target['name']} bought {resource_id} from {agent['name']} for {price}g{term}")
        return f"{agent['name']} sold {resource_id} to {target['name']} for {price}g"

    def _find_house_to_claim(self, agent):
        """The nearest WORKING, unclaimed house -- built houses first-come."""
        c = self.civilization
        candidates = [s for s in c["structures"]
                      if (self._get_structure_function(s.get("type")) or {}).get("houses")
                      and not s.get("isRuin") and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                      and not s.get("homeOf")]
        if not candidates:
            return None
        return min(candidates, key=lambda s: _dist(agent["x"], agent["y"], s["x"], s["y"]))

    def _claim_home(self, agent, structure):
        """First-come home claim (called on build/repair-from-ruin, and by an
        explicit claim). Releases any previous home the agent held (an agent
        can hold only one home at a time) and logs it. `homeOf`/`prevHomeOf`
        are inheritance breadcrumbs only -- Phase F consumes them."""
        old_id = agent.get("homeStructureId")
        if old_id and old_id != structure["id"]:
            prev = next((s for s in self.civilization["structures"] if s["id"] == old_id), None)
            if prev and prev.get("homeOf") == agent["name"]:
                prev["homeOf"] = None
        structure["homeOf"] = agent["name"]
        agent["homeStructureId"] = structure["id"]
        agent["lastHomelessNudgeFrame"] = None
        name = structure.get("name") or structure.get("type")
        self._push_activity(f"{agent['name']} claimed the {name} in {structure.get('districtId')} as home")

    def _maybe_auto_claim_home(self, agent, structure):
        """Called right after a house is built or rebuilt from ruin: the
        builder/repairer claims it first-come if they're homeless. Doesn't
        force a claim on someone who already has a home -- leaves the new
        house open for the next homeless villager (_find_house_to_claim /
        the homeless nudge)."""
        if not ECONOMY_ENABLED:
            return
        if (self._get_structure_function(structure.get("type")) or {}).get("houses") \
                and not agent.get("homeStructureId"):
            self._claim_home(agent, structure)

    def _agent_wealth(self, agent):
        """gold + goods valued at current prices (0 signal when no market
        exists -- goods are worth nothing tradeable yet, matching barter-only
        reality)."""
        gold = agent["resources"].get("gold", 0)
        if not self._market_active():
            return gold
        value = gold
        for rid, amt in agent["resources"].items():
            if rid == "gold" or amt <= 0:
                continue
            value += amt * self._resource_price(rid)
        return value

    def _wealth_gini(self):
        """Standard Gini coefficient over per-agent wealth (gold + priced
        goods). 0 = perfect equality, ~1 = maximal inequality. None when there
        are no agents (never during a live session)."""
        if not self.agents:
            return None
        values = sorted(self._agent_wealth(a) for a in self.agents)
        n = len(values)
        total = sum(values)
        if total <= 0:
            return 0.0
        cum = sum((i + 1) * v for i, v in enumerate(values))
        return round((2 * cum) / (n * total) - (n + 1) / n, 3)

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
                        if STRUCTURE_UPGRADES_ENABLED:
                            count = self._weighted_working_count(type_id, did)
                        else:
                            count = self._working_structure_count(type_id, did)
                        if count <= 0:
                            continue
                        total = int(amount_each * count)
                        if total < 1:
                            continue
                        self._deposit_produced(resource, total, type_id, did)
                        self._effect_period_fired += 1
                else:
                    if STRUCTURE_UPGRADES_ENABLED:
                        count = self._weighted_working_count(type_id)
                    else:
                        count = self._working_structure_count(type_id)
                    if count <= 0:
                        continue
                    total = int(amount_each * count)
                    if total < 1:
                        continue
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
        self._tick_comfort_consumption()

    def _tick_comfort_consumption(self):
        if not ECONOMY_SINKS_ENABLED:
            return
        if (self.frameTick // GOODS_TICK_FRAMES) % COMFORT_EVERY_N_GOODS_TICKS != 0:
            return
        stock = self.civilization["stockpile"]
        consumed = 0
        for agent in self._living_agents():
            resource = next((r for r in ("pottery", "dried_fish") if stock.get(r, 0) > 0), None)
            if not resource:
                break
            stock[resource] -= 1
            agent["hunger"] = min(100, agent.get("hunger", 0) + 2)
            agent["health"] = min(100, agent.get("health", 0) + 1)
            consumed += 1
        if consumed:
            self._push_activity(f"Village comforts consumed: {consumed} crafted goods")

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
                if ECONOMY_ENABLED and s.get("homeOf"):
                    owner = self._find_agent(s["homeOf"])
                    if owner and owner.get("homeStructureId") == s["id"]:
                        owner["homeStructureId"] = None
                    self._push_activity(f"{s['homeOf']} is left homeless — the {name} they lived in is a ruin")
                    s["homeOf"] = None

    def _tick_structure_health_benchmark(self):
        """Logs a `structure_health` benchmark every goods tick so village-wide
        structural collapse (like the 2026-07 incident where 54/66 structures
        silently rotted into ruins with zero automated visibility) shows up in
        benchmarks.jsonl automatically during any soak/test run, instead of
        requiring an ad-hoc /state query to discover after the fact."""
        if not GOODS_ENABLED:
            return
        structures = self.civilization["structures"]
        total = len(structures)
        if total == 0:
            return
        working = 0
        disrepaired = 0
        ruined = 0
        for s in structures:
            if s.get("isRuin"):
                ruined += 1
                continue
            cond = s.get("condition", 100)
            if cond >= STRUCTURE_DISREPAIR_THRESHOLD:
                working += 1
            elif cond > 0:
                disrepaired += 1
        self._log_benchmark(
            "structure_health",
            round(working / total, 2),
            {"total": total, "working": working, "disrepaired": disrepaired, "ruined": ruined},
        )

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

    def _env_shelter_capacity(self):
        """ENV_EFFECTS_ENABLED: sum of `shelter.capacity` across every working
        structure whose function declares a shelter effect. Stacks with the
        implicit `houses` beds (a block declaring both counts both)."""
        c = self.civilization
        total = 0
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            shelter = fn.get("shelter")
            if not isinstance(shelter, dict):
                continue
            cap = shelter.get("capacity")
            if not isinstance(cap, int) or cap <= 0:
                continue
            count = sum(1 for s in c["structures"]
                        if s["type"] == type_id
                        and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)
            total += cap * count
        return total

    def _tick_shelter(self):
        """Nightly (every DAY_FRAMES): each working house shelters
        HOUSE_SHELTER_OCCUPANTS villagers (nearest to a house first);
        everyone else spends the night outside and loses a little hunger --
        a surfaced nudge, never a hard punishment (floored at
        SHELTER_HUNGER_FLOOR, above the starvation band). This is what makes
        houses consumed nightly instead of just population math.
        Phase E (ECONOMY_ENABLED): a homeowner is guaranteed a bed in THEIR
        OWN house regardless of proximity -- property has to mean something
        mechanically, not just log a claim message. Remaining beds (any house
        minus its live-in owner's reserved bed) go to the homeless, nearest
        first, exactly as before."""
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
        living = self._living_agents()
        slots = len(house_structs) * HOUSE_SHELTER_OCCUPANTS
        if ENV_EFFECTS_ENABLED:
            slots += self._env_shelter_capacity()
        # Corpses stay in self.agents for burial; only the living need beds.
        if slots >= len(living):
            self._push_activity("Night falls -- every villager has a roof tonight")
            return

        sheltered_names = set()
        remaining_slots = slots
        if ECONOMY_ENABLED:
            owned_ids = {s["id"] for s in house_structs if s.get("homeOf")}
            for a in living:
                if a.get("homeStructureId") in owned_ids:
                    sheltered_names.add(a["name"])
            remaining_slots = max(0, slots - len(sheltered_names))

        def dist_to_house(a):
            if not house_structs:
                return float("inf")
            return min(_dist(a["x"], a["y"], s["x"], s["y"]) for s in house_structs)

        others = [a for a in living if a["name"] not in sheltered_names]
        sheltered_names.update(a["name"] for a in
                               sorted(others, key=dist_to_house)[:remaining_slots])
        unsheltered = [a for a in living if a["name"] not in sheltered_names]
        penalized = 0
        for a in unsheltered:
            if a["incapacitated"] or a["hunger"] <= SHELTER_HUNGER_FLOOR:
                continue
            a["hunger"] = max(SHELTER_HUNGER_FLOOR, a["hunger"] - SHELTER_HUNGER_PENALTY)
            a["lastShelterNote"] = {
                "reason": (f"you spent the night outside -- {len(house_structs)} working "
                           f"house(s) shelter only {slots} of {len(living)} villagers"),
                "frame": self.frameTick,
            }
            if ECONOMY_ENABLED and not a.get("homeStructureId") \
                    and (self.frameTick - (a.get("lastHomelessNudgeFrame") or -HOMELESS_NUDGE_FRAMES)) \
                    >= HOMELESS_NUDGE_FRAMES:
                a["lastHomelessNudgeFrame"] = self.frameTick
            penalized += 1
        if penalized:
            self._push_activity(
                f"Night falls -- {penalized} villager(s) had no shelter "
                f"({len(house_structs)} working houses, {slots} beds for {len(living)})")

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
        if ECONOMY_SINKS_ENABLED and c["stockpile"].get("planks", 0) > 0:
            return {"planks": 1}
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
            self._maybe_auto_claim_home(agent, s)
        else:
            s["condition"] = min(100.0, s.get("condition", 100.0) + REPAIR_CONDITION_RESTORE)
            summary = (f"{agent['name']} repaired the {name} in {s.get('districtId')} "
                       f"(condition {int(s['condition'])})")
        agent["lastRepairRejection"] = None
        self._log_benchmark("structure_repaired", int(s["condition"]),
                            {"structure": s.get("type"), "ruin_rebuild": was_ruin})
        return summary

    # --- Structure upgrades (STRUCTURE_UPGRADES_ENABLED) ---
    def _structure_level(self, structure):
        if not STRUCTURE_UPGRADES_ENABLED:
            return MAX_STRUCTURE_LEVEL
        return int(structure.get("level") or 1)

    def _visual_tier_index(self, level):
        idx = 0
        for i, thresh in enumerate(UPGRADE_TIERS):
            if level >= thresh:
                idx = i
        return idx

    def _type_has_unmaxed_instance(self, type_):
        if not STRUCTURE_UPGRADES_ENABLED:
            return False
        return any(
            s.get("type") == type_
            and not s.get("isRuin")
            and self._structure_level(s) < MAX_STRUCTURE_LEVEL
            for s in self.civilization["structures"]
        )

    def _upgradeable_structures_brief(self):
        if not STRUCTURE_UPGRADES_ENABLED:
            return []
        out = []
        for s in self.civilization["structures"]:
            if s.get("isRuin"):
                continue
            lvl = self._structure_level(s)
            if lvl < MAX_STRUCTURE_LEVEL:
                out.append({
                    "id": s.get("id"),
                    "type": s.get("type"),
                    "name": s.get("name") or s.get("type"),
                    "level": lvl,
                    "district": s.get("districtId"),
                })
        return out

    def _find_upgrade_target(self, agent, target):
        pool = [s for s in self.civilization["structures"]
                if not s.get("isRuin")
                and self._structure_level(s) < MAX_STRUCTURE_LEVEL
                and (not GOODS_ENABLED or s.get("condition", 100) > 0)]
        if not pool:
            return None
        if target:
            t = str(target).strip().lower()
            matches = [s for s in pool
                       if str(s.get("id")) == t
                       or (s.get("type") or "").lower() == t
                       or (s.get("name") or "").lower() == t]
            if matches:
                return min(matches, key=lambda s: self._structure_level(s))
        local = [s for s in pool if s.get("districtId") == agent.get("currentDistrict")]
        pool2 = local or pool
        return min(pool2, key=lambda s: self._structure_level(s))

    def _upgrade_cost(self, structure):
        level = self._structure_level(structure)
        tmpl = (self.civilization["projectRegistry"].get(structure.get("type"))
                or PROJECT_TEMPLATES.get(structure.get("type")) or {})
        needs = tmpl.get("needs") or {"wood": 2}
        primary = next(iter(needs), "wood")
        amt = max(1, UPGRADE_COST_BASE * max(1, level // UPGRADE_STAT_STEP))
        return {primary: amt}

    def _sprite_dimensions(self, sprite):
        if not sprite or not isinstance(sprite.get("grid"), list) or not sprite["grid"]:
            return 0, 0
        grid = sprite["grid"]
        return len(grid), max(len(str(r)) for r in grid)

    def _structure_footprint(self, s):
        """Drawn footprint in world px, mirroring the client (sprites.js
        getStructureRenderSize/getStructureGrid/upgradedSeedGrid): take the
        max rows/cols across every candidate source (conservative -- the
        client's exact path can vary by sprite/degenerate-check state), then
        scale by STRUCTURE_PX_SCALE and renderScale."""
        candidates = []
        sprite = s.get("sprite")
        is_degenerate = self.d.get("sprite_spec_is_degenerate", lambda sp: False)
        if sprite and not is_degenerate(sprite):
            rows, cols = self._sprite_dimensions(sprite)
            if rows and cols:
                candidates.append((rows, cols))
        type_id = s.get("type")
        if type_id in SEED_SPRITE_DIMS:
            seed_rows, seed_cols = SEED_SPRITE_DIMS[type_id]
            factor = min(max(1, int(s.get("visualTier") or 1)), 3)
            candidates.append((seed_rows * factor, seed_cols * factor))
        if not candidates:
            candidates.append(PROC_SPRITE_DIMS)
        rows = max(c[0] for c in candidates)
        cols = max(c[1] for c in candidates)
        render_scale = float(s.get("renderScale") or 1.0)
        w = cols * STRUCTURE_PX_SCALE * render_scale
        h = rows * STRUCTURE_PX_SCALE * render_scale
        return w, h

    def _structure_rect(self, s):
        w, h = self._structure_footprint(s)
        return s.get("x", 0), s.get("y", 0), w, h

    def _footprint_rects_collide(self, a, b):
        """AABB overlap test for two (x, y, w, h) rects, inflated by the
        structure gap constants. Distinct from the module-level
        `_rects_overlap` (x1/y1/x2/y2 dict form used for district bounds)."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return (ax < bx + bw + STRUCTURE_GAP_X and bx < ax + aw + STRUCTURE_GAP_X
                and ay < by + bh + STRUCTURE_GAP_Y and by < ay + ah + STRUCTURE_GAP_Y)

    def _structures_overlapping(self, a, b):
        return self._footprint_rects_collide(self._structure_rect(a), self._structure_rect(b))

    def _farm_plot_tier_sprite(self, structure, tier_idx, palette):
        rows = min(14, 6 + tier_idx * 2)
        cols = min(14, 8 + tier_idx * 2)
        key = f"{structure.get('id')}|{tier_idx}"
        h = sum(ord(c) * (i + 1) for i, c in enumerate(key)) & 0xFFFFFFFF
        grid = []
        for y in range(rows):
            chars = []
            for x in range(cols):
                if x in (0, cols - 1) or y in (0, rows - 1):
                    chars.append(".")
                elif (y // 2) % 2 == 0:
                    chars.append("a" if (x + y + h) % 4 else "b")
                else:
                    chars.append("c" if (x + y) % 3 == 0 else "b")
            grid.append("".join(chars))
        return {"palette": palette[:5], "grid": grid}

    def _procedural_tier_sprite(self, structure, tier_idx):
        """Deterministic bigger pixel grid for a visual tier (no LLM required)."""
        type_id = structure.get("type") or ""
        seed_palette = SEED_UPGRADE_PALETTES.get(type_id)
        if type_id == "farm_plot" and seed_palette:
            return self._farm_plot_tier_sprite(structure, tier_idx, seed_palette)
        palettes = [
            ["#8B5A2B", "#C62828", "#F5E6C8"], ["#78909C", "#37474F", "#FFD54F"],
            ["#A1887F", "#4E342E", "#AED581"], ["#90A4AE", "#B71C1C", "#E3F2FD"],
        ]
        key = f"{structure.get('type')}|{structure.get('id')}|{tier_idx}"
        h = sum(ord(c) * (i + 1) for i, c in enumerate(key)) & 0xFFFFFFFF
        palette = seed_palette if seed_palette else palettes[h % len(palettes)]
        rows = min(14, 6 + tier_idx * 2)
        cols = min(14, 6 + tier_idx * 2)
        grid = []
        for y in range(rows):
            chars = []
            for x in range(cols):
                if y < max(1, rows // 3):
                    ch = "b" if (x + y + h) % 3 else "a"
                elif y > rows * 2 // 3 and cols // 3 <= x <= cols * 2 // 3:
                    ch = "c"
                elif (x + y + h) % 5 == 0:
                    ch = "c"
                else:
                    ch = "a"
                chars.append(ch)
            grid.append("".join(chars))
        return {"palette": palette, "grid": grid}

    def _expand_sprite_to_tier(self, structure, tier_idx):
        current = structure.get("sprite")
        if current and current.get("grid"):
            rows, cols = self._sprite_dimensions(current)
            target_rows = min(14, max(rows + 2, rows + tier_idx * 2))
            target_cols = min(14, max(cols + 2, cols + tier_idx * 2))
            palette = list(current.get("palette") or ["#8B5A2B", "#C62828", "#F5E6C8"])[:5]
            old_grid = [str(r) for r in current["grid"]]
            new_grid = []
            for y in range(target_rows):
                if y < len(old_grid):
                    row = old_grid[y]
                    row = row + "a" * max(0, target_cols - len(row))
                    row = row[:target_cols]
                else:
                    row = "a" * target_cols
                new_grid.append(row)
            return {"palette": palette, "grid": new_grid}
        return self._procedural_tier_sprite(structure, tier_idx)

    def _apply_visual_tier(self, structure, new_tier_idx):
        structure["visualTier"] = new_tier_idx + 1
        structure["renderScale"] = round(1.0 + new_tier_idx * 0.25, 2)
        structure["sprite"] = self._expand_sprite_to_tier(structure, new_tier_idx)
        if new_tier_idx >= len(UPGRADE_TIERS) - 1:
            base_name = structure.get("name") or structure.get("type")
            if "Mega" not in str(base_name):
                structure["name"] = f"Mega {base_name}"

    def _structure_upgrade_weight(self, structure):
        """Effective contribution of a structure to produces/boosts (1-10)."""
        if not STRUCTURE_UPGRADES_ENABLED:
            return 1
        return max(1, self._structure_level(structure) // UPGRADE_STAT_STEP)

    def _weighted_working_count(self, type_id, district_id=None):
        total = 0.0
        for s in self.civilization["structures"]:
            if s.get("type") != type_id:
                continue
            if district_id and s.get("districtId") != district_id:
                continue
            if GOODS_ENABLED and s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                continue
            if s.get("isRuin"):
                continue
            total += self._structure_upgrade_weight(s)
        return total

    def _pay_upgrade_cost(self, agent, cost, name):
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
            agent["lastUpgradeRejection"] = {
                "reason": (f"upgrading {name} needs {cost_str} -- you and the stockpile "
                           f"together lack {', '.join(missing)}"),
                "frame": self.frameTick,
            }
            return False
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
                f"{agent['name']}'s upgrade of the {name}")
        return True

    def _upgrade_structure(self, agent, target):
        s = self._find_upgrade_target(agent, target)
        if not s:
            agent["lastUpgradeRejection"] = {
                "reason": "no upgradeable structure found", "frame": self.frameTick}
            return f"{agent['name']} found no structure to upgrade"
        level = self._structure_level(s)
        if level >= MAX_STRUCTURE_LEVEL:
            agent["lastUpgradeRejection"] = {
                "reason": f"{s.get('name')} is already at max level",
                "frame": self.frameTick,
            }
            return f"{agent['name']} cannot upgrade {s.get('name')} further"
        cost = self._upgrade_cost(s)
        name = s.get("name") or s.get("type")
        if not self._pay_upgrade_cost(agent, cost, name):
            return f"{agent['name']} lacks resources to upgrade the {name}"
        old_tier = self._visual_tier_index(level)
        new_level = min(MAX_STRUCTURE_LEVEL, level + LEVEL_STEP)
        s["level"] = new_level
        new_tier = self._visual_tier_index(new_level)
        if new_tier > old_tier:
            self._apply_visual_tier(s, new_tier)
            rows, cols = self._sprite_dimensions(s.get("sprite"))
            agent["spriteDesignTurn"] = {
                "structureId": s["id"],
                "tier": new_tier,
                "minRows": rows,
                "minCols": cols,
                "structureName": name,
                "structureType": s.get("type"),
            }
            # The upgrade may have grown this structure's footprint enough to
            # overlap a neighbor; the upgrader becomes the relocator.
            self._enqueue_reorg_for_overlaps(s, preferred_agent=agent)
        agent["lastUpgradeRejection"] = None
        self._push_activity(f"{agent['name']} upgraded the {name} to level {new_level}")
        self._log_benchmark("structure_upgraded", new_level,
                            {"structure": s.get("type"), "id": s["id"]})
        return f"{agent['name']} upgraded {name} to level {new_level}"

    def _apply_structure_sprite(self, agent, sprite):
        turn = agent.get("spriteDesignTurn") or {}
        sid = turn.get("structureId")
        s = next((x for x in self.civilization["structures"] if x.get("id") == sid), None)
        if not s:
            agent["spriteDesignTurn"] = None
            return f"{agent['name']} had no pending sprite design"
        validate = self.d.get("validate_sprite_block")
        min_rows = int(turn.get("minRows") or 0)
        min_cols = int(turn.get("minCols") or 0)
        if validate:
            ok, reason = validate(sprite, min_rows=min_rows, min_cols=min_cols)
        else:
            ok, reason = True, None
        if not ok:
            agent["lastSpriteRejection"] = {"reason": reason, "frame": self.frameTick}
            return f"{agent['name']}'s sprite design was rejected ({reason})"
        if self.d.get("sprite_spec_is_degenerate", lambda sp: False)(sprite):
            agent["lastSpriteRejection"] = {
                "reason": "sprite is too flat (use varied colors/pattern, not one solid fill)",
                "frame": self.frameTick,
            }
            return f"{agent['name']}'s sprite design was rejected (too flat)"
        s["sprite"] = sprite
        agent["spriteDesignTurn"] = None
        agent["lastSpriteRejection"] = None
        name = s.get("name") or s.get("type")
        self._push_activity(f"{agent['name']} refined the sprite for the {name}")
        # An agent-refined sprite can also grow the drawn footprint enough to
        # overlap a neighbor -- same reorg trigger as a tier upgrade.
        self._enqueue_reorg_for_overlaps(s, preferred_agent=agent)
        return f"{agent['name']} applied a new larger sprite to the {name}"

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
        if LIFECYCLE_ENABLED:
            # Phase F: once every AGENT_DEFS name is in use (all 12 named
            # slots occupied by long-lived villagers), housing headroom can
            # still exist -- births then use a generated villager (see
            # _next_agent_slot) instead of stalling at the fixed roster size.
            # Without this, _population_cap topping out at len(AGENT_DEFS)
            # would make birth impossible the moment the named roster fills,
            # even with houses to spare.
            return cap
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
            headroom = len(AGENT_DEFS)
            if LIFECYCLE_ENABLED:
                # _population_cap() is uncapped past len(AGENT_DEFS) under
                # this flag (generated villagers can be born once every named
                # slot is full) -- without matching headroom here, the house
                # soft cap would flag "enough houses" before there's actually
                # room for the next birth, throttling population growth for
                # no mechanical reason. Current agent count is the simplest
                # lower bound that tracks any already-realized growth.
                headroom = max(headroom, len(self.agents) + HOUSES_PER_NEW_VILLAGER)
            return count >= (headroom - base) * every_n + 3
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

    def _find_structure_spot(self, district_id, footprint=None, ignore_id=None):
        d = self.civilization["districts"].get(district_id)
        grid = d.get("build_grid") if d else None
        if not grid:
            b = d["bounds"] if d else {"x1": 0, "y1": 0}
            return {"x": b["x1"], "y": b["y1"]}
        bounds = d["bounds"]
        fw, fh = footprint if footprint else (8 * STRUCTURE_PX_SCALE, 8 * STRUCTURE_PX_SCALE)
        # Big footprints can shadow slots across district edges, so check
        # every existing structure civilization-wide, not just this district.
        existing = [s for s in self.civilization["structures"] if s.get("id") != ignore_id]
        existing_rects = [self._structure_rect(s) for s in existing]
        cap = grid.get("cap", 30)
        for i in range(cap):
            x = grid["x0"] + (i % grid["cols"]) * grid["dx"]
            y = grid["y0"] + (i // grid["cols"]) * grid["dy"]
            if x < bounds["x1"] or y < bounds["y1"]:
                continue
            if x + fw > bounds["x2"] or y + fh + 14 > bounds["y2"]:
                continue
            candidate = (x, y, fw, fh)
            if any(self._footprint_rects_collide(candidate, r) for r in existing_rects):
                continue
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
        struct_type = project["type"]
        footprint = self._structure_footprint({
            "type": struct_type, "sprite": project.get("sprite"),
            "visualTier": 1, "renderScale": 1.0,
        })
        spot = self._find_structure_spot(district_id, footprint=footprint)
        if not spot:
            return f"{agent['name']} finds {district_id} has no room left to build"
        new_structure = {
            "id": c["nextStructureId"], "type": struct_type,
            "x": spot["x"], "y": spot["y"],
            "visualStyle": project.get("visualStyle") or "generic",
            "sprite": project.get("sprite"),
            "name": project["name"], "districtId": district_id,
            # Phase C decay stat; every read uses .get(default 100) so
            # structures from pre-Phase-C saves need no migration.
            "condition": 100.0, "isRuin": False,
            # Phase E property: None until claimed (see _maybe_auto_claim_home).
            "homeOf": None,
            "level": 1, "visualTier": 1, "renderScale": 1.0,
        }
        c["structures"].append(new_structure)
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
        self._maybe_auto_claim_home(agent, new_structure)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "build")
        return f"{agent['name']} built {built_name} in {district_id}"

    def _perform_gather(self, agent, resource):
        """Ecology-aware gather with structure boosts. Returns summary string."""
        c = self.civilization
        if (TRANSIT_ENABLED and self._gather_zone_for_resource(resource) == "ocean"
                and not self._has_ocean_transit()):
            return f"{agent['name']} needs a working ocean transit structure to gather {resource}"
        if LIFECYCLE_ENABLED:
            # Governance (I4): a harvest_quota rule binds before the ecology
            # gate -- this is a policy refusal, not a depletion one, so it
            # deliberately does NOT trigger _scarcity_reflex_on_depletion
            # (there's nothing to terraform/relocate away from; the escape
            # is waiting out the period, a different resource, or a district
            # move, all surfaced in the reason text).
            quota_ok, quota_reason = self._harvest_quota_gate(agent, resource)
            if not quota_ok:
                agent["lastQuotaRejection"] = {"reason": quota_reason, "frame": self.frameTick}
                return f"{agent['name']} found nothing — {quota_reason}"
            agent["lastQuotaRejection"] = None
        tool_ok, tool_reason = self._can_gather_resource(agent, resource)
        if not tool_ok:
            if RESOURCE_MIN_TOOL.get(resource) == "wooden_pick" and path1_on("TERRAIN_TILES_ENABLED"):
                # Bootstrap: a pickless stone gather becomes a dig instead of
                # failing, so a fresh world can reach its first Workshop/pick.
                return self._dig_terrain(agent)
            agent["lastGatherRejection"] = {"reason": tool_reason, "frame": self.frameTick}
            self._path1_tool_benchmark(resource, False)
            return f"{agent['name']} found nothing — {tool_reason}"
        allowed, reason, scale = self._ecology_gather_gate(agent, resource)
        if not allowed:
            agent["lastGatherRejection"] = {"reason": reason, "frame": self.frameTick}
            self._scarcity_reflex_on_depletion(agent, resource)
            return f"{agent['name']} found nothing — {reason}"
        amount = 1
        if STRUCTURE_EFFECTS_ENABLED:
            amount += self._gather_yield_bonus(agent, resource)
        if path1_on("TOOL_TIERS_ENABLED") and RESOURCE_MIN_TOOL.get(resource):
            if self._gather_tool_tier(agent) >= TOOL_TIER_LEVEL[RESOURCE_MIN_TOOL[resource]]:
                amount += TOOL_YIELD_BONUS
        if CULTURE_ENABLED:
            amount += self._skill_bonus(agent, "gather")
        if ECOLOGY_ENABLED and scale < 1.0:
            amount = max(1, int(amount * scale))
        if ECOLOGY_ENABLED and path1_on("TERRAIN_TILES_ENABLED"):
            did = agent.get("currentDistrict")
            if did:
                grove_mult = 0.5 + self._terrain_grove_ratio(did)
                amount = max(1, int(amount * grove_mult))
        amount = max(1, min(amount, self._carry_cap(agent) - agent["resources"].get(resource, 0)))
        agent["resources"][resource] = agent["resources"].get(resource, 0) + amount
        c["collectSuccesses"] += 1
        self._path1_tool_benchmark(resource, True)
        if ECOLOGY_ENABLED:
            did = agent.get("currentDistrict")
            if did:
                self._deplete_district_stock(
                    did, resource, amount * STOCK_DEPLETE_MULTIPLIER)
        if LIFECYCLE_ENABLED:
            self._record_harvest_quota_use(agent, resource, amount)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "gather")
        agent["lastGatherRejection"] = None
        bonus_note = ""
        if amount > 1:
            bonus_note = " (structure effects boosted the harvest)"
        return f"{agent['name']} collected {resource}" + (f" x{amount}{bonus_note}" if amount > 1 else "")

    # --- Path 1: tool tiers ---
    def _gather_tool_tier(self, agent):
        if not path1_on("TOOL_TIERS_ENABLED"):
            return 0
        best = 0
        for tool in TOOL_TIER_ORDER:
            if agent["resources"].get(tool, 0) > 0:
                best = max(best, TOOL_TIER_LEVEL[tool])
        return best

    def _can_gather_resource(self, agent, resource):
        if not path1_on("TOOL_TIERS_ENABLED"):
            return True, None
        needed = RESOURCE_MIN_TOOL.get(resource)
        if not needed:
            return True, None
        have = self._gather_tool_tier(agent)
        need_lvl = TOOL_TIER_LEVEL[needed]
        if have < need_lvl:
            return False, f"{resource} needs a {needed} (you have tier {have} tools)"
        return True, None

    # --- Path 1: composable tiles ---
    def _district_at_pos(self, agent):
        did = agent.get("currentDistrict")
        if did and did in self.civilization["districts"]:
            return did, self.civilization["districts"][did]
        return None, None

    def _pos_to_grid(self, agent):
        did, d = self._district_at_pos(agent)
        if not d:
            return None, None, None, None
        b = d["bounds"]
        gx = int((agent["x"] - b["x1"]) // TILE_CELL)
        gy = int((agent["y"] - b["y1"]) // TILE_CELL)
        gx = max(0, min(PATH1_GRID_COLS - 1, gx))
        gy = max(0, min(PATH1_GRID_ROWS - 1, gy))
        return did, d, gx, gy

    def _tile_key(self, gx, gy):
        return f"{gx},{gy}"

    def _find_nearby_terrain(self, district, kind, from_gx, from_gy):
        """Nearest cell of `kind` in district['terrain'] to (from_gx, from_gy),
        by grid distance. Grid is fixed-size (PATH1_GRID_COLS x ROWS), so a
        full scan is cheap. Returns (gx, gy) or None if no match exists."""
        best = None
        best_dist = None
        for key, value in district.get("terrain", {}).items():
            if value != kind:
                continue
            gx_s, gy_s = key.split(",")
            gx, gy = int(gx_s), int(gy_s)
            if gx == from_gx and gy == from_gy:
                continue
            dist = (gx - from_gx) ** 2 + (gy - from_gy) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = (gx, gy), dist
        return best

    def _nearest_diggable_district(self, exclude_district_id, agent=None):
        """The district, other than the given one, that actually has a soil
        tile to dig right now — nearest to the agent by district-center
        distance when an agent is given (so eight stone-seekers don't all
        funnel down the same road to the same field), else the first match."""
        best = None
        best_dist = None
        for did, d in self.civilization["districts"].items():
            if did == exclude_district_id or d.get("kind") in NON_DIGGABLE_DISTRICT_KINDS:
                continue
            self._ensure_district_terrain(d)
            if "soil" not in d["terrain"].values():
                continue
            if agent is None:
                return did
            b = d["bounds"]
            cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
            dist = (cx - agent["x"]) ** 2 + (cy - agent["y"]) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = did, dist
        return best

    def _pickless_stone_route(self, agent, resource):
        """Feasibility-aware routing for a pickless stone-seeker: dig right
        here if the ground allows, else head to the nearest diggable
        district. Returns a summary string, or None when normal zone routing
        (to the cave) is correct — i.e. the agent has the pick, or the
        resource isn't gated on one. Without this, agents get routed to the
        cave (stone's nominal gather zone), find no soil there, get bounced
        to a farm by the dig-relocate backstop, and commute forever."""
        if not (path1_on("TOOL_TIERS_ENABLED") and path1_on("TERRAIN_TILES_ENABLED")):
            return None
        if RESOURCE_MIN_TOOL.get(resource) != "wooden_pick":
            return None
        tool_ok, _ = self._can_gather_resource(agent, resource)
        if tool_ok:
            return None
        did, d = self._district_at_pos(agent)
        if did and d.get("kind") not in NON_DIGGABLE_DISTRICT_KINDS:
            return self._dig_terrain(agent)
        dest = self._nearest_diggable_district(did, agent)
        if not dest:
            return None
        self._set_agent_target_once(agent, dest)
        if USE_GOALS:
            agent["goal"] = {"kind": "dig_relocate", "target_district": dest,
                             "ttl": STALL_THRESHOLD * 2}
        return f"{agent['name']} heads to {dest} to find diggable ground"

    def _ensure_district_tiles(self, district):
        district.setdefault("tiles", {})

    def _ensure_district_terrain(self, district):
        if "terrain" not in district:
            kind = district.get("kind", "village")
            default = {"forest": "grove", "farm": "soil", "beach": "sand",
                       "cave": "rock", "ocean": "water"}.get(kind, "soil")
            district["terrain"] = {}
            for gx in range(PATH1_GRID_COLS):
                for gy in range(PATH1_GRID_ROWS):
                    district["terrain"][self._tile_key(gx, gy)] = default

    def _place_block(self, agent, block_type, gx=None, gy=None):
        if not path1_on("COMPOSABLE_BUILD_ENABLED"):
            return f"{agent['name']} cannot place blocks — composable build is disabled"
        bt = BLOCK_TYPES.get(block_type or "")
        if not bt:
            agent["lastBlockRejection"] = {"reason": f"unknown block type {block_type}",
                                           "frame": self.frameTick}
            return f"{agent['name']} cannot place unknown block {block_type}"
        did, d, agx, agy = self._pos_to_grid(agent)
        if not did:
            agent["lastBlockRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot place blocks outside a district"
        gx = int(gx) if gx is not None else agx
        gy = int(gy) if gy is not None else agy
        self._ensure_district_tiles(d)
        tiles = d["tiles"]
        if len(tiles) >= TILE_CAP_PER_DISTRICT:
            agent["lastBlockRejection"] = {"reason": "district tile cap reached", "frame": self.frameTick}
            return f"{agent['name']} cannot place — district is at tile cap"
        key = self._tile_key(gx, gy)
        if key in tiles:
            agent["lastBlockRejection"] = {"reason": "tile already occupied", "frame": self.frameTick}
            return f"{agent['name']} cannot place — tile already has {tiles[key]}"
        for res, n in bt["cost"].items():
            if agent["resources"].get(res, 0) < n:
                agent["lastBlockRejection"] = {"reason": f"need {n} {res}", "frame": self.frameTick}
                return f"{agent['name']} lacks {res} to place {block_type}"
        for res, n in bt["cost"].items():
            agent["resources"][res] -= n
        tiles[key] = block_type
        agent["lastBlockRejection"] = None
        c = self.civilization
        c["path1Placements"] = c.get("path1Placements", 0) + 1
        self._log_benchmark("composable_placements", c["path1Placements"],
                            {"block": block_type, "district": did})
        self._push_activity(f"{agent['name']} placed {block_type} at {did} ({gx},{gy})")
        return f"{agent['name']} placed {block_type}"

    def _remove_block(self, agent, gx=None, gy=None):
        if not path1_on("COMPOSABLE_BUILD_ENABLED"):
            return f"{agent['name']} cannot remove blocks"
        did, d, agx, agy = self._pos_to_grid(agent)
        if not did:
            return f"{agent['name']} cannot remove blocks outside a district"
        gx = int(gx) if gx is not None else agx
        gy = int(gy) if gy is not None else agy
        self._ensure_district_tiles(d)
        key = self._tile_key(gx, gy)
        block_type = d["tiles"].pop(key, None)
        if not block_type:
            agent["lastBlockRejection"] = {"reason": "no block here", "frame": self.frameTick}
            return f"{agent['name']} found no block to remove"
        bt = BLOCK_TYPES.get(block_type, {})
        for res, n in bt.get("cost", {}).items():
            refund = max(0, int(n * BLOCK_REFUND_RATIO)) or 1
            agent["resources"][res] = agent["resources"].get(res, 0) + refund
        return f"{agent['name']} removed {block_type}"

    def _composable_shelter_count(self):
        if not path1_on("COMPOSABLE_BUILD_ENABLED"):
            return 0
        count = 0
        for d in self.civilization["districts"].values():
            tiles = d.get("tiles") or {}
            walls = sum(1 for t in tiles.values() if t in ("wall", "fence"))
            has_door = any(t == "door" for t in tiles.values())
            if walls >= 8 and has_door:
                count += 1
        return count

    # --- Path 1: terrain mutation ---
    def _dig_terrain(self, agent):
        if not path1_on("TERRAIN_TILES_ENABLED"):
            return f"{agent['name']} cannot dig — terrain tiles disabled"
        did, d, gx, gy = self._pos_to_grid(agent)
        if not did:
            agent["lastTerrainRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot dig outside a district"
        # Digging is deliberately tool-free: it is the bootstrap stone source
        # for a fresh world (stone gathers are pick-gated, the pick needs a
        # Workshop, and the Workshop needs stone).
        self._ensure_district_terrain(d)
        key = self._tile_key(gx, gy)
        current = d["terrain"].get(key, "soil")
        gained = None
        if current == "grove":
            d["terrain"][key] = "soil"
        elif current == "soil":
            d["terrain"][key] = "rock"
            gained = "stone"
        else:
            # This tile is exhausted (rock/sand/water) -- relocate to the
            # nearest fresh soil tile instead of failing forever on the same
            # spot. The walk is the action this turn; the next dig call
            # (LLM or goal-driven) lands on diggable ground.
            nearby = self._find_nearby_terrain(d, "soil", gx, gy)
            if nearby:
                ngx, ngy = nearby
                b = d["bounds"]
                agent["targetX"] = b["x1"] + (ngx + 0.5) * TILE_CELL
                agent["targetY"] = b["y1"] + (ngy + 0.5) * TILE_CELL
                agent["waypoints"] = []
                agent["lastTerrainRejection"] = None
                return f"{agent['name']} moves to fresh ground to keep digging"
            # No soil anywhere in this district -- some district kinds never
            # have any (cave defaults its whole grid to "rock", beach to
            # "sand", ocean to "water"; see _ensure_district_terrain). A
            # same-district relocate can't help there, so route to a
            # different district of a soil-bearing kind instead of leaving
            # the agent (e.g. a miner in a cave) stuck forever.
            dest = self._nearest_diggable_district(did, agent)
            if dest:
                self._set_agent_target_once(agent, dest)
                if USE_GOALS:
                    # Persistent goal: while it's set, the think tick steps
                    # this deterministically instead of dispatching an LLM
                    # think, so the agent's role reflexes can't reverse the
                    # trip mid-transit (a miner would otherwise bounce back
                    # to the cave every think cycle and never arrive).
                    agent["goal"] = {"kind": "dig_relocate", "target_district": dest,
                                     "ttl": STALL_THRESHOLD * 2}
                agent["lastTerrainRejection"] = None
                return f"{agent['name']} heads to {dest} to find diggable ground"
            agent["lastTerrainRejection"] = {
                "reason": f"no diggable ground left in {did} — try another district",
                "frame": self.frameTick,
            }
            return f"{agent['name']} cannot dig {current} here"
        if gained:
            cap = self._carry_cap(agent)
            if agent["resources"].get(gained, 0) < cap:
                agent["resources"][gained] = agent["resources"].get(gained, 0) + 1
        c = self.civilization
        c["path1TerrainMutations"] = c.get("path1TerrainMutations", 0) + 1
        self._log_benchmark("terrain_mutations", c["path1TerrainMutations"], {"action": "dig", "district": did})
        agent["lastTerrainRejection"] = None
        self._push_activity(f"{agent['name']} dug terrain at {did} ({gx},{gy})")
        return f"{agent['name']} dug terrain" + (f" and found {gained}" if gained else "")

    def _plant_terrain(self, agent):
        if not path1_on("TERRAIN_TILES_ENABLED"):
            return f"{agent['name']} cannot plant — terrain tiles disabled"
        did, d, gx, gy = self._pos_to_grid(agent)
        if not did:
            agent["lastTerrainRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot plant outside a district"
        if agent["resources"].get("wood", 0) < 1:
            agent["lastTerrainRejection"] = {"reason": "need 1 wood", "frame": self.frameTick}
            return f"{agent['name']} needs wood to plant"
        self._ensure_district_terrain(d)
        key = self._tile_key(gx, gy)
        current = d["terrain"].get(key, "soil")
        if current not in ("soil", "rock"):
            agent["lastTerrainRejection"] = {"reason": f"cannot plant on {current}", "frame": self.frameTick}
            return f"{agent['name']} cannot plant on {current}"
        agent["resources"]["wood"] -= 1
        d["terrain"][key] = "grove"
        c = self.civilization
        c["path1TerrainMutations"] = c.get("path1TerrainMutations", 0) + 1
        self._log_benchmark("terrain_mutations", c["path1TerrainMutations"], {"action": "plant", "district": did})
        agent["lastTerrainRejection"] = None
        return f"{agent['name']} planted a grove"

    def _terrain_grove_ratio(self, district_id):
        d = self.civilization["districts"].get(district_id) or {}
        terrain = d.get("terrain") or {}
        if not terrain:
            return 0.5
        groves = sum(1 for t in terrain.values() if t == "grove")
        return groves / max(1, len(terrain))

    def _maybe_expand_field(self, agent):
        if not path1_on("TERRAIN_TILES_ENABLED"):
            return
        did = agent.get("currentDistrict")
        if not did:
            return
        d = self.civilization["districts"].get(did)
        if not d or d.get("kind") != "farm":
            return
        if self._terrain_grove_ratio(did) > 0.3:
            return
        if agent.get("goal"):
            return
        agent["goal"] = {"kind": "plant_terrain", "ttl": STALL_THRESHOLD * 2}

    # --- Path 1: diplomacy ---
    def _init_settlements(self):
        c = self.civilization
        if c.get("settlements"):
            return
        home_districts = list(c["districts"].keys())
        c["settlements"] = [{"id": "home", "name": "Home Village", "districts": home_districts}]
        for did in home_districts:
            c["districts"][did].setdefault("settlementId", "home")
        c.setdefault("treaties", [])
        c.setdefault("caravanLog", [])

    def _maybe_found_settlement(self):
        if not path1_on("PATH1_DIPLOMACY_ENABLED"):
            return
        c = self.civilization
        self._init_settlements()
        if len(c["settlements"]) >= 2:
            return
        living = len(self._living_agents())
        structures = len([s for s in c["structures"] if not s.get("isRuin")])
        if structures < SETTLEMENT_STRUCT_THRESHOLD or living < SETTLEMENT_POP_THRESHOLD:
            return
        plot = self._claim_frontier_plot()
        if not plot:
            return
        self._found_district("village", DISTRICT_KIND_TEMPLATES["village"], plot)
        new_did = plot.get("claimedBy")
        if not new_did:
            return
        sid = "outpost"
        c["settlements"].append({"id": sid, "name": "Frontier Outpost", "districts": [new_did]})
        c["districts"][new_did]["settlementId"] = sid
        self._push_activity("A second settlement is founded — the Frontier Outpost!")
        self._log_benchmark("settlement_founded", len(c["settlements"]), {"id": sid})

    def _settlement_for_agent(self, agent):
        did = agent.get("currentDistrict")
        if did:
            return self.civilization["districts"].get(did, {}).get("settlementId", "home")
        return "home"

    def _border_settlement_agent(self, agent):
        if not path1_on("PATH1_DIPLOMACY_ENABLED"):
            return False
        self._init_settlements()
        settlements = {s["id"] for s in self.civilization["settlements"]}
        if len(settlements) < 2:
            return False
        sid = self._settlement_for_agent(agent)
        for other in self.agents:
            if other["name"] == agent["name"] or other.get("deathFrame"):
                continue
            if self._distance_to(agent, other) > 150:
                continue
            if self._settlement_for_agent(other) != sid:
                return True
        return False

    def _maybe_caravan_goal(self, agent):
        if not path1_on("PATH1_DIPLOMACY_ENABLED"):
            return
        carry = self._carry_cap(agent)
        has_vehicle = any(agent["resources"].get(v, 0) > 0 for v in ("cart", "wagon"))
        if not has_vehicle or sum(agent["resources"].values()) < CARAVAN_CARRY_MIN:
            return
        c = self.civilization
        self._init_settlements()
        if len(c["settlements"]) < 2:
            return
        my_sid = self._settlement_for_agent(agent)
        other = next((s for s in c["settlements"] if s["id"] != my_sid), None)
        if not other or not other["districts"]:
            return
        dest = other["districts"][0]
        if agent.get("currentDistrict") == dest:
            if TRANSIT_ENABLED and self._has_ocean_transit():
                if not self._consume_ocean_transit():
                    return
            c["caravanLog"].append({"agent": agent["name"], "settlement": other["id"],
                                    "frame": self.frameTick})
            self._log_benchmark("inter_village_trades", len(c["caravanLog"]),
                                {"agent": agent["name"], "dest": dest})
            self._push_activity(f"{agent['name']} arrives at {other['name']} with trade goods")
            return
        if not agent.get("goal"):
            agent["goal"] = {"kind": "caravan", "target_district": dest, "ttl": STALL_THRESHOLD * 4}

    def _ocean_transit_unlocks(self):
        if not TRANSIT_ENABLED:
            return []
        out = []
        for s in self.civilization["structures"]:
            if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                continue
            for unlock in (self._get_structure_function(s.get("type")) or {}).get("unlocks") or []:
                if unlock.get("kind") == "transit" and unlock.get("terrain") == "ocean":
                    out.append(unlock)
        return out

    def _has_ocean_transit(self):
        return bool(self._ocean_transit_unlocks())

    def _consume_ocean_transit(self):
        unlock = self._ocean_transit_unlocks()[0] if self._ocean_transit_unlocks() else None
        if not unlock:
            return False
        costs = unlock.get("consumes") or {}
        stock = self.civilization["stockpile"]
        if any(stock.get(r, 0) < n for r, n in costs.items()):
            self._push_activity("Ocean caravan waits for transit supplies")
            return False
        for resource, amount in costs.items():
            stock[resource] -= amount
        self._push_activity("An ocean caravan launches, consuming " + ", ".join(f"{n} {r}" for r, n in costs.items()))
        return True

    def _propose_treaty(self, agent, decision):
        if not path1_on("PATH1_DIPLOMACY_ENABLED"):
            return f"{agent['name']} cannot propose treaties"
        rule = decision.get("rule") or {}
        if not isinstance(rule, dict) or not rule.get("id") or not rule.get("name"):
            agent["lastTreatyRejection"] = {"reason": "invalid treaty proposal", "frame": self.frameTick}
            return f"{agent['name']} drafted an invalid treaty"
        entry = {
            "id": rule["id"], "name": rule["name"], "kind": "treaty",
            "value": rule.get("value") or "trade",
            "description": rule.get("description", "Inter-settlement treaty"),
            "proposedBy": agent["name"], "enacted": False,
            "votes": {agent["name"]: "yes"},
        }
        self.civilization["pendingRules"].append(entry)
        self._tally_and_maybe_enact(entry)
        agent["lastTreatyRejection"] = None
        return f'{agent["name"]} proposed treaty "{entry["name"]}"'

    def _vote_treaty(self, agent, decision):
        if not path1_on("PATH1_DIPLOMACY_ENABLED"):
            return f"{agent['name']} cannot vote on treaties"
        target = decision.get("target")
        vote = (decision.get("vote") or "yes").lower()
        pending = next((r for r in self.civilization["pendingRules"]
                        if r["id"] == target and r.get("kind") == "treaty"), None)
        if not pending:
            agent["lastTreatyRejection"] = {"reason": "no such treaty pending", "frame": self.frameTick}
            return f"{agent['name']} found no treaty {target} to vote on"
        pending["votes"][agent["name"]] = vote
        self._tally_and_maybe_enact(pending)
        if pending.get("enacted"):
            self.civilization.setdefault("treaties", []).append({
                "id": pending["id"], "name": pending["name"], "value": pending["value"],
                "frame": self.frameTick,
            })
        return f'{agent["name"]} voted {vote} on treaty "{pending["name"]}"'

    # --- Path 1: pressure loop ---
    def _is_night(self):
        if not path1_on("PRESSURE_LOOP_ENABLED"):
            return False
        phase = self.frameTick % DAY_FRAMES
        return phase >= int(DAY_FRAMES * (1 - NIGHT_FRACTION))

    def _pay_upkeep(self, structures, resource, total_needed):
        """All-or-nothing: pay total_needed of resource, district stock (per
        structure's own district) first, then the village stockpile. Returns
        True if paid in full, False (no state change) if unaffordable."""
        c = self.civilization
        remaining = total_needed
        district_pulls = []
        seen_districts = []
        for s in structures:
            did = s.get("districtId")
            if did and did not in seen_districts:
                seen_districts.append(did)
        for did in seen_districts:
            if remaining <= 0:
                break
            avail = self._district_stock(did, resource)
            if avail is None:
                continue
            take = min(avail, remaining)
            if take > 0:
                district_pulls.append((did, take))
                remaining -= take
        stockpile_avail = int(c["stockpile"].get(resource, 0))
        if remaining > stockpile_avail:
            return False
        for did, amt in district_pulls:
            self._add_district_stock(did, resource, -amt)
        if remaining > 0:
            c["stockpile"][resource] = stockpile_avail - remaining
        return True

    def _tick_env_upkeep(self):
        """ENV_EFFECTS_ENABLED: at the first night-pressure tick of each day
        (frameTick // DAY_FRAMES changes), each working structure type
        declaring an `upkeep` effect consumes amount * count of its resource.
        Unaffordable types go unfueled for the night (their `light` effect,
        if any, is inactive); tracked per type in
        civilization["upkeepLastDay"]."""
        if not ENV_EFFECTS_ENABLED:
            return
        c = self.civilization
        day = self.frameTick // DAY_FRAMES
        last_day = c.setdefault("upkeepLastDay", {})
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            upkeep = fn.get("upkeep")
            if not isinstance(upkeep, dict):
                continue
            entry = last_day.get(type_id)
            if entry and entry.get("day") == day:
                continue
            working = [s for s in c["structures"]
                       if s["type"] == type_id
                       and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD]
            if not working:
                last_day[type_id] = {"day": day, "fueled": False}
                continue
            res = upkeep.get("resource")
            amount = upkeep.get("amount", 1)
            needed = amount * len(working)
            fueled = self._pay_upkeep(working, res, needed)
            last_day[type_id] = {"day": day, "fueled": fueled}
            if fueled:
                name = self._structure_display_name(type_id)
                self._push_activity(f"The {name} burns {needed} {res} through the night")

    def _env_lit_types(self):
        """ENV_EFFECTS_ENABLED: structure type ids whose function declares a
        `light` effect and are currently fueled (charged this day's upkeep)."""
        c = self.civilization
        last_day = c.get("upkeepLastDay", {})
        lit_types = set()
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            if not isinstance(fn.get("light"), dict):
                continue
            entry = last_day.get(type_id)
            if entry and entry.get("fueled"):
                lit_types.add(type_id)
        return lit_types

    def _env_lit_districts(self):
        """ENV_EFFECTS_ENABLED: district ids containing a working AND fueled
        `light` structure right now."""
        c = self.civilization
        lit_types = self._env_lit_types()
        lit = set()
        for s in c["structures"]:
            if (s["type"] in lit_types
                    and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                    and s.get("districtId")):
                lit.add(s["districtId"])
        return lit

    def _tick_night_pressure(self):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not SURVIVAL_ENABLED:
            return
        if not self._is_night():
            return
        c = self.civilization
        if ENV_EFFECTS_ENABLED:
            self._tick_env_upkeep()
        house_slots = len([s for s in c["structures"]
                           if (self._get_structure_function(s.get("type")) or {}).get("houses")
                           and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD])
        house_slots *= HOUSE_SHELTER_OCCUPANTS
        house_slots += self._composable_shelter_count() * HOUSE_SHELTER_OCCUPANTS
        if ENV_EFFECTS_ENABLED:
            house_slots += self._env_shelter_capacity()
        lit_districts = self._env_lit_districts() if ENV_EFFECTS_ENABLED else set()
        c["litDistricts"] = sorted(lit_districts)
        living = self._living_agents()
        sheltered = set()
        if house_slots >= len(living):
            c["nightSheltered"] = len(living)
            c["nightTotal"] = len(living)
            return
        for a in living:
            if a.get("homeStructureId"):
                sheltered.add(a["name"])
        others = [a for a in living if a["name"] not in sheltered]
        sheltered.update(a["name"] for a in others[:max(0, house_slots - len(sheltered))])
        exposed = 0
        lit_spared = 0
        for a in living:
            if a["name"] in sheltered or a["incapacitated"]:
                continue
            if a.get("currentDistrict") in lit_districts:
                lit_spared += 1
                continue
            if a["health"] > 10:
                a["health"] = max(10, a["health"] - NIGHT_EXPOSURE_DAMAGE)
                a["lastNightNote"] = {"reason": "exposed to the night cold", "frame": self.frameTick}
                exposed += 1
        c["nightSheltered"] = len(sheltered)
        c["nightTotal"] = len(living)
        rate = len(sheltered) / max(1, len(living))
        benchmark_payload = {"sheltered": len(sheltered), "total": len(living)}
        if lit_spared:
            benchmark_payload["lit"] = lit_spared
        self._log_benchmark("night_shelter_rate", round(rate, 2), benchmark_payload)
        if exposed:
            self._push_activity(f"Night exposure — {exposed} villager(s) took cold damage")

    def _tick_wildlife(self):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not SURVIVAL_ENABLED:
            return
        if random.random() > WILDLIFE_EVENT_PROB:
            return
        forest_agents = [a for a in self._living_agents()
                         if not a["incapacitated"]
                         and self.civilization["districts"].get(a.get("currentDistrict"), {}).get("kind") == "forest"]
        if not forest_agents:
            return
        victim = random.choice(forest_agents)
        guarded = any(self._distance_to(victim, g) <= WILDLIFE_GUARD_RADIUS
                      for g in self._living_agents()
                      if g["name"] != victim["name"] and g.get("role") == "guard"
                      and not g["incapacitated"])
        if guarded:
            self._push_activity(f"Wildlife stirs near {victim['name']} but guards keep it at bay")
            return
        victim["health"] = max(5, victim["health"] - 5)
        victim["lastNightNote"] = {"reason": "startled by wildlife", "frame": self.frameTick}
        self._push_activity(f"Wildlife attacks {victim['name']} in the forest!")

    def _maybe_seek_shelter(self, agent):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not self._is_night():
            return
        if agent.get("homeStructureId") or agent.get("goal"):
            return
        village_district = next((did for did, d in self.civilization["districts"].items()
                                 if d.get("kind") == "village"), None)
        if village_district and agent.get("currentDistrict") != village_district:
            agent["goal"] = {"kind": "seek_shelter", "target_district": village_district,
                             "ttl": STALL_THRESHOLD}

    def _path1_industry_benchmark(self):
        if not path1_on("INDUSTRY_ENABLED"):
            return
        depth = len([r for r in self.RECIPES if r not in ("planks", "bricks", "tools", "cart", "wagon")])
        self._log_benchmark("industry_recipe_depth", depth, {"recipes": depth})

    def _path1_tool_benchmark(self, resource, success):
        if not path1_on("TOOL_TIERS_ENABLED"):
            return
        c = self.civilization
        key = "tool_gather_ok" if success else "tool_gather_fail"
        c[key] = c.get(key, 0) + 1
        total = c.get("tool_gather_ok", 0) + c.get("tool_gather_fail", 0)
        if total > 0:
            self._log_benchmark("tool_tier_gather_ratio",
                                round(c.get("tool_gather_ok", 0) / total, 2))

    def _project_resource_list(self, project):
        return " and ".join(project["needs"].keys())

    def _belief_project_score(self, agent, project_id):
        """Match an available project to belief tenets and affinity vectors."""
        if not agent or not agent.get("beliefs"):
            return 0
        tmpl = self.civilization["projectRegistry"].get(project_id) or {}
        haystack = " ".join([project_id, str(tmpl.get("name") or "")]
                              + list((tmpl.get("needs") or {}).keys())).lower()
        score = 0
        for belief_id in agent["beliefs"]:
            entry = self._belief_entry(belief_id)
            words = {w for w in re.findall(r"[a-z]{3,}", str(entry.get("tenet") or "").lower())}
            score += sum(1 for word in words if word in haystack)
            affinity = set(entry.get("affinity") or ())
            if "priority" in affinity and self._active_priority_resource() in (tmpl.get("needs") or {}):
                score += 2
            if "resource_tax" in affinity and project_id in ("granary", "market", "house"):
                score += 1
            if "custom" in affinity and tmpl.get("custom"):
                score += 2
        return score

    def _role_default_project(self, role, agent=None):
        pref = self.d["ROLE_PROJECT"].get((role or "").lower(), "house")
        prefs = pref if isinstance(pref, list) else [pref]
        prefs = prefs or ["house"]
        open_prefs = [p for p in prefs if not self._type_saturated(p)
                      and not self._is_project_type_deferred(p)[0]
                      and not self._type_tier_locked(p)[0]
                      and not self._type_has_unmaxed_instance(p)]
        if open_prefs:
            return max(open_prefs, key=lambda project_id: self._belief_project_score(agent, project_id))
        # Every preferred type is saturated: fall back to any unsaturated
        # registry type (this is what steers the default loop toward the
        # granary and approved customs once the basics are overbuilt).
        fallback = [tid for tid in self.civilization["projectRegistry"]
                    if not self._type_saturated(tid)
                    and not self._is_project_type_deferred(tid)[0]
                    and not self._type_tier_locked(tid)[0]
                    and not self._type_has_unmaxed_instance(tid)]
        if fallback:
            return max(fallback, key=lambda project_id: self._belief_project_score(agent, project_id))
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
        # Phase D: a tier-locked seed (the granary before the Forge exists)
        # can't be started, so it must not hold the invention gate shut --
        # same reasoning as the deferred clause above.
        if self._type_tier_locked(tid)[0]:
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
        type_ = target if explicit else self._role_default_project(agent["role"], agent)
        if not explicit:
            # Bias the default (role-based) pick toward an approved-but-
            # unbuilt custom project of the same kind, before any seed
            # repeat -- this is what makes invention pay off even before
            # it's strictly required.
            preferred_kind = PROJECT_KIND.get(type_, "village")
            biased = next((pid for pid in self._custom_project_ids()
                           if pid not in c["builtTypes"]
                           and not self._is_project_type_deferred(pid)[0]
                           and not self._type_tier_locked(pid)[0]
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
        locked, lock_reason = self._type_tier_locked(type_)
        if locked:
            name = tmpl.get("name", type_)
            agent["lastProjectRejection"] = {
                "reason": f"the {name} is tier-locked: {lock_reason}",
                "frame": self.frameTick,
            }
            self._log_benchmark("tier_gate_rejection", self._type_tier(type_),
                                {"kind": "project", "target": type_,
                                 "village_tier": self._village_tech_tier()})
            return f"{agent['name']} cannot start {name} — {lock_reason}"
        if self._invention_required() and not tmpl.get("custom"):
            name = tmpl.get("name", type_)
            agent["lastProjectRejection"] = {
                "reason": f"blocked by invention gate — the village needs a NEW invention for {name}",
                "frame": self.frameTick,
            }
            agent["inventionTurn"] = True
            agent["inventionBuildContext"] = {"type": type_, "typeName": name, "district": target_district}
            return (f"{agent['name']} wants to build {name}, but the village needs a NEW invention "
                    f"(propose_blueprint) — {agent['name']} will draft one")
        if STRUCTURE_UPGRADES_ENABLED and self._type_has_unmaxed_instance(type_):
            unmaxed = [s for s in c["structures"]
                       if s.get("type") == type_
                       and not s.get("isRuin")
                       and self._structure_level(s) < MAX_STRUCTURE_LEVEL]
            target_s = min(unmaxed, key=lambda s: self._structure_level(s))
            name = tmpl.get("name", type_)
            agent["lastProjectRejection"] = {
                "reason": (f"a {name} already exists at level {self._structure_level(target_s)} "
                           f"(max {MAX_STRUCTURE_LEVEL}) -- upgrade_structure id {target_s['id']} "
                           f"instead of building another"),
                "frame": self.frameTick,
            }
            return (f"{agent['name']} cannot build another {name} -- upgrade the existing one "
                    f"(id {target_s['id']}, level {self._structure_level(target_s)}) with "
                    f"upgrade_structure first")
        if self._type_saturated(type_):
            # Only suggest an alternative the agent can actually start:
            # deferred types and types with an active duplicate both get
            # deterministically rejected, so naming them here just rams
            # agents into a wall (471 such nudges in the 2026-07-05 soak).
            alt = next((tid for tid in c["projectRegistry"]
                        if not self._type_saturated(tid)
                        and not self._is_project_type_deferred(tid)[0]
                        and not self._type_tier_locked(tid)[0]
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
        project_needs = dict(tmpl["needs"])
        if ECONOMY_SINKS_ENABLED and self._type_tier(type_) >= 2:
            material = next((r for r in ("planks", "bricks", "tools") if r not in project_needs), "planks")
            project_needs[material] = project_needs.get(material, 0) + 1
        contributed = {res: 0 for res in project_needs}
        c["districtProjects"][district_id] = {
            "type": type_, "name": tmpl["name"], "needs": project_needs,
            "contributed": contributed, "visualStyle": tmpl.get("visualStyle") or "generic",
            "sprite": tmpl.get("sprite"),
            "districtId": district_id,
            "lead": agent["name"], "leadReassigned": None,
        }
        self._seed_project_from_stockpile(district_id, c["districtProjects"][district_id], agent=agent)
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
        (covers stale directives restored from state.db too)."""
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
        # Phase F: incapacitated is no longer always transient (a dead agent
        # stays incapacitated forever), so this must exclude it explicitly --
        # otherwise a deceased villager could sit in the elder's idle list
        # indefinitely and get assign_task'd to a corpse every gate.
        idle = [a for a in self.agents if a.get("deathFrame") is None and not a["incapacitated"]
                and self._is_idle(a)
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
        # Feasibility gates run BEFORE any travel: routing an agent to a
        # station district that can't serve them (no Workshop/Kiln built,
        # tier-gated recipe, missing inputs) just produces a useless commute.
        # Workshop-station recipes need a physical Workshop somewhere in the
        # village (structures of type "workshop" are placed in village-kind
        # districts, so this is a village-wide check, not a per-district one).
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop" \
                and not self._craft_station_unlocked("workshop"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Workshop built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe_id in ("charcoal", "copper_ingot", "iron_ingot") \
                and not self._craft_station_unlocked("kiln"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Kiln built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe_id == "iron_pick" \
                and not self._craft_station_unlocked("foundry"):
            agent["lastCraftRejection"] = {"reason": "requires a working Foundry", "frame": self.frameTick}
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Foundry built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe.get("station") == "kiln" \
                and not self._craft_station_unlocked("kiln"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Kiln built yet"
        if path1_on("TIER3_CONTENT_ENABLED") and recipe.get("station") == "foundry" \
                and not self._craft_station_unlocked("foundry"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Foundry built yet"
        if TECH_TREE_ENABLED:
            tier = recipe.get("tier", 1)
            village_tier = self._village_tech_tier()
            if isinstance(tier, int) and tier > village_tier:
                reason = self._tier_gate_reason(tier)
                agent["lastCraftRejection"] = {"reason": reason, "frame": self.frameTick}
                self._log_benchmark("tier_gate_rejection", tier,
                                    {"kind": "craft", "target": recipe_id,
                                     "village_tier": village_tier})
                return (f"{agent['name']} cannot craft {recipe_id} — it is tier {tier} "
                        f"tech and the village is tier {village_tier} ({reason})")
        if not self._has_inputs(agent, recipe["inputs"]):
            self._craft_input_reflex(agent, recipe_id, recipe)
            missing = self._largest_missing_input(agent, recipe["inputs"])
            return f"{agent['name']} lacks {missing} to craft {recipe_id}"
        if recipe.get("station") and agent["currentZone"] != recipe["station"]:
            self._set_agent_target_once(agent, recipe["station"])
            return f"{agent['name']} heads to the {recipe['station']} to craft {recipe_id}"
        for r, n in recipe["inputs"].items():
            agent["resources"][r] -= n
        output = 1
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop":
            output += self._craft_output_bonus(recipe, agent.get("currentDistrict"))
        if CULTURE_ENABLED:
            output += self._skill_bonus(agent, "craft")
        agent["resources"][recipe_id] = agent["resources"].get(recipe_id, 0) + output
        agent["lastCraftRejection"] = None
        self.civilization["lastCraftActivityFrame"] = self.frameTick
        if CULTURE_ENABLED:
            self._practice_skill(agent, "craft")
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
        if TECH_TREE_ENABLED and isinstance(rc, dict):
            # Phase D: recipes may declare a tech tier (default 1). Declaring a
            # tier above the village's station-unlocked tier is refused with a
            # surfaced reason (the escape: build the tier's station first).
            tier = rc.get("tier", 1)
            if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int)
                                     or not (1 <= tier <= MAX_TECH_TIER)):
                reason = f"recipe tier must be an integer 1-{MAX_TECH_TIER}"
                agent["lastRecipeRejection"] = {"reason": reason, "frame": self.frameTick}
                return f"{agent['name']} drafted an invalid recipe ({reason})"
            village_tier = self._village_tech_tier()
            if (tier or 1) > village_tier:
                reason = self._tier_gate_reason(tier)
                agent["lastRecipeRejection"] = {"reason": reason, "frame": self.frameTick}
                self._log_benchmark("tier_gate_rejection", tier,
                                    {"kind": "recipe", "target": rc.get("id"),
                                     "village_tier": village_tier})
                return f"{agent['name']}'s recipe {rc.get('id')} was refused — {reason}"
        if not self._validate_recipe(rc):
            return f"{agent['name']} drafted an invalid recipe"
        agent["lastRecipeRejection"] = None
        c["pendingRecipes"].append({
            "id": rc["id"], "name": rc["name"], "inputs": dict(rc["inputs"]),
            "station": rc.get("station"), "color": rc.get("color", "#BCAAA4"),
            "proposedBy": agent["name"],
            **({"tier": rc.get("tier") or 1} if TECH_TREE_ENABLED else {}),
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
        self.RECIPES[rc["id"]] = {"name": rc["name"], "inputs": dict(rc["inputs"]), "station": rc["station"],
                                  **({"tier": rc.get("tier") or 1} if TECH_TREE_ENABLED else {})}
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
        if kind == "succession":
            # Succession ballots are created deterministically by
            # _start_succession_election on the elder's death, never by an
            # agent's propose_rule call -- keeps the election tamper-proof
            # (no one can nominate themselves mid-arc or spam candidacies).
            return False
        if kind == "resource_tax":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (0 <= v <= 3):
                return False
        if kind == "priority":
            # Sid-parity Phase 2: a priority rule biases contributions toward
            # a named resource. Value must be a known resource id.
            value = rule.get("value")
            if not isinstance(value, str) or value not in c["resourceRegistry"]:
                return False
        if LIFECYCLE_ENABLED and kind == "harvest_quota":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (1 <= v <= 20):
                return False
        if LIFECYCLE_ENABLED and kind == "rationing":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (1 <= v <= RATIONING_WITHDRAW_CAP * 4):
                return False
        return True

    def _record_rule_kind_enacted(self, kind):
        c = self.civilization
        kinds = c.setdefault("ruleKindsEverEnacted", [])
        if kind and kind not in kinds:
            kinds.append(kind)

    def _tally_and_maybe_enact(self, rule):
        c = self.civilization
        votes = list(rule["votes"].values())
        yes = votes.count("yes")
        no = votes.count("no")
        quorum = self._vote_quorum()
        if yes >= quorum:
            rule["enacted"] = True
            c["pendingRules"] = [r for r in c["pendingRules"] if r["id"] != rule["id"]]
            c["lastRuleActivityFrame"] = self.frameTick
            if rule.get("kind") == "repeal":
                return self._enact_repeal(rule, yes)
            if LIFECYCLE_ENABLED and rule["kind"] == "succession":
                # Succession ballots are a leadership record, not an ongoing
                # governance constraint -- they deliberately do NOT join
                # c["rules"] (which has a small MAX_ACTIVE_RULES budget shared
                # with resource_tax/harvest_quota/rationing/priority). Elder
                # deaths recur naturally over a long soak; letting every
                # succession permanently consume that budget would crowd out
                # real governance over time. activity.jsonl + the "succession"
                # benchmark are the permanent record instead.
                self._enact_succession_winner(rule)
            else:
                rule["enactedFrame"] = self.frameTick
                c["rules"].append(rule)
                self._record_rule_kind_enacted(rule.get("kind"))
                self._push_activity(f'Rule "{rule["name"]}" enacted by vote ({yes} yes)')
                self._log_benchmark("rule_enacted", len(c["rules"]), {
                    "id": rule["id"], "yes": yes, "no": no, "kind": rule.get("kind")})
                self._apply_governance_rule(rule)
            return "enacted"
        if no >= quorum:
            c["pendingRules"] = [r for r in c["pendingRules"] if r["id"] != rule["id"]]
            c["lastRuleActivityFrame"] = self.frameTick
            self._push_activity(f'Rule "{rule["name"]}" rejected by vote ({no} no)')
            return "rejected"
        return "pending"

    def _apply_governance_rule(self, rule):
        """Give harvest_quota/rationing/priority mechanical teeth the moment
        they're enacted. Rules stay in effect while in civilization["rules"];
        repeal_rule reverses them. Rationing additionally self-lifts once
        storage recovers (checked at withdrawal time in _rationing_gate)."""
        c = self.civilization
        if rule["kind"] == "harvest_quota":
            try:
                value = int(float(rule.get("value")))
            except (TypeError, ValueError):
                value = HARVEST_QUOTA_PERIOD_FRAMES and 5
            c["harvestQuotas"][rule["id"]] = {"value": max(1, value)}
            self._push_activity(f'Harvest quota "{rule["name"]}" now limits gathers to '
                                f'{max(1, value)} per resource per {HARVEST_QUOTA_PERIOD_FRAMES // 30}s per district')
        elif rule["kind"] == "rationing":
            try:
                value = int(float(rule.get("value")))
            except (TypeError, ValueError):
                value = RATIONING_WITHDRAW_CAP
            c["rationingActive"][rule["id"]] = {"value": max(1, value)}
            self._push_activity(f'Rationing "{rule["name"]}" now caps stockpile withdrawals to '
                                f'{max(1, value)} while storage is low')
        elif rule["kind"] == "priority":
            rid = rule.get("value")
            self._push_activity(
                f'Priority rule "{rule["name"]}" now biases contributions toward {rid}')

    def _clear_governance_rule(self, rule):
        """Reverse _apply_governance_rule side effects on repeal."""
        c = self.civilization
        rid = rule.get("id")
        if not rid:
            return
        c.get("harvestQuotas", {}).pop(rid, None)
        c.get("rationingActive", {}).pop(rid, None)

    def _enact_repeal(self, repeal_ballot, yes_count):
        """Remove the targeted enacted rule after a successful repeal vote."""
        c = self.civilization
        target_id = repeal_ballot.get("targetRuleId") or repeal_ballot.get("value")
        target = next((r for r in c["rules"] if r["id"] == target_id), None)
        if not target:
            self._push_activity(
                f'Repeal of "{target_id}" passed ({yes_count} yes) but the rule was already gone')
            self._log_benchmark("rule_repealed", len(c["rules"]),
                                {"id": target_id, "yes": yes_count, "missing": True})
            return "enacted"
        c["rules"] = [r for r in c["rules"] if r["id"] != target_id]
        self._clear_governance_rule(target)
        self._push_activity(
            f'Rule "{target["name"]}" repealed by vote ({yes_count} yes)')
        self._log_benchmark("rule_repealed", len(c["rules"]),
                            {"id": target_id, "yes": yes_count, "kind": target.get("kind")})
        return "enacted"

    def _propose_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot propose rules"
        rule = decision.get("rule")
        if not self._validate_rule(rule):
            return f"{agent['name']} drafted an invalid rule"
        kind = rule.get("kind") or "custom"
        if kind == "resource_tax":
            value = float(rule["value"])
        else:
            value = rule.get("value")
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

    def _propose_repeal(self, agent, decision):
        """Sid-parity Phase 2: start a repeal ballot for an enacted rule.
        Reuses the vote_rule / _tally_and_maybe_enact quorum scaffold."""
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot repeal rules"
        target_id = decision.get("target")
        if not isinstance(target_id, str) or not target_id:
            return f"{agent['name']} named no rule to repeal"
        target = next((r for r in c["rules"] if r["id"] == target_id), None)
        if not target:
            return f"{agent['name']} found no enacted rule {target_id}"
        if len(c["pendingRules"]) >= MAX_PENDING_RULES:
            return f"{agent['name']} cannot propose a repeal — too many pending votes"
        ballot_id = f"repeal_{target_id}"
        if any(r["id"] == ballot_id for r in c["pendingRules"]):
            return f"{agent['name']} found a repeal of {target_id} already pending"
        if any(r.get("kind") == "repeal" and r.get("targetRuleId") == target_id
               for r in c["pendingRules"]):
            return f"{agent['name']} found a repeal of {target_id} already pending"
        entry = {
            "id": ballot_id,
            "name": f'Repeal {target["name"]}',
            "kind": "repeal",
            "value": target_id,
            "targetRuleId": target_id,
            "description": f'Repeal the enacted rule "{target["name"]}" ({target_id}).',
            "proposedBy": agent["name"],
            "enacted": False,
            "votes": {agent["name"]: "yes"},
        }
        c["pendingRules"].append(entry)
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("rule_proposal", agent["name"], "everyone",
                                 f'{entry["name"]}: {entry["description"]}')
        self._tally_and_maybe_enact(entry)
        return f'{agent["name"]} proposed repealing "{target["name"]}"'

    def _active_priority_resource(self):
        """Return the resource id from the newest enacted priority rule, if any."""
        if not RULES_ENABLED:
            return None
        for rule in reversed(self.civilization["rules"]):
            if rule.get("kind") == "priority" and rule.get("enacted"):
                rid = rule.get("value")
                if isinstance(rid, str) and rid in self.civilization["resourceRegistry"]:
                    return rid
        return None

    def _vote_on_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot vote"
        rule = next((r for r in c["pendingRules"] if r["id"] == decision.get("target")), None)
        if not rule:
            return f"{agent['name']} found no such pending rule"
        vote = "no" if decision.get("vote") == "no" else "yes"
        rule["votes"][agent["name"]] = vote
        if LIFECYCLE_ENABLED and rule["kind"] == "succession" and vote == "yes":
            # An election is N candidate ballots, not N independent yes/no
            # referenda: voting yes for one candidate is implicitly a no for
            # every other candidate in the same election, so a villager's
            # ballot can't count toward two winners at once.
            election_id = (c.get("pendingSuccession") or {}).get("electionId")
            for sibling in c["pendingRules"]:
                if sibling is not rule and sibling["kind"] == "succession" \
                        and sibling.get("electionId") == election_id:
                    sibling["votes"].setdefault(agent["name"], "no")
                    if sibling["votes"][agent["name"]] == "yes":
                        sibling["votes"][agent["name"]] = "no"
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

    # --- Phase F: population lifecycle (aging / birth / death / succession) ---
    def _life_stage(self, agent):
        """One-word life stage for prompt identity (#1). Distinct from the
        elder ROLE (Sage may be young if succession just landed; an aged
        villager who never held the elder role is still labeled 'elder')."""
        age = agent.get("age")
        if age is None:
            return None
        if age < ADULT_AGE:
            return "young"
        if age >= ELDER_AGE:
            return "elder"
        return "adult"

    def _tick_lifecycle(self):
        """Gate for aging + natural death, tick-gated like every other
        _maybe_* backstop. Birth and succession are handled by their own
        gated methods so each has an isolated, independently testable
        forcing path (age-to-death, kill-the-elder, enact-a-quota)."""
        if not LIFECYCLE_ENABLED:
            return
        for a in self.agents:
            if a.get("deathFrame") is not None:
                continue
            a["age"] = (a.get("age") or 0.0) + AGE_YEARS_PER_TICK
        self._maybe_natural_death()
        self._maybe_birth()

    def _living_agents(self):
        """Agents who are not permanently dead. Corpses stay in self.agents for
        burial/memorial/inheritance, but must not consume housing headroom or
        inflate the birth food-surplus threshold — otherwise a village at the
        population floor can never recover once enough names have died."""
        return [a for a in self.agents if a.get("deathFrame") is None]

    def _eligible_adults(self, exclude=None):
        return [a for a in self.agents
                if a.get("deathFrame") is None and not a["incapacitated"]
                and (a.get("age") or 0) >= ADULT_AGE and a is not exclude]

    def _maybe_natural_death(self):
        c = self.civilization
        for agent in list(self.agents):
            if agent.get("deathFrame") is not None or agent["incapacitated"]:
                continue
            age = agent.get("age") or 0.0
            if age < DEATH_CHANCE_START_AGE:
                continue
            # Linear ramp from 0 at DEATH_CHANCE_START_AGE to a saturating
            # multiple of the base roll at MAX_LIFE_EXPECTANCY -- deterministic
            # curve, stochastic roll (matches the plan's "deterministic curve").
            span = max(1.0, MAX_LIFE_EXPECTANCY - DEATH_CHANCE_START_AGE)
            progress = min(1.0, (age - DEATH_CHANCE_START_AGE) / span)
            chance = DEATH_CHANCE_PER_TICK * (1 + progress * 9)
            if random.random() >= chance:
                continue
            # Population floor: dying would drop non-incapacitated adults to
            # or below the floor -- defer (never permanently; re-rolled every
            # gate until either the population grows or this agent is no
            # longer the one keeping it above floor). Logged, not silent.
            living_adults = len(self._eligible_adults())
            if living_adults <= POPULATION_FLOOR:
                if not c.get("populationFloorHeld"):
                    c["populationFloorHeld"] = True
                    self._push_activity(
                        f"{agent['name']} is frail with age, but the village is too small to "
                        f"bear a loss (population at the floor of {POPULATION_FLOOR}) -- death defers.")
                continue
            c["populationFloorHeld"] = False
            self._agent_dies(agent, cause="old age")
            return  # one death per gate keeps the arc easy to follow/test

    def _agent_dies(self, agent, cause="old age"):
        """Natural death (#3): never mid-emergency (Sage-priority logic only
        ever incapacitates, and _sage_emergency short-circuits when no elder
        exists -- see CLAUDE.md), always logged, always followed by
        inheritance + a memorial memory pushed to every living agent. The
        elder's death additionally starts a succession election (#4)."""
        c = self.civilization
        was_elder = agent["role"] == "elder"
        agent["deathFrame"] = self.frameTick
        agent["incapacitated"] = True
        agent["goal"] = None
        agent["assignedTask"] = None
        agent["reorgTask"] = None
        c["deaths"] = c.get("deaths", 0) + 1
        c["lastDeathActivityFrame"] = self.frameTick
        age_txt = f" at age {int(agent.get('age') or 0)}" if agent.get("age") is not None else ""
        self._push_activity(f"{agent['name']} has died of {cause}{age_txt}.")
        self._log_benchmark("death", c["deaths"], {"name": agent["name"], "cause": cause,
                                                     "age": agent.get("age"), "role": agent["role"]})
        memorial = f"{agent['name']} has passed away. The village will remember them."
        for other in self.agents:
            if other is agent or other.get("deathFrame") is not None:
                continue
            self._push_memory(other, memorial, kind="memorial")
        if CULTURE_ENABLED:
            self._push_chronicle(f"{agent['name']} died of {cause}{age_txt}.", kind="death")
            self._store_knowledge_on_death(agent)
            # Bereavement (#4): the closest surviving ally drifts, deterministic
            # template only -- a life event, not an LLM call.
            bereaved = next((o for o in self.agents
                             if o is not agent and o.get("deathFrame") is None
                             and o.get("relationships", {}).get(agent["name"]) == "ally"), None)
            if bereaved:
                self._drift_personality(bereaved, f"grieving the loss of {agent['name']}")
        self._inherit_from(agent)
        if was_elder:
            # Every "find the elder" lookup across the codebase (assign_task,
            # the directive broadcast, _maybe_advance_rules, the invention
            # backstop, ...) is a bare `role == "elder"` scan with no
            # deathFrame check -- rather than audit every call site, demote
            # the deceased elder's own role here so "elder" uniquely
            # identifies the living leader again everywhere, immediately,
            # for the whole (deterministic, bounded) span of the election.
            agent["role"] = "retired_elder"
            self._start_succession_election()

    def _heirs_of(self, agent):
        """Heirs are the deceased's children (parents[] linkage) if any exist
        and are alive; otherwise every living adult shares equally (a village
        this small has no formal family tree yet -- #Phase G territory)."""
        children = [a for a in self.agents
                    if a.get("deathFrame") is None and a.get("parents")
                    and agent["name"] in a["parents"]]
        if children:
            return children
        return self._eligible_adults(exclude=agent) or [a for a in self.agents if a is not agent]

    def _inherit_from(self, agent):
        """Goods/home flow to heirs (#3, Phase E inheritance records finally
        consumed). Beliefs (memes) were already shared in life via proximity/
        talk (#Phase G is full lineage); here we guarantee the deceased's
        beliefs survive them by handing the full set to every heir, which is
        what makes 'someone who never met Sage cites a rule he enacted'
        (Part 4's civilization test) mechanically possible even without a
        direct conversation."""
        c = self.civilization
        heirs = self._heirs_of(agent)
        if not heirs:
            return
        share = {res: amt for res, amt in agent.get("resources", {}).items() if amt > 0}
        if share:
            # Integer split (remainder to the first heir) -- resource counts
            # are integers everywhere else in the game (gather/contribute/
            # trade amounts), so this avoids introducing float stockpiles
            # that quota/rationing/display code elsewhere doesn't expect.
            for res, amt in share.items():
                base_each, remainder = divmod(int(amt), len(heirs))
                for i, heir in enumerate(heirs):
                    give = base_each + (remainder if i == 0 else 0)
                    if give:
                        heir["resources"][res] = heir["resources"].get(res, 0) + give
            agent["resources"] = {}
        if MEMES_ENABLED and agent.get("beliefs"):
            for heir in heirs:
                heir["beliefs"] |= agent["beliefs"]
        home_id = agent.get("homeStructureId")
        if home_id:
            structure = next((s for s in c["structures"] if s["id"] == home_id), None)
            new_owner = heirs[0]
            if structure and not new_owner.get("homeStructureId"):
                structure["homeOf"] = new_owner["name"]
                new_owner["homeStructureId"] = home_id
                self._push_activity(f"{new_owner['name']} inherits {agent['name']}'s home.")
            elif structure and structure.get("homeOf") == agent["name"]:
                structure["homeOf"] = None
            agent["homeStructureId"] = None
        self._push_activity(
            f"{agent['name']}'s belongings pass to " +
            (heirs[0]["name"] if len(heirs) == 1 else f"{len(heirs)} villagers") + ".")

    # --- Cemetery & burial (CEMETERY_ENABLED): permanent death shouldn't
    # leave a corpse lying wherever it fell. ---
    def _cemetery_district_id(self):
        """The dedicated burial-grounds district, if present."""
        if not CEMETERY_ENABLED:
            return None
        for did, d in self.civilization["districts"].items():
            if d.get("kind") == "cemetery" and d.get("grave_grid"):
                return did
        return None

    def _working_cemeteries(self):
        """Cemetery plots that can receive burials. Burial uses the district
        grave_grid, not the chapel's produce/boost status -- so a disrepaired
        or ruined chapel must not strand corpses (the escape is repair, but
        burial itself stays reachable)."""
        if not CEMETERY_ENABLED:
            return []
        did = self._cemetery_district_id()
        return [s for s in self.civilization["structures"]
                if s.get("type") == "cemetery"
                and (not did or s.get("districtId") == did)]

    def _grave_grid_position(self, district_id, index):
        """Structure-style grid slot for a grave in the cemetery district.
        Rows extend without wrapping so tombstones never stack on one spot."""
        d = self.civilization["districts"].get(district_id)
        grid = d.get("grave_grid") if d else None
        if not grid:
            return None
        col = index % grid["cols"]
        row = index // grid["cols"]
        return (grid["x0"] + col * grid["dx"],
                grid["y0"] + row * grid["dy"])

    def _buried_count_in_district(self, district_id):
        return sum(1 for a in self.agents
                   if a.get("buried") and a.get("restingDistrictId") == district_id)

    def _nearest_unburied_corpse(self, agent):
        """Auto-target fallback for bury_agent, mirroring _neediest_nearby's
        restraint: only agents already NEARBY are auto-picked. A corpse
        farther away must be named explicitly as `target` (which then drives
        the move-closer-first branch in apply_decision, same as heal_agent)."""
        nearby = [self._find_agent(n) for n in self._get_nearby_agents(agent)]
        nearby = [a for a in nearby if a and a.get("deathFrame") is not None and not a.get("buried")]
        if not nearby:
            return None
        nearby.sort(key=lambda a: self._distance_to(agent, a))
        return nearby[0]

    def _bury_agent_at(self, cemetery, corpse, buried_by=None):
        """Move a corpse to its resting place in the cemetery district grid.
        buried_by is the agent who performed the burial (organic bury_agent),
        or None when the BURIAL_BACKSTOP_FRAMES grace window expires."""
        district_id = cemetery.get("districtId") or self._cemetery_district_id()
        if not district_id:
            return
        index = self._buried_count_in_district(district_id)
        pos = self._grave_grid_position(district_id, index)
        if not pos:
            return
        x, y = pos
        corpse["x"] = x
        corpse["y"] = y
        corpse["targetX"] = x
        corpse["targetY"] = y
        corpse["buried"] = True
        corpse["restingPlaceId"] = cemetery["id"]
        corpse["restingDistrictId"] = district_id
        who = f"{buried_by['name']} buried" if buried_by else "The village buried"
        self._push_activity(f"{who} {corpse['name']} in the Cemetery.")
        if CULTURE_ENABLED:
            self._push_chronicle(f"{corpse['name']} was laid to rest in the Cemetery.", kind="burial")
        if buried_by:
            self._push_memory(buried_by, f"Buried {corpse['name']} in the Cemetery")

    def _ensure_cemetery_district(self):
        """Back-compat: older saves may lack the starter cemetery grounds."""
        if not CEMETERY_ENABLED:
            return
        c = self.civilization
        starter = STARTER_DISTRICTS["cemetery_grounds"]
        if "cemetery_grounds" not in c["districts"]:
            c["districts"]["cemetery_grounds"] = json.loads(json.dumps(starter))
            c["districtProjects"].setdefault("cemetery_grounds", None)
            c["districtLastContribution"].setdefault("cemetery_grounds", 0)
        if "cemetery_gate" not in c["roadNodes"]:
            c["roadNodes"]["cemetery_gate"] = dict(STARTER_ROAD_NODES["cemetery_gate"])
        edge = ["beach_gate", "cemetery_gate"]
        if edge not in c["roadEdges"] and list(reversed(edge)) not in c["roadEdges"]:
            c["roadEdges"].append(edge)
            self._recompute_road_paths()

    def _migrate_cemetery_structure(self):
        """Move the cemetery chapel onto the burial district's build grid."""
        did = self._cemetery_district_id()
        if not did:
            return
        d = self.civilization["districts"][did]
        grid = d.get("build_grid")
        if not grid:
            return
        spot_x = grid["x0"]
        spot_y = grid["y0"]
        cemeteries = [s for s in self.civilization["structures"] if s.get("type") == "cemetery"]
        if not cemeteries:
            return
        primary = min(cemeteries, key=lambda s: s["id"])
        primary["x"] = spot_x
        primary["y"] = spot_y
        primary["districtId"] = did

    def _relayout_cemetery_graves(self):
        """Re-seat every buried villager on the cemetery grave grid (load-time
        fix for the old tight-offset layout that stacked tombstones)."""
        did = self._cemetery_district_id()
        if not did:
            return
        buried = [a for a in self.agents if a.get("buried") and a.get("deathFrame") is not None]
        buried.sort(key=lambda a: (a.get("deathFrame", 0), a["id"]))
        for i, agent in enumerate(buried):
            pos = self._grave_grid_position(did, i)
            if not pos:
                continue
            agent["x"], agent["y"] = pos
            agent["targetX"], agent["targetY"] = pos
            agent["restingDistrictId"] = did

    def _maybe_build_cemetery(self):
        """Deterministic backstop (mirrors _maybe_start_approved_custom): once
        at least one agent has died with nowhere to be laid to rest, the
        elder starts a Cemetery project, founding new village land if the
        existing district is full -- same escape hatch as every other
        structure backstop, so this can never deadlock."""
        c = self.civilization
        if self.frameTick < c.get("cemeteryBackoffUntil", 0):
            return
        if self.frameTick - c.get("lastCemeteryCheckFrame", 0) < STALL_THRESHOLD:
            return
        if len(self._active_project_districts()) >= MAX_CONCURRENT_PROJECTS:
            return
        if self._project_type_active("cemetery"):
            return
        c["lastCemeteryCheckFrame"] = self.frameTick
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        elder["goal"] = None
        decision = {"action": "start_project", "target": "cemetery",
                    "reasoning": "The village has dead awaiting burial and no cemetery exists."}

        def _try_start():
            self.apply_decision(elder, decision)
            return self._project_type_active("cemetery")

        if _try_start():
            c["cemeteryBackstopFailures"] = 0
            c["cemeteryEscalationLogged"] = False
            c["cemeteryBackoffUntil"] = 0
            self._push_activity(f"Elder {elder['name']} directs the village to build a Cemetery for the dead.")
            return

        kind = PROJECT_KIND.get("cemetery", "village")
        tmpl = DISTRICT_KIND_TEMPLATES.get(kind)
        if tmpl:
            plot = self._claim_frontier_plot()
            if plot:
                self._found_district(kind, tmpl, plot)
                if _try_start():
                    c["cemeteryBackstopFailures"] = 0
                    c["cemeteryEscalationLogged"] = False
                    c["cemeteryBackoffUntil"] = 0
                    self._push_activity(
                        f"Elder {elder['name']} opens new {kind} land and starts the Cemetery.")
                    return

        c["cemeteryBackstopFailures"] = c.get("cemeteryBackstopFailures", 0) + 1
        if not c.get("cemeteryEscalationLogged"):
            self._push_activity(
                f"Cannot start the Cemetery — all {kind} districts are blocked; "
                f"backing off until land opens")
            c["cemeteryEscalationLogged"] = True
        c["cemeteryBackoffUntil"] = self.frameTick + APPROVED_CUSTOM_BACKOFF_FRAMES

    def _maybe_handle_burials(self):
        """Tick-gated backstop: build a Cemetery if the village needs one and
        doesn't have one; once one exists, give bury_agent an organic grace
        window (nudged in the prompt) before burying the dead itself so no
        corpse waits forever. Never touches a non-permanent collapse
        (deathFrame is None) -- only LIFECYCLE_ENABLED's permanent death is
        eligible, matching "any non-permanent death should not be in the
        cemetery"."""
        if not CEMETERY_ENABLED or not LIFECYCLE_ENABLED:
            return
        unburied = [a for a in self.agents if a.get("deathFrame") is not None and not a.get("buried")]
        if not unburied:
            return
        cemeteries = self._working_cemeteries()
        if not cemeteries:
            self._maybe_build_cemetery()
            return
        cemetery = cemeteries[0]
        for corpse in unburied:
            if self.frameTick - corpse["deathFrame"] < BURIAL_BACKSTOP_FRAMES:
                continue
            self._bury_agent_at(cemetery, corpse, buried_by=None)

    def _repair_backstop_agent(self, structures):
        """Pick a living non-responder nearest the worst-condition target who
        can fund the repair (stockpile + held). Shared by housing/market
        emergency rebuilds."""
        if not structures:
            return None
        em = self._sage_emergency() if SURVIVAL_ENABLED else None
        responders = self._sage_responders(em) if em else set()
        candidates = [
            a for a in self.agents
            if a.get("deathFrame") is None
            and not a.get("incapacitated")
            and a["name"] not in responders
        ]
        if not candidates:
            return None
        target = min(structures, key=lambda s: s.get("condition", 100))

        def _can_fund(agent):
            cost = self._repair_cost(target)
            stock = self.civilization["stockpile"]
            for res, amt in cost.items():
                held = agent["resources"].get(res, 0) + int(stock.get(res, 0))
                if held < amt:
                    return False
            return True

        funded = [a for a in candidates if _can_fund(a)]
        pool = funded or candidates
        did = target.get("districtId")
        if did and did in self.civilization["districts"]:
            return min(pool, key=lambda a: self._distance_to_district(a, did))
        return pool[0]

    def _critical_structure_categories(self):
        """Ordered table of (type_, guard_fn, trigger_fn, message_template)
        driving `_maybe_repair_critical`. Guard/trigger are zero-arg
        callables closed over `self` so the table can be built once per call
        without repeating boilerplate. Order matters: only the first
        matching category is repaired per call (see `_maybe_repair_critical`)."""
        c = self.civilization

        def has_type(type_):
            return any(s.get("type") == type_ for s in c["structures"])

        return (
            (
                "house",
                lambda: GOODS_ENABLED,
                lambda: self._working_structure_count("house") == 0,
                "Housing emergency -- {summary} (no working houses left; "
                "population cap was locked)",
            ),
            (
                "market",
                lambda: GOODS_ENABLED and ECONOMY_ENABLED and has_type("market"),
                lambda: not self._market_active(),
                "Market emergency -- {summary} (no working market left; "
                "priced trade was locked)",
            ),
            (
                "workshop",
                lambda: GOODS_ENABLED and has_type("workshop"),
                lambda: self._working_structure_count("workshop") == 0,
                "Workshop emergency -- {summary} (no working workshop left; "
                "crafting was locked)",
            ),
            (
                "foundry",
                lambda: GOODS_ENABLED and path1_on("TIER3_CONTENT_ENABLED") and has_type("foundry"),
                lambda: self._working_structure_count("foundry") == 0,
                "Foundry emergency -- {summary} (no working foundry left; "
                "tier-3 crafting was locked)",
            ),
            (
                "granary",
                lambda: GOODS_ENABLED and CRAFTING_ENABLED and has_type("granary"),
                lambda: self._working_structure_count("granary") == 0,
                "Granary emergency -- {summary} (no working granary left; "
                "food storage was locked)",
            ),
            (
                "farm_plot",
                lambda: GOODS_ENABLED and has_type("farm_plot"),
                lambda: self._working_structure_count("farm_plot") == 0,
                "Farm emergency -- {summary} (no working farm plot left; "
                "food production was locked)",
            ),
        )

    def _maybe_repair_critical(self):
        """Deterministic escape when a whole critical-structure category has
        zero working instances village-wide. Generalizes the 2026-07-10
        house/market backstops (see git history) that were added after soaks
        showed repair_structure -- though reachable by the model and funded
        from the stockpile -- consistently loses the priority contest under
        survival pressure, permanently locking housing or priced trade.
        Table-driven (`_critical_structure_categories`) so the same escape
        covers workshop/foundry/granary/farm_plot too, without duplicating
        the guard/trigger/repair/log boilerplate per category.

        Walks the table in order and repairs at most ONE category per call
        (matching the original "one house/market rebuild per RULES_TICK
        gate" behavior) so multiple emergencies never compete for the same
        scarce stockpile resources within a single gate tick."""
        for type_, guard, trigger, message in self._critical_structure_categories():
            if not guard():
                continue
            if not trigger():
                continue
            structures = [s for s in self.civilization["structures"] if s.get("type") == type_]
            agent = self._repair_backstop_agent(structures)
            if not agent:
                continue
            agent["goal"] = None
            summary = self._repair_structure(agent, type_)
            if summary and "lacks" not in summary and "nothing" not in summary:
                self._push_activity(message.format(summary=summary))
            return

    # --- succession (#4): reuses the propose_rule/vote_rule scaffold ---
    def _start_succession_election(self):
        """One pending 'succession' rule per eligible candidate (adults,
        excluding the just-deceased elder, capped to keep MAX_PENDING_RULES
        headroom for ordinary governance). Candidates are the eligible-adult
        set -- deterministic, no LLM involved in nomination. Villagers vote
        via the existing vote_rule action; _vote_on_rule's exclusivity logic
        (above) makes a "yes" on one candidate a "no" on the rest."""
        c = self.civilization
        candidates = self._eligible_adults()
        if not candidates:
            # No adult left at all (extreme edge case): fall back to any
            # living agent so the village is never leaderless.
            candidates = [a for a in self.agents if a.get("deathFrame") is None]
        if not candidates:
            return  # truly no one left; nothing to elect (village-extinction edge case)
        candidates = candidates[:max(2, MAX_PENDING_RULES)]
        election_id = f"succession_{self.frameTick}"
        c["pendingRules"] = [r for r in c["pendingRules"] if r["kind"] != "succession"]
        entries = []
        for cand in candidates:
            entry = {
                "id": f"{election_id}_{cand['name'].lower()}", "name": f"Elect {cand['name']}",
                "kind": "succession", "value": cand["name"],
                "description": f"{cand['name']} succeeds as village elder.",
                "proposedBy": "the village", "enacted": False, "votes": {},
                "electionId": election_id, "candidateName": cand["name"],
            }
            entries.append(entry)
            c["pendingRules"].append(entry)
        c["pendingSuccession"] = {
            "electionId": election_id,
            "candidates": [cand["name"] for cand in candidates],
            "startFrame": self.frameTick,
            "deadline": self.frameTick + SUCCESSION_ELECTION_TTL_FRAMES,
        }
        c["lastSuccessionActivityFrame"] = self.frameTick
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_activity(
            f"The village must choose a new elder. Candidates: {', '.join(c['pendingSuccession']['candidates'])}.")
        self._push_communication("election", "the village", "everyone",
                                 f"Succession election opened: {', '.join(c['pendingSuccession']['candidates'])}")

    def _enact_succession_winner(self, rule):
        """Promotes the winning candidate to elder (direct role assignment --
        succession is a deterministic engine act, not an LLM decision, same
        as _found_district or any other backstop mutation) and clears the
        rest of the election's ballots. Called from _tally_and_maybe_enact
        once a candidate's rule crosses quorum."""
        c = self.civilization
        winner_name = rule.get("candidateName") or rule.get("value")
        winner = self._find_agent(winner_name)
        election_id = rule.get("electionId")
        other_candidates = [r.get("candidateName") for r in c["pendingRules"]
                            if r["kind"] == "succession" and r.get("electionId") == election_id
                            and r.get("candidateName") != winner_name]
        c["pendingRules"] = [r for r in c["pendingRules"]
                             if not (r["kind"] == "succession" and r.get("electionId") == election_id)]
        c["pendingSuccession"] = None
        c["lastSuccessionActivityFrame"] = self.frameTick
        if winner and (winner.get("deathFrame") is not None or winner["incapacitated"]):
            # Edge case: the winning candidate died (of old age) or collapsed
            # during the ~13 min TTL window between nomination and tiebreak.
            # Crowning a corpse (or a currently-incapacitated agent) would
            # leave the village silently leaderless -- no other code path
            # re-triggers an election for an agent that was never actually
            # made elder. Re-open a fresh election among the remaining
            # candidates instead; the arc still cannot stall, it just takes
            # one more round.
            self._push_activity(
                f"{winner['name']} could not take up the elder's mantle -- "
                f"the village must choose again.")
            self._start_succession_election()
            return
        if winner:
            old_role = winner["role"]
            winner["role"] = "elder"
            winner["thinkInterval"] = 240
            self._push_activity(f"{winner['name']} (formerly {old_role}) is chosen as the new village elder!")
            self._push_communication("election", "the village", "everyone",
                                     f"{winner['name']} is the new elder")
            self._log_benchmark("succession", 1, {"winner": winner["name"], "electionId": election_id})
            if CULTURE_ENABLED:
                self._push_chronicle(f"{winner['name']} was elected the new village elder.", kind="election")
                self._drift_personality(winner, "emboldened by winning the election")
                for name in other_candidates:
                    loser = self._find_agent(name)
                    if loser and loser.get("deathFrame") is None:
                        self._drift_personality(loser, "humbled by losing the election")

    def _maybe_resolve_stalled_succession(self):
        """Deterministic escape hatch: an election cannot stall forever. Once
        SUCCESSION_ELECTION_TTL_FRAMES elapses with no candidate at quorum,
        pick the one with the most yes votes (ties broken by lowest agent id,
        fully deterministic) and enact it directly -- the arc always
        completes, matching the hard rule that succession cannot softlock."""
        c = self.civilization
        pending = c.get("pendingSuccession")
        if not pending or self.frameTick < pending["deadline"]:
            return
        entries = [r for r in c["pendingRules"]
                  if r["kind"] == "succession" and r.get("electionId") == pending["electionId"]]
        if not entries:
            c["pendingSuccession"] = None
            return
        def _yes_count(r):
            return list(r["votes"].values()).count("yes")
        def _candidate_id(r):
            cand = self._find_agent(r.get("candidateName"))
            return cand["id"] if cand else 1 << 30
        entries.sort(key=lambda r: (-_yes_count(r), _candidate_id(r)))
        winner_rule = entries[0]
        winner_rule["enacted"] = True
        self._push_activity(
            f"The succession vote stalled without a majority -- by village custom, "
            f"{winner_rule['candidateName']} (most votes, tie broken by seniority) becomes elder.")
        self._enact_succession_winner(winner_rule)

    # --- birth (#2): reuses the newcomer machinery, adds a birth persona ---
    def _birth_food_surplus(self):
        c = self.civilization
        living = self._living_agents()
        held = sum(a["resources"].get(rid, 0) for a in living for rid in EDIBLE_RESOURCES)
        stocked = sum(c["stockpile"].get(rid, 0) for rid in EDIBLE_RESOURCES)
        return held + stocked

    def _ally_pair_from(self, candidates):
        """First ally-linked pair (either direction) in a candidate list."""
        for i, a in enumerate(candidates):
            for b in candidates[i + 1:]:
                if a["relationships"].get(b["name"]) == "ally" or b["relationships"].get(a["name"]) == "ally":
                    return a, b
        return None

    def _find_ally_birth_pair(self):
        """Two adults, ally-linked (either direction). Prefer a shared district;
        when the living population is at the floor, any village-wide ally pair
        is enough — otherwise four survivors scattered across districts can
        never recover even with housing and food to spare.

        Floor escape (2026-07-10 evening): if survivors' only ally links point
        at the dead, `_ally_pair_from` stays empty forever and births never
        reopen even with working houses + food. At the floor, any two living
        adults are enough — the ally preference still wins when it can."""
        adults = self._eligible_adults()
        by_district = {}
        for a in adults:
            by_district.setdefault(a.get("currentDistrict"), []).append(a)
        for district_agents in by_district.values():
            if len(district_agents) < 2:
                continue
            pair = self._ally_pair_from(district_agents)
            if pair:
                return pair
        if len(adults) <= POPULATION_FLOOR:
            pair = self._ally_pair_from(adults)
            if pair:
                return pair
            if len(adults) >= 2:
                return adults[0], adults[1]
        return None

    def _maybe_birth(self):
        """Birth (#2): housing headroom + food surplus + two ally adults
        sharing a district. Gated to at most one birth per interval so a
        housing boom can't spawn a crowd in one tick. The ONLY LLM call in
        the whole lifecycle system happens here (persona authoring) -- an
        event, never a tick."""
        c = self.civilization
        if self.frameTick - c.get("lastBirthFrame", 0) < BIRTH_MIN_INTERVAL_FRAMES:
            return
        living_n = len(self._living_agents())
        if living_n >= self._population_cap():
            return  # no housing headroom
        if self._birth_food_surplus() < BIRTH_FOOD_SURPLUS_PER_AGENT * max(1, living_n):
            return  # no food surplus
        pair = self._find_ally_birth_pair()
        if not pair:
            return
        parent_a, parent_b = pair
        self._spawn_newborn(parent_a, parent_b)

    def _next_agent_slot(self):
        """An unused AGENT_DEFS entry if one exists (mirrors
        _maybe_welcome_newcomer); otherwise a generated villager beyond the
        fixed 12-name roster, so births never stall just because every named
        slot is occupied by long-lived retirees."""
        unused = next((d for d in AGENT_DEFS if d["name"] not in self.agent_names), None)
        if unused:
            return dict(unused), False
        c = self.civilization
        gen_id = c.get("nextGeneratedAgentId", 1000)
        c["nextGeneratedAgentId"] = gen_id + 1
        roles = list(self.d["ROLES"].keys()) or ["gatherer"]
        role = random.choice([r for r in roles if r != "elder"] or roles)
        zone = random.choice(list(self.civilization["districts"].keys()))
        return {"id": gen_id, "name": f"Villager{gen_id}", "role": role,
                "personality": "newly born", "color": "#%06x" % random.randint(0, 0xFFFFFF),
                "zone": zone}, True

    def _spawn_newborn(self, parent_a, parent_b):
        c = self.civilization
        slot, generated = self._next_agent_slot()
        newborn = self._make_agents([slot])[0]
        newborn["age"] = 0.0
        newborn["parents"] = [parent_a["name"], parent_b["name"]]
        # Low-skill start (#2): a newborn's specialty carries no structure/
        # role bonus differently from an adult -- it starts at the young
        # life stage, which _life_stage already surfaces in prompts, and
        # begins with empty resources rather than the usual starter stash.
        newborn["resources"] = {"food": 0, "wood": 0, "gold": 0}
        if MEMES_ENABLED:
            newborn["beliefs"] = set(parent_a.get("beliefs") or set()) | set(parent_b.get("beliefs") or set())
        # Inherit a share of goods from both parents (#2). Integer amounts --
        # resource counts are integers everywhere else in the game.
        for parent in (parent_a, parent_b):
            for res, amt in list(parent["resources"].items()):
                share = int(amt * NEWBORN_GOODS_SHARE)
                if share <= 0:
                    continue
                parent["resources"][res] = amt - share
                newborn["resources"][res] = newborn["resources"].get(res, 0) + share
        # Inherit a home claim if either parent has one and the newborn
        # doesn't yet (Phase E property, finally consumed).
        home_id = parent_a.get("homeStructureId") or parent_b.get("homeStructureId")
        if home_id:
            newborn["homeStructureId"] = None  # child doesn't claim outright while parents live; breadcrumb only
        self.agents.append(newborn)
        self.agent_names.add(newborn["name"])
        c["lastBirthFrame"] = self.frameTick
        c["births"] = c.get("births", 0) + 1
        # Persona authoring (#2): exactly ONE lm_complete call, this event
        # only -- never per tick. A failed/empty call falls back to the
        # deterministic slot name so birth never blocks on the LLM.
        persona = None
        try:
            persona = self.d["lm_complete"](
                "You write a one-sentence birth announcement for a village simulation. "
                "Given the two parents' names and roles, invent a short first name for "
                "the newborn and one brief personality trait. Output ONLY the sentence, "
                "no preamble, in the form: NAME is a NAME_'s child, TRAIT.",
                f"Parents: {parent_a['name']} ({parent_a['role']}) and "
                f"{parent_b['name']} ({parent_b['role']}).",
                max_tokens=100, temperature=0.8,
            )
        except Exception:
            persona = None
        # Belt-and-suspenders: lm_complete already rejects scaffold, but a
        # truncated instruction echo that ends in '.' can still slip past
        # finish_reason==length (cycle 10.morning: 2/36 births).
        is_scaffold = self.d.get("is_scaffold_text")
        if persona and is_scaffold and is_scaffold(persona):
            persona = None
        if persona:
            newborn["persona"] = persona.strip()[:200]
            announce = persona.strip()
        else:
            announce = f"{newborn['name']} is born to {parent_a['name']} and {parent_b['name']}."
        self._push_activity(announce)
        self._push_communication("birth", parent_a["name"], "everyone", announce)
        for a in self.agents:
            if a is newborn:
                continue
            self._push_memory(a, f"{newborn['name']} was born to {parent_a['name']} and {parent_b['name']}.")
        self._log_benchmark("birth", c["births"], {"name": newborn["name"], "generated": generated,
                                                     "parents": [parent_a["name"], parent_b["name"]]})

    # --- governance gates (#5): harvest_quota / rationing enforcement ---
    def _active_harvest_quota(self):
        if not LIFECYCLE_ENABLED:
            return None
        quotas = self.civilization.get("harvestQuotas") or {}
        if not quotas:
            return None
        # If the village has enacted more than one harvest_quota rule, they
        # compose as "must satisfy all of them" -- the strictest (lowest)
        # value binds, not the most permissive, so a later lenient vote can
        # never silently override an earlier, intentionally tight one.
        return min(q["value"] for q in quotas.values())

    def _harvest_quota_gate(self, agent, resource):
        """Returns (allowed, reason). Caps an agent's gathers of ONE resource
        in their current district per HARVEST_QUOTA_PERIOD_FRAMES window.
        Deterministic escape: the counter resets every period, so a refusal
        is never permanent -- wait out the period, gather a different
        resource, or move to another district."""
        quota = self._active_harvest_quota()
        if quota is None:
            return True, None
        if self.frameTick - agent.get("lastQuotaResetFrame", 0) >= HARVEST_QUOTA_PERIOD_FRAMES:
            agent["gatherCountThisPeriod"] = {}
            agent["lastQuotaResetFrame"] = self.frameTick
        counts = agent.setdefault("gatherCountThisPeriod", {})
        district = agent.get("currentDistrict") or "?"
        key = f"{district}:{resource}"
        if counts.get(key, 0) >= quota:
            remaining = HARVEST_QUOTA_PERIOD_FRAMES - (self.frameTick - agent["lastQuotaResetFrame"])
            return False, (f"harvest quota reached for {resource} in {district} "
                           f"({quota}/period) -- resets in ~{max(1, remaining // 30)}s")
        return True, None

    def _record_harvest_quota_use(self, agent, resource, amount):
        if self._active_harvest_quota() is None:
            return
        counts = agent.setdefault("gatherCountThisPeriod", {})
        district = agent.get("currentDistrict") or "?"
        key = f"{district}:{resource}"
        counts[key] = counts.get(key, 0) + amount

    def _rationing_active_cap(self):
        if not LIFECYCLE_ENABLED:
            return None
        active = self.civilization.get("rationingActive") or {}
        if not active:
            return None
        # Deterministic escape: rationing only actually restricts while
        # storage utilization is low -- once storage recovers, withdrawals
        # are unrestricted again even with the rule still enacted (matches
        # "rationing lifts when storage recovers" in the hard rules).
        if not self._storage_low():
            return None
        return min(v["value"] for v in active.values())

    def _storage_low(self):
        if not GOODS_ENABLED:
            return False
        caps = {rid: self._storage_capacity(rid) for rid in EDIBLE_RESOURCES}
        total_cap = sum(caps.values()) or 1
        c = self.civilization
        stored = sum(c["stockpile"].get(rid, 0) + sum(a["resources"].get(rid, 0) for a in self.agents)
                    for rid in EDIBLE_RESOURCES)
        return (stored / total_cap) < RATIONING_STORAGE_LOW_RATIO

    def _rationing_gate(self, agent, resource, amount):
        """Returns (allowed_amount, reason|None) for a stockpile withdrawal
        (contribute_resources reversed, trade, etc. all funnel through here
        when they pull FROM the shared stockpile). Caps rather than outright
        refuses so a partial withdrawal still gets through when possible."""
        cap = self._rationing_active_cap()
        if cap is None or resource not in EDIBLE_RESOURCES:
            return amount, None
        if amount <= cap:
            return amount, None
        return cap, f"rationing limits {resource} withdrawals to {cap} while storage is low"

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
            village_tier=self._village_tech_tier() if TECH_TREE_ENABLED else None,
        )

    # --- live role registry ---
    def _rebuild_role_maps(self):
        """Derive every role lookup from this world's persistent registry.

        The injected maps are intentionally replaced rather than mutating the
        server's seed maps: roles.json remains seed-only authoring data while a
        running world can safely specialize independently of another engine.
        """
        registry = self.civilization.get("roleRegistry") or {}
        self.d["ROLE_PROJECT"] = {
            role: definition.get("preferredProject", "house")
            for role, definition in registry.items() if isinstance(definition, dict)
        }
        self.d["ROLE_SKILLS"] = {
            role: definition.get("skill", "helps the village")
            for role, definition in registry.items() if isinstance(definition, dict)
        }
        self.d["ROLE_PRIMARY_RESOURCE"] = {
            role: definition["specialty"][0]
            for role, definition in registry.items()
            if isinstance(definition, dict) and definition.get("specialty")
        }
        gather_roles = {}
        for role, definition in registry.items():
            if not isinstance(definition, dict):
                continue
            for resource in definition.get("specialty") or []:
                gather_roles.setdefault(resource, []).append(role)
        self.d["RESOURCE_GATHER_ROLES"] = {
            resource: tuple(roles) for resource, roles in gather_roles.items()
        }

    def _validate_role(self, role):
        """Validate an emergent role proposal against the live world state."""
        c = self.civilization
        if not isinstance(role, dict):
            return False, "role must be an object"
        if len(c.get("pendingRoles") or []) >= MAX_PENDING_ROLES:
            return False, "too many pending roles"
        allowed = {"slug", "name", "specialty", "preferredProject", "skill"}
        extra = set(role) - allowed
        if extra:
            return False, f"unknown role fields: {', '.join(sorted(extra))}"
        slug = role.get("slug")
        if not isinstance(slug, str) or not self.SLUG_RE.match(slug):
            return False, "invalid role slug"
        registry = c.get("roleRegistry") or {}
        if slug in registry:
            return False, "role already exists"
        if any(p.get("slug") == slug for p in c.get("pendingRoles") or [] if isinstance(p, dict)):
            return False, "role is already pending"
        seed_roles = set(self.d["ROLES"])
        emergent_count = len(set(registry) - seed_roles)
        if emergent_count >= MAX_EMERGENT_ROLES:
            return False, "too many emergent roles"
        name = role.get("name")
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
            return False, "invalid role name"
        skill = role.get("skill")
        if not isinstance(skill, str) or not (1 <= len(skill.strip()) <= 160) or "\n" in skill:
            return False, "skill must be one line of 1-160 characters"
        specialty = role.get("specialty")
        if not isinstance(specialty, list) or len(specialty) > 4 \
                or any(not isinstance(resource, str) or resource not in c["resourceRegistry"]
                       for resource in specialty):
            return False, "specialty must list up to 4 known resources"
        preferred = role.get("preferredProject")
        projects = c["projectRegistry"]
        preferred_values = preferred if isinstance(preferred, list) else [preferred]
        if not preferred_values or len(preferred_values) > 4 \
                or any(not isinstance(project, str) or project not in projects
                       for project in preferred_values):
            return False, "preferredProject must name 1-4 known project types"
        return True, None

    @staticmethod
    def _role_record(role):
        """Copy the proposal into the registry's seed-compatible shape."""
        preferred = role["preferredProject"]
        return {
            "name": role["name"].strip(),
            "skill": role["skill"].strip(),
            "specialty": list(role["specialty"]),
            "preferredProject": list(preferred) if isinstance(preferred, list) else preferred,
        }

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
        # Sid-parity Phase 2: an enacted priority rule biases contributions
        # toward its named resource (mirrors harvest_spirit edible bias).
        priority_res = self._active_priority_resource()
        if priority_res:
            need = p["needs"].get(priority_res, 0)
            have = p["contributed"].get(priority_res, 0)
            if need > have and agent["resources"].get(priority_res, 0) > 0:
                return priority_res
        # Phase G belief-driven bias (deterministic, no new action): a
        # harvest_spirit believer prefers contributing an EDIBLE resource the
        # project still needs, ahead of the generic need-order scan below --
        # "beliefs influence a deterministic bias" from the plan, at zero
        # token cost (it reads the existing beliefs set, no new prompt line).
        if CULTURE_ENABLED and HARVEST_SPIRIT_CONTRIB_BOOST and MEME_SEED_ID in agent.get("beliefs", ()):
            for res in EDIBLE_RESOURCES:
                need = p["needs"].get(res, 0)
                have = p["contributed"].get(res, 0)
                if need > have and agent["resources"].get(res, 0) > 0:
                    return res
        for res in p["needs"]:
            need = p["needs"].get(res, 0)
            have = p["contributed"].get(res, 0)
            if need > have and agent["resources"].get(res, 0) > 0:
                return res
        return None

    # --- memes ---
    def _belief_registry(self):
        """Live seed + authored beliefs; old saves gain seed records lazily."""
        registry = self.civilization.get("beliefRegistry")
        if not isinstance(registry, dict):
            registry = {}
            self.civilization["beliefRegistry"] = registry
        for bid, tenet in MEMES.items():
            registry.setdefault(bid, {
                "id": bid, "name": bid.replace("_", " ").title(),
                "tenet": tenet, "affinity": sorted(MEME_RULE_AFFINITY.get(bid, set())),
                "authoredBy": None, "createdFrame": 0, "seed": True,
            })
        return registry

    def _belief_entry(self, belief_id):
        return self._belief_registry().get(belief_id) or {}

    def _belief_name(self, belief_id):
        return self._belief_entry(belief_id).get("name") or belief_id.replace("_", " ").title()

    def _belief_text(self, bid):
        entry = self._belief_entry(bid)
        if entry.get("tenet"):
            return entry["tenet"]
        # Keep legacy mutation overrides readable when restoring an old state.
        if CULTURE_ENABLED:
            override = self.civilization.get("memeTexts", {}).get(bid)
            if override:
                return override
        return MEMES.get(bid, bid)

    def _seed_beliefs(self):
        """Seed two competing memes on different living agents (Sid-parity
        Phase 3). Falls back to a single seed if the roster is too small."""
        if not MEMES_ENABLED or not self.agents:
            return
        living = [a for a in self.agents if a.get("deathFrame") is None] or list(self.agents)
        random.shuffle(living)
        origins = living[:2] if len(living) >= 2 else living[:1]
        for agent, meme_id in zip(origins, MEME_SEED_IDS):
            agent["beliefs"].add(meme_id)
            self._push_activity(
                f'{agent["name"]} began spreading a rumor: "{self._belief_text(meme_id)}"')
            self._push_communication("rumor", agent["name"], "everyone",
                                     self._belief_text(meme_id))
            self._push_memory(agent, f"I believe: {self._belief_text(meme_id)}")

    def _belief_favored_kinds(self, agent):
        favored = set()
        for bid in agent.get("beliefs") or ():
            affinity = self._belief_entry(bid).get("affinity")
            favored |= set(affinity if isinstance(affinity, list) else MEME_RULE_AFFINITY.get(bid, set()))
        return favored

    def _belief_biased_vote(self, agent, pending):
        """Return yes/no biased by the voter's beliefs, or None for no bias.
        harvest_spirit believers favor food-protective rules; river_spirit
        believers favor priority rules and lean against heavy rationing."""
        if not MEMES_ENABLED or not pending:
            return None
        kind = pending.get("kind")
        beliefs = agent.get("beliefs") or set()
        favored = self._belief_favored_kinds(agent)
        if kind in favored:
            return "yes"
        if MEME_RIVAL_ID in beliefs and kind in ("rationing", "harvest_quota"):
            return "no"
        if MEME_SEED_ID in beliefs and kind == "priority" and pending.get("value") == "fish":
            return "no"
        return None

    def _found_belief(self, agent, belief):
        if not MEMES_ENABLED:
            return f"{agent['name']} cannot found a belief while culture is disabled"
        if not isinstance(belief, dict):
            return f"{agent['name']} did not provide a belief"
        belief_id, name, tenet, affinity = (belief.get("id"), belief.get("name"),
                                             belief.get("tenet"), belief.get("affinity"))
        registry = self._belief_registry()
        if not isinstance(belief_id, str) or not self.SLUG_RE.match(belief_id):
            return f"{agent['name']} proposed an invalid belief id"
        if belief_id in registry:
            return f"{agent['name']} cannot found {belief_id} — it already exists"
        if len(registry) >= MAX_BELIEFS:
            return f"{agent['name']} cannot found another belief — the village has reached its belief limit"
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
            return f"{agent['name']} proposed an invalid belief name"
        if not isinstance(tenet, str) or not (8 <= len(tenet.strip()) <= 160) or "\n" in tenet:
            return f"{agent['name']} proposed an invalid belief tenet"
        if not isinstance(affinity, list) or not affinity or len(affinity) > len(RULE_KINDS) \
                or any(not isinstance(kind, str) for kind in affinity) \
                or len(set(affinity)) != len(affinity) or not set(affinity).issubset(RULE_KINDS):
            return f"{agent['name']} proposed an invalid belief affinity"
        registry[belief_id] = {
            "id": belief_id, "name": name.strip(), "tenet": tenet.strip(),
            "affinity": list(affinity), "authoredBy": agent["name"],
            "createdFrame": self.frameTick, "seed": False,
        }
        agent["beliefs"].add(belief_id)
        self._push_activity(f"{agent['name']} founded {name.strip()}: \"{tenet.strip()}\"")
        self._push_communication("belief_founded", agent["name"], "everyone", tenet.strip())
        self._push_memory(agent, f"Founded {name.strip()}: {tenet.strip()}")
        self._push_chronicle(f"{agent['name']} founded {name.strip()}", kind="belief_founded")
        return f"{agent['name']} founded {name.strip()}"

    def _adopt_belief(self, speaker, recipient, belief_id, quality, fallback=False):
        if not MEMES_ENABLED or not speaker or not recipient \
                or belief_id not in speaker.get("beliefs", set()) \
                or belief_id in recipient.get("beliefs", set()) \
                or recipient is speaker or recipient["incapacitated"]:
            return None
        recipient["beliefs"].add(belief_id)
        self._nudge_ally(speaker, recipient["name"])
        self._nudge_ally(recipient, speaker["name"])
        source = "deterministic fallback" if fallback else f"pitch quality {quality:.2f}"
        self._push_activity(f'{recipient["name"]} adopted {self._belief_name(belief_id)} from {speaker["name"]} ({source})')
        self._push_communication("belief", speaker["name"], recipient["name"], self._belief_text(belief_id))
        self._push_memory(recipient, f"Came to believe {self._belief_name(belief_id)}: {self._belief_text(belief_id)}")
        self._push_chronicle(f'{recipient["name"]} adopted {self._belief_name(belief_id)}', kind="belief_adoption")
        return belief_id

    def _belief_conversion_probability(self, speaker, recipient, quality):
        left = BELIEF_RELATIONSHIP_WEIGHT.get(self._relationship_between(speaker, recipient["name"]), 0.68)
        right = BELIEF_RELATIONSHIP_WEIGHT.get(self._relationship_between(recipient, speaker["name"]), 0.68)
        probability = 0.08 + (0.70 * max(0.0, min(1.0, quality)) * ((left + right) / 2.0))
        if recipient.get("beliefs"):
            probability *= BELIEF_EXISTING_PENALTY
        return max(0.02, min(0.88, probability))

    def _deterministic_belief_roll(self, speaker, recipient, belief_id):
        material = f"{self.frameTick}|{speaker['name']}|{recipient['name']}|{belief_id}"
        return (sum((idx + 1) * ord(ch) for idx, ch in enumerate(material)) % 1000) / 1000.0

    def _maybe_spread_beliefs(self, agent, recipient_name, message, belief_pitch=None,
                              judged_quality=None, model_scored=False):
        if not MEMES_ENABLED or not recipient_name or recipient_name == "everyone" \
                or not isinstance(belief_pitch, dict):
            return
        recipient = self._find_agent(recipient_name)
        belief_id = belief_pitch.get("belief_id")
        pitch_text = belief_pitch.get("pitch")
        # Count an actual returned model score before checking whether the
        # pair remained adjacent while the decision was in flight. Otherwise
        # rapid movement could spend unbounded scores that never reach the
        # conversion branch. The server never requests a score for a target
        # absent from the original nearby payload.
        use_model = (model_scored and isinstance(judged_quality, (int, float))
                     and not isinstance(judged_quality, bool) and 0.0 <= judged_quality <= 1.0
                     and self.civilization.get("beliefPitchCalls", 0) < BELIEF_PITCH_SESSION_CAP)
        if use_model:
            self.civilization["beliefPitchCalls"] = self.civilization.get("beliefPitchCalls", 0) + 1
        # `talk_to_nearby` preserves its historical move-and-deliver behavior
        # for a named distant target. Belief persuasion is stricter: it is an
        # adjacent conversation only, never a remote conversion while walking.
        if not recipient or self._distance_to(agent, recipient) > 80 \
                or belief_id not in agent.get("beliefs", set()) \
                or belief_id in recipient.get("beliefs", set()) \
                or not isinstance(pitch_text, str) or not (4 <= len(pitch_text.strip()) <= 240):
            return
        quality = float(judged_quality) if use_model else BELIEF_FALLBACK_QUALITY
        if self._deterministic_belief_roll(agent, recipient, belief_id) \
                <= self._belief_conversion_probability(agent, recipient, quality):
            self._adopt_belief(agent, recipient, belief_id, quality, fallback=not use_model)

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
        """Retained tick hook: adjacency creates a pitch opportunity only.
        A belief changes hands exclusively through talk_to_nearby's explicit
        belief_pitch payload, never through a background probability roll."""
        return

    def _meme_adoption_counts(self):
        """Per-meme living-agent adoption counts (Sid-parity Phase 3)."""
        if not MEMES_ENABLED:
            return {}
        living = [a for a in self.agents if a.get("deathFrame") is None]
        counts = {mid: 0 for mid in self._belief_registry()}
        for a in living:
            for bid in a.get("beliefs") or ():
                if bid in counts:
                    counts[bid] += 1
        return counts

    def _meme_adoption_count(self):
        """Total living agents holding one or more live beliefs."""
        if not MEMES_ENABLED:
            return 0
        living = [a for a in self.agents if a.get("deathFrame") is None]
        return len([a for a in living if a.get("beliefs")])

    def _maybe_mutate_meme(self, belief_id, speaker, recipient):
        """Event-driven, capped mutation of a belief's text on spread (#3).
        Exactly one lm_complete call per mutation attempt, itself gated by a
        low probability AND a hard session-lifetime cap
        (MEME_MUTATION_SESSION_CAP) so a long soak can never turn ordinary
        proximity chatter into a background LLM-spam loop -- the same
        discipline as Phase F's one-call-per-birth. A failed/empty call is a
        silent no-op (the belief keeps its prior text), never a blocker."""
        c = self.civilization
        if random.random() > MEME_MUTATION_PROB:
            return
        if c.get("memeMutations", 0) >= MEME_MUTATION_SESSION_CAP:
            return
        current_text = self._belief_text(belief_id)
        try:
            # Few-shot form: a thinking-class model (qwen) measured live to be
            # unreliable at following an abstract "reword this, don't explain"
            # instruction -- it kept emitting meta-commentary about the task
            # instead of doing it, even with generous max_tokens. Worked
            # examples of INPUT/OUTPUT pairs are more reliable at constraining
            # a small/thinking model to plain output than instructions alone.
            mutated = self.d["lm_complete"](
                "Rewrite a village rumor with slightly different wording, same "
                "meaning, under 15 words. Reply with the rewritten sentence only.",
                "Input: The river spirit blesses fishers at dawn.\n"
                "Output: Fishers who rise at dawn are blessed by the river spirit.\n\n"
                "Input: Strangers from the hills bring bad luck.\n"
                "Output: Bad luck follows strangers who come from the hills.\n\n"
                f"Input: {current_text}\n"
                "Output:",
                max_tokens=120, temperature=0.7,
            )
        except Exception:
            mutated = None
        if not mutated:
            return
        mutated = mutated.split("\n\n")[0].strip().strip('"').strip()
        # A thinking-class model (qwen, measured live) frequently prefixes a
        # one-or-two-word analysis label before the actual answer ("Subject:
        # ...", "Meaning: ...", "Object/Condition: ..."). Strip a single
        # leading "<ShortLabel>:" rather than reject the whole response --
        # the content after the colon is usually the real rewrite.
        label_match = re.match(r"^[A-Za-z][A-Za-z /]{0,24}:\s*", mutated)
        if label_match:
            mutated = mutated[label_match.end():].strip().strip('"').strip()
        mutated = mutated[:120]
        # Reject a remaining instruction-echo/meta-commentary/few-shot-repeat
        # leak (a known failure mode of thinking-class models -- lm_complete's
        # own is_scaffold_text already screens the raw response, but a
        # *clean-looking* single sentence can still be the model talking
        # ABOUT the task, or echoing the worked examples, rather than
        # producing a new one). Treat either signal exactly like an empty
        # response: a silent no-op, never a corrupted belief.
        low = mutated.lower()
        looks_meta = any(w in low for w in
                         ("context hint", "reword", "rumor sentence", "task:",
                          "instruction", "i should", "the model", "as an ai",
                          "input", "output:", "sentence:", "example", "generate"))
        has_words = bool(re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", mutated))
        if not mutated or mutated == current_text or looks_meta or not has_words \
                or self.d["is_scaffold_text"](mutated):
            return
        c.setdefault("memeTexts", {})[belief_id] = mutated
        c["memeMutations"] = c.get("memeMutations", 0) + 1
        self._push_activity(f'The belief "{current_text}" drifted into "{mutated}" as it spread through the village.')
        self._push_chronicle(f'A belief mutated: "{mutated}"', kind="meme_mutation")

    # --- Phase G: skills by practice + teaching (CULTURE_ENABLED) ---
    def _skill_level(self, agent, kind):
        if not CULTURE_ENABLED:
            return 0.0
        return (agent.get("skills") or {}).get(kind, 0.0)

    def _skill_bonus(self, agent, kind):
        """Integer yield/output bonus from a practiced skill -- +1 per
        SKILL_BONUS_DIVISOR levels, so early practice is legible but the cap
        (SKILL_MAX_LEVEL / SKILL_BONUS_DIVISOR, e.g. 2 at defaults) stays
        modest next to structure-effect bonuses."""
        if not CULTURE_ENABLED:
            return 0
        return int(self._skill_level(agent, kind) // SKILL_BONUS_DIVISOR)

    def _practice_skill(self, agent, kind):
        """Deterministic practice-raises-skill (#1): every successful use of
        a practiced verb nudges that skill up by a fixed amount, capped at
        SKILL_MAX_LEVEL. Called from the existing success paths of
        gather/craft/build/heal -- no new tick, no new action."""
        if not CULTURE_ENABLED or kind not in SKILL_KINDS:
            return
        skills = agent.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        before = skills.get(kind, 0.0)
        skills[kind] = min(SKILL_MAX_LEVEL, before + SKILL_PRACTICE_GAIN)
        self.civilization["skillPracticeCount"] = self.civilization.get("skillPracticeCount", 0) + 1

    def _maybe_teach(self, teacher, recipient_name, message):
        """Teaching (#1 apprenticeship): a talk_to_nearby message containing
        a teach-intent keyword (TEACH_KEYWORDS) and a recognized skill kind
        transfers TEACH_TRANSFER_FRACTION of the teacher's level in that
        skill to the recipient -- deterministic keyword check, no extra LLM
        call, no new action verb (the plan's change-map hint). No silent
        rejection: a failed match is simply not a teaching event (the talk
        still lands as ordinary conversation)."""
        if not CULTURE_ENABLED or not message or not recipient_name or recipient_name == "everyone":
            return
        text_lower = message.lower()
        if not any(kw in text_lower for kw in TEACH_KEYWORDS):
            return
        recipient = self._find_agent(recipient_name)
        if not recipient or recipient is teacher or recipient["incapacitated"]:
            return
        skill_kind = next((k for k in SKILL_KINDS if k in text_lower), None)
        if not skill_kind:
            # No specific skill named -- teach whichever the teacher is best at.
            teacher_skills = teacher.get("skills") or {}
            skill_kind = max(SKILL_KINDS, key=lambda k: teacher_skills.get(k, 0.0))
        teacher_level = self._skill_level(teacher, skill_kind)
        if teacher_level <= 0:
            return
        recipient_skills = recipient.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        transfer = teacher_level * TEACH_TRANSFER_FRACTION
        before = recipient_skills.get(skill_kind, 0.0)
        recipient_skills[skill_kind] = min(SKILL_MAX_LEVEL, before + transfer)
        if recipient_skills[skill_kind] <= before:
            return
        teacher["lastTeachFrame"] = self.frameTick
        self.civilization["teachCount"] = self.civilization.get("teachCount", 0) + 1
        self._push_activity(f"{teacher['name']} taught {recipient['name']} some {skill_kind} skill.")
        self._push_memory(recipient, f"Learned {skill_kind} from {teacher['name']}")

    # --- Phase G: library knowledge persistence (CULTURE_ENABLED) ---
    def _library_active(self, district_id=None):
        if not CULTURE_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        return self._working_structure_count("library", district_id) > 0

    def _library_upgrade_weight(self, district_id, cap=10):
        """Best local working Library's bounded upgrade contribution. `cap`
        defaults to 10 for knowledge-capacity scaling; study-gain callers pass
        LIBRARY_STUDY_WEIGHT_CAP (5) to keep skill-by-study bounded."""
        if not LIBRARY_SCALING_ENABLED:
            return 1
        libraries = [s for s in self.civilization["structures"]
                     if s.get("type") == "library"
                     and (district_id is None or s.get("districtId") == district_id)
                     and not s.get("isRuin")
                     and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD]
        return min(cap, max((self._structure_upgrade_weight(s) for s in libraries), default=1))

    def _library_lessons(self, district_id):
        if not self._library_active(district_id):
            return None
        knowledge = sorted(self.civilization.get("libraryKnowledge") or [],
                           key=lambda k: k.get("level", 0), reverse=True)[:3]
        chronicle = (self.civilization.get("chronicle") or [])[-2:]
        parts = [f"{k.get('skill')} {k.get('level')} ({k.get('agent')})" for k in knowledge]
        parts.extend(str(c.get("text", "")) for c in chronicle)
        return " | ".join(parts)[:480] or None

    def _store_knowledge_on_death(self, agent):
        """Library (#2): while a working Library exists, a dying agent's
        single best (non-trivial) skill is preserved in
        civilization["libraryKnowledge"] so it remains learnable via
        _study_at_library even though the agent is gone -- "death matters
        without erasing progress". Capped (LIBRARY_KNOWLEDGE_CAP): the
        weakest stored entry retires first, the same discipline as blueprint/
        custom-resource retirement elsewhere in the file, so a long soak
        can't grow this list forever."""
        if not CULTURE_ENABLED or not self._library_active():
            return
        skills = agent.get("skills") or {}
        best_kind = max(SKILL_KINDS, key=lambda k: skills.get(k, 0.0), default=None)
        if not best_kind or skills.get(best_kind, 0.0) < SKILL_PRACTICE_GAIN:
            return
        c = self.civilization
        knowledge = c.setdefault("libraryKnowledge", [])
        knowledge.append({"agent": agent["name"], "skill": best_kind,
                          "level": round(skills[best_kind], 2), "frame": self.frameTick})
        cap = LIBRARY_KNOWLEDGE_CAP * self._library_upgrade_weight(None)
        while len(knowledge) > cap:
            weakest = min(range(len(knowledge)), key=lambda i: knowledge[i]["level"])
            knowledge.pop(weakest)
        self._push_activity(f"{agent['name']}'s knowledge of {best_kind} is preserved in the Library.")
        self._push_chronicle(f"{agent['name']}'s {best_kind} knowledge was preserved in the Library.",
                             kind="knowledge_preserved")

    def _maybe_study_at_library(self):
        """Deterministic backstop (#2, the "study there via a goal" idiom
        without a new decision action/schema field): any living agent
        currently standing in a district with a working Library who has room
        to learn a stored skill studies it for free, tick-gated like every
        other _maybe_* backstop. A newcomer/child naturally has the most
        headroom (skills start at 0), so this is exactly the mechanism by
        which death stops being total knowledge loss."""
        if not CULTURE_ENABLED or not self._library_active():
            return
        library_districts = {s.get("districtId") for s in self.civilization["structures"]
                             if s.get("type") == "library" and not s.get("isRuin")
                             and (not GOODS_ENABLED or s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)}
        for agent in self.agents:
            if agent.get("deathFrame") is not None or agent["incapacitated"]:
                continue
            if agent.get("currentDistrict") not in library_districts:
                continue
            summary = self._study_at_library(agent)
            if summary:
                self._push_activity(summary)
                self._push_memory(agent, summary)

    def _study_at_library(self, agent):
        """A living agent studying at a working Library gains
        LIBRARY_STUDY_GAIN toward the strongest stored skill they don't
        already exceed -- the mechanism by which a newcomer/child can still
        learn a dead specialist's craft. Returns a summary string, or None if
        there's nothing to study (deterministic escape: no knowledge stored
        yet, or the agent already exceeds every stored entry)."""
        if not CULTURE_ENABLED or not self._library_active():
            return None
        knowledge = self.civilization.get("libraryKnowledge") or []
        if not knowledge:
            return None
        agent_skills = agent.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        best = max(knowledge, key=lambda k: k["level"]
                   if k["level"] > agent_skills.get(k["skill"], 0.0) else -1)
        if best["level"] <= agent_skills.get(best["skill"], 0.0):
            return None
        before = agent_skills.get(best["skill"], 0.0)
        gain = LIBRARY_STUDY_GAIN * self._library_upgrade_weight(
            agent.get("currentDistrict"), cap=LIBRARY_STUDY_WEIGHT_CAP)
        agent_skills[best["skill"]] = min(SKILL_MAX_LEVEL, before + gain)
        return f"{agent['name']} studied {best['skill']} at the Library (from {best['agent']}'s preserved knowledge)"

    # --- Phase G: chronicle (CULTURE_ENABLED) ---
    def _push_chronicle(self, text, kind="event"):
        """Village-level ring of major events (#3), STORED in civilization
        state (not just activity.jsonl) so it survives restarts and can be
        summarized into prompts. Capped at CHRONICLE_CAP; oldest drops first."""
        if not CULTURE_ENABLED:
            return
        chronicle = self.civilization.setdefault("chronicle", [])
        chronicle.append({"text": text, "frame": self.frameTick, "kind": kind})
        if len(chronicle) > CHRONICLE_CAP:
            del chronicle[:-CHRONICLE_CAP]

    def _chronicle_prompt_line(self):
        """Compact 'Village history: ...' line folding the most recent
        CHRONICLE_PROMPT_ENTRIES entries -- the whole reason the chronicle is
        stored rather than just logged (the civilization test needs it
        legible to the LLM, not just to a human reading activity.jsonl)."""
        if not CULTURE_ENABLED:
            return None
        chronicle = self.civilization.get("chronicle") or []
        if not chronicle:
            return None
        recent = chronicle[-CHRONICLE_PROMPT_ENTRIES:]
        return "; ".join(e["text"] for e in recent)

    # --- Phase G: personality drift (CULTURE_ENABLED) ---
    def _drift_personality(self, agent, trait):
        """Major life events append one short deterministic trait clause to
        the agent's persona (#4) -- persona already flows into the prompt's
        personality line at zero extra template cost, matching Phase F's
        life-stage fold-in. Capped (PERSONALITY_DRIFT_CAP) so a long-lived
        elder's persona string can't grow without bound; a new trait bumps
        out the oldest once at the cap."""
        if not CULTURE_ENABLED or not trait:
            return
        traits = agent.setdefault("personalityTraits", [])
        if trait in traits:
            return
        traits.append(trait)
        if len(traits) > PERSONALITY_DRIFT_CAP:
            del traits[:-PERSONALITY_DRIFT_CAP]

    def _personality_with_drift(self, agent):
        """Folds drift traits into the existing personality string at build
        time -- no new prompt template line (matching Phase F's life-stage
        fold-in), so flag-off/no-drift-yet prompts render byte-identically
        to the base personality text."""
        base = agent.get("personality") or ""
        if not CULTURE_ENABLED:
            return base
        traits = agent.get("personalityTraits") or []
        if not traits:
            return base
        return f"{base}, {', '.join(traits)}" if base else ", ".join(traits)

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

    def _role_is_filled(self, roles):
        """True if any living, able agent currently holds one of the roles."""
        role_set = set(roles) if not isinstance(roles, str) else {roles}
        return any(
            a["role"] in role_set
            and a.get("deathFrame") is None
            and not a["incapacitated"]
            for a in self.agents
        )

    def _village_needed_role(self):
        """Return a gather role the village needs, or None.

        Checks three need sources in priority order (build gap, survival,
        ecology). Sid-parity Phase 1: specialization must rebalance to real
        collective need, not only stalled builds.
        """
        if not EMERGENT_ROLES:
            return None

        # 1) Build-project gather gap (original signal).
        if self._active_project_districts():
            unmet = self._first_unmet_resource_anywhere()
            if unmet:
                roles = self.d["RESOURCE_GATHER_ROLES"].get(unmet)
                if roles and not self._role_is_filled(roles):
                    return roles[0]

        # 2) Survival need: starving agents and no living food/fish gatherer.
        if SURVIVAL_ENABLED:
            living = self._living_agents()
            starving = [
                a for a in living
                if not a["incapacitated"] and a["hunger"] <= STARVING_HUNGER
            ]
            if len(starving) >= ROLE_STARVE_NEED_THRESHOLD:
                food_roles = []
                for rid in EDIBLE_RESOURCES:
                    food_roles.extend(self.d["RESOURCE_GATHER_ROLES"].get(rid) or ())
                # Prefer farmer (food) over fisher when both are missing.
                for role in food_roles:
                    if not self._role_is_filled(role):
                        return role

        # 3) Ecology need: a tracked resource is depleted/low village-wide
        # and its gather role is unfilled.
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            # Aggregate stock ratio per resource across districts; pick the
            # scarcest unfilled gather role.
            totals = {}
            for stocks in self.civilization["districtStocks"].values():
                for rid, val in stocks.items():
                    max_s = self._stock_max(rid)
                    if not max_s:
                        continue
                    entry = totals.setdefault(rid, {"sum": 0.0, "max": 0.0})
                    entry["sum"] += val
                    entry["max"] += max_s
            scarce = []
            for rid, entry in totals.items():
                if entry["max"] <= 0:
                    continue
                ratio = entry["sum"] / entry["max"]
                if ratio > STOCK_LOW_RATIO:
                    continue
                roles = self.d["RESOURCE_GATHER_ROLES"].get(rid)
                if not roles or self._role_is_filled(roles):
                    continue
                scarce.append((ratio, roles[0]))
            if scarce:
                scarce.sort(key=lambda t: t[0])
                return scarce[0][1]

        return None

    def _auto_switch_candidate(self, needed_role):
        cands = [a for a in self.agents
                 if a.get("deathFrame") is None
                 and not a["incapacitated"] and a["role"] != needed_role
                 and self._is_flexible_role(a["role"])
                 and a["role"] not in AUTOSWITCH_PROTECTED_ROLES]
        if not cands:
            return None
        # Prefer agents whose flexible role is oversupplied (2+ living holders)
        # so specialization rebalances rather than randomly pulling any idle.
        role_counts = {}
        for a in self._living_agents():
            role_counts[a["role"]] = role_counts.get(a["role"], 0) + 1

        def sort_key(a):
            oversupplied = 0 if role_counts.get(a["role"], 0) >= 2 else 1
            idle = 0 if self._is_idle(a) else 1
            return (oversupplied, idle)

        cands.sort(key=sort_key)
        return cands[0]

    def _maybe_auto_switch_role(self):
        if not EMERGENT_ROLES:
            return
        c = self.civilization
        needed_role = self._village_needed_role()
        if not needed_role:
            c["roleNeedSinceFrame"] = None
            return
        if c.get("roleNeedSinceFrame") is None:
            c["roleNeedSinceFrame"] = self.frameTick
        if self.frameTick - c["lastRoleSwitchFrame"] < ROLE_SWITCH_COOLDOWN:
            return
        agent = self._auto_switch_candidate(needed_role)
        if not agent:
            return
        since = c.get("roleNeedSinceFrame")
        if since is not None:
            c["lastRoleRebalanceLatency"] = self.frameTick - since
        c["lastRoleSwitchFrame"] = self.frameTick
        c["roleNeedSinceFrame"] = None
        agent["goal"] = None
        unmet = self._first_unmet_resource_anywhere()
        reason = (
            f"The village has no one gathering {unmet}; "
            f"retraining to {needed_role} to fill the gap."
            if unmet else
            f"Village need requires a {needed_role}; retraining to fill the gap."
        )
        self.apply_decision(agent, {
            "action": "switch_role", "new_role": needed_role,
            "reasoning": reason})

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
        persist via state.db like any other agent."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return
        # Corpses remain in self.agents for burial; only the living occupy beds.
        if len(self._living_agents()) >= self._population_cap():
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

    # --- sage review (two-stage blueprint approval: SAGE_REVIEW_ENABLED) ---
    def _is_sage_reviewer(self, agent):
        """Who may perform the geography/resource review stage. No separate
        Sage role exists -- the current elder does both the review and the
        final approve/reject turn (two decisions, one agent); this predicate
        is the single swap point if that ever changes."""
        return agent["role"] == "elder"

    def _maybe_skip_sage_review(self):
        """A pending review that never lands (elder offline/incapacitated the
        whole window) auto-skips after SAGE_REVIEW_TIMEOUT_FRAMES instead of
        blocking approval forever -- same deadlock-avoidance shape as
        _maybe_amnesty_rejected_blueprints, for the review stage."""
        c = self.civilization
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if elder:
            return
        for bp in c["pendingBlueprints"]:
            if bp.get("sageReview") != "pending":
                continue
            proposed_at = bp.get("proposedFrame")
            if proposed_at is None or self.frameTick - proposed_at < SAGE_REVIEW_TIMEOUT_FRAMES:
                continue
            bp["sageReview"] = "skipped"
            bp["sageReviewReason"] = "sage unavailable; timeout auto-skip"
            bp["sageReviewFrame"] = self.frameTick
            self._push_activity(
                f"No elder was available to review {bp['name']} -- the review was skipped")

    def _maybe_amnesty_denied_sage_reviews(self):
        """A denied review just blocks approve_blueprint; give it the same
        amnesty clock as an outright reject_blueprint so it doesn't sit
        pending forever -- once BLUEPRINT_AMNESTY_FRAMES pass, it's popped and
        blacklisted the normal way (subject to the normal rejection amnesty)."""
        c = self.civilization
        for bp in list(c["pendingBlueprints"]):
            if bp.get("sageReview") != "denied":
                continue
            denied_at = bp.get("sageReviewFrame")
            if denied_at is None or self.frameTick - denied_at < BLUEPRINT_AMNESTY_FRAMES:
                continue
            c["pendingBlueprints"].remove(bp)
            c["rejectedBlueprintIds"].add(bp["id"])
            c.setdefault("rejectedBlueprintFrames", {})[bp["id"]] = self.frameTick
            self._push_activity(
                f"The sage's denial of {bp['name']} stands -- the proposal has been withdrawn")

    def _resolve_project_lead(self, proposed_by_name):
        """The proposer leads their own approved project unless they're dead
        or incapacitated, in which case the most-idle available agent (same
        ordering _idle_agents_for_elder already uses for task assignment)
        takes over."""
        proposer = self._find_agent(proposed_by_name)
        if proposer and not proposer.get("incapacitated") and proposer in self._living_agents():
            return proposer["name"]
        able = [a for a in self._living_agents() if not a.get("incapacitated")]
        idle_able = [a for a in self._idle_agents_for_elder() if not a.get("incapacitated")]
        candidates = idle_able or able
        if not candidates:
            return None
        return candidates[0]["name"]

    def _district_matches_blueprint_geo(self, district_id, bp):
        """Lightweight siting check for approve_blueprint's optional
        target_district: the district must exist, be buildable, and not
        already host another active project."""
        c = self.civilization
        d = c["districts"].get(district_id)
        if not d or not d.get("build_grid"):
            return False, f"{district_id} is not a buildable district"
        if c["districtProjects"].get(district_id):
            return False, f"a project is already active in {district_id}"
        return True, None

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
        """Retain all invented resources; invention is intentionally unlimited.

        This hook remains as a compatibility no-op because older saves and the
        tick loop still reference it.
        """
        return

    # --- Phase D invention council (diegetic LLM-council; TECH_TREE_ENABLED) ---
    def _council_active(self):
        if not TECH_TREE_ENABLED:
            return None
        return self.civilization.get("councilActive")

    def _stamp_council_event(self, entry):
        """Attach frame + wall-clock time to a council transcript line."""
        out = dict(entry)
        out.setdefault("frame", self.frameTick)
        out.setdefault("ts", datetime.now(timezone.utc).isoformat())
        return out

    def _append_council_transcript(self, entry):
        council = self._council_active()
        if not council:
            return
        council.setdefault("transcript", []).append(self._stamp_council_event(entry))

    def _record_council_proposal(self, agent, bp, decision):
        """A propose_blueprint that lands while a council is in session becomes
        part of the debate record, and appears in-world as a speech bubble
        (staged debate: existing message/bubble mechanics only)."""
        c = self.civilization
        council = c.get("councilActive")
        if not council:
            return
        council.setdefault("proposals", []).append({
            "proposer": agent["name"], "id": bp["id"], "name": bp["name"],
            "needs": dict(bp["needs"]),
            "function_summary": self._function_summary(bp.get("function")),
        })
        if not decision.get("message"):
            agent["message"] = f"I propose the {bp['name']}!"
            agent["messageTimer"] = 240
        self._push_activity(
            f"Council: {agent['name']} lays the {bp['name']} before the elder "
            f"({len(council['proposals'])} proposal(s) on the table)")
        self._append_council_transcript({
            "type": "proposal",
            "frame": self.frameTick,
            "proposer": agent["name"],
            "blueprint_id": bp["id"],
            "blueprint_name": bp["name"],
            "function_summary": self._function_summary(bp.get("function")),
            "needs": dict(bp.get("needs") or {}),
            "message": decision.get("message") or agent.get("message"),
            "reasoning": str(decision.get("reasoning") or "")[:500],
        })

    def _clear_invention_retry_flags(self, council):
        """Clear the per-agent one-shot invention-retry guard (see
        _think_job's same-window retry) for every proposer once their
        council session ends -- verdict or TTL dissolve -- so the flag
        doesn't carry over and silently block a retry in a future council."""
        if not council:
            return
        for name in council.get("proposers") or []:
            member = self._find_agent(name)
            if member:
                member["inventionRetryUsed"] = False

    def _council_reject_pending(self, elder, target_id, reason):
        """Reject one pending blueprint as part of a comparative verdict:
        pops it, records the rejection (amnesty clock included), and routes
        the reason back to the proposer's next prompt -- the same feedback
        loop a standalone reject_blueprint uses, plus the per-candidate
        reason the council pattern requires."""
        c = self.civilization
        idx = next((i for i, p in enumerate(c["pendingBlueprints"])
                    if p["id"] == target_id), -1)
        if idx == -1:
            return None
        bp = c["pendingBlueprints"].pop(idx)
        c["rejectedBlueprintIds"].add(bp["id"])
        c.setdefault("rejectedBlueprintFrames", {})[bp["id"]] = self.frameTick
        proposer = self._find_agent(bp.get("proposedBy"))
        if proposer:
            proposer["lastBlueprintRejection"] = {
                "reason": f"the elder chose another design: {reason}",
                "frame": self.frameTick}
        return bp

    def _record_council_verdict(self, elder, approved_bp, decision):
        """Conclude a comparative judgment: process the optional
        verdict.rejections map (reject-the-rest-with-reasons in the same
        decision), log the comparison as a village event, persist the debate
        to councilLog (the viewer's Council panel), and stage the verdict
        as a longer-lived elder speech bubble."""
        c = self.civilization
        council = c.get("councilActive")
        verdict = decision.get("verdict")
        rejections = {}
        if isinstance(verdict, dict) and isinstance(verdict.get("rejections"), dict):
            for rid, reason in verdict["rejections"].items():
                if rid == approved_bp["id"]:
                    continue
                reason = str(reason or "the approved design served the village better")[:160]
                if self._council_reject_pending(elder, rid, reason):
                    rejections[rid] = reason
        if not council and not rejections:
            return  # plain single-blueprint approval: not a council event
        # Build the debate record. Prefer the live council's proposal list;
        # fall back to what we know (approved + rejected candidates).
        proposals = list((council or {}).get("proposals") or [])
        known_ids = {p["id"] for p in proposals}
        for bp in [approved_bp]:
            if bp["id"] not in known_ids:
                proposals.append({
                    "proposer": bp.get("proposedBy", "?"), "id": bp["id"],
                    "name": bp["name"], "needs": dict(bp.get("needs") or {}),
                    "function_summary": self._function_summary(bp.get("function")),
                })
        loser_names = [rid for rid in rejections]
        outcome = f"{approved_bp['name']} approved"
        if loser_names:
            outcome += f"; {len(loser_names)} rejected"
            first_reason = rejections[loser_names[0]]
            self._push_activity(
                f"Elder {elder['name']} chose the {approved_bp['name']} over "
                f"{', '.join(loser_names)}: {first_reason}")
        end_frame = self.frameTick
        start_frame = (council or {}).get("frame")
        transcript = list((council or {}).get("transcript") or [])
        losers_part = f" over {', '.join(loser_names)}" if loser_names else ""
        if not decision.get("message"):
            elder["message"] = (f"The council has spoken: we build the "
                                f"{approved_bp['name']}{losers_part}!")
        elder["messageTimer"] = 480
        transcript.append(self._stamp_council_event({
            "type": "verdict",
            "elder": elder["name"],
            "approved_id": approved_bp["id"],
            "approved_name": approved_bp["name"],
            "rejections": dict(rejections),
            "message": decision.get("message") or elder.get("message"),
            "reasoning": str(decision.get("reasoning") or "")[:500],
        }))
        record = {
            "frame": start_frame or end_frame,
            "end_frame": end_frame,
            "start_frame": start_frame,
            "ts": datetime.now(timezone.utc).isoformat(),
            "started_ts": (council or {}).get("ts"),
            "trigger": (council or {}).get("trigger") or "elder_review",
            "proposers": list((council or {}).get("proposers") or []),
            "proposals": proposals,
            "verdict": {"approved_id": approved_bp["id"],
                        "reasons_per_candidate": rejections},
            "outcome": outcome,
            "transcript": transcript,
        }
        log = c.setdefault("councilLog", [])
        log.insert(0, record)
        del log[COUNCIL_LOG_CAP:]
        if council:
            self._clear_invention_retry_flags(council)
            c["councilActive"] = None

    def _maybe_dissolve_council(self):
        """A council whose verdict never lands (elder offline, proposals all
        invalid) dissolves after COUNCIL_TTL_FRAMES -- the deterministic
        escape from a stuck councilActive state. Pending proposals stay in
        pendingBlueprints for the normal (non-comparative) review path."""
        if not TECH_TREE_ENABLED:
            return
        c = self.civilization
        council = c.get("councilActive")
        if not council:
            return
        if self.frameTick - council.get("frame", 0) < COUNCIL_TTL_FRAMES:
            return
        end_frame = self.frameTick
        transcript = list(council.get("transcript") or [])
        transcript.append(self._stamp_council_event({
            "type": "dissolve",
            "message": "dissolved without a verdict",
        }))
        record = {
            "frame": council.get("frame", end_frame),
            "end_frame": end_frame,
            "start_frame": council.get("frame"),
            "ts": datetime.now(timezone.utc).isoformat(),
            "started_ts": council.get("ts"),
            "trigger": council.get("trigger") or "invention_backstop",
            "proposers": list(council.get("proposers") or []),
            "proposals": list(council.get("proposals") or []),
            "verdict": None,
            "outcome": "dissolved without a verdict",
            "transcript": transcript,
        }
        log = c.setdefault("councilLog", [])
        log.insert(0, record)
        del log[COUNCIL_LOG_CAP:]
        self._clear_invention_retry_flags(council)
        c["councilActive"] = None
        self._push_activity("The invention council disperses without a verdict")

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
        if TECH_TREE_ENABLED and c.get("councilActive"):
            return  # a council is already deliberating
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        idle = [a for a in self._idle_agents_for_elder()
                if a["name"] != elder["name"] and not a.get("inventionTurn")]
        if c.get("inventionBackstopFires", 0) >= INVENTION_ELDER_TAKEOVER or not idle:
            c["inventionRequiredStreak"] = 0
            c["inventionBackstopFires"] = 0
            elder["inventionTurn"] = True
            self._push_activity(f"Elder {elder['name']} will draft the new blueprint himself.")
            return
        c["inventionRequiredStreak"] = 0
        c["inventionBackstopFires"] = c.get("inventionBackstopFires", 0) + 1
        if TECH_TREE_ENABLED and len(idle) >= 2:
            # Invention COUNCIL (plan Part 6): 2-3 idle villagers get parallel
            # invention-only turns (each REPLACES that villager's next normal
            # think turn -- no added LLM call volume) and walk to the elder;
            # the elder judges the proposals comparatively when they land.
            members = idle[:INVENTION_COUNCIL_SIZE]
            names = [m["name"] for m in members]
            for m in members:
                m["inventionTurn"] = True
                m["goal"] = None
                m["assignedTask"] = "bring the council a new structure blueprint (propose_blueprint)"
                m["lastTaskedFrame"] = self.frameTick
                self._set_agent_target_to_agent(m, elder["name"])
            c["councilActive"] = {
                "frame": self.frameTick,
                "ts": datetime.now(timezone.utc).isoformat(),
                "trigger": "invention_backstop",
                "proposers": names,
                "proposals": [],
                "transcript": [],
            }
            elder["message"] = f"The council convenes! {', '.join(names)}, bring me your inventions."
            elder["messageTimer"] = 360
            self._append_council_transcript({
                "type": "convene",
                "frame": self.frameTick,
                "elder": elder["name"],
                "proposers": names,
                "message": elder["message"],
            })
            self._push_communication(
                "directive", elder["name"], "everyone",
                f"Invention council: {', '.join(names)} will each draft a blueprint")
            self._push_activity(
                f"Elder {elder['name']} convenes an invention council — "
                f"{', '.join(names)} will each draft a proposal")
            return
        # Legacy single-delegation path (flag off, or only one villager idle --
        # the council never fans out in that case, per the cost guard).
        target = idle[0]
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

    # --- agent-driven structure reorganization (fixes footprint overlaps) ---
    def _find_relocation_spot(self, structure):
        """Size-aware relocation destination for `structure`: prefer a free
        spot in its own district (excluding itself from the collision check,
        since it's the thing being moved), else another buildable district of
        the same kind with no active project (mirrors
        _maybe_relocate_stuck_project's same-kind-district fallback).
        Returns (district_id, x, y) or None."""
        footprint = self._structure_footprint(structure)
        own_district = structure.get("districtId")
        if own_district:
            spot = self._find_structure_spot(own_district, footprint=footprint,
                                             ignore_id=structure.get("id"))
            if spot:
                return own_district, spot["x"], spot["y"]
        c = self.civilization
        kind = c["districts"].get(own_district, {}).get("kind") if own_district else None
        if not kind:
            return None
        for did in self._buildable_district_ids():
            if did == own_district:
                continue
            if c["districts"][did]["kind"] != kind:
                continue
            if c["districtProjects"].get(did):
                continue
            spot = self._find_structure_spot(did, footprint=footprint, ignore_id=structure.get("id"))
            if spot:
                return did, spot["x"], spot["y"]
        return None

    def _enqueue_reorg_for_overlaps(self, structure, preferred_agent=None):
        """Enqueue (at most) one relocation task for the smaller of `structure`
        and any structure it overlaps. Ruins are kept in the collision check
        (still occupy their footprint visually) so they can still be the
        mover or the displacer. If a destination can't be found, emit a
        single throttled activity nudge and leave the overlap for the next
        gate/founding cycle to resolve."""
        c = self.civilization
        tasked_ids = {t["structureId"] for t in c["reorgTasks"]}
        if structure.get("id") in tasked_ids:
            return
        for other in c["structures"]:
            if other.get("id") == structure.get("id") or other.get("id") in tasked_ids:
                continue
            if not self._structures_overlapping(structure, other):
                continue
            w1, h1 = self._structure_footprint(structure)
            w2, h2 = self._structure_footprint(other)
            area1, area2 = w1 * h1, w2 * h2
            if area1 < area2:
                mover, displacer = structure, other
            elif area2 < area1:
                mover, displacer = other, structure
            else:
                # Tie: relocate the higher id (the newer/duplicate one).
                mover, displacer = (structure, other) if structure["id"] > other["id"] \
                    else (other, structure)
            mover_name = mover.get("name") or mover.get("type")
            dest = self._find_relocation_spot(mover)
            if not dest:
                if self.frameTick - c.get("lastReorgNoRoomFrame", 0) >= REORG_NO_ROOM_NUDGE_FRAMES:
                    c["lastReorgNoRoomFrame"] = self.frameTick
                    self._push_activity(
                        f"No room to relocate the {mover_name} -- it stays crowded for now")
                continue
            to_district, to_x, to_y = dest
            task = {
                "structureId": mover["id"], "toDistrict": to_district,
                "toX": to_x, "toY": to_y,
                "displacedBy": displacer.get("name") or displacer.get("type"),
                "assignedTo": None, "workLeft": 3, "createdFrame": self.frameTick,
            }
            c["reorgTasks"].append(task)
            if (preferred_agent is not None and preferred_agent.get("role") != "elder"
                    and not preferred_agent.get("incapacitated")
                    and not preferred_agent.get("reorgTask")
                    and preferred_agent.get("deathFrame") is None):
                task["assignedTo"] = preferred_agent["name"]
                preferred_agent["reorgTask"] = mover["id"]
                self._push_activity(
                    f"{preferred_agent['name']} sets out to relocate the {mover_name} — "
                    f"the {task['displacedBy']} has outgrown its plot")
            return  # one task enqueued per call

    def _maybe_reorganize_structures(self):
        """Periodic backstop (~every REORG_CHECK_FRAMES): keeps at most one
        reorg task in flight -- reassigns a task whose agent died/collapsed,
        assigns an unassigned task (preferring the builder, else the nearest
        able agent), and, when no task is pending, scans all structure pairs
        for a footprint overlap to enqueue. The elder and any current
        sage-emergency responder are never assigned -- Sage priority stays
        absolute."""
        c = self.civilization
        if self.frameTick - c.get("lastReorgCheckFrame", 0) < REORG_CHECK_FRAMES:
            return
        c["lastReorgCheckFrame"] = self.frameTick
        em_target = self._sage_emergency()
        protected = self._sage_responders(em_target) if em_target else set()

        def unavailable(a):
            return (a["role"] == "elder" or a["incapacitated"]
                    or a.get("deathFrame") is not None or a["name"] in protected)

        tasks = c["reorgTasks"]
        if tasks:
            task = tasks[0]
            assignee = self._find_agent(task["assignedTo"]) if task.get("assignedTo") else None
            if task.get("assignedTo") and (not assignee or assignee["incapacitated"]
                                            or assignee.get("deathFrame") is not None):
                if assignee:
                    assignee["reorgTask"] = None
                task["assignedTo"] = None
            if not task.get("assignedTo"):
                structure = next((s for s in c["structures"]
                                  if s.get("id") == task["structureId"]), None)
                if not structure:
                    tasks.remove(task)
                    return
                candidate = next((a for a in self.agents if a["role"] == "builder"
                                  and not unavailable(a) and not a.get("reorgTask")), None)
                if not candidate:
                    nearest, nearest_d = None, float("inf")
                    for a in self.agents:
                        if unavailable(a) or a.get("reorgTask"):
                            continue
                        dd = _dist(a["x"], a["y"], structure.get("x", 0), structure.get("y", 0))
                        if dd < nearest_d:
                            nearest_d, nearest = dd, a
                    candidate = nearest
                if candidate:
                    task["assignedTo"] = candidate["name"]
                    candidate["reorgTask"] = structure["id"]
                    name = structure.get("name") or structure.get("type")
                    self._push_activity(
                        f"{candidate['name']} sets out to relocate the {name} — "
                        f"the {task['displacedBy']} has outgrown its plot")
            return
        # No task in flight: scan for the first overlapping pair and enqueue.
        structures = c["structures"]
        for i, s1 in enumerate(structures):
            for s2 in structures[i + 1:]:
                if self._structures_overlapping(s1, s2):
                    self._enqueue_reorg_for_overlaps(s1)
                    return

    def _step_reorg(self, agent):
        """Deterministic reorg stepping, modeled on _rush_to_heal: walk to the
        tasked structure, work a fixed timer, then rewrite its position once
        a destination is (re-)confirmed still free. Never lets a reorg-tasked
        agent fall through to LLM thinking -- the per-agent tick loop calls
        this instead of dispatching a think job while agent['reorgTask'] is set."""
        c = self.civilization
        structure_id = agent.get("reorgTask")
        task = next((t for t in c["reorgTasks"] if t["structureId"] == structure_id), None)
        structure = next((s for s in c["structures"] if s.get("id") == structure_id), None)
        if not task or not structure:
            if task:
                c["reorgTasks"].remove(task)
            agent["reorgTask"] = None
            return
        if agent["incapacitated"]:
            task["assignedTo"] = None
            agent["reorgTask"] = None
            return
        fw, fh = self._structure_footprint(structure)
        sx = structure.get("x", 0) + fw / 2
        sy = structure.get("y", 0) + fh / 2
        if _dist(agent["x"], agent["y"], sx, sy) > 80:
            agent["targetX"] = sx
            agent["targetY"] = sy
            agent["waypoints"] = []
            return
        task["workLeft"] = task.get("workLeft", 3) - 1
        if task["workLeft"] > 0:
            return
        # Work complete -- re-validate the destination (something else may
        # have claimed the slot since the task was enqueued).
        to_district = task["toDistrict"]
        spot = self._find_structure_spot(to_district, footprint=(fw, fh),
                                         ignore_id=structure.get("id"))
        if spot:
            tx, ty = spot["x"], spot["y"]
        else:
            dest = self._find_relocation_spot(structure)
            if not dest:
                name = structure.get("name") or structure.get("type")
                self._push_activity(
                    f"{agent['name']} finds no room to relocate the {name} -- giving up for now")
                c["reorgTasks"].remove(task)
                agent["reorgTask"] = None
                return
            to_district, tx, ty = dest
        structure["x"] = tx
        structure["y"] = ty
        structure["districtId"] = to_district
        name = structure.get("name") or structure.get("type")
        self._push_activity(
            f"{agent['name']} relocated the {name} to make room for the {task['displacedBy']}")
        self._log_benchmark("structure_relocated", structure["id"],
                            {"structure": structure.get("type"), "district": to_district})
        c["reorgTasks"].remove(task)
        agent["reorgTask"] = None
        c["lastReorgFrame"] = self.frameTick

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
        if LIFECYCLE_ENABLED and c.get("pendingSuccession"):
            # Election backstop: cast a ballot for the first still-eligible
            # candidate found (deterministic, round-robins across candidates
            # rule-by-rule rather than always favoring pendingRules[0], so a
            # quiet soak doesn't mechanically crown whichever candidate
            # happened to be listed first). This guarantees the arc completes
            # even with zero organic LLM votes; _maybe_resolve_stalled_succession
            # is the final backstop if even this never fires.
            succession_rules = [r for r in c["pendingRules"] if r["kind"] == "succession"]
            for pending in succession_rules:
                eligible = [a for a in self.agents
                           if not a["incapacitated"] and a["role"] != "elder"
                           and a["name"] not in pending["votes"]]
                voter = next((a for a in eligible if self._is_idle(a)), None) or (eligible[0] if eligible else None)
                if voter:
                    self.apply_decision(voter, {
                        "action": "vote_rule", "target": pending["id"], "vote": "yes",
                        "reasoning": f'Casting my vote for {pending["candidateName"]} as the new elder.'})
                    return
            return
        pending = c["pendingRules"][0] if c["pendingRules"] else None
        if pending:
            eligible = [a for a in self.agents
                        if not a["incapacitated"] and a["role"] != "elder"
                        and a["name"] not in pending["votes"]]
            voter = next((a for a in eligible if self._is_idle(a)), None) or (eligible[0] if eligible else None)
            if voter:
                biased = self._belief_biased_vote(voter, pending)
                if biased is not None:
                    vote = biased
                else:
                    vote = "no" if (pending["kind"] == "resource_tax" and (pending.get("value") or 0) > 2) else "yes"
                reason = f'Casting my vote on the proposed rule "{pending["name"]}".'
                if biased is not None:
                    reason = f'My beliefs lead me to vote {vote} on "{pending["name"]}".'
                self.apply_decision(voter, {"action": "vote_rule", "target": pending["id"],
                                            "vote": vote,
                                            "reasoning": reason})
            return
        if self.frameTick - c["lastRuleActivityFrame"] < RULE_PROPOSE_COOLDOWN:
            return
        elder = next((a for a in self.agents if a["role"] == "elder" and not a["incapacitated"]), None)
        if not elder:
            return
        # Sid-parity Phase 2: once a tax exists, propose a priority rule for
        # the scarcest unmet build resource (or wood) so governance diversifies.
        if self._active_resource_tax() > 0 and not self._active_priority_resource():
            unmet = self._first_unmet_resource_anywhere() or "wood"
            if unmet in c["resourceRegistry"]:
                self.apply_decision(elder, {
                    "action": "propose_rule",
                    "rule": {
                        "id": f"priority_{unmet}",
                        "name": f"{unmet.title()} Priority",
                        "kind": "priority",
                        "value": unmet,
                        "description": f"Contributors prioritize delivering {unmet} to active builds.",
                    },
                    "reasoning": f"Proposing a priority rule so the village focuses on {unmet}.",
                })
                return
        # If several rules are stacked, propose repealing the oldest non-tax
        # rule so amendment is exercised (Sid's amendable-rules benchmark).
        # Age-gated (RULE_REPEAL_MIN_AGE_FRAMES): without this, tax+priority --
        # the normal 2-rule steady state -- meant this branch fired the very
        # next cooldown window after the propose branch ever enacted a
        # priority rule, undoing it immediately and oscillating forever.
        non_tax = [r for r in c["rules"] if r.get("kind") != "resource_tax"]
        repeal_eligible = [r for r in non_tax
                          if self.frameTick - r.get("enactedFrame", 0) >= RULE_REPEAL_MIN_AGE_FRAMES]
        if len(c["rules"]) >= 2 and repeal_eligible:
            target = repeal_eligible[0]
            self.apply_decision(elder, {
                "action": "repeal_rule",
                "target": target["id"],
                "reasoning": f'Repealing outdated rule "{target["name"]}" to keep village law lean.',
            })
            return
        if self._active_resource_tax() > 0:
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
        adoption_by_meme = self._meme_adoption_counts()
        adoption = self._meme_adoption_count()
        living_n = len([a for a in self.agents if a.get("deathFrame") is None]) or len(self.agents) or 1
        adoption_rate = adoption / living_n
        self.lastBenchmarks = {
            "entropy": entropy, "adherence": adherence, "adoption": adoption,
            "adoptionRate": adoption_rate, "adoptionByMeme": adoption_by_meme,
            "moduleTotal": self._module_period_runs,
            "rules": len(self.civilization["rules"]),
            "structures": len(self.civilization["structures"]),
            "level": self.civilization["level"], "memory": self.lastMemorySize,
            "effectThroughput": self._effect_period_fired,
            "ecologyScarcity": self._ecology_scarcity_index(),
            "roleRebalanceLatency": self.civilization.get("lastRoleRebalanceLatency"),
            "ruleKindDiversity": len(self.civilization.get("ruleKindsEverEnacted") or []),
        }
        if TECH_TREE_ENABLED:
            self.lastBenchmarks["era"] = self._current_era_name()
            self.lastBenchmarks["techTier"] = self._village_tech_tier()
        role_counts = {}
        for a in self.agents:
            role_counts[a["role"]] = role_counts.get(a["role"], 0) + 1
        self._log_benchmark("specialization_entropy", round(entropy, 2), {"counts": role_counts})
        latency = self.civilization.get("lastRoleRebalanceLatency")
        if EMERGENT_ROLES and latency is not None:
            self._log_benchmark("role_rebalance_latency", latency,
                                {"frames": latency})
        if adherence is not None:
            self._log_benchmark("rule_adherence", round(adherence, 2),
                                {"paid": self.civilization["taxPaid"], "due": self.civilization["taxDue"]})
        if RULES_ENABLED:
            kinds = list(self.civilization.get("ruleKindsEverEnacted") or [])
            self._log_benchmark("rule_kind_diversity", len(kinds), {"kinds": kinds})
        if MEMES_ENABLED:
            self._log_benchmark("meme_adoption", adoption,
                                {"rate": round(adoption_rate, 2),
                                 "by_meme": adoption_by_meme,
                                 "authored_beliefs": sum(1 for b in self._belief_registry().values()
                                                          if not b.get("seed")),
                                 "belief_pitch_calls": self.civilization.get("beliefPitchCalls", 0),
                                 "of": living_n})
        if PIANO_MODULES or META_SYSTEM:
            self._log_benchmark("module_total", self._module_period_runs,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._module_period_runs = 0
        if PIANO_MODULES:
            # Sid-parity Phase 1: surface module-pool health so regressions
            # (slow modules, timeout spikes) are visible in soak runs.
            latency = {
                module: round(total_ms / count, 1)
                for module, (total_ms, count) in self._piano_latency_ms.items()
                if count
            }
            self.lastBenchmarks["piano_module_latency"] = latency
            self.lastBenchmarks["piano_module_drops"] = self._piano_module_drops
            self._log_benchmark("piano_module_latency", latency,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._log_benchmark("piano_module_drops", self._piano_module_drops)
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
        if TECH_TREE_ENABLED:
            self._log_benchmark("era", self.civilization.get("eraIndex") or 0,
                                {"era": self._current_era_name(),
                                 "tech_tier": self._village_tech_tier()})
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
        if ECONOMY_ENABLED:
            gini = self._wealth_gini()
            if gini is not None:
                homeowners = sum(1 for a in self.agents if a.get("homeStructureId"))
                self._log_benchmark("wealth_gini", gini,
                                    {"market_active": self._market_active(),
                                     "homeowners": homeowners, "agents": len(self.agents)})
        if LIFECYCLE_ENABLED:
            c = self.civilization
            ages = sorted(a["age"] for a in self.agents if a.get("age") is not None)
            living = [a for a in self.agents if a.get("deathFrame") is None]
            if ages:
                median_age = ages[len(ages) // 2] if len(ages) % 2 else \
                    (ages[len(ages) // 2 - 1] + ages[len(ages) // 2]) / 2
                self._log_benchmark(
                    "population_median_age", round(median_age, 1),
                    {"population": len(living), "cap": self._population_cap(),
                     "births": c.get("births", 0), "deaths": c.get("deaths", 0),
                     "elder_age": round(next((a["age"] for a in self.agents
                                              if a["role"] == "elder"), 0) or 0, 1),
                     "population_floor_held": c.get("populationFloorHeld", False)})
        if CULTURE_ENABLED:
            c = self.civilization
            living = [a for a in self.agents if a.get("deathFrame") is None]
            avg_skills = {k: round(sum(a["skills"].get(k, 0.0) for a in living) / len(living), 2)
                         for k in SKILL_KINDS} if living else {k: 0.0 for k in SKILL_KINDS}
            self._log_benchmark(
                "skill_spread", round(sum(avg_skills.values()), 2),
                {"avg_by_kind": avg_skills, "teach_count": c.get("teachCount", 0),
                 "practice_count": c.get("skillPracticeCount", 0),
                 "library_knowledge_entries": len(c.get("libraryKnowledge") or [])})
            self._log_benchmark(
                "chronicle_size", len(c.get("chronicle") or []),
                {"meme_mutations": c.get("memeMutations", 0),
                 "belief_pitch_calls": c.get("beliefPitchCalls", 0)})

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

        elif action == "found_belief":
            summary = self._found_belief(agent, decision.get("belief"))

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
                        redirect = self._pickless_stone_route(agent, unmet)
                        if redirect:
                            summary = redirect
                        elif agent["currentZone"] != gz:
                            self._set_agent_target_once(agent, gz)
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
                self._maybe_spread_beliefs(
                    agent, recipient, decision["message"], decision.get("belief_pitch"),
                    decision.get("belief_pitch_quality"), decision.get("belief_pitch_scored", False))
                self._maybe_form_commitment(agent, recipient, decision["message"])
                if CULTURE_ENABLED:
                    self._maybe_teach(agent, recipient, decision["message"])
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
            if nearby and give and ECONOMY_ENABLED and self._market_active():
                summary = self._priced_trade(agent, target, give)
                resource_acted = give if "refused" not in summary else None
            elif nearby and give:
                agent["resources"][give] -= 1
                target["resources"][give] = target["resources"].get(give, 0) + 1
                self._nudge_ally(agent, target["name"])
                self._nudge_ally(target, agent["name"])
                self._push_memory(target, f"Received {give} from {agent['name']}")
                summary = f"{agent['name']} traded {give} to {target['name']}"
                resource_acted = give
                if ECONOMY_ENABLED:
                    agent["lastTradeRejection"] = None
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

        elif action == "upgrade_structure":
            if STRUCTURE_UPGRADES_ENABLED:
                summary = self._upgrade_structure(agent, decision.get("target"))
            else:
                summary = f"{agent['name']} cannot upgrade — structure upgrades are disabled"

        elif action == "submit_structure_sprite":
            if STRUCTURE_UPGRADES_ENABLED:
                summary = self._apply_structure_sprite(agent, decision.get("sprite"))
            else:
                summary = f"{agent['name']} cannot submit a sprite — upgrades are disabled"

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
                    redirect = self._pickless_stone_route(agent, unmet) if unmet else None
                    if redirect:
                        summary = redirect
                    elif unmet and gz and agent["currentZone"] != gz:
                        self._set_agent_target_once(agent, gz)
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
                    build_ctx = agent.get("inventionBuildContext") or {}
                    agent["inventionBuildContext"] = None
                    dup_owner = self._effect_vector_owner_map().get(
                        self._canonical_effect_vector(bp.get("function")))
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
                        "sageReview": "pending", "sageReviewReason": None, "sageReviewFrame": None,
                        "duplicateOf": dup_owner,
                        "proposedFrame": self.frameTick,
                        "requestedDistrict": build_ctx.get("district"),
                        "buildIntent": build_ctx.get("type"),
                        **({"tier": bp.get("tier") or 1} if TECH_TREE_ENABLED else {}),
                    })
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    c["inventionRequiredStreak"] = 0
                    c["inventionBackstopFires"] = 0
                    agent["lastBlueprintRejection"] = None
                    summary = f"{agent['name']} proposed {bp['name']} (needs {needs_str})"
                    if TECH_TREE_ENABLED:
                        self._record_council_proposal(agent, bp, decision)
                else:
                    agent["lastBlueprintRejection"] = {"reason": reason, "frame": self.frameTick}
                    summary = f"{agent['name']} drafted an invalid blueprint ({reason})"
                    if TECH_TREE_ENABLED and "tier" in (reason or ""):
                        self._log_benchmark(
                            "tier_gate_rejection", (bp or {}).get("tier") or 0,
                            {"kind": "blueprint", "target": (bp or {}).get("id"),
                             "village_tier": self._village_tech_tier()})

        elif action == "sage_review_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            sage_decision = decision.get("sage_decision")
            if self._is_sage_reviewer(agent) and idx != -1 and sage_decision in ("approve", "deny") \
                    and c["pendingBlueprints"][idx]["sageReview"] == "pending":
                bp = c["pendingBlueprints"][idx]
                bp["sageReview"] = "approved" if sage_decision == "approve" else "denied"
                bp["sageReviewReason"] = decision.get("message") or decision.get("reasoning") or None
                bp["sageReviewFrame"] = self.frameTick
                if decision.get("message"):
                    agent["message"] = decision["message"]
                    agent["messageTimer"] = 180
                verb = "approved" if sage_decision == "approve" else "denied"
                summary = f"{agent['name']} {verb} the sage review of {bp['name']}"
            else:
                summary = f"{agent['name']} could not sage-review that blueprint"

        elif action == "approve_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            bp = c["pendingBlueprints"][idx] if idx != -1 else None
            review_ok = not SAGE_REVIEW_ENABLED or (bp and bp.get("sageReview") in ("approved", "skipped"))
            resolved = False
            if agent["role"] == "elder" and idx != -1 and review_ok:
                if bp.get("duplicateOf") and not self._structure_type_built(bp["duplicateOf"]):
                    # duplicateOf can name a seed/custom type that's registered
                    # but not yet standing (still under construction, or --
                    # since _effect_vector_owner_map also scans pendingBlueprints
                    # -- another proposal that hasn't even been approved yet).
                    # There is nothing to upgrade yet: leave the blueprint
                    # pending rather than popping it into a failed upgrade
                    # attempt, so the elder can retry once the original is
                    # built, or reject_blueprint it explicitly.
                    summary = (f"{agent['name']} cannot approve {bp['name']} as an upgrade yet -- "
                               f"{bp['duplicateOf']} is not built yet. Wait for it to be built, "
                               f"or reject_blueprint if it's unnecessary.")
                elif bp.get("duplicateOf"):
                    lead_agent = self._find_agent(bp.get("proposedBy")) or agent
                    upgrade_summary = self._upgrade_structure(lead_agent, bp["duplicateOf"])
                    c["pendingBlueprints"].pop(idx)
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    summary = (f"{agent['name']} approved {bp['name']} as an upgrade to "
                               f"{bp['duplicateOf']} -- {upgrade_summary}")
                    resolved = True
                else:
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
                        **({"tier": bp.get("tier") or 1} if TECH_TREE_ENABLED else {}),
                    }
                    c.setdefault("approvedCustomApprovedFrame", {})[bp["id"]] = self.frameTick
                    c["pendingBlueprints"].pop(idx)
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    summary = f"{agent['name']} approved {bp['name']} blueprint"
                    lead_name = self._resolve_project_lead(bp.get("proposedBy"))
                    target_district = decision.get("target_district") or bp.get("requestedDistrict")
                    geo_ok, geo_reason = self._district_matches_blueprint_geo(target_district, bp) \
                        if target_district else (False, None)
                    if target_district and not geo_ok:
                        summary += f" (ignored target_district {target_district}: {geo_reason})"
                    district_id = target_district if geo_ok else self._resolve_build_district(
                        agent, bp["id"], None)
                    if district_id and lead_name:
                        contributed = {res: 0 for res in bp["needs"]}
                        c["districtProjects"][district_id] = {
                            "type": bp["id"], "name": bp["name"], "needs": dict(bp["needs"]),
                            "contributed": contributed, "visualStyle": bp["visualStyle"],
                            "sprite": bp.get("sprite"), "districtId": district_id,
                            "lead": lead_name, "leadReassigned": None,
                        }
                        c["districtLastContribution"][district_id] = self.frameTick
                        if lead_name != bp.get("proposedBy"):
                            c["districtProjects"][district_id]["leadReassigned"] = {
                                "from": bp.get("proposedBy"), "to": lead_name, "frame": self.frameTick}
                            self._push_activity(
                                f"{bp.get('proposedBy')} unavailable to lead the {bp['name']} project -- "
                                f"{lead_name} takes over")
                        summary += f", started in {district_id} with {lead_name} as lead"
                    resolved = True
                if resolved and TECH_TREE_ENABLED:
                    self._record_council_verdict(agent, bp, decision)
            else:
                if bp and not review_ok:
                    summary = f"{agent['name']} cannot approve {bp['name']} -- sage review still pending"
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

        elif action == "propose_role":
            role = decision.get("role")
            ok, reason = self._validate_role(role)
            if ok:
                pending = dict(role)
                pending["specialty"] = list(role["specialty"])
                if isinstance(role["preferredProject"], list):
                    pending["preferredProject"] = list(role["preferredProject"])
                pending["proposedBy"] = agent["name"]
                pending["proposedFrame"] = self.frameTick
                c["pendingRoles"].append(pending)
                summary = f"{agent['name']} proposed the {role['name']} role"
            else:
                summary = f"{agent['name']} drafted an invalid role ({reason})"

        elif action == "approve_role":
            idx = next((i for i, p in enumerate(c["pendingRoles"])
                        if p.get("slug") == decision.get("target")), -1)
            if agent["role"] != "elder" or idx == -1:
                summary = f"{agent['name']} could not approve that role"
            else:
                role = c["pendingRoles"][idx]
                registry = c["roleRegistry"]
                seed_roles = set(self.d["ROLES"])
                emergent_count = len(set(registry) - seed_roles)
                if role["slug"] in registry or emergent_count >= MAX_EMERGENT_ROLES:
                    summary = f"{agent['name']} could not approve the {role['name']} role"
                else:
                    c["pendingRoles"].pop(idx)
                    registry[role["slug"]] = self._role_record(role)
                    self._rebuild_role_maps()
                    summary = f"{agent['name']} approved the {role['name']} role"

        elif action == "reject_role":
            idx = next((i for i, p in enumerate(c["pendingRoles"])
                        if p.get("slug") == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                role = c["pendingRoles"].pop(idx)
                summary = f"{agent['name']} rejected the {role['name']} role"
            else:
                summary = f"{agent['name']} could not reject that role"

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
                summary = f"Elder {agent['name']} tasked {target['name']}: {task_text}"
            else:
                summary = f"{agent['name']} could not assign that task"

        elif action == "change_role":
            if decision.get("new_role"):
                agent["role"] = decision["new_role"]
                summary = f"{agent['name']} became a {decision['new_role']}"

        elif action == "switch_role":
            new_role = decision.get("new_role") or decision.get("target")
            if EMERGENT_ROLES and new_role and new_role in c["roleRegistry"] and new_role != agent["role"]:
                old = agent["role"]
                agent["role"] = new_role
                agent["assignedTask"] = None
                agent["idleCycles"] = 0
                summary = f"{agent['name']} switched role from {old} to {new_role}"
            else:
                summary = f"{agent['name']} kept the {agent['role']} role"

        elif action == "propose_rule":
            summary = self._propose_rule(agent, decision)

        elif action == "repeal_rule":
            summary = self._propose_repeal(agent, decision)

        elif action == "vote_rule":
            summary = self._vote_on_rule(agent, decision)

        elif action == "heal_agent":
            patient = self._find_agent(decision.get("target")) if decision.get("target") else None
            dead_target = patient["name"] if patient is not None and patient.get("deathFrame") is not None else None
            if dead_target:
                patient = None
            if not patient or (patient["health"] >= 100 and not patient["incapacitated"]):
                patient = self._neediest_nearby(agent)
            if not patient:
                summary = (f"{agent['name']} cannot revive {dead_target} — they have passed away"
                           if dead_target else f"{agent['name']} found no one to heal")
            elif self._distance_to(agent, patient) > 80:
                self._auto_move_toward_target(agent, patient["name"])
                summary = f"{agent['name']} moves to help {patient['name']}"
            else:
                boost = HEAL_AMOUNT * 2 if agent["role"] == "healer" else HEAL_AMOUNT
                if CULTURE_ENABLED:
                    boost += self._skill_level(agent, "heal") * SKILL_HEAL_BONUS_PER_LEVEL
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
                if CULTURE_ENABLED:
                    self._practice_skill(agent, "heal")
                summary = f"{agent['name']} healed {patient['name']}"

        elif action == "bury_agent":
            corpse = self._find_agent(decision.get("target")) if decision.get("target") else None
            if corpse is not None and (corpse.get("deathFrame") is None or corpse.get("buried")):
                corpse = None
            if not corpse:
                corpse = self._nearest_unburied_corpse(agent)
            if not corpse:
                summary = f"{agent['name']} found no one awaiting burial"
            else:
                cemeteries = self._working_cemeteries()
                if not cemeteries:
                    agent["lastBurialRejection"] = {
                        "reason": "no cemetery has been built yet",
                        "frame": self.frameTick,
                    }
                    summary = f"{agent['name']} wants to bury {corpse['name']} but no cemetery exists"
                elif self._distance_to(agent, corpse) > BURY_CONTACT_DIST:
                    self._auto_move_toward_target(agent, corpse["name"])
                    summary = f"{agent['name']} moves to lay {corpse['name']} to rest"
                else:
                    cemetery = min(cemeteries, key=lambda s: self._distance_to(agent, s))
                    self._bury_agent_at(cemetery, corpse, buried_by=agent)
                    summary = f"{agent['name']} buried {corpse['name']} in the Cemetery"

        elif action == "place_block":
            gx = gy = None
            target = decision.get("target") or ""
            if "," in str(target):
                parts = str(target).split(",")
                try:
                    gx, gy = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            block_type = decision.get("message") or decision.get("new_role") or "wall"
            if target and "," not in str(target):
                block_type = target
            summary = self._place_block(agent, block_type, gx, gy)

        elif action == "remove_block":
            gx = gy = None
            target = decision.get("target") or ""
            if "," in str(target):
                parts = str(target).split(",")
                try:
                    gx, gy = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            summary = self._remove_block(agent, gx, gy)

        elif action == "dig_terrain":
            summary = self._dig_terrain(agent)

        elif action == "plant_terrain":
            summary = self._plant_terrain(agent)

        elif action == "propose_treaty":
            summary = self._propose_treaty(agent, decision)

        elif action == "vote_treaty":
            summary = self._vote_treaty(agent, decision)

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
        if g["kind"] == "plant_terrain":
            self.apply_decision(agent, {"action": "plant_terrain", "reasoning": "goal:plant"})
            agent["goal"] = None
            return True
        if g["kind"] == "seek_shelter":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_agent_target_once(agent, dest)
                return True
            agent["goal"] = None
            return False
        if g["kind"] == "dig_relocate":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_agent_target_once(agent, dest)
                return True
            summary = self._dig_terrain(agent) or ""
            if "cannot dig" in summary:
                agent["goal"] = None
                return False
            if agent["resources"].get("stone", 0) >= self._carry_cap(agent):
                # Full load: release the goal so the LLM/fallback can route
                # the stone to a project via contribute_resources.
                agent["goal"] = None
                return False
            return True
        if g["kind"] == "caravan":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_agent_target_once(agent, dest)
                return True
            self._maybe_caravan_goal(agent)
            agent["goal"] = None
            return False
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
                                "lacks ", "built ", "could not", "cannot dig")):
            agent["goal"] = None
            return False
        return True

    def _apply_rule_based_fallback(self, agent):
        district_id = random.choice(list(self.civilization["districts"].keys()))
        self._set_agent_target(agent, district_id)
        self._push_memory(agent, f"{agent['name']} wandered toward {district_id}")
        self._push_activity(f"{agent['name']} wandered toward {district_id} (LLM fallback)")

    def _should_renudge(self, agent, kind, rejection_frame):
        """Per-kind rejection-nudge cooldown for P2 recovery notes.

        Without this, a rejection note re-fires on every think turn for the
        entire DIRECTIVE_TTL_FRAMES window even when nothing has changed,
        crowding out the fixed MAX_BEHAVIOR_NUDGES slots. Re-emit only if
        this is a NEW rejection (different frame than last nudged) or the
        cooldown has fully elapsed since this kind was last nudged.
        """
        last_nudged = agent.setdefault("lastRejectionNudgeFrame", {})
        last_frame_for_kind = last_nudged.get(kind)
        if last_frame_for_kind is None:
            allow = True
        elif rejection_frame != last_frame_for_kind.get("rejectionFrame"):
            allow = True
        elif self.frameTick - last_frame_for_kind.get("nudgedFrame", 0) >= DIRECTIVE_TTL_FRAMES:
            allow = True
        else:
            allow = False
        if allow:
            last_nudged[kind] = {"rejectionFrame": rejection_frame, "nudgedFrame": self.frameTick}
        return allow

    # --- LLM think job (runs in worker; builds payload, calls LM, applies) ---
    def _build_think_payload(self, agent):
        """Mirror index.html thinkAgent payload, computed under lock."""
        c = self.civilization
        nearby_detailed = self._get_nearby_detailed(agent)
        idle_agents = []
        if agent["role"] == "elder":
            # C3: cap at MAX_IDLE_AGENTS_PROMPT -- _idle_agents_for_elder is
            # already ordered least-recently-tasked first, so the slice keeps
            # the agents most in need of a task.
            for i, a in enumerate(self._idle_agents_for_elder()[:MAX_IDLE_AGENTS_PROMPT]):
                idle_agents.append({
                    "name": a["name"], "role": a["role"], "longest_idle": i == 0,
                    "contribution_debt": self.frameTick - (a["lastContributedFrame"] or 0),
                })

        actives = self._active_project_districts()
        invention_required = self._invention_required()
        # One-shot invention-only turn (set by _maybe_invention_backstop):
        # the server swaps in a slim, proposal-only prompt for this call.
        invention_turn = bool(agent.get("inventionTurn"))
        # inventionBuildContext deliberately survives past this point (unlike
        # inventionTurn) -- it's read later in apply_decision's propose_blueprint
        # branch, which runs after the async LLM round-trip, and clearing it
        # here would erase it before that branch ever sees it.
        invention_build_context = dict(agent["inventionBuildContext"]) \
            if invention_turn and agent.get("inventionBuildContext") else None
        if invention_turn:
            agent["inventionTurn"] = False
        sprite_design_turn = bool(agent.get("spriteDesignTurn"))
        nudges = []

        def note(prio, text):
            """Collect a (priority, text) nudge. Lower prio = more important.
            P0=emergency/survival, P1=governance/commitment,
            P2=rejection-recovery/stall, P3=opportunity/idle. Selection into
            the final behavior_nudge happens once, below, via MAX_BEHAVIOR_NUDGES."""
            nudges.append((prio, text))

        # Phase A1 (shadow-log only): tracked alongside the nudges below and
        # folded into high_stakes_reason near the end of this function. Purely
        # additive -- none of these locals feed a nudge or a decision.
        fresh_rejection_kinds = set()
        emergency_active = False
        elder_blueprint_review_active = False
        rejection = agent.get("lastBlueprintRejection")
        rejection_nudge = None
        if rejection and self.frameTick - rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("blueprint")
            rejection_nudge = (f"NOTE: Your last blueprint proposal was rejected: {rejection['reason']}. "
                               f"Propose a different blueprint that avoids that problem.")
            rejection_nudge += " Use a fresh non-seed id; never reuse a seed, approved, pending, or rejected id."
            note(2, rejection_nudge)
        gather_rejection = agent.get("lastGatherRejection")
        if gather_rejection and self.frameTick - gather_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("gather")
            if self._should_renudge(agent, "gather", gather_rejection.get("frame", 0)):
                reason_text = gather_rejection.get("reason") or ""
                if "pick" in reason_text:
                    note(2, f"NOTE: Your last gather failed: {reason_text}. "
                            f"Craft the required pick at the workshop (craft_item), or dig_terrain for stone.")
                else:
                    note(2, f"NOTE: Your last gather failed: {reason_text}. "
                            f"Contribute to an active terraform or start_terraform here before moving elsewhere.")
        terraform_rejection = agent.get("lastTerraformRejection")
        if terraform_rejection and self.frameTick - terraform_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("terraform")
            note(2, f"NOTE: Your last start_terraform failed: {terraform_rejection['reason']}. "
                    f"Use a template id (plant_grove/clear_field/extend_beach) or name the district.")
        craft_rejection = agent.get("lastCraftRejection")
        if craft_rejection and self.frameTick - craft_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("craft")
            if self._should_renudge(agent, "craft", craft_rejection.get("frame", 0)):
                note(2, f"NOTE: Your last craft failed: {craft_rejection['reason']}. "
                        f"Gather the missing input first.")
        if TECH_TREE_ENABLED:
            recipe_rejection = agent.get("lastRecipeRejection")
            if recipe_rejection and self.frameTick - recipe_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("recipe")
                if self._should_renudge(agent, "recipe", recipe_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last recipe proposal was refused: {recipe_rejection['reason']}.")
        project_rejection = agent.get("lastProjectRejection")
        if project_rejection and self.frameTick - project_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("project")
            if self._should_renudge(agent, "project", project_rejection.get("frame", 0)):
                note(2, f"NOTE: Your last start_project failed: {project_rejection['reason']}.")
        if STRUCTURE_UPGRADES_ENABLED:
            upgrade_rejection = agent.get("lastUpgradeRejection")
            if upgrade_rejection and self.frameTick - upgrade_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("upgrade")
                if self._should_renudge(agent, "upgrade", upgrade_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last upgrade failed: {upgrade_rejection['reason']}.")
            sprite_rejection = agent.get("lastSpriteRejection")
            if sprite_rejection and self.frameTick - sprite_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("sprite")
                note(2, f"NOTE: Your last sprite design was rejected: {sprite_rejection['reason']}.")
            upgradeable = self._upgradeable_structures_brief()
            if upgradeable and not sprite_design_turn:
                sample = upgradeable[:3]
                parts = ", ".join(
                    f"{u['name']} id {u['id']} Lv.{u['level']}" for u in sample)
                note(3,
                    f"NOTE: Upgrade existing facilities before building duplicates. "
                    f"Use upgrade_structure (target = structure id): {parts}.")
        if GOODS_ENABLED:
            repair_rejection = agent.get("lastRepairRejection")
            if repair_rejection and self.frameTick - repair_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("repair")
                if self._should_renudge(agent, "repair", repair_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last repair failed: {repair_rejection['reason']}.")
            spoilage = c.get("lastSpoilage")
            if spoilage and self.frameTick - spoilage.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(3, f"NOTE: {spoilage['reason']}.")
            shelter = agent.get("lastShelterNote")
            if shelter and self.frameTick - shelter.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(3, f"NOTE: {shelter['reason']}. More houses would fix this.")
            worst_local = min((s for s in c["structures"]
                               if s.get("districtId") == agent.get("currentDistrict")
                               and (s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD)),
                              key=lambda s: s.get("condition", 100), default=None)
            if worst_local:
                is_ruin = bool(worst_local.get("isRuin"))
                state_word = "in ruins" if is_ruin else "in disrepair and not working"
                note(1 if is_ruin else 2,
                     f"NOTE: The {worst_local.get('name') or worst_local.get('type')} here is "
                     f"{state_word} (condition {int(worst_local.get('condition', 0))}). "
                     f"Use repair_structure to restore it.")
            # Village-wide ruin-pressure nudge (P1): independent of the
            # agent's current district, fires when decay is widespread even
            # if the agent isn't standing next to the worst offender. Two
            # triggers: >25% of all structures are ruins, or an entire
            # structure category (house/market/workshop/foundry/granary/
            # farm_plot) has zero working instances village-wide.
            all_structures = c["structures"]
            if all_structures:
                ruin_count = sum(1 for s in all_structures if s.get("isRuin"))
                ruin_ratio_trigger = (ruin_count / len(all_structures)) > 0.25
                zero_working_category = False
                for kind in ("house", "market", "workshop", "foundry", "granary", "farm_plot"):
                    of_kind = [s for s in all_structures if s.get("type") == kind]
                    if of_kind and not any(
                            not s.get("isRuin") and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                            for s in of_kind):
                        zero_working_category = True
                        break
                if ruin_ratio_trigger or zero_working_category:
                    failing = sorted(
                        (s for s in all_structures
                         if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD),
                        key=lambda s: s.get("condition", 100))
                    worst_few = failing[:3]
                    if worst_few:
                        parts = ", ".join(
                            f"{s.get('name') or s.get('type')} ({s.get('districtId')}, "
                            f"condition {int(s.get('condition', 0))})" for s in worst_few)
                        note(1, f"NOTE: The village has {len(failing)} ruined/failing structures "
                                f"village-wide, including {parts}. Travel there and use repair_structure.")
        if ECONOMY_ENABLED:
            trade_rejection = agent.get("lastTradeRejection")
            if trade_rejection and self.frameTick - trade_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                if self._should_renudge(agent, "trade", trade_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last trade was refused: {trade_rejection['reason']}.")
            if not agent.get("homeStructureId") \
                    and (self.frameTick - (agent.get("lastHomelessNudgeFrame") or -HOMELESS_NUDGE_FRAMES)) \
                    >= HOMELESS_NUDGE_FRAMES:
                claimable = self._find_house_to_claim(agent)
                agent["lastHomelessNudgeFrame"] = self.frameTick
                if claimable:
                    note(3, "NOTE: You have no home, but an unclaimed house exists. "
                            "Be the one to repair_structure it (if damaged) or help build the "
                            "next house to claim it as your own.")
                else:
                    note(3, "NOTE: You have no home and no house is unclaimed. "
                            "Consider start_project to build a house -- the builder claims it.")
        if LIFECYCLE_ENABLED:
            quota_rejection = agent.get("lastQuotaRejection")
            if quota_rejection and self.frameTick - quota_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {quota_rejection['reason']}. "
                        f"Try a different resource or district, or wait for the quota to reset.")
            ration_rejection = agent.get("lastRationingRejection")
            if ration_rejection and self.frameTick - ration_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {ration_rejection['reason']}. "
                        f"Gather more or wait for storage to recover.")
            pending_succession = c.get("pendingSuccession")
            if pending_succession and agent["name"] not in \
                    next((r["votes"] for r in c["pendingRules"]
                         if r["kind"] == "succession"
                         and r.get("electionId") == pending_succession["electionId"]
                         and r.get("candidateName") == agent["name"]), {}):
                # An agent votes with vote_rule targeting the candidate's own
                # succession rule id (not the election as a whole) -- listing
                # every candidate's rule id here so the model has what it
                # needs without a new action verb.
                candidate_ids = ", ".join(
                    f"{r['candidateName']} (id {r['id']})" for r in c["pendingRules"]
                    if r["kind"] == "succession" and r.get("electionId") == pending_succession["electionId"])
                note(1, f"NOTE: The village elder has died. Vote for the next elder with vote_rule: "
                        f"{candidate_ids}. Set target to your preferred candidate's id and vote yes.")
        if CEMETERY_ENABLED:
            burial_rejection = agent.get("lastBurialRejection")
            if burial_rejection and self.frameTick - burial_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {burial_rejection['reason']}. "
                        f"Use start_project with target cemetery to build one.")
            unburied = next((a for a in self.agents
                             if a.get("deathFrame") is not None and not a.get("buried")), None)
            if unburied:
                if self._working_cemeteries():
                    note(3, f"NOTE: {unburied['name']} awaits burial. "
                            f"Use bury_agent (target {unburied['name']}) to lay them to rest in the Cemetery.")
                else:
                    note(3, f"NOTE: {unburied['name']} awaits burial but the village has no Cemetery yet. "
                            f"Use start_project with target cemetery.")
        abandonment = c.get("lastProjectAbandonment")
        if abandonment and self.frameTick - abandonment.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            note(2, f"NOTE: {abandonment['reason']}.")
        stalled_customs = self._stalled_approved_customs()
        if stalled_customs:
            pid, name, _ = stalled_customs[0]
            note(2, f"NOTE: The village approved {name} but never built it. "
                    f"Use start_project with target {pid}.")
        if ECOLOGY_ENABLED:
            stocks_line = self._format_district_stocks_for_prompt(agent)
            if ":depleted" in stocks_line or ":low" in stocks_line:
                note(3, f"NOTE: Local stocks are strained ({stocks_line}). "
                        f"Consider start_terraform (plant_grove/clear_field/extend_beach) or move_to_district.")
        if agent["assignedTask"] and \
                self.frameTick - (agent.get("lastTaskedFrame") or 0) > DIRECTIVE_TTL_FRAMES:
            # Same staleness problem as the directive: an old task (possibly
            # restored from state.db) shouldn't bias decisions forever.
            agent["assignedTask"] = None
        if agent["assignedTask"]:
            note(1, f"Your leader assigned you: {agent['assignedTask']}. Do it now.")
        if invention_required:
            note(1, "NOTE: All known structures are already built. The village needs a NEW "
                    "invention -- use propose_blueprint now.")
        ready = next((did for did in actives if self._is_project_complete(did)), None)
        if ready:
            note(3, f"PROJECT READY: the build in {ready} is fully funded. "
                    f"Use build_structure with target_district {ready} now.")
        if agent.get("commitment"):
            commitment = agent["commitment"]
            note(1, f'NOTE: You agreed to help {commitment["to"]}: "{commitment["text"]}". '
                    f'Honor it soon with collect_resource, contribute_resources, or '
                    f'trade_resource for {commitment["resource"]}.')
        if not actives:
            # Suppressed while invention is required: start_project would be
            # refused anyway, and the nudge pulls the model away from
            # propose_blueprint (the only action that unblocks progress).
            if not invention_required:
                note(3, "NOTE: No active project exists anywhere. Use start_project now to begin a build "
                        "(optionally set target_district to one of the known_districts ids).")
        elif agent["consecutiveTalks"] >= 2:
            note(3, "NOTE: You have chatted twice. Prioritize collect_resource, contribute_resources, or move_to_agent.")
        directive = self._current_directive()
        if agent["role"] != "elder" and directive:
            note(1, f"Your leader directs: {directive}. Prioritize it.")
        if agent.get("consecutiveIdleMoves", 0) >= 3:
            note(3, "NOTE: You have been moving without acting. Prioritize collect_resource or contribute_resources.")
        carry_cap = self._carry_cap(agent)
        capped = next(((k, v) for k, v in agent["resources"].items() if v >= carry_cap), None)
        if capped:
            note(3, f"NOTE: You are at capacity for {capped[0]} ({capped[1]}/{carry_cap}). "
                    f"Use contribute_resources or trade_resource instead of collecting more.")
        spec = self._role_specialty_resource(agent["role"])
        if spec and spec == self._first_unmet_resource_anywhere():
            note(3, f"NOTE: Your role specializes in {spec}, which an active project still needs. Prioritize collect_resource.")
        if EMERGENT_ROLES:
            need_role = self._village_needed_role()
            if need_role and need_role != agent["role"] and self._is_flexible_role(agent["role"]):
                unmet = self._first_unmet_resource_anywhere()
                if unmet:
                    note(3,
                        f"NOTE: No one is gathering {unmet}, which a build needs. "
                        f"Consider switch_role to {need_role} to fill the gap.")
                else:
                    note(3,
                        f"NOTE: The village needs a {need_role} (survival or scarce "
                        f"resources). Consider switch_role to {need_role} to fill the gap.")
        if RULES_ENABLED:
            unvoted = next((r for r in c["pendingRules"] if agent["name"] not in r["votes"]), None)
            if unvoted:
                note(1, f'NOTE: Pending rule "{unvoted["name"]}" (id {unvoted["id"]}) needs your vote. '
                        f"Use vote_rule with target {unvoted['id']} and vote yes or no.")
            elif (not c["rules"] and not c["pendingRules"]
                  and self.frameTick - c["lastRuleActivityFrame"] > BLUEPRINT_STALL_THRESHOLD):
                note(3, "NOTE: The village has no shared rules yet. Consider propose_rule (a small resource_tax builds a shared stockpile).")
        if agent["role"] == "elder" and c["pendingBlueprints"]:
            # Was gated at >=2 (the comparative council judgment): a LONE
            # valid proposal got no nudge at all and could sit unreviewed
            # indefinitely -- the elder's only other path to it was the
            # fallback-on-decision-failure branch in role_fallback_action,
            # which only fires by accident. Found live 2026-07-08: Marco's
            # "Storage House" validated on his own invention-only turn but
            # was never surfaced back to him because it was the only one
            # pending. Now covers n=1 too, with matching singular wording.
            #
            # SAGE_REVIEW_ENABLED splits the queue into three buckets: still
            # needs a geography/resource review pass, cleared and awaiting a
            # verdict, or denied at review (no action offered -- it expires on
            # its own via _maybe_amnesty_denied_sage_reviews).
            needs_review = [b for b in c["pendingBlueprints"]
                            if SAGE_REVIEW_ENABLED and b.get("sageReview", "pending") == "pending"]
            ready = [b for b in c["pendingBlueprints"]
                     if not SAGE_REVIEW_ENABLED or b.get("sageReview") in ("approved", "skipped")]
            denied = [b for b in c["pendingBlueprints"] if b.get("sageReview") == "denied"]
            elder_blueprint_review_active = bool(needs_review or ready)
            if needs_review:
                # C3: cap rendered briefs per bucket -- MAX_PENDING_BLUEPRINTS
                # already loosely bounds the queue, so this is mostly a
                # safeguard against a bucket absorbing the whole queue.
                shown = needs_review[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(needs_review) - len(shown)
                briefs = "; ".join(
                    f"{b['id']} by {b['proposedBy']} (needs "
                    + ", ".join(f"{k} {v}" for k, v in (b.get('needs') or {}).items())
                    + f"; {self._function_summary(b.get('function'))}"
                    + (f"; duplicates {b['duplicateOf']}" if b.get("duplicateOf") else "")
                    + ")"
                    for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                note(1,
                    f"BLUEPRINT NEEDS SAGE REVIEW: {briefs}. Check district stock shortages, "
                    f"gather-zone availability, existing producers, and structure distribution "
                    f"({self._sage_review_geo_context()}), then use sage_review_blueprint "
                    f"(target = its id, sage_decision = approve or deny).")
            if ready:
                shown = ready[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(ready) - len(shown)
                briefs = "; ".join(
                    f"{b['id']} by {b['proposedBy']}"
                    + (f" [sage: {b['sageReviewReason']}]" if b.get("sageReviewReason") else "")
                    + (f" [duplicates {b['duplicateOf']} -- approving upgrades it instead of "
                       f"building new]" if b.get("duplicateOf") else "")
                    for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                if len(ready) == 1:
                    note(1,
                        f"BLUEPRINT AWAITS YOUR VERDICT: {briefs}. Use approve_blueprint "
                        f"(target = its id, optionally target_district) if it serves the village, "
                        f"or reject_blueprint with a one-line reason if not.")
                else:
                    note(1,
                        f"COUNCIL VERDICT NEEDED: {len(ready)} blueprint proposals "
                        f"compete: {briefs}. Compare them and approve the BEST with approve_blueprint "
                        f'(target = its id), rejecting the rest IN THE SAME decision by adding '
                        f'"verdict": {{"rejections": {{"<id>": "<one-line reason it lost>"}}}}.')
            if denied and not needs_review and not ready:
                shown = denied[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(denied) - len(shown)
                briefs = "; ".join(f"{b['id']} ({b.get('sageReviewReason') or 'no reason given'})"
                                   for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                note(1, f"NOTE: Sage denied {briefs} -- it cannot be approved as-is; "
                        f"it will expire on its own.")
        if agent["role"] == "elder" and actives:
            stalled_district = next((did for did in actives
                                     if self.frameTick - c["districtLastContribution"].get(did, 0) > STALL_THRESHOLD), None)
            if stalled_district:
                stalled = self._first_unmet_project_resource(stalled_district)
                if stalled:
                    holders = sorted((a for a in self.agents if a["resources"].get(stalled, 0) > 0),
                                     key=lambda a: a["resources"].get(stalled, 0), reverse=True)
                    holder = holders[0]["name"] if holders else "no one"
                    note(2, f"NOTE: No progress on {stalled_district} in a while. {stalled} is still short; "
                            f"{holder} is holding the most of it. Consider assign_task or contribute_resources.")
        if len(actives) < MAX_CONCURRENT_PROJECTS:
            idle_buildable = next((did for did in self._buildable_district_ids()
                                   if not c["districtProjects"].get(did)
                                   and self._district_structure_count(did) < c["districts"][did]["build_grid"]["cap"]),
                                  None)
            if idle_buildable and idle_buildable != agent.get("currentDistrict"):
                note(3, f"NOTE: {idle_buildable} has no build underway and there's room for another "
                        f"concurrent project (up to {MAX_CONCURRENT_PROJECTS} at once). Consider start_project "
                        f"with target_district {idle_buildable} if you're nearby.")
        if len(c["pendingBlueprints"]) < MAX_PENDING_BLUEPRINTS \
                and self.frameTick - c["lastBlueprintActivityFrame"] > BLUEPRINT_STALL_THRESHOLD:
            note(3, "NOTE: No new blueprint activity in a while. Consider propose_blueprint if you have an idea.")
        if STRUCTURE_EFFECTS_ENABLED and not invention_required:
            pref = self.d["ROLE_PROJECT"].get(agent["role"].lower(), "house")
            prefs = pref if isinstance(pref, list) else [pref]
            if prefs and all(self._type_saturated(p) for p in prefs):
                note(3, f"NOTE: The village has enough {', '.join(prefs)} structures -- "
                        f"more add nothing. Build a different type or propose_blueprint.")
        if CRAFTING_ENABLED and self.frameTick - c["lastCraftActivityFrame"] > CRAFT_STALL_THRESHOLD:
            has_workshop = any(s["type"] == "workshop" for s in c["structures"])
            if agent["role"] == "elder" and not has_workshop:
                note(2, "NOTE: No workshop exists yet. Direct an agent to build a Workshop so the village can craft planks, bricks, and tools for advanced builds.")
            elif has_workshop:
                granary = c["projectRegistry"].get("granary")
                if granary and "granary" not in c["builtTypes"]:
                    crafted_needs = ", ".join(f"{n} {r}" for r, n in granary["needs"].items()
                                              if r in self.RECIPES)
                    note(2, f"NOTE: No crafting in a while and the Granary is still unbuilt -- "
                            f"it needs {crafted_needs}. At the workshop, craft_item those now.")
                else:
                    note(2, "NOTE: No crafting in a while. At the workshop, craft_item (planks/bricks/tools) — advanced builds like the Granary need crafted goods.")
            else:
                note(2, "NOTE: The village should build a Workshop, then craft goods for advanced builds like the Granary.")
        if SURVIVAL_ENABLED:
            if agent["hunger"] < EAT_THRESHOLD and agent["resources"].get("food", 0) == 0:
                note(0, "NOTE: You are hungry and have no food. Gather food from the farm (or fish at the beach) before you starve.")
            # Dead agents stay incapacitated forever (no post-mortem revive
            # path), so without the deathFrame guard a deceased agent reads
            # as a standing "collapsed" emergency in every prompt.
            collapsed = next((a for a in self.agents
                              if a["incapacitated"] and a.get("deathFrame") is None), None)
            if collapsed and collapsed["name"] != agent["name"]:
                verb = "Go heal_agent" if agent["role"] == "healer" else "Bring food or heal_agent"
                note(0, f"NOTE: {collapsed['name']} has collapsed. {verb} to revive them.")
            em = self._sage_emergency()
            if em and em["name"] != agent["name"] and agent["name"] in self._sage_responders(em):
                emergency_active = True
                note(0, f"EMERGENCY: Elder Sage's life is the top priority — abandon your task and "
                        f"heal_agent {em['name']}. Nothing matters more than the elder's survival.")
        if nearby_detailed and agent["consecutiveTalks"] == 0 \
                and self.frameTick - agent.get("lastSpokeFrame", 0) > SOCIAL_SILENCE_FRAMES:
            note(3, "NOTE: You haven't spoken with anyone in a while and someone is nearby. "
                    "Consider talk_to_nearby to coordinate plans, ask for help, or share what you know.")
        if MEMES_ENABLED and agent.get("beliefs"):
            listener = next((self._find_agent(n.get("name")) for n in nearby_detailed
                             if n.get("name") and not (self._find_agent(n.get("name")) or {}).get("beliefs")), None)
            if listener:
                note(3, f"NOTE: {listener['name']} is nearby and has no belief. You may use talk_to_nearby with a belief_pitch to persuade them.")
        tool_line = None
        industry_line = None
        neighbor_line = None
        if path1_on():
            tools = [t for t in TOOL_TIER_ORDER if agent["resources"].get(t, 0) > 0]
            tool_line = f"wooden/stone/iron picks held: {', '.join(tools) or 'none'}"
            industry_line = f"Industry recipes: {len(self.RECIPES)} (smelt ores at kiln via craft_item)"
            if self._is_night():
                note(3, "NOTE: It is night — seek shelter in a house or composable shelter.")
            if self._border_settlement_agent(agent):
                neighbor_line = "Neighbor settlement nearby — trade or propose_treaty"
                note(3, f"NOTE: {neighbor_line}.")
            for rej_key, label in (("lastBlockRejection", "block"), ("lastTerrainRejection", "terrain")):
                rej = agent.get(rej_key)
                if rej and self.frameTick - rej.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                    note(2, f"NOTE: Your last {label} action failed: {rej['reason']}.")
            self._maybe_seek_shelter(agent)
            self._maybe_expand_field(agent)
            self._maybe_caravan_goal(agent)
        if invention_turn:
            # Invention turns get the dedicated INVENTION_SYSTEM_PROMPT/
            # INVENTION_USER_PROMPT (build_invention_prompt in server.py),
            # which already covers taken ids, resources, and tier rules --
            # every other nudge here (talk/craft/heal/capacity/social/etc.)
            # is a distraction from the one job this turn has. The 2026-07-09
            # investigation found competing nudges in 100% of 171 invention
            # turns, correlating with duplicate/off-target proposals. Keep
            # only the blueprint-rejection reason (if any) so a retried
            # invention turn still learns why its last attempt failed.
            # These overrides bypass the priority-cap selection below --
            # they already reduce to <=1 nudge, so there's nothing to cap.
            final_nudges = [rejection_nudge] if rejection_nudge else []
        elif sprite_design_turn:
            sprite_rej = agent.get("lastSpriteRejection")
            sprite_note_text = None
            if sprite_rej and self.frameTick - sprite_rej.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                sprite_note_text = (f"NOTE: Your last sprite was rejected: {sprite_rej['reason']}. "
                                    f"Submit a strictly BIGGER grid.")
            final_nudges = [sprite_note_text] if sprite_note_text else []
        else:
            # Priority selection: keep ALL P0 (rare) nudges, then fill
            # remaining slots up to MAX_BEHAVIOR_NUDGES with P1/P2/P3 nudges
            # in priority order, preserving relative order within each class
            # (Python's sort is stable).
            p0_nudges = [text for prio, text in nudges if prio == 0]
            rest_nudges = sorted(
                ((prio, text) for prio, text in nudges if prio != 0),
                key=lambda pt: pt[0])
            remaining_slots = max(0, MAX_BEHAVIOR_NUDGES - len(p0_nudges))
            final_nudges = p0_nudges + [text for _, text in rest_nudges[:remaining_slots]]
        # Observability: total collected vs. how many survived selection.
        # For invention/sprite turns the override already IS the total (no
        # separate pool was capped), so nothing reads as "dropped".
        if invention_turn or sprite_design_turn:
            nudges_total = len(final_nudges)
            nudges_dropped = 0
        else:
            nudges_total = len(nudges)
            nudges_dropped = nudges_total - len(final_nudges)
        behavior_nudge = " ".join(final_nudges)

        # Phase A1 (shadow-log only, no behavior change): name the first
        # matching high-stakes trigger for this turn, priority order below.
        # Not read by is_high_stakes_turn/model_for_decision -- logging only.
        election_active = bool(c.get("pendingSuccession"))
        treaty_unvoted = any(r.get("kind") == "treaty" and agent["name"] not in r["votes"]
                             for r in c["pendingRules"])
        if emergency_active:
            high_stakes_reason = "emergency"
        elif election_active:
            high_stakes_reason = "election"
        elif treaty_unvoted:
            high_stakes_reason = "treaty_vote"
        elif elder_blueprint_review_active:
            high_stakes_reason = "elder_blueprint_review"
        elif len(fresh_rejection_kinds) >= 2:
            high_stakes_reason = "repeated_rejections"
        else:
            high_stakes_reason = None

        # C3: trim the payload lists below that otherwise grow monotonically
        # across a long session. Each trim is prompt-facing only -- anything
        # server.py's validate_blueprint reads for id-collision/membership
        # checks keeps a separate, always-full list (noted per field).
        resource_items = [{"id": rid, "gather_zone": d.get("gatherZone"),
                           # Crafted goods are built-in resources, not
                           # invention slots (match _custom_resource_count).
                           "custom": (rid not in BASE_RESOURCES
                                       and rid not in CRAFTED_RESOURCES)}
                          for rid, d in c["resourceRegistry"].items()]
        # known_resource_ids: always-full, cheap id-only list. server.py's
        # validate_blueprint uses this (via build_agent_data) for the
        # duplicate-resource-id and needs-reference checks, so it must never
        # be trimmed -- only the rich known_resources list below (used for
        # the prompt) is capped.
        known_resource_ids_full = [r["id"] for r in resource_items]
        belief_records = [{"id": bid, "name": entry.get("name"), "tenet": entry.get("tenet"),
                           "affinity": list(entry.get("affinity") or [])}
                          for bid, entry in self._belief_registry().items()]
        belief_examples = [dict(example) for example in BELIEF_ARCHETYPES.values()]
        nearby_beliefs = {
            n["name"]: sorted((self._find_agent(n["name"]) or {}).get("beliefs") or [])
            for n in nearby_detailed if n.get("name")
        }
        seed_resources = [r for r in resource_items if not r["custom"]]
        custom_resources = [r for r in resource_items if r["custom"]]
        if len(seed_resources) + len(custom_resources) > MAX_KNOWN_RESOURCES_PROMPT:
            custom_slots = max(0, MAX_KNOWN_RESOURCES_PROMPT - len(seed_resources))
            known_resources_prompt = seed_resources + (custom_resources[-custom_slots:] if custom_slots else [])
        else:
            known_resources_prompt = seed_resources + custom_resources

        recipe_items = list(self.RECIPES.items()) if CRAFTING_ENABLED else []
        if len(recipe_items) > MAX_KNOWN_RECIPES_PROMPT:
            recipe_items = recipe_items[-MAX_KNOWN_RECIPES_PROMPT:]

        # rejected_blueprints: server.py's validate_blueprint reads the
        # full, untrimmed "rejected_blueprints" field (via build_agent_data)
        # for the "id was previously rejected" check, so that field is left
        # exactly as before. "rejected_blueprints_prompt" is a new, separate,
        # prompt-only view.
        rejected_full = list(c["rejectedBlueprintIds"])
        if len(rejected_full) > MAX_REJECTED_BLUEPRINTS_PROMPT:
            rejected_prompt = rejected_full[-MAX_REJECTED_BLUEPRINTS_PROMPT:] + [
                f"(+{len(rejected_full) - MAX_REJECTED_BLUEPRINTS_PROMPT} older rejected ids omitted)"]
        else:
            rejected_prompt = rejected_full

        # approved_custom_projects: same caution -- validate_blueprint's
        # approved_ids arg (duplicate-id + MAX_APPROVED_CUSTOM count checks)
        # keeps reading the full "approved_custom_projects" field unchanged.
        # "approved_custom_projects_prompt" is the new, separate, prompt-only
        # view (in practice a no-op today since approvals are already capped
        # at MAX_APPROVED_CUSTOM <= MAX_APPROVED_PROJECTS_PROMPT).
        approved_full = self._custom_project_ids()
        if len(approved_full) > MAX_APPROVED_PROJECTS_PROMPT:
            approved_prompt = approved_full[-MAX_APPROVED_PROJECTS_PROMPT:] + [
                f"(+{len(approved_full) - MAX_APPROVED_PROJECTS_PROMPT} older approved ids omitted)"]
        else:
            approved_prompt = approved_full

        # active_rules: not read by validate_blueprint at all, so a plain cap
        # on the existing field is safe. Already loosely bounded by
        # MAX_ACTIVE_RULES (8) <= MAX_ACTIVE_RULES_PROMPT (12) today.
        rules_full = list(c["rules"]) if RULES_ENABLED else []
        if len(rules_full) > MAX_ACTIVE_RULES_PROMPT:
            active_rules_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"]}
                                 for r in rules_full[-MAX_ACTIVE_RULES_PROMPT:]]
            active_rules_list.append(f"(+{len(rules_full) - MAX_ACTIVE_RULES_PROMPT} older rules)")
        else:
            active_rules_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"]}
                                 for r in rules_full]

        return {
            "agent_name": agent["name"],
            "frame_tick": self.frameTick,
            "role": agent["role"],
            "role_skill": self.d["ROLE_SKILLS"].get(agent["role"], "helps the village"),
            "personality": self._personality_with_drift(agent),
            "life_stage": self._life_stage(agent) if LIFECYCLE_ENABLED else None,
            "memory": self._memory_for_prompt(agent),
            "resources": dict(agent["resources"]),
            "hunger": agent["hunger"],
            "health": agent["health"],
            "relationships": dict(agent["relationships"]),
            "beliefs": [self._belief_text(b) for b in agent["beliefs"]] if MEMES_ENABLED else [],
            "belief_ids": sorted(agent["beliefs"]) if MEMES_ENABLED else [],
            "belief_registry": belief_records if MEMES_ENABLED else [],
            "belief_examples": belief_examples if MEMES_ENABLED else [],
            "nearby_beliefs": nearby_beliefs if MEMES_ENABLED else {},
            "belief_pitch_budget_remaining": max(0, BELIEF_PITCH_SESSION_CAP - c.get("beliefPitchCalls", 0)),
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
            "invention_build_context": invention_build_context,
            "sprite_design_only": sprite_design_turn,
            "sprite_design_context": dict(agent["spriteDesignTurn"]) if sprite_design_turn else None,
            "upgradeable_structures": self._upgradeable_structures_brief() if STRUCTURE_UPGRADES_ENABLED else [],
            "invention_status": ("REQUIRED: every known structure is built or at capacity. Use "
                                 "propose_blueprint to invent a new structure.") if invention_required else "not needed",
            "commitment": agent.get("commitment"),
            "idle_agents": idle_agents,
            "known_resources": known_resources_prompt,
            # C3: always-full id list for server.py validation; see comment above.
            "known_resource_ids": known_resource_ids_full,
            "pending_blueprints": [{"id": b["id"], "needs": b["needs"], "proposed_by": b["proposedBy"],
                                    "sage_review": b.get("sageReview", "pending"),
                                    "sage_review_reason": b.get("sageReviewReason"),
                                    "duplicate_of": b.get("duplicateOf")}
                                   for b in c["pendingBlueprints"]],
            "known_recipes": [{"id": rid, "inputs": r["inputs"], "station": r["station"]}
                              for rid, r in recipe_items] if CRAFTING_ENABLED else [],
            "pending_recipes": [{"id": r["id"], "inputs": r["inputs"], "proposed_by": r["proposedBy"]}
                                for r in c["pendingRecipes"]],
            # C3: kept full (unchanged) for server.py's validate_blueprint;
            # *_prompt is the new, separate, capped view for rendering.
            "approved_custom_projects": approved_full,
            "approved_custom_projects_prompt": approved_prompt,
            "rejected_blueprints": rejected_full,
            "rejected_blueprints_prompt": rejected_prompt,
            "district_stocks": self._format_district_stocks_for_prompt(agent),
            "known_terraform": list(TERRAFORM_TEMPLATES.keys()) if ECOLOGY_ENABLED else [],
            # Phase C: one short prompt line (server renders it only when set,
            # so flag-off prompts stay byte-identical to Phase B).
            "season": self._current_season(),
            # Phase D: era replaces the level line and the tech tier feeds the
            # blueprint tier gate + invention prompt. Both None when the flag
            # is off, so the server renders Phase C prompts byte-identically.
            "era": self._current_era_name() if TECH_TREE_ENABLED else None,
            "village_tech_tier": self._village_tech_tier() if TECH_TREE_ENABLED else None,
            # Phase E: rendered as one compact "Prices: ..." line only when a
            # market exists (server renders it only when set, so flag-off /
            # no-market prompts stay byte-identical to Phase D).
            "prices_line": self._format_prices_for_prompt() if ECONOMY_ENABLED else None,
            "pending_rules": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"],
                               "yes": list(r["votes"].values()).count("yes"),
                               "no": list(r["votes"].values()).count("no"),
                               "proposed_by": r["proposedBy"]}
                              for r in c["pendingRules"]] if RULES_ENABLED else [],
            "active_rules": active_rules_list if RULES_ENABLED else [],
            "recent_conversations": self._recent_conversations_text(),
            "inbox": self._drain_inbox(agent),
            "self_prompt": "",
            "module_reports": "none",
            "behavior_nudge": behavior_nudge,
            "nudges_total": nudges_total,
            "nudges_dropped": nudges_dropped,
            "needed_role": self._village_needed_role() if EMERGENT_ROLES else None,
            "known_role_ids": sorted(c["roleRegistry"]),
            "pending_role_count": len(c["pendingRoles"]),
            "emergent_role_count": len(set(c["roleRegistry"]) - set(self.d["ROLES"])),
            "known_project_ids": sorted(c["projectRegistry"]),
            # Server fallback helpers run outside the engine and must consult
            # this world's live registry, never server.py's process-start seed
            # maps. Copy list values so the think payload remains a snapshot.
            "role_project_map": {
                role: list(project) if isinstance(project, list) else project
                for role, project in self.d["ROLE_PROJECT"].items()
            },
            "role_primary_resource_map": dict(self.d["ROLE_PRIMARY_RESOURCE"]),
            "resource_gather_roles_map": {
                resource: list(roles)
                for resource, roles in self.d["RESOURCE_GATHER_ROLES"].items()
            },
            "pending_roles": [{"slug": role["slug"], "name": role["name"],
                               "specialty": list(role.get("specialty") or []),
                               "proposed_by": role.get("proposedBy")}
                              for role in c["pendingRoles"]],
            # Phase G: compact skills summary (folded into the existing "Your
            # skill:" line server-side, zero new template line) and a short
            # rotating village-history line (server renders it only when set,
            # so flag-off prompts stay byte-identical to Phase F).
            "skills": {k: round(v, 1) for k, v in agent["skills"].items()} if CULTURE_ENABLED else None,
            "chronicle_line": self._chronicle_prompt_line() if CULTURE_ENABLED else None,
            "library_lessons": (self._library_lessons(agent.get("currentDistrict"))
                                if CULTURE_ENABLED and LIBRARY_SCALING_ENABLED else None),
            "path1_tool_line": tool_line,
            "path1_industry_line": industry_line,
            "path1_neighbor_line": neighbor_line,
            "high_stakes_reason": high_stakes_reason,
            "available_actions": [a for a in self.d["AVAILABLE_ACTIONS"]
                                  if (a != "start_terraform" or ECOLOGY_ENABLED)
                                  and (a != "found_belief" or MEMES_ENABLED)
                                  and (a != "repair_structure" or GOODS_ENABLED)
                                  and (a != "bury_agent" or CEMETERY_ENABLED)
                                  and (a != "repeal_rule" or RULES_ENABLED)
                                  and (a != "upgrade_structure" or STRUCTURE_UPGRADES_ENABLED)
                                   and (a != "submit_structure_sprite" or sprite_design_turn)
                                   and (a not in ("propose_role", "approve_role", "reject_role") or EMERGENT_ROLES)
                                   and (a not in ("place_block", "remove_block") or path1_on("COMPOSABLE_BUILD_ENABLED"))
                                  and (a not in ("dig_terrain", "plant_terrain") or path1_on("TERRAIN_TILES_ENABLED"))
                                  and (a not in ("propose_treaty", "vote_treaty") or path1_on("PATH1_DIPLOMACY_ENABLED"))],
        }

    def _recent_conversations_text(self):
        if not self.conversationLog:
            return "none"
        return " | ".join(f"{c['from']} -> {c['to']}: {c['message']}"
                          for c in self.conversationLog[:5])

    def _piano_module_context(self, agent, payload):
        """Compact context string for PIANO sub-calls (kept small for cost)."""
        parts = [
            f"role={agent.get('role')}",
            f"zone={agent.get('currentZone')}",
            f"hunger={agent.get('hunger')}",
            f"health={agent.get('health')}",
            f"resources={payload.get('resources')}",
            f"project={payload.get('active_project')}",
            f"nudge={payload.get('behavior_nudge')}",
        ]
        return "; ".join(str(p) for p in parts if p)

    def _run_piano_modules(self, agent_name, modules, module_tick, context):
        """Sid-parity Phase 1/5: run staggered PIANO modules on the dedicated
        piano_workers pool (never the decision pool), so a module backlog can
        never starve the Cognitive Controller decision call. Stagger:
        perception+desire every turn; social every 2nd; reflection every 3rd.
        Modules not due this turn are served from the module-report cache
        (PIANO_MODULE_CACHE_TTL module-ticks) instead of an empty slot.
        Returns (report_string, new_module_tick, runs)."""
        runner = self.d.get("run_piano_module")
        if not runner or not PIANO_MODULES:
            return "none", module_tick, 0
        tick = (module_tick or 0) + 1
        modules = modules or {
            "perception": True, "social": True, "desire": True, "reflection": True,
        }
        to_run = []
        if modules.get("perception", True):
            to_run.append("perception")
        if modules.get("desire", True):
            to_run.append("desire")
        if modules.get("social", True) and tick % 2 == 0:
            to_run.append("social")
        if modules.get("reflection", True) and tick % 3 == 0:
            to_run.append("reflection")
        # Off-tick modules: enabled but not due this turn. Filled from cache
        # (if fresh) so the decision payload keeps seeing their last real
        # report instead of a gap on the ticks they don't fire.
        off_tick = [m for m in ("social", "reflection")
                    if modules.get(m, True) and m not in to_run]
        cache = self._piano_module_cache.setdefault(agent_name, {})
        ordered = list(to_run)
        for module in off_tick:
            if module not in ordered:
                ordered.append(module)
        report_by_module = {}
        runs = 0
        if to_run:
            futures = {}
            dispatch_started = {}
            for module in to_run:
                start_ts = time.time()
                dispatch_started[module] = start_ts
                futures[module] = self.piano_workers.submit(
                    runner, module, agent_name, context, frame_tick=self.frameTick)
            for module, fut in futures.items():
                try:
                    text = fut.result(timeout=PIANO_MODULE_TIMEOUT_WAIT_S)
                except Exception:
                    text = None
                latency_ms = (time.time() - dispatch_started[module]) * 1000.0
                totals = self._piano_latency_ms.setdefault(module, [0.0, 0])
                totals[0] += latency_ms
                totals[1] += 1
                if text:
                    cache[module] = {"tick": tick, "text": text}
                    report_by_module[module] = f"{module}: {text}"
                    runs += 1
                else:
                    self._piano_module_drops += 1
        for module in off_tick:
            cached = cache.get(module)
            if cached and (tick - cached["tick"]) <= PIANO_MODULE_CACHE_TTL:
                report_by_module[module] = f"{module}: {cached['text']}"
        reports = [report_by_module[m] for m in ordered if m in report_by_module]
        return (" | ".join(reports) if reports else "none"), tick, runs

    def _maybe_meta_update(self):
        """Sid-parity Phase 5: rotate one living agent through META_SYSTEM
        persona refresh on META_TICK_FRAMES. Amortized: one agent per gate."""
        if not META_SYSTEM:
            return
        runner = self.d.get("run_meta_update")
        if not runner:
            return
        living = [a for a in self._living_agents() if not a["incapacitated"]]
        if not living:
            return
        idx = self._meta_agent_index % len(living)
        self._meta_agent_index += 1
        agent = living[idx]
        top_actions = sorted(
            (agent.get("actionCounts") or {}).items(),
            key=lambda kv: kv[1], reverse=True,
        )[:3]
        report = {
            "role": agent.get("role"),
            "top_actions": ", ".join(f"{k}:{v}" for k, v in top_actions) or "none",
            "resources": dict(agent.get("resources") or {}),
            "beliefs": [self._belief_text(b) for b in (agent.get("beliefs") or ())],
        }
        agent_name = agent["name"]
        # LLM call while holding the tick lock is acceptable here: one agent
        # every META_TICK_FRAMES (~80s), same discipline as birth-persona.
        result = runner(agent_name, report, frame_tick=self.frameTick)
        if result and result.get("persona"):
            agent["persona"] = result["persona"]

    def _think_job(self, agent_name):
        """Runs in the worker pool. Build payload under lock, do the network
        call OUTSIDE the lock, then apply the result UNDER the lock."""
        try:
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent or agent["incapacitated"]:
                    return
                payload = self._build_think_payload(agent)
                self_prompt = (agent.get("persona") or "").strip() if META_SYSTEM else ""
                payload["self_prompt"] = self_prompt
                piano_context = None
                piano_modules = None
                piano_tick = 0
                if PIANO_MODULES:
                    piano_context = self._piano_module_context(agent, payload)
                    piano_modules = dict(agent.get("modules") or {})
                    piano_tick = int(agent.get("moduleTick") or 0)
            if PIANO_MODULES:
                # Module fan-out dispatches onto self.piano_workers (its own
                # PIANO_CONCURRENT_LLM-sized pool, see _run_piano_modules) --
                # decoupled from MAX_CONCURRENT_LLM so a module backlog can
                # never starve the decision path. Still called outside the
                # lock so this worker-pool thread can block waiting on it
                # without freezing the tick.
                reports, new_tick, runs = self._run_piano_modules(
                    agent_name, piano_modules, piano_tick, piano_context)
                payload["module_reports"] = reports
            else:
                new_tick, runs = 0, 0
            # Network call outside the lock (never block the tick thread or peers).
            decision = self.d["llm_decide"](payload)
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent:
                    return
                if PIANO_MODULES:
                    agent["moduleTick"] = new_tick
                    self._module_period_runs += runs
                    # Reflection reports are actual deliberate practice: this
                    # makes the high-reflection founder gate reachable without
                    # adding a separate action or blocking a decision turn.
                    if new_tick % 3 == 0 and "reflection:" in reports:
                        self._practice_skill(agent, "reflection")
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
                    if decision.get("sprite_rejection_note"):
                        agent["lastSpriteRejection"] = {
                            "reason": decision["sprite_rejection_note"],
                            "frame": self.frameTick,
                        }
                    if decision.get("upgrade_rejection_note"):
                        agent["lastUpgradeRejection"] = {
                            "reason": decision["upgrade_rejection_note"],
                            "frame": self.frameTick,
                        }
                    retried_invention = False
                    if decision.get("rejection_note"):
                        # normalize_decision swapped an invalid propose_blueprint
                        # for a fallback; remember why so the next prompt can
                        # tell the model instead of failing silently again.
                        note = decision["rejection_note"]
                        agent["lastBlueprintRejection"] = {
                            "reason": note, "frame": self.frameTick}
                        if TECH_TREE_ENABLED and "tier" in (note or "").lower():
                            # Phase D observability: tier-gate rejections are
                            # village events, not just private prompt nudges.
                            self._push_activity(
                                f"Tech tree: {agent['name']}'s blueprint was "
                                f"refused — {note}")
                            self._log_benchmark(
                                "tier_gate_rejection", self._village_tech_tier(),
                                {"kind": "blueprint_normalize", "agent": agent["name"]})
                        # Same-window retry (2026-07-09 council investigation):
                        # a rejected propose_blueprint used to get swapped for
                        # a gather/move fallback whose goal then ran deterministically
                        # for a while, so a council member who failed validation
                        # (59/171 on a duplicate taken id alone) never got another
                        # invention-only turn before COUNCIL_TTL_FRAMES -- the
                        # council just dissolved empty. If a council is live and
                        # this agent is one of its proposers, give them ONE
                        # immediate retry instead: re-flag inventionTurn (so the
                        # next think is invention-only again, with this rejection
                        # reason in the prompt's feedback) and rest this beat
                        # rather than committing to the fallback's lasting goal.
                        council = self.civilization.get("councilActive")
                        if (payload.get("invention_only") and council
                                and agent["name"] in (council.get("proposers") or [])
                                and not agent.get("inventionRetryUsed")):
                            agent["inventionRetryUsed"] = True
                            agent["inventionTurn"] = True
                            agent["goal"] = None
                            self.apply_decision(agent, {"action": "rest"})
                            retried_invention = True
                    if not retried_invention:
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
        """Returns True if a think job was actually submitted, False if
        skipped (pool full / cooldown / min-gap) -- the caller uses this to
        retry soon (THINK_RETRY_FRAMES) instead of waiting a full
        thinkInterval, so a busy worker pool doesn't silently cost an agent
        an entire cycle."""
        if agent["name"] in self._inflight:
            return False
        if len(self._inflight) >= MAX_CONCURRENT_LLM:
            return False
        now_ms = time.time() * 1000.0
        if time.time() < self.llm_cooldown_until:
            return False
        if now_ms - self.last_llm_dispatch_ms < LLM_MIN_GAP_MS:
            return False
        if agent["role"] == "elder":
            c = self.civilization
            c["inventionRequiredStreak"] = (c.get("inventionRequiredStreak", 0) + 1) \
                if self._invention_required() else 0
        self.last_llm_dispatch_ms = now_ms
        self._inflight.add(agent["name"])
        agent["isThinking"] = True
        self._executor.submit(self._think_job, agent["name"])
        return True

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
            if META_SYSTEM and ft % META_TICK_FRAMES == 0:
                self._maybe_meta_update()
            if EMERGENT_ROLES and ft % ROLE_SWITCH_TICK_FRAMES == 0:
                self._maybe_auto_switch_role()
            if RULES_ENABLED and ft % RULES_TICK_FRAMES == 0:
                self._maybe_advance_rules()
            if LIFECYCLE_ENABLED and ft % RULES_TICK_FRAMES == 0:
                # Deterministic escape hatch for a stalled succession vote --
                # checked on the same fast cadence as rule advancement so a
                # quorum-less election can't linger past its TTL.
                self._maybe_resolve_stalled_succession()
            if LIFECYCLE_ENABLED and ft % LIFECYCLE_TICK_FRAMES == 0:
                self._tick_lifecycle()
            if ft % RULES_TICK_FRAMES == 0:
                self._maybe_feed_starving()
                self._maybe_repair_critical()
                self._maybe_abandon_stalled_projects()
                self._maybe_relocate_stuck_project()
                self._maybe_reorganize_structures()
                self._maybe_force_contribution()
                self._maybe_start_idle_district_project()
                self._maybe_build_funded_project()
                self._maybe_start_approved_custom()
                self._maybe_retire_blueprint()
                self._maybe_amnesty_rejected_blueprints()
                if SAGE_REVIEW_ENABLED:
                    self._maybe_skip_sage_review()
                    self._maybe_amnesty_denied_sage_reviews()
                self._maybe_retire_custom_resource()
                self._maybe_invention_backstop()
                self._maybe_found_district()
                self._maybe_welcome_newcomer()
                if TECH_TREE_ENABLED:
                    self._maybe_era_transition()
                    self._maybe_dissolve_council()
                if CULTURE_ENABLED:
                    self._maybe_study_at_library()
                if CEMETERY_ENABLED:
                    self._maybe_handle_burials()
                if path1_on():
                    self._maybe_found_settlement()
                    self._path1_industry_benchmark()
            if path1_on("PRESSURE_LOOP_ENABLED") and ft % GOODS_TICK_FRAMES == 0:
                self._tick_wildlife()
            if path1_on("PRESSURE_LOOP_ENABLED") and ft % 30 == 0:
                if self._is_night():
                    self._tick_night_pressure()
                elif ENV_EFFECTS_ENABLED and self.civilization.get("litDistricts"):
                    self.civilization["litDistricts"] = []
            if STRUCTURE_EFFECTS_ENABLED and ft % EFFECT_TICK_FRAMES == 0:
                self._tick_structure_effects()
            if ECOLOGY_ENABLED and ft % ECOLOGY_REGROW_FRAMES == 0:
                self._tick_ecology_regrow()
            if GOODS_ENABLED and ft % GOODS_TICK_FRAMES == 0:
                self._tick_goods()
                self._tick_structure_health_benchmark()
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
                if a.get("reorgTask"):
                    a["thinkTimer"] -= 1
                    if a["thinkTimer"] <= 0:
                        self._step_reorg(a)
                        a["thinkTimer"] = GOAL_STEP_FRAMES
                    continue
                a["thinkTimer"] -= 1
                if a["thinkTimer"] <= 0 and not a["isThinking"] and a["name"] not in self._inflight:
                    if USE_GOALS and a["goal"] and not self._has_unread(a):
                        continuing = self._step_goal(a)
                        a["thinkTimer"] = GOAL_STEP_FRAMES if continuing else 1
                    else:
                        dispatched = self._schedule_think(a)
                        a["thinkTimer"] = a["thinkInterval"] if dispatched else THINK_RETRY_FRAMES

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
            # state.db with a near-identical one before any work happens.
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
        """Atomically write the complete world to state.db. Never raises."""
        try:
            with self.lock:
                payload = self._serialize_state()
            _write_state_db(DB_PATH, payload)
            return True
        except Exception:
            # Persistence must never crash the sim.
            return False

    def clear_state(self):
        """Remove state.db so the next start cold-starts. Never raises."""
        for suffix in ("", "-wal", "-shm"):
            try:
                path = DB_PATH + suffix
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def _ensure_registry_entry_from_instance(self, civ, type_id):
        """Restore-only fallback for retired structure recipes."""
        registry = civ.get("projectRegistry")
        if not isinstance(registry, dict):
            return None
        entry = registry.get(type_id)
        if isinstance(entry, dict):
            return entry
        instance = next((s for s in civ.get("structures", [])
                         if s.get("type") == type_id), None)
        if not instance:
            return None
        entry = {
            "name": instance.get("name") or type_id.replace("_", " ").title(),
            "needs": {"wood": 2, "stone": 2},
            "visualStyle": instance.get("visualStyle") or "generic",
            "function": {},
            "custom": True,
        }
        registry[type_id] = entry
        return entry

    def restore_state(self):
        """If a valid state.db exists, rehydrate the world from it instead of
        the cold-start roster. Returns True on a successful restore."""
        data = _read_state_db(DB_PATH)
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
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
                # Agent-driven structure reorganization: purely additive, same
                # setdefault-only back-compat as every other phase -- an old
                # save simply starts with no reorg task pending, and the
                # periodic backstop (_maybe_reorganize_structures) discovers
                # any pre-existing footprint overlap (e.g. the House/Mill
                # overlap in the live save) within the first ~10s.
                civ.setdefault("reorgTasks", [])
                civ.setdefault("lastReorgFrame", 0)
                civ.setdefault("lastReorgCheckFrame", 0)
                civ.setdefault("lastReorgNoRoomFrame", 0)
                civ.setdefault("lastRoleSwitchFrame", 0)
                civ.setdefault("roleNeedSinceFrame", None)
                civ.setdefault("lastRoleRebalanceLatency", None)
                # Phase 2 role registry migration: older saves only know the
                # roles.json seeds, while newer saves carry per-world approved
                # roles. Merge missing seeds without overwriting live entries.
                registry = civ.get("roleRegistry")
                if not isinstance(registry, dict):
                    registry = {}
                for role, definition in self.d["ROLES"].items():
                    registry.setdefault(role, dict(definition))
                civ["roleRegistry"] = registry
                civ.setdefault("pendingRoles", [])
                civ.setdefault("ruleKindsEverEnacted", [])
                # Backfill diversity from currently enacted rules so old saves
                # don't report 0 forever after a restore.
                for r in (civ.get("rules") or []):
                    kind = r.get("kind")
                    if kind and kind not in civ["ruleKindsEverEnacted"]:
                        civ["ruleKindsEverEnacted"].append(kind)
                # Phase C: spoilage nudge state. Structure condition/isRuin
                # deliberately have NO migration -- every read defaults via
                # .get(cond, 100), so pre-Phase-C structures start pristine.
                civ.setdefault("lastSpoilage", None)
                # Phase D: era + council state; registry entries from pre-D
                # saves carry no tier field (read via _type_tier's seed-template
                # fallback), and the new seed types (Forge, wagon resource)
                # merge into restored registries so an old save can build them.
                civ.setdefault("era", None)
                civ.setdefault("eraIndex", 0)
                civ.setdefault("councilActive", None)
                civ.setdefault("councilLog", [])
                if TECH_TREE_ENABLED:
                    for tid, tmpl in PROJECT_TEMPLATES.items():
                        if isinstance(civ.get("projectRegistry"), dict):
                            civ["projectRegistry"].setdefault(tid, dict(tmpl))
                    for rid, rdef in CRAFTED_RESOURCES.items():
                        if isinstance(civ.get("resourceRegistry"), dict):
                            civ["resourceRegistry"].setdefault(rid, dict(rdef))
                if ECOLOGY_ENABLED and not civ.get("districtStocks"):
                    civ["districtStocks"] = self._init_district_stocks(
                        civ.get("districts") or {}, civ.get("resourceRegistry"))
                # Civ-1 coastal visual migration: only replace the narrow
                # legacy starter bounds, never player-founded beaches.
                legacy_coast = {
                    "beach": {"x1": 230, "y1": 120, "x2": 400, "y2": 880},
                    "ocean": {"x1": 30, "y1": 120, "x2": 180, "y2": 880},
                }
                for did, old_bounds in legacy_coast.items():
                    district = (civ.get("districts") or {}).get(did)
                    if district and district.get("bounds") == old_bounds:
                        district["bounds"] = dict(STARTER_DISTRICTS[did]["bounds"])
                if ECONOMY_ENABLED:
                    # Phase E: the market seed joins existing registries (old
                    # saves can build it); structures/houses from pre-Phase-E
                    # saves have no "homeOf" -- every read uses .get(homeOf)
                    # so this setdefault is cosmetic (keeps snapshot/JSON
                    # shape consistent) rather than load-bearing.
                    if isinstance(civ.get("projectRegistry"), dict):
                        civ["projectRegistry"].setdefault("market", dict(PROJECT_TEMPLATES["market"]))
                    for s in (civ.get("structures") or []):
                        s.setdefault("homeOf", None)
                if STRUCTURE_UPGRADES_ENABLED:
                    for s in (civ.get("structures") or []):
                        s.setdefault("level", 1)
                        s.setdefault("visualTier", 1)
                        s.setdefault("renderScale", 1.0)
                if LIFECYCLE_ENABLED:
                    # Phase F: population lifecycle + governance state. A save
                    # from before this phase has none of this -- setdefault is
                    # purely additive (no migration needed, matching every
                    # prior phase's back-compat pattern). Gated on the flag
                    # like every other phase's restore block, so a flag-off
                    # restore introduces none of this state (byte-identical
                    # to Phase E).
                    civ.setdefault("lastBirthFrame", 0)
                    civ.setdefault("lastDeathActivityFrame", 0)
                    civ.setdefault("births", 0)
                    civ.setdefault("deaths", 0)
                    civ.setdefault("nextGeneratedAgentId", 1000)
                    civ.setdefault("pendingSuccession", None)
                    civ.setdefault("lastSuccessionActivityFrame", 0)
                    civ.setdefault("harvestQuotas", {})
                    civ.setdefault("rationingActive", {})
                    civ.setdefault("populationFloorHeld", False)
                if CULTURE_ENABLED:
                    # Phase G: knowledge/culture state. Purely additive --
                    # matches every prior phase's setdefault-only back-compat
                    # (no migration step), so the live save loads with the
                    # flag on and simply starts with an empty chronicle/library.
                    if isinstance(civ.get("projectRegistry"), dict):
                        civ["projectRegistry"].setdefault("library", dict(PROJECT_TEMPLATES["library"]))
                    civ.setdefault("chronicle", [])
                    civ.setdefault("libraryKnowledge", [])
                    civ.setdefault("memeTexts", {})
                    civ.setdefault("memeMutations", 0)
                    civ.setdefault("beliefRegistry", {
                        bid: {"id": bid, "name": bid.replace("_", " ").title(),
                              "tenet": text, "affinity": sorted(MEME_RULE_AFFINITY.get(bid, set())),
                              "authoredBy": None, "createdFrame": 0, "seed": True}
                        for bid, text in MEMES.items()
                    })
                    civ.setdefault("beliefPitchCalls", 0)
                    civ.setdefault("skillPracticeCount", 0)
                    civ.setdefault("teachCount", 0)
                if CEMETERY_ENABLED:
                    # Cemetery/burial state: purely additive, same discipline
                    # as every other phase's setdefault-only back-compat --
                    # an old save can build a Cemetery with no migration step.
                    if isinstance(civ.get("projectRegistry"), dict):
                        civ["projectRegistry"].setdefault("cemetery", dict(PROJECT_TEMPLATES["cemetery"]))
                    civ.setdefault("lastCemeteryCheckFrame", 0)
                    civ.setdefault("cemeteryBackoffUntil", 0)
                    civ.setdefault("cemeteryBackstopFailures", 0)
                    civ.setdefault("cemeteryEscalationLogged", False)
                if path1_on():
                    for tid, tmpl in PROJECT_TEMPLATES.items():
                        if isinstance(civ.get("projectRegistry"), dict):
                            civ["projectRegistry"].setdefault(tid, dict(tmpl))
                    for rid, rdef in {**BASE_RESOURCES, **CRAFTED_RESOURCES}.items():
                        if isinstance(civ.get("resourceRegistry"), dict):
                            civ["resourceRegistry"].setdefault(rid, dict(rdef))
                    civ.setdefault("settlements", [])
                    civ.setdefault("treaties", [])
                    civ.setdefault("caravanLog", [])
                    civ.setdefault("path1Placements", 0)
                    civ.setdefault("path1TerrainMutations", 0)
                    for d in (civ.get("districts") or {}).values():
                        d.setdefault("tiles", {})
                        d.setdefault("settlementId", "home")
                        if "terrain" not in d:
                            d["terrain"] = {}
                if ENV_EFFECTS_ENABLED:
                    civ.setdefault("upkeepLastDay", {})
                    civ.setdefault("litDistricts", [])
                    for tid in ("hearth", "lighthouse"):
                        tmpl = self._ensure_registry_entry_from_instance(civ, tid)
                        if not isinstance(tmpl, dict):
                            continue
                        fn = tmpl.setdefault("function", {})
                        if not isinstance(fn.get("light"), dict):
                            fn["light"] = {"scope": "district"}
                        fn.setdefault("upkeep", {"resource": "charcoal", "amount": 1})
                if TRANSIT_ENABLED:
                    for tid in ("dock", "shipyard"):
                        entry = self._ensure_registry_entry_from_instance(civ, tid)
                        if not isinstance(entry, dict):
                            continue
                        fn = entry.setdefault("function", {})
                        unlocks = fn.setdefault("unlocks", [])
                        if not any(u.get("kind") == "transit" for u in unlocks if isinstance(u, dict)):
                            unlocks.append({"kind": "transit", "terrain": "ocean", "consumes": {"boat": 1}})
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
                    a.setdefault("lastRecipeRejection", None)
                    a.setdefault("lastShelterNote", None)
                    a.setdefault("homeStructureId", None)
                    a.setdefault("lastTradeRejection", None)
                    a.setdefault("lastHomelessNudgeFrame", None)
                    a.setdefault("lastBurialRejection", None)
                    a.setdefault("inventionRetryUsed", False)
                    a.setdefault("inventionBuildContext", None)
                    a.setdefault("spriteDesignTurn", None)
                    a.setdefault("lastUpgradeRejection", None)
                    a.setdefault("lastSpriteRejection", None)
                    a.setdefault("lastBlockRejection", None)
                    a.setdefault("lastTerrainRejection", None)
                    a.setdefault("lastTreatyRejection", None)
                    a.setdefault("lastNightNote", None)
                    a.setdefault("reorgTask", None)
                    a.setdefault("persona", "")
                    a.setdefault("moduleTick", 0)
                    a.setdefault("modules", {
                        "perception": True, "social": True,
                        "desire": True, "reflection": True,
                    })
                    if LIFECYCLE_ENABLED:
                        # Phase F: every restored agent gets an age (staggered
                        # by roster position, same deterministic spread
                        # _make_agents uses for a cold start; the elder starts
                        # oldest) so a long-lived save (e.g. the live
                        # 416-structure world) can turn LIFECYCLE_ENABLED on
                        # with no migration step. Gated on the flag so a
                        # flag-off restore never introduces an age field --
                        # matching every other phase's discipline.
                        if a.get("age") is None:
                            if a.get("role") == "elder":
                                a["age"] = float(ELDER_AGE + 5)
                            else:
                                a["age"] = float(ADULT_AGE + 2 + (len(agents) * 7) % 30)
                        a.setdefault("lastQuotaResetFrame", 0)
                        a.setdefault("gatherCountThisPeriod", {})
                        a.setdefault("lastQuotaRejection", None)
                        a.setdefault("lastRationingRejection", None)
                        a.setdefault("parents", None)
                        a.setdefault("deathFrame", None)
                        a.setdefault("buried", False)
                        a.setdefault("restingPlaceId", None)
                        a.setdefault("restingDistrictId", None)
                    else:
                        a["age"] = None
                    if CULTURE_ENABLED:
                        # Phase G: an agent restored from a pre-Phase-G save
                        # (or with the flag freshly turned on) starts with no
                        # practiced skill and no drift traits -- additive only.
                        skills = a.get("skills")
                        a["skills"] = {k: float((skills or {}).get(k, 0.0)) for k in SKILL_KINDS}
                        a.setdefault("personalityTraits", [])
                        a.setdefault("lastTeachFrame", 0)
                    # state.db may have been written before scaffold
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
                self.civilization = civ
                self._rebuild_role_maps()
                self.agents = agents
                self.agent_names = set(a["name"] for a in agents)
                self.frameTick = int(data.get("frameTick") or 0)
                rs = data.get("roster_size")
                if rs:
                    self.roster_size = int(rs)
                self._recompute_road_paths()
                if CEMETERY_ENABLED:
                    self._ensure_cemetery_district()
                    self._migrate_cemetery_structure()
                    self._relayout_cemetery_graves()
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
            self._piano_module_cache = {}
            self._piano_module_drops = 0
            self._piano_latency_ms = {}
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
                "isThinking": a["isThinking"],
                "beliefs": [self._belief_text(b) for b in a["beliefs"]],
                "beliefIds": sorted(a["beliefs"]) if MEMES_ENABLED else [],
                "lastAction": a["lastAction"], "assignedTask": a["assignedTask"],
                "age": round(a["age"], 1) if LIFECYCLE_ENABLED and a.get("age") is not None else None,
                "lifeStage": self._life_stage(a) if LIFECYCLE_ENABLED else None,
                "skills": {k: round(v, 1) for k, v in a["skills"].items()} if CULTURE_ENABLED else None,
                "personalityTraits": list(a.get("personalityTraits") or []) if CULTURE_ENABLED else [],
                # Cemetery/burial (viewer-only booleans, not the raw frame --
                # same discipline as councilActive's "frame" omission): lets
                # the renderer tell a permanent death (tombstone sprite) apart
                # from a temporary survival collapse (grey overlay, same body).
                "deceased": bool(LIFECYCLE_ENABLED and a.get("deathFrame") is not None),
                "buried": bool(CEMETERY_ENABLED and a.get("buried")),
            } for a in self.agents]
            env_lit_types = self._env_lit_types() if ENV_EFFECTS_ENABLED else set()
            civ = {
                "level": c["level"],
                "structures": [{"id": s["id"], "type": s["type"], "x": s["x"], "y": s["y"],
                                "visualStyle": s.get("visualStyle"), "name": s.get("name"),
                                "sprite": s.get("sprite"),
                                "districtId": s.get("districtId"),
                                "condition": s.get("condition", 100),
                                "isRuin": bool(s.get("isRuin")),
                                "homeOf": s.get("homeOf"),
                                "level": s.get("level", 1),
                                "visualTier": s.get("visualTier", 1),
                                "renderScale": s.get("renderScale", 1.0),
                                "light": bool(
                                    ENV_EFFECTS_ENABLED and s["type"] in env_lit_types
                                    and not s.get("isRuin")
                                    and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)}
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
            if CULTURE_ENABLED:
                # Phase G: chronicle + library knowledge for the viewer (thin,
                # read-only -- no simulation logic moves to the browser).
                civ["chronicle"] = list((c.get("chronicle") or [])[-CHRONICLE_CAP:])
                civ["libraryKnowledge"] = list(c.get("libraryKnowledge") or [])
                civ["memeMutations"] = c.get("memeMutations", 0)
                civ["beliefRegistry"] = json.loads(json.dumps(self._belief_registry(), default=str))
                civ["beliefPitchCalls"] = c.get("beliefPitchCalls", 0)
            if TECH_TREE_ENABLED:
                # Phase D: era chip, council banner, and the persisted debate
                # records for the viewer's Council panel.
                civ["era"] = self._current_era_name()
                civ["techTier"] = self._village_tech_tier()
                council = c.get("councilActive")
                civ["councilActive"] = ({
                    "active": True,
                    "trigger": council.get("trigger"),
                    "proposers": list(council.get("proposers") or []),
                    "proposals": len(council.get("proposals") or []),
                } if council else None)
                civ["councilLog"] = json.loads(json.dumps(
                    (c.get("councilLog") or [])[:COUNCIL_LOG_CAP], default=str))
            if ECONOMY_ENABLED:
                # Phase E: market status + a live prices dict for the viewer
                # (thin-viewer-only rendering -- no simulation logic moves).
                civ["marketActive"] = self._market_active()
                civ["prices"] = ({rid: self._resource_price(rid)
                                  for rid in c["resourceRegistry"] if rid != "gold"}
                                 if civ["marketActive"] else {})
            if path1_on():
                civ["settlements"] = list(c.get("settlements") or [])
                civ["treaties"] = list(c.get("treaties") or [])
                civ["isNight"] = self._is_night()
            if ENV_EFFECTS_ENABLED:
                civ["litDistricts"] = list(c.get("litDistricts") or [])
            if TRANSIT_ENABLED:
                boat_count = int(c.get("stockpile", {}).get("boat", 0))
                civ["physicalProps"] = ([{"resource": "boat", "count": min(3, boat_count)}]
                                        if boat_count >= 3 else [])
            benchmarks = dict(self.lastBenchmarks)
            activity = list(self.activityLog)
            conversation = list(self.conversationLog[:30])
            return {
                "frameTick": self.frameTick,
                "paused": self.paused,
                "uptimeSeconds": time.time() - self.processStartTime,
                "calendar": self._calendar(),
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
                        "TECH_TREE_ENABLED": TECH_TREE_ENABLED,
                        "ECONOMY_ENABLED": ECONOMY_ENABLED,
                        "LIFECYCLE_ENABLED": LIFECYCLE_ENABLED,
                        "CULTURE_ENABLED": CULTURE_ENABLED,
                        "CEMETERY_ENABLED": CEMETERY_ENABLED,
                        "STRUCTURE_UPGRADES_ENABLED": STRUCTURE_UPGRADES_ENABLED,
                        "PATH1_ENABLED": PATH1_ENABLED,
                        "INDUSTRY_ENABLED": path1_on("INDUSTRY_ENABLED"),
                        "TOOL_TIERS_ENABLED": path1_on("TOOL_TIERS_ENABLED"),
                        "COMPOSABLE_BUILD_ENABLED": path1_on("COMPOSABLE_BUILD_ENABLED"),
                        "TERRAIN_TILES_ENABLED": path1_on("TERRAIN_TILES_ENABLED"),
                        "DIPLOMACY_ENABLED": path1_on("PATH1_DIPLOMACY_ENABLED"),
                        "TIER3_CONTENT_ENABLED": path1_on("TIER3_CONTENT_ENABLED"),
                        "PRESSURE_LOOP_ENABLED": path1_on("PRESSURE_LOOP_ENABLED"),
                        "ENV_EFFECTS_ENABLED": ENV_EFFECTS_ENABLED,
                        "LIBRARY_SCALING_ENABLED": LIBRARY_SCALING_ENABLED,
                        "TRANSIT_ENABLED": TRANSIT_ENABLED,
                        "ECONOMY_SINKS_ENABLED": ECONOMY_SINKS_ENABLED,
                    },
                },
            }
