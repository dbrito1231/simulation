# Sid-parity Phase 3 belief-biased voting/persuasion plus the PIANO module stagger/cache/pulse and library/civic-era tests.
# Split out of the original monolithic scripts/sid_parity_smoke.py (pure move,
# no behavior change).
from _sid_parity_smoke.helpers import *  # noqa: F401,F403


def test_belief_biased_vote(engine):
    # Find a harvest_spirit believer and a river_spirit believer.
    harvest = next(
        (a for a in engine.agents if se.MEME_SEED_ID in a.get("beliefs", ())), None)
    river = next(
        (a for a in engine.agents if se.MEME_RIVAL_ID in a.get("beliefs", ())), None)
    assert_true(harvest and river, "need both meme holders")
    pending_ration = {"kind": "rationing", "name": "Rations", "value": 2}
    pending_priority = {"kind": "priority", "name": "Fish First", "value": "fish"}
    assert_true(engine._belief_biased_vote(harvest, pending_ration) == "yes",
                "harvest should favor rationing")
    assert_true(engine._belief_biased_vote(river, pending_ration) == "no",
                "river should oppose rationing")
    assert_true(engine._belief_biased_vote(river, pending_priority) == "yes",
                "river should favor priority")
    print("  OK belief-biased votes")


def test_authored_belief_persuasion_and_project_preference(engine):
    # The resolved Phase-3 rule is fully emergent: a villager with neither a
    # seed belief nor reflection practice can author the first new belief.
    uninitiated = next(a for a in engine.agents if not a.get("beliefs"))
    uninitiated["skills"]["reflection"] = 0.0
    ungated_payload = engine._build_think_payload(uninitiated)
    assert_true("found_belief" in ungated_payload["available_actions"],
                "belief authoring was hidden from an uninitiated agent")
    engine.apply_decision(uninitiated, {
        "action": "found_belief",
        "belief": {
            "id": "first_voice", "name": "First Voice",
            "tenet": "A new village learns by naming what it hopes for.",
            "affinity": ["custom"],
        },
        "reasoning": "Giving the village its first original belief.",
    })
    assert_true("first_voice" in uninitiated["beliefs"],
                "zero-reflection, belief-free agent could not found a belief")

    founder = next(a for a in engine.agents if se.MEME_SEED_ID in a.get("beliefs", ()))
    listener = next(a for a in engine.agents if a is not founder and not a.get("beliefs"))
    engine.apply_decision(founder, {
        "action": "found_belief",
        "belief": {
            "id": "granary_steward", "name": "Granary Stewardship",
            "tenet": "A granary stores the shared harvest safely.",
            "affinity": ["resource_tax"],
        },
        "reasoning": "A shared store makes our harvest secure.",
    })
    registry = engine.civilization.get("beliefRegistry") or {}
    assert_true("granary_steward" in registry, "authored belief was not persisted in registry")
    assert_true("granary_steward" in founder["beliefs"], "founder did not hold authored belief")
    serialized = engine._serialize_state()
    assert_true("granary_steward" in serialized["civilization"].get("beliefRegistry", {}),
                "authored belief missing from persisted state")

    engine.civilization["projectRegistry"]["shared_harvest_store"] = {
        "name": "Shared Harvest Store", "needs": {"wood": 1}, "custom": True,
    }
    engine.d["ROLE_PROJECT"][founder["role"]] = ["farm_plot", "shared_harvest_store"]
    preferred = engine._role_default_project(founder["role"], founder)
    assert_true(preferred == "shared_harvest_store",
                f"belief did not prefer matching project: {preferred}")

    # A named distant agent is valid for ordinary talk/movement, but it must
    # neither be eligible for server pitch scoring nor convert before contact.
    founder["x"] = founder["y"] = 0
    listener["x"] = listener["y"] = 1000
    distant_payload = engine._build_think_payload(founder)
    assert_true(listener["name"] not in distant_payload["nearby_beliefs"],
                "distant target was incorrectly eligible for pitch scoring")
    before_pitch_calls = engine.civilization.get("beliefPitchCalls", 0)
    engine.apply_decision(founder, {
        "action": "talk_to_nearby", "target": listener["name"],
        "message": "Our shared granary protects every family through lean days.",
        "belief_pitch": {"belief_id": "granary_steward",
                          "pitch": "Our shared granary protects every family through lean days."},
        "reasoning": "Trying to persuade from too far away.",
    })
    assert_true("granary_steward" not in listener["beliefs"],
                "distant belief pitch converted before the speakers met")
    assert_true(engine.civilization.get("beliefPitchCalls", 0) == before_pitch_calls,
                "distant belief pitch consumed a scorer result")

    founder["x"] = listener["x"] = 700
    founder["y"] = listener["y"] = 1000
    founder["relationships"][listener["name"]] = "ally"
    listener["relationships"][founder["name"]] = "ally"
    belief_id = "granary_steward"
    for frame in range(1000):
        engine.frameTick = frame
        if engine._deterministic_belief_roll(founder, listener, belief_id) <= \
                engine._belief_conversion_probability(founder, listener, se.BELIEF_FALLBACK_QUALITY):
            break
    engine.apply_decision(founder, {
        "action": "talk_to_nearby", "target": listener["name"],
        "message": "Our shared granary protects every family through lean days.",
        "belief_pitch": {"belief_id": belief_id,
                          "pitch": "Our shared granary protects every family through lean days."},
        "reasoning": "Persuading a neighbor to protect the harvest.",
    })
    assert_true(belief_id in listener["beliefs"], "offline belief pitch did not convert listener")
    assert_true(founder["relationships"].get(listener["name"]) == "ally"
                and listener["relationships"].get(founder["name"]) == "ally",
                "co-believers did not receive reciprocal relationship bonus")
    print("  OK authored belief, project preference, deterministic persuasion")


