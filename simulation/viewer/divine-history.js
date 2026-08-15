// --- History power tools (Phase 6) ---------------------------------------
const GOD_HISTORY_DISPLAY_CAP = 50;
const GOD_CANCELABLE_KINDS = new Set([
  "providence", "private_omen", "whisper_campaign", "crowd_compulsion", "dream_broadcast",
  "agent_sampling",
  "context_mask", "decision_compulsion", "decision_veto_arm", "agent_possession",
  "anoint", "identity_edit", "identity_copy_overwrite", "architect_zone",
  "story_event", "weather_override", "merovingian_bargain",
]);
let godHistoryFilter = {
  kind: "",
  agent: "",
  publicOnly: true,
  includePrivate: false,
  frameFrom: "",
  frameTo: "",
};

const godHistoryKindFilterEl = document.getElementById("godHistoryKindFilter");
const godHistoryAgentFilterEl = document.getElementById("godHistoryAgentFilter");
const godHistoryPublicOnlyEl = document.getElementById("godHistoryPublicOnly");
const godHistoryIncludePrivateEl = document.getElementById("godHistoryIncludePrivate");
const godHistoryIncludePrivateWrapEl = document.getElementById("godHistoryIncludePrivateWrap");
const godHistoryFrameFromEl = document.getElementById("godHistoryFrameFrom");
const godHistoryFrameToEl = document.getElementById("godHistoryFrameTo");
const godHistoryFilterStatusEl = document.getElementById("godHistoryFilterStatus");
const godHistoryKindListEl = document.getElementById("godHistoryKindList");

function godHistoryPublicRecords() {
  return ((world.god && world.god.recentPublicInterventions) || []).slice();
}

function godHistoryMergedRecords(includePrivate) {
  const byId = new Map();
  godHistoryPublicRecords().forEach((r) => {
    if (r && r.id) byId.set(r.id, r);
  });
  if (includePrivate && godEffectivelyAuthorized() && godLastSight) {
    (godLastSight.recentInterventions || []).forEach((r) => {
      if (r && r.id) byId.set(r.id, r);
    });
  }
  return Array.from(byId.values()).sort((a, b) => (b.frameTick || 0) - (a.frameTick || 0));
}

function godHistoryRecordById(id) {
  if (!id) return null;
  return godHistoryMergedRecords(true).find((r) => r.id === id) || null;
}

function godHistoryRecordAgentHaystack(record) {
  const parts = [];
  const ids = [];
  if (record.targetId != null) ids.push(record.targetId);
  if (record.targetAgentId != null) ids.push(record.targetAgentId);
  if (Array.isArray(record.targetIds)) record.targetIds.forEach((id) => ids.push(id));
  ids.forEach((id) => {
    parts.push(String(id));
    const living = getLivingAgents().find((a) => a.id === id);
    const anyAgent = living || (world.agents || []).find((a) => a.id === id);
    if (anyAgent) {
      parts.push(anyAgent.name || "");
      parts.push(anyAgent.role || "");
    }
  });
  return parts.join(" ").toLowerCase();
}

function godHistoryFilteredRecords() {
  const includePrivate = !!(godHistoryFilter.includePrivate
    && godEffectivelyAuthorized()
    && godLastSight
    && (godLastSight.recentInterventions || []).length);
  let records = godHistoryMergedRecords(includePrivate);
  if (godHistoryFilter.publicOnly) {
    records = records.filter((r) => r.public === true);
  }
  const kindNeedle = (godHistoryFilter.kind || "").trim().toLowerCase();
  if (kindNeedle) {
    records = records.filter((r) => String(r.kind || "").toLowerCase().includes(kindNeedle));
  }
  const agentNeedle = (godHistoryFilter.agent || "").trim().toLowerCase();
  if (agentNeedle) {
    records = records.filter((r) => godHistoryRecordAgentHaystack(r).includes(agentNeedle));
  }
  const frameFrom = godHistoryFilter.frameFrom !== "" ? parseInt(godHistoryFilter.frameFrom, 10) : null;
  if (frameFrom != null && Number.isFinite(frameFrom)) {
    records = records.filter((r) => (r.frameTick || 0) >= frameFrom);
  }
  const frameTo = godHistoryFilter.frameTo !== "" ? parseInt(godHistoryFilter.frameTo, 10) : null;
  if (frameTo != null && Number.isFinite(frameTo)) {
    records = records.filter((r) => (r.frameTick || 0) <= frameTo);
  }
  return records.slice(0, GOD_HISTORY_DISPLAY_CAP);
}

