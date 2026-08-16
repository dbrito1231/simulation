// =====================================================================
// Anomaly radar (idea-07, ANOMALY_RADAR_ENABLED; expanded to two surfaces +
// richer rendering by idea-07b, docs/plans/idea-07b-anomaly-console/plan.md).
// Pure renderer over GET /anomalies -- see specs/11-viewer.md "Anomaly
// panel" and specs/04-http-api.md "Anomaly radar (idea-07)". Polls on its
// own setInterval, independent of pollState()'s STATE_POLL_MS and
// pollDistricts()'s DISTRICTS_POLL_MS (viewer/polling.js, viewer/render.js)
// -- this feature adds no /state key, so it cannot piggyback on either loop.
// No client-side detection/threshold logic: this file only lists/groups the
// `anomalies` array the route already computed.
//
// Two surfaces share one poll: the sidebar #anomalyPanel (always-available,
// compact) and the divine-bar Anomaly button + #divineTab-anomaly modal
// view (richer, grouped by kind). The modal view never calls
// godApiFetch()/`/control/god/*` and is not gated by godAuthorized -- it is
// a self-contained poll+render pair, independent of the Divine Console auth
// state machine (specs/11-viewer.md).
// =====================================================================
const ANOMALY_POLL_MS = 5000;
const ANOMALY_KIND_ORDER = ["range_break", "new_rule_kind", "faction_split"];

const anomalySectionEl = document.getElementById("anomalySection");
const anomalyListEl = document.getElementById("anomalyList");
const anomalyBarBtnEl = document.querySelector('.gbtn[data-feature="anomaly"]');
const divineAnomalyGroupsEl = document.getElementById("divineAnomalyGroups");
let lastAnomalyKey = null;
let lastAnomalyModalKey = null;

function anomalyKindLabel(kind) {
  if (kind === "range_break") return "Range break";
  if (kind === "new_rule_kind") return "New rule kind";
  if (kind === "faction_split") return "Faction Split";
  return kind || "anomaly";
}

function anomalySeverityLabel(severity) {
  if (severity === "high") return "high";
  if (severity === "medium") return "medium";
  if (severity === "low") return "low";
  return severity || "";
}

function anomalyFrameLabel(a) {
  return a.timestamp != null ? `frame ${escapeHtml(String(a.timestamp))}` : "";
}

// data: {ok, enabled, anomalies: [{timestamp, metric, kind, value, detail?, severity}, ...]}
function renderAnomalies(data) {
  updateAnomalyBarButton(data);
  renderAnomalySidebar(data);
  renderAnomalyModal(data);
}

function updateAnomalyBarButton(data) {
  if (!anomalyBarBtnEl) return;
  // ANOMALY_RADAR_ENABLED is not echoed in /state config.flags -- the
  // route's own `enabled` field is the single kill switch for BOTH surfaces
  // (idea-07b §6). Independent of GOD_MODE_ENABLED_FLAG's show/hide of the
  // whole #divineBar.
  anomalyBarBtnEl.style.display = (data && data.enabled) ? "" : "none";
}

function renderAnomalySidebar(data) {
  if (!anomalySectionEl || !anomalyListEl) return;
  if (!data || !data.enabled) {
    anomalySectionEl.style.display = "none";
    lastAnomalyKey = null;
    return;
  }
  anomalySectionEl.style.display = "";
  const anomalies = data.anomalies || [];
  const key = JSON.stringify(anomalies);
  if (key === lastAnomalyKey) return;
  lastAnomalyKey = key;
  anomalyListEl.innerHTML = anomalies.slice().reverse().map((a) => {
    const kindLabel = anomalyKindLabel(a.kind);
    const metric = escapeHtml(String(a.metric != null ? a.metric : ""));
    const value = escapeHtml(String(a.value != null ? a.value : ""));
    const severity = anomalySeverityLabel(a.severity);
    const frame = anomalyFrameLabel(a);
    return `<li><span class="anomaly-kind">${escapeHtml(kindLabel)}</span> ` +
      `<span class="anomaly-metric">${metric}</span>: ` +
      `<span class="anomaly-value">${value}</span>` +
      (severity ? ` <span class="anomaly-severity anomaly-severity-${escapeHtml(severity)}">${escapeHtml(severity)}</span>` : "") +
      (frame ? ` <span class="anomaly-frame">${frame}</span>` : "") +
      `</li>`;
  }).join("") || `<li class="civ-label">No anomalies detected yet</li>`;
}

