# Toward Project Sid Parity (without the 3D environment)

A roadmap for evolving this 2D browser-based AI village simulation toward the
capabilities described in **"Project Sid: Many-agent simulations toward AI
civilization"** (Altera.AL — `docs/2024-10-31.pdf`), while keeping the existing
2D pixel-art canvas instead of Minecraft.

The real targets are Sid's three **civilizational benchmarks** — emergent
specialization, amendable collective rules, and cultural/religious transmission
— plus its defining **PIANO** cognitive architecture. The ideas can be adopted
in a 2D world without Sid's 1000-agent Minecraft infrastructure.

> See [project-sid-comparison](#appendix--how-this-codebase-currently-differs-from-sid)
> at the bottom for the gap analysis this roadmap addresses.

---

## Tier 1 — High impact, moderate effort

### 1. Persistent agent memory (the missing PIANO Memory module)
Today `server.py` is stateless between `/agent/think` calls and all state lives
in the browser frame — agents have no recall. Sid's coherence depends on
multi-timescale memory.

- Add `agent.memory = { recent: [...], longterm: [...] }` in the `agents` array
  (near `index.html:478`).
- Feed a compacted memory slice into `build_agent_data()` (`server.py:811`) so
  the prompt includes "what you did/said recently" and "salient facts."
- Cheap version: ring buffer of last N actions + dialogue, plus an occasional
  LLM "reflection" summarization call into longterm.

**Effort: ~1–2 days.** This single change most improves coherence and unlocks
everything below.

### 2. Emergent specialization instead of prescribed roles
Today roles are fixed in `roles.json` and the elder assigns tasks
deterministically (`task_for_role`, `pick_idle_agent_for_project`). Sid's
headline result is that jobs *emerge and rebalance to collective need*.

- Make `role`/`specialty` mutable at runtime. Add a `switch_role` action to
  `AVAILABLE_ACTIONS` (`index.html:661`) plus `DECISION_ACTIONS` /
  `SYSTEM_PROMPT` (`server.py`).
- Drive it from a village-need signal (e.g. persistent resource shortfalls from
  `parse_project_shortfalls`) surfaced in the prompt, so an idle agent chooses
  to become a farmer when food is short. Keep `roles.json` as *starting* roles
  only.

**Effort: ~2 days.** Add a Gini/entropy "specialization index" readout to get
Sid's benchmark.

---

## Tier 2 — The governance benchmark

### 3. Amendable collective rules / lightweight democracy
The proposal→approval scaffold already exists
(`propose_blueprint`/`approve`, `propose_recipe`/`approve_recipe`,
`pendingRecipes`, `validateRecipe`). That's a single-autocrat model (Sage).
Sid's benchmark is agents *creating, voting on, and amending* rules.

- Add a `civilization.rules = []` registry (taxes, build priorities, sharing
  norms).
- New actions: `propose_rule`, `vote` — replace elder-only approval with quorum
  tallying (generalize the existing pending-approval merge logic).
- Enforce one rule mechanically so adherence is measurable (e.g. a "contribute
  20% of gathered resources to a granary" tax checked in `contribute_resources`
  / `applyDecision` at `index.html:1507`).

**Effort: ~2–3 days.** Reuses the blueprint pipeline almost wholesale.

---

## Tier 3 — Cultural transmission

### 4. Meme / belief propagation
Entirely absent today. Give each agent a small `beliefs` / `memes` set; when
agents `talk` (already logged to `conversation.jsonl`), let beliefs spread
probabilistically through the `format_nearby_agents` social channel. Seed one
"religion" or rumor and chart its adoption curve over time — directly
reproducing Sid's Pastafarianism / rumor-spread result.

**Effort: ~2 days.** Visually striking on the 2D canvas (color-tint agents by
dominant belief in `sprites.js`).

---

## Tier 4 — Architectural (PIANO proper), highest effort

### 5. Concurrent cognitive modules + Cognitive Controller bottleneck
This is Sid's defining contribution and the biggest lift. The current design is
one think-call returning one decision, with coherence enforced *post-hoc* by
`normalize_decision()` / `role_fallback_action()`. To approximate PIANO without
a full rewrite:

- Split the single prompt into 2–3 cheap async sub-calls (Social Awareness,
  Goal Generation) whose outputs are summarized into a bottleneck object, then
  one Cognitive Controller call makes the final decision conditioned on that
  bottleneck.
- The concurrency *infrastructure* already exists (`MAX_CONCURRENT_LLM = 3`,
  `drainThinkQueue`) — reuse it for per-agent module fan-out.
- Keep `USE_GOALS` / `stepGoal()` as the "fast reflex" path.

**Effort: ~1 week**, and it multiplies LLM cost per agent — pair it with a
smaller roster or a higher `LLM_MIN_GAP_MS`.

---

## Practical constraints

- **Scale:** matching Sid's 50–1000 agents is gated by a single local LM Studio
  instance (3 concurrent slots). Realistically the *mechanics* can push to
  ~20–30 agents in one town; "multiple interacting societies" would need either
  multiple model backends or heavy use of the deterministic `stepGoal` path to
  keep LLM calls down.
- **Keep the feature-flag discipline** already in use (`SURVIVAL_ENABLED`,
  `USE_GOALS`, etc.) — add `MEMORY_ENABLED`, `EMERGENT_ROLES`, `RULES_ENABLED`,
  `MEMES_ENABLED`, `PIANO_MODULES` flags so each can be A/B compared.
- **Add metrics** (`benchmarks.jsonl`): specialization index, rule-adherence
  rate, meme-adoption curve. Without these you can claim Sid-like *features* but
  not Sid-like *results*.

---

## Suggested order

1. **Memory** (Tier 1.1) — everything else builds on it
2. **Emergent roles** (Tier 1.2)
3. **Rules / voting** (Tier 2.3)
4. **Memes** (Tier 3.4)
5. **PIANO modules** (Tier 4.5)

The first four are each ~1–3 days, reuse existing pipelines, and deliver the
three civilizational benchmarks in 2D. PIANO (5) is the only true architectural
rewrite and is optional if the goal is behavioral parity rather than
architectural fidelity.

---

## Appendix — how this codebase currently differs from Sid

| Dimension | Project Sid (paper) | This codebase |
|---|---|---|
| **Scale** | 10 – 1000+ agents; societies of 50–100, civilizations of 500–1000 across *multiple interacting towns* | 8 (default), URL-overridable to ~12; a single village |
| **Agent architecture** | **PIANO** — 10 concurrent modules (Memory, Action Awareness, Goal Generation, Social Awareness, Talking, Skill Execution, …) with a **Cognitive Controller** bottleneck | Single `POST /agent/think` returning one JSON decision; no parallel cognitive modules, no CC bottleneck, no multi-timescale memory |
| **Concurrency model** | Per-agent internal concurrency (fast reflex nets + slow deliberation) | Infrastructure queue (`MAX_CONCURRENT_LLM=3`) across agents, not within a mind; `USE_GOALS`/`stepGoal()` is a lightweight fast/slow nod |
| **Environment** | Minecraft (3D voxel tech trees, biomes) | 2D browser pixel-art canvas with abstract zones/structures |
| **Coherence mechanism** | Architectural — CC conditions talk modules so speech and action stay bidirectionally influential | Post-hoc — server validates/rejects impossible actions and substitutes deterministic fallbacks |
| **Memory** | Explicit multi-timescale memory (LTM/STM/WM) | None; server stateless, all state in the browser frame |
| **Specialization** | *Emergent* — agents converge to jobs; village rebalances to need | *Prescribed* in `roles.json`; elder assigns tasks deterministically |
| **Governance** | Agents create, follow, vote on, and **amend** collective rules (constitution, tax, democracy) | Single fixed leader (Sage) approves/rejects blueprints & recipes — autocratic, not democratic |
| **Cultural/religious transmission** | Memes/religion and rumors propagate socially across the population | Absent |
| **Benchmarks** | Quantitative civilizational metrics | `completedProjects` + a level counter; no formal metrics |
| **Beyond the paper** | — | Survival (hunger/health/collapse-revive), Mineflayer-style crafting chain, deterministic Sage-priority emergency, blueprint/recipe proposal-approval flow |

**Bottom line:** this codebase is an honest proof-of-concept of Sid's *premise*
(LLM-as-brain, emergent collaborative village) but a deliberate simplification
of its *architecture and ambitions* — consistent with the stated intent in
`CLAUDE.md` ("Keep it minimal and observable… inspired by Project Sid"). The
gaps above are scope choices, and this roadmap is the path to closing them.
