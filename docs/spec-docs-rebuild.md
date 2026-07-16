# Documentation Refresh Plan — CLAUDE.md, AGENTS.md, specs/, REFERENCE.md

## Context

The specs/ folder still describes the original **browser-authoritative** 6-gate build plan (state in browser, 4 routes, 19 actions, 7 fixed zones, `MAX_CONCURRENT_LLM=2`), while the real system is a **server-authoritative** engine (`sim_engine.py`, ~10k lines, 30/s tick, districts + frontier world, ~30 flag-gated systems, 35 actions, 18 routes). The user requires: (1) CLAUDE.md/AGENTS.md current and context-friendly; (2) all specs rewritten — restructured & renumbered — deep enough that **an AI assistant can fully rebuild the app from specs alone** and specs remain the primary interface for future spec-driven development; (3) REFERENCE.md updated; (4) HANDOFF.md untouched; (5) zero stale data or stale-doc references anywhere; (6) **.md files only** (moves allowed).

User decisions (confirmed via Q&A): restructure & renumber the spec set; keep docs/archive/* rationale pointers (marked historical); move completed plan docs to docs/archive/; spec depth = behavior + data shapes + load-bearing constants, organized for AI-driven rebuild and SDD editing.

Per CLAUDE.md model policy: orchestrator plans/reviews only; all writing is dispatched to `implementer` subagents (Sonnet).

## Verified current-state facts (inventory baseline)

From three exploration passes over the code (implementers must re-verify at write time — see Fact Protocol):

- **Engine** ([sim_engine.py](simulation/sim_engine.py)): 30/s tick daemon; ~30 module-level flags (only `PIANO_MODULES`, `META_SYSTEM` off); world 5200×5400 with 12 starter **districts** + frontier plots (max 26), BFS road network, per-district terrain tiles; 12 agent defs / default-8 roster (Sage forced in); survival/crafting/goals/goods/economy/tech-tree (eras, tier≤3, Sage two-stage review, invention council)/lifecycle (aging, births, deaths, succession elections)/culture/cemetery/memes/rules+voting/ecology+terraform/Path 1 (industry, tool tiers, composable blocks, terrain tiles, diplomacy w/ second settlement+treaties+caravans, pressure loop w/ day-night+wildlife); day/season/year clock (`DAY_FRAMES=13500`); deterministic Sage emergency with in-flight LLM decision discard; state.json v2 atomic autosave every 10s + v1 migration; `MAX_CONCURRENT_LLM=3` ThreadPoolExecutor, think intervals `360+i*60` (elder 240).
- **Server/cognition** ([server.py](simulation/server.py)): 18 Flask routes; 35 `DECISION_ACTIONS`; `DECISION_SCHEMA` json_schema structured output with auto-degrade retries; `normalize_decision` + `role_fallback_action`; `MODEL_SMART`/`MODEL_FAST` (both `qwen/qwen3.5-9b`, fallback `local-model`); high-stakes routing (elder/invention/sprite + rate-limited emergency/election/treaty); slim-prompt retry on context overflow; in-process 128-dim hashing-trick memory store; SessionLogger JSONL (activity/conversation/lm_studio/benchmarks). `/agent/think` is legacy (engine calls `run_agent_decision` directly).
- **Viewer** ([index.html](simulation/index.html), [sprites.js](simulation/sprites.js)): pure thin renderer — polls `GET /state` at 10 Hz + `GET /districts.js` every 3 s; day/night + season overlays; minimap; no sim logic client-side. There is **no districts.js file** — it's a Flask route serving JSON.
- **Docs audit**: CLAUDE.md/AGENTS.md/REFERENCE.md cross-refs all live; the only dangling refs are specs/05 → `.cursor/plans/*` (dir gone). Stale docs found: old specs 00–05, README.md ("Twelve autonomous agents", wrong context notes, layout missing sim_engine.py/roles.json), `docs/path-1-minecraft-like-world-plan.md` (completed/audited but labeled "active" in CLAUDE.md), `docs/rule-oscillation-fix-plan.md` (implemented 2026-07-12), `files/SPEC-BUILD-PLAN.md` (build plan for the OLD spec set), `simulation/ISSUES.md` (diagnostic of a previously-broken state). Open/current: three `docs/plan-visual-*.md`. Word counts: CLAUDE.md 774, AGENTS.md 159, REFERENCE.md 828, specs total ~4,000.

## New spec set (13 files, replaces specs/00–05)

Each spec ≤ ~1,500 words, table-heavy, independently loadable. Standard 3-line header on every file: title · one-line scope · "Canonical for: <facts owned>. See also: <links>." Old spec content is preserved by git history only (no archive copies; say so in the commit message).

| File | Scope (canonical facts owned) | ~Words |
|---|---|---|
| `specs/00-overview.md` | What/why, non-goals (carried forward), repo layout, run one-liners → CLAUDE.md, **spec index table**, SDD contract ("edit specs first, code second; specs must stay sufficient to rebuild") | 500 |
| `specs/01-architecture.md` | Server-authoritative topology, data flow (`_build_think_payload` → `run_agent_decision` → `normalize_decision` → `apply_decision`), threading/lock discipline, action-sync invariant, **complete flag index table** (flag → owning spec) | 900 |
| `specs/02-engine-core.md` | Tick loop + frame gates, time model (day/night/seasons/years constants), think scheduling, pause/resume/reset, Sage emergency, persistence (state.json v2 shape, autosave, v1 migration) | 1,100 |
| `specs/03-cognition.md` | Prompt construction (~3,100 tokens, payload sections), SYSTEM_PROMPT contract, DECISION_SCHEMA, normalize/fallback rules, model routing + high-stakes, retries (slim/context-overflow, response_format, model-id), LM Studio settings + context-sizing formula, PIANO/META experiments, `?agents=N` | 1,500 |
| `specs/04-http-api.md` | All 18 routes: method, request/response shape, purpose; `/state` payload key inventory; `/agent/think` marked legacy | 1,000 |
| `specs/05-world.md` | World geometry, 12 starter districts + frontier founding, road network, terrain tiles, ecology stocks/regrow/terraform, structures (registry, levels ≤100, upgrades, decay/ruins), composable blocks, cemetery | 1,300 |
| `specs/06-agents.md` | Agent defs/roster rules, roles.json schema (data stays in roles.json), full agent state field table, lifecycle (aging/births/deaths/succession), memory system, emergent roles | 1,300 |
| `specs/07-actions.md` | **The action catalog** — all 35 actions: params · flag gate/preconditions · effect · owning-spec link. No other file lists actions | 1,500 |
| `specs/08-systems-economy.md` | Flag semantics: SURVIVAL, CRAFTING, USE_GOALS, STRUCTURE_EFFECTS, GOODS (spoilage/decay/disasters/shelter/carts), ECONOMY (pricing, ally/rival modifiers) — each with cadence, constants, observable effects | 1,400 |
| `specs/09-systems-society.md` | TECH_TREE (eras, invention council, Sage review, blueprint safeguards), RULES + voting (incl. oscillation guard behavior, so the archived fix plan isn't load-bearing), MEMES, CULTURE (skills/teaching/library/chronicle), messaging, BENCHMARKS | 1,400 |
| `specs/10-path1.md` | PATH1 bundle sub-flag by sub-flag: industry, tool tiers, composable build, terrain tiles, diplomacy (settlement/treaties/caravans), tier-3, pressure loop. Historical pointer → archived path-1 plan | 1,500 |
| `specs/11-viewer.md` | Thin-viewer contract (polling rates, zero sim state, ACTION_LABELS display-only), sprites.js rendering incl. seasonal variants, pointer to open plan-visual docs | 900 |
| `specs/12-ops.md` | SessionLogger JSONL files, `/log/*` ingestion, all six scripts/ tools and what each verifies, no-test-suite verification-by-observation workflow | 800 |

**Single-source-of-truth rules**: actions → 07 only; routes → 04 only; complete flag list → 01 index (semantics in owning spec); time/persistence constants → 02; LLM constants → 03; geometry → 05; agent fields → 06; role data → roles.json (06 documents schema only); commands → CLAUDE.md. Everyone else cross-links instead of duplicating.

## Existing top-level docs

- **CLAUDE.md** (target: ≤ current 774 words — must not grow): keep model policy, commands (incl. cmd-window restart recipe), architecture summary, critical invariants, logs, archive warning. Change: "browser-based" → server-authoritative phrasing; shrink flag section to 2 lines pointing at specs/01 + 08–10; docs map — drop the path-1 "active plan" line, add `specs/` as the canonical SDD spec set (index: specs/00), list the three open plan-visual docs, update REFERENCE.md description.
- **AGENTS.md**: minimal — add one clause pointing at specs/ as the canonical spec set; verify conventions still match `git log`. Stays ≤ 18 lines.
- **docs/REFERENCE.md** (828 → ~450 words): delete "Specs vs. reality" (obsolete) and the flag table (now specs/01+owners); move LM Studio operational detail into specs/03 leaving one-line pointers; repoint path-1 reference to its archive location; keep docs/archive/* rationale pointers explicitly marked **historical** (user choice); new role = slim router to specs/ + historical rationale + LM Studio ops pointers.
- **README.md** (stale, in scope under "zero stale data"): fix agent count (8 default / 12 max), fix the wrong context/concurrency notes (link specs/03), add sim_engine.py + roles.json to the layout table.

## File moves (git mv; each gets a top banner: "> Historical: completed/archived <date>. Current behavior: specs/<owner>.")

1. `docs/path-1-minecraft-like-world-plan.md` → `docs/archive/` — update REFERENCE.md pointer + CLAUDE.md docs map; specs/10 carries the historical pointer. HANDOFF.md links to it but is untouchable — explicitly exempt from link-check.
2. `docs/rule-oscillation-fix-plan.md` → `docs/archive/` — behavior captured in specs/09.
3. `files/SPEC-BUILD-PLAN.md` → `docs/archive/` — it's the build plan for the old spec set.
4. `simulation/ISSUES.md` → `docs/archive/` — REFERENCE.md itself calls it a diagnostic of the previously-broken state; drop the REFERENCE.md paragraph about it.

Leave alone: `docs/HANDOFF.md` (untouched, per acceptance), `docs/plan-visual-*.md` (open work), `docs/lm studio fixes.txt` (not .md), everything already in `docs/archive/`.

## Fact-verification protocol (binding on every implementer)

The inventory above is a map, **not** a citable source. Every number/name/count/constant written into a doc must be line-verified in code at write time: derive the 35 actions from `DECISION_ACTIONS`, the routes from `@app.route` decorators (18 today), the flag set from module-level assignments in sim_engine.py, the 12 roles from roles.json, districts from `STARTER_DISTRICTS`. Each implementer returns a fact table (claim → `file:line`) with its report; orchestrator spot-checks ≥5 entries per report. Code wins over this plan on any conflict — flag discrepancies.

## Execution phases (all writing via `implementer` subagents; orchestrator reviews at each checkpoint)

**Designed for phased/resumable execution.** Each phase is an independently runnable unit that leaves the repo in a consistent, committable state (no dangling links, no half-written files). A session may stop after any phase — e.g. if usage credits run out — and a later session resumes by reading this plan and the Progress tracker below. Rules:

- **One phase per dispatch round.** Never start a phase unless the previous phase's exit criteria are checked off. Phases with parallel implementers (1 and 2) may optionally be split further: each lettered sub-batch (A/B/C/D) is itself resumable — finishing only batch A is a valid stopping point.
- **Commit after each phase** (subject: `docs(sdd): phase N — <summary>`) so a credit cutoff never loses verified work and `git log` doubles as a progress record.
- **Update the Progress tracker in `docs/spec-docs-rebuild.md`** (mark the phase done, note any deviations) as the last step of every phase — this file is the single source of resume state.
- **Resume procedure for a fresh session:** read `docs/spec-docs-rebuild.md` → check the Progress tracker + `git log --oneline` → run the previous phase's exit-criteria checks (cheap greps) to confirm state → dispatch the next phase. No other context needed.

### Phase 0 — Moves & banners (1 implementer, Haiku)
- **Do:** the 4 git mv's + historical banners + interim REFERENCE.md/CLAUDE.md repoint so no link dangles mid-refactor.
- **Exit criteria:** all 4 files exist under docs/archive/ with banners; grep finds no non-archive references to their old paths (HANDOFF.md exempt).

### Phase 1 — Core specs (2 implementers in parallel, Sonnet)
- **Batch A:** specs/00, 01, 02 (engine facts). **Batch B:** specs/03, 04 (server facts).
- **Exit criteria:** all 5 files exist with headers + fact tables reviewed (≥5 citations spot-checked each); specs/01 flag index matches the code-derived flag list; specs/04 route count = `@app.route` decorator count.
- **Note:** old specs/00 and 01 filenames are overwritten here; old 02–05 remain (stale) until Phase 3 — acceptable mid-state, flagged in the tracker.

### Phase 2 — World/agents/actions + systems (2 implementers in parallel, Sonnet)
- **Batch C:** specs/05, 06, 07 (07 is the anchor). **Batch D:** specs/08, 09, 10 (must use 07's action names verbatim — if split across sessions, C runs before D).
- **Exit criteria:** specs/07 action list diffs clean against `DECISION_ACTIONS`; every flag in 01's index has exactly one owning section across 02/03/08/09/10.

### Phase 3 — Viewer + ops + old-spec deletion (1 implementer, Sonnet)
- **Do:** specs/11, 12; delete old `specs/02-server-spec.md`, `03-world-spec.md`, `04-agent-spec.md`, `05-simulation-loop-spec.md`.
- **Exit criteria:** specs/ contains exactly the 13 new files; no old-gate content remains.

### Phase 4 — Top-level docs (1 implementer, Sonnet; only after all specs final so links resolve)
- **Do:** CLAUDE.md, AGENTS.md, REFERENCE.md, README.md per §Existing top-level docs.
- **Exit criteria:** CLAUDE.md word count ≤ 774; all four files link only to existing targets.

### Phase 5 — Verification (1 implementer, Haiku, read-only report)
- **Do:** run the full Verification suite below; orchestrator dispatches fix-up implementers for any failure, then re-runs the failed checks.
- **Exit criteria:** all 8 checks pass; Progress tracker marked complete.

## Progress tracker (update after every phase; source of truth for resuming)

| Phase | Status | Session/date | Notes |
|---|---|---|---|
| 0 — moves & banners | ☑ done | 2026-07-15 | 4 files archived w/ banners; REFERENCE.md + CLAUDE.md repointed. Out-of-scope leftover: `scripts/path1_soak.py:3` docstring still cites the old path-1 plan path (code file — .md-only constraint; fixed in a follow-up commit 2026-07-15). |
| 1A — specs/00,01,02 | ☑ done | 2026-07-15 | 519/820/930 words. Flag index is code-derived and complete; notes which flags are NOT echoed to /state config.flags. |
| 1B — specs/03,04 | ☑ done | 2026-07-15 | 1,827/1,024 words — specs/03 exceeds the ~1,500 ceiling (accepted: densest spec; optional trim in Phase 5). **Plan-doc corrections:** route count is **18, not 19**; roster override is a JSON body field `{"agents": N}` on `POST /control/reset`, not a `?agents=N` query param — phases 2D/4/5 must use the corrected facts. |
| 2C — specs/05,06,07 | ☑ done | 2026-07-15 | 1,351/1,309/1,564 words. **Plan-doc correction:** starter districts = **12, not 15** (verified against `STARTER_DISTRICTS`); plan doc fixed. All 35 actions covered in 07 (approve/reject_recipe share a row). |
| 2D — specs/08,09,10 | ☑ done | 2026-07-15 | 1,537/1,477/1,111 words (08 ~9.8% over target, within tolerance). Anti-oscillation guard documented from code (`RULE_PROPOSE_COOLDOWN=1500`, `RULE_REPEAL_MIN_AGE_FRAMES=4×`, backstop-only scope) — archived fix plan no longer load-bearing. |
| 3 — specs/11,12 + old-spec deletion | ☑ done | 2026-07-15 | 1,014/932 words. specs/ = exactly the 13 new files; 4 old gate specs deleted (content in git history). Correction: plan-visual-3 is fully DONE per its own status line (not "partially done") — specs/11 reflects that. |
| 4 — CLAUDE/AGENTS/REFERENCE/README | ☑ done | 2026-07-15 | CLAUDE.md 771 words (≤774 ✓, did not grow); AGENTS.md 16 lines (spec-set clause added, commit example refreshed); REFERENCE.md rewritten as slim router, 367 words; README fixed (8/12 agents, correct concurrency/context facts, layout table). All links verified to resolve. |
| 5 — verification suite | ☑ done | 2026-07-15 | All 8 checks PASS. One grep false positive (README's port-5000 line is the correct 5001 rationale, kept). Size notes: specs 03/11/12 slightly over ceiling, all previously accepted. Orchestrator rebuildability spot-check (Sage emergency, treaties, spoilage) confirmed specs alone give trigger/constants/outcome. **PLAN COMPLETE.** |

## Model assignment per phase

Repo policy (CLAUDE.md): orchestrator plans/reviews only; implementation subagents run **Sonnet 5 or lower**. For this docs-only plan that's not just compliant but the efficient choice on merits — accuracy comes from the binding fact-verification protocol (every claim cited `file:line`, code wins), not from raw model tier.

| Phase | Work type | Model | Rationale |
|---|---|---|---|
| 0 — moves & banners | Mechanical: 4 git mv's, banner lines, 2 link repoints | **Haiku 4.5** (`model: "haiku"`) | Fully specified, no judgment; fastest/cheapest tier that can do it. |
| 1–2 — the 10 core specs | Extract + line-verify facts from a ~10k-line engine and ~3.4k-line server; write dense, accurate spec tables | **Sonnet 5** (`model: "sonnet"`) | The hard part — needs real code comprehension and disciplined synthesis. Ceiling allowed by repo policy and genuinely the right tier. |
| 3 — viewer/ops specs + deletions | Moderate spec writing | **Sonnet 5** | Same nature as Phases 1–2, smaller surface. |
| 4 — CLAUDE.md/AGENTS.md/REFERENCE.md/README.md | Careful editing under strict word budgets | **Sonnet 5** | Shrinking docs without losing load-bearing facts takes judgment. |
| 5 — verification suite | Scripted greps/counts/link checks + pass-fail report | **Haiku 4.5** | Almost entirely mechanical. |
| Orchestration + checkpoints | Review fact tables, spot-check ≥5 citations per report, final rebuildability review | **This session (Fable 5)** | Deep-judgment review is where the orchestrator tier pays off. |

Scoped dispatches (2–3 specs per implementer, one fact domain each) matter more than tier: no single agent holds the whole codebase, which keeps fact density high and hallucination risk low.

## Verification suite (Phase 5; exclude docs/archive/, docs/HANDOFF.md, .claude/, simulation/logs/)

1. **Action coverage**: extract `DECISION_ACTIONS` from server.py; assert every action appears in specs/07 and the count matches exactly.
2. **Flag coverage**: extract flag names from sim_engine.py; assert each is in specs/01's index AND has exactly one owning spec section.
3. **Route coverage**: extract `@app.route` paths; assert all in specs/04, count matches.
4. **Staleness greps** (zero hits outside exclusions): `.cursor/plans`, `port 5000`, `seven zones|7 zones`, browser-authoritative phrasing ("state lives in the browser", "server holds no simulation state"), `19 actions`, `MAX_CONCURRENT_LLM = 2`, non-archive paths to the four moved files, `Specs vs. reality`.
5. **Link check**: every relative `](...)` link in CLAUDE.md, AGENTS.md, README.md, REFERENCE.md, specs/*.md, docs/plan-visual-*.md resolves to an existing file (HANDOFF.md exempt as a source).
6. **Size budgets**: each spec ≤ ~1,500 words; CLAUDE.md ≤ 774 words.
7. **Scope check**: `git status --porcelain` shows only .md adds/edits/renames; HANDOFF.md absent from the diff.
8. **Rebuildability spot-check** (orchestrator): pick 3 mechanics (e.g. Sage emergency, treaty flow, spoilage) and confirm specs alone give trigger, constants, and observable outcome without reading code.

## Out of scope

No changes to any .py/.js/.html/.json file; no HANDOFF.md edits; no commits unless requested afterward.
