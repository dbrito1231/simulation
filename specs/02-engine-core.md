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

`_tick_once()` (sim_engine.py:9388) runs under `self.lock` once per
`TICK_DT = 1/30 s` (`TICKS_PER_SEC = 30`). If `self.paused`, it returns
immediately — the sim clock (`frameTick`) freezes entirely. Otherwise
`frameTick` advances by 1 and, per frame `ft`, these flag-gated systems run on
their own cadence (all frame counts are ticks at 30/s):

| Gate | Cadence (frames) | System |
|---|---|---|
| `SURVIVAL_ENABLED` | 30 | `_update_survival` per agent |
| `MEMORY_ENABLED` | 1800 | `_run_memory_maintenance` |
| `META_SYSTEM` (off) | 2400 | `_maybe_meta_update` |
| `EMERGENT_ROLES` | 120 | `_maybe_auto_switch_role` |
| `RULES_ENABLED` | 150 | `_maybe_advance_rules` |
| `LIFECYCLE_ENABLED` | 150 | `_maybe_resolve_stalled_succession` |
| `LIFECYCLE_ENABLED` | 300 | `_tick_lifecycle` |
| (unconditional) | 150 | a fixed batch: `_maybe_feed_starving`, `_maybe_repair_critical`, `_maybe_abandon_stalled_projects`, `_maybe_relocate_stuck_project`, `_maybe_reorganize_structures`, `_maybe_force_contribution`, `_maybe_start_idle_district_project`, `_maybe_build_funded_project`, `_maybe_start_approved_custom`, `_maybe_retire_blueprint`, `_maybe_amnesty_rejected_blueprints`, `_maybe_retire_custom_resource`, `_maybe_invention_backstop`, `_maybe_found_district`, `_maybe_welcome_newcomer` |
| within the 150-batch, `SAGE_REVIEW_ENABLED` | 150 | `_maybe_skip_sage_review`, `_maybe_amnesty_denied_sage_reviews` |
| within the 150-batch, `TECH_TREE_ENABLED` | 150 | `_maybe_era_transition`, `_maybe_dissolve_council` |
| within the 150-batch, `CULTURE_ENABLED` | 150 | `_maybe_study_at_library` |
| within the 150-batch, `CEMETERY_ENABLED` | 150 | `_maybe_handle_burials` |
| within the 150-batch, `ECONOMY_ENABLED` | 150 | `_maybe_mint_coin`, `_maybe_fund_project_coin` |
| within the 150-batch, `path1_on()` | 150 | `_maybe_found_settlement`, `_path1_industry_benchmark` |
| `path1_on("PRESSURE_LOOP_ENABLED")` | 900 | `_tick_wildlife` |
| `path1_on("PRESSURE_LOOP_ENABLED")` and `_is_night()` | 30 | `_tick_night_pressure` |
| `STRUCTURE_EFFECTS_ENABLED` | 150 | `_tick_structure_effects` |
| `ECOLOGY_ENABLED` | 600 | `_tick_ecology_regrow` |
| `GOODS_ENABLED` | 900 | `_tick_goods` |
| `GOODS_ENABLED` | 13500 (=`DAY_FRAMES`) | `_tick_shelter` |
| `MEMES_ENABLED` | 90 | `_spread_beliefs_by_proximity` |
| `BENCHMARKS_ENABLED` | 600, or frame 60 (`FIRST_BENCHMARK_FRAME`) | `_sample_benchmarks` |

After the gated systems: every non-incapacitated agent moves (`_move_agent`);
`_sage_emergency()` computes an emergency target (see below); message timers
decrement; and, for each non-incapacitated agent not currently a designated
emergency responder, either a reorg task steps, a goal steps, or the agent's
`thinkTimer` reaches 0 and `_schedule_think` is attempted.

## Time model

- `TICKS_PER_SEC = 30`; `DAY_FRAMES = 13500` (sim_engine.py:488); one day = 450 s.
- `YEAR_FRAMES = 324_000`; `SEASON_FRAMES = YEAR_FRAMES // 4 = 81_000`
  (sim_engine.py:523-524) — one season = 6 day/night cycles.
- `NIGHT_FRACTION = 0.25` (sim_engine.py:1007): `_is_night()` is true for the last
  quarter of each `DAY_FRAMES` cycle, but only when `PRESSURE_LOOP_ENABLED` — night
  otherwise never triggers (sim_engine.py:4321-4325).
- `SEASON_REGROW_MULT = {"spring": 2, "summer": 1, "autumn": 1, "winter": 0}`
  (sim_engine.py:526): ecology regrowth is doubled in spring and fully halted in
  winter (`_tick_ecology_regrow`, applied only when `ECOLOGY_ENABLED`).
- `_calendar()` (sim_engine.py:2765) is a pure function of `frameTick`: returns
  `year`, `season`, `dayOfSeason`, `daysPerSeason`, `isNight`, `dayFraction` — all
  derived, nothing persisted separately.

## Roster / cold start

