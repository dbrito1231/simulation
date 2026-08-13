// =====================================================================
// Phase D — Council panel (pure renderer over civ.councilLog, the engine's
// persisted debate records). Latest debate renders as a thread of proposal
// cards (winner highlighted, losers greyed with their rejection reasons);
// older debates collapse to a one-line history list.
// =====================================================================
// Debate records carry a wall-clock "ts" (ISO string) alongside "frame" as
// of 2026-07-07; records persisted before that change have no "ts", so fall
// back to the frame number rather than showing a blank/invalid time.
function formatCouncilTime(ts, frame) {
  if (ts) {
    const d = new Date(ts);
    if (!isNaN(d.getTime())) {
      const now = new Date();
      const sameDay = d.toDateString() === now.toDateString();
      return sameDay
        ? d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
        : d.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    }
  }
  return frame != null ? `frame ${frame}` : "unknown time";
}

function councilCardHtml(p, verdict) {
  const approvedId = verdict && verdict.approved_id;
  const reasons = (verdict && verdict.reasons_per_candidate) || {};
  const isWinner = approvedId && p.id === approvedId;
  const reason = reasons[p.id];
  const needs = Object.entries(p.needs || {}).map(([k, v]) => `${k}×${v}`).join(", ");
  const cls = isWinner ? "council-card winner" : (verdict ? "council-card loser" : "council-card");
  return `<div class="${cls}" role="button" tabindex="0">` +
    `<span class="cc-name">${escapeHtml(p.name || p.id)}</span> ` +
    `<span class="cc-by">by ${escapeHtml(p.proposer || "?")}</span>` +
    `<span class="cc-fn">${escapeHtml(p.function_summary || "")}${needs ? " · needs " + escapeHtml(needs) : ""}</span>` +
    (isWinner ? `<span class="cc-verdict">✔ approved</span>` : "") +
    (reason ? `<span class="cc-reason">✘ ${escapeHtml(reason)}</span>` : "") +
    `</div>`;
}

const councilTranscriptModal = document.getElementById("councilTranscriptModal");
const councilTranscriptBodyEl = document.getElementById("councilTranscriptBody");
const councilTranscriptTitleEl = document.getElementById("councilTranscriptTitle");
const councilTranscriptCloseBtn = document.getElementById("councilTranscriptCloseBtn");

// Daily Council is intentionally a read-only view of the serialized session.
// The only local state below is modal preference, so closing it never changes
// the simulation and a session cannot force the observer to keep it open.
const councilAssemblyModal = document.getElementById("councilAssemblyModal");
const councilAssemblyCanvas = document.getElementById("councilAssemblyCanvas");
const councilAssemblyCtx = councilAssemblyCanvas.getContext("2d");
const councilAssemblyPhaseEl = document.getElementById("councilAssemblyPhase");
const councilAssemblyAgendaEl = document.getElementById("councilAssemblyAgenda");
const councilAssemblyTallyEl = document.getElementById("councilAssemblyTally");
const councilAssemblyTranscriptEl = document.getElementById("councilAssemblyTranscript");
const councilAssemblyBallotSectionEl = document.getElementById("councilAssemblyBallotSection");
const councilAssemblyVerdictSectionEl = document.getElementById("councilAssemblyVerdictSection");
const councilAssemblyVerdictHeadingEl = document.getElementById("councilAssemblyVerdictHeading");
const councilAssemblyVerdictEl = document.getElementById("councilAssemblyVerdict");
const councilAssemblyCloseBtn = document.getElementById("councilAssemblyCloseBtn");
const councilAssemblyReopenBtn = document.getElementById("councilAssemblyReopenBtn");
let councilAssemblyDismissedId = null;
let councilAssemblyAutoOpenedId = null;

function dailyCouncilId(council) {
  return council ? `${council.day ?? "?"}:${council.frame ?? "?"}` : null;
}

function isDailyCouncilLive(council) {
  return !!(council && council.phase && council.phase !== "adjourned");
}

function closeCouncilAssembly(manual = true) {
  const council = getCiv().dailyCouncil;
  if (manual && council) councilAssemblyDismissedId = dailyCouncilId(council);
  councilAssemblyModal.classList.remove("open");
  if (isDailyCouncilLive(council)) councilAssemblyReopenBtn.classList.add("visible");
}

