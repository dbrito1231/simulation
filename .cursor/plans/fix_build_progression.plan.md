---
name: Fix Build Progression, GUI, Throughput, and Roster
overview: "Civilization never progresses because no agent ever calls start_project (0/36 LLM calls), so the build pipeline is dead behind an activeProject==null gate. Fix progression deterministically in code AND open start_project/build_structure to every role (weighted by role so project type stays role-appropriate), then address the secondary issues from ISSUES.md: GUI clutter (no depth sorting, overlapping labels, unrendered isThinking, missing zone labels, soft/overflowing layout), the serial one-at-a-time LLM queue that starves the builder/elder, and reducing the roster from 12 to 8 agents. Diagnosis source: simulation/ISSUES.md."
todos:
  - id: all-roles-start-build
    content: "index.html: remove the builder/elder gate on start_project and the builder gate on build_structure so any role can start and build; add a ROLE_PROJECT bias map so the project type stays role-appropriate (P0)"
    status: completed
  - id: redirect-build-no-project
    content: "index.html: in applyDecision, when build_structure/contribute_resources runs with no activeProject, redirect to start_project via a shared startProjectFor() helper (P0)"
    status: completed
  - id: add-startproject-nudge
    content: "index.html: in thinkAgent, nudge any thinking agent to start_project when active_project is 'none' (P0 backstop)"
    status: completed
  - id: allrole-fallback-client
    content: "index.html roleFallbackAction: every role starts a role-appropriate project when none active"
    status: completed
  - id: allrole-fallback-server
    content: "server.py role_fallback_action: every role starts a role-appropriate project when none active so server and client agree"
    status: completed
  - id: loosen-prompt
    content: "server.py SYSTEM_PROMPT rule 5: any agent may start_project (not only builders)"
    status: completed
  - id: elder-blueprint-authority
    content: "index.html + server.py: make blueprint approve/reject elder-only (remove builder); update prompt rule 7"
    status: completed
  - id: elder-directive
    content: "index.html + server.py: add civilization.directive the elder sets and broadcast it to other agents via the think payload, prompt, and behavior_nudge so the village follows the leader"
    status: completed
  - id: elder-cadence-roster
    content: "index.html: give the elder a shorter thinkInterval (and optional queue priority) and guarantee the elder is always in the active roster"
    status: completed
  - id: elder-assign-idle
    content: "MAIN RULE — index.html + server.py: elder checks for idle agents each turn (isIdle helper) and assigns them a task via a new assign_task action + assignedTask field, surfaced through the think payload, prompt, and behavior_nudge; deterministic fallback assigns work when the LLM doesn't"
    status: completed
  - id: gui-depth-sorting
    content: "index.html: depth-sort agents and structures by y before drawing so nearer entities paint last"
    status: completed
  - id: gui-label-stacking
    content: "index.html drawAgent/drawSpeechBubble: stop the role badge, name, dots, and speech bubble from overlapping; render the isThinking indicator"
    status: completed
  - id: gui-zone-labels
    content: "sprites.js: add readable labels for farm, forest, village, beach, cave (high contrast) and stop static houses from colliding with built structures"
    status: completed
  - id: gui-layout
    content: "index.html: add viewport meta + high-DPI canvas scaling, make layout fluid, enlarge agent/activity panes, and update conversation render to preserve scroll; fix the misleading Resources stat"
    status: completed
  - id: queue-parallelism
    content: "index.html: replace the serial drainThinkQueue with N concurrent in-flight LLM requests (MAX_CONCURRENT_LLM), keeping a small stagger"
    status: completed
  - id: roster-size
    content: "index.html: make the active roster configurable (ROSTER_SIZE / dev subset) defaulting to 8 agents; update specs/04-agent-spec.md since this deviates from the fixed 12"
    status: completed
  - id: validate-all
    content: Run the sim and walk the full validation checklist (progression, GUI, throughput, roster)
    status: completed
isProject: false
---

# Fix Build Progression, GUI, Throughput, and Roster

