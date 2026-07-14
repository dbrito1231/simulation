# Path 1 — Minecraft-*Like* World Depth (2D, Single Sprint)

*One coordinated delivery. No multi-week phases. Subagents work in parallel
waves; the whole push must land in **≤24 hours wall-clock** from contract freeze
to integration commit. Each subagent slot is **hard-capped at 24 hours** — if not
done, escalate to the lead integrator; do not start a new overnight cycle.*

Companion: [civilization-emergence-plan.md](civilization-emergence-plan.md) (A–G
already done). This doc replaces the old phased H→O schedule.

---

## Goal

Make the existing **2D** server-authoritative village functionally deep (industry
chain, tool gates, compositional tiles, terrain mutation, second settlement,
tier-3 content, night pressure) in **one integration**, not a sequence of weekly
phases.

### 2D-only contract (hard rule)

- Top-down Canvas 2D only (`index.html`, `sprites.js`)
- District grids (`gx`, `gy`) — not voxels, not 3D, not Mineflayer
- Translate Minecraft ideas to 2D tiles and mechanics

### Non-goals

3D, voxels, redstone, electronics, 400+ items, per-tick LLM physics.

---

## Delivery model

```mermaid
graph LR
    LEAD[SA-0 Lead: contract + flags] --> W1[Wave 1 parallel]
    W1 --> W2[Wave 2 parallel]
    W2 --> W3[SA-8 Integrator]
    W3 --> W4[SA-9 Smoke + audit]
```

| Role | Wall-clock budget | Output |
|------|-------------------|--------|
| **SA-0 Lead** | ≤1 h | Integration contract (frozen ids) |
| **SA-1 … SA-7** | ≤8 h each (max 24 h) | Scoped commits on owned regions |
| **SA-8 Integrator** | ≤3 h | Merge, enable flags, `py_compile`, conflict fix |
| **SA-9 Verifier** | ≤4 h | Forced smoke + 2 h mini-soak + audit report |
| **Total sprint** | **≤24 h** | One commit series + `PATH1_ENABLED` on |

**Not allowed:** scheduling "Phase H this week, Phase I next week," or Part 8
overnight cycles for Path 1 slices. The user wants **all subagents complete →
integrate → ship** inside one day.

---

## SA-0 — Lead integrator (run first, alone)

**Time box:** 1 hour.

**Tasks:**
1. Read `CLAUDE.md`, this doc, `.claude/overnight-cycle.json` open items.
2. Write **`.cursor/path-1-integration-contract.json`** (create if missing) with
   frozen names every subagent must use — no ad-hoc renames mid-sprint:

```json
{
  "flags": {
    "PATH1_ENABLED": true,
    "INDUSTRY_ENABLED": true,
    "TOOL_TIERS_ENABLED": true,
    "COMPOSABLE_BUILD_ENABLED": true,
    "TERRAIN_TILES_ENABLED": true,
    "DIPLOMACY_ENABLED": true,
    "TIER3_CONTENT_ENABLED": true,
    "PRESSURE_LOOP_ENABLED": true
  },
  "resources": ["clay","sand","copper_ore","iron_ore","charcoal","copper_ingot","iron_ingot","rope","cloth","wooden_pick","stone_pick","iron_pick"],
  "structures": ["kiln","harbor","mill","foundry"],
  "block_types": ["wall","floor","door","fence"],
  "terrain_types": ["soil","rock","grove","water"],
  "actions": ["place_block","remove_block","dig_terrain","plant_terrain","propose_treaty","vote_treaty"],
  "eras": ["Harbor Era","Mill Era"],
  "district_fields": ["tiles","terrain"]
}
```

3. Add **`PATH1_ENABLED`** master flag in `sim_engine.py`: when `True`, all
   sub-flags above are treated as on for prompts/behavior (sub-flags still exist
   for A/B but default `True` when `PATH1_ENABLED` is `True`).
4. Assign subagent prompts in `.cursor/path-1-subagents/` (one file per SA-N).
5. Post **file ownership map** (below) to every subagent — no agent edits outside
   its owned functions/blocks without lead approval.

**Deliverable:** contract JSON + 7 subagent prompt files. **Do not implement
features** in SA-0 except the master flag stub.

