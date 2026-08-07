"""Agent-roster registries and small free-function helpers (geometry,
district/road validation, task-text cleanup) for the simulation engine.
Split out of the former single-file sim_engine.py during the Phase 6a
package conversion -- pure move, no behavior change (see
simulation/sim_engine/__init__.py for the package overview).
"""

import math
import re
from collections import deque

from .constants import (
    CORE_RESERVED_BOUNDS,
    FRONTIER_PLOT_H,
    FRONTIER_PLOT_W,
    STARTER_DISTRICTS,
    STARTER_ROAD_EDGES,
    STARTER_ROAD_NODES,
    WORLD_H,
    WORLD_W,
)


__all__ = [
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
]


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
    {"id": 13, "name": "Kane", "role": "hunter", "personality": "quiet and relentless", "color": "#A1887F", "zone": "forest"},
]
ROSTER = ["Zara", "Sage", "Aria", "Luna", "Marco", "Colt", "Finn", "Mia"]

# Sid-parity Phase 6: scale headroom past the hand-written AGENT_DEFS.
# Not a bid for Project Sid's ~500-agent scale (explicit non-goal, see
# specs/00-overview.md) -- just enough room that emergent roles (Phase 2) and
# belief factions (Phase 3) can differentiate into more than a 2-3 person
# "faction". Raise this, not the individual roster-size clamps scattered
# through the file, to change the ceiling.
MAX_ROSTER_SIZE = 20

# Fixed pools for procedurally generated agents (roster indices
# len(AGENT_DEFS)..MAX_ROSTER_SIZE-1) -- see _generated_agent_defs. Sized to
# cover MAX_ROSTER_SIZE - len(AGENT_DEFS) headroom; if MAX_ROSTER_SIZE
# ever grows past the name pool, _generated_agent_defs appends a numeric
# suffix rather than silently duplicating a name.
_GENERATED_AGENT_NAMES = ["Wren", "Ash", "Briar", "Juno", "Rowan", "Sable", "Tarn", "Vesper"]
_GENERATED_AGENT_PERSONALITIES = [
    "steady and dependable", "watchful and reserved", "eager and talkative",
    "practical and blunt", "warm and easygoing", "restless and inventive",
    "careful and thoughtful", "spirited and stubborn",
]
_GENERATED_AGENT_COLORS = ["#3F51B5", "#009688", "#CDDC39", "#673AB7",
                           "#FFC085", "#03A9F4", "#8D6E63", "#EC407A"]


def _generated_agent_defs(count):
    """AGENT_DEFS-shaped entries for roster slots beyond the hand-written
    ones. Deterministic in `count` (no randomness) so a given roster_size
    always yields the same generated roster, which every other system --
    roles, beliefs, relationships, think scheduling -- treats identically to
    a hand-written def. Role/zone rotate across the seed's non-elder
    roles.json roles (one generated agent per role before any role repeats),
    reusing the zone the matching hand-written def already spawns into, so
    generated agents spread across specialties instead of clustering into
    one and land in a district that actually supports their role."""
    non_elder_defs = [d for d in AGENT_DEFS if d["role"] != "elder"]
    next_id = max(d["id"] for d in AGENT_DEFS) + 1
    out = []
    for i in range(count):
        base = non_elder_defs[i % len(non_elder_defs)]
        name = _GENERATED_AGENT_NAMES[i % len(_GENERATED_AGENT_NAMES)]
        if i >= len(_GENERATED_AGENT_NAMES):
            name = f"{name}{i // len(_GENERATED_AGENT_NAMES) + 1}"
        personality = _GENERATED_AGENT_PERSONALITIES[i % len(_GENERATED_AGENT_PERSONALITIES)]
        color = _GENERATED_AGENT_COLORS[i % len(_GENERATED_AGENT_COLORS)]
        out.append({"id": next_id + i, "name": name, "role": base["role"],
                    "personality": personality, "color": color, "zone": base["zone"]})
    return out


# Sid-parity Phase 6: proximity radius shared by _get_nearby_agents /
# _get_nearby_detailed and the district-bucket cache that backs them (see
# SimEngine._nearby_candidate_pool). A single constant so the bucket
# adjacency computation and the actual distance check never drift apart.
NEARBY_RADIUS = 80


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
