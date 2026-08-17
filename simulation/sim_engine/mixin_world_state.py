"""Phase 6b mixin: world-state bookkeeping slice of SimEngine.

Extracted unchanged (pure move, no behavior change) from core.py's SimEngine
class body — the contiguous method range from `_init_state_delta_sets`
through `_structure_type_built` (formerly core.py lines ~495-2038). Covers:
state-delta dirty tracking (Contract 2), logging/memory helpers
(`_push_activity`, `_push_memory`, `_god_memory_*`), agent lookups
(`_find_agent*`), the district-bucketed proximity scan (Sid-parity Phase 6),
districts/roads/movement, survival, Sage emergency, project helpers
(concurrent per-district builds), and resource ecology (Phase B,
ECOLOGY_ENABLED) through `_structure_type_built`.

`__init__`/`_select_active_defs`/`_make_agents`/`_reset_world` stay on the
concrete `SimEngine` class in core.py — they are intimately tied to
construction, so they were left there per the Phase 6b plan rather than
moved here.

Exec-loaded into the shared package namespace — see simulation/sim_engine/__init__.py.
"""

# NOTE: constants.py/persistence.py/helpers.py names (SURVIVAL_ENABLED,
# ECOLOGY_ENABLED, STOCK_LOW_RATIO, WILDLIFE_ENABLED, ...) are NOT imported
# here. They live in the exec()-shared namespace — see
# simulation/sim_engine/__init__.py.


