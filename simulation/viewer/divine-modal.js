// --- sessionStorage "remember for this tab" (never localStorage) ------
if (GOD_AUTH_REQUIRED_FLAG) {
  (function restoreGodTokenFromSession() {
    try {
      const stored = sessionStorage.getItem("godToken");
      if (stored) {
        godTokenInput.value = stored;
        godRememberCheckbox.checked = true;
      }
    } catch (err) { /* ignore */ }
  })();
  godRememberCheckbox.addEventListener("change", () => {
    if (!godRememberCheckbox.checked) {
      try { sessionStorage.removeItem("godToken"); } catch (err) { /* ignore */ }
    } else if (godToken) {
      try { sessionStorage.setItem("godToken", godToken); } catch (err) { /* ignore */ }
    }
  });
  // Re-derive the "remember" persistence on every successful connect (not just
  // the change event) so a token typed AFTER checking the box is still saved.
  const _godConnectOriginal = godConnect;
  godConnect = async function (tokenValue) {
    await _godConnectOriginal(tokenValue);
    if (godAuthorized && godRememberCheckbox.checked) {
      try { sessionStorage.setItem("godToken", tokenValue); } catch (err) { /* ignore */ }
    }
  };
}

// --- Bottom bar + modal (relocated from sidebar tabs) --------------------
const GOD_TABS = ["unlock", "sight", "voice", "matrix", "miracles", "story", "laws", "history", "compile"];
const DIVINE_WIDE_MODAL_FEATURES = new Set(["matrix", "story", "laws", "compile"]);
const godBarButtons = Array.from(document.querySelectorAll("#divineBar .gbtn"));
let divineModalOpenFeature = null;

function applyDivinePlainTips() {
  syncDivineBarTooltips();
  const hold = document.getElementById("divineTabHold");
  const previewJson = JSON.stringify(DIVINE_PREVIEW_TIP);
  const applyJson = JSON.stringify(DIVINE_APPLY_TIP);
  const applyIrrevJson = JSON.stringify(DIVINE_APPLY_IRREVERSIBLE_TIP);
  const irrevApplyFieldsets = new Set([
    "godVitalsFieldset", "godGrantFieldset", "godStructureFieldset",
    "godMassRepairFieldset", "godClearRuinsFieldset",
  ]);
  if (hold) {
    hold.querySelectorAll("[id$='PreviewBtn']").forEach((btn) => btn.setAttribute("data-tip", previewJson));
    hold.querySelectorAll("[id$='ApplyBtn']").forEach((btn) => {
      const fs = btn.closest("fieldset");
      const irrev = fs && (fs.classList.contains("divine-fieldset-irreversible") || irrevApplyFieldsets.has(fs.id));
      btn.setAttribute("data-tip", irrev ? applyIrrevJson : applyJson);
    });
  }
  const agentFilter = document.getElementById("godAgentFilterWrap");
  if (agentFilter) {
    agentFilter.setAttribute("data-tip", JSON.stringify({
      t: "Villager filter",
      d: "Narrows villager dropdowns by name or role. Press / while the modal is open to focus.",
    }));
  }
  const pinBtn = document.getElementById("divinePinSectionBtn");
  if (pinBtn) {
    pinBtn.setAttribute("data-tip", JSON.stringify({
      t: "Pin this section",
      d: "Save a shortcut to this tool (max 4, remembered until you close this browser tab).",
    }));
  }
  const matrixPin = document.getElementById("divineMatrixPinBtn");
  if (matrixPin) {
    matrixPin.setAttribute("data-tip", JSON.stringify({
      t: "Pin Matrix section",
      d: "Pin the Matrix section currently near the top of the scroll area.",
    }));
  }
  const stripApply = document.getElementById("divinePreviewApplyBtn");
  if (stripApply) {
    stripApply.setAttribute("data-tip", JSON.stringify({
      t: "Apply",
      d: "Make it real (uses the last successful check).",
    }));
  }
  const stripDiscard = document.getElementById("divinePreviewDiscardBtn");
  if (stripDiscard) {
    stripDiscard.setAttribute("data-tip", JSON.stringify({
      t: "Discard",
      d: "Clear the last successful check without changing the village.",
    }));
  }
}
applyDivinePlainTips();

