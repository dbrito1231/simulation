# Sovereign God mode Phase 5 -- storyteller events and timed lawgiver modifiers.
# Split out of the original monolithic scripts/god_mode_smoke.py (pure move,
# no behavior change).
from _god_mode_smoke.helpers import *  # noqa: F401,F403


def test_divine_modifier_default_and_flag_gate():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 1.0,
                    "no active effect must return exactly the default")
        applied = _apply_story_event(engine, "req-modifier-default-1",
                                      modifiers={"gather_yield_multiplier": 0.25})
        assert_true(applied["ok"], applied)
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.25, "active effect not read back")
        se.GOD_MODE_ENABLED = False
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 1.0,
                    "the flag gate must win over an active effect still sitting in godState")
        se.GOD_MODE_ENABLED = True
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.25,
                    "re-enabling the flag must see the still-active effect again")
        print("  OK _divine_modifier: 1.0 default with nothing active; active value read back; "
              "flag-off always wins regardless of stored state")
    finally:
        se.GOD_MODE_ENABLED = old


def test_modifier_range_validation_every_key():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        for key, (lo, hi) in se.GOD_MODIFIER_RANGES.items():
            for value in (lo, (lo + hi) / 2.0, hi, 1.0):
                p = engine.god_preview(_story_event_envelope(modifiers={key: value}))
                assert_true(p["ok"], (key, value, p))
            below = engine.god_preview(_story_event_envelope(modifiers={key: lo - 0.01}))
            assert_true(below["ok"] is False and key in below["reason"], (key, below))
            above = engine.god_preview(_story_event_envelope(modifiers={key: hi + 0.01}))
            assert_true(above["ok"] is False and key in above["reason"], (key, above))
        unknown_key = engine.god_preview(_story_event_envelope(modifiers={"not_a_real_key": 1.0}))
        assert_true(unknown_key["ok"] is False and "unknown modifier key" in unknown_key["reason"], unknown_key)
        print(f"  OK all {len(se.GOD_MODIFIER_RANGES)} modifier keys: 0.0/fractional/1.0/max accepted, "
              f"out-of-range and unknown keys rejected")
    finally:
        se.GOD_MODE_ENABLED = old


def test_gather_zero_path_before_carry_cap_clamp():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["currentDistrict"] = "forest"
        agent["resources"]["wood"] = 0
        # gather_yield_multiplier's allowed range is 0.25..3.0 (never 0.0 --
        # only fish_yield_multiplier goes that low), but floor(1 * 0.25) is
        # still 0 for the base amount==1 a fresh agent gathers, so the
        # in-range minimum already exercises the zero-path.
        applied = _apply_story_event(engine, "req-gather-zero-1",
                                      modifiers={"gather_yield_multiplier": 0.25})
        assert_true(applied["ok"], applied)

        c = engine.civilization
        before_success = c["collectSuccesses"]
        before_ok = c.get("tool_gather_ok", 0)
        before_fail = c.get("tool_gather_fail", 0)
        summary = engine._perform_gather(agent, "wood")

        assert_true("found nothing" in summary, summary)
        assert_true(agent["resources"]["wood"] == 0,
                    "the zero-path resurrected an amount past the carry-cap clamp (max(1, min(...)))")
        assert_true(c["collectSuccesses"] == before_success, "zero-path incremented collectSuccesses")
        assert_true(c.get("tool_gather_ok", 0) == before_ok and c.get("tool_gather_fail", 0) == before_fail,
                    "zero-path touched the tool benchmark")
        assert_true(agent["lastGatherRejection"] is not None, "zero-path did not record lastGatherRejection")
        print("  OK a 0.0 gather_yield_multiplier returns BEFORE the carry-cap clamp: "
              "no resource added, collectSuccesses/tool benchmark untouched")
    finally:
        se.GOD_MODE_ENABLED = old


