# HANDOFF — AI Village Simulation / Civilization Emergence Project

> Read this file FIRST when resuming this work in a new session. It is a
> point-in-time snapshot (see timestamp below) — verify anything
> load-bearing against the live system before acting on it, per the
> project's own "memory is not live state" discipline.

---

## 0. Machine-readable snapshot

```json
{
  "snapshot_generated_utc": "2026-07-09T01:05:00Z",
  "repo_root": "C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation",
  "git": {
    "branch": "feat/server-authoritative-engine",
    "head_commit": "879982f",
    "head_subject": "docs(skills): add manual morning/evening civilization cycle skills",
    "uncommitted_changes_at_snapshot": [
      "simulation/sim_engine.py — cemetery_grounds starter district, structure-style grave_grid, burial migration on restore, PROJECT_KIND cemetery",
      "simulation/sprites.js — TILE_CEMETERY, cemetery district/road sync, tombstones only when buried, fenced cemetery props",
      "simulation/index.html — deferred terrain cache + loading overlay, living-only Agents list + Deceased modal, dead vs collapsed labels, resource dots on hover/sidebar-select only"
    ],
    "recent_commits_newest_first": [
      "879982f docs(skills): add manual morning/evening civilization cycle skills",
      "8b81ee5 docs: refresh HANDOFF.md and REMAINING-WORK-PLAN.md to current state",
      "fc04070 feat(lifecycle): cemetery + burial for permanent death (CEMETERY_ENABLED)",
      "16224ee fix(viewer): draw a generic sprite for agents with no hand-drawn entry",
      "b3587aa Cycle 7.morning: PASS all flags on 6h soak; hot-fixed invention-council id-collision blindspot",
      "00df296 fix(invention): expose reserved seed structure ids on regular prompt turns",
      "d4d59e5 Cycle 6.evening: Phase F fixes (aging rate, heal_agent zombies) verified holding; Phase G (CULTURE_ENABLED) landed",
      "4889c09 feat(culture): Phase G -- knowledge, culture & personality drift (CULTURE_ENABLED)",
      "8902465 fix(lifecycle): correct aging rate that was running 10x too fast",
      "be47a60 feat(viewer): move the Council panel from the right sidebar to the left Activity/Chat column",
      "6e930ca fix(lifecycle): stop heal_agent from resurrecting corpses into zombies",
      "33a309a docs: add machine-readable HANDOFF.md for session resume"
    ],
    "known_untracked_files_at_snapshot": [
      "simulation/sprite_examples/animals.jpg (predates this project's sprite work, origin unknown, leave alone)",
      "simulation/sprite_examples/houses.jpg (same)",
      "simulation/sprite_examples/cemetery_capture.png (session screenshot, optional to commit or gitignore)",
      "simulation/sprite_examples/cemetery_structure.png (same)"
    ],
    "no_worktrees_no_branches_rule": "ALL work happens directly on feat/server-authoritative-engine. Never create a worktree or a new branch for this project."
  },
  "server": {
    "url": "http://127.0.0.1:5001",
    "launch_method": "visible cmd window titled 'SimServer', NEVER a background/Bash task",
    "launch_command_powershell": "Start-Process cmd -ArgumentList '/k', 'title SimServer && cd /d C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation && uv run python simulation/server.py'",
    "stop_command_powershell": "taskkill /F /FI \"WINDOWTITLE eq SimServer*\" ; then taskkill /F /PID <pid> for anything still listening on 5001 -- ALWAYS confirm the port is free before editing state.json directly, an autosave from a still-running process will silently clobber a manual data patch",
    "health_check": "HTTP 200 from http://127.0.0.1:5001/ AND newest simulation/logs/<ts>/*.jsonl files growing",
    "standing_rule": "The simulation runs 24/7. Both scheduled cycle stages MUST end with the server running. Interactive sessions should also leave it running.",
    "status_at_snapshot": "running, port 5001 responding 200, single instance confirmed (process tree: cmd -> uv.exe -> venv python.exe -> python.exe bound to 5001)",
    "gui_static_preview_config": ".claude/launch.json has a 'gui-static-preview' entry (plain `python -m http.server` on 8899, serves simulation/ statically) for safely spot-checking pure client-side rendering changes (sprites.js/index.html CSS/DOM) WITHOUT risking a second engine instance touching the live state.json. Never use the 'simulation-server-verify' config or otherwise run a second simulation/server.py -- sim_engine.py's STATE_PATH is hardcoded relative to its own file, so any second instance reads/writes the SAME state.json as the live 24/7 server; this was nearly done by accident this session (caught before real damage, see section 5)."
  },
  "lm_studio": {
    "current_model": "qwen/qwen3.5-9b",
    "context_length": 13000,
    "parallel_slots": 2,
    "load_command": "lms load qwen/qwen3.5-9b --context-length 13000 --parallel 2 -y",
    "quirk": "qwen is a 'thinking' model -- its actual answer usually lands in reasoning_content with content empty. server.py's extractor already handles this."
  },
  "world_state_at_snapshot": {
    "frame_tick": 1457852,
    "era": "Wagon Era",
    "agent_count": 21,
    "structure_count": 224,
    "deceased_agents": 17,
    "buried_agents": 17,
    "cemetery": {
      "structure_id": 148,
      "district_id": "cemetery_grounds",
      "position": [340, 980],
      "note": "After the uncommitted cemetery-layout work loads (server restart), the chapel sits on cemetery_grounds' build_grid and all 17 buried agents are re-seated on the district grave_grid (structure spacing, no slot wraparound). Prior to that migration the chapel lived in a founded village district with graves clustered on 18px offsets."
    },
    "note": "Same 2026-07-05 reset world, still running 24/7. Population grew 12→21 via newcomers; structures 70→224. All 17 deaths so far are buried. Pre-reset 416-structure legacy world remains at archive/state.json — never restore over live save without explicit instruction."
  },
  "feature_flags_all_on_current_world": {
    "SURVIVAL_ENABLED": true, "CRAFTING_ENABLED": true, "USE_GOALS": true,
    "ECOLOGY_ENABLED": true, "ROADS_ENABLED": true, "GOODS_ENABLED": true,
    "TECH_TREE_ENABLED": true, "ECONOMY_ENABLED": true, "LIFECYCLE_ENABLED": true,
    "CULTURE_ENABLED": true, "CEMETERY_ENABLED": true, "EMERGENT_ROLES": true,
    "RULES_ENABLED": true, "MEMES_ENABLED": true, "PIANO_MODULES": false, "META_SYSTEM": false,
    "note": "DIPLOMACY_ENABLED does not exist yet -- see phase_status.G below."
  },
  "phase_status": {
    "A_through_F": "All PASSED/CONFIRMED via organic multi-hour soaks logged in .claude/overnight-cycle.json (cycle 6.evening and 7.morning entries) -- Phase D's invention council now reaches organic comparative verdicts (Mine Cart approved among others), Phase E's market is built and exercised (wealth_gini falling), Phase F's aging/death/succession confirmed clean under the corrected rate (see section 4).",
    "G_culture": "LANDED (commit 4889c09, CULTURE_ENABLED=true) and CONFIRMED via organic soak (cycle 7.morning audit): skills-by-practice, Library knowledge persistence, and personality/chronicle all firing live. Teaching (teach_count) and meme mutation remain organically unexercised so far -- low-probability/keyword-gated, not a defect, still an open watch item.",
    "G_diplomacy": "NOT STARTED. The original plan (docs/REMAINING-WORK-PLAN.md as first written) called for implementing CULTURE_ENABLED and DIPLOMACY_ENABLED together in one pass; in practice the automated cycle implemented only culture and explicitly deferred diplomacy out of its batch (see .claude/overnight-cycle.json still_open list, cycle 7.morning). Second settlement, inter-village trade caravans, and treaty/rivalry state remain unimplemented. This is the only unimplemented net-new phase.",
    "cemetery_burial": "LANDED (fc04070) and extended this session with uncommitted viewer/engine polish: dedicated cemetery_grounds district, structure-style grave grid, tombstones only after burial, Agents sidebar cleanup. See section 4 items 7-8."
  },
  "scheduled_tasks": {
    "civilization-cycle-morning": { "cron": "30 7 * * *", "human_schedule": "7:38 AM daily", "enabled": true, "last_run_utc": "2026-07-08T11:38:40Z" },
    "civilization-cycle-night": { "cron": "0 1 * * *", "human_schedule": "1:08 AM daily", "enabled": true, "last_run_utc": "2026-07-08T05:08:14Z", "note": "Schedule drift from the original ~9:38 PM design is a settled, documented decision (see civilization-emergence-plan.md Part 8) -- do not re-flag this as an anomaly." },
    "both_tasks_use_prompts_that": "read docs/civilization-emergence-plan.md Part 8 as the authoritative procedure at run time.",
    "cursor_manual_alternative": {
      "location": ".cursor/skills/civilization-cycle-morning/SKILL.md and civilization-cycle-night/SKILL.md",
      "commit": "879982f",
      "note": "User runs the same Part 8 morning/evening procedure manually in Cursor when Claude Code scheduled-task tokens are exhausted. disable-model-invocation: true on both skills."
    }
  },
  "cycle_state_file": ".claude/overnight-cycle.json",
  "cycle_state_at_snapshot": {
    "lastReviewedCommit": "00df296",
    "iteration": 8,
    "phase": "G",
    "note_summary": "Cycle 7.morning: full PASS on a ~6h/509,100-frame soak across all active flags; hot-fixed a reserved-structure-id blindspot that was causing 82% of invention-council proposal rejections (commit 00df296). Phase G's culture side confirmed organically working. DIPLOMACY_ENABLED explicitly deferred as the last unscheduled batch item. Full verbatim note is long and detailed -- read the file directly.",
    "STALE_WARNING": "lastReviewedCommit (00df296) predates commits 879982f (manual cycle skills), 8b81ee5 (doc refresh), fc04070 (cemetery), 16224ee (generic sprite), plus uncommitted cemetery/viewer work. The next scheduled or manual cycle should review 00df296..HEAD including any committed cemetery-layout follow-up."
  },
  "next_prompt_file": ".cursor/next-prompt.md",
  "next_prompt_status": "Does not exist. No follow-up implementation is queued for the next cycle slot beyond DIPLOMACY_ENABLED (not pre-staged as a phase-prompt file the way C-G were).",
  "pre_staged_phase_prompts": {
    "location": ".cursor/phase-prompts/phase-{C,D,E,F,G}.md",
    "status": "All consumed/implemented. No phase-H or diplomacy-specific prompt has been pre-staged -- if diplomacy is tackled next, recon-from-scratch (or write a new pre-staged prompt first) rather than expecting one to exist."
  },
  "session_focus_this_conversation": "Interactive session (continuation): (1) created manual Cursor skills for Part 8 morning/evening cycle (879982f), (2) diagnosed GUI stall on page load (sync terrain cache) and fixed with requestIdleCallback + loading overlay, (3) diagnosed cluttered/scattered tombstones — old 18px grave offsets wrapped after 12 burials and cemetery was built in a random village district, (4) implemented cemetery_grounds starter district with structure-style grave_grid and restore-time migration, (5) tombstones render only after burial (unburied dead stay as greyed bodies at death site), (6) Agents sidebar shows living villagers only with a Deceased modal and dead vs collapsed labels, (7) resource inventory dots hidden by default — shown only on canvas hover or sidebar agent selection. Cemetery/viewer changes are uncommitted as of this snapshot; server restart required to apply grave migration on live state.json.",
  "open_items_ranked_by_priority": [
    "1. Commit the uncommitted cemetery-layout + viewer UX work (sim_engine.py, sprites.js, index.html) and restart the server so restore_state migration re-seats buried agents",
    "2. DIPLOMACY_ENABLED (second settlement, inter-village trade caravans, treaty/rivalry state) -- the only unimplemented net-new phase; no pre-staged prompt exists, recon needed first",
    "3. Watch Phase C's structure_condition trend over a longer soak (95.3->69.7 avg as structure count nearly quadrupled last cycle) -- confirm it stabilizes rather than trending toward the 30 disrepair floor",
    "4. Priced trade (ECONOMY_ENABLED) is still thin (1 buy/sell pair in a 6h soak) -- watch for more organic market activity, no code change expected yet",
    "5. Teaching (teach_count) and meme mutation remain organically unexercised (both are correctly gated by keyword/probability, not confirmed defects) -- watch for natural occurrence over more soak time",
    "6. Cemetery/burial: confirm post-layout soak — new deaths bury into cemetery_grounds grid, no duplicate grave positions, disrepaired chapel (condition can fall below STRUCTURE_DISREPAIR_THRESHOLD) still accepts burials",
    "7. Tier 4 / final acceptance per civilization-emergence-plan.md Part 5: once diplomacy lands and everything above closes out, run the long all-flags-on soak looking for genuinely unanticipated emergent events (two consecutive audits), not just mechanical PASS verdicts"
  ]
}
```

