// --- Auth / fetch plumbing --------------------------------------------
async function godApiFetch(path, opts) {
  opts = opts || {};
  const headers = {};
  if (godToken) headers["X-God-Token"] = godToken;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      cache: "no-store",
    });
  } catch (err) {
    return { ok: false, status: 0, data: { ok: false, reason: "network error" } };
  }
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (res.status === 401) {
    godLockConsole("Sign-in failed — token cleared. Enter your secret token again.");
  }
  return { ok: res.ok, status: res.status, data: data || { ok: false, reason: `HTTP ${res.status}` } };
}

function updateGodAuthStatus(text, cls) {
  godAuthStatusEl.textContent = text;
  godAuthStatusEl.className = "divine-status" + (cls ? " " + cls : "");
}

function godEffectivelyAuthorized() {
  return godAuthorized || !GOD_AUTH_REQUIRED_FLAG;
}

function godDefaultDayFrames() {
  const cfg = world.config || {};
  if (cfg.DAY_FRAMES != null) return Number(cfg.DAY_FRAMES) || 18000;
  return 18000;
}

function godInterventionCount() {
  const pub = (world.god && world.god.recentPublicInterventions) || [];
  if (pub.length) return pub.length;
  const gs = (getCiv().godState && getCiv().godState.recentInterventions) || [];
  return gs.length;
}

function godVoiceGuidanceInWindow(record) {
  if (!record || typeof record !== "object") return false;
  const now = world.frameTick || 0;
  const exp = Number(record.expiresFrame);
  if (Number.isFinite(exp) && now >= exp) return false;
  const created = Number(record.createdFrame);
  if (Number.isFinite(created) && now < created) return false;
  return true;
}

function godProvidenceIsActive() {
  const prov = (godLastSight && godLastSight.providence) || (world.god && world.god.providence);
  return godVoiceGuidanceInWindow(prov);
}

function godActiveEventsList() {
  if (godEffectivelyAuthorized() && godLastSight && godLastSight.activeEvents) {
    return (godLastSight.activeEvents || []).filter((e) => e && e.status === "active");
  }
  return ((world.god && world.god.activePublicEvents) || []).filter((e) => e && e.status === "active");
}

function godLawEventsList() {
  return godActiveEventsList().filter((e) => e.modifiers && Object.keys(e.modifiers).length);
}

function godBarPrivateCounts() {
  if (!godEffectivelyAuthorized() || !godLastSight) return null;
  const agents = godLastSight.agents || [];
  return {
    omenCount: agents.filter((a) => a.omen).length,
    gateCount: agents.filter((a) => a.decisionGate || a.divineHold).length,
    samplingCount: agents.filter((a) => a.sampling).length,
    zoneCount: (godLastSight.architectZones || []).length,
  };
}

function godBarSituationalSnapshot() {
  const privateCounts = godBarPrivateCounts();
  const activeEvents = godActiveEventsList();
  const lawEvents = godLawEventsList();
  const providenceOn = godProvidenceIsActive();
  const voiceActivity = providenceOn
    ? 1 + (privateCounts ? privateCounts.omenCount : 0)
    : (privateCounts ? privateCounts.omenCount : 0);
  const matrixAggregate = privateCounts
    ? privateCounts.gateCount + privateCounts.samplingCount + privateCounts.zoneCount
    : 0;
  return {
    providenceOn,
    privateCounts,
    activeEventCount: activeEvents.length,
    lawEventCount: lawEvents.length,
    voiceActivity,
    matrixAggregate,
  };
}

function openDivineBarEffectTarget(feature, scrollTargetId) {
  if (!feature) return;
  openDivineModal(feature);
  godScrollDivineFieldset(scrollTargetId);
}