function godHistorySyncFilterFromUi() {
  godHistoryFilter.kind = godHistoryKindFilterEl ? godHistoryKindFilterEl.value : "";
  godHistoryFilter.agent = godHistoryAgentFilterEl ? godHistoryAgentFilterEl.value : "";
  godHistoryFilter.publicOnly = godHistoryPublicOnlyEl ? godHistoryPublicOnlyEl.checked : true;
  godHistoryFilter.includePrivate = godHistoryIncludePrivateEl ? godHistoryIncludePrivateEl.checked : false;
  godHistoryFilter.frameFrom = godHistoryFrameFromEl ? godHistoryFrameFromEl.value : "";
  godHistoryFilter.frameTo = godHistoryFrameToEl ? godHistoryFrameToEl.value : "";
}

function godHistoryPopulateKindDatalist() {
  if (!godHistoryKindListEl) return;
  const kinds = new Set();
  godHistoryMergedRecords(true).forEach((r) => {
    if (r && r.kind) kinds.add(r.kind);
  });
  godHistoryKindListEl.innerHTML = Array.from(kinds).sort().map((k) =>
    `<option value="${escapeHtml(k)}"></option>`).join("");
}

function updateGodHistorySightToggle() {
  const canInclude = godEffectivelyAuthorized()
    && godLastSight
    && (godLastSight.recentInterventions || []).length;
  if (godHistoryIncludePrivateWrapEl) {
    godHistoryIncludePrivateWrapEl.style.display = canInclude ? "" : "none";
  }
  if (!canInclude && godHistoryIncludePrivateEl) {
    godHistoryIncludePrivateEl.checked = false;
    godHistoryFilter.includePrivate = false;
  }
}

function godHistoryFilterStatusText(filtered, total) {
  const bits = [`Showing ${filtered.length} of ${total} intervention${total === 1 ? "" : "s"}`];
  if (godHistoryFilter.publicOnly) bits.push("public only");
  if (godHistoryFilter.includePrivate && godEffectivelyAuthorized() && godLastSight) {
    bits.push("Sight private merged");
  }
  return bits.join(" · ");
}

function godDurationSecondsFromRecord(record) {
  if (record.expiresFrame == null || record.frameTick == null) return "";
  const frames = Number(record.expiresFrame) - Number(record.frameTick);
  if (!Number.isFinite(frames) || frames <= 0) return "";
  return godFramesToSeconds(frames);
}

function godSetVoicePresentation(radioName, presentation) {
  const val = presentation === "thunder" ? "thunder" : "soft";
  document.querySelectorAll(`input[name="${radioName}"]`).forEach((el) => {
    el.checked = el.value === val;
  });
}

function godInvalidateDivineFieldset(fieldsetId) {
  const fs = document.getElementById(fieldsetId);
  if (fs) fs.dispatchEvent(new Event("input", { bubbles: true }));
}

function godCancelKindAllowed(kind) {
  return GOD_CANCELABLE_KINDS.has(kind);
}

function godInterventionLikelyActive(record) {
  if (!record || !record.id || !godCancelKindAllowed(record.kind)) return false;
  const ft = world.frameTick || 0;
  const exp = record.expiresFrame;
  if (typeof exp === "number" && ft >= exp) return false;
  if (!godLastSight) {
    return typeof exp === "number" && ft < exp;
  }
  if (record.kind === "providence") {
    return !!(godLastSight.providence && godLastSight.providence.id === record.id
      && godVoiceGuidanceInWindow(godLastSight.providence));
  }
  if (record.kind === "story_event" || record.kind === "weather_override") {
    return (godLastSight.activeEvents || []).some((e) =>
      e && e.id === record.id && e.status === "active");
  }
  if (record.kind === "architect_zone") {
    return (godLastSight.architectZones || []).some((z) => z && z.id === record.id);
  }
  if (record.kind === "whisper_campaign" || record.kind === "crowd_compulsion"
      || record.kind === "dream_broadcast") {
    return typeof exp === "number" && ft < exp;
  }
  if (typeof exp === "number") return ft < exp;
  return false;
}

