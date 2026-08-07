"""Phase 6d mixin: Path 1 pressure loop + huntable wildlife slice of
SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body -- the contiguous method range from `_is_night` through
`_maybe_seek_shelter` (formerly core.py lines ~504-1243). Covers: Path 1
night/upkeep pressure loop (`_is_night`, `_pay_upkeep`, `_tick_env_upkeep`,
lit-district helpers, `_tick_night_pressure`), the huntable wildlife system
gated by WILDLIFE_ENABLED (population seeding, per-district habitat/stage
math, migration, movement/flee behavior, hunt damage/yield, god wildlife
spawn/despawn/set-hp commands, wildlife snapshot for /state, and the
`_tick_wildlife` driver), and `_maybe_seek_shelter`.

Loaded the same way as the other Phase 6 mixin files: `sim_engine/__init__.py`
exec()s this file's source into its own module namespace (not a plain
submodule import), BEFORE it exec()s core.py, so that
`class SimEngine(..., _WildlifeMixin, ...)` in core.py can reference this
class by name at class-definition time, and so every bare-name global
(WILDLIFE_ENABLED, PRESSURE_LOOP_ENABLED, HUNT_RADIUS, ...) referenced in
these method bodies keeps resolving against the one shared module dict --
required for scripts/*_smoke.py monkeypatches to keep working. See
simulation/sim_engine/__init__.py for the full rationale.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They are already present in this exec()-shared namespace by the time this
# file's body runs -- see the module docstring above and
# simulation/sim_engine/__init__.py.


class _WildlifeMixin:
    """Mixin slice of SimEngine: Path 1 pressure loop and huntable wildlife.
    See module docstring for exact scope."""

    # --- Path 1: pressure loop ---
    def _is_night(self):
        if not path1_on("PRESSURE_LOOP_ENABLED"):
            return False
        phase = self.frameTick % DAY_FRAMES
        return phase >= int(DAY_FRAMES * (1 - NIGHT_FRACTION))

    def _pay_upkeep(self, structures, resource, total_needed):
        """All-or-nothing: pay total_needed of resource, district stock (per
        structure's own district) first, then the village stockpile. Returns
        True if paid in full, False (no state change) if unaffordable."""
        c = self.civilization
        remaining = total_needed
        district_pulls = []
        seen_districts = []
        for s in structures:
            did = s.get("districtId")
            if did and did not in seen_districts:
                seen_districts.append(did)
        for did in seen_districts:
            if remaining <= 0:
                break
            avail = self._district_stock(did, resource)
            if avail is None:
                continue
            take = min(avail, remaining)
            if take > 0:
                district_pulls.append((did, take))
                remaining -= take
        stockpile_avail = int(c["stockpile"].get(resource, 0))
        if remaining > stockpile_avail:
            return False
        for did, amt in district_pulls:
            self._add_district_stock(did, resource, -amt)
        if remaining > 0:
            c["stockpile"][resource] = stockpile_avail - remaining
        return True

    def _tick_env_upkeep(self):
        """ENV_EFFECTS_ENABLED: at the first night-pressure tick of each day
        (frameTick // DAY_FRAMES changes), each working structure type
        declaring an `upkeep` effect consumes amount * count of its resource.
        Unaffordable types go unfueled for the night (their `light` effect,
        if any, is inactive); tracked per type in
        civilization["upkeepLastDay"]."""
        if not ENV_EFFECTS_ENABLED:
            return
        c = self.civilization
        day = self.frameTick // DAY_FRAMES
        last_day = c.setdefault("upkeepLastDay", {})
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            upkeep = fn.get("upkeep")
            if not isinstance(upkeep, dict):
                continue
            entry = last_day.get(type_id)
            if entry and entry.get("day") == day:
                continue
            working = [s for s in c["structures"]
                       if s["type"] == type_id
                       and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD]
            if not working:
                last_day[type_id] = {"day": day, "fueled": False}
                continue
            res = upkeep.get("resource")
            amount = upkeep.get("amount", 1)
            needed = amount * len(working)
            fueled = self._pay_upkeep(working, res, needed)
            last_day[type_id] = {"day": day, "fueled": fueled}
            if fueled:
                name = self._structure_display_name(type_id)
                self._push_activity(f"The {name} burns {needed} {res} through the night")

    def _env_lit_types(self):
        """ENV_EFFECTS_ENABLED: structure type ids whose function declares a
        `light` effect and are currently fueled (charged this day's upkeep)."""
        c = self.civilization
        last_day = c.get("upkeepLastDay", {})
        lit_types = set()
        for type_id in {s["type"] for s in c["structures"]}:
            fn = self._get_structure_function(type_id) or {}
            if not isinstance(fn.get("light"), dict):
                continue
            entry = last_day.get(type_id)
            if entry and entry.get("fueled"):
                lit_types.add(type_id)
        return lit_types

    def _env_lit_districts(self):
        """ENV_EFFECTS_ENABLED: district ids containing a working AND fueled
        `light` structure right now."""
        c = self.civilization
        lit_types = self._env_lit_types()
        lit = set()
        for s in c["structures"]:
            if (s["type"] in lit_types
                    and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD
                    and s.get("districtId")):
                lit.add(s["districtId"])
        return lit

    def _tick_night_pressure(self):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not SURVIVAL_ENABLED:
            return
        if not self._is_night():
            return
        c = self.civilization
        if ENV_EFFECTS_ENABLED:
            self._tick_env_upkeep()
        house_slots = len([s for s in c["structures"]
                           if (self._get_structure_function(s.get("type")) or {}).get("houses")
                           and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD])
        house_slots *= HOUSE_SHELTER_OCCUPANTS
        house_slots += self._composable_shelter_count() * HOUSE_SHELTER_OCCUPANTS
        if ENV_EFFECTS_ENABLED:
            house_slots += self._env_shelter_capacity()
        lit_districts = self._env_lit_districts() if ENV_EFFECTS_ENABLED else set()
        c["litDistricts"] = sorted(lit_districts)
        living = self._living_agents()
        sheltered = set()
        if house_slots >= len(living):
            c["nightSheltered"] = len(living)
            c["nightTotal"] = len(living)
            return
        for a in living:
            if a.get("homeStructureId"):
                sheltered.add(a["name"])
        others = [a for a in living if a["name"] not in sheltered]
        sheltered.update(a["name"] for a in others[:max(0, house_slots - len(sheltered))])
        exposed = 0
        lit_spared = 0
        for a in living:
            if a["name"] in sheltered or a["incapacitated"]:
                continue
            if a.get("currentDistrict") in lit_districts:
                lit_spared += 1
                continue
            if a["health"] > 10:
                a["health"] = max(10, a["health"] - NIGHT_EXPOSURE_DAMAGE)
                a["lastNightNote"] = {"reason": "exposed to the night cold", "frame": self.frameTick}
                exposed += 1
        c["nightSheltered"] = len(sheltered)
        c["nightTotal"] = len(living)
        rate = len(sheltered) / max(1, len(living))
        benchmark_payload = {"sheltered": len(sheltered), "total": len(living)}
        if lit_spared:
            benchmark_payload["lit"] = lit_spared
        self._log_benchmark("night_shelter_rate", round(rate, 2), benchmark_payload)
        if exposed:
            self._push_activity(f"Night exposure — {exposed} villager(s) took cold damage")

    # --- Huntable wildlife (WILDLIFE_ENABLED; not Path-1 _tick_wildlife) ---

    @staticmethod
    def _wildlife_hash_seed(text):
        """FNV-1a 32-bit — mirrors index.html wildlifeHashSeed for placement."""
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        return h

    def _wildlife_stage_for_district(self, district_id):
        """Ecology stage name for a district (barren/sparse/healthy/lush)."""
        if not ECOLOGY_ENABLED:
            return "healthy"
        ratio = self._district_ecology_ratio(district_id)
        if ratio is None:
            return None
        stage_state = self.civilization.setdefault("districtEcologyStage", {})
        idx = self._district_ecology_stage_with_hysteresis(ratio, stage_state.get(district_id))
        stage_state[district_id] = idx
        return DISTRICT_ECOLOGY_STAGES[idx]

    def _wildlife_stage_target(self, district_id):
        stage = self._wildlife_stage_for_district(district_id)
        if stage is None:
            return 0
        return min(WILDLIFE_STAGE_COUNT.get(stage, 0), WILDLIFE_CAP_PER_DISTRICT)

    def _wildlife_district_kind(self, district_id):
        d = self.civilization["districts"].get(district_id)
        if not d:
            return None
        kind = d.get("kind")
        return kind if kind in WILDLIFE_KIND_POOLS else None

    def _wildlife_ocean_district(self):
        """Return the ocean district dict, preferring id 'ocean'."""
        districts = self.civilization.get("districts") or {}
        ocean = districts.get("ocean")
        if ocean and ocean.get("kind") == "ocean":
            return ocean
        for d in districts.values():
            if d.get("kind") == "ocean":
                return d
        return None

    def _wildlife_habitat_rect(self, district_id, kind):
        """Return (x1, y1, x2, y2) clamp rect for a creature kind in a district."""
        d = self.civilization["districts"].get(district_id)
        if not d:
            return None
        b = d["bounds"]
        x1, y1, x2, y2 = b["x1"], b["y1"], b["x2"], b["y2"]
        dkind = d.get("kind")
        if dkind in ("forest", "farm"):
            inset = WILDLIFE_HABITAT_INSET
            return (x1 + inset, y1 + inset, max(x1 + inset + 1, x2 - inset),
                    max(y1 + inset + 1, y2 - inset))
        if dkind == "beach":
            if kind in WILDLIFE_BEACH_WATER_KINDS:
                ocean = self._wildlife_ocean_district()
                if ocean:
                    ob = ocean["bounds"]
                    ox1, oy1, ox2, oy2 = ob["x1"], ob["y1"], ob["x2"], ob["y2"]
                    strip = min(WILDLIFE_SHORE_STRIP, max(1, ox2 - ox1))
                    hy1 = max(y1, oy1)
                    hy2 = min(y2, oy2)
                    if hy2 <= hy1:
                        hy1, hy2 = oy1, oy2
                    return (ox2 - strip, hy1, ox2, hy2)
                # fallback: beach-west strip when no ocean district
                strip = min(WILDLIFE_SHORE_STRIP, max(1, x2 - x1))
                return (x1, y1, x1 + strip, y2)
            # gull (and any non-water beach kind): full beach bounds
            return (x1, y1, x2, y2)
        return (x1, y1, x2, y2)

    def _wildlife_clamp_pos(self, district_id, kind, x, y):
        rect = self._wildlife_habitat_rect(district_id, kind)
        if not rect:
            return x, y
        x1, y1, x2, y2 = rect
        return (max(x1, min(x2, x)), max(y1, min(y2, y)))

    def _wildlife_hash_point(self, district_id, kind, index):
        """Deterministic habitat point — ports drawWildlife hash placement."""
        d = self.civilization["districts"].get(district_id)
        if not d:
            return 0.0, 0.0
        b = d["bounds"]
        seed = self._wildlife_hash_seed(str(district_id))
        rx = ((seed * (index + 7) * 48271) % 2147483647) / 2147483647
        ry = ((seed * (index + 13) * 16807) % 2147483647) / 2147483647
        rect = self._wildlife_habitat_rect(district_id, kind)
        if not rect:
            return (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        x1, y1, x2, y2 = rect
        x = x1 + rx * max(1, x2 - x1)
        y = y1 + ry * max(1, y2 - y1)
        return self._wildlife_clamp_pos(district_id, kind, x, y)

    def _wildlife_random_point(self, district_id, kind):
        rect = self._wildlife_habitat_rect(district_id, kind)
        if not rect:
            return 0.0, 0.0
        x1, y1, x2, y2 = rect
        x = x1 + random.random() * max(1, x2 - x1)
        y = y1 + random.random() * max(1, y2 - y1)
        return self._wildlife_clamp_pos(district_id, kind, x, y)

    def _next_wildlife_id(self):
        c = self.civilization
        wid = int(c.get("nextWildlifeId") or 1)
        c["nextWildlifeId"] = wid + 1
        return f"w{wid}"

    def _make_wildlife_creature(self, district_id, kind, x=None, y=None, index=0):
        max_hp = int(WILDLIFE_MAX_HP.get(kind, 1))
        if x is None or y is None:
            x, y = self._wildlife_hash_point(district_id, kind, index)
        else:
            x, y = self._wildlife_clamp_pos(district_id, kind, x, y)
        tx, ty = self._wildlife_random_point(district_id, kind)
        return {
            "id": self._next_wildlife_id(),
            "kind": kind,
            "districtId": district_id,
            "x": x, "y": y,
            "targetX": tx, "targetY": ty,
            "waypoints": [],
            "hp": max_hp,
            "maxHp": max_hp,
            "alive": True,
            "respawnAt": None,
            "migrateDest": None,
        }

    def _normalize_wildlife_records(self):
        """Restore-time shape fill for persisted fauna (never re-seeds)."""
        fauna = self.civilization.setdefault("wildlife", [])
        if not isinstance(fauna, list):
            self.civilization["wildlife"] = []
            return
        for i, cre in enumerate(list(fauna)):
            if not isinstance(cre, dict):
                continue
            kind = cre.get("kind") or "bird"
            if kind == "butterfly":
                kind = "bee"
                cre["kind"] = kind
            if kind == "grazer":
                kind = "cow"
                cre["kind"] = kind
            did = cre.get("districtId")
            cre.setdefault("id", self._next_wildlife_id())
            cre.setdefault("kind", kind)
            cre.setdefault("districtId", did)
            cre.setdefault("alive", True)
            cre.setdefault("respawnAt", None)
            cre.setdefault("waypoints", [])
            max_hp = int(cre.get("maxHp") or WILDLIFE_MAX_HP.get(kind, 1))
            cre["maxHp"] = max_hp
            cre.setdefault("hp", max_hp)
            if cre.get("x") is None or cre.get("y") is None:
                x, y = self._wildlife_hash_point(did, kind, i) if did else (0.0, 0.0)
                cre["x"], cre["y"] = x, y
            if cre.get("targetX") is None or cre.get("targetY") is None:
                if did:
                    cre["targetX"], cre["targetY"] = self._wildlife_random_point(did, kind)
                else:
                    cre["targetX"], cre["targetY"] = cre["x"], cre["y"]

    def _seed_wildlife_population(self):
        """Cold-start / empty-restore: fill each habitat district to stage target."""
        if not WILDLIFE_ENABLED:
            return
        c = self.civilization
        c.setdefault("wildlife", [])
        c.setdefault("nextWildlifeId", 1)
        if c["wildlife"]:
            return
        for did, d in c["districts"].items():
            dkind = d.get("kind")
            pool = WILDLIFE_KIND_POOLS.get(dkind)
            if not pool:
                continue
            target = self._wildlife_stage_target(did)
            for i in range(target):
                kind = pool[i % len(pool)]
                # Prefer a stable hash pick so cold-start mixes kinds, not only
                # the first N of the pool list.
                seed = self._wildlife_hash_seed(f"{did}:{i}")
                kind = pool[seed % len(pool)]
                c["wildlife"].append(self._make_wildlife_creature(did, kind, index=i))

    def _wildlife_alive_in_district(self, district_id):
        return [w for w in (self.civilization.get("wildlife") or [])
                if w.get("alive") and w.get("districtId") == district_id]

    def _wildlife_road_waypoints(self, from_district_id, to_district_id, dest_x, dest_y):
        """Reuse agent road cache between districts; [] if no road path."""
        if not ROADS_ENABLED:
            return []
        path_nodes = self._road_path_between_districts(from_district_id, to_district_id)
        if not path_nodes:
            return []
        nodes = self.civilization.get("roadNodes") or {}
        waypoints = [{"x": nodes[n]["x"], "y": nodes[n]["y"]}
                     for n in path_nodes if n in nodes]
        waypoints.append({"x": dest_x, "y": dest_y})
        return waypoints

    def _wildlife_begin_migration(self, creature, dest_district_id):
        kind = creature["kind"]
        dest_x, dest_y = self._wildlife_random_point(dest_district_id, kind)
        src = creature.get("districtId")
        waypoints = self._wildlife_road_waypoints(src, dest_district_id, dest_x, dest_y)
        if waypoints:
            creature["waypoints"] = waypoints[1:]
            first = waypoints[0]
            creature["targetX"] = first["x"]
            creature["targetY"] = first["y"]
        else:
            # Straight-line toward destination habitat, then clamp on arrival.
            creature["waypoints"] = []
            creature["targetX"] = dest_x
            creature["targetY"] = dest_y
        creature["migrateDest"] = dest_district_id

    def _wildlife_pick_wander_target(self, creature):
        did = creature.get("districtId")
        kind = creature.get("kind")
        if not did or not kind:
            return
        creature["targetX"], creature["targetY"] = self._wildlife_random_point(did, kind)
        creature["waypoints"] = []

    def _wildlife_force_flee(self, creature, from_x, from_y):
        """Retarget away from a threat; used by proximity flee and hunt hits."""
        dx = creature["x"] - from_x
        dy = creature["y"] - from_y
        dist = math.sqrt(dx * dx + dy * dy) or 1.0
        flee_dist = 80 + random.random() * 40
        tx = creature["x"] + (dx / dist) * flee_dist
        ty = creature["y"] + (dy / dist) * flee_dist
        did = creature.get("districtId")
        kind = creature.get("kind")
        if did and kind and not creature.get("migrateDest"):
            tx, ty = self._wildlife_clamp_pos(did, kind, tx, ty)
        creature["targetX"] = tx
        creature["targetY"] = ty
        creature["waypoints"] = []

    def _move_wildlife(self):
        """Every-tick fauna motion: wander, flee, follow migration waypoints."""
        if not WILDLIFE_ENABLED:
            return
        fauna = self.civilization.get("wildlife") or []
        living_agents = [a for a in self.agents
                         if not a.get("incapacitated")
                         and not (LIFECYCLE_ENABLED and a.get("deathFrame") is not None)]
        for cre in fauna:
            if not cre.get("alive"):
                continue
            kind = cre.get("kind")
            speed = WILDLIFE_SPEED.get(kind, 2.0) * MOVE_SCALE
            # Flee if a living agent is within radius (skip while mid-migration
            # along roads so long-range travel isn't constantly cancelled).
            if not cre.get("migrateDest") and living_agents:
                nearest = None
                nearest_d = WILDLIFE_FLEE_RADIUS + 1
                for a in living_agents:
                    dd = _dist(cre["x"], cre["y"], a["x"], a["y"])
                    if dd < nearest_d:
                        nearest_d, nearest = dd, a
                if nearest is not None and nearest_d <= WILDLIFE_FLEE_RADIUS:
                    self._wildlife_force_flee(cre, nearest["x"], nearest["y"])
            dx = cre["targetX"] - cre["x"]
            dy = cre["targetY"] - cre["y"]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist <= speed:
                cre["x"] = cre["targetX"]
                cre["y"] = cre["targetY"]
                waypoints = cre.get("waypoints") or []
                if waypoints:
                    nxt = waypoints.pop(0)
                    cre["targetX"] = nxt["x"]
                    cre["targetY"] = nxt["y"]
                elif cre.get("migrateDest"):
                    dest = cre.pop("migrateDest", None)
                    if dest and dest in self.civilization["districts"]:
                        cre["districtId"] = dest
                    cre["waypoints"] = []
                    if cre.get("districtId") and kind:
                        cre["x"], cre["y"] = self._wildlife_clamp_pos(
                            cre["districtId"], kind, cre["x"], cre["y"])
                    self._wildlife_pick_wander_target(cre)
                else:
                    self._wildlife_pick_wander_target(cre)
            else:
                cre["x"] += (dx / dist) * speed
                cre["y"] += (dy / dist) * speed
            # In-district habitat clamp (migration waypoints may leave habitat).
            if not cre.get("migrateDest") and cre.get("districtId") and kind:
                cre["x"], cre["y"] = self._wildlife_clamp_pos(
                    cre["districtId"], kind, cre["x"], cre["y"])
        self._mark_top_dirty("wildlife")

    def _tick_huntable_wildlife(self):
        """Slower cadence: respawn, spawn to stage target, cull excess, migrate."""
        if not WILDLIFE_ENABLED:
            return
        c = self.civilization
        fauna = c.setdefault("wildlife", [])
        ft = self.frameTick
        # --- revive / spawn / cull per habitat district ---
        for did, d in list(c["districts"].items()):
            dkind = d.get("kind")
            pool = WILDLIFE_KIND_POOLS.get(dkind)
            if not pool:
                continue
            target = self._wildlife_stage_target(did)
            alive = self._wildlife_alive_in_district(did)
            # Cull excess above stage cap (mark dead; schedule ordinary respawn).
            if len(alive) > target:
                excess = sorted(alive, key=lambda w: w.get("id") or "")[target:]
                for cre in excess:
                    cre["alive"] = False
                    cre["hp"] = 0
                    cre["respawnAt"] = ft + WILDLIFE_RESPAWN_FRAMES
                    cre["waypoints"] = []
                    cre.pop("migrateDest", None)
                alive = self._wildlife_alive_in_district(did)
            # Respawn dead creatures for this district whose timer elapsed.
            for cre in fauna:
                if cre.get("alive") or cre.get("districtId") != did:
                    continue
                respawn_at = cre.get("respawnAt")
                if respawn_at is None or ft < respawn_at:
                    continue
                if len(self._wildlife_alive_in_district(did)) >= target:
                    continue
                kind = cre.get("kind")
                if kind not in pool:
                    kind = pool[self._wildlife_hash_seed(cre.get("id") or did) % len(pool)]
                    cre["kind"] = kind
                max_hp = int(WILDLIFE_MAX_HP.get(kind, 1))
                x, y = self._wildlife_random_point(did, kind)
                cre["x"], cre["y"] = x, y
                cre["hp"] = max_hp
                cre["maxHp"] = max_hp
                cre["alive"] = True
                cre["respawnAt"] = None
                cre["waypoints"] = []
                cre.pop("migrateDest", None)
                self._wildlife_pick_wander_target(cre)
            # Spawn fresh creatures if still under target.
            while len(self._wildlife_alive_in_district(did)) < target:
                idx = len(self._wildlife_alive_in_district(did))
                seed = self._wildlife_hash_seed(f"{did}:spawn:{ft}:{idx}")
                kind = pool[seed % len(pool)]
                fauna.append(self._make_wildlife_creature(did, kind, index=idx))
        # --- migration checks ---
        if WILDLIFE_MIGRATE_CHECK_FRAMES and ft % WILDLIFE_MIGRATE_CHECK_FRAMES == 0:
            for cre in list(fauna):
                if not cre.get("alive") or cre.get("kind") in WILDLIFE_DECORATIVE_KINDS:
                    continue
                if cre.get("migrateDest") or (cre.get("waypoints") or []):
                    continue
                if random.random() > WILDLIFE_MIGRATE_CHANCE:
                    continue
                kind = cre.get("kind")
                src = cre.get("districtId")
                src_kind = self._wildlife_district_kind(src)
                if not src_kind or kind not in WILDLIFE_KIND_POOLS.get(src_kind, []):
                    continue
                candidates = []
                for did, d in c["districts"].items():
                    if did == src or d.get("kind") != src_kind:
                        continue
                    if len(self._wildlife_alive_in_district(did)) >= self._wildlife_stage_target(did):
                        continue
                    candidates.append(did)
                if not candidates:
                    continue
                dest = random.choice(candidates)
                self._wildlife_begin_migration(cre, dest)

    def _nearest_huntable_wildlife(self, agent, radius=None):
        """Nearest living non-decorative creature within HUNT_RADIUS of agent."""
        if not WILDLIFE_ENABLED or agent is None:
            return None
        r = HUNT_RADIUS if radius is None else radius
        best, best_d = None, r + 1
        for cre in (self.civilization.get("wildlife") or []):
            if not cre.get("alive"):
                continue
            if cre.get("kind") in WILDLIFE_DECORATIVE_KINDS:
                continue
            if cre.get("kind") not in WILDLIFE_YIELD:
                continue
            dd = _dist(agent["x"], agent["y"], cre["x"], cre["y"])
            if dd <= r and dd < best_d:
                best, best_d = cre, dd
        return best

    def _find_wildlife_by_id(self, creature_id):
        if creature_id is None or creature_id == "":
            return None
        # LLM targets often arrive as digit strings; ids are ints.
        cid = creature_id
        if isinstance(creature_id, str) and creature_id.isdigit():
            cid = int(creature_id)
        for cre in (self.civilization.get("wildlife") or []):
            if cre.get("id") == cid or cre.get("id") == creature_id:
                return cre
        return None

    def _grant_hunt_yield(self, agent, kind):
        """Grant +1 meat/fish with carry-cap room first, overflow to settlement store."""
        resource = WILDLIFE_YIELD.get(kind)
        if not resource or not agent:
            return 0, 0, None
        amount = 1
        cap = self._carry_cap(agent)
        held = agent["resources"].get(resource, 0)
        room = max(0, cap - held)
        agent_added = min(amount, room)
        overflow_added = amount - agent_added
        if agent_added:
            agent["resources"][resource] = held + agent_added
        if overflow_added:
            self._credit_settlement_overflow(agent, resource, overflow_added)
        return agent_added, overflow_added, resource

    def _apply_hunt_damage(self, agent, creature, damage=None):
        """Apply hunt damage under the lock. Returns a result dict for apply_decision."""
        if not WILDLIFE_ENABLED:
            return {"ok": False, "reason": "wildlife disabled"}
        if not creature or not creature.get("alive"):
            return {"ok": False, "reason": "no living prey"}
        kind = creature.get("kind")
        if kind in WILDLIFE_DECORATIVE_KINDS or kind not in WILDLIFE_YIELD:
            return {"ok": False, "reason": "not huntable"}
        if damage is None:
            damage = (HUNT_DAMAGE_HUNTER if agent and agent.get("role") == "hunter"
                      else HUNT_DAMAGE)
        damage = max(1, int(damage))
        creature["hp"] = max(0, int(creature.get("hp") or 0) - damage)
        self._wildlife_force_flee(creature, agent["x"], agent["y"])
        if creature["hp"] > 0:
            self._push_activity(
                f"{agent['name']} strikes a {kind} ({creature['hp']}/{creature.get('maxHp', '?')} hp)")
            return {"ok": True, "killed": False, "creature": creature, "damage": damage,
                    "hp": creature["hp"], "kind": kind}
        creature["alive"] = False
        creature["respawnAt"] = self.frameTick + WILDLIFE_RESPAWN_FRAMES
        creature["waypoints"] = []
        creature.pop("migrateDest", None)
        agent_added, overflow_added, resource = self._grant_hunt_yield(agent, kind)
        note = f"{agent['name']} hunted a {kind}"
        if resource:
            note += f" (+{agent_added + overflow_added} {resource}"
            if overflow_added:
                note += f"; {overflow_added} overflow to settlement store"
            note += ")"
        self._push_activity(note)
        return {"ok": True, "killed": True, "creature": creature, "damage": damage,
                "kind": kind, "resource": resource, "agentAdded": agent_added,
                "stockpileAdded": overflow_added}

    def god_wildlife_spawn(self, district_id, kind, respect_cap=True):
        """Spawn an alive creature at a habitat-legal position. Caller holds lock.
        Returns the creature dict, or None on reject."""
        if not WILDLIFE_ENABLED:
            return None
        dkind = self._wildlife_district_kind(district_id)
        pool = WILDLIFE_KIND_POOLS.get(dkind or "", [])
        if not dkind or kind not in pool:
            return None
        if respect_cap:
            if len(self._wildlife_alive_in_district(district_id)) >= WILDLIFE_CAP_PER_DISTRICT:
                return None
        idx = len(self._wildlife_alive_in_district(district_id))
        cre = self._make_wildlife_creature(district_id, kind, index=idx)
        self.civilization.setdefault("wildlife", []).append(cre)
        return cre

    def god_wildlife_despawn(self, creature_id=None, district_id=None):
        """Mark target(s) dead. Pass creature id, or district_id to clear a district.
        Returns count despawned. Caller holds lock."""
        if not WILDLIFE_ENABLED:
            return 0
        fauna = self.civilization.get("wildlife") or []
        count = 0
        for cre in fauna:
            if not cre.get("alive"):
                continue
            if creature_id is not None and cre.get("id") != creature_id:
                continue
            if district_id is not None and cre.get("districtId") != district_id:
                continue
            if creature_id is None and district_id is None:
                continue
            cre["alive"] = False
            cre["hp"] = 0
            cre["respawnAt"] = self.frameTick + WILDLIFE_RESPAWN_FRAMES
            cre["waypoints"] = []
            cre.pop("migrateDest", None)
            count += 1
            if creature_id is not None:
                break
        return count

    def god_wildlife_set_hp(self, creature_id, hp):
        """Clamp and set hp; kill with ordinary respawn bookkeeping if hp <= 0.
        Returns the creature or None. Caller holds lock."""
        if not WILDLIFE_ENABLED:
            return None
        cre = self._find_wildlife_by_id(creature_id)
        if not cre:
            return None
        max_hp = int(cre.get("maxHp") or WILDLIFE_MAX_HP.get(cre.get("kind"), 1))
        cre["maxHp"] = max_hp
        new_hp = max(0, min(max_hp, int(hp)))
        cre["hp"] = new_hp
        if new_hp <= 0:
            cre["alive"] = False
            cre["respawnAt"] = self.frameTick + WILDLIFE_RESPAWN_FRAMES
            cre["waypoints"] = []
            cre.pop("migrateDest", None)
        else:
            cre["alive"] = True
            cre["respawnAt"] = None
        return cre

    def _wildlife_snapshot(self):
        """Alive-only /state projection."""
        if not WILDLIFE_ENABLED:
            return []
        out = []
        for cre in (self.civilization.get("wildlife") or []):
            if not cre.get("alive"):
                continue
            out.append({
                "id": cre.get("id"),
                "kind": cre.get("kind"),
                "districtId": cre.get("districtId"),
                "x": round(float(cre.get("x") or 0), 1),
                "y": round(float(cre.get("y") or 0), 1),
                "hp": int(cre.get("hp") or 0),
                "maxHp": int(cre.get("maxHp") or 0),
            })
        return out

    def _tick_wildlife(self):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not SURVIVAL_ENABLED:
            return
        if random.random() > WILDLIFE_EVENT_PROB:
            return
        forest_agents = [a for a in self._living_agents()
                         if not a["incapacitated"]
                         and self.civilization["districts"].get(a.get("currentDistrict"), {}).get("kind") == "forest"]
        if not forest_agents:
            return
        victim = random.choice(forest_agents)
        guarded = any(self._distance_to(victim, g) <= WILDLIFE_GUARD_RADIUS
                      for g in self._living_agents()
                      if g["name"] != victim["name"] and g.get("role") == "guard"
                      and not g["incapacitated"])
        if guarded:
            self._push_activity(f"Wildlife stirs near {victim['name']} but guards keep it at bay")
            return
        victim["health"] = max(5, victim["health"] - 5)
        victim["lastNightNote"] = {"reason": "startled by wildlife", "frame": self.frameTick}
        self._push_activity(f"Wildlife attacks {victim['name']} in the forest!")

    def _maybe_seek_shelter(self, agent):
        if not path1_on("PRESSURE_LOOP_ENABLED") or not self._is_night():
            return
        if agent.get("homeStructureId") or agent.get("goal"):
            return
        village_district = next((did for did, d in self.civilization["districts"].items()
                                 if d.get("kind") == "village"), None)
        if village_district and agent.get("currentDistrict") != village_district:
            agent["goal"] = {"kind": "seek_shelter", "target_district": village_district,
                             "ttl": STALL_THRESHOLD}