function wireDivineMatrixNav() {
  const nav = document.querySelector("#divineTab-matrix .divine-matrix-nav");
  if (!nav || nav.dataset.wired) return;
  nav.dataset.wired = "1";
  nav.addEventListener("click", (e) => {
    const chip = e.target.closest(".divine-matrix-chip");
    if (!chip) return;
    const sec = document.getElementById(chip.dataset.matrixSection || "");
    if (sec) sec.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
wireDivineMatrixNav();

const divineBarFavoritesEl = document.getElementById("divineBarFavorites");
if (divineBarFavoritesEl) {
  divineBarFavoritesEl.addEventListener("click", (e) => {
    const chip = e.target.closest(".divine-fav-chip");
    if (!chip) return;
    const idx = Number(chip.dataset.favIndex);
    const favs = loadDivineFavorites();
    if (favs[idx]) openDivineFavorite(favs[idx]);
  });
}
if (divineBarEffectsEl) {
  divineBarEffectsEl.addEventListener("click", (e) => {
    const chip = e.target.closest(".divine-effect-chip");
    if (!chip) return;
    openDivineBarEffectTarget(chip.dataset.feature, chip.dataset.scroll || null);
  });
}
const divinePinSectionBtn = document.getElementById("divinePinSectionBtn");
if (divinePinSectionBtn) divinePinSectionBtn.addEventListener("click", pinDivineFavoriteFromModal);
const divineMatrixPinBtn = document.getElementById("divineMatrixPinBtn");
if (divineMatrixPinBtn) divineMatrixPinBtn.addEventListener("click", pinDivineFavoriteFromModal);
const godAgentFilterEl = document.getElementById("godAgentFilter");
if (godAgentFilterEl) {
  godAgentFilterEl.addEventListener("input", () => populateGodAgentSelects());
}
document.addEventListener("dblclick", (e) => {
  const legend = e.target.closest(".divine-fieldset legend[data-fav]");
  if (!legend) return;
  const fieldset = legend.closest(".divine-fieldset");
  if (!fieldset || !divineModalOpenFeature) return;
  const fav = {
    feature: divineModalOpenFeature,
    fieldsetId: fieldset.id || undefined,
    label: (legend.textContent || "").trim().slice(0, 48) || fieldset.id,
  };
  const favs = loadDivineFavorites().filter((f) =>
    !(f.feature === fav.feature && f.fieldsetId === fav.fieldsetId)
  );
  favs.unshift(fav);
  saveDivineFavorites(favs.slice(0, DIVINE_FAVORITES_MAX));
});
document.querySelectorAll(".divine-fieldset legend").forEach((lg) => {
  if (!lg.hasAttribute("data-fav")) lg.setAttribute("data-fav", "1");
});
renderDivineFavoritesBar();

function closeDivineModal() {
  clearDivineFeatureGuide();
  if (divineModalOpenFeature) {
    const panel = document.getElementById("divineTab-" + divineModalOpenFeature);
    if (panel && divineTabHoldEl) divineTabHoldEl.appendChild(panel);
    divineModalOpenFeature = null;
  }
  if (divineModalEl) divineModalEl.classList.remove("wide");
  if (divineModalScrimEl) divineModalScrimEl.classList.remove("open");
  godBarButtons.forEach((btn) => btn.classList.remove("active"));
  hideDivineTip();
  clearDivinePreviewStrip();
}

function openDivineModal(name) {
  if (GOD_TABS.indexOf(name) === -1) name = "unlock";
  const feature = DIVINE_FEATURES[name] || DIVINE_FEATURES.unlock;
  // Reparent any currently open panel back to the hold first.
  if (divineModalOpenFeature && divineModalOpenFeature !== name) {
    const prev = document.getElementById("divineTab-" + divineModalOpenFeature);
    if (prev && divineTabHoldEl) {
      prev.style.display = "none";
      divineTabHoldEl.appendChild(prev);
    }
  }
  godActiveTab = name;
  godBarButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.feature === name));
  if (divineModalIconEl) divineModalIconEl.innerHTML = DIVINE_FEATURE_ICONS[name] || "";
  if (divineModalTitleEl) divineModalTitleEl.textContent = feature.title;
  if (divineModalSubEl) divineModalSubEl.textContent = feature.sub;
  const panel = document.getElementById("divineTab-" + name);
  if (panel && divineModalBodyEl) {
    divineModalBodyEl.appendChild(panel);
    panel.style.display = "";
  }
  // Hide siblings still in the hold (not the open panel).
  GOD_TABS.forEach((t) => {
    if (t === name) return;
    const el = document.getElementById("divineTab-" + t);
    if (el && el.parentElement === divineTabHoldEl) el.style.display = "none";
  });
  divineModalOpenFeature = name;
  if (divineModalEl) divineModalEl.classList.toggle("wide", DIVINE_WIDE_MODAL_FEATURES.has(name));
  if (divineModalScrimEl) divineModalScrimEl.classList.add("open");
  renderDivineFeatureGuide(name);
  if (name === "sight" && godEffectivelyAuthorized()) refreshGodSight();
  if (name === "voice" && godEffectivelyAuthorized()) {
    if (godLastSight) renderGodVoiceAdherence();
    else refreshGodSight();
  }
  if (name === "laws") renderGodLawsActive();
  if (name === "history") renderGodHistory();
  renderGodPinRow();
  reorderDivineModalBodyChildren();
}

// Thin alias: existing callers (connect→sight, lock→unlock, compile→story) open the modal.
function showGodTab(name) {
  openDivineModal(name);
}

godBarButtons.forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.disabled) return;
    openDivineModal(btn.dataset.feature);
  });
});
const divineModalCloseBtn = document.getElementById("divineModalClose");
if (divineModalCloseBtn) divineModalCloseBtn.addEventListener("click", closeDivineModal);
if (divineModalScrimEl) {
  divineModalScrimEl.addEventListener("click", (e) => {
    if (e.target === divineModalScrimEl) closeDivineModal();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && divineModalScrimEl && divineModalScrimEl.classList.contains("open")) {
    closeDivineModal();
    return;
  }
  const modalOpen = divineModalScrimEl && divineModalScrimEl.classList.contains("open");
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && modalOpen && divinePreviewController) {
    e.preventDefault();
    const owner = divinePreviewOwnerForm;
    if (godFormIsIrreversible(owner) && !godIrreversibleConfirmTextMatches(owner)) {
      return;
    }
    divinePreviewController.applyFn();
    return;
  }
  if (!modalOpen) return;
  if (divineModalTypingTarget()) return;
  if (e.key === "/") {
    e.preventDefault();
    const filter = document.getElementById("godAgentFilter");
    if (filter) filter.focus();
    return;
  }
  if ((e.key === "s" || e.key === "S") && godEffectivelyAuthorized()) {
    e.preventDefault();
    refreshGodSight();
    return;
  }
  const digit = parseInt(e.key, 10);
  if (digit >= 1 && digit <= 9) {
    const features = godVisibleBarFeatures();
    const feature = features[digit - 1];
    if (feature) {
      e.preventDefault();
      const btn = divineBarEl && divineBarEl.querySelector(`.gbtn[data-feature="${feature}"]`);
      if (btn && !btn.disabled) openDivineModal(feature);
    }
  }
});

