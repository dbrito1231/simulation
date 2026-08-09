"""Decision/blueprint/role/sprite validation and role-fallback ladder, split
out of server.py (Phase 5 modularization, pure move, no behavior change).
"""

import json
import re

from _server.prompt_format import (
    ROLE_PRIMARY_RESOURCE,
    first_shortfall_resource,
    held_shortfall_resource,
    parse_nearby_names,
    pick_idle_agent_for_project,
    role_default_project,
    task_for_role,
)
from _server.roles_data import ROLES
from _server.validation_constants import (
    BASE_RESOURCE_IDS,
    FUNCTION_EFFECT_KEYS,
    GATHER_ZONES,
    KIND_TO_TERRAFORM,
    MAX_APPROVED_CUSTOM,
    MAX_EMERGENT_ROLES,
    MAX_PENDING_BLUEPRINTS,
    MAX_PENDING_ROLES,
    MAX_TECH_TIER,
    RESOURCE_TO_TERRAFORM,
    SEED_PROJECT_IDS,
    SLUG_RE,
    SPRITE_GRID_MAX,
    SPRITE_GRID_MIN,
    TERRAFORM_KIND,
    TERRAFORM_PROJECT_IDS,
    VALID_BOOST_KINDS,
    VALID_BOOST_SCOPES,
    VALID_PRODUCE_SCOPES,
    VALID_UNLOCK_KINDS,
    VISUAL_STYLES,
)


def _district_kind_map(agent_data):
    out = {}
    for d in agent_data.get("known_districts") or []:
        if isinstance(d, dict) and d.get("id"):
            out[d["id"]] = d.get("kind")
    return out


def _fuzzy_terraform_id(raw):
    """Map display names and slugs to canonical terraform template ids."""
    if not raw:
        return None
    s = str(raw).strip().lower()
    if s in TERRAFORM_PROJECT_IDS:
        return s
    slug = s.replace(" ", "_").replace("-", "_")
    if slug in TERRAFORM_PROJECT_IDS:
        return slug
    compact = s.replace(" ", "").replace("_", "").replace("-", "")
    for tid in TERRAFORM_PROJECT_IDS:
        if compact == tid.replace("_", ""):
            return tid
    aliases = {
        "plant grove": "plant_grove", "plantgrove": "plant_grove",
        "clear field": "clear_field", "clearfield": "clear_field",
        "extend beach": "extend_beach", "extendbeach": "extend_beach",
    }
    return aliases.get(s) or aliases.get(compact)


def _infer_terraform_decision(decision, agent_data):
    """Promote district/resource targets to template ids (models name places)."""
    district_map = _district_kind_map(agent_data)
    target = decision.get("target")
    target_district = decision.get("target_district")

    if target in TERRAFORM_PROJECT_IDS:
        if target_district and target_district not in district_map:
            decision["target_district"] = None
        return decision, None

    if target and target in district_map:
        tmpl = KIND_TO_TERRAFORM.get(district_map[target])
        if tmpl:
            decision["target"] = tmpl
            decision["target_district"] = target
            return decision, None

    if target_district and target_district in district_map:
        tmpl = KIND_TO_TERRAFORM.get(district_map[target_district])
        if tmpl:
            decision["target"] = tmpl
            return decision, None

    fuzzy = _fuzzy_terraform_id(target)
    if fuzzy:
        decision["target"] = fuzzy
        return decision, None

    known_resources = agent_data.get("known_resource_ids") or []
    if target and target in known_resources:
        tmpl = RESOURCE_TO_TERRAFORM.get(target)
        if tmpl:
            decision["target"] = tmpl
            want_kind = TERRAFORM_KIND[tmpl]
            if target_district and district_map.get(target_district) == want_kind:
                decision["target_district"] = target_district
            else:
                current = agent_data.get("current_district")
                if current and district_map.get(current) == want_kind:
                    decision["target_district"] = current
                else:
                    match = next((did for did, k in district_map.items() if k == want_kind), None)
                    if match:
                        decision["target_district"] = match
            return decision, None

    return None, f"could not infer terraform template from target {target!r}"


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
    if function.get("shelter"):
        shelter = function["shelter"]
        if isinstance(shelter, dict):
            payload["shelter"] = {k: shelter[k] for k in sorted(shelter)}
    if function.get("light"):
        light = function["light"]
        if isinstance(light, dict):
            payload["light"] = {k: light[k] for k in sorted(light)}
    if function.get("upkeep"):
        upkeep = function["upkeep"]
        if isinstance(upkeep, dict):
            payload["upkeep"] = {k: upkeep[k] for k in sorted(upkeep)}
    if not payload:
        return ""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
SPRITE_CELL_RE = re.compile(r"^[.a-e]+$")


