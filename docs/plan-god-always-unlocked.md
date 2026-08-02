# Plan — Divine Console always unlocked (no token prompt)

**Status:** implemented 2026-07-29. Scope decided: auth off, `HOST` stays `0.0.0.0` (LAN-wide access is a requirement — Phase 4 cut).
**Owner specs:** [specs/12-ops.md](../specs/12-ops.md) (God mode ops/security contract), [specs/04-http-api.md](../specs/04-http-api.md) (route auth), [specs/01-architecture.md](../specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy) (flag index), [specs/11-viewer.md](../specs/11-viewer.md) (viewer gating).
**Goal:** the Divine Console bar renders with **every button enabled and usable immediately** on page load — no Unlock step, no token entry, no per-tab re-auth.

---

## Current gating chain

Three *independent* gates must all pass before a God button does anything. "Always unlocked" means neutralizing gates 2 and 3 while leaving gate 1 intact.

1. **Bar visibility** — `GOD_MODE_ENABLED` ([sim_engine.py:386](../simulation/sim_engine.py:386)) is echoed into `/state` `config.flags` ([sim_engine.py:17107](../simulation/sim_engine.py:17107)); the viewer's `updateGodModeGate()` ([index.html:4979](../simulation/index.html:4979)) shows/hides `#divineBar` from it. **Already defaults on** — no change needed.
2. **Server-side token auth** — `GOD_TOKEN` is read once at import ([server.py:4194](../simulation/server.py:4194)); `GOD_ROUTES_ACTIVE = GOD_MODE_ENABLED and bool(GOD_TOKEN)` ([server.py:4195](../simulation/server.py:4195)); `_god_authorized()` ([server.py:4207](../simulation/server.py:4207)) requires that *plus* an `X-God-Token` header matching via `hmac.compare_digest`. Every `/control/god/*` route calls it and returns a uniform 401 ([server.py:4216](../simulation/server.py:4216)) otherwise.
3. **Client-side lock UI** — `godAuthorized` starts false; `updateDivineBarAuthUi()` ([index.html:4056](../simulation/index.html:4056)) sets `disabled` on every `.gbtn.locked-dependent`, so seven of the eight buttons are literally unclickable until `godConnect()` ([index.html:4088](../simulation/index.html:4088)) succeeds. Any 401 calls `godLockConsole()` ([index.html:4073](../simulation/index.html:4073)), which clears the token and reopens the Unlock modal.

Removing only gate 3 would produce clickable buttons whose every request 401s. Gates 2 and 3 must change together.

---

## Security posture — decided

`HOST` defaults to **`0.0.0.0`** ([server.py:4499](../simulation/server.py:4499)), i.e. the server listens on every interface, and the existing comment there already notes this exposes it to the whole local network. The token is currently the *only* thing standing between any device on that network and the full God API — a surface that can heal/damage agents, grant resources, spawn storms, and despawn wildlife.

**Decision (2026-07-29): LAN-wide access is a requirement.** The viewer must stay reachable from any device on the network, so the server keeps binding `0.0.0.0` and loopback-only binding is off the table. Combined with auth off, the accepted consequence is:

> Any device on the local network can reach `/control/god/*` and mutate the world without credentials.

The owner has confirmed this deployment runs on a permanently trusted network, and that **how to lock it down is a later decision, deliberately deferred**. This is settled — treat it as a resolved design constraint, not an open risk to re-raise in future passes on this file.

Two facts recorded for whenever that later decision happens:

- The exposure is **mutation**, not just observation — a LAN client can change world state, not merely watch it. The `divine.jsonl` audit trail still records every applied intervention, so anything unexpected is attributable after the fact.
- The cheapest future upgrade is **Phase 4-alt** below — a source-IP allowlist on the God routes only, preserving LAN viewing for everyone while restricting mutation to named hosts. Written up, deliberately **not** part of this change.

---

## Plan

### Phase 1 — New flag: `GOD_AUTH_REQUIRED`

Introduce one module-level flag rather than deleting the auth code, so the token path stays available and this is a one-line revert.