def test_fish_modifier_replaces_general_modifier():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        applied = _apply_story_event(engine, "req-fish-replace-1",
                                      modifiers={"gather_yield_multiplier": 0.25,
                                                "fish_yield_multiplier": 2.0})
        assert_true(applied["ok"], applied)

        agent["currentDistrict"] = "forest"
        agent["resources"]["wood"] = 0
        wood_summary = engine._perform_gather(agent, "wood")
        assert_true("found nothing" in wood_summary,
                    "gather_yield_multiplier=0.25 must still null a non-fish resource at base amount 1")

        agent["currentDistrict"] = "beach"
        agent["resources"]["fish"] = 0
        fish_summary = engine._perform_gather(agent, "fish")
        assert_true("found nothing" not in fish_summary,
                    "fish_yield_multiplier must REPLACE (not be nulled by) gather_yield_multiplier")
        assert_true(agent["resources"]["fish"] == 2,
                    f"expected fish_yield_multiplier=2.0 to double the base yield, got {agent['resources']['fish']}")
        print("  OK fish_yield_multiplier replaces (never multiplies with) gather_yield_multiplier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_collapsed_agent_recovers_under_zero_health_regen():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        applied = _apply_story_event(engine, "req-collapse-regen-1",
                                      modifiers={"health_regen_multiplier": 0.0},
                                      durationFrames=se.GOD_GUIDANCE_MAX_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        assert_true(engine._divine_modifier("health_regen_multiplier") == 0.0, "modifier not active")

        agent["incapacitated"] = True
        agent["health"] = 5.0
        agent["hunger"] = 60.0
        agent["deathFrame"] = None
        for _ in range(40):
            engine._update_survival(agent)
            if not agent["incapacitated"]:
                break
        assert_true(not agent["incapacitated"],
                    "a collapsed agent never recovered under a 0.0 health_regen_multiplier -- "
                    "COLLAPSE_REGEN must be excluded from divine scaling")
        assert_true(agent["health"] >= se.COLLAPSE_REVIVE_HEALTH, agent["health"])
        print("  OK a collapsed agent still recovers under a 0.0 health_regen_multiplier "
              "(COLLAPSE_REGEN is never scaled)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_survival_arithmetic_ordering_hunger_and_starvation():
    """Hunger drain and starvation damage multiply their base rate BEFORE
    the existing clamp, and 0.0 suppresses each independently without
    touching the other."""
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-survival-1",
                                      modifiers={"hunger_drain_multiplier": 0.0,
                                                "starvation_damage_multiplier": 0.0})
        assert_true(applied["ok"], applied)
        # Above EAT_THRESHOLD so the eat/share branches (which mutate
        # hunger for reasons unrelated to hunger_drain_multiplier) never
        # trigger -- isolates the drain-clamp arithmetic itself.
        agent = engine.agents[0]
        agent["incapacitated"] = False
        agent["hunger"] = 90.0
        agent["health"] = 50.0
        before_hunger = agent["hunger"]
        engine._update_survival(agent)
        assert_true(agent["hunger"] == before_hunger, "0.0 hunger_drain_multiplier still drained hunger")

        # Starvation damage: hunger already at 0 with no edibles anywhere to
        # eat/share, so the ONLY thing that can move health this tick is the
        # starvation branch -- isolates the starvation-damage arithmetic.
        for a in engine.agents:
            for rid in se.EDIBLE_RESOURCES:
                a["resources"][rid] = 0
        agent["incapacitated"] = False
        agent["hunger"] = 0.0
        agent["health"] = 50.0
        before_health = agent["health"]
        engine._update_survival(agent)
        assert_true(agent["health"] == before_health, "0.0 starvation_damage_multiplier still damaged health")
        print("  OK hunger_drain_multiplier=0.0 suppresses drain; starvation_damage_multiplier=0.0 suppresses damage")
    finally:
        se.GOD_MODE_ENABLED = old


def test_identity_path_all_modifiers_1_0_byte_identical():
    """An effective 1.0 for every key must execute the identical arithmetic
    (and therefore produce identical results) as the feature-off baseline --
    proven by running the SAME operations against two independently built
    engines and comparing the concrete outputs, the same pattern
    sid_parity_smoke.py uses for its engine-vs-control comparisons."""
    old = se.GOD_MODE_ENABLED
    try:
        se.GOD_MODE_ENABLED = False
        baseline = make_engine()
        b_agent = baseline.agents[0]
        b_agent["currentDistrict"] = "forest"
        b_agent["resources"]["wood"] = 0
        b_gather_summary = baseline._perform_gather(b_agent, "wood")
        b_agent["incapacitated"] = False
        b_agent["hunger"], b_agent["health"] = 40.0, 70.0
        baseline._update_survival(b_agent)
        b_struct = _add_test_structure(baseline, condition=50.0)
        baseline._tick_structure_decay()
        cap = baseline._storage_capacity("food")
        baseline.civilization["stockpile"]["food"] = cap + 8
        baseline._tick_spoilage()
        b_spoilage = dict(baseline.civilization.get("lastSpoilage") or {})
        b_food = baseline.civilization["stockpile"]["food"]

        se.GOD_MODE_ENABLED = True
        divine = make_engine()
        # Insert the active event directly (bypassing preview/apply) so the
        # divine command's OWN narration/Chronicle side effects -- which are
        # expected and unrelated to the arithmetic contract under test --
        # never enter this comparison.
        divine._god_events_insert({
            "id": "divine-identity-test", "kind": "story_event",
            "title": "t", "narration": "n", "visibility": "public", "targetId": None,
            "createdFrame": divine.frameTick, "startFrame": divine.frameTick,
            "expiresFrame": divine.frameTick + 10_000_000, "status": "active",
            "modifiers": {k: 1.0 for k in se.GOD_MODIFIER_RANGES},
            "primitiveInterventionIds": [], "providenceId": None, "replaces": None,
        })
        d_agent = divine.agents[0]
        d_agent["currentDistrict"] = "forest"
        d_agent["resources"]["wood"] = 0
        d_gather_summary = divine._perform_gather(d_agent, "wood")
        assert_true(d_gather_summary == b_gather_summary, (d_gather_summary, b_gather_summary))
        assert_true(d_agent["resources"]["wood"] == b_agent["resources"]["wood"], "gather diverged at 1.0")

        d_agent["incapacitated"] = False
        d_agent["hunger"], d_agent["health"] = 40.0, 70.0
        divine._update_survival(d_agent)
        assert_true(d_agent["hunger"] == b_agent["hunger"] and d_agent["health"] == b_agent["health"],
                    "survival tick diverged at 1.0")

        d_struct = _add_test_structure(divine, condition=50.0)
        divine._tick_structure_decay()
        assert_true(d_struct["condition"] == b_struct["condition"], "structure decay diverged at 1.0")

        divine.civilization["stockpile"]["food"] = cap + 8
        divine._tick_spoilage()
        d_spoilage = dict(divine.civilization.get("lastSpoilage") or {})
        assert_true(divine.civilization["stockpile"]["food"] == b_food, "spoilage diverged at 1.0")
        assert_true(d_spoilage == b_spoilage, "spoilage record diverged at 1.0")
        print("  OK an effective 1.0 modifier for every key is byte-identical to the feature-off baseline "
              "(gather, survival, structure decay, spoilage)")
    finally:
        se.GOD_MODE_ENABLED = old


def test_carry_cap_and_low_stock_boundaries():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["currentDistrict"] = "forest"
        applied = _apply_story_event(engine, "req-carry-cap-1",
                                      modifiers={"gather_yield_multiplier": 3.0})
        assert_true(applied["ok"], applied)
        agent["resources"]["wood"] = se.COLLECT_CAP - 1
        engine._perform_gather(agent, "wood")
        assert_true(agent["resources"]["wood"] == se.COLLECT_CAP,
                    "a maxed divine multiplier must still respect the carry cap")

        engine2 = make_engine()
        agent2 = engine2.agents[0]
        agent2["currentDistrict"] = "forest"
        agent2["resources"]["wood"] = 0
        engine2._ensure_district_stocks()
        engine2._set_district_stock("forest", "wood", 1)  # near-zero but > 0 -> ecology floors amount at 1
        applied2 = _apply_story_event(engine2, "req-carry-cap-2",
                                       modifiers={"gather_yield_multiplier": 0.4})
        assert_true(applied2["ok"], applied2)
        before = engine2.civilization["collectSuccesses"]
        summary2 = engine2._perform_gather(agent2, "wood")
        assert_true("found nothing" in summary2, summary2)
        assert_true(engine2.civilization["collectSuccesses"] == before,
                    "low-stock + fractional divine multiplier zero-path still incremented collectSuccesses")
        print("  OK carry-cap clamp still applies at max divine multiplier; "
              "low ecology stock + a fractional divine multiplier can still zero out")
    finally:
        se.GOD_MODE_ENABLED = old


def test_spoilage_divine_multiplier_bounds():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        c = engine.civilization
        cap = engine._storage_capacity("food")
        c["stockpile"]["food"] = cap + 50
        applied = _apply_story_event(engine, "req-spoil-0", modifiers={"spoilage_multiplier": 0.0})
        assert_true(applied["ok"], applied)
        before = c["stockpile"]["food"]
        engine._tick_spoilage()
        assert_true(c["stockpile"]["food"] == before, "a 0.0 spoilage multiplier must spoil nothing")

        engine2 = make_engine()
        c2 = engine2.civilization
        cap2 = engine2._storage_capacity("food")
        # Zero out every agent's edible holdings first so "overflow" is
        # driven ENTIRELY by the stockpile figure below -- _tick_spoilage's
        # overflow = stockpile + agent-held - capacity, and a fresh roster
        # may already be holding some food.
        for a in engine2.agents:
            for rid in se.EDIBLE_RESOURCES:
                a["resources"][rid] = 0
        overflow = 5
        c2["stockpile"]["food"] = cap2 + overflow
        applied2 = _apply_story_event(engine2, "req-spoil-max", modifiers={"spoilage_multiplier": 3.0})
        assert_true(applied2["ok"], applied2)
        engine2._tick_spoilage()
        spoiled = (cap2 + overflow) - c2["stockpile"]["food"]
        assert_true(0 <= spoiled <= overflow, f"spoiled {spoiled} exceeded eligible overflow {overflow}")
        print("  OK spoilage_multiplier: 0.0 spoils nothing; a 3.0 maximum never exceeds eligible overflow")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_modifier_conflict_warnings_non_fatal():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        preview = engine.god_preview(_story_event_envelope(
            modifiers={"gather_yield_multiplier": 2.0, "hunger_drain_multiplier": 2.0}))
        assert_true(preview["ok"], preview)
        warnings = preview.get("warnings") or []
        assert_true(len(warnings) >= 1, preview)
        assert_true(any("hunger drain" in w.lower() for w in warnings), warnings)
        single = engine.god_preview(_story_event_envelope(
            modifiers={"gather_yield_multiplier": 2.0}))
        assert_true(single["ok"] and not single.get("warnings"), single)
        print("  OK conflicting modifier pair: preview ok:true with warnings[]; single modifier omits warnings")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_one_value_per_key_and_replace_effect_id():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied1 = _apply_story_event(engine, "req-occupy-1",
                                      title="Bountiful Harvest", narration="The fields swell.",
                                      modifiers={"gather_yield_multiplier": 2.0})
        assert_true(applied1["ok"], applied1)
        event_id = applied1["outcome"]["interventionId"]
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 2.0, "occupying event did not register")

        conflict_preview = engine.god_preview(_story_event_envelope(
            title="Black River", narration="A blight follows.",
            modifiers={"gather_yield_multiplier": 0.5}))
        assert_true(conflict_preview["ok"] is False
                    and "gather_yield_multiplier" in conflict_preview["reason"]
                    and "replaceEffectId" in conflict_preview["reason"], conflict_preview)

        replace_preview = engine.god_preview(_story_event_envelope(
            title="Black River", narration="A blight follows.",
            modifiers={"gather_yield_multiplier": 0.5}, replaceEffectId=event_id))
        assert_true(replace_preview["ok"], replace_preview)
        replace_applied = engine.god_apply(replace_preview["previewId"], "req-replace-1")
        assert_true(replace_applied["ok"], replace_applied)
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 0.5, "replacement did not take effect")

        active_holders = [e for e in engine.civilization["godState"]["activeEvents"]
                          if e.get("status") == "active"
                          and "gather_yield_multiplier" in (e.get("modifiers") or {})]
        assert_true(len(active_holders) == 1, active_holders)
        original = next(e for e in engine.civilization["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(original["status"] == "replaced", original)

        bad_replace = engine.god_preview(_story_event_envelope(
            title="X", narration="Y", modifiers={"gather_yield_multiplier": 1.0},
            replaceEffectId="not-a-real-event-id"))
        assert_true(bad_replace == {"ok": False, "reason": "replaceEffectId does not name an active event"},
                    bad_replace)
        print("  OK one active value per key enforced; replaceEffectId accepted only against the active occupier")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_atomicity_one_invalid_component_changes_nothing():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        before_health = agent["health"]
        before_events = [dict(e) for e in engine.civilization["godState"]["activeEvents"]]
        before_providence = engine.civilization["godState"]["providence"]
        before_intervened = engine.civilization["godState"]["intervened"]

        preview = engine.god_preview(_story_event_envelope(
            title="Mixed Fortune", narration="Fate is uneven.",
            primitives=[
                {"kind": "agent_vitals", "payload": {"targetId": agent["id"], "healthDelta": 5}},
                {"kind": "agent_vitals", "payload": {"targetId": 999999, "healthDelta": 5}},
            ]))
        assert_true(preview["ok"] is False, preview)
        assert_true(agent["health"] == before_health, "an invalid sibling primitive still mutated the world")
        assert_true(engine.civilization["godState"]["activeEvents"] == before_events,
                    "atomicity violated: activeEvents changed")
        assert_true(engine.civilization["godState"]["providence"] == before_providence,
                    "atomicity violated: providence changed")
        assert_true(engine.civilization["godState"]["intervened"] == before_intervened,
                    "atomicity violated: intervened flipped")
        print("  OK story_event with one invalid component rejects atomically -- nothing changes")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_full_composition_and_reversibility_class():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        agent = engine.agents[0]
        agent["health"] = 50.0
        c = engine.civilization
        resource_id = "wood"
        struct = _add_test_structure(engine, condition=80.0)
        stock_before = c["stockpile"].get(resource_id, 0)

        preview = engine.god_preview(_story_event_envelope(
            title="Bountiful Harvest", narration="The gods smile on the fields.",
            modifiers={"gather_yield_multiplier": 2.0},
            primitives=[
                {"kind": "agent_vitals", "payload": {"targetId": agent["id"], "healthDelta": 5}},
                {"kind": "grant_resource", "payload": {"resourceId": resource_id, "amount": 10}},
                {"kind": "structure_condition", "payload": {"structureId": struct["id"], "delta": 5}},
            ],
            providence={"text": "A season of plenty is coming."}))
        assert_true(preview["ok"], preview)
        assert_true(preview["reversibilityClass"] == "consequential", preview)
        assert_true(preview["previewOutcome"]["modifiers"] == {"gather_yield_multiplier": 2.0},
                    preview["previewOutcome"])
        assert_true(len(preview["previewOutcome"]["primitives"]) == 3, preview["previewOutcome"])

        applied = engine.god_apply(preview["previewId"], "req-composition-1")
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        assert_true(agent["health"] == 55.0, agent["health"])
        assert_true(c["stockpile"].get(resource_id, 0) == stock_before + 10, c["stockpile"])
        assert_true(struct["condition"] == 85.0, struct)
        assert_true(c["godState"]["providence"]["text"] == "A season of plenty is coming.",
                    c["godState"]["providence"])
        assert_true(engine._divine_modifier("gather_yield_multiplier") == 2.0, "modifier not active")
        event = next(e for e in c["godState"]["activeEvents"] if e["id"] == event_id)
        assert_true(event["providenceId"] == c["godState"]["providence"]["id"], event)
        assert_true(len(event["primitiveInterventionIds"]) == 3, event)
        print("  OK story_event applies modifiers+primitives+providence atomically; "
              "reversibilityClass 'consequential' once primitives are present")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_expiry_closes_exactly_once_with_linked_providence():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-expiry-1",
                                      title="A Brief Omen", narration="It will not last.",
                                      modifiers={"structure_decay_multiplier": 0.0},
                                      providence={"text": "Endure -- this too shall pass."},
                                      durationFrames=se.GOD_GUIDANCE_MIN_DURATION_FRAMES)
        assert_true(applied["ok"], applied)
        god = engine.civilization["godState"]
        event = god["activeEvents"][-1]
        expires_frame = event["expiresFrame"]
        providence_id = event["providenceId"]
        assert_true(god["providence"]["id"] == providence_id, god["providence"])

        engine.frameTick = expires_frame - 1
        assert_true(engine._divine_modifier("structure_decay_multiplier") == 0.0,
                    "modifier stopped influencing one frame before expiresFrame")
        engine._expire_divine_effects()
        assert_true(event["status"] == "active", "event closed one frame too early")

        engine.frameTick = expires_frame
        assert_true(engine._divine_modifier("structure_decay_multiplier") == 1.0,
                    "modifier still influenced at exactly expiresFrame, BEFORE cleanup ran -- "
                    "the predicate itself, not the cleanup sweep, must stop it")
        engine._expire_divine_effects()
        assert_true(event["status"] == "expired", event)
        assert_true(god["providence"] is None, "linked providence was not closed when its event expired")

        engine._expire_divine_effects()
        assert_true(event["status"] == "expired", "a second expiry sweep re-fired an already-closed event")
        print("  OK story_event expiry: modifier stops exactly at expiresFrame (before cleanup runs); "
              "event + its linked providence both close exactly once")
    finally:
        se.GOD_MODE_ENABLED = old


def test_god_cancel_events_and_refuses_miracles():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-cancel-1",
                                      title="A Long Winter", narration="Hunger presses in.",
                                      modifiers={"hunger_drain_multiplier": 2.0},
                                      providence={"text": "Ration carefully."})
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        assert_true(engine._divine_modifier("hunger_drain_multiplier") == 2.0, "modifier not active")

        cancelled = engine.god_cancel(event_id)
        assert_true(cancelled == {"ok": True, "cancelled": True,
                                  "targetId": event_id, "targetKind": "story_event"}, cancelled)
        assert_true(engine._divine_modifier("hunger_drain_multiplier") == 1.0,
                    "cancelled event still influenced the modifier")
        assert_true(engine.civilization["godState"]["providence"] is None,
                    "cancelling the event did not also close its linked providence")

        vitals_preview = engine.god_preview(_vitals_envelope(engine.agents[0]["id"], health_delta=1))
        vitals_applied = engine.god_apply(vitals_preview["previewId"], "req-cancel-miracle")
        assert_true(vitals_applied["ok"], vitals_applied)
        miracle_cancel = engine.god_cancel(vitals_applied["interventionId"])
        assert_true(miracle_cancel == {
            "ok": True, "cancelled": False, "reason": "nothing to cancel",
            "targetId": vitals_applied["interventionId"],
        }, miracle_cancel)
        print("  OK god_cancel closes an active story_event and its linked providence; "
              "refuses an irreversible miracle's interventionId")
    finally:
        se.GOD_MODE_ENABLED = old


