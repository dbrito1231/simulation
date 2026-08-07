"""Phase 6g mixin: Sovereign God mode command validation slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_god_active_weather_override`
through `_god_check_fingerprint` (formerly core.py lines ~547-2136). Covers:
weather-override validation (`_god_active_weather_override`,
`_validate_god_weather_override`), the timed lawgiver-modifier reader
(`_divine_modifier`), repair_structures/clear_ruins selection helpers
(`_god_select_repair_structures`, `_god_select_clear_ruins`,
`_god_project_structure_repair`), the large per-kind command-envelope
validator/canonicalizer `_validate_god_envelope` (moved whole, not split —
every `if kind == ...` branch it dispatches to stays inside this one method
body exactly as it was in core.py), the command digest/preview-outcome
projection cluster (`_god_command_digest`, `_god_preview_outcome`,
`_god_custom_rule_gather_context`, `_god_reversibility_class`,
`_god_current_outgoing_guidance_id`, `_god_current_revoke_target`,
`_god_target_fingerprint`), and the target-fingerprint staleness check
`_god_check_fingerprint`.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodValidationMixin:
    """Mixin slice of SimEngine: Sovereign God mode command validation --
    weather override, timed lawgiver modifiers, repair/clear-ruins selection,
    the full per-kind envelope validator (including `_validate_god_envelope`
    moved whole), and the preview-outcome/digest/fingerprint cluster. See
    module docstring for exact scope."""

    # --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 6) ---
    def _god_active_weather_override(self, exclude_id=None):
        """The currently-active weather_override activeEvents record, or
        None -- "one active weather override at a time" (docs/archive/plan-sovereign-god-mode-v2.md
        Validation), mirroring _god_active_event_holding_key's shape for the
        story_event one-value-per-key policy."""
        god = self.civilization.get("godState") or {}
        for event in god.get("activeEvents") or []:
            if not isinstance(event, dict) or event.get("status") != "active":
                continue
            if event.get("kind") != "weather_override":
                continue
            if exclude_id is not None and event.get("id") == exclude_id:
                continue
            return event
        return None

    def _validate_god_weather_override(self, payload):
        """Validate + canonicalize a weather_override command. NO mutation.
        docs/archive/plan-sovereign-god-mode-v2.md Phase 6 "Validation": requires WEATHER_ENABLED; state must
        be a real machine state; district ids must exist, with an empty list
        allowed only for "clear" (the only state the natural machine itself
        ever clears districts for -- gathering/storm/clearing all require at
        least one, matching what _weather_enter actually does for each);
        duration uses the existing god duration clamp; one active override at
        a time unless replaceEffectId names the currently active one."""
        if not WEATHER_ENABLED:
            return None, "weather_override requires WEATHER_ENABLED"

        state = payload.get("state")
        if state not in WEATHER_STATES:
            return None, f"state must be one of {WEATHER_STATES}"

        districts_raw = payload.get("districts")
        if districts_raw is None:
            districts_raw = []
        if not isinstance(districts_raw, list):
            return None, "districts must be a list"
        if state == "clear":
            if districts_raw:
                return None, 'state "clear" does not take districts (must be empty)'
            districts = []
        else:
            if not districts_raw:
                return None, f'state "{state}" requires at least one district'
            districts = []
            seen = set()
            for d in districts_raw:
                if not isinstance(d, str) or d not in self.civilization["districts"]:
                    return None, f"unknown district id '{d}'"
                if d not in seen:
                    seen.add(d)
                    districts.append(d)

        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)

        replace_effect_id = payload.get("replaceEffectId")
        if replace_effect_id is not None and (
            not isinstance(replace_effect_id, str) or not replace_effect_id.strip()
        ):
            return None, "replaceEffectId must be a non-empty string"
        if isinstance(replace_effect_id, str):
            replace_effect_id = replace_effect_id.strip()
            replaced = self._god_active_weather_override()
            if replaced is None or replaced.get("id") != replace_effect_id:
                return None, "replaceEffectId does not name the active weather override"
        else:
            existing = self._god_active_weather_override()
            if existing is not None:
                return None, (f"a weather override is already active (id {existing.get('id')}) "
                              "-- supply replaceEffectId to replace it")

        return {"kind": "weather_override",
                "payload": {"state": state, "districts": districts,
                           "durationFrames": duration, "replaceEffectId": replace_effect_id}}, None

    # --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 5) ---
    def _divine_modifier(self, key, default=1.0):
        """The one active value for `key` (docs/archive/plan-sovereign-god-mode-v2.md "Timed lawgiver
        modifiers" + "Expiry ownership"), or exactly `default` when the flag
        is off, godState is missing/malformed, or no activeEvents record
        currently carries that key within [startFrame, expiresFrame). Every
        consumer site multiplies its own local delta/amount by this value,
        so an effective 1.0 (flag off, or flag on with no matching active
        effect) executes the identical arithmetic as the feature-off
        baseline -- see each consumer site's comment for the exact ordering.
        Called from inside the tick loop / apply_decision, both of which
        already hold self.lock -- this method never acquires it itself."""
        if not GOD_MODE_ENABLED:
            return default
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return default
        events = god.get("activeEvents")
        if not isinstance(events, list):
            return default
        ft = self.frameTick
        for event in events[:GOD_ACTIVE_EVENTS_CAP]:
            if not isinstance(event, dict) or event.get("status") != "active":
                continue
            modifiers = event.get("modifiers")
            if not isinstance(modifiers, dict) or key not in modifiers:
                continue
            start = event.get("startFrame")
            expires = event.get("expiresFrame")
            if not isinstance(start, int) or not isinstance(expires, int):
                continue
            if start <= ft < expires:
                value = modifiers[key]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
        return default

    def _god_select_repair_structures(self, payload):
        """Resolve repair_structures target set. Returns (structures, reason)."""
        scope = payload.get("scope")
        un_ruin = payload.get("unRuin", True)
        c = self.civilization
        if scope == "ids":
            ids = set(payload.get("structureIds") or [])
            structures = [s for s in c["structures"] if s.get("id") in ids]
            if len(structures) != len(ids):
                return None, "unknown structure id in structureIds"
        elif scope == "all_critical":
            structures = [
                s for s in c["structures"]
                if s.get("type") in REPAIR_CAMPAIGN_CRITICAL_TYPES
            ]
        elif isinstance(scope, dict) and "districtId" in scope:
            did = scope["districtId"]
            if did not in c.get("districts", {}):
                return None, "unknown district"
            structures = [s for s in c["structures"] if s.get("districtId") == did]
        else:
            return None, 'scope must be "ids", "all_critical", or {"districtId": "<id>"}'
        if not un_ruin:
            ruined = [s for s in structures if s.get("isRuin") or s.get("condition", 100) <= 0]
            if ruined:
                return None, "unRuin must be true to include ruined structures"
        return structures, None

    def _god_select_clear_ruins(self, payload):
        """Resolve clear_ruins target ruins. Returns (ruins, reason)."""
        c = self.civilization
        structure_ids = payload.get("structureIds")
        district_id = payload.get("districtId")
        min_age = payload.get("minAgeFrames", RUIN_CULL_AGE_FRAMES)
        if structure_ids is not None:
            ruins = []
            for sid in structure_ids:
                s = next((x for x in c["structures"] if x.get("id") == sid), None)
                if s is None:
                    return None, f"unknown structure id {sid}"
                if not s.get("isRuin"):
                    return None, f"structure {sid} is not a ruin"
                ruins.append(s)
            return ruins, None
        pool = [s for s in c["structures"] if s.get("isRuin")]
        if district_id is not None:
            if district_id not in c.get("districts", {}):
                return None, "unknown district"
            pool = [s for s in pool if s.get("districtId") == district_id]
        if min_age is not None:
            pool = [
                s for s in pool
                if self.frameTick - s.get("ruinedSinceFrame", 0) >= min_age
            ]
        return pool, None

    def _god_project_structure_repair(self, structure, condition_target, un_ruin):
        """Non-mutating preview of one repair_structures entry."""
        old_cond = structure.get("condition", 100.0)
        was_ruin = bool(structure.get("isRuin")) or old_cond <= 0
        if was_ruin:
            if not un_ruin:
                return None
            target = condition_target if condition_target is not None else REPAIR_CONDITION_RESTORE
            new_cond = max(REPAIR_CONDITION_RESTORE, min(100.0, float(target)))
        elif condition_target is not None:
            delta = float(condition_target) - old_cond
            delta = max(-GOD_REPAIR_STRUCTURES_CONDITION_MAX,
                        min(GOD_REPAIR_STRUCTURES_CONDITION_MAX, delta))
            new_cond = min(100.0, old_cond + delta) if delta >= 0 else max(0.0, old_cond + delta)
        else:
            new_cond = min(100.0, old_cond + REPAIR_CONDITION_RESTORE)
        return {
            "structureId": structure["id"],
            "structureName": structure.get("name") or structure.get("type"),
            "oldCondition": old_cond,
            "newCondition": new_cond,
            "unRuined": was_ruin and un_ruin,
        }

    def _validate_god_envelope(self, envelope):
        """Validate + canonicalize a {kind, payload, expectedFrame} command
        envelope. NO mutation. Returns (normalized_command, reason);
        normalized_command is None on rejection."""
        if not isinstance(envelope, dict):
            return None, "envelope must be an object"
        kind = envelope.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            return None, "kind is required"
        kind = kind.strip()
        payload = envelope.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return None, "payload must be an object"
        expected_frame = envelope.get("expectedFrame")
        if expected_frame is not None and not isinstance(expected_frame, int):
            return None, "expectedFrame must be an integer"

        if kind == "proclamation":
            text, reason = self._normalize_divine_text(payload.get("text"))
            if reason:
                return None, f"proclamation text {reason}"
            presentation, pres_reason = self._normalize_god_presentation(
                payload.get("presentation"))
            if pres_reason:
                return None, pres_reason
            out_payload = {"text": text, **self._god_presentation_payload_field(presentation)}
            return {"kind": "proclamation", "payload": out_payload}, None

        if kind == "providence":
            text, reason = self._normalize_divine_text(payload.get("text"))
            if reason:
                return None, f"providence text {reason}"
            presentation, pres_reason = self._normalize_god_presentation(
                payload.get("presentation"))
            if pres_reason:
                return None, pres_reason
            duration_raw = payload.get("durationFrames")
            if duration_raw is not None and (
                isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
            ):
                return None, "durationFrames must be an integer"
            duration = self._clamp_god_duration(duration_raw)
            out_payload = {
                "text": text,
                "durationFrames": duration,
                **self._god_presentation_payload_field(presentation),
            }
            return {"kind": "providence", "payload": out_payload}, None

        if kind == "private_omen":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            text, reason = self._normalize_divine_text(payload.get("text"))
            if reason:
                return None, f"private_omen text {reason}"
            duration_raw = payload.get("durationFrames")
            if duration_raw is not None and (
                isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
            ):
                return None, "durationFrames must be an integer"
            duration = self._clamp_god_duration(duration_raw)
            return {"kind": "private_omen",
                    "payload": {"targetId": target_id, "text": text,
                               "durationFrames": duration}}, None

        if kind == "whisper_campaign":
            theme, reason = self._normalize_divine_text(payload.get("theme"))
            if reason:
                return None, f"whisper_campaign theme {reason}"
            whispers_raw = payload.get("whispers")
            if not isinstance(whispers_raw, list) or not whispers_raw:
                return None, "whispers must be a non-empty list"
            if len(whispers_raw) > GOD_WHISPER_CAMPAIGN_MAX_TARGETS:
                return None, (
                    f"whispers may include at most {GOD_WHISPER_CAMPAIGN_MAX_TARGETS} targets")
            seen_targets = set()
            whispers = []
            for idx, entry in enumerate(whispers_raw):
                if not isinstance(entry, dict):
                    return None, f"whispers[{idx}] must be an object"
                target_id = entry.get("targetId")
                if isinstance(target_id, bool) or not isinstance(target_id, int):
                    return None, f"whispers[{idx}].targetId must be an integer agent id"
                if target_id in seen_targets:
                    return None, "duplicate targetId in whispers"
                agent = self._find_agent_by_id(target_id)
                if agent is None:
                    return None, f"whispers[{idx}]: unknown target agent"
                if agent.get("deathFrame") is not None:
                    return None, f"whispers[{idx}]: target agent is deceased"
                text, reason = self._normalize_divine_text(entry.get("text"))
                if reason:
                    return None, f"whispers[{idx}].text {reason}"
                seen_targets.add(target_id)
                whispers.append({"targetId": target_id, "text": text})
            duration_raw = payload.get("durationFrames")
            if duration_raw is not None and (
                isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
            ):
                return None, "durationFrames must be an integer"
            duration = self._clamp_god_duration(duration_raw)
            return {"kind": "whisper_campaign",
                    "payload": {"theme": theme, "whispers": whispers,
                                "durationFrames": duration}}, None

        if kind == "crowd_compulsion":
            normalized, reason = self._validate_god_crowd_compulsion(payload)
            if reason:
                return None, reason
            return {"kind": "crowd_compulsion", "payload": normalized}, None

        if kind == "dream_broadcast":
            normalized, reason = self._validate_god_dream_broadcast(payload)
            if reason:
                return None, reason
            return {"kind": "dream_broadcast", "payload": normalized}, None

        if kind == "agent_sampling":
            normalized, reason = self._validate_god_agent_sampling(payload)
            if reason:
                return None, reason
            return {"kind": "agent_sampling", "payload": normalized}, None

        if kind == "revoke_agent_sampling":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            if self._god_active_agent_sampling_record(target_id) is None:
                return None, "no active sampling override for target agent"
            return {"kind": "revoke_agent_sampling",
                    "payload": {"targetId": target_id}}, None

        if kind == "memory_insert":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            text, reason = self._normalize_divine_text(payload.get("text"))
            if reason:
                return None, f"memory_insert text {reason}"
            salience_raw = payload.get("salience")
            if salience_raw is None:
                salience = 0.7
            elif isinstance(salience_raw, bool) or not isinstance(salience_raw, (int, float)):
                return None, "salience must be a number"
            elif not math.isfinite(salience_raw):
                return None, "salience must be finite"
            else:
                salience = max(0.0, min(1.0, float(salience_raw)))
            mem_kind = payload.get("kind")
            if mem_kind is None:
                mem_kind = GOD_MEMORY_DEFAULT_KIND
            elif not isinstance(mem_kind, str) or not mem_kind.strip():
                return None, "kind must be a non-empty string"
            else:
                mem_kind = mem_kind.strip()
                if len(mem_kind) > GOD_MEMORY_KIND_MAX_LEN:
                    return None, f"kind exceeds {GOD_MEMORY_KIND_MAX_LEN} characters"
            return {"kind": "memory_insert",
                    "payload": {"targetId": target_id, "text": text,
                                "salience": salience, "kind": mem_kind}}, None

        if kind == "memory_delete":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            keyword = payload.get("keyword")
            if keyword is not None:
                if not isinstance(keyword, str) or not keyword.strip():
                    return None, "keyword must be a non-empty string"
                keyword = keyword.strip()
            frame_from = payload.get("frameFrom")
            if frame_from is not None and (
                isinstance(frame_from, bool) or not isinstance(frame_from, int)
            ):
                return None, "frameFrom must be an integer"
            frame_to = payload.get("frameTo")
            if frame_to is not None and (
                isinstance(frame_to, bool) or not isinstance(frame_to, int)
            ):
                return None, "frameTo must be an integer"
            kinds_raw = payload.get("kinds")
            kinds = None
            if kinds_raw is not None:
                if not isinstance(kinds_raw, list) or not kinds_raw:
                    return None, "kinds must be a non-empty list"
                kinds = []
                for idx, entry in enumerate(kinds_raw):
                    if not isinstance(entry, str) or not entry.strip():
                        return None, f"kinds[{idx}] must be a non-empty string"
                    kinds.append(entry.strip())
            if not any(x is not None for x in (keyword, frame_from, frame_to, kinds)):
                return None, "at least one of keyword/frameFrom/frameTo/kinds is required"
            normalized = {"targetId": target_id}
            if keyword is not None:
                normalized["keyword"] = keyword
            if frame_from is not None:
                normalized["frameFrom"] = frame_from
            if frame_to is not None:
                normalized["frameTo"] = frame_to
            if kinds is not None:
                normalized["kinds"] = kinds
            return {"kind": "memory_delete", "payload": normalized}, None

        if kind == "context_mask":
            normalized, reason = self._validate_god_context_mask(payload)
            if reason:
                return None, reason
            return {"kind": "context_mask", "payload": normalized}, None

        if kind == "decision_compulsion":
            normalized, reason = self._validate_god_decision_compulsion(payload)
            if reason:
                return None, reason
            return {"kind": "decision_compulsion", "payload": normalized}, None

        if kind == "decision_veto_arm":
            normalized, reason = self._validate_god_decision_veto_arm(payload)
            if reason:
                return None, reason
            return {"kind": "decision_veto_arm", "payload": normalized}, None

        if kind == "decision_veto_resolve":
            normalized, reason = self._validate_god_decision_veto_resolve(payload)
            if reason:
                return None, reason
            return {"kind": "decision_veto_resolve", "payload": normalized}, None

        if kind == "agent_possession":
            normalized, reason = self._validate_god_agent_possession(payload)
            if reason:
                return None, reason
            return {"kind": "agent_possession", "payload": normalized}, None

        if kind == "revoke_decision_gate":
            normalized, reason = self._validate_god_revoke_decision_gate(payload)
            if reason:
                return None, reason
            return {"kind": "revoke_decision_gate", "payload": normalized}, None

        if kind == "burning_bush_message":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            text, reason = self._normalize_divine_text(payload.get("text"))
            if reason:
                return None, f"burning_bush_message text {reason}"
            return {"kind": "burning_bush_message",
                    "payload": {"targetId": target_id, "text": text}}, None

        if kind == "burning_bush_close":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            key = str(target_id)
            bush = (self.civilization.get("godState") or {}).get("burningBush", {}).get(key)
            if not isinstance(bush, dict) or bush.get("status") != "active":
                return None, "no active burning bush session for target agent"
            return {"kind": "burning_bush_close",
                    "payload": {"targetId": target_id}}, None

        if kind == "merovingian_bargain":
            normalized, reason = self._validate_god_merovingian_bargain(payload)
            if reason:
                return None, reason
            return {"kind": "merovingian_bargain", "payload": normalized}, None

        if kind == "bargain_settle":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            outcome = payload.get("outcome")
            if outcome not in ("success", "failure"):
                return None, 'outcome must be "success" or "failure"'
            key = str(target_id)
            bush = (self.civilization.get("godState") or {}).get("burningBush", {}).get(key)
            if not isinstance(bush, dict):
                return None, "no burning bush session for target agent"
            bargain = bush.get("bargain")
            if not isinstance(bargain, dict) or bargain.get("status") != "open":
                return None, "no open bargain for target agent"
            return {"kind": "bargain_settle",
                    "payload": {"targetId": target_id, "outcome": outcome}}, None

        if kind == "anoint":
            normalized, reason = self._validate_god_anoint(payload)
            if reason:
                return None, reason
            return {"kind": "anoint", "payload": normalized}, None

        if kind == "revoke_anoint":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            if self._god_active_anointment_record(target_id) is None:
                return None, "no active anointment for target agent"
            return {"kind": "revoke_anoint",
                    "payload": {"targetId": target_id}}, None

        if kind == "identity_edit":
            normalized, reason = self._validate_god_identity_edit(payload)
            if reason:
                return None, reason
            return {"kind": "identity_edit", "payload": normalized}, None

        if kind == "identity_copy_overwrite":
            normalized, reason = self._validate_god_identity_copy_overwrite(payload)
            if reason:
                return None, reason
            return {"kind": "identity_copy_overwrite", "payload": normalized}, None

        if kind == "identity_forge_cancel":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            if self._god_active_identity_forge_record(target_id) is None:
                return None, "no active identity forge for target agent"
            return {"kind": "identity_forge_cancel",
                    "payload": {"targetId": target_id}}, None

        if kind == "checkpoint_create":
            label_raw = payload.get("label")
            label, reason = self._normalize_divine_text(label_raw)
            if reason:
                return None, f"checkpoint_create label {reason}"
            replace_raw = payload.get("replaceOldest")
            if replace_raw is not None and not isinstance(replace_raw, bool):
                return None, "replaceOldest must be a boolean when provided"
            replace_oldest = bool(replace_raw)
            checkpoints = (self.civilization.get("godState") or {}).get("checkpoints") or []
            if len(checkpoints) >= GOD_CHECKPOINT_MAX and not replace_oldest:
                return None, (
                    f"checkpoint cap ({GOD_CHECKPOINT_MAX}) reached; "
                    "set replaceOldest to true to drop the oldest checkpoint"
                )
            return {"kind": "checkpoint_create",
                    "payload": {"label": label, "replaceOldest": replace_oldest}}, None

        if kind == "checkpoint_restore":
            checkpoint_id = payload.get("checkpointId")
            if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                return None, "checkpointId is required"
            checkpoint_id = checkpoint_id.strip()
            if self._god_checkpoint_by_id(checkpoint_id) is None:
                return None, "checkpoint not found"
            return {"kind": "checkpoint_restore",
                    "payload": {"checkpointId": checkpoint_id}}, None

        if kind == "deja_vu_replay":
            normalized, reason = self._validate_god_deja_vu_replay(payload)
            if reason:
                return None, reason
            return {"kind": "deja_vu_replay", "payload": normalized}, None

        if kind == "architect_zone":
            normalized, reason = self._validate_god_architect_zone(payload)
            if reason:
                return None, reason
            return {"kind": "architect_zone", "payload": normalized}, None

        if kind == "architect_zone_cancel":
            zone_id = payload.get("zoneId")
            if not isinstance(zone_id, str) or not zone_id.strip():
                return None, "zoneId is required"
            if self._god_architect_zone_by_id(zone_id.strip()) is None:
                return None, "architect zone not found or already inactive"
            return {"kind": "architect_zone_cancel",
                    "payload": {"zoneId": zone_id.strip()}}, None

        if kind == "architect_release_hold":
            zone_id = payload.get("zoneId")
            if not isinstance(zone_id, str) or not zone_id.strip():
                return None, "zoneId is required"
            zone = self._god_architect_zone_by_id(zone_id.strip())
            if zone is None or zone.get("kind") != "limbo":
                return None, "no active limbo architect zone for zoneId"
            agent_ids = None
            if payload.get("agentIds") is not None:
                if not isinstance(payload.get("agentIds"), list):
                    return None, "agentIds must be a list when provided"
                agent_ids = []
                for raw_id in payload["agentIds"]:
                    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                        return None, "agentIds entries must be integer agent ids"
                    agent_ids.append(raw_id)
            normalized = {"zoneId": zone_id.strip()}
            if agent_ids is not None:
                normalized["agentIds"] = agent_ids
            return {"kind": "architect_release_hold", "payload": normalized}, None

        if kind == "belief_plant":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"
            if not MEMES_ENABLED:
                return None, "memes disabled"
            belief_id = payload.get("beliefId")
            if belief_id is not None:
                if not isinstance(belief_id, str) or not belief_id.strip():
                    return None, "beliefId must be a non-empty string"
                belief_id = belief_id.strip()
            text_raw = payload.get("text")
            custom_text = None
            if text_raw is not None:
                custom_text, reason = self._normalize_divine_text(text_raw)
                if reason:
                    return None, f"belief_plant text {reason}"
            if not belief_id and not custom_text:
                return None, "at least one of beliefId or text is required"
            plant_raw = payload.get("plantInMemeTexts")
            if not isinstance(plant_raw, bool):
                return None, "plantInMemeTexts must be a boolean"
            salience_raw = payload.get("salience")
            if salience_raw is None:
                salience = 0.7
            elif isinstance(salience_raw, bool) or not isinstance(salience_raw, (int, float)):
                return None, "salience must be a number"
            elif not math.isfinite(salience_raw):
                return None, "salience must be finite"
            else:
                salience = max(0.0, min(1.0, float(salience_raw)))
            if belief_id:
                registry = self._belief_registry()
                if belief_id not in registry and belief_id not in MEMES:
                    return None, "unknown belief id"
            normalized = {
                "targetId": target_id,
                "plantInMemeTexts": plant_raw,
                "salience": salience,
            }
            if belief_id:
                normalized["beliefId"] = belief_id
            if custom_text:
                normalized["text"] = custom_text
            return {"kind": "belief_plant", "payload": normalized}, None

        if kind == "revoke_guidance":
            guidance_id = payload.get("id")
            if not isinstance(guidance_id, str) or not guidance_id.strip():
                return None, "id is required"
            return {"kind": "revoke_guidance",
                    "payload": {"id": guidance_id.strip()}}, None

        # --- Sovereign God mode Phase 4: bounded immediate miracles ---
        if kind == "agent_vitals":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, "unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, "target agent is deceased"

            def _vitals_num(raw, label):
                if raw is None:
                    return 0.0, None
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    return None, f"{label} must be a number"
                if not math.isfinite(raw):
                    return None, f"{label} must be finite"
                if abs(raw) > GOD_VITALS_DELTA_MAX:
                    return None, f"{label} magnitude exceeds {GOD_VITALS_DELTA_MAX}"
                return float(raw), None

            health_delta, reason = _vitals_num(payload.get("healthDelta"), "healthDelta")
            if reason:
                return None, reason
            hunger_delta, reason = _vitals_num(payload.get("hungerDelta"), "hungerDelta")
            if reason:
                return None, reason
            if health_delta == 0.0 and hunger_delta == 0.0:
                return None, "at least one of healthDelta/hungerDelta must be non-zero"
            return {"kind": "agent_vitals",
                    "payload": {"targetId": target_id, "healthDelta": health_delta,
                               "hungerDelta": hunger_delta}}, None

        if kind == "grant_resource":
            resource_id = payload.get("resourceId")
            if not isinstance(resource_id, str) or not resource_id.strip():
                return None, "resourceId is required"
            resource_id = resource_id.strip()
            if resource_id not in self.civilization.get("resourceRegistry", {}):
                return None, "unknown resource id"
            amount = payload.get("amount")
            if isinstance(amount, bool) or not isinstance(amount, int):
                return None, "amount must be an integer"
            if amount <= 0:
                return None, "amount must be positive"
            if amount > GOD_GRANT_PER_COMMAND_CAP:
                return None, f"amount exceeds the per-command cap of {GOD_GRANT_PER_COMMAND_CAP}"
            if self._god_grant_session_total + amount > GOD_GRANT_SESSION_CAP:
                return None, f"amount would exceed the per-session cap of {GOD_GRANT_SESSION_CAP}"
            # NOTE: the normalized "target" field below deliberately keeps the
            # SAME shape as the raw input ("stockpile" string, or {"agentId":
            # int}) rather than expanding it into separate targetKind/
            # targetAgentId keys -- apply-time revalidation re-runs this exact
            # validator over its OWN previously normalized output (see
            # god_apply), so the normalized shape must be idempotent under
            # re-validation or a stale digest mismatch fires on every apply.
            target = payload.get("target")
            if target is None:
                target = "stockpile"
            if target == "stockpile":
                normalized_target = "stockpile"
            elif isinstance(target, dict) and "agentId" in target:
                agent_id = target.get("agentId")
                if isinstance(agent_id, bool) or not isinstance(agent_id, int):
                    return None, "target.agentId must be an integer agent id"
                agent = self._find_agent_by_id(agent_id)
                if agent is None:
                    return None, "unknown target agent"
                if agent.get("deathFrame") is not None:
                    return None, "target agent is deceased"
                normalized_target = {"agentId": agent_id}
            else:
                return None, 'target must be "stockpile" or {"agentId": <int>}'
            return {"kind": "grant_resource",
                    "payload": {"resourceId": resource_id, "amount": amount,
                               "target": normalized_target}}, None

        if kind == "structure_condition":
            structure_id = payload.get("structureId")
            if isinstance(structure_id, bool) or not isinstance(structure_id, int):
                return None, "structureId must be an integer"
            structure = next((s for s in self.civilization["structures"]
                              if s.get("id") == structure_id), None)
            if structure is None:
                return None, "unknown structure"
            if structure.get("isRuin") or structure.get("condition", 100.0) <= 0:
                return None, "structure is already ruined"
            delta = payload.get("delta")
            if isinstance(delta, bool) or not isinstance(delta, (int, float)):
                return None, "delta must be a number"
            if not math.isfinite(delta):
                return None, "delta must be finite"
            if delta == 0:
                return None, "delta must be non-zero"
            if abs(delta) > GOD_STRUCTURE_DELTA_MAX:
                return None, f"delta magnitude exceeds {GOD_STRUCTURE_DELTA_MAX}"
            return {"kind": "structure_condition",
                    "payload": {"structureId": structure_id, "delta": float(delta)}}, None

        if kind == "repair_structures":
            scope = payload.get("scope")
            if scope == "ids":
                structure_ids = payload.get("structureIds")
                if not isinstance(structure_ids, list) or not structure_ids:
                    return None, "structureIds must be a non-empty list when scope is ids"
                seen = set()
                for sid in structure_ids:
                    if isinstance(sid, bool) or not isinstance(sid, int):
                        return None, "structureIds must contain integers"
                    if sid in seen:
                        return None, "structureIds must be unique"
                    seen.add(sid)
            elif scope == "all_critical":
                pass
            elif isinstance(scope, dict) and "districtId" in scope:
                did = scope["districtId"]
                if not isinstance(did, str) or not did.strip():
                    return None, "districtId must be a non-empty string"
                if did.strip() not in self.civilization.get("districts", {}):
                    return None, "unknown district"
            else:
                return None, 'scope must be "ids", "all_critical", or {"districtId": "<id>"}'
            un_ruin = payload.get("unRuin", True)
            if not isinstance(un_ruin, bool):
                return None, "unRuin must be a boolean"
            condition_target = payload.get("conditionTarget")
            if condition_target is not None:
                if isinstance(condition_target, bool) or not isinstance(condition_target, (int, float)):
                    return None, "conditionTarget must be a number"
                if not math.isfinite(condition_target):
                    return None, "conditionTarget must be finite"
                if not (0 <= condition_target <= 100):
                    return None, "conditionTarget must be between 0 and 100"
            structures, sel_reason = self._god_select_repair_structures({
                "scope": scope,
                "structureIds": payload.get("structureIds"),
                "unRuin": un_ruin,
            })
            if sel_reason:
                return None, sel_reason
            if not structures:
                return None, "empty structure selection"
            if len(structures) > GOD_REPAIR_STRUCTURES_BATCH_MAX:
                return None, f"batch exceeds cap of {GOD_REPAIR_STRUCTURES_BATCH_MAX} structures"
            norm_payload = {"scope": scope, "unRuin": un_ruin}
            if scope == "ids":
                norm_payload["structureIds"] = list(payload.get("structureIds") or [])
            if condition_target is not None:
                norm_payload["conditionTarget"] = float(condition_target)
            return {"kind": "repair_structures", "payload": norm_payload}, None

        if kind == "clear_ruins":
            structure_ids = payload.get("structureIds")
            district_id = payload.get("districtId")
            min_age = payload.get("minAgeFrames", RUIN_CULL_AGE_FRAMES)
            if structure_ids is None and district_id is None and "minAgeFrames" not in payload:
                return None, "at least one selector is required (structureIds, districtId, or minAgeFrames)"
            if structure_ids is not None:
                if not isinstance(structure_ids, list) or not structure_ids:
                    return None, "structureIds must be a non-empty list"
            if district_id is not None:
                if not isinstance(district_id, str) or not district_id.strip():
                    return None, "districtId must be a non-empty string"
                if district_id.strip() not in self.civilization.get("districts", {}):
                    return None, "unknown district"
            if min_age is not None:
                if isinstance(min_age, bool) or not isinstance(min_age, int):
                    return None, "minAgeFrames must be an integer"
                if min_age < 0:
                    return None, "minAgeFrames must be non-negative"
            ruins, sel_reason = self._god_select_clear_ruins({
                "structureIds": structure_ids,
                "districtId": district_id.strip() if isinstance(district_id, str) else district_id,
                "minAgeFrames": min_age,
            })
            if sel_reason:
                return None, sel_reason
            if not ruins:
                return None, "empty ruin selection"
            if len(ruins) > GOD_CLEAR_RUINS_BATCH_MAX:
                return None, f"batch exceeds cap of {GOD_CLEAR_RUINS_BATCH_MAX} ruins"
            norm_payload = {}
            if structure_ids is not None:
                norm_payload["structureIds"] = list(structure_ids)
            if district_id is not None:
                norm_payload["districtId"] = district_id.strip()
            if "minAgeFrames" in payload:
                norm_payload["minAgeFrames"] = min_age
            elif structure_ids is None and district_id is None:
                norm_payload["minAgeFrames"] = min_age
            return {"kind": "clear_ruins", "payload": norm_payload}, None

        # --- Sovereign God mode Phase 6: weather override ---
        if kind == "weather_override":
            return self._validate_god_weather_override(payload)

        # --- Sovereign God mode Phase 5: storyteller events ---
        if kind == "story_event":
            return self._validate_god_story_event(payload)

        # --- Huntable wildlife god kinds (specs/02-engine-core.md) ---
        if kind == "wildlife_spawn":
            if not WILDLIFE_ENABLED:
                return None, "wildlife is disabled"
            district_id = payload.get("districtId")
            if not isinstance(district_id, str) or not district_id.strip():
                return None, "districtId is required"
            district_id = district_id.strip()
            if district_id not in self.civilization.get("districts", {}):
                return None, "unknown district"
            dkind = self._wildlife_district_kind(district_id)
            if not dkind:
                return None, "district is not a wildlife habitat"
            creature_kind = payload.get("kind")
            if not isinstance(creature_kind, str) or not creature_kind.strip():
                return None, "kind is required"
            creature_kind = creature_kind.strip()
            pool = WILDLIFE_KIND_POOLS.get(dkind, [])
            if creature_kind not in pool:
                return None, f"kind '{creature_kind}' is not valid for {dkind} habitat"
            if len(self._wildlife_alive_in_district(district_id)) >= WILDLIFE_CAP_PER_DISTRICT:
                return None, f"district is at wildlife cap ({WILDLIFE_CAP_PER_DISTRICT})"
            return {"kind": "wildlife_spawn",
                    "payload": {"districtId": district_id, "kind": creature_kind}}, None

        if kind == "wildlife_despawn":
            if not WILDLIFE_ENABLED:
                return None, "wildlife is disabled"
            creature_id = payload.get("id")
            district_id = payload.get("districtId")
            has_id = creature_id is not None
            has_district = district_id is not None
            if has_id == has_district:
                return None, "exactly one of id or districtId is required"
            if has_id:
                if not isinstance(creature_id, str) or not creature_id.strip():
                    return None, "id must be a non-empty string"
                creature_id = creature_id.strip()
                cre = self._find_wildlife_by_id(creature_id)
                if cre is None:
                    return None, "unknown wildlife id"
                if not cre.get("alive"):
                    return None, "wildlife is already dead"
                return {"kind": "wildlife_despawn",
                        "payload": {"id": creature_id}}, None
            if not isinstance(district_id, str) or not district_id.strip():
                return None, "districtId must be a non-empty string"
            district_id = district_id.strip()
            if district_id not in self.civilization.get("districts", {}):
                return None, "unknown district"
            if not self._wildlife_alive_in_district(district_id):
                return None, "no living wildlife in district"
            return {"kind": "wildlife_despawn",
                    "payload": {"districtId": district_id}}, None

        if kind == "wildlife_set_hp":
            if not WILDLIFE_ENABLED:
                return None, "wildlife is disabled"
            creature_id = payload.get("id")
            if not isinstance(creature_id, str) or not creature_id.strip():
                return None, "id is required"
            creature_id = creature_id.strip()
            cre = self._find_wildlife_by_id(creature_id)
            if cre is None:
                return None, "unknown wildlife id"
            hp = payload.get("hp")
            if isinstance(hp, bool) or not isinstance(hp, int):
                return None, "hp must be an integer"
            if hp < 0:
                return None, "hp must be non-negative"
            max_hp = int(cre.get("maxHp") or WILDLIFE_MAX_HP.get(cre.get("kind"), 1))
            # Normalize to the clamped value so revalidation is idempotent
            # (same discipline as grant_resource's normalized shape).
            clamped_hp = max(0, min(max_hp, hp))
            return {"kind": "wildlife_set_hp",
                    "payload": {"id": creature_id, "hp": clamped_hp}}, None

        if kind in self._GOD_FUTURE_KINDS:
            return None, f"kind '{kind}' is not implemented in this phase"
        return None, f"unknown kind '{kind}'"

    def _god_command_digest(self, normalized_command):
        blob = json.dumps(normalized_command, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _god_preview_outcome(self, normalized):
        """Sovereign God mode Phase 4: a non-mutating projection of the EXACT
        clamped/bounded value an immediate miracle would apply, computed
        against CURRENT live state (docs/archive/plan-sovereign-god-mode-v2.md Phase 4: "Preview must show the
        exact clamped/bounded value that will be applied, plus the affected
        target, computed against current world state"). Uses the identical
        clamp arithmetic the corresponding _god_apply_* helper uses, so as
        long as no other mutation touches the same target between preview and
        apply, the value shown here is the value that gets applied. Returns
        None for kinds with no derived value to preview (every Phase 2/3
        kind, and any target that has vanished since preview -- apply-time
        revalidation is the authoritative rejection path for that)."""
        kind = normalized["kind"]
        payload = normalized["payload"]
        if kind == "agent_vitals":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            health_delta, hunger_delta = payload["healthDelta"], payload["hungerDelta"]
            old_health, old_hunger = agent["health"], agent["hunger"]
            new_health = old_health
            if health_delta:
                new_health = (min(100.0, old_health + health_delta) if health_delta >= 0
                             else max(GOD_VITALS_HEALTH_FLOOR, old_health + health_delta))
            new_hunger = old_hunger
            if hunger_delta:
                new_hunger = max(0.0, min(100.0, old_hunger + hunger_delta))
            return {"targetId": payload["targetId"], "targetName": agent["name"],
                    "oldHealth": old_health, "newHealth": new_health,
                    "oldHunger": old_hunger, "newHunger": new_hunger}
        if kind == "grant_resource":
            amount = payload["amount"]
            target = payload["target"]
            if target == "stockpile":
                return {"resourceId": payload["resourceId"], "amount": amount,
                        "targetKind": "stockpile", "agentAdded": 0, "stockpileAdded": amount}
            target_agent_id = target["agentId"]
            agent = self._find_agent_by_id(target_agent_id)
            if agent is None:
                return None
            cap = self._carry_cap(agent)
            held = agent["resources"].get(payload["resourceId"], 0)
            room = max(0, cap - held)
            agent_added = min(amount, room)
            stockpile_added = amount - agent_added
            return {"resourceId": payload["resourceId"], "amount": amount,
                    "targetKind": "agent", "targetAgentId": target_agent_id,
                    "targetName": agent["name"], "agentAdded": agent_added,
                    "stockpileAdded": stockpile_added}
        if kind == "structure_condition":
            structure = next((s for s in self.civilization["structures"]
                              if s.get("id") == payload["structureId"]), None)
            if structure is None:
                return None
            old_cond = structure.get("condition", 100.0)
            delta = payload["delta"]
            new_cond = min(100.0, old_cond + delta) if delta >= 0 else max(0.0, old_cond + delta)
            return {"structureId": payload["structureId"],
                    "structureName": structure.get("name") or structure.get("type"),
                    "oldCondition": old_cond, "newCondition": new_cond,
                    "wouldBecomeRuin": bool(delta < 0 and new_cond <= 0)}
        if kind == "repair_structures":
            structures, sel_reason = self._god_select_repair_structures(payload)
            if sel_reason or not structures:
                return None
            condition_target = payload.get("conditionTarget")
            un_ruin = payload.get("unRuin", True)
            outcomes = []
            for s in structures:
                projected = self._god_project_structure_repair(s, condition_target, un_ruin)
                if projected:
                    outcomes.append(projected)
            return {"structures": outcomes, "count": len(outcomes)}
        if kind == "clear_ruins":
            ruins, sel_reason = self._god_select_clear_ruins(payload)
            if sel_reason or not ruins:
                return None
            return {
                "structureIds": [s["id"] for s in ruins],
                "structureNames": [s.get("name") or s.get("type") for s in ruins],
                "count": len(ruins),
            }
        if kind == "story_event":
            modifiers = payload.get("modifiers") or {}
            outcome = {
                "modifiers": dict(modifiers),
                "primitives": [
                    {"kind": prim["kind"], "outcome": self._god_preview_outcome(prim)}
                    for prim in (payload.get("primitives") or [])
                ],
                "providenceOutgoingId": None,
            }
            # docs/archive/plan-sovereign-god-mode-v2.md "Divine law vs. village law": when a proposed
            # gather/fish modifier composes with an agent-authored custom
            # rule, preview discloses BOTH contributions separately so the
            # operator sees they are amplifying an emergent effect rather
            # than replacing it -- the divine multiplier is applied AFTER
            # the custom-rule addition at the gather consumer site.
            if "gather_yield_multiplier" in modifiers or "fish_yield_multiplier" in modifiers:
                outcome["customRuleContext"] = self._god_custom_rule_gather_context()
            if payload.get("providence"):
                outcome["providenceOutgoingId"] = self._god_current_outgoing_guidance_id("providence", {})
            return outcome
        if kind == "weather_override":
            # docs/archive/plan-sovereign-god-mode-v2.md Phase 6 Answer 4 ("consequential disclosure"):
            # preview must state explicitly that entering "storm" can
            # PERMANENTLY damage structures in the named districts, that
            # neither cancelling nor expiry undoes that damage, and must
            # list the exact districts plus the count of currently
            # non-ruined structures at risk in them -- computed against
            # CURRENT live state, same discipline as every other
            # _god_preview_outcome branch.
            state, districts = payload["state"], payload["districts"]
            w = self.civilization.get("weather") or {}
            at_risk = sum(
                1 for s in self.civilization["structures"]
                if s.get("districtId") in districts and not s.get("isRuin")
                and s.get("condition", 100.0) > 0
            ) if districts else 0
            warning = None
            if state == "storm":
                warning = ("Entering 'storm' can PERMANENTLY damage structures in the "
                          "named districts. That damage is not undone by cancelling this "
                          "override or by its natural expiry.")
            return {
                "state": state, "districts": list(districts),
                "priorState": w.get("state", "clear"),
                "atRiskStructureCount": at_risk,
                "warning": warning,
            }
        if kind == "wildlife_spawn":
            district_id = payload["districtId"]
            creature_kind = payload["kind"]
            alive = self._wildlife_alive_in_district(district_id)
            return {
                "districtId": district_id,
                "kind": creature_kind,
                "aliveCount": len(alive),
                "cap": WILDLIFE_CAP_PER_DISTRICT,
                "wouldSpawn": len(alive) < WILDLIFE_CAP_PER_DISTRICT,
            }
        if kind == "wildlife_despawn":
            if "id" in payload:
                cre = self._find_wildlife_by_id(payload["id"])
                if cre is None:
                    return None
                return {
                    "id": payload["id"],
                    "kind": cre.get("kind"),
                    "districtId": cre.get("districtId"),
                    "despawnCount": 1 if cre.get("alive") else 0,
                }
            district_id = payload["districtId"]
            alive = self._wildlife_alive_in_district(district_id)
            return {
                "districtId": district_id,
                "despawnCount": len(alive),
                "ids": [c.get("id") for c in alive],
            }
        if kind == "wildlife_set_hp":
            cre = self._find_wildlife_by_id(payload["id"])
            if cre is None:
                return None
            max_hp = int(cre.get("maxHp") or WILDLIFE_MAX_HP.get(cre.get("kind"), 1))
            new_hp = max(0, min(max_hp, int(payload["hp"])))
            return {
                "id": payload["id"],
                "kind": cre.get("kind"),
                "districtId": cre.get("districtId"),
                "oldHp": int(cre.get("hp") or 0),
                "newHp": new_hp,
                "maxHp": max_hp,
                "wouldKill": new_hp <= 0,
                "wasAlive": bool(cre.get("alive")),
            }
        if kind == "memory_insert":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "salience": payload["salience"],
                "kind": payload["kind"],
            }
        if kind == "memory_delete":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "wouldDelete": self._god_memory_match_count(
                    agent,
                    keyword=payload.get("keyword"),
                    frameFrom=payload.get("frameFrom"),
                    frameTo=payload.get("frameTo"),
                    kinds=payload.get("kinds"),
                ),
            }
        if kind == "belief_plant":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            belief_count = len(agent.get("beliefs") or ())
            would_add = 1
            if payload.get("beliefId") and payload["beliefId"] in (agent.get("beliefs") or ()):
                would_add = 0
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "beliefId": payload.get("beliefId"),
                "beliefCount": belief_count,
                "wouldAddBelief": bool(would_add),
            }
        if kind == "context_mask":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "mode": payload["mode"],
                "durationFrames": payload["durationFrames"],
            }
        if kind == "decision_compulsion":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "mode": "compulsion",
                "pinnedAction": payload["pinnedDecision"].get("action"),
                "expiresFrame": (self.frameTick + payload["durationFrames"]
                                 if payload.get("durationFrames") else None),
                "remainingTurns": payload.get("remainingTurns"),
            }
        if kind == "decision_veto_arm":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "mode": "veto",
                "armed": True,
                "expiresFrame": self.frameTick + payload["durationFrames"],
            }
        if kind == "decision_veto_resolve":
            gate = self._god_active_decision_gate_record(payload["targetId"])
            pending = gate.get("pendingDecision") if isinstance(gate, dict) else None
            return {
                "targetId": payload["targetId"],
                "resolution": payload["resolution"],
                "pendingAction": (pending or {}).get("action"),
            }
        if kind == "agent_possession":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            pin = payload.get("pinnedDecision") or (payload.get("queue") or [{}])[0]
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "mode": "possession",
                "bypassLlm": True,
                "pinnedAction": pin.get("action"),
                "queueLength": len(payload.get("queue") or []),
                "expiresFrame": self.frameTick + payload["durationFrames"],
            }
        if kind == "revoke_decision_gate":
            agent = self._find_agent_by_id(payload["targetId"])
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"] if agent else None,
            }
        if kind == "identity_edit":
            agent = self._find_agent_by_id(payload["targetId"])
            if agent is None:
                return None
            outcome = {
                "targetId": payload["targetId"],
                "targetName": agent["name"],
                "currentRole": agent.get("role"),
                "fields": [k for k in ("persona", "personality", "role") if k in payload],
                "expiresFrame": (self.frameTick + payload["durationFrames"]
                                 if payload.get("durationFrames") else None),
            }
            warning = self._god_elder_swap_warning(agent, payload.get("role"))
            if warning:
                outcome["warning"] = warning
            if payload.get("role"):
                outcome["newRoleSkill"] = self.d["ROLE_SKILLS"].get(
                    payload["role"], "helps the village")
            return outcome
        if kind == "identity_copy_overwrite":
            target = self._find_agent_by_id(payload["targetId"])
            source = self._find_agent_by_id(payload["sourceId"])
            if target is None or source is None:
                return None
            thinks_to_complete = None
            rate = payload.get("ratePerThink")
            if isinstance(rate, (int, float)) and rate > 0:
                thinks_to_complete = int(math.ceil(1.0 / float(rate)))
            return {
                "targetId": payload["targetId"],
                "targetName": target["name"],
                "sourceId": payload["sourceId"],
                "sourceName": source["name"],
                "ratePerThink": rate,
                "thinksToComplete": thinks_to_complete,
                "syncMemories": bool(payload.get("syncMemories")),
                "expiresFrame": (self.frameTick + payload["durationFrames"]
                                 if payload.get("durationFrames") else None),
            }
        if kind == "identity_forge_cancel":
            agent = self._find_agent_by_id(payload["targetId"])
            rec = self._god_active_identity_forge_record(payload["targetId"])
            return {
                "targetId": payload["targetId"],
                "targetName": agent["name"] if agent else None,
                "forgeKind": ("copy" if isinstance(rec, dict) and rec.get("copyFromId")
                              else "edit"),
                "progress": (rec or {}).get("progress"),
            }
        if kind == "architect_zone":
            return {
                "zoneKind": payload["zoneKind"],
                "districtId": payload.get("districtId"),
                "cellCount": len(payload.get("cellsExpanded") or []),
                "expiresFrame": self.frameTick + payload["durationFrames"],
                "reversible": payload.get("reversible", True),
                "paintTerrain": payload.get("paintTerrain"),
                "keyId": payload.get("keyId"),
                "holdAgentIds": list(payload.get("holdAgentIds") or []),
                "grantKeyAgentIds": list(payload.get("grantKeyAgentIds") or []),
            }
        if kind == "architect_zone_cancel":
            zone = self._god_architect_zone_by_id(payload["zoneId"])
            return {
                "zoneId": payload["zoneId"],
                "zoneKind": zone.get("kind") if isinstance(zone, dict) else None,
                "cellCount": len((zone or {}).get("cells") or []),
            }
        if kind == "architect_release_hold":
            zone = self._god_architect_zone_by_id(payload["zoneId"])
            hold_ids = (zone or {}).get("holdAgentIds") or []
            if payload.get("agentIds"):
                hold_ids = [aid for aid in hold_ids if aid in payload["agentIds"]]
            return {
                "zoneId": payload["zoneId"],
                "wouldRelease": hold_ids,
            }
        if kind == "checkpoint_create":
            checkpoints = (self.civilization.get("godState") or {}).get("checkpoints") or []
            return {
                "label": payload["label"],
                "frameTick": self.frameTick,
                "checkpointCount": len(checkpoints),
                "willReplaceOldest": (
                    len(checkpoints) >= GOD_CHECKPOINT_MAX and payload.get("replaceOldest")
                ),
            }
        if kind == "checkpoint_restore":
            rec = self._god_checkpoint_by_id(payload["checkpointId"])
            return {
                "checkpointId": payload["checkpointId"],
                "label": rec.get("label") if isinstance(rec, dict) else None,
                "checkpointFrameTick": rec.get("frameTick") if isinstance(rec, dict) else None,
                "currentFrameTick": self.frameTick,
                "irreversibleWarning": (
                    "Irreversible world replace — the live world and godState "
                    "will be replaced by this checkpoint snapshot."
                ),
            }
        return None

    def _god_custom_rule_gather_context(self):
        """Compact summary of currently-enacted village custom rules that
        modify collect_resource -- shown alongside a proposed divine
        gather/fish multiplier in preview (docs/archive/plan-sovereign-god-mode-v2.md "Divine law vs. village
        law"). Mirrors the matching logic in _custom_rule_modifier without
        needing an agent/resource to evaluate against (a preview has
        neither)."""
        entries = []
        for rule_id, effect in (self.civilization.get("customRuleModifiers") or {}).items():
            if not isinstance(effect, dict):
                continue
            condition = effect.get("condition") or {}
            if condition.get("action", "collect_resource") != "collect_resource":
                continue
            subject = effect.get("subject") or {}
            subject_kind, subject_value = next(iter(subject.items()), (None, None))
            entries.append({
                "ruleId": rule_id,
                "subject": {subject_kind: subject_value} if subject_kind else {},
                "value": int((effect.get("modifier") or {}).get("value") or 0),
            })
        return entries

    def _god_reversibility_class(self, normalized_command):
        """Three classes per the docs/archive/plan-sovereign-god-mode-v2.md "Honest reversibility" table.
        providence/private_omen are cancellable (revocable before expiry,
        expire naturally otherwise). proclamation is a one-shot broadcast
        with no revocable state, and revoke_guidance is itself the "undo" --
        neither has anything left to cancel -- so both stay irreversible,
        like the vitals/resource/structure primitives Phase 4 added. A
        story_event with no immediate primitives is cancellable (only timed
        modifiers/providence, both revocable). One WITH primitives is
        consequential: cancelling it stops future effect (modifiers,
        providence) but cannot retract the primitives it already applied --
        those are irreversible mutations by their own nature (docs/archive/plan-sovereign-god-mode-v2.md
        "Honest reversibility" -- the third, consequential, class)."""
        kind = normalized_command["kind"] if isinstance(normalized_command, dict) else normalized_command
        if kind in ("providence", "private_omen", "whisper_campaign",
                    "crowd_compulsion", "dream_broadcast"):
            return "cancellable"
        if kind in ("agent_sampling", "revoke_agent_sampling"):
            return "cancellable"
        if kind == "context_mask":
            return "cancellable"
        if kind in ("decision_compulsion", "decision_veto_arm", "agent_possession",
                    "revoke_decision_gate"):
            return "cancellable"
        if kind in ("anoint", "revoke_anoint"):
            return "cancellable"
        if kind == "identity_forge_cancel":
            return "irreversible"
        if kind == "identity_copy_overwrite":
            return "cancellable"
        if kind == "identity_edit":
            payload = (normalized_command.get("payload") or {}
                       if isinstance(normalized_command, dict) else {})
            return ("cancellable" if payload.get("durationFrames") is not None
                    else "consequential")
        if kind in ("architect_zone", "architect_zone_cancel", "architect_release_hold"):
            return "cancellable"
        if kind in ("checkpoint_create", "checkpoint_restore"):
            return "irreversible"
        if kind == "deja_vu_replay":
            return "cancellable"
        if kind == "decision_veto_resolve":
            return "irreversible"
        if kind == "story_event":
            payload = normalized_command["payload"] if isinstance(normalized_command, dict) else {}
            return "consequential" if payload.get("primitives") else "cancellable"
        # docs/archive/plan-sovereign-god-mode-v2.md Phase 6 Answer 4: weather_override is consequential, not
        # merely cancellable -- entering "storm" can trigger real, permanent
        # structure damage (via the normal _maybe_disaster path) that
        # neither cancelling the override nor letting it expire retracts.
        if kind == "weather_override":
            return "consequential"
        return "irreversible"

    def _god_current_outgoing_guidance_id(self, kind, payload):
        """The id of the guidance record a providence/private_omen command
        would replace if applied right now, or None if the slot is empty.
        Shared by the preview-time fingerprint and the apply-time
        revalidation of it -- this IS the "disclose-then-replace" mechanism:
        preview freezes the outgoing id it saw; apply recomputes it fresh and
        rejects on mismatch (docs/archive/plan-sovereign-god-mode-v2.md "Replacing an existing one is allowed
        ONLY when the preview disclosed the replacement")."""
        god = self.civilization.get("godState") or {}
        if kind == "providence":
            prov = god.get("providence")
            return prov.get("id") if isinstance(prov, dict) else None
        if kind == "private_omen":
            omen = (god.get("privateOmens") or {}).get(str(payload.get("targetId")))
            return omen.get("id") if isinstance(omen, dict) else None
        return None

    def _god_current_revoke_target(self, guidance_id):
        """Where a revoke_guidance id currently points, or a not-found
        marker -- used the same way as _god_current_outgoing_guidance_id so
        a revoke preview can't apply against a target that already expired
        or was replaced in the meantime."""
        god = self.civilization.get("godState") or {}
        prov = god.get("providence")
        if isinstance(prov, dict) and prov.get("id") == guidance_id:
            return {"targetKind": "providence", "existed": True}
        for omen in (god.get("privateOmens") or {}).values():
            if isinstance(omen, dict) and omen.get("id") == guidance_id:
                return {"targetKind": "private_omen", "existed": True}
        return {"targetKind": None, "existed": False}

    def _god_target_fingerprint(self, normalized_command):
        """Precondition fingerprint bound into a preview record, revalidated
        at apply time. proclamation targets "everyone" and has no
        target-specific precondition. providence/private_omen record the
        outgoing guidance id they would replace; revoke_guidance records
        where its id currently points. Later phases
        (agent_vitals/grant_resource/structure_condition/story_event) will
        add target ids, current values, and district ids here too."""
        kind = normalized_command["kind"]
        if kind in ("providence", "private_omen"):
            return {"outgoingId": self._god_current_outgoing_guidance_id(
                kind, normalized_command["payload"])}
        if kind == "whisper_campaign":
            outgoing = {}
            for whisper in normalized_command["payload"]["whispers"]:
                tid = whisper["targetId"]
                omen = (self.civilization.get("godState") or {}).get("privateOmens", {}).get(str(tid))
                outgoing[str(tid)] = omen.get("id") if isinstance(omen, dict) else None
            return {"outgoingIds": outgoing}
        if kind == "crowd_compulsion":
            outgoing = {}
            gates = (self.civilization.get("godState") or {}).get("decisionGates") or {}
            for entry in normalized_command["payload"]["targets"]:
                tid = entry["targetId"]
                gate = gates.get(str(tid)) if isinstance(gates, dict) else None
                outgoing[str(tid)] = gate.get("id") if isinstance(gate, dict) else None
            return {"outgoingIds": outgoing}
        if kind == "dream_broadcast":
            outgoing = {}
            masks = (self.civilization.get("godState") or {}).get("contextMasks") or {}
            for tid in normalized_command["payload"]["targetIds"]:
                mask = masks.get(str(tid)) if isinstance(masks, dict) else None
                outgoing[str(tid)] = mask.get("id") if isinstance(mask, dict) else None
            return {"outgoingIds": outgoing}
        if kind == "revoke_guidance":
            return self._god_current_revoke_target(normalized_command["payload"]["id"])
        if kind == "context_mask":
            return {"outgoingId": self._god_current_outgoing_context_mask_id(
                normalized_command["payload"]["targetId"])}
        if kind in ("decision_compulsion", "decision_veto_arm", "agent_possession",
                    "revoke_decision_gate", "decision_veto_resolve"):
            return {"outgoingId": self._god_current_outgoing_decision_gate_id(
                normalized_command["payload"]["targetId"])}
        if kind == "anoint":
            return {"outgoingId": self._god_current_outgoing_anointment_id(
                normalized_command["payload"]["targetId"])}
        if kind in ("identity_edit", "identity_copy_overwrite"):
            return {"outgoingId": self._god_current_outgoing_identity_forge_id(
                normalized_command["payload"]["targetId"])}
        if kind == "deja_vu_replay":
            steps = normalized_command["payload"].get("replaySteps") or []
            last_frame = steps[-1].get("frameTick") if steps else None
            return {"stepCount": len(steps), "lastDigestFrame": last_frame}
        return {}

    def _god_check_fingerprint(self, normalized_command, fingerprint):
        """Returns a rejection reason string, or None if every recorded
        precondition still matches current state. frameTick drift alone is
        acceptable and never checked here."""
        kind = normalized_command["kind"]
        if kind in ("providence", "private_omen"):
            current = self._god_current_outgoing_guidance_id(
                kind, normalized_command["payload"])
            if current != fingerprint.get("outgoingId"):
                return f"{kind} changed since preview -- re-preview to see the current guidance"
            return None
        if kind == "whisper_campaign":
            current = {}
            for whisper in normalized_command["payload"]["whispers"]:
                tid = whisper["targetId"]
                omen = (self.civilization.get("godState") or {}).get("privateOmens", {}).get(str(tid))
                current[str(tid)] = omen.get("id") if isinstance(omen, dict) else None
            if current != fingerprint.get("outgoingIds"):
                return "whisper targets changed since preview -- re-preview to see current omens"
            return None
        if kind == "crowd_compulsion":
            current = {}
            gates = (self.civilization.get("godState") or {}).get("decisionGates") or {}
            for entry in normalized_command["payload"]["targets"]:
                tid = entry["targetId"]
                gate = gates.get(str(tid)) if isinstance(gates, dict) else None
                current[str(tid)] = gate.get("id") if isinstance(gate, dict) else None
            if current != fingerprint.get("outgoingIds"):
                return "crowd compulsion targets changed since preview -- re-preview to see current gates"
            return None
        if kind == "dream_broadcast":
            current = {}
            masks = (self.civilization.get("godState") or {}).get("contextMasks") or {}
            for tid in normalized_command["payload"]["targetIds"]:
                mask = masks.get(str(tid)) if isinstance(masks, dict) else None
                current[str(tid)] = mask.get("id") if isinstance(mask, dict) else None
            if current != fingerprint.get("outgoingIds"):
                return "dream broadcast targets changed since preview -- re-preview to see current masks"
            return None
        if kind == "revoke_guidance":
            current = self._god_current_revoke_target(normalized_command["payload"]["id"])
            if current != fingerprint:
                return "guidance target changed since preview -- re-preview to see the current state"
            return None
        if kind == "context_mask":
            current = self._god_current_outgoing_context_mask_id(
                normalized_command["payload"]["targetId"])
            if current != fingerprint.get("outgoingId"):
                return "context mask changed since preview -- re-preview to see the current mask"
            return None
        if kind in ("decision_compulsion", "decision_veto_arm", "agent_possession",
                    "revoke_decision_gate", "decision_veto_resolve"):
            current = self._god_current_outgoing_decision_gate_id(
                normalized_command["payload"]["targetId"])
            if current != fingerprint.get("outgoingId"):
                return "decision gate changed since preview -- re-preview to see the current gate"
            return None
        if kind == "anoint":
            current = self._god_current_outgoing_anointment_id(
                normalized_command["payload"]["targetId"])
            if current != fingerprint.get("outgoingId"):
                return "anointment changed since preview -- re-preview to see the current record"
            return None
        if kind in ("identity_edit", "identity_copy_overwrite"):
            current = self._god_current_outgoing_identity_forge_id(
                normalized_command["payload"]["targetId"])
            if current != fingerprint.get("outgoingId"):
                return "identity forge changed since preview -- re-preview to see the current record"
            return None
        if kind == "deja_vu_replay":
            payload = normalized_command["payload"]
            target_id = payload.get("targetId")
            if self._god_active_decision_gate_record(target_id) is not None:
                return "agent already has an active decision gate"
            replays = (self.civilization.get("godState") or {}).get("dejaVuReplays") or {}
            if isinstance(replays, dict):
                for rec in replays.values():
                    if (isinstance(rec, dict) and rec.get("status") == "active"
                            and rec.get("targetId") == target_id):
                        return "agent already has an active deja vu replay"
            if self._god_deja_vu_session_total >= GOD_DEJA_VU_SESSION_CAP:
                return f"deja vu replay session cap ({GOD_DEJA_VU_SESSION_CAP}) reached"
            steps = payload.get("replaySteps") or []
            last_frame = steps[-1].get("frameTick") if steps else None
            current = {"stepCount": len(steps), "lastDigestFrame": last_frame}
            if current != fingerprint:
                return "decision digests changed since preview -- re-preview to refresh steps"
            return None
        return None

