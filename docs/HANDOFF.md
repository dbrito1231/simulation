# HANDOFF — Daily Council integration (2026-07-26)

> This top section is the current resume snapshot. The prior Path 1 handoff is
> retained below as historical context.

## 0. Current machine-readable snapshot

```json
{
  "snapshot_generated_utc": "2026-07-26T06:34:06Z",
  "conversation_scope": "Daily Council Assembly Phases 0-5 implementation, integration regression, and retention soak",
  "repo_root": "C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation",
  "git": {
    "branch": "main",
    "baseline_head": "5387d83",
    "baseline_subject": "Merge branch 'codex/module-quality' into main",
    "working_tree": "uncommitted Daily Council implementation; do not stage/commit as part of Phase 4"
  },
  "daily_council": {
    "status": "FULLY_IMPLEMENTED_THROUGH_PHASE_5",
    "default_enabled": true,
    "action_count": 42,
    "new_actions": [
      "council_speak",
      "council_propose",
      "council_vote"
    ],
    "cadence": "one meeting at each DAY_FRAMES boundary when eligible",
    "eligibility": "at least 2 living agents plus a living, non-incapacitated elder",
    "attendance": "all living non-incapacitated agents; collapsed living agents excused",
    "death_semantics": "dead agents remain in self.agents and are excluded by non-null deathFrame",
    "voting": "whole-village quorum normally; exact yes/no ties use the seated elder's personal yes/no vote (default no if absent/abstaining); non-tied sub-quorum results reject; elder ratifies",
    "records": "verbatim council_transcript rows plus bounded councilDigests and councilLog",
    "viewer": "responsive auto-opening assembly modal with 760x760 logical canvas, full seat ring, agenda, transcript, tally, and verdict",
    "flag_off": "no Daily Council convene/actions offered/actions accepted; legacy invention-council behavior retained"
  },
  "state_db_impact": {
    "state_version": 2,
    "migration": "additive CREATE TABLE IF NOT EXISTS; no version bump",
    "new_table": "council_transcript(rowid_pk, meeting_id, who, type, text, feeling, frame_tick, ts)",
    "write_shape": "authoritative in-RAM list, atomically delete/reinsert on save like memory",
    "retention": "newest 30 distinct meeting_id values",
    "civ_keys": [
      "dailyCouncil",
      "councilDigests"
    ]
  },
  "verification": {
    "command": "uv run python scripts/daily_council_regression.py",
    "result": "PASS (6/6 cells, 3.307 s total)",
    "matrix": {
      "flag_on": {
        "daily_council_smoke.py": "PASS (0.195 s)",
        "sid_parity_smoke.py": "PASS (0.594 s)",
        "path1_smoke.py": "PASS (0.875 s)"
      },
      "flag_off": {
        "daily_council_smoke.py": "PASS (0.123 s)",
        "sid_parity_smoke.py": "PASS (0.599 s)",
        "path1_smoke.py": "PASS (0.921 s)"
      }
    },
    "artifact": "scripts/out/daily_council_regression.json",
    "isolation": "fresh subprocess and temporary state.db per cell; source files and real simulation/state.db untouched",
    "py_compile": "PASS: sim_engine.py, server.py, prompts.py, daily council smoke/regression/state probe/soak",
    "soak": {
      "command": "uv run python scripts/daily_council_soak.py",
      "result": "PASS (35/35 meetings, 0 anomalies, 0.613 s)",
      "artifact": "scripts/out/daily_council_soak_20260726T063406Z.json",
      "retention": "30 distinct meeting IDs retained exactly (6-35); 990 rows expected/actual; rows plateaued from meetings 30-35",
      "sqlite_growth": "114688 bytes after meeting 1; 294912 bytes at meeting 30; unchanged at 294912 through meeting 35",
      "caps_and_safety": "digest/log caps, phase order, attendee/head integrity, TTL/wedge checks, no stuck session, guarded source hashes, and real state.db fingerprint all passed",
      "ollama": "disabled for the deterministic required soak; --ollama is available with deterministic action fallback"
    }
  },
  "runtime_assumptions": {
    "decision_model": "sim-smart (Qwen3.5 9B); every decision and council turn",
    "background_model": "sim-fast (llama3.2:3b); PIANO/memory/meta background cognition only",
    "smart_num_ctx": 20480,
    "fast_num_ctx": 4096,
    "decision_concurrency": 3,
    "piano_concurrency": 2,
    "ollama_parallel": 3,
    "regression_requires_ollama": false,
    "implementation_assignments": "historical GPT-5.6 sol/terra assignments remain recorded in docs/plan-daily-council.md per explicit user direction"
  },
  "remaining": []
}
```

