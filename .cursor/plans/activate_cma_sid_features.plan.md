---
name: Activate the CMA + Sid features (deterministic triggers, meme decoupling, Phase 3/4 validation)
overview: "Follow-up to cma_sid_enhancement.plan.md. That plan shipped all phases, but the 2026-06-29T17-38-36 run showed only Phases 1 (memory) and 2 (message bus) actually firing: switch_role, propose_rule, and vote_rule were chosen 0 times; meme adoption stayed flat at 0.13 (1/8) for the whole run; and Phases 3 (PIANO modules) + 4 (meta system) were never exercised because their flags are off. Root cause is the same failure mode CLAUDE.md already documents for start_project — purely LLM-opt-in actions never get chosen without a deterministic backstop. This plan adds deterministic triggers/nudges for the civilizational actions, decouples cultural transmission from the unused talk_to_nearby action, runs a controlled flag-on validation of Phases 3/4 with cost measurement, and fixes the weak specialization metric. No new features — make the implemented ones actually fire and prove they work. Diagnosis source: review of logs/2026-06-29T17-38-36/ (88 think calls, 0 fallbacks, 0 switch_role/propose_rule/vote_rule, meme rate constant)."
todos:
  - id: emergent-role-trigger
    content: "Phase 5 fix — index.html: add a deterministic switch_role backstop mirroring the start_project fix. When parse_project_shortfalls / village-need shows a needed resource has no able gatherer and an idle agent has no gathering specialty, have the role-fallback (client roleFallbackAction + server role_fallback_action) emit switch_role to the needed role; add a thinkAgent nudge so the LLM is prompted to switch before the fallback forces it. Guard against thrash (cooldown + don't abandon a still-needed role)."
    status: completed
    note: "Done as a tick-loop backstop (maybeAutoSwitchRole, ROLE_SWITCH_TICK_FRAMES=120, ROLE_SWITCH_COOLDOWN=600) routed through applyDecision. The LLM nudge already existed. Key finding: the default 8-agent roster already has every gatherer (farmer/fisher/gatherer/miner), so villageNeededRole() is correctly null in a healthy roster — the baseline's 0 switches was correct, not only an LLM stall. Backstop now fires when a gap truly opens (gatherer collapses / reduced roster). Protected roles (elder/builder/healer) are never auto-retrained. Validated via Node harness: full roster=0, miner-collapse=trader→miner, within-cooldown=0, protected-only=0."
  - id: rules-voting-trigger
    content: "Phase 6 fix — index.html: give the elder a deterministic propose_rule path (e.g. propose the resource_tax when the shared stockpile is empty and projects are stalling for resources), and an idle-agent vote nudge so pending rules actually get tallied. Surface 'there is a pending rule awaiting your vote' in the behavior_nudge (server.py) so non-elder agents are pushed to vote_rule. Enact on majority and confirm the tax fires in contribute_resources."
    status: completed
    note: "Done as tick-loop backstop maybeAdvanceRules (RULES_TICK_FRAMES=150, RULE_PROPOSE_COOLDOWN=1500): elder proposes a value-1 resource_tax on a cooldown when none is in force; one eligible agent/tick (idle-preferred) casts a vote (yes to a modest tax, no if value>2) until quorum, all routed through applyDecision. Reuses existing proposeRule/voteOnRule/tallyAndMaybeEnact/enforceResourceTax + the existing vote nudge. Validated via Node harness: propose(1 yes) → 4 yes votes → quorum 5 → enacted, tax=1, then stops proposing."
  - id: meme-decouple
    content: "Phase 7 fix — index.html: stop gating maybeSpreadBeliefs solely on talk_to_nearby (0 such actions occurred). Spread beliefs (a) on proximity each social tick between co-located agents, and (b) piggybacked on Sage's directive deliveries through the message bus, with a per-pair probability and cooldown. Keep the talk path too. Goal: a rising meme-adoption curve in benchmarks.jsonl."
    status: completed
    note: "Extracted shared transmitBelief(speaker,recipient,prob); now three channels: talk (MEME_SPREAD_PROB=0.5, kept), proximity (spreadBeliefsByProximity over getNearbyAgents every MEME_TICK_FRAMES=90 at MEME_PROXIMITY_PROB=0.2), and elder-directive piggyback (transmitBelief on assign_task delivery). Validated via Node harness: clustered village goes 1→7→8 adopters, saturating — no longer depends on the never-chosen talk action."
  - id: phase34-validation-run
    content: "Phases 3/4 validation — temporarily enable PIANO_MODULES and META_SYSTEM (index.html flags), run a short session, and confirm via lm_studio.jsonl + benchmarks.jsonl that /agent/module returns the 4 module reports, the Cognitive Controller consumes them, /meta/update produces autobiography + persona, self_prompt reaches agent_think, and module self-activation toggles. Record added LLM calls/latency per agent-turn; decide a sustainable default (likely keep off, or modules-only) given MAX_CONCURRENT_LLM=3 and context-size limits. Document findings; do not leave flags on by default without the cost check."
    status: completed
    note: "Validated against the LIVE running server (LM Studio up, qwen3.5-9b et al.) by exercising the endpoints directly rather than toggling flags on the running sim. RESULTS — all 4 /agent/module endpoints return coherent one-sentence reports (~2.8-3.2s each); /meta/update returns a first-person autobiography (capped 300 chars, kind=autobiography→longTerm) AND a persona directive that absorbed the seeded belief ('honor the Harvest Spirit'), ~6.9s (2 LLM calls); the CC path /agent/think accepted module_reports + self_prompt and returned a coherent decision (contribute wood) in ~5.6s. Server logged 4 module_run + 1 meta_update benchmark rows as designed. COST — runModules fans out in parallel via Promise.all but BYPASSES the MAX_CONCURRENT_LLM queue; staggering (social every 2nd module-tick, reflection every 3rd; self-activation further gates social off unless talking/unread) means a PIANO turn averages ~3-4 LLM calls (worst case 5: 4 modules + CC) vs 1 for a plain think, and ~9-18s wall vs ~3-6s — a 3-5x compute and 2-3x latency hit on a single 3-slot LM Studio, with context-burst risk per CLAUDE.md. DECISION — left BOTH flags at false (default unchanged). META_SYSTEM alone is the sweet spot (periodic: 2 calls every META_TICK_FRAMES=2400/agent, negligible amortized cost, high-value persona drift) and is safe for the user to enable; PIANO_MODULES should stay off except for experiments with a reduced roster (?agents=4-5) and raised LM Studio context length. Self-activation toggling is code-verified (updateModuleActivation) but only runtime-meaningful with PIANO on; not exercised in a full multi-agent loop."
  - id: specialization-metric-fix
    content: "Phase 8 fix — index.html: make specialization_entropy a real Shannon entropy over the live role *distribution* (counts per role), not the list of distinct roles (which is constant at log2(8)=3). Only then does emergent-role activity show up as a moving metric; add a rule-adherence metric (tax-paid vs tax-due) once Phase 6 fires."
    status: completed
    note: "Reassessed: roleEntropy() was ALREADY correct Shannon entropy over the role distribution; the flat 3.0 = log2(8) just means 8 agents held 8 distinct roles and none switched (correct, not stuck). Only change made was logging the role *counts* in the benchmark detail (instead of the role list) so a flat reading is self-evidently 'roster unchanged'. rule_adherence metric already existed."
  - id: prompt-housekeeping
    content: "server.py SYSTEM_PROMPT: reorder the rule numbering so 15 (SAGE PRIORITY) is not sandwiched between 17 and 18; keep the Sage-priority content unchanged. Optional: drop full 128-float vecs from memory.json persistence (recompute embeddings on load) to keep the file small."
    status: completed
    note: "Moved the SAGE PRIORITY block up to sit after CRAFTING (14) so the sequence reads 12,13,14,15(Sage),16,17,18 in order — rule 15 stays Sage priority, matching the CLAUDE.md reference (content unchanged). memory.json slimming: _persist now omits each entry's 128-float 'vec' (confirmed there is no load path — memory is per-session and never read back, so no recompute-on-load is needed). server.py compiles clean via uv. Takes effect on the next server restart."
  - id: verify-rerun
    content: "Verification — re-run a session and confirm in the logs: switch_role > 0 when a gather gap exists, at least one rule proposed/voted/enacted with the tax applied, a meme-adoption curve that climbs above the seed, and (if Phase 3/4 left on) module/meta benchmark rows. Compare against the 2026-06-29T17-38-36 baseline. No fallbacks/context errors regressions."
    status: pending
