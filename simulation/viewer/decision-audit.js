// =====================================================================
// Decision audit panel (idea-10): polls GET /decision-audit on its own
// cadence and renders server-provided per-agent aggregates — no client-side
// join, category classification, or scoring.
// =====================================================================
const DECISION_AUDIT_POLL_MS = 3000;

const decisionAuditPanelEl = document.getElementById("decisionAuditPanel");
const decisionAuditAgentListEl = document.getElementById("decisionAuditAgentList");
const decisionAuditRecentEl = document.getElementById("decisionAuditRecent");
const decisionAuditRecentWrapEl = document.getElementById("decisionAuditRecentWrap");

let lastDecisionAuditKey = "";
let decisionAuditPollTimer = null;

function stopDecisionAuditPoll() {
  if (decisionAuditPollTimer != null) {
    clearInterval(decisionAuditPollTimer);
    decisionAuditPollTimer = null;
  }
}

function truncateAuditText(text, maxLen) {
  const s = String(text || "");
  return s.length > maxLen ? `${s.slice(0, maxLen - 1)}…` : s;
}

function formatMismatchRate(rate) {
  if (rate == null || Number.isNaN(rate)) return "—";
  return `${Math.round(rate * 100)}%`;
}

function decisionAuditScoreBadge(score) {
  if (score === "match") {
    return '<span class="decision-audit-badge decision-audit-badge-match">match</span>';
  }
  if (score === "mismatch") {
    return '<span class="decision-audit-badge decision-audit-badge-mismatch">mismatch</span>';
  }
  return '<span class="decision-audit-badge decision-audit-badge-muted">—</span>';
}

function renderDecisionAuditPanel(data) {
  if (!decisionAuditPanelEl) return;
  if (!data || !data.enabled) {
    stopDecisionAuditPoll();
    decisionAuditPanelEl.style.display = "";
    decisionAuditAgentListEl.innerHTML =
      '<li class="decision-audit-empty">Decision audit disabled</li>';
    if (decisionAuditRecentWrapEl) decisionAuditRecentWrapEl.style.display = "none";
    lastDecisionAuditKey = "";
    return;
  }

  decisionAuditPanelEl.style.display = "";

  const agents = (data.agents || []).filter((a) => (a.scored || 0) >= 1);
  const recent = data.recent || [];
  const auditKey = JSON.stringify({ agents, recent });
  if (auditKey === lastDecisionAuditKey) return;
  lastDecisionAuditKey = auditKey;

  const agentScroll = decisionAuditAgentListEl.scrollTop;
  if (agents.length === 0) {
    decisionAuditAgentListEl.innerHTML =
      '<li class="decision-audit-empty">No scored decisions yet</li>';
  } else {
    decisionAuditAgentListEl.innerHTML = agents.map((agent) => {
      const name = escapeHtml(agent.agent_name || "—");
      const scored = agent.scored || 0;
      const matches = agent.matches || 0;
      const mismatches = agent.mismatches || 0;
      const rate = formatMismatchRate(agent.mismatch_rate);
      return `<li class="decision-audit-agent-row">` +
        `<span class="decision-audit-agent-name">${name}</span> ` +
        `<span class="decision-audit-agent-stats">` +
        `${scored} scored · ${matches}/${mismatches} match/mismatch · ` +
        `<span class="decision-audit-rate">${escapeHtml(rate)}</span> mismatch` +
        `</span></li>`;
    }).join("");
  }
  decisionAuditAgentListEl.scrollTop = agentScroll;

  if (decisionAuditRecentWrapEl) {
    decisionAuditRecentWrapEl.style.display = recent.length ? "" : "none";
  }

  const recentScroll = decisionAuditRecentEl.scrollTop;
  if (recent.length === 0) {
    decisionAuditRecentEl.innerHTML = "";
  } else {
    decisionAuditRecentEl.innerHTML = recent.map((entry) => {
      const frame = entry.frame_tick != null ? `f${entry.frame_tick}` : "";
      const action = escapeHtml((entry.action || "—").replace(/_/g, " "));
      const category = escapeHtml(entry.reasoning_category || "—");
      const msg = escapeHtml(truncateAuditText(entry.activity_message, 80));
      return `<li class="decision-audit-recent-row">` +
        `${frame ? `<span class="decision-audit-frame">${escapeHtml(frame)}</span> ` : ""}` +
        `<span class="decision-audit-action">${action}</span> ` +
        `<span class="decision-audit-category">(${category})</span> ` +
        `${decisionAuditScoreBadge(entry.score)} ` +
        (msg ? `<span class="decision-audit-msg">${msg}</span>` : "") +
        `</li>`;
    }).join("");
  }
  decisionAuditRecentEl.scrollTop = recentScroll;
}

