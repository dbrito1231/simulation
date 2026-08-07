"""Phase 6h mixin: Sovereign God mode Burning Bush / Merovingian bargain /
anointment / identity-forge handlers slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_ensure_burning_bush_session`
through `_god_apply_identity_forge_cancel` (formerly core.py lines
~1008-1377). Covers the Burning Bush conversational-thread session lifecycle
(`_ensure_burning_bush_session`, `_close_burning_bush`,
`_god_apply_burning_bush_message`, `_god_apply_burning_bush_close`), the
Merovingian bargain primitives/settlement machinery
(`_god_apply_merovingian_bargain`, `_god_apply_bargain_primitive`,
`_settle_bargain`, `_god_apply_bargain_settle`), the Anointment apply/revoke
handlers (`_god_current_outgoing_anointment_id`, `_close_anointment`,
`_god_apply_anoint`, `_god_apply_revoke_anoint`), and the Identity Forge apply
handlers (`_god_apply_identity_values`, `_god_apply_identity_edit`,
`_god_apply_identity_copy_overwrite`, `_god_apply_identity_forge_cancel`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _GodBushBargainMixin:
    """Mixin slice of SimEngine: Sovereign God mode Burning Bush session
    lifecycle, Merovingian bargain primitives/settlement, Anointment apply/
    revoke, and Identity Forge apply handlers. See module docstring for the
    exact method range and rationale."""

    def _ensure_burning_bush_session(self, target_id):
        """Return active bush record for target, creating one if needed."""
        god = self.civilization["godState"]
        bushes = god.setdefault("burningBush", {})
        key = str(target_id)
        rec = bushes.get(key)
        if isinstance(rec, dict) and rec.get("status") == "active":
            return rec
        intervention_id = self._next_intervention_id()
        agent = self._find_agent_by_id(target_id)
        rec = {
            "id": intervention_id,
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "thread": [],
            "createdFrame": self.frameTick,
            "status": "active",
        }
        bushes[key] = rec
        return rec

    def _close_burning_bush(self, key, status):
        """Close a Burning Bush session. Lock held."""
        god = self.civilization.get("godState") or {}
        bushes = god.get("burningBush") or {}
        rec = bushes.get(key)
        if not isinstance(rec, dict) or rec.get("status") != "active":
            return None
        rec["status"] = status
        rec["closedFrame"] = self.frameTick
        bargain = rec.get("bargain")
        if isinstance(bargain, dict) and bargain.get("status") == "open":
            bargain["status"] = "cancelled"
            bargain["settledFrame"] = self.frameTick
        self._log_divine(rec.get("id"), None, "burning_bush_close", rec,
                         {"status": status}, status, public=False)
        return rec

    def _god_apply_burning_bush_message(self, payload):
        """Append a private God line to one agent's Burning Bush thread."""
        target_id = payload["targetId"]
        key = str(target_id)
        rec = self._ensure_burning_bush_session(target_id)
        intervention_id = rec["id"]
        text = payload["text"]
        thread = rec.setdefault("thread", [])
        if not isinstance(thread, list):
            rec["thread"] = thread = []
        thread.append({"role": "god", "text": text, "frame": self.frameTick})
        if len(thread) > GOD_BURNING_BUSH_THREAD_MAX:
            del thread[:-GOD_BURNING_BUSH_THREAD_MAX]
        self._god_record_intervention({
            "id": intervention_id, "kind": "burning_bush_message",
            "frameTick": self.frameTick, "targetId": target_id,
            "messageCount": len(thread), "status": "applied", "public": False,
        })
        self._log_divine(intervention_id, None, "burning_bush_message",
                         {"targetId": target_id, "text": text},
                         {"targetId": target_id, "messageCount": len(thread)},
                         "applied", public=False)
        return {
            "interventionId": intervention_id,
            "kind": "burning_bush_message",
            "targetId": target_id,
            "messageCount": len(thread),
        }

    def _god_apply_burning_bush_close(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        rec = self._close_burning_bush(key, "closed")
        if rec is None:
            return {"interventionId": None, "kind": "burning_bush_close",
                    "targetId": target_id, "status": "noop"}
        return {
            "interventionId": rec.get("id"),
            "kind": "burning_bush_close",
            "targetId": target_id,
            "status": "closed",
        }

    def _god_apply_merovingian_bargain(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        rec = self._ensure_burning_bush_session(target_id)
        bargain_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        bargain = {
            "id": bargain_id,
            "termsText": payload["termsText"],
            "successPredicate": payload["successPredicate"],
            "status": "open",
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        if payload.get("failurePredicate") is not None:
            bargain["failurePredicate"] = payload["failurePredicate"]
        if payload.get("rewardPrimitive") is not None:
            bargain["rewardPrimitive"] = payload["rewardPrimitive"]
        if payload.get("punishPrimitive") is not None:
            bargain["punishPrimitive"] = payload["punishPrimitive"]
        rec["bargain"] = bargain
        self._god_record_intervention({
            "id": bargain_id, "kind": "merovingian_bargain",
            "frameTick": self.frameTick, "targetId": target_id,
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        self._log_divine(bargain_id, None, "merovingian_bargain",
                         {"targetId": target_id, "termsText": payload["termsText"],
                          "successPredicate": payload["successPredicate"]},
                         {"targetId": target_id, "expiresFrame": expires_frame},
                         "applied", public=False)
        return {
            "interventionId": bargain_id,
            "kind": "merovingian_bargain",
            "targetId": target_id,
            "expiresFrame": expires_frame,
        }

    def _god_apply_bargain_primitive(self, primitive):
        if not isinstance(primitive, dict):
            return None
        kind = primitive.get("kind")
        payload = primitive.get("payload") or {}
        if kind == "grant_resource":
            return self._god_apply_grant_resource(payload)
        if kind == "agent_vitals":
            return self._god_apply_agent_vitals(payload)
        return None

    def _settle_bargain(self, key, bush, outcome, trigger):
        """Settle an open bargain. Lock held. outcome: success|failure."""
        bargain = bush.get("bargain")
        if not isinstance(bargain, dict) or bargain.get("status") != "open":
            return None
        bargain["status"] = outcome
        bargain["settledFrame"] = self.frameTick
        bargain["settleTrigger"] = trigger
        primitive = (bargain.get("rewardPrimitive") if outcome == "success"
                       else bargain.get("punishPrimitive"))
        prim_outcome = self._god_apply_bargain_primitive(primitive)
        target_id = bush.get("targetId")
        record_public = isinstance(primitive, dict) and primitive.get("kind") == "grant_resource"
        settle_id = bargain.get("id") or self._next_intervention_id()
        self._god_record_intervention({
            "id": settle_id, "kind": "bargain_settle",
            "frameTick": self.frameTick, "targetId": target_id,
            "outcome": outcome, "trigger": trigger,
            "primitiveKind": primitive.get("kind") if isinstance(primitive, dict) else None,
            "status": "applied", "public": record_public,
        })
        self._log_divine(settle_id, None, "bargain_settle",
                         {"targetId": target_id, "outcome": outcome, "trigger": trigger},
                         {"outcome": outcome, "primitiveOutcome": prim_outcome},
                         "applied", public=record_public)
        return {
            "outcome": outcome,
            "trigger": trigger,
            "primitiveOutcome": prim_outcome,
        }

    def _god_apply_bargain_settle(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        god = self.civilization["godState"]
        bush = (god.get("burningBush") or {}).get(key)
        if not isinstance(bush, dict):
            return None, "no burning bush session for target agent"
        outcome = payload["outcome"]
        settled = self._settle_bargain(key, bush, outcome, "manual")
        if settled is None:
            return None, "no open bargain for target agent"
        return settled, None

    def _god_current_outgoing_anointment_id(self, target_id):
        rec = (self.civilization.get("godState") or {}).get("anointments", {}).get(str(target_id))
        return rec.get("id") if isinstance(rec, dict) else None

    def _close_anointment(self, key, status):
        """Clear one anointment record exactly once. Lock held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        anointments = god.get("anointments")
        rec = anointments.get(key) if isinstance(anointments, dict) else None
        if not isinstance(rec, dict):
            return None
        if isinstance(anointments, dict):
            anointments.pop(key, None)
        self._log_divine(rec.get("id"), None, "anoint", rec,
                         {"status": status}, status, public=False)
        return rec

    def _god_apply_anoint(self, payload):
        """Mark one agent anointed — destiny/oracle private, stigmata in prompts."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        key = str(target_id)
        outgoing = (self._close_anointment(key, "replaced")
                    if key in god.get("anointments", {}) else None)
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "destinyText": payload["destinyText"],
            "stigmataTags": list(payload.get("stigmataTags") or []),
            "oracleHints": list(payload.get("oracleHints") or []),
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        god.setdefault("anointments", {})[key] = record
        self._god_record_intervention({
            "id": intervention_id, "kind": "anoint", "frameTick": self.frameTick,
            "targetId": target_id, "tagCount": len(record["stigmataTags"]),
            "oracleHintCount": len(record["oracleHints"]),
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        self._log_divine(intervention_id, None, "anoint",
                         {"targetId": target_id,
                          "destinyText": payload["destinyText"],
                          "stigmataTags": record["stigmataTags"],
                          "oracleHints": record["oracleHints"]},
                         {"targetId": target_id, "tagCount": len(record["stigmataTags"]),
                          "oracleHintCount": len(record["oracleHints"])},
                         "applied", public=False)
        return {
            "interventionId": intervention_id,
            "kind": "anoint",
            "targetId": target_id,
            "tagCount": len(record["stigmataTags"]),
            "oracleHintCount": len(record["oracleHints"]),
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_revoke_anoint(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        closed = self._close_anointment(key, "revoked")
        if closed is None:
            return None, "no active anointment for target agent"
        return {
            "interventionId": closed.get("id"),
            "kind": "revoke_anoint",
            "targetId": target_id,
        }, None

    def _god_apply_identity_values(self, agent, *, persona=None, personality=None, role=None):
        """Apply one or more identity fields on a living agent. Lock held."""
        if persona is not None:
            agent["persona"] = persona
        if personality is not None:
            agent["personality"] = personality
        if role is not None:
            agent["role"] = role
        self._mark_context_dirty(agent)

    def _god_apply_identity_edit(self, payload):
        """Mutate persona/personality/role with forge snapshot for restore."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        agent = self._find_agent_by_id(target_id)
        key = str(target_id)
        outgoing = (self._close_identity_forge(key, "replaced")
                    if key in god.get("identityForges", {}) else None)
        snapshot = self._identity_forge_snapshot(agent)
        self._god_apply_identity_values(
            agent,
            persona=payload.get("persona"),
            personality=payload.get("personality"),
            role=payload.get("role"),
        )
        intervention_id = self._next_intervention_id()
        expires_frame = None
        if payload.get("durationFrames") is not None:
            expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "snapshot": snapshot,
            "baseline": dict(snapshot),
            "progress": 1.0,
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        god.setdefault("identityForges", {})[key] = record
        self._god_record_intervention({
            "id": intervention_id, "kind": "identity_edit", "frameTick": self.frameTick,
            "targetId": target_id, "fields": [k for k in ("persona", "personality", "role")
                                             if k in payload],
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        self._log_divine(intervention_id, None, "identity_edit", payload,
                         {"targetId": target_id,
                          "fields": [k for k in ("persona", "personality", "role") if k in payload]},
                         "applied", public=False)
        return {
            "interventionId": intervention_id,
            "kind": "identity_edit",
            "targetId": target_id,
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_identity_copy_overwrite(self, payload):
        """Start gradual persona/personality copy from source to target."""
        god = self.civilization["godState"]
        target_id = payload["targetId"]
        source_id = payload["sourceId"]
        target = self._find_agent_by_id(target_id)
        key = str(target_id)
        outgoing = (self._close_identity_forge(key, "replaced")
                    if key in god.get("identityForges", {}) else None)
        snapshot = self._identity_forge_snapshot(target)
        intervention_id = self._next_intervention_id()
        expires_frame = None
        if payload.get("durationFrames") is not None:
            expires_frame = self.frameTick + payload["durationFrames"]
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "copyFromId": source_id,
            "rate": payload["ratePerThink"],
            "progress": 0.0,
            "snapshot": snapshot,
            "baseline": dict(snapshot),
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
        }
        god.setdefault("identityForges", {})[key] = record
        memories_planted = 0
        if payload.get("syncMemories"):
            source = self._find_agent_by_id(source_id)
            if source is not None:
                memories_planted = self._god_plant_copy_memories(target, source)
        self._advance_identity_forge_on_think(target)
        self._god_record_intervention({
            "id": intervention_id, "kind": "identity_copy_overwrite",
            "frameTick": self.frameTick, "targetId": target_id, "sourceId": source_id,
            "ratePerThink": payload["ratePerThink"], "memoriesPlanted": memories_planted,
            "expiresFrame": expires_frame, "status": "applied", "public": False,
        })
        self._log_divine(intervention_id, None, "identity_copy_overwrite", payload,
                         {"targetId": target_id, "sourceId": source_id,
                          "memoriesPlanted": memories_planted},
                         "applied", public=False)
        return {
            "interventionId": intervention_id,
            "kind": "identity_copy_overwrite",
            "targetId": target_id,
            "sourceId": source_id,
            "progress": record.get("progress"),
            "memoriesPlanted": memories_planted,
            "expiresFrame": expires_frame,
            "outgoingId": outgoing.get("id") if isinstance(outgoing, dict) else None,
        }

    def _god_apply_identity_forge_cancel(self, payload):
        target_id = payload["targetId"]
        key = str(target_id)
        closed = self._close_identity_forge(key, "cancelled")
        if closed is None:
            return None, "no active identity forge for target agent"
        return {
            "interventionId": closed.get("id"),
            "kind": "identity_forge_cancel",
            "targetId": target_id,
        }, None