- Add `GOD_AUTH_REQUIRED` next to `GOD_MODE_ENABLED` in [sim_engine.py:386](../simulation/sim_engine.py:386), env-backed and read-once-at-import in the identical idiom: `str(os.environ.get("SIM_GOD_AUTH", "0")).strip().lower() in ("1","true","yes","on")` — i.e. **default `False` (no auth required)**, per the request, with `SIM_GOD_AUTH=1` restoring today's token gate.
- Echo it to the viewer alongside `GOD_MODE_ENABLED` in the `config.flags` block ([sim_engine.py:17107](../simulation/sim_engine.py:17107)) so the client can gate its UI off server truth rather than a hardcoded twin.
- Keep the flag comment block explicit that this is a **security-relevant** default and cross-reference the bind-host note.

### Phase 2 — Server: honor the flag

- `GOD_ROUTES_ACTIVE` ([server.py:4195](../simulation/server.py:4195)) becomes `GOD_MODE_ENABLED and (bool(GOD_TOKEN) or not GOD_AUTH_REQUIRED)` — routes go live without a token when auth isn't required.
- `_god_authorized()` ([server.py:4207](../simulation/server.py:4207)): after the `GOD_ROUTES_ACTIVE` check, return `True` immediately when `not GOD_AUTH_REQUIRED`; otherwise fall through to the existing `compare_digest` path **unchanged**.
- Replace the startup warning at [server.py:4196](../simulation/server.py:4196) with a branch: when auth is disabled, print a clear one-line **security banner** naming the bind host, so an unauthenticated God API is never silent. When auth is required and the token is missing, keep today's warning verbatim.
- Leave `_god_unauthorized_response()` and every route body untouched — the change is confined to the predicate.

### Phase 3 — Viewer: no Unlock step

- Add `GOD_AUTH_REQUIRED_FLAG`, mirrored from `config.flags` in `applyFlags`, same pattern as `GOD_MODE_ENABLED_FLAG`.
- In `updateDivineBarAuthUi()` ([index.html:4056](../simulation/index.html:4056)), treat "effectively authorized" as `godAuthorized || !GOD_AUTH_REQUIRED_FLAG`, so no `.gbtn.locked-dependent` is ever `disabled` in no-auth mode. The `disabled` attributes hardcoded in the bar markup (`index.html` ~1214–1253) must also be cleared on first gate evaluation, since they apply before any JS runs.
- When auth isn't required: set `godAuthorized = true` once at startup, hide the **Unlock** button and its `statuspip` entirely (it has nothing to do), and change the brand-state text ([index.html:4057](../simulation/index.html:4057)) from `locked`/`authorized` to something honest like `open`.
- Fetch `/control/god/capabilities` **once at load** without a token so `godCapabilities` is populated (it drives form bounds via `applyGodCapabilitiesToForms()`) and `populateGodAgentSelects()` runs — today both only happen inside `godConnect()`.
- `godApiFetch()` ([index.html:4027](../simulation/index.html:4027)) already omits the header when `godToken` is null; no change needed.
- Make `godLockConsole()` ([index.html:4073](../simulation/index.html:4073)) a **no-op re-lock** in no-auth mode — otherwise an unrelated 401 (or a `GOD_MODE_ENABLED` race) would pop the now-hidden Unlock modal and strand the user. Log to console instead.
- Skip `restoreGodTokenFromSession()` ([index.html:4162](../simulation/index.html:4162)) and the "remember" wiring in no-auth mode; leave both intact for when auth is on.
- Keep `openDivineModal('unlock')` reachable as dead-but-harmless code so re-enabling auth needs no viewer changes.

### Phase 4 — ~~Bind to loopback~~ (CUT — conflicts with the LAN requirement)

**Do not implement.** Changing `HOST`'s default at [server.py:4499](../simulation/server.py:4499) to `127.0.0.1` would break viewing the sim from other devices, which is a stated requirement. `HOST` stays `0.0.0.0`. Recorded here so a future reader doesn't "helpfully" harden it and silently break phone/tablet access.

### Phase 4-alt — Source-IP allowlist for God routes (NOT in this change; future option)

Written up only as the escape hatch referenced in the security section above. Preserves LAN-wide viewing while limiting *mutation* to named hosts:

- Add a `GOD_ALLOWED_IPS` env-backed flag (comma-separated; empty = allow all, preserving current behavior).
- In `_god_authorized()` ([server.py:4207](../simulation/server.py:4207)), when the list is non-empty, reject any request whose `request.remote_addr` isn't in it — reusing the existing uniform 401 so the failure stays indistinguishable from a bad token.
- Caveat to check before relying on it: `remote_addr` is trivially spoofable on a hostile network and DHCP can reassign addresses, so this is a guardrail against accident, not an attacker. It also needs care behind any reverse proxy (`X-Forwarded-For`).

