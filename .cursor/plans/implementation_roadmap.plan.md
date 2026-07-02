---
name: "Implementation Roadmap — Required Order for All Parts"
overview: "Master sequencing plan for the two implementation plans: fix_build_progression.plan.md (Parts 1–4: pipeline, elder leadership, GUI, throughput, roster) and emergence_llm_brain.plan.md (Part 5: blueprint-gated growth, goals, commitments, world expansion). Defines three passes with a mandatory validation session between passes A and B, what is deferred, and why the order is what it is."
todos:
  - id: pass-a-progression
    content: "PASS A: implement fix_build_progression Part 1 (all fixes 1.1–1.6, including elder leadership + assign_task) — read the plan's 'Implementation gotchas' section first"
    status: pending
  - id: pass-a-throughput
    content: "PASS A: implement Part 3 (bounded-concurrency LLM pool, MAX_CONCURRENT_LLM=3, fix the llmBusy reference at index.html:1335)"
    status: pending
  - id: pass-a-roster
    content: "PASS A: implement Part 4 (8-agent roster via activeDefs wired through makeAgents + AGENT_NAMES; update specs/04-agent-spec.md)"
    status: pending
  - id: pass-a-gui-minimum
    content: "PASS A: implement only the two cheap GUI items from Part 2 — zone labels (Fix 2.3) and the isThinking indicator (from Fix 2.2); defer the rest"
    status: pending
  - id: validation-session
    content: "GATE: run a full session (LM Studio + server, ~15+ min); confirm the seed loop completes repeatedly per the Parts 1–4 validation checklist; record pace observations (project duration, elder cadence, any spontaneous propose_blueprint) needed to tune Part 5"
    status: pending
  - id: pass-b-emergence
    content: "PASS B: implement emergence_llm_brain.plan.md (Part 5) — blueprint-gated progression, invention nudges, goals, commitments, world expansion — tuned with the pace data from the validation session"
    status: pending
  - id: pass-b-validate
    content: "GATE: run a long session; walk the Part 5 emergence validation checklist (custom structure invented, approved, built, village footprint grows)"
    status: pending
  - id: pass-c-gui-polish
    content: "PASS C (optional, anytime after Pass A): remaining Part 2 GUI work — depth sorting, annotation de-stacking, layout/DPI/panels, sprite variety (stretch)"
    status: pending
isProject: false
---

# Implementation Roadmap — Required Order for All Parts

Two implementation plans exist; this document sequences them:

- **[fix_build_progression.plan.md](fix_build_progression.plan.md)** — Parts 1–4: build-pipeline fix +
  all-roles building + elder leadership (Part 1), GUI improvements (Part 2), LLM queue parallelism
  (Part 3), 8-agent roster (Part 4).
- **[emergence_llm_brain.plan.md](emergence_llm_brain.plan.md)** — Part 5: making the LLM load-bearing
  (blueprint-gated growth, persistent goals, consequential conversations, world expansion).

## Why this order

Part 5 sits **on top of** the build pipeline, not beside it. The logged evidence (0/36 `start_project`
calls, zero build events) means the seed loop has never been observed completing. Building the emergence
layer on an unproven pipeline makes stalls undiagnosable — you cannot tell whether a stuck blueprint
project is an emergence bug or the same dead pipeline underneath. Throughput (Part 3) and the smaller
roster (Part 4) are prerequisites in spirit: emergence needs agents to think often, and a serial queue at
~18–30s per agent per turn starves any smart behavior. Most GUI work serves observation, not function, so
it can trail everything except the two items that make validation itself easier (zone labels, thinking
indicator).

---

## PASS A — Foundation (fix_build_progression: Parts 1, 3, 4 + minimal GUI)

Implement in this order within the pass:

1. **Part 1 — Build progression + elder leadership** (Fixes 1.1–1.6). The `startProjectFor()` helper,
   all-roles start/build with `ROLE_PROJECT` bias, nudges, client+server fallback parity, elder-only
   blueprint authority, directive, cadence, roster guard, and the MAIN RULE (elder assigns tasks to idle
   agents). **Read the plan's "Implementation gotchas" section before touching code** — it documents five
   verified traps (double-logging, normalize passthrough for new actions, schema requirements for new
   decision fields, `assignedTask` lifecycle, and scope bugs).
