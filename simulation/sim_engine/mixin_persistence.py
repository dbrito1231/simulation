"""Phase 6f mixin: full-state persistence + Sovereign God mode core slice
of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_save_loop` through
`_clamp_god_duration` (formerly core.py lines ~540-1847). Covers: full-state
persistence (Contract 3) — `_save_loop`, `_serialize_state`, `save_state`,
`clear_state`, `_ensure_registry_entry_from_instance`, and the large
`restore_state` (kept as a single undivided method — splitting its body
would be a logic change, not a pure move); Sovereign God mode core state
(`_default_god_state`, `_normalize_god_state`); decision digests and Deja Vu
replay (godState v3: `_decision_reasoning_hash` through
`_god_apply_deja_vu_replay`); the stored-text contract
(`_normalize_divine_text`, `_normalize_god_presentation`,
`_god_presentation_payload_field`); and Sovereign God mode Phase 5
storyteller-event validation basics (`_god_active_event_holding_key`,
`_validate_god_story_event`, `_clamp_god_duration`).

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names are NOT imported here.
# They live in the exec()-shared namespace — see simulation/sim_engine/__init__.py.


class _PersistenceMixin:
    """Mixin slice of SimEngine: full-state persistence (save/restore/
    serialize), Sovereign God mode core state, decision digests + Deja Vu
    replay, the stored-text contract, and story-event validation basics.
    See module docstring for exact scope."""

    # --- full-state persistence (Contract 3) ---
    def _save_loop(self):
        while not self._stop.is_set():
            # Wait first so we don't immediately overwrite a freshly restored
            # state.db with a near-identical one before any work happens.
            if self._stop.wait(AUTOSAVE_SECONDS):
                break
            self.save_state()

    def _serialize_state(self):
        """Build the Contract-3 payload. Caller must hold self.lock."""
        c = self.civilization
        civ = {k: v for k, v in c.items() if k not in _CIV_SET_KEYS}
        # Deep copy into JSON-safe form so the DB write can't race a mutation
        # after the lock is released; known set keys are sorted arrays.
        civ = _json_safe_copy(civ)
        for key in _CIV_SET_KEYS:
            civ[key] = sorted(c.get(key, set()))
        # Structure sprite grids are stored in structure_sprites, not civ JSON.
        for s in civ.get("structures") or []:
            if isinstance(s, dict):
                s.pop("sprite", None)
        agents = []
        for a in self.agents:
            ad = {k: v for k, v in a.items() if k not in ("beliefs", "godKeys", "isThinking")}
            ad = _json_safe_copy(ad)
            ad["beliefs"] = sorted(a.get("beliefs", set()))
            ad["godKeys"] = sorted(a.get("godKeys", set()))
            agents.append(ad)
        memory = []
        ms = self.d.get("memory_store")
        if ms is not None:
            try:
                memory = ms.export_entries()
            except Exception:
                memory = []
        payload = {
            "version": STATE_VERSION,
            "frameTick": self.frameTick,
            "savedAt": datetime.now(timezone.utc).isoformat(),
            "roster_size": self.roster_size,
            "civilization": civ,
            "agents": agents,
            "memory": memory,
            "council_transcript": _json_safe_copy(self.council_transcript_rows),
        }
        fp = _structure_sprites_fingerprint(self.civilization)
        if fp:
            payload["structureSpritesFingerprint"] = fp
        sprite_upserts = {}
        for sid in self._persist_dirty_structure_sprites:
            live = next(
                (x for x in self.civilization["structures"] if x.get("id") == sid), None,
            )
            if live and live.get("sprite"):
                sprite_upserts[sid] = {
                    "sprite": _json_safe_copy(live["sprite"]),
                    "updated_frame": self.frameTick,
                }
        if sprite_upserts:
            payload["_sprite_upserts"] = sprite_upserts
        if self._persist_sprite_removals:
            payload["_sprite_removals"] = sorted(self._persist_sprite_removals)
        return payload

    def save_state(self, force=False):
        """Atomically write the complete world to state.db. Never raises.

        Periodic autosave passes force=False and skips the SQLite rewrite when
        the content hash (excluding savedAt) matches the last successful write.
        Graceful shutdown, reset, and checkpoint paths pass force=True so
        savedAt and on-disk bytes always refresh."""
        try:
            content_hash = None
            with self.lock:
                payload = self._serialize_state()
                content_hash = _state_content_hash(payload)
                self._last_save_considered_at = time.time()
                if not force and content_hash == self._last_saved_hash:
                    return True
            _write_state_db(DB_PATH, payload)
            self._last_saved_hash = content_hash
            with self.lock:
                self._persist_dirty_structure_sprites.clear()
                self._persist_sprite_removals.clear()
            return True
        except Exception:
            # Persistence must never crash the sim.
            return False

    def clear_state(self):
        """Remove state.db so the next start cold-starts. Never raises."""
        for suffix in ("", "-wal", "-shm"):
            try:
                path = DB_PATH + suffix
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def _ensure_registry_entry_from_instance(self, civ, type_id):
        """Restore-only fallback for retired structure recipes."""
        registry = civ.get("projectRegistry")
        if not isinstance(registry, dict):
            return None
        entry = registry.get(type_id)
        if isinstance(entry, dict):
            return entry
        instance = next((s for s in civ.get("structures", [])
                         if s.get("type") == type_id), None)
        if not instance:
            return None
        entry = {
            "name": instance.get("name") or type_id.replace("_", " ").title(),
            "needs": {"wood": 2, "stone": 2},
            "visualStyle": instance.get("visualStyle") or "generic",
            "function": {},
            "custom": True,
        }
        registry[type_id] = entry
        return entry

    def restore_state(self):
        """If a valid state.db exists, rehydrate the world from it instead of
        the cold-start roster. Returns True on a successful restore."""
        data = _read_state_db(DB_PATH)
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(data, dict) or version not in RESTORE_STATE_VERSIONS:
            return False
        try:
            with self.lock:
                civ = dict(data.get("civilization") or {})
                table_sprites = data.get("structureSprites") or {}
                for s in civ.get("structures") or []:
                    sid = s.get("id")
                    if sid and sid in table_sprites:
                        s["sprite"] = table_sprites[sid]
                    elif version == 2 and sid and s.get("sprite"):
                        # v2 migration: embedded sprites split out on next save.
                        self._persist_dirty_structure_sprites.add(sid)
                for key in _CIV_SET_KEYS:
                    civ[key] = set(civ.get(key) or [])
                # builtTypes backfill: a save from before #5.1 has no record of
                # which project types were ever completed, even though
                # civ["structures"] already captures it losslessly (append-only,
                # never pruned) -- derive it instead of leaving
                # _invention_required() permanently False for a long-lived,
                # already-built-out village.
                civ["builtTypes"].update(
                    s.get("type") for s in (civ.get("structures") or []) if s.get("type"))
                # basePopulation backfill: saves from before structure effects
                # existed have no record of the starting roster -- treat the
                # saved roster as the base so existing houses grow it from here.
                if not civ.get("basePopulation"):
                    civ["basePopulation"] = max(1, min(MAX_ROSTER_SIZE,
                                                       len(data.get("agents") or []) or 8))
                civ.setdefault("effectLastFire", {})
                civ.setdefault("approvedCustomApprovedFrame", {})
                civ.setdefault("lastProjectAbandonment", None)
                civ.setdefault("approvedCustomBackoffUntil", 0)
                civ.setdefault("approvedCustomBackstopFailures", 0)
                civ.setdefault("approvedCustomEscalationLogged", False)
                civ.setdefault("projectAbandonStreak", {})
                civ.setdefault("deferredProjectTypes", {})
                civ.setdefault("rejectedBlueprintFrames", {})
                civ.setdefault("customResourceAddedFrame", {})
                # Agent-driven structure reorganization: purely additive, same
                # setdefault-only back-compat as every other phase -- an old
                # save simply starts with no reorg task pending, and the
                # periodic backstop (_maybe_reorganize_structures) discovers
                # any pre-existing footprint overlap (e.g. the House/Mill
                # overlap in the live save) within the first ~10s.
                civ.setdefault("reorgTasks", [])
                civ.setdefault("lastReorgFrame", 0)
                civ.setdefault("lastReorgCheckFrame", 0)
                civ.setdefault("lastReorgNoRoomFrame", 0)
                # Living-ecosystem Phase 4: weather state machine. Purely
                # additive/setdefault-only back-compat like every other
                # phase -- an old save simply starts at "clear" (frame 0
                # backfill; _tick_weather advances it on the next goods
                # tick using the real, post-restore frameTick). Critically,
                # this setdefault NEVER overwrites an already-persisted
                # weather value, so a save that already has real weather
                # state resumes it unchanged (no re-roll on load).
                civ.setdefault("weather", self._weather_default(0))
                # Sovereign God mode (Phase 2): old saves get a fresh default
                # god state via setdefault, same discipline as weather above
                # -- never overwrites an already-persisted godState. Malformed
                # nested fields on an existing godState are normalized
                # conservatively (never raise) by _normalize_god_state.
                civ["godState"] = self._normalize_god_state(civ.get("godState"))
                civ.setdefault("lastRoleSwitchFrame", 0)
                civ.setdefault("lastRuleAttemptFrame", 0)
                civ.setdefault("priorityRuleSeq", 0)
                civ.setdefault("taxRuleSeq", 0)
                civ.setdefault("emergencyRuleSeq", 0)
                civ.setdefault("roleNeedSinceFrame", None)
                civ.setdefault("lastRoleRebalanceLatency", None)
                civ.setdefault("confrontCooldowns", {})
                # Phase 2 role registry migration: older saves only know the
                # roles.json seeds, while newer saves carry per-world approved
                # roles. Merge missing seeds without overwriting live entries.
                registry = civ.get("roleRegistry")
                if not isinstance(registry, dict):
                    registry = {}
                for role, definition in self.d["ROLES"].items():
                    registry.setdefault(role, dict(definition))
                civ["roleRegistry"] = registry
                civ.setdefault("pendingRoles", [])
                civ.setdefault("ruleKindsEverEnacted", [])
                # Backfill diversity from currently enacted rules so old saves
                # don't report 0 forever after a restore.
                for r in (civ.get("rules") or []):
                    kind = r.get("kind")
                    if kind and kind not in civ["ruleKindsEverEnacted"]:
                        civ["ruleKindsEverEnacted"].append(kind)
                # Phase 4 governance migration: old saves have active rules
                # but no constitutional ledger or compiled custom effects.
                # Keep any already-persisted history in order and append only
                # missing active provisions, so repeated restore cycles cannot
                # duplicate a law.
                constitution = civ.get("constitution")
                if not isinstance(constitution, list):
                    constitution = []
                constitution = [p for p in constitution
                                if isinstance(p, dict) and isinstance(p.get("id"), str)]
                provision_ids = {p["id"] for p in constitution}
                for r in (civ.get("rules") or []):
                    if not isinstance(r, dict) or not r.get("id"):
                        continue
                    if r["id"] not in provision_ids:
                        provision = {
                            "id": r["id"], "name": r.get("name") or r["id"],
                            "kind": r.get("kind") or "custom",
                            "description": r.get("description") or "",
                            "enactedFrame": r.get("enactedFrame", 0), "status": "active",
                        }
                        if r.get("effect") is not None:
                            provision["effect"] = r["effect"]
                        if r.get("supersedes"):
                            provision["supersedes"] = r["supersedes"]
                        constitution.append(provision)
                        provision_ids.add(r["id"])
                civ["constitution"] = constitution
                civ.setdefault("customRuleModifiers", {})
                # Phase C: spoilage nudge state. Structure condition/isRuin
                # deliberately have NO migration -- every read defaults via
                # .get(cond, 100), so pre-Phase-C structures start pristine.
                civ.setdefault("lastSpoilage", None)
                # Phase D: era + council state; registry entries from pre-D
                # saves carry no tier field (read via _type_tier's seed-template
                # fallback), and the new seed types (Forge, wagon resource)
                # merge into restored registries so an old save can build them.
                civ.setdefault("era", None)
                civ.setdefault("eraIndex", 0)
                civ.setdefault("councilActive", None)
                civ.setdefault("councilLog", [])
                civ.setdefault("dailyCouncil", None)
                civ.setdefault("councilDigests", [])
                if TECH_TREE_ENABLED:
                    for tid, tmpl in PROJECT_TEMPLATES.items():
                        if isinstance(civ.get("projectRegistry"), dict):
                            civ["projectRegistry"].setdefault(tid, dict(tmpl))
                    for rid, rdef in CRAFTED_RESOURCES.items():
                        if isinstance(civ.get("resourceRegistry"), dict):
                            civ["resourceRegistry"].setdefault(rid, dict(rdef))
                if ECOLOGY_ENABLED and not civ.get("districtStocks"):
                    civ["districtStocks"] = self._init_district_stocks(
                        civ.get("districts") or {}, civ.get("resourceRegistry"))
                # Civ-1 coastal visual migration: only replace the narrow
                # legacy starter bounds, never player-founded beaches.
                legacy_coast = {
                    "beach": {"x1": 230, "y1": 120, "x2": 400, "y2": 880},
                    "ocean": {"x1": 30, "y1": 120, "x2": 180, "y2": 880},
                }
                for did, old_bounds in legacy_coast.items():
                    district = (civ.get("districts") or {}).get(did)
                    if district and district.get("bounds") == old_bounds:
                        district["bounds"] = dict(STARTER_DISTRICTS[did]["bounds"])
                if ECONOMY_ENABLED:
                    # Phase E: the market seed joins existing registries (old
                    # saves can build it); structures/houses from pre-Phase-E
                    # saves have no "homeOf" -- every read uses .get(homeOf)
                    # so this setdefault is cosmetic (keeps snapshot/JSON
                    # shape consistent) rather than load-bearing.
                    if isinstance(civ.get("projectRegistry"), dict):
                        civ["projectRegistry"].setdefault("market", dict(PROJECT_TEMPLATES["market"]))
                        # The mint + coin: a save from before this feature has
                        # neither. Purely additive -- an old village starts
                        # with the mint buildable and coin registered but
                        # unminted (0 everywhere), same setdefault-only
                        # back-compat as every other phase.
                        civ["projectRegistry"].setdefault("mint", dict(PROJECT_TEMPLATES["mint"]))
                    if isinstance(civ.get("resourceRegistry"), dict):
                        civ["resourceRegistry"].setdefault("coin", dict(BASE_RESOURCES["coin"]))
                    for s in (civ.get("structures") or []):
                        s.setdefault("homeOf", None)
                if CONTRACTS_ENABLED:
                    # Contracts & escrow: additive setdefault for old saves.
                    civ.setdefault("contracts", [])
                    civ.setdefault("contractEscrow", 0)
                    civ.setdefault("nextContractId", 1)
                    civ.setdefault("contractsOpened", 0)
                    civ.setdefault("contractsFulfilled", 0)
                    civ.setdefault("contractDefaults", 0)
                if STRUCTURE_UPGRADES_ENABLED:
                    for s in (civ.get("structures") or []):
                        s.setdefault("level", 1)
                        s.setdefault("visualTier", 1)
                        s.setdefault("renderScale", 1.0)
                if LIFECYCLE_ENABLED:
                    # Phase F: population lifecycle + governance state. A save
                    # from before this phase has none of this -- setdefault is
                    # purely additive (no migration needed, matching every
                    # prior phase's back-compat pattern). Gated on the flag
                    # like every other phase's restore block, so a flag-off
                    # restore introduces none of this state (byte-identical
                    # to Phase E).
                    civ.setdefault("lastBirthFrame", 0)
                    civ.setdefault("lastDeathActivityFrame", 0)
                    civ.setdefault("births", 0)
                    civ.setdefault("deaths", 0)
                    civ.setdefault("nextGeneratedAgentId", 1000)
                    civ.setdefault("pendingSuccession", None)
                    civ.setdefault("lastSuccessionActivityFrame", 0)
                    civ.setdefault("harvestQuotas", {})
                    civ.setdefault("rationingActive", {})
                    if RAIDERS_CONTAGION_ENABLED:
                        civ.setdefault("quarantineActive", {})
                    civ.setdefault("populationFloorHeld", False)
                # Huntable wildlife: additive setdefault for old saves; seed
                # only when the list is still empty so a persisted population
                # never re-rolls on load.
                civ.setdefault("nextWildlifeId", 1)
                civ.setdefault("wildlife", [])
                if CULTURE_ENABLED:
                    # Phase G: knowledge/culture state. Purely additive --
                    # matches every prior phase's setdefault-only back-compat
                    # (no migration step), so the live save loads with the
                    # flag on and simply starts with an empty chronicle/library.
                    if isinstance(civ.get("projectRegistry"), dict):
                        civ["projectRegistry"].setdefault("library", dict(PROJECT_TEMPLATES["library"]))
                    civ.setdefault("chronicle", [])
                    civ.setdefault("saga", [])
                    civ.setdefault("libraryKnowledge", [])
                    civ.setdefault("memeTexts", {})
                    civ.setdefault("memeMutations", 0)
                    civ.setdefault("beliefRegistry", {
                        bid: {"id": bid, "name": bid.replace("_", " ").title(),
                              "tenet": text, "affinity": sorted(MEME_RULE_AFFINITY.get(bid, set())),
                              "authoredBy": None, "createdFrame": 0, "seed": True}
                        for bid, text in MEMES.items()
                    })
                    civ.setdefault("beliefPitchCalls", 0)
                    civ.setdefault("skillPracticeCount", 0)
                    civ.setdefault("teachCount", 0)
                if FACTION_SPLIT_ENABLED:
                    # F4.1: wrap legacy flat governance/belief fields under the
                    # primary home settlement id without removing flat keys.
                    self._migrate_faction_split_storage_on_restore(civ)
                if TESTAMENT_ENABLED:
                    civ.setdefault("testament", [])
                    civ.setdefault("testamentAuthored", 0)
                # Cemetery/burial state: purely additive, same discipline
                # as every other phase's setdefault-only back-compat --
                # an old save can build a Cemetery with no migration step.
                if isinstance(civ.get("projectRegistry"), dict):
                    civ["projectRegistry"].setdefault("cemetery", dict(PROJECT_TEMPLATES["cemetery"]))
                civ.setdefault("lastCemeteryCheckFrame", 0)
                civ.setdefault("cemeteryBackoffUntil", 0)
                civ.setdefault("cemeteryBackstopFailures", 0)
                civ.setdefault("cemeteryEscalationLogged", False)
                if path1_on():
                    for tid, tmpl in PROJECT_TEMPLATES.items():
                        if isinstance(civ.get("projectRegistry"), dict):
                            civ["projectRegistry"].setdefault(tid, dict(tmpl))
                    for rid, rdef in {**BASE_RESOURCES, **CRAFTED_RESOURCES}.items():
                        if isinstance(civ.get("resourceRegistry"), dict):
                            civ["resourceRegistry"].setdefault(rid, dict(rdef))
                    civ.setdefault("settlements", [])
                    civ.setdefault("treaties", [])
                    civ.setdefault("caravanLog", [])
                    stores = civ.setdefault("settlementStores", {})
                    for s in civ.get("settlements") or []:
                        if isinstance(s, dict) and s.get("id"):
                            stores.setdefault(s["id"], {})
                    civ.setdefault("path1Placements", 0)
                    civ.setdefault("path1TerrainMutations", 0)
                    for d in (civ.get("districts") or {}).values():
                        d.setdefault("tiles", {})
                        d.setdefault("settlementId", "home")
                        if "terrain" not in d:
                            d["terrain"] = {}
                if ENV_EFFECTS_ENABLED:
                    civ.setdefault("upkeepLastDay", {})
                    civ.setdefault("litDistricts", [])
                    for tid in ("hearth", "lighthouse"):
                        tmpl = self._ensure_registry_entry_from_instance(civ, tid)
                        if not isinstance(tmpl, dict):
                            continue
                        fn = tmpl.setdefault("function", {})
                        if not isinstance(fn.get("light"), dict):
                            fn["light"] = {"scope": "district"}
                        fn.setdefault("upkeep", {"resource": "charcoal", "amount": 1})
                for tid in ("dock", "shipyard"):
                    entry = self._ensure_registry_entry_from_instance(civ, tid)
                    if not isinstance(entry, dict):
                        continue
                    fn = entry.setdefault("function", {})
                    unlocks = fn.setdefault("unlocks", [])
                    if not any(u.get("kind") == "transit" for u in unlocks if isinstance(u, dict)):
                        unlocks.append({"kind": "transit", "terrain": "ocean", "consumes": {"boat": 1}})
                agents = []
                is_scaffold = self.d.get("is_scaffold_text")
                for ad in (data.get("agents") or []):
                    a = dict(ad)
                    # isThinking is an in-flight-only runtime flag; a snapshot
                    # taken mid-think would otherwise wedge the agent forever
                    # (the dispatch gate requires False and only the dead
                    # process's _think_job could have reset it).
                    a["isThinking"] = False
                    a["beliefs"] = set(a.get("beliefs") or [])
                    a.setdefault("lastProjectRejection", None)
                    a.setdefault("lastTerraformRejection", None)
                    a.setdefault("lastCraftRejection", None)
                    a.setdefault("lastRepairRejection", None)
                    a.setdefault("lastRecipeRejection", None)
                    a.setdefault("lastShelterNote", None)
                    a.setdefault("homeStructureId", None)
                    # Home settlement is a persistent field (Phase: settlement
                    # residency fix); resolving a sensible default needs
                    # self.civilization, which isn't assigned yet here, so we
                    # only stage the field now and resolve None values in the
                    # post-load pass below (after self.civilization/self.agents
                    # are set).
                    a.setdefault("homeSettlementId", None)
                    a.setdefault("lastTradeRejection", None)
                    a.setdefault("lastHomelessNudgeFrame", None)
                    a.setdefault("lastBurialRejection", None)
                    a.setdefault("inventionRetryUsed", False)
                    a.setdefault("inventionBuildContext", None)
                    a.setdefault("councilTurn", False)
                    a.setdefault("spriteDesignTurn", None)
                    # Heals worlds saved before the fix that made
                    # _upgrade_structure zero out a dimension already at the
                    # sprite grid cap (SPRITE_GRID_MAX) instead of demanding
                    # "strictly more than the cap" rows/cols -- an
                    # unsatisfiable request the model would refuse forever.
                    # Restored turns predating that fix can still carry
                    # minRows/minCols == the old cap in both dimensions; without
                    # this pass they'd loop until the attempt-counter give-up
                    # burns SPRITE_DESIGN_MAX_ATTEMPTS LLM calls for nothing.
                    turn = a.get("spriteDesignTurn")
                    if isinstance(turn, dict):
                        cap = int(self.d.get("SPRITE_GRID_MAX") or 14)
                        try:
                            min_rows = int(turn.get("minRows") or 0)
                            min_cols = int(turn.get("minCols") or 0)
                        except (TypeError, ValueError):
                            # Malformed minRows/minCols: drop the turn rather
                            # than crash restore_state on a bad state.db value.
                            a["spriteDesignTurn"] = None
                        else:
                            if min_rows >= cap and min_cols >= cap:
                                a["spriteDesignTurn"] = None
                            elif min_rows >= cap:
                                turn["minRows"] = 0
                            elif min_cols >= cap:
                                turn["minCols"] = 0
                    elif turn is not None:
                        # Malformed (non-dict) restored turn: drop it rather
                        # than risk a crash later when it's read as a dict.
                        a["spriteDesignTurn"] = None
                    a.setdefault("lastUpgradeRejection", None)
                    a.setdefault("lastSpriteRejection", None)
                    a.setdefault("lastBlockRejection", None)
                    a.setdefault("lastTerrainRejection", None)
                    a.setdefault("lastTreatyRejection", None)
                    a.setdefault("lastNightNote", None)
                    a.setdefault("reorgTask", None)
                    a.setdefault("divineHold", False)
                    a.setdefault("architectLimbo", None)
                    a["godKeys"] = set(a.get("godKeys") or ())
                    if isinstance(a.get("resources"), dict):
                        a["resources"].setdefault("coin", 0)
                    a.setdefault("persona", "")
                    a.setdefault("moduleTick", 0)
                    a.setdefault("moduleReports", {})
                    a.setdefault("memoryWiki", {})
                    default_modules = {
                        "perception": True, "social": True,
                        "desire": True, "reflection": True,
                    }
                    if THEORY_OF_MIND_ENABLED:
                        default_modules["theory_of_mind"] = True
                    a.setdefault("modules", default_modules)
                    if THEORY_OF_MIND_ENABLED:
                        a.setdefault("peerModel", {})
                    if LIFECYCLE_ENABLED:
                        # Phase F: every restored agent gets an age (staggered
                        # by roster position, same deterministic spread
                        # _make_agents uses for a cold start; the elder starts
                        # oldest) so a long-lived save (e.g. the live
                        # 416-structure world) can turn LIFECYCLE_ENABLED on
                        # with no migration step. Gated on the flag so a
                        # flag-off restore never introduces an age field --
                        # matching every other phase's discipline.
                        if a.get("age") is None:
                            if a.get("role") == "elder":
                                a["age"] = float(ELDER_AGE + 5)
                            else:
                                a["age"] = float(ADULT_AGE + 2 + (len(agents) * 7) % 30)
                        a.setdefault("lastQuotaResetFrame", 0)
                        a.setdefault("gatherCountThisPeriod", {})
                        a.setdefault("lastQuotaRejection", None)
                        a.setdefault("lastRationingRejection", None)
                        a.setdefault("parents", None)
                        a.setdefault("children", [])
                        a.setdefault("inheritedTestament", [])
                        a.setdefault("inheritedBeliefs", [])
                        a.setdefault("deathFrame", None)
                        a.setdefault("buried", False)
                        a.setdefault("restingPlaceId", None)
                        a.setdefault("restingDistrictId", None)
                    else:
                        a["age"] = None
                    if RAIDERS_CONTAGION_ENABLED:
                        a.setdefault("infected", False)
                        a.setdefault("infectionFrame", None)
                    if CULTURE_ENABLED:
                        # Phase G: an agent restored from a pre-Phase-G save
                        # (or with the flag freshly turned on) starts with no
                        # practiced skill and no drift traits -- additive only.
                        skills = a.get("skills")
                        a["skills"] = {k: float((skills or {}).get(k, 0.0)) for k in SKILL_KINDS}
                        a.setdefault("personalityTraits", [])
                        a.setdefault("lastTeachFrame", 0)
                    # state.db may have been written before scaffold
                    # validation existed (or before a clean cycle ran), so a
                    # saved agent's memory.longTerm list can carry leaked
                    # chain-of-thought text wholesale -- scrub it on load too.
                    if is_scaffold and isinstance(a.get("memory"), dict):
                        long_term = a["memory"].get("longTerm")
                        if long_term:
                            a["memory"] = dict(a["memory"])
                            a["memory"]["longTerm"] = [
                                t for t in long_term if not is_scaffold(t)
                            ]
                    agents.append(a)
                if not agents or not civ:
                    return False
                if LIFECYCLE_ENABLED:
                    # Older saves may retain child-to-parent links but lack
                    # inverse parent-to-children arrays. Rebuild those links
                    # after every agent has loaded, preserving persisted
                    # children and avoiding duplicates.
                    by_name = {a.get("name"): a for a in agents if a.get("name")}
                    for child in agents:
                        parents = child.get("parents")
                        if not isinstance(parents, (list, tuple)):
                            continue
                        child_name = child.get("name")
                        if not child_name:
                            continue
                        for parent_name in parents:
                            parent = by_name.get(parent_name)
                            if parent is None:
                                continue
                            children = parent.get("children")
                            if not isinstance(children, list):
                                children = []
                                parent["children"] = children
                            if child_name not in children:
                                children.append(child_name)
                self.civilization = civ
                self._rebuild_role_maps()
                if FACTION_SPLIT_ENABLED:
                    for sid in (self.civilization.get("rulesBySettlement") or {}):
                        self._rebuild_settlement_governance(sid)
                else:
                    self._ensure_constitution()
                    self._rebuild_custom_rule_modifiers()
                self.agents = agents
                self.agent_names = set(a["name"] for a in agents)
                # One-time snapshot: any agent restored without a persistent
                # home settlement (pre-fix save) becomes a resident of
                # wherever they physically are at upgrade time. Must run
                # after self.civilization/self.agents are assigned --
                # _settlement_for_agent reads self.civilization["districts"].
                for a in agents:
                    if a.get("homeSettlementId") is None:
                        a["homeSettlementId"] = self._settlement_for_agent(a)
                self._revert_inland_founded_beaches()
                self.council_transcript_rows = [
                    dict(row) for row in (data.get("council_transcript") or [])
                    if isinstance(row, dict)
                ]
                # Rehydrate PIANO module working memory: state.db already
                # persists each agent's moduleReports mirror (written
                # alongside moduleTick after every think), so a restore need
                # not start every module blind for PIANO_MODULE_CACHE_TTL
                # ticks. /control/reset intentionally keeps wiping this cache
                # from scratch -- only restore rehydrates it.
                self._piano_module_cache = {}
                for a in agents:
                    reports = a.get("moduleReports") or {}
                    if isinstance(reports, dict) and reports:
                        self._piano_module_cache[a["name"]] = {
                            m: dict(v) for m, v in reports.items() if isinstance(v, dict)
                        }
                self.frameTick = int(data.get("frameTick") or 0)
                rs = data.get("roster_size")
                if rs:
                    self.roster_size = int(rs)
                # Sovereign God mode (Phase 2): restart/restore invalidates
                # every outstanding preview and in-flight idempotency record
                # (neither is persisted -- see __init__). Then close out any
                # timed effect that was already past its expiresFrame at the
                # restored frameTick, exactly once, using the same bounded
                # scan _tick_once uses every tick.
                self._god_preview_cache = {}
                self._god_requests = {}
                # Optional Phase 8: compiler rate-limit/session-cap state is
                # equally in-memory-only and never persisted -- restart/
                # restore resets it, exactly like the preview/idempotency
                # caches above.
                self._god_compiler_state = {"lastCompileWallTime": 0.0, "compileCount": 0}
                self._expire_divine_effects(restore=True)
                self._recompute_road_paths()
                if WILDLIFE_ENABLED:
                    # Old saves get a seeded population once; existing fauna
                    # lists are only shape-normalized (never re-rolled).
                    if not self.civilization.get("wildlife"):
                        self._seed_wildlife_population()
                    else:
                        self._normalize_wildlife_records()
                self._ensure_cemetery_district()
                self._migrate_cemetery_structure()
                self._relayout_cemetery_graves()
                _validate_districts(self.civilization["districts"])
                _validate_road_graph(self.civilization["roadNodes"], self.civilization["roadEdges"])
                self._bump_districts_epoch()
                # Rebuild the MemoryStore by re-embedding each entry's text.
                ms = self.d.get("memory_store")
                if ms is not None:
                    try:
                        ms.import_entries(data.get("memory") or [])
                    except Exception:
                        pass
                self._on_world_replaced()
            return True
        except Exception:
            return False

    # --- Sovereign God mode (docs/archive/plan-sovereign-god-mode-v2.md, Phase 2) ---
    # Secure kernel only: the module-level GOD_MODE_ENABLED flag gates the
    # dark default, and server.py's SIM_GOD_TOKEN + X-God-Token check gates
    # every route BEFORE it reaches these methods -- these engine entry
    # points have no access to (and never see) the token. Every public entry
    # point below still re-checks GOD_MODE_ENABLED itself and no-ops cleanly
    # if it is somehow called with the flag off, so a misrouted call can
    # never mutate state. The only applyable command this phase is a
    # no-mechanics `proclamation` -- every other kind validates far enough to
    # be rejected cleanly with "not implemented in this phase".

    def _default_god_state(self):
        """Authoritative shape (docs/archive/plan-sovereign-god-mode-v2.md section "Authoritative state
        model"). recentRequests is deliberately absent -- the idempotency
        store is self._god_requests, in-memory only, never persisted."""
        return {
            "version": GOD_STATE_VERSION,
            "intervened": False,
            "nextInterventionSeq": 1,
            "providence": None,
            "privateOmens": {},
            "activeEvents": [],
            "recentInterventions": [],
            "recentDivineResponses": [],
            # Divine Matrix interventions (godState v2) — whisperCampaigns + agentSampling live.
            "whisperCampaigns": {},
            "agentSampling": {},  # Phase 2: live per-agent LLM sampling overrides
            "contextMasks": {},
            "decisionGates": {},
            "burningBush": {},
            "anointments": {},
            "identityForges": {},
            "architectZones": [],
            "checkpoints": [],
            "decisionDigests": [],
            "dejaVuReplays": {},
            "crowdCompulsions": {},
            "dreamBroadcasts": {},
        }

    def _normalize_god_state(self, raw):
        """Conservative restore-time normalizer: never raises, backfills any
        missing/malformed nested field with its default instead of rejecting
        the whole structure -- the same setdefault-only back-compat
        discipline every other phase's restore_state block uses. Never
        overwrites an already-valid persisted field."""
        base = self._default_god_state()
        if not isinstance(raw, dict):
            return base
        out = dict(base)
        if isinstance(raw.get("version"), int):
            out["version"] = raw["version"]
        out["intervened"] = bool(raw.get("intervened"))
        seq = raw.get("nextInterventionSeq")
        out["nextInterventionSeq"] = seq if isinstance(seq, int) and seq >= 1 else 1
        # Phase 3: a malformed/incomplete providence or omen record is
        # dropped (conservative, never raise) rather than kept half-shaped --
        # a record missing expiresFrame could never expire, and one missing
        # memoryWritten on an omen could re-fire its memory write on the
        # very next restore-time expiry sweep.
        providence = raw.get("providence")
        out["providence"] = (
            providence if isinstance(providence, dict)
            and all(k in providence for k in ("id", "text", "createdFrame", "expiresFrame"))
            else None
        )
        omens_raw = raw.get("privateOmens")
        omens = {}
        if isinstance(omens_raw, dict):
            for k, v in omens_raw.items():
                if (isinstance(k, str) and isinstance(v, dict)
                        and all(kk in v for kk in ("id", "targetId", "text", "createdFrame", "expiresFrame"))):
                    v = dict(v)
                    v.setdefault("memoryWritten", False)
                    omens[k] = v
        out["privateOmens"] = omens
        events = raw.get("activeEvents")
        out["activeEvents"] = (
            [e for e in events if isinstance(e, dict)][:GOD_ACTIVE_EVENTS_CAP]
            if isinstance(events, list) else []
        )
        recent = raw.get("recentInterventions")
        out["recentInterventions"] = (
            [r for r in recent if isinstance(r, dict)][-GOD_RECENT_INTERVENTIONS_CAP:]
            if isinstance(recent, list) else []
        )
        divine_responses = raw.get("recentDivineResponses")
        out["recentDivineResponses"] = (
            [r for r in divine_responses if isinstance(r, dict)][-GOD_DIVINE_RESPONSE_LOG_MAX:]
            if isinstance(divine_responses, list) else []
        )
        if isinstance(out.get("providence"), dict):
            out["providence"].setdefault("ackedAgentIds", {})
        for omen in out.get("privateOmens", {}).values():
            if isinstance(omen, dict):
                omen.setdefault("acked", False)
        # godState v2: Matrix intervention maps — setdefault/backfill; drop malformed
        # entries conservatively (same discipline as privateOmens above).
        campaigns_raw = raw.get("whisperCampaigns")
        campaigns = {}
        if isinstance(campaigns_raw, dict):
            for k, v in campaigns_raw.items():
                if not isinstance(k, str) or not isinstance(v, dict):
                    continue
                targets_raw = v.get("targets")
                if not isinstance(targets_raw, dict):
                    continue
                targets = {}
                for tk, omen_id in targets_raw.items():
                    if isinstance(tk, (str, int)) and isinstance(omen_id, str) and omen_id.strip():
                        targets[str(tk)] = omen_id.strip()
                if not all(kk in v for kk in ("id", "theme", "createdFrame", "expiresFrame")):
                    continue
                v = dict(v)
                v["targets"] = targets
                v.setdefault("status", "active")
                campaigns[k] = v
        out["whisperCampaigns"] = campaigns
        sampling_raw = raw.get("agentSampling")
        sampling = {}
        if isinstance(sampling_raw, dict):
            for k, v in sampling_raw.items():
                if not isinstance(k, str) or not isinstance(v, dict):
                    continue
                if not all(kk in v for kk in ("id", "targetId", "model", "temperature", "createdFrame")):
                    continue
                if v.get("model") not in GOD_AGENT_SAMPLING_MODELS:
                    continue
                temp = v.get("temperature")
                if not isinstance(temp, (int, float)) or not math.isfinite(temp):
                    continue
                if not (GOD_AGENT_SAMPLING_TEMP_MIN <= float(temp) <= GOD_AGENT_SAMPLING_TEMP_MAX):
                    continue
                sampling[k] = dict(v)
        out["agentSampling"] = sampling
        for map_key in ("contextMasks", "decisionGates",
                        "burningBush", "anointments", "identityForges"):
            mraw = raw.get(map_key)
            cleaned = {}
            if isinstance(mraw, dict):
                for k, v in mraw.items():
                    if isinstance(k, str) and isinstance(v, dict):
                        cleaned[k] = v
            out[map_key] = cleaned
        zones_raw = raw.get("architectZones")
        out["architectZones"] = (
            [e for e in zones_raw if isinstance(e, dict)] if isinstance(zones_raw, list) else []
        )
        checkpoints_raw = raw.get("checkpoints")
        checkpoints = []
        if isinstance(checkpoints_raw, list):
            for e in checkpoints_raw:
                if not isinstance(e, dict):
                    continue
                if not all(k in e for k in ("id", "label", "frameTick", "path", "createdAt")):
                    continue
                checkpoints.append(dict(e))
        out["checkpoints"] = checkpoints
        digests_raw = raw.get("decisionDigests")
        digests = []
        if isinstance(digests_raw, list):
            for d in digests_raw:
                if not isinstance(d, dict):
                    continue
                agent_id = d.get("agentId")
                action = d.get("action")
                frame_tick = d.get("frameTick")
                if isinstance(agent_id, bool) or not isinstance(agent_id, int):
                    continue
                if not isinstance(action, str) or not action.strip():
                    continue
                if not isinstance(frame_tick, int):
                    continue
                entry = {
                    "frameTick": frame_tick,
                    "agentId": agent_id,
                    "action": action.strip(),
                }
                rh = d.get("reasoningHash")
                if isinstance(rh, str) and rh.strip():
                    entry["reasoningHash"] = rh.strip()[:16]
                digests.append(entry)
        out["decisionDigests"] = digests[-GOD_DECISION_DIGEST_CAP:]
        replays_raw = raw.get("dejaVuReplays")
        replays = {}
        if isinstance(replays_raw, dict):
            for k, v in replays_raw.items():
                if not isinstance(k, str) or not isinstance(v, dict):
                    continue
                if not all(kk in v for kk in ("id", "targetId", "steps", "currentIndex", "createdFrame")):
                    continue
                steps_raw = v.get("steps")
                if not isinstance(steps_raw, list):
                    continue
                steps = []
                for step in steps_raw:
                    if not isinstance(step, dict):
                        continue
                    if not isinstance(step.get("action"), str) or not step["action"].strip():
                        continue
                    if not isinstance(step.get("frameTick"), int):
                        continue
                    s = {"frameTick": step["frameTick"], "action": step["action"].strip()}
                    if isinstance(step.get("reasoningHash"), str) and step["reasoningHash"].strip():
                        s["reasoningHash"] = step["reasoningHash"].strip()[:16]
                    steps.append(s)
                if not steps:
                    continue
                v = dict(v)
                v["steps"] = steps
                v.setdefault("status", "active")
                replays[k] = v
        out["dejaVuReplays"] = replays
        for map_key in ("crowdCompulsions", "dreamBroadcasts"):
            mraw = raw.get(map_key)
            cleaned = {}
            if isinstance(mraw, dict):
                for k, v in mraw.items():
                    if isinstance(k, str) and isinstance(v, dict):
                        cleaned[k] = v
            out[map_key] = cleaned
        return out

    # --- decision digests + Déjà Vu replay (godState v3) ---
    def _decision_reasoning_hash(self, reasoning):
        if not isinstance(reasoning, str) or not reasoning.strip():
            return None
        return hashlib.sha256(reasoning.strip().encode("utf-8")).hexdigest()[:16]

    def _append_decision_digest(self, agent_id, decision):
        """Record one applied decision on the bounded digest ring. Lock held."""
        if not GOD_MODE_ENABLED:
            return
        if not isinstance(decision, dict):
            return
        action = decision.get("action")
        if not isinstance(action, str) or not action.strip():
            return
        god = self.civilization.setdefault("godState", self._default_god_state())
        digests = god.get("decisionDigests")
        if not isinstance(digests, list):
            digests = []
            god["decisionDigests"] = digests
        entry = {
            "frameTick": self.frameTick,
            "agentId": agent_id,
            "action": action.strip(),
        }
        rh = self._decision_reasoning_hash(decision.get("reasoning"))
        if rh:
            entry["reasoningHash"] = rh
        digests.append(entry)
        if len(digests) > GOD_DECISION_DIGEST_CAP:
            god["decisionDigests"] = digests[-GOD_DECISION_DIGEST_CAP:]

    def _god_agent_digest_steps(self, agent_id, max_steps=None):
        """Last K digest entries for one agent in chronological order."""
        if max_steps is None:
            max_steps = GOD_DEJA_VU_MAX_STEPS
        max_steps = max(1, min(int(max_steps), GOD_DEJA_VU_MAX_STEPS))
        god = self.civilization.get("godState") or {}
        digests = god.get("decisionDigests") or []
        if not isinstance(digests, list):
            return []
        matched = [
            d for d in digests
            if isinstance(d, dict) and d.get("agentId") == agent_id
            and isinstance(d.get("action"), str) and d["action"].strip()
            and isinstance(d.get("frameTick"), int)
        ]
        tail = matched[-max_steps:]
        steps = []
        for d in tail:
            step = {"frameTick": d["frameTick"], "action": d["action"].strip()}
            if isinstance(d.get("reasoningHash"), str) and d["reasoningHash"].strip():
                step["reasoningHash"] = d["reasoningHash"].strip()[:16]
            steps.append(step)
        return steps

    def _validate_god_deja_vu_replay(self, payload):
        if not GOD_DEJA_VU_REPLAY:
            return None, "deja vu replay is not enabled (GOD_DEJA_VU_REPLAY flag off)"
        target_id = payload.get("targetId")
        if isinstance(target_id, bool) or not isinstance(target_id, int):
            return None, "targetId must be an integer agent id"
        agent = self._find_agent_by_id(target_id)
        if agent is None:
            return None, "unknown target agent"
        if agent.get("deathFrame") is not None:
            return None, "target agent is deceased"
        max_steps_raw = payload.get("maxSteps")
        if max_steps_raw is None:
            max_steps = GOD_DEJA_VU_MAX_STEPS
        elif isinstance(max_steps_raw, bool) or not isinstance(max_steps_raw, int):
            return None, "maxSteps must be an integer"
        else:
            max_steps = max(1, min(max_steps_raw, GOD_DEJA_VU_MAX_STEPS))
        replay_steps = self._god_agent_digest_steps(target_id, max_steps)
        if not replay_steps:
            return None, "no decision digests for this agent"
        if self._god_deja_vu_session_total >= GOD_DEJA_VU_SESSION_CAP:
            return None, (
                f"deja vu replay session cap ({GOD_DEJA_VU_SESSION_CAP}) reached")
        if self._god_active_decision_gate_record(target_id) is not None:
            return None, "agent already has an active decision gate"
        replays = (self.civilization.get("godState") or {}).get("dejaVuReplays") or {}
        if isinstance(replays, dict):
            for rec in replays.values():
                if (isinstance(rec, dict) and rec.get("status") == "active"
                        and rec.get("targetId") == target_id):
                    return None, "agent already has an active deja vu replay"
        return {
            "targetId": target_id,
            "maxSteps": max_steps,
            "replaySteps": replay_steps,
        }, None

    def _validate_god_crowd_compulsion(self, payload):
        theme = None
        if payload.get("theme") is not None:
            theme, reason = self._normalize_divine_text(payload.get("theme"))
            if reason:
                return None, f"crowd_compulsion theme {reason}"
        targets_raw = payload.get("targets")
        if not isinstance(targets_raw, list) or not targets_raw:
            return None, "targets must be a non-empty list"
        if len(targets_raw) > GOD_CROWD_COMPULSION_MAX_TARGETS:
            return None, (
                f"targets may include at most {GOD_CROWD_COMPULSION_MAX_TARGETS} agents")
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
        duration = self._clamp_god_duration(duration_raw) if duration_raw is not None else None
        seen_targets = set()
        targets = []
        for idx, entry in enumerate(targets_raw):
            if not isinstance(entry, dict):
                return None, f"targets[{idx}] must be an object"
            target_id = entry.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, f"targets[{idx}].targetId must be an integer agent id"
            if target_id in seen_targets:
                return None, "duplicate targetId in targets"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, f"targets[{idx}]: unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, f"targets[{idx}]: target agent is deceased"
            pinned, reason = self._validate_god_pinned_decision_fields(
                agent, entry.get("pinnedDecision"))
            if reason:
                return None, reason
            seen_targets.add(target_id)
            targets.append({"targetId": target_id, "pinnedDecision": pinned})
        normalized = {"targets": targets}
        if theme is not None:
            normalized["theme"] = theme
        if duration is not None:
            normalized["durationFrames"] = duration
        if remaining is not None:
            normalized["remainingTurns"] = remaining
        return normalized, None

    def _validate_god_dream_broadcast(self, payload):
        duration_raw = payload.get("durationFrames")
        if duration_raw is None or isinstance(duration_raw, bool) or not isinstance(
                duration_raw, int):
            return None, "durationFrames is required and must be an integer"
        duration = self._clamp_god_duration(duration_raw)
        snapshot, reason = self._validate_god_dream_snapshot(payload.get("dreamSnapshot"))
        if reason:
            return None, reason
        target_ids_raw = payload.get("targetIds")
        if not isinstance(target_ids_raw, list) or not target_ids_raw:
            return None, "targetIds must be a non-empty list"
        if len(target_ids_raw) > GOD_DREAM_BROADCAST_MAX_TARGETS:
            return None, (
                f"targetIds may include at most {GOD_DREAM_BROADCAST_MAX_TARGETS} agents")
        seen_targets = set()
        target_ids = []
        for idx, target_id in enumerate(target_ids_raw):
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, f"targetIds[{idx}] must be an integer agent id"
            if target_id in seen_targets:
                return None, "duplicate targetId in targetIds"
            agent = self._find_agent_by_id(target_id)
            if agent is None:
                return None, f"targetIds[{idx}]: unknown target agent"
            if agent.get("deathFrame") is not None:
                return None, f"targetIds[{idx}]: target agent is deceased"
            seen_targets.add(target_id)
            target_ids.append(target_id)
        return {
            "durationFrames": duration,
            "dreamSnapshot": snapshot,
            "targetIds": target_ids,
        }, None

    def _spawn_deja_vu_compulsion_step(self, parent):
        """Start the compulsion gate for parent.currentIndex. Lock held."""
        idx = int(parent.get("currentIndex") or 0)
        steps = parent.get("steps") or []
        if idx >= len(steps):
            self._finalize_deja_vu_replay(parent.get("id"), "completed")
            return None
        step = steps[idx]
        action = step.get("action")
        if not isinstance(action, str) or not action.strip():
            self._finalize_deja_vu_replay(parent.get("id"), "failed")
            return None
        target_id = parent.get("targetId")
        intervention_id = self._next_intervention_id()
        remaining_steps = max(1, len(steps) - idx)
        expires_frame = self.frameTick + (
            GOD_GUIDANCE_DEFAULT_DURATION_FRAMES * remaining_steps)
        record = {
            "id": intervention_id,
            "targetId": target_id,
            "mode": "compulsion",
            "pinnedDecision": {
                "action": action.strip(),
                "reasoning": "Déjà vu replay.",
            },
            "remainingTurns": 1,
            "createdFrame": self.frameTick,
            "expiresFrame": expires_frame,
            "dejaVuReplayId": parent.get("id"),
            "dejaVuStepIndex": idx,
        }
        parent["currentGateId"] = intervention_id
        return self._god_set_decision_gate(target_id, record, "decision_compulsion")

    def _advance_deja_vu_after_step(self, replay_id, completed_index):
        god = self.civilization.get("godState") or {}
        replays = god.get("dejaVuReplays") or {}
        parent = replays.get(replay_id) if isinstance(replays, dict) else None
        if not isinstance(parent, dict) or parent.get("status") != "active":
            return
        if int(parent.get("currentIndex") or 0) != int(completed_index):
            return
        parent["currentIndex"] = int(completed_index) + 1
        if parent["currentIndex"] >= len(parent.get("steps") or []):
            self._finalize_deja_vu_replay(replay_id, "completed")
        else:
            self._spawn_deja_vu_compulsion_step(parent)

    def _finalize_deja_vu_replay(self, replay_id, status):
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        replays = god.get("dejaVuReplays")
        if not isinstance(replays, dict):
            return None
        parent = replays.pop(replay_id, None)
        if not isinstance(parent, dict):
            return None
        parent["status"] = status
        self._log_divine(replay_id, None, "deja_vu_replay", parent,
                         {"status": status, "stepCount": len(parent.get("steps") or [])},
                         status, public=False)
        return parent

    def _close_deja_vu_replay(self, replay_id, status):
        """Cancel a replay parent and any in-flight gate. Lock held."""
        god = self.civilization.get("godState")
        if not isinstance(god, dict):
            return None
        replays = god.get("dejaVuReplays")
        parent = replays.get(replay_id) if isinstance(replays, dict) else None
        if not isinstance(parent, dict):
            return None
        target_id = parent.get("targetId")
        gates = god.get("decisionGates") or {}
        gate = gates.get(str(target_id)) if target_id is not None else None
        if isinstance(gate, dict) and gate.get("dejaVuReplayId") == replay_id:
            self._close_decision_gate(str(target_id), "cancelled")
        return self._finalize_deja_vu_replay(replay_id, status)

    def _god_apply_deja_vu_replay(self, payload):
        replay_id = self._next_intervention_id()
        target_id = payload["targetId"]
        steps = copy.deepcopy(payload["replaySteps"])
        parent = {
            "id": replay_id,
            "targetId": target_id,
            "steps": steps,
            "currentIndex": 0,
            "status": "active",
            "createdFrame": self.frameTick,
        }
        god = self.civilization["godState"]
        god.setdefault("dejaVuReplays", {})[replay_id] = parent
        self._god_deja_vu_session_total += 1
        gate_result = self._spawn_deja_vu_compulsion_step(parent)
        agent = self._find_agent_by_id(target_id)
        self._god_record_intervention({
            "id": replay_id, "kind": "deja_vu_replay", "frameTick": self.frameTick,
            "targetId": target_id, "stepCount": len(steps),
            "maxSteps": payload.get("maxSteps"), "status": "applied", "public": False,
        })
        self._log_divine(replay_id, None, "deja_vu_replay", payload,
                         {"targetId": target_id, "stepCount": len(steps)},
                         "applied", public=False)
        return {
            "interventionId": replay_id,
            "kind": "deja_vu_replay",
            "targetId": target_id,
            "targetName": agent["name"] if agent else None,
            "stepCount": len(steps),
            "firstGateId": (gate_result or {}).get("interventionId"),
        }

    # --- stored-text contract ---
    def _normalize_divine_text(self, raw, max_chars=GOD_TEXT_MAX_CHARS, max_bytes=GOD_TEXT_MAX_BYTES):
        """Single normalizer used by every divine text field (docs/archive/plan-sovereign-god-mode-v2.md
        section "Stored-content safety"): Unicode NFC, reject NUL and C0/C1
        controls other than ordinary space, reject embedded newlines, enforce
        char AND UTF-8-byte limits post-normalization, return plain text --
        never HTML. Returns (normalized_text, None) on success or
        (None, reason) on rejection; `reason` is short and secret-free (safe
        to return over HTTP / write to divine.jsonl)."""
        if not isinstance(raw, str):
            return None, "must be a string"
        normalized = unicodedata.normalize("NFC", raw)
        for ch in normalized:
            cp = ord(ch)
            if ch in ("\n", "\r"):
                return None, "must not contain newlines"
            if cp == 0 or cp < 0x20 or 0x7F <= cp <= 0x9F:
                return None, "must not contain NUL or control characters"
        normalized = normalized.strip()
        if not normalized:
            return None, "must not be empty"
        if len(normalized) > max_chars:
            return None, f"exceeds {max_chars} characters"
        if len(normalized.encode("utf-8")) > max_bytes:
            return None, f"exceeds {max_bytes} bytes"
        return normalized, None

    def _normalize_god_presentation(self, raw):
        """Cosmetic stage direction for public Voice commands only.
        Returns (canonical_value, None) where canonical_value is 'soft' or
        'thunder', or (None, reason) on rejection. Omitted/empty/'soft' →
        'soft'."""
        if raw is None or raw == "":
            return "soft", None
        if not isinstance(raw, str):
            return None, "presentation must be 'soft' or 'thunder'"
        val = raw.strip().lower()
        if val in ("soft", "thunder"):
            return val, None
        return None, "presentation must be 'soft' or 'thunder'"

    def _god_presentation_payload_field(self, presentation):
        """Emit presentation in normalized payload only when thunder."""
        if presentation == "thunder":
            return {"presentation": "thunder"}
        return {}

    # --- command envelope ---
    # Kinds already named in the v2 catalog (docs/archive/plan-sovereign-god-mode-v2.md) that this phase still
    # does not implement -- validated far enough to be rejected cleanly
    # rather than falling into the generic "unknown kind" branch. Phase 4
    # implements agent_vitals/grant_resource/structure_condition (removed
    # below); story_event (Phase 5, timed/composite) remains deferred.
    _GOD_FUTURE_KINDS = frozenset()

    # --- Sovereign God mode Phase 5: storyteller events ---
    _GOD_PRIMITIVE_KINDS = ("agent_vitals", "grant_resource", "structure_condition")

    def _god_active_event_holding_key(self, key, exclude_id=None):
        """The currently-active activeEvents record that carries `key` in
        its modifiers, or None if the key is unoccupied. Shared by the
        one-value-per-key conflict check (both preview time and apply-time
        revalidation re-run this against CURRENT state) and by
        _divine_modifier's read side staying consistent with what
        "occupied" means."""
        god = self.civilization.get("godState") or {}
        for event in god.get("activeEvents") or []:
            if not isinstance(event, dict) or event.get("status") != "active":
                continue
            if exclude_id is not None and event.get("id") == exclude_id:
                continue
            modifiers = event.get("modifiers")
            if isinstance(modifiers, dict) and key in modifiers:
                return event
        return None

    def _validate_god_story_event(self, payload):
        """Validate + canonicalize a story_event command. NO mutation.
        Atomic by construction: every sub-component (title, narration,
        visibility/target, duration, modifiers, primitives, providence,
        replaceEffectId conflict check) is validated here before anything is
        accepted; a single invalid component rejects the WHOLE envelope, so
        god_apply's revalidate-then-apply sequence can never partially
        apply a story event."""
        title, reason = self._normalize_divine_text(payload.get("title"), max_chars=GOD_EVENT_TITLE_MAX_CHARS)
        if reason:
            return None, f"story_event title {reason}"
        narration, reason = self._normalize_divine_text(payload.get("narration"))
        if reason:
            return None, f"story_event narration {reason}"

        visibility = payload.get("visibility", "public")
        if visibility not in ("public", "private"):
            return None, 'visibility must be "public" or "private"'
        target_id = None
        if visibility == "private":
            target_id = payload.get("targetId")
            if isinstance(target_id, bool) or not isinstance(target_id, int):
                return None, "targetId must be an integer agent id for a private story_event"
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

        replace_effect_id = payload.get("replaceEffectId")
        if replace_effect_id is not None and (
            not isinstance(replace_effect_id, str) or not replace_effect_id.strip()
        ):
            return None, "replaceEffectId must be a non-empty string"
        if isinstance(replace_effect_id, str):
            replace_effect_id = replace_effect_id.strip()
            replaced_event = next(
                (e for e in (self.civilization.get("godState") or {}).get("activeEvents") or []
                 if isinstance(e, dict) and e.get("id") == replace_effect_id
                 and e.get("status") == "active"), None)
            if replaced_event is None:
                return None, "replaceEffectId does not name an active event"

        modifiers_raw = payload.get("modifiers")
        modifiers = {}
        if modifiers_raw is not None:
            if not isinstance(modifiers_raw, dict):
                return None, "modifiers must be an object"
            if len(modifiers_raw) > GOD_STORY_EVENT_MAX_MODIFIERS:
                return None, f"modifiers may name at most {GOD_STORY_EVENT_MAX_MODIFIERS} keys"
            for mkey, mvalue in modifiers_raw.items():
                if mkey not in GOD_MODIFIER_RANGES:
                    return None, f"unknown modifier key '{mkey}'"
                if isinstance(mvalue, bool) or not isinstance(mvalue, (int, float)):
                    return None, f"modifier '{mkey}' must be a number"
                if not math.isfinite(mvalue):
                    return None, f"modifier '{mkey}' must be finite"
                lo, hi = GOD_MODIFIER_RANGES[mkey]
                if not (lo <= mvalue <= hi):
                    return None, f"modifier '{mkey}' must be between {lo} and {hi}"
                # One active value per key: reject an occupied key unless
                # replaceEffectId names the event that holds it.
                holder = self._god_active_event_holding_key(mkey)
                if holder is not None and holder.get("id") != replace_effect_id:
                    return None, (f"modifier '{mkey}' already has an active effect "
                                  f"(id {holder.get('id')}) -- supply replaceEffectId to replace it")
                modifiers[mkey] = float(mvalue)

        primitives_raw = payload.get("primitives")
        primitives = []
        if primitives_raw is not None:
            if not isinstance(primitives_raw, list):
                return None, "primitives must be a list"
            if len(primitives_raw) > GOD_STORY_EVENT_MAX_PRIMITIVES:
                return None, f"primitives may contain at most {GOD_STORY_EVENT_MAX_PRIMITIVES} entries"
            for prim in primitives_raw:
                if not isinstance(prim, dict):
                    return None, "each primitive must be an object"
                prim_kind = prim.get("kind")
                if prim_kind not in self._GOD_PRIMITIVE_KINDS:
                    return None, f"primitive kind must be one of {self._GOD_PRIMITIVE_KINDS}"
                prim_normalized, prim_reason = self._validate_god_envelope(
                    {"kind": prim_kind, "payload": prim.get("payload") or {}})
                if prim_reason:
                    return None, f"primitive '{prim_kind}': {prim_reason}"
                primitives.append(prim_normalized)

        providence_raw = payload.get("providence")
        providence_payload = None
        if providence_raw is not None:
            if not isinstance(providence_raw, dict):
                return None, "providence must be an object"
            prov_text, reason = self._normalize_divine_text(providence_raw.get("text"))
            if reason:
                return None, f"story_event providence text {reason}"
            providence_payload = {"text": prov_text}

        return {
            "kind": "story_event",
            "payload": {
                "title": title, "narration": narration,
                "visibility": visibility, "targetId": target_id,
                "durationFrames": duration,
                "modifiers": modifiers, "primitives": primitives,
                "providence": providence_payload,
                "replaceEffectId": replace_effect_id,
            },
        }, None

    def _clamp_god_duration(self, raw):
        """Sovereign God mode (Phase 3): silently clamp a caller-supplied
        durationFrames into range, or fall back to the default when omitted
        -- the plan's preview contract clamps rather than rejects in-range
        violations. `raw` has already been type-checked (int, not bool) by
        the caller when not None."""
        if raw is None:
            return GOD_GUIDANCE_DEFAULT_DURATION_FRAMES
        return max(GOD_GUIDANCE_MIN_DURATION_FRAMES,
                   min(GOD_GUIDANCE_MAX_DURATION_FRAMES, raw))

