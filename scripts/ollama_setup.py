"""Canonical CLI loader for the sim's two Ollama models (no GUI required).

Successor to scripts/lms_load.py, which loaded/verified LM Studio's single
qwen/qwen3.5-9b model. Ollama's model is different: env vars control server-
wide behavior (parallelism, dual residency, KV-cache attention, keep-alive),
and `ollama create` bakes per-model settings (context length, sampling
defaults) from version-controlled Modelfiles. See ollama_config.md for the
full settings table and docs/archive/plan-ollama-migration.md Phase 1 for how this
script's responsibilities were scoped.

Target state (see ollama/Modelfile.smart, ollama/Modelfile.fast,
ollama_config.md):
  - User env vars: OLLAMA_NUM_PARALLEL=3, OLLAMA_MAX_LOADED_MODELS=3,
    OLLAMA_FLASH_ATTENTION=1, OLLAMA_KEEP_ALIVE=-1 (both models resident
    24/7, matching the sim's always-on server; raised from 2 to 3
    2026-07-25 to allow a 3rd model -- e.g. a CPU-offload probe model --
    to coexist without evicting sim-smart/sim-fast, see ollama_config.md
    "CPU-offload probe (2026-07-25, corrected retry)").
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
  uv run python scripts/ollama_setup.py --with-system
      # Phase 6 (docs/archive/plan-ollama-migration.md, dark by default -- see
      # SYSTEM_PROMPT_AT_LOAD_TIME in simulation/server.py). Generates
      # ollama/Modelfile.smart.system (copy of Modelfile.smart + a SYSTEM
      # block baking in simulation/prompts.py's SYSTEM_PROMPT text, the
      # single source of truth) and runs `ollama create sim-smart-sys -f
      # <generated>` -- a SEPARATE model name from sim-smart, so the live
      # sim-smart the sim server uses is untouched. Does not set env vars,
      # restart Ollama, or touch sim-smart/sim-fast; safe to run any time,
      # including while the sim server is live on sim-smart/sim-fast.
"""

import argparse
import json
import subprocess
import sys
import time

import requests

BASE = "http://localhost:11434"
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
SIMULATION_DIR = REPO_ROOT / "simulation"
MODELFILE_SMART = REPO_ROOT / "ollama" / "Modelfile.smart"
MODELFILE_FAST = REPO_ROOT / "ollama" / "Modelfile.fast"
MODELFILE_SMART_SYS = REPO_ROOT / "ollama" / "Modelfile.smart.system"
# Portable registry fallback (see that file's header) -- used when neither
# SIM_SMART_GGUF nor Modelfile.smart's local GGUF path is available.
MODELFILE_SMART_REGISTRY = REPO_ROOT / "ollama" / "Modelfile.smart.registry"
# Generated (not committed) when SIM_SMART_GGUF points at a valid local
# override path -- same pattern as Modelfile.smart.system below.
MODELFILE_SMART_GENERATED = REPO_ROOT / "ollama" / "Modelfile.smart.generated"

SIM_SMART = "sim-smart"
SIM_FAST = "sim-fast"
SIM_SMART_SYS = "sim-smart-sys"
FAST_BASE_MODEL = "llama3.2:3b"
SMART_REGISTRY_MODEL = "qwen3.5:9b"

ENV_VARS = {
    "OLLAMA_NUM_PARALLEL": "3",
    "OLLAMA_MAX_LOADED_MODELS": "3",
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
    if the app executable isn't found (e.g. a service-only install).

    IMPORTANT (found 2026-07-25, corrected-retry probe): `setx` only writes
    the new value to HKCU\\Environment -- it does NOT update this already-
    running Python process's own `os.environ`, and a plain
    `subprocess.Popen([...])` with no `env=` argument inherits the CHILD's
    environment from the CURRENT process's (stale, pre-setx) block, not a
    fresh registry read. Windows only re-reads HKCU\\Environment into a new
    process's env block when the *parent* is Explorer (e.g. a genuinely new
    top-level window) -- a child spawned from an already-running script
    never sees the setx'd value no matter how long it waits. Verified live:
    after this function ran once with the old (env=None) code path, `ollama
    ps` showed the relaunched server still enforcing a 2-model cap even
    though `[Environment]::GetEnvironmentVariable(...,'User')` and a brand
    new `cmd.exe` both read the correct new value -- the running Ollama
    process itself had the stale env baked in from launch. Fix: explicitly
    merge ENV_VARS onto a copy of this process's environment and pass it via
    `env=` to Popen, so the relaunched Ollama process is guaranteed to see
    the current target values regardless of registry-propagation timing to
    other, unrelated processes."""
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
    child_env = dict(_os.environ)
    child_env.update(ENV_VARS)
    if _os.path.exists(OLLAMA_APP_EXE):
        print(f"  relaunching {OLLAMA_APP_EXE} (explicit env: {ENV_VARS})")
        subprocess.Popen([OLLAMA_APP_EXE], shell=False, env=child_env,
                         creationflags=subprocess.DETACHED_PROCESS
                         if hasattr(subprocess, "DETACHED_PROCESS") else 0)
    else:
        print("  ollama app.exe not found -- falling back to `ollama serve` "
              f"(explicit env: {ENV_VARS})")
        subprocess.Popen([OLLAMA_CLI_EXE, "serve"], shell=False, env=child_env,
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


def _parse_from_path(modelfile_path):
    """Return the path after a Modelfile's `FROM` line, or None."""
    if not modelfile_path.exists():
        return None
    for line in modelfile_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("FROM "):
            return line[len("FROM "):].strip()
    return None