isProject: false
---

# Activate the CMA + Sid features

Companion remediation plan to
[cma_sid_enhancement.plan.md](cma_sid_enhancement.plan.md). The enhancement plan
is fully *implemented*; this plan makes the civilizational features actually
*fire* and proves the dark phases work.

## Why this is needed (evidence from `logs/2026-06-29T17-38-36/`)

| Feature | Status in code | What the run showed |
|---|---|---|
| Memory (Phase 1) | ✅ working | 204 entries, 3 Summarizer passes, recall into prompts |
| Message bus (Phase 2) | ✅ working | 21 Sage directives routed through inboxes |
| Emergent roles (Phase 5) | ⚠ inert | `switch_role` chosen **0** times; entropy flat at 3.0 |
| Rules / voting (Phase 6) | ⚠ inert | `propose_rule`/`vote_rule` **0**; no tax enacted |
| Memes (Phase 7) | ⚠ inert | adoption stuck at **0.13 (1/8)** all run |
| PIANO modules (Phase 3) | ❓ untested | `PIANO_MODULES = false`; `/agent/module` never called |
| Meta system (Phase 4) | ❓ untested | `META_SYSTEM = false`; `/meta/update` never called |

Run health was otherwise good: 88 think calls, **0** fallbacks, **0**
`bad_response`, **0** context-size errors.