function godScrollDivineFieldset(fieldsetId) {
  if (!fieldsetId) return;
  requestAnimationFrame(() => {
    const el = document.getElementById(fieldsetId);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function godSightIntervene(agentId, feature, fieldsetId) {
  if (agentId != null) {
    setGodFocusAgent(agentId, { mirrorSelection: true, centerCamera: true });
  }
  openDivineModal(feature);
  godScrollDivineFieldset(fieldsetId);
  if (feature === "miracles" && fieldsetId === "godVitalsFieldset") {
    const agent = (godLastSight && (godLastSight.agents || []).find((a) => a.id === agentId))
      || getLivingAgents().find((a) => a.id === agentId);
    const healthEl = document.getElementById("godVitalsHealth");
    if (agent && healthEl && Number(agent.health) < 80) {
      healthEl.value = String(Math.min(30, Math.max(5, Math.ceil((80 - Number(agent.health)) / 5) * 5)));
    }
  }
}

function godSightInterveneButtonsHtml(agentId) {
  const id = Number(agentId);
  if (!Number.isFinite(id)) return "";
  return (
    `<div class="god-sight-intervene-row">` +
    `<button type="button" class="god-sight-intervene-btn" data-agent-id="${id}" data-feature="voice" data-fieldset="godOmenFieldset">Omen</button>` +
    `<button type="button" class="god-sight-intervene-btn" data-agent-id="${id}" data-feature="miracles" data-fieldset="godVitalsFieldset">Heal</button>` +
    `<button type="button" class="god-sight-intervene-btn" data-agent-id="${id}" data-feature="matrix" data-fieldset="godPossessionFieldset">Possess</button>` +
    `<button type="button" class="god-sight-intervene-btn" data-agent-id="${id}" data-feature="matrix" data-fieldset="godSamplingFieldset">Sampling</button>` +
    `</div>`
  );
}

function godSightGateSummary(gate) {
  if (!gate) return "none";
  const parts = [gate.mode || "?", gate.status || "active"];
  if (gate.armed) parts.push("armed");
  if (gate.pinnedAction) parts.push(`pin=${gate.pinnedAction}`);
  return parts.join(", ");
}

function godSightAgentDiffFields(agent) {
  return {
    health: agent.health,
    hunger: agent.hunger,
    lastAction: agent.lastAction || null,
    divineHold: !!agent.divineHold,
    decisionGate: agent.decisionGate
      ? {
        mode: agent.decisionGate.mode || null,
        status: agent.decisionGate.status || null,
        armed: !!agent.decisionGate.armed,
        pinnedAction: agent.decisionGate.pinnedAction || null,
      }
      : null,
  };
}

function godSightAgentChangeLines(prevAgent, nextAgent) {
  const lines = [];
  if (prevAgent.health !== nextAgent.health) {
    lines.push(`health ${prevAgent.health}→${nextAgent.health}`);
  }
  if (prevAgent.hunger !== nextAgent.hunger) {
    lines.push(`hunger ${prevAgent.hunger}→${nextAgent.hunger}`);
  }
  if ((prevAgent.lastAction || null) !== (nextAgent.lastAction || null)) {
    lines.push(`lastAction ${prevAgent.lastAction || "—"}→${nextAgent.lastAction || "—"}`);
  }
  if (!!prevAgent.divineHold !== !!nextAgent.divineHold) {
    lines.push(`divineHold ${prevAgent.divineHold ? "on" : "off"}→${nextAgent.divineHold ? "on" : "off"}`);
  }
  const pg = godSightGateSummary(prevAgent.decisionGate);
  const ng = godSightGateSummary(nextAgent.decisionGate);
  if (pg !== ng) lines.push(`gate ${pg}→${ng}`);
  return lines;
}

function godSightPulseCardHtml(pulse) {
  if (!pulse || typeof pulse !== "object") return "";
  const crisis = (pulse.crisisAgents || []).slice(0, 8);
  const crisisHtml = crisis.length
    ? crisis.map((c) =>
      `<span class="god-sight-pulse-crisis">${escapeHtml(c.name)} (${escapeHtml(c.reason)})</span>`
    ).join(", ")
    : "none";
  const stock = Object.entries(pulse.stockpileTotals || {})
    .map(([k, v]) => `${escapeHtml(k)} ${escapeHtml(String(v))}`)
    .join(", ") || "empty";
  const sage = pulse.sageStatus || {};
  const sageLine = sage.present
    ? `${escapeHtml(sage.name || "Elder")} (${escapeHtml(sage.role || "elder")}) — ${escapeHtml(sage.status || "?")}, H${escapeHtml(String(sage.health ?? "?"))} / hunger ${escapeHtml(String(sage.hunger ?? "?"))}`
    : escapeHtml(sage.status || "absent");
  const weather = pulse.weather || {};
  const weatherLine = `${escapeHtml(String(weather.state || "clear"))}${(weather.districts || []).length ? ` (${escapeHtml((weather.districts || []).join(", "))})` : ""}`;
  const events = (pulse.activeEventTitles || [])
    .map((t) => escapeHtml(String(t))).join(", ") || "none";
  const prov = pulse.providence || {};
  const provLine = prov.active
    ? `active${prov.expiresFrame != null ? `, ${escapeHtml(godCountdownLabel(prov.expiresFrame))}` : ""}`
    : "off";
  return (
    `<div class="god-sight-pulse-card">` +
    `<div class="god-sight-pulse-title">Village pulse</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Crisis:</span> ${crisisHtml}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Stockpile:</span> ${stock}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Open projects:</span> ${escapeHtml(String(pulse.openProjectsCount ?? 0))}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Sage:</span> ${sageLine}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Weather:</span> ${weatherLine}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Active events:</span> ${events}</div>` +
    `<div class="god-sight-pulse-row"><span class="divine-kv-key">Providence:</span> ${provLine}</div>` +
    `</div>`
  );
}

function godSightDiffStripHtml(prevSight, nextSight, focusId) {
  if (prevSight === undefined) return "";
  if (!prevSight || !nextSight) {
    return `<div class="god-sight-diff-strip god-sight-diff-first">First look — no prior snapshot to diff.</div>`;
  }
  const prevMap = new Map((prevSight.agents || []).map((a) => [a.id, a]));
  const focusLines = [];
  const otherLines = [];
  for (const agent of (nextSight.agents || [])) {
    const prev = prevMap.get(agent.id);
    if (!prev) continue;
    const changes = godSightAgentChangeLines(prev, agent);
    if (!changes.length) continue;
    const text = changes.join("; ");
    const row = `<span class="god-sight-diff-entry">${escapeHtml(agent.name)}: ${escapeHtml(text)}</span>`;
    if (agent.id === focusId) focusLines.push(row);
    else otherLines.push(row);
  }
  if (!focusLines.length && !otherLines.length) {
    return `<div class="god-sight-diff-strip god-sight-diff-none">No changes since last look.</div>`;
  }
  const compactOthers = otherLines.slice(0, 5);
  const overflow = otherLines.length > 5
    ? `<span class="god-sight-diff-more">+${otherLines.length - 5} more agents</span>`
    : "";
  return (
    `<div class="god-sight-diff-strip">` +
    `<div class="god-sight-diff-title">Changed since last look</div>` +
    (focusLines.length ? `<div class="god-sight-diff-focus">${focusLines.join("")}</div>` : "") +
    (compactOthers.length ? `<div class="god-sight-diff-others">${compactOthers.join("")}${overflow}</div>` : "") +
    `</div>`
  );
}

function godCloneSightForDiff(sight) {
  if (!sight) return null;
  return {
    agents: (sight.agents || []).map((a) => ({
      id: a.id,
      name: a.name,
      ...godSightAgentDiffFields(a),
    })),
  };
}

function renderDivineBarEffects() {
  if (!divineBarEffectsEl || !GOD_MODE_ENABLED_FLAG) return;
  if (!godEffectivelyAuthorized()) {
    divineBarEffectsEl.innerHTML = "";
    return;
  }
  const snap = godBarSituationalSnapshot();
  const chips = [];
  chips.push({
    key: "providence",
    label: snap.providenceOn ? "Providence on" : "Providence off",
    cls: snap.providenceOn ? "active" : "inactive",
    feature: "voice",
    scroll: "godProvidenceFieldset",
  });
  if (snap.privateCounts) {
    if (snap.privateCounts.omenCount > 0) {
      chips.push({
        key: "omens",
        label: `Omens ${snap.privateCounts.omenCount}`,
        cls: "active",
        feature: "voice",
        scroll: "godOmenFieldset",
      });
    }
    if (snap.privateCounts.gateCount > 0) {
      chips.push({
        key: "gates",
        label: `Gates ${snap.privateCounts.gateCount}`,
        cls: "active",
        feature: "matrix",
        scroll: "matrix-sec-will",
      });
    }
    if (snap.privateCounts.zoneCount > 0) {
      chips.push({
        key: "zones",
        label: `Zones ${snap.privateCounts.zoneCount}`,
        cls: "active",
        feature: "matrix",
        scroll: "matrix-sec-place",
      });
    }
    if (snap.privateCounts.samplingCount > 0) {
      chips.push({
        key: "sampling",
        label: `Sampling ${snap.privateCounts.samplingCount}`,
        cls: "active",
        feature: "matrix",
        scroll: "matrix-sec-mind",
      });
    }
  }
  if (snap.activeEventCount > 0) {
    chips.push({
      key: "events",
      label: `Events ${snap.activeEventCount}`,
      cls: "active",
      feature: "laws",
      scroll: "godLawsActive",
    });
  }
  divineBarEffectsEl.innerHTML = chips.map((c) =>
    `<button type="button" class="divine-effect-chip ${c.cls}" data-effect="${escapeHtml(c.key)}" ` +
    `data-feature="${escapeHtml(c.feature)}" data-scroll="${escapeHtml(c.scroll || "")}" ` +
    `title="${escapeHtml(c.label)}">${escapeHtml(c.label)}</button>`
  ).join("");
}

function updateDivineBarPips() {
  if (!GOD_MODE_ENABLED_FLAG || !godEffectivelyAuthorized()) {
    [divineVoicePipEl, divineLawsPipEl, divineMatrixPipEl].forEach((el) => {
      if (!el) return;
      el.hidden = true;
      el.textContent = "";
      el.setAttribute("aria-hidden", "true");
    });
    return;
  }
  const snap = godBarSituationalSnapshot();
  if (divineVoicePipEl) {
    if (snap.voiceActivity > 0) {
      divineVoicePipEl.hidden = false;
      divineVoicePipEl.textContent = String(snap.voiceActivity);
      divineVoicePipEl.setAttribute("aria-hidden", "false");
    } else {
      divineVoicePipEl.hidden = true;
      divineVoicePipEl.textContent = "";
      divineVoicePipEl.setAttribute("aria-hidden", "true");
    }
  }
  if (divineLawsPipEl) {
    const n = snap.lawEventCount || snap.activeEventCount;
    if (n > 0) {
      divineLawsPipEl.hidden = false;
      divineLawsPipEl.textContent = String(n);
      divineLawsPipEl.setAttribute("aria-hidden", "false");
    } else {
      divineLawsPipEl.hidden = true;
      divineLawsPipEl.textContent = "";
      divineLawsPipEl.setAttribute("aria-hidden", "true");
    }
  }
  if (divineMatrixPipEl) {
    if (snap.matrixAggregate > 0) {
      divineMatrixPipEl.hidden = false;
      divineMatrixPipEl.textContent = String(snap.matrixAggregate);
      divineMatrixPipEl.setAttribute("aria-hidden", "false");
    } else {
      divineMatrixPipEl.hidden = true;
      divineMatrixPipEl.textContent = "";
      divineMatrixPipEl.setAttribute("aria-hidden", "true");
    }
  }
}

function updateDivineBarSituational() {
  renderDivineBarEffects();
  updateDivineBarPips();
}

function pulseDivineBar() {
  if (!divineBarEl) return;
  divineBarEl.classList.remove("divine-bar-pulse");
  void divineBarEl.offsetWidth;
  divineBarEl.classList.add("divine-bar-pulse");
  if (godBarPulseTimer) clearTimeout(godBarPulseTimer);
  godBarPulseTimer = setTimeout(() => {
    if (divineBarEl) divineBarEl.classList.remove("divine-bar-pulse");
    godBarPulseTimer = null;
  }, 950);
}

function divineModalIsOpen() {
  return !!(divineModalScrimEl && divineModalScrimEl.classList.contains("open"));
}

function godSightEagerRefreshModal() {
  const feat = divineModalOpenFeature;
  return feat === "sight" || feat === "voice";
}

async function maybeRefreshGodSight() {
  if (!GOD_MODE_ENABLED_FLAG || !godEffectivelyAuthorized()) return;
  const modalOpen = divineModalOpenFeature;
  if (modalOpen && !godSightEagerRefreshModal()) return;
  const throttleMs = godSightEagerRefreshModal()
    ? GOD_SIGHT_MODAL_REFRESH_MS
    : GOD_SIGHT_BAR_REFRESH_MS;
  const stale = !godLastSight || (Date.now() - godLastSightFetchedAt > throttleMs);
  if (!stale || godSightBarRefreshInFlight) return;
  godSightBarRefreshInFlight = true;
  try {
    await refreshGodSight();
  } finally {
    godSightBarRefreshInFlight = false;
  }
}

const GOD_ARCHITECT_ZONE_OVERLAY_COLORS = {
  paint: "rgba(255, 193, 58, 0.72)",
  door: "rgba(80, 220, 240, 0.72)",
  limbo: "rgba(180, 120, 255, 0.78)",
};

function drawDivineAgentRing(ctx, x, y, radius, stroke, lineWidth, dash) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y - 8, radius, 0, Math.PI * 2);
  ctx.strokeStyle = stroke;
  ctx.lineWidth = lineWidth;
  if (dash && dash.length) ctx.setLineDash(dash);
  ctx.stroke();
  ctx.restore();
}

function drawDivineSightOverlays(ctx, frameTick) {
  if (!GOD_MODE_ENABLED_FLAG || !divineModalIsOpen()) return;

  const focusId = godFocusAgentId != null ? godFocusAgentId : selectedAgentId;
  const worldAgents = getAgents();
  if (focusId != null) {
    const focusAgent = worldAgents.find((a) => a.id === focusId && !a.deceased);
    if (focusAgent) {
      const pulse = 0.85 + 0.15 * Math.sin((frameTick || 0) * 0.14);
      drawDivineAgentRing(
        ctx, focusAgent.x, focusAgent.y, 22 * pulse,
        "rgba(255, 210, 90, 0.95)", 2.5, null
      );
    }
  }

  if (!godEffectivelyAuthorized() || !godLastSight) return;

  const zones = godLastSight.architectZones || [];
  for (const zone of zones) {
    const bounds = zone.districtId ? getDistrictBounds(zone.districtId) : null;
    if (!bounds) continue;
    const color = GOD_ARCHITECT_ZONE_OVERLAY_COLORS[zone.kind] || "rgba(200, 200, 200, 0.55)";
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = zone.kind === "limbo" ? 3 : 2;
    ctx.setLineDash(zone.kind === "paint" ? [10, 6] : [6, 4]);
    ctx.strokeRect(
      bounds.x1, bounds.y1,
      bounds.x2 - bounds.x1, bounds.y2 - bounds.y1
    );
    ctx.restore();
  }

  const sightAgents = godLastSight.agents || [];
  for (const sa of sightAgents) {
    const agent = worldAgents.find((a) => a.id === sa.id);
    if (!agent || agent.deceased) continue;
    const limboHold = sa.divineHold || (sa.architectLimbo && sa.architectLimbo.active);
    if (limboHold) {
      const pulse = 0.9 + 0.1 * Math.sin((frameTick || 0) * 0.18 + sa.id);
      drawDivineAgentRing(
        ctx, agent.x, agent.y, 26 * pulse,
        "rgba(160, 90, 255, 0.88)", 2, [4, 3]
      );
    }
    if (sa.anointment && sa.anointment.active) {
      const pulse = 0.92 + 0.08 * Math.sin((frameTick || 0) * 0.12 + sa.id * 0.7);
      drawDivineAgentRing(
        ctx, agent.x, agent.y, 30 * pulse,
        "rgba(255, 170, 90, 0.75)", 1.5, null
      );
    }
  }
}

let divinePreviewController = null;
let divinePreviewOwnerForm = null;
let godLastAppliedPin = null;

const DIVINE_FAVORITES_KEY = "divineFavorites";
const DIVINE_FAVORITES_MAX = 4;

function godFormIsIrreversible(formEl) {
  return !!(formEl && formEl.classList.contains("divine-fieldset-irreversible"));
}

function godFormTargetAgentName(formEl) {
  if (!formEl) return null;
  const sel = formEl.querySelector("select[id*='Agent']");
  if (sel && sel.value) {
    const id = Number(sel.value);
    const agent = getLivingAgents().find((a) => a.id === id);
    if (agent) return agent.name;
  }
  const pref = godPreferredAgentId();
  if (pref != null) {
    const agent = getLivingAgents().find((a) => a.id === pref);
    if (agent) return agent.name;
  }
  return null;
}

function godIrreversibleConfirmTextMatches(formEl) {
  const expected = godFormTargetAgentName(formEl);
  if (!expected) return false;
  const input = document.getElementById("divineIrreversibleConfirmInput");
  if (!input) return false;
  return input.value.trim().toLowerCase() === expected.trim().toLowerCase();
}

function godResolveIrreversibleForm(formEl, getOwnerForm) {
  if (formEl) return formEl;
  if (typeof getOwnerForm === "function") return getOwnerForm();
  return divinePreviewOwnerForm;
}

function godBindIrreversibleApply(btn, formEl, applyFn, hintEl, getOwnerForm) {
  if (!btn) return;
  let holdTimer = null;
  let appliedViaHold = false;

  function ownerForm() {
    return godResolveIrreversibleForm(formEl, getOwnerForm);
  }

  function clearHold() {
    if (holdTimer) {
      clearTimeout(holdTimer);
      holdTimer = null;
    }
    btn.classList.remove("divine-apply-holding");
  }

  function showHint() {
    if (!hintEl) return;
    const name = godFormTargetAgentName(ownerForm());
    renderGodError(hintEl, name
      ? `Irreversible: hold Apply ~0.4s or type "${name}" in the confirm field.`
      : "Irreversible: hold Apply ~0.4s to confirm.");
  }

  function guardedApply(fromHold) {
    const owner = ownerForm();
    if (!godFormIsIrreversible(owner)) {
      applyFn();
      return;
    }
    if (fromHold || godIrreversibleConfirmTextMatches(owner)) {
      clearHold();
      applyFn();
      return;
    }
    showHint();
  }

  btn.addEventListener("mousedown", (e) => {
    if (e.button !== 0 || btn.disabled) return;
    const owner = ownerForm();
    if (!godFormIsIrreversible(owner)) return;
    if (godIrreversibleConfirmTextMatches(owner)) return;
    appliedViaHold = false;
    btn.classList.add("divine-apply-holding");
    holdTimer = setTimeout(() => {
      holdTimer = null;
      appliedViaHold = true;
      btn.classList.remove("divine-apply-holding");
      if (!btn.disabled) applyFn();
    }, 400);
  });
  btn.addEventListener("mouseup", clearHold);
  btn.addEventListener("mouseleave", clearHold);
  btn.addEventListener("click", (e) => {
    if (btn.disabled) return;
    const owner = ownerForm();
    if (!godFormIsIrreversible(owner)) {
      applyFn();
      return;
    }
    if (appliedViaHold) {
      appliedViaHold = false;
      e.preventDefault();
      return;
    }
    if (godIrreversibleConfirmTextMatches(owner)) {
      applyFn();
      return;
    }
    e.preventDefault();
    showHint();
  });
}

function loadDivineFavorites() {
  try {
    const raw = sessionStorage.getItem(DIVINE_FAVORITES_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, DIVINE_FAVORITES_MAX) : [];
  } catch (err) {
    return [];
  }
}

function saveDivineFavorites(favs) {
  try {
    sessionStorage.setItem(DIVINE_FAVORITES_KEY, JSON.stringify(favs.slice(0, DIVINE_FAVORITES_MAX)));
  } catch (err) { /* ignore */ }
  renderDivineFavoritesBar();
}

function renderDivineFavoritesBar() {
  const el = document.getElementById("divineBarFavorites");
  if (!el) return;
  const favs = loadDivineFavorites();
  if (!favs.length) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = favs.map((f, i) =>
    `<button type="button" class="divine-fav-chip" data-fav-index="${i}" title="${escapeHtml(f.label || f.feature)}">${escapeHtml(f.label || f.feature)}</button>`
  ).join("");
}

function godDetectModalScrollTarget() {
  if (!divineModalBodyEl) return { fieldsetId: null, sectionId: null, label: null };
  const scrollRoot = divineModalBodyEl;
  const rootRect = scrollRoot.getBoundingClientRect();
  const candidates = scrollRoot.querySelectorAll(".divine-fieldset[id], .divine-matrix-section[id], .divine-voice-adherence-section");
  let best = null;
  let bestTop = Infinity;
  candidates.forEach((node) => {
    const r = node.getBoundingClientRect();
    if (r.bottom < rootRect.top + 40 || r.top > rootRect.bottom) return;
    if (r.top < bestTop) {
      bestTop = r.top;
      best = node;
    }
  });
  if (!best) return { fieldsetId: null, sectionId: null, label: DIVINE_FEATURES[divineModalOpenFeature]?.title || divineModalOpenFeature };
  const fieldsetId = best.classList.contains("divine-fieldset") ? best.id : null;
  const sectionId = best.classList.contains("divine-matrix-section") ? best.id : null;
  let label = best.querySelector("legend")?.textContent?.trim()
    || best.querySelector(".divine-section-title")?.textContent?.trim();
  if (!label) label = DIVINE_FEATURES[divineModalOpenFeature]?.title || divineModalOpenFeature;
  return {
    fieldsetId: fieldsetId || (best.closest(".divine-fieldset") && best.closest(".divine-fieldset").id) || null,
    sectionId,
    label: String(label).slice(0, 48),
  };
}

function pinDivineFavoriteFromModal() {
  if (!divineModalOpenFeature) return;
  const target = godDetectModalScrollTarget();
  const fav = {
    feature: divineModalOpenFeature,
    label: target.label || DIVINE_FEATURES[divineModalOpenFeature]?.title || divineModalOpenFeature,
  };
  if (target.fieldsetId) fav.fieldsetId = target.fieldsetId;
  else if (target.sectionId) fav.fieldsetId = target.sectionId;
  const favs = loadDivineFavorites().filter((f) =>
    !(f.feature === fav.feature && f.fieldsetId === fav.fieldsetId)
  );
  favs.unshift(fav);
  saveDivineFavorites(favs.slice(0, DIVINE_FAVORITES_MAX));
}

function openDivineFavorite(fav) {
  if (!fav || !fav.feature) return;
  openDivineModal(fav.feature);
  godScrollDivineFieldset(fav.fieldsetId);
}

function godVisibleBarFeatures() {
  if (!divineBarEl) return [];
  return Array.from(divineBarEl.querySelectorAll(".gbtn")).filter((btn) => {
    if (btn.style.display === "none") return false;
    if (btn.classList.contains("unlock") && !GOD_AUTH_REQUIRED_FLAG) return false;
    if (btn.classList.contains("compile") && btn.id === "godCompileTabBtn" && btn.style.display === "none") return false;
    return true;
  }).map((btn) => btn.dataset.feature).filter(Boolean);
}

function divineModalTypingTarget() {
  const ae = document.activeElement;
  if (!ae) return false;
  const tag = ae.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

function clearDivinePreviewStrip() {
  divinePreviewController = null;
  divinePreviewOwnerForm = null;
  if (divinePreviewStripEl) divinePreviewStripEl.classList.remove("visible");
  const confirmWrap = document.getElementById("divineIrreversibleConfirmWrap");
  const confirmInput = document.getElementById("divineIrreversibleConfirmInput");
  if (confirmWrap) confirmWrap.hidden = true;
  if (confirmInput) confirmInput.value = "";
}

function updateDivineIrreversibleConfirmUi(formEl) {
  const wrap = document.getElementById("divineIrreversibleConfirmWrap");
  const input = document.getElementById("divineIrreversibleConfirmInput");
  if (!wrap || !input) return;
  const irreversible = formEl && formEl.classList.contains("divine-fieldset-irreversible");
  const name = irreversible ? godFormTargetAgentName(formEl) : null;
  wrap.hidden = !irreversible;
  input.placeholder = name ? `Type "${name}" to apply` : "Hold Apply ~0.4s";
  if (!irreversible) input.value = "";
}

function showDivinePreviewStrip(label, data, applyFn, discardFn, ownerForm) {
  if (!divinePreviewStripEl) return;
  divinePreviewController = { label, data, applyFn, discardFn };
  divinePreviewOwnerForm = ownerForm || null;
  updateDivineIrreversibleConfirmUi(ownerForm);
  if (divinePreviewStripLabelEl) {
    const kind = data.normalizedCommand && data.normalizedCommand.kind;
    let html = godReversibilityBadge(data.reversibilityClass) +
      `<span>${escapeHtml(label || kind || "command")} — preview ready</span>`;
    html += renderGodPreviewWarningsHtml(data.warnings);
    divinePreviewStripLabelEl.innerHTML = html;
  }
  divinePreviewStripEl.classList.add("visible");
}

function renderGodPinRow() {
  if (!divineModalBodyEl) return;
  let pin = document.getElementById("divinePinRow");
  if (!godLastAppliedPin && !godFindRevokeTarget()) {
    if (pin) pin.remove();
    return;
  }
  if (!pin) {
    pin = document.createElement("div");
    pin.id = "divinePinRow";
    pin.className = "divine-pin-row";
    const guide = document.getElementById("divineFeatureGuide");
    divineModalBodyEl.insertBefore(pin, guide ? guide.nextSibling : divineModalBodyEl.firstChild);
  }
  let html = "";
  if (godLastAppliedPin) {
    const p = godLastAppliedPin;
    html += `Last applied: <strong>${escapeHtml(p.label)}</strong> (id ${escapeHtml(String(p.id))}) — ` +
      `<a href="#" id="divinePinHistoryLink">View in History</a>`;
  }
  const revokeTarget = godFindRevokeTarget();
  if (revokeTarget) {
    html += ` <button type="button" class="divine-pin-revoke" id="divinePinRevokeBtn" ` +
      `data-id="${escapeHtml(String(revokeTarget.id))}">Revoke last cancellable</button>`;
  }
  pin.innerHTML = html;
  const link = document.getElementById("divinePinHistoryLink");
  if (link) {
    link.addEventListener("click", (e) => {
      e.preventDefault();
      openDivineModal("history");
    });
  }
  const revokeBtn = document.getElementById("divinePinRevokeBtn");
  if (revokeBtn) {
    revokeBtn.addEventListener("click", async () => {
      revokeBtn.disabled = true;
      const resp = await godCancelEffect(revokeBtn.dataset.id);
      if (!resp.data || !resp.data.cancelled) {
        revokeBtn.disabled = false;
        const reason = (resp.data && resp.data.reason) || "nothing to cancel";
        window.alert(`Revoke failed: ${reason}`);
      } else {
        renderGodPinRow();
        if (godActiveTab === "history") renderGodHistory();
        if (godActiveTab === "laws") renderGodLawsActive();
      }
    });
  }
}

if (divinePreviewApplyBtnEl) {
  godBindIrreversibleApply(divinePreviewApplyBtnEl, null, () => {
    if (divinePreviewController && divinePreviewController.applyFn) divinePreviewController.applyFn();
  }, null, () => divinePreviewOwnerForm);
}
if (divinePreviewDiscardBtnEl) {
  divinePreviewDiscardBtnEl.addEventListener("click", () => {
    if (divinePreviewController && divinePreviewController.discardFn) divinePreviewController.discardFn();
    clearDivinePreviewStrip();
  });
}

function updateDivineBarAuthUi() {
  if (divineBarBrandStateEl) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      divineBarBrandStateEl.textContent = "open";
      divineBarBrandStateEl.style.color = "#a5d6a7";
    } else {
      divineBarBrandStateEl.textContent = godAuthorized ? "authorized" : "locked";
      divineBarBrandStateEl.style.color = godAuthorized ? "#a5d6a7" : "#6f6f7c";
    }
  }
  const unlockBtn = divineBarEl && divineBarEl.querySelector(".gbtn.unlock");
  if (unlockBtn) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      unlockBtn.style.display = "none";
    } else {
      unlockBtn.style.display = "";
      unlockBtn.dataset.state = godAuthorized ? "unlocked" : "locked";
    }
  }
  if (divineUnlockPipEl) {
    if (!GOD_AUTH_REQUIRED_FLAG) {
      divineUnlockPipEl.style.display = "none";
      divineUnlockPipEl.textContent = "";
    } else {
      divineUnlockPipEl.style.display = "";
      divineUnlockPipEl.textContent = godAuthorized ? "ready" : "locked";
    }
  }
  if (divineBarInterventionCountEl) {
    if (GOD_MODE_ENABLED_FLAG) {
      const n = godInterventionCount();
      divineBarInterventionCountEl.textContent = n ? `${n} intervention${n === 1 ? "" : "s"}` : "no interventions yet";
      divineBarInterventionCountEl.style.display = "";
    } else {
      divineBarInterventionCountEl.textContent = "";
      divineBarInterventionCountEl.style.display = "none";
    }
  }
  if (!divineBarEl) return;
  const effective = godEffectivelyAuthorized();
  divineBarEl.querySelectorAll(".gbtn.locked-dependent").forEach((btn) => {
    // Compile stays dual-gated by capabilities display; still lock-disable when unauthorized.
    btn.disabled = !effective;
  });
  updateDivineBarSituational();
}

