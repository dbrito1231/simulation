# SPEC 12 — Operations: Logging & Scripts

How the sim is observed and debugged in the absence of a test suite: JSONL
session logs, `/log/*` ingestion, and the `scripts/` toolbox.

**Canonical for:** `SessionLogger`'s file layout and record shapes, the
never-raise logging contract, Ollama's own server log location, and what
each of the `scripts/*.py` tools does and whether it needs Ollama.
**See also:** [04-http-api.md](04-http-api.md) for `/log/event` and
`/log/benchmark` route shapes (not repeated here); [03-cognition.md](03-cognition.md)
for what's inside an LLM request/response payload; [CLAUDE.md](../CLAUDE.md)
for the no-test-suite verification workflow this spec elaborates.

## SessionLogger

`SessionLogger` (server.py:248) is constructed once at import time —
`session_logger = SessionLogger(...)` (server.py:321) — so every server
process (`uv run python simulation/server.py`) gets exactly one session
folder for its lifetime.

- **Folder naming**: `simulation/logs/<session_id>/` where `session_id =
  datetime.now().strftime("%Y-%m-%dT%H-%M-%S")` (server.py:252-253), e.g.
  `simulation/logs/2026-07-15T09-30-00/`. The whole `logs/` tree is
  gitignored.
- **Four JSONL streams**, each created empty on startup (server.py:255-264):
  | File | Written by | Record `type` |
  |---|---|---|
  | `activity.jsonl` | `log_activity(message, frame_tick)` (server.py:286-289) | `"activity"` |
  | `conversation.jsonl` | `log_conversation(sender, recipient, message, frame_tick, kind, outcome)` (server.py:291-303) | `"conversation"` |
  | `llm.jsonl` | `log_lm_exchange(record)` (server.py:305-307) | `"llm"` (sessions predating the Ollama migration, `docs/plan-ollama-migration.md` Phase 5, wrote `lm_studio.jsonl` with `type: "lm_studio"`) |
  | `benchmarks.jsonl` | `log_benchmark(metric, value, frame_tick, detail)` (server.py:309-318) | `"benchmark"` |
- Every record passes through `_append()` (server.py:272-284), which stamps
  `ts` (UTC ISO-8601) and `session_id` onto whatever fields the caller
  supplied, then appends one JSON line. The first `conversation.jsonl` line
  is always a synthetic `kind: "session_start"` entry (server.py:265-270).
- **Per-session `memory.json`**: the in-process vector `MemoryStore`
  persists to `session_logger.dir/memory.json` (server.py:620) — debounced
  (`MEMORY_PERSIST_EVERY = 12` stores, server.py:333) plus always-flushed on
  `clean()`/`clear()` (server.py:535-536, 581-588) via atomic
  write-tmp-then-`os.replace` (server.py:611-614). Shape: `{session_id,
  size, entries: [{id, agent, text, salience, kind, tier, frame_tick, ts}]}`
  — the 128-float `vec` is stripped before writing (recomputable, pure disk
  bloat, server.py:600-609). It's a per-session **inspection artifact
  only**, never read back by the running server (state.db carries the
  authoritative memory export across restarts).
- **Record shapes** beyond the common `ts`/`session_id` envelope:
  - `activity`: `{type, message, frame_tick}`.
  - `conversation`: `{type, kind, from, to, message, frame_tick, outcome?}`.
  - `llm`: built per decision call by closure `log_lm(...)`
    (server.py:3010-3035): `{agent_name, frame_tick, latency_ms,
    invention_only, sprite_design_only, high_stakes_reason,
    high_stakes_active, high_stakes_capped, prompt_chars, system_chars,
    nudges_total, nudges_dropped, request, response, http_status, decision,
    error}` — `request` is the exact payload sent (post any slim-retry
    swap), `decision` is the normalized/applied decision or fallback.
  - `benchmark`: `{type, metric, value, frame_tick, detail?}`.