def _generate_smart_modelfile_from_gguf(gguf_path):
    """Write ollama/Modelfile.smart.generated: Modelfile.smart's PARAMETER
    block with `FROM <gguf_path>` substituted in, plus a DO-NOT-EDIT header
    -- same generated-file pattern as generate_system_modelfile() below.
    Not committed to version control (report the needed .gitignore line
    separately -- another change owns .gitignore)."""
    base_text = MODELFILE_SMART.read_text(encoding="utf-8")
    out_lines = []
    replaced = False
    for line in base_text.splitlines():
        if not replaced and line.strip().startswith("FROM "):
            out_lines.append(f"FROM {gguf_path}")
            replaced = True
        else:
            out_lines.append(line)
    header = (
        "# ============================================================\n"
        "# GENERATED FILE -- DO NOT EDIT BY HAND.\n"
        "# Produced by scripts/ollama_setup.py from ollama/Modelfile.smart\n"
        "# with FROM replaced by the SIM_SMART_GGUF env var override.\n"
        "# ============================================================\n\n"
    )
    generated = header + "\n".join(out_lines) + "\n"
    MODELFILE_SMART_GENERATED.write_text(generated, encoding="utf-8")
    print(f"  wrote {MODELFILE_SMART_GENERATED}")
    return MODELFILE_SMART_GENERATED


def resolve_smart_source():
    """Select the sim-smart Modelfile source, in priority order:
      a. SIM_SMART_GGUF env var, if set AND the file it names exists
      b. ollama/Modelfile.smart's own local GGUF FROM path, if that path
         exists on disk (current/original behavior, unchanged)
      c. the registry fallback (ollama/Modelfile.smart.registry, pulls
         qwen3.5:9b)
    Returns (modelfile_path, pull_tag_or_None). Prints exactly which source
    was selected."""
    gguf_override = os.environ.get("SIM_SMART_GGUF", "").strip()
    if gguf_override:
        override_path = __import__("pathlib").Path(gguf_override)
        if override_path.exists():
            print(f"-- smart model source: SIM_SMART_GGUF override ({override_path}) --")
            return _generate_smart_modelfile_from_gguf(override_path), None
        print(f"  WARNING: SIM_SMART_GGUF={gguf_override!r} does not exist; "
              "ignoring override and falling back.")

    local_from = _parse_from_path(MODELFILE_SMART)
    if local_from and __import__("pathlib").Path(local_from).exists():
        print(f"-- smart model source: local GGUF (ollama/Modelfile.smart -> {local_from}) --")
        return MODELFILE_SMART, None

    print(f"-- smart model source: registry ({SMART_REGISTRY_MODEL}) --")
    return MODELFILE_SMART_REGISTRY, SMART_REGISTRY_MODEL


