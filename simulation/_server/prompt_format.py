"""Prompt-context formatting helpers, split out of server.py (Phase 5
modularization, pure move, no behavior change).

`memory_store` is deliberately NOT imported from _server.memory_store here:
the live MemoryStore singleton is constructed in server.py (it needs
session_logger.dir, a server.py bootstrap value, for its mirror_path), and
server.py in turn imports compose_memory from this module -- importing the
singleton back from server.py would be a circular import. Instead server.py
injects it into this module's namespace right after constructing it (see
server.py's `_prompt_format.memory_store = memory_store`); compose_memory
looks the name up in this module's own globals on every call, so the
injection only has to happen once, after which behavior is unchanged from
the original single-file version.
"""

import re

from _server.roles_data import ROLES, ROLE_PROJECT
from _server.validation_constants import SEED_PROJECT_IDS

# Set by server.py immediately after constructing the MemoryStore singleton
# -- see module docstring above.
memory_store = None

# Hard ceiling on the composed "Recent memory:" prompt line. Bug 1's fix
# removes the current worst offenders (leaked scaffold text), but this cap
# guards against any future bloat regardless of cause.
# Raised 600 -> 900 (plan Phase 4 / WIKI_MEMORY): when the flag is on,
# _memory_for_prompt prepends up to three "wiki <section>: ..." lines
# (each hard-capped at WIKI_SECTION_CHAR_CAP=300 chars in sim_engine.py),
# alongside -- never instead of -- the existing longTerm/shortTerm/working
# lines. At 600 the old budget, _cap_memory_text's oldest-first eviction
# would drop the (now-prepended) wiki lines before touching anything the
# flag-off path already showed, silently defeating the point of adding
# them. 900 gives the wiki lines real headroom without letting one flag
# blow the prompt-cost budget open-endedly (kept <= 900 per plan). This is
# a shared cap: it applies in both flag states, but only changes observed
# behavior when WIKI_MEMORY is True (flag-off callers never populate wiki
# lines, so the wider ceiling is simply unused headroom for them).
MEMORY_PROMPT_CHAR_BUDGET = 900


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
    if agent_name and memory_store is not None and memory_store.size() > 0:
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
                stigma_suffix = ""
                stigmata = item.get("stigmata")
                if isinstance(stigmata, list) and stigmata:
                    stigma_suffix = f", signs: {', '.join(str(t) for t in stigmata)}"
                parts.append(
                    f"{name} ({role}, food:{food} wood:{wood} gold:{gold}{stigma_suffix})")
                peer_hint = item.get("peer_model")
                if peer_hint:
                    parts[-1] += f" [think: {peer_hint}]"
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


def format_reserved_structure_ids(approved, pending):
    """Every structure id a new propose_blueprint id must avoid: the seed
    templates (SEED_PROJECT_IDS -- includes tier-2+ ones like forge/granary/
    market/library, which is exactly what agents keep re-proposing since
    "Approved custom builds" below only lists CUSTOM ids) plus every
    already-approved custom and currently-pending blueprint id. Mirrors the
    invention-only prompt's `taken` set (server.build_invention_prompt) so
    ordinary turns get the same collision guidance the council already had."""
    ids = set(SEED_PROJECT_IDS)
    if isinstance(approved, list):
        ids.update(str(a) for a in approved if a)
    if isinstance(pending, list):
        ids.update(b.get("id") for b in pending if isinstance(b, dict) and b.get("id"))
    return ", ".join(sorted(ids)) if ids else "none"


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
        if isinstance(r, str):
            # C3: the engine appends a plain "(+N older rules)" marker string
            # when active_rules is truncated -- render it as-is.
            parts.append(r)
            continue
        if not isinstance(r, dict):
            continue
        val = r.get("value")
        val_str = f" {val}" if val not in (None, "") else ""
        parts.append(f"{r.get('name', '?')} ({r.get('kind', 'custom')}{val_str})")
    return "; ".join(parts) if parts else "none"


