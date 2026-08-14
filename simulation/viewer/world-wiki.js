// =====================================================================
// World Wiki modal — pure renderer over GET /wiki payload.
// Gated by WORLD_WIKI_ENABLED_FLAG (mirrors config.flags.WORLD_WIKI_ENABLED
// from /state, set in polling.js). No simulation logic: only renders the
// cross-linked page model returned by the server.
//
// Public API (global scope, called from sidebar.js / council.js):
//   openWorldWiki(kind, id)  — open modal to a specific entity page
// =====================================================================

// Wiki modal DOM refs (elements added in index.html).
const wikiModal = document.getElementById("worldWikiModal");
const wikiTitleEl = document.getElementById("wikiTitle");
const wikiSubtitleEl = document.getElementById("wikiSubtitle");
const wikiCloseBtn = document.getElementById("wikiCloseBtn");
const wikiBackBtn = document.getElementById("wikiBackBtn");
const wikiIndexEl = document.getElementById("wikiIndex");
const wikiBodyEl = document.getElementById("wikiBody");
const wikiSearchEl = document.getElementById("wikiSearch");

// Runtime state — all client-only, no simulation state.
let wikiModalOpen = false;
let wikiCurrentKind = null;
let wikiCurrentId = null;
let wikiData = null;
let wikiPollTimer = null;

// Back-navigation stack: [{kind, id}, ...]
const wikiHistory = [];

const WIKI_POLL_MS = 3000;

// Kind display names for all twelve entity kinds.
const WIKI_KIND_LABELS = {
  agent: "Agents",
  structure: "Structures",
  belief: "Beliefs",
  rule: "Rules",
  chronicle: "Chronicle",
  district: "Districts",
  settlement: "Settlements",
  treaty: "Treaties",
  resource: "Resources",
  project: "Projects",
  recipe: "Recipes",
};

// Relation display labels (structured fields only per plan §2 Answer 2).
function wikiFormatRelation(relation) {
  if (!relation) return "";
  if (relation.startsWith("socialTie:")) {
    return `social tie (${relation.split(":")[1]})`;
  }
  const MAP = {
    ally: "ally",
    rival: "rival",
    district: "in district",
    homeDistrict: "home district",
    districtId: "in district",
    homeOf: "home of",
    settlementId: "settlement",
    needs: "needs resource",
    ingredient: "ingredient",
  };
  return MAP[relation] || relation.replace(/_/g, " ");
}

// ---- Public entry-point ----

function openWorldWiki(kind, id) {
  if (!WORLD_WIKI_ENABLED_FLAG) return;
  if (wikiModalOpen && wikiCurrentKind && wikiCurrentId) {
    // Push current position to history before navigating.
    wikiHistory.push({ kind: wikiCurrentKind, id: wikiCurrentId });
  }
  wikiCurrentKind = kind;
  wikiCurrentId = String(id);
  if (!wikiModalOpen) {
    wikiModal.classList.add("open");
    wikiModalOpen = true;
    startWikiPoll();
  } else {
    renderWikiModal();
  }
  wikiBackBtn.disabled = wikiHistory.length === 0;
}

function closeWorldWiki() {
  wikiModal.classList.remove("open");
  wikiModalOpen = false;
  wikiCurrentKind = null;
  wikiCurrentId = null;
  wikiHistory.length = 0;
  stopWikiPoll();
}

function wikiNavigateBack() {
  const prev = wikiHistory.pop();
  if (!prev) return;
  wikiCurrentKind = prev.kind;
  wikiCurrentId = prev.id;
  wikiBackBtn.disabled = wikiHistory.length === 0;
  renderWikiModal();
}

// ---- Polling ----

function startWikiPoll() {
  if (wikiPollTimer) return;
  fetchWiki();
  wikiPollTimer = setInterval(fetchWiki, WIKI_POLL_MS);
}

function stopWikiPoll() {
  if (wikiPollTimer) {
    clearInterval(wikiPollTimer);
    wikiPollTimer = null;
  }
}

