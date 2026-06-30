---
name: Tune Sage Emergency, Fisher Starvation, and Responder Scope
overview: "Three tuning/behavior fixes to the survival + Sage-priority systems in simulation/index.html, exposed by log review (session 2026-06-28T09-46-10): the Sage emergency fires almost constantly (270 rushes, SAGE_CRITICAL_HEALTH=50 triggers too early); the fisher chronically starves (Finn collapsed 14x because auto-eat only consumes 'food', not the 'fish' he gathers); and the original 'all agents rush Sage' rule is overkill (61/63 heals went to Sage). Fixes: (1) raise the emergency bar by lowering SAGE_CRITICAL_HEALTH 50->30; (2) auto-eat any edible (food OR fish) so fishers self-feed; (3) scope the emergency response to only the healer (Mia) + the single nearest able agent, leaving everyone else to keep working. Prior sub-rules preserved: if Sage AND healer are both down, the healer is revived first (sageEmergency() already targets her), then she prioritizes Sage. Enforcement stays deterministic (tick-loop override + in-flight LLM guard); SYSTEM_PROMPT rule #15 + a nudge are coherence-only."
todos:
  - id: lower-sage-threshold
    content: "index.html: change SAGE_CRITICAL_HEALTH from 50 to 30 so the Sage-priority emergency triggers only when he is genuinely in danger or collapsed. No other logic change (sageEmergency() already keys off this constant)."
    status: completed
  - id: edible-resources-const
    content: "index.html: add `const EDIBLE_RESOURCES = [\"food\", \"fish\"];` near the survival tuning constants (extensible list of what counts as food)."
    status: completed
  - id: firstedible-helper
    content: "index.html: add a small helper `firstEdible(agent)` that returns the first id in EDIBLE_RESOURCES the agent currently holds (qty > 0), or null."
    status: completed
  - id: autoeat-any-edible
    content: "index.html updateSurvival(): replace the food-only auto-eat check with firstEdible(agent) — decrement that resource and restore hunger by FOOD_RESTORE. Fixes the fisher (holds 'fish') never self-feeding and chronically collapsing."
    status: completed
  - id: heal-feed-any-edible
    content: "index.html heal_agent case: when feeding a collapsed patient, donate the rescuer's firstEdible(agent) instead of hardcoded 'food', so a fisher-rescuer can feed fish."
    status: completed
  - id: sage-responders-fn
    content: "index.html: add `sageResponders(target)` returning a Set of <=2 agents who must rush: the healer (role==='healer') if able and not the target, PLUS the nearest able agent to the target (by distanceTo, excluding target and healer). Edge cases fall out of sageEmergency() already targeting the healer when both Sage+healer are down."
    status: completed
  - id: tick-scope-responders
    content: "index.html tick() think-block: compute emTarget = sageEmergency() and responders = emTarget ? sageResponders(emTarget) : null once per tick; change the per-agent override condition from (agent !== emTarget) to (responders && responders.has(agent)) so only responders rushToHeal and everyone else runs normal goals/LLM."
    status: completed
  - id: guard-scope-responders
    content: "index.html thinkAgent() in-flight guard: change the discard condition so only agents in the current responder set discard their returned LLM decision and rushToHeal; non-responders apply their decision normally."
    status: completed
  - id: nudge-and-prompt-text
    content: "index.html: send the Sage-emergency nudge only to responders. server.py: reword SYSTEM_PROMPT rule #15 from 'everyone abandons their task' to 'the healer and the nearest villager revive the elder (healer first if she is also down); other agents continue their work'."
    status: completed
  - id: docs
    content: "CLAUDE.md: update the Sage-priority paragraph — responders = healer + nearest agent, threshold 30, edible foods include fish."
    status: completed
  - id: verify
    content: "Run server + preview_eval: responder set = {healer, nearest} only and a third agent keeps its goal; both-down -> target healer then flips to Sage; fisher with only fish auto-eats and heal can feed fish; no emergency at Sage health 40, emergency at 25; far fewer rushes than 270 baseline; console clean; /agent/think still 200."
    status: completed
