"""Phase 6h mixin: Sovereign God mode decision-gate / possession / veto /
sweep machinery slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_god_apply_agent_sampling`
through `_god_apply_revoke_guidance` (formerly core.py lines ~1378-2163).
Covers the per-agent LLM sampling override apply/revoke handlers
(`_god_apply_agent_sampling`, `_god_apply_revoke_agent_sampling`), memory/
belief plant apply handlers (`_god_apply_memory_insert`,
`_god_apply_memory_delete`, `_god_apply_belief_plant`), the context-mask
apply handler (`_god_apply_context_mask` — the APPLY/entry-point version;
distinct from `_apply_context_mask`, the Phase 6f validation helper in
mixin_divine_sampling.py), and the full decision-gate/possession/veto
machinery: gate creation (`_god_set_decision_gate`), the decision-compulsion/
veto-arm/veto-resolve/possession/revoke apply handlers
(`_god_apply_decision_compulsion`, `_god_apply_decision_veto_arm`,
`_god_apply_decision_veto_resolve`, `_god_apply_agent_possession`,
`_god_apply_revoke_decision_gate`), gate-record lookups/closure
(`_close_agent_sampling`, `_close_context_mask`,
`_god_active_decision_gate_record`, `_god_current_outgoing_decision_gate_id`,
`_god_veto_hold_count`, `_god_decision_gate_pinned`,
`_god_agent_data_for_decision_norm`, `_god_normalize_pinned_decision`,
`_close_decision_gate`), the per-tick gate-turn advancement applied at
decision time (`_advance_possession_queue`, `_dec_compulsion_turn`,
`_apply_divine_possessed_decision`, `_apply_gated_decision`,
`_expire_decision_gates`), the whisper-campaign/crowd-compulsion/
dream-broadcast closure and per-tick sweep helpers
(`_close_whisper_campaign`, `_close_crowd_compulsion`,
`_maybe_finalize_crowd_compulsion`, `_sweep_crowd_compulsions`,
`_close_dream_broadcast`, `_maybe_finalize_dream_broadcast`,
`_sweep_dream_broadcasts`, `_maybe_finalize_whisper_campaign`,
`_sweep_whisper_campaigns`), and the guidance-revocation apply handler
(`_god_apply_revoke_guidance`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodGateMixin:
    """Mixin slice of SimEngine: Sovereign God mode agent-sampling/memory/
    belief-plant/context-mask apply handlers, and the full decision-gate/
    possession/veto/sweep machinery. See module docstring for the exact
    method range and rationale."""

    def _god_apply_agent_sampling(self, payload):
        """Set one active LLM sampling override for a living agent. Private —
        never in snapshot() god allowlist."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        key = str(target_id)
        outgoing = (self._close_agent_sampling(key, "replaced")
                    if key in god.get("agentSampling", {}) else None)
        intervention_id = self._next_intervention_id()
        expires_frame = None
        if "durationFrames" in payload:
            expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "model": payload["model"],
            "temperature": payload["temperature"],
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "sourceId": intervention_id,
        }
        for opt in ("top_p", "top_k", "min_p"):
            if opt in payload:
                record[opt] = payload[opt]
        god.setdefault("agentSampling", {})[key] = record
        self._god_record_intervention({
            "id": intervention_id, "kind": "agent_sampling", "frameTick": self.frameTick,
            "targetId": target_id, "model": payload["model"],
            "temperature": payload["temperature"],
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "agent_sampling",
            "targetId": target_id,
            "model": payload["model"],
            "temperature": payload["temperature"],
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_revoke_agent_sampling(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        closed = self._close_agent_sampling(key, "revoked")
        if closed is None:
            return None, "no active sampling override for target agent"
        return {
            "interventionId": closed.get("id"),
            "kind": "revoke_agent_sampling",
            "targetId": target_id,
        }, None

    def _god_apply_memory_insert(self, payload):
        agent = self._find_agent_by_id(payload["targetId"])
        entry = self._god_memory_insert(
            agent, payload["text"], payload["salience"], payload["kind"])
        intervention_id = self._next_intervention_id()
        self._god_record_intervention({
            "id": intervention_id, "kind": "memory_insert",
            "frameTick": self.frameTick, "targetId": payload["targetId"],
            "salience": payload["salience"], "memoryKind": payload["kind"],
            "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "memory_insert",
            "targetId": payload["targetId"],
            "targetName": agent["name"] if agent else None,
            "memoryEntryId": entry.get("id") if isinstance(entry, dict) else None,
            "salience": payload["salience"],
            "memoryKind": payload["kind"],
        }

    def _god_apply_memory_delete(self, payload):
        agent = self._find_agent_by_id(payload["targetId"])
        deleted = self._god_memory_delete(
            agent,
            keyword=payload.get("keyword"),
            frameFrom=payload.get("frameFrom"),
            frameTo=payload.get("frameTo"),
            kinds=payload.get("kinds"),
        )
        intervention_id = self._next_intervention_id()
        self._god_record_intervention({
            "id": intervention_id, "kind": "memory_delete",
            "frameTick": self.frameTick, "targetId": payload["targetId"],
            "deletedCount": deleted, "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "memory_delete",
            "targetId": payload["targetId"],
            "targetName": agent["name"] if agent else None,
            "deletedCount": deleted,
        }

    def _god_apply_belief_plant(self, payload):
        agent = self._find_agent_by_id(payload["targetId"])
        belief_id, tenet_or_reason = self._god_belief_plant(
            agent,
            belief_id=payload.get("beliefId"),
            custom_text=payload.get("text"),
            salience=payload["salience"],
            plant_in_meme_texts=payload["plantInMemeTexts"],
        )
        if belief_id is None:
            return None, tenet_or_reason
        intervention_id = self._next_intervention_id()
        self._god_record_intervention({
            "id": intervention_id, "kind": "belief_plant",
            "frameTick": self.frameTick, "targetId": payload["targetId"],
            "beliefId": belief_id, "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "belief_plant",
            "targetId": payload["targetId"],
            "targetName": agent["name"] if agent else None,
            "beliefId": belief_id,
            "beliefCount": len(agent.get("beliefs") or ()),
        }

    def _god_apply_context_mask(self, payload):
        """Set one active context mask for a living agent (replace semantics)."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        key = str(target_id)
        outgoing = (self._close_context_mask(key, "replaced")
                    if key in god.get("contextMasks", {}) else None)
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": payload["mode"],
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        if payload["mode"] == "dream":
            record["dreamSnapshot"] = copy.deepcopy(payload["dreamSnapshot"])
        elif payload["mode"] == "whisper_chain":
            record["forgedConversations"] = list(payload["forgedConversations"])
        god.setdefault("contextMasks", {})[key] = record
        agent = self._find_agent_by_id(target_id)
        self._god_record_intervention({
            "id": intervention_id, "kind": "context_mask", "frameTick": self.frameTick,
            "targetId": target_id, "mode": payload["mode"],
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "context_mask",
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "mode": payload["mode"],
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_set_decision_gate(self, target_id, record, kind_label):
        god = self.civilization["godState"]
        key = str(target_id)
        outgoing = (self._close_decision_gate(key, "replaced")
                    if key in god.get("decisionGates", {}) else None)
        god.setdefault("decisionGates", {})[key] = record
        agent = self._find_agent_by_id(target_id)
        if agent is not None:
            agent["divineHold"] = False
        intervention_id = record["id"]
        self._god_record_intervention({
            "id": intervention_id, "kind": kind_label, "frameTick": self.frameTick,
            "targetId": target_id, "mode": record.get("mode"),
            "expiresFrame": record.get("expiresFrame"),
            "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": kind_label,
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "mode": record.get("mode"),
            "expiresFrame": record.get("expiresFrame"),
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_decision_compulsion(self, payload):
        target_id = payload["targetId"]
        intervention_id = self._next_intervention_id()
        expires_frame = None
        if payload.get("durationFrames") is not None:
            expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "compulsion",
            "pinnedDecision": copy.deepcopy(payload["pinnedDecision"]),
            "createdFrame": self.frameTick,
        }
        if expires_frame is not None:
            record["expiresFrame"] = expires_frame
        if payload.get("remainingTurns") is not None:
            record["remainingTurns"] = payload["remainingTurns"]
        return self._god_set_decision_gate(target_id, record, "decision_compulsion")

    def _god_apply_decision_veto_arm(self, payload):
        target_id = payload["targetId"]
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "veto",
            "armed": True,
            "status": "armed",
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        return self._god_set_decision_gate(target_id, record, "decision_veto_arm")

    def _god_apply_decision_veto_resolve(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        gate = (self.civilization.get("godState") or {}).get("decisionGates", {}).get(key)
        if not isinstance(gate, dict):
            return None, "no active decision gate for this agent"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        pending = gate.get("pendingDecision") or {}
        resolution = payload["resolution"]
        if resolution == "approve":
            decision = dict(pending)
        elif resolution == "reject":
            decision = {"action": "rest", "reasoning": "Divine veto rejected."}
        else:
            decision = dict(payload["rewrittenDecision"])
        agent["divineHold"] = False
        self._apply_divine_possessed_decision(agent, decision, gate, gate_kind="veto_resolve")
        outgoing_id = gate.get("id")
        self._close_decision_gate(key, "resolved")
        intervention_id = self._next_intervention_id()
        self._god_record_intervention({
            "id": intervention_id, "kind": "decision_veto_resolve",
            "frameTick": self.frameTick, "targetId": target_id,
            "resolution": resolution, "appliedAction": decision.get("action"),
            "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "decision_veto_resolve",
            "targetId": target_id,
            "targetName": agent["name"],
            "resolution": resolution,
            "appliedAction": decision.get("action"),
            "gateId": outgoing_id,
        }, None

    def _god_apply_agent_possession(self, payload):
        target_id = payload["targetId"]
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "possession",
            "bypassLlm": True,
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "queueIndex": 0,
        }
        if payload.get("queue"):
            record["queue"] = copy.deepcopy(payload["queue"])
        else:
            record["pinnedDecision"] = copy.deepcopy(payload["pinnedDecision"])
        return self._god_set_decision_gate(target_id, record, "agent_possession")

    def _god_apply_revoke_decision_gate(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        outgoing = self._close_decision_gate(key, "revoked")
        if outgoing is None:
            return None, "no active decision gate for this agent"
        intervention_id = self._next_intervention_id()
        agent = self._find_agent_by_id(target_id)
        self._god_record_intervention({
            "id": intervention_id, "kind": "revoke_decision_gate",
            "frameTick": self.frameTick, "targetId": target_id,
            "revokedGateId": outgoing.get("id"), "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "revoke_decision_gate",
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "revokedGateId": outgoing.get("id"),
        }, None

    def _close_agent_sampling(self, key, status):
        """Remove one agentSampling entry exactly once. Must be called with
        self.lock already held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        sampling = god.get("agentSampling")
        if not isinstance(sampling, dict):
            return None
        rec = sampling.pop(key, None)
        if not isinstance(rec, dict):
            return None
        self._log_divine(rec.get("id"), None, "agent_sampling", rec,
                         {"status": status}, status, public=False)
        return rec

    def _close_context_mask(self, key, status):
        """Remove one contextMasks entry exactly once. Must be called with
        self.lock already held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        masks = god.get("contextMasks")
        if not isinstance(masks, dict):
            return None
        rec = masks.pop(key, None)
        if not isinstance(rec, dict):
            return None
        self._log_divine(rec.get("id"), None, "context_mask", rec,
                         {"status": status}, status, public=False)
        broadcast_id = rec.get("dreamBroadcastId")
        if broadcast_id:
            self._maybe_finalize_dream_broadcast(broadcast_id)
        return rec

    def _god_active_decision_gate_record(self, agent_id):
        """Active decision gate for agent_id, or None if absent/expired."""
        god = self.civilization.get("godState") or {}
        rec = (god.get("decisionGates") or {}).get(str(agent_id))
        if not isinstance(rec, dict):
            return None
        expires = rec.get("expiresFrame")
        if isinstance(expires, int) and self.frameTick >= expires:
            return None
        agent = self._find_agent_by_id(agent_id)
        if agent is None or agent.get("deathFrame") is not None:
            return None
        return rec

    def _god_current_outgoing_decision_gate_id(self, target_id):
        rec = (self.civilization.get("godState") or {}).get("decisionGates", {}).get(str(target_id))
        return rec.get("id") if isinstance(rec, dict) else None

    def _god_veto_hold_count(self):
        gates = (self.civilization.get("godState") or {}).get("decisionGates") or {}
        count = 0
        for rec in gates.values():
            if isinstance(rec, dict) and rec.get("mode") == "veto" and rec.get("status") == "holding":
                count += 1
        return count

    def _god_decision_gate_pinned(self, agent, gate):
        """Pinned decision for compulsion/possession (queue advances for possession)."""
        queue = gate.get("queue")
        if isinstance(queue, list) and queue:
            idx = int(gate.get("queueIndex") or 0)
            if idx >= len(queue):
                return dict(queue[-1])
            return dict(queue[idx])
        pinned = gate.get("pinnedDecision")
        if isinstance(pinned, dict):
            return dict(pinned)
        return {"action": "rest", "reasoning": "Divine gate default."}

    def _god_agent_data_for_decision_norm(self, agent):
        """Think-payload slice for normalize_decision during god preview/apply."""
        payload = self._build_think_payload(agent)
        build_fn = self.d.get("build_agent_data")
        if build_fn is None:
            return payload
        return build_fn(
            payload,
            payload.get("nearby_agents_formatted") or payload.get("nearby_agents"),
            payload.get("known_resources") or [],
            payload.get("pending_blueprints") or [],
            payload.get("approved_custom_projects") or [],
            payload.get("rejected_blueprints") or [],
        )

    def _god_normalize_pinned_decision(self, agent, raw):
        if not isinstance(raw, dict):
            return None, "pinnedDecision must be an object"
        action = raw.get("action")
        if not isinstance(action, str) or not action.strip():
            return None, "pinnedDecision.action is required"
        norm_fn = self.d.get("normalize_decision")
        if norm_fn is not None:
            agent_data = self._god_agent_data_for_decision_norm(agent)
            normalized = norm_fn(dict(raw), agent_data)
            requested = action.strip()
            if normalized.get("action") != requested:
                note = (
                    normalized.get("rejection_note")
                    or normalized.get("council_rejection_note")
                    or normalized.get("terraform_rejection_note")
                    or normalized.get("upgrade_rejection_note")
                    or normalized.get("sprite_rejection_note")
                    or "normalize_decision substituted a fallback"
                )
                return None, f"invalid pinned decision: {note}"
            return normalized, None
        if action.strip() not in self.d.get("AVAILABLE_ACTIONS", []):
            return None, f"unknown action {action.strip()}"
        return dict(raw), None

    def _close_decision_gate(self, key, status):
        """Remove one decisionGates entry exactly once. Lock held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        gates = god.get("decisionGates")
        if not isinstance(gates, dict):
            return None
        rec = gates.pop(key, None)
        if not isinstance(rec, dict):
            return None
        agent = self._find_agent_by_id(int(key))
        if agent is not None:
            agent["divineHold"] = False
        self._log_divine(rec.get("id"), None, rec.get("mode") or "decision_gate", rec,
                         {"status": status}, status, public=False)
        deja_id = rec.get("dejaVuReplayId")
        if deja_id and status in ("expired", "replaced", "revoked", "restore-closed"):
            replays = (self.civilization.get("godState") or {}).get("dejaVuReplays") or {}
            if isinstance(replays, dict) and deja_id in replays:
                self._finalize_deja_vu_replay(deja_id, status)
        crowd_id = rec.get("crowdCompulsionId")
        if crowd_id:
            self._maybe_finalize_crowd_compulsion(crowd_id)
        return rec

    def _advance_possession_queue(self, gate):
        if not isinstance(gate.get("queue"), list):
            return
        idx = int(gate.get("queueIndex") or 0) + 1
        gate["queueIndex"] = idx
        if idx >= len(gate["queue"]):
            gate["queueIndex"] = len(gate["queue"]) - 1

    def _dec_compulsion_turn(self, gate):
        remaining = gate.get("remainingTurns")
        if remaining is None:
            return
        if not isinstance(remaining, int) or remaining <= 0:
            return
        remaining -= 1
        gate["remainingTurns"] = remaining
        if remaining <= 0:
            key = str(gate.get("targetId"))
            deja_id = gate.get("dejaVuReplayId")
            step_index = gate.get("dejaVuStepIndex")
            self._close_decision_gate(key, "completed")
            if deja_id is not None and step_index is not None:
                self._advance_deja_vu_after_step(deja_id, step_index)

    def _apply_divine_possessed_decision(self, agent, decision, gate, gate_kind=None):
        """Apply a compelled/possessed decision with explicit divine attribution."""
        kind = gate_kind or gate.get("mode") or "possession"
        action = decision.get("action") or "rest"
        self.apply_decision(agent, decision)
        label = f"Divine {kind}: {agent['name']} — {action}"
        self._push_communication(f"divine_{kind}", "divine", agent["name"], label, source="divine")
        if action not in ("rest", "talk_to_nearby"):
            self._push_chronicle(label, kind="divine", source="divine")
        self._log_benchmark("divine_decision_gate", 1.0, {
            "kind": kind, "agent": agent["name"], "action": action,
        })

    def _apply_gated_decision(self, agent, decision, bypass_gate=False):
        """Decision gate hook immediately before apply_decision on the LLM path.

        Sage emergency rush-heal and in-flight Sage discard call apply_decision
        directly (bypass_gate=True) so survival stays authoritative over story."""
        voice = self._active_voice_guidance(agent)
        if voice.get("voice_guidance_active"):
            divine_response = decision.get("divine_response")
            if not isinstance(divine_response, dict):
                divine_response = {
                    "stance": "continue",
                    "reason": "missing_divine_response",
                }
                decision["divine_response"] = divine_response
                decision["divine_response_synthetic"] = True
            if divine_response.get("stance") == "follow":
                agent["goal"] = None
                agent["assignedTask"] = None
        if bypass_gate:
            self.apply_decision(agent, decision)
            if voice.get("voice_guidance_active"):
                self._record_divine_response_adherence(agent, decision, voice)
            return True
        gate = self._god_active_decision_gate_record(agent["id"])
        if not gate:
            self.apply_decision(agent, decision)
            self._append_decision_digest(agent["id"], decision)
            if voice.get("voice_guidance_active"):
                self._record_divine_response_adherence(agent, decision, voice)
            return True
        mode = gate.get("mode")
        if mode == "possession":
            pinned_raw = self._god_decision_gate_pinned(agent, gate)
            pinned, _ = self._god_normalize_pinned_decision(agent, pinned_raw)
            if pinned:
                self._apply_divine_possessed_decision(agent, pinned, gate)
                self._advance_possession_queue(gate)
            return True
        if mode == "compulsion":
            pinned_raw = gate.get("pinnedDecision")
            if isinstance(pinned_raw, dict):
                pinned, _ = self._god_normalize_pinned_decision(agent, pinned_raw)
                if pinned:
                    self._apply_divine_possessed_decision(agent, pinned, gate, gate_kind="compulsion")
                    self._dec_compulsion_turn(gate)
            return True
        if mode == "veto":
            status = gate.get("status") or "armed"
            if status == "holding":
                return False
            if gate.get("armed"):
                if self._god_veto_hold_count() >= GOD_VETO_HOLD_CAP:
                    self.apply_decision(agent, {
                        "action": "rest",
                        "reasoning": "Divine veto hold cap reached — auto-rest.",
                    })
                    return True
                gate["pendingDecision"] = copy.deepcopy(decision)
                gate["status"] = "holding"
                gate["holdFrame"] = self.frameTick
                gate["holdExpiresFrame"] = self.frameTick + GOD_VETO_HOLD_TIMEOUT_FRAMES
                agent["divineHold"] = True
                self._push_activity(
                    f"{agent['name']} is held for divine veto review (action withheld)")
                self._push_communication(
                    "divine_veto_hold", "divine", agent["name"],
                    f"Divine veto: {agent['name']}'s decision is withheld for operator review",
                    source="divine")
                return False
        self.apply_decision(agent, decision)
        self._append_decision_digest(agent["id"], decision)
        if voice.get("voice_guidance_active"):
            self._record_divine_response_adherence(agent, decision, voice)
        return True

    def _expire_decision_gates(self, restore=False):
        """Expire decision gates and resolve veto hold timeouts. Lock held."""
        god = self.civilization.get("godState") or {}
        gates = god.get("decisionGates")
        if not isinstance(gates, dict) or not gates:
            return
        ft = self.frameTick
        for key in list(gates.keys()):
            rec = gates.get(key)
            if not isinstance(rec, dict):
                gates.pop(key, None)
                continue
            if rec.get("mode") == "veto" and rec.get("status") == "holding":
                hold_expires = rec.get("holdExpiresFrame")
                if isinstance(hold_expires, int) and ft >= hold_expires:
                    agent = self._find_agent_by_id(int(key))
                    if agent is not None:
                        agent["divineHold"] = False
                        self._apply_divine_possessed_decision(
                            agent, {"action": "rest", "reasoning": "Divine veto timed out."},
                            rec, gate_kind="veto_timeout")
                    self._close_decision_gate(key, "restore-closed" if restore else "veto-timeout")
                    continue
            expires = rec.get("expiresFrame")
            if isinstance(expires, int) and ft >= expires:
                agent = self._find_agent_by_id(int(key))
                if agent is not None:
                    agent["divineHold"] = False
                status = "restore-closed" if restore else "expired"
                self._close_decision_gate(key, status)

    def _close_whisper_campaign(self, campaign_id, status):
        """Revoke every linked omen still active and remove the campaign record.
        Must be called with self.lock already held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        campaigns = god.get("whisperCampaigns")
        campaign = campaigns.get(campaign_id) if isinstance(campaigns, dict) else None
        if not isinstance(campaign, dict):
            return None
        omens = god.get("privateOmens") or {}
        for agent_key, omen_id in (campaign.get("targets") or {}).items():
            omen = omens.get(agent_key) if isinstance(omens, dict) else None
            if isinstance(omen, dict) and omen.get("id") == omen_id:
                self._close_omen(agent_key, status)
        if isinstance(campaigns, dict):
            campaigns.pop(campaign_id, None)
        self._log_divine(campaign_id, None, "whisper_campaign", campaign,
                         {"status": status}, status, public=False)
        return campaign

    def _close_crowd_compulsion(self, compulsion_id, status):
        """Revoke every linked gate still active and remove the parent record."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        compulsions = god.get("crowdCompulsions")
        parent = compulsions.get(compulsion_id) if isinstance(compulsions, dict) else None
        if not isinstance(parent, dict):
            return None
        gates = god.get("decisionGates") or {}
        for agent_key, gate_id in (parent.get("targets") or {}).items():
            gate = gates.get(agent_key) if isinstance(gates, dict) else None
            if isinstance(gate, dict) and gate.get("id") == gate_id:
                self._close_decision_gate(agent_key, status)
        if isinstance(compulsions, dict):
            compulsions.pop(compulsion_id, None)
        self._log_divine(compulsion_id, None, "crowd_compulsion", parent,
                         {"status": status}, status, public=False)
        return parent

    def _maybe_finalize_crowd_compulsion(self, compulsion_id):
        """Drop a crowd compulsion when every linked gate has closed."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        compulsions = god.get("crowdCompulsions")
        parent = compulsions.get(compulsion_id) if isinstance(compulsions, dict) else None
        if not isinstance(parent, dict):
            return
        gates = god.get("decisionGates") or {}
        for agent_key, gate_id in (parent.get("targets") or {}).items():
            gate = gates.get(agent_key) if isinstance(gates, dict) else None
            if isinstance(gate, dict) and gate.get("id") == gate_id:
                return
        if isinstance(compulsions, dict):
            compulsions.pop(compulsion_id, None)

    def _sweep_crowd_compulsions(self, restore=False):
        """Expire or finalize crowd compulsion parents after gate closures."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        compulsions = god.get("crowdCompulsions")
        if not isinstance(compulsions, dict) or not compulsions:
            return
        ft = self.frameTick
        for compulsion_id in list(compulsions.keys()):
            parent = compulsions.get(compulsion_id)
            if not isinstance(parent, dict):
                del compulsions[compulsion_id]
                continue
            expires_frame = parent.get("expiresFrame")
            if isinstance(expires_frame, int) and ft >= expires_frame:
                status = "restore-closed" if restore else "expired"
                self._close_crowd_compulsion(compulsion_id, status)
            else:
                self._maybe_finalize_crowd_compulsion(compulsion_id)

    def _close_dream_broadcast(self, broadcast_id, status):
        """Revoke every linked dream mask still active and remove the parent."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        broadcasts = god.get("dreamBroadcasts")
        parent = broadcasts.get(broadcast_id) if isinstance(broadcasts, dict) else None
        if not isinstance(parent, dict):
            return None
        masks = god.get("contextMasks") or {}
        for agent_key, mask_id in (parent.get("targets") or {}).items():
            mask = masks.get(agent_key) if isinstance(masks, dict) else None
            if isinstance(mask, dict) and mask.get("id") == mask_id:
                self._close_context_mask(agent_key, status)
        if isinstance(broadcasts, dict):
            broadcasts.pop(broadcast_id, None)
        self._log_divine(broadcast_id, None, "dream_broadcast", parent,
                         {"status": status}, status, public=False)
        return parent

    def _maybe_finalize_dream_broadcast(self, broadcast_id):
        """Drop a dream broadcast when every linked mask has closed."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        broadcasts = god.get("dreamBroadcasts")
        parent = broadcasts.get(broadcast_id) if isinstance(broadcasts, dict) else None
        if not isinstance(parent, dict):
            return
        masks = god.get("contextMasks") or {}
        for agent_key, mask_id in (parent.get("targets") or {}).items():
            mask = masks.get(agent_key) if isinstance(masks, dict) else None
            if isinstance(mask, dict) and mask.get("id") == mask_id:
                return
        if isinstance(broadcasts, dict):
            broadcasts.pop(broadcast_id, None)

    def _sweep_dream_broadcasts(self, restore=False):
        """Expire or finalize dream broadcast parents after mask closures."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        broadcasts = god.get("dreamBroadcasts")
        if not isinstance(broadcasts, dict) or not broadcasts:
            return
        ft = self.frameTick
        for broadcast_id in list(broadcasts.keys()):
            parent = broadcasts.get(broadcast_id)
            if not isinstance(parent, dict):
                del broadcasts[broadcast_id]
                continue
            expires_frame = parent.get("expiresFrame")
            if isinstance(expires_frame, int) and ft >= expires_frame:
                status = "restore-closed" if restore else "expired"
                self._close_dream_broadcast(broadcast_id, status)
            else:
                self._maybe_finalize_dream_broadcast(broadcast_id)

    def _maybe_finalize_whisper_campaign(self, campaign_id):
        """Drop a campaign when every linked omen has already closed."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        campaigns = god.get("whisperCampaigns")
        campaign = campaigns.get(campaign_id) if isinstance(campaigns, dict) else None
        if not isinstance(campaign, dict):
            return
        omens = god.get("privateOmens") or {}
        for agent_key, omen_id in (campaign.get("targets") or {}).items():
            omen = omens.get(agent_key) if isinstance(omens, dict) else None
            if isinstance(omen, dict) and omen.get("id") == omen_id:
                return
        campaigns.pop(campaign_id, None)

    def _sweep_whisper_campaigns(self, restore=False):
        """Expire or finalize whisper campaigns after omen closures."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        campaigns = god.get("whisperCampaigns")
        if not isinstance(campaigns, dict) or not campaigns:
            return
        ft = self.frameTick
        for campaign_id in list(campaigns.keys()):
            campaign = campaigns.get(campaign_id)
            if not isinstance(campaign, dict):
                del campaigns[campaign_id]
                continue
            expires_frame = campaign.get("expiresFrame")
            if isinstance(expires_frame, int) and ft >= expires_frame:
                status = "restore-closed" if restore else "expired"
                self._close_whisper_campaign(campaign_id, status)
            else:
                self._maybe_finalize_whisper_campaign(campaign_id)

    def _god_apply_revoke_guidance(self, guidance_id):
        """Ends a providence or omen early by id (docs/archive/plan-sovereign-god-mode-v2.md: "records a
        revocation"). Returns (outcome, reason) directly -- unlike the other
        _god_apply_* helpers this can itself fail (unknown/already-inactive
        id), and a failed revoke must never consume an intervention id."""
        god = self.civilization["godState"]
        prov = god.get("providence")
        if isinstance(prov, dict) and prov.get("id") == guidance_id:
            self._close_providence("revoked")
            self._push_activity("A divine providence is revoked.")
            intervention_id = self._next_intervention_id()
            self._god_record_intervention({
                "id": intervention_id, "kind": "revoke_guidance", "frameTick": self.frameTick,
                "targetGuidanceId": guidance_id, "targetKind": "providence",
                "status": "applied", "public": True,
            })
            return {"interventionId": intervention_id, "kind": "revoke_guidance",
                    "targetGuidanceId": guidance_id, "targetKind": "providence"}, None
        for key, omen in list((god.get("privateOmens") or {}).items()):
            if isinstance(omen, dict) and omen.get("id") == guidance_id:
                self._close_omen(key, "revoked")
                intervention_id = self._next_intervention_id()
                self._god_record_intervention({
                    "id": intervention_id, "kind": "revoke_guidance", "frameTick": self.frameTick,
                    "targetGuidanceId": guidance_id, "targetKind": "private_omen",
                    "status": "applied", "public": False,
                })
                return {"interventionId": intervention_id, "kind": "revoke_guidance",
                        "targetGuidanceId": guidance_id, "targetKind": "private_omen"}, None
        return None, "guidance id not found or already inactive"
