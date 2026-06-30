# A Concurrent Modular Agent (CMA): Framework for Autonomous LLM Agents

Review and summary of **"A Concurrent Modular Agent: Framework for Autonomous
LLM Agents"** (`docs/A_Concurrent_Modular_Agent_Framework_for_Autonomou.pdf`).

- **Authors:** Norihiro Maruyama, Takahide Yoshida, Hiroki Sato, Atsushi
  Masumori, Johnsmith, Takashi Ikegami
- **Affiliations:** Alternative Machine Inc.; The University of Tokyo (Komaba)
- **Venue / ID:** arXiv:2508.19042v1 [cs.AI], 26 Aug 2025
- **Source code:** stated to be released (link redacted/blinded in the preprint)

---

## One-line thesis

Intelligence (and even apparent self-awareness) can be made to *emerge* from a
swarm of **fully asynchronous, stateless LLM "modules"** that never share
internal state — they coordinate only through (a) a **shared vector-database
memory** and (b) **natural-language messages over MQTT**. The authors frame this
as a practical, contemporary realization of Marvin Minsky's *Society of Mind*.

---

## Core architecture

The Concurrent Modular Agent has **three components**:

1. **Functional modules** — each an independent async Python function doing one
   subtask (perception, memory, planning, a single "sense", action, reflection).
   Modules are **isolated**: no shared internal state. Each may call an LLM
   internally, but treats the LLM as a black-box external API
   (GPT-4 / GPT-4o-mini, deepseek). Every module can do exactly four things:
   1. Interact with the external world (vision/audio/text in, web access).
   2. **Retrieve** from global state (query the vector store).
   3. **Store** into global state (embed + save its textual output).
   4. **Send** a natural-language message to another module.

2. **Global state = shared vector memory.** All long-term memory and
   inter-module knowledge sharing goes through a **ChromaDB** vector store.
   Modules embed text and asynchronously read/write. Because Chroma runs in a
   Docker container over HTTP, modules can in principle each run on a separate
   host — that is the framework's scalability story.

3. **Inter-module communication = MQTT.** Asynchronous message passing uses
   **MQTT** (a lightweight pub/sub protocol, via a Mosquitto broker), used here
   as a backend for one-to-one messaging. Network-transparent, so modules are
   host-independent. The message-passing-changes-behavior idea is explicitly
   credited to Brooks' **subsumption architecture**.

**No central control loop, no fixed scheduler.** Each module runs on its own;
the failure of one does not halt the others. Coherence is an *emergent* property
of shared memory + messaging, not an enforced one.

### Stated contributions
- Robustness through modular concurrent processing.
- Practically unbounded scalability (host-independent modules).
- Unifies concurrent-module interaction with agent-based modeling via shared DB
  + MQTT.

---

## The actual tech stack (Implementation Details, Appendix D)

| Concern | Technology |
|---|---|
| LLM backend | OpenAI GPT-4 and `deepseek-chat` (GPT-4o-mini in the robots) |
| Inter-module messaging | **MQTT** via a **Mosquitto** broker |
| Shared memory / global state | **ChromaDB** (persistent mode), in **Docker**, HTTP access |
| Concurrency | Python **asyncio** with thread wrappers |
| Platform | MacBook Pro (14-core CPU / 32-core GPU, 36 GB) |
| Logging | **JSONL** logs per module, with timestamps |

> Note: this is the concrete, named storage stack that the Project Sid paper
> never disclosed. For memory, CMA = **ChromaDB (vector store) + MQTT**.

---

## Two embodied applications

### 1. Plantbot — a hybrid plant/robot lifeform (12 modules, 2 layers)
A living plant + sensor-embedded soil + a mobile robotic base, linked by LLM
modules. The *original* version used per-module local memory and a central Chat
hub; the **rewrite under CMA replaced both with a shared ChromaDB store**, so
modules coordinate purely through shared memory rather than a central
integrator (Chat is demoted to human conversation only). Modules: Vision
Interpreter, Audio Interpreter, Soil Sensor Interpreter, Action (2-stage),
Chat, Thinking, Memory Manager (prunes/summarizes to keep the store relevant).
Only the motor-control module in the hardware layer also embeds an LLM.

### 2. ALTER3 — humanoid android (20+ modules, 3 layers)
A 43-pneumatic-actuator humanoid, run as a layered "OS":

