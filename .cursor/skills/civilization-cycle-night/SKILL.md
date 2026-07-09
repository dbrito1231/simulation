---
name: civilization-cycle-night
description: >-
  Run the evening slot of the civilization emergence compressed cycle: stop the
  local sim server, audit the day soak logs, hot-fix or implement the next
  batch, review commits, restart the server. Use when the user asks to run the
  evening cycle, night audit, Part 8 evening slot, or manually check the
  daytime soak.
disable-model-invocation: true
---

# Civilization cycle — evening slot

Manual run of the **evening** compressed twice-daily cycle for the AI village simulation.

## Read first

1. [docs/HANDOFF.md](docs/HANDOFF.md) — current snapshot (verify against live state)
2. [docs/civilization-emergence-plan.md](docs/civilization-emergence-plan.md) — **Part 8** ("Compressed cadence") is authoritative
3. [CLAUDE.md](CLAUDE.md) — architecture and commands
4. [.claude/overnight-cycle.json](.claude/overnight-cycle.json) — `lastReviewedCommit`, `iteration`, `phase`, `still_open`
5. [.cursor/next-prompt.md](.cursor/next-prompt.md) if it exists — queued implementation work

## Preflight

- Work only on branch `feat/server-authoritative-engine`. No worktrees, no new branches, no push.
- **Never run a second `simulation/server.py`** — `state.json` is shared; a second instance races the live server.
- For GUI-only checks, use the `gui-static-preview` launch config (port 8899), not another engine.
- Confirm LM Studio: `lms ps` or `GET http://localhost:1234/v1/models`.

## Execute Part 8 steps 1–7

Track progress with this checklist:

```
- [ ] 1. Stop server
- [ ] 2. Audit soak
- [ ] 3. Hot-fix or queue loop-back
- [ ] 4. Implement + review (if due)
- [ ] 5. Commit cycle verdict
- [ ] 6. Restart server
- [ ] 7. Plain-language summary
```

### 1. Stop the server

```powershell
taskkill /F /FI "WINDOWTITLE eq SimServer*" 2>$null
# then taskkill /F /PID <pid> for anything still on port 5001
(Get-NetTCPConnection -LocalPort 5001 -State Listen -ErrorAction SilentlyContinue | Measure-Object).Count
```

Flag loudly if the server was not running (standing rule violated).

### 2. Audit the soak

- Newest folder under `simulation/logs/<timestamp>/`
- Audit against the civilization test of **every phase flag that was ON**, judging each flag independently (grep/count JSONL evidence)
- Soaks ≥4h → full verdict; shorter → provisional
- Append verdicts to the phase logs in `docs/civilization-emergence-plan.md` Part 4

### 3. On FAIL

- **Small precise FAIL** (tuning constant, interface bug) → hot-fix in this session
- **Design-level FAIL** → write loop-back prompt to `.cursor/next-prompt.md`; pull forward the next non-dependent batch item

### 4. Implement (if due)

If `.cursor/next-prompt.md` exists or the batch schedule says work is due:

- Launch a **generalPurpose** subagent as implementer (`feat/server-authoritative-engine` only)
- Subagent must **force-smoke-test** new mechanics in a short live run before committing
- Review `lastReviewedCommit..HEAD` for two invariants: no silent rejections; every gate has a deterministic escape
- Fix small issues, commit, update `.claude/overnight-cycle.json` (`lastReviewedCommit`, `iteration`, `note`, `still_open`)
- Run `py_compile` on `simulation/sim_engine.py` and `simulation/server.py`

If nothing is queued (soak-only slot), skip implementation and say so.

### 5. Commit plan updates

Commit message format: `Cycle N.evening: <one-line verdict summary>`

Include plan-doc / `overnight-cycle.json` updates. Do not push.

### 6. Restart the server (mandatory)

```powershell
Start-Process cmd -ArgumentList '/k', 'title SimServer && cd /d C:\Users\dbadmin\Desktop\GitServ\simulation && uv run python simulation/server.py'
```

Health check: HTTP 200 from `http://127.0.0.1:5001/` and newest `simulation/logs/<ts>/` `.jsonl` files growing.

**Sole exception:** LM Studio down → report loudly, leave server off.

### 7. Summary for the user

Plain language:

- Per-flag verdicts and key numbers
- What changed (commits, hot-fixes)
- What the **morning** slot should do next
- Any `still_open` watch items

## Standing rules

- Sim runs 24/7 in the visible **SimServer** cmd window — never a background Bash task
- Phase advancement requires PASS on prerequisites; audit-then-implement in one slot is intentional
- Implementation invariants: feature flags, no per-tick LLM calls, no silent rejections, deterministic escape hatches, `state.json` back-compat via `setdefault`, observability in the same change
- Before manually editing `state.json`, confirm port 5001 is free

## Quick health commands

```powershell
Invoke-WebRequest http://127.0.0.1:5001/ -UseBasicParsing | Select StatusCode
```

```bash
curl -s http://127.0.0.1:5001/state | python -c "import json,sys; d=json.load(sys.stdin); print(d['frameTick'], d['civilization']['era'], d['config']['flags'])"
```