## 1. Daily Council behavior

`DAILY_COUNCIL_ENABLED = True` schedules a whole-village assembly at the
in-world day boundary. Convening requires at least two living villagers and a
living, non-incapacitated elder. Every other living, non-incapacitated villager
is seated; collapsed villagers are excused. Dead villagers are retained in the
engine roster for lifecycle/history rendering but excluded by `deathFrame`.

The engine advances convening → discussion → proposal → voting → verdict →
adjourned with phase/session TTLs. Three council-only actions replace ordinary
think turns. A ballot normally needs a strict majority of the complete
non-excused living attendance. An exact yes/no tie is the defined exception:
the seated elder's personal yes/no vote breaks it (default no if absent or
abstaining), and a rule tie uses a synthetic ratification vote through the
existing enactment path. Non-tied sub-quorum results reject. The elder announces
and ratifies the result. When the flag is off, no assembly convenes,
the actions are neither offered nor accepted, and the legacy reactive invention
council remains active.

The viewer is read-only. Its modal auto-opens for a live session, can be
dismissed/re-opened, and draws the engine-authored seating ring on a 760×760
logical canvas that scales down to fit short/narrow viewports.

## 2. Persistence and verification

The existing `STATE_VERSION = 2` remains valid. SQLite DDL additively creates
`council_transcript`; old saves restore with `dailyCouncil = None` and
`councilDigests = []`. Raw transcript rows are retained for 30 meeting ids and
never enter prompts. Compact digests are capped at five and at most two enter a
think payload.

Resume checks:

```powershell
uv run python scripts/daily_council_regression.py
uv run python scripts/daily_council_soak.py
uv run python -m py_compile simulation/sim_engine.py simulation/server.py simulation/prompts.py scripts/daily_council_smoke.py scripts/daily_council_regression.py scripts/daily_council_state_probe.py scripts/daily_council_soak.py
```

Phase 4 regression passed all six on/off cells in 3.307 seconds. Exact per-cell
results and captured output tails are in
`scripts/out/daily_council_regression.json`.

Phase 5's deterministic no-Ollama soak passed 35/35 meetings in 0.613 seconds
with zero anomalies. The transcript table retained exactly meeting IDs 6–35
(990 rows), and both row count and the 294,912-byte SQLite file size plateaued
from meeting 30 through meeting 35. Full per-day lifecycle, tally/ruling, cap,
TTL/wedge, integrity, isolation, and growth evidence is in
`scripts/out/daily_council_soak_20260726T063406Z.json`.

---

## Historical handoff — Path 1 sprint session (2026-07-11)

> Read this file FIRST when resuming work from this session. It documents
> **only** the Cursor conversation that implemented
> `docs/path-1-minecraft-like-world-plan.md` — verify load-bearing facts
> against the live repo and `/state` before acting.
>
> **Runtime note (2026-07-24):** everything below reflects the LM Studio
> runtime in use at the time. LM Studio has since been permanently retired
> and replaced by Ollama (`docs/plan-ollama-migration.md`); do not treat any
> LM Studio reference below as describing the current runtime — see
> `ollama_config.md` for the live configuration.

---

## 0. Machine-readable snapshot

