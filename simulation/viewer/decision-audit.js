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
    decisionAuditPanelEl.style.display = "none";
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
      return;
    }
    const data = await res.json();
    renderDecisionAuditPanel(data);
  } catch (err) {
    lastDecisionAuditKey = "";
    /* keep last render; /state polling surfaces connectivity issues */
  }
}
