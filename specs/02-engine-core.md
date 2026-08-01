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
| (unconditional) | 150 | a fixed batch: `_maybe_feed_starving`, `_maybe_repair_critical`, `_maybe_repair_campaign`, `_maybe_cull_ruins`, `_maybe_abandon_stalled_projects`, `_maybe_relocate_stuck_project`, `_maybe_reorganize_structures`, `_maybe_force_contribution`, `_maybe_start_idle_district_project`, `_maybe_build_funded_project`, `_maybe_start_approved_custom`, `_maybe_retire_blueprint`, `_maybe_amnesty_rejected_blueprints`, `_maybe_retire_custom_resource`, `_maybe_invention_backstop`, `_maybe_found_district`, `_maybe_welcome_newcomer` |
| within the 150-batch, `SAGE_REVIEW_ENABLED` | 150 | `_maybe_skip_sage_review`, `_maybe_amnesty_denied_sage_reviews` |
| within the 150-batch, `TECH_TREE_ENABLED` | 150 | `_maybe_era_transition`, `_maybe_dissolve_council` |
| `DAILY_COUNCIL_ENABLED` | day boundary (`frameTick % DAY_FRAMES == 0`) and deterministic phase gate | `_maybe_convene_daily_council`, `_maybe_advance_daily_council` |
| within the 150-batch, `CULTURE_ENABLED` | 150 | `_maybe_study_at_library` |
| within the 150-batch, `CEMETERY_ENABLED` | 150 | `_maybe_handle_burials` |
| within the 150-batch, `ECONOMY_ENABLED` | 150 | `_maybe_mint_coin`, `_maybe_fund_project_coin` |
| within the 150-batch, `path1_on()` | 150 | `_maybe_found_settlement`, `_path1_industry_benchmark` |
| `path1_on("PRESSURE_LOOP_ENABLED")` | 900 | `_tick_wildlife` (Path-1 forest attack pressure — **not** huntable fauna) |
| `path1_on("PRESSURE_LOOP_ENABLED")` and `_is_night()` | 30 | `_tick_night_pressure` |
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

When `ALWAYS_ON_MODULES` is enabled, district arrival marks that agent's module
context dirty; inbox delivery and hunger/health threshold crossings do the
same. Action application marks its actor dirty, and project/build/rule/role/
belief events plus season turns mark the affected village context dirty. The
pulse submits only up to the free PIANO-pool slots and never joins a module
future while holding the tick lock.

## Time model

All calendar fields are **derived from `frameTick`** — nothing calendar-shaped is
persisted separately. `_calendar()` (sim_engine.py) is a pure function of
`frameTick`: returns `year`, `season`, `dayOfSeason`, `daysPerSeason`, `isNight`,
`dayFraction`. The `/state` JSON shape is unchanged across the retune.

**Invariants (must hold):**

- `YEAR_FRAMES % DAY_FRAMES === 0` — whole days per in-world year.
- `SEASON_FRAMES = YEAR_FRAMES // 4` — four equal seasons per year.
- `RUIN_CULL_AGE_FRAMES = DAY_FRAMES` — ruin cull age stays ~one sim day.
- `GOODS_TICK_FRAMES = 900` (~30 s) and `LIFECYCLE_TICK_FRAMES = 300` are
  **unchanged** — micro cadences decouple from day length.

**Calendar stretch (2026-07-31, atmosphere Phase 4b):** uniform **+33% real-time**
lengthening. Ratios preserved: **24 days per year**, **6 days per season**,
`daysPerSeason = SEASON_FRAMES // DAY_FRAMES = 6`.

| Constant | Before (frames) | After (frames) | Real-time before | Real-time after |
|---|---|---|---|---|
| `TICKS_PER_SEC` | 30 | 30 | — | — |
| **`DAY_FRAMES`** | **13,500** | **18,000** | **7.5 min** | **10.0 min** |
| **`YEAR_FRAMES`** | **324,000** | **432,000** | **3.0 h** | **4.0 h** |
| **`SEASON_FRAMES`** | **81,000** | **108,000** | **45 min** | **60 min** |
| Days per year | 24 | 24 | — | — |
| Days per season | 6 | 6 | — | — |

**Canonical constants (sim_engine.py:1032, 1096–1097):**

- `TICKS_PER_SEC = 30`; `DAY_FRAMES = 18000` — one day/night cycle = 600 s
  (10.0 min real time at 30/s).
- `YEAR_FRAMES = 432_000` — one in-world year = 24 × `DAY_FRAMES` = **4.0 h**
  real time (exactly 24 day/night cycles).
- `SEASON_FRAMES = YEAR_FRAMES // 4 = 108_000` — one season = 6 × `DAY_FRAMES`
  = **60 min** real time (exactly 6 day/night cycles).

**Derived impacts (auto from formulas):**

| Dependent | Before | After |
|---|---|---|
| `AGE_YEARS_PER_TICK` | `300/324000 = 1/1080` | `300/432000 = 1/1440` |
| Sim lifespan 0→90y (wall) | ~11.25 h | ~15.0 h |
| Shelter tick (`_tick_shelter`) | every 7.5 min | every 10 min |
| `RUIN_CULL_AGE_FRAMES` | 13,500 (~7.5 min) | 18,000 (~10 min) |
| Ecology `SEASON_REGROW_MULT` | unchanged per-season mults | same mults, longer seasons |

