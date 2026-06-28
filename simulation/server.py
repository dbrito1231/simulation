# HOW TO RUN:
# 1. pip install flask flask-cors requests
# 2. Make sure LM Studio is running at localhost:1234 with a model loaded
# 3. python server.py
# 4. Open http://127.0.0.1:5001 in Chrome or Firefox
#    (macOS AirPlay uses port 5000 and returns 403 — do not use 5000)

import json
import os
import re
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"


class SessionLogger:
    """Append-only JSON Lines logger. One session folder per server run."""

    def __init__(self, base_dir):
        self.session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.dir = os.path.join(base_dir, "logs", self.session_id)
        os.makedirs(self.dir, exist_ok=True)
        self.activity_path = os.path.join(self.dir, "activity.jsonl")
        self.conversation_path = os.path.join(self.dir, "conversation.jsonl")
        self.lm_studio_path = os.path.join(self.dir, "lm_studio.jsonl")
        for path in [self.activity_path, self.conversation_path, self.lm_studio_path]:
            open(path, "a", encoding="utf-8").close()
        logs_root = os.path.dirname(self.dir)
        open(os.path.join(logs_root, "conversation.jsonl"), "a", encoding="utf-8").close()
        self.log_conversation(
            "system",
            "log",
            "Conversation log started. Agent speech, directives, and talk attempts are recorded here.",
            kind="session_start",
        )

    def _append(self, path, record):
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": self.session_id,
            **record,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)
            global_path = os.path.join(os.path.dirname(self.dir), os.path.basename(path))
            with open(global_path, "a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            # Logging must never break the simulation.
            pass

    def log_activity(self, message, frame_tick=None):
        self._append(self.activity_path, {
            "type": "activity", "message": message, "frame_tick": frame_tick,
        })

    def log_conversation(self, sender, recipient, message, frame_tick=None,
                         kind="speech", outcome=None):
        record = {
            "type": "conversation",
            "kind": kind,
            "from": sender,
            "to": recipient,
            "message": message,
            "frame_tick": frame_tick,
        }
        if outcome:
            record["outcome"] = outcome
        self._append(self.conversation_path, record)

    def log_lm_exchange(self, record):
        record = {"type": "lm_studio", **record}
        self._append(self.lm_studio_path, record)


session_logger = SessionLogger(os.path.dirname(os.path.abspath(__file__)))
print(f"[server] Logging session to: {session_logger.dir}")

# --- Blueprint validation constants ---
GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
BASE_RESOURCE_IDS = {"food", "wood", "gold"}
SEED_PROJECT_IDS = {"house", "farm_plot", "workshop", "wall"}
VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,24}$")
MAX_PENDING_BLUEPRINTS = 5
MAX_APPROVED_CUSTOM = 15
MAX_CUSTOM_RESOURCES = 10
# Role definitions are the single source of truth in roles.json (also served to
# the browser as /roles.js). The server derives its role maps from it so the
# client and server can never drift.
_ROLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roles.json")
with open(_ROLES_PATH, encoding="utf-8") as _f:
    ROLES = json.load(_f)

# role -> preferred project (string or list, mirroring the client).
ROLE_PROJECT = {role: d["preferredProject"] for role, d in ROLES.items()}

# --- Structured output (LM Studio response_format) ---
# Constrain the model to emit a conforming JSON decision at decode time, which
# largely eliminates the malformed-JSON fallback path. "json_schema" shapes every
# field; "json_object" only guarantees syntactic validity; "off" disables it.
# extract_json_decision/normalize_decision remain as defense in depth regardless.
STRUCTURED_OUTPUT_MODE = "json_schema"

# Full 19-action superset (mirrors AVAILABLE_ACTIONS in index.html). Per-agent
# availability is still enforced by normalize_decision/role_fallback_action.
DECISION_ACTIONS = [
    "move_to_farm", "move_to_market", "move_to_forest", "move_to_beach",
    "move_to_village", "move_to_cave", "move_to_agent",
    "collect_resource", "talk_to_nearby", "trade_resource",
    "start_project", "contribute_resources", "build_structure",
    "propose_blueprint", "approve_blueprint", "reject_blueprint",
    "assign_task", "change_role", "rest",
]

