# Claude Review Handoff — Daily Council and Succession Integration

## 1. Purpose and review baseline

This document is a review guide for the complete Daily Council implementation
and the succession changes requested after the original plan was completed. It
is not a commit message, pull request description, or claim that the work has
been committed.

Review baseline observed on 2026-07-26:

- Repository: `C:\Users\dbadmin\Desktop\GitServ\simulation`
- Branch: `main`
- HEAD: `5387d83 Merge branch 'codex/module-quality' into main`
- The full Daily Council and follow-up succession implementation is in the
  working tree and is **uncommitted**.
- `git status --short` also contains unrelated user/untracked material,
  including `.codex/`, `.ua/`, other planning documents, state snapshots,
  `simulation/state.json.bak-overlap`, and `scripts/out/`.
- Do not infer that every dirty or untracked path belongs to this feature.
  Preserve unrelated files and review/stage only intentional feature paths.
- No commit or PR was created as part of this work.

Primary source documents:

- `docs/plan-daily-council.md` — original phase plan and historical model
  assignments.
- `docs/HANDOFF.md` — original implementation snapshot and soak evidence.
- `specs/02-engine-core.md`, `03-cognition.md`, `07-actions.md`,
  `09-systems-society.md`, and `11-viewer.md` — current canonical behavior.

Line numbers below are navigation aids from the current working tree and may
drift. Function and constant names are the stable review entry points.

## 2. Original plan implementation, phase by phase

The original plan records explicit GPT-5.6 Codex assignments. `gpt-5.6-luna`
was not exposed and was not used.

### Phase 0 — spec-first design lock

- Planned assignment: `gpt-5.6-terra`, medium reasoning.
- Status: completed.
- SDD changes established ownership and contracts before implementation:
  feature flag, state machine, SQLite transcript table, council actions,
  cognition tier, viewer modal, and digest context.
- Specs touched: `specs/00-overview.md`, `01-architecture.md`,
  `02-engine-core.md`, `03-cognition.md`, `07-actions.md`,
  `09-systems-society.md`, and `11-viewer.md`.

### Phase 1 — engine, state machine, trigger, and persistence

- Planned assignment: `gpt-5.6-sol`, high reasoning.
- Status: completed.
- Added `DAILY_COUNCIL_ENABLED = True` and bounded session constants.
- Added `civilization["dailyCouncil"]`, `councilDigests`, transcript rows,
  council-turn agent state, restore defaults, and `/state` projection.
- Implemented deterministic seating, attendance/excusal, agenda generation,
  convening, discussion, proposal, voting, verdict, adjourn, phase TTL, and
  session TTL.
- Added additive SQLite `council_transcript` DDL and full-list persistence
  mirroring the existing memory-table pattern; `STATE_VERSION` remains 2.
- Added newest-30-meeting transcript retention and bounded log/digest rings.
- Original deterministic harness: `scripts/daily_council_smoke.py`, producing
  `scripts/out/daily_council_smoke.json`.

### Phase 2 — cognition, actions, and prompts

- Planned assignment: `gpt-5.6-sol`, high reasoning.
- Status: completed.
- Added `council_speak`, `council_propose`, and `council_vote` at all
  action-sync points. The catalog remains 42 actions.
- Added council-only action availability, engine validation, server
  normalization/fallback, and real rule/blueprint enactment delegation.
- Added `COUNCIL_SYSTEM_PROMPT` and `build_council_user_prompt()`.
- Council turns replace ordinary think turns; they do not add an LLM slot.
- Added bounded `Recent council:` digest context for later turns.
- Ordinary council behavior remains majority based, with the seated elder's
  defined exact-tie ruling and rejection of non-tied sub-quorum pluralities.

### Phase 3 — viewer

- Planned assignment: `gpt-5.6-terra`, medium reasoning.
- Status: completed.
- Added the responsive, auto-opening Daily Council modal, manual close/reopen,
  760×760 logical canvas, round table, engine-authored seats, existing agent
  sprite rendering, agenda, live transcript, tally, and verdict.
