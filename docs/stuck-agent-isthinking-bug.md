# Bug: agents permanently stuck after a restart (`isThinking` never resets)

**Status:** fixed 2026-07-02 — `restore_state()` now forces `isThinking = False`
on every agent as it loads, and `_serialize_state()` no longer persists the
flag at all.
**Discovered:** 2026-07-02, while reviewing logs from a session running since `2026-07-02T00-11-15`.

## Symptom

After restarting the server, most of the village goes silent: no conversations,
no `assign_task`, no `heal_agent`, no `propose_blueprint` — the elder in
particular never seems to lead. It looks like the LLM has stopped choosing a
whole category of actions.

## What's actually happening

It isn't an LLM behavior problem. **Most agents are permanently unable to think
at all.** In the session under review, only 2 of 8 agents (Luna, Colt) ever
produced a single decision in over 8 hours and 1,913 total LLM calls — the
other 6 (including Sage, the elder) had **zero** entries in `lm_studio.jsonl`
for the entire session.

Confirmed live via `GET /state`: `isThinking: true` for Zara, Sage, Aria,
Marco, Finn, and Mia; `false` only for Luna and Colt. An agent whose
`isThinking` flag is stuck `true` can never be scheduled again, because the
tick loop's think-dispatch gate requires it to be `false`:

```2732:2732:simulation/sim_engine.py
                if a["thinkTimer"] <= 0 and not a["isThinking"] and a["name"] not in self._inflight:
```

The only code path that ever sets `isThinking` back to `False` is the
`finally` block inside `_think_job`, which runs in the process that scheduled
the think:

```2652:2657:simulation/sim_engine.py
        finally:
            with self.lock:
                a = self._find_agent(agent_name)
                if a:
                    a["isThinking"] = False
                self._inflight.discard(agent_name)
```

## Root cause

The engine autosaves the full world to `simulation/state.json` every 10
seconds (`AUTOSAVE_SECONDS = 10`, `sim_engine.py:32`), and that snapshot
includes each agent's `isThinking` flag verbatim — whatever it happens to be
at that instant, including `true` if a think request is in flight.

`restore_state()` loads agents straight back from that JSON with **no
sanitization of transient runtime flags**:

```2882:2905:simulation/sim_engine.py
                agents = []
                is_scaffold = self.d.get("is_scaffold_text")
                for ad in (data.get("agents") or []):
                    a = dict(ad)
                    a["beliefs"] = set(a.get("beliefs") or [])
                    ...
                    agents.append(a)
                if not agents or not civ:
                    return False
                if data.get("version") == 1:
                    self._migrate_v1_to_v2(civ, agents)
                self.civilization = civ
                self.agents = agents
```

If the process is killed (or crashes) at a moment when the last-written
`state.json` had `isThinking: true` for some agent — because that agent's
think request just happened to be in flight during the most recent 10-second
autosave — that agent comes back from the next restart permanently wedged.
Nothing in the new process will ever schedule a think for it again, because
the flag it inherited from the old process's mid-flight moment is
meaningless in the new process's memory, yet the gate still respects it
forever.

`MAX_CONCURRENT_LLM = 2` (`sim_engine.py:295`), so at most 2 agents can be
genuinely in flight at any single instant. 6-of-8 agents ending up stuck is
consistent with this having happened across **multiple** restart/kill cycles
during the same dev session (each one freezing up to 2 more agents), rather
than a single occurrence.

Killing the server process forcefully (e.g. `Stop-Process -Force`, or any
hard kill) makes this worse: it bypasses the graceful shutdown save
(`atexit` / `SIGINT` / `SIGTERM` handlers in `server.py:2089-2108`) and
leaves whatever the last periodic autosave happened to capture, up to 10
seconds stale.

## Why this matters beyond "agents seem quiet"

Every feature that depends on a specific agent (especially the elder) getting
a think turn is silently starved:

- The elder's `assign_task` MAIN RULE never fires if Sage is stuck.
- Invention-gating pressure (escalating nudges, the elder backstop) never
  escalates if Sage never thinks.
- `talk_to_nearby`, `heal_agent`, `trade_resource`, `propose_blueprint`,
  `craft_item`, `propose_rule` / `vote_rule` all become unreachable for any
  frozen agent, shrinking the effective action space for the whole village to
  whatever the surviving agents happen to do (in the observed session:
  `move_to_district`, `collect_resource`, `contribute_resources`,
  `start_project`, `build_structure` only).

This can make an otherwise-correct feature (e.g. the invention-gating or
commitment work) look broken or absent in testing, when the actual defect is
upstream in persistence/restore, not in the feature itself.

## Fix (applied 2026-07-02)

Two parts, both in `simulation/sim_engine.py`:

1. **Root fix:** `restore_state()` forces `isThinking = False` for every agent
   as it's loaded, so a stale mid-flight snapshot can never brick an agent
   across a restart. Because the sanitization happens at load time, no manual
   edit of an already-poisoned `state.json` is needed — a plain restart heals
   it. `_inflight` itself needs no attention — it is a runtime-only Python
   `set()` that is always empty at construction and is never persisted.
2. **Belt-and-braces:** `_serialize_state()` excludes `isThinking` from the
   persisted agent dict entirely (alongside `beliefs`), so future saves never
   capture the transient flag in the first place. The live `GET /state`
   snapshot (`snapshot()`) still reports it — that's how this bug was
   diagnosed.

## How to reproduce / verify

1. Start the server against a `state.json` known to be clean (fresh cold
   start), let it run, and force-kill the process (not Ctrl-C) at an arbitrary
   moment.
2. Restart and poll `GET /state`; check each agent's `isThinking` value.
3. Any agent showing `isThinking: true` immediately after restore is
   reproducing this bug — confirm by tailing `lm_studio.jsonl` and observing
   that agent never appears again.

After the root fix lands, step 3 should show every agent at `isThinking:
false` immediately post-restore, and all 8 agents should reappear in
`lm_studio.jsonl` within a few think intervals.
