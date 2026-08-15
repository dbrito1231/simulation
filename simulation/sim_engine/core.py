"""Server-authoritative simulation engine core (Phases 2-5 of the engine port).

This module ports the browser engine (simulation/index.html) into Python so the
simulation runs headless server-side. It owns ALL world state (the `civilization`
dict + `agents` list + frameTick/paused), runs a fixed-timestep daemon thread,
and dispatches LLM "think" jobs to a bounded worker pool. A single RLock guards
all state mutation (tick thread, LLM callbacks, and /state snapshots).

Field names and behavior mirror index.html per the frozen Contract 1/2 in
.cursor/plans/engine-port-contracts.md. The cognition side (prompt builder,
normalize_decision, role_fallback_action, MemoryStore, lm_complete) is reused
from server.py and injected at construction time to avoid a circular import.

Phase 6a package conversion: this module holds the `SimEngine` class body,
relocated unchanged (pure move) from the former single-file sim_engine.py.
Module-level feature flags/constants, DB persistence functions, and small
free-function helpers now live in sibling modules (constants.py,
persistence.py, helpers.py) within this package -- see
simulation/sim_engine/__init__.py for the package overview and full
re-export list.

IMPORTANT -- how this file is loaded: simulation/sim_engine/__init__.py does
NOT `import` this file as an ordinary submodule. It `exec()`s this file's
source directly into its own module namespace (after already populating that
namespace via `from .constants import *` / `from .persistence import *` /
`from .helpers import *`), so every name below (DB_PATH, SURVIVAL_ENABLED,
AGENT_DEFS, etc.) resolves as a bare global against the SAME dict that `import
sim_engine as se; se.DB_PATH = ...` mutates. Many scripts/*_smoke.py and
scripts/_*_smoke/*.py helpers monkeypatch dozens of these module-level names
(DB_PATH, SURVIVAL_ENABLED, GOD_MODE_ENABLED, WEATHER_ENABLED, ...) on the
imported `sim_engine` module object for test isolation; an ordinary
`from .constants import *` inside a normally-imported core.py submodule would
give this class its OWN separate copies of those names (bound at package-
import time), so a caller's `se.DB_PATH = tmp_path` would silently stop
affecting what SimEngine.save_state()/restore_state() actually read -- a
real behavior break, not a cosmetic one. The exec()-into-shared-namespace
approach keeps this file physically separate (for readability / the Phase 6b+
mixin split) while preserving exact single-file-module monkeypatch semantics.
See simulation/sim_engine/__init__.py for the loader.
"""

import hashlib
import copy
import json
import math
import os
import random
import shutil
import re
import secrets
import sqlite3
import threading
import time
import unicodedata
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone

# NOTE: constants.py/persistence.py/helpers.py names (DB_PATH,
# SURVIVAL_ENABLED, AGENT_DEFS, get_zone, _dist, ...) are NOT imported here.
# They are already present in this exec()-shared namespace by the time this
# file's body runs -- see the module docstring above and
# simulation/sim_engine/__init__.py.


