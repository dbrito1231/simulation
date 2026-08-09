"""Phase 6d mixin: crafting + rules/voting slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_is_idle` through
`_enforce_resource_tax` (formerly core.py lines ~2634-3413). Covers:
idle-agent/task helpers, crafting (`_has_inputs`, `_craft_item`, custom
recipe validation/proposal/review), and rules/voting machinery (quorum,
custom rule-effect normalization, constitution bookkeeping, rule validation,
enactment/repeal, proposal/repeal/vote decisions, priority-resource and
resource-tax enforcement).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _CraftingRulesMixin:
    """Mixin slice of SimEngine: idle/task helpers, crafting, and
    rules/voting. See module docstring for exact scope."""

    def _is_idle(self, agent):
        return agent["role"] != "elder" and (
            agent["lastAction"] is None or agent["lastAction"] == "rest"
            or agent.get("idleCycles", 0) >= 2)

    def _idle_agents_for_elder(self):
        # Re-task cooldown: an agent tasked recently isn't offered to the
        # elder again, so the MAIN RULE can't spend every elder turn
        # re-announcing directives at the same villagers (the 2026-07-02
        # session logged 1,556 elder directives vs 19 villager speeches).
        # Phase F: incapacitated is no longer always transient (a dead agent
        # stays incapacitated forever), so this must exclude it explicitly --
        # otherwise a deceased villager could sit in the elder's idle list
        # indefinitely and get assign_task'd to a corpse every gate.
        idle = [a for a in self.agents if a.get("deathFrame") is None and not a["incapacitated"]
                and self._is_idle(a)
                and (a["lastTaskedFrame"] is None
                     or self.frameTick - a["lastTaskedFrame"] > ELDER_RETASK_COOLDOWN_FRAMES)]
        idle.sort(key=lambda a: (a["lastTaskedFrame"] if a["lastTaskedFrame"] is not None
                                 else float("-inf")))
        return idle

    def _task_for_agent(self, agent):
        c = self.civilization
        district_id = self._resolve_contribution_district(agent)
        ap = c["districtProjects"].get(district_id) if district_id else None
        if ap:
            lacking = next((res for res in ap["needs"]
                            if ap["contributed"].get(res, 0) < ap["needs"][res]), None)
            if lacking:
                return f"gather or contribute {lacking} to the {ap['name']} in {district_id}"
            return f"help finish the {ap['name']} in {district_id}"
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
        # Feasibility gates run BEFORE any travel: routing an agent to a
        # station district that can't serve them (no Workshop/Kiln built,
        # tier-gated recipe, missing inputs) just produces a useless commute.
        # Workshop-station recipes need a physical Workshop somewhere in the
        # village (structures of type "workshop" are placed in village-kind
        # districts, so this is a village-wide check, not a per-district one).
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop" \
                and not self._craft_station_unlocked("workshop"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Workshop built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe_id in ("charcoal", "copper_ingot", "iron_ingot") \
                and not self._craft_station_unlocked("kiln"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Kiln built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe_id == "iron_pick" \
                and not self._craft_station_unlocked("foundry"):
            agent["lastCraftRejection"] = {"reason": "requires a working Foundry", "frame": self.frameTick}
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Foundry built yet"
        if path1_on("INDUSTRY_ENABLED") and recipe.get("station") == "kiln" \
                and not self._craft_station_unlocked("kiln"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Kiln built yet"
        if path1_on("TIER3_CONTENT_ENABLED") and recipe.get("station") == "foundry" \
                and not self._craft_station_unlocked("foundry"):
            return f"{agent['name']} cannot craft {recipe_id} -- the village has no Foundry built yet"
        if TECH_TREE_ENABLED:
            tier = recipe.get("tier", 1)
            village_tier = self._village_tech_tier()
            if isinstance(tier, int) and tier > village_tier:
                reason = self._tier_gate_reason(tier)
                agent["lastCraftRejection"] = {"reason": reason, "frame": self.frameTick}
                self._log_benchmark("tier_gate_rejection", tier,
                                    {"kind": "craft", "target": recipe_id,
                                     "village_tier": village_tier})
                return (f"{agent['name']} cannot craft {recipe_id} — it is tier {tier} "
                        f"tech and the village is tier {village_tier} ({reason})")
        if recipe.get("station") and agent["currentZone"] != recipe["station"]:
            self._set_agent_target_once(agent, recipe["station"])
            return f"{agent['name']} heads to the {recipe['station']} to craft {recipe_id}"
        if self._has_inputs(agent, recipe["inputs"]):
            for r, n in recipe["inputs"].items():
                agent["resources"][r] -= n
        else:
            paid, missing = self._pay_local_cost(agent, recipe["inputs"])
            if missing:
                self._craft_input_reflex(agent, recipe_id, recipe)
                missing_res = self._largest_missing_input(agent, recipe["inputs"])
                return f"{agent['name']} lacks {missing_res} to craft {recipe_id}"
        output = 1
        if STRUCTURE_EFFECTS_ENABLED and recipe.get("station") == "workshop":
            output += self._craft_output_bonus(recipe, agent.get("currentDistrict"))
        if CULTURE_ENABLED:
            output += self._skill_bonus(agent, "craft")
        output += self._custom_rule_modifier("craft_item", agent, recipe_id)
        agent["resources"][recipe_id] = agent["resources"].get(recipe_id, 0) + output
        agent["lastCraftRejection"] = None
        self.civilization["lastCraftActivityFrame"] = self.frameTick
        if CULTURE_ENABLED:
            self._practice_skill(agent, "craft")
        return f"{agent['name']} crafted {recipe_id}" \
            + (f" x{output} (well-equipped workshops)" if output > 1 else "")

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
        if TECH_TREE_ENABLED and isinstance(rc, dict):
            # Phase D: recipes may declare a tech tier (default 1). Declaring a
            # tier above the village's station-unlocked tier is refused with a
            # surfaced reason (the escape: build the tier's station first).
            tier = rc.get("tier", 1)
            if tier is not None and (isinstance(tier, bool) or not isinstance(tier, int)
                                     or not (1 <= tier <= MAX_TECH_TIER)):
                reason = f"recipe tier must be an integer 1-{MAX_TECH_TIER}"
                agent["lastRecipeRejection"] = {"reason": reason, "frame": self.frameTick}
                return f"{agent['name']} drafted an invalid recipe ({reason})"
            village_tier = self._village_tech_tier()
            if (tier or 1) > village_tier:
                reason = self._tier_gate_reason(tier)
                agent["lastRecipeRejection"] = {"reason": reason, "frame": self.frameTick}
                self._log_benchmark("tier_gate_rejection", tier,
                                    {"kind": "recipe", "target": rc.get("id"),
                                     "village_tier": village_tier})
                return f"{agent['name']}'s recipe {rc.get('id')} was refused — {reason}"
        if not self._validate_recipe(rc):
            return f"{agent['name']} drafted an invalid recipe"
        agent["lastRecipeRejection"] = None
        c["pendingRecipes"].append({
            "id": rc["id"], "name": rc["name"], "inputs": dict(rc["inputs"]),
            "station": rc.get("station"), "color": rc.get("color", "#BCAAA4"),
            "proposedBy": agent["name"],
            **({"tier": rc.get("tier") or 1} if TECH_TREE_ENABLED else {}),
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
        c.setdefault("customResourceAddedFrame", {})[rc["id"]] = self.frameTick
        self.RECIPES[rc["id"]] = {"name": rc["name"], "inputs": dict(rc["inputs"]), "station": rc["station"],
                                  **({"tier": rc.get("tier") or 1} if TECH_TREE_ENABLED else {})}
        c["lastCraftActivityFrame"] = self.frameTick
        return f"{agent['name']} approved the {rc['name']} recipe"

    # --- rules / voting ---
    def _active_agent_count(self):
        return len([a for a in self.agents if not a["incapacitated"]])

    def _vote_quorum(self, rule=None):
        if not SCHISM_ENABLED:
            return (self._active_agent_count() // 2) + 1
        if rule and self._is_global_governance_ballot(rule):
            return (self._active_agent_count() // 2) + 1
        sid = self._ballot_settlement_id(rule) if rule else self._primary_settlement_id()
        return (self._settlement_living_agent_count(sid) // 2) + 1

    def _normalize_custom_rule_effect(self, effect):
        """Return a canonical, safe custom-rule effect or None.

        The grammar deliberately has no expressions: one subject selector,
        an action plus optional context selectors, and a small additive
        modifier. Canonicalizing before it reaches persisted state makes the
        downstream lookup both deterministic and simple to audit.
        """
        c = self.civilization
        if not isinstance(effect, dict) or set(effect) != {"subject", "condition", "modifier"}:
            return None
        subject = effect.get("subject")
        condition = effect.get("condition")
        modifier = effect.get("modifier")
        if not isinstance(subject, dict) or len(subject) != 1 or not isinstance(condition, dict) \
                or not isinstance(modifier, dict):
            return None
        subject_kind, subject_value = next(iter(subject.items()))
        if subject_kind not in {"resource", "role", "district", "action"} \
                or not isinstance(subject_value, str):
            return None
        if subject_kind == "resource" and subject_value not in c["resourceRegistry"]:
            return None
        if subject_kind == "role" and subject_value not in c["roleRegistry"]:
            return None
        if subject_kind == "district" and subject_value not in c["districts"]:
            return None
        if subject_kind == "action" and subject_value not in CUSTOM_RULE_ACTIONS:
            return None
        if not set(condition).issubset({"action", "resource", "role", "district"}):
            return None
        action = condition.get("action") if subject_kind != "action" else subject_value
        if action not in CUSTOM_RULE_ACTIONS:
            return None
        if subject_kind == "action" and "action" in condition and condition["action"] != action:
            return None
        for selector, registry in (("resource", c["resourceRegistry"]),
                                   ("role", c["roleRegistry"]),
                                   ("district", c["districts"])):
            value = condition.get(selector)
            if value is not None and (not isinstance(value, str) or value not in registry):
                return None
        if set(modifier) != {"kind", "value"} or modifier.get("kind") != "add":
            return None
        value = modifier.get("value")
        if isinstance(value, bool) or not isinstance(value, int) \
                or not (1 <= value <= CUSTOM_RULE_MODIFIER_MAX):
            return None
        return {
            "subject": {subject_kind: subject_value},
            "condition": {key: condition[key] for key in ("action", "resource", "role", "district")
                          if key in condition},
            "modifier": {"kind": "add", "value": value},
        }

    def _custom_rule_modifier(self, action, agent, resource=None, district=None):
        """Total enacted custom-rule addition for one real action context."""
        if not RULES_ENABLED or action not in CUSTOM_RULE_ACTIONS:
            return 0
        total = 0
        district = district or agent.get("currentDistrict")
        if SCHISM_ENABLED and agent:
            modifiers = self._custom_modifiers_for_settlement(self._settlement_id_for_agent(agent))
        else:
            modifiers = self.civilization.get("customRuleModifiers") or {}
        for effect in modifiers.values():
            if not isinstance(effect, dict):
                continue
            subject = effect.get("subject") or {}
            condition = effect.get("condition") or {}
            if condition.get("action", action) != action:
                continue
            subject_kind, subject_value = next(iter(subject.items()), (None, None))
            if subject_kind == "resource" and subject_value != resource:
                continue
            if subject_kind == "role" and subject_value != agent.get("role"):
                continue
            if subject_kind == "district" and subject_value != district:
                continue
            if subject_kind == "action" and subject_value != action:
                continue
            if condition.get("resource") not in (None, resource):
                continue
            if condition.get("role") not in (None, agent.get("role")):
                continue
            if condition.get("district") not in (None, district):
                continue
            total += int((effect.get("modifier") or {}).get("value") or 0)
        return total

    def _constitution_provision(self, rule, status="active"):
        """Compact, viewer/prompt-safe historical record for one law."""
        provision = {
            "id": rule["id"], "name": rule.get("name") or rule["id"],
            "kind": rule.get("kind") or "custom",
            "description": rule.get("description") or "",
            "enactedFrame": rule.get("enactedFrame", self.frameTick),
            "status": status,
        }
        if rule.get("effect") is not None:
            provision["effect"] = rule["effect"]
        if rule.get("supersedes"):
            provision["supersedes"] = rule["supersedes"]
        return provision

    def _ensure_constitution(self, settlement_id=None):
        """Backfill missing active provisions without duplicating history."""
        c = self.civilization
        if SCHISM_ENABLED:
            sid = settlement_id or self._primary_settlement_id()
            active_rules = self._rules_for_settlement(sid)
            constitution = self._constitution_for_settlement(sid)
        else:
            sid = None
            active_rules = c.get("rules") or []
            constitution = c.get("constitution")
        if not isinstance(constitution, list):
            constitution = []
        # Rule ids are globally non-reusable. Keep the last duplicate so a
        # status update made before an old duplicate is cleaned cannot leave a
        # stale active provision behind; then restore chronological order.
        seen_ids = set()
        cleaned_reversed = []
        for provision in reversed(constitution):
            if not isinstance(provision, dict) or not isinstance(provision.get("id"), str):
                continue
            if provision["id"] in seen_ids:
                continue
            seen_ids.add(provision["id"])
            cleaned_reversed.append(provision)
        cleaned = list(reversed(cleaned_reversed))
        by_id = {p["id"]: p for p in cleaned}
        for rule in active_rules:
            if not isinstance(rule, dict) or not rule.get("id"):
                continue
            provision = by_id.get(rule["id"])
            if provision is None:
                provision = self._constitution_provision(rule)
                cleaned.append(provision)
                by_id[rule["id"]] = provision
            else:
                provision.setdefault("name", rule.get("name") or rule["id"])
                provision.setdefault("kind", rule.get("kind") or "custom")
                provision.setdefault("description", rule.get("description") or "")
                provision.setdefault("enactedFrame", rule.get("enactedFrame", 0))
                provision["status"] = "active"
        if len(cleaned) > MAX_CONSTITUTION_HISTORY:
            # Trim oldest inactive rows first (chronological order is
            # preserved since `cleaned` is already oldest-first); every
            # active provision is kept regardless of how far back it sits,
            # so a currently-enacted law is never silently dropped.
            overflow = len(cleaned) - MAX_CONSTITUTION_HISTORY
            trimmed = []
            for provision in cleaned:
                if overflow > 0 and provision.get("status") != "active":
                    overflow -= 1
                    continue
                trimmed.append(provision)
            cleaned = trimmed
        if SCHISM_ENABLED and sid:
            self._set_constitution_for_settlement(sid, cleaned)
        else:
            c["constitution"] = cleaned
        return cleaned

    def _set_constitution_status(self, rule_id, status, settlement_id=None, **extra):
        sid = settlement_id
        if SCHISM_ENABLED:
            sid = settlement_id or self._primary_settlement_id()
            constitution = self._ensure_constitution(sid)
        else:
            constitution = self._ensure_constitution()
        for provision in reversed(constitution):
            if provision.get("id") == rule_id:
                provision["status"] = status
                provision.update(extra)
                return

    def _rebuild_custom_rule_modifiers(self, settlement_id=None):
        """Restore-safe compilation of only currently enacted custom laws."""
        if SCHISM_ENABLED:
            sid = settlement_id or self._primary_settlement_id()
            rules = self._rules_for_settlement(sid)
            compiled = {}
            for rule in rules:
                if rule.get("kind") != "custom":
                    continue
                effect = self._normalize_custom_rule_effect(rule.get("effect"))
                if effect is not None:
                    rule["effect"] = effect
                    compiled[rule["id"]] = effect
            self._set_custom_modifiers_for_settlement(sid, compiled)
            return
        compiled = {}
        for rule in self.civilization.get("rules") or []:
            if rule.get("kind") != "custom":
                continue
            effect = self._normalize_custom_rule_effect(rule.get("effect"))
            if effect is not None:
                rule["effect"] = effect
                compiled[rule["id"]] = effect
        self.civilization["customRuleModifiers"] = compiled

    def _validate_rule(self, rule, agent=None):
        c = self.civilization
        if not RULES_ENABLED or not isinstance(rule, dict):
            return False
        if SCHISM_ENABLED:
            sid = self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id()
            pending_rules = self._pending_for_settlement(sid)
            active_rules = self._rules_for_settlement(sid)
        else:
            pending_rules = c["pendingRules"]
            active_rules = c["rules"]
        if len(pending_rules) >= MAX_PENDING_RULES:
            return False
        rid = rule.get("id")
        if not isinstance(rid, str) or not self.SLUG_RE.match(rid):
            return False
        if rid in self._all_enacted_rule_ids():
            return False
        if rid in self._all_pending_rule_ids():
            return False
        if rid in self._all_constitution_rule_ids():
            return False
        name = rule.get("name")
        if not isinstance(name, str) or not (1 <= len(name) <= 32):
            return False
        kind = rule.get("kind") or "custom"
        if kind not in RULE_KINDS:
            return False
        supersedes = rule.get("supersedes")
        target = None
        if supersedes is not None:
            if not isinstance(supersedes, str) or supersedes == rid:
                return False
            target = next((r for r in active_rules if r.get("id") == supersedes), None)
            if target is None:
                return False
        if len(active_rules) >= MAX_ACTIVE_RULES and target is None:
            return False
        if kind == "succession":
            # Succession ballots are created deterministically by
            # _start_succession_election on the elder's death, never by an
            # agent's propose_rule call -- keeps the election tamper-proof
            # (no one can nominate themselves mid-arc or spam candidacies).
            return False
        if kind == "resource_tax":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (0 <= v <= 3):
                return False
        if kind == "priority":
            # Sid-parity Phase 2: a priority rule biases contributions toward
            # a named resource. Value must be a known resource id.
            value = rule.get("value")
            if not isinstance(value, str) or value not in c["resourceRegistry"]:
                return False
        if LIFECYCLE_ENABLED and kind == "harvest_quota":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (1 <= v <= 20):
                return False
        if LIFECYCLE_ENABLED and kind == "rationing":
            try:
                v = float(rule.get("value"))
            except (TypeError, ValueError):
                return False
            if not (1 <= v <= RATIONING_WITHDRAW_CAP * 4):
                return False
        if kind == "custom" and rule.get("effect") is not None \
                and self._normalize_custom_rule_effect(rule.get("effect")) is None:
            return False
        if kind == "treaty":
            tariff = self._parse_treaty_tariff(rule.get("tariff", 0))
            if tariff is None:
                return False
        return True

    def _record_rule_kind_enacted(self, kind):
        c = self.civilization
        kinds = c.setdefault("ruleKindsEverEnacted", [])
        if kind and kind not in kinds:
            kinds.append(kind)

    def _tally_and_maybe_enact(self, rule):
        c = self.civilization
        rules_list, pending_list, scope_sid = self._governance_scope_lists(rule)
        votes = list(rule["votes"].values())
        yes = votes.count("yes")
        no = votes.count("no")
        quorum = self._vote_quorum(rule)
        if yes >= quorum:
            if rule.get("kind") == "repeal":
                rule["enacted"] = True
                pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
                c["lastRuleActivityFrame"] = self.frameTick
                return self._enact_repeal(rule, yes, scope_sid)
            if LIFECYCLE_ENABLED and rule["kind"] == "succession":
                rule["enacted"] = True
                pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
                c["lastRuleActivityFrame"] = self.frameTick
                # Succession ballots are a leadership record, not an ongoing
                # governance constraint -- they deliberately do NOT join
                # c["rules"] (which has a small MAX_ACTIVE_RULES budget shared
                # with resource_tax/harvest_quota/rationing/priority). Elder
                # deaths recur naturally over a long soak; letting every
                # succession permanently consume that budget would crowd out
                # real governance over time. activity.jsonl + the "succession"
                # benchmark are the permanent record instead.
                self._enact_succession_winner(rule)
            else:
                superseded = None
                if rule.get("supersedes"):
                    superseded = next((r for r in rules_list
                                       if r.get("id") == rule["supersedes"]), None)
                    if superseded is None:
                        pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
                        c["lastRuleActivityFrame"] = self.frameTick
                        self._push_activity(
                            f'Rule "{rule["name"]}" rejected: its amendment target is no longer active')
                        return "rejected"
                # A normal enact adds one rule; an amendment first removes one.
                # Re-check under the engine lock because other ballots may have
                # changed the active set after this proposal was validated.
                projected_rules = len(rules_list) - (1 if superseded is not None else 0)
                if projected_rules >= MAX_ACTIVE_RULES:
                    pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
                    c["lastRuleActivityFrame"] = self.frameTick
                    self._push_activity(
                        f'Rule "{rule["name"]}" rejected: the active-rule budget is full')
                    return "rejected"
                rule["enacted"] = True
                rule["enactedFrame"] = self.frameTick
                pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
                c["lastRuleActivityFrame"] = self.frameTick
                if superseded is not None:
                    # An amendment replaces an active provision atomically:
                    # reverse its effect exactly once before applying the new
                    # one, then retain only the historical constitution row.
                    rules_list[:] = [r for r in rules_list if r.get("id") != superseded["id"]]
                    self._clear_governance_rule(superseded, scope_sid)
                    self._set_constitution_status(
                        superseded["id"], "superseded", settlement_id=scope_sid,
                        supersededBy=rule["id"])
                rules_list.append(rule)
                # _ensure_constitution backfills the newly active rule once;
                # do not append a second provision after that migration pass.
                self._ensure_constitution(scope_sid if SCHISM_ENABLED else None)
                self._record_rule_kind_enacted(rule.get("kind"))
                self._push_activity(f'Rule "{rule["name"]}" enacted by vote ({yes} yes)')
                self._log_benchmark("rule_enacted", len(rules_list), {
                    "id": rule["id"], "yes": yes, "no": no, "kind": rule.get("kind")})
                self._apply_governance_rule(rule, scope_sid)
                # Living-ecosystem Phase 5: the emergency storm-rationing
                # auto-proposal (see _maybe_advance_rules) mints ids prefixed
                # "emerg_" -- id-prefix detection because _propose_rule's
                # pendingRules entry only whitelists id/name/kind/value/
                # description/proposedBy/enacted/votes(+effect/supersedes),
                # so an arbitrary extra key on the input rule dict would not
                # have survived to this enacted copy. Chronicle milestone so
                # the village's disaster response becomes recorded history,
                # same pattern as the Phase 1 disaster/district_founded kinds.
                if isinstance(rule.get("id"), str) and rule["id"].startswith("emerg_"):
                    self._push_chronicle(
                        f'The village enacted "{rule["name"]}" in response to storm-driven scarcity.',
                        kind="emergency_measure")
            return "enacted"
        if no >= quorum:
            pending_list[:] = [r for r in pending_list if r["id"] != rule["id"]]
            c["lastRuleActivityFrame"] = self.frameTick
            self._push_activity(f'Rule "{rule["name"]}" rejected by vote ({no} no)')
            return "rejected"
        return "pending"

    def _apply_governance_rule(self, rule, settlement_id=None):
        """Compile/apply an enacted law; repeal and amendment reverse it."""
        c = self.civilization
        sid = settlement_id
        if SCHISM_ENABLED and not self._is_global_governance_ballot(rule):
            sid = settlement_id or self._ballot_settlement_id(rule)
            harvest_quotas = self._harvest_quotas_for_settlement(sid)
            rationing_active = self._rationing_for_settlement(sid)
            custom_modifiers = self._custom_modifiers_for_settlement(sid)
        else:
            harvest_quotas = c.setdefault("harvestQuotas", {})
            rationing_active = c.setdefault("rationingActive", {})
            custom_modifiers = c.setdefault("customRuleModifiers", {})
        if rule["kind"] == "harvest_quota":
            try:
                value = int(float(rule.get("value")))
            except (TypeError, ValueError):
                value = HARVEST_QUOTA_PERIOD_FRAMES and 5
            harvest_quotas[rule["id"]] = {"value": max(1, value)}
            self._push_activity(f'Harvest quota "{rule["name"]}" now limits gathers to '
                                f'{max(1, value)} per resource per {HARVEST_QUOTA_PERIOD_FRAMES // 30}s per district')
        elif rule["kind"] == "rationing":
            try:
                value = int(float(rule.get("value")))
            except (TypeError, ValueError):
                value = RATIONING_WITHDRAW_CAP
            rationing_active[rule["id"]] = {"value": max(1, value)}
            self._push_activity(f'Rationing "{rule["name"]}" now caps stockpile withdrawals to '
                                f'{max(1, value)} while storage is low')
        elif rule["kind"] == "priority":
            rid = rule.get("value")
            self._push_activity(
                f'Priority rule "{rule["name"]}" now biases contributions toward {rid}')
        elif rule["kind"] == "custom":
            effect = self._normalize_custom_rule_effect(rule.get("effect"))
            if effect is not None:
                rule["effect"] = effect
                custom_modifiers[rule["id"]] = effect
                self._push_activity(
                    f'Custom rule "{rule["name"]}" now adds {effect["modifier"]["value"]} '
                    f'to matching {effect["condition"].get("action", next(iter(effect["subject"].values())))} actions')

    def _clear_governance_rule(self, rule, settlement_id=None):
        """Reverse _apply_governance_rule side effects on repeal."""
        rid = rule.get("id")
        if not rid:
            return
        if SCHISM_ENABLED and not self._is_global_governance_ballot(rule):
            sid = settlement_id or self._ballot_settlement_id(rule)
            self._harvest_quotas_for_settlement(sid).pop(rid, None)
            self._rationing_for_settlement(sid).pop(rid, None)
            self._custom_modifiers_for_settlement(sid).pop(rid, None)
        else:
            c = self.civilization
            c.get("harvestQuotas", {}).pop(rid, None)
            c.get("rationingActive", {}).pop(rid, None)
            c.get("customRuleModifiers", {}).pop(rid, None)

    def _enact_repeal(self, repeal_ballot, yes_count, settlement_id=None):
        """Remove the targeted enacted rule after a successful repeal vote."""
        c = self.civilization
        target_id = repeal_ballot.get("targetRuleId") or repeal_ballot.get("value")
        sid = settlement_id
        if SCHISM_ENABLED and not self._is_global_governance_ballot(repeal_ballot):
            sid = settlement_id or self._ballot_settlement_id(repeal_ballot)
            rules_list = self._rules_for_settlement(sid)
        else:
            rules_list = c["rules"]
        target = next((r for r in rules_list if r["id"] == target_id), None)
        if not target:
            self._push_activity(
                f'Repeal of "{target_id}" passed ({yes_count} yes) but the rule was already gone')
            self._log_benchmark("rule_repealed", len(rules_list),
                                {"id": target_id, "yes": yes_count, "missing": True})
            return "enacted"
        rules_list[:] = [r for r in rules_list if r["id"] != target_id]
        self._clear_governance_rule(target, sid)
        self._set_constitution_status(target_id, "repealed", settlement_id=sid)
        self._push_activity(
            f'Rule "{target["name"]}" repealed by vote ({yes_count} yes)')
        self._log_benchmark("rule_repealed", len(rules_list),
                            {"id": target_id, "yes": yes_count, "kind": target.get("kind")})
        return "enacted"

    def _propose_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot propose rules"
        rule = decision.get("rule")
        if not self._validate_rule(rule, agent=agent):
            # Advance the attempt cooldown even on failure so a rejected
            # proposal (colliding id, malformed rule, etc.) waits a full
            # RULE_PROPOSE_COOLDOWN before the auto-proposer retries, instead
            # of lastRuleActivityFrame staying frozen and the deterministic
            # backstop re-firing every RULES_TICK_FRAMES window. Deliberately
            # NOT lastRuleActivityFrame itself -- that field also backstops
            # blueprint-stall detection and must only reflect real governance
            # activity.
            c["lastRuleAttemptFrame"] = self.frameTick
            return f"{agent['name']} drafted an invalid rule"
        kind = rule.get("kind") or "custom"
        if kind == "resource_tax":
            value = float(rule["value"])
        else:
            value = rule.get("value")
        effect = self._normalize_custom_rule_effect(rule.get("effect")) \
            if kind == "custom" and rule.get("effect") is not None else None
        entry = {
            "id": rule["id"], "name": rule["name"], "kind": kind, "value": value,
            "description": rule.get("description", ""), "proposedBy": agent["name"],
            "enacted": False, "votes": {agent["name"]: "yes"},
        }
        if effect is not None:
            entry["effect"] = effect
        if rule.get("supersedes"):
            entry["supersedes"] = rule["supersedes"]
        if SCHISM_ENABLED and not self._is_global_governance_ballot(entry):
            entry["settlementId"] = self._settlement_id_for_agent(agent)
            pending_list = self._pending_for_settlement(entry["settlementId"])
        else:
            pending_list = c["pendingRules"]
        pending_list.append(entry)
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("rule_proposal", agent["name"], "everyone",
                                 f"{entry['name']}: {entry['description']}")
        self._tally_and_maybe_enact(entry)
        return f'{agent["name"]} proposed rule "{entry["name"]}"'

    def _propose_repeal(self, agent, decision):
        """Sid-parity Phase 2: start a repeal ballot for an enacted rule.
        Reuses the vote_rule / _tally_and_maybe_enact quorum scaffold."""
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot repeal rules"
        target_id = decision.get("target")
        if not isinstance(target_id, str) or not target_id:
            return f"{agent['name']} named no rule to repeal"
        sid = self._settlement_id_for_agent(agent) if SCHISM_ENABLED else None
        rules_list = self._rules_for_settlement(sid) if sid else c["rules"]
        pending_list = self._pending_for_settlement(sid) if sid else c["pendingRules"]
        target = next((r for r in rules_list if r["id"] == target_id), None)
        if not target:
            return f"{agent['name']} found no enacted rule {target_id}"
        if len(pending_list) >= MAX_PENDING_RULES:
            return f"{agent['name']} cannot propose a repeal — too many pending votes"
        ballot_id = f"repeal_{target_id}"
        if any(r["id"] == ballot_id for r in pending_list):
            return f"{agent['name']} found a repeal of {target_id} already pending"
        if any(r.get("kind") == "repeal" and r.get("targetRuleId") == target_id
               for r in pending_list):
            return f"{agent['name']} found a repeal of {target_id} already pending"
        entry = {
            "id": ballot_id,
            "name": f'Repeal {target["name"]}',
            "kind": "repeal",
            "value": target_id,
            "targetRuleId": target_id,
            "description": f'Repeal the enacted rule "{target["name"]}" ({target_id}).',
            "proposedBy": agent["name"],
            "enacted": False,
            "votes": {agent["name"]: "yes"},
        }
        if SCHISM_ENABLED:
            entry["settlementId"] = sid
        pending_list.append(entry)
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("rule_proposal", agent["name"], "everyone",
                                 f'{entry["name"]}: {entry["description"]}')
        self._tally_and_maybe_enact(entry)
        return f'{agent["name"]} proposed repealing "{target["name"]}"'

    def _active_priority_resource(self, agent=None, settlement_id=None):
        """Return the resource id from the newest enacted priority rule, if any."""
        if not RULES_ENABLED:
            return None
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            rules = self._rules_for_settlement(sid)
        else:
            rules = self.civilization["rules"]
        for rule in reversed(rules):
            if rule.get("kind") == "priority" and rule.get("enacted"):
                rid = rule.get("value")
                if isinstance(rid, str) and rid in self.civilization["resourceRegistry"]:
                    return rid
        return None

    def _vote_on_rule(self, agent, decision):
        c = self.civilization
        if not RULES_ENABLED:
            return f"{agent['name']} cannot vote"
        rule = self._find_pending_ballot(decision.get("target"), voter=agent)
        if not rule:
            return f"{agent['name']} found no such pending rule"
        vote = "no" if decision.get("vote") == "no" else "yes"
        rule["votes"][agent["name"]] = vote
        if LIFECYCLE_ENABLED and rule["kind"] == "succession" and vote == "yes":
            # An election is N candidate ballots, not N independent yes/no
            # referenda: voting yes for one candidate is implicitly a no for
            # every other candidate in the same election, so a villager's
            # ballot can't count toward two winners at once.
            election_id = (c.get("pendingSuccession") or {}).get("electionId")
            for sibling in c["pendingRules"]:
                if sibling is not rule and sibling["kind"] == "succession" \
                        and sibling.get("electionId") == election_id:
                    sibling["votes"].setdefault(agent["name"], "no")
                    if sibling["votes"][agent["name"]] == "yes":
                        sibling["votes"][agent["name"]] = "no"
        c["lastRuleActivityFrame"] = self.frameTick
        self._push_communication("vote", agent["name"], "everyone", f"{vote} on {rule['name']}")
        outcome = self._tally_and_maybe_enact(rule)
        suffix = f" ({outcome})" if outcome != "pending" else ""
        return f'{agent["name"]} voted {vote} on "{rule["name"]}"{suffix}'

    def _active_resource_tax(self, agent=None, settlement_id=None):
        if not RULES_ENABLED:
            return 0
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            rules = self._rules_for_settlement(sid)
        else:
            rules = self.civilization["rules"]
        rule = next((r for r in rules
                     if r["kind"] == "resource_tax" and r.get("enacted")), None)
        return (rule.get("value") or 0) if rule else 0

    def _active_or_pending_rationing(self, agent=None, settlement_id=None):
        """Living-ecosystem Phase 5: True if a "rationing" rule is already
        active or already awaiting a vote, LLM-driven or auto-proposed alike.
        Governance-churn guard for the emergency branch in
        _maybe_advance_rules -- without this, a storm that lingers for its
        full WEATHER_DWELL_TICKS window would re-propose a fresh emergency
        rationing rule every RULE_PROPOSE_COOLDOWN, crowding out ordinary
        priority/tax governance and pressuring MAX_PENDING_RULES."""
        if SCHISM_ENABLED:
            sid = settlement_id or (
                self._settlement_id_for_agent(agent) if agent else self._primary_settlement_id())
            rules = self._rules_for_settlement(sid)
            pending = self._pending_for_settlement(sid)
        else:
            c = self.civilization
            rules = c["rules"]
            pending = c["pendingRules"]
        if any(r.get("kind") == "rationing" for r in rules):
            return True
        if any(r.get("kind") == "rationing" for r in pending):
            return True
        return False

    def _enforce_resource_tax(self, agent, res):
        tax = self._active_resource_tax(agent)
        # Edibles are exempt: nothing ever consumes the stockpile, so taxing
        # food/fish just deletes it from the survival economy.
        if tax <= 0 or res in EDIBLE_RESOURCES:
            return
        c = self.civilization
        c["taxDue"] += tax
        # Once a mint exists, resource_tax prefers collecting coin (the
        # treasury's native currency) over the just-gathered/contributed
        # resource `res` -- but only INSTEAD of, never in addition to, so a
        # single tax event never double-charges an agent. Falls back to the
        # original per-resource tax whenever the agent holds no coin, and
        # is a complete no-op change with no mint built (ECONOMY_ENABLED off
        # or _mint_active() False), keeping pre-mint behavior byte-identical.
        if ECONOMY_ENABLED and self._mint_active() and agent["resources"].get("coin", 0) > 0:
            pay = min(tax, agent["resources"]["coin"])
            if pay > 0:
                agent["resources"]["coin"] -= pay
                c["stockpile"]["coin"] = c["stockpile"].get("coin", 0) + pay
                c["taxPaid"] += pay
            return
        pay = min(tax, agent["resources"].get(res, 0))
        if pay > 0:
            agent["resources"][res] -= pay
            c["stockpile"][res] = c["stockpile"].get(res, 0) + pay
            c["taxPaid"] += pay