def test_active_events_survive_save_restore_with_absolute_expiry():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    old_db_path = se.DB_PATH
    tmpdir = tempfile.mkdtemp()
    tmp_db = str(Path(tmpdir) / "state_god_events_roundtrip.db")
    try:
        engine = make_engine()
        applied = _apply_story_event(engine, "req-restore-event-1",
                                      title="Merciful Rain", narration="The drought breaks.",
                                      modifiers={"starvation_damage_multiplier": 0.0},
                                      durationFrames=20000)
        assert_true(applied["ok"], applied)
        event_id = applied["outcome"]["interventionId"]
        expires_frame = engine.civilization["godState"]["activeEvents"][-1]["expiresFrame"]

        se.DB_PATH = tmp_db
        assert_true(engine.save_state(), "save_state failed")

        restored = make_engine()
        assert_true(restored.restore_state(), "restore_state failed")
        events = restored.civilization["godState"]["activeEvents"]
        event = next(e for e in events if e["id"] == event_id)
        assert_true(event["status"] == "active", event)
        assert_true(event["expiresFrame"] == expires_frame,
                    "expiresFrame must round-trip as an ABSOLUTE frame, not be re-based on restore")
        assert_true(restored._divine_modifier("starvation_damage_multiplier") == 0.0,
                    "a restored active event does not influence _divine_modifier")

        restored.frameTick = expires_frame
        restored._expire_divine_effects(restore=True)
        assert_true(event["status"] == "restore-closed", event)
        print("  OK an active story_event survives save_state/restore_state with an absolute "
              "expiresFrame; restore-time expiry closes it once")
    finally:
        se.DB_PATH = old_db_path
        se.GOD_MODE_ENABLED = old


