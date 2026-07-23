# AGENTS.md

**Read [CLAUDE.md](CLAUDE.md) first — it is the canonical AI-agent guide for this repo** (what this is, commands, architecture, invariants, flags, logs). Deep mechanics: [docs/REFERENCE.md](docs/REFERENCE.md). For behavior changes, [specs/](specs/00-overview.md) is the canonical spec set — edit specs first, then code (SDD). **Specs must always stay in sync with the repo:** any code change that alters documented behavior, actions, flags, routes, or constants must update the owning spec in the same commit. Only conventions not covered there are listed below.

## Model policy

**One orchestrator, many Sonnet 5 subagents.** The model that initiates the plan (any tier — Fable, Mythos, Opus, Sonnet) orchestrates only: plan, split into phases/steps, dispatch, review. Implementation is done by subagents on Sonnet 5 models and lower (use lower tiers when sufficient); Sonnet 5 is the highest model any implementation agent may use.

## Commit & pull request conventions

- Commit subjects: concise, imperative, often scoped — e.g. `path1: ...`, `docs(sdd): ...`. Keep unrelated changes in separate commits.
- PRs should explain behavior changes, list verification commands, call out feature-flag or `state.db`-format impacts, include screenshots for visible UI changes, and note any LM Studio model/context/concurrency assumptions.

## Do not commit

Credentials, local model data, `simulation/logs/`, or generated state (`simulation/state.db` and backups).
