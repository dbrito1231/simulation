# HANDOFF — AI Village Simulation / Civilization Emergence Project

> Read this file FIRST when resuming this work in a new session. It is a
> point-in-time snapshot (see timestamp below) — verify anything
> load-bearing against the live system before acting on it, per the
> project's own "memory is not live state" discipline.

---

## 0. Machine-readable snapshot

```json
{
  "snapshot_generated_utc": "2026-07-08T00:35:00Z",
  "repo_root": "C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation",
  "git": {
    "branch": "feat/server-authoritative-engine",
    "head_commit": "33694a6",
    "head_subject": "fix(scheduling): don't burn a full thinkInterval when the LLM pool is full",
    "recent_commits_newest_first": [
      "33694a6 fix(scheduling): don't burn a full thinkInterval when the LLM pool is full",
      "c16aa84 fix(council): nudge the elder to review a SINGLE pending blueprint too",
      "4334634 feat(council): show wall-clock time instead of raw frame number",
      "1310c94 fix(viewer): move minimap off the Activity panel, into the map pane's corner",
      "265136c fix(invention): raise HTTP timeout for invention-only LLM calls (75s vs 30s)",
      "0f299a8 Cycle 5.morning: Phase C/D/E audits provisional-PASS/dormant-explained, Phase F landed (6c4fcab)",
      "6c4fcab feat(lifecycle): Phase F -- population lifecycle & governance depth (LIFECYCLE_ENABLED)",
      "68b1d08 Cycle 4.evening: Phase C recovery-arc provisional PASS, Phase D dormancy explained (not a bug), Phase E landed (148b03d)",
      "148b03d feat(economy): Phase E -- market, property & priced trade (ECONOMY_ENABLED)",
      "509a407 Cycle 4.morning: Phase D landed (review+security fix) -> restored recovery-arc world after data-loss near-miss",
      "3dfb73c feat(tech-tree): Phase D -- technology tiers & eras, invention council (TECH_TREE_ENABLED)"
    ],
    "known_untracked_files_at_snapshot": [
      "simulation/sprite_examples/animals.jpg (mtime 2026-06-26, predates this project's sprite work -- origin unknown, probably pre-existing/unrelated, NOT created by any agent session; leave alone unless investigated)",
      "simulation/sprite_examples/houses.jpg (same mtime/origin note as above)"
    ],
    "no_worktrees_no_branches_rule": "ALL work happens directly on feat/server-authoritative-engine. Never create a worktree or a new branch for this project -- this is a standing, explicit rule, not a default."
  },
  "server": {
    "url": "http://127.0.0.1:5001",
    "launch_method": "visible cmd window titled 'SimServer', NEVER a background/Bash task (background tasks die with the session; the user watches this window)",
    "launch_command_powershell": "Start-Process cmd -ArgumentList '/k', 'title SimServer && cd /d C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation && uv run python simulation/server.py'",
    "stop_command_powershell": "taskkill /F /FI \"WINDOWTITLE eq SimServer*\" ; then taskkill /F /PID <pid> for anything still listening on 5001",
    "health_check": "HTTP 200 from http://127.0.0.1:5001/ AND newest simulation/logs/<ts>/*.jsonl files growing",
    "standing_rule": "The simulation runs 24/7. Both scheduled cycle stages MUST end with the server running (sole exception: LM Studio itself is down). Interactive/manual sessions should also leave it running now -- the old 'kill it when you're done' habit is SUPERSEDED (see memory file kill-server-after-handoff.md).",
    "status_at_snapshot": "running, PID varies, port 5001 responding 200"
  },
  "lm_studio": {
    "current_model": "qwen/qwen3.5-9b",
    "context_length": 13000,
    "parallel_slots": 2,
    "tokens_per_slot": 6500,
    "load_command": "lms load qwen/qwen3.5-9b --context-length 13000 --parallel 2 -y",
    "why_this_model": "2026-07-05 replay benchmark (100 logged prompts vs gemma-4-e4b): equal JSON/action validity, but qwen halved move_to_district fixation (32% vs 65%), 9 vs 7 distinct actions, 20/20 vs 19/20 valid blueprints, at ~3s/decision more. Full method: docs/civilization-emergence-plan.md Part 6.",
    "quirk": "qwen is a 'thinking' model -- its actual answer usually lands in the response's reasoning_content field with content empty. server.py's extractor (lm_message_text / extract_json_decision, ~line 2048-2055) already handles this. Any NEW code that reads raw LLM responses must do the same or it will silently see empty output.",
    "fallback_model_on_disk": "google/gemma-4-e4b (6.33 GB) -- NOT currently loaded; do not run both models simultaneously, the 12GB card cannot hold two loaded models without starving both (measured fact from 2026-07-02, see CLAUDE.md)."
  },
  "world_state_at_snapshot": {
    "frame_tick": 736201,
    "era": "Forge Era",
    "level": 27,
    "agent_count": 12,
    "structure_count": 70,
    "note": "This is a FRESH world (reset 2026-07-05) with ALL implemented flags on from frame 0 -- NOT the old 416-structure legacy world, which is archived at archive/state.json (dated 2026-07-02) for regression reference, never restore it over the live save without explicit user instruction."
  },
  "feature_flags_all_on_current_world": {
    "SURVIVAL_ENABLED": true,
    "CRAFTING_ENABLED": true,
    "USE_GOALS": true,
    "STRUCTURE_EFFECTS_ENABLED": "implied on (Phase A, always-on since landed, no separate toggle observed in /state flags -- verify in sim_engine.py if this matters)",
    "ECOLOGY_ENABLED": true,
    "ROADS_ENABLED": true,
    "GOODS_ENABLED": true,
    "TECH_TREE_ENABLED": true,
    "ECONOMY_ENABLED": true,
    "LIFECYCLE_ENABLED": true,
    "EMERGENT_ROLES": true,
    "RULES_ENABLED": true,
    "MEMES_ENABLED": true,
    "PIANO_MODULES": false,
    "META_SYSTEM": false
  },
  "phase_status": {
    "A_consequence_engine": "PASSED (2026-07-03) -- structure function registry, no more decorative buildings",
    "B_ecology_terraforming": "PASSED after 3 loop-backs (final pass 2026-07-05/06, cycle 1/2) -- district stocks, depletion/regrowth, terraform, project abandonment, scarcity reflex",
    "C_goods_decay_seasons": "IMPLEMENTED (commit f555a46), audited PROVISIONAL PASS multiple cycles running (recovery-arc trend confirmed slow but real: ruins healing, repair_structure firing) -- long-soak trend confirmation still OPEN, see still_open below",
    "D_tech_tree_eras_council": "IMPLEMENTED (commit 3dfb73c) -- tiers, eras, invention council, LLM-authored sprites, sprite few-shot examples. Core mechanics verdict: PASS via forced smoke test. Organic (unprompted, real-world) exercise of the council had NEVER produced a resolved verdict until THIS SESSION's three bug fixes (see section 6) -- still needs a confirmed organic resolved debate as the final open item",
    "E_market_property": "IMPLEMENTED (commit 148b03d) -- pricing, priced trade, property claims, relationship-conditioned trade terms. No market structure has been organically built yet in the live world as of snapshot -- mechanics unexercised in the wild, not a bug (root-caused by cycle 5.morning: nobody has built one)",
    "F_population_lifecycle": "IMPLEMENTED (commit 6c4fcab, LIFECYCLE_ENABLED=true) -- aging, birth, natural death (elder included), succession via existing vote scaffold, harvest_quota/rationing rule kinds. Verified so far ONLY via the implementer's forced/scratch-copy smoke test -- a live multi-hour organic soak is the open item",
    "G_culture_diplomacy": "NOT STARTED. Pre-staged implementation prompt exists at .cursor/phase-prompts/phase-G.md, written 2026-07-05, includes a recon-grade change map. Two sub-flags planned: CULTURE_ENABLED and DIPLOMACY_ENABLED (diplomacy is separable/optional if a slot runs long)."
  },
  "scheduled_tasks": {
    "civilization-cycle-morning": {
      "cron": "30 7 * * *",
      "human_schedule": "7:30/7:38 AM daily (matches original design)",
      "enabled": true,
      "last_run_utc": "2026-07-07T13:14:07Z"
    },
    "civilization-cycle-night": {
      "cron": "0 1 * * *",
      "human_schedule": "1:08 AM daily",
      "enabled": true,
      "last_run_utc": "2026-07-07T12:32:15Z",
      "DRIFT_WARNING": "This was ORIGINALLY configured for ~9:38 PM ('evening slot' -- see its own description field, which still says 'Evening slot of the compressed twice-daily cycle'). The live cronExpression now fires at 1:08 AM instead. Cause unknown -- not changed by this session. Verify current schedule with list_scheduled_tasks before assuming Part 8's '~21:30 / ~07:30' timing table in the plan doc is accurate; it is NOT currently accurate for the night slot. Either fix the cron back to evening, or accept 1 AM as the new de-facto schedule and update the plan doc -- user has not been asked yet."
    },
    "both_tasks_use_prompts_that": "read docs/civilization-emergence-plan.md Part 8 as the authoritative procedure at run time, so behavior is governed by the plan doc, not just the frozen SKILL.md prompt text -- keep Part 8 accurate."
  },
  "cycle_state_file": ".claude/overnight-cycle.json",
  "cycle_state_at_snapshot": {
    "lastReviewedCommit": "6c4fcab",
    "iteration": 6,
    "phase": "F",
    "note_summary": "Last morning cycle (5.morning) landed Phase F and gave provisional/dormant-explained verdicts for C/D/E. Full verbatim note preserved in the file itself -- read it, it's long and detailed.",
    "STALE_WARNING": "lastReviewedCommit (6c4fcab) predates FIVE commits made in this interactive session (265136c through 33694a6). The next scheduled cycle run will review 6c4fcab..HEAD, which is correct and by design -- but be aware the 'iteration: 6' counter and phase:'F' marker have not been bumped by this session's manual fixes. That's fine (this session was interactive debugging, not a cycle slot), just don't assume iteration number tracks 1:1 with commit count."
  },
  "next_prompt_file": ".cursor/next-prompt.md",
  "next_prompt_status": "DELETED by this session (2026-07-08) -- it contained the Phase F prompt, which was already fully implemented and committed as 6c4fcab. It was never cleaned up by the cycle that consumed it (a minor process gap in that slot). If a NEW next-prompt.md appears, it means a cycle slot queued follow-up work -- read it before assuming Phase G is next by default.",
  "pre_staged_phase_prompts": {
    "location": ".cursor/phase-prompts/phase-{C,D,E,F,G}.md",
    "status": "C, D, E, F have been consumed/implemented (their scope is now history, see phase_status above). ONLY phase-G.md remains un-implemented and is the next phase in sequence once E/F organic soaks are confirmed and D's council is confirmed working."
  },
  "session_focus_this_conversation": "User first asked for a Copilot-style intelligence audit response plan, then set up the twice-daily overnight automation cycle, then asked 'why don't I see the council debate in the GUI' which turned into a 3-bug debugging arc (see section 6) fully resolved and committed by end of session.",
  "open_items_ranked_by_priority": [
    "1. CONFIRM an organic (non-forced) council debate actually resolves with a verdict now that all 3 scheduling/timeout/nudge bugs are fixed -- a council was ACTIVE at snapshot time (proposers: Ivy, Aria, Zara, 0 proposals yet) -- check its outcome first thing next session via GET /state civilization.councilLog[0]",
    "2. Fix or accept the civilization-cycle-night schedule drift (21:38 configured -> 1:08 AM actual) -- see scheduled_tasks.civilization-cycle-night.DRIFT_WARNING above",
    "3. Phase C long-soak trend confirmation (ruins healing slowly, 408/416 was the last count -- re-check via GET /state or logs)",
    "4. Phase E first organic market build (watch for a start_project targeting the seed 'market' type)",
    "5. Phase F first organic multi-hour soak (births/deaths/succession have only been smoke-tested, never observed live)",
    "6. Investigate or ignore the unexplained 2026-07-07 08:45-09:02 crash-loop (~20 rapid restarts, no error evidence) -- watch for recurrence; if it repeats, the next cycle instance is instructed to capture console output for a traceback",
    "7. Phase G (culture, knowledge transmission, factions, diplomacy) -- not started; prompt is pre-staged and ready"
  ]
}
```