# Loose shape only; validate_blueprint() stays the authority on blueprint detail.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": DECISION_ACTIONS},
        "target": {"type": ["string", "null"]},
        "message": {"type": ["string", "null"]},
        "new_role": {"type": ["string", "null"]},
        "relationship_update": {
            "type": ["object", "null"],
            "additionalProperties": {"enum": ["ally", "neutral", "rival"]},
        },
        "reasoning": {"type": "string"},
        "blueprint": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "needs": {"type": "object"},
                "new_resources": {"type": "array"},
                "visual_style": {"type": "string"},
            },
        },
    },
}

# Flipped off for the rest of the session if LM Studio rejects response_format.
_structured_output_enabled = STRUCTURED_OUTPUT_MODE != "off"


def build_response_format():
    """The response_format payload field for the current mode, or None."""
    if not _structured_output_enabled:
        return None
    if STRUCTURED_OUTPUT_MODE == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "agent_decision", "schema": DECISION_SCHEMA},
        }
    if STRUCTURED_OUTPUT_MODE == "json_object":
        return {"type": "json_object"}
    return None


def looks_like_response_format_error(http_status, lm_body):
    """True when LM Studio rejected the request specifically over response_format."""
    text = ""
    if isinstance(lm_body, dict):
        text = str(lm_body.get("error") or lm_body)
    if http_status == 400 or text:
        low = text.lower()
        return any(k in low for k in ("response_format", "json_schema", "grammar", "schema"))
    return False

SYSTEM_PROMPT = """You are an autonomous agent in a pixel-art village simulation.
Your shared goal: help the village grow into a civilization by gathering resources,
contributing to build projects, and coordinating with others.

RULES (follow exactly):
MAIN RULE (elder only): on every turn, if any agent is idle, use assign_task to give that agent a specific job. The elder leads by keeping everyone busy. Idle agents are listed least-recently-tasked first; prefer the one marked "longest idle" unless a resource shortfall clearly calls for a different role — don't keep assigning work to the same one or two agents.
1. NEVER use talk_to_nearby if Agents near you is "none".
2. If talk_to_nearby, message and target MUST both be set to a nearby agent name.
3. Prefer collect_resource, contribute_resources, start_project, build_structure,
   or move_to_* over idle talk.
4. Talk is for coordination (request resources, announce builds)—not small talk.
5. Any agent may start_project when none is active; everyone contributes and builds as resources allow.

BLUEPRINTS (inventing new structures):
6. Any agent may use propose_blueprint to invent a new structure type. Include a
   "blueprint" object (see schema below). Optionally bundle up to 3 new gatherable
   resources inside "new_resources".
7. Only the elder may approve_blueprint or reject_blueprint. The "target"
   must be the id of a blueprint listed in Pending blueprints.
8. The elder should review Pending blueprints before starting a vanilla project
   when proposals are waiting.
9. Only propose resources that have a gather_zone (one of: farm, forest, village,
   market, beach, cave, ocean) so villagers can collect them, or set gather_zone to
   null for trade-only resources (these cannot be collected).
10. To gather a custom resource, move to its gather_zone and use collect_resource with
   target set to that resource id.
11. Don't repeat a message you or another agent already said recently (see Recent
   village conversations) — vary your wording each time you talk.

Respond with ONLY valid JSON. No markdown, no explanation, no extra text.
Do not use chain-of-thought or reasoning — output the JSON object immediately.
The JSON must match this structure exactly:
{
  "action": "<one of the available_actions>",
  "target": "<agent name, zone name, project type, resource id, blueprint id, or null>",
  "message": "<what you say if talking, or null>",
  "new_role": "<a new role name if changing role, or null>",
  "relationship_update": {"<agent_name>": "ally|neutral|rival"} or null,
  "reasoning": "<one short sentence>",
  "blueprint": <blueprint object for propose_blueprint, otherwise omit or null>
}

BLUEPRINT object schema (only for propose_blueprint):
{
  "id": "library",                       // ^[a-z][a-z0-9_]{1,24}$, not a seed/duplicate
  "name": "Library",                     // 1-32 chars
  "needs": {"wood": 4, "paper": 2},      // 1-8 entries, each amount 1-5
  "new_resources": [                      // 0-3 items, bundled new resources
    {"id": "paper", "name": "Paper", "gather_zone": "forest", "color": "#E8D5B7"}
  ],
  "visual_style": "house"                // house | farm_plot | workshop | wall | generic
}

EXAMPLE (farmer, no one nearby):
{"action":"collect_resource","target":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"I should gather food for the village."}

EXAMPLE (builder, project needs wood):
{"action":"contribute_resources","target":"wood","message":null,"new_role":null,"relationship_update":null,"reasoning":"Donating wood to the active build."}

EXAMPLE (trader, Marco nearby):
{"action":"talk_to_nearby","target":"Marco","message":"Could you spare any wood? I'll trade you food for it.","new_role":null,"relationship_update":null,"reasoning":"Coordinating trade for the build."}

EXAMPLE (gatherer proposing a library + paper):
{"action":"propose_blueprint","target":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"The village needs knowledge storage.","blueprint":{"id":"library","name":"Library","needs":{"wood":4,"paper":2},"new_resources":[{"id":"paper","name":"Paper","gather_zone":"forest","color":"#E8D5B7"}],"visual_style":"house"}}

EXAMPLE (elder approving a pending blueprint):
{"action":"approve_blueprint","target":"library","message":"Approved. Gather paper from the forest.","new_role":null,"relationship_update":null,"reasoning":"A worthy addition to the village."}"""

