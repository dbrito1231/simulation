"""Phase 6f mixin: Divine Matrix Phase 2 per-agent sampling overlay slice of
SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_god_sampling_optional_float`
through `_apply_context_mask` (formerly core.py lines ~1850-2643). Covers the
Divine Matrix Phase 2 per-agent sampling overlay cluster: sampling-payload
validation helpers, dream-snapshot/forged-conversation validation, context
masks, decision compulsion/veto/possession gate validation, anointment and
stigmata, identity edit/copy-overwrite validation, Merovingian bargain
predicates/primitives, and the reality-distortion `_apply_context_mask` used
by the think-payload builder.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _DivineSamplingMixin:
    """Mixin slice of SimEngine: Divine Matrix Phase 2 per-agent sampling
    overlay -- sampling validation, dream snapshots, context masks, decision
    gate validation, anointment/identity edit validation, and Merovingian
    bargain predicates. See module docstring for exact scope."""

    # --- Divine Matrix Phase 2: per-agent sampling overlay ---
    def _god_sampling_optional_float(self, raw, label, lo, hi):
        if raw is None:
            return None, None
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None, f"{label} must be a number"
        if not math.isfinite(raw):
            return None, f"{label} must be finite"
        val = float(raw)
        if not (lo <= val <= hi):
            return None, f"{label} must be between {lo} and {hi}"
        return val, None

    def _god_sampling_optional_int(self, raw, label, lo, hi):
        if raw is None:
            return None, None
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None, f"{label} must be an integer"
        if not (lo <= raw <= hi):
            return None, f"{label} must be between {lo} and {hi}"
        return raw, None

    def _god_active_agent_sampling_record(self, agent_id):
        """Active sampling override for agent_id, or None if absent/expired."""
        god = self.civilization.get("godState") or {}
        rec = (god.get("agentSampling") or {}).get(str(agent_id))
        if not isinstance(rec, dict):
            return None
        expires = rec.get("expiresFrame")
        if isinstance(expires, int) and self.frameTick >= expires:
            return None
        agent = self._find_agent_by_id(agent_id)
        if agent is None or agent.get("deathFrame") is not None:
            return None
        return rec

    def _god_fast_sampling_override_count(self, exclude_target_id=None):
        """Living agents with an active sim-fast decision override, optionally
        excluding one target (replace semantics for the fast-route cap)."""
        god = self.civilization.get("godState") or {}
        sampling = god.get("agentSampling") or {}
        count = 0
        if not isinstance(sampling, dict):
            return 0
        exclude_key = str(exclude_target_id) if exclude_target_id is not None else None
        for key, rec in sampling.items():
            if exclude_key is not None and key == exclude_key:
                continue
            if not isinstance(rec, dict) or rec.get("model") != "sim-fast":
                continue
            expires = rec.get("expiresFrame")
            if isinstance(expires, int) and self.frameTick >= expires:
                continue
            agent = self._find_agent_by_id(rec.get("targetId"))
            if agent is None or agent.get("deathFrame") is not None:
                continue
            count += 1
        return count

    def _validate_god_agent_sampling(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"

        model = payload.get("model", "sim-smart")
        if not isinstance(model, str):
            return None, "model must be a string"
        model = model.strip()
        if model not in GOD_AGENT_SAMPLING_MODELS:
            return None, 'model must be "sim-smart" or "sim-fast"'

        temperature, reason = self._god_sampling_optional_float(
            payload.get("temperature"), "temperature",
            GOD_AGENT_SAMPLING_TEMP_MIN, GOD_AGENT_SAMPLING_TEMP_MAX)
        if reason:
            return None, reason
        if temperature is None:
            return None, "temperature is required"

        top_p, reason = self._god_sampling_optional_float(
            payload.get("top_p"), "top_p",
            GOD_AGENT_SAMPLING_TOP_P_MIN, GOD_AGENT_SAMPLING_TOP_P_MAX)
        if reason:
            return None, reason
        top_k, reason = self._god_sampling_optional_int(
            payload.get("top_k"), "top_k",
            GOD_AGENT_SAMPLING_TOP_K_MIN, GOD_AGENT_SAMPLING_TOP_K_MAX)
        if reason:
            return None, reason
        min_p, reason = self._god_sampling_optional_float(
            payload.get("min_p"), "min_p",
            GOD_AGENT_SAMPLING_MIN_P_MIN, GOD_AGENT_SAMPLING_MIN_P_MAX)
        if reason:
            return None, reason

        duration_raw = payload.get("durationFrames")
        if duration_raw is not None:
            if isinstance(duration_raw, bool) or not isinstance(duration_raw, int):
                return None, "durationFrames must be an integer"

        if model == "sim-fast":
            if (self._god_fast_sampling_override_count(exclude_target_id=target_id)
                    >= GOD_AGENT_SAMPLING_FAST_DECISION_CAP):
                return None, (
                    f"at most {GOD_AGENT_SAMPLING_FAST_DECISION_CAP} living agent may "
                    "use sim-fast for decisions at once")

        normalized = {
            "targetId": target_id,
            "model": model,
            "temperature": temperature,
        }
        if top_p is not None:
            normalized["top_p"] = top_p
        if top_k is not None:
            normalized["top_k"] = top_k
        if min_p is not None:
            normalized["min_p"] = min_p
        if duration_raw is not None:
            normalized["durationFrames"] = self._clamp_god_duration(duration_raw)
        return normalized, None

    def _god_divine_sampling_for_think(self, agent):
        """Think-payload overlay for build_decision_payload; no secrets."""
        if not GOD_MODE_ENABLED:
            return None
        rec = self._god_active_agent_sampling_record(agent["id"])
        if rec is None:
            return None
        out = {"model": rec["model"], "temperature": rec["temperature"]}
        for key in ("top_p", "top_k", "min_p"):
            if key in rec:
                out[key] = rec[key]
        return out

    def _god_active_context_mask_record(self, agent_id):
        """Active context mask for agent_id, or None if absent/expired."""
        god = self.civilization.get("godState") or {}
        rec = (god.get("contextMasks") or {}).get(str(agent_id))
        if not isinstance(rec, dict):
            return None
        expires = rec.get("expiresFrame")
        if isinstance(expires, int) and self.frameTick >= expires:
            return None
        agent = self._find_agent_by_id(agent_id)
        if agent is None or agent.get("deathFrame") is not None:
            return None
        return rec

    def _god_current_outgoing_context_mask_id(self, target_id):
        rec = (self.civilization.get("godState") or {}).get("contextMasks", {}).get(str(target_id))
        return rec.get("id") if isinstance(rec, dict) else None

    def _validate_god_dream_snapshot(self, raw):
        if not isinstance(raw, dict) or not raw:
            return None, "dreamSnapshot must be a non-empty object"
        unknown = set(raw.keys()) - GOD_CONTEXT_MASK_DREAM_KEYS
        if unknown:
            return None, f"dreamSnapshot has unknown keys: {sorted(unknown)}"
        out = {}
        for key, val in raw.items():
            if key == "nearby_agents":
                if not isinstance(val, list):
                    return None, "dreamSnapshot.nearby_agents must be a list"
                out[key] = [dict(e) if isinstance(e, dict) else e for e in val]
            elif key == "resources":
                if not isinstance(val, dict):
                    return None, "dreamSnapshot.resources must be an object"
                out[key] = {str(k): v for k, v in val.items()}
            elif key == "nearby_wildlife":
                if not isinstance(val, list):
                    return None, "dreamSnapshot.nearby_wildlife must be a list"
                out[key] = [dict(e) if isinstance(e, dict) else e for e in val]
            elif key in ("weather_line", "recent_conversations", "district_stocks",
                           "nearby_wildlife_line"):
                if val is not None and not isinstance(val, str):
                    return None, f"dreamSnapshot.{key} must be a string or null"
                if isinstance(val, str):
                    text, reason = self._normalize_divine_text(
                        val, max_chars=GOD_TEXT_MAX_CHARS * 2, max_bytes=GOD_TEXT_MAX_BYTES * 2)
                    if reason:
                        return None, f"dreamSnapshot.{key} {reason}"
                    out[key] = text
                else:
                    out[key] = val
            elif key in ("hunger", "health"):
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    return None, f"dreamSnapshot.{key} must be a number"
                if not math.isfinite(val):
                    return None, f"dreamSnapshot.{key} must be finite"
                out[key] = float(val)
        return out, None

    def _validate_god_forged_conversations(self, raw):
        if not isinstance(raw, list) or not raw:
            return None, "forgedConversations must be a non-empty list"
        if len(raw) > GOD_CONTEXT_MASK_FORGED_MAX:
            return None, (
                f"forgedConversations may include at most {GOD_CONTEXT_MASK_FORGED_MAX} entries")
        out = []
        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                return None, f"forgedConversations[{idx}] must be an object"
            frm = entry.get("from")
            to = entry.get("to")
            if not isinstance(frm, str) or not frm.strip():
                return None, f"forgedConversations[{idx}].from must be a non-empty string"
            if not isinstance(to, str) or not to.strip():
                return None, f"forgedConversations[{idx}].to must be a non-empty string"
            msg, reason = self._normalize_divine_text(entry.get("message"))
            if reason:
                return None, f"forgedConversations[{idx}].message {reason}"
            out.append({"from": frm.strip(), "to": to.strip(), "message": msg})
        return out, None

    def _validate_god_context_mask(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in GOD_CONTEXT_MASK_MODES:
            return None, 'mode must be one of "dream", "blue_pill", "red_pill", "whisper_chain"'
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)
        normalized = {"targetId": target_id, "mode": mode, "durationFrames": duration}
        if mode == "dream":
            snapshot, reason = self._validate_god_dream_snapshot(payload.get("dreamSnapshot"))
            if reason:
                return None, reason
            normalized["dreamSnapshot"] = snapshot
        elif mode == "whisper_chain":
            forged, reason = self._validate_god_forged_conversations(
                payload.get("forgedConversations"))
            if reason:
                return None, reason
            normalized["forgedConversations"] = forged
        return normalized, None

    def _validate_god_pinned_decision_fields(self, agent, raw, label="pinnedDecision"):
        if not isinstance(raw, dict):
            return None, f"{label} must be an object"
        normalized, reason = self._god_normalize_pinned_decision(agent, raw)
        if reason:
            return None, f"{label} {reason}"
        return normalized, None

    def _validate_god_decision_compulsion(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        pinned, reason = self._validate_god_pinned_decision_fields(
            agent, payload.get("pinnedDecision"))
        if reason:
            return None, reason
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        remaining = payload.get("remainingTurns")
        if remaining is not None:
            if isinstance(remaining, bool) or not isinstance(remaining, int):
                return None, "remainingTurns must be an integer"
            if remaining <= 0:
                return None, "remainingTurns must be positive"
        if duration_raw is None and remaining is None:
            return None, "at least one of durationFrames or remainingTurns is required"
        normalized = {
            "targetId": target_id,
            "pinnedDecision": pinned,
        }
        if duration_raw is not None:
            normalized["durationFrames"] = self._clamp_god_duration(duration_raw)
        if remaining is not None:
            normalized["remainingTurns"] = remaining
        return normalized, None

    def _validate_god_decision_veto_arm(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)
        return {"targetId": target_id, "durationFrames": duration}, None

    def _validate_god_decision_veto_resolve(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        resolution = payload.get("resolution")
        if resolution not in ("approve", "reject", "rewrite"):
            return None, 'resolution must be "approve", "reject", or "rewrite"'
        gate = self._god_active_decision_gate_record(target_id)
        if gate is None or gate.get("mode") != "veto" or gate.get("status") != "holding":
            return None, "no pending veto hold for this agent"
        normalized = {"targetId": target_id, "resolution": resolution}
        if resolution == "rewrite":
            rewritten, reason = self._validate_god_pinned_decision_fields(
                agent, payload.get("rewrittenDecision"), label="rewrittenDecision")
            if reason:
                return None, reason
            normalized["rewrittenDecision"] = rewritten
        return normalized, None

    def _validate_god_agent_possession(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)
        queue_raw = payload.get("queue")
        pinned_raw = payload.get("pinnedDecision")
        if queue_raw is not None:
            if not isinstance(queue_raw, list) or not queue_raw:
                return None, "queue must be a non-empty list"
            if len(queue_raw) > 8:
                return None, "queue may include at most 8 decisions"
            queue = []
            for idx, entry in enumerate(queue_raw):
                norm, reason = self._validate_god_pinned_decision_fields(
                    agent, entry, label=f"queue[{idx}]")
                if reason:
                    return None, reason
                queue.append(norm)
            return {
                "targetId": target_id,
                "queue": queue,
                "durationFrames": duration,
            }, None
        if pinned_raw is None:
            return None, "pinnedDecision or queue is required"
        pinned, reason = self._validate_god_pinned_decision_fields(agent, pinned_raw)
        if reason:
            return None, reason
        return {
            "targetId": target_id,
            "pinnedDecision": pinned,
            "durationFrames": duration,
        }, None

    def _validate_god_revoke_decision_gate(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        gate = (self.civilization.get("godState") or {}).get("decisionGates", {}).get(str(target_id))
        if not isinstance(gate, dict):
            return None, "no active decision gate for this agent"
        return {"targetId": target_id}, None

    def _normalize_stigmata_tag(self, raw):
        """Short public tag for stigmata (neighbor-visible, not destiny)."""
        if not isinstance(raw, str):
            return None, "must be a string"
        text, reason = self._normalize_divine_text(
            raw, max_chars=GOD_ANOINT_STIGMATA_TAG_MAX_CHARS,
            max_bytes=GOD_ANOINT_STIGMATA_TAG_MAX_CHARS * 3)
        return text, reason

    def _validate_god_anoint(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"

        destiny, reason = self._normalize_divine_text(payload.get("destinyText"))
        if reason:
            return None, f"destinyText {reason}"

        stigmata_raw = payload.get("stigmataTags")
        stigmata_tags = []
        if stigmata_raw is not None:
            if not isinstance(stigmata_raw, list):
                return None, "stigmataTags must be an array"
            if len(stigmata_raw) > GOD_ANOINT_STIGMATA_MAX:
                return None, f"stigmataTags may have at most {GOD_ANOINT_STIGMATA_MAX} entries"
            for i, tag_raw in enumerate(stigmata_raw):
                tag, tag_reason = self._normalize_stigmata_tag(tag_raw)
                if tag_reason:
                    return None, f"stigmataTags[{i}] {tag_reason}"
                if tag:
                    stigmata_tags.append(tag)

        hints_raw = payload.get("oracleHints")
        oracle_hints = []
        if hints_raw is not None:
            if not isinstance(hints_raw, list):
                return None, "oracleHints must be an array"
            if len(hints_raw) > GOD_ANOINT_ORACLE_HINTS_MAX:
                return None, (f"oracleHints may have at most "
                              f"{GOD_ANOINT_ORACLE_HINTS_MAX} entries")
            for i, hint_raw in enumerate(hints_raw):
                if not isinstance(hint_raw, dict):
                    return None, f"oracleHints[{i}] must be an object"
                reveal = hint_raw.get("revealFrame")
                if isinstance(reveal, bool) or not isinstance(reveal, int) or reveal < 0:
                    return None, f"oracleHints[{i}].revealFrame must be a non-negative integer"
                text, text_reason = self._normalize_divine_text(hint_raw.get("text"))
                if text_reason:
                    return None, f"oracleHints[{i}].text {text_reason}"
                oracle_hints.append({"text": text, "revealFrame": reveal})

        duration_raw = payload.get("durationFrames")
        if duration_raw is not None:
            if isinstance(duration_raw, bool) or not isinstance(duration_raw, int):
                return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)

        normalized = {
            "targetId": target_id,
            "destinyText": destiny,
            "stigmataTags": stigmata_tags,
            "oracleHints": oracle_hints,
            "durationFrames": duration,
        }
        return normalized, None

    def _validate_god_identity_edit(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"

        persona = None
        if payload.get("persona") is not None:
            persona, reason = self._normalize_divine_text(
                payload.get("persona"),
                max_chars=GOD_IDENTITY_PERSONA_MAX_CHARS,
                max_bytes=GOD_IDENTITY_PERSONA_MAX_CHARS * 3,
            )
            if reason:
                return None, f"persona {reason}"

        personality = None
        if payload.get("personality") is not None:
            personality, reason = self._normalize_divine_text(
                payload.get("personality"),
                max_chars=GOD_IDENTITY_PERSONALITY_MAX_CHARS,
                max_bytes=GOD_TEXT_MAX_BYTES,
            )
            if reason:
                return None, f"personality {reason}"

        role = None
        if payload.get("role") is not None:
            if not isinstance(payload.get("role"), str) or not payload.get("role").strip():
                return None, "role must be a non-empty string"
            role = payload.get("role").strip()
            if not self._god_role_valid(role):
                return None, "role must exist in roles.json"

        if persona is None and personality is None and role is None:
            return None, "at least one of persona, personality, or role is required"

        duration_raw = payload.get("durationFrames")
        if duration_raw is not None:
            if isinstance(duration_raw, bool) or not isinstance(duration_raw, int):
                return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw) if duration_raw is not None else None

        normalized = {"targetId": target_id}
        if persona is not None:
            normalized["persona"] = persona
        if personality is not None:
            normalized["personality"] = personality
        if role is not None:
            normalized["role"] = role
        if duration is not None:
            normalized["durationFrames"] = duration
        return normalized, None

    def _validate_god_identity_copy_overwrite(self, payload):
        target_id = payload.get("targetId")
        source_id = payload.get("sourceId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            return None, "sourceId must be an integer agent id"
        if target_id == source_id:
            return None, "targetId and sourceId must differ"
        target = self._find_agent_by_id(target_id)
        if target is None:
            return None, "unknown target agent"
        if target.get("deathFrame") is not None:
            return None, "target agent is deceased"
        source = self._find_agent_by_id(source_id)
        if source is None:
            return None, "unknown source agent"
        if source.get("deathFrame") is not None:
            return None, "source agent is deceased"
        rate_raw = payload.get("ratePerThink")
        if isinstance(rate_raw, bool) or not isinstance(rate_raw, (int, float)):
            return None, "ratePerThink must be a number"
        if not math.isfinite(rate_raw):
            return None, "ratePerThink must be finite"
        rate = max(0.0, min(1.0, float(rate_raw)))
        sync_raw = payload.get("syncMemories")
        if sync_raw is not None and not isinstance(sync_raw, bool):
            return None, "syncMemories must be a boolean"
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None:
            if isinstance(duration_raw, bool) or not isinstance(duration_raw, int):
                return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw) if duration_raw is not None else None
        normalized = {
            "targetId": target_id,
            "sourceId": source_id,
            "ratePerThink": rate,
            "syncMemories": bool(sync_raw),
        }
        if duration is not None:
            normalized["durationFrames"] = duration
        return normalized, None

    def _validate_god_bargain_predicate(self, raw, target_id, label="predicate"):
        """Validate one allowlisted bargain predicate. Returns (normalized, reason)."""
        if not isinstance(raw, dict):
            return None, f"{label} must be an object"
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in GOD_BARGAIN_PREDICATES:
            return None, (f"{label}.kind must be one of "
                          f"{sorted(GOD_BARGAIN_PREDICATES)}")
        if kind == "agent_has_resource":
            resource_id = raw.get("resourceId")
            if not isinstance(resource_id, str) or not resource_id.strip():
                return None, f"{label}.resourceId is required"
            resource_id = resource_id.strip()
            if resource_id not in self.civilization.get("resourceRegistry", {}):
                return None, f"{label}.resourceId is unknown"
            amount = raw.get("amount", 1)
            if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
                return None, f"{label}.amount must be a positive integer"
            return {"kind": kind, "resourceId": resource_id, "amount": amount}, None
        if kind == "structure_built":
            structure_type = raw.get("structureType")
            if not isinstance(structure_type, str) or not structure_type.strip():
                return None, f"{label}.structureType is required"
            structure_type = structure_type.strip()
            registry = self.civilization.get("projectRegistry") or {}
            if structure_type not in registry:
                return None, f"{label}.structureType is unknown"
            return {"kind": kind, "structureType": structure_type}, None
        if kind == "frame_reached":
            frame = raw.get("frame")
            if isinstance(frame, bool) or not isinstance(frame, int) or frame < 0:
                return None, f"{label}.frame must be a non-negative integer"
            return {"kind": kind, "frame": frame}, None
        if kind == "agent_health_below":
            threshold = raw.get("threshold")
            if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
                return None, f"{label}.threshold must be a number"
            if not math.isfinite(threshold):
                return None, f"{label}.threshold must be finite"
            threshold = float(threshold)
            if not (0.0 <= threshold <= 100.0):
                return None, f"{label}.threshold must be between 0 and 100"
            return {"kind": kind, "threshold": threshold}, None
        return None, f"unknown {label} kind"

    def _validate_god_bargain_primitive(self, raw, label):
        if raw is None:
            return None, None
        if not isinstance(raw, dict):
            return None, f"{label} must be an object"
        kind = raw.get("kind")
        if kind not in GOD_BARGAIN_PRIMITIVE_KINDS:
            return None, (f"{label}.kind must be one of "
                          f"{sorted(GOD_BARGAIN_PRIMITIVE_KINDS)}")
        prim_normalized, reason = self._validate_god_envelope(
            {"kind": kind, "payload": raw.get("payload") or {}})
        if reason:
            return None, f"{label}: {reason}"
        return {"kind": kind, "payload": prim_normalized["payload"]}, None

    def _validate_god_merovingian_bargain(self, payload):
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        terms_text, reason = self._normalize_divine_text(payload.get("termsText"))
        if reason:
            return None, f"merovingian_bargain termsText {reason}"
        success_pred, reason = self._validate_god_bargain_predicate(
            payload.get("successPredicate"), target_id, label="successPredicate")
        if reason:
            return None, reason
        failure_pred = None
        if payload.get("failurePredicate") is not None:
            failure_pred, reason = self._validate_god_bargain_predicate(
                payload.get("failurePredicate"), target_id, label="failurePredicate")
            if reason:
                return None, reason
        reward_prim, reason = self._validate_god_bargain_primitive(
            payload.get("rewardPrimitive"), "rewardPrimitive")
        if reason:
            return None, reason
        punish_prim, reason = self._validate_god_bargain_primitive(
            payload.get("punishPrimitive"), "punishPrimitive")
        if reason:
            return None, reason
        duration_raw = payload.get("durationFrames")
        if duration_raw is not None and (
            isinstance(duration_raw, bool) or not isinstance(duration_raw, int)
        ):
            return None, "durationFrames must be an integer"
        duration = self._clamp_god_duration(duration_raw)
        key = str(target_id)
        bush = (self.civilization.get("godState") or {}).get("burningBush", {}).get(key)
        if isinstance(bush, dict):
            bargain = bush.get("bargain")
            if isinstance(bargain, dict) and bargain.get("status") == "open":
                return None, "target already has an open bargain"
        normalized = {
            "targetId": target_id,
            "termsText": terms_text,
            "successPredicate": success_pred,
            "durationFrames": duration,
        }
        if failure_pred is not None:
            normalized["failurePredicate"] = failure_pred
        if reward_prim is not None:
            normalized["rewardPrimitive"] = reward_prim
        if punish_prim is not None:
            normalized["punishPrimitive"] = punish_prim
        return normalized, None

    def _evaluate_god_bargain_predicate(self, predicate, target_id):
        if not isinstance(predicate, dict):
            return False
        kind = predicate.get("kind")
        agent = self._find_agent_by_id(target_id)
        if agent is None or agent.get("deathFrame") is not None:
            return False
        if kind == "agent_has_resource":
            rid = predicate.get("resourceId")
            amount = predicate.get("amount", 1)
            held = agent.get("resources", {}).get(rid, 0)
            stock = self.civilization.get("stockpile", {}).get(rid, 0)
            return (held + stock) >= amount
        if kind == "structure_built":
            return self._structure_type_built(predicate.get("structureType"))
        if kind == "frame_reached":
            frame = predicate.get("frame")
            return isinstance(frame, int) and self.frameTick >= frame
        if kind == "agent_health_below":
            threshold = predicate.get("threshold")
            return isinstance(threshold, (int, float)) and agent.get("health", 100) < threshold
        return False

    def _divine_public_event_line(self, agent):
        """Active public story-event narration for think payload (blue_pill strips)."""
        if not GOD_MODE_ENABLED:
            return None
        god = self.civilization.get("godState") or {}
        ft = self.frameTick
        lines = []
        for event in god.get("activeEvents") or []:
            if not isinstance(event, dict) or event.get("status") != "active":
                continue
            if event.get("visibility", "public") != "public":
                continue
            expires = event.get("expiresFrame")
            if isinstance(expires, int) and ft >= expires:
                continue
            text = event.get("narration") or event.get("title")
            if text:
                lines.append(str(text))
        if not lines:
            return None
        combined = " | ".join(lines[:3])
        if len(combined) > GOD_TEXT_MAX_CHARS:
            combined = combined[:GOD_TEXT_MAX_CHARS]
        return combined

    def _filter_divine_conversations_text(self, conv_text):
        if not conv_text or conv_text == "none":
            return "none"
        parts = [p for p in conv_text.split(" | ")
                 if p and not p.lower().startswith("divine ->")]
        return " | ".join(parts) if parts else "none"

    def _build_red_pill_truth_line(self, agent):
        """Simulation-truth injection — never includes other agents' private omens."""
        parts = [
            "SIMULATION TRUTH: You are an agent in a server-authoritative village simulation.",
            f"Your id is {agent['id']}; your role is {agent.get('role')}.",
        ]
        god = self.civilization.get("godState") or {}
        if god.get("intervened"):
            parts.append("A divine operator has actively intervened in this world.")
        public_stories = []
        for event in god.get("activeEvents") or []:
            if (isinstance(event, dict) and event.get("status") == "active"
                    and event.get("visibility", "public") == "public"):
                title = event.get("title")
                if title:
                    public_stories.append(str(title))
        if public_stories:
            parts.append("Active public divine stories: " + ", ".join(public_stories[:3]) + ".")
        flags = []
        if MEMES_ENABLED:
            flags.append("memes")
        if WEATHER_ENABLED:
            flags.append("weather")
        if TECH_TREE_ENABLED:
            flags.append("tech_tree")
        if flags:
            parts.append("Enabled systems: " + ", ".join(flags) + ".")
        text = " ".join(parts)
        if len(text) > GOD_TEXT_MAX_CHARS:
            text = text[:GOD_TEXT_MAX_CHARS]
        return text

    def _apply_context_mask(self, agent, payload):
        """Reality-distortion layer on the think snapshot only — never mutates world logs."""
        if not GOD_MODE_ENABLED:
            return
        rec = self._god_active_context_mask_record(agent["id"])
        if rec is None:
            return
        mode = rec.get("mode")
        if mode == "blue_pill":
            payload["divine_public_line"] = None
            payload["divine_private_line"] = None
            payload["divine_public_event_line"] = None
            conv = payload.get("recent_conversations")
            if conv:
                payload["recent_conversations"] = self._filter_divine_conversations_text(conv)
        elif mode == "red_pill":
            payload["divine_simulation_truth_line"] = self._build_red_pill_truth_line(agent)
        elif mode == "dream":
            for key, val in (rec.get("dreamSnapshot") or {}).items():
                if isinstance(val, (dict, list)):
                    payload[key] = copy.deepcopy(val)
                else:
                    payload[key] = val
        elif mode == "whisper_chain":
            forged = rec.get("forgedConversations") or []
            if forged:
                payload["recent_conversations"] = " | ".join(
                    f"{e['from']} -> {e['to']}: {e['message']}" for e in forged)

