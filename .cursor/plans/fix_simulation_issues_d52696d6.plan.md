---
name: Fix Simulation Issues
overview: "Fix 4 confirmed issues: LLM bad_response causing agents to only rest, missing cumulative logs, missing conversation.jsonl, and browser tab backgrounding pausing the simulation. Sprite review is included for user feedback."
todos:
  - id: fix-llm-bad-response
    content: "server.py: Return role_fallback_action instead of rest on bad_response (Part A of Fix 1)"
    status: pending
  - id: fix-llm-model
    content: Switch LM Studio to non-reasoning model OR disable thinking mode for qwen3.5-9b (Part B of Fix 1)
    status: pending
  - id: fix-cumulative-logs
    content: "server.py: Add global aggregate log files alongside per-session files (Fix 2)"
    status: pending
  - id: fix-conversation-file
    content: "server.py: Touch all 3 JSONL files at session init so they always exist (Fix 3)"
    status: pending
  - id: fix-tab-warning
    content: "index.html: Add visibilitychange listener with on-screen warning (Fix 4)"
    status: pending
  - id: validate-all
    content: Run through full validation checklist before marking complete
    status: pending
isProject: false
---

# Fix Simulation Issues

## Confirmed Root Causes (from logs)

- **Agents only rest**: `qwen3.5-9b` is a reasoning model. It uses ~299 of 300 tokens for internal thinking (`reasoning_content`) and outputs empty `content`. Server reads only `content`, gets empty string, returns `{"action": "rest"}`. Confirmed: 50/51 LLM calls = `bad_response`.
- **Sparse data / too few events**: Browser tab was backgrounded for hours (frame 757 at 4:14 UTC → frame 1189 at 10:47 UTC = 6+ hour gap). `requestAnimationFrame` throttles in hidden tabs, pausing the entire simulation.
- **No cumulative log**: `SessionLogger` creates a new folder per server restart. Old session data is not aggregated anywhere.
- **Missing `conversation.jsonl`**: File is created lazily on first write. Because LLM calls all failed, no `talk_to_nearby` actions ever fired. File never created.

---

## Fix 1 — Agents Do Actual Work (Critical)

**File**: [`simulation/server.py`](simulation/server.py)

**Two-part fix:**

**Part A — Use `role_fallback_action` instead of `rest` on `bad_response`**

Currently lines 594–601 return `{"action": "rest"}` on any parse failure. Change those paths to call `role_fallback_action(data.get("role"), data)` so agents gather/build/move even when the LLM fails.

**Part B — Switch LLM model in LM Studio** (since you have multiple models available)

Load a **non-reasoning model** (e.g. `llama-3.2-3b-instruct`, `phi-3.5-mini`, `mistral-7b-instruct`, `gemma-2-2b-it` — any model that does NOT use `<think>` tags or `reasoning_content`). The simulation prompt is already well-structured; a 3B–7B instruction-tuned model works well here.

If you want to keep `qwen3.5-9b`, also add `"thinking": {"type": "disabled", "budget_tokens": 0}` to the payload in `server.py` line 540 (Qwen3 supports disabling thinking mode via this parameter).

**Validation**: After fix, open `lm_studio.jsonl` — `"error"` field should be `null` and `"decision"` should contain a non-null action. `activity.jsonl` should show varied actions (collect_resource, move_to_forest, start_project, etc.), not just "rested".

---

## Fix 2 — Cumulative Logs Across Sessions

**File**: [`simulation/server.py`](simulation/server.py)

Modify `SessionLogger._append()` to write to **two places**: the per-session file (existing) AND a global aggregate file in `simulation/logs/`.

Global files:
- `simulation/logs/activity.jsonl`
- `simulation/logs/conversation.jsonl`
- `simulation/logs/lm_studio.jsonl`

Each record already has `"session_id"` embedded, so sessions remain distinguishable in the global file.

