# Simulation Intelligence Audit

This is a pure analysis/audit — no code changes were made as part of producing this
document. It is based on direct inspection of `simulation/server.py`,
`simulation/index.html`, `simulation/sprites.js`, `simulation/roles.json`, `specs/`,
`.cursor/plans/`, and `simulation/ISSUES.md`.

## 1. Executive Summary

The simulation is a **stateless decision dispenser wrapped around a fixed-topology world**, not a civilization engine. Every subsystem that *could* produce emergence (economy, population, technology, ecology, government) is either absent, decorative, or capped at a single instance that resets to zero information value once satisfied. The single most consequential fact in the entire codebase is this:

```
const civilization = {
  activeProject: null, ...   // ONE global project slot, shared by all 8-12 agents
}
```

Everything else — resource gathering, contribution, building, blueprints, recipes — is a queue feeding this one slot. There is no persistent world memory beyond a 5-line-per-agent scrolling buffer and a 30-entry global activity log that vanish on refresh. There is no scarcity (`collect_resource` manufactures resources from nothing — no zone depletion), no population dynamics (roster is fixed at boot, no births/deaths), no economy (trade is a random 1-unit gift, not a market), and no technology tree beyond a single hardcoded "granary" plus whatever the LLM invents through `propose_blueprint`/`propose_recipe`, both of which are capped at small totals (15 structures, 10 custom resources, 12 recipes) and never removed or superseded. The elder is a scripted task-dispatcher, not a governing intelligence. Nothing in the system can regress: health/hunger can drop to 0 (`incapacitated`) but agents cannot die, structures cannot decay or be destroyed, and civilization level only counts upward (`Math.floor(completedProjects/3)+1`).

In short: the simulation has the *vocabulary* of a civilization sim (roles, blueprints, recipes, relationships, survival) but the *grammar* enforces a single converging loop — gather → fund one slot → build → repeat — with a small, easily-exhausted space of novelty (custom blueprints/recipes) bolted on top. The LLM is asked to make "decisions," but `normalize_decision()` / `role_fallback_action()` (server) and `normalizeDecision()` / `roleFallbackAction()` (client) silently override most agent choices with deterministic, role-scripted fallbacks whenever the model's answer doesn't fit a narrow validation window — meaning a large fraction of "agent decisions" are actually **hardcoded scripts wearing an LLM costume**.

## 2. Top 20 Reasons the Simulation Does Not Feel Alive