isProject: false
---

# Tune Sage Emergency, Fisher Starvation, and Responder Scope

## Context

Log review of session `2026-06-28T09-46-10` (437 LLM calls, post Sage-priority feature) found three problems:

1. **Emergency fires almost constantly** — 270 "rushes" in one session. `SAGE_CRITICAL_HEALTH = 50` triggers on routine health dips, keeping the village in perpetual panic and starving normal work.
2. **Fisher chronically starves** — Finn collapsed 14×. `updateSurvival()` auto-eat only consumes `food`, but the fisher gathers `fish` at the beach, so he never self-feeds.
3. **All agents abandon work for Sage** — 61/63 heals went to Sage. The user wants only the **healer (Mia) + the single nearest agent** to respond; everyone else keeps working.

All changes are in `simulation/index.html` (survival + Sage systems built earlier), with a one-line `simulation/server.py` `SYSTEM_PROMPT` reword and a `CLAUDE.md` note.

## Changes

### 1. Rarer Sage emergency
`SAGE_CRITICAL_HEALTH`: **50 → 30**. `sageEmergency()` already keys off this constant — no other change.

### 2. Auto-eat any edible (fixes fisher starvation)
- Add `const EDIBLE_RESOURCES = ["food", "fish"];` near the survival constants.
- Add `firstEdible(agent)` → first edible id the agent holds, else null.
- In `updateSurvival()`, replace the `food`-only auto-eat with `firstEdible(agent)`: decrement it, restore hunger by `FOOD_RESTORE`. Fisher holding `fish` now self-feeds.
- In the `heal_agent` case, feed the rescuer's `firstEdible(agent)` to a collapsed patient instead of hardcoded `food`.

### 3. Scope the emergency to healer + nearest agent
- New `sageResponders(target)` → Set of ≤2 agents: the **healer** (if able and not the target) **plus** the **nearest able agent to the target** (`distanceTo`, excluding target and healer).
  - Both-down case: `sageEmergency()` already returns the **healer** as the target when Sage and healer are both collapsed → responders = nearest able agent (revive her first). Once she's up, target flips to Sage and she becomes a responder (her priority is Sage). Prior sub-rules preserved.
- **Tick loop**: compute `emTarget` and `responders` once per tick; the per-agent override runs only when `responders.has(agent)` — everyone else falls through to normal goals/LLM.
- **In-flight LLM guard** (`thinkAgent`): only responders discard their in-flight decision and `rushToHeal`; non-responders apply normally.
- **Coherence**: nudge only responders; reword `SYSTEM_PROMPT` rule #15 to "the healer and the nearest villager revive the elder (healer first if she's also down); others continue."

## Key existing code to reuse
- `sageEmergency()`, `rushToHeal(agent, target)`, `neediestNearby()`, `distanceTo()` — already in `index.html`.
- `updateSurvival()` auto-eat block, `heal_agent` case feed block — already in `index.html`.
- Tick think-block override + `thinkAgent` in-flight guard + emergency nudge — already in `index.html` (built this session); this plan narrows their scope from "all able agents" to the responder set.

## Out of scope
Passive self-revive, structured output, crafting, and the collapse model are unchanged.

## Verification (no test suite — run server + preview_eval)
1. `node` parse of index.html + `py_compile server.py`; restart preview server.
2. **Responder scoping:** Sage `health < 30` → `sageResponders()` = exactly {healer, nearest able agent}; those two `rushToHeal`; a third agent with an active goal keeps its own task.
3. **Both down:** collapse Sage + healer → target = healer, responders = nearest agent only; revive healer → target flips to Sage, Mia is a responder again.
4. **Fisher fix:** agent holding only `fish` with low hunger auto-eats fish (fish decremented, hunger up); `heal_agent` can feed fish to a collapsed patient.
5. **Threshold:** no emergency at Sage health 40; emergency at 25.
6. **Live run:** far fewer "rushes" than the 270 baseline; Finn/fishers stop chronically collapsing; `preview_console_logs` clean; `/agent/think` still 200.
