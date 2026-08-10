// =====================================================================
// Sidebar render — fills the Civilization / Agents panels and the
// Activity & Chat left panel from the snapshot.
// =====================================================================
const statusDot = document.getElementById("statusDot");
const statusLabel = document.getElementById("statusLabel");
const agentListEl = document.getElementById("agentList");
const agentRollupEl = document.getElementById("agentRollup");
const agentDetailEl = document.getElementById("agentDetail");
const livingAgentCountEl = document.getElementById("livingAgentCount");
const deadAgentsBtn = document.getElementById("deadAgentsBtn");
const deadAgentsModal = document.getElementById("deadAgentsModal");
const deadAgentsListEl = document.getElementById("deadAgentsList");
const deadAgentsCloseBtn = document.getElementById("deadAgentsCloseBtn");
let selectedAgentId = null;
let godFocusAgentId = null;
let hoveredAgentId = null;
let followAgentId = null;
const AGENT_HIT_RADIUS = 28;

// C5 follow-cam: center the scrollable canvas viewport on an agent, reusing the
// existing centerViewportOn() helper (defined near the minimap handlers) so the
// scroll/zoom math lives in exactly one place.
function centerCameraOnAgent(agent) {
  centerViewportOn(agent.x, agent.y);
}

function clientToWorld(clientX, clientY) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((clientX - rect.left) / rect.width) * WORLD_W,
    y: ((clientY - rect.top) / rect.height) * WORLD_H,
  };
}

function agentAtWorldPoint(wx, wy) {
  let best = null;
  let bestDist = AGENT_HIT_RADIUS;
  for (const a of getLivingAgents()) {
    const dx = a.x - wx;
    const dy = a.y - wy;
    const dist = Math.sqrt(dx * dx + dy * dy);
    if (dist < bestDist) {
      bestDist = dist;
      best = a;
    }
  }
  return best;
}

function structureAtWorldPoint(wx, wy) {
  const structures = (getCiv().structures || []).filter(
    (s) => !s.isRuin && (s.condition == null || s.condition > 0)
  );
  let best = null;
  let bestArea = Infinity;
  for (const s of structures) {
    const size = getStructureRenderSize(s, STRUCTURE_WEAR_ENABLED);
    const x0 = s.x;
    const y0 = s.y;
    const x1 = x0 + size.width;
    const y1 = y0 + size.height;
    if (wx >= x0 && wx <= x1 && wy >= y0 && wy <= y1) {
      const area = size.width * size.height;
      if (area < bestArea) {
        bestArea = area;
        best = s;
      }
    }
  }
  return best;
}