1. **A single global `activeProject` slot** (`index.html:442-459`) means only one build can happen at a time across the *entire village*, regardless of population size — this is the single biggest throughput bottleneck.
2. **`collect_resource` manufactures resources from nothing** (`index.html:1463-1465`, `agent.resources[resource] += 1`) — no zone depletion, no scarcity, no diminishing returns, so abundance/scarcity as a concept doesn't exist.
3. **No population system** — the roster (`AGENT_DEFS`, `makeAgents()`) is fixed at page load; there is no birth, aging, death, or generational turnover anywhere in the code.
4. **No permanent death** — `incapacitated` is the only failure state and it's always recoverable (`updateSurvival`, `heal_agent`), so there is no real risk/consequence loop.
5. **Structures are never destroyed, decayed, or replaced** — `civilization.structures.push(...)` is the only mutation; there is no fire, disaster, war, or obsolescence.
6. **`role_fallback_action`/`roleFallbackAction` are large deterministic decision trees** (~90 lines each, mirrored client/server) that fire whenever the LLM's output fails validation (invalid talk target, invalid blueprint, wrong role for an action, etc.) — in practice a large share of "agent behavior" is scripted, not model-driven.
7. **Memory is 5 lines per agent, in-RAM, non-summarized** (`pushMemory`, `index.html:1355-1358`) — no long-term memory, no reflection, no learning from past mistakes; the model literally cannot get smarter over a session because it has no way to accumulate insight beyond the last 5 actions it took.
8. **Relationships are a flat enum with no mechanical effect** (`ally|neutral|rival`) — they're shown in the prompt and set via `relationship_update`/`nudgeAlly`, but nothing in `applyDecision` checks relationship state to alter trade willingness, task assignment, or conflict — decorative.
9. **Trade is a random gift, not a market** (`trade_resource`, `index.html:1501-1519`) — gives away the agent's single most abundant resource with no price, negotiation, or scarcity signal; there is no economy, currency, or property concept (gold is just another gatherable resource, not a medium of exchange).
10. **The elder is a scripted dispatcher, not a decision-maker** — `assign_task` validation requires the target be in a literal `idle_agents` list computed by `idleAgentsForElder()`, and the deterministic fallback (`pick_idle_agent_for_project`) already knows what to assign before the LLM is even asked.
11. **Behavior nudges pre-bias almost every prompt** (`thinkAgent`, `index.html:1835-1882`) — hardcoded `NOTE:` strings tell the agent what to prioritize (start a project, stop talking, go heal, etc.), meaning the "emergent" decision is frequently a rephrasing of a scripted suggestion.
12. **Goals bypass the LLM entirely for multiple ticks** (`stepGoal`, `GOAL_STEP_FRAMES=45`, ttl 6-8) — once a goal is set, `applyDecision` is called with a synthetic decision object and the model isn't consulted again until the goal expires/completes; this improves performance but caps how often genuine novel reasoning can occur.
13. **No weather, seasons, or environmental variability** — `ZONE_CENTERS`/`ZONE_BOUNDS` are static; there's no reason to ever change strategy based on the environment.
14. **No exploration or map growth** — the world is a fixed 1600×1000 canvas with 7 named zones; "scout"/"explorer" roles have no distinct mechanic (they fall back to `move_to_village`, `roleFallbackAction` "guard, scout, explorer" branch), so exploration is purely cosmetic.
15. **Blueprint/recipe novelty is hard-capped and never expires or evolves** — `MAX_PENDING_BLUEPRINTS=5`, `MAX_APPROVED_CUSTOM=15`, `MAX_CUSTOM_RESOURCES=10`, `MAX_CUSTOM_RECIPES=12`. Once hit, `validateBlueprint`/`validateRecipe` reject all further novelty for the rest of the session — the "invention" system has a hard ceiling with no way to retire old inventions to make room.
16. **Rejected blueprints/recipes are permanently blacklisted** (`rejectedBlueprintIds`, `rejectedRecipeIds`) with no path to reconsideration, so one bad elder judgment call permanently forecloses an idea space.
17. **Civilization "level" is a pure counter** (`Math.floor(completedProjects/3)+1`) with no gameplay effect — it's a vanity metric, not a system that unlocks anything or changes rules.
18. **No conflict/competition system** — `rival` relationship exists as a label but there's no mechanic for agents to withhold resources, sabotage, or compete for scarce space/goods.
19. **No government beyond one leader flag** (`"leader": true` on elder in `roles.json`) — there's no council, no voting, no succession, no changing power structure.
20. **History is just logs, not simulation state** — `activityLog`/`conversationLog` are capped ring buffers (30/100 entries) purely for the UI sidebar and are POSTed to JSONL for human debugging; nothing in the simulation itself reads its own history to inform future behavior (no "we tried X and it failed" reasoning loop).

## 3-10. Bottlenecks by Category

**3. Architectural bottlenecks**
- Single mutable `activeProject` global — a hard serialization point regardless of agent count.
- Client (`index.html`) is both the sole state owner and the renderer; the server is fully stateless per-request, so there is no server-side "world model" that could drive independent NPC-like subsystems (weather, disasters, migrations) — everything must be pushed through the same 300-tick-interval per-agent LLM call.
- `AVAILABLE_ACTIONS`/`DECISION_ACTIONS` are a fixed enum duplicated in three places (client, server, prompt) — adding any truly new category of agent behavior (e.g., "explore", "fight", "found settlement") requires coordinated code changes in all three, which is inherently anti-emergent: the ceiling on what agents can ever do is a static list, not something agents/elder can expand at runtime (contrast with blueprints/recipes, which *can* expand, but only structures/craftables — not verbs).

**4. AI bottlenecks**
- 512 max_tokens, temperature 0.4, "thinking: disabled" (`server.py:954-957`) — actively suppresses exploratory/creative reasoning in favor of fast, conforming JSON.
- `normalize_decision`/`role_fallback_action` silently discard a large class of "risky" or novel LLM choices (any invalid talk target, any unapprovable blueprint, any assign_task not aimed at a listed idle agent) and replace them with the *exact same deterministic script every time* — so repeated novel attempts are punished identically to a first attempt; there's no shaping/learning signal.
- No memory of *why* a past action failed (no "you tried X, it was rejected because Y" in the 5-line memory) — the agent will happily re-attempt the same invalid idea indefinitely except where explicit reject-ID blacklists exist.
- 8-12 independent single-turn decisions per interval with no cross-agent joint planning — the elder's "coordination" is one line of scripted logic (`pick_idle_agent_for_project`), not multi-agent negotiation.