async function pollDecisionAudit() {
  try {
    const res = await fetch("/decision-audit", { cache: "no-store" });
    if (!res.ok) {
      lastDecisionAuditKey = "";
      if (decisionAuditAgentListEl) {
        decisionAuditAgentListEl.innerHTML =
          '<li class="decision-audit-empty">Audit unavailable</li>';
      }
      return;
    }
    const data = await res.json();
    renderDecisionAuditPanel(data);
  } catch (err) {
    lastDecisionAuditKey = "";
    if (decisionAuditAgentListEl) {
      decisionAuditAgentListEl.innerHTML =
        '<li class="decision-audit-empty">Could not reach audit route</li>';
    }
  }
}

pollDecisionAudit();
decisionAuditPollTimer = setInterval(pollDecisionAudit, DECISION_AUDIT_POLL_MS);

// --- Divine Audit tab (idea-10 full view) --------------------------------
const GOD_DECISION_AUDIT_POLL_MS = DECISION_AUDIT_POLL_MS;

const godDecisionAuditAgentFilterEl = document.getElementById("godDecisionAuditAgentFilter");
const godDecisionAuditIntentFilterEl = document.getElementById("godDecisionAuditIntentFilter");
const godDecisionAuditOutcomeFilterEl = document.getElementById("godDecisionAuditOutcomeFilter");
const godDecisionAuditStatusEl = document.getElementById("godDecisionAuditStatus");
const godDecisionAuditSummaryEl = document.getElementById("godDecisionAuditSummary");
const godDecisionAuditEntriesEl = document.getElementById("godDecisionAuditEntries");

let godDecisionAuditPollTimer = null;
let godDecisionAuditCache = null;
let lastGodDecisionAuditKey = "";
const godDecisionAuditFilters = { agent: "", intent: "all", outcome: "all" };

function stopGodDecisionAuditPoll() {
  if (godDecisionAuditPollTimer != null) {
    clearInterval(godDecisionAuditPollTimer);
    godDecisionAuditPollTimer = null;
  }
}

function startGodDecisionAuditPoll() {
  stopGodDecisionAuditPoll();
  pollGodDecisionAudit();
  godDecisionAuditPollTimer = setInterval(pollGodDecisionAudit, GOD_DECISION_AUDIT_POLL_MS);
}

function decisionAuditIntentBadge(intent) {
  if (intent === "match") {
    return '<span class="decision-audit-badge decision-audit-badge-match">match</span>';
  }
  if (intent === "mismatch") {
    return '<span class="decision-audit-badge decision-audit-badge-mismatch">mismatch</span>';
  }
  if (intent === "unclassified" || intent === "uncorrelated" || intent === "fallback") {
    return `<span class="decision-audit-badge decision-audit-badge-muted">${escapeHtml(intent)}</span>`;
  }
  return '<span class="decision-audit-badge decision-audit-badge-muted">—</span>';
}

function decisionAuditOutcomeBadge(outcome) {
  if (outcome === "ok") {
    return '<span class="decision-audit-badge decision-audit-badge-outcome-ok">ok</span>';
  }
  if (outcome === "fail") {
    return '<span class="decision-audit-badge decision-audit-badge-outcome-fail">fail</span>';
  }
  if (outcome === "unknown") {
    return '<span class="decision-audit-badge decision-audit-badge-outcome-unknown">unknown</span>';
  }
  return '<span class="decision-audit-badge decision-audit-badge-muted">—</span>';
}

