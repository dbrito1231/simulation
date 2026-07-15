# SPEC 07 — Actions

**The action catalog** — the sole source for every decision action an agent can be
offered. No other spec lists actions.

**Canonical for:** all 35 `DECISION_ACTIONS`: params, flag gate/preconditions,
effect, validation. The build pipeline and blueprint two-stage flow as the core
game loop.
**See also:** [01-architecture.md](01-architecture.md#action-sync-invariant) for the
action-sync invariant (every action must stay consistent across
`DECISION_ACTIONS`/`DECISION_SCHEMA`/`SYSTEM_PROMPT`/`apply_decision`/
`available_actions`/`ACTION_LABELS`) — this spec is the "canonical action name
list" side of that invariant; [03-cognition.md](03-cognition.md) for
`DECISION_SCHEMA`/`normalize_decision`/model routing; [05-world.md](05-world.md)
for districts/terrain/structures referenced by params; [08](08-systems-economy.md)/
[09](09-systems-society.md)/[10](10-path1.md) for the flag semantics gating many
of these actions.

Fact source: `DECISION_ACTIONS` (server.py:752-777, verified 35 entries, listed
here in declaration order). Params legend: `target` (agent name / district id /
structure id / grid `"gx,gy"` depending on action), `target_district`, `message`,
`new_role`, `blueprint` (object), `recipe` (object), `rule` (object), `vote`
(`yes`/`no`), `sage_decision` (`approve`/`deny`), `sprite` (grid block).

## Action table

| Action | Key params | Flag gate / precondition | Effect (`apply_decision`, sim_engine.py) |
|---|---|---|---|
| `move_to_district` | `target` or `target_district` | none | Sets movement target to the resolved district; accepts either param since models commonly put the id in `target_district` |
| `move_to_agent` | `target` (agent name) | none | Moves toward the named agent, or the nearest agent if `target` is missing/unresolved |
| `collect_resource` | `target` (resource id, optional), `target_district` | none | Gathers a resource in-zone (subject to ecology gate, [05](05-world.md)); if no active project district resolves, falls through to `start_project` |
| `talk_to_nearby` | `target` (recipient or "everyone"), `message` | `AGENT_MESSAGING` for delivery ([06](06-agents.md)) | Sets `agent["message"]`, logs conversation, delivers to inbox, may spread beliefs/teach ([09](09-systems-society.md)) |
| `trade_resource` | `target` (agent name) | `ECONOMY_ENABLED` for priced trade | Moves toward target if not adjacent; within 80px, trades the agent's most-abundant resource — priced via market if `ECONOMY_ENABLED` and a market is active, else 1-for-nothing barter |
| `start_project` | `target` (project type), `target_district` | project type must exist in `PROJECT_TEMPLATES`/`projectRegistry` | Starts a district build project (see build pipeline below) |
| `contribute_resources` | `target` (resource id, optional), `target_district` | active project in the district | Deposits a resource toward the active project; auto-builds if complete; falls back to gathering the unmet resource |
| `build_structure` | `target_district` | project fully funded | Completes construction if the district's project is fully funded, else reports waiting |
| `start_terraform` | `target` (terraform id: `plant_grove`/`clear_field`/`extend_beach`), `target_district` | `ECOLOGY_ENABLED`; district kind must match template | Starts a terraform project funded like a build ([05](05-world.md)) |
| `repair_structure` | `target` (structure id) | `GOODS_ENABLED` | Restores `condition`, un-ruins a collapsed structure for half original materials ([08](08-systems-economy.md)) |
| `upgrade_structure` | `target` (structure id/type/name) | `STRUCTURE_UPGRADES_ENABLED`; target must be in `upgradeable_structures` (validated by `normalize_decision`) | Raises structure `level` by `LEVEL_STEP`, up to `MAX_STRUCTURE_LEVEL` ([05](05-world.md)) |
| `submit_structure_sprite` | `sprite` (grid block) | only offered when it's the agent's sprite-design turn; sprite must pass `validate_sprite_block` | Applies a custom sprite render to a structure |
| `propose_blueprint` | `blueprint` (id/name/needs/function/new_resources/visual_style/sprite[/tier]) | must pass `validate_blueprint` (schema, uniqueness, tier gate if `TECH_TREE_ENABLED`); rejected-id blueprints are permanently blocked | Adds to `pendingBlueprints` with `sageReview: "pending"`; records an invention-council proposal if `TECH_TREE_ENABLED` |
| `approve_blueprint` | `target` (blueprint id), `target_district` (optional) | actor role `elder`; `SAGE_REVIEW_ENABLED` requires `sageReview` already `approved`/`skipped` | Registers the new structure/resource type, pops the pending blueprint, starts a district project for it (or applies as a structure upgrade if `duplicateOf` an existing type) |
| `reject_blueprint` | `target` (blueprint id) | actor role `elder` | Pops the pending blueprint into `rejectedBlueprintIds` (amnesty-expiring — [09](09-systems-society.md)) |
| `sage_review_blueprint` | `target` (blueprint id), `sage_decision` (`approve`/`deny`) | actor is the Sage reviewer; blueprint pending; `sage_decision` valid (enforced by `normalize_decision`) | Marks `sageReview` `approved`/`denied`, gating `approve_blueprint` |
| `assign_task` | `target` (agent name), `message` (task text) | actor role `elder`; target idle | Sets `target["assignedTask"]`, delivers a directive message (deliberately not broadcast — see sim_engine.py:8273-8276) |
| `change_role` | `new_role` | none (unconditional) | Sets `agent["role"]` directly — the manual/LLM-chosen counterpart to `switch_role`'s auto-eligible flow |
| `rest` | — | none — the universal safe fallback | No-op; `summary = "<agent> rested"` |
| `heal_agent` | `target` (agent name, optional) | actor typically role `healer` for the yield bonus, but any agent may act | Restores health (`HEAL_AMOUNT`, doubled for `healer` role, + skill bonus if `CULTURE_ENABLED`); revives an incapacitated patient; cannot target a dead (unburied) agent |
| `craft_item` | `target` (recipe id) | `CRAFTING_ENABLED`; recipe known, inputs available, correct station present | Consumes recipe inputs, produces the crafted resource ([08](08-systems-economy.md)) |
| `propose_recipe` | `recipe` (object) | `CRAFTING_ENABLED` | Adds to `pendingRecipes` for elder review |
| `approve_recipe` / `reject_recipe` | `target` (recipe id), `message` | actor role `elder` | Registers or discards a pending recipe |
| `switch_role` | `new_role` or `target` | `EMERGENT_ROLES`; `new_role` must be a known role and differ from current | Sets role, clears `assignedTask`/`idleCycles` — same code path the deterministic auto-switch backstop uses ([06](06-agents.md)) |
| `propose_rule` | `rule` (id/name/kind/value/description) | `RULES_ENABLED`; must pass `_validate_rule` | Adds to `pendingRules` with the proposer's own `yes` vote, tallies immediately |
| `vote_rule` | `target` (rule id), `vote` | `RULES_ENABLED`; rule must be pending | Records the agent's vote; succession ballots cross-cancel sibling candidate votes ([06](06-agents.md), [09](09-systems-society.md)) |
| `repeal_rule` | `target` (enacted rule id) | `RULES_ENABLED`; rule must be currently enacted; pending-rule cap not exceeded | Opens a `repeal_<id>` ballot reusing the same vote/quorum scaffold |
| `bury_agent` | `target` (deceased agent name, optional) | `CEMETERY_ENABLED`; a working (non-disrepaired) cemetery structure must exist; actor within `BURY_CONTACT_DIST` of the corpse | Assigns the corpse a grave-grid slot, marks `buried: True` ([05](05-world.md)) |
| `place_block` | `target` (block type or `"gx,gy"`), `message`/`new_role` (block type fallback) | `COMPOSABLE_BUILD_ENABLED` (path1); tile unoccupied, district tile cap not reached, resource cost affordable | Charges the block's cost, writes `district["tiles"][gx,gy]` ([05](05-world.md)) |
| `remove_block` | `target` (`"gx,gy"`, optional — defaults to agent's cell) | `COMPOSABLE_BUILD_ENABLED` | Refunds the block's cost, clears the tile |
| `dig_terrain` | — (acts on agent's current cell) | `TERRAIN_TILES_ENABLED`; tool-free by design (bootstrap stone source) | Converts `grove`→`soil` (or similar) at the agent's grid cell, yields a resource |
| `plant_terrain` | — (acts on agent's current cell) | `TERRAIN_TILES_ENABLED`; needs 1 wood; cell must be `soil` or `rock` | Converts the cell toward a planted/vegetated kind, consumes 1 wood |
| `propose_treaty` | `rule` (id/name/value/description) | `PATH1_DIPLOMACY_ENABLED`; rule must have `id`+`name` | Adds a `kind: "treaty"` entry to `pendingRules` with proposer auto-yes, tallies immediately |
| `vote_treaty` | `target` (treaty id), `vote` | `PATH1_DIPLOMACY_ENABLED`; treaty must be pending | Records vote; on enactment appends to `civilization["treaties"]` ([10](10-path1.md)) |

`available_actions` in the think payload (sim_engine.py:9143-9152) further filters
this list per-agent by live flag state: `start_terraform` requires
`ECOLOGY_ENABLED`; `repair_structure` requires `GOODS_ENABLED`; `bury_agent`
requires `CEMETERY_ENABLED`; `repeal_rule` requires `RULES_ENABLED`;
`upgrade_structure` requires `STRUCTURE_UPGRADES_ENABLED`;
`submit_structure_sprite` only appears on an agent's actual sprite-design turn;
`place_block`/`remove_block` require `COMPOSABLE_BUILD_ENABLED`;
`dig_terrain`/`plant_terrain` require `TERRAIN_TILES_ENABLED`;
`propose_treaty`/`vote_treaty` require `PATH1_DIPLOMACY_ENABLED`. All other
actions in the table are always offered (subject to `DECISION_SCHEMA`'s fixed
enum superset — [03-cognition.md](03-cognition.md)). Invalid or disallowed choices
are replaced by `normalize_decision` + `role_fallback_action` (server.py) before
reaching `apply_decision`, per the action-sync invariant.

## The build pipeline

The core resource-to-structure loop, chained across four actions:

1. **`start_project`** — an agent (any role, but usually the district's
   specialist) names a project type and district. `_start_project_for` looks up
   the type in `PROJECT_TEMPLATES` (or `projectRegistry` for approved custom
   blueprints) and opens `civilization["districtProjects"][district_id]` with
   zeroed `contributed` counters against the template's `needs`.
2. **`collect_resource`** — agents gather raw resources in their zone (subject to
   the ecology stock gate, [05-world.md](05-world.md)).
3. **`contribute_resources`** — deposits a carried resource into the active
   project's `contributed` tally, up to the amount `needs` requires. Once fully
   funded, contributing again auto-triggers construction.
4. **`build_structure`** — explicit trigger to complete construction once
   `_is_project_complete(district_id)` is true; appends the new structure to
   `civilization["structures"]` with `level: 1`, `visualTier: 1`.

`collect_resource`, `contribute_resources`, and `build_structure` all fall back
to `start_project` when no active project exists in the resolved district, so an
LLM choosing any of the three "downstream" actions before a project exists still
makes forward progress.

## The blueprint two-stage flow

Custom structure/resource types (beyond the seed `PROJECT_TEMPLATES`) go through
a two-stage approval gate before they can be built:

1. **`propose_blueprint`** — any agent drafts a `blueprint` object (needs, effect
   `function`, optional `new_resources`, visual style/sprite, tier). Server-side
   `validate_blueprint` rejects malformed, duplicate-id, or (when
   `TECH_TREE_ENABLED`) tier-ungated proposals before it ever reaches
   `apply_decision`. Valid proposals enter `pendingBlueprints` with
   `sageReview: "pending"`.
2. **`sage_review_blueprint`** — the Sage (elder reviewer) marks the pending
   blueprint `approved` or `denied`, with an optional reasoning note. This stage
   is gated by `SAGE_REVIEW_ENABLED`; when off, blueprints skip straight to
   step 3 eligibility.
3. **`approve_blueprint`** / **`reject_blueprint`** — the elder gives the final
   verdict. `approve_blueprint` requires `sageReview` to already be
   `approved`/`skipped` (when `SAGE_REVIEW_ENABLED`). Approval registers the new
   resource/structure type into `resourceRegistry`/`projectRegistry` and opens a
   district project for it (via `PROJECT_KIND`/geo matching); if the blueprint
   duplicates an already-built type's effect vector (`duplicateOf`), approval
   instead applies it as an `upgrade_structure` on the existing instance.
   Rejection records the blueprint id in `rejectedBlueprintIds` (amnesty-timed
   expiry — see [09-systems-society.md](09-systems-society.md)).

Full invention-council mechanics (tier gates, duplicate-effect-vector detection,
Sage-emergency interaction, safeguards against runaway proposal spam):
[09-systems-society.md](09-systems-society.md).

## Action-sync invariant reminder

Any change to this catalog must be mirrored across `DECISION_ACTIONS`,
`DECISION_SCHEMA`, `SYSTEM_PROMPT` (server.py), `apply_decision` +
`available_actions` (sim_engine.py), and `ACTION_LABELS` (index.html, display
only) — see [01-architecture.md](01-architecture.md#action-sync-invariant) for the
full table of locations.
