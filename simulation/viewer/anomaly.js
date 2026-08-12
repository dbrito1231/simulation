// =====================================================================
// Anomaly radar panel (idea-07, ANOMALY_RADAR_ENABLED). Pure renderer over
// GET /anomalies -- see specs/11-viewer.md "Anomaly panel" and
// specs/04-http-api.md "Anomaly radar (idea-07)". Polls on its own
// setInterval, independent of pollState()'s STATE_POLL_MS and
// pollDistricts()'s DISTRICTS_POLL_MS (viewer/polling.js, viewer/render.js)
// -- this feature adds no /state key, so it cannot piggyback on either loop.
// No client-side detection/threshold logic: this file only lists the
// `anomalies` array the route already computed.
// =====================================================================
const ANOMALY_POLL_MS = 5000;
const anomalyPanelEl = document.getElementById("anomalyPanel");
const anomalyListEl = document.getElementById("anomalyList");
let lastAnomalyKey = null;

function anomalyKindLabel(kind) {
  if (kind === "range_break") return "Range break";
  if (kind === "new_rule_kind") return "New rule kind";
  if (kind === "schism") return "Schism";
  return kind || "anomaly";
}

// data: {ok, enabled, anomalies: [{timestamp, metric, kind, value, detail?}, ...]}
function renderAnomalies(data) {
  if (!anomalyPanelEl || !anomalyListEl) return;
  // ANOMALY_RADAR_ENABLED is not echoed in /state config.flags (unlike every
  // other flagged panel) -- the route's own `enabled` field is the only
  // signal for this feature's on/off state (specs/11-viewer.md). Flag off:
  // hide the panel rather than show a stale/empty list as if it were on.
  if (!data || !data.enabled) {
    anomalyPanelEl.style.display = "none";
    lastAnomalyKey = null;
    return;
  }
  anomalyPanelEl.style.display = "";
  const anomalies = data.anomalies || [];
  const key = JSON.stringify(anomalies);
  if (key === lastAnomalyKey) return;
  lastAnomalyKey = key;
  anomalyListEl.innerHTML = anomalies.slice().reverse().map((a) => {
    const kindLabel = anomalyKindLabel(a.kind);
    const metric = escapeHtml(String(a.metric != null ? a.metric : ""));
    const value = escapeHtml(String(a.value != null ? a.value : ""));
    const frame = a.timestamp != null ? `frame ${escapeHtml(String(a.timestamp))}` : "";
    return `<li><span class="anomaly-kind">${escapeHtml(kindLabel)}</span> ` +
      `<span class="anomaly-metric">${metric}</span>: ` +
      `<span class="anomaly-value">${value}</span>` +
      (frame ? ` <span class="anomaly-frame">${frame}</span>` : "") +
      `</li>`;
  }).join("") || `<li class="civ-label">No anomalies detected yet</li>`;
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
