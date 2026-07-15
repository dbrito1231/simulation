# SPEC 00 — Overview

GitServ is a real-time, browser-viewed, server-authoritative simulation of an AI
village: a local LLM (LM Studio) acts as the decision-making "brain" for 8–12
autonomous pixel-art agents who move, talk, trade, gather, build, and govern
themselves. It is a proof-of-concept of the LLM-as-brain loop, inspired by
Project Sid, not a game or a research-grade sim.

**Canonical for:** what/why, non-goals, repo layout, spec index, SDD contract.
**See also:** [CLAUDE.md](../CLAUDE.md) for commands; [01-architecture.md](01-architecture.md)
for topology and the flag index; [docs/REFERENCE.md](../docs/REFERENCE.md) for
deep mechanics.

## Non-goals

- Not a game or shippable product — no win condition, scoring, or player input beyond
  observation and admin controls (pause/resume/reset/roster size).
- Not a research-grade multi-agent benchmark — `BENCHMARKS_ENABLED` sampling exists for
  observability, not publishable evaluation.
- Kept minimal and observable: every mechanic must be debuggable from JSONL logs and
  the `/state` snapshot, not just from behavior.
- (Superseded non-goal, dropped here: an earlier draft of this spec said "no rule
  voting" — that's no longer true. Rule proposals and succession elections
  (`propose_rule`/`vote_rule`) are implemented and load-bearing; see
  [09-systems-society.md](09-systems-society.md).)

## Repo layout

| Path | Role |
|---|---|
| `simulation/sim_engine.py` | The engine: all world state, tick loop, `apply_decision`, persistence |
| `simulation/server.py` | Flask app + cognition: routes, prompt building, LLM calls, decision validation |
| `simulation/index.html` | Thin browser viewer — polls `/state`, renders, holds no sim state |
| `simulation/sprites.js` | Pure stateless Canvas drawing helpers |
| `simulation/roles.json` | Single source of truth for role definitions |
| `simulation/logs/<timestamp>/` | Per-run JSONL logs (gitignored) |
| `specs/` | This spec set — canonical, rebuild-from-scratch documentation |
| `scripts/` | Deterministic smoke/soak tools (no LM Studio needed for most) |
| `docs/` | CLAUDE.md companion docs: REFERENCE.md, HANDOFF.md, active plans, archive |

## Running it

```
uv sync
uv run python simulation/server.py   # http://127.0.0.1:5001
```

Full run/restart recipe (including the required titled-window restart convention and
the LM Studio dependency): [CLAUDE.md](../CLAUDE.md#commands).

## Spec index

| Spec | Scope |
|---|---|
| [00-overview.md](00-overview.md) | This file |
| [01-architecture.md](01-architecture.md) | Topology, data flow, threading, action-sync invariant, flag index |
| [02-engine-core.md](02-engine-core.md) | Tick loop, time model, think scheduling, Sage emergency, persistence |
| [03-cognition.md](03-cognition.md) | Prompt construction, DECISION_SCHEMA, model routing, retries |
| [04-http-api.md](04-http-api.md) | All Flask routes |
| [05-world.md](05-world.md) | World geometry, districts, roads, terrain, ecology, structures |
| [06-agents.md](06-agents.md) | Agent defs/roster, roles.json schema, agent state fields, lifecycle |
| [07-actions.md](07-actions.md) | The action catalog (sole source for all actions) |
| [08-systems-economy.md](08-systems-economy.md) | Survival, crafting, goals, structure effects, goods, economy |
| [09-systems-society.md](09-systems-society.md) | Tech tree, rules/voting, memes, culture, benchmarks |
| [10-path1.md](10-path1.md) | Path 1 bundle: industry, tools, terrain, diplomacy, pressure loop |
| [11-viewer.md](11-viewer.md) | Thin-viewer contract, sprites.js rendering |
| [12-ops.md](12-ops.md) | SessionLogger, log ingestion, scripts/ tools |

## Spec-driven development contract

These specs are the primary interface for this codebase, not an afterthought.
Changes are made by editing the relevant spec(s) first, then the code, so specs
never drift from behavior. The bar: an AI assistant with no other context should be
able to rebuild this application from the spec set alone.
