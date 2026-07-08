# Plan: Remaining Work — Civilization Emergence Project

_Companion to [HANDOFF.md](HANDOFF.md) (the point-in-time state snapshot)
and [civilization-emergence-plan.md](civilization-emergence-plan.md) (the
master plan this document extends). Written 2026-07-08._

## Context

`HANDOFF.md` (committed `33a309a`) already captures a full snapshot of
this project as of 2026-07-08T00:35:00Z: git/server/LM Studio state, world
state, per-phase status (A–G), the automated twice-daily overnight cycle,
and a ranked open-items list. Since that snapshot, the world has continued
running (no code changes), and one important thing has been discovered
live: **a new, currently-active regression in the invention council** that
sits on top of tonight's three already-fixed bugs. This plan supersedes
HANDOFF.md's open-items list with a fuller, dependency-ordered picture of
everything left before this project reaches its own stated finish line
(Part 5 of `civilization-emergence-plan.md`: two consecutive audits
finding events the plan didn't anticipate).

Two decisions were made with the user before finalizing this plan:
- The `civilization-cycle-night` schedule has drifted from ~9:38 PM to
  ~1:08 AM with no known cause. **Decision: adopt 1:08 AM as the real
  schedule** — don't fight the drift, just document it as ground truth.
- Phase G's two sub-flags (`CULTURE_ENABLED`, `DIPLOMACY_ENABLED`).
  **Decision: implement both together in one pass**, not culture-first.

Everything below is organized the way this project already organizes
itself: feature-flagged phases, the Part 7 subagent relay pattern, and
Part 5's civilization-test-driven audits — not a new process.

---

## Tier 0 — RESOLVED (2026-07-08 follow-up session)

The council the was "stuck" at snapshot time (see original finding below)
had actually already self-resolved via TTL by the time this follow-up
session checked (`councilLog[0]` showed a clean "dissolved without a
verdict" at frame 949050). **Tier 2 is also now confirmed**: `councilLog`
contains two genuine organic resolutions with verdicts — Fishery approved
(frame 781616) and Water Pump approved (frame 854792).

While checking the then-current council (proposers Ivy, Luna, Mia), found
a **real, separate, currently-live bug**: Ivy and Mia were both long-dead
(`deathFrame` set) but showed `incapacitated: False` — a "zombie" state.
Root cause: `heal_agent` (`simulation/sim_engine.py`, was ~line 5444) never
checked `deathFrame` before reviving, and `_neediest_nearby` (~line 1435)
sorted ALL `incapacitated` agents first regardless of death — since a
corpse's `incapacitated` stays `True` forever unless revived, healers
always targeted the nearest corpse over any genuinely injured living
agent, and would eventually "revive" it (`incapacitated -> False`) while
leaving `deathFrame` permanently set. Movement/think dispatch in the tick
loop check `incapacitated` only, not `deathFrame`, so a zombie-revived
corpse would resume moving, thinking, and — as observed — getting flagged
for invention-council turns it could never meaningfully complete. On
inspection, **all 8 dead agents in the live world** had already been
corrupted this way (not just the 2 in the active council), implying this
had been happening repeatedly for a while.

**Fixed and verified live** (commit pending / see git log after this
session): `heal_agent` now refuses a `deathFrame`-set target;
`_neediest_nearby` now excludes them from its candidate pool;
`_idle_agents_for_elder` now filters `deathFrame` directly as defense in
depth (not just `incapacitated`, which the bug proved could be wrong).
The live `state.json` was one-time data-patched to set `incapacitated:
True` on all 8 already-corrupted dead agents (had to stop the server
first — the still-running old process's autosave clobbered the first
attempt at this patch). Verified post-restart: all 8 dead agents show
`incapacitated: True` and stay that way; a brand-new council formed
immediately after with 3 correctly-living proposers (Luna, Dex, Finn), no
zombies. This bug and its blast radius (ambulatory, thinking corpses) were
NOT previously known/documented anywhere in the plan docs.

<details>
<summary>Original Tier 0 finding (superseded, kept for history)</summary>

## Tier 0 — Stuck invention council (interactive, top priority, blocks Tier 2)

**Finding (verified live, read-only, this session):** a council
(proposers Ivy, Aria, Zara; trigger `invention_backstop`) has been
`councilActive.active: true` with `proposals: 0` for 30,000+ engine frames.
`COUNCIL_TTL_FRAMES` is 12,000 (~6.7 min) — this should have auto-dissolved
via `_maybe_dissolve_council` (`simulation/sim_engine.py` ~4687, gated only
by `TECH_TREE_ENABLED` and a 150-frame tick, so it's had ~200+ chances to
fire) long ago. It hasn't. `councilLog[0]` is still the *previous* debate
(frame 711300, dissolved without a verdict) — this one has neither
resolved nor dissolved.

**Evidence gathered this session (rules out one hypothesis, narrows the
rest):**
- All three proposers are alive, healthy, not incapacitated
  (`health: 100`, `deathFrame: None`) — the "Phase F mortality broke a
  Phase D assumption" hypothesis is **ruled out** for this specific
  occurrence (though the underlying gap — `_agent_dies` never clears a
  dead agent's `inventionTurn` flag — is still real and worth fixing
  defensively; see below).
- Each proposer's `lastAction` is a normal, non-invention action
  (`repair_structure`, `contribute_resources`, `collect_resource`) —
  consistent with each having already spent their one-shot invention turn.
- **Confirmed via `lm_studio.jsonl`**: Ivy and Zara each received exactly
  one `invention_only` dispatch (the scheduling-starvation fix from
  tonight is working — nobody was starved this time). **Aria received
  two** — an anomaly worth investigating specifically: either she was
  independently re-flagged by a second `_maybe_invention_backstop` firing
  (which should be blocked while `councilActive` is truthy — check that
  guard for a race/edge case) or flagged by two genuinely separate debates
  that got conflated.
- All three attempts failed to produce an accepted proposal (0 recorded).

**Root-cause candidates, ranked by likelihood:**
1. An uncaught exception is silently killing `_maybe_dissolve_council`
   (or the code path that calls it) on every tick for this specific
   council — e.g. a `KeyError`/`TypeError` reading `council.get("frame")`
   if that key is malformed or missing on this instance. Check whether the
   surrounding tick-gate loop swallows exceptions silently anywhere nearby.
2. `councilActive["frame"]` is stale/wrong for this specific debate (was
   it created with the right frame? does anything ever mutate `frame` on
   an existing `councilActive` after creation, e.g. Aria's double-flagging
   above?).
3. A structural gap where "all flagged members already used their one
   shot and nothing landed" doesn't map to either a resolved or a dissolve
   path — i.e., the debate is logically "over" but nothing declares it so
   short of the pure frame-based TTL, and something is preventing that TTL
   check from ever being true.

**Recommended fix approach (implement in the next available interactive
or cycle session — don't wait for a scheduled slot given this blocks
Tier 2):**
1. Diagnose first (cheap, mostly read-only): read `_maybe_dissolve_council`
   and its caller in the tick loop line-by-line against this specific
   council's actual persisted state (dump `councilActive` from a paused
   moment or from `state.json` directly, not just `/state`'s whitelisted
   summary — note `/state`'s `councilActive` serialization only exposes
   `{active, trigger, proposers, proposals}`, NOT `frame`, so this
   diagnostic needs `state.json` or a debug log line, not the client API).
2. Fix the actual defect found (likely a one-line correction once found,
   per the exception/stale-key hypotheses above).
3. **Defensive hardening in the same pass** (small, cheap, prevents a
   whole class of future stuck-council bugs): clear `inventionTurn` in
   the death/collapse path (`_agent_dies` and wherever incapacitation is
   set) so a flagged member who becomes unavailable mid-debate can't
   silently strand a slot; and make sure `_maybe_invention_backstop`'s
   "already deliberating" guard is airtight against Aria's double-dispatch
   anomaly.
4. **Immediate mitigation**: once the fix is verified, force-resolve the
   currently-stuck council (either let the fixed dissolve logic clear it
   naturally, or manually clear `councilActive` if the fix doesn't
   retroactively apply to already-corrupted state).
5. Smoke-test per the project's own standing invariant: force a new
   council, force all members to fail, confirm it dissolves on schedule
   this time.

**Files:** `simulation/sim_engine.py` (`_maybe_dissolve_council`,
`_maybe_invention_backstop`, `_agent_dies`, the tick loop's council-related
gates), `simulation/state.json` (for direct inspection if needed).

</details>

---

## Tier 1 — Parallel observation/soak items (automated cycle handles these)

These need soak time, not new code, unless an audit surfaces a real
defect. All three can run concurrently since the world runs 24/7 regardless.

- **Phase C recovery-arc trend confirmation.** Still "provisional PASS" —
  ruins have been healing slowly (repair_structure firing, trend
  confirmed directionally correct) but needs one longer uninterrupted soak
  to fully confirm the trend before Phase C can be marked definitively
  closed. No code change expected; a cycle audit re-checks the
  `structure_condition` benchmark trend.
- **Phase E market exercise — RESOLVED.** As of 2026-07-08 follow-up
  check, 3 `market` structures now exist in the live world (0 at the
  original snapshot) — economy mechanics (pricing, priced trade, property)
  are now organically exercised. No further action needed; a cycle audit
  should confirm price/trade behavior looks sane the next time it runs.
- **Phase F multi-hour soak confirmation.** Lifecycle mechanics (aging,
  birth, death, succession) have only been smoke-tested by their
  implementer on a scratch copy of state.json — never observed in a real
  live soak. Watch the next several cycle audits for actual births/deaths/
  elections in the wild. **New standing audit question to add to Part 5**
  as a direct consequence of the Tier 0 finding: *"did any agent
  die/collapse while flagged for something else (invention turn, task
  assignment, etc.), and did the system handle that gracefully?"* — this
  generalizes the specific defensive fix in Tier 0 into an ongoing check.

**Files:** none directly (observation-only); `civilization-emergence-plan.md`
Part 4/5 gets the audit-log entries and the new standing question.

---

## Tier 2 — Phase D organic resolution (blocked on Tier 0)

Once Tier 0 lands, watch the next council that forms all the way through
to either a genuine resolved verdict (a proposal gets approved and built)
or a clean, on-schedule dissolve. This is the one remaining unconfirmed
piece of Phase D's core mechanics — everything else (tiers, eras, sprites,
few-shot examples) has already passed forced smoke tests.

