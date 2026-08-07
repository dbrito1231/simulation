// --- Sight ---------------------------------------------------------------
async function refreshGodSight() {
  const prevSightForDiff = godCloneSightForDiff(godLastSight);
  const resp = await godApiFetch("/control/god/sight");
  if (!resp.data || !resp.data.ok) {
    godLastSight = null;
    renderGodError(godSightOutputEl, (resp.data && resp.data.reason) || "sight unavailable");
    const sightFailNote = `<div class="divine-note">Sight unavailable — refresh failed.</div>`;
    if (godVoiceAdherenceTimelineEl) godVoiceAdherenceTimelineEl.innerHTML = sightFailNote;
    if (godVoiceReplyInboxEl) godVoiceReplyInboxEl.innerHTML = sightFailNote;
    return;
  }
  godLastSight = resp.data;
  godLastSightFetchedAt = Date.now();
  renderGodSight(prevSightForDiff);
  renderGodVoiceAdherence();
  populateGodCheckpointRestoreSelect();
  if (godActiveTab === "laws") renderGodLawsActive();
  updateDivineBarSituational();
  updateGodHistorySightToggle();
  if (godActiveTab === "history") renderGodHistory();
  renderGodPinRow();
}
document.getElementById("godSightRefreshBtn").addEventListener("click", refreshGodSight);
if (godSightOutputEl) {
  godSightOutputEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".god-sight-intervene-btn");
    if (!btn) return;
    godSightIntervene(
      Number(btn.dataset.agentId),
      btn.dataset.feature,
      btn.dataset.fieldset || null
    );
  });
}
const godVoiceAdherenceRefreshBtn = document.getElementById("godVoiceAdherenceRefreshBtn");
if (godVoiceAdherenceRefreshBtn) {
  godVoiceAdherenceRefreshBtn.addEventListener("click", refreshGodSight);
}

