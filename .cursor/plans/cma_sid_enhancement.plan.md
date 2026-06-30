---
name: CMA + Project Sid Enhancement (2D, no 3D/physical embodiment)
overview: "Fold the implementable ideas from two papers into this 2D browser sim: Project Sid/PIANO (docs/2024-10-31.pdf) and the Concurrent Modular Agent (docs/cma-concurrent-modular-agent-summary.md). Sid contributes concurrent cognitive modules + a Cognitive Controller bottleneck, multi-timescale memory, emergent specialization, amendable collective rules/voting, and cultural transmission. CMA contributes a shared vector-store memory with dedicated Memory Manager/Cleaner/Summarizer loops, asynchronous module-to-module natural-language messaging (subsumption-style), and a self-modifying meta layer (autobiographical memory, prompt modifier, module self-activation). CMA's external-service stack (ChromaDB + Docker + Mosquitto/MQTT + cloud LLMs) is replaced with in-process equivalents to honor this project's minimal/single-process/local-LM-Studio ethos. Every system is behind a feature flag for A/B comparison, matching the existing convention (SURVIVAL_ENABLED, USE_GOALS, ...). Companion overview: docs/project-sid-parity-roadmap.md."
todos:
  - id: flags-and-benchmarks
    content: "Phase 0 — index.html: add MEMORY_ENABLED, AGENT_MESSAGING, PIANO_MODULES, META_SYSTEM, EMERGENT_ROLES, RULES_ENABLED, MEMES_ENABLED, BENCHMARKS_ENABLED const flags near the existing ones (~index.html:361); server.py: add a benchmarks.jsonl stream to SessionLogger and a POST /log/benchmark endpoint"
    status: completed
  - id: memory-store-server
    content: "Phase 1 (CMA B + Sid 1.1) — server.py: in-process vector memory store (cosine over a hashing/embedding fn, persisted to logs/<ts>/memory.json), replacing ChromaDB/Docker; endpoints POST /memory/store (embed+save) and POST /memory/query (top-k retrieve); WM/STM/LTM tiers by recency+salience"
    status: completed
  - id: memory-agent-fields
    content: "Phase 1 — index.html: agent.memory = { working:[], shortTerm:[], longTerm:[] }; write actions/dialogue/observations on each frame tick; feed a compacted slice into build_agent_data (server.py:811) so the prompt carries recent + salient facts"
    status: completed
  - id: memory-manager-loops
    content: "Phase 1 (CMA E) — Summarizer (periodic LLM call: last N memories -> summary back to LTM) and Memory Cleaner (LLM prunes stale/duplicate entries to cap store size); frame-gated like updateSurvival via MEMORY_TICK_FRAMES"
    status: completed
  - id: message-bus
    content: "Phase 2 (CMA C/D) — index.html: in-process pub/sub message bus + per-agent inbox replacing MQTT/Mosquitto; agent.sendMessage(to, text) and inbox drained into the think payload; subsumption-style: an inbound message can pre-empt the current goal"
    status: completed
  - id: modules-and-cc
    content: Phase 3 (Sid 4.5 + CMA A) — split per-agent cognition into modules (Perception, Social, Desire/GoalGen, Reflection) that run on independent cadences via the existing think queue (MAX_CONCURRENT_LLM/drainThinkQueue); a Cognitive Controller call consumes a bottleneck object of module outputs and emits the single decision normalize_decision already validates
    status: completed
  - id: meta-system
    content: Phase 4 (CMA F) — Autobiographical Memory (periodic first-person life-story from last K memories), Prompt Modifier (rewrites an agent's appended system-prompt block from a meta report), Meta Report (per-agent module-activity + resource summary), and module self-activation/deactivation gated by the meta report
    status: completed
  - id: emergent-roles
    content: Phase 5 (Sid 1.2) — make role/specialty mutable; add switch_role action across AVAILABLE_ACTIONS (index.html:661), DECISION_ACTIONS/SYSTEM_PROMPT (server.py); drive from village-need signal (parse_project_shortfalls); roles.json becomes starting roles only
    status: completed
  - id: rules-voting
    content: Phase 6 (Sid 2.3) — civilization.rules registry; propose_rule + vote actions generalizing the existing pending-approval/merge logic (propose_blueprint/approve); enforce one mechanical rule (e.g. resource tax in contribute_resources/applyDecision at index.html:1507) so adherence is measurable
    status: completed
  - id: memes
    content: Phase 7 (Sid 3.4) — agent.beliefs set; probabilistic spread through talk/format_nearby_agents social channel; seed one rumor/religion and record adoption over time
    status: completed
  - id: benchmarks-metrics
    content: Phase 8 — compute + log specialization index (role entropy), rule-adherence rate, meme-adoption curve, memory-store size, module activation timeline; surface a minimal readout in the existing GUI panel
    status: completed
  - id: sync-actions-and-prompt
    content: Cross-cutting — keep every new action in sync across AVAILABLE_ACTIONS (index.html) and DECISION_ACTIONS/DECISION_SCHEMA/SYSTEM_PROMPT (server.py), per the existing rule in CLAUDE.md
    status: completed
isProject: false
---

# CMA + Project Sid Enhancement Plan (2D)

Goal: implement every implementable idea from **Project Sid / PIANO**
(`docs/2024-10-31.pdf`) and the **Concurrent Modular Agent**
(`docs/cma-concurrent-modular-agent-summary.md`) into this codebase, **excluding
3D / physical-embodiment visuals**. The two papers are architectural opposites
(Sid = centralized Cognitive Controller bottleneck; CMA = decentralized,
emergent coherence over a shared vector store + MQTT). This plan takes a
**hybrid**: CMA's modular fan-out and shared memory for richness, Sid's
Cognitive Controller and server-side `normalize_decision()` for coherence — the
combination best fits this project's "minimal and observable" mandate.

Everything is behind a feature flag (matching `SURVIVAL_ENABLED`, `USE_GOALS`,
`CRAFTING_ENABLED`) so each system can be toggled and A/B compared.

---

## Architecture decisions (how CMA's stack maps onto this project)

| CMA / Sid component | This project's implementation | Rationale |
|---|---|---|
| ChromaDB vector store (Docker, HTTP) | In-process cosine-similarity store in `server.py`, persisted to `logs/<ts>/memory.json` | Keeps the no-external-service, single-process ethos; swappable for Chroma later behind the same `/memory/*` endpoints |
| MQTT / Mosquitto broker | In-browser pub/sub bus + per-agent inbox | All agents already live in one browser frame; a broker adds a daemon for zero benefit here |
| asyncio + thread wrappers (CMA) | Existing `drainThinkQueue` + `MAX_CONCURRENT_LLM` queue | The bounded-concurrency queue already provides cross-agent concurrency; reuse it for module fan-out |
| Cloud LLMs (GPT-4 / deepseek) | Existing local LM Studio backend | Local-first is a core project constraint |
| PIANO Cognitive Controller bottleneck | A final CC think-call per agent feeding `normalize_decision()` | Preserves the post-hoc validation that already guarantees coherence |
| Per-module JSONL logging | Existing `SessionLogger` (+ new `benchmarks.jsonl`) | Already the project's primary debugging surface |

---

## Phases

### Phase 0 — Foundations
Add the feature flags and a metrics surface so every later phase is measurable
and toggleable. New `benchmarks.jsonl` stream in `SessionLogger` and a
`POST /log/benchmark` endpoint mirroring the existing `POST /log/event`.

### Phase 1 — Memory (CMA shared vector store + Sid WM/STM/LTM)
The biggest unlock: agents are currently amnesiac (`server.py` is stateless
between `/agent/think` calls). Add an in-process vector store with
`embed-on-write / query-on-read`, three recency/salience tiers, and CMA's
**Summarizer** + **Memory Cleaner** consolidation loops. Memory slices flow into
`build_agent_data()` so prompts finally carry continuity. Everything else builds
on this.

### Phase 2 — Asynchronous module/agent messaging (CMA MQTT, subsumption)
An in-process message bus gives modules and agents direct natural-language
messaging on top of shared memory — CMA's claimed novelty over a single-shared-
state design. Subsumption-style: an inbound message can pre-empt a running
`goal` (ties into `stepGoal`).

### Phase 3 — Concurrent modules + Cognitive Controller (Sid PIANO + CMA fan-out)
Replace the single think-call with a small fan-out of per-agent modules
(Perception, Social, Desire/Goal-Gen, Reflection) running on independent
cadences through the existing queue, summarized into a bottleneck object that a
final **Cognitive Controller** call turns into one decision. Keeps
`USE_GOALS`/`stepGoal()` as the fast-reflex path. This is the only true
architectural change and multiplies LLM cost — pair with a smaller roster or a
higher `LLM_MIN_GAP_MS`.

### Phase 4 — Meta system (CMA's open-ended-drift layer)
The most novel CMA contribution: **Autobiographical Memory** (periodic
first-person life-story), **Prompt Modifier** (rewrites an agent's appended
system-prompt block from a meta report), **Meta Report** (module-activity +
resource summary), and module **self-activation/deactivation**. Realizes CMA's
"true self emerges from memories" without fine-tuning.

### Phase 5 — Emergent specialization (Sid benchmark #1)
Make `role`/`specialty` mutable via a `switch_role` action driven by village
need; `roles.json` becomes the *starting* distribution only.

### Phase 6 — Collective rules / voting (Sid benchmark #2)
A `civilization.rules` registry with `propose_rule` + `vote`, generalizing the
existing elder-only blueprint approval into quorum tallying, plus one mechanical
rule (a resource tax) so adherence is measurable.

### Phase 7 — Cultural transmission (Sid benchmark #3)
`agent.beliefs` spreading probabilistically through conversation; seed a rumor
and chart its adoption curve.

### Phase 8 — Benchmarks & metrics
Specialization index (role entropy), rule-adherence rate, meme-adoption curve,
memory-store size, and a module-activation timeline — logged and shown in the
existing GUI panel, turning Sid-like *features* into Sid-like *results*.

---

## Suggested order & effort

1. Phase 0 (~0.5 day) → 2. Phase 1 memory (~2–3 days) → 3. Phase 2 messaging
(~1–2 days) → 4. Phase 5 emergent roles (~2 days) → 5. Phase 6 rules/voting
(~2–3 days) → 6. Phase 7 memes (~2 days) → 7. Phase 4 meta system (~3–4 days) →
8. Phase 3 PIANO modules + CC (~1 week) → 9. Phase 8 benchmarks (~1–2 days).

Rationale: memory and messaging are prerequisites for everything; the
single-call civilizational benchmarks (5–7) are cheap and high-value; the meta
system and full PIANO fan-out are the expensive, LLM-cost-multiplying changes
and come last so they can be flag-gated off if throughput suffers.

---

## Options NOT implemented (and why)

| Option | One-sentence reason |
|---|---|
| **3D Minecraft environment (Sid)** | Explicitly out of scope — the request is to keep the existing 2D pixel-art canvas. |
| **Physical robot embodiment — Plantbot / ALTER3, motor-control LLM (CMA)** | Requires sensors, actuators, and hardware this browser sim has no access to; it is the physical analogue of the excluded 3D visuals. |
| **ChromaDB + Docker for memory (CMA)** | Replaced with an in-process cosine store behind identical `/memory/*` endpoints to preserve the no-external-service, single-process ethos — Chroma can be swapped in later without API changes. |
| **MQTT / Mosquitto broker (CMA)** | Replaced with an in-browser pub/sub bus because every agent already runs in one frame, so a network broker adds a daemon and latency for no benefit. |
| **Cloud LLM backends — GPT-4 / deepseek (CMA)** | The project is deliberately local-first on LM Studio; backend choice is orthogonal to the behaviors being ported. |
| **Network-transparent / host-independent modules across machines (CMA)** | Only meaningful for multi-host robot deployments; a single-browser sim gains nothing from cross-host transparency. |
| **Fully decentralized "no central control loop" (CMA)** | Adopted only partially — the browser `requestAnimationFrame` loop remains the scheduler and a Cognitive Controller still guarantees coherence, because emergent-only coherence is unproven (the paper's own weakness) and conflicts with the "minimal and observable" goal. |
| **1000+ agents / multiple interacting societies (Sid)** | Gated by a single local LM Studio with 3 parallel slots; the mechanics target ~20–30 agents in one town, and true scale would need multiple model backends. |
| **Legal system as a distinct institution (Sid)** | Folded into Phase 6 (rules/voting/tax) rather than built separately, since a full legal/court system is disproportionate to an 8–12 agent village. |
| **Quantitative "self-awareness" claims (CMA)** | Not a build target — the paper itself concedes this framing outruns its evidence, so we ship the mechanism (meta layer) without the claim. |

---

## Cross-cutting notes
- Keep each new action synchronized across `AVAILABLE_ACTIONS` (index.html) and
  `DECISION_ACTIONS` / `DECISION_SCHEMA` / `SYSTEM_PROMPT` (server.py), per
  CLAUDE.md.
- Verify by running the server and watching the browser + JSONL logs (no test
  suite exists); read `lm_studio.jsonl` to confirm module/CC outputs and which
  fallback fired.
- Mind LM Studio context sizing: Phases 1, 3, and 4 enlarge prompts and add
  calls — watch for `"Context size has been exceeded"` under concurrent load and
  raise context length or lower `MAX_CONCURRENT_LLM` as documented in CLAUDE.md.
