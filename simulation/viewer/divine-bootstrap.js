// =====================================================================
// Divine Console (Sovereign God mode Phase 7, docs/plan-sovereign-god-mode-
// v2.md "Viewer: Divine Console"). Strictly additive and feature-gated on
// GOD_MODE_ENABLED_FLAG (mirrors config.flags.GOD_MODE_ENABLED from /state,
// see applyFlags above): every function below is a no-op, and the panel/
// banner stay display:none, unless the flag is true. No new poll loop is
// added -- this reuses the existing pollState()->renderSidebar() cadence for
// the passive History/Laws/banner projections, and only fetches
// /control/god/* on an explicit user click (Connect/Preview/Apply/Cancel/
// Refresh).
//
// Security contract (docs/plan "Feature gate and security contract" +
// "Stored-content safety"):
//   - the token lives in the `godToken` variable only -- never localStorage,
//     optionally sessionStorage behind the explicit "remember" checkbox;
//   - every dynamic string reaching innerHTML goes through escapeHtml();
//     normalizedCommand is NEVER rendered raw -- previews/results are
//     rebuilt field-by-field from known, typed response keys;
//   - a 401 from any /control/god/* call clears the in-memory token and
//     re-locks the console (godApiFetch below);
//   - Apply always sends only {previewId, requestId} -- see wireDivineForm.
// =====================================================================

let godToken = null;               // in-memory ONLY
let godAuthorized = false;
let godCapabilities = null;        // last /control/god/capabilities response
let godLastSight = null;           // last /control/god/sight response
let godActiveTab = "unlock";
let godLastStateKey = null;        // change-detect for world.god (History/Laws/banner)
let godSeenInterventionIds = null; // Set of ids already shown in the public banner (edge-detected, like foundingFramesSeen)
let godBannerTimer = null;
let godBarPulseTimer = null;
let godLastSightFetchedAt = 0;
let godSightBarRefreshInFlight = false;
const GOD_SIGHT_BAR_REFRESH_MS = 30000;
const GOD_SIGHT_MODAL_REFRESH_MS = 1500;

const godTokenInput = document.getElementById("godTokenInput");
const godRememberCheckbox = document.getElementById("godRememberCheckbox");
const godConnectBtn = document.getElementById("godConnectBtn");
const godAuthStatusEl = document.getElementById("godAuthStatus");
const divineBarEl = document.getElementById("divineBar");
const divineBarBrandStateEl = document.getElementById("divineBarBrandState");
const divineBarInterventionCountEl = document.getElementById("divineBarInterventionCount");
const divineBarEffectsEl = document.getElementById("divineBarEffects");
const divineVoicePipEl = document.getElementById("divineVoicePip");
const divineLawsPipEl = document.getElementById("divineLawsPip");
const divineMatrixPipEl = document.getElementById("divineMatrixPip");
const divinePreviewStripEl = document.getElementById("divinePreviewStrip");
const divinePreviewStripLabelEl = document.getElementById("divinePreviewStripLabel");
const divinePreviewApplyBtnEl = document.getElementById("divinePreviewApplyBtn");
const divinePreviewDiscardBtnEl = document.getElementById("divinePreviewDiscardBtn");
const divineUnlockPipEl = document.getElementById("divineUnlockPip");
const divineModalScrimEl = document.getElementById("divineModalScrim");
const divineModalEl = document.getElementById("divineModal");
const divineModalBodyEl = document.getElementById("divineModalBody");
const divineModalIconEl = document.getElementById("divineModalIcon");
const divineModalTitleEl = document.getElementById("divineModalTitle");
const divineModalSubEl = document.getElementById("divineModalSub");
const divineTabHoldEl = document.getElementById("divineTabHold");
const divineTooltipEl = document.getElementById("tooltip");
const godPublicBannerEl = document.getElementById("godPublicBanner");
const godHistoryListEl = document.getElementById("godHistoryList");
const godLawsActiveEl = document.getElementById("godLawsActive");
const godSightOutputEl = document.getElementById("godSightOutput");
const godSightAgentSelectEl = document.getElementById("godSightAgentSelect");
const godVoiceAdherenceTimelineEl = document.getElementById("godVoiceAdherenceTimeline");
const godVoiceReplyInboxEl = document.getElementById("godVoiceReplyInbox");
const DIVINE_VOICE_PRESETS_KEY = "divineVoicePresets";
const GOD_VOICE_ADHERENCE_CAP = 30;
const GOD_VOICE_REPLY_SNIPPET = 80;
const GOD_VOICE_REPLY_MAX = 200;