USER_PROMPT_TEMPLATE = """Your name: {agent_name}
Your role: {role}
Your skill: {role_skill}
Your personality: {personality}
Recent memory: {memory}
Resources: {resources}
Relationships: {relationships}
Agents near you: {nearby_agents}
Current zone: {world_zone}
Civilization level: {civilization_level}
Structures built: {structures_built}
Active project: {active_project}
Project progress: {project_progress}
Civilization directive: {directive}
Idle agents needing a task: {idle_agents}
Known resources: {known_resources}
Pending blueprints: {pending_blueprints}
Approved custom builds: {approved_custom_projects}
Rejected blueprints (do NOT re-propose these ids): {rejected_blueprints}
Recent village conversations: {recent_conversations}
{behavior_nudge}
Available actions: {available_actions}

What do you do next? Respond with only the JSON."""


def format_nearby_agents(nearby):
    """Format nearby agents as 'none' or a detailed string."""
    if not nearby or nearby == "none":
        return "none"
    if isinstance(nearby, str):
        return nearby
    if isinstance(nearby, list):
        if len(nearby) == 0:
            return "none"
        parts = []
        for item in nearby:
            if isinstance(item, dict):
                name = item.get("name", "?")
                role = item.get("role", "?")
                food = item.get("food", 0)
                wood = item.get("wood", 0)
                gold = item.get("gold", 0)
                parts.append(f"{name} ({role}, food:{food} wood:{wood} gold:{gold})")
            else:
                parts.append(str(item))
        return "; ".join(parts)
    return str(nearby)


def parse_nearby_names(nearby):
    """Extract agent names from formatted or structured nearby data."""
    if not nearby or nearby == "none":
        return []
    if isinstance(nearby, str):
        if nearby.strip().lower() == "none":
            return []
        names = []
        for part in nearby.split(";"):
            part = part.strip()
            if not part:
                continue
            name = part.split("(")[0].strip()
            if name:
                names.append(name)
        return names
    if isinstance(nearby, list):
        names = []
        for item in nearby:
            if isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
            elif isinstance(item, str):
                names.append(item)
        return names
    return []