---

## 1. What this project is

A real-time, browser-based AI village simulation (started at 8-12, now 21
autonomous LLM-driven pixel-art agents) being incrementally evolved from a
"decision dispenser wrapped around a fixed-topology world" into something
resembling an actual emergent civilization. The governing document for that
evolution is **[civilization-emergence-plan.md](civilization-emergence-plan.md)**
— read it in full before doing anything structural. This handoff file does
not replace it; it's a fast-resume pointer plus a record of recent session
work.

**[CLAUDE.md](../CLAUDE.md)** (repo root) has the architecture reference:
four files do all the work (`sim_engine.py` = the world, `server.py` =
Flask + the LLM pipeline, `index.html` = thin viewer, `sprites.js` = pure
Canvas drawing), plus `roles.json` as the single source of truth for role
data. Read it for file responsibilities before editing anything.

## 2. How to resume — the fast path

1. Read this file's JSON block (section 0) for the objective snapshot.
2. Verify it against reality: `git log --oneline -5`,
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
   cycle slot — read it before assuming what's "next." (It does not exist
   as of this snapshot.)
6. Pick up the highest-priority open item from section 0's
   `open_items_ranked_by_priority`, or whatever the user asks for.

## 3. The automated overnight cycle (Part 8) — operating this project

Two scheduled Claude Code tasks (`civilization-cycle-morning`,
`civilization-cycle-night`) each run a full audit → hot-fix/implement →
review → restart-server cycle, twice a day, autonomously. This is how most
of Phases B through G got implemented and audited — not through manual
interactive sessions. **Manual alternative:** `.cursor/skills/civilization-cycle-{morning,night}/`
(commit `879982f`) runs the same Part 8 procedure on demand in Cursor when
scheduled-task tokens are unavailable. Key facts:

- **The simulation runs 24/7.** Both stages end with the server running,
  no exceptions except LM Studio being down.
- **State lives in two files**: `.claude/overnight-cycle.json` (last
  reviewed commit, iteration, phase, and a running prose log) and
  `.cursor/next-prompt.md` (the next implementation prompt, if any is
  queued — its absence means "soak only, nothing to implement").
- **Hot-fix authority**: a cycle stage may fix small, precisely-understood
  bugs itself in-session rather than always writing a loop-back prompt.
  Interactive sessions use the same authority (see section 4's zombie/aging
  fixes, both applied directly rather than queued).
- **Pre-staged phase prompts** at `.cursor/phase-prompts/phase-{C..G}.md`
  gave each phase's implementer a recon-grade head start — all consumed now.
- **The schedule (1:08 AM / 7:38 AM)** is a settled decision, not drift to
  chase — see `civilization-emergence-plan.md` Part 8.

## 4. Recent interactive-session work (chronological, each step committed)

1. **Zombie-heal bug (`6e930ca`)**: `heal_agent`/`_neediest_nearby` never
   checked `deathFrame`, so a healer could flip a permanently-dead agent's
   `incapacitated` back to `False` while `deathFrame` stayed set — an
   ambulatory, thinking corpse that could even get flagged for invention-
   council turns it could never complete. All 8 dead agents in the live
   world at the time were already corrupted this way. Fixed at the source
   (`heal_agent`, `_neediest_nearby`, `_idle_agents_for_elder` now check
   `deathFrame` directly) and the live data was one-time patched.