---

## 1. What this project is

A real-time, browser-based AI village simulation (8-12 autonomous LLM-driven
pixel-art agents) being incrementally evolved from a "decision dispenser
wrapped around a fixed-topology world" into something resembling an actual
emergent civilization. The governing document for that evolution is
**[civilization-emergence-plan.md](civilization-emergence-plan.md)** — read
it in full before doing anything structural. This handoff file does not
replace it; it's a fast-resume pointer plus a record of this session's
specific work.

**[CLAUDE.md](../CLAUDE.md)** (repo root) has the architecture reference:
four files do all the work (`sim_engine.py` = the world, `server.py` =
Flask + the LLM pipeline, `index.html` = thin viewer, `sprites.js` = pure
Canvas drawing), plus `roles.json` as the single source of truth for role
data. Read it for file responsibilities before editing anything.

## 2. How to resume — the fast path

1. Read this file's JSON block (section 0) for the objective snapshot.
2. Verify it against reality — things drift (see the schedule DRIFT_WARNING
   above, found live in this session): `git log --oneline -5`,
   `curl -s http://127.0.0.1:5001/state`, `lms ps`,
   `mcp__scheduled-tasks__list_scheduled_tasks`.
3. Read `docs/civilization-emergence-plan.md` — specifically:
   - **Part 4** for phase scopes and their civilization tests.
   - **Part 5** for the standing re-analysis audit questions.
   - **Part 7** for the subagent relay pattern.
   - **Part 8 "Compressed cadence"** for the automated twice-daily cycle
     procedure — this is what actually runs the project day to day.
