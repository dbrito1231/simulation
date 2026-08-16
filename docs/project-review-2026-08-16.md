# GitServ Simulation — Project Review & Rating

**Reviewed:** 2026-08-16 · **Branch:** `main` @ `063a520` · **Reviewer:** Claude (Opus 5), read-only pass over `specs/`, `simulation/`, `scripts/`, `README.md`, `ollama_config.md`.

Everything below is grounded in the repo's own specs and code — no runtime session was started for this review, so all performance figures cited are the ones the specs record from prior measured soaks.

---

## 1. What it is

A **server-authoritative AI village simulation**. A headless Python engine runs a 5200×5400 px pixel-art world at 30 ticks/second; a **local LLM (Ollama)** acts as the actual decision-making brain for 8–12 (up to 20) autonomous villagers; the browser is a **pure renderer** that polls and draws.

The stated framing (`specs/00-overview.md`) is *"a proof-of-concept of the LLM-as-brain loop, inspired by Project Sid — not a game."* That matters for the ratings later: judged as a game it's missing win conditions and player agency by design; judged as a simulation/observability toy it's unusually complete.

Scale of the thing:

| Surface | Size |
|---|---|
| Python (engine + server) | ~37,700 lines |
| Viewer JS + sprites + CSS | ~13,800 lines |
| Specs | 13 files, ~9,000 lines, treated as canonical |
| Feature flags | 66 module-level toggles |
| Agent actions | 47 `DECISION_ACTIONS` |
| HTTP routes | 67 |
| God-mode command kinds | ~45 validated command types |
| Smoke/soak scripts | 35 |

---

## 2. How it's built

**Three-layer split, hard boundary between them:**

1. **`simulation/sim_engine/`** — `SimEngine` (in `core.py`) plus `constants.py`/`persistence.py`/`helpers.py` and 22 `mixin_*.py` topic files `exec()`'d into one shared namespace. Owns *all* world state, runs the tick loop, applies decisions, persists to SQLite (`state.db`). Mutates only under a single `threading.RLock`; **LLM calls always happen outside the lock**.
2. **`simulation/server.py` + `simulation/_server/`** — Flask app, every route, the action catalog (`DECISION_ACTIONS`), the JSON schema (`DECISION_SCHEMA`), the system prompt, and `run_agent_decision()`. The engine calls the LLM through an injected in-process function (`_ENGINE_DEPS["llm_decide"]`), *not* over HTTP — the `/agent/think` route is legacy/manual-testing only.
3. **`simulation/viewer/*.js` (21 files) + `sprites/*.js` (8) + `css/*.css` (6)** — no bundler, no ES modules, no framework. Plain ordered `<script>` tags sharing one global scope. The viewer holds zero simulation state; closing the tab doesn't stop the world.

**The think cycle** (one agent, one decision):

```
tick thread advances world
  → agent's think timer fires
  → _build_think_payload() snapshots context UNDER the lock
  → lock released
  → run_agent_decision() → Ollama /api/chat (structured JSON output)
  → normalize_decision() validates / falls back
  → lock re-acquired → apply_decision() mutates the world
```

**Persistence & observability.** Full world serializes to `state.db` (autosave + graceful-exit flush + `restore_state()` migration for old saves). Every run writes JSONL logs to `simulation/logs/<timestamp>/`: `activity`, `conversation`, `llm` (full request/response/decision per call), `benchmarks`, `divine`, `compiler`. The specs are explicit that every mechanic must be debuggable from logs and `/state`, not just from watching behavior — and the codebase honors that.

**Development process** (`AGENTS.md`/`CLAUDE.md`): spec-driven, with a plan → orchestrator → implementer → reviewer agent loop. Specs must be updated in the same change as behavior code. This is unusually disciplined for a hobby project and it shows in how little the docs drift from the code.

---

## 3. What the LLM actually does

There are **five distinct LLM call sites**, each with its own model, concurrency pool, and budget — a genuinely thoughtful piece of engineering:

| Call site | Model | Pool | Purpose |
|---|---|---|---|
| Agent decisions (routine + high-stakes) | `sim-smart` (Qwen3.5-9B Q4_K_M, `num_ctx` 20480) | `MAX_CONCURRENT_LLM = 3` | The actual brain — every action an agent takes |
| PIANO background modules | `sim-fast` (llama3.2:3b, `num_ctx` 4096) | `PIANO_CONCURRENT_LLM = 2` | Perception / Social / Desire / Reflection advisory notes |
| Chronicle saga | `sim-fast` | shares PIANO pool | One ~150-word narrative summary per sim day |
| Operator agent interview | `sim-smart` | `INTERVIEW_CONCURRENT_LLM = 1` | Out-of-world Q&A with one agent; read-only, zero mutation |
| God free-prose compiler (dark by default) | `sim-smart` | none — blocks the tick thread, 10 s cap | Turns operator prose into a draft `story_event` |

**Agent decisions.** Prompt = a static ~20-rule `SYSTEM_PROMPT` (byte-identical across every agent and turn, deliberately, so Ollama's longest-common-prefix KV cache always hits — the per-agent persona line is prepended to the *user* message for exactly this reason) plus a templated user message: identity, vitals, spatial context, seasons/weather/prices, build state, civilization state, social context, behaviour nudges, and the filtered `available_actions` list. ~3,100–3,400 tokens routine; up to ~6,163 for invention turns.

Output is forced through `DECISION_SCHEMA` (`json_schema` structured output, `additionalProperties: false`). Notable hardening: the sprite grid is bounded **at the grammar level** (4–14 rows/cols) after 5/5 sprite turns were observed truncating mid-JSON at 768 tokens.

Thinking mode is **off** (`think: false`) — the specs record a measured 1288→81 output tokens and 77s→4.8s improvement, and a 48-sample analysis finding zero decision-quality benefit from reasoning traces.

**Validation & fallback ladder.** Every decision passes `normalize_decision()` → `role_fallback_action()`. Invalid action, wrong payload shape, disallowed action for the flag state, or an offline LLM all degrade to a deterministic role-appropriate action (ultimately `rest`), never a crash. Ollama unreachable = the world keeps running with agents resting.

**PIANO modules** (default on) are advisory only — a staggered fan-out of four cognitive modules whose short text reports get folded into the next decision prompt, cached per `(agent, module)` with a 2-tick TTL, and labelled with staleness (`social (2 turns ago): …`) so the decision model can discount them. Timeouts are dropped, never retried. Cross-module visibility is achieved by appending a `last_reports=` suffix to each module's context — no extra call.

**Memory** is three-tier (`working`/`shortTerm`/`longTerm`) plus an optional **wiki-style compounding memory**: three named sections (relationships / goals / lessons), each capped at 300 chars, merged and contradiction-reconciled by reusing the *existing* summarizer call rather than adding a new one. Semantic recall runs on an in-process 128-dim hashing-trick vector store persisted to `memory_store.json` (restart-stable). Composed memory line is budget-capped at 900 chars.

**Animals do NOT use the LLM.** This is worth stating plainly. `WILDLIFE_ENABLED` fauna (boar, seal, gull, fish, crab, turtle, bee…) are **fully deterministic**: per-kind HP/speed tables, simple steering wander, flee-on-proximity, habitat clamping (water kinds pinned to the actual ocean strip), density caps driven by district ecology stage, respawn timers, and small-probability cross-district migration. Agents interact with them only through the LLM-chosen `hunt_wildlife` action, resolved as multi-hit combat with role-based damage. Same story for raiders and contagion — those add **zero** new actions; they're tick-driven events agents respond to using existing actions.

---

## 4. Feature inventory

**World & ecology** — districts with frontier founding (including coastal pairs), a road network with pathfinding, terrain tile grid, per-district ecology stocks with depletion/regrowth gates, crop growth, seasons, weather (storms with sky tint + particles), a cemetery with a grave grid.

**Survival & economy** — hunger/health, starvation reflexes and forced-hunt precedence, crafting with recipes and stations, structure effects, goods with condition/wear/ruin/repair, a market with prices, a minted coin currency distinct from gold, economy sinks, **contracts with escrow** (offer/accept, deadline expiry, refund, relationship penalty on default), settlement stores, caravans with rendered goods-in-motion.

**Building** — the core loop: `start_project` → `collect_resource` → `contribute_resources` → `build_structure`, with all three downstream actions falling back to `start_project` so a confused model still makes progress. Plus a **two-stage blueprint flow** where agents invent new structure/resource types, a Sage reviews, an elder approves, and the model can then design a **custom pixel sprite** for it on a dedicated turn.

**Society & governance** — a tech tree with tier gates, a **Daily Council** (seating, agenda, deterministic speaking order, proposals, ballots, elder verdicts, transcripts and compact digests fed back into prompts), rule proposal/voting/repeal (including quarantine rules), succession elections, **faction splits and secession**, emergent roles (agents can propose and switch roles; leadership roles are locked so only succession can seat an elder), beliefs/memes with pitch-based persuasion, culture and skill teaching, testament inheritance, dynasty/lineage tracking, bounded PvP (`confront_agent`, gated by rivalry, cooldowns, and no killing the Sage), diplomacy with treaties and tariffs.

**Observability** — benchmarks (specialization index, rule adherence, meme adoption, peer-prediction accuracy), an **anomaly radar**, a **decision-audit** panel correlating `llm.jsonl` intent to `activity.jsonl` outcome via a minted `_decision_id`, a **world wiki** with cross-linked pages for twelve entity kinds, a chronicle plus an LLM-written daily saga, and a spectator **prediction market** on council ballots.

**Sovereign God mode** (on by default; auth optional) — a 13-tab Divine Console with ~45 validated command kinds: proclamations, providence, private omens, whisper campaigns, per-agent temperature dials, memory insertion/deletion, belief planting, context masks (blue pill / red pill / dream / forged conversations), decision compulsion / veto / possession, burning-bush dialogue with bargains, anointing with oracle hints, identity forging, checkpoints and déjà-vu replay, architect zones, wildlife spawn/despawn, weather override, timed lawgiver modifiers, and mass repair/ruin clearance. Every command goes through **preview → apply** with a digest, a reversibility class, an audit line in `divine.jsonl`, and a permanent `intervened` mark on the run.

The design integrity here is notable: divine guidance is **binding** — agents must return a `divine_response` `{stance, reason}` while guidance is active, non-compliance is counted rather than hidden, and the specs explicitly forbid citing an intervened run as evidence of emergent behavior.

---

## 5. Ratings

### How hard is it to play/use — **4/10 difficulty (easy to watch, hard to master)**

Two very different answers. As a **spectator**: trivial. Open `http://127.0.0.1:5001`, the world renders itself, sidebar panels narrate what's happening, Pause/Resume/Reset are three buttons. As an **operator**: steep. The Divine Console has 13 tabs and ~45 command kinds with typed payloads, frame-window durations, reversibility classes, and preview/apply semantics; the anomaly radar, decision audit, and benchmark streams assume you know what `piano_module_drops` or a `role_fallback` means. There is no tutorial or in-app explanation of the systems — the 9,000 lines of specs *are* the manual.

### How hard is it to set up — **9/10 difficulty (the weakest part of the project)**

Blunt assessment: **almost nobody but the author can currently set this up successfully.**

- `ollama/Modelfile.smart` hardcodes `FROM C:\Users\dbadmin\.lmstudio\models\...\Qwen3.5-9B-Q4_K_M.gguf` — an absolute path to a *specific machine's retired LM Studio cache*. On any other machine `ollama create sim-smart` fails, and there is no fallback registry pull. The smart model is the one that runs every single agent decision, so this is a hard blocker, not an inconvenience.
- Needs a GPU with roughly **12 GB VRAM** (measured 11,923 MiB with both models resident).
- Four Ollama environment variables must be set via the setup script, which also restarts the Ollama service (`OLLAMA_NUM_PARALLEL=3`, `OLLAMA_MAX_LOADED_MODELS=2`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KEEP_ALIVE=-1`).
- Docker path requires pre-creating bind-mount targets by hand, or Docker Desktop on Windows silently creates directories where files should be and corrupts SQLite/JSON.
- A documented recurring failure mode of *multiple server instances fighting over port 5001 and `state.db`* — with a whole check-and-kill procedure in `CLAUDE.md`.
- Windows-centric throughout (PowerShell recipes, `setx`, `%LOCALAPPDATA%` log paths).

Credit where due: `scripts/ollama_setup.py` is idempotent, verifies dual residency, and has a `--check` readback. The design is right; the GGUF path is the fatal detail.

### How interactive is it — **7/10**

Much higher than the "pure observer" framing suggests. Baseline is spectator-only (pause/resume/reset/roster size, camera, agent selection). But with God mode on — and it *is* on by default — you can whisper to individuals, force decisions, possess agents, rewrite memories, plant beliefs, forge identities, spawn animals, override weather, run timed economic modifiers, checkpoint and rewind, hold a burning-bush conversation with a villager, and interrogate any agent about its own memories. Plus a prediction market to bet on council outcomes.

What holds it back from higher: it's all **operator-tier** interaction, mediated through form-heavy modals. There's no direct-manipulation play — you don't place a building, drag a villager, or issue an order as a character. Every intervention is a typed command with a preview step, which is exactly right for auditability and exactly wrong for flow.

### How enjoyable is it — **7/10 for the right person, 3/10 for a general audience**

The pleasure here is the *ambient-narrative* kind — watching a village argue in council, invent a structure type, vote out a leader, split into factions, or starve after over-hunting a district, and being able to open `llm.jsonl` to see exactly why an agent decided what it decided. That loop is legitimately compelling and the God mode adds a genuine "poke the anthill" dimension.

But there's no goal, no scoring, no progression to *you*, and events unfold at LLM speed — agents think on staggered intervals of hundreds of frames. It's a lava lamp with a debugger attached. If you don't find "watch and inspect" intrinsically rewarding, you'll be bored inside ten minutes.

### What I like

1. **Architectural discipline.** The server-authoritative split is real, not aspirational. The viewer genuinely holds no state. The lock discipline (snapshot → release → LLM → reacquire → write) is applied consistently across decisions, saga, and council paths, with the one exception documented rather than hidden.
2. **The failure ladder.** Model returns garbage → schema rejects it. Schema mode breaks → auto-disable and retry. Context overflows → slim prompt retry. Ollama offline → deterministic role fallback, world keeps running. Render loop throws → the frame is skipped, not the loop. Very few hobby projects degrade this gracefully.
3. **Measurement over vibes.** Nearly every non-obvious constant carries the soak that justified it. `ALWAYS_ON_MODULES` was *built, measured, failed its gate, and left dark* rather than shipped on optimism. `THINKING_ENABLED_HIGH_STAKES` was tried, measured at zero benefit and 33% concurrency cost, and reverted. `sim-fast` decision routing was rolled back on measured PIANO contention. That is rare and admirable.
4. **God mode's epistemic honesty.** Interventions are attributable, replay-auditable, and permanently mark the run `intervened`, with an explicit spec rule that such runs must never be cited as evidence of emergence. The temptation to let divine nudges masquerade as emergent behavior was recognized and designed against.
5. **The specs.** Thirteen files written to a "rebuild the app from these alone" bar, kept in sync by process. They are the best artifact in the repo.
6. **Emergent depth on a genuinely small budget.** Blueprints → Sage review → elder approval → LLM-designed sprites, and succession → faction split → secession, are real emergent chains, running on a 9B and a 3B model on one consumer GPU.

### What I don't like

1. **The hardcoded GGUF path.** `Modelfile.smart` points at one machine's disk. This single line makes the project effectively un-runnable for anyone else, and it undercuts everything else the setup tooling does well. Highest-value fix in the repo by a wide margin.
2. **Complexity has outrun the "kept intentionally minimal" claim.** 66 flags, 47 actions, 45 god kinds, 67 routes, ~52k lines. The README still says "intentionally minimal." It isn't, and pretending otherwise makes the system harder to approach than it needs to be.
3. **No test suite.** Zero unit tests, no linter, no build step — verification is 35 hand-run smoke scripts plus watching logs. For a codebase this size with this much cross-cutting state, that's the largest structural risk. The action-sync invariant alone (six places that must agree for every action) is exactly the kind of thing a test should enforce, not a spec paragraph and reviewer vigilance.
4. **Documentation sprawl outside `specs/`.** The working tree has ~50 untracked plan/archive files, `.mhtml` page saves, PDFs, and parallel `.claude/plans/` + `.cursor/plans/` trees. `docs/archive/` is explicitly flagged "do not read." The canonical set is excellent; everything around it is noise.
5. **God mode defaults to on.** For a project whose central claim is observing *emergent* LLM behavior, shipping the intervention control plane enabled by default — with auth off — is the wrong default. It's well-audited, so it's safe, but it makes "was this run clean?" a question you have to check rather than assume.
6. **Wildlife is thinner than it looks.** Fauna are pure steering + HP tables. That's the right engineering call, but the pixel-art presentation implies more life than the model has, and hunting reduces to "walk over, press the action 2–6 times."
7. **Windows-only in practice.** Every operational recipe is PowerShell; log paths, `setx`, and process checks assume Windows. The Docker path could have fixed this and mostly doesn't.

### Overall rating (as an engineering project) — **8.5/10**

An impressive, disciplined, well-measured system with a rigorous spec culture, a genuinely sound architecture, and a real degradation story. Held back from a 9+ by the absence of automated tests and the setup blocker.

### Overall game rating — **5.5/10**

It's not really a game, and it says so itself. As an *interactive experience* it's a beautiful, deep aquarium with a god-console attached: fascinating to watch, endlessly inspectable, but with no objectives, no progression, no pacing, no onboarding, and an intervention layer that speaks in JSON payloads rather than verbs. The raw ingredients for a genuinely great emergent-village game are all here — what's missing is the player-facing half.

### Likelihood users will enjoy it — **35%**

Breakdown of the estimate:

| Audience | Share of likely visitors | Enjoyment odds |
|---|---|---|
| AI/agent engineers, simulation researchers, Project Sid followers | ~25% | **~85%** — this is exactly their thing, and the logs/specs are the payoff |
| Dwarf Fortress / RimWorld / ambient-sim enthusiasts | ~25% | **~50%** — the emergent stories land, the lack of goals and slow pace won't |
| General gamers | ~35% | **~10%** — no goal, no controls, hours-long payoff curve |
| Casual devs who just want to run it | ~15% | **~15%** — most will not get past the model setup |

Weighted: **≈35%**. Two changes would move this materially: fix the `Modelfile.smart` GGUF path to a registry pull (setup becomes achievable → the casual-dev and enthusiast buckets stop dropping out at step one), and add a 60-second in-viewer orientation explaining what to watch for. That alone would plausibly push it toward 50–55%.

---

## 6. Highest-value next steps

1. **Fix `ollama/Modelfile.smart`** to `FROM` a pullable registry model (or make `ollama_setup.py` pull-and-fallback when the local GGUF path is absent). One line; unblocks every other user.
2. **Add a minimal automated test layer** over the action-sync invariant, `normalize_decision` fallbacks, and state save/restore — the three places where a silent regression would be most expensive.
3. **Flip `GOD_MODE_ENABLED` to default off**, so a fresh run is an unintervened run by default.
4. **Add an onboarding overlay** — 5 sentences on what a district, a project, a blueprint, and the council are — and the "hard to play" score halves.
5. **Prune the untracked doc sprawl** or gitignore it; point newcomers at `specs/00-overview.md` and nothing else.