**5. Economic bottlenecks**
- No currency, price discovery, or property ownership — "gold" is just a gatherable resource used only as a project-need line item.
- `trade_resource` transfers exactly 1 unit of the giver's most-abundant resource unconditionally if within range — no negotiation, no refusal, no value asymmetry.
- No markets, no supply/demand, no inflation/scarcity feedback (see #2 — resources are infinite per zone).

**6. Ecology bottlenecks**
- Zones (`farm`, `forest`, `cave`, etc.) are inexhaustible resource dispensers — `collect_resource` never checks or decrements a zone-level stock.
- No weather, seasons, or disasters — `ZONE_CENTERS`/`ZONE_BOUNDS` never change.
- No environmental degradation or recovery mechanics tied to gathering (e.g., over-forestation) — nothing to recover from.

**7. Population bottlenecks**
- Roster is fixed by `AGENT_DEFS`/`ROSTER_SIZE` at page load (`?agents=` URL param) — no reproduction, aging, immigration, or emigration.
- `incapacitated` is the only "failure" state, and it's always reversible — no permanent death, so there's no generational or population pressure at all.

**8. Technology bottlenecks**
- Fixed seed tech: 4 base projects (`house`, `farm_plot`, `workshop`, `wall`) + 1 hardcoded advanced build (`granary`, gated by `CRAFTING_ENABLED`) + 3 seed recipes (`planks`, `bricks`, `tools`).
- All further "technology" comes only from LLM-authored blueprints/recipes, capped at 15/10/12 respectively with no expiry/pruning — so the tech tree has a small, fixed ceiling and cannot be regenerated once exhausted or once ideas are rejected (permanent blacklist).
- No tech *tiers* or prerequisites beyond "needs N of resource X" — nothing like "wall requires workshop to exist first."

**9. Evolution bottlenecks**
- Nothing changes the rules of the simulation over time — no unlockable mechanics, no phase transitions beyond the cosmetic `civilization.level` counter.
- The single global project slot caps the rate of structural change to "one building's worth of progress at a time," independent of population or resource abundance.
- No mechanism for agents to change their own role distribution in response to civilization needs beyond a single `change_role` action with no strategic driver behind it in the fallback logic.

**10. Realism bottlenecks**
- Uniform, static personalities (`"hardworking and cautious"`, etc. — `AGENT_DEFS`) never evolve based on experience.
- No day/night cycle, no fatigue beyond hunger/health, no travel cost besides straight-line movement speed.
- Sprites (`sprites.js`) are stateless/decorative and have no gameplay feedback loop into behavior.

## 11-13. Decorative / Dead / Missing Systems

**11. Decorative (exist, render/log, but don't influence decisions)**
- `relationships` (ally/neutral/rival) — displayed to the LLM in the prompt but no code path conditions behavior on relationship value.
- `personality` strings — passed into every prompt but never referenced by any validation, fallback, or scoring logic; purely a stylistic hint to the LLM that the LLM itself may or may not honor.
- `civilization.level` — a pure counter with no unlock effects.
- Scout/Explorer/Guard roles — mechanically identical (`roleFallbackAction` "guard, scout, explorer" branch just does `move_to_village`); no distinct exploration, patrol, or combat mechanic exists.

**12. Systems that never influence anything else**
- `conversationLog`/`activityLog` — read-only for the sidebar and JSONL persistence; never consulted by any decision logic.
- `memory` array — passed to the prompt but never programmatically inspected by fallback/normalize logic (only the LLM "reads" it, and only the last 5 lines).
- Directive/`civilization.directive` — set by elder actions and shown to agents, but nothing enforces or scores compliance; agents can ignore it freely.

**13. Systems that should exist but are missing**
- Zone-level resource stock and depletion/regrowth.
- Persistent, structured agent memory (event importance, summarization, cross-session persistence).
- Multiple concurrent projects (per-zone or per-team) instead of one global slot.
- A real economy: currency, price signals, negotiation outcomes in `trade_resource`.
- Population dynamics: aging, birth (new agent creation via village growth), permanent death, generational skill inheritance.
- Consequence for failure: risk of losing structures/resources, not just temporary incapacitation.
- A pruning/versioning mechanism for the tech tree (retire old recipes/blueprints, allow upgrades).
- Any world-state variability (weather/seasons/disasters) that forces adaptive re-planning.

## 14-15. ROI Ranking

**Highest ROI (ranked)**
1. **Zone/resource scarcity with depletion & regrowth** — introduces the single missing ingredient (scarcity) that would make gathering, trading, and project prioritization actually matter, and it's a localized change (`collect_resource` in both server validation and client `applyDecision`).
2. **Multiple concurrent projects** (e.g., per-zone or team-based building slots instead of one `activeProject`) — directly removes the single largest serialization bottleneck and would let population size actually matter.
3. **Structured, persistent agent memory with failure reasons** — lets agents "learn" why past attempts were rejected instead of repeating scripted failure loops; even a lightweight structured memory (not full RAG) would materially change apparent intelligence.
4. **Consequence/permanence** (structures can degrade/be lost, agents can permanently die or be permanently replaced by "children") — creates real stakes so decisions matter.
5. **A real trade/economy layer** (negotiated exchange rates, refusal, price signals) tied to actual scarcity from #1.

**Lowest ROI**
- More prompt engineering/nudges (`behavior_nudge` strings) — treats a systemic bottleneck as a wording problem; diminishing returns already visible from the density of nudges already in `thinkAgent`.
- More decorative buildings/resources/recipes within the existing capped tech tree — doesn't change the underlying single-project bottleneck or lack of scarcity.
- More LLM calls / larger `MAX_CONCURRENT_LLM` — throughput isn't the bottleneck; the shared single-project slot and infinite resources are.
- Larger roster (`?agents=12`) alone — more agents just queue for the same one project slot and infinite resource zones, producing more redundant LLM calls with no new emergent behavior.

## 16. Recommended Implementation Order (highest leverage first, dependency-aware)

1. Zone-level resource scarcity (finite stock + regrowth rate) — this is a prerequisite for every other system (economy, ecology, population) to have meaning.
2. Multiple/concurrent project slots (per-zone or per-team) — unblocks population size from mattering and enables specialization/competition for build sites.
3. Structured agent memory with outcome/failure tags (still lightweight — a few categorized fields, not a new subsystem) — makes "learning" observable without a heavy architecture change.
4. Consequence and permanence (irreversible loss: destroyed structures, permanent agent death/replacement) — creates real risk, which is a prerequisite for meaningful "recovery" and "collapse" narratives the user wants to observe.
5. A genuine trade/economy layer building on scarcity from step 1.
6. Tech-tree pruning/versioning so blueprint/recipe caps don't permanently foreclose the invention space.
7. Environmental variability (weather/seasons) once the above create a world where variability actually changes optimal behavior.

## 17. Numerical Scores (0–100), with evidence

| Dimension | Score | Justification |
|---|---|---|
| **Emergence** | 15 | Nearly every consequential decision path is intercepted by deterministic `normalize_decision`/`role_fallback_action` logic (server.py:602-690, index.html:1069+); genuine novelty is limited to blueprint/recipe proposals capped at single-digit/low-double-digit totals per session. |
| **Intelligence** | 20 | Decisions are single-turn, 512-token, temperature-0.4 JSON completions with no memory beyond 5 lines and no cross-agent joint planning; the elder's "coordination" is largely pre-computed (`pick_idle_agent_for_project`). |
| **Civilization** | 15 | One global build slot (`civilization.activeProject`), one leader flag, no economy/government/culture; `civilization.level` is a vanity counter with no unlocks. |
| **Adaptability** | 20 | `stepGoal`/goal system explicitly bypasses the LLM for multiple ticks (index.html:1751-1779); behavior nudges pre-suggest the "adaptive" response before the model reasons. |
| **Realism** | 20 | Infinite resources (index.html:1463-1465), no death, no economy, no environmental variability, static personalities. |
| **Evolution** | 10 | No population turnover, no rule changes over time, hard caps on tech tree with permanent blacklisting of rejected ideas (`rejectedBlueprintIds`/`rejectedRecipeIds`). |
| **Replayability** | 25 | The blueprint/recipe system and LLM's stochastic phrasing offer *some* session-to-session variety, but the underlying loop (single project → funded → built → repeat) converges identically every run. |
| **System interconnectedness** | 20 | Relationships, personality, memory, and history are all read-only inputs to the LLM prompt with no downstream mechanical effect (server.py `format_*` helpers just stringify state for display) — most "systems" are parallel silos feeding one prompt, not systems that feed each other. |

## Closing Note

The project's own `.cursor/plans/fix_build_progression.plan.md` documents that the team already diagnosed and patched the most acute failure (the LLM never spontaneously choosing `start_project`) by adding deterministic fallbacks and nudges. That fix worked for its narrow goal — the pipeline now runs — but it did so by *increasing* the amount of scripted determinism in the system, which is structurally at odds with the stated goal of emergence. The architecture is sound for a proof-of-concept of "LLM-as-brain," but the specific mechanism preventing a living civilization is not a missing feature — it's that the world model itself (one project, infinite resources, no death, no economy, no history feedback) has no room in it for the kind of surprise the user is asking for, no matter how well the LLM reasons.