// --- Shared tooltip engine (delegated; safe for late-rendered sight/history) ---
let divineTipHideTimer = null;
function hideDivineTip() {
  if (divineTooltipEl) divineTooltipEl.classList.remove("show");
}
function showDivineTip(el) {
  if (!divineTooltipEl || !el) return;
  let raw = el.getAttribute("data-tip");
  if (!raw) return;
  let data;
  try {
    data = JSON.parse(raw.replace(/&#39;/g, "'"));
  } catch (err) {
    return;
  }
  const title = data && data.t != null ? String(data.t) : "";
  const desc = data && data.d != null ? String(data.d) : "";
  divineTooltipEl.innerHTML = '<span class="t-title">' + escapeHtml(title) + "</span>" + escapeHtml(desc);
  divineTooltipEl.classList.add("show");
  const r = el.getBoundingClientRect();
  const tw = divineTooltipEl.offsetWidth;
  const th = divineTooltipEl.offsetHeight;
  let x = r.left + r.width / 2 - tw / 2;
  let y = r.top - th - 8;
  if (y < 8) y = r.bottom + 8;
  x = Math.max(8, Math.min(x, window.innerWidth - tw - 8));
  divineTooltipEl.style.left = x + "px";
  divineTooltipEl.style.top = y + "px";
}
document.addEventListener("mouseenter", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (!el) return;
  clearTimeout(divineTipHideTimer);
  showDivineTip(el);
}, true);
document.addEventListener("mouseleave", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (!el) return;
  divineTipHideTimer = setTimeout(hideDivineTip, 60);
}, true);
document.addEventListener("focusin", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (el) showDivineTip(el);
});
document.addEventListener("focusout", (e) => {
  const el = e.target && e.target.closest && e.target.closest("[data-tip]");
  if (el) hideDivineTip();
});

