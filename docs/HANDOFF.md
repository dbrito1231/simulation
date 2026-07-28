# Handoff — Sovereign God Mode planning

**Updated:** 2026-07-28

**Branch:** `main`

**HEAD:** `7077d40 Merge branch 'feature/living-ecosystem'`
**Status:** Planning and review only. No God-mode implementation has begun.

## Start here

Read [CLAUDE.md](../CLAUDE.md) first. It is the canonical agent guide. The
repository is server-authoritative, canonical specs must stay synchronized with
implemented behavior, and implementation must follow the repository's
orchestrator/Sonnet-5 model policy.

The active planning artifact is:

- [plan-sovereign-god-mode-v2.md](plan-sovereign-god-mode-v2.md) — **current**
- [plan-sovereign-god-mode.md](plan-sovereign-god-mode.md) — superseded; retained
  for review history only

v2 was produced after verifying v1's claims line-by-line against the working
tree. It corrects four contract defects (gather arithmetic ordering, weather
expiry ownership, divine-vs-emergent rule composition, and the survival modifier
catalog), splits the SPEC 00 identity amendment into its own commit, moves the
idempotency store in-memory, defers weather override to its own phase, and
rewrites the model-routing section for the Claude Code harness. See v2's "Why v2
exists" section.

Do not implement it until the user explicitly approves implementation and the
decisions in v2's "Decisions required before implementation" section (14 items;
five changed or new since v1).

## Conversation outcome

The user explored adding a God feature to GitServ. The concepts discussed were:

- **Storyteller God:** authors narrative events whose mechanics are translated
  into bounded, typed effects; agents decide how to respond.
- **Miracle God:** directly performs visible, validated interventions such as
  healing, resource grants, weather changes, and structure repair/damage.
- **Avatar God:** a user-controlled in-world character. The user ultimately
  excluded this mode.
- **All-in-one/Sovereign God:** a disembodied combination of omniscient Sight,
  Voice, Providence, Miracles, Storytelling, and temporary Lawgiving.

The resulting plan is deliberately control-plane based. Divine commands are not
agent decisions and do not enter `DECISION_ACTIONS`, `DECISION_SCHEMA`,
`SYSTEM_PROMPT` as selectable actions, `apply_decision()`, `available_actions`,
or viewer `ACTION_LABELS`. If a future change adds an agent action such as
`pray`, it must satisfy the full action-sync invariant separately.

## Planned feature shape

The plan currently specifies:

- default-dark, startup-only enablement through `SIM_GOD_MODE=1`;
- a required non-empty `SIM_GOD_TOKEN` for private and mutating routes;
- authenticated Sight separated from the public `/state` projection;
- public proclamations, public non-binding providence, and private omens;
- bounded immediate miracles for vitals, known resources, structure condition,
  and weather;
- timed allowlisted modifiers for gathering, fish yield, hunger drain, health
  regeneration, structure decay, and spoilage;
- atomic Storyteller events composed from those typed primitives;
- a feature-gated Divine Console with preview/apply/cancel workflow;
- persisted `civilization["godState"]`, bounded intervention/idempotency history,
  and a dedicated `divine.jsonl` audit stream;
- an optional, deferred free-prose story compiler that may only draft a typed
  preview and requires separate model-contention measurement.

Dangerous powers are explicitly deferred: avatar control, resurrection, forced
death, teleportation, agent creation/deletion, direct belief or relationship
rewriting, forced council/election outcomes, arbitrary structure spawning, and
arbitrary state-path editing.

## Review and revisions

The initial plan was reviewed as an implementation handoff. It was not considered
implementation-ready until the following contracts were corrected. The plan now
contains all of these revisions:

1. **Spec synchronization:** Phase 0 is plan approval only and cannot edit
   canonical specs. Each behavior phase updates its owning specs in the same
   commit as code.
2. **Preview binding:** preview creates a short-lived, opaque, server-held
   `previewId`, canonical command digest, and precondition fingerprint. Apply
   accepts only `{previewId, requestId}` and revalidates under the engine lock.
