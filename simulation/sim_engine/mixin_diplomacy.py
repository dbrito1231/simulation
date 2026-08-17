"""Phase 6c mixin: Path 1 tiles/terrain and diplomacy slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from the Path 1 tool-tiers section
through `_deliver_caravan_action` (formerly core.py lines ~3855-4675, before
Phase 6b's extraction shifted line numbers; re-located by method name for
this move). Covers: Path 1 tool tiers, composable tiles, terrain mutation,
Path 1 diplomacy (settlements/caravans/treaties), and the Living-ecosystem
Phase 3 cosmetic shipment records.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names (PATH1_ENABLED, ...)
# are NOT imported here. They live in the exec()-shared namespace — see
# simulation/sim_engine/__init__.py.


class _DiplomacyMixin:
    """Mixin slice of SimEngine: Path 1 tool tiers, composable tiles, terrain
    mutation, Path 1 diplomacy (settlements/caravans/treaties), and
    Living-ecosystem Phase 3 cosmetic shipment records. See module docstring
    for exact scope."""

    # --- Path 1: tool tiers ---
    def _gather_tool_tier(self, agent):
        if not PATH1_ENABLED:
            return 0
        best = 0
        for tool in TOOL_TIER_ORDER:
            if agent["resources"].get(tool, 0) > 0:
                best = max(best, TOOL_TIER_LEVEL[tool])
        return best

    def _can_gather_resource(self, agent, resource):
        if not PATH1_ENABLED:
            return True, None
        needed = RESOURCE_MIN_TOOL.get(resource)
        if not needed:
            return True, None
        have = self._gather_tool_tier(agent)
        need_lvl = TOOL_TIER_LEVEL[needed]
        if have < need_lvl:
            return False, f"{resource} needs a {needed} (you have tier {have} tools)"
        return True, None

    # --- Path 1: composable tiles ---
    def _district_at_pos(self, agent):
        did = agent.get("currentDistrict")
        if did and did in self.civilization["districts"]:
            return did, self.civilization["districts"][did]
        return None, None

    def _pos_to_grid(self, agent):
        did, d = self._district_at_pos(agent)
        if not d:
            return None, None, None, None
        b = d["bounds"]
        gx = int((agent["x"] - b["x1"]) // TILE_CELL)
        gy = int((agent["y"] - b["y1"]) // TILE_CELL)
        gx = max(0, min(PATH1_GRID_COLS - 1, gx))
        gy = max(0, min(PATH1_GRID_ROWS - 1, gy))
        return did, d, gx, gy

    def _tile_key(self, gx, gy):
        return f"{gx},{gy}"

    def _find_nearby_terrain(self, district, kind, from_gx, from_gy):
        """Nearest cell of `kind` in district['terrain'] to (from_gx, from_gy),
        by grid distance. Grid is fixed-size (PATH1_GRID_COLS x ROWS), so a
        full scan is cheap. Returns (gx, gy) or None if no match exists."""
        best = None
        best_dist = None
        for key, value in district.get("terrain", {}).items():
            if value != kind:
                continue
            gx_s, gy_s = key.split(",")
            gx, gy = int(gx_s), int(gy_s)
            if gx == from_gx and gy == from_gy:
                continue
            dist = (gx - from_gx) ** 2 + (gy - from_gy) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = (gx, gy), dist
        return best

    def _nearest_diggable_district(self, exclude_district_id, agent=None):
        """The district, other than the given one, that actually has a soil
        tile to dig right now — nearest to the agent by district-center
        distance when an agent is given (so eight stone-seekers don't all
        funnel down the same road to the same field), else the first match."""
        best = None
        best_dist = None
        for did, d in self.civilization["districts"].items():
            if did == exclude_district_id or d.get("kind") in NON_DIGGABLE_DISTRICT_KINDS:
                continue
            self._ensure_district_terrain(d)
            if "soil" not in d["terrain"].values():
                continue
            if agent is None:
                return did
            b = d["bounds"]
            cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
            dist = (cx - agent["x"]) ** 2 + (cy - agent["y"]) ** 2
            if best_dist is None or dist < best_dist:
                best, best_dist = did, dist
        return best

    def _pickless_stone_route(self, agent, resource):
        """Feasibility-aware routing for a pickless stone-seeker: dig right
        here if the ground allows, else head to the nearest diggable
        district. Returns a summary string, or None when normal zone routing
        (to the cave) is correct — i.e. the agent has the pick, or the
        resource isn't gated on one. Without this, agents get routed to the
        cave (stone's nominal gather zone), find no soil there, get bounced
        to a farm by the dig-relocate backstop, and commute forever."""
        if not PATH1_ENABLED:
            return None
        if RESOURCE_MIN_TOOL.get(resource) != "wooden_pick":
            return None
        tool_ok, _ = self._can_gather_resource(agent, resource)
        if tool_ok:
            return None
        did, d = self._district_at_pos(agent)
        if did and d.get("kind") not in NON_DIGGABLE_DISTRICT_KINDS:
            return self._dig_terrain(agent)
        dest = self._nearest_diggable_district(did, agent)
        if not dest:
            return None
        self._set_agent_target_once(agent, dest)
        agent["goal"] = {"kind": "dig_relocate", "target_district": dest,
                         "ttl": STALL_THRESHOLD * 2}
        return f"{agent['name']} heads to {dest} to find diggable ground"

    def _ensure_district_tiles(self, district):
        district.setdefault("tiles", {})

    def _ensure_district_terrain(self, district):
        if "terrain" not in district:
            kind = district.get("kind", "village")
            default = {"forest": "grove", "farm": "soil", "beach": "sand",
                       "cave": "rock", "ocean": "water"}.get(kind, "soil")
            district["terrain"] = {}
            for gx in range(PATH1_GRID_COLS):
                for gy in range(PATH1_GRID_ROWS):
                    district["terrain"][self._tile_key(gx, gy)] = default

    def _place_block(self, agent, block_type, gx=None, gy=None):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot place blocks — composable build is disabled"
        bt = BLOCK_TYPES.get(block_type or "")
        if not bt:
            agent["lastBlockRejection"] = {"reason": f"unknown block type {block_type}",
                                           "frame": self.frameTick}
            return f"{agent['name']} cannot place unknown block {block_type}"
        did, d, agx, agy = self._pos_to_grid(agent)
        if not did:
            agent["lastBlockRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot place blocks outside a district"
        gx = int(gx) if gx is not None else agx
        gy = int(gy) if gy is not None else agy
        self._ensure_district_tiles(d)
        tiles = d["tiles"]
        if len(tiles) >= TILE_CAP_PER_DISTRICT:
            agent["lastBlockRejection"] = {"reason": "district tile cap reached", "frame": self.frameTick}
            return f"{agent['name']} cannot place — district is at tile cap"
        key = self._tile_key(gx, gy)
        if key in tiles:
            agent["lastBlockRejection"] = {"reason": "tile already occupied", "frame": self.frameTick}
            return f"{agent['name']} cannot place — tile already has {tiles[key]}"
        for res, n in bt["cost"].items():
            if agent["resources"].get(res, 0) < n:
                agent["lastBlockRejection"] = {"reason": f"need {n} {res}", "frame": self.frameTick}
                return f"{agent['name']} lacks {res} to place {block_type}"
        for res, n in bt["cost"].items():
            agent["resources"][res] -= n
        tiles[key] = block_type
        self._bump_districts_epoch()
        agent["lastBlockRejection"] = None
        c = self.civilization
        c["path1Placements"] = c.get("path1Placements", 0) + 1
        self._log_benchmark("composable_placements", c["path1Placements"],
                            {"block": block_type, "district": did})
        self._push_activity(f"{agent['name']} placed {block_type} at {did} ({gx},{gy})")
        return f"{agent['name']} placed {block_type}"

    def _remove_block(self, agent, gx=None, gy=None):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot remove blocks"
        did, d, agx, agy = self._pos_to_grid(agent)
        if not did:
            return f"{agent['name']} cannot remove blocks outside a district"
        gx = int(gx) if gx is not None else agx
        gy = int(gy) if gy is not None else agy
        self._ensure_district_tiles(d)
        key = self._tile_key(gx, gy)
        block_type = d["tiles"].pop(key, None)
        if not block_type:
            agent["lastBlockRejection"] = {"reason": "no block here", "frame": self.frameTick}
            return f"{agent['name']} found no block to remove"
        self._bump_districts_epoch()
        bt = BLOCK_TYPES.get(block_type, {})
        for res, n in bt.get("cost", {}).items():
            refund = max(0, int(n * BLOCK_REFUND_RATIO)) or 1
            agent["resources"][res] = agent["resources"].get(res, 0) + refund
        return f"{agent['name']} removed {block_type}"

    def _composable_shelter_count(self):
        if not PATH1_ENABLED:
            return 0
        count = 0
        for d in self.civilization["districts"].values():
            tiles = d.get("tiles") or {}
            walls = sum(1 for t in tiles.values() if t in ("wall", "fence"))
            has_door = any(t == "door" for t in tiles.values())
            if walls >= 8 and has_door:
                count += 1
        return count

    # --- Path 1: terrain mutation ---
    def _dig_terrain(self, agent):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot dig — terrain tiles disabled"
        did, d, gx, gy = self._pos_to_grid(agent)
        if not did:
            agent["lastTerrainRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot dig outside a district"
        # Digging is deliberately tool-free: it is the bootstrap stone source
        # for a fresh world (stone gathers are pick-gated, the pick needs a
        # Workshop, and the Workshop needs stone).
        self._ensure_district_terrain(d)
        key = self._tile_key(gx, gy)
        current = d["terrain"].get(key, "soil")
        gained = None
        if current == "grove":
            d["terrain"][key] = "soil"
            self._bump_districts_epoch()
        elif current == "soil":
            d["terrain"][key] = "rock"
            self._bump_districts_epoch()
            gained = "stone"
        else:
            # This tile is exhausted (rock/sand/water) -- relocate to the
            # nearest fresh soil tile instead of failing forever on the same
            # spot. The walk is the action this turn; the next dig call
            # (LLM or goal-driven) lands on diggable ground.
            nearby = self._find_nearby_terrain(d, "soil", gx, gy)
            if nearby:
                ngx, ngy = nearby
                b = d["bounds"]
                agent["targetX"] = b["x1"] + (ngx + 0.5) * TILE_CELL
                agent["targetY"] = b["y1"] + (ngy + 0.5) * TILE_CELL
                agent["waypoints"] = []
                agent["lastTerrainRejection"] = None
                return f"{agent['name']} moves to fresh ground to keep digging"
            # No soil anywhere in this district -- some district kinds never
            # have any (cave defaults its whole grid to "rock", beach to
            # "sand", ocean to "water"; see _ensure_district_terrain). A
            # same-district relocate can't help there, so route to a
            # different district of a soil-bearing kind instead of leaving
            # the agent (e.g. a miner in a cave) stuck forever.
            dest = self._nearest_diggable_district(did, agent)
            if dest:
                self._set_agent_target_once(agent, dest)
                # Persistent goal: while it's set, the think tick steps
                # this deterministically instead of dispatching an LLM
                # think, so the agent's role reflexes can't reverse the
                # trip mid-transit (a miner would otherwise bounce back
                # to the cave every think cycle and never arrive).
                agent["goal"] = {"kind": "dig_relocate", "target_district": dest,
                                 "ttl": STALL_THRESHOLD * 2}
                agent["lastTerrainRejection"] = None
                return f"{agent['name']} heads to {dest} to find diggable ground"
            agent["lastTerrainRejection"] = {
                "reason": f"no diggable ground left in {did} — try another district",
                "frame": self.frameTick,
            }
            return f"{agent['name']} cannot dig {current} here"
        if gained:
            cap = self._carry_cap(agent)
            if agent["resources"].get(gained, 0) < cap:
                agent["resources"][gained] = agent["resources"].get(gained, 0) + 1
        c = self.civilization
        c["path1TerrainMutations"] = c.get("path1TerrainMutations", 0) + 1
        self._log_benchmark("terrain_mutations", c["path1TerrainMutations"], {"action": "dig", "district": did})
        agent["lastTerrainRejection"] = None
        self._push_activity(f"{agent['name']} dug terrain at {did} ({gx},{gy})")
        return f"{agent['name']} dug terrain" + (f" and found {gained}" if gained else "")

    def _plant_terrain(self, agent):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot plant — terrain tiles disabled"
        did, d, gx, gy = self._pos_to_grid(agent)
        if not did:
            agent["lastTerrainRejection"] = {"reason": "not in a district", "frame": self.frameTick}
            return f"{agent['name']} cannot plant outside a district"
        if agent["resources"].get("wood", 0) < 1:
            agent["lastTerrainRejection"] = {"reason": "need 1 wood", "frame": self.frameTick}
            return f"{agent['name']} needs wood to plant"
        self._ensure_district_terrain(d)
        key = self._tile_key(gx, gy)
        current = d["terrain"].get(key, "soil")
        if current not in ("soil", "rock"):
            agent["lastTerrainRejection"] = {"reason": f"cannot plant on {current}", "frame": self.frameTick}
            return f"{agent['name']} cannot plant on {current}"
        agent["resources"]["wood"] -= 1
        d["terrain"][key] = "grove"
        self._bump_districts_epoch()
        c = self.civilization
        c["path1TerrainMutations"] = c.get("path1TerrainMutations", 0) + 1
        self._log_benchmark("terrain_mutations", c["path1TerrainMutations"], {"action": "plant", "district": did})
        agent["lastTerrainRejection"] = None
        return f"{agent['name']} planted a grove"

    def _terrain_grove_ratio(self, district_id):
        d = self.civilization["districts"].get(district_id) or {}
        terrain = d.get("terrain") or {}
        if not terrain:
            return 0.5
        groves = sum(1 for t in terrain.values() if t == "grove")
        return groves / max(1, len(terrain))

    def _maybe_expand_field(self, agent):
        if not PATH1_ENABLED:
            return
        did = agent.get("currentDistrict")
        if not did:
            return
        d = self.civilization["districts"].get(did)
        if not d or d.get("kind") != "farm":
            return
        if self._terrain_grove_ratio(did) > 0.3:
            return
        if agent.get("goal"):
            return
        agent["goal"] = {"kind": "plant_terrain", "ttl": STALL_THRESHOLD * 2}

    # --- Path 1: diplomacy ---
    def _init_settlements(self):
        c = self.civilization
        if c.get("settlements"):
            return
        home_districts = list(c["districts"].keys())
        c["settlements"] = [{"id": "home", "name": "Home Village", "districts": home_districts}]
        for did in home_districts:
            c["districts"][did].setdefault("settlementId", "home")
        c.setdefault("treaties", [])
        c.setdefault("caravanLog", [])
        stores = c.setdefault("settlementStores", {})
        for sid in (s["id"] for s in c["settlements"]):
            stores.setdefault(sid, {})

    def _ensure_settlement_stores(self):
        self._init_settlements()
        stores = self.civilization.setdefault("settlementStores", {})
        for sid in (s["id"] for s in self.civilization["settlements"]):
            stores.setdefault(sid, {})

    def _settlement_store_bucket(self, settlement_id):
        self._ensure_settlement_stores()
        return self.civilization["settlementStores"].setdefault(settlement_id, {})

    def _credit_settlement_overflow(self, agent, resource, amount):
        if amount <= 0:
            return
        c = self.civilization
        if PATH1_ENABLED:
            bucket = self._settlement_store_bucket(self._settlement_for_agent(agent))
            bucket[resource] = bucket.get(resource, 0) + amount
        else:
            c["stockpile"][resource] = c["stockpile"].get(resource, 0) + amount

    def _pay_local_cost(self, agent, cost):
        """Fund from agent inventory, settlement store, then village stockpile."""
        c = self.civilization
        sid = self._settlement_for_agent(agent)
        store = (self._settlement_store_bucket(sid)
                 if PATH1_ENABLED else {})
        plan = {}
        missing = []
        for res, amt in cost.items():
            remaining = amt
            from_agent = min(agent["resources"].get(res, 0), remaining)
            remaining -= from_agent
            from_store = min(int(store.get(res, 0)), remaining) if store else 0
            remaining -= from_store
            from_stock = remaining
            if from_stock > int(c["stockpile"].get(res, 0)):
                missing.append(res)
            else:
                plan[res] = (from_agent, from_store, from_stock)
        if missing:
            return None, missing
        store_parts = []
        stock_parts = []
        for res, (from_agent, from_store, from_stock) in plan.items():
            if from_agent:
                agent["resources"][res] -= from_agent
            if from_store:
                store[res] = int(store.get(res, 0)) - from_store
                store_parts.append(f"{from_store} {res}")
            if from_stock:
                c["stockpile"][res] = int(c["stockpile"].get(res, 0)) - from_stock
                stock_parts.append(f"{from_stock} {res}")
        return (store_parts, stock_parts), None

    def _format_settlement_stores_for_prompt(self, agent):
        if not PATH1_ENABLED:
            return None
        self._ensure_settlement_stores()
        stores = self.civilization.get("settlementStores") or {}
        if not stores:
            return None
        parts = []
        my_sid = self._settlement_for_agent(agent)
        for sid, bucket in stores.items():
            if not bucket:
                continue
            label = sid + (" (yours)" if sid == my_sid else "")
            items = ", ".join(f"{qty} {res}" for res, qty in sorted(bucket.items()) if qty > 0)
            if items:
                parts.append(f"{label}: {items}")
        return "; ".join(parts) if parts else "per-settlement stores empty"

    def _enacted_treaty_tariff(self):
        tariff = 0.0
        for entry in (self.civilization.get("treaties") or []):
            try:
                tval = float(entry.get("tariff") or 0)
            except (TypeError, ValueError):
                tval = 0.0
            tariff = max(tariff, tval)
        for rule in (self.civilization.get("rules") or []):
            if rule.get("kind") != "treaty":
                continue
            try:
                tval = float(rule.get("tariff") or 0)
            except (TypeError, ValueError):
                tval = 0.0
            tariff = max(tariff, tval)
        return max(0.0, min(TREATY_TARIFF_MAX, tariff))

    def _parse_treaty_tariff(self, raw):
        try:
            tariff = float(raw if raw is not None else 0)
        except (TypeError, ValueError):
            return None
        if 0 <= tariff <= TREATY_TARIFF_MAX:
            return tariff
        return None

    def _caravan_trade_bundle(self, agent, dest_settlement_id=None):
        bundle = {}
        for res, qty in agent["resources"].items():
            if qty <= 0 or res in CARAVAN_VEHICLE_RESOURCES:
                continue
            if res in EDIBLE_RESOURCES:
                transferable = max(0, qty - EDIBLE_RESERVE)
            else:
                transferable = qty
            if transferable > 0:
                bundle[res] = transferable
        return bundle

    def _deliver_caravan(self, agent, dest_settlement_id, source_settlement_id=None):
        c = self.civilization
        bundle = self._caravan_trade_bundle(agent, dest_settlement_id)
        if not bundle:
            return False
        source_sid = source_settlement_id or next(
            (s["id"] for s in c["settlements"] if s["id"] != dest_settlement_id),
            self._settlement_for_agent(agent),
        )
        dest_stores = self._settlement_store_bucket(dest_settlement_id)
        source_stores = self._settlement_store_bucket(source_sid)
        tariff = self._enacted_treaty_tariff()
        from_district = agent.get("currentDistrict")
        dest_settlement = next(
            (s for s in c["settlements"] if s["id"] == dest_settlement_id), None)
        dest_district = (
            (dest_settlement or {}).get("districts") or [from_district])[0]
        goods = {}
        for res, qty in bundle.items():
            agent["resources"][res] -= qty
            tariff_qty = int(qty * tariff) if tariff > 0 else 0
            dest_qty = qty - tariff_qty
            if tariff_qty > 0:
                if PATH1_ENABLED:
                    source_stores[res] = source_stores.get(res, 0) + tariff_qty
                else:
                    c["stockpile"][res] = c["stockpile"].get(res, 0) + tariff_qty
            if dest_qty > 0:
                dest_stores[res] = dest_stores.get(res, 0) + dest_qty
            if from_district and dest_district:
                self._emit_shipment(from_district, dest_district, res)
            goods[res] = qty
        c["caravanLog"].append({
            "goods": goods,
            "from": source_sid,
            "to": dest_settlement_id,
            "frame": self.frameTick,
            "agent": agent["name"],
        })
        c["caravanLog"] = c["caravanLog"][-CARAVAN_LOG_CAP:]
        self._log_benchmark("inter_village_trades", len(c["caravanLog"]),
                            {"agent": agent["name"], "dest": dest_settlement_id, "goods": goods})
        dest_name = (dest_settlement or {}).get("name") or dest_settlement_id
        parts = ", ".join(f"{n} {r}" for r, n in goods.items())
        self._push_activity(
            f"{agent['name']} delivered {parts} to {dest_name}"
            + (f" (tariff {int(tariff * 100)}%)" if tariff > 0 else ""))
        return True

    def _transit_district_for_settlement(self, settlement_id):
        c = self.civilization
        candidates = []
        for s in c["structures"]:
            if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                continue
            if s.get("type") not in ("dock", "shipyard"):
                continue
            did = s.get("districtId")
            if did and c["districts"].get(did, {}).get("settlementId") == settlement_id:
                candidates.append(did)
        if candidates:
            return candidates[0]
        for did, d in c["districts"].items():
            if d.get("settlementId") == settlement_id and d.get("kind") == "beach":
                return did
        for settlement in c["settlements"]:
            if settlement["id"] == settlement_id and settlement.get("districts"):
                return settlement["districts"][0]
        return None

    def _ocean_district_id(self):
        ocean = self._wildlife_ocean_district()
        if ocean:
            for did, dist in self.civilization["districts"].items():
                if dist is ocean:
                    return did
        return "ocean" if "ocean" in self.civilization["districts"] else None

    def _caravan_route_legs(self, agent, dest_district_id):
        c = self.civilization
        my_sid = self._settlement_for_agent(agent)
        dest_sid = (c["districts"].get(dest_district_id) or {}).get("settlementId", "home")
        if (my_sid == dest_sid
                or not PATH1_ENABLED
                or not self._has_ocean_transit()):
            return [dest_district_id]
        source_dock = self._transit_district_for_settlement(my_sid)
        ocean_did = self._ocean_district_id()
        dest_dock = self._transit_district_for_settlement(dest_sid)
        legs = []
        if source_dock:
            legs.append(source_dock)
        if ocean_did:
            legs.append(ocean_did)
        if dest_dock and dest_dock != dest_district_id:
            legs.append(dest_dock)
        legs.append(dest_district_id)
        deduped = []
        for leg in legs:
            if not deduped or deduped[-1] != leg:
                deduped.append(leg)
        return deduped

    def _caravan_next_hop(self, agent, dest_district_id):
        legs = self._caravan_route_legs(agent, dest_district_id)
        my_did = agent.get("currentDistrict")
        for leg in legs:
            if leg == my_did:
                continue
            if not self._en_route_to(agent, leg):
                return leg
        return dest_district_id

    def _set_caravan_target(self, agent, dest_district_id):
        hop = self._caravan_next_hop(agent, dest_district_id)
        self._set_agent_target_once(agent, hop)

    def _caravan_eligible(self, agent):
        if not PATH1_ENABLED:
            return False
        carry = self._carry_cap(agent)
        has_vehicle = any(agent["resources"].get(v, 0) > 0 for v in CARAVAN_VEHICLE_RESOURCES)
        if not has_vehicle or sum(agent["resources"].values()) < CARAVAN_CARRY_MIN:
            return False
        c = self.civilization
        self._init_settlements()
        return len(c["settlements"]) >= 2

    def _resolve_caravan_dest(self, agent, decision):
        c = self.civilization
        self._init_settlements()
        my_sid = self._settlement_for_agent(agent)
        raw = decision.get("target_district") or decision.get("target")
        if raw:
            resolved = self._resolve_target_district(raw, agent)
            if resolved:
                return resolved
        other = next((s for s in c["settlements"] if s["id"] != my_sid), None)
        if other and other.get("districts"):
            return other["districts"][0]
        return None

    def _assign_caravan_goal(self, agent, dest_district):
        agent["goal"] = {
            "kind": "caravan",
            "target_district": dest_district,
            "source_settlement": self._settlement_for_agent(agent),
            "ttl": STALL_THRESHOLD * 4,
        }

    def _maybe_found_settlement(self):
        if not PATH1_ENABLED:
            return
        c = self.civilization
        self._init_settlements()
        if len(c["settlements"]) >= 2:
            return
        living = len(self._living_agents())
        structures = len([s for s in c["structures"] if not s.get("isRuin")])
        if structures < SETTLEMENT_STRUCT_THRESHOLD or living < SETTLEMENT_POP_THRESHOLD:
            return
        plot = self._claim_frontier_plot()
        if not plot:
            return
        self._found_district("village", DISTRICT_KIND_TEMPLATES["village"], plot)
        new_did = plot.get("claimedBy")
        if not new_did:
            return
        sid = "outpost"
        c["settlements"].append({"id": sid, "name": "Frontier Outpost", "districts": [new_did]})
        c["districts"][new_did]["settlementId"] = sid
        self._push_activity("A second settlement is founded — the Frontier Outpost!")
        self._log_benchmark("settlement_founded", len(c["settlements"]), {"id": sid})

    def _settlement_for_agent(self, agent):
        did = agent.get("currentDistrict")
        if did:
            return self.civilization["districts"].get(did, {}).get("settlementId", "home")
        return "home"

    def _border_settlement_agent(self, agent):
        if not PATH1_ENABLED:
            return False
        self._init_settlements()
        settlements = {s["id"] for s in self.civilization["settlements"]}
        if len(settlements) < 2:
            return False
        sid = self._settlement_for_agent(agent)
        for other in self.agents:
            if other["name"] == agent["name"] or other.get("deathFrame"):
                continue
            if self._distance_to(agent, other) > 150:
                continue
            if self._settlement_for_agent(other) != sid:
                return True
        return False

    def _maybe_caravan_goal(self, agent):
        if not self._caravan_eligible(agent):
            return
        c = self.civilization
        my_sid = self._settlement_for_agent(agent)
        other = next((s for s in c["settlements"] if s["id"] != my_sid), None)
        if not other or not other["districts"]:
            return
        dest = other["districts"][0]
        dest_sid = other["id"]
        if agent.get("currentDistrict") == dest:
            goal = agent.get("goal") or {}
            source_sid = goal.get("source_settlement") or next(
                (s["id"] for s in c["settlements"] if s["id"] != dest_sid), my_sid)
            if (source_sid != dest_sid and self._has_ocean_transit()
                    and not self._consume_ocean_transit(agent)):
                return
            if self._deliver_caravan(agent, dest_sid, source_settlement_id=source_sid):
                agent["goal"] = None
            return
        if not agent.get("goal"):
            self._assign_caravan_goal(agent, dest)

    def _ocean_transit_unlocks(self):
        out = []
        for s in self.civilization["structures"]:
            if s.get("isRuin") or s.get("condition", 100) < STRUCTURE_DISREPAIR_THRESHOLD:
                continue
            for unlock in (self._get_structure_function(s.get("type")) or {}).get("unlocks") or []:
                if unlock.get("kind") == "transit" and unlock.get("terrain") == "ocean":
                    out.append(unlock)
        return out

    def _has_ocean_transit(self):
        return bool(self._ocean_transit_unlocks())

    def _consume_ocean_transit(self, agent=None):
        unlock = self._ocean_transit_unlocks()[0] if self._ocean_transit_unlocks() else None
        if not unlock:
            return False
        costs = unlock.get("consumes") or {}
        c = self.civilization
        sid = self._settlement_for_agent(agent) if agent else "home"
        store = (self._settlement_store_bucket(sid)
                 if agent and PATH1_ENABLED else {})
        stock = c["stockpile"]
        plan = {}
        for resource, amount in costs.items():
            remaining = amount
            from_store = min(int(store.get(resource, 0)), remaining) if store else 0
            remaining -= from_store
            from_stock = remaining
            if from_stock > int(stock.get(resource, 0)):
                self._push_activity("Ocean caravan waits for transit supplies")
                return False
            plan[resource] = (from_store, from_stock)
        for resource, (from_store, from_stock) in plan.items():
            if from_store:
                store[resource] = int(store.get(resource, 0)) - from_store
            if from_stock:
                stock[resource] = int(stock.get(resource, 0)) - from_stock
        self._push_activity("An ocean caravan launches, consuming " + ", ".join(f"{n} {r}" for r, n in costs.items()))
        return True

    # --- Living-ecosystem Phase 3: cosmetic shipment records ---
    def _shipment_mode(self, from_district_id, to_district_id):
        """boat when the shipment crosses a settlement boundary and ocean
        transit is unlocked (reuses _has_ocean_transit -- the existing
        caravan foothold), else cart. Purely a display choice; never
        affects the underlying transfer."""
        c = self.civilization
        from_sid = (c["districts"].get(from_district_id) or {}).get("settlementId")
        to_sid = (c["districts"].get(to_district_id) or {}).get("settlementId")
        if from_sid and to_sid and from_sid != to_sid and self._has_ocean_transit():
            return "boat"
        return "cart"

    def _emit_shipment(self, from_district_id, to_district_id, resource):
        """Append a short-lived, purely cosmetic in-flight record for the
        viewer to animate along the road graph (CARAVAN_VISUALS_ENABLED).

        HARD CONSTRAINT: this is called AFTER the resource has already
        moved between its authoritative owners (agent inventories /
        district project contributions / stockpile) -- it never gates,
        delays, or reverses that transfer. Silently skips (no visual, never
        a fabricated straight line) when the districts match or no road
        path connects them. Kept off civilization state (self.shipments,
        not self.civilization) so it is never written to state.db.

        The resolved node path is embedded as `path` ({x,y} waypoints) so
        the viewer interpolates the exact same route the engine's own
        road-resolution helper (_road_path_between_districts, backed by the
        shared ROAD_PATH_CACHE) computed -- no second pathfinder in JS."""
        if not CARAVAN_VISUALS_ENABLED:
            return
        if not from_district_id or not to_district_id or from_district_id == to_district_id:
            return
        node_path = self._road_path_between_districts(from_district_id, to_district_id)
        if not node_path:
            return
        c = self.civilization
        waypoints = [{"x": c["roadNodes"][n]["x"], "y": c["roadNodes"][n]["y"]}
                     for n in node_path if n in c["roadNodes"]]
        if len(waypoints) < 2:
            return
        self._shipment_seq += 1
        self.shipments.append({
            "id": f"ship-{self._shipment_seq}",
            "fromDistrict": from_district_id,
            "toDistrict": to_district_id,
            "resource": resource,
            "path": waypoints,
            "startFrame": self.frameTick,
            "endFrame": self.frameTick + SHIPMENT_TRAVEL_FRAMES,
            "mode": self._shipment_mode(from_district_id, to_district_id),
        })
        if len(self.shipments) > SHIPMENT_RING_CAP:
            del self.shipments[: len(self.shipments) - SHIPMENT_RING_CAP]
        self._mark_top_dirty("shipments")

    def _prune_shipments(self):
        """Age out expired shipments. Called from the existing goods tick
        (_tick_goods, GOODS_TICK_FRAMES cadence) -- no new timer. The ring
        cap in _emit_shipment already bounds worst-case size between prunes,
        so this is hygiene, not a correctness requirement."""
        if self.shipments:
            self.shipments = [s for s in self.shipments if s["endFrame"] >= self.frameTick]
            self._mark_top_dirty("shipments")

    def _shipment_snapshot(self):
        """Read-only /state projection: only still-live shipments."""
        if not CARAVAN_VISUALS_ENABLED or not self.shipments:
            return []
        return [s for s in self.shipments if s["endFrame"] >= self.frameTick]

    def _propose_treaty(self, agent, decision):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot propose treaties"
        rule = decision.get("rule") or {}
        if not isinstance(rule, dict) or not rule.get("id") or not rule.get("name"):
            agent["lastTreatyRejection"] = {"reason": "invalid treaty proposal", "frame": self.frameTick}
            return f"{agent['name']} drafted an invalid treaty"
        tariff = self._parse_treaty_tariff(rule.get("tariff", 0))
        if tariff is None:
            agent["lastTreatyRejection"] = {"reason": "tariff must be 0–0.25", "frame": self.frameTick}
            return f"{agent['name']} drafted an invalid treaty tariff"
        entry = {
            "id": rule["id"], "name": rule["name"], "kind": "treaty",
            "value": rule.get("value") or "trade",
            "description": rule.get("description", "Inter-settlement treaty"),
            "tariff": tariff,
            "proposedBy": agent["name"], "enacted": False,
            "votes": {agent["name"]: "yes"},
        }
        self.civilization["pendingRules"].append(entry)
        self._tally_and_maybe_enact(entry)
        agent["lastTreatyRejection"] = None
        tariff_note = f" (tariff {int(tariff * 100)}%)" if tariff > 0 else ""
        return f'{agent["name"]} proposed treaty "{entry["name"]}"{tariff_note}'

    def _vote_treaty(self, agent, decision):
        if not PATH1_ENABLED:
            return f"{agent['name']} cannot vote on treaties"
        target = decision.get("target")
        vote = (decision.get("vote") or "yes").lower()
        pending = next((r for r in self.civilization["pendingRules"]
                        if r["id"] == target and r.get("kind") == "treaty"), None)
        if not pending:
            agent["lastTreatyRejection"] = {"reason": "no such treaty pending", "frame": self.frameTick}
            return f"{agent['name']} found no treaty {target} to vote on"
        pending["votes"][agent["name"]] = vote
        self._tally_and_maybe_enact(pending)
        if pending.get("enacted"):
            self.civilization.setdefault("treaties", []).append({
                "id": pending["id"], "name": pending["name"], "value": pending["value"],
                "tariff": pending.get("tariff", 0),
                "frame": self.frameTick,
            })
        return f'{agent["name"]} voted {vote} on treaty "{pending["name"]}"'

    def _deliver_caravan_action(self, agent, decision):
        if not self._caravan_eligible(agent):
            return f"{agent['name']} cannot run a caravan yet"
        dest = self._resolve_caravan_dest(agent, decision)
        if not dest:
            return f"{agent['name']} found no destination settlement"
        dest_sid = self.civilization["districts"].get(dest, {}).get("settlementId")
        if dest_sid == self._settlement_for_agent(agent):
            return f"{agent['name']} is already at the destination settlement"
        self._assign_caravan_goal(agent, dest)
        return f"{agent['name']} sets out on a caravan toward {dest}"