if (!GOD_AUTH_REQUIRED_FLAG) godAuthorized = true;
updateDivineBarAuthUi();
["godVitalsFieldset", "godGrantFieldset", "godStructureFieldset", "godMassRepairFieldset", "godClearRuinsFieldset"]
  .forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.classList.add("divine-fieldset-irreversible");
  });

// --- Preview -> Apply generic wiring ------------------------------------
// One reusable helper per mutating form (docs/plan: "no Apply before a
// successful preview; any field edit invalidates it"). `resultEl` renders
// via escapeHtml()-composed HTML fragments only -- normalizedCommand itself
// is never inserted into innerHTML (rule: stored-content safety).
const godDivineFormControllers = {};

function wireDivineForm(formSelector, opts) {
  const formEl = document.querySelector(formSelector);
  const previewBtn = document.getElementById(opts.previewBtnId);
  const applyBtn = document.getElementById(opts.applyBtnId);
  const resultEl = document.getElementById(opts.resultElId);
  let previewState = null;

  function invalidate() {
    previewState = null;
    applyBtn.disabled = true;
    if (divinePreviewOwnerForm === formEl) clearDivinePreviewStrip();
  }
  formEl.addEventListener("input", invalidate);
  formEl.addEventListener("change", invalidate);

  async function doApply() {
    if (!previewState) return;
    applyBtn.disabled = true;
    const requestId = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : ("req-" + Date.now() + "-" + Math.random().toString(36).slice(2));
    const resp = await godApiFetch("/control/god/apply", {
      method: "POST",
      body: { previewId: previewState.previewId, requestId },
    });
    previewState = null;
    clearDivinePreviewStrip();
    if (!resp.data || !resp.data.ok) {
      renderGodError(resultEl, (resp.data && resp.data.reason) || "apply failed");
      return;
    }
    resultEl.innerHTML = renderGodAppliedHtml(resp.data);
    godLastAppliedPin = {
      label: opts.label || (resp.data.outcome && resp.data.outcome.kind) || "intervention",
      id: resp.data.interventionId,
    };
    renderGodPinRow();
    if (opts.onApplied) opts.onApplied(resp.data);
    refreshGodSightIfOpen();
  }

  function acceptServerPreview(data) {
    if (!data || !data.previewId) return;
    previewState = data;
    resultEl.innerHTML = renderGodPreviewHtml(previewState, opts.label);
    applyBtn.disabled = false;
    divinePreviewOwnerForm = formEl;
    showDivinePreviewStrip(opts.label, previewState, doApply, invalidate, formEl);
  }

  godDivineFormControllers[formSelector] = { invalidate, acceptServerPreview };

  previewBtn.addEventListener("click", async () => {
    invalidate();
    const built = opts.buildEnvelope();
    if (!built || built.error) {
      renderGodError(resultEl, (built && built.error) || "invalid form input");
      return;
    }
    const resp = await godApiFetch("/control/god/preview", { method: "POST", body: built.envelope });
    if (!resp.data || !resp.data.ok) {
      renderGodError(resultEl, (resp.data && resp.data.reason) || "preview failed");
      if (opts.onPreviewRejected) opts.onPreviewRejected(resp.data && resp.data.reason);
      return;
    }
    previewState = resp.data;
    resultEl.innerHTML = renderGodPreviewHtml(resp.data, opts.label);
    applyBtn.disabled = false;
    divinePreviewOwnerForm = formEl;
    showDivinePreviewStrip(opts.label, resp.data, doApply, invalidate, formEl);
  });

  godBindIrreversibleApply(applyBtn, formEl, doApply, resultEl);
}

