# LLM fallback: default actions catalog & observed responses

Companion to [`llm-fallback-rejection-evidence.md`](llm-fallback-rejection-evidence.md).

This file documents:

1. **Available default / fallback actions** the engine may substitute via `role_fallback_action()` (and the separate Pattern‑1 error path).
2. **What agents actually did** in the logged Pattern‑2 cases (sprite + council rejections).

Source of truth for the ladder: `role_fallback_action()` in `simulation/server.py` (also summarized in `specs/03-cognition.md`). No code was changed for this document.

---

## How fallbacks are chosen

When `normalize_decision()` rejects a model answer (or cannot use it), it calls `role_fallback_action(role, agent_data)` and returns that decision (often with a `*_rejection_note` and an `(invalid …)` suffix on `reasoning`).

**Priority is first-match wins** (higher items block lower ones). Context fields (`council_turn`, `idle_agents`, `active_project`, `world_zone`, etc.) decide which branch fires.

### A. Council-seated turn (before normal village ladder)

Only if `council_turn` **and** `council_seated`:

| Council phase | Fallback action | Default payload (summary) |
|---|---|---|
| `discussion` | `council_speak` | Message: succession compare-line **or** `"We should protect essentials while making steady progress."`; `feeling: hopeful`; topic from agenda (or `leadership_vacancy`) |
| `proposal` | `council_propose` | `kind: idea`, title `"Steady village priorities"`, fixed detail about food/health/stalled project |
| `voting` | `council_vote` | `vote: abstain` |
| `verdict` (elder only) | `council_speak` | Announce recorded verdict outcome |

If council phase doesn’t match (or elder isn’t on verdict), execution falls through to the village ladder below.

**Important interaction seen in logs:** if `council_turn` is set but the model returns a **non-council** action (e.g. still `submit_structure_sprite`), normalize may reject with `council_rejection_note` and still substitute a council-oriented fallback from this ladder when seated—or the canned discussion speak when the fallback path rebuilds one. See observed Rex case below.

### B. Village / role ladder (non-council, or fall-through)

| Priority | Condition | Fallback action | Typical reasoning |
|---|---|---|---|
| 1 | Village `needed_role` ≠ current; agent not elder/builder/healer; current role has no primary resource | `switch_role` | Retrain to fill the gap |
| 2 | Elder + pending blueprint ready (`approved`/`skipped` review) | `approve_blueprint` | Review pending blueprint |
| 3 | Elder + pending blueprint needs review | `sage_review_blueprint` (`sage_decision: approve`) | Check geography before approving |
| 4 | Elder + pending roles | `approve_role` | Review pending role proposal |
| 5 | Elder + `idle_agents` non-empty | `assign_task` | Assign work to an idle villager; message from `task_for_role(...)` |
| 6 | Upgradeable structures exist **and** no active project | `upgrade_structure` | Upgrade before building duplicates |
| 7a | No active project **and** invention REQUIRED | `collect_resource` | Gather until invention path unblocks |
| 7b | No active project (else) | `start_project` | Role-default project via `role_default_project` |
| 8 | Agent holds a resource the project still needs | `contribute_resources` | Contribute held shortfall resource |
| 9 | Hunter + prey in range | `hunt_wildlife` | Hunt nearby wildlife |
| 10a | Farmer / gatherer / fisher wrong zone | `move_to_district` | farm / forest / beach |
| 10b | Farmer / gatherer / fisher in zone | `collect_resource` | Gather shortfall (or generic) |
| 11a | Miner not in cave | `move_to_district` → `cave` | Head to mine |
| 11b | Miner in cave | `collect_resource` | Mine shortfall or gold |
| 12a | Hunter not on hunting grounds | `move_to_district` → `forest` | Head to hunting grounds |
| 12b | Hunter on grounds, no prey | `collect_resource` | Gather while scouting |
| 13 | Builder | `contribute_resources` | Contribute to active project |
| 14 | Trader | `move_to_district` → `market` | Head to market |
| 15 | Guard / scout / explorer | `move_to_district` → `village` | Patrol |
| 16a | Healer / elder / blacksmith + active project | `contribute_resources` | Support the build |
| 16b | Healer / elder / blacksmith, no project | `move_to_district` → `village` | Return to center |
| 17 | Catch-all | `collect_resource` | Working toward civilization goals |

### C. Pattern 1 error path (no usable AI answer) — not `role_fallback_action`

Per `specs/03-cognition.md` / `run_agent_decision()`:

