// Collapsible sidebar chrome only. Panel visibility remains owned by the
// existing renderers, which may independently hide or show each section.
const PANEL_COLLAPSE_STORAGE_KEY = "sim.panels.collapsed";
const PANEL_ARROW_EXPANDED = "\u25bc";
const PANEL_ARROW_COLLAPSED = "\u25b6";

function readCollapsedPanelIds() {
  try {
    const raw = localStorage.getItem(PANEL_COLLAPSE_STORAGE_KEY);
    if (raw === null) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.some(id => typeof id !== "string")) {
      return new Set();
    }
    return new Set(parsed);
  } catch (_error) {
    return new Set();
  }
}

function persistCollapsedPanelIds() {
  const ids = Array.from(document.querySelectorAll(".panel-section.panel-collapsed"), section => section.id)
    .filter(Boolean);
  try {
    localStorage.setItem(PANEL_COLLAPSE_STORAGE_KEY, JSON.stringify(ids));
  } catch (_error) {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

function setPanelCollapsed(section, toggle, arrow, collapsed) {
  section.classList.toggle("panel-collapsed", collapsed);
  toggle.setAttribute("aria-expanded", String(!collapsed));
  arrow.textContent = collapsed ? PANEL_ARROW_COLLAPSED : PANEL_ARROW_EXPANDED;
}

const collapsedPanelIds = readCollapsedPanelIds();
document.querySelectorAll("section.panel-section").forEach(section => {
  const toggle = section.querySelector(":scope > .panel-head, :scope > .agents-panel-head > .panel-head");
  const arrow = toggle?.querySelector(".panel-arrow");
  if (!toggle || !arrow) return;

  setPanelCollapsed(section, toggle, arrow, collapsedPanelIds.has(section.id));

  const clickSurface = toggle.closest(".agents-panel-head") || toggle;
  clickSurface.addEventListener("click", event => {
    const interactive = event.target instanceof Element
      ? event.target.closest("button, a")
      : null;
    if (interactive && interactive !== toggle) return;

    const collapsed = !section.classList.contains("panel-collapsed");
    setPanelCollapsed(section, toggle, arrow, collapsed);
    persistCollapsedPanelIds();
  });
});
