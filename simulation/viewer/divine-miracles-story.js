// --- Miracles: agent_vitals / grant_resource / structure_condition -------
wireDivineForm("#godVitalsFieldset", {
  previewBtnId: "godVitalsPreviewBtn", applyBtnId: "godVitalsApplyBtn", resultElId: "godVitalsResult",
  label: "agent vitals",
  buildEnvelope: () => {
    const targetId = document.getElementById("godVitalsAgentSelect").value;
    if (!targetId) return { error: "select an agent" };
    const health = parseFloat(document.getElementById("godVitalsHealth").value || "0");
    const hunger = parseFloat(document.getElementById("godVitalsHunger").value || "0");
    if (!health && !hunger) return { error: "at least one of health/hunger delta must be non-zero" };
    const payload = { targetId: parseInt(targetId, 10) };
    if (health) payload.healthDelta = health;
    if (hunger) payload.hungerDelta = hunger;
    return { envelope: { kind: "agent_vitals", payload } };
  },
});

wireDivineForm("#godGrantFieldset", {
  previewBtnId: "godGrantPreviewBtn", applyBtnId: "godGrantApplyBtn", resultElId: "godGrantResult",
  label: "grant",
  buildEnvelope: () => {
    const resourceId = document.getElementById("godGrantResourceSelect").value;
    if (!resourceId) return { error: "select a resource" };
    const amount = parseInt(document.getElementById("godGrantAmount").value || "0", 10);
    if (!amount || amount <= 0) return { error: "amount must be positive" };
    const targetKind = document.getElementById("godGrantTargetKind").value;
    let target = "stockpile";
    if (targetKind === "agent") {
      const agentId = document.getElementById("godGrantAgentSelect").value;
      if (!agentId) return { error: "select a target agent" };
      target = { agentId: parseInt(agentId, 10) };
    }
    return { envelope: { kind: "grant_resource", payload: { resourceId, amount, target } } };
  },
});

wireDivineForm("#godStructureFieldset", {
  previewBtnId: "godStructurePreviewBtn", applyBtnId: "godStructureApplyBtn", resultElId: "godStructureResult",
  label: "structure condition",
  buildEnvelope: () => {
    const structureId = document.getElementById("godStructureSelect").value;
    if (!structureId) return { error: "select a structure" };
    const delta = parseFloat(document.getElementById("godStructureDelta").value || "0");
    if (!delta) return { error: "delta must be non-zero" };
    return { envelope: { kind: "structure_condition", payload: { structureId: parseInt(structureId, 10), delta } } };
  },
});

wireDivineForm("#godMassRepairFieldset", {
  previewBtnId: "godMassRepairPreviewBtn", applyBtnId: "godMassRepairApplyBtn", resultElId: "godMassRepairResult",
  label: "mass repair structures",
  buildEnvelope: () => {
    const scopeMode = document.getElementById("godMassRepairScope").value;
    const payload = { unRuin: document.getElementById("godMassRepairUnRuin").checked };
    const targetRaw = document.getElementById("godMassRepairTarget").value;
    if (targetRaw) {
      const conditionTarget = parseFloat(targetRaw);
      if (!Number.isFinite(conditionTarget) || conditionTarget < 0 || conditionTarget > 100) {
        return { error: "condition target must be 0–100" };
      }
      payload.conditionTarget = conditionTarget;
    }
    if (scopeMode === "ids") {
      const raw = document.getElementById("godMassRepairIds").value.trim();
      if (!raw) return { error: "enter comma-separated structure ids" };
      const structureIds = raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n));
      if (!structureIds.length) return { error: "invalid structure ids" };
      payload.scope = "ids";
      payload.structureIds = structureIds;
    } else if (scopeMode === "district") {
      const districtId = document.getElementById("godMassRepairDistrict").value;
      if (!districtId) return { error: "select a district" };
      payload.scope = { districtId };
    } else {
      payload.scope = "all_critical";
    }
    return { envelope: { kind: "repair_structures", payload } };
  },
});