class _WorldStateMixin:
    """Mixin slice of SimEngine: dirty tracking, logging/memory, agent
    lookups, districts/roads/movement, survival, Sage emergency, project
    helpers, and resource ecology. See module docstring for exact scope."""

    # --- /state delta dirty tracking (Contract 2) ---
    def _init_state_delta_sets(self):
        """Ensure last-mod frame maps exist (safe during _reset_world before bump)."""
        self._dirty_agents = {}
        self._dirty_civ_keys = {}
        self._dirty_structure_upserts = {}
        self._dirty_structure_removals = {}
        self._dirty_structure_sprites = {}
        self._dirty_top_keys = {}
        self._paused_mod_frame = None
        self._config_mod_frame = None
        self._dirty_this_frame = set()

    def _on_world_replaced(self):
        """Bump generation and reset dirty maps after reset/restore/cold start."""
        self.stateGeneration = getattr(self, "stateGeneration", 0) + 1
        self._init_state_delta_sets()
        self._paused_mod_frame = self.frameTick
        self._dirty_this_frame.add("paused")
        self._last_reset_frame = self.frameTick

    def _delta_include_mod(self, last_mod, since_int, frame_tag=None):
        """Whether a last-mod stamp should appear in a delta for *since_int*."""
        if last_mod > since_int:
            return True
        if (frame_tag and since_int == self.frameTick
                and frame_tag in self._dirty_this_frame):
            return True
        return False

    def _discard_frame_tags(self, since_int, *tags):
        """Drop same-frame pending tags once a client has caught up to *last_mod*."""
        if since_int >= self.frameTick:
            for tag in tags:
                self._dirty_this_frame.discard(tag)

    def _has_state_dirty(self, since=0):
        """True when any tracked key was modified after frame *since*."""
        if since == self.frameTick and self._dirty_this_frame:
            return True
        if any(v > since for v in self._dirty_agents.values()):
            return True
        if any(v > since for v in self._dirty_civ_keys.values()):
            return True
        if any(v > since for v in self._dirty_structure_upserts.values()):
            return True
        if any(v > since for v in self._dirty_structure_removals.values()):
            return True
        if any(v > since for v in self._dirty_top_keys.values()):
            return True
        if self._paused_mod_frame is not None and self._paused_mod_frame > since:
            return True
        if self._config_mod_frame is not None and self._config_mod_frame > since:
            return True
        return False

    def _prune_state_dirty(self, ft):
        """Drop last-mod entries older than the delta gap window."""
        cutoff = ft - STATE_DELTA_MAX_GAP

        def _prune_map(m):
            for k in list(m.keys()):
                if m[k] <= cutoff:
                    del m[k]

        _prune_map(self._dirty_agents)
        _prune_map(self._dirty_civ_keys)
        _prune_map(self._dirty_structure_upserts)
        _prune_map(self._dirty_structure_removals)
        _prune_map(self._dirty_structure_sprites)
        _prune_map(self._dirty_top_keys)
        if self._paused_mod_frame is not None and self._paused_mod_frame <= cutoff:
            self._paused_mod_frame = None
        if self._config_mod_frame is not None and self._config_mod_frame <= cutoff:
            self._config_mod_frame = None

    def _mark_agent_dirty(self, agent_or_id):
        aid = agent_or_id["id"] if isinstance(agent_or_id, dict) else agent_or_id
        self._dirty_agents[aid] = self.frameTick
        self._dirty_this_frame.add(f"a:{aid}")

    def _mark_civ_dirty(self, *keys):
        ft = self.frameTick
        for key in keys:
            self._dirty_civ_keys[key] = ft
            self._dirty_this_frame.add(f"c:{key}")

    def _mark_structure_dirty(self, structure, sprite_changed=False):
        sid = structure["id"] if isinstance(structure, dict) else structure
        ft = self.frameTick
        self._dirty_structure_upserts[sid] = ft
        self._dirty_civ_keys["structures"] = ft
        self._dirty_this_frame.add(f"su:{sid}")
        self._dirty_this_frame.add("c:structures")
        if sprite_changed:
            self._dirty_structure_sprites[sid] = ft
            self._dirty_this_frame.add(f"sp:{sid}")
            self._persist_dirty_structure_sprites.add(sid)

    def _mark_structure_removed(self, structure_id):
        sid = structure_id["id"] if isinstance(structure_id, dict) else structure_id
        ft = self.frameTick
        self._dirty_structure_removals[sid] = ft
        self._dirty_structure_upserts.pop(sid, None)
        self._dirty_structure_sprites.pop(sid, None)
        self._persist_dirty_structure_sprites.discard(sid)
        self._persist_sprite_removals.add(sid)
        self._dirty_civ_keys["structures"] = ft
        self._dirty_this_frame.add(f"sr:{sid}")
        self._dirty_this_frame.add("c:structures")

    def _mark_top_dirty(self, *keys):
        ft = self.frameTick
        for key in keys:
            self._dirty_top_keys[key] = ft
            self._dirty_this_frame.add(f"t:{key}")

    # --- logging helpers (mirror pushActivity / pushCommunication) ---
    def _push_activity(self, line, decision_id=None):
        self.activityLog.insert(0, line)
        del self.activityLog[30:]
        self._mark_top_dirty("activity")
        try:
            self.d["log_activity"](line, self.frameTick, decision_id=decision_id)
        except Exception:
            pass

    def _push_communication(self, kind, frm, to, message, outcome=None, source=None):
        entry = {"kind": kind, "from": frm, "to": to, "message": message}
        if outcome:
            entry["outcome"] = outcome
        if source:
            # Explicit non-emergent attribution (Sovereign God mode Phase 2's
            # "source: divine" contract). Additive-only: every pre-existing
            # caller passes no source and gets byte-identical entries/log
            # calls to before this parameter existed.
            entry["source"] = source
        self.conversationLog.insert(0, entry)
        del self.conversationLog[100:]
        self._mark_top_dirty("conversation")
        try:
            log_outcome = outcome
            if source:
                log_outcome = dict(outcome or {})
                log_outcome["source"] = source
            self.d["log_conversation"](frm, to, message, self.frameTick,
                                       kind=kind, outcome=log_outcome)
        except Exception:
            pass

    def _push_conversation(self, frm, to, message):
        self._push_communication("speech", frm, to, message)

    def _log_benchmark(self, metric, value, detail=None):
        if not BENCHMARKS_ENABLED:
            return
        try:
            self.d["log_benchmark"](metric, value, self.frameTick, detail)
        except Exception:
            pass

    # --- memory (tiered; vector store lives in server) ---
    _HIGH_SAL_WORDS = ("built", "collapsed", "revived", "approved", "rejected",
                       "started", "proposed", "tasked", "reached level",
                       "became a", "enacted", "voted", "switched")
    _LOW_SAL_WORDS = ("rested", "wandered", "found nothing", "has nothing",
                      "heads to", "moves toward", "looked for")

    def _salience_for(self, line):
        low = (line or "").lower()
        if any(w in low for w in self._HIGH_SAL_WORDS):
            return 0.85
        if any(w in low for w in self._LOW_SAL_WORDS):
            return 0.2
        return 0.5

    def _push_memory(self, agent, line, kind=None):
        m = agent["memory"]
        sal = self._salience_for(line)
        m["working"].append(line)
        while len(m["working"]) > WORKING_MEM_CAP:
            evicted = m["working"].pop(0)
            if self._salience_for(evicted) >= 0.7:
                m["shortTerm"].append(evicted)
                if len(m["shortTerm"]) > SHORT_MEM_CAP:
                    m["shortTerm"].pop(0)
        try:
            self.d["memory_store"].store(agent["name"], line, salience=sal,
                                         kind=kind or "event", frame_tick=self.frameTick)
        except Exception:
            pass

    def _god_memory_insert(self, agent, text, salience, kind=GOD_MEMORY_DEFAULT_KIND):
        """Divine Matrix Phase 3: insert a false/recall memory for one agent.
        Mirrors into local working/shortTerm tiers and MemoryStore. Never
        touches public activity/communication/chronicle."""
        text = (text or "").strip()
        if not text:
            return None
        try:
            salience = max(0.0, min(1.0, float(salience)))
        except (TypeError, ValueError):
            salience = 0.5
        line = text[:280]
        m = agent["memory"]
        m["working"].append(line)
        while len(m["working"]) > WORKING_MEM_CAP:
            evicted = m["working"].pop(0)
            if salience >= 0.7:
                m["shortTerm"].append(evicted)
                if len(m["shortTerm"]) > SHORT_MEM_CAP:
                    m["shortTerm"].pop(0)
        entry = None
        ms = self.d.get("memory_store")
        if ms is not None:
            try:
                entry = ms.store(agent["name"], line, salience=salience,
                                 kind=kind or GOD_MEMORY_DEFAULT_KIND,
                                 frame_tick=self.frameTick)
            except Exception:
                pass
        return entry

    def _god_memory_delete(self, agent, *, keyword=None, frameFrom=None,
                           frameTo=None, kinds=None):
        """Delete matching MemoryStore rows and purge keyword hits from local
        working/shortTerm lists. Requires at least one filter (validated at
        god envelope). Returns count deleted from MemoryStore."""
        ms = self.d.get("memory_store")
        deleted = 0
        if ms is not None:
            try:
                deleted = ms.delete_where(
                    agent=agent["name"],
                    keyword=keyword,
                    frame_from=frameFrom,
                    frame_to=frameTo,
                    kinds=kinds,
                )
            except Exception:
                deleted = 0
        if keyword:
            kw = keyword.lower()
            mem = agent.get("memory") or {}
            for tier in ("working", "shortTerm"):
                rows = mem.get(tier)
                if isinstance(rows, list):
                    mem[tier] = [line for line in rows
                                 if kw not in (line or "").lower()]
        return deleted

    def _god_memory_match_count(self, agent, *, keyword=None, frameFrom=None,
                                frameTo=None, kinds=None):
        ms = self.d.get("memory_store")
        if ms is None:
            return 0
        try:
            return ms.count_where(
                agent=agent["name"],
                keyword=keyword,
                frame_from=frameFrom,
                frame_to=frameTo,
                kinds=kinds,
            )
        except Exception:
            return 0

    def _god_belief_plant(self, agent, *, belief_id=None, custom_text=None,
                          salience=0.7, plant_in_meme_texts=False):
        """Plant a belief on one agent (private). Returns (belief_id, tenet)
        or (None, reason) on failure."""
        if not MEMES_ENABLED:
            return None, "memes disabled"
        registry = self._belief_registry()
        tenet = None
        resolved_id = belief_id
        if isinstance(custom_text, str) and custom_text.strip():
            tenet = custom_text.strip()[:160]
        if resolved_id:
            if resolved_id not in registry and resolved_id not in MEMES:
                return None, "unknown belief id"
            if not tenet:
                tenet = self._belief_text(resolved_id)
        else:
            if not tenet:
                return None, "belief text is required when beliefId is omitted"
            digest = hashlib.sha256(tenet.encode("utf-8")).hexdigest()[:8]
            resolved_id = f"divine_{digest}"
            if resolved_id not in registry:
                registry[resolved_id] = {
                    "id": resolved_id,
                    "name": "Divine planting",
                    "tenet": tenet,
                    "affinity": [],
                    "authoredBy": "divine",
                    "createdFrame": self.frameTick,
                    "seed": False,
                }
        if plant_in_meme_texts and tenet:
            self.civilization.setdefault("memeTexts", {})[resolved_id] = tenet
        agent.setdefault("beliefs", set()).add(resolved_id)
        self._god_memory_insert(
            agent, f"I believe: {tenet}", salience, kind=GOD_BELIEF_MEMORY_KIND)
        return resolved_id, tenet

    def _memory_for_prompt(self, agent):
        m = agent["memory"]
        lines = m["longTerm"][-3:] + m["shortTerm"][-4:] + m["working"][-4:]
        if WIKI_MEMORY:
            wiki = agent.get("memoryWiki") or {}
            wiki_lines = []
            for key, label in (("relationships", "wiki relationships"),
                                ("goals", "wiki goals"),
                                ("lessons", "wiki lessons")):
                text = wiki.get(key)
                if text:
                    wiki_lines.append(f"{label}: {text}")
            # Prepended, never replacing the existing longTerm/shortTerm/
            # working slices -- nothing the LLM previously saw is removed.
            lines = wiki_lines + lines
        return lines

    # --- agent lookups + movement ---
    def _find_agent(self, name):
        for a in self.agents:
            if a["name"] == name:
                return a
        return None

    def _find_agent_by_id(self, agent_id):
        """Sovereign God mode (Phase 3): omens/miracles target stable int
        ids, never names (see docs/archive/plan-sovereign-god-mode-v2.md "Agent ids are stable ints")."""
        for a in self.agents:
            if a["id"] == agent_id:
                return a
        return None

    # --- Sid-parity Phase 6: district-bucketed proximity scan ---
    # _get_nearby_agents/_get_nearby_detailed run once per agent per think
    # payload build (the hottest per-tick pass over the roster), each doing a
    # flat O(n) scan. At roster 20 that's ~400 comparisons per full think
    # round -- not huge, but the district bucket below turns it into a scan
    # of just the agents sharing (or bordering) this agent's district, which
    # is what the plan calls out. Districts are far bigger than NEARBY_RADIUS
    # in the common case, but a few starter districts sit closer together
    # than that (e.g. village_core/market are only ~70px apart, narrower than
    # the 80-unit radius) -- so a same-district-only bucket would silently
    # drop cross-border neighbors that a flat scan would have found. The
    # adjacency cache below fixes that: an agent's candidate pool is its own
    # district's bucket plus every other district whose bounds, expanded by
    # NEARBY_RADIUS, still reach this district -- so results stay identical
    # to the flat O(n) scan for any hand-placed position, just computed over
    # a much smaller candidate set at scale.
    def _rebuild_district_buckets(self):
        buckets = {}
        for o in self.agents:
            buckets.setdefault(o.get("currentDistrict"), []).append(o)
        self._district_agent_buckets = buckets
        self._district_agent_buckets_frame = self.frameTick

    def _district_adjacency_for(self, did):
        districts = self.civilization["districts"]
        cache = getattr(self, "_district_adjacency", None)
        if cache is None or getattr(self, "_district_adjacency_n", None) != len(districts):
            cache = {}
            items = list(districts.items())
            for a_id, a_d in items:
                eb = {"x1": a_d["bounds"]["x1"] - NEARBY_RADIUS, "y1": a_d["bounds"]["y1"] - NEARBY_RADIUS,
                      "x2": a_d["bounds"]["x2"] + NEARBY_RADIUS, "y2": a_d["bounds"]["y2"] + NEARBY_RADIUS}
                neighbors = {a_id}
                for b_id, b_d in items:
                    if b_id == a_id:
                        continue
                    if _rects_overlap(eb, b_d["bounds"]):
                        neighbors.add(b_id)
                cache[a_id] = neighbors
            self._district_adjacency = cache
            self._district_adjacency_n = len(districts)
        return cache.get(did) or {did}

    def _nearby_candidate_pool(self, agent):
        """Agents worth an actual distance check against `agent` -- its own
        district bucket plus any district close enough to matter, per the
        adjacency cache above. Falls back to a full flat scan if the agent
        has no currentDistrict (shouldn't happen in practice, but keeps this
        provably never less correct than the old scan)."""
        did = agent.get("currentDistrict")
        if not did:
            return self.agents
        if getattr(self, "_district_agent_buckets_frame", None) != self.frameTick:
            self._rebuild_district_buckets()
        pool = []
        for other_id in self._district_adjacency_for(did):
            pool.extend(self._district_agent_buckets.get(other_id, ()))
        return pool

    def _get_nearby_agents(self, agent):
        near = []
        for o in self._nearby_candidate_pool(agent):
            if o is agent:
                continue
            if _dist(agent["x"], agent["y"], o["x"], o["y"]) <= NEARBY_RADIUS:
                near.append(o["name"])
        return near

    def _get_nearby_detailed(self, agent):
        near = []
        for o in self._nearby_candidate_pool(agent):
            if o is agent:
                continue
            d = _dist(agent["x"], agent["y"], o["x"], o["y"])
            if d <= NEARBY_RADIUS:
                entry = {"name": o["name"], "role": o["role"],
                         "food": o["resources"].get("food", 0),
                         "wood": o["resources"].get("wood", 0),
                         "gold": o["resources"].get("gold", 0)}
                stigmata = self._anointment_stigmata_tags(o["id"])
                if stigmata:
                    entry["stigmata"] = stigmata
                near.append((d, entry))
        # C3: sort nearest-first before the MAX_NEARBY_AGENTS_PROMPT cap below
        # so a crowded radius always keeps the closest agents, not an
        # arbitrary iteration-order slice.
        near.sort(key=lambda pair: pair[0])
        return [item for _, item in near[:MAX_NEARBY_AGENTS_PROMPT]]

    def _find_nearest_agent(self, agent):
        best, best_d = None, float("inf")
        for o in self.agents:
            if o is agent:
                continue
            dd = _dist(agent["x"], agent["y"], o["x"], o["y"])
            if dd < best_d:
                best_d, best = dd, o
        return best

    def _distance_to(self, a, b):
        return _dist(a["x"], a["y"], b["x"], b["y"])

    # --- districts + roads ---
    def _bump_districts_epoch(self):
        """Content revision for GET /districts.js ?since= polls."""
        self.districtsEpoch = getattr(self, "districtsEpoch", 0) + 1

    @staticmethod
    def _district_bounds_share_edge(a, b):
        """Edge-sharing adjacency — same geometry as _claim_adjacent_frontier_pair."""
        if a["y1"] == b["y1"] and a["y2"] == b["y2"]:
            if a["x2"] == b["x1"] or b["x2"] == a["x1"]:
                return True
        if a["x1"] == b["x1"] and a["x2"] == b["x2"]:
            if a["y2"] == b["y1"] or b["y2"] == a["y1"]:
                return True
        return False

    def _district_has_adjacent_kind(self, did, kind):
        districts = self.civilization["districts"]
        bounds = districts[did]["bounds"]
        for other_id, other in districts.items():
            if other_id == did or other.get("kind") != kind:
                continue
            if self._district_bounds_share_edge(bounds, other["bounds"]):
                return True
        return False

    def _inland_founded_districts_to_revert(self):
        """Founded beach/ocean districts missing their coastal pair (starter ids exempt)."""
        districts = self.civilization["districts"]
        to_revert = []
        for did, d in districts.items():
            kind = d.get("kind")
            if kind == "beach" and did != "beach":
                if not self._district_has_adjacent_kind(did, "ocean"):
                    to_revert.append(did)
            elif kind == "ocean" and did != "ocean":
                if not self._district_has_adjacent_kind(did, "beach"):
                    to_revert.append(did)
        return to_revert

    def _migration_fallback_district(self, removed_did):
        c = self.civilization
        removed_kind = c["districts"].get(removed_did, {}).get("kind")
        prefer = "beach" if removed_kind in ("beach", "ocean") else "village"
        for kind in (prefer, "village", "beach", "farm"):
            for did, d in c["districts"].items():
                if did != removed_did and d.get("kind") == kind:
                    return did
        return next((did for did in c["districts"] if did != removed_did), None)

    def _remove_road_gate(self, gate_id):
        c = self.civilization
        c["roadNodes"].pop(gate_id, None)
        c["roadEdges"] = [
            e for e in c["roadEdges"] if e[0] != gate_id and e[1] != gate_id]

    def _relocate_or_drop_structures_from_district(self, removed_did):
        c = self.civilization
        dropped = []
        for s in list(c.get("structures") or []):
            if s.get("districtId") != removed_did:
                continue
            kind = PROJECT_KIND.get(s.get("type"), "village")
            dest = None
            for did in self._buildable_district_ids():
                if did == removed_did:
                    continue
                if c["districts"][did].get("kind") != kind:
                    continue
                spot = self._find_structure_spot(
                    did, footprint=self._structure_footprint(s), ignore_id=s.get("id"))
                if spot:
                    dest = (did, spot)
                    break
            if dest:
                did, spot = dest
                s["districtId"] = did
                s["x"] = spot["x"]
                s["y"] = spot["y"]
            else:
                dropped.append(s)
        if dropped:
            dropped_ids = {s.get("id") for s in dropped}
            c["structures"] = [s for s in c["structures"] if s.get("id") not in dropped_ids]
            c["reorgTasks"] = [
                t for t in c.get("reorgTasks", [])
                if t.get("structureId") not in dropped_ids]
            for a in self.agents:
                if a.get("reorgTask") in dropped_ids:
                    a["reorgTask"] = None
        return dropped

    def _reassign_agents_from_district(self, removed_did):
        fallback = self._migration_fallback_district(removed_did)
        if not fallback:
            return
        for agent in self.agents:
            if agent.get("currentDistrict") != removed_did:
                continue
            near_node = self._nearest_road_node(agent["x"], agent["y"])
            node_district = None
            if near_node:
                for did, d in self.civilization["districts"].items():
                    if did != removed_did and d.get("entryNode") == near_node:
                        node_district = did
                        break
            dest = node_district or fallback
            if dest not in self.civilization["districts"]:
                dest = fallback
            db = self.civilization["districts"][dest]["bounds"]
            agent["currentDistrict"] = dest
            agent["x"] = (db["x1"] + db["x2"]) / 2 + (random.random() - 0.5) * 40
            agent["y"] = (db["y1"] + db["y2"]) / 2 + (random.random() - 0.5) * 40
            agent["waypoints"] = []
            agent["targetX"] = agent["x"]
            agent["targetY"] = agent["y"]

    def _revert_founded_district(self, did):
        c = self.civilization
        for plot in c.get("frontierPlots") or []:
            if plot.get("claimedBy") == did:
                plot["claimed"] = False
                plot["claimedBy"] = None
        dropped = self._relocate_or_drop_structures_from_district(did)
        self._reassign_agents_from_district(did)
        c["wildlife"] = [w for w in c.get("wildlife", []) if w.get("districtId") != did]
        lit = c.get("litDistricts")
        if isinstance(lit, list) and did in lit:
            c["litDistricts"] = [x for x in lit if x != did]
        gate_id = f"{did}_gate"
        if gate_id in c.get("roadNodes", {}):
            self._remove_road_gate(gate_id)
        c["districts"].pop(did, None)
        c.get("districtProjects", {}).pop(did, None)
        c.get("districtStocks", {}).pop(did, None)
        c.get("districtEcologyStage", {}).pop(did, None)
        c.get("districtWildlifeStage", {}).pop(did, None)
        c.get("districtLastContribution", {}).pop(did, None)
        return dropped

    def _revert_inland_founded_beaches(self):
        """On restore: drop inland-founded beach_N / orphan ocean_N back to frontier."""
        to_revert = self._inland_founded_districts_to_revert()
        if not to_revert:
            return
        reverted = []
        total_dropped = 0
        for did in to_revert:
            dropped = self._revert_founded_district(did)
            total_dropped += len(dropped)
            reverted.append(did)
        self._district_adjacency = None
        self._recompute_road_paths()
        _validate_districts(self.civilization["districts"])
        self._bump_districts_epoch()
        msg = (
            f"Migrated save: reverted inland-founded coast districts to frontier "
            f"({', '.join(reverted)}).")
        if total_dropped:
            msg += f" Dropped {total_dropped} structure(s) with no relocation room."
        self._push_activity(msg)

    def _districts_of_kind(self, kind):
        return [did for did, d in self.civilization["districts"].items() if d["kind"] == kind]

    def _resolve_target_district(self, target, agent=None):
        """Resolve a decision/movement 'target' to a concrete district id.
        Accepts either a specific district id, or (hedge for the prompt-tuning
        transition / any remaining kind-based call site) a kind name like
        "farm", in which case the nearest district of that kind to `agent`
        (or simply the first one, if no agent given) is used instead of
        failing outright."""
        c = self.civilization
        if not target:
            return None
        if target in c["districts"]:
            return target
        ids = self._districts_of_kind(target)
        if not ids:
            return None
        if agent is None or len(ids) == 1:
            return ids[0]
        best, best_d = ids[0], float("inf")
        for did in ids:
            b = c["districts"][did]["bounds"]
            cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
            dd = _dist(agent["x"], agent["y"], cx, cy)
            if dd < best_d:
                best_d, best = dd, did
        return best

    def _nearest_road_node(self, x, y):
        nodes = self.civilization["roadNodes"]
        best, best_d = None, float("inf")
        for nid, n in nodes.items():
            dd = _dist(x, y, n["x"], n["y"])
            if dd < best_d:
                best_d, best = dd, nid
        return best

    def _recompute_road_paths(self):
        """All-pairs shortest paths via BFS. Cheap at this graph's size (a
        dozen-ish nodes even after several foundings) -- recomputed on cold
        start and again after any district-founding graph change, not cached
        as a one-time module-load constant, since the graph itself isn't one."""
        nodes = self.civilization["roadNodes"]
        edges = self.civilization["roadEdges"]
        adj = {n: [] for n in nodes}
        for a, b in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        cache = {}
        for start in nodes:
            prev = {start: None}
            queue = deque([start])
            while queue:
                cur = queue.popleft()
                for nxt in adj.get(cur, []):
                    if nxt not in prev:
                        prev[nxt] = cur
                        queue.append(nxt)
            for end in prev:
                path = []
                node = end
                while node is not None:
                    path.append(node)
                    node = prev[node]
                path.reverse()
                cache[(start, end)] = path
        self.ROAD_PATH_CACHE = cache

    def _road_path_between(self, agent, dest_district_id):
        c = self.civilization
        dest_node = c["districts"][dest_district_id].get("entryNode")
        origin_district = agent.get("currentDistrict")
        origin_node = None
        if origin_district and origin_district in c["districts"]:
            origin_node = c["districts"][origin_district].get("entryNode")
        if not origin_node:
            origin_node = self._nearest_road_node(agent["x"], agent["y"])
        if not dest_node:
            dest_node = self._nearest_road_node(agent["x"], agent["y"])
        if not origin_node or not dest_node or origin_node == dest_node:
            return []
        return self.ROAD_PATH_CACHE.get((origin_node, dest_node)) or []

    def _road_path_between_districts(self, from_district_id, to_district_id):
        """Like _road_path_between, but resolved purely from two district
        ids (no agent needed) -- reuses the same ROAD_PATH_CACHE agent
        movement already populates via _recompute_road_paths. Used by the
        cosmetic shipment system (CARAVAN_VISUALS_ENABLED) to interpolate
        goods along the existing road graph. Returns [] (never a fabricated
        straight line) when either district or its entryNode is missing, or
        no cached path connects them."""
        c = self.civilization
        d_from = c["districts"].get(from_district_id)
        d_to = c["districts"].get(to_district_id)
        if not d_from or not d_to:
            return []
        origin_node = d_from.get("entryNode")
        dest_node = d_to.get("entryNode")
        if not origin_node or not dest_node or origin_node == dest_node:
            return []
        return self.ROAD_PATH_CACHE.get((origin_node, dest_node)) or []

    def _set_agent_target(self, agent, target):
        """Route the agent to a random interior point of the destination
        district. Travel goes via cached road-node paths
        (agent["waypoints"]) instead of a straight line -- this is general
        travel (idle wander, craft-station redirects, move_to_district);
        move_to_agent/trade/talk and Sage-emergency rescue use
        _set_agent_target_to_agent instead and always stay direct."""
        district_id = self._resolve_target_district(target, agent)
        if not district_id:
            return
        if self._quarantine_blocks_travel(agent, agent.get("currentDistrict"), district_id):
            return
        agent.pop("_quarantineTravelBypass", None)
        bounds = self.civilization["districts"][district_id]["bounds"]
        dest_x = bounds["x1"] + random.random() * (bounds["x2"] - bounds["x1"])
        dest_y = bounds["y1"] + random.random() * (bounds["y2"] - bounds["y1"])
        path_nodes = self._road_path_between(agent, district_id)
        waypoints = [dict(self.civilization["roadNodes"][n]) for n in path_nodes]
        waypoints.append({"x": dest_x, "y": dest_y})
        agent["waypoints"] = waypoints[1:]
        first = waypoints[0]
        agent["targetX"] = first["x"]
        agent["targetY"] = first["y"]

    def _set_agent_target_to_agent(self, agent, target_name, bypass_quarantine=False):
        target = self._find_agent(target_name)
        if not target:
            return
        if not bypass_quarantine and self._quarantine_blocks_travel(
                agent, agent.get("currentDistrict"), target.get("currentDistrict")):
            return
        if bypass_quarantine:
            agent["_quarantineTravelBypass"] = True
        else:
            agent.pop("_quarantineTravelBypass", None)
        agent["targetX"] = target["x"] + (random.random() - 0.5) * 60
        agent["targetY"] = target["y"] + (random.random() - 0.5) * 60
        agent["waypoints"] = []

    def _en_route_to(self, agent, district_id):
        """True while the agent's final travel destination already lies in
        the given district and they haven't arrived yet. Guards the callers
        that re-issue routing every goal step: without it each call re-rolls
        a new random destination point and replans the road path, which reads
        as agents jittering/circling around road hubs instead of walking."""
        d = self.civilization["districts"].get(district_id)
        if not d:
            return False
        wps = agent.get("waypoints") or []
        fx = wps[-1]["x"] if wps else agent.get("targetX")
        fy = wps[-1]["y"] if wps else agent.get("targetY")
        if fx is None or fy is None:
            return False
        b = d["bounds"]
        if not (b["x1"] <= fx <= b["x2"] and b["y1"] <= fy <= b["y2"]):
            return False
        return abs(agent["x"] - fx) + abs(agent["y"] - fy) > 1.0

    def _set_agent_target_once(self, agent, target):
        """_set_agent_target, but a no-op while already traveling there."""
        district_id = self._resolve_target_district(target, agent)
        if district_id and self._en_route_to(agent, district_id):
            return
        self._set_agent_target(agent, target)

    def _auto_move_toward_target(self, agent, target_name, bypass_quarantine=False):
        if not target_name or target_name not in self.agent_names:
            return
        other = self._find_agent(target_name)
        if not other:
            return
        if self._distance_to(agent, other) > 80:
            self._set_agent_target_to_agent(
                agent, target_name, bypass_quarantine=bypass_quarantine)

    def _move_agent(self, agent, scale=1.0):
        prior_x, prior_y = agent["x"], agent["y"]
        dx = agent["targetX"] - agent["x"]
        dy = agent["targetY"] - agent["y"]
        dist = math.sqrt(dx * dx + dy * dy)
        # Phase D: wagon holders travel faster (query-time vehicle effect).
        step = agent["speed"] * scale * self._vehicle_speed_mult(agent)
        if dist <= step:
            new_x, new_y = agent["targetX"], agent["targetY"]
            arrived = True
        else:
            new_x = agent["x"] + (dx / dist) * step
            new_y = agent["y"] + (dy / dist) * step
            arrived = False
        physical_prior_district = (
            get_district(self.civilization["districts"], prior_x, prior_y)
            or agent.get("currentDistrict")
        )
        next_district = get_district(
            self.civilization["districts"], new_x, new_y)
        quarantine_blocks = (
            not agent.get("_quarantineTravelBypass")
            and physical_prior_district != next_district
            and self._quarantine_blocks_travel(
                agent, physical_prior_district, next_district)
        )
        if quarantine_blocks:
            # A rule may be enacted after this route was assigned. Cancel at
            # the boundary without snapping the agent to another position.
            agent["targetX"], agent["targetY"] = prior_x, prior_y
            agent["waypoints"] = []
            agent["idleFrames"] = 0
        elif self._architect_door_blocks_move(agent, prior_x, prior_y, new_x, new_y):
            agent["idleFrames"] = 0
        else:
            agent["x"], agent["y"] = new_x, new_y
            if arrived:
                agent.pop("_quarantineTravelBypass", None)
                waypoints = agent.get("waypoints") or []
                if waypoints:
                    nxt = waypoints.pop(0)
                    agent["targetX"] = nxt["x"]
                    agent["targetY"] = nxt["y"]
                    agent["idleFrames"] = 0
                elif DAILY_COUNCIL_ENABLED and agent.get("councilTurn"):
                    # A seated councillor remains at the persisted seat instead of
                    # triggering the ordinary 60-frame idle-wander behavior.
                    agent["idleFrames"] = 0
                else:
                    agent["idleFrames"] = agent.get("idleFrames", 0) + 1
                    if agent["idleFrames"] >= 60:
                        cur = agent.get("currentDistrict")
                        if cur:
                            wander = cur
                        else:
                            wander = random.choice(list(self.civilization["districts"].keys()))
                        self._set_agent_target(agent, wander)
                        agent["idleCycles"] = agent.get("idleCycles", 0) + 1
                        agent["idleFrames"] = 0
            else:
                agent["idleFrames"] = 0
        prior_district = agent.get("currentDistrict")
        agent["currentZone"] = get_zone(self.civilization["districts"], agent["x"], agent["y"])
        agent["currentDistrict"] = get_district(self.civilization["districts"], agent["x"], agent["y"])
        if agent.get("currentDistrict") != prior_district:
            self._mark_context_dirty(agent)
        if agent["x"] != prior_x or agent["y"] != prior_y:
            self._mark_agent_dirty(agent)

    # --- survival ---
    def _first_edible(self, agent):
        for rid in EDIBLE_RESOURCES:
            if agent["resources"].get(rid, 0) > 0:
                return rid
        return None

    def _share_edible_with(self, agent):
        """Deterministic anti-hoarding backstop: a starving agent with nothing
        to eat receives one edible from a nearby villager holding surplus
        (above EDIBLE_RESERVE). Without this the only food-transfer paths are
        heal-donation and voluntary LLM trades, so one well-fed fisher can sit
        on a full stack while neighbours starve."""
        for donor in self.agents:
            if donor is agent or donor["incapacitated"]:
                continue
            if self._distance_to(agent, donor) > SHARE_RADIUS:
                continue
            for rid in EDIBLE_RESOURCES:
                if donor["resources"].get(rid, 0) > EDIBLE_RESERVE:
                    donor["resources"][rid] -= 1
                    agent["hunger"] = min(100, agent["hunger"] + FOOD_RESTORE)
                    self._push_activity(f"{donor['name']} shared {rid} with {agent['name']}")
                    return True
        return False

    def _update_survival(self, agent):
        if not SURVIVAL_ENABLED:
            return
        if LIFECYCLE_ENABLED and agent.get("deathFrame") is not None:
            # Phase F: death is permanent, unlike a survival collapse. Without
            # this guard the COLLAPSE_REGEN/COLLAPSE_REVIVE_HEALTH path below
            # (designed for a temporarily incapacitated agent) would
            # eventually heal a corpse back past the revive threshold and
            # resurrect it -- "{name} recovered" on someone already dead,
            # who then resumes moving/thinking/voting with a stale role.
            return
        old_hunger, old_health = agent["hunger"], agent["health"]
        edible = self._first_edible(agent) if agent["hunger"] < EAT_THRESHOLD else None
        if edible:
            agent["resources"][edible] -= 1
            agent["hunger"] = min(100, agent["hunger"] + FOOD_RESTORE)
            self._push_activity(f"{agent['name']} ate {edible}")
        if not edible and agent["hunger"] <= 0:
            self._share_edible_with(agent)
        # Sovereign God mode Phase 5: hunger drain is scaled BEFORE the
        # max(0, ...) clamp -- 0.0 suppresses drain entirely without
        # touching any other survival effect.
        agent["hunger"] = max(0, agent["hunger"] - HUNGER_RATE * self._divine_modifier("hunger_drain_multiplier"))
        if agent["incapacitated"]:
            # COLLAPSE_REGEN is DELIBERATELY excluded from divine scaling
            # (docs/archive/plan-sovereign-god-mode-v2.md "Collapse recovery -- deliberately excluded"):
            # health_regen_multiplier must never reach this line, or a 0.0
            # value would permanently strand an incapacitated agent below
            # COLLAPSE_REVIVE_HEALTH with no deterministic escape.
            agent["health"] = min(100, agent["health"] + COLLAPSE_REGEN)
            if agent["health"] >= COLLAPSE_REVIVE_HEALTH:
                agent["incapacitated"] = False
                agent["hunger"] = max(agent["hunger"], REVIVE_HUNGER)
                self._push_activity(f"{agent['name']} recovered")
        else:
            if agent["hunger"] <= 0:
                # Starvation damage scaled BEFORE the max(0, ...) clamp.
                agent["health"] = max(0, agent["health"]
                                      - HEALTH_RATE * self._divine_modifier("starvation_damage_multiplier"))
            else:
                # Fed regen scaled BEFORE the min(100, ...) clamp. This is
                # the ONLY health_regen_multiplier consumer site.
                agent["health"] = min(100, agent["health"]
                                      + HEALTH_REGEN * self._divine_modifier("health_regen_multiplier"))
            if agent["health"] <= 0:
                agent["incapacitated"] = True
                agent["goal"] = None
                self._push_activity(f"{agent['name']} collapsed from starvation")
                if CULTURE_ENABLED:
                    self._drift_personality(agent, "wary of hunger since a collapse")
        thresholds = (EAT_THRESHOLD, 0)
        if any((old_hunger > t) != (agent["hunger"] > t) for t in thresholds) \
                or any((old_health > t) != (agent["health"] > t) for t in (60, SAGE_CRITICAL_HEALTH, 0)):
            self._mark_context_dirty(agent)
        if old_hunger != agent["hunger"] or old_health != agent["health"]:
            self._mark_agent_dirty(agent)

    def _neediest_nearby(self, agent):
        nearby = [self._find_agent(n) for n in self._get_nearby_agents(agent)]
        nearby = [a for a in nearby if a and a.get("deathFrame") is None
                  and (a["incapacitated"] or a["health"] < 60)]
        if not nearby:
            return None
        nearby.sort(key=lambda a: (0 if a["incapacitated"] else 1, a["health"]))
        return nearby[0]

    # --- Sage emergency ---
    def _sage_emergency(self):
        if not SURVIVAL_ENABLED:
            return None
        # Phase F: the elder is mortal. A dead elder is permanently
        # incapacitated (no revival path applies post-mortem), so without this
        # guard a deceased Sage would look like a standing emergency forever
        # -- responders would rush to a corpse instead of working, and no
        # amount of healing ever clears it. Once dead, there is no emergency
        # to respond to; _agent_dies has already started succession, which is
        # the correct next step, not a rescue.
        sage = next((a for a in self.agents
                    if a["role"] == "elder" and a.get("deathFrame") is None), None)
        if not sage:
            return None
        if not sage["incapacitated"] and sage["health"] >= SAGE_CRITICAL_HEALTH:
            return None
        healer = next((a for a in self.agents if a["role"] == "healer" and a.get("deathFrame") is None), None)
        return healer if (healer and healer["incapacitated"]) else sage

    def _sage_responders(self, target):
        responders = set()
        healer = next((a for a in self.agents if a["role"] == "healer"), None)
        if healer and not healer["incapacitated"] and healer is not target:
            responders.add(healer["name"])
        nearest, nearest_d = None, float("inf")
        for a in self.agents:
            if a is target or a["incapacitated"] or a["name"] in responders:
                continue
            dd = self._distance_to(a, target)
            if dd < nearest_d:
                nearest_d, nearest = dd, a
        if nearest:
            responders.add(nearest["name"])
        return responders

    def _rush_to_heal(self, agent, target):
        """Sage-priority emergency heal — bypasses decision gates (survival > story)."""
        agent["goal"] = None
        if self._distance_to(agent, target) > 80:
            self._auto_move_toward_target(agent, target["name"], bypass_quarantine=True)
            self._push_activity(f"{agent['name']} rushes to save {target['name']}")
            return
        self.apply_decision(agent, {"action": "heal_agent", "target": target["name"],
                                    "message": None, "reasoning": "Sage-priority emergency."})

    # --- project helpers (concurrent per-district builds) ---
    def _touch_kind_activity(self, kind):
        self.civilization["kindLastActivityFrame"][kind] = self.frameTick

    def _active_project_districts(self):
        return [did for did, p in self.civilization["districtProjects"].items() if p]

    def _buildable_district_ids(self):
        return [did for did, d in self.civilization["districts"].items() if d.get("build_grid")]

    def _resolve_contribution_district(self, agent, target_district=None):
        """Which district a contribute/collect/build decision should act on:
        an explicit target_district with an active project, else the agent's
        own district if it has one, else the most-stalled active district
        village-wide (mirrors the old single-project "the project" default,
        generalized to pick fairly across concurrent builds)."""
        c = self.civilization
        if target_district and c["districtProjects"].get(target_district):
            return target_district
        cur = agent.get("currentDistrict")
        if cur and c["districtProjects"].get(cur):
            return cur
        actives = self._active_project_districts()
        if not actives:
            return None
        actives.sort(key=lambda did: c["districtLastContribution"].get(did, 0))
        return actives[0]

    def _resolve_build_district(self, agent, type_, target_district=None):
        """Which district a new project of `type_` should start in: an
        explicit target_district (if it's buildable and idle), else the
        agent's current district (if its kind matches and it's idle and
        under cap), else the nearest matching-kind buildable district with
        room, else the nearest matching-kind buildable district at all."""
        c = self.civilization
        kind = PROJECT_KIND.get(type_, "village")

        def usable(did):
            d = c["districts"].get(did)
            # Note: this count-vs-cap check is an optimistic pre-filter -- it
            # can overestimate room now that footprint-aware placement lets
            # large (upgraded) structures shadow multiple grid slots.
            # _find_structure_spot returning None is the authoritative gate;
            # _maybe_relocate_stuck_project handles the case where a project
            # starts here anyway and then can't actually complete.
            return bool(d and d.get("build_grid") and d["kind"] == kind
                        and not c["districtProjects"].get(did)
                        and self._district_structure_count(did) < d["build_grid"]["cap"])

        if target_district and usable(target_district):
            return target_district
        cur = agent.get("currentDistrict")
        if cur and usable(cur):
            return cur
        # Only districts with room: a project started in a full district can
        # never build and squats on a concurrent-project slot forever. When
        # every district of this kind is full, returning None is correct --
        # _maybe_found_district exists to open up new land in that case.
        with_room = [did for did in self._buildable_district_ids() if usable(did)]
        if not with_room:
            return None
        return min(with_room, key=lambda did: self._distance_to_district(agent, did))

    def _distance_to_district(self, agent, district_id):
        b = self.civilization["districts"][district_id]["bounds"]
        cx, cy = (b["x1"] + b["x2"]) / 2, (b["y1"] + b["y2"]) / 2
        return _dist(agent["x"], agent["y"], cx, cy)

    def _project_progress_text(self, district_id):
        p = self.civilization["districtProjects"].get(district_id)
        if not p:
            return "none"
        parts = [f"{res} {p['contributed'].get(res, 0)}/{need}" for res, need in p["needs"].items()]
        return ", ".join(parts)

    def _active_projects_brief(self):
        actives = self._active_project_districts()
        if not actives:
            return "none"
        c = self.civilization
        def _brief(did):
            p = c["districtProjects"][did]
            lead = p.get("lead")
            return f"{p['name']} in {did}" + (f" (lead: {lead})" if lead else "")
        return "; ".join(_brief(did) for did in actives)

    def _active_projects_progress_text(self):
        actives = self._active_project_districts()
        if not actives:
            return "none"
        return "; ".join(f"{did}: {self._project_progress_text(did)}" for did in actives)

    def _first_unmet_project_resource(self, district_id):
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return None
        for res in p["needs"]:
            if p["contributed"].get(res, 0) < p["needs"].get(res, 0):
                return res
        return None

    def _first_unmet_resource_anywhere(self):
        """First unmet resource across ANY active district project -- used by
        the emergent-role gap-filling logic, which cares about "is anything
        stalled village-wide" rather than one specific district."""
        for did in self._active_project_districts():
            res = self._first_unmet_project_resource(did)
            if res:
                return res
        return None

    def _gather_zone_for_resource(self, rid):
        d = self.civilization["resourceRegistry"].get(rid)
        return d.get("gatherZone") if d else None

    def _resource_is_obtainable(self, rid):
        if self._gather_zone_for_resource(rid):
            return True
        c = self.civilization
        if rid in self.RECIPES or any(p["id"] == rid for p in c.get("pendingRecipes") or []):
            return True
        type_ids = set(c.get("projectRegistry") or {})
        type_ids.update(s.get("type") for s in c.get("structures") or [] if s.get("type"))
        for type_id in type_ids:
            if self._resource_in_function(rid, self._structure_function_for_type(type_id)):
                return True
        # The one exception to "producers must be declarative": coin is minted
        # deterministically while a working Mint exists (see _maybe_mint_coin).
        if ECONOMY_ENABLED and rid == "coin" and self._mint_active():
            return True
        return False

    def _village_holdings(self, rid):
        """Stockpile plus every agent inventory; excludes districtStocks (in-ground
        deposits, not village stores — those feed _format_district_stocks_for_prompt)."""
        c = self.civilization
        total = (c.get("stockpile") or {}).get(rid, 0)
        total += sum(a["resources"].get(rid, 0) for a in self.agents)
        return total

    def _get_zone_resources(self, zone):
        return [rid for rid, d in self.civilization["resourceRegistry"].items()
                if d.get("gatherZone") == zone]

    # --- resource ecology (Phase B; gated by ECOLOGY_ENABLED) ---
    def _stock_max(self, resource_id):
        return STOCK_DEFAULT_MAX

    def _resources_for_district_kind(self, kind, resource_registry=None):
        reg = resource_registry or self.civilization["resourceRegistry"]
        return [rid for rid, d in reg.items()
                if d.get("gatherZone") == kind and not d.get("crafted")]

    def _init_district_stocks(self, districts, resource_registry=None):
        stocks = {}
        for did, d in districts.items():
            kind = d.get("kind")
            if not kind:
                continue
            res_ids = self._resources_for_district_kind(kind, resource_registry)
            if res_ids:
                stocks[did] = {rid: self._stock_max(rid) for rid in res_ids}
        return stocks

    def _ensure_district_stocks(self):
        c = self.civilization
        if c.get("districtStocks"):
            return
        c["districtStocks"] = self._init_district_stocks(c["districts"])

    def _district_stock(self, district_id, resource_id):
        return c_stocks.get(resource_id) if (c_stocks := self.civilization["districtStocks"].get(district_id)) else None

    def _set_district_stock(self, district_id, resource_id, value):
        c = self.civilization
        max_s = self._stock_max(resource_id)
        c.setdefault("districtStocks", {}).setdefault(district_id, {})[resource_id] = \
            min(max_s, max(0, value))

    def _add_district_stock(self, district_id, resource_id, amount):
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return
        self._set_district_stock(district_id, resource_id, current + amount)

    def _deplete_district_stock(self, district_id, resource_id, amount):
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return
        new_val = max(0, current - amount)
        self._set_district_stock(district_id, resource_id, new_val)
        if current > 0 and new_val <= 0:
            kind = self.civilization["districts"][district_id]["kind"]
            self._push_activity(
                f"The {kind} in {district_id} is depleted of {resource_id} — gathering fails here until it regrows")

    def _ecology_gather_gate(self, agent, resource_id):
        """Returns (allowed, reason, yield_scale). Non-tracked resources pass through."""
        if not ECOLOGY_ENABLED:
            return True, None, 1.0
        district_id = agent.get("currentDistrict")
        if not district_id:
            return True, None, 1.0
        current = self._district_stock(district_id, resource_id)
        if current is None:
            return True, None, 1.0
        max_s = self._stock_max(resource_id)
        if current <= 0:
            kind = self.civilization["districts"][district_id]["kind"]
            reason = f"the {kind} here is depleted of {resource_id}"
            return False, reason, 0.0
        ratio = min(1.0, current / max_s)
        scale = max(STOCK_MIN_YIELD_RATIO, ratio)
        return True, None, scale

    def _format_district_stocks_for_prompt(self, agent):
        if not ECOLOGY_ENABLED:
            return "none"
        self._ensure_district_stocks()
        did = agent.get("currentDistrict")
        if not did:
            return "none"
        stocks = self.civilization["districtStocks"].get(did) or {}
        if not stocks:
            return "none"
        parts = []
        for rid, val in sorted(stocks.items()):
            max_s = self._stock_max(rid)
            if val <= 0:
                parts.append(f"{rid}:depleted")
            elif val < max_s * STOCK_LOW_RATIO:
                parts.append(f"{rid}:low")
            elif val < max_s * 0.5:
                parts.append(f"{rid}:fair")
            else:
                parts.append(f"{rid}:ok")
        return ", ".join(parts)

    def _structure_distribution_by_district(self):
        """Per-district structure type counts, computed fresh from
        civilization["structures"] -- no caching needed at this scale. Used to
        give the sage review a sense of what's already built where."""
        counts = {}
        for s in self.civilization["structures"]:
            did = s.get("districtId")
            if not did or s.get("isRuin"):
                continue
            counts.setdefault(did, {}).setdefault(s.get("type"), 0)
            counts[did][s.get("type")] += 1
        return counts

    def _sage_review_geo_context(self):
        """Compact village-wide geography/resource summary for the sage
        review nudge: per buildable district, stock levels and what's already
        standing there."""
        c = self.civilization
        distribution = self._structure_distribution_by_district()
        parts = []
        for did, d in c["districts"].items():
            if not d.get("build_grid"):
                continue
            stocks = c.get("districtStocks", {}).get(did) or {} if ECOLOGY_ENABLED else {}
            low = [rid for rid, val in stocks.items() if val <= 0 or val < self._stock_max(rid) * STOCK_LOW_RATIO]
            built = distribution.get(did) or {}
            built_str = ", ".join(f"{t}x{n}" for t, n in sorted(built.items())) or "nothing built"
            shortage_str = f"short on {', '.join(sorted(low))}" if low else "stocks fine"
            parts.append(f"{did} ({d.get('kind')}): {built_str}; {shortage_str}")
        return "; ".join(parts) if parts else "no district data"

    def _tick_ecology_regrow(self):
        if not ECOLOGY_ENABLED:
            return
        self._ensure_district_stocks()
        c = self.civilization
        regrow = STOCK_REGROW_PER_TICK
        if GOODS_ENABLED:
            # Phase C seasons: spring regrows double, winter not at all --
            # the loop-closer with storage/spoilage (stockpile before winter).
            regrow *= SEASON_REGROW_MULT.get(self._current_season(), 1)
            if regrow <= 0:
                return
        # Living-ecosystem Phase 5 (WEATHER_GOVERNANCE_ENABLED): extend this
        # SAME multiplier chain -- never a second parallel mechanism -- with
        # a per-district weather term. Only districts civilization["weather"]
        # names are affected, and only while weather is "storm" (suppress)
        # or "clearing" (partial rain-boost recovery right after). Off, or
        # weather clear/gathering, or a district not currently named: the
        # multiplier is exactly 1 and this is byte-identical to Phase 4.
        weather_state = None
        weather_districts = ()
        if WEATHER_GOVERNANCE_ENABLED and WEATHER_ENABLED:
            w = c.get("weather") or {}
            weather_state = w.get("state")
            weather_districts = w.get("districts") or ()
        for did, stocks in c["districtStocks"].items():
            kind = c["districts"].get(did, {}).get("kind", "land")
            district_regrow = regrow
            if weather_state and did in weather_districts:
                # Fractional, never floored to 0: WEATHER_DWELL_TICKS already
                # bounds how long "storm"/"clearing" lasts (a few minutes),
                # so a nonzero multiplier is enough to guarantee a district
                # always keeps inching toward recovery even mid-storm --
                # the starvation-spiral floor the plan calls for, without a
                # second bespoke duration/cap mechanism.
                if weather_state == "storm":
                    district_regrow = regrow * WEATHER_STORM_REGROW_MULT
                elif weather_state == "clearing":
                    district_regrow = regrow * WEATHER_CLEARING_REGROW_MULT
            for rid, val in list(stocks.items()):
                max_s = self._stock_max(rid)
                if val >= max_s:
                    continue
                new_val = min(max_s, val + district_regrow)
                if new_val == val:
                    continue
                stocks[rid] = new_val
                if val <= 0 < new_val:
                    self._push_activity(
                        f"The {kind} in {did} is recovering — {rid} stock is growing again")
                elif val < max_s * STOCK_LOW_RATIO <= new_val:
                    self._push_activity(f"{rid} stock in {did} has regrown to fair levels")

    def _district_ecology_ratio(self, district_id):
        """Average stock ratio (0..1) across a district's gatherable
        resources -- the same ratio _resource_price/_ecology_scarcity_index
        already compute, just scoped to one district. None for districts with
        no ecology stocks (market/workshop/cemetery/ocean kinds have no
        gatherable resource; village kinds DO have one -- "water")."""
        stocks = self.civilization.get("districtStocks", {}).get(district_id)
        if not stocks:
            return None
        total, count = 0.0, 0
        for rid, val in stocks.items():
            max_s = self._stock_max(rid)
            total += min(1.0, val / max_s) if max_s else 0.0
            count += 1
        return total / count if count else None

    @staticmethod
    def _district_ecology_stage_raw(ratio):
        """Stage index with no hysteresis, mirroring the depleted/low/fair/ok
        boundaries _format_district_stocks_for_prompt already narrates."""
        if ratio <= 0:
            return 0   # barren
        if ratio < STOCK_LOW_RATIO:
            return 1   # sparse
        if ratio < 0.5:
            return 2   # healthy
        return 3       # lush

    @staticmethod
    def _district_ecology_stage_with_hysteresis(ratio, prev_idx):
        """Applies DISTRICT_ECOLOGY_HYSTERESIS so a ratio hovering on a
        boundary can't flip the stage (and the terrain-cache rebuild it
        triggers) every /state poll. Moves at most one stage per call --
        harmless for a decorative projection; a large jump just settles over
        a couple of polls instead of one."""
        raw = SimEngine._district_ecology_stage_raw(ratio)
        if prev_idx is None:
            return raw
        if raw > prev_idx:
            boundary = DISTRICT_ECOLOGY_THRESHOLDS[prev_idx]
            if ratio > boundary + DISTRICT_ECOLOGY_HYSTERESIS:
                return prev_idx + 1
            return prev_idx
        if raw < prev_idx:
            target_boundary = DISTRICT_ECOLOGY_THRESHOLDS[prev_idx - 1]
            if target_boundary <= 0:
                # Barren's floor is ratio<=0 itself (ratio can never go
                # negative), so there is nothing to subtract a margin from --
                # require the exact raw condition instead.
                if ratio <= 0:
                    return prev_idx - 1
                return prev_idx
            if ratio < target_boundary - DISTRICT_ECOLOGY_HYSTERESIS:
                return prev_idx - 1
            return prev_idx
        return prev_idx

    def _district_ecology_snapshot(self):
        """Read-only /state projection of per-district ecology health, e.g.
        {"districtId": "forest", "stage": "lush", "ratio": 0.87}. Derived
        server-side from districtStocks (never exposed directly) so
        thresholds stay authoritative -- same pattern as conditionTier/
        socialTies/chronicle. Stage carries hysteresis state in
        civilization["districtEcologyStage"] across polls."""
        if not ECOLOGY_ENABLED:
            return []
        self._ensure_district_stocks()
        stage_state = self.civilization.setdefault("districtEcologyStage", {})
        out = []
        for did in self.civilization["districts"]:
            ratio = self._district_ecology_ratio(did)
            if ratio is None:
                continue
            idx = self._district_ecology_stage_with_hysteresis(ratio, stage_state.get(did))
            stage_state[did] = idx
            out.append({"districtId": did, "stage": DISTRICT_ECOLOGY_STAGES[idx], "ratio": round(ratio, 3)})
        return out

    def _ecology_scarcity_index(self):
        if not ECOLOGY_ENABLED:
            return None
        self._ensure_district_stocks()
        total, count = 0.0, 0
        for stocks in self.civilization["districtStocks"].values():
            for rid, val in stocks.items():
                max_s = self._stock_max(rid)
                total += min(1.0, val / max_s if max_s else 0)
                count += 1
        return round(total / count, 3) if count else 1.0

    def _is_project_type_deferred(self, type_):
        """Returns (deferred, frames_remaining). Clears expired deferrals."""
        c = self.civilization
        until = c.get("deferredProjectTypes", {}).get(type_)
        if not until:
            return False, 0
        if self.frameTick >= until:
            c["deferredProjectTypes"].pop(type_, None)
            c.get("projectAbandonStreak", {}).pop(type_, None)
            return False, 0
        return True, until - self.frameTick

    def _unbuilt_customs_blocking_invention(self):
        c = self.civilization
        return any(pid not in c["builtTypes"]
                   and not self._is_project_type_deferred(pid)[0]
                   and not self._type_tier_locked(pid)[0]
                   for pid in self._custom_project_ids())

    def _seed_project_from_stockpile(self, district_id, project, agent=None):
        """Transfer matching stockpile materials into a newly started project.
        Phase F (I4): a rationing rule caps how much of an EDIBLE resource
        this can pull out per call while village storage is low -- the
        deterministic "stockpile withdrawal" the plan's rationing kind
        governs. Non-edible materials (wood/stone/etc.) are never rationed."""
        c = self.civilization
        needs = project.get("needs") or {}
        contributed = project.setdefault("contributed", {})
        seeds = []
        capped_note = None
        for res, need in needs.items():
            short = need - contributed.get(res, 0)
            if short <= 0:
                continue
            available = int(c["stockpile"].get(res, 0))
            if available <= 0:
                continue
            take = min(short, available)
            if LIFECYCLE_ENABLED and res in EDIBLE_RESOURCES:
                take, reason = self._rationing_gate(agent, res, take)
                if reason and take < min(short, available):
                    capped_note = reason
            if take <= 0:
                continue
            c["stockpile"][res] = available - take
            contributed[res] = contributed.get(res, 0) + take
            seeds.append((take, res))
        if seeds:
            parts = ", ".join(f"{amt} {res}" for amt, res in seeds)
            self._push_activity(
                f"The village stockpile supplied {parts} toward the {project['name']}")
        if capped_note and agent is not None:
            agent["lastRationingRejection"] = {"reason": capped_note, "frame": self.frameTick}
        return bool(seeds)

    def _largest_missing_input(self, agent, inputs):
        best = None
        best_short = 0
        for res, need in inputs.items():
            short = need - agent["resources"].get(res, 0)
            if short > best_short:
                best_short = short
                best = res
        return best

    def _craft_input_reflex(self, agent, recipe_id, recipe):
        """On missing craft inputs: gather the largest deficit deterministically."""
        missing = self._largest_missing_input(agent, recipe["inputs"])
        if not missing:
            return
        reason = f"lacks {missing} to craft {recipe_id}"
        agent["lastCraftRejection"] = {"reason": reason, "frame": self.frameTick, "resource": missing}
        allowed, _, _ = self._ecology_gather_gate(agent, missing)
        if not allowed and ECOLOGY_ENABLED:
            self._scarcity_reflex_on_depletion(agent, missing)
        elif USE_GOALS:
            agent["goal"] = {
                "kind": "craft_gather", "target": missing, "recipe": recipe_id, "ttl": 10,
            }
        else:
            gz = self._gather_zone_for_resource(missing)
            if gz and agent["currentZone"] != gz:
                self._set_agent_target(agent, gz)
        self._push_activity(
            f"{agent['name']} craft reflex: gathering {missing} for {recipe_id}")

    def _step_craft_gather_goal(self, agent, g):
        resource = g.get("target")
        if not resource:
            agent["goal"] = None
            return False
        if agent["resources"].get(resource, 0) >= self._carry_cap(agent):
            agent["goal"] = None
            return False
        gz = self._gather_zone_for_resource(resource)
        if gz and agent["currentZone"] != gz:
            self._set_agent_target(agent, gz)
            return True
        allowed, _, _ = self._ecology_gather_gate(agent, resource)
        if not allowed:
            self._scarcity_reflex_on_depletion(agent, resource)
            return True
        summary = self._perform_gather(agent, resource)
        if "found nothing" in summary:
            return True
        return True

    def _get_terraform_function(self, terraform_id):
        return TERRAFORM_FUNCTIONS.get(terraform_id) or {}

    def _stalled_approved_customs(self):
        c = self.civilization
        frames = c.get("approvedCustomApprovedFrame") or {}
        out = []
        for pid in self._custom_project_ids():
            if pid in c["builtTypes"]:
                continue
            if self._is_project_type_deferred(pid)[0]:
                continue
            if self._type_tier_locked(pid)[0]:
                continue
            if any(p and p.get("type") == pid for p in c["districtProjects"].values()):
                continue
            approved_at = frames.get(pid, c.get("lastBlueprintActivityFrame", 0))
            if self.frameTick - approved_at < APPROVED_CUSTOM_STALL_FRAMES:
                continue
            name = c["projectRegistry"][pid].get("name", pid)
            out.append((pid, name, approved_at))
        out.sort(key=lambda x: x[2])
        return out

    def _terraform_template_for_kind(self, kind):
        return KIND_TERRAFORM.get(kind)

    def _active_terraform_for_kind(self, kind):
        template = self._terraform_template_for_kind(kind)
        if not template:
            return None
        for did, p in self.civilization["districtProjects"].items():
            if p and p.get("isTerraform") and p.get("type") == template:
                return did
        return None

    def _district_highest_stock(self, resource_id):
        if not ECOLOGY_ENABLED:
            return None
        self._ensure_district_stocks()
        best_did = None
        best_val = -1
        for did, stocks in self.civilization["districtStocks"].items():
            val = stocks.get(resource_id)
            if val is not None and val > best_val:
                best_val = val
                best_did = did
        return best_did if best_val > 0 else None

    def _scarcity_reflex_migrate(self, agent, resource):
        dest = self._district_highest_stock(resource)
        if not dest:
            gz = self._gather_zone_for_resource(resource)
            if gz:
                dest = next((did for did, d in self.civilization["districts"].items()
                             if d.get("kind") == gz), None)
        if dest and dest != agent.get("currentDistrict"):
            self.apply_decision(agent, {
                "action": "move_to_district", "target": dest,
                "reasoning": f"scarcity reflex: seeking {resource}",
            })
            self._push_activity(
                f"{agent['name']} scarcity reflex: routed to {dest} for depleted {resource}")

    def _scarcity_reflex_on_depletion(self, agent, resource):
        """Deterministic response to ecology depletion (no LLM). Terraform
        contribute/start first; migrate to best-stocked district last."""
        if not ECOLOGY_ENABLED:
            return
        c = self.civilization
        did = agent.get("currentDistrict")
        if not did or did not in c.get("districts", {}):
            self._scarcity_reflex_migrate(agent, resource)
            return
        kind = c["districts"][did].get("kind")
        terraform_did = self._active_terraform_for_kind(kind)
        if terraform_did:
            p = c["districtProjects"][terraform_did]
            unmet = self._first_unmet_project_resource(terraform_did)
            if USE_GOALS:
                agent["goal"] = {
                    "kind": "gather" if unmet else "deliver",
                    "target": unmet,
                    "district": terraform_did,
                    "ttl": 10,
                }
            self._push_activity(
                f"{agent['name']} scarcity reflex: contributing to {p['name']} in {terraform_did}")
            return
        template = self._terraform_template_for_kind(kind)
        if template and not c["districtProjects"].get(did):
            summary = self._start_terraform_for(agent, template, did)
            if summary:
                self._push_activity(f"{agent['name']} scarcity reflex: {summary}")
                return
        self._scarcity_reflex_migrate(agent, resource)

    def _start_terraform_for(self, agent, target, target_district=None):
        c = self.civilization
        tmpl = TERRAFORM_TEMPLATES.get(target) if target else None
        if not tmpl:
            return None
        if len(self._active_project_districts()) >= MAX_CONCURRENT_PROJECTS:
            return None
        kind = tmpl["kind"]
        district_id = None
        if target_district and c["districts"].get(target_district, {}).get("kind") == kind \
                and not c["districtProjects"].get(target_district):
            district_id = target_district
        if not district_id:
            cur = agent.get("currentDistrict")
            if cur and c["districts"].get(cur, {}).get("kind") == kind \
                    and not c["districtProjects"].get(cur):
                district_id = cur
        if not district_id:
            candidates = [did for did, d in c["districts"].items()
                          if d.get("kind") == kind and not c["districtProjects"].get(did)]
            if candidates:
                district_id = min(candidates, key=lambda did: self._distance_to_district(agent, did))
        if not district_id:
            return None
        c["districtProjects"][district_id] = {
            "type": target, "name": tmpl["name"], "needs": dict(tmpl["needs"]),
            "contributed": {res: 0 for res in tmpl["needs"]},
            "districtId": district_id, "isTerraform": True,
        }
        self._seed_project_from_stockpile(district_id, c["districtProjects"][district_id], agent=agent)
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        return f"{agent['name']} started {tmpl['name']} terraform in {district_id}"

    def _apply_terraform_modifiers(self, district_id, function):
        c = self.civilization
        self._ensure_district_stocks()
        for mod in function.get("modifies") or []:
            if mod.get("target") != "stock":
                continue
            scope_did = district_id if mod.get("scope", "district") == "district" else district_id
            for rid in mod.get("resources") or []:
                max_s = self._stock_max(rid)
                if mod.get("set_ratio") is not None:
                    self._set_district_stock(scope_did, rid, int(max_s * mod["set_ratio"]))
                elif mod.get("add"):
                    self._add_district_stock(scope_did, rid, mod["add"])
        if function.get("found_coastal_pair"):
            self._try_found_coastal_pair()

    def _complete_terraform(self, agent, district_id):
        c = self.civilization
        project = c["districtProjects"].get(district_id)
        if not project or not project.get("isTerraform"):
            return f"{agent['name']} has nothing to terraform"
        tid = project["type"]
        tmpl = TERRAFORM_TEMPLATES.get(tid) or {}
        fn = tmpl.get("function") or self._get_terraform_function(tid)
        self._apply_terraform_modifiers(district_id, fn)
        name = project["name"]
        c["districtProjects"][district_id] = None
        c["completedProjects"] += 1
        agent["lastContributedFrame"] = self.frameTick
        c["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(c["districts"][district_id]["kind"])
        self._check_civilization_level()
        self._push_activity(f"{agent['name']} completed {name} — the land in {district_id} has changed")
        return f"{agent['name']} completed {name} in {district_id}"

    def _try_contribute_resource(self, agent, res, district_id=None):
        district_id = district_id or self._resolve_contribution_district(agent)
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p or not res:
            return None
        need = p["needs"].get(res, 0)
        have = p["contributed"].get(res, 0)
        if have >= need or agent["resources"].get(res, 0) <= 0:
            return None
        # Phase G: a practiced builder contributes a bit more efficiently per
        # action -- the "build" skill's mechanical payoff, capped by what the
        # project still needs and what the agent actually holds (never over-
        # contributes past the requirement or below zero resources).
        amount = 1
        if CULTURE_ENABLED:
            amount += self._skill_bonus(agent, "build")
        amount += self._custom_rule_modifier("contribute_resources", agent, res, district_id)
        amount = max(1, min(amount, need - have, agent["resources"].get(res, 0)))
        agent["resources"][res] -= amount
        p["contributed"][res] = have + amount
        agent["lastContributedFrame"] = self.frameTick
        self.civilization["districtLastContribution"][district_id] = self.frameTick
        self._touch_kind_activity(self.civilization["districts"][district_id]["kind"])
        self._enforce_resource_tax(agent, res)
        if CULTURE_ENABLED:
            self._practice_skill(agent, "build")
        self._emit_shipment(agent.get("currentDistrict"), district_id, res)
        bonus_note = " (skilled builder)" if amount > 1 else ""
        return f"{agent['name']} contributed {res} x{amount}{bonus_note} to {p['name']} ({district_id})" \
            if amount > 1 else f"{agent['name']} contributed {res} to {p['name']} ({district_id})"

    def _is_project_complete(self, district_id):
        p = self.civilization["districtProjects"].get(district_id) if district_id else None
        if not p:
            return False
        return all(p["contributed"].get(res, 0) >= need for res, need in p["needs"].items())

    def _district_structure_count(self, district_id):
        return sum(1 for s in self.civilization["structures"] if s.get("districtId") == district_id)

    def _structure_count(self, type_, district_id=None):
        return sum(1 for s in self.civilization["structures"]
                   if s.get("type") == type_
                   and (district_id is None or s.get("districtId") == district_id))

    def _structure_type_built(self, type_):
        """True once at least one non-ruin structure of this type is actually
        standing. duplicateOf can name a seed/custom type that's registered
        (or even just another pendingBlueprints id) but has no built instance
        yet -- approve_blueprint's upgrade routing checks this first so it
        never pops a proposal into a doomed "no structure to upgrade" call."""
        return any(s.get("type") == type_ and not s.get("isRuin")
                   for s in self.civilization["structures"])

