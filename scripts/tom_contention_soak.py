"""F2 Theory of Mind contention gate — matched flag-off vs flag-on native soaks.

Orchestrates two native ``simulation/server.py`` runs (ToM off, then
``SIM_THEORY_OF_MIND=1``), each observed by ``soak_monitor.py``. Refuses if
Docker ``gitserv-sim`` is running or the target port is already served.

Usage:
    uv run python scripts/tom_contention_soak.py [--minutes 45] [--port 5001]

Outputs:
    simulation/logs/soak-tom-baseline.json
    simulation/logs/soak-tom-flagon.json
    simulation/logs/tom-contention-soak-result.json
    simulation/logs/tom-contention-soak.log (when stdout redirected)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = ROOT / "simulation" / "logs"
RESULT_PATH = LOG_ROOT / "tom-contention-soak-result.json"
PROGRESS_LOG = LOG_ROOT / "tom-contention-soak.log"
SOAK_MONITOR = ROOT / "scripts" / "soak_monitor.py"
SERVER_SCRIPT = ROOT / "simulation" / "server.py"
DOCKER_NAME = "gitserv-sim"
BASELINE_LABEL = "tom-baseline"
FLAGON_LABEL = "tom-flagon"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    line = f"[{utc_now()}] {msg}"
    print(line, flush=True)


def server_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def http_get_state(port: int, timeout: float = 3.0) -> dict | None:
    try:
        req = urllib.request.Request(
            f"{server_url(port)}/state",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        return None


def port_served(port: int) -> bool:
    return http_get_state(port) is not None


def docker_gitserv_running() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--filter", f"name={DOCKER_NAME}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    names = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    return any(name == DOCKER_NAME or name.endswith(DOCKER_NAME) for name in names)


def native_server_pids() -> list[int]:
    if sys.platform == "win32":
        ps_cmd = (
            "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match 'simulation[\\\\/]server\\.py|simulation\\.server' } | "
            "Select-Object -ExpandProperty ProcessId"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return []
        pids = []
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.isdigit():
                pids.append(int(line))
        return pids
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "simulation/server.py|simulation.server"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def session_dirs() -> list[Path]:
    if not LOG_ROOT.is_dir():
        return []
    return [
        p for p in LOG_ROOT.iterdir()
        if p.is_dir() and (p / "llm.jsonl").exists() and (p / "benchmarks.jsonl").exists()
    ]


def wait_for_new_session(before_names: set[str], timeout_s: float) -> Path:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        candidates = [p for p in session_dirs() if p.name not in before_names]
        if candidates:
            session = max(candidates, key=lambda p: p.stat().st_mtime)
            log(f"new session ready: {session.name}")
            return session
        time.sleep(2)
    raise SystemExit(f"timeout waiting for new session after {timeout_s:.0f}s")


def wait_for_server(port: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = http_get_state(port)
        if state is not None:
            flags = (state.get("config") or {}).get("flags") or {}
            log(f"server up on port {port} THEORY_OF_MIND_ENABLED={flags.get('THEORY_OF_MIND_ENABLED')}")
            return
        time.sleep(1)
    raise SystemExit(f"server failed to start within {timeout_s:.0f}s on port {port}")


def start_server(port: int, tom_env: str | None) -> subprocess.Popen:
    env = os.environ.copy()
    env["SIM_PORT"] = str(port)
    if tom_env is None:
        env.pop("SIM_THEORY_OF_MIND", None)
    else:
        env["SIM_THEORY_OF_MIND"] = tom_env
    log(
        f"starting server port={port} SIM_THEORY_OF_MIND={env.get('SIM_THEORY_OF_MIND', '(unset)')}",
    )
    proc = subprocess.Popen(
        ["uv", "run", "python", str(SERVER_SCRIPT)],
        cwd=str(ROOT),
        env=env,
    )
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    log(f"stopping server pid={proc.pid}")
    proc.terminate()
    try:
        proc.wait(timeout=45)
    except subprocess.TimeoutExpired:
        log(f"killing server pid={proc.pid}")
        proc.kill()
        proc.wait()


def run_soak_monitor(label: str, minutes: float) -> int:
    cmd = [
        "uv", "run", "python", str(SOAK_MONITOR),
        "--label", label,
        "--minutes", str(minutes),
    ]
    log(f"running soak_monitor label={label} minutes={minutes}")
    proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
    return proc.returncode


def read_soak_summary(label: str) -> dict | None:
    path = LOG_ROOT / f"soak-{label}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_comparison(baseline: dict, flagon: dict) -> dict:
    def module_slice(summary: dict) -> dict:
        return {
            "metric": summary.get("module_failure_metric"),
            "count": summary.get("module_failure_count"),
            "attempts": summary.get("module_failure_attempts"),
            "rate": summary.get("module_failure_rate"),
        }

    return {
        "module_failure_rate": {
            "baseline": module_slice(baseline),
            "flagon": module_slice(flagon),
        },
        "decision_latency_ms": {
            "baseline": baseline.get("decision_latency_ms"),
            "flagon": flagon.get("decision_latency_ms"),
        },
        "gate_verdict": "pending_orchestrator_verdict",
    }


def preflight(port: int) -> None:
    if docker_gitserv_running():
        if port_served(port):
            raise SystemExit(
                f"refusing: Docker container {DOCKER_NAME} is running and port {port} is served"
            )
        raise SystemExit(
            f"refusing: Docker container {DOCKER_NAME} is running (stop it before this soak)"
        )
    pids = native_server_pids()
    if pids:
        raise SystemExit(
            f"refusing: native simulation/server.py already running (pids={pids})"
        )
    if port_served(port):
        raise SystemExit(f"refusing: port {port} already serves /state")


def run_phase(
    label: str,
    port: int,
    minutes: float,
    tom_env: str | None,
) -> tuple[dict, str]:
    before_names = {p.name for p in session_dirs()}
    proc = start_server(port, tom_env)
    try:
        wait_for_server(port, 90.0)
        session = wait_for_new_session(before_names, 120.0)
        rc = run_soak_monitor(label, minutes)
        if rc != 0:
            log(f"warning: soak_monitor returned {rc} for label={label}")
        summary = read_soak_summary(label)
        if summary is None:
            raise SystemExit(f"missing soak summary for label={label}")
        return summary, session.name
    finally:
        stop_server(proc)
        # Brief pause so the port releases before the next phase.
        for _ in range(15):
            if not port_served(port):
                break
            time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=45, help="minutes per phase (default: 45)")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SIM_PORT", "5001")),
        help="server port (default: SIM_PORT or 5001)",
    )
    args = parser.parse_args()
    if args.minutes <= 0:
        raise SystemExit("--minutes must be positive")

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    log(f"tom contention soak starting minutes={args.minutes} port={args.port}")

    preflight(args.port)

    baseline_summary, baseline_session = run_phase(
        BASELINE_LABEL, args.port, args.minutes, None,
    )
    flagon_summary, flagon_session = run_phase(
        FLAGON_LABEL, args.port, args.minutes, "1",
    )

    result = {
        "started_at": started_at,
        "ended_at": utc_now(),
        "minutes_per_phase": args.minutes,
        "port": args.port,
        "sessions": {
            "baseline": baseline_session,
            "flagon": flagon_session,
        },
        "baseline": baseline_summary,
        "flagon": flagon_summary,
        "comparison": build_comparison(baseline_summary, flagon_summary),
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    log(f"DONE combined results -> {RESULT_PATH}")
    print(f"RESULTS {RESULT_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