This plan starts with the P0 progression fix (Part 1) because nothing else matters if the world never
builds anything, then addresses the GUI, throughput, and roster issues that were previously deferred
(Parts 2–4). Parts 2–4 are independent and can be implemented in any order after Part 1.

---

## Implementation gotchas (verified against the current code — read first)

These are non-obvious traps confirmed by reading the source. Each is easy to get wrong and would cause a
subtle bug rather than a crash:

1. **Activity is double-logged today; don't compound it.** `applyDecision` sets a local `summary`, and the
   tail at [index.html:1107](simulation/index.html:1107) does `if (logActivity) pushActivity(summary)`.
   `logActivity` is initialized to `true` ([index.html:856](simulation/index.html:856)) and is **never set
   to `false` anywhere**, yet the `start_project`/`contribute_resources`/`build_structure` cases *also* call
   `pushActivity(summary)` inline ([index.html:955](simulation/index.html:955), `:971`, `:998`) — so those
   events are already logged twice. **For all new/edited branches (startProjectFor, assign_task, etc.): set
   `summary` and let the single tail call log it. Do NOT add inline `pushActivity` calls.** Optionally
   remove the redundant inline calls at 955/971/998 to fix the pre-existing double-log.

2. **`startProjectFor` should return a summary, not log.** Have it `return` the summary string (or `null`
   on no-op); the calling case assigns it to `summary`. This keeps logging centralized at line 1107 and
   avoids the double-log above. (Update the snippet's `sidebarDirty`/return accordingly — drop any inline
   `pushActivity`.)

3. **The server passes non-talk actions straight through.** `normalize_decision`
   ([server.py:445](simulation/server.py:445)) returns immediately for any action that isn't
   `talk_to_nearby`/`propose_blueprint`/`approve_blueprint`/`reject_blueprint`. So the new **`assign_task`
   action will bypass all validation** unless you add an explicit branch (like the blueprint ones) that
   (a) requires `role == "elder"` and (b) checks `target` is a real, currently-idle agent — otherwise fall
   back. Mirror the same guard client-side in `normalizeDecision` ([index.html:673](simulation/index.html:673)),
   which today also early-returns for any non-talk action ([index.html:676](simulation/index.html:676)).

4. **A new LLM decision field needs a schema change.** The elder `directive` (Fix 1.6 B) is **not** in the
   JSON schema block of `SYSTEM_PROMPT` ([server.py:109-118](simulation/server.py:109)), so the model won't
   emit it reliably. **Preferred: derive the directive deterministically** from the elder's `start_project`
   / `assign_task` actions (no schema change, always works). Only add a `directive` field to the prompt
   schema if you want the elder to author free-text directives — and if so, update the schema block and the
   example. `assign_task` itself needs no schema change: it reuses the existing `target` + `message` fields.

5. **`assignedTask` lifecycle.** Initialize `agent.assignedTask = null` at
   [index.html:412](simulation/index.html:412). Clear it in the `applyDecision` tail (near line 1099, where
   `lastAction` is set) when the agent performs a productive (non-`rest`, non-`talk`) action, so a completed
   task doesn't keep re-nudging.

---

# Part 1 — Build progression (P0, critical)

## Confirmed root cause (from logs + source)

The simulation boots with `civilization.activeProject = null` ([index.html:284](simulation/index.html:284)).
`contribute_resources` ([index.html:962](simulation/index.html:962)) and `build_structure`
([index.html:981](simulation/index.html:981)) are both gated on `activeProject`. The only thing that sets
it is `start_project` ([index.html:941](simulation/index.html:941)), which the LLM chose **0 of 36 times**
last session — despite the system prompt explicitly saying *"Builders start projects when none active"*
([server.py:92](simulation/server.py:92)) at `temperature: 0.4`. The existing recovery logic already knows
how to start a project ([index.html:631](simulation/index.html:631), [server.py:376](simulation/server.py:376))
but only fires on talk-redirect / bad-response, and a valid `build_structure` passes straight through
([server.py:445](simulation/server.py:445)). **Fix must be deterministic in code, not a prompt tweak.**

