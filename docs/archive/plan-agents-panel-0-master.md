# Master Plan — Agents Panel Overhaul (one project, three plans)

**Status:** Planned — not yet executing. Awaiting go-ahead.
**Branch:** `feature/agents-panel-overhaul` (already created off `main`).
**Delivery gate:** Nothing merges to `main` without the user's explicit approval.

This is the umbrella plan that coordinates the three child plans into a single
project, defines the agent team, assigns models, and sets the evidence bar for
delivery.

## Child plans (the work)

| # | Plan | File | Touches |
|---|------|------|---------|
| 1 | Vitals & crisis | [plan-agents-panel-1-vitals-and-crisis.md](plan-agents-panel-1-vitals-and-crisis.md) | `index.html` only |
| 2 | Inventory & history | [plan-agents-panel-2-inventory-and-history.md](plan-agents-panel-2-inventory-and-history.md) | `index.html` (+ optional read-only endpoint in `server.py`) |
| 3 | Social + reasoning + roll-up + follow-cam + inline last-thought | [plan-agents-panel-3-social-reasoning-rollup-followcam.md](plan-agents-panel-3-social-reasoning-rollup-followcam.md) | `index.html`, `sim_engine.py` snapshot, one spec |

## Dependency graph — why order matters

All three plans edit the **same client surface**: `renderAgentPanel`,
`lastAgentPanelKey`, the sidebar HTML, and the `#agentList` CSS in
`simulation/index.html`. Plan 3 additionally depends on artifacts the earlier
plans create:

- Plan 3's roll-up (C4) reuses **Plan 1's crisis-severity** computation.
- Plan 3's C1/C2 blocks extend **Plan 2's `#agentDetail`** panel.
- Plan 3's inline last-thought removes the beliefs line that Plans 1/2 still
  render around.

```
Plan 1 ─┐
        ├─► Plan 3 (must be last)
Plan 2 ─┘
```

**Consequence:** because the plans overlap on `renderAgentPanel` /
`lastAgentPanelKey`, running them in fully parallel worktrees would guarantee
merge conflicts in that function. This project therefore uses **sequential
integration on the shared branch** — Plan 1 → Plan 2 → Plan 3 — with a defined
handoff after each. Intra-plan parallelism (independent files/blocks) is allowed;
inter-plan parallelism on the shared function is not.

## Team topology (org chart)

```
                    ┌─────────────────────────────┐
                    │      MAIN ORCHESTRATOR       │  (this session)
                    │  owns branch, sequencing,    │
                    │  integration, evidence bundle│
                    └──────────────┬──────────────┘
         dispatch + handoff        │        collects results
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ PLAN-1 ORCH.  │         │ PLAN-2 ORCH.  │         │ PLAN-3 ORCH.  │
│ vitals&crisis │  ───►   │ inventory&hist│  ───►   │ social/rollup │
└──────┬────────┘         └──────┬────────┘         └──────┬────────┘
       │ implementers            │ implementers            │ implementers
   ┌───┴───┐                 ┌───┴───┐                 ┌───┴────────┐
   ▼       ▼                 ▼       ▼                 ▼    ▼       ▼
 impl    impl              impl    impl             impl impl    impl
 (CSS/  (sort/                                     (engine)(client)(spec)
  bars) crisis)
                    ┌─────────────────────────────┐
   all three ─────► │          REVIEWER           │ ─────► evidence → USER
   plans done       │ validates EVERYTHING before │
                    │ any delivery / merge request │
                    └─────────────────────────────┘
```

**Roles & responsibilities:**

- **Main orchestrator (this session).** Owns the branch. Sequences the three
  plan orchestrators in dependency order, performs the integration checkpoint
  after each plan (server boots, smokes pass, panel renders), resolves any
  cross-plan conflict, assembles the final evidence bundle, and is the only one
  that requests the user's merge approval. Writes no implementation code itself
  (per CLAUDE.md model policy) beyond trivial one-line fixes.
- **Plan orchestrator (×3).** One per child plan. Reads its plan doc, splits it
  into steps, dispatches implementers, reviews their diffs against the plan's
  acceptance/verify section, and reports a per-plan completion summary (diff +
  smoke result) back to the main orchestrator. Does **not** proceed to the next
  plan — that handoff is the main orchestrator's call.
- **Implementer(s).** Do the actual editing for one step/file. Repo policy pins
  these to the `implementer` subagent type. Multiple may run in parallel **only**
  on non-overlapping files within a single plan.
- **Reviewer (×1).** Runs after all three plans are integrated. Independently
  validates the whole diff against all three plans' acceptance criteria + the
  critical invariants (action-sync, specs-match-repo, snapshot shape), runs the
  deterministic smokes and a live server check, and produces the pass/fail
  verdict. Nothing is delivered or proposed for merge until the reviewer passes.

**How they work together (required collaboration, not siloed):**

- Every plan orchestrator receives the **same shared context** from the main
  orchestrator: current branch state, the exact `index.html` line ranges its
  plan owns, and the integration state left by the previous plan.
- Handoff is explicit: Plan N orchestrator returns `{diff summary, files touched,
  smoke result, open risks}`; the main orchestrator verifies the integration
  checkpoint, then briefs Plan N+1 with the **updated** line references (since the
  previous plan shifted them). This closes the loop so no agent works from a stale
  view — satisfying "agents must work together."

## Model utilization (platform-agnostic: Claude **and/or** OpenAI GPT/Codex)

