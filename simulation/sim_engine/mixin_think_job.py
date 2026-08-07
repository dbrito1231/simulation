"""Phase 6e mixin: LLM think-job + per-frame tick loop slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_build_think_payload` through
`stop` (formerly core.py lines ~1793-3404). Covers: the large ~840-line
`_build_think_payload` LLM-context builder (kept as a single undivided
method — splitting its body would be a logic change, not a pure move),
PIANO module helpers (context dirtying, always-on module reports, module
pulsing/throttling), the `_think_job` worker-pool callback and
`_schedule_think` dispatch, and the per-frame tick (`_tick_once`,
`_run_loop`, `start`, `stop`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _ThinkJobMixin:
    """Mixin slice of SimEngine: LLM think-payload building, PIANO module
    orchestration, think-job dispatch/execution, and the per-frame tick
    loop. See module docstring for exact scope."""

    # --- LLM think job (runs in worker; builds payload, calls LM, applies) ---
    def _build_think_payload(self, agent):
        """Mirror index.html thinkAgent payload, computed under lock."""
        c = self.civilization
        nearby_detailed = self._get_nearby_detailed(agent)
        idle_agents = []
        if agent["role"] == "elder":
            # C3: cap at MAX_IDLE_AGENTS_PROMPT -- _idle_agents_for_elder is
            # already ordered least-recently-tasked first, so the slice keeps
            # the agents most in need of a task.
            for i, a in enumerate(self._idle_agents_for_elder()[:MAX_IDLE_AGENTS_PROMPT]):
                idle_agents.append({
                    "name": a["name"], "role": a["role"], "longest_idle": i == 0,
                    "contribution_debt": self.frameTick - (a["lastContributedFrame"] or 0),
                })

        actives = self._active_project_districts()
        invention_required = self._invention_required()
        voice_guidance = self._active_voice_guidance(agent)
        voice_guidance_active = bool(voice_guidance.get("voice_guidance_active"))
        # One-shot invention-only turn (set by _maybe_invention_backstop):
        # the server swaps in a slim, proposal-only prompt for this call.
        invention_turn = bool(agent.get("inventionTurn")) and not voice_guidance_active
        # inventionBuildContext deliberately survives past this point (unlike
        # inventionTurn) -- it's read later in apply_decision's propose_blueprint
        # branch, which runs after the async LLM round-trip, and clearing it
        # here would erase it before that branch ever sees it.
        invention_build_context = dict(agent["inventionBuildContext"]) \
            if invention_turn and agent.get("inventionBuildContext") else None
        if invention_turn:
            agent["inventionTurn"] = False
        elif voice_guidance_active:
            self._cancel_voice_blocked_special_turns([agent["id"]])
        sprite_design_turn = bool(agent.get("spriteDesignTurn")) and not voice_guidance_active
        if voice_guidance_active and agent.get("spriteDesignTurn"):
            agent["spriteDesignTurn"] = None
        # A saved mid-meeting session may still exist after the rollback flag
        # is switched off. Treat it as inert: no council prompt/actions may be
        # offered until the feature is explicitly re-enabled.
        daily_council = c.get("dailyCouncil") if DAILY_COUNCIL_ENABLED else None
        council_seated = bool(
            daily_council
            and agent["name"] in (daily_council.get("attendees") or [])
            and any(
                seat.get("name") == agent["name"]
                and math.hypot(agent["x"] - seat["x"], agent["y"] - seat["y"]) <= 5.0
                for seat in daily_council.get("seats") or []
            )
        )
        council_turn = bool(agent.get("councilTurn") and council_seated)
        if council_turn:
            agent["councilTurn"] = False
        nudges = []

        def note(prio, text):
            """Collect a (priority, text) nudge. Lower prio = more important.
            P0=emergency/survival, P1=governance/commitment,
            P2=rejection-recovery/stall, P3=opportunity/idle. Selection into
            the final behavior_nudge happens once, below, via MAX_BEHAVIOR_NUDGES."""
            nudges.append((prio, text))

        # Phase A1 (shadow-log only): tracked alongside the nudges below and
        # folded into high_stakes_reason near the end of this function. Purely
        # additive -- none of these locals feed a nudge or a decision.
        fresh_rejection_kinds = set()
        emergency_active = False
        elder_blueprint_review_active = False
        rejection = agent.get("lastBlueprintRejection")
        rejection_nudge = None
        if rejection and self.frameTick - rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("blueprint")
            rejection_nudge = (f"NOTE: Your last blueprint proposal was rejected: {rejection['reason']}. "
                               f"Propose a different blueprint that avoids that problem.")
            rejection_nudge += " Use a fresh non-seed id; never reuse a seed, approved, pending, or rejected id."
            note(2, rejection_nudge)
        gather_rejection = agent.get("lastGatherRejection")
        if gather_rejection and self.frameTick - gather_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("gather")
            if self._should_renudge(agent, "gather", gather_rejection.get("frame", 0)):
                reason_text = gather_rejection.get("reason") or ""
                if "pick" in reason_text:
                    note(2, f"NOTE: Your last gather failed: {reason_text}. "
                            f"Craft the required pick at the workshop (craft_item), or dig_terrain for stone.")
                else:
                    note(2, f"NOTE: Your last gather failed: {reason_text}. "
                            f"Contribute to an active terraform or start_terraform here before moving elsewhere.")
        terraform_rejection = agent.get("lastTerraformRejection")
        if terraform_rejection and self.frameTick - terraform_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("terraform")
            note(2, f"NOTE: Your last start_terraform failed: {terraform_rejection['reason']}. "
                    f"Use a template id (plant_grove/clear_field/extend_beach) or name the district.")
        craft_rejection = agent.get("lastCraftRejection")
        if craft_rejection and self.frameTick - craft_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("craft")
            if self._should_renudge(agent, "craft", craft_rejection.get("frame", 0)):
                note(2, f"NOTE: Your last craft failed: {craft_rejection['reason']}. "
                        f"Gather the missing input first.")
        if TECH_TREE_ENABLED:
            recipe_rejection = agent.get("lastRecipeRejection")
            if recipe_rejection and self.frameTick - recipe_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("recipe")
                if self._should_renudge(agent, "recipe", recipe_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last recipe proposal was refused: {recipe_rejection['reason']}.")
        project_rejection = agent.get("lastProjectRejection")
        if project_rejection and self.frameTick - project_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            fresh_rejection_kinds.add("project")
            if self._should_renudge(agent, "project", project_rejection.get("frame", 0)):
                note(2, f"NOTE: Your last start_project failed: {project_rejection['reason']}.")
        if STRUCTURE_UPGRADES_ENABLED:
            upgrade_rejection = agent.get("lastUpgradeRejection")
            if upgrade_rejection and self.frameTick - upgrade_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("upgrade")
                if self._should_renudge(agent, "upgrade", upgrade_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last upgrade failed: {upgrade_rejection['reason']}.")
            sprite_rejection = agent.get("lastSpriteRejection")
            if sprite_rejection and self.frameTick - sprite_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("sprite")
                note(2, f"NOTE: Your last sprite design was rejected: {sprite_rejection['reason']}.")
            upgradeable = self._upgradeable_structures_brief()
            if upgradeable and not sprite_design_turn:
                sample = upgradeable[:3]
                parts = ", ".join(
                    f"{u['name']} id {u['id']} Lv.{u['level']}" for u in sample)
                note(3,
                    f"NOTE: Upgrade existing facilities before building duplicates. "
                    f"Use upgrade_structure (target = structure id): {parts}.")
        if GOODS_ENABLED:
            repair_rejection = agent.get("lastRepairRejection")
            if repair_rejection and self.frameTick - repair_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                fresh_rejection_kinds.add("repair")
                if self._should_renudge(agent, "repair", repair_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last repair failed: {repair_rejection['reason']}.")
            spoilage = c.get("lastSpoilage")
            if spoilage and self.frameTick - spoilage.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(3, f"NOTE: {spoilage['reason']}.")
            shelter = agent.get("lastShelterNote")
            if shelter and self.frameTick - shelter.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(3, f"NOTE: {shelter['reason']}. More houses would fix this.")
            worst_local = min((s for s in c["structures"]
                               if s.get("districtId") == agent.get("currentDistrict")
                               and (s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD)),
                              key=lambda s: s.get("condition", 100), default=None)
            if worst_local:
                is_ruin = bool(worst_local.get("isRuin"))
                state_word = "in ruins" if is_ruin else "in disrepair and not working"
                note(1 if is_ruin else 2,
                     f"NOTE: The {worst_local.get('name') or worst_local.get('type')} here is "
                     f"{state_word} (condition {int(worst_local.get('condition', 0))}). "
                     f"Use repair_structure to restore it.")
            # Village-wide ruin-pressure nudge (P1): independent of the
            # agent's current district, fires when decay is widespread even
            # if the agent isn't standing next to the worst offender. Two
            # triggers: >25% of all structures are ruins, or an entire
            # structure category (house/market/workshop/foundry/granary/
            # farm_plot) has zero working instances village-wide.
            all_structures = c["structures"]
            if all_structures:
                ruin_count = sum(1 for s in all_structures if s.get("isRuin"))
                ruin_ratio_trigger = (ruin_count / len(all_structures)) > 0.25
                zero_working_category = False
                for kind in ("house", "market", "workshop", "foundry", "granary", "farm_plot"):
                    of_kind = [s for s in all_structures if s.get("type") == kind]
                    if of_kind and not any(
                            not s.get("isRuin") and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                            for s in of_kind):
                        zero_working_category = True
                        break
                if ruin_ratio_trigger or zero_working_category:
                    failing = sorted(
                        (s for s in all_structures
                         if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD),
                        key=lambda s: s.get("condition", 100))
                    worst_few = failing[:3]
                    if worst_few:
                        parts = ", ".join(
                            f"{s.get('name') or s.get('type')} ({s.get('districtId')}, "
                            f"condition {int(s.get('condition', 0))})" for s in worst_few)
                        note(1, f"NOTE: The village has {len(failing)} ruined/failing structures "
                                f"village-wide, including {parts}. Travel there and use repair_structure.")
        if ECONOMY_ENABLED:
            trade_rejection = agent.get("lastTradeRejection")
            if trade_rejection and self.frameTick - trade_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                if self._should_renudge(agent, "trade", trade_rejection.get("frame", 0)):
                    note(2, f"NOTE: Your last trade was refused: {trade_rejection['reason']}.")
            if not agent.get("homeStructureId") \
                    and (self.frameTick - (agent.get("lastHomelessNudgeFrame") or -HOMELESS_NUDGE_FRAMES)) \
                    >= HOMELESS_NUDGE_FRAMES:
                claimable = self._find_house_to_claim(agent)
                agent["lastHomelessNudgeFrame"] = self.frameTick
                if claimable:
                    note(3, "NOTE: You have no home, but an unclaimed house exists. "
                            "Be the one to repair_structure it (if damaged) or help build the "
                            "next house to claim it as your own.")
                else:
                    note(3, "NOTE: You have no home and no house is unclaimed. "
                            "Consider start_project to build a house -- the builder claims it.")
        if LIFECYCLE_ENABLED:
            quota_rejection = agent.get("lastQuotaRejection")
            if quota_rejection and self.frameTick - quota_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {quota_rejection['reason']}. "
                        f"Try a different resource or district, or wait for the quota to reset.")
            ration_rejection = agent.get("lastRationingRejection")
            if ration_rejection and self.frameTick - ration_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {ration_rejection['reason']}. "
                        f"Gather more or wait for storage to recover.")
            pending_succession = c.get("pendingSuccession")
            if pending_succession and agent["name"] not in \
                    next((r["votes"] for r in c["pendingRules"]
                         if r["kind"] == "succession"
                         and r.get("electionId") == pending_succession["electionId"]
                         and r.get("candidateName") == agent["name"]), {}):
                # An agent votes with vote_rule targeting the candidate's own
                # succession rule id (not the election as a whole) -- listing
                # every candidate's rule id here so the model has what it
                # needs without a new action verb.
                candidate_ids = ", ".join(
                    f"{r['candidateName']} (id {r['id']})" for r in c["pendingRules"]
                    if r["kind"] == "succession" and r.get("electionId") == pending_succession["electionId"])
                note(1, f"NOTE: The village elder has died. Vote for the next elder with vote_rule: "
                        f"{candidate_ids}. Set target to your preferred candidate's id and vote yes.")
        if CEMETERY_ENABLED:
            burial_rejection = agent.get("lastBurialRejection")
            if burial_rejection and self.frameTick - burial_rejection.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                note(2, f"NOTE: {burial_rejection['reason']}. "
                        f"Use start_project with target cemetery to build one.")
            unburied = next((a for a in self.agents
                             if a.get("deathFrame") is not None and not a.get("buried")), None)
            if unburied:
                if self._working_cemeteries():
                    note(3, f"NOTE: {unburied['name']} awaits burial. "
                            f"Use bury_agent (target {unburied['name']}) to lay them to rest in the Cemetery.")
                else:
                    note(3, f"NOTE: {unburied['name']} awaits burial but the village has no Cemetery yet. "
                            f"Use start_project with target cemetery.")
        abandonment = c.get("lastProjectAbandonment")
        if abandonment and self.frameTick - abandonment.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
            note(2, f"NOTE: {abandonment['reason']}.")
        stalled_customs = self._stalled_approved_customs()
        if stalled_customs:
            pid, name, _ = stalled_customs[0]
            note(2, f"NOTE: The village approved {name} but never built it. "
                    f"Use start_project with target {pid}.")
        if ECOLOGY_ENABLED:
            stocks_line = self._format_district_stocks_for_prompt(agent)
            if ":depleted" in stocks_line or ":low" in stocks_line:
                note(3, f"NOTE: Local stocks are strained ({stocks_line}). "
                        f"Consider start_terraform (plant_grove/clear_field/extend_beach) or move_to_district.")
        if agent["assignedTask"] and \
                self.frameTick - (agent.get("lastTaskedFrame") or 0) > DIRECTIVE_TTL_FRAMES:
            # Same staleness problem as the directive: an old task (possibly
            # restored from state.db) shouldn't bias decisions forever.
            agent["assignedTask"] = None
        if agent["assignedTask"]:
            note(1, f"Your leader assigned you: {agent['assignedTask']}. Do it now.")
        if invention_required:
            note(1, "NOTE: All known structures are already built. The village needs a NEW "
                    "invention -- use propose_blueprint now.")
        ready = next((did for did in actives if self._is_project_complete(did)), None)
        if ready:
            note(3, f"PROJECT READY: the build in {ready} is fully funded. "
                    f"Use build_structure with target_district {ready} now.")
        if agent.get("commitment"):
            commitment = agent["commitment"]
            note(1, f'NOTE: You agreed to help {commitment["to"]}: "{commitment["text"]}". '
                    f'Honor it soon with collect_resource, contribute_resources, or '
                    f'trade_resource for {commitment["resource"]}.')
        if not actives:
            # Suppressed while invention is required: start_project would be
            # refused anyway, and the nudge pulls the model away from
            # propose_blueprint (the only action that unblocks progress).
            if not invention_required:
                note(3, "NOTE: No active project exists anywhere. Use start_project now to begin a build "
                        "(optionally set target_district to one of the known_districts ids).")
        elif agent["consecutiveTalks"] >= 2:
            note(3, "NOTE: You have chatted twice. Prioritize collect_resource, contribute_resources, or move_to_agent.")
        directive = self._current_directive()
        if agent["role"] != "elder" and directive:
            note(1, f"Your leader directs: {directive}. Prioritize it.")
        if agent.get("consecutiveIdleMoves", 0) >= 3:
            note(3, "NOTE: You have been moving without acting. Prioritize collect_resource or contribute_resources.")
        carry_cap = self._carry_cap(agent)
        capped = next(((k, v) for k, v in agent["resources"].items() if v >= carry_cap), None)
        if capped:
            note(3, f"NOTE: You are at capacity for {capped[0]} ({capped[1]}/{carry_cap}). "
                    f"Use contribute_resources or trade_resource instead of collecting more.")
        spec = self._role_specialty_resource(agent["role"])
        if spec and spec == self._first_unmet_resource_anywhere():
            note(3, f"NOTE: Your role specializes in {spec}, which an active project still needs. Prioritize collect_resource.")
        if EMERGENT_ROLES:
            need_role = self._village_needed_role()
            if need_role and need_role != agent["role"] and self._is_flexible_role(agent["role"]):
                unmet = self._first_unmet_resource_anywhere()
                if unmet:
                    note(3,
                        f"NOTE: No one is gathering {unmet}, which a build needs. "
                        f"Consider switch_role to {need_role} to fill the gap.")
                else:
                    note(3,
                        f"NOTE: The village needs a {need_role} (survival or scarce "
                        f"resources). Consider switch_role to {need_role} to fill the gap.")
        if RULES_ENABLED:
            unvoted = next((r for r in c["pendingRules"] if agent["name"] not in r["votes"]), None)
            if unvoted:
                note(1, f'NOTE: Pending rule "{unvoted["name"]}" (id {unvoted["id"]}) needs your vote. '
                        f"Use vote_rule with target {unvoted['id']} and vote yes or no.")
            elif (not c["rules"] and not c["pendingRules"]
                  and self.frameTick - c["lastRuleActivityFrame"] > BLUEPRINT_STALL_THRESHOLD):
                note(3, "NOTE: The village has no shared rules yet. Consider propose_rule (a small resource_tax builds a shared stockpile).")
        if agent["role"] == "elder" and c["pendingBlueprints"]:
            # Was gated at >=2 (the comparative council judgment): a LONE
            # valid proposal got no nudge at all and could sit unreviewed
            # indefinitely -- the elder's only other path to it was the
            # fallback-on-decision-failure branch in role_fallback_action,
            # which only fires by accident. Found live 2026-07-08: Marco's
            # "Storage House" validated on his own invention-only turn but
            # was never surfaced back to him because it was the only one
            # pending. Now covers n=1 too, with matching singular wording.
            #
            # SAGE_REVIEW_ENABLED splits the queue into three buckets: still
            # needs a geography/resource review pass, cleared and awaiting a
            # verdict, or denied at review (no action offered -- it expires on
            # its own via _maybe_amnesty_denied_sage_reviews).
            needs_review = [b for b in c["pendingBlueprints"]
                            if SAGE_REVIEW_ENABLED and b.get("sageReview", "pending") == "pending"]
            ready = [b for b in c["pendingBlueprints"]
                     if not SAGE_REVIEW_ENABLED or b.get("sageReview") in ("approved", "skipped")]
            denied = [b for b in c["pendingBlueprints"] if b.get("sageReview") == "denied"]
            elder_blueprint_review_active = bool(needs_review or ready)
            if needs_review:
                # C3: cap rendered briefs per bucket -- MAX_PENDING_BLUEPRINTS
                # already loosely bounds the queue, so this is mostly a
                # safeguard against a bucket absorbing the whole queue.
                shown = needs_review[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(needs_review) - len(shown)
                briefs = "; ".join(
                    f"{b['id']} by {b['proposedBy']} (needs "
                    + ", ".join(f"{k} {v}" for k, v in (b.get('needs') or {}).items())
                    + f"; {self._function_summary(b.get('function'))}"
                    + (f"; duplicates {b['duplicateOf']}" if b.get("duplicateOf") else "")
                    + ")"
                    for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                note(1,
                    f"BLUEPRINT NEEDS SAGE REVIEW: {briefs}. Check district stock shortages, "
                    f"gather-zone availability, existing producers, and structure distribution "
                    f"({self._sage_review_geo_context()}), then use sage_review_blueprint "
                    f"(target = its id, sage_decision = approve or deny).")
            if ready:
                shown = ready[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(ready) - len(shown)
                briefs = "; ".join(
                    f"{b['id']} by {b['proposedBy']}"
                    + (f" [sage: {b['sageReviewReason']}]" if b.get("sageReviewReason") else "")
                    + (f" [duplicates {b['duplicateOf']} -- approving upgrades it instead of "
                       f"building new]" if b.get("duplicateOf") else "")
                    for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                if len(ready) == 1:
                    note(1,
                        f"BLUEPRINT AWAITS YOUR VERDICT: {briefs}. Use approve_blueprint "
                        f"(target = its id, optionally target_district) if it serves the village, "
                        f"or reject_blueprint with a one-line reason if not.")
                else:
                    note(1,
                        f"COUNCIL VERDICT NEEDED: {len(ready)} blueprint proposals "
                        f"compete: {briefs}. Compare them and approve the BEST with approve_blueprint "
                        f'(target = its id), rejecting the rest IN THE SAME decision by adding '
                        f'"verdict": {{"rejections": {{"<id>": "<one-line reason it lost>"}}}}.')
            if denied and not needs_review and not ready:
                shown = denied[:MAX_BLUEPRINT_BRIEFS]
                overflow = len(denied) - len(shown)
                briefs = "; ".join(f"{b['id']} ({b.get('sageReviewReason') or 'no reason given'})"
                                   for b in shown)
                if overflow > 0:
                    briefs += f"; (+{overflow} more)"
                note(1, f"NOTE: Sage denied {briefs} -- it cannot be approved as-is; "
                        f"it will expire on its own.")
        if agent["role"] == "elder" and actives:
            stalled_district = next((did for did in actives
                                     if self.frameTick - c["districtLastContribution"].get(did, 0) > STALL_THRESHOLD), None)
            if stalled_district:
                stalled = self._first_unmet_project_resource(stalled_district)
                if stalled:
                    holders = sorted((a for a in self.agents if a["resources"].get(stalled, 0) > 0),
                                     key=lambda a: a["resources"].get(stalled, 0), reverse=True)
                    holder = holders[0]["name"] if holders else "no one"
                    note(2, f"NOTE: No progress on {stalled_district} in a while. {stalled} is still short; "
                            f"{holder} is holding the most of it. Consider assign_task or contribute_resources.")
        if len(actives) < MAX_CONCURRENT_PROJECTS:
            idle_buildable = next((did for did in self._buildable_district_ids()
                                   if not c["districtProjects"].get(did)
                                   and self._district_structure_count(did) < c["districts"][did]["build_grid"]["cap"]),
                                  None)
            if idle_buildable and idle_buildable != agent.get("currentDistrict"):
                note(3, f"NOTE: {idle_buildable} has no build underway and there's room for another "
                        f"concurrent project (up to {MAX_CONCURRENT_PROJECTS} at once). Consider start_project "
                        f"with target_district {idle_buildable} if you're nearby.")
        if len(c["pendingBlueprints"]) < MAX_PENDING_BLUEPRINTS \
                and self.frameTick - c["lastBlueprintActivityFrame"] > BLUEPRINT_STALL_THRESHOLD:
            note(3, "NOTE: No new blueprint activity in a while. Consider propose_blueprint if you have an idea.")
        if STRUCTURE_EFFECTS_ENABLED and not invention_required:
            pref = self.d["ROLE_PROJECT"].get(agent["role"].lower(), "house")
            prefs = pref if isinstance(pref, list) else [pref]
            if prefs and all(self._type_saturated(p) for p in prefs):
                note(3, f"NOTE: The village has enough {', '.join(prefs)} structures -- "
                        f"more add nothing. Build a different type or propose_blueprint.")
        if CRAFTING_ENABLED and self.frameTick - c["lastCraftActivityFrame"] > CRAFT_STALL_THRESHOLD:
            has_workshop = any(s["type"] == "workshop" for s in c["structures"])
            if agent["role"] == "elder" and not has_workshop:
                note(2, "NOTE: No workshop exists yet. Direct an agent to build a Workshop so the village can craft planks, bricks, and tools for advanced builds.")
            elif has_workshop:
                granary = c["projectRegistry"].get("granary")
                if granary and "granary" not in c["builtTypes"]:
                    crafted_needs = ", ".join(f"{n} {r}" for r, n in granary["needs"].items()
                                              if r in self.RECIPES)
                    note(2, f"NOTE: No crafting in a while and the Granary is still unbuilt -- "
                            f"it needs {crafted_needs}. At the workshop, craft_item those now.")
                else:
                    note(2, "NOTE: No crafting in a while. At the workshop, craft_item (planks/bricks/tools) — advanced builds like the Granary need crafted goods.")
            else:
                note(2, "NOTE: The village should build a Workshop, then craft goods for advanced builds like the Granary.")
        if SURVIVAL_ENABLED:
            if agent["hunger"] < EAT_THRESHOLD and agent["resources"].get("food", 0) == 0:
                note(0, "NOTE: You are hungry and have no food. Gather food from the farm (or fish at the beach) before you starve.")
            # Dead agents stay incapacitated forever (no post-mortem revive
            # path), so without the deathFrame guard a deceased agent reads
            # as a standing "collapsed" emergency in every prompt.
            collapsed = next((a for a in self.agents
                              if a["incapacitated"] and a.get("deathFrame") is None), None)
            if collapsed and collapsed["name"] != agent["name"]:
                verb = "Go heal_agent" if agent["role"] == "healer" else "Bring food or heal_agent"
                note(0, f"NOTE: {collapsed['name']} has collapsed. {verb} to revive them.")
            em = self._sage_emergency()
            if em and em["name"] != agent["name"] and agent["name"] in self._sage_responders(em):
                emergency_active = True
                note(0, f"EMERGENCY: Elder Sage's life is the top priority — abandon your task and "
                        f"heal_agent {em['name']}. Nothing matters more than the elder's survival.")
        if nearby_detailed and agent["consecutiveTalks"] == 0 \
                and self.frameTick - agent.get("lastSpokeFrame", 0) > SOCIAL_SILENCE_FRAMES:
            note(3, "NOTE: You haven't spoken with anyone in a while and someone is nearby. "
                    "Consider talk_to_nearby to coordinate plans, ask for help, or share what you know.")
        if MEMES_ENABLED and agent.get("beliefs"):
            listener = next((self._find_agent(n.get("name")) for n in nearby_detailed
                             if n.get("name") and not (self._find_agent(n.get("name")) or {}).get("beliefs")), None)
            if listener:
                note(3, f"NOTE: {listener['name']} is nearby and has no belief. You may use talk_to_nearby with a belief_pitch to persuade them.")
        tool_line = None
        industry_line = None
        neighbor_line = None
        if path1_on():
            tools = [t for t in TOOL_TIER_ORDER if agent["resources"].get(t, 0) > 0]
            tool_line = f"wooden/stone/iron picks held: {', '.join(tools) or 'none'}"
            industry_line = f"Industry recipes: {len(self.RECIPES)} (smelt ores at kiln via craft_item)"
            if self._is_night():
                note(3, "NOTE: It is night — seek shelter in a house or composable shelter.")
            if self._border_settlement_agent(agent):
                neighbor_line = "Neighbor settlement nearby — trade, deliver_caravan, or propose_treaty"
                note(3, f"NOTE: {neighbor_line}.")
            for rej_key, label in (("lastBlockRejection", "block"), ("lastTerrainRejection", "terrain")):
                rej = agent.get(rej_key)
                if rej and self.frameTick - rej.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                    note(2, f"NOTE: Your last {label} action failed: {rej['reason']}.")
            self._maybe_seek_shelter(agent)
            self._maybe_expand_field(agent)
            self._maybe_caravan_goal(agent)
        if council_turn:
            final_nudges = []
        elif invention_turn:
            # Invention turns get the dedicated INVENTION_SYSTEM_PROMPT/
            # INVENTION_USER_PROMPT (build_invention_prompt in server.py),
            # which already covers taken ids, resources, and tier rules --
            # every other nudge here (talk/craft/heal/capacity/social/etc.)
            # is a distraction from the one job this turn has. The 2026-07-09
            # investigation found competing nudges in 100% of 171 invention
            # turns, correlating with duplicate/off-target proposals. Keep
            # only the blueprint-rejection reason (if any) so a retried
            # invention turn still learns why its last attempt failed.
            # These overrides bypass the priority-cap selection below --
            # they already reduce to <=1 nudge, so there's nothing to cap.
            final_nudges = [rejection_nudge] if rejection_nudge else []
        elif sprite_design_turn:
            sprite_rej = agent.get("lastSpriteRejection")
            sprite_note_text = None
            if sprite_rej and self.frameTick - sprite_rej.get("frame", 0) <= DIRECTIVE_TTL_FRAMES:
                sprite_note_text = (f"NOTE: Your last sprite was rejected: {sprite_rej['reason']}. "
                                    f"Submit a strictly BIGGER grid.")
            final_nudges = [sprite_note_text] if sprite_note_text else []
        else:
            # Priority selection: keep ALL P0 (rare) nudges, then fill
            # remaining slots up to MAX_BEHAVIOR_NUDGES with P1/P2/P3 nudges
            # in priority order, preserving relative order within each class
            # (Python's sort is stable).
            p0_nudges = [text for prio, text in nudges if prio == 0]
            rest_nudges = sorted(
                ((prio, text) for prio, text in nudges if prio != 0),
                key=lambda pt: pt[0])
            remaining_slots = max(0, MAX_BEHAVIOR_NUDGES - len(p0_nudges))
            final_nudges = p0_nudges + [text for _, text in rest_nudges[:remaining_slots]]
        # Observability: total collected vs. how many survived selection.
        # For invention/sprite turns the override already IS the total (no
        # separate pool was capped), so nothing reads as "dropped".
        if council_turn or invention_turn or sprite_design_turn:
            nudges_total = len(final_nudges)
            nudges_dropped = 0
        else:
            nudges_total = len(nudges)
            nudges_dropped = nudges_total - len(final_nudges)
        behavior_nudge = " ".join(final_nudges)

        # Phase A1 (shadow-log only, no behavior change): name the first
        # matching high-stakes trigger for this turn, priority order below.
        # Not read by is_high_stakes_turn/model_for_decision -- logging only.
        election_active = bool(c.get("pendingSuccession"))
        treaty_unvoted = any(r.get("kind") == "treaty" and agent["name"] not in r["votes"]
                             for r in c["pendingRules"])
        if council_turn and (daily_council or {}).get("phase") == "verdict" \
                and agent.get("role") == "elder":
            high_stakes_reason = "council_verdict"
        elif emergency_active:
            high_stakes_reason = "emergency"
        elif election_active:
            high_stakes_reason = "election"
        elif treaty_unvoted:
            high_stakes_reason = "treaty_vote"
        elif elder_blueprint_review_active:
            high_stakes_reason = "elder_blueprint_review"
        elif len(fresh_rejection_kinds) >= 2:
            high_stakes_reason = "repeated_rejections"
        else:
            high_stakes_reason = None

        # C3: trim the payload lists below that otherwise grow monotonically
        # across a long session. Each trim is prompt-facing only -- anything
        # server.py's validate_blueprint reads for id-collision/membership
        # checks keeps a separate, always-full list (noted per field).
        resource_items = [{"id": rid, "gather_zone": d.get("gatherZone"),
                           # Crafted goods are built-in resources, not
                           # invention slots (match _custom_resource_count).
                           "custom": (rid not in BASE_RESOURCES
                                       and rid not in CRAFTED_RESOURCES)}
                          for rid, d in c["resourceRegistry"].items()]
        # known_resource_ids: always-full, cheap id-only list. server.py's
        # validate_blueprint uses this (via build_agent_data) for the
        # duplicate-resource-id and needs-reference checks, so it must never
        # be trimmed -- only the rich known_resources list below (used for
        # the prompt) is capped.
        known_resource_ids_full = [r["id"] for r in resource_items]
        belief_records = [{"id": bid, "name": entry.get("name"), "tenet": entry.get("tenet"),
                           "affinity": list(entry.get("affinity") or [])}
                          for bid, entry in self._belief_registry().items()]
        belief_examples = [dict(example) for example in BELIEF_ARCHETYPES.values()]
        nearby_beliefs = {
            n["name"]: sorted((self._find_agent(n["name"]) or {}).get("beliefs") or [])
            for n in nearby_detailed if n.get("name")
        }
        seed_resources = [r for r in resource_items if not r["custom"]]
        custom_resources = [r for r in resource_items if r["custom"]]
        if len(seed_resources) + len(custom_resources) > MAX_KNOWN_RESOURCES_PROMPT:
            custom_slots = max(0, MAX_KNOWN_RESOURCES_PROMPT - len(seed_resources))
            known_resources_prompt = seed_resources + (custom_resources[-custom_slots:] if custom_slots else [])
        else:
            known_resources_prompt = seed_resources + custom_resources

        recipe_items = list(self.RECIPES.items()) if CRAFTING_ENABLED else []
        if len(recipe_items) > MAX_KNOWN_RECIPES_PROMPT:
            recipe_items = recipe_items[-MAX_KNOWN_RECIPES_PROMPT:]

        # rejected_blueprints: server.py's validate_blueprint reads the
        # full, untrimmed "rejected_blueprints" field (via build_agent_data)
        # for the "id was previously rejected" check, so that field is left
        # exactly as before. "rejected_blueprints_prompt" is a new, separate,
        # prompt-only view.
        rejected_full = list(c["rejectedBlueprintIds"])
        if len(rejected_full) > MAX_REJECTED_BLUEPRINTS_PROMPT:
            rejected_prompt = rejected_full[-MAX_REJECTED_BLUEPRINTS_PROMPT:] + [
                f"(+{len(rejected_full) - MAX_REJECTED_BLUEPRINTS_PROMPT} older rejected ids omitted)"]
        else:
            rejected_prompt = rejected_full

        # approved_custom_projects: same caution -- validate_blueprint's
        # approved_ids arg (duplicate-id + MAX_APPROVED_CUSTOM count checks)
        # keeps reading the full "approved_custom_projects" field unchanged.
        # "approved_custom_projects_prompt" is the new, separate, prompt-only
        # view (in practice a no-op today since approvals are already capped
        # at MAX_APPROVED_CUSTOM <= MAX_APPROVED_PROJECTS_PROMPT).
        approved_full = self._custom_project_ids()
        if len(approved_full) > MAX_APPROVED_PROJECTS_PROMPT:
            approved_prompt = approved_full[-MAX_APPROVED_PROJECTS_PROMPT:] + [
                f"(+{len(approved_full) - MAX_APPROVED_PROJECTS_PROMPT} older approved ids omitted)"]
        else:
            approved_prompt = approved_full

        # active_rules: not read by validate_blueprint at all, so a plain cap
        # on the existing field is safe. Already loosely bounded by
        # MAX_ACTIVE_RULES (8) <= MAX_ACTIVE_RULES_PROMPT (12) today.
        rules_full = list(c["rules"]) if RULES_ENABLED else []
        if len(rules_full) > MAX_ACTIVE_RULES_PROMPT:
            active_rules_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"],
                                  "effect": r.get("effect"), "supersedes": r.get("supersedes")}
                                 for r in rules_full[-MAX_ACTIVE_RULES_PROMPT:]]
            active_rules_list.append(f"(+{len(rules_full) - MAX_ACTIVE_RULES_PROMPT} older rules)")
        else:
            active_rules_list = [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"],
                                  "effect": r.get("effect"), "supersedes": r.get("supersedes")}
                                 for r in rules_full]

        daily_prompt = None
        if daily_council:
            order = daily_council.get("speakingOrder") or []
            speaker_idx = int(daily_council.get("nextSpeakerIdx") or 0)
            daily_prompt = {
                "trigger": daily_council.get("trigger"),
                "phase": daily_council.get("phase"),
                "round": daily_council.get("round"),
                "maxRounds": daily_council.get("maxRounds"),
                "currentSpeaker": order[speaker_idx % len(order)] if order else None,
                "agenda": json.loads(json.dumps(daily_council.get("agenda") or [])),
                "ballot": json.loads(json.dumps(daily_council.get("ballot"), default=str)),
                "verdict": json.loads(json.dumps(daily_council.get("verdict"), default=str)),
            }
        council_action_names = {"council_speak", "council_propose", "council_vote"}
        if council_turn:
            phase = (daily_council or {}).get("phase")
            allowed_council = {
                "discussion": {"council_speak"},
                "proposal": {"council_propose"},
                "voting": {"council_vote"},
                "verdict": {"council_speak"} if agent.get("role") == "elder" else set(),
            }.get(phase, set())
        else:
            allowed_council = set()
        available_actions = [
            action_name for action_name in self.d["AVAILABLE_ACTIONS"]
            if (action_name not in council_action_names or action_name in allowed_council)
            and (not council_turn or action_name in allowed_council)
            and (action_name != "start_terraform" or ECOLOGY_ENABLED)
            and (action_name != "found_belief" or MEMES_ENABLED)
            and (action_name != "repair_structure" or GOODS_ENABLED)
            and (action_name != "bury_agent" or CEMETERY_ENABLED)
            and (action_name != "repeal_rule" or RULES_ENABLED)
            and (action_name != "upgrade_structure" or STRUCTURE_UPGRADES_ENABLED)
            and (action_name != "submit_structure_sprite" or sprite_design_turn)
            and (action_name not in ("propose_role", "approve_role", "reject_role")
                 or EMERGENT_ROLES)
            and (action_name not in ("place_block", "remove_block")
                 or path1_on("COMPOSABLE_BUILD_ENABLED"))
            and (action_name not in ("dig_terrain", "plant_terrain")
                 or path1_on("TERRAIN_TILES_ENABLED"))
            and (action_name not in ("propose_treaty", "vote_treaty")
                 or path1_on("PATH1_DIPLOMACY_ENABLED"))
            and (action_name != "deliver_caravan"
                 or self._caravan_eligible(agent))
            and (action_name != "hunt_wildlife"
                 or (WILDLIFE_ENABLED and self._nearest_huntable_wildlife(agent) is not None))
            and (action_name != "confront_agent"
                 or (SURVIVAL_ENABLED and bool(self._confront_eligible_targets(agent))))
        ]

        # Sovereign God mode (Phase 3): computed once per think payload,
        # separate from directive above -- see _divine_prompt_lines.
        divine_public_line, divine_private_line = self._divine_prompt_lines(agent)

        # Nearby huntable fauna hint for the LLM / role_fallback (WILDLIFE_ENABLED).
        nearby_wildlife = []
        nearby_wildlife_line = None
        prey_in_range = False
        if WILDLIFE_ENABLED:
            for cre in (self.civilization.get("wildlife") or []):
                if not cre.get("alive"):
                    continue
                kind = cre.get("kind")
                if kind in WILDLIFE_DECORATIVE_KINDS or kind not in WILDLIFE_YIELD:
                    continue
                dd = _dist(agent["x"], agent["y"], cre["x"], cre["y"])
                if dd > HUNT_RADIUS:
                    continue
                nearby_wildlife.append({
                    "id": cre.get("id"), "kind": kind,
                    "hp": cre.get("hp"), "maxHp": cre.get("maxHp"),
                })
            if nearby_wildlife:
                prey_in_range = True
                parts = ", ".join(
                    f"{w['kind']}#{w['id']} ({w['hp']}/{w['maxHp']} hp)"
                    for w in nearby_wildlife[:5])
                nearby_wildlife_line = (
                    f"Nearby wildlife (hunt_wildlife target=id): {parts}"
                )

        think_payload = {
            "agent_name": agent["name"],
            "frame_tick": self.frameTick,
            "role": agent["role"],
            "role_skill": self.d["ROLE_SKILLS"].get(agent["role"], "helps the village"),
            "personality": self._personality_with_drift(agent),
            "life_stage": self._life_stage(agent) if LIFECYCLE_ENABLED else None,
            "memory": self._memory_for_prompt(agent),
            "resources": dict(agent["resources"]),
            "hunger": agent["hunger"],
            "health": agent["health"],
            "relationships": dict(agent["relationships"]),
            "beliefs": [self._belief_text(b) for b in agent["beliefs"]] if MEMES_ENABLED else [],
            "belief_ids": sorted(agent["beliefs"]) if MEMES_ENABLED else [],
            "belief_registry": belief_records if MEMES_ENABLED else [],
            "belief_examples": belief_examples if MEMES_ENABLED else [],
            "nearby_beliefs": nearby_beliefs if MEMES_ENABLED else {},
            "belief_pitch_budget_remaining": max(0, BELIEF_PITCH_SESSION_CAP - c.get("beliefPitchCalls", 0)),
            "nearby_agents": nearby_detailed,
            "nearby_wildlife": nearby_wildlife,
            "nearby_wildlife_line": nearby_wildlife_line,
            "prey_in_range": prey_in_range,
            "world_zone": agent["currentZone"],
            "current_district": agent.get("currentDistrict") or "none",
            "civilization_level": c["level"],
            "structures_built": len(c["structures"]),
            "structure_counts": {tid: self._structure_count(tid) for tid in c["projectRegistry"]}
                                if STRUCTURE_EFFECTS_ENABLED else {},
            "active_project": self._active_projects_brief(),
            "project_progress": self._active_projects_progress_text(),
            "known_districts": [{"id": did, "kind": d["kind"]} for did, d in c["districts"].items()
                                if d.get("build_grid")],
            "directive": self._current_directive() or "none",
            # Sovereign God mode (Phase 3): SEPARATE keys from "directive"
            # above, rendered as their own prompt lines only when set (see
            # server.py build_user_prompt) -- never folded into the elder
            # directive line, in either direction.
            "divine_public_line": divine_public_line,
            "divine_private_line": divine_private_line,
            "voice_guidance_active": voice_guidance_active,
            "voice_guidance_id": voice_guidance.get("voice_guidance_id"),
            "voice_guidance_text": voice_guidance.get("voice_guidance_text"),
            "divine_burning_bush_line": self._burning_bush_prompt_line(agent),
            "divine_anointment_line": self._anointment_prompt_line(agent),
            "divine_sampling": self._god_divine_sampling_for_think(agent),
            "invention_only": invention_turn,
            "council_turn": council_turn,
            "council_seated": council_seated,
            "daily_council": daily_prompt,
            "council_world_status": {
                "era": self._current_era_name() if TECH_TREE_ENABLED else c.get("level"),
                "techTier": self._village_tech_tier() if TECH_TREE_ENABLED else None,
                "projects": self._active_projects_brief(),
                "stalls": self._active_projects_progress_text(),
                "resourcePressure": self._format_district_stocks_for_prompt(agent),
                "activeRules": [r.get("name") or r.get("id") for r in rules_full[:8]],
                "inventionRequired": invention_required,
            } if council_turn else None,
            "invention_build_context": invention_build_context,
            "sprite_design_only": sprite_design_turn,
            "sprite_design_context": dict(agent["spriteDesignTurn"]) if sprite_design_turn else None,
            "upgradeable_structures": self._upgradeable_structures_brief() if STRUCTURE_UPGRADES_ENABLED else [],
            "invention_status": ("REQUIRED: every known structure is built or at capacity. Use "
                                 "propose_blueprint to invent a new structure.") if invention_required else "not needed",
            "commitment": agent.get("commitment"),
            "idle_agents": idle_agents,
            "known_resources": known_resources_prompt,
            # C3: always-full id list for server.py validation; see comment above.
            "known_resource_ids": known_resource_ids_full,
            "pending_blueprints": [{"id": b["id"], "needs": b["needs"], "proposed_by": b["proposedBy"],
                                    "sage_review": b.get("sageReview", "pending"),
                                    "sage_review_reason": b.get("sageReviewReason"),
                                    "duplicate_of": b.get("duplicateOf")}
                                   for b in c["pendingBlueprints"]],
            "known_recipes": [{"id": rid, "inputs": r["inputs"], "station": r["station"]}
                              for rid, r in recipe_items] if CRAFTING_ENABLED else [],
            "pending_recipes": [{"id": r["id"], "inputs": r["inputs"], "proposed_by": r["proposedBy"]}
                                for r in c["pendingRecipes"]],
            # C3: kept full (unchanged) for server.py's validate_blueprint;
            # *_prompt is the new, separate, capped view for rendering.
            "approved_custom_projects": approved_full,
            "approved_custom_projects_prompt": approved_prompt,
            "rejected_blueprints": rejected_full,
            "rejected_blueprints_prompt": rejected_prompt,
            "district_stocks": self._format_district_stocks_for_prompt(agent),
            "known_terraform": list(TERRAFORM_TEMPLATES.keys()) if ECOLOGY_ENABLED else [],
            # Phase C: one short prompt line (server renders it only when set,
            # so flag-off prompts stay byte-identical to Phase B).
            "season": self._current_season(),
            # Phase D: era replaces the level line and the tech tier feeds the
            # blueprint tier gate + invention prompt. Both None when the flag
            # is off, so the server renders Phase C prompts byte-identically.
            "era": self._current_era_name() if TECH_TREE_ENABLED else None,
            "village_tech_tier": self._village_tech_tier() if TECH_TREE_ENABLED else None,
            # Phase E: rendered as one compact "Prices: ..." line only when a
            # market exists (server renders it only when set, so flag-off /
            # no-market prompts stay byte-identical to Phase D).
            "prices_line": self._format_prices_for_prompt() if ECONOMY_ENABLED else None,
            "pending_rules": [{"id": r["id"], "name": r["name"], "kind": r["kind"], "value": r["value"],
                               "yes": list(r["votes"].values()).count("yes"),
                               "no": list(r["votes"].values()).count("no"),
                               "proposed_by": r["proposedBy"]}
                              for r in c["pendingRules"]] if RULES_ENABLED else [],
            "active_rules": active_rules_list if RULES_ENABLED else [],
            "constitution": [dict(p) for p in self._ensure_constitution()] if RULES_ENABLED else [],
            "recent_conversations": self._recent_conversations_text(),
            "inbox": self._drain_inbox(agent),
            "self_prompt": "",
            "module_reports": "none",
            "behavior_nudge": behavior_nudge,
            "nudges_total": nudges_total,
            "nudges_dropped": nudges_dropped,
            "needed_role": self._village_needed_role() if EMERGENT_ROLES else None,
            "known_role_ids": sorted(c["roleRegistry"]),
            "pending_role_count": len(c["pendingRoles"]),
            "emergent_role_count": len(set(c["roleRegistry"]) - set(self.d["ROLES"])),
            "known_project_ids": sorted(c["projectRegistry"]),
            # Server fallback helpers run outside the engine and must consult
            # this world's live registry, never server.py's process-start seed
            # maps. Copy list values so the think payload remains a snapshot.
            "role_project_map": {
                role: list(project) if isinstance(project, list) else project
                for role, project in self.d["ROLE_PROJECT"].items()
            },
            "role_primary_resource_map": dict(self.d["ROLE_PRIMARY_RESOURCE"]),
            "resource_gather_roles_map": {
                resource: list(roles)
                for resource, roles in self.d["RESOURCE_GATHER_ROLES"].items()
            },
            "pending_roles": [{"slug": role["slug"], "name": role["name"],
                               "specialty": list(role.get("specialty") or []),
                               "proposed_by": role.get("proposedBy")}
                              for role in c["pendingRoles"]],
            # Phase G: compact skills summary (folded into the existing "Your
            # skill:" line server-side, zero new template line) and a short
            # rotating village-history line (server renders it only when set,
            # so flag-off prompts stay byte-identical to Phase F).
            "skills": {k: round(v, 1) for k, v in agent["skills"].items()} if CULTURE_ENABLED else None,
            "chronicle_line": self._chronicle_prompt_line() if CULTURE_ENABLED else None,
            "council_digest_line": self._council_digest_prompt_line(),
            "weather_line": self._weather_prompt_line(),
            "library_lessons": (self._library_lessons(agent.get("currentDistrict"))
                                if CULTURE_ENABLED and LIBRARY_SCALING_ENABLED else None),
            "path1_tool_line": tool_line,
            "path1_industry_line": industry_line,
            "path1_neighbor_line": neighbor_line,
            "settlement_stores_line": self._format_settlement_stores_for_prompt(agent),
            "high_stakes_reason": high_stakes_reason,
            "available_actions": available_actions,
            "divine_public_event_line": self._divine_public_event_line(agent),
        }
        self._apply_context_mask(agent, think_payload)
        return think_payload

    def _recent_conversations_text(self):
        if not self.conversationLog:
            return "none"
        return " | ".join(f"{c['from']} -> {c['to']}: {c['message']}"
                          for c in self.conversationLog[:5])

    def _piano_module_context(self, agent, payload):
        """Compact context string for PIANO sub-calls (kept small for cost)."""
        parts = [
            f"role={agent.get('role')}",
            f"zone={agent.get('currentZone')}",
            f"hunger={agent.get('hunger')}",
            f"health={agent.get('health')}",
            f"resources={payload.get('resources')}",
            f"project={payload.get('active_project')}",
            f"nudge={payload.get('behavior_nudge')}",
        ]
        return "; ".join(str(p) for p in parts if p)

    def _mark_context_dirty(self, agent):
        """Make one agent eligible for the next always-on PIANO pulse."""
        if ALWAYS_ON_MODULES:
            agent["contextDirty"] = True
            agent["contextDirtySince"] = time.time()

    def _mark_all_context_dirty(self):
        if ALWAYS_ON_MODULES:
            for agent in self.agents:
                self._mark_context_dirty(agent)

    def _always_on_reports(self, agent_name, modules):
        """Read-only whiteboard assembly for a decision turn (no futures)."""
        now = time.time()
        enabled = modules or {}
        cache = self._piano_module_cache.get(agent_name, {})
        reports = []
        ages = []
        for module in ("perception", "social", "desire", "reflection"):
            if not enabled.get(module, True):
                continue
            note = cache.get(module) or {}
            text = note.get("text")
            wall_ts = note.get("wall_ts")
            if not text or not isinstance(wall_ts, (int, float)):
                continue
            age = max(0, now - wall_ts)
            # Ancient advice is worse than no advice; retries remain dirty.
            if age > MODULE_NOTE_MAX_AGE_S * 2:
                continue
            ages.append(age)
            reports.append(f"{module} ({int(age)}s ago): {text}")
        if ages:
            self._module_note_ages.extend(ages)
        return " | ".join(reports) if reports else "none"

    def _piano_free_slots(self):
        """Shared PIANO pool budget for decision fan-out and always-on refresh."""
        return max(0, PIANO_CONCURRENT_LLM - len(self._piano_refresh_inflight))

    def _run_decision_piano_module(self, runner, agent_name, module, context, frame_tick):
        """Decision-path module call; releases unified inflight slot on completion."""
        try:
            return runner(module, agent_name, context, frame_tick=frame_tick)
        finally:
            with self.lock:
                self._piano_refresh_inflight.discard((agent_name, module))

    def _fill_piano_report_from_cache(self, module, cache, tick, report_by_module):
        """Serve a throttled/off-tick module from cache when still within TTL."""
        cached = cache.get(module)
        if cached and (tick - cached["tick"]) <= PIANO_MODULE_CACHE_TTL:
            age = tick - cached["tick"]
            report_by_module[module] = (
                f"{module} ({age} turns ago): {cached['text']}")
            return True
        return False

    def _always_on_module_done(self, agent_name, module, dirty_since, text, started):
        """Store one background refresh after reacquiring the engine lock."""
        with self.lock:
            self._piano_refresh_inflight.discard((agent_name, module))
            latency_ms = (time.time() - started) * 1000.0
            totals = self._piano_latency_ms.setdefault(module, [0.0, 0])
            totals[0] += latency_ms
            totals[1] += 1
            agent = self._find_agent(agent_name)
            if not agent:
                return
            if not text:
                self._module_refresh_failures += 1
                return
            now = time.time()
            cache = self._piano_module_cache.setdefault(agent_name, {})
            cache[module] = {"tick": int(agent.get("moduleTick") or 0),
                             "text": text, "wall_ts": now}
            agent["moduleReports"] = {m: dict(v) for m, v in cache.items()}
            enabled = agent.get("modules") or {}
            due = [m for m in ("perception", "social", "desire", "reflection")
                   if enabled.get(m, True)]
            # Do not clear a newer dirty event that arrived while this work ran.
            if agent.get("contextDirty") and agent.get("contextDirtySince", 0) <= dirty_since:
                if all((cache.get(m) or {}).get("wall_ts", 0) >= dirty_since for m in due):
                    agent["contextDirty"] = False

    def _run_always_on_module(self, runner, agent_name, module, context, dirty_since):
        started = time.time()
        try:
            text = runner(module, agent_name, context, frame_tick=self.frameTick,
                          timeout_s=MODULE_REFRESH_TIMEOUT_S)
        except Exception:
            text = None
        self._always_on_module_done(agent_name, module, dirty_since, text, started)

    def _pulse_piano_modules(self):
        """Dispatch one bounded, event-gated PIANO refresh pulse under lock."""
        if not ALWAYS_ON_MODULES or not PIANO_MODULES:
            return
        runner = self.d.get("run_piano_module")
        if not runner:
            return
        now = time.time()
        due = []
        for agent in self.agents:
            # Phase A skips only agents who cannot act.  A night-wide cadence
            # change is deliberately reserved for the optional Phase C
            # backstop, not smuggled into this gate.
            if MODULE_REFRESH_IDLE_SKIP and agent.get("incapacitated"):
                continue
            dirty = bool(agent.get("contextDirty"))
            dirty_since = agent.get("contextDirtySince") or now
            for priority, module in enumerate(("perception", "desire", "social", "reflection")):
                if not (agent.get("modules") or {}).get(module, True):
                    continue
                note = (self._piano_module_cache.get(agent["name"], {}) or {}).get(module) or {}
                age = now - note.get("wall_ts", 0)
                # A dirty generation needs one successful report from every
                # enabled module. Completed modules from that same generation
                # must not jump back ahead of social/reflection on the next
                # bounded pulse; failures retain their old wall_ts and retry.
                dirty_due = dirty and note.get("wall_ts", 0) < dirty_since
                if (dirty_due or age >= MODULE_NOTE_MAX_AGE_S) and (agent["name"], module) not in self._piano_refresh_inflight:
                    # Dirty agents first; then oldest notes.  Priority retains
                    # the legacy 1/1/2/3 cadence preference as a tie-breaker.
                    due.append((0 if dirty else 1, -age, priority, agent, module, dirty_since))
        due.sort(key=lambda item: item[:3])
        free = self._piano_free_slots()
        selected = due[:min(MODULE_PULSE_MAX_BATCH, free)]
        self._module_pulse_work.append(len(selected))
        for _, _, _, agent, module, dirty_since in selected:
            payload = {"resources": dict(agent.get("resources") or {}),
                       "active_project": self._active_projects_brief(),
                       "behavior_nudge": agent.get("behaviorNudge")}
            context = self._piano_module_context(agent, payload)
            cache = self._piano_module_cache.get(agent["name"], {})
            recent = []
            for mod_name, note in cache.items():
                age = now - note.get("wall_ts", 0)
                if note.get("text") and age <= MODULE_NOTE_MAX_AGE_S:
                    recent.append((age, mod_name, note["text"]))
            if recent:
                recent.sort()
                context += "; last_reports=" + " | ".join(
                    f"{mod_name}({int(age)}s ago): {text}" for age, mod_name, text in recent)
            self._piano_refresh_inflight.add((agent["name"], module))
            self.piano_workers.submit(self._run_always_on_module, runner, agent["name"],
                                      module, context, dirty_since)

    def _run_piano_modules(self, agent_name, modules, module_tick, context, *,
                          force_cache_only=False):
        """Sid-parity Phase 1/5: run staggered PIANO modules on the dedicated
        piano_workers pool (never the decision pool), so a module backlog can
        never starve the Cognitive Controller decision call. Stagger:
        perception+desire every turn; social every 2nd; reflection every 3rd.
        Modules not due this turn are served from the module-report cache
        (PIANO_MODULE_CACHE_TTL module-ticks) instead of an empty slot.
        Decision-path dispatches share `_piano_refresh_inflight` with the
        always-on pulse and never submit more work than free PIANO slots.
        Returns (report_string, new_module_tick, runs)."""
        runner = self.d.get("run_piano_module")
        if not runner or not PIANO_MODULES:
            return "none", module_tick, 0
        if ALWAYS_ON_MODULES:
            return self._always_on_reports(agent_name, modules), module_tick, 0
        tick = (module_tick or 0) + 1
        modules = modules or {
            "perception": True, "social": True, "desire": True, "reflection": True,
        }
        to_run = []
        if modules.get("perception", True):
            to_run.append("perception")
        if modules.get("desire", True):
            to_run.append("desire")
        if modules.get("social", True) and tick % 2 == 0:
            to_run.append("social")
        if modules.get("reflection", True) and tick % 3 == 0:
            to_run.append("reflection")
        # Off-tick modules: enabled but not due this turn. Filled from cache
        # (if fresh) so the decision payload keeps seeing their last real
        # report instead of a gap on the ticks they don't fire.
        off_tick = [m for m in ("social", "reflection")
                    if modules.get(m, True) and m not in to_run]
        cache = self._piano_module_cache.setdefault(agent_name, {})
        ordered = list(to_run)
        for module in off_tick:
            if module not in ordered:
                ordered.append(module)
        report_by_module = {}
        runs = 0

        def _throttle_modules(modules_to_skip):
            for module in modules_to_skip:
                if not self._fill_piano_report_from_cache(
                        module, cache, tick, report_by_module):
                    self._piano_module_drops += 1

        if to_run and not force_cache_only:
            with self.lock:
                if self._piano_free_slots() == 0:
                    force_cache_only = True

        if to_run and force_cache_only:
            _throttle_modules(to_run)
        elif to_run:
            # Cross-module visibility (working-memory half-step): build one
            # shared "last_reports=" suffix from every cached report still
            # within PIANO_CROSS_CONTEXT_TTL module-ticks, and give it to
            # every module dispatched this turn. A module seeing its own
            # previous report is intentional (continuity, esp. reflection).
            fresh = []
            for mod_name, cached in cache.items():
                age = tick - cached["tick"]
                if 0 < age <= PIANO_CROSS_CONTEXT_TTL:
                    fresh.append((age, mod_name, cached["text"]))
            dispatch_context = context
            if fresh:
                fresh.sort(key=lambda t: t[0])
                suffix = "last_reports=" + " | ".join(
                    f"{mod_name}({age} ago): {text}" for age, mod_name, text in fresh)
                dispatch_context = context + "; " + suffix
            pending = list(to_run)
            active = {}
            while pending or active:
                with self.lock:
                    while pending and self._piano_free_slots() > 0:
                        module = pending.pop(0)
                        if (agent_name, module) in self._piano_refresh_inflight:
                            if not self._fill_piano_report_from_cache(
                                    module, cache, tick, report_by_module):
                                self._piano_module_drops += 1
                            continue
                        self._piano_refresh_inflight.add((agent_name, module))
                        start_ts = time.time()
                        active[module] = (
                            self.piano_workers.submit(
                                self._run_decision_piano_module, runner,
                                agent_name, module, dispatch_context,
                                self.frameTick),
                            start_ts,
                        )
                if not active:
                    _throttle_modules(pending)
                    pending.clear()
                    break
                done, _ = wait(
                    [fut for fut, _ in active.values()],
                    timeout=PIANO_MODULE_TIMEOUT_WAIT_S,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    for module, (fut, start_ts) in list(active.items()):
                        latency_ms = (time.time() - start_ts) * 1000.0
                        totals = self._piano_latency_ms.setdefault(module, [0.0, 0])
                        totals[0] += latency_ms
                        totals[1] += 1
                        self._piano_module_drops += 1
                        with self.lock:
                            self._piano_refresh_inflight.discard((agent_name, module))
                        del active[module]
                    continue
                for module, (fut, start_ts) in list(active.items()):
                    if fut not in done:
                        continue
                    try:
                        text = fut.result(timeout=0)
                    except Exception:
                        text = None
                    latency_ms = (time.time() - start_ts) * 1000.0
                    totals = self._piano_latency_ms.setdefault(module, [0.0, 0])
                    totals[0] += latency_ms
                    totals[1] += 1
                    if text:
                        cache[module] = {"tick": tick, "text": text}
                        report_by_module[module] = f"{module}: {text}"
                        runs += 1
                    else:
                        self._piano_module_drops += 1
                    del active[module]
        for module in off_tick:
            self._fill_piano_report_from_cache(
                module, cache, tick, report_by_module)
        reports = [report_by_module[m] for m in ordered if m in report_by_module]
        return (" | ".join(reports) if reports else "none"), tick, runs

    def _maybe_meta_update(self):
        """Sid-parity Phase 5: rotate one living agent through META_SYSTEM
        persona refresh on META_TICK_FRAMES. Amortized: one agent per gate."""
        if not META_SYSTEM:
            return
        runner = self.d.get("run_meta_update")
        if not runner:
            return
        living = [a for a in self._living_agents() if not a["incapacitated"]]
        if not living:
            return
        idx = self._meta_agent_index % len(living)
        self._meta_agent_index += 1
        agent = living[idx]
        top_actions = sorted(
            (agent.get("actionCounts") or {}).items(),
            key=lambda kv: kv[1], reverse=True,
        )[:3]
        report = {
            "role": agent.get("role"),
            "top_actions": ", ".join(f"{k}:{v}" for k, v in top_actions) or "none",
            "resources": dict(agent.get("resources") or {}),
            "beliefs": [self._belief_text(b) for b in (agent.get("beliefs") or ())],
        }
        agent_name = agent["name"]
        # LLM call while holding the tick lock is acceptable here: one agent
        # every META_TICK_FRAMES (~80s), same discipline as birth-persona.
        result = runner(agent_name, report, frame_tick=self.frameTick)
        if result and result.get("persona"):
            agent["persona"] = result["persona"]

    def _finish_think_identity_forge(self, agent):
        """Advance identity-copy blend after one think cycle completes."""
        if agent is not None:
            self._advance_identity_forge_on_think(agent)

    def _record_llm_orphan_timeout(self):
        """Count orphaned client-side timeouts toward decision-dispatch pause."""
        with self.lock:
            self._llm_orphan_timeouts += 1
            if self._llm_orphan_timeouts >= LLM_ORPHAN_TIMEOUT_THRESHOLD:
                self.llm_cooldown_until = time.time() + LLM_ORPHAN_COOLDOWN_S
                self._llm_orphan_timeouts = 0

    def _think_job(self, agent_name):
        """Runs in the worker pool. Build payload under lock, do the network
        call OUTSIDE the lock, then apply the result UNDER the lock."""
        try:
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent or agent["incapacitated"]:
                    return
                gate = self._god_active_decision_gate_record(agent["id"])
                possession_skip = (
                    isinstance(gate, dict)
                    and gate.get("mode") == "possession"
                    and gate.get("bypassLlm")
                )
                payload = self._build_think_payload(agent)
                self_prompt = (agent.get("persona") or "").strip() if META_SYSTEM else ""
                payload["self_prompt"] = self_prompt
                piano_context = None
                piano_modules = None
                piano_tick = 0
                piano_force_cache_only = False
                if PIANO_MODULES:
                    piano_context = self._piano_module_context(agent, payload)
                    piano_modules = dict(agent.get("modules") or {})
                    piano_tick = int(agent.get("moduleTick") or 0)
                    if not ALWAYS_ON_MODULES and self._piano_free_slots() == 0:
                        piano_force_cache_only = True
            if possession_skip:
                with self.lock:
                    agent = self._find_agent(agent_name)
                    if not agent:
                        return
                    em = self._sage_emergency()
                    if em and agent is not em and not agent["incapacitated"] \
                            and agent["name"] in self._sage_responders(em):
                        self._rush_to_heal(agent, em)
                        return
                    gate = self._god_active_decision_gate_record(agent["id"])
                    if gate:
                        pinned_raw = self._god_decision_gate_pinned(agent, gate)
                        pinned, _ = self._god_normalize_pinned_decision(agent, pinned_raw)
                        if pinned:
                            self._apply_divine_possessed_decision(agent, pinned, gate)
                            self._advance_possession_queue(gate)
                            agent["goal"] = self._goal_for_decision(pinned)
                            self._log_benchmark("divine_possession_skip", 1.0, {
                                "agent": agent["name"],
                                "action": pinned.get("action"),
                                "divine_possession": True,
                            })
                            self._finish_think_identity_forge(agent)
                return
            if PIANO_MODULES:
                # Module fan-out dispatches onto self.piano_workers (its own
                # PIANO_CONCURRENT_LLM-sized pool, see _run_piano_modules) --
                # decoupled from MAX_CONCURRENT_LLM so a module backlog can
                # never starve the decision path. Still called outside the
                # lock so this worker-pool thread can block waiting on it
                # without freezing the tick.
                reports, new_tick, runs = self._run_piano_modules(
                    agent_name, piano_modules, piano_tick, piano_context,
                    force_cache_only=piano_force_cache_only)
                payload["module_reports"] = reports
            else:
                new_tick, runs = 0, 0
            # Network call outside the lock (never block the tick thread or peers).
            decision = self.d["llm_decide"](payload)
            with self.lock:
                agent = self._find_agent(agent_name)
                if not agent:
                    return
                if PIANO_MODULES:
                    agent["moduleTick"] = new_tick
                    # Persistence-only mirror of this agent's module cache
                    # entry: the engine keeps reading _piano_module_cache on
                    # the hot path; this field only exists so restore_state
                    # can rebuild the cache after a restart (state.db already
                    # persists the agent dict, so this piggybacks on that).
                    agent["moduleReports"] = {
                        m: dict(v)
                        for m, v in self._piano_module_cache.get(agent_name, {}).items()
                    }
                    self._module_period_runs += runs
                    # Reflection reports are actual deliberate practice: this
                    # makes the high-reflection founder gate reachable without
                    # adding a separate action or blocking a decision turn.
                    if new_tick % 3 == 0 and "reflection:" in reports:
                        self._practice_skill(agent, "reflection")
                # In-flight guard (#A): if a Sage emergency began and THIS agent is
                # a designated responder, discard the decision and rush instead.
                em = self._sage_emergency()
                if em and agent is not em and not agent["incapacitated"] \
                        and agent["name"] in self._sage_responders(em):
                    self._rush_to_heal(agent, em)
                    return
                if not decision or decision.get("error") == "llm offline":
                    self.lmStatus = "offline"
                    self._apply_rule_based_fallback(agent)
                    self._finish_think_identity_forge(agent)
                elif decision.get("error") == "llm timeout":
                    self.lmStatus = "offline"
                    self._record_llm_orphan_timeout()
                    self._apply_rule_based_fallback(agent)
                    self._finish_think_identity_forge(agent)
                elif decision.get("error") == "compute_error":
                    self.lmStatus = "compute_error"
                    self.llm_cooldown_until = time.time() + LLM_ORPHAN_COOLDOWN_S
                    self._apply_rule_based_fallback(agent)
                    self._finish_think_identity_forge(agent)
                elif decision.get("error"):
                    self.lmStatus = "online"
                    self._apply_gated_decision(agent, {"action": "rest"})
                    self._finish_think_identity_forge(agent)
                else:
                    self.lmStatus = "online"
                    self.llm_cooldown_until = 0.0
                    self._llm_orphan_timeouts = 0
                    if decision.get("terraform_rejection_note"):
                        agent["lastTerraformRejection"] = {
                            "reason": decision["terraform_rejection_note"],
                            "frame": self.frameTick,
                        }
                    if decision.get("sprite_rejection_note"):
                        agent["lastSpriteRejection"] = {
                            "reason": decision["sprite_rejection_note"],
                            "frame": self.frameTick,
                        }
                    if decision.get("upgrade_rejection_note"):
                        agent["lastUpgradeRejection"] = {
                            "reason": decision["upgrade_rejection_note"],
                            "frame": self.frameTick,
                        }
                    retried_invention = False
                    if decision.get("rejection_note"):
                        # normalize_decision swapped an invalid propose_blueprint
                        # for a fallback; remember why so the next prompt can
                        # tell the model instead of failing silently again.
                        note = decision["rejection_note"]
                        agent["lastBlueprintRejection"] = {
                            "reason": note, "frame": self.frameTick}
                        if TECH_TREE_ENABLED and "tier" in (note or "").lower():
                            # Phase D observability: tier-gate rejections are
                            # village events, not just private prompt nudges.
                            self._push_activity(
                                f"Tech tree: {agent['name']}'s blueprint was "
                                f"refused — {note}")
                            self._log_benchmark(
                                "tier_gate_rejection", self._village_tech_tier(),
                                {"kind": "blueprint_normalize", "agent": agent["name"]})
                        # Same-window retry (2026-07-09 council investigation):
                        # a rejected propose_blueprint used to get swapped for
                        # a gather/move fallback whose goal then ran deterministically
                        # for a while, so a council member who failed validation
                        # (59/171 on a duplicate taken id alone) never got another
                        # invention-only turn before COUNCIL_TTL_FRAMES -- the
                        # council just dissolved empty. If a council is live and
                        # this agent is one of its proposers, give them ONE
                        # immediate retry instead: re-flag inventionTurn (so the
                        # next think is invention-only again, with this rejection
                        # reason in the prompt's feedback) and rest this beat
                        # rather than committing to the fallback's lasting goal.
                        council = self.civilization.get("councilActive")
                        if (payload.get("invention_only") and council
                                and agent["name"] in (council.get("proposers") or [])
                                and not agent.get("inventionRetryUsed")):
                            agent["inventionRetryUsed"] = True
                            agent["inventionTurn"] = True
                            agent["goal"] = None
                            self._apply_gated_decision(agent, {"action": "rest"})
                            retried_invention = True
                            self._finish_think_identity_forge(agent)
                    if not retried_invention:
                        applied = self._apply_gated_decision(agent, decision)
                        if applied:
                            agent["goal"] = self._goal_for_decision(decision)
                        self._finish_think_identity_forge(agent)
        except Exception:
            with self.lock:
                agent = self._find_agent(agent_name)
                if agent:
                    self.lmStatus = "offline"
                    self._apply_rule_based_fallback(agent)
                    self._finish_think_identity_forge(agent)
        finally:
            with self.lock:
                a = self._find_agent(agent_name)
                if a:
                    a["isThinking"] = False
                    self._mark_agent_dirty(a)
                self._inflight.discard(agent_name)

    def _schedule_think(self, agent):
        """Returns True if a think job was actually submitted, False if
        skipped (pool full / cooldown / min-gap) -- the caller uses this to
        retry soon (THINK_RETRY_FRAMES) instead of waiting a full
        thinkInterval, so a busy worker pool doesn't silently cost an agent
        an entire cycle."""
        if agent.get("divineHold"):
            return False
        if agent["name"] in self._inflight:
            return False
        if len(self._inflight) >= MAX_CONCURRENT_LLM:
            return False
        now_ms = time.time() * 1000.0
        if time.time() < self.llm_cooldown_until:
            return False
        if now_ms - self.last_llm_dispatch_ms < LLM_MIN_GAP_MS:
            return False
        if agent["role"] == "elder":
            c = self.civilization
            c["inventionRequiredStreak"] = (c.get("inventionRequiredStreak", 0) + 1) \
                if self._invention_required() else 0
        self.last_llm_dispatch_ms = now_ms
        self._inflight.add(agent["name"])
        agent["isThinking"] = True
        self._mark_agent_dirty(agent)
        self._executor.submit(self._think_job, agent["name"])
        return True

    # --- the per-frame tick (ported tick(), minus rendering) ---
    def _tick_once(self):
        with self.lock:
            # When paused, the sim clock freezes entirely (no movement, survival,
            # thinking, or frameTick advance) so the viewer sees a frozen world
            # and persistence captures a stable frame. (The browser advanced its
            # render-frame counter while paused; here frameTick is the sim clock.)
            if self.paused:
                return
            self.frameTick += 1
            ft = self.frameTick

            if GOD_MODE_ENABLED:
                # Sovereign God mode (Phase 2): bounded scan (activeEvents
                # capped at 8), immediately after frameTick advances and
                # before every other consumer -- see docs/archive/plan-sovereign-god-
                # mode-v2.md's "Expiry ownership" section. In Phase 2 there
                # are no timed effects yet (only the no-mechanics
                # `proclamation` command applies), so this call is a cheap
                # no-op scan; it earns its keep starting Phase 5.
                self._expire_divine_effects()

            if SURVIVAL_ENABLED and ft % SURVIVAL_TICK_FRAMES == 0:
                for a in self.agents:
                    self._update_survival(a)
            if MEMORY_ENABLED and ft % MEMORY_TICK_FRAMES == 0:
                self._run_memory_maintenance()
            if META_SYSTEM and ft % META_TICK_FRAMES == 0:
                self._maybe_meta_update()
            if EMERGENT_ROLES and ft % ROLE_SWITCH_TICK_FRAMES == 0:
                self._maybe_auto_switch_role()
            if LIFECYCLE_ENABLED and ft % RULES_TICK_FRAMES == 0:
                # Repair restored/malformed leaderless state before the rule
                # backstop attempts to read or vote on succession ballots.
                self._ensure_succession_election()
            if RULES_ENABLED and ft % RULES_TICK_FRAMES == 0:
                self._maybe_advance_rules()
            if LIFECYCLE_ENABLED and ft % RULES_TICK_FRAMES == 0:
                # Deterministic escape hatch for a stalled succession vote --
                # checked on the same fast cadence as rule advancement so a
                # quorum-less election can't linger past its TTL.
                self._maybe_resolve_stalled_succession()
            if LIFECYCLE_ENABLED and ft % LIFECYCLE_TICK_FRAMES == 0:
                self._tick_lifecycle()
            if ft % RULES_TICK_FRAMES == 0:
                self._maybe_feed_starving()
                self._maybe_forced_hunt()
                self._maybe_repair_critical()
                if GOODS_ENABLED:
                    self._maybe_repair_campaign()
                    self._maybe_cull_ruins()
                self._maybe_abandon_stalled_projects()
                self._maybe_relocate_stuck_project()
                self._maybe_reorganize_structures()
                self._maybe_force_contribution()
                self._maybe_start_idle_district_project()
                self._maybe_build_funded_project()
                self._maybe_start_approved_custom()
                self._maybe_retire_blueprint()
                self._maybe_amnesty_rejected_blueprints()
                if SAGE_REVIEW_ENABLED:
                    self._maybe_skip_sage_review()
                    self._maybe_amnesty_denied_sage_reviews()
                self._maybe_retire_custom_resource()
                self._maybe_invention_backstop()
                self._maybe_found_district()
                self._maybe_welcome_newcomer()
                if TECH_TREE_ENABLED:
                    self._maybe_era_transition()
                    self._maybe_dissolve_council()
                if CULTURE_ENABLED:
                    self._maybe_study_at_library()
                if CEMETERY_ENABLED:
                    self._maybe_handle_burials()
                if ECONOMY_ENABLED:
                    self._maybe_mint_coin()
                    self._maybe_fund_project_coin()
                if path1_on():
                    self._maybe_found_settlement()
                    self._path1_industry_benchmark()
            if path1_on("PRESSURE_LOOP_ENABLED") and ft % GOODS_TICK_FRAMES == 0:
                self._tick_wildlife()
            if path1_on("PRESSURE_LOOP_ENABLED") and ft % 30 == 0:
                if self._is_night():
                    self._tick_night_pressure()
                elif ENV_EFFECTS_ENABLED and self.civilization.get("litDistricts"):
                    self.civilization["litDistricts"] = []
            if STRUCTURE_EFFECTS_ENABLED and ft % EFFECT_TICK_FRAMES == 0:
                self._tick_structure_effects()
            if ECOLOGY_ENABLED and ft % ECOLOGY_REGROW_FRAMES == 0:
                self._tick_ecology_regrow()
            if GOODS_ENABLED and ft % GOODS_TICK_FRAMES == 0:
                self._tick_goods()
                self._tick_structure_health_benchmark()
            if GOODS_ENABLED and ft % DAY_FRAMES == 0:
                self._tick_shelter()
            if DAILY_COUNCIL_ENABLED:
                if ft % DAY_FRAMES == 0:
                    self._maybe_convene_daily_council()
                self._maybe_advance_daily_council()
            if MEMES_ENABLED and ft % MEME_TICK_FRAMES == 0:
                self._spread_beliefs_by_proximity()
            if BENCHMARKS_ENABLED and (ft % BENCHMARK_TICK_FRAMES == 0 or ft == FIRST_BENCHMARK_FRAME):
                self._sample_benchmarks()

            for a in self.agents:
                if a.get("divineHold"):
                    continue
                if not a["incapacitated"]:
                    self._move_agent(a, MOVE_SCALE)
            if WILDLIFE_ENABLED:
                self._move_wildlife()
            if WILDLIFE_ENABLED and ft % WILDLIFE_POP_TICK_FRAMES == 0:
                self._tick_huntable_wildlife()

            # The dark-gated whiteboard scheduler runs on the world clock,
            # never from a decision worker. An empty due-list only appends a
            # zero-work observation and submits no GPU inference.
            if ALWAYS_ON_MODULES and ft % int(MODULE_PULSE_INTERVAL_S * TICKS_PER_SEC) == 0:
                self._pulse_piano_modules()

            em_target = self._sage_emergency()
            responders = self._sage_responders(em_target) if em_target else None

            think_ready = []
            for a in self.agents:
                if a["messageTimer"] > 0:
                    a["messageTimer"] -= 1
                    if a["messageTimer"] == 0:
                        a["message"] = None
                        self._mark_agent_dirty(a)
                if a["incapacitated"]:
                    continue
                if a.get("divineHold"):
                    continue
                daily = (self.civilization.get("dailyCouncil")
                         if DAILY_COUNCIL_ENABLED else None)
                if daily and a["name"] in (daily.get("attendees") or []) \
                        and (daily.get("phase") == "convening" or not a.get("councilTurn")):
                    # Seated attendees do not resume ordinary goals/actions
                    # between their one-shot council turns.
                    a["thinkTimer"] = 1
                    continue
                if responders and a["name"] in responders:
                    a["thinkTimer"] -= 1
                    if a["thinkTimer"] <= 0:
                        self._rush_to_heal(a, em_target)
                        a["thinkTimer"] = GOAL_STEP_FRAMES
                    continue
                if a.get("reorgTask"):
                    a["thinkTimer"] -= 1
                    if a["thinkTimer"] <= 0:
                        self._step_reorg(a)
                        a["thinkTimer"] = GOAL_STEP_FRAMES
                    continue
                a["thinkTimer"] -= 1
                if a["thinkTimer"] <= 0 and not a["isThinking"] and a["name"] not in self._inflight:
                    if USE_GOALS and a["goal"] and not self._has_unread(a):
                        continuing = self._step_goal(a)
                        a["thinkTimer"] = GOAL_STEP_FRAMES if continuing else 1
                    else:
                        think_ready.append(a)

            # Sid-parity Phase 6: dispatch ready agents in staleness-priority
            # order (most overdue since their last successful think first),
            # not fixed roster order. With MAX_CONCURRENT_LLM slots per tick,
            # a naive `for a in self.agents` dispatch order systematically
            # favors early-indexed agents whenever more agents are ready than
            # there are free slots -- late-roster agents could retry at the
            # same fixed THINK_RETRY_FRAMES cadence indefinitely without ever
            # winning the race. Sorting by lastThinkFrame (ascending = longest
            # ago = most overdue) means whoever has waited longest gets first
            # crack at a freed slot each tick, regardless of roster position.
            think_ready.sort(key=lambda ag: ag.get("lastThinkFrame", -1))
            for a in think_ready:
                dispatched = self._schedule_think(a)
                if dispatched:
                    a["lastThinkFrame"] = ft
                a["thinkTimer"] = a["thinkInterval"] if dispatched else THINK_RETRY_FRAMES

    def _run_loop(self):
        while not self._stop.is_set():
            start = time.time()
            try:
                self._tick_once()
            except Exception:
                pass
            elapsed = time.time() - start
            sleep = TICK_DT - elapsed
            if sleep > 0:
                self._stop.wait(sleep)

    def start(self):
        self._thread = threading.Thread(target=self._run_loop, name="SimEngine", daemon=True)
        self._thread.start()
        # Periodic full-state autosave (Contract 3). Separate daemon thread so a
        # slow disk write never stalls the fixed-timestep tick loop.
        self._saver = threading.Thread(target=self._save_loop, name="SimSaver", daemon=True)
        self._saver.start()

    def stop(self):
        self._stop.set()

