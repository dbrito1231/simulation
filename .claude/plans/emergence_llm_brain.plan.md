---
name: "Part 5 — Emergence: Make the LLM Matter"
overview: "Today the LLM is a picker, not a brain: it chooses among ~18 canned actions, the buildable universe is 4 hardcoded templates rotated by a modulo counter, the one creative channel (propose_blueprint) fired 0 times in 36 logged calls, conversations have no consequences, and structures drop onto 12 fixed spots so the settlement physically cannot grow. This plan makes the LLM load-bearing: blueprint-gated progression (the village must invent to grow), persistent agent goals, consequential conversations (commitments), and expandable build space. PREREQUISITE: the fix_build_progression plan (Parts 1–4) must be implemented and validated first — emergence sits on top of a working build pipeline."
todos:
  - id: blueprint-gated-progression
    content: "index.html: after all 4 seed templates have been built at least once, require the next project to be an approved custom blueprint (seed templates locked); surface the lock in the prompt so agents know they must invent"
    status: pending
  - id: blueprint-nudges
    content: "index.html + server.py: escalating propose_blueprint nudges — remind agents when invention is required, and have the elder's directive demand proposals when the registry has no unbuilt custom projects"
    status: pending
  - id: persistent-goals
    content: "index.html + server.py: add agent.goal (LLM-authored via a new optional 'goal' decision field), persist it across turns in the prompt, and track/clear completion so plans outlive a single action"
    status: pending
  - id: consequential-conversations
    content: "index.html: turn talk requests into commitments — when agent A asks agent B for something, store a commitment on B, surface 'You agreed to…' in B's prompt, and clear it when honored"
    status: pending
  - id: world-expansion
    content: "index.html + sprites.js: replace the 12 fixed structure spots with generated spots that expand outward in rings/districts as the village grows; render the growth"
    status: pending
  - id: schema-updates
    content: "server.py: add 'goal' to the decision JSON schema block + an example; add prompt lines for current goal, commitments, and the invention requirement"
    status: pending
  - id: validate-emergence
    content: "Run a long session and walk the emergence validation checklist (blueprints proposed/approved/built, goals persisting, commitments honored, village footprint growing)"
    status: pending
isProject: false
---

# Part 5 — Emergence: Make the LLM Matter

## Prerequisite (hard requirement)

Implement and validate `.cursor/plans/fix_build_progression.plan.md` (Parts 1–4) **first**. This plan
assumes: the build pipeline completes (start → contribute → build), the elder assigns tasks and sets
directives, `startProjectFor()` exists, the concurrency pool runs, and the 8-agent roster is live. Run at
least one full session and confirm several seed projects complete before starting here — Fix 5.1's design
depends on observing the seed loop's real pace (project duration, elder cadence, whether blueprints get
proposed at all once agents are un-stuck).

## The problem this plan solves

Evidence from the code and `simulation/logs/`:

- The buildable universe is 4 hardcoded templates (`PROJECT_TEMPLATES`, [index.html:243](simulation/index.html:243)),
  rotated by `completedProjects % 4` ([index.html:579](simulation/index.html:579)). The LLM has no say in
  what gets built.
- `propose_blueprint` — the only channel where the LLM can invent — fired **0 times in 36 logged calls**.
  The entire blueprint pipeline (validation [server.py:267](simulation/server.py:267), pending queue,
  elder approval) is well-built and dead.
- Each think is nearly stateless: a few memory strings, no persistent plan. No decision outlives one action.
- Conversations mutate nothing. A trade discussion doesn't create a trade or an obligation.
- `findStructureSpot()` ([index.html:584](simulation/index.html:584)) has 12 fixed spots inside the village
  rectangle. The settlement physically cannot expand, no matter how smart the model is.

