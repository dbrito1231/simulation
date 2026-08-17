"""Phase 6d mixin: population lifecycle slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_life_stage` through
`_spawn_newborn` (formerly core.py lines ~3414-4574). Covers: Phase F
population lifecycle (aging, natural death, inheritance), Cemetery & burial
(grave placement/burial mechanics), the village repair-pressure backstop and
ruin-culling machinery, succession election machinery (daily-council-driven
and standalone), and birth/newcomer machinery (ally-pair birth checks,
newborn spawning).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _LifecycleMixin:
    """Mixin slice of SimEngine: population lifecycle, cemetery/burial,
    repair/ruin backstop, succession, and birth/newcomer machinery. See
    module docstring for exact scope."""

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
        if TESTAMENT_ENABLED and WIKI_MEMORY:
            self._merge_testament_on_death(agent)
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
        """Heirs are the deceased's living children if any exist; otherwise
        every living adult shares equally (a village this small has no formal
        family tree beyond the persisted children[] linkage)."""
        if DYNASTY_TREE_ENABLED:
            by_name = {a["name"]: a for a in self.agents}
            children = [
                by_name[n] for n in (agent.get("children") or [])
                if n in by_name and by_name[n].get("deathFrame") is None
            ]
        else:
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

    # --- Cemetery & burial: permanent death shouldn't leave a corpse lying
    # wherever it fell. ---
    def _cemetery_district_id(self):
        """The dedicated burial-grounds district, if present."""
        for did, d in self.civilization["districts"].items():
            if d.get("kind") == "cemetery" and d.get("grave_grid"):
                return did
        return None

    def _working_cemeteries(self):
        """Cemetery plots that can receive burials. Burial uses the district
        grave_grid, not the chapel's produce/boost status -- so a disrepaired
        or ruined chapel must not strand corpses (the escape is repair, but
        burial itself stays reachable)."""
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
        c = self.civilization
        starter = STARTER_DISTRICTS["cemetery_grounds"]
        mutated = False
        if "cemetery_grounds" not in c["districts"]:
            c["districts"]["cemetery_grounds"] = json.loads(json.dumps(starter))
            c["districtProjects"].setdefault("cemetery_grounds", None)
            c["districtLastContribution"].setdefault("cemetery_grounds", 0)
            mutated = True
        if "cemetery_gate" not in c["roadNodes"]:
            c["roadNodes"]["cemetery_gate"] = dict(STARTER_ROAD_NODES["cemetery_gate"])
            mutated = True
        edge = ["beach_gate", "cemetery_gate"]
        if edge not in c["roadEdges"] and list(reversed(edge)) not in c["roadEdges"]:
            c["roadEdges"].append(edge)
            self._recompute_road_paths()
            mutated = True
        if mutated:
            self._bump_districts_epoch()

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
        if not LIFECYCLE_ENABLED:
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

    def _village_repair_pressure(self):
        """True when ruin ratio or working fraction crosses campaign thresholds."""
        if not GOODS_ENABLED:
            return False
        structures = self.civilization["structures"]
        total = len(structures)
        if total == 0:
            return False
        ruined = sum(
            1 for s in structures
            if s.get("isRuin") or s.get("condition", 100) <= 0
        )
        working = sum(
            1 for s in structures
            if not s.get("isRuin")
            and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
        )
        return (
            ruined / total >= REPAIR_CAMPAIGN_RUIN_RATIO
            or working / total < REPAIR_CAMPAIGN_WORKING_FRAC
        )

    def _category_has_disrepaired_or_ruined(self, type_):
        for s in self.civilization["structures"]:
            if s.get("type") != type_:
                continue
            if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                return True
        return False

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
                lambda: (
                    self._working_structure_count("house") == 0
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("house")
                    )
                ),
                "Housing emergency -- {summary} (no working houses left; "
                "population cap was locked)",
            ),
            (
                "market",
                lambda: GOODS_ENABLED and ECONOMY_ENABLED and has_type("market"),
                lambda: (
                    not self._market_active()
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("market")
                    )
                ),
                "Market emergency -- {summary} (no working market left; "
                "priced trade was locked)",
            ),
            (
                "workshop",
                lambda: GOODS_ENABLED and has_type("workshop"),
                lambda: (
                    self._working_structure_count("workshop") == 0
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("workshop")
                    )
                ),
                "Workshop emergency -- {summary} (no working workshop left; "
                "crafting was locked)",
            ),
            (
                "foundry",
                lambda: GOODS_ENABLED and PATH1_ENABLED and has_type("foundry"),
                lambda: (
                    self._working_structure_count("foundry") == 0
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("foundry")
                    )
                ),
                "Foundry emergency -- {summary} (no working foundry left; "
                "tier-3 crafting was locked)",
            ),
            (
                "granary",
                lambda: GOODS_ENABLED and CRAFTING_ENABLED and has_type("granary"),
                lambda: (
                    self._working_structure_count("granary") == 0
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("granary")
                    )
                ),
                "Granary emergency -- {summary} (no working granary left; "
                "food storage was locked)",
            ),
            (
                "farm_plot",
                lambda: GOODS_ENABLED and has_type("farm_plot"),
                lambda: (
                    self._working_structure_count("farm_plot") == 0
                    or (
                        self._village_repair_pressure()
                        and self._category_has_disrepaired_or_ruined("farm_plot")
                    )
                ),
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

    def _repair_campaign_targets(self):
        """Worst structures village-wide, preferring critical categories."""
        damaged = [
            s for s in self.civilization["structures"]
            if s.get("isRuin") or s.get("condition", 100) < 100
        ]
        if not damaged:
            return []
        critical = [s for s in damaged if s.get("type") in REPAIR_CAMPAIGN_CRITICAL_TYPES]
        pool = critical or damaged

        def _sort_key(s):
            is_critical = 0 if s.get("type") in REPAIR_CAMPAIGN_CRITICAL_TYPES else 1
            return (is_critical, s.get("condition", 100))

        return sorted(pool, key=_sort_key)

    def _maybe_repair_campaign(self):
        """Assign repair goals when village-wide structural pressure is high."""
        if not GOODS_ENABLED or not self._village_repair_pressure():
            return
        em = self._sage_emergency() if SURVIVAL_ENABLED else None
        responders = self._sage_responders(em) if em else set()
        targets = self._repair_campaign_targets()
        if not targets:
            return
        assigned = 0
        used_agents = set()
        for target in targets:
            if assigned >= REPAIR_CAMPAIGN_MAX_ASSIGN:
                break
            agent = self._repair_backstop_agent([target])
            if not agent or agent["name"] in used_agents:
                continue
            if agent["name"] in responders:
                continue
            if (agent.get("goal") or {}).get("kind") == "repair":
                continue
            agent["goal"] = {
                "kind": "repair",
                "target": target["id"],
                "ttl": REPAIR_CAMPAIGN_GOAL_TTL,
            }
            used_agents.add(agent["name"])
            assigned += 1

    def _village_resources_total(self):
        totals = dict(self.civilization.get("stockpile") or {})
        for agent in self.agents:
            if agent.get("deathFrame") is not None:
                continue
            for res, amt in (agent.get("resources") or {}).items():
                totals[res] = totals.get(res, 0) + int(amt)
        return totals

    def _village_can_afford_rebuild(self, structure):
        cost = self._repair_cost(structure)
        totals = self._village_resources_total()
        for res, amt in cost.items():
            if totals.get(res, 0) < amt:
                return False
        return True

    def _village_can_afford_any_rebuild(self):
        ruins = [
            s for s in self.civilization["structures"]
            if s.get("isRuin") or s.get("condition", 100) <= 0
        ]
        return any(self._village_can_afford_rebuild(s) for s in ruins)

    def _cull_priority_key(self, structure, ruined_list):
        type_ = structure.get("type")
        if type_ not in REPAIR_CAMPAIGN_CRITICAL_TYPES:
            return (0, -(structure.get("ruinedSinceFrame") or 0))
        if self._working_structure_count(type_) == 0:
            ruins_of_type = [r for r in ruined_list if r.get("type") == type_]
            if len(ruins_of_type) <= 1:
                return (2, -(structure.get("ruinedSinceFrame") or 0))
        return (1, -(structure.get("ruinedSinceFrame") or 0))

    def _remove_structures_from_registry(self, structure_ids):
        """Drop structures and clear homeStructureId / reorgTasks references."""
        if not structure_ids:
            return 0
        ids = set(structure_ids)
        c = self.civilization
        before = len(c["structures"])
        c["structures"] = [s for s in c["structures"] if s.get("id") not in ids]
        removed = before - len(c["structures"])
        for agent in self.agents:
            if agent.get("homeStructureId") in ids:
                agent["homeStructureId"] = None
        reorg_tasks = c.get("reorgTasks")
        if reorg_tasks is not None:
            c["reorgTasks"] = [
                t for t in reorg_tasks if t.get("structureId") not in ids
            ]
        return removed

    def _maybe_cull_ruins(self):
        """Remove aged, unaffordable ruins when ruin pressure is high."""
        if not GOODS_ENABLED:
            return
        structures = self.civilization["structures"]
        total = len(structures)
        if total == 0:
            return
        ruined = [s for s in structures if s.get("isRuin")]
        if not ruined:
            return
        if len(ruined) / total <= REPAIR_CAMPAIGN_RUIN_RATIO:
            return
        eligible = []
        for s in ruined:
            age_frame = s.get("ruinedSinceFrame", 0)
            if self.frameTick - age_frame >= RUIN_CULL_AGE_FRAMES:
                eligible.append(s)
        if not eligible:
            return
        if self._village_can_afford_any_rebuild():
            return
        eligible.sort(key=lambda s: self._cull_priority_key(s, ruined))
        cull_count = min(RUIN_CULL_MAX_PER_CALL, max(RUIN_CULL_MIN_PER_CALL, len(eligible)))
        to_remove = eligible[:cull_count]
        ids = [s["id"] for s in to_remove]
        names = [s.get("name") or s.get("type") for s in to_remove]
        removed = self._remove_structures_from_registry(ids)
        if removed:
            self._push_activity(
                f"The village abandons {removed} ruined structure(s) beyond repair: "
                f"{', '.join(names)}")
            self._tick_structure_health_benchmark()

    # --- succession (#4): reuses the propose_rule/vote_rule scaffold ---
    def _succession_candidates(self, settlement_id=None):
        """Deterministic pool of agents who can safely take office now."""
        candidates = self._eligible_adults()
        if FACTION_SPLIT_ENABLED and settlement_id:
            candidates = [
                a for a in candidates
                if self._settlement_id_for_agent(a) == settlement_id
            ]
        if candidates:
            return candidates
        # A village made entirely of young survivors still needs leadership,
        # but an incapacitated survivor cannot safely win or exercise it.
        living = [a for a in self._living_agents() if not a["incapacitated"]]
        if FACTION_SPLIT_ENABLED and settlement_id:
            living = [
                a for a in living
                if self._settlement_id_for_agent(a) == settlement_id
            ]
        return living

    def _ensure_succession_daily_council(self):
        """Convene or refresh the visible emergency assembly for this election."""
        if not DAILY_COUNCIL_ENABLED:
            return
        pending = self.civilization.get("pendingSuccession")
        if not isinstance(pending, dict):
            return
        pending_sid = (
            pending.get("settlementId") or self._primary_settlement_id()
            if FACTION_SPLIT_ENABLED else None
        )
        council = self.civilization.get("dailyCouncil")
        if council is None:
            self._maybe_convene_daily_council()
            return
        if council.get("trigger") != "succession":
            # A settlement becoming leaderless takes precedence over an
            # ordinary assembly already in progress. Transform that session
            # in place so the election is immediately visible without
            # creating a colliding same-frame meeting id.
            council["trigger"] = "succession"
            if FACTION_SPLIT_ENABLED:
                council["settlementId"] = pending_sid
            self._refresh_daily_council_roster(council)
        ballot = council.get("ballot") or {}
        candidates = list(pending.get("candidates") or [])
        if ballot.get("id") == pending.get("electionId") \
                and ballot.get("candidates") == candidates \
                and (not FACTION_SPLIT_ENABLED
                     or council.get("settlementId") == pending_sid):
            return
        if FACTION_SPLIT_ENABLED:
            council["settlementId"] = pending_sid
            self._refresh_daily_council_roster(council)
        council["agenda"] = self._daily_council_agenda()
        council["ballot"] = {
            "kind": "succession", "id": pending.get("electionId"),
            "title": "Choose the next village elder",
            "proposedBy": "the village", "candidates": candidates,
            "votes": {}, "quorum": len(council.get("attendees") or []) // 2 + 1,
        }
        council["verdict"] = None
        council["elderVerdictSpoken"] = False
        council["round"] = 0
        council["nextSpeakerIdx"] = 0
        council["phase"] = "convening"
        council["phaseFrame"] = self.frameTick
        self._append_council_transcript({
            "type": "succession_restart", "who": "the village",
            "text": "Succession candidates changed; discussion and voting restart",
            "candidates": candidates, "electionId": pending.get("electionId"),
        })
        self._sync_daily_council_turns(council)

    # Seed gather roles (the four with a non-empty "specialty" in
    # roles.json), in the fixed order demotions rotate through.
    _SEED_GATHER_ROLES = ("farmer", "fisher", "gatherer", "miner")

    def _collapse_duplicate_elders(self, elders, context=None):
        """Repair a corrupted state with more than one living role=="elder"
        agent (e.g. a state.db saved before the switch_role/change_role
        leader-role guard landed). Deterministic, no randomness: the oldest
        living elder keeps office (ties broken by ascending agent id, the
        same tie-break convention used elsewhere for deterministic ordering
        -- see the burial sort in this file). Every other elder is demoted,
        round-robin by their position in self.agents, through the seed
        gather roles (farmer/fisher/gatherer/miner) -- mirroring
        _next_agent_slot's newborn role assignment, which also excludes
        "elder" from its role pool. Demoted agents have their assignedTask/
        idleCycles reset the same way switch_role does. Returns the
        survivor (or the sole elder, or None if `elders` is empty)."""
        if not elders:
            return None
        if len(elders) == 1:
            return elders[0]
        ordered = sorted(elders, key=lambda a: (-(a.get("age") or 0.0), a["id"]))
        survivor = ordered[0]
        demoted = ordered[1:]
        roster_order = {id(a): idx for idx, a in enumerate(self.agents)}
        demoted.sort(key=lambda a: roster_order.get(id(a), 0))
        demotions = []
        for i, demoted_agent in enumerate(demoted):
            new_role = self._SEED_GATHER_ROLES[i % len(self._SEED_GATHER_ROLES)]
            demoted_agent["role"] = new_role
            demoted_agent["assignedTask"] = None
            demoted_agent["idleCycles"] = 0
            demotions.append(f"{demoted_agent['name']} to {new_role}")
        where = f" in {context}" if context else ""
        self._push_activity(
            f"Duplicate elders repaired{where}: {survivor['name']} retains office; "
            + "; ".join(demotions) + ".")
        return survivor

    def _ensure_settlement_succession_elections(self):
        """Per-settlement elder repair when FACTION_SPLIT_ENABLED (multiple elders allowed)."""
        c = self.civilization
        self._init_settlements()
        pending = c.get("pendingSuccession")
        pending_sid = (
            (pending.get("settlementId") or self._primary_settlement_id())
            if isinstance(pending, dict) else None
        )
        for entry in c.get("settlements") or []:
            sid = entry.get("id")
            if not sid:
                continue
            settlement_elders = [
                agent for agent in self._living_agents()
                if agent.get("role") == "elder"
                and self._settlement_id_for_agent(agent) == sid
            ]
            if len(settlement_elders) > 1:
                self._collapse_duplicate_elders(settlement_elders, context=f"settlement {sid}")
            formal_elder = next((
                agent for agent in self._living_agents()
                if agent.get("role") == "elder"
                and self._settlement_id_for_agent(agent) == sid
            ), None)
            if formal_elder:
                if isinstance(pending, dict) and pending_sid == sid:
                    c["pendingRules"] = [
                        r for r in c.get("pendingRules") or []
                        if not (isinstance(r, dict) and r.get("kind") == "succession"
                                and (r.get("settlementId") or self._primary_settlement_id()) == sid)
                    ]
                    c["pendingSuccession"] = None
                    council = c.get("dailyCouncil")
                    council_sid = (
                        council.get("settlementId") or self._primary_settlement_id()
                        if isinstance(council, dict) else None
                    )
                    if council and council.get("trigger") == "succession" \
                            and council_sid == sid and not council.get("verdict"):
                        self._adjourn_daily_council(
                            f"Elder {formal_elder['name']} retains office")
                continue
            if isinstance(pending, dict) and pending_sid == sid:
                eligible = {a["name"] for a in self._succession_candidates(sid)}
                candidates = pending.get("candidates") or []
                if candidates and set(candidates).issubset(eligible):
                    continue
            if isinstance(pending, dict) and pending_sid and pending_sid != sid:
                continue
            if self._succession_candidates(sid):
                self._start_succession_election(settlement_id=sid)

    def _ensure_succession_election(self):
        """Repair missing/corrupt succession state without resetting a valid vote.

        A living formal elder, including one temporarily incapacitated, keeps
        office; Sage emergency owns their recovery. Only a village with no
        living role=="elder" needs succession. The validator deliberately
        accepts an expired but otherwise sound election so the existing TTL
        resolver can decide it immediately instead of granting a fresh term.

        Runs continuously (tick-gated, not just on restore), so a state.db
        saved with more than one living role=="elder" agent -- a shape only
        possible from before the switch_role/change_role leader-role guard
        existed -- self-heals the moment the engine ticks: see
        _collapse_duplicate_elders, called below before this function trusts
        a single `formal_elder`.
        """
        if not LIFECYCLE_ENABLED:
            return
        if FACTION_SPLIT_ENABLED:
            self._ensure_settlement_succession_elections()
            self._ensure_succession_daily_council()
            return
        c = self.civilization
        succession_rules = [
            r for r in c["pendingRules"]
            if isinstance(r, dict) and r.get("kind") == "succession"
        ]
        pending = c.get("pendingSuccession")
        living = self._living_agents()
        if not living:
            return

        elders = [a for a in living if a.get("role") == "elder"]
        if len(elders) > 1:
            self._collapse_duplicate_elders(elders)

        formal_elder = next((a for a in living if a.get("role") == "elder"), None)
        if formal_elder:
            # A restored stray ballot must never promote a second elder. Treat
            # an incapacitated formal elder as still holding office.
            council = c.get("dailyCouncil")
            resolved_here = (
                council
                and council.get("trigger") == "succession"
                and (council.get("verdict") or {}).get("winner") == formal_elder["name"]
                and not pending
            )
            if council and council.get("trigger") == "succession" and not resolved_here:
                self._adjourn_daily_council("formal elder retains office")
            if pending or succession_rules:
                c["pendingRules"] = [
                    r for r in c["pendingRules"]
                    if not (isinstance(r, dict) and r.get("kind") == "succession")
                ]
                c["pendingSuccession"] = None
                c["lastSuccessionActivityFrame"] = self.frameTick
                c["lastRuleActivityFrame"] = self.frameTick
                self._push_activity(
                    f"Succession voting is cancelled; Elder {formal_elder['name']} still holds office.")
            return

        eligible_names = {a["name"] for a in self._succession_candidates()}
        valid = isinstance(pending, dict)
        election_id = pending.get("electionId") if valid else None
        candidates = pending.get("candidates") if valid else None
        start_frame = pending.get("startFrame") if valid else None
        deadline = pending.get("deadline") if valid else None
        valid = (
            valid
            and isinstance(election_id, str) and bool(election_id)
            and isinstance(candidates, list) and bool(candidates)
            and all(isinstance(name, str) and name for name in candidates)
            and len(candidates) == len(set(candidates))
            and set(candidates).issubset(eligible_names)
            and isinstance(start_frame, int) and not isinstance(start_frame, bool)
            and isinstance(deadline, int) and not isinstance(deadline, bool)
            and 0 <= start_frame <= self.frameTick
            and deadline == start_frame + SUCCESSION_ELECTION_TTL_FRAMES
        )
        ballot_names = []
        ballot_ids = []
        if valid:
            for rule in succession_rules:
                candidate_name = rule.get("candidateName")
                ballot_names.append(candidate_name)
                ballot_ids.append(rule.get("id"))
                if (
                    rule.get("electionId") != election_id
                    or rule.get("value") != candidate_name
                    or candidate_name not in candidates
                    or not isinstance(rule.get("id"), str) or not rule.get("id")
                    or not isinstance(rule.get("votes"), dict)
                    or not all(
                        isinstance(voter, str) and vote in {"yes", "no"}
                        for voter, vote in (rule.get("votes") or {}).items()
                    )
                ):
                    valid = False
                    break
            valid = (
                valid
                and len(ballot_names) == len(candidates)
                and len(set(ballot_ids)) == len(ballot_ids)
                and sorted(ballot_names) == sorted(candidates)
            )
        if valid:
            self._ensure_succession_daily_council()
            return

        # _start_succession_election atomically replaces every old succession
        # ballot. If all survivors are incapacitated, clear corrupt state and
        # defer quietly until a safe candidate recovers.
        c["pendingRules"] = [
            r for r in c["pendingRules"]
            if not (isinstance(r, dict) and r.get("kind") == "succession")
        ]
        c["pendingSuccession"] = None
        if self._succession_candidates():
            self._start_succession_election()
            self._ensure_succession_daily_council()

    def _start_succession_election(self, settlement_id=None):
        """One pending 'succession' rule per eligible candidate (adults,
        excluding the just-deceased elder, capped to keep MAX_PENDING_RULES
        headroom for ordinary governance). Candidates are the eligible-adult
        set -- deterministic, no LLM involved in nomination. Daily Council
        projects these records into its candidate ballot; with that feature
        off, legacy vote_rule exclusivity still applies."""
        c = self.civilization
        sid = settlement_id or self._primary_settlement_id()
        candidates = self._succession_candidates(sid if FACTION_SPLIT_ENABLED else None)
        if not candidates:
            return  # extinction/all-incapacitated edge: no safe winner yet
        candidates = candidates[:max(2, MAX_PENDING_RULES)]
        election_id = (
            f"succession_{sid}_{self.frameTick}" if FACTION_SPLIT_ENABLED
            else f"succession_{self.frameTick}")
        if FACTION_SPLIT_ENABLED:
            c["pendingRules"] = [
                r for r in c["pendingRules"]
                if not (r.get("kind") == "succession"
                        and (r.get("settlementId") or self._primary_settlement_id()) == sid)
            ]
        else:
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
            if FACTION_SPLIT_ENABLED:
                entry["settlementId"] = sid
            entries.append(entry)
            c["pendingRules"].append(entry)
        pending_payload = {
            "electionId": election_id,
            "candidates": [cand["name"] for cand in candidates],
            "startFrame": self.frameTick,
            "deadline": self.frameTick + SUCCESSION_ELECTION_TTL_FRAMES,
        }
        if FACTION_SPLIT_ENABLED:
            pending_payload["settlementId"] = sid
        c["pendingSuccession"] = pending_payload
        c["lastSuccessionActivityFrame"] = self.frameTick
        c["lastRuleActivityFrame"] = self.frameTick
        scope = f" ({sid})" if FACTION_SPLIT_ENABLED and sid != self._primary_settlement_id() else ""
        self._push_activity(
            f"The village{scope} must choose a new elder. "
            f"Candidates: {', '.join(c['pendingSuccession']['candidates'])}.")
        self._push_communication("election", "the village", "everyone",
                                 f"Succession election opened{scope}: "
                                 f"{', '.join(c['pendingSuccession']['candidates'])}")

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
        winner_sid = (
            rule.get("settlementId") or self._primary_settlement_id()
            if FACTION_SPLIT_ENABLED else None)
        other_candidates = [r.get("candidateName") for r in c["pendingRules"]
                            if r["kind"] == "succession" and r.get("electionId") == election_id
                            and r.get("candidateName") != winner_name]
        c["pendingRules"] = [r for r in c["pendingRules"]
                             if not (r["kind"] == "succession" and r.get("electionId") == election_id)]
        c["pendingSuccession"] = None
        c["lastSuccessionActivityFrame"] = self.frameTick
        incumbent = next((
            a for a in self._living_agents()
            if a.get("role") == "elder" and a is not winner
            and (not FACTION_SPLIT_ENABLED or self._settlement_id_for_agent(a) == winner_sid)
        ), None)
        if incumbent:
            self._push_activity(
                f"Succession voting closes; Elder {incumbent['name']} still holds office.")
            return
        if not winner or winner.get("deathFrame") is not None or winner["incapacitated"]:
            # Edge case: the winning candidate died (of old age) or collapsed
            # during the ~13 min TTL window between nomination and tiebreak.
            # Crowning a corpse (or a currently-incapacitated agent) would
            # leave the village silently leaderless -- no other code path
            # re-triggers an election for an agent that was never actually
            # made elder. Re-open a fresh election among the remaining
            # candidates instead; the arc still cannot stall, it just takes
            # one more round.
            self._push_activity(
                f"{winner_name or 'The chosen candidate'} could not take up the elder's mantle -- "
                f"the village must choose again.")
            self._start_succession_election(
                settlement_id=winner_sid if FACTION_SPLIT_ENABLED else None)
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
        """Flag-off deterministic escape for the legacy rule-ballot election.

        Daily Council owns its own visible phase/session TTL and is never
        bypassed here. Without it, once SUCCESSION_ELECTION_TTL_FRAMES elapses,
        highest legacy yes count wins, tied by lowest stable agent id.
        """
        c = self.civilization
        self._ensure_succession_election()
        pending = c.get("pendingSuccession")
        if not pending or self.frameTick < pending["deadline"]:
            return
        if DAILY_COUNCIL_ENABLED:
            # The emergency council's own phase/session TTL resolves solely
            # from recorded candidate choices. Never bypass visible
            # deliberation with legacy rule-ballot yes counts.
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
        slot is occupied by long-lived retirees.

        AGENT_DEFS ids must never collide with an id already held by any
        agent that has ever existed in this world -- living or dead, since
        buried/deceased agents stay in self.agents forever. This matters
        because AGENT_DEFS has grown over the project's history (e.g. Kane
        was added with a hardcoded id after some long-running worlds had
        already generated a procedural agent using that same id via the
        nextGeneratedAgentId counter). If an AGENT_DEFS entry's *name* is
        free but its *id* is already taken, we keep the def's identity
        (name/role/personality/color/zone) but substitute a fresh id from
        the same generated-id counter the fallback path uses, so the two
        paths can never hand out a colliding id. The generated-id fallback
        itself also fast-forwards past any id already in use, in case the
        counter is ever behind (e.g. after restoring an older save)."""
        used_ids = {a["id"] for a in self.agents}
        c = self.civilization

        def _next_generated_id():
            gen_id = c.get("nextGeneratedAgentId", 1000)
            while gen_id in used_ids:
                gen_id += 1
            c["nextGeneratedAgentId"] = gen_id + 1
            return gen_id

        unused = next((d for d in AGENT_DEFS if d["name"] not in self.agent_names), None)
        if unused:
            slot = dict(unused)
            if slot["id"] in used_ids:
                slot["id"] = _next_generated_id()
            return slot, False
        gen_id = _next_generated_id()
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
        # Inherit persistent home settlement from a parent (governance
        # residency, not physical position) rather than _make_agents'
        # cold-start "home" default, so a newborn to outpost-resident
        # parents is an outpost resident too.
        newborn["homeSettlementId"] = (
            parent_a.get("homeSettlementId")
            or parent_b.get("homeSettlementId")
            or self._primary_settlement_id()
        )
        parent_a.setdefault("children", []).append(newborn["name"])
        parent_b.setdefault("children", []).append(newborn["name"])
        # Low-skill start (#2): a newborn's specialty carries no structure/
        # role bonus differently from an adult -- it starts at the young
        # life stage, which _life_stage already surfaces in prompts, and
        # begins with empty resources rather than the usual starter stash.
        newborn["resources"] = {"food": 0, "wood": 0, "gold": 0, "coin": 0}
        if MEMES_ENABLED:
            belief_union = set(parent_a.get("beliefs") or set()) | set(parent_b.get("beliefs") or set())
            newborn["beliefs"] = belief_union
            newborn["inheritedBeliefs"] = sorted(belief_union)
        else:
            newborn["inheritedBeliefs"] = []
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
        if TESTAMENT_ENABLED and WIKI_MEMORY:
            self._seed_newborn_wiki_from_testament(newborn, parent_a, parent_b)
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