function refreshGodSightIfOpen() {
  if ((godActiveTab === "sight" || godActiveTab === "voice") && godEffectivelyAuthorized()) refreshGodSight();
  if (godActiveTab === "laws") renderGodLawsActive();
}

function renderGodError(el, message) {
  el.innerHTML = `<div class="divine-error">${escapeHtml(String(message))}</div>`;
}

function godReversibilityBadge(cls) {
  const norm = String(cls || "irreversible");
  const label = norm === "irreversible" ? "IRREVERSIBLE" : norm === "consequential" ? "CONSEQUENTIAL" : "CANCELLABLE";
  const css = norm === "irreversible" ? "divine-badge-irreversible"
    : norm === "consequential" ? "divine-badge-consequential" : "divine-badge-cancellable";
  return `<span class="divine-badge ${css}">${escapeHtml(label)}</span>`;
}

// Every value inserted below is either a known numeric/boolean field or run
// through escapeHtml() -- never the raw normalizedCommand object.
function renderGodPreviewOutcomeHtml(kind, outcome) {
  if (!outcome) return "";
  const rows = [];
  if (kind === "agent_vitals") {
    rows.push(`Target: ${escapeHtml(String(outcome.targetName))} (#${escapeHtml(String(outcome.targetId))})`);
    rows.push(`Health: ${escapeHtml(String(outcome.oldHealth))} &rarr; ${escapeHtml(String(outcome.newHealth))}`);
    rows.push(`Hunger: ${escapeHtml(String(outcome.oldHunger))} &rarr; ${escapeHtml(String(outcome.newHunger))}`);
  } else if (kind === "grant_resource") {
    rows.push(`Resource: ${escapeHtml(String(outcome.resourceId))} &times; ${escapeHtml(String(outcome.amount))}`);
    if (outcome.targetKind === "agent") {
      rows.push(`To ${escapeHtml(String(outcome.targetName))}: +${escapeHtml(String(outcome.agentAdded))} carried` +
        (outcome.stockpileAdded ? `, +${escapeHtml(String(outcome.stockpileAdded))} overflow to stockpile` : ""));
    } else {
      rows.push(`To village stockpile: +${escapeHtml(String(outcome.stockpileAdded))}`);
    }
  } else if (kind === "structure_condition") {
    rows.push(`Structure: ${escapeHtml(String(outcome.structureName))} (#${escapeHtml(String(outcome.structureId))})`);
    rows.push(`Condition: ${escapeHtml(String(outcome.oldCondition))} &rarr; ${escapeHtml(String(outcome.newCondition))}`);
    if (outcome.wouldBecomeRuin) rows.push(`<span class="divine-warning">This would reduce the structure to ruin.</span>`);
  } else if (kind === "story_event" || kind === "law") {
    const mods = outcome.modifiers || {};
    if (Object.keys(mods).length) {
      rows.push("Modifiers: " + Object.entries(mods).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", "));
    }
    if (outcome.customRuleContext && outcome.customRuleContext.length) {
      rows.push("Composes with active village custom rule(s) on gather: " +
        outcome.customRuleContext.map((r) => `${escapeHtml(r.ruleId)} (modifier ${escapeHtml(String(r.value))})`).join(", "));
    }
    if (outcome.primitives && outcome.primitives.length) {
      rows.push(`${outcome.primitives.length} immediate primitive effect(s) will apply atomically with this event.`);
    }
    if (outcome.providenceOutgoingId) {
      rows.push(`<span class="divine-warning">This will REPLACE the active providence (id ${escapeHtml(String(outcome.providenceOutgoingId))}).</span>`);
    }
  } else if (kind === "checkpoint_create") {
    rows.push(`Label: ${escapeHtml(String(outcome.label || ""))}`);
    rows.push(`Frame tick: ${escapeHtml(String(outcome.frameTick))}`);
    rows.push(`Checkpoints: ${escapeHtml(String(outcome.checkpointCount))}`);
    if (outcome.willReplaceOldest) {
      rows.push(`<span class="divine-warning">At cap — oldest checkpoint will be dropped.</span>`);
    }
  } else if (kind === "checkpoint_restore") {
    rows.push(`Checkpoint: ${escapeHtml(String(outcome.label || outcome.checkpointId || ""))}`);
    if (outcome.checkpointFrameTick != null) {
      rows.push(`Snapshot frame: ${escapeHtml(String(outcome.checkpointFrameTick))}`);
    }
    rows.push(`Current frame: ${escapeHtml(String(outcome.currentFrameTick))}`);
    if (outcome.irreversibleWarning) {
      rows.push(`<span class="divine-warning">${escapeHtml(String(outcome.irreversibleWarning))}</span>`);
    }
  }
  return rows.map((r) => `<div>${r}</div>`).join("");
}

