# Plan: Smarter Civilization

**Status:** PLANNED
**Created:** 2026-07-16
**Implementation order:** Phases 0-6; each phase must pass its acceptance gate
before the next phase starts.

## Goal

Make agents complete useful work, learn from outcomes, and coordinate with the
right villagers without increasing routine LLM call volume or weakening the
server-authoritative architecture.

The engine is **assistive**: it owns facts, task state, ranking, validation, and
recovery recommendations. Agents may still choose any valid action. Existing
actions remain the execution interface; this plan adds no action verbs.

## Invariants

- Follow SDD: update the owning specs first, then code in the same phase.
- Mutate world, task, learning, and capability state only under the engine lock.
- Keep the browser read-only; `/state` exposes summaries, never client-owned logic.
- Preserve the action-sync invariant. Because this plan adds no actions,
  `DECISION_ACTIONS`, `DECISION_SCHEMA`, `apply_decision`, and `ACTION_LABELS`
  should remain unchanged.
- Keep `MAX_CONCURRENT_LLM = 3`; do not enable general thinking mode or PIANO fan-out.
- Every new collection persisted in `state.json` must be bounded and restored with
  `setdefault` migration. Flag-off behavior must omit its prompt/state sections and
  retain current decision behavior.
- Existing survival, Sage emergency, succession, and repair backstops always outrank
  task-board recommendations.

## Frozen data contracts

### Task record

Persist active tasks in `civilization["taskBoard"]`, capped at 64 records:

```json
{
  "id": "project:village_core:library:wood",
  "kind": "gather|craft|deliver|build|repair|survival|upkeep|teach",
  "status": "open|assigned|blocked|completed|expired",
  "priority": 0,
  "source": "project|structure|agent|system|commitment|recovery",
  "action": "collect_resource",
  "targetDistrict": "village_core",
  "targetId": "library",
  "resource": "wood",
  "amount": 3,
  "assignee": null,
  "blockedReason": null,
  "attempts": 0,
  "createdFrame": 100,
  "lastProgressFrame": 100,
  "expiresFrame": 5500
}
```

- IDs are deterministic from `kind/source/district/target/resource`; refresh updates
  an existing record instead of creating a duplicate.
- Priority 0: immediate survival/Sage emergency. Priority 1: funded build, critical
  repair, explicit commitment. Priority 2: active-project gather/craft/deliver.
  Priority 3: upkeep, teaching, upgrade, and non-blocking opportunity.
- Terminal records are logged and removed from persisted state on the next task-board
  refresh; aggregate counters retain history.
- A task becomes `completed` only from observed world progress, never from an LLM's
  claim. It becomes `blocked` after three failed compatible actions with no progress,
  and `expired` after `DIRECTIVE_TTL_FRAMES` without progress or when its source
  disappears. Dead/incapacitated assignees release the task immediately.

### Outcome record

Persist the newest 24 records per agent in `agent["outcomeHistory"]` and newest 64
village lessons in `civilization["operationalLessons"]`:

```json
{
  "frame": 200,
  "agent": "Aria",
  "action": "collect_resource",
  "taskId": "project:village_core:library:wood",
  "contextKey": "gather:wood:forest",
  "result": "success|rejected|no_progress|completed",
  "reasonCode": "ok|wrong_zone|depleted|missing_tool|missing_input|no_target|invalid",
  "progressDelta": 2,
  "travelFrames": 120,
  "resourceDelta": {"wood": 2}
}
```

- Reason codes come from engine gates and summary branches, not text parsing.
- Operational lessons aggregate a `contextKey`, success/failure counts, latest
  reason, and latest successful recovery; never store chain-of-thought.
- Retrieval returns at most three matching lessons and 360 characters total.

### Capability profile

Persist `agent["capabilityProfile"]` by task kind:

```json
{
  "gather": {"attempts": 10, "successes": 8, "progress": 16,
             "travelFrames": 900, "lastSuccessFrame": 1000}
}
```

Ranking score is deterministic:

`skill*4 + success_rate*20 + recent_success*5 - travel_distance/200 - active_load*8`

Role suitability is a tie-breaker, not a hard gate. Elder, healer, and builder
emergency protections remain unchanged.

## Phase 0 - Baseline and specifications

**Purpose:** freeze behavior and establish measurable before/after evidence.