```json
{
  "snapshot_generated_utc": "2026-07-11T13:08:00Z",
  "conversation_scope": "Path 1 Minecraft-like world depth sprint — implement plan, server restart/kill, status Q&A, commit/push",
  "repo_root": "C:\\Users\\dbadmin\\Desktop\\GitServ\\simulation",
  "git": {
    "branch": "feat/sid-parity-deepening",
    "head_commit": "bffc91a",
    "head_subject": "path1: 2D world depth sprint (industry, tools, tiles, terrain, diplomacy, pressure)",
    "pushed_to": "origin/feat/sid-parity-deepening",
    "commit_included_files": [
      "simulation/sim_engine.py",
      "simulation/server.py",
      "simulation/sprites.js",
      "simulation/index.html",
      "scripts/path1_smoke.py",
      "docs/path-1-minecraft-like-world-plan.md",
      ".cursor/path-1-integration-contract.json",
      "CLAUDE.md"
    ],
    "left_untracked_at_session_end": [
      "state_snapshot.json",
      "simulation/sprite_examples/cemetery_capture.png",
      "simulation/sprite_examples/cemetery_structure.png"
    ]
  },
  "path1_status": {
    "PATH1_ENABLED": true,
    "sprint_verdict": "SOFT-PASS",
    "smoke_script": "scripts/path1_smoke.py",
    "soak_script": "scripts/path1_soak.py",
    "smoke_result": "PASS (headless, no LM Studio)",
    "soak_result": "SOFT-PASS — session 2026-07-11T01-08-45 (~9h live); check 10 FAIL (prompt max 5732 > 3500)",
    "smoke_checks_passed": [
      "PATH1 flags in /state config",
      "copper_ingot craft with kiln+workshop",
      "iron_ore blocked without iron_pick, succeeds with pick",
      "place_block populates district.tiles",
      "dig_terrain",
      "two settlements (home + outpost)",
      "py_compile sim_engine.py + server.py"
    ],
    "audit_items_not_verified": [
      "Prompt <=3500 tokens (FAIL: max 5732 with all phase flags on — see check 10)"
    ],
    "not_created": [
      ".cursor/path-1-subagents/ (SA-0 prompt files per plan)"
    ],
    "minor_gaps": [
      "Tool-tier gather rejection lives in sim_engine._perform_gather only — server.normalize_decision not extended (engine is authoritative)",
      "Tool durability intentionally omitted per plan v1"
    ]
  },
  "feature_flags_path1": {
    "PATH1_ENABLED": true,
    "INDUSTRY_ENABLED": true,
    "TOOL_TIERS_ENABLED": true,
    "COMPOSABLE_BUILD_ENABLED": true,
    "TERRAIN_TILES_ENABLED": true,
    "DIPLOMACY_ENABLED": true,
    "TIER3_CONTENT_ENABLED": true,
    "PRESSURE_LOOP_ENABLED": true,
    "note": "When PATH1_ENABLED is true, path1_on() bundles all sub-flags on. Disable master flag in sim_engine.py to turn off entire Path 1 stack."
  },
  "server": {
    "url": "http://127.0.0.1:5001",
    "launch_command": "uv run python simulation/server.py",
    "status_at_session_end": "stopped by user request — port 5001 was free after kill",
    "session_note": "Agent restarted server once for preview; user then asked to kill it and restart manually themselves."
  },
  "session_focus_this_conversation": [
    "1. Implemented full Path 1 plan (SA-1..SA-8 scope in one integration): industry/smelt chain, tool gates, compositional 2D tiles, terrain mutation, second settlement + caravans + treaties, night pressure + wildlife, viewer layers + Settlements panel.",
    "2. Created scripts/path1_smoke.py, .cursor/path-1-integration-contract.json, updated docs/path-1-minecraft-like-world-plan.md audit log.",
    "3. Brief live verification: /state showed PATH1 flags, new resources (clay, ores, ingots, picks), settlements Home Village + Frontier Outpost.",
    "4. User Q&A: implementation completeness (SOFT-PASS), SA-9 verifier role explained.",
    "5. Committed bffc91a and pushed to origin/feat/sid-parity-deepening."
  ],
  "open_items_from_this_session": [
    "1. Prompt budget (check 10): LM Studio reports max ~5732 prompt tokens with all flags on — exceeds 3500 bar; consider SYSTEM_PROMPT trim or raise audit threshold.",
    "2. Optionally add server.normalize_decision gather tool-gate (defense in depth; engine already enforces).",
    "3. Optionally create .cursor/path-1-subagents/ SA-0 prompt files (process artifact only — code is landed).",
    "4. Fresh 2h mini-soak on reset world: uv run python scripts/path1_soak.py run --reset --duration 7200"
  ]
}
```

---

## 1. What was done this session

The user asked to **build** `docs/path-1-minecraft-like-world-plan.md` — the
Path 1 “Minecraft-*like*” 2D world depth sprint (industry, tools, compositional
tiles, terrain, diplomacy, pressure). That was implemented as a single integration
(not separate subagent branches).

### Shipped mechanics (`PATH1_ENABLED = True` in `sim_engine.py`)

| Area | Summary |
|------|---------|
| **Industry** | New resources (clay, sand, ores, ingots, rope, cloth, picks); kiln + harbor/mill/foundry seeds; smelt/craft recipes |
| **Tool tiers** | `wooden_pick` / `stone_pick` / `iron_pick` gate stone / copper_ore / iron_ore gathers |
| **Composable build** | `place_block` / `remove_block` on district `tiles` (50% refund on remove) |
| **Terrain** | `dig_terrain` / `plant_terrain` on district `terrain`; grove ratio affects ecology |
| **Diplomacy** | Second settlement at thresholds, caravan goals, `propose_treaty` / `vote_treaty` |
| **Pressure** | Night exposure damage, forest wildlife events, `_maybe_seek_shelter` backstop |
| **Viewer** | `drawDistrictTiles()` / `drawDistrictTerrain()` in `sprites.js`; Settlements sidebar in `index.html` |
| **Server** | Six new actions in `DECISION_ACTIONS`; prompt rules P1–P3; `/districts.js` serves tiles/terrain |