function godFindRevokeTarget() {
  const candidates = [];
  if (godLastAppliedPin && godLastAppliedPin.id) {
    const rec = godHistoryRecordById(godLastAppliedPin.id);
    if (rec) candidates.push(rec);
  }
  if (godLastSight && godLastSight.recentInterventions) {
    for (let i = godLastSight.recentInterventions.length - 1; i >= 0; i--) {
      const rec = godLastSight.recentInterventions[i];
      if (!rec || !rec.id) continue;
      if (candidates.some((c) => c.id === rec.id)) continue;
      if (godCancelKindAllowed(rec.kind)) candidates.push(rec);
    }
  }
  for (const rec of candidates) {
    if (godInterventionLikelyActive(rec)) return rec;
  }
  return null;
}

function godHistoryRerunAssessment(record) {
  const kind = record.kind;
  if (kind === "proclamation") {
    if (!record.text) return { ok: false, reason: "text not stored" };
    return { ok: true, feature: "voice", fieldsetId: "godProclamationFieldset" };
  }
  if (kind === "providence") {
    if (!record.text) return { ok: false, reason: "text not stored" };
    return { ok: true, feature: "voice", fieldsetId: "godProvidenceFieldset" };
  }
  if (kind === "private_omen") {
    if (record.targetId == null || !record.text) return { ok: false, reason: "target/text not stored" };
    return { ok: true, feature: "voice", fieldsetId: "godOmenFieldset" };
  }
  if (kind === "agent_vitals") {
    if (record.targetId == null) return { ok: false, reason: "target not stored" };
    if (!record.healthDelta && !record.hungerDelta) return { ok: false, reason: "deltas not stored" };
    return { ok: true, feature: "miracles", fieldsetId: "godVitalsFieldset" };
  }
  if (kind === "grant_resource") {
    if (!record.resourceId || !record.amount) return { ok: false, reason: "grant fields not stored" };
    return { ok: true, feature: "miracles", fieldsetId: "godGrantFieldset" };
  }
  if (kind === "structure_condition") {
    if (record.structureId == null || record.delta == null) {
      return { ok: false, reason: "structure/delta not stored" };
    }
    return { ok: true, feature: "miracles", fieldsetId: "godStructureFieldset" };
  }
  if (kind === "agent_sampling") {
    if (record.targetId == null || record.temperature == null) {
      return { ok: false, reason: "sampling fields not stored" };
    }
    return { ok: true, feature: "matrix", fieldsetId: "godSamplingFieldset" };
  }
  if (kind === "context_mask") {
    if (record.targetId == null || !record.mode) return { ok: false, reason: "mask fields not stored" };
    if (record.mode === "dream" || record.mode === "whisper_chain") {
      return { ok: false, reason: "dream/whisper JSON not stored in history" };
    }
    return { ok: true, feature: "matrix", fieldsetId: "godDistortionFieldset" };
  }
  if (kind === "decision_veto_arm") {
    if (record.targetId == null) return { ok: false, reason: "target not stored" };
    return { ok: true, feature: "matrix", fieldsetId: "godVetoArmFieldset" };
  }
  if (kind === "story_event") {
    if ((record.modifierKeys && record.modifierKeys.length)
      || (record.primitiveInterventionIds && record.primitiveInterventionIds.length)) {
      return { ok: false, reason: "modifier/primitive values not stored" };
    }
    if (!record.title && !record.narration) return { ok: false, reason: "title/narration not stored" };
    return { ok: true, feature: "story", fieldsetId: "godStoryFieldset" };
  }
  if (kind === "whisper_campaign" || kind === "crowd_compulsion" || kind === "dream_broadcast") {
    return { ok: false, reason: "batch payload details not stored in history" };
  }
  return { ok: false, reason: "kind not rehydratable from history" };
}