def format_known_resources(resources):
    """Format known resources for the prompt, e.g. 'food (farm), paper (forest, custom)'."""
    if not resources or not isinstance(resources, list):
        return "food (farm), wood (forest), gold (cave)"
    parts = []
    for r in resources:
        if not isinstance(r, dict):
            continue
        rid = r.get("id", "?")
        zone = r.get("gather_zone") or "trade-only"
        tag = ", custom" if r.get("custom") else ""
        parts.append(f"{rid} ({zone}{tag})")
    return ", ".join(parts) if parts else "none"


def format_pending_blueprints(pending):
    """Format pending blueprints for the prompt."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for b in pending:
        if not isinstance(b, dict):
            continue
        needs = b.get("needs") or {}
        needs_str = ", ".join(f"{k} {v}" for k, v in needs.items())
        by = b.get("proposed_by", "?")
        parts.append(f"{b.get('id', '?')} by {by} (needs {needs_str})")
    return "; ".join(parts) if parts else "none"


def format_approved_custom(approved):
    """Format approved custom build ids for the prompt."""
    if not approved or not isinstance(approved, list):
        return "none"
    ids = [str(a) for a in approved if a]
    return ", ".join(ids) if ids else "none"


def format_rejected_blueprints(rejected):
    """Format rejected blueprint ids for the prompt."""
    if not rejected or not isinstance(rejected, list):
        return "none"
    ids = [str(r) for r in rejected if r]
    return ", ".join(ids) if ids else "none"


def format_idle_agents(idle_agents):
    """Format idle agents for the elder prompt. Ordered least-recently-tasked
    first; the first entry is tagged so the elder spreads work fairly instead
    of always picking the same agent."""
    if not idle_agents or not isinstance(idle_agents, list):
        return "none"
    parts = []
    for agent in idle_agents:
        if not isinstance(agent, dict):
            continue
        name = agent.get("name")
        role = agent.get("role")
        tag = ", longest idle" if agent.get("longest_idle") else ""
        debt = agent.get("contribution_debt")
        if isinstance(debt, (int, float)) and debt > 0:
            tag += f", debt {int(debt)} ticks"
        if name:
            parts.append(f"{name} ({role or 'unknown'}{tag})")
    return "; ".join(parts) if parts else "none"


def role_default_project(role):
    pref = ROLE_PROJECT.get((role or "").lower(), "house")
    # preferredProject may be a list (e.g. builder -> ["house", "wall"]); pick
    # the first deterministically.
    if isinstance(pref, list):
        return pref[0] if pref else "house"
    return pref


# resource id -> tuple of roles that specialize in gathering it, derived by
# inverting each role's specialty list in roles.json (captures miner -> gold+stone).
def _build_resource_gather_roles():
    out = {}
    for role, d in ROLES.items():
        for res in d.get("specialty", []):
            out.setdefault(res, []).append(role)
    return {res: tuple(roles) for res, roles in out.items()}


RESOURCE_GATHER_ROLES = _build_resource_gather_roles()

# role -> its primary specialty resource (first in the specialty list), used to
# phrase task assignments. Only roles with a specialty appear.
ROLE_PRIMARY_RESOURCE = {
    role: d["specialty"][0] for role, d in ROLES.items() if d.get("specialty")
}


def parse_project_shortfalls(project_progress):
    """Parse 'wood 0/3, food 1/1' into [(resource, amount_still_needed), ...]."""
    if not project_progress or project_progress in ("none", "null"):
        return []
    shortfalls = []
    for part in str(project_progress).split(","):
        match = re.match(r"(\w+)\s+(\d+)\s*/\s*(\d+)", part.strip())
        if not match:
            continue
        res, have, need = match.group(1), int(match.group(2)), int(match.group(3))
        if have < need:
            shortfalls.append((res, need - have))
    return shortfalls


def pick_idle_agent_for_project(idle_agents, project_progress):
    """Prefer idle agents whose role gathers the resource the project still needs."""
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        preferred_roles = RESOURCE_GATHER_ROLES.get(needed_res, ())
        for role in preferred_roles:
            for agent in idle_agents:
                if (agent.get("role") or "").lower() == role:
                    return agent
    return idle_agents[0] if idle_agents else None


def task_for_role(role, active_project=None, project_progress=None):
    role = (role or "").lower()
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        if ROLE_PRIMARY_RESOURCE.get(role) == needed_res:
            return f"gather {needed_res} for the active project"
        return f"gather or contribute {needed_res} to the active project"
    if active_project and active_project not in ("none", "null", None, ""):
        return f"gather or contribute resources to {active_project}"
    project = role_default_project(role).replace("_", " ")
    return f"prepare to start a {project} project"


def first_shortfall_resource(agent_data):
    shortfalls = parse_project_shortfalls(agent_data.get("project_progress"))
    return shortfalls[0][0] if shortfalls else None


def validate_blueprint(blueprint, known_resource_ids, pending_ids, approved_ids,
                       custom_resource_count, rejected_ids=None):
    """Validate a proposed blueprint. Returns (ok: bool, reason: str|None)."""
    rejected_ids = rejected_ids or []
    if not isinstance(blueprint, dict):
        return False, "blueprint must be an object"

    if len(pending_ids) >= MAX_PENDING_BLUEPRINTS:
        return False, "too many pending blueprints"
    if len(approved_ids) >= MAX_APPROVED_CUSTOM:
        return False, "too many approved blueprints"

    bid = blueprint.get("id")
    if not isinstance(bid, str) or not SLUG_RE.match(bid):
        return False, "invalid id"
    if bid in SEED_PROJECT_IDS:
        return False, "id collides with a seed template"
    if bid in pending_ids or bid in approved_ids:
        return False, "duplicate blueprint id"
    if bid in rejected_ids:
        return False, "blueprint was previously rejected"

    name = blueprint.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 32):
        return False, "invalid name"

    new_resources = blueprint.get("new_resources") or []
    if not isinstance(new_resources, list) or len(new_resources) > 3:
        return False, "new_resources must be 0-3 items"

    new_ids = set()
    for r in new_resources:
        if not isinstance(r, dict):
            return False, "new_resource must be an object"
        rid = r.get("id")
        if not isinstance(rid, str) or not SLUG_RE.match(rid):
            return False, "invalid resource id"
        if rid in BASE_RESOURCE_IDS:
            return False, "resource id shadows a base resource"
        if rid in set(known_resource_ids) or rid in new_ids:
            return False, "resource already exists"
        rname = r.get("name")
        if not isinstance(rname, str) or not (1 <= len(rname) <= 32):
            return False, "invalid resource name"
        gz = r.get("gather_zone")
        if gz is not None and gz not in GATHER_ZONES:
            return False, "invalid gather_zone"
        new_ids.add(rid)

    if custom_resource_count + len(new_ids) > MAX_CUSTOM_RESOURCES:
        return False, "too many custom resources"

    needs = blueprint.get("needs")
    if not isinstance(needs, dict) or not (1 <= len(needs) <= 8):
        return False, "needs must have 1-8 entries"
    available = set(known_resource_ids) | new_ids | BASE_RESOURCE_IDS
    for key, amount in needs.items():
        if key not in available:
            return False, f"unknown resource in needs: {key}"
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "need amount must be 1-5"

    visual_style = blueprint.get("visual_style", "generic")
    if visual_style not in VISUAL_STYLES:
        return False, "invalid visual_style"

    return True, None


def role_fallback_action(role, agent_data):
    """Return a role-appropriate fallback decision when talk is invalid."""
    role = (role or "").lower()
    active_project = agent_data.get("active_project")
    has_project = active_project and active_project not in ("none", "null", None, "")

    pending_ids = agent_data.get("pending_blueprint_ids") or []
    if role == "elder" and pending_ids:
        return {"action": "approve_blueprint", "target": pending_ids[0], "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Reviewing a pending blueprint proposal."}

    idle_agents = agent_data.get("idle_agents") or []
    if role == "elder" and idle_agents:
        project_progress = agent_data.get("project_progress")
        target = pick_idle_agent_for_project(idle_agents, project_progress)
        target_name = target.get("name") if target else None
        if target_name:
            return {"action": "assign_task", "target": target_name,
                    "message": task_for_role(
                        target.get("role"), active_project, project_progress,
                    ),
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Assigning work to an idle villager."}

    if not has_project:
        return {"action": "start_project", "target": role_default_project(role), "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Starting a role-appropriate build project."}

    if role in ("farmer", "fisher", "gatherer"):
        zone = agent_data.get("world_zone", "")
        if role == "farmer" and zone != "farm":
            return {"action": "move_to_farm", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to farm to gather food."}
        if role == "gatherer" and zone != "forest":
            return {"action": "move_to_forest", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to forest to gather wood."}
        if role == "fisher" and zone != "beach":
            return {"action": "move_to_beach", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to beach to fish."}
        needed = first_shortfall_resource(agent_data)
        return {"action": "collect_resource", "target": needed, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Gathering resources for the village."}

    if role == "miner":
        zone = agent_data.get("world_zone", "")
        if zone != "cave":
            return {"action": "move_to_cave", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to the cave to mine."}
        needed = first_shortfall_resource(agent_data) or "gold"
        return {"action": "collect_resource", "target": needed, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Mining gold for civilization."}

    if role == "builder":
        needed = first_shortfall_resource(agent_data) or "wood"
        return {"action": "contribute_resources", "target": needed, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Contributing to the active project."}

    if role == "trader":
        return {"action": "move_to_market", "target": None, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Heading to market to trade."}

    if role in ("guard", "scout", "explorer"):
        return {"action": "move_to_village", "target": None, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Patrolling the village."}

    if role in ("healer", "elder", "blacksmith"):
        if has_project:
            needed = first_shortfall_resource(agent_data)
            return {"action": "contribute_resources", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Supporting the village build."}
        return {"action": "move_to_village", "target": None, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Returning to the village center."}

    return {"action": "collect_resource", "target": None, "message": None,
            "new_role": None, "relationship_update": None,
            "reasoning": "Working toward civilization goals."}


def normalize_decision(decision, agent_data):
    """Reject invalid talk_to_nearby and substitute role fallback."""
    if not isinstance(decision, dict):
        return role_fallback_action(agent_data.get("role"), agent_data)

    action = decision.get("action", "rest")
    nearby_raw = agent_data.get("nearby_agents")
    nearby_names = parse_nearby_names(nearby_raw)
    nearby_empty = len(nearby_names) == 0

    if action == "propose_blueprint":
        known_ids = agent_data.get("known_resource_ids") or []
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        approved_ids = agent_data.get("approved_blueprint_ids") or []
        rejected_ids = agent_data.get("rejected_blueprint_ids") or []
        custom_count = agent_data.get("custom_resource_count", 0)
        ok, _reason = validate_blueprint(
            decision.get("blueprint"), known_ids, pending_ids, approved_ids, custom_count,
            rejected_ids,
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid blueprint)").strip()
            return fallback
        return decision

    if action in ("approve_blueprint", "reject_blueprint"):
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        if role != "elder" or not target or target not in pending_ids:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid blueprint action)").strip()
            return fallback
        return decision

    if action == "assign_task":
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        idle_names = [a.get("name") for a in agent_data.get("idle_agents") or [] if isinstance(a, dict)]
        if role != "elder" or not target or target not in idle_names or not decision.get("message"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid task assignment)").strip()
            return fallback
        return decision

    if action != "talk_to_nearby":
        if isinstance(decision, dict):
            decision.pop("blueprint", None)
        return decision

    target = decision.get("target")
    message = decision.get("message")
    invalid_talk = (
        nearby_empty
        or not target
        or not message
        or target not in nearby_names
    )

    if invalid_talk:
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + " (redirected from talk)").strip()
        return fallback

    decision.pop("blueprint", None)
    return decision


@app.route("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")


@app.route("/sprites.js")
def sprites():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "sprites.js")


@app.route("/roles.js")
def roles_js():
    # Serve the single role source as a JS global so the browser uses the exact
    # same data the server derives its maps from.
    body = f"const ROLES = {json.dumps(ROLES)};"
    return app.response_class(body, mimetype="application/javascript")


@app.route("/log/event", methods=["POST"])
def log_event():
    """Persist a browser-origin activity or conversation event."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        event_type = body.get("type")
        frame_tick = body.get("frame_tick")
        if event_type == "activity":
            session_logger.log_activity(body.get("message", ""), frame_tick)
        elif event_type == "conversation":
            session_logger.log_conversation(
                body.get("from", ""),
                body.get("to", ""),
                body.get("message"),
                frame_tick,
                kind=body.get("kind", "speech"),
                outcome=body.get("outcome"),
            )
        # Unknown types are ignored; logging must never break the simulation.
    except Exception:
        pass
    return ("", 204)


