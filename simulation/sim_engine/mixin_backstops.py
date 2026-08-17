"""Phase 6d mixin: message bus + emergent-role backstop slice of
SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_deliver_message` through
`_maybe_retire_custom_resource` (formerly core.py lines ~5340-6194). Covers:
the agent message bus/inbox, emergent-role helpers, scarcity/confront
mechanics, role auto-switching, and a large family of deterministic
backstops that keep the village unstuck (stalled-contribution,
feed-starving, forced-hunt, idle-district projects, funded-project
building, stalled-project abandonment, approved-custom-resource starts,
newcomer welcome, blueprint retirement/amnesty, Sage review skip/amnesty,
and custom-resource retirement).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _BackstopsMixin:
    """Mixin slice of SimEngine: message bus/inbox, emergent roles, and
    deterministic village-unsticking backstops. See module docstring for
    exact scope."""

    # --- message bus / inbox ---
    def _deliver_message(self, from_name, to_name, text, kind):
        if not text:
            return
        broadcast = to_name in ("everyone", "all", None)
        for r in self.agents:
            if r["name"] == from_name:
                continue
            if not broadcast and r["name"] != to_name:
                continue
            r["inbox"].append({"from": from_name, "text": text,
                               "kind": kind or "message", "frame": self.frameTick})
            self._mark_context_dirty(r)
            while len(r["inbox"]) > INBOX_CAP:
                r["inbox"].pop(0)

    def _drain_inbox(self, agent):
        if not agent["inbox"]:
            return "none"
        msgs = " | ".join(f"{m['from']} ({m['kind']}): {m['text']}" for m in agent["inbox"])
        agent["inbox"] = []
        return msgs

    def _has_unread(self, agent):
        return bool(agent["inbox"])

    # --- emergent roles ---
    def _role_specialty_resource(self, role):
        return self.d["ROLE_PRIMARY_RESOURCE"].get((role or "").lower())

    def _is_flexible_role(self, role):
        return not self._role_specialty_resource(role) and role != "elder"

    def _role_is_filled(self, roles):
        """True if any living, able agent currently holds one of the roles."""
        role_set = set(roles) if not isinstance(roles, str) else {roles}
        return any(
            a["role"] in role_set
            and a.get("deathFrame") is None
            and not a["incapacitated"]
            for a in self.agents
        )

    def _edible_scarce(self, rid):
        """Village held + stockpiled quantity at/below scarcity threshold."""
        return self._village_holdings(rid) <= EDIBLE_SCARCITY_THRESHOLD

    def _meat_scarce(self):
        if self._edible_scarce("meat"):
            return True
        total = sum(self._village_holdings(rid) for rid in EDIBLE_RESOURCES)
        return total <= EDIBLE_SCARCITY_THRESHOLD

    def _gather_failing(self, rid):
        """True when every district of rid's gather zone is ecology-depleted."""
        zone = self._gather_zone_for_resource(rid)
        if not zone:
            return False
        districts = self._districts_of_kind(zone)
        if not districts:
            return True
        for did in districts:
            ratio = self._district_ecology_ratio(did)
            if ratio is not None and ratio >= STOCK_LOW_RATIO:
                return False
        return True

    def _wildlife_present(self):
        if not WILDLIFE_ENABLED:
            return False
        for cre in (self.civilization.get("wildlife") or []):
            if not cre.get("alive"):
                continue
            kind = cre.get("kind")
            if kind in WILDLIFE_DECORATIVE_KINDS or kind not in WILDLIFE_YIELD:
                continue
            return True
        return False

    def _agent_accessible_edible_total(self, agent):
        """Personal edibles plus nearby surplus (SHARE_RADIUS backstop semantics)."""
        total = sum(agent["resources"].get(rid, 0) for rid in EDIBLE_RESOURCES)
        for donor in self.agents:
            if donor is agent or donor["incapacitated"]:
                continue
            if self._distance_to(agent, donor) > SHARE_RADIUS:
                continue
            for rid in EDIBLE_RESOURCES:
                total += max(0, donor["resources"].get(rid, 0) - EDIBLE_RESERVE)
        return total

    def _nearest_gather_edible_distance(self, agent):
        best = None
        for rid in EDIBLE_RESOURCES:
            zone = self._gather_zone_for_resource(rid)
            if not zone:
                continue
            for did in self._districts_of_kind(zone):
                d = self._distance_to_district(agent, did)
                if best is None or d < best:
                    best = d
        return best

    def _nearest_prey_distance(self, agent):
        prey = self._nearest_huntable_wildlife(agent)
        if prey is None:
            return None
        return _dist(agent["x"], agent["y"], prey["x"], prey["y"])

    def _confront_pair_key(self, id_a, id_b):
        a, b = int(id_a), int(id_b)
        lo, hi = (a, b) if a <= b else (b, a)
        return f"{lo}:{hi}"

    def _confront_on_cooldown(self, agent, target):
        key = self._confront_pair_key(agent["id"], target["id"])
        expires = (self.civilization.get("confrontCooldowns") or {}).get(key, 0)
        return self.frameTick < expires

    def _confront_pressure_context(self, agent):
        if not path1_on("PRESSURE_LOOP_ENABLED"):
            return False
        if self._is_night() and not agent.get("homeStructureId"):
            return True
        note = agent.get("lastNightNote")
        if note and self.frameTick - note.get("frame", 0) <= CONFRONT_PRESSURE_WINDOW_FRAMES:
            return True
        return False

    def _confront_authorized(self, agent, target):
        if agent["relationships"].get(target["name"]) == "rival":
            return True
        return self._confront_pressure_context(agent)

    def _confront_eligible_targets(self, agent):
        em = self._sage_emergency()
        if em and agent["name"] in self._sage_responders(em):
            return []
        targets = []
        for other in self.agents:
            if other is agent or other["incapacitated"]:
                continue
            if other.get("deathFrame") is not None:
                continue
            if other["role"] == "elder":
                continue
            if not self._confront_authorized(agent, other):
                continue
            if self._confront_on_cooldown(agent, other):
                continue
            if self._distance_to(agent, other) > 200:
                continue
            targets.append(other)
        return targets

    def _most_abundant_edible_for_steal(self, agent):
        best, best_count = None, EDIBLE_RESERVE
        for rid in EDIBLE_RESOURCES:
            count = agent["resources"].get(rid, 0)
            if count > best_count:
                best_count, best = count, rid
        return best

    def _flee_from_agent(self, agent, other):
        dx = agent["x"] - other["x"]
        dy = agent["y"] - other["y"]
        dist = math.sqrt(dx * dx + dy * dy) or 1.0
        agent["targetX"] = agent["x"] + (dx / dist) * CONFRONT_FLEE_DIST
        agent["targetY"] = agent["y"] + (dy / dist) * CONFRONT_FLEE_DIST
        agent["waypoints"] = []
        agent["goal"] = None

    def _village_needed_role(self):
        """Return a gather role the village needs, or None.

        Checks three need sources in priority order (build gap, survival,
        ecology). Sid-parity Phase 1: specialization must rebalance to real
        collective need, not only stalled builds.
        """
        # 1) Build-project gather gap (original signal).
        if self._active_project_districts():
            unmet = self._first_unmet_resource_anywhere()
            if unmet:
                roles = self.d["RESOURCE_GATHER_ROLES"].get(unmet)
                if roles and not self._role_is_filled(roles):
                    return roles[0]

        # 2) Survival need: stock-aware, wildlife-aware precedence (specs/08).
        if SURVIVAL_ENABLED:
            living = self._living_agents()
            starving = [
                a for a in living
                if not a["incapacitated"] and a["hunger"] <= STARVING_HUNGER
            ]
            if len(starving) >= ROLE_STARVE_NEED_THRESHOLD:
                # Hunter before farmer/fisher when gather zones are barren but prey
                # exists (specs/08 survival precedence — old fixed-order missed this).
                if (self._wildlife_present()
                        and not self._role_is_filled("hunter")
                        and (self._meat_scarce()
                             or any(self._edible_scarce(r) for r in EDIBLE_RESOURCES))
                        and (self._gather_failing("food") or self._gather_failing("fish"))):
                    return "hunter"
                if self._edible_scarce("food") and not self._role_is_filled("farmer"):
                    return "farmer"
                if self._edible_scarce("fish") and not self._role_is_filled("fisher"):
                    return "fisher"
                for rid in EDIBLE_RESOURCES:
                    for role in (self.d["RESOURCE_GATHER_ROLES"].get(rid) or ()):
                        if not self._role_is_filled(role):
                            return role

        # 3) Ecology need: a tracked resource is depleted/low village-wide
        # and its gather role is unfilled (meat uses village totals — no gatherZone).
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
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
            meat_total = self._village_holdings("meat")
            meat_ratio = meat_total / MEAT_SCARCITY_CAP
            if meat_ratio < STOCK_LOW_RATIO:
                roles = self.d["RESOURCE_GATHER_ROLES"].get("meat")
                if roles and not self._role_is_filled(roles):
                    scarce.append((meat_ratio, roles[0]))
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

    def _maybe_forced_hunt(self):
        """Deterministic hunt goal when starving, prey is nearer than gather, and
        edibles are below reserve. Runs after _maybe_feed_starving (specs/08)."""
        if not SURVIVAL_ENABLED or not WILDLIFE_ENABLED:
            return
        em = self._sage_emergency()
        responders = self._sage_responders(em) if em else set()
        candidates = []
        for agent in self.agents:
            if agent["incapacitated"] or agent["hunger"] > STARVING_HUNGER:
                continue
            if agent["name"] in responders or self._first_edible(agent):
                continue
            if self._agent_accessible_edible_total(agent) >= EDIBLE_RESERVE:
                continue
            prey = self._nearest_huntable_wildlife(agent)
            if prey is None:
                continue
            prey_dist = _dist(agent["x"], agent["y"], prey["x"], prey["y"])
            gather_dist = self._nearest_gather_edible_distance(agent)
            if gather_dist is not None and gather_dist <= prey_dist:
                continue
            goal = agent.get("goal")
            if goal and goal.get("kind") in ("gather", "hunt"):
                continue
            candidates.append((0 if agent["role"] == "hunter" else 1, prey_dist, agent, prey))
        candidates.sort(key=lambda t: (t[0], t[1]))
        for _, _, agent, prey in candidates:
            agent["goal"] = {
                "kind": "hunt",
                "target": prey.get("id"),
                "ttl": FORCED_HUNT_GOAL_TTL,
            }
            self._push_activity(
                f"{agent['name']} is starving and hunts nearby {prey.get('kind')}")

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
        AGENT_DEFS entry moves in (at most one per gate interval). Once all 12
        hand-written AGENT_DEFS are occupied, falls back to
        _generated_agent_defs (the same deterministic pool used for a
        roster_size > 12 cold-start) so this path can still reach
        MAX_ROSTER_SIZE -- deliberately not _next_agent_slot's style (random
        name/color), since this function has always been deterministic and
        _generated_agent_defs is what the cold-start path already uses for
        these exact slot indices, so a newcomer looks identical regardless of
        whether the village started large or grew into this slot. Newcomers
        persist via state.db like any other agent.

        AGENT_DEFS ids must never collide with an id already held by any
        agent that has ever existed in this world -- living or dead (mirrors
        _next_agent_slot's fix for the same defect). Unlike that function,
        this path is deliberately deterministic (see above), so a candidate
        whose name is free but whose id collides is not eligible: we don't
        substitute a different id for it, we just skip it and let the next
        candidate (from AGENT_DEFS, then the generated pool) be considered.
        If nothing in either source has both a free name and a free id, no
        newcomer arrives this cycle -- same as any other "unused is None"
        case today."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return
        # Corpses remain in self.agents for burial; only the living occupy beds.
        if len(self._living_agents()) >= self._population_cap():
            return
        used_ids = {a["id"] for a in self.agents}

        def _free(d):
            return d["name"] not in self.agent_names and d["id"] not in used_ids

        unused = next((d for d in AGENT_DEFS if _free(d)), None)
        if not unused:
            generated_pool = _generated_agent_defs(MAX_ROSTER_SIZE - len(AGENT_DEFS))
            unused = next((d for d in generated_pool if _free(d)), None)
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

    # --- sage review (two-stage blueprint approval, always on) ---
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

    # --- custom-resource retirement (orphan GC; no cap — invention unlimited) ---
    def _custom_resource_referenced(self, rid):
        """True while anything still uses the custom resource: obtainable via
        gather/recipe/structure/mint, a structure function (produces/boosts/
        stores/upkeep/unlocks), a project (registry, standing, or active) that
        needs or contributed it, a recipe that inputs or outputs it (pending
        included), a harvest-quota rule target, or a remaining balance in the
        stockpile / any agent's inventory."""
        if self._resource_is_obtainable(rid):
            return True
        c = self.civilization
        if c["stockpile"].get(rid, 0) > 0:
            return True
        if any(a["resources"].get(rid, 0) > 0 for a in self.agents):
            return True
        if rid in self.RECIPES or any(rid in r["inputs"] for r in self.RECIPES.values()):
            return True
        if any(p["id"] == rid or rid in p.get("inputs", {}) for p in c["pendingRecipes"]):
            return True
        type_ids = set(c.get("projectRegistry") or {})
        type_ids.update(s.get("type") for s in c.get("structures") or [] if s.get("type"))
        for pid in type_ids:
            tmpl = c["projectRegistry"].get(pid) or {}
            if rid in (tmpl.get("needs") or {}):
                return True
            if self._resource_in_function(rid, self._structure_function_for_type(pid)):
                return True
        for bp in c["pendingBlueprints"]:
            if rid in (bp.get("needs") or {}):
                return True
            if self._resource_in_function(rid, bp.get("function") or {}):
                return True
        for p in c["districtProjects"].values():
            if not p:
                continue
            if rid in (p.get("needs") or {}):
                return True
            if rid in (p.get("contributed") or {}):
                return True
        for q in c.get("harvestQuotas", {}).values():
            if q.get("resource") == rid:
                return True
        return False

    def _maybe_retire_custom_resource(self):
        """Prune orphan custom resources after CUSTOM_RESOURCE_RETIRE_FRAMES.

        No cap — invention stays unlimited by policy. Ids with no references
        get a stamp-on-first-sight clock; once the window elapses the registry
        entry plus stockpile and districtStocks keys are removed. Retired ids
        are re-inventable (no tombstone)."""
        c = self.civilization
        frames = c.setdefault("customResourceAddedFrame", {})
        for rid in list(c["resourceRegistry"]):
            if rid in BASE_RESOURCES or rid in CRAFTED_RESOURCES:
                continue
            if self._custom_resource_referenced(rid):
                continue
            added = frames.get(rid)
            if added is None:
                frames[rid] = self.frameTick
                continue
            if self.frameTick - added < CUSTOM_RESOURCE_RETIRE_FRAMES:
                continue
            name = c["resourceRegistry"][rid].get("name", rid)
            del c["resourceRegistry"][rid]
            c["stockpile"].pop(rid, None)
            for stocks in c["districtStocks"].values():
                stocks.pop(rid, None)
            frames.pop(rid, None)
            self._push_activity(
                f"The idea of {name} has faded from the village — nothing made or used it")

