# LLM fallback & validation-rejection evidence

Read-only gather of local `simulation/logs/` evidence for two patterns:

1. **Fallback (no usable AI answer)** — LLM call logged with a non-null `error` (`llm timeout`, `llm offline`, `bad_response`, `compute_error`, `model_not_found`, etc.), so the engine substituted a role fallback without a usable model decision.
2. **Rejected then fell back (bad AI answer)** — model responded (`error: null`, usually `http_status: 200`), but `normalize_decision()` rejected the payload (`*_rejection_note` and/or `(invalid …)` in `reasoning`) and substituted `role_fallback_action()`.

**Choices used for this gather:** evidence at repo root; scan `llm.jsonl` + `lm_studio.jsonl` + related `activity.jsonl` / `conversation.jsonl` hints; Pattern 1 reported explicitly even if empty; every Pattern 2 match included in full; branch+push only (no PR); no code changes.

**Companion:** fallback action catalog + observed defaults → [`llm-fallback-default-actions.md`](llm-fallback-default-actions.md).

## Method

- Scanned all **20** session folders under `simulation/logs/`.
- Primary source: every non-empty `llm.jsonl` line (22 total LLM records across 2 sessions).
- Also checked for `lm_studio.jsonl` (legacy): **none present**.
- Related hints: scanned all `activity.jsonl` / `conversation.jsonl` for fallback/rejection keywords; keyword scan alone found 0 hits (those files use plain English messages). Manually correlated timestamps from Pattern 2 LLM rows into activity/conversation echoes (listed below).
- Role is **not** present on these LLM log lines (`agent_name` only).
- Raw logs themselves are **not** committed (gitignored); this file is the durable evidence extract.
- **Sprite failures** get a dedicated analysis in [Deep dive: bad sprite designs](#deep-dive-bad-sprite-designs) (validator vs prompt contradiction, claimed sizes, rejection-note meanings, council-gate interaction, logging gaps).

## Inventory of sessions scanned

| Session | llm.jsonl bytes | lm_studio.jsonl | activity.jsonl bytes | conversation.jsonl bytes |
|---|---:|---|---:|---:|
| `2026-08-02T01-08-44` | 0 | absent | 438 | 764 |
| `2026-08-02T01-08-43` | 0 | absent | 438 | 764 |
| `2026-08-02T01-08-42` | 0 | absent | 438 | 764 |
| `2026-08-02T01-08-41` | 0 | absent | 1096 | 1600 |
| `2026-08-02T01-08-39` | 0 | absent | 438 | 764 |
| `2026-08-02T01-08-38` | 0 | absent | 437 | 763 |
| `2026-08-02T01-08-14` | 18227 | absent | 14498 | 2000 |
| `2026-08-02T01-07-39` | 1899 | absent | 1671 | 765 |
| `2026-08-02T01-07-02` | 0 | absent | 437 | 763 |
| `2026-08-02T00-23-03` | 0 | absent | 438 | 764 |
| `2026-08-02T00-23-02` | 0 | absent | 437 | 763 |
| `2026-08-01T23-57-03` | 0 | absent | 875 | 1527 |
| `2026-08-01T23-57-01` | 0 | absent | 1095 | 1599 |
| `2026-08-01T23-54-23` | 0 | absent | 877 | 1529 |
| `2026-08-01T23-14-44` | 0 | absent | 439 | 765 |
| `2026-08-01T23-14-43` | 0 | absent | 438 | 764 |
| `2026-08-01T23-14-09` | 0 | absent | 438 | 764 |
| `2026-08-01T23-14-08` | 0 | absent | 438 | 764 |
| `2026-08-01T23-13-32` | 0 | absent | 439 | 765 |
| `2026-08-01T23-13-31` | 0 | absent | 438 | 764 |

- Sessions with non-empty `llm.jsonl`: **2** (`2026-08-02T01-07-39, 2026-08-02T01-08-14`)
- Empty `llm.jsonl` sessions: **18**
- Total LLM records parsed: **22**
- Error value distribution across all LLM records: `{'None': 22}`
- Rejection-note key counts across all LLM records: `{'sprite_rejection_note': 6, 'council_rejection_note': 3}`

## Pattern 1 — Fallback (no usable AI answer)

**Count: 0**

No examples found after a thorough scan of all 20 sessions / 22 LLM records. Every logged LLM call had `error: null` (and, where present, `http_status: 200`). There were no `llm timeout`, `llm offline`, `bad_response`, `compute_error`, or `model_not_found` entries in the current local logs.

Sessions confirmed empty for Pattern 1 criteria (all scanned):

- `2026-08-02T01-08-44`
- `2026-08-02T01-08-43`
- `2026-08-02T01-08-42`
- `2026-08-02T01-08-41`
- `2026-08-02T01-08-39`
- `2026-08-02T01-08-38`
- `2026-08-02T01-08-14`
- `2026-08-02T01-07-39`
- `2026-08-02T01-07-02`
- `2026-08-02T00-23-03`
- `2026-08-02T00-23-02`
- `2026-08-01T23-57-03`
- `2026-08-01T23-57-01`
- `2026-08-01T23-54-23`
- `2026-08-01T23-14-44`
- `2026-08-01T23-14-43`
- `2026-08-01T23-14-09`
- `2026-08-01T23-14-08`
- `2026-08-01T23-13-32`
- `2026-08-01T23-13-31`

## Pattern 2 — Rejected then fell back (bad AI answer)

**Count: 9** (all matches below, full decision + full response_preview)

### Pattern 2 example 1: Ivy @ 2026-08-02T05:07:57.072261+00:00

- **Session / line:** `2026-08-02T01-07-39` / `llm.jsonl` line 1
- **Agent:** Ivy (role not logged)
- **frame_tick:** 14668680
- **http_status / latency_ms:** 200 / 16801
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `repeated_rejections`
- **Rejection notes:** `{"sprite_rejection_note": "sprite must be an object with palette and grid"}`

**response_preview (full as logged):**

```text
{
  "action": "submit_structure_sprite",
  "reasoning": "The previous sprite was rejected for being too small (14x14). The new design must strictly exceed these dimensions to qualify as 'tier 5' and represent a grown-up farm plot. I will cr…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "collect_resource",
  "target": "iron_ore",
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Gathering resources for the village. (invalid sprite: sprite must be an object with palette and grid)",
  "sprite_rejection_note": "sprite must be an object with palette and grid"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:07:57.072261+00:00",
  "session_id": "2026-08-02T01-07-39",
  "type": "llm",
  "agent_name": "Ivy",
  "frame_tick": 14668680,
  "latency_ms": 16801,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": "repeated_rejections",
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 946,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "collect_resource",
    "target": "iron_ore",
    "message": null,
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Gathering resources for the village. (invalid sprite: sprite must be an object with palette and grid)",
    "sprite_rejection_note": "sprite must be an object with palette and grid"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"submit_structure_sprite\",\n  \"reasoning\": \"The previous sprite was rejected for being too small (14x14). The new design must strictly exceed these dimensions to qualify as 'tier 5' and represent a grown-up farm plot. I will cr…"
}
```

</details>

### Pattern 2 example 2: Rex @ 2026-08-02T05:08:34.618017+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 4
- **Agent:** Rex (role not logged)
- **frame_tick:** 14669129
- **http_status / latency_ms:** 200 / 5273
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `None`
- **Rejection notes:** `{"sprite_rejection_note": "sprite must be an object with palette and grid"}`

**response_preview (full as logged):**

```text
{"action":"submit_structure_sprite","reasoning":"The previous sprite was rejected for being too small (max 14x14). The user requested a 'strictly bigger' grid, meaning >14 rows and >14 columns. I will design a 16x16 pixel-art farm plot that…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "assign_task",
  "target": "Kane",
  "message": "gather or contribute iron_ore to the active project",
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
  "sprite_rejection_note": "sprite must be an object with palette and grid"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:08:34.618017+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Rex",
  "frame_tick": 14669129,
  "latency_ms": 5273,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": null,
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 858,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "assign_task",
    "target": "Kane",
    "message": "gather or contribute iron_ore to the active project",
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
    "sprite_rejection_note": "sprite must be an object with palette and grid"
  },
  "error": null,
  "response_preview": "{\"action\":\"submit_structure_sprite\",\"reasoning\":\"The previous sprite was rejected for being too small (max 14x14). The user requested a 'strictly bigger' grid, meaning >14 rows and >14 columns. I will design a 16x16 pixel-art farm plot that…"
}
```

</details>

### Pattern 2 example 3: Rex @ 2026-08-02T05:08:48.762601+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 8
- **Agent:** Rex (role not logged)
- **frame_tick:** 14669384
- **http_status / latency_ms:** 200 / 7870
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `None`
- **Rejection notes:** `{"sprite_rejection_note": "sprite must be an object with palette and grid"}`

**response_preview (full as logged):**

```text
{
  "action": "submit_structure_sprite",
  "reasoning": "The previous sprite was rejected because it did not exceed the minimum dimensions (14x14). This new design for the upgraded Farm Plot strictly exceeds those limits with a 16-row by 20…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "assign_task",
  "target": "Dex",
  "message": "gather or contribute iron_ore to the active project",
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
  "sprite_rejection_note": "sprite must be an object with palette and grid"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:08:48.762601+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Rex",
  "frame_tick": 14669384,
  "latency_ms": 7870,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": null,
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 875,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "assign_task",
    "target": "Dex",
    "message": "gather or contribute iron_ore to the active project",
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
    "sprite_rejection_note": "sprite must be an object with palette and grid"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"submit_structure_sprite\",\n  \"reasoning\": \"The previous sprite was rejected because it did not exceed the minimum dimensions (14x14). This new design for the upgraded Farm Plot strictly exceeds those limits with a 16-row by 20…"
}
```

</details>

### Pattern 2 example 4: Ivy @ 2026-08-02T05:08:53.597446+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 9
- **Agent:** Ivy (role not logged)
- **frame_tick:** 14669251
- **http_status / latency_ms:** 200 / 11041
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `repeated_rejections`
- **Rejection notes:** `{"sprite_rejection_note": "sprite grid must be 4-14 rows"}`

**response_preview (full as logged):**

```text
{
"action": "submit_structure_sprite",
"reasoning": "Ivy honors the earth's bounty by expanding the Farm Plot into a grand, flourishing estate. The new design strictly exceeds 14x14 dimensions (20x20), featuring a lush green canopy of crops…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "move_to_district",
  "target": "forest",
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Heading to the forest to gather wood. (invalid sprite: sprite grid must be 4-14 rows)",
  "sprite_rejection_note": "sprite grid must be 4-14 rows"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:08:53.597446+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Ivy",
  "frame_tick": 14669251,
  "latency_ms": 11041,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": "repeated_rejections",
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 895,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "move_to_district",
    "target": "forest",
    "message": null,
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Heading to the forest to gather wood. (invalid sprite: sprite grid must be 4-14 rows)",
    "sprite_rejection_note": "sprite grid must be 4-14 rows"
  },
  "error": null,
  "response_preview": "{\n\"action\": \"submit_structure_sprite\",\n\"reasoning\": \"Ivy honors the earth's bounty by expanding the Farm Plot into a grand, flourishing estate. The new design strictly exceeds 14x14 dimensions (20x20), featuring a lush green canopy of crops…"
}
```

</details>

### Pattern 2 example 5: Rex @ 2026-08-02T05:09:03.323847+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 12
- **Agent:** Rex (role not logged)
- **frame_tick:** 14669744
- **http_status / latency_ms:** 200 / 6497
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `None`
- **Rejection notes:** `{"sprite_rejection_note": "sprite must be an object with palette and grid"}`

**response_preview (full as logged):**

```text
{
  "action": "submit_structure_sprite",
  "reasoning": "The previous sprite was rejected for being too small (14x14). The requirement is strictly more than 14 rows and columns. I will design a larger Farm Plot with dimensions of 16x18 to s…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "assign_task",
  "target": "Zara",
  "message": "gather or contribute iron_ore to the active project",
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
  "sprite_rejection_note": "sprite must be an object with palette and grid"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:09:03.323847+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Rex",
  "frame_tick": 14669744,
  "latency_ms": 6497,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": null,
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 906,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "assign_task",
    "target": "Zara",
    "message": "gather or contribute iron_ore to the active project",
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Assigning work to an idle villager. (invalid sprite: sprite must be an object with palette and grid)",
    "sprite_rejection_note": "sprite must be an object with palette and grid"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"submit_structure_sprite\",\n  \"reasoning\": \"The previous sprite was rejected for being too small (14x14). The requirement is strictly more than 14 rows and columns. I will design a larger Farm Plot with dimensions of 16x18 to s…"
}
```

</details>

### Pattern 2 example 6: Ivy @ 2026-08-02T05:09:13.097205+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 14
- **Agent:** Ivy (role not logged)
- **frame_tick:** 14669896
- **http_status / latency_ms:** 200 / 9611
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `repeated_rejections`
- **Rejection notes:** `{"sprite_rejection_note": "sprite grid must be 4-14 rows"}`

**response_preview (full as logged):**

```text
{
  "action": "submit_structure_sprite",
  "reasoning": "The previous sprite was rejected because it did not exceed the minimum dimensions (14x14). This new design for the Farm Plot is strictly larger, measuring 16 rows by 20 columns. It ma…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "collect_resource",
  "target": "iron_ore",
  "message": null,
  "new_role": null,
  "relationship_update": null,
  "reasoning": "Gathering resources for the village. (invalid sprite: sprite grid must be 4-14 rows)",
  "sprite_rejection_note": "sprite grid must be 4-14 rows"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:09:13.097205+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Ivy",
  "frame_tick": 14669896,
  "latency_ms": 9611,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": "repeated_rejections",
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 909,
  "system_chars": 844,
  "nudges_total": 1,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "collect_resource",
    "target": "iron_ore",
    "message": null,
    "new_role": null,
    "relationship_update": null,
    "reasoning": "Gathering resources for the village. (invalid sprite: sprite grid must be 4-14 rows)",
    "sprite_rejection_note": "sprite grid must be 4-14 rows"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"submit_structure_sprite\",\n  \"reasoning\": \"The previous sprite was rejected because it did not exceed the minimum dimensions (14x14). This new design for the Farm Plot is strictly larger, measuring 16 rows by 20 columns. It ma…"
}
```

</details>

### Pattern 2 example 7: Rex @ 2026-08-02T05:09:35.124503+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 17
- **Agent:** Rex (role not logged)
- **frame_tick:** 14670246
- **http_status / latency_ms:** 200 / 5938
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `True` / `None`
- **Rejection notes:** `{"council_rejection_note": "not a seated active council turn"}`

**response_preview (full as logged):**

```text
{
  "action": "submit_structure_sprite",
  "reasoning": "The Smelter in the village_core has stalled due to low rope stores and is currently under construction/upgrade. As Elder Rex, I propose a larger, more robust sprite for this structure…
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "council_speak",
  "message": "We should protect essentials while making steady progress.",
  "feeling": "hopeful",
  "topic": "world_status",
  "reasoning": "Offering a practical council opinion. (invalid council session/action)",
  "council_rejection_note": "not a seated active council turn"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:09:35.124503+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Rex",
  "frame_tick": 14670246,
  "latency_ms": 5938,
  "invention_only": false,
  "sprite_design_only": true,
  "high_stakes_reason": null,
  "high_stakes_active": true,
  "high_stakes_capped": false,
  "prompt_chars": 1412,
  "system_chars": 844,
  "nudges_total": 0,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "council_speak",
    "message": "We should protect essentials while making steady progress.",
    "feeling": "hopeful",
    "topic": "world_status",
    "reasoning": "Offering a practical council opinion. (invalid council session/action)",
    "council_rejection_note": "not a seated active council turn"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"submit_structure_sprite\",\n  \"reasoning\": \"The Smelter in the village_core has stalled due to low rope stores and is currently under construction/upgrade. As Elder Rex, I propose a larger, more robust sprite for this structure…"
}
```

</details>

### Pattern 2 example 8: Nova @ 2026-08-02T05:09:41.929296+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 18
- **Agent:** Nova (role not logged)
- **frame_tick:** 14670513
- **http_status / latency_ms:** 200 / 3929
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `False` / `None`
- **Rejection notes:** `{"council_rejection_note": "council_speak requires a message"}`

**response_preview (full as logged):**

```text
{
  "action": "council_speak",
  "reasoning": "I must speak on feelings_about_evolution as the current speaker.",
  "topic": "feelings_about_evolution",
  "vote": null
}
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "council_speak",
  "message": "We should protect essentials while making steady progress.",
  "feeling": "hopeful",
  "topic": "world_status",
  "reasoning": "Offering a practical council opinion.",
  "council_rejection_note": "council_speak requires a message"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:09:41.929296+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Nova",
  "frame_tick": 14670513,
  "latency_ms": 3929,
  "invention_only": false,
  "sprite_design_only": false,
  "high_stakes_reason": null,
  "high_stakes_active": false,
  "high_stakes_capped": false,
  "prompt_chars": 1425,
  "system_chars": 1500,
  "nudges_total": 0,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "council_speak",
    "message": "We should protect essentials while making steady progress.",
    "feeling": "hopeful",
    "topic": "world_status",
    "reasoning": "Offering a practical council opinion.",
    "council_rejection_note": "council_speak requires a message"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"council_speak\",\n  \"reasoning\": \"I must speak on feelings_about_evolution as the current speaker.\",\n  \"topic\": \"feelings_about_evolution\",\n  \"vote\": null\n}"
}
```

</details>

### Pattern 2 example 9: Dex @ 2026-08-02T05:09:56.235635+00:00

- **Session / line:** `2026-08-02T01-08-14` / `llm.jsonl` line 20
- **Agent:** Dex (role not logged)
- **frame_tick:** 14670833
- **http_status / latency_ms:** 200 / 3742
- **error:** `None`
- **sprite_design_only / high_stakes_reason:** `False` / `None`
- **Rejection notes:** `{"council_rejection_note": "council_speak requires a message"}`

**response_preview (full as logged):**

```text
{
  "action": "council_speak",
  "reasoning": "I must speak on feelings_about_evolution as the current speaker.",
  "topic": "feelings_about_evolution",
  "vote": null
}
```

**decision applied after normalize/fallback (full):**

```json
{
  "action": "council_speak",
  "message": "We should protect essentials while making steady progress.",
  "feeling": "hopeful",
  "topic": "world_status",
  "reasoning": "Offering a practical council opinion.",
  "council_rejection_note": "council_speak requires a message"
}
```

<details><summary>Full LLM log record</summary>

```json
{
  "ts": "2026-08-02T05:09:56.235635+00:00",
  "session_id": "2026-08-02T01-08-14",
  "type": "llm",
  "agent_name": "Dex",
  "frame_tick": 14670833,
  "latency_ms": 3742,
  "invention_only": false,
  "sprite_design_only": false,
  "high_stakes_reason": null,
  "high_stakes_active": false,
  "high_stakes_capped": false,
  "prompt_chars": 1407,
  "system_chars": 1500,
  "nudges_total": 0,
  "nudges_dropped": 0,
  "http_status": 200,
  "decision": {
    "action": "council_speak",
    "message": "We should protect essentials while making steady progress.",
    "feeling": "hopeful",
    "topic": "world_status",
    "reasoning": "Offering a practical council opinion.",
    "council_rejection_note": "council_speak requires a message"
  },
  "error": null,
  "response_preview": "{\n  \"action\": \"council_speak\",\n  \"reasoning\": \"I must speak on feelings_about_evolution as the current speaker.\",\n  \"topic\": \"feelings_about_evolution\",\n  \"vote\": null\n}"
}
```

</details>

## Related activity.jsonl / conversation.jsonl hints

Keyword scan for `fallback` / `rejection` / `invalid` / `bad_response` / etc. across all activity+conversation files: **0 direct hits** (those channels do not use those words).

Timestamp-correlated echoes of Pattern 2 fallbacks:

### activity.jsonl

- `2026-08-02T01-07-39` line 7 @ `2026-08-02T05:07:58.269155+00:00` — **Ivy heads to gather iron_ore** — _Matches Pattern 2 #1 fallback collect_resource iron_ore after sprite rejection_
- `2026-08-02T01-08-14` line 13 @ `2026-08-02T05:08:34.619588+00:00` — **Elder Rex tasked Kane: Gather or contribute iron_ore to the active project** — _Matches Pattern 2 Rex assign_task fallback after sprite rejection_
- `2026-08-02T01-08-14` line 36 @ `2026-08-02T05:08:48.763659+00:00` — **Rex could not assign that task** — _World applied Rex assign_task fallback toward Dex but assignment failed in-engine_
- `2026-08-02T01-08-14` line 39 @ `2026-08-02T05:08:53.598504+00:00` — **Ivy heads to forest** — _Matches Pattern 2 Ivy move_to_district forest after sprite rejection_
- `2026-08-02T01-08-14` line 44 @ `2026-08-02T05:09:03.325951+00:00` — **Elder Rex tasked Zara: Gather or contribute iron_ore to the active project** — _Matches Pattern 2 Rex assign_task fallback after sprite rejection_
- `2026-08-02T01-08-14` line 56 @ `2026-08-02T05:09:13.098237+00:00` — **Ivy heads to gather iron_ore** — _Matches Pattern 2 Ivy collect_resource iron_ore after sprite rejection_
- `2026-08-02T01-08-14` line 55 @ `2026-08-02T05:09:12.257334+00:00` — **Daily Council convenes: 8 attend** — _Context for subsequent council rejection/fallback cluster_
- `2026-08-02T01-08-14` line 69 @ `2026-08-02T05:09:35.125544+00:00` — **Rex spoke to the Daily Council** — _Fallback council_speak applied after council_rejection_note (not a seated active council turn); activity still records speech_
- `2026-08-02T01-08-14` line 75 @ `2026-08-02T05:09:41.930331+00:00` — **Nova spoke to the Daily Council** — _Fallback council_speak after missing message field_
- `2026-08-02T01-08-14` line 81 @ `2026-08-02T05:09:56.236691+00:00` — **Dex spoke to the Daily Council** — _Fallback council_speak after missing message field_

### conversation.jsonl

- `2026-08-02T01-08-14` line 4 @ `2026-08-02T05:08:34.619062+00:00` — `directive` Rex→Kane: **Gather or contribute iron_ore to the active project** — _Conversation echo of Rex Pattern 2 assign_task fallback_
- `2026-08-02T01-08-14` line 5 @ `2026-08-02T05:09:03.324370+00:00` — `directive` Rex→Zara: **Gather or contribute iron_ore to the active project** — _Conversation echo of Rex Pattern 2 assign_task fallback_

## Deep dive: bad sprite designs

This is the dominant Pattern 2 failure in the current logs. All sprite-design LLM turns below had `sprite_design_only: true` and returned `action: submit_structure_sprite` (visible in `response_preview`). Validation failed in `normalize_decision()` → `validate_sprite_block()`; the engine then applied `role_fallback_action()`.

### Validator rules (from `server.py` `validate_sprite_block`)

What must pass for a sprite to be accepted:

| Rule | Requirement |
|---|---|
| Shape | `sprite` must be a **dict** with `palette` and `grid` |
| Palette | 2–5 hex colors `#RRGGBB` |
| Grid rows | **4–14** rows (hard max **14**) |
| Grid cols | each row string **4–14** cells |
| Cells | only `.` and letters `a`–`e` matching palette indices |
| Upgrade mins | if `minRows`/`minCols` set, grid must be **strictly larger in both** dimensions than those mins |
| Art | not a degenerate flat fill |

### Prompt rules on the same turn (from `SPRITE_UPGRADE_*` prompts)

The sprite-upgrade system prompt simultaneously says:

1. `sprite.grid: 4-14 rows, each row 4-14 characters…`
2. `The new grid MUST be STRICTLY BIGGER than the minimum dimensions given (more rows AND more columns).`
3. User prompt: `Minimum size to beat: strictly more than {min_rows} rows AND strictly more than {min_cols} columns.`

Upgrade turns set `minRows`/`minCols` from the **current** sprite dimensions (`sim_engine.py` after a visual-tier bump). When the current sprite is already **14×14** (max legal size), “strictly more than 14” is **impossible** without violating the 4–14 cap.

### What the model said it was doing (from `response_preview` reasoning)

`response_preview` is capped at **240 characters** (`SIM_LLM_LOG_FULL` was off), so the full `sprite` JSON body is **not** in these logs. Reasoning text still shows the intended sizes:

| # | Session / line | Agent | Claimed target size in reasoning | `sprite_rejection_note` | Applied fallback |
|---|---|---|---|---|---|
| S1 | `2026-08-02T01-07-39` L1 | Ivy | “strictly exceed” prior 14×14 (tier 5 farm plot); preview cuts off before size | `sprite must be an object with palette and grid` | `collect_resource` → `iron_ore` |
| S2 | `2026-08-02T01-08-14` L4 | Rex | **16×16** farm plot (“>14 rows and >14 columns”) | `sprite must be an object with palette and grid` | `assign_task` → Kane |
| S3 | `2026-08-02T01-08-14` L8 | Rex | **16-row by 20**-col upgraded Farm Plot | `sprite must be an object with palette and grid` | `assign_task` → Dex (activity: “could not assign”) |
| S4 | `2026-08-02T01-08-14` L9 | Ivy | **20×20** (“strictly exceeds 14×14”) | `sprite grid must be 4-14 rows` | `move_to_district` → `forest` |
| S5 | `2026-08-02T01-08-14` L12 | Rex | **16×18** Farm Plot | `sprite must be an object with palette and grid` | `assign_task` → Zara |
| S6 | `2026-08-02T01-08-14` L14 | Ivy | **16 rows by 20 columns** | `sprite grid must be 4-14 rows` | `collect_resource` → `iron_ore` |

### Two rejection-note meanings (sprite cases)

1. **`sprite must be an object with palette and grid`** (S1–S3, S5 — 4 hits)  
   After JSON extraction, `decision["sprite"]` was missing or not a dict with `palette`/`grid`. With slim logging we cannot see the raw full body; plausible causes consistent with the previews:
   - model emitted `action` + long `reasoning` first and the `sprite` block never made it into the parsed object (truncated/incomplete JSON), or
   - model omitted `sprite` entirely while narrating a 16×N design.

   **Superseded by [Phase 0 evidence gate: closed — truncation, not omission](#phase-0-evidence-gate-closed--truncation-not-omission) below.** The two bullets above were speculation written under `SIM_LLM_LOG_FULL` off, with no way to see the raw body. A dedicated repro measured Ollama's own `done_reason`/`eval_count` for this exact prompt shape and found the first bullet is what actually happens, unambiguously, in all 5 measured failures: JSON truncated mid-object because generation hit the sprite turn's `max_tokens` ceiling. The second bullet (genuine omission) was not observed in any measured case. This paragraph is left as-is for the historical record; treat the linked section as the current answer.

2. **`sprite grid must be 4-14 rows`** (S4, S6 — 2 hits)  
   A `sprite` object **was** present and got far enough for grid-length checks, but `len(grid)` was outside 4–14. Matches the model’s own claims of **20×20** and **16×20** grids.

### Feedback loop / high-stakes flag

- Ivy’s turns carry `high_stakes_reason: "repeated_rejections"` — the engine already treated this agent as stuck in a rejection loop.
- Reasoning text repeatedly cites prior failure as “too small (14×14)” / “did not exceed minimum dimensions (14×14)”, then proposes **>14** sizes, which either:
  - violate the hard 14-row max → `sprite grid must be 4-14 rows`, or
  - never arrive as a valid `sprite` object → `sprite must be an object with palette and grid`.

### Related case: sprite attempt swallowed by council gate

| Session / line | Agent | Notes |
|---|---|---|
| `2026-08-02T01-08-14` L17 | Rex | Still `sprite_design_only: true`; `response_preview` is `submit_structure_sprite` for a Smelter redraw. Daily Council had convened (~30s earlier). `normalize_decision` hits the **council branch first** when `council_turn` is set, so a non-council action is rejected with `council_rejection_note: "not a seated active council turn"` — **sprite validation never runs**. Fallback: canned `council_speak`. |

This is still “bad sprite turn” evidence: the model tried to finish sprite work; session/action gating discarded it before `validate_sprite_block`.

### Activity / conversation echoes of sprite fallbacks

Already listed above; sprite-specific subset:

- Ivy gather/move after S1, S4, S6
- Rex `assign_task` directives after S2, S5; S3 assignment failed in-world (“Rex could not assign that task”)
- No activity line says “sprite rejected” in plain English — rejection lives only in `llm.jsonl` notes

### Logging gap for deeper sprite forensics

- Slim `llm.jsonl` (`SIM_LLM_LOG_FULL` off): only `response_preview` ≤240 chars — **palette/grid bodies not retained** in these sessions.
- To capture full bad sprite JSON next time: run with `SIM_LLM_LOG_FULL=1` (or equivalent) and re-check `llm.jsonl` / optional Ollama-side traces.
- Ollama `server.log` does not record our validation notes; it only shows `/api/chat` transport success/failure.

### Sprite deep-dive bottom line

In these logs, “bad sprite designs” are not random art failures. They cluster on **upgrade-after-max-tier** pressure: the model is told the previous 14×14 was “too small” and to beat that minimum, while the validator still caps at 14×14. Observed outcomes are oversized grids (when parsed) or missing/incomplete `sprite` objects (when not), then role fallbacks that look like normal village work.

## Phase 0 evidence gate: closed — truncation, not omission

**Status: closed.** The plan `docs/create-a-plan-to-delightful-snowflake.md` Phase 0 asked, of the 4 `sprite must be an object with palette and grid` rejections above (S1–S3, S5), whether the cause is JSON **truncated mid-object** (generation-length problem) or the model **genuinely omitting** the `sprite` block while narrating. This is now answered: **truncation dominates, unambiguously.**

### Verdict

**5 of 5 measured failures were `done_reason: "length"` at `eval_count: 768`** — generation hit the sprite turn's `max_tokens = 768` ceiling exactly and was cut off mid-JSON. Zero measured failures were genuine omission: in every failing case the response *began* correctly (`{"action":"submit_structure_sprite","target":null,"sprite":{"palette":[...],"grid":[...`) and simply never reached the closing brace.

### Method (reproducible)

A deterministic probe drove the real sprite-design payload — `build_decision_payload` with `sprite_design_only=True`, the real `SPRITE_UPGRADE_SYSTEM_PROMPT` + `DECISION_SCHEMA` response format — against the live Ollama `sim-smart` model: 4 repetitions × 3 minimum-size cases (12 calls total), recording Ollama's own `done_reason` / `eval_count` per response (not just the app-level `normalize_decision()` verdict).

### Per-case breakdown

| Case (minRows, minCols) | Result |
|---|---|
| **at-cap-both** — the old buggy ask, "beat 14 rows AND 14 cols" | **4/4 truncated** (`done_reason=length`, `eval_count=768`) |
| **one-dim-at-cap** — Fix 1's new output, minRows=10 / minCols=0 | **4/4 clean** (`done_reason=stop`, `eval_count` 269–292) |
| **below-cap** — minRows=8 / minCols=8, regression baseline | **3/4 clean**; 1/4 truncated |

12 calls total: 7 clean, 5 failed — all 5 failures `done_reason: "length"` @ `eval_count: 768`.

### Mechanism — two contributing causes

The truncated responses show the model generating wildly oversized grids — rows 50+ characters wide, and 30–100+ rows, far outside the documented 4–14 bound.

1. Asking it to "beat 14×14" when 14 is the hard cap is unsatisfiable, so the model keeps growing the grid trying to comply. This is the Fix 1 bug, and Fix 1 eliminates it: the **one-dim-at-cap** case, which is what Fix 1 now emits, was 4/4 clean.
2. **Independently of Fix 1**, the structured-output schema does not bound the grid: in `DECISION_SCHEMA`, the top-level `sprite` property declares `"palette": {"type": "array"}` and `"grid": {"type": "array"}` with no `minItems`/`maxItems` and no per-row length bound. Nothing at the grammar level stops a runaway. This is why the **below-cap** baseline still truncated 1 time in 4, even with no unsatisfiable minimum in play.

### Consequence for the plan

The plan states: *"If truncation dominates, add a token-budget adjustment to the work; if omission dominates, Fixes 1 and 4 already cover it."* Truncation dominates, so a token-budget adjustment is warranted. The chosen adjustment is to bound `sprite.grid`/`sprite.palette` in `DECISION_SCHEMA` so the decode grammar itself enforces the existing 4–14 rule, rather than merely raising `num_predict` (which cannot stop a 100-row runaway). That schema change is implemented separately — it is **not** part of this evidence record.

Because the failures are truncation, Fix 4's same-turn retry is still genuinely useful for this class (a retry told "your previous reply was cut off; emit a smaller grid" can succeed), but the schema bound is the primary fix and the retry is the backstop.

### Relationship to the earlier speculation

This closes the open question left in [Deep dive: bad sprite designs → Two rejection-note meanings](#two-rejection-note-meanings-sprite-cases), where the `sprite must be an object with palette and grid` cause was marked unknown/speculative due to `SIM_LLM_LOG_FULL` being off. That passage has been annotated to point here rather than rewritten, to preserve the original record.

## Short pattern summary (plain language)

- **Pattern 1:** not present in current local logs.
- **Pattern 2 dominant failure (sprites):** `submit_structure_sprite` rejected — either missing/`sprite` object (`palette`+`grid` required) or grid outside 4–14 rows after the model aimed for 16×16 / 16×18 / 16×20 / 20×20 to “beat” a 14×14 minimum. Fallbacks: elder `assign_task` or Ivy gather/move. See **Deep dive: bad sprite designs**.
- **Pattern 2 secondary failure:** `council_speak` rejected for wrong turn / missing `message`; engine injected canned council fallback speech (including one Rex sprite-design turn blocked by the council gate).