## Modification — all roles can start and build, weighted by role

Previously only the builder/elder could `start_project` and only the builder could `build_structure`. This
plan removes those hard role gates so **every agent can start and build projects**, while still respecting
roles: the *project type* an agent starts is biased by its role, so the behavior stays role-appropriate
rather than uniform. This also widens the funnel for the P0 fix — any agent that tries to build with no
active project now bootstraps one, instead of only two of the agents being able to.

Add a role→project bias map near `PROJECT_TEMPLATES`:

```js
// Each role prefers thematically appropriate project(s). A value may be a single type
// or a list (the builder handles both House and Wall). Falls back to "house".
const ROLE_PROJECT = {
  elder: "house", healer: "house",          // elder = leader of the civilization, anchored at its center
  farmer: "farm_plot", fisher: "farm_plot", gatherer: "farm_plot",
  miner: "workshop", blacksmith: "workshop", trader: "workshop",
  builder: ["house", "wall"],               // the builder constructs both housing and fortifications
  guard: "wall", scout: "wall", explorer: "wall"
};

// Resolve a role's default to a single project type (random pick when the role has several).
function roleDefaultProject(role) {
  const pref = ROLE_PROJECT[role] || "house";
  if (Array.isArray(pref)) return pref[Math.floor(Math.random() * pref.length)];
  return pref;
}
```

**Role intent:** the `elder` is the leader/king of the civilization — the main figure guiding it — so it
anchors the settlement with the House (its civic center) rather than doing defensive work. The `builder`
is the construction expert and handles **both** the House and the Wall. With the 8-agent roster (Part 4),
all four building types remain reachable as role *defaults*: House (elder, healer, builder), Farm Plot
(farmer, fisher, gatherer), Workshop (miner, trader), Wall (builder).

## Fix 1.1 — Redirect dead build/contribute to start_project (any role)

**File**: [`simulation/index.html`](simulation/index.html), `applyDecision`. Extract the project-creation
block from the `start_project` case into a helper. **Drop the builder/elder restriction**; pick the project
type from the explicit `target` if given, otherwise from the agent's role bias:

```js
function startProjectFor(agent, target) {
  if (civilization.activeProject) return null;            // idempotent: never double-start
  // Explicit target wins; otherwise the AGENT'S ROLE picks the type (NOT the PROJECT_ORDER cycle).
  // Note: do NOT use `pickProjectType(target) || ...` — pickProjectType never returns falsy
  // (it falls back to the PROJECT_ORDER cycle), so the role bias would be dead code.
  const type = (target && civilization.projectRegistry[target])
    ? target
    : roleDefaultProject(agent.role);
  const tmpl = civilization.projectRegistry[type];
  const contributed = {};
  for (const res of Object.keys(tmpl.needs)) contributed[res] = 0;
  civilization.activeProject = {
    type, name: tmpl.name, needs: { ...tmpl.needs },
    contributed, visualStyle: tmpl.visualStyle || "generic"
  };
  sidebarDirty = true;
  return `${agent.name} started ${tmpl.name} project`;
}
```

- `start_project` case ([index.html:941](simulation/index.html:941)): remove the
  `agent.role === "builder" || agent.role === "elder"` gate so any role can start.
- `build_structure` case ([index.html:981](simulation/index.html:981)): remove the `agent.role === "builder"`
  gate so any role can complete a fully-funded build; when there is **no** active project, call
  `startProjectFor(agent, decision.target)` instead of emitting `"has no project to build"`.
- `contribute_resources` case ([index.html:962](simulation/index.html:962)): when there is no active
  project, call `startProjectFor(...)` instead of `"has nothing to contribute"`.

