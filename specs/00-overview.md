# SPEC 00 — Overview

GitServ is a real-time, browser-viewed, server-authoritative simulation of an AI
village: a local LLM (Ollama) acts as the decision-making "brain" for 8–12
autonomous pixel-art agents who move, talk, trade, gather, build, and govern
themselves. It is a proof-of-concept of the LLM-as-brain loop, inspired by
Project Sid, not a game or a research-grade sim.

**Canonical for:** what/why, non-goals, repo layout, spec index, SDD contract.
**See also:** [AGENTS.md](../AGENTS.md) for AI workflow; [CLAUDE.md](../CLAUDE.md) for
commands/ops; [01-architecture.md](01-architecture.md) for topology and the flag
index; [docs/REFERENCE.md](../docs/REFERENCE.md) for deep mechanics.

## Non-goals

- Not a game or shippable product — no win condition or scoring. Player input is
  limited to observation and admin controls (pause/resume/reset/roster size),
  plus the optional intervention mode described below.
- **Optional intervention mode (God mode).** `GOD_MODE_ENABLED` (`SIM_GOD_MODE`) is
  **on by default**; disable with `0`/`false`/`no`/`off` at startup. When off,
  the simulation is exactly as autonomous as it was before the flag existed.
  When on, an operator may observe private state and author bounded, typed
  interventions. Token auth is a separate optional gate: `GOD_AUTH_REQUIRED`
  (`SIM_GOD_AUTH`) defaults **off**; when auth is on, a non-empty
  `SIM_GOD_TOKEN` must also be configured or routes stay disabled until restart.
  A god-enabled run is partly interactive — a deliberate departure from the
  pure-observer stance of earlier drafts. Divine influence must never masquerade as emergent agent behavior,
  so every intervention is attributable and replay-auditable, and a run that has
  received one is permanently marked `intervened`. **Intervened runs are not
  comparable to untouched autonomous runs** and must never be cited as evidence
  of emergent behavior. The optional **Matrix interventions** expansion (whisper
  campaigns, temperature dial, memory surgery, and more) extends the same God
  control plane — not agent `DECISION_ACTIONS` — with phased rollout on branch
  `feature/divine-matrix-interventions`. **Voice** (proclamation, providence,
  private omen, whisper campaign) is **binding guidance**: agents must return
  `divine_response` `{stance, reason}` on every think while guidance is active,
  with adherence visible in Sight and the Divine Console Voice Adherence panel.
  See [02-engine-core.md](02-engine-core.md) for state and
  [04-http-api.md](04-http-api.md) for the routes.
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
| `simulation/sim_engine/*.py` | The engine package (`SimEngine` in `core.py`): all world state, tick loop, `apply_decision`, persistence; module-level `constants.py`/`persistence.py`/`helpers.py` plus 23 `mixin_*.py` topic files exec()'d into a shared namespace — see [01-architecture.md](01-architecture.md) |
| `simulation/server.py` | Flask app + cognition entry point: every route, `DECISION_ACTIONS`/`DECISION_SCHEMA`, prompt building, LLM calls, `if __name__ == "__main__"` |
| `simulation/_server/*.py` | Non-route helper modules server.py imports from (validation, prompt formatting, memory store, session logging, model routing, structured-output error parsing, role data) — pure move split, no behavior change — see [01-architecture.md](01-architecture.md) |
| `simulation/index.html` | Thin browser viewer shell — markup only |
| `simulation/css/*.css` | Viewer stylesheet, split into 6 ordered files (layout, panels, agents, council/chronicle, Divine Console chrome, responsive breakpoints) — see [specs/11-viewer.md](11-viewer.md) |
| `simulation/viewer/*.js` | Viewer client — polls `/state`, render loop, sidebar, holds no sim state; split into 16 ordered files (setup, state, render, sidebar, council, minimap, polling, controls, renderloop, Divine Console × 7) — see [specs/11-viewer.md](11-viewer.md) |
| `simulation/sprites/*.js` | Pure stateless Canvas drawing helpers, split into 8 ordered files (core primitives, tiles, props, structures, agents, world, wildlife, shipments) — see [specs/11-viewer.md](11-viewer.md) |
| `simulation/roles.json` | Single source of truth for role definitions |
| `simulation/logs/<timestamp>/` | Per-run JSONL logs (gitignored) |
| `specs/` | This spec set — canonical, rebuild-from-scratch documentation |
| `scripts/` | Deterministic smoke/soak tools (no Ollama needed for most) |
| `docs/` | Companion docs: REFERENCE.md, HANDOFF.md, active plans, archive |

## Running it

**Supported primary path — Docker** (foreground container, host-native Ollama via
`SIM_OLLAMA_HOST=host.docker.internal:11434`; bind mounts for `state.db`,
`simulation/logs/`, `memory_store.json`):

```
docker build -t gitserv-sim .
# pre-create bind targets, then docker run — see CLAUDE.md
```

Full build/run recipe (bind-mount prep, titled-window `docker run`, single-instance
checks, Ollama on host): [CLAUDE.md](../CLAUDE.md#commands).

**Native fallback:**

```
uv sync && uv run python simulation/server.py   # http://127.0.0.1:5001
```

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
| [09-systems-society.md](09-systems-society.md) | Tech tree, Daily Council/governance and voting, memes, culture, benchmarks |
| [10-path1.md](10-path1.md) | Path 1 bundle: industry, tools, terrain, diplomacy, pressure loop, raiders/contagion |
| [11-viewer.md](11-viewer.md) | Thin-viewer contract, viewer/*.js/css/*.css, sprites/*.js rendering |
| [12-ops.md](12-ops.md) | SessionLogger, log ingestion, scripts/ tools |

## Spec-driven development contract

These specs are the primary interface for this codebase. Edit relevant spec(s)
first, then code, so specs never drift. Bar: rebuild the application from the
spec set alone.
