"""Idea-05 external pressure: raiders and contagion.

Deterministic tick-gated raid + contagion mechanics (telegraph, mitigation,
spread/recovery, elder exclusion). Exec-loaded into the shared package
namespace — see simulation/sim_engine/__init__.py.
"""


class _PressureRaidersMixin:
    """Mixin slice of SimEngine: raid/contagion pressure events."""

    def _pressure_telegraph(self):
        if not RAIDERS_CONTAGION_ENABLED:
            return None
        tele = self.civilization.get("pressureTelegraph")
        return tele if isinstance(tele, dict) else None

    def _pressure_telegraph_snapshot(self):
        """Viewer-facing telegraph projection for full and delta /state."""
        tele = self._pressure_telegraph()
        if not tele:
            return None
        return {
            "kind": tele.get("kind"),
            "impactFrame": tele.get("impactFrame"),
            "targetDistrictId": tele.get("targetDistrictId"),
            "targetStructureId": tele.get("targetStructureId"),
            "targetAgentId": tele.get("targetAgentId"),
            "framesRemaining": max(0, int(tele.get("impactFrame", 0)) - self.frameTick),
            "leadFrames": RAID_TELEGRAPH_LEAD_FRAMES,
        }

    def _tick_pressure_raiders(self):
        if not RAIDERS_CONTAGION_ENABLED:
            return
        self._maybe_resolve_pressure_telegraph()
        if self.frameTick % GOODS_TICK_FRAMES != 0:
            return
        self._tick_contagion_infection()
        if self._pressure_telegraph():
            return
        if random.random() <= RAID_EVENT_PROB:
            self._begin_raid_telegraph()
        elif random.random() <= CONTAGION_EVENT_PROB:
            self._begin_contagion_telegraph()

    def _begin_raid_telegraph(self, target_district_id=None, target_structure_id=None):
        """Schedule a telegraphed raid. Optional target overrides for smokes."""
        if not RAIDERS_CONTAGION_ENABLED:
            return None
        if self._pressure_telegraph() and target_district_id is None:
            return self._pressure_telegraph()
        district_id, structure = self._pick_raid_target(
            preferred_district=target_district_id,
            preferred_structure_id=target_structure_id,
        )
        if not district_id:
            return None
        tele = {
            "kind": "raid",
            "impactFrame": self.frameTick + RAID_TELEGRAPH_LEAD_FRAMES,
            "targetDistrictId": district_id,
            "targetStructureId": structure.get("id") if structure else None,
            "scheduledFrame": self.frameTick,
        }
        self.civilization["pressureTelegraph"] = tele
        self._mark_top_dirty("pressureTelegraph")
        if structure:
            label = structure.get("name") or structure.get("type") or "structure"
        else:
            label = district_id
        lead_s = max(1, RAID_TELEGRAPH_LEAD_FRAMES // TICKS_PER_SEC)
        self._push_activity(
            f"Raiders spotted — attack expected on {label} in {district_id} "
            f"within ~{lead_s}s")
        return tele

    def _begin_contagion_telegraph(self, patient_zero_id=None):
        """Schedule a telegraphed contagion outbreak. Optional patient-zero id for smokes."""
        if not RAIDERS_CONTAGION_ENABLED:
            return None
        if self._pressure_telegraph() and patient_zero_id is None:
            return self._pressure_telegraph()
        patient = self._pick_contagion_patient_zero(patient_zero_id)
        if not patient:
            return None
        tele = {
            "kind": "contagion",
            "impactFrame": self.frameTick + RAID_TELEGRAPH_LEAD_FRAMES,
            "targetAgentId": patient.get("id"),
            "targetDistrictId": patient.get("currentDistrict"),
            "scheduledFrame": self.frameTick,
        }
        self.civilization["pressureTelegraph"] = tele
        self._mark_top_dirty("pressureTelegraph")
        lead_s = max(1, RAID_TELEGRAPH_LEAD_FRAMES // TICKS_PER_SEC)
        self._push_activity(
            f"Sickness reported — contagion expected near {patient.get('name')} "
            f"within ~{lead_s}s")
        return tele

    def _pick_contagion_patient_zero(self, preferred_agent_id=None):
        """Non-elder living agent for outbreak seeding (never the elder)."""
        if preferred_agent_id is not None:
            for agent in self._living_agents():
                if agent.get("id") == preferred_agent_id and agent.get("role") != "elder":
                    return agent
            return None
        candidates = [
            a for a in self._living_agents()
            if a.get("role") != "elder"
        ]
        return random.choice(candidates) if candidates else None

    def _infect_agent(self, agent, *, announce=True):
        """Mark an agent infected. Returns False if ineligible or already infected."""
        if not RAIDERS_CONTAGION_ENABLED:
            return False
        if agent.get("role") == "elder" or agent.get("infected"):
            return False
        agent["infected"] = True
        agent["infectionFrame"] = self.frameTick
        self._mark_agent_dirty(agent)
        if announce:
            self._push_activity(f"{agent.get('name')} has fallen ill")
        return True

    def _clear_agent_infection(self, agent, *, announce=True, reason="recovered"):
        if not agent.get("infected"):
            return
        agent["infected"] = False
        agent["infectionFrame"] = None
        self._mark_agent_dirty(agent)
        if announce:
            self._push_activity(f"{agent.get('name')} {reason} from illness")

    def _agent_contagion_recovery_prob(self, agent):
        """Per goods-tick-gate early-recovery probability (healer + clinic only)."""
        prob = 0.0
        ax, ay = float(agent.get("x", 0)), float(agent.get("y", 0))
        for healer in self._living_agents():
            if healer.get("role") != "healer" or healer.get("incapacitated"):
                continue
            hx, hy = float(healer.get("x", 0)), float(healer.get("y", 0))
            if _dist(ax, ay, hx, hy) <= HEALER_RECOVERY_RADIUS:
                prob += HEALER_RECOVERY_BONUS
                break
        district_id = agent.get("currentDistrict")
        if district_id:
            prob += self._district_clinic_recovery_bonus(district_id)
        return prob

    def _tick_contagion_infection(self):
        """Spread, health loss, and recovery on each goods-tick gate."""
        if not RAIDERS_CONTAGION_ENABLED:
            return
        carriers = [a for a in self._living_agents() if a.get("infected")]
        if not carriers:
            return

        for agent in list(carriers):
            before_h = agent.get("health", 100)
            agent["health"] = max(0, before_h - CONTAGION_HEALTH_LOSS_PER_TICK_GATE)
            self._mark_agent_dirty(agent)

            start = agent.get("infectionFrame")
            if start is not None and self.frameTick - start >= CONTAGION_DURATION_FRAMES:
                self._clear_agent_infection(agent)
                continue

            recovery_prob = self._agent_contagion_recovery_prob(agent)
            if recovery_prob > 0 and random.random() < recovery_prob:
                self._clear_agent_infection(agent)

        carriers = [a for a in self._living_agents() if a.get("infected")]
        for carrier in carriers:
            cx, cy = float(carrier.get("x", 0)), float(carrier.get("y", 0))
            for target in self._living_agents():
                if target.get("infected") or target.get("role") == "elder":
                    continue
                tx, ty = float(target.get("x", 0)), float(target.get("y", 0))
                if _dist(cx, cy, tx, ty) > CONTAGION_SPREAD_RADIUS:
                    continue
                if random.random() < CONTAGION_TRANSMISSION_PROB:
                    self._infect_agent(target)

    def _pick_raid_target(self, preferred_district=None, preferred_structure_id=None):
        c = self.civilization

        def _standing(s):
            return (s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                    and not s.get("isRuin"))

        if preferred_structure_id is not None:
            for s in c["structures"]:
                if s.get("id") == preferred_structure_id and _standing(s):
                    return s.get("districtId"), s

        standing = [s for s in c["structures"] if _standing(s)]
        if preferred_district:
            in_district = [s for s in standing if s.get("districtId") == preferred_district]
            if in_district:
                return preferred_district, random.choice(in_district)
            return preferred_district, None

        if standing:
            chosen = random.choice(standing)
            return chosen.get("districtId"), chosen

        self._ensure_district_stocks()
        best_did, best_total = None, -1
        stockpile = c.get("stockpile") or {}
        for did, meta in c["districts"].items():
            total = sum((c["districtStocks"].get(did) or {}).values())
            if meta.get("kind") == "village":
                total += sum(stockpile.values())
            if total > best_total:
                best_total = total
                best_did = did
        if best_did is None and c["districts"]:
            best_did = next(iter(c["districts"]))
        return best_did, None

    def _raid_target_point(self, tele):
        c = self.civilization
        sid = tele.get("targetStructureId")
        if sid is not None:
            for s in c["structures"]:
                if s.get("id") == sid:
                    return float(s.get("x", 0)), float(s.get("y", 0))
        did = tele.get("targetDistrictId")
        if did and did in c.get("districts", {}):
            b = c["districts"][did]["bounds"]
            return (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        return WORLD_W / 2, WORLD_H / 2

    def _resolve_raid_mitigation(self, tx, ty, district_id):
        guard_count = 0
        for g in self._living_agents():
            if g.get("role") != "guard" or g.get("incapacitated"):
                continue
            if _dist(float(g.get("x", 0)), float(g.get("y", 0)), tx, ty) <= RAID_GUARD_RADIUS:
                guard_count += 1
        guard_mitigation = min(
            RAID_GUARD_MITIGATION_CAP,
            guard_count * RAID_GUARD_MITIGATION_PER_GUARD,
        )
        wall_mitigation = self._district_raid_wall_mitigation(district_id)
        return min(RAID_GUARD_MITIGATION_CAP, guard_mitigation + wall_mitigation)

    def _maybe_resolve_pressure_telegraph(self):
        if not RAIDERS_CONTAGION_ENABLED:
            return
        tele = self._pressure_telegraph()
        if not tele:
            return
        if self.frameTick < tele.get("impactFrame", 0):
            return
        kind = tele.get("kind")
        if kind == "raid":
            self._resolve_raid(tele)
        elif kind == "contagion":
            self._resolve_contagion(tele)
        self.civilization.pop("pressureTelegraph", None)
        self._mark_top_dirty("pressureTelegraph")

    def _resolve_contagion(self, tele):
        patient = None
        pid = tele.get("targetAgentId")
        if pid is not None:
            patient = next((a for a in self._living_agents() if a.get("id") == pid), None)
        if not patient or patient.get("role") == "elder":
            patient = self._pick_contagion_patient_zero()
        if not patient:
            return
        self._infect_agent(patient, announce=False)
        self._push_activity(
            f"Contagion outbreak — {patient.get('name')} is patient zero")

    def _resolve_raid(self, tele):
        c = self.civilization
        district_id = tele.get("targetDistrictId")
        tx, ty = self._raid_target_point(tele)
        mitigation = self._resolve_raid_mitigation(tx, ty, district_id)
        severity = 1.0 - mitigation

        removed_bits = []
        self._ensure_district_stocks()
        meta = c["districts"].get(district_id) or {}
        if meta.get("kind") == "village":
            stockpile = c.setdefault("stockpile", {})
            for rid in list(stockpile.keys()):
                amt = int(stockpile.get(rid) or 0)
                if amt <= 0:
                    continue
                base_loss = random.randint(RAID_RESOURCE_LOSS_MIN, RAID_RESOURCE_LOSS_MAX)
                loss = min(amt, max(0, int(round(base_loss * severity))))
                if loss > 0:
                    stockpile[rid] = amt - loss
                    removed_bits.append(f"{loss} {rid}")
        dstocks = c["districtStocks"].setdefault(district_id, {})
        for rid in list(dstocks.keys()):
            amt = int(dstocks.get(rid) or 0)
            if amt <= 0:
                continue
            base_loss = random.randint(RAID_RESOURCE_LOSS_MIN, RAID_RESOURCE_LOSS_MAX)
            loss = min(amt, max(0, int(round(base_loss * severity))))
            if loss > 0:
                dstocks[rid] = amt - loss
                removed_bits.append(f"{loss} {rid} from {district_id}")

        structure = None
        sid = tele.get("targetStructureId")
        if sid is not None:
            structure = next((s for s in c["structures"] if s.get("id") == sid), None)
        if structure is None:
            candidates = [
                s for s in c["structures"]
                if s.get("districtId") == district_id
                and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                and not s.get("isRuin")
            ]
            if candidates:
                structure = random.choice(candidates)
        struct_note = None
        if structure:
            before = structure.get("condition", 100.0)
            dmg = RAID_STRUCTURE_DAMAGE * severity
            after = self._apply_structure_condition_delta(structure, -dmg)
            struct_note = (
                f"{structure.get('name') or structure.get('type')} "
                f"condition {before:.0f}->{after:.0f}")

        victims = []
        contact_dmg = max(0, int(round(RAID_CONTACT_DAMAGE * severity)))
        if contact_dmg > 0:
            for agent in self._living_agents():
                if agent.get("role") == "elder" or agent.get("incapacitated"):
                    continue
                ax, ay = float(agent.get("x", 0)), float(agent.get("y", 0))
                if _dist(ax, ay, tx, ty) > RAID_GUARD_RADIUS:
                    continue
                before_h = agent.get("health", 100)
                agent["health"] = max(0, before_h - contact_dmg)
                self._mark_agent_dirty(agent)
                victims.append(agent.get("name"))

        parts = [f"Raiders struck {district_id}!"]
        if removed_bits:
            parts.append("lost " + ", ".join(removed_bits))
        if struct_note:
            parts.append(struct_note)
        if victims:
            parts.append(f"{len(victims)} villagers hurt")
        if mitigation > 0:
            parts.append(f"mitigation {int(round(mitigation * 100))}%")
        self._push_activity(" — ".join(parts))

    def _pressure_warning_prompt_line(self):
        if not RAIDERS_CONTAGION_ENABLED:
            return None
        tele = self._pressure_telegraph()
        if not tele:
            return None
        remaining = max(0, tele.get("impactFrame", 0) - self.frameTick)
        secs = max(1, remaining // TICKS_PER_SEC)
        kind = tele.get("kind")
        if kind == "raid":
            did = tele.get("targetDistrictId") or "the village"
            return (f"RAID WARNING: raiders will strike {did} in ~{secs}s — "
                    f"organize guards and defenses")
        if kind == "contagion":
            pid = tele.get("targetAgentId")
            patient = next(
                (a for a in self._living_agents() if a.get("id") == pid), None)
            label = patient.get("name") if patient else "the village"
            return (f"CONTAGION WARNING: sickness will reach {label} in ~{secs}s — "
                    f"seek healer coverage or a clinic")
        return None
