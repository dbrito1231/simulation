# Plan — Review Fixes (items 1–4, 6–7)

**Source:** `docs/project-review-2026-08-16.md` dislikes list.
**Scope:** items 1, 2, 3, 4, 6. **Two items are out of scope by user decision:**

- **Item 5 (god mode default)** — `GOD_MODE_ENABLED` stays default True until the user says otherwise. No phase below may change that flag or its default.
- **Item 7 (cross-platform)** — Windows is the only supported platform for now. No phase below adds bash recipes or non-Windows handling.

Five phases, ordered by value. Each is independently shippable; Phase 1 is the only one that unblocks other people.

---

## Phase 1 — Make the smart model portable (dislike #1)

**Goal.** `scripts/ollama_setup.py` succeeds on a machine that has never had LM Studio.

**Problem.** `ollama/Modelfile.smart:48` is `FROM C:\Users\dbadmin\.lmstudio\models\...\Qwen3.5-9B-Q4_K_M.gguf`. `ollama create sim-smart` fails everywhere else, and that model serves every agent decision.

**Approach.**
- Add a registry-based `ollama/Modelfile.smart.registry` (same `num_ctx`/sampling `PARAMETER` lines, `FROM <registry tag>`).
- **Verify the tag before writing it** — do not assume `qwen3.5:9b` exists in the Ollama registry. The implementer probes with a real pull/show; if no equivalent Qwen3.5-9B tag exists, escalate to the orchestrator with the closest candidates rather than guessing.
- `create_models()` picks a source in order: (a) `SIM_SMART_GGUF` env var if set and the file exists, (b) the existing local GGUF path if it exists, (c) the registry Modelfile. Prints which one it used.
- Keep `Modelfile.smart` as-is for this machine — no regression to the working setup here.

**Owning specs:** `ollama_config.md` (required-settings table + restore procedure), `README.md` (prerequisites), `specs/03-cognition.md` migration note if the model id changes.

**In scope:** `ollama/Modelfile.smart.registry` (new), `scripts/ollama_setup.py`, `ollama_config.md`, `README.md`.
**Out of scope:** `MODEL_SMART`/`MODEL_FAST` constants, prompt text, `sim-smart-sys`.

**Acceptance:** `uv run python scripts/ollama_setup.py --check` passes here unchanged; a dry-run on the registry path shows the fallback selection and prints its source; `ollama_config.md` documents all three source cases.

---

## Phase 2 — Drop the "minimal" claim, add a real orientation (dislike #2)

**Goal.** The project describes itself accurately, and a newcomer knows where to start.