- Added council action labels to the agent-status renderer.
- Added `scripts/daily_council_state_probe.py`, which writes
  `scripts/out/daily_council_state.json`.
- The viewer remains a read-only projection; it does not derive seats, quorum,
  tally, or outcomes.

### Phase 4 — integration cleanup and regression

- Planned assignment: `gpt-5.6-sol`, high reasoning.
- Status: completed.
- Reconciled specs, handoff, flag-on/flag-off behavior, action sync, restore,
  ordinary governance, Sid parity, and Path 1.
- Added `scripts/daily_council_regression.py`, which runs three deterministic
  smokes under both flag values in isolated subprocesses and writes
  `scripts/out/daily_council_regression.json`.
- Current post-follow-up matrix result: all 6 cells pass.

### Phase 5 — soak

- Planned assignment: `gpt-5.6-sol`, high reasoning, then handback.
- Status for the original plan: completed.
- `scripts/daily_council_soak.py` drove 35 deterministic no-Ollama meetings
  through the original Daily Council lifecycle.
- Artifact:
  `scripts/out/daily_council_soak_20260726T063406Z.json`.
- Recorded result: PASS in 0.613 seconds, 35/35 meetings, zero anomalies,
  retained meeting IDs 6–35, 990 retained transcript rows, and SQLite size
  plateauing at 294,912 bytes from meetings 30–35.
- Important review boundary: this 35-meeting soak predates the post-plan
  succession-council patch and was **not rerun** afterward. Focused and broad
  deterministic regressions were rerun after that patch.

## 3. Affected-file inventory

### Canonical specs

| File | Current feature content |
|---|---|
| `specs/00-overview.md` | Daily Council ownership mapping. |
| `specs/01-architecture.md` | `DAILY_COUNCIL_ENABLED` in the flag index and action-sync context. |
| `specs/02-engine-core.md` | Day-boundary scheduling plus emergency succession cadence; `council_transcript` SQLite schema, serialization, restore, and retention. |
| `specs/03-cognition.md` | Council prompt tier, digest context, candidate-comparison prompt, succession `candidate` output, and abstaining fallback. |
| `specs/07-actions.md` | Parameters/preconditions/effects for all three council actions, including ordinary yes/no ballots and succession candidate ballots. |
| `specs/09-systems-society.md` | Complete council state machine, attendance, agenda, voting, persistence, succession recovery, emergency council, tie/no-vote behavior, and flag-off legacy path. |
| `specs/11-viewer.md` | Modal behavior, responsive table, head/headless seats, ordinary tally, succession candidate tally, transcript, and village verdict. |

### Engine, persistence, and cognition runtime

| File | Precise changes |
|---|---|
| `simulation/sim_engine.py` | SQLite transcript DDL/read/write; council constants and seeded/restored state; transcript RAM list; council agenda/seats/session state machine; action handlers and ratification; digest/log retention; think-payload/action gating; tick scheduling; snapshot projection; invention-council suppression; automatic succession repair; emergency succession council; safe winner enactment. |
| `simulation/server.py` | Three action verbs in `DECISION_ACTIONS`; schema fields for council payloads including `candidate`; council-aware fallback and normalization; council prompt routing; existing 42-action catalog retained. |
| `simulation/prompts.py` | Main prompt council rules, `COUNCIL_SYSTEM_PROMPT`, slim council user prompt, named-candidate comparison, succession choice shape, and verdict guidance. |

Important current engine data shapes:

- `civilization.dailyCouncil` now includes:
  - `meetingId` (frame-derived unique integer),
  - `trigger` (`daily` or `succession`),
  - phase/frame/day/timestamp,
  - seats/attendees/excused,
  - complete agenda,
  - round/speaking order,
  - live transcript,
  - optional ballot and verdict.
- An ordinary ballot has `kind: rule|blueprint|idea` and per-voter
  `yes|no|abstain`.
- A succession ballot has `kind: succession`, election id, named `candidates`,
  and per-voter candidate-name or `abstain` choices.