const DIVINE_FEATURE_ICONS = {
  unlock:  '<svg viewBox="0 0 24 24"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 7.5-2"/></svg>',
  sight:   '<svg viewBox="0 0 24 24"><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.5"/></svg>',
  voice:   '<svg viewBox="0 0 24 24"><path d="M4 8h9l5-3v14l-5-3H4z"/><path d="M20 9a4 4 0 0 1 0 6"/></svg>',
  matrix:  '<svg viewBox="0 0 24 24"><path d="M4 4h16v16H4z"/><path d="M4 9h16M9 4v16"/></svg>',
  miracles:'<svg viewBox="0 0 24 24"><path d="M12 3l1.8 4.4L18 9l-4.2 1.6L12 15l-1.8-4.4L6 9l4.2-1.6z"/><path d="M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z"/></svg>',
  story:   '<svg viewBox="0 0 24 24"><path d="M4 5a2 2 0 0 1 2-2h6v18H6a2 2 0 0 1-2-2z"/><path d="M12 3h6a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-6"/></svg>',
  laws:    '<svg viewBox="0 0 24 24"><path d="M12 3v18M5 7h14M7 7l-3 6h6zM17 7l3 6h-6z"/></svg>',
  history: '<svg viewBox="0 0 24 24"><path d="M3 5h14v14H3z"/><path d="M7 9h6M7 13h6M7 5v14"/></svg>',
  compile: '<svg viewBox="0 0 24 24"><path d="M6 20l9-14M15 6l3 1-1 3M9 20l-3-1 1-3"/></svg>',
};
const DIVINE_PREVIEW_TIP = {
  t: "Preview",
  d: "Check that this is allowed without changing the village yet.",
};
const DIVINE_APPLY_TIP = {
  t: "Apply",
  d: "Make it real (uses the last successful check).",
};
const DIVINE_APPLY_IRREVERSIBLE_TIP = {
  t: "Apply",
  d: "Make it real — cannot be undone.",
};

