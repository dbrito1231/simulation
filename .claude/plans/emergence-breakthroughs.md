# Emergence Breakthroughs — Plan

**Status: Approved — Phase A2 done; B1 F2 implemented (default off)** (`scripts/fork_compare.py` fork harness on branch
`emergence-breakthroughs`; A0/A1 determinism proof + pinning shipped). Per [AGENTS.md](../../AGENTS.md), implementation follows
the orchestrator → implementer → reviewer loop. The orchestrator owns splitting the
features below into phase prompts.

**Author context:** derived from a read of the full spec set (`specs/00`–`12`), the
`simulation/sim_engine/` mixin layout, and [docs/HANDOFF.md](../../docs/HANDOFF.md).
Every gap claim below cites the spec or code path it came from.

---

## 0. Decisions taken (user, at plan review)

These are settled. They constrain everything below; where a section conflicted with one
of these, the section has been updated to match.

| # | Question | Decision |
|---|---|---|
| D1 | Scope | **All five features.** |
| D2 | F1 `WIKI_MEMORY` prerequisite soak | **Soak on the live 24/7 server** — flip the flag on the running instance, observe, decide. |
| D3 | F5 determinism effort | **Spend whatever it takes.** Determinism is a hard requirement, multiple phases if needed, engine internals in scope if the tick loop or executor needs pinning. |
| D4 | F5 if determinism is unreachable | **Drop F5. Do not ship a weaker version.** N-run averaging with variance is explicitly rejected. |
| D5 | Consequence of dropping F5 | **Pause and re-plan** — but **F1 runs in parallel** and is not blocked (see D6). |
| D6 | Serialization | F5 is a hard gate on **F2, F3, F4**. **F1 proceeds in parallel** with the determinism work: low risk, own flag, reuses an existing call budget. |
| D7 | Flag end state | **Default-on after each feature's gates pass.** Each flip comes to the user with soak evidence attached. |
| D8 | Git | **One long-running branch for all five**, single PR at the end. |
| D9 | F3 prompt growth | **Measure and report**, no pre-set ceiling. Orchestrator brings the measured number to the user before the phase is accepted. |
| D10 | F4 rule/belief scoping audit | **Standalone read-only phase with its own reviewer pass**, reported to the user before any F4 code is planned. |
| D11 | Implementer surface | **Cursor (Composer 2.5).** This session produces copy-pasteable phase prompts; the user runs them against `.cursor/skills/`. |
| D12 | Server posture | **Keep the server running 24/7** throughout. Restarts happen at phase end, not as a standing stop. |

### Two consequences worth stating plainly

- **D7 + D8 interact.** The 24/7 server tracks `main`; a long-running feature branch means
  no flag flip is live until the branch merges. "Default-on after gates pass" therefore
  means *set True on the branch*, with real 24/7 exercise beginning at merge. The one
  exception is D2's F1 soak, which toggles `WIKI_MEMORY` — an **existing** flag already on
  `main` — so that soak can run against the live server immediately, independent of the
  branch.