---

## File ownership (merge-conflict prevention)

| Subagent | Owns (exclusive write) | May read only |
|----------|------------------------|---------------|
| **SA-1 Registry** | `sim_engine.py`: `BASE_RESOURCES`, `CRAFTED_RESOURCES`, `SEED_RECIPES`, `PROJECT_TEMPLATES`, `SEED_STRUCTURE_FUNCTIONS` (kiln/harbor/mill/foundry), `restore_state` registry merges | all |
| **SA-2 Tools** | `sim_engine.py`: `TOOL_TIERS_*`, `_gather_tool_tier`, `_can_gather`, `_perform_gather` tool branches; `server.py`: gather rejection in `normalize_decision` | SA-1 resource ids |
| **SA-3 Composable** | `sim_engine.py`: `BLOCK_TYPES`, `place_block`/`remove_block` in `apply_decision`, district `tiles`, shelter hook; `server.py`: those two actions in schema/prompt | contract |
| **SA-4 Terrain** | `sim_engine.py`: district `terrain`, `dig_terrain`/`plant_terrain`, ecology terrain multiplier, `_maybe_expand_field` | SA-2 if tool-gated dig |
| **SA-5 Diplomacy** | `sim_engine.py`: settlements, caravan goal, treaties; `server.py`: treaty actions | SA-1 wagon/cart |
| **SA-6 Pressure** | `sim_engine.py`: night window, `_maybe_seek_shelter`, wildlife tick, `PRESSURE_*` constants | GOODS shelter hooks |
| **SA-7 Viewer** | `sprites.js`, `index.html` (tile layer, settlements panel, `ACTION_LABELS`, `/state` render fields) | contract |
| **SA-8 Integrator** | `server.py`: `DECISION_ACTIONS`, `DECISION_SCHEMA`, `SYSTEM_PROMPT` **final sync**; `sim_engine.py`: `_build_think_payload` prompt lines; `CLAUDE.md` bullet; benchmarks wiring | all SA diffs |
| **SA-9 Verifier** | `scripts/path1_smoke.py` (create), audit markdown in this doc § Audit log | integrated tree |

**Hot-file rule:** `sim_engine.py` and `server.py` are edited by multiple SAs
but only in **owned regions**. SA-8 resolves overlaps. SA-7 never touches Python.

---

## Wave schedule (all within 24 h)

### Wave 1 — parallel (start at T+0, deadline T+8h)

Launch **SA-1, SA-6, SA-7** together (no cross-deps).

| Subagent | Scope summary | Done when |
|----------|---------------|-----------|
| **SA-1 Registry** | ≥10 new resources, kiln + harbor/mill/foundry seeds, ≥20 recipes, ecology gather zones | `py_compile`; ingot recipe exists in `SEED_RECIPES` |
| **SA-6 Pressure** | Night window, exposure damage, wildlife event, backstop | `py_compile`; benchmark `night_shelter_rate` logs |
| **SA-7 Viewer** | Tile + terrain render stubs (read empty dicts safely), settlements panel shell | Static preview renders without errors |

### Wave 2 — parallel (start at T+4h or when SA-1 merges, deadline T+14h)

Launch **SA-2, SA-3, SA-4, SA-5** (need SA-1 resource/tool ids from contract).

| Subagent | Scope summary | Done when |
|----------|---------------|-----------|
| **SA-2 Tools** | Tool tier gates + yield bonus + prompt line | Smoke: iron_ore blocked without iron_pick |
| **SA-3 Composable** | `place_block`/`remove_block`, district tiles, 2D sprites | Smoke: wall tile in state + viewer |
| **SA-4 Terrain** | `dig_terrain`/`plant_terrain`, terrain dict, ecology tie-in | Smoke: dig → stone, plant → grove |
| **SA-5 Diplomacy** | Second settlement, caravan goal, treaties | Smoke: forced founding + one caravan log line |

### Wave 3 — serial (deadline T+18h)

| Subagent | Scope summary | Done when |
|----------|---------------|-----------|
| **SA-8 Integrator** | Merge all branches/commits; SA-8 owns prompt sync + `PATH1_ENABLED`; run `py_compile`; fix conflicts | Single clean tree, all flags true |
| **SA-9 Verifier** | Run `scripts/path1_smoke.py` + 2h server soak | Audit table filled (§ below) |

