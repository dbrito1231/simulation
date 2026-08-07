"""Phase 6i mixin: Sovereign God mode bounded-miracle / wildlife-god /
weather-override / story-event apply handlers, plus the top-level God
command dispatch (`god_apply`, `god_cancel`, `god_sight`), slice of
SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_god_apply_agent_vitals`
through `god_sight` (formerly core.py lines ~571-1699), immediately
following the four construction methods (`__init__`, `_select_active_defs`,
`_make_agents`, `_reset_world`) that stay on the concrete `SimEngine` class
in core.py. Covers the Sovereign God mode Phase 4 bounded immediate
miracles (`_god_apply_agent_vitals`, `_god_apply_grant_resource`,
`_god_apply_structure_condition`, `_god_apply_repair_structures`,
`_god_apply_clear_ruins`), the huntable wildlife god kinds
(`_god_apply_wildlife_spawn`, `_god_apply_wildlife_despawn`,
`_god_apply_wildlife_set_hp`), the Phase 6 weather override
(`_god_apply_weather_override`, `_close_weather_override`), the Phase 5
storyteller events (`_god_events_insert`, `_close_story_event`,
`_god_apply_story_event`), and the top-level God command dispatch/cancel/
observability routes (`god_apply`, `god_cancel`, `god_sight`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodMiraclesMixin:
    """Mixin slice of SimEngine: Sovereign God mode bounded miracles,
    huntable wildlife god kinds, weather override, storyteller events, and
    the top-level God command dispatch (god_apply/god_cancel/god_sight). See
    module docstring for the exact method range and rationale."""

    # --- Sovereign God mode Phase 4: bounded immediate miracles ---
    # All three are irreversible (docs/archive/plan-sovereign-god-mode-v2.md "Honest reversibility" -- the
    # default branch of _god_reversibility_class covers them; god_cancel and
    # _god_apply_revoke_guidance only ever look inside providence/
    # privateOmens, so an intervention id from one of these three can never
    # match either and is therefore already refused by construction, with no
    # extra code needed). Each is public (no private-omen-style visibility
    # boundary applies to vitals/resources/structures), source-attributed via
    # source="divine" through the same _push_communication/_push_chronicle
    # helpers proclamation/providence use, and records one recentInterventions
    # entry + one divine.jsonl "applied" record via the shared
    # _next_intervention_id/_god_record_intervention/_log_divine machinery.
    def _god_apply_agent_vitals(self, payload):
        """v1 "cannot kill" (Decision #6): a negative healthDelta is clamped
        to GOD_VITALS_HEALTH_FLOOR, never lower -- see that constant's
        comment for why 0 (the _update_survival incapacitation threshold) is
        never reachable through this miracle. Never touches deathFrame,
        incapacitated, or any lifecycle-succession state."""
        target_id = payload["targetId"]
        agent = self._find_agent_by_id(target_id)
        health_delta, hunger_delta = payload["healthDelta"], payload["hungerDelta"]
        old_health, old_hunger = agent["health"], agent["hunger"]
        new_health, new_hunger = old_health, old_hunger
        if health_delta:
            new_health = (min(100.0, old_health + health_delta) if health_delta >= 0
                         else max(GOD_VITALS_HEALTH_FLOOR, old_health + health_delta))
            agent["health"] = new_health
        if hunger_delta:
            new_hunger = max(0.0, min(100.0, old_hunger + hunger_delta))
            agent["hunger"] = new_hunger
        intervention_id = self._next_intervention_id()
        parts = []
        if health_delta:
            parts.append(f"health {old_health:g} -> {new_health:g}")
        if hunger_delta:
            parts.append(f"hunger {old_hunger:g} -> {new_hunger:g}")
        detail = ", ".join(parts)
        verb = "touches" if (health_delta >= 0 and hunger_delta >= 0) else "afflicts"
        text = f"A divine hand {verb} {agent['name']} ({detail})"
        self._push_activity(text)
        self._push_communication("divine_vitals", "divine", agent["name"], text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        self._mark_context_dirty(agent)
        self._god_record_intervention({
            "id": intervention_id, "kind": "agent_vitals", "frameTick": self.frameTick,
            "targetId": target_id, "healthDelta": health_delta, "hungerDelta": hunger_delta,
            "newHealth": new_health, "newHunger": new_hunger, "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "agent_vitals", "targetId": target_id,
                "newHealth": new_health, "newHunger": new_hunger}

    def _god_apply_grant_resource(self, payload):
        """Storage/carry semantics preserved explicitly (docs/archive/plan-sovereign-god-mode-v2.md Phase 4):
        a grant to an agent fills their _carry_cap room first, then routes
        any remainder to the village stockpile -- the SAME two sinks every
        normal gather/trade/tax path already uses, never a third bypass
        sink. A grant to "stockpile" always goes straight to the stockpile."""
        resource_id = payload["resourceId"]
        amount = payload["amount"]
        target = payload["target"]
        target_kind = "stockpile" if target == "stockpile" else "agent"
        c = self.civilization
        intervention_id = self._next_intervention_id()
        target_agent_id = None
        if target_kind == "stockpile":
            c["stockpile"][resource_id] = c["stockpile"].get(resource_id, 0) + amount
            agent_added, stockpile_added = 0, amount
            target_desc = "the village stockpile"
            comm_to = "everyone"
        else:
            target_agent_id = target["agentId"]
            agent = self._find_agent_by_id(target_agent_id)
            cap = self._carry_cap(agent)
            held = agent["resources"].get(resource_id, 0)
            room = max(0, cap - held)
            agent_added = min(amount, room)
            stockpile_added = amount - agent_added
            if agent_added:
                agent["resources"][resource_id] = held + agent_added
            if stockpile_added:
                c["stockpile"][resource_id] = c["stockpile"].get(resource_id, 0) + stockpile_added
            target_desc = agent["name"]
            comm_to = agent["name"]
        self._god_grant_session_total += amount
        text = f"A divine gift of {amount} {resource_id} appears for {target_desc}"
        if target_kind == "agent" and stockpile_added:
            text += f" ({agent_added} carried, {stockpile_added} overflow to the village stockpile)"
        self._push_activity(text)
        self._push_communication("divine_grant", "divine", comm_to, text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        self._god_record_intervention({
            "id": intervention_id, "kind": "grant_resource", "frameTick": self.frameTick,
            "resourceId": resource_id, "amount": amount, "targetKind": target_kind,
            "targetAgentId": target_agent_id, "agentAdded": agent_added,
            "stockpileAdded": stockpile_added, "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "grant_resource",
                "resourceId": resource_id, "amount": amount, "targetKind": target_kind,
                "targetAgentId": target_agent_id, "agentAdded": agent_added,
                "stockpileAdded": stockpile_added}

    def _god_apply_structure_condition(self, payload):
        """Repair (delta >= 0) and damage (delta < 0) both go through the
        SAME _apply_structure_condition_delta helper _tick_structure_decay
        uses, so a damage delta that crosses the ruin threshold fires the
        identical disrepair/ruin narration and homeOf homeless handling a
        natural decay collapse would (docs/archive/plan-sovereign-god-mode-v2.md Phase 4: "damage may
        legitimately drive a structure to ruin -- if it does, it must go
        through the same ruin path"). Validation already rejected ruined
        structures, so repair here can never recreate a destroyed structure
        (it only ever restores condition on a structure that was never a
        ruin to begin with)."""
        structure_id = payload["structureId"]
        delta = payload["delta"]
        structure = next((s for s in self.civilization["structures"]
                          if s.get("id") == structure_id), None)
        intervention_id = self._next_intervention_id()
        old_cond = structure.get("condition", 100.0)
        new_cond = self._apply_structure_condition_delta(structure, delta)
        name = structure.get("name") or structure.get("type")
        did = structure.get("districtId") or "the village"
        verb = "mends" if delta >= 0 else "strikes"
        text = f"A divine hand {verb} the {name} in {did} (condition {old_cond:g} -> {new_cond:g})"
        self._push_activity(text)
        self._push_communication("divine_structure", "divine", "everyone", text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        became_ruin = bool(structure.get("isRuin"))
        self._god_record_intervention({
            "id": intervention_id, "kind": "structure_condition", "frameTick": self.frameTick,
            "structureId": structure_id, "delta": delta, "oldCondition": old_cond,
            "newCondition": new_cond, "becameRuin": became_ruin,
            "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "structure_condition",
                "structureId": structure_id, "oldCondition": old_cond, "newCondition": new_cond,
                "becameRuin": became_ruin}

    def _god_apply_repair_structures(self, payload):
        """Batch condition restore / un-ruin for operator escape."""
        structures, sel_reason = self._god_select_repair_structures(payload)
        if sel_reason or not structures:
            return None
        condition_target = payload.get("conditionTarget")
        un_ruin = payload.get("unRuin", True)
        intervention_id = self._next_intervention_id()
        outcomes = []
        for structure in structures:
            old_cond = structure.get("condition", 100.0)
            was_ruin = bool(structure.get("isRuin")) or old_cond <= 0
            name = structure.get("name") or structure.get("type")
            if was_ruin:
                target = condition_target if condition_target is not None else REPAIR_CONDITION_RESTORE
                new_cond = max(REPAIR_CONDITION_RESTORE, min(100.0, float(target)))
                structure["condition"] = new_cond
                structure["isRuin"] = False
                structure.pop("ruinedSinceFrame", None)
            elif condition_target is not None:
                delta = float(condition_target) - old_cond
                delta = max(-GOD_REPAIR_STRUCTURES_CONDITION_MAX,
                            min(GOD_REPAIR_STRUCTURES_CONDITION_MAX, delta))
                new_cond = self._apply_structure_condition_delta(structure, delta)
            else:
                new_cond = self._apply_structure_condition_delta(
                    structure, min(REPAIR_CONDITION_RESTORE, GOD_REPAIR_STRUCTURES_CONDITION_MAX))
            outcomes.append({
                "structureId": structure["id"],
                "structureName": name,
                "oldCondition": old_cond,
                "newCondition": structure.get("condition", new_cond),
                "unRuined": was_ruin and un_ruin,
            })
        count = len(outcomes)
        text = f"A divine hand restores {count} structure(s) across the village"
        self._push_activity(text)
        self._push_communication("divine_repair_structures", "divine", "everyone", text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        self._god_record_intervention({
            "id": intervention_id, "kind": "repair_structures", "frameTick": self.frameTick,
            "count": count, "structures": outcomes, "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "repair_structures",
                "count": count, "structures": outcomes}

    def _god_apply_clear_ruins(self, payload):
        """Delete selected or aged ruins from the registry."""
        ruins, sel_reason = self._god_select_clear_ruins(payload)
        if sel_reason or not ruins:
            return None
        intervention_id = self._next_intervention_id()
        ids = [s["id"] for s in ruins]
        names = [s.get("name") or s.get("type") for s in ruins]
        removed = self._remove_structures_from_registry(ids)
        if removed:
            text = f"A divine hand clears {removed} ruin(s) from the village: {', '.join(names)}"
            self._push_activity(text)
            self._push_communication("divine_clear_ruins", "divine", "everyone", text, source="divine")
            self._push_chronicle(text, kind="divine", source="divine")
            self._tick_structure_health_benchmark()
        self._god_record_intervention({
            "id": intervention_id, "kind": "clear_ruins", "frameTick": self.frameTick,
            "structureIds": ids, "removed": removed, "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "clear_ruins",
                "structureIds": ids, "removed": removed}

    # --- Huntable wildlife god kinds (irreversible; same public audit path) ---
    def _god_apply_wildlife_spawn(self, payload):
        """Spawn via god_wildlife_spawn under the lock. Returns (outcome, reason)."""
        district_id = payload["districtId"]
        creature_kind = payload["kind"]
        cre = self.god_wildlife_spawn(district_id, creature_kind, respect_cap=True)
        if cre is None:
            return None, "wildlife spawn rejected"
        intervention_id = self._next_intervention_id()
        text = (f"A divine hand releases a {creature_kind} into {district_id} "
                f"({cre.get('id')})")
        self._push_activity(text)
        self._push_communication("divine_wildlife", "divine", "everyone", text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        outcome = {
            "interventionId": intervention_id, "kind": "wildlife_spawn",
            "id": cre.get("id"), "creatureKind": creature_kind,
            "districtId": district_id, "hp": cre.get("hp"), "maxHp": cre.get("maxHp"),
            "x": cre.get("x"), "y": cre.get("y"),
        }
        self._god_record_intervention({
            "id": intervention_id, "kind": "wildlife_spawn", "frameTick": self.frameTick,
            "wildlifeId": cre.get("id"), "creatureKind": creature_kind,
            "districtId": district_id, "status": "applied", "public": True,
        })
        return outcome, None

    def _god_apply_wildlife_despawn(self, payload):
        """Despawn via god_wildlife_despawn under the lock. Returns (outcome, reason)."""
        creature_id = payload.get("id")
        district_id = payload.get("districtId")
        count = self.god_wildlife_despawn(
            creature_id=creature_id, district_id=district_id)
        if count <= 0:
            return None, "wildlife despawn rejected"
        intervention_id = self._next_intervention_id()
        if creature_id is not None:
            text = f"A divine hand banishes wildlife {creature_id}"
        else:
            text = f"A divine hand clears wildlife from {district_id} ({count} removed)"
        self._push_activity(text)
        self._push_communication("divine_wildlife", "divine", "everyone", text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        outcome = {
            "interventionId": intervention_id, "kind": "wildlife_despawn",
            "id": creature_id, "districtId": district_id, "despawnCount": count,
        }
        self._god_record_intervention({
            "id": intervention_id, "kind": "wildlife_despawn", "frameTick": self.frameTick,
            "wildlifeId": creature_id, "districtId": district_id,
            "despawnCount": count, "status": "applied", "public": True,
        })
        return outcome, None

    def _god_apply_wildlife_set_hp(self, payload):
        """Set HP via god_wildlife_set_hp under the lock. Returns (outcome, reason)."""
        creature_id = payload["id"]
        hp = payload["hp"]
        cre = self.god_wildlife_set_hp(creature_id, hp)
        if cre is None:
            return None, "wildlife set_hp rejected"
        intervention_id = self._next_intervention_id()
        new_hp = int(cre.get("hp") or 0)
        max_hp = int(cre.get("maxHp") or 1)
        killed = not cre.get("alive")
        kind_name = cre.get("kind") or "creature"
        if killed:
            text = (f"A divine hand strikes down a {kind_name} "
                    f"({creature_id}, hp {new_hp}/{max_hp})")
        else:
            text = (f"A divine hand reshapes a {kind_name} "
                    f"({creature_id}, hp {new_hp}/{max_hp})")
        self._push_activity(text)
        self._push_communication("divine_wildlife", "divine", "everyone", text, source="divine")
        self._push_chronicle(text, kind="divine", source="divine")
        outcome = {
            "interventionId": intervention_id, "kind": "wildlife_set_hp",
            "id": creature_id, "creatureKind": kind_name,
            "districtId": cre.get("districtId"), "hp": new_hp, "maxHp": max_hp,
            "alive": bool(cre.get("alive")), "killed": killed,
        }
        self._god_record_intervention({
            "id": intervention_id, "kind": "wildlife_set_hp", "frameTick": self.frameTick,
            "wildlifeId": creature_id, "hp": new_hp, "maxHp": max_hp,
            "alive": bool(cre.get("alive")), "status": "applied", "public": True,
        })
        return outcome, None

    # --- Sovereign God mode Phase 6: weather override ---
    def _god_apply_weather_override(self, payload):
        """docs/archive/plan-sovereign-god-mode-v2.md Phase 6 Answers 1-4. By the time this runs, god_apply
        has already revalidated the whole normalized command against current
        live state under the same lock, so replaceEffectId (if any) still
        names the active weather override.

        Answer 1 (clock ownership): activeEvents[].expiresFrame is
        authoritative -- weather["exitFrame"] is set to the SAME absolute
        frame via _weather_enter_forced, so _tick_weather's early-return
        (frameTick < exitFrame) defers to the override automatically; no
        second clock can drift out of sync because there is only ever one
        frame value, read by both.

        Answer 3 (RNG discipline): enters via _weather_enter_forced, which
        draws no RNG -- state/districts/exitFrame all come from the already-
        validated command, never random.randint/random.sample.

        `priorState` is recorded for audit/preview only, per Answer 2 --
        it is NEVER used to restore anything; ending the override always
        hands off FORWARD to the natural cycle's successor of the
        overridden state (see _close_weather_override), not back to
        priorState."""
        replace_effect_id = payload.get("replaceEffectId")
        replaced_event = None
        if replace_effect_id:
            replaced_event = self._god_active_weather_override()
            if replaced_event is not None and replaced_event.get("id") == replace_effect_id:
                self._close_weather_override(replaced_event, "replaced")

        intervention_id = self._next_intervention_id()
        now = self.frameTick
        expires_frame = now + payload["durationFrames"]
        state, districts = payload["state"], payload["districts"]
        prior_state = (self.civilization.get("weather") or {}).get("state", "clear")

        self._weather_enter_forced(state, districts, expires_frame)

        event = {
            "id": intervention_id, "kind": "weather_override",
            "state": state, "districts": list(districts), "priorState": prior_state,
            "createdFrame": now, "startFrame": now, "expiresFrame": expires_frame,
            "status": "active",
            "replaces": replaced_event.get("id") if replaced_event else None,
        }
        self._god_events_insert(event)

        self._god_record_intervention({
            "id": intervention_id, "kind": "weather_override", "frameTick": self.frameTick,
            "state": state, "districts": list(districts), "priorState": prior_state,
            "expiresFrame": expires_frame, "status": "applied", "public": True,
        })
        return {"interventionId": intervention_id, "kind": "weather_override",
                "state": state, "districts": list(districts), "priorState": prior_state,
                "expiresFrame": expires_frame,
                "replacedEventId": replaced_event.get("id") if replaced_event else None}

    def _close_weather_override(self, event, status):
        """Closes one weather_override activeEvents record exactly once and
        hands off to the natural cycle's successor of the OVERRIDDEN state
        via _weather_handoff_successor (docs/archive/plan-sovereign-god-mode-v2.md Phase 6 Answer 2 -- never
        restores `event["priorState"]`, which would desync the cycle).
        Shared verbatim by expiry (_expire_divine_effects) and cancel
        (god_cancel) so both paths behave identically -- "cancelling an
        active override runs the SAME expiry handoff (so the machine never
        gets stranded mid-override), and says plainly that any damage
        already dealt stands" (docs/archive/plan-sovereign-god-mode-v2.md Validation)."""
        if not isinstance(event, dict) or event.get("status") != "active":
            return
        event["status"] = status
        self._weather_handoff_successor(event.get("state", "clear"))
        self._log_divine(event.get("id"), None, "weather_override", event,
                         {"status": status}, status, public=True)

    # --- Sovereign God mode Phase 5: storyteller events ---
    def _god_events_insert(self, event):
        """Append to the bounded activeEvents ring (cap GOD_ACTIVE_EVENTS_CAP
        per docs/archive/plan-sovereign-god-mode-v2.md). Evicts the oldest CLOSED (non-"active") entry first
        so a full ring never displaces something still live; only falls back
        to evicting the oldest active entry if every slot happens to be
        active, which the cap (8) plus normal expiry/cancellation makes an
        edge case rather than the common path."""
        events = self.civilization["godState"]["activeEvents"]
        events.append(event)
        if len(events) > GOD_ACTIVE_EVENTS_CAP:
            closed_idx = next((i for i, e in enumerate(events) if e.get("status") != "active"), None)
            del events[closed_idx if closed_idx is not None else 0]

    def _close_story_event(self, event, status):
        """Closes one activeEvents record exactly once (docs/archive/plan-sovereign-god-mode-v2.md "Expiry
        ownership" -- shared by cancel and _expire_divine_effects so both
        paths log identically). Once closed, _divine_modifier can no longer
        see its modifiers (status != "active"). If the event carried an
        embedded providence, that providence is closed through the SAME
        _close_providence path expiry/revocation/replacement use -- but only
        if it is STILL the active providence (a later divine command may
        have already replaced it independently, in which case there is
        nothing left here to touch). Primitives already applied are NOT
        retracted -- see _god_reversibility_class's "consequential" class."""
        if not isinstance(event, dict) or event.get("status") != "active":
            return
        event["status"] = status
        god = self.civilization.get("godState") or {}
        providence_id = event.get("providenceId")
        if providence_id:
            prov = god.get("providence")
            if isinstance(prov, dict) and prov.get("id") == providence_id:
                self._close_providence(status)
        self._log_divine(event.get("id"), None, "story_event", event,
                         {"status": status}, status, public=(event.get("visibility") != "private"))

    def _god_apply_story_event(self, payload):
        """Atomic multi-effect apply (docs/archive/plan-sovereign-god-mode-v2.md "Storyteller events" --
        "Events are atomic: preview validates every component; apply
        accepts all or changes nothing"). By the time this runs, god_apply
        has already revalidated the WHOLE normalized command against
        current state under the same lock (a stale replaceEffectId target or
        a since-occupied modifier key would already have rejected before
        reaching this method), so every step below always succeeds -- there
        is no partial-application path to guard against here."""
        god = self.civilization["godState"]
        replace_effect_id = payload.get("replaceEffectId")
        replaced_event = None
        if replace_effect_id:
            replaced_event = next(
                (e for e in god["activeEvents"]
                 if isinstance(e, dict) and e.get("id") == replace_effect_id
                 and e.get("status") == "active"), None)
            if replaced_event is not None:
                self._close_story_event(replaced_event, "replaced")

        intervention_id = self._next_intervention_id()
        now = self.frameTick
        expires_frame = now + payload["durationFrames"]
        modifiers = dict(payload.get("modifiers") or {})
        title, narration = payload["title"], payload["narration"]
        visibility, target_id = payload["visibility"], payload.get("targetId")

        # Immediate primitives reuse the EXACT SAME apply helpers (and
        # therefore the exact same clamp arithmetic/narration/audit trail)
        # the standalone agent_vitals/grant_resource/structure_condition
        # commands use -- each still mints its own intervention id (for its
        # own recentInterventions/divine.jsonl record), tagged with
        # parentEventId so every sub-effect stays traceable to this one
        # story event.
        primitive_outcomes = []
        for prim in payload.get("primitives") or []:
            prim_kind = prim["kind"]
            if prim_kind == "agent_vitals":
                prim_outcome = self._god_apply_agent_vitals(prim["payload"])
            elif prim_kind == "grant_resource":
                prim_outcome = self._god_apply_grant_resource(prim["payload"])
            elif prim_kind == "structure_condition":
                prim_outcome = self._god_apply_structure_condition(prim["payload"])
            else:
                continue
            prim_outcome["parentEventId"] = intervention_id
            primitive_outcomes.append(prim_outcome)

        providence_id = None
        if payload.get("providence"):
            prov_outcome = self._god_apply_providence({
                "text": payload["providence"]["text"],
                "durationFrames": payload["durationFrames"],
            })
            providence_id = prov_outcome["interventionId"]

        target_name = None
        if visibility == "private" and target_id is not None:
            target_agent = self._find_agent_by_id(target_id)
            target_name = target_agent["name"] if target_agent else None

        event = {
            "id": intervention_id, "kind": "story_event",
            "title": title, "narration": narration,
            "visibility": visibility, "targetId": target_id,
            "createdFrame": now, "startFrame": now, "expiresFrame": expires_frame,
            "status": "active",
            "modifiers": modifiers,
            "primitiveInterventionIds": [o["interventionId"] for o in primitive_outcomes],
            "providenceId": providence_id,
            "replaces": replaced_event.get("id") if replaced_event else None,
        }
        self._god_events_insert(event)

        if visibility == "public":
            self._push_activity(f'A divine story unfolds -- "{title}": {narration}')
            self._push_communication("divine_story_event", "divine", "everyone", narration, source="divine")
            self._push_chronicle(f"{title}: {narration}", kind="divine", source="divine")
        # Private events never touch public activity/communication/Chronicle
        # (same visibility boundary private_omen already enforces).

        self._god_record_intervention({
            "id": intervention_id, "kind": "story_event", "frameTick": self.frameTick,
            "title": title, "narration": narration, "visibility": visibility,
            "targetId": target_id, "expiresFrame": expires_frame,
            "modifierKeys": list(modifiers.keys()),
            "primitiveInterventionIds": event["primitiveInterventionIds"],
            "providenceId": providence_id,
            "status": "applied", "public": (visibility == "public"),
        })
        return {"interventionId": intervention_id, "kind": "story_event",
                "title": title, "expiresFrame": expires_frame,
                "modifiers": modifiers, "primitiveOutcomes": primitive_outcomes,
                "providenceId": providence_id, "targetId": target_id, "targetName": target_name,
                "replacedEventId": replaced_event.get("id") if replaced_event else None}

    def god_apply(self, preview_id, request_id):
        """Apply an exact previewed command. Accepts only {previewId,
        requestId} -- the client-returned normalizedCommand is NEVER
        authoritative input; apply resolves the server-held preview by id."""
        with self.lock:
            if not GOD_MODE_ENABLED:
                return {"ok": False, "reason": "god mode disabled"}
            if not isinstance(request_id, str) or not request_id.strip():
                self._god_rejected_count += 1
                return {"ok": False, "reason": "requestId is required"}
            request_id = request_id.strip()
            if not isinstance(preview_id, str) or not preview_id.strip():
                self._god_rejected_count += 1
                return {"ok": False, "reason": "previewId is required"}
            preview_id = preview_id.strip()

            self._god_preview_evict_expired()
            preview = self._god_preview_cache.get(preview_id)
            digest = preview["commandDigest"] if preview else None

            existing = self._god_requests.get(request_id)
            if existing is not None:
                # Idempotent replay: same requestId returns the ORIGINAL
                # response without re-applying. Same requestId bound to a
                # DIFFERENT preview/digest is a conflict -- reject, apply
                # nothing, return neither the old nor a new response.
                if existing.get("previewId") != preview_id or (
                    digest is not None and existing.get("commandDigest") != digest
                ):
                    self._god_rejected_count += 1
                    return {"ok": False, "reason": "requestId already used with a different preview"}
                return existing.get("response")

            if preview is None:
                self._god_rejected_count += 1
                return {"ok": False, "reason": "preview missing or expired"}
            if preview["expiresAt"] <= time.time():
                del self._god_preview_cache[preview_id]
                self._god_rejected_count += 1
                return {"ok": False, "reason": "preview expired"}

            normalized = preview["normalizedCommand"]
            # Revalidate atomically against current live state: re-run the
            # SAME validator over the server-held normalized command (never
            # fresh client input), recompute and compare the digest, then
            # recheck the recorded precondition fingerprint. frameTick drift
            # alone is acceptable; a fingerprint mismatch is not.
            revalidated, reason = self._validate_god_envelope(normalized)
            if reason:
                del self._god_preview_cache[preview_id]
                self._god_rejected_count += 1
                return {"ok": False, "reason": f"preview no longer valid: {reason}"}
            fresh_digest = self._god_command_digest(revalidated)
            if fresh_digest != preview["commandDigest"]:
                del self._god_preview_cache[preview_id]
                self._god_rejected_count += 1
                return {"ok": False, "reason": "preview digest mismatch"}
            fp_reason = self._god_check_fingerprint(revalidated, preview["fingerprint"])
            if fp_reason:
                del self._god_preview_cache[preview_id]
                self._god_rejected_count += 1
                return {"ok": False, "reason": fp_reason}

            outcome, apply_reason = self._god_apply_command(revalidated)
            if apply_reason:
                del self._god_preview_cache[preview_id]
                self._god_rejected_count += 1
                return {"ok": False, "reason": apply_reason}

            response = {
                "ok": True, "interventionId": outcome["interventionId"],
                "outcome": outcome, "appliedFrame": self.frameTick,
            }
            self._god_requests[request_id] = {
                "previewId": preview_id,
                "commandDigest": preview["commandDigest"],
                "interventionId": outcome["interventionId"],
                "status": "applied",
                "response": response,
                "createdAt": time.time(),
            }
            self._god_requests_evict()
            self._god_preview_cache.pop(preview_id, None)  # single-use once applied
            self._log_divine(outcome["interventionId"], request_id, revalidated["kind"],
                             revalidated, outcome, "applied", public=True)
            return response

    def god_cancel(self, target_id):
        """Cancel an active omen, providence, or timed story event by id
        (docs/archive/plan-sovereign-god-mode-v2.md Phase 5: "wire the /control/god/cancel route properly").
        A direct, lock-held mutation -- unlike every other applyable command
        this route intentionally has no preview/apply step (Phase 2 already
        established that shape for this stub; nothing here changes it).
        Only ever finds ids inside the three cancellable stores
        (godState["providence"], godState["privateOmens"], and ACTIVE
        entries of godState["activeEvents"]) -- an id minted by an
        irreversible Phase 4 miracle (agent_vitals/grant_resource/
        structure_condition) or a one-shot proclamation can never appear in
        any of those, so it is refused by construction, falling straight
        through to "nothing to cancel" with no special-case code needed."""
        with self.lock:
            if not GOD_MODE_ENABLED:
                return {"ok": False, "reason": "god mode disabled"}
            if not isinstance(target_id, str) or not target_id.strip():
                return {"ok": True, "cancelled": False,
                        "reason": "nothing to cancel", "targetId": target_id}
            target_id = target_id.strip()
            god = self.civilization["godState"]

            prov = god.get("providence")
            if isinstance(prov, dict) and prov.get("id") == target_id:
                self._close_providence("cancelled")
                self._push_activity("A divine providence is revoked.")
                return {"ok": True, "cancelled": True, "targetId": target_id, "targetKind": "providence"}

            for key, omen in list((god.get("privateOmens") or {}).items()):
                if isinstance(omen, dict) and omen.get("id") == target_id:
                    self._close_omen(key, "cancelled")
                    return {"ok": True, "cancelled": True, "targetId": target_id, "targetKind": "private_omen"}

            campaigns = god.get("whisperCampaigns") or {}
            if isinstance(campaigns, dict) and target_id in campaigns:
                self._close_whisper_campaign(target_id, "cancelled")
                return {"ok": True, "cancelled": True, "targetId": target_id,
                        "targetKind": "whisper_campaign"}

            compulsions = god.get("crowdCompulsions") or {}
            if isinstance(compulsions, dict) and target_id in compulsions:
                self._close_crowd_compulsion(target_id, "cancelled")
                return {"ok": True, "cancelled": True, "targetId": target_id,
                        "targetKind": "crowd_compulsion"}

            broadcasts = god.get("dreamBroadcasts") or {}
            if isinstance(broadcasts, dict) and target_id in broadcasts:
                self._close_dream_broadcast(target_id, "cancelled")
                return {"ok": True, "cancelled": True, "targetId": target_id,
                        "targetKind": "dream_broadcast"}

            replays = god.get("dejaVuReplays") or {}
            if isinstance(replays, dict) and target_id in replays:
                self._close_deja_vu_replay(target_id, "cancelled")
                return {"ok": True, "cancelled": True, "targetId": target_id,
                        "targetKind": "deja_vu_replay"}

            sampling = god.get("agentSampling") or {}
            if isinstance(sampling, dict):
                for key, rec in list(sampling.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_agent_sampling(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "agent_sampling"}

            masks = god.get("contextMasks") or {}
            if isinstance(masks, dict):
                for key, rec in list(masks.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_context_mask(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "context_mask"}

            gates = god.get("decisionGates") or {}
            if isinstance(gates, dict):
                for key, rec in list(gates.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_decision_gate(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "decision_gate"}

            bushes = god.get("burningBush") or {}
            if isinstance(bushes, dict):
                for key, rec in list(bushes.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_burning_bush(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "burning_bush"}
                    bargain = rec.get("bargain") if isinstance(rec, dict) else None
                    if isinstance(bargain, dict) and bargain.get("id") == target_id:
                        if bargain.get("status") == "open":
                            bargain["status"] = "cancelled"
                            bargain["settledFrame"] = self.frameTick
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "merovingian_bargain"}

            anointments = god.get("anointments") or {}
            if isinstance(anointments, dict):
                for key, rec in list(anointments.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_anointment(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "anoint"}

            forges = god.get("identityForges") or {}
            if isinstance(forges, dict):
                for key, rec in list(forges.items()):
                    if isinstance(rec, dict) and rec.get("id") == target_id:
                        self._close_identity_forge(key, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "identity_forge"}

            zones = god.get("architectZones") or []
            if isinstance(zones, list):
                for zone in zones:
                    if isinstance(zone, dict) and zone.get("id") == target_id and zone.get("status") == "active":
                        self._close_architect_zone(target_id, "cancelled")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "architect_zone"}

            for event in god.get("activeEvents") or []:
                if (isinstance(event, dict) and event.get("id") == target_id
                        and event.get("status") == "active"):
                    if event.get("kind") == "weather_override":
                        # Phase 6: cancel runs the SAME handoff expiry uses --
                        # the machine never gets stranded mid-override -- and
                        # any storm damage already dealt stands (consequential,
                        # not undone).
                        self._close_weather_override(event, "cancelled")
                        self._push_activity(
                            "A divine weather override is cancelled -- the sky "
                            "returns to its natural course. Any damage already "
                            "dealt stands.")
                        return {"ok": True, "cancelled": True, "targetId": target_id,
                                "targetKind": "weather_override"}
                    self._close_story_event(event, "cancelled")
                    if event.get("visibility", "public") == "public":
                        self._push_activity(f'A divine story is cut short: "{event.get("title")}"')
                    return {"ok": True, "cancelled": True, "targetId": target_id, "targetKind": "story_event"}

            return {
                "ok": True, "cancelled": False,
                "reason": "nothing to cancel", "targetId": target_id,
            }

    def god_sight(self, filters=None):
        """Bounded private projection: authenticated inspection beyond what
        /state exposes publicly. Must NOT include memory-store embeddings,
        raw unbounded logs, or any auth material."""
        with self.lock:
            if not GOD_MODE_ENABLED:
                return {"ok": False, "reason": "god mode disabled"}
            god = self.civilization.get("godState") or self._default_god_state()
            omens = god.get("privateOmens") or {}

            def _omen_status(agent_id):
                # Phase 3: STATUS only (active + exact remaining frame), not
                # raw omen content -- the sight comment this replaces already
                # named that contract; the omen's actual text is still
                # reachable through recentInterventions ("intervention
                # outcomes" is explicitly in scope for /control/god/sight per
                # docs/archive/plan-sovereign-god-mode-v2.md) or by the operator recalling what they wrote.
                omen = omens.get(str(agent_id))
                if not isinstance(omen, dict):
                    return None
                if not self._voice_guidance_in_window(omen):
                    return None
                return {
                    "active": True,
                    "expiresFrame": omen.get("expiresFrame"),
                    "unacked": not omen.get("acked"),
                }

            def _providence_status(agent_id):
                prov = god.get("providence")
                if not isinstance(prov, dict) or not self._voice_guidance_in_window(prov):
                    return None
                acked = prov.get("ackedAgentIds") or {}
                return {
                    "active": True,
                    "expiresFrame": prov.get("expiresFrame"),
                    "unacked": not acked.get(str(agent_id)),
                }

            def _sampling_status(agent_id):
                rec = self._god_active_agent_sampling_record(agent_id)
                if rec is None:
                    return None
                status = {
                    "active": True,
                    "model": rec.get("model"),
                    "temperature": rec.get("temperature"),
                }
                if rec.get("expiresFrame") is not None:
                    status["expiresFrame"] = rec.get("expiresFrame")
                return status

            def _mask_status(agent_id):
                rec = self._god_active_context_mask_record(agent_id)
                if rec is None:
                    return None
                return {
                    "active": True,
                    "mode": rec.get("mode"),
                    "expiresFrame": rec.get("expiresFrame"),
                }

            def _gate_status(agent_id):
                rec = self._god_active_decision_gate_record(agent_id)
                if rec is None:
                    return None
                status = {
                    "active": True,
                    "mode": rec.get("mode"),
                    "armed": rec.get("armed"),
                    "status": rec.get("status"),
                    "expiresFrame": rec.get("expiresFrame"),
                    "hasPending": bool(rec.get("pendingDecision")),
                }
                pin = rec.get("pinnedDecision")
                if isinstance(pin, dict) and pin.get("action"):
                    status["pinnedAction"] = pin.get("action")
                elif isinstance(rec.get("queue"), list) and rec["queue"]:
                    status["pinnedAction"] = rec["queue"][0].get("action")
                return status

            def _burning_bush_status(agent_id):
                rec = (god.get("burningBush") or {}).get(str(agent_id))
                if not isinstance(rec, dict) or rec.get("status") != "active":
                    return None
                thread = rec.get("thread") or []
                message_count = len(thread) if isinstance(thread, list) else 0
                bargain = rec.get("bargain")
                bargain_active = (
                    isinstance(bargain, dict) and bargain.get("status") == "open"
                )
                status = {
                    "active": True,
                    "messageCount": message_count,
                    "bargainActive": bargain_active,
                }
                if bargain_active and isinstance(bargain.get("expiresFrame"), int):
                    status["expiresFrame"] = bargain.get("expiresFrame")
                return status

            def _anointment_status(agent_id):
                rec = self._god_active_anointment_record(agent_id)
                if rec is None:
                    return None
                status = {
                    "active": True,
                    "tagCount": len(rec.get("stigmataTags") or []),
                    "expiresFrame": rec.get("expiresFrame"),
                }
                next_oracle = self._anointment_next_oracle_frame(rec)
                if next_oracle is not None:
                    status["nextOracleFrame"] = next_oracle
                return status

            def _identity_forge_status(agent_id):
                rec = self._god_active_identity_forge_record(agent_id)
                if rec is None:
                    return None
                status = {
                    "active": True,
                    "progress": rec.get("progress"),
                    "expiresFrame": rec.get("expiresFrame"),
                }
                if rec.get("copyFromId") is not None:
                    status["copyFromId"] = rec.get("copyFromId")
                    status["rate"] = rec.get("rate")
                return status

            def _architect_limbo_status(agent_id):
                agent = self._find_agent_by_id(agent_id)
                hold = agent.get("architectLimbo") if agent else None
                if not isinstance(hold, dict):
                    return None
                return {
                    "active": True,
                    "zoneId": hold.get("zoneId"),
                }

            def _architect_zones_sight_summary():
                summary = []
                for zone in self._god_active_architect_zones():
                    cells_expanded, _ = self._expand_architect_cells(zone.get("cells"))
                    entry = {
                        "id": zone.get("id"),
                        "kind": zone.get("kind"),
                        "districtId": zone.get("districtId"),
                        "cellCount": len(cells_expanded or []),
                        "expiresFrame": zone.get("expiresFrame"),
                    }
                    if zone.get("kind") == "limbo":
                        entry["holdCount"] = len(zone.get("holdAgentIds") or [])
                    summary.append(entry)
                return summary

            def _checkpoints_sight_summary():
                summary = []
                for rec in (god.get("checkpoints") or []):
                    if not isinstance(rec, dict):
                        continue
                    summary.append({
                        "id": rec.get("id"),
                        "label": rec.get("label"),
                        "frameTick": rec.get("frameTick"),
                        "createdAt": rec.get("createdAt"),
                    })
                return summary

            def _decision_digests_sight_summary():
                digests = god.get("decisionDigests") or []
                if not isinstance(digests, list):
                    return []
                filter_agent_id = None
                if isinstance(filters, dict):
                    filter_agent_id = filters.get("agentId")
                rows = []
                for d in digests:
                    if not isinstance(d, dict):
                        continue
                    if filter_agent_id is not None and d.get("agentId") != filter_agent_id:
                        continue
                    if not isinstance(d.get("action"), str):
                        continue
                    row = {
                        "frameTick": d.get("frameTick"),
                        "agentId": d.get("agentId"),
                        "action": d.get("action"),
                    }
                    if isinstance(d.get("reasoningHash"), str):
                        row["reasoningHash"] = d.get("reasoningHash")
                    rows.append(row)
                cap = GOD_DEJA_VU_MAX_STEPS * 4 if filter_agent_id is not None else 40
                return rows[-cap:]

            def _deja_vu_replays_sight_summary():
                summary = []
                replays = god.get("dejaVuReplays") or {}
                if not isinstance(replays, dict):
                    return summary
                for rec in replays.values():
                    if not isinstance(rec, dict):
                        continue
                    summary.append({
                        "id": rec.get("id"),
                        "targetId": rec.get("targetId"),
                        "stepCount": len(rec.get("steps") or []),
                        "currentIndex": rec.get("currentIndex"),
                        "status": rec.get("status"),
                    })
                return summary

            def _crowd_compulsions_sight_summary():
                summary = []
                compulsions = god.get("crowdCompulsions") or {}
                if not isinstance(compulsions, dict):
                    return summary
                for rec in compulsions.values():
                    if not isinstance(rec, dict):
                        continue
                    summary.append({
                        "id": rec.get("id"),
                        "targetCount": len(rec.get("targets") or {}),
                        "expiresFrame": rec.get("expiresFrame"),
                        "status": rec.get("status"),
                    })
                return summary

            def _dream_broadcasts_sight_summary():
                summary = []
                broadcasts = god.get("dreamBroadcasts") or {}
                if not isinstance(broadcasts, dict):
                    return summary
                for rec in broadcasts.values():
                    if not isinstance(rec, dict):
                        continue
                    summary.append({
                        "id": rec.get("id"),
                        "targetCount": len(rec.get("targets") or {}),
                        "expiresFrame": rec.get("expiresFrame"),
                        "status": rec.get("status"),
                    })
                return summary

            def _village_pulse_sight_summary():
                """Ephemeral village aggregate — derived live, never persisted."""
                crisis = []
                for a in self.agents:
                    if a.get("deathFrame") is not None:
                        continue
                    reasons = []
                    health = a.get("health", 100)
                    hunger = a.get("hunger", 100)
                    if a.get("incapacitated"):
                        reasons.append("incapacitated")
                    if health < SAGE_CRITICAL_HEALTH:
                        reasons.append("critical health")
                    elif health < 60:
                        reasons.append("low health")
                    if hunger <= 0:
                        reasons.append("starving")
                    elif hunger <= 30:
                        reasons.append("very hungry")
                    if reasons:
                        crisis.append({
                            "id": a["id"],
                            "name": a["name"],
                            "reason": "; ".join(reasons),
                        })

                def _crisis_rank(entry):
                    agent = next((x for x in self.agents if x["id"] == entry["id"]), None)
                    if not agent:
                        return 99
                    if agent.get("incapacitated"):
                        return 0
                    if agent.get("health", 100) < SAGE_CRITICAL_HEALTH:
                        return 1
                    if agent.get("hunger", 100) <= 0:
                        return 2
                    if agent.get("health", 100) < 60:
                        return 3
                    return 4

                crisis.sort(key=_crisis_rank)
                crisis = crisis[:8]

                stockpile = self.civilization.get("stockpile") or {}
                stockpile_totals = {
                    k: v for k, v in stockpile.items()
                    if isinstance(v, (int, float)) and v > 0
                }
                open_projects = sum(
                    1 for p in (self.civilization.get("districtProjects") or {}).values() if p
                )

                sage = next(
                    (a for a in self.agents
                     if a["role"] == "elder" and a.get("deathFrame") is None),
                    None,
                )
                if sage:
                    if sage.get("incapacitated"):
                        sage_summary = "incapacitated"
                    elif sage.get("health", 100) < SAGE_CRITICAL_HEALTH:
                        sage_summary = "critical"
                    else:
                        sage_summary = "living"
                    sage_status = {
                        "present": True,
                        "name": sage["name"],
                        "role": sage["role"],
                        "status": sage_summary,
                        "health": sage.get("health"),
                        "hunger": sage.get("hunger"),
                    }
                else:
                    sage_status = {"present": False, "status": "absent"}

                event_titles = []
                for event in god.get("activeEvents") or []:
                    if not isinstance(event, dict) or event.get("status") != "active":
                        continue
                    title = event.get("title")
                    if isinstance(title, str) and title.strip():
                        event_titles.append(title.strip())
                    elif isinstance(event.get("kind"), str):
                        event_titles.append(event["kind"])

                prov = god.get("providence")
                if isinstance(prov, dict) and self._voice_guidance_in_window(prov):
                    pulse_providence = {
                        "active": True,
                        "expiresFrame": prov.get("expiresFrame"),
                    }
                else:
                    pulse_providence = {"active": False}

                return {
                    "crisisAgents": crisis,
                    "stockpileTotals": stockpile_totals,
                    "openProjectsCount": open_projects,
                    "sageStatus": sage_status,
                    "weather": self._weather_snapshot(),
                    "activeEventTitles": event_titles[:GOD_ACTIVE_EVENTS_CAP],
                    "providence": pulse_providence,
                }

            agents = [{
                "id": a["id"], "name": a["name"], "role": a["role"],
                "health": a.get("health"), "hunger": a.get("hunger"),
                "incapacitated": a.get("incapacitated"),
                "deceased": bool(a.get("deathFrame") is not None),
                "resources": dict(a.get("resources") or {}),
                "relationships": dict(a.get("relationships") or {}),
                "lastAction": a.get("lastAction"),
                "lastReasoning": (a.get("lastReasoning") or "")[:240] or None,
                "currentDistrict": a.get("currentDistrict"),
                "omen": _omen_status(a["id"]),
                "providence": _providence_status(a["id"]),
                "sampling": _sampling_status(a["id"]),
                "contextMask": _mask_status(a["id"]),
                "decisionGate": _gate_status(a["id"]),
                "burningBush": _burning_bush_status(a["id"]),
                "anointment": _anointment_status(a["id"]),
                "identityForge": _identity_forge_status(a["id"]),
                "architectLimbo": _architect_limbo_status(a["id"]),
                "divineHold": bool(a.get("divineHold")),
                "memoryCounts": {
                    "working": len((a.get("memory") or {}).get("working") or []),
                    "shortTerm": len((a.get("memory") or {}).get("shortTerm") or []),
                },
                "beliefCount": len(a.get("beliefs") or ()),
            } for a in self.agents]
            recent_divine_responses = list(god.get("recentDivineResponses") or [])
            if isinstance(filters, dict):
                filter_agent_id = filters.get("agentId")
                if filter_agent_id is not None:
                    recent_divine_responses = [
                        r for r in recent_divine_responses
                        if isinstance(r, dict) and r.get("agentId") == filter_agent_id
                    ]
            return {
                "ok": True,
                "frameTick": self.frameTick,
                "intervened": bool(god.get("intervened")),
                "providence": god.get("providence"),
                "activeEvents": list(god.get("activeEvents") or [])[:GOD_ACTIVE_EVENTS_CAP],
                "recentInterventions": list(god.get("recentInterventions") or [])[-GOD_RECENT_INTERVENTIONS_CAP:],
                "recentDivineResponses": recent_divine_responses[:GOD_DIVINE_RESPONSE_LOG_MAX],
                "architectZones": _architect_zones_sight_summary(),
                "checkpoints": _checkpoints_sight_summary(),
                "decisionDigests": _decision_digests_sight_summary(),
                "dejaVuReplays": _deja_vu_replays_sight_summary(),
                "crowdCompulsions": _crowd_compulsions_sight_summary(),
                "dreamBroadcasts": _dream_broadcasts_sight_summary(),
                "pulse": _village_pulse_sight_summary(),
                "agents": agents,
            }