---

## Tier 3 — Phase G: culture, knowledge & diplomacy (the only net-new implementation)

The last unimplemented phase. Prompt is pre-staged with a recon-grade
change map at `.cursor/phase-prompts/phase-G.md` — per user decision,
implement **both `CULTURE_ENABLED` and `DIPLOMACY_ENABLED` together in one
pass**, not culture-first. Scope per the pre-staged prompt: skills-by-
practice + teaching, a library structure preserving knowledge past death,
an event chronicle + meme mutation, personality drift from major life
events (culture); a second settlement, inter-village trade caravans, and
treaty/rivalry state (diplomacy).

**Process:** follow this project's own established pattern —
1. Recon (read-only) confirms the phase-G.md change map is still accurate
   against current code (Phase F landed since it was written; re-verify
   the `_maybe_welcome_newcomer`/election/inheritance hooks it plans to
   reuse still look the way it assumed).
2. Implementation subagent builds behind both flags, with the standing
   invariants: no silent rejections, every gate has a deterministic
   escape, ≤200 prompt tokens added total across both flags, no new
   per-tick LLM calls (only event-driven: meme mutation, settlement
   naming), state.json back-compat via `setdefault`, observability
   (events + benchmarks) shipped in the same commit.
3. **Mandatory forced smoke test before commit** (per the project's own
   hard-won lesson from Phase C/D's earlier loop-backs): practice a skill
   and verify the bonus; teach between two agents; kill a skilled agent
   with a library built and verify knowledge survives; force the
   settlement threshold and verify founding + a caravan round trip; verify
   real prompt-token growth stays in budget.
4. Review pass against the two standing invariants, then hand to the
   automated cycle for its first real soak + audit.

**Recommend scheduling this via the normal Part 8 cycle** (not another
ad-hoc interactive session) once Tier 0's fix is confirmed stable — Phase
G is large enough to want the full recon → implement → smoke-test → review
→ soak → audit relay, not a rushed interactive pass.

**Files:** `.cursor/phase-prompts/phase-G.md` (source of scope),
`simulation/sim_engine.py`, `simulation/server.py`, `civilization-emergence-plan.md`
Part 4 (implementation log entry).

---

## Cross-cutting items

- **Schedule drift — RESOLVED BY DECISION.** Adopt 1:08 AM as the real
  `civilization-cycle-night` schedule (no cron change). Update
  `civilization-emergence-plan.md` Part 8's timing table (currently
  says "~21:30/~07:30") to state the actual live cron times, and correct
  the memory-index note that still claims 21:38. Trivial doc-only change.
