"""Canonical CLI loader for the sim's two Ollama models (no GUI required).

Successor to scripts/lms_load.py, which loaded/verified LM Studio's single
qwen/qwen3.5-9b model. Ollama's model is different: env vars control server-
wide behavior (parallelism, dual residency, KV-cache attention, keep-alive),
and `ollama create` bakes per-model settings (context length, sampling
defaults) from version-controlled Modelfiles. See ollama_config.md for the
full settings table and docs/plan-ollama-migration.md Phase 1 for how this
script's responsibilities were scoped.

Target state (see ollama/Modelfile.smart, ollama/Modelfile.fast,
ollama_config.md):
  - User env vars: OLLAMA_NUM_PARALLEL=3, OLLAMA_MAX_LOADED_MODELS=2,
    OLLAMA_FLASH_ATTENTION=1, OLLAMA_KEEP_ALIVE=-1 (both models resident
    24/7, matching the sim's always-on server).
  - Two named models: sim-smart (Qwen3.5-9B-Q4_K_M, num_ctx 20480),
    sim-fast (llama3.2:3b, num_ctx 4096).
  - Both models warm (loaded into VRAM) and visible in `ollama ps` /
    `/api/ps` at the same time -- this is the dual-residency requirement
    Phase 0 found needs OLLAMA_MAX_LOADED_MODELS>=2 (default is 1, which
    evicts on every model switch).

Why a script, not just `ollama create`: env vars set via `setx` only take
effect for NEW processes, so the Ollama app/service must be restarted after
setting them -- this script detects what's running, kills it, and relaunches
it, then polls /api/version until the server is back before touching models.

Usage:
  uv run python scripts/ollama_setup.py           # apply full target state
  uv run python scripts/ollama_setup.py --check   # readback only, no changes
"""

import argparse
import json
import subprocess
import sys
import time

import requests

BASE = "http://localhost:11434"
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
MODELFILE_SMART = REPO_ROOT / "ollama" / "Modelfile.smart"
MODELFILE_FAST = REPO_ROOT / "ollama" / "Modelfile.fast"

SIM_SMART = "sim-smart"
SIM_FAST = "sim-fast"
FAST_BASE_MODEL = "llama3.2:3b"

ENV_VARS = {
    "OLLAMA_NUM_PARALLEL": "3",
    "OLLAMA_MAX_LOADED_MODELS": "2",
    "OLLAMA_FLASH_ATTENTION": "1",
    "OLLAMA_KEEP_ALIVE": "-1",
}

# Windows install path confirmed live on this machine (2026-07-24):
# `where ollama` -> C:\Users\<user>\AppData\Local\Programs\Ollama\ollama.exe,
# with a sibling "ollama app.exe" (the tray-app / server launcher). Resolved
# relative to the current user's profile so this isn't hardcoded to one
# machine's username.
import os
OLLAMA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama")
OLLAMA_APP_EXE = os.path.join(OLLAMA_DIR, "ollama app.exe")
OLLAMA_CLI_EXE = os.path.join(OLLAMA_DIR, "ollama.exe")


def sh(cmd, timeout=600, **kwargs):
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, shell=False,
                          encoding="utf-8", errors="replace", timeout=timeout,
                          **kwargs)
    out = (proc.stdout or "") + (proc.stderr or "")
    safe = out.strip().encode(sys.stdout.encoding or "utf-8", errors="replace") \
                       .decode(sys.stdout.encoding or "utf-8", errors="replace")
    print(safe)
    return proc.returncode, out


def check():
    """Readback only: env vars (user scope), running processes, /api/version,
    /api/ps residency, and `ollama list` for both target models."""
    print("-- user environment variables --")
    ps_cmd = ("[Environment]::GetEnvironmentVariable('{0}','User')")
    for var in ENV_VARS:
        rc, out = sh(["powershell", "-NoProfile", "-Command", ps_cmd.format(var)],
                     timeout=30)
        val = out.strip()
        expected = ENV_VARS[var]
        status = "OK" if val == expected else f"MISMATCH (want {expected!r})"
        print(f"  {var} = {val!r}  [{status}]")

    print("\n-- ollama processes --")
    sh(["powershell", "-NoProfile", "-Command",
        "Get-Process 'ollama app','ollama' -ErrorAction SilentlyContinue "
        "| Select-Object ProcessName,Id | Format-Table -AutoSize | Out-String -Width 200"],
       timeout=30)

    print("-- /api/version --")
    try:
        v = requests.get(f"{BASE}/api/version", timeout=5).json()
        print(f"  {v}")
    except Exception as exc:
        print(f"  WARNING: not reachable: {exc}")
        return 1

    print("\n-- ollama list (model catalog) --")
    sh([OLLAMA_CLI_EXE, "list"], timeout=30)

    print("-- /api/ps (residency) --")
    try:
        ps = requests.get(f"{BASE}/api/ps", timeout=5).json()
        models = ps.get("models", [])
        names = [m.get("name") for m in models]
        print(f"  resident: {names}")
        for want in (SIM_SMART, SIM_FAST):
            tag = f"{want}:latest"
            hit = tag in names or want in names
            print(f"  {want}: {'RESIDENT' if hit else 'NOT RESIDENT'}")
        if len(models) < 2:
            print("  WARNING: fewer than 2 models resident -- dual residency "
                  "not confirmed (check OLLAMA_MAX_LOADED_MODELS).")
    except Exception as exc:
        print(f"  WARNING: /api/ps failed: {exc}")
        return 1
    return 0


