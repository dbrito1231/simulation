"""Phase 6c mixin: structures/economy/weather slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_structure_function_for_type`
through the end of the structure-upgrades section (formerly core.py lines
~2041-3854, before Phase 6b's extraction shifted line numbers; re-located by
method name for this move). Covers: the structure function registry (Phase A
consequence engine), Phase C/D query-time helpers (GOODS_ENABLED,
TECH_TREE_ENABLED), tech eras, the weather state machine (Living-ecosystem
Phase 4), Phase E market pricing/priced trade/property (ECONOMY_ENABLED),
Phase C tick mechanics (spoilage/decay/disaster/shelter), `repair_structure`
(the decay escape hatch), and structure upgrades (STRUCTURE_UPGRADES_ENABLED).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names (WEATHER_ENABLED,
# ECONOMY_ENABLED, STRUCTURE_UPGRADES_ENABLED, TECH_TREE_ENABLED, ...) are NOT
# imported here. They live in the exec()-shared namespace — see
# simulation/sim_engine/__init__.py.


class _StructuresEconomyMixin:
    """Mixin slice of SimEngine: structure function registry, GOODS/TECH
    query helpers, tech eras, weather state machine, market pricing/priced
    trade/property, spoilage/decay/disaster/shelter tick mechanics,
    repair_structure, and structure upgrades. See module docstring for exact
    scope."""

    # --- structure function registry (Phase A consequence engine) ---
    def _structure_function_for_type(self, type_):
        c = self.civilization
        tmpl = c["projectRegistry"].get(type_) or PROJECT_TEMPLATES.get(type_) or {}
        fn = tmpl.get("function")
        if fn:
            return fn
        if type_ in SEED_STRUCTURE_FUNCTIONS:
            seed_fn = SEED_STRUCTURE_FUNCTIONS[type_]
            if type_ == "wall" and not RAIDERS_CONTAGION_ENABLED and "mitigates" in seed_fn:
                return {key: value for key, value in seed_fn.items() if key != "mitigates"}
            return seed_fn
        if tmpl.get("custom"):
            return {"produces": [dict(LEGACY_CUSTOM_PRODUCE)]}
        return {}

    def _resource_in_function(self, rid, fn):
        if not fn:
            return False
        for prod in fn.get("produces") or []:
            if prod.get("resource") == rid:
                return True
        for boost in fn.get("boosts") or []:
            if rid in (boost.get("resources") or []):
                return True
        for store in fn.get("stores") or []:
            if store.get("resource") == rid:
                return True
        upkeep = fn.get("upkeep")
        if isinstance(upkeep, dict) and upkeep.get("resource") == rid:
            return True
        for unlock in fn.get("unlocks") or []:
            if unlock.get("kind") == "transit":
                if rid in (unlock.get("consumes") or {}):
                    return True
        return False

    def _get_structure_function(self, type_):
        return self._structure_function_for_type(type_) if STRUCTURE_EFFECTS_ENABLED else {}

    def _canonical_effect_vector(self, function):
        return self.d["canonical_effect_vector"](function)

    def _effect_vector_owner_map(self):
        """Map canonical effect vector -> owning id (seed/custom structure type
        or pending blueprint id), so a new proposal can be tagged duplicateOf
        the thing it duplicates instead of just being rejected outright."""
        c = self.civilization
        owners = {}
        for tid in SEED_STRUCTURE_FUNCTIONS:
            fn = self._get_structure_function(tid)
            vec = self._canonical_effect_vector(fn)
            if vec:
                owners.setdefault(vec, tid)
        for pid in c["projectRegistry"]:
            fn = self._get_structure_function(pid)
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    owners.setdefault(vec, pid)
        for bp in c["pendingBlueprints"]:
            fn = bp.get("function")
            if fn:
                vec = self._canonical_effect_vector(fn)
                if vec:
                    owners.setdefault(vec, bp["id"])
        return owners

    def _known_effect_vectors(self):
        return set(self._effect_vector_owner_map())

    def _structure_display_name(self, type_id):
        c = self.civilization
        return (c["projectRegistry"].get(type_id) or PROJECT_TEMPLATES.get(type_id) or {}).get("name", type_id)

    # --- Phase C query-time helpers (GOODS_ENABLED) ---
    def _working_structure_count(self, type_, district_id=None):
        """Structures still functional under decay: condition >= the disrepair
        threshold (ruins are 0 and never count). With GOODS_ENABLED off this
        is exactly _structure_count, so Phase A/B behavior is unchanged."""
        if not GOODS_ENABLED:
            return self._structure_count(type_, district_id)
        return sum(1 for s in self.civilization["structures"]
                   if s.get("type") == type_
                   and (district_id is None or s.get("districtId") == district_id)
                   and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)

    def _district_raid_wall_mitigation(self, district_id):
        """Passive wall raid mitigation for a district (query-time, not ticked)."""
        if not RAIDERS_CONTAGION_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return 0.0
        if self._working_structure_count("wall", district_id) <= 0:
            return 0.0
        fn = self._get_structure_function("wall")
        for mit in fn.get("mitigates") or []:
            if mit.get("kind") != "raid":
                continue
            if mit.get("scope", "district") not in ("district", "village"):
                continue
            try:
                return float(mit.get("amount", RAID_WALL_MITIGATION))
            except (TypeError, ValueError):
                return RAID_WALL_MITIGATION
        return RAID_WALL_MITIGATION

    def _district_has_standing_clinic(self, district_id):
        """True when a non-ruined clinic stands in the district."""
        if not RAIDERS_CONTAGION_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        return self._working_structure_count("clinic", district_id) > 0

    def _district_clinic_recovery_bonus(self, district_id):
        """Passive in-district clinic recovery bonus (query-time, not ticked)."""
        if not self._district_has_standing_clinic(district_id):
            return 0.0
        fn = self._get_structure_function("clinic")
        for heal in fn.get("heals") or []:
            if heal.get("kind") != "contagion_recovery":
                continue
            if heal.get("scope", "district") not in ("district", "village"):
                continue
            try:
                return float(heal.get("bonus", CLINIC_RECOVERY_BONUS))
            except (TypeError, ValueError):
                return CLINIC_RECOVERY_BONUS
        return CLINIC_RECOVERY_BONUS

    def _carry_cap(self, agent):
        """Per-agent carry cap: COLLECT_CAP, +CART_CARRY_BONUS while holding a
        cart (query-time vehicle effect, like _gather_yield_bonus). Phase D:
        the wagon (the cart's tier-2 upgrade) grants the larger bonus."""
        if TECH_TREE_ENABLED and agent["resources"].get("wagon", 0) > 0:
            return COLLECT_CAP + WAGON_CARRY_BONUS
        if GOODS_ENABLED and agent["resources"].get("cart", 0) > 0:
            return COLLECT_CAP + CART_CARRY_BONUS
        return COLLECT_CAP

    # --- Phase D query-time helpers (TECH_TREE_ENABLED) ---
    def _vehicle_speed_mult(self, agent):
        """Movement speed multiplier for vehicle holders (query-time, applied
        in _move_agent). Only the wagon moves faster; the cart only carries."""
        if TECH_TREE_ENABLED and agent["resources"].get("wagon", 0) > 0:
            return WAGON_SPEED_MULT
        return 1.0

    def _type_tier(self, type_):
        """Tech tier of a structure type: live registry entry first, then the
        seed template (covers registries restored from pre-Phase-D saves,
        whose entries carry no tier field), else 1."""
        c = self.civilization
        tier = (c["projectRegistry"].get(type_) or {}).get("tier")
        if tier is None:
            tier = (PROJECT_TEMPLATES.get(type_) or {}).get("tier")
        return tier if isinstance(tier, int) and tier >= 1 else 1

    def _village_tech_tier(self):
        """Highest tier unlocked by a built, WORKING station structure
        (floor 1). The workshop's craft unlock is tier 1; the Forge's is
        tier 2; blueprints may declare higher unlock tiers (bounded by
        validate_blueprint's escape rule: unlock tier <= blueprint tier + 1)."""
        if not TECH_TREE_ENABLED:
            return MAX_TECH_TIER  # gate disabled: nothing is ever tier-locked
        best = 1
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") != "craft":
                    continue
                t = unlock.get("tier", 1)
                if isinstance(t, int) and t > best and self._working_structure_count(type_id) > 0:
                    best = t
        return best

    def _tier_gate_reason(self, tier):
        """Human-readable refusal for tier-locked tech, always naming the
        deterministic escape."""
        if tier <= 2:
            return (f"tier {tier} tech needs a tier-{tier} station built first "
                    f"(the Forge unlocks tier 2 and is a normal tier-1 build)")
        return (f"tier {tier} tech needs a tier-{tier} station built first "
                f"(invent a structure whose function unlocks tier {tier} crafting)")

    def _type_tier_locked(self, type_):
        """(locked, reason) for starting a project of this type."""
        if not TECH_TREE_ENABLED:
            return False, None
        tier = self._type_tier(type_)
        if tier <= self._village_tech_tier():
            return False, None
        return True, self._tier_gate_reason(tier)

    def _function_summary(self, fn):
        """Compact one-line summary of a function block, for the elder's
        comparative council prompt and the persisted councilLog records."""
        parts = []
        for p in (fn or {}).get("produces") or []:
            parts.append(f"produces {p.get('amount', 1)} {p.get('resource')}")
        for b in (fn or {}).get("boosts") or []:
            res = "/".join(b.get("resources") or []) if b.get("kind") == "gather" \
                else f"@{b.get('station')}"
            parts.append(f"boosts {b.get('kind')} {res}")
        for u in (fn or {}).get("unlocks") or []:
            t = u.get("tier")
            parts.append(f"unlocks {u.get('station')}" + (f" (tier {t})" if t else ""))
        for s in (fn or {}).get("stores") or []:
            parts.append(f"stores {s.get('capacity')} {s.get('resource')}")
        for m in (fn or {}).get("mitigates") or []:
            parts.append(f"mitigates {m.get('kind')} {m.get('amount')}")
        for h in (fn or {}).get("heals") or []:
            parts.append(f"heals {h.get('kind')} {h.get('bonus')}")
        if (fn or {}).get("houses"):
            parts.append("houses villagers")
        return "; ".join(parts) or "no effect"

    # --- Phase D eras ---
    def _era_capabilities(self):
        caps = set()
        c = self.civilization
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id)
            if (fn.get("unlocks") or []) and self._working_structure_count(type_id) > 0:
                caps.add("crafting")
                break
        if self._village_tech_tier() >= 2:
            caps.add("metallurgy")
        if PATH1_ENABLED:
            if any(s.get("type") == "harbor" and self._working_structure_count("harbor") > 0
                   for s in c["structures"]):
                caps.add("harbor")
            if any(s.get("type") == "mill" and self._working_structure_count("mill") > 0
                   for s in c["structures"]):
                caps.add("mill")
        vehicles = ("cart", "wagon")
        if any(a["resources"].get(v, 0) > 0 for a in self.agents for v in vehicles) \
                or any(c["stockpile"].get(v, 0) > 0 for v in vehicles):
            caps.add("vehicles")
        has_light = any(isinstance((self._get_structure_function(s.get("type")) or {}).get("light"), dict)
                        and self._working_structure_count(s.get("type")) > 0
                        for s in c["structures"])
        if has_light and self._has_ocean_transit():
            caps.add("civilization")
        return caps

    def _current_era_index(self):
        caps = self._era_capabilities()
        idx = 0
        for i, (_, cap) in enumerate(ERA_LADDER):
            if cap is None or cap in caps:
                idx = i
        return idx

    def _current_era_name(self):
        if not TECH_TREE_ENABLED:
            return None
        c = self.civilization
        return c.get("era") or ERA_LADDER[max(0, min(len(ERA_LADDER) - 1,
                                                     c.get("eraIndex") or 0))][0]

    def _maybe_era_transition(self):
        """Tick-gated era check. Monotonic: capabilities only ever advance the
        era (a broken forge doesn't un-name the age). Transitions are logged
        dramatically and benchmarked (`era`)."""
        if not TECH_TREE_ENABLED:
            return
        c = self.civilization
        idx = self._current_era_index()
        stored = c.get("eraIndex") or 0
        if idx > stored or not c.get("era"):
            advanced = idx > stored
            c["eraIndex"] = max(idx, stored)
            c["era"] = ERA_LADDER[c["eraIndex"]][0]
            if advanced:
                self._push_activity(
                    f"A new age dawns — the village enters the {c['era']}!")
                self._log_benchmark("era", c["eraIndex"],
                                    {"era": c["era"],
                                     "tech_tier": self._village_tech_tier()})

    def _storage_capacity(self, resource_id):
        """Village-wide storage capacity for a resource: the base camp pile
        plus every working structure's `stores` entries (Phase A function
        registry -- accepted by validate_function_block since Phase A, made
        real here)."""
        cap = BASE_STORAGE_CAPACITY
        if not STRUCTURE_EFFECTS_ENABLED:
            return cap
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for store in fn.get("stores") or []:
                if store.get("resource") != resource_id:
                    continue
                cap += store.get("capacity", 0) * self._working_structure_count(type_id)
        return cap

    def _current_season(self):
        """Four-season clock derived from frameTick (no persisted state)."""
        if not GOODS_ENABLED:
            return None
        return SEASONS[(self.frameTick // SEASON_FRAMES) % len(SEASONS)]

    def _calendar(self):
        """In-world calendar, a pure function of frameTick (nothing persisted)."""
        return {
            "year": self.frameTick // YEAR_FRAMES + 1,
            "season": self._current_season(),
            "dayOfSeason": (self.frameTick % SEASON_FRAMES) // DAY_FRAMES + 1,
            "daysPerSeason": SEASON_FRAMES // DAY_FRAMES,
            "isNight": self._is_night(),
            "dayFraction": (self.frameTick % DAY_FRAMES) / DAY_FRAMES,
        }

    # --- Living-ecosystem Phase 4: weather state machine ---
    def _weather_default(self, frame=0):
        """Cold-start / restore-backfill weather value -- always "clear",
        never re-rolled from a real prior state (that only happens via
        setdefault, which leaves an already-persisted value untouched)."""
        return {"state": "clear", "since": frame, "exitFrame": frame, "districts": []}

    def _weather_enter(self, state):
        """Draw a dwell duration (in GOODS_TICK_FRAMES units) for `state` and
        record it as civilization["weather"]. Called only from
        _tick_weather, itself only reached on the GOODS_TICK_FRAMES cadence
        -- no new timer."""
        c = self.civilization
        w = c["weather"]
        lo, hi = WEATHER_DWELL_TICKS[state]
        storminess = WEATHER_SEASON_STORMINESS.get(self._current_season(), 1.0)
        if state == "clear":
            # Stormier seasons shorten the gap between storm attempts.
            lo = max(4, round(lo / storminess))
            hi = max(lo + 1, round(hi / storminess))
        dwell = random.randint(lo, hi)
        w["state"] = state
        w["since"] = self.frameTick
        w["exitFrame"] = self.frameTick + dwell * GOODS_TICK_FRAMES
        if state == "storm":
            districts = sorted(c["districts"].keys())
            pick_n = min(len(districts), random.choice((1, 1, 2)))
            w["districts"] = random.sample(districts, k=pick_n) if districts else []
            where = ", ".join(w["districts"]) if w["districts"] else "the village"
            self._push_activity(f"Storm clouds break over {where} -- structures may take damage")
        elif state == "clearing":
            where = ", ".join(w.get("districts") or []) or "the village"
            self._push_activity(f"The storm passes; skies begin to clear over {where}")
        elif state == "clear":
            w["districts"] = []

    def _weather_enter_forced(self, state, districts, exit_frame):
        """Sovereign God mode Phase 6 (docs/archive/plan-sovereign-god-mode-v2.md Answer 3 -- "RNG
        discipline"): enters `state` with an OPERATOR-chosen district list
        and an exit frame DERIVED from the divine event's expiresFrame
        (Answer 1: the event owns the clock), drawing NO RNG at all --
        unlike _weather_enter, this never calls random.randint or
        random.sample; state/districts/exitFrame all come straight from the
        already-validated god command. Emits the SAME narration
        _weather_enter emits for that state, so a forced storm/clearing
        reads identically to a natural one in activity. Left completely
        separate from _weather_enter (which stays untouched and still
        RNG-driven) rather than adding a branch to it, so the natural
        machine's RNG-consumption contract can never be affected by this
        code path existing."""
        c = self.civilization
        w = c["weather"]
        w["state"] = state
        w["since"] = self.frameTick
        w["exitFrame"] = exit_frame
        w["districts"] = list(districts)
        if state == "storm":
            where = ", ".join(w["districts"]) if w["districts"] else "the village"
            self._push_activity(f"Storm clouds break over {where} -- structures may take damage")
        elif state == "clearing":
            where = ", ".join(w["districts"]) if w["districts"] else "the village"
            self._push_activity(f"The storm passes; skies begin to clear over {where}")
        elif state == "clear":
            w["districts"] = []

    def _weather_handoff_successor(self, state):
        """Sovereign God mode Phase 6 (docs/archive/plan-sovereign-god-mode-v2.md Answer 2 -- "returning to
        the natural cycle"): the natural _tick_weather successor for the
        OVERRIDDEN `state`, entered via the SAME RNG-drawing _weather_enter()
        the natural machine always uses -- mirrors _tick_weather's
        transition table exactly (clear -> gathering, gathering -> storm or
        clear (same probability branch), storm -> clearing, clearing ->
        clear), but is invoked unconditionally at override-close time (the
        caller -- _close_weather_override -- already knows the override is
        ending) rather than gated on civilization["weather"]["exitFrame"].
        Deliberately does NOT restore the pre-override state -- that would
        double back and desync the strict cycle. Callers in a deterministic
        smoke must random.seed() a fixed value before invoking this (via
        expiry or cancel) to keep the resulting draw reproducible."""
        if state == "clear":
            self._weather_enter("gathering")
        elif state == "gathering":
            storminess = WEATHER_SEASON_STORMINESS.get(self._current_season(), 1.0)
            p_storm = min(0.95, max(0.05, WEATHER_BASE_STORM_CHANCE * storminess))
            self._weather_enter("storm" if random.random() < p_storm else "clear")
        elif state == "storm":
            self._weather_enter("clearing")
        else:  # "clearing" (or any unknown legacy value) -> clear
            self._weather_enter("clear")

    def _tick_weather(self):
        """Living-ecosystem Phase 4: deterministic clear -> gathering ->
        storm -> clearing -> clear cycle, advanced on the existing
        GOODS_TICK_FRAMES cadence (called from _tick_goods) -- no new timer.
        The current state's dwell duration was drawn (in goods-ticks) when
        it was entered and stored as civilization["weather"]["exitFrame"];
        this only checks whether that frame has been reached and, if so,
        transitions once. Season-weighted via WEATHER_SEASON_STORMINESS so
        storms cluster in stormier seasons -- see _maybe_disaster for how
        this is calibrated against the legacy disaster rate."""
        if not WEATHER_ENABLED:
            return
        c = self.civilization
        w = c.setdefault("weather", self._weather_default(self.frameTick))
        if self.frameTick < w.get("exitFrame", 0):
            return
        state = w.get("state", "clear")
        if state == "clear":
            self._weather_enter("gathering")
        elif state == "gathering":
            storminess = WEATHER_SEASON_STORMINESS.get(self._current_season(), 1.0)
            p_storm = min(0.95, max(0.05, WEATHER_BASE_STORM_CHANCE * storminess))
            self._weather_enter("storm" if random.random() < p_storm else "clear")
        elif state == "storm":
            self._weather_enter("clearing")
        else:  # "clearing" (or any unknown legacy value) -> clear
            self._weather_enter("clear")

    def _weather_snapshot(self):
        """Read-only /state projection of the weather state machine -- same
        placement pattern (sibling of civilization) as socialTies/
        districtEcology/shipments."""
        w = self.civilization.get("weather") or {}
        return {
            "state": w.get("state", "clear"),
            "since": w.get("since", 0),
            "districts": list(w.get("districts") or []),
        }

    def _weather_prompt_line(self):
        """Living-ecosystem Phase 5 (WEATHER_GOVERNANCE_ENABLED): one short
        think-payload line surfacing storm/clearing conditions so agents can
        actually reference them in council -- follows the exact
        chronicle_line/council_digest_line pattern (server renders it ONLY
        when this returns non-None, so flag-off/clear-weather prompts stay
        byte-identical to Phase 4 alone). Deliberately silent during "clear"
        and "gathering" (nothing worth a line yet) to keep this the ONE short
        line the plan calls for, not a running weather ticker. No new LLM
        call -- rides the existing think cycle."""
        if not WEATHER_GOVERNANCE_ENABLED or not WEATHER_ENABLED:
            return None
        w = self.civilization.get("weather") or {}
        state = w.get("state", "clear")
        if state not in ("storm", "clearing"):
            return None
        districts = w.get("districts") or []
        where = ", ".join(districts) if districts else "the village"
        if state == "storm":
            return f"a storm is battering {where} -- gathering there is suppressed"
        return f"skies are clearing over {where} -- stocks there are recovering faster than usual"

    def _gather_yield_bonus(self, agent, resource):
        if not STRUCTURE_EFFECTS_ENABLED:
            return 0
        district = agent.get("currentDistrict")
        bonus = 0
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for boost in fn.get("boosts") or []:
                if boost.get("kind") != "gather":
                    continue
                resources = boost.get("resources") or []
                if resource not in resources:
                    continue
                scope = boost.get("scope", "district")
                if STRUCTURE_UPGRADES_ENABLED:
                    count = int(self._weighted_working_count(
                        type_id, district if scope == "district" else None))
                else:
                    count = self._working_structure_count(type_id, district if scope == "district" else None)
                every_n = boost.get("every_n", 1)
                max_bonus = boost.get("max_bonus", 1)
                bonus += min(max_bonus, (count // every_n) * boost.get("bonus", 1))
        return bonus

    def _craft_station_unlocked(self, station):
        if not STRUCTURE_EFFECTS_ENABLED or not station:
            return True
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") == "craft" and unlock.get("station") == station \
                        and self._working_structure_count(type_id) > 0:
                    return True
        return False

    def _craft_output_bonus(self, recipe, district_id=None):
        if not STRUCTURE_EFFECTS_ENABLED:
            return 0
        station = recipe.get("station")
        if not station:
            return 0
        bonus = 0
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for boost in fn.get("boosts") or []:
                if boost.get("kind") != "craft" or boost.get("station") != station:
                    continue
                scope = boost.get("scope", "village")
                if STRUCTURE_UPGRADES_ENABLED:
                    count = int(self._weighted_working_count(
                        type_id, district_id if scope == "district" else None))
                else:
                    count = self._working_structure_count(type_id, district_id if scope == "district" else None)
                every_n = boost.get("every_n", 1)
                max_bonus = boost.get("max_bonus", 1)
                bonus += min(max_bonus, (count // every_n) * boost.get("bonus", 1))
        return bonus

    # --- Phase E: market pricing, priced trade, property (ECONOMY_ENABLED) ---
    def _market_active(self):
        """True while at least one WORKING market unlocks pricing (same
        query-time unlock pattern as craft stations)."""
        if not ECONOMY_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") == "pricing" and self._working_structure_count(type_id) > 0:
                    return True
        return False

    def _mint_active(self):
        """True while at least one WORKING mint unlocks currency (mirrors
        _market_active exactly, checking the "currency" unlock kind)."""
        if not ECONOMY_ENABLED or not STRUCTURE_EFFECTS_ENABLED:
            return False
        for type_id in {s["type"] for s in self.civilization["structures"]}:
            fn = self._get_structure_function(type_id)
            for unlock in fn.get("unlocks") or []:
                if unlock.get("kind") == "currency" and self._working_structure_count(type_id) > 0:
                    return True
        return False

    def _active_currency(self):
        """Which resource settles a priced trade: "coin" once a mint exists,
        else "gold" (the pre-mint, byte-identical default). Only ever
        consulted from the priced-trade path, which is itself already gated
        on _market_active() at its call site -- this decides WHICH resource
        settles once both a market and a mint exist, nothing else."""
        return "coin" if self._mint_active() else "gold"

    def _maybe_mint_coin(self):
        """Deterministic treasury mechanic, same RULES_TICK_FRAMES batch as
        _maybe_repair_critical etc: while a WORKING mint exists, convert up to
        MINT_RATE gold from the VILLAGE STOCKPILE (not agents' held gold) into
        that many coin. No LLM action needed -- minting is infrastructure, the
        same way _market_active itself needs no agent action. With no mint
        built this is a no-op every call (the pre-mint, byte-identical path)."""
        if not ECONOMY_ENABLED or not self._mint_active():
            return
        c = self.civilization
        gold = c["stockpile"].get("gold", 0)
        minted = min(gold, MINT_RATE)
        if minted <= 0:
            return
        c["stockpile"]["gold"] = gold - minted
        c["stockpile"]["coin"] = c["stockpile"].get("coin", 0) + minted

    def _maybe_fund_project_coin(self):
        """Deterministic backstop, same RULES_TICK_FRAMES batch: once a mint
        exists, the coin treasury (village stockpile, built by minting +
        resource_tax) tops up any active project's coin requirement directly
        -- coin is spendable like any other stockpiled resource once minted,
        no elder-only gate (the elder's role in provisioning the treasury is
        upstream, via the tax rule they enact -- see specs/08). No-op today
        since no seed project needs coin; ready the moment one does."""
        if not ECONOMY_ENABLED or not self._mint_active():
            return
        c = self.civilization
        for p in c["districtProjects"].values():
            if not p or "coin" not in (p.get("needs") or {}):
                continue
            need = p["needs"]["coin"]
            have = p["contributed"].get("coin", 0)
            if have >= need:
                continue
            avail = c["stockpile"].get("coin", 0)
            take = min(need - have, avail)
            if take <= 0:
                continue
            c["stockpile"]["coin"] = avail - take
            p["contributed"]["coin"] = have + take

    def _resource_price(self, resource_id):
        """Deterministic price in gold, no persisted state. base * a scarcity
        multiplier derived from (a) the average district-stock ratio for this
        resource village-wide (ECOLOGY_ENABLED) and (b) the village stockpile
        depth relative to storage capacity (GOODS_ENABLED) -- either signal
        alone is enough to move price; both compound. Gold and coin are both
        priced at 1 (a currency never prices itself, whichever one is
        currently the active trade medium -- see _active_currency)."""
        if resource_id == "gold" or resource_id == "coin":
            return 1
        base = BASE_PRICE.get(resource_id, 2)
        scarcity = 1.0  # 1.0 = comfortable stock, 0.0 = fully depleted
        signals = 0
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            ratios = []
            for stocks in self.civilization["districtStocks"].values():
                if resource_id in stocks:
                    max_s = self._stock_max(resource_id)
                    ratios.append(min(1.0, stocks[resource_id] / max_s) if max_s else 1.0)
            if ratios:
                scarcity = min(scarcity, sum(ratios) / len(ratios))
                signals += 1
        if GOODS_ENABLED and resource_id in EDIBLE_RESOURCES:
            cap = self._storage_capacity(resource_id)
            if cap:
                c = self.civilization
                held = c["stockpile"].get(resource_id, 0) + \
                    sum(a["resources"].get(resource_id, 0) for a in self.agents)
                scarcity = min(scarcity, min(1.0, held / cap))
                signals += 1
        if signals == 0:
            scarcity = 1.0
        mult = 1.0 + (1.0 - scarcity) * (PRICE_SCARCITY_MULT - 1.0)
        return max(PRICE_MIN, round(base * mult))

    def _format_prices_for_prompt(self):
        """One compact prompt line, rendered only while a market exists (flag-
        off / no-market prompts are unaffected)."""
        if not self._market_active():
            return None
        ids = sorted(self.civilization["resourceRegistry"].keys())
        parts = [f"{rid} {self._resource_price(rid)}g" for rid in ids if rid not in ("gold", "coin")]
        return ", ".join(parts) if parts else None

    def _relationship_between(self, agent, other_name):
        return agent["relationships"].get(other_name, "neutral")

    def _priced_trade_terms(self, seller, buyer_name, resource_id):
        """Returns (unit_price, refused, refusal_reason). Relationship
        modifiers apply from the SELLER's perspective (their opinion of the
        buyer): ally = discount, rival = surcharge, and a rival trade is
        refused outright if the buyer can't afford even the surcharged price
        -- never for any other reason, so barter/other partners/waiting for
        price to move all remain reachable."""
        price = self._resource_price(resource_id)
        rel = self._relationship_between(seller, buyer_name)
        if rel == "ally":
            price = max(PRICE_MIN, round(price * ALLY_PRICE_DISCOUNT))
        elif rel == "rival":
            price = max(PRICE_MIN, round(price * RIVAL_PRICE_SURCHARGE))
        return price, rel

    def _priced_trade(self, agent, target, resource_id):
        """Priced exchange (market active): target buys 1 unit of resource_id
        from agent at the relationship-adjusted price, settled in the active
        currency (_active_currency: coin once a mint exists, else gold -- the
        pre-mint, byte-identical default). Refusals are NEVER silent: every
        one sets lastTradeRejection (read by the next prompt) and logs an
        in-world activity line. Deterministic escapes: a rival refusal
        doesn't touch either agent's inventory (both keep everything they
        came with -- gather more of the active currency, wait for price to
        move, or approach a different, non-rival partner); an ally/neutral
        trade the buyer can't afford falls back to the barter swap (never
        blocked just because the currency is short)."""
        price, rel = self._priced_trade_terms(agent, target["name"], resource_id)
        currency = self._active_currency()
        suffix = "c" if currency == "coin" else "g"
        buyer_funds = target["resources"].get(currency, 0)
        if rel == "rival" and buyer_funds < price:
            reason = (f"{target['name']} can't afford {agent['name']}'s rival surcharge "
                      f"for {resource_id} ({price}{suffix}, has {buyer_funds}{suffix})")
            agent["lastTradeRejection"] = {"reason": reason, "frame": self.frameTick}
            self._push_activity(f"{agent['name']} refused to trade with his rival {target['name']}")
            return f"{agent['name']} refused to trade with rival {target['name']}"
        if buyer_funds < price:
            # Ally/neutral, currency short: barter fallback (the deterministic
            # escape -- a thin coin/gold supply never blocks trade outright).
            agent["resources"][resource_id] -= 1
            target["resources"][resource_id] = target["resources"].get(resource_id, 0) + 1
            self._nudge_ally(agent, target["name"])
            self._nudge_ally(target, agent["name"])
            self._push_memory(target, f"Received {resource_id} from {agent['name']} (bartered, short on {currency})")
            agent["lastTradeRejection"] = None
            self._emit_shipment(agent.get("currentDistrict"), target.get("currentDistrict"), resource_id)
            return f"{agent['name']} bartered {resource_id} to {target['name']} (short on {currency})"
        agent["resources"][resource_id] -= 1
        target["resources"][resource_id] = target["resources"].get(resource_id, 0) + 1
        target["resources"][currency] = buyer_funds - price
        agent["resources"][currency] = agent["resources"].get(currency, 0) + price
        self._nudge_ally(agent, target["name"])
        self._nudge_ally(target, agent["name"])
        self._push_memory(target, f"Bought {resource_id} from {agent['name']} for {price}{suffix}")
        agent["lastTradeRejection"] = None
        self._emit_shipment(agent.get("currentDistrict"), target.get("currentDistrict"), resource_id)
        term = f" ({rel} price)" if rel != "neutral" else ""
        self._push_activity(
            f"{target['name']} bought {resource_id} from {agent['name']} for {price}{suffix}{term}")
        return f"{agent['name']} sold {resource_id} to {target['name']} for {price}{suffix}"

    def _find_house_to_claim(self, agent):
        """The nearest WORKING, unclaimed house -- built houses first-come."""
        c = self.civilization
        candidates = [s for s in c["structures"]
                      if (self._get_structure_function(s.get("type")) or {}).get("houses")
                      and not s.get("isRuin") and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                      and not s.get("homeOf")]
        if not candidates:
            return None
        return min(candidates, key=lambda s: _dist(agent["x"], agent["y"], s["x"], s["y"]))

    def _claim_home(self, agent, structure):
        """First-come home claim (called on build/repair-from-ruin, and by an
        explicit claim). Releases any previous home the agent held (an agent
        can hold only one home at a time) and logs it. `homeOf`/`prevHomeOf`
        are inheritance breadcrumbs only -- Phase F consumes them."""
        old_id = agent.get("homeStructureId")
        if old_id and old_id != structure["id"]:
            prev = next((s for s in self.civilization["structures"] if s["id"] == old_id), None)
            if prev and prev.get("homeOf") == agent["name"]:
                prev["homeOf"] = None
        structure["homeOf"] = agent["name"]
        agent["homeStructureId"] = structure["id"]
        agent["lastHomelessNudgeFrame"] = None
        name = structure.get("name") or structure.get("type")
        self._push_activity(f"{agent['name']} claimed the {name} in {structure.get('districtId')} as home")

    def _maybe_auto_claim_home(self, agent, structure):
        """Called right after a house is built or rebuilt from ruin: the
        builder/repairer claims it first-come if they're homeless. Doesn't
        force a claim on someone who already has a home -- leaves the new
        house open for the next homeless villager (_find_house_to_claim /
        the homeless nudge)."""
        if not ECONOMY_ENABLED:
            return
        if (self._get_structure_function(structure.get("type")) or {}).get("houses") \
                and not agent.get("homeStructureId"):
            self._claim_home(agent, structure)

    def _agent_wealth(self, agent):
        """gold + coin + goods valued at current prices. Coin (once minted)
        counts unconditionally, same as gold -- a currency is always valuable,
        unlike other goods which need an active market to have a price (0
        signal from goods when no market exists -- matching barter-only
        reality)."""
        currency_total = agent["resources"].get("gold", 0) + agent["resources"].get("coin", 0)
        if not self._market_active():
            return currency_total
        value = currency_total
        for rid, amt in agent["resources"].items():
            if rid in ("gold", "coin") or amt <= 0:
                continue
            value += amt * self._resource_price(rid)
        return value

    def _wealth_gini(self):
        """Standard Gini coefficient over per-agent wealth (gold + coin +
        priced goods). 0 = perfect equality, ~1 = maximal inequality. None
        when there are no agents (never during a live session)."""
        if not self.agents:
            return None
        values = sorted(self._agent_wealth(a) for a in self.agents)
        n = len(values)
        total = sum(values)
        if total <= 0:
            return 0.0
        cum = sum((i + 1) * v for i, v in enumerate(values))
        return round((2 * cum) / (n * total) - (n + 1) / n, 3)

    def _deposit_produced(self, resource, amount, type_id, district_id=None):
        c = self.civilization
        if resource not in c["resourceRegistry"]:
            return
        if ECOLOGY_ENABLED:
            self._ensure_district_stocks()
            if district_id and self._district_stock(district_id, resource) is not None:
                self._add_district_stock(district_id, resource, amount)
            else:
                dids = [did for did, stocks in c["districtStocks"].items()
                        if resource in stocks]
                if dids:
                    share = max(1, amount // len(dids))
                    for did in dids:
                        self._add_district_stock(did, resource, share)
                else:
                    c["stockpile"][resource] = c["stockpile"].get(resource, 0) + amount
        else:
            c["stockpile"][resource] = c["stockpile"].get(resource, 0) + amount
        name = self._structure_display_name(type_id)
        where = f" in {district_id}" if district_id else ""
        self._push_activity(f"{name} produced {amount} {resource}{where}")

    def _tick_structure_effects(self):
        """Apply tick-time produces (and log every firing). Boosts/unlocks/houses
        are query-time via the registry helpers above."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return
        c = self.civilization
        last_fire = c.setdefault("effectLastFire", {})
        built_types = {s["type"] for s in c["structures"]}
        for type_id in built_types:
            fn = self._get_structure_function(type_id)
            for prod in fn.get("produces") or []:
                resource = prod.get("resource")
                every = prod.get("every_ticks", 600)
                fire_key = f"{type_id}:{resource}:{prod.get('scope', 'village')}"
                if self.frameTick - last_fire.get(fire_key, -every) < every:
                    continue
                scope = prod.get("scope", "village")
                amount_each = prod.get("amount", 1)
                # Phase C: only structures in working condition produce
                # (_working_structure_count == _structure_count with GOODS off).
                if scope == "district":
                    for did in {s.get("districtId") for s in c["structures"] if s["type"] == type_id}:
                        if STRUCTURE_UPGRADES_ENABLED:
                            count = self._weighted_working_count(type_id, did)
                        else:
                            count = self._working_structure_count(type_id, did)
                        if count <= 0:
                            continue
                        total = int(amount_each * count)
                        if total < 1:
                            continue
                        self._deposit_produced(resource, total, type_id, did)
                        self._effect_period_fired += 1
                else:
                    if STRUCTURE_UPGRADES_ENABLED:
                        count = self._weighted_working_count(type_id)
                    else:
                        count = self._working_structure_count(type_id)
                    if count <= 0:
                        continue
                    total = int(amount_each * count)
                    if total < 1:
                        continue
                    self._deposit_produced(resource, total, type_id)
                    self._effect_period_fired += 1
                last_fire[fire_key] = self.frameTick

    # --- Phase C tick mechanics (GOODS_ENABLED): spoilage / decay / disaster / shelter ---
    def _tick_goods(self):
        """The slow goods tick (GOODS_TICK_FRAMES): season bookkeeping,
        edible spoilage beyond storage capacity, structure decay, and the
        rare disaster. All deterministic -- no LLM involvement."""
        if not GOODS_ENABLED:
            return
        season = self._current_season()
        if season != self._last_season:
            self._last_season = season
            note = " -- district stocks will not regrow until spring" if season == "winter" else ""
            self._push_activity(f"The season turns: {season} begins{note}")
            self._log_benchmark("season_turn", SEASONS.index(season), {"season": season})
            self._mark_all_context_dirty()
        self._tick_spoilage()
        self._tick_structure_decay()
        self._tick_weather()
        self._maybe_disaster()
        self._tick_comfort_consumption()
        self._prune_shipments()
        self._mark_civ_dirty("stockpile", "season")
        self._mark_top_dirty("weather")

    def _tick_comfort_consumption(self):
        if (self.frameTick // GOODS_TICK_FRAMES) % COMFORT_EVERY_N_GOODS_TICKS != 0:
            return
        stock = self.civilization["stockpile"]
        consumed = 0
        for agent in self._living_agents():
            resource = next((r for r in ("pottery", "dried_fish") if stock.get(r, 0) > 0), None)
            if not resource:
                break
            stock[resource] -= 1
            agent["hunger"] = min(100, agent.get("hunger", 0) + 2)
            agent["health"] = min(100, agent.get("health", 0) + 1)
            consumed += 1
        if consumed:
            self._push_activity(f"Village comforts consumed: {consumed} crafted goods")

    def _tick_spoilage(self):
        """Edibles beyond village storage capacity rot: SPOILAGE_RATIO of the
        overflow per goods tick (min 1), stockpile first, then the largest
        holders -- never below EDIBLE_RESERVE, so spoilage cannot starve
        anyone. The escape is storage: build a structure with a `stores`
        effect (granary), or eat/contribute the surplus."""
        c = self.civilization
        for rid in EDIBLE_RESOURCES:
            cap = self._storage_capacity(rid)
            stock = c["stockpile"].get(rid, 0)
            held = sum(a["resources"].get(rid, 0) for a in self.agents)
            overflow = stock + held - cap
            if overflow <= 0:
                continue
            # Sovereign God mode Phase 5: the divine spoilage multiplier
            # scales the ordinary computed to_spoil amount (base_spoil,
            # which already floors at 1 to guarantee normal-rate spoilage)
            # BEFORE the existing min(overflow, ...) bound, so spoilage can
            # never exceed the eligible overflow -- an active 0.0 multiplier
            # means no spoilage at all that tick, overriding the normal
            # floor-of-1 guarantee.
            base_spoil = max(1, int(overflow * SPOILAGE_RATIO))
            to_spoil = min(overflow, math.floor(base_spoil * self._divine_modifier("spoilage_multiplier")))
            spoiled = min(to_spoil, stock)
            if spoiled > 0:
                c["stockpile"][rid] = stock - spoiled
            while spoiled < to_spoil:
                holders = [a for a in self.agents
                           if a["resources"].get(rid, 0) > EDIBLE_RESERVE]
                if not holders:
                    break
                top = max(holders, key=lambda a: a["resources"].get(rid, 0))
                top["resources"][rid] -= 1
                spoiled += 1
            if spoiled <= 0:
                continue
            self._spoiled_period += spoiled
            reason = (f"{spoiled} {rid} spoiled -- the village holds more than its "
                      f"storage capacity ({cap}). Build storage (a granary or a "
                      f"blueprint with a stores effect) or eat/contribute the surplus")
            c["lastSpoilage"] = {"reason": reason, "frame": self.frameTick}
            self._push_activity(reason[0].upper() + reason[1:])

    def _apply_structure_condition_delta(self, s, delta):
        """Shared condition-delta + ruin-transition helper. Used by BOTH the
        passive per-goods-tick decay below (always a negative delta) AND the
        Sovereign God mode structure_condition miracle (docs/archive/plan-sovereign-
        god-mode-v2.md Phase 4: "Reuse condition/ruin helpers ... so disrepair
        and ruin transitions fire with their normal narration"). A no-op on
        an already-ruined structure (cond <= 0) -- callers that can reach a
        ruin (the miracle) reject it earlier in validation instead. Returns
        the new condition. Must be called with self.lock already held (both
        callers already hold it: the tick loop and god_apply)."""
        cond = s.get("condition", 100.0)
        if cond <= 0:
            return cond
        new_cond = min(100.0, cond + delta) if delta >= 0 else max(0.0, cond + delta)
        s["condition"] = new_cond
        name = s.get("name") or s.get("type")
        did = s.get("districtId") or "the village"
        if cond >= STRUCTURE_DISREPAIR_THRESHOLD > new_cond:
            self._push_activity(
                f"The {name} in {did} has fallen into disrepair -- it stops "
                f"working until someone uses repair_structure")
        if new_cond <= 0:
            s["isRuin"] = True
            s.setdefault("ruinedSinceFrame", self.frameTick)
            self._push_activity(
                f"The {name} in {did} has collapsed into a ruin! "
                f"repair_structure can rebuild it for half the original materials")
            if ECONOMY_ENABLED and s.get("homeOf"):
                owner = self._find_agent(s["homeOf"])
                if owner and owner.get("homeStructureId") == s["id"]:
                    owner["homeStructureId"] = None
                self._push_activity(f"{s['homeOf']} is left homeless — the {name} they lived in is a ruin")
                s["homeOf"] = None
        if new_cond != cond:
            self._mark_structure_dirty(s)
        return new_cond

    def _tick_structure_decay(self):
        """Structures decay STRUCTURE_DECAY_PER_GOODS_TICK per goods tick.
        Below STRUCTURE_DISREPAIR_THRESHOLD they stop working (produces/
        boosts/unlocks/houses all go through _working_structure_count); at 0
        they collapse into a ruin, rebuildable via repair_structure for half
        the original materials (the deterministic escape)."""
        c = self.civilization
        # Sovereign God mode Phase 5: scales only ordinary passive decay --
        # direct disaster damage and the god_apply structure_condition
        # miracle both call _apply_structure_condition_delta directly with
        # their own delta and are unaffected.
        decay = STRUCTURE_DECAY_PER_GOODS_TICK * self._divine_modifier("structure_decay_multiplier")
        for s in c["structures"]:
            if s.get("condition", 100.0) <= 0:
                continue
            self._apply_structure_condition_delta(s, -decay)

    def _tick_structure_health_benchmark(self):
        """Logs a `structure_health` benchmark every goods tick so village-wide
        structural collapse (like the 2026-07 incident where 54/66 structures
        silently rotted into ruins with zero automated visibility) shows up in
        benchmarks.jsonl automatically during any soak/test run, instead of
        requiring an ad-hoc /state query to discover after the fact."""
        if not GOODS_ENABLED:
            return
        structures = self.civilization["structures"]
        total = len(structures)
        if total == 0:
            return
        working = 0
        disrepaired = 0
        ruined = 0
        for s in structures:
            if s.get("isRuin"):
                ruined += 1
                continue
            cond = s.get("condition", 100)
            if cond >= STRUCTURE_DISREPAIR_THRESHOLD:
                working += 1
            elif cond > 0:
                disrepaired += 1
        self._log_benchmark(
            "structure_health",
            round(working / total, 2),
            {"total": total, "working": working, "disrepaired": disrepaired, "ruined": ruined},
        )

    def _maybe_disaster(self):
        """Rare random structure damage, so decay isn't perfectly predictable
        and repair stays relevant even in a well-kept village. Logged
        dramatically; the standard escape applies (repair, or rebuild from
        the ruin).

        WEATHER_ENABLED off: byte-identical to the pre-Phase-4 behavior --
        DISASTER_PROB per goods tick, any structure anywhere.

        WEATHER_ENABLED on: damage only fires while civilization["weather"]
        is in the "storm" state (STORM_DISASTER_PROB per goods tick while
        storming), preferring a structure inside one of the storm's
        districts (falling back to any structure if none qualify there) --
        so the sky effect and the damage event agree on where the storm is.

        Rate calibration (also documented in specs/08-systems-economy.md):
        model one clear->gathering->(storm->clearing | nothing)->clear cycle
        per season with P_storm = clip(WEATHER_BASE_STORM_CHANCE *
        WEATHER_SEASON_STORMINESS[season], 0.05, 0.95) and season-scaled
        clear dwell. Expected cycle length
        E[cycle] = E[clear] + E[gathering] + P_storm*(E[storm]+E[clearing]),
        and the storm-time fraction of that cycle is
        P_storm*E[storm] / E[cycle] (note the P_storm factor on the
        numerator too -- only P_storm of cycles pass through storm at all).
        With the shipped constants this comes out to ~2.2% (spring), ~0.7%
        (summer), ~3.1% (autumn), ~1.9% (winter) of goods ticks -- averaging
        ~1.97% across the four equal-length seasons (equal weight is valid
        since SEASON_FRAMES is the same for all four). The naive analytic
        pick (STORM_DISASTER_PROB = 0.0049/0.0197 ~= 0.25) undershot in a
        200k-goods-tick empirical run (mean on-rate ~0.0040 vs the measured
        legacy off-rate ~0.0051 -- likely correlated-timing effects the
        independent-cycle model doesn't capture), so the constant was tuned
        empirically to STORM_DISASTER_PROB = 0.32, which lands the 5-seed
        mean on-rate at ~0.0051 -- matching the mean off-rate to within ~1%.
        See the Phase 4 report for the measured on/off rates."""
        c = self.civilization
        candidates = [s for s in c["structures"] if s.get("condition", 100) > 0]
        if not candidates:
            return
        if WEATHER_ENABLED:
            w = c.get("weather") or {}
            if w.get("state") != "storm":
                return
            if random.random() >= STORM_DISASTER_PROB:
                return
            storm_districts = set(w.get("districts") or [])
            localized = [s for s in candidates if s.get("districtId") in storm_districts]
            pool = localized or candidates
        else:
            if random.random() >= DISASTER_PROB:
                return
            pool = candidates
        s = random.choice(pool)
        dmg = random.randint(*DISASTER_DAMAGE)
        s["condition"] = max(0.0, s.get("condition", 100.0) - dmg)
        name = s.get("name") or s.get("type")
        did = s.get("districtId") or "the village"
        line = (f"DISASTER! A storm tears through the {name} in {did} -- "
                f"{dmg} damage (condition {int(s['condition'])})")
        ruined = s["condition"] <= 0
        if ruined:
            s["isRuin"] = True
            line += "; it lies in ruins"
        self._push_activity(line)
        self._log_benchmark("disaster_damage", dmg,
                            {"structure": s.get("type"), "district": did})
        chron = f"A storm ruined the {name} in {did}." if ruined \
            else f"A storm damaged the {name} in {did}."
        self._push_chronicle(chron, kind="disaster")

    def _env_shelter_capacity(self):
        """ENV_EFFECTS_ENABLED: sum of `shelter.capacity` across every working
        structure whose function declares a shelter effect. Stacks with the
        implicit `houses` beds (a block declaring both counts both)."""
        c = self.civilization
        total = 0
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            shelter = fn.get("shelter")
            if not isinstance(shelter, dict):
                continue
            cap = shelter.get("capacity")
            if not isinstance(cap, int) or cap <= 0:
                continue
            count = sum(1 for s in c["structures"]
                        if s["type"] == type_id
                        and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)
            total += cap * count
        return total

    def _tick_shelter(self):
        """Nightly (every DAY_FRAMES): each working house shelters
        HOUSE_SHELTER_OCCUPANTS villagers (nearest to a house first);
        everyone else spends the night outside and loses a little hunger --
        a surfaced nudge, never a hard punishment (floored at
        SHELTER_HUNGER_FLOOR, above the starvation band). This is what makes
        houses consumed nightly instead of just population math.
        Phase E (ECONOMY_ENABLED): a homeowner is guaranteed a bed in THEIR
        OWN house regardless of proximity -- property has to mean something
        mechanically, not just log a claim message. Remaining beds (any house
        minus its live-in owner's reserved bed) go to the homeless, nearest
        first, exactly as before."""
        if not GOODS_ENABLED or not SURVIVAL_ENABLED:
            return
        c = self.civilization
        house_structs = []
        for type_id in {s["type"] for s in c["structures"]}:
            if (self._get_structure_function(type_id) or {}).get("houses"):
                house_structs.extend(
                    s for s in c["structures"]
                    if s["type"] == type_id
                    and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD)
        living = self._living_agents()
        slots = len(house_structs) * HOUSE_SHELTER_OCCUPANTS
        if ENV_EFFECTS_ENABLED:
            slots += self._env_shelter_capacity()
        # Corpses stay in self.agents for burial; only the living need beds.
        if slots >= len(living):
            self._push_activity("Night falls -- every villager has a roof tonight")
            return

        sheltered_names = set()
        remaining_slots = slots
        if ECONOMY_ENABLED:
            owned_ids = {s["id"] for s in house_structs if s.get("homeOf")}
            for a in living:
                if a.get("homeStructureId") in owned_ids:
                    sheltered_names.add(a["name"])
            remaining_slots = max(0, slots - len(sheltered_names))

        def dist_to_house(a):
            if not house_structs:
                return float("inf")
            return min(_dist(a["x"], a["y"], s["x"], s["y"]) for s in house_structs)

        others = [a for a in living if a["name"] not in sheltered_names]
        sheltered_names.update(a["name"] for a in
                               sorted(others, key=dist_to_house)[:remaining_slots])
        unsheltered = [a for a in living if a["name"] not in sheltered_names]
        penalized = 0
        for a in unsheltered:
            if a["incapacitated"] or a["hunger"] <= SHELTER_HUNGER_FLOOR:
                continue
            a["hunger"] = max(SHELTER_HUNGER_FLOOR, a["hunger"] - SHELTER_HUNGER_PENALTY)
            a["lastShelterNote"] = {
                "reason": (f"you spent the night outside -- {len(house_structs)} working "
                           f"house(s) shelter only {slots} of {len(living)} villagers"),
                "frame": self.frameTick,
            }
            if ECONOMY_ENABLED and not a.get("homeStructureId") \
                    and (self.frameTick - (a.get("lastHomelessNudgeFrame") or -HOMELESS_NUDGE_FRAMES)) \
                    >= HOMELESS_NUDGE_FRAMES:
                a["lastHomelessNudgeFrame"] = self.frameTick
            penalized += 1
        if penalized:
            self._push_activity(
                f"Night falls -- {penalized} villager(s) had no shelter "
                f"({len(house_structs)} working houses, {slots} beds for {len(living)})")

    # --- Phase C: repair_structure (the decay escape hatch) ---
    def _find_repair_target(self, agent, target):
        """Resolve a repair target: explicit structure id/type/name first
        (worst-condition match wins), else the worst STANDING damaged structure
        (cheap 1-unit upkeep that keeps it working -- the Phase C test is
        'repairs a decaying structure BEFORE it collapses'), falling back to
        ruins (expensive half-rebuild) only when nothing standing is damaged.
        2026-07-07 audit: plain min(condition) always chose a ruin, so every
        repair turn hit the multi-resource rebuild cost and failed. District
        preference applies within each tier."""
        c = self.civilization
        damaged = [s for s in c["structures"]
                   if s.get("isRuin") or s.get("condition", 100) < 100]
        if not damaged:
            return None
        if target:
            t = str(target).strip().lower()
            matches = [s for s in damaged
                       if str(s.get("id")) == t
                       or (s.get("type") or "").lower() == t
                       or (s.get("name") or "").lower() == t]
            if matches:
                return min(matches, key=lambda s: s.get("condition", 100))
        standing = [s for s in damaged
                    if not s.get("isRuin") and s.get("condition", 100) > 0]
        tier = standing or damaged
        local = [s for s in tier if s.get("districtId") == agent.get("currentDistrict")]
        pool = local or tier
        return min(pool, key=lambda s: s.get("condition", 100))

    def _repair_cost(self, structure):
        """Normal repair: 1 unit of the structure's primary material per
        +REPAIR_CONDITION_RESTORE. Ruin rebuild: half the original needs
        (min 1 each) -- deliberately cheaper than starting a new project."""
        c = self.civilization
        tmpl = c["projectRegistry"].get(structure.get("type")) \
            or PROJECT_TEMPLATES.get(structure.get("type")) or {}
        needs = tmpl.get("needs") or {"wood": 2}
        if structure.get("isRuin") or structure.get("condition", 100) <= 0:
            return {res: max(1, amt // 2) for res, amt in needs.items()}
        if c["stockpile"].get("planks", 0) > 0:
            return {"planks": 1}
        primary = next(iter(needs), "wood")
        return {primary: 1}

    def _repair_structure(self, agent, target):
        s = self._find_repair_target(agent, target)
        if not s:
            agent["lastRepairRejection"] = {
                "reason": "nothing needs repair right now", "frame": self.frameTick}
            return f"{agent['name']} found nothing that needs repair"
        cost = self._repair_cost(s)
        name = s.get("name") or s.get("type")
        paid, missing = self._pay_local_cost(agent, cost)
        if missing:
            cost_str = ", ".join(f"{amt} {res}" for res, amt in cost.items())
            agent["lastRepairRejection"] = {
                "reason": (f"repairing the {name} needs {cost_str} -- you, your settlement store, "
                           f"and the village stockpile together lack {', '.join(missing)}"),
                "frame": self.frameTick}
            return f"{agent['name']} lacks {', '.join(missing)} to repair the {name}"
        store_parts, stock_parts = paid
        if store_parts:
            self._push_activity(
                f"The {self._settlement_for_agent(agent)} settlement store supplied "
                f"{', '.join(store_parts)} for {agent['name']}'s repair of the {name}")
        if stock_parts:
            self._push_activity(
                f"The village stockpile supplied {', '.join(stock_parts)} for "
                f"{agent['name']}'s repair of the {name}")
        was_ruin = bool(s.get("isRuin")) or s.get("condition", 100) <= 0
        if was_ruin:
            s["condition"] = 100.0
            s["isRuin"] = False
            summary = f"{agent['name']} rebuilt the {name} from its ruins in {s.get('districtId')}"
            self._maybe_auto_claim_home(agent, s)
        else:
            s["condition"] = min(100.0, s.get("condition", 100.0) + REPAIR_CONDITION_RESTORE)
            summary = (f"{agent['name']} repaired the {name} in {s.get('districtId')} "
                       f"(condition {int(s['condition'])})")
        agent["lastRepairRejection"] = None
        self._log_benchmark("structure_repaired", int(s["condition"]),
                            {"structure": s.get("type"), "ruin_rebuild": was_ruin})
        return summary

    # --- Structure upgrades (STRUCTURE_UPGRADES_ENABLED) ---
    def _structure_level(self, structure):
        if not STRUCTURE_UPGRADES_ENABLED:
            return MAX_STRUCTURE_LEVEL
        return int(structure.get("level") or 1)

    def _visual_tier_index(self, level):
        idx = 0
        for i, thresh in enumerate(UPGRADE_TIERS):
            if level >= thresh:
                idx = i
        return idx

    def _type_has_unmaxed_instance(self, type_):
        if not STRUCTURE_UPGRADES_ENABLED:
            return False
        return any(
            s.get("type") == type_
            and not s.get("isRuin")
            and self._structure_level(s) < MAX_STRUCTURE_LEVEL
            for s in self.civilization["structures"]
        )

    def _upgradeable_structures_brief(self):
        if not STRUCTURE_UPGRADES_ENABLED:
            return []
        out = []
        for s in self.civilization["structures"]:
            if s.get("isRuin"):
                continue
            lvl = self._structure_level(s)
            if lvl < MAX_STRUCTURE_LEVEL:
                out.append({
                    "id": s.get("id"),
                    "type": s.get("type"),
                    "name": s.get("name") or s.get("type"),
                    "level": lvl,
                    "district": s.get("districtId"),
                })
        return out

    def _find_upgrade_target(self, agent, target):
        pool = [s for s in self.civilization["structures"]
                if not s.get("isRuin")
                and self._structure_level(s) < MAX_STRUCTURE_LEVEL
                and (not GOODS_ENABLED or s.get("condition", 100) > 0)]
        if not pool:
            return None
        if target:
            t = str(target).strip().lower()
            matches = [s for s in pool
                       if str(s.get("id")) == t
                       or (s.get("type") or "").lower() == t
                       or (s.get("name") or "").lower() == t]
            if matches:
                return min(matches, key=lambda s: self._structure_level(s))
        local = [s for s in pool if s.get("districtId") == agent.get("currentDistrict")]
        pool2 = local or pool
        return min(pool2, key=lambda s: self._structure_level(s))

    def _upgrade_cost(self, structure):
        level = self._structure_level(structure)
        tmpl = (self.civilization["projectRegistry"].get(structure.get("type"))
                or PROJECT_TEMPLATES.get(structure.get("type")) or {})
        needs = tmpl.get("needs") or {"wood": 2}
        primary = next(iter(needs), "wood")
        amt = max(1, UPGRADE_COST_BASE * max(1, level // UPGRADE_STAT_STEP))
        return {primary: amt}

    def _sprite_dimensions(self, sprite):
        if not sprite or not isinstance(sprite.get("grid"), list) or not sprite["grid"]:
            return 0, 0
        grid = sprite["grid"]
        return len(grid), max(len(str(r)) for r in grid)

    def _structure_footprint(self, s):
        """Drawn footprint in world px, mirroring the client (sprites.js
        getStructureRenderSize/getStructureGrid/upgradedSeedGrid): take the
        max rows/cols across every candidate source (conservative -- the
        client's exact path can vary by sprite/degenerate-check state), then
        scale by STRUCTURE_PX_SCALE and renderScale."""
        candidates = []
        sprite = s.get("sprite")
        is_degenerate = self.d.get("sprite_spec_is_degenerate", lambda sp: False)
        if sprite and not is_degenerate(sprite):
            rows, cols = self._sprite_dimensions(sprite)
            if rows and cols:
                candidates.append((rows, cols))
        type_id = s.get("type")
        if type_id in SEED_SPRITE_DIMS:
            seed_rows, seed_cols = SEED_SPRITE_DIMS[type_id]
            factor = min(max(1, int(s.get("visualTier") or 1)), 3)
            candidates.append((seed_rows * factor, seed_cols * factor))
        if not candidates:
            candidates.append(PROC_SPRITE_DIMS)
        rows = max(c[0] for c in candidates)
        cols = max(c[1] for c in candidates)
        render_scale = float(s.get("renderScale") or 1.0)
        w = cols * STRUCTURE_PX_SCALE * render_scale
        h = rows * STRUCTURE_PX_SCALE * render_scale
        return w, h

    def _structure_rect(self, s):
        w, h = self._structure_footprint(s)
        return s.get("x", 0), s.get("y", 0), w, h

    def _footprint_rects_collide(self, a, b):
        """AABB overlap test for two (x, y, w, h) rects, inflated by the
        structure gap constants. Distinct from the module-level
        `_rects_overlap` (x1/y1/x2/y2 dict form used for district bounds)."""
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        return (ax < bx + bw + STRUCTURE_GAP_X and bx < ax + aw + STRUCTURE_GAP_X
                and ay < by + bh + STRUCTURE_GAP_Y and by < ay + ah + STRUCTURE_GAP_Y)

    def _structures_overlapping(self, a, b):
        return self._footprint_rects_collide(self._structure_rect(a), self._structure_rect(b))

    def _farm_plot_tier_sprite(self, structure, tier_idx, palette):
        rows = min(14, 6 + tier_idx * 2)
        cols = min(14, 8 + tier_idx * 2)
        key = f"{structure.get('id')}|{tier_idx}"
        h = sum(ord(c) * (i + 1) for i, c in enumerate(key)) & 0xFFFFFFFF
        grid = []
        for y in range(rows):
            chars = []
            for x in range(cols):
                if x in (0, cols - 1) or y in (0, rows - 1):
                    chars.append(".")
                elif (y // 2) % 2 == 0:
                    chars.append("a" if (x + y + h) % 4 else "b")
                else:
                    chars.append("c" if (x + y) % 3 == 0 else "b")
            grid.append("".join(chars))
        return {"palette": palette[:5], "grid": grid}

    def _procedural_tier_sprite(self, structure, tier_idx):
        """Deterministic bigger pixel grid for a visual tier (no LLM required)."""
        type_id = structure.get("type") or ""
        seed_palette = SEED_UPGRADE_PALETTES.get(type_id)
        if type_id == "farm_plot" and seed_palette:
            return self._farm_plot_tier_sprite(structure, tier_idx, seed_palette)
        palettes = [
            ["#8B5A2B", "#C62828", "#F5E6C8"], ["#78909C", "#37474F", "#FFD54F"],
            ["#A1887F", "#4E342E", "#AED581"], ["#90A4AE", "#B71C1C", "#E3F2FD"],
        ]
        key = f"{structure.get('type')}|{structure.get('id')}|{tier_idx}"
        h = sum(ord(c) * (i + 1) for i, c in enumerate(key)) & 0xFFFFFFFF
        palette = seed_palette if seed_palette else palettes[h % len(palettes)]
        rows = min(14, 6 + tier_idx * 2)
        cols = min(14, 6 + tier_idx * 2)
        grid = []
        for y in range(rows):
            chars = []
            for x in range(cols):
                if y < max(1, rows // 3):
                    ch = "b" if (x + y + h) % 3 else "a"
                elif y > rows * 2 // 3 and cols // 3 <= x <= cols * 2 // 3:
                    ch = "c"
                elif (x + y + h) % 5 == 0:
                    ch = "c"
                else:
                    ch = "a"
                chars.append(ch)
            grid.append("".join(chars))
        return {"palette": palette, "grid": grid}

    def _expand_sprite_to_tier(self, structure, tier_idx):
        current = structure.get("sprite")
        if current and current.get("grid"):
            rows, cols = self._sprite_dimensions(current)
            target_rows = min(14, max(rows + 2, rows + tier_idx * 2))
            target_cols = min(14, max(cols + 2, cols + tier_idx * 2))
            palette = list(current.get("palette") or ["#8B5A2B", "#C62828", "#F5E6C8"])[:5]
            old_grid = [str(r) for r in current["grid"]]
            new_grid = []
            for y in range(target_rows):
                if y < len(old_grid):
                    row = old_grid[y]
                    row = row + "a" * max(0, target_cols - len(row))
                    row = row[:target_cols]
                else:
                    row = "a" * target_cols
                new_grid.append(row)
            return {"palette": palette, "grid": new_grid}
        return self._procedural_tier_sprite(structure, tier_idx)

    def _apply_visual_tier(self, structure, new_tier_idx):
        structure["visualTier"] = new_tier_idx + 1
        structure["renderScale"] = round(1.0 + new_tier_idx * 0.25, 2)
        structure["sprite"] = self._expand_sprite_to_tier(structure, new_tier_idx)
        if new_tier_idx >= len(UPGRADE_TIERS) - 1:
            base_name = structure.get("name") or structure.get("type")
            if "Mega" not in str(base_name):
                structure["name"] = f"Mega {base_name}"

    def _structure_upgrade_weight(self, structure):
        """Effective contribution of a structure to produces/boosts (1-10)."""
        if not STRUCTURE_UPGRADES_ENABLED:
            return 1
        return max(1, self._structure_level(structure) // UPGRADE_STAT_STEP)

    def _weighted_working_count(self, type_id, district_id=None):
        total = 0.0
        for s in self.civilization["structures"]:
            if s.get("type") != type_id:
                continue
            if district_id and s.get("districtId") != district_id:
                continue
            if GOODS_ENABLED and s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                continue
            if s.get("isRuin"):
                continue
            total += self._structure_upgrade_weight(s)
        return total

    def _pay_upgrade_cost(self, agent, cost, name):
        paid, missing = self._pay_local_cost(agent, cost)
        if missing:
            cost_str = ", ".join(f"{amt} {res}" for res, amt in cost.items())
            agent["lastUpgradeRejection"] = {
                "reason": (f"upgrading {name} needs {cost_str} -- you, your settlement store, "
                           f"and the stockpile together lack {', '.join(missing)}"),
                "frame": self.frameTick,
            }
            return False
        store_parts, stock_parts = paid
        if store_parts:
            self._push_activity(
                f"The {self._settlement_for_agent(agent)} settlement store supplied "
                f"{', '.join(store_parts)} for {agent['name']}'s upgrade of the {name}")
        if stock_parts:
            self._push_activity(
                f"The village stockpile supplied {', '.join(stock_parts)} for "
                f"{agent['name']}'s upgrade of the {name}")
        return True

    def _upgrade_structure(self, agent, target):
        s = self._find_upgrade_target(agent, target)
        if not s:
            agent["lastUpgradeRejection"] = {
                "reason": "no upgradeable structure found", "frame": self.frameTick}
            return f"{agent['name']} found no structure to upgrade"
        level = self._structure_level(s)
        if level >= MAX_STRUCTURE_LEVEL:
            agent["lastUpgradeRejection"] = {
                "reason": f"{s.get('name')} is already at max level",
                "frame": self.frameTick,
            }
            return f"{agent['name']} cannot upgrade {s.get('name')} further"
        cost = self._upgrade_cost(s)
        name = s.get("name") or s.get("type")
        if not self._pay_upgrade_cost(agent, cost, name):
            return f"{agent['name']} lacks resources to upgrade the {name}"
        old_tier = self._visual_tier_index(level)
        new_level = min(MAX_STRUCTURE_LEVEL, level + LEVEL_STEP)
        s["level"] = new_level
        new_tier = self._visual_tier_index(new_level)
        if new_tier > old_tier:
            self._apply_visual_tier(s, new_tier)
            sprite_max = int(self.d.get("SPRITE_GRID_MAX") or 14)
            rows, cols = self._sprite_dimensions(s.get("sprite"))
            rows_at_cap = rows >= sprite_max
            cols_at_cap = cols >= sprite_max
            if rows_at_cap and cols_at_cap:
                # Already at the validator's hard cap in both dimensions: asking for
                # a bigger sprite is unsatisfiable, and asking for a same-size redraw
                # burns an LLM turn for no visual change. The procedural tier sprite
                # from _apply_visual_tier stands.
                agent["spriteDesignTurn"] = None
            else:
                agent["spriteDesignTurn"] = {
                    "structureId": s["id"],
                    "tier": new_tier,
                    "minRows": 0 if rows_at_cap else rows,   # 0 = no growth required
                    "minCols": 0 if cols_at_cap else cols,
                    "structureName": name,
                    "structureType": s.get("type"),
                }
            # The upgrade may have grown this structure's footprint enough to
            # overlap a neighbor; the upgrader becomes the relocator.
            self._enqueue_reorg_for_overlaps(s, preferred_agent=agent)
        self._mark_structure_dirty(s, sprite_changed=(new_tier > old_tier))
        agent["lastUpgradeRejection"] = None
        self._push_activity(f"{agent['name']} upgraded the {name} to level {new_level}")
        self._log_benchmark("structure_upgraded", new_level,
                            {"structure": s.get("type"), "id": s["id"]})
        return f"{agent['name']} upgraded {name} to level {new_level}"

    def _count_sprite_design_failure(self, agent, structure_or_name):
        """Count one failed think-cycle against agent['spriteDesignTurn'].

        Shared by every site where a pending sprite-design turn fails to
        produce an applied submit_structure_sprite -- an engine-side
        validate_sprite_block/degenerate-sprite rejection (_apply_structure_sprite)
        as well as a decision whose action never made it to
        submit_structure_sprite at all (e.g. rejected server-side by
        normalize_decision() and replaced with a _fallback-stamped role
        fallback; see apply_decision). The apply_decision missing-case covers
        _fallback-stamped substitutions only, not infra/network rests such as
        _think_job's bare ``{"action": "rest"}`` with no _fallback stamp.
        Bumps turn["attempts"], writes it back, and once
        SPRITE_DESIGN_MAX_ATTEMPTS is reached clears the turn (the existing
        procedural sprite stays on the structure) and logs the give-up.
        structure_or_name may be the structure dict, a plain name string, or
        None (e.g. the structure was deleted out from under the turn).
        """
        turn = agent.get("spriteDesignTurn")
        if not turn:
            return
        turn["attempts"] = int(turn.get("attempts") or 0) + 1
        agent["spriteDesignTurn"] = turn
        if turn["attempts"] >= SPRITE_DESIGN_MAX_ATTEMPTS:
            agent["spriteDesignTurn"] = None
            if isinstance(structure_or_name, dict):
                name = structure_or_name.get("name") or structure_or_name.get("type")
            else:
                name = structure_or_name
            name = name or turn.get("structureName") or "structure"
            self._push_activity(f"{agent['name']} gave up refining the sprite for the {name}")

    def _apply_structure_sprite(self, agent, sprite):
        turn = agent.get("spriteDesignTurn") or {}
        sid = turn.get("structureId")
        s = next((x for x in self.civilization["structures"] if x.get("id") == sid), None)
        if not s:
            agent["spriteDesignTurn"] = None
            return f"{agent['name']} had no pending sprite design"
        validate = self.d.get("validate_sprite_block")
        min_rows = int(turn.get("minRows") or 0)
        min_cols = int(turn.get("minCols") or 0)
        if validate:
            ok, reason = validate(sprite, min_rows=min_rows, min_cols=min_cols)
        else:
            ok, reason = True, None
        if not ok:
            agent["lastSpriteRejection"] = {"reason": reason, "frame": self.frameTick}
            self._count_sprite_design_failure(agent, s)
            return f"{agent['name']}'s sprite design was rejected ({reason})"
        if self.d.get("sprite_spec_is_degenerate", lambda sp: False)(sprite):
            agent["lastSpriteRejection"] = {
                "reason": "sprite is too flat (use varied colors/pattern, not one solid fill)",
                "frame": self.frameTick,
            }
            self._count_sprite_design_failure(agent, s)
            return f"{agent['name']}'s sprite design was rejected (too flat)"
        s["sprite"] = sprite
        agent["spriteDesignTurn"] = None
        agent["lastSpriteRejection"] = None
        name = s.get("name") or s.get("type")
        self._mark_structure_dirty(s, sprite_changed=True)
        self._push_activity(f"{agent['name']} refined the sprite for the {name}")
        # An agent-refined sprite can also grow the drawn footprint enough to
        # overlap a neighbor -- same reorg trigger as a tier upgrade.
        self._enqueue_reorg_for_overlaps(s, preferred_agent=agent)
        return f"{agent['name']} applied a new larger sprite to the {name}"

    def _population_cap(self):
        c = self.civilization
        base = c.get("basePopulation") or len(self.agents)
        cap = base
        if STRUCTURE_EFFECTS_ENABLED:
            for type_id in {s["type"] for s in c["structures"]} | set(SEED_STRUCTURE_FUNCTIONS):
                fn = self._get_structure_function(type_id)
                houses = fn.get("houses")
                if houses:
                    every_n = houses.get("every_n", HOUSES_PER_NEW_VILLAGER)
                    cap += self._working_structure_count(type_id) // every_n
        if LIFECYCLE_ENABLED:
            # Phase F: once every AGENT_DEFS name is in use (all 12 named
            # slots occupied by long-lived villagers), housing headroom can
            # still exist -- births then use a generated villager (see
            # _next_agent_slot) instead of stalling at the fixed roster size.
            # Without this, _population_cap topping out at len(AGENT_DEFS)
            # would make birth impossible the moment the named roster fills,
            # even with houses to spare.
            return cap
        return min(MAX_ROSTER_SIZE, cap)

    def _type_saturated(self, type_):
        """Soft cap per structure type, derived from what the type actually
        does, so building past the cap is provably waste. Saturated types are
        skipped by role defaults, refused by _start_project_for, and count as
        'exhausted' toward the invention gate. Deliberately counts TOTAL
        structures (not Phase C working ones): a district full of decayed
        houses should be repaired, not built over."""
        if not STRUCTURE_EFFECTS_ENABLED:
            return False
        c = self.civilization
        count = self._structure_count(type_)
        fn = self._get_structure_function(type_)
        houses = fn.get("houses")
        if houses:
            base = c.get("basePopulation") or len(self.agents)
            every_n = houses.get("every_n", HOUSES_PER_NEW_VILLAGER)
            headroom = len(AGENT_DEFS)
            if LIFECYCLE_ENABLED:
                # _population_cap() is uncapped past len(AGENT_DEFS) under
                # this flag (generated villagers can be born once every named
                # slot is full) -- without matching headroom here, the house
                # soft cap would flag "enough houses" before there's actually
                # room for the next birth, throttling population growth for
                # no mechanical reason. Current agent count is the simplest
                # lower bound that tracks any already-realized growth.
                headroom = max(headroom, len(self.agents) + HOUSES_PER_NEW_VILLAGER)
            return count >= (headroom - base) * every_n + 3
        for boost in fn.get("boosts") or []:
            if boost.get("kind") == "gather":
                every_n = boost.get("every_n", FARM_PLOTS_PER_EXTRA)
                max_bonus = boost.get("max_bonus", FARM_YIELD_BONUS_CAP)
                farm_districts = sum(1 for d in c["districts"].values()
                                     if d["kind"] == "farm" and d.get("build_grid"))
                return count >= every_n * max_bonus * max(1, farm_districts)
            if boost.get("kind") == "craft":
                eligible = sum(1 for d in c["districts"].values()
                               if d["kind"] in ("village", "workshop") and d.get("build_grid"))
                return count >= WORKSHOP_DISTRICT_CAP * max(1, eligible)
        if type_ == "wall":
            return count >= WALL_SOFT_CAP
        return count >= CUSTOM_SOFT_CAP

    def _find_structure_spot(self, district_id, footprint=None, ignore_id=None):
        d = self.civilization["districts"].get(district_id)
        grid = d.get("build_grid") if d else None
        if not grid:
            b = d["bounds"] if d else {"x1": 0, "y1": 0}
            return {"x": b["x1"], "y": b["y1"]}
        bounds = d["bounds"]
        fw, fh = footprint if footprint else (8 * STRUCTURE_PX_SCALE, 8 * STRUCTURE_PX_SCALE)
        # Big footprints can shadow slots across district edges, so check
        # every existing structure civilization-wide, not just this district.
        existing = [s for s in self.civilization["structures"] if s.get("id") != ignore_id]
        existing_rects = [self._structure_rect(s) for s in existing]
        cap = grid.get("cap", 30)
        for i in range(cap):
            x = grid["x0"] + (i % grid["cols"]) * grid["dx"]
            y = grid["y0"] + (i // grid["cols"]) * grid["dy"]
            if x < bounds["x1"] or y < bounds["y1"]:
                continue
            if x + fw > bounds["x2"] or y + fh + 14 > bounds["y2"]:
                continue
            candidate = (x, y, fw, fh)
            if any(self._footprint_rects_collide(candidate, r) for r in existing_rects):
                continue
            return {"x": x, "y": y}
        return None  # district's build grid is at capacity

    def _check_civilization_level(self):
        new_level = (self.civilization["completedProjects"] // 3) + 1
        if new_level > self.civilization["level"]:
            self.civilization["level"] = new_level
            self._push_activity(f"Civilization reached level {self.civilization['level']}!")

    def _build_active_structure(self, agent, district_id=None):
        c = self.civilization
        district_id = district_id or self._resolve_contribution_district(agent)
        project = c["districtProjects"].get(district_id) if district_id else None
        if not project:
            return f"{agent['name']} has nothing to build"
        if project.get("isTerraform"):
            return self._complete_terraform(agent, district_id)
        struct_type = project["type"]
        footprint = self._structure_footprint({
            "type": struct_type, "sprite": project.get("sprite"),
            "visualTier": 1, "renderScale": 1.0,
        })
        spot = self._find_structure_spot(district_id, footprint=footprint)
        if not spot:
            return f"{agent['name']} finds {district_id} has no room left to build"
        new_structure = {
            "id": c["nextStructureId"], "type": struct_type,
            "x": spot["x"], "y": spot["y"],
            "visualStyle": project.get("visualStyle") or "generic",
            "sprite": project.get("sprite"),
            "name": project["name"], "districtId": district_id,
            # Phase C decay stat; every read uses .get(default 100) so
            # structures from pre-Phase-C saves need no migration.
            "condition": 100.0, "isRuin": False,
            # Phase E property: None until claimed (see _maybe_auto_claim_home).
            "homeOf": None,
            "level": 1, "visualTier": 1, "renderScale": 1.0,
        }
        c["structures"].append(new_structure)
        c["nextStructureId"] += 1
        self._mark_structure_dirty(new_structure, sprite_changed=bool(new_structure.get("sprite")))
        self._mark_civ_dirty("districtProjects", "completedProjects", "level", "builtTypes")
        self._mark_agent_dirty(agent)
        built_name = project["name"]
        c["districtProjects"][district_id] = None
        c["completedProjects"] += 1
        c["builtTypes"].add(struct_type)
        c.get("projectAbandonStreak", {}).pop(struct_type, None)
        c.get("deferredProjectTypes", {}).pop(struct_type, None)
        agent["lastContributedFrame"] = self.frameTick
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        self._check_civilization_level()
        self._maybe_auto_claim_home(agent, new_structure)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "build")
        return f"{agent['name']} built {built_name} in {district_id}"

    def _perform_gather(self, agent, resource):
        """Ecology-aware gather with structure boosts. Returns summary string."""
        c = self.civilization
        if (self._gather_zone_for_resource(resource) == "ocean"
                and not self._has_ocean_transit()):
            return f"{agent['name']} needs a working ocean transit structure to gather {resource}"
        if LIFECYCLE_ENABLED:
            # Governance (I4): a harvest_quota rule binds before the ecology
            # gate -- this is a policy refusal, not a depletion one, so it
            # deliberately does NOT trigger _scarcity_reflex_on_depletion
            # (there's nothing to terraform/relocate away from; the escape
            # is waiting out the period, a different resource, or a district
            # move, all surfaced in the reason text).
            quota_ok, quota_reason = self._harvest_quota_gate(agent, resource)
            if not quota_ok:
                agent["lastQuotaRejection"] = {"reason": quota_reason, "frame": self.frameTick}
                return f"{agent['name']} found nothing — {quota_reason}"
            agent["lastQuotaRejection"] = None
        tool_ok, tool_reason = self._can_gather_resource(agent, resource)
        if not tool_ok:
            if RESOURCE_MIN_TOOL.get(resource) == "wooden_pick" and PATH1_ENABLED:
                # Bootstrap: a pickless stone gather becomes a dig instead of
                # failing, so a fresh world can reach its first Workshop/pick.
                return self._dig_terrain(agent)
            agent["lastGatherRejection"] = {"reason": tool_reason, "frame": self.frameTick}
            self._path1_tool_benchmark(resource, False)
            return f"{agent['name']} found nothing — {tool_reason}"
        allowed, reason, scale = self._ecology_gather_gate(agent, resource)
        if not allowed:
            agent["lastGatherRejection"] = {"reason": reason, "frame": self.frameTick}
            self._scarcity_reflex_on_depletion(agent, resource)
            return f"{agent['name']} found nothing — {reason}"
        amount = 1
        if STRUCTURE_EFFECTS_ENABLED:
            amount += self._gather_yield_bonus(agent, resource)
        if PATH1_ENABLED and RESOURCE_MIN_TOOL.get(resource):
            if self._gather_tool_tier(agent) >= TOOL_TIER_LEVEL[RESOURCE_MIN_TOOL[resource]]:
                amount += TOOL_YIELD_BONUS
        if CULTURE_ENABLED:
            amount += self._skill_bonus(agent, "gather")
        amount += self._custom_rule_modifier("collect_resource", agent, resource)
        if ECOLOGY_ENABLED and scale < 1.0:
            amount = max(1, int(amount * scale))
        if ECOLOGY_ENABLED and PATH1_ENABLED:
            did = agent.get("currentDistrict")
            if did:
                grove_mult = 0.5 + self._terrain_grove_ratio(did)
                amount = max(1, int(amount * grove_mult))
        # Sovereign God mode Phase 5 (docs/archive/plan-sovereign-god-mode-v2.md
        # "Exact consumer sites and arithmetic"): the divine yield multiplier
        # is applied AFTER the custom-rule addition above (line ~5095) and
        # the ecology/grove scaling, and BEFORE the carry-cap clamp below --
        # that clamp is `max(1, min(...))`, so multiplying after it would
        # resurrect a 0.0 result back to 1. Fish gets its own modifier that
        # REPLACES (never multiplies with) the general gather modifier. A
        # resulting amount <= 0 returns HERE, before the resource is added
        # and before collectSuccesses/the tool benchmark/ecology depletion/
        # harvest-quota recording/skill practice, so a divinely-nulled gather
        # never inflates that evidence stream. With no active effect for the
        # relevant key, _divine_modifier returns exactly 1.0 and
        # floor(amount * 1.0) == amount, so this is byte-identical to the
        # feature-off baseline.
        divine_mult = (self._divine_modifier("fish_yield_multiplier")
                       if resource == "fish"
                       else self._divine_modifier("gather_yield_multiplier"))
        amount = math.floor(amount * divine_mult)
        if amount <= 0:
            reason = "a divine hand stills the harvest"
            agent["lastGatherRejection"] = {"reason": reason, "frame": self.frameTick}
            return f"{agent['name']} found nothing — {reason}"
        intended = amount
        room = max(0, self._carry_cap(agent) - agent["resources"].get(resource, 0))
        agent_add = min(intended, room)
        overflow = intended - agent_add
        if agent_add <= 0 and overflow <= 0 and intended > 0:
            overflow = intended
        elif agent_add <= 0 and room > 0:
            agent_add = min(intended, max(1, room))
            overflow = intended - agent_add
        amount = agent_add + overflow
        if agent_add:
            agent["resources"][resource] = agent["resources"].get(resource, 0) + agent_add
        if overflow:
            self._credit_settlement_overflow(agent, resource, overflow)
        c["collectSuccesses"] += 1
        self._path1_tool_benchmark(resource, True)
        if ECOLOGY_ENABLED:
            did = agent.get("currentDistrict")
            if did:
                self._deplete_district_stock(
                    did, resource, amount * STOCK_DEPLETE_MULTIPLIER)
        if LIFECYCLE_ENABLED:
            self._record_harvest_quota_use(agent, resource, amount)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "gather")
        agent["lastGatherRejection"] = None
        bonus_note = ""
        if amount > 1:
            bonus_note = " (structure effects boosted the harvest)"
        summary = f"{agent['name']} collected {resource}" + (f" x{amount}{bonus_note}" if amount > 1 else "")
        if overflow:
            summary += f" ({overflow} overflow to settlement store)"
        return summary

    # --- Contracts & escrow (CONTRACTS_ENABLED) ---
    def _total_tracked_coin(self):
        """Agent-held + stockpile + engine escrow coin (conservation audits)."""
        c = self.civilization
        total = int(c.get("contractEscrow") or 0)
        total += int((c.get("stockpile") or {}).get("coin") or 0)
        for agent in self.agents:
            total += int((agent.get("resources") or {}).get("coin") or 0)
        return total

    def _contract_deadline_frame(self, contract):
        return contract["createdFrame"] + contract["deadline_frames"]

    def _find_contract(self, contract_id):
        for ct in self.civilization.get("contracts") or []:
            if ct.get("id") == contract_id:
                return ct
        return None

    def _alloc_contract_id(self):
        c = self.civilization
        cid = f"contract_{c.get('nextContractId', 1)}"
        c["nextContractId"] = c.get("nextContractId", 1) + 1
        return cid

    def _debit_agent_coin(self, agent, amount):
        held = agent.get("resources", {}).get("coin", 0)
        if held < amount:
            return False
        agent["resources"]["coin"] = held - amount
        return True

    def _credit_agent_coin(self, agent, amount):
        agent["resources"]["coin"] = agent.get("resources", {}).get("coin", 0) + amount

    def _has_acceptable_contract(self, agent):
        if not CONTRACTS_ENABLED:
            return False
        name = agent["name"]
        for ct in self.civilization.get("contracts") or []:
            if ct.get("status") != "open":
                continue
            if ct.get("offerer") == name:
                continue
            target = ct.get("target")
            if target == "open" or target == name:
                return True
        return False

    def _format_contracts_for_prompt(self, agent):
        """Compact open/accepted contract line for think payload (flag-on only)."""
        if not CONTRACTS_ENABLED:
            return None
        name = agent["name"]
        frame = self.frameTick
        parts = []
        for ct in self.civilization.get("contracts") or []:
            status = ct.get("status")
            if status not in ("open", "accepted"):
                continue
            offerer = ct.get("offerer")
            acceptor = ct.get("acceptor")
            target = ct.get("target")
            if status == "open":
                if offerer != name and target not in ("open", name):
                    continue
            elif offerer != name and acceptor != name:
                continue
            deadline = self._contract_deadline_frame(ct)
            left = max(0, deadline - frame)
            role = "you offer" if offerer == name else (
                "you accept" if acceptor == name else "open")
            parts.append(
                f"{ct.get('id')} {status} {role} "
                f"{ct.get('qty')} {ct.get('want')} for {ct.get('pay_coin')} coin "
                f"(offerer {offerer}, target {target}, {left}f left)"
            )
            if len(parts) >= MAX_CONTRACTS_PROMPT:
                break
        return "; ".join(parts) if parts else None

    def _apply_offer_contract(self, agent, decision):
        c = self.civilization
        body = decision.get("contract") or {}
        pay_coin = body["pay_coin"]
        if not self._debit_agent_coin(agent, pay_coin):
            return f"{agent['name']} cannot offer a contract — insufficient coin"
        contracts = c.setdefault("contracts", [])
        if len(contracts) >= MAX_OPEN_CONTRACTS:
            self._credit_agent_coin(agent, pay_coin)
            return f"{agent['name']} cannot offer a contract — too many open contracts"
        target = decision.get("target")
        if target not in ("open",) and target not in self.agent_names:
            self._credit_agent_coin(agent, pay_coin)
            return f"{agent['name']} cannot offer a contract — invalid target"
        if target == agent["name"]:
            self._credit_agent_coin(agent, pay_coin)
            return f"{agent['name']} cannot offer a contract to themselves"
        if target != "open":
            tagent = self._find_agent(target)
            if not tagent or tagent.get("deathFrame") is not None:
                self._credit_agent_coin(agent, pay_coin)
                return f"{agent['name']} cannot offer a contract — target not found"
        c["contractEscrow"] = c.get("contractEscrow", 0) + pay_coin
        cid = self._alloc_contract_id()
        record = {
            "id": cid,
            "want": body["want"],
            "qty": body["qty"],
            "pay_coin": pay_coin,
            "deadline_frames": body["deadline_frames"],
            "offerer": agent["name"],
            "target": target,
            "createdFrame": self.frameTick,
            "status": "open",
            "acceptor": None,
            "acceptedFrame": None,
        }
        contracts.append(record)
        c["contractsOpened"] = c.get("contractsOpened", 0) + 1
        note = (f"{agent['name']} offered contract {cid} "
                f"({body['qty']} {body['want']} for {pay_coin} coin)")
        self._push_activity(note)
        return note

    def _apply_accept_contract(self, agent, decision):
        cid = decision.get("target")
        ct = self._find_contract(cid)
        if not ct:
            return f"{agent['name']} found no contract to accept"
        if ct.get("status") != "open":
            return f"{agent['name']} cannot accept contract {cid} — not open"
        if ct.get("offerer") == agent["name"]:
            return f"{agent['name']} cannot accept their own contract"
        target = ct.get("target")
        if target != "open" and target != agent["name"]:
            return f"{agent['name']} cannot accept contract {cid} — not directed at them"
        if self.frameTick >= self._contract_deadline_frame(ct):
            return f"{agent['name']} cannot accept contract {cid} — past deadline"
        ct["status"] = "accepted"
        ct["acceptor"] = agent["name"]
        ct["acceptedFrame"] = self.frameTick
        note = f"{agent['name']} accepted contract {cid}"
        self._push_activity(note)
        return note

    def _deliver_contract_goods(self, acceptor, offerer, resource, qty):
        held = acceptor.get("resources", {}).get(resource, 0)
        if held < qty:
            return False
        acceptor["resources"][resource] = held - qty
        cap = self._carry_cap(offerer)
        oheld = offerer.get("resources", {}).get(resource, 0)
        room = max(0, cap - oheld)
        deliver = min(qty, room)
        overflow = qty - deliver
        if deliver:
            offerer["resources"][resource] = oheld + deliver
        if overflow:
            c = self.civilization
            c.setdefault("stockpile", {})
            c["stockpile"][resource] = c["stockpile"].get(resource, 0) + overflow
        return True

    def _route_escrow_refund_coin(self, offerer_name, pay):
        """Return escrowed coin to a living offerer, else heirs, else stockpile."""
        offerer = self._find_agent(offerer_name)
        if offerer and offerer.get("deathFrame") is None:
            self._credit_agent_coin(offerer, pay)
            return
        heirs = []
        if offerer:
            heirs = [h for h in self._heirs_of(offerer) if h.get("deathFrame") is None]
        if heirs:
            base_each, remainder = divmod(int(pay), len(heirs))
            for i, heir in enumerate(heirs):
                give = base_each + (remainder if i == 0 else 0)
                if give:
                    self._credit_agent_coin(heir, give)
            return
        c = self.civilization
        c.setdefault("stockpile", {})
        c["stockpile"]["coin"] = c["stockpile"].get("coin", 0) + pay

    def _refund_contract_escrow(self, ct):
        c = self.civilization
        pay = ct["pay_coin"]
        self._route_escrow_refund_coin(ct["offerer"], pay)
        c["contractEscrow"] = max(0, c.get("contractEscrow", 0) - pay)

    def _fulfill_contract(self, ct):
        c = self.civilization
        acceptor = self._find_agent(ct.get("acceptor"))
        offerer = self._find_agent(ct.get("offerer"))
        if (not acceptor or not offerer
                or acceptor.get("deathFrame") is not None
                or offerer.get("deathFrame") is not None):
            self._refund_contract_escrow(ct)
            return
        if not self._deliver_contract_goods(acceptor, offerer, ct["want"], ct["qty"]):
            return
        pay = ct["pay_coin"]
        self._credit_agent_coin(acceptor, pay)
        c["contractEscrow"] = max(0, c.get("contractEscrow", 0) - pay)
        c["contractsFulfilled"] = c.get("contractsFulfilled", 0) + 1
        note = (f"Contract {ct['id']} fulfilled: {acceptor['name']} delivered "
                f"{ct['qty']} {ct['want']} to {offerer['name']} for {pay} coin")
        self._push_activity(note)

    def _default_contract(self, ct):
        c = self.civilization
        offerer = self._find_agent(ct.get("offerer"))
        acceptor = self._find_agent(ct.get("acceptor"))
        self._refund_contract_escrow(ct)
        c["contractDefaults"] = c.get("contractDefaults", 0) + 1
        both_living = (
            offerer and acceptor
            and offerer.get("deathFrame") is None
            and acceptor.get("deathFrame") is None
        )
        if both_living:
            if offerer["relationships"].get(acceptor["name"]) != "rival":
                offerer["relationships"][acceptor["name"]] = "rival"
            self._mark_top_dirty("socialTies")
        note = f"Contract {ct['id']} defaulted — escrow refunded to {ct['offerer']}"
        if both_living:
            note += f"; {acceptor['name']} marked rival by {ct['offerer']}"
        self._push_activity(note)

    def _tick_contract_settlement(self):
        if not CONTRACTS_ENABLED:
            return
        contracts = self.civilization.get("contracts") or []
        if not contracts:
            return
        surviving = []
        for ct in contracts:
            deadline = self._contract_deadline_frame(ct)
            if self.frameTick >= deadline:
                if ct.get("status") == "open":
                    self._refund_contract_escrow(ct)
                    self._push_activity(
                        f"Contract {ct['id']} expired unaccepted — refunded {ct['offerer']}")
                elif ct.get("status") == "accepted":
                    self._default_contract(ct)
                continue
            if ct.get("status") == "accepted":
                acceptor = self._find_agent(ct.get("acceptor"))
                offerer = self._find_agent(ct.get("offerer"))
                if (acceptor and offerer
                        and acceptor.get("deathFrame") is None
                        and offerer.get("deathFrame") is None):
                    held = acceptor.get("resources", {}).get(ct["want"], 0)
                    if held >= ct["qty"]:
                        self._fulfill_contract(ct)
                        continue
            surviving.append(ct)
        self.civilization["contracts"] = surviving