`AGE_YEARS_PER_TICK = LIFECYCLE_TICK_FRAMES / YEAR_FRAMES` (sim_engine.py:1223) —
**exactly one in-world year per `YEAR_FRAMES`**, so agent aging, the season clock,
and the GUI calendar stay locked to the same canonical year. With today's constants
that is `1/1440` per lifecycle gate (~10 s at 30/s).

- `NIGHT_FRACTION = 0.25` (sim_engine.py): `_is_night()` is true for the last
  quarter of each `DAY_FRAMES` cycle, but only when `PRESSURE_LOOP_ENABLED` — night
  otherwise never triggers.
- `SEASON_REGROW_MULT = {"spring": 2, "summer": 1, "autumn": 1, "winter": 0}`
  (sim_engine.py:1099): ecology regrowth is doubled in spring and fully halted in
  winter (`_tick_ecology_regrow`, applied only when `ECOLOGY_ENABLED`).
- A scheduled Daily Council may convene once at the deterministic day boundary
  (`frameTick % DAY_FRAMES == 0`) when `DAILY_COUNCIL_ENABLED` is on. A leaderless
  lifecycle succession is the bounded emergency exception and convenes on the next
  `RULES_TICK_FRAMES` recovery pass. The persisted, tick-gated phase machine is
  specified in [09-systems-society.md](09-systems-society.md). It never adds a
  worker-pool slot: a council speaking/voting turn replaces the selected agent's
  ordinary think turn.

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
  the 11 non-elder `roles.json` seed roles (one generated agent per role
  before any role repeats) — a generated agent's zone is copied from the
  hand-written def that shares its role, so it spawns in a district that
  actually supports that role. Generated agents are built by the same
  `_make_agents` as hand-written ones and are indistinguishable to every
  other system (roles, beliefs, relationships, think scheduling); they just
  carry pool-drawn flavor text instead of bespoke hand-authored text.
  `civilization["basePopulation"]` reflects the full `roster_size` (clamped to
  `MAX_ROSTER_SIZE`, not `len(AGENT_DEFS)`), so the Structure-Effects house
  population cap (specs/08) computes correctly above 12 agents too.
- `_maybe_welcome_newcomer` (sim_engine.py, the house-driven backstop in the
  150-tick batch — see the gate table above) grows a running village that
  never cold-started above 12 agents: it draws the next unused `AGENT_DEFS`
  entry first, and once all 12 are occupied, falls back to the same
  `_generated_agent_defs(MAX_ROSTER_SIZE - len(AGENT_DEFS))` pool the
  `roster_size > len(AGENT_DEFS)` cold-start path uses, so this growth path
  can also reach `MAX_ROSTER_SIZE` instead of silently stalling at 12. This
  is deliberately the deterministic `_generated_agent_defs` pool, not
  `_next_agent_slot`'s random `Villager{id}` style (used for births): a
  newcomer at a given slot index looks identical whether the village started
  large or grew into that slot via housing.

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
`civilization`, `agents`, `memory`, `council_transcript` (sets are serialized as sorted arrays,
`isThinking` is dropped, memory rows are vec-stripped for storage and
re-embedded on import).

`_connect_db(path)` opens a SQLite connection in WAL mode
(`synchronous=NORMAL`) and idempotently runs the schema DDL. The schema has
four tables: `meta(key, value)` (one row each for `version`, `frameTick`,
`savedAt`, `roster_size`); `civ(key, value)` (one row per top-level
`civilization` key, value JSON-encoded); `agents(name PK, ord, data)` (one row
per agent, `data` JSON-encoded, `ord` preserving roster order on load); and
`memory(rowid_pk, id, agent, text, salience, kind, tier, frame_tick, ts)`; and
`council_transcript(rowid_pk, meeting_id, who, type, text, feeling, frame_tick,
ts)`. The latter is the full human/audit record for Daily Council events, not
prompt context.

`_write_state_db(path, payload)` performs a full rewrite on every save: it
upserts `meta`, then deletes and re-inserts all `civ`/`agents`/`memory`/
`council_transcript` rows, all inside a single transaction, followed by a
`wal_checkpoint`. `save_state()`
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

Daily Council transcript persistence deliberately mirrors `memory`: the engine
owns an authoritative in-RAM `council_transcript_rows` list; appending a live
transcript event appends its durable row immediately; serialization exports the
whole list; and DB save deletes then re-inserts the whole table atomically.
Restore rehydrates that list. At adjourn, after rows for the meeting are
appended, retention keeps only the newest
`DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS = 30` distinct `meeting_id` values.
This audit table is never folded into an LLM prompt; the bounded digest in the
`civ` blob is the prompt-facing record (see [03](03-cognition.md)).
`clear_state()` deletes `state.db` along with its `state.db-wal` and
`state.db-shm` sidecar files for a cold start.

## Sovereign God mode (Phase 2 — secure kernel)

