# Gordon AI build brief — GitServ simulation containerization

This file is the instruction set for Docker's Gordon AI agent (`docker ai`). Paste or reference this file's contents when running `docker ai` from the repo root. Gordon proposes files with approval-first execution — review every proposed change against the constraints below before approving.

**Do not run this brief yet.** It is a planning artifact (Phase 0). Running `docker ai` against it is **Phase 2** of [docker-migration-plan.md](docker-migration-plan.md), and it needs both separate user approval *and* Phase 1 (below) already merged.

## Precondition — Phase 1 must be done first

**Do not run this brief until `simulation/server.py` reads the Ollama host from an environment variable.**

As shipped today, `OLLAMA_CHAT_URL` (`simulation/server.py:103`) is a hardcoded literal `"http://localhost:11434/api/chat"`. Inside a container, `localhost` resolves to the container itself, not the host, so the app **cannot** reach host-native Ollama. Phase 1 fixes this at the source with a settled shape:

```python
OLLAMA_CHAT_URL = f"http://{os.environ.get('SIM_OLLAMA_HOST', 'localhost:11434')}/api/chat"
```

So the container passes `-e SIM_OLLAMA_HOST=host.docker.internal:11434`, and native runs (no env var) are unchanged. **Confirm this is actually present in `simulation/server.py` before you begin.** If the constant is still hardcoded, **stop and say so** — do not work around it with `/etc/hosts` rewriting, `--network host`, or an env var the app doesn't read.

Note the value is **host:port only** — the `/api/chat` path is deliberately fixed in code. Do not propose changing that, and do not construct a full-URL env var: `ollama_config.md` documents that the OpenAI-compat `/v1/chat/completions` endpoint silently ignores `think:false`, and keeping the path unconfigurable is what makes that endpoint unreachable by misconfiguration.

## Goal

Produce a `Dockerfile` (plus `.dockerignore`, and `compose.yaml` only if justified — see constraint 3) that runs the existing Flask app in `simulation/server.py` inside one container, connecting to an Ollama instance that stays running on the host — nothing else.

## Hard constraints (do not deviate)

