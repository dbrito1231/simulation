// =====================================================================
// Prediction market panel (idea-04): spectator picks on Daily Council
// ballots; submits to /predictions/* routes only — never mutates world.
// =====================================================================
const PREDICTIONS_HISTORY_POLL_MS = 3000;

const predictionMarketPanelEl = document.getElementById("predictionMarketPanel");
const predictionMarketHitRateEl = document.getElementById("predictionMarketHitRate");
const predictionMarketQuestionEl = document.getElementById("predictionMarketQuestion");
const predictionMarketChoicesEl = document.getElementById("predictionMarketChoices");
const predictionMarketStatusEl = document.getElementById("predictionMarketStatus");
const predictionMarketHistoryEl = document.getElementById("predictionMarketHistory");

const PREDICTION_KIND_LABELS = {
  rule: "Will this rule pass?",
  blueprint: "Will this blueprint pass?",
  idea: "Will this idea pass?",
  succession: "Who will be chosen?",
};

let pendingPrediction = null;
const resolvedBallotKeys = new Set();
let lastHistoryKey = "";
let resolveInFlight = false;
let submitInFlight = false;
let currentBallotContext = null;
let currentBallotChoices = [];
let predictionsRouteEnabled = true;

function isPredictionMarketActive() {
  return PREDICTION_MARKET_ENABLED && predictionsRouteEnabled;
}

function predictionBallotKey(council, ballot) {
  if (!council || !ballot) return null;
  const sessionId = typeof dailyCouncilId === "function"
    ? dailyCouncilId(council)
    : `${council.day ?? "?"}:${council.frame ?? "?"}`;
  return `${sessionId}:${ballot.kind}:${ballot.title || ""}`;
}

function predictionQuestion(ballot) {
  if (ballot.title && String(ballot.title).trim()) return ballot.title.trim();
  return PREDICTION_KIND_LABELS[ballot.kind] || "Make your prediction";
}

function formatHitRate(hitRate) {
  if (hitRate == null || Number.isNaN(hitRate)) return "—";
  return `${Math.round(hitRate * 100)}%`;
}

function ballotChoices(ballot) {
  if (!ballot) return [];
  if (ballot.kind === "succession") {
    return (ballot.candidates || []).filter((name) => typeof name === "string" && name.trim());
  }
  return ["yes", "no"];
}

function hidePredictionPanel() {
  if (predictionMarketPanelEl) predictionMarketPanelEl.style.display = "none";
}

function showPredictionPanel() {
  if (predictionMarketPanelEl) predictionMarketPanelEl.style.display = "";
}

async function postPredictionSubmit(kind, question, pick) {
  const frameTick = world && world.frameTick != null ? world.frameTick : 0;
  const res = await fetch("/predictions/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind,
      question,
      pick,
      ballot_frame_tick: frameTick,
    }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.ok && data.id ? data.id : null;
}