function godLockConsole(message) {
  if (!GOD_AUTH_REQUIRED_FLAG) {
    console.warn(message || "godLockConsole ignored — Divine Console is in open mode (GOD_AUTH_REQUIRED off).");
    return;
  }
  godToken = null;
  godAuthorized = false;
  godCapabilities = null;
  godLastSight = null;
  godLastSightFetchedAt = 0;
  try { sessionStorage.removeItem("godToken"); } catch (err) { /* ignore */ }
  updateGodAuthStatus(message || "Locked — enter your secret token.", "divine-status-locked");
  updateDivineBarAuthUi();
  if (GOD_MODE_ENABLED_FLAG) openDivineModal("unlock");
  else {
    godActiveTab = "unlock";
    closeDivineModal();
  }
}

let godOpenModeBootstrapped = false;
async function godOpenModeBootstrap() {
  if (GOD_AUTH_REQUIRED_FLAG || godOpenModeBootstrapped || !GOD_MODE_ENABLED_FLAG) return;
  godOpenModeBootstrapped = true;
  godAuthorized = true;
  updateDivineBarAuthUi();
  const resp = await godApiFetch("/control/god/capabilities");
  if (resp.ok && resp.data && resp.data.ok) {
    godCapabilities = resp.data;
    applyGodCapabilitiesToForms();
    populateGodAgentSelects();
  }
}

