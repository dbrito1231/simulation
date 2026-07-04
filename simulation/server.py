# HOW TO RUN:
# 1. pip install flask flask-cors requests
# 2. Make sure LM Studio is running at localhost:1234 with a model loaded
# 3. python server.py
# 4. Open http://127.0.0.1:5001 in Chrome or Firefox
#    (macOS AirPlay uses port 5000 and returns 403 — do not use 5000)

import atexit
import hashlib
import json
import math
import os
import re
import signal
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"

# Model routing: high-stakes turns (the elder's leadership/approval decisions,
# and any villager turn taken while invention is REQUIRED, i.e. blueprint
# authoring) go to MODEL_SMART; routine villager turns and background
# cognition go to MODEL_FAST. Ids must match LM Studio's loaded-model ids
# (GET /v1/models). If a routed id isn't available, run_agent_decision
# degrades to "local-model" for the rest of the session (same pattern as the
# response_format auto-degrade), so a single-model setup keeps working.
#
# Both tiers currently resolve to gemma: the 2026-07-02 session showed
# llama-3.2-3b picking move_to_district on 95% of 2,764 villager turns (the
# ~3,100-token prompt is beyond a 3B) while ALSO running slower than gemma
# (6.6s vs 4.6s avg -- its Q8 quant spilled to CPU next to gemma on a 12GB
# card). Slot a real secondary back in here only if it's <=4GB on-GPU AND
# demonstrably handles the decision prompt; otherwise one good model wins.
MODEL_SMART = "google/gemma-4-e4b"
MODEL_FAST = "google/gemma-4-e4b"


def model_for_decision(data):
    if data.get("invention_only"):
        return MODEL_SMART
    if (data.get("role") or "").lower() == "elder":
        return MODEL_SMART
    if str(data.get("invention_status") or "").startswith("REQUIRED"):
        return MODEL_SMART
    return MODEL_FAST


class SessionLogger:
    """Append-only JSON Lines logger. One session folder per server run."""

    def __init__(self, base_dir):
        self.session_id = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.dir = os.path.join(base_dir, "logs", self.session_id)
        os.makedirs(self.dir, exist_ok=True)
        self.activity_path = os.path.join(self.dir, "activity.jsonl")
        self.conversation_path = os.path.join(self.dir, "conversation.jsonl")
        self.lm_studio_path = os.path.join(self.dir, "lm_studio.jsonl")
        # benchmarks.jsonl (Phase 0/8): a dedicated metrics stream so Sid-like
        # features can be measured (specialization index, rule adherence,
        # meme adoption, memory-store size, module-activation timeline).
        self.benchmark_path = os.path.join(self.dir, "benchmarks.jsonl")
        for path in [self.activity_path, self.conversation_path, self.lm_studio_path,
                     self.benchmark_path]:
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

    def log_benchmark(self, metric, value, frame_tick=None, detail=None):
        record = {
            "type": "benchmark",
            "metric": metric,
            "value": value,
            "frame_tick": frame_tick,
        }
        if detail is not None:
            record["detail"] = detail
        self._append(self.benchmark_path, record)


session_logger = SessionLogger(os.path.dirname(os.path.abspath(__file__)))
print(f"[server] Logging session to: {session_logger.dir}")


# --- Phase 1: in-process vector memory store (replaces ChromaDB/Docker) ---
# CMA's shared vector store + Sid's WM/STM/LTM tiers, kept in-process to honor
# the no-external-service ethos. Embedding is a deterministic hashing trick
# (bag-of-tokens hashed into a fixed dimension, L2-normalized) so cosine
# similarity == dot product. Swappable for a real embedding model / Chroma
# later behind the identical /memory/* endpoints.
MEMORY_DIM = 128
MEMORY_MAX_ENTRIES = 1200       # global cap; the cleaner trims past this
MEMORY_PERSIST_EVERY = 12       # debounce: rewrite memory.json every N stores
_MEMORY_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Tokens that carry no salience signal, dropped before embedding.
_MEMORY_STOPWORDS = frozenset(
    "the a an and or to of for in on at is are was were be been has have had "
    "i you he she it we they me him her them my your his its our their this "
    "that with from into nothing none".split()
)


def _stable_hash(token):
    """Process-stable hash so persisted vectors survive a reload."""
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)


def embed_text(text):
    """Hashing-trick embedding: L2-normalized bag-of-tokens vector."""
    vec = [0.0] * MEMORY_DIM
    if not text:
        return vec
    for tok in _MEMORY_TOKEN_RE.findall(text.lower()):
        if tok in _MEMORY_STOPWORDS:
            continue
        vec[_stable_hash(tok) % MEMORY_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


# Reasoning models (e.g. qwen3.5) sometimes route their entire output --
# chain-of-thought scaffold included -- into `reasoning_content` instead of
# `content`. Left unchecked, that scaffold gets stored verbatim as agent
# memory and re-enters every future prompt via compose_memory(). These two
# helpers extract the real answer and reject anything that still looks like
# leaked scaffolding, for both the plain-text LLM path (lm_complete) and the
# memory stores that may already hold poisoned entries (MemoryStore.clean,
# and the engine's longTerm lists -- see _ENGINE_DEPS below).
_SCAFFOLD_MARKER_RE = re.compile(
    r"(thinking process|\*\*analyze|let'?s think|let me think|"
    r"chain[- ]of[- ]thought|step[- ]by[- ]step)",
    re.IGNORECASE,
)
_SCAFFOLD_LEADING_LIST_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s+")


def is_scaffold_text(text):
    """True if `text` looks like leaked chain-of-thought scaffold rather than
    a clean plain-text answer."""
    if not text:
        return False
    if _SCAFFOLD_MARKER_RE.search(text):
        return True
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) > 2:
        return True
    return any(_SCAFFOLD_LEADING_LIST_RE.match(ln) for ln in lines)


def extract_plain_answer(text):
    """Pull the real answer out of raw reasoning-model scaffold text: the
    answer follows the scaffold, so take the final non-empty line/segment and
    strip any leftover list markers or quoting."""
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    answer = _SCAFFOLD_LEADING_LIST_RE.sub("", lines[-1])
    answer = answer.strip(" \"'").strip()
    return answer or None


