# HANDOFF — Divine Console Improvements

**Branch:** `feature/divine-console-improvements` → PR into **`feature/god-mode`** (not `main`).

**Plan:** [`docs/plan-divine-console-improvements.md`](plan-divine-console-improvements.md)

**Phase status:** Phases **0–12 shipped** (automated verify + draft PR). Manual simserver walkthrough still recommended before merge.

**Base:** `feature/god-mode` tip `89aac707` (Matrix merged via PR #8).

---

## Shipped summary (Phases 0–11)

| Area | What landed |
|---|---|
| **Matrix IA** | Sticky category chips inside one Matrix modal; wider modals for Matrix/Story/Laws/Compile |
| **Focus + canvas** | `godFocusAgentId`; canvas pick when God mode on; seconds-first duration UI; favorites (max 4); irreversible hold/name confirm |
| **Bar HUD** | Effect chips (Providence, Omens, Gates, Zones, Events); status pips; bar pulse on new public interventions |
| **Sight HUD** | Soft-refresh cadence; diff strip; one-click intervene; canvas overlays (focus, zones, limbo, anointed) |
| **Voice QoL** | Preset library; adherence timeline + reply inbox; optional `presentation` (`soft` \| `thunder`) — cosmetic only |
| **History tools** | Filter toolbar; Re-run → Preview; Revoke last cancellable; Markdown export |
| **Miracles / Story / Laws** | Canvas structure pick; preview `warnings[]` on conflicting modifiers; Story recipe bundles |
| **godState v3 + Déjà Vu** | `decisionDigests` ring; `deja_vu_replay` parent → sequenced compulsion gates; Sight summaries |
| **Crowd / dream** | `crowd_compulsion` + `dream_broadcast` batch parents (max 12 targets); cancel-all; Matrix forms |
| **Village pulse** | Ephemeral `pulse` on Sight (crisis, stockpiles, projects, Sage, weather, events, providence summary) |
| **Compiler UX** | Solid Compile bar chrome when enabled; compile → Story handoff via `acceptServerPreview`; A/B contention **deferred** |
| **Plain-language help** | `#divineFeatureGuide` modal intro + everyday `data-tip` / `.divine-help` copy for non-technical operators |

**`GOD_STATE_VERSION` = 3** (digests + crowd/dream parent maps). Village pulse and compiler UX did not bump version.

**Feature flags (default off):**

- `SIM_GOD_DEJA_VU_REPLAY=1` → applyable Déjà Vu replay (`GOD_DEJA_VU_REPLAY`)
- `SIM_GOD_COMPILER=1` → free-prose compiler (`GOD_COMPILER_ENABLED`)

---

## Invariants (unchanged)

- God kinds stay **off** agent action sync (`DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT`, `apply_decision`, `ACTION_LABELS`).
- Mutations only via `/control/god/{preview,apply,cancel,compile}`; Apply body is `{previewId, requestId}` only.
- Private maps never in `/state` god allowlist; escape all dynamic HTML; token in memory / optional `sessionStorage` only.
- SDD: owning spec before code each phase (viewer → `specs/11-viewer.md`).

---

## Verify (Phase 12)

```bash
uv run python scripts/god_mode_smoke.py
uv run python scripts/sid_parity_smoke.py
uv run python scripts/path1_smoke.py
node --check simulation/viewer.js
```

**Phase 12 automated (2026-08-01):** all four commands **PASS**.

Manual (orchestrator before merge): titled `simserver` on port **5001**, single `simulation/server.py` process; Divine bar walkthrough; tail `divine.jsonl`.

---

## Next

1. Manual Divine bar walkthrough on port 5001 (single `simulation/server.py`).
2. Review/merge draft PR [#9](https://github.com/dbrito1231/simulation/pull/9) into **`feature/god-mode`**.
