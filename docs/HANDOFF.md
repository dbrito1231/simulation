# HANDOFF — Simulation repo resume

**Branch:** `emergence-breakthroughs` @ `c6c72eb` (2026-08-09), forked from `main` @ `50202f9` (2026-08-06)  
**Maintained:** manually (In-flight: Emergence Breakthroughs — see below)

Start here when resuming work. Behavior truth is [`specs/`](../specs/00-overview.md); ops/commands are [`CLAUDE.md`](../CLAUDE.md); AI workflow is [`AGENTS.md`](../AGENTS.md).

---

## What this is

Server-authoritative AI village simulation: Python engine runs the world headless; a local LLM (Ollama) is the decision brain for 8–12 pixel-art agents; the browser is a thin viewer. Proof-of-concept of the LLM-as-brain loop (Project Sid–inspired). Non-goals: [`specs/00-overview.md`](../specs/00-overview.md).

---

## Recent landings (`git log -20` snapshot)

On branch `emergence-breakthroughs` (not yet merged to `main`) — full detail in
"In-flight: Emergence Breakthroughs" below:

| Commit | Summary |
|--------|---------|
| `c6c72eb` | Harden ToM soak process-tree kill and record F2 contention gate FAIL |
| `51e01be` | Harden ToM soak: assert flag-on and wait for port release |
| `86b3968` | Add ToM contention soak script with `SIM_THEORY_OF_MIND` env override |
| `80fdc93` | Include schism in chronicle milestone kinds for `/state` projection |
| `ef2b5cd` | Implement schism trigger, secession, and settlement-scoped succession |
| `2bc1f54` | Thread settlement-scoped rules and voting when `SCHISM_ENABLED` |
| `bdf4698` | Add `SCHISM_ENABLED` settlement-keyed governance storage and restore wrap |
| `c6aa85e` | Add D10 rules and beliefs scoping audit for Schism prep |
| `c0935c5` | Add Testament and finish contracts prompt with flag-gated addendum |
| `5969e1c` | Implement contract escrow offer, accept, and settlement |
| `27d3abc` | Add contract action schema and validation behind a default-off flag |
| `7b62574` | Add Theory of Mind peer models behind a default-off flag |
| `577c2f7` | Add fork_compare harness for one-variable headless diffs |
| `1b26c83` | Add `DETERMINISM_PINNING` for harness-identical twin runs |
| `8ef3693` | Add A0 headless determinism proof for emergence harness |

Landed on `main` before the branch point (`cbcd42a`):

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
| A1 | Pin RNG / executor / tick scheduling (`DETERMINISM_PINNING`) | Done (branch `emergence-breakthroughs`) |
| A2 | Fork compare harness (`scripts/fork_compare.py`) | Done (branch `emergence-breakthroughs`) |
| B0 F1 | Testament (`TESTAMENT_ENABLED`, `scripts/testament_smoke.py`) | Implemented (default **off**; requires `WIKI_MEMORY` for meaningful carryover) |
| B1 F2 | Theory of Mind (`THEORY_OF_MIND_ENABLED`, `scripts/peer_model_smoke.py`) | Implemented (default **off**; default-on soak gate **FAIL** — keep off) |
| B2 F3 | Contracts and escrow (`CONTRACTS_ENABLED`, `offer_contract`/`accept_contract`, `scripts/contract_smoke.py`) | Implemented (default **off**) |
| D10 | F4 rules/beliefs scoping audit | Done (`schism-rules-beliefs-audit.md`) |
| B3 F4 | Schism: settlement-keyed storage/restore, rule/vote threading, trigger + secession + settlement-scoped succession, Chronicle allowlist fix (`SCHISM_ENABLED`, `scripts/schism_smoke.py`) | Implemented (default **off**) |

**F1 default-on gate:** D2 `WIKI_MEMORY` soak accepted on session `2026-08-09T19-47-41`. Before flipping `TESTAMENT_ENABLED` to default `True`, run a matched soak confirming deathbed merges populate the ring and `cultural_carryover` behaves as expected.

**F2 default-on gate:** **FAIL** (2026-08-09). Matched 4+4 min native soak via `uv run python scripts/tom_contention_soak.py --minutes 4` (native server only; refuses Docker `gitserv-sim` on port 5001). Methodologically valid after `scripts/tom_contention_soak.py` hardening: process-tree kill + `sys.executable` start (see [`specs/12-ops.md`](../specs/12-ops.md)). Sessions: baseline `2026-08-09T22-40-54` (`THEORY_OF_MIND_ENABLED` assert **False**), flag-on `2026-08-09T22-45-00` (assert **True**, env `SIM_THEORY_OF_MIND=1`). `piano_module_drops` rate **0.169** (12/71) → **0.25** (26/104) — flag-on materially worse (~48% relative increase). Decision latency p50 **6686** → **7218** ms; p90 **15286** → **13201** ms. **Keep `THEORY_OF_MIND_ENABLED` default `False`.** Invalid prior runs discarded: 45 min flag-on without True assert; 15 min flag-on orphan race. Artifacts: `simulation/logs/tom-contention-soak-4m.log`, `simulation/logs/tom-contention-soak-result.json`.

---

## Verify

```bash
uv sync
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
uv run python scripts/peer_model_smoke.py   # if Theory of Mind touched
uv run python scripts/testament_smoke.py    # if Testament touched
uv run python scripts/contract_smoke.py     # if Contracts/escrow touched
uv run python scripts/schism_smoke.py       # if Schism storage touched
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