function renderGodPreviewWarningsHtml(warnings) {
  if (!warnings || !warnings.length) return "";
  return warnings.map((w) =>
    `<div class="divine-warning">${escapeHtml(String(w))}</div>`
  ).join("");
}

function renderGodPreviewHtml(data, label) {
  let html = godReversibilityBadge(data.reversibilityClass);
  if (data.fingerprint && data.fingerprint.outgoingId) {
    html += `<div class="divine-warning">This will REPLACE the active ${escapeHtml(label || "guidance")} (id ${escapeHtml(String(data.fingerprint.outgoingId))}).</div>`;
  }
  html += renderGodPreviewWarningsHtml(data.warnings);
  const kind = data.normalizedCommand && data.normalizedCommand.kind;
  html += renderGodPreviewOutcomeHtml(kind, data.previewOutcome);
  const secsLeft = Math.max(0, Math.round((data.expiresAt || 0) - Date.now() / 1000));
  html += `<div class="divine-meta">Preview valid ~${secsLeft}s. Any field edit invalidates it.</div>`;
  return html;
}

function godRenderKeyValueRows(obj, skipKeys) {
  const skip = new Set(skipKeys || []);
  return Object.entries(obj || {})
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && typeof v !== "object")
    .map(([k, v]) => `<div><span class="divine-kv-key">${escapeHtml(k)}:</span> ${escapeHtml(String(v))}</div>`)
    .join("");
}