| Logged `error` | Decision shape the engine sees | Notes |
|---|---|---|
| `llm offline` | `{"error": "llm offline", "action": "rest"}` | Includes missing-model setup failure |
| `llm timeout` | `{"error": "llm timeout", "action": "rest"}` | Triggers orphan backpressure accounting |
| `compute_error` | `{"error": "compute_error", "action": "rest"}` | Ollama compute-error body |
| `server_error` | `{"error": "server_error", "action": "rest"}` | Uncaught exception |
| `bad_response` / `context_overflow` | **`role_fallback_action()` result** tagged with that error | JSON/schema extraction failed after response |

**In current local logs: Pattern 1 count = 0** — none of these were observed. Listed here because they are the allowed defaults when that pattern *does* fire.

### Actions that appear in the fallback catalog (unique set)

`assign_task`, `approve_blueprint`, `approve_role`, `collect_resource`, `contribute_resources`, `council_propose`, `council_speak`, `council_vote`, `hunt_wildlife`, `move_to_district`, `rest` (Pattern‑1 error path only), `sage_review_blueprint`, `start_project`, `switch_role`, `upgrade_structure`.

These are **not** the full `DECISION_ACTIONS` list — only what the fallback / error paths emit.

---

## Observed defaults after Pattern 2 (these logs)

Sessions: `2026-08-02T01-07-39`, `2026-08-02T01-08-14`. Role is not on `llm.jsonl` rows; inferred from fallback choice + activity (Rex = elder path; Ivy = gatherer-style move/collect).

### Sprite rejections → applied fallback

| Case | Agent | Rejection | Applied default action | Target / message | Why this branch (best fit) |
|---|---|---|---|---|---|
| S1 | Ivy | missing sprite object | `collect_resource` | `iron_ore` | Gatherer-style ladder: in/near gather path, collect shortfall |
| S2 | Rex | missing sprite object | `assign_task` | Kane — `"gather or contribute iron_ore to the active project"` | Elder + idle agents (priority 5) |
| S3 | Rex | missing sprite object | `assign_task` | Dex — same iron_ore message | Elder + idle agents; **activity:** “Rex could not assign that task” (fallback chosen, world apply failed) |
| S4 | Ivy | grid not 4–14 rows | `move_to_district` | `forest` | Gatherer wrong-zone branch (priority 10a) |
| S5 | Rex | missing sprite object | `assign_task` | Zara — same iron_ore message | Elder + idle agents |
| S6 | Ivy | grid not 4–14 rows | `collect_resource` | `iron_ore` | Gatherer collect branch (priority 10b) |

### Council / gating rejections → applied fallback

| Case | Agent | Rejection | Applied default action | Payload | Why this branch (best fit) |
|---|---|---|---|---|---|
| C1 | Rex | `not a seated active council turn` (model still returned `submit_structure_sprite` during council window) | `council_speak` | Canned `"We should protect essentials while making steady progress."`, `feeling: hopeful`, `topic: world_status` | Council discussion fallback text (even though note says not seated — normalize built this council-shaped fallback) |
| C2 | Nova | `council_speak requires a message` | `council_speak` | Same canned essentials message / hopeful / `world_status` | Discussion-phase council fallback after empty `message` |
| C3 | Dex | `council_speak requires a message` | `council_speak` | Same canned essentials message / hopeful / `world_status` | Same as C2 |

### What was *not* observed as a fallback in these logs

From the allowed catalog, these defaults **did not** appear in the Pattern‑2 substitutions above:

`switch_role`, `approve_blueprint`, `sage_review_blueprint`, `approve_role`, `upgrade_structure`, `start_project`, `contribute_resources`, `hunt_wildlife`, `rest`, and non-forest `move_to_district` targets (`farm`, `beach`, `cave`, `market`, `village`).

That does not mean they are unavailable — only that context (elder+idle, gatherer zones, council discussion) selected other branches first.

---

## Plain-language summary

When sprite or council answers fail validation, agents don’t freeze. The engine picks a **safe canned action** from a fixed priority list:

- **Elder (Rex)** tended to **order idle villagers** (`assign_task` about iron_ore), or during council confusion emit a **stock council speech**.
- **Ivy** tended to **go to the forest** or **gather iron_ore** — normal gatherer defaults.
- **Nova / Dex** on bad `council_speak` got the same **stock council line** (“protect essentials…”).

Pattern‑1 (timeout/offline) would usually force **`rest`** with an `error` tag; that path has **no examples** in the current local session logs.
