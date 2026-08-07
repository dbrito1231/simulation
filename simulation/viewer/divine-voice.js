// --- Voice: proclamation / providence / private omen ---------------------
wireDivineForm("#godProclamationFieldset", {
  previewBtnId: "godProcPreviewBtn", applyBtnId: "godProcApplyBtn", resultElId: "godProcResult",
  label: "proclamation",
  buildEnvelope: () => {
    const text = document.getElementById("godProcText").value;
    if (!text.trim()) return { error: "text is required" };
    return {
      envelope: {
        kind: "proclamation",
        payload: { text, ...godVoicePresentationPayload("godProcPresentation") },
      },
    };
  },
});

wireDivineForm("#godProvidenceFieldset", {
  previewBtnId: "godProvPreviewBtn", applyBtnId: "godProvApplyBtn", resultElId: "godProvResult",
  label: "providence",
  buildEnvelope: () => {
    const text = document.getElementById("godProvText").value;
    if (!text.trim()) return { error: "text is required" };
    const durationRaw = document.getElementById("godProvDuration").value;
    const payload = { text, ...godVoicePresentationPayload("godProvPresentation") };
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "providence", payload } };
  },
});

wireDivineForm("#godOmenFieldset", {
  previewBtnId: "godOmenPreviewBtn", applyBtnId: "godOmenApplyBtn", resultElId: "godOmenResult",
  label: "private omen",
  buildEnvelope: () => {
    const targetId = document.getElementById("godOmenAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const text = document.getElementById("godOmenText").value;
    if (!text.trim()) return { error: "text is required" };
    const durationRaw = document.getElementById("godOmenDuration").value;
    const payload = { targetId: parseInt(targetId, 10), text };
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "private_omen", payload } };
  },
});

const GOD_WHISPER_CAMPAIGN_MAX_TARGETS = 12;

function addGodWhisperRow(selectedId, text) {
  const container = document.getElementById("godWhisperRows");
  if (!container) return;
  const rows = container.querySelectorAll(".god-whisper-row");
  if (rows.length >= GOD_WHISPER_CAMPAIGN_MAX_TARGETS) return;
  const row = document.createElement("div");
  row.className = "divine-row god-whisper-row";
  row.innerHTML =
    `<label>Agent <select class="god-whisper-agent">${godAgentOptionsHtml(selectedId)}</select></label>` +
    `<label>Text <textarea class="god-whisper-text"></textarea></label>` +
    `<button type="button" class="god-whisper-remove">Remove</button>`;
  const textEl = row.querySelector(".god-whisper-text");
  if (textEl && text) textEl.value = text;
  row.querySelector(".god-whisper-remove").addEventListener("click", () => {
    row.remove();
    document.getElementById("godWhisperCampaignFieldset").dispatchEvent(new Event("input", { bubbles: true }));
  });
  container.appendChild(row);
}

function initGodWhisperRows() {
  const container = document.getElementById("godWhisperRows");
  if (!container || container.querySelector(".god-whisper-row")) return;
  const agents = getLivingAgents();
  if (agents.length >= 2) {
    addGodWhisperRow(agents[0].id, "");
    addGodWhisperRow(agents[1].id, "");
  } else if (agents.length === 1) {
    addGodWhisperRow(agents[0].id, "");
  }
}

const godWhisperAddRowBtn = document.getElementById("godWhisperAddRow");
if (godWhisperAddRowBtn) {
  godWhisperAddRowBtn.addEventListener("click", () => addGodWhisperRow(null, ""));
}