def wait_for_server(timeout_s=60):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            requests.get(f"{BASE}/api/version", timeout=3)
            return True
        except Exception:
            time.sleep(1)
    return False


def set_env_vars():
    print("-- setting user environment variables (setx) --")
    for var, val in ENV_VARS.items():
        sh(["setx", var, val], timeout=30)
    print("NOTE: setx only affects NEW processes; the Ollama app/service is "
          "restarted below so these take effect immediately for this run too.")


def restart_ollama():
    """Kill any running Ollama app/server processes and relaunch the app so
    the freshly-setx'd env vars are picked up. Falls back to `ollama serve`
    if the app executable isn't found (e.g. a service-only install)."""
    print("-- restarting Ollama so env vars take effect --")
    # Detect what's running before killing anything.
    rc, out = sh(["powershell", "-NoProfile", "-Command",
                  "Get-Process 'ollama app','ollama' -ErrorAction SilentlyContinue "
                  "| Select-Object -ExpandProperty ProcessName"], timeout=30)
    running = set(line.strip() for line in out.splitlines() if line.strip())
    print(f"  currently running: {running or 'none'}")

    for image in ("ollama app.exe", "ollama.exe"):
        sh(["taskkill", "/F", "/IM", image], timeout=30)
    time.sleep(2)

    import os as _os
    if _os.path.exists(OLLAMA_APP_EXE):
        print(f"  relaunching {OLLAMA_APP_EXE}")
        subprocess.Popen([OLLAMA_APP_EXE], shell=False,
                         creationflags=subprocess.DETACHED_PROCESS
                         if hasattr(subprocess, "DETACHED_PROCESS") else 0)
    else:
        print("  ollama app.exe not found -- falling back to `ollama serve`")
        subprocess.Popen([OLLAMA_CLI_EXE, "serve"], shell=False,
                         creationflags=subprocess.DETACHED_PROCESS
                         if hasattr(subprocess, "DETACHED_PROCESS") else 0)

    print("  waiting for /api/version ...")
    if not wait_for_server(60):
        print("  ERROR: Ollama server did not come back within 60s.")
        return False
    print("  server is back up.")
    return True


def ensure_fast_base_pulled():
    print(f"-- ensuring base model {FAST_BASE_MODEL} is pulled --")
    rc, out = sh([OLLAMA_CLI_EXE, "list"], timeout=30)
    if FAST_BASE_MODEL in out:
        print(f"  {FAST_BASE_MODEL} already present.")
        return True
    rc, _ = sh([OLLAMA_CLI_EXE, "pull", FAST_BASE_MODEL], timeout=1800)
    if rc != 0:
        print(f"  ERROR: pull of {FAST_BASE_MODEL} failed.")
        return False
    return True


def create_models():
    print("-- creating/updating sim-smart and sim-fast (idempotent) --")
    ok = True
    rc, _ = sh([OLLAMA_CLI_EXE, "create", SIM_SMART, "-f", str(MODELFILE_SMART)],
               timeout=600)
    ok = ok and rc == 0
    rc, _ = sh([OLLAMA_CLI_EXE, "create", SIM_FAST, "-f", str(MODELFILE_FAST)],
               timeout=600)
    ok = ok and rc == 0
    return ok


def warm_model(name):
    print(f"-- warming {name} (keep_alive -1) --")
    payload = {
        "model": name,
        "messages": [{"role": "user", "content": "Reply with just: ok"}],
        "stream": False,
        "keep_alive": -1,
        "think": False,
    }
    try:
        resp = requests.post(f"{BASE}/api/chat", json=payload, timeout=120)
        body = resp.json()
        content = (body.get("message") or {}).get("content", "")
        print(f"  http={resp.status_code} content={content!r}")
        return resp.status_code == 200
    except Exception as exc:
        print(f"  WARNING: warm-up call to {name} failed: {exc}")
        return False


def verify_dual_residency():
    print("-- verifying dual residency via /api/ps --")
    try:
        ps = requests.get(f"{BASE}/api/ps", timeout=5).json()
        models = ps.get("models", [])
        names = [m.get("name") for m in models]
        print(f"  resident: {names}")
    except Exception as exc:
        print(f"  WARNING: /api/ps failed: {exc}")
        return False
    smart_ok = any(n.startswith(SIM_SMART) for n in names)
    fast_ok = any(n.startswith(SIM_FAST) for n in names)
    if smart_ok and fast_ok:
        print("  PASS: both sim-smart and sim-fast resident simultaneously.")
        return True
    print(f"  FAIL: sim-smart resident={smart_ok}, sim-fast resident={fast_ok}")
    return False


def apply():
    set_env_vars()
    if not restart_ollama():
        return 1
    if not ensure_fast_base_pulled():
        return 1
    if not create_models():
        print("ERROR: model creation failed; see output above.")
        return 1
    warm_model(SIM_SMART)
    warm_model(SIM_FAST)
    if not verify_dual_residency():
        print("\nWARNING: dual residency not confirmed. Check "
              "OLLAMA_MAX_LOADED_MODELS took effect (may need a manual "
              "logoff/reboot for setx'd vars to reach a stubborn parent "
              "process) and re-run --check.")
        return 1
    print("\nOK: sim-smart and sim-fast created, warmed, and dual-resident.")
    check()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="readback only, no changes")
    args = ap.parse_args()
    if args.check:
        return check()
    return apply()


if __name__ == "__main__":
    sys.exit(main())