`GOD_MODE_ENABLED` (sim_engine.py, env-backed via `SIM_GOD_MODE`, read once at
import — see [01](01-architecture.md)) gates a second, optional control plane.
Every entry point below re-checks the flag itself and no-ops with
`{"ok": False, "reason": "god mode disabled"}` when it is off; the token
check that gates HTTP access to these entry points lives in server.py (see
[04-http-api.md](04-http-api.md)).

**State shape.** `civilization["godState"]` persists wholesale with the rest
of `civilization` (no serializer change) and always exists, flag on or off:

```json
{
  "version": 1, "intervened": false, "nextInterventionSeq": 1,
  "providence": null, "privateOmens": {}, "activeEvents": [],
  "recentInterventions": []
}
```

`_default_god_state()` builds this; `_normalize_god_state(raw)` is the
restore-time normalizer, called unconditionally (same setdefault-only
back-compat discipline as every other phase) so an old save with no
`godState` key, or one with a malformed nested field, rehydrates to a
conservative default rather than raising. `reset()` assigns a fresh god
state via the same `_reset_world` path that seeds it at cold start.
`recentRequests` (the idempotency store) is deliberately **not** part of this
persisted shape — see below.

**Preview cache.** `self._god_preview_cache` (previewId → record) is
in-memory only, never persisted, bounded to `GOD_PREVIEW_CACHE_MAX = 32`
entries each valid `GOD_PREVIEW_TTL_SECONDS = 60` wall-clock seconds (not
frame-based — a preview is a request-scoped concept). `god_preview(envelope)`
validates and normalizes a `{kind, payload, expectedFrame}` command with NO
mutation, computes a canonical SHA-256 digest of the normalized command, and
inserts a record binding `previewId` → `{normalizedCommand, commandDigest,
previewFrame, fingerprint}`. Cleared on `reset()` and on `restore_state()`
(a restart/restore invalidates every outstanding preview, per the plan).

**Idempotency store.** `self._god_requests` (requestId → outcome) is
in-memory only, bounded to `GOD_REQUEST_CACHE_MAX = 100` entries, also
cleared on `reset()`/`restore_state()`. `god_apply(preview_id, request_id)`:
resolves the preview, checks the idempotency store first (an exact
`requestId` replay returns the stored response without re-applying; the same
`requestId` bound to a *different* preview/digest is a conflict that applies
nothing), then — for a fresh `requestId` — revalidates the normalized command
against current state (re-runs the same validator, recomputes and compares
the digest, rechecks the precondition fingerprint), applies atomically under
the lock, stores the response, and consumes the preview (single-use once
applied).

**Command catalog (Phase 2).** `kind == "proclamation"` is applyable:
`{"kind": "proclamation", "payload": {"text": str}}`. `text` passes through
`_normalize_divine_text` (Unicode NFC; rejects NUL and C0/C1 controls other
than space; rejects embedded newlines; enforces both a 240-character and a
600-UTF-8-byte cap post-normalization — the byte cap is intentionally tighter
than `4 * 240` so it is load-bearing, not merely redundant with the character
cap) and is stored as plain text, never HTML. Applying a proclamation
consumes `godState["nextInterventionSeq"]` for an id like `divine-1`, sets
`intervened = True` (monotonic `false → true`), and writes an activity line,
a `conversationLog` entry (`kind="divine_proclamation"`, `source="divine"`),
and a chronicle entry (`kind="divine"`, `source="divine"`) — all with
explicit non-emergent attribution. `story_event` (Phase 5, timed/composite)
still validates far enough to be rejected cleanly with "not implemented in
this phase"; an unrecognized kind is rejected as unknown.
`god_cancel(target_id)` is plumbing only — there is nothing it handles;
`revoke_guidance` (Phase 3, below) is the real cancellation path for
providence and private omens, and it always returns a clean
`{"ok": True, "cancelled": False, "reason": "nothing to cancel"}`. Phase 4
adds three more applyable kinds (below); none of them is cancellable, so
both `god_cancel` and `revoke_guidance` continue to refuse to touch them —
see "Sovereign God mode (Phase 4 — bounded immediate miracles)".

**Expiry.** `_expire_divine_effects()` is a bounded scan (capped at
`GOD_ACTIVE_EVENTS_CAP = 8` for `activeEvents`) that marks any expired entry
— `activeEvents`, the single `providence` slot, and every `privateOmens`
record — as `expired` (or `restore-closed` when called from
`restore_state()`) exactly once, leaving already-closed entries untouched.
Called every tick immediately after `frameTick` advances in `_tick_once`
(before every other consumer) and once more after restore rehydration. Phase
2 never populates `activeEvents`, so that part of the scan is currently a
cheap no-op that proves the call sites are wired before Phase 5 gives it real
work; providence/omen expiry is live from Phase 3 (below).

**Benchmarks.** When `GOD_MODE_ENABLED`, `_sample_benchmarks()` adds
`lastBenchmarks["intervened"]` and logs a `god_interventions` metric with
`intervened`/`active_effects`/`rejected_commands` detail — see
[12-ops.md](12-ops.md).

## Sovereign God mode (Phase 3 — voice and providence)