wireDivineForm("#godClearRuinsFieldset", {
  previewBtnId: "godClearRuinsPreviewBtn", applyBtnId: "godClearRuinsApplyBtn", resultElId: "godClearRuinsResult",
  label: "clear ruins",
  buildEnvelope: () => {
    const mode = document.getElementById("godClearRuinsMode").value;
    const payload = {};
    const minAgeRaw = document.getElementById("godClearRuinsMinAge").value;
    if (minAgeRaw) {
      const minAgeFrames = parseInt(minAgeRaw, 10);
      if (!Number.isFinite(minAgeFrames) || minAgeFrames < 0) return { error: "min age must be a non-negative integer" };
      payload.minAgeFrames = minAgeFrames;
    }
    if (mode === "ids") {
      const raw = document.getElementById("godClearRuinsIds").value.trim();
      if (!raw) return { error: "enter comma-separated ruin ids" };
      const structureIds = raw.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n));
      if (!structureIds.length) return { error: "invalid ruin ids" };
      payload.structureIds = structureIds;
    } else if (mode === "district") {
      const districtId = document.getElementById("godClearRuinsDistrict").value;
      if (!districtId) return { error: "select a district" };
      payload.districtId = districtId;
      if (!("minAgeFrames" in payload)) payload.minAgeFrames = godDefaultDayFrames();
    } else {
      if (!("minAgeFrames" in payload)) payload.minAgeFrames = godDefaultDayFrames();
    }
    return { envelope: { kind: "clear_ruins", payload } };
  },
});

// --- Shared modifier editor (Story + Laws both submit story_event) -------
const GOD_MODIFIER_KEYS = [
  "gather_yield_multiplier", "fish_yield_multiplier", "hunger_drain_multiplier",
  "health_regen_multiplier", "starvation_damage_multiplier", "structure_decay_multiplier",
  "spoilage_multiplier",
];
function renderGodModifierEditor(containerId, prefix) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const ranges = (godCapabilities && godCapabilities.modifierRanges) || {};
  el.innerHTML = GOD_MODIFIER_KEYS.map((key) => {
    const range = ranges[key] || [0, 3];
    return `<label style="flex-direction:row; align-items:center; gap:6px;">` +
      `<input type="checkbox" class="${prefix}-mod-enable" data-key="${escapeHtml(key)}" style="width:auto;" /> ` +
      `${escapeHtml(key)} <input type="number" class="${prefix}-mod-value" data-key="${escapeHtml(key)}" ` +
      `min="${range[0]}" max="${range[1]}" step="0.05" value="1.0" style="width:80px;" />` +
      ` <span class="divine-meta">(${range[0]}..${range[1]})</span></label>`;
  }).join("");
}
function godReadModifiers(prefix) {
  const modifiers = {};
  document.querySelectorAll(`.${prefix}-mod-enable`).forEach((cb) => {
    if (!cb.checked) return;
    const key = cb.dataset.key;
    const valueInput = document.querySelector(`.${prefix}-mod-value[data-key="${key}"]`);
    modifiers[key] = parseFloat(valueInput ? valueInput.value : "1.0");
  });
  return modifiers;
}

function godWriteModifiers(prefix, modifiers, clearOthers) {
  document.querySelectorAll(`.${prefix}-mod-enable`).forEach((cb) => {
    const key = cb.dataset.key;
    const valueInput = document.querySelector(`.${prefix}-mod-value[data-key="${key}"]`);
    const val = modifiers && Object.prototype.hasOwnProperty.call(modifiers, key)
      ? modifiers[key]
      : null;
    if (val != null) {
      cb.checked = true;
      if (valueInput) valueInput.value = String(val);
    } else if (clearOthers) {
      cb.checked = false;
      if (valueInput) valueInput.value = "1.0";
    }
  });
}