def format_constitution(constitution):
    """Render the bounded constitutional ledger without exposing raw JSON."""
    if not isinstance(constitution, list) or not constitution:
        return "none"
    parts = []
    for provision in constitution[-12:]:
        if not isinstance(provision, dict):
            continue
        status = provision.get("status") or "active"
        text = f"{provision.get('id', '?')} / {provision.get('name', '?')} [{status}]"
        if provision.get("supersedes"):
            text += f" supersedes {provision['supersedes']}"
        parts.append(text)
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


def role_default_project(role, role_project_map=None):
    """Return a role's preferred project from a live payload map when given.

    The module global is deliberately only a seed fallback: each engine world
    owns its emergent role registry independently.
    """
    projects = role_project_map if isinstance(role_project_map, dict) else ROLE_PROJECT
    pref = projects.get((role or "").lower(), "house")
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


def pick_idle_agent_for_project(idle_agents, project_progress, resource_gather_roles_map=None):
    """Prefer idle agents whose role gathers the resource the project still needs."""
    gather_roles = (resource_gather_roles_map if isinstance(resource_gather_roles_map, dict)
                    else RESOURCE_GATHER_ROLES)
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        preferred_roles = gather_roles.get(needed_res, ())
        for role in preferred_roles:
            for agent in idle_agents:
                if (agent.get("role") or "").lower() == role:
                    return agent
    return idle_agents[0] if idle_agents else None


def task_for_role(role, active_project=None, project_progress=None,
                  role_primary_resource_map=None, role_project_map=None):
    role = (role or "").lower()
    primary_resources = (role_primary_resource_map
                         if isinstance(role_primary_resource_map, dict)
                         else ROLE_PRIMARY_RESOURCE)
    shortfalls = parse_project_shortfalls(project_progress)
    if shortfalls:
        needed_res = shortfalls[0][0]
        if primary_resources.get(role) == needed_res:
            return f"gather {needed_res} for the active project"
        return f"gather or contribute {needed_res} to the active project"
    if active_project and active_project not in ("none", "null", None, ""):
        return f"gather or contribute resources to {active_project}"
    project = role_default_project(role, role_project_map).replace("_", " ")
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


def build_agent_data(data, nearby_formatted, known_resources, pending_blueprints,
                     approved_custom_projects, rejected_blueprints):
    """Assemble agent context used by normalize_decision and role_fallback_action."""
    agent_data = dict(data)
    agent_data["nearby_agents"] = nearby_formatted
    # C3: prefer the engine's always-full "known_resource_ids" (cheap id-only
    # list) so the duplicate-resource-id/needs-reference checks in
    # validate_blueprint never see a trimmed set, even though `known_resources`
    # (the rich dict list used for the prompt) is now capped. Falls back to
    # deriving from `known_resources` for callers that don't send the new field.
    agent_data["known_resource_ids"] = list(data.get("known_resource_ids") or [
        r.get("id") for r in known_resources if isinstance(r, dict) and r.get("id")
    ])
    agent_data["custom_resource_count"] = sum(
        1 for r in known_resources if isinstance(r, dict) and r.get("custom")
    )
    agent_data["pending_blueprint_ids"] = [
        b.get("id") for b in pending_blueprints if isinstance(b, dict) and b.get("id")
    ]
    # sage_review status per pending id, so role_fallback_action/normalize can
    # tell a not-yet-reviewed blueprint apart from one ready for a verdict.
    agent_data["pending_blueprint_reviews"] = {
        b["id"]: b.get("sage_review", "pending")
        for b in pending_blueprints if isinstance(b, dict) and b.get("id")
    }
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
    agent_data["upgradeable_structures"] = list(data.get("upgradeable_structures") or [])
    agent_data["sprite_design_only"] = bool(data.get("sprite_design_only"))
    agent_data["sprite_design_context"] = data.get("sprite_design_context")
    return agent_data