def test_role_fallback_switch():
    # Avoid importing simulation.server (it constructs the live SimEngine and
    # resumes state.db). Replicate the Phase-1 switch_role branch of
    # role_fallback_action with the same guard conditions.
    role = "trader"
    needed_role = "farmer"
    protected = {"elder", "builder", "healer"}
    primary = {"farmer": "food", "fisher": "fish", "gatherer": "wood", "miner": "gold"}
    assert_true(
        needed_role and needed_role != role
        and role not in protected
        and not primary.get(role),
        "trader should be eligible to switch",
    )
    decision = {
        "action": "switch_role", "new_role": needed_role,
        "reasoning": f"The village needs a {needed_role}; retraining to fill the gap.",
    }
    assert_true(decision["action"] == "switch_role", decision)
    assert_true(decision["new_role"] == "farmer", decision)
    print("  OK role_fallback_action switch_role guards")


def test_piano_stagger_offline():
    """Phase 5: module stagger works without LM (runner returns None)."""
    engine = make_engine(4)
    calls = []

    def stub(module, agent_name, context, frame_tick=None):
        calls.append((module, context))
        return "ok"

    engine.d["run_piano_module"] = stub
    # Force-enable for this unit check only.
    old, old_always = se.PIANO_MODULES, se.ALWAYS_ON_MODULES
    se.PIANO_MODULES, se.ALWAYS_ON_MODULES = True, False
    try:
        reports1, tick1, runs1 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            0,
            "role=farmer",
        )
        # tick 1: perception + desire only (social needs %2==0, reflection %3==0)
        assert_true(tick1 == 1, tick1)
        assert_true(runs1 == 2, runs1)
        assert_true("perception" in reports1 and "desire" in reports1, reports1)
        assert_true("social" not in reports1 and "reflection" not in reports1, reports1)
        # Cross-module visibility: tick 1 has nothing cached yet, so no
        # last_reports suffix should be attached to any dispatched context.
        tick1_calls = calls[:2]
        assert_true(len(tick1_calls) == 2, tick1_calls)
        assert_true(all("last_reports" not in ctx for _, ctx in tick1_calls),
                    tick1_calls)

        reports2, tick2, runs2 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick1,
            "role=farmer",
        )
        # tick 2: perception + desire + social
        assert_true(tick2 == 2 and runs2 == 3, (tick2, runs2, reports2))
        assert_true("social" in reports2, reports2)
        # Every module dispatched on tick 2 should see both tick-1 reports,
        # each labeled "1 ago" (tick2 - tick1 == 1).
        tick2_calls = calls[2:5]
        assert_true(len(tick2_calls) == 3, tick2_calls)
        assert_true(all("perception(1 ago)" in ctx and "desire(1 ago)" in ctx
                        for _, ctx in tick2_calls), tick2_calls)

        # Force social's cache entry to look 2 ticks stale (as if it were
        # last reported on tick 1 instead of tick 2), then dispatch an
        # off-tick turn (social doesn't run on odd ticks) to confirm the
        # decision payload age-labels the stale fill distinctly from the
        # bare "module:" form fresh reports use.
        cache = engine._piano_module_cache["Aria"]
        cache["social"]["tick"] = tick2 - 1

        reports3, tick3, runs3 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick2,
            "role=farmer",
        )
        # tick 3: perception + desire + reflection run fresh; social is
        # off-tick, served from cache and age-labeled "2 turns ago".
        assert_true(tick3 == 3 and runs3 == 3, (tick3, runs3, reports3))
        assert_true("reflection" in reports3, reports3)
        assert_true("social (2 turns ago):" in reports3, reports3)

        # TTL boundary: a report older than PIANO_CROSS_CONTEXT_TTL must be
        # excluded from the last_reports suffix. Force "desire"'s cache entry
        # to look stale, then confirm the next dispatch's context omits it.
        cache["desire"]["tick"] = tick3 - se.PIANO_CROSS_CONTEXT_TTL - 1
        before = len(calls)
        reports4, tick4, runs4 = engine._run_piano_modules(
            "Aria",
            {"perception": True, "social": True, "desire": True, "reflection": True},
            tick3,
            "role=farmer",
        )
        tick4_calls = calls[before:]
        assert_true(len(tick4_calls) == 3, tick4_calls)
        assert_true(all("desire(" not in ctx for _, ctx in tick4_calls),
                    tick4_calls)
        print("  OK PIANO stagger (2 / 3 / 3 modules across ticks 1-3) "
              "+ cross-module last_reports visibility, age labels, TTL cutoff")
    finally:
        se.PIANO_MODULES, se.ALWAYS_ON_MODULES = old, old_always


