# Plan: Remaining Work — Civilization Emergence Project

_Companion to [HANDOFF.md](HANDOFF.md) (the point-in-time state snapshot)
and [civilization-emergence-plan.md](civilization-emergence-plan.md) (the
master plan this document extends). Originally written 2026-07-08;
last updated 2026-07-09 after cemetery-layout and viewer UX work._

## Context

The original version of this document (see git history if you want the
blow-by-blow) tracked a stuck invention council, three soak-confirmation
items (Phase C/E/F), and Phase G as the last unimplemented phase. All of
that is now resolved:

- The stuck council self-resolved via its own TTL; organic council
  verdicts are now routine (Mine Cart and others approved).
- Phase C's recovery arc, Phase E's market, and Phase F's lifecycle have
  all been confirmed via real multi-hour organic soaks (see
  `.claude/overnight-cycle.json`, cycle 6.evening/7.morning entries).
- Phase G's culture half (`CULTURE_ENABLED`) landed (`4889c09`) and is
  confirmed organically working.

Several things happened that the original plan didn't anticipate:

1. **Phase G's diplomacy half was never implemented.** The original plan
   (and a decision recorded in this doc's first version) said to implement
   `CULTURE_ENABLED` and `DIPLOMACY_ENABLED` together in one pass. In
   practice, the automated cycle's batch only implemented culture and
   explicitly deferred diplomacy as a separate item (see
   `overnight-cycle.json`'s `still_open` list). It remains the only
   unimplemented net-new phase.
2. **Interactive-session bugs and features** outside the original A-G phase
   structure:
   - A "zombie" bug (`6e930ca`): `heal_agent` could resurrect a permanently
     dead agent's `incapacitated` flag without clearing `deathFrame`.
   - A 10x-too-fast aging-rate bug (`8902465`) that caused a compressed
     die-off (8 of 12 agents within ~30 minutes of a fresh world).
   - Cemetery/burial (`fc04070`, `CEMETERY_ENABLED`) — seed chapel,
     `bury_agent`, deterministic backstop.
   - **Cemetery layout + viewer polish (uncommitted as of 2026-07-09):**
     `cemetery_grounds` district with structure-style `grave_grid` (fixes
     clustered/wrapping tombstones), tombstones only after burial, Agents
     sidebar deceased modal, page-load stall fix, resource dots on
     hover/select only. See HANDOFF.md section 4 item 7.
3. **Manual cycle skills (`879982f`)** at
   `.cursor/skills/civilization-cycle-{morning,night}/` — same Part 8
   procedure as the Claude Code scheduled tasks, runnable on demand in
   Cursor when scheduled-task tokens are exhausted.

Everything below is organized the way this project already organizes
itself: feature-flagged phases, the Part 7 subagent relay pattern, and
Part 5's civilization-test-driven audits.

---

## Tier A — Resolved (historical record)

All of the following are DONE and confirmed; no action needed unless a
regression is observed. Kept here as a compact record rather than the
original multi-page blow-by-blow (still in git history at commit
`33a309a..879982f`'s docs changes if the full detail is ever needed).

| Item | Status | Evidence |
|---|---|---|
| Invention council TTL/stuck-debate bug | Self-resolved via existing TTL logic; separately, a reserved-structure-id blindspot causing 82% of proposal rejections was found and hot-fixed (`00df296`) | `overnight-cycle.json` cycle 7.morning |
| Phase C recovery arc | Confirmed: 0 ruins across a 6h/~4x-structure-growth soak, decay curve working as tuned | `overnight-cycle.json` cycle 7.morning |
| Phase D organic council resolution | Confirmed: comparative elder judgments now fire routinely (Mine Cart approved, etc.) after the id-collision fix | `overnight-cycle.json`, live `councilLog` |
| Phase E market exercise | Confirmed: Market built, `wealth_gini` falling (0.772→0.503) | `overnight-cycle.json` cycle 7.morning |
| Phase F lifecycle soak | Confirmed: natural deaths + succession clean under the corrected aging rate, zombie-fix regression explicitly re-checked and closed | `overnight-cycle.json` cycle 6.evening/7.morning |
| Phase G culture | Landed (`4889c09`) and confirmed: skills-by-practice, Library knowledge persistence, chronicle all firing organically | `overnight-cycle.json` cycle 7.morning |
| Zombie-heal bug | Fixed (`6e930ca`), verified live, data-patched | Interactive session |
| Aging-rate bug | Fixed (`8902465`), verified live, data-patched | Interactive session |
| Cemetery/burial (initial) | Implemented (`fc04070`), verified live end-to-end | Interactive session |
| Council panel GUI position | Moved to left column (`be47a60`), verified | Interactive session |
| Newcomer sprite fallback | Fixed (`16224ee`), verified via pixel inspection | Interactive session |
| Manual Part 8 cycle skills | Added (`879982f`) | `.cursor/skills/civilization-cycle-*/` |

---

## Tier A½ — Pending commit (viewer + cemetery layout, 2026-07-09)

Implemented in the working tree but **not yet committed** as of this
snapshot. Commit + server restart should be the next housekeeping step
before the next cycle audit.

| Item | Files | What it does |
|---|---|---|
| Cemetery district + grave grid | `sim_engine.py`, `sprites.js` | `cemetery_grounds` starter district; graves on structure-style grid; `restore_state` migration; disrepaired chapel still buries |
| Tombstone rendering gate | `sprites.js`, `index.html` | Tombstone sprite only when `buried`; unburied dead stay greyed at death site |
| Agents sidebar UX | `index.html` | Living list only; Deceased modal; `dead` vs `collapsed` labels |
| Page-load stall | `index.html` | `requestIdleCallback` terrain cache + loading overlay |
| Resource dot clutter | `index.html` | Dots on canvas hover or sidebar agent select only |

**Verification after commit + restart:**
- Pan west from village to fenced **CEMETERY** district; chapel at top,
  tombstones in spaced rows (no overlapping stacks).
- `deceased` count equals `buried` count (or buried trails by one grace window).
- Refresh viewer: brief loading overlay, no multi-second freeze.
- Hover/select an agent: resource dots appear; deselect/hover away: hidden.

---

## Tier B — Phase G diplomacy (the only unimplemented net-new phase)

`DIPLOMACY_ENABLED` was never built. Per the original phase-G scope: a
second settlement, inter-village trade caravans, and treaty/rivalry state.
Unlike Phases C-G, **no pre-staged recon prompt exists** for this
specifically (the phase-G.md prompt covered both culture and diplomacy,
but only culture got implemented from it) — treat this as needing fresh
recon, not a ready-to-consume prompt.

**Process** (follow this project's own established pattern):
1. Recon (read-only): re-read `.cursor/phase-prompts/phase-G.md`'s
   diplomacy section for the original scope/change-map intent, then verify
   it's still accurate against current code — Phase G's culture landing
   and this session's Cemetery feature both touched adjacent territory
   (`_maybe_welcome_newcomer`, structure/station patterns) since it was
   written.
2. Implementation subagent builds behind the flag, with the standing
   invariants this project has held for every phase: no silent
   rejections, every gate has a deterministic escape, prompt token growth
   stays modest, no new per-tick LLM calls (event-driven only — settlement
   naming, treaty proposals), `state.json` back-compat via `setdefault`,
   observability (events + benchmarks) shipped in the same commit.
3. **Mandatory forced smoke test before commit**: force the settlement
   threshold and verify founding + a caravan round trip; force a treaty
   proposal/acceptance and verify the state transition; verify prompt-token
   growth stays in budget.
4. Review pass, then hand to the automated cycle for its first real soak
   + audit.

**Recommend scheduling this via the normal Part 8 cycle** (scheduled Claude
Code task or manual `.cursor/skills/civilization-cycle-*/` skill) rather
than another ad-hoc interactive session — it's large enough to want the
full recon → implement → smoke-test → review → soak → audit relay.

**Files:** `.cursor/phase-prompts/phase-G.md` (diplomacy section),
`simulation/sim_engine.py`, `simulation/server.py`,
`civilization-emergence-plan.md` Part 4 (implementation log entry).

---

## Tier C — Active soak watch items (automated cycle handles these)

These need more soak time, not new code, unless an audit surfaces a real
defect. All observation-only.

- **Phase C structure-condition long-term trend.** Average condition
  drifted 95.3→69.7 as structure count nearly quadrupled last cycle (0
  ruins throughout — not currently a problem). Watch that it stabilizes
  rather than trending toward the 30 disrepair floor as the village keeps
  growing.
- **Priced trade (Phase E) is still thin** — only one buy/sell pair
  observed in a 6h soak. Not a defect (market mechanics are confirmed
  correct), just low organic uptake so far.
- **Teaching and meme mutation (Phase G) remain organically unexercised**
  (`teach_count` and mutation counts still at/near 0). Both are correctly
  gated by keyword-match/low-probability triggers, not confirmed defects
  — watch for natural occurrence over more soak time.
- **Cemetery/burial — post-layout soak.** Initial feature (`fc04070`)
  verified over a short window; live world now 17/17 deceased-and-buried.
  The 2026-07-09 layout work fixes grave-slot wraparound (duplicate
  tombstone coordinates past 12 burials) and moves all graves into
  `cemetery_grounds`. After commit + server restart, confirm: (a) no
  duplicate `(x,y)` among buried agents, (b) new deaths land in the
  district grid, (c) a disrepaired chapel (`condition` below 30) still
  accepts burials, (d) unburied dead show as greyed bodies (not
  tombstones) until the backstop/`bury_agent` runs.

**Files:** none directly (observation-only); `civilization-emergence-plan.md`
Part 4/5 gets the audit-log entries as each is confirmed.

---

## Tier D — Final acceptance (terminal, gated on Tier B)

Once diplomacy (Tier B) lands and Tier C's watch items close out, run the
all-flags-on long soak this project has been building toward. Judge it by
Part 5's own definition — **not** an arbitrary fixed duration: two
consecutive audits where the logged events genuinely weren't anticipated
by the plan (a quota rule nobody scripted, a famine narrative, a faction
schism, a diplomatic incident). If that soak passes every mechanical
civilization test but produces zero such surprise, the honest conclusion
(per this project's own Part 6 model-strategy reasoning) is that the
remaining gap is model capability, not world design — worth revisiting the
model choice at that point, not adding more phases.

---

## Dependency summary

```
Tier A (resolved) ─────────────────────────────┐
Tier A½ (commit cemetery/viewer, restart) ───┤
                                             │
Tier B (diplomacy, the only unimplemented phase)┼─→ Tier D (final acceptance)
                                             │
Tier C (soak watch items, parallel, no deps) ─┘
```

---

## Verification

- **Tier A½**: commit the three modified files, restart server, spot-check
  cemetery district + viewer behaviors listed in that tier's table.
- **Tier B**: the mandatory forced smoke test IS the verification gate
  before commit (see Tier B process step 3); after landing, the next
  cycle's audit against the diplomacy civilization test is the real-world
  verification.
- **Tier C**: read `civilization-emergence-plan.md` Part 4 after each
  cycle audit for updated verdicts; no manual verification needed beyond
  reading the cycle's own audit log, except for the Cemetery watch item
  which can also be spot-checked directly:

  ```bash
  # Deceased vs buried counts (should match, or buried trails by ≤1 grace window)
  curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); a=d['agents']; print('deceased', sum(x.get('deceased') for x in a), 'buried', sum(x.get('buried') for x in a))"

  # Duplicate grave positions (should print 0 duplicates after layout migration)
  curl -s http://127.0.0.1:5001/state | python -c "import json,sys; from collections import Counter; a=[(x['x'],x['y']) for x in json.load(sys.stdin)['agents'] if x.get('buried')]; c=Counter(a); print('dupes', sum(v-1 for v in c.values() if v>1))"
  ```
- **Tier D**: read two consecutive `civilization-emergence-plan.md`
  Part 5 audit entries and confirm they independently report unanticipated
  emergent events, not just mechanical PASS verdicts.