const GOD_STORY_RECIPES = {
  festival: {
    label: "Festival",
    title: "Village Festival",
    narration: "Music and feast fill the square; the harvest feels blessed.",
    durationSeconds: 120,
    modifiers: { gather_yield_multiplier: 2.0, hunger_drain_multiplier: 0.5 },
  },
  famine_week: {
    label: "Famine week",
    title: "Lean Season",
    narration: "Stores dwindle and every meal feels insufficient.",
    durationSeconds: 180,
    modifiers: {
      gather_yield_multiplier: 0.5,
      hunger_drain_multiplier: 2.0,
      starvation_damage_multiplier: 1.5,
    },
  },
  plague_scare: {
    label: "Plague scare",
    title: "Whispers of Pestilence",
    narration: "A cough travels faster than comfort; healers work through the night.",
    durationSeconds: 90,
    modifiers: { health_regen_multiplier: 0.5, starvation_damage_multiplier: 2.0 },
  },
  harsh_winter: {
    label: "Harsh winter",
    title: "Bitter Cold",
    narration: "Frost claims stores and timbers alike; warmth is rationed.",
    durationSeconds: 150,
    modifiers: { spoilage_multiplier: 2.0, structure_decay_multiplier: 2.0 },
  },
  bountiful_seas: {
    label: "Bountiful seas",
    title: "Calm Waters",
    narration: "The tide brings fish in abundance; bellies stay full longer.",
    durationSeconds: 120,
    modifiers: { fish_yield_multiplier: 2.5, hunger_drain_multiplier: 0.75 },
  },
};

function godApplyStoryRecipe(recipeKey) {
  const recipe = GOD_STORY_RECIPES[recipeKey];
  if (!recipe) return;
  document.getElementById("godStoryTitle").value = recipe.title || "";
  document.getElementById("godStoryNarration").value = recipe.narration || "";
  if (recipe.durationSeconds != null) {
    document.getElementById("godStoryDuration").value = String(recipe.durationSeconds);
  }
  godWriteModifiers("gs", recipe.modifiers || {}, true);
  const primContainer = document.getElementById("godStoryPrimitives");
  if (primContainer) {
    primContainer.innerHTML = "";
    godStoryPrimitiveCount = 0;
  }
  document.getElementById("godStoryProvidenceCheckbox").checked = false;
  document.getElementById("godStoryProvidenceText").value = "";
  document.getElementById("godStoryReplaceCheckbox").checked = false;
  document.getElementById("godStoryReplaceCheckbox").disabled = true;
  document.getElementById("godStoryReplaceId").value = "";
  document.getElementById("godStoryFieldset").dispatchEvent(new Event("input", { bubbles: true }));
}

function godInitStoryRecipeSelect() {
  const sel = document.getElementById("godStoryRecipeSelect");
  if (!sel || sel.options.length > 1) return;
  sel.innerHTML = `<option value="">(choose a recipe)</option>` +
    Object.entries(GOD_STORY_RECIPES).map(([key, r]) =>
      `<option value="${escapeHtml(key)}">${escapeHtml(r.label || key)}</option>`
    ).join("");
}

// --- Story primitives editor (bounded, up to GOD_STORY_EVENT_MAX_PRIMITIVES) ---
let godStoryPrimitiveCount = 0;
function godPrimitiveFieldsHtml(kind, index) {
  if (kind === "agent_vitals") {
    return `<select class="godPrimAgent">${godAgentOptionsHtml(null)}</select>` +
      `<input type="number" class="godPrimHealth" placeholder="health delta" value="0" style="width:90px;" />` +
      `<input type="number" class="godPrimHunger" placeholder="hunger delta" value="0" style="width:90px;" />`;
  }
  if (kind === "grant_resource") {
    const reg = resourceRegistry();
    const resOptions = Object.keys(reg).map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(reg[id].label || id)}</option>`).join("");
    return `<select class="godPrimResource">${resOptions}</select>` +
      `<input type="number" class="godPrimAmount" placeholder="amount" value="1" min="1" style="width:70px;" />` +
      `<select class="godPrimTargetKind"><option value="stockpile">Stockpile</option><option value="agent">Agent</option></select>` +
      `<select class="godPrimTargetAgent">${godAgentOptionsHtml(null)}</select>`;
  }
  if (kind === "structure_condition") {
    const structures = (getCiv().structures || []).filter((s) => !s.isRuin && (s.condition == null || s.condition > 0));
    const structOptions = structures.map((s) => `<option value="${s.id}">${escapeHtml(s.name || s.type)} (#${s.id})</option>`).join("");
    return `<select class="godPrimStructure">${structOptions}</select>` +
      `<input type="number" class="godPrimDelta" placeholder="delta" value="0" style="width:80px;" />`;
  }
  return "";
}
document.getElementById("godStoryAddPrimitiveBtn").addEventListener("click", () => {
  const cap = (godCapabilities && godCapabilities.kinds && godCapabilities.kinds.story_event &&
    godCapabilities.kinds.story_event.payload.primitives && godCapabilities.kinds.story_event.payload.primitives.maxItems) || 5;
  if (godStoryPrimitiveCount >= cap) return;
  const container = document.getElementById("godStoryPrimitives");
  const row = document.createElement("div");
  row.className = "divine-primitive-row";
  const index = godStoryPrimitiveCount++;
  row.dataset.index = index;
  row.innerHTML = `<select class="godPrimKind">` +
    `<option value="agent_vitals">Agent vitals</option>` +
    `<option value="grant_resource">Grant resource</option>` +
    `<option value="structure_condition">Structure condition</option>` +
    `</select><span class="godPrimFields">${godPrimitiveFieldsHtml("agent_vitals", index)}</span>` +
    `<button type="button" class="godPrimRemove">Remove</button>`;
  container.appendChild(row);
  row.querySelector(".godPrimKind").addEventListener("change", (e) => {
    row.querySelector(".godPrimFields").innerHTML = godPrimitiveFieldsHtml(e.target.value, index);
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });
  row.querySelector(".godPrimRemove").addEventListener("click", () => {
    row.remove();
    container.dispatchEvent(new Event("change", { bubbles: true }));
  });
  container.dispatchEvent(new Event("change", { bubbles: true }));
});