def test_piano_cache_restore_roundtrip():
    """Phase B: _piano_module_cache survives a save/restore round-trip via
    each agent's persistence-only moduleReports mirror."""
    import tempfile

    engine = make_engine(4)

    def stub(module, agent_name, context, frame_tick=None):
        return f"{module} report for {agent_name}"

    engine.d["run_piano_module"] = stub
    old_piano, old_always = se.PIANO_MODULES, se.ALWAYS_ON_MODULES
    se.PIANO_MODULES, se.ALWAYS_ON_MODULES = True, False
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_roundtrip.db")
    try:
        agent_name = engine.agents[0]["name"]
        modules = {"perception": True, "social": True, "desire": True, "reflection": True}
        # Tick 1: perception + desire only. Tick 2: + social (tick % 2 == 0).
        # This leaves "social" freshly cached right before the restart.
        _, tick1, _ = engine._run_piano_modules(agent_name, modules, 0, "role=farmer")
        reports2, tick2, runs2 = engine._run_piano_modules(
            agent_name, modules, tick1, "role=farmer")
        assert_true(tick2 == 2 and "social" in reports2, (tick2, reports2))
        # Mirror the cache into the agent dict the same way the post-think
        # callback does (sim_engine.py, right after agent["moduleTick"] = new_tick).
        agent = engine._find_agent(agent_name)
        agent["moduleTick"] = tick2
        agent["moduleReports"] = {
            m: dict(v) for m, v in engine._piano_module_cache.get(agent_name, {}).items()
        }
        cache_before = deepcopy(engine._piano_module_cache.get(agent_name))
        assert_true(cache_before and "social" in cache_before,
                    "cache should hold a fresh social report before the restart")

        # Serialize + persist to a throwaway state.db, then simulate a fresh
        # process by wiping the in-memory cache (as a restart would) before
        # restoring from disk.
        se.DB_PATH = tmp_db
        engine.save_state()
        engine._piano_module_cache = {}

        restored = engine.restore_state()
        assert_true(restored, "restore_state should succeed against the just-written db")
        assert_true(agent_name in engine._piano_module_cache,
                    f"restore did not rehydrate cache for {agent_name}: "
                    f"{engine._piano_module_cache}")
        restored_entry = engine._piano_module_cache[agent_name]
        for module, report in cache_before.items():
            assert_true(restored_entry.get(module) == report,
                        f"restored cache entry for {module} mismatched: "
                        f"{restored_entry.get(module)} != {report}")

        # First post-restore turn: dispatch tick 3, where social is off-tick
        # (tick % 2 != 0). It must be served as an age-labeled fill from the
        # rehydrated cache instead of an empty slot.
        agent2 = engine._find_agent(agent_name)
        restored_tick = int(agent2.get("moduleTick") or 0)
        assert_true(restored_tick == tick2, (restored_tick, tick2))
        reports3, tick3, runs3 = engine._run_piano_modules(
            agent_name, modules, restored_tick, "role=farmer")
        assert_true(tick3 == restored_tick + 1, (tick3, restored_tick))
        assert_true("social (" in reports3 and "turns ago):" in reports3,
                    f"restored cache did not serve an off-tick social fill: {reports3}")
        print("  OK PIANO module cache rehydrates from state.db after restore "
              "and serves an off-tick fill on the first post-restore turn")
    finally:
        se.PIANO_MODULES, se.ALWAYS_ON_MODULES = old_piano, old_always
        se.DB_PATH = old_db_path