**Implementation** (add one line to `_append`):
```python
def _append(self, path, record):
    record = {"ts": ..., "session_id": ..., **record}
    # write to session-specific file (existing)
    with open(path, "a") as fh: fh.write(...)
    # ALSO write to global aggregate file
    global_path = os.path.join(os.path.dirname(self.dir), os.path.basename(path))
    with open(global_path, "a") as fh: fh.write(...)
```

**Validation**: Restart the server twice. Both session folders should exist AND `simulation/logs/activity.jsonl` should contain records from both sessions with different `session_id` values.

---

## Fix 3 — Ensure `conversation.jsonl` Exists

**File**: [`simulation/server.py`](simulation/server.py)

In `SessionLogger.__init__`, touch (create empty) all three JSONL files immediately at startup so the files always exist even before any events are logged:

```python
for path in [self.activity_path, self.conversation_path, self.lm_studio_path]:
    open(path, "a").close()
```

**Note**: Conversations will only appear in this file once Fix 1 is working and agents are successfully calling `talk_to_nearby`. After Fix 1, conversations should appear within the first few minutes of running.

**Validation**: Immediately after server start, all three files must exist in the session folder (`ls simulation/logs/<latest-session>/`).

---

## Fix 4 — Browser Tab Backgrounding (Sparse Data)

**File**: [`simulation/index.html`](simulation/index.html)

Add a `document.visibilitychange` listener that shows a visible on-screen warning when the tab is hidden. This prevents silent pausing of the simulation.

```javascript
document.addEventListener("visibilitychange", () => {
  document.getElementById("tab-warning").style.display =
    document.hidden ? "block" : "none";
});
```

Add a `<div id="tab-warning">` overlay in the HTML with red text: "⚠ Tab hidden — simulation paused. Keep this tab visible."

**Validation**: Switch to another browser tab — warning must appear. Switch back — warning disappears.

---

## Validation Checklist (Run Before Calling Complete)

- [ ] `lm_studio.jsonl` shows `"error": null` and valid `"decision"` objects
- [ ] `activity.jsonl` shows actions other than just "rested" (collect, move, start_project, etc.)
- [ ] After restart, `simulation/logs/activity.jsonl` (global) contains records from multiple sessions
- [ ] `conversation.jsonl` exists immediately after server start (even if empty)
- [ ] After 5+ minutes of running with tab focused, `conversation.jsonl` contains at least one record
- [ ] Tab hidden → warning overlay appears; tab focused → overlay gone

---

## Sprite Review

The sprite sheet above shows the current state of all 12 agents. Key facts about the current implementation in [`simulation/sprites.js`](simulation/sprites.js):

- Each agent is a **16×16 pixel grid rendered at 2× scale** (32×32 on screen)
- **Only 2 animation frames**: `stand` (legs together) and `walk` (legs spread apart, toggled every 12 ticks)
- Agents are **horizontally flipped** when moving left
- A small **accessory** (hat detail/tool) is drawn on top of the head for each agent
- All 12 agents have **identical body shapes** — only the color palette differs
- No action-specific animations (no collect animation, no build animation, no idle bob)

**Current accessory summary per agent** (small icons drawn atop the head):
- Aria: flower/feather (green/gold)
- Marco: brim hat (gold/brown)
- Zara: tall pointed hat (purple/grey)
- Rex: shield emblem (grey/red)
- Luna: round hat (blue/brown)
- Finn: sailor cap (navy/cyan)
- Mia: cross symbol (white/pink)
- Colt: hard hat (gold/brown)
- Ivy: leaf crown (dark green)
- Dex: visor (dark grey/blue-grey)
- Nova: crest (orange/coral)
- Sage: elder crown (brown/yellow)

**Review the sprite images above and list any changes you want** (examples: add a 3rd idle animation frame, make roles visually distinct by tool/outfit, change specific agent colors, enlarge sprites, add gender differentiation). No sprite changes will be implemented until you specify them.