class MemoryStore:
    """Append-on-write / query-on-read memory with WM/STM/LTM tiers.

    Thread-safe (the Flask dev server handles think requests concurrently).
    Tier assignment is by salience + kind; the cleaner ages and prunes.
    """

    TIERS = ("working", "shortTerm", "longTerm")

    def __init__(self, path):
        self.path = path
        self.entries = []
        self._next_id = 1
        self._since_persist = 0
        self._lock = threading.Lock()

    @staticmethod
    def _tier_for(salience, kind):
        if kind in ("summary", "autobiography"):
            return "longTerm"
        if salience >= 0.7:
            return "shortTerm"
        return "working"

    def store(self, agent, text, salience=0.5, kind="event", frame_tick=None,
              tier=None):
        text = (text or "").strip()
        if not text:
            return None
        try:
            salience = max(0.0, min(1.0, float(salience)))
        except (TypeError, ValueError):
            salience = 0.5
        entry = {
            "id": self._next_id,
            "agent": agent or "?",
            "text": text[:280],
            "vec": embed_text(text),
            "salience": salience,
            "kind": kind or "event",
            "tier": tier or self._tier_for(salience, kind),
            "frame_tick": frame_tick,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._next_id += 1
            self.entries.append(entry)
            self._trim_locked()
            self._since_persist += 1
            should_persist = self._since_persist >= MEMORY_PERSIST_EVERY
            if should_persist:
                self._since_persist = 0
        if should_persist:
            self._persist()
        return entry

    def query(self, agent=None, text="", top_k=5, tier=None, kinds=None):
        qv = embed_text(text)
        kinds = set(kinds) if kinds else None
        scored = []
        with self._lock:
            snapshot = list(self.entries)
        for e in snapshot:
            if agent and e["agent"] != agent:
                continue
            if tier and e["tier"] != tier:
                continue
            if kinds and e["kind"] not in kinds:
                continue
            # Cosine relevance plus a small salience/recency prior so important
            # and fresh memories surface even on a weak text match.
            score = _cosine(qv, e["vec"]) + 0.12 * e["salience"]
            scored.append((score, e["id"], e))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [e for _, _, e in scored[:max(1, int(top_k or 5))]]

    def recent(self, agent=None, limit=8, tier=None):
        with self._lock:
            snapshot = list(self.entries)
        if agent:
            snapshot = [e for e in snapshot if e["agent"] == agent]
        if tier:
            snapshot = [e for e in snapshot if e["tier"] == tier]
        return snapshot[-max(1, int(limit)):]

    def _trim_locked(self):
        """Drop the lowest-value entries once over the global cap."""
        if len(self.entries) <= MEMORY_MAX_ENTRIES:
            return
        # Keep summaries/autobiography and high-salience items; evict the rest
        # oldest-first until back under the cap.
        def value(e):
            keep = 1 if e["kind"] in ("summary", "autobiography") else 0
            return (keep, e["salience"], e["id"])
        self.entries.sort(key=value)
        overflow = len(self.entries) - MEMORY_MAX_ENTRIES
        self.entries = self.entries[overflow:]
        self.entries.sort(key=lambda e: e["id"])

    def clean(self):
        """Memory Cleaner: drop scaffold-poisoned entries (leaked
        chain-of-thought text from a reasoning model, see is_scaffold_text),
        then exact-duplicate texts per agent (keeping the most salient/newest
        copy), then re-trim to the cap. Deterministic and cheap so it can run
        often without burning LLM calls."""
        with self._lock:
            best = {}
            for e in self.entries:
                if is_scaffold_text(e["text"]):
                    continue
                key = (e["agent"], e["text"])
                prev = best.get(key)
                if prev is None or (e["salience"], e["id"]) > (prev["salience"], prev["id"]):
                    best[key] = e
            kept = sorted(best.values(), key=lambda e: e["id"])
            removed = len(self.entries) - len(kept)
            self.entries = kept
            self._trim_locked()
            self._since_persist = 0
        # Always flush on clean so memory.json reliably exists for inspection.
        self._persist()
        return removed

    def size(self):
        with self._lock:
            return len(self.entries)

    def export_entries(self):
        """Entries WITHOUT the recomputable `vec` field, for full-state
        persistence (Contract 3)."""
        with self._lock:
            return [{k: v for k, v in e.items() if k != "vec"} for e in self.entries]

    def import_entries(self, rows):
        """Rebuild the store from persisted rows, re-embedding each text.
        Replaces all current entries (used on resume from state.json)."""
        rebuilt = []
        max_id = 0
        for r in rows or []:
            try:
                text = (r.get("text") or "").strip()
                if not text:
                    continue
                eid = int(r.get("id") or 0)
                max_id = max(max_id, eid)
                sal = float(r.get("salience", 0.5))
                kind = r.get("kind") or "event"
                rebuilt.append({
                    "id": eid,
                    "agent": r.get("agent") or "?",
                    "text": text[:280],
                    "vec": embed_text(text),
                    "salience": max(0.0, min(1.0, sal)),
                    "kind": kind,
                    "tier": r.get("tier") or self._tier_for(sal, kind),
                    "frame_tick": r.get("frame_tick"),
                    "ts": r.get("ts") or datetime.now(timezone.utc).isoformat(),
                })
            except (TypeError, ValueError):
                continue
        with self._lock:
            self.entries = sorted(rebuilt, key=lambda e: e["id"])
            self._next_id = max_id + 1
            self._trim_locked()

    def clear(self):
        """Wipe all entries (used by engine.reset() so a reset starts the
        world with no carried-over agent memories)."""
        with self._lock:
            self.entries = []
            self._next_id = 1
            self._since_persist = 0
        self._persist()

    def tier_counts(self):
        counts = {t: 0 for t in self.TIERS}
        with self._lock:
            for e in self.entries:
                counts[e["tier"]] = counts.get(e["tier"], 0) + 1
        return counts

    def _persist(self):
        try:
            with self._lock:
                # memory.json is a per-session inspection artifact that is never
                # read back, so omit the 128-float "vec" of each entry — it's
                # pure bloat on disk and recomputable from the text if needed.
                payload = {
                    "session_id": os.path.basename(os.path.dirname(self.path)),
                    "size": len(self.entries),
                    "entries": [
                        {k: v for k, v in e.items() if k != "vec"}
                        for e in self.entries
                    ],
                }
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            # Persistence must never break the simulation.
            pass


memory_store = MemoryStore(os.path.join(session_logger.dir, "memory.json"))

# --- Blueprint validation constants ---
GATHER_ZONES = {"farm", "forest", "village", "market", "beach", "cave", "ocean"}
BASE_RESOURCE_IDS = {"food", "wood", "gold"}
SEED_PROJECT_IDS = {"house", "farm_plot", "workshop", "wall"}
TERRAFORM_PROJECT_IDS = frozenset({"plant_grove", "clear_field", "extend_beach"})
VISUAL_STYLES = {"house", "farm_plot", "workshop", "wall", "generic"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,24}$")
FUNCTION_EFFECT_KEYS = ("produces", "boosts", "unlocks", "stores", "houses")
VALID_PRODUCE_SCOPES = {"village", "district"}
VALID_BOOST_KINDS = {"gather", "craft"}
VALID_BOOST_SCOPES = {"village", "district"}
VALID_UNLOCK_KINDS = {"craft"}
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

# Full action superset (mirrors AVAILABLE_ACTIONS in index.html). Per-agent
# availability is still enforced by normalize_decision/role_fallback_action.
# World-expansion plan: the fixed move_to_farm/move_to_market/etc. members were
# replaced by a single generic move_to_district (target names a district id,
# or a legacy kind name -- sim_engine.py's _resolve_target_district resolves
# either). Hardcoding a move_to_X per district doesn't scale once districts
# can be founded at runtime rather than fixed at code-authoring time.
DECISION_ACTIONS = [
    "move_to_district", "move_to_agent",
    "collect_resource", "talk_to_nearby", "trade_resource",
    "start_project", "contribute_resources", "build_structure",
    "start_terraform",
    "propose_blueprint", "approve_blueprint", "reject_blueprint",
    "assign_task", "change_role", "rest",
    # Survival (#2) and crafting (#4) actions. The client gates these by flag,
    # but the schema enum is a fixed superset (normalize_decision filters).
    "heal_agent",
    "craft_item", "propose_recipe", "approve_recipe", "reject_recipe",
    # CMA + Sid enhancement actions (emergent roles + collective rules/voting).
    "switch_role", "propose_rule", "vote_rule",
]

# Loose shape only; validate_blueprint() stays the authority on blueprint detail.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": DECISION_ACTIONS},
        "target": {"type": ["string", "null"]},
        "target_district": {"type": ["string", "null"]},
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
                "function": {"type": "object"},
            },
        },
        "recipe": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "inputs": {"type": "object"},
                "station": {"type": ["string", "null"]},
            },
        },
        "rule": {
            "type": ["object", "null"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "kind": {"type": "string"},
                "value": {"type": ["number", "string", "null"]},
                "description": {"type": ["string", "null"]},
            },
        },
        "vote": {"type": ["string", "null"]},
    },
}

# Flipped off for the rest of the session if LM Studio rejects response_format.
_structured_output_enabled = STRUCTURED_OUTPUT_MODE != "off"

# Flipped off for the rest of the session if LM Studio doesn't know the routed
# model ids (MODEL_SMART/MODEL_FAST) -- falls back to "local-model", which LM
# Studio resolves to whatever single model is loaded.
_model_routing_enabled = True


def looks_like_model_not_found_error(http_status, lm_body):
    """True when LM Studio rejected the request because the requested model id
    isn't downloaded/loaded (as opposed to any other error)."""
    text = ""
    if isinstance(lm_body, dict):
        text = str(lm_body.get("error") or "")
    low = text.lower()
    return bool(low) and "model" in low and any(
        k in low for k in ("not found", "no model", "failed to load", "unknown model"))


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
   or move_to_district over idle talk.