1. **One service, one container.** Do not add nginx, a reverse proxy, a database container, Redis, or any second service. This is a single Flask process (`app.run(..., threaded=True)`) — the container's job is to run exactly that.
2. **Do not containerize Ollama.** Ollama runs natively on the host at `http://localhost:11434` and is already near its VRAM ceiling (RTX 3060 12 GB, ~225–1,400 MiB free with both required models resident — see `ollama_config.md`). The container reaches it via `host.docker.internal:11434` (Docker Desktop's built-in host alias on Windows/Mac). Do not propose a `--network host` workaround, a GPU-passthrough setup, NVIDIA Container Toolkit config, or an Ollama image.
3. **`compose.yaml` is your call — but justify it.** The user has explicitly left this to your judgement. The workload is one service, one published port, three bind mounts, and a few env vars. If a plain `docker run` one-liner is just as clear, **skip compose entirely** rather than adding a file for its own sake. If you do propose compose, state in one sentence why it is meaningfully better than the `docker run` equivalent. Either answer is acceptable; an unjustified compose file is not.
4. **Base image:** use a slim, minimal Python 3.12 image (e.g. `python:3.12-slim`) matching `requires-python = ">=3.12"` in `pyproject.toml`. Do not select a larger/GPU/CUDA base image — this container does no ML inference itself.
5. **Dependency install:** use `uv` (the project's actual tool — see `pyproject.toml` + `uv.lock`) to install dependencies inside the image. Do not invent a parallel `pip install -r requirements.txt` flow; there is no `requirements.txt` in this repo and one should not be created for this purpose.
6. **Single-stage build by default.** Only propose a multi-stage build if there's a concrete reason (e.g. `uv` needs to be excluded from the final image for size) — state the reason explicitly if you do. Do not add build stages speculatively.
7. **Exposed/published port: 5001 only.** The app deliberately avoids port 5000 (macOS AirPlay conflict on the maintainer's other machines). `EXPOSE 5001` in the Dockerfile; `-p 5001:5001` / `ports: ["5001:5001"]` at run time. Do not remap.
8. **Runtime state must NOT be baked into the image.** These paths are gitignored and must be **bind mounts to their existing host paths** — not named Docker volumes. `CLAUDE.md` treats `simulation/logs/<timestamp>/llm.jsonl` as the primary debugging surface and it must stay readable at the same host path it is today. Writable from inside the container:
   - `simulation/state.db` (+ `-wal`/`-shm` siblings)
   - `simulation/logs/`
   - `simulation/memory_store.json`

   These paths must persist across container restarts and must never be committed or added to the image layer.
9. **No `.dockerignore` gaps that leak secrets or bloat the image.** Exclude at minimum: `.venv/`, `.git/`, `simulation/logs/`, `simulation/state.db*`, `simulation/memory_store.json*`, `simulation/_vendor/`, `docs/archive/`, `scripts/out/`, `__pycache__/`, `*.pyc`.
10. **Environment variables the container must accept** (do not hardcode into the image — pass at run time):
    - `SIM_HOST` / `SIM_PORT` — already read by `simulation/server.py:3746-3747` (defaults `0.0.0.0` / `5001`); pass through, don't reimplement.
    - `SIM_AGENTS` (roster size override) — an existing, documented env var (see `README.md`); pass through, don't reimplement.
    - `SIM_OLLAMA_HOST` — set to `host.docker.internal:11434` for container runs (see the Precondition section).
11. **Single-instance discipline.** Name the container explicitly in run instructions (`--name gitserv-sim`), not anonymous/`--rm`-only, so `docker ps` / `docker stop gitserv-sim` is the obvious way to check for and stop an existing instance before starting another. This repo has a standing rule against ever running two server instances at once. The native `uv run` path is being **kept as a documented fallback**, so both ways of starting the server stay live permanently — your run instructions must make it trivial, not merely possible, to confirm nothing else is already running (container *and* native).
12. **Run in the foreground, in a titled window — do not use `-d`.** `CLAUDE.md` requires the server run in its own visible, titled `cmd` window, never backgrounded, and that convention carries over. The documented start command should be shaped like:
    ```
    Start-Process cmd.exe -ArgumentList '/k', 'title simserver && docker run --name gitserv-sim ...'
    ```
    Closing the window must stop the sim, exactly as it does today. Do not propose a detached container plus a `docker logs -f` window.
13. **No `--restart` policy.** Do not add `--restart unless-stopped` or any equivalent, even though the sim runs 24/7. Crashes must stay visible rather than silently looping — a crash-loop would churn `state.db` and spawn junk log sessions unnoticed. Whether to add one later is an explicit post-soak decision that is not yours to make.
14. **No health-check/orchestration machinery beyond Docker's basics** unless a concrete failure mode requires it — no supervisor process, restart-loop wrapper, or process manager. `CMD ["uv", "run", "python", "simulation/server.py"]` (or equivalent) is the expected shape.
15. **Do not modify application source.** No changes to `simulation/server.py`, `simulation/sim_engine/`, `simulation/_server/`, or any file under `specs/`. This is a packaging change only — the one permitted source change (the Ollama URL) belongs to Phase 1 and happens before you run.

## Files Gordon should propose

- `Dockerfile` (repo root)
- `.dockerignore` (repo root)
- `compose.yaml` (repo root) — only per constraint 3, with justification

## Files Gordon should NOT touch

- Anything under `simulation/` except reading it for context.
- Anything under `specs/`, plus `AGENTS.md`, `CLAUDE.md`, `README.md`. These *will* be rewritten — Docker becomes the supported run path (D4) — but that is **Phase 4**, a spec-driven-development step with its own implementer/reviewer pass. It is not yours to do here, even though you can see it needs doing.
- `scripts/ollama_setup.py` and any other host-Ollama-configuration script — these configure the host, not the container, and are out of scope.

## Verification checklist (for whoever runs Phase 3 later — not part of this planning task)

- [ ] `docker build` succeeds from repo root.
- [ ] Container starts, binds `5001`, and `http://127.0.0.1:5001` serves the viewer.
- [ ] Container reaches host Ollama via `SIM_OLLAMA_HOST=host.docker.internal:11434` — `sim-smart`/`sim-fast` decisions succeed, visible in the bind-mounted `simulation/logs/<timestamp>/llm.jsonl`. (Requires Phase 1; see Precondition.)
- [ ] Native run still works unchanged with `SIM_OLLAMA_HOST` unset — Phase 1 must be backward-compatible, and the native path is a supported fallback, not a dead one.
- [ ] Logs land at the same host path as before, readable without `docker cp` (bind mount, not a named volume).
- [ ] `simulation/state.db` persists across a container restart (bind mount confirmed, not baked into image).
- [ ] Container runs in the foreground in a titled window; closing the window stops the sim.
- [ ] Only one instance running at a time — `docker ps` **and** the native `pgrep -fa "simulation/server.py"` check from `CLAUDE.md`, combined ≤1.
- [ ] Image does not contain `.venv/`, `.git/`, logs, or `state.db` (`docker history` / `docker run --rm <image> ls -la` spot check).
- [ ] **Post-soak:** record whether a `--restart` policy is warranted (see constraint 13) — write the decision down in the plan, don't leave it implicit.