function godReadStoryPrimitives() {
  const rows = Array.from(document.querySelectorAll("#godStoryPrimitives .divine-primitive-row"));
  const primitives = [];
  for (const row of rows) {
    const kind = row.querySelector(".godPrimKind").value;
    if (kind === "agent_vitals") {
      const agentId = row.querySelector(".godPrimAgent").value;
      if (!agentId) return { error: "primitive: select an agent" };
      const health = parseFloat(row.querySelector(".godPrimHealth").value || "0");
      const hunger = parseFloat(row.querySelector(".godPrimHunger").value || "0");
      if (!health && !hunger) return { error: "primitive: health/hunger delta required" };
      const payload = { targetId: parseInt(agentId, 10) };
      if (health) payload.healthDelta = health;
      if (hunger) payload.hungerDelta = hunger;
      primitives.push({ kind: "agent_vitals", payload });
    } else if (kind === "grant_resource") {
      const resourceId = row.querySelector(".godPrimResource").value;
      const amount = parseInt(row.querySelector(".godPrimAmount").value || "0", 10);
      if (!resourceId || !amount) return { error: "primitive: resource + amount required" };
      const targetKind = row.querySelector(".godPrimTargetKind").value;
      let target = "stockpile";
      if (targetKind === "agent") {
        const agentId = row.querySelector(".godPrimTargetAgent").value;
        if (!agentId) return { error: "primitive: select a target agent" };
        target = { agentId: parseInt(agentId, 10) };
      }
      primitives.push({ kind: "grant_resource", payload: { resourceId, amount, target } });
    } else if (kind === "structure_condition") {
      const structureId = row.querySelector(".godPrimStructure").value;
      const delta = parseFloat(row.querySelector(".godPrimDelta").value || "0");
      if (!structureId || !delta) return { error: "primitive: structure + non-zero delta required" };
      primitives.push({ kind: "structure_condition", payload: { structureId: parseInt(structureId, 10), delta } });
    }
  }
  return { primitives };
}

const godStoryRecipeApplyBtn = document.getElementById("godStoryRecipeApplyBtn");
if (godStoryRecipeApplyBtn) {
  godStoryRecipeApplyBtn.addEventListener("click", () => {
    const sel = document.getElementById("godStoryRecipeSelect");
    const key = sel ? sel.value : "";
    if (!key) return;
    godApplyStoryRecipe(key);
  });
}
godInitStoryRecipeSelect();