function renderGodSight(prevSightForDiff) {
  if (!godLastSight) { godSightOutputEl.innerHTML = ""; return; }
  const selectedId = godSightAgentSelectEl.value ? Number(godSightAgentSelectEl.value) : null;
  const focusId = godPreferredAgentId() ?? selectedId;
  const agent = (godLastSight.agents || []).find((a) => a.id === selectedId) || (godLastSight.agents || [])[0];
  if (!agent) { godSightOutputEl.innerHTML = `<div class="divine-note">No agents.</div>`; return; }
  const diffHtml = godSightDiffStripHtml(prevSightForDiff, godLastSight, focusId);
  const pulseHtml = godSightPulseCardHtml(godLastSight.pulse);
  const resourceRows = Object.entries(agent.resources || {})
    .map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(String(v))}`).join(", ") || "(none)";
  const omenUnacked = agent.omen && agent.omen.unacked
    ? ' <span class="divine-voice-unacked">unacked</span>' : "";
  const omen = agent.omen
    ? `active, ${escapeHtml(godCountdownLabel(agent.omen.expiresFrame))}${omenUnacked}`
    : "none";
  const providenceUnacked = agent.providence && agent.providence.unacked
    ? ' <span class="divine-voice-unacked">unacked</span>' : "";
  const providence = agent.providence
    ? `active, ${escapeHtml(godCountdownLabel(agent.providence.expiresFrame))}${providenceUnacked}`
    : "none";
  const sampling = agent.sampling
    ? `active, ${escapeHtml(agent.sampling.model || "?")} @ ${escapeHtml(String(agent.sampling.temperature))}${agent.sampling.expiresFrame ? `, ${escapeHtml(godCountdownLabel(agent.sampling.expiresFrame))}` : " (until revoke)"}`
    : "none";
  const contextMask = agent.contextMask
    ? `active, ${escapeHtml(agent.contextMask.mode || "?")}, ${escapeHtml(godCountdownLabel(agent.contextMask.expiresFrame))}`
    : "none";
  const decisionGate = agent.decisionGate
    ? `${escapeHtml(godSightGateSummary(agent.decisionGate))}${agent.decisionGate.expiresFrame ? `, ${escapeHtml(godCountdownLabel(agent.decisionGate.expiresFrame))}` : ""}`
    : "none";
  const divineHold = agent.divineHold ? "yes" : "no";
  const architectLimbo = agent.architectLimbo && agent.architectLimbo.active
    ? `yes (zone ${escapeHtml(String(agent.architectLimbo.zoneId || "?"))})`
    : "no";
  const anointment = agent.anointment && agent.anointment.active
    ? `active, ${escapeHtml(String(agent.anointment.tagCount || 0))} tags, ${escapeHtml(godCountdownLabel(agent.anointment.expiresFrame))}`
    : "none";
  const active = (godLastSight.activeEvents || [])
    .filter((e) => e.status === "active")
    .map((e) => {
      const label = escapeHtml(e.kind === "story_event" ? (e.title || e.kind) : e.kind);
      const priv = e.visibility === "private"
        ? " <span class=\"divine-history-badge divine-history-private\">private</span>" : "";
      return `<li>${label} — ${escapeHtml(godCountdownLabel(e.expiresFrame))}${priv}</li>`;
    })
    .join("") || "<li>(none)</li>";
  const zoneSummaries = (godLastSight.architectZones || [])
    .map((z) => `<li>${escapeHtml(String(z.kind || "?"))} in ${escapeHtml(String(z.districtId || "?"))} — ${escapeHtml(String(z.cellCount || 0))} cells, ${escapeHtml(godCountdownLabel(z.expiresFrame))}</li>`)
    .join("") || "<li>(none)</li>";
  const agentResponses = godFilterDivineResponsesForAgent(
    godLastSight.recentDivineResponses, agent
  ).slice(0, GOD_VOICE_ADHERENCE_SIGHT_CAP);
  const adherenceHtml = godRenderDivineResponseRows(agentResponses, {
    hideAgent: true,
    emptyText: "No adherence records for this agent yet.",
  });
  godSightOutputEl.innerHTML = diffHtml + pulseHtml +
    godSightInterveneButtonsHtml(agent.id) +
    `<div><span class="divine-kv-key">Health:</span> ${escapeHtml(String(agent.health))} &nbsp; <span class="divine-kv-key">Hunger:</span> ${escapeHtml(String(agent.hunger))}</div>` +
    `<div><span class="divine-kv-key">Incapacitated:</span> ${escapeHtml(String(!!agent.incapacitated))} &nbsp; <span class="divine-kv-key">District:</span> ${escapeHtml(String(agent.currentDistrict || "—"))}</div>` +
    `<div><span class="divine-kv-key">Resources:</span> ${resourceRows}</div>` +
    `<div><span class="divine-kv-key">Last action:</span> ${escapeHtml(String(agent.lastAction || "—"))}</div>` +
    `<div><span class="divine-kv-key">Decision gate:</span> ${decisionGate}</div>` +
    `<div><span class="divine-kv-key">Divine hold:</span> ${escapeHtml(divineHold)} &nbsp; <span class="divine-kv-key">Architect limbo:</span> ${architectLimbo}</div>` +
    `<div><span class="divine-kv-key">Anointment:</span> ${anointment}</div>` +
    `<div><span class="divine-kv-key">Private omen:</span> ${omen}</div>` +
    `<div><span class="divine-kv-key">Providence:</span> ${providence}</div>` +
    `<div><span class="divine-kv-key">Sampling override:</span> ${sampling}</div>` +
    `<div><span class="divine-kv-key">Context mask:</span> ${contextMask}</div>` +
    `<div><span class="divine-kv-key">Memory tiers:</span> working ${escapeHtml(String((agent.memoryCounts || {}).working ?? 0))}, shortTerm ${escapeHtml(String((agent.memoryCounts || {}).shortTerm ?? 0))}</div>` +
    `<div><span class="divine-kv-key">Beliefs held:</span> ${escapeHtml(String(agent.beliefCount ?? 0))}</div>` +
    `<div><span class="divine-kv-key">Active effects (village-wide, this authenticated view):</span><ul>${active}</ul></div>` +
    `<div><span class="divine-kv-key">Architect zones (district outlines on map while modal open):</span><ul>${zoneSummaries}</ul></div>` +
    `<div class="divine-voice-adherence-sight"><span class="divine-kv-key">Voice adherence:</span>${adherenceHtml}` +
    `<p class="divine-note divine-voice-adherence-xlink">See Voice → Adherence for the full village feed.</p></div>`;
  const checkpoints = (godLastSight.checkpoints || [])
    .map((c) => `<li>${escapeHtml(String(c.label || c.id))} — frame ${escapeHtml(String(c.frameTick))} (${escapeHtml(String(c.id))})</li>`)
    .join("") || "<li>(none)</li>";
  const digestRows = (godLastSight.decisionDigests || [])
    .filter((d) => !selectedId || d.agentId === selectedId)
    .slice(-8)
    .map((d) => `<li>frame ${escapeHtml(String(d.frameTick))} — ${escapeHtml(String(d.action))}${d.reasoningHash ? ` <span class="divine-note">#${escapeHtml(String(d.reasoningHash))}</span>` : ""}</li>`)
    .join("") || "<li>(none — natural decisions populate the digest ring)</li>";
  const replayRows = (godLastSight.dejaVuReplays || [])
    .filter((r) => !selectedId || r.targetId === selectedId)
    .map((r) => `<li>${escapeHtml(String(r.id))} — ${escapeHtml(String(r.currentIndex ?? 0))}/${escapeHtml(String(r.stepCount ?? 0))} (${escapeHtml(String(r.status || "?"))})</li>`)
    .join("") || "<li>(none)</li>";
  godSightOutputEl.innerHTML +=
    `<div><span class="divine-kv-key">Decision digests (operator):</span><ul>${digestRows}</ul></div>` +
    `<div><span class="divine-kv-key">Déjà Vu replays:</span><ul>${replayRows}</ul></div>` +
    `<div><span class="divine-kv-key">Checkpoints:</span><ul>${checkpoints}</ul></div>`;
}
godSightAgentSelectEl.addEventListener("change", () => renderGodSight(undefined));

function populateGodCheckpointRestoreSelect() {
  const sel = document.getElementById("godCheckpointRestoreSelect");
  if (!sel) return;
  const prev = sel.value;
  const list = (godLastSight && godLastSight.checkpoints) || [];
  sel.innerHTML = list.length
    ? list.map((c) =>
      `<option value="${escapeHtml(String(c.id))}">${escapeHtml(String(c.label || c.id))} (frame ${escapeHtml(String(c.frameTick))})</option>`
    ).join("")
    : "<option value=\"\">— refresh Sight or create first —</option>";
  if (prev && list.some((c) => c.id === prev)) sel.value = prev;
}

// --- Voice presets (sessionStorage) --------------------------------------
function loadDivineVoicePresets() {
  try {
    const raw = sessionStorage.getItem(DIVINE_VOICE_PRESETS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch (err) {
    return [];
  }
}

function saveDivineVoicePresets(presets) {
  try {
    sessionStorage.setItem(DIVINE_VOICE_PRESETS_KEY, JSON.stringify(presets));
  } catch (err) { /* ignore */ }
  populateGodVoicePresetSelect();
}

function populateGodVoicePresetSelect() {
  const sel = document.getElementById("godVoicePresetSelect");
  if (!sel) return;
  const prev = sel.value;
  const presets = loadDivineVoicePresets();
  const opts = presets.map((p) =>
    `<option value="${escapeHtml(String(p.id))}">${escapeHtml(String(p.name || p.kind || "preset"))} (${escapeHtml(String(p.kind || "?"))})</option>`
  );
  sel.innerHTML = `<option value="">— none —</option>${opts.join("")}`;
  if (prev && presets.some((p) => p.id === prev)) sel.value = prev;
}

function godDetectActiveVoicePresetKind() {
  const procText = (document.getElementById("godProcText") && document.getElementById("godProcText").value.trim()) || "";
  const provText = (document.getElementById("godProvText") && document.getElementById("godProvText").value.trim()) || "";
  const omenText = (document.getElementById("godOmenText") && document.getElementById("godOmenText").value.trim()) || "";
  if (procText) return "proclamation";
  if (provText) return "providence";
  if (omenText) return "private_omen";
  return null;
}

function godCaptureVoicePresetFields(kind) {
  if (kind === "proclamation") {
    return {
      kind,
      text: document.getElementById("godProcText").value,
      presentation: godReadVoicePresentation("godProcPresentation"),
    };
  }
  if (kind === "providence") {
    return {
      kind,
      text: document.getElementById("godProvText").value,
      durationSeconds: document.getElementById("godProvDuration").value,
      presentation: godReadVoicePresentation("godProvPresentation"),
    };
  }
  if (kind === "private_omen") {
    return {
      kind,
      text: document.getElementById("godOmenText").value,
      durationSeconds: document.getElementById("godOmenDuration").value,
      targetId: document.getElementById("godOmenAgentSelect").value,
    };
  }
  return null;
}

function godApplyVoicePreset(preset) {
  if (!preset || !preset.kind) return;
  if (preset.kind === "proclamation") {
    document.getElementById("godProcText").value = preset.text || "";
    const pres = preset.presentation === "thunder" ? "thunder" : "soft";
    const radio = document.querySelector(`input[name="godProcPresentation"][value="${pres}"]`);
    if (radio) radio.checked = true;
    document.getElementById("godProclamationFieldset").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (preset.kind === "providence") {
    document.getElementById("godProvText").value = preset.text || "";
    document.getElementById("godProvDuration").value = preset.durationSeconds != null ? String(preset.durationSeconds) : "";
    const pres = preset.presentation === "thunder" ? "thunder" : "soft";
    const radio = document.querySelector(`input[name="godProvPresentation"][value="${pres}"]`);
    if (radio) radio.checked = true;
    document.getElementById("godProvidenceFieldset").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  if (preset.kind === "private_omen") {
    if (preset.targetId != null && document.getElementById("godOmenAgentSelect")) {
      document.getElementById("godOmenAgentSelect").value = String(preset.targetId);
    }
    document.getElementById("godOmenText").value = preset.text || "";
    document.getElementById("godOmenDuration").value = preset.durationSeconds != null ? String(preset.durationSeconds) : "";
    document.getElementById("godOmenFieldset").scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function wireGodVoicePresetControls() {
  populateGodVoicePresetSelect();
  const loadBtn = document.getElementById("godVoicePresetLoadBtn");
  const saveBtn = document.getElementById("godVoicePresetSaveBtn");
  const delBtn = document.getElementById("godVoicePresetDeleteBtn");
  const sel = document.getElementById("godVoicePresetSelect");
  if (loadBtn && sel) {
    loadBtn.addEventListener("click", () => {
      const id = sel.value;
      if (!id) return;
      const preset = loadDivineVoicePresets().find((p) => p.id === id);
      if (preset) godApplyVoicePreset(preset);
    });
  }
  if (saveBtn) {
    saveBtn.addEventListener("click", () => {
      const kind = godDetectActiveVoicePresetKind();
      if (!kind) {
        window.alert("Fill in a proclamation, providence, or omen form first.");
        return;
      }
      const fields = godCaptureVoicePresetFields(kind);
      const name = window.prompt("Preset name:", `${kind} preset`);
      if (!name || !name.trim()) return;
      const presets = loadDivineVoicePresets();
      const id = `vp-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      presets.unshift({ id, name: name.trim(), ...fields });
      saveDivineVoicePresets(presets.slice(0, 32));
      if (sel) sel.value = id;
    });
  }
  if (delBtn && sel) {
    delBtn.addEventListener("click", () => {
      const id = sel.value;
      if (!id) return;
      saveDivineVoicePresets(loadDivineVoicePresets().filter((p) => p.id !== id));
    });
  }
}
wireGodVoicePresetControls();