3. **Idempotency:** persisted `recentRequests` records retain the original
   bounded authoritative response. Same-ID retries return it; mismatched reuse
   rejects.
4. **Stored-content safety:** divine text is normalized plain text and must be
   rendered through `textContent`/`escapeHtml`; hostile stored-string cases are
   part of verification.
5. **Expiry ownership:** effect lookups enforce
   `startFrame <= frameTick < expiresFrame`; `_expire_divine_effects()` runs
   immediately after frame advancement and before affected systems.
6. **Modifier arithmetic:** gathering, survival, decay, and spoilage now have
   defined ordering, rounding, zero-result, identity, and resource-specific
   precedence rules.
7. **Stable private targets and memory:** private omens use `agent["id"]`, not
   names. They remain a dedicated prompt line while active and enter ordinary
   agent/vector memory exactly once on expiry or revocation.
8. **Operational enablement:** `SIM_GOD_MODE` is read at startup; runtime enable
   routes are forbidden. Both the flag and token are required.

v2's phases (renumbered from v1):

0. Contract freeze — planning only, no spec edits.
1. SPEC 00 identity amendment — prose only, its own commit.
2. Secure kernel, persistence, preview, and audit.
3. Voice and providence.
4. Bounded immediate miracles.
5. Storyteller events and temporary laws.
6. Weather override — promoted out of the baseline.
7. Divine Console and public presentation.
8. Optional free-prose story compiler.

Phases 2–5 overlap heavily in `simulation/sim_engine.py` and must be sequential.
The recommended first delivery slice, after explicit approval, is Phases 1–3.
Phase 3's cognition measurement gate is open-ended research and is the schedule
risk in that slice.

## Validation already performed

Planning-only checks:

- confirmed the plan includes all eight corrected contracts listed above;
- confirmed no trailing whitespace in the plan;
- confirmed the plan is documentation-only;
- no canonical spec, Python, JavaScript, runtime flag, route, state database, or
  server configuration was changed;
- no server was started, stopped, or restarted during plan creation/revision.

No implementation smoke, live God-mode test, visual QA, or Ollama cognition
measurement exists because the feature has not been implemented.

## Earlier agent-beliefs review

The conversation began with a review of the untracked
[agent-beliefs.md](agent-beliefs.md). Two findings were reported, but the file
was not edited:

- it names `CULTURE_ENABLED` as the belief-system flag, while founding,
  availability, adoption, and voting are primarily governed by `MEMES_ENABLED`;
- its "Current live state" section is a volatile runtime snapshot and should be
  labeled as such or moved to a dated run note.

At the time of that review, the running `/state` snapshot did match the document:
14 agents held both seed beliefs except Ash, who held neither, and the registry
contained only the two seeds. That was a 2026-07-27 observation and must not be
treated as current without rechecking.

## Dirty worktree and ownership cautions

Current relevant files:

- `docs/HANDOFF.md` — replaced with this handoff at the user's request.
- `docs/plan-sovereign-god-mode.md` — untracked planning artifact created in this
  conversation.
- `docs/agent-beliefs.md` — pre-existing untracked user file; reviewed but not
  modified.

Nothing has been staged or committed. Preserve `docs/agent-beliefs.md` and do not
fold it into God-mode work without explicit user direction.

## Next-agent checklist

1. Read `CLAUDE.md` and the complete God-mode plan.
2. Check `git status` before touching files.
3. Do not treat the plan's recommended defaults as user approval.
4. If the user requests implementation, resolve the required Sonnet 5
   implementation-agent availability before editing code.
5. Keep Phase 0 non-canonical; update specs only with their implementing behavior.
6. Implement sequentially, preserve the thin-viewer/server-authoritative
   boundary, and use the plan's validation gates.
7. If cognition prompts change, measure them before shipping and record fresh
   post-restart evidence in `specs/03-cognition.md`; restored cached PIANO reports
   do not count.
8. Finish any server-touching implementation with the single-instance/port-5001
   verification required by `CLAUDE.md`.