def sprite_spec_is_degenerate(sprite):
    """Reject flat single-color blobs (common LLM failure on upgrade turns)."""
    if not isinstance(sprite, dict):
        return True
    grid = sprite.get("grid")
    palette = sprite.get("palette") or []
    if not isinstance(grid, list):
        return True
    counts = {}
    total = 0
    colors_used = set()
    for row in grid:
        for ch in str(row):
            if ch == ".":
                continue
            counts[ch] = counts.get(ch, 0) + 1
            total += 1
            idx = ord(ch) - ord("a")
            if 0 <= idx < len(palette):
                colors_used.add(palette[idx].lower())
    if total < 4:
        return True
    if len(colors_used) < 2:
        return True
    if max(counts.values()) / total > 0.82:
        return True
    return False


def validate_sprite_block(sprite, min_rows=0, min_cols=0):
    """Validate an optional LLM-authored pixel sprite. Returns (ok, reason).
    Kept deliberately permissive on artistry, strict on shape: the viewer
    renders whatever passes, and a missing sprite falls back to a
    deterministic procedural one (never a blocker for invention).
    When min_rows/min_cols are set (sprite upgrade turns), the grid must be
    strictly larger in BOTH dimensions than the procedural fallback."""
    if not isinstance(sprite, dict):
        return False, "sprite must be an object with palette and grid"
    palette = sprite.get("palette")
    if not isinstance(palette, list) or not (2 <= len(palette) <= 5):
        return False, "sprite palette must be 2-5 hex colors"
    for color in palette:
        if not isinstance(color, str) or not HEX_COLOR_RE.match(color):
            return False, f"invalid sprite color: {color!r} (use #RRGGBB)"
    grid = sprite.get("grid")
    if not isinstance(grid, list) or not (SPRITE_GRID_MIN <= len(grid) <= SPRITE_GRID_MAX):
        return False, f"sprite grid must be {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} rows"
    max_col = 0
    for row in grid:
        if not isinstance(row, str) or not (SPRITE_GRID_MIN <= len(row) <= SPRITE_GRID_MAX):
            return False, f"each sprite row must be a string of {SPRITE_GRID_MIN}-{SPRITE_GRID_MAX} cells"
        if not SPRITE_CELL_RE.match(row):
            return False, "sprite rows may only contain . (empty) and letters a-e"
        max_col = max(max_col, len(row))
        for ch in row:
            if ch != "." and (ord(ch) - ord("a")) >= len(palette):
                return False, f"sprite cell '{ch}' has no palette entry"
    if min_rows and len(grid) <= min_rows:
        return False, (f"sprite must be taller than the current tier "
                       f"(need >{min_rows} rows, got {len(grid)})")
    if min_cols and max_col <= min_cols:
        return False, (f"sprite must be wider than the current tier "
                       f"(need >{min_cols} columns, got {max_col})")
    if sprite_spec_is_degenerate(sprite):
        return False, "sprite is too flat (use varied colors/pattern, not one solid fill)"
    return True, None


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
        if kind == "transit":
            if unlock.get("terrain") != "ocean":
                return False, "transit terrain must be ocean"
            consumes = unlock.get("consumes")
            if not isinstance(consumes, dict) or not consumes:
                return False, "transit consumes required"
            for res, amount in consumes.items():
                if res not in available_resource_ids:
                    return False, f"unknown transit resource: {res}"
                if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
                    return False, "transit consumption must be positive integers"

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

    shelter = function.get("shelter")
    if shelter is not None:
        if not isinstance(shelter, dict):
            return False, "shelter must be an object"
        cap = shelter.get("capacity")
        if isinstance(cap, bool) or not isinstance(cap, int) or not (1 <= cap <= 4):
            return False, "shelter capacity must be 1-4"

    light = function.get("light")
    if light is not None:
        if not isinstance(light, dict):
            return False, "light must be an object"
        scope = light.get("scope", "district")
        if scope != "district":
            return False, "light scope must be district"

    upkeep = function.get("upkeep")
    if upkeep is not None:
        if not isinstance(upkeep, dict):
            return False, "upkeep must be an object"
        res = upkeep.get("resource")
        if not isinstance(res, str) or res not in available_resource_ids:
            return False, f"unknown upkeep resource: {res}"
        amount = upkeep.get("amount")
        if isinstance(amount, bool) or not isinstance(amount, int) or not (1 <= amount <= 5):
            return False, "upkeep amount must be 1-5"

    return True, None


