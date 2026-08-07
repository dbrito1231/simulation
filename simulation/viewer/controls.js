// =====================================================================
// Controls — Pause / Resume / Reset are server-side now.
// =====================================================================
const pauseBtn = document.getElementById("pauseBtn");

function syncPauseButton() {
  pauseBtn.textContent = world.paused ? "Resume" : "Pause";
}

async function postControl(path, body) {
  try {
    return await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined
    });
  } catch (err) { /* ignore; next poll reflects real state */ }
  return null;
}

pauseBtn.addEventListener("click", async () => {
  const wantPause = !world.paused;
  await postControl(wantPause ? "/control/pause" : "/control/resume");
  // Optimistic flip; reconciled by the next /state poll.
  world.paused = wantPause;
  syncPauseButton();
  pollState();
});

const resetBtn = document.getElementById("resetBtn");
resetBtn.title = "Requires password (SIM_RESET_PASSWORD)";
function doReset() {
  if (!window.confirm("Reset the simulation? This restarts the village.")) return;
  const password = window.prompt("Type the reset password to wipe the world:");
  if (password === null || password === "") return;
    postControl("/control/reset", { password }).then(async (res) => {
    if (res && res.status === 401) {
      window.alert("Reset refused — wrong password (SIM_RESET_PASSWORD).");
      return;
    }
    statePollFull = true;
    lastFrameTick = 0;
    pollState();
  });
}
resetBtn.addEventListener("click", doReset);

// Keyboard shortcut (R) kept for convenience alongside the visible button.
document.addEventListener("keydown", (e) => {
  if (e.key !== "r" && e.key !== "R") return;
  if (e.altKey || e.ctrlKey || e.metaKey) return;
  const t = e.target;
  if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/i.test(t.tagName))) return;
  doReset();
});

// (No tab-hidden warning anymore: the legacy client sim paused when the tab
// was hidden, but the server-authoritative engine keeps running regardless —
// a background tab merely stops rendering until it's visible again.)