1. Update specs first:
   - `01-architecture`: four new flags and lock/data-flow ownership.
   - `02-engine-core`: task refresh cadence, persistence, and migration.
   - `03-cognition`: prompt fields and token budgets.
   - `04-http-api`: `/state` summary shapes.
   - `06-agents`: outcome/capability agent fields.
   - `08-systems-economy`: task generation for survival/economy/projects.
   - `09-systems-society`: commitments, teaching, learning, and recovery.
   - `11-viewer`: read-only task panel.
   - `12-ops`: metrics and smoke/soak coverage.
2. Add a deterministic `scripts/smartness_smoke.py` harness using the same injected
   no-LM engine construction pattern as existing smokes.
3. Extend soak reporting with current fallback rate, action rejection rate, project
   progress latency, repeated identical failures, taskless idle turns, and prompt
   token percentiles.
4. Capture a baseline from the latest complete log session and record it in this
   plan's implementation log before behavior changes.
5. Record the development-agent model manifest in the implementation log: provider,
   exact model ID, reasoning effort, assigned phase/step, and fallback model. Verify
   that every implementation agent follows the model ceiling in **Model and change
   ownership** before dispatching Phase 1.

**Acceptance:** canonical specs define every contract above; baseline report runs on
existing logs; the model manifest is complete and uses only approved tiers; all
current smokes and `py_compile` pass unchanged.

## Phase 1 - Task board in shadow mode

**Flag:** `TASK_BOARD_ENABLED`, initially False; turn on after acceptance.

1. Add bounded task-board state and restore migration. Do not bump `STATE_VERSION`;
   use additive `setdefault` fields.
2. Implement `_refresh_task_board()` on the existing 150-frame deterministic batch.
   Derive tasks from active project shortfalls, craft prerequisites, funded builds,
   ruined/disrepaired structures, survival emergencies, light upkeep, and explicit
   commitments.
3. Implement deterministic task transitions and progress observation by comparing
   relevant world values before/after `apply_decision`.
4. Run in shadow mode: generate/rank/log tasks but do not alter prompts, goals,
   assignments, or fallback decisions.
5. Expose only aggregate task counters under `/state` during shadow mode.

**Acceptance:** no duplicate IDs; every active project shortfall produces a viable
task chain; removed projects/agents expire tasks; flag-off state and decisions match
the pre-phase behavior; existing smokes plus task lifecycle tests pass.

## Phase 2 - Assistive recommendations, prompt compression, and viewer

1. Select one recommendation per agent from compatible open tasks. Compatibility
   checks survival status, known action availability, required station/tool,
   district reachability, current inventory, and active assignment.
2. Add compact payload fields:
   - `recommended_task`: one agent-facing task summary.
   - `task_board_summary`: elder-only top five tasks, capped at 600 characters.
3. Render recommendations as one prompt section. Replace overlapping project,
   assignment, repair, and stall nudges when they describe the same task; retain
   unrelated P0/P1 nudges.
4. Update `role_fallback_action` to prefer the recommended existing action only when
   the LM response is invalid/offline. A valid LLM decision is never overridden.
5. Add a read-only viewer panel showing top tasks, assignees, status, blocker, and
   age. Limit `/state.civilization.taskBoard` to 20 active display records.
6. Refactor repeated prompt lists into relevance-filtered briefs. Update
   `path1_soak.py` from its current 5,800-token ceiling to staged gates: no sample
   above 5,800, p95 at or below 4,800, then final maximum at or below 4,200 before
   Phase 2 is accepted.

**Acceptance:** recommendations point to feasible actions; valid autonomous choices
remain untouched; no stale recommendation survives source completion; task panel
matches engine state; routine prompt maximum is 4,200 tokens in the full-feature
fixture; no context-overflow regression.

## Phase 3 - Outcome-driven learning

**Flag:** `OUTCOME_LEARNING_ENABLED`, initially False; turn on after acceptance.

1. Add structured outcome capture around `apply_decision`, goal steps, and fallback
   application. Stamp whether the decision came from the LLM, a goal, emergency,
   deterministic backstop, or normalization fallback.
2. Update matching task attempts/progress/status from the structured outcome.
3. Aggregate bounded operational lessons by context key. Promote a lesson only after
   two matching failures or one success; refresh rather than duplicate it.
4. Add `relevant_lessons` to the think payload, capped at three entries/360 chars.
   Retrieval matches task kind, action, resource, district kind, and reason code.
