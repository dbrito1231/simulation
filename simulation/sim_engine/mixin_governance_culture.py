"""Phase 6d mixin: governance gates + culture slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_active_harvest_quota`
through `_personality_with_drift` (formerly core.py lines ~4575-5339).
Covers: governance gates (#5 — harvest_quota/rationing enforcement),
blueprint/role validation and the live role registry, small relationship
helpers, memes (belief registry/seeding/spread/mutation), and Phase G
skills/library/chronicle/personality-drift mechanics.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GovernanceCultureMixin:
    """Mixin slice of SimEngine: governance gates, blueprint/role validation,
    memes, and Phase G skills/library/chronicle/personality-drift. See
    module docstring for exact scope."""

    # --- Schism storage (SCHISM_ENABLED): settlement-scoped governance maps ---
    _SCHISM_FLAT_KEYED_PAIRS = (
        ("rules", "rulesBySettlement", []),
        ("pendingRules", "pendingRulesBySettlement", []),
        ("constitution", "constitutionBySettlement", []),
        ("customRuleModifiers", "customRuleModifiersBySettlement", {}),
        ("harvestQuotas", "harvestQuotasBySettlement", {}),
        ("rationingActive", "rationingActiveBySettlement", {}),
        ("beliefRegistry", "beliefRegistryBySettlement", {}),
        ("memeTexts", "memeTextsBySettlement", {}),
    )

    def _primary_settlement_id(self):
        """Primary home settlement id (Path 1 `_init_settlements` convention)."""
        if path1_on():
            self._init_settlements()
        return "home"

    @staticmethod
    def _primary_settlement_id_for_civ(civ):
        """Home settlement id while restoring (before self.civilization is set)."""
        for entry in civ.get("settlements") or []:
            if isinstance(entry, dict) and entry.get("id") == "home":
                return "home"
        return "home"

    def _settlement_id_for_agent(self, agent):
        return self._settlement_for_agent(agent)

    def _rules_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("rules") or []
        bucket = c.get("rulesBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("rules") or []

    def _pending_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("pendingRules") or []
        bucket = c.get("pendingRulesBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("pendingRules") or []

    def _constitution_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("constitution") or []
        bucket = c.get("constitutionBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("constitution") or []

    def _registry_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("beliefRegistry") or {}
        bucket = c.get("beliefRegistryBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("beliefRegistry") or {}

    def _meme_texts_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("memeTexts") or {}
        bucket = c.get("memeTextsBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("memeTexts") or {}

    def _custom_modifiers_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("customRuleModifiers") or {}
        bucket = c.get("customRuleModifiersBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("customRuleModifiers") or {}

    def _harvest_quotas_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("harvestQuotas") or {}
        bucket = c.get("harvestQuotasBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("harvestQuotas") or {}

    def _rationing_for_settlement(self, sid):
        c = self.civilization
        if not SCHISM_ENABLED:
            return c.get("rationingActive") or {}
        bucket = c.get("rationingActiveBySettlement") or {}
        if sid in bucket:
            return bucket[sid]
        home = self._primary_settlement_id()
        if home in bucket:
            return bucket[home]
        return c.get("rationingActive") or {}

    def _wrap_schism_storage(self, civ, home):
        """Install settlement-keyed maps sharing refs with flat home fields."""
        for flat_key, keyed_key, default in self._SCHISM_FLAT_KEYED_PAIRS:
            obj = civ.get(flat_key)
            if obj is None:
                obj = dict(default) if isinstance(default, dict) else list(default)
                civ[flat_key] = obj
            keyed = civ.get(keyed_key)
            if not isinstance(keyed, dict):
                civ[keyed_key] = {home: obj}
            else:
                keyed.setdefault(home, obj)

    def _init_schism_storage(self):
        if not SCHISM_ENABLED:
            return
        self._wrap_schism_storage(self.civilization, self._primary_settlement_id())

    def _migrate_schism_storage_on_restore(self, civ):
        if not SCHISM_ENABLED:
            return
        self._wrap_schism_storage(civ, self._primary_settlement_id_for_civ(civ))

    def _rebuild_settlement_governance(self, sid):
        """Rebuild constitution + compiled custom effects for one settlement."""
        if not SCHISM_ENABLED:
            self._ensure_constitution()
            self._rebuild_custom_rule_modifiers()
            return
        self._ensure_constitution(sid)
        self._rebuild_custom_rule_modifiers(sid)

    def _is_global_governance_ballot(self, rule):
        """Treaty and succession ballots stay civ-wide (quorum + flat pending)."""
        if not rule:
            return False
        return rule.get("kind") in ("treaty", "succession")

    def _settlement_living_agent_count(self, sid):
        count = 0
        for agent in self.agents:
            if agent.get("incapacitated"):
                continue
            if self._settlement_id_for_agent(agent) == sid:
                count += 1
        return count

    def _ballot_settlement_id(self, rule):
        if not SCHISM_ENABLED or self._is_global_governance_ballot(rule):
            return self._primary_settlement_id()
        sid = rule.get("settlementId")
        if isinstance(sid, str) and sid:
            return sid
        proposer = self._find_agent(rule.get("proposedBy"))
        if proposer:
            return self._settlement_id_for_agent(proposer)
        return self._primary_settlement_id()

    def _all_enacted_rule_ids(self):
        ids = set()
        c = self.civilization
        if not SCHISM_ENABLED:
            for rule in c.get("rules") or []:
                if isinstance(rule, dict) and rule.get("id"):
                    ids.add(rule["id"])
            return ids
        for rules in (c.get("rulesBySettlement") or {}).values():
            for rule in rules or []:
                if isinstance(rule, dict) and rule.get("id"):
                    ids.add(rule["id"])
        return ids

    def _all_pending_rule_ids(self):
        ids = set()
        c = self.civilization
        if not SCHISM_ENABLED:
            for rule in c.get("pendingRules") or []:
                if isinstance(rule, dict) and rule.get("id"):
                    ids.add(rule["id"])
            return ids
        for pending in (c.get("pendingRulesBySettlement") or {}).values():
            for rule in pending or []:
                if isinstance(rule, dict) and rule.get("id"):
                    ids.add(rule["id"])
        return ids

    def _all_constitution_rule_ids(self):
        ids = set()
        c = self.civilization
        if not SCHISM_ENABLED:
            for provision in c.get("constitution") or []:
                if isinstance(provision, dict) and provision.get("id"):
                    ids.add(provision["id"])
            return ids
        for constitution in (c.get("constitutionBySettlement") or {}).values():
            for provision in constitution or []:
                if isinstance(provision, dict) and provision.get("id"):
                    ids.add(provision["id"])
        return ids

    def _governance_scope_lists(self, rule):
        """Return (rules, pending, settlement_id) for a ballot's domestic scope."""
        c = self.civilization
        if not SCHISM_ENABLED or self._is_global_governance_ballot(rule):
            return c["rules"], c["pendingRules"], self._primary_settlement_id()
        sid = self._ballot_settlement_id(rule)
        return self._rules_for_settlement(sid), self._pending_for_settlement(sid), sid

    def _find_pending_ballot(self, ballot_id, voter=None):
        c = self.civilization
        if not SCHISM_ENABLED:
            return next((r for r in c.get("pendingRules") or [] if r.get("id") == ballot_id), None)
        for rule in c.get("pendingRules") or []:
            if rule.get("id") == ballot_id and self._is_global_governance_ballot(rule):
                return rule
        if voter is not None:
            sid = self._settlement_id_for_agent(voter)
            for rule in self._pending_for_settlement(sid):
                if rule.get("id") == ballot_id:
                    return rule
        for pending in (c.get("pendingRulesBySettlement") or {}).values():
            for rule in pending or []:
                if rule.get("id") == ballot_id:
                    return rule
        return None

    def _pending_rules_for_voter(self, agent):
        if not SCHISM_ENABLED:
            return list(self.civilization.get("pendingRules") or [])
        sid = self._settlement_id_for_agent(agent)
        domestic = [
            rule for rule in self._pending_for_settlement(sid)
            if not self._is_global_governance_ballot(rule)
        ]
        global_pending = [
            rule for rule in self.civilization.get("pendingRules") or []
            if self._is_global_governance_ballot(rule)
        ]
        return domestic + global_pending

    # --- Schism trigger + secession (F4.3, SCHISM_ENABLED) ---
    def _governance_rule_kinds_for_beliefs(self):
        kinds = set(RULE_KINDS)
        if LIFECYCLE_ENABLED:
            kinds |= {"harvest_quota", "rationing"}
        return kinds

    def _belief_contradicts_enacted_rule(self, agent, belief_id, rule):
        """Deterministic opposition: reuse _belief_biased_vote signals plus
        affinity mismatch on enacted domestic rule kinds."""
        if belief_id not in (agent.get("beliefs") or ()):
            return False
        if not rule or not rule.get("enacted"):
            return False
        entry = self._belief_entry(belief_id, agent)
        affinity = set(entry.get("affinity") or MEME_RULE_AFFINITY.get(belief_id, set()))
        kind = rule.get("kind")
        if kind in affinity:
            return False
        if belief_id == MEME_RIVAL_ID and kind in ("rationing", "harvest_quota"):
            return True
        if belief_id == MEME_SEED_ID and kind == "priority" and rule.get("value") == "fish":
            return True
        if affinity and kind in self._governance_rule_kinds_for_beliefs():
            return True
        return False

    def _cluster_mutual_allies(self, agents):
        if len(agents) < 2:
            return True
        for i, left in enumerate(agents):
            rels = left.get("relationships") or {}
            for right in agents[i + 1:]:
                if rels.get(right["name"]) != "ally":
                    return False
                if (right.get("relationships") or {}).get(left["name"]) != "ally":
                    return False
        return True

    def _agent_rivals_elder(self, agent, elder):
        if not elder:
            return False
        return (agent.get("relationships") or {}).get(elder["name"]) == "rival"

    def _elder_for_settlement(self, settlement_id):
        for agent in self.agents:
            if agent.get("deathFrame") is not None or agent.get("incapacitated"):
                continue
            if agent.get("role") != "elder":
                continue
            if not SCHISM_ENABLED or self._settlement_id_for_agent(agent) == settlement_id:
                return agent
        return None

    def _find_schism_cluster(self, settlement_id):
        if not SCHISM_ENABLED or not MEMES_ENABLED or not RULES_ENABLED:
            return None
        elder = self._elder_for_settlement(settlement_id)
        if not elder:
            return None
        enacted = [r for r in self._rules_for_settlement(settlement_id) if r.get("enacted")]
        if not enacted:
            return None
        roster = [
            a for a in self.agents
            if a.get("deathFrame") is None and not a.get("incapacitated")
            and self._settlement_id_for_agent(a) == settlement_id
            and a.get("role") != "elder"
        ]
        belief_ids = set()
        for agent in roster:
            belief_ids |= set(agent.get("beliefs") or ())
        enacted_sorted = sorted(enacted, key=lambda r: r.get("id") or "")
        for rule in enacted_sorted:
            for belief_id in sorted(belief_ids):
                cluster = [
                    a for a in roster
                    if self._belief_contradicts_enacted_rule(a, belief_id, rule)
                    and self._agent_rivals_elder(a, elder)
                ]
                if len(cluster) < SCHISM_MIN_CLUSTER:
                    continue
                if self._cluster_mutual_allies(cluster):
                    return {
                        "agents": cluster,
                        "belief_id": belief_id,
                        "rule": rule,
                        "parent_sid": settlement_id,
                        "elder": elder,
                    }
        return None

    def _init_schism_settlement_buckets(self, settlement_id):
        c = self.civilization
        c.setdefault("rulesBySettlement", {}).setdefault(settlement_id, [])
        c.setdefault("pendingRulesBySettlement", {}).setdefault(settlement_id, [])
        c.setdefault("constitutionBySettlement", {}).setdefault(settlement_id, [])
        c.setdefault("customRuleModifiersBySettlement", {}).setdefault(settlement_id, {})
        c.setdefault("harvestQuotasBySettlement", {}).setdefault(settlement_id, {})
        c.setdefault("rationingActiveBySettlement", {}).setdefault(settlement_id, {})
        c.setdefault("beliefRegistryBySettlement", {}).setdefault(settlement_id, {})
        c.setdefault("memeTextsBySettlement", {}).setdefault(settlement_id, {})

    def _fork_settlement_governance(self, parent_sid, child_sid):
        parent_rules = self._rules_for_settlement(parent_sid)
        child_rules = [copy.deepcopy(r) for r in parent_rules if r.get("enacted")]
        self._init_schism_settlement_buckets(child_sid)
        self.civilization["rulesBySettlement"][child_sid] = child_rules
        self._rebuild_settlement_governance(child_sid)

    def _fork_settlement_beliefs(self, parent_sid, child_sid, belief_ids):
        parent_reg = self._registry_for_settlement(parent_sid)
        parent_texts = self._meme_texts_for_settlement(parent_sid)
        child_reg = {}
        child_texts = {}
        for bid in belief_ids:
            if bid in parent_reg:
                child_reg[bid] = copy.deepcopy(parent_reg[bid])
            if bid in parent_texts:
                child_texts[bid] = parent_texts[bid]
        self._init_schism_settlement_buckets(child_sid)
        self.civilization["beliefRegistryBySettlement"][child_sid] = child_reg
        self.civilization["memeTextsBySettlement"][child_sid] = child_texts

    def _ensure_schism_settlement(self, parent_sid):
        """Reuse an existing frontier settlement or found one via Path 1 helpers."""
        self._init_settlements()
        c = self.civilization
        home = self._primary_settlement_id()
        for entry in c.get("settlements") or []:
            sid = entry.get("id")
            if sid and sid != parent_sid and entry.get("districts"):
                self._init_schism_settlement_buckets(sid)
                self._ensure_settlement_stores()
                self._settlement_store_bucket(sid)
                return sid, entry["districts"][0]
        if len(c.get("settlements") or []) >= 2:
            return None, None
        plot = self._claim_frontier_plot()
        if not plot:
            return None, None
        self._found_district("village", DISTRICT_KIND_TEMPLATES["village"], plot)
        new_did = plot.get("claimedBy")
        if not new_did:
            return None, None
        sid = f"schism_{self.frameTick}"
        c["settlements"].append({
            "id": sid,
            "name": "Seceding Settlement",
            "districts": [new_did],
        })
        c["districts"][new_did]["settlementId"] = sid
        self._init_schism_settlement_buckets(sid)
        self._ensure_settlement_stores()
        self._settlement_store_bucket(sid)
        self._push_activity(f"A seceding faction claims the frontier as {sid}.")
        return sid, new_did

    def _migrate_agents_to_settlement(self, agents, settlement_id, district_id):
        district = self.civilization["districts"].get(district_id) or {}
        bounds = district.get("bounds") or {}
        cx = (bounds.get("x1", 0) + bounds.get("x2", 0)) / 2
        cy = (bounds.get("y1", 0) + bounds.get("y2", 0)) / 2
        for agent in agents:
            agent["currentDistrict"] = district_id
            if bounds:
                agent["x"] = cx
                agent["y"] = cy
            agent["goal"] = None

    def _execute_schism(self, cluster_info):
        parent_sid = cluster_info["parent_sid"]
        agents = cluster_info["agents"]
        belief_id = cluster_info["belief_id"]
        rule = cluster_info["rule"]
        child_sid, district_id = self._ensure_schism_settlement(parent_sid)
        if not child_sid or not district_id:
            return False
        belief_ids = set()
        for agent in agents:
            belief_ids |= set(agent.get("beliefs") or ())
        self._fork_settlement_governance(parent_sid, child_sid)
        self._fork_settlement_beliefs(parent_sid, child_sid, belief_ids)
        self._migrate_agents_to_settlement(agents, child_sid, district_id)
        names = ", ".join(a["name"] for a in agents)
        belief_name = self._belief_name(belief_id, agents[0])
        rule_name = rule.get("name") or rule.get("id")
        self.civilization["lastSchismFrame"] = self.frameTick
        self._push_activity(
            f"Schism: {names} secede to {child_sid} over belief {belief_name} "
            f"and enacted rule \"{rule_name}\".")
        self._push_communication(
            "schism", agents[0]["name"], "everyone",
            f"The faction secedes to {child_sid}, rejecting \"{rule_name}\".")
        if CULTURE_ENABLED:
            self._push_chronicle(
                f"{names} seceded to {child_sid} over {belief_name} vs \"{rule_name}\".",
                kind="schism")
        self._log_benchmark("schism", len(agents), {
            "parent": parent_sid,
            "child": child_sid,
            "belief": belief_id,
            "rule": rule.get("id"),
        })
        if LIFECYCLE_ENABLED:
            self._start_succession_election(settlement_id=child_sid)
        return True

    def _maybe_trigger_schism(self):
        if not SCHISM_ENABLED or not MEMES_ENABLED or not RULES_ENABLED:
            return
        c = self.civilization
        last = c.get("lastSchismFrame")
        if isinstance(last, int) and self.frameTick - last < SCHISM_COOLDOWN_FRAMES:
            return
        self._init_settlements()
        for entry in sorted(c.get("settlements") or [], key=lambda s: s.get("id") or ""):
            sid = entry.get("id")
            if not sid:
                continue
            cluster = self._find_schism_cluster(sid)
            if cluster and self._execute_schism(cluster):
                return

    def _set_constitution_for_settlement(self, sid, constitution):
        c = self.civilization
        if not SCHISM_ENABLED or sid == self._primary_settlement_id():
            c["constitution"] = constitution
            if SCHISM_ENABLED:
                c.setdefault("constitutionBySettlement", {})[sid] = constitution
        else:
            c.setdefault("constitutionBySettlement", {})[sid] = constitution

    def _set_custom_modifiers_for_settlement(self, sid, compiled):
        c = self.civilization
        if not SCHISM_ENABLED or sid == self._primary_settlement_id():
            c["customRuleModifiers"] = compiled
        else:
            c.setdefault("customRuleModifiersBySettlement", {})[sid] = compiled

    # --- governance gates (#5): harvest_quota / rationing enforcement ---
    def _active_harvest_quota(self, agent=None, settlement_id=None):
        if not LIFECYCLE_ENABLED:
            return None
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            quotas = self._harvest_quotas_for_settlement(sid)
        else:
            quotas = self.civilization.get("harvestQuotas") or {}
        if not quotas:
            return None
        # If the village has enacted more than one harvest_quota rule, they
        # compose as "must satisfy all of them" -- the strictest (lowest)
        # value binds, not the most permissive, so a later lenient vote can
        # never silently override an earlier, intentionally tight one.
        return min(q["value"] for q in quotas.values())

    def _harvest_quota_gate(self, agent, resource):
        """Returns (allowed, reason). Caps an agent's gathers of ONE resource
        in their current district per HARVEST_QUOTA_PERIOD_FRAMES window.
        Deterministic escape: the counter resets every period, so a refusal
        is never permanent -- wait out the period, gather a different
        resource, or move to another district."""
        quota = self._active_harvest_quota(agent)
        if quota is None:
            return True, None
        if self.frameTick - agent.get("lastQuotaResetFrame", 0) >= HARVEST_QUOTA_PERIOD_FRAMES:
            agent["gatherCountThisPeriod"] = {}
            agent["lastQuotaResetFrame"] = self.frameTick
        counts = agent.setdefault("gatherCountThisPeriod", {})
        district = agent.get("currentDistrict") or "?"
        key = f"{district}:{resource}"
        if counts.get(key, 0) >= quota:
            remaining = HARVEST_QUOTA_PERIOD_FRAMES - (self.frameTick - agent["lastQuotaResetFrame"])
            return False, (f"harvest quota reached for {resource} in {district} "
                           f"({quota}/period) -- resets in ~{max(1, remaining // 30)}s")
        return True, None

    def _record_harvest_quota_use(self, agent, resource, amount):
        if self._active_harvest_quota() is None:
            return
        counts = agent.setdefault("gatherCountThisPeriod", {})
        district = agent.get("currentDistrict") or "?"
        key = f"{district}:{resource}"
        counts[key] = counts.get(key, 0) + amount

    def _rationing_active_cap(self, agent=None, settlement_id=None):
        if not LIFECYCLE_ENABLED:
            return None
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            active = self._rationing_for_settlement(sid)
        else:
            active = self.civilization.get("rationingActive") or {}
        if not active:
            return None
        # Deterministic escape: rationing only actually restricts while
        # storage utilization is low -- once storage recovers, withdrawals
        # are unrestricted again even with the rule still enacted (matches
        # "rationing lifts when storage recovers" in the hard rules).
        if not self._storage_low():
            return None
        return min(v["value"] for v in active.values())

    def _storage_low(self):
        if not GOODS_ENABLED:
            return False
        caps = {rid: self._storage_capacity(rid) for rid in EDIBLE_RESOURCES}
        total_cap = sum(caps.values()) or 1
        c = self.civilization
        stored = sum(c["stockpile"].get(rid, 0) + sum(a["resources"].get(rid, 0) for a in self.agents)
                    for rid in EDIBLE_RESOURCES)
        return (stored / total_cap) < RATIONING_STORAGE_LOW_RATIO

    def _rationing_gate(self, agent, resource, amount):
        """Returns (allowed_amount, reason|None) for a stockpile withdrawal
        (contribute_resources reversed, trade, etc. all funnel through here
        when they pull FROM the shared stockpile). Caps rather than outright
        refuses so a partial withdrawal still gets through when possible."""
        cap = self._rationing_active_cap(agent)
        if cap is None or resource not in EDIBLE_RESOURCES:
            return amount, None
        if amount <= cap:
            return amount, None
        return cap, f"rationing limits {resource} withdrawals to {cap} while storage is low"

    # --- blueprint validation ---
    def _custom_resource_count(self):
        return len([rid for rid in self.civilization["resourceRegistry"]
                    if rid not in BASE_RESOURCES and rid not in CRAFTED_RESOURCES])

    def _custom_project_ids(self):
        return [pid for pid, p in self.civilization["projectRegistry"].items() if p.get("custom")]

    def _validate_blueprint(self, bp):
        c = self.civilization
        if not isinstance(bp, dict):
            return False, "blueprint must be an object"
        return self.d["validate_blueprint"](
            bp,
            list(c["resourceRegistry"].keys()),
            [p["id"] for p in c["pendingBlueprints"]],
            self._custom_project_ids(),
            self._custom_resource_count(),
            list(c["rejectedBlueprintIds"]),
            list(self._known_effect_vectors()),
            village_tier=self._village_tech_tier() if TECH_TREE_ENABLED else None,
        )

    # --- live role registry ---
    def _rebuild_role_maps(self):
        """Derive every role lookup from this world's persistent registry.

        The injected maps are intentionally replaced rather than mutating the
        server's seed maps: roles.json remains seed-only authoring data while a
        running world can safely specialize independently of another engine.
        """
        registry = self.civilization.get("roleRegistry") or {}
        self.d["ROLE_PROJECT"] = {
            role: definition.get("preferredProject", "house")
            for role, definition in registry.items() if isinstance(definition, dict)
        }
        self.d["ROLE_SKILLS"] = {
            role: definition.get("skill", "helps the village")
            for role, definition in registry.items() if isinstance(definition, dict)
        }
        self.d["ROLE_PRIMARY_RESOURCE"] = {
            role: definition["specialty"][0]
            for role, definition in registry.items()
            if isinstance(definition, dict) and definition.get("specialty")
        }
        gather_roles = {}
        for role, definition in registry.items():
            if not isinstance(definition, dict):
                continue
            for resource in definition.get("specialty") or []:
                gather_roles.setdefault(resource, []).append(role)
        self.d["RESOURCE_GATHER_ROLES"] = {
            resource: tuple(roles) for resource, roles in gather_roles.items()
        }

    def _validate_role(self, role):
        """Validate an emergent role proposal against the live world state."""
        c = self.civilization
        if not isinstance(role, dict):
            return False, "role must be an object"
        if len(c.get("pendingRoles") or []) >= MAX_PENDING_ROLES:
            return False, "too many pending roles"
        allowed = {"slug", "name", "specialty", "preferredProject", "skill"}
        extra = set(role) - allowed
        if extra:
            return False, f"unknown role fields: {', '.join(sorted(extra))}"
        slug = role.get("slug")
        if not isinstance(slug, str) or not self.SLUG_RE.match(slug):
            return False, "invalid role slug"
        registry = c.get("roleRegistry") or {}
        if slug in registry:
            return False, "role already exists"
        if any(p.get("slug") == slug for p in c.get("pendingRoles") or [] if isinstance(p, dict)):
            return False, "role is already pending"
        seed_roles = set(self.d["ROLES"])
        emergent_count = len(set(registry) - seed_roles)
        if emergent_count >= MAX_EMERGENT_ROLES:
            return False, "too many emergent roles"
        name = role.get("name")
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
            return False, "invalid role name"
        skill = role.get("skill")
        if not isinstance(skill, str) or not (1 <= len(skill.strip()) <= 160) or "\n" in skill:
            return False, "skill must be one line of 1-160 characters"
        specialty = role.get("specialty")
        if not isinstance(specialty, list) or len(specialty) > 4 \
                or any(not isinstance(resource, str) or resource not in c["resourceRegistry"]
                       for resource in specialty):
            return False, "specialty must list up to 4 known resources"
        preferred = role.get("preferredProject")
        projects = c["projectRegistry"]
        preferred_values = preferred if isinstance(preferred, list) else [preferred]
        if not preferred_values or len(preferred_values) > 4 \
                or any(not isinstance(project, str) or project not in projects
                       for project in preferred_values):
            return False, "preferredProject must name 1-4 known project types"
        return True, None

    @staticmethod
    def _role_record(role):
        """Copy the proposal into the registry's seed-compatible shape."""
        preferred = role["preferredProject"]
        return {
            "name": role["name"].strip(),
            "skill": role["skill"].strip(),
            "specialty": list(role["specialty"]),
            "preferredProject": list(preferred) if isinstance(preferred, list) else preferred,
        }

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

    def _pick_contribution_resource(self, agent, decision, district_id=None):
        district_id = district_id or self._resolve_contribution_district(agent, decision.get("target_district"))
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return None
        target = decision.get("target")
        if target and target in self.civilization["resourceRegistry"]:
            if agent["resources"].get(target, 0) > 0 and p["contributed"].get(target, 0) < p["needs"].get(target, 0):
                return target
        # Sid-parity Phase 2: an enacted priority rule biases contributions
        # toward its named resource (mirrors harvest_spirit edible bias).
        priority_res = self._active_priority_resource(agent)
        if priority_res:
            need = p["needs"].get(priority_res, 0)
            have = p["contributed"].get(priority_res, 0)
            if need > have and agent["resources"].get(priority_res, 0) > 0:
                return priority_res
        # Phase G belief-driven bias (deterministic, no new action): a
        # harvest_spirit believer prefers contributing an EDIBLE resource the
        # project still needs, ahead of the generic need-order scan below --
        # "beliefs influence a deterministic bias" from the plan, at zero
        # token cost (it reads the existing beliefs set, no new prompt line).
        if CULTURE_ENABLED and HARVEST_SPIRIT_CONTRIB_BOOST and MEME_SEED_ID in agent.get("beliefs", ()):
            for res in EDIBLE_RESOURCES:
                need = p["needs"].get(res, 0)
                have = p["contributed"].get(res, 0)
                if need > have and agent["resources"].get(res, 0) > 0:
                    return res
        for res in p["needs"]:
            need = p["needs"].get(res, 0)
            have = p["contributed"].get(res, 0)
            if need > have and agent["resources"].get(res, 0) > 0:
                return res
        return None

    # --- memes ---
    def _belief_registry(self, agent=None, settlement_id=None):
        """Live seed + authored beliefs; old saves gain seed records lazily."""
        c = self.civilization
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            registry = self._registry_for_settlement(sid)
            if not isinstance(registry, dict):
                registry = {}
                if sid == self._primary_settlement_id():
                    c["beliefRegistry"] = registry
                else:
                    c.setdefault("beliefRegistryBySettlement", {})[sid] = registry
        else:
            registry = c.get("beliefRegistry")
            if not isinstance(registry, dict):
                registry = {}
                c["beliefRegistry"] = registry
        for bid, tenet in MEMES.items():
            registry.setdefault(bid, {
                "id": bid, "name": bid.replace("_", " ").title(),
                "tenet": tenet, "affinity": sorted(MEME_RULE_AFFINITY.get(bid, set())),
                "authoredBy": None, "createdFrame": 0, "seed": True,
            })
        return registry

    def _belief_entry(self, belief_id, agent=None):
        return self._belief_registry(agent).get(belief_id) or {}

    def _belief_name(self, belief_id, agent=None):
        return self._belief_entry(belief_id, agent).get("name") or belief_id.replace("_", " ").title()

    def _belief_text(self, bid, agent=None):
        entry = self._belief_entry(bid, agent)
        if entry.get("tenet"):
            return entry["tenet"]
        # Keep legacy mutation overrides readable when restoring an old state.
        if CULTURE_ENABLED:
            if SCHISM_ENABLED and agent:
                override = self._meme_texts_for_settlement(self._settlement_id_for_agent(agent)).get(bid)
            else:
                override = self.civilization.get("memeTexts", {}).get(bid)
            if override:
                return override
        return MEMES.get(bid, bid)

    def _seed_beliefs(self):
        """Seed two competing memes on different living agents (Sid-parity
        Phase 3). Falls back to a single seed if the roster is too small."""
        if not MEMES_ENABLED or not self.agents:
            return
        living = [a for a in self.agents if a.get("deathFrame") is None] or list(self.agents)
        random.shuffle(living)
        origins = living[:2] if len(living) >= 2 else living[:1]
        for agent, meme_id in zip(origins, MEME_SEED_IDS):
            agent["beliefs"].add(meme_id)
            self._push_activity(
                f'{agent["name"]} began spreading a rumor: "{self._belief_text(meme_id)}"')
            self._push_communication("rumor", agent["name"], "everyone",
                                     self._belief_text(meme_id))
            self._push_memory(agent, f"I believe: {self._belief_text(meme_id)}")

    def _belief_favored_kinds(self, agent):
        favored = set()
        for bid in agent.get("beliefs") or ():
            affinity = self._belief_entry(bid, agent).get("affinity")
            favored |= set(affinity if isinstance(affinity, list) else MEME_RULE_AFFINITY.get(bid, set()))
        return favored

    def _belief_biased_vote(self, agent, pending):
        """Return yes/no biased by the voter's beliefs, or None for no bias.
        harvest_spirit believers favor food-protective rules; river_spirit
        believers favor priority rules and lean against heavy rationing."""
        if not MEMES_ENABLED or not pending:
            return None
        kind = pending.get("kind")
        beliefs = agent.get("beliefs") or set()
        favored = self._belief_favored_kinds(agent)
        if kind in favored:
            return "yes"
        if MEME_RIVAL_ID in beliefs and kind in ("rationing", "harvest_quota"):
            return "no"
        if MEME_SEED_ID in beliefs and kind == "priority" and pending.get("value") == "fish":
            return "no"
        return None

    def _found_belief(self, agent, belief):
        if not MEMES_ENABLED:
            return f"{agent['name']} cannot found a belief while culture is disabled"
        if not isinstance(belief, dict):
            return f"{agent['name']} did not provide a belief"
        belief_id, name, tenet, affinity = (belief.get("id"), belief.get("name"),
                                             belief.get("tenet"), belief.get("affinity"))
        registry = self._belief_registry(agent)
        if not isinstance(belief_id, str) or not self.SLUG_RE.match(belief_id):
            return f"{agent['name']} proposed an invalid belief id"
        if belief_id in registry:
            return f"{agent['name']} cannot found {belief_id} — it already exists"
        if len(registry) >= MAX_BELIEFS:
            return f"{agent['name']} cannot found another belief — the village has reached its belief limit"
        if not isinstance(name, str) or not (1 <= len(name.strip()) <= 32):
            return f"{agent['name']} proposed an invalid belief name"
        if not isinstance(tenet, str) or not (8 <= len(tenet.strip()) <= 160) or "\n" in tenet:
            return f"{agent['name']} proposed an invalid belief tenet"
        if not isinstance(affinity, list) or not affinity or len(affinity) > len(RULE_KINDS) \
                or any(not isinstance(kind, str) for kind in affinity) \
                or len(set(affinity)) != len(affinity) or not set(affinity).issubset(RULE_KINDS):
            return f"{agent['name']} proposed an invalid belief affinity"
        registry[belief_id] = {
            "id": belief_id, "name": name.strip(), "tenet": tenet.strip(),
            "affinity": list(affinity), "authoredBy": agent["name"],
            "createdFrame": self.frameTick, "seed": False,
        }
        agent["beliefs"].add(belief_id)
        self._push_activity(f"{agent['name']} founded {name.strip()}: \"{tenet.strip()}\"")
        self._push_communication("belief_founded", agent["name"], "everyone", tenet.strip())
        self._push_memory(agent, f"Founded {name.strip()}: {tenet.strip()}")
        self._push_chronicle(f"{agent['name']} founded {name.strip()}", kind="belief_founded")
        return f"{agent['name']} founded {name.strip()}"

    def _adopt_belief(self, speaker, recipient, belief_id, quality, fallback=False):
        if not MEMES_ENABLED or not speaker or not recipient \
                or belief_id not in speaker.get("beliefs", set()) \
                or belief_id in recipient.get("beliefs", set()) \
                or recipient is speaker or recipient["incapacitated"]:
            return None
        recipient["beliefs"].add(belief_id)
        self._nudge_ally(speaker, recipient["name"])
        self._nudge_ally(recipient, speaker["name"])
        if CULTURE_ENABLED:
            self._maybe_mutate_meme(belief_id, speaker, recipient)
        source = "deterministic fallback" if fallback else f"pitch quality {quality:.2f}"
        self._push_activity(f'{recipient["name"]} adopted {self._belief_name(belief_id)} from {speaker["name"]} ({source})')
        self._push_communication("belief", speaker["name"], recipient["name"], self._belief_text(belief_id))
        self._push_memory(recipient, f"Came to believe {self._belief_name(belief_id)}: {self._belief_text(belief_id)}")
        self._push_chronicle(f'{recipient["name"]} adopted {self._belief_name(belief_id)}', kind="belief_adoption")
        return belief_id

    def _belief_conversion_probability(self, speaker, recipient, quality):
        left = BELIEF_RELATIONSHIP_WEIGHT.get(self._relationship_between(speaker, recipient["name"]), 0.68)
        right = BELIEF_RELATIONSHIP_WEIGHT.get(self._relationship_between(recipient, speaker["name"]), 0.68)
        probability = 0.08 + (0.70 * max(0.0, min(1.0, quality)) * ((left + right) / 2.0))
        if recipient.get("beliefs"):
            probability *= BELIEF_EXISTING_PENALTY
        return max(0.02, min(0.88, probability))

    def _deterministic_belief_roll(self, speaker, recipient, belief_id):
        material = f"{self.frameTick}|{speaker['name']}|{recipient['name']}|{belief_id}"
        return (sum((idx + 1) * ord(ch) for idx, ch in enumerate(material)) % 1000) / 1000.0

    def _maybe_spread_beliefs(self, agent, recipient_name, message, belief_pitch=None,
                              judged_quality=None, model_scored=False):
        if not MEMES_ENABLED or not recipient_name or recipient_name == "everyone" \
                or not isinstance(belief_pitch, dict):
            return
        recipient = self._find_agent(recipient_name)
        belief_id = belief_pitch.get("belief_id")
        pitch_text = belief_pitch.get("pitch")
        # Count an actual returned model score before checking whether the
        # pair remained adjacent while the decision was in flight. Otherwise
        # rapid movement could spend unbounded scores that never reach the
        # conversion branch. The server never requests a score for a target
        # absent from the original nearby payload.
        use_model = (model_scored and isinstance(judged_quality, (int, float))
                     and not isinstance(judged_quality, bool) and 0.0 <= judged_quality <= 1.0
                     and self.civilization.get("beliefPitchCalls", 0) < BELIEF_PITCH_SESSION_CAP)
        if use_model:
            self.civilization["beliefPitchCalls"] = self.civilization.get("beliefPitchCalls", 0) + 1
        # `talk_to_nearby` preserves its historical move-and-deliver behavior
        # for a named distant target. Belief persuasion is stricter: it is an
        # adjacent conversation only, never a remote conversion while walking.
        if not recipient or self._distance_to(agent, recipient) > 80 \
                or belief_id not in agent.get("beliefs", set()) \
                or belief_id in recipient.get("beliefs", set()) \
                or not isinstance(pitch_text, str) or not (4 <= len(pitch_text.strip()) <= 240):
            return
        quality = float(judged_quality) if use_model else BELIEF_FALLBACK_QUALITY
        if self._deterministic_belief_roll(agent, recipient, belief_id) \
                <= self._belief_conversion_probability(agent, recipient, quality):
            self._adopt_belief(agent, recipient, belief_id, quality, fallback=not use_model)

    def _maybe_form_commitment(self, agent, recipient_name, message):
        """Consequential conversations (#5.4): talk stops being purely
        advisory -- a request naming a known resource creates a commitment
        on the recipient. One commitment per agent; a new one overwrites
        the old. Honored/cleared in apply_decision's post-action bookkeeping."""
        if not recipient_name or recipient_name == "everyone":
            return
        recipient = self._find_agent(recipient_name)
        if not recipient or recipient is agent:
            return
        text_lower = message.lower()
        matched = next((rid for rid in self.civilization["resourceRegistry"] if rid in text_lower), None)
        if not matched:
            return
        recipient["commitment"] = {"to": agent["name"], "text": message,
                                   "madeAt": self.frameTick, "resource": matched}

    def _spread_beliefs_by_proximity(self):
        """Retained tick hook: adjacency creates a pitch opportunity only.
        A belief changes hands exclusively through talk_to_nearby's explicit
        belief_pitch payload, never through a background probability roll."""
        return

    def _meme_adoption_counts(self):
        """Per-meme living-agent adoption counts (Sid-parity Phase 3)."""
        if not MEMES_ENABLED:
            return {}
        living = [a for a in self.agents if a.get("deathFrame") is None]
        counts = {mid: 0 for mid in self._belief_registry()}
        for a in living:
            for bid in a.get("beliefs") or ():
                if bid in counts:
                    counts[bid] += 1
        return counts

    def _meme_adoption_count(self):
        """Total living agents holding one or more live beliefs."""
        if not MEMES_ENABLED:
            return 0
        living = [a for a in self.agents if a.get("deathFrame") is None]
        return len([a for a in living if a.get("beliefs")])

    def _maybe_mutate_meme(self, belief_id, speaker, recipient):
        """Event-driven, capped mutation of a belief's text on spread (#3).
        Exactly one lm_complete call per mutation attempt, itself gated by a
        low probability AND a hard session-lifetime cap
        (MEME_MUTATION_SESSION_CAP) so a long soak can never turn ordinary
        proximity chatter into a background LLM-spam loop -- the same
        discipline as Phase F's one-call-per-birth. A failed/empty call is a
        silent no-op (the belief keeps its prior text), never a blocker."""
        c = self.civilization
        if random.random() > MEME_MUTATION_PROB:
            return
        if c.get("memeMutations", 0) >= MEME_MUTATION_SESSION_CAP:
            return
        current_text = self._belief_text(belief_id, speaker)
        try:
            # Few-shot form: a thinking-class model (qwen) measured live to be
            # unreliable at following an abstract "reword this, don't explain"
            # instruction -- it kept emitting meta-commentary about the task
            # instead of doing it, even with generous max_tokens. Worked
            # examples of INPUT/OUTPUT pairs are more reliable at constraining
            # a small/thinking model to plain output than instructions alone.
            mutated = self.d["lm_complete"](
                "Rewrite a village rumor with slightly different wording, same "
                "meaning, under 15 words. Reply with the rewritten sentence only.",
                "Input: The river spirit blesses fishers at dawn.\n"
                "Output: Fishers who rise at dawn are blessed by the river spirit.\n\n"
                "Input: Strangers from the hills bring bad luck.\n"
                "Output: Bad luck follows strangers who come from the hills.\n\n"
                f"Input: {current_text}\n"
                "Output:",
                max_tokens=120, temperature=0.7,
            )
        except Exception:
            mutated = None
        if not mutated:
            return
        mutated = mutated.split("\n\n")[0].strip().strip('"').strip()
        # A thinking-class model (qwen, measured live) frequently prefixes a
        # one-or-two-word analysis label before the actual answer ("Subject:
        # ...", "Meaning: ...", "Object/Condition: ..."). Strip a single
        # leading "<ShortLabel>:" rather than reject the whole response --
        # the content after the colon is usually the real rewrite.
        label_match = re.match(r"^[A-Za-z][A-Za-z /]{0,24}:\s*", mutated)
        if label_match:
            mutated = mutated[label_match.end():].strip().strip('"').strip()
        mutated = mutated[:120]
        # Reject a remaining instruction-echo/meta-commentary/few-shot-repeat
        # leak (a known failure mode of thinking-class models -- lm_complete's
        # own is_scaffold_text already screens the raw response, but a
        # *clean-looking* single sentence can still be the model talking
        # ABOUT the task, or echoing the worked examples, rather than
        # producing a new one). Treat either signal exactly like an empty
        # response: a silent no-op, never a corrupted belief.
        low = mutated.lower()
        looks_meta = any(w in low for w in
                         ("context hint", "reword", "rumor sentence", "task:",
                          "instruction", "i should", "the model", "as an ai",
                          "input", "output:", "sentence:", "example", "generate"))
        has_words = bool(re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", mutated))
        if not mutated or mutated == current_text or looks_meta or not has_words \
                or self.d["is_scaffold_text"](mutated):
            return
        entry = self._belief_entry(belief_id, speaker)
        if entry.get("tenet"):
            entry.setdefault("originalTenet", entry["tenet"])
            entry["tenet"] = mutated
        else:
            if SCHISM_ENABLED:
                self._meme_texts_for_settlement(self._settlement_id_for_agent(speaker))[belief_id] = mutated
            else:
                c.setdefault("memeTexts", {})[belief_id] = mutated
        c["memeMutations"] = c.get("memeMutations", 0) + 1
        self._push_activity(f'The belief "{current_text}" drifted into "{mutated}" as it spread through the village.')
        self._push_chronicle(f'A belief mutated: "{mutated}"', kind="meme_mutation")

    # --- Phase G: skills by practice + teaching (CULTURE_ENABLED) ---
    def _skill_level(self, agent, kind):
        if not CULTURE_ENABLED:
            return 0.0
        return (agent.get("skills") or {}).get(kind, 0.0)

    def _skill_bonus(self, agent, kind):
        """Integer yield/output bonus from a practiced skill -- +1 per
        SKILL_BONUS_DIVISOR levels, so early practice is legible but the cap
        (SKILL_MAX_LEVEL / SKILL_BONUS_DIVISOR, e.g. 2 at defaults) stays
        modest next to structure-effect bonuses."""
        if not CULTURE_ENABLED:
            return 0
        return int(self._skill_level(agent, kind) // SKILL_BONUS_DIVISOR)

    def _practice_skill(self, agent, kind):
        """Deterministic practice-raises-skill (#1): every successful use of
        a practiced verb nudges that skill up by a fixed amount, capped at
        SKILL_MAX_LEVEL. Called from the existing success paths of
        gather/craft/build/heal -- no new tick, no new action."""
        if not CULTURE_ENABLED or kind not in SKILL_KINDS:
            return
        skills = agent.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        before = skills.get(kind, 0.0)
        skills[kind] = min(SKILL_MAX_LEVEL, before + SKILL_PRACTICE_GAIN)
        self.civilization["skillPracticeCount"] = self.civilization.get("skillPracticeCount", 0) + 1

    def _maybe_teach(self, teacher, recipient_name, message):
        """Teaching (#1 apprenticeship): a talk_to_nearby message containing
        a teach-intent keyword (TEACH_KEYWORDS) and a recognized skill kind
        transfers TEACH_TRANSFER_FRACTION of the teacher's level in that
        skill to the recipient -- deterministic keyword check, no extra LLM
        call, no new action verb (the plan's change-map hint). No silent
        rejection: a failed match is simply not a teaching event (the talk
        still lands as ordinary conversation)."""
        if not CULTURE_ENABLED or not message or not recipient_name or recipient_name == "everyone":
            return
        text_lower = message.lower()
        if not any(kw in text_lower for kw in TEACH_KEYWORDS):
            return
        recipient = self._find_agent(recipient_name)
        if not recipient or recipient is teacher or recipient["incapacitated"]:
            return
        skill_kind = next((k for k in SKILL_KINDS if k in text_lower), None)
        if not skill_kind:
            # No specific skill named -- teach whichever the teacher is best at.
            teacher_skills = teacher.get("skills") or {}
            skill_kind = max(SKILL_KINDS, key=lambda k: teacher_skills.get(k, 0.0))
        teacher_level = self._skill_level(teacher, skill_kind)
        if teacher_level <= 0:
            return
        recipient_skills = recipient.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        transfer = teacher_level * TEACH_TRANSFER_FRACTION
        before = recipient_skills.get(skill_kind, 0.0)
        recipient_skills[skill_kind] = min(SKILL_MAX_LEVEL, before + transfer)
        if recipient_skills[skill_kind] <= before:
            return
        teacher["lastTeachFrame"] = self.frameTick
        self.civilization["teachCount"] = self.civilization.get("teachCount", 0) + 1
        self._push_activity(f"{teacher['name']} taught {recipient['name']} some {skill_kind} skill.")
        self._push_memory(recipient, f"Learned {skill_kind} from {teacher['name']}")

    # --- Phase G: library knowledge persistence (CULTURE_ENABLED) ---
    def _library_active(self, district_id=None):
        if not CULTURE_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        return self._working_structure_count("library", district_id) > 0

    def _library_upgrade_weight(self, district_id, cap=10):
        """Best local working Library's bounded upgrade contribution. `cap`
        defaults to 10 for knowledge-capacity scaling; study-gain callers pass
        LIBRARY_STUDY_WEIGHT_CAP (5) to keep skill-by-study bounded."""
        if not LIBRARY_SCALING_ENABLED:
            return 1
        libraries = [s for s in self.civilization["structures"]
                     if s.get("type") == "library"
                     and (district_id is None or s.get("districtId") == district_id)
                     and not s.get("isRuin")
                     and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD]
        return min(cap, max((self._structure_upgrade_weight(s) for s in libraries), default=1))

    def _library_lessons(self, district_id):
        if not self._library_active(district_id):
            return None
        knowledge = sorted(self.civilization.get("libraryKnowledge") or [],
                           key=lambda k: k.get("level", 0), reverse=True)[:3]
        chronicle = (self.civilization.get("chronicle") or [])[-2:]
        parts = [f"{k.get('skill')} {k.get('level')} ({k.get('agent')})" for k in knowledge]
        parts.extend(str(c.get("text", "")) for c in chronicle)
        return " | ".join(parts)[:480] or None

    def _store_knowledge_on_death(self, agent):
        """Library (#2): while a working Library exists, a dying agent's
        single best (non-trivial) skill is preserved in
        civilization["libraryKnowledge"] so it remains learnable via
        _study_at_library even though the agent is gone -- "death matters
        without erasing progress". Capped (LIBRARY_KNOWLEDGE_CAP): the
        weakest stored entry retires first, the same discipline as blueprint/
        custom-resource retirement elsewhere in the file, so a long soak
        can't grow this list forever."""
        if not CULTURE_ENABLED or not self._library_active():
            return
        skills = agent.get("skills") or {}
        best_kind = max(SKILL_KINDS, key=lambda k: skills.get(k, 0.0), default=None)
        if not best_kind or skills.get(best_kind, 0.0) < SKILL_PRACTICE_GAIN:
            return
        c = self.civilization
        knowledge = c.setdefault("libraryKnowledge", [])
        knowledge.append({"agent": agent["name"], "skill": best_kind,
                          "level": round(skills[best_kind], 2), "frame": self.frameTick})
        cap = LIBRARY_KNOWLEDGE_CAP * self._library_upgrade_weight(None)
        while len(knowledge) > cap:
            weakest = min(range(len(knowledge)), key=lambda i: knowledge[i]["level"])
            knowledge.pop(weakest)
        self._push_activity(f"{agent['name']}'s knowledge of {best_kind} is preserved in the Library.")
        self._push_chronicle(f"{agent['name']}'s {best_kind} knowledge was preserved in the Library.",
                             kind="knowledge_preserved")

    def _maybe_study_at_library(self):
        """Deterministic backstop (#2, the "study there via a goal" idiom
        without a new decision action/schema field): any living agent
        currently standing in a district with a working Library who has room
        to learn a stored skill studies it for free, tick-gated like every
        other _maybe_* backstop. A newcomer/child naturally has the most
        headroom (skills start at 0), so this is exactly the mechanism by
        which death stops being total knowledge loss."""
        if not CULTURE_ENABLED or not self._library_active():
            return
        library_districts = {s.get("districtId") for s in self.civilization["structures"]
                             if s.get("type") == "library" and not s.get("isRuin")
                             and (not GOODS_ENABLED or s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)}
        for agent in self.agents:
            if agent.get("deathFrame") is not None or agent["incapacitated"]:
                continue
            if agent.get("currentDistrict") not in library_districts:
                continue
            summary = self._study_at_library(agent)
            if summary:
                self._push_activity(summary)
                self._push_memory(agent, summary)

    def _study_at_library(self, agent):
        """A living agent studying at a working Library gains
        LIBRARY_STUDY_GAIN toward the strongest stored skill they don't
        already exceed -- the mechanism by which a newcomer/child can still
        learn a dead specialist's craft. Returns a summary string, or None if
        there's nothing to study (deterministic escape: no knowledge stored
        yet, or the agent already exceeds every stored entry)."""
        if not CULTURE_ENABLED or not self._library_active():
            return None
        knowledge = self.civilization.get("libraryKnowledge") or []
        if not knowledge:
            return None
        agent_skills = agent.setdefault("skills", {k: 0.0 for k in SKILL_KINDS})
        best = max(knowledge, key=lambda k: k["level"]
                   if k["level"] > agent_skills.get(k["skill"], 0.0) else -1)
        if best["level"] <= agent_skills.get(best["skill"], 0.0):
            return None
        before = agent_skills.get(best["skill"], 0.0)
        gain = LIBRARY_STUDY_GAIN * self._library_upgrade_weight(
            agent.get("currentDistrict"), cap=LIBRARY_STUDY_WEIGHT_CAP)
        agent_skills[best["skill"]] = min(SKILL_MAX_LEVEL, before + gain)
        return f"{agent['name']} studied {best['skill']} at the Library (from {best['agent']}'s preserved knowledge)"

    # --- Phase G: chronicle (CULTURE_ENABLED) ---
    def _push_chronicle(self, text, kind="event", source=None, presentation=None):
        """Village-level ring of major events (#3), STORED in civilization
        state (not just activity.jsonl) so it survives restarts and can be
        summarized into prompts. Capped at CHRONICLE_CAP; oldest drops first.

        source is additive (default None, byte-identical entries for every
        pre-existing caller) -- Sovereign God mode Phase 2 passes
        source="divine" for explicit non-emergent attribution.
        presentation is cosmetic-only (Phase 5 Voice): stored when thunder so
        the viewer can style chronicle rows; never injected into prompts."""
        if not CULTURE_ENABLED:
            return
        chronicle = self.civilization.setdefault("chronicle", [])
        entry = {"text": text, "frame": self.frameTick, "kind": kind}
        if source:
            entry["source"] = source
        if presentation == "thunder":
            entry["presentation"] = "thunder"
        chronicle.append(entry)
        if len(chronicle) > CHRONICLE_CAP:
            del chronicle[:-CHRONICLE_CAP]

    def _chronicle_prompt_line(self):
        """Compact 'Village history: ...' line folding the most recent
        CHRONICLE_PROMPT_ENTRIES entries -- the whole reason the chronicle is
        stored rather than just logged (the civilization test needs it
        legible to the LLM, not just to a human reading activity.jsonl)."""
        if not CULTURE_ENABLED:
            return None
        chronicle = self.civilization.get("chronicle") or []
        if not chronicle:
            return None
        recent = chronicle[-CHRONICLE_PROMPT_ENTRIES:]
        return "; ".join(e["text"] for e in recent)

    # --- Testament (TESTAMENT_ENABLED + WIKI_MEMORY prerequisite) ---
    def _push_testament_entry(self, text, author, generation):
        """Append one attributed lesson line to civilization["testament"],
        deduplicated by text and capped at TESTAMENT_CAP (oldest drops first)."""
        if not TESTAMENT_ENABLED:
            return
        text = (text or "").strip()[:WIKI_SECTION_CHAR_CAP]
        if not text:
            return
        c = self.civilization
        testament = c.setdefault("testament", [])
        norm = text.lower()
        for entry in testament:
            if (entry.get("text") or "").strip().lower() == norm:
                return
        testament.append({
            "text": text,
            "author": author,
            "frame": self.frameTick,
            "generation": generation,
        })
        c["testamentAuthored"] = c.get("testamentAuthored", 0) + 1
        if len(testament) > TESTAMENT_CAP:
            del testament[:-TESTAMENT_CAP]

    def _merge_testament_on_death(self, agent):
        """Deterministic deathbed fold of memoryWiki into the testament ring.
        No LLM call — skips when wiki sections are empty."""
        if not TESTAMENT_ENABLED or not WIKI_MEMORY:
            return
        wiki = agent.get("memoryWiki") or {}
        lessons = (wiki.get("lessons") or "").strip()
        relationships = (wiki.get("relationships") or "").strip()
        if not lessons and not relationships:
            return
        generation = self.civilization.get("births", 0)
        if lessons:
            self._push_testament_entry(lessons, agent["name"], generation)
        if relationships:
            self._push_testament_entry(relationships, agent["name"], generation)

    def _testament_prompt_line(self):
        """Compact 'Village testament: ...' slice for think payloads."""
        if not TESTAMENT_ENABLED:
            return None
        testament = self.civilization.get("testament") or []
        if not testament:
            return None
        recent = testament[-TESTAMENT_PROMPT_ENTRIES:]
        parts = []
        for entry in recent:
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            author = entry.get("author") or "?"
            parts.append(f"{author}: {text}")
        return "; ".join(parts) if parts else None

    def _seed_newborn_wiki_from_testament(self, newborn, parent_a, parent_b):
        """Inherit parent wiki sections plus the newest testament entries,
        each capped at WIKI_SECTION_CHAR_CAP."""
        if not TESTAMENT_ENABLED or not WIKI_MEMORY:
            return
        wiki = newborn.setdefault("memoryWiki", {
            "relationships": "", "goals": "", "lessons": "",
        })
        pa_wiki = parent_a.get("memoryWiki") or {}
        pb_wiki = parent_b.get("memoryWiki") or {}
        testament = self.civilization.get("testament") or []

        def _join_unique(*parts):
            seen = set()
            kept = []
            for part in parts:
                val = (part or "").strip()
                if not val:
                    continue
                key = val.lower()
                if key in seen:
                    continue
                seen.add(key)
                kept.append(val)
            return " | ".join(kept)[:WIKI_SECTION_CHAR_CAP]

        testament_texts = [
            (e.get("text") or "").strip()
            for e in testament[-TESTAMENT_PROMPT_ENTRIES:]
            if (e.get("text") or "").strip()
        ]
        wiki["lessons"] = _join_unique(
            pa_wiki.get("lessons"), pb_wiki.get("lessons"), *testament_texts)
        wiki["relationships"] = _join_unique(
            pa_wiki.get("relationships"), pb_wiki.get("relationships"))
        wiki["goals"] = _join_unique(pa_wiki.get("goals"), pb_wiki.get("goals"))

    def _council_digest_prompt_line(self):
        """Bounded newest-first Daily Council continuity for every agent."""
        digests = self.civilization.get("councilDigests") or []
        if not digests:
            return None
        lines = []
        for digest in digests[:COUNCIL_DIGEST_PROMPT_ENTRIES]:
            topics = ",".join((digest.get("topics") or [])[:4]) or "general"
            verdict = digest.get("verdict") or {}
            outcome = verdict.get("outcome") or "unresolved"
            mood = digest.get("mood") or "not recorded"
            lines.append(
                f"day {digest.get('day')}: {topics}; {outcome}; mood {mood}"
            )
        return " | ".join(lines)

    # --- Phase G: personality drift (CULTURE_ENABLED) ---
    def _drift_personality(self, agent, trait):
        """Major life events append one short deterministic trait clause to
        the agent's persona (#4) -- persona already flows into the prompt's
        personality line at zero extra template cost, matching Phase F's
        life-stage fold-in. Capped (PERSONALITY_DRIFT_CAP) so a long-lived
        elder's persona string can't grow without bound; a new trait bumps
        out the oldest once at the cap."""
        if not CULTURE_ENABLED or not trait:
            return
        traits = agent.setdefault("personalityTraits", [])
        if trait in traits:
            return
        traits.append(trait)
        if len(traits) > PERSONALITY_DRIFT_CAP:
            del traits[:-PERSONALITY_DRIFT_CAP]

    def _personality_with_drift(self, agent):
        """Folds drift traits into the existing personality string at build
        time -- no new prompt template line (matching Phase F's life-stage
        fold-in), so flag-off/no-drift-yet prompts render byte-identically
        to the base personality text."""
        base = agent.get("personality") or ""
        if not CULTURE_ENABLED:
            return base
        traits = agent.get("personalityTraits") or []
        if not traits:
            return base
        return f"{base}, {', '.join(traits)}" if base else ", ".join(traits)