- **Hardware System** — motion control, voice, image processing (continuous).
- **Base System** — ~11 LLM modules, e.g. *Summarizer* (reads last 10 memories →
  summary back to DB), *Desire* ("If you were in this scene, what do you want to
  do?"), *Magi-A/B/C* inner-dialogue modules that converse to self-generate
  context, *Memory Cleaner* (LLM deletes unneeded memories to prevent overflow),
  prediction, task planning, reaction analyzer.
- **Meta System** — *Meta System Report* (monitors base modules + CPU/resource
  state), *Autobiographical Memory* (rewrites a first-person life-story from the
  last 20 memories), and a **Prompt Modifier** that dynamically rewrites other
  modules' system prompts based on meta reports. Modules also self-activate /
  self-deactivate based on meta-report output.

**Key design claim:** identity is *not* given by an initial personality prompt —
"true self emerges from memories." Three evolving variables (the database, human
conversation, and dynamically-modified system prompts) let the system drift
open-endedly.

**Result:** a 14-hour live deployment at the Venice Biennale Architecture 2025.
Periodic modules (autobiographical memory, prompt modifier) ran continuously;
others self-gated on/off via meta reports, producing autonomous mode
transitions while staying coherent. The paper quotes generated
autobiographical text and Magi inner-dialogue as evidence of emergent
personality.

---

## Intellectual lineage

- **Minsky, *Society of Mind*** — intelligence as emergent from many simple,
  specialized, intercommunicating processes; base-level + meta-level processes.
  CMA is pitched as its modern LLM-based instantiation.
- **Brooks' subsumption architecture** — reactive, message-coordinated modules.
- **Blackboard systems / ROS** — shared workspace and async distributed nodes
  (CMA's global state is a vector-store blackboard).
- **Project Sid (PIANO)** and **Lyfe Agent** — explicitly contrasted (below).
- **Hogeweg** (structure-oriented ALife modeling) and **Ackley** (indefinitely
  scalable computation, local async interaction over global synchrony).

---

## How CMA differs from Project Sid / PIANO

The paper directly positions itself against Sid:

| Dimension | Project Sid (PIANO) | CMA (this paper) |
|---|---|---|
| Coordination | **Centralized** orchestration via a Cognitive Controller bottleneck | **Decentralized** — no central loop; coherence emerges from shared memory + MQTT |
| Scale focus | Thousands of agents in one Minecraft world | A few **deeply embodied** agents; "mind-like functions distributed within a single body" |
| Embodiment | Virtual (Minecraft) | **Physical robots** (plant-robot hybrid, humanoid) |
| Shared state | Single shared state (acknowledged as prior art, incl. Sid) | Single shared **vector** state, **plus** parallel inter-module messaging (claimed novelty) |
| Memory storage | Undisclosed (only S3 for the voting pipeline) | **ChromaDB vector store, named explicitly** |

CMA's claimed novelty over a Sid-style single-shared-state design is **adding
direct parallel module-to-module messaging** on top of the shared store, which
they argue yields richer/more diverse behavior.

---

## Critical assessment

**Strengths**
- Concrete, reproducible stack (Chroma + MQTT + asyncio) — unusually specific
  vs. Sid's abstraction.
- Genuinely decentralized: no single point of failure, host-independent modules.
- Two real embodied deployments, including a long (14 h) public run.
- The meta-layer (prompt modifier + self-activation + autobiographical memory)
  is a clean, interesting mechanism for open-ended drift without fine-tuning.

**Weaknesses / open questions**
- **Evaluation is largely qualitative** — quoted autobiographical snippets and a
  module-activity timeline, not quantitative coherence/behavioral metrics. Hard
  to judge how often the un-enforced coherence actually breaks.
- **Cost/latency unaddressed** — 20+ concurrent modules each calling cloud LLMs
  implies heavy API load; no token/cost/latency analysis.
- **"Self-awareness" framing is strong** relative to the evidence (suggestive
  outputs, not a test).
- Scalability is argued architecturally (host-independent modules) but not
  demonstrated at scale; the runs are single-agent embodiments.
- Coherence-without-a-controller is the central bet, and the paper shows it
  *can* hold for hours but not the conditions under which it fails.

---

## Relevance to this codebase

This sim is a Sid-inspired, *centralized* design (one `/agent/think` call per
agent, post-hoc validation/fallback for coherence). CMA is the **opposite
architectural pole** — decentralized, emergent coherence, vector-store memory —
and is directly informative for two roadmap items in
[project-sid-parity-roadmap.md](project-sid-parity-roadmap.md):

- **Tier 1.1 (memory):** CMA is a worked example of the storage layer Sid left
  unspecified — a **ChromaDB vector store with embed-on-write / query-on-read**,
  plus dedicated *Memory Manager* / *Memory Cleaner* / *Summarizer* modules for
  pruning and consolidation. That maps onto the WM/STM/LTM tiers discussed for
  1.1, and the "summarize last N → write back; LLM deletes stale entries"
  pattern is a concrete, copyable consolidation loop. (Note the heavier
  dependency footprint — Chroma + a broker — vs. this project's current
  browser-RAM + JSONL approach.)
- **Tier 4.5 (PIANO-style concurrent modules):** CMA shows a fan-out of many
  per-mind modules *without* a Cognitive Controller bottleneck — a useful
  contrast to PIANO when deciding whether emergent or enforced coherence fits
  this project's "minimal and observable" goal.

One small convergence worth noting: CMA also logs **per-module JSONL with
timestamps**, the same debugging surface this project already uses
(`SessionLogger`).