function godRerunFromHistory(record) {
  const assessment = godHistoryRerunAssessment(record);
  if (!assessment.ok) return assessment;
  const kind = record.kind;
  if (kind === "proclamation") {
    document.getElementById("godProcText").value = record.text || "";
    godSetVoicePresentation("godProcPresentation", record.presentation);
    godInvalidateDivineFieldset("godProclamationFieldset");
  } else if (kind === "providence") {
    document.getElementById("godProvText").value = record.text || "";
    document.getElementById("godProvDuration").value = godDurationSecondsFromRecord(record);
    godSetVoicePresentation("godProvPresentation", record.presentation);
    godInvalidateDivineFieldset("godProvidenceFieldset");
  } else if (kind === "private_omen") {
    document.getElementById("godOmenAgentSelect").value = String(record.targetId);
    document.getElementById("godOmenText").value = record.text || "";
    document.getElementById("godOmenDuration").value = godDurationSecondsFromRecord(record);
    if (record.targetId != null) setGodFocusAgent(record.targetId, { mirrorSelection: false });
    godInvalidateDivineFieldset("godOmenFieldset");
  } else if (kind === "agent_vitals") {
    document.getElementById("godVitalsAgentSelect").value = String(record.targetId);
    document.getElementById("godVitalsHealth").value = record.healthDelta != null ? record.healthDelta : "0";
    document.getElementById("godVitalsHunger").value = record.hungerDelta != null ? record.hungerDelta : "0";
    if (record.targetId != null) setGodFocusAgent(record.targetId, { mirrorSelection: false });
    godInvalidateDivineFieldset("godVitalsFieldset");
  } else if (kind === "grant_resource") {
    document.getElementById("godGrantResourceSelect").value = record.resourceId || "";
    document.getElementById("godGrantAmount").value = record.amount != null ? String(record.amount) : "";
    if (record.targetKind === "agent" && record.targetAgentId != null) {
      document.getElementById("godGrantTargetKind").value = "agent";
      document.getElementById("godGrantAgentSelect").value = String(record.targetAgentId);
      setGodFocusAgent(record.targetAgentId, { mirrorSelection: false });
    } else {
      document.getElementById("godGrantTargetKind").value = "stockpile";
    }
    document.getElementById("godGrantTargetKind").dispatchEvent(new Event("change"));
    godInvalidateDivineFieldset("godGrantFieldset");
  } else if (kind === "structure_condition") {
    document.getElementById("godStructureSelect").value = String(record.structureId);
    document.getElementById("godStructureDelta").value = record.delta != null ? String(record.delta) : "";
    godInvalidateDivineFieldset("godStructureFieldset");
  } else if (kind === "agent_sampling") {
    document.getElementById("godSamplingAgentSelect").value = String(record.targetId);
    if (record.model) document.getElementById("godSamplingModel").value = record.model;
    document.getElementById("godSamplingTemp").value = record.temperature;
    document.getElementById("godSamplingDuration").value = godDurationSecondsFromRecord(record);
    if (godSamplingTempOutEl) godSamplingTempOutEl.textContent = String(record.temperature);
    if (record.targetId != null) setGodFocusAgent(record.targetId, { mirrorSelection: false });
    godInvalidateDivineFieldset("godSamplingFieldset");
  } else if (kind === "context_mask") {
    document.getElementById("godDistortionAgentSelect").value = String(record.targetId);
    const modeEl = document.querySelector(`#godDistortionFieldset input[name="godDistortionMode"][value="${record.mode}"]`);
    if (modeEl) modeEl.checked = true;
    document.getElementById("godDistortionDuration").value = godDurationSecondsFromRecord(record);
    if (record.targetId != null) setGodFocusAgent(record.targetId, { mirrorSelection: false });
    godInvalidateDivineFieldset("godDistortionFieldset");
  } else if (kind === "decision_veto_arm") {
    document.getElementById("godVetoArmAgentSelect").value = String(record.targetId);
    document.getElementById("godVetoArmDuration").value = godDurationSecondsFromRecord(record);
    if (record.targetId != null) setGodFocusAgent(record.targetId, { mirrorSelection: false });
    godInvalidateDivineFieldset("godVetoArmFieldset");
  } else if (kind === "story_event") {
    document.getElementById("godStoryTitle").value = record.title || "";
    document.getElementById("godStoryNarration").value = record.narration || "";
    const vis = record.visibility === "private" ? "private" : "public";
    document.getElementById("godStoryVisibility").value = vis;
    if (vis === "private" && record.targetId != null) {
      document.getElementById("godStoryTargetSelect").value = String(record.targetId);
    }
    document.getElementById("godStoryDuration").value = godDurationSecondsFromRecord(record);
    document.querySelectorAll(".gs-mod-enable").forEach((cb) => { cb.checked = false; });
    document.getElementById("godStoryProvidenceCheckbox").checked = false;
    document.getElementById("godStoryProvidenceText").value = "";
    godInvalidateDivineFieldset("godStoryFieldset");
  }
  openDivineModal(assessment.feature);
  godScrollDivineFieldset(assessment.fieldsetId);
  return { ok: true };
}