function godSetStructureTargetFromCanvas(structureId) {
  const idStr = String(structureId);
  if (godActiveTab === "miracles") {
    const sel = document.getElementById("godStructureSelect");
    if (sel && Array.from(sel.options).some((o) => o.value === idStr)) {
      sel.value = idStr;
      sel.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return;
  }
  if (godActiveTab === "story") {
    document.querySelectorAll(".godPrimStructure").forEach((sel) => {
      if (Array.from(sel.options).some((o) => o.value === idStr)) sel.value = idStr;
    });
    const fieldset = document.getElementById("godStoryFieldset");
    if (fieldset) fieldset.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

function isAgentInventoryVisible(agent) {
  const id = agent.id;
  return (hoveredAgentId != null && hoveredAgentId === id)
    || (selectedAgentId != null && selectedAgentId === id);
}

function syncAgentListSelection() {
  agentListEl.querySelectorAll("li.agent-row").forEach((li) => {
    li.classList.toggle("agent-selected", Number(li.dataset.agentId) === selectedAgentId);
  });
}
const convListEl = document.getElementById("convList");
const actListEl = document.getElementById("actList");
const chronicleLogEl = document.getElementById("chronicleLog");
const chronicleListEl = document.getElementById("chronicleList");
const timeNowEl = document.getElementById("timeNow");
const timeUptimeEl = document.getElementById("timeUptime");
const timeCalendarEl = document.getElementById("timeCalendar");
const worldClockHudEl = document.getElementById("worldClockHud");
const civLevelEl = document.getElementById("civLevel");
const civStructuresEl = document.getElementById("civStructures");
const civUpgradesRowEl = document.getElementById("civUpgradesRow");
const civUpgradesEl = document.getElementById("civUpgrades");
const civProjectCountEl = document.getElementById("civProjectCount");
const civProjectsListEl = document.getElementById("civProjectsList");
const civResourcesEl = document.getElementById("civResources");
const civResourceListEl = document.getElementById("civResourceList");
const civCustomBuildsEl = document.getElementById("civCustomBuilds");
const civCustomListEl = document.getElementById("civCustomList");
const civRecipeRow = document.getElementById("civRecipeRow");
const civRecipeCountEl = document.getElementById("civRecipeCount");
const civRecipeListEl = document.getElementById("civRecipeList");
const civCollectRateEl = document.getElementById("civCollectRate");
const civRulesRow = document.getElementById("civRulesRow");
const civRuleCountEl = document.getElementById("civRuleCount");
const civRuleListEl = document.getElementById("civRuleList");
const civBenchRow = document.getElementById("civBenchRow");
const civBenchEl = document.getElementById("civBench");
const civPendingRow = document.getElementById("civPendingRow");
const civPendingEl = document.getElementById("civPending");
const civProjectSprite = document.getElementById("civProjectSprite");
const civProjectSpriteCtx = civProjectSprite.getContext("2d");
const civLevelLabelEl = document.getElementById("civLevelLabel");
const councilBannerEl = document.getElementById("councilBanner");
const foundingBannerEl = document.getElementById("foundingBanner");
const disasterBannerEl = document.getElementById("disasterBanner");
const councilSectionEl = document.getElementById("councilSection");
const councilMetaEl = document.getElementById("councilMeta");
const councilCardsEl = document.getElementById("councilCards");
const councilHistoryEl = document.getElementById("councilHistory");

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Base project set used to distinguish "custom builds" (registry entries beyond
// the seed set) for the sidebar count, matching the old client behavior.
const BASE_PROJECT_IDS = new Set(["house", "farm_plot", "workshop", "wall", "granary"]);

function customProjectIds() {
  const reg = getCiv().projectRegistry || {};
  return Object.keys(reg).filter((id) => !BASE_PROJECT_IDS.has(id));
}

function totalVillageResources() {
  const breakdown = villageResourceBreakdown();
  return Object.values(breakdown).reduce((sum, n) => sum + n, 0);
}

function villageResourceBreakdown() {
  const reg = resourceRegistry();
  const stockpile = getCiv().stockpile || {};
  const out = {};
  for (const key of Object.keys(reg)) {
    let n = (stockpile[key] || 0);
    for (const agent of getAgents()) n += (agent.resources || {})[key] || 0;
    if (n > 0) out[key] = n;
  }
  return out;
}

function renderResourceChips(breakdown) {
  const reg = resourceRegistry();
  const keys = Object.keys(breakdown);
  if (keys.length === 0) return '<span class="civ-label">none</span>';
  return keys.map((key) => {
    const color = (reg[key] || {}).color || "#BDBDBD";
    return `<span class="res-chip"><span class="swatch" style="background:${color}"></span>${escapeHtml(key)} ${breakdown[key]}</span>`;
  }).join("");
}

const ACTION_LABELS = {
  collect_resource: "gathering",
  contribute_resources: "contributing",
  build_structure: "building",
  start_project: "starting a project",
  repair_structure: "repairing",
  upgrade_structure: "upgrading",
  submit_structure_sprite: "designing sprite",
  talk_to_nearby: "talking",
  found_belief: "founding a belief",
  trade_resource: "trading",
  propose_blueprint: "proposing a blueprint",
  approve_blueprint: "reviewing a blueprint",
  reject_blueprint: "reviewing a blueprint",
  assign_task: "assigning tasks",
  change_role: "changing role",
  switch_role: "retraining",
  propose_role: "proposing a role",
  approve_role: "reviewing a role",
  reject_role: "reviewing a role",
  propose_rule: "proposing a rule",
  vote_rule: "voting",
  repeal_rule: "proposing a repeal",
  move_to_agent: "approaching someone",
  heal_agent: "healing",
  craft_item: "crafting",
  propose_recipe: "proposing a recipe",
  approve_recipe: "reviewing a recipe",
  reject_recipe: "reviewing a recipe",
  bury_agent: "burying the dead",
  place_block: "placing a block",
  remove_block: "removing a block",
  dig_terrain: "digging terrain",
  plant_terrain: "planting terrain",
  propose_treaty: "proposing a treaty",
  vote_treaty: "voting on a treaty",
  deliver_caravan: "running a caravan",
  council_speak: "speaking at council",
  council_propose: "proposing at council",
  council_vote: "voting at council",
  hunt_wildlife: "hunting",
  confront_agent: "confronting",
  offer_contract: "offering a contract",
  accept_contract: "accepting a contract",
  rest: "resting"
};
function humanizeAction(agent) {
  if (agent.deceased) return agent.buried ? "dead · buried" : "dead · unburied";
  if (agent.incapacitated) return "collapsed";
  if (agent.isThinking) return "thinking…";
  const a = agent.lastAction;
  if (!a) return "idle";
  return actionLabel(a);
}

// Shared with humanizeAction's action-name mapping (excludes the
// deceased/incapacitated/thinking special-cases, which don't apply to
// historical log entries).
function actionLabel(actionName) {
  if (!actionName) return "idle";
  if (actionName.startsWith("move_to_")) return "heading to the " + actionName.replace("move_to_", "");
  return ACTION_LABELS[actionName] || actionName.replace(/_/g, " ");
}

function renderBenchmarks() {
  const b = world.benchmarks;
  if (!b || Object.keys(b).length === 0) {
    return '<span class="civ-label">sampling…</span>';
  }
  const r2 = (n) => (n == null ? "0" : Math.round(n * 100) / 100);
  const MEME_SHORT_NAMES = {
    harvest_spirit: "Harvest",
    river_spirit: "River",
  };
  const renderGroup = (label, chips) => {
    if (!chips.length) return "";
    return `<div class="bench-group"><span class="bench-group-label">${label}</span>${chips.join("")}</div>`;
  };
  const culture = [];
  const governance = [];
  const systems = [];
  culture.push(`<span class="res-chip">entropy <span class="civ-value">${r2(b.entropy)}</span></span>`);
  if (MEMES_ENABLED) {
    const byMeme = b.adoptionByMeme || {};
    const memeIds = Object.keys(byMeme);
    if (memeIds.length) {
      memeIds.forEach((mid) => {
        const short = MEME_SHORT_NAMES[mid] || mid;
        culture.push(`<span class="res-chip">${escapeHtml(short)} <span class="civ-value">${byMeme[mid]}/${getLivingAgents().length}</span></span>`);
      });
    } else {
      culture.push(`<span class="res-chip">meme adopt <span class="civ-value">${b.adoption}/${getLivingAgents().length}</span></span>`);
    }
  }
  if (b.adherence !== null && b.adherence !== undefined) {
    governance.push(`<span class="res-chip">tax <span class="civ-value">${Math.round(b.adherence * 100)}%</span></span>`);
  }
  if (RULES_ENABLED) {
    governance.push(`<span class="res-chip">rules <span class="civ-value">${b.rules}</span></span>`);
    if (b.ruleKindDiversity != null) {
      governance.push(`<span class="res-chip">rule kinds <span class="civ-value">${b.ruleKindDiversity}</span></span>`);
    }
  }
  if (MEMORY_ENABLED) {
    systems.push(`<span class="res-chip">memory <span class="civ-value">${b.memory}</span></span>`);
  }
  if (b.effectThroughput != null && b.effectThroughput !== undefined) {
    systems.push(`<span class="res-chip">effects <span class="civ-value">${b.effectThroughput}</span></span>`);
  }
  if (b.ecologyScarcity != null && b.ecologyScarcity !== undefined) {
    systems.push(`<span class="res-chip">ecology <span class="civ-value">${Math.round(b.ecologyScarcity * 100)}%</span></span>`);
  }
  if (PIANO_MODULES || META_SYSTEM) {
    systems.push(`<span class="res-chip">modules <span class="civ-value">${b.moduleTotal}</span></span>`);
  }
  const html = renderGroup("Culture", culture)
    + renderGroup("Governance", governance)
    + renderGroup("Systems", systems);
  return html || '<span class="civ-label">sampling…</span>';
}

// Draw one active project's structure sprite into the civ-panel mini-canvas
// (the first active district build, if any), reusing the same grid/fallback
// helpers as the in-world drawStructure().
function renderProjectSprite(p) {
  const c = civProjectSpriteCtx;
  c.clearRect(0, 0, civProjectSprite.width, civProjectSprite.height);
  if (!p) {
    civProjectSprite.style.display = "none";
    return;
  }
  civProjectSprite.style.display = "block";
  const fake = { type: p.type, visualStyle: p.visualStyle, name: p.name };
  const grid = getStructureGrid(fake);
  if (grid) {
    const gw = grid.reduce((m, row) => Math.max(m, row.length), 0);
    const gh = grid.length;
    const scale = Math.max(1, Math.floor(Math.min(civProjectSprite.width / gw, civProjectSprite.height / gh)));
    const ox = Math.round((civProjectSprite.width - gw * scale) / 2);
    const oy = Math.round((civProjectSprite.height - gh * scale) / 2);
    drawPixelGrid(c, ox, oy, grid, scale, false);
  } else {
    drawGenericStructure(c, 8, 8, p.name || p.type, colorFromId(p.type));
  }
}

// Renders the "Active builds" list -- one row per district with a non-null
// project (the one required sidebar behavioral change of the world-expansion
// plan: districtProjects, plural, replaces the old singular activeProject).
function renderProjectsList() {
  const projects = getCiv().districtProjects || {};
  const entries = Object.entries(projects).filter(([, p]) => p);
  civProjectCountEl.textContent = entries.length;
  if (entries.length === 0) {
    civProjectsListEl.innerHTML = '<div class="civ-row"><span class="civ-label">none</span></div>';
  } else {
    civProjectsListEl.innerHTML = entries.map(([districtId, p]) => {
      const pct = p.progressPercent != null ? p.progressPercent : 0;
      return `<div class="civ-row civ-project-list-item">` +
        `<span class="civ-value">${escapeHtml(p.name)}</span> ` +
        `<span class="civ-label">in ${escapeHtml(districtId)} — ${escapeHtml(p.progressText || "")} (${pct}%)</span>` +
        `<div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>` +
        `</div>`;
    }).join("");
  }
  renderProjectSprite(entries.length ? entries[0][1] : null);
}

// Cheap change-detection so we don't rebuild innerHTML every frame.
let lastSidebarKey = "";
let lastAgentPanelKey = "";
let lastAgentDetailKey = "";
let lastLogKey = "";
let lastChronicleKey = "";
let foundingFramesSeen = null; // null until first snapshot processed (see founding-banner logic)
let foundingBannerTimer = null;
let disasterFramesSeen = null;
let disasterBannerTimer = null;
const prevStructureCondition = {}; // structure id -> { condition, isRuin }
const structureFlashUntil = {}; // structure id -> wall-clock ms deadline
const STRUCTURE_HIT_FLASH_MIN_DROP = 5; // ignore passive decay (~0.05/tick); catch disasters (40–70)

// Client-only diff of structure condition/isRuin between polls; flash on meaningful
// damage or a new ruin transition — not every passive decay tick.
function trackStructureConditionDeltas() {
  if (!STRUCTURE_WEAR_ENABLED) return;
  const structures = getCiv().structures || [];
  const liveIds = new Set();
  for (const s of structures) {
    if (s.id == null) continue;
    liveIds.add(s.id);
    const cond = s.condition != null ? Number(s.condition) : 100;
    const ruin = !!s.isRuin;
    const prev = prevStructureCondition[s.id];
    if (prev && ((prev.condition - cond) >= STRUCTURE_HIT_FLASH_MIN_DROP || (ruin && !prev.isRuin))) {
      structureFlashUntil[s.id] = Date.now() + 800;
    }
    prevStructureCondition[s.id] = { condition: cond, isRuin: ruin };
  }
  for (const id of Object.keys(prevStructureCondition)) {
    if (!liveIds.has(Number(id))) {
      delete prevStructureCondition[id];
      delete structureFlashUntil[id];
    }
  }
}

function isStructureHitFlashing(id) {
  if (id == null) return false;
  const until = structureFlashUntil[id];
  if (!until) return false;
  if (Date.now() > until) {
    delete structureFlashUntil[id];
    return false;
  }
  return true;
}

// --- Client-only per-agent activity history (Plan B1: no server endpoint,
// resets on reload). Newest entry is last in the array; capped per agent. ---
const AGENT_HISTORY_LIMIT = 8;
const agentHistory = new Map(); // agentId -> [{ action, label }]

function recordAgentHistory() {
  const living = getLivingAgents();
  const seenIds = new Set();
  for (const a of living) {
    seenIds.add(a.id);
    const action = a.lastAction || null;
    let buf = agentHistory.get(a.id);
    if (!buf) {
      buf = [];
      agentHistory.set(a.id, buf);
    }
    const last = buf.length ? buf[buf.length - 1] : null;
    if (!last || last.action !== action) {
      buf.push({ action, label: humanizeAction(a) });
      if (buf.length > AGENT_HISTORY_LIMIT) buf.shift();
    }
  }
  // Prune buffers for agents that no longer exist in the current world.
  for (const id of Array.from(agentHistory.keys())) {
    if (!seenIds.has(id)) agentHistory.delete(id);
  }
}

function agentAgeLabel(a) {
  if (a.age == null || Number.isNaN(Number(a.age))) return "";
  return ` · age ${Math.round(Number(a.age))}`;
}

function agentVitalsHtml(a) {
  if (!SURVIVAL_ENABLED) return "";
  if (a.deceased) {
    return ` <span class="agent-vitals agent-dead">${a.buried ? "† buried" : "† unburied"}</span>`;
  }
  if (a.incapacitated) {
    return ` <span class="agent-vitals">☠ collapsed</span>`;
  }
  const health = Math.round(a.health);
  const hunger = Math.round(a.hunger);
  const healthColor = health >= 60 ? "#5cb85c" : health >= 30 ? "#e0a63f" : "#d9534f";
  // hunger is 0-100 where HIGHER = HUNGRIER = worse, so the bar fills (and
  // reddens) as hunger rises — it reads as a "danger" meter, not a "fullness" one.
  const hungerColor = hunger <= 40 ? "#5cb85c" : hunger <= 70 ? "#e0a63f" : "#d9534f";
  return ` <span class="agent-vitals">` +
    `<span class="vital-row" title="Health ${health}">❤<span class="vital-bar"><span class="vital-fill" style="width:${health}%;background:${healthColor}"></span></span></span>` +
    `<span class="vital-row" title="Hunger ${hunger} (higher = hungrier)">🍗<span class="vital-bar"><span class="vital-fill" style="width:${hunger}%;background:${hungerColor}"></span></span></span>` +
    `</span>`;
}

// Truncated last-thought line for the main list row (replaces the old
// per-agent beliefs line — see docs/archive/plan-agents-panel-3-*.md step 4b).
// Intentionally no "feeling" line: the decision `feeling` field is ~0%
// populated in practice, so showing it would be an empty column.
const AGENT_THOUGHT_TRUNCATE = 60;
function agentThoughtHtml(a) {
  if (a.isThinking) return `<span class="agent-thought">thinking…</span>`;
  const reasoning = a.lastReasoning;
  if (!reasoning) return `<span class="agent-thought muted">—</span>`;
  const truncated = reasoning.length > AGENT_THOUGHT_TRUNCATE
    ? reasoning.slice(0, AGENT_THOUGHT_TRUNCATE) + "…"
    : reasoning;
  return `<span class="agent-thought">${escapeHtml(truncated)}</span>`;
}

// Lower score = more urgent. Incapacitated agents always float to the top;
// otherwise rank by low health / high hunger.
function agentSeverityScore(a) {
  if (a.incapacitated) return -1000;
  const health = Number(a.health);
  const hunger = Number(a.hunger);
  const healthPenalty = Number.isFinite(health) ? (100 - health) : 0;
  const hungerPenalty = Number.isFinite(hunger) ? hunger : 0;
  return -(healthPenalty + hungerPenalty);
}

function isAgentCritical(a) {
  if (!SURVIVAL_ENABLED) return false;
  if (a.incapacitated) return true;
  const health = Number(a.health);
  const hunger = Number(a.hunger);
  return (Number.isFinite(health) && health < 30) || (Number.isFinite(hunger) && hunger > 85);
}

function renderDeceasedAgentRow(a) {
  const burial = a.buried ? "buried in the Cemetery" : "awaiting burial";
  return `<li><span class="dot" style="background:${a.color}"></span>` +
    `<span><span class="agent-main">${escapeHtml(a.name)} — ${escapeHtml(a.role)}${escapeHtml(agentAgeLabel(a))}</span>` +
    `<span class="agent-dead-detail">dead · ${burial}</span></span></li>`;
}

// Renders the inventory + activity-history detail panel for the currently
// selected agent (or clears it when nothing is selected). Kept separate from
// renderAgentPanel's own change-detection so selecting an agent (or new
// history entries) always refreshes it, even when the roster itself hasn't
// changed and renderAgentPanel's `lastAgentPanelKey` early-return fires.
function renderAgentDetail() {
  if (selectedAgentId == null) {
    if (lastAgentDetailKey !== "") {
      agentDetailEl.classList.remove("open");
      agentDetailEl.innerHTML = "";
      lastAgentDetailKey = "";
    }
    return;
  }
  const agent = getLivingAgents().find((a) => a.id === selectedAgentId);
  if (!agent) {
    if (lastAgentDetailKey !== "") {
      agentDetailEl.classList.remove("open");
      agentDetailEl.innerHTML = "";
      lastAgentDetailKey = "";
    }
    return;
  }
  const history = agentHistory.get(agent.id) || [];
  const detailKey = JSON.stringify({
    id: agent.id, resources: agent.resources || {},
    history: history.map((h) => h.label),
    relationships: agent.relationships || {},
    lastReasoning: agent.lastReasoning, isThinking: agent.isThinking,
    following: followAgentId === agent.id,
  });
  if (detailKey === lastAgentDetailKey) return;
  lastAgentDetailKey = detailKey;

  const resources = agent.resources || {};
  const breakdown = {};
  for (const key of Object.keys(resources)) {
    if (resources[key] > 0) breakdown[key] = resources[key];
  }

  const historyHtml = history.length
    ? history.slice().reverse().map((h) => `<li>${escapeHtml(h.label)}</li>`).join("")
    : `<li class="civ-label">No recent activity yet</li>`;

  const relationships = agent.relationships || {};
  const tieNames = Object.keys(relationships);
  const relationshipsHtml = tieNames.length
    ? tieNames.map((name) => {
        const tie = relationships[name];
        const cls = tie === "ally" ? "ally" : tie === "rival" ? "rival" : "";
        return `<span class="agent-tie-chip ${cls}">${escapeHtml(name)} (${escapeHtml(tie)})</span>`;
      }).join("")
    : `<span class="civ-label">no notable ties</span>`;

  const reasoningHtml = agent.isThinking
    ? `<span class="agent-reasoning">thinking…</span>`
    : agent.lastReasoning
      ? `<span class="agent-reasoning">${escapeHtml(agent.lastReasoning)}</span>`
      : `<span class="civ-label">no reasoning recorded</span>`;

  const following = followAgentId === agent.id;

  agentDetailEl.classList.add("open");
  agentDetailEl.innerHTML =
    `<h3>${escapeHtml(agent.name)}` +
    `<button type="button" id="agentFollowBtn" class="${following ? "active" : ""}">${following ? "Following" : "Follow"}</button>` +
    `</h3>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Inventory</span>` +
    renderResourceChips(breakdown) +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Relationships</span>` +
    relationshipsHtml +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Last reasoning</span>` +
    reasoningHtml +
    `</div>` +
    `<div class="agent-detail-section">` +
    `<span class="agent-detail-label">Recent activity</span>` +
    `<ul class="agent-history">${historyHtml}</ul>` +
    `</div>`;

  const followBtn = document.getElementById("agentFollowBtn");
  if (followBtn) {
    followBtn.addEventListener("click", () => {
      followAgentId = followAgentId === agent.id ? null : agent.id;
      lastAgentDetailKey = ""; // force re-render to reflect pressed state
      renderAgentDetail();
    });
  }
}

// C4 roll-up header: counts by role, thinking, crisis, and avg hunger across
// living agents. Gated by its own key so it only touches the DOM on change.
let lastRollupKey = "";
function renderAgentRollup(living) {
  if (!living.length) {
    if (lastRollupKey !== "") {
      agentRollupEl.innerHTML = "";
      lastRollupKey = "";
    }
    return;
  }
  const byRole = {};
  let thinking = 0;
  let crisis = 0;
  let hungerTotal = 0;
  let hungerCount = 0;
  for (const a of living) {
    byRole[a.role] = (byRole[a.role] || 0) + 1;
    if (a.isThinking) thinking++;
    if (isAgentCritical(a)) crisis++;
    if (Number.isFinite(Number(a.hunger))) {
      hungerTotal += Number(a.hunger);
      hungerCount++;
    }
  }
  const avgHunger = hungerCount ? Math.round(hungerTotal / hungerCount) : null;
  const rollupKey = JSON.stringify({ byRole, thinking, crisis, avgHunger, n: living.length });
  if (rollupKey === lastRollupKey) return;
  lastRollupKey = rollupKey;

  const roleChips = Object.keys(byRole).sort().map((role) =>
    `<span class="rollup-chip">${escapeHtml(role)} ${byRole[role]}</span>`
  ).join("");
  const thinkingChip = `<span class="rollup-chip">thinking ${thinking}/${living.length}</span>`;
  const crisisChip = `<span class="rollup-chip${crisis ? " rollup-crisis" : ""}">crisis ${crisis}</span>`;
  const hungerChip = avgHunger != null ? `<span class="rollup-chip">avg hunger ${avgHunger}</span>` : "";
  agentRollupEl.innerHTML = roleChips + thinkingChip + crisisChip + hungerChip;
}

function renderAgentPanel() {
  if (selectedAgentId != null && !getLivingAgents().some((a) => a.id === selectedAgentId)) {
    selectedAgentId = null;
    godFocusAgentId = null;
  }
  recordAgentHistory();
  renderAgentDetail();
  const living = getLivingAgents();
  const deceased = getDeceasedAgents();
  const panelKey = JSON.stringify({
    living: living.map((a) => [a.id, a.name, a.role, a.age, a.incapacitated, a.health, a.hunger, a.lastAction, a.isThinking, a.lastReasoning]),
    deceased: deceased.map((a) => [a.id, a.name, a.role, a.buried, a.age]),
  });
  renderAgentRollup(living);
  if (panelKey === lastAgentPanelKey) return;
  lastAgentPanelKey = panelKey;

  livingAgentCountEl.textContent = living.length ? `(${living.length} living)` : "";
  if (deceased.length > 0) {
    deadAgentsBtn.hidden = false;
    deadAgentsBtn.textContent = `Deceased (${deceased.length})`;
  } else {
    deadAgentsBtn.hidden = true;
  }

  if (living.length === 0) {
    agentListEl.innerHTML = '<li><span class="civ-label">No living villagers</span></li>';
  } else {
    // Float agents in crisis to the top; stable sort via original-index tiebreak.
    const sortedLiving = living
      .map((a, index) => ({ a, index, score: agentSeverityScore(a) }))
      .sort((x, y) => (x.score - y.score) || (x.index - y.index))
      .map((entry) => entry.a);
    agentListEl.innerHTML = sortedLiving.map((a) => {
      const selected = selectedAgentId === a.id;
      const critical = isAgentCritical(a);
      return `<li class="agent-row${selected ? " agent-selected" : ""}${critical ? " agent-critical" : ""}" data-agent-id="${a.id}">` +
        `<span class="dot" style="background:${a.color}"></span>` +
        `<span><span class="agent-main">${escapeHtml(a.name)} — ${escapeHtml(a.role)}${escapeHtml(agentAgeLabel(a))}</span>` +
        `<span class="agent-status">${escapeHtml(humanizeAction(a))}${agentVitalsHtml(a)}</span>` +
        agentThoughtHtml(a) +
        `</span></li>`;
    }).join("");
  }

  if (deadAgentsModal.classList.contains("open")) {
    deadAgentsListEl.innerHTML = deceased.length
      ? deceased.map(renderDeceasedAgentRow).join("")
      : '<li><span class="civ-label">No deceased villagers</span></li>';
  }
}

function openDeadAgentsModal() {
  deadAgentsListEl.innerHTML = getDeceasedAgents().map(renderDeceasedAgentRow).join("")
    || '<li><span class="civ-label">No deceased villagers</span></li>';
  deadAgentsModal.classList.add("open");
}

function closeDeadAgentsModal() {
  deadAgentsModal.classList.remove("open");
}

deadAgentsBtn.addEventListener("click", openDeadAgentsModal);
deadAgentsCloseBtn.addEventListener("click", closeDeadAgentsModal);
agentListEl.addEventListener("click", (event) => {
  const li = event.target.closest("li.agent-row");
  if (!li) return;
  const id = Number(li.dataset.agentId);
  const wasSelected = selectedAgentId === id;
  selectedAgentId = wasSelected ? null : id;
  godFocusAgentId = selectedAgentId;
  if (wasSelected) {
    // Deselecting releases any active follow-cam lock on this agent.
    if (followAgentId === id) followAgentId = null;
  } else {
    // First-time selection: one-time jump-to-center on the agent.
    const agent = getLivingAgents().find((a) => a.id === id);
    if (agent) centerCameraOnAgent(agent);
  }
  syncAgentListSelection();
  renderAgentDetail();
});
canvasWrapEl.addEventListener("mousemove", (event) => {
  const { x, y } = clientToWorld(event.clientX, event.clientY);
  const hit = agentAtWorldPoint(x, y);
  hoveredAgentId = hit ? hit.id : null;
});
canvasWrapEl.addEventListener("mouseleave", () => {
  hoveredAgentId = null;
});
canvasWrapEl.addEventListener("click", (event) => {
  if (!GOD_MODE_ENABLED_FLAG) return;
  const { x, y } = clientToWorld(event.clientX, event.clientY);
  const agentHit = agentAtWorldPoint(x, y);
  if (agentHit) {
    selectedAgentId = agentHit.id;
    godFocusAgentId = agentHit.id;
    syncAgentListSelection();
    renderAgentDetail();
    centerCameraOnAgent(agentHit);
    populateGodAgentSelects();
    return;
  }
  const modalOpen = divineModalScrimEl && divineModalScrimEl.classList.contains("open");
  const structurePickMode = modalOpen && (godActiveTab === "miracles" || godActiveTab === "story");
  if (structurePickMode) {
    const structHit = structureAtWorldPoint(x, y);
    if (structHit) godSetStructureTargetFromCanvas(structHit.id);
  }
});
deadAgentsModal.addEventListener("click", (event) => {
  if (event.target === deadAgentsModal) closeDeadAgentsModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && deadAgentsModal.classList.contains("open")) {
    closeDeadAgentsModal();
  }
});

function formatEstClock(ms) {
  return new Date(ms).toLocaleTimeString("en-US", {
    timeZone: "America/New_York", hour: "numeric", minute: "2-digit", second: "2-digit",
  });
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return d > 0 ? `${d}d ${pad(h)}:${pad(m)}:${pad(s)}` : `${pad(h)}:${pad(m)}:${pad(s)}`;
}

setInterval(() => { timeNowEl.textContent = formatEstClock(Date.now()); }, 1000);

function renderSidebar() {
  const civ = getCiv();
  if (world.uptimeSeconds != null) timeUptimeEl.textContent = formatDuration(world.uptimeSeconds);
  const cal = world.calendar;
  if (cal) {
    const phase = cal.isNight ? "night" : "day";
    timeCalendarEl.textContent =
      `Year ${cal.year} · ${cal.season} · day ${cal.dayOfSeason}/${cal.daysPerSeason} · ${phase}`;
  }
  const lmStatus = world.lmStatus || "offline";
  const statusColor = lmStatus === "online" ? "#4CAF50"
    : lmStatus === "compute_error" ? "#FF9800" : "#F44336";
  const label = lmStatus === "online" ? "Ollama: online"
    : lmStatus === "compute_error" ? "Ollama: GPU memory error"
    : (lmStatus === "disconnected" ? "Server: disconnected" : "Ollama: offline");
  statusDot.style.background = lmStatus === "disconnected" ? "#9E9E9E" : statusColor;
  statusLabel.innerHTML = label + (lmStatus === "compute_error"
    ? '<span class="hint">Use a smaller model or Q4 quant</span>'
    : (lmStatus === "disconnected"
      ? '<span class="hint">Showing last frame; retrying /state…</span>'
      : ""));

  // Phase D: the era replaces the vanity level when the server sends one
  // (TECH_TREE_ENABLED); otherwise the classic level chip renders unchanged.
  if (civ.era) {
    civLevelLabelEl.textContent = "Era:";
    civLevelEl.textContent = civ.techTier ? `${civ.era} (tier ${civ.techTier})` : civ.era;
  } else {
    civLevelLabelEl.textContent = "Level:";
    civLevelEl.textContent = civ.level != null ? civ.level : 1;
  }
  civStructuresEl.textContent = (civ.structures || []).length;
  const upgradable = (civ.structures || []).filter((s) => !s.isRuin && (s.level == null || s.level < 100));
  if (STRUCTURE_UPGRADES_ENABLED && upgradable.length) {
    civUpgradesRowEl.style.display = "";
    const top = upgradable.slice().sort((a, b) => (b.level || 1) - (a.level || 1)).slice(0, 3);
    civUpgradesEl.textContent = `${upgradable.length} (${top.map((s) => `${s.name || s.type} Lv.${s.level || 1}`).join(", ")})`;
  } else {
    civUpgradesRowEl.style.display = "none";
  }
  renderProjectsList();

  // Phase D: "Council in session" banner, driven by civ.councilActive.
  const councilActive = civ.councilActive;
  if (councilActive && councilActive.active) {
    councilBannerEl.style.display = "block";
    councilBannerEl.textContent =
      `Council in session — ${councilActive.proposals || 0} proposal(s)` +
      (councilActive.proposers ? ` (${councilActive.proposers.join(", ")})` : "");
  } else {
    councilBannerEl.style.display = "none";
  }

  // --- Civilization detail rows (change-detected) ---
  const sidebarKey = JSON.stringify([
    // villageResourceBreakdown() is stockpile's proxy in this key (stockpile omitted — ~40 keys, changes every poll)
    civ.resourceRegistry, villageResourceBreakdown(), customProjectIds(),
    civ.recipes, civ.rules, civ.constitution, civ.pendingRules, civ.pendingBlueprints,
    world.benchmarks, civ.collectAttempts, civ.collectSuccesses,
    civ.councilLog, civ.era, civ.techTier,
    CRAFTING_ENABLED, RULES_ENABLED, BENCHMARKS_ENABLED, MEMES_ENABLED, MEMORY_ENABLED
  ]);
  if (sidebarKey !== lastSidebarKey) {
    lastSidebarKey = sidebarKey;

    civResourcesEl.textContent = totalVillageResources();
    civResourceListEl.innerHTML = renderResourceChips(villageResourceBreakdown());

    const customIds = customProjectIds();
    civCustomBuildsEl.textContent = customIds.length;
    civCustomListEl.innerHTML = customIds.map((id) =>
      `<span class="res-chip civ-value">${escapeHtml((civ.projectRegistry[id] || {}).name || id)}</span>`
    ).join("");

    civCollectRateEl.textContent = civ.collectAttempts > 0
      ? `${Math.round(100 * civ.collectSuccesses / civ.collectAttempts)}% (${civ.collectSuccesses}/${civ.collectAttempts})`
      : "—";

    // Recipes registry (server-supplied; gated by the crafting flag).
    const recipes = civ.recipes || {};
    if (CRAFTING_ENABLED && Object.keys(recipes).length > 0) {
      const recipeIds = Object.keys(recipes);
      civRecipeRow.style.display = "block";
      civRecipeCountEl.textContent = recipeIds.length;
      civRecipeListEl.innerHTML = recipeIds.map((id) => {
        const r = recipes[id];
        const ins = Object.entries(r.inputs || {}).map(([k, v]) => `${k}×${v}`).join("+");
        return `<span class="res-chip civ-value">${escapeHtml(r.name || id)} <span class="civ-label">(${escapeHtml(ins)})</span></span>`;
      }).join("");
    } else {
      civRecipeRow.style.display = "none";
    }

    const rules = civ.rules || [];
    const constitution = civ.constitution || [];
    const pendingRules = civ.pendingRules || [];
    if (RULES_ENABLED && (rules.length > 0 || constitution.length > 0 || pendingRules.length > 0)) {
      civRulesRow.style.display = "block";
      civRuleCountEl.textContent = rules.length;
      const enacted = (constitution.length ? constitution : rules).map((r) => {
        const status = r.status ? ` <span class="civ-label">[${escapeHtml(r.status)}]</span>` : "";
        const amendment = r.supersedes
          ? ` <span class="civ-label">→ ${escapeHtml(r.supersedes)}</span>` : "";
        return `<span class="res-chip civ-value">${escapeHtml(r.name || r.id)}${status}${amendment}</span>`;
      }
      ).join("");
      const pending = pendingRules.map((r) => {
        const votes = r.votes || {};
        const yes = Object.values(votes).filter((v) => v === "yes").length;
        const no = Object.values(votes).filter((v) => v === "no").length;
        const label = r.name || r.id;
        return `<span class="res-chip"><span class="civ-label">vote ${escapeHtml(label)} (${yes}/${no})</span></span>`;
      }).join("");
      civRuleListEl.innerHTML = enacted + pending;
    } else {
      civRulesRow.style.display = "none";
    }

    if (BENCHMARKS_ENABLED) {
      civBenchRow.style.display = "block";
      civBenchEl.innerHTML = renderBenchmarks();
    } else {
      civBenchRow.style.display = "none";
    }

    renderCouncil(civ);
    if (SHOW_SETTLEMENTS) {
      renderSettlements(civ);
    } else {
      settlementsSectionEl.style.display = "none";
    }

    const pendingBp = civ.pendingBlueprints || [];
    if (pendingBp.length > 0) {
      civPendingRow.style.display = "block";
      civPendingEl.innerHTML = pendingBp.slice(0, 3).map((b) =>
        `<div class="civ-value">${escapeHtml(b.name)} <span class="civ-label">by ${escapeHtml(b.proposedBy)}</span></div>`
      ).join("");
    } else {
      civPendingRow.style.display = "none";
    }
  }

  renderAgentPanel();

  // --- Activity & Chat panel (change-detected separately, higher churn) ---
  const conversation = world.conversation || [];
  const activity = world.activity || [];
  const logKey = JSON.stringify(SHOW_CONVERSATIONS ? [conversation, activity] : [activity]);
  if (logKey !== lastLogKey) {
    lastLogKey = logKey;
    if (SHOW_CONVERSATIONS) {
      const convScroll = convListEl.scrollTop;
      convListEl.innerHTML = conversation.map((c) => {
        const kindLabel = c.kind && c.kind !== "speech"
          ? `<span class="kind">[${escapeHtml(c.kind)}]</span> `
          : "";
        const msg = c.message
          ? `<span class="msg">${escapeHtml(c.message)}</span>`
          : (c.outcome ? `<span class="msg muted">(${escapeHtml(c.outcome)})</span>` : "");
        return `<li>${kindLabel}<span class="from">${escapeHtml(c.from)}</span> <span class="arrow">→</span> <span class="to">${escapeHtml(c.to)}</span>${msg ? ": " + msg : ""}</li>`;
      }).join("");
      convListEl.scrollTop = convScroll;
    }

    actListEl.innerHTML = activity.map((line) =>
      `<li>${escapeHtml(line)}</li>`
    ).join("");
  }

  if (!CHRONICLE_ENABLED) {
    chronicleLogEl.style.display = "none";
  } else {
    chronicleLogEl.style.display = "";
    const chronicle = world.chronicle || [];
    const chronicleKey = JSON.stringify(chronicle);
    if (chronicleKey !== lastChronicleKey) {
      lastChronicleKey = chronicleKey;
      const scrollTop = chronicleListEl.scrollTop;
      chronicleListEl.innerHTML = chronicle.slice().reverse().map((entry) => {
        const kind = String(entry.kind || "event").replace(/_/g, " ");
        const frame = entry.frame != null ? `frame ${entry.frame}` : "";
        return `<li><span class="chronicle-kind">${escapeHtml(kind)}</span> ` +
          `<span class="${entry.presentation === "thunder" ? "chronicle-presentation-thunder" : ""}">${escapeHtml(entry.text || "")}</span>` +
          (frame ? ` <span class="chronicle-frame">${escapeHtml(frame)}</span>` : "") +
          `</li>`;
      }).join("") || `<li class="civ-label">No village milestones yet</li>`;
      chronicleListEl.scrollTop = scrollTop;
    }
  }

  // Founding banner: derived by diffing newly-appeared "district_founded"
  // chronicle entries rather than a dedicated server field (foundedFrame).
  // The client already parses the full chronicle array every poll (see
  // chronicleKey above), so remembering which frames have already been
  // banner-ed needs no new /state shape and no extra server-side state --
  // the simpler option per the phase brief.
  if (FOUNDING_EVENTS_ENABLED) {
    const foundingEntries = (world.chronicle || []).filter((e) => e.kind === "district_founded");
    if (foundingFramesSeen === null) {
      // First snapshot after page load/refresh: remember whatever foundings
      // already happened without banner-ing them, so resuming a long-running
      // village doesn't replay its whole settlement history as banners.
      foundingFramesSeen = new Set(foundingEntries.map((e) => e.frame));
    } else {
      const fresh = foundingEntries.find((e) => !foundingFramesSeen.has(e.frame));
      if (fresh) {
        foundingFramesSeen.add(fresh.frame);
        foundingBannerEl.textContent = fresh.text;
        foundingBannerEl.style.display = "block";
        if (foundingBannerTimer) clearTimeout(foundingBannerTimer);
        foundingBannerTimer = setTimeout(() => {
          foundingBannerEl.style.display = "none";
          foundingBannerTimer = null;
        }, 6000);
      }
      // Chronicle is a capped ring (CHRONICLE_CAP) -- drop frames that have
      // aged out so this set doesn't grow unboundedly across a long session.
      for (const f of Array.from(foundingFramesSeen)) {
        if (!foundingEntries.some((e) => e.frame === f)) foundingFramesSeen.delete(f);
      }
    }
  } else {
    foundingBannerEl.style.display = "none";
  }

  // Disaster banner: edge-detect new chronicle entries with kind === "disaster".
  if (CHRONICLE_ENABLED) {
    const disasterEntries = (world.chronicle || []).filter((e) => e.kind === "disaster");
    if (disasterFramesSeen === null) {
      disasterFramesSeen = new Set(disasterEntries.map((e) => e.frame));
    } else {
      const fresh = disasterEntries.find((e) => !disasterFramesSeen.has(e.frame));
      if (fresh) {
        disasterFramesSeen.add(fresh.frame);
        disasterBannerEl.textContent = fresh.text;
        disasterBannerEl.style.display = "block";
        if (disasterBannerTimer) clearTimeout(disasterBannerTimer);
        disasterBannerTimer = setTimeout(() => {
          disasterBannerEl.style.display = "none";
          disasterBannerTimer = null;
        }, 5500);
      }
      for (const f of Array.from(disasterFramesSeen)) {
        if (!disasterEntries.some((e) => e.frame === f)) disasterFramesSeen.delete(f);
      }
    }
  } else {
    disasterBannerEl.style.display = "none";
  }

  trackStructureConditionDeltas();

  renderDivineConsole();
}

function calendarPhase(cal) {
  if (!cal) return null;
  const f = cal.dayFraction;
  if (f == null) return cal.isNight ? "night" : "day";
  const duskStart = TWILIGHT_START;
  const duskEnd = TWILIGHT_END_DUSK;
  const nightEnd = TWILIGHT_START_DAWN;
  if (f < duskStart) return "day";
  if (f < duskEnd) return "dusk";
  if (f < nightEnd) return "night";
  return "dawn";
}

function weatherStateLabel(weather) {
  if (!weather || !weather.state) return null;
  const labels = { clear: "Clear", gathering: "Gathering", storm: "Storm", clearing: "Clearing" };
  return labels[weather.state] || null;
}

function renderWorldClockHud() {
  if (!WORLD_CLOCK_HUD_ENABLED) {
    worldClockHudEl.style.display = "none";
    return;
  }
  const cal = world.calendar;
  const phase = calendarPhase(cal);
  if (!cal || !cal.season || !phase) {
    worldClockHudEl.style.display = "none";
    return;
  }
  worldClockHudEl.style.display = "block";
  let label = `${cal.season} ${phase}`;
  if (WEATHER_ENABLED && world.weather) {
    const wl = weatherStateLabel(world.weather);
    if (wl) label += ` · ${wl}`;
  }
  worldClockHudEl.textContent = label;
}