Three more catalog kinds are applyable, and `god_sight` gains the per-agent
omen status field this section describes. Cognition-side rendering (the two
prompt lines, the elder-directive separation) is [03](03-cognition.md).

**`providence`** — `{"text": str, "durationFrames": int?}`. One active public
non-binding line at a time, stored in `civilization["godState"]["providence"]`
as `{id, text, createdFrame, expiresFrame, visibility: "public"}`.
`durationFrames` is optional (default `GOD_GUIDANCE_DEFAULT_DURATION_FRAMES =
5400`, ~3 minutes, mirroring `DIRECTIVE_TTL_FRAMES`) and is silently clamped
into `GOD_GUIDANCE_MIN_DURATION_FRAMES..GOD_GUIDANCE_MAX_DURATION_FRAMES`
(`300..54000`, ~10s–30min) rather than rejected when out of range. Applying
writes the same activity/`conversationLog`/chronicle trio as a proclamation
(`source="divine"`) — providence is public per the plan's Visibility rule.
Replacing an active providence is allowed, but only in the disclose-then-
replace sense: `_god_target_fingerprint` records the current providence id
(or `None`) as `{"outgoingId": ...}` at preview time, and `_god_check_fingerprint`
recomputes it fresh at apply time — a mismatch (the providence changed
between preview and apply) rejects with "providence changed since preview".
On a successful replace the outgoing record is closed through
`_close_providence("replaced")` before the new one is written, so it is
logged exactly once regardless of how it ends.

**`private_omen`** — `{"targetId": int, "text": str, "durationFrames": int?}`.
`targetId` must resolve to a living agent (`_find_agent_by_id`); an unknown or
deceased (`deathFrame is not None`) target is rejected before the text is
even normalized. Stored in `civilization["godState"]["privateOmens"]`, keyed
**only** by `str(agent["id"])`, one record per agent:
`{id, targetId, targetName, text, createdFrame, expiresFrame,
memoryWritten}`. `targetName` is a non-authoritative display snapshot only.
Never touches public activity/`conversationLog`/chronicle. Replacement
follows the same disclose-then-replace fingerprint mechanism as providence,
keyed per-target.

**`revoke_guidance`** — `{"id": str}`. Ends an active providence or private
omen early by its intervention id, whichever it matches (checked in that
order); an id that no longer resolves to anything active — already expired,
already replaced, already revoked, or never existed — is rejected with
"guidance id not found or already inactive" and consumes no intervention
sequence number. `_god_target_fingerprint` records `{"targetKind":
"providence"|"private_omen"|None, "existed": bool}` at preview time so a
stale revoke (its target already closed by something else in the meantime)
is caught at apply time the same way a stale replace is.

**Closure and the memory contract.** `_close_providence(status)` and
`_close_omen(key, status)` are the single choke point every ending path
(expiry, revocation, replacement) routes through, so each is logged exactly
once via `_log_divine` regardless of which path closed it.
`_close_providence` clears the single `providence` slot; providence carries
no memory-write contract. `_close_omen` is guarded by the record's own
`memoryWritten` flag: **while an omen is active it is never written to the
target's ordinary memory** — only reachable through the dedicated prompt
line (`03-cognition.md`) — and **exactly once**, on whichever of
expiry/revocation/replacement closes it first, its text is written via
`_push_memory(agent, text, kind="divine_omen")` and `memoryWritten` is set
`True` before the record is deleted from `privateOmens`. A second closure
attempt on an already-deleted key is a no-op by construction (nothing left
to close); `_normalize_god_state`'s restore-time normalizer preserves
`memoryWritten` (defaulting it to `False` only when genuinely absent), so a
save captured mid-way through an unwritten omen still fires its memory
exactly once on the next restore-time `_expire_divine_effects(restore=True)`
sweep, and a save captured *after* that write never fires it again.