function readGodDecisionAuditFilters() {
  godDecisionAuditFilters.agent = godDecisionAuditAgentFilterEl
    ? godDecisionAuditAgentFilterEl.value.trim().toLowerCase()
    : "";
  godDecisionAuditFilters.intent = godDecisionAuditIntentFilterEl
    ? godDecisionAuditIntentFilterEl.value
    : "all";
  godDecisionAuditFilters.outcome = godDecisionAuditOutcomeFilterEl
    ? godDecisionAuditOutcomeFilterEl.value
    : "all";
}

function filterGodDecisionAuditEntries(entries) {
  readGodDecisionAuditFilters();
  const agentNeedle = godDecisionAuditFilters.agent;
  const intentFilter = godDecisionAuditFilters.intent;
  const outcomeFilter = godDecisionAuditFilters.outcome;
  return (entries || []).filter((entry) => {
    if (agentNeedle) {
      const name = String(entry.agent_name || "").toLowerCase();
      if (!name.includes(agentNeedle)) return false;
    }
    if (intentFilter !== "all" && entry.intent !== intentFilter) return false;
    if (outcomeFilter !== "all" && entry.outcome !== outcomeFilter) return false;
    return true;
  });
}

function renderGodDecisionAuditSummary(agents) {
  if (!godDecisionAuditSummaryEl) return;
  const rows = agents || [];
  if (!rows.length) {
    godDecisionAuditSummaryEl.innerHTML =
      '<div class="divine-note">No agent aggregates yet.</div>';
    return;
  }
  const head = "<tr><th>Agent</th><th>Scored</th><th>Match</th><th>Mismatch</th><th>Mismatch rate</th><th>Outcome ok</th><th>Outcome fail</th><th>Outcome unknown</th></tr>";
  const body = rows.map((agent) => {
    const name = escapeHtml(agent.agent_name || "—");
    const scored = escapeHtml(String(agent.scored ?? 0));
    const matches = escapeHtml(String(agent.matches ?? 0));
    const mismatches = escapeHtml(String(agent.mismatches ?? 0));
    const rate = escapeHtml(formatMismatchRate(agent.mismatch_rate));
    const ok = escapeHtml(String(agent.outcome_ok ?? 0));
    const fail = escapeHtml(String(agent.outcome_fail ?? 0));
    const unknown = escapeHtml(String(agent.outcome_unknown ?? 0));
    return `<tr><td>${name}</td><td>${scored}</td><td>${matches}</td><td>${mismatches}</td><td>${rate}</td><td>${ok}</td><td>${fail}</td><td>${unknown}</td></tr>`;
  }).join("");
  godDecisionAuditSummaryEl.innerHTML =
    `<table class="divine-audit-summary-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderGodDecisionAuditEntries(entries) {
  if (!godDecisionAuditEntriesEl) return;
  const filtered = filterGodDecisionAuditEntries(entries);
  const auditKey = JSON.stringify(filtered);
  if (auditKey === lastGodDecisionAuditKey) return;
  lastGodDecisionAuditKey = auditKey;

  const scroll = godDecisionAuditEntriesEl.scrollTop;
  if (!filtered.length) {
    godDecisionAuditEntriesEl.innerHTML =
      '<li class="divine-audit-empty">No entries match the current filters.</li>';
  } else {
    godDecisionAuditEntriesEl.innerHTML = filtered.map((entry) => {
      const frame = entry.frame_tick != null ? `f${entry.frame_tick}` : "—";
      const agent = escapeHtml(entry.agent_name || "—");
      const action = escapeHtml((entry.action || "—").replace(/_/g, " "));
      const reasoning = escapeHtml(entry.reasoning || "—");
      const activity = entry.activity_message
        ? escapeHtml(entry.activity_message)
        : "—";
      const category = escapeHtml(entry.reasoning_category || "—");
      return `<li class="divine-audit-entry">` +
        `<div class="divine-audit-entry-head">` +
        `<span class="divine-audit-frame">${escapeHtml(frame)}</span> ` +
        `<span class="divine-audit-agent">${agent}</span> ` +
        `<span class="divine-audit-action">${action}</span> ` +
        `${decisionAuditIntentBadge(entry.intent)} ` +
        `${decisionAuditOutcomeBadge(entry.outcome)}` +
        `</div>` +
        `<div class="divine-audit-entry-reasoning"><span class="divine-audit-k">Reasoning</span> ${reasoning}</div>` +
        `<div class="divine-audit-entry-meta"><span class="divine-audit-k">Category</span> ${category}</div>` +
        `<div class="divine-audit-entry-activity"><span class="divine-audit-k">Activity</span> ${activity}</div>` +
        `</li>`;
    }).join("");
  }
  godDecisionAuditEntriesEl.scrollTop = scroll;
}

function renderGodDecisionAuditPanel(data) {
  if (!godDecisionAuditSummaryEl && !godDecisionAuditEntriesEl) return;

  if (!data || !data.enabled) {
    stopGodDecisionAuditPoll();
    lastGodDecisionAuditKey = "";
    if (godDecisionAuditStatusEl) {
      godDecisionAuditStatusEl.textContent = "Decision audit disabled.";
    }
    renderGodDecisionAuditSummary([]);
    if (godDecisionAuditEntriesEl) {
      godDecisionAuditEntriesEl.innerHTML =
        '<li class="divine-audit-empty">Decision audit disabled</li>';
    }
    return;
  }

  if (godDecisionAuditStatusEl) {
    const total = (data.entries || []).length;
    godDecisionAuditStatusEl.textContent = total
      ? `${total} decision(s) in session (newest first).`
      : "No decisions logged yet.";
  }

  const agentsKey = JSON.stringify(data.agents || []);
  const summaryScroll = godDecisionAuditSummaryEl ? godDecisionAuditSummaryEl.scrollTop : 0;
  renderGodDecisionAuditSummary(data.agents || []);
  if (godDecisionAuditSummaryEl) {
    godDecisionAuditSummaryEl.dataset.agentsKey = agentsKey;
    godDecisionAuditSummaryEl.scrollTop = summaryScroll;
  }
  renderGodDecisionAuditEntries(data.entries || []);
}

function renderGodDecisionAuditFromCache() {
  if (godDecisionAuditCache) renderGodDecisionAuditPanel(godDecisionAuditCache);
}

function wireGodDecisionAuditFiltersOnce() {
  if (wireGodDecisionAuditFiltersOnce._wired) return;
  wireGodDecisionAuditFiltersOnce._wired = true;
  [godDecisionAuditAgentFilterEl, godDecisionAuditIntentFilterEl, godDecisionAuditOutcomeFilterEl]
    .forEach((el) => {
      if (!el) return;
      const evt = el.tagName === "SELECT" ? "change" : "input";
      el.addEventListener(evt, () => {
        lastGodDecisionAuditKey = "";
        renderGodDecisionAuditFromCache();
      });
    });
}

function renderGodDecisionAudit() {
  wireGodDecisionAuditFiltersOnce();
  startGodDecisionAuditPoll();
}

async function pollGodDecisionAudit() {
  if (typeof divineModalOpenFeature === "undefined" || divineModalOpenFeature !== "audit") {
    return;
  }
  try {
    const res = await fetch("/decision-audit?view=full", { cache: "no-store" });
    if (!res.ok) {
      lastGodDecisionAuditKey = "";
      if (godDecisionAuditStatusEl) godDecisionAuditStatusEl.textContent = "Audit unavailable.";
      if (godDecisionAuditEntriesEl) {
        godDecisionAuditEntriesEl.innerHTML =
          '<li class="divine-audit-empty">Audit unavailable</li>';
      }
      return;
    }
    const data = await res.json();
    godDecisionAuditCache = data;
    renderGodDecisionAuditPanel(data);
  } catch (err) {
    lastGodDecisionAuditKey = "";
    if (godDecisionAuditStatusEl) godDecisionAuditStatusEl.textContent = "Could not reach audit route.";
    if (godDecisionAuditEntriesEl) {
      godDecisionAuditEntriesEl.innerHTML =
        '<li class="divine-audit-empty">Could not reach audit route</li>';
    }
  }
}