4. Read `.claude/overnight-cycle.json` for the last cycle's own verbatim
   notes (long, detailed, written by the cycle itself each run).
5. If `.cursor/next-prompt.md` exists, that's queued work for the next
   cycle slot — read it before assuming what's "next."
6. Pick up the highest-priority open item from section 0's
   `open_items_ranked_by_priority`, or whatever the user asks for.

## 3. The automated overnight cycle (Part 8) — operating this project

Two scheduled Claude Code tasks (`civilization-cycle-morning`,
`civilization-cycle-night`) each run a full audit → hot-fix/implement →
review → restart-server cycle, twice a day, autonomously. This is how most
of Phases B through F got implemented and audited — not through manual
interactive sessions like this one. Key facts:

- **The simulation runs 24/7.** Both stages end with the server running,
  no exceptions except LM Studio being down.
- **State lives in two files**: `.claude/overnight-cycle.json` (last
  reviewed commit, iteration, phase, and a running prose log) and
  `.cursor/next-prompt.md` (the next implementation prompt, if any is
  queued — its absence means "soak only, nothing to implement").
- **Hot-fix authority**: a cycle stage may fix small, precisely-understood
  bugs itself in-session rather than always writing a loop-back prompt for
  the next slot — this is what makes the twice-daily cadence viable.