**Visibility.** `providence` is public: it rides the same
activity/`conversationLog`/chronicle path as a proclamation, appears in
`snapshot()["god"]["providence"]`, and its `recentInterventions` records set
`"public": True`. Private omens are the opposite by construction: every
`recentInterventions` record Phase 3 writes for a `private_omen` apply,
replace, or a `revoke_guidance` targeting one sets `"public": False`, and
`snapshot()`'s `recentPublicInterventions` filters strictly on that flag —
this is the one guard standing between a private omen and a public `/state`
leak, so every new intervention-recording call site MUST set `"public"`
explicitly. `god_sight(filters)`'s per-agent projection exposes omen
**status** only — `{"active": true, "expiresFrame": int}` or `None` — never
the omen's text; the text itself is still reachable through
`recentInterventions` in the same `god_sight` response (an "intervention
outcome", explicitly in scope for the authenticated sight route) or by the
operator recalling what they wrote. No omen content ever reaches unauthenticated
surfaces.

## Sovereign God mode (Phase 4 — bounded immediate miracles)

Three catalog kinds become applyable: `agent_vitals`, `grant_resource`,
`structure_condition`. All three are **irreversible**
(`_god_reversibility_class`'s default branch already covers any kind that is
not `providence`/`private_omen`, so no change was needed there) and **public**
(every `recentInterventions` record they write sets `"public": True` — there
is no private-omen-style visibility boundary for a vitals/resource/structure
change). Each is source-attributed exactly like a proclamation: one activity
line, one `conversationLog` entry (`source="divine"`, kind
`divine_vitals`/`divine_grant`/`divine_structure`), and one chronicle entry
(`kind="divine"`, `source="divine"`) — a divine effect is never disguised as
an emergent one. Each consumes one `_next_intervention_id()`, appends one
`recentInterventions` record, and writes one `divine.jsonl` `"applied"`
record through the same shared machinery Phases 2–3 use. None of the three is
cancellable: `god_cancel` only ever looks for something to cancel among
`activeEvents`/`providence`/`privateOmens`-shaped state (Phase 4 adds none),
and `_god_apply_revoke_guidance` only ever matches an id against the
`providence` slot or a `privateOmens` record — a Phase 4 intervention id can
never match either, so both refuse by construction with no kind-specific
carve-out required.

**Preview outcome.** `god_preview()`'s response gains a `previewOutcome`
field: the exact clamped/bounded value the corresponding miracle would apply
right now, computed against current live state by `_god_preview_outcome()`
using the identical arithmetic the matching `_god_apply_*` helper uses.
`None` for every Phase 2/3 kind (nothing to preview) and for a Phase 4 target
that has vanished since preview (apply-time revalidation, not this field, is
the authoritative rejection path for that). As long as nothing else mutates
the same target between preview and apply, the applied outcome equals the
previewed one exactly.

**`agent_vitals`** — `{"targetId": int, "healthDelta": number?,
"hungerDelta": number?}`. `targetId` must resolve to a living agent; at least
one delta must be present and non-zero; each delta's magnitude is capped at
`GOD_VITALS_DELTA_MAX = 100` independently. Health and hunger are each
clamped through the same 0..100 range `_update_survival` clamps them to, with
one deliberate asymmetry: **v1 cannot kill.** Health `<= 0` is
`_update_survival`'s incapacitation threshold, not death — permanent death
only ever happens through `_agent_dies` (old age today), never as a direct
consequence of health hitting 0 — but a negative `healthDelta` is still
clamped to stop at `GOD_VITALS_HEALTH_FLOOR = 1`, one full point above that
threshold, so the miracle itself can never be the thing that flips
`incapacitated = True`, and it never touches `deathFrame`,
`incapacitated`, or any lifecycle-succession state directly. Hunger has no
such floor — hunger reaching 0 does not incapacitate or kill by itself (it
only changes which branch the *next* `_update_survival` tick takes) — so
`hungerDelta` clamps to the ordinary `0..100` range on both ends. See
[06-agents.md](06-agents.md) for the full no-kill contract.

**`grant_resource`** — `{"resourceId": str, "amount": int,
"target": "stockpile" | {"agentId": int}?}` (`target` omitted or `"stockpile"`
defaults to the village stockpile). `resourceId` must exist in the live
`civilization["resourceRegistry"]` (the same known-resource registry the
cognition prompt and blueprint validation read) — anything else is rejected
as "unknown resource id". `amount` is a positive integer, capped per command
at `GOD_GRANT_PER_COMMAND_CAP = 200` and cumulatively across every applied
`grant_resource` command this process lifetime at `GOD_GRANT_SESSION_CAP =
2000` (tracked by `self._god_grant_session_total`, in-memory only, reset by
`reset()` like every other non-persisted God-mode counter — never
`state.db`). A grant to an agent target must resolve to a living agent and
respects the exact carry-cap semantics `_perform_gather` and every other
gain-resource path already use (`_carry_cap(agent)`): it fills the agent's
remaining carry room first, then routes any remainder to the village
stockpile — the same two sinks every normal path already writes to, never a
third bypass sink. See [08-systems-economy.md](08-systems-economy.md) for the
full split-arithmetic contract.

**`structure_condition`** — `{"structureId": int, "delta": number}`.
`structureId` must resolve to a structure that is not ruined
(`isRuin` false and `condition > 0`) — an unknown or already-ruined structure
is rejected before `delta` is even checked. `delta` must be non-zero and its
magnitude capped at `GOD_STRUCTURE_DELTA_MAX = 100`. Both repair (`delta >=
0`) and damage (`delta < 0`) are applied through
`_apply_structure_condition_delta(structure, delta)` — the SAME helper
`_tick_structure_decay` calls per goods tick (it was extracted from that
tick's per-structure body specifically so both callers share it), clamping to
`0..100` and firing the exact `STRUCTURE_DISREPAIR_THRESHOLD`-crossing and
ruin-transition narration (including the `homeOf`/`homeStructureId` homeless
handling) a natural decay collapse would. **Single-structure scope:** this
Phase 4 miracle still targets one non-ruined structure only — it cannot
un-ruin a collapsed structure. Batch un-ruin and registry deletion are
separate town-integrity commands (`repair_structures`, `clear_ruins`; see
below). **Registry contract (amended):** passive decay, disasters, and this
miracle's damage path still produce ruins rather than deleting registry
entries — a structure that reaches 0 becomes a ruin, exactly like natural
decay, and remains in `civilization["structures"]` until culled by
`_maybe_cull_ruins()`, removed by God `clear_ruins`, or pruned offline. See
[05-world.md](05-world.md) for the shared decay/ruin helper contract and
[08-systems-economy.md](08-systems-economy.md) for repair campaigns, ruin
cull, and disaster retune.

## Sovereign God mode (Town integrity — mass structure repair and ruin clearance)

Two additional irreversible, public apply kinds extend the Phase 4 miracle
set for operator escape hatches when ruin pressure outruns autonomous repair.
Both use the same `god_preview` / `god_apply` pipeline, write one
`divine.jsonl` `"applied"` record each, set `godState.intervened = True`
(monotonic), and are wired into the Divine Console **Miracles** tab alongside
the Phase 4 trio ([11-viewer.md](11-viewer.md)). Neither is cancellable via
`god_cancel` (same irreversible class as `structure_condition`). Field
schemas and caps are advertised in `/control/god/capabilities`
([04-http-api.md](04-http-api.md)).

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

One more catalog kind becomes applyable — `story_event` — and `activeEvents`
goes from an empty plumbing-only list to real, timed, composable state.
`god_cancel` is wired for real (it was a stub through Phase 4). See
[08-systems-economy.md](08-systems-economy.md) for the full arithmetic
contract at every consumer site and [09-systems-society.md](09-systems-society.md)
for the divine-vs-village-law composition rule.

**`_divine_modifier(key, default=1.0)`.** The single read path every
consumer site uses. Returns `default` immediately when `GOD_MODE_ENABLED` is
false, when `godState` is missing/malformed, or when no `activeEvents` entry
currently carries `key` within `[startFrame, expiresFrame)` — the same
expiry predicate Phase 2 established, so a modifier stops influencing
*exactly* at `expiresFrame`, before the next `_expire_divine_effects` cleanup
sweep even runs. With no active effect for `key` this returns exactly `1.0`,
so an untouched (or all-1.0) run is byte-identical to the feature-off
baseline — every consumer site multiplies its own local delta/amount by this
value rather than branching on whether God mode is enabled.

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

Base module constants (`HUNGER_RATE`, `HEALTH_RATE`, `HEALTH_REGEN`,
`STRUCTURE_DECAY_PER_GOODS_TICK`, `SPOILAGE_RATIO`) are unchanged; every
consumer site multiplies its own local value by `_divine_modifier(key)` at
the existing calculation site, in the existing order, before the existing
clamp — see [08-systems-economy.md](08-systems-economy.md) for the exact
per-site ordering, the gather zero-path contract, and why `COLLAPSE_REGEN`
is deliberately unreachable through `health_regen_multiplier`.

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
At most `GOD_STORY_EVENT_MAX_MODIFIERS = 7` modifier keys and
`GOD_STORY_EVENT_MAX_PRIMITIVES = 5` primitives per event. Each primitive is
validated by calling `_validate_god_envelope({"kind": ..., "payload": ...})`
for that primitive kind directly — the exact same validator (and therefore
the exact same bounds/rejections) the standalone Phase 4 command uses — and
the normalized primitive list stored in the event is itself idempotent under
re-validation, which is what makes apply-time revalidation safe. An optional
`providence` sub-payload reuses the event's own `durationFrames` (it does not
carry a separate one) and is applied through the same `_god_apply_providence`
path a standalone `providence` command uses, so it gets its own intervention
id, `recentInterventions` record, and the standard disclose-then-replace
guard against whatever else is occupying the providence slot.

**One active value per key.** A new `story_event` (or a replacement) whose
`modifiers` name a key another **active** `activeEvents` entry already
carries is rejected — `"modifier '<key>' already has an active effect (id
<id>) -- supply replaceEffectId to replace it"` — unless the command names
that exact event's id as `replaceEffectId`. `replaceEffectId` must itself
resolve to a currently-active event or the whole command is rejected before
anything else is validated. This occupancy check re-runs against *current*
live state both at `god_preview` time and again at `god_apply`-time
revalidation (the same generic revalidate-then-apply sequence every other
kind uses), so a key that was grabbed by something else between preview and
apply is caught there rather than needing its own bespoke fingerprint.

**Atomicity.** `_validate_god_story_event` validates every sub-component —
title, narration, visibility/target, duration, every modifier, every
primitive (recursively, via the shared per-kind validators), the optional
providence text, and the `replaceEffectId` conflict check — before returning
a normalized command; a single invalid component rejects the *whole*
envelope with no partial normalization. Because `god_apply` always
revalidates the full normalized command before calling
`_god_apply_story_event`, that method itself never has a rejection path to
guard against — every step it takes always succeeds, so "accepts all or
changes nothing" holds by construction rather than needing a rollback path.

**Applying** a `story_event` (`_god_apply_story_event`): optionally closes a
named `replaceEffectId` event first (`_close_story_event(event, "replaced")`,
see below); mints one intervention id for the event itself; applies every
primitive by calling the *same* standalone `_god_apply_agent_vitals` /
`_god_apply_grant_resource` / `_god_apply_structure_condition` helpers Phase
4 uses (each still mints its own intervention id and writes its own
`recentInterventions`/`divine.jsonl` record, tagged with `parentEventId` so
every sub-effect stays traceable back to the one event id); optionally sets
providence; then appends one `activeEvents` record —
`{id, kind: "story_event", title, narration, visibility, targetId,
createdFrame, startFrame, expiresFrame, status, modifiers,
primitiveInterventionIds, providenceId, replaces}` — via
`_god_events_insert`, which bounds the ring at `GOD_ACTIVE_EVENTS_CAP = 8` by
evicting the oldest **closed** entry first (falling back to the oldest
active one only if every slot happens to be active, a backstop the cap plus
normal expiry makes an edge case rather than the common path). A public
event also writes the standard activity/`conversationLog`/chronicle trio
(`source="divine"`); a private one writes none of those, matching the
private-omen visibility boundary.

**Reversibility.** `_god_reversibility_class` now takes the full normalized
command (not just the kind string, so it can inspect `story_event`'s
payload): a `story_event` with no `primitives` is **cancellable** (only
timed modifiers and/or providence, both revocable); one *with* primitives is
**consequential** — cancelling it stops the modifiers/providence from
influencing anything further, but the primitives it already applied are
irreversible mutations by their own nature and are never retracted. Preview
surfaces this distinction directly in `reversibilityClass`.

**Closure — `_close_story_event(event, status)`.** The single choke point
`_expire_divine_effects`, `god_cancel`, and a replacing `story_event` all
route through, so an event closes (and logs) exactly once regardless of
which path closes it. If the event carries a `providenceId` and that id is
*still* the active `godState["providence"]["id"]` (a later, unrelated divine
command may already have replaced it independently, in which case there is
nothing left here to touch), the linked providence is closed too through the
same `_close_providence` path expiry/revocation/replacement already share —
so cancelling or expiring an event with embedded providence closes both in
one step, logged once each.

**Expiry.** `_expire_divine_effects` (Phase 2's bounded scan, previously a
no-op for `activeEvents`) now calls `_close_story_event` on every entry whose
`expiresFrame` the current `frameTick` has reached, exactly as it already did
conceptually for providence/omens. Because `_divine_modifier` itself checks
`startFrame <= frameTick < expiresFrame`, a modifier stops influencing the
instant `frameTick` reaches `expiresFrame` — *before* this cleanup sweep
next runs, not because of it; the sweep only closes the record and emits its
one audit/narration line. Restore-time closure (`restore=True`) runs once
after rehydration and marks anything already past its absolute
`expiresFrame` as `"restore-closed"` rather than replaying it.

**`god_cancel(target_id)` — now real.** Checks, in order: the active
`providence` slot, every `privateOmens` record, then every `"active"`
`activeEvents` entry, matching on `id`. A match closes that record through
its normal closure path (`_close_providence`, `_close_omen`, or
`_close_story_event`) and returns `{"ok": true, "cancelled": true,
"targetId": ..., "targetKind": "providence"|"private_omen"|"story_event"}`.
No match — including any id minted by an irreversible Phase 4 miracle
(`agent_vitals`/`grant_resource`/`structure_condition`) or a one-shot
`proclamation`, none of which is ever stored in any of the three searched
stores — returns the same `{"ok": true, "cancelled": false, "reason":
"nothing to cancel", "targetId": ...}` shape Phase 2 already established, so
a miracle id is refused by construction rather than through a kind-specific
carve-out. This is a direct, lock-held mutation with no preview/apply step,
matching the shape Phase 2 already gave this route.

**Preview — divine vs. custom-rule composition.** When a `story_event`
preview's `modifiers` include `gather_yield_multiplier` or
`fish_yield_multiplier`, `_god_preview_outcome` adds a `customRuleContext`
field: a compact summary (`{ruleId, subject, value}` per entry) of every
currently-enacted village custom rule that modifies `collect_resource`,
mirroring `_custom_rule_modifier`'s own matching logic. This is additive —
the divine multiplier is *never* replaced by or merged into the custom-rule
value — so an operator previewing a divine gather effect on top of an
existing village law sees both contributions named separately, because the
consumer site genuinely composes them (see
[08-systems-economy.md](08-systems-economy.md)).

**Restore.** `activeEvents` round-trips through `save_state()`/
`restore_state()` like the rest of `godState`; `expiresFrame` is always an
absolute frame number, so it needs no re-basing on restore. A restored event
already past its `expiresFrame` is closed once by the restore-time
`_expire_divine_effects(restore=True)` sweep, exactly like providence/omens.

## Sovereign God mode (Phase 6 — divine weather override)

`weather_override` is a new `activeEvents` kind that plugs into the exact
event-integration machinery Phase 5 built above, rather than adding a
parallel one. Full behavior (validation, RNG discipline, the natural-cycle
handoff, reversibility) is documented once, alongside the weather state
machine it drives, in [05-world.md](05-world.md#divine-weather-override-sovereign-god-mode-phase-6-weather_override) —
this section only notes its participation in the shared plumbing above:

- **Closure** goes through `_close_weather_override(event, status)`, a
  weather-specific counterpart to `_close_story_event` used by exactly the
  same three callers (`_expire_divine_effects`, `god_cancel`, and a
  replacing `weather_override`), so a `weather_override` closes exactly once
  regardless of which path closes it, just like a `story_event` does. Unlike
  `_close_story_event`, it has no linked-providence step — a
  `weather_override` never carries one — but it does always call
  `_weather_handoff_successor` before returning, which is its one required
  extra step beyond simply marking the record closed.
- **Expiry** is dispatched from inside `_expire_divine_effects`'s existing
  `activeEvents` scan: an entry with `kind == "weather_override"` routes to
  `_close_weather_override` instead of `_close_story_event`, but is still
  found, capped (`GOD_ACTIVE_EVENTS_CAP`), and marked `"expired"` (or
  `"restore-closed"` when `restore=True`) by the same loop — no second scan.
- **Restore-time atomicity.** Because `_close_weather_override` (called from
  the `restore=True` sweep) both closes the record *and* performs the
  natural-cycle handoff in the same call, a save captured after an active
  override's `expiresFrame` has already passed closes and hands off in one
  atomic step on the very next restore — never a closed-but-not-handed-off
  (or handed-off-twice) intermediate state, and a second restore-time sweep
  against the same now-closed record is a verified no-op (the `status !=
  "active"` guard `_close_weather_override` shares with `_close_story_event`).

## Huntable wildlife (`WILDLIFE_ENABLED`)

Server-authoritative fauna subsystem, distinct from Path-1's
`_tick_wildlife` pressure event ([10-path1.md](10-path1.md)). Gate
`WILDLIFE_ENABLED` (default True; semantics also in
[05-world.md](05-world.md)): off → no fauna state, no `_move_wildlife` /
`_tick_huntable_wildlife`, `hunt_wildlife` omitted from `available_actions`,
viewer draw no-ops.

**State.** `civilization["wildlife"]` is a list of creature records owned
under the engine lock and persisted with the rest of civilization state
(cold-start seed + `restore_state` rehydrate). Per-creature fields include
at least: `id`, `kind`, `districtId`, `x`, `y`, `targetX`/`targetY`,
optional `waypoints`, `hp`, `maxHp`, `alive`, `respawnAt`. `maxHp` is set
on spawn from `WILDLIFE_MAX_HP[kind]` (HP tiers: low kinds ≈1–2 hits; mid
≈3–4; high `boar`/`seal` ≈5–6; decorative `bee` is not a combat
target). Kind pools and kill yields live in
[08-systems-economy.md](08-systems-economy.md); density caps
(`WILDLIFE_STAGE_COUNT` / `WILDLIFE_CAP_PER_DISTRICT = 4`) key off
`districtEcology` stage ([05-world.md](05-world.md)).

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

**`_tick_huntable_wildlife()`** — slower cadence
(`WILDLIFE_POP_TICK_FRAMES`, migrate check frames). Spawn into under-cap
habitat districts from the kind pool; respawn dead creatures whose
`respawnAt` has elapsed; cull / hold density to stage cap; run migration
checks (below).

**Cross-district migration.** Alive non-decorative creatures may, with small
probability on the pop tick, pick another district whose kind pool includes
their `kind` (forest↔forest, farm↔farm, beach↔beach only). Destination must
be under the stage cap. Path: reuse the **agent road pathfinder** / road
waypoint machinery when a road path exists between districts; else
straight-line toward the destination district center, then clamp into
habitat on arrival. On arrival update `districtId`, clear waypoints, resume
wander.

**Combat.** `hunt_wildlife` ([07-actions.md](07-actions.md)) damages under
the lock; kill grants `meat` or `fish` per the yield table. No separate
weapon inventory — damage is role-based only (`HUNT_DAMAGE_HUNTER` vs
`HUNT_DAMAGE`).

**`/state` projection.** Alive creatures only:
`wildlife: [{id, kind, districtId, x, y, hp, maxHp}]` — see
[11-viewer.md](11-viewer.md). The viewer does not pathfind fauna.

### Sovereign God mode: wildlife kinds

Three additional irreversible, public god apply kinds mutate
`civilization["wildlife"]` under the lock via the existing
`god_preview` / `god_apply` pipeline (outside `DECISION_ACTIONS` —
[01-architecture.md](01-architecture.md)). Field schemas are advertised in
`/control/god/capabilities`. Each applied intervention writes one
`divine.jsonl` `"applied"` record like other miracles
([12-ops.md](12-ops.md)):

| Kind | Payload (conceptual) | Effect |
|---|---|---|
| `wildlife_spawn` | `districtId`, `kind` (must be valid for that district's pool) | Spawns an alive creature at a habitat-legal position (respects cap when practicable; reject unknown kind/district) |
| `wildlife_despawn` | creature `id`, or a district clear | Marks target(s) dead / removes from the alive set |
| `wildlife_set_hp` | creature `id`, `hp` | Clamps and sets `hp` (and may kill if `hp <= 0`, with ordinary dead/respawn bookkeeping) |

None of the three is cancellable via `god_cancel` (same irreversible class as
`grant_resource` / Phase 4 miracles).