class SimEngine(
    _WorldStateMixin,
    _StructuresEconomyMixin,
    _DiplomacyMixin,
    _WildlifeMixin,
    _PressureRaidersMixin,
    _ProjectHelpersMixin,
    _DivineMatrixMixin,
    _CraftingRulesMixin,
    _LifecycleMixin,
    _GovernanceCultureMixin,
    _BackstopsMixin,
    _CouncilGrowthMixin,
    _DecisionsMixin,
    _ThinkJobMixin,
    _PersistenceMixin,
    _DivineSamplingMixin,
    _GodValidationMixin,
    _GodLifecycleMixin,
    _GodBroadcastMixin,
    _GodBushBargainMixin,
    _GodGateMixin,
    _GodMiraclesMixin,
    _SnapshotMixin,
):
    """Owns world state and the fixed-timestep loop. Thread-safe via self.lock.

    Phase 6b: inherits `_WorldStateMixin` (mixin_world_state.py) for the
    state-delta/logging/memory/agent-lookup/districts-roads-movement/
    survival/Sage-emergency/project-helpers/resource-ecology method slice.
    Phase 6c: inherits `_StructuresEconomyMixin` (mixin_structures_economy.py)
    for structure functions/upgrades, weather, and market/economy, plus
    `_DiplomacyMixin` (mixin_diplomacy.py) for Path 1 tool tiers/tiles/
    terrain and diplomacy (settlements/caravans/treaties).
    Phase 6d: inherits `_WildlifeMixin` (mixin_wildlife.py) for the Path 1
    pressure loop and huntable wildlife; `_ProjectHelpersMixin`
    (mixin_project_helpers.py) for Path 1 benchmarks and project/invention
    helpers; `_DivineMatrixMixin` (mixin_divine_matrix.py) for Divine Voice
    guidance, Burning Bush, Anointment, Identity Forge, Architect Zones, and
    checkpoints; `_CraftingRulesMixin` (mixin_crafting_rules.py) for
    idle/task helpers, crafting, and rules/voting; `_LifecycleMixin`
    (mixin_lifecycle.py) for population lifecycle, cemetery/burial,
    repair/ruin backstop, succession, and birth; `_GovernanceCultureMixin`
    (mixin_governance_culture.py) for governance gates, blueprint/role
    validation, memes, and Phase G skills/library/chronicle/personality
    drift; `_BackstopsMixin` (mixin_backstops.py) for the message bus and
    village-unsticking backstops; and `_CouncilGrowthMixin`
    (mixin_council_growth.py) for the Daily Council Assembly, invention
    council, structure reorganization, district founding, and the rules
    backstop.
    Phase 6e: inherits `_DecisionsMixin` (mixin_decisions.py) for benchmarks,
    memory maintenance, the `apply_decision` world-mutation switch, talk-
    target resolution, and goal tracking; and `_ThinkJobMixin`
    (mixin_think_job.py) for the LLM think-payload builder, PIANO module
    orchestration, think-job dispatch/execution, and the per-frame tick loop.
    Phase 6f: inherits `_PersistenceMixin` (mixin_persistence.py) for
    full-state persistence (save/restore/serialize), Sovereign God mode core
    state, decision digests + Deja Vu replay, the stored-text contract, and
    story-event validation basics; and `_DivineSamplingMixin`
    (mixin_divine_sampling.py) for the Divine Matrix Phase 2 per-agent
    sampling overlay cluster.
    Phase 6g: inherits `_GodValidationMixin` (mixin_god_validation.py) for
    Sovereign God mode weather-override validation, timed lawgiver
    modifiers, repair/clear-ruins selection, the full per-kind envelope
    validator (`_validate_god_envelope`, moved whole), and the
    preview-outcome/digest/fingerprint cluster; and `_GodLifecycleMixin`
    (mixin_god_lifecycle.py) for the preview cache, idempotency store,
    guidance closure, divine-effect/bargain expiry, and the Optional
    Phase 8 free-prose compiler.
    Phase 6h: inherits `_GodBroadcastMixin` (mixin_god_broadcast.py) for the
    Sovereign God mode preview entry point, intervention-id/recording
    bookkeeping, the per-kind apply dispatcher, and the proclamation/
    providence/private-omen/whisper-campaign/crowd-compulsion/dream-broadcast
    apply handlers; `_GodBushBargainMixin` (mixin_god_bush_bargain.py) for
    the Burning Bush session lifecycle, Merovingian bargain primitives/
    settlement, Anointment apply/revoke, and Identity Forge apply handlers;
    and `_GodGateMixin` (mixin_god_gate.py) for the agent-sampling/memory/
    belief-plant/context-mask apply handlers and the full decision-gate/
    possession/veto/sweep machinery.
    Phase 6i (final mixin-extraction sub-phase): inherits `_GodMiraclesMixin`
    (mixin_god_miracles.py) for Sovereign God mode Phase 4 bounded immediate
    miracles, huntable wildlife god kinds, the Phase 6 weather override, the
    Phase 5 storyteller events, and the top-level God command dispatch
    (`god_apply`/`god_cancel`/`god_sight`); and `_SnapshotMixin`
    (mixin_snapshot.py) for control (`pause`/`resume`/`reset`) and the full
    Contract 2 /state snapshot-building cluster (`snapshot`/
    `snapshot_delta` and their helpers).
    `__init__`/`_select_active_defs`/`_make_agents`/`_reset_world` stay here
    since they are the construction path proper -- after Phase 6i, this is
    ALL that remains in core.py's SimEngine class body; the mixin-extraction
    portion of Phase 6 is structurally complete."""

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
        self._llm_orphan_timeouts = 0
        self.last_llm_dispatch_ms = 0.0
        self.activityLog = []      # most-recent-first, capped 30
        self.conversationLog = []  # most-recent-first, capped 100
        self.lastBenchmarks = {}
        self.lastMemorySize = 0
        self._memory_maint_index = 0
        self._stop = threading.Event()
        self._pin_think_queue = []
        self._pin_last_dispatch_frame = 0
        self._pin_cooldown_until_frame = 0
        if DETERMINISM_PINNING:
            random.seed(DETERMINISM_SEED)
        self._executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_LLM)
        # Sid-parity Phase 1: separate pool for PIANO module calls so they
        # never compete with decision calls for MAX_CONCURRENT_LLM slots.
        self.piano_workers = ThreadPoolExecutor(max_workers=PIANO_CONCURRENT_LLM)
        self._inflight = set()      # agent names with a think job in flight
        # Full Daily Council audit trail. This authoritative list mirrors the
        # MemoryStore export list and is rewritten atomically on every save.
        self.council_transcript_rows = []
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
        # Always-on scheduler state is deliberately inert while its gate is
        # off.  Individual agent fields are only introduced under that gate,
        # retaining the old serialized/runtime shape by default.
        self._piano_refresh_inflight = set()
        self._saga_inflight = None       # Future for in-flight day-boundary saga dispatch
        self._module_pulse_work = []
        self._module_refresh_failures = 0
        self._module_note_ages = []
        self._meta_agent_index = 0
        # Theory of Mind (F2): session-lifetime peer-action prediction scoring.
        self._peer_prediction_pending = {}
        self._peer_prediction_hits = 0
        self._peer_prediction_total = 0
        self.ROAD_PATH_CACHE = {}   # (nodeA, nodeB) -> [node ids]; see _recompute_road_paths
        # Living-ecosystem Phase 3: cosmetic shipment ring. Deliberately kept
        # off the civilization dict (see CARAVAN_VISUALS_ENABLED) so it is
        # never written to state.db.
        self.shipments = []
        self._shipment_seq = 0
        # Sovereign God mode (Phase 2): both caches are deliberately kept off
        # the civilization dict, in-memory only, never persisted to state.db
        # -- restart/reset/restore always start empty (see reset() and
        # restore_state()). _god_preview_cache: previewId -> preview record
        # (bounded GOD_PREVIEW_CACHE_MAX, each valid GOD_PREVIEW_TTL_SECONDS
        # wall-clock seconds). _god_requests: requestId -> apply outcome
        # (bounded GOD_REQUEST_CACHE_MAX), the idempotency store.
        self._god_preview_cache = {}
        self._god_requests = {}
        self._god_rejected_count = 0  # session-lifetime counter, benchmark metadata only
        # Sovereign God mode (Phase 4): cumulative grant_resource total across
        # every applied command this process lifetime, in-memory only, never
        # persisted -- see GOD_GRANT_SESSION_CAP above.
        self._god_grant_session_total = 0
        self._god_deja_vu_session_total = 0
        # Sovereign God mode (Optional Phase 8): compiler rate-limit +
        # session-cap state, in-memory only, never persisted -- mirrors
        # _god_preview_cache/_god_requests above. lastCompileWallTime
        # enforces GOD_COMPILER_MIN_INTERVAL_SEC; compileCount enforces
        # GOD_COMPILER_SESSION_CAP for this process's lifetime (bumped even
        # on a rejected/failed compile -- see god_compile_prose).
        self._god_compiler_state = {"lastCompileWallTime": 0.0, "compileCount": 0}
        # Autosave skip-if-unchanged: last successful write hash + last tick
        # the saver considered a write (even when skipped).
        self._last_saved_hash = None
        self._last_save_considered_at = 0.0
        # Persistence: structure sprite rows upserted only when dirty (separate
        # from HTTP delta last-mod maps, which are pruned not cleared per poll).
        self._persist_dirty_structure_sprites = set()
        self._persist_sprite_removals = set()
        self._reset_world(roster_size)

    # --- roster + cold start ---
    def _select_active_defs(self, roster_size):
        roster_size = max(1, min(MAX_ROSTER_SIZE, roster_size))
        if roster_size > len(AGENT_DEFS):
            # Phase 6 headroom: all 12 hand-written defs plus procedurally
            # generated ones for the remaining slots. roster_size <= 12
            # (today's default/range) never reaches this branch, so that
            # path's behavior is unchanged.
            return list(AGENT_DEFS) + _generated_agent_defs(roster_size - len(AGENT_DEFS))
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
                # Wiki-style compounding memory (WIKI_MEMORY flag, plan Phase
                # 4): merged/reconciled sections, populated only when the
                # flag is on. Always present so persistence is free.
                "memoryWiki": {},
                "resources": {"food": 2, "wood": 0, "gold": 0, "coin": 0},
                "relationships": {}, "inbox": [], "beliefs": set(), "votes": {},
                "currentZone": district["kind"], "currentDistrict": d["zone"],
                "waypoints": [], "message": None, "messageTimer": 0,
                "thinkTimer": 0, "thinkInterval": 300, "isThinking": False,
                # Sid-parity Phase 6: frame of this agent's last successfully
                # dispatched think (see _tick_once's staleness-priority
                # dispatch order). 0 at cold start so the initial round breaks
                # ties by roster order, same as before this field existed.
                "lastThinkFrame": 0,
                "lastAction": None, "lastReasoning": None, "consecutiveTalks": 0,
                "pendingThink": False, "assignedTask": None, "idleCycles": 0,
                "lastTaskedFrame": None, "lastContributedFrame": None,
                "consecutiveIdleMoves": 0, "hunger": 80, "health": 100,
                "incapacitated": False, "goal": None, "actionCounts": {},
                "commitment": None, "inventionTurn": False, "inventionRetryUsed": False,
                "councilTurn": False,
                "inventionBuildContext": None,
                "spriteDesignTurn": None,
                "lastBlueprintRejection": None, "lastGatherRejection": None,
                "lastUpgradeRejection": None, "lastSpriteRejection": None,
                "lastProjectRejection": None, "lastTerraformRejection": None,
                "lastCraftRejection": None, "lastRepairRejection": None,
                "lastRecipeRejection": None, "lastBurialRejection": None,
                "lastShelterNote": None, "lastSpokeFrame": 0,
                "persona": "", "idleFrames": 0, "moduleTick": 0,
                "moduleReports": {},
                **({"contextDirty": True, "contextDirtySince": time.time()}
                   if ALWAYS_ON_MODULES else {}),
                "modules": self._piano_default_modules(),
                **({"peerModel": {}} if THEORY_OF_MIND_ENABLED else {}),
                # Phase E: home structure id (None = homeless) + refusal nudges.
                "homeStructureId": None, "lastTradeRejection": None,
                "lastHomelessNudgeFrame": None,
                # Agent-driven reorg: structureId this agent is relocating, or None.
                "reorgTask": None,
                # Divine Matrix Phase 5: veto hold pauses movement + think scheduling.
                "divineHold": False,
                # Divine Matrix Phase 9: god-granted key tags for architect door zones.
                "godKeys": set(),
                "architectLimbo": None,
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
                a["children"] = []
                a["inheritedTestament"] = []
                a["inheritedBeliefs"] = []
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
            if RAIDERS_CONTAGION_ENABLED:
                a["infected"] = False
                a["infectionFrame"] = None
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
        self._init_state_delta_sets()
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
            "basePopulation": max(1, min(MAX_ROSTER_SIZE, roster_size)),
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
            # Distinct from lastRuleActivityFrame (which only advances on a
            # SUCCESSFUL enact/repeal and also backstops blueprint-stall
            # detection): this advances on every propose_rule attempt,
            # success or failure, so a rejected auto-proposal still respects
            # the full RULE_PROPOSE_COOLDOWN before retrying instead of
            # re-firing every RULES_TICK_FRAMES window.
            "lastRuleAttemptFrame": 0,
            # Monotonic counter (not frameTick) for auto-proposed priority-rule
            # instance ids -- see _maybe_advance_rules. Using a counter instead
            # of the raw frame number keeps the base36 suffix compact
            # indefinitely (a raw growing frameTick would eventually push
            # "priority_<resource>_<frame>" past SLUG_RE's 25-char cap on a
            # long-running/24-7 server, silently recreating the exact
            # permanently-blocked-id bug this field exists to fix).
            "priorityRuleSeq": 0,
            # Same rationale as priorityRuleSeq, but for auto-proposed
            # resource_tax instance ids (see _maybe_advance_rules' tax
            # branch) -- kept as a separate counter/field for restore
            # compatibility with existing saves that only know about
            # priorityRuleSeq.
            "taxRuleSeq": 0,
            # Living-ecosystem Phase 5: same rationale as priorityRuleSeq/
            # taxRuleSeq, but for the auto-proposed storm-emergency
            # "rationing" rule (see _maybe_advance_rules' emergency branch,
            # WEATHER_GOVERNANCE_ENABLED). A dedicated counter/field so an
            # old save missing it simply setdefault-backfills to 0.
            "emergencyRuleSeq": 0,
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
            # Ordered historical document of enacted ongoing rules. Active
            # provisions mirror rules; superseded/repealed entries remain so
            # amendments are legible after their live effects are gone.
            "constitution": [],
            # rule id -> normalized custom-effect grammar. This is compiled
            # on enact/restore and queried by real action computations.
            "customRuleModifiers": {},
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
            "dailyCouncil": None,
            "councilDigests": [],
            # Phase F (LIFECYCLE_ENABLED): population lifecycle + governance.
            "lastBirthFrame": 0,
            "lastDeathActivityFrame": 0,
            "births": 0,
            "deaths": 0,
            "nextGeneratedAgentId": 1000,  # synthetic ids for generated villagers once AGENT_DEFS is exhausted
            "nextWildlifeId": 1,           # huntable fauna creature ids (WILDLIFE_ENABLED)
            "wildlife": [],                # civilization["wildlife"] fauna records
            "confrontCooldowns": {},       # pairKey -> frameTick when cooldown expires
            "pendingSuccession": None,     # {electionId, candidates:[names], startFrame, deadline}
            "lastSuccessionActivityFrame": 0,
            "harvestQuotas": {},            # rule id -> {"district": id|None, "resource": id|None, "value": n}
            "rationingActive": {},          # rule id -> {"value": n}
            **({"quarantineActive": {}} if RAIDERS_CONTAGION_ENABLED else {}),
            "populationFloorHeld": False,   # last death-deferred-at-floor state, for the nudge
            # Phase G (CULTURE_ENABLED): knowledge, chronicle, meme mutation.
            "chronicle": [],                # capped ring: {"text": str, "frame": int, "kind": str}
            "saga": [],                     # capped ring: {"text": str, "frame": int, "dayIndex": int}
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
            "settlementStores": {},
            "path1Placements": 0,
            "path1TerrainMutations": 0,
            # Agent-driven structure reorganization (footprint-overlap fixup):
            # at most one task in flight; see _maybe_reorganize_structures.
            "reorgTasks": [],
            "lastReorgFrame": 0,
            "lastReorgCheckFrame": 0,
            "lastReorgNoRoomFrame": 0,
            # Living-ecosystem Phase 4: weather state machine (see
            # WEATHER_ENABLED above). Set to a real value below (after
            # districts exist, since "storm" picks from them); restore_state
            # setdefaults this for old saves without re-rolling an existing one.
            "weather": None,
            # Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md Phase 2):
            # set to a real default below via _default_god_state(), mirroring
            # the weather pattern above. Persists wholesale with the rest of
            # civilization (see save_state/_serialize_state) with NO
            # serializer change needed -- civ persists everything except
            # _CIV_SET_KEYS. recentRequests (the idempotency store) is
            # deliberately NOT part of this dict -- it lives only in
            # self._god_requests, in-memory, never persisted.
            "godState": None,
            # Contracts & escrow (CONTRACTS_ENABLED): open offers + engine-held
            # coin. Persisted wholesale; restore setdefaults empty containers.
            "contracts": [],
            "contractEscrow": 0,
            "nextContractId": 1,
            "contractsOpened": 0,
            "contractsFulfilled": 0,
            "contractDefaults": 0,
        }
        self._effect_period_fired = 0
        self._module_period_runs = 0
        self._last_effect_benchmark_fired = 0
        self._meta_agent_index = 0
        self.council_transcript_rows = []
        self._spoiled_period = 0     # Phase C: spoilage counter per benchmark period
        self._last_season = None     # Phase C: season-turn activity logging
        if ECOLOGY_ENABLED:
            self.civilization["districtStocks"] = self._init_district_stocks(self.civilization["districts"])
        self.civilization["weather"] = self._weather_default(0)
        self.civilization["godState"] = self._default_god_state()
        if path1_on():
            for d in self.civilization["districts"].values():
                d.setdefault("tiles", {})
                self._ensure_district_terrain(d)
            self._init_settlements()
        self._init_schism_storage()
        self._recompute_road_paths()
        self._rebuild_role_maps()
        active_defs = self._select_active_defs(roster_size)
        self.agent_names = set(d["name"] for d in active_defs)
        self.agents = self._make_agents(active_defs)
        self.frameTick = 0
        self._seed_beliefs()
        if WILDLIFE_ENABLED:
            self._seed_wildlife_population()
        self.districtsEpoch = 1
        self._on_world_replaced()