- **Pre-staged phase prompts** at `.cursor/phase-prompts/phase-{C..G}.md`
  give each phase's implementer a recon-grade head start instead of
  re-deriving the codebase from scratch every time.
- **The schedule has drifted** (see section 0) — verify actual cron times
  with `list_scheduled_tasks`, don't trust the plan doc's "~21:30/~07:30"
  table blindly.

## 4. This session's work: the invention-council debugging arc

This was an interactive (non-scheduled-cycle) session. Chronology, each
step fully committed:

1. **Model switch to qwen3.5-9b** + pre-staged Phase C-G prompts (commit
   before this session's visible history, referenced in Part 6 of the plan).
2. **Added a diegetic invention council** (Karpathy LLM-council pattern,
   applied only where it's affordable: rare high-stakes invention events,
   not routine turns) — 2-3 villagers propose in parallel, the elder judges
   comparatively. Landed as part of Phase D (`3dfb73c`).
3. **Built two GUI views for council debates**: a persisted sidebar panel
   (`#councilSection` in `index.html`, backed by `civilization.councilLog`)
   and a live "Council in session" banner. Also landed in Phase D.
4. **Added LLM-authored structure sprites** — blueprints can carry an
   optional `{palette, grid}` pixel-art spec; a procedural generator covers
   customs that don't (no more letter-in-a-box fallback). Plus 7 few-shot
   sprite examples derived from Kenney's CC0 "Tiny Town" asset pack
   (`simulation/sprite_examples/`, license documented there).
5. **User reported**: "I see a council in session but no way to view the
   debate." Investigation found the GUI was rendering correctly the whole
   time — the underlying pipeline just never produced a resolved debate to
   show. Three independent bugs were found and fixed, each verified with a
   live restart:
   - **Bug 1 (`265136c`)**: invention-turn prompts (function schema + tier
     rules + sprite instructions) measured a median 32s response time,
     just over the flat 30s HTTP timeout used for every decision call —
     71% of invention calls were silently timing out and falling back to
     mundane actions. Fixed with a per-call timeout
     (`INVENTION_TIMEOUT_S = 75`, routine calls unaffected at 30s) and a
     matching `COUNCIL_TTL_FRAMES` increase.
   - **Bug 2 (`c16aa84`)**: even after fixing timeouts, a real successful
     proposal (Marco's "Storage House") sat unapproved indefinitely,
     because the elder's only nudge pointing at pending blueprints required
     **2 or more** pending — a lone valid proposal got zero prompt signal.
     Fixed: nudge now fires at `>= 1` with matching singular wording.
     **Confirmed working**: Storage House was approved and built within
     this same session after the fix (see `simulation/logs/2026-07-07T20-25-13/activity.jsonl`,
     "Marco built Storage House in village_east").
   - **Bug 3 (`33694a6`)**: a flagged council member could lose their
     one-shot invention turn entirely if their scheduling timer expired
     while both `MAX_CONCURRENT_LLM=2` worker slots were busy —
     `_schedule_think` silently failed to dispatch, but the caller reset
     the agent's `thinkTimer` to the FULL interval anyway (up to 20+
     seconds) rather than retrying soon. This is what happened to "Sage"
     in one debate — the other two members got dispatched, he never did.
     Fixed: `_schedule_think` now reports whether it actually dispatched;
     on failure the agent retries in `THINK_RETRY_FRAMES` (0.5s) instead
     of waiting a full cycle. This is a general scheduling-fairness fix,
     not council-specific.
6. Along the way: also fixed the minimap's CSS position (was
   `position:fixed` bottom-left, colliding with the Activity panel — moved
   to the map pane's own corner) and added wall-clock timestamps to council
   debate records (was showing raw frame numbers; `ts` field added,
   graceful fallback to `frame N` for the 12 pre-existing records that
   predate the change).

**All six fixes from tonight are committed and the server is running with
all of them live.** The one thing NOT yet confirmed: an organic council
debate actually reaching a resolved verdict end-to-end (proposal → elder
review → approval or rejection with reasons) without any forced/manual
intervention. A council was active at snapshot time — check its outcome
first.

## 5. Known gotchas (accumulated project-wide, not just this session)

- **`index.html` used to contain a full legacy client-side simulation** —
  removed in the C5 cleanup (`3c22b96`), but if you ever see code there
  that looks like it's mutating world state instead of just rendering
  `/state`, that's a regression, not a feature — the engine
  (`sim_engine.py`) owns ALL simulation state; the browser is a thin
  viewer.
- **`archive/state.json`** (2026-07-02, 416 structures) is the pre-reset
  legacy world, kept for regression reference. Never restore it over the
  live save without explicit user instruction.
- **`archive/simulation/state.json`** also exists (nested path) — appears
  to be an artifact from a "data-loss near-miss" recovery mentioned in the
  cycle-4.morning commit; not fully explained, treat as historical debris
  unless investigated further.
- **Prompt token budget**: the decision prompt has grown substantially
  across phases (function schema, tier rules, ecology stocks, sprite
  instructions, sprite few-shot example, lifecycle fields). No hard
  overflow has been observed with the current 13000/2-slot LM Studio
  config, but if `context_overflow` retries start appearing in
  `lm_studio.jsonl`, that budget is the first thing to check — either
  raise LM Studio's context length or trim a phase's prompt section.
- **Never load two models at once** on this 12GB card — confirmed
  empirically to starve both (2026-07-02 measurement, documented in
  CLAUDE.md).
- **`git status` shows two untracked jpg files** in
  `simulation/sprite_examples/` (`animals.jpg`, `houses.jpg`, dated
  2026-06-26, predating all sprite work) — origin unknown, left alone by
  this session, worth a one-time look if anyone has time.

## 6. Verification commands (copy-paste ready)

```powershell
# Server health
(Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
Invoke-WebRequest http://127.0.0.1:5001/ -UseBasicParsing | Select StatusCode

# LM Studio status
lms ps

# Restart the server (canonical method -- NEVER a background Bash task)
taskkill /F /FI "WINDOWTITLE eq SimServer*" 2>$null
Start-Process cmd -ArgumentList '/k', 'title SimServer && cd /d C:\Users\dbadmin\Desktop\GitServ\simulation && uv run python simulation/server.py'
```

```bash
# Git state
cd "C:/Users/dbadmin/Desktop/GitServ/simulation" && git log --oneline -10 && git status --short

# Current world snapshot
curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); print(d['frameTick'], d['civilization']['era'], d['config']['flags'])"

# Latest council debate outcome
curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); print(d['civilization']['councilActive']); print(d['civilization']['councilLog'][0] if d['civilization']['councilLog'] else 'none')"

# Newest session log folder
ls -td simulation/logs/*/ | head -1
```

```
# Scheduled tasks (MCP tool, not shell)
mcp__scheduled-tasks__list_scheduled_tasks
```

## 7. Persistent memory cross-reference

Two memory files exist at
`C:\Users\dbadmin\.claude\projects\C--Users-dbadmin-Desktop-GitServ-simulation\memory\`:
`kill-server-after-handoff.md` (records the 24/7-server standing rule) and
an overnight-cycle entry in `MEMORY.md` pointing back to this plan doc.
This HANDOFF.md is the more complete, up-to-date resume point — memory
files are a quick index, this file is the full snapshot.
