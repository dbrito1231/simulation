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

Manual entry point for the repo AI development loop. This skill does not redefine the workflow — read and follow [`AGENTS.md`](../../../AGENTS.md) exactly.

## Instructions

1. **Read [`AGENTS.md`](../../../AGENTS.md) first** and follow it exactly (SDD, KISS, no hallucinations/unproven theories/AI slop, roles, models, escalation, no skipped steps).
2. **Pick an entry mode** from what the user provided:
   - **New idea:** create a plan; wait for the user to proceed before becoming orchestrator.
   - **Approved plan / proceed:** become orchestrator immediately; split into phase prompts; dispatch implementer → reviewer → report SUCCESS or FAIL-reprompt.
   - **Resume mid-loop:** continue from the last incomplete step (orchestrator / implementer / reviewer) without restarting the whole cycle.
3. **Hard reminders:**
   - Orchestrator is read-only for edits; only implementers write.
   - Specs under `specs/` updated for every behavior change.
   - Doubts from implementer/reviewer → orchestrator → user.
   - No hallucinations, unproven theories, or AI slop.
   - Models: orchestrator = current session model; **implementer = Composer 2.5 only**; reviewer = Claude Sonnet 5 or Composer 2.5 Fast.
