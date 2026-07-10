# LM Studio config (simulation)

Target load for this project. If `lms ps` shows anything else after a restart or model reload, re-apply the commands below.

## Required settings

| Setting | Value | Why |
|---|---|---|
| Model / identifier | `qwen/qwen3.5-9b` | Must match `MODEL_SMART` / `MODEL_FAST` in `simulation/server.py` |
| Context length | **13000** | Invention + normal prompts are ~3,100 tokens; need headroom |
| Parallel slots | **2** | Matches `MAX_CONCURRENT_LLM` in `simulation/sim_engine.py` |
| Per-slot budget | ~6500 tokens (`13000 ÷ 2`) | Must stay ≥ ~3400 or you get `"Context size has been exceeded"` under concurrent load |
| API port | **1234** | `http://localhost:1234` (OpenAI-compatible) |

### Wrong config (do not use)

| Setting | Bad value | Effect |
|---|---|---|
| Context | 8192 | With parallel 4 → ~2048 tokens/slot |
| Parallel | 4 | Invention turns overflow; decisions fall back to `rest` / slim retry |

## Restore after reset

PowerShell (from any directory):

```powershell
lms unload --all
lms load qwen/qwen3.5-9b --context-length 13000 --parallel 2 --identifier "qwen/qwen3.5-9b" -y
lms ps
```

Expected `lms ps` line:

```
IDENTIFIER         MODEL              STATUS    SIZE       CONTEXT    PARALLEL
qwen/qwen3.5-9b    qwen/qwen3.5-9b    IDLE      ~6.5 GB    13000      2
```

## Quick checks

```powershell
# Server up?
lms server status
# → The server is running on port 1234.

# Identifier visible to the sim?
# GET http://localhost:1234/v1/models should list "qwen/qwen3.5-9b"
```

If the loaded identifier does not match `MODEL_SMART`/`MODEL_FAST`, `run_agent_decision` prints a notice, disables routing for the session, and retries with `"local-model"` (works for a single loaded model, but prefer matching ids).

## Related sim knobs (not LM Studio)

These live in code; they assume the LM Studio settings above:

- `MAX_CONCURRENT_LLM = 2` — `simulation/sim_engine.py`
- `INVENTION_MAX_TOKENS = 1024`, `INVENTION_TEMPERATURE = 0.6` — `simulation/server.py`
- Context-overflow retry: `run_agent_decision()` slim-prompt retry + `context_overflow` log in `lm_studio.jsonl`

## Notes

- Qwen 3.5 9B is a thinking model: responses often put text in `reasoning_content` with empty `content`. The sim's extractor already handles that.
- LM Studio does not always persist context/parallel across unload/reload or app restart — re-run the restore commands if `lms ps` drifts.
- Rule of thumb: `context length ÷ parallel slots ≥ 3400` (prefer ~6500 as above).