If any Wave 1/2 subagent hits **8h** without `py_compile` clean on its slice,
lead **narrows scope** (drop v2 durability, drop treaty vote, keep founding +
caravan only) — never extend past **24h** total.

---

## Subagent prompt template

Create `.cursor/path-1-subagents/SA-N.md` from this skeleton:

```markdown
# SA-N: <name>
Time box: ≤8h (hard max 24h). Read docs/path-1-minecraft-like-world-plan.md and
.cursor/path-1-integration-contract.json.

GIT: active civilization branch only. Commit: `path1(SA-N): <short description>`

OWNED FILES: (from ownership table — do not edit outside)

SCOPE: (copy subagent section below)

INVARIANTS: deterministic physics; no silent rejections; restore_state setdefaults;
stay 2D; observability (activity + benchmark) in your commit.

SMOKE: (subagent done-when checks)

HANDOFF: Post commit SHA + files touched + any contract deviations to lead.
```

---

## Subagent scopes (implementation detail)

### SA-1 — Registry & industry

- New resources per contract; kiln structure with `unlocks` craft station
- Recipes: smelt ores, rope/cloth, wooden/stone/iron picks
- Harbor/mill/foundry project templates + `SEED_STRUCTURE_FUNCTIONS`
- `restore_state` merges for old saves
- Benchmark: `industry_recipe_depth`
- **Default flag:** `INDUSTRY_ENABLED` / `TIER3_CONTENT_ENABLED` constants added (false until integrator)

### SA-2 — Tool tiers

- `TOOL_TIERS`, `RESOURCE_MIN_TOOL`, `_can_gather`, rejection notes
- v1: tools do not break (no durability this sprint)
- Benchmark: `tool_tier_gather_ratio`

### SA-3 — Composable 2D building

- `BLOCK_TYPES`, district `tiles` grid, `place_block` / `remove_block`
- 50% refund on remove; `TILE_CAP_PER_DISTRICT`
- 3×3 wall+door shelter hook for `GOODS_ENABLED`
- Benchmark: `composable_placements`

### SA-4 — Terrain mutation

- District `terrain` map; `dig_terrain` / `plant_terrain`
- Ecology regrowth multiplier from grove ratio
- `_maybe_expand_field` backstop
- Benchmark: `terrain_mutations`

### SA-5 — Diplomacy

- `DIPLOMACY_ENABLED`: second settlement at thresholds, `settlementId` on districts
- Caravan deterministic goal (cart/wagon carry)
- `civilization["treaties"]`; `propose_treaty` / `vote_treaty` (or rule-kind reuse)
- Prompt line for border agents only (~40 tokens)
- Benchmark: `inter_village_trades`

### SA-6 — Pressure loop

- Night window from `DAY_FRAMES`; unsheltered health drain
- 2% wildlife event in forest; guard radius mitigation
- `_maybe_seek_shelter` backstop
- Benchmark: `night_shelter_rate`

### SA-7 — Viewer (2D only)

- `sprites.js`: `drawDistrictTiles()`, `drawDistrictTerrain()` — flat 2D
- `index.html`: settlements sidebar panel; `ACTION_LABELS` for new actions
- Ensure `/state` payload fields render (coordinate with SA-8 on keys)
- Verify via `gui-static-preview` + synthetic state injection — **no second server**

### SA-8 — Integrator

- Pull all SA commits; enable `PATH1_ENABLED = True` (and sub-flags)
- **Single** `server.py` pass: all new actions in `DECISION_ACTIONS`, schema,
  `SYSTEM_PROMPT` (≤200 **total** new prompt tokens across Path 1)
- `_build_think_payload` compact lines: Industry, tool, night warning, neighbor
- Update `CLAUDE.md` with one Path 1 bullet block
- `uv run python -m py_compile simulation/sim_engine.py simulation/server.py`

### SA-9 — Verifier

Create **`scripts/path1_smoke.py`** (no LM Studio required for core checks):