5. Add benchmarks: `action_success_rate`, `fallback_rate`,
   `task_completion_latency`, `repeated_failure_count`, and `task_abandon_rate`.

**Acceptance:** outcomes never rely on parsing human-readable summaries; histories
and lessons respect caps across save/restore; unrelated lessons do not appear in a
prompt; repeated missing-tool/input failures produce the correct recovery lesson;
flag-off prompts and behavior remain unchanged.

## Phase 4 - Adaptive specialization and social coordination

**Flag:** `ADAPTIVE_SPECIALIZATION_ENABLED`, initially False; turn on after acceptance.

1. Update capability profiles from structured outcomes only. Recalculate rankings
   on task refresh; do not add a separate high-frequency tick.
2. Use the frozen score to recommend assignees and order elder `idle_agents`; never
   force role changes or interrupt survival/emergency responders.
3. Link `assign_task` text to the selected task ID internally while preserving the
   existing action schema and viewer label.
4. Replace resource-only commitments with optional `taskId`. Honor a commitment only
   when its task records progress/completion; retain legacy resource matching for old
   saves without `taskId`.
5. Generate `teach` tasks when a high-capability agent and low-capability agent are
   co-located, idle, and the village has a demonstrated capability gap. Reuse
   `talk_to_nearby` and existing teaching mechanics.
6. Add an elder-only capability summary capped at five agents/500 characters.

**Acceptance:** ranking is deterministic; nearer/reliable agents outrank unsuitable
ones in fixtures; task load prevents one agent receiving all work; commitments clear
only on measured progress; teaching never preempts P0-P2 tasks; protected roles and
flag-off behavior remain unchanged.

## Phase 5 - Bounded stall reflection

**Flag:** `STALL_RECOVERY_ENABLED`, initially False; turn on after acceptance.

1. Trigger only when a source project has at least one blocked task, three failed
   attempts, no progress for `PROJECT_ABANDON_THRESHOLD`, and no recovery call for
   `DIRECTIVE_TTL_FRAMES`.
2. Reuse the elder/high-stakes route for one dedicated recovery call. Cap input at
   1,200 prompt tokens and output at 256 tokens; permit one village-wide recovery
   call per cooldown and no more than four per real-time hour.
3. Require JSON `{project_id, steps:[{action,target,target_district,resource}]}` with
   one to four steps. Validate every action against existing actions and every
   target/resource/district against current registries/state.
4. Compile valid steps into `source: recovery` task records. Reject the whole plan if
   the first step is infeasible; drop only later invalid steps. Log raw response,
   validation result, and compiled task IDs.
5. If the call fails, returns invalid JSON, or is rate-limited, retain deterministic
   task recovery and make no world mutation.

**Acceptance:** trigger/cooldown/rate limits are deterministic; no call occurs outside
the trigger; invalid plans cannot mutate world state; valid plans create feasible
existing-action tasks; offline LM Studio leaves the simulation progressing through
existing backstops.

## Phase 6 - Integration, rollout, and signoff

1. Run `py_compile`, all existing smokes, `smartness_smoke.py`, and blueprint tests.
2. Run replay benchmarks against representative routine, elder, rejection, and
   stalled-project prompts. Confirm latency and malformed-response rates do not
   regress beyond 10% relative to Phase 0.
3. Run a fresh two-hour soak with all four flags on. Require:
   - zero context overflows;
   - prompt maximum <= 4,200 tokens;
   - fallback and repeated-failure rates lower than baseline;
   - median active-task completion latency lower than baseline project-shortfall
     latency;
   - no 30-minute interval without craft, build, repair, or task completion progress;
   - no unbounded task/outcome/lesson collections.
4. Inspect JSONL joins from LLM decision -> outcome -> task transition for at least
   one success, rejection recovery, reassignment, teaching event, and stall recovery.
5. Verify the viewer task panel, save/restart continuity, and each flag independently.
6. Update this document to `DONE` with verification commands, measured baseline/final
   values, LM Studio model/context/parallel assumptions, and any deferred findings.

## Model and change ownership

- The initiating session orchestrates, splits phases, reviews diffs, and signs off.
- Specs and implementation for each phase are delegated to the repo's `implementer`
  subagent on Claude Sonnet 5 or lower, or to the equivalent Codex/OpenAI GPT tier
  below. The orchestrator may use any tier but does not perform implementation,
  except for trivial one-line fixes allowed by `CLAUDE.md`.
