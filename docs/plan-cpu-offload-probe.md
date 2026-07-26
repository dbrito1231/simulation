# Plan: CPU-offload probe for the always-on whiteboard retry

Status: proposed (2026-07-25). Owner: orchestrator + one `implementer`
subagent (Sonnet 5) per [CLAUDE.md](../CLAUDE.md#model-policy), including
the usage-limit pause rule. This is a **probe, not a feature** — read-mostly,
one throwaway model, zero changes to sim behavior. Its output is a
go/no-go verdict on retrying [plan-always-on-modules.md](plan-always-on-modules.md)
Phase B with `sim-fast` pinned to the CPU.

## Why

The always-on Phase B gate failed twice on a single-GPU contention squeeze
(record: `ollama_config.md` "Always-on PIANO Phase B gate"; verdict in
[plan-sid-parity-gaps.md](plan-sid-parity-gaps.md)): batch 2 kept notes
fresh but taxed decision p50 +17.7%; batch 1 passed latency but notes went
10–17 min stale. The two gates share one resource — the RTX 3060.

Hardware audit (2026-07-25): i7-12700KF (12C/20T), 32 GB RAM, no
`OLLAMA_NUM_THREADS`-style env set. Ollama supports per-model `num_gpu 0`
(full CPU inference). Pinning `sim-fast` (llama3.2:3b Q4, ~2 GB) to CPU
would put decisions and module refreshes on **separate compute pools**,
decoupling the two failed gates. The always-on design tolerates slow
refreshes by construction (nothing waits on them), so CPU-speed inference
is acceptable *if* throughput clears the freshness bar and the sim's own
tick loop doesn't starve.

## The two questions the probe answers (nothing else)

1. **Throughput:** what is real end-to-end latency + tokens/sec for
   module-sized calls on CPU-pinned llama3.2:3b, at concurrency 1 and 2?
   Derived: refreshes/minute at concurrency 2 vs. the ~32-notes roster
   demand (8 agents × 4 modules; only dirty notes refresh, so steady-state
   demand is far lower — the plan's freshness bar is median ≤ 120 s).
2. **Tick integrity:** does the engine's 30/s tick hold while CPU inference
   runs underneath, and does decision latency on the GPU stay at its
   uncontended baseline?

## Probe steps (single implementer dispatch)

Branch: work directly on `main` — the probe adds no repo code except one
scratch script and a findings record; no sim source files change.

1. **Create the throwaway model.** Write a scratch Modelfile (in the agent
   scratchpad or `simulation/logs/`-adjacent temp, NOT in `ollama/`) that is
   a copy of `ollama/Modelfile.fast`'s FROM/PARAMETER lines plus
   `PARAMETER num_gpu 0`. `ollama create probe-fast-cpu -f <file>`. Verify
   via `ollama ps` after a warm call that its `PROCESSOR` column reports
   CPU (not GPU) residency, and confirm `sim-smart`/`sim-fast` were not
   evicted (`/api/ps`).
2. **Thread cap.** Also test with `num_thread 8` as a second variant
   (probe-fast-cpu8) — the candidate "leave the P-cores for the engine"
   config. Same Modelfile plus `PARAMETER num_thread 8`.
3. **Throughput measurement.** Take 10 real module prompts from a recent
   session's `llm.jsonl` (records where the system prompt names the
   Perception/Social/Desire/Reflection module; the sessions from 2026-07-25
   have hundreds). For each variant (unpinned-threads, num_thread 8):
   replay them at concurrency 1, then concurrency 2 (two parallel callers,
   matching `PIANO_CONCURRENT_LLM`). Record per-call wall time,
   `eval_count`/`eval_duration` (tokens/sec), and compute refreshes/minute
   at concurrency 2. Use `/api/chat` with `stream:false`, `num_predict 60`,
   the same options server.py sends. 40 calls total per variant is enough.
4. **Tick-integrity check.** With the sim server running normally (GPU
   decisions active), run the concurrency-2 CPU replay loop continuously
   for 10 minutes. During it: (a) sample `/state`'s `frameTick` every 15 s
   and compute effective ticks/sec (must hold ~30; the engine is
   server-authoritative so frameTick delta / wall delta is the direct
   measure); (b) after, read the newest session's `llm.jsonl` decision
   latencies for that window and compare p50 against the immediately
   preceding 10-minute window (no-probe baseline) — the GPU should show no
   contention effect at all; flag anything >5%.
5. **Findings record.** Append a dated "CPU-offload probe" section to
   `ollama_config.md`: both variants' tok/s, per-call latency at
   concurrency 1/2, refreshes/minute, tick-rate result, decision-latency
   delta, and a one-paragraph verdict against the two questions above. If
   the verdict is GO, note the recommended config (num_thread cap or not)
   for the retry. Update `TASKS_PENDING.md` item 4's closing line from
   "needs a second GPU or smaller fast model" to include the probe result
   (GO: CPU-offload retry is the third option / NO-GO: confirmed, record
   the numbers).
6. **Cleanup.** `ollama rm probe-fast-cpu probe-fast-cpu8`; delete the
   scratch Modelfiles; confirm `sim-smart`/`sim-fast` still resident and
   the sim server untouched (single instance, `/state` 200). No sim source
   files, specs, or `ollama/` files were changed — verify with
   `git status` (only `ollama_config.md` + `TASKS_PENDING.md` modified) —
   then commit those two as "ollama: CPU-offload probe findings".

## GO / NO-GO bar (for the verdict paragraph)

- **GO** if, at concurrency 2 on either variant: refreshes/minute ≥ 8
  (enough to hold an 8-agent dirty-driven roster under a 120 s median with
  headroom), AND tick rate held ≥ 29/s, AND GPU decision p50 delta ≤ 5%.
- **NO-GO** otherwise; record the numbers so the "second GPU or smaller
  model" verdict stands on evidence about this box, not assumption.
- The probe does NOT flip any flags, edit `ollama/Modelfile.fast`, or
  start a Phase B soak. A GO verdict feeds a separate retry plan (attempt
  3) with its own gates — one step at a time.

## Constraints for the implementer

- Do not restart, pause, or POST control routes to the sim server; step 4
  reads `/state` and log files only.
- Do not touch `ollama/Modelfile.*`, `scripts/ollama_setup.py`, or any
  `simulation/` source file.
- Ollama env vars (`OLLAMA_NUM_PARALLEL` etc.) stay untouched — the
  per-model Modelfile parameters are the whole point.
- Both deterministic smokes must still pass at the end (they should be
  unaffected; run them as the standard closing check).
- Timebox: if `probe-fast-cpu` creation or the first replay shows something
  structurally broken (e.g. Ollama 0.32.3 rejects `num_gpu 0`, or CPU
  inference evicts the GPU models), stop and report the blocker instead of
  improvising around it.