def create_models():
    print("-- creating/updating sim-smart and sim-fast (idempotent) --")
    ok = True

    smart_modelfile, pull_tag = resolve_smart_source()
    if pull_tag:
        rc, out = sh([OLLAMA_CLI_EXE, "list"], timeout=30)
        if pull_tag in out:
            print(f"  {pull_tag} already present.")
        else:
            rc2, _ = sh([OLLAMA_CLI_EXE, "pull", pull_tag], timeout=1800)
            if rc2 != 0:
                print(
                    f"ERROR: no smart model source available. Tried, in order:\n"
                    f"  a. SIM_SMART_GGUF env var (unset or file missing)\n"
                    f"  b. local GGUF path in ollama/Modelfile.smart (missing on this machine)\n"
                    f"  c. registry pull of {pull_tag} (FAILED -- check network/registry access)\n"
                    "Set SIM_SMART_GGUF to a local GGUF path, restore the "
                    "expected local GGUF, or fix registry access, then re-run."
                )
                return False

    rc, _ = sh([OLLAMA_CLI_EXE, "create", SIM_SMART, "-f", str(smart_modelfile)],
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


def _load_system_prompt():
    """Import simulation/prompts.py's SYSTEM_PROMPT -- the single source of
    truth (specs/03-cognition.md) -- without importing simulation/server.py.
    server.py has module-level side effects on import (SessionLogger() opens
    a new simulation/logs/<timestamp>/ session directory, and the live
    SimEngine is constructed against state.db further down the module), so
    importing it from this setup script would create stray session
    directories / touch persisted state just by importing it -- unacceptable
    for a script that must be safe to run at any time. prompts.py has no such
    side effects: it only imports sim_engine.py (for the TECH_TREE_ENABLED
    flag), which itself does not touch state.db or start threads at import
    time (verified by reading sim_engine.py top to bottom -- SimEngine's
    state.db reads and thread starts only happen inside explicitly-called
    methods, never at module scope). Prints two informational
    "[server] system prompt sha256=..." lines as a side effect of importing
    prompts.py (its own startup-proof logging) -- harmless, matches what
    server.py's own startup prints."""
    sys.path.insert(0, str(SIMULATION_DIR))
    import prompts as _prompts  # noqa: E402
    return _prompts.SYSTEM_PROMPT


def generate_system_modelfile():
    """Write ollama/Modelfile.smart.system: a copy of Modelfile.smart plus a
    `SYSTEM \"\"\"...\"\"\"` block baking in the exact SYSTEM_PROMPT text from
    simulation/prompts.py, and a DO-NOT-EDIT header. Never hand-edit the
    generated file -- re-run this function (via --with-system) after any
    SYSTEM_PROMPT change in prompts.py."""
    print("-- generating ollama/Modelfile.smart.system --")
    system_prompt = _load_system_prompt()
    if '"""' in system_prompt:
        print("  ERROR: SYSTEM_PROMPT contains a literal triple-quote, which "
              "would break the generated Modelfile's SYSTEM \"\"\"...\"\"\" "
              "block. Aborting generation -- fix prompts.py first.")
        return False
    base_text = MODELFILE_SMART.read_text(encoding="utf-8")
    header = (
        "# ============================================================\n"
        "# GENERATED FILE -- DO NOT EDIT BY HAND.\n"
        "# Produced by `uv run python scripts/ollama_setup.py --with-system`\n"
        "# from ollama/Modelfile.smart + simulation/prompts.py's SYSTEM_PROMPT\n"
        "# (the single source of truth -- edit the rulebook there, then\n"
        "# re-run --with-system to regenerate this file and re-bake the\n"
        "# sim-smart-sys model). See docs/archive/plan-ollama-migration.md Phase 6\n"
        "# and ollama_config.md \"Load-time rulebook (dark)\".\n"
        "# ============================================================\n\n"
    )
    system_block = (
        "\n# Baked rulebook (Phase 6, load-time system prompt) -- applies\n"
        "# ONLY when a request omits a system message (Ollama Modelfile SYSTEM\n"
        "# semantics, verified live: ollama_config.md \"Modelfile SYSTEM\n"
        "# semantics\"). An explicit request-time system message always\n"
        "# overrides this, never concatenates with it.\n"
        f'SYSTEM """{system_prompt}"""\n'
    )
    generated = header + base_text + system_block
    MODELFILE_SMART_SYS.parent.mkdir(parents=True, exist_ok=True)
    MODELFILE_SMART_SYS.write_text(generated, encoding="utf-8")
    print(f"  wrote {MODELFILE_SMART_SYS} ({len(generated)} chars, "
          f"SYSTEM_PROMPT {len(system_prompt)} chars)")
    return True


def create_system_model():
    """`ollama create sim-smart-sys -f ollama/Modelfile.smart.system`.
    SEPARATE model name from sim-smart -- creating/updating it never touches
    the live sim-smart/sim-fast models the sim server has resident, so this
    is safe to run while the sim server is up."""
    if not generate_system_modelfile():
        return False
    print(f"-- creating/updating {SIM_SMART_SYS} (idempotent) --")
    rc, _ = sh([OLLAMA_CLI_EXE, "create", SIM_SMART_SYS, "-f", str(MODELFILE_SMART_SYS)],
               timeout=600)
    if rc != 0:
        print(f"  ERROR: creation of {SIM_SMART_SYS} failed.")
        return False
    print(f"  OK: {SIM_SMART_SYS} created.")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="readback only, no changes")
    ap.add_argument("--with-system", action="store_true",
                     help="generate ollama/Modelfile.smart.system from "
                          "simulation/prompts.py's SYSTEM_PROMPT and `ollama "
                          "create sim-smart-sys` from it (Phase 6, dark). "
                          "Does not touch sim-smart/sim-fast or env vars.")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.with_system:
        return 0 if create_system_model() else 1
    return apply()


if __name__ == "__main__":
    sys.exit(main())
