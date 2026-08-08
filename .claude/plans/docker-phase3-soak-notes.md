# Docker Phase 3 verification + D8 soak notes

**Worktree:** `C:\Users\dbadmin\Desktop\GitServ\simulation\.claude\worktrees\docker-migration`  
**Branch:** `worktree-docker-migration`  
**Image:** `gitserv-sim:phase2` (existing; no rebuild)  
**Container started:** 2026-08-07 ~12:54 PM local (16:54 UTC session `2026-08-07T16-54-50`)

## Bind-mount prep (Windows)

Pre-created before first `docker run`:

- `simulation/logs/` — directory
- `simulation/state.db` — empty file (0 bytes); SQLite grew to 118784 bytes on first run
- `simulation/memory_store.json` — `{}` (2 bytes)

**First start omitted** `-wal` / `-shm` mounts (files did not exist). After stop/start, still no `state.db-wal` or `state.db-shm` on host — only `state.db`. No wal/shm bind mounts used.

## Run command (foreground, D7)

```powershell
Start-Process cmd.exe -ArgumentList '/k', 'title simserver && docker run --name gitserv-sim -p 5001:5001 -e SIM_OLLAMA_HOST=host.docker.internal:11434 -v "%CD%\simulation\state.db:/app/simulation/state.db" -v "%CD%\simulation\logs:/app/simulation/logs" -v "%CD%\simulation\memory_store.json:/app/simulation/memory_store.json" gitserv-sim:phase2' -WorkingDirectory 'C:\Users\dbadmin\Desktop\GitServ\simulation\.claude\worktrees\docker-migration'
```

No `-d`, no `--restart`. `docker inspect` restart policy: `no`.

## Verification checklist

| Item | Result | Evidence |
|------|--------|----------|
| Container up, port 5001, HTTP | **PASS** | `docker ps`: `0.0.0.0:5001->5001/tcp`; `Invoke-WebRequest http://127.0.0.1:5001/state` → 200 (~14.9 KB) within 0s of poll |
| Ollama via `host.docker.internal:11434` | **PASS** | `docker exec gitserv-sim printenv SIM_OLLAMA_HOST` → `host.docker.internal:11434`; Python probe inside container: `tags_ok 6` including `sim-smart:latest`, `sim-fast:latest`; bind-mounted `simulation/logs/2026-08-07T16-55-37/llm.jsonl`: 85 lines, all `http_status: 200` with decisions (e.g. Marco `start_project` at 16:55:41 UTC). First session had one cold-start `llm timeout` then successful calls after model warm |
| Logs on host (no `docker cp`) | **PASS** | Host paths under `simulation/logs/2026-08-07T16-54-50/` and `2026-08-07T16-55-37/` with `activity.jsonl`, `llm.jsonl`, etc. |
| `state.db` persists across stop/start | **PASS** | `docker stop gitserv-sim` then `docker start gitserv-sim`; at-stop SHA256 `210FDFE6F99D3D17B90FAE8F683C41B52DBCA9196B52DA52D809E35AE215B5A6` matched after-start; size 118784; HTTP 200 after restart |
| Foreground titled window | **PASS** | `Start-Process cmd.exe` with `title simserver`; `Get-Process` shows `WindowsTerminal` window title `simserver` (PID 25196). Not `-d` |
| Combined instance count ≤1 | **PASS** | `docker ps`: one `gitserv-sim`; native `simulation.server` python processes: none |
| Image lacks host logs/state | **PASS** | `docker run --rm gitserv-sim:phase2 sh -c "ls state.db logs"` → no `state.db` or `logs` in image layer |

## Dockerfile / mount adjustments

- **No Dockerfile or `.dockerignore` changes.**
- **Mount adjustment vs Dockerfile comment:** omitted wal/shm on first and subsequent starts because host never produced separate wal/shm files (SQLite WAL mode may be off or consolidated in main db).

## D8 provisional decision (restart policy)

**Keep no `--restart` for now.** Container started 2026-08-07 ~12:54 PM local for intended 24/7 soak; no crash evidence yet. Revisit after longer soak (days) or first unexpected exit — do not add `--restart` without crash/soak evidence.

## Windows bind-mount observations

- Pre-creating `state.db` and `memory_store.json` as **files** before mount worked; empty `state.db` initialized correctly.
- `logs/` as directory mount worked; session subdirs created by server on host.
- No accidental directory-over-file mounts observed.

## Current state (end of Phase 3)

- Container `gitserv-sim` **running** (`docker ps`), image `gitserv-sim:phase2`
- Soak **left running** for ongoing observation
- Second log session `2026-08-07T16-55-37` active after `docker start` (new server run; state restored from bind-mounted `state.db`)