function godExportHistoryMarkdown() {
  godHistorySyncFilterFromUi();
  const records = godHistoryFilteredRecords();
  const lines = [
    "# Divine intervention history",
    "",
    `Exported at frame ${world.frameTick || 0}.`,
    "",
  ];
  if (!records.length) {
    lines.push("_No interventions match the current filter._");
  }
  records.forEach((r) => {
    lines.push(`## ${r.kind || "intervention"} (${r.id})`);
    lines.push(`- **Frame:** ${r.frameTick ?? "—"}`);
    lines.push(`- **Public:** ${r.public ? "yes" : "no"}`);
    if (r.title) lines.push(`- **Title:** ${r.title}`);
    if (r.text) lines.push(`- **Text:** ${r.text}`);
    if (r.narration) lines.push(`- **Narration:** ${r.narration}`);
    if (r.targetId != null) lines.push(`- **Target agent:** ${r.targetId}`);
    if (r.expiresFrame != null) lines.push(`- **Expires frame:** ${r.expiresFrame}`);
    lines.push("");
  });
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `divine-history-${world.frameTick || 0}.md`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function wireGodHistoryControls() {
  const rerender = () => {
    godHistorySyncFilterFromUi();
    godHistoryPopulateKindDatalist();
    renderGodHistory();
  };
  [
    godHistoryKindFilterEl,
    godHistoryAgentFilterEl,
    godHistoryPublicOnlyEl,
    godHistoryIncludePrivateEl,
    godHistoryFrameFromEl,
    godHistoryFrameToEl,
  ].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", rerender);
    el.addEventListener("change", rerender);
  });
  const clearBtn = document.getElementById("godHistoryClearFiltersBtn");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      if (godHistoryKindFilterEl) godHistoryKindFilterEl.value = "";
      if (godHistoryAgentFilterEl) godHistoryAgentFilterEl.value = "";
      if (godHistoryPublicOnlyEl) godHistoryPublicOnlyEl.checked = true;
      if (godHistoryIncludePrivateEl) godHistoryIncludePrivateEl.checked = false;
      if (godHistoryFrameFromEl) godHistoryFrameFromEl.value = "";
      if (godHistoryFrameToEl) godHistoryFrameToEl.value = "";
      godHistoryFilter = {
        kind: "", agent: "", publicOnly: true, includePrivate: false, frameFrom: "", frameTo: "",
      };
      rerender();
    });
  }
  const exportBtn = document.getElementById("godHistoryExportBtn");
  if (exportBtn) exportBtn.addEventListener("click", godExportHistoryMarkdown);
  if (godHistoryListEl) {
    godHistoryListEl.addEventListener("click", (e) => {
      const btn = e.target.closest(".god-history-rerun");
      if (!btn || btn.disabled) return;
      const id = btn.dataset.id;
      const record = godHistoryRecordById(id);
      if (!record) return;
      const result = godRerunFromHistory(record);
      if (!result.ok && godHistoryFilterStatusEl) {
        godHistoryFilterStatusEl.textContent = `Re-run failed: ${result.reason || "unknown"}`;
      }
    });
  }
}
wireGodHistoryControls();