Design principle throughout (same as Part 1): **the LLM proposes, deterministic code guarantees a floor.**
Every emergent behavior gets a prompt channel (so the model can be smart) plus a deterministic backstop (so
the sim never stalls when it isn't).

---

## Fix 5.1 — Blueprint-gated progression (the growth mechanic)

**File**: [`simulation/index.html`](simulation/index.html)

Make invention mandatory for growth instead of optional garnish.

1. Track which project types have been completed: add `civilization.builtTypes = new Set()` to the
   civilization object ([index.html:284](simulation/index.html:284)); add the type on every completed
   build in the `build_structure` case.
2. Add a helper `inventionRequired()`: true when all 4 seed template ids are in `builtTypes` **and** the
   project registry contains no approved-but-unbuilt custom project.
3. Gate `startProjectFor()` (from Part 1): when `inventionRequired()`, refuse to start a *seed* template —
   return a summary like `"${agent.name} wants to build, but the village needs a NEW invention
   (propose_blueprint)"`. Approved custom blueprints can always be started.
4. When a custom project *is* available, bias `startProjectFor` to pick it before any seed repeat.

The result: House → Farm Plot → Workshop → Wall → **the village cannot grow further until an agent invents
something and the elder approves it**. Civilization level (currently `completedProjects / 3`,
[index.html:602](simulation/index.html:602)) keeps rising only through invention.

## Fix 5.2 — Escalating invention nudges + elder demand (deterministic floor)

**Files**: [`simulation/index.html`](simulation/index.html), [`simulation/server.py`](simulation/server.py)

The 0/36 lesson from Part 1 applies here too — the model will not spontaneously pick `propose_blueprint`.
Give it pressure that escalates:

1. **Prompt line** (server, user prompt template [server.py:146](simulation/server.py:146)): add
   `Invention status: {invention_status}` — either `"not needed"`, or
   `"REQUIRED: all known structures are built. Use propose_blueprint to invent a new structure."`.
   Client sends the flag in the think payload.
2. **Behavior nudge escalation** (client, `thinkAgent`): while `inventionRequired()`, every agent's
   `behavior_nudge` leads with the invention demand; the elder's directive (Part 1, Fix 1.6 B) becomes
   `"Elder Sage directs: the village needs a new invention — propose a blueprint!"`.
3. **Deterministic backstop**: if `inventionRequired()` has been true for N consecutive elder turns
   (suggest N=3) and no blueprint is pending, the elder's fallback `assign_task` (Part 1, Fix 1.6 E)
   targets the most idle agent with the task `"propose a new structure blueprint"`. This keeps the pressure
   in-world instead of hard-coding a fake proposal — the *content* of the blueprint must still come from
   the LLM. Accept that a weak model may stall here; log the stall (`pushActivity`) so it is observable.
4. Blueprint quality guardrails already exist (`validate_blueprint`, [server.py:267](simulation/server.py:267)) —
   ids, needs bounds (1–8 entries, amounts 1–5), resource caps. No changes needed; they are the reason this
   can be opened up safely.

## Fix 5.3 — Persistent goals (plans that outlive one action)

**Files**: [`simulation/index.html`](simulation/index.html), [`simulation/server.py`](simulation/server.py)

1. **Schema**: add an optional `"goal"` field to the decision JSON schema block in `SYSTEM_PROMPT`
   ([server.py:109-118](simulation/server.py:109)) — `"goal": "<a short multi-step plan you commit to, or
   null to keep your current goal>"` — plus one example (e.g. a gatherer:
   `"goal":"stockpile 5 wood then contribute it all to the next project"`). This is the same lesson as
   gotcha #4 in the Part 1 plan: **a field the schema doesn't show will never be emitted.**
2. **Client state**: add `agent.goal = null` to agent init ([index.html:394](simulation/index.html:394)).
   In `thinkAgent`'s decision handling, if `decision.goal` is a non-empty string, set `agent.goal`
   (cap length ~120 chars). Include `goal` in the think payload; add `Your current goal: {goal}` to the
   user prompt template so the model sees its own standing plan every turn.
3. **Completion/staleness**: goals are self-managed — the model can replace its goal any turn. Add a
   deterministic staleness cap: clear a goal unchanged for more than ~10 of that agent's turns and nudge
   `"Your goal went stale — set a new one."`. No complex goal-parsing; the goal is *context*, not a state
   machine. Its value is coherence across turns, cheap to implement, impossible to break the sim with.

## Fix 5.4 — Consequential conversations (commitments)

**File**: [`simulation/index.html`](simulation/index.html)

Make talk change the world. Narrow scope deliberately: **requests create commitments.**

1. In the `talk_to_nearby` case of `applyDecision` ([index.html:909](simulation/index.html:909) area):
   after the message is delivered, if the message contains a resource word the *target* could act on
   (match against `Object.keys(civilization.resourceRegistry)`), store on the target:
   `target.commitment = { to: agent.name, text: message, madeAt: frameTick }`. One commitment per agent;
   a new one overwrites.
2. Surface it: include `commitment` in the think payload and prompt —
   `You agreed to help: {commitment_from}: "{commitment_text}"` — and fold into the target's
   `behavior_nudge` after any elder-assigned task (leader outranks peers).
3. Honor + clear: when the committed agent next performs a matching productive action
   (`collect_resource`/`contribute_resources`/`trade_resource` involving the mentioned resource), clear the
   commitment and log `"${name} honored a promise to ${to}"`. Also expire after ~15 of that agent's turns
   so stale promises don't pile up.
4. Priority order inside `behavior_nudge` (single source of truth, client-side):
   **elder assigned task → invention requirement → commitment → own goal → generic nudges.**

## Fix 5.5 — World expansion (the settlement physically grows)

**Files**: [`simulation/index.html`](simulation/index.html), [`simulation/sprites.js`](simulation/sprites.js)

1. Replace the fixed 12-spot list in `findStructureSpot()` ([index.html:584](simulation/index.html:584))
   with a generator: keep the original 12 as ring 0, and when they are occupied, generate ring 1, 2, …
   outward from the village center (e.g. spiral/grid steps of ~55px), skipping water
   (`getZone(x,y) !== "ocean"`) and clamping to canvas bounds. Track `civilization.usedSpots`.
2. Structures already render generically for custom types (`drawGenericStructure` fallback in sprites.js) —
   verify a custom blueprint's `visual_style` renders at outer-ring positions without clipping.
3. Optional flourish (cheap): every completed ring, `pushActivity("The village has grown — a new district
   opens.")` — visible feedback that the world is expanding.
4. Do **not** attempt dynamic zones/terrain edits in this pass; that touches `getZone`'s hardcoded
   rectangles ([index.html:429](simulation/index.html:429)) and the tile renderer, and is a large change
   with little emergence payoff compared to rings. Note it as a future follow-up.

---

## What this plan deliberately does NOT do

- No new deterministic content (no hardcoded new buildings/resources) — new content must come through the
  LLM blueprint channel or not at all. That is the point.
- No dynamic zone/terrain generation (see 5.5.4).
- No model swap. But note honestly: a small local model may plateau at Fix 5.2's backstop (stalling at
  "invention required" with weak proposals). If sessions stall there, the highest-leverage change is a
  stronger model in LM Studio, not more code.

