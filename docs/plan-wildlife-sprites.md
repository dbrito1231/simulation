# Plan — Wildlife sprite legibility (Tiny Farm art direction)

**Status:** done / shipped on `feature/wildlife-sprites` (PR #5).
**Owner spec:** [specs/11-viewer.md](../specs/11-viewer.md) (wildlife rendering, render pipeline). Shipped: kind renames (`grazer`→`cow`, `butterfly`→`bee`), `/wildlife.png` + `/wildlife_refsheet.html`, save migration in `_normalize_wildlife_records`.
**Delivery:** new branch in a new git worktree based on `feature/god-mode` → push → PR into `feature/god-mode` for user review (see [Branch, worktree, and delivery](#branch-worktree-and-delivery)).
**Goal:** make each of the 16 wildlife kinds identifiable at a glance in the viewer, without breaking the load-performance win or the server-authoritative fauna contract.

**Reported problem (user):** *"the animals show … those sprites are small and I can't make sense of what each animal is."*

---

## Diagnosis

### The sprites in question are uncommitted, brand-new work

`simulation/sprites.js` carries a ~564-line **uncommitted** diff that is itself a wildlife rewrite. Committed `HEAD` drew fauna with per-kind canvas-primitive functions (`drawFishRipple`, `drawBird`, `drawGrazer`, `drawSquirrel`, `drawDeer`, `drawBoar`, `drawButterfly`, …). The working tree replaced all of them with a static pixel-grid table, `WILDLIFE_SPRITES` + `WILDLIFE_SCALE = 2` ([sprites.js:1919](../simulation/sprites.js:1919)), dispatched through a single `drawWildlifeCreature` ([sprites.js:2090](../simulation/sprites.js:2090)), and went 15 → 16 kinds.

**Any plan here must treat the working tree, not `HEAD`, as the baseline.**

### Cause 1 — the rewrite silently dropped all per-kind animation

The committed versions animated off `frameTick`: fish wobble, bird wing-flap, squirrel tail-flick, butterfly flap. The replacement table is explicitly static — the file's own comment notes only the caller-side cosmetic `bob` in `drawWildlife` ([index.html:3636](../simulation/index.html:3636)) remains.

Motion is one of the strongest species-identification cues available at this pixel budget. A flapping butterfly and a wiggling fish were previously self-identifying. **This capability regressed and is cheap to restore.**

### Cause 2 — wildlife is drawn at half agent size

| | grid | scale | on-screen |
|---|---|---|---|
| Agent sprites ([sprites.js:1645](../simulation/sprites.js:1645)) | 16 wide | 2 | **32 px** |
| Wildlife ([sprites.js:1919](../simulation/sprites.js:1919)) | 8–9 wide | 2 | **16–18 px** |

Confirms the "small" half of the complaint numerically.

### Cause 3 — silhouettes and palettes collide

`deer`, `fox`, `boar`, `squirrel`, and `mouse` are all mid-size brown blobs differing mainly in hue, at a size where hue reads before shape does. Silhouette must carry identification, not colour.

### Constraint — the load-performance plan banks on zero image loading

[plan-load-performance.md](plan-load-performance.md) states the viewer's sprite art is *"fully procedural with zero image/spritesheet network loading"* and relies on that to draw agents before the terrain cache exists. Introducing a spritesheet puts a fetch near the critical path, so **a procedural fall-through is mandatory, not optional.**

### The purity objection has already evaporated

The same uncommitted diff introduced a module-level mutable cache and offscreen canvases into `sprites.js` (`_tileSourceCanvasCache`, `createTileSourceCanvas`, `getTileSourceCanvas`) as Phase 1 of [plan-load-performance.md](plan-load-performance.md). The file's former "pure, stateless / no mutable module state" convention has already been deliberately relaxed, so an image cache is now *consistent with* the file's direction rather than a violation of it.

### Existing spec drift (fix regardless of this plan)

[specs/11-viewer.md](../specs/11-viewer.md) still documents *"wing-flap, fin-wiggle … `drawWildlifeCreature` dispatching per-kind helpers"* — untrue after the uncommitted rewrite. An SDD violation currently sitting uncommitted.

---

## Art source: Tiny Farm

[Kenney Tiny Farm](https://kenney.nl/assets/tiny-farm) — CC0 1.0, 130 files, **16 × 16** tiles, top-down pixel-art RPG idiom.

**Verified contents (inspected at 4× zoom): only three animals — sheep, cow, chicken.** The remaining files are soil/crop tiles, produce icons, barn and building tiles, crates, and two human farmer sprites.

Therefore Tiny Farm is **a style bible plus a direct source for 2–3 of 16 kinds**, not a drop-in replacement. Its value:

- **16 × 16** matches the repo's `TILE = 16` constant exactly, and 16×16-source blitting infrastructure now already exists.
- Heavy near-black outline with 3–4 interior shade steps — markedly more readable than the current flat fill + single outline.
- ¾ side view with visible head and legs; its sheep/cow/chicken read instantly, which is precisely the failure being fixed.

Alternative considered and rejected: **Animal Pack Remastered** ([kenney.nl](https://kenney.nl/assets/animal-pack-remastered), CC0, 240 files) has real species breadth — ~30 animals × 8 colour styles — but is styled as front-facing flat mascot-face icons. It reads as avatar iconography, not creatures moving through a top-down world. Possible future use: hover tooltips or hunt-log icons.

---

## Branch, worktree, and delivery

All work happens **outside the primary working directory**, in a dedicated worktree, and lands via PR.

- **Base:** `feature/god-mode` (current branch).
- **New branch:** `feature/wildlife-sprites`.
- **New worktree:** created from that base, outside `C:\Users\dbadmin\Desktop\GitServ\simulation` so the primary tree is untouched.
- **On completion:** push `feature/wildlife-sprites`, then open a PR **targeting `feature/god-mode`** (not `main`). The user reviews and takes the PR from there — do not merge.

### Carrying the dirty `sprites.js` across (do this carefully)

A new worktree is a **clean checkout of the base commit**; uncommitted changes in the primary tree do *not* appear in it. The ~564-line `sprites.js` diff must be moved deliberately, and it must be **committed in the new worktree**, not in the primary tree.

Complications to handle rather than assume away:

- The primary tree is dirty in **three** tracked files: `simulation/sprites.js` (wanted) plus `specs/00-overview.md` and `specs/03-cognition.md` (**unrelated cognition edits — must not be carried over**). Move `sprites.js` only.
- Untracked files also live only in the primary tree, including **this plan document**. Carry `docs/plan-wildlife-sprites.md` into the worktree so it lands with the PR; leave `simulation/backup/` (local `state.db` backups) behind entirely.
- Preferred mechanism: a path-scoped patch (`git diff -- simulation/sprites.js` → `git apply` in the worktree), which avoids the shared-stash ambiguity of a multi-worktree `git stash`. Verify the applied diff is byte-identical to the source before committing.
- After the transfer, decide explicitly what the primary tree keeps. The intent is that the `sprites.js` work leaves the primary tree and lives on the new branch — confirm with the user before reverting anything there, since discarding it in the primary tree is not easily reversible.

### Server-instance hazard (worktree-specific)

A worktree is a second copy of the repo, so it has its **own** `simulation/state.db` — but **not** its own port. Running `server.py` from the worktree while the primary tree's server is running means two instances contending for port **5001**, exactly the failure CLAUDE.md's single-instance rule exists to prevent.

Before any verification run from the worktree: stop the primary tree's server (close the `simserver` window), then start one from the worktree. Remember `uv run` legitimately shows **two** `python.exe` in a parent/child pair — that is one instance.

---

## Plan

Ordered by impact-per-risk. **Measure after Phase 1 before committing to Phases 2–4.**

### Phase 0 — Create the worktree and untangle the uncommitted diff

Nothing should be built on top of an unreviewed 564-line diff mixing two unrelated concerns.

- Create the `feature/wildlife-sprites` branch and worktree from `feature/god-mode`, per [Branch, worktree, and delivery](#branch-worktree-and-delivery).
- Transfer the `sprites.js` diff (and this plan doc) into the worktree; leave the unrelated `specs/` cognition edits and `simulation/backup/` behind.
- **Commit in the worktree**, split into two commits so the concerns stay reviewable in the PR:
  1. the load-perf pattern-fill work (Phase 1 of [plan-load-performance.md](plan-load-performance.md)),
  2. the wildlife grid refactor.
- Fix the stale [specs/11-viewer.md](../specs/11-viewer.md) animation/dispatch wording as part of the wildlife commit.
- **Keep the grid-table refactor.** A single dispatch point, one scale constant, and a table keyed by kind is a far better foundation for asset swapping than 15 bespoke canvas functions. Build on it; do not revert.

**Exit criteria:** worktree on `feature/wildlife-sprites`, clean tree, spec matches code, grid table retained, unrelated edits not carried.

### Phase 1 — Restore motion (cheapest real win)

- Reinstate the per-kind `frameTick` animation the rewrite dropped: butterfly wing-flap, fish fin-wiggle, squirrel tail-flick, bird flap.
- Implement as an optional second frame per kind (`{ stand, alt }`), mirroring how `drawAgentSprite` already selects `data.walk` vs `data.stand` ([sprites.js:1654](../simulation/sprites.js:1654)) — reuses an established idiom instead of inventing one.
- Purely cosmetic and caller-driven; no new position source, preserving the spec's "does not invent a second position" rule.

**Exit criteria:** screenshot/observe before continuing. If legibility is acceptable, Phases 2–4 may be unnecessary.

### Phase 2 — Size and scale

- Move wildlife to a **16 × 16** canvas with **size tiers**, so relative scale stays believable while legibility improves:
  - **large** (fills the tile): `deer`, `boar`, `grazer`, `seal`
  - **mid** (~12 px): `fox`, `owl`, `turtle`, `rabbit`, `chicken`, `gull`
  - **small** (~8 px): `mouse`, `squirrel`, `fish`, `crab`, `butterfly`
- Large fauna reach parity with the 32 px agent sprites; a mouse never becomes deer-sized.

### Phase 3 — Asset pipeline (route A — confirmed)

- Spritesheet loader: one PNG, one preload, a `ready` flag, `ctx.drawImage` with `imageSmoothingEnabled = false`; cache keyed by identity in the manner `_tileSourceCanvasCache` already establishes.
- **Mandatory** fall-through to the procedural grids while unloaded and for any kind lacking an image, so the sub-500 ms load win is never regressed and nothing ever renders blank.
- Strategic payoff: this pipeline allows *any* proper animal pack to be dropped in later — the durable fix for the 13-kind gap, rather than betting everything on hand-authored pixel art.

### Phase 4 — Sprite coverage

- **Direct from Tiny Farm:** `grazer` → sheep (the existing code comment already describes it as "sheep/goat/cow-like"), `chicken` → chicken, cow as a second `grazer` variant.
- **Redrawn in Tiny Farm's idiom** for the remaining 13: heavy near-black outline, 3–4 shade steps, ¾ side view with visible head and legs.
- **Silhouette-first.** Antlers, snout, bushy tail, ear shape, and body posture must differ before colour does. Prioritise the confusable set: `deer`, `fox`, `boar`, `squirrel`, `mouse`.
- Consistency beats per-sprite fidelity: avoid a mixed look of 3 professional sprites against 13 amateur ones.

---

## Verification

No test suite — verify by running the server and watching the browser ([CLAUDE.md](../CLAUDE.md)).

1. Restart the server in its own visible, titled `cmd` window per CLAUDE.md; never backgrounded.
2. Hard-reload `http://127.0.0.1:5001` (never `index.html` as a file).
3. Before/after screenshots of the same fauna at each zoom level; the pass condition is **naming each kind on sight without reference to code**.
4. Confirm the load-performance win did not regress: fauna must never block first paint, and the procedural fallback must be visibly exercised (e.g. by throttling or blocking the spritesheet fetch).
5. Run the deterministic smokes — `uv run python scripts/sid_parity_smoke.py`, `uv run python scripts/path1_smoke.py` — to confirm nothing server-side moved.
6. Confirm only one `simulation/server.py` instance is running before reporting done. Note `uv run` legitimately shows **two** `python.exe` in a parent/child pair — that is one instance.

**Optional accelerator:** [tasks-pending.md](tasks-pending.md) item 1 notes `wildlife_spawn` / `wildlife_set_hp` have no Divine Console form. Wiring `wildlife_spawn` first would let all 16 kinds be summoned on demand instead of waiting for natural spawns — substantially faster iteration for this plan.

## Spec obligations (SDD)

Per CLAUDE.md, specs must match the repo in the same change:

- **[specs/11-viewer.md](../specs/11-viewer.md)** — the wildlife rendering section: correct the stale per-kind-helper / wing-flap wording (Phase 0), then document the restored animation model (Phase 1), grid size and size tiers (Phase 2), and the spritesheet-plus-fallback path (Phase 3).
- **[specs/12-ops.md](../specs/12-ops.md)** — only if assets become a served static path; record art provenance and licence.
- No new actions, flags, or routes, so the action-sync invariant and flag index are untouched.

**Asset provenance to record:** pack name, source URL, CC0 1.0, retrieval date. CC0 requires no attribution, but provenance should be tracked in-repo regardless.

## Decisions

Both previously open decisions are resolved by the user:

1. **Dirty `sprites.js`** — commit it, **in the new worktree**, split into the two commits described in Phase 0. Not committed in the primary working tree.
2. **Phase 3 route** — **route A (PNG spritesheet + loader) confirmed.** Route B (transcribing all 16 kinds to `tileFromStrings` grids) is rejected: 13 of 16 sprites would be hand-authored pixel art at 16×16 with real quality risk, the purity argument that once favoured it no longer holds (see Diagnosis), and route A builds the pipeline that makes a future full art pack a drop-in.

Still to confirm during Phase 0: what the primary working tree keeps once the `sprites.js` work moves to the new branch.

## Downloads requiring approval

Tiny Farm has **not** been downloaded. Fetching it is a file download and needs explicit user approval at Phase 4 time — state pack, source URL, and size when asking.

## Delegation

Per CLAUDE.md's model policy, each phase is dispatched to the `implementer` subagent (Sonnet 5) as its own step and reviewed before the next begins. The orchestrator writes no implementation code.