function renderGodHistory() {
  godHistorySyncFilterFromUi();
  updateGodHistorySightToggle();
  godHistoryPopulateKindDatalist();
  const includePrivate = !!(godHistoryFilter.includePrivate
    && godEffectivelyAuthorized()
    && godLastSight);
  const total = godHistoryMergedRecords(includePrivate).length;
  const records = godHistoryFilteredRecords();
  if (godHistoryFilterStatusEl) {
    godHistoryFilterStatusEl.textContent = godHistoryFilterStatusText(records, total);
  }
  if (!records.length) {
    godHistoryListEl.innerHTML = `<li class="divine-note">No interventions match the current filter.</li>`;
    return;
  }
  godHistoryListEl.innerHTML = records.map((r) => {
    const badgeClass = r.public ? "divine-history-public" : "divine-history-private";
    const badgeLabel = r.public ? "public" : "private";
    const badge = `<span class="divine-history-badge ${badgeClass}">${escapeHtml(badgeLabel)}</span>`;
    const label = escapeHtml(String(r.title || r.text || r.narration || r.kind || "intervention"));
    const assessment = godHistoryRerunAssessment(r);
    const rerunBtn = assessment.ok
      ? `<button type="button" class="god-history-rerun" data-id="${escapeHtml(String(r.id))}" ` +
        `data-tip='{"t":"Re-run","d":"Fill the form from this log entry — you must Preview again before Apply."}'>Re-run</button>`
      : `<button type="button" class="god-history-rerun" disabled ` +
        `data-tip='{"t":"Re-run unavailable","d":"${escapeHtml(assessment.reason || "details not saved")}"}'>Re-run</button>` +
        `<span class="divine-history-rerun-note">${escapeHtml(assessment.reason || "")}</span>`;
    let agentMeta = "";
    if (r.targetId != null) agentMeta = `, agent ${escapeHtml(String(r.targetId))}`;
    else if (r.targetAgentId != null) agentMeta = `, agent ${escapeHtml(String(r.targetAgentId))}`;
    return `<li class="divine-history-item">${badge}` +
      `<span class="divine-kv-key">${escapeHtml(String(r.kind || ""))}</span> — ${label}` +
      `<div class="divine-meta">frame ${escapeHtml(String(r.frameTick))}, id ${escapeHtml(String(r.id))}${agentMeta}</div>` +
      `<div class="divine-history-actions-row">${rerunBtn}</div></li>`;
  }).join("");
}