`AGENT_DEFS` (sim_engine.py:1316) is 12 hand-written entries (name, role,
personality, color, starting district). `MAX_ROSTER_SIZE = 20` is the hard
ceiling for `roster_size` — a Sid-parity Phase 6 headroom increase from the
8-12 agent range, *not* a bid at Project Sid's ~500-agent scale (explicit
non-goal, specs/00-overview.md). `SimEngine._select_active_defs(roster_size)`
clamps to `[1, MAX_ROSTER_SIZE]` and resolves the active def list:
- `roster_size <= len(AGENT_DEFS)` (today's 8-12 default/range): unchanged
  from before Phase 6 — `ROSTER` (the 8 default names) fills first, then
  remaining `AGENT_DEFS` entries in def order, with Sage force-included if
  dropped. `roster_size == len(AGENT_DEFS)` returns `AGENT_DEFS` verbatim.
- `roster_size > len(AGENT_DEFS)`: all 12 hand-written defs plus
  `_generated_agent_defs(roster_size - len(AGENT_DEFS))` for the rest.
  Generation is deterministic (no randomness): name and personality cycle
  through small fixed pools (`_GENERATED_AGENT_NAMES`,
  `_GENERATED_AGENT_PERSONALITIES`), and role/starting-district rotate across
  the 12 non-elder `roles.json` seed roles (one generated agent per role
  before any role repeats) — a generated agent's zone is copied from the
  hand-written def that shares its role, so it spawns in a district that
  actually supports that role. Generated agents are built by the same
  `_make_agents` as hand-written ones and are indistinguishable to every
  other system (roles, beliefs, relationships, think scheduling); they just
  carry pool-drawn flavor text instead of bespoke hand-authored text.
  `civilization["basePopulation"]` reflects the full `roster_size` (clamped to
  `MAX_ROSTER_SIZE`, not `len(AGENT_DEFS)`), so the Structure-Effects house
  population cap (specs/08) computes correctly above 12 agents too.

## Think scheduling

Each agent gets a staggered `thinkInterval` at construction:
`thinkInterval = 360 + i*60` for the i-th agent, overridden to `240` for the
elder role (sim_engine.py:1381-1384); `thinkTimer` starts at `i*30` so agents
don't all think on the same frame. `_schedule_think` (sim_engine.py:9362) only
actually dispatches a job if: the agent isn't already in `self._inflight`,
`len(self._inflight) < MAX_CONCURRENT_LLM` (3), the global LLM cooldown has
expired, and at least `LLM_MIN_GAP_MS = 250` ms have passed since the last
dispatch. If any of these block it, the caller retries after
`THINK_RETRY_FRAMES = 15` frames (0.5 s) instead of waiting a full interval.
`self._inflight` is a set of agent names with a job in flight; entries are added
on dispatch and discarded in the job's `finally` block (sim_engine.py:9360).

**Dispatch fairness (Phase 6).** `MAX_CONCURRENT_LLM`/`LLM_MIN_GAP_MS` remain
the de facto global throughput cap (unchanged); the gap Phase 6 closes is
*ordering* under contention. `_tick_once` no longer attempts dispatch in fixed
`self.agents` roster order — every agent whose `thinkTimer` reached 0 this
tick (and isn't mid-goal/reorg/emergency-response) is collected into a
`think_ready` list, then sorted by `lastThinkFrame` ascending (least-recent
successful think first, i.e. most overdue) before `_schedule_think` is tried
in that order. `lastThinkFrame` is stamped with the current `frameTick` only
on a successful dispatch; a failed attempt (pool full, cooldown, min-gap)
leaves it unchanged, so the same agent keeps front-of-line priority on its
next retry instead of losing it to fixed-order bias. Without this, a roster
larger than `MAX_CONCURRENT_LLM` could starve late-indexed agents indefinitely
under sustained pool contention, since every failed retry reset to the same
`THINK_RETRY_FRAMES` with no memory of how overdue the agent actually was.

## Proximity scans (district-bucketed, Phase 6)

`_get_nearby_agents`/`_get_nearby_detailed` (both `NEARBY_RADIUS = 80`) back
the `nearby_agents`/`nearby_agents_detailed` think-payload fields and are
called once per agent per think-payload build — the hottest per-tick pass
over the roster, so a flat `for o in self.agents` scan is O(n) per call
(O(n²) per full think round). Both now route through
`_nearby_candidate_pool(agent)` instead of scanning `self.agents` directly:

- `_rebuild_district_buckets()` groups `self.agents` by `currentDistrict`
  into `self._district_agent_buckets`, rebuilt lazily once per `frameTick`
  (cached by frame stamp, not per-call).
- `_district_adjacency_for(did)` returns the set of district ids whose
  bounds — expanded by `NEARBY_RADIUS` on every side — overlap district
  `did`'s bounds (via the same `_rects_overlap` used for district-founding
  validation), cached and invalidated only when the district count changes.
  This matters because starter districts aren't always farther apart than
  `NEARBY_RADIUS`: `village_core` and `market` are only ~70px apart at their
  closest edge, narrower than the 80-unit radius, so a same-district-only
  bucket would silently drop real cross-border neighbors a flat scan would
  have found. The candidate pool for an agent is its own district's bucket
  plus every adjacent district's bucket — provably equivalent to the flat
  O(n) scan for any hand-placed position (see
  `scripts/sid_parity_smoke.py::test_district_bucket_matches_flat_scan`),
  just computed over a much smaller candidate set at roster 20.
- `_find_nearest_agent` (used only for the reactive `move_to_agent` fallback
  when no explicit target is given, not the hot think-payload path)
  deliberately stays a flat scan — it has no radius bound and must find the
  true global nearest agent even across the whole map, which a
  district-local candidate pool cannot guarantee.

## Pause / resume / reset

- `pause()` / `resume()` (sim_engine.py:9883-9889) just flip `self.paused` under
  the lock; `_tick_once` early-returns while paused, freezing `frameTick`.
- `reset(roster_size=None)` (sim_engine.py:9891) rebuilds the world
  (`_reset_world`), clears the in-process memory store, then deletes and
  immediately rewrites `state.db` via `clear_state()` + `save_state()` so a
  reset persists cleanly.

## Sage emergency

`_sage_emergency()` (sim_engine.py:1884) returns a target agent needing rescue,
or `None`, only when `SURVIVAL_ENABLED`. It finds the living elder (`role ==
"elder"` and not dead); if the elder is not incapacitated and
`health >= SAGE_CRITICAL_HEALTH` (30, sim_engine.py:350), there's no emergency.
Otherwise the target is the healer (if the healer is also incapacitated) or the
elder itself. `_sage_responders(target)` (sim_engine.py:1903) picks the
non-incapacitated healer (if not the target) plus the nearest other
non-incapacitated agent. Each tick, a designated responder skips normal
thinking/goal logic entirely and instead steps `_rush_to_heal` (sim_engine.py:1919)
every `GOAL_STEP_FRAMES` — moving toward the target, then issuing a hardcoded
`heal_agent` decision once within 80 px.

**In-flight LLM decision discard:** if a think job's LLM response comes back
(sim_engine.py:9264-9279) and, in the meantime, a Sage emergency began *and*
this agent is now a designated responder, the just-returned decision is
discarded entirely and `_rush_to_heal` runs instead — the emergency always wins
over a stale in-flight decision.

## Persistence

World state is persisted to a SQLite database at `DB_PATH` (`<module dir>/
state.db`), replacing the earlier monolithic `state.json` file. `_serialize_state()`
(sim_engine.py:9531) still builds the save payload under the lock, with the
same shape as before: top-level keys `version` (`STATE_VERSION = 2`,
sim_engine.py:31), `frameTick`, `savedAt` (UTC ISO timestamp), `roster_size`,
`civilization`, `agents`, `memory` (sets are serialized as sorted arrays,
`isThinking` is dropped, memory rows are vec-stripped for storage and
re-embedded on import).

`_connect_db(path)` opens a SQLite connection in WAL mode
(`synchronous=NORMAL`) and idempotently runs the schema DDL. The schema has
four tables: `meta(key, value)` (one row each for `version`, `frameTick`,
`savedAt`, `roster_size`); `civ(key, value)` (one row per top-level
`civilization` key, value JSON-encoded); `agents(name PK, ord, data)` (one row
per agent, `data` JSON-encoded, `ord` preserving roster order on load); and
`memory(rowid_pk, id, agent, text, salience, kind, tier, frame_tick, ts)`.

`_write_state_db(path, payload)` performs a full rewrite on every save: it
upserts `meta`, then deletes and re-inserts all `civ`/`agents`/`memory` rows,
all inside a single transaction, followed by a `wal_checkpoint`. `save_state()`
serializes the payload under the lock, then writes it outside the lock via a
per-call connection (`_write_state_db`) and never raises — the single-
transaction commit gives crash safety without the old tmp-file-plus-rename
trick. A dedicated `SimSaver` daemon thread calls `save_state()` every
`AUTOSAVE_SECONDS = 10` s (sim_engine.py:33, 9523-9529), unchanged. `atexit`
and signal handlers in server.py additionally flush a final save on graceful
shutdown (server.py:3420-3439).

`_read_state_db(path)` checks the file exists, connects, and returns the same
payload dict shape as `_serialize_state()` produced, or `None` if the file is
missing or `meta.version` isn't present. `restore_state()` (sim_engine.py:9613)
accepts only `STATE_VERSION = 2` — the old v1→v2 migration
(`_migrate_v1_to_v2`, which seeded `districts`/`roadNodes`/`roadEdges`/
`frontierPlots`/`districtProjects` from the starter blueprint for pre-districts
saves) has been removed. The `setdefault`/flag-gated backfill chain for
everything added since v2 (basePopulation, effect/reorg/role-switch state,
rule diversity tracking, spoilage nudges, etc.) still runs on every restore,
for forward-compat with DBs saved under older feature-flag sets.
`clear_state()` deletes `state.db` along with its `state.db-wal` and
`state.db-shm` sidecar files for a cold start.