- Keep each phase reviewable and independently flag-gated. Do not combine unrelated
  visual work or economy changes into these commits.

### Codex/OpenAI GPT implementation tiers

"Equivalent" here is an operational routing policy, not a claim of benchmark or
capability parity between vendors. Use the lowest tier that can reliably complete
the assigned, reviewable step:

| Work class | Claude implementation ceiling | Codex/OpenAI GPT equivalent |
| --- | --- | --- |
| Cross-file behavior, engine concurrency, persistence, migrations, or difficult debugging | Sonnet 5 | `gpt-5.4` at high or xhigh reasoning |
| Bounded feature steps, specs, tests, viewer integration, or straightforward refactors | Sonnet 5 or a lower Claude tier | `gpt-5.4` at medium reasoning |
| Mechanical docs, fixtures, formatting, narrow test additions, or simple inspections | Lower Claude tier | `gpt-5.4` at none or low reasoning |

Only GPT-5.4 through GPT-5.6 are available in the target Codex environment. Treat
`gpt-5.4` as the Sonnet 5-equivalent implementation ceiling and scale reasoning
effort down for lower-tier work instead of referencing unavailable mini, nano, or
older Codex-specific models. Reserve `gpt-5.5` and `gpt-5.6` for the initiating
orchestrator, difficult review, phase decomposition, and final sign-off; they must
not implement phase code under the current repo model policy.

Re-check the [official model catalog](https://developers.openai.com/api/docs/models/all)
and the models exposed by the current Codex environment during Phase 0. Record the
exact available model ID and reasoning effort in the implementation log. Do not
silently move implementation above `gpt-5.4`; changing the ceiling requires an
explicit repo model-policy update.

This routing applies to development agents implementing this plan. It does not
replace the simulation's local LM Studio model or change runtime cognition routing;
any runtime model-route change remains conditional scope and must update the owning
specs, benchmarks, context assumptions, and feature flags first.

## Conditionally in scope

The following are permitted when a phase demonstrates that the existing design
cannot meet its acceptance criteria without them. Any use must be added to the
owning specs first, independently flag-gated, measured against the Phase 0
baseline, and documented in the implementation log:

- A narrowly-scoped new LLM action when existing actions cannot express a required
  task or recovery step. The full action-sync invariant applies.
- Additional deterministic engine automation when assistive recommendations alone
  cannot prevent a measured deadlock. It must remain observable and reversible.
- A bounded background LLM call or specialized model route when replay evidence
  shows a material quality gain that cannot be achieved through prompt/context
  improvements.
- A local vector index or replacement retrieval implementation if the current
  bounded memory search cannot meet relevance and latency gates.
- Increased context or reduced concurrency when prompt compression cannot meet the
  quality target, provided throughput and memory assumptions are re-benchmarked.
- Changes to deterministic backstops when they conflict with task coordination;
  equivalent survival, recovery, and offline-LM guarantees must remain.
- Additional read-only viewer controls for inspecting, filtering, or debugging
  tasks, outcomes, lessons, and capability profiles.

## Explicitly out of scope

- Multiplayer, accounts, authentication, remote administration, or internet-facing
  deployment hardening.
- Moving authoritative simulation state or decision execution into the browser.
- 3D rendering, a new game engine, mobile-native clients, or replacement of the
  existing Canvas viewer.
- Player-controlled agents, combat systems, win conditions, scoring, monetization,
  or conversion of the simulation into a conventional game.
- Cloud-hosted inference as a required runtime dependency; offline LM Studio and
  deterministic fallback operation must continue to work.
- Training or fine-tuning foundation models, collecting external training data, or
  building a general research benchmark from this application.
- Unbounded chain-of-thought storage, exposing hidden reasoning in the viewer/logs,
  or persisting sensitive model internals.
- Rewriting unrelated economy, world-generation, art, or visual-polish systems
  unless a specific smartness acceptance criterion requires the change.

## Implementation log

Populate during execution:

| Phase | Status | Verification | Measurements/notes |
|---|---|---|---|
| 0 | pending | | |
| 1 | pending | | |
| 2 | pending | | |
| 3 | pending | | |
| 4 | pending | | |
| 5 | pending | | |
| 6 | pending | | |