def test_preview_shows_divine_and_custom_rule_contributions_separately():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        effect = {
            "subject": {"resource": "wood"},
            "condition": {"action": "collect_resource"},
            "modifier": {"kind": "add", "value": 2},
        }
        engine.civilization["customRuleModifiers"]["wood_charter"] = effect

        preview = engine.god_preview(_story_event_envelope(
            title="Bountiful Harvest", narration="The gods amplify the harvest.",
            modifiers={"gather_yield_multiplier": 2.0}))
        assert_true(preview["ok"], preview)
        outcome = preview["previewOutcome"]
        assert_true(outcome["modifiers"] == {"gather_yield_multiplier": 2.0}, outcome)
        assert_true(any(e["ruleId"] == "wood_charter" and e["value"] == 2
                        for e in outcome["customRuleContext"]), outcome)

        # A modifier unrelated to gather/fish carries no custom-rule context key at all.
        preview2 = engine.god_preview(_story_event_envelope(
            title="A Long Winter", narration="Hunger presses in.",
            modifiers={"hunger_drain_multiplier": 2.0}))
        assert_true("customRuleContext" not in preview2["previewOutcome"], preview2["previewOutcome"])
        print("  OK preview discloses the divine multiplier and any active custom-rule contribution "
              "separately for gather/fish modifiers only")
    finally:
        se.GOD_MODE_ENABLED = old