def validate_contract(contract, known_resource_ids=None):
    """Validate a contract offer object. Returns (ok: bool, reason: str|None)."""
    known_resource_ids = known_resource_ids or []
    if not isinstance(contract, dict):
        return False, "contract must be an object"
    want = contract.get("want")
    if not isinstance(want, str) or not want.strip():
        return False, "contract want must be a resource id"
    if want not in known_resource_ids:
        return False, f"unknown resource: {want}"
    qty = contract.get("qty")
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        return False, "contract qty must be a positive integer"
    pay_coin = contract.get("pay_coin")
    if isinstance(pay_coin, bool) or not isinstance(pay_coin, int) or pay_coin < 1:
        return False, "contract pay_coin must be a positive integer"
    deadline_frames = contract.get("deadline_frames")
    if (isinstance(deadline_frames, bool) or not isinstance(deadline_frames, int)
            or deadline_frames < 1):
        return False, "contract deadline_frames must be a positive integer"
    return True, None


def validate_blueprint(blueprint, known_resource_ids, pending_ids, approved_ids,
                       custom_resource_count, rejected_ids=None, known_effect_vectors=None,
                       village_tier=None):
    """Validate a proposed blueprint. Returns (ok: bool, reason: str|None).

    village_tier (Phase D, TECH_TREE_ENABLED): when not None, the blueprint's
    optional "tier" (default 1) must not exceed it, and any unlock effect's
    tier must be at most blueprint tier + 1 (the deterministic-escape rule: the
    station for tier N must itself be buildable at tier N-1). None = no tier
    checks at all (flag off)."""
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

    # Invented resources are intentionally unlimited. Keep the count argument
    # for compatibility with older callers, but do not reject valid resources
    # based on the former MAX_CUSTOM_RESOURCES policy.

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

    # Optional LLM-authored pixel sprite. Missing is fine (the viewer draws a
    # deterministic procedural sprite instead); a PRESENT-but-malformed sprite
    # is rejected with a reason so the model can fix it next attempt.
    sprite = blueprint.get("sprite")
    if sprite is not None:
        ok_sprite, sprite_reason = validate_sprite_block(sprite)
        if not ok_sprite:
            return False, sprite_reason

    available = set(known_resource_ids) | new_ids | BASE_RESOURCE_IDS
    fn = blueprint.get("function")
    ok_fn, fn_reason = validate_function_block(fn, available)
    if not ok_fn:
        return False, fn_reason

    if village_tier is not None:
        tier = blueprint.get("tier", 1)
        if tier is None:
            tier = 1
        if isinstance(tier, bool) or not isinstance(tier, int) \
                or not (1 <= tier <= MAX_TECH_TIER):
            return False, f"tier must be an integer 1-{MAX_TECH_TIER}"
        for unlock in (fn.get("unlocks") or []) if isinstance(fn, dict) else []:
            ut = unlock.get("tier")
            if ut is None:
                continue
            if isinstance(ut, bool) or not isinstance(ut, int) \
                    or not (1 <= ut <= MAX_TECH_TIER):
                return False, f"unlock tier must be an integer 1-{MAX_TECH_TIER}"
            if ut > tier + 1:
                return False, (f"a station unlocking tier {ut} must itself be tier "
                               f"{ut - 1} or lower, so the chain stays buildable")
        if tier > village_tier:
            hint = ("the Forge unlocks tier 2 and is a normal tier-1 build"
                    if tier == 2 else
                    f"invent a structure whose function unlocks tier {tier} crafting")
            return False, (f"tier {tier} tech requires a tier-{tier} station "
                           f"built first ({hint})")

    # Duplicate-effect proposals are no longer hard-rejected here: the engine
    # (sim_engine._effect_vector_owner_map, via propose_blueprint) tags a
    # matching proposal with duplicateOf and keeps it pending so the elder can
    # route it to an upgrade instead of silently losing the idea.

    return True, None


def validate_role(role, known_resource_ids, known_role_ids, pending_role_slugs,
                  known_project_ids, pending_role_count=None,
                  emergent_role_count=None):
    """Validate the wire shape of an emergent role proposal.

    The engine repeats this validation against its locked live registry before
    storing a proposal; this prompt-side version keeps invalid structured LLM
    output from consuming a decision turn.
    """
    if not isinstance(role, dict):
        return False, "role must be an object"
    # Counts come from the engine's locked snapshot. Defaults preserve direct
    # helper callers while still deriving a conservative answer from ids.
    if isinstance(pending_role_count, bool) or not isinstance(pending_role_count, int):
        pending_role_count = len(set(pending_role_slugs))
    if isinstance(emergent_role_count, bool) or not isinstance(emergent_role_count, int):
        emergent_role_count = len(set(known_role_ids) - set(ROLES))
    if pending_role_count >= MAX_PENDING_ROLES:
        return False, "too many pending roles"
    if emergent_role_count >= MAX_EMERGENT_ROLES:
        return False, "too many emergent roles"
    if set(role) - {"slug", "name", "specialty", "preferredProject", "skill"}:
        return False, "role has unknown fields"
    slug = role.get("slug")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        return False, "invalid role slug"
    if slug in set(known_role_ids) or slug in set(pending_role_slugs):
        return False, "role slug already exists or is pending"
    name = role.get("name")
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
        return False, "invalid role name"
    skill = role.get("skill")
    if not isinstance(skill, str) or not (1 <= len(skill.strip()) <= 160) or "\n" in skill:
        return False, "skill must be one line of 1-160 characters"
    specialty = role.get("specialty")
    if not isinstance(specialty, list) or len(specialty) > 4 \
            or any(not isinstance(resource, str) or resource not in set(known_resource_ids)
                   for resource in specialty):
        return False, "specialty must list up to 4 known resources"
    preferred = role.get("preferredProject")
    preferences = preferred if isinstance(preferred, list) else [preferred]
    if not preferences or len(preferences) > 4 \
            or any(not isinstance(project, str) or project not in set(known_project_ids)
                   for project in preferences):
        return False, "preferredProject must name 1-4 known project types"
    return True, None