def test_always_on_piano_pulse():
    """Phase A: the gated scheduler does no clean work and decisions only read."""
    engine = make_engine(4)
    calls = []

    def stub(module, agent_name, context, frame_tick=None, timeout_s=None):
        calls.append((module, agent_name, timeout_s))
        return f"{module} note"

    old_always, old_piano = se.ALWAYS_ON_MODULES, se.PIANO_MODULES
    se.ALWAYS_ON_MODULES, se.PIANO_MODULES = True, True
    try:
        engine.d["run_piano_module"] = stub
        now = time.time()
        # A clean, fresh roster must make a true zero-dispatch pulse.
        for agent in engine.agents:
            agent["contextDirty"] = False
            agent["contextDirtySince"] = now
            engine._piano_module_cache[agent["name"]] = {
                m: {"tick": 0, "text": "fresh", "wall_ts": now}
                for m in ("perception", "social", "desire", "reflection")}
        engine._pulse_piano_modules()
        assert_true(not calls and engine._module_pulse_work[-1] == 0,
                    (calls, engine._module_pulse_work))

        # One dirty subject is selected, bounded by free PIANO slots, and
        # completion writes persistent wall-clock reports then clears dirty.
        agent = engine.agents[0]
        agent["contextDirty"] = True
        agent["contextDirtySince"] = time.time()
        for note in engine._piano_module_cache[agent["name"]].values():
            note["wall_ts"] = agent["contextDirtySince"] - 1
        engine._pulse_piano_modules()
        deadline = time.time() + 2
        while engine._piano_refresh_inflight and time.time() < deadline:
            time.sleep(.01)
        assert_true(0 < len(calls) <= min(se.MODULE_PULSE_MAX_BATCH, se.PIANO_CONCURRENT_LLM), calls)
        assert_true(all(timeout_s == se.MODULE_REFRESH_TIMEOUT_S
                        for _, _, timeout_s in calls), calls)
        assert_true(agent["contextDirty"], "batch cap should leave remaining modules due")
        # A pulse may refresh fewer than the remaining modules (batch=1 in
        # the Attempt-2 re-soak), so drain bounded pulses until this dirty
        # generation has every enabled report instead of assuming batch=2.
        for _ in range(4):
            if not agent["contextDirty"]:
                break
            engine._pulse_piano_modules()
            deadline = time.time() + 2
            while engine._piano_refresh_inflight and time.time() < deadline:
                time.sleep(.01)
        assert_true(not agent["contextDirty"], agent)
        report, tick, runs = engine._run_piano_modules(agent["name"], agent["modules"], 7, "unused")
        assert_true(tick == 7 and runs == 0 and "s ago):" in report, (report, tick, runs))

        # A failed refresh preserves its prior note and remains eligible.
        agent["contextDirty"] = True
        agent["contextDirtySince"] = time.time()
        old_note = dict(engine._piano_module_cache[agent["name"]]["perception"])
        engine.d["run_piano_module"] = lambda *args, **kwargs: None
        engine._piano_module_cache[agent["name"]].pop("desire")
        engine._pulse_piano_modules()
        deadline = time.time() + 2
        while engine._piano_refresh_inflight and time.time() < deadline:
            time.sleep(.01)
        assert_true(agent["contextDirty"], "failed refresh incorrectly cleared dirty")
        assert_true(engine._piano_module_cache[agent["name"]]["perception"] == old_note,
                    "failed refresh replaced prior note")
        # The server runner keeps 15s for legacy fan-out and accepts the
        # explicit 60s always-on override without changing its default.
        import server  # noqa: E402
        timeouts, old_complete = [], server.lm_complete
        server.lm_complete = lambda *args, **kwargs: timeouts.append(kwargs["timeout"]) or "ok"
        try:
            server.run_piano_module("perception", "Aria", "ctx")
            server.run_piano_module("perception", "Aria", "ctx",
                                    timeout_s=se.MODULE_REFRESH_TIMEOUT_S)
        finally:
            server.lm_complete = old_complete
        assert_true(timeouts == [server.PIANO_MODULE_TIMEOUT_S, se.MODULE_REFRESH_TIMEOUT_S], timeouts)
        print("  OK always-on PIANO pulse is gated, bounded, uses 60s refreshes, and preserves 15s fan-out")
    finally:
        se.ALWAYS_ON_MODULES, se.PIANO_MODULES = old_always, old_piano


