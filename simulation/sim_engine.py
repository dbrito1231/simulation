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

import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor


# --- Feature flags (ported from index.html consts; now server config) ---
SURVIVAL_ENABLED = True
CRAFTING_ENABLED = True
USE_GOALS = True
MEMORY_ENABLED = True
AGENT_MESSAGING = True
PIANO_MODULES = False
META_SYSTEM = False
EMERGENT_ROLES = True
RULES_ENABLED = True
MEMES_ENABLED = True
BENCHMARKS_ENABLED = True

# --- World geometry ---
WORLD_W = 1600
WORLD_H = 1000

ZONE_CENTERS = {
    "farm": {"x": 700, "y": 260},
    "forest": {"x": 1280, "y": 280},
    "village": {"x": 760, "y": 730},
    "market": {"x": 1040, "y": 670},
    "beach": {"x": 310, "y": 500},
    "cave": {"x": 1380, "y": 855},
    "ocean": {"x": 100, "y": 500},
}

ZONE_BOUNDS = {
    "farm": {"x1": 500, "y1": 110, "x2": 920, "y2": 410},
    "forest": {"x1": 1030, "y1": 110, "x2": 1550, "y2": 450},
    "village": {"x1": 540, "y1": 560, "x2": 900, "y2": 940},
    "market": {"x1": 970, "y1": 620, "x2": 1110, "y2": 720},
    "beach": {"x1": 230, "y1": 120, "x2": 400, "y2": 880},
    "cave": {"x1": 1210, "y1": 750, "x2": 1540, "y2": 960},
    "ocean": {"x1": 30, "y1": 120, "x2": 180, "y2": 880},
}
ZONE_NAMES = list(ZONE_CENTERS.keys())

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

HUNGER_RATE = 0.6
HEALTH_RATE = 2
HEALTH_REGEN = 1.5
EAT_THRESHOLD = 65
FOOD_RESTORE = 45
EDIBLE_RESOURCES = ["food", "fish"]
HEAL_AMOUNT = 25
COLLAPSE_REGEN = 0.5
COLLAPSE_REVIVE_HEALTH = 15

COLLECT_CAP = 20
STALL_THRESHOLD = 600
BLUEPRINT_STALL_THRESHOLD = 1800
GOAL_STEP_FRAMES = 45
SAGE_CRITICAL_HEALTH = 30
CRAFT_STALL_THRESHOLD = 1500

MAX_PENDING_BLUEPRINTS = 5
MAX_APPROVED_CUSTOM = 15
MAX_CUSTOM_RESOURCES = 10
MAX_CUSTOM_RECIPES = 12
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

AGENT_DEFS = [
    {"id": 1, "name": "Aria", "role": "farmer", "personality": "hardworking and cautious", "color": "#4CAF50", "zone": "farm"},
    {"id": 2, "name": "Marco", "role": "trader", "personality": "sociable and opportunistic", "color": "#FF9800", "zone": "market"},
    {"id": 3, "name": "Zara", "role": "builder", "personality": "creative and methodical", "color": "#9C27B0", "zone": "village"},
    {"id": 4, "name": "Rex", "role": "guard", "personality": "loyal and aggressive", "color": "#F44336", "zone": "village"},
    {"id": 5, "name": "Luna", "role": "gatherer", "personality": "curious and adventurous", "color": "#2196F3", "zone": "forest"},
    {"id": 6, "name": "Finn", "role": "fisher", "personality": "patient and quiet", "color": "#00BCD4", "zone": "beach"},
    {"id": 7, "name": "Mia", "role": "healer", "personality": "empathetic and generous", "color": "#E91E63", "zone": "village"},
    {"id": 8, "name": "Colt", "role": "miner", "personality": "stubborn and hardworking", "color": "#795548", "zone": "cave"},
    {"id": 9, "name": "Ivy", "role": "scout", "personality": "fast and observant", "color": "#8BC34A", "zone": "forest"},
    {"id": 10, "name": "Dex", "role": "blacksmith", "personality": "focused and proud", "color": "#607D8B", "zone": "market"},
    {"id": 11, "name": "Nova", "role": "explorer", "personality": "bold and impulsive", "color": "#FF5722", "zone": "beach"},
    {"id": 12, "name": "Sage", "role": "elder", "personality": "wise and slow-moving", "color": "#FFC107", "zone": "village"},
]
ROSTER = ["Zara", "Sage", "Aria", "Luna", "Marco", "Colt", "Finn", "Mia"]