### Phase 5 — Startup script cleanup

`SIM_GOD_TOKEN` in `scripts/sim_schedule_start.ps1` becomes unused once auth is off. **Leave the line in place** (harmless, and it's the escape hatch if `SIM_GOD_AUTH=1` is ever set) but add a comment noting it only takes effect when auth is required. Mirror the comment into `scripts/sim_schedule_start.ps1.example`.

---

## Restart procedure (Task Scheduler)

`GOD_AUTH_REQUIRED`, `GOD_TOKEN`, and `HOST` are all **read once at import** — a restart is mandatory for any of these to take effect. Use the scheduled tasks, not a manual launch, so the server keeps the same environment it normally runs with:

```powershell
Start-ScheduledTask -TaskName "SimVillage Stop"
```

Confirm the process is gone before starting (the stop task also unloads both Ollama models):

```powershell
Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation[\\/]server\.py' }
```

Then start it again:

```powershell
Start-ScheduledTask -TaskName "SimVillage Start"
```

The start task runs `scripts/ollama_setup.py` first, so the server process takes ~30–60 s to appear — wait for it rather than assuming failure. Note the task's `LastTaskResult` may be non-zero even on success (it reflects the launching script, not the server).

**Single-instance check (required last step per [CLAUDE.md](../CLAUDE.md)):**

```powershell
Get-CimInstance Win32_Process -Filter "name='python.exe'" | Where-Object { $_.CommandLine -match 'simulation[\\/]server\.py' } | Select-Object ProcessId, ParentProcessId, CreationDate
```

Expect **two** `python.exe` rows with the same `CreationDate` and a parent→child link — that is `uv run`'s wrapper/interpreter pair, i.e. one instance. More than one *pair*, or pairs with different creation times, means a stale server is still up; kill the older one.

---

## Verification

No test suite — verify against the running server.

1. **Auth off, no token sent** — should now be `200`:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5001/control/god/capabilities
   ```
2. **Startup banner** — confirm the `simserver` window prints the unauthenticated-God-API security line naming the bind host.
3. **Viewer** — hard-reload `http://127.0.0.1:5001`; confirm the bar shows with **no Unlock button**, every remaining button enabled and clickable from the first frame, and the brand state reading `open`.
4. **LAN reachability (requirement regression check)** — load the viewer from a second device on the network via `http://<this-machine-ip>:5001` and confirm both the world renders *and* the Divine buttons are live there too. This is the check that would catch an accidental loopback bind.
4. **End-to-end mutation** — run one Preview→Apply (a Proclamation is the safest: public, no vitals touched) and confirm it lands in the Chronicle and in `simulation/logs/<timestamp>/divine.jsonl`.
5. **Revert path** — restart once with `SIM_GOD_AUTH=1` set and confirm the Unlock modal returns and a tokenless request 401s again. This proves the flag is a real switch, not a one-way deletion.
6. **Regression smokes** — `uv run python scripts/god_mode_smoke.py` (it drives the God routes and may itself assume token auth — expect to update it in Phase 2), plus `uv run python scripts/sid_parity_smoke.py` and `uv run python scripts/path1_smoke.py`.

## Spec obligations (SDD — same change, not a follow-up)

- **[specs/12-ops.md](../specs/12-ops.md)** — rewrite the God-mode security contract: two-gate model (flag + optional auth), the new default, and the bind-host interaction.
- **[specs/04-http-api.md](../specs/04-http-api.md)** — `/control/god/*` auth is now conditional; document that a tokenless 200 is expected when `GOD_AUTH_REQUIRED` is false.
- **[specs/01-architecture.md](../specs/01-architecture.md#flag-index-complete--30-module-level-flags-sim_enginepy)** — add `GOD_AUTH_REQUIRED` to the flag index and to the env-var-backed-flag precedent note.
- **[specs/11-viewer.md](../specs/11-viewer.md)** — Divine Console no longer has an unlock lifecycle when auth is off.
- No new actions, so the action-sync invariant is untouched.

## Delegation

Per [CLAUDE.md](../CLAUDE.md)'s model policy, dispatch Phases 1–3 and 5 to the `implementer` subagent (Sonnet 5) as separate reviewed steps. Phase 4 is cut; Phase 4-alt is out of scope. Restart + single-instance verification is the last step of whichever phase lands last.
