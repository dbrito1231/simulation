"""Phase 6e mixin: benchmarks/memory-maintenance/apply_decision/goals slice
of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_role_entropy` through
`_should_renudge` (formerly core.py lines ~532-1790). Covers: Sid-parity
benchmark helpers (`_role_entropy`, `_rule_adherence`, `_sample_benchmarks`),
wiki-memory merge and periodic memory maintenance, the large ~730-line
`apply_decision` world-mutation switch (kept as a single undivided method —
splitting its body would be a logic change, not a pure move), talk-target
resolution, and the goal-tracking cluster (`_goal_for_decision`, `_step_goal`,
`_apply_rule_based_fallback`, `_should_renudge`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _DecisionsMixin:
    """Mixin slice of SimEngine: benchmarks, memory maintenance,
    apply_decision, talk-target resolution, and goal tracking. See module
    docstring for exact scope."""

    # --- benchmarks ---
    def _role_entropy(self):
        counts = {}
        for a in self.agents:
            counts[a["role"]] = counts.get(a["role"], 0) + 1
        n = len(self.agents) or 1
        h = 0.0
        for k in counts:
            p = counts[k] / n
            if p > 0:
                h -= p * math.log2(p)
        return h

    def _rule_adherence(self):
        if self.civilization["taxDue"] <= 0:
            return None
        return self.civilization["taxPaid"] / self.civilization["taxDue"]

    # --- Theory of Mind (Emergence Breakthroughs F2) ---
    def _parse_theory_of_mind_line(self, text):
        if not text or "peer=" not in str(text).lower():
            return None
        parts = {}
        for chunk in str(text).split("|"):
            if "=" not in chunk:
                continue
            key, _, val = chunk.strip().partition("=")
            parts[key.strip().lower()] = val.strip()
        peer = parts.get("peer")
        expect = parts.get("expect")
        if not peer or not expect:
            return None
        return {
            "peer": peer,
            "wants": parts.get("wants", ""),
            "good_at": parts.get("good_at", ""),
            "owes": parts.get("owes", ""),
            "trust": parts.get("trust", "0.5"),
            "expect": expect,
        }

    def _apply_theory_of_mind_report(self, observer, text):
        if not THEORY_OF_MIND_ENABLED or not text:
            return
        parsed = self._parse_theory_of_mind_line(text)
        if not parsed:
            return
        peer = self._find_agent(parsed["peer"])
        if not peer or peer is observer:
            return
        self._upsert_peer_model(observer, peer["id"], {
            "wants": parsed["wants"],
            "good_at": parsed["good_at"],
            "owes": parsed["owes"],
            "trust": parsed["trust"],
        })
        expected = parsed["expect"].strip().lower()
        if expected:
            self._peer_prediction_pending[str(peer["id"])] = {
                "observer": observer["name"],
                "expected": expected,
                "frame": self.frameTick,
            }

    def _piano_default_modules(self):
        mods = {"perception": True, "social": True, "desire": True, "reflection": True}
        if THEORY_OF_MIND_ENABLED:
            mods["theory_of_mind"] = True
        return mods

    def _piano_module_names(self):
        names = ["perception", "social", "desire", "reflection"]
        if THEORY_OF_MIND_ENABLED:
            names.append("theory_of_mind")
        return names

    def _piano_to_run(self, modules, tick):
        """Staggered PIANO dispatch for one module-tick. theory_of_mind swaps
        into the social slot every 4th tick when enabled — no extra call."""
        modules = modules or self._piano_default_modules()
        to_run = []
        if modules.get("perception", True):
            to_run.append("perception")
        if modules.get("desire", True):
            to_run.append("desire")
        social_slot = None
        if modules.get("social", True) and tick % 2 == 0:
            social_slot = "social"
        if (THEORY_OF_MIND_ENABLED
                and modules.get("theory_of_mind", True)
                and tick % 4 == 0
                and tick % 2 == 0):
            social_slot = "theory_of_mind"
        if social_slot:
            to_run.append(social_slot)
        if modules.get("reflection", True) and tick % 3 == 0:
            to_run.append("reflection")
        return to_run, modules

    def _piano_off_tick_modules(self, modules, to_run):
        candidates = ["social", "reflection"]
        if THEORY_OF_MIND_ENABLED:
            candidates.append("theory_of_mind")
        return [m for m in candidates if modules.get(m, True) and m not in to_run]

    def _cap_peer_model_field(self, text):
        if not text:
            return ""
        return str(text).strip()[:PEER_MODEL_FIELD_CHAR_CAP]

    def _upsert_peer_model(self, agent, peer_id, fields):
        peer_id = str(peer_id)
        model = agent.setdefault("peerModel", {})
        try:
            trust = float(fields.get("trust", 0.5))
        except (TypeError, ValueError):
            trust = 0.5
        model[peer_id] = {
            "wants": self._cap_peer_model_field(fields.get("wants", "")),
            "good_at": self._cap_peer_model_field(fields.get("good_at", "")),
            "owes_me": self._cap_peer_model_field(
                fields.get("owes_me", fields.get("owes", ""))),
            "trust": max(0.0, min(1.0, trust)),
            "frame": self.frameTick,
        }
        while len(model) > PEER_MODEL_MAX_PEERS:
            oldest_id = min(model.items(), key=lambda kv: kv[1].get("frame", 0))[0]
            del model[oldest_id]

    def _peer_model_prompt_suffix(self, agent, peer_agent):
        if not THEORY_OF_MIND_ENABLED:
            return None
        entry = (agent.get("peerModel") or {}).get(str(peer_agent["id"]))
        if not entry:
            return None
        parts = []
        if entry.get("wants"):
            parts.append(f"wants {entry['wants']}")
        if entry.get("good_at"):
            parts.append(f"good at {entry['good_at']}")
        if entry.get("owes_me"):
            parts.append(f"owes me {entry['owes_me']}")
        trust = entry.get("trust")
        if trust is not None:
            parts.append(f"trust {trust:.1f}")
        return " — ".join(parts) if parts else None

    def _score_peer_prediction(self, peer, action):
        if not THEORY_OF_MIND_ENABLED:
            return
        pending = self._peer_prediction_pending.pop(str(peer["id"]), None)
        if not pending:
            return
        self._peer_prediction_total += 1
        if str(action).lower() == pending["expected"]:
            self._peer_prediction_hits += 1

    def _peer_prediction_accuracy(self):
        if self._peer_prediction_total == 0:
            return None
        return self._peer_prediction_hits / self._peer_prediction_total

    def _sample_benchmarks(self):
        if not BENCHMARKS_ENABLED:
            return
        entropy = self._role_entropy()
        adherence = self._rule_adherence()
        adoption_by_meme = self._meme_adoption_counts()
        adoption = self._meme_adoption_count()
        living_n = len([a for a in self.agents if a.get("deathFrame") is None]) or len(self.agents) or 1
        adoption_rate = adoption / living_n
        self.lastBenchmarks = {
            "entropy": entropy, "adherence": adherence, "adoption": adoption,
            "adoptionRate": adoption_rate, "adoptionByMeme": adoption_by_meme,
            "moduleTotal": self._module_period_runs,
            "rules": len(self.civilization["rules"]),
            "structures": len(self.civilization["structures"]),
            "level": self.civilization["level"], "memory": self.lastMemorySize,
            "effectThroughput": self._effect_period_fired,
            "ecologyScarcity": self._ecology_scarcity_index(),
            "roleRebalanceLatency": self.civilization.get("lastRoleRebalanceLatency"),
            "ruleKindDiversity": len(self.civilization.get("ruleKindsEverEnacted") or []),
        }
        self._mark_top_dirty("benchmarks")
        if TECH_TREE_ENABLED:
            self.lastBenchmarks["era"] = self._current_era_name()
            self.lastBenchmarks["techTier"] = self._village_tech_tier()
        role_counts = {}
        for a in self.agents:
            role_counts[a["role"]] = role_counts.get(a["role"], 0) + 1
        self._log_benchmark("specialization_entropy", round(entropy, 2), {"counts": role_counts})
        latency = self.civilization.get("lastRoleRebalanceLatency")
        if EMERGENT_ROLES and latency is not None:
            self._log_benchmark("role_rebalance_latency", latency,
                                {"frames": latency})
        if adherence is not None:
            self._log_benchmark("rule_adherence", round(adherence, 2),
                                {"paid": self.civilization["taxPaid"], "due": self.civilization["taxDue"]})
        if RULES_ENABLED:
            kinds = list(self.civilization.get("ruleKindsEverEnacted") or [])
            self._log_benchmark("rule_kind_diversity", len(kinds), {"kinds": kinds})
        if MEMES_ENABLED:
            self._log_benchmark("meme_adoption", adoption,
                                {"rate": round(adoption_rate, 2),
                                 "by_meme": adoption_by_meme,
                                 "authored_beliefs": sum(1 for b in self._belief_registry().values()
                                                          if not b.get("seed")),
                                 "belief_pitch_calls": self.civilization.get("beliefPitchCalls", 0),
                                 "of": living_n})
        if PIANO_MODULES or META_SYSTEM:
            self._log_benchmark("module_total", self._module_period_runs,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._module_period_runs = 0
        if PIANO_MODULES:
            # Sid-parity Phase 1: surface module-pool health so regressions
            # (slow modules, timeout spikes) are visible in soak runs.
            latency = {
                module: round(total_ms / count, 1)
                for module, (total_ms, count) in self._piano_latency_ms.items()
                if count
            }
            self.lastBenchmarks["piano_module_latency"] = latency
            self.lastBenchmarks["piano_module_drops"] = self._piano_module_drops
            self._log_benchmark("piano_module_latency", latency,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._log_benchmark("piano_module_drops", self._piano_module_drops)
        if ALWAYS_ON_MODULES:
            ages = self._module_note_ages
            note_age = {"avg_s": round(sum(ages) / len(ages), 1) if ages else 0.0,
                        "max_s": round(max(ages), 1) if ages else 0.0,
                        "count": len(ages)}
            pulse_work = list(self._module_pulse_work)
            self.lastBenchmarks["module_note_age"] = note_age
            self.lastBenchmarks["module_pulse_work"] = pulse_work
            self.lastBenchmarks["module_refresh_failures"] = self._module_refresh_failures
            self._log_benchmark("module_note_age", note_age,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._log_benchmark("module_pulse_work", pulse_work,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._log_benchmark("module_refresh_failures", self._module_refresh_failures)
            self._module_note_ages = []
            self._module_pulse_work = []
        self._log_benchmark("memory_store_size", self.lastMemorySize)
        if STRUCTURE_EFFECTS_ENABLED:
            fired = self._effect_period_fired
            self._log_benchmark("structure_effect_throughput", fired,
                                {"period_ticks": BENCHMARK_TICK_FRAMES})
            self._last_effect_benchmark_fired = fired
            self._effect_period_fired = 0
        if ECOLOGY_ENABLED:
            scarcity = self._ecology_scarcity_index()
            if scarcity is not None:
                self._log_benchmark("ecology_scarcity_index", scarcity,
                                    {"period_ticks": BENCHMARK_TICK_FRAMES})
        if TECH_TREE_ENABLED:
            self._log_benchmark("era", self.civilization.get("eraIndex") or 0,
                                {"era": self._current_era_name(),
                                 "tech_tier": self._village_tech_tier()})
        if GOODS_ENABLED:
            c = self.civilization
            caps = {rid: self._storage_capacity(rid) for rid in EDIBLE_RESOURCES}
            stored = {rid: c["stockpile"].get(rid, 0)
                      + sum(a["resources"].get(rid, 0) for a in self.agents)
                      for rid in EDIBLE_RESOURCES}
            total_cap = sum(caps.values()) or 1
            self._log_benchmark("storage_utilization",
                                round(sum(stored.values()) / total_cap, 3),
                                {"stored": stored, "capacity": caps,
                                 "spoiled_period": self._spoiled_period,
                                 "season": self._current_season()})
            self._spoiled_period = 0
            conds = [s.get("condition", 100) for s in c["structures"]]
            if conds:
                self._log_benchmark(
                    "structure_condition", round(sum(conds) / len(conds), 1),
                    {"ruins": sum(1 for s in c["structures"] if s.get("isRuin")),
                     "disrepair": sum(1 for v in conds
                                      if 0 < v < STRUCTURE_DISREPAIR_THRESHOLD),
                     "structures": len(conds)})
        if ECONOMY_ENABLED:
            gini = self._wealth_gini()
            if gini is not None:
                homeowners = sum(1 for a in self.agents if a.get("homeStructureId"))
                self._log_benchmark("wealth_gini", gini,
                                    {"market_active": self._market_active(),
                                     "homeowners": homeowners, "agents": len(self.agents)})
        if LIFECYCLE_ENABLED:
            c = self.civilization
            ages = sorted(a["age"] for a in self.agents if a.get("age") is not None)
            living = [a for a in self.agents if a.get("deathFrame") is None]
            if ages:
                median_age = ages[len(ages) // 2] if len(ages) % 2 else \
                    (ages[len(ages) // 2 - 1] + ages[len(ages) // 2]) / 2
                self._log_benchmark(
                    "population_median_age", round(median_age, 1),
                    {"population": len(living), "cap": self._population_cap(),
                     "births": c.get("births", 0), "deaths": c.get("deaths", 0),
                     "elder_age": round(next((a["age"] for a in self.agents
                                              if a["role"] == "elder"), 0) or 0, 1),
                     "population_floor_held": c.get("populationFloorHeld", False)})
        if CULTURE_ENABLED:
            c = self.civilization
            living = [a for a in self.agents if a.get("deathFrame") is None]
            avg_skills = {k: round(sum(a["skills"].get(k, 0.0) for a in living) / len(living), 2)
                         for k in SKILL_KINDS} if living else {k: 0.0 for k in SKILL_KINDS}
            self._log_benchmark(
                "skill_spread", round(sum(avg_skills.values()), 2),
                {"avg_by_kind": avg_skills, "teach_count": c.get("teachCount", 0),
                 "practice_count": c.get("skillPracticeCount", 0),
                 "library_knowledge_entries": len(c.get("libraryKnowledge") or [])})
            self._log_benchmark(
                "chronicle_size", len(c.get("chronicle") or []),
                {"meme_mutations": c.get("memeMutations", 0),
                 "belief_pitch_calls": c.get("beliefPitchCalls", 0)})
        if THEORY_OF_MIND_ENABLED:
            accuracy = self._peer_prediction_accuracy()
            if accuracy is not None:
                self.lastBenchmarks["peerPredictionAccuracy"] = round(accuracy, 3)
                self._log_benchmark(
                    "peer_prediction_accuracy", round(accuracy, 3),
                    {"hits": self._peer_prediction_hits,
                     "total": self._peer_prediction_total,
                     "period_ticks": BENCHMARK_TICK_FRAMES})
        if GOD_MODE_ENABLED:
            # Sovereign God mode (Phase 2): intervention-aware evidence (rule
            # 6 of the plan) -- expose `intervened` in benchmark metadata so
            # autonomous and god-influenced runs are never mixed accidentally,
            # plus counts for interventions/active-effects/rejected-commands.
            god = self.civilization.get("godState") or {}
            self.lastBenchmarks["intervened"] = bool(god.get("intervened"))
            self._log_benchmark(
                "god_interventions", len(god.get("recentInterventions") or []),
                {"intervened": bool(god.get("intervened")),
                 "active_effects": len(god.get("activeEvents") or []),
                 "rejected_commands": self._god_rejected_count})
        if CONTRACTS_ENABLED:
            c = self.civilization
            opened = c.get("contractsOpened", 0)
            fulfilled = c.get("contractsFulfilled", 0)
            defaults = c.get("contractDefaults", 0)
            settled = fulfilled + defaults
            default_rate = (defaults / settled) if settled else 0.0
            self.lastBenchmarks["contractsOpened"] = opened
            self.lastBenchmarks["contractsFulfilled"] = fulfilled
            self.lastBenchmarks["contractDefaultRate"] = round(default_rate, 3)
            self._log_benchmark(
                "contracts_opened", opened,
                {"open": len(c.get("contracts") or []),
                 "escrow": c.get("contractEscrow", 0)})
            self._log_benchmark(
                "contracts_fulfilled", fulfilled, {"defaults": defaults})
            self._log_benchmark(
                "contract_default_rate", round(default_rate, 3),
                {"opened": opened, "fulfilled": fulfilled, "defaults": defaults})
        try:
            flush = self.d.get("flush_benchmarks")
            if flush:
                flush()
        except Exception:
            pass

    def _run_wiki_memory_merge(self, agent, ms, joined):
        """WIKI_MEMORY path for _run_memory_maintenance. Replaces the plain
        summarize-and-append call with a single merge-and-reconcile call
        (same one-call-per-pass budget) that updates agent["memoryWiki"]'s
        three named sections instead of FIFO-dropping into longTerm. The
        contradiction lint rides the same prompt/response -- no extra call."""
        wiki = agent.setdefault("memoryWiki", {})
        cur_rel = wiki.get("relationships") or ""
        cur_goals = wiki.get("goals") or ""
        cur_lessons = wiki.get("lessons") or ""
        system = (
            "You are the memory keeper for an agent in a village simulation. "
            "You maintain three short wiki sections describing the agent: "
            "RELATIONSHIPS, GOALS, LESSONS. Given the agent's CURRENT sections "
            "and a batch of recent raw memories, return UPDATED sections that "
            "merge the new facts into the existing text: deduplicate repeats, "
            "keep everything still relevant (drop nothing that still matters), "
            "and if two facts contradict, resolve to the better-supported one "
            "and briefly note the resolution. Output EXACTLY these labeled "
            "lines and nothing else:\n"
            "RELATIONSHIPS: <text>\nGOALS: <text>\nLESSONS: <text>\n"
            "Only if you found and resolved a contradiction, append one more "
            "line:\nCONTRADICTION: <brief note>\n"
            f"Keep each section under {WIKI_SECTION_CHAR_CAP} characters."
        )
        user = (
            f"Agent {agent['name']}'s current sections:\n"
            f"RELATIONSHIPS: {cur_rel}\nGOALS: {cur_goals}\nLESSONS: {cur_lessons}\n\n"
            f"Recent raw memories: {joined}\n"
        )
        response = self.d["lm_complete"](system, user, max_tokens=220, temperature=0.4)
        if not response:
            return
        is_scaffold = self.d.get("is_scaffold_text")
        parsed = {}
        contradiction = None
        for line in response.splitlines():
            line = line.strip()
            if not line:
                continue
            low = line.lower()
            if low.startswith("relationships:"):
                parsed["relationships"] = line.split(":", 1)[1].strip()
            elif low.startswith("goals:"):
                parsed["goals"] = line.split(":", 1)[1].strip()
            elif low.startswith("lessons:"):
                parsed["lessons"] = line.split(":", 1)[1].strip()
            elif low.startswith("contradiction:"):
                contradiction = line.split(":", 1)[1].strip()
        for key, cur in (("relationships", cur_rel), ("goals", cur_goals),
                         ("lessons", cur_lessons)):
            new_val = parsed.get(key)
            if new_val and is_scaffold and is_scaffold(new_val):
                # Poisoned/scaffold output -- discard, keep prior text (same
                # guard as the flag-off longTerm-cleaning pass below).
                new_val = None
            wiki[key] = (new_val if new_val else cur)[:WIKI_SECTION_CHAR_CAP]
        lessons_text = wiki.get("lessons") or ""
        if lessons_text:
            # Mirror the flag-off path: feed the semantic recall store so
            # recall keeps working in both modes.
            ms.store(agent["name"], lessons_text, salience=0.9, kind="summary",
                     frame_tick=self.frameTick, tier="longTerm")
        if contradiction:
            self._push_activity(f"{agent['name']} reconciled a memory: {contradiction}")

    # --- memory maintenance (round-robin summarizer + periodic cleaner) ---
    def _run_memory_maintenance(self):
        if not MEMORY_ENABLED or not self.agents:
            return
        agent = self.agents[self._memory_maint_index % len(self.agents)]
        self._memory_maint_index += 1
        try:
            ms = self.d["memory_store"]
            recents = [e for e in ms.recent(agent=agent["name"], limit=12) if e["kind"] != "summary"]
            if len(recents) >= 4:
                joined = "; ".join(e["text"] for e in recents)
                if WIKI_MEMORY:
                    self._run_wiki_memory_merge(agent, ms, joined)
                else:
                    summary = self.d["lm_complete"](
                        "You compress an agent's recent memories into ONE concise "
                        "first-person sentence capturing what matters for their future "
                        "decisions. Output only the sentence, no preamble.",
                        f"Agent {agent['name']}'s recent memories: {joined}\nSummary:",
                        max_tokens=80, temperature=0.4)
                    if summary:
                        summary = summary.strip().strip('"').strip()[:200]
                    if summary:
                        ms.store(agent["name"], summary, salience=0.9, kind="summary",
                                 frame_tick=self.frameTick, tier="longTerm")
                        agent["memory"]["longTerm"].append(summary)
                        while len(agent["memory"]["longTerm"]) > LONG_MEM_CAP:
                            agent["memory"]["longTerm"].pop(0)
                        self._push_activity(f"{agent['name']} reflected: {summary}")
            self.lastMemorySize = ms.size()
        except Exception:
            pass
        if self._memory_maint_index % 4 == 0:
            try:
                self.d["memory_store"].clean()
                self.lastMemorySize = self.d["memory_store"].size()
            except Exception:
                pass
            # MemoryStore.clean() only scrubs the vector store; each agent's
            # live memory.longTerm list is separate engine state and can hold
            # the same leaked chain-of-thought scaffold (see is_scaffold_text)
            # if it was written before validation existed. Without this, a
            # running session keeps poisoned entries indefinitely -- they only
            # roll off after LONG_MEM_CAP new (now-validated) summaries arrive.
            try:
                is_scaffold = self.d["is_scaffold_text"]
                for a in self.agents:
                    long_term = a.get("memory", {}).get("longTerm")
                    if long_term:
                        a["memory"]["longTerm"] = [
                            t for t in long_term if not is_scaffold(t)
                        ]
            except Exception:
                pass

    # --- the 27-case world-mutation switch (ported applyDecision) ---
    def apply_decision(self, agent, decision):
        action = decision.get("action") or "rest"
        summary = f"{agent['name']} rested"
        resource_acted = None  # set by collect_resource/contribute_resources/trade_resource
        # below; used only to honor a pending commitment (#5.4) after the fact.
        c = self.civilization
        # Snapshot the pending sprite-design turn (if any) before dispatch so
        # the missing-case check below can tell whether *this* turn survived
        # untouched -- i.e. the decision's action wasn't submit_structure_sprite
        # and nothing else (e.g. _upgrade_structure replacing it, or the
        # voice-guidance cancel in _build_think_payload) already handled it.
        # See _count_sprite_design_failure.
        pending_sprite_turn = agent.get("spriteDesignTurn")

        is_talk = action == "talk_to_nearby"
        if is_talk and decision.get("message"):
            agent["consecutiveTalks"] += 1
        elif action != "rest":
            agent["consecutiveTalks"] = 0

        is_move_only = action.startswith("move_to_") or action == "rest"
        agent["consecutiveIdleMoves"] = (agent.get("consecutiveIdleMoves", 0) + 1) if is_move_only else 0

        if action == "move_to_district":
            # Models often put the district id in target_district instead of
            # target; accept either so the move actually happens.
            target = decision.get("target") or decision.get("target_district")
            self._set_agent_target(agent, target)
            district_id = self._resolve_target_district(target, agent) or target or "somewhere"
            summary = f"{agent['name']} heads to {district_id}"

        elif action == "move_to_agent":
            target = decision.get("target")
            if target and target in self.agent_names:
                self._set_agent_target_to_agent(agent, target)
                summary = f"{agent['name']} moves toward {target}"
            else:
                nearest = self._find_nearest_agent(agent)
                if nearest:
                    self._set_agent_target_to_agent(agent, nearest["name"])
                    summary = f"{agent['name']} moves toward {nearest['name']}"

        elif action.startswith("move_to_"):
            # Back-compat hedge: an older move_to_<kind> action (e.g.
            # "move_to_farm", from a stale client/model) still resolves via
            # the kind-name hedge in _resolve_target_district instead of
            # failing outright.
            kind = action[len("move_to_"):]
            self._set_agent_target(agent, kind)
            summary = f"{agent['name']} heads to the {kind}"

        elif action == "found_belief":
            summary = self._found_belief(agent, decision.get("belief"))

        elif action == "collect_resource":
            c["collectAttempts"] += 1
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            else:
                zone = agent["currentZone"]
                unmet = self._first_unmet_project_resource(district_id)
                target = decision.get("target")
                target_def = c["resourceRegistry"].get(target) if target else None
                zone_resources = self._get_zone_resources(zone)
                candidates = []
                if target_def and target_def.get("gatherZone") == zone:
                    candidates.append(target)
                if unmet and self._gather_zone_for_resource(unmet) == zone:
                    candidates.append(unmet)
                candidates.extend(zone_resources)
                if not zone_resources and zone == "beach" and agent["role"] == "fisher":
                    candidates.append("food")
                resource = next((r for r in candidates
                                 if agent["resources"].get(r, 0) < self._carry_cap(agent)), None)
                if resource:
                    summary = self._perform_gather(agent, resource)
                    resource_acted = resource if "collected" in summary else None
                else:
                    contrib_res = self._pick_contribution_resource(
                        agent, {"target": unmet, "target_district": district_id}, district_id)
                    contributed = self._try_contribute_resource(agent, contrib_res, district_id)
                    if contributed:
                        summary = contributed
                        resource_acted = contrib_res
                    elif unmet and self._gather_zone_for_resource(unmet):
                        gz = self._gather_zone_for_resource(unmet)
                        redirect = self._pickless_stone_route(agent, unmet)
                        if redirect:
                            summary = redirect
                        elif agent["currentZone"] != gz:
                            self._set_agent_target_once(agent, gz)
                            summary = f"{agent['name']} heads to gather {unmet}"
                        else:
                            summary = f"{agent['name']} found nothing to collect"
                    else:
                        summary = f"{agent['name']} found nothing to collect"

        elif action == "hunt_wildlife":
            if not WILDLIFE_ENABLED:
                summary = f"{agent['name']} cannot hunt — wildlife is disabled"
            else:
                target_raw = decision.get("target")
                creature = self._find_wildlife_by_id(target_raw) if target_raw not in (None, "") else None
                if creature is None:
                    creature = self._nearest_huntable_wildlife(agent)
                if creature is None:
                    summary = f"{agent['name']} found no wildlife to hunt"
                elif (creature.get("kind") in WILDLIFE_DECORATIVE_KINDS
                      or creature.get("kind") not in WILDLIFE_YIELD
                      or not creature.get("alive")):
                    summary = f"{agent['name']} cannot hunt that creature"
                elif _dist(agent["x"], agent["y"], creature["x"], creature["y"]) > HUNT_RADIUS:
                    summary = f"{agent['name']} is too far to hunt that {creature.get('kind')}"
                else:
                    result = self._apply_hunt_damage(agent, creature)
                    if not result.get("ok"):
                        summary = f"{agent['name']} failed to hunt ({result.get('reason') or 'unknown'})"
                    elif result.get("killed"):
                        resource = result.get("resource")
                        gained = (result.get("agentAdded") or 0) + (result.get("stockpileAdded") or 0)
                        summary = f"{agent['name']} hunted a {result.get('kind')}"
                        if resource and gained:
                            summary += f" (+{gained} {resource})"
                        resource_acted = resource
                    else:
                        summary = (f"{agent['name']} strikes a {result.get('kind')} "
                                   f"({result.get('hp')}/{creature.get('maxHp', '?')} hp)")

        elif action == "confront_agent":
            if not SURVIVAL_ENABLED:
                summary = f"{agent['name']} cannot confront — survival is disabled"
            else:
                em = self._sage_emergency()
                if em and agent["name"] in self._sage_responders(em):
                    summary = f"{agent['name']} cannot confront — Sage emergency duty"
                else:
                    target = self._find_agent(decision.get("target"))
                    if not target or target.get("deathFrame") is not None:
                        summary = f"{agent['name']} found no one to confront"
                    elif target["incapacitated"]:
                        summary = (f"{agent['name']} cannot confront {target['name']} — "
                                   "they have collapsed")
                    elif target["role"] == "elder":
                        summary = f"{agent['name']} cannot confront the elder"
                    elif not self._confront_authorized(agent, target):
                        summary = (f"{agent['name']} has no cause to confront "
                                   f"{target['name']}")
                    elif self._confront_on_cooldown(agent, target):
                        summary = (f"{agent['name']} must wait before confronting "
                                   f"{target['name']} again")
                    elif self._distance_to(agent, target) > CONFRONT_CONTACT_DIST:
                        self._auto_move_toward_target(agent, target["name"])
                        summary = f"{agent['name']} moves to confront {target['name']}"
                    else:
                        pre_health = target["health"]
                        if pre_health <= CONFRONT_LETHAL_THRESHOLD:
                            target["health"] = max(0, pre_health - CONFRONT_DAMAGE)
                            if target["health"] <= 0:
                                target["incapacitated"] = True
                        else:
                            target["health"] = max(
                                CONFRONT_INCAP_HEALTH, pre_health - CONFRONT_DAMAGE)
                        stolen = self._most_abundant_edible_for_steal(target)
                        if stolen:
                            target["resources"][stolen] -= 1
                            cap = self._carry_cap(agent)
                            held = agent["resources"].get(stolen, 0)
                            if held < cap:
                                agent["resources"][stolen] = held + 1
                            else:
                                c["stockpile"][stolen] = (
                                    c["stockpile"].get(stolen, 0) + 1)
                        self._flee_from_agent(agent, target)
                        if agent["relationships"].get(target["name"]) != "rival":
                            agent["relationships"][target["name"]] = "rival"
                        pair_key = self._confront_pair_key(agent["id"], target["id"])
                        c.setdefault("confrontCooldowns", {})[pair_key] = (
                            self.frameTick + CONFRONT_COOLDOWN_FRAMES)
                        note = f"{agent['name']} confronted {target['name']}"
                        if stolen:
                            note += f" and took {stolen}"
                        self._push_activity(note)
                        self._push_memory(agent, f"Confronted {target['name']}")
                        self._push_memory(target, f"Confronted by {agent['name']}")
                        summary = note

        elif action == "talk_to_nearby":
            recipient = self._resolve_talk_target(agent, decision)
            self._auto_move_toward_target(agent, recipient if recipient != "everyone" else decision.get("target"))
            if decision.get("message"):
                agent["message"] = decision["message"]
                agent["messageTimer"] = 180
                agent["lastSpokeFrame"] = self.frameTick
                self._push_conversation(agent["name"], recipient, decision["message"])
                self._deliver_message(agent["name"], recipient, decision["message"], "speech")
                self._maybe_spread_beliefs(
                    agent, recipient, decision["message"], decision.get("belief_pitch"),
                    decision.get("belief_pitch_quality"), decision.get("belief_pitch_scored", False))
                self._maybe_form_commitment(agent, recipient, decision["message"])
                if CULTURE_ENABLED:
                    self._maybe_teach(agent, recipient, decision["message"])
                summary = f"{agent['name']} talked to {recipient}"
            else:
                self._push_communication("talk_attempt", agent["name"], recipient, None, "no_message")
                summary = f"{agent['name']} looked for someone to talk to"

        elif action == "trade_resource":
            target = self._find_agent(decision.get("target"))
            if target:
                self._auto_move_toward_target(agent, target["name"])
            nearby = target and self._distance_to(agent, target) <= 80
            give = self._most_abundant_resource(agent)
            if nearby and give and ECONOMY_ENABLED and self._market_active():
                summary = self._priced_trade(agent, target, give)
                resource_acted = give if "refused" not in summary else None
            elif nearby and give:
                agent["resources"][give] -= 1
                target["resources"][give] = target["resources"].get(give, 0) + 1
                self._nudge_ally(agent, target["name"])
                self._nudge_ally(target, agent["name"])
                self._push_memory(target, f"Received {give} from {agent['name']}")
                summary = f"{agent['name']} traded {give} to {target['name']}"
                resource_acted = give
                self._emit_shipment(agent.get("currentDistrict"), target.get("currentDistrict"), give)
                if ECONOMY_ENABLED:
                    agent["lastTradeRejection"] = None
            elif target:
                summary = f"{agent['name']} moves to trade with {target['name']}"
            else:
                summary = f"{agent['name']} rested"

        elif action == "craft_item":
            summary = self._craft_item(agent, decision.get("target"))

        elif action == "propose_recipe":
            summary = self._propose_recipe(agent, decision.get("recipe"))

        elif action in ("approve_recipe", "reject_recipe"):
            summary = self._review_recipe(agent, action, decision.get("target"), decision.get("message"))

        elif action == "start_project":
            summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                or f"{agent['name']} could not start a project"

        elif action == "start_terraform":
            if ECOLOGY_ENABLED:
                summary = self._start_terraform_for(agent, decision.get("target"),
                                                    decision.get("target_district"))
                if summary:
                    agent["lastTerraformRejection"] = None
                else:
                    agent["lastTerraformRejection"] = {
                        "reason": "no free district of the right kind for that terraform",
                        "frame": self.frameTick,
                    }
                    summary = f"{agent['name']} could not start that terraform project"
            else:
                summary = f"{agent['name']} cannot terraform — ecology is disabled"

        elif action == "repair_structure":
            if GOODS_ENABLED:
                summary = self._repair_structure(agent, decision.get("target"))
            else:
                summary = f"{agent['name']} cannot repair — structure decay is disabled"

        elif action == "upgrade_structure":
            if STRUCTURE_UPGRADES_ENABLED:
                summary = self._upgrade_structure(agent, decision.get("target"))
            else:
                summary = f"{agent['name']} cannot upgrade — structure upgrades are disabled"

        elif action == "submit_structure_sprite":
            if STRUCTURE_UPGRADES_ENABLED:
                summary = self._apply_structure_sprite(agent, decision.get("sprite"))
            else:
                summary = f"{agent['name']} cannot submit a sprite — upgrades are disabled"

        elif action == "contribute_resources":
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            else:
                res = self._pick_contribution_resource(agent, decision, district_id)
                contributed = self._try_contribute_resource(agent, res, district_id)
                if contributed:
                    summary = contributed
                    resource_acted = res
                elif self._is_project_complete(district_id):
                    summary = self._build_active_structure(agent, district_id)
                else:
                    unmet = self._first_unmet_project_resource(district_id)
                    gz = self._gather_zone_for_resource(unmet) if unmet else None
                    redirect = self._pickless_stone_route(agent, unmet) if unmet else None
                    if redirect:
                        summary = redirect
                    elif unmet and gz and agent["currentZone"] != gz:
                        self._set_agent_target_once(agent, gz)
                        summary = f"{agent['name']} heads to gather {unmet}"
                    elif unmet and gz and agent["currentZone"] == gz \
                            and agent["resources"].get(unmet, 0) < self._carry_cap(agent):
                        summary = self._perform_gather(agent, unmet)
                        if "collected" in summary:
                            resource_acted = unmet
                        else:
                            resource_acted = None
                    else:
                        summary = f"{agent['name']} has nothing to contribute"

        elif action == "build_structure":
            district_id = self._resolve_contribution_district(agent, decision.get("target_district"))
            if not district_id:
                summary = self._start_project_for(agent, decision.get("target"), decision.get("target_district")) \
                    or f"{agent['name']} could not start a project"
            elif self._is_project_complete(district_id):
                summary = self._build_active_structure(agent, district_id)
            else:
                summary = f"{agent['name']} waiting for more resources in {district_id}"

        elif action == "propose_blueprint":
            bp = decision.get("blueprint")
            if bp and bp.get("id") in c["rejectedBlueprintIds"]:
                summary = f"{agent['name']}'s blueprint {bp.get('id')} was already rejected"
                agent["lastBlueprintRejection"] = {
                    "reason": "blueprint was previously rejected", "frame": self.frameTick}
            else:
                ok, reason = self._validate_blueprint(bp)
                if ok:
                    needs_str = ", ".join(f"{k}x{v}" for k, v in bp["needs"].items())
                    build_ctx = agent.get("inventionBuildContext") or {}
                    agent["inventionBuildContext"] = None
                    dup_owner = self._effect_vector_owner_map().get(
                        self._canonical_effect_vector(bp.get("function")))
                    c["pendingBlueprints"].append({
                        "id": bp["id"], "name": bp["name"], "needs": dict(bp["needs"]),
                        "function": dict(bp["function"]),
                        "newResources": [{"id": r["id"], "name": r["name"],
                                          "gatherZone": r.get("gather_zone"),
                                          "color": r.get("color", "#BDBDBD")}
                                         for r in (bp.get("new_resources") or [])],
                        "visualStyle": bp.get("visual_style") or "generic",
                        "sprite": bp.get("sprite"),
                        "proposedBy": agent["name"],
                        "sageReview": "pending", "sageReviewReason": None, "sageReviewFrame": None,
                        "duplicateOf": dup_owner,
                        "proposedFrame": self.frameTick,
                        "requestedDistrict": build_ctx.get("district"),
                        "buildIntent": build_ctx.get("type"),
                        **({"tier": bp.get("tier") or 1} if TECH_TREE_ENABLED else {}),
                    })
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    c["inventionRequiredStreak"] = 0
                    c["inventionBackstopFires"] = 0
                    agent["lastBlueprintRejection"] = None
                    summary = f"{agent['name']} proposed {bp['name']} (needs {needs_str})"
                    if TECH_TREE_ENABLED:
                        self._record_council_proposal(agent, bp, decision)
                else:
                    agent["lastBlueprintRejection"] = {"reason": reason, "frame": self.frameTick}
                    summary = f"{agent['name']} drafted an invalid blueprint ({reason})"
                    if TECH_TREE_ENABLED and "tier" in (reason or ""):
                        self._log_benchmark(
                            "tier_gate_rejection", (bp or {}).get("tier") or 0,
                            {"kind": "blueprint", "target": (bp or {}).get("id"),
                             "village_tier": self._village_tech_tier()})

        elif action == "sage_review_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            sage_decision = decision.get("sage_decision")
            if self._is_sage_reviewer(agent) and idx != -1 and sage_decision in ("approve", "deny") \
                    and c["pendingBlueprints"][idx]["sageReview"] == "pending":
                bp = c["pendingBlueprints"][idx]
                bp["sageReview"] = "approved" if sage_decision == "approve" else "denied"
                bp["sageReviewReason"] = decision.get("message") or decision.get("reasoning") or None
                bp["sageReviewFrame"] = self.frameTick
                if decision.get("message"):
                    agent["message"] = decision["message"]
                    agent["messageTimer"] = 180
                verb = "approved" if sage_decision == "approve" else "denied"
                summary = f"{agent['name']} {verb} the sage review of {bp['name']}"
            else:
                summary = f"{agent['name']} could not sage-review that blueprint"

        elif action == "approve_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            bp = c["pendingBlueprints"][idx] if idx != -1 else None
            review_ok = not SAGE_REVIEW_ENABLED or (bp and bp.get("sageReview") in ("approved", "skipped"))
            resolved = False
            if agent["role"] == "elder" and idx != -1 and review_ok:
                if bp.get("duplicateOf") and not self._structure_type_built(bp["duplicateOf"]):
                    # duplicateOf can name a seed/custom type that's registered
                    # but not yet standing (still under construction, or --
                    # since _effect_vector_owner_map also scans pendingBlueprints
                    # -- another proposal that hasn't even been approved yet).
                    # There is nothing to upgrade yet: leave the blueprint
                    # pending rather than popping it into a failed upgrade
                    # attempt, so the elder can retry once the original is
                    # built, or reject_blueprint it explicitly.
                    summary = (f"{agent['name']} cannot approve {bp['name']} as an upgrade yet -- "
                               f"{bp['duplicateOf']} is not built yet. Wait for it to be built, "
                               f"or reject_blueprint if it's unnecessary.")
                elif bp.get("duplicateOf"):
                    lead_agent = self._find_agent(bp.get("proposedBy")) or agent
                    upgrade_summary = self._upgrade_structure(lead_agent, bp["duplicateOf"])
                    c["pendingBlueprints"].pop(idx)
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    summary = (f"{agent['name']} approved {bp['name']} as an upgrade to "
                               f"{bp['duplicateOf']} -- {upgrade_summary}")
                    resolved = True
                else:
                    for r in bp["newResources"]:
                        if r["id"] not in c["resourceRegistry"]:
                            c["resourceRegistry"][r["id"]] = {"name": r["name"],
                                                              "gatherZone": r["gatherZone"], "color": r["color"]}
                            # Stamp for orphan custom-resource retirement (CUSTOM_RESOURCE_RETIRE_FRAMES).
                            c.setdefault("customResourceAddedFrame", {})[r["id"]] = self.frameTick
                    c["projectRegistry"][bp["id"]] = {
                        "name": bp["name"], "needs": dict(bp["needs"]),
                        "visualStyle": bp["visualStyle"], "custom": True,
                        "sprite": bp.get("sprite"),
                        "function": dict(bp.get("function") or {}),
                        **({"tier": bp.get("tier") or 1} if TECH_TREE_ENABLED else {}),
                    }
                    c.setdefault("approvedCustomApprovedFrame", {})[bp["id"]] = self.frameTick
                    c["pendingBlueprints"].pop(idx)
                    c["lastBlueprintActivityFrame"] = self.frameTick
                    if decision.get("message"):
                        agent["message"] = decision["message"]
                        agent["messageTimer"] = 180
                    summary = f"{agent['name']} approved {bp['name']} blueprint"
                    lead_name = self._resolve_project_lead(bp.get("proposedBy"))
                    target_district = decision.get("target_district") or bp.get("requestedDistrict")
                    geo_ok, geo_reason = self._district_matches_blueprint_geo(target_district, bp) \
                        if target_district else (False, None)
                    if target_district and not geo_ok:
                        summary += f" (ignored target_district {target_district}: {geo_reason})"
                    district_id = target_district if geo_ok else self._resolve_build_district(
                        agent, bp["id"], None)
                    if district_id and lead_name:
                        contributed = {res: 0 for res in bp["needs"]}
                        c["districtProjects"][district_id] = {
                            "type": bp["id"], "name": bp["name"], "needs": dict(bp["needs"]),
                            "contributed": contributed, "visualStyle": bp["visualStyle"],
                            "sprite": bp.get("sprite"), "districtId": district_id,
                            "lead": lead_name, "leadReassigned": None,
                        }
                        c["districtLastContribution"][district_id] = self.frameTick
                        if lead_name != bp.get("proposedBy"):
                            c["districtProjects"][district_id]["leadReassigned"] = {
                                "from": bp.get("proposedBy"), "to": lead_name, "frame": self.frameTick}
                            self._push_activity(
                                f"{bp.get('proposedBy')} unavailable to lead the {bp['name']} project -- "
                                f"{lead_name} takes over")
                        summary += f", started in {district_id} with {lead_name} as lead"
                    resolved = True
                if resolved and TECH_TREE_ENABLED:
                    self._record_council_verdict(agent, bp, decision)
            else:
                if bp and not review_ok:
                    summary = f"{agent['name']} cannot approve {bp['name']} -- sage review still pending"
                else:
                    summary = f"{agent['name']} could not approve that blueprint"

        elif action == "reject_blueprint":
            idx = next((i for i, p in enumerate(c["pendingBlueprints"]) if p["id"] == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                bp = c["pendingBlueprints"].pop(idx)
                c["rejectedBlueprintIds"].add(bp["id"])
                # Amnesty clock (C3): the rejection expires after
                # BLUEPRINT_AMNESTY_FRAMES via _maybe_amnesty_rejected_blueprints.
                c.setdefault("rejectedBlueprintFrames", {})[bp["id"]] = self.frameTick
                summary = f"{agent['name']} rejected {bp['name']} blueprint"
            else:
                summary = f"{agent['name']} could not reject that blueprint"

        elif action == "propose_role":
            role = decision.get("role")
            ok, reason = self._validate_role(role)
            if ok:
                pending = dict(role)
                pending["specialty"] = list(role["specialty"])
                if isinstance(role["preferredProject"], list):
                    pending["preferredProject"] = list(role["preferredProject"])
                pending["proposedBy"] = agent["name"]
                pending["proposedFrame"] = self.frameTick
                c["pendingRoles"].append(pending)
                summary = f"{agent['name']} proposed the {role['name']} role"
            else:
                summary = f"{agent['name']} drafted an invalid role ({reason})"

        elif action == "approve_role":
            idx = next((i for i, p in enumerate(c["pendingRoles"])
                        if p.get("slug") == decision.get("target")), -1)
            if agent["role"] != "elder" or idx == -1:
                summary = f"{agent['name']} could not approve that role"
            else:
                role = c["pendingRoles"][idx]
                registry = c["roleRegistry"]
                seed_roles = set(self.d["ROLES"])
                emergent_count = len(set(registry) - seed_roles)
                if role["slug"] in registry or emergent_count >= MAX_EMERGENT_ROLES:
                    summary = f"{agent['name']} could not approve the {role['name']} role"
                else:
                    c["pendingRoles"].pop(idx)
                    registry[role["slug"]] = self._role_record(role)
                    self._rebuild_role_maps()
                    summary = f"{agent['name']} approved the {role['name']} role"

        elif action == "reject_role":
            idx = next((i for i, p in enumerate(c["pendingRoles"])
                        if p.get("slug") == decision.get("target")), -1)
            if agent["role"] == "elder" and idx != -1:
                role = c["pendingRoles"].pop(idx)
                summary = f"{agent['name']} rejected the {role['name']} role"
            else:
                summary = f"{agent['name']} could not reject that role"

        elif action == "assign_task":
            target = self._find_agent(decision.get("target"))
            if agent["role"] == "elder" and target and self._is_idle(target) and decision.get("message"):
                task_text = _clean_task_text(decision["message"], target["name"])
                target["assignedTask"] = task_text
                target["lastTaskedFrame"] = self.frameTick
                # Deliberately NOT written to c["directive"]: that field is
                # broadcast to every agent's prompt with "Prioritize it", and a
                # per-agent task there sent the whole roster chasing one
                # villager's errand (measured 83% move_to_district sessions).
                self._push_communication("directive", agent["name"], target["name"], task_text)
                self._deliver_message(agent["name"], target["name"], task_text, "directive")
                summary = f"Elder {agent['name']} tasked {target['name']}: {task_text}"
            else:
                summary = f"{agent['name']} could not assign that task"

        elif action == "change_role":
            if decision.get("new_role"):
                agent["role"] = decision["new_role"]
                summary = f"{agent['name']} became a {decision['new_role']}"

        elif action == "switch_role":
            new_role = decision.get("new_role") or decision.get("target")
            if EMERGENT_ROLES and new_role and new_role in c["roleRegistry"] and new_role != agent["role"]:
                old = agent["role"]
                agent["role"] = new_role
                agent["assignedTask"] = None
                agent["idleCycles"] = 0
                summary = f"{agent['name']} switched role from {old} to {new_role}"
            else:
                summary = f"{agent['name']} kept the {agent['role']} role"

        elif action == "propose_rule":
            summary = self._propose_rule(agent, decision)

        elif action == "repeal_rule":
            summary = self._propose_repeal(agent, decision)

        elif action == "vote_rule":
            summary = self._vote_on_rule(agent, decision)

        elif action == "heal_agent":
            patient = self._find_agent(decision.get("target")) if decision.get("target") else None
            dead_target = patient["name"] if patient is not None and patient.get("deathFrame") is not None else None
            if dead_target:
                patient = None
            if not patient or (patient["health"] >= 100 and not patient["incapacitated"]):
                patient = self._neediest_nearby(agent)
            if not patient:
                summary = (f"{agent['name']} cannot revive {dead_target} — they have passed away"
                           if dead_target else f"{agent['name']} found no one to heal")
            elif self._distance_to(agent, patient) > 80:
                self._auto_move_toward_target(agent, patient["name"])
                summary = f"{agent['name']} moves to help {patient['name']}"
            else:
                boost = HEAL_AMOUNT * 2 if agent["role"] == "healer" else HEAL_AMOUNT
                if CULTURE_ENABLED:
                    boost += self._skill_level(agent, "heal") * SKILL_HEAL_BONUS_PER_LEVEL
                patient["health"] = min(100, patient["health"] + boost)
                donate = self._first_edible(agent) if patient["incapacitated"] else None
                if donate:
                    agent["resources"][donate] -= 1
                    patient["resources"][donate] = patient["resources"].get(donate, 0) + 1
                    patient["hunger"] = min(100, patient["hunger"] + FOOD_RESTORE)
                if patient["incapacitated"] and patient["health"] > 0:
                    patient["incapacitated"] = False
                    patient["hunger"] = max(patient["hunger"], REVIVE_HUNGER)
                    self._push_activity(f"{patient['name']} was revived by {agent['name']}")
                self._nudge_ally(agent, patient["name"])
                self._push_memory(patient, f"Healed by {agent['name']}")
                if CULTURE_ENABLED:
                    self._practice_skill(agent, "heal")
                summary = f"{agent['name']} healed {patient['name']}"

        elif action == "bury_agent":
            corpse = self._find_agent(decision.get("target")) if decision.get("target") else None
            if corpse is not None and (corpse.get("deathFrame") is None or corpse.get("buried")):
                corpse = None
            if not corpse:
                corpse = self._nearest_unburied_corpse(agent)
            if not corpse:
                summary = f"{agent['name']} found no one awaiting burial"
            else:
                cemeteries = self._working_cemeteries()
                if not cemeteries:
                    agent["lastBurialRejection"] = {
                        "reason": "no cemetery has been built yet",
                        "frame": self.frameTick,
                    }
                    summary = f"{agent['name']} wants to bury {corpse['name']} but no cemetery exists"
                elif self._distance_to(agent, corpse) > BURY_CONTACT_DIST:
                    self._auto_move_toward_target(agent, corpse["name"])
                    summary = f"{agent['name']} moves to lay {corpse['name']} to rest"
                else:
                    cemetery = min(cemeteries, key=lambda s: self._distance_to(agent, s))
                    self._bury_agent_at(cemetery, corpse, buried_by=agent)
                    summary = f"{agent['name']} buried {corpse['name']} in the Cemetery"

        elif action == "place_block":
            gx = gy = None
            target = decision.get("target") or ""
            if "," in str(target):
                parts = str(target).split(",")
                try:
                    gx, gy = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            block_type = decision.get("message") or decision.get("new_role") or "wall"
            if target and "," not in str(target):
                block_type = target
            summary = self._place_block(agent, block_type, gx, gy)

        elif action == "remove_block":
            gx = gy = None
            target = decision.get("target") or ""
            if "," in str(target):
                parts = str(target).split(",")
                try:
                    gx, gy = int(parts[0]), int(parts[1])
                except ValueError:
                    pass
            summary = self._remove_block(agent, gx, gy)

        elif action == "dig_terrain":
            summary = self._dig_terrain(agent)

        elif action == "plant_terrain":
            summary = self._plant_terrain(agent)

        elif action == "propose_treaty":
            summary = self._propose_treaty(agent, decision)

        elif action == "vote_treaty":
            summary = self._vote_treaty(agent, decision)

        elif action == "deliver_caravan":
            summary = self._deliver_caravan_action(agent, decision)

        elif action == "offer_contract":
            if not CONTRACTS_ENABLED:
                summary = f"{agent['name']} cannot offer a contract — contracts are disabled"
            else:
                summary = self._apply_offer_contract(agent, decision)

        elif action == "accept_contract":
            if not CONTRACTS_ENABLED:
                summary = f"{agent['name']} cannot accept a contract — contracts are disabled"
            else:
                summary = self._apply_accept_contract(agent, decision)

        elif action == "council_speak":
            summary = self._council_speak(agent, decision)

        elif action == "council_propose":
            summary = self._council_propose(agent, decision)

        elif action == "council_vote":
            summary = self._council_vote(agent, decision)

        # rest / default: summary already set

        # Missing-case counting: the decision's action never reached
        # submit_structure_sprite (typically because server.py's
        # normalize_decision() rejected the model's sprite reply and
        # substituted a _fallback-stamped role fallback action). Only count
        # _fallback-stamped substitutions -- _think_job's infra/network path
        # applies bare {"action": "rest"} with no _fallback stamp. Only count
        # if the pending turn is still the exact same object -- if
        # _upgrade_structure (or anything else) already replaced/cleared
        # spriteDesignTurn during dispatch above, don't double-count it here.
        if (pending_sprite_turn is not None
                and action != "submit_structure_sprite"
                and agent.get("spriteDesignTurn") is pending_sprite_turn
                and decision.get("_fallback")):
            sid = pending_sprite_turn.get("structureId")
            s = next((x for x in c["structures"] if x.get("id") == sid), None)
            self._count_sprite_design_failure(agent, s or pending_sprite_turn.get("structureName"))

        agent["lastAction"] = action
        agent["lastReasoning"] = decision.get("reasoning")
        if THEORY_OF_MIND_ENABLED:
            self._score_peer_prediction(agent, action)
        agent["actionCounts"][action] = agent["actionCounts"].get(action, 0) + 1
        if action not in ("rest", "talk_to_nearby", "assign_task"):
            agent["assignedTask"] = None
            agent["idleCycles"] = 0
        if agent.get("commitment"):
            commitment = agent["commitment"]
            if resource_acted and resource_acted == commitment.get("resource"):
                agent["commitment"] = None
                self._push_activity(f"{agent['name']} honored a promise to {commitment['to']}")
            elif self.frameTick - commitment.get("madeAt", self.frameTick) > COMMITMENT_EXPIRE_FRAMES:
                agent["commitment"] = None
        self._push_memory(agent, summary)

        ru = decision.get("relationship_update")
        if isinstance(ru, dict):
            agent["relationships"].update(ru)
            if ru:
                self._mark_top_dirty("socialTies")

        self._push_activity(summary)
        # A successful action can change the actor's material context, role,
        # or beliefs.  Village-wide project/rule events can change every
        # agent's desire/reflection, so make all notes eligible then.
        if action != "rest":
            self._mark_context_dirty(agent)
        if action in {"start_project", "build_structure", "contribute_resources",
                      "propose_rule", "vote_rule", "repeal_rule", "switch_role",
                      "found_belief", "propose_blueprint", "approve_blueprint",
                      "reject_blueprint", "sage_review_blueprint",
                      "council_speak", "council_propose", "council_vote"}:
            self._mark_all_context_dirty()
        self._capture_burning_bush_reply(agent, decision)
        self._mark_agent_dirty(agent)
        if action in ("collect_resource", "contribute_resources", "trade_resource", "craft_item",
                      "repair_structure", "upgrade_structure", "build_structure", "start_project",
                      "propose_rule", "vote_rule", "repeal_rule", "propose_blueprint",
                      "approve_blueprint", "reject_blueprint", "sage_review_blueprint"):
            self._mark_civ_dirty("stockpile", "districtProjects", "rules", "pendingRules",
                                 "pendingBlueprints", "level")
        if CONTRACTS_ENABLED and action in ("offer_contract", "accept_contract"):
            self._mark_civ_dirty("contracts", "contractEscrow", "stockpile")
        return summary

    def _resolve_talk_target(self, agent, decision):
        target = decision.get("target")
        if target and target in self.agent_names:
            return target
        nearby = self._get_nearby_agents(agent)
        return nearby[0] if nearby else "everyone"

    # --- goals (#1) ---
    def _goal_for_decision(self, decision):
        if not USE_GOALS or not decision:
            return None
        a = decision.get("action")
        district = decision.get("target_district")
        if a == "collect_resource":
            return {"kind": "gather", "target": decision.get("target"), "district": district, "ttl": 8}
        if a == "contribute_resources":
            return {"kind": "deliver", "target": decision.get("target"), "district": district, "ttl": 6}
        if a == "craft_item":
            return {"kind": "craft", "target": decision.get("target"), "ttl": 6}
        if a == "build_structure":
            return {"kind": "build", "target": None, "district": district, "ttl": 6}
        if a == "deliver_caravan":
            dest = decision.get("target_district") or decision.get("target")
            return {
                "kind": "caravan",
                "target_district": dest,
                "source_settlement": None,
                "ttl": STALL_THRESHOLD * 4,
            }
        return None

    def _step_goal(self, agent):
        g = agent["goal"]
        if not g:
            return False
        if agent["incapacitated"]:
            agent["goal"] = None
            return False
        g["ttl"] -= 1
        if g["ttl"] < 0:
            agent["goal"] = None
            return False
        if g["kind"] == "craft_gather":
            return self._step_craft_gather_goal(agent, g)
        if g["kind"] == "plant_terrain":
            self.apply_decision(agent, {"action": "plant_terrain", "reasoning": "goal:plant"})
            agent["goal"] = None
            return True
        if g["kind"] == "seek_shelter":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_agent_target_once(agent, dest)
                return True
            agent["goal"] = None
            return False
        if g["kind"] == "dig_relocate":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_agent_target_once(agent, dest)
                return True
            summary = self._dig_terrain(agent) or ""
            if "cannot dig" in summary:
                agent["goal"] = None
                return False
            if agent["resources"].get("stone", 0) >= self._carry_cap(agent):
                # Full load: release the goal so the LLM/fallback can route
                # the stone to a project via contribute_resources.
                agent["goal"] = None
                return False
            return True
        if g["kind"] == "caravan":
            dest = g.get("target_district")
            if dest and agent.get("currentDistrict") != dest:
                self._set_caravan_target(agent, dest)
                return True
            self._maybe_caravan_goal(agent)
            agent["goal"] = None
            return False
        if g["kind"] == "hunt":
            prey_id = g.get("target")
            creature = self._find_wildlife_by_id(prey_id)
            if creature is None or not creature.get("alive"):
                agent["goal"] = None
                return False
            if _dist(agent["x"], agent["y"], creature["x"], creature["y"]) > HUNT_RADIUS:
                agent["goal"] = None
                return False
            self.apply_decision(agent, {
                "action": "hunt_wildlife",
                "target": str(prey_id),
                "message": None,
                "reasoning": "goal:hunt",
            })
            if not creature.get("alive"):
                agent["goal"] = None
            return True
        if g["kind"] == "repair":
            target_id = g.get("target")
            structure = next(
                (s for s in self.civilization["structures"] if s.get("id") == target_id),
                None,
            )
            if structure is None:
                agent["goal"] = None
                return False
            did = structure.get("districtId")
            if did and agent.get("currentDistrict") != did:
                self._set_agent_target_once(agent, did)
                return True
            summary = self.apply_decision(agent, {
                "action": "repair_structure",
                "target": str(target_id),
                "reasoning": f"goal:repair {target_id}",
            })
            s = summary or ""
            if any(t in s for t in ("lacks ", "found nothing", "nothing needs repair")):
                agent["goal"] = None
                return False
            if "repaired" in s.lower() or "rebuilt" in s.lower():
                agent["goal"] = None
            return True
        district_id = g.get("district") or self._resolve_contribution_district(agent)
        if g["kind"] in ("gather", "deliver", "build") and not district_id:
            agent["goal"] = None
            return False
        if g["kind"] == "gather" and not self._first_unmet_project_resource(district_id):
            agent["goal"] = None
            return False
        action_by_kind = {"gather": "collect_resource", "deliver": "contribute_resources",
                          "craft": "craft_item", "build": "build_structure"}
        action = action_by_kind.get(g["kind"])
        if not action:
            agent["goal"] = None
            return False
        summary = self.apply_decision(agent, {"action": action, "target": g.get("target"),
                                              "target_district": district_id,
                                              "message": None, "reasoning": f"goal:{g['kind']}"})
        s = summary or ""
        if any(t in s for t in ("has nothing to contribute", "found nothing", "nothing to craft",
                                "lacks ", "built ", "could not", "cannot dig")):
            agent["goal"] = None
            return False
        return True

    def _apply_rule_based_fallback(self, agent):
        council = self.civilization.get("dailyCouncil")
        if council and agent["name"] in (council.get("attendees") or []):
            phase = council.get("phase")
            if phase == "discussion":
                ballot = council.get("ballot") or {}
                succession = ballot.get("kind") == "succession"
                candidates = ", ".join(ballot.get("candidates") or [])
                self._apply_gated_decision(agent, {
                    "action": "council_speak",
                    "message": (
                        f"We should compare {candidates} by judgment, care, and service."
                        if succession else
                        "We should preserve essential supplies and finish shared work."
                    ),
                    "feeling": "hopeful",
                    "topic": "leadership_vacancy" if succession else "world_status",
                    "reasoning": "Deterministic offline council fallback.",
                })
                return
            if phase == "proposal" and not council.get("ballot"):
                self._apply_gated_decision(agent, {
                    "action": "council_propose", "kind": "idea",
                    "title": "Protect essentials",
                    "detail": "Keep food and health secure while finishing the most-stalled project.",
                    "reasoning": "Deterministic offline council fallback.",
                })
                return
            if phase == "voting" and agent["name"] not in (
                    (council.get("ballot") or {}).get("votes") or {}):
                self._apply_gated_decision(agent, {
                    "action": "council_vote", "vote": "abstain",
                    "reasoning": "Deterministic offline council fallback.",
                })
                return
            if phase == "verdict":
                self._apply_gated_decision(agent, {
                    "action": "council_speak",
                    "message": f"The council ratifies: "
                               f"{(council.get('verdict') or {}).get('outcome', 'the result')}.",
                    "feeling": "resolute", "topic": "verdict",
                    "reasoning": "Deterministic offline council fallback.",
                })
                return
        district_id = random.choice(list(self.civilization["districts"].keys()))
        self._set_agent_target(agent, district_id)
        self._push_memory(agent, f"{agent['name']} wandered toward {district_id}")
        self._push_activity(f"{agent['name']} wandered toward {district_id} (LLM fallback)")

    def _should_renudge(self, agent, kind, rejection_frame):
        """Per-kind rejection-nudge cooldown for P2 recovery notes.

        Without this, a rejection note re-fires on every think turn for the
        entire DIRECTIVE_TTL_FRAMES window even when nothing has changed,
        crowding out the fixed MAX_BEHAVIOR_NUDGES slots. Re-emit only if
        this is a NEW rejection (different frame than last nudged) or the
        cooldown has fully elapsed since this kind was last nudged.
        """
        last_nudged = agent.setdefault("lastRejectionNudgeFrame", {})
        last_frame_for_kind = last_nudged.get(kind)
        if last_frame_for_kind is None:
            allow = True
        elif rejection_frame != last_frame_for_kind.get("rejectionFrame"):
            allow = True
        elif self.frameTick - last_frame_for_kind.get("nudgedFrame", 0) >= DIRECTIVE_TTL_FRAMES:
            allow = True
        else:
            allow = False
        if allow:
            last_nudged[kind] = {"rejectionFrame": rejection_frame, "nudgedFrame": self.frameTick}
        return allow