function openCouncilAssembly() {
  const council = getCiv().dailyCouncil;
  if (!isDailyCouncilLive(council)) return;
  councilAssemblyDismissedId = null;
  councilAssemblyAutoOpenedId = dailyCouncilId(council);
  councilAssemblyModal.classList.add("open");
  councilAssemblyReopenBtn.classList.remove("visible");
  renderDailyCouncil(council);
}

function dailyCouncilTranscriptEntry(entry) {
  const who = entry.who || entry.proposer || entry.elder || "Council";
  const text = entry.text || entry.message || entry.title || entry.outcome || entry.type || "event";
  const feeling = entry.feeling ? ` <span class="assembly-feeling">feeling: ${escapeHtml(entry.feeling)}</span>` : "";
  const time = formatCouncilTime(entry.ts, entry.frame);
  return `<div class="assembly-entry"><span class="assembly-time">${escapeHtml(time)}</span>` +
    `<span class="assembly-who">${escapeHtml(who)}</span>: ${escapeHtml(text)}${feeling}</div>`;
}

// Autoscroll only sticks the view to the newest entry while the observer is
// already at (or near) the bottom -- if they've scrolled up to read earlier
// discussion, the ~10Hz /state poll must not yank them back down every tick.
function isScrolledNearBottom(el, thresholdPx = 40) {
  return el.scrollHeight - el.scrollTop - el.clientHeight <= thresholdPx;
}

function renderDailyCouncil(council) {
  const live = isDailyCouncilLive(council);
  if (!live) {
    councilAssemblyModal.classList.remove("open");
    councilAssemblyReopenBtn.classList.remove("visible");
    return;
  }
  const id = dailyCouncilId(council);
  if (councilAssemblyAutoOpenedId !== id && councilAssemblyDismissedId !== id) {
    councilAssemblyAutoOpenedId = id;
    councilAssemblyModal.classList.add("open");
  }
  councilAssemblyReopenBtn.classList.toggle("visible", !councilAssemblyModal.classList.contains("open"));
  councilAssemblyPhaseEl.textContent = `${council.phase || "convening"} · day ${council.day ?? "?"}`;
  const agenda = council.agenda || [];
  councilAssemblyAgendaEl.innerHTML = agenda.length
    ? agenda.map((item) => `<li><strong>${escapeHtml(item.topic || "Topic")}</strong>${item.detail ? ` — ${escapeHtml(item.detail)}` : ""}</li>`).join("")
    : "<li>No agenda published yet.</li>";

  const ballot = council.ballot;
  councilAssemblyBallotSectionEl.style.display = ballot ? "block" : "none";
  if (ballot) {
    const votes = ballot.votes || {};
    const attendees = council.attendees || [];
    if (ballot.kind === "succession") {
      const candidates = ballot.candidates || [];
      const totals = Object.fromEntries(candidates.map((name) => [name, 0]));
      let abstain = 0;
      attendees.forEach((name) => {
        const vote = votes[name];
        if (vote in totals) totals[vote] += 1;
        else if (vote === "abstain") abstain += 1;
      });
      councilAssemblyTallyEl.innerHTML =
        `<div class="assembly-entry"><strong>${escapeHtml(ballot.title || "Choose the next elder")}</strong></div>` +
        candidates.map((name) => `<div class="assembly-vote yes">${escapeHtml(name)} ${totals[name]}</div>`).join("") +
        `<div class="assembly-vote abstain">Abstain ${abstain}</div>` +
        attendees.map((name) => {
          const vote = votes[name] || "pending";
          return `<div class="assembly-vote ${vote === "pending" ? "abstain" : "yes"}">${escapeHtml(name)}: ${escapeHtml(vote)}</div>`;
        }).join("");
    } else {
      const totals = { yes: 0, no: 0, abstain: 0 };
      attendees.forEach((name) => { const vote = votes[name]; if (vote in totals) totals[vote] += 1; });
      councilAssemblyTallyEl.innerHTML =
        `<div class="assembly-vote yes">Yes ${totals.yes}</div><div class="assembly-vote no">No ${totals.no}</div><div class="assembly-vote abstain">Abstain ${totals.abstain}</div>` +
        attendees.map((name) => {
          const vote = votes[name] || "pending";
          return `<div class="assembly-vote ${vote === "pending" ? "abstain" : vote}">${escapeHtml(name)}: ${escapeHtml(vote)}</div>`;
        }).join("");
    }
  }
  const verdict = council.verdict;
  councilAssemblyVerdictSectionEl.style.display = verdict ? "block" : "none";
  if (verdict) {
    const succession = ballot && ballot.kind === "succession";
    councilAssemblyVerdictHeadingEl.textContent = succession ? "Village verdict" : "Elder ruling";
    const ruling = verdict.outcome || verdict.elderRuling || verdict.winner ||
      (succession ? "Village verdict pending" : "Elder ruling pending");
    councilAssemblyVerdictEl.textContent = ruling;
  }
  const transcript = council.transcript || [];
  const stickToBottom = isScrolledNearBottom(councilAssemblyTranscriptEl);
  councilAssemblyTranscriptEl.innerHTML = transcript.length
    ? transcript.map(dailyCouncilTranscriptEntry).join("")
    : '<div class="assembly-entry">The council is gathering.</div>';
  if (stickToBottom) councilAssemblyTranscriptEl.scrollTop = councilAssemblyTranscriptEl.scrollHeight;
}