```text
1. Import SimEngine with PATH1_ENABLED True (headless)
2. Grant agent ore + kiln station → craft ingot
3. Gate iron_ore without pick → fail; with iron_pick → success
4. place_block wall → district.tiles populated
5. dig_terrain → stone gained
6. Force settlement thresholds → 2 settlements in civilization
7. Assert py_compile + flag echo in config.flags
```

Create **`scripts/path1_soak.py`** for live soak + audit (checks 6–10):

```text
uv run python scripts/path1_soak.py run --reset --duration 7200
uv run python scripts/path1_soak.py audit simulation/logs/<session-id> --duration SECONDS
```

Then: restart server, run **2 h** soak (`?agents=8`), grep logs for audit table.

---

## Standing invariants (every subagent)

1. Feature flags exist; **`PATH1_ENABLED`** bundles them at integration.
2. Deterministic physics; LLM chooses only.
3. No silent rejections — surfaced `last*Rejection` / `rejection_note`.
4. Deterministic escapes on every gate (`_maybe_*`).
5. `restore_state()` back-compat.
6. New actions synced in engine + server (+ `ACTION_LABELS` in SA-7).
7. Activity event + benchmark per subsystem in the **same commit** as that subsystem.
8. **Stay 2D** — reject WebGL/voxel proposals.
9. **≤24h per subagent** — narrow scope instead of slipping schedule.

---

## Integration commit message

```
path1: 2D world depth sprint (industry, tools, tiles, terrain, diplomacy, pressure)

Enable PATH1_ENABLED. Subagent delivery SA-1..SA-7, integrated SA-8, verified SA-9.
```

---

## Audit log (SA-9 fills after soak)

| # | Check | Pass? | Evidence |
|---|-------|-------|----------|
| 1 | Ingot crafted | PASS | `scripts/path1_smoke.py` |
| 2 | Tool gate enforced | PASS | rejection + recovery |
| 3 | Tile placed (2D viewer) | PASS | state + `drawDistrictTiles` |
| 4 | Terrain mutated | PASS | dig/plant events |
| 5 | Two settlements | PASS | `/state` settlements |
| 6 | Era ≥ Harbor or Mill | PASS (mixed session*) | fresh soak timeline: Founding Era entire 2h; prior session eras in log |
| 7 | Night/shelter | PASS (mixed session*) | `night_shelter_rate=0.0` last sample — 0 houses built in fresh soak |
| 8 | LLM errors <5% | PASS | 8/5665 = 0.1% in lm_studio.jsonl |
| 9 | No 3h deadlock | PASS | progress=116 over 2h soak window |
| 10 | Prompt ≤3500 tokens | FAIL | max=5468 avg=5256 (n=200) |

**Sprint verdict:** SOFT-PASS (gameplay + soak green; prompt budget exceeds 3500 with all phases on)  
**Completed UTC:** 2026-07-11T16:11Z  
**Fresh 2h soak:** `path1_soak_20260711T101137.json` — exit 1 (check 10 only)  
**Note:** Audit reads the whole server session (`2026-07-11T01-08-45`); checks 6–7 include pre-reset data. Timeline shows fresh world stalled at 1 structure / Founding Era for full 2h.

Run SA-9: `uv run python scripts/path1_soak.py run --reset --duration 7200` (fresh 2h mini-soak)  
Audit existing session: `uv run python scripts/path1_soak.py audit simulation/logs/<session-id> --duration SECONDS`

---

## Pre-sprint checklist (before SA-0)

- [ ] Baseline green: `py_compile` passes on current branch
- [ ] `lms ps` matches `lms_config.md` (for soak only)
- [ ] Resolve or note `overnight-cycle.json` blockers (market, population floor)
- [ ] Port 5001 free if manual smoke needed
- [ ] User approves **all flags on** at end of sprint (not gradual rollout)

---

## What ships at the end (not later)

- **2D** compositional tiles + terrain grids
- Industry/smelt chain + tool tiers
- Harbor / mill / foundry + era extensions
- Second settlement + caravans
- Night pressure loop
- All behind `PATH1_ENABLED` (default **`True`** after sprint — user may flip off)

No follow-on phased doc. Fixes after FAIL are **hot-fixes in the same sprint
window**, not new phases.

---

*Document version: 2026-07-11 (subagent sprint). Replaces phased H–O schedule.*