2. **Council panel moved (`be47a60`)**: from the right sidebar into the
   left Activity/Chat column, per user request. Verified via a static
   (no-engine) preview server — DOM placement, no duplicate ids, correct
   layout at desktop width.
3. **Aging-rate bug (`8902465`)**: `_tick_lifecycle()` multiplied
   `AGE_YEARS_PER_TICK` by an erroneous extra 10x factor (`LIFECYCLE_TICK_FRAMES
   / 30.0`), so agents aged 1 year per 1,500 frames instead of the documented
   1 year per 15,000. Consequence: 8 of 12 agents blew past death-eligible
   age within ~30 minutes of the fresh world's life and died in a cluster
   (frames 62100-92400), crashing population to `POPULATION_FLOOR` (4) where
   it correctly stayed wedged (the floor mechanism worked as designed — that
   part wasn't a bug). Fixed the rate; the 4 survivors' already-inflated ages
   (676-701 "years") were one-time recomputed to a realistic 88-114 matching
   what ~9h of real 24/7 runtime actually implies at the correct pace.
4. **User reset the world** (fresh frame 0, all fixes already live).
5. **Generic-sprite bug (`16224ee`)**: `AGENT_SPRITES` in `sprites.js` is a
   hand-authored dict keyed by only the original 12 agent names.
   `drawAgentSprite` silently no-op'd for any other name, but `drawAgent`
   in `index.html` still drew the health bar/label/resource dots
   unconditionally — so newcomer/newborn agents (e.g. the `Villager1000+`
   names from the newcomer-welcome mechanic) rendered as a floating name
   with no body. Fixed with a generic fallback sprite (the shared body
   shape every hand-drawn entry already uses, palette-ized from the
   agent's own `color` field). Verified via `getImageData` pixel inspection
   on a static preview server.
6. **Cemetery & burial feature (`fc04070`)**: user reported disliking that
   dead agents "just die in random places" and asked for a full cemetery
   mechanic. Implemented `CEMETERY_ENABLED` end-to-end, additive to Phase F,
   mirroring the existing Market/Library "station" pattern:
   - Seed `cemetery` structure (tier 1, buildable like house/wall).
   - `_maybe_build_cemetery`: elder deterministically starts one once any
     agent has died with nowhere to rest (mirrors
     `_maybe_start_approved_custom`'s escape hatch, including founding new
     village land if the district is full).
   - New `bury_agent` action (any agent, auto-targets/auto-walks like
     `heal_agent`) plus a `_maybe_handle_burials` backstop that buries the
     dead deterministically after a ~1 min organic grace window, so no
     corpse waits forever.
   - Strictly gated on `deathFrame is not None` — a temporary survival
     collapse is never eligible.
   - Tombstone sprite fully replaces the living body for a deceased agent
     (not an overlay); dedicated cemetery structure sprite (fenced grass
     plot, not the workshop-style fallback other stations reuse).
   - **Verified live end-to-end**: elder Aria started the Cemetery within
     seconds of restart, Finn built it, and all 5 then-existing dead agents
     were buried in the same tick with distinct, non-overlapping grave
     slots exactly matching the grid formula. Tombstone and cemetery
     sprites verified pixel-exact via a static preview server. As of the
     prior snapshot the world had 12 dead / 12 buried; it has since grown
     to 17/17.
7. **Cemetery layout + viewer UX follow-up (uncommitted at this snapshot)**:
   user reported tombstones clustered/scattered (not reading as a cemetery)
   and several viewer issues. Changes span `sim_engine.py`, `sprites.js`,
   `index.html`:
   - **`cemetery_grounds` starter district** west of the village (below
     beach): muted tile, fenced bounds, road from `beach_gate`, labeled
     **CEMETERY** on the map. Chapel uses `build_grid` (cap 1); graves use
     `grave_grid` with the same 100×95 spacing as village structures (no
     12-slot wraparound that stacked tombstones on duplicate coordinates).
   - **`PROJECT_KIND["cemetery"] = "cemetery"`** so the chapel builds in
     that district, not a random founded village plot.
   - **`restore_state` migration**: injects `cemetery_grounds` into old
     saves, moves the chapel onto its plot, re-seats all buried agents on
     the grave grid sorted by `deathFrame`.
   - **Tombstones only when `buried`** — unburied permanent dead render as
     a greyed body at the death site until `bury_agent`/backstop runs.
   - **Agents sidebar**: living list only; **Deceased (N)** button opens a
     modal; labels distinguish `dead` vs `collapsed` (recoverable).
   - **Page-load stall fix**: terrain cache built via `requestIdleCallback`
     with a loading overlay instead of blocking the main thread.
   - **Resource dots**: inventory dots hidden by default; shown only when
     hovering an agent on the canvas or selecting them in the Agents list.
   - **Requires server restart** to run migration on the live `state.json`.
8. **Manual cycle skills (`879982f`)**: Cursor skills at
   `.cursor/skills/civilization-cycle-morning/` and `...-night/` mirror the
   Claude Code scheduled tasks for manual Part 8 runs (`disable-model-invocation:
   true`).

## 5. Known gotchas (accumulated project-wide)

- **`drawResourceDots` in `index.html`** shows each carried resource as a
  colored pixel (up to 5 per type). Dots are **hidden unless** the agent is
  hovered on the canvas or selected in the Agents sidebar — do not revert
  to always-on without an explicit request.
- **`index.html` used to contain a full legacy client-side simulation** —
  removed long ago; if you ever see code there mutating world state instead
  of just rendering `/state`, that's a regression, not a feature.
- **`archive/state.json`** (416-structure legacy world) — never restore
  over the live save without explicit instruction.
- **Never run a second `simulation/server.py` instance.** `STATE_PATH` in
  `sim_engine.py` is hardcoded relative to the file's own directory, so ANY
  second instance (even on a different port) reads/writes the SAME
  `state.json` as the live 24/7 server — two ticking engines racing on one
  file. This was nearly done by accident this session while trying to
  verify a GUI change (`.claude/launch.json`'s `simulation-server-verify`
  config) — caught within seconds via the log output ("resumed from
  state.json @ frameTick=...") and stopped before real damage. **For any
  future pure-rendering (sprites.js/index.html) verification, use the
  `gui-static-preview` launch config instead** (plain `python -m
  http.server`, no engine, no state.json access at all) and inject
  synthetic data via `preview_eval` + `getImageData` pixel checks.
- **When manually patching `state.json`,** always confirm the server
  process is fully stopped first (check `Get-NetTCPConnection -LocalPort
  5001`) — an autosave from a still-running process will silently clobber
  a manual edit. This happened once this session on the first attempt at
  the aging-data patch.
- **Never load two LLM models at once** on the 12GB card — confirmed
  empirically to starve both.
- **`git status` shows two untracked jpg files** in `simulation/sprite_examples/`
  — pre-existing, unrelated, left alone.

## 6. Verification commands (copy-paste ready)

```powershell
# Server health
(Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
Invoke-WebRequest http://127.0.0.1:5001/ -UseBasicParsing | Select StatusCode

# Confirm ONLY 5001 is listening among project ports before/after any restart
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in @(5001,5062,8899) } | Select-Object LocalPort, OwningProcess

# Restart the server (canonical method -- NEVER a background Bash task)
taskkill /F /FI "WINDOWTITLE eq SimServer*" 2>$null
Start-Process cmd -ArgumentList '/k', 'title SimServer && cd /d C:\Users\dbadmin\Desktop\GitServ\simulation && uv run python simulation/server.py'
```

```bash
# Git state
cd "C:/Users/dbadmin/Desktop/GitServ/simulation" && git log --oneline -10 && git status --short

# Current world snapshot (flags, era, frame)
curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); print(d['frameTick'], d['civilization']['era'], d['config']['flags'])"

# Deceased / buried agent counts (Cemetery health check)
curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); a=d['agents']; print('deceased', sum(x.get('deceased') for x in a), 'buried', sum(x.get('buried') for x in a))"

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