function drawCouncilAssemblyTable(council, frameTick) {
  if (!councilAssemblyModal.classList.contains("open") || !isDailyCouncilLive(council)) return;
  const ctx = councilAssemblyCtx, size = councilAssemblyCanvas.width, center = size / 2;
  const seats = [...(council.seats || [])].sort((a, b) => (a.seatIndex || 0) - (b.seatIndex || 0));
  ctx.clearRect(0, 0, size, size);
  ctx.fillStyle = "#201b19"; ctx.fillRect(0, 0, size, size);
  const radius = Math.max(220, Math.min(285, 215 + seats.length * 6));
  ctx.fillStyle = "#4a2815"; ctx.beginPath(); ctx.arc(center, center, radius * .67, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "#bd7b3f"; ctx.lineWidth = 10; ctx.beginPath(); ctx.arc(center, center, radius * .67, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = "#d3a65f"; ctx.font = "bold 16px system-ui"; ctx.textAlign = "center"; ctx.fillText("DAILY COUNCIL", center, center - 4);
  ctx.fillStyle = "#f1d69b"; ctx.font = "12px system-ui"; ctx.fillText(`${seats.length} seated · ${council.phase}`, center, center + 18);
  const agentsByName = new Map(getAgents().map((agent) => [agent.name, agent]));
  seats.forEach((seat, index) => {
    // seatIndex and isHead come from the engine; this only maps that serialized
    // ring order into the modal's pixel space.
    const angle = -Math.PI / 2 + ((seat.seatIndex ?? index) / Math.max(seats.length, 1)) * Math.PI * 2;
    const x = center + Math.cos(angle) * radius, y = center + Math.sin(angle) * radius;
    ctx.fillStyle = seat.isHead ? "#e7bd56" : "#795335";
    ctx.beginPath(); ctx.arc(x, y + 7, seat.isHead ? 31 : 25, 0, Math.PI * 2); ctx.fill();
    const agent = agentsByName.get(seat.name) || { name: seat.name, role: seat.role || "villager", color: "#BDBDBD" };
    const seated = { ...agent, x, y: y + 10, targetX: x, targetY: y + 10 };
    drawAgentSprite(ctx, seated, frameTick);
    ctx.fillStyle = seat.isHead ? "#ffe4a1" : "#e9e3d4"; ctx.font = seat.isHead ? "bold 13px system-ui" : "12px system-ui";
    ctx.textAlign = "center"; ctx.fillText(`${seat.isHead ? "★ " : ""}${seat.name}`, x, y + 48);
  });
}

function councilAgentNames(record) {
  const names = [...(record.proposers || [])];
  const elder = getAgents().find((a) => a.role === "elder" && !a.deceased);
  if (elder && !names.includes(elder.name)) names.push(elder.name);
  return names;
}

const COUNCIL_TRANSCRIPT_TYPES = new Set(["convene", "proposal", "verdict", "dissolve"]);
const SIM_FPS = 30;
let councilModalRecord = null;

function councilTranscriptEntries(record) {
  return (record.transcript || []).filter((e) => COUNCIL_TRANSCRIPT_TYPES.has(e.type));
}

// Two council systems both persist to record.transcript with different
// per-entry schemas: the legacy invention council (proposer/elder/blueprint
// fields, type in the 4-entry COUNCIL_TRANSCRIPT_TYPES set) and the Daily
// Council (who/text/feeling fields, plus phase/speak/vote/adjourn/etc types).
// Detect which one a record holds so the modal can render each correctly.
function isDailyCouncilRecord(record) {
  const transcript = (record && record.transcript) || [];
  return transcript.some((e) => e.who !== undefined || !COUNCIL_TRANSCRIPT_TYPES.has(e.type));
}

// Daily Council schema-tolerant renderer (mirrors dailyCouncilTranscriptEntry
// used by the live Assembly modal), but emits .ct-* markup to match the rest
// of the history transcript modal.
function renderDailyCouncilTranscriptEntry(entry) {
  const time = councilTimePrefix(entry, councilModalRecord);
  const who = entry.who || entry.proposer || entry.elder || entry.candidate || "Council";
  const text = entry.text || entry.message || entry.title || entry.topic || entry.outcome || entry.type || "event";
  const feeling = entry.feeling ? ` <span class="ct-reasoning">feeling: ${escapeHtml(entry.feeling)}</span>` : "";
  const targetAgent = WORLD_WIKI_ENABLED_FLAG ? getAgents().find((a) => a.name === who) : null;
  const whoHtml = targetAgent
    ? `<span class="ct-who wiki-link" data-wiki-kind="agent" data-wiki-id="${targetAgent.id}">${escapeHtml(who)}</span>`
    : `<span class="ct-who">${escapeHtml(who)}</span>`;
  return `<div class="ct-entry">${time}${whoHtml}: ${escapeHtml(text)}${feeling}</div>`;
}

function councilEntryTimeLabel(entry, record) {
  const frame = entry.frame != null ? entry.frame : entry.frame_tick;
  if (entry.ts) {
    const wall = formatCouncilTime(entry.ts, frame);
    return frame != null ? `${wall} · frame ${frame}` : wall;
  }
  if (frame != null && record) {
    const anchorTs = record.started_ts || record.ts;
    const anchorFrame = record.start_frame != null ? record.start_frame : record.frame;
    if (anchorTs && anchorFrame != null) {
      const d0 = new Date(anchorTs).getTime();
      if (!isNaN(d0)) {
        const est = new Date(d0 + ((frame - anchorFrame) / SIM_FPS) * 1000);
        return `${formatCouncilTime(est.toISOString(), frame)} · frame ${frame} (est.)`;
      }
    }
    return `frame ${frame}`;
  }
  return "";
}

function councilTimePrefix(entry, record) {
  const label = councilEntryTimeLabel(entry, record);
  return label ? `<span class="ct-time">${escapeHtml(label)}</span>` : "";
}

function renderTranscriptEntry(entry) {
  const time = councilTimePrefix(entry, councilModalRecord);
  const t = entry.type || "event";
  if (t === "convene") {
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.elder || "Elder")}</span> convenes the council` +
      (entry.proposers ? ` (${escapeHtml(entry.proposers.join(", "))})` : "") +
      (entry.message ? `: <span class="ct-action">${escapeHtml(entry.message)}</span>` : "") +
      `</div>`;
  }
  if (t === "proposal") {
    const needs = Object.entries(entry.needs || {}).map(([k, v]) => `${k}×${v}`).join(", ");
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.proposer || "?")}</span> proposes ` +
      `<span class="ct-action">${escapeHtml(entry.blueprint_name || entry.blueprint_id || "a blueprint")}</span>` +
      (entry.function_summary ? ` — ${escapeHtml(entry.function_summary)}` : "") +
      (needs ? ` · needs ${escapeHtml(needs)}` : "") +
      (entry.message ? `<br><span class="ct-reasoning">"${escapeHtml(entry.message)}"</span>` : "") +
      (entry.reasoning ? `<span class="ct-reasoning">${escapeHtml(entry.reasoning)}</span>` : "") +
      `</div>`;
  }
  if (t === "verdict") {
    const rej = entry.rejections || {};
    const rejText = Object.entries(rej).map(([id, r]) => `${id}: ${r}`).join("; ");
    return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.elder || "Elder")}</span> verdict: ` +
      `<span class="ct-action">${escapeHtml(entry.approved_name || entry.approved_id || "approved")}</span>` +
      (rejText ? `<br><span class="ct-reasoning">Rejected — ${escapeHtml(rejText)}</span>` : "") +
      (entry.message ? `<br><span class="ct-reasoning">"${escapeHtml(entry.message)}"</span>` : "") +
      (entry.reasoning ? `<span class="ct-reasoning">${escapeHtml(entry.reasoning)}</span>` : "") +
      `</div>`;
  }
  if (t === "dissolve") {
    return `<div class="ct-entry">${time}<span class="ct-action">${escapeHtml(entry.message || "Council dissolved")}</span></div>`;
  }
  return "";
}

function renderLlmTranscriptEntry(entry) {
  const time = councilTimePrefix(
    { ts: entry.ts, frame: entry.frame_tick },
    councilModalRecord,
  );
  const d = entry.decision || {};
  const action = d.action || entry.error || "unknown";
  const bp = d.blueprint_name ? ` (${d.blueprint_name})` : "";
  const inv = entry.invention_only ? " [invention]" : "";
  return `<div class="ct-entry">${time}<span class="ct-who">${escapeHtml(entry.agent_name || "?")}</span>` +
    ` <span class="ct-action">${escapeHtml(action)}${escapeHtml(bp)}</span>${inv}` +
    (d.reasoning ? `<span class="ct-reasoning">${escapeHtml(d.reasoning)}</span>` : "") +
    (d.message ? `<span class="ct-reasoning">"${escapeHtml(d.message)}"</span>` : "") +
    `</div>`;
}

async function openCouncilTranscript(idx) {
  const record = (getCiv().councilLog || [])[idx];
  if (!record) return;
  councilModalRecord = record;
  councilTranscriptTitleEl.textContent =
    `Council — ${formatCouncilTime(record.ts, record.frame)}: ${record.outcome || "debate"}`;
  const proposals = record.proposals || [];
  const dailyCouncil = isDailyCouncilRecord(record);
  const sequence = dailyCouncil ? (record.transcript || []) : councilTranscriptEntries(record);
  let html = dailyCouncil
    ? `<p class="ct-note">Daily Council session — a live village gathering where attendees speak, debate ` +
      `proposals, vote, and the elder delivers a verdict.</p>`
    : `<p class="ct-note">Invention councils are blueprint pitches, not live debate chat. ` +
      `Villagers each propose a structure design; the elder compares them and picks a winner.</p>`;
  if (!sequence.length && (!record.transcript || !record.transcript.length)) {
    html += `<p class="ct-note">Full timeline available for councils held after the transcript update.</p>`;
  } else if (sequence.length) {
    html += `<div class="ct-section"><h4>Council timeline</h4>` +
      sequence.map(dailyCouncil ? renderDailyCouncilTranscriptEntry : renderTranscriptEntry).join("") + `</div>`;
  } else if (record.transcript && record.transcript.length) {
    html += `<p class="ct-note">Older record — only structured events are shown (random village chat omitted).</p>`;
  }
  if (proposals.length) {
    html += `<div class="ct-section"><h4>Proposals compared</h4>` +
      proposals.map((p) => councilCardHtml(p, record.verdict)).join("") + `</div>`;
  }
  const llmHeading = dailyCouncil ? "Council speeches &amp; verdict (LLM)" : "Blueprint pitches &amp; verdict (LLM)";
  councilTranscriptBodyEl.innerHTML = html + `<div class="ct-section" id="councilLlmSection"><h4>${llmHeading}</h4><span class="civ-label">Loading…</span></div>`;
  councilTranscriptModal.classList.add("open");

  const start = record.start_frame != null ? record.start_frame : record.frame;
  const end = record.end_frame != null ? record.end_frame : record.frame;
  const agents = councilAgentNames(record).join(",");
  const llmSection = document.getElementById("councilLlmSection");
  try {
    const resp = await fetch(`/council-llm-log?start_frame=${start}&end_frame=${end}&agents=${encodeURIComponent(agents)}`);
    const data = await resp.json();
    const entries = data.entries || [];
    llmSection.innerHTML = `<h4>${llmHeading}</h4>` +
      (entries.length
        ? entries.map(renderLlmTranscriptEntry).join("")
        : `<span class="civ-label">No blueprint or verdict LLM turns logged for this council window.</span>`);
  } catch (_err) {
    llmSection.innerHTML = `<h4>${llmHeading}</h4><span class="civ-label">Could not load LM records.</span>`;
  }
}

function closeCouncilTranscript() {
  councilTranscriptModal.classList.remove("open");
  councilModalRecord = null;
}

const settlementsSectionEl = document.getElementById("settlementsSection");
const settlementsMetaEl = document.getElementById("settlementsMeta");
const settlementsListEl = document.getElementById("settlementsList");

function renderSettlements(civ) {
  if (!PATH1_ENABLED) {
    settlementsSectionEl.style.display = "none";
    return;
  }
  const settlements = civ.settlements || [];
  if (!settlements.length) {
    settlementsSectionEl.style.display = "none";
    return;
  }
  settlementsSectionEl.style.display = "block";
  const nightNote = civ.isNight ? " · night" : "";
  settlementsMetaEl.textContent = `${settlements.length} settlement(s)${nightNote}`;
  settlementsListEl.innerHTML = settlements.map((s) => {
    const districts = (s.districts || []).length;
    const nameEl = WORLD_WIKI_ENABLED_FLAG && s.id
      ? `<span class="wiki-link civ-value" data-wiki-kind="settlement" data-wiki-id="${escapeHtml(String(s.id))}">${escapeHtml(s.name || s.id)}</span>`
      : `<span class="civ-value">${escapeHtml(s.name || s.id)}</span>`;
    return `<li>${nameEl} ` +
      `<span class="civ-label">(${districts} district${districts === 1 ? "" : "s"})</span></li>`;
  }).join("");
}

function renderCouncil(civ) {
  const log = civ.councilLog || [];
  if (!log.length) {
    councilSectionEl.style.display = "none";
    return;
  }
  councilSectionEl.style.display = "block";
  const latest = log[0];
  councilMetaEl.textContent =
    `Latest debate (${formatCouncilTime(latest.ts, latest.frame)}, ` +
    `${latest.trigger || "?"}): ${latest.outcome || "in progress"}`;
  councilMetaEl.classList.add("council-clickable");
  councilMetaEl.dataset.councilIdx = "0";
  const proposals = latest.proposals || [];
  councilCardsEl.innerHTML = proposals.length
    ? proposals.map((p) => councilCardHtml(p, latest.verdict)).join("")
    : '<span class="civ-label">no proposals recorded</span>';
  councilHistoryEl.innerHTML = log.slice(1, 8).map((r, i) => {
    const n = (r.proposals || []).length;
    return `<li data-council-idx="${i + 1}">${formatCouncilTime(r.ts, r.frame)} — ${n} proposal(s) — ${escapeHtml(r.outcome || "?")}</li>`;
  }).join("");
}

councilMetaEl.addEventListener("click", () => {
  const idx = Number(councilMetaEl.dataset.councilIdx);
  if (!Number.isNaN(idx)) openCouncilTranscript(idx);
});
councilCardsEl.addEventListener("click", () => openCouncilTranscript(0));
councilHistoryEl.addEventListener("click", (event) => {
  const li = event.target.closest("li[data-council-idx]");
  if (!li) return;
  openCouncilTranscript(Number(li.dataset.councilIdx));
});
councilTranscriptCloseBtn.addEventListener("click", closeCouncilTranscript);
councilTranscriptModal.addEventListener("click", (event) => {
  if (event.target === councilTranscriptModal) closeCouncilTranscript();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && councilTranscriptModal.classList.contains("open")) {
    closeCouncilTranscript();
  }
});
councilAssemblyCloseBtn.addEventListener("click", () => closeCouncilAssembly(true));
councilAssemblyReopenBtn.addEventListener("click", openCouncilAssembly);
councilAssemblyModal.addEventListener("click", (event) => {
  if (event.target === councilAssemblyModal) closeCouncilAssembly(true);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && councilAssemblyModal.classList.contains("open")) {
    closeCouncilAssembly(true);
  }
});

