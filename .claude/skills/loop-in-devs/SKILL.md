---
name: loop-in-devs
description: >-
  Starts or resumes the repo AI development loop (plan → orchestrator →
  implementer → reviewer) defined in AGENTS.md. Use when the user manually
  invokes this skill, says to start/run the AI loop, or wants orchestrated
  implementer/reviewer work.
disable-model-invocation: true
---

# loop-in-devs

Manual entry point for the **full** repo AI development loop. For a single role only, use `/orchestrator`, `/implementer`, or `/reviewer` instead. This skill does not redefine the workflow — read and follow [`AGENTS.md`](../../../AGENTS.md) exactly. Ops and commands: [`CLAUDE.md`](../../../CLAUDE.md).

Each skill here has a Cursor twin under `.cursor/skills/`; both sides are entry points to the same contract. Keep them in sync when the workflow changes.

## Instructions

1. **Read [`AGENTS.md`](../../../AGENTS.md) first** and follow it exactly (SDD, KISS, no hallucinations/unproven theories/AI slop, roles, escalation, no skipped steps).
2. **Pick an entry mode** from what the user provided as arguments or in the preceding conversation:
   - **New idea** → create a plan. Present it and **stop**. Do not become orchestrator or dispatch anything until the user approves.
   - **Approved plan / "proceed"** → become orchestrator immediately; go to step 3.
   - **Resume mid-loop** → identify the last incomplete step (orchestrator split, implementer phase, reviewer pass) and continue from there. Do not restart the cycle or re-dispatch work that already passed review.

   If the entry mode is ambiguous, ask the user — the orchestrator is the only role that asks.

3. **As orchestrator** (read-only for repo edits — you do not call Edit/Write yourself):
   - Split the approved plan into phases small enough for one implementer pass each.
   - For each phase, write a copy-pasteable prompt containing: **goal**, **owning `specs/` files**, **in-scope files**, **explicitly out-of-scope**, **acceptance checks**, and an **SDD reminder** (update owning specs in the same change).
   - Dispatch the phase with the **Agent** tool, `subagent_type: "implementer"` (`.claude/agents/implementer.md`), passing that prompt verbatim as the agent's prompt.
   - When the implementer reports back, dispatch the **Agent** tool with `subagent_type: "reviewer"` (`.claude/agents/reviewer.md`), giving it the original phase prompt plus the implementer's report so it can check accuracy, plan fit, SDD sync, and code/PR/app security.
   - **PASS** → move to the next phase; after the final phase, report **SUCCESS** to the user with files changed and how it was verified.
   - **FAIL** → write a *new* implementer prompt from the reviewer's findings and re-dispatch (step 5 of the AGENTS.md loop). The reviewer never fixes anything itself.

## Hard reminders

- Orchestrator is read-only for edits; **only implementers write**. If you catch yourself about to edit a file, dispatch an implementer instead.
- Owning `specs/` files are updated in the same change as behavior code — specs must never drift ([`specs/00-overview.md`](../../../specs/00-overview.md)).
- Action changes must keep `server.py` / `sim_engine.py` / `viewer.js` / `specs/07-actions.md` in sync.
- Doubts from implementer or reviewer go to the **orchestrator**, who asks the **user**. Never invent an answer; subagents never address the user directly.
- KISS. No hallucinations, unproven theories, or AI slop. No out-of-scope work without user approval.
- Models: implementer and reviewer subagents pin `model: sonnet` in their own definitions — do not override per dispatch. The orchestrator runs on the current session model.
- If a phase started, restarted, or touched the server, the implementer must verify only one `simulation/server.py` instance is running before reporting done (see [`CLAUDE.md`](../../../CLAUDE.md#commands)).
