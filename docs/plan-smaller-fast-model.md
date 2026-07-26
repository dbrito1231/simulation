# Plan: smaller `sim-fast` → reopen the always-on retry

Status: completed — NO-GO (2026-07-25). Owner: orchestrator + subagents per
[CLAUDE.md](../CLAUDE.md#model-policy), including the usage-limit pause rule.
Branch: `smaller-fast-model` off `main`; one reviewable commit per phase,
never straight to `main`.

Implementation result: Phase 0 screened both smaller fast-model candidates
against the current `sim-fast` baseline; both failed manual qualitative review
because of material grounding and format regressions. The relative numeric
threshold could not be established confidently because the baseline itself
has defects. No runtime configuration, model, specification, or feature-flag
changes were authorized; Phases 1–3 are not authorized by this plan.

## North star

The always-on PIANO whiteboard is implemented, correct, and shelved dark
because no refresh-batch size cleared both the decision-latency and
note-freshness gates on one RTX 3060
([plan-always-on-modules.md](plan-always-on-modules.md); verdict in
[plan-sid-parity-gaps.md](plan-sid-parity-gaps.md)). The CPU-offload escape
hatch was then closed too: `sim-smart` (qwen3.5:9b) + `sim-fast`
(llama3.2:3b) already consume ~all 12 GB VRAM, leaving no room for a third
model even CPU-pinned ([plan-cpu-offload-probe.md](plan-cpu-offload-probe.md),
NO-GO).

A smaller `sim-fast` attacks **both** blockers at once, which is why it's
the highest-leverage single change:

1. **Less GPU compute per module call** → shorter time-slices stolen from
   decisions → the batch-2 latency penalty (+17.7% last time) should shrink,
   possibly enough that batch 2 clears *both* gates with no CPU offload at all.
2. **~2.3 GB VRAM freed** (llama3.2:3b Q4 ~3.3 GB → llama3.2:1b Q4 ~1.0 GB;
   this is a weight-delta saving, not KV-cache, so it's reliable) → enough
   headroom to reopen the CPU-offload path if #1 isn't sufficient.

`sim-smart` stays **qwen3.5:9b, unchanged** — this plan deliberately does
not touch the decision model, so no decision-quality requalification is
needed and the risk stays confined to module-note quality. (Shrinking the
smart model too is a separate, higher-stakes plan — the "Solution 2/3"
fallback below, out of scope here.)

## Model assignments

| Work | Agent | Model |
| --- | --- | --- |
| Phase 0 quality screen (judge module outputs) | `general-purpose` | Sonnet 5 |
| Phase 1 config swap + VRAM measurement | `implementer` | Sonnet 5 |
| Phase 2/3 soaks + analysis | `general-purpose` | Sonnet 5 |
| Post-review doc/spec passes | `implementer` | Haiku 4.5 |

## Candidate fast models

- **Primary: `llama3.2:1b`** — same family/template as the current
  llama3.2:3b, whose template already screened clean (Ollama Phase 0). Lowest
  requalification risk; the only open question is whether 1B holds module
  coherence.
- **Alternate: `smollm2:1.7b`** — purpose-built for short low-stakes
  completions, ~1 GB, different family (new template risk, screen from
  scratch). Only reach for it if `llama3.2:1b` fails the Phase 0 screen.

---

## Phase 0 — quality screen (the gate that can kill this early)

No config change yet. `ollama pull llama3.2:1b`. Build 12 module-style
prompts using server.py's real `MODULE_PROMPTS` system text (grep for it)
plus realistic synthetic context lines (agent/role/hunger/nearby — see the
CPU-probe plan's step-2 note; module calls are NOT in `llm.jsonl`, so
construct them). Run each against **both** `llama3.2:1b` and the current
`sim-fast` (llama3.2:3b) at the module sampling settings (temp 0.5, top_p
0.8, top_k 20, min_p 0, num_predict 60).

Judge each output on: (a) coherent single-sentence report, (b) grounded in
the given context — no invented facts, (c) clean — no template/stop-token
leakage. The bar is **relative, not absolute**: `llama3.2:1b` passes if it
is within ~1 of the 3B's pass count on the same 12 prompts (i.e. not
meaningfully worse than what already ships). Record the side-by-side in
`ollama_config.md`.

- Pass → Phase 1. Fail → screen `smollm2:1.7b` the same way; if it also
  fails, STOP: the module tier can't shrink without quality loss, the
  always-on retry stays hardware-blocked, and the honest next lever is a
  second GPU (record and stop, no forcing).

## Phase 1 — swap `sim-fast`, measure headroom, confirm decisions unaffected

Files: `ollama/Modelfile.fast`, `scripts/ollama_setup.py`, `ollama_config.md`;
spec `specs/03-cognition.md`.

1. `ollama/Modelfile.fast`: `FROM llama3.2:1b` (keep num_ctx 4096 + the
   sampling params). Update the provenance comment block. `scripts/ollama_setup.py`:
   `ollama pull llama3.2:1b` if absent; the `ollama create sim-fast` step is
   unchanged (rebuilds from the new Modelfile). `MODEL_FAST = "sim-fast"` in
   server.py stays as-is (the ollama tag is what changes, not the constant).
2. Re-run `uv run python scripts/ollama_setup.py` (restarts Ollama — the sim
   falls back to `rest` for ~30 s, acceptable). Verify `/api/ps`: both
   models resident; record `nvidia-smi` free-VRAM delta vs. the ~1.5 GB the
   probe found (expect ~+2.3 GB → ~3.8 GB free).
3. **Confirm decisions are untouched**: `sim-smart` is unchanged, but the
   restart is a good moment to spot-check — 5-min watch of the newest
   session's `llm.jsonl`, decision p50 in line with the ~6–7.5 s baseline,
   0 errors.
4. Spec: `specs/03-cognition.md` model table + `ollama_config.md` models row
   → sim-fast is now llama3.2:1b, with the freed-VRAM number recorded.
5. Both smokes pass. Commit.

This phase is shippable on its own even if the always-on retry never
happens — a lighter module tier is a strict resource win with (per Phase 0)
no quality regression.

## Phase 2 — always-on Phase B retry, GPU-resident (the primary test)

Precondition: `ALWAYS_ON_MODULES` flips True for the treatment soak only,
reverting to False after per the existing one-flag-revert discipline. Reuse
the exact Phase B soak protocol and gates from
[plan-always-on-modules.md](plan-always-on-modules.md) (two 45-min soaks,
same world save, `soak_monitor.py`, the same six gate criteria) — this plan
changes only the fast model underneath, not the method.

Key difference from the failed attempts: **start at `MODULE_PULSE_MAX_BATCH = 2`**
(the freshness-passing config), because the open question is now whether the
1B's reduced compute has bought back the latency headroom that batch 2 lacked.
Expected signature if the lighter model works: batch 2 clears the +15%
latency gate *and* keeps median note age under 120 s — the dilemma dissolved
by cheaper refreshes rather than by trading one gate for the other.

- All gates pass → leave the flag on, record, done: the whiteboard ships.
- Latency still misses at batch 2 → this is the CPU-offload trigger, Phase 3
  (we now have the VRAM for it). Do NOT re-run the batch-1 mistuning — the
  smaller model changes the compute math, not the throughput-vs-contention
  topology, so batch 1 would starve freshness exactly as before.

## Phase 3 — CPU-offload retry (only if Phase 2 misses latency)

Now viable because Phase 1 freed VRAM for a third model's residual buffers.
Re-run [plan-cpu-offload-probe.md](plan-cpu-offload-probe.md) end to end
(it stopped at the residency blocker last time): a CPU-pinned `sim-fast`
(`num_gpu 0`) alongside the GPU-resident `sim-smart`, `OLLAMA_MAX_LOADED_MODELS`
already at 3 and the env-propagation bug already fixed (commit `bdef964`).
Confirm the three-model residency the probe couldn't reach, then run its
throughput + tick-integrity measurements and its GO bar. If GO, run the
always-on Phase B soak with modules on the CPU pool — decisions get the whole
GPU (latency gate trivially passed), CPU throughput carries freshness.

## Stop conditions

- Phase 0 double-fail (both candidates) → module tier can't shrink; second
  GPU is the only remaining lever; record and stop.
- Phase 2 pass → done, Phase 3 unneeded.
- Phase 3 NO-GO (residency or throughput) → the always-on retry is exhausted
  on this hardware; record the full evidence trail and rest it until a
  hardware change. The next escalation (shrinking `sim-smart` too — the
  Solution 2/3 pairs) is explicitly a *different* plan with decision-model
  requalification, not a silent continuation of this one.

No stop condition degrades the sim: Phase 1's fast-model swap is a net win
regardless, and `ALWAYS_ON_MODULES` stays dark unless a soak earns the flip.

## Spec obligations (same change as the code)

| Phase | Spec/doc | Update |
| --- | --- | --- |
| 1 | `specs/03-cognition.md`, `ollama_config.md` | sim-fast = llama3.2:1b, freed-VRAM figure |
| 2 | `ollama_config.md` | Phase B retry (batch 2, lighter model) results |
| 3 | `ollama_config.md`, `TASKS_PENDING.md` item 4 | CPU-offload retry result; final always-on verdict |