async function postPredictionResolve(id, correct, verdict, resolvedFrameTick) {
  const res = await fetch("/predictions/resolve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, correct, verdict, resolved_frame_tick: resolvedFrameTick }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  return !!data.ok;
}

async function tryResolvePending(verdict) {
  if (!isPredictionMarketActive() || !pendingPrediction || resolveInFlight) return;
  if (resolvedBallotKeys.has(pendingPrediction.ballotKey)) return;
  const winner = verdict && verdict.winner;
  if (typeof winner !== "string" || !winner.trim()) return;

  resolveInFlight = true;
  try {
    const correct = pendingPrediction.pick === winner;
    const resolvedFrameTick = world && Number.isInteger(world.frameTick)
      ? world.frameTick
      : null;
    const ok = await postPredictionResolve(
      pendingPrediction.id, correct, winner, resolvedFrameTick,
    );
    if (ok) {
      resolvedBallotKeys.add(pendingPrediction.ballotKey);
      pollPredictionsHistory();
    }
  } finally {
    resolveInFlight = false;
  }
}

function renderPredictionChoices(ballot, locked) {
  if (!predictionMarketChoicesEl) return;
  if (locked || !ballot) {
    predictionMarketChoicesEl.innerHTML = "";
    currentBallotChoices = [];
    return;
  }
  const choices = ballotChoices(ballot);
  currentBallotChoices = choices;
  predictionMarketChoicesEl.innerHTML = choices.map((choice, index) => {
    const label = ballot.kind === "succession" ? choice : choice.toUpperCase();
    return `<button type="button" class="prediction-choice-btn" data-pick-idx="${index}">${escapeHtml(label)}</button>`;
  }).join("");
}

function renderPredictionsHistory(data) {
  if (!isPredictionMarketActive() || !data || !data.enabled) return;
  if (predictionMarketHitRateEl) {
    predictionMarketHitRateEl.textContent = `Hit rate: ${formatHitRate(data.hitRate)}`;
  }
  if (!predictionMarketHistoryEl) return;
  const resolved = (data.predictions || []).filter((row) => typeof row.correct === "boolean");
  const recent = resolved.slice(-5).reverse();
  const historyKey = JSON.stringify({ hitRate: data.hitRate, recent });
  if (historyKey === lastHistoryKey) return;
  lastHistoryKey = historyKey;
  if (recent.length === 0) {
    predictionMarketHistoryEl.innerHTML = '<li class="prediction-history-empty">No resolved predictions yet</li>';
    return;
  }
  predictionMarketHistoryEl.innerHTML = recent.map((row) => {
    const badge = row.correct
      ? '<span class="prediction-badge prediction-badge-correct">✓</span>'
      : '<span class="prediction-badge prediction-badge-wrong">✗</span>';
    const question = escapeHtml(truncatePredictionText(row.question, 48));
    const pick = escapeHtml(row.pick || "—");
    return `<li class="prediction-history-row">${badge} ${question} · ${pick}</li>`;
  }).join("");
}

function truncatePredictionText(text, maxLen) {
  const s = String(text || "");
  return s.length > maxLen ? `${s.slice(0, maxLen - 1)}…` : s;
}

function updatePredictionsPanel(council) {
  if (!isPredictionMarketActive()) {
    hidePredictionPanel();
    currentBallotContext = null;
    return;
  }
  showPredictionPanel();

  const ballot = council && council.ballot;
  const verdict = council && council.verdict;
  if (verdict) tryResolvePending(verdict);

  const ballotKey = ballot ? predictionBallotKey(council, ballot) : null;
  if (ballotKey && pendingPrediction && pendingPrediction.ballotKey !== ballotKey) {
    pendingPrediction = null;
  }

  const windowOpen = !!(ballot && !verdict);
  const hasPick = !!(pendingPrediction && ballotKey && pendingPrediction.ballotKey === ballotKey);

  if (predictionMarketQuestionEl) {
    if (ballot) {
      predictionMarketQuestionEl.textContent = predictionQuestion(ballot);
    } else {
      predictionMarketQuestionEl.textContent = "Waiting for the next council ballot.";
    }
  }

  renderPredictionChoices(ballot, !windowOpen || hasPick);

  if (predictionMarketStatusEl) {
    if (hasPick && windowOpen) {
      predictionMarketStatusEl.textContent = `Your pick: ${pendingPrediction.pick} (locked)`;
    } else if (hasPick && verdict) {
      const correct = pendingPrediction.pick === verdict.winner;
      predictionMarketStatusEl.textContent =
        `Your pick: ${pendingPrediction.pick} — ${correct ? "correct" : "incorrect"} (${verdict.winner})`;
    } else if (windowOpen && !hasPick) {
      predictionMarketStatusEl.textContent = "Pick an outcome before the verdict.";
    } else {
      predictionMarketStatusEl.textContent = "";
    }
  }

  currentBallotContext = windowOpen && !hasPick && ballot
    ? { council, ballot, ballotKey }
    : null;
}

async function onPredictionChoiceClick(ev) {
  const btn = ev.target.closest(".prediction-choice-btn");
  if (!btn || !isPredictionMarketActive() || !currentBallotContext || submitInFlight) return;
  const idx = Number(btn.getAttribute("data-pick-idx"));
  const pick = currentBallotChoices[idx];
  if (!pick) return;

  const { ballot, ballotKey } = currentBallotContext;
  const kind = ballot.kind;
  const question = predictionQuestion(ballot);
  submitInFlight = true;
  try {
    const id = await postPredictionSubmit(kind, question, pick);
    if (!id) return;
    pendingPrediction = { ballotKey, id, pick };
    updatePredictionsPanel(currentBallotContext.council);
  } finally {
    submitInFlight = false;
  }
}

async function pollPredictionsHistory() {
  if (!PREDICTION_MARKET_ENABLED) return;
  try {
    const res = await fetch("/predictions/history", { cache: "no-store" });
    if (!res.ok) return;
    const data = await res.json();
    if (!data.enabled) {
      predictionsRouteEnabled = false;
      hidePredictionPanel();
      currentBallotContext = null;
      return;
    }
    predictionsRouteEnabled = true;
    renderPredictionsHistory(data);
  } catch (err) {
    /* keep last render */
  }
}

if (predictionMarketChoicesEl) {
  predictionMarketChoicesEl.addEventListener("click", onPredictionChoiceClick);
}

setInterval(pollPredictionsHistory, PREDICTIONS_HISTORY_POLL_MS);
pollPredictionsHistory();