4. Talk is for coordination (request resources, announce builds)—not small talk.
5. The village has SEVERAL buildable districts at once (see Known districts) and can have up to a few concurrent
   builds in progress. Any agent may start_project in a district that has no active build; set target_district to
   steer which one (it defaults to your current district). contribute_resources and build_structure also accept an
   optional target_district (defaults to your current district, or the district most in need of help).
5b. If "Incoming messages" lists requests or directives addressed to you, act on them this turn (gather/contribute/heal/trade as asked, or reply with talk_to_nearby).
5c. Use move_to_district with target set to a district id from Known districts (e.g. "farm_north", "village_east") to travel there. You'll automatically walk the road network to get there.

ECOLOGY (when enabled):
5d. Each district has local resource stocks that deplete when you gather and regrow over time. If Local stocks shows "depleted" or "low", gathering that resource here fails until stocks recover — use start_terraform (plant_grove restores forest wood/herbs; clear_field restores farm food; extend_beach restores fish and may claim new beach land) or move_to_district to another district.
5e. start_terraform with target set to plant_grove, clear_field, or extend_beach begins a funded terraform project (same contribute/build flow as structures). Use build_structure when the terraform project is fully funded.

BLUEPRINTS (inventing new structures):
6. Any agent may use propose_blueprint to invent a new structure type. Include a
   "blueprint" object (see schema below) with a required "function" block that
   declares what the building DOES (produces/boosts/unlocks/houses). Identical
   effect sets are rejected. Optionally bundle up to 3 new gatherable resources
   inside "new_resources".
7. Only the elder may approve_blueprint or reject_blueprint. The "target"
   must be the id of a blueprint listed in Pending blueprints.
8. The elder should review Pending blueprints before starting a vanilla project
   when proposals are waiting.
8b. If Invention status is REQUIRED, every seed structure type is already built —
   start_project on a seed type will be refused. Use propose_blueprint (or build/
   contribute to an Approved custom build) instead.
9. Only propose resources that have a gather_zone (one of: farm, forest, village,
   market, beach, cave, ocean) so villagers can collect them, or set gather_zone to
   null for trade-only resources (these cannot be collected).
10. To gather a custom resource, move to its gather_zone and use collect_resource with
   target set to that resource id.
11. Don't repeat a message you or another agent already said recently (see Recent
   village conversations) — vary your wording each time you talk.
11b. If a nearby agent's message mentions a resource you could help with, it may become
   a Commitment on you. If Commitment is set, prioritize honoring it soon via
   collect_resource, contribute_resources, or trade_resource for that resource —
   this fulfills the promise and clears it.

SURVIVAL:
12. You have Hunger and Health. You auto-eat your own food when hungry, so keep food
   on hand. If Hunger reaches 0 your Health drops; at 0 Health you collapse and cannot
   act until revived. Use heal_agent (target a nearby hurt/collapsed villager; any role
   may, healers heal more) to restore their health.

CRAFTING (recipe tree):
13. Some advanced builds need crafted goods. Use craft_item with target set to the item
   id; you must be in the recipe's station zone and hold its inputs.
14. Any agent may propose_recipe to invent a new crafted good (include a "recipe"
   object). Only the elder may approve_recipe / reject_recipe a pending recipe by id.

SAGE PRIORITY (absolute):
15. The elder Sage's survival overrides everything. If Sage has collapsed or is
   critically hurt, the healer and the single nearest villager revive the elder
   (if the healer has also collapsed, revive her first — she is the key to saving
   Sage — then heal Sage). Other agents continue their own work; only those
   responders abandon their task for the elder.

EMERGENT ROLES:
16. Your role is not fixed. If "Incoming messages" or a NOTE says the village
   lacks a gatherer for a needed resource and you have no gathering specialty,
   use switch_role with new_role set to the needed role (e.g. farmer, gatherer,
   miner, fisher) to fill the gap. Don't switch away from a role the village
   still needs.

COLLECTIVE RULES (voting):
17. Any agent may propose_rule to suggest a village-wide rule (include a "rule"
   object). Others use vote_rule with target set to the rule id and "vote" set
   to "yes" or "no". A rule that reaches a majority is enacted and enforced
   mechanically (e.g. a resource tax on contributions funds a shared stockpile).

COGNITIVE CONTROLLER:
18. If "Module reports" are present, you are the Cognitive Controller: weigh the
   Perception/Social/Desire/Reflection reports together and output the single
   best decision. The reports advise you; they never replace the JSON output.

Respond with ONLY valid JSON. No markdown, no explanation, no extra text.
Do not use chain-of-thought or reasoning — output the JSON object immediately.
The JSON must match this structure exactly:
{
  "action": "<one of the available_actions>",
  "target": "<agent name, district id, project type, resource id, blueprint id, or null>",
  "target_district": "<district id for start_project/contribute_resources/build_structure, or null to use your current district>",
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
  "visual_style": "house",               // house | farm_plot | workshop | wall | generic
  "function": {                          // REQUIRED: at least one effect
    "produces": [{"resource":"herbs","amount":2,"every_ticks":600,"scope":"district"}],
    "boosts": [{"kind":"gather","resources":["food"],"every_n":4,"bonus":1,"max_bonus":2,"scope":"district"}],
    "unlocks": [{"kind":"craft","station":"workshop"}],
    "houses": {"every_n": 3}
  }
}

RECIPE object schema (only for propose_recipe):
{
  "id": "rope",                          // ^[a-z][a-z0-9_]{1,24}$, not a duplicate
  "name": "Rope",                        // 1-32 chars
  "inputs": {"herbs": 2},                // 1-6 entries, each amount 1-5
  "station": "workshop"                  // farm|forest|village|market|beach|cave, or null
}

RULE object schema (only for propose_rule):
{
  "id": "resource_tax",                  // ^[a-z][a-z0-9_]{1,24}$, not a duplicate
  "name": "Resource Tax",                // 1-32 chars
  "kind": "resource_tax",                // resource_tax | custom
  "value": 1,                            // tax magnitude (0-3) for resource_tax
  "description": "Contributors add 1 to the shared stockpile."
}
For vote_rule set "target" to the rule id and "vote" to "yes" or "no".

EXAMPLE (farmer, no one nearby):
{"action":"collect_resource","target":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"I should gather food for the village."}

EXAMPLE (builder, project needs wood):
{"action":"contribute_resources","target":"wood","message":null,"new_role":null,"relationship_update":null,"reasoning":"Donating wood to the active build."}

EXAMPLE (trader, Marco nearby):
{"action":"talk_to_nearby","target":"Marco","message":"Could you spare any wood? I'll trade you food for it.","new_role":null,"relationship_update":null,"reasoning":"Coordinating trade for the build."}

EXAMPLE (gatherer proposing a library + paper):
{"action":"propose_blueprint","target":null,"message":null,"new_role":null,"relationship_update":null,"reasoning":"The village needs knowledge storage.","blueprint":{"id":"library","name":"Library","needs":{"wood":4,"paper":2},"new_resources":[{"id":"paper","name":"Paper","gather_zone":"forest","color":"#E8D5B7"}],"visual_style":"house","function":{"produces":[{"resource":"paper","amount":1,"every_ticks":900,"scope":"village"}]}}}