// --- Story tab ------------------------------------------------------------
wireDivineForm("#godStoryFieldset", {
  previewBtnId: "godStoryPreviewBtn", applyBtnId: "godStoryApplyBtn", resultElId: "godStoryResult",
  label: "story event",
  buildEnvelope: () => {
    const title = document.getElementById("godStoryTitle").value;
    const narration = document.getElementById("godStoryNarration").value;
    if (!title.trim() || !narration.trim()) return { error: "title and narration are required" };
    const visibility = document.getElementById("godStoryVisibility").value;
    const payload = { title, narration, visibility };
    if (visibility === "private") {
      const targetId = document.getElementById("godStoryTargetSelect").value;
      if (!targetId) return { error: "select a private target agent" };
      payload.targetId = parseInt(targetId, 10);
    }
    const durationRaw = document.getElementById("godStoryDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    const modifiers = godReadModifiers("gs");
    if (Object.keys(modifiers).length) payload.modifiers = modifiers;
    const primResult = godReadStoryPrimitives();
    if (primResult.error) return { error: primResult.error };
    if (primResult.primitives.length) payload.primitives = primResult.primitives;
    if (document.getElementById("godStoryProvidenceCheckbox").checked) {
      const provText = document.getElementById("godStoryProvidenceText").value;
      if (!provText.trim()) return { error: "providence text is required when the checkbox is on" };
      payload.providence = { text: provText };
    }
    const replaceCheckbox = document.getElementById("godStoryReplaceCheckbox");
    const replaceId = document.getElementById("godStoryReplaceId").value;
    if (replaceCheckbox.checked && replaceId) payload.replaceEffectId = replaceId;
    return { envelope: { kind: "story_event", payload } };
  },
  onPreviewRejected: (reason) => {
    // Hard-reject conflict (backend rejects rather than disclosing an
    // outgoingId for modifier keys — see report's backend-gap note): parse
    // the id out of the reason string so the operator can opt into replacing
    // it without having to already know the id.
    const match = /\(id ([^)]+)\)/.exec(String(reason || ""));
    const replaceCheckbox = document.getElementById("godStoryReplaceCheckbox");
    const replaceIdInput = document.getElementById("godStoryReplaceId");
    if (match) {
      replaceCheckbox.disabled = false;
      replaceIdInput.value = match[1];
    } else {
      replaceCheckbox.disabled = true;
      replaceCheckbox.checked = false;
      replaceIdInput.value = "";
    }
  },
  onApplied: () => {
    document.getElementById("godStoryReplaceCheckbox").checked = false;
    document.getElementById("godStoryReplaceCheckbox").disabled = true;
    document.getElementById("godStoryReplaceId").value = "";
  },
});

// --- Compile tab (Sovereign God mode Optional Phase 8, docs/plan-sovereign-
// god-mode-v2.md "Free-prose story compiler") ---------------------------
// Deliberately NOT wired through wireDivineForm/Apply: a successful compile
// fills the Story tab and switches to it -- the operator still has to
// explicitly Preview (again, through the normal Story tab flow) and Apply
// there. No shortcut Apply button exists on this tab.
let godCompilerMinIntervalSec = 5;

