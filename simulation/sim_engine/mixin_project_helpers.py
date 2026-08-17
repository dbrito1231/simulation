"""Phase 6d mixin: project/invention helper slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_path1_industry_benchmark`
through `_start_project_for` (formerly core.py lines ~1244-1478). Covers:
Path 1 industry/tool benchmarks, project resource-list/belief-score helpers,
default project selection per role, seed-exhaustion checks, invention
requirement gating, and `_start_project_for` (the core "begin a build
project" mechanic).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _ProjectHelpersMixin:
    """Mixin slice of SimEngine: Path 1 benchmarks and project/invention
    helpers. See module docstring for exact scope."""

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
            entry = self._belief_entry(belief_id, agent)
            words = {w for w in re.findall(r"[a-z]{3,}", str(entry.get("tenet") or "").lower())}
            score += sum(1 for word in words if word in haystack)
            affinity = set(entry.get("affinity") or ())
            if "priority" in affinity and self._active_priority_resource(agent) in (tmpl.get("needs") or {}):
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
        if self._type_tier(type_) >= 2:
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