async function godConnect(tokenValue) {
  if (!tokenValue) {
    updateGodAuthStatus("Enter your secret token first.", "divine-status-locked");
    return;
  }
  godToken = tokenValue;
  const resp = await godApiFetch("/control/god/capabilities");
  if (resp.ok && resp.data && resp.data.ok) {
    godAuthorized = true;
    godCapabilities = resp.data;
    updateGodAuthStatus("Connected — Divine tools unlocked.", "divine-status-ok");
    updateDivineBarAuthUi();
    applyGodCapabilitiesToForms();
    populateGodAgentSelects();
    showGodTab("sight");
  } else {
    godToken = null;
    godAuthorized = false;
    updateDivineBarAuthUi();
    updateGodAuthStatus("Could not connect — check your token.", "divine-status-locked");
  }
}

godConnectBtn.addEventListener("click", () => godConnect(godTokenInput.value.trim()));

// Apply capabilities-reported bounds/defaults to number/text inputs so the
// forms are driven by the server's allowlist rather than hardcoded twins.
function applyGodCapabilitiesToForms() {
  if (!godCapabilities) return;
  const kinds = godCapabilities.kinds || {};
  const setBounds = (id, field) => {
    const el = document.getElementById(id);
    if (!el || !field) return;
    if (field.min != null) el.min = field.min;
    if (field.max != null) el.max = field.max;
    if (field.default != null && !el.value) el.value = field.default;
    if (field.maxChars != null) el.maxLength = field.maxChars;
  };
  const setDurationBounds = (id, framesField) => {
    if (!framesField) return;
    setBounds(id, {
      min: framesField.min != null ? godFramesToSeconds(framesField.min) : undefined,
      max: framesField.max != null ? godFramesToSeconds(framesField.max) : undefined,
      default: framesField.default != null ? godFramesToSeconds(framesField.default) : undefined,
    });
  };
  const prov = (kinds.providence || {}).payload || {};
  setDurationBounds("godProvDuration", prov.durationFrames);
  setBounds("godProvText", prov.text);
  const omen = (kinds.private_omen || {}).payload || {};
  setDurationBounds("godOmenDuration", omen.durationFrames);
  setBounds("godOmenText", omen.text);
  const proc = (kinds.proclamation || {}).payload || {};
  setBounds("godProcText", proc.text);
  const vitals = (kinds.agent_vitals || {}).payload || {};
  if (vitals.healthDelta) { setBounds("godVitalsHealth", vitals.healthDelta); }
  if (vitals.hungerDelta) { setBounds("godVitalsHunger", vitals.hungerDelta); }
  const grant = (kinds.grant_resource || {}).payload || {};
  setBounds("godGrantAmount", grant.amount);
  const structure = (kinds.structure_condition || {}).payload || {};
  setBounds("godStructureDelta", structure.delta);
  const story = (kinds.story_event || {}).payload || {};
  setBounds("godStoryTitle", story.title);
  setBounds("godStoryNarration", story.narration);
  setDurationBounds("godStoryDuration", story.durationFrames);
  setDurationBounds("godLawDuration", story.durationFrames);
  const sampling = (kinds.agent_sampling || {}).payload || {};
  setDurationBounds("godSamplingDuration", sampling.durationFrames);
  if (sampling.temperature) {
    setBounds("godSamplingTemp", sampling.temperature);
    const tempEl = document.getElementById("godSamplingTemp");
    const outEl = document.getElementById("godSamplingTempOut");
    if (tempEl && outEl) outEl.textContent = tempEl.value;
  }
  if (sampling.top_p) setBounds("godSamplingTopP", sampling.top_p);
  if (sampling.top_k) setBounds("godSamplingTopK", sampling.top_k);
  if (sampling.min_p) setBounds("godSamplingMinP", sampling.min_p);
  renderGodModifierEditor("godStoryModifiers", "gs");
  renderGodModifierEditor("godLawModifiers", "gl");
  godInitStoryRecipeSelect();
  applyGodDejaVuAvailability();
  const dejaVu = (kinds.deja_vu_replay || {}).payload || {};
  setBounds("godDejaVuMaxSteps", dejaVu.maxSteps);
  // Sovereign God mode Optional Phase 8: Compile tab dual-gated via
  // capabilities.compiler.enabled (GOD_MODE_ENABLED AND GOD_COMPILER_ENABLED).
  const compiler = godCapabilities.compiler || {};
  const compileTabBtn = document.getElementById("godCompileTabBtn");
  if (compileTabBtn) compileTabBtn.style.display = compiler.enabled ? "" : "none";
  const proseEl = document.getElementById("godCompileProse");
  if (proseEl && compiler.promptMaxChars) proseEl.maxLength = compiler.promptMaxChars;
  godCompilerMinIntervalSec = compiler.minIntervalSec || 5;
  if (!compiler.enabled && godActiveTab === "compile") showGodTab("sight");
}