def _dist(ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    return math.sqrt(dx * dx + dy * dy)


def get_zone(x, y):
    # market is checked first because it sits inside the village rectangle.
    if 950 <= x <= 1130 and 600 <= y <= 740:
        return "market"
    if 0 <= x <= 200:
        return "ocean"
    if 200 <= x <= 420:
        return "beach"
    if 480 <= x <= 940 and 90 <= y <= 430:
        return "farm"
    if 1010 <= x <= 1570 and 90 <= y <= 470:
        return "forest"
    if 500 <= x <= 1180 and 540 <= y <= 960:
        return "village"
    if 1190 <= x <= 1570 and 730 <= y <= 980:
        return "cave"
    return "path"


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
            center = ZONE_CENTERS[d["zone"]]
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
                "currentZone": d["zone"], "message": None, "messageTimer": 0,
                "thinkTimer": 0, "thinkInterval": 300, "isThinking": False,
                "lastAction": None, "lastReasoning": None, "consecutiveTalks": 0,
                "pendingThink": False, "assignedTask": None, "idleCycles": 0,
                "lastTaskedFrame": None, "lastContributedFrame": None,
                "consecutiveIdleMoves": 0, "hunger": 80, "health": 100,
                "incapacitated": False, "goal": None, "actionCounts": {},
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
            self._set_agent_target(a, a["currentZone"])
        return agents

    def _reset_world(self, roster_size):
        self.RECIPES = {k: {"name": v["name"], "inputs": dict(v["inputs"]), "station": v["station"]}
                        for k, v in SEED_RECIPES.items()}
        self.civilization = {
            "level": 1,
            "structures": [],
            "activeProject": None,
            "completedProjects": 0,
            "nextStructureId": 1,
            "resourceRegistry": {**{k: dict(v) for k, v in BASE_RESOURCES.items()},
                                 **{k: dict(v) for k, v in CRAFTED_RESOURCES.items()}},
            "projectRegistry": {k: dict(v) for k, v in PROJECT_TEMPLATES.items()},
            "pendingBlueprints": [],
            "rejectedBlueprintIds": set(),
            "pendingRecipes": [],
            "rejectedRecipeIds": set(),
            "directive": None,
            "lastProjectContributionFrame": 0,
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
        }
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

    def _set_agent_target(self, agent, zone_name):
        bounds = ZONE_BOUNDS.get(zone_name)
        if not bounds:
            center = ZONE_CENTERS.get(zone_name)
            if not center:
                return
            agent["targetX"] = center["x"]
            agent["targetY"] = center["y"]
            return
        agent["targetX"] = bounds["x1"] + random.random() * (bounds["x2"] - bounds["x1"])
        agent["targetY"] = bounds["y1"] + random.random() * (bounds["y2"] - bounds["y1"])

    def _set_agent_target_to_agent(self, agent, target_name):
        target = self._find_agent(target_name)
        if not target:
            return
        agent["targetX"] = target["x"] + (random.random() - 0.5) * 60
        agent["targetY"] = target["y"] + (random.random() - 0.5) * 60

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
            agent["idleFrames"] = agent.get("idleFrames", 0) + 1
            if agent["idleFrames"] >= 60:
                if agent["currentZone"] not in ("path", "ocean"):
                    wander = agent["currentZone"]
                else:
                    wander = random.choice(ZONE_NAMES)
                self._set_agent_target(agent, wander)
                agent["idleCycles"] = agent.get("idleCycles", 0) + 1
                agent["idleFrames"] = 0
        else:
            agent["x"] += (dx / dist) * step
            agent["y"] += (dy / dist) * step
            agent["idleFrames"] = 0
        agent["currentZone"] = get_zone(agent["x"], agent["y"])

    # --- survival ---
    def _first_edible(self, agent):
        for rid in EDIBLE_RESOURCES:
            if agent["resources"].get(rid, 0) > 0:
                return rid
        return None

    def _update_survival(self, agent):
        if not SURVIVAL_ENABLED:
            return
        edible = self._first_edible(agent) if agent["hunger"] < EAT_THRESHOLD else None
        if edible:
            agent["resources"][edible] -= 1
            agent["hunger"] = min(100, agent["hunger"] + FOOD_RESTORE)
            self._push_activity(f"{agent['name']} ate {edible}")
        agent["hunger"] = max(0, agent["hunger"] - HUNGER_RATE)
        if agent["incapacitated"]:
            agent["health"] = min(100, agent["health"] + COLLAPSE_REGEN)
            if agent["health"] >= COLLAPSE_REVIVE_HEALTH:
                agent["incapacitated"] = False
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

    # --- project helpers ---
    def _project_progress_text(self):
        p = self.civilization["activeProject"]
        if not p:
            return "none"
        parts = []
        for res, need in p["needs"].items():
            have = p["contributed"].get(res, 0)
            parts.append(f"{res} {have}/{need}")
        return ", ".join(parts)

    def _first_unmet_project_resource(self):
        p = self.civilization["activeProject"]
        if not p:
            return None
        for res in p["needs"]:
            if p["contributed"].get(res, 0) < (p["needs"].get(res, 0)):
                return res
        return None

    def _gather_zone_for_resource(self, rid):
        d = self.civilization["resourceRegistry"].get(rid)
        return d.get("gatherZone") if d else None

    def _get_zone_resources(self, zone):
        return [rid for rid, d in self.civilization["resourceRegistry"].items()
                if d.get("gatherZone") == zone]

    def _try_contribute_resource(self, agent, res):
        p = self.civilization["activeProject"]
        if not p or not res:
            return None
        need = p["needs"].get(res, 0)
        have = p["contributed"].get(res, 0)
        if have >= need or agent["resources"].get(res, 0) <= 0:
            return None
        agent["resources"][res] -= 1
        p["contributed"][res] = have + 1
        agent["lastContributedFrame"] = self.frameTick
        self.civilization["lastProjectContributionFrame"] = self.frameTick
        self._enforce_resource_tax(agent, res)
        return f"{agent['name']} contributed {res} to {p['name']}"

    def _is_project_complete(self):
        p = self.civilization["activeProject"]
        if not p:
            return False
        for res, need in p["needs"].items():
            if p["contributed"].get(res, 0) < need:
                return False
        return True

    def _build_region_for(self, type_):
        if type_ == "farm_plot":
            return {"x0": 520, "y0": 250, "cols": 4, "dx": 105, "dy": 85}
        return {"x0": 560, "y0": 580, "cols": 4, "dx": 100, "dy": 95}

    def _find_structure_spot(self, type_):
        r = self._build_region_for(type_)
        for i in range(240):
            x = r["x0"] + (i % r["cols"]) * r["dx"]
            y = r["y0"] + (i // r["cols"]) * r["dy"]
            taken = any(abs(s["x"] - x) < 70 and abs(s["y"] - y) < 80
                        for s in self.civilization["structures"])
            if not taken:
                return {"x": x, "y": y}
        return {"x": r["x0"], "y": r["y0"]}

    def _check_civilization_level(self):
        new_level = (self.civilization["completedProjects"] // 3) + 1
        if new_level > self.civilization["level"]:
            self.civilization["level"] = new_level
            self._push_activity(f"Civilization reached level {self.civilization['level']}!")

    def _build_active_structure(self, agent):
        c = self.civilization
        struct_type = c["activeProject"]["type"]
        spot = self._find_structure_spot(struct_type)
        c["structures"].append({
            "id": c["nextStructureId"], "type": struct_type,
            "x": spot["x"], "y": spot["y"],
            "visualStyle": c["activeProject"].get("visualStyle") or "generic",
            "name": c["activeProject"]["name"],
        })
        c["nextStructureId"] += 1
        built_name = c["activeProject"]["name"]
        c["activeProject"] = None
        c["completedProjects"] += 1
        agent["lastContributedFrame"] = self.frameTick
        c["lastProjectContributionFrame"] = self.frameTick
        self._check_civilization_level()
        where = get_zone(spot["x"], spot["y"])
        place = "the village outskirts" if where == "path" else f"the {where}"
        return f"{agent['name']} built {built_name} at {place}"

    def _project_resource_list(self, project):
        return " and ".join(project["needs"].keys())

    def _role_default_project(self, role):
        pref = self.d["ROLE_PROJECT"].get((role or "").lower(), "house")
        if isinstance(pref, list):
            return random.choice(pref) if pref else "house"
        return pref

    def _start_project_for(self, agent, target):
        c = self.civilization
        if c["activeProject"]:
            return None
        type_ = target if (target and target in c["projectRegistry"]) else self._role_default_project(agent["role"])
        tmpl = c["projectRegistry"].get(type_)
        if not tmpl:
            return None
        contributed = {res: 0 for res in tmpl["needs"]}
        c["activeProject"] = {
            "type": type_, "name": tmpl["name"], "needs": dict(tmpl["needs"]),
            "contributed": contributed, "visualStyle": tmpl.get("visualStyle") or "generic",
        }
        c["lastProjectContributionFrame"] = self.frameTick
        if agent["role"] == "elder":
            c["directive"] = (f"Elder {agent['name']} directs: build the {tmpl['name']}; "
                              f"gather {self._project_resource_list(tmpl)}.")
            return f"{agent['name']} started {tmpl['name']} project. {c['directive']}"
        return f"{agent['name']} started {tmpl['name']} project"

    def _is_idle(self, agent):
        return agent["role"] != "elder" and (
            agent["lastAction"] is None or agent["lastAction"] == "rest"
            or agent.get("idleCycles", 0) >= 2)

    def _idle_agents_for_elder(self):
        idle = [a for a in self.agents if self._is_idle(a)]
        idle.sort(key=lambda a: (a["lastTaskedFrame"] if a["lastTaskedFrame"] is not None
                                 else float("-inf")))
        return idle

    def _task_for_agent(self, agent):
        c = self.civilization
        if c["activeProject"]:
            ap = c["activeProject"]
            lacking = next((res for res in ap["needs"]
                            if ap["contributed"].get(res, 0) < ap["needs"][res]), None)
            if lacking:
                return f"gather or contribute {lacking} to the {ap['name']}"
            return f"help finish the {ap['name']}"
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
        if not self._has_inputs(agent, recipe["inputs"]):
            missing = [r for r in recipe["inputs"] if agent["resources"].get(r, 0) < recipe["inputs"][r]]
            return f"{agent['name']} lacks {', '.join(missing)} to craft {recipe_id}"
        for r, n in recipe["inputs"].items():
            agent["resources"][r] -= n
        agent["resources"][recipe_id] = agent["resources"].get(recipe_id, 0) + 1
        self.civilization["lastCraftActivityFrame"] = self.frameTick
        return f"{agent['name']} crafted {recipe_id}"

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
        if tax <= 0:
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
            return False
        if len(c["pendingBlueprints"]) >= MAX_PENDING_BLUEPRINTS:
            return False
        if len(self._custom_project_ids()) >= MAX_APPROVED_CUSTOM:
            return False
        bid = bp.get("id")
        if not isinstance(bid, str) or not self.SLUG_RE.match(bid):
            return False
        if bid in PROJECT_TEMPLATES or bid in c["projectRegistry"]:
            return False
        if any(p["id"] == bid for p in c["pendingBlueprints"]):
            return False
        name = bp.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= 32):
            return False
        new_resources = bp.get("new_resources") or []
        if not isinstance(new_resources, list) or len(new_resources) > 3:
            return False
        new_ids = set()
        for r in new_resources:
            if not isinstance(r, dict):
                return False
            rid = r.get("id")
            if not isinstance(rid, str) or not self.SLUG_RE.match(rid):
                return False
            if rid in BASE_RESOURCES:
                return False
            if rid in c["resourceRegistry"] or rid in new_ids:
                return False
            rname = r.get("name")
            if not isinstance(rname, str) or not (1 <= len(rname) <= 32):
                return False
            gz = r.get("gather_zone")
            if gz is not None and gz not in VALID_GATHER_ZONES:
                return False
            new_ids.add(rid)
        if self._custom_resource_count() + len(new_ids) > MAX_CUSTOM_RESOURCES:
            return False
        needs = bp.get("needs")
        if not isinstance(needs, dict):
            return False
        if not (1 <= len(needs) <= 8):
            return False
        for key, amt in needs.items():
            known = (key in c["resourceRegistry"]) or (key in new_ids) or (key in BASE_RESOURCES)
            if not known:
                return False
            if isinstance(amt, bool) or not isinstance(amt, int) or not (1 <= amt <= 5):
                return False
        vs = bp.get("visual_style") or "generic"
        if vs not in VALID_VISUAL_STYLES:
            return False
        return True

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

    def _pick_contribution_resource(self, agent, decision):
        p = self.civilization["activeProject"]
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
        if not EMERGENT_ROLES or not self.civilization["activeProject"]:
            return None
        unmet = self._first_unmet_project_resource()
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
        unmet = self._first_unmet_project_resource()
        self.apply_decision(agent, {
            "action": "switch_role", "new_role": needed_role,
            "reasoning": f"The village has no one gathering {unmet}; "
                         f"retraining to {needed_role} to fill the gap."})

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

    # --- the 27-case world-mutation switch (ported applyDecision) ---
    def apply_decision(self, agent, decision):
        action = decision.get("action") or "rest"
        summary = f"{agent['name']} rested"
        c = self.civilization

        is_talk = action == "talk_to_nearby"
        if is_talk and decision.get("message"):
            agent["consecutiveTalks"] += 1
        elif action != "rest":
            agent["consecutiveTalks"] = 0

        is_move_only = action.startswith("move_to_") or action == "rest"
        agent["consecutiveIdleMoves"] = (agent.get("consecutiveIdleMoves", 0) + 1) if is_move_only else 0

        if action in ("move_to_farm", "move_to_market", "move_to_forest",
                      "move_to_beach", "move_to_village", "move_to_cave"):
            zone = action.replace("move_to_", "")
            self._set_agent_target(agent, zone)
            summary = f"{agent['name']} heads to the {zone}"

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

        elif action == "collect_resource":
            c["collectAttempts"] += 1
            if not c["activeProject"]:
                summary = self._start_project_for(agent, decision.get("target")) or f"{agent['name']} could not start a project"
            else:
                zone = agent["currentZone"]
                unmet = self._first_unmet_project_resource()
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
                resource = next((r for r in candidates if agent["resources"].get(r, 0) < COLLECT_CAP), None)
                if resource:
                    agent["resources"][resource] = agent["resources"].get(resource, 0) + 1
                    c["collectSuccesses"] += 1
                    summary = f"{agent['name']} collected {resource}"
                else:
                    contrib_res = self._pick_contribution_resource(agent, {"target": unmet})
                    contributed = self._try_contribute_resource(agent, contrib_res)
                    if contributed:
                        summary = contributed
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
                self._push_conversation(agent["name"], recipient, decision["message"])
                self._deliver_message(agent["name"], recipient, decision["message"], "speech")
                self._maybe_spread_beliefs(agent, recipient, decision["message"])
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
            summary = self._start_project_for(agent, decision.get("target")) or f"{agent['name']} could not start a project"

        elif action == "contribute_resources":
            if not c["activeProject"]:
                summary = self._start_project_for(agent, decision.get("target")) or f"{agent['name']} could not start a project"
            else:
                res = self._pick_contribution_resource(agent, decision)
                contributed = self._try_contribute_resource(agent, res)
                if contributed:
                    summary = contributed
                elif self._is_project_complete():
                    summary = self._build_active_structure(agent)
                else:
                    unmet = self._first_unmet_project_resource()
                    gz = self._gather_zone_for_resource(unmet) if unmet else None
                    if unmet and gz and agent["currentZone"] != gz:
                        self._set_agent_target(agent, gz)
                        summary = f"{agent['name']} heads to gather {unmet}"
                    elif unmet and gz and agent["currentZone"] == gz and agent["resources"].get(unmet, 0) < COLLECT_CAP:
                        agent["resources"][unmet] = agent["resources"].get(unmet, 0) + 1
                        summary = f"{agent['name']} collected {unmet}"
                    else:
                        summary = f"{agent['name']} has nothing to contribute"

        elif action == "build_structure":
            if not c["activeProject"]:
                summary = self._start_project_for(agent, decision.get("target")) or f"{agent['name']} could not start a project"
            elif self._is_project_complete():
                summary = self._build_active_structure(agent)
            else:
                summary = f"{agent['name']} waiting for more resources"

        elif action == "propose_blueprint":
            bp = decision.get("blueprint")
            if bp and bp.get("id") in c["rejectedBlueprintIds"]:
                summary = f"{agent['name']}'s blueprint {bp.get('id')} was already rejected"
            elif self._validate_blueprint(bp):
                needs_str = ", ".join(f"{k}x{v}" for k, v in bp["needs"].items())
                c["pendingBlueprints"].append({
                    "id": bp["id"], "name": bp["name"], "needs": dict(bp["needs"]),
                    "newResources": [{"id": r["id"], "name": r["name"],
                                      "gatherZone": r.get("gather_zone"),
                                      "color": r.get("color", "#BDBDBD")}
                                     for r in (bp.get("new_resources") or [])],
                    "visualStyle": bp.get("visual_style") or "generic",
                    "proposedBy": agent["name"],
                })
                if decision.get("message"):
                    agent["message"] = decision["message"]
                    agent["messageTimer"] = 180
                c["lastBlueprintActivityFrame"] = self.frameTick
                summary = f"{agent['name']} proposed {bp['name']} (needs {needs_str})"
            else:
                summary = f"{agent['name']} drafted an invalid blueprint"

        elif action == "approve_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                bp = c["pendingBlueprints"][idx]
                for r in bp["newResources"]:
                    if r["id"] not in c["resourceRegistry"]:
                        c["resourceRegistry"][r["id"]] = {"name": r["name"],
                                                          "gatherZone": r["gatherZone"], "color": r["color"]}
                c["projectRegistry"][bp["id"]] = {"name": bp["name"], "needs": dict(bp["needs"]),
                                                  "visualStyle": bp["visualStyle"], "custom": True}
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
                summary = f"{agent['name']} rejected {bp['name']} blueprint"
            else:
                summary = f"{agent['name']} could not reject that blueprint"

        elif action == "assign_task":
            target = self._find_agent(decision.get("target"))
            if agent["role"] == "elder" and target and self._is_idle(target) and decision.get("message"):
                target["assignedTask"] = decision["message"]
                target["lastTaskedFrame"] = self.frameTick
                c["directive"] = f"Elder {agent['name']} directs: {target['name']} should {decision['message']}."
                self._push_communication("directive", agent["name"], target["name"], decision["message"])
                self._deliver_message(agent["name"], target["name"], decision["message"], "directive")
                self._transmit_belief(agent, target, MEME_SPREAD_PROB)
                summary = f"Elder {agent['name']} tasked {target['name']}: {decision['message']}"
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
        if a == "collect_resource":
            return {"kind": "gather", "target": decision.get("target"), "ttl": 8}
        if a == "contribute_resources":
            return {"kind": "deliver", "target": decision.get("target"), "ttl": 6}
        if a == "craft_item":
            return {"kind": "craft", "target": decision.get("target"), "ttl": 6}
        if a == "build_structure":
            return {"kind": "build", "target": None, "ttl": 6}
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
        if g["kind"] in ("gather", "deliver", "build") and not self.civilization["activeProject"]:
            agent["goal"] = None
            return False
        if g["kind"] == "gather" and not self._first_unmet_project_resource():
            agent["goal"] = None
            return False
        action_by_kind = {"gather": "collect_resource", "deliver": "contribute_resources",
                          "craft": "craft_item", "build": "build_structure"}
        action = action_by_kind.get(g["kind"])
        if not action:
            agent["goal"] = None
            return False
        summary = self.apply_decision(agent, {"action": action, "target": g.get("target"),
                                              "message": None, "reasoning": f"goal:{g['kind']}"})
        s = summary or ""
        if any(t in s for t in ("has nothing to contribute", "found nothing", "nothing to craft",
                                "lacks ", "built ", "could not")):
            agent["goal"] = None
            return False
        return True

    def _apply_rule_based_fallback(self, agent):
        zone = random.choice(ZONE_NAMES)
        self._set_agent_target(agent, zone)
        self._push_memory(agent, f"{agent['name']} wandered toward the {zone}")
        self._push_activity(f"{agent['name']} wandered toward the {zone} (LLM fallback)")

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

        nudges = []
        if agent["assignedTask"]:
            nudges.append(f"Your leader assigned you: {agent['assignedTask']}. Do it now.")
        if not c["activeProject"]:
            nudges.append("NOTE: No active project exists. Use start_project now to begin a build.")
        elif agent["consecutiveTalks"] >= 2:
            nudges.append("NOTE: You have chatted twice. Prioritize collect_resource, contribute_resources, or move_to_agent.")
        if agent["role"] != "elder" and c["directive"]:
            nudges.append(f"Your leader directs: {c['directive']}. Prioritize it.")
        if agent.get("consecutiveIdleMoves", 0) >= 3:
            nudges.append("NOTE: You have been moving without acting. Prioritize collect_resource or contribute_resources.")
        capped = next(((k, v) for k, v in agent["resources"].items() if v >= COLLECT_CAP), None)
        if capped:
            nudges.append(f"NOTE: You are at capacity for {capped[0]} ({capped[1]}/{COLLECT_CAP}). "
                          f"Use contribute_resources or trade_resource instead of collecting more.")
        spec = self._role_specialty_resource(agent["role"])
        if spec and spec == self._first_unmet_project_resource():
            nudges.append(f"NOTE: Your role specializes in {spec}, which the active project still needs. Prioritize collect_resource.")
        if EMERGENT_ROLES:
            need_role = self._village_needed_role()
            if need_role and need_role != agent["role"] and self._is_flexible_role(agent["role"]):
                nudges.append(f"NOTE: No one is gathering {self._first_unmet_project_resource()}, "
                              f"which the build needs. Consider switch_role to {need_role} to fill the gap.")
        if RULES_ENABLED:
            unvoted = next((r for r in c["pendingRules"] if agent["name"] not in r["votes"]), None)
            if unvoted:
                nudges.append(f'NOTE: Pending rule "{unvoted["name"]}" (id {unvoted["id"]}) needs your vote. '
                              f"Use vote_rule with target {unvoted['id']} and vote yes or no.")
            elif (not c["rules"] and not c["pendingRules"]
                  and self.frameTick - c["lastRuleActivityFrame"] > BLUEPRINT_STALL_THRESHOLD):
                nudges.append("NOTE: The village has no shared rules yet. Consider propose_rule (a small resource_tax builds a shared stockpile).")
        if agent["role"] == "elder" and c["activeProject"] \
                and self.frameTick - c["lastProjectContributionFrame"] > STALL_THRESHOLD:
            stalled = self._first_unmet_project_resource()
            if stalled:
                holders = sorted((a for a in self.agents if a["resources"].get(stalled, 0) > 0),
                                 key=lambda a: a["resources"].get(stalled, 0), reverse=True)
                holder = holders[0]["name"] if holders else "no one"
                nudges.append(f"NOTE: No project progress in a while. {stalled} is still short; "
                              f"{holder} is holding the most of it. Consider assign_task or contribute_resources.")
        if len(c["pendingBlueprints"]) < MAX_PENDING_BLUEPRINTS \
                and self.frameTick - c["lastBlueprintActivityFrame"] > BLUEPRINT_STALL_THRESHOLD:
            nudges.append("NOTE: No new blueprint activity in a while. Consider propose_blueprint if you have an idea.")
        if CRAFTING_ENABLED and self.frameTick - c["lastCraftActivityFrame"] > CRAFT_STALL_THRESHOLD:
            has_workshop = any(s["type"] == "workshop" for s in c["structures"])
            if agent["role"] == "elder" and not has_workshop:
                nudges.append("NOTE: No workshop exists yet. Direct an agent to build a Workshop so the village can craft planks, bricks, and tools for advanced builds.")
            elif has_workshop:
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
            "world_zone": get_zone(agent["x"], agent["y"]),
            "civilization_level": c["level"],
            "structures_built": len(c["structures"]),
            "active_project": c["activeProject"]["name"] if c["activeProject"] else "none",
            "project_progress": self._project_progress_text(),
            "directive": c["directive"] or "none",
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
            "available_actions": self.d["AVAILABLE_ACTIONS"],
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

    def stop(self):
        self._stop.set()

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

    def snapshot(self):
        """Consistent /state snapshot per Contract 2 (copied under lock)."""
        with self.lock:
            c = self.civilization
            ap = c["activeProject"]
            active_project = None
            if ap:
                total = sum(ap["needs"].values())
                done = sum(min(ap["contributed"].get(r, 0), n) for r, n in ap["needs"].items())
                pct = round(done / total * 100) if total else 0
                progress_text = ", ".join(f"{r} {ap['contributed'].get(r, 0)}/{n}"
                                          for r, n in ap["needs"].items())
                active_project = {"name": ap["name"], "type": ap["type"],
                                  "progressText": progress_text, "progressPercent": pct}
            agents = [{
                "id": a["id"], "name": a["name"], "role": a["role"], "color": a["color"],
                "x": a["x"], "y": a["y"], "currentZone": a["currentZone"],
                "resources": dict(a["resources"]), "hunger": a["hunger"], "health": a["health"],
                "incapacitated": a["incapacitated"], "message": a["message"],
                "isThinking": a["isThinking"], "beliefs": [self._belief_text(b) for b in a["beliefs"]],
                "lastAction": a["lastAction"], "assignedTask": a["assignedTask"],
            } for a in self.agents]
            civ = {
                "level": c["level"],
                "structures": [{"id": s["id"], "type": s["type"], "x": s["x"], "y": s["y"],
                                "visualStyle": s.get("visualStyle"), "name": s.get("name")}
                               for s in c["structures"]],
                "activeProject": active_project,
                "completedProjects": c["completedProjects"],
                "resourceRegistry": {rid: dict(d) for rid, d in c["resourceRegistry"].items()},
                "projectRegistry": {pid: dict(p) for pid, p in c["projectRegistry"].items()},
                "pendingBlueprints": [dict(b) for b in c["pendingBlueprints"]],
                "pendingRecipes": [dict(r) for r in c["pendingRecipes"]],
                "rules": [dict(r) for r in c["rules"]],
                "pendingRules": [dict(r) for r in c["pendingRules"]],
                "directive": c["directive"],
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
                    },
                },
            }