wireDivineForm("#godWhisperCampaignFieldset", {
  previewBtnId: "godWhisperPreviewBtn", applyBtnId: "godWhisperApplyBtn", resultElId: "godWhisperResult",
  label: "whisper campaign",
  buildEnvelope: () => {
    const theme = document.getElementById("godWhisperTheme").value;
    if (!theme.trim()) return { error: "theme is required" };
    const rows = document.querySelectorAll("#godWhisperRows .god-whisper-row");
    if (!rows.length) return { error: "add at least one whisper target" };
    if (rows.length > GOD_WHISPER_CAMPAIGN_MAX_TARGETS) {
      return { error: `whispers may include at most ${GOD_WHISPER_CAMPAIGN_MAX_TARGETS} targets` };
    }
    const whispers = [];
    const seen = new Set();
    for (const row of rows) {
      const targetId = row.querySelector(".god-whisper-agent").value;
      if (!targetId) return { error: "select an agent for each whisper row" };
      const tid = parseInt(targetId, 10);
      if (seen.has(tid)) return { error: "duplicate agent in whisper rows" };
      seen.add(tid);
      const text = row.querySelector(".god-whisper-text").value;
      if (!text.trim()) return { error: "text is required for each whisper" };
      whispers.push({ targetId: tid, text });
    }
    const payload = { theme, whispers };
    const durationRaw = document.getElementById("godWhisperDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "whisper_campaign", payload } };
  },
});

const godSamplingTempEl = document.getElementById("godSamplingTemp");
const godSamplingTempOutEl = document.getElementById("godSamplingTempOut");
if (godSamplingTempEl && godSamplingTempOutEl) {
  const syncSamplingTemp = () => { godSamplingTempOutEl.textContent = godSamplingTempEl.value; };
  godSamplingTempEl.addEventListener("input", syncSamplingTemp);
  syncSamplingTemp();
}

wireDivineForm("#godSamplingFieldset", {
  previewBtnId: "godSamplingPreviewBtn", applyBtnId: "godSamplingApplyBtn", resultElId: "godSamplingResult",
  label: "agent sampling",
  buildEnvelope: () => {
    const targetId = document.getElementById("godSamplingAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const temperature = parseFloat(document.getElementById("godSamplingTemp").value);
    if (!Number.isFinite(temperature)) return { error: "temperature must be a number" };
    const payload = {
      targetId: parseInt(targetId, 10),
      model: document.getElementById("godSamplingModel").value || "sim-smart",
      temperature,
    };
    const durationRaw = document.getElementById("godSamplingDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    const topP = document.getElementById("godSamplingTopP").value;
    if (topP) payload.top_p = parseFloat(topP);
    const topK = document.getElementById("godSamplingTopK").value;
    if (topK) payload.top_k = parseInt(topK, 10);
    const minP = document.getElementById("godSamplingMinP").value;
    if (minP) payload.min_p = parseFloat(minP);
    return { envelope: { kind: "agent_sampling", payload } };
  },
});

wireDivineForm("#godSamplingRevokeFieldset", {
  previewBtnId: "godSamplingRevokePreviewBtn", applyBtnId: "godSamplingRevokeApplyBtn",
  resultElId: "godSamplingRevokeResult",
  label: "revoke agent sampling",
  buildEnvelope: () => {
    const targetId = document.getElementById("godSamplingRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_agent_sampling", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godMemoryInsertFieldset", {
  previewBtnId: "godMemoryInsertPreviewBtn", applyBtnId: "godMemoryInsertApplyBtn",
  resultElId: "godMemoryInsertResult",
  label: "memory insert",
  buildEnvelope: () => {
    const targetId = document.getElementById("godMemoryInsertAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const text = document.getElementById("godMemoryInsertText").value;
    if (!text.trim()) return { error: "text is required" };
    const salience = parseFloat(document.getElementById("godMemoryInsertSalience").value);
    if (!Number.isFinite(salience)) return { error: "salience must be a number" };
    const payload = { targetId: parseInt(targetId, 10), text, salience };
    const kind = document.getElementById("godMemoryInsertKind").value.trim();
    if (kind) payload.kind = kind;
    return { envelope: { kind: "memory_insert", payload } };
  },
});

wireDivineForm("#godMemoryDeleteFieldset", {
  previewBtnId: "godMemoryDeletePreviewBtn", applyBtnId: "godMemoryDeleteApplyBtn",
  resultElId: "godMemoryDeleteResult",
  label: "memory delete",
  buildEnvelope: () => {
    const targetId = document.getElementById("godMemoryDeleteAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const keyword = document.getElementById("godMemoryDeleteKeyword").value.trim();
    if (keyword) payload.keyword = keyword;
    const frameFromRaw = document.getElementById("godMemoryDeleteFrameFrom").value;
    if (frameFromRaw) payload.frameFrom = parseInt(frameFromRaw, 10);
    const frameToRaw = document.getElementById("godMemoryDeleteFrameTo").value;
    if (frameToRaw) payload.frameTo = parseInt(frameToRaw, 10);
    const kindsRaw = document.getElementById("godMemoryDeleteKinds").value.trim();
    if (kindsRaw) {
      payload.kinds = kindsRaw.split(",").map((s) => s.trim()).filter(Boolean);
    }
    if (!payload.keyword && payload.frameFrom == null && payload.frameTo == null && !payload.kinds) {
      return { error: "at least one filter (keyword, frame range, or kinds) is required" };
    }
    return { envelope: { kind: "memory_delete", payload } };
  },
});

wireDivineForm("#godBeliefPlantFieldset", {
  previewBtnId: "godBeliefPlantPreviewBtn", applyBtnId: "godBeliefPlantApplyBtn",
  resultElId: "godBeliefPlantResult",
  label: "belief plant",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBeliefPlantAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const beliefId = document.getElementById("godBeliefPlantId").value.trim();
    const text = document.getElementById("godBeliefPlantText").value.trim();
    if (!beliefId && !text) return { error: "belief id or text is required" };
    const salience = parseFloat(document.getElementById("godBeliefPlantSalience").value);
    if (!Number.isFinite(salience)) return { error: "salience must be a number" };
    const payload = {
      targetId: parseInt(targetId, 10),
      plantInMemeTexts: document.getElementById("godBeliefPlantMemeTexts").checked,
      salience,
    };
    if (beliefId) payload.beliefId = beliefId;
    if (text) payload.text = text;
    return { envelope: { kind: "belief_plant", payload } };
  },
});

function syncGodDistortionModeFields() {
  const mode = document.querySelector('input[name="godDistortionMode"]:checked')?.value || "blue_pill";
  const dreamRow = document.getElementById("godDistortionDreamRow");
  const whisperRow = document.getElementById("godDistortionWhisperRow");
  if (dreamRow) dreamRow.style.display = mode === "dream" ? "" : "none";
  if (whisperRow) whisperRow.style.display = mode === "whisper_chain" ? "" : "none";
}
document.querySelectorAll('input[name="godDistortionMode"]').forEach((el) => {
  el.addEventListener("change", syncGodDistortionModeFields);
});
syncGodDistortionModeFields();

const GOD_CROWD_COMPULSION_MAX_TARGETS = 12;
const GOD_DREAM_BROADCAST_MAX_TARGETS = 12;

wireDivineForm("#godDistortionFieldset", {
  previewBtnId: "godDistortionPreviewBtn", applyBtnId: "godDistortionApplyBtn",
  resultElId: "godDistortionResult",
  label: "context mask",
  buildEnvelope: () => {
    const targetId = document.getElementById("godDistortionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const mode = document.querySelector('input[name="godDistortionMode"]:checked')?.value;
    if (!mode) return { error: "select a mask mode" };
    const payload = { targetId: parseInt(targetId, 10), mode };
    const durationRaw = document.getElementById("godDistortionDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    if (mode === "dream") {
      const raw = document.getElementById("godDistortionDreamJson").value.trim();
      if (!raw) return { error: "dream snapshot JSON is required for dream mode" };
      try {
        payload.dreamSnapshot = JSON.parse(raw);
      } catch (err) {
        return { error: "dream snapshot must be valid JSON" };
      }
    }
    if (mode === "whisper_chain") {
      const raw = document.getElementById("godDistortionWhisperJson").value.trim();
      if (!raw) return { error: "forged conversations JSON is required for whisper chain" };
      try {
        payload.forgedConversations = JSON.parse(raw);
      } catch (err) {
        return { error: "forged conversations must be valid JSON" };
      }
    }
    return { envelope: { kind: "context_mask", payload } };
  },
});

wireDivineForm("#godDreamBroadcastFieldset", {
  previewBtnId: "godDreamBroadcastPreviewBtn", applyBtnId: "godDreamBroadcastApplyBtn",
  resultElId: "godDreamBroadcastResult", label: "dream broadcast",
  buildEnvelope: () => {
    const durationRaw = document.getElementById("godDreamBroadcastDuration").value;
    const _df = godSecondsToFrames(durationRaw);
    if (_df == null) return { error: "duration (seconds) is required" };
    const raw = document.getElementById("godDreamBroadcastJson").value.trim();
    if (!raw) return { error: "dream snapshot JSON is required" };
    let dreamSnapshot;
    try {
      dreamSnapshot = JSON.parse(raw);
    } catch (err) {
      return { error: "dream snapshot must be valid JSON" };
    }
    const agentsEl = document.getElementById("godDreamBroadcastAgents");
    const selected = Array.from(agentsEl ? agentsEl.selectedOptions : [])
      .map((o) => parseInt(o.value, 10))
      .filter((id) => Number.isFinite(id));
    if (!selected.length) return { error: "select at least one target agent" };
    if (selected.length > GOD_DREAM_BROADCAST_MAX_TARGETS) {
      return { error: `targetIds may include at most ${GOD_DREAM_BROADCAST_MAX_TARGETS} agents` };
    }
    if (new Set(selected).size !== selected.length) return { error: "duplicate target agents" };
    return {
      envelope: {
        kind: "dream_broadcast",
        payload: { durationFrames: _df, dreamSnapshot, targetIds: selected },
      },
    };
  },
});

function syncGodVetoResolveFields() {
  const mode = document.getElementById("godVetoResolveMode")?.value;
  const row = document.getElementById("godVetoRewriteRow");
  if (row) row.style.display = mode === "rewrite" ? "" : "none";
}
const godVetoResolveModeEl = document.getElementById("godVetoResolveMode");
if (godVetoResolveModeEl) {
  godVetoResolveModeEl.addEventListener("change", syncGodVetoResolveFields);
  syncGodVetoResolveFields();
}

function addGodCrowdRow(selectedId, action) {
  const container = document.getElementById("godCrowdRows");
  if (!container) return;
  const rows = container.querySelectorAll(".god-crowd-row");
  if (rows.length >= GOD_CROWD_COMPULSION_MAX_TARGETS) return;
  const row = document.createElement("div");
  row.className = "divine-row god-crowd-row";
  row.innerHTML =
    `<label>Agent <select class="god-crowd-agent">${godAgentOptionsHtml(selectedId)}</select></label>` +
    `<label>Action <select class="god-crowd-action"><option value="rest">rest</option></select></label>` +
    `<button type="button" class="god-crowd-remove">Remove</button>`;
  const actionEl = row.querySelector(".god-crowd-action");
  if (actionEl && action) actionEl.value = action;
  populateGodPinActionSelects();
  row.querySelector(".god-crowd-remove").addEventListener("click", () => {
    row.remove();
    document.getElementById("godCrowdCompulsionFieldset").dispatchEvent(new Event("input", { bubbles: true }));
  });
  container.appendChild(row);
}

function initGodCrowdRows() {
  const container = document.getElementById("godCrowdRows");
  if (!container || container.querySelector(".god-crowd-row")) return;
  const agents = getLivingAgents();
  if (agents.length >= 2) {
    addGodCrowdRow(agents[0].id, "rest");
    addGodCrowdRow(agents[1].id, "rest");
  } else if (agents.length === 1) {
    addGodCrowdRow(agents[0].id, "rest");
  }
}

const godCrowdAddRowBtn = document.getElementById("godCrowdAddRow");
if (godCrowdAddRowBtn) {
  godCrowdAddRowBtn.addEventListener("click", () => addGodCrowdRow(null, "rest"));
}

wireDivineForm("#godCrowdCompulsionFieldset", {
  previewBtnId: "godCrowdPreviewBtn", applyBtnId: "godCrowdApplyBtn", resultElId: "godCrowdResult",
  label: "crowd compulsion",
  buildEnvelope: () => {
    const themeRaw = document.getElementById("godCrowdTheme").value;
    const rows = document.querySelectorAll("#godCrowdRows .god-crowd-row");
    if (!rows.length) return { error: "add at least one target row" };
    if (rows.length > GOD_CROWD_COMPULSION_MAX_TARGETS) {
      return { error: `targets may include at most ${GOD_CROWD_COMPULSION_MAX_TARGETS} agents` };
    }
    const targets = [];
    const seen = new Set();
    for (const row of rows) {
      const targetId = row.querySelector(".god-crowd-agent").value;
      if (!targetId) return { error: "select an agent for each target row" };
      const tid = parseInt(targetId, 10);
      if (seen.has(tid)) return { error: "duplicate agent in crowd rows" };
      seen.add(tid);
      const action = row.querySelector(".god-crowd-action").value || "rest";
      targets.push({
        targetId: tid,
        pinnedDecision: { action, reasoning: "Divine crowd compulsion." },
      });
    }
    const payload = { targets };
    if (themeRaw.trim()) payload.theme = themeRaw.trim();
    const durationRaw = document.getElementById("godCrowdDuration").value;
    const turnsRaw = document.getElementById("godCrowdTurns").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    if (turnsRaw) payload.remainingTurns = parseInt(turnsRaw, 10);
    if (!payload.durationFrames && !payload.remainingTurns) {
      return { error: "set duration (seconds) or remaining turns" };
    }
    return { envelope: { kind: "crowd_compulsion", payload } };
  },
});

wireDivineForm("#godCompulsionFieldset", {
  previewBtnId: "godCompulsionPreviewBtn", applyBtnId: "godCompulsionApplyBtn",
  resultElId: "godCompulsionResult", label: "decision compulsion",
  buildEnvelope: () => {
    const targetId = document.getElementById("godCompulsionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const action = document.getElementById("godCompulsionAction").value;
    const payload = {
      targetId: parseInt(targetId, 10),
      pinnedDecision: { action, reasoning: "Divine compulsion." },
    };
    const durationRaw = document.getElementById("godCompulsionDuration").value;
    const turnsRaw = document.getElementById("godCompulsionTurns").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    if (turnsRaw) payload.remainingTurns = parseInt(turnsRaw, 10);
    if (!payload.durationFrames && !payload.remainingTurns) {
      return { error: "set duration (seconds) or remaining turns" };
    }
    return { envelope: { kind: "decision_compulsion", payload } };
  },
});

wireDivineForm("#godVetoArmFieldset", {
  previewBtnId: "godVetoArmPreviewBtn", applyBtnId: "godVetoArmApplyBtn",
  resultElId: "godVetoArmResult", label: "veto arm",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVetoArmAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const durationRaw = document.getElementById("godVetoArmDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "decision_veto_arm", payload } };
  },
});

wireDivineForm("#godVetoResolveFieldset", {
  previewBtnId: "godVetoResolvePreviewBtn", applyBtnId: "godVetoResolveApplyBtn",
  resultElId: "godVetoResolveResult", label: "veto resolve",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVetoResolveAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const resolution = document.getElementById("godVetoResolveMode").value;
    const payload = { targetId: parseInt(targetId, 10), resolution };
    if (resolution === "rewrite") {
      const action = document.getElementById("godVetoRewriteAction").value;
      payload.rewrittenDecision = { action, reasoning: "Divine veto rewrite." };
    }
    return { envelope: { kind: "decision_veto_resolve", payload } };
  },
});

wireDivineForm("#godPossessionFieldset", {
  previewBtnId: "godPossessionPreviewBtn", applyBtnId: "godPossessionApplyBtn",
  resultElId: "godPossessionResult", label: "agent possession",
  buildEnvelope: () => {
    const targetId = document.getElementById("godPossessionAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const action = document.getElementById("godPossessionAction").value;
    const payload = {
      targetId: parseInt(targetId, 10),
      pinnedDecision: { action, reasoning: "Divine possession." },
    };
    const durationRaw = document.getElementById("godPossessionDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "agent_possession", payload } };
  },
});

wireDivineForm("#godGateRevokeFieldset", {
  previewBtnId: "godGateRevokePreviewBtn", applyBtnId: "godGateRevokeApplyBtn",
  resultElId: "godGateRevokeResult", label: "revoke decision gate",
  buildEnvelope: () => {
    const targetId = document.getElementById("godGateRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_decision_gate", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

function godBuildBargainPredicate(kind, arg1, arg2) {
  if (kind === "agent_has_resource") {
    if (!arg1) return { error: "resource id required for agent_has_resource" };
    const pred = { kind, resourceId: arg1.trim() };
    if (arg2) {
      const amount = parseInt(arg2, 10);
      if (!Number.isInteger(amount)) return { error: "amount must be a valid integer" };
      pred.amount = amount;
    }
    return { predicate: pred };
  }
  if (kind === "structure_built") {
    if (!arg1) return { error: "structure type required for structure_built" };
    return { predicate: { kind, structureType: arg1.trim() } };
  }
  if (kind === "frame_reached") {
    if (!arg1) return { error: "frame required for frame_reached" };
    const frame = parseInt(arg1, 10);
    if (!Number.isFinite(frame) || frame < 0) return { error: "frame must be a non-negative integer" };
    return { predicate: { kind, frame } };
  }
  if (kind === "agent_health_below") {
    if (!arg1) return { error: "threshold required for agent_health_below" };
    const threshold = parseFloat(arg1);
    if (!Number.isFinite(threshold)) return { error: "threshold must be a number" };
    return { predicate: { kind, threshold } };
  }
  return { error: "unknown predicate kind" };
}

wireDivineForm("#godBurningBushFieldset", {
  previewBtnId: "godBurningBushPreviewBtn", applyBtnId: "godBurningBushApplyBtn",
  resultElId: "godBurningBushResult", label: "burning bush message",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBurningBushAgentSelect").value;
    const text = (document.getElementById("godBurningBushText").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!text) return { error: "message text required" };
    return { envelope: { kind: "burning_bush_message", payload: { targetId: parseInt(targetId, 10), text } } };
  },
});

wireDivineForm("#godBurningBushCloseFieldset", {
  previewBtnId: "godBurningBushClosePreviewBtn", applyBtnId: "godBurningBushCloseApplyBtn",
  resultElId: "godBurningBushCloseResult", label: "burning bush close",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBurningBushCloseAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "burning_bush_close", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godBargainFieldset", {
  previewBtnId: "godBargainPreviewBtn", applyBtnId: "godBargainApplyBtn",
  resultElId: "godBargainResult", label: "merovingian bargain",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBargainAgentSelect").value;
    const termsText = (document.getElementById("godBargainTerms").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!termsText) return { error: "bargain terms required" };
    const succKind = document.getElementById("godBargainSuccessKind").value;
    const succBuilt = godBuildBargainPredicate(
      succKind,
      document.getElementById("godBargainSuccessArg1").value,
      document.getElementById("godBargainSuccessArg2").value,
    );
    if (succBuilt.error) return succBuilt;
    const payload = {
      targetId: parseInt(targetId, 10),
      termsText,
      successPredicate: succBuilt.predicate,
    };
    const durationRaw = document.getElementById("godBargainDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    const rewardKind = document.getElementById("godBargainRewardKind").value;
    if (rewardKind === "grant_resource") {
      const resourceId = (document.getElementById("godBargainRewardResource").value || "").trim();
      const amount = parseInt(document.getElementById("godBargainRewardAmount").value, 10) || 1;
      if (!resourceId) return { error: "reward resource id required" };
      payload.rewardPrimitive = {
        kind: "grant_resource",
        payload: { resourceId, amount, target: { agentId: parseInt(targetId, 10) } },
      };
    } else if (rewardKind === "agent_vitals") {
      payload.rewardPrimitive = {
        kind: "agent_vitals",
        payload: { targetId: parseInt(targetId, 10), healthDelta: 10, hungerDelta: 0 },
      };
    }
    const punishKind = document.getElementById("godBargainPunishKind").value;
    if (punishKind === "agent_vitals") {
      const healthDelta = parseFloat(document.getElementById("godBargainPunishHealth").value) || -10;
      payload.punishPrimitive = {
        kind: "agent_vitals",
        payload: { targetId: parseInt(targetId, 10), healthDelta, hungerDelta: 0 },
      };
    }
    return { envelope: { kind: "merovingian_bargain", payload } };
  },
});

wireDivineForm("#godBargainSettleFieldset", {
  previewBtnId: "godBargainSettlePreviewBtn", applyBtnId: "godBargainSettleApplyBtn",
  resultElId: "godBargainSettleResult", label: "bargain settle",
  buildEnvelope: () => {
    const targetId = document.getElementById("godBargainSettleAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const outcome = document.getElementById("godBargainSettleOutcome").value;
    return { envelope: { kind: "bargain_settle", payload: { targetId: parseInt(targetId, 10), outcome } } };
  },
});

function parseGodOracleHints(raw) {
  const hints = [];
  for (const line of (raw || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const pipe = trimmed.indexOf("|");
    if (pipe < 1) return { error: `oracle hint must be revealFrame|text: ${trimmed}` };
    const revealFrame = parseInt(trimmed.slice(0, pipe).trim(), 10);
    const text = trimmed.slice(pipe + 1).trim();
    if (!Number.isFinite(revealFrame) || revealFrame < 0) {
      return { error: `invalid revealFrame in: ${trimmed}` };
    }
    if (!text) return { error: `oracle hint text required: ${trimmed}` };
    hints.push({ revealFrame, text });
  }
  return { hints };
}

wireDivineForm("#godAnointFieldset", {
  previewBtnId: "godAnointPreviewBtn", applyBtnId: "godAnointApplyBtn",
  resultElId: "godAnointResult", label: "anoint",
  buildEnvelope: () => {
    const targetId = document.getElementById("godAnointAgentSelect").value;
    const destinyText = (document.getElementById("godAnointDestiny").value || "").trim();
    if (!targetId) return { error: "select an agent" };
    if (!destinyText) return { error: "destiny text required" };
    const payload = {
      targetId: parseInt(targetId, 10),
      destinyText,
    };
    const stigmataRaw = (document.getElementById("godAnointStigmata").value || "").trim();
    if (stigmataRaw) {
      payload.stigmataTags = stigmataRaw.split(",").map((t) => t.trim()).filter(Boolean);
    }
    const oracleParsed = parseGodOracleHints(document.getElementById("godAnointOracle").value);
    if (oracleParsed.error) return oracleParsed;
    if (oracleParsed.hints.length) payload.oracleHints = oracleParsed.hints;
    const durationRaw = document.getElementById("godAnointDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "anoint", payload } };
  },
});

wireDivineForm("#godAnointRevokeFieldset", {
  previewBtnId: "godAnointRevokePreviewBtn", applyBtnId: "godAnointRevokeApplyBtn",
  resultElId: "godAnointRevokeResult", label: "revoke anoint",
  buildEnvelope: () => {
    const targetId = document.getElementById("godAnointRevokeAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "revoke_anoint", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

wireDivineForm("#godIdentityEditFieldset", {
  previewBtnId: "godIdentityEditPreviewBtn", applyBtnId: "godIdentityEditApplyBtn",
  resultElId: "godIdentityEditResult", label: "identity edit",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityEditAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const persona = (document.getElementById("godIdentityEditPersona").value || "").trim();
    const personality = (document.getElementById("godIdentityEditPersonality").value || "").trim();
    const role = (document.getElementById("godIdentityEditRole").value || "").trim();
    if (persona) payload.persona = persona;
    if (personality) payload.personality = personality;
    if (role) payload.role = role;
    if (!persona && !personality && !role) {
      return { error: "enter at least one of persona, personality, or role" };
    }
    const durationRaw = document.getElementById("godIdentityEditDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "identity_edit", payload } };
  },
});

wireDivineForm("#godIdentityCopyFieldset", {
  previewBtnId: "godIdentityCopyPreviewBtn", applyBtnId: "godIdentityCopyApplyBtn",
  resultElId: "godIdentityCopyResult", label: "identity copy",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityCopyTargetSelect").value;
    const sourceId = document.getElementById("godIdentityCopySourceSelect").value;
    if (!targetId || !sourceId) return { error: "select target and source agents" };
    if (targetId === sourceId) return { error: "target and source must differ" };
    const rate = parseFloat(document.getElementById("godIdentityCopyRate").value);
    if (!Number.isFinite(rate) || rate < 0 || rate > 1) {
      return { error: "rate per think must be 0.0–1.0" };
    }
    const payload = {
      targetId: parseInt(targetId, 10),
      sourceId: parseInt(sourceId, 10),
      ratePerThink: rate,
      syncMemories: document.getElementById("godIdentityCopySyncMemories").checked,
    };
    const durationRaw = document.getElementById("godIdentityCopyDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    return { envelope: { kind: "identity_copy_overwrite", payload } };
  },
});

wireDivineForm("#godIdentityCancelFieldset", {
  previewBtnId: "godIdentityCancelPreviewBtn", applyBtnId: "godIdentityCancelApplyBtn",
  resultElId: "godIdentityCancelResult", label: "identity forge cancel",
  buildEnvelope: () => {
    const targetId = document.getElementById("godIdentityCancelAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    return { envelope: { kind: "identity_forge_cancel", payload: { targetId: parseInt(targetId, 10) } } };
  },
});

function parseGodArchitectCells(raw) {
  const text = (raw || "").trim();
  if (!text) return { error: "cells are required" };
  if (text.startsWith("{")) {
    try {
      const bounds = JSON.parse(text);
      return { cells: [bounds] };
    } catch (_e) {
      return { error: "bounds JSON is invalid" };
    }
  }
  const cells = [];
  for (const line of text.split(/\r?\n/)) {
    const part = line.trim();
    if (!part) continue;
    if (!/^\d+\s*,\s*\d+$/.test(part)) return { error: `invalid cell line: ${part}` };
    cells.push(part.replace(/\s+/g, ""));
  }
  if (!cells.length) return { error: "cells are required" };
  return { cells };
}

function selectedGodAgentIds(selectId) {
  const el = document.getElementById(selectId);
  if (!el) return [];
  return Array.from(el.selectedOptions || []).map((o) => parseInt(o.value, 10)).filter((n) => Number.isFinite(n));
}

wireDivineForm("#godArchitectZoneFieldset", {
  previewBtnId: "godArchitectZonePreviewBtn", applyBtnId: "godArchitectZoneApplyBtn",
  resultElId: "godArchitectZoneResult", label: "architect zone",
  buildEnvelope: () => {
    const zoneKind = document.getElementById("godArchitectZoneKind").value;
    const districtId = document.getElementById("godArchitectDistrict").value;
    const parsed = parseGodArchitectCells(document.getElementById("godArchitectCells").value);
    if (parsed.error) return parsed;
    const payload = { zoneKind, cells: parsed.cells };
    if (districtId) payload.districtId = districtId;
    const durationRaw = document.getElementById("godArchitectDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    if (zoneKind === "paint") {
      if (!districtId) return { error: "district is required for paint zones" };
      payload.paintTerrain = document.getElementById("godArchitectPaintTerrain").value;
      payload.reversible = document.getElementById("godArchitectReversible").checked;
    }
    if (zoneKind === "door") {
      if (!districtId) return { error: "district is required for door zones" };
      const keyId = (document.getElementById("godArchitectKeyId").value || "").trim();
      if (!keyId) return { error: "key id is required for door zones" };
      payload.keyId = keyId;
      const grantIds = selectedGodAgentIds("godArchitectGrantKeyAgents");
      if (grantIds.length) payload.grantKeyAgentIds = grantIds;
    }
    if (zoneKind === "limbo") {
      const holdIds = selectedGodAgentIds("godArchitectHoldAgents");
      if (!holdIds.length) return { error: "select at least one limbo hold agent" };
      payload.holdAgentIds = holdIds;
    }
    return { envelope: { kind: "architect_zone", payload } };
  },
});

wireDivineForm("#godArchitectCancelFieldset", {
  previewBtnId: "godArchitectCancelPreviewBtn", applyBtnId: "godArchitectCancelApplyBtn",
  resultElId: "godArchitectCancelResult", label: "architect zone cancel",
  buildEnvelope: () => {
    const zoneId = (document.getElementById("godArchitectCancelZoneId").value || "").trim();
    if (!zoneId) return { error: "zone id is required" };
    return { envelope: { kind: "architect_zone_cancel", payload: { zoneId } } };
  },
});

wireDivineForm("#godArchitectReleaseFieldset", {
  previewBtnId: "godArchitectReleasePreviewBtn", applyBtnId: "godArchitectReleaseApplyBtn",
  resultElId: "godArchitectReleaseResult", label: "architect release hold",
  buildEnvelope: () => {
    const zoneId = (document.getElementById("godArchitectReleaseZoneId").value || "").trim();
    if (!zoneId) return { error: "zone id is required" };
    const payload = { zoneId };
    const agentIds = selectedGodAgentIds("godArchitectReleaseAgents");
    if (agentIds.length) payload.agentIds = agentIds;
    return { envelope: { kind: "architect_release_hold", payload } };
  },
});

wireDivineForm("#godCheckpointCreateFieldset", {
  previewBtnId: "godCheckpointCreatePreviewBtn", applyBtnId: "godCheckpointCreateApplyBtn",
  resultElId: "godCheckpointCreateResult", label: "checkpoint create",
  buildEnvelope: () => {
    const label = (document.getElementById("godCheckpointLabel").value || "").trim();
    if (!label) return { error: "label is required" };
    const payload = { label };
    if (document.getElementById("godCheckpointReplaceOldest").checked) {
      payload.replaceOldest = true;
    }
    return { envelope: { kind: "checkpoint_create", payload } };
  },
  onApplied: () => refreshGodSight(),
});

wireDivineForm("#godCheckpointRestoreFieldset", {
  previewBtnId: "godCheckpointRestorePreviewBtn", applyBtnId: "godCheckpointRestoreApplyBtn",
  resultElId: "godCheckpointRestoreResult", label: "checkpoint restore",
  buildEnvelope: () => {
    const checkpointId = (document.getElementById("godCheckpointRestoreSelect").value || "").trim();
    if (!checkpointId) return { error: "select a checkpoint" };
    return { envelope: { kind: "checkpoint_restore", payload: { checkpointId } } };
  },
  onApplied: () => refreshGodSight(),
});

wireDivineForm("#godDejaVuFieldset", {
  previewBtnId: "godDejaVuPreviewBtn", applyBtnId: "godDejaVuApplyBtn",
  resultElId: "godDejaVuResult", label: "deja vu replay",
  buildEnvelope: () => {
    const targetId = document.getElementById("godDejaVuAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const payload = { targetId: parseInt(targetId, 10) };
    const maxRaw = document.getElementById("godDejaVuMaxSteps").value;
    if (maxRaw) payload.maxSteps = parseInt(maxRaw, 10);
    return { envelope: { kind: "deja_vu_replay", payload } };
  },
  onApplied: () => refreshGodSight(),
});