- **D12 + engine phases.** Editing `sim_engine/` or `server.py` while the server runs is
  safe (the running process holds already-imported modules), but changes are not live
  until restart. Every phase that restarts must end with the single-instance check from
  [CLAUDE.md](../../CLAUDE.md#commands) — at most one container **or** one native
  `simulation/server.py` (a `uv run` wrapper + interpreter pair is **one** instance).

---

## 1. Thesis

The world already has depth in four areas:

- **Governance** — rules, ballots, Daily Council, succession, treaties
  ([09](../../specs/09-systems-society.md), [10](../../specs/10-path1.md))
- **Economy** — goods, market pricing, coin/mint, caravans, sinks
  ([08](../../specs/08-systems-economy.md))
- **Ecology** — weather, wildlife, terraform, seasons, disasters
  ([05](../../specs/05-world.md), [02](../../specs/02-engine-core.md))
- **Operator layer** — Sovereign God mode + Divine Matrix, 10 phases
  ([02](../../specs/02-engine-core.md), [11](../../specs/11-viewer.md), [12](../../specs/12-ops.md))

What is comparatively thin is the **cognitive and evidentiary** layer:

1. Agents hold no model of each other beyond a three-valued relationship valence.
2. Knowledge dies with its owner — one skill survives, in a 12-slot Library.
3. Trade cannot create obligation, so specialization has no economic pressure behind it.
4. A second settlement is an economic annex, never a divergent culture.
5. There is no way to prove any observed behavior emerged, or that a change improved
   anything — the repo has no test suite ([CLAUDE.md](../../CLAUDE.md#commands)) and
   several measurement gates are explicitly deferred.

The five features below address exactly those five points, in that order of subject
matter. Recommended *execution* order is different — see §8.

---

## 2. Global constraints (apply to every feature)

**MUST:**

- SDD: update the owning `specs/` file in the same change as behavior code.
- Every feature ships behind its own module-level flag in
  `simulation/sim_engine/constants.py`, **default off**, echoed in `/state`
  `config.flags` where the viewer needs it. Flag-off must be byte-identical to today
  (the `WIKI_MEMORY` one-flag-revert guarantee in
  [03-cognition.md](../../specs/03-cognition.md#wiki-memory) is the precedent to copy).
- Anything adding an agent action keeps the **action-sync invariant**
  ([01-architecture.md](../../specs/01-architecture.md#action-sync-invariant)):
  `DECISION_ACTIONS` / `DECISION_SCHEMA` / `SYSTEM_PROMPT` (server.py + prompts.py),
  `apply_decision()` + payload `available_actions` (`mixin_decisions.py`,
  `mixin_think_job.py`), `ACTION_LABELS` (`viewer/sidebar.js`), and
  [07-actions.md](../../specs/07-actions.md) — all in one change.
- Anything adding persisted state adds a `restore_state()` migration path
  ([02-engine-core.md](../../specs/02-engine-core.md#persistence)) so old `state.db`
  saves keep loading.
- Prompt additions respect existing budgets: `MEMORY_PROMPT_CHAR_BUDGET = 900`,
  `MAX_BEHAVIOR_NUDGES = 3`, `CHRONICLE_PROMPT_ENTRIES = 3`. Measured routine prompt is
  ~3,100–3,400 tokens ([03](../../specs/03-cognition.md)); no feature may push a routine
  turn materially past that without an explicit measurement.
- Viewer stays a pure renderer ([11](../../specs/11-viewer.md)) — no decisions,
  movement, or mutation in the browser.

**MUST NOT:**

- Add a new LLM call site or timer where an existing budget can carry the work.
  `sim-fast` contention is a known, measured regression source
  ([03-cognition.md](../../specs/03-cognition.md), migration note): routing decisions to
  `sim-fast` drove `piano_module_drops` from ~9% to ~25–38%.
- Route anything new to `sim-smart`'s pool without a contention measurement — that pool
  serves every agent decision (`MAX_CONCURRENT_LLM = 3`).
- Leak private state into `/state`. Divine private surfaces (omens, masks, gates,
  threads) are precedent for how privacy boundaries are drawn
  ([09](../../specs/09-systems-society.md), [12](../../specs/12-ops.md)).
- Introduce a new engine lock or hold `self.lock` across an LLM call.

---

## 3. Feature 1 — Testament (generational knowledge inheritance)

### Gap (verified)

- Births exist and are gated on food surplus, housing headroom, and two ally adults
  ([06-agents.md:160](../../specs/06-agents.md:160)); a newborn inherits
  `NEWBORN_GOODS_SHARE = 0.15` of a parent's **goods only**.
- On death, `CULTURE_ENABLED`'s Library persists **one** best skill, capped at
  `LIBRARY_KNOWLEDGE_CAP = 12` entries, oldest retiring first
  ([09-systems-society.md:539](../../specs/09-systems-society.md:539)).
- `agent["memory"]` tiers and `agent["memoryWiki"]` sections
  (`relationships`/`goals`/`lessons`, `WIKI_SECTION_CHAR_CAP = 300`) are destroyed with
  the agent. Nothing carries lessons across a generation boundary.

Net effect: generation 3 knows no more than generation 1. That is the central
Sid-parity claim the sim cannot currently make.

### Feature

- New `civilization["testament"]` — a bounded ring of short, attributed lesson lines
  (`{text, author, frame, generation}`), cap as a new constant next to `CHRONICLE_CAP`.
- **Deathbed merge:** on agent death, fold that agent's `memoryWiki["lessons"]` (and
  optionally a single `relationships` line) into the testament ring, deduplicated
  against existing entries, each line hard-truncated at `WIKI_SECTION_CHAR_CAP`.
- **Inheritance:** a newborn seeds `memoryWiki` from the parent's wiki plus the newest
  testament entries, subject to the same section caps.
- **Prompt surface:** one additional bounded line alongside the existing
  `Village history:` line, capped by entry count the way `CHRONICLE_PROMPT_ENTRIES`
  is — raising the ring size must never change prompt length.
- **Benchmark:** `cultural_carryover` — testament entries still present N generations
  after authorship, plus authored-vs-surviving ratio — into the existing
  `_sample_benchmarks()` (`mixin_decisions.py:53`, `BENCHMARK_TICK_FRAMES = 600`).

### Call budget

**Zero new LLM call sites.** `WIKI_MEMORY`'s `_run_wiki_memory_merge()` already owns a
merge prompt, a deterministic line-prefix parser, and the `is_scaffold_text` poisoning
guard, on a one-call-per-`MEMORY_TICK_FRAMES = 1800` round-robin budget. The deathbed
merge reuses that same prompt shape and parser. If a death occurs with no wiki content
(flag was off, or agent too young), the merge is skipped — deterministic, no call.

### Dependency (D2, D6)

Meaningful only with `WIKI_MEMORY` on (currently default `False`, never run as a
default). **Prerequisite sub-phase:** flip `WIKI_MEMORY = True` on the **live 24/7
server** and observe for a set window — `llm.jsonl` for merge-call behavior and parse
failures, `benchmarks.jsonl` for memory-store size, `activity.jsonl` for the
`reconciled a memory` contradiction line. The flag is an existing `main` constant, so
this soak needs no branch and no second instance; reverting is a one-line flip plus
restart.

If the soak fails, F1 is re-planned against the `longTerm` tier rather than abandoned.

**F1 is not gated on F5** (D6) — it proceeds in parallel with the determinism work.

### Scope

| | |
|---|---|
| **Owning specs** | [06-agents.md](../../specs/06-agents.md) (agent data shape, lifecycle), [09-systems-society.md](../../specs/09-systems-society.md) (culture, testament ring, benchmark) |
| **In-scope files** | `sim_engine/constants.py`, `mixin_lifecycle.py` (death + birth paths), `mixin_decisions.py` (wiki merge, benchmark sample), `mixin_persistence.py` (persist + restore migration), `_server/prompt_format.py` (prompt line), `server.py` (template slot) |
| **Out of scope** | New actions; changing `LIBRARY_KNOWLEDGE_CAP` semantics; the `longTerm` tier; any viewer surface |
| **Acceptance** | Flag-off byte-identical to baseline; deathbed merge produces ≤ cap entries with truncation enforced; newborn wiki non-empty after inheritance; restore round-trips the ring; new smoke `scripts/testament_smoke.py` covering merge/inherit/cap/restore, deterministic and Ollama-free |

**Risk:** Low. Reuses an existing parser and call budget. Main risk is prompt growth —
must be measured, not assumed.

---

## 4. Feature 2 — Theory of Mind (agents modeling other agents)

### Gap (verified)

- `nearby_agents` / `format_nearby_agents` supply ground-truth facts plus an
  `ally`/`neutral`/`rival` valence ([06](../../specs/06-agents.md),
  [09](../../specs/09-systems-society.md) `socialTies`). There is no representation of
  *what A believes about B*.
- `found_belief` / beliefs are about **memes**, not people
  ([09](../../specs/09-systems-society.md#memes_enabled)).
- PIANO modules ([03-cognition.md](../../specs/03-cognition.md)) are self-directed
  cognition only, advisory to the Cognitive Controller.

### Feature

- New bounded `agent["peerModel"][peerIdStr] = {wants, good_at, owes_me, trust, frame}` —
  short strings plus one float, hard-capped in both per-peer char length and number of
  peers tracked (new constants; evict least-recently-updated).
- Maintained by a **new module inside the existing PIANO fan-out** — same
  `MODEL_FAST`, `PIANO_MODULE_MAX_TOKENS = 90`, `PIANO_MODULE_TIMEOUT_S = 15`,
  `PIANO_CONCURRENT_LLM = 2` budget and the same drop-on-timeout semantics as every
  other module. It is one more module in the rotation, **not** an extra call per turn.
- **Prompt surface:** one short line per *nearby* peer only, folded into the existing
  nearby-agents section. Never the full table.
- **Benchmark — the reason to build this:** `peer_prediction_accuracy`. When a module
  report states an expectation about a peer, record it; score it against that peer's
  next applied action. This is a direct emergence signal, materially stronger than the
  current role-specialization-entropy proxy.

### Why it matters beyond the metric

Reputation, deception, and trade-partner selection become emergent rather than
scripted. It also gives Divine Matrix memory surgery and `belief_plant`
([03](../../specs/03-cognition.md), [06](../../specs/06-agents.md)) a real substrate to
corrupt — currently they can only plant free-floating memory lines.

### Scope

| | |
|---|---|
| **Owning specs** | [03-cognition.md](../../specs/03-cognition.md) (PIANO module registry, prompt section), [06-agents.md](../../specs/06-agents.md) (agent data shape), [09-systems-society.md](../../specs/09-systems-society.md) (benchmark) |
| **In-scope files** | `sim_engine/constants.py`, `mixin_think_job.py` (payload + prompt section), `mixin_decisions.py` (module registry, benchmark scorer), `mixin_persistence.py` (persist + migration), `_server/prompt_format.py`, `server.py` (template slot) |
| **Out of scope** | New actions; changing `PIANO_CONCURRENT_LLM`; any behavior that *acts* on `peerModel` deterministically (it is advisory prompt context only, exactly like existing module reports) |
| **Acceptance** | Flag-off byte-identical; per-peer and peer-count caps enforced; module drop leaves prior model intact (no wipe on timeout); `scripts/soak_monitor.py` run showing `module_refresh_failures` / `piano_module_drops` **not** materially worse than a flag-off soak of the same length — this is a hard gate, not a nice-to-have |

**Risk:** Medium — contention. Adding a module to a rotation that already drops work is
the exact failure mode [03](../../specs/03-cognition.md) documents. The soak comparison
is the gate; if drops regress, the module's rotation frequency is reduced before
anything else is tuned.

---

## 5. Feature 3 — Contracts and escrow

### Gap (verified)

- `trade_resource` ([07-actions.md](../../specs/07-actions.md)) is instantaneous,
  single-resource, one-sided: it moves the **actor's most-abundant** resource, priced by
  market if `ECONOMY_ENABLED` and a market is working, else 1-for-nothing barter.
- Coin and a mint exist ([08-systems-economy.md:576](../../specs/08-systems-economy.md:576))
  but nothing is ever *owed* — there is no debt, deadline, or default anywhere.
- `assign_task` is elder-only and requires an idle target
  ([07](../../specs/07-actions.md)). No agent can hire another.

### Feature

Two new actions plus engine-held escrow:

- `offer_contract` — params `target` (agent name or `"open"`), `contract`
  (`{want: resource_id, qty, pay_coin, deadline_frames}`). Escrow of `pay_coin` is
  debited at offer time and held by the engine.
- `accept_contract` — params `target` (contract id). Binds the acceptor.
- **Settlement (deterministic, on tick):** delivery of the wanted goods inside the
  deadline pays out escrow; expiry refunds the offerer and writes a relationship hit on
  the defaulting acceptor (neutral→rival, reusing the exact shape `confront_agent`
  already applies), plus an `activity` line.
- **Benchmarks:** `contracts_opened`, `contracts_fulfilled`, `contract_default_rate`.

### Why it matters

Specialization today happens because roles say so. Contracts create a *demand signal*
that makes specialization pay, and defaults create the first endogenous grievance —
which then feeds `confront_agent`'s rival social gate
([09](../../specs/09-systems-society.md#bounded-agent-conflict-confront_agent)) and gives
the rule system something real to legislate about. It closes the loop between
[08](../../specs/08-systems-economy.md) and [09](../../specs/09-systems-society.md),
which are currently near-disjoint.

### Scope

| | |
|---|---|
| **Owning specs** | [07-actions.md](../../specs/07-actions.md) (the two actions — sole action catalog), [08-systems-economy.md](../../specs/08-systems-economy.md) (escrow, settlement, coin flow), [09-systems-society.md](../../specs/09-systems-society.md) (relationship consequence, benchmarks), [03-cognition.md](../../specs/03-cognition.md) (`DECISION_SCHEMA` `contract` object, `normalize_decision` validation) |
| **In-scope files** | `server.py` (`DECISION_ACTIONS`, `DECISION_SCHEMA`), `simulation/prompts.py` (`SYSTEM_PROMPT` rules + one worked example), `_server/decision_validation.py`, `mixin_decisions.py` (`apply_decision` branches), `mixin_think_job.py` (`available_actions` gating + prompt state), `mixin_structures_economy.py` (settlement tick), `mixin_persistence.py`, `viewer/sidebar.js` (`ACTION_LABELS`, display only), `sim_engine/constants.py` |
| **Out of scope** | Multi-resource baskets; contract renegotiation; interest/lending; any new viewer panel; contracts across settlements (deliberately deferred until Feature 4 settles rule scoping) |
| **Acceptance** | Action-sync invariant satisfied across all six surfaces in a single change; escrow conserves coin exactly across offer/fulfil/default/expiry (no mint, no burn) — asserted in a new `scripts/contract_smoke.py`; open contracts survive save/restore; `normalize_decision` rejects malformed `contract` objects to the role fallback with a `*_rejection_note` per existing convention; **measured routine-prompt token delta reported to the user before the phase is accepted (D9)** |

### Prompt budget (D9)

No pre-set ceiling. The implementer **measures** the routine-prompt token delta against
the current ~3,100–3,400 baseline and reports the actual number; the orchestrator brings
it to the user as an accept/trim decision. Keep the additions terse by default — one
worked example, not one per action — so the measured number starts low rather than
needing to be walked back. If growth proves unacceptable,
`SYSTEM_PROMPT_AT_LOAD_TIME` ([03](../../specs/03-cognition.md#load-time-rulebook))
is the documented ~3k-token reclamation path, but it carries its own unrun A/B gate and
is **not** part of this plan.

**Risk:** Medium-high. Widest blast radius of the five — six sync surfaces, coin
conservation, persistence migration, and a new tick-time settlement path. Should be
sub-phased (schema+validation → apply+settlement → prompt+viewer label → smoke).

---

## 6. Feature 4 — Schism (cultural divergence between settlements)

### Gap (verified)

Every multi-settlement mechanic is already built: frontier and coastal-pair founding
([05-world.md:77](../../specs/05-world.md:77)), the ocean transit corridor,
`deliver_caravan` and settlement stores, treaties with tariffs bounded `0`–`0.25`
([10-path1.md:110](../../specs/10-path1.md:110)). But rules, beliefs, and leadership are
**global** — a second settlement is an economic annex of the first, never a divergent
culture. Divergence never happens.

### Feature

- **Trigger (deterministic, no new LLM call):** a cluster of agents sharing a belief
  that an enacted rule contradicts, holding mutual `ally` ties to each other and `rival`
  toward the elder, above a minimum cluster size — evaluated on an existing governance
  tick.
- **Secession:** the cluster migrates to a new (or existing frontier) settlement,
  forking the enacted ruleset and carrying their beliefs, and elects their own elder via
  the existing `succession` ballot machinery
  ([09](../../specs/09-systems-society.md#succession-lifecycle_enabled-governance-slice)).
- **Aftermath:** the two cultures interact only through the existing
  treaty / caravan / tariff surface. Chronicle gets a new `schism` milestone kind.

### The real work

Not the trigger — the **scoping**. `civilization["rules"]` and beliefs are global today;
making them per-settlement is invasive and is where the risk lives, especially on
`restore_state()` for saves written before scoping existed. This feature is
"reconfiguration of parts that already exist," but the reconfiguration is structural.

### Mandatory audit phase (D10)

Before any F4 code is planned, a **standalone read-only phase** enumerates every
read and write of `civilization["rules"]` and belief state across `sim_engine/`,
`_server/`, and `server.py`, and identifies every persistence/restore path that touches
them. That audit gets **its own reviewer pass** and is reported to the user. F4
implementation phases are only written after the audit lands — the scope table below is
provisional until then.

### Scope

| | |
|---|---|
| **Owning specs** | [09-systems-society.md](../../specs/09-systems-society.md) (rule/belief scoping, schism trigger, succession reuse, Chronicle kind), [05-world.md](../../specs/05-world.md) (settlement founding on secession), [10-path1.md](../../specs/10-path1.md) (inter-settlement consequence) |
| **In-scope files** | `mixin_governance_culture.py` (trigger + fork), `mixin_world_state.py` / founding path, `mixin_diplomacy.py`, `mixin_council_growth.py` (per-settlement council seating), `mixin_persistence.py` (scoping migration — the critical piece), `sim_engine/constants.py` |
| **Out of scope** | War, raids, or any inter-settlement violence; per-settlement currencies; forced reunification; a viewer settlement-comparison panel |
| **Acceptance** | Pre-scoping `state.db` saves restore into single-settlement-scoped rules with no behavior change; a scripted schism produces two settlements with independently enacted rulesets and two distinct elders; treaties/tariffs still function across the pair; new `scripts/schism_smoke.py`; existing `scripts/path1_smoke.py` and `scripts/sid_parity_smoke.py` still green |

**Risk:** Medium-high, concentrated entirely in rule/belief scoping + restore migration.
Recommend a standalone read-only audit sub-phase (enumerate every read/write of
`civilization["rules"]` and belief state) **before** any code is written.

---

## 7. Feature 5 — Fork-and-compare experiment harness

### Gap (verified)

The specs are full of measurement gates that cannot currently be run:

- Load-time rulebook A/B — `SYSTEM_PROMPT_AT_LOAD_TIME` ships dark pending an A/B soak
  ([03-cognition.md](../../specs/03-cognition.md#load-time-rulebook)).
- Free-prose compiler contention protocol — "not yet run," and HANDOFF says explicitly:
  *do not claim green contention results* ([12-ops.md](../../specs/12-ops.md)).
- Binding Voice guidance measurement gate ([03](../../specs/03-cognition.md)).
- [00-overview.md](../../specs/00-overview.md) states flatly that intervened runs "are
  not comparable to untouched autonomous runs."

And there is **no test suite, linter, or build step** ([CLAUDE.md](../../CLAUDE.md#commands)) —
verification is smoke scripts plus watching logs. So "did this change improve emergence?"
is currently unanswerable.

Meanwhile the ingredients already exist: god reload checkpoints (Divine Matrix Phase 10,
`GOD_CHECKPOINT_ROOT`, create/restore/cap), the dark `GOD_DEJA_VU_REPLAY` path, per-run
`benchmarks.jsonl`, `scripts/llm_replay_bench.py`, and `scripts/soak_monitor.py`.

### Feature

A headless harness under `scripts/`:

1. Load a checkpoint (or cold-start with a fixed seed).
2. Fork it into N worlds differing in **exactly one** variable — a flag, a divine
   intervention, a prompt variant.
3. Run each fork headless at uncapped tick rate, with the LLM path served either from a
   recorded decision stream (`llm_replay_bench.py` shape) or a fixed seed with the LLM
   disabled — so forks are comparable rather than noise-dominated.
4. Diff the resulting `benchmarks.jsonl` trajectories into one comparison report.

### Why this is the highest strategic value

Every other feature in this plan currently ships on judgment plus a smoke script. This
one makes their effects **measurable**, retroactively unblocks four deferred gates, and
is the difference between a demo and a research instrument. It is also the only feature
here with **zero risk to the running simulation**: pure `scripts/` plus a checkpoint-load
entry point — no engine behavior change, no new action, no viewer surface, no flag in the
live path.

### Scope

| | |
|---|---|
| **Owning specs** | [12-ops.md](../../specs/12-ops.md) (scripts table, the harness contract and its honest limitations) |
| **In-scope files** | New `scripts/fork_compare.py` (+ helpers); read-only reuse of the existing checkpoint create/restore path; possibly a small non-behavioral headless entry point if `SimEngine` cannot currently be driven without Flask |
| **Out of scope** | Any change to god-mode behavior, checkpoint semantics, or `GOD_DEJA_VU_REPLAY`'s default; running the deferred A/B protocols themselves (that is follow-on work, and its results must be reported as measured, never assumed); any claim that replayed forks are equivalent to live LLM runs |
| **Acceptance** | Two forks with **identical** inputs produce **bit-identical** benchmark trajectories. This is pass/fail, not a target. One-variable forks produce a readable diff; harness never writes to the real `state.db` (same safety property `scripts/god_mode_smoke.py` already guarantees) so it is safe to run beside the 24/7 server. |

### Determinism is the whole feature (D3, D4)

RNG state, tick-thread scheduling, and executor ordering must all be pinned or the diff
is noise rather than signal.

- **Effort is unbounded (D3).** Determinism is a hard requirement, worth multiple phases,
  with engine internals in scope if the tick loop or executor ordering needs pinning.
  Any such engine change is still additive and flag-guarded, and must leave the live
  behavior path unchanged — pinning is for the headless harness, not the running world.
- **No weaker version (D4).** N-run averaging with reported variance is **explicitly
  rejected**. If bit-identical forks cannot be achieved, F5 is dropped outright.
- **Suggested first phase:** the cheap proof — same seed, same checkpoint, two headless
  runs, diff the benchmarks — so the size of the determinism problem is known before
  harness code is written. This is a sequencing suggestion, not a cap on D3's effort.

**Risk:** Low to the product (nothing touches the live sim path), high to the schedule —
this is now the gate on F2/F3/F4 (D6).

---

## 8. Sequencing (per D5, D6)

Two tracks. **Track A gates Track B.**

### Track A — the measurement gate (blocking)

| Step | Work | Exit condition |
|---|---|---|
| A0 | Cheap determinism proof: same seed, same checkpoint, two headless runs, diff benchmarks | Known size of the determinism problem |
| A1 | Pin RNG state / tick-thread scheduling / executor ordering — as many phases as it takes (D3) | Two identical forks produce bit-identical trajectories |
| A2 | Build `scripts/fork_compare.py` on top of that | One-variable forks produce a readable diff |

**If A1 cannot be reached: F5 is dropped (D4), and the plan pauses for re-planning (D5).**
F2/F3/F4 do **not** proceed unmeasured. F1 is unaffected.

### Track B — features

| Order | Feature | Gated on | Risk |
|---|---|---|---|
| B0 | **F1 — Testament** (+ its live `WIKI_MEMORY` soak) | **Nothing — runs in parallel with Track A (D6)** | Low |
| B1 | **F2 — Theory of Mind** | Track A complete | Medium (contention) — **implemented** (`THEORY_OF_MIND_ENABLED` default off; default-on needs soak gate) |
| B2 | **F3 — Contracts** | Track A complete | Medium-high (six sync surfaces, coin conservation) |
| B3 | **F4 — Schism** | Track A complete **+ the D10 audit phase reviewed and reported** | Medium-high (rule scoping, restore migration) |

Within Track B the order is unchanged from the original risk ordering: F3 before F4 so
the economy has been exercised under obligation before rule scoping is torn up.

Each feature stays independently revertable via its own flag. F1 depends on
`WIKI_MEMORY`; contracts-across-settlements is deliberately deferred until after F4.

**Branch (D8):** all of the above lands on **one long-running branch**, single PR at the
end. Track A and B0 commit into the same branch despite running in parallel.

---

## 9. Explicit non-goals

- **No war, raids, or inter-settlement violence.** `confront_agent` is deliberately
  bounded, non-lethal-by-default, and socially gated
  ([09](../../specs/09-systems-society.md)). Nothing here widens it.
- **No new god/divine surface.** All five are agent-side or ops-side. The Divine Matrix
  is feature-complete for the purposes of this plan.
- **No changes to `MAX_CONCURRENT_LLM`, `PIANO_CONCURRENT_LLM`, or model routing.**
- **No refactors.** These are additive features under flags, per KISS.
- **No touching `docs/archive/`.**
- **Deferred runner-up — the Historian.** An in-world historian compiling the Chronicle
  ring (`CHRONICLE_CAP = 100`) into written saga chapters stored in the Library, prompt-
  visible to agents and exported as a human-readable run report. Real value — it answers
  "what did last night's run actually produce?" better than JSONL does — but lower
  mechanical impact than the five above, and it competes for the same `sim-fast` budget
  as F2. Revisit after F2's contention soak lands, when the true headroom is known.

---

## 10. Verification posture (all features)

Per [AGENTS.md](../../AGENTS.md) and [CLAUDE.md](../../CLAUDE.md#commands):

```bash
uv run python scripts/sid_parity_smoke.py
```

```bash
uv run python scripts/path1_smoke.py
```

- Existing smokes must stay green for every phase; each feature adds its own
  deterministic, Ollama-free smoke under `scripts/` and a row in
  [12-ops.md](../../specs/12-ops.md)'s scripts table.
- Flag-off equivalence is proven, not assumed — the `god_mode_smoke.py` precedent of
  building an all-neutral run and asserting it is byte-identical to a feature-off
  baseline is the standard to copy.
- Contention-sensitive features (F2, and F5 if it ever touches a live pool) require a
  `scripts/soak_monitor.py` run compared against a flag-off soak of equal length before
  any default-on proposal.
- Single-instance rule applies to any phase that starts or restarts the server: verify at
  most one `gitserv-sim` container **or** one native `simulation/server.py` (a `uv run`
  wrapper + interpreter pair is **one** instance) before reporting done.

---

## 11. Open questions

**None.** All twelve decisions are recorded in §0 and folded into the sections above.

Standing assumptions the orchestrator should treat as settled unless the user says
otherwise:

- Reviewer passes follow every implementer phase, per [AGENTS.md](../../AGENTS.md) —
  including the D10 audit phase, which is read-only but still reviewed.
- Implementer prompts are authored here and run in **Cursor / Composer 2.5** (D11);
  this session does not write code.
- The `docs/HANDOFF.md` "In-flight" table should gain a row for this campaign when the
  first phase starts, and `docs/HANDOFF.md` plus [12-ops.md](../../specs/12-ops.md)'s
  scripts table are updated as each new smoke lands.

Escalation path is unchanged: implementer and reviewer doubts go to the orchestrator,
who asks the user. Neither subagent asks the user directly, and neither invents an
answer.
