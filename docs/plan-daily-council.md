# Plan — Daily Council Assembly

**Status:** fully implemented through Phase 5 (35-meeting soak PASS in 0.613 s;
newest-30 transcript retention and bounded SQLite growth verified)
**Author:** orchestrator (planning pass 2026-07-26)
**Supersedes:** the reactive *invention council* debate (`_maybe_invention_backstop`
fan-out + `councilActive` invention flow) as the primary deliberation surface.

## 1. Goal & intent

Replace the current slow, reactive debate system (invention council fires only when
`_invention_required()` has held for 3 elder turns; 2–3 idle villagers; blueprint-only
scope) with a **scheduled Daily Council Assembly**: once per in-world day, **all** living
agents gather around one large circular table, the **elder sits at the head as lead**, and
the village openly discusses the state of the world — current status, ongoing and new
projects, limitations, ideas, proposals, and how each agent *feels* about the path toward
evolution. Discussion escalates into **proposals**, agents **vote**, and the elder declares
outcomes by **majority rule** (elder breaks ties / ratifies the winner).

A dedicated, adequately-sized **viewer window** opens whenever a council convenes, drawing
all agents seated around the round table with the elder at the head, live speech, the running
vote tally, and the elder's verdict.

### Design decisions (defaults chosen; flag any you want changed)

- **Cadence:** one assembly per in-world day, triggered deterministically at the day
  boundary (`frameTick % DAY_FRAMES == 0`, `DAY_FRAMES = 13500` ≈ real minutes). Gated by a
  new flag `DAILY_COUNCIL_ENABLED` (default **True**).
