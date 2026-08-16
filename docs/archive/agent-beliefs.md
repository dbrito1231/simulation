# What religion do the agents follow?

The simulation has no literal "religion" system — the closest analogue is the
**belief/meme system**, governed by the `MEMES_ENABLED` flag
([sim_engine.py:232](../simulation/sim_engine.py:232)). See
[specs/09-systems-society.md](../specs/09-systems-society.md) for the canonical
description. Agents can hold, spread, and found beliefs that function like small
folk religions, each with a name, a tenet, and a mechanical "affinity" that
biases voting and project preferences.

## Which flag governs what

`MEMES_ENABLED` is the belief system's flag. It gates seeding
([:7926](../simulation/sim_engine.py:7926)), founding via `found_belief`
([:7964](../simulation/sim_engine.py:7964)), pitching and adoption
([:7998](../simulation/sim_engine.py:7998),
[:8027](../simulation/sim_engine.py:8027)), the rule-vote affinity bias, and the
`beliefIds` projection into `/state`.

`CULTURE_ENABLED` is a *separate* flag covering skills, the chronicle, and
personality drift. It touches beliefs only downstream: the
`HARVEST_SPIRIT_CONTRIB_BOOST` food-contribution tilt
([:7878](../simulation/sim_engine.py:7878)) and the belief catalog assembled for
prompts ([:7917](../simulation/sim_engine.py:7917)) are both gated on it.

An earlier revision of this file attributed the whole system to
`CULTURE_ENABLED`. That was wrong.

## Seed beliefs (present at world start)

| id | name | tenet | affinity (biases) |
|---|---|---|---|
| `harvest_spirit` | Harvest Spirit | "The Harvest Spirit rewards those who share food" | `rationing`, `harvest_quota`, `resource_tax` |
| `river_spirit` | River Spirit | "The River Spirit blesses fishers who keep the waters free" | `priority` (favors free waters / fish priority over food rationing) |

These two are seeded as competing starting memes (`MEME_SEED_IDS`,
[sim_engine.py:700](../simulation/sim_engine.py:700)), not a fixed catalogue —
any agent may found a new belief at any time via the `found_belief` action, up to
`MAX_BELIEFS = 6` live beliefs total, stored in `civilization["beliefRegistry"]`
and persisted with `state.db`.

Three further **authoring exemplars** ship in `BELIEF_ARCHETYPES`
([:713](../simulation/sim_engine.py:713)) — `forest_steward` (practical),
`egalitarian` (political), and `dreamwalker` (outlier). They are offered in the
prompt catalog but are deliberately **not** pre-adopted, so the competing
dual-seed opening survives and the `MAX_BELIEFS` budget stays open for agent
authorship.

## How beliefs spread

Not by passive proximity. `_spread_beliefs_by_proximity` performs no conversion;
it only exposes adjacent mixed-belief pairs in think payloads. Conversion happens
when an agent uses `talk_to_nearby` carrying a `belief_pitch`, scored by
`run_belief_pitch` when Ollama is available (deterministic
`BELIEF_FALLBACK_QUALITY` otherwise, so offline runs stay reproducible). Both the
scorer and the engine require the target inside the 80px nearby-talk radius.

Beliefs carry real mechanical weight: the affinity biases `_belief_biased_vote`,
believers prefer matching projects when choosing a role-default project, and
co-believers gain a reciprocal relationship bonus on adoption.

## Runtime observation — 2026-07-27, not current

> **This section is a dated snapshot of one running world, not a property of the
> simulation.** Belief state changes continuously. Re-check
> `GET /state` before relying on any of it.

At that observation, all 14 agents held **both** seed beliefs simultaneously
except **Ash**, who held neither. No agent had founded a new belief;
`beliefRegistry` contained only the two seeds.