EXAMPLE (elder approving a pending blueprint):
{"action":"approve_blueprint","target":"library","message":"Approved. Gather paper from the forest.","new_role":null,"relationship_update":null,"reasoning":"A worthy addition to the village."}"""

# Reduced-context variant for the context-overflow retry (see
# run_agent_decision): drops the worked EXAMPLE blocks, which are the bulk of
# SYSTEM_PROMPT's size, while keeping the rules and the JSON schema so output
# is still shaped. Sliced by marker rather than a hardcoded example count so
# this stays correct if examples are added/removed.
_SYSTEM_PROMPT_EXAMPLES_IDX = SYSTEM_PROMPT.find("\nEXAMPLE (")
SYSTEM_PROMPT_SLIM = (
    SYSTEM_PROMPT[:_SYSTEM_PROMPT_EXAMPLES_IDX]
    if _SYSTEM_PROMPT_EXAMPLES_IDX != -1 else SYSTEM_PROMPT
)

USER_PROMPT_TEMPLATE = """Your name: {agent_name}
Your role: {role}
Your skill: {role_skill}
Your personality: {personality}
Recent memory: {memory}
Resources: {resources}
Hunger: {hunger}/100  Health: {health}/100
Relationships: {relationships}
Your beliefs: {beliefs}
Agents near you: {nearby_agents}
Current zone: {world_zone}
Current district: {current_district}
Known districts (use as target_district): {known_districts}
Local resource stocks (your current district): {district_stocks}
Terraform projects (start_terraform targets): {known_terraform}
Civilization level: {civilization_level}
Structures built: {structures_built}
Active builds (by district): {active_project}
Build progress (by district): {project_progress}
Civilization directive: {directive}
Invention status: {invention_status}
Commitment: {commitment_text}
Idle agents needing a task: {idle_agents}
Known resources: {known_resources}
Known recipes (craft_item targets): {known_recipes}
Pending blueprints: {pending_blueprints}
Pending recipes: {pending_recipes}
Approved custom builds: {approved_custom_projects}
Rejected blueprints (do NOT re-propose these ids): {rejected_blueprints}
Pending rules (vote with vote_rule): {pending_rules}
Enacted rules: {active_rules}
Recent village conversations: {recent_conversations}
Incoming messages (reply or act on these): {inbox}
Module reports (Cognitive Controller — weigh these): {module_reports}
{behavior_nudge}
Available actions: {available_actions}

What do you do next? Respond with only the JSON."""


# Hard ceiling on the composed "Recent memory:" prompt line. Bug 1's fix
# removes the current worst offenders (leaked scaffold text), but this cap
# guards against any future bloat regardless of cause.
MEMORY_PROMPT_CHAR_BUDGET = 600


def _cap_memory_text(lines, budget=MEMORY_PROMPT_CHAR_BUDGET):
    """Join memory lines (oldest first) under a total character budget,
    dropping the oldest lines first and hard-truncating whatever remains
    (including a "(recalled: ...)" suffix) if it still doesn't fit."""
    if not lines:
        return "none"
    kept = list(lines)
    merged = " | ".join(kept)
    while len(merged) > budget and len(kept) > 1:
        kept.pop(0)
        merged = " | ".join(kept)
    if len(merged) > budget:
        merged = merged[:max(0, budget - 3)].rstrip() + "..."
    return merged or "none"


def compose_memory(data):
    """Merge the client's compacted memory slice with salient memories the
    server retrieves from its vector store for the current situation (Phase 1),
    capped to MEMORY_PROMPT_CHAR_BUDGET characters total.
    """
    client_mem = data.get("memory")
    lines = []
    if isinstance(client_mem, list):
        lines = [str(x) for x in client_mem if x]
    elif client_mem:
        lines = [str(client_mem)]

    agent_name = data.get("agent_name")
    if agent_name and memory_store.size() > 0:
        context = " ".join(str(x) for x in [
            data.get("role"), data.get("world_zone"),
            data.get("active_project"), data.get("directive"),
            format_nearby_agents(data.get("nearby_agents")),
        ] if x)
        try:
            retrieved = memory_store.query(agent=agent_name, text=context, top_k=4)
        except Exception:
            retrieved = []
        seen = set(lines)
        recalled = []
        for e in retrieved:
            txt = e.get("text")
            if txt and txt not in seen:
                seen.add(txt)
                recalled.append(txt)
        if recalled:
            lines.append("(recalled: " + "; ".join(recalled) + ")")

    return _cap_memory_text(lines)


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


def format_known_districts(districts):
    """Format the terse known_districts list (id+kind only, per the
    prompt-token-growth caution) for the target_district hint, e.g.
    'farm_north (farm), village_core (village)'."""
    if not districts or not isinstance(districts, list):
        return "none"
    parts = []
    for d in districts:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        parts.append(f"{d['id']} ({d.get('kind', '?')})")
    return ", ".join(parts) if parts else "none"


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


def format_known_recipes(recipes):
    """Format craftable recipes, e.g. 'tools <- wood 2, stone 1 @workshop'."""
    if not recipes or not isinstance(recipes, list):
        return "none"
    parts = []
    for r in recipes:
        if not isinstance(r, dict):
            continue
        inputs = r.get("inputs") or {}
        ins = ", ".join(f"{k} {v}" for k, v in inputs.items())
        station = r.get("station")
        at = f" @{station}" if station else ""
        parts.append(f"{r.get('id', '?')} <- {ins}{at}")
    return "; ".join(parts) if parts else "none"