- **Unexplained 2026-07-07 crash-loop** (~20 rapid restarts, no error
  evidence). Watch-only — no action unless it recurs. If it does, the next
  cycle instance should capture console output via a redirected launch to
  get a traceback (already noted in `overnight-cycle.json`'s `still_open`
  list).

**Files:** `civilization-emergence-plan.md` (Part 8 timing table),
`.claude/projects/.../memory/MEMORY.md` (schedule note correction).

---

## Tier 4 — Final acceptance (terminal, gated on everything above)

Once Tier 3 (Phase G) lands and Tiers 1–2's soak confirmations all close
out, run the all-flags-on long soak this project has been building toward.
Judge it by Part 5's own definition — **not** an arbitrary fixed duration:
two consecutive audits where the logged events genuinely weren't
anticipated by the plan (a quota rule nobody scripted, a famine narrative,
a faction schism, a diplomatic incident). If that soak passes every
mechanical civilization test but produces zero such surprise, the honest
conclusion (per this project's own Part 6 model-strategy reasoning) is
that the remaining gap is model capability, not world design — worth
revisiting the model choice at that point, not adding more phases.

---

## Dependency summary

```
Tier 0 (stuck council fix) ──┬─→ Tier 2 (Phase D organic resolution)
                              │
Tier 1 (C/E/F soak, parallel) ┘
                              │
                              ▼
Tier 3 (Phase G, both flags) ─→ Tier 4 (final acceptance)

Cross-cutting (schedule doc fix, crash-loop watch) — no dependencies, do anytime.
```

---

## Verification

- **Tier 0**: force-test the fix directly — create a council, force all
  members to fail validation, confirm `_maybe_dissolve_council` fires and
  logs a dissolve entry within `COUNCIL_TTL_FRAMES` of the debate starting;
  separately, confirm the currently-stuck live council resolves (either
  naturally via the fix, or via manual clear) and a new one can complete
  the full lifecycle end-to-end (form → proposals → elder verdict →
  `councilLog` entry with a real outcome).
- **Tier 1**: read `civilization-emergence-plan.md` Part 4 after each
  cycle audit for updated verdicts on Phase C/E/F; no manual verification
  needed beyond reading the cycle's own audit log.
- **Tier 3**: the mandatory forced smoke test IS the verification gate
  before commit (see Tier 3 process step 3); after landing, the next
  cycle's audit against Phase G's civilization test (from `phase-G.md`)
  is the real-world verification.
- **Cross-cutting**: `git diff` on the plan doc/memory file confirms the
  doc-only schedule correction landed; no runtime verification needed.
- **Tier 4**: read two consecutive `civilization-emergence-plan.md`
  Part 5 audit entries and confirm they independently report unanticipated
  emergent events, not just mechanical PASS verdicts.