- A succession verdict has the candidate winner, per-candidate tally,
  `elderRuling: null` for the leaderless declaration, outcome, reason, and
  optional stable-ID tie-break marker.
- `civilization.councilDigests` is capped at 5; at most 2 recent digests enter
  prompt context.
- `self.council_transcript_rows` is persisted in
  `council_transcript(rowid_pk, meeting_id, who, type, text, feeling,
  frame_tick, ts)`.
- `meetingId`, rather than calendar day alone, prevents an emergency meeting
  from colliding with a scheduled meeting held earlier on the same day.

### Viewer

| File | Precise changes |
|---|---|
| `simulation/index.html` | Council modal markup/CSS/state, auto-open/reopen behavior, round-table canvas, agenda/transcript/tally/verdict renderers, action labels, succession candidate totals and per-voter choices, `Village verdict` heading, and refreshed winner head seat. |

`simulation/sprites.js` was reused but not changed for this feature.

### Deterministic scripts and artifacts

| File | Purpose |
|---|---|
| `scripts/daily_council_smoke.py` | Main lifecycle/action/persistence/TTL/flag-off/action-sync assertions; updated to match frame-derived `meetingId`. |
| `scripts/daily_council_regression.py` | Six-cell on/off integration matrix over Daily Council, Sid parity, and Path 1. |
| `scripts/daily_council_state_probe.py` | Live `/state` shape probe for viewer-consumed council data. |
| `scripts/daily_council_soak.py` | Original multi-meeting lifecycle, anomaly, retention, and SQLite-growth soak. |
| `scripts/succession_recovery_smoke.py` | Restore repair, corrupt/orphaned/ineligible ballots, prompt/schema normalization, visible discussion/voting, non-first winner, tie/no-vote behavior, viewer projection, legacy flag-off timeout, and dual-elder protection. |
| `scripts/out/daily_council_smoke.json` | Latest focused Daily Council assertion artifact. |
| `scripts/out/daily_council_regression.json` | Latest six-cell matrix artifact. |
| `scripts/out/daily_council_state.json` | Viewer state-probe artifact. |
| `scripts/out/daily_council_soak_20260726T063406Z.json` | Original 35-meeting soak evidence. |

### Documentation

| File | Purpose |
|---|---|
| `docs/plan-daily-council.md` | Original design, phases, assignments, acceptance mapping, and original completion record. |
| `docs/HANDOFF.md` | Original completed-plan machine-readable/narrative handoff. Its top snapshot predates the later succession-council changes; use current specs and this review document for those additions. |
| `docs/review-daily-council-implementation.md` | This review handoff. |

## 4. Post-plan request chronology

### 4.1 Leadership vacancy was questioned

After the original Daily Council plan was complete, the user questioned why the
village had no elder leadership. Inspection of the then-live state showed Sage
as `retired_elder`, incapacitated, a living village, and
`pendingSuccession: None`. Natural-death succession existed, but restored or
otherwise leaderless state had no general invariant repairing the vacancy.

### 4.2 Automatic leaderless succession recovery

The first follow-up added `_ensure_succession_election()` and a focused smoke.
On the existing `RULES_TICK_FRAMES` cadence it:

- detects a living village with no living formal `role == "elder"`;
- preserves a living formal elder even when temporarily incapacitated, leaving
  Sage emergency/recovery in charge rather than creating a second elder;
- validates election id, candidate list, deadline, candidate ballot set, vote
  maps, and living/non-incapacitated candidate eligibility;
- replaces missing, corrupt, orphaned, mismatched, or ineligible restored
  succession state with one deterministic election;
- leaves a valid election untouched, preventing election spam/deadline resets;
- excludes dead/incapacitated candidates and rejects an unsafe/missing winner;
- cancels stray succession state when a formal elder still holds office;
- retains the bounded direct legacy resolver when Daily Council is disabled.

### 4.3 Why Zara was elder