async function fetchWiki() {
  try {
    const resp = await fetch("/wiki");
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.ok && data.pages) {
      wikiData = data;
      if (wikiModalOpen) renderWikiModal();
    }
  } catch (_) {
    // Ignore fetch errors; keep last wikiData.
  }
}

// ---- Data helpers ----

function wikiPageById(kind, id) {
  if (!wikiData || !wikiData.pages) return null;
  const pages = wikiData.pages[kind];
  if (!Array.isArray(pages)) return null;
  const needle = String(id);
  return pages.find((p) => String(p.id) === needle) || null;
}

function wikiPageNameById(kind, id) {
  const page = wikiPageById(kind, id);
  if (!page) return String(id);
  const f = page.fields || {};
  return String(f.name || f.id || id);
}

// ---- HTML helpers ----

function wikiLinkHtml(kind, id, label) {
  const safeKind = escapeHtml(kind);
  const safeId = escapeHtml(String(id));
  const safeLbl = escapeHtml(String(label));
  return `<span class="wiki-link" data-wiki-kind="${safeKind}" data-wiki-id="${safeId}">${safeLbl}</span>`;
}

// ---- Index pane ----

function renderWikiIndex() {
  if (!wikiData || !wikiData.pages) {
    return '<div class="wiki-empty">Loading…</div>';
  }
  const filter = (wikiSearchEl ? wikiSearchEl.value : "").toLowerCase().trim();
  const kinds = Object.keys(WIKI_KIND_LABELS);
  let html = "";
  for (const kind of kinds) {
    const pages = wikiData.pages[kind];
    if (!Array.isArray(pages) || !pages.length) continue;
    const filtered = filter
      ? pages.filter((p) => {
          const f = p.fields || {};
          const name = String(f.name || f.id || p.id || "");
          return name.toLowerCase().includes(filter);
        })
      : pages;
    if (!filtered.length) continue;
    html += `<div class="wiki-index-group">`;
    html += `<div class="wiki-index-kind">${escapeHtml(WIKI_KIND_LABELS[kind])}</div>`;
    for (const p of filtered) {
      const f = p.fields || {};
      const name = String(f.name || f.id || p.id || "?");
      const active = wikiCurrentKind === kind && wikiCurrentId === String(p.id);
      html += `<div class="wiki-index-item${active ? " active" : ""}" data-wiki-kind="${kind}" data-wiki-id="${escapeHtml(String(p.id))}">${escapeHtml(name)}</div>`;
    }
    html += `</div>`;
  }
  return html || '<div class="wiki-empty">No entities found.</div>';
}

// ---- Page content pane ----

function formatWikiFieldValue(key, val) {
  if (val == null) return null;
  if (typeof val === "boolean") return val ? "yes" : "no";
  if (Array.isArray(val)) {
    if (!val.length) return null;
    return val.map((v) => escapeHtml(String(v))).join(", ");
  }
  if (typeof val === "object") {
    const entries = Object.entries(val);
    if (!entries.length) return null;
    return entries.map(([k, v]) => `<span class="wiki-kv">${escapeHtml(k)}: ${escapeHtml(String(v))}</span>`).join(" ");
  }
  const s = String(val);
  if (!s || s === "null" || s === "undefined") return null;
  return escapeHtml(s);
}

