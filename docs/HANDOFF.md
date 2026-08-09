# HANDOFF — Simulation repo resume

**Branch:** `main` @ `50202f9` (2026-08-06)  
**Maintained:** manually (Phase C, AI Deslop Campaign)

Start here when resuming work. Behavior truth is [`specs/`](../specs/00-overview.md); ops/commands are [`CLAUDE.md`](../CLAUDE.md); AI workflow is [`AGENTS.md`](../AGENTS.md).

---

## What this is

Server-authoritative AI village simulation: Python engine runs the world headless; a local LLM (Ollama) is the decision brain for 8–12 pixel-art agents; the browser is a thin viewer. Proof-of-concept of the LLM-as-brain loop (Project Sid–inspired). Non-goals: [`specs/00-overview.md`](../specs/00-overview.md).

---

## Recent landings (`git log -20` snapshot)

| Commit | Summary |
|--------|---------|
| `50202f9` | Claude Code + Cursor role skills for the AGENTS.md dev loop |
| `1ed9bff` | **Modularization:** `sim_engine.py` → `simulation/sim_engine/` package; `server.py` helpers → `simulation/_server/`; viewer/sprites/css split into multi-file packages (pure move, no behavior change) |
| `74ab8b7` | `AGENTS.md` as AI workflow source of truth + Cursor loop wiring |
| `e0359e5` | LLM fallback fixes: sprite constraints, agent id collisions, council race, Voice ack cap (#13) |
| `3b879b8` | Sovereign God mode, Divine Console, Matrix interventions (#12) |
| `7077d40` / `bcd7286` | Living-ecosystem layer: events, ecology, goods, weather, feedback |
| `6a78162` | Elder invalid-rule proposal loop fix |
| `ab23244` | Living-world viewer: structure decay, activity, social ties, chronicle |
| `9ae0676` | Agents panel overhaul (PR #2) |

Older shipped batches (agents panel, living world, visual atmosphere, wildlife sprites, ghost-resource cleanup, log retention, perf fixes) are indexed in [`docs/archive/plan-archive-triage-phase-b.md`](archive/plan-archive-triage-phase-b.md).

---

## Architecture (post-modularization)

| Path | Role |
|------|------|
| `simulation/sim_engine/` | Engine package — `SimEngine` in `core.py`; `constants.py` / `persistence.py` / `helpers.py` + **22** `mixin_*.py` topic files `exec()`'d into one namespace by `__init__.py` |
| `simulation/server.py` | Flask app, routes, `DECISION_ACTIONS` / `DECISION_SCHEMA` / `SYSTEM_PROMPT`, Ollama cognition entry |
| `simulation/_server/` | Non-route helpers imported by `server.py` (validation, logging, memory store, model routing) |
| `simulation/viewer/*.js` | **16** viewer modules (polling, render loop, sidebar, Divine Console) |
| `simulation/sprites/*.js` | **8** Canvas drawing modules |
| `simulation/css/*.css` | **6** stylesheet modules |
| `simulation/roles.json` | Sole source of truth for role definitions |
| `specs/` | Canonical behavior specs (`00`–`12`) |

Data flow: tick thread → think timer → `_build_think_payload()` under lock → `run_agent_decision()` → `apply_decision()` under lock → browser polls `/state`.

Full detail: [`CLAUDE.md`](../CLAUDE.md#architecture).

---

## AI development workflow

Every user-requested change follows the [`AGENTS.md`](../AGENTS.md) loop: plan → user approves → orchestrator (read-only) → implementer (Composer 2.5 in Cursor) → reviewer → SUCCESS or FAIL-reprompt. Specs must stay in sync with code (SDD). Manual entry: `/loop-in-devs`, `/orchestrator`, `/implementer`, `/reviewer`.

---

## Sovereign God mode (shipped on `main`)

Landed via PR #12 (`3b879b8`). Divine Console bottom bar, Matrix interventions, Voice binding, Sight/History tools, `godState` **v3** (`GOD_STATE_VERSION = 3` in `simulation/sim_engine/constants.py`).

**Optional feature flags (default off):**

- `SIM_GOD_DEJA_VU_REPLAY=1` → applyable Déjà Vu replay (`GOD_DEJA_VU_REPLAY`)
- `SIM_GOD_COMPILER=1` → free-prose compiler (`GOD_COMPILER_ENABLED`)

God kinds stay **off** agent action sync. Mutations only via `/control/god/{preview,apply,cancel,compile}`. Specs: [`specs/11-viewer.md`](../specs/11-viewer.md), [`specs/12-ops.md`](../specs/12-ops.md).

Historical implementation plans: [`docs/archive/plan-sovereign-god-mode-v2.md`](archive/plan-sovereign-god-mode-v2.md), [`docs/archive/plan-divine-matrix-interventions.md`](archive/plan-divine-matrix-interventions.md), [`docs/archive/plan-divine-console-improvements.md`](archive/plan-divine-console-improvements.md).

---

## Implementation plans

**No active `docs/plan-*.md` files** after Phase B/C archive triage (2026-08-06). Shipped and superseded plans live under [`docs/archive/`](archive/) with disposition evidence in [`docs/archive/plan-archive-triage-phase-b.md`](archive/plan-archive-triage-phase-b.md). Do not read or act on archive plans unless explicitly asked.

---

## In-flight: AI Deslop Campaign

Plan: [`.claude/plans/ai-deslop-campaign.md`](../.claude/plans/ai-deslop-campaign.md) (wording/structure cleanup — **no behavior changes**).

| Phase | Target | Status |
|-------|--------|--------|
| A | Consolidate 22 mixin module docstrings | *(orchestrator — not this session)* |
| B | Triage `docs/plan-*.md` → archive | Done (28 moved; triage doc written) |
| C | `docs/HANDOFF.md` currency | **This update** |
| D | Code-comment sweep (`simulation/`, `scripts/`) | Pending |
| E | `specs/` prose-tightening (one file at a time) | Pending |

Campaign runs with simserver **stopped**; restart once after all approved phases complete.

## In-flight: Emergence Breakthroughs

Plan: [`.claude/plans/emergence-breakthroughs.md`](../.claude/plans/emergence-breakthroughs.md) (branch `emergence-breakthroughs`).

| Phase | Target | Status |
|-------|--------|--------|
| A0 | Determinism proof (`scripts/determinism_proof.py`) | Done (branch `emergence-breakthroughs`) |

---

## Verify

```bash
uv sync
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
uv run python scripts/god_mode_smoke.py    # if God mode surface touched
```

Run server in a titled `simserver` cmd window on port **5001**; confirm only one `simulation/server.py` process. Browser: `http://127.0.0.1:5001`. Session logs: `simulation/logs/<timestamp>/` (esp. `llm.jsonl`, `divine.jsonl`).

Viewer JS syntax (post-split): `node --check simulation/viewer/setup.js` (or any loaded module under `simulation/viewer/`).

---

## Open questions / branch notes

- **`feature/god-mode`:** **abandoned.** Tip `bd954fc` (PR #9 divine-console improvements) is not an ancestor of `main`. `main` is source of truth — it already carries the shipped God/Divine/Matrix surface via PR #12 plus post-merge modularization (`1ed9bff`) and AGENTS.md wiring (`74ab8b7`, `50202f9`). Do not merge or reconcile `feature/god-mode`.
- **`changes.md`:** historical branch inventory for `feature/four-breakthroughs-ace` (shipped content lives on `main`); use this HANDOFF for current resume context.
- **Compiler A/B contention protocol** (`specs/12-ops.md`): explicitly deferred; do not claim green contention results.

---

## Docs map

| Doc | Purpose |
|-----|---------|
| [`AGENTS.md`](../AGENTS.md) | AI workflow SoT |
| [`CLAUDE.md`](../CLAUDE.md) | Commands, architecture, invariants |
| [`specs/00-overview.md`](../specs/00-overview.md) | Spec index + SDD contract |
| [`docs/REFERENCE.md`](REFERENCE.md) | Deep-mechanics router + Ollama ops tips |
| [`README.md`](../README.md) | Human setup/run |
| [`docs/archive/`](archive/) | Historical plans and notes — read only when asked |