def role_fallback_action(role, agent_data):
    """Return a role-appropriate fallback decision when talk is invalid.

    Thin wrapper around _role_fallback_action(): that function has many return
    points (one per role/phase branch), so rather than trust every branch to
    remember to stamp _fallback individually, this single call site stamps it
    once on whatever comes back -- provably covering every path. normalize_decision
    also returns un-noted fallbacks (e.g. its non-dict guard) that
    sprite_rejection_note/council_rejection_note sniffing would miss entirely;
    _fallback is the one signal that always fires. apply_decision() reads named
    fields only, so the extra key is inert there; it stays in the logged
    decision because llm.jsonl benefits from it."""
    decision = _role_fallback_action(role, agent_data)
    if isinstance(decision, dict):
        decision["_fallback"] = True
    return decision


def _role_fallback_candidate_checks(role, agent_data):
    """Ordered list of zero-arg checks producing role_fallback_action's
    ladder, one per branch, in the ladder's original priority order.

    Both `_role_fallback_action` (first match wins) and
    `role_fallback_candidates` (Phase 6 Fix 5: accumulate up to `limit`
    matches) iterate this same list, so the branch conditions -- and their
    priority order -- live in exactly one place instead of two copies that
    could silently drift. Each check is a closure over the state computed
    once up front (role, council, active_project, ...), mirroring how the
    original single-function ladder read those locals; nothing here mutates
    agent_data, so evaluating every check (as role_fallback_candidates does)
    is safe and side-effect free. The final check (`default_branch`) always
    returns a candidate, so `_role_fallback_action`'s "first match" loop is
    guaranteed to find one -- same guarantee the original unconditional
    trailing `return` gave."""
    role = (role or "").lower()
    council = agent_data.get("daily_council") or {}
    council_turn = bool(agent_data.get("council_turn") and agent_data.get("council_seated"))

    def council_branch():
        if not council_turn:
            return None
        phase = council.get("phase")
        agenda = council.get("agenda") or []
        topic = next((a.get("topic") for a in agenda if isinstance(a, dict)), "world_status")
        if phase == "discussion":
            ballot = council.get("ballot") or {}
            succession = ballot.get("kind") == "succession"
            candidates = ", ".join(ballot.get("candidates") or [])
            return {
                "action": "council_speak",
                "message": (
                    f"We should compare {candidates} by judgment, care, and service."
                    if succession else
                    "We should protect essentials while making steady progress."
                ),
                "feeling": "hopeful",
                "topic": "leadership_vacancy" if succession else topic,
                "reasoning": "Offering a practical council opinion.",
            }
        if phase == "proposal":
            return {
                "action": "council_propose", "kind": "idea",
                "title": "Steady village priorities",
                "detail": "Protect food and health while completing the most-stalled shared project.",
                "reasoning": "Offering a safe advisory proposal.",
            }
        if phase == "voting":
            return {
                "action": "council_vote", "vote": "abstain",
                "reasoning": "Recording a neutral ballot rather than inventing a position.",
            }
        if phase == "verdict" and role == "elder":
            verdict = council.get("verdict") or {}
            return {
                "action": "council_speak",
                "message": f"The council ratifies: {verdict.get('outcome') or 'the recorded result'}.",
                "feeling": "resolute", "topic": "verdict",
                "reasoning": "Announcing the council's recorded ruling.",
            }
        return None

    active_project = agent_data.get("active_project")
    has_project = active_project and active_project not in ("none", "null", None, "")
    role_projects = agent_data.get("role_project_map")
    primary_resources = agent_data.get("role_primary_resource_map")
    gather_roles = agent_data.get("resource_gather_roles_map")

    # Sid-parity Phase 1: when the village needs a gather role this agent can
    # fill, prefer switch_role over a generic wander/collect fallback.
    needed_role = agent_data.get("needed_role")

    def needed_role_branch():
        if (needed_role and needed_role != role
                and role not in ("elder", "builder", "healer")
                and not (primary_resources if isinstance(primary_resources, dict)
                         else ROLE_PRIMARY_RESOURCE).get(role)):
            return {"action": "switch_role", "target": None, "message": None,
                    "new_role": needed_role, "relationship_update": None,
                    "reasoning": f"The village needs a {needed_role}; retraining to fill the gap."}
        return None

    pending_ids = agent_data.get("pending_blueprint_ids") or []

    def pending_blueprint_ready_branch():
        if role == "elder" and pending_ids:
            reviews = agent_data.get("pending_blueprint_reviews") or {}
            ready = next((bid for bid in pending_ids if reviews.get(bid) in ("approved", "skipped")), None)
            if ready:
                return {"action": "approve_blueprint", "target": ready, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Reviewing a pending blueprint proposal."}
        return None

    def pending_blueprint_review_branch():
        if role == "elder" and pending_ids:
            reviews = agent_data.get("pending_blueprint_reviews") or {}
            needs_review = next((bid for bid in pending_ids if reviews.get(bid, "pending") == "pending"), None)
            if needs_review:
                return {"action": "sage_review_blueprint", "target": needs_review, "message": None,
                        "sage_decision": "approve", "new_role": None, "relationship_update": None,
                    "reasoning": "Checking district geography/resources before approving."}
        return None

    pending_roles = agent_data.get("pending_roles") or []

    def pending_role_branch():
        if role == "elder" and pending_roles:
            target = next((p.get("slug") for p in pending_roles if isinstance(p, dict) and p.get("slug")), None)
            if target:
                return {"action": "approve_role", "target": target, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Reviewing a pending role proposal."}
        return None

    idle_agents = agent_data.get("idle_agents") or []

    def assign_task_branch():
        if role == "elder" and idle_agents:
            project_progress = agent_data.get("project_progress")
            target = pick_idle_agent_for_project(idle_agents, project_progress, gather_roles)
            target_name = target.get("name") if target else None
            if target_name:
                return {"action": "assign_task", "target": target_name,
                        "message": task_for_role(
                            target.get("role"), active_project, project_progress,
                            primary_resources, role_projects,
                        ),
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Assigning work to an idle villager."}
        return None

    invention_required = str(agent_data.get("invention_status") or "").startswith("REQUIRED")
    upgradeable = agent_data.get("upgradeable_structures") or []

    def upgrade_branch():
        if upgradeable and not has_project:
            target_u = upgradeable[0]
            return {"action": "upgrade_structure", "target": str(target_u.get("id")), "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": f"Upgrading {target_u.get('name')} before building duplicates."}
        return None

    def no_project_branch():
        if not has_project:
            if invention_required:
                # Mirrors sim_engine._invention_required's gate on
                # _start_project_for: every seed structure is already built,
                # so a role-default seed project would just be refused.
                # Gather instead of stalling; the elder's own
                # _maybe_invention_backstop is what actually pushes someone
                # toward propose_blueprint.
                return {"action": "collect_resource", "target": None, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "The village needs a new invention before building again; "
                                     "gathering resources for now."}
            return {"action": "start_project", "target": role_default_project(role, role_projects),
                    "message": None, "new_role": None, "relationship_update": None,
                    "reasoning": "Starting a role-appropriate build project."}
        return None

    def held_shortfall_branch():
        held = held_shortfall_resource(agent_data)
        if held:
            # Catches any role (esp. trader/guard/scout, whose branches below
            # never contribute) sitting on a resource the build is waiting on
            # instead of wandering past it forever.
            return {"action": "contribute_resources", "target": held, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Contributing a held resource the project needs."}
        return None

    def hunter_prey_branch():
        if role == "hunter" and agent_data.get("prey_in_range"):
            return {"action": "hunt_wildlife", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Hunting nearby wildlife for meat or fish."}
        return None

    def gatherer_family_branch():
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
        return None

    def miner_branch():
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
        return None

    def hunter_role_branch():
        if role == "hunter":
            zone = agent_data.get("world_zone", "")
            if zone not in ("forest", "farm", "beach"):
                return {"action": "move_to_district", "target": "forest", "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Heading to hunting grounds for wildlife."}
            return {"action": "collect_resource", "target": None, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "No prey in range; gathering while scouting for wildlife."}
        return None

    def builder_branch():
        if role == "builder":
            needed = first_shortfall_resource(agent_data) or "wood"
            return {"action": "contribute_resources", "target": needed, "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Contributing to the active project."}
        return None

    def trader_branch():
        if role == "trader":
            return {"action": "move_to_district", "target": "market", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Heading to market to trade."}
        return None

    def patrol_branch():
        if role in ("guard", "scout", "explorer"):
            return {"action": "move_to_district", "target": "village", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Patrolling the village."}
        return None

    def support_branch():
        if role in ("healer", "elder", "blacksmith"):
            if has_project:
                needed = first_shortfall_resource(agent_data)
                return {"action": "contribute_resources", "target": needed, "message": None,
                        "new_role": None, "relationship_update": None,
                        "reasoning": "Supporting the village build."}
            return {"action": "move_to_district", "target": "village", "message": None,
                    "new_role": None, "relationship_update": None,
                    "reasoning": "Returning to the village center."}
        return None

    def default_branch():
        return {"action": "collect_resource", "target": None, "message": None,
                "new_role": None, "relationship_update": None,
                "reasoning": "Working toward civilization goals."}

    return [
        council_branch,
        needed_role_branch,
        pending_blueprint_ready_branch,
        pending_blueprint_review_branch,
        pending_role_branch,
        assign_task_branch,
        upgrade_branch,
        no_project_branch,
        held_shortfall_branch,
        hunter_prey_branch,
        gatherer_family_branch,
        miner_branch,
        hunter_role_branch,
        builder_branch,
        trader_branch,
        patrol_branch,
        support_branch,
        default_branch,
    ]


def _role_fallback_action(role, agent_data):
    """Return a role-appropriate fallback decision when talk is invalid.

    First-match walk over _role_fallback_candidate_checks -- see that
    function's docstring for why the ladder now lives there instead of
    inline here."""
    for check in _role_fallback_candidate_checks(role, agent_data):
        candidate = check()
        if candidate is not None:
            return candidate
    # Unreachable: default_branch (the checks list's final entry) always
    # returns a candidate, matching the original ladder's unconditional
    # trailing return.
    return {"action": "collect_resource", "target": None, "message": None,
            "new_role": None, "relationship_update": None,
            "reasoning": "Working toward civilization goals."}


def role_fallback_candidates(role, agent_data, limit=3):
    """Phase 6 (Fix 5): collect up to `limit` role-appropriate fallback
    candidates, in the same priority order _role_fallback_action's ladder
    would try them (both walk _role_fallback_candidate_checks, so the
    conditions can't drift between the two). Used only by
    run_agent_decision's terminal-fallback AI-choice tiebreak
    (FALLBACK_AI_CHOICE_ENABLED) -- every existing role_fallback_action call
    site is unaffected by this function's existence.

    During a seated council turn (``council_turn`` and ``council_seated``
    both set), the candidate set is intentionally capped to the first
    ladder match (always ``council_branch``) so Fix 5 cannot replace a
    council beat with village work (``assign_task``, etc.).

    Many branches are mutually exclusive by construction: a role only
    matches one of the per-role branches near the end of the ladder, and
    "no active project" vs. "has an active project" branches can't both
    fire. So in a lot of states this returns a single candidate -- that is
    expected, not a bug, and is NOT a reason to loosen any branch's
    condition; a candidate that the ladder itself would never produce for
    this agent_data must never appear here.

    Returned dicts are the same shape _role_fallback_action returns
    (unstamped -- callers that want the _fallback marker still go through
    role_fallback_action's wrapper semantics themselves)."""
    candidates = []
    seen = set()
    council_only = bool(agent_data.get("council_turn") and agent_data.get("council_seated"))
    for check in _role_fallback_candidate_checks(role, agent_data):
        candidate = check()
        if candidate is None:
            continue
        # A later branch (e.g. the unconditional trailing default_branch)
        # can legitimately produce the exact same (action, target) an
        # earlier, higher-priority branch already returned in this same
        # state (e.g. two different branches both landing on a plain
        # collect_resource). That is not a *different* safe option, just
        # the same one reached twice, so skip the repeat instead of
        # offering the AI two letters with an identical outcome -- this
        # trims noise, it does not add or loosen any branch condition.
        key = (candidate.get("action"), candidate.get("target"))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
        if council_only or len(candidates) >= limit:
            break
    return candidates


def synthesize_divine_response(decision, agent_data):
    """When binding Voice guidance is active, ensure divine_response is present.

    Missing/invalid values synthesize continue + missing_divine_response without
    rejecting the validated action."""
    if not isinstance(decision, dict) or not agent_data.get("voice_guidance_active"):
        return decision
    raw = decision.get("divine_response")
    valid = (
        isinstance(raw, dict)
        and raw.get("stance") in ("follow", "continue")
        and isinstance(raw.get("reason"), str)
        and raw.get("reason", "").strip()
    )
    if valid:
        decision["divine_response"] = {
            "stance": raw["stance"],
            "reason": raw["reason"].strip()[:240],
        }
        return decision
    decision["divine_response"] = {
        "stance": "continue",
        "reason": "missing_divine_response",
    }
    decision["divine_response_synthetic"] = True
    return decision


def normalize_decision(decision, agent_data):
    """Reject invalid talk_to_nearby and substitute role fallback."""
    if not isinstance(decision, dict):
        return role_fallback_action(agent_data.get("role"), agent_data)

    action = decision.get("action", "rest")
    nearby_raw = agent_data.get("nearby_agents")
    nearby_names = parse_nearby_names(nearby_raw)
    nearby_empty = len(nearby_names) == 0
    council_actions = {"council_speak", "council_propose", "council_vote"}
    council = agent_data.get("daily_council") or {}
    council_turn = bool(agent_data.get("council_turn") and agent_data.get("council_seated"))
    if action in council_actions or council_turn:
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        phase = council.get("phase")
        # Genuine "agent ignored its designated council turn" — not a timing artifact,
        # since council_turn/council_seated were snapshotted for this agent's own turn.
        if council_turn and action not in council_actions:
            fallback["reasoning"] = (
                fallback.get("reasoning", "") + " (invalid council session/action)"
            ).strip()
            fallback["council_rejection_note"] = "not a seated active council turn"
            return fallback
        # Coarse, slow-changing check: is there a council session at all? A model that
        # spuriously emits a council action outside any session should not sail through
        # to apply_decision only to be rejected live and waste the whole turn.
        if action in council_actions and (not council or not phase):
            fallback["council_rejection_note"] = "no active council session"
            return fallback
        # Coarse, slow-changing check: is this agent even seated at the council at
        # all? Unlike phase/speaking-order (which are racy -- see below), seat
        # membership is fixed for the whole session, so a non-attendee agent
        # emitting a council action would otherwise sail through to
        # apply_decision only to be rejected live ("actor is not an attendee" /
        # "actor is not seated"), wasting the whole turn.
        if action in council_actions and not council_turn and not agent_data.get("council_seated"):
            fallback["council_rejection_note"] = "not seated at the council"
            return fallback
        # Per-turn/per-phase eligibility (council_turn, phase gates) is deliberately NOT
        # re-checked here: council_turn/phase were snapshotted before the LLM call and can
        # go stale while the model thinks. apply_decision()'s _daily_council_actor() is the
        # live authority and re-checks phase/turn eligibility, rejecting non-fatally if stale.
        if action == "council_speak":
            message = decision.get("message")
            feeling = decision.get("feeling")
            topic = decision.get("topic")
            if not isinstance(message, str) or not message.strip():
                fallback["council_rejection_note"] = "council_speak requires a message"
                return fallback
            decision["message"] = message.strip()[:500]
            decision["feeling"] = str(feeling or "thoughtful").strip()[:80]
            decision["topic"] = str(topic or "world_status").strip()[:80]
            return decision
        if action == "council_vote":
            ballot = council.get("ballot") or {}
            if ballot.get("kind") == "succession":
                candidate = decision.get("candidate")
                valid_vote = (
                    decision.get("vote") == "abstain"
                    or candidate in (ballot.get("candidates") or [])
                )
            else:
                valid_vote = decision.get("vote") in ("yes", "no", "abstain")
            if not valid_vote:
                fallback["council_rejection_note"] = "council_vote requires a valid vote"
                return fallback
            return decision
        kind = decision.get("kind")
        if kind == "idea":
            title, detail = decision.get("title"), decision.get("detail")
            if not isinstance(title, str) or not title.strip() or not isinstance(detail, str) \
                    or not detail.strip():
                fallback["council_rejection_note"] = "idea proposals require title and detail"
                return fallback
            decision["title"] = title.strip()[:120]
            decision["detail"] = detail.strip()[:500]
            return decision
        if kind == "rule":
            rule = decision.get("rule")
            if not isinstance(rule, dict):
                fallback["council_rejection_note"] = "rule proposal has an invalid shape"
                return fallback
            return decision
        if kind == "blueprint":
            known_ids = agent_data.get("known_resource_ids") or []
            ok, reason = validate_blueprint(
                decision.get("blueprint"), known_ids,
                agent_data.get("pending_blueprint_ids") or [],
                agent_data.get("approved_blueprint_ids") or [],
                agent_data.get("custom_resource_count", 0),
                agent_data.get("rejected_blueprint_ids") or [],
                agent_data.get("known_effect_vectors"),
                village_tier=agent_data.get("village_tech_tier"),
            )
            if not ok:
                fallback["council_rejection_note"] = f"invalid council blueprint: {reason}"
                return fallback
            return decision
        fallback["council_rejection_note"] = "council proposal kind must be rule, blueprint, or idea"
        return fallback

    if action == "start_terraform":
        inferred, reason = _infer_terraform_decision(decision, agent_data)
        if inferred:
            return inferred
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid terraform: {reason})").strip()
        fallback["terraform_rejection_note"] = reason
        return fallback

    if action == "upgrade_structure":
        upgradeable = agent_data.get("upgradeable_structures") or []
        if not upgradeable:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (no upgradeable structure)").strip()
            return fallback
        target = decision.get("target")
        if target:
            t = str(target).strip().lower()
            ids = {str(u.get("id")) for u in upgradeable}
            types = {(u.get("type") or "").lower() for u in upgradeable}
            names = {(u.get("name") or "").lower() for u in upgradeable}
            if t not in ids and t not in types and t not in names:
                fallback = role_fallback_action(agent_data.get("role"), agent_data)
                fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid upgrade target)").strip()
                fallback["upgrade_rejection_note"] = "target is not an upgradeable structure"
                return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "submit_structure_sprite":
        if not agent_data.get("sprite_design_only"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (not a sprite design turn)").strip()
            return fallback
        ctx = agent_data.get("sprite_design_context") or {}
        sprite = decision.get("sprite")
        ok, reason = validate_sprite_block(
            sprite,
            min_rows=int(ctx.get("minRows") or 0),
            min_cols=int(ctx.get("minCols") or 0),
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid sprite: {reason})").strip()
            fallback["sprite_rejection_note"] = reason
            return fallback
        decision.pop("blueprint", None)
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
            village_tier=agent_data.get("village_tech_tier"),
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid blueprint: {reason})").strip()
            # Surfaced to the agent's next prompt by the engine so the model
            # learns why its proposal vanished instead of repeating it.
            fallback["rejection_note"] = reason
            return fallback
        return decision

    if action == "sage_review_blueprint":
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_ids = agent_data.get("pending_blueprint_ids") or []
        sage_decision = decision.get("sage_decision")
        if role != "elder" or not target or target not in pending_ids \
                or sage_decision not in ("approve", "deny"):
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid sage review)").strip()
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
        known_roles = agent_data.get("known_role_ids") or ROLES
        if new_role in known_roles:
            decision["new_role"] = new_role
            decision.pop("blueprint", None)
            return decision
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid role switch)").strip()
        return fallback

    if action == "propose_role":
        role = decision.get("role")
        pending_roles = agent_data.get("pending_roles") or []
        pending_slugs = {p.get("slug") for p in pending_roles if isinstance(p, dict)}
        ok, reason = validate_role(
            role, agent_data.get("known_resource_ids") or [],
            agent_data.get("known_role_ids") or [], pending_slugs,
            agent_data.get("known_project_ids") or [],
            pending_role_count=agent_data.get("pending_role_count"),
            emergent_role_count=agent_data.get("emergent_role_count"),
        )
        if ok:
            decision.pop("blueprint", None)
            return decision
        fallback = role_fallback_action(agent_data.get("role"), agent_data)
        fallback["reasoning"] = (fallback.get("reasoning", "") + f" (invalid role: {reason})").strip()
        return fallback

    if action in ("approve_role", "reject_role"):
        role = (agent_data.get("role") or "").lower()
        target = decision.get("target")
        pending_slugs = {p.get("slug") for p in agent_data.get("pending_roles") or []
                         if isinstance(p, dict)}
        if role != "elder" or not target or target not in pending_slugs:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid role review)").strip()
            return fallback
        return decision

    if action == "propose_rule":
        # Keep the effect/supersession fields explicit through normalization;
        # SimEngine owns registry-aware grammar validation under its lock.
        rule = decision.get("rule")
        effect = rule.get("effect") if isinstance(rule, dict) else None
        supersedes = rule.get("supersedes") if isinstance(rule, dict) else None
        malformed = (not isinstance(rule, dict)
                     or (effect is not None and not isinstance(effect, dict))
                     or (supersedes is not None and not isinstance(supersedes, str)))
        if malformed:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (fallback.get("reasoning", "") + " (invalid rule shape)").strip()
            return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "offer_contract":
        target = decision.get("target")
        if not isinstance(target, str) or not target.strip():
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (
                fallback.get("reasoning", "") + " (invalid contract offer)"
            ).strip()
            fallback["contract_rejection_note"] = (
                "offer_contract requires target (agent name or open)"
            )
            return fallback
        ok, reason = validate_contract(
            decision.get("contract"),
            agent_data.get("known_resource_ids") or [],
        )
        if not ok:
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (
                fallback.get("reasoning", "") + f" (invalid contract: {reason})"
            ).strip()
            fallback["contract_rejection_note"] = reason
            return fallback
        decision.pop("blueprint", None)
        return decision

    if action == "accept_contract":
        target = decision.get("target")
        if not isinstance(target, str) or not target.strip():
            fallback = role_fallback_action(agent_data.get("role"), agent_data)
            fallback["reasoning"] = (
                fallback.get("reasoning", "") + " (invalid contract accept)"
            ).strip()
            fallback["contract_rejection_note"] = (
                "accept_contract requires target (contract id)"
            )
            return fallback
        decision.pop("blueprint", None)
        return decision

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
