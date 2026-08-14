"""Phase 6i mixin: control (pause/resume/reset) plus the full /state
snapshot-building cluster, slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `pause` through
`snapshot_delta` (formerly core.py lines ~1701-2167), the FINAL slice of the
Phase 6 mixin-extraction effort: after this file, core.py's SimEngine
retains only `__init__`, `_select_active_defs`, `_make_agents`, and
`_reset_world` (the construction path, per Phase 6a's decision). Covers
control (`pause`, `resume`, `reset`) and Contract 2 snapshot-building
(`_social_ties_snapshot`, `_chronicle_snapshot`, `_agent_snapshot_row`,
`_structure_snapshot_row`, `_build_district_projects_snapshot`,
`_build_civ_snapshot`, `_build_snapshot_config`, `_build_god_snapshot`,
`_build_snapshot_core`, `_snapshot_delta_top_key`, `snapshot`,
`snapshot_delta`) — the data source for the live, browser-polled `/state`
HTTP endpoint.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _SnapshotMixin:
    """Mixin slice of SimEngine: pause/resume/reset control plus the full
    Contract 2 /state snapshot-building cluster. See module docstring for
    the exact method range and rationale."""

    # --- control + snapshot (Contract 2) ---
    def pause(self):
        with self.lock:
            self.paused = True
            self._paused_mod_frame = self.frameTick
            self._dirty_this_frame.add("paused")

    def resume(self):
        with self.lock:
            self.paused = False
            self._paused_mod_frame = self.frameTick
            self._dirty_this_frame.add("paused")

    def reset(self, roster_size=None):
        with self.lock:
            self._reset_world(roster_size if roster_size else self.roster_size)
            if roster_size:
                self.roster_size = roster_size
            self._piano_module_cache = {}
            self._piano_module_drops = 0
            self._piano_latency_ms = {}
            self._piano_refresh_inflight = set()
            self._module_pulse_work = []
            self._module_refresh_failures = 0
            self._module_note_ages = []
            self._peer_prediction_pending = {}
            self._peer_prediction_hits = 0
            self._peer_prediction_total = 0
            self.shipments = []
            self._shipment_seq = 0
            self._god_preview_cache = {}
            self._god_requests = {}
            self._god_rejected_count = 0
            self._god_grant_session_total = 0
            self._god_deja_vu_session_total = 0
            self._god_compiler_state = {"lastCompileWallTime": 0.0, "compileCount": 0}
            ms = self.d.get("memory_store")
            if ms is not None:
                try:
                    ms.clear()
                    print("[server] /control/reset cleared MemoryStore "
                          "(in-memory + stable-path flush)")
                except Exception:
                    pass
        # Replace the on-disk save so a reset truly starts fresh: clear the old
        # snapshot, then immediately persist the fresh cold-started world.
        self.clear_state()
        self.save_state(force=True)

    def _social_ties_snapshot(self):
        """Return compact non-neutral ties between living agents for the viewer.

        Relationships are name-keyed and may be one-sided. One canonical pair
        avoids duplicate canvas lines; a disagreement resolves to rival so the
        more adverse visible state is never hidden.
        """
        living = {
            agent["name"]: agent for agent in self.agents
            if agent.get("deathFrame") is None
        }
        pairs = {}
        for source in living.values():
            for target_name, valence in (source.get("relationships") or {}).items():
                target = living.get(target_name)
                if target is None or valence not in {"ally", "rival"}:
                    continue
                source_id, target_id = sorted((source["id"], target["id"]))
                key = (source_id, target_id)
                prior = pairs.get(key)
                pairs[key] = "rival" if valence == "rival" or prior == "rival" else "ally"
        return [
            {"from": source_id, "to": target_id, "valence": valence}
            for (source_id, target_id), valence in sorted(pairs.items())
        ]

    def _chronicle_snapshot(self):
        """Return the curated viewer projection of the persisted chronicle ring."""
        return [
            {"text": str(entry.get("text") or ""), "frame": entry.get("frame"), "kind": kind}
            for entry in self.civilization.get("chronicle") or []
            if (kind := entry.get("kind")) in CHRONICLE_MILESTONE_KINDS
        ][-CHRONICLE_CAP:]

    def _saga_snapshot(self):
        """Return the viewer projection of the persisted saga ring."""
        return [
            {
                "text": str(entry.get("text") or ""),
                "frame": entry.get("frame"),
                "dayIndex": entry.get("dayIndex"),
            }
            for entry in self.civilization.get("saga") or []
        ][-SAGA_CAP:]

    def _agent_snapshot_row(self, a):
        """One agent row for /state (lock held)."""
        return {
            "id": a["id"], "name": a["name"], "role": a["role"], "color": a["color"],
            "x": a["x"], "y": a["y"], "currentZone": a["currentZone"],
            "currentDistrict": a.get("currentDistrict"),
            "waypoints": len(a.get("waypoints") or []),
            "resources": dict(a["resources"]), "hunger": a["hunger"], "health": a["health"],
            "incapacitated": a["incapacitated"], "message": a["message"],
            "isThinking": a["isThinking"],
            "beliefs": [self._belief_text(b) for b in a["beliefs"]],
            "beliefIds": sorted(a["beliefs"]) if MEMES_ENABLED else [],
            "lastAction": a["lastAction"], "assignedTask": a["assignedTask"],
            "age": round(a["age"], 1) if LIFECYCLE_ENABLED and a.get("age") is not None else None,
            "lifeStage": self._life_stage(a) if LIFECYCLE_ENABLED else None,
            "skills": {k: round(v, 1) for k, v in a["skills"].items()} if CULTURE_ENABLED else None,
            "personalityTraits": list(a.get("personalityTraits") or []) if CULTURE_ENABLED else [],
            "deceased": bool(LIFECYCLE_ENABLED and a.get("deathFrame") is not None),
            "buried": bool(CEMETERY_ENABLED and a.get("buried")),
            "relationships": {k: v for k, v in (a.get("relationships") or {}).items() if v != "neutral"},
            "lastReasoning": (a.get("lastReasoning") or "")[:160] or None,
        }

    def _structure_snapshot_row(self, s, env_lit_types, include_sprite=True):
        """One structure row for /state (lock held)."""
        row = {
            "id": s["id"], "type": s["type"], "x": s["x"], "y": s["y"],
            "visualStyle": s.get("visualStyle"), "name": s.get("name"),
            "districtId": s.get("districtId"),
            "condition": s.get("condition", 100),
            "isRuin": bool(s.get("isRuin")),
            "conditionTier": structure_condition_tier(s),
            "homeOf": s.get("homeOf"),
            "level": s.get("level", 1),
            "visualTier": s.get("visualTier", 1),
            "renderScale": s.get("renderScale", 1.0),
            "light": bool(
                ENV_EFFECTS_ENABLED and s["type"] in env_lit_types
                and not s.get("isRuin")
                and s.get("condition", 100) >= STRUCTURE_DISREPAIR_THRESHOLD),
        }
        if include_sprite:
            row["sprite"] = s.get("sprite")
        return row

    def _build_district_projects_snapshot(self):
        c = self.civilization
        district_projects = {}
        for did, ap in c["districtProjects"].items():
            if not ap:
                district_projects[did] = None
                continue
            total = sum(ap["needs"].values())
            done = sum(min(ap["contributed"].get(r, 0), n) for r, n in ap["needs"].items())
            pct = round(done / total * 100) if total else 0
            progress_text = ", ".join(f"{r} {ap['contributed'].get(r, 0)}/{n}"
                                      for r, n in ap["needs"].items())
            district_projects[did] = {"name": ap["name"], "type": ap["type"],
                                      "progressText": progress_text, "progressPercent": pct}
        return district_projects

    def _build_civ_snapshot(self):
        """Full civilization projection for /state (lock held)."""
        c = self.civilization
        env_lit_types = self._env_lit_types() if ENV_EFFECTS_ENABLED else set()
        civ = {
            "level": c["level"],
            "structures": [self._structure_snapshot_row(s, env_lit_types, include_sprite=True)
                           for s in c["structures"]],
            "districtProjects": self._build_district_projects_snapshot(),
            "completedProjects": c["completedProjects"],
            "resourceRegistry": {rid: dict(d) for rid, d in c["resourceRegistry"].items()},
            "projectRegistry": {pid: dict(p) for pid, p in c["projectRegistry"].items()},
            "pendingBlueprints": [dict(b) for b in c["pendingBlueprints"]],
            "pendingRecipes": [dict(r) for r in c["pendingRecipes"]],
            "recipes": {rid: {"name": r["name"], "inputs": dict(r["inputs"]),
                              "station": r.get("station")}
                        for rid, r in self.RECIPES.items()} if CRAFTING_ENABLED else {},
            "rules": [dict(r) for r in c["rules"]],
            "pendingRules": [dict(r) for r in c["pendingRules"]],
            "constitution": [dict(p) for p in self._ensure_constitution()],
            "directive": self._current_directive(),
            "season": self._current_season(),
            "stockpile": dict(c["stockpile"]),
            "taxDue": c["taxDue"], "taxPaid": c["taxPaid"],
            "collectAttempts": c["collectAttempts"], "collectSuccesses": c["collectSuccesses"],
        }
        if CULTURE_ENABLED:
            civ["chronicle"] = list((c.get("chronicle") or [])[-CHRONICLE_CAP:])
            civ["libraryKnowledge"] = list(c.get("libraryKnowledge") or [])
            civ["memeMutations"] = c.get("memeMutations", 0)
            civ["beliefRegistry"] = json.loads(json.dumps(self._belief_registry(), default=str))
            civ["beliefPitchCalls"] = c.get("beliefPitchCalls", 0)
        if DAILY_COUNCIL_ENABLED:
            civ["dailyCouncil"] = json.loads(json.dumps(c.get("dailyCouncil"), default=str))
            civ["councilDigests"] = json.loads(json.dumps(
                (c.get("councilDigests") or [])[:DAILY_COUNCIL_DIGEST_CAP], default=str))
            if not TECH_TREE_ENABLED:
                civ["councilLog"] = json.loads(json.dumps(
                    (c.get("councilLog") or [])[:DAILY_COUNCIL_LOG_CAP], default=str))
        if TECH_TREE_ENABLED:
            civ["era"] = self._current_era_name()
            civ["techTier"] = self._village_tech_tier()
            council = c.get("councilActive")
            civ["councilActive"] = ({
                "active": True,
                "trigger": council.get("trigger"),
                "proposers": list(council.get("proposers") or []),
                "proposals": len(council.get("proposals") or []),
            } if council else None)
            civ["councilLog"] = json.loads(json.dumps(
                (c.get("councilLog") or [])[:COUNCIL_LOG_CAP], default=str))
        if ECONOMY_ENABLED:
            civ["marketActive"] = self._market_active()
            civ["prices"] = ({rid: self._resource_price(rid)
                              for rid in c["resourceRegistry"] if rid != "gold"}
                             if civ["marketActive"] else {})
        if path1_on():
            civ["settlements"] = list(c.get("settlements") or [])
            civ["treaties"] = list(c.get("treaties") or [])
            civ["settlementStores"] = {
                sid: dict(bucket or {})
                for sid, bucket in (c.get("settlementStores") or {}).items()
            }
            civ["caravanLog"] = list(c.get("caravanLog") or [])[-CARAVAN_LOG_CAP:]
            civ["isNight"] = self._is_night()
        if ENV_EFFECTS_ENABLED:
            civ["litDistricts"] = list(c.get("litDistricts") or [])
        if TRANSIT_ENABLED:
            boat_count = int(c.get("stockpile", {}).get("boat", 0))
            civ["physicalProps"] = ([{"resource": "boat", "count": min(3, boat_count)}]
                                    if boat_count >= 3 else [])
        return civ

    def _build_snapshot_config(self):
        return {
            "WORLD_W": WORLD_W, "WORLD_H": WORLD_H,
            "flags": {
                "SURVIVAL_ENABLED": SURVIVAL_ENABLED, "USE_GOALS": USE_GOALS,
                "EMERGENT_ROLES": EMERGENT_ROLES, "RULES_ENABLED": RULES_ENABLED,
                "MEMES_ENABLED": MEMES_ENABLED, "CRAFTING_ENABLED": CRAFTING_ENABLED,
                "META_SYSTEM": META_SYSTEM, "PIANO_MODULES": PIANO_MODULES,
                "ROADS_ENABLED": ROADS_ENABLED,
                "ECOLOGY_ENABLED": ECOLOGY_ENABLED,
                "GOODS_ENABLED": GOODS_ENABLED,
                "TECH_TREE_ENABLED": TECH_TREE_ENABLED,
                "ECONOMY_ENABLED": ECONOMY_ENABLED,
                "CONTRACTS_ENABLED": CONTRACTS_ENABLED,
                "LIFECYCLE_ENABLED": LIFECYCLE_ENABLED,
                "CULTURE_ENABLED": CULTURE_ENABLED,
                "CEMETERY_ENABLED": CEMETERY_ENABLED,
                "STRUCTURE_UPGRADES_ENABLED": STRUCTURE_UPGRADES_ENABLED,
                "STRUCTURE_WEAR_ENABLED": STRUCTURE_WEAR_ENABLED,
                "ACTIVITY_CUES_ENABLED": ACTIVITY_CUES_ENABLED,
                "SOCIAL_LAYER_ENABLED": SOCIAL_LAYER_ENABLED,
                "CHRONICLE_ENABLED": CHRONICLE_ENABLED,
                "CHRONICLE_SAGA_ENABLED": CHRONICLE_SAGA_ENABLED,
                "FOUNDING_EVENTS_ENABLED": FOUNDING_EVENTS_ENABLED,
                "WORLD_CLOCK_HUD_ENABLED": WORLD_CLOCK_HUD_ENABLED,
                "SEASONAL_AGENTS_ENABLED": SEASONAL_AGENTS_ENABLED,
                "PATH1_ENABLED": PATH1_ENABLED,
                "INDUSTRY_ENABLED": path1_on("INDUSTRY_ENABLED"),
                "TOOL_TIERS_ENABLED": path1_on("TOOL_TIERS_ENABLED"),
                "COMPOSABLE_BUILD_ENABLED": path1_on("COMPOSABLE_BUILD_ENABLED"),
                "TERRAIN_TILES_ENABLED": path1_on("TERRAIN_TILES_ENABLED"),
                "DIPLOMACY_ENABLED": path1_on("PATH1_DIPLOMACY_ENABLED"),
                "TIER3_CONTENT_ENABLED": path1_on("TIER3_CONTENT_ENABLED"),
                "PRESSURE_LOOP_ENABLED": path1_on("PRESSURE_LOOP_ENABLED"),
                "ENV_EFFECTS_ENABLED": ENV_EFFECTS_ENABLED,
                "LIBRARY_SCALING_ENABLED": LIBRARY_SCALING_ENABLED,
                "TRANSIT_ENABLED": TRANSIT_ENABLED,
                "ECONOMY_SINKS_ENABLED": ECONOMY_SINKS_ENABLED,
                "WIKI_MEMORY": WIKI_MEMORY,
                "TESTAMENT_ENABLED": TESTAMENT_ENABLED,
                "THEORY_OF_MIND_ENABLED": THEORY_OF_MIND_ENABLED,
                "SCHISM_ENABLED": SCHISM_ENABLED,
                "CROP_GROWTH_ENABLED": CROP_GROWTH_ENABLED,
                "WILDLIFE_ENABLED": WILDLIFE_ENABLED,
                "CARAVAN_VISUALS_ENABLED": CARAVAN_VISUALS_ENABLED,
                "WEATHER_ENABLED": WEATHER_ENABLED,
                "WEATHER_GOVERNANCE_ENABLED": WEATHER_GOVERNANCE_ENABLED,
                "GOD_MODE_ENABLED": GOD_MODE_ENABLED,
                "GOD_AUTH_REQUIRED": GOD_AUTH_REQUIRED,
                "GOD_DEJA_VU_REPLAY": GOD_DEJA_VU_REPLAY,
                "WORLD_WIKI_ENABLED": WORLD_WIKI_ENABLED,
            },
        }

    def _build_god_snapshot(self):
        c = self.civilization
        god = c.get("godState") or self._default_god_state()
        return {
            "intervened": bool(god.get("intervened")),
            "providence": god.get("providence"),
            "activePublicEvents": [
                e for e in (god.get("activeEvents") or [])[:GOD_ACTIVE_EVENTS_CAP]
                if isinstance(e, dict) and e.get("status") == "active"
                and e.get("visibility", "public") == "public"
            ],
            "recentPublicInterventions": [
                r for r in (god.get("recentInterventions") or [])[-GOD_RECENT_INTERVENTIONS_CAP:]
                if isinstance(r, dict) and r.get("public", True)
            ],
        }

    def _build_snapshot_core(self):
        """Full /state body. Must be called under self.lock."""
        snapshot = {
            "frameTick": self.frameTick,
            "paused": self.paused,
            "uptimeSeconds": time.time() - self.processStartTime,
            "calendar": self._calendar(),
            "lmStatus": self.lmStatus,
            "agents": [self._agent_snapshot_row(a) for a in self.agents],
            "civilization": self._build_civ_snapshot(),
            "benchmarks": dict(self.lastBenchmarks),
            "activity": list(self.activityLog),
            "conversation": list(self.conversationLog[:30]),
            "config": self._build_snapshot_config(),
        }
        if SOCIAL_LAYER_ENABLED:
            snapshot["socialTies"] = self._social_ties_snapshot()
        if CHRONICLE_ENABLED and CULTURE_ENABLED:
            snapshot["chronicle"] = self._chronicle_snapshot()
        if CHRONICLE_SAGA_ENABLED:
            snapshot["saga"] = self._saga_snapshot()
        if CROP_GROWTH_ENABLED or WILDLIFE_ENABLED:
            snapshot["districtEcology"] = self._district_ecology_snapshot()
        if WILDLIFE_ENABLED:
            snapshot["wildlife"] = self._wildlife_snapshot()
        else:
            snapshot["wildlife"] = []
        if CARAVAN_VISUALS_ENABLED:
            snapshot["shipments"] = self._shipment_snapshot()
        if WEATHER_ENABLED:
            snapshot["weather"] = self._weather_snapshot()
        if GOD_MODE_ENABLED:
            snapshot["god"] = self._build_god_snapshot()
        return snapshot

    def _snapshot_delta_top_key(self, key):
        """Project one optional top-level /state key (lock held)."""
        if key == "activity":
            return "activity", list(self.activityLog)
        if key == "conversation":
            return "conversation", list(self.conversationLog[:30])
        if key == "benchmarks":
            return "benchmarks", dict(self.lastBenchmarks)
        if key == "lmStatus":
            return "lmStatus", self.lmStatus
        if key == "wildlife":
            return "wildlife", self._wildlife_snapshot() if WILDLIFE_ENABLED else []
        if key == "shipments" and CARAVAN_VISUALS_ENABLED:
            return "shipments", self._shipment_snapshot()
        if key == "weather" and WEATHER_ENABLED:
            return "weather", self._weather_snapshot()
        if key == "socialTies" and SOCIAL_LAYER_ENABLED:
            return "socialTies", self._social_ties_snapshot()
        if key == "chronicle" and CHRONICLE_ENABLED and CULTURE_ENABLED:
            return "chronicle", self._chronicle_snapshot()
        if key == "saga" and CHRONICLE_SAGA_ENABLED:
            return "saga", self._saga_snapshot()
        if key == "districtEcology" and (CROP_GROWTH_ENABLED or WILDLIFE_ENABLED):
            return "districtEcology", self._district_ecology_snapshot()
        if key == "god" and GOD_MODE_ENABLED:
            return "god", self._build_god_snapshot()
        if key == "config":
            return "config", self._build_snapshot_config()
        return None, None

    def snapshot(self):
        """Consistent full /state snapshot per Contract 2 (copied under lock)."""
        with self.lock:
            snap = self._build_snapshot_core()
            snap["stateGeneration"] = self.stateGeneration
            snap["full"] = True
            self._dirty_this_frame.clear()
            self._prune_state_dirty(self.frameTick)
            return snap

    def snapshot_delta(self, since):
        """Incremental /state for GET /state?since=<frameTick> (lock held for copy)."""
        with self.lock:
            ft = self.frameTick
            gen = self.stateGeneration
            try:
                since_int = int(since) if since is not None else 0
            except (TypeError, ValueError):
                since_int = 0

            def _full_and_prune():
                snap = self._build_snapshot_core()
                snap["stateGeneration"] = gen
                snap["full"] = True
                self._dirty_this_frame.clear()
                self._prune_state_dirty(ft)
                return snap

            if since is None or since_int <= 0:
                return _full_and_prune()
            if since_int > ft or since_int < self._last_reset_frame:
                return _full_and_prune()
            if ft - since_int > STATE_DELTA_MAX_GAP:
                return _full_and_prune()
            if since_int == ft and not self._has_state_dirty(since_int):
                self._prune_state_dirty(ft)
                return {"frameTick": ft, "stateGeneration": gen, "unchanged": True}

            payload = {
                "frameTick": ft,
                "baseFrame": since_int,
                "stateGeneration": gen,
                "calendar": self._calendar(),
                "uptimeSeconds": time.time() - self.processStartTime,
            }
            if (self._paused_mod_frame is not None
                    and self._delta_include_mod(
                        self._paused_mod_frame, since_int, "paused")):
                payload["paused"] = self.paused
                self._discard_frame_tags(since_int, "paused")

            dirty_agents = [
                aid for aid, mod in self._dirty_agents.items()
                if self._delta_include_mod(mod, since_int, f"a:{aid}")
            ]
            if dirty_agents:
                by_id = {a["id"]: a for a in self.agents}
                payload["agents"] = [
                    self._agent_snapshot_row(by_id[aid])
                    for aid in sorted(dirty_agents) if aid in by_id
                ]
                self._discard_frame_tags(
                    since_int, *(f"a:{aid}" for aid in dirty_agents))

            dirty_civ_keys = [
                k for k, mod in self._dirty_civ_keys.items()
                if self._delta_include_mod(mod, since_int, f"c:{k}")
            ]
            dirty_upserts = [
                sid for sid, mod in self._dirty_structure_upserts.items()
                if self._delta_include_mod(mod, since_int, f"su:{sid}")
            ]
            dirty_removals = [
                sid for sid, mod in self._dirty_structure_removals.items()
                if self._delta_include_mod(mod, since_int, f"sr:{sid}")
            ]
            if dirty_civ_keys or dirty_upserts or dirty_removals:
                full_civ = self._build_civ_snapshot()
                civ_partial = {}
                for key in dirty_civ_keys:
                    if key == "structures":
                        continue
                    if key in full_civ:
                        civ_partial[key] = full_civ[key]
                if dirty_upserts or dirty_removals:
                    env_lit_types = self._env_lit_types() if ENV_EFFECTS_ENABLED else set()
                    by_struct = {s["id"]: s for s in self.civilization["structures"]}
                    upserts = []
                    for sid in sorted(dirty_upserts):
                        s = by_struct.get(sid)
                        if s:
                            sprite_mod = self._dirty_structure_sprites.get(sid, 0)
                            include_sprite = self._delta_include_mod(
                                sprite_mod, since_int, f"sp:{sid}")
                            upserts.append(self._structure_snapshot_row(
                                s, env_lit_types, include_sprite=include_sprite))
                    if upserts:
                        civ_partial["structures"] = upserts
                    if dirty_removals:
                        civ_partial["structuresRemoved"] = sorted(dirty_removals)
                if civ_partial:
                    payload["civilization"] = civ_partial
                self._discard_frame_tags(
                    since_int,
                    *(f"c:{k}" for k in dirty_civ_keys),
                    *(f"su:{sid}" for sid in dirty_upserts),
                    *(f"sp:{sid}" for sid in dirty_upserts),
                    *(f"sr:{sid}" for sid in dirty_removals),
                )

            dirty_top_keys = [
                k for k, mod in self._dirty_top_keys.items()
                if self._delta_include_mod(mod, since_int, f"t:{k}")
            ]
            for key in sorted(dirty_top_keys):
                k, val = self._snapshot_delta_top_key(key)
                if k is not None:
                    payload[k] = val
            if dirty_top_keys:
                self._discard_frame_tags(
                    since_int, *(f"t:{k}" for k in dirty_top_keys))
            if self._config_mod_frame is not None and self._config_mod_frame > since_int:
                payload["config"] = self._build_snapshot_config()
                self._discard_frame_tags(since_int, "config")

            self._prune_state_dirty(ft)
            return payload