def build_agent_data(data, nearby_formatted, known_resources, pending_blueprints,
                     approved_custom_projects, rejected_blueprints):
    """Assemble agent context used by normalize_decision and role_fallback_action."""
    agent_data = dict(data)
    agent_data["nearby_agents"] = nearby_formatted
    agent_data["known_resource_ids"] = [
        r.get("id") for r in known_resources if isinstance(r, dict) and r.get("id")
    ]
    agent_data["custom_resource_count"] = sum(
        1 for r in known_resources if isinstance(r, dict) and r.get("custom")
    )
    agent_data["pending_blueprint_ids"] = [
        b.get("id") for b in pending_blueprints if isinstance(b, dict) and b.get("id")
    ]
    agent_data["approved_blueprint_ids"] = [
        str(a) for a in approved_custom_projects if a
    ]
    agent_data["rejected_blueprint_ids"] = [
        str(r) for r in rejected_blueprints if r
    ]
    agent_data["idle_agents"] = [
        a for a in data.get("idle_agents") or [] if isinstance(a, dict) and a.get("name")
    ]
    return agent_data


def strip_code_fences(text):
    """Remove markdown ```json ... ``` fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: -3]
    return cleaned.strip()


def lm_message_text(message):
    """Return model output text; reasoning models may leave content empty."""
    if not isinstance(message, dict):
        return ""
    content = (message.get("content") or "").strip()
    if content:
        return content
    return (message.get("reasoning_content") or "").strip()


def extract_json_decision(text):
    """Parse a decision object from model output, including partial/truncated JSON."""
    if not text or not isinstance(text, str):
        return None

    cleaned = strip_code_fences(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    start = cleaned.find("{")
    if start == -1:
        return None

    depth = 0
    for idx in range(start, len(cleaned)):
        char = cleaned[idx]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(cleaned[start:idx + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except (ValueError, TypeError):
                    break

    action_match = re.search(r'"action"\s*:\s*"([^"]+)"', cleaned)
    if not action_match:
        return None

    decision = {
        "action": action_match.group(1),
        "target": None,
        "message": None,
        "new_role": None,
        "relationship_update": None,
        "reasoning": "Parsed from partial model response.",
    }
    target_match = re.search(r'"target"\s*:\s*(?:"([^"]*)"|null)', cleaned)
    if target_match:
        decision["target"] = target_match.group(1) or None
    message_match = re.search(r'"message"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|null)', cleaned)
    if message_match:
        decision["message"] = message_match.group(1) or None
    return decision


@app.route("/agent/think", methods=["POST"])
def agent_think():
    try:
        data = request.get_json(force=True) or {}

        nearby_formatted = format_nearby_agents(data.get("nearby_agents"))
        behavior_nudge = data.get("behavior_nudge") or ""

        known_resources = data.get("known_resources") or []
        pending_blueprints = data.get("pending_blueprints") or []
        approved_custom_projects = data.get("approved_custom_projects") or []
        rejected_blueprints = data.get("rejected_blueprints") or []
        idle_agents = data.get("idle_agents") or []

        user_prompt = USER_PROMPT_TEMPLATE.format(
            agent_name=data.get("agent_name"),
            role=data.get("role"),
            role_skill=data.get("role_skill", ""),
            personality=data.get("personality"),
            memory=data.get("memory"),
            resources=data.get("resources"),
            relationships=data.get("relationships"),
            nearby_agents=nearby_formatted,
            world_zone=data.get("world_zone"),
            civilization_level=data.get("civilization_level", 1),
            structures_built=data.get("structures_built", 0),
            active_project=data.get("active_project", "none"),
            project_progress=data.get("project_progress", "none"),
            directive=data.get("directive", "none"),
            idle_agents=format_idle_agents(idle_agents),
            known_resources=format_known_resources(known_resources),
            pending_blueprints=format_pending_blueprints(pending_blueprints),
            approved_custom_projects=format_approved_custom(approved_custom_projects),
            rejected_blueprints=format_rejected_blueprints(rejected_blueprints),
            recent_conversations=data.get("recent_conversations", "none"),
            behavior_nudge=behavior_nudge,
            available_actions=data.get("available_actions"),
        )

        payload = {
            "model": "local-model",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 512,
            "temperature": 0.4,
            "stream": False,
            "thinking": {"type": "disabled", "budget_tokens": 0},
        }
        response_format = build_response_format()
        if response_format is not None:
            payload["response_format"] = response_format

        agent_name = data.get("agent_name")
        frame_tick = data.get("frame_tick")
        agent_data = build_agent_data(
            data, nearby_formatted, known_resources, pending_blueprints,
            approved_custom_projects, rejected_blueprints,
        )

        def log_lm(latency_ms, response=None, http_status=None, decision=None, error=None):
            session_logger.log_lm_exchange({
                "agent_name": agent_name,
                "frame_tick": frame_tick,
                "latency_ms": latency_ms,
                "request": payload,
                "response": response,
                "http_status": http_status,
                "decision": decision,
                "error": error,
            })

        def bad_response_fallback(latency_ms, response=None, http_status=None):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            log_lm(latency_ms, response=response, http_status=http_status,
                   decision=fallback, error="bad_response")
            return jsonify(fallback)

        global _structured_output_enabled

        start = datetime.now()
        try:
            resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
        except requests.exceptions.RequestException:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            log_lm(latency_ms, error="LM Studio offline")
            return jsonify({"error": "LM Studio offline", "action": "rest"})

        latency_ms = int((datetime.now() - start).total_seconds() * 1000)
        http_status = resp.status_code

        try:
            lm_body = resp.json()
        except ValueError:
            lm_body = None

        # Auto-degrade: if LM Studio rejected response_format (model doesn't
        # support structured output), disable it for the session and retry once
        # so this turn still succeeds. Prevents a regression to all-fallback.
        if ("response_format" in payload and _structured_output_enabled
                and looks_like_response_format_error(http_status, lm_body)):
            print("[server] LM Studio rejected response_format; disabling "
                  "structured output for this session and retrying without it.")
            _structured_output_enabled = False
            payload.pop("response_format", None)
            start = datetime.now()
            try:
                resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
            except requests.exceptions.RequestException:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_lm(latency_ms, error="LM Studio offline")
                return jsonify({"error": "LM Studio offline", "action": "rest"})
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

        if lm_body is None:
            return bad_response_fallback(latency_ms, http_status=http_status)

        if isinstance(lm_body, dict) and lm_body.get("error"):
            err = str(lm_body.get("error"))
            if "compute error" in err.lower():
                log_lm(latency_ms, response=lm_body, http_status=http_status, error="compute_error")
                return jsonify({"error": "compute_error", "action": "rest"})
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

        try:
            message = lm_body["choices"][0]["message"]
        except (TypeError, KeyError, IndexError):
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

        raw_text = lm_message_text(message)
        decision = extract_json_decision(raw_text)
        if not decision and isinstance(message, dict):
            decision = extract_json_decision(message.get("content") or "")
        if not decision:
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

        decision = normalize_decision(decision, agent_data)

        log_lm(latency_ms, response=lm_body, http_status=http_status, decision=decision, error=None)
        return jsonify(decision)

    except Exception:
        return jsonify({"error": "server_error", "action": "rest"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)