This plan is written to run on **either** stack. Roles are defined by the
**capability tier** they need, then bound to concrete models per platform — so
whether you execute under Claude Code or OpenAI Codex, the team structure and
the tier assignments stay identical. Pick one column (or mix, if your tooling
lets you) and bind the roles accordingly.

| Role | Capability tier needed | Claude binding | OpenAI GPT / Codex binding |
|------|------------------------|----------------|-----------------------------|
| Main orchestrator | Frontier reasoning / synthesis (low volume, high stakes) | **Opus 4.8** | Your frontier reasoning model (e.g. a GPT‑5 / o‑series–class model — use whatever you actually have) |
| Plan orchestrators (×3) | Strong planning + dispatch, mid volume | **Sonnet 5** | A mid reasoning model (frontier‑minus tier) |
| Implementers | Capable coding, high volume, lower cost | **Sonnet 5** (`implementer` type); **Haiku 4.5** for mechanical sub-steps (CSS‑only, string swaps) | Codex's capable coding‑tier model; a lighter model for trivial edits |
| Reviewer (×1) | Strong reasoning **plus** ability to run smokes / boot server / read `/state` | **Sonnet 5 or Opus 4.8** | A strong reasoning model with tool/exec access in Codex |

**Portability rationale:**

- Keep the expensive orchestration/synthesis on the **strongest tier**; push
  high-volume implementation to a **capable-but-cheaper coding tier**; drop to
  the **cheapest tier** for purely mechanical sub-steps. This tiering holds on
  both platforms — only the model names change.
- **Optional cross-family check:** if you have access to both, running the final
  review on a *different* family than the one that authored the code (Claude
  reviewing a GPT/Codex build, or vice-versa) catches blind spots the authoring
  family shares. This is an advisory sanity check, not a required merge gate.

> **Claude-specific note (only when executing under Claude Code):** CLAUDE.md's
> model policy pins all non-orchestrator work to **Sonnet 5 or lower** and routes
> implementation through the `implementer` subagent type. That policy is the
> Claude *binding* of the tier abstraction above — honor it when running on
> Claude; substitute the equivalent tiers when running on Codex.

## Execution sequence

1. **Setup (done):** branch `feature/agents-panel-overhaul` created off `main`.
2. **Plan 1 — vitals & crisis.** Plan-1 orchestrator → implementers (CSS/bars,
   crisis sort). Integration checkpoint: server boots, panel renders bars +
   crisis ordering, `SURVIVAL_ENABLED` off hides it.
3. **Plan 2 — inventory & history.** Plan-2 orchestrator adds `#agentDetail`,
   inventory chips, client-side history buffer (B1). Checkpoint: select an agent
   → inventory + timeline render; Plan 1 behavior intact.
4. **Plan 3 — social/reasoning/roll-up/follow-cam/inline-thought.** Plan-3
   orchestrator: engine snapshot (`relationships`, `lastReasoning`) + **spec
   update**, then roll-up header, detail-panel C1/C2, inline last-thought
   (removes beliefs), follow-cam. Checkpoint: `/state` exposes new fields;
   roll-up counts match; follow-cam tracks; beliefs line gone; no empty feeling
   line (per Plan 3 step 4b — `feeling` is ~0% populated, deliberately omitted).
5. **Final review.** Reviewer validates the whole diff + invariants + smokes +
   live check. Optional: a second review on a different model family (see
   Model utilization) as an advisory cross-check.
6. **Deliver evidence to user; request merge approval.** No merge before approval.

## Critical invariants to hold (from CLAUDE.md)

- **Specs match repo:** Plan 3's new snapshot fields require a same-change spec
  update (owner via [specs/00-overview.md](../specs/00-overview.md)).
- **Action-sync invariant:** no new actions are introduced here, but the reviewer
  confirms none of the edits desync `DECISION_ACTIONS` / schema / prompt.
- **Snapshot discipline:** engine mutates world state only under its lock; the
  new snapshot fields are read-only projections — keep payload bounded (filter
  neutral ties, cap reasoning length).

## Definition of done (evidence bundle — MUST accompany delivery)

The main orchestrator delivers to the user **only** with all of:

1. **Diff summary** per plan + combined, with files touched.
2. **Smoke evidence:** `uv run python scripts/sid_parity_smoke.py` and
   `uv run python scripts/path1_smoke.py` both passing (paste output).
3. **Live evidence:** server boots on 5001; a screenshot of the Agents panel
   showing vitals bars, crisis ordering, inventory/history on select, roll-up
   header, and inline last-thought; a `GET /state` excerpt showing the new
   `relationships` / `lastReasoning` fields.
4. **Spec-update confirmation:** the owning spec diff alongside the code.
5. **Reviewer verdict:** explicit pass on all three plans' acceptance criteria.

If any item is missing or failing, the project is **not** done — the main
orchestrator reports the gap rather than delivering. No merge to `main` without
the user's explicit approval.

## Guardrails (from the user's constraints)

- **MUST NOT merge to `main`** without explicit user approval.
- **MUST NOT hallucinate** — every claim of "done" is backed by an artifact in
  the evidence bundle; unknowns are surfaced, not invented. Model bindings name
  only tiers you actually have (no invented model IDs).
- **MUST NOT let agents work in isolation** — enforced by the shared-context
  handoff protocol between the main orchestrator and each plan orchestrator.
- **MUST NOT deliver without evidence** — enforced by the Definition of Done.