function renderWikiPageContent(page) {
  if (!page) {
    return '<div class="wiki-empty">Entity not found. The world may not have this yet.</div>';
  }
  const fields = page.fields || {};
  const links = page.links || [];

  // Fields table — skip id (shown in header), nulls, empty strings, false.
  let fieldsHtml = "";
  const SKIP_FIELDS = new Set(["id"]);
  for (const [key, val] of Object.entries(fields)) {
    if (SKIP_FIELDS.has(key)) continue;
    if (val === null || val === undefined || val === "" || val === false) continue;
    const formatted = formatWikiFieldValue(key, val);
    if (!formatted) continue;
    const label = key.replace(/_/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
    fieldsHtml += `<tr><th>${escapeHtml(label)}</th><td>${formatted}</td></tr>`;
  }

  let html = fieldsHtml
    ? `<table class="wiki-fields"><tbody>${fieldsHtml}</tbody></table>`
    : '<p class="wiki-empty">No fields to display.</p>';

  // Forward cross-links.
  if (links.length) {
    html += `<div class="wiki-links-section"><h4 class="wiki-links-heading">Links</h4>`;
    html += links.map((link) => {
      const targetName = wikiPageNameById(link.targetKind, link.targetId);
      const kindLabel = WIKI_KIND_LABELS[link.targetKind] || link.targetKind;
      const relation = wikiFormatRelation(link.relation);
      return `<div class="wiki-link-row"><span class="wiki-link-rel">${escapeHtml(relation)}</span> → ` +
        `<span class="wiki-link-kind-badge">${escapeHtml(kindLabel)}</span> ` +
        wikiLinkHtml(link.targetKind, link.targetId, targetName) +
        `</div>`;
    }).join("");
    html += `</div>`;
  }

  // Reverse links: structures whose homeOf points to this agent.
  // This enables the agent → structure step of the required click-through chain.
  if (page.kind === "agent" && wikiData) {
    const homeStructures = (wikiData.pages.structure || []).filter((s) =>
      (s.links || []).some(
        (l) => l.relation === "homeOf" && String(l.targetId) === String(page.id)
      )
    );
    if (homeStructures.length) {
      html += `<div class="wiki-links-section"><h4 class="wiki-links-heading">Home structure</h4>`;
      html += homeStructures.map((s) => {
        const sname = String((s.fields && (s.fields.name || s.fields.type)) || s.id || "?");
        return `<div class="wiki-link-row">` +
          `<span class="wiki-link-kind-badge">Structure</span> ` +
          wikiLinkHtml("structure", s.id, sname) +
          `</div>`;
      }).join("");
      html += `</div>`;
    }
  }

  return html;
}

// ---- Main render ----

function renderWikiModal() {
  const page = (wikiCurrentKind && wikiCurrentId)
    ? wikiPageById(wikiCurrentKind, wikiCurrentId)
    : null;

  const f = page ? (page.fields || {}) : {};
  const pageName = String(f.name || f.id || wikiCurrentId || "—");
  const kindLabel = wikiCurrentKind ? (WIKI_KIND_LABELS[wikiCurrentKind] || wikiCurrentKind) : "";

  wikiTitleEl.textContent = wikiCurrentKind ? pageName : "World Wiki";
  wikiSubtitleEl.textContent = kindLabel;
  wikiBackBtn.disabled = wikiHistory.length === 0;

  wikiIndexEl.innerHTML = renderWikiIndex();

  wikiBodyEl.innerHTML = wikiCurrentKind
    ? renderWikiPageContent(page)
    : '<div class="wiki-empty">Select an entity from the index on the left.</div>';
}

// ---- Event wiring ----

wikiCloseBtn.addEventListener("click", closeWorldWiki);
wikiBackBtn.addEventListener("click", wikiNavigateBack);

wikiModal.addEventListener("click", (event) => {
  if (event.target === wikiModal) closeWorldWiki();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && wikiModalOpen) closeWorldWiki();
});

// Delegated click handler for all wiki-link elements site-wide.
// Also handles wiki-index-item clicks inside the modal.
document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) return;
  const el = event.target.closest("[data-wiki-kind][data-wiki-id]");
  if (!el) return;
  const kind = el.dataset.wikiKind;
  const id = el.dataset.wikiId;
  if (!kind || id == null) return;
  event.stopPropagation();
  openWorldWiki(kind, id);
}, true); // capture phase so stopPropagation works before list item handlers

if (wikiSearchEl) {
  wikiSearchEl.addEventListener("input", () => {
    if (wikiModalOpen) wikiIndexEl.innerHTML = renderWikiIndex();
  });
}
