# HANDOFF — Divine Matrix Interventions

**Branch:** `feature/divine-matrix-interventions` (open PR into **`feature/god-mode`**, not `main`).

**Status:** Phases 0–10 implemented; Phase 11 handoff complete. Ready for commit stack review and PR.

**Plan:** [`docs/plan-divine-matrix-interventions.md`](plan-divine-matrix-interventions.md) — canonical phased spec for all Matrix work.

---

## What landed

### Shared plumbing (`godState` v2)

- `GOD_STATE_VERSION = 2` with restore-time `_normalize_god_state` setdefault migration.
- New private maps on `civilization["godState"]`: `whisperCampaigns`, `agentSampling`, `contextMasks`, `decisionGates`, `burningBush`, `anointments`, `identityForges`, `architectZones`, `checkpoints`.
- Context-mask pipeline (`_apply_context_mask`) after `_divine_prompt_lines` in `_build_think_payload`.
- Decision-gate pipeline (compulsion / veto / possession) before `apply_decision`; Sage emergency bypasses gates.
- `MemoryStore.delete_where` for filtered memory surgery.
- Preview → apply → cancel/expiry → `divine.jsonl` contract unchanged; Matrix kinds stay **off** the agent `DECISION_ACTIONS` / `apply_decision` action-sync invariant.

### Ten Matrix interventions (all implemented)

| # | Feature | God kinds | Console UI |
|---|---------|-----------|------------|
| 1 | **Multi-Voice Whispers** | `whisper_campaign` (+ cancel) | Voice tab — campaign form |
| 2 | **Temperature Dial** | `agent_sampling`, `revoke_agent_sampling` | Matrix tab — sampling + revoke |
| 3 | **Memory Surgery** | `memory_insert`, `memory_delete`, `belief_plant` | Matrix tab — insert / delete / belief |
| 4 | **Reality Distortion** | `context_mask` (+ cancel) | Matrix tab — dream / blue pill / red pill / whisper chain |
| 5 | **Possession Pipeline** | `decision_compulsion`, `decision_veto_arm`, `decision_veto_resolve`, `agent_possession` (+ revoke) | Matrix tab — compulsion / veto / possession |
| 6 | **Burning Bush + Merovingian Bargain** | `burning_bush_message`, `burning_bush_close`, `merovingian_bargain`, `bargain_settle` | Matrix tab — bush chat + bargain |
| 7 | **Anointed** | `anoint`, `revoke_anoint` | Matrix tab — destiny / stigmata / oracle |
| 8 | **Identity Forge** | `identity_edit`, `identity_copy_overwrite`, `identity_forge_cancel` | Matrix tab — edit / copy / cancel |
| 9 | **Architect Zones** | `architect_zone`, `architect_zone_cancel`, `architect_release_hold` | Matrix tab — paint / door / limbo forms |
| 10 | **Reload / Déjà Vu** | `checkpoint_create`, `checkpoint_restore`, `deja_vu_replay` (stub) | Matrix tab — create / restore; Déjà Vu disabled in UI |

Specs updated in SDD order: `specs/01-architecture.md`, `02-engine-core.md`, `03-cognition.md`, `04-http-api.md`, `09-systems-society.md`, `11-viewer.md`, `12-ops.md`.

---

## How to verify

### Deterministic smokes (no Ollama)

```bash
uv run python scripts/god_mode_smoke.py    # Matrix + full God-mode regression
uv run python scripts/sid_parity_smoke.py  # unrelated baseline
uv run python scripts/path1_smoke.py         # unrelated baseline
```

`god_mode_smoke.py` covers all ten Matrix phases plus prior God-mode phases (HTTP layer, privacy assertions, checkpoint roundtrip on temp dirs). **Phase 11 result: ALL PASS** (all three scripts green).

### Manual (browser + logs)

1. Start server in a titled `simserver` cmd window (port **5001**); ensure **only one** `simulation/server.py` process.
2. Open `http://127.0.0.1:5001` — Divine Console → **Matrix** tab (and Voice tab for whisper campaigns).
3. Exercise preview → apply for at least one intervention per category; confirm Sight summaries update without leaking private text.
4. Tail `simulation/logs/<session>/divine.jsonl` — each apply records `kind`, `interventionId`, `public` flag, and attribution (`source="divine"`).
5. Poll `/state` — `god` allowlist exposes only public fields (`providence`, `activePublicEvents`, `recentPublicInterventions`); private maps (whispers, sampling, masks, gates, bush, anointments, forges, architect secrets) must be absent.

God mode requires `SIM_GOD_MODE=1`; production posture uses `SIM_GOD_AUTH=1` + `SIM_GOD_TOKEN` (see `specs/12-ops.md`).

---

## Checkpoint disk layout

Operator checkpoints live outside the live DB:

```
simulation/backup/god-checkpoints/<checkpoint-id>/
  state.db
  memory_store.json
```

- Metadata in `godState["checkpoints"]` (cap **5**); `path` stored relative (`backup/god-checkpoints/<id>`).
- Create: pause-safe copy via `save_state` + WAL truncate; restore copies back to live `DB_PATH` + memory store, then `restore_state()`.
- Sight lists checkpoint summaries (id, label, frameTick) — **no absolute disk paths** in API responses.
- Smoke tests inject `GOD_CHECKPOINT_ROOT` / per-engine `god_checkpoint_root` to avoid touching live files.

---

## Flags

| Flag | Default | Notes |
|------|---------|-------|
| `GOD_DEJA_VU_REPLAY` | **off** (`SIM_GOD_DEJA_VU_REPLAY` env) | Stub only — rejects even when enabled; checkpoint restore is the v1 replay story |
| `GOD_MODE_ENABLED` | unchanged | Matrix kinds gate behind existing God control plane |
| `GOD_STATE_VERSION` | `2` | Bumped for Matrix scaffolding |

---

## Known limits / out of scope (per plan)

- **No canvas click-to-paint** — Architect Zones use form-based cell/bounds input only.
- **No tick-accurate Déjà Vu replay** — `deja_vu_replay` is an honest stub behind `GOD_DEJA_VU_REPLAY` (default off).
- **No God kinds in agent action sync** — Matrix commands are not in `DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT`, `apply_decision`, or `ACTION_LABELS`.
- **No God Compiler expansion** — Phase 8 free-prose compiler unchanged.
- **No multiplayer / RBAC** beyond existing `X-God-Token`.
- **Intervened runs not comparable** to untouched autonomous benchmarks — `intervened` marker stays set.
- **Identity copy** does not auto-clone full memory without explicit Memory Surgery.
- **`sim-fast` decision routing** capped (documented) — shared Ollama pool with PIANO.

---

## Next steps for orchestrator

1. Review commit stack on `feature/divine-matrix-interventions`.
2. Push branch; open PR into `feature/god-mode` (not `main`).
3. PR body: link plan + HANDOFF; list smoke commands; note `GOD_STATE_VERSION` 2 save compatibility; screenshots of Matrix tab flows optional.
4. Do **not** commit `simulation/logs/`, `state.db`, or credentials.