function renderGodAppliedHtml(data) {
  const outcome = data.outcome || {};
  let html = `<div class="divine-applied-banner">Applied — intervention ${escapeHtml(String(data.interventionId))}, frame ${escapeHtml(String(data.appliedFrame))}.</div>`;
  if (outcome.kind === "story_event") {
    html += `<div>Title: ${escapeHtml(String(outcome.title || ""))}</div>`;
    const mods = outcome.modifiers || {};
    if (Object.keys(mods).length) {
      html += "<div>Modifiers: " + Object.entries(mods).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", ") + "</div>";
    }
    if (outcome.replacedEventId) html += `<div>Replaced event: ${escapeHtml(String(outcome.replacedEventId))}</div>`;
    if (outcome.primitiveOutcomes && outcome.primitiveOutcomes.length) {
      html += `<div>${outcome.primitiveOutcomes.length} primitive effect(s) applied.</div>`;
    }
    html += `<div>Expires: ${godDurationLabel((outcome.expiresFrame || 0) - (world.frameTick || 0))}</div>`;
  } else {
    html += godRenderKeyValueRows(outcome, ["interventionId", "kind"]);
    if (outcome.expiresFrame != null) {
      html += `<div>Expires: ${godDurationLabel(outcome.expiresFrame - (world.frameTick || 0))}</div>`;
    }
  }
  return html;
}

function godStanceBadgeHtml(stance) {
  const norm = String(stance || "").toLowerCase();
  const css = norm === "follow" ? "divine-voice-stance-follow" : "divine-voice-stance-continue";
  const label = norm === "follow" ? "follow" : "continue";
  return `<span class="divine-badge ${css}">${escapeHtml(label)}</span>`;
}

function godFilterDivineResponsesForAgent(responses, agent) {
  if (!agent) return [];
  const agentId = agent.id;
  const agentName = agent.name;
  return (responses || []).filter((r) => {
    if (!r || typeof r !== "object") return false;
    if (agentId != null && r.agentId === agentId) return true;
    return agentName && r.agentName === agentName;
  });
}

const GOD_VOICE_ADHERENCE_SIGHT_CAP = 20;

function godVoiceReasonSnippet(text, maxLen) {
  const s = String(text || "—");
  if (s.length <= maxLen) return s;
  return `${s.slice(0, maxLen - 1)}…`;
}

function godReadVoicePresentation(radioName) {
  const picked = document.querySelector(`input[name="${radioName}"]:checked`);
  const val = picked ? String(picked.value || "soft").toLowerCase() : "soft";
  return val === "thunder" ? "thunder" : "soft";
}

function godVoicePresentationPayload(radioName) {
  return godReadVoicePresentation(radioName) === "thunder"
    ? { presentation: "thunder" }
    : {};
}

function godRenderDivineResponseRows(entries, opts) {
  const showAgent = !(opts && opts.hideAgent);
  if (!entries.length) {
    return `<div class="divine-note">${escapeHtml((opts && opts.emptyText) || "No adherence records yet.")}</div>`;
  }
  const head = showAgent
    ? "<tr><th>Agent</th><th>Stance</th><th>Reason</th><th>Kind</th><th>Action</th><th>Frame</th></tr>"
    : "<tr><th>Stance</th><th>Reason</th><th>Kind</th><th>Action</th><th>Frame</th></tr>";
  const rows = entries.map((r) => {
    const synthetic = r.synthetic
      ? ' <span class="divine-voice-synthetic" title="Server synthesized missing divine_response">synthetic</span>'
      : "";
    const reason = escapeHtml(String(r.reason || "—"));
    const kind = escapeHtml(String(r.guidanceKind || "—"));
    const action = escapeHtml(String(r.action || "—"));
    const frame = escapeHtml(String(r.frameTick ?? "—"));
    const agentCell = showAgent
      ? `<td>${escapeHtml(String(r.agentName || r.agentId || "—"))}</td>`
      : "";
    return `<tr>${agentCell}<td>${godStanceBadgeHtml(r.stance)}${synthetic}</td><td class="divine-voice-reason">${reason}</td><td>${kind}</td><td>${action}</td><td>${frame}</td></tr>`;
  }).join("");
  return `<table class="divine-voice-adherence-table"><thead>${head}</thead><tbody>${rows}</tbody></table>`;
}

