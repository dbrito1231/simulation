# Docker migration plan

Status: **Phases 1–4 complete on PR #14** (`worktree-docker-migration`). `Dockerfile`, `.dockerignore`, `SIM_OLLAMA_HOST`, and docs/spec cutover are landed. **D8 (`--restart` policy) remains provisional** — deferred to soak evidence per [docker-phase3-soak-notes.md](docker-phase3-soak-notes.md).

**Worktree/branch:** whoever initiates this plan creates the worktree and branch — it is deliberately *not* created as part of authoring the plan. Do all implementation phases there, never on `main`.

## 0. Decisions (answered by user, 2026-08-07)

These four decisions are settled and drive the rest of this document. They are not open questions.

| # | Decision | Choice | Consequence |
|---|---|---|---|
| D1 | How the containerized server reaches Ollama | **Make the Ollama host env-configurable** (small `simulation/server.py` change, `localhost:11434` stays the default) | Adds a code-change phase *before* any Gordon work — see Phase 1 |
| D2 | Ollama hosting | **Stays native on the host** — not containerized | No GPU passthrough, no NVIDIA Container Toolkit, no Ollama image, no VRAM risk |
| D3 | Run interface | **Gordon decides** whether `compose.yaml` earns its place vs. a plain `docker run` | Gordon must justify its call; reviewer approves or rejects |
| D4 | Role of Docker afterwards | **Docker becomes the supported way to run the project** | Phase 4 is now a mandatory, substantial docs/spec cutover — not an optional afterthought |
| D5 | Fate of the native `uv run` path | **Kept as a documented fallback** | `README.md`/`CLAUDE.md` lead with Docker but retain a "running natively" section; both paths must stay in sync from Phase 4 onward |
| D6 | Phase 1 env var shape | **`SIM_OLLAMA_HOST`, host:port only** (e.g. `localhost:11434`) | Matches existing `SIM_HOST`/`SIM_PORT`/`SIM_AGENTS` naming; `/api/chat` stays hardcoded so the OpenAI-compat endpoint is unreachable by config |
| D7 | Titled-window run convention | **Foreground container inside a titled `cmd` window** (no `-d`) | Preserves the intent of the existing `CLAUDE.md` rule: one visible window, live logs, closing it stops the server |
| D8 | Restart policy | **None for now — revisit after the Phase 3 soak** | Ship without `--restart`; add one only if real crashes justify it. Phase 3 must explicitly record the outcome |

**Resolved by the planner (not a user decision):** runtime state (`state.db`, `logs/`, `memory_store.json`) uses **bind mounts to the existing host paths**, not named Docker volumes — `CLAUDE.md` treats `simulation/logs/<timestamp>/llm.jsonl` as the primary debugging surface, and it must stay readable at the same host path it is today.

## 1. Prerequisites

State these before any Docker work starts — verify each one, don't assume it:

| # | Prerequisite | Why | How to verify |
|---|---|---|---|
| 1 | Docker Desktop installed (Windows, WSL2 backend) | Gordon AI ships inside Docker Desktop; this repo runs on Windows 11 | `docker --version` in a terminal |
| 2 | Docker account signed in inside Docker Desktop | Gordon requires sign-in (free Base plan) — no functional `docker ai` without it | Docker Desktop → account menu shows signed-in user |
| 3 | `docker ai` CLI available | This is the Gordon entry point the whole plan depends on | `docker ai --help` |
| 4 | Ollama running **natively on the host** (D2), unchanged | The repo is already VRAM-constrained (`sim-smart` + `sim-fast` leave only ~225–1,400 MiB free of 12,288 MiB per [ollama_config.md](../../ollama_config.md)); containerizing Ollama would add GPU-passthrough complexity and VRAM risk for zero benefit — violates the "no excessive resource hogs" / KISS constraints | `ollama ps` shows both models resident |
| 5 | Only one `simulation/server.py` instance running at a time (native **or** containerized) | Repo-wide single-instance rule (`CLAUDE.md`). Docker adds a *second* way to start the server, so this rule gets easier to violate, not harder — it must be actively enforced during the transition | `pgrep -fa "simulation/server.py"` (native) **and** `docker ps` (containerized) — combined must show ≤1 |
| 6 | `uv` available inside the build context (or vendored via the Dockerfile's base image) | The project's install/run commands are `uv sync` / `uv run python simulation/server.py` — the container should mirror this, not invent a parallel pip flow | Confirmed via `pyproject.toml`/`uv.lock` already in repo |
| 7 | Port 5001 free on the host | Project deliberately avoids port 5000 (macOS AirPlay conflict); Docker must publish `5001:5001`, not remap it | `netstat -ano \| findstr 5001` before first run |
| 8 | Phase 1 (`SIM_OLLAMA_HOST` support) merged and verified | Per D1/D6 — Gordon cannot produce a working container before this lands, because the hardcoded `localhost` can never reach host Ollama from inside a container | `simulation/server.py` composes the Ollama URL from `SIM_OLLAMA_HOST`; native run still works with no env var set |
| 9 | A worktree + branch created by the plan initiator | Required by the task constraints; keeps `main` clean across a multi-phase change | `git worktree list` shows the new worktree |

## 2. Repo review — what can move into Docker

Reviewed: `AGENTS.md`, `CLAUDE.md`, `README.md`, `ollama_config.md`, `specs/00-overview.md`, `pyproject.toml`, `.gitignore`, `simulation/server.py` routes and entry point.

### 2.1 Blocking gap found during review: `OLLAMA_CHAT_URL` is not configurable — RESOLVED by D1

`simulation/server.py:103` — `OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"` — is a module-level constant, not read from an environment variable (unlike `SIM_HOST`/`SIM_PORT`, which already are — `server.py:3746-3747`). A container's `localhost` is its own network namespace, so the hardcoded URL will never reach the host's Ollama instance once this runs inside Docker.

**Decisions D1 + D6: fix it at the source, as `SIM_OLLAMA_HOST` (host:port only).** The exact shape is settled:

```python
OLLAMA_CHAT_URL = f"http://{os.environ.get('SIM_OLLAMA_HOST', 'localhost:11434')}/api/chat"
```

Native runs are byte-for-byte unaffected (no env var set → identical string to today); a container run only needs `-e SIM_OLLAMA_HOST=host.docker.internal:11434`.

This is a small, additive, backward-compatible change, but it **is** a source edit to a load-bearing constant, so it gets its own phase (Phase 1) and its own AGENTS.md loop pass — spec update + implementer + reviewer. It must **not** be silently bundled into the Gordon/Docker packaging work.

Notes for whoever implements Phase 1:
- `OLLAMA_CHAT_URL` is defined at `server.py:103` and used at `1280`, `2090`, `2106` — keep the single derived constant as shown above rather than scattering `os.environ` reads across call sites. The three call sites should not need to change at all.
- **The `/api/chat` path stays hardcoded — this is the point of D6, not an implementation detail.** `ollama_config.md` documents that the OpenAI-compat `/v1/chat/completions` endpoint silently ignores `think:false`, which would reintroduce the thinking-leak epidemic. Configuring host:port only makes that endpoint unreachable by configuration.
- Verify both directions: no env var set → native run behaves exactly as before; env var set → URL composes correctly.
- Owning spec is [specs/03-cognition.md](../../specs/03-cognition.md) (model routing / LLM calls); `ollama_config.md`'s "Related sim knobs" section also lists `OLLAMA_CHAT_URL` and will need updating.

### 2.2 Containerizable (the app itself)

| Component | Path | Notes |
|---|---|---|
| Flask server / cognition entry point | `simulation/server.py` | `app.run(host=HOST, port=PORT, threaded=True)` — single process, no WSGI server swap needed to stay KISS |
| Engine package | `simulation/sim_engine/` | Pure Python, no external services beyond Ollama over HTTP |
| Server helper package | `simulation/_server/` | Same — pure Python |
| Viewer static assets | `simulation/index.html`, `simulation/viewer/*.js`, `simulation/sprites/*.js`, `simulation/css/*.css` | Served via Flask `send_from_directory` — no build step, no bundler, ships as-is |
| `simulation/roles.json` | Data file | Read at runtime, bake into image |
| `simulation/wildlife.png` | Committed asset | Bake into image (the `_vendor/` cache that *generates* it is a local build-time tool, gitignored, out of scope) |
| Dependency manifest | `pyproject.toml`, `uv.lock` | `uv sync` inside the image |

### 2.3 Must stay outside the image (runtime/mutable state — bind-mount or volume, never baked in)

| Path | Reason |
|---|---|
| `simulation/state.db` (+ `-wal`/`-shm`) | Gitignored persistence; must survive container restarts and stay out of the image layer |
| `simulation/logs/` | Gitignored per-run JSONL logs; `CLAUDE.md` explicitly forbids committing these |
| `simulation/memory_store.json` | Gitignored restart-stable memory index |

### 2.4 Must stay outside Docker entirely (host dependency, not containerized)

| Component | Reason |
|---|---|
| Ollama (`sim-smart`/`sim-fast`, port 11434) | GPU-bound and VRAM-constrained (D2, prerequisite #4). The container reaches it via `host.docker.internal:11434` — **which requires the Phase 1 change from §2.1**; there is no way to do this without it |
| `scripts/ollama_setup.py` and other host-oriented scripts | Configure the *host's* Ollama install (registry env vars, `ollama create`); meaningless inside a container |
| `.venv/` | Host-only virtualenv; the image builds its own via `uv sync`, not a copy of this |

### 2.5 Scope note

The only source change this migration authorizes is the Ollama-URL configurability fix in §2.1 (Phase 1), plus the documentation/spec cutover in Phase 4 that D4 requires. Nothing else in `simulation/` is in scope. Any other refactor is a separate, separately-approved change per KISS/AGENTS.md.

## 3. Why Gordon AI, and division of labor

Docker's Gordon AI agent (`docker ai`) reads the working directory's Dockerfiles/Compose files/containers and proposes changes with approval-first execution — it does not require a bespoke config format, but it works best from a clear, unambiguous brief rather than open-ended prompting.

- **This plan** — human/orchestrator-facing: decisions, prerequisites, repo review, constraints, phase breakdown.
- **[docker-gordon-brief.md](docker-gordon-brief.md)** — the instruction set handed to Gordon (`docker ai`) to generate the `Dockerfile`, `.dockerignore`, and (per D3, at Gordon's justified discretion) `compose.yaml`. Kept separate and concrete (exact paths, exact port, exact env vars, exact volumes) so Gordon has no ambiguity to fill in with its own assumptions.

## 4. KISS boundaries for the eventual Docker environment

Explicit limits Gordon's output must respect (also stated in the brief):

- **One service, one container.** No multi-container orchestration for a single-process Flask app. No Redis/nginx/reverse proxy/message queue — none of that exists in the current architecture and none is being added for Docker's sake.
- **No Ollama container** (D2). Restated here because it is the single biggest temptation to overengineer this.
- **No more base-image weight than necessary.** A `python:3.12-slim` class image (matching `requires-python = ">=3.12"`) is sufficient; no full desktop/GPU images.
- **No multi-stage build unless the simple single-stage image fails a real constraint** (e.g. image size becomes a problem) — start with the simplest Dockerfile that works, and state the reason if you deviate.
- **`compose.yaml` is Gordon's call (D3), but must be justified.** If a plain `docker run` one-liner is equally clear, no compose file. If compose is proposed, the justification is reviewed like any other design choice.
- **No healthcheck/orchestration machinery beyond what Docker gives for free**, unless a concrete failure mode requires it. No supervisor process, no restart-loop wrapper.
- **No `--restart` policy on first ship (D8).** Crashes stay visible rather than silently looping — a crash-loop would churn `state.db` and spawn junk log sessions unnoticed. Revisit only with Phase 3 soak evidence.
- **Foreground, titled window (D7).** The container runs in the foreground inside a titled `cmd` window, mirroring the existing `CLAUDE.md` convention — not `-d`. Closing the window stops the sim, exactly as today.
- **Single-instance rule carries over and gets harder:** run instructions must make it obvious how to check for and stop an existing instance — named container (e.g. `--name gitserv-sim`), not anonymous/`--rm`-only. Because D4 makes Docker primary while D5 keeps the native path documented, **both** start paths remain live indefinitely — the single-instance check must cover both, permanently, not just during a transition window.

## 5. Phases

This plan originally stopped at Phase 0; Phases 1–4 have since shipped on this branch. Each phase below records what was done; D8 restart policy is the only deliberate deferral still open.

Every phase below follows the manual-step policy in §8: the agent doing the work attempts each step itself first and only asks the user to act where the step is categorically outside what the agent can do.

| Phase | Scope | Status | Agent does | User does |
|---|---|---|---|---|
| **Phase 0 — Plan** (this task) | Decisions, prerequisites, repo review, KISS boundaries, Gordon brief | **Complete — this deliverable** | Everything | Answered the D1–D8 decisions |
| **Phase 0.5 — Worktree/branch** | Create the isolated worktree + branch this migration runs in | **Complete** — `worktree-docker-migration` at `.claude/worktrees/docker-migration` | Created directly (`EnterWorktree`) — reversing the plan's original "user creates this" note, per explicit later instruction | Told the agent to do it instead |
| Phase 1 — Add `SIM_OLLAMA_HOST` (D1, D6) | Full AGENTS.md loop: update owning spec ([specs/03-cognition.md](../../specs/03-cognition.md)) + `ollama_config.md`'s "Related sim knobs" entry, then the one-line `simulation/server.py` change from §2.1; verify the native run is unaffected with no env var set. **Blocks Phase 2.** | **Complete** | Spec edits, code edit, implementer + reviewer dispatch, verification | Approve the phase result (SUCCESS/FAIL per AGENTS.md) |
| Phase 2 — Gordon-assisted scaffold | Run `docker ai` using `docker-gordon-brief.md` as the instruction set; review every proposed file before finalizing. Gordon justifies its `compose.yaml` call (D3) | **Complete** — `Dockerfile` + `.dockerignore` landed; no `compose.yaml` (plain `docker run` sufficient per D3) | Invoke `docker ai "<brief>"` directly (confirmed scriptable: `docker ai --help` shows a one-shot `docker ai "<prompt>"` form, not only an interactive REPL); check proposed files against the 15 constraints | Confirm before the agent spends Docker AI credits by invoking Gordon; review the final diff if the agent flags anything ambiguous |
| Phase 3 — Manual verification + soak | Build image; run container (foreground, titled window, per D7) with the bind mounts/port/env from §1 and §2.3; hit `http://127.0.0.1:5001`; confirm host Ollama reachable via `host.docker.internal:11434`; confirm `state.db` persists across restart; confirm exactly one instance running. **Then soak it under normal 24/7 use and record whether a `--restart` policy is warranted (D8) — this decision must be written down, not left implicit.** | **Complete** (soak ongoing; D8 restart policy still provisional — see [docker-phase3-soak-notes.md](docker-phase3-soak-notes.md)) | `docker build`/`docker run`, all verification checks, stopping/restarting the native server around the test window, scheduling periodic soak check-ins and logging results | **Explicit go-ahead before the live 24/7 sim is stopped** — the agent will not kill it unprompted |
| Phase 4 — Cutover + SDD sync (D4, D5, mandatory) | Docker becomes the supported run path *while the native path stays documented as a fallback*: rewrite `README.md` setup/run with both paths clearly ranked; update `CLAUDE.md` Commands — the titled-window restart recipe becomes a foreground `docker run` in a titled window (D7), and the single-instance check must cover container **and** native; update owning specs — at minimum [specs/00-overview.md](../../specs/00-overview.md) "Running it" and [specs/12-ops.md](../../specs/12-ops.md) for log paths under a bind mount | **Complete** | Everything — doc/spec rewrite is a normal implementer task | Review |

**Phase 4 is the largest documentation change in this plan.** D4 means the run instructions in `README.md`, `CLAUDE.md`, and `specs/00-overview.md` all currently describe a path that will no longer be primary. Per the SDD contract, those updates ship *with* the change, not after it.

**D5's ongoing cost, stated plainly:** keeping the native path documented means every future change to how the server is started, configured, or restarted has to be reflected in *two* sets of instructions. That is the accepted price of the escape hatch — but it is a real, permanent drift risk, and Phase 4 should structure the docs to minimize duplication (one canonical prerequisites list, two short run sections) rather than maintaining two parallel guides.

## 6. Acceptance mapping

| Acceptance criterion | Where addressed |
|---|---|
| Prerequisites identified and stated | §1 (9 items, including the two added by D1/D4) |
| Repo review to identify Docker-migratable pieces | §2, including the blocking `OLLAMA_CHAT_URL` gap found during review (§2.1) |
| Gordon AI utilized to create the environment | §3; executed in Phase 2 — `Dockerfile` + `.dockerignore` landed |
| `.md` file for Gordon AI to build the environment | [docker-gordon-brief.md](docker-gordon-brief.md) |
| KISS at all times | §4, and reflected throughout §2/§5 |
| Own worktree and branch | `worktree-docker-migration` at `.claude/worktrees/docker-migration` — not created during planning (Phase 0), created afterward once the user asked the agent to do it (Phase 0.5) |
| Docker environment created | `Dockerfile` + `.dockerignore` at repo root (Phase 2); docs/spec cutover in Phase 4 |
| No vague Gordon brief | Brief specifies exact paths, port, bind mounts, the exact env var names and values, the §2.1 precondition, run-window shape, and explicit non-goals |
| No excessive resource hogs | Ollama excluded from containerization (D2, §2.4, §4); single-container/no-orchestration design (§4); no restart policy (D8) |
| No overengineering | §4 KISS boundaries; D3 forces Gordon to justify compose rather than add it by default; D8 defers the restart policy to evidence rather than adding it speculatively |
| No more than one server instance running | Prerequisite #5, §4, and Phase 3/Phase 4 checks — D5 makes this permanent rather than transitional, since both start paths stay supported |

## 7. Remaining open items

All decisions raised during planning (D1–D8) are settled. Two items are deliberately deferred to evidence rather than left unanswered:

1. **`--restart` policy** — deferred to the Phase 3 soak by D8. Must be explicitly recorded when decided. **Still open** — soak notes record provisional "no restart" pending more evidence.
2. **Whether `compose.yaml` earns its place** — delegated to Gordon by D3, subject to reviewer approval in Phase 2. **Resolved:** Gordon skipped compose; plain `docker run` is the supported path.

Nothing else in this plan requires a user decision before follow-up work (e.g. D8 closure) can proceed.

## 8. Manual step policy

**Default: the agent driving a phase attempts the step itself.** "Manual" in earlier drafts of this plan meant "the user does this" by default. That default is reversed — a step only goes to the user when the agent is categorically unable to do it, and the agent must say so explicitly at the point it happens, not bury it in a status table.

### Genuinely agent-cannot-do (no amount of tooling changes this)

- **Entering credentials, signing into accounts, or completing any GUI-only installer step.** Already done for this plan (Docker Desktop + sign-in confirmed working — `docker --version` and `docker ai --help` both succeeded). Recorded here for reproducibility if this plan is ever re-run from scratch on another machine.
- **Time passing.** The Phase 3 soak needs the container to run under real load for a real duration. The agent can *schedule* check-ins and log results automatically instead of the user having to remember to look, but it cannot compress the wait itself.

### Agent-does-it, but asks first (destructive, costly, or affecting the live 24/7 sim)

These are not hard blockers — the agent should execute them, not hand them to the user — but each needs an explicit yes before acting, per this session's standing rule that hard-to-reverse or shared-state actions get confirmed first:

- **Stopping the live sim server** before Phase 3 verification. Memory notes explicitly warn against killing it casually; the agent asks, then does the stop/restart itself (including the titled-window relaunch convention from `CLAUDE.md`) rather than telling the user to run the commands.
- **Invoking `docker ai`** (Phase 2). Gordon spends the account's AI credit budget per the product's own docs — the agent confirms before running it, then drives the CLI directly rather than making the user type into a `docker ai` session by hand.
- **Any `git worktree`/branch operation beyond the one already created.** The user reversed the original "you create it" restriction for this migration; that permission is scoped to this plan, not standing — the agent still names what it's about to do before doing it.

### What changed from the original draft

Earlier phase descriptions (and the Cursor-workflow explanation given earlier in this conversation) undersold what a single agent session can do — e.g., assuming `docker ai` only works as an interactive chat a human has to drive. That was corrected after actually running `docker ai --help`, which confirmed a scriptable one-shot form (`docker ai "<prompt>"`) exists. Phase 2's row in §5 reflects this. If a future check finds Gordon's one-shot mode still requires an interactive approval the Bash tool can't satisfy (no verified evidence of this either way yet), that becomes a new, narrower manual step to flag at that time — not a reason to fall back to "the user runs all of Gordon" by default.