All three call `startProjectFor(...)`, assign its return value to `summary`, and rely on the existing tail
log at [index.html:1107](simulation/index.html:1107) (see gotchas #1–#2 — do not add inline `pushActivity`).
Because the helper returns `null` if a project already exists, concurrent ticks (Part 3) can't double-start;
when it returns `null`, fall back to a `"could not start a project"` summary.

## Fix 1.2 — Start_project nudge for any role (backstop)

**File**: [`simulation/index.html`](simulation/index.html), `thinkAgent`
([index.html:1152](simulation/index.html:1152)). Nudge whichever agent is thinking when no project exists:

```js
let behaviorNudge = "";
if (!civilization.activeProject) {
  behaviorNudge = "NOTE: No active project exists. Use start_project now to begin a build.";
} else if (agent.consecutiveTalks >= 2) {
  behaviorNudge = "NOTE: You have chatted twice. Prioritize collect_resource, contribute_resources, or move_to_agent.";
}
```

## Fix 1.3 / 1.4 — All-role start/build in the fallbacks (client + server)

Update both fallback functions so **every role** starts a project when none is active (using its
`ROLE_PROJECT` bias), instead of only builder/elder:

- Client `roleFallbackAction` ([index.html:611](simulation/index.html:611)): in each role branch, when
  `!civilization.activeProject`, return `start_project` with `target = roleDefaultProject(agent.role)`.
  Otherwise keep the existing gather/contribute/move behavior.
- Server `role_fallback_action` ([server.py:336](simulation/server.py:336)): mirror the same logic — when
  `not has_project`, return `start_project` with a role-appropriate target (replicate a small role→type
  dict in Python so client and server agree).

## Fix 1.5 — Loosen the prompt to match (consistency)

**File**: [`simulation/server.py`](simulation/server.py), `SYSTEM_PROMPT` rule 5
([server.py:92](simulation/server.py:92)). Change *"Builders start projects when none active"* to
*"Any agent may start_project when none is active; everyone contributes and builds as resources allow."*
so the prompt no longer implies only builders start projects.

The first house costs only `{ wood: 3, food: 1 }` ([index.html:243](simulation/index.html:243)), so once a
project starts, existing collect/contribute behavior funds and builds it with no economy changes.

## Fix 1.6 — Elder leadership (the elder is the civilization's leader)

Opening start/build to every role (Fixes 1.1–1.5) makes all agents equal builders. To keep the **elder
(Sage) as the singular leader/king of the civilization**, give that one role authority the others lack.
There is exactly one elder, so "leader" is well-defined.

**A. Blueprint authority becomes elder-only.** Today both elder *and* builder may approve/reject new
structure blueprints. Make the elder the sole decision-maker on what the civilization adopts:

- Client: `approve_blueprint` reviewer check ([index.html:1040](simulation/index.html:1040)) and
  `reject_blueprint` ([index.html:1071](simulation/index.html:1071)) — change
  `agent.role === "elder" || agent.role === "builder"` to `agent.role === "elder"`.
- Server: the elder/builder pending-review fallback ([server.py:343](simulation/server.py:343)) and the
  approve/reject guard ([server.py:439](simulation/server.py:439)) — change `("elder", "builder")` to
  `("elder",)`.
- Prompt: rule 7 ([server.py:98](simulation/server.py:98)) — change *"Only an elder or builder may
  approve_blueprint or reject_blueprint"* to *"Only the elder may approve_blueprint or reject_blueprint."*

Anyone may still *propose* a blueprint; only the elder ratifies it. (Builders keep full build power from
Fixes 1.1/1.3 — they simply no longer ratify blueprints.)

**B. The elder sets a civilization directive that others follow.** Add a shared directive the leader can
broadcast so the village moves in one direction instead of 8 independent agendas:

- Add `civilization.directive = null` to the civilization object ([index.html:284](simulation/index.html:284)).
- Set it **deterministically** (preferred — see gotcha #4): whenever the elder starts a project or issues an
  `assign_task`, derive a short directive like `"Elder Sage directs: build the Workshop; gather wood and
  gold."`, store it on `civilization.directive`, and let it log via the normal `summary`/tail path (gotcha
  #1). Authoring it as a free-text LLM field is optional and requires adding `directive` to the prompt
  schema block.
- Surface it to every other agent: include `directive` in the `/agent/think` payload
  ([index.html:1157](simulation/index.html:1157)) and the user prompt template
  ([server.py:146](simulation/server.py:146)) as `Civilization directive: {directive}`, and fold it into
  `behavior_nudge` for non-elder agents (e.g. `"Your leader directs: <directive>. Prioritize it."`). This
  is the mechanism that makes the elder actually *lead* rather than just hold a title.

**C. The leader acts more often.** Give the elder a shorter think interval so the civilization's direction
updates promptly. This lives in the `agents.forEach((a, i) => {...})` block
([index.html:422](simulation/index.html:422)) where `a.thinkInterval = 360 + i * 60` — note the loop
variable is `a` (the agent), **not** `def`. Override there: `if (a.role === "elder") a.thinkInterval = 240;`
(place it after the `360 + i * 60` line so it wins). Under the concurrency pool (Part 3), optionally let the
elder jump the queue when it has a pending think.

**D. Roster guarantee.** The elder (Sage) must always be in the active roster. It is already in the
8-agent set (Part 4); add an assertion/guard so any future roster override cannot drop the elder, since the
leadership mechanics above assume exactly one elder exists.

**E. MAIN RULE — the elder assigns tasks to idle agents.** The elder's primary leadership duty: on each of
its turns, check whether any agent is doing nothing, and if so, give that agent a task. This is the
headline behavior, not an afterthought.

1. **Define "doing nothing."** An agent is idle when its `lastAction` ([index.html:1099](simulation/index.html:1099))
   is `"rest"` or `null` (never acted), or it has been idle-wandering (`idleFrames` repeatedly resetting,
   [index.html:529](simulation/index.html:529)) without a productive action. Add a small helper
   `isIdle(agent)` capturing this. Exclude the elder itself.
2. **Surface idle agents to the elder.** When the thinking agent is the elder, compute
   `idleAgents = agents.filter(isIdle)` and include them in the `/agent/think` payload
   ([index.html:1157](simulation/index.html:1157)) as `idle_agents` (name + role each), plus add
   `Idle agents needing a task: {idle_agents}` to the prompt template ([server.py:146](simulation/server.py:146)).
3. **New action `assign_task`.** Add `"assign_task"` to `AVAILABLE_ACTIONS`
   ([index.html:360](simulation/index.html:360)) and handle it in `applyDecision`: `target` = the idle
   agent's name, `message` = the task (e.g. "gather wood", "contribute food to the Workshop"). On apply,
   set that agent's `assignedTask` field and assign `summary = \`Elder ${agent.name} tasked ${target}: ${message}\``
   (let the tail log it — gotcha #1). **Guard it (gotcha #3):** `assign_task` is a non-talk action, so it
   bypasses validation unless you add an explicit elder-only branch in both `normalizeDecision`
   ([index.html:673](simulation/index.html:673)) and server `normalize_decision`
   ([server.py:435](simulation/server.py:435), alongside the blueprint branches) that requires
   `role == "elder"` and a valid agent `target`, else falls back.
4. **Assigned agents obey.** Add `agent.assignedTask = null` to agent init
   ([index.html:412](simulation/index.html:412)). In `thinkAgent`, if an agent has an `assignedTask`, fold
   it into `behavior_nudge` first: `"Your leader assigned you: <task>. Do it now."` Clear `assignedTask`
   after the agent next acts on a productive action so tasks don't loop forever.
5. **Deterministic backstop.** In the elder's fallback branch (client `roleFallbackAction`
   [index.html:657](simulation/index.html:657) and server `role_fallback_action`
   [server.py:395](simulation/server.py:395)), if there are idle agents, return `assign_task` targeting the
   first idle agent with a role-appropriate task — so the elder assigns work even when the LLM doesn't pick
   `assign_task` itself.
6. **Prompt main rule.** Add to `SYSTEM_PROMPT` ([server.py:86](simulation/server.py:86)) a top-level rule:
   *"MAIN RULE (elder only): on every turn, if any agent is idle, use assign_task to give that agent a
   specific job. The elder leads by keeping everyone busy."*

This keeps the "everyone can build" change while restoring a clear hierarchy: the elder ratifies new
structure types, sets the village's current goal, and — as its main duty — keeps every agent working by
assigning tasks to anyone who falls idle.

---

# Part 2 — GUI improvements (P1–P2)

Reference: ISSUES.md §4. Goal: a readable scene where you can tell who is who, see build progress, and
read the panels without the layout fighting you.

## Fix 2.1 — Depth sorting (highest GUI impact)

**File**: [`simulation/index.html`](simulation/index.html), main render loop
([index.html:1313](simulation/index.html:1313)). Agents are drawn in array order, so later agents always
paint over earlier ones. Sort by `y` (and draw structures interleaved or before by `y`) so nearer
entities paint last:

```js
const drawList = [...agents].sort((a, b) => a.y - b.y);
for (const agent of drawList) drawAgent(ctx, agent, frameTick);
```

Also draw built structures with a soft ground shadow so agents don't look like they float over flat tiles.

## Fix 2.2 — Annotation stacking + thinking indicator

**File**: [`simulation/index.html`](simulation/index.html), `drawAgent`
([index.html:741](simulation/index.html:741)) and `drawSpeechBubble`
([index.html:719](simulation/index.html:719)). Today the role badge (`y-58`), speech bubble (`y-72`), name
(`y+30`), and resource dots (`y+36`) collide when agents cluster (talk radius 80px). Changes:

- When a speech bubble is showing, hide or shift the role badge so they don't share the `y-58..y-72` band.
- Combine the role badge into the name label (e.g. `B·Zara`) to cut one floating element.
- Draw a background plate behind names, or only render the name on hover/selection, to reduce clutter when
  many agents overlap.
- Render the `isThinking` flag (set [index.html:1149](simulation/index.html:1149), cleared
  [index.html:1217](simulation/index.html:1217)) — e.g. a small "…" bubble or pulsing dot — so an agent
  waiting on a slow LLM call doesn't read as idle.

## Fix 2.3 — Zone labels + structure collisions

**File**: [`simulation/sprites.js`](simulation/sprites.js). Only the market is labeled
([sprites.js:911](simulation/sprites.js:911)), and with poor contrast. Add high-contrast labels (text with
a dark outline/plate) for **farm, forest, village, beach, cave** at their zone centers. Move or remove the
static decorative houses ([sprites.js:906](simulation/sprites.js:906)) so they don't overlap
player-built structures and look like rendering glitches; if kept, place them away from `findStructureSpot`
output.

## Fix 2.4 — Layout, DPI, and panels

**File**: [`simulation/index.html`](simulation/index.html). The page is locked to `width: 1280px`
([index.html:15](simulation/index.html:15)) with no viewport meta and no high-DPI handling, so it overflows
small windows and looks soft on Retina. Changes:

- Add `<meta name="viewport" content="width=device-width, initial-scale=1">` and scale the canvas backing
  store by `devicePixelRatio` (set canvas `width/height` to CSS size × DPR, then `ctx.scale(dpr, dpr)`).
- Make `#wrap` fluid (max-width instead of fixed 1280px) so it doesn't overflow narrow windows.
- Enlarge the agent list (currently ~140px for 12 agents) and the activity log (~70px) so more is visible.
- **Conversation panel**: today `convListEl.innerHTML = ...` ([index.html:1285](simulation/index.html:1285))
  rebuilds the whole list each update, losing scroll position. Append only new entries (or diff) and
  preserve scroll so the log doesn't jump while reading.
- **Resources stat**: `civResourcesEl` shows the count of registered resource *types*
  ([index.html:1263](simulation/index.html:1263)), which is misleading. Show village totals (sum of agent
  inventories, or a chosen aggregate) instead, and relabel if needed.

## Fix 2.5 — Sprite distinctiveness (optional, P3, higher effort)

All 12 agents share one silhouette differing only by palette ([sprites.js:378](simulation/sprites.js:378))
with a two-frame walk ([sprites.js:847](simulation/sprites.js:847)). Optional improvements: per-role
accessory/silhouette variation and a gather/build pose. Treat as stretch — depth sorting + labels (2.1–2.3)
deliver most of the readability win.

---

# Part 3 — LLM queue parallelism (P1)

Reference: ISSUES.md §2 item 6. The queue processes one decision at a time
([index.html:1124](simulation/index.html:1124)) behind a single `llmBusy` flag and a 1.5s floor
(`LLM_MIN_GAP_MS`, [index.html:771](simulation/index.html:771)), so with 12 agents each gets a turn only
every ~18–30s — starving the builder/elder of chances to act.

**File**: [`simulation/index.html`](simulation/index.html). Replace the single-flight drain with a bounded
concurrency pool:

```js
const MAX_CONCURRENT_LLM = 3;   // tune to LM Studio throughput
let llmInFlight = 0;

async function drainThinkQueue() {
  while (
    llmInFlight < MAX_CONCURRENT_LLM &&
    thinkQueue.length > 0 &&
    Date.now() >= llmCooldownUntil
  ) {
    const agent = thinkQueue.shift();
    agent.pendingThink = false;
    llmInFlight++;
    thinkAgent(agent).finally(() => {
      llmInFlight--;
      drainThinkQueue();
    });
  }
}
```

Notes:
- Keep `LLM_MIN_GAP_MS` as a small inter-dispatch stagger if desired, but it should no longer be the
  per-agent bottleneck. Drop the now-unused `llmBusy`/`lastLlmCallMs` single-flight gating.
- **Update the other `llmBusy` reference:** the frame loop calls `drainThinkQueue()` guarded by
  `if (!paused && thinkQueue.length > 0 && !llmBusy)` ([index.html:1335](simulation/index.html:1335)).
  Removing `llmBusy` breaks this line — change it to just `if (!paused && thinkQueue.length > 0) drainThinkQueue();`
  (the new `drainThinkQueue` already self-guards on `llmInFlight`). Grep for every `llmBusy` use before
  deleting the declaration ([index.html:764](simulation/index.html:764)).
- A local LM Studio instance has finite throughput; **start at `MAX_CONCURRENT_LLM = 3`** and adjust. If
  `compute_error` cooldowns spike, lower it.
- `thinkAgent` already mutates only its own agent plus shared `civilization`; the start-project path is
  now idempotent via `startProjectFor` (returns null if a project already exists). This matters more now
  that **any** role can start a project — concurrent ticks from multiple agents still can't double-start.
  Re-check other shared writes (e.g. blueprint approval) remain safe under concurrency.

---

# Part 4 — Roster size reduced to 8 (P3, design decision)

Reference: ISSUES.md §3. The default roster drops from 12 to **8 agents** to improve LLM throughput and
canvas readability. This is a deliberate deviation from `specs/04-agent-spec.md`, which states **"The 12
agents (exactly these — no more, no fewer)"**, so the spec must be updated as part of this change (see
reconciliation below) — this is not a silent override.

**File**: [`simulation/index.html`](simulation/index.html), `AGENT_DEFS`
([index.html:369](simulation/index.html:369)) and `makeAgents()` ([index.html:386](simulation/index.html:386)).
Make the roster a config value and default it to the 8-agent set:

```js
// Optional URL override (?agents=N); defaults to 8.
const _urlAgents = parseInt(new URLSearchParams(location.search).get("agents"), 10);
const ROSTER_SIZE = Number.isFinite(_urlAgents) ? _urlAgents : 8;
// The 8 active agents. Must include both project-starting roles (builder Zara, elder Sage).
const ROSTER = ["Zara", "Sage", "Aria", "Luna", "Marco", "Colt", "Finn", "Mia"];
//               builder elder  farmer gatherer trader miner  fisher healer
const activeDefs = ROSTER_SIZE >= AGENT_DEFS.length
  ? AGENT_DEFS
  : AGENT_DEFS.filter((d) => ROSTER.includes(d.name)).slice(0, ROSTER_SIZE);
```

**Wire it through, not just declare it:**
- `makeAgents()` currently iterates `AGENT_DEFS.map((def, i) => …)` ([index.html:387](simulation/index.html:387)).
  Change it to iterate **`activeDefs`** so only the active agents spawn.
- `AGENT_NAMES` is built from all of `AGENT_DEFS` ([index.html:384](simulation/index.html:384)). Rebuild it
  from `activeDefs` so talk/assign-task name validation only references agents that actually exist.
- The post-spawn `agents.forEach((a, i) => …)` ([index.html:422](simulation/index.html:422)) already
  iterates the spawned `agents`, so it needs no roster change beyond the elder override (Fix 1.6 C).

The chosen 8 keep both project-starting roles plus the core resource economy
(food via farmer/fisher, wood via gatherer, gold via miner) and coordination roles (trader, healer). The
dropped 4 are Rex (guard), Ivy (scout), Dex (blacksmith), and Nova (explorer) — none of which gather a
base resource or can start a project.

**Spec reconciliation (required):** update `specs/04-agent-spec.md` so the roster is defined as 8
(configurable, with the 8 names above) instead of "exactly these 12." Update any downstream references
(e.g. the `## The 8 agents` heading and the agent table) so the spec and code agree. Confirm the final
8-name list with the user before editing the spec.

---

## Validation checklist

**Progression (Part 1)**
1. Within the first minute, `logs/activity.jsonl` shows a `started ... project` event, then `contributed`
   events, then `built ... at village`.
2. `logs/lm_studio.jsonl` shows `start_project` in the `decision` field at least once.
3. At least one new structure renders on the canvas; `completedProjects` increments and Civ Level advances.

**GUI (Part 2)**
4. Overlapping agents resolve front-to-back (depth sorting); names/badges/bubbles no longer pile up.
5. The thinking indicator appears while an agent awaits the LLM.
6. Farm, forest, village, beach, and cave are clearly labeled; no static house overlaps a built structure.
7. Page fits a narrow window and looks crisp on Retina; conversation log keeps scroll position; the
   Resources stat reflects village totals.

**Throughput (Part 3)**
8. Multiple agents have decisions in flight at once (observe overlapping `latency_ms` windows in
   `lm_studio.jsonl`); per-agent cadence is visibly faster; no surge of `compute_error`.

**Elder leadership (Fix 1.6)**
9. Only the elder can approve/reject blueprints; the builder no longer can.
10. The elder sets a civilization directive that appears in `activity.jsonl` and in other agents' prompts.
11. When an agent goes idle (rests/wanders), the elder issues an `assign_task` to it (visible as
    `Elder Sage tasked <name>: <task>` in `activity.jsonl`), and that agent acts on the task next turn.

**Roster (Part 4)**
12. Default run spawns exactly 8 agents (Zara, Sage, Aria, Luna, Marco, Colt, Finn, Mia) — including
    builder and elder — and the world still builds. `specs/04-agent-spec.md` matches the new 8-agent roster.
    The elder cannot be dropped by a roster override.

**General**
13. No regressions: collect/move/talk still work; no browser console errors.

---

## Recommended models for implementing this plan in Cursor

The plan now spans precise progression edits, canvas rendering/layout work, an async concurrency change,
and a config refactor that touches a spec. It rewards strong multi-file reasoning, faithful adherence to
existing style, and care around concurrency correctness. Best-suited models, in order:

1. **Claude Opus 4.8** — best for the breadth + precision here: the `applyDecision` redirect and shared
   helper, the concurrency rewrite (avoiding double-start / shared-state races), and depth sorting without
   breaking existing draw order. Strongest at staying within scope across many small edits.
2. **Claude Sonnet 4.6** — nearly as accurate on focused diffs and faster/cheaper; a good primary for the
   GUI and roster parts, with Opus reserved for the concurrency change if you want extra assurance.
3. **GPT-5 (Cursor)** — strong code-editing alternative and a useful second opinion/cross-check,
   particularly on the queue-parallelism edge cases (in-flight accounting, cooldown interplay).

Avoid "mini"/"haiku"-class models: the change is moderate-volume but high-precision (concurrency races and
canvas draw-order regressions are easy to introduce), which favors a top-tier model over a cheap one.