function godRenderVoiceAdherenceTimeline(entries, opts) {
  const cap = (opts && opts.cap) || GOD_VOICE_ADHERENCE_CAP;
  const list = (entries || []).slice(0, cap);
  if (!list.length) {
    return `<div class="divine-note">${escapeHtml((opts && opts.emptyText) || "No adherence records yet.")}</div>`;
  }
  return list.map((r) => {
    const synthetic = r.synthetic
      ? ' <span class="divine-voice-synthetic" title="Server synthesized missing divine_response">synthetic</span>'
      : "";
    const agent = escapeHtml(String(r.agentName || r.agentId || "—"));
    const snippet = escapeHtml(godVoiceReasonSnippet(r.reason, GOD_VOICE_REPLY_SNIPPET));
    const frame = escapeHtml(String(r.frameTick ?? "—"));
    return `<div class="divine-voice-timeline-entry">` +
      `<span class="divine-voice-timeline-agent">${agent}</span>` +
      `${godStanceBadgeHtml(r.stance)}${synthetic}` +
      `<span class="divine-voice-timeline-snippet">${snippet}</span>` +
      `<span class="divine-voice-timeline-meta">f${frame}</span>` +
      `</div>`;
  }).join("");
}

function godRenderVoiceReplyInbox(entries, opts) {
  const cap = (opts && opts.cap) || GOD_VOICE_ADHERENCE_CAP;
  const list = (entries || []).slice(0, cap);
  if (!list.length) {
    return `<div class="divine-note">${escapeHtml((opts && opts.emptyText) || "No agent replies yet.")}</div>`;
  }
  return list.map((r) => {
    const agent = escapeHtml(String(r.agentName || r.agentId || "—"));
    const reason = escapeHtml(godVoiceReasonSnippet(r.reason, GOD_VOICE_REPLY_MAX));
    const stance = String(r.stance || "").toLowerCase() === "follow" ? "follow" : "continue";
    const frame = escapeHtml(String(r.frameTick ?? "—"));
    const synthetic = r.synthetic ? " · synthetic" : "";
    return `<div class="divine-voice-reply-item">` +
      `<div class="divine-voice-reply-from">${agent}</div>` +
      `<div class="divine-voice-reply-body">${reason}</div>` +
      `<div class="divine-voice-reply-meta">${escapeHtml(stance)} · frame ${frame}${escapeHtml(synthetic)}</div>` +
      `</div>`;
  }).join("");
}

function renderGodVoiceAdherence() {
  const emptyNote = `<div class="divine-note">Refresh Sight first (or click Refresh above).</div>`;
  if (!godVoiceAdherenceTimelineEl && !godVoiceReplyInboxEl) return;
  if (!godLastSight) {
    if (godVoiceAdherenceTimelineEl) godVoiceAdherenceTimelineEl.innerHTML = emptyNote;
    if (godVoiceReplyInboxEl) godVoiceReplyInboxEl.innerHTML = emptyNote;
    return;
  }
  const entries = (godLastSight.recentDivineResponses || []).slice();
  const timelineOpts = { emptyText: "No village adherence records yet.", cap: GOD_VOICE_ADHERENCE_CAP };
  const inboxOpts = { emptyText: "No agent replies yet.", cap: GOD_VOICE_ADHERENCE_CAP };
  if (godVoiceAdherenceTimelineEl) {
    godVoiceAdherenceTimelineEl.innerHTML = godRenderVoiceAdherenceTimeline(entries, timelineOpts);
  }
  if (godVoiceReplyInboxEl) {
    godVoiceReplyInboxEl.innerHTML = godRenderVoiceReplyInbox(entries, inboxOpts);
  }
}