---

## Validation checklist

1. After the 4 seed templates are built once each, no seed template starts again; the activity log shows
   the invention demand.
2. Within a few elder cycles of `inventionRequired()`, agents receive invention nudges/tasks, and at least
   one `propose_blueprint` appears in `lm_studio.jsonl` decisions.
3. An approved blueprint becomes the active project, gets funded, and is **built** — a custom structure
   renders on canvas (generic style) at a generated spot.
4. `agent.goal` values appear in think payloads, persist across turns, and change over a session.
5. A talk request produces a commitment (`honored a promise` lines in `activity.jsonl`) at least once.
6. Structures eventually appear outside the original 12 spots (ring 1+); no structure on ocean tiles or
   off-canvas.
7. Civilization level advances past the seed ceiling (completedProjects > 4) purely via custom projects.
8. No regressions in the Part 1–4 behavior (pipeline, elder leadership, throughput, roster).

---

## Recommended models for implementing this plan in Cursor

1. **Claude Opus 4.8** — this plan is design-heavy (gating logic, nudge priority ordering, commitment
   lifecycle) with cross-file prompt/schema coupling; Opus is strongest at keeping those invariants
   consistent across index.html and server.py.
2. **Claude Sonnet 4.6** — good for the mechanical pieces (spot generator, schema block edits, payload
   plumbing) once the gating/priority design from 5.1/5.2/5.4 is in place.
3. **GPT-5 (Cursor)** — useful second pass to hunt lifecycle leaks (commitments/goals that never clear,
   invention deadlocks when a blueprint is rejected).

Avoid "mini"/"haiku"-class models: lifecycle bugs here (a stuck `inventionRequired`, a commitment that
never clears) soft-lock the whole simulation and are exactly the class of error small models introduce.