### Verification

- `uv run python scripts/path1_smoke.py` → **PASS**
- `uv run python -m py_compile simulation/sim_engine.py simulation/server.py` → clean
- Plan audit log updated to **SOFT-PASS** (smoke green; live soak pending)

### Git

```
bffc91a path1: 2D world depth sprint (industry, tools, tiles, terrain, diplomacy, pressure)
```

Pushed to `origin/feat/sid-parity-deepening`.

---

## 2. Server ops this session

1. **Restart** — agent stopped prior process on port 5001 and started
   `uv run python simulation/server.py`. Live `/state` confirmed Path 1
   (settlements, new resource registry).
2. **Kill** — user asked to stop it; agent killed listener on 5001. User
   intended to restart manually.

---

## 3. User Q&A from this session

**“Were all plan changes implemented?”**  
Functionally yes for gameplay + integration. Process gaps: no `.cursor/path-1-subagents/` files, no 2h SA-9 soak, audit items 6–10 open. Verdict: **SOFT-PASS**.

**“What’s SA-9?”**  
The **Verifier** subagent role: run `path1_smoke.py`, optional 2h soak, fill the plan’s audit table, declare PASS/SOFT-PASS/FAIL. QA only — no new features.

---

## 4. Key files to read next

| File | Purpose |
|------|---------|
| [path-1-minecraft-like-world-plan.md](path-1-minecraft-like-world-plan.md) | Full sprint spec + audit log |
| [.cursor/path-1-integration-contract.json](../.cursor/path-1-integration-contract.json) | Frozen ids/flags |
| [scripts/path1_smoke.py](../scripts/path1_smoke.py) | Headless regression harness |
| [scripts/path1_soak.py](../scripts/path1_soak.py) | SA-9 live soak + log audit (checks 6–10) |
| [CLAUDE.md](../CLAUDE.md) | `PATH1_ENABLED` bullet block |

---

## 5. Quick resume commands

```powershell
cd C:\Users\dbadmin\Desktop\GitServ\simulation
git log -1 --oneline
uv run python scripts/path1_smoke.py
uv run python scripts/path1_soak.py report          # smoke + prompt sample
uv run python scripts/path1_soak.py audit         # audit newest log session
uv run python scripts/path1_soak.py run --reset --duration 7200  # full SA-9 2h soak
uv run python simulation/server.py
# → http://127.0.0.1:5001
```

```powershell
# Confirm port free before manual state.db edits
Get-NetTCPConnection -LocalPort 5001 -ErrorAction SilentlyContinue
```

---

## 6. LM Studio thinking config update (2026-07-14, post-session)

Not part of the Path 1 sprint above, but affects live behavior: a full-session
audit found a `bad_response` epidemic (57% of high-stakes/thinking turns, 65%
of the elder's) — with thinking on, the model burned its whole `max_tokens`
budget on `reasoning_content` before ever emitting the decision JSON.

- **Phase 1**: fixed the epidemic by disabling thinking on high-stakes turns
  (`THINKING_ENABLED_HIGH_STAKES = False` in `simulation/server.py`).
- **Phase 2**: tried fixing the root cause instead — dropped LM Studio
  `parallel` 3→2 (`scripts/lms_load.py`, `MAX_CONCURRENT_LLM = 2` in
  `simulation/sim_engine.py`) for a bigger per-slot token budget, raised
  `HIGH_STAKES_MAX_TOKENS` to 1600, and re-enabled thinking to test it live.
- **Phase 3 verdict**: a live analysis of 48 diverse high-stakes samples
  showed thinking produced zero measurable reasoning benefit — the model
  emitted the same direct JSON, just via `reasoning_content` instead of
  `content`. Reverted to the Phase 1 fix as the final state:
  `THINKING_ENABLED_HIGH_STAKES = False` and `parallel = 3` /
  `MAX_CONCURRENT_LLM = 3` restored for max routine-turn throughput. This was
  under the former LM Studio runtime, since replaced by Ollama
  (`docs/plan-ollama-migration.md`); the full history is carried forward in
  `ollama_config.md`'s "Thinking-epidemic history" section (`lms_config.md`,
  the original record, was removed in the migration's Phase 5).

---

*This HANDOFF replaces prior session content and reflects only the 2026-07-11 Path 1 conversation (plus the 2026-07-14 LM Studio config note above).*