## Root cause

The inert features are all **LLM-opt-in with no deterministic trigger**, and the
model never volunteers them — the identical failure mode `CLAUDE.md` records for
`start_project` ("the pipeline stalled because the LLM never spontaneously chose
`start_project`"), which was only fixed by deterministic fallbacks + nudges. The
fix is to give `switch_role`, `propose_rule`/`vote_rule`, and meme spread the
same deterministic backstops, and to stop coupling meme spread to
`talk_to_nearby` (chosen 0 times this run).

## Approach

1. **Deterministic triggers** for emergent roles and rules/voting, modeled
   exactly on the existing `role_fallback_action` / `thinkAgent`-nudge pattern,
   with anti-thrash cooldowns.
2. **Decouple cultural transmission** from a rarely-chosen action — spread on
   proximity and on directive delivery, not only on free talk.
3. **Validate the dark phases** (3/4) under controlled flag-on conditions and
   measure their real LLM cost before deciding a default, given the 3-slot
   `MAX_CONCURRENT_LLM` ceiling and LM Studio context limits noted in CLAUDE.md.
4. **Fix the specialization metric** so emergent-role activity is observable.
5. **Housekeeping** — prompt rule ordering, optional memory.json slimming.

## Scope guardrails

- No new features and no new actions — every action already exists and is synced
  across `AVAILABLE_ACTIONS` / `DECISION_ACTIONS` / `DECISION_SCHEMA` /
  `SYSTEM_PROMPT`. This plan only adds triggers, metric fixes, and validation.
- Keep all behavior behind the existing flags so it stays A/B-comparable.
- Verify by running the server and reading `benchmarks.jsonl` + `lm_studio.jsonl`
  against the `2026-06-29T17-38-36` baseline (no test suite exists).
- Watch for `"Context size has been exceeded"` if Phase 3/4 are left enabled;
  prefer lowering `MAX_CONCURRENT_LLM` or keeping modules off over silent
  per-turn cost blowups.

## Suggested order

1. specialization-metric-fix (cheap, makes everything else observable) →
2. emergent-role-trigger → 3. rules-voting-trigger → 4. meme-decouple →
5. phase34-validation-run → 6. prompt-housekeeping → 7. verify-rerun.