// Small self-contained key/value renderer for the `detail` field -- kept
// local to this file (not divine-bootstrap.js's godRenderKeyValueRows)
// because anomaly.js loads and runs before divine-bootstrap.js in
// index.html's <script> order, and this feature is a self-contained
// poll+render pair independent of the Divine Console's other machinery.
function anomalyDetailRows(detail) {
  if (!detail || typeof detail !== "object") return "";
  return Object.entries(detail)
    .filter(([, v]) => v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `<span class="anomaly-detail-kv"><span class="anomaly-detail-key">${escapeHtml(k)}:</span> ${escapeHtml(String(v))}</span>`)
    .join(" ");
}

function anomalyModalRowHtml(a) {
  const metric = escapeHtml(String(a.metric != null ? a.metric : ""));
  const value = escapeHtml(String(a.value != null ? a.value : ""));
  const severity = anomalySeverityLabel(a.severity);
  const frame = anomalyFrameLabel(a);
  const detailHtml = anomalyDetailRows(a.detail);
  return `<li>` +
    `<span class="anomaly-metric">${metric}</span>: ` +
    `<span class="anomaly-value">${value}</span>` +
    (severity ? ` <span class="anomaly-severity anomaly-severity-${escapeHtml(severity)}">${escapeHtml(severity)}</span>` : "") +
    (frame ? ` <span class="anomaly-frame">${frame}</span>` : "") +
    (detailHtml ? `<div class="anomaly-detail-row">${detailHtml}</div>` : "") +
    `</li>`;
}

// Grouped by the existing `kind` field only -- not detection logic (the
// server already computed every entry); matches
// specs/04-http-api.md "No per-kind grouping field added to the response".
function renderAnomalyModal(data) {
  if (!divineAnomalyGroupsEl) return;
  if (!data || !data.enabled) {
    divineAnomalyGroupsEl.innerHTML = `<p class="divine-note">Anomaly radar is disabled (ANOMALY_RADAR_ENABLED is off).</p>`;
    lastAnomalyModalKey = null;
    return;
  }
  const anomalies = data.anomalies || [];
  const key = JSON.stringify(anomalies);
  if (key === lastAnomalyModalKey) return;
  lastAnomalyModalKey = key;
  const byKind = {};
  ANOMALY_KIND_ORDER.forEach((k) => { byKind[k] = []; });
  anomalies.forEach((a) => {
    if (!byKind[a.kind]) byKind[a.kind] = [];
    byKind[a.kind].push(a);
  });
  divineAnomalyGroupsEl.innerHTML = ANOMALY_KIND_ORDER.map((kind) => {
    const entries = (byKind[kind] || []).slice().reverse();
    const label = escapeHtml(anomalyKindLabel(kind));
    const rows = entries.length
      ? entries.map(anomalyModalRowHtml).join("")
      : `<li class="civ-label">None detected yet</li>`;
    return `<details class="divine-anomaly-group" open>` +
      `<summary>${label} (<span class="anomaly-group-count">${entries.length}</span>)</summary>` +
      `<ul>${rows}</ul>` +
      `</details>`;
  }).join("");
}

async function pollAnomalies() {
  try {
    const res = await fetch("/anomalies", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    renderAnomalies(data);
  } catch (err) {
    // Keep the last rendered panel state; /state polling already surfaces
    // connectivity issues, no need to duplicate that here.
  }
}

pollAnomalies();
setInterval(pollAnomalies, ANOMALY_POLL_MS);