**Approach.**
- `README.md` and `specs/00-overview.md`: replace "kept intentionally minimal" with an honest one-paragraph scale statement (the review's numbers table: ~52k lines, 66 flags, 47 actions, 67 routes, ~45 god kinds). Keep the "observable/debuggable" goal — that one is still true.
- Add a short **"Start here"** block at the top of `README.md`: run it, open 5001, read `specs/00-overview.md`, everything else is reference.
- State the platform stance plainly in `README.md` prerequisites: **Windows only** — the PowerShell recipes are the supported path, not an oversight.

**Owning specs:** `specs/00-overview.md`, `README.md`.
**In scope:** those two files only.
**Out of scope:** any code, any flag, deleting features to "become minimal again."

**Acceptance:** no file in `README.md`/`specs/` claims minimality of implementation; the numbers stated match a fresh count.

---

## Phase 3 — Minimal automated test layer (dislike #3)

**Goal.** The three most expensive silent regressions get caught automatically.

**Approach.** Add `tests/` with plain `pytest`, runnable as `uv run pytest`, **no Ollama and no server required**. Three files only:

1. `test_action_sync.py` — the action-sync invariant: every name in `DECISION_ACTIONS` appears in `DECISION_SCHEMA`'s enum, in `SYSTEM_PROMPT`, in `apply_decision`'s dispatch, in `available_actions`' filter table, and in viewer `ACTION_LABELS`; and no extras in any of them. This is currently enforced only by spec prose and reviewer attention.
2. `test_normalize_decision.py` — the fallback ladder: unknown action, malformed payload, flag-disabled action, missing required field, and empty/None response each produce a valid action and never raise.
3. `test_state_roundtrip.py` — `save_state()` → `restore_state()` on a cold-start engine preserves agents, civilization keys, and wildlife; an old-shape save still restores.

Add `pytest` as a dev dependency in `pyproject.toml`. Do **not** convert the 35 existing smoke scripts — they stay as-is.

**Owning specs:** `specs/12-ops.md` (scripts/verification section), `CLAUDE.md` ("no test suite" line becomes "unit tests: `uv run pytest`; smokes unchanged").

**In scope:** `tests/*`, `pyproject.toml`, `specs/12-ops.md`, `CLAUDE.md`.
**Out of scope:** rewriting smokes, CI config, coverage targets, touching engine code to make it testable — if a test needs a refactor to pass, escalate instead.

**Acceptance:** `uv run pytest` green in under 30s with Ollama stopped and no server running.

---

## Phase 4 — Clean the doc sprawl (dislike #4)

**Goal.** A browser of the repo sees `specs/` + a handful of live docs, not ~50 stray files.

**Approach.**
- Gitignore the machine-local planning surfaces: `.claude/plans/`, `.cursor/plans/`, `*.mhtml`.
- Move the loose untracked PDFs/HTML/plan drafts currently sitting in `docs/` into `docs/archive/` (already the "historical record only" bucket).
- One `docs/README.md` index: what's live (`HANDOFF.md`, `REFERENCE.md`, `plans/`), what's frozen (`archive/`), and a one-line "read `specs/00-overview.md` first."

**Owning specs:** `specs/00-overview.md` repo-layout table row for `docs/`.

**In scope:** `.gitignore`, `docs/README.md` (new), file moves within `docs/`.
**Out of scope:** deleting anything (move only), touching `specs/`, touching `docs/archive/` contents.

**Acceptance:** `git status` shows no untracked plan/PDF/mhtml noise; every moved file still exists under `docs/archive/`.

---

## Phase 5 — Give wildlife visible behavior (dislike #6)

**Goal.** Fauna behave enough to justify how they look — still **fully deterministic, still zero LLM calls**.

**Approach.** Behind a new flag `WILDLIFE_BEHAVIOR_ENABLED` (default True, one-flag revert), add a small per-creature state machine to `_move_wildlife()`:
- `graze` (slow drift, frequent pauses) ↔ `wander` (current behavior) ↔ `flee` (existing) — plus a `rest` state at night for land kinds.
- Loose herding for flock kinds: mild attraction toward the nearest same-kind creature within a radius, capped so it never overrides flee or the habitat clamp.

Constants live in `sim_engine/constants.py` next to the existing `WILDLIFE_*` tables. No new tick system, no new `/state` fields beyond an optional `state` string on the wildlife row for the viewer.

**Owning specs:** `specs/02-engine-core.md` (huntable wildlife section), `specs/05-world.md`, `specs/01-architecture.md` flag index, `specs/11-viewer.md` if the viewer renders the state.

**In scope:** `mixin_wildlife.py`, `constants.py`, `mixin_snapshot.py` (one field), the four specs above.
**Out of scope:** any LLM involvement, changes to `hunt_wildlife`, new actions, new god kinds, predator-prey killing.

**Acceptance:** flag off is byte-identical to current behavior; flag on shows creatures grazing/herding in the viewer; `uv run python scripts/hunt_conflict_smoke.py` still passes.

---

## Sequencing

Phase 1 first (it's the only external blocker). Then 3 (tests protect everything after). Then 2 and 4 (doc-weight, low risk, either order). Phase 5 last — it's the only one that changes simulation behavior.

## Standing constraints for every phase

- SDD: owning spec updated in the same change as the code.
- KISS: smallest change that satisfies the phase. No speculative refactors.
- **Do not touch `GOD_MODE_ENABLED` or `GOD_AUTH_REQUIRED` defaults.**
- Implementer doubts go to the orchestrator, never straight to the user.