// Populates the Story tab's own fields from a compiled draft's
// normalizedCommand. When skipPreviewInvalidate is true (compile handoff),
// leaves any server preview intact so acceptServerPreview can wire the strip.
function godPopulateStoryFromCompiled(normalizedCommand, opts = {}) {
  const payload = (normalizedCommand && normalizedCommand.payload) || {};
  document.getElementById("godStoryTitle").value = payload.title || "";
  document.getElementById("godStoryNarration").value = payload.narration || "";
  const visibilityEl = document.getElementById("godStoryVisibility");
  visibilityEl.value = payload.visibility === "private" ? "private" : "public";
  if (payload.visibility === "private" && payload.targetId != null) {
    document.getElementById("godStoryTargetSelect").value = String(payload.targetId);
  }
  document.getElementById("godStoryDuration").value = payload.durationFrames != null
    ? godFramesToSeconds(payload.durationFrames) : "";

  document.querySelectorAll(".gs-mod-enable").forEach((cb) => { cb.checked = false; });
  const modifiers = payload.modifiers || {};
  Object.keys(modifiers).forEach((key) => {
    const cb = document.querySelector(`.gs-mod-enable[data-key="${key}"]`);
    const valueInput = document.querySelector(`.gs-mod-value[data-key="${key}"]`);
    if (cb) cb.checked = true;
    if (valueInput) valueInput.value = modifiers[key];
  });

  const primContainer = document.getElementById("godStoryPrimitives");
  primContainer.innerHTML = "";
  godStoryPrimitiveCount = 0;
  const addBtn = document.getElementById("godStoryAddPrimitiveBtn");
  (payload.primitives || []).forEach((prim) => {
    addBtn.click();
    const row = primContainer.lastElementChild;
    if (!row) return;
    const kindSelect = row.querySelector(".godPrimKind");
    kindSelect.value = prim.kind;
    kindSelect.dispatchEvent(new Event("change"));
    const p = prim.payload || {};
    if (prim.kind === "agent_vitals") {
      if (p.targetId != null) row.querySelector(".godPrimAgent").value = String(p.targetId);
      if (p.healthDelta != null) row.querySelector(".godPrimHealth").value = p.healthDelta;
      if (p.hungerDelta != null) row.querySelector(".godPrimHunger").value = p.hungerDelta;
    } else if (prim.kind === "grant_resource") {
      if (p.resourceId) row.querySelector(".godPrimResource").value = p.resourceId;
      if (p.amount != null) row.querySelector(".godPrimAmount").value = p.amount;
      if (p.target && typeof p.target === "object" && p.target.agentId != null) {
        row.querySelector(".godPrimTargetKind").value = "agent";
        row.querySelector(".godPrimTargetAgent").value = String(p.target.agentId);
      }
    } else if (prim.kind === "structure_condition") {
      if (p.structureId != null) row.querySelector(".godPrimStructure").value = String(p.structureId);
      if (p.delta != null) row.querySelector(".godPrimDelta").value = p.delta;
    }
  });

  if (payload.providence) {
    document.getElementById("godStoryProvidenceCheckbox").checked = true;
    document.getElementById("godStoryProvidenceText").value = payload.providence.text || "";
  } else {
    document.getElementById("godStoryProvidenceCheckbox").checked = false;
    document.getElementById("godStoryProvidenceText").value = "";
  }
  document.getElementById("godStoryReplaceCheckbox").checked = false;
  document.getElementById("godStoryReplaceCheckbox").disabled = true;
  document.getElementById("godStoryReplaceId").value = "";

  if (!opts.skipPreviewInvalidate) {
    // Any field edit invalidates a stale preview per the standard contract.
    document.getElementById("godStoryFieldset").dispatchEvent(new Event("input"));
  }
}

function godNormalizeCompilePreviewHandoff(data) {
  return {
    ok: true,
    previewId: data.previewId,
    commandDigest: data.commandDigest,
    previewOutcome: data.previewOutcome,
    normalizedCommand: data.normalizedCommand,
    reversibilityClass: data.reversibilityClass,
    expiresAt: data.expiresAt,
    warnings: data.warnings,
  };
}

const godCompileBtnEl = document.getElementById("godCompileBtn");
const godCompileResultEl = document.getElementById("godCompileResult");
if (godCompileBtnEl) {
  godCompileBtnEl.addEventListener("click", async () => {
    const proseEl = document.getElementById("godCompileProse");
    const prose = proseEl.value;
    if (!prose || !prose.trim()) {
      godCompileResultEl.textContent = "Enter some prose first.";
      return;
    }
    godCompileBtnEl.disabled = true;
    godCompileResultEl.textContent = "Compiling...";
    const resp = await godApiFetch("/control/god/compile", { method: "POST", body: { prose } });
    const data = resp.data || {};
    if (data.compileOk) {
      godCompileResultEl.textContent = "Compiled — Story fields filled; preview ready in the strip.";
      godPopulateStoryFromCompiled(data.normalizedCommand, { skipPreviewInvalidate: true });
      const storyCtrl = godDivineFormControllers["#godStoryFieldset"];
      if (storyCtrl) storyCtrl.acceptServerPreview(godNormalizeCompilePreviewHandoff(data));
      openDivineModal("story");
    } else {
      // Rejection or error -- render the reason as plain text only (rule:
      // stored-content safety -- never innerHTML for anything the compiler
      // or its model produced).
      godCompileResultEl.textContent = data.reason || "compile failed";
    }
    // Client-side rate-limit UX only -- the server's GOD_COMPILER_MIN_INTERVAL_SEC
    // check is authoritative regardless of what this timer does.
    setTimeout(() => { godCompileBtnEl.disabled = false; }, godCompilerMinIntervalSec * 1000);
  });
}

