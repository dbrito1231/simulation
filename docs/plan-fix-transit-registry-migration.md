# Plan: Fix — Transit Migration vs. the Retired Recipe Catalog

**Status:** COMPLETE (Phases 1–3 landed in commit `0cfa047`; Phase 4 implemented and verified 2026-07-16)
**Created:** 2026-07-16
**Branch:** `feat/civ-advancement`
**Related:** [plan-civ-1-advanced-civilization.md](plan-civ-1-advanced-civilization.md) (Phase 3 introduced the bug this plan fixes)

## The bug, in plain terms

The Civ-1 Phase 3 update gives ocean travel to the village by stamping a
`transit` unlock onto the **dock** and **shipyard** entries in
`civilization["projectRegistry"]` (the "recipe catalog") during
`restore_state()`. But the catalog deliberately holds only
`MAX_APPROVED_CUSTOM = 15` entries — `_maybe_retire_blueprint` evicts the
oldest *built* type to keep an invention slot free (specs/09, invention
safeguards). A long-lived save like the live world has invented 90+ types, so
the dock/shipyard recipes were evicted long ago even though the buildings
still stand.

The migration did `projectRegistry.get(tid)` → `if not a dict: continue` —
i.e. **when the recipe was missing it silently skipped the stamp**. No error,
no log line. Result on exactly the saves the feature was built for:

- `_has_ocean_transit()` stayed False → ocean gathering blocked, ocean
  caravans never fired, boats never consumed.
- The Civic Era gate (working light + working transit) could never be reached.

A fresh village was unaffected — only old saves hit it, which is why smokes
(cold-start engines) passed while the live world was broken. Found by running
a deterministic offline soak against a *copy* of the live `state.json` and
noticing `lit=[]`, `era=Mill Era`, and `has_ocean_transit: False` despite the
migration having "run".

## Root cause (one sentence)

The transit migration attached a capability to a registry entry and assumed
the entry always exists, but the registry is designed to forget old entries —
the neighboring hearth/lighthouse light migration already handled this with a
create-from-instance fallback; the transit migration lacked it.

## Phases

### Phase 1 — Spec first (SDD) ✅

specs/10-path1.md, `TRANSIT_ENABLED` section: new "Save migration" paragraph
specifying the create-from-instance fallback — when the registry entry is
gone but a structure instance of the type still stands, recreate a minimal
entry from the instance, then add the
`{"kind":"transit","terrain":"ocean","consumes":{"boat":1}}` unlock.
Idempotent.

### Phase 2 — Engine fix ✅

`sim_engine.py restore_state()` (~line 10034): the dock/shipyard loop now
mirrors the hearth/lighthouse pattern directly above it — if
`projectRegistry` lacks the entry but a structure of that type exists in
`civ["structures"]`, build a minimal entry (name from the instance or
title-cased type id, `needs {"wood":2,"stone":2}`, visualStyle from the
instance or `"generic"`, empty function block, `custom: True`), register it,
then stamp the transit unlock if no transit unlock is already present.

**Model:** `implementer` subagent (Sonnet 5), per the CLAUDE.md model policy
(orchestrator dispatches, writes no implementation code).

### Phase 3 — Regression smoke ✅

`scripts/path1_smoke.py`, `test_transit_migration_from_instance()`:
serializes an engine whose `dock` registry entry was removed while a dock
structure instance remains (via `_serialize_state()` to a temp file;
`STATE_PATH` monkeypatched for the test only, real `state.json` untouched),
restores into a fresh engine, and asserts the dock entry is recreated with
the ocean-transit unlock. Verified against a copy of the live save:
`dock fn: {'unlocks': [{'kind': 'transit', ...}]}` after restore.

**Model:** same `implementer` dispatch as Phase 2.

### Phase 4 — Hardening ✓

The same trap awaits any future migration that attaches abilities to registry
entries (Civ-1 Phase 4-style upkeep generalization, future transit terrains,
etc.). Completed with two hardening changes and expanded regression coverage:

1. Extracted the duplicated create-from-instance logic (hearth/lighthouse
   branch + dock/shipyard branch, both in `restore_state()`) into the
   narrowly migration-focused `_ensure_registry_entry_from_instance(civ,
   type_id)` helper. Both migrations call it; existing entries are returned
   unchanged, missing registries/instances return `None`, and recreated
   entries preserve the prior defaults.
2. Updated specs/09's invention safeguards table: registry retirement means
   "forgotten recipe", and code attaching semantics to a registry entry must
   either tolerate its absence or recreate a minimal entry from a standing
   instance.
3. Expanded the Path 1 restore regression to cover both a retired hearth
   recipe and a retired dock recipe, including reconstructed defaults,
   attached semantics, and helper idempotence for existing entries.

**Model:** `implementer` (`gpt-5.4`, the configured Sonnet 5-equivalent ceiling); single dispatch.

## Verification record

- `uv run python scripts/path1_smoke.py` — PASS (includes the new migration
  assertions for both light and transit callers and all prior Civ-1 checks;
  rerun after Phase 4 on 2026-07-16).
- `uv run python scripts/sid_parity_smoke.py` — PASS (includes the Civic Era
  gate check: light-only ⇒ no era; light + transit ⇒ Civic Era; rerun after
  Phase 4 on 2026-07-16).
- `uv run python -m py_compile simulation/sim_engine.py scripts/path1_smoke.py`
  — PASS after Phase 4 on 2026-07-16.
- Offline check against a live-save copy: dock registry entry recreated with
  the transit unlock on restore.
- Live server restarted on the fixed code (2026-07-16); flags echoed, world
  resumed.

## Known remaining world-state caveat (not a code issue)

On the live save, the dock/shipyard (and hearth/lighthouse) structures are
**ruins** (condition 0). The fixed migration restores the *capability
plumbing*, but `_has_ocean_transit()` requires a *working* structure — agents
must repair a dock (half original materials; the village holds ~10k planks)
before ocean caravans, boat consumption, and the Civic Era can actually fire.
This is the intended maintenance pressure, not a regression.