The next user question was why Zara became elder. Review found first-ballot bias
in the old succession backstop: succession was represented as multiple
rule-shaped candidate ballots, and `_maybe_advance_rules()` generated automatic
yes votes rather than conducting Daily Council deliberation. Zara's pre-fix
election was therefore a deterministic engine-backstop result, not the result
of a visible Council conversation comparing candidates.

This is important historical context, not a claim that Zara's current role is
invalid. The simulation does not rewrite past outcomes retroactively.

### 4.4 Succession moved into the Daily Council

The final follow-up moved future succession into the visible assembly:

- A valid leaderless election promptly convenes an emergency Daily Council on
  the recovery cadence instead of waiting for the next day boundary.
- The emergency can convene with one available survivor; ordinary scheduled
  councils retain the two-living minimum.
- The council keeps all ordinary agenda topics and adds
  `leadership_vacancy`, naming every candidate.
- It starts headless and uses stable `(role, name)` seating. After promotion,
  roster refresh places the winner in the unique head seat.
- Normal convening, discussion, proposal, voting, verdict, and adjourn phases
  remain. The pre-opened succession ballot passes through proposal without
  soliciting a competing proposal.
- Discussion prompts explicitly ask villagers to compare named candidates.
- `council_vote` accepts an exact candidate name or abstention. Choices appear
  in the normal transcript and live tally.
- Highest recorded candidate total wins. Exact vote ties and all-zero/no-vote
  TTL completion use lowest stable agent id.
- The village, not a nonexistent elder, declares the succession result.
  Promotion occurs only through `_enact_succession_winner()`.
- The new elder may take the existing verdict-speech turn and the same session
  then adjourns normally.
- `_maybe_advance_rules()` no longer manufactures candidate support while
  Daily Council is enabled.
- `_maybe_resolve_stalled_succession()` is isolated to the flag-off legacy
  path. Enabled-mode phase/session TTLs resolve from recorded Council choices
  and cannot be bypassed by the older rule-ballot timeout.
- Candidate invalidation can restart the election and refresh the active
  emergency ballot/discussion without crowning a corpse or collapsed agent.
- Frame-derived `meetingId` avoids same-day transcript/retention collisions.
- The viewer now shows candidate totals, each attendee's choice, transcript,
  and a `Village verdict` rather than an implied elder ruling.

## 5. Current live-world status

The server has been restarted and was listening on port 5001 when this handoff
was prepared. A read-only `GET /state` observation at frame 9,379,388 showed:

- 8 living villagers;
- Zara as the sole living elder;
- Zara was not incapacitated;
- no pending succession election;
- no active Daily Council.

Zara remains elder from the pre-fix deterministic election. The patch prevents
a duplicate election while a living formal elder exists. Future genuine
vacancies use the emergency Daily Council flow. Existing history and Zara's
role are not retroactively rewritten.

## 6. Validation evidence

### Post-succession focused and broad validation

All of the following completed successfully:

```powershell
uv run python scripts/succession_recovery_smoke.py
```

Result: `ALL PASS`, including restored leaderless recovery, corrupt/orphaned
repair, normal-plus-succession agenda, named discussion prompt, distinct
candidate choices, a non-first candidate winning, stable-ID exact tie,
stable-ID no-vote timeout without synthetic support, viewer projection,
server candidate normalization/fallback abstention, flag-off timeout, and
temporarily incapacitated formal-elder protection.

```powershell
uv run python -m py_compile simulation/sim_engine.py simulation/server.py simulation/prompts.py scripts/succession_recovery_smoke.py scripts/daily_council_smoke.py
```

Result: PASS.

```powershell
uv run python scripts/sid_parity_smoke.py
```

Result: `ALL PASS`.

```powershell
uv run python scripts/path1_smoke.py
```

Result: `PASS — all Path 1 smoke checks`, including its compile check.

```powershell
uv run python scripts/daily_council_smoke.py
```

Result: JSON artifact reported `"pass": true`.

```powershell
uv run python scripts/daily_council_regression.py
```

Result: PASS in 3.97 seconds. All six isolated cells passed:

| Flag | Daily Council smoke | Sid parity | Path 1 |
|---|---:|---:|---:|
| on | PASS | PASS | PASS |
| off | PASS | PASS | PASS |

The matrix used a fresh temporary `state.db` per subprocess and reports that
the real save and source files were not mutated by the harness.

Viewer JavaScript syntax was checked without writing an intermediate file:

```powershell
@'
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync('simulation/index.html', 'utf8');
const blocks = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m => m[1]).filter(Boolean);
for (const block of blocks) new vm.Script(block);
console.log(`inline viewer scripts parse (${blocks.length} block)`);
'@ | node
```

Result: `inline viewer scripts parse (1 block)`.

Relevant-file whitespace/error check:

```powershell
git diff --check -- simulation/sim_engine.py simulation/server.py simulation/prompts.py simulation/index.html specs/02-engine-core.md specs/03-cognition.md specs/07-actions.md specs/09-systems-society.md specs/11-viewer.md scripts/daily_council_smoke.py scripts/succession_recovery_smoke.py
```

Result: clean; only the repository's existing LF→CRLF warnings were printed.

### Original soak evidence

```powershell
uv run python scripts/daily_council_soak.py
```

Original result recorded in the plan/handoff:

- PASS, 35/35 meetings;
- 0 anomalies;
- 0.613 seconds;
- retained meeting IDs 6–35;
- 990 retained transcript rows;
- SQLite plateau at 294,912 bytes after meeting 30.

This soak was not rerun after the succession follow-up. Do not use it as direct
evidence for 35 consecutive emergency succession meetings; use it as evidence
for the original ordinary-council lifecycle and retention design.

Live Ollama output quality was not deterministically exercised by these tests.
The deterministic suite exercises prompt construction, normalization,
fallback, state transitions, and action application without requiring Ollama.

## 7. Review checklist and risks

### SDD and action contracts

- [ ] Confirm current specs describe both ordinary and emergency succession
  behavior and no old “elder required for every council” statement remains
  unqualified.
- [ ] Confirm `DAILY_COUNCIL_ENABLED` is synchronized in the flag index.
- [ ] Confirm the 42-action invariant remains synchronized across
  `DECISION_ACTIONS`, `DECISION_SCHEMA`, prompts, `apply_decision()`,
  payload `available_actions`, and viewer `ACTION_LABELS`.
- [ ] Confirm schema/normalization preserves `candidate` only for a current
  succession candidate and deterministically abstains on invalid fallback.

### State machine and lifecycle safety

- [ ] Walk scheduled convening → discussion → proposal → voting → verdict →
  adjourn and verify TTL behavior remains unchanged for ordinary councils.
- [ ] Walk emergency headless convening and verify it does not wait for
  `DAY_FRAMES`.
- [ ] Review the one-survivor succession exception and all-incapacitated
  deferral.
- [ ] Review candidate death/collapse during convening, discussion, and voting:
  election repair must refresh the ballot and never crown the invalid agent.
- [ ] Verify the promoted winner becomes the only elder, receives the head
  seat/verdict turn, and the session cannot wedge.
- [ ] Verify a temporarily incapacitated formal elder prevents succession and
  stray ballots cannot create dual elders.
- [ ] Confirm no enabled-mode path in `_maybe_advance_rules()` or
  `_maybe_resolve_stalled_succession()` can bypass visible candidate voting.
- [ ] Confirm `DAILY_COUNCIL_ENABLED = False` retains the bounded legacy
  succession resolver and legacy invention council.

### Persistence and records

- [ ] Review additive SQLite DDL and unchanged `STATE_VERSION = 2`.
- [ ] Confirm save/restore round-trips `dailyCouncil`, digests, and authoritative
  transcript rows.
- [ ] Confirm `meetingId` is used for transcript rows/logs and newest-30
  retention, including two meetings in one calendar day.
- [ ] Confirm old sessions lacking `meetingId` retain the documented fallback.
- [ ] Consider rerunning the 35-meeting soak if review requires post-succession
  long-run evidence; it was not rerun for this patch.