def format_pending_recipes(pending):
    """Format pending recipe proposals for the elder."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for r in pending:
        if not isinstance(r, dict):
            continue
        inputs = r.get("inputs") or {}
        ins = ", ".join(f"{k} {v}" for k, v in inputs.items())
        parts.append(f"{r.get('id', '?')} by {r.get('proposed_by', '?')} (inputs {ins})")
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


def format_pending_rules(pending):
    """Format pending rules with their running vote tallies."""
    if not pending or not isinstance(pending, list):
        return "none"
    parts = []
    for r in pending:
        if not isinstance(r, dict):
            continue
        val = r.get("value")
        val_str = f" value {val}" if val not in (None, "") else ""
        parts.append(
            f"{r.get('id', '?')} \"{r.get('name', '?')}\" ({r.get('kind', 'custom')}{val_str}; "
            f"yes {r.get('yes', 0)}, no {r.get('no', 0)})"
        )
    return "; ".join(parts) if parts else "none"


def format_active_rules(active):
    """Format enacted rules for the prompt."""
    if not active or not isinstance(active, list):
        return "none"
    parts = []
    for r in active:
        if not isinstance(r, dict):
            continue
        val = r.get("value")
        val_str = f" {val}" if val not in (None, "") else ""
        parts.append(f"{r.get('name', '?')} ({r.get('kind', 'custom')}{val_str})")
    return "; ".join(parts) if parts else "none"


def format_commitment(commitment):
    """Format a pending commitment (#5.4) for the prompt, or 'none'."""
    if not isinstance(commitment, dict) or not commitment.get("to"):
        return "none"
    return f'You agreed to help {commitment["to"]}: "{commitment.get("text", "")}"'


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


def held_shortfall_resource(agent_data):
    """A project-needed resource this agent is ALREADY holding (e.g. via
    trade), regardless of role/specialty. Catches stalls where a trader or
    off-spec agent sits on the exact resource a build is waiting on."""
    shortfalls = parse_project_shortfalls(agent_data.get("project_progress"))
    if not shortfalls:
        return None
    held = agent_data.get("resources") or {}
    for res, _ in shortfalls:
        if held.get(res, 0) > 0:
            return res
    return None


def canonical_effect_vector(function):
    """Stable JSON key for duplicate-effect detection (ignores structure id/name)."""
    if not isinstance(function, dict):
        return ""

    def _norm_list(items):
        normed = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            normed.append({k: (sorted(v) if k == "resources" and isinstance(v, list) else v)
                           for k, v in sorted(item.items())})
        return sorted(normed, key=lambda x: json.dumps(x, sort_keys=True))

    payload = {}
    if function.get("produces"):
        payload["produces"] = _norm_list(function["produces"])
    if function.get("boosts"):
        payload["boosts"] = _norm_list(function["boosts"])
    if function.get("unlocks"):
        payload["unlocks"] = _norm_list(function["unlocks"])
    if function.get("stores"):
        payload["stores"] = _norm_list(function["stores"])
    if function.get("houses"):
        houses = function["houses"]
        if isinstance(houses, dict):
            payload["houses"] = {k: houses[k] for k in sorted(houses)}
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def validate_function_block(function, available_resource_ids):
    """Validate a blueprint function block. Returns (ok, reason)."""
    if not isinstance(function, dict):
        return False, "function block required (produces/boosts/unlocks/stores/houses)"
    if not any(function.get(k) for k in FUNCTION_EFFECT_KEYS):
        return False, "function must declare at least one effect"

    for prod in function.get("produces") or []:
        if not isinstance(prod, dict):
            return False, "produce entry must be an object"
        res = prod.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown produce resource: {res}"
        amount = prod.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "produce amount must be 1-5"
        every = prod.get("every_ticks", 600)
        if isinstance(every, bool) or not isinstance(every, int) or not (150 <= every <= 7200):
            return False, "produce every_ticks must be 150-7200"
        scope = prod.get("scope", "village")
        if scope not in VALID_PRODUCE_SCOPES:
            return False, "invalid produce scope"

    for boost in function.get("boosts") or []:
        if not isinstance(boost, dict):
            return False, "boost entry must be an object"
        kind = boost.get("kind")
        if kind not in VALID_BOOST_KINDS:
            return False, "invalid boost kind"
        if kind == "gather":
            resources = boost.get("resources")
            if not isinstance(resources, list) or not resources:
                return False, "gather boost needs resources list"
            for res in resources:
                if res not in available_resource_ids:
                    return False, f"unknown boost resource: {res}"
        if kind == "craft" and not boost.get("station"):
            return False, "craft boost needs station"
        every_n = boost.get("every_n", 1)
        if isinstance(every_n, bool) or not isinstance(every_n, int) or not (1 <= every_n <= 10):
            return False, "boost every_n must be 1-10"
        bonus = boost.get("bonus", 1)
        if isinstance(bonus, bool) or not isinstance(bonus, int) or not (1 <= bonus <= 5):
            return False, "boost bonus must be 1-5"
        max_bonus = boost.get("max_bonus", 1)
        if isinstance(max_bonus, bool) or not isinstance(max_bonus, int) or not (1 <= max_bonus <= 10):
            return False, "boost max_bonus must be 1-10"
        scope = boost.get("scope", "village")
        if scope not in VALID_BOOST_SCOPES:
            return False, "invalid boost scope"

    for unlock in function.get("unlocks") or []:
        if not isinstance(unlock, dict):
            return False, "unlock entry must be an object"
        kind = unlock.get("kind")
        if kind not in VALID_UNLOCK_KINDS:
            return False, "invalid unlock kind"
        if kind == "craft" and not unlock.get("station"):
            return False, "craft unlock needs station"

    houses = function.get("houses")
    if houses is not None:
        if not isinstance(houses, dict):
            return False, "houses must be an object"
        every_n = houses.get("every_n", 3)
        if isinstance(every_n, bool) or not isinstance(every_n, int) or not (1 <= every_n <= 10):
            return False, "houses every_n must be 1-10"

    for store in function.get("stores") or []:
        if not isinstance(store, dict):
            return False, "store entry must be an object"
        res = store.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown store resource: {res}"
        cap = store.get("capacity")
        if isinstance(cap, bool) or not isinstance(cap, int) or not (5 <= cap <= 100):
            return False, "store capacity must be 5-100"

    return True, None


def validate_blueprint(blueprint, known_resource_ids, pending_ids, approved_ids,
                       custom_resource_count, rejected_ids=None, known_effect_vectors=None):
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

    available = set(known_resource_ids) | new_ids | BASE_RESOURCE_IDS
    fn = blueprint.get("function")
    ok_fn, fn_reason = validate_function_block(fn, available)
    if not ok_fn:
        return False, fn_reason

    vec = canonical_effect_vector(fn)
    known = set(known_effect_vectors or [])
    if vec and vec in known:
        return False, "duplicate effect vector (another structure already has identical effects)"

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

    invention_required = str(agent_data.get("invention_status") or "").startswith("REQUIRED")
    if not has_project:
        if invention_required:
            # Mirrors sim_engine._invention_required's gate on _start_project_for:
            # every seed structure is already built, so a role-default seed
            # project would just be refused. Gather instead of stalling; the
            # elder's own _maybe_invention_backstop is what actually pushes
            # someone toward propose_blueprint.
            return {"action": "collect_resource", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "The village needs a new invention before building again; "
                                 "gathering resources for now."}
        return {"action": "start_project", "target": role_default_project(role), "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Starting a role-appropriate build project."}

    held = held_shortfall_resource(agent_data)
    if held:
        # Catches any role (esp. trader/guard/scout, whose fallbacks below
        # never contribute) sitting on a resource the build is waiting on
        # instead of wandering past it forever.
        return {"action": "contribute_resources", "target": held, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Contributing a held resource the project needs."}

    if role in ("farmer", "fisher", "gatherer"):
        zone = agent_data.get("world_zone", "")
        if role == "farmer" and zone != "farm":
            return {"action": "move_to_district", "target": "farm", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to a farm to gather food."}
        if role == "gatherer" and zone != "forest":
            return {"action": "move_to_district", "target": "forest", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to the forest to gather wood."}
        if role == "fisher" and zone != "beach":
            return {"action": "move_to_district", "target": "beach", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to the beach to fish."}
        needed = first_shortfall_resource(agent_data)
        return {"action": "collect_resource", "target": needed, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Gathering resources for the village."}

    if role == "miner":
        zone = agent_data.get("world_zone", "")
        if zone != "cave":
            return {"action": "move_to_district", "target": "cave", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to a cave to mine."}
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
        return {"action": "move_to_district", "target": "market", "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Heading to market to trade."}

    if role in ("guard", "scout", "explorer"):
        return {"action": "move_to_district", "target": "village", "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Patrolling the village."}

    if role in ("healer", "elder", "blacksmith"):
        if has_project:
            needed = first_shortfall_resource(agent_data)
            return {"action": "contribute_resources", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Supporting the village build."}
        return {"action": "move_to_district", "target": "village", "message": None,
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

    if action == "start_terraform":
        target = decision.get("target")
        if target not in TERRAFORM_PROJECT_IDS:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid terraform target)").strip()
            return fallback
        return decision

    if action == "propose_blueprint":
        known_ids = agent_data.get("known_resource_ids") or []
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        approved_ids = agent_data.get("approved_blueprint_ids") or []
        rejected_ids = agent_data.get("rejected_blueprint_ids") or []
        custom_count = agent_data.get("custom_resource_count", 0)
        ok, reason = validate_blueprint(
            decision.get("blueprint"), known_ids, pending_ids, approved_ids, custom_count,
            rejected_ids, agent_data.get("known_effect_vectors"),
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid blueprint: {reason})").strip()
            # Surfaced to the agent's next prompt by the engine so the model
            # learns why its proposal vanished instead of repeating it.
            fallback["rejection_note"] = reason
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

    if action == "switch_role":
        new_role = decision.get("new_role") or decision.get("target")
        if new_role in ROLES:
            decision["new_role"] = new_role
            decision.pop("blueprint", None)
            return decision
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid role switch)").strip()
        return fallback

    if action == "move_to_district" and not decision.get("target"):
        # Models reliably put the district id in target_district (the schema
        # describes that field as "district id"); the engine reads only
        # target, so without this promotion the agent never moves.
        if decision.get("target_district"):
            decision["target"] = decision["target_district"]

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


@app.route("/log/benchmark", methods=["POST"])
def log_benchmark():
    """Persist a browser-origin benchmark metric (Phase 0/8 metrics stream)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        metric = body.get("metric")
        if metric:
            session_logger.log_benchmark(
                metric,
                body.get("value"),
                body.get("frame_tick"),
                body.get("detail"),
            )
    except Exception:
        pass
    return ("", 204)


@app.route("/memory/store", methods=["POST"])
def memory_store_endpoint():
    """Embed + persist one or more memories (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        items = body.get("entries")
        if not isinstance(items, list):
            items = [body]
        stored = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            entry = memory_store.store(
                item.get("agent"),
                item.get("text"),
                salience=item.get("salience", 0.5),
                kind=item.get("kind", "event"),
                frame_tick=item.get("frame_tick"),
                tier=item.get("tier"),
            )
            if entry:
                stored += 1
        return jsonify({"ok": True, "stored": stored, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/query", methods=["POST"])
def memory_query_endpoint():
    """Top-k retrieval over the memory store (Phase 1)."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        results = memory_store.query(
            agent=body.get("agent"),
            text=body.get("text", ""),
            top_k=body.get("top_k", 5),
            tier=body.get("tier"),
            kinds=body.get("kinds"),
        )
        return jsonify({"results": [
            {
                "text": e["text"],
                "tier": e["tier"],
                "kind": e["kind"],
                "salience": e["salience"],
                "frame_tick": e["frame_tick"],
            }
            for e in results
        ]})
    except Exception:
        return jsonify({"results": []}), 200


@app.route("/memory/summarize", methods=["POST"])
def memory_summarize_endpoint():
    """Summarizer loop (Phase 1, CMA E): compress an agent's recent memories
    into one durable first-person sentence stored back into long-term memory."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        agent = body.get("agent")
        frame_tick = body.get("frame_tick")
        recents = memory_store.recent(agent=agent, limit=12)
        recents = [e for e in recents if e["kind"] != "summary"]
        if len(recents) < 4:
            return jsonify({"ok": False, "reason": "not enough memories"})
        joined = "; ".join(e["text"] for e in recents)
        summary = lm_complete(
            "You compress an agent's recent memories into ONE concise "
            "first-person sentence capturing what matters for their future "
            "decisions. Output only the sentence, no preamble.",
            f"Agent {agent}'s recent memories: {joined}\nSummary:",
            max_tokens=80, temperature=0.4,
        )
        if not summary:
            return jsonify({"ok": False, "reason": "no summary"})
        summary = summary.strip().strip('"').strip()[:200]
        if not summary:
            return jsonify({"ok": False, "reason": "empty summary"})
        memory_store.store(agent, summary, salience=0.9, kind="summary",
                           frame_tick=frame_tick, tier="longTerm")
        session_logger.log_benchmark(
            "memory_summary", memory_store.size(), frame_tick,
            {"agent": agent, "summary": summary},
        )
        return jsonify({"ok": True, "summary": summary, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


# PIANO modules (Phase 3): each is a small, single-sentence cognitive sub-call.
# The Cognitive Controller (the /agent/think decision call) consumes their
# combined output as a bottleneck and emits the one validated decision.
MODULE_PROMPTS = {
    "perception": "You are the Perception module of a village agent. In ONE "
                  "sentence, state the key facts of the current situation and "
                  "any immediate threat or opportunity. Output only the sentence.",
    "social": "You are the Social module of a village agent. In ONE sentence, "
              "suggest who to coordinate with and what to say or request, based "
              "on nearby agents, relationships, and incoming messages. Output "
              "only the sentence.",
    "desire": "You are the Desire/Goal module of a village agent. In ONE "
              "sentence, name the single most useful goal right now given the "
              "village's needs and this agent's role and resources. Output only "
              "the sentence.",
    "reflection": "You are the Reflection module of a village agent. In ONE "
                  "sentence, note one lesson or pattern from the agent's "
                  "memories worth applying now. Output only the sentence.",
}


@app.route("/agent/module", methods=["POST"])
def agent_module_endpoint():
    """Run one PIANO cognitive module (Phase 3). Returns a one-sentence report."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        module = body.get("module")
        sysp = MODULE_PROMPTS.get(module)
        if not sysp:
            return jsonify({"text": None}), 200
        text = lm_complete(
            sysp,
            f"Agent {body.get('agent')} context: {body.get('context')}",
            max_tokens=60, temperature=0.5,
        )
        if text:
            text = text.strip().strip('"').strip()[:200]
        session_logger.log_benchmark(
            "module_run", 1, body.get("frame_tick"),
            {"agent": body.get("agent"), "module": module},
        )
        return jsonify({"text": text})
    except Exception:
        return jsonify({"text": None}), 200


@app.route("/meta/update", methods=["POST"])
def meta_update_endpoint():
    """Meta system (Phase 4, CMA F): build an autobiographical memory and a
    persona directive for an agent from its self-report + memories."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        agent = body.get("agent")
        report = body.get("report") or {}
        frame_tick = body.get("frame_tick")
        mems = memory_store.recent(agent=agent, limit=14)
        joined = "; ".join(e["text"] for e in mems)

        autobiography = None
        if joined:
            autobiography = lm_complete(
                "Write a 1-2 sentence first-person life story for this village "
                "agent from their memories, capturing their identity and what "
                "they care about. Output only the story.",
                f"Agent {agent} ({report.get('role')}). Memories: {joined}. "
                f"Top actions: {report.get('top_actions')}. "
                f"Beliefs: {report.get('beliefs')}.",
                max_tokens=100, temperature=0.6,
            )
            if autobiography:
                autobiography = autobiography.strip().strip('"').strip()[:300]
                if autobiography:
                    memory_store.store(
                        agent, autobiography, salience=0.95,
                        kind="autobiography", frame_tick=frame_tick,
                        tier="longTerm",
                    )

        persona = lm_complete(
            "From this agent's self-report, write ONE short imperative directive "
            "(max 18 words) to guide their future behavior, reflecting who they "
            "have become. Output only the directive.",
            f"Agent {agent}. Role: {report.get('role')}. "
            f"Top actions: {report.get('top_actions')}. "
            f"Resources: {report.get('resources')}. "
            f"Beliefs: {report.get('beliefs')}. "
            f"Life story: {autobiography or 'n/a'}.",
            max_tokens=40, temperature=0.6,
        )
        if persona:
            persona = persona.strip().strip('"').strip()[:160]

        session_logger.log_benchmark(
            "meta_update", 1, frame_tick,
            {"agent": agent, "persona": persona, "autobiography": autobiography},
        )
        return jsonify({"ok": True, "autobiography": autobiography, "persona": persona})
    except Exception:
        return jsonify({"ok": False}), 200


@app.route("/memory/clean", methods=["POST"])
def memory_clean_endpoint():
    """Memory Cleaner loop (Phase 1, CMA E): dedupe + trim the store."""
    try:
        body = request.get_json(force=True, silent=True) or {}
        removed = memory_store.clean()
        session_logger.log_benchmark(
            "memory_clean", memory_store.size(), body.get("frame_tick"),
            {"removed": removed},
        )
        return jsonify({"ok": True, "removed": removed, "size": memory_store.size()})
    except Exception:
        return jsonify({"ok": False}), 200


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
    agent_data["known_effect_vectors"] = list(data.get("known_effect_vectors") or [])
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


def lm_complete(system_prompt, user_prompt, max_tokens=200, temperature=0.5):
    """Plain-text LM Studio completion for the background cognition loops
    (Summarizer, meta system, PIANO modules / Cognitive Controller). Returns the
    text or None on any failure so every caller can degrade gracefully.

    Unlike lm_message_text() (used on the JSON-decision path, where any
    surrounding text is harmless because extract_json_decision() picks the
    JSON out of it), this inspects the raw message dict itself: when the
    model left `content` empty and routed everything into `reasoning_content`,
    that field holds a chain-of-thought scaffold and only the trailing answer
    should be kept -- and rejected outright if it still looks scaffolded."""
    payload = {
        # Background cognition is routine work -- always the fast model.
        "model": MODEL_FAST if _model_routing_enabled else "local-model",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "thinking": {"type": "disabled", "budget_tokens": 0},
    }
    try:
        resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
        body = resp.json()
        message = body["choices"][0]["message"]
    except (requests.exceptions.RequestException, ValueError, KeyError,
            IndexError, TypeError):
        return None
    if not isinstance(message, dict):
        return None
    content = (message.get("content") or "").strip()
    if content:
        text = content
    else:
        text = extract_plain_answer((message.get("reasoning_content") or "").strip())
    if not text or is_scaffold_text(text):
        return None
    return text


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


INVENTION_USER_PROMPT = """You are {agent_name}, the village {role}.

THIS TURN YOU HAVE EXACTLY ONE JOB: invent a new structure for the village by responding with a propose_blueprint action. Ignore every other duty this turn (including task assignment if you are the elder). Do NOT pick any other action.

What problem does this structure solve? Your blueprint must include a "function" block (produces/boosts/unlocks/houses) describing its mechanical effect — not just a name.

Structure ids already taken (your blueprint id must NOT be any of these): {taken_ids}
Blueprint ids previously rejected (do NOT reuse): {rejected_ids}
Resources you may reference in "needs" and "function": {resource_ids}
You may also introduce up to 3 brand-new resources via "new_resources", each with a gather_zone of farm, forest, village, market, beach, cave, or ocean (or null for crafted-only goods).
{feedback}
Respond with ONLY the JSON decision object: action "propose_blueprint" plus a complete "blueprint" (id, name, needs, required function block, optional new_resources, visual_style). Invent something with a NEW effect, not a renamed duplicate."""


def build_invention_prompt(data):
    """Slim, single-purpose user prompt for a dedicated invention turn (set by
    the engine's _maybe_invention_backstop). Strips every competing nudge and
    state section so the model's whole budget goes into authoring a valid,
    novel blueprint."""
    taken = sorted(set(list(data.get("structure_counts") or {})
                       + [str(a) for a in data.get("approved_custom_projects") or []]
                       + [b.get("id") for b in data.get("pending_blueprints") or []
                          if isinstance(b, dict) and b.get("id")]))
    rejected = [str(r) for r in data.get("rejected_blueprints") or []]
    resources = [r.get("id") for r in data.get("known_resources") or []
                 if isinstance(r, dict) and r.get("id")]
    feedback = data.get("behavior_nudge") or ""
    return INVENTION_USER_PROMPT.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        taken_ids=", ".join(taken) or "none",
        rejected_ids=", ".join(rejected) or "none",
        resource_ids=", ".join(resources) or "none",
        feedback=feedback,
    )


def build_user_prompt(data, slim=False):
    """Fill in USER_PROMPT_TEMPLATE from the agent/civilization state. When
    slim=True (the context-overflow retry, see run_agent_decision), drop the
    memory line and recent conversations -- the two most compressible,
    highest-variance-size fields -- to shrink the prompt. invention_only
    turns get the dedicated proposal-only prompt instead."""
    if data.get("invention_only"):
        return build_invention_prompt(data)
    nearby_formatted = format_nearby_agents(data.get("nearby_agents"))
    known_resources = data.get("known_resources") or []
    pending_blueprints = data.get("pending_blueprints") or []
    approved_custom_projects = data.get("approved_custom_projects") or []
    rejected_blueprints = data.get("rejected_blueprints") or []
    idle_agents = data.get("idle_agents") or []
    behavior_nudge = data.get("behavior_nudge") or ""

    return USER_PROMPT_TEMPLATE.format(
        agent_name=data.get("agent_name"),
        role=data.get("role"),
        role_skill=data.get("role_skill", ""),
        personality=data.get("personality"),
        memory="none" if slim else compose_memory(data),
        hunger=data.get("hunger", 100),
        health=data.get("health", 100),
        resources=data.get("resources"),
        relationships=data.get("relationships"),
        beliefs=data.get("beliefs") or "none",
        nearby_agents=nearby_formatted,
        world_zone=data.get("world_zone"),
        current_district=data.get("current_district", "none"),
        known_districts=format_known_districts(data.get("known_districts") or []),
        district_stocks=data.get("district_stocks") or "none",
        known_terraform=", ".join(data.get("known_terraform") or []) or "none",
        civilization_level=data.get("civilization_level", 1),
        structures_built=data.get("structures_built", 0),
        active_project=data.get("active_project", "none"),
        project_progress=data.get("project_progress", "none"),
        directive=data.get("directive", "none"),
        invention_status=data.get("invention_status", "not needed"),
        commitment_text=format_commitment(data.get("commitment")),
        idle_agents=format_idle_agents(idle_agents),
        known_resources=format_known_resources(known_resources),
        known_recipes=format_known_recipes(data.get("known_recipes") or []),
        pending_blueprints=format_pending_blueprints(pending_blueprints),
        pending_recipes=format_pending_recipes(data.get("pending_recipes") or []),
        approved_custom_projects=format_approved_custom(approved_custom_projects),
        rejected_blueprints=format_rejected_blueprints(rejected_blueprints),
        pending_rules=format_pending_rules(data.get("pending_rules") or []),
        active_rules=format_active_rules(data.get("active_rules") or []),
        recent_conversations="none" if slim else data.get("recent_conversations", "none"),
        inbox=data.get("inbox", "none"),
        module_reports=data.get("module_reports", "none"),
        behavior_nudge=behavior_nudge,
        available_actions=data.get("available_actions"),
    )


def build_decision_payload(data, self_prompt, response_format, slim=False):
    """Assemble the LM Studio chat-completion payload for a decision call.
    slim=True builds the reduced-context retry payload (see
    run_agent_decision): SYSTEM_PROMPT_SLIM instead of SYSTEM_PROMPT (drops
    the worked EXAMPLE blocks) plus the slim user prompt. The rules and JSON
    schema are kept either way so response_format still shapes the output."""
    system_content = SYSTEM_PROMPT_SLIM if slim else SYSTEM_PROMPT
    if self_prompt:
        system_content += f"\n\nYOUR PERSONA (act in character): {self_prompt}"
    payload = {
        "model": model_for_decision(data) if _model_routing_enabled else "local-model",
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": build_user_prompt(data, slim=slim)},
        ],
        "max_tokens": 512,
        "temperature": 0.4,
        "stream": False,
        "thinking": {"type": "disabled", "budget_tokens": 0},
    }
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def is_context_overflow_error(err_text):
    """True for LM Studio's per-slot context-window error, e.g.
    {"error": "Context size has been exceeded."}."""
    return "context size has been exceeded" in (err_text or "").lower()


def run_agent_decision(data):
    """Build the prompt, call LM Studio, and return a validated decision dict.

    Shared by the HTTP /agent/think endpoint and the server-authoritative
    SimEngine's think worker. Returns a plain dict (already normalized) — on any
    failure it returns either an {"error": ...} dict (engine maps these to its
    offline/compute/rest paths) or a role fallback decision."""
    try:
        self_prompt = (data.get("self_prompt") or "").strip()
        response_format = build_response_format()
        payload = build_decision_payload(data, self_prompt, response_format)

        known_resources = data.get("known_resources") or []
        pending_blueprints = data.get("pending_blueprints") or []
        approved_custom_projects = data.get("approved_custom_projects") or []
        rejected_blueprints = data.get("rejected_blueprints") or []
        nearby_formatted = format_nearby_agents(data.get("nearby_agents"))

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

        def bad_response_fallback(latency_ms, response=None, http_status=None, error="bad_response"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            log_lm(latency_ms, response=response, http_status=http_status,
                   decision=fallback, error=error)
            return fallback

        global _structured_output_enabled, _model_routing_enabled

        start = datetime.now()
        try:
            resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
        except requests.exceptions.RequestException:
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            log_lm(latency_ms, error="LM Studio offline")
            return {"error": "LM Studio offline", "action": "rest"}

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
            response_format = None
            start = datetime.now()
            try:
                resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
            except requests.exceptions.RequestException:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_lm(latency_ms, error="LM Studio offline")
                return {"error": "LM Studio offline", "action": "rest"}
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

        # Auto-degrade: if the routed model id isn't loaded in LM Studio,
        # disable per-role routing for the session and retry once with the
        # generic "local-model" id, so a single-model setup keeps working.
        if (_model_routing_enabled
                and looks_like_model_not_found_error(http_status, lm_body)):
            print(f"[server] LM Studio doesn't know model {payload.get('model')!r}; "
                  f"disabling per-role model routing for this session and retrying "
                  f"with 'local-model'.")
            _model_routing_enabled = False
            payload["model"] = "local-model"
            start = datetime.now()
            try:
                resp = requests.post(LM_STUDIO_URL, json=payload, timeout=30)
            except requests.exceptions.RequestException:
                latency_ms = int((datetime.now() - start).total_seconds() * 1000)
                log_lm(latency_ms, error="LM Studio offline")
                return {"error": "LM Studio offline", "action": "rest"}
            latency_ms = int((datetime.now() - start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

        if lm_body is None:
            return bad_response_fallback(latency_ms, http_status=http_status)

        # error_kind tags the whole call for logging once at the end (below),
        # even if the context-overflow retry ultimately recovers a decision --
        # this is what makes context_overflow measurable/distinguishable in
        # lm_studio.jsonl per the plan, without double-logging each attempt.
        error_kind = None
        if isinstance(lm_body, dict) and lm_body.get("error"):
            err = str(lm_body.get("error"))
            if "compute error" in err.lower():
                log_lm(latency_ms, response=lm_body, http_status=http_status, error="compute_error")
                return {"error": "compute_error", "action": "rest"}
            if not is_context_overflow_error(err):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status)

            # Retry ONCE with a slimmed-down payload: no memory line, no
            # recent conversations, no worked examples. Rules + JSON schema
            # are kept so response_format still shapes the output. On any
            # further failure this falls through to the normal handling below
            # (which will hit bad_response_fallback), so there is no loop.
            error_kind = "context_overflow"
            slim_payload = build_decision_payload(data, self_prompt, response_format, slim=True)
            payload = slim_payload
            retry_start = datetime.now()
            try:
                resp = requests.post(LM_STUDIO_URL, json=slim_payload, timeout=30)
            except requests.exceptions.RequestException:
                latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
                log_lm(latency_ms, error=error_kind)
                return {"error": "LM Studio offline", "action": "rest"}
            latency_ms += int((datetime.now() - retry_start).total_seconds() * 1000)
            http_status = resp.status_code
            try:
                lm_body = resp.json()
            except ValueError:
                lm_body = None

            if lm_body is None:
                return bad_response_fallback(latency_ms, http_status=http_status, error=error_kind)
            if isinstance(lm_body, dict) and lm_body.get("error"):
                return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status, error=error_kind)

        try:
            message = lm_body["choices"][0]["message"]
        except (TypeError, KeyError, IndexError):
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                          error=error_kind or "bad_response")

        raw_text = lm_message_text(message)
        decision = extract_json_decision(raw_text)
        if not decision and isinstance(message, dict):
            decision = extract_json_decision(message.get("content") or "")
        if not decision:
            return bad_response_fallback(latency_ms, response=lm_body, http_status=http_status,
                                          error=error_kind or "bad_response")

        decision = normalize_decision(decision, agent_data)

        log_lm(latency_ms, response=lm_body, http_status=http_status, decision=decision, error=error_kind)
        return decision

    except Exception:
        return {"error": "server_error", "action": "rest"}


@app.route("/agent/think", methods=["POST"])
def agent_think():
    """Legacy HTTP think endpoint. Now unused by the server-authoritative
    engine (which calls run_agent_decision directly), but kept functional."""
    data = request.get_json(force=True) or {}
    return jsonify(run_agent_decision(data))


# --- Server-authoritative SimEngine wiring (Phases 2-6) ---
# AVAILABLE_ACTIONS: the full action superset the engine advertises to the model
# (mirrors AVAILABLE_ACTIONS in index.html). normalize_decision still filters.
# Reuses the module-level lm_complete() and the MemoryStore `memory_store`
# instance already defined above.
AVAILABLE_ACTIONS = list(DECISION_ACTIONS)

# Import the engine module whether server.py is run as a script (cwd-relative)
# or imported as simulation.server (package-relative).
import sys as _sys  # noqa: E402
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim_engine as _sim_engine  # noqa: E402


def _llm_decide(payload):
    """Engine -> LM bridge: run the existing decision pipeline + log it."""
    return run_agent_decision(payload)


_ENGINE_DEPS = {
    "ROLES": ROLES,
    "ROLE_PROJECT": ROLE_PROJECT,
    "ROLE_SKILLS": {role: d.get("skill", "helps the village") for role, d in ROLES.items()},
    "ROLE_PRIMARY_RESOURCE": ROLE_PRIMARY_RESOURCE,
    "RESOURCE_GATHER_ROLES": RESOURCE_GATHER_ROLES,
    "AVAILABLE_ACTIONS": AVAILABLE_ACTIONS,
    "SLUG_RE": SLUG_RE,
    "llm_decide": _llm_decide,
    "lm_complete": lm_complete,
    "is_scaffold_text": is_scaffold_text,
    "memory_store": memory_store,
    "log_activity": session_logger.log_activity,
    "log_conversation": session_logger.log_conversation,
    "log_benchmark": session_logger.log_benchmark,
    "validate_blueprint": validate_blueprint,
    "canonical_effect_vector": canonical_effect_vector,
}

_roster_env = os.environ.get("SIM_AGENTS")
try:
    _roster_size = int(_roster_env) if _roster_env else 8
except ValueError:
    _roster_size = 8

engine = _sim_engine.SimEngine(_ENGINE_DEPS, roster_size=_roster_size)

# Full-state resume (Contract 3): if a valid state.json exists, rehydrate the
# world (frameTick, civilization, agents, re-embedded memory) instead of using
# the cold-start roster the constructor just built. Otherwise keep cold start.
if engine.restore_state():
    print(f"[server] resumed from state.json @ frameTick={engine.frameTick} "
          f"(level {engine.civilization['level']}, "
          f"{len(engine.civilization['structures'])} structures, "
          f"memory {memory_store.size()})")
else:
    print("[server] cold start (no valid state.json)")


@app.route("/state")
def state():
    """Consistent world snapshot for the thin viewer (Contract 2)."""
    return jsonify(engine.snapshot())


@app.route("/districts.js")
def districts_js():
    """Live districts/roads for the viewer (world-expansion plan). Unlike the
    static /roles.js precedent, this reads the engine's LIVE civilization
    state under its lock -- like /state does -- so a district founded mid-session
    shows up to a connected viewer on its next poll, no reload needed. Despite
    the ".js" name (matching the plan's route naming), the body is plain JSON;
    the viewer fetch()-polls it rather than re-injecting a <script> tag, which
    would otherwise throw on re-declaring `const` globals every poll."""
    with engine.lock:
        c = engine.civilization
        districts = [
            {"id": did, "kind": d["kind"], "tile": d["tile"], "label": d.get("label"),
             "bounds": dict(d["bounds"]),
             "buildGrid": dict(d["build_grid"]) if d.get("build_grid") else None}
            for did, d in c["districts"].items()
        ]
        road_nodes = {nid: dict(n) for nid, n in c["roadNodes"].items()}
        road_edges = [list(e) for e in c["roadEdges"]]
    return jsonify({"districts": districts, "roadNodes": road_nodes, "roadEdges": road_edges})


@app.route("/control/pause", methods=["POST"])
def control_pause():
    engine.pause()
    return jsonify({"ok": True, "paused": True})


@app.route("/control/resume", methods=["POST"])
def control_resume():
    engine.resume()
    return jsonify({"ok": True, "paused": False})


@app.route("/control/reset", methods=["POST"])
def control_reset():
    body = request.get_json(force=True, silent=True) or {}
    agents = body.get("agents")
    try:
        agents = int(agents) if agents else None
    except (TypeError, ValueError):
        agents = None
    engine.reset(roster_size=agents)
    return jsonify({"ok": True, "agents": engine.roster_size})


if __name__ == "__main__":
    # Bind 0.0.0.0 so any device on the LAN can reach the sim (req #3); find this
    # machine's LAN IP with `ipconfig` and open the URL from another device as
    # http://<host-ip>:5001. On Windows, allow inbound TCP 5001 through the
    # firewall (or accept the first-run prompt). threaded=True lets the request
    # handlers run concurrently alongside the (forthcoming) SimEngine thread.
    # NOTE: this exposes the server — including the LM Studio proxy — to the whole
    # local network. Intended for a trusted home LAN, not a hostile network.
    HOST = os.environ.get("SIM_HOST", "0.0.0.0")
    PORT = int(os.environ.get("SIM_PORT", "5001"))
    # Start the server-authoritative engine thread BEFORE the HTTP server so the
    # world ticks headless regardless of any connected viewer.
    engine.start()
    print(f"[server] SimEngine started ({engine.roster_size} agents, "
          f"{_sim_engine.TICKS_PER_SEC} ticks/s)")

    # Graceful shutdown: flush the full state to disk on exit so a restart
    # resumes exactly. atexit covers normal exit; the signal handlers cover
    # Ctrl-C / `kill` (which otherwise bypass atexit during app.run()).
    _saved_once = threading.Event()

    def _flush_on_exit():
        if _saved_once.is_set():
            return
        _saved_once.set()
        engine.stop()
        engine.save_state()

    atexit.register(_flush_on_exit)

    def _signal_shutdown(signum, frame):
        _flush_on_exit()
        os._exit(0)

    for _sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(_sig, _signal_shutdown)
        except (ValueError, OSError):
            pass  # not in main thread / unsupported platform

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