function divineTipAttr(tip) {
  return JSON.stringify(tip).replace(/'/g, "&#39;");
}

const DIVINE_FEATURES = {
  unlock: {
    title: "Unlock",
    sub: "Enter your secret token to use Divine tools.",
    guide: [
      "Sign in with the secret password set when the server starts.",
      "Only you see this console — villagers never know you are here.",
      "Remember saves the token until you close this browser tab.",
      "You can use every other tool after a successful connect.",
    ],
    gated: false,
  },
  sight: {
    title: "Sight",
    sub: "Look at private details villagers do not show each other.",
    guide: [
      "Pick a villager to see health, ties, active whispers, and hidden effects.",
      "This view is for you only — nothing here is announced to the village.",
      "Refresh updates the panel; it does not change the world.",
      "Use the quick buttons to jump into Voice, Miracles, or Matrix for that villager.",
    ],
    gated: true,
  },
  voice: {
    title: "Voice",
    sub: "Speak to the whole village, set ongoing guidance, or whisper to one or many.",
    guide: [
      "Proclamations and Providence are public — everyone sees them and must respond.",
      "Omens and whisper campaigns are private — only the targeted villager hears their line.",
      "Most Voice effects can be cancelled early from History or the bar.",
      "Check Adherence below to see who followed your guidance and why.",
    ],
    gated: true,
  },
  matrix: {
    title: "Matrix",
    sub: "Change how villagers think, remember, perceive, and move — mostly in secret.",
    guide: [
      "These tools nudge minds, memories, perception, identity, and the map.",
      "Villagers usually cannot tell you intervened; some map changes are visible.",
      "Preview checks the action; Apply makes it real.",
      "Timed effects expire on their own — many can also be cancelled.",
    ],
    gated: true,
  },
  miracles: {
    title: "Miracles",
    sub: "Directly change health, goods, or buildings.",
    guide: [
      "Heal or hurt a villager, grant resources, or repair or damage structures.",
      "Effects apply immediately and are permanent — there is no undo.",
      "Preview shows what will happen; Apply commits it forever.",
      "Undoing a miracle means issuing the opposite change as a new action.",
    ],
    gated: true,
  },
  story: {
    title: "Story",
    sub: "Run a timed story event with optional world effects.",
    guide: [
      "Title and narration can be public for the whole village or private for one villager.",
      "Timed modifiers bend hunger, gathering, and more while the event runs.",
      "Adding instant effects makes the story consequential — past changes stay even if you cancel the timer.",
      "You can cancel an active story event early; that stops future effects, not what already happened.",
    ],
    gated: true,
  },
  laws: {
    title: "Laws",
    sub: "Temporary world rules that change how the village works.",
    guide: [
      "Laws scale hunger, gathering, spoilage, and similar rules for a set time.",
      "A value of 1.0 means normal — the law does nothing.",
      "Active laws appear above the form; cancel ends one early.",
      "Laws are cancellable while running; they do not roll back time.",
    ],
    gated: true,
  },
  history: {
    title: "History",
    sub: "A log of what you have done, newest first.",
    guide: [
      "Every applied action appears here — public village events and your private moves.",
      "Filter by kind, villager, or time; export a Markdown summary for notes.",
      "Re-run fills a form from a past entry — you must Preview again before Apply.",
      "Revoke last cancellable undoes the newest effect that still allows cancel.",
    ],
    gated: true,
  },
  compile: {
    title: "Compile",
    sub: "Turn a written story idea into a Story draft (optional advanced tool).",
    guide: [
      "Paste prose and the compiler suggests a Story event draft.",
      "Nothing changes until you review in Story and Apply yourself.",
      "Experimental — your server admin must enable the compiler.",
      "Compile never applies directly; it only fills the Story form for you.",
    ],
    gated: true,
  },
};

let divineFeatureGuideEl = null;

function clearDivineFeatureGuide() {
  if (divineFeatureGuideEl) {
    divineFeatureGuideEl.remove();
    divineFeatureGuideEl = null;
  }
}

function renderDivineFeatureGuide(name) {
  clearDivineFeatureGuide();
  if (!divineModalBodyEl) return;
  const feature = DIVINE_FEATURES[name];
  if (!feature || !feature.guide || !feature.guide.length) return;
  divineFeatureGuideEl = document.createElement("div");
  divineFeatureGuideEl.id = "divineFeatureGuide";
  divineFeatureGuideEl.className = "divine-feature-guide";
  const titleEl = document.createElement("div");
  titleEl.className = "divine-feature-guide-title";
  titleEl.textContent = feature.title;
  divineFeatureGuideEl.appendChild(titleEl);
  const bodyEl = document.createElement("p");
  bodyEl.className = "divine-feature-guide-body";
  bodyEl.textContent = feature.guide.join(" ");
  divineFeatureGuideEl.appendChild(bodyEl);
  divineModalBodyEl.insertBefore(divineFeatureGuideEl, divineModalBodyEl.firstChild);
}

function syncDivineBarTooltips() {
  if (!divineBarEl) return;
  divineBarEl.querySelectorAll(".gbtn[data-feature]").forEach((btn) => {
    const key = btn.dataset.feature;
    const feature = DIVINE_FEATURES[key];
    if (!feature) return;
    btn.setAttribute("data-tip", JSON.stringify({ t: feature.title, d: feature.sub }));
  });
}

function reorderDivineModalBodyChildren() {
  if (!divineModalBodyEl) return;
  const guide = document.getElementById("divineFeatureGuide");
  const pin = document.getElementById("divinePinRow");
  const panel = divineModalOpenFeature
    ? document.getElementById("divineTab-" + divineModalOpenFeature)
    : null;
  if (guide) divineModalBodyEl.insertBefore(guide, divineModalBodyEl.firstChild);
  if (pin) {
    const after = guide || divineModalBodyEl.firstChild;
    if (after === pin) return;
    divineModalBodyEl.insertBefore(pin, guide ? guide.nextSibling : divineModalBodyEl.firstChild);
  }
  if (panel && panel.parentElement === divineModalBodyEl) {
    divineModalBodyEl.appendChild(panel);
  }
}

function godFramesToSeconds(frames) {
  return Math.round((Number(frames) || 0) / 30);
}

function godSecondsToFrames(secRaw) {
  if (secRaw === "" || secRaw == null) return null;
  const sec = Number(secRaw);
  if (!Number.isFinite(sec)) return null;
  return Math.round(sec * 30);
}

function godPreferredAgentId() {
  const living = getLivingAgents();
  if (godFocusAgentId != null && living.some((a) => a.id === godFocusAgentId)) return godFocusAgentId;
  if (selectedAgentId != null && living.some((a) => a.id === selectedAgentId)) return selectedAgentId;
  return null;
}

function setGodFocusAgent(id, opts) {
  opts = opts || {};
  godFocusAgentId = id;
  if (opts.mirrorSelection !== false && id != null) {
    selectedAgentId = id;
    syncAgentListSelection();
    renderAgentDetail();
    const agent = getLivingAgents().find((a) => a.id === id);
    if (agent && opts.centerCamera) centerCameraOnAgent(agent);
  }
  populateGodAgentSelects();
}

function godAgentFilterText() {
  const el = document.getElementById("godAgentFilter");
  return el ? el.value.trim().toLowerCase() : "";
}

// Both simulation time AND raw frames, per docs/plan UX requirement.
function godDurationLabel(frames) {
  return `${godFramesToSeconds(frames)}s (${Math.round(Number(frames) || 0)}f)`;
}

function godCountdownLabel(expiresFrame) {
  const now = world.frameTick || 0;
  const remaining = Math.max(0, (Number(expiresFrame) || 0) - now);
  return remaining > 0 ? `expires in ${godDurationLabel(remaining)}` : "expired (awaiting cleanup)";
}

function godAgentOptionsHtml(selectedId, filterText) {
  const q = filterText != null ? String(filterText).trim().toLowerCase() : godAgentFilterText();
  let agents = getLivingAgents();
  if (q) {
    agents = agents.filter((a) =>
      String(a.name || "").toLowerCase().includes(q)
      || String(a.role || "").toLowerCase().includes(q)
      || String(a.id).includes(q)
    );
  }
  return agents.map((a) =>
    `<option value="${a.id}"${a.id === selectedId ? " selected" : ""}>${escapeHtml(a.name)} (#${a.id}, ${escapeHtml(a.role)})</option>`
  ).join("") || `<option value="">(no matching agents)</option>`;
}

function godFillAgentSelect(el, preferredId) {
  if (!el) return;
  const prior = el.value;
  const priorNum = prior ? Number(prior) : null;
  const pick = (priorNum != null && getLivingAgents().some((a) => a.id === priorNum))
    ? priorNum
    : preferredId;
  el.innerHTML = godAgentOptionsHtml(pick);
  if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  else if (pick != null && Array.from(el.options).some((o) => Number(o.value) === pick)) el.value = String(pick);
}

function populateGodAgentSelects() {
  const preferred = godPreferredAgentId();
  [godSightAgentSelectEl, document.getElementById("godOmenAgentSelect"),
   document.getElementById("godVitalsAgentSelect"), document.getElementById("godGrantAgentSelect"),
   document.getElementById("godStoryTargetSelect"),
   document.getElementById("godSamplingAgentSelect"),
   document.getElementById("godSamplingRevokeAgentSelect"),
   document.getElementById("godMemoryInsertAgentSelect"),
   document.getElementById("godMemoryDeleteAgentSelect"),
   document.getElementById("godBeliefPlantAgentSelect"),
   document.getElementById("godDistortionAgentSelect"),
   document.getElementById("godCompulsionAgentSelect"),
   document.getElementById("godVetoArmAgentSelect"),
   document.getElementById("godVetoResolveAgentSelect"),
   document.getElementById("godPossessionAgentSelect"),
   document.getElementById("godGateRevokeAgentSelect"),
   document.getElementById("godBurningBushAgentSelect"),
   document.getElementById("godBurningBushCloseAgentSelect"),
   document.getElementById("godBargainAgentSelect"),
   document.getElementById("godBargainSettleAgentSelect"),
   document.getElementById("godAnointAgentSelect"),
   document.getElementById("godAnointRevokeAgentSelect"),
   document.getElementById("godIdentityEditAgentSelect"),
   document.getElementById("godIdentityCopyTargetSelect"),
   document.getElementById("godIdentityCopySourceSelect"),
   document.getElementById("godIdentityCancelAgentSelect"),
   document.getElementById("godDejaVuAgentSelect")].forEach((el) => {
    godFillAgentSelect(el, preferred);
  });
  const resourceSelect = document.getElementById("godGrantResourceSelect");
  if (resourceSelect) {
    const reg = resourceRegistry();
    resourceSelect.innerHTML = Object.keys(reg).map((id) =>
      `<option value="${escapeHtml(id)}">${escapeHtml(reg[id].label || id)}</option>`
    ).join("");
  }
  const structureSelect = document.getElementById("godStructureSelect");
  if (structureSelect) {
    const structures = (getCiv().structures || []).filter((s) => !s.isRuin && (s.condition == null || s.condition > 0));
    structureSelect.innerHTML = structures.map((s) =>
      `<option value="${s.id}">${escapeHtml(s.name || s.type)} (#${s.id})</option>`
    ).join("") || `<option value="">(no structures)</option>`;
  }
  const districtOptions = getDistricts().map((d) =>
    `<option value="${escapeHtml(d.id)}">${escapeHtml(d.label || d.id)}</option>`
  ).join("") || `<option value="">(no districts)</option>`;
  ["godMassRepairDistrict", "godClearRuinsDistrict", "godArchitectDistrict"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const prior = el.value;
    el.innerHTML = districtOptions;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
  ["godArchitectGrantKeyAgents", "godArchitectHoldAgents", "godArchitectReleaseAgents"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const selected = new Set(Array.from(el.selectedOptions || []).map((o) => o.value));
    const q = godAgentFilterText();
    let agents = getLivingAgents();
    if (q) {
      agents = agents.filter((a) =>
        String(a.name || "").toLowerCase().includes(q)
        || String(a.role || "").toLowerCase().includes(q)
        || String(a.id).includes(q)
      );
    }
    el.innerHTML = agents.map((a) =>
      `<option value="${a.id}">${escapeHtml(a.name)} (#${a.id})</option>`
    ).join("") || "";
    Array.from(el.options).forEach((o) => { o.selected = selected.has(o.value); });
  });
  const whisperRows = document.querySelectorAll("#godWhisperRows .god-whisper-row");
  whisperRows.forEach((row) => {
    const sel = row.querySelector(".god-whisper-agent");
    if (!sel) return;
    const prior = sel.value;
    const priorId = prior ? parseInt(prior, 10) : null;
    const pick = (priorId != null && getLivingAgents().some((a) => a.id === priorId))
      ? priorId
      : preferred;
    sel.innerHTML = godAgentOptionsHtml(pick);
    if (prior && Array.from(sel.options).some((o) => o.value === prior)) sel.value = prior;
  });
  if (!whisperRows.length) initGodWhisperRows();
  const crowdRows = document.querySelectorAll("#godCrowdRows .god-crowd-row");
  crowdRows.forEach((row) => {
    const sel = row.querySelector(".god-crowd-agent");
    if (!sel) return;
    const prior = sel.value;
    const priorId = prior ? parseInt(prior, 10) : null;
    const pick = (priorId != null && getLivingAgents().some((a) => a.id === priorId))
      ? priorId
      : preferred;
    sel.innerHTML = godAgentOptionsHtml(pick);
    if (prior && Array.from(sel.options).some((o) => o.value === prior)) sel.value = prior;
  });
  if (!crowdRows.length) initGodCrowdRows();
  const dreamAgentsEl = document.getElementById("godDreamBroadcastAgents");
  if (dreamAgentsEl) {
    const selected = new Set(Array.from(dreamAgentsEl.selectedOptions || []).map((o) => o.value));
    const q = godAgentFilterText();
    let agents = getLivingAgents();
    if (q) {
      agents = agents.filter((a) =>
        String(a.name || "").toLowerCase().includes(q)
        || String(a.role || "").toLowerCase().includes(q)
        || String(a.id).includes(q)
      );
    }
    dreamAgentsEl.innerHTML = agents.map((a) =>
      `<option value="${a.id}">${escapeHtml(a.name)} (#${a.id})</option>`
    ).join("") || "";
    Array.from(dreamAgentsEl.options).forEach((o) => { o.selected = selected.has(o.value); });
  }
  populateGodPinActionSelects();
}

const GOD_PIN_ACTIONS = [
  "rest", "collect_resource", "contribute_resources", "build_structure",
  "talk_to_nearby", "move_to_district", "move_to_agent", "craft_item", "heal_agent",
];

function populateGodPinActionSelects() {
  const html = GOD_PIN_ACTIONS.map((a) =>
    `<option value="${escapeHtml(a)}">${escapeHtml(actionLabel(a))}</option>`
  ).join("");
  ["godCompulsionAction", "godPossessionAction", "godVetoRewriteAction"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    const prior = el.value;
    el.innerHTML = html;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
  document.querySelectorAll("#godCrowdRows .god-crowd-action").forEach((el) => {
    const prior = el.value;
    el.innerHTML = html;
    if (prior && Array.from(el.options).some((o) => o.value === prior)) el.value = prior;
  });
}

