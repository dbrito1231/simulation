# Plan: Remaining Work — Civilization Emergence Project

_Companion to [HANDOFF.md](HANDOFF.md) (the point-in-time state snapshot)
and [civilization-emergence-plan.md](civilization-emergence-plan.md) (the
master plan this document extends). Originally written 2026-07-08;
rewritten 2026-07-08 (later same day) after the plan's entire original
scope (Tiers 0-3 below) resolved — mostly via the automated overnight
cycle, one bug via an interactive session._

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

Two things happened that the original plan didn't anticipate:

1. **Phase G's diplomacy half was never implemented.** The original plan
   (and a decision recorded in this doc's first version) said to implement
   `CULTURE_ENABLED` and `DIPLOMACY_ENABLED` together in one pass. In
   practice, the automated cycle's batch only implemented culture and
   explicitly deferred diplomacy as a separate item (see
   `overnight-cycle.json`'s `still_open` list). It remains the only
   unimplemented net-new phase.
2. **Two real bugs and one new feature emerged from interactive sessions**,
   outside the original A-G phase structure entirely:
   - A "zombie" bug (`6e930ca`): `heal_agent` could resurrect a permanently
     dead agent's `incapacitated` flag without clearing `deathFrame`,
     letting corpses move/think/get tasked.
   - A 10x-too-fast aging-rate bug (`8902465`) that caused a compressed
     die-off (8 of 12 agents within ~30 minutes of a fresh world).
   - A user-requested Cemetery/burial feature (`fc04070`,
     `CEMETERY_ENABLED`) so permanently-dead agents are built a cemetery,
     buried by the village, and rendered as a tombstone instead of lying
     wherever they fell.

Everything below is organized the way this project already organizes
itself: feature-flagged phases, the Part 7 subagent relay pattern, and
Part 5's civilization-test-driven audits.

---

## Tier A — Resolved (historical record)

All of the following are DONE and confirmed; no action needed unless a
regression is observed. Kept here as a compact record rather than the
original multi-page blow-by-blow (still in git history at commit
`33a309a..fc04070`'s docs changes if the full detail is ever needed).

| Item | Status | Evidence |
|---|---|---|
| Invention council TTL/stuck-debate bug | Self-resolved via existing TTL logic; separately, a reserved-structure-id blindspot causing 82% of proposal rejections was found and hot-fixed (`00df296`) | `overnight-cycle.json` cycle 7.morning |
| Phase C recovery arc | Confirmed: 0 ruins across a 6h/~4x-structure-growth soak, decay curve working as tuned | `overnight-cycle.json` cycle 7.morning |
| Phase D organic council resolution | Confirmed: comparative elder judgments now fire routinely (Mine Cart approved, etc.) after the id-collision fix | `overnight-cycle.json`, live `councilLog` |
| Phase E market exercise | Confirmed: Market built, `wealth_gini` falling (0.772→0.503) | `overnight-cycle.json` cycle 7.morning |
| Phase F lifecycle soak | Confirmed: natural deaths + succession clean under the corrected aging rate, zombie-fix regression explicitly re-checked and closed | `overnight-cycle.json` cycle 6.evening/7.morning |
| Phase G culture | Landed (`4889c09`) and confirmed: skills-by-practice, Library knowledge persistence, chronicle all firing organically | `overnight-cycle.json` cycle 7.morning |
| Zombie-heal bug | Fixed (`6e930ca`), verified live, data-patched | This session |
| Aging-rate bug | Fixed (`8902465`), verified live, data-patched | This session |
| Cemetery/burial feature | Implemented (`fc04070`), verified live end-to-end within a ~15 min window | This session |
| Council panel GUI position | Moved to left column (`be47a60`), verified | This session |
| Newcomer sprite fallback | Fixed (`16224ee`), verified via pixel inspection | This session |

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

**Recommend scheduling this via the normal Part 8 cycle** rather than
another ad-hoc interactive session — it's large enough to want the full
recon → implement → smoke-test → review → soak → audit relay.

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
- **Cemetery/burial (this session's feature) needs a longer soak.**
  Verified end-to-end over a ~15-minute window (one cemetery built, 5
  then-dead agents buried); by the time this doc was rewritten the same
  session, the live world already had 12/12 deceased-and-buried with no
  incident, which is a good early sign, but a multi-hour cycle audit
  should still confirm no edge case (e.g., cemetery destroyed/ruined
  mid-soak, grave-slot wraparound past 12 graves) surfaces.

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
                                                 │
Tier B (diplomacy, the only unimplemented phase)┼─→ Tier D (final acceptance)
                                                 │
Tier C (soak watch items, parallel, no deps) ───┘
```

---

## Verification

- **Tier B**: the mandatory forced smoke test IS the verification gate
  before commit (see Tier B process step 3); after landing, the next
  cycle's audit against the diplomacy civilization test is the real-world
  verification.
- **Tier C**: read `civilization-emergence-plan.md` Part 4 after each
  cycle audit for updated verdicts; no manual verification needed beyond
  reading the cycle's own audit log, except for the Cemetery watch item
  which can also be spot-checked directly: `curl -s
  http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin);
  a=d['agents']; print(sum(x.get('deceased') for x in a), sum(x.get('buried') for x in a))"`
  — the two counts should stay equal (or buried trailing deceased by at
  most one grace window's worth).
- **Tier D**: read two consecutive `civilization-emergence-plan.md`
  Part 5 audit entries and confirm they independently report unanticipated
  emergent events, not just mechanical PASS verdicts.