// --- Laws tab (a story_event carrying ONLY modifiers, no primitives) -----
wireDivineForm("#godLawFieldset", {
  previewBtnId: "godLawPreviewBtn", applyBtnId: "godLawApplyBtn", resultElId: "godLawResult",
  label: "law",
  buildEnvelope: () => {
    const modifiers = godReadModifiers("gl");
    if (!Object.keys(modifiers).length) return { error: "enable at least one modifier" };
    let title = document.getElementById("godLawTitle").value.trim();
    let narration = document.getElementById("godLawNarration").value.trim();
    if (!title) title = "Divine decree";
    if (!narration) {
      narration = "The village feels a shift: " +
        Object.entries(modifiers).map(([k, v]) => `${k} set to ${v}`).join(", ") + ".";
    }
    const payload = { title, narration, visibility: "public", modifiers };
    const durationRaw = document.getElementById("godLawDuration").value;
    const _df = godSecondsToFrames(durationRaw); if (_df != null) payload.durationFrames = _df;
    const replaceCheckbox = document.getElementById("godLawReplaceCheckbox");
    const replaceId = document.getElementById("godLawReplaceId").value;
    if (replaceCheckbox.checked && replaceId) payload.replaceEffectId = replaceId;
    return { envelope: { kind: "story_event", payload } };
  },
  onPreviewRejected: (reason) => {
    const match = /\(id ([^)]+)\)/.exec(String(reason || ""));
    const replaceCheckbox = document.getElementById("godLawReplaceCheckbox");
    const replaceIdInput = document.getElementById("godLawReplaceId");
    if (match) {
      replaceCheckbox.disabled = false;
      replaceIdInput.value = match[1];
    } else {
      replaceCheckbox.disabled = true;
      replaceCheckbox.checked = false;
      replaceIdInput.value = "";
    }
  },
  onApplied: () => {
    document.getElementById("godLawReplaceCheckbox").checked = false;
    document.getElementById("godLawReplaceCheckbox").disabled = true;
    document.getElementById("godLawReplaceId").value = "";
    renderGodLawsActive();
  },
});

async function godCancelEffect(targetId) {
  const resp = await godApiFetch("/control/god/cancel", { method: "POST", body: { targetId } });
  refreshGodSightIfOpen();
  return resp;
}

function renderGodLawsActive() {
  // Prefer the authenticated Sight projection (includes private-visibility
  // law events, which the public /state god.activePublicEvents omits) --
  // falls back to the public projection when Sight hasn't been fetched yet.
  const events = (godLastSight && godLastSight.activeEvents)
    || (world.god && world.god.activePublicEvents) || [];
  const lawEvents = events.filter((e) => e && e.status === "active" && e.modifiers && Object.keys(e.modifiers).length);
  if (!lawEvents.length) {
    godLawsActiveEl.innerHTML = `<div class="divine-note">No active timed law modifiers.</div>`;
    return;
  }
  godLawsActiveEl.innerHTML = lawEvents.map((e) => {
    const mods = Object.entries(e.modifiers).map(([k, v]) => `${escapeHtml(k)}=${escapeHtml(String(v))}`).join(", ");
    return `<div class="divine-history-item">` +
      `<div>${escapeHtml(e.title || "law")} — ${mods}</div>` +
      `<div class="divine-meta">${escapeHtml(godCountdownLabel(e.expiresFrame))}</div>` +
      `<div class="divine-actions"><button type="button" class="godCancelLawBtn" data-id="${escapeHtml(e.id)}">Cancel</button></div>` +
      `</div>`;
  }).join("");
  godLawsActiveEl.querySelectorAll(".godCancelLawBtn").forEach((btn) => {
    btn.setAttribute("data-tip", JSON.stringify({
      t: "Cancel law",
      d: "End this temporary rule early. Does not undo what already happened.",
    }));
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await godCancelEffect(btn.dataset.id);
      renderGodLawsActive();
    });
  });
}

