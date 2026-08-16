# Rename "schism" → "faction_split"

## Context

"Schism" is the internal name for the settlement-secession mechanic (Feature
4.3): a cluster of allied agents who share a belief and are rivals with their
elder can split off and found a new settlement. The user wants a simpler,
plainer name. After confirming with the user: the new name is **"faction
split"** (`faction_split` in identifiers), applied to flags, functions,
string literals/event kinds, the persisted `lastSchismFrame` field, the spec
section, and the smoke-test script. The user confirmed this is allowed to be
a breaking change for the persisted chronicle/activity event-kind string —
old logs keep the old string as historical record; new events use the new
name going forward.

Historical/point-in-time documents (already-shipped plan write-ups, PR logs,
changelog prose) are **not** touched, per the same "historical record, don't
rewrite" convention this repo already applies to `docs/archive/`. Only live
code, live specs (SDD source of truth), and live run-commands are renamed.

## Naming map

| Old | New |
|---|---|
| `SCHISM_ENABLED` | `FACTION_SPLIT_ENABLED` |
| `SCHISM_MIN_CLUSTER` | `FACTION_SPLIT_MIN_CLUSTER` |
| `SCHISM_COOLDOWN_FRAMES` | `FACTION_SPLIT_COOLDOWN_FRAMES` |
| `_SCHISM_FLAT_KEYED_PAIRS` | `_FACTION_SPLIT_FLAT_KEYED_PAIRS` |
| `_init_schism_storage` | `_init_faction_split_storage` |
| `_wrap_schism_storage` | `_wrap_faction_split_storage` |
| `_migrate_schism_storage_on_restore` | `_migrate_faction_split_storage_on_restore` |
| `_find_schism_cluster` | `_find_faction_split_cluster` |
| `_init_schism_settlement_buckets` | `_init_faction_split_settlement_buckets` |
| `_ensure_schism_settlement` | `_ensure_faction_split_settlement` |
| `_execute_schism` | `_execute_faction_split` |
| `_maybe_trigger_schism` | `_maybe_trigger_faction_split` |
| `civilization["lastSchismFrame"]` | `civilization["lastFactionSplitFrame"]` |
| `_push_communication("schism", ...)` kind | `"faction_split"` |
| `_push_chronicle(kind="schism", ...)` | `kind="faction_split"` |
| `_log_benchmark("schism", ...)` metric name | `"faction_split"` |
| `CHRONICLE_MILESTONE_KINDS` member `"schism"` | `"faction_split"` |
| Activity message `f"Schism: {names} secede..."` | `f"Faction split: {names} secede..."` |
| Settlement-id prefix `f"schism_{frameTick}"` | `f"faction_split_{frameTick}"` |
| `anomaly_radar.py` metric compare `"schism"` / `"kind": "schism"` | `"faction_split"` |
| `anomaly.js` `ANOMALY_KIND_ORDER` entry, `kind === "schism"`, label `"Schism"` | `"faction_split"`, label `"Faction Split"` |
| `divine-bootstrap.js` prose "schisms" | "faction splits" |
| Spec anchor `## SCHISM_ENABLED (default True) {#schism_enabled}` | `## FACTION_SPLIT_ENABLED (default True) {#faction_split_enabled}` |
| Spec sub-heading `### F4.3 — schism trigger, secession, elder (flag on)` | `### F4.3 — faction split trigger, secession, elder (flag on)` |
| `scripts/schism_smoke.py` | `scripts/faction_split_smoke.py` |

**Not renamed (historical records, left as-is):** `.claude/plans/schism-rules-beliefs-audit.md`, `.claude/plans/emergence-breakthroughs.md`, `docs/plans/idea-07-anomaly-radar/plan.md`, `docs/pull-requests.md`, `docs/fun-and-evolution-ideas.md`, `docs/HANDOFF.md` changelog-table prose, `docs/archive/**`, `.cursor/plans/**`. Exception: `docs/HANDOFF.md`'s live smoke-test run command (`uv run python scripts/schism_smoke.py`, ~line 147) must be updated to the new filename since it's an active instruction, not a historical description.

## Isolation: worktree + branch + separate Docker container

All work happens in an isolated git worktree so the main checkout (and the
always-on server on port 5001, per this repo's "server runs 24/7" memory
convention) is untouched throughout.

- **Branch:** `rename/schism-to-faction-split`
- **Worktree path:** `../gitserv-sim-faction-split` (sibling to the main
  repo directory), created via `git worktree add -b rename/schism-to-faction-split ../gitserv-sim-faction-split main`
