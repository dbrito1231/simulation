# Response Plan: Copilot Intelligence Audit

Triage of [copilot-audit.md](copilot-audit.md) against the current
`feat/server-authoritative-engine` branch, and the plan for the items that
survive triage. **The master plan remains
[civilization-emergence-plan.md](civilization-emergence-plan.md)** — this
document routes audit findings into it rather than opening a second
workstream; adopted items are folded into the master plan's phases and
executed through its Part 7/Part 8 subagent machinery.

## Why triage first

The audit inspected `simulation/index.html` and describes the **legacy
client-side simulation** that file still contains — not `sim_engine.py`, the
server-authoritative engine that actually runs the world on this branch. It
also predates (or missed) Phases A–B and their three loop-backs. Roughly half
its findings were fixed before it was written. That is itself a finding: the
dead client-side sim code in `index.html` is misleading auditors (item C5).

## Bucket A — Already fixed on this branch (no action; evidence cited)

| Audit claim | Reality on this branch |
|---|---|
| "Single global activeProject slot" (#1, top ROI #2) | Per-district `districtProjects`, `MAX_CONCURRENT_PROJECTS = 3` (sim_engine.py:349) |
| "Resources manufactured from nothing, no depletion" (#2, top ROI #1) | Phase B ecology: `districtStocks`, 2× depletion, regrowth, yield scaling, terraform recovery |
| "Memory is 5 lines per agent" (#7, ROI #3) | Tiered working/short/long memory (caps 6/12/8) + server-side MemoryStore (1,200 entries) + reflection |
| "No failure reasons — agents repeat mistakes blindly" (§4) | `rejection_note` → `lastBlueprintRejection` / `lastGatherRejection` / `lastProjectRejection` / `lastCraftRejection` nudges |
| "Caps permanently foreclose invention; no retirement" (#15) | `_maybe_retire_blueprint` (approved cap) + project-type deferral (loop-back #3) |
| "No map growth or exploration" (#14) | District founding on frontier plots + `extend_beach` terraform claims new land |
| "Client is sole state owner; no server world model" (§3) | The engine IS the world model; the browser is a viewer. (CLAUDE.md's stale "Data flow" line fixed as part of C5.) |
| "Elder never governs, no voting" (#19, partially) | `RULES_ENABLED` voting scaffold exists (tax rule); depth is Phase F scope |

## Bucket B — Valid, already scheduled in the master plan (no new work)

| Audit item | Master-plan home |
|---|---|
| No economy/currency/negotiated trade (#9, §5, ROI #5) | Phase E (market & property) |
| No population/birth/death/generations (#3, #4, §7) | Phase F (population lifecycle) |
| No tech tiers/prerequisites (§8) | Phase D (technology tiers & eras) |
| Civilization level is a vanity counter (#17) | Phase D (eras replace level) |
| History never feeds back into behavior (#20, §12) | Phase G (chronicle & lore) |
| No storage/spoilage stakes (§10 partially) | Phase C (goods & needs) |
| Weather/seasons (#13, §6) | Was listed in Part 2 F2 but unphased — now explicitly Phase C scope (slow modifiers on district stocks) |

## Bucket C — New items adopted from the audit

| # | Item | Disposition |
|---|---|---|
| C1 | **Structure decay/disasters — consequence permanence** (#5, ROI #4). Nothing can regress; no stakes. | Added to Phase C scope: structures gain condition, decay without upkeep, a repair verb, and rare disasters — with the standard deterministic-escape invariant (decay must be repairable/rebuildable). |
| C2 | **Relationships & personality are decorative** (#8, §11). | Added to Phase E scope (relationship modifies trade terms/refusal) and Phase F (task/succession bias). Personality evolution → Phase G. |
| C3 | **Rejected-blueprint permanent blacklist** (#16) — verified still true (`rejectedBlueprintIds` only ever grows). | Small standalone fix: rejected ids expire after a long cooldown (amnesty), mirroring `_maybe_retire_blueprint`. Queued as part of the next loop-back/phase prompt. |
| C4 | **LLM settings suppress creativity** (§4: 512 tokens, temp 0.4, thinking disabled). | Deliberate for gemma's routine turns — but the audit has a point for *authoring* turns. Added to master plan Part 6: an A/B replay experiment raising temperature (~0.8) and token budget on invention-only prompts, judged by blueprint validity + novelty rates in the logs. Not a blind change. |
| C5 | **Legacy client-sim code in index.html** (root cause of the audit's staleness; also real divergence risk). | Cleanup task: strip the dead client-side simulation/decision paths from index.html, leaving the viewer; fix CLAUDE.md's stale "Data flow" paragraph. Scheduled AFTER Phase B passes (touching index.html mid-exam adds noise). |
| C6 | **Fixed action-verb enum is "anti-emergent"** (§3). | **Rejected with rationale:** the fixed verb list is the X3 constraint working as designed — the LLM chooses among verbs, deterministic code executes them; emergence is engineered into world state (stocks, functions, tiers), not verb invention. A 7.5B model inventing verbs means inventing unvalidated world mutations — the exact "silent script" failure the audit criticizes elsewhere. Revisit only at Phase G+ with a much stronger model (Part 6 replay gate). |

## Execution: subagent split (via the existing Part 7/Part 8 machinery)

No new pipeline — items flow through the overnight cycle's implement → review
→ soak → audit relay. Phase gating unchanged: **nothing below starts until
Phase B passes its soak exam** (tonight's cycle).

1. **Recon subagent — "audit reconciliation"** (read-only, first): verify
   every Bucket A row against HEAD (the table above cites evidence, but the
   subagent re-derives it fresh), and produce the C1/C2 change maps for the
   phase scopes they joined. Output lands in this doc.
2. **Implementation subagents** (one per item, sequenced by the cycle):
   - C3 (blueprint amnesty) — bundled into the next Phase B loop-back prompt
     or, if Phase B passes, into the Phase C relay prompt as a warm-up item.
   - C5 (index.html legacy strip + CLAUDE.md data-flow fix) — its own
     next-prompt.md iteration right after Phase B passes; verified by the
     standard soak (viewer must render identically) plus a diff of served
     pages.
   - C1, C2, C4 — not standalone: they ride Phase C, E/F, and the Part 6
     model-check agent respectively, via the amended phase scopes.
3. **Audit subagent** (morning stage, unchanged): the standing Part 5
   questions already cover the audit's scoring dimensions; its "does every
   noun have a verb" question is the same test the copilot audit called
   "decorative systems."

## Scorecard note

The audit's 0–100 scores (§17) were measured against the legacy architecture.
Re-scoring is only meaningful after Phase C+C5 land; the morning audits'
benchmark stream (`benchmarks.jsonl`: scarcity index, effect throughput,
specialization entropy, rule adherence, meme adoption) is the running
equivalent and already trends most of what the audit scored.