- **Never-raise contract**: `_append()` wraps its write in
  `try/except OSError: pass` (server.py:279-284, "Logging must never break
  the simulation"); `_persist()` for `memory.json` has the identical guard
  (server.py:615-617). `/log/event` and `/log/benchmark` wrap their entire
  body in `try/except Exception: pass` too (server.py:2194-2233) — a
  malformed browser-origin log POST can never 500 or disturb the sim.
- **`/log/event`**/**`/log/benchmark`** (server.py:2194-2233) let the
  browser forward client-origin events into the same session streams; full
  request/response shapes are in [04-http-api.md](04-http-api.md).
- **Ollama's own server log** (not written by `SessionLogger`, and not
  under `simulation/logs/`) lives at `%LOCALAPPDATA%\Ollama\server.log` —
  token usage and per-request checkpoints, useful alongside `llm.jsonl`.

## Debugging workflow

There is **no automated test suite or linter** in this repo. Verification is
by observation: run the server (own titled window per
[CLAUDE.md](../CLAUDE.md#commands)), watch the browser render, and read the
JSONL logs for the current session. `llm.jsonl` is the **primary
debugging surface** — every record carries the exact `request` payload, the
raw `response`, and the resulting `decision`, answering "what did the model
return, and which fallback (if any) fired" without reproducing the call.
Cross-check `activity.jsonl`/`conversation.jsonl` for the world-visible
effect and `benchmarks.jsonl` for aggregate metrics (specialization index,
rule adherence, meme adoption, memory-store size — see
[09-systems-society.md](09-systems-society.md)). For full determinism
without an Ollama dependency, use the smoke scripts below instead.

## Scripts (`scripts/`, repo root)

| Script | Needs Ollama? | What it does |
|---|---|---|
| `sid_parity_smoke.py` | No | Deterministic smoke harness for Sid-parity Phases 1–3: specialization-need signals, priority/repeal governance, competing memes, belief-biased votes — drives `sim_engine` directly (imports `sim_engine.py`/`roles.json`, no network). Run: `uv run python scripts/sid_parity_smoke.py`. |
| `path1_smoke.py` | No | Deterministic smoke harness for the Path 1 bundle (industry, tool tiers, terrain, diplomacy, pressure loop) — same direct-import approach as `sid_parity_smoke.py`. Run: `uv run python scripts/path1_smoke.py`. |
| `path1_soak.py` | Mode-dependent | "SA-9 Path 1 soak verifier": live soak orchestration + log audit for the 2h mini-soak from the archived Path 1 plan. Subcommands: `report`/`prompt-check`/`audit LOG_DIR` need no Ollama; `run [--duration S] [--agents N]` is a live soak against a running server (Ollama optional, recommended for one check). |
| `blueprint_smoke.py` | No | Deterministic blueprint validation/recovery checks — imports `server.py`/`sim_engine.py` directly to exercise proposal/approval edge cases (e.g. duplicate-effect detection) with no live LLM call. |
| `llm_replay_bench.py` | Yes | Replay-benchmarks previously-logged decision calls (from a session's `llm.jsonl`, falling back to the pre-rename `lm_studio.jsonl` for old sessions) for repeatable before/after latency numbers. As of the Ollama migration this script still targets the former LM Studio runtime's OpenAI-compat endpoint — porting it to Ollama's native `/api/chat` is `docs/plan-ollama-migration.md` Phase 4 work, not yet done. Modes: `--as-logged` (resend verbatim) and `--patched` (apply the historical Phase-2 transforms — `reasoning_effort="none"` for routine turns, Qwen non-thinking sampling pins, thinking-preserving sampling for invention/sprite turns). Standalone (imports nothing from `server.py`). Usage: `uv run python scripts/llm_replay_bench.py --as-logged [--session PATH] [--n N]`; pause the sim server first (`POST /control/pause`) so its own think traffic doesn't skew latencies. |
| `ollama_setup.py` | Yes (configures Ollama itself) | Canonical CLI loader for the sim's Ollama models: sets `OLLAMA_NUM_PARALLEL`/`OLLAMA_MAX_LOADED_MODELS`/`OLLAMA_FLASH_ATTENTION`/`OLLAMA_KEEP_ALIVE` env vars, restarts the Ollama service, pulls/creates `sim-smart`/`sim-fast` from `ollama/Modelfile.{smart,fast}` (idempotent), warms both, and verifies dual residency via `/api/ps`. Usage: `uv run python scripts/ollama_setup.py` (apply) or `--check` (readback only). Successor to the removed `scripts/lms_load.py`, per `docs/plan-ollama-migration.md` Phase 5. |

`llm_replay_bench.py` and `ollama_setup.py` are the two LLM-runtime-dependent
tools; the other four are pure Python harnesses against `sim_engine`/`server`
module code or existing log files, safe to run with Ollama offline.