### Viewer and cognition

- [ ] Browser-check auto-open, manual close/reopen, Escape/modal accessibility,
  focus behavior, short/narrow responsive layout, full seat ring, headless
  state, refreshed head seat, candidate totals, transcript, and village verdict.
- [ ] Confirm the viewer only renders serialized state and derives no outcome.
- [ ] Inspect prompt-token impact of named candidates and the extra agenda item.
  Candidate count is bounded by the succession nomination cap and digest
  context remains capped, but no new live token audit was run.
- [ ] Exercise at least one live Ollama succession discussion if qualitative
  model behavior is in review scope. Deterministic fallback is tested; organic
  multi-candidate deliberation quality is not.
- [ ] Treat Zara's current role as a legacy outcome. Review should not trigger
  another election or rewrite history merely because the election preceded the
  Council fix.

## 8. Reproduction and key entry points

Run from `C:\Users\dbadmin\Desktop\GitServ\simulation`:

```powershell
uv run python scripts/succession_recovery_smoke.py
uv run python scripts/daily_council_smoke.py
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
uv run python scripts/daily_council_regression.py
uv run python -m py_compile simulation/sim_engine.py simulation/server.py simulation/prompts.py scripts/succession_recovery_smoke.py scripts/daily_council_smoke.py
```

Optional original soak rerun:

```powershell
uv run python scripts/daily_council_soak.py
```

Live browser review:

```powershell
uv run python simulation/server.py
# Open http://127.0.0.1:5001
```

Key engine navigation points:

- SQLite read/write: `simulation/sim_engine.py` `_write_state_db()` near line
  62 and `_read_state_db()` near line 126.
- Succession recovery: `_ensure_succession_daily_council()` near 6664,
  `_ensure_succession_election()` near 6702,
  `_start_succession_election()` near 6808,
  `_enact_succession_winner()` near 6846, and
  `_maybe_resolve_stalled_succession()` near 6901.
- Daily Council: `_daily_council_agenda()` near 8469,
  `_assign_council_seats()` near 8540,
  `_maybe_convene_daily_council()` near 8651,
  `_council_speak()` near 8844, `_council_propose()` near 8896,
  `_council_vote()` near 8955,
  `_resolve_daily_council_ballot()` near 8999,
  `_ratify_daily_council_ballot()` near 9075,
  `_adjourn_daily_council()` near 9201, and
  `_maybe_advance_daily_council()` near 9246.
- Bias/backstop isolation: `_maybe_advance_rules()` near 9922.
- Prompt payload and scheduling: `_build_think_payload()` near 11083 and
  `_tick_once()` near 12293.
- Restore/snapshot: `restore_state()` near 12558 and `snapshot()` near 12961.

Key cognition/viewer navigation:

- `simulation/prompts.py`: main Daily Council rules near line 143,
  `COUNCIL_SYSTEM_PROMPT` near 312, and `build_council_user_prompt()` near 340.
- `simulation/server.py`: `DECISION_ACTIONS` near 918,
  `DECISION_SCHEMA` near 949, `role_fallback_action()` near 2076,
  `normalize_decision()` near 2264, and `run_agent_decision()` near 3577.
- `simulation/index.html`: modal near 715, `ACTION_LABELS` near 1455,
  `renderDailyCouncil()` near 2009, and
  `drawCouncilAssemblyTable()` near 2076.

Key deterministic review scripts:

- `scripts/daily_council_smoke.py`: `exercise_main_session()` near line 100 and
  flag-off coverage near 621.
- `scripts/daily_council_regression.py`: matrix definition near line 27 and
  runner near 97.
- `scripts/daily_council_soak.py`: main runner near line 410.
- `scripts/daily_council_state_probe.py`: main probe near line 107.
- `scripts/succession_recovery_smoke.py`: focused tests near lines 71–339.

All line numbers are approximate and should be re-derived with `rg -n` after
further edits.