def test_library_scaling_and_lessons():
    engine = make_engine(4)
    c = engine.civilization
    did = "village_core"
    c["structures"].append({"id": 9991, "type": "library", "districtId": did,
                            "condition": 100, "isRuin": False, "level": 30})
    c["libraryKnowledge"] = [
        {"agent": "Old", "skill": "craft", "level": 5.0, "frame": 1},
        {"agent": "Old", "skill": "build", "level": 4.0, "frame": 2},
    ]
    agent = engine.agents[0]
    agent["currentDistrict"] = did
    before = agent["skills"]["craft"]
    engine._study_at_library(agent)
    assert_true(agent["skills"]["craft"] - before == se.LIBRARY_STUDY_GAIN * 3,
                agent["skills"])
    assert_true("craft 5.0" in engine._library_lessons(did), engine._library_lessons(did))
    assert_true(engine._library_lessons("farm_north") is None, "lessons leaked outside library district")
    print("  OK library scaling + local prompt lessons")


def test_civic_era_requires_both_light_and_transit():
    """The final Civic Era rung is monotonic and requires BOTH a working
    light structure and working ocean transit -- neither alone is enough."""
    engine = make_engine(4)
    c = engine.civilization
    did = "village_core"
    c["projectRegistry"]["hearth"] = {
        "name": "Hearth", "needs": {"stone": 2}, "visualStyle": "generic",
        "function": {"light": {"scope": "district"}},
    }
    c["structures"].append({
        "id": 9800, "type": "hearth", "districtId": did,
        "condition": 100, "isRuin": False,
    })
    caps = engine._era_capabilities()
    assert_true("civilization" not in caps,
                f"light alone must not unlock civilization era, got {caps}")
    print(f"  OK light-only caps: {caps}")

    c["projectRegistry"]["dock"] = {
        "name": "Dock", "needs": {"wood": 2}, "visualStyle": "generic",
        "function": {"unlocks": [
            {"kind": "transit", "terrain": "ocean", "consumes": {"boat": 1}}]},
    }
    c["structures"].append({
        "id": 9801, "type": "dock", "districtId": "beach",
        "condition": 100, "isRuin": False,
    })
    caps = engine._era_capabilities()
    assert_true("civilization" in caps,
                f"light + transit should unlock civilization era, got {caps}")
    idx = engine._current_era_index()
    assert_true(idx == len(se.ERA_LADDER) - 1,
                f"expected the final (Civic Era) rung, got index {idx} of {se.ERA_LADDER}")
    assert_true(se.ERA_LADDER[idx][0] == "Civic Era",
                f"expected Civic Era at the top rung, got {se.ERA_LADDER[idx]}")
    print(f"  OK light + transit -> civilization era (index={idx}, caps={caps})")
