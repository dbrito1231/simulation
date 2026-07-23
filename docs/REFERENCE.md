# REFERENCE.md — read on demand

Deep mechanics now live in [`specs/`](../specs/00-overview.md) (index:
[specs/00-overview.md](../specs/00-overview.md)) — that's the canonical,
rebuildable spec set; edit specs first, then code. This file is now a slim
router to specs plus operational notes and historical-rationale pointers that
aren't load-bearing spec content. Read the code for exact current behavior.

## Where things live now

- Feature flags (complete index, defaults, owning spec): [specs/01-architecture.md](../specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy).
- The build/blueprint pipeline (`start_project` → gather → contribute →
  `build_structure`, the two-stage Sage review flow): [specs/07-actions.md](../specs/07-actions.md).
- Sage's deterministic emergency system (elder rescue, in-flight decision
  discard): [specs/02-engine-core.md](../specs/02-engine-core.md#sage-emergency).
- Invention pipeline safeguards (`MAX_APPROVED_CUSTOM`, rejection/review
  amnesty, invention backstop/council): [specs/09-systems-society.md](../specs/09-systems-society.md).
- Action-sync invariant, threading/lock discipline: [specs/01-architecture.md](../specs/01-architecture.md).

## LLM operational notes (not already canonical in specs/03)

Canonical constants (model ids, timeouts, sampling, prompt sizing, retries,
routing) live in [specs/03-cognition.md](../specs/03-cognition.md). Operational
tips only:

- **Model-id matching:** `MODEL_SMART`/`MODEL_FAST` (server.py:49-50) must
  match an id LM Studio actually has loaded — check `GET /v1/models`. If a
  routed id 404s, the server auto-falls-back to `"local-model"` for the rest
  of the session (see specs/03's Retries section).
- **`scripts/lms_load.py`** is the canonical CLI loader for the target LM
  Studio config (model, context, parallel slots, flash attention) — run it
  instead of clicking through the LM Studio GUI. `--check` reads back the
  current config without applying anything.
- **PIANO/META roster advice:** `PIANO_MODULES`/`META_SYSTEM` default on in
  sim_engine.py and fan out multiple LLM calls per think turn. For local LM
  Studio setups, use a reduced roster and a correspondingly raised
  context — reduce the roster via `{"agents": N}` in the JSON body of
  `POST /control/reset`, or the `SIM_AGENTS` env var at startup, not a URL
  query param. Details: [specs/03-cognition.md](../specs/03-cognition.md).

## Historical rationale (archived — not load-bearing)

These describe *why* past design decisions were made; the current, correct
behavior is always the code + specs/ above.

- `docs/archive/cursor-plans-consolidated.md` — merged record of the former
  `.cursor/plans/` directory.
- `docs/archive/docs-archive-consolidated.md` — earlier consolidated docs
  archive.
- `docs/archive/civilization-emergence-plan.md` — original phase-by-phase
  mechanic design rationale.
- `docs/archive/path-1-minecraft-like-world-plan.md` — the Path 1 (2D world
  depth) plan; current behavior is [specs/10-path1.md](../specs/10-path1.md).
  Companion machine-readable contract: `.cursor/path-1-integration-contract.json`.
- `docs/archive/rule-oscillation-fix-plan.md` — the anti-oscillation guard
  incident/fix; current behavior is documented in
  [specs/09-systems-society.md](../specs/09-systems-society.md).
- `docs/archive/ISSUES.md` — a diagnostic of a previously-broken state, kept
  for context only.