- **Mandatory full attendance:** **every living agent must attend.** On convene, the engine
  summons *all* living agents — it interrupts each one's current goal/task, sets the
  `councilTurn` flag, and walks them to an assigned seat. There is no idle/subset selection
  (unlike today's invention council, which pulls only 2–3 idle villagers).
  - **Dead agents never attend.** They are excluded at the source — `_assign_council_seats()`
    and every attendee-derived list (`c["dailyCouncil"]["attendees"]`, quorum denominator,
    seat ring sizing) is built from the engine's living-agent roster only
    (`agent["incapacitated"]` distinguishes collapsed-but-alive from dead). Dead agents remain
    in `self.agents` for lifecycle/history rendering and are excluded by their non-null
    `deathFrame`, matching the engine's living-roster filter. A death mid-session removes that agent
    from the live seat ring and vote denominator on the next phase-advance tick, same as it
    already vacates rule votes/elections elsewhere in the codebase.
  - The **only** excused-but-alive agents are the currently **incapacitated/collapsed** — and
    even they can be seated as "present but silent" if you prefer (a one-line policy switch;
    default is *excused* since a collapsed agent can't walk).
  - The assembly runs only when at least 2 living agents and a living, non-incapacitated elder
    exist (a degenerate-world and leadership guard,
    `DAILY_COUNCIL_MIN_LIVING = 2` — not a subset filter; if the whole village is one survivor
    there is no council to hold).
- **Relationship to invention council:** the daily assembly becomes the deliberation surface.
  The reactive invention backstop is **subsumed** — its "the village needs a new invention"
  demand becomes one agenda item inside the assembly rather than a separate `councilActive`
  session. When `DAILY_COUNCIL_ENABLED` is on, `_maybe_invention_backstop`'s council fan-out
  is disabled (the elder-drafts-it-himself deterministic escape is retained as a safety net if
  no assembly has produced a blueprint within a timeout). This keeps LLM call volume flat:
  council turns **replace** an agent's normal think turn, exactly like `inventionTurn` does today.
- **Voting mechanics:** reuse the existing `propose_rule` / `vote_rule` /
  `_tally_and_maybe_enact` quorum scaffold verbatim for *rule* proposals. The assembly adds a
  thin **agenda/ballot** layer on top so blueprint approvals, rule changes, and free-form
  "ideas" all tally under one majority test. Because attendance is mandatory, quorum for an
  assembly ballot = majority of the **whole living village** (`(living // 2) + 1`, with any
  excused-incapacitated agents excluded from the denominator). Yes/no reaching quorum resolves
  normally. An exact yes/no tie below quorum is the explicit exception: the seated elder's
  personal yes/no vote breaks it (default no if absent/abstaining) and may approve; for a rule
  tie, one synthetic elder ratification vote is passed through `_tally_and_maybe_enact`.
  Non-tied sub-quorum pluralities/abstentions reject. The elder ratifies the result.
- **No added LLM budget:** `MAX_CONCURRENT_LLM = 3` is unchanged; agents speak in bounded
  rounds, each speaking turn consuming that agent's normal think slot for the frame.
- **Two-tier record, per meeting, both in `state.db`:** every meeting produces (a) a **full
  verbatim transcript** — every speak/propose/vote/verdict event, human-auditable, no
  summarization — persisted in a **dedicated `state.db` table** (not a separate log file, so
  there is one persistence surface to manage/back up/restore, matching how `memory` already
  gets its own table rather than living in the `civ` blob), and (b) a **compact,
  AI-context-friendly digest** — a short, bounded summary of that meeting's
  topics/proposals/verdict/mood that is cheap enough to fold into *every* agent's future
  think-payload context (not just attendees), giving the whole village continuity across
  meetings without paying the token cost of the raw transcript. See §3 and §6 for the
  schema/prompt wiring and §7 for persistence and table design.

## 2. Architecture touchpoints (read before implementing)

- **Engine state / tick loop:** `simulation/sim_engine.py` — new `civilization["dailyCouncil"]`
  session object (persisted to `state.db`), a `_tick_once` day-boundary trigger next to the
  existing `ft % DAY_FRAMES == 0` block (sim_engine.py:11169), a `_maybe_advance_daily_council`
  state-machine gate co-located with the other `_maybe_*` backstops (sim_engine.py:11123-11144),
  and seat-geometry + `/state` serialization next to the current `councilActive` block
  (sim_engine.py:11827-11835).
- **Cognition / prompts:** `simulation/server.py` (`run_agent_decision`, `normalize_decision`,
  `SYSTEM_PROMPT`, `DECISION_ACTIONS`, `DECISION_SCHEMA`) and `simulation/prompts.py` — a slim
  **council-turn prompt** (mirrors the invention-turn prompt) that folds in the world-status
  agenda and asks the agent for opinion + feeling + optional proposal/vote.
- **Action sync invariant** (CLAUDE.md, specs/01): any new action verbs
  (`council_speak`, `council_propose`, `council_vote` — see §4) must be added in lockstep to
  `DECISION_ACTIONS` / `DECISION_SCHEMA` / `SYSTEM_PROMPT` (server.py),
  `apply_decision()` + payload `available_actions` (sim_engine.py), and `ACTION_LABELS`
  (index.html). The final catalog contains **42 actions**.
- **Viewer:** `simulation/index.html` (modal pattern already exists —
  `#councilTranscriptModal`, index.html:345-436; sidebar `#councilSection`, index.html:569+)
  and `simulation/sprites.js` (pure agent-sprite drawing, reusable for seated figures). The new
  assembly window is a sibling modal that auto-opens off `/state` `civilization.dailyCouncil`.
- **Persistence:** `simulation/sim_engine.py`'s `state.db` layer (`_DB_DDL`, `_serialize_state`,
  `_write_state_db`, `_read_state_db`, sim_engine.py:41-113, 11266-11296) gains a new
  **`council_transcript` table** — one row per transcript event, mirroring the existing
  per-row `memory` table's storage shape *and* its exact full-list-in-RAM,
  delete-and-reinsert-all-on-save persistence mechanism (pinned in §7) — rather than a
  standalone log file. This keeps the full record inside the one persistence surface the repo
  already backs up/restores/manages (`state.db`), instead of adding a second file-based system
  to keep in sync. The bounded `councilDigests` ring (§3) is small enough
  (`DAILY_COUNCIL_DIGEST_CAP = 5`) to live in the existing `civ` key/value blob alongside
  `councilLog`.
- **Specs (SDD — specs first, code second):** `specs/09-systems-society.md` owns governance/
  voting and will own the assembly; `specs/03-cognition.md` owns the council-turn prompt tier
  and the digest-in-context mechanic; `specs/07-actions.md` owns the new action params;
  `specs/11-viewer.md` owns the window; `specs/02-engine-core.md` owns the `state.db` schema
  addition (`council_transcript` table); `specs/01-architecture.md` flag index gets
  `DAILY_COUNCIL_ENABLED`; `specs/00-overview.md` ownership map updated.

## 3. Session state machine (`civilization["dailyCouncil"]`)

A single persisted object, `None` when no assembly is in session:

```
{
  "phase": "convening" | "discussion" | "proposal" | "voting" | "verdict" | "adjourned",
  "day": <int>,                      # calendar day index that convened it
  "frame": <int>, "ts": <iso>,       # start stamp (mirrors councilActive discipline)
  "seats": [ {"name","role","seatIndex","x","y","isHead"} ],  # deterministic geometry
  "attendees": [<name>, ...],        # ALL living agents (mandatory) minus excused incapacitated
  "excused": [<name>, ...],          # incapacitated/collapsed agents that could not attend
  "agenda": [ {"topic","detail"} ],  # world status, projects, limitations, invention-needed…
  "round": <int>, "maxRounds": DAILY_COUNCIL_DISCUSSION_ROUNDS,
  "speakingOrder": [<name>, ...],    # elder first, then round-robin
  "nextSpeakerIdx": <int>,
  "transcript": [ {type,who,frame,ts,text,feeling,...} ],  # full log, reuses _stamp_council_event
  "ballot": {                         # present during voting/verdict
     "kind": "rule" | "blueprint" | "idea",
     "id","title","proposedBy","votes": {name: "yes"|"no"|"abstain"},
     "quorum": <int>
  } | None,
  "verdict": {"winner","tally","elderRuling","outcome"} | None
}
```

On adjourn, the session's full `transcript` is (1) written row-by-row into the new
`council_transcript` state.db table (durable, keyed by a `meeting_id` = this session's `day`,
one row per event — never truncated by the normal save cycle since it's a dedicated table, not
part of the `civ` blob that gets fully rewritten every save) and (2) archived verbatim as the
`transcript` field of the `councilLog` record (bounded, `DAILY_COUNCIL_LOG_CAP`, viewer history
— same pattern as today's `councilLog` for the invention council). A **digest** is derived
deterministically from the same session data (no extra LLM call — built from `agenda`,
`ballot`/`verdict`, and a keyword/length-bounded fold of `transcript`, the same way
`_function_summary` already compresses a blueprint's function into one line) and appended to a
new capped ring:

```
civilization["councilDigests"]: [
  {
    "day": <int>, "frame": <int>, "ts": <iso>,
    "topics": [<short strings>],       # from agenda
    "proposals": [ {"title","kind","outcome"} ],   # one line each, from ballot/verdict history
    "verdict": {"winner","outcome"} | None,
    "mood": <short aggregate string>,  # deterministic fold of transcript "feeling" fields
  }, ...
]  # capped at DAILY_COUNCIL_DIGEST_CAP, newest first
```

Phase transitions are driven by `_maybe_advance_daily_council()` (tick-gated, deterministic,
same `_maybe_*` shape as `_maybe_advance_rules`), each with a frame-count TTL so no phase can
wedge (mirrors `COUNCIL_TTL_FRAMES`). Constants (new, sim_engine.py near the invention-council
block sim_engine.py:766-838):

- `DAILY_COUNCIL_ENABLED = True`
- `DAILY_COUNCIL_MIN_LIVING = 2` (degenerate-world guard only — **not** a subset filter; every
  living agent above this floor is a required attendee)
- `DAILY_COUNCIL_DISCUSSION_ROUNDS = 2` (each attendee speaks up to twice)
- `DAILY_COUNCIL_PHASE_TTL_FRAMES = STALL_THRESHOLD * 8` (per-phase wedge escape)
- `DAILY_COUNCIL_SESSION_TTL_FRAMES = STALL_THRESHOLD * 30` (whole-session escape → adjourn)
- `DAILY_COUNCIL_LOG_CAP = 12` (reuses/parallels `COUNCIL_LOG_CAP`; records appended to
  `councilLog` so the existing history panel keeps working)
- `DAILY_COUNCIL_DIGEST_CAP = 5` (bounded `councilDigests` ring; kept small since digests are
  folded into *every* agent's think payload, unlike `councilLog` which only the viewer reads)

## 4. New actions (action-sync invariant applies to all three)

| Action | Params | Effect |
|---|---|---|
| `council_speak` | `message` (required, seated only), `feeling` (short enum/free text), `topic` (agenda ref) | Records the agent's opinion + feeling to the transcript, stages an in-world speech bubble, advances `nextSpeakerIdx`. Non-mutating beyond transcript/bubble. |
| `council_propose` | `kind` (`rule`\|`blueprint`\|`idea`), plus the existing `rule`/blueprint payload, or `{title, detail}` for `idea` | Opens the assembly `ballot`. `rule`/`blueprint` reuse the existing validators (`_validate_rule` / `_validate_blueprint`) so nothing new can bypass caps. `idea` is advisory-only (records intent, no mechanical effect). Transitions phase → `voting`. |
| `council_vote` | `vote` (`yes`\|`no`\|`abstain`) | Casts on the open ballot; re-tally via whole-village majority. An exact yes/no tie uses the seated elder's personal vote as the deterministic exception; non-tied sub-quorum results reject. For `kind: "rule"` it delegates to the real `_tally_and_maybe_enact` (including one synthetic elder ratification vote on a tie) so an approved rule genuinely enacts. |

These are only accepted while `dailyCouncil` is in session and the agent is seated; outside a
session they hard-reject with the deterministic reason (same style as existing rejects).
`role_fallback_action` gets a council branch so a seated agent that returns junk still speaks.

## 5. Seating geometry & the assembly window (viewer)

- **Geometry (engine, deterministic):** `_assign_council_seats()` places **every attendee** evenly
  on a circle — the table is sized to the full living population, not a fixed 3–4 seats. The elder
  gets the **head** seat (`isHead: true`); remaining agents fill clockwise by a stable order (role
  then name) so seats don't jump between polls. Seat `x,y` are world coords
  near the village center (or the elder's district); persisted in the session so the viewer and
  any future in-world walk-to-seat share one source of truth. Serialized under
  `/state` `civilization.dailyCouncil` (mirrors the existing `councilActive` projection at
  sim_engine.py:11827).
- **Window (index.html):** a new `#councilAssemblyModal` sibling to `#councilTranscriptModal`,
  auto-opened by `pollState()` when `dailyCouncil` is present and closed on `adjourned`
  (with a manual close/re-open control so the user isn't trapped). It renders:
  - a large round table on a 760×760 logical canvas, preferring at least 640×640 when the
    viewport permits and responsively scaling down with a square aspect ratio on short/narrow
    viewports, with the elder's chair visually distinguished at the head;
  - each attendee drawn seated using the existing `sprites.js` agent sprite + name label, placed
    at the serialized seat coords;
  - the **live transcript** (who is speaking, their opinion + feeling) streaming beside/below
    the table;
  - the **agenda** and, during voting, a **running tally** (yes/no/abstain per attendee) with
    the elder's ruling highlighted on verdict.
- **`ACTION_LABELS`** gains gerunds for the three new verbs (display only). The existing
  sidebar `#councilSection` history keeps rendering `councilLog` records unchanged.

## 6. Prompt design (cognition)

- A **council-turn prompt** built in `prompts.py` (parallel to the invention-turn prompt),
  routed when an agent carries a new `councilTurn` flag (set by the engine on seated attendees,
  cleared when they've spoken / the session adjourns — same lifecycle as `inventionTurn`). It is
  a **slim** prompt (per specs/03 slim-prompt discipline) that folds in a compact **world-status
  agenda**: era/tech tier, active projects & stalls, resource pressure, active rules, whether an
  invention is required, and the current ballot if any. It explicitly asks the agent to (a) share
  an **opinion** on the topic, (b) state a **feeling** (to satisfy "discuss opinions and feelings
  toward evolution"), and (c) optionally `council_propose` or `council_vote`.
- The elder's council turn additionally asks for the **verdict/ruling** when a ballot has reached
  quorum: it names the majority winner and ratifies it (or breaks a tie). This reuses the
  comparative-verdict framing already present for the invention elder.
- Prompt-tier routing: council turns are routine-tier by default; the elder's verdict turn routes
  to `MODEL_SMART` like other high-stakes elder decisions (specs/03 table, specs/03-cognition.md:56).
- **Digest-in-context (every agent, every think turn, not just council turns):** the most recent
  `councilDigests` entries fold into `_build_think_payload()` as one short line — "Recent
  council: ..." — the same mechanism and prompt slot as the existing Chronicle line
  (`CHRONICLE_PROMPT_ENTRIES = 3`, specs/09-systems-society.md `CULTURE_ENABLED` → Chronicle).
  A new `COUNCIL_DIGEST_PROMPT_ENTRIES` constant (default 1–2) bounds how many digest entries
  are folded in per turn, keeping prompt size flat regardless of how much the meeting actually
  discussed. This is what makes the digest "AI context friendly": every agent (attendee or not,
  since agents born/switching roles after a meeting still need continuity) carries a cheap,
  bounded reference to recent meetings without ever seeing the full transcript.

## 7. Persistence, safety, and determinism

- `dailyCouncil` is initialized `None` and `councilDigests` initialized `[]` in the civilization
  seed (next to `councilActive`/`councilLog`, sim_engine.py:1821) and both `setdefault`-restored
  in `restore_state` (next to sim_engine.py:11445) so old saves load cleanly.
- Every phase and the whole session have TTL escapes → deterministic `adjourn` that always writes
  a `councilLog` record (verdict or "adjourned without resolution"), mirroring
  `_maybe_dissolve_council` (sim_engine.py:8396) — and, unlike the invention council, **also**
  writes the full transcript into `council_transcript` and appends a `councilDigests` entry, so
  an adjourn-without-resolution still leaves both records complete for what was (and wasn't)
  discussed.
- **`state.db` schema:** `_DB_DDL` gains `CREATE TABLE IF NOT EXISTS council_transcript
  (rowid_pk INTEGER PRIMARY KEY, meeting_id INTEGER, who TEXT, type TEXT, text TEXT,
  feeling TEXT, frame_tick INTEGER, ts TEXT);` — additive, so existing DBs upgrade in place on
  next open (same as how the `memory` table was added without a migration script).

  **Pinned mechanism — mirrors `memory` exactly, not a new pattern:** the engine keeps one
  in-RAM authoritative list, `self.council_transcript_rows` (a plain Python list of row dicts,
  same shape as `memory_store.export_entries()`'s output), owned by the engine instance the
  same way `self.d["memory_store"]` is. Every transcript event is appended to this list the
  instant it happens — inside `_append_council_transcript`/`_stamp_council_event`, the same
  call site that already appends to the live session's `transcript` field — so there is no
  separate buffering, flush, or "pending rows" step to reason about. `_serialize_state()`
  (sim_engine.py:11266) exports the current full list into the payload's new
  `"council_transcript"` key, exactly like it already does for `memory`
  (sim_engine.py:11281-11287). `_write_state_db` does `DELETE FROM council_transcript` then
  reinserts the full list in the same transaction as the other three tables' delete+reinsert
  (sim_engine.py:86-104) — full-rewrite-per-save, not incremental, because that's what every
  other table here already does and it's what keeps `save_state` a single atomic transaction
  (a crash mid-write can't half-update). `_read_state_db` reads all rows back into
  `self.council_transcript_rows` on restore, mirroring the existing `memory` read
  (sim_engine.py:144-152).
- **Retention (deterministic, pinned to run at adjourn, mirrors `_run_memory_maintenance`'s
  tick-gated pruning shape):** unlike `memory`, `council_transcript` is an audit trail, not
  working context, so it is **not** read back into any prompt — only `councilDigests` is. A
  `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS = 30` constant (a real availability window, well
  past `DAILY_COUNCIL_LOG_CAP`'s 12) bounds growth: at the moment a session adjourns, after that
  session's rows are appended, the engine computes the set of distinct `meeting_id` values
  present in `self.council_transcript_rows`, keeps only the most recent
  `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS` of them, and drops every row whose `meeting_id`
  falls outside that set — one deterministic list-comprehension pass, no extra table scan, no
  background tick needed since it only ever needs to run right after a new meeting's rows land.
  This is a plain constant to raise (or set very high) if you want the full history kept
  effectively forever instead.
- **Two tiers, two audiences, both in `state.db`:** `council_transcript` (full transcript,
  per-event rows, retained per `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS`, for
  human/audit/debug review — never folded into a prompt) vs. `councilDigests` (bounded ring in
  the `civ` blob, `DAILY_COUNCIL_DIGEST_CAP`, folded into every agent's prompt every turn) vs.
  `councilLog` (bounded ring in the `civ` blob, `DAILY_COUNCIL_LOG_CAP`, read by the viewer's
  history panel only). The digest is computed once at adjourn time from the live session data,
  not reconstructed from the transcript table at runtime.
- Voting that enacts real rules goes **only** through the existing validated
  `_tally_and_maybe_enact` path — the assembly never mutates `civilization["rules"]` directly, so
  all rule caps / anti-oscillation guards still hold.
- All mutation under the engine lock; no new lock or thread. No change to `MAX_CONCURRENT_LLM`.

## 8. Phases & subagent dispatch

At the user's explicit direction, implementation used the GPT-5.6 Codex variants and reasoning
efforts recorded below. The assignments are retained as the historical execution record for
each phase. `gpt-5.6-luna` was not exposed in the configured Codex agent model list and was
therefore not assigned.
Specs are written **before** code in every phase (SDD). Each phase ends with a deterministic
smoke that a reviewer can run; the long-running phases (§8, Phase 5) emit a **results file** so
completion is checkable without watching a live soak.

### Phase 0 — Spec-first design lock  *(Codex override: `gpt-5.6-terra`, medium)*

**Model fit:** `gpt-5.6-terra` at medium reasoning is appropriate for the bounded but dense
cross-spec synthesis and contract alignment.

Write/patch the specs before any code:

- `specs/09-systems-society.md`: new "Daily Council Assembly" section (state machine, cadence,
  quorum, elder ruling, subsumption of the invention council, the two logging tiers and
  `councilDigests` retention).
- `specs/07-actions.md`: `council_speak` / `council_propose` / `council_vote` param tables.
- `specs/03-cognition.md`: council-turn prompt tier + agenda contents + the digest-in-context
  mechanic (`COUNCIL_DIGEST_PROMPT_ENTRIES`).
- `specs/11-viewer.md`: the assembly window.
- `specs/02-engine-core.md`: the `state.db` schema addition (`council_transcript` table,
  retention policy) alongside the existing `meta`/`civ`/`agents`/`memory` tables.
- `specs/01-architecture.md`: add `DAILY_COUNCIL_ENABLED` to the flag index.
- `specs/00-overview.md`: ownership map note.
**Deliverable / review:** spec diff only; no code. Reviewer reads the diff.

### Phase 1 — Engine: session state machine, trigger & persistence  *(Codex override: `gpt-5.6-sol`, high)*

**Model fit:** `gpt-5.6-sol` at high reasoning is warranted for the engine state machine,
lock-sensitive transitions, additive persistence, restore behavior, and retention invariants.

- Add constants + `civilization["dailyCouncil"]` seed + `councilDigests: []` seed +
  `restore_state` setdefault for both.
- `_maybe_convene_daily_council()` at the day boundary — **summons every living agent**
  (interrupt goal/task, set `councilTurn`, walk to assigned seat; excuse only the
  incapacitated); `_assign_council_seats()` sizes the ring to the full population;
  `_maybe_advance_daily_council()` phase machine (convening→discussion→proposal→voting→verdict→
  adjourned) with TTL escapes; `councilLog` record on adjourn.
- Wire the invention-required demand in as an agenda item; disable the reactive
  `_maybe_invention_backstop` council fan-out when the flag is on (retain elder-self-draft
  safety net).
- **`state.db` plumbing (pinned, §7):** add `council_transcript` to `_DB_DDL`; add
  `self.council_transcript_rows` as an engine-instance list, appended to inline at every
  `_append_council_transcript` call (no buffering step); extend `_serialize_state` /
  `_write_state_db` / `_read_state_db` to export/persist/restore that list with the same
  delete-then-reinsert-all transaction shape the `memory` table already uses; on adjourn, after
  appending the session's rows, prune to the most recent
  `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS` distinct `meeting_id`s; compute and append the
  `councilDigests` entry.
**Validation script → file:** `scripts/daily_council_smoke.py` (deterministic, **no Ollama**;
follows the `sid_parity_smoke.py` pattern): fast-forwards frames, forces a convene, drives the
state machine with scripted decisions through a full session, asserts phase order,
**full attendance (every living agent seated, elder at head, incapacitated excused, and —
seeded with at least one dead/buried agent in the fixture — confirms that dead agent never
appears in `attendees`, `seats`, or the vote denominator)**, TTL escapes, a clean `councilLog`
record, **and the persistence round-trip**: after adjourn, close and reopen `state.db` and
confirm the full transcript is readable from `council_transcript` (row count matches the
in-memory transcript) and `councilDigests` restored correctly, plus that retention pruning
drops rows for a meeting older than the retention window in a seeded multi-meeting fixture.
**Writes `scripts/out/daily_council_smoke.json`** (pass/fail + per-assertion detail).

### Phase 2 — Cognition: actions + council prompt  *(Codex override: `gpt-5.6-sol`, high)*

**Model fit:** `gpt-5.6-sol` at high reasoning covers the cross-file action/schema/prompt
invariants, validation paths, fallback routing, and council-turn lifecycle.

- Add the three actions across all sync points (server.py + sim_engine.py + index.html label map).
- `apply_decision` handlers `_council_speak` / `_council_propose` / `_council_vote`;
  `role_fallback_action` council branch.
- `councilTurn` flag lifecycle + slim council-turn prompt in `prompts.py`; elder verdict prompt.
**Validation script → file:** extend `scripts/daily_council_smoke.py` to exercise the three
actions, the whole-village majority tally, elder exact-tie exception, and non-tied sub-quorum
rejection deterministically; re-emits the JSON results
file. Add a one-line action-sync assertion (all three verbs present in every required list).

### Phase 3 — Viewer: the assembly window  *(Codex override: `gpt-5.6-terra`, medium)*

**Model fit:** `gpt-5.6-terra` at medium reasoning fits the bounded viewer integration,
serialized-state rendering, manual browser validation, and state-probe artifact.

- `#councilAssemblyModal`, seat rendering (round table + elder at head, seated sprites),
  live transcript, agenda, running tally, verdict highlight; auto-open/close off `/state`.
- `ACTION_LABELS` gerunds.
**Validation:** manual browser check per CLAUDE.md (run the server, open 5001, confirm the
window draws on convene). Because this is UI, no results file is required — but Phase 3 includes a
`scripts/daily_council_state_probe.py` that hits `GET /state` and **writes
`scripts/out/daily_council_state.json`** proving the `dailyCouncil` seat/agenda/tally payload the
viewer consumes is well-formed while a session is live.

### Phase 4 — Integration cleanup & spec reconciliation  *(Codex override: `gpt-5.6-sol`, high)*

**Model fit:** `gpt-5.6-sol` at high reasoning supports regression-matrix diagnosis and
reconciliation of final behavior against the canonical specs and handoff snapshot.

- Confirm specs match final code (SDD); update `docs/HANDOFF.md` snapshot; ensure
  `sid_parity_smoke.py` and `path1_smoke.py` still pass (flag on and off).
**Validation script → file:** `scripts/daily_council_regression.py` runs all three deterministic
smokes with `DAILY_COUNCIL_ENABLED` both True and False and **writes
`scripts/out/daily_council_regression.json`** (matrix of results). This is the "tell the
implementer it's done for review" artifact.

**Implemented result (2026-07-26):** PASS, 6/6 isolated subprocess cells in 3.307 seconds.
Flag-on and flag-off each passed `daily_council_smoke.py`, `sid_parity_smoke.py`, and
`path1_smoke.py`; each cell used a temporary `state.db` fixture and did not mutate source or
the real save.

### Phase 5 — Soak (long-running; script + file required)  *(Codex override: `gpt-5.6-sol`, high; then hands back)*

**Model fit:** `gpt-5.6-sol` at high reasoning fits the soak harness, retention-growth checks,
anomaly classification, and reviewable results-file logic.

- `scripts/daily_council_soak.py`: runs the engine headless (optionally with Ollama) for N
  in-world days, logging every convene/adjourn, vote tally, elder ruling, and any wedged phase or
  TTL escape. Also tracks `state.db` size and `council_transcript` row count over the run to
  confirm retention pruning actually bounds growth past
  `DAILY_COUNCIL_TRANSCRIPT_RETENTION_MEETINGS` meetings, rather than growing unbounded.
  **Writes `scripts/out/daily_council_soak_<stamp>.json`** with per-day summaries, the
  transcript-table growth curve, and a top-level `pass`/`fail` + anomaly list, so the soak's
  completion is reported by a file rather than requiring a live watch. The orchestrator/user
  checks the file to sign off.

**Completed 2026-07-26:** `scripts/daily_council_soak.py` drove 35 deterministic,
no-Ollama meetings through the real engine lifecycle and all three council action handlers
against an isolated temporary `state.db`. The stamped artifact
`scripts/out/daily_council_soak_20260726T063406Z.json` reports **PASS** in 0.613 s with zero
anomalies. At meeting 30 the transcript table reached 990 rows / 30 distinct meeting IDs and
then remained flat through meeting 35; retained IDs were exactly 6–35. SQLite grew from
114,688 bytes after meeting 1 to 294,912 bytes at meeting 30, then remained exactly 294,912
bytes through meeting 35. Digest/log caps, phase ordering, attendance/head integrity, TTL
escapes, stuck-session detection, source fingerprints, and the real `simulation/state.db`
fingerprint all passed.

## 9. Acceptance-criteria traceability

| Acceptance MUST | Covered by |
|---|---|
| **All** agents attend, around one large table, elder as head | §1 mandatory-attendance, §5 (`_assign_council_seats` sizes to full village, `#councilAssemblyModal`), Phase 1 summon + Phase 3 |
| Discuss world status / projects / limitations / ideas / proposals (replaces debate) | §3 agenda, §6 prompt, §1 subsumption, Phase 1–2 |
| Discussion + voting for improvements / modifications / new rules | §4 actions, §3 ballot, Phase 2 |
| Whole-village majority rules; elder breaks exact ties and ratifies the result; non-tied sub-quorum rejects | §3 `verdict`, §4 `council_vote` whole-village quorum + elder ruling, §6 |
| Adequately-sized window appears on convene showing all agents + round table | §5 window, Phase 3 |
| Agents discuss opinions **and feelings** toward evolution | §6 prompt (opinion + feeling required) |
| Full conversation log kept per meeting, in `state.db` (not a separate file) | §1 two-tier record, §3 `council_transcript`, §7 `state.db` schema + retention, Phase 1 |
| AI-context-friendly reference version per meeting, available to all agents | §3 `councilDigests`, §6 digest-in-context prompt fold, Phase 1–2 |
| Plan split into phases and subagents | §8 |
| Long-validation phases → script that produces a file for review | §8 Phase 1/2/4 (`daily_council_smoke.json`, `_regression.json`), Phase 3 (`_state.json`), Phase 5 (`_soak_<stamp>.json`) |

## 10. Risks / open questions

- **Cadence feel:** one assembly per in-world day (~minutes real time) may be frequent during
  fast soaks. `DAILY_COUNCIL_ENABLED` gives an off switch; cadence is a one-constant change if
  you want every-N-days instead.
- **Invention council removal:** if you'd rather **keep** the reactive invention council alongside
  the daily assembly instead of subsuming it, that's a one-flag branch (say the word and Phase 1
  keeps both).
- **Window auto-open UX:** auto-opening a modal every day could interrupt world-watching; the plan
  includes a manual toggle and closes on adjourn. Could instead be a non-modal docked panel.
