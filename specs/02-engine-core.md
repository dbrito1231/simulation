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