def test_story_event_private_visibility_and_target_validation():
    old = se.GOD_MODE_ENABLED
    se.GOD_MODE_ENABLED = True
    try:
        engine = make_engine()
        target_id = engine.agents[0]["id"]

        missing_target = engine.god_preview(_story_event_envelope(visibility="private"))
        assert_true(missing_target["ok"] is False and "targetId" in missing_target["reason"], missing_target)

        applied = _apply_story_event(engine, "req-private-story-1",
                                      title="A Secret Trial", narration="Only you will know.",
                                      visibility="private", targetId=target_id)
        assert_true(applied["ok"], applied)
        snap = engine.snapshot()
        dumped = json.dumps(snap)
        assert_true("Only you will know" not in dumped, "a private story_event leaked into /state")
        assert_true(not any("Only you will know" in line for line in engine.activityLog),
                    "a private story_event leaked into public activity")
        print("  OK a private story_event requires a living targetId and never reaches public /state/activity")
    finally:
        se.GOD_MODE_ENABLED = old


# --- Phase 6 tests: divine weather override (docs/archive/plan-sovereign-god-mode-
# v2.md Phase 6 -- event-authoritative clock, RNG-free forced entry, handoff
# to the natural cycle's successor of the OVERRIDDEN state, consequential
# reversibility class) ---
