"""Phase 6h mixin: Sovereign God mode broadcast/proclamation handlers slice
of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `god_preview` through
`_god_apply_dream_broadcast` (formerly core.py lines ~560-1007). Covers the
HTTP-facing preview entry point (`god_preview`), the shared intervention-id/
recentInterventions bookkeeping (`_next_intervention_id`,
`_god_record_intervention`), the per-kind apply dispatcher
(`_god_apply_command`), and the proclamation/providence/private-omen/
whisper-campaign/crowd-compulsion/dream-broadcast apply handlers
(`_god_apply_proclamation`, `_god_apply_providence`, `_god_apply_private_omen`,
`_god_apply_whisper_campaign`, `_god_apply_crowd_compulsion_gate`,
`_god_apply_crowd_compulsion`, `_god_apply_dream_broadcast_mask`,
`_god_apply_dream_broadcast`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodBroadcastMixin:
    """Mixin slice of SimEngine: Sovereign God mode preview entry point,
    intervention-id/recording bookkeeping, the per-kind apply dispatcher, and
    the proclamation/providence/private-omen/whisper-campaign/crowd-compulsion/
    dream-broadcast apply handlers. See module docstring for the exact method
    range and rationale."""

    # --- entry points (each acquires self.lock itself; Flask request
    # threads call these from outside the tick thread) ---
    def god_preview(self, envelope):
        """Validate + normalize WITHOUT mutation. Returns a preview record
        (opaque previewId, canonical digest, TTL, reversibility class)."""
        with self.lock:
            if not GOD_MODE_ENABLED:
                return {"ok": False, "reason": "god mode disabled"}
            normalized, reason = self._validate_god_envelope(envelope)
            if reason:
                self._god_rejected_count += 1
                return {"ok": False, "reason": reason}
            digest = self._god_command_digest(normalized)
            preview_id = secrets.token_urlsafe(24)
            now = time.time()
            record = {
                "previewId": preview_id,
                "normalizedCommand": normalized,
                "commandDigest": digest,
                "previewFrame": self.frameTick,
                "createdAt": now,
                "expiresAt": now + GOD_PREVIEW_TTL_SECONDS,
                "fingerprint": self._god_target_fingerprint(normalized),
            }
            self._god_preview_insert(record)
            response = {
                "ok": True,
                "previewId": preview_id,
                "commandDigest": digest,
                "previewFrame": self.frameTick,
                "expiresAt": record["expiresAt"],
                "normalizedCommand": normalized,
                "reversibilityClass": self._god_reversibility_class(normalized),
                # Sovereign God mode (Phase 3): "include the outgoing
                # providence in the preview response" -- the same fingerprint
                # bound into the cached preview record is echoed back here so
                # a client can show the disclosed replacement BEFORE the
                # operator commits to apply. Empty dict for kinds with no
                # precondition (proclamation).
                "fingerprint": record["fingerprint"],
                # Sovereign God mode (Phase 4): the exact clamped/bounded
                # value an immediate miracle would apply right now, or None
                # for every kind with no derived value (Phase 2/3 kinds).
                "previewOutcome": self._god_preview_outcome(normalized),
            }
            if normalized.get("kind") == "story_event":
                payload = normalized.get("payload") or {}
                warnings = _god_modifier_conflict_warnings(payload.get("modifiers") or {})
                if warnings:
                    response["warnings"] = warnings
            return response

    def _next_intervention_id(self):
        """Shared per-world intervention id sequence + the monotonic
        `intervened` marker -- every successful apply (proclamation,
        providence, private_omen, revoke_guidance, and later phases) goes
        through this one counter. Must be called with self.lock already
        held, and only after every rejection path has already returned (a
        failed apply must never consume a sequence number)."""
        god = self.civilization["godState"]
        seq = god.get("nextInterventionSeq", 1)
        intervention_id = f"divine-{seq}"
        god["nextInterventionSeq"] = seq + 1
        god["intervened"] = True
        return intervention_id

    def _god_record_intervention(self, record):
        """Append one outcome record to the bounded recentInterventions ring.
        `record["public"]` MUST be set by the caller -- snapshot()'s
        recentPublicInterventions filters on it, and it is the ONLY thing
        standing between a private_omen record and a public /state leak."""
        god = self.civilization["godState"]
        recent = god["recentInterventions"]
        recent.append(record)
        if len(recent) > GOD_RECENT_INTERVENTIONS_CAP:
            del recent[:-GOD_RECENT_INTERVENTIONS_CAP]

    def _god_apply_command(self, normalized):
        """Dispatch by kind. Returns (outcome, reason); outcome is None on
        rejection. Must be called with self.lock already held."""
        kind = normalized["kind"]
        if kind == "proclamation":
            return self._god_apply_proclamation(normalized["payload"]), None
        if kind == "providence":
            return self._god_apply_providence(normalized["payload"]), None
        if kind == "private_omen":
            return self._god_apply_private_omen(normalized["payload"]), None
        if kind == "whisper_campaign":
            return self._god_apply_whisper_campaign(normalized["payload"]), None
        if kind == "crowd_compulsion":
            return self._god_apply_crowd_compulsion(normalized["payload"]), None
        if kind == "dream_broadcast":
            return self._god_apply_dream_broadcast(normalized["payload"]), None
        if kind == "agent_sampling":
            return self._god_apply_agent_sampling(normalized["payload"]), None
        if kind == "revoke_agent_sampling":
            return self._god_apply_revoke_agent_sampling(normalized["payload"])
        if kind == "memory_insert":
            return self._god_apply_memory_insert(normalized["payload"]), None
        if kind == "memory_delete":
            return self._god_apply_memory_delete(normalized["payload"]), None
        if kind == "belief_plant":
            return self._god_apply_belief_plant(normalized["payload"]), None
        if kind == "context_mask":
            return self._god_apply_context_mask(normalized["payload"]), None
        if kind == "decision_compulsion":
            return self._god_apply_decision_compulsion(normalized["payload"]), None
        if kind == "decision_veto_arm":
            return self._god_apply_decision_veto_arm(normalized["payload"]), None
        if kind == "decision_veto_resolve":
            return self._god_apply_decision_veto_resolve(normalized["payload"])
        if kind == "agent_possession":
            return self._god_apply_agent_possession(normalized["payload"]), None
        if kind == "revoke_decision_gate":
            return self._god_apply_revoke_decision_gate(normalized["payload"])
        if kind == "burning_bush_message":
            return self._god_apply_burning_bush_message(normalized["payload"]), None
        if kind == "burning_bush_close":
            return self._god_apply_burning_bush_close(normalized["payload"]), None
        if kind == "merovingian_bargain":
            return self._god_apply_merovingian_bargain(normalized["payload"]), None
        if kind == "bargain_settle":
            return self._god_apply_bargain_settle(normalized["payload"])
        if kind == "anoint":
            return self._god_apply_anoint(normalized["payload"]), None
        if kind == "revoke_anoint":
            return self._god_apply_revoke_anoint(normalized["payload"])
        if kind == "identity_edit":
            return self._god_apply_identity_edit(normalized["payload"]), None
        if kind == "identity_copy_overwrite":
            return self._god_apply_identity_copy_overwrite(normalized["payload"]), None
        if kind == "identity_forge_cancel":
            return self._god_apply_identity_forge_cancel(normalized["payload"])
        if kind == "architect_zone":
            return self._god_apply_architect_zone(normalized["payload"]), None
        if kind == "architect_zone_cancel":
            return self._god_apply_architect_zone_cancel(normalized["payload"])
        if kind == "architect_release_hold":
            return self._god_apply_architect_release_hold(normalized["payload"])
        if kind == "checkpoint_create":
            return self._god_apply_checkpoint_create(normalized["payload"])
        if kind == "checkpoint_restore":
            return self._god_apply_checkpoint_restore(normalized["payload"])
        if kind == "deja_vu_replay":
            return self._god_apply_deja_vu_replay(normalized["payload"]), None
        if kind == "revoke_guidance":
            return self._god_apply_revoke_guidance(normalized["payload"]["id"])
        if kind == "agent_vitals":
            return self._god_apply_agent_vitals(normalized["payload"]), None
        if kind == "grant_resource":
            return self._god_apply_grant_resource(normalized["payload"]), None
        if kind == "structure_condition":
            return self._god_apply_structure_condition(normalized["payload"]), None
        if kind == "repair_structures":
            return self._god_apply_repair_structures(normalized["payload"]), None
        if kind == "clear_ruins":
            return self._god_apply_clear_ruins(normalized["payload"]), None
        if kind == "story_event":
            return self._god_apply_story_event(normalized["payload"]), None
        if kind == "weather_override":
            return self._god_apply_weather_override(normalized["payload"]), None
        if kind == "wildlife_spawn":
            return self._god_apply_wildlife_spawn(normalized["payload"])
        if kind == "wildlife_despawn":
            return self._god_apply_wildlife_despawn(normalized["payload"])
        if kind == "wildlife_set_hp":
            return self._god_apply_wildlife_set_hp(normalized["payload"])
        return None, f"kind '{kind}' is not implemented in this phase"

    def _god_apply_proclamation(self, payload):
        text = payload["text"]
        presentation = payload.get("presentation", "soft")
        intervention_id = self._next_intervention_id()
        self._push_activity(f'A divine voice proclaims: "{text}"')
        self._push_communication("divine_proclamation", "divine", "everyone", text,
                                 source="divine")
        self._push_chronicle(text, kind="divine", source="divine",
                             presentation=presentation)
        proc_record = {
            "id": intervention_id, "kind": "proclamation",
            "frameTick": self.frameTick, "text": text, "status": "applied",
            "public": True,
        }
        if presentation == "thunder":
            proc_record["presentation"] = "thunder"
        self._god_record_intervention(proc_record)
        prov_payload = {
            "text": text,
            "durationFrames": GOD_GUIDANCE_DEFAULT_DURATION_FRAMES,
        }
        if presentation == "thunder":
            prov_payload["presentation"] = "thunder"
        self._god_apply_providence(prov_payload)
        living_ids = [
            a["id"] for a in self.agents if a.get("deathFrame") is None
        ]
        self._cancel_voice_blocked_special_turns(living_ids)
        outcome = {"interventionId": intervention_id, "kind": "proclamation", "text": text}
        if presentation == "thunder":
            outcome["presentation"] = "thunder"
        return outcome

    def _god_apply_providence(self, payload):
        """Sets the single active public providence line, replacing any
        prior one. The outgoing record (if any) is closed through the SAME
        _close_providence path expiry/revocation use, so it is logged
        exactly once regardless of how it ends. Public per docs/archive/plan-sovereign-god-mode-v2.md
        Visibility: activity/communication/Chronicle, same treatment as
        proclamation."""
        god = self.civilization["godState"]
        outgoing = self._close_providence("replaced") if isinstance(god.get("providence"), dict) else None
        intervention_id = self._next_intervention_id()
        text = payload["text"]
        presentation = payload.get("presentation", "soft")
        expires_frame = self.frameTick + payload["durationFrames"]
        prov_record = {
            "id": intervention_id, "text": text, "createdFrame": self.frameTick,
            "expiresFrame": expires_frame, "visibility": "public",
            "ackedAgentIds": {},
        }
        if presentation == "thunder":
            prov_record["presentation"] = "thunder"
        god["providence"] = prov_record
        self._push_activity(f'A divine providence settles over the village: "{text}"')
        self._push_communication("divine_providence", "divine", "everyone", text,
                                 source="divine")
        self._push_chronicle(text, kind="divine", source="divine",
                             presentation=presentation)
        int_record = {
            "id": intervention_id, "kind": "providence", "frameTick": self.frameTick,
            "text": text, "expiresFrame": expires_frame, "status": "applied",
            "public": True,
        }
        if presentation == "thunder":
            int_record["presentation"] = "thunder"
        self._god_record_intervention(int_record)
        living_ids = [
            a["id"] for a in self.agents if a.get("deathFrame") is None
        ]
        self._cancel_voice_blocked_special_turns(living_ids)
        outcome = {
            "interventionId": intervention_id, "kind": "providence", "text": text,
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }
        if presentation == "thunder":
            outcome["presentation"] = "thunder"
        return outcome

    def _god_apply_private_omen(self, payload):
        """Sets the one active private omen for a single living agent,
        replacing any prior one for that same agent. Never touches public
        activity/communication/Chronicle (docs/archive/plan-sovereign-god-mode-v2.md Visibility: private
        omens must never appear there). The outgoing record (if any) is
        closed through the SAME _close_omen path expiry/revocation use, so
        its memory write (if not already written) fires exactly once here,
        not again later."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        key = str(target_id)
        outgoing = self._close_omen(key, "replaced") if key in god["privateOmens"] else None
        intervention_id = self._next_intervention_id()
        agent = self._find_agent_by_id(target_id)
        text = payload["text"]
        expires_frame = self.frameTick + payload["durationFrames"]
        god["privateOmens"][key] = {
            "id": intervention_id, "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "text": text, "createdFrame": self.frameTick,
            "expiresFrame": expires_frame, "memoryWritten": False,
            "acked": False,
        }
        self._god_record_intervention({
            "id": intervention_id, "kind": "private_omen", "frameTick": self.frameTick,
            "targetId": target_id, "text": text, "expiresFrame": expires_frame,
            "status": "applied", "public": False,
        })
        self._cancel_voice_blocked_special_turns([target_id])
        return {"interventionId": intervention_id, "kind": "private_omen",
                "targetId": target_id, "expiresFrame": expires_frame,
                "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None}

    def _god_apply_whisper_campaign(self, payload):
        """Batch private omens under one campaign id. Each whisper reuses the
        private_omen machinery (one omen per agent, replace semantics unchanged).
        Campaign theme and per-target texts never appear in snapshot() god
        allowlist; only per-agent omen text reaches _divine_prompt_lines."""
        god = self.civilization["godState"]
        campaign_id = self._next_intervention_id()
        duration = payload["durationFrames"]
        expires_frame = self.frameTick + duration
        targets = {}
        for whisper in payload["whispers"]:
            outcome = self._god_apply_private_omen({
                "targetId": whisper["targetId"],
                "text": whisper["text"],
                "durationFrames": duration,
            })
            targets[str(whisper["targetId"])] = outcome["interventionId"]
        god["whisperCampaigns"][campaign_id] = {
            "id": campaign_id,
            "theme": payload["theme"],
            "targets": targets,
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "status": "active",
        }
        self._god_record_intervention({
            "id": campaign_id, "kind": "whisper_campaign", "frameTick": self.frameTick,
            "theme": payload["theme"], "targetIds": [w["targetId"] for w in payload["whispers"]],
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        return {
            "interventionId": campaign_id,
            "kind": "whisper_campaign",
            "theme": payload["theme"],
            "targets": targets,
            "expiresFrame": expires_frame,
        }

    def _god_apply_crowd_compulsion_gate(self, target_id, pinned_decision,
                                         duration_frames, remaining_turns, crowd_id):
        """One compulsion gate for a crowd_compulsion batch target."""
        intervention_id = self._next_intervention_id()
        expires_frame = None
        if duration_frames is not None:
            expires_frame = self.frameTick + duration_frames
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "compulsion",
            "pinnedDecision": copy.deepcopy(pinned_decision),
            "createdFrame": self.frameTick,
            "crowdCompulsionId": crowd_id,
        }
        if expires_frame is not None:
            record["expiresFrame"] = expires_frame
        if remaining_turns is not None:
            record["remainingTurns"] = remaining_turns
        return self._god_set_decision_gate(target_id, record, "decision_compulsion")

    def _god_apply_crowd_compulsion(self, payload):
        """Batch decision compulsion gates under one parent id."""
        god = self.civilization["godState"]
        compulsion_id = self._next_intervention_id()
        duration = payload.get("durationFrames")
        remaining = payload.get("remainingTurns")
        expires_frame = (self.frameTick + duration) if duration is not None else None
        targets = {}
        for entry in payload["targets"]:
            outcome = self._god_apply_crowd_compulsion_gate(
                entry["targetId"], entry["pinnedDecision"],
                duration, remaining, compulsion_id)
            targets[str(entry["targetId"])] = outcome["interventionId"]
        parent = {
            "id": compulsion_id,
            "theme": payload.get("theme"),
            "targets": targets,
            "createdFrame": self.frameTick,
            "status": "active",
        }
        if expires_frame is not None:
            parent["expiresFrame"] = expires_frame
        if remaining is not None:
            parent["remainingTurns"] = remaining
        god.setdefault("crowdCompulsions", {})[compulsion_id] = parent
        target_ids = [t["targetId"] for t in payload["targets"]]
        self._god_record_intervention({
            "id": compulsion_id, "kind": "crowd_compulsion", "frameTick": self.frameTick,
            "theme": payload.get("theme"), "targetIds": target_ids,
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        return {
            "interventionId": compulsion_id,
            "kind": "crowd_compulsion",
            "theme": payload.get("theme"),
            "targets": targets,
            "expiresFrame": expires_frame,
        }

    def _god_apply_dream_broadcast_mask(self, target_id, dream_snapshot,
                                        duration_frames, broadcast_id):
        """One dream context mask for a dream_broadcast batch target."""
        god = self.civilization["godState"]
        key = str(target_id)
        outgoing = (self._close_context_mask(key, "replaced")
                    if key in god.get("contextMasks", {}) else None)
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + duration_frames
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "dream",
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "dreamSnapshot": copy.deepcopy(dream_snapshot),
            "dreamBroadcastId": broadcast_id,
        }
        god.setdefault("contextMasks", {})[key] = record
        agent = self._find_agent_by_id(target_id)
        self._god_record_intervention({
            "id": intervention_id, "kind": "context_mask", "frameTick": self.frameTick,
            "targetId": target_id, "mode": "dream",
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "context_mask",
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "mode": "dream",
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_dream_broadcast(self, payload):
        """Batch dream context masks under one parent id."""
        god = self.civilization["godState"]
        broadcast_id = self._next_intervention_id()
        duration = payload["durationFrames"]
        expires_frame = self.frameTick + duration
        snapshot = payload["dreamSnapshot"]
        targets = {}
        for target_id in payload["targetIds"]:
            outcome = self._god_apply_dream_broadcast_mask(
                target_id, snapshot, duration, broadcast_id)
            targets[str(target_id)] = outcome["interventionId"]
        god.setdefault("dreamBroadcasts", {})[broadcast_id] = {
            "id": broadcast_id,
            "targets": targets,
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "status": "active",
        }
        self._god_record_intervention({
            "id": broadcast_id, "kind": "dream_broadcast", "frameTick": self.frameTick,
            "targetIds": list(payload["targetIds"]),
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        self._log_divine(broadcast_id, None, "dream_broadcast",
                         {"targetCount": len(payload["targetIds"])},
                         {"targetCount": len(payload["targetIds"])},
                         "applied", public=False)
        return {
            "interventionId": broadcast_id,
            "kind": "dream_broadcast",
            "targets": targets,
            "expiresFrame": expires_frame,
        }

