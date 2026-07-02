---
name: Fix LLM pipeline bugs
overview: "Fix the two bugs found in the log review: reasoning-model chain-of-thought leaking into agent memory (poisoning all future prompts), and the ~3,100-token prompts overflowing LM Studio's per-slot context (48% of calls failing with HTTP 400)."
todos:
  - id: fix-lm-complete
    content: Add answer-extraction + scaffold rejection to lm_complete()/lm_message_text() in server.py
    status: pending
  - id: scrub-memory
    content: Extend MemoryStore.clean() to purge scaffold-poisoned entries; clean longTerm lists
    status: pending
  - id: cap-memory-prompt
    content: Cap compose_memory() output to a fixed char budget
    status: pending
  - id: context-overflow-retry
    content: Detect 'Context size has been exceeded' in run_agent_decision() and retry once with slimmed prompt; log as context_overflow
    status: pending
  - id: update-docs
    content: Update CLAUDE.md LM Studio sizing note (~1500 to ~3400 tokens per slot)
    status: pending
  - id: verify-logs
    content: Run a session and verify lm_studio.jsonl error rate, reflected memories, and prompt sizes
    status: pending
isProject: false
---

# Fix Memory Poisoning and Context Overflow

## Background (from log review)

Session `2026-07-01T12-19-09`: 467 of 975 LLM calls failed with HTTP 400 `"Context size has been exceeded"` (prompt ~3,100 tokens vs the ~1,500 the LM Studio slot budget assumes). Separately, ~130 `reflected:` activity events stored the model's raw scaffold text (`Thinking Process: 1. **Analyze the Request:** ...`) as agent memory, which then re-entered every subsequent prompt via `compose_memory()` — both wasting tokens (aggravating the overflow) and feeding garbage context.

The two bugs are coupled: the poisoned memories are the single biggest source of prompt bloat, so Bug 1's fix directly reduces Bug 2's pressure.

## Bug 1: Chain-of-thought leaking into memory

**Root cause.** `lm_message_text()` ([simulation/server.py](simulation/server.py) ~line 1510) falls back to raw `reasoning_content` when `content` is empty. For the decision path this is fine (the JSON gets extracted). But `lm_complete()` (~line 1520) returns that raw text directly to plain-text consumers — the memory summarizer in `_run_memory_maintenance()` ([simulation/sim_engine.py](simulation/sim_engine.py) ~line 1891), plus the reflection/autobiography/persona callers (server.py ~lines 1333–1436). The qwen3.5 model routes its entire output (scaffold + answer) into `reasoning_content`, so the scaffold gets stored verbatim.

**Fix — three layers:**

1. **Extract the answer in `lm_complete()`**: when the text came from `reasoning_content`, take the final non-empty line/segment (the answer follows the scaffold), stripping quotes. Keep this as a helper (e.g. `extract_plain_answer(text)`) next to `lm_message_text`.
2. **Validate before storing**: in `lm_complete()` (or in the summarizer), reject output containing scaffold markers — `Thinking Process`, `**Analyze`, leading `1.`/`*` bullets, or multi-line output where one sentence was requested. Return `None` on rejection so every caller already degrades gracefully (they all handle `None`).
3. **Scrub existing poison**: extend `MemoryStore.clean()` (server.py ~line 251) to drop entries whose text matches the scaffold markers, so `memory.json` and `agent["memory"]["longTerm"]` recover on the next clean cycle. Also filter `longTerm` lists on state load if the persistence path restores them.

## Bug 2: Context overflow (HTTP 400, 48% of calls lost)

Three parts, in order of impact:

1. **Cap prompt inputs in `compose_memory()`** (server.py ~line 642): the memory line is unbounded today. Cap the merged memory string to a total budget (~600 chars), truncating oldest-first and the `(recalled: ...)` suffix. Bug 1's fix removes the worst offenders; this cap guards against any future bloat.
2. **Detect and retry context overflow in `run_agent_decision()`** (server.py ~line 1742): the current code lumps the 400 `{"error": "Context size has been exceeded."}` body into `bad_response_fallback`, losing the turn. Instead, detect that error string specifically and retry once with a slimmed payload — drop the memory line, recent conversations, and the three worked EXAMPLE blocks from `SYSTEM_PROMPT` (keep rules + JSON schema; the `json_schema` response_format still shapes output). Log it as a distinct error kind (e.g. `context_overflow`) in `lm_studio.jsonl` so it's measurable.
3. **Update the sizing docs**: CLAUDE.md's guidance says ~1,500 prompt tokens per slot; measured reality is ~3,100. Update the LM Studio note to `context length >= 3400 x parallel slots` (or lower `MAX_CONCURRENT_LLM`), so the operator-side config matches.

## Verification

No test suite exists; verify by running the server against LM Studio and checking a fresh session's logs:

- `lm_studio.jsonl`: `error` field should be null on nearly all calls; zero (or isolated, retried) `context_overflow`; `usage.prompt_tokens` visibly lower.
- `activity.jsonl`: `reflected:` events contain a single clean first-person sentence, never `Thinking Process`.
- `memory.json`: no scaffold text after a clean cycle.
- Prompts in `lm_studio.jsonl` requests: `Recent memory:` line is short and human-readable.