2. **Part 3 — Queue parallelism.** Do this after Part 1 so the idempotent `startProjectFor` guard is
   already in place when concurrency lands. Remember the second `llmBusy` reference in the frame loop
   ([index.html:1335](simulation/index.html:1335)).
3. **Part 4 — Roster to 8.** Small, but touches `makeAgents`/`AGENT_NAMES` wiring and
   `specs/04-agent-spec.md`; doing it last in the pass keeps agent-name assumptions stable while editing
   Part 1 logic.
4. **Minimal GUI (from Part 2):** only Fix 2.3 zone labels and the `isThinking` indicator from Fix 2.2.
   These exist to make the validation gate below *observable*. Defer everything else in Part 2 to Pass C.

**Exit criteria:** code compiles/runs; a quick smoke session shows at least one `started ... project`
event in `logs/activity.jsonl`.

---

## GATE 1 — Validation session (mandatory, do not skip)

Run LM Studio + `uv run python simulation/server.py`, open `http://127.0.0.1:5001`, and let a session run
~15+ minutes with the tab foregrounded (backgrounded tabs throttle `requestAnimationFrame` and pause the
sim). Walk the Parts 1–4 validation checklist in `fix_build_progression.plan.md` (items 1–13).

Additionally **record the pace data Part 5's tuning depends on**:

- How long does one project take start→built (frames / wall clock)?
- How often does the elder act (its effective think cadence under the pool)?
- Does `propose_blueprint` *ever* fire spontaneously now that agents are un-stuck?
- Any `compute_error` bursts at `MAX_CONCURRENT_LLM = 3`? (If so, drop to 2 before Pass B.)

If the seed loop does not complete repeatedly here, **stop and fix** — do not proceed to Pass B on a
broken foundation.

---

## PASS B — Emergence (emergence_llm_brain: Part 5)

Implement `emergence_llm_brain.plan.md` in its internal order (5.1 gating → 5.2 nudges/backstop →
5.3 goals → 5.4 commitments → 5.5 world expansion). Use the Gate 1 pace data to tune:

- The elder-turn threshold N for the invention backstop (plan suggests 3 — lengthen if elder cadence is
  fast, shorten if slow).
- Goal staleness (~10 agent turns) and commitment expiry (~15 agent turns) against real turn cadence.

**Exit criteria (Gate 2):** the Part 5 validation checklist passes — most importantly: a custom blueprint
is proposed, approved, funded, **built**, and rendered at a generated (ring 1+) spot, and civilization
level advances past the seed ceiling.

Known honest risk: a small local model may stall at "invention required" with weak or invalid proposals.
The plan's backstop keeps pressure on and logs the stall; if it persists, the highest-leverage fix is a
stronger model in LM Studio, not more code.

---

## PASS C — GUI polish (optional; any time after Pass A)

The remainder of `fix_build_progression.plan.md` Part 2, in impact order:

1. Fix 2.1 depth sorting (+ structure shadows)
2. Fix 2.2 remaining annotation de-stacking
3. Fix 2.4 layout/DPI/panels/Resources stat
4. Fix 2.5 sprite distinctiveness (stretch — highest effort, lowest functional value)

Pass C touches only rendering and DOM panels, so it cannot break Passes A/B logic; it can run in parallel
with Pass B if two working branches are acceptable, but merge carefully around `drawAgent`.

---

## Cross-pass rules

- **One concern per commit** (e.g. "Part 1 Fix 1.1", "Part 3 pool") so a regression bisects cleanly.
- After every pass, re-run the earlier passes' validation items — Part 5 must not regress Parts 1–4
  (checklist item 8 in the emergence plan).
- The JSONL logs (`simulation/logs/<session>/`) are the ground truth for every gate; read
  `lm_studio.jsonl` decisions, not just the canvas.
- Keep client/server behavior mirrored wherever both exist (fallbacks, action guards, prompt claims) —
  divergence between `index.html` and `server.py` was a root cause the first time around.

## Recommended models (all passes)

1. **Claude Opus 4.8** — Pass A Part 1 + Part 3 (precision, concurrency) and Pass B (cross-file
   invariants, lifecycle correctness).
2. **Claude Sonnet 4.6** — Pass A Parts 4 + minimal GUI, and Pass C (mechanical, well-specified edits).
3. **GPT-5 (Cursor)** — second-opinion reviews at each gate, especially queue edge cases (Pass A) and
   goal/commitment lifecycle leaks (Pass B).