- **Docker container:** built from the worktree, own image/container name
  (e.g. `gitserv-sim-faction-split`), **host port 5020** mapped to the
  container's internal 5001 (`-p 5020:5001`), `SIM_OLLAMA_HOST=host.docker.internal:11434`
  (same host-native Ollama the main server uses — no isolation needed there,
  it's stateless inference).
- **Own state:** separate `state.db`, `memory_store.json`, and `logs/`
  living inside the worktree directory (`../gitserv-sim-faction-split/simulation/...`),
  pre-created empty per the Docker bind-mount rules in CLAUDE.md (empty
  `state.db` file, `memory_store.json` as `{}`, `logs/` directory as an
  actual directory, not left for Docker to auto-create) — never touches the
  main checkout's `simulation/state.db`.
- Implementer/reviewer phases below all operate inside this worktree.
- **After all phases pass** and the container starts clean at
  `http://127.0.0.1:5020`: open a PR from `rename/schism-to-faction-split`
  into `main` via `gh pr create` (ask before pushing/opening the PR, per the
  usual confirm-before-visible-action rule).
- **Teardown is explicit and user-driven**: do not stop/remove the
  `gitserv-sim-faction-split` container, do not remove the worktree, and do
  not delete the branch until the user says to, even after the PR merges.

## Phases (implementer → reviewer per phase, per AGENTS.md)

**Phase 0 — Worktree setup + plan doc**:
- Create the worktree/branch as described above.
- Copy this plan into the repo at `docs/plans/rename-schism-to-faction-split/plan.md` inside the worktree (matching the existing `docs/plans/idea-XX/plan.md` convention), so it's committed as part of the change rather than living only in the local `.claude/plans` scratch location.
- Pre-create the worktree's own empty `state.db`, `memory_store.json` (`{}`), and `logs/` directory per the Docker bind-mount rules.

**Phase 1 — Engine core + owning spec** (same change, per SDD):
- `simulation/sim_engine/constants.py`: rename the 3 constants + `__all__` exports + section comment.
- `simulation/sim_engine/__init__.py`: rename the corresponding exports.
- `simulation/sim_engine/mixin_governance_culture.py`: rename all functions, the `_SCHISM_FLAT_KEYED_PAIRS` tuple, `lastSchismFrame` key, and the string-literal event kinds/messages/settlement-id prefix listed above.
- `simulation/sim_engine/mixin_council_growth.py`, `mixin_crafting_rules.py`, `mixin_lifecycle.py`, `mixin_persistence.py`, `mixin_think_job.py`, `core.py`, `mixin_snapshot.py`: update every reference to the renamed constants/functions (flag guards, calls, snapshot payload key).
- `specs/09-systems-society.md`: rename the `SCHISM_ENABLED` section heading/anchor, the F4.3 sub-heading, and all prose occurrences of "schism"/"Schism" in that file (trigger predicate, secession description, chronicle-kinds list, self-link).

**Phase 2 — Cross-spec references + server/viewer**:
- `specs/01-architecture.md`, `specs/02-engine-core.md`, `specs/05-world.md`, `specs/10-path1.md`: update the 4 inbound links to `09-systems-society.md#faction_split_enabled`, plus any other "schism" prose in those files.
- `specs/04-http-api.md`, `specs/11-viewer.md`, `specs/12-ops.md`: update prose mentions of `_execute_schism`/"schism" event kind/etc.
- `simulation/_server/anomaly_radar.py`: rename the metric compare and `"kind"` literal.
- `simulation/viewer/anomaly.js`: rename the `ANOMALY_KIND_ORDER` entry, comparison, and display label.
- `simulation/viewer/divine-bootstrap.js`: update the prose string.

**Phase 3 — Script rename + live doc pointer**:
- Rename `scripts/schism_smoke.py` → `scripts/faction_split_smoke.py`; update its internal calls to the renamed functions/constants (it currently calls `_find_schism_cluster`, `_init_schism_settlement_buckets`, `_execute_schism`).
- `scripts/idea07_anomaly_radar_smoke.py`: update its hardcoded `metric`/`kind` literals and assertions from `"schism"` to `"faction_split"` (found during Phase 2 review — `anomaly_radar.py` now matches on `"faction_split"` only, so this smoke currently fails on `test_schism_reported_every_time` until updated).
- Update `docs/HANDOFF.md`'s live run-command line (only) to the new script path.
- Grep the full repo (excluding `docs/archive/**`, `.cursor/**`, and the historical files listed above) for any remaining case-insensitive `schism` hits and resolve or explicitly confirm they're intentionally-preserved historical text.
- Run `uv run python scripts/faction_split_smoke.py` (native, inside the worktree) and confirm it passes, plus `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py` as a regression check.

**Phase 4 — Docker verification + PR** (orchestrator step, not an implementer phase):
- Build the image from the worktree: `docker build -t gitserv-sim-faction-split .`
- Run it with `-p 5020:5001`, `SIM_OLLAMA_HOST=host.docker.internal:11434`, and bind mounts to the worktree's own `state.db`/`logs/`/`memory_store.json` (pre-created per CLAUDE.md's bind-mount rules).
- Confirm it comes up clean at `http://127.0.0.1:5020` — no startup errors, and if a faction-split chronicle event occurs during a run, confirm it's tagged `faction_split` not `schism`.
- Hand off to the user to verify through the container themselves.
- Once the user confirms it's good, ask before pushing the branch and opening the PR (`gh pr create`, base `main`, from `rename/schism-to-faction-split`).
- Leave the worktree, branch, and container running/intact until the user explicitly says to tear them down (even after merge).

## Verification

- `uv run python scripts/faction_split_smoke.py`, `scripts/sid_parity_smoke.py`, `scripts/path1_smoke.py` all pass inside the worktree (native).
- Repo-wide case-insensitive grep for `schism` returns only the intentionally-preserved historical files.
- The `gitserv-sim-faction-split` Docker container starts clean on `http://127.0.0.1:5020`, fully isolated from the main checkout's port-5001 server and `state.db`.
- User verifies via the container before the PR is opened; PR is opened only after that confirmation.