// --- Gate + passive per-poll refresh (History/Laws/banner) ---------------
let godLastAgentListKey = null;
function updateGodModeGate() {
  const flags = (world.config && world.config.flags) || {};
  const enabled = !!flags.GOD_MODE_ENABLED;
  if (enabled !== GOD_MODE_ENABLED_FLAG) GOD_MODE_ENABLED_FLAG = enabled;
  const authRequired = !!flags.GOD_AUTH_REQUIRED;
  const authFlagChanged = authRequired !== GOD_AUTH_REQUIRED_FLAG;
  if (authFlagChanged) GOD_AUTH_REQUIRED_FLAG = authRequired;
  if (divineBarEl) divineBarEl.style.display = GOD_MODE_ENABLED_FLAG ? "" : "none";
  document.body.classList.toggle("divine-bar-visible", !!GOD_MODE_ENABLED_FLAG);
  // idea-03 Agent interview: dual-gated on AGENT_INTERVIEW_ENABLED_FLAG AND
  // GOD_MODE_ENABLED_FLAG (specs/11-viewer.md "Interview") -- unlike Compile,
  // no /control/god/capabilities probe is needed since the route itself
  // requires no auth; both flags are plain config.flags booleans.
  const interviewTabBtn = document.getElementById("godInterviewTabBtn");
  if (interviewTabBtn) {
    const interviewVisible = AGENT_INTERVIEW_ENABLED_FLAG && GOD_MODE_ENABLED_FLAG;
    interviewTabBtn.style.display = interviewVisible ? "" : "none";
    if (!interviewVisible && godActiveTab === "interview") showGodTab("sight");
  }
  if (!GOD_MODE_ENABLED_FLAG) {
    closeDivineModal();
    godPublicBannerEl.style.display = "none";
    return;
  }
  if (authFlagChanged) {
    if (GOD_AUTH_REQUIRED_FLAG) {
      if (!godToken) godAuthorized = false;
    } else {
      godAuthorized = true;
      godOpenModeBootstrapped = false;
    }
    updateDivineBarAuthUi();
    if (!GOD_AUTH_REQUIRED_FLAG) godOpenModeBootstrap();
  }
  if (!GOD_AUTH_REQUIRED_FLAG) godOpenModeBootstrap();
  // Change-detected, not every-poll: repopulating <select> elements on every
  // 100ms tick would reset an in-progress dropdown selection.
  const agentKey = JSON.stringify([
    getLivingAgents().map((a) => [a.id, a.name, a.role]),
    Object.keys(resourceRegistry()),
    (getCiv().structures || []).map((s) => [s.id, s.isRuin, s.condition]),
  ]);
  if (agentKey !== godLastAgentListKey) {
    godLastAgentListKey = agentKey;
    populateGodAgentSelects();
  }
  updateDivineBarSituational();
  maybeRefreshGodSight();
}

function renderGodPublicBanner() {
  const records = (world.god && world.god.recentPublicInterventions) || [];
  if (godSeenInterventionIds === null) {
    // First snapshot after page load: remember existing history without
    // banner-ing it (same edge-detection precedent as the founding banner).
    godSeenInterventionIds = new Set(records.map((r) => r.id));
    return;
  }
  const fresh = records.find((r) => !godSeenInterventionIds.has(r.id));
  if (!fresh) return;
  godSeenInterventionIds.add(fresh.id);
  const label = fresh.title || fresh.text || fresh.kind || "A divine intervention";
  godPublicBannerEl.textContent = `Divine: ${String(label)}`;
  godPublicBannerEl.classList.remove("divine-banner-soft", "divine-banner-thunder");
  const pres = fresh.presentation === "thunder" ? "thunder" : "soft";
  godPublicBannerEl.classList.add(pres === "thunder" ? "divine-banner-thunder" : "divine-banner-soft");
  godPublicBannerEl.style.display = "block";
  pulseDivineBar();
  if (godBannerTimer) clearTimeout(godBannerTimer);
  godBannerTimer = setTimeout(() => {
    godPublicBannerEl.style.display = "none";
    godBannerTimer = null;
  }, 6000);
  // Bounded like foundingFramesSeen: drop ids that aged out of the ring.
  const stillPresent = new Set(records.map((r) => r.id));
  for (const id of Array.from(godSeenInterventionIds)) {
    if (!stillPresent.has(id)) godSeenInterventionIds.delete(id);
  }
}

function renderDivineConsole() {
  updateGodModeGate();
  if (!GOD_MODE_ENABLED_FLAG) return;
  if (godActiveTab === "lineage") renderGodLineage();
  const key = JSON.stringify(world.god || null);
  if (key === godLastStateKey) return;
  godLastStateKey = key;
  renderGodPublicBanner();
  updateDivineBarAuthUi();
  if (godActiveTab === "history") renderGodHistory();
  if (godActiveTab === "laws" && !(godLastSight && godLastSight.activeEvents)) renderGodLawsActive();
}

// Kick off: render immediately (mock or last frame), poll on an interval.
syncPauseButton();
scheduleTerrainCacheBuild(); // speculative build via STARTER_DISTRICTS_JS (~10 ms)
requestAnimationFrame(tick);
pollState();
setInterval(pollState, STATE_POLL_MS);
pollDistricts();
setInterval(pollDistricts, DISTRICTS_POLL_MS);

