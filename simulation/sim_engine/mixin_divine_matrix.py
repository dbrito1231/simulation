"""Phase 6d mixin: Divine Matrix slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_current_directive` through
`_anointment_prompt_line` (formerly core.py lines ~1479-2633). Covers: the
leader-directive helper, Divine Voice guidance-window/adherence tracking,
Burning Bush conversational threads, Anointment (stigmata/oracle hints),
Identity Forge basics (snapshot/restore/blend/advance/close), Divine Matrix
Phase 9 Architect Zones (paint/door/limbo mechanics), and Divine Matrix
Phase 10 checkpoint helpers (create/restore/file-copy machinery).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _DivineMatrixMixin:
    """Mixin slice of SimEngine: Divine Voice guidance, Burning Bush,
    Anointment, Identity Forge, Architect Zones, and checkpoints. See module
    docstring for exact scope."""

    def _current_directive(self):
        """The leader directive, or None once it has aged past its TTL
        (covers stale directives restored from state.db too)."""
        c = self.civilization
        if not c["directive"]:
            return None
        if self.frameTick - c.get("directiveFrame", 0) > DIRECTIVE_TTL_FRAMES:
            return None
        return c["directive"]

    def _divine_prompt_lines(self, agent):
        """Sovereign God mode (Phase 3): the at-most-two divine cognition
        lines for one agent's think payload -- (public_line, private_line),
        either or both None. Only active, unacknowledged binding Voice
        guidance is injected; Matrix soft lines use separate helpers.
        Deliberately SEPARATE from _current_directive: elder leadership and
        divine providence are different fields that must never overwrite or
        shadow each other."""
        if not GOD_MODE_ENABLED:
            return None, None
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None, None
        agent_key = str(agent["id"])
        public_line = None
        prov = god.get("providence")
        if isinstance(prov, dict) and self._voice_guidance_in_window(prov):
            acked = prov.get("ackedAgentIds") or {}
            if not acked.get(agent_key):
                public_line = prov.get("text")
        private_line = None
        omen = (god.get("privateOmens") or {}).get(agent_key)
        if isinstance(omen, dict) and self._voice_guidance_in_window(omen):
            if not omen.get("acked"):
                private_line = omen.get("text")
        return public_line, private_line

    def _cancel_voice_blocked_special_turns(self, agent_ids):
        """Drop pending invention/sprite special turns for Voice-affected agents."""
        for aid in agent_ids:
            agent = aid if isinstance(aid, dict) else self._find_agent_by_id(aid)
            if not agent:
                continue
            agent["inventionTurn"] = False
            agent["inventionRetryUsed"] = False
            agent["inventionBuildContext"] = None
            agent["spriteDesignTurn"] = None

    def _voice_guidance_in_window(self, record):
        if not isinstance(record, dict):
            return False
        start = record.get("createdFrame", 0)
        expires = record.get("expiresFrame")
        return isinstance(expires, int) and start <= self.frameTick < expires

    def _active_voice_guidance(self, agent):
        """Active, unacknowledged binding Voice guidance for one agent."""
        result = {
            "voice_guidance_active": False,
            "voice_guidance_id": None,
            "voice_guidance_text": None,
            "voice_guidance_public": False,
            "voice_guidance_private": False,
            "unacked_guidance": [],
        }
        if not GOD_MODE_ENABLED:
            return result
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return result
        agent_key = str(agent["id"])
        unacked = []
        prov = god.get("providence")
        if isinstance(prov, dict) and self._voice_guidance_in_window(prov):
            acked = prov.get("ackedAgentIds") or {}
            if not acked.get(agent_key):
                unacked.append({
                    "id": prov.get("id"),
                    "kind": "providence",
                    "public": True,
                    "text": prov.get("text"),
                })
        omen = (god.get("privateOmens") or {}).get(agent_key)
        if isinstance(omen, dict) and self._voice_guidance_in_window(omen):
            if not omen.get("acked"):
                unacked.append({
                    "id": omen.get("id"),
                    "kind": "private_omen",
                    "public": False,
                    "text": omen.get("text"),
                })
        if not unacked:
            return result
        result["voice_guidance_active"] = True
        result["unacked_guidance"] = unacked
        primary = next((g for g in unacked if g["kind"] == "private_omen"), unacked[0])
        result["voice_guidance_id"] = primary.get("id")
        result["voice_guidance_text"] = primary.get("text")
        result["voice_guidance_public"] = any(g["public"] for g in unacked)
        result["voice_guidance_private"] = any(not g["public"] for g in unacked)
        return result

    def _mark_voice_guidance_acked(self, agent, guidance_entries):
        """Record first response for each (agentId, guidanceId) pair."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return
        agent_key = str(agent["id"])
        for entry in guidance_entries:
            gid = entry.get("id")
            kind = entry.get("kind")
            if kind == "providence":
                prov = god.get("providence")
                if isinstance(prov, dict) and prov.get("id") == gid:
                    acked = prov.setdefault("ackedAgentIds", {})
                    acked[agent_key] = True
            elif kind == "private_omen":
                omen = (god.get("privateOmens") or {}).get(agent_key)
                if isinstance(omen, dict) and omen.get("id") == gid:
                    omen["acked"] = True

    def _bump_voice_guidance_skip(self, entry, agent_key):
        """Increment and return the consecutive-synthetic-response counter for
        one guidance entry (providence's skipCounts[agent_key], or the
        per-agent omen's skipCount), reading/writing directly against the
        live godState record so it persists like ackedAgentIds does. Returns
        None if the underlying record can no longer be found (e.g. guidance
        expired between _active_voice_guidance() and here).

        Both the container and the counter are coerced defensively: this runs
        under the engine lock on every synthetic divine_response, and a
        restored/hand-edited state.db carrying a non-dict skipCounts or a
        null/non-numeric counter must not raise here and break the tick loop.
        A malformed value is treated as "no skips counted yet" (0)."""
        def _as_count(value):
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        gid = entry.get("id")
        kind = entry.get("kind")
        if kind == "providence":
            prov = god.get("providence")
            if isinstance(prov, dict) and prov.get("id") == gid:
                skip_counts = prov.get("skipCounts")
                if not isinstance(skip_counts, dict):
                    skip_counts = {}
                    prov["skipCounts"] = skip_counts
                skip_counts[agent_key] = _as_count(skip_counts.get(agent_key)) + 1
                return skip_counts[agent_key]
        elif kind == "private_omen":
            omens = god.get("privateOmens")
            omen = omens.get(agent_key) if isinstance(omens, dict) else None
            if isinstance(omen, dict) and omen.get("id") == gid:
                omen["skipCount"] = _as_count(omen.get("skipCount")) + 1
                return omen["skipCount"]
        return None

    def _record_divine_response_adherence(self, agent, decision, voice):
        """Log Voice adherence after a decision is applied. Lock held.

        A genuine (non-synthetic) divine_response acks its guidance entry
        immediately, same as before. A synthetic one (model omitted/malformed
        the field) no longer auto-acks -- it increments a per-guidance skip
        counter instead, so the same binding prompt line keeps reappearing on
        the agent's next think. Only once that counter reaches
        GOD_VOICE_ACK_SKIP_CAP consecutive synthetic turns is the entry
        force-acked (capped close), so a model that never cooperates can't
        stall the guidance forever."""
        if not voice.get("voice_guidance_active"):
            return
        unacked = list(voice.get("unacked_guidance") or [])
        if not unacked:
            return
        divine_response = decision.get("divine_response")
        if not isinstance(divine_response, dict):
            divine_response = {
                "stance": "continue",
                "reason": "missing_divine_response",
            }
            synthetic = True
        else:
            synthetic = bool(decision.get("divine_response_synthetic"))
        stance = divine_response.get("stance")
        if stance not in ("follow", "continue"):
            stance = "continue"
            synthetic = True
        reason = divine_response.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = "missing_divine_response"
            synthetic = True
        else:
            reason = reason.strip()[:GOD_TEXT_MAX_CHARS]
        action = decision.get("action") or agent.get("lastAction") or "rest"
        reaction = decision.get("message")
        if not isinstance(reaction, str) or not reaction.strip():
            reaction = reason
        else:
            reaction = reaction.strip()[:GOD_TEXT_MAX_CHARS]
        god = self.civilization["godState"]
        log_ring = god.setdefault("recentDivineResponses", [])
        if not isinstance(log_ring, list):
            log_ring = []
            god["recentDivineResponses"] = log_ring
        agent_key = str(agent["id"])
        to_ack = []
        for entry in unacked:
            skip_count = None
            capped = False
            if not synthetic:
                to_ack.append(entry)
            else:
                skip_count = self._bump_voice_guidance_skip(entry, agent_key)
                if skip_count is None or skip_count >= GOD_VOICE_ACK_SKIP_CAP:
                    capped = True
                    to_ack.append(entry)
            record = {
                "agentId": agent["id"],
                "agentName": agent["name"],
                "guidanceId": entry.get("id"),
                "guidanceKind": entry.get("kind"),
                "stance": stance,
                "reason": reason,
                "synthetic": synthetic,
                "frameTick": self.frameTick,
                "action": action,
                "public": bool(entry.get("public")),
                "skipCount": skip_count,
                "capped": capped,
            }
            log_ring.insert(0, record)
            kind_label = "public guidance" if entry.get("public") else "private guidance"
            self._push_activity(
                f'{agent["name"]} {stance}d divine {kind_label}: {reaction}')
            self._push_communication(
                "divine_response", agent["name"], "divine",
                f"{stance}: {reason}", outcome=action, source="divine")
            self._log_divine(
                entry.get("id"), None, "voice_adherence",
                {"agentId": agent["id"], "guidanceKind": entry.get("kind")},
                {
                    "stance": stance,
                    "reason": reason,
                    "action": action,
                    "synthetic": synthetic,
                    "agentName": agent["name"],
                    "skipCount": skip_count,
                    "capped": capped,
                },
                "adherence",
                public=bool(entry.get("public")),
            )
        if len(log_ring) > GOD_DIVINE_RESPONSE_LOG_MAX:
            del log_ring[GOD_DIVINE_RESPONSE_LOG_MAX:]
        if to_ack:
            self._mark_voice_guidance_acked(agent, to_ack)

    def _active_burning_bush_record(self, agent_id):
        """Active Burning Bush session for one agent, or None."""
        if not GOD_MODE_ENABLED:
            return None
        god = self.civilization.get("godState") or {}
        rec = (god.get("burningBush") or {}).get(str(agent_id))
        if not isinstance(rec, dict) or rec.get("status") != "active":
            return None
        return rec

    def _burning_bush_prompt_line(self, agent):
        """Private Divine-audience thread for one agent's think payload."""
        rec = self._active_burning_bush_record(agent["id"])
        if not rec:
            return None
        parts = []
        thread = rec.get("thread") or []
        if isinstance(thread, list):
            for entry in thread[-GOD_BURNING_BUSH_THREAD_MAX:]:
                if not isinstance(entry, dict):
                    continue
                text = entry.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                role = entry.get("role")
                if role == "god":
                    parts.append(f"Divine audience: {text}")
                elif role == "agent":
                    parts.append(f"You replied: {text}")
        bargain = rec.get("bargain")
        if isinstance(bargain, dict) and bargain.get("status") == "open":
            terms = bargain.get("termsText")
            if isinstance(terms, str) and terms.strip():
                parts.append(f"Active bargain: {terms}")
        if not parts:
            return None
        combined = " ".join(parts)
        if len(combined) > GOD_BURNING_BUSH_PROMPT_MAX_CHARS:
            combined = combined[-GOD_BURNING_BUSH_PROMPT_MAX_CHARS:]
        return combined

    def _capture_burning_bush_reply(self, agent, decision):
        """Append agent talk/reasoning to a private Burning Bush thread."""
        rec = self._active_burning_bush_record(agent["id"])
        if not rec:
            return
        action = decision.get("action")
        raw = None
        if action == "talk_to_nearby" and decision.get("message"):
            raw = decision["message"]
        elif decision.get("reasoning"):
            raw = decision["reasoning"]
        if not isinstance(raw, str) or not raw.strip():
            return
        text, reason = self._normalize_divine_text(raw)
        if reason:
            text = raw.strip()[:GOD_TEXT_MAX_CHARS]
        thread = rec.setdefault("thread", [])
        if not isinstance(thread, list):
            rec["thread"] = thread = []
        thread.append({
            "role": "agent", "text": text, "frame": self.frameTick,
        })
        if len(thread) > GOD_BURNING_BUSH_THREAD_MAX:
            del thread[:-GOD_BURNING_BUSH_THREAD_MAX]

    def _god_active_anointment_record(self, agent_id):
        """Active anointment for one agent, or None if absent/expired."""
        if not GOD_MODE_ENABLED:
            return None
        god = self.civilization.get("godState") or {}
        rec = (god.get("anointments") or {}).get(str(agent_id))
        if not isinstance(rec, dict):
            return None
        expires = rec.get("expiresFrame")
        if isinstance(expires, int) and self.frameTick >= expires:
            return None
        agent = self._find_agent_by_id(agent_id)
        if agent is None or agent.get("deathFrame") is not None:
            return None
        return rec

    def _anointment_stigmata_tags(self, agent_id):
        """Public-facing stigmata tags for neighbors (never includes destiny)."""
        rec = self._god_active_anointment_record(agent_id)
        if not rec:
            return []
        tags = rec.get("stigmataTags") or []
        if not isinstance(tags, list):
            return []
        out = []
        for tag in tags[:GOD_ANOINT_STIGMATA_MAX]:
            if isinstance(tag, str) and tag.strip():
                out.append(tag.strip())
        return out

    def _anointment_next_oracle_frame(self, rec):
        """Earliest unrevealed oracle revealFrame, or None."""
        hints = rec.get("oracleHints") or []
        if not isinstance(hints, list):
            return None
        ft = self.frameTick
        pending = []
        for hint in hints:
            if not isinstance(hint, dict):
                continue
            reveal = hint.get("revealFrame")
            text = hint.get("text")
            if not isinstance(reveal, int) or reveal <= ft:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            pending.append(reveal)
        return min(pending) if pending else None

    def _god_active_identity_forge_record(self, agent_id):
        """Active identity-forge record for one agent, or None if absent/expired."""
        god = self.civilization.get("godState") or {}
        rec = (god.get("identityForges") or {}).get(str(agent_id))
        if not isinstance(rec, dict):
            return None
        expires = rec.get("expiresFrame")
        if isinstance(expires, int) and self.frameTick >= expires:
            return None
        return rec

    def _identity_forge_snapshot(self, agent):
        """Capture persona/personality/role for forge restore."""
        return {
            "persona": agent.get("persona") or "",
            "personality": agent.get("personality") or "",
            "role": agent.get("role") or "",
        }

    def _restore_identity_forge_snapshot(self, agent, snapshot):
        """Restore one agent's identity fields from a forge snapshot. Lock held."""
        if not isinstance(snapshot, dict):
            return
        agent["persona"] = snapshot.get("persona") or ""
        agent["personality"] = snapshot.get("personality") or ""
        role = snapshot.get("role")
        if isinstance(role, str) and role.strip():
            agent["role"] = role.strip()
        self._mark_context_dirty(agent)

    def _blend_identity_field(self, baseline, target, progress):
        """Blend two identity strings toward target by progress in [0, 1]."""
        progress = max(0.0, min(1.0, float(progress)))
        baseline = baseline or ""
        target = target or ""
        if progress <= 0.0:
            return baseline
        if progress >= 1.0:
            return target
        if baseline == target:
            return baseline
        base_chars = max(0, int(len(baseline) * (1.0 - progress)))
        tgt_chars = max(0, int(len(target) * progress))
        blended = (baseline[:base_chars] + target[:tgt_chars]).strip()
        return blended or baseline or target

    def _god_role_valid(self, role):
        return isinstance(role, str) and role.strip() in self.d.get("ROLES", {})

    def _god_elder_swap_warning(self, agent, new_role):
        """Warn in preview when an identity edit would swap elder role."""
        if not isinstance(new_role, str) or not new_role.strip():
            return None
        new_role = new_role.strip()
        old_role = agent.get("role")
        if old_role == new_role:
            return None
        if old_role == "elder" or new_role == "elder":
            return (f"Elder role swap: {old_role} -> {new_role}. "
                    "May affect council, invention, and emergency systems.")
        return None

    def _god_plant_copy_memories(self, target, source):
        """Optional one-shot memory sync for identity copy (not a full clone)."""
        mem = source.get("memory") or {}
        lines = []
        for tier in ("working", "shortTerm"):
            for line in reversed(mem.get(tier) or []):
                if isinstance(line, str) and line.strip():
                    lines.append(line.strip())
                if len(lines) >= GOD_IDENTITY_COPY_MEMORIES_MAX:
                    break
            if len(lines) >= GOD_IDENTITY_COPY_MEMORIES_MAX:
                break
        planted = 0
        for line in lines[:GOD_IDENTITY_COPY_MEMORIES_MAX]:
            if self._god_memory_insert(target, line, 0.6, kind=GOD_MEMORY_DEFAULT_KIND):
                planted += 1
        return planted

    def _advance_identity_forge_on_think(self, agent):
        """Advance identity-copy blend after one think completes. Lock held."""
        rec = self._god_active_identity_forge_record(agent["id"])
        if not rec or not rec.get("copyFromId"):
            return
        source = self._find_agent_by_id(rec["copyFromId"])
        if source is None or source.get("deathFrame") is not None:
            return
        baseline = rec.get("baseline") or rec.get("snapshot") or {}
        rate = float(rec.get("rate") or 0.0)
        progress = float(rec.get("progress") or 0.0)
        new_progress = min(1.0, progress + rate)
        rec["progress"] = new_progress
        agent["persona"] = self._blend_identity_field(
            baseline.get("persona") or "",
            source.get("persona") or "",
            new_progress,
        )[:GOD_IDENTITY_PERSONA_MAX_CHARS]
        agent["personality"] = self._blend_identity_field(
            baseline.get("personality") or "",
            source.get("personality") or "",
            new_progress,
        )[:GOD_IDENTITY_PERSONALITY_MAX_CHARS]
        self._mark_context_dirty(agent)

    def _close_identity_forge(self, key, status):
        """Restore snapshot and remove one identity-forge record. Lock held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        forges = god.get("identityForges")
        if not isinstance(forges, dict):
            return None
        rec = forges.pop(key, None)
        if not isinstance(rec, dict):
            return None
        agent = self._find_agent_by_id(int(key))
        if agent is not None:
            self._restore_identity_forge_snapshot(agent, rec.get("snapshot"))
        kind = "identity_copy_overwrite" if rec.get("copyFromId") else "identity_edit"
        self._log_divine(rec.get("id"), None, kind, rec,
                         {"status": status}, status, public=False)
        return rec

    def _god_current_outgoing_identity_forge_id(self, target_id):
        rec = (self.civilization.get("godState") or {}).get("identityForges", {}).get(
            str(target_id))
        return rec.get("id") if isinstance(rec, dict) else None

    # --- Divine Matrix Phase 9: Architect Zones ---
    def _god_active_architect_zones(self):
        zones = (self.civilization.get("godState") or {}).get("architectZones") or []
        return [z for z in zones if isinstance(z, dict) and z.get("status") == "active"]

    def _god_architect_zone_by_id(self, zone_id):
        if not isinstance(zone_id, str) or not zone_id.strip():
            return None
        zone_id = zone_id.strip()
        for zone in self._god_active_architect_zones():
            if zone.get("id") == zone_id:
                return zone
        return None

    def _expand_architect_cells(self, cells):
        """Expand cells list into a bounded set of (gx, gy) grid coords."""
        if not isinstance(cells, list) or not cells:
            return None, "cells must be a non-empty list"
        out = set()
        for entry in cells:
            if isinstance(entry, str):
                parts = entry.split(",")
                if len(parts) != 2:
                    return None, "each cell string must be gx,gy"
                try:
                    gx, gy = int(parts[0].strip()), int(parts[1].strip())
                except ValueError:
                    return None, "cell coordinates must be integers"
            elif isinstance(entry, dict):
                if all(k in entry for k in ("gx1", "gy1", "gx2", "gy2")):
                    try:
                        gx1, gy1 = int(entry["gx1"]), int(entry["gy1"])
                        gx2, gy2 = int(entry["gx2"]), int(entry["gy2"])
                    except (TypeError, ValueError):
                        return None, "bounds coordinates must be integers"
                    lo_gx, hi_gx = sorted((gx1, gx2))
                    lo_gy, hi_gy = sorted((gy1, gy2))
                    for gx in range(lo_gx, hi_gx + 1):
                        for gy in range(lo_gy, hi_gy + 1):
                            if len(out) >= GOD_ARCHITECT_ZONE_MAX_CELLS:
                                return None, f"cells exceed cap of {GOD_ARCHITECT_ZONE_MAX_CELLS}"
                            out.add((gx, gy))
                    continue
                return None, "each bounds object must include gx1, gy1, gx2, gy2"
            else:
                return None, "cells entries must be gx,gy strings or bounds objects"
            if not (0 <= gx < PATH1_GRID_COLS and 0 <= gy < PATH1_GRID_ROWS):
                return None, f"cell ({gx},{gy}) out of grid bounds"
            if len(out) >= GOD_ARCHITECT_ZONE_MAX_CELLS:
                return None, f"cells exceed cap of {GOD_ARCHITECT_ZONE_MAX_CELLS}"
            out.add((gx, gy))
        if not out:
            return None, "cells must resolve to at least one grid cell"
        return sorted(out), None

    def _architect_zone_covers_cell(self, zone, district_id, gx, gy):
        if zone.get("districtId") != district_id:
            return False
        cells = zone.get("cells")
        expanded, _ = self._expand_architect_cells(cells)
        if not expanded:
            return False
        return (gx, gy) in expanded

    def _world_pos_to_district_grid(self, x, y):
        did = get_district(self.civilization["districts"], x, y)
        if not did:
            return None, None, None
        d = self.civilization["districts"][did]
        b = d["bounds"]
        gx = int((x - b["x1"]) // TILE_CELL)
        gy = int((y - b["y1"]) // TILE_CELL)
        gx = max(0, min(PATH1_GRID_COLS - 1, gx))
        gy = max(0, min(PATH1_GRID_ROWS - 1, gy))
        return did, gx, gy

    def _agent_has_god_key(self, agent, key_id):
        keys = agent.get("godKeys") or set()
        return isinstance(key_id, str) and key_id in keys

    def _grant_god_key(self, agent, key_id):
        keys = agent.get("godKeys")
        if not isinstance(keys, set):
            keys = set(keys or ())
            agent["godKeys"] = keys
        keys.add(key_id)

    def _architect_door_blocks_move(self, agent, prior_x, prior_y, new_x, new_y):
        if not GOD_MODE_ENABLED:
            return False
        new_did, new_gx, new_gy = self._world_pos_to_district_grid(new_x, new_y)
        if not new_did:
            return False
        prior_did, prior_gx, prior_gy = self._world_pos_to_district_grid(prior_x, prior_y)
        if (new_did, new_gx, new_gy) == (prior_did, prior_gx, prior_gy):
            return False
        for zone in self._god_active_architect_zones():
            if zone.get("kind") != "door":
                continue
            if not self._architect_zone_covers_cell(zone, new_did, new_gx, new_gy):
                continue
            key_id = zone.get("keyId")
            if isinstance(key_id, str) and self._agent_has_god_key(agent, key_id):
                continue
            return True
        return False

    def _architect_paint_snapshot(self, district_id, cells, paint_terrain):
        district = self.civilization["districts"].get(district_id)
        if not district:
            return {}
        self._ensure_district_terrain(district)
        terrain = district["terrain"]
        snap = {}
        for gx, gy in cells:
            key = self._tile_key(gx, gy)
            snap[key] = terrain.get(key, "soil")
            terrain[key] = paint_terrain
        if snap:
            self._bump_districts_epoch()
        return snap

    def _architect_revert_paint(self, zone):
        snap = zone.get("revertSnapshot")
        district_id = zone.get("districtId")
        if not zone.get("reversible", True) or not isinstance(snap, dict) or not district_id:
            return
        district = self.civilization["districts"].get(district_id)
        if not district:
            return
        self._ensure_district_terrain(district)
        terrain = district["terrain"]
        for key, value in snap.items():
            if isinstance(key, str) and isinstance(value, str):
                terrain[key] = value
        self._bump_districts_epoch()

    def _park_agent_in_limbo(self, agent, zone):
        limbo_x, limbo_y = GOD_LIMBO_STATION
        hold = {
            "zoneId": zone.get("id"),
            "priorX": agent["x"],
            "priorY": agent["y"],
            "priorTargetX": agent["targetX"],
            "priorTargetY": agent["targetY"],
            "priorDistrict": agent.get("currentDistrict"),
        }
        agent["architectLimbo"] = hold
        agent["divineHold"] = True
        agent["x"] = limbo_x
        agent["y"] = limbo_y
        agent["targetX"] = limbo_x
        agent["targetY"] = limbo_y
        agent["waypoints"] = []
        agent["currentZone"] = get_zone(self.civilization["districts"], limbo_x, limbo_y)
        agent["currentDistrict"] = get_district(self.civilization["districts"], limbo_x, limbo_y)
        zone.setdefault("limboHolds", {})[str(agent["id"])] = hold

    def _release_architect_limbo_agent(self, agent, zone=None):
        hold = agent.get("architectLimbo")
        if not isinstance(hold, dict):
            return False
        if zone is not None and hold.get("zoneId") != zone.get("id"):
            return False
        agent["x"] = hold.get("priorX", agent["x"])
        agent["y"] = hold.get("priorY", agent["y"])
        agent["targetX"] = hold.get("priorTargetX", agent["x"])
        agent["targetY"] = hold.get("priorTargetY", agent["y"])
        agent["architectLimbo"] = None
        if zone is not None:
            holds = zone.get("limboHolds")
            if isinstance(holds, dict):
                holds.pop(str(agent["id"]), None)
        if self._god_active_decision_gate_record(agent["id"]) is None:
            agent["divineHold"] = False
        agent["currentZone"] = get_zone(self.civilization["districts"], agent["x"], agent["y"])
        agent["currentDistrict"] = get_district(self.civilization["districts"], agent["x"], agent["y"])
        return True

    def _release_architect_limbo_zone(self, zone, agent_ids=None):
        released = []
        hold_ids = zone.get("holdAgentIds") or []
        if agent_ids is not None:
            hold_ids = [aid for aid in hold_ids if aid in agent_ids]
        for agent_id in hold_ids:
            agent = self._find_agent_by_id(agent_id)
            if agent is None:
                continue
            if self._release_architect_limbo_agent(agent, zone):
                released.append(agent_id)
        return released

    def _close_architect_zone(self, zone_id, status):
        """Close one architect zone exactly once. Lock held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        zones = god.get("architectZones")
        if not isinstance(zones, list):
            return None
        closed = None
        for idx, zone in enumerate(zones):
            if not isinstance(zone, dict) or zone.get("id") != zone_id:
                continue
            if zone.get("status") != "active":
                return None
            zone = dict(zone)
            zone["status"] = status
            if zone.get("kind") == "paint":
                self._architect_revert_paint(zone)
            elif zone.get("kind") == "limbo":
                self._release_architect_limbo_zone(zone)
            zones[idx] = zone
            closed = zone
            break
        if closed is None:
            return None
        public = closed.get("kind") == "paint"
        self._log_divine(closed.get("id"), None, "architect_zone", closed,
                         {"status": status, "zoneKind": closed.get("kind")},
                         status, public=public)
        return closed

    def _validate_god_architect_zone(self, payload):
        zone_kind = payload.get("zoneKind")
        if zone_kind not in ("paint", "door", "limbo"):
            return None, 'zoneKind must be "paint", "door", or "limbo"'
        district_id = payload.get("districtId")
        if zone_kind in ("paint", "door"):
            if not isinstance(district_id, str) or not district_id.strip():
                return None, "districtId is required for paint and door zones"
            district_id = district_id.strip()
            if district_id not in self.civilization.get("districts", {}):
                return None, "unknown district id"
        else:
            district_id = district_id.strip() if isinstance(district_id, str) and district_id.strip() else None
        cells_raw = payload.get("cells")
        cells_expanded, reason = self._expand_architect_cells(cells_raw)
        if reason:
            return None, reason
        duration = payload.get("durationFrames")
        if duration is None:
            duration = GOD_GUIDANCE_DEFAULT_DURATION_FRAMES
        elif isinstance(duration, bool) or not isinstance(duration, int):
            return None, "durationFrames must be an integer"
        elif not (GOD_GUIDANCE_MIN_DURATION_FRAMES <= duration <= GOD_GUIDANCE_MAX_DURATION_FRAMES):
            return None, (
                f"durationFrames must be between {GOD_GUIDANCE_MIN_DURATION_FRAMES} "
                f"and {GOD_GUIDANCE_MAX_DURATION_FRAMES}")
        reversible_raw = payload.get("reversible")
        if reversible_raw is None:
            reversible = True
        elif not isinstance(reversible_raw, bool):
            return None, "reversible must be a boolean"
        else:
            reversible = reversible_raw
        normalized = {
            "zoneKind": zone_kind,
            "cells": cells_raw,
            "cellsExpanded": cells_expanded,
            "durationFrames": duration,
            "reversible": reversible,
        }
        if district_id is not None:
            normalized["districtId"] = district_id
        if zone_kind == "paint":
            if not PATH1_ENABLED:
                return None, "terrain tiles disabled"
            paint_terrain = payload.get("paintTerrain")
            if not isinstance(paint_terrain, str) or paint_terrain not in GOD_ARCHITECT_PAINT_TERRAINS:
                allowed = ", ".join(sorted(GOD_ARCHITECT_PAINT_TERRAINS))
                return None, f"paintTerrain must be one of: {allowed}"
            normalized["paintTerrain"] = paint_terrain
        if zone_kind == "door":
            key_id = payload.get("keyId")
            if not isinstance(key_id, str) or not key_id.strip():
                return None, "keyId is required for door zones"
            key_id = key_id.strip()
            if len(key_id) > GOD_ARCHITECT_KEY_MAX_LEN:
                return None, f"keyId exceeds {GOD_ARCHITECT_KEY_MAX_LEN} characters"
            normalized["keyId"] = key_id
            grant_ids = []
            for raw_id in payload.get("grantKeyAgentIds") or []:
                if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                    return None, "grantKeyAgentIds entries must be integer agent ids"
                agent = self._find_agent_by_id(raw_id)
                if agent is None:
                    return None, f"unknown grantKeyAgentIds agent {raw_id}"
                if agent.get("deathFrame") is not None:
                    return None, f"grantKeyAgentIds agent {raw_id} is deceased"
                grant_ids.append(raw_id)
            if grant_ids:
                normalized["grantKeyAgentIds"] = grant_ids
        if zone_kind == "limbo":
            hold_ids = []
            for raw_id in payload.get("holdAgentIds") or []:
                if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                    return None, "holdAgentIds entries must be integer agent ids"
                agent = self._find_agent_by_id(raw_id)
                if agent is None:
                    return None, f"unknown holdAgentIds agent {raw_id}"
                if agent.get("deathFrame") is not None:
                    return None, f"holdAgentIds agent {raw_id} is deceased"
                hold_ids.append(raw_id)
            if not hold_ids:
                return None, "holdAgentIds must include at least one living agent"
            normalized["holdAgentIds"] = hold_ids
        active = self._god_active_architect_zones()
        if len(active) >= GOD_ARCHITECT_ZONES_MAX:
            return None, f"active architect zone cap of {GOD_ARCHITECT_ZONES_MAX} reached"
        return normalized, None

    def _god_apply_architect_zone(self, payload):
        intervention_id = self._next_intervention_id()
        expires_frame = self.frameTick + payload["durationFrames"]
        zone = {
            "id": intervention_id,
            "kind": payload["zoneKind"],
            "cells": payload["cells"],
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "reversible": payload.get("reversible", True),
            "status": "active",
            "holdAgentIds": list(payload.get("holdAgentIds") or []),
            "limboHolds": {},
        }
        if payload.get("districtId"):
            zone["districtId"] = payload["districtId"]
        if payload.get("paintTerrain"):
            zone["paintTerrain"] = payload["paintTerrain"]
        if payload.get("keyId"):
            zone["keyId"] = payload["keyId"]
        god = self.civilization["godState"]
        if zone["kind"] == "paint":
            snap = self._architect_paint_snapshot(
                zone["districtId"], payload["cellsExpanded"], zone["paintTerrain"])
            zone["revertSnapshot"] = snap
            self._push_activity(
                f"A divine architect repainted {len(payload['cellsExpanded'])} cells "
                f"in {zone['districtId']} as {zone['paintTerrain']}.")
        elif zone["kind"] == "door":
            for agent_id in payload.get("grantKeyAgentIds") or []:
                agent = self._find_agent_by_id(agent_id)
                if agent is not None:
                    self._grant_god_key(agent, zone["keyId"])
        elif zone["kind"] == "limbo":
            for agent_id in zone["holdAgentIds"]:
                agent = self._find_agent_by_id(agent_id)
                if agent is not None:
                    self._park_agent_in_limbo(agent, zone)
        god.setdefault("architectZones", []).append(zone)
        is_public = zone["kind"] == "paint"
        self._god_record_intervention({
            "id": intervention_id, "kind": "architect_zone", "frameTick": self.frameTick,
            "zoneKind": zone["kind"], "districtId": zone.get("districtId"),
            "cellCount": len(payload["cellsExpanded"]),
            "expiresFrame": expires_frame, "status": "applied", "public": is_public,
        })
        self._log_divine(intervention_id, None, "architect_zone", payload,
                         {"zoneKind": zone["kind"], "districtId": zone.get("districtId"),
                          "cellCount": len(payload["cellsExpanded"]),
                          "holdCount": len(zone["holdAgentIds"])},
                         "applied", public=is_public)
        return {
            "interventionId": intervention_id,
            "kind": "architect_zone",
            "zoneKind": zone["kind"],
            "districtId": zone.get("districtId"),
            "cellCount": len(payload["cellsExpanded"]),
            "expiresFrame": expires_frame,
            "holdAgentIds": list(zone["holdAgentIds"]),
        }

    def _god_apply_architect_zone_cancel(self, payload):
        zone_id = payload.get("zoneId")
        if not isinstance(zone_id, str) or not zone_id.strip():
            return None, "zoneId is required"
        closed = self._close_architect_zone(zone_id.strip(), "cancelled")
        if closed is None:
            return None, "architect zone not found or already inactive"
        intervention_id = self._next_intervention_id()
        is_public = closed.get("kind") == "paint"
        self._god_record_intervention({
            "id": intervention_id, "kind": "architect_zone_cancel", "frameTick": self.frameTick,
            "zoneId": closed.get("id"), "zoneKind": closed.get("kind"),
            "status": "applied", "public": is_public,
        })
        return {
            "interventionId": intervention_id,
            "kind": "architect_zone_cancel",
            "zoneId": closed.get("id"),
            "zoneKind": closed.get("kind"),
        }, None

    def _god_apply_architect_release_hold(self, payload):
        zone_id = payload.get("zoneId")
        if not isinstance(zone_id, str) or not zone_id.strip():
            return None, "zoneId is required"
        zone = self._god_architect_zone_by_id(zone_id.strip())
        if zone is None or zone.get("kind") != "limbo":
            return None, "no active limbo architect zone for zoneId"
        agent_ids = None
        if payload.get("agentIds") is not None:
            agent_ids = []
            for raw_id in payload["agentIds"]:
                if isinstance(raw_id, bool) or not isinstance(raw_id, int):
                    return None, "agentIds entries must be integer agent ids"
                agent_ids.append(raw_id)
        released = self._release_architect_limbo_zone(zone, agent_ids)
        if not released:
            return None, "no limbo holds released for this zone"
        intervention_id = self._next_intervention_id()
        self._god_record_intervention({
            "id": intervention_id, "kind": "architect_release_hold", "frameTick": self.frameTick,
            "zoneId": zone.get("id"), "releasedAgentIds": released,
            "status": "applied", "public": False,
        })
        return {
            "interventionId": intervention_id,
            "kind": "architect_release_hold",
            "zoneId": zone.get("id"),
            "releasedAgentIds": released,
        }, None

    # --- Divine Matrix Phase 10: Reload / Déjà Vu checkpoints ---
    def _god_checkpoint_root_abs(self):
        override = getattr(self, "god_checkpoint_root", None)
        if isinstance(override, str) and override.strip():
            return override.strip()
        return GOD_CHECKPOINT_ROOT

    def _god_checkpoint_rel_path(self, checkpoint_id):
        return f"backup/god-checkpoints/{checkpoint_id}"

    def _god_memory_store_file_path(self):
        ms = self.d.get("memory_store")
        path = getattr(ms, "path", None) if ms is not None else None
        if isinstance(path, str) and path.strip():
            return path.strip()
        return None

    def _god_checkpoint_by_id(self, checkpoint_id):
        for rec in (self.civilization.get("godState") or {}).get("checkpoints") or []:
            if isinstance(rec, dict) and rec.get("id") == checkpoint_id:
                return rec
        return None

    def _god_checkpoint_dir_from_record(self, rec):
        checkpoint_id = rec.get("id") if isinstance(rec, dict) else None
        if isinstance(checkpoint_id, str) and checkpoint_id.strip():
            root_candidate = os.path.join(
                self._god_checkpoint_root_abs(), checkpoint_id.strip())
            if os.path.isdir(root_candidate):
                return root_candidate
        rel = rec.get("path") if isinstance(rec, dict) else None
        if isinstance(rel, str) and rel.strip():
            rel_norm = rel.strip().replace("\\", "/")
            if rel_norm.startswith("backup/"):
                # NOTE: core.py now lives one directory deeper than the
                # original single-file sim_engine.py (see persistence.py's
                # DB_PATH comment) -- an extra os.path.dirname() hop keeps
                # this resolving to simulation/, matching pre-split legacy
                # "backup/..." relative paths recorded before the package split.
                module_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                legacy = os.path.normpath(
                    os.path.join(module_dir, rel_norm.replace("/", os.sep)))
                if os.path.isdir(legacy):
                    return legacy
        return None

    def _god_delete_checkpoint_files(self, rec):
        abs_dir = self._god_checkpoint_dir_from_record(rec)
        if abs_dir and os.path.isdir(abs_dir):
            try:
                shutil.rmtree(abs_dir, ignore_errors=True)
            except Exception:
                pass

    def _god_copy_checkpoint_db_to_live(self, src_db):
        for suffix in ("-wal", "-shm"):
            sidecar = DB_PATH + suffix
            if os.path.exists(sidecar):
                try:
                    os.remove(sidecar)
                except OSError:
                    pass
        shutil.copy2(src_db, DB_PATH)

    def _god_copy_checkpoint_memory_to_live(self, src_mem):
        dst = self._god_memory_store_file_path()
        if not dst:
            return
        tmp = dst + ".tmp"
        try:
            shutil.copy2(src_mem, tmp)
            os.replace(tmp, dst)
        except OSError:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def _god_apply_checkpoint_create(self, payload):
        label = payload["label"]
        replace_oldest = bool(payload.get("replaceOldest"))
        god = self.civilization["godState"]
        checkpoints = god.get("checkpoints")
        if not isinstance(checkpoints, list):
            checkpoints = []
            god["checkpoints"] = checkpoints

        was_paused = self.paused
        self.paused = True
        self.save_state(force=True)
        ms = self.d.get("memory_store")
        if ms is not None:
            try:
                ms._persist()
            except Exception:
                pass

        checkpoint_id = f"ckpt-{secrets.token_urlsafe(8)}"
        rel_path = self._god_checkpoint_rel_path(checkpoint_id)
        abs_dir = os.path.join(self._god_checkpoint_root_abs(), checkpoint_id)
        try:
            os.makedirs(abs_dir, exist_ok=True)
            shutil.copy2(DB_PATH, os.path.join(abs_dir, "state.db"))
            ms_path = self._god_memory_store_file_path()
            if ms_path and os.path.exists(ms_path):
                shutil.copy2(ms_path, os.path.join(abs_dir, "memory_store.json"))
        except OSError:
            self.paused = was_paused
            return None, "checkpoint file copy failed"

        if len(checkpoints) >= GOD_CHECKPOINT_MAX and replace_oldest:
            oldest = checkpoints[0]
            self._god_delete_checkpoint_files(oldest)
            checkpoints.pop(0)

        frame_tick = self.frameTick
        created_at = datetime.now(timezone.utc).isoformat()
        meta = {
            "id": checkpoint_id,
            "label": label,
            "frameTick": frame_tick,
            "path": rel_path,
            "createdAt": created_at,
        }
        checkpoints.append(meta)

        self.paused = was_paused

        intervention_id = self._next_intervention_id()
        self._log_divine(intervention_id, None, "checkpoint_create", payload,
                         {"checkpointId": checkpoint_id, "label": label,
                          "frameTick": frame_tick, "path": rel_path},
                         "applied", public=True)
        self._god_record_intervention({
            "id": intervention_id, "kind": "checkpoint_create", "frameTick": self.frameTick,
            "checkpointId": checkpoint_id, "label": label, "status": "applied", "public": True,
        })
        return {
            "interventionId": intervention_id,
            "kind": "checkpoint_create",
            "checkpointId": checkpoint_id,
            "label": label,
            "frameTick": frame_tick,
            "path": rel_path,
            "checkpointCount": len(checkpoints),
        }, None

    def _god_apply_checkpoint_restore(self, payload):
        checkpoint_id = payload["checkpointId"]
        rec = self._god_checkpoint_by_id(checkpoint_id)
        if rec is None:
            return None, "checkpoint not found"
        abs_dir = self._god_checkpoint_dir_from_record(rec)
        if not abs_dir:
            return None, "checkpoint path invalid"
        ckpt_db = os.path.join(abs_dir, "state.db")
        if not os.path.isfile(ckpt_db):
            return None, "checkpoint state.db missing on disk"

        was_paused = self.paused
        self.paused = True
        try:
            self._god_copy_checkpoint_db_to_live(ckpt_db)
            ckpt_mem = os.path.join(abs_dir, "memory_store.json")
            if os.path.isfile(ckpt_mem):
                self._god_copy_checkpoint_memory_to_live(ckpt_mem)
            if not self.restore_state():
                self.paused = was_paused
                return None, "checkpoint restore failed: state.db unreadable"
        except OSError:
            self.paused = was_paused
            return None, "checkpoint restore file operation failed"

        self.paused = was_paused

        intervention_id = self._next_intervention_id()
        restored_tick = self.frameTick
        self._log_divine(intervention_id, None, "checkpoint_restore", payload,
                         {"checkpointId": checkpoint_id, "label": rec.get("label"),
                          "restoredFrameTick": restored_tick},
                         "applied", public=True)
        self._god_record_intervention({
            "id": intervention_id, "kind": "checkpoint_restore", "frameTick": self.frameTick,
            "checkpointId": checkpoint_id, "label": rec.get("label"),
            "restoredFrameTick": restored_tick, "status": "applied", "public": True,
        })
        return {
            "interventionId": intervention_id,
            "kind": "checkpoint_restore",
            "checkpointId": checkpoint_id,
            "label": rec.get("label"),
            "restoredFrameTick": restored_tick,
        }, None

    def _anointment_prompt_line(self, agent):
        """Private destiny + due oracle hints for the anointed target."""
        rec = self._god_active_anointment_record(agent["id"])
        if not rec:
            return None
        parts = []
        destiny = rec.get("destinyText")
        if isinstance(destiny, str) and destiny.strip():
            parts.append(f"Your anointed destiny: {destiny.strip()}")
        hints = rec.get("oracleHints") or []
        if isinstance(hints, list):
            due = []
            for hint in hints[:GOD_ANOINT_ORACLE_HINTS_MAX]:
                if not isinstance(hint, dict):
                    continue
                reveal = hint.get("revealFrame")
                text = hint.get("text")
                if not isinstance(reveal, int) or reveal > self.frameTick:
                    continue
                if not isinstance(text, str) or not text.strip():
                    continue
                due.append(text.strip())
            if due:
                parts.append("Oracle: " + " | ".join(due))
        if not parts:
            return None
        combined = " ".join(parts)
        if len(combined) > GOD_ANOINT_PROMPT_MAX_CHARS:
            combined = combined[:GOD_ANOINT_PROMPT_MAX_CHARS]
        return combined

