# SPEC 02 — Engine Core

The fixed-timestep tick loop, time model, think scheduling, Sage's deterministic
emergency system, and full-state persistence.

**Canonical for:** tick loop + per-system frame cadence, day/season/year time
constants, think-scheduling constants, pause/resume/reset semantics, Sage
emergency trigger/response, state.db schema and payload shape.
**See also:** [01-architecture.md](01-architecture.md) for the flag index and
threading model; [03-cognition.md](03-cognition.md) for what happens inside a
think job's network call.

## Tick loop

`_tick_once()` (sim_engine/mixin_think_job.py:1446) runs under `self.lock` once per
`TICK_DT = 1/30 s` (`TICKS_PER_SEC = 30`). If `self.paused`, it returns
immediately — the sim clock (`frameTick`) freezes entirely. Otherwise
`frameTick` advances by 1 and, per frame `ft`, these flag-gated systems run on
their own cadence (all frame counts are ticks at 30/s):

| Gate | Cadence (frames) | System |
|---|---|---|
| `SURVIVAL_ENABLED` | 30 | `_update_survival` per agent |
| `MEMORY_ENABLED` | 1800 | `_run_memory_maintenance` |
| (unconditional) | 2400 | `_maybe_meta_update` |
| (unconditional) | 120 | `_maybe_auto_switch_role` |
| `RULES_ENABLED` | 150 | `_maybe_advance_rules` |
| `LIFECYCLE_ENABLED` | 150 | `_maybe_resolve_stalled_succession` |
| `LIFECYCLE_ENABLED` | 300 | `_tick_lifecycle` |
| (unconditional) | 150 | a fixed batch: `_maybe_feed_starving`, `_maybe_repair_critical`, `_maybe_repair_campaign`, `_maybe_cull_ruins`, `_maybe_abandon_stalled_projects`, `_maybe_relocate_stuck_project`, `_maybe_reorganize_structures`, `_maybe_force_contribution`, `_maybe_start_idle_district_project`, `_maybe_build_funded_project`, `_maybe_start_approved_custom`, `_maybe_retire_blueprint`, `_maybe_amnesty_rejected_blueprints`, `_maybe_skip_sage_review`, `_maybe_amnesty_denied_sage_reviews`, `_maybe_retire_custom_resource`, `_maybe_invention_backstop`, `_maybe_found_district`, `_maybe_welcome_newcomer` |
| within the 150-batch, `TECH_TREE_ENABLED` | 150 | `_maybe_era_transition`, `_maybe_dissolve_council` |
| `DAILY_COUNCIL_ENABLED` | day boundary (`frameTick % DAY_FRAMES == 0`) and deterministic phase gate | `_maybe_convene_daily_council`, `_maybe_advance_daily_council` |
| `CHRONICLE_SAGA_ENABLED` | day boundary (`frameTick % DAY_FRAMES == 0`) | `_maybe_append_daily_saga` — see [Chronicle saga](#chronicle-saga-chronicle_saga_enabled) |
| within the 150-batch, `CULTURE_ENABLED` | 150 | `_maybe_study_at_library` |
| within the 150-batch, (unconditional) | 150 | `_maybe_handle_burials` |
| within the 150-batch, `ECONOMY_ENABLED` | 150 | `_maybe_mint_coin`, `_maybe_fund_project_coin` |
| within the 150-batch, `PATH1_ENABLED` | 150 | `_maybe_found_settlement`, `_path1_industry_benchmark` |
| `PATH1_ENABLED` | 900 | `_tick_wildlife` (Path-1 forest attack pressure — **not** huntable fauna) |
| `PATH1_ENABLED` and `_is_night()` | 30 | `_tick_night_pressure` |
| `STRUCTURE_EFFECTS_ENABLED` | 150 | `_tick_structure_effects` |
| `ECOLOGY_ENABLED` | 600 | `_tick_ecology_regrow` |
| `GOODS_ENABLED` | 900 | `_tick_goods` |
| `GOODS_ENABLED` | 18000 (=`DAY_FRAMES`) | `_tick_shelter` |
| `MEMES_ENABLED` | 90 | `_spread_beliefs_by_proximity` |
| `ALWAYS_ON_MODULES` | `MODULE_PULSE_INTERVAL_S * TICKS_PER_SEC` (45 s; dark default) | `_pulse_piano_modules`: a bounded, non-blocking, event-gated PIANO refresh pulse |
| `BENCHMARKS_ENABLED` | 600, or frame 60 (`FIRST_BENCHMARK_FRAME`) | `_sample_benchmarks` |
| `WILDLIFE_ENABLED` | every tick (with agent move) | `_move_wildlife` |
| `WILDLIFE_ENABLED` | slower cadence (`WILDLIFE_POP_TICK_FRAMES` / migrate check) | `_tick_huntable_wildlife` (spawn/respawn/migrate) |

After the gated systems: every non-incapacitated agent moves (`_move_agent`);
when `WILDLIFE_ENABLED`, `_move_wildlife()` advances fauna the same tick;
`_sage_emergency()` computes an emergency target (see below); message timers
decrement; and, for each non-incapacitated agent not currently a designated
emergency responder, either a reorg task steps, a goal steps, or the agent's
`thinkTimer` reaches 0 and `_schedule_think` is attempted.

When `ALWAYS_ON_MODULES` is enabled, district arrival, inbox delivery, and
hunger/health threshold crossings mark that agent's module context dirty;
action application marks its actor dirty; project/build/rule/role/belief
events and season turns mark the affected village context dirty. The pulse
submits only up to free PIANO-pool slots and never joins a module future
while holding the tick lock.

## Time model

All calendar fields derive from `frameTick`; nothing calendar-shaped is
persisted separately. `_calendar()` (sim_engine/mixin_structures_economy.py:293)
is a pure function of `frameTick`: `year`, `season`, `dayOfSeason`,
`daysPerSeason`, `isNight`, `dayFraction`. `/state` JSON shape unchanged
across the retune.

**Invariants (must hold):**

- `YEAR_FRAMES % DAY_FRAMES === 0` — whole days per in-world year.
- `SEASON_FRAMES = YEAR_FRAMES // 4` — four equal seasons per year.
- `RUIN_CULL_AGE_FRAMES = DAY_FRAMES` — ruin cull age stays ~one sim day.
- `GOODS_TICK_FRAMES = 900` (~30 s) and `LIFECYCLE_TICK_FRAMES = 300` are
  **unchanged** — micro cadences decouple from day length.

**Calendar stretch (2026-07-31, atmosphere Phase 4b):** +33% real-time
lengthening; ratios preserved (24 days/year, 6 days/season;
`daysPerSeason = SEASON_FRAMES // DAY_FRAMES = 6`).

| Constant | Before (frames) | After (frames) | Real-time before | Real-time after |
|---|---|---|---|---|
| `TICKS_PER_SEC` | 30 | 30 | — | — |
| **`DAY_FRAMES`** | **13,500** | **18,000** | **7.5 min** | **10.0 min** |
| **`YEAR_FRAMES`** | **324,000** | **432,000** | **3.0 h** | **4.0 h** |
| **`SEASON_FRAMES`** | **81,000** | **108,000** | **45 min** | **60 min** |
| Days per year | 24 | 24 | — | — |
| Days per season | 6 | 6 | — | — |

**Canonical constants (sim_engine/constants.py:1029 `TICKS_PER_SEC`, :1386 `DAY_FRAMES`, :1450-1451 `YEAR_FRAMES`/`SEASON_FRAMES`):**

- `TICKS_PER_SEC = 30`; `DAY_FRAMES = 18000` — 600 s (10.0 min) per day/night
  cycle at 30/s.
- `YEAR_FRAMES = 432_000` — 24 × `DAY_FRAMES` = 4.0 h (24 day/night cycles).
- `SEASON_FRAMES = YEAR_FRAMES // 4 = 108_000` — 6 × `DAY_FRAMES` = 60 min
  (6 day/night cycles).

**Derived impacts (auto from formulas):**

| Dependent | Before | After |
|---|---|---|
| `AGE_YEARS_PER_TICK` | `300/324000 = 1/1080` | `300/432000 = 1/1440` |
| Sim lifespan 0→90y (wall) | ~11.25 h | ~15.0 h |
| Shelter tick (`_tick_shelter`) | every 7.5 min | every 10 min |
| `RUIN_CULL_AGE_FRAMES` | 13,500 (~7.5 min) | 18,000 (~10 min) |
| Ecology `SEASON_REGROW_MULT` | unchanged per-season mults | same mults, longer seasons |

`AGE_YEARS_PER_TICK = LIFECYCLE_TICK_FRAMES / YEAR_FRAMES`
(sim_engine/constants.py:1577) — one in-world year per `YEAR_FRAMES`; agent
aging, season clock, and GUI calendar share that year. Today: `1/1440` per
lifecycle gate (~10 s at 30/s).

- `NIGHT_FRACTION = 0.25` (sim_engine/constants.py:2009): `_is_night()` is true for the last
  quarter of each `DAY_FRAMES` cycle, but only when `PATH1_ENABLED` — night
  otherwise never triggers.
- `SEASON_REGROW_MULT = {"spring": 2, "summer": 1, "autumn": 1, "winter": 0}`
  (sim_engine/constants.py:1453): ecology regrowth is doubled in spring and fully halted in
  winter (`_tick_ecology_regrow`, applied only when `ECOLOGY_ENABLED`).
- Daily Council: at most once per day boundary (`frameTick % DAY_FRAMES == 0`)
  when `DAILY_COUNCIL_ENABLED`; leaderless lifecycle succession is the emergency
  exception (next `RULES_TICK_FRAMES` pass). Phase machine:
  [09-systems-society.md](09-systems-society.md). Council turns replace the
  selected agent's ordinary think turn — no extra worker-pool slot.
- `_ensure_succession_election()` (and, under `FACTION_SPLIT_ENABLED`,
  `_ensure_settlement_succession_elections()`) runs on this same
  `RULES_TICK_FRAMES` cadence whenever `LIFECYCLE_ENABLED`, so it repairs
  malformed leaderless/succession state continuously from server startup, not
  just once at `restore_state()`. That includes a `state.db` restored with
  more than one living `role=="elder"` agent (possible from a save predating
  the `switch_role`/`change_role` leader-role guard): the repair collapses it
  to a single elder every tick pass it runs, deterministically, until the
  invariant holds — mechanics (survivor/demotion rules) in
  [09-systems-society.md](09-systems-society.md#succession-lifecycle_enabled-governance-slice).

### Chronicle saga (`CHRONICLE_SAGA_ENABLED`) {#chronicle-saga-chronicle_saga_enabled}

When `CHRONICLE_SAGA_ENABLED` (default True — [01](01-architecture.md)), every
day boundary (`frameTick % DAY_FRAMES == 0`) fires a village saga dispatch.
The trigger stays engine-side in `_tick_once` (same gate shape as Daily
Council above) and is gated only by the flag — it **never** early-returns on
an empty chronicle window.

**Under-lock snapshot.** While holding `self.lock`, `_maybe_append_daily_saga()`
(`mixin_governance_culture.py`) gathers the completed day's inputs into one
`saga_context` payload: chronicle-ring entries
for the day window, civilization counters (`births`, `deaths`, daily-council
verdict fields when present), and a bounded dialogue excerpt from
`self.d["read_conversation_window"](start_frame, end_frame)` — the new
server-injected dependency ([12](12-ops.md)) that reads the current run's
`conversation.jsonl` via `session_logger.conversation_path`. This call is
synchronous local file I/O (not a network call) and is safe inside the lock,
unlike `lm_complete`.

**Outside-lock LLM + write-back.** After releasing the lock, the handler
dispatches one `self.d["run_chronicle_saga"](saga_context)` call onto
`self.piano_workers` (shared with PIANO — `PIANO_CONCURRENT_LLM = 2`, never
`self._executor`) via `_run_daily_saga_worker` (`mixin_governance_culture.py`).
The server helper calls `lm_complete` with `MODEL_FAST`/`sim-fast` ([03](03-cognition.md)).
When the call returns, the worker re-acquires the lock and appends the result
to `civilization["saga"]` — the same snapshot → release → dispatch → reacquire →
write shape as `_build_think_payload` / `run_agent_decision` / `apply_decision`
(`mixin_think_job.py`), **not** the single in-lock `lm_complete` in
`_spawn_newborn` (`mixin_lifecycle.py`). In-flight work is tracked on
`self._saga_inflight` (a `concurrent.futures.Future`) for smokes; `_tick_once`
never waits on it.

**Day window.** At day boundary after `frameTick` increments in `_tick_once`,
the completed day is `[frameTick - DAY_FRAMES, frameTick)` (same convention as
Daily Council's `day = frameTick // DAY_FRAMES` at the boundary frame).

**Always-fire / empty-day behavior.** Every day boundary produces a saga entry.
When the day's chronicle window and dialogue excerpt are both empty, the call
still fires with explicit "nothing notable happened" context so the model can
emit a quiet-day line (~"a quiet day passed"). If `lm_complete` itself fails or
times out, the deterministic fallback string `SAGA_FALLBACK_TEXT`
(`constants.py`) is written instead — the tick loop never blocks and the trigger
never silently skips a day (mirrors the birth-persona "never blocks the simulation
on the LLM" discipline in `_spawn_newborn`, except the network call runs outside
the lock here).

**Dialogue cap.** The under-lock snapshot passes at most
`SAGA_DIALOGUE_EXCERPT_CAP = 10` `conversation.jsonl` lines into `saga_context`
(a small multiple of `CHRONICLE_PROMPT_ENTRIES = 3`, not the full day's
transcript). Prompt rendering additionally caps each normalized message at
`SAGA_PROMPT_EXCERPT_CHAR_CAP = 300` characters. The reader receives the line
cap before parsing/materializing matching records, so a chatty day cannot first
allocate an unbounded transcript, and one oversized message cannot expand the
saga prompt without bound.

**Cognition isolation.** Saga text is stored only in `civilization["saga"]`;
it is never injected into agent think payloads ([09](09-systems-society.md#saga-chronicle_saga_enabled)).

## Roster / cold start

`AGENT_DEFS` (sim_engine/helpers.py:46) is 12 hand-written entries (name,
role, personality, color, starting district). `MAX_ROSTER_SIZE = 20` caps
`roster_size` (Sid-parity Phase 6 headroom from 8–12; not a bid at Project
Sid's ~500-agent scale — non-goal, specs/00-overview.md). `SimEngine._select_active_defs(roster_size)`
clamps to `[1, MAX_ROSTER_SIZE]` and resolves the active def list:
- `roster_size <= len(AGENT_DEFS)` (today's 8-12 default/range): unchanged
  from before Phase 6 — `ROSTER` (the 8 default names) fills first, then
  remaining `AGENT_DEFS` entries in def order, with Sage force-included if
  dropped. `roster_size == len(AGENT_DEFS)` returns `AGENT_DEFS` verbatim.
- `roster_size > len(AGENT_DEFS)`: all 12 hand-written defs plus
  `_generated_agent_defs(roster_size - len(AGENT_DEFS))`. Generation is
  deterministic: names/personalities cycle fixed pools (`_GENERATED_AGENT_NAMES`,
  `_GENERATED_AGENT_PERSONALITIES`); role/district rotate across 11 non-elder
  `roles.json` seed roles (one per role before repeat). Zone copied from the
  hand-written def sharing that role. Built by `_make_agents` like hand-written
  agents; indistinguishable to other systems except pool-drawn flavor text.
  `civilization["basePopulation"]` reflects full `roster_size` (clamped to
  `MAX_ROSTER_SIZE`, not `len(AGENT_DEFS)`) for Structure-Effects house cap
  (specs/08) above 12 agents.
- `_maybe_welcome_newcomer` (sim_engine/mixin_backstops.py:653, 150-tick batch)
  grows villages that never cold-started above 12: next unused `AGENT_DEFS`
  entry first, then `_generated_agent_defs(MAX_ROSTER_SIZE - len(AGENT_DEFS))`
  (same pool as `roster_size > len(AGENT_DEFS)` cold start) — can reach
  `MAX_ROSTER_SIZE`. Deterministic `_generated_agent_defs` pool, not
  `_next_agent_slot`'s random `Villager{id}` births; slot index looks identical
  whether the village started large or grew via housing.

## Think scheduling

Each agent gets a staggered `thinkInterval` at construction:
`thinkInterval = 360 + i*60` for the i-th agent, overridden to `240` for the
elder role (sim_engine/core.py:376-378); `thinkTimer` starts at `i*30` so agents
don't all think on the same frame. `_schedule_think` (sim_engine/mixin_think_job.py:1417) only
actually dispatches a job if: the agent isn't already in `self._inflight`,
`len(self._inflight) < MAX_CONCURRENT_LLM` (3), the global LLM cooldown has
expired, and at least `LLM_MIN_GAP_MS = 250` ms have passed since the last
dispatch. If any of these block it, the caller retries after
`THINK_RETRY_FRAMES = 15` frames (0.5 s) instead of waiting a full interval.
`self._inflight` is a set of agent names with a job in flight; entries are added
on dispatch and discarded in the job's `finally` block (sim_engine/mixin_think_job.py:1409-1415, in `_think_job`).

**Dispatch fairness (Phase 6).** `MAX_CONCURRENT_LLM`/`LLM_MIN_GAP_MS`
unchanged (global throughput cap); Phase 6 fixes ordering under contention.
Agents with `thinkTimer` at 0 (not mid-goal/reorg/emergency-response) go into
`think_ready`, sorted by `lastThinkFrame` ascending (most overdue first) before
`_schedule_think`. `lastThinkFrame` updates only on successful dispatch;
failed attempts keep front-of-line priority. Without this, rosters larger than
`MAX_CONCURRENT_LLM` could starve late-indexed agents under sustained
contention.

### Determinism pinning (`DETERMINISM_PINNING`, Phase A1)

Headless-harness only (Emergence Breakthroughs F5). Default **off**; opt in via
env `SIM_DETERMINISM_PINNING=1` or `scripts/determinism_proof.py --pin`. When
on:

- `SimEngine.__init__` calls `random.seed(DETERMINISM_SEED)` (env
  `SIM_DETERMINISM_SEED`, default `42`) so each cold start gets an isolated RNG
  stream regardless of prior harness runs in the same process.
- `_schedule_think` defers jobs to `_pin_think_queue` instead of
  `ThreadPoolExecutor.submit`; `_tick_once` drains that queue synchronously in
  sorted agent-name order via `_run_pin_think_queue`.
- LLM dispatch gap / orphan cooldown use `frameTick`-based frames
  (`_pin_last_dispatch_frame`, `_pin_cooldown_until_frame`) instead of
  `time.time()` / `LLM_MIN_GAP_MS` wall clock.

Flag off preserves the live 24/7 path exactly (async executor + wall-clock
gaps). Not echoed to `/state` `config.flags`.

## Proximity scans (district-bucketed, Phase 6)

`_get_nearby_agents`/`_get_nearby_detailed` (both `NEARBY_RADIUS = 80`) back
`nearby_agents`/`nearby_agents_detailed` think-payload fields — O(n²) per
think round if each call scanned `self.agents`. Both route through
`_nearby_candidate_pool(agent)`:

- `_rebuild_district_buckets()` groups `self.agents` by `currentDistrict`
  into `self._district_agent_buckets`, rebuilt lazily once per `frameTick`
  (cached by frame stamp, not per-call).
- `_district_adjacency_for(did)` returns district ids whose bounds (expanded
  by `NEARBY_RADIUS`) overlap `did` (via `_rects_overlap`). Cached; invalidated
  when district count changes. Starter districts can be closer than
  `NEARBY_RADIUS` (e.g. `village_core`/`market` ~70px apart), so same-district-only
  buckets would drop cross-border neighbors. Candidate pool = own bucket plus
  adjacent buckets — equivalent to flat scan for hand-placed positions
  (`scripts/_sid_parity_smoke/scale_headroom.py::test_district_bucket_matches_flat_scan`),
  smaller at roster 20.
- `_find_nearest_agent` (reactive `move_to_agent` fallback only) stays a flat
  scan — no radius bound; must find global nearest across the map.

## Pause / resume / reset

- `pause()` / `resume()` (sim_engine/mixin_snapshot.py:40, 46) just flip `self.paused` under
  the lock; `_tick_once` early-returns while paused, freezing `frameTick`.
- `reset(roster_size=None)` (sim_engine/mixin_snapshot.py:52) rebuilds the world
  (`_reset_world`), clears the in-process memory store, then deletes and
  immediately rewrites `state.db` via `clear_state()` + `save_state()` so a
  reset persists cleanly. The HTTP route `POST /control/reset` (server.py)
  calls this only after a successful `SIM_RESET_PASSWORD` check — see
  [04-http-api.md](04-http-api.md).

## Sage emergency

`_sage_emergency()` (sim_engine/mixin_world_state.py:800) returns a target agent needing rescue,
or `None`, only when `SURVIVAL_ENABLED`. It finds the living elder (`role ==
"elder"` and not dead); if the elder is not incapacitated and
`health >= SAGE_CRITICAL_HEALTH` (30, sim_engine/constants.py:1156), there's no emergency.
Otherwise the target is the healer (if the healer is also incapacitated) or the
elder itself. `_sage_responders(target)` (sim_engine/mixin_world_state.py:819) picks the
non-incapacitated healer (if not the target) plus the nearest other
non-incapacitated agent. Each tick, a designated responder skips normal
thinking/goal logic entirely and instead steps `_rush_to_heal` (sim_engine/mixin_world_state.py:835)
every `GOAL_STEP_FRAMES` — moving toward the target, then issuing a hardcoded
`heal_agent` decision once within 80 px.

**In-flight LLM decision discard:** if a think job's LLM response returns
(sim_engine/mixin_think_job.py:1314-1320) while this agent became a Sage
emergency responder, the decision is discarded and `_rush_to_heal` runs —
emergency wins over stale in-flight decisions.

**Raiders/contagion interaction (`RAIDERS_CONTAGION_ENABLED`).** The elder
(`role == "elder"`) is **never** a valid raid-contact victim or contagion-spread
target — the same outright exclusion `confront_agent` already applies
(`mixin_decisions.py:675-676` rejects `target["role"] == "elder"`). Raid
target-selection for contact health damage and contagion proximity-spread
candidate pools must skip any agent with `role == "elder"` before any RNG or
proximity math runs. This is **stricter** than `_sage_emergency()`'s reactive
health-threshold rescue (`SAGE_CRITICAL_HEALTH = 30`): the elder cannot even be
the mechanism's initial victim, so a raid/contagion event and `_rush_to_heal`
never race each other on elder selection. Guards, healers, and all other
non-elder roles remain fully eligible. Structure damage from a raid is
unaffected — if a structure the elder is inside or near is targeted, its
`condition` still degrades; only the agent-targeting half excludes the elder.

`_sage_emergency()` / `_rush_to_heal` are **unchanged** for all other health
events (starvation collapse, wildlife pressure, `confront_agent` incapacitation,
etc.): they still trigger when the elder or healer crosses the existing
critical-health thresholds. Raid contact damage and contagion per-gate health
loss apply via `agent["health"] = max(0, agent["health"] - X)` — the same clamp
`_update_survival` and `confront_agent` use — and may lead to incapacitation
through `_update_survival`'s existing collapse floor; they **never** call
`_agent_dies` under this plan's default design ([10-path1.md](10-path1.md),
[06-agents.md](06-agents.md)).

## Persistence

World state is persisted to a SQLite database at `DB_PATH` (`<module dir>/
state.db`), replacing the earlier monolithic `state.json` file. `_serialize_state()`
(sim_engine/mixin_persistence.py:50) still builds the save payload under the lock, with the
same shape as before: top-level keys `version` (`STATE_VERSION = 3`,
sim_engine/persistence.py:37; restore also accepts `2` once for embedded-sprite migration),
`frameTick`, `savedAt` (UTC ISO timestamp), `roster_size`,
`civilization`, `agents`, `memory`, `council_transcript` (sets are serialized as sorted arrays,
`isThinking` is dropped, memory rows are vec-stripped for storage and
re-embedded on import). Structure `sprite` grids are **not** stored inside
`civilization["structures"]` in the DB — they live in the `structure_sprites`
table and are merged back onto structures in memory on `restore_state()`.
Saves upsert only dirty sprite rows (`_persist_dirty_structure_sprites`); a
`structureSpritesFingerprint` field in the serialized payload (not written to
`meta`) keeps autosave hashing correct when only sprites change.

`_connect_db(path)` opens a SQLite connection in WAL mode
(`synchronous=NORMAL`) and idempotently runs the schema DDL. The schema has
four tables: `meta(key, value)` (one row each for `version`, `frameTick`,
`savedAt`, `roster_size`); `civ(key, value)` (one row per top-level
`civilization` key, value JSON-encoded — structure instances omit embedded
`sprite` grids); `agents(name PK, ord, data)` (one row
per agent, `data` JSON-encoded, `ord` preserving roster order on load);
`memory(rowid_pk, id, agent, text, salience, kind, tier, frame_tick, ts)`;
`council_transcript(rowid_pk, meeting_id, who, type, text, feeling, frame_tick,
ts)`; and `structure_sprites(structure_id PK, sprite_json, updated_frame)`
(one row per structure that has a custom/stored sprite grid). The council
transcript table is the full human/audit record for Daily Council events, not
prompt context.

`_write_state_db(path, payload)` full-rewrites on every save that reaches
disk: upserts `meta`, deletes/re-inserts `civ`/`agents`/`memory`/
`council_transcript`, upserts dirty `structure_sprites` (deletes removed ids),
single transaction, then `wal_checkpoint`. `save_state(force=False)` serializes
under the lock, hashes via `_state_content_hash()` (excludes `savedAt`), updates
`_last_save_considered_at`, and skips rewrite when hash matches
`_last_saved_hash` (typical paused/idle). Successful write updates
`_last_saved_hash`. `force=True` always rewrites — graceful shutdown, `reset()`,
God checkpoints. Serialization uses `_json_safe_copy()` (set→sorted-list), not
`json.loads(json.dumps(...))`. Write outside lock via `_write_state_db`; never
raises; single-transaction commit replaces tmp-file rename. `SimSaver` calls
`save_state()` (default `force=False`) every `AUTOSAVE_SECONDS = 10` s.
`atexit`/signal handlers in server.py flush `save_state(force=True)` on
shutdown.

`_read_state_db(path)` checks file exists, connects, returns payload dict like
`_serialize_state()`, or `None` if missing or no `meta.version`.
`restore_state()` (sim_engine/mixin_persistence.py:163) accepts v3 and v2 once
(v2 sprites in `structures` merge to memory, persist-dirty, next save splits to
`structure_sprites` as v3). v1→v2 migration removed. `setdefault`/flag-gated
backfill for post-v2 fields still runs on every restore (forward-compat).

**Home settlement back-compat:** a save predating `homeSettlementId`
(agent field — see [06-agents.md](06-agents.md#home-settlement-homesettlementid))
gets `a.setdefault("homeSettlementId", None)` in the per-agent backfill
pass, same discipline as other `setdefault`-only fields; unlike most of
them, resolving a real value requires `self.civilization` (via
`_settlement_for_agent`), which isn't assigned yet at that point in
`restore_state()`. A second pass runs after `self.civilization`/`self.agents`
are set, resolving every still-`None` value to that agent's current
physical settlement (`_settlement_for_agent(a)`) — a one-time snapshot, not
a recurring recompute.

**Lifecycle lineage back-compat (`LIFECYCLE_ENABLED`):** inside the existing
`if LIFECYCLE_ENABLED:` restore block that already runs
`a.setdefault("parents", None)` and `a.setdefault("deathFrame", None)`
(`mixin_persistence.py:540-544`), Phase 1 adds `setdefault`-only back-compat for
`children`, `inheritedTestament`, and `inheritedBeliefs` — same discipline as
`parents`/`deathFrame`, no schema bump. A restored save with `parents` set but
no `children` key must not raise and must back-fill `children == []`.
After all agents load, restore also reconstructs each known parent's inverse
`children` link from the child's persisted `parents` names, without
duplicating existing entries;
likewise `inheritedTestament` and `inheritedBeliefs` default to `[]` when
absent. Every reader (`_heirs_of()`, viewer projection) must use
`agent.get("children") or []` defensively even after back-compat lands, matching
this codebase's existing `.get(... ) or []` / `.get(..., {})` read style.

**Inland-founded beach migration (coastal pairs):** after agents/civilization
rehydrate, `_revert_inland_founded_beaches()` removes founded `beach_N` /
orphan `ocean_N` districts that lack an edge-adjacent coastal pair (starter
`beach`/`ocean` exempt), unclaims the frontier plot, cleans structures/agents/
wildlife/road gates, then validates districts — see
[05-world.md](05-world.md#restore-migration-inland-founded-beaches).

**Faction split storage migration (`FACTION_SPLIT_ENABLED`):** when the flag is on at load,
`restore_state()` wraps legacy flat `rules` / `pendingRules` / `constitution` /
compiled governance maps and `beliefRegistry` / `memeTexts` under the primary
`"home"` settlement id before `_rebuild_settlement_governance` runs per
bucket. Flag-off restore skips this entirely — see
[09-systems-society.md](09-systems-society.md#faction_split_enabled).

Daily Council transcript persistence mirrors `memory`: authoritative in-RAM
`council_transcript_rows`; append on live event; serialization exports full
list; DB save deletes/re-inserts atomically. Restore rehydrates. At adjourn,
retention keeps newest `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS = 30`
distinct `meeting_id` values. Audit table never in LLM prompts; bounded digest
in `civ` blob is prompt-facing ([03](03-cognition.md)).

**Chronicle saga ring (`CHRONICLE_SAGA_ENABLED`).** `civilization["saga"]` is
a capped ring of daily dispatch records `{text, frame, dayIndex}` (sibling to
`civilization["chronicle"]`, not prompt-facing — [09](09-systems-society.md#saga-chronicle_saga_enabled)).
Ring cap: `SAGA_CAP = 100`. Day-boundary append: `_maybe_append_daily_saga()`
(`mixin_governance_culture.py`), gated from `_tick_once` (`mixin_think_job.py`).
`restore_state()` applies `setdefault("saga", [])` on the civilization blob so
older saves without the field load without raising — same setdefault-only
back-compat discipline as the other post-v2 fields above (line 300). The ring
is projected to `/state` when the flag is on (implementation in
`mixin_snapshot.py`, Phase 1).

`clear_state()` deletes `state.db` plus `state.db-wal`/`state.db-shm`.

**Spectator predictions (`predictions.json`).** Unlike
`simulation/memory_store.json`, whose entries mirror into the `state.db`
`memory` table and are merged back into the world on `restore_state()` /
`import_entries()`, `simulation/predictions.json` is **not** part of full-state
save/restore. No `persistence.py` function opens it; `save_state()` /
`restore_state()` never read or write prediction rows; predictions never reach
`civilization` / `agents` or `_build_think_payload()`. Only the three
`/predictions/*` route handlers (see [04-http-api.md](04-http-api.md#prediction-market-routes))
touch this file. Each row carries the ballot frame and, once resolved, the
optional `resolved_frame_tick`; these fields remain prediction-store metadata
and never enter the engine snapshot or agent prompts.

## Sovereign God mode (Phase 2 — secure kernel)

`GOD_MODE_ENABLED` (sim_engine/constants.py:644, env `SIM_GOD_MODE`, read at
import — [01](01-architecture.md)) gates an optional control plane. Each entry
point re-checks the flag and no-ops with `{"ok": False, "reason": "god mode
disabled"}` when off; HTTP token check lives in server.py
([04-http-api.md](04-http-api.md)).

**State shape.** `civilization["godState"]` persists with `civilization` (no
serializer change); always exists, flag on or off:

```json
{
  "version": 3, "intervened": false, "nextInterventionSeq": 1,
  "providence": null, "privateOmens": {}, "activeEvents": [],
  "recentInterventions": [], "recentDivineResponses": [],
  "whisperCampaigns": {}, "agentSampling": {}, "contextMasks": {},
  "decisionGates": {}, "burningBush": {}, "anointments": {},
  "identityForges": {}, "architectZones": [], "checkpoints": [],
  "decisionDigests": [], "dejaVuReplays": {},
  "crowdCompulsions": {}, "dreamBroadcasts": {}
}
```

`GOD_STATE_VERSION` is `3` (Divine Console Phase 8: bounded `decisionDigests`
ring + live `dejaVuReplays`; Phase 9 placeholder maps `crowdCompulsions` /
`dreamBroadcasts` default empty).
Private maps (`whisperCampaigns`, `agentSampling`, `contextMasks`,
`decisionDigests`, `dejaVuReplays`, `crowdCompulsions`, `dreamBroadcasts`,
etc.) never appear in `snapshot()["god"]` — only the public allowlist fields
below.

`_default_god_state()` builds this; `_normalize_god_state(raw)` is the
restore-time normalizer (setdefault-only back-compat). Old saves without
`godState` or with malformed nested fields rehydrate to conservative defaults.
`reset()` seeds fresh god state via `_reset_world`. `recentRequests` (idempotency
store) is not persisted — see below.

**Preview cache.** `self._god_preview_cache` (previewId → record) is in-memory
only, bounded to `GOD_PREVIEW_CACHE_MAX = 32`, TTL `GOD_PREVIEW_TTL_SECONDS =
60` wall-clock seconds. `god_preview(envelope)` validates/normalizes
`{kind, payload, expectedFrame}`, computes SHA-256 digest, inserts
`previewId` → `{normalizedCommand, commandDigest, previewFrame, fingerprint}`.
Cleared on `reset()` and `restore_state()`.

**Idempotency store.** `self._god_requests` (requestId → outcome) is in-memory
only, bounded to `GOD_REQUEST_CACHE_MAX = 100`, cleared on `reset()`/
`restore_state()`. `god_apply(preview_id, request_id)`: resolves preview;
idempotency store first (exact `requestId` replay returns stored response;
same `requestId` with different preview/digest = conflict, applies nothing);
fresh `requestId` revalidates against current state (validator, digest,
fingerprint), applies under lock, stores response, consumes preview (single-use).

**Command catalog (Phase 2).** `kind == "proclamation"` is applyable:
`{"kind": "proclamation", "payload": {"text": str, "durationFrames": int?, "presentation": "soft"|"thunder"?}}`.
Optional `presentation` (`"soft"` \| `"thunder"`) is cosmetic only: validated in
`_validate_god_envelope`, stored on intervention/providence records, copied to
chronicle as `presentation` when not default `"soft"`. Does not change cognition
prompts, activity wording, or `conversationLog` — viewer banner/chronicle CSS
and `divine.jsonl` audit only. Omitted/`"soft"` equivalent; only `"thunder"` in
canonical payload. No `GOD_STATE_VERSION` bump — setdefault-safe on older saves.
`text` passes `_normalize_divine_text` (Unicode NFC; rejects NUL/C0/C1 controls
except space, embedded newlines; 240-char and 600-UTF-8-byte cap — byte cap
load-bearing, tighter than `4 * 240`). **Proclamation auto-converts to
providence:** applies via `_god_apply_providence` (single `godState["providence"]`
slot, default/clamped `durationFrames`, disclose-then-replace fingerprint,
`revoke_guidance`/`god_cancel`, expiry below). Activity/`conversationLog`/
chronicle attribute `kind="divine_proclamation"`, `source="divine"`; cognition
uses providence record. `story_event` (Phase 5) validates to clean rejection;
unrecognized kind rejected. `god_cancel(target_id)` is plumbing only — nothing
to cancel; `revoke_guidance` (Phase 3) cancels providence/private omens, returns
`{"ok": True, "cancelled": False, "reason": "nothing to cancel"}`. Phase 4 adds
three applyable kinds (below); neither `god_cancel` nor `revoke_guidance` touches
them — see "Sovereign God mode (Phase 4 — bounded immediate miracles)".

**Expiry.** `_expire_divine_effects()` bounded scan (cap `GOD_ACTIVE_EVENTS_CAP =
8` for `activeEvents`) marks expired `activeEvents`, `providence`, and
`privateOmens` as `expired` (or `restore-closed` from `restore_state()`),
leaving closed entries untouched. Called every tick after `frameTick` advances
(before other consumers) and once after restore. Phase 2 never populates
`activeEvents` (cheap no-op until Phase 5); providence/omen expiry live from
Phase 3.

**Benchmarks.** When `GOD_MODE_ENABLED`, `_sample_benchmarks()` adds
`lastBenchmarks["intervened"]` and logs a `god_interventions` metric with
`intervened`/`active_effects`/`rejected_commands` detail — see
[12-ops.md](12-ops.md).

## Sovereign God mode (Phase 3 — Voice binding guidance)

Three more catalog kinds are applyable, and `god_sight` gains the per-agent
omen status field and `recentDivineResponses` adherence log this section
describes. Cognition-side rendering (binding prompt lines, `divine_response`,
the elder-directive separation) is [03](03-cognition.md).

**`providence`** — `{"text": str, "durationFrames": int?, "presentation": "soft"|"thunder"?}`.
One active public binding guidance line in `civilization["godState"]["providence"]`:
`{id, text, createdFrame, expiresFrame, visibility: "public", ackedAgentIds?: {},
skipCounts?: {}, presentation?: "thunder"}`. `skipCounts` maps `str(agentId)` to
consecutive synthetic `divine_response` turns against this id (Voice-adherence cap
below). `presentation` cosmetic-only (chronicle/banner; cognition unchanged).
`durationFrames` optional (default `GOD_GUIDANCE_DEFAULT_DURATION_FRAMES = 5400`,
~3 min, mirrors `DIRECTIVE_TTL_FRAMES`); clamped to
`GOD_GUIDANCE_MIN_DURATION_FRAMES..GOD_GUIDANCE_MAX_DURATION_FRAMES` (`300..54000`,
~10s–30min), not rejected. Writes activity/`conversationLog`/chronicle
(`source="divine"`). Replace allowed via disclose-then-replace:
`_god_target_fingerprint` records `{"outgoingId": ...}` at preview;
`_god_check_fingerprint` at apply — mismatch rejects "providence changed since
preview". Successful replace closes outgoing via `_close_providence("replaced")`
before writing new.

**`private_omen`** — `{"targetId": int, "text": str, "durationFrames": int?}`.
`targetId` must resolve to a living agent; unknown/deceased rejected before text
normalization. Stored in `godState["privateOmens"]`, keyed by `str(agent["id"])`,
one per agent: `{id, targetId, targetName, text, createdFrame, expiresFrame,
memoryWritten, acked?: bool, skipCount?: int}`. `targetName` display-only.
Binding — same `divine_response` contract as providence ([03](03-cognition.md)).
`skipCount`: consecutive synthetic turns (Voice-adherence cap). Never touches
public activity/`conversationLog`/chronicle. Replace: disclose-then-replace
fingerprint, keyed per-target.

**`whisper_campaign`** — `{"theme": str, "durationFrames": int?,
"whispers": [{targetId, text}, ...]}` (max `GOD_WHISPER_CAMPAIGN_MAX_TARGETS =
12`). Batch: parent id in `whisperCampaigns` linking
`targets: {str(agentId): omenId}` plus per-target private omens via
`_god_apply_private_omen`. Theme/whisper texts private (not snapshot allowlist or
public logs). `god_cancel(campaignId)` revokes linked active omens; omen
expiry/replacement finalizes campaign when none remain.

**`agent_sampling`** — `{"targetId": int, "model": "sim-smart"|"sim-fast",
"temperature": 0.0–1.5, "top_p"?, "top_k"?, "min_p"?, "durationFrames"?}`.
Living `targetId` required. `model` defaults `"sim-smart"`. Sampling finite and
in range (`top_p`/`min_p` 0.0–1.0, `top_k` 0–200). `durationFrames` optional —
omit = until `revoke_agent_sampling`/`god_cancel(interventionId)`; supplied =
clamped like other guidance. Stored in `agentSampling`, keyed `str(agentId)`:
`{id, targetId, model, temperature, top_p?, top_k?, min_p?, createdFrame,
expiresFrame?, sourceId}` (one per agent; replace semantics). Private; not
snapshot allowlist; `recentInterventions` `"public": false`. At most one living
agent with active `sim-fast` override (PIANO pool contention — [03](03-cognition.md));
replacing own existing `sim-fast` override exempt. Expiry via
`_expire_divine_effects` when `expiresFrame` reached.

**`revoke_agent_sampling`** — `{"targetId": int}`. Clears the active sampling
override for that agent if present; rejects when none is active. Also
cancellable by `god_cancel(interventionId)` on the override's `id`.

**`memory_insert`** — `{"targetId": int, "text": str, "salience": 0.0–1.0,
"kind"?}`. Living `targetId`. `text` via `_normalize_divine_text`. `kind`
defaults `"divine_false_memory"`. `_god_memory_insert` writes `MemoryStore` (when
present) and mirrors to `memory.working`/`memory.shortTerm` with salience
eviction like `_push_memory`. Private, irreversible; not snapshot allowlist or
public logs. `recentInterventions` `"public": false`; outcomes expose counts only.

**`memory_delete`** — `{"targetId": int, "keyword"?, "frameFrom"?, "frameTo"?,
"kinds"?}`. Living `targetId`. At least one of `keyword` (case-insensitive
substring), `frameFrom`/`frameTo` (inclusive `frame_tick`), or `kinds` required.
`MemoryStore.delete_where` plus local `memory.working`/`memory.shortTerm` purge
when `keyword` set (local tiers lack kind/frame metadata). Private,
irreversible; same visibility as `memory_insert`. Outcomes report `deletedCount`
only.

**`belief_plant`** — `{"targetId": int, "beliefId"?, "text"?, "plantInMemeTexts":
bool, "salience"?}`. Living `targetId`. At least one of `beliefId` or `text`.
Text-only: registers divine-authored belief (`authoredBy: "divine"`) with
`divine_<hash>` id. `beliefId` must exist in `beliefRegistry`/seed `MEMES`.
`plantInMemeTexts: true` stores tenet in `memeTexts[beliefId]`. Adds to
`agent["beliefs"]`; private memory via `_god_memory_insert`
(`kind="divine_belief"`). Private, irreversible; outcomes expose `beliefId` and
counts, not tenet text.

**`context_mask`** — `{"targetId": int, "mode": "dream"|"blue_pill"|"red_pill"|
"whisper_chain", "durationFrames"?, "dreamSnapshot"?, "forgedConversations"?}`.
Living `targetId`. `durationFrames` clamped like other guidance (default when
omitted). Stored in `contextMasks`, keyed `str(agentId)`:
`{id, targetId, mode, createdFrame, expiresFrame, dreamSnapshot?,
forgedConversations?}` — one per agent; replace with preview fingerprint
`outgoingId`. Private, cancellable; not snapshot allowlist;
`recentInterventions` `"public": false`. Cancel via `god_cancel(interventionId)`;
expiry via `_expire_divine_effects`. Modes mutate think payload only (after true
snapshot) — never `conversationLog` or world state:

| Mode | Effect |
|---|---|
| `dream` | Replace allowlisted think-payload keys from `dreamSnapshot` |
| `blue_pill` | Strip `divine_public_line`, `divine_private_line`, `divine_public_event_line`, and divine-sourced `recent_conversations` slices |
| `red_pill` | Inject `divine_simulation_truth_line` (flags, agent identity, intervened status — never other agents' private omens) |
| `whisper_chain` | Replace `recent_conversations` with `forgedConversations` text |

`dreamSnapshot` keys are allowlisted (`nearby_agents`, `resources`,
`weather_line`, `recent_conversations`, `district_stocks`, `nearby_wildlife`,
`nearby_wildlife_line`, `hunger`, `health`); unknown keys are rejected at
preview. `forgedConversations` is a bounded list of `{from, to, message}`
(normalized via `_normalize_divine_text` per message).

**Decision gate (Divine Matrix Phase 5).** `decisionGates`, keyed `str(agentId)` —
private, not snapshot allowlist. One per agent (replace + preview fingerprint
`outgoingId`). Modes:

| Mode | Record shape | Think-path behavior |
|---|---|---|
| `compulsion` | `{mode, pinnedDecision, expiresFrame?, remainingTurns?, id, ...}` | After LLM (or fallback), `_apply_gated_decision` replaces the candidate with `pinnedDecision` (validated via `normalize_decision` at preview/apply). Decrements `remainingTurns` when set; expires when turns or `expiresFrame` elapse. |
| `veto` | `{mode, armed: true, status, pendingDecision?, holdExpiresFrame?, id, ...}` | When armed, stashes the LLM candidate in `pendingDecision`, sets `agent["divineHold"]=True`, and does **not** apply until `decision_veto_resolve` (`approve`/`reject`/`rewrite`). Non-blocking for the tick thread. Concurrent holds capped at `GOD_VETO_HOLD_CAP = 3`; hold timeout → reject + `rest`. |
| `possession` | `{mode, bypassLlm: true, pinnedDecision \| queue, queueIndex?, id, ...}` | Pre-LLM short-circuit in `_think_job`: skips `llm_decide`, applies pin/queue under lock. Post-LLM gate still forces pin if LLM somehow ran. |

**Sage emergency bypass:** `_rush_to_heal` and Sage in-flight discard in
`_think_job` call `apply_decision` directly — not `_apply_gated_decision` —
so survival heals bypass compulsion/veto/possession.

Kinds: `decision_compulsion`, `decision_veto_arm`, `decision_veto_resolve`,
`agent_possession`, `revoke_decision_gate`; cancel via `god_cancel` on gate
`id`. Applied compelled/possessed world actions attribute
`source="divine"` via `_push_communication` / `_push_chronicle` (activity
summary from `apply_decision` remains; divine lines are additive). Agent field
`divineHold` pauses movement and think scheduling while a veto hold is active.

**Burning Bush + Merovingian Bargain (Divine Matrix Phase 6).** `burningBush`,
keyed `str(agentId)` — private, not snapshot allowlist. Per-agent:
`{id, targetId, thread: [{role: god|agent, text, frame}], bargain?, status,
createdFrame}`. Thread cap `GOD_BURNING_BUSH_THREAD_MAX = 20`;
`GOD_BURNING_BUSH_PROMPT_MAX_CHARS` in prompt injection.

Kinds: `burning_bush_message` (append God line to thread),
`burning_bush_close` (end session), `merovingian_bargain` (attach open bargain
with allowlisted predicates), `bargain_settle` (manual success/failure).

Bargain predicates (`GOD_BARGAIN_PREDICATES`): `agent_has_resource`
(`resourceId`, `amount?`), `structure_built` (`structureType`),
`frame_reached` (`frame`), `agent_health_below` (`threshold`). Reward/punish
primitives reuse `grant_resource` / `agent_vitals` only
(`GOD_BARGAIN_PRIMITIVE_KINDS`). `_tick_divine_bargains()` runs under lock
from `_expire_divine_effects` each tick: success predicate → reward;
`failurePredicate` (optional) → punish; `expiresFrame` without success →
failure punish. Grant rewards produce public activity (divine attribution);
bush thread text and bargain terms stay private (`public: false` audit).

Agent replies append to `thread` from `apply_decision` talk/reasoning via
`_capture_burning_bush_reply`. Sight: `{active, messageCount, bargainActive,
expiresFrame?}` — never thread text.

**Anointed (Divine Matrix Phase 7).** `anointments`, keyed `str(agentId)` —
private, not snapshot allowlist. `{id, targetId, destinyText, stigmataTags:
[str], oracleHints: [{text, revealFrame}], createdFrame, expiresFrame}`.
`destinyText` and due oracle hints (`revealFrame <= frameTick`) inject via
`divine_anointment_line` (target only). `stigmataTags` in `_get_nearby_detailed`
for other agents' prompts (`format_nearby_agents` suffix `signs: …`) — not
`/state` public fields.

Kinds: `anoint` (replace semantics per agent), `revoke_anoint`. Caps:
`GOD_ANOINT_STIGMATA_MAX`, `GOD_ANOINT_ORACLE_HINTS_MAX`,
`GOD_ANOINT_PROMPT_MAX_CHARS`. Expiry via `_expire_divine_effects`; cancel via
`god_cancel` on intervention `id` or `revoke_anoint`. Audit `public: false`
for destiny/oracle text. Sight: `{active, tagCount, nextOracleFrame?,
expiresFrame}` — never destiny or oracle secret text.

**Identity Forge (Divine Matrix Phase 8).** `identityForges`, keyed
`str(agentId)` — private, not snapshot allowlist. `{id, targetId, snapshot:
{persona, personality, role}, baseline?, copyFromId?, rate?, progress,
createdFrame, expiresFrame?}`. Kinds: `identity_edit` (role must exist in
`roles.json`; timed edits restore on expiry/cancel via `_close_identity_forge`),
`identity_copy_overwrite` (`sourceId`, `ratePerThink` 0.0–1.0, optional
`syncMemories` plants up to `GOD_IDENTITY_COPY_MEMORIES_MAX` source
working/shortTerm lines — not full clone), `identity_forge_cancel`. Copy blend in `_advance_identity_forge_on_think`
after think cycle (`_finish_think_identity_forge`). Elder role swaps warn in
preview but allowed. Permanent edits (no `durationFrames`) consequential; timed
edits and copy cancellable. Audit `public: false`. Sight: `{active, progress,
rate?, copyFromId?, expiresFrame}` — no full persona dump.

**Architect Zones (Divine Matrix Phase 9).** `architectZones` (list) — omitted
from snapshot god allowlist; Sight exposes `{id, kind, districtId?, cellCount,
expiresFrame, holdCount?}` only (never `keyId`, `revertSnapshot`, limbo prior
coords). Per-zone: `{id, kind: paint|door|limbo, districtId?, cells,
paintTerrain?, keyId?, holdAgentIds[], grantKeyAgentIds?, reversible?,
revertSnapshot?, limboHolds?, expiresFrame, status}`.

Kinds: `architect_zone`, `architect_zone_cancel`, `architect_release_hold`.
Constants: `GOD_LIMBO_STATION = (140, 500)` (ocean district Trainman platform),
`GOD_ARCHITECT_ZONE_MAX_CELLS = 64`, `GOD_ARCHITECT_ZONES_MAX = 16`,
`GOD_ARCHITECT_PAINT_TERRAINS` (`soil`, `rock`, `grove`, `water`, `sand`).

| Kind | Mechanics | Audit `public` |
|---|---|---|
| `paint` | Writes Path1 `district["terrain"]` cells; `revertSnapshot` on cancel/expiry when `reversible: true` (default) | `true` (world-visible terrain) |
| `door` | `_architect_door_blocks_move` in `_move_agent` — agents without matching `agent["godKeys"]` tag bounce in place | `false` |
| `limbo` | Sets `divineHold=True`, parks agents at `GOD_LIMBO_STATION`, stores prior pose in `architectLimbo` | `false` |

`grantKeyAgentIds` on door apply adds `keyId` to `agent["godKeys"]` (god-granted
tags, not craft items). `architect_release_hold` restores pose and clears
`divineHold` when no decision-gate veto hold remains. Expiry/cancel via
`_close_architect_zone` (shared with `god_cancel` on zone `id`).

**Reload / Déjà Vu checkpoints (Divine Matrix Phase 10).** Kinds:
`checkpoint_create`, `checkpoint_restore`. Metadata in `checkpoints` (cap
`GOD_CHECKPOINT_MAX = 5`): `{id, label, frameTick, path, createdAt}` with
`path` relative (`backup/god-checkpoints/<id>`). Disk:
`simulation/backup/god-checkpoints/<id>/`: `state.db` + `memory_store.json`
(WAL truncated on create via `save_state` + `PRAGMA wal_checkpoint(TRUNCATE)`). Injectable root: `GOD_CHECKPOINT_ROOT` or per-engine
`god_checkpoint_root` (smokes use temp dirs). At cap, preview rejects unless
`replaceOldest: true`. Restore: pause-safe copy to live `DB_PATH` + memory store,
`restore_state()`, clear preview/idempotency caches, resume. Audit `public:
true`. Sight lists summaries (no absolute paths); not in `/state` god allowlist.

**Déjà Vu replay (Divine Console Phase 8).** Kind `deja_vu_replay` (flag
`GOD_DEJA_VU_REPLAY`, env `SIM_GOD_DEJA_VU_REPLAY`, default off). `decisionDigests`
ring (cap `GOD_DECISION_DIGEST_CAP = 200`): `{frameTick, agentId, action,
reasoningHash?}` — appended on gated LLM apply (not divine compulsion/possession/
`decision_veto_resolve`/replay steps). `reasoningHash` optional SHA-256 of
reasoning, 16 hex chars. `dejaVuReplays` stores cancellable sessions
`{id, targetId, steps[], currentIndex, status, createdFrame}`. Apply
`{targetId, maxSteps?}` (default `GOD_DEJA_VU_MAX_STEPS = 8`): preview freezes
last K digest actions into `replaySteps`; apply creates parent and sequences
`decision_compulsion` gates (`remainingTurns: 1`, `dejaVuReplayId` each) until
steps exhaust or cancel. Session cap `GOD_DEJA_VU_SESSION_CAP = 12`. `god_cancel(replayId)` closes parent and
in-flight gate. Audit `public: false`. Sight: digest snippets and active replay
summaries — not `/state`.

**`crowd_compulsion`** — `{"theme"?: str, "durationFrames"?: int,
"remainingTurns"?: int, "targets": [{targetId, pinnedDecision}, ...]}` (max
`GOD_CROWD_COMPULSION_MAX_TARGETS = 12`). At least one of `durationFrames` or
`remainingTurns` at campaign level. Batch: parent in `crowdCompulsions` linking
`targets: {str(agentId): gateId}` plus per-target `decision_compulsion` gates
(`crowdCompulsionId` each). Theme/pinned decisions private. `god_cancel(parentId)`
closes linked gates; gate expiry/replacement finalizes parent when none remain.

**`dream_broadcast`** — `{"durationFrames": int, "dreamSnapshot": object,
"targetIds": [int, ...]}` (max `GOD_DREAM_BROADCAST_MAX_TARGETS = 12`). Batch:
parent in `dreamBroadcasts` linking `targets: {str(agentId): maskId}` plus
per-target `context_mask` mode `dream` (`dreamBroadcastId` each). Snapshot text
private; Sight shows mask mode/expiry only. `god_cancel(parentId)` closes linked
masks; mask expiry/replacement finalizes parent when none remain.

**Voice apply side effects.** `_god_apply_providence`, `_god_apply_private_omen`,
whisper-campaign omens, and proclamation (via providence) call
`_cancel_voice_blocked_special_turns(affected_agent_ids)` at apply — drops pending
`sprite_design_only`/`invention_only` turns (clears flag, no reschedule). Same
cancellation each tick while Voice guidance active and unacknowledged
([03](03-cognition.md)). Hard drop, not deferral.

**Divine-response log.** `godState["recentDivineResponses"]` is a bounded
newest-first ring (cap `GOD_DIVINE_RESPONSE_LOG_MAX`, same order of magnitude
as `recentInterventions`) of adherence records written when an agent's think
records a valid or synthesized `divine_response` against active guidance:

```json
{
  "agentId": 3,
  "agentName": "Ash",
  "guidanceId": "divine-12",
  "guidanceKind": "providence" | "private_omen",
  "stance": "follow" | "continue",
  "reason": "…",
  "synthetic": false,
  "frameTick": 120450,
  "action": "contribute_resources",
  "skipCount": null,
  "capped": false
}
```

`synthetic: true` when engine supplied `missing_divine_response`. Genuine
`divine_response` acks immediately (`skipCount` null, `capped` false). Synthetic
no longer acks immediately — `skipCount` from `_bump_voice_guidance_skip`
(providence `skipCounts[agentIdStr]` or omen `skipCount`); `capped: true` when
count reaches `GOD_VOICE_ACK_SKIP_CAP` (3) and force-acked as non-compliance
close. `reason` operator-visible in Sight; private-omen text never here — only
adherence reason. Log private (not `/state`); `god_sight()` exposes full ring.
Never in agent prompts.

**`revoke_guidance`** — `{"id": str}`. Ends active providence or private omen
by intervention id (providence checked first). Inactive id rejects "guidance id
not found or already inactive" without consuming intervention sequence.
`_god_target_fingerprint` records `{"targetKind": "providence"|"private_omen"|
None, "existed": bool}` at preview; stale revoke caught at apply like stale
replace.

**Closure and the memory contract.** `_close_providence(status)` and
`_close_omen(key, status)` are single choke points (expiry, revocation,
replacement) — each logged once via `_log_divine`. `_close_providence` clears
the slot; no memory-write contract. `_close_omen` guarded by `memoryWritten`:
active omens never in ordinary memory (prompt line only — [03](03-cognition.md));
on first close (expiry/revocation/replacement), text written via
`_push_memory(agent, text, kind="divine_omen")` and `memoryWritten` set `True`
before delete. Second close on deleted key no-op. `_normalize_god_state` preserves
`memoryWritten` (default `False` when absent); restore-time
`_expire_divine_effects(restore=True)` writes once for mid-omen saves, never
again after write captured.

**Visibility.** `providence` public: activity/`conversationLog`/chronicle like
proclamation, `snapshot()["god"]["providence"]`, `recentInterventions`
`"public": True`. Private omens: every Phase 3 `recentInterventions` record
for `private_omen`/replace/`revoke_guidance` sets `"public": False`;
`recentPublicInterventions` filters on that flag — guard against `/state` leak;
new call sites must set `"public"` explicitly. `god_sight(filters)` per-agent
omen status only — `{"active": true, "expiresFrame": int, "unacked": bool}` or
`None` — never omen text; `unacked` until `divine_response` for that guidance id.
Sight also carries `recentDivineResponses` and per-agent slices. Guidance text
reachable via `recentInterventions` in `god_sight` or operator recall — not
unauthenticated surfaces.

**Village pulse (Divine Console improvements, Phase 10).** `god_sight()` adds
top-level `pulse` from live world state under lock — crisis agents, stockpile
totals, open build projects, elder status, weather, active-event titles,
providence `{active, expiresFrame}` without text. Not in `godState`, not
autosaved; recomputed per Sight fetch, no LLM.

## Sovereign God mode (Phase 4 — bounded immediate miracles)

Three catalog kinds applyable: `agent_vitals`, `grant_resource`,
`structure_condition`. All irreversible (`_god_reversibility_class` default for
non-providence/omen kinds) and public (`recentInterventions` `"public": True`).
Source-attributed like proclamation: activity, `conversationLog`
(`source="divine"`, kind `divine_vitals`/`divine_grant`/`divine_structure`),
chronicle (`kind="divine"`, `source="divine"`). Each consumes
`_next_intervention_id()`, appends `recentInterventions`, writes `divine.jsonl`
`"applied"`. None cancellable: `god_cancel` searches
`activeEvents`/`providence`/`privateOmens` only; `_god_apply_revoke_guidance`
matches providence/`privateOmens` ids only — Phase 4 ids refuse by construction.

**Preview warnings (Divine Console improvements, Phase 7).** `god_preview()` may
attach `warnings: string[]` on `ok: true` — non-fatal; Apply still allowed.
`story_event` with non-empty `modifiers`: `_god_modifier_conflict_warnings()`
scans opposing `GOD_MODIFIER_RANGES` pairs (both stressed above neutral).
Active-key occupancy conflicts (`replaceEffectId` required) stay fatal in
`_validate_god_story_event`.

**Preview outcome.** `god_preview()` adds `previewOutcome`: clamped/bounded value
`_god_preview_outcome()` computes with same arithmetic as `_god_apply_*`.
`None` for Phase 2/3 kinds and vanished Phase 4 targets (apply-time
revalidation authoritative for vanished targets). Unchanged target between
preview and apply → applied outcome matches preview.

**`agent_vitals`** — `{"targetId": int, "healthDelta": number?,
"hungerDelta": number?}`. Living `targetId`; at least one non-zero delta; each
capped `GOD_VITALS_DELTA_MAX = 100`. Health/hunger clamped 0..100 like
`_update_survival`, with asymmetry: **v1 cannot kill** — health `<= 0` is
incapacitation threshold, not death (`_agent_dies` only for old age); negative
`healthDelta` stops at `GOD_VITALS_HEALTH_FLOOR = 1` (miracle cannot set
`incapacitated`); never touches `deathFrame`/`incapacitated`/succession.
Hunger 0 does not incapacitate/kill (affects next `_update_survival` branch);
`hungerDelta` clamps 0..100 both ends. See [06-agents.md](06-agents.md).

**`grant_resource`** — `{"resourceId": str, "amount": int,
"target": "stockpile" | {"agentId": int}?}` (omit/`"stockpile"` = village).
`resourceId` in live `resourceRegistry`; else "unknown resource id". Positive
`amount`, cap `GOD_GRANT_PER_COMMAND_CAP = 200`, session total
`GOD_GRANT_SESSION_CAP = 2000` (`self._god_grant_session_total`, in-memory,
reset by `reset()` — not `state.db`). Agent target: living agent; fills carry
room first (`_carry_cap`), remainder to stockpile — same sinks as
`_perform_gather`. See [08-systems-economy.md](08-systems-economy.md).

**`structure_condition`** — `{"structureId": int, "delta": number}`.
Non-ruined structure (`isRuin` false, `condition > 0`); else rejected before
`delta` check. Non-zero `delta`, cap `GOD_STRUCTURE_DELTA_MAX = 100`. Applied via
`_apply_structure_condition_delta` (shared with `_tick_structure_decay`),
clamping 0..100, `STRUCTURE_DISREPAIR_THRESHOLD` crossings, ruin transitions,
`homeOf`/`homeStructureId` handling. Single-structure scope — cannot un-ruin
(batch un-ruin: `repair_structures`, `clear_ruins` below). Passive decay,
disasters, and damage path produce ruins, not registry deletion — remains until
`_maybe_cull_ruins()`, God `clear_ruins`, or offline prune. See
[05-world.md](05-world.md), [08-systems-economy.md](08-systems-economy.md).

## Sovereign God mode (Town integrity — mass structure repair and ruin clearance)

Two additional irreversible, public apply kinds for operator escape hatches when
ruin pressure outruns autonomous repair. Same `god_preview`/`god_apply` pipeline,
one `divine.jsonl` `"applied"` each, `godState.intervened = True` (monotonic),
Divine Console Miracles tab ([11-viewer.md](11-viewer.md)). Neither cancellable
via `god_cancel` (same class as `structure_condition`). Schemas in
`/control/god/capabilities` ([04-http-api.md](04-http-api.md)).

**`repair_structures`** — batch condition restore and optional un-ruin.
Payload (conceptual):

```json
{
  "scope": "ids" | "all_critical" | {"districtId": "<slug>"},
  "structureIds": [int]?,
  "conditionTarget": number?,
  "unRuin": true?
}
```

- `scope` selects the target set: explicit `structureIds` (required when
  `scope == "ids"`), every working-critical type village-wide when
  `scope == "all_critical"`, or all structures in `districtId` when scoped
  to a district. `districtId` is a string slug matching
  `civilization["districts"]` keys (e.g. `"village_core"`, `"forest"`).
- `conditionTarget` is optional; when present it sets `condition` (clamped
  `0..100`) via `_apply_structure_condition_delta`-equivalent semantics per
  structure, magnitude capped at `GOD_REPAIR_STRUCTURES_CONDITION_MAX = 100`
  per structure and `GOD_REPAIR_STRUCTURES_BATCH_MAX = 10` structures per
  command.
- When `unRuin` is true (default), ruined targets (`isRuin` or
  `condition <= 0`) are restored to at least `REPAIR_CONDITION_RESTORE`
  (`50`) and `isRuin` cleared — the explicit exception to the old Phase 4
  text that "repair through this miracle can never recreate a destroyed
  structure." The single-target `structure_condition` miracle remains
  non-ruin-only; only `repair_structures` (and agent `repair_structure`)
  may un-ruin.
- Rejects unknown ids, empty selections, or batches exceeding the cap.
  Preview reports the exact per-structure outcomes.

**`clear_ruins`** — delete selected or aged ruins from the registry (mirrors
engine cull cleanup). Payload (conceptual):

```json
{
  "structureIds": [int]?,
  "minAgeFrames": int?,
  "districtId": "<slug>"?
}
```

- At least one selector is required. `structureIds` removes explicit ruins;
  `minAgeFrames` (default `RUIN_CULL_AGE_FRAMES = DAY_FRAMES`) restricts to
  ruins at least that old; `districtId` limits to one district (string slug,
  e.g. `"village_core"`).
- Each removed ruin uses the same cleanup as `_maybe_cull_ruins()` and
  [`scripts/prune_ruins.py`](../scripts/prune_ruins.py): drop from
  `civilization["structures"]`, clear `homeStructureId`, filter
  `reorgTasks`. Non-ruin ids are rejected.
- Batch capped at `GOD_CLEAR_RUINS_BATCH_MAX = 10` per command. Writes
  activity + chronicle attribution (`source="divine"`) like other miracles.

**Amended invariants (summary).**

| Path | May un-ruin? | May delete registry entry? |
|---|---|---|
| Passive decay / disaster damage | No (produces ruin) | No |
| `structure_condition` miracle | No (non-ruin targets only) | No |
| Agent `repair_structure` | Yes (funded half-cost rebuild) | No |
| `repair_structures` (God) | Yes (batch, capped) | No |
| `_maybe_cull_ruins()` (engine) | No | Yes (1–3 aged, unaffordable ruins) |
| `clear_ruins` (God) | No | Yes (operator-selected/aged ruins) |
| `scripts/prune_ruins.py` (offline) | No | Yes (all ruins) |

## Sovereign God mode (Phase 5 — storyteller events and timed lawgiver modifiers)

One more applyable kind — `story_event`; `activeEvents` becomes real timed
composable state. `god_cancel` wired for real (stub through Phase 4). See
[08-systems-economy.md](08-systems-economy.md) (arithmetic at consumer sites),
[09-systems-society.md](09-systems-society.md) (divine vs village-law composition).

**`_divine_modifier(key, default=1.0)`.** Single read path for all consumers.
Returns `default` when `GOD_MODE_ENABLED` false, `godState` missing/malformed, or
no `activeEvents` entry carries `key` within `[startFrame, expiresFrame)`.
Modifier stops at `expiresFrame` before next `_expire_divine_effects` sweep.
No active effect → `1.0` (feature-off baseline). Consumers multiply local
delta/amount by this value.

**Seven allowlisted keys**, each a `float` range checked at validation time:

| Key | Range | Consumer |
|---|---:|---|
| `gather_yield_multiplier` | `0.25..3.0` | General `_perform_gather` yield. |
| `fish_yield_multiplier` | `0.0..3.0` | Fish only — **replaces**, never multiplies with, `gather_yield_multiplier`. |
| `hunger_drain_multiplier` | `0.0..3.0` | `_update_survival`'s `HUNGER_RATE` drain. |
| `health_regen_multiplier` | `0.0..3.0` | `_update_survival`'s **fed** `HEALTH_REGEN` branch only — never `COLLAPSE_REGEN`. |
| `starvation_damage_multiplier` | `0.0..3.0` | `_update_survival`'s `HEALTH_RATE` starvation-damage branch. |
| `structure_decay_multiplier` | `0.0..3.0` | `_tick_structure_decay`'s passive `STRUCTURE_DECAY_PER_GOODS_TICK` only — never direct disaster/miracle damage. |
| `spoilage_multiplier` | `0.0..3.0` | `_tick_spoilage`'s computed `to_spoil`. |

Base module constants unchanged; each consumer multiplies local value by
`_divine_modifier(key)` at existing calculation site, order, before clamp — see
[08-systems-economy.md](08-systems-economy.md) (per-site ordering, gather
zero-path, `COLLAPSE_REGEN` unreachable via `health_regen_multiplier`).

**`story_event`** —
```json
{
  "title": "str (<=80 chars, GOD_EVENT_TITLE_MAX_CHARS)",
  "narration": "str (<=240 chars/600 bytes, same cap as proclamation)",
  "visibility": "public | private",
  "targetId": "int, required iff visibility == 'private'",
  "durationFrames": "int?, clamped like providence/omen (300..54000, default 5400)",
  "modifiers": {"<one of the seven keys>": "number in its range, ..."},
  "primitives": [{"kind": "agent_vitals|grant_resource|structure_condition", "payload": {}}],
  "providence": {"text": "str"},
  "replaceEffectId": "str?"
}
```
At most `GOD_STORY_EVENT_MAX_MODIFIERS = 7` keys and
`GOD_STORY_EVENT_MAX_PRIMITIVES = 5` primitives. Primitives validated via
`_validate_god_envelope` per kind (same bounds as standalone Phase 4);
normalized list idempotent under re-validation. Optional `providence` sub-payload
uses event `durationFrames`, applied via `_god_apply_providence` (own intervention
id, `recentInterventions`, disclose-then-replace guard).

**One active value per key.** New/replacement `story_event` rejecting occupied
modifier key (`"modifier '<key>' already has an active effect (id <id>) --
supply replaceEffectId to replace it"`) unless `replaceEffectId` names that
active event's id; `replaceEffectId` must resolve to active event. Occupancy
re-checked at preview and apply.

**Atomicity.** `_validate_god_story_event` validates all sub-components before
normalization; one invalid component rejects whole envelope. `god_apply`
revalidates before `_god_apply_story_event` — apply path has no rejection branch;
accept-all-or-nothing by construction.

**Applying** (`_god_apply_story_event`): optionally `_close_story_event` on
`replaceEffectId`; mints event intervention id; applies primitives via Phase 4
helpers (each own id, `recentInterventions`/`divine.jsonl`, `parentEventId`);
optional providence; appends `activeEvents` record
`{id, kind: "story_event", title, narration, visibility, targetId, createdFrame,
startFrame, expiresFrame, status, modifiers, primitiveInterventionIds,
providenceId, replaces}` via `_god_events_insert` (cap `GOD_ACTIVE_EVENTS_CAP =
8`, evict oldest closed first, oldest active backstop if all slots active).
Public event: activity/`conversationLog`/chronicle (`source="divine"`); private: none.

**Reversibility.** `_god_reversibility_class` takes full normalized command:
no `primitives` → cancellable (modifiers/providence only); with primitives →
consequential (primitives irreversible). Preview surfaces `reversibilityClass`.

**Closure — `_close_story_event(event, status)`.** Choke point for
`_expire_divine_effects`, `god_cancel`, replacing `story_event` — logged once.
If `providenceId` still matches active `godState["providence"]["id"]`, closes
linked providence via `_close_providence`.

**Expiry.** `_expire_divine_effects` calls `_close_story_event` when
`expiresFrame` reached. `_divine_modifier` checks `startFrame <= frameTick <
expiresFrame` — influence stops at `expiresFrame` before cleanup sweep.
Restore-time (`restore=True`) marks past `expiresFrame` as `"restore-closed"`.

**`god_cancel(target_id)` — now real.** Checks: `providence`, `privateOmens`,
active `activeEvents` (by `id`). Match → close via normal path, return
`cancelled: true` with `targetKind`. No match (including Phase 4 miracle ids
not in those stores) → `cancelled: false`, `"nothing to cancel"`. Proclamation
cancellable via providence slot when active. Direct lock-held mutation, no
preview/apply.

**Preview — divine vs. custom-rule composition.** `story_event` preview with
`gather_yield_multiplier` or `fish_yield_multiplier` adds `customRuleContext`:
`{ruleId, subject, value}` per enacted village rule on `collect_resource`
(additive — divine multiplier not merged into custom-rule value; see
[08-systems-economy.md](08-systems-economy.md)).

**Restore.** `activeEvents` round-trips via `godState`; `expiresFrame` absolute
(no re-basing). Past `expiresFrame` closed once by
`_expire_divine_effects(restore=True)`.

## Sovereign God mode (Phase 6 — divine weather override)

`weather_override` is an `activeEvents` kind using Phase 5 machinery. Full
behavior in [05-world.md](05-world.md#divine-weather-override-sovereign-god-mode-phase-6-weather_override);
shared plumbing notes:

- **Closure:** `_close_weather_override(event, status)` — same callers as
  `_close_story_event` (`_expire_divine_effects`, `god_cancel`, replacing
  `weather_override`); no linked providence; always calls
  `_weather_handoff_successor` before return.
- **Expiry:** inside `_expire_divine_effects` `activeEvents` scan;
  `kind == "weather_override"` routes to `_close_weather_override` (same cap
  `GOD_ACTIVE_EVENTS_CAP`, `"expired"`/`"restore-closed"`).
- **Restore-time atomicity:** `restore=True` sweep closes and hands off in one
  call; no closed-but-not-handed-off intermediate; second sweep no-op (`status
  != "active"` guard).

## Huntable wildlife (`WILDLIFE_ENABLED`)

Server-authoritative fauna, distinct from Path-1 `_tick_wildlife` pressure
([10-path1.md](10-path1.md)). `WILDLIFE_ENABLED` (default True;
[05-world.md](05-world.md)): off → no fauna state, no `_move_wildlife`/
`_tick_huntable_wildlife`, `hunt_wildlife` omitted, viewer draw no-ops.

**State.** `civilization["wildlife"]` list under engine lock, persisted
(cold-start + restore). Fields: `id`, `kind`, `districtId`, `x`, `y`,
`targetX`/`targetY`, optional `waypoints`, `hp`, `maxHp`, `alive`, `respawnAt`,
`behaviorState` (see below; defaults to `"wander"` — `_make_wildlife_creature`
sets it on spawn, `_normalize_wildlife_records` backfills it via `setdefault`
on restore for saves predating this field).
`maxHp` from `WILDLIFE_MAX_HP[kind]` (low ≈1–2 hits; mid ≈3–4; high
`boar`/`seal` ≈5–6; `bee` not combat target). Kind pools/yields:
[08-systems-economy.md](08-systems-economy.md); caps `WILDLIFE_STAGE_COUNT` /
`WILDLIFE_CAP_PER_DISTRICT = 4` from per-district wildlife stage — farm/forest
via `districtEcologyStage` (same averaged stock ratio as `districtEcology`);
beach via fish-only ratio in `districtWildlifeStage` ([05-world.md](05-world.md)).

**Habitat clamp.** Forest/farm: district bounds (inset). Beach water kinds
(`fish`, `crab`, `turtle`, `seal`): eastern shore strip (~70px) of the adjacent
**ocean** district (actual water tiles, not beach sand); y-range intersects the
spawn beach and ocean bounds. Fallback when no ocean district exists: beach-west
strip as before. `gull`: full beach bounds.

**`_move_wildlife()`** — every tick when the flag is on (alongside agent
move). In-district idle wander via simple steering at `WILDLIFE_SPEED[kind]`
(no road required). When an agent is within `WILDLIFE_FLEE_RADIUS`, or after
a combat hit, retarget away (flee). Creatures with migration / long-range
waypoints follow those waypoints at the same step logic.

**Behavior state machine (`WILDLIFE_BEHAVIOR_ENABLED`, default True).**
Layered inside the same `_move_wildlife()` call — no new tick system, timer,
or thread. Off reverts `_move_wildlife()` byte-identical to the flag's
pre-existing wander/flee/migrate code path (the new branches are gated
behind a single `if WILDLIFE_BEHAVIOR_ENABLED:` block; `_wildlife_force_flee`
only writes `behaviorState` when the flag is on). Per-creature
`behaviorState` is one of:

- **`flee`** — always wins. Resolved first each tick from the same
  proximity check as before (agent within `WILDLIFE_FLEE_RADIUS`, or a
  combat hit via `_apply_hunt_damage` → `_wildlife_force_flee`); overrides
  graze/rest/wander unconditionally.
- **`rest`** — `WILDLIFE_LAND_KINDS` only (forest/farm pool kinds minus the
  decorative `bee`), when `self._is_night()` is true (existing time-of-day
  source — [Time model](#time-model), `PATH1_ENABLED`-gated). Halts
  movement entirely for the tick.
- **`graze`** — `WILDLIFE_LAND_KINDS` by day: slow drift at
  `WILDLIFE_GRAZE_SPEED_MULT` (0.4×) of normal speed, with a
  `WILDLIFE_GRAZE_PAUSE_CHANCE` (0.4) per-tick chance of holding still
  (seeded `random.random()`, same global RNG the rest of the wildlife
  system already uses — no fresh `random` import, deterministic under
  `DETERMINISM_PINNING`).
- **`wander`** — default for beach-pool kinds (`fish`, `crab`, `gull`,
  `turtle`, `seal`) and for any creature mid-migration; identical motion to
  the pre-existing behavior.

**Loose herding (`WILDLIFE_FLOCK_KINDS`).** While in `graze`/`wander` (never
`flee`/`rest`), flock kinds (`bird`, `deer`, `cow`, `rabbit`, `chicken`,
`fish`, `gull`) nudge their current target toward the nearest living
same-kind creature in the same district within `WILDLIFE_HERD_RADIUS` (140px),
capped at `WILDLIFE_HERD_PULL` (6px) per tick and re-clamped to the habitat
rect (`_wildlife_clamp_pos`) — so herding can never leave habitat or override
an active flee.

**Wildlife stage target (`_wildlife_stage_for_district` /
`_wildlife_stage_target`).** Maps district stock health to spawn cap via
`WILDLIFE_STAGE_COUNT` (`barren` 0 → `lush` 4). Farm and forest districts
use `_district_ecology_ratio` (average across gatherable stocks). **Beach
districts use fish stock only:**
`min(1.0, fish / STOCK_DEFAULT_MAX)` (0 when fish is missing), not the
clay/sand-averaged ecology ratio — so depleted fish drives beach fauna
toward `barren`/`sparse` even when sand/clay remain high. Stage hysteresis
reuses `_district_ecology_stage_with_hysteresis`; beach persists in
`civilization["districtWildlifeStage"]` (separate from
`districtEcologyStage`, which still averages all beach stocks for viewer
terrain tint).

**`_tick_huntable_wildlife()`** — slower cadence
(`WILDLIFE_POP_TICK_FRAMES`, migrate check frames). Spawn into under-cap
habitat districts from the kind pool; respawn dead creatures whose
`respawnAt` has elapsed; cull / hold density to stage cap; run migration
checks (below).

**Cross-district migration.** Alive non-decorative creatures may, with small
probability on pop tick, pick another district with their `kind` in pool (forest↔forest, farm↔farm,
beach↔beach); destination under cap. Road pathfinder when path exists; else
straight-line to district center, clamp habitat on arrival; update `districtId`,
clear waypoints, resume wander.

**Combat.** `hunt_wildlife` ([07-actions.md](07-actions.md)) damages under
the lock; kill grants `meat` or `fish` per the yield table. No separate
weapon inventory — damage is role-based only (`HUNT_DAMAGE_HUNTER` vs
`HUNT_DAMAGE`).

**`/state` projection.** Alive creatures only:
`wildlife: [{id, kind, districtId, x, y, hp, maxHp}]` — see
[11-viewer.md](11-viewer.md). The viewer does not pathfind fauna.

### Sovereign God mode: wildlife kinds

Three irreversible, public god kinds mutate `wildlife` under lock via
`god_preview`/`god_apply` (outside `DECISION_ACTIONS` —
[01-architecture.md](01-architecture.md)). Schemas in `/control/god/capabilities`;
each writes `divine.jsonl` `"applied"` ([12-ops.md](12-ops.md)):

| Kind | Payload (conceptual) | Effect |
|---|---|---|
| `wildlife_spawn` | `districtId`, `kind` (must be valid for that district's pool) | Spawns an alive creature at a habitat-legal position (respects cap when practicable; reject unknown kind/district) |
| `wildlife_despawn` | creature `id`, or a district clear | Marks target(s) dead / removes from the alive set |
| `wildlife_set_hp` | creature `id`, `hp` | Clamps and sets `hp` (and may kill if `hp <= 0`, with ordinary dead/respawn bookkeeping) |

None of the three is cancellable via `god_cancel` (same irreversible class as
`grant_resource` / Phase 4 miracles).